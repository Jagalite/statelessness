import XCTest
@testable import Statelessness
final class StatefulTests: XCTestCase {
    func testFencesAndDeadline() throws {
        let m: LifecycleMonitor<ScopedKey<UInt8>> = indexedLifecycleMonitor(limits: RetentionLimits(obligations:2,resources:2,violations:8),cloneKey:{$0})
        var h = LifecycleHistory<ScopedKey<UInt8>>()
        for epoch in 0..<1000 { let k = ScopedKey(epoch:UInt64(epoch),generation:0,key:UInt8(7)); h = try m.advance(h,events:[.begin(k,deadline:10),.acquire(k),.settle(k),.release(k),.fence(before:UInt64(epoch+1))]); XCTAssertEqual(h.obligations.count,0) }
        h = try m.advance(h,events:[.settle(ScopedKey(epoch:0,generation:0,key:7))]); XCTAssertEqual(h.violations.first?.code,"retired_epoch_reference")
        let legacy = LifecycleMonitor<Int>(compare:{$0 < $1 ? -1 : $0 > $1 ? 1 : 0},cloneKey:{$0})
        var old = try legacy.advance(LifecycleHistory(),events:[.begin(7,deadline:10),.tick(11),.cancel(7)]); XCTAssertEqual(old.violations.first?.code,"missing_before_advance")
        old = try legacy.advance(LifecycleHistory(),events:[.begin(7,deadline:10),.tick(10),.settle(7)]); XCTAssertEqual(try legacy.checks(old).first?.status,.passed)
    }
    func testExplicitNetwork() throws {
        let child = Model<Int,Int,Int>(initialState:{0},step:{Transition(state:$0+$1,outputs:[$1],disposition:.ignored("observed"))},checkState:{_ in [.passed("safe")]},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},inputs:{_ in InputIterator(values:[1])},equalStates:{$0 == $1})
        let w = NetworkWiring<Int,Int,Int>(routes:{[NetworkRoute(destination:($0.machine+1)%3,channel:7,input:$0.value)]},checkState:{_,c in try c.push(.passed("sum"))})
        let n = try Network(machines:[NetworkAddress(2,child),NetworkAddress(0,child),NetworkAddress(1,child)],wiring:w,policy:QueuePolicy(maxPending:2,maxPerChannel:2,allowDrop:true,allowDuplicate:true))
        let initial = try n.initialState(); var t = try n.step(initial,.local(NetworkAddress(0,1)))
        XCTAssertEqual(initial.pending.count,0); XCTAssertEqual(t.disposition,.ignored("observed"))
        let dup = try n.step(t.state,.duplicate(0)); XCTAssertThrowsError(try n.step(dup.state,.deliver(1))); XCTAssertThrowsError(try n.step(dup.state,.duplicate(0)))
        for _ in 0..<100 { t = try n.step(t.state,.deliver(0)); XCTAssertEqual(t.state.pending.count,1) }
        XCTAssertEqual(t.state.machines.reduce(0){$0+$1.value},101)
    }
    func testLifecycleCodecGoldens() throws {
        let legacy = LifecycleMonitor<UInt8>(compare:{$0 < $1 ? -1 : $0 > $1 ? 1 : 0},cloneKey:{$0})
        let c = try legacy.historyCodec(key:ValueCodecs.u8)
        XCTAssertEqual(try c.traceBytes(LifecycleHistory(),maximum:1024),Array(repeating:0,count:20))
        let indexed: LifecycleMonitor<ScopedKey<UInt8>> = indexedLifecycleMonitor(cloneKey:{$0})
        let ic = try indexed.historyCodec(key:scopedKeyCodec(ValueCodecs.u8))
        XCTAssertEqual(try ic.traceBytes(LifecycleHistory(),maximum:1024),[1]+Array(repeating:0,count:28))
        let h = try indexed.advance(LifecycleHistory(),events:[.begin(ScopedKey(epoch:2,generation:3,key:7),deadline:10)])
        let bytes = try ic.traceBytes(h,maximum:1024); XCTAssertEqual(bytes.count,57)
        XCTAssertTrue(indexed.equalHistory(h,try ic.fromTrace(bytes)))
        XCTAssertThrowsError(try ic.fromTrace(bytes+[0]))
    }
    func testPairExplicitHandoff() throws {
        let child = Model<Int,Int,Int>(initialState:{0},step:{Transition(state:$0+$1,outputs:[$1])},checkState:{_ in [.passed("safe")]},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},inputs:{_ in InputIterator(values:[1])},equalStates:{$0 == $1})
        let w = PairWiring<Int,Int,Int,Int,Int,Int>(messages:{ o in switch o { case let .left(v): return [.right(v)]; case let .right(v): return [.left(v)] } },checkState:{_,c in try c.push(.passed("global"))})
        let p = try Pair(left:child,right:child,wiring:w,maxPending:1)
        let a = try p.step(p.initialState(),.local(.left(1)))
        XCTAssertEqual(a.state.left,1); XCTAssertEqual(a.state.right,0)
        let b = try p.step(a.state,.deliver(0)); XCTAssertEqual(b.state.right,1); XCTAssertEqual(b.state.pending.count,1)
        let m = p.model(equalLeftInput:{$0 == $1},equalRightInput:{$0 == $1}); XCTAssertFalse(try m.equalStates!(a.state,b.state))
    }
    func testAcknowledgedHandoff() throws {
        func make(_ faulty:Bool) throws -> Network<Int,Int,Int> {
            let machines = [UInt32(10),20,30].map { id in NetworkAddress(id,Model<Int,Int,Int>(initialState:{id == 20 ? 1 : 0},step:{ s,i in
                if id == 10 && i == 1 { return Transition(state:1,outputs:faulty ? [10,11] : [10]) }
                if id == 10 && i == 4 { return Transition(state:2,outputs:[11]) }
                if id == 20 && i == 2 { return Transition(state:0,outputs:[12]) }
                if id == 30 && i == 3 { return Transition(state:1) }
                return Transition(state:s)
            },checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})) }
            let w = NetworkWiring<Int,Int,Int>(routes:{ o in let routes:[Int:(UInt32,Int)] = [10:(20,2),11:(30,3),12:(10,4)]; guard let r = routes[o.value] else { throw ModelError("unknown effect") }; return [NetworkRoute(destination:r.0,channel:0,input:r.1)] },checkState:{ s,c in try c.push(s.machines[1].value+s.machines[2].value>1 ? .failed("unique-owner","two owners") : .passed("unique-owner")) })
            return try Network(machines:machines,wiring:w)
        }
        let safe = try make(false); var t = try safe.step(safe.initialState(),.local(NetworkAddress(10,1)))
        for _ in 0..<3 { t = try safe.step(t.state,.deliver(0)); XCTAssertEqual(try safe.model().checkState(t.state).first?.status,.passed) }
        let bad = try make(true); t = try bad.step(bad.initialState(),.local(NetworkAddress(10,1))); t = try bad.step(t.state,.deliver(1)); XCTAssertEqual(try bad.model().checkState(t.state).first?.status,.failed)
    }
}

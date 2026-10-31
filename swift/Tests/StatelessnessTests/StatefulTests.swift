import XCTest
@testable import Statelessness
final class StatefulTests: XCTestCase {
    func testLifecycleOracleRejectsHistoryBeforeClassifiers() throws {
        let monitor = LifecycleMonitor<Int>(limits: RetentionLimits(obligations: 1, resources: 1, violations: 0), compare: { $0 < $1 ? -1 : $0 > $1 ? 1 : 0 }, cloneKey: { $0 })
        var calls = 0
        let oracle: Oracle<Int, LifecycleHistory<Int>, Int, Int> = monitor.oracle(
            metadata: { Metadata(name: "preflight", build: "test") },
            inputEvents: { _,_ in calls += 1; return [] },
            cloneOutput: { calls += 1; return $0 },
            outputEvents: { _ in calls += 1; return [] })
        var invalid = LifecycleHistory<Int>()
        invalid.obligations = [(1, LifecycleObligation(deadline: 5)), (2, LifecycleObligation(deadline: 5))]
        XCTAssertThrowsError(try oracle.advance(invalid, 0, [1], .accepted))
        XCTAssertEqual(calls, 0)
        var overdue = LifecycleHistory<Int>()
        overdue.obligations = [(1, LifecycleObligation(deadline: 0))]
        XCTAssertThrowsError(try oracle.advance(overdue, 0, [1], .accepted))
        XCTAssertEqual(calls, 0)
        var clones = 0
        let failing = LifecycleMonitor<Int>(compare: { $0 < $1 ? -1 : $0 > $1 ? 1 : 0 }, cloneKey: { key in
            clones += 1
            if clones == 2 { throw ModelError("prior violation key copy") }
            return key
        })
        let failingOracle: Oracle<Int, LifecycleHistory<Int>, Int, Int> = failing.oracle(
            metadata: { Metadata(name: "preflight", build: "test") },
            inputEvents: { _,_ in calls += 1; return [] },
            outputEvents: { _ in calls += 1; return [] })
        XCTAssertThrowsError(try failingOracle.advance(overdue, 0, [], .accepted))
        XCTAssertEqual(calls, 0)
    }
    func testLegacyDuplicateObligationsUseAnyMatchingEntry() throws {
        let monitor = LifecycleMonitor<UInt8>(compare: { $0 < $1 ? -1 : $0 > $1 ? 1 : 0 }, cloneKey: { $0 })
        var history = LifecycleHistory<UInt8>()
        var settled = LifecycleObligation(deadline: 100)
        settled.settled = true
        let pending = LifecycleObligation(deadline: 100)
        history.obligations = [(7, pending), (7, settled)]
        XCTAssertTrue(try monitor.advance(history, events: [.publish(7)]).violations.isEmpty)
        history.obligations = [(7, settled), (7, pending)]
        history.resources = [(7, true)]
        XCTAssertEqual(try monitor.advance(history, events: [.release(7)]).violations.map(\.code), ["release_before_settlement"])
    }
    func testLegacyLifecycleRejectsIndexedOnlyHistory() throws {
        let monitor = LifecycleMonitor<UInt8>(compare: { $0 < $1 ? -1 : $0 > $1 ? 1 : 0 }, cloneKey: { $0 })
        let codec = try monitor.historyCodec(key: ValueCodecs.u8)
        var fenced = LifecycleHistory<UInt8>()
        fenced.retiredBefore = 1
        XCTAssertThrowsError(try monitor.advance(fenced, events: []))
        XCTAssertThrowsError(try codec.traceBytes(fenced, maximum: 1024))
        var published = LifecycleHistory<UInt8>()
        var obligation = LifecycleObligation(deadline: 10)
        obligation.settled = true
        obligation.published = true
        published.obligations = [(1, obligation)]
        XCTAssertThrowsError(try monitor.advance(published, events: []))
        XCTAssertThrowsError(try codec.traceBytes(published, maximum: 1024))
    }
    func testLifecycleOracleCopiesOutputsBeforeInputClassifier() throws {
        final class Output { var key: Int; init(_ key: Int) { self.key = key } }
        let monitor = LifecycleMonitor<Int>(compare: { $0 - $1 }, cloneKey: { $0 })
        let output = Output(1)
        let oracle: Oracle<Int, LifecycleHistory<Int>, Int, Output> = monitor.oracle(
            metadata: { Metadata(name:"snapshot",build:"test") },
            inputEvents: { _,_ in output.key = 9; return [] },
            cloneOutput: { Output($0.key) },
            outputEvents: { [.begin($0[0].key,deadline:10)] })
        let result = try oracle.advance(LifecycleHistory(),0,[output],.accepted)
        XCTAssertEqual(result.obligations.map { $0.0 }, [1])
        XCTAssertEqual(output.key,9)

        // The identity default reproduces the aliasing behavior of the old API.
        output.key = 1
        let identity: Oracle<Int, LifecycleHistory<Int>, Int, Output> = monitor.oracle(
            metadata: { Metadata(name:"snapshot",build:"test") },
            inputEvents: { _,_ in output.key = 9; return [] },
            outputEvents: { [.begin($0[0].key,deadline:10)] })
        XCTAssertEqual(try identity.advance(LifecycleHistory(),0,[output],.accepted).obligations.map { $0.0 }, [9])

        enum TestError: Error { case input, output }
        let failure: Oracle<Int, LifecycleHistory<Int>, Int, Output> = monitor.oracle(
            metadata: { Metadata(name:"snapshot",build:"test") },
            inputEvents: { _,_ in throw TestError.input },
            cloneOutput: { _ in throw TestError.output },
            outputEvents: { _ in XCTFail("output classifier must not run"); return [] })
        XCTAssertThrowsError(try failure.advance(LifecycleHistory(),0,[output],.accepted)) {
            guard case TestError.input = $0 else { return XCTFail("expected input failure, got \($0)") }
        }
    }
    func testLifecycleOracleSnapshotsReferenceKeysBeforeClassifiers() throws {
        final class Key { var value: Int; init(_ value: Int) { self.value = value } }
        let monitor = LifecycleMonitor<Key>(compare: { $0.value - $1.value }, cloneKey: { Key($0.value) })
        let retained = Key(1), classified = Key(2)
        var history = LifecycleHistory<Key>()
        history.obligations = [(retained, LifecycleObligation(deadline: 10))]
        let oracle: Oracle<Int, LifecycleHistory<Key>, Int, Int> = monitor.oracle(
            metadata: { Metadata(name:"snapshot",build:"test") },
            inputEvents: { _,_ in retained.value = 9; return [.begin(classified,deadline:10)] },
            outputEvents: { _ in classified.value = 8; return [] })
        let result = try oracle.advance(history,0,[],.accepted)
        XCTAssertEqual(result.obligations.map { $0.0.value }, [1,2])
        XCTAssertEqual(retained.value,9)
        XCTAssertEqual(classified.value,8)
    }
    func testFencesAndDeadline() throws {
        let m: LifecycleMonitor<ScopedKey<UInt8>> = indexedLifecycleMonitor(limits: RetentionLimits(obligations:2,resources:2,violations:8),cloneKey:{$0})
        var h = LifecycleHistory<ScopedKey<UInt8>>()
        for epoch in 0..<1000 { let k = ScopedKey(epoch:UInt64(epoch),generation:0,key:UInt8(7)); h = try m.advance(h,events:[.begin(k,deadline:10),.acquire(k),.settle(k),.release(k),.fence(before:UInt64(epoch+1))]); XCTAssertEqual(h.obligations.count,0) }
        h = try m.advance(h,events:[.settle(ScopedKey(epoch:0,generation:0,key:7))]); XCTAssertEqual(h.violations.first?.code,"retired_epoch_reference")
        let legacy = LifecycleMonitor<Int>(compare:{$0 < $1 ? -1 : $0 > $1 ? 1 : 0},cloneKey:{$0})
        var old = try legacy.advance(LifecycleHistory(),events:[.begin(7,deadline:10),.tick(11),.cancel(7)]); XCTAssertEqual(old.violations.first?.code,"missing_before_advance")
        old = try legacy.advance(LifecycleHistory(),events:[.begin(7,deadline:10),.tick(10),.settle(7)]); XCTAssertEqual(try legacy.checks(old).first?.status,.passed)
    }
    func testOutOfOrderLateBeginRetainsPriorObligation() throws {
        let monitor: LifecycleMonitor<ScopedKey<UInt8>> = indexedLifecycleMonitor(cloneKey:{$0})
        let high = ScopedKey(epoch:2,generation:0,key:UInt8(2)),low = ScopedKey(epoch:1,generation:0,key:UInt8(1))
        let history = try monitor.advance(LifecycleHistory(),events:[.begin(high,deadline:99),.tick(10),.begin(low,deadline:9)])
        let violation = try XCTUnwrap(history.violations.first { $0.code == "began_after_deadline" })
        XCTAssertEqual(violation.key,low); XCTAssertEqual(violation.prior,LifecycleObligation(deadline:9))
        XCTAssertEqual(history.obligations.map { $0.0 },[low,high])
        let codec = try monitor.historyCodec(key:scopedKeyCodec(ValueCodecs.u8))
        XCTAssertTrue(monitor.equalHistory(history,try codec.fromTrace(codec.traceBytes(history,maximum:4096))))
    }
    func testExplicitNetwork() throws {
        let child = Model<Int,Int,Int>(initialState:{0},step:{Transition(state:$0+$1,outputs:[$1],disposition:.ignored("observed"))},checkState:{_ in [.passed("safe")]},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},inputs:{_ in InputIterator(values:[1])},equalStates:{$0 == $1})
        let w = NetworkWiring<Int,Int,Int>(routes:{[NetworkRoute(destination:($0.machine+1)%3,channel:7,input:$0.value)]},checkState:{_,c in try c.push(.passed("sum"))})
        let n = try Network(machines:[NetworkAddress(2,child),NetworkAddress(0,child),NetworkAddress(1,child)],wiring:w,policy:QueuePolicy(maxPending:2,maxPerChannel:2,allowDrop:true,allowDuplicate:true))
        let initial = try n.initialState(); var t = try n.step(initial,.local(NetworkAddress(0,1)))
        XCTAssertEqual(n.machineIDs,[0,1,2])
        var ids = n.machineIDs; ids.removeLast(); XCTAssertEqual(n.machineIDs,[0,1,2])
        let values = NetworkState<Int,Int>(machines:[NetworkAddress(0,10),NetworkAddress(2,20),NetworkAddress(UInt32.max,30)])
        XCTAssertEqual(values.machine(0),10); XCTAssertEqual(values.machine(2),20); XCTAssertEqual(values.machine(UInt32.max),30)
        XCTAssertNil(values.machine(1)); XCTAssertNil(values.machine(3)); XCTAssertNil(NetworkState<Int,Int>(machines:[]).machine(0))
        XCTAssertEqual(initial.pending.count,0); XCTAssertEqual(t.disposition,.ignored("observed"))
        let dup = try n.step(t.state,.duplicate(0)); XCTAssertThrowsError(try n.step(dup.state,.deliver(1))); XCTAssertThrowsError(try n.step(dup.state,.duplicate(0)))
        for _ in 0..<100 { t = try n.step(t.state,.deliver(0)); XCTAssertEqual(t.state.pending.count,1) }
        XCTAssertEqual(t.state.machines.reduce(0){$0+$1.value},101)
    }
    func testNetworkChannelBoundsUseFullChannelIdentity() throws {
        let child = Model<Int,Int,Int>(initialState:{ 0 },step:{ state,_ in Transition(state:state,outputs:[]) },checkState:{ _ in [] },cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let network = try Network(machines:[NetworkAddress(1,child),NetworkAddress(2,child)],wiring:NetworkWiring<Int,Int,Int>(routes:{ _ in [] },checkState:{ _,_ in }),policy:QueuePolicy(maxPending:5,maxPerChannel:1))
        let states = [NetworkAddress(1,0),NetworkAddress(2,0)]
        func message(_ source: UInt32,_ destination: UInt32,_ channel: UInt32) -> NetworkMessage<Int> { NetworkMessage(source:source,route:NetworkRoute(destination:destination,channel:channel,input:0)) }
        let distinct = [message(1,1,1),message(2,1,1),message(1,2,1),message(1,1,2)]
        XCTAssertNoThrow(try network.validate(NetworkState(machines:states,pending:distinct)))
        XCTAssertThrowsError(try network.validate(NetworkState(machines:states,pending:distinct + [message(1,1,1)])))
        XCTAssertThrowsError(try network.validate(NetworkState(machines:states,pending:[message(3,1,1)])))
    }

    func testNetworkEnumerationDoesNotCloneQueuedPayloads() throws {
        var inputClones = 0
        let child = Model<Int,Int,Int>(initialState:{0},step:{ state,_ in Transition(state:state) },checkState:{_ in []},cloneState:{$0},cloneInput:{ value in inputClones += 1; throw ModelError("queued payload must not be cloned") },cloneOutput:{$0},inputs:{_ in InputIterator(values:[])})
        let wiring = NetworkWiring<Int,Int,Int>(routes:{_ in []},checkState:{_,_ in})
        let network = try Network(machines:[NetworkAddress(1,child)],wiring:wiring,policy:QueuePolicy(maxPending:2,maxPerChannel:2,allowDrop:true,allowDuplicate:true))
        let message = NetworkMessage(source:1,route:NetworkRoute(destination:1,channel:7,input:42))
        let iterator = try network.inputs(NetworkState(machines:[NetworkAddress(1,0)],pending:[message]))
        guard case .deliver(0)? = try iterator.next(), case .drop(0)? = try iterator.next(), case .duplicate(0)? = try iterator.next() else { return XCTFail("unexpected queue actions") }
        XCTAssertNil(try iterator.next())
        XCTAssertEqual(inputClones,0)
    }
    func testLifecycleDecodeRejectsRetentionCountBeforeReadingEntries() throws {
        let wide: LifecycleMonitor<ScopedKey<UInt8>> = indexedLifecycleMonitor(limits:RetentionLimits(obligations:3,resources:3,violations:3),cloneKey:{$0})
        let narrow: LifecycleMonitor<ScopedKey<UInt8>> = indexedLifecycleMonitor(limits:RetentionLimits(obligations:1,resources:1,violations:1),cloneKey:{$0})
        let k1 = ScopedKey(epoch:1,generation:0,key:UInt8(1)),k2 = ScopedKey(epoch:2,generation:0,key:UInt8(2))
        let history = try wide.advance(LifecycleHistory(),events:[.begin(k1,deadline:1),.begin(k2,deadline:1)])
        let writer = try wide.historyCodec(key:scopedKeyCodec(ValueCodecs.u8))
        let bytes = try writer.traceBytes(history,maximum:1024)
        var decodedKeys = 0
        let base = try scopedKeyCodec(ValueCodecs.u8)
        let tracked = TraceValueCodec<ScopedKey<UInt8>>(schema:base.schema,layout:base.layout,encode:{ try base.encodeRaw($0,into:$1) },decode:{ decodedKeys += 1; return try $0.value(base) })
        let reader = try narrow.historyCodec(key:tracked)
        XCTAssertThrowsError(try reader.fromTrace(bytes)); XCTAssertEqual(decodedKeys,0)
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

import XCTest
import Statelessness
@testable import StatelessDebug
final class DiagnosticLiveTests: XCTestCase {
    func testArrivalQueuePreservesFIFOAndCapacityAcrossCompaction() throws {
        var options = ControllerOptions(); options.maxArrivals = 140; options.maxArrivalBytes = 140
        let gate = try LiveController<Int>(epoch:1,options:options)
        for value in 0..<140 { XCTAssertNil(gate.externalArrival(value,ownedBytes:1)) }
        XCTAssertEqual(gate.bufferedArrivals,140); XCTAssertEqual(gate.bufferedBytes,140)
        XCTAssertEqual(gate.externalArrival(999,ownedBytes:1)?.error,.overflow)
        for value in 0..<90 { let (permit,input) = try XCTUnwrap(gate.beginNext()); XCTAssertEqual(input,value); try gate.delivered(permit,outputs:0) }
        for value in 140..<230 { XCTAssertNil(gate.externalArrival(value,ownedBytes:1)) }
        XCTAssertEqual(gate.bufferedArrivals,140)
        for value in 90..<230 { let (permit,input) = try XCTUnwrap(gate.beginNext()); XCTAssertEqual(input,value); try gate.delivered(permit,outputs:0) }
        XCTAssertEqual(gate.bufferedArrivals,0); XCTAssertEqual(gate.bufferedBytes,0); XCTAssertNil(try gate.beginNext())
    }
    func testProbeNoInterestRevisionAndExporterFault() throws {
        let hub = try DiagnosticHub(); var calls = 0
        let context = CaptureContext(producer: UInt64.max,origin: .test,sequence: 1)
        try hub.probe(site: "quantity",severity: .info,context: context) { _,_ in calls += 1; throw ModelError("unexpected") }
        XCTAssertEqual(calls,0)
        try hub.addSink(id: 1,permissions: .allLocal,maxRecords: 1)
        let sub = ProbeSubscription(sink: 1,site: "quantity")
        XCTAssertEqual(try hub.configure(expectedRevision: 0,subscriptions: [sub]),1)
        XCTAssertThrowsError(try hub.configure(expectedRevision: 0,subscriptions: []))
        for n: UInt64 in 1...2 {
            try hub.probe(site: "quantity",severity: .info,context: CaptureContext(producer: 1,origin: .test,sequence: n)) { _,_ in try InspectContext().scalar(.integer(kind: ExactString("u64"),decimal: ExactString(String(UInt64.max)))) }
        }
        XCTAssertEqual(hub.health.dropped,1)
        XCTAssertThrowsError(try hub.export(sink: 1,limit: 1) { _ in throw ModelError("write") }); XCTAssertEqual(hub.health.exporterFaults,1)
    }
    func testThresholdStrictGreaterAndHysteresis() throws {
        let hub = try DiagnosticHub(); try hub.addSink(id: 1,permissions: .allLocal)
        let trigger = ProbeTrigger.aboveU128(threshold: TraceUInt128(high: 0,low: 10),hysteresis: TraceUInt128(high: 0,low: 2),cooldownTurns: 0)
        _ = try hub.configure(expectedRevision: 0,subscriptions: [ProbeSubscription(sink: 1,site: "q",trigger: trigger)])
        for (index,n) in [10,11,12,8,11].enumerated() {
            try hub.probe(site: "q",severity: .info,context: CaptureContext(producer: 1,origin: .test,sequence: UInt64(index))) { _,_ in try InspectContext().scalar(.integer(kind: ExactString("u128"),decimal: ExactString(String(n)))) }
        }
        XCTAssertEqual(try hub.drain(sink: 1,limit: 100).count,2)
    }
    func testSafePointAndSingleUsePermits() throws {
        var options = ControllerOptions(); options.authority.pauseAndStep = true
        let gate = try LiveController<Int>(epoch: UInt64.max-1,options: options)
        XCTAssertNil(gate.externalArrival(3,ownedBytes: 8))
        let (delivery,input) = try XCTUnwrap(gate.beginNext()); XCTAssertEqual(input,3)
        try gate.requestPause(epoch: UInt64.max-1,revision: 0)
        XCTAssertThrowsError(try gate.safePoint())
        try gate.delivered(delivery,outputs: 1); XCTAssertThrowsError(try gate.delivered(delivery,outputs: 1))
        let permit = try gate.takeDispatch(outputIndex: 0)
        XCTAssertThrowsError(try gate.safePoint()); XCTAssertThrowsError(try gate.takeDispatch(outputIndex: 0))
        try gate.dispatched(permit); XCTAssertThrowsError(try gate.dispatched(permit))
        XCTAssertEqual(try gate.safePoint()?.revision,1)
        gate.disconnect(); XCTAssertEqual(try gate.reconnect(),UInt64.max)
        XCTAssertEqual(permit.origin.epoch,UInt64.max-1)
        XCTAssertThrowsError(try gate.resume(epoch: UInt64.max-1,revision: 1))
    }
    func testDisconnectAdmittedDeliveryAndDispatchUncertainty() throws {
        var options = ControllerOptions(); options.detach = .terminate
        let gate = try LiveController<Int>(epoch: 1,options: options)
        XCTAssertNil(gate.externalArrival(1,ownedBytes: 8)); let (permit,_) = try XCTUnwrap(gate.beginNext())
        gate.disconnect(); XCTAssertEqual(gate.phase,.delivering)
        try gate.delivered(permit,outputs: 0); XCTAssertEqual(gate.revision,1); XCTAssertEqual(gate.phase,.terminated)
        let uncertain = try LiveController<Int>(epoch: 2)
        XCTAssertNil(uncertain.externalArrival(1,ownedBytes: 8)); let (delivery,_) = try XCTUnwrap(uncertain.beginNext()); try uncertain.delivered(delivery,outputs: 1)
        let dispatch = try uncertain.takeDispatch(outputIndex: 0); try uncertain.dispatchFailed(dispatch)
        XCTAssertThrowsError(try uncertain.dispatched(dispatch))
        XCTAssertEqual(uncertain.phase,.terminated); XCTAssertThrowsError(try uncertain.beginNext())
    }
}

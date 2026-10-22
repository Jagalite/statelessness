import XCTest
import Statelessness
@testable import StatelessDebug
final class WatchTests: XCTestCase {
    func testNoInterestSkipsProjectionAndConfigurationAuthority() throws {
        let registry = try WatchRegistry(authorization: .denyAll)
        var called = 0
        try registry.observe(snapshot: SnapshotID(session: 1,revision: 1,sequence: 1),origin: .simulation) { _,_ in called += 1; throw ModelError("unexpected") }
        XCTAssertEqual(called,0)
        XCTAssertThrowsError(try registry.add(WatchConfig()))
    }
    func testRevisionSamplingBaselinesAndBoundedLoss() throws {
        let registry = try WatchRegistry(authorization: .localAll,maxRecords: 1)
        var config = WatchConfig(); config.evaluationInterval = 2
        let ack = try registry.add(config); XCTAssertEqual(ack.captureRevision,1)
        var calls = 0
        for n: UInt64 in 1...5 {
            let snapshot = SnapshotID(session: UInt64.max,revision: n,sequence: n)
            try registry.observe(snapshot: snapshot,origin: .simulation) { path,limits in
                calls += 1
                return InspectResult(snapshot: snapshot,schema: DisplaySchema(name: "counter"),path: path,node: try InspectContext(limits: limits).scalar(.integer(kind: ExactString("u64"),decimal: ExactString(String(n)))))
            }
        }
        XCTAssertEqual(calls,3); XCTAssertEqual(registry.health.evaluationSampled,2); XCTAssertEqual(registry.health.droppedEvents,2)
        let event = try registry.drain(limit: 1).first
        XCTAssertEqual(event?.kind,.changeSinceLastSample(true)); XCTAssertEqual(event?.captureRevision,1)
        let updated = try registry.setEnabled(ack.watchID,enabled: false); XCTAssertEqual(updated.captureRevision,2); XCTAssertEqual(updated.generation,2)
    }
    func testIncompleteProjectionNeverEqualAndGapResetsBaseline() throws {
        let registry = try WatchRegistry(authorization: .localAll)
        _ = try registry.add(WatchConfig())
        for n: UInt64 in [1,2,4] {
            let snapshot = SnapshotID(session: 1,revision: n,sequence: n)
            try registry.observe(snapshot: snapshot,origin: .exactReplay) { path,_ in InspectResult(snapshot: snapshot,schema: DisplaySchema(name: "redacted"),path: path,node: InspectNode(kind: .redacted,completeness: .partial(.redacted))) }
        }
        let events = try registry.drain(limit: 100)
        XCTAssertEqual(events.map(\.kind),[.baseline,.unknown,.baseline]); XCTAssertEqual(registry.health.observationGaps,1)
    }
    func testValidatedRedactionTTLAndUpdate() throws {
        let registry = try WatchRegistry(authorization: .localAll)
        let snapshot = SnapshotID(session: 1,revision: 1,sequence: 1)
        XCTAssertThrowsError(try registry.addValidated(WatchConfig(),snapshot: snapshot) { path,_ in InspectResult(snapshot: snapshot,schema: DisplaySchema(name: "secret"),path: path,node: InspectNode(kind: .redacted)) })
        var config = WatchConfig(); config.ttlObservations = 0
        XCTAssertThrowsError(try registry.add(config))
        config.ttlObservations = 1
        let ack = try registry.add(config)
        try registry.observe(snapshot: snapshot,origin: .simulation) { path,_ in InspectResult(snapshot: snapshot,schema: DisplaySchema(name: "x"),path: path,node: InspectNode(kind: .scalar(.bool(true)))) }
        XCTAssertEqual(try registry.drain(limit: 100).map(\.kind),[.baseline,.expired])
        XCTAssertEqual(try registry.list().first?.enabled,false); XCTAssertNil(try registry.current(ack.watchID))
        config.ttlObservations = nil
        XCTAssertEqual(try registry.update(ack.watchID,config: config).generation,2)
    }
    func testSelectorSharedProjectionSourceAndSchemaBoundaries() throws {
        let registry = try WatchRegistry(authorization: .localAll)
        var config = WatchConfig(); config.model = "selected"
        _ = try registry.add(config); _ = try registry.add(config)
        var calls = 0
        let ignored = WatchObservationContext(snapshot: SnapshotID(session: 1,revision: 1,sequence: 1),origin: .simulation)
        try registry.observe(context: ignored) { _,_ in calls += 1; throw ModelError("filtered") }
        XCTAssertEqual(calls,0)
        var selected = WatchObservationContext(snapshot: SnapshotID(session: 1,revision: 2,sequence: 2),origin: .simulation); selected.model = "selected"
        try registry.observe(context: selected) { path,_ in calls += 1; return InspectResult(snapshot: selected.snapshot,schema: DisplaySchema(name: "x"),path: path,node: InspectNode(kind: .scalar(.bool(true)))) }
        XCTAssertEqual(calls,1)
        var next = WatchObservationContext(snapshot: SnapshotID(session: 1,revision: 3,sequence: 3),origin: .exactReplay); next.model = "selected"
        try registry.observe(context: next) { path,_ in InspectResult(snapshot: next.snapshot,schema: DisplaySchema(name: "x"),path: path,node: InspectNode(kind: .scalar(.bool(true)))) }
        let events = try registry.drain(limit: 100)
        XCTAssertEqual(events.suffix(2).map(\.baselineReason),[.sourceChanged,.sourceChanged])
        var changed = WatchObservationContext(snapshot: SnapshotID(session: 1,revision: 4,sequence: 4),origin: .exactReplay); changed.model = "selected"
        try registry.observe(context: changed) { path,_ in InspectResult(snapshot: changed.snapshot,schema: DisplaySchema(name: "y"),path: path,node: InspectNode(kind: .scalar(.bool(true)))) }
        XCTAssertEqual(try registry.drain(limit: 100).map(\.kind),[.schemaChanged,.schemaChanged])
    }
    func testPropertyFailureBypassesEvaluationAndDeliverySampling() throws {
        let registry = try WatchRegistry(authorization: .localAll)
        var config = WatchConfig(); config.evaluationInterval = 10; config.deliveryInterval = 10
        _ = try registry.add(config)
        for n: UInt64 in 1...2 {
            let snapshot = SnapshotID(session: 1,revision: n,sequence: n)
            try registry.observe(snapshot: snapshot,origin: .simulation,propertyFailure: n == 2) { path,_ in InspectResult(snapshot: snapshot,schema: DisplaySchema(name: "x"),path: path,node: InspectNode(kind: .scalar(.bool(true)))) }
        }
        XCTAssertEqual(try registry.drain(limit: 100).map(\.kind),[.baseline,.propertyFailure])
    }
}

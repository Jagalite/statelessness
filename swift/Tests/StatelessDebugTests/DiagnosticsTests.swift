import XCTest
import Foundation
import Statelessness
@testable import StatelessDebug

final class DiagnosticsTests: XCTestCase {
    func value(_ n: String,kind: String = "i128",schema: String = "quantity",version: UInt32 = 1) -> DiagnosticValue {
        DiagnosticValue(schema:DisplaySchema(name:schema,version:version)) { _,limits in try InspectContext(limits:limits).scalar(.integer(kind:ExactString(kind),decimal:ExactString(n))) }
    }
    func setup(_ subscriptions: [ProbeSubscription],limits: DiagnosticLimits = DiagnosticLimits()) throws -> DiagnosticHub {
        let hub = try DiagnosticHub(limits:limits)
        for sink in Set(subscriptions.map(\.sink)) {try hub.addSink(id:sink,permissions:.allLocal)}
        try hub.registerProducer(id:1,epoch:UInt64.max)
        _ = try hub.configureSelected(expectedRevision:0,subscriptions:subscriptions)
        _ = try hub.acknowledge(producer:1,revision:1,effectiveTransition:0)
        return hub
    }
    func emit(_ hub: DiagnosticHub,_ sequence: UInt64,_ n: String,context: CaptureContext = CaptureContext(),schema: String = "quantity",version: UInt32 = 1,runID: UInt64 = UInt64.max) throws {
        let scope = try hub.beginTurn(producer:1,runID:runID,transition:sequence,origin:.test,context:context)
        try scope.probe(site:"q",value:{self.value(n,schema:schema,version:version)})
        try scope.finish(completeTurn:true)
    }
    func testExplicitAcknowledgementKeepsOldConfigurationAndProducerIsolation() throws {
        let hub = try DiagnosticHub(); try hub.addSink(id:1,permissions:.allLocal)
        try hub.registerProducer(id:1,epoch:UInt64.max); try hub.registerProducer(id:2,epoch:9)
        let ack = try hub.configureSelected(expectedRevision:0,subscriptions:[ProbeSubscription(sink:1,site:"q",trigger:.changed)])
        XCTAssertEqual(ack.pendingProducers,[1,2]); XCTAssertEqual(hub.pendingProducers,[1,2])
        do {let scope = try hub.beginTurn(producer:1,runID:5,transition:0,origin:.test); var calls = 0; try scope.probe(site:"q",value:{calls += 1; return self.value("1")}); XCTAssertEqual(calls,0); try scope.finish(completeTurn:true)}
        _ = try hub.acknowledge(producer:1,revision:1,effectiveTransition:1)
        try emit(hub,1,"1",runID:5)
        _ = try hub.configureSelected(expectedRevision:1,subscriptions:[])
        try emit(hub,2,"2",runID:5) // Unacknowledged producer still uses revision one.
        let events = try hub.drain(sink:1,limit:10); XCTAssertEqual(events.map(\.captureRevision),[1,1]); XCTAssertEqual(events.map(\.epoch),[UInt64.max,UInt64.max])
        _ = try hub.acknowledge(producer:1,revision:2,effectiveTransition:3)
        XCTAssertThrowsError(try hub.beginTurn(producer:1,runID:5,transition:2,origin:.test))
        try emit(hub,3,"3",runID:5); XCTAssertEqual(hub.queued(sink:1)?.records,0)
        XCTAssertEqual(hub.producerStatus.first(where:{$0.producer == 2})?.captureRevision,0)
        XCTAssertThrowsError(try hub.acknowledge(producer:1,revision:1,effectiveTransition:4))
    }
    func testValueExactlyOnceAcrossPathsAndNoInterestSkipsFactory() throws {
        var a = ProbeSubscription(sink:1,site:"q",path:[.field(ExactString("a"))]); a.minimumSeverity = .info
        let b = ProbeSubscription(sink:2,site:"q",path:[.field(ExactString("b"))])
        let duplicate = ProbeSubscription(sink:3,site:"q",path:[.field(ExactString("a"))])
        let hub = try setup([a,b,duplicate]); let scope = try hub.beginTurn(producer:1,runID:1,transition:0,origin:.test)
        var factories = 0, projections = 0
        try scope.probe(site:"unselected",value:{factories += 1; throw ModelError("must not run")})
        try scope.probe(site:"q",severity:.info,value:{ factories += 1; return DiagnosticValue(schema:DisplaySchema(name:"pair")) { _,limits in projections += 1; return try InspectContext(limits:limits).scalar(.unit) } })
        XCTAssertEqual(factories,1); XCTAssertEqual(projections,2)
        XCTAssertThrowsError(try hub.export(sink:1,limit:1) {_ in XCTFail("export during capture")})
        try scope.finish(completeTurn:true)
        for sink: UInt64 in [1,2,3] {XCTAssertEqual(try hub.drain(sink:sink,limit:1).count,1)}
    }
    func testSamplingOccursAfterTriggerAndBaselineUpdates() throws {
        var sub = ProbeSubscription(sink:1,site:"q",trigger:.changed); sub.sampleEvery = 2
        let hub = try setup([sub])
        for (index,n) in ["1","2","2","3"].enumerated() {try emit(hub,UInt64(index),n)}
        let events = try hub.drain(sink:1,limit:10)
        XCTAssertEqual(events.map(\.transition),[0,3])
        XCTAssertEqual(hub.health.sampled,1); XCTAssertEqual(hub.health.filtered,1)
    }
    func testSourceSchemaGapSelectorAndProducerBaselinesAreIndependent() throws {
        var sub = ProbeSubscription(sink:1,site:"q",trigger:.changed); sub.selector.entity = "one"
        let hub = try setup([sub]); var selected = CaptureContext(); selected.entity = "one"
        try emit(hub,0,"7",context:selected)
        try emit(hub,1,"7",context:selected,runID:8)
        try emit(hub,3,"7",context:selected,runID:8)
        try emit(hub,4,"7",context:selected,schema:"other",runID:8)
        var absent = selected; absent.entity = "two"; try emit(hub,5,"7",context:absent,runID:8)
        try emit(hub,6,"7",context:selected,schema:"other",runID:8)
        let events = try hub.drain(sink:1,limit:20)
        XCTAssertEqual(events.map(\.baselineReason),[.initial,.sourceChanged,.observationGap,.schemaChanged,.selectorGap])
        XCTAssertEqual(events.map(\.changeUnknown),[false,true,true,true,true]); XCTAssertEqual(hub.health.observationGaps,1)
    }
    func testThresholdSignedPositiveFullWidthCooldownAndIncompleteReset() throws {
        let threshold = try TraceUInt128(decimal:"18446744073709551616")
        let sub = ProbeSubscription(sink:1,site:"q",trigger:.aboveU128(threshold:threshold,hysteresis:TraceUInt128(high:0,low:2),cooldownTurns:3))
        let hub = try setup([sub])
        try emit(hub,0,"18446744073709551617") // Positive i128 projects are accepted by Rust parse-u128.
        try emit(hub,1,"18446744073709551614")
        try emit(hub,2,"18446744073709551617") // Still cooldown; state remains rearmed.
        try emit(hub,3,"18446744073709551617")
        do {
            let scope = try hub.beginTurn(producer:1,runID:UInt64.max,transition:4,origin:.test)
            try scope.probe(site:"q",value:{DiagnosticValue(schema:DisplaySchema(name:"quantity")) {_,_ in InspectNode(kind:.redacted,completeness:.partial(.redacted))}})
            try scope.finish(completeTurn:true)
        }
        try emit(hub,5,"18446744073709551617") // Incomplete reset clears cooldown and high latch.
        let events = try hub.drain(sink:1,limit:20); XCTAssertEqual(events.map(\.transition),[0,3,5]); XCTAssertEqual(events.last?.baselineReason,.inspectionIncomplete); XCTAssertTrue(events.last!.changeUnknown)
    }
    func testScopedPublicationIncompleteDropAndIndependentSinkPolicies() throws {
        let hub = try DiagnosticHub(); var newest = DiagnosticSinkLimits(); newest.records = 1
        var oldest = newest; oldest.policy = .oldest
        try hub.addSink(id:1,permissions:.allLocal,limits:newest); try hub.addSink(id:2,permissions:.allLocal,limits:oldest)
        try hub.registerProducer(id:1,epoch:1)
        _ = try hub.configureSelected(expectedRevision:0,subscriptions:[ProbeSubscription(sink:1,site:"q"),ProbeSubscription(sink:2,site:"q")]); _ = try hub.acknowledge(producer:1,revision:1,effectiveTransition:0)
        do {let scope = try hub.beginTurn(producer:1,runID:1,transition:0,origin:.test); try scope.probe(site:"q",value:{self.value("1")}); XCTAssertEqual(hub.queued(sink:1)?.records,0)}
        XCTAssertTrue(try hub.peekEvents(sink:1,maximum:1)[0].incompleteTurn)
        try emit(hub,1,"2",runID:1)
        XCTAssertEqual(try hub.peekEvents(sink:1,maximum:1)[0].transition,0)
        XCTAssertEqual(try hub.peekEvents(sink:2,maximum:1)[0].transition,1)
        XCTAssertEqual(hub.sinkHealth(1)?.dropped,1); XCTAssertEqual(hub.sinkHealth(2)?.dropped,1)
        XCTAssertEqual(try hub.abandon(sink:1),1); XCTAssertEqual(hub.sinkHealth(1)?.abandoned,1)
    }
    func testBaselineEvictionAndExporterFaultPreserveUntouchedQueue() throws {
        var limits = DiagnosticLimits(); limits.baselineBytes = 0
        let hub = try setup([ProbeSubscription(sink:1,site:"q",trigger:.changed)],limits:limits)
        try emit(hub,0,"1"); try emit(hub,1,"1")
        XCTAssertEqual(hub.sinkHealth(1)?.baselineEvicted,2)
        XCTAssertEqual(try hub.peekEvents(sink:1,maximum:10).last?.baselineReason,.evicted)
        let count = try hub.drainTo(sink:1,maximum:10) { _ in
            // Export I/O is outside capture: a read and producer registration are permitted.
            XCTAssertEqual(hub.queued(sink:1)?.records,1); try hub.registerProducer(id:2,epoch:1); throw ModelError("writer failed")
        }
        XCTAssertEqual(count,0); XCTAssertEqual(hub.queued(sink:1)?.records,1); XCTAssertEqual(hub.sinkHealth(1)?.exporterFaults,1)
    }
    func testTurnBudgetAndRedactedProjectionNeverEvaluatesGetter() throws {
        var limits = DiagnosticLimits(); limits.turnRecords = 1
        let hub = try setup([ProbeSubscription(sink:1,site:"q"),ProbeSubscription(sink:1,site:"q")],limits:limits)
        let scope = try hub.beginTurn(producer:1,runID:1,transition:0,origin:.test); var getter = 0
        try scope.probe(site:"q",value:{DiagnosticValue(schema:DisplaySchema(name:"secret")) {_,limits in
            let context = try InspectContext(limits:limits)
            return try context.object("secret") {cx in [try cx.field("token",redacted:true) {_ in getter += 1; throw ModelError("secret getter")}]}
        }})
        try scope.finish(completeTurn:false)
        XCTAssertEqual(getter,0); XCTAssertEqual(hub.sinkHealth(1)?.dropped,1)
        let event = try XCTUnwrap(hub.pop(sink:1)); XCTAssertTrue(event.incompleteTurn); XCTAssertTrue(event.changeUnknown)
    }
    func testManualEventJSONGoldenAndBoundedExactSidecarBinding() throws {
        let hub = try setup([ProbeSubscription(sink:1,site:"q")]); try emit(hub,0,"7")
        let event = try XCTUnwrap(hub.pop(sink:1))
        let golden = #"{"schema_version":"1","run_id":"18446744073709551615","producer":"1","epoch":"18446744073709551615","producer_sequence":"1","capture_revision":"1","transition":"0","origin":"Test","severity":"Debug","site":"q","kind":"Probe","path":[],"display_schema":{"name":"quantity","version":"1"},"baseline":true,"baseline_reason":"Initial","change_unknown":false,"incomplete_turn":false,"payload":{"kind":"integer","value":{"type":"I128","decimal":"7"},"completeness":{"kind":"complete","reason":null},"child_count":null,"page":null,"children":[]}}"#
        XCTAssertEqual(try diagnosticEventJSON(event),golden)
        let trace = AuditTrace(metadata:Metadata(name:"manual",build:"golden"),config:RunConfig(strategy:"manual"),initialState:[0],initialChecks:[],steps:[],termination:.completed)
        let manifest = try DiagnosticSidecarManifest(trace:trace,runID:UInt64.max)
        let bytes = try manifest.jsonl(events:[event],maxEvents:1,maxBytes:10000)
        let firstLine = Data(bytes.prefix(through:try XCTUnwrap(bytes.firstIndex(of:10))))
        let decoded = try DiagnosticSidecarManifest.readHeader(firstLine); try decoded.validate(trace:trace)
        let changed = AuditTrace(metadata:trace.metadata,config:trace.config,initialState:[1],initialChecks:[],steps:[],termination:.completed)
        XCTAssertThrowsError(try decoded.validate(trace:changed))
        var writes = 0
        XCTAssertThrowsError(try manifest.writeJSONL(events:[event],maxEvents:1,maxBytes:1) {_ in writes += 1})
        XCTAssertEqual(writes,0)
        XCTAssertThrowsError(try DiagnosticSidecarManifest.readHeader(Data(repeating:65,count:4097)))
        XCTAssertThrowsError(try diagnosticNodeJSON(InspectNode(kind:.scalar(.character(0x110000)))))
        XCTAssertThrowsError(try diagnosticPathJSON([.typedMapKey(.character(0xD800))]))
        var output = Data(); try DiagnosticJSONLSink {output.append($0)}.emit(event)
        XCTAssertEqual(output,Data((golden+"\n").utf8))
    }
    func testReconfiguredTypeUnavailableAndStaleEffectiveBoundary() throws {
        let sub = ProbeSubscription(sink:1,site:"q",trigger:.changed)
        let hub = try setup([sub]); try emit(hub,0,"1")
        _ = try hub.configureSelected(expectedRevision:1,subscriptions:[sub])
        XCTAssertThrowsError(try hub.acknowledge(producer:1,revision:2,effectiveTransition:0))
        _ = try hub.acknowledge(producer:1,revision:2,effectiveTransition:3)
        XCTAssertThrowsError(try hub.beginTurn(producer:1,runID:1,transition:2,origin:.test))
        try emit(hub,3,"1")
        XCTAssertEqual(try hub.drain(sink:1,limit:10).last?.baselineReason,.observationGap)
        var threshold = sub; threshold.trigger = .aboveU128(threshold:TraceUInt128(high:0,low:0),hysteresis:TraceUInt128(high:0,low:0),cooldownTurns:100)
        _ = try hub.configureSelected(expectedRevision:2,subscriptions:[threshold]); _ = try hub.acknowledge(producer:1,revision:3,effectiveTransition:4)
        try emit(hub,4,"-1"); try emit(hub,5,"1")
        let event = try XCTUnwrap(hub.pop(sink:1)); XCTAssertEqual(event.baselineReason,.typeUnavailable); XCTAssertTrue(event.changeUnknown)
    }

    func testTypedConfigurationRejectsInvalidMetadataWithoutRevisionChange() throws {
        let hub = try DiagnosticHub(); try hub.addSink(id:1,permissions:.allLocal)
        XCTAssertThrowsError(try hub.configureSelected(expectedRevision:0,subscriptions:[ProbeSubscription(sink:1,site:"q",trigger:.equals(.character(0x110000)))]))
        XCTAssertEqual(hub.captureRevision,0)
        XCTAssertThrowsError(try hub.configureSelected(expectedRevision:0,subscriptions:[ProbeSubscription(sink:1,site:"q",path:[.typedMapKey(.character(0xD800))])]))
        XCTAssertEqual(hub.captureRevision,0)
        var subscription = ProbeSubscription(sink:1,site:"q"); subscription.selector.entity = ""
        XCTAssertThrowsError(try hub.configureSelected(expectedRevision:0,subscriptions:[subscription]))
        XCTAssertEqual(hub.captureRevision,0)
    }

}

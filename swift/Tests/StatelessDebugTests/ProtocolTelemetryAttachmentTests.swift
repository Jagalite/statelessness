import XCTest
import Foundation
import Statelessness
@testable import StatelessDebug

final class ProtocolTelemetryAttachmentTests: XCTestCase {
    func testRustGeneratedEffectWireFixture() throws {
        let root = URL(fileURLWithPath:#filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let path = ProcessInfo.processInfo.environment["STATELESS_SWIFT_EFFECT_WIRE"].map { URL(fileURLWithPath:$0) } ?? root.appendingPathComponent("conformance/swift-effect-wire-v1.tsv")
        let fixture = try String(contentsOf:path,encoding:.utf8)
        let expected = Dictionary(uniqueKeysWithValues:fixture.split(separator:"\n").map { line -> (String,[Substring]) in
            let fields = line.split(separator:"\t",omittingEmptySubsequences:false); return (String(fields[0]),Array(fields.dropFirst(9)))
        })
        var ticks: UInt64 = 0
        let clock = DiagnosticClock { defer { ticks += 100 }; return LocalInstant(domain:7,nanos:ticks) }
        var options = EffectOptions(); options.origin = .testClock
        let observer = try EffectObserver(options:options,clock:clock,schema:HistogramSchema())
        let effect = try observer.requested(origin:RequestOrigin(run:1,epoch:1,machine:1,transitionSequence:1,outputIndex:0))
        try observer.debuggerPause(true); try observer.admission(effect,accepted:true)
        let attempt = try observer.attemptCreated(effect); try observer.attemptStarted(attempt)
        let adapter = try make(observer)
        for (index,item) in [("details","effect_details",["u64:1","u64:1","u64:1"]),("timeline","effect_timeline",["u64:1","u64:1","u64:1","u64:4"])].enumerated() {
            let response = adapter.handle(ProtocolRequest(session:"counter",epoch:adapter.epoch,requestID:UInt64(index+1),command:item.1,arguments:item.2))
            let wire = String(decoding:try ProtocolWire.encodeResponse(response),as:UTF8.self)
            XCTAssertEqual(Array(wire.split(separator:"\t",omittingEmptySubsequences:false).dropFirst(8)),expected[item.0],item.0)
        }
        withExtendedLifetime((effect,attempt)) {}
    }
    func testEffectLabelAndAggregateDebuggerAffectedAreDistinct() throws {
        var options = EffectOptions(); options.timings = false
        let observer = try EffectObserver(options:options,clock:DiagnosticClock { throw MeasurementError.clockUnavailable },schema:HistogramSchema())
        let effect = try observer.requested(origin:RequestOrigin(run:1,epoch:1,machine:1,transitionSequence:1,outputIndex:0))
        try observer.debuggerPause(true); try observer.admission(effect,accepted:true)
        let attempt = try observer.attemptCreated(effect); try observer.attemptStarted(attempt)
        let p = try make(observer)
        let response = p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:1,command:"effect_details",arguments:["u64:1","u64:1","u64:1"]))
        XCTAssertNil(response.error); XCTAssertEqual(response.fields["effect.labels.debugger_affected"],"bool:false"); XCTAssertEqual(response.fields["effect.debugger_affected"],"bool:true")
        XCTAssertLessThan(response.fieldOrder!.firstIndex(of:"effect.labels.debugger_affected")!,response.fieldOrder!.firstIndex(of:"effect.debugger_affected")!)
        withExtendedLifetime((effect,attempt)) {}
    }
    func testZeroBoundaryEffectOriginIsRetainedOnWire() throws {
        var options = EffectOptions(); options.timings = false
        let observer = try EffectObserver(options:options,clock:DiagnosticClock { throw MeasurementError.clockUnavailable },schema:HistogramSchema())
        let origin = RequestOrigin(run:1,epoch:1,machine:UInt64.max,transitionSequence:0,outputIndex:UInt32.max)
        let effect = try observer.requested(origin:origin)
        let p = try make(observer)
        for (id,command,args) in [(UInt64(1),"effect_details",["u64:1","u64:1","u64:1"]),(2,"effect_timeline",["u64:1","u64:1","u64:1","u64:1"])] {
            let result = p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:id,command:command,arguments:args))
            XCTAssertNil(result.error); XCTAssertEqual(result.fields["effect.origin.run"],"u64:1"); XCTAssertEqual(result.fields["effect.origin.epoch"],"u64:1")
            XCTAssertEqual(result.fields["effect.origin.machine"],"u64:18446744073709551615"); XCTAssertEqual(result.fields["effect.origin.transition"],"u64:0"); XCTAssertEqual(result.fields["effect.origin.output_index"],"u64:4294967295")
        }
        withExtendedLifetime(effect) {}
    }
    func testProbeAndTelemetryHostsCoexist() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        let hub = try DiagnosticHub(); try hub.addSink(id:9,permissions:.allLocal)
        let profile = ProtocolProbeProfile(id:2,label:"marker",subscription:ProbeSubscription(sink:9,site:"site",kind:.marker))
        let probeHost = try diagnosticProbeHost(hub:hub,profiles:[profile],sinks:[9])
        var options = EffectOptions(); options.timings = false
        let observer = try EffectObserver(options:options,clock:DiagnosticClock { throw MeasurementError.clockUnavailable },schema:HistogramSchema())
        let combined = try combineProtocolHosts([probeHost,telemetryProtocolHost(observer:observer)])
        XCTAssertThrowsError(try combineProtocolHosts([probeHost,probeHost]))
        var authority = ProtocolAuthority(); authority.configureDiagnostics = true
        let p = try ProtocolAdapter(session:session,authority:authority,host:combined)
        func send(_ id: UInt64,_ command: String,_ args: [String]) -> ProtocolResponse { p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:id,command:command,arguments:args)) }
        let status = send(1,"status",[])
        XCTAssertNil(status.error); XCTAssertEqual(status.fields["capability.probe_queries"],"bool:true"); XCTAssertEqual(status.fields["capability.metric_queries"],"bool:true")
        XCTAssertNil(send(2,"probe_configure",["u64:0","u64:2"]).error); XCTAssertEqual(hub.captureRevision,1)
        let catalog = send(3,"metric_catalog",["u64:0","u64:1"])
        XCTAssertNil(catalog.error); XCTAssertEqual(catalog.fields["catalog.0.family"],"enum:Requests")
        XCTAssertEqual(catalog.fieldOrder,["catalog.total","catalog.next_offset","catalog.0.family","catalog.0.name","catalog.0.kind","catalog.0.unit","catalog.0.population","catalog.0.reset"])
        XCTAssertNil(send(4,"telemetry_health",[]).error)
        XCTAssertEqual(send(5,"snapshot",[]).fields["handle"],"u64:1")
        let metrics = send(6,"metric_snapshot",["none","u64:0","u64:1"])
        XCTAssertNil(metrics.error); XCTAssertEqual(metrics.fields["metrics.handle"],"u64:2")
        XCTAssertEqual(Array(metrics.fieldOrder!.prefix(11)),["metrics.handle","metrics.revision","metrics.epoch","metrics.complete","metrics.temporality","metrics.gauge_scope","metrics.histogram_schema","metrics.histogram_storage_unit","metrics.series.total","metrics.series.offset","metrics.series.next_offset"])
    }
    func testStagedSnapshotDoesNotOverwriteUnversionedHealthChanges() throws {
        let registry = try MetricRegistry(epoch:1,schema:HistogramSchema())
        let (_,commit) = try registry.prepareSnapshot(at:nil)
        registry.noteOmittedMeasurement()
        XCTAssertThrowsError(try commit())
        XCTAssertEqual(registry.health.omittedMeasurements,1)
        XCTAssertTrue(registry.availableWindows.isEmpty)
    }
    func testClockCannotReenterProtocolSession() throws {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        var armed = false, rejected = false
        let observer = try EffectObserver(clock:DiagnosticClock {
            if armed { do { try session.cancel(expectedRevision:0); XCTFail("clock reentered session") } catch DebugError.reentrant { rejected = true } }
            return LocalInstant(domain:1,nanos:0)
        },schema:HistogramSchema())
        let p = try ProtocolAdapter(session:session,host:telemetryProtocolHost(observer:observer))
        armed = true
        let r = p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:1,command:"metric_snapshot",arguments:["none","u64:0","u64:1"]))
        XCTAssertNil(r.error); XCTAssertTrue(rejected); XCTAssertEqual(session.phase,.ready)
    }
    func testClockMutationCannotPublishSnapshot() throws {
        var observer: EffectObserver!
        let clock = DiagnosticClock {
            if let observer { try observer.metrics.increment(.requests) }
            return LocalInstant(domain:1,nanos:0)
        }
        observer = try EffectObserver(clock:clock,schema:HistogramSchema())
        let p = try make(observer)
        let response = p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:1,command:"metric_snapshot",arguments:["none","u64:0","u64:1"]))
        XCTAssertEqual(response.error,.busy); XCTAssertTrue(observer.availableWindows.isEmpty)
    }
    func make(_ observer: EffectObserver,limits: ProtocolLimits = ProtocolLimits()) throws -> ProtocolAdapter<Int,Int,Int> {
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s,outputs:[])},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"counter",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        return try ProtocolAdapter(session:session,limits:limits,host:telemetryProtocolHost(observer:observer,limits:limits))
    }
    func testEffectAndTelemetryUseObserverEvidence() throws {
        var options = EffectOptions(); options.maxEventsPerEffect = 1; options.timings = false
        let observer = try EffectObserver(options:options,clock:DiagnosticClock { throw MeasurementError.clockUnavailable },schema:HistogramSchema())
        let effect = try observer.requested(origin:RequestOrigin(run:1,epoch:1,machine:7,transitionSequence:1,outputIndex:0))
        try observer.admission(effect,accepted:true); try observer.resolved(effect,outcome:.success)
        let p = try make(observer)
        func send(_ id: UInt64,_ command: String,_ args: [String]) -> ProtocolResponse { p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:id,command:command,arguments:args)) }
        let detail = send(1,"effect_details",["u64:1","u64:1","u64:1"])
        XCTAssertNil(detail.error); XCTAssertEqual(detail.fields["effect.origin.machine"],"u64:7")
        XCTAssertEqual(Array(detail.fieldOrder!.prefix(8)),["effect.run","effect.epoch","effect.serial","effect.origin.run","effect.origin.epoch","effect.origin.machine","effect.origin.transition","effect.origin.output_index"])
        XCTAssertEqual(detail.fields["effect.resolution"],"enum:Success"); XCTAssertEqual(detail.fields["effect.complete"],"bool:false")
        let timeline = send(2,"effect_timeline",["u64:1","u64:1","u64:1","u64:1"])
        XCTAssertNil(timeline.error); XCTAssertEqual(timeline.fields["timeline.events.0.kind"],"enum:requested")
        let health = send(3,"telemetry_health",[])
        XCTAssertNil(health.error); XCTAssertEqual(health.fields["telemetry.detail_event_drops"],"u64:2")
        XCTAssertEqual(health.fields["telemetry.clock_failures"],"u64:0")
        XCTAssertEqual(send(4,"effect_details",["u64:1","u64:1","u64:999"]).error,.staleHandle)
        XCTAssertEqual(send(5,"effect_details",["u64:2","u64:1","u64:1"]).error,.wrongEpoch)
        XCTAssertEqual(send(6,"effect_timeline",["u64:1","u64:2","u64:1","u64:1"]).error,.wrongEpoch)
        withExtendedLifetime(effect) {}
    }
    func testMetricCaptureIsStableAndFailedPreflightDoesNotRetain() throws {
        var options = EffectOptions(); options.timings = false
        let observer = try EffectObserver(options:options,clock:DiagnosticClock { throw MeasurementError.clockUnavailable },schema:HistogramSchema(finiteBoundsNS:[10,100]))
        let effect = try observer.requested()
        var tiny = ProtocolLimits(); tiny.maxResponseFields = 5
        let limited = try make(observer,limits:tiny)
        let failed = limited.handle(ProtocolRequest(session:"counter",epoch:limited.epoch,requestID:1,command:"metric_snapshot",arguments:["none","u64:0","u64:1"]))
        XCTAssertEqual(failed.error,.limit); XCTAssertTrue(observer.availableWindows.isEmpty)
        let snapshotAfterFailure = limited.handle(ProtocolRequest(session:"counter",epoch:limited.epoch,requestID:2,command:"snapshot"))
        XCTAssertNil(snapshotAfterFailure.error); XCTAssertEqual(snapshotAfterFailure.fields["handle"],"u64:1")
        let p = try make(observer)
        func send(_ id: UInt64,_ args: [String]) -> ProtocolResponse { p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:id,command:"metric_snapshot",arguments:args)) }
        let first = send(1,["none","u64:0","u64:1"])
        XCTAssertNil(first.error); XCTAssertEqual(first.fields["metrics.handle"],"u64:1")
        XCTAssertEqual(first.fields["metrics.series.0.name"],"string:effect_requests_total")
        let revision = first.fields["metrics.revision"]
        try observer.admission(effect,accepted:true)
        let again = send(2,["u64:1","u64:0","u64:1"])
        XCTAssertNil(again.error); XCTAssertEqual(again.fields["metrics.revision"],revision)
        let second = send(3,["none","u64:0","u64:1"])
        XCTAssertNil(second.error); XCTAssertEqual(second.fields["metrics.handle"],"u64:2")
        let window = p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:4,command:"metric_window",arguments:[revision!,second.fields["metrics.revision"]!,"u64:1"]))
        XCTAssertNil(window.error); XCTAssertEqual(window.fields["metrics.temporality"],"enum:Delta"); XCTAssertEqual(window.fields["metrics.handle"],"u64:3")
        XCTAssertEqual(send(5,["u64:1","u64:0","u64:1"]).error,.staleHandle)
        withExtendedLifetime(effect) {}
    }
    func testSnapshotAndMetricCapturesShareHandleSequence() throws {
        var options = EffectOptions(); options.timings = false
        let observer = try EffectObserver(options:options,clock:DiagnosticClock { throw MeasurementError.clockUnavailable },schema:HistogramSchema())
        let p = try make(observer)
        func send(_ id: UInt64,_ command: String,_ args: [String] = []) -> ProtocolResponse { p.handle(ProtocolRequest(session:"counter",epoch:p.epoch,requestID:id,command:command,arguments:args)) }
        XCTAssertEqual(send(1,"snapshot").fields["handle"],"u64:1")
        let metric1 = send(2,"metric_snapshot",["none","u64:0","u64:1"])
        XCTAssertNil(metric1.error); XCTAssertEqual(metric1.fields["metrics.handle"],"u64:2")
        XCTAssertEqual(send(3,"snapshot").fields["handle"],"u64:3")
        let metric2 = send(4,"metric_snapshot",["none","u64:0","u64:1"])
        XCTAssertNil(metric2.error); XCTAssertEqual(metric2.fields["metrics.handle"],"u64:4")
        let window = send(5,"metric_window",[metric1.fields["metrics.revision"]!,metric2.fields["metrics.revision"]!,"u64:1"])
        XCTAssertNil(window.error); XCTAssertEqual(window.fields["metrics.handle"],"u64:5")
        XCTAssertEqual(send(6,"checks",["u64:2"]).error,.staleHandle)
        XCTAssertNil(send(7,"checks",["u64:1"]).error)
        try p.disconnect(); _ = try p.reconnect()
        XCTAssertEqual(send(1,"metric_snapshot",["u64:5","u64:0","u64:1"]).error,.staleHandle)
        XCTAssertEqual(send(2,"snapshot").fields["handle"],"u64:6")
    }
}

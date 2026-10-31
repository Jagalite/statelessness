import XCTest
import Foundation
import Statelessness
@testable import StatelessDebug

final class DashboardTests: XCTestCase {
    func testExactFullWidthWireGoldenAndUnknownSeries() throws {
        let registry = try MetricRegistry(epoch: UInt64.max,schema: HistogramSchema(),start: LocalInstant(domain: 9,nanos: 0))
        try registry.increment(.requests,by: UInt64.max)
        var health = TelemetryHealth(); health.effectHandleBytes = 0; health.attemptHandleBytes = 0; health.deliveryHandleBytes = 0
        let view = try Dashboard(snapshot: registry.current(),health: health,labels: [MetricLabels()],maxRows: 1)
        let actual = try view.json()
        XCTAssertTrue(actual.contains("\"epoch\":18446744073709551615")); XCTAssertTrue(actual.contains("\"value\":18446744073709551615"))
        let golden = #"{"schema":"statelessness.effect-dashboard/1","epoch":18446744073709551615,"revision":1,"temporality":"cumulative","start":{"clock_domain":9,"nanos":0},"end":null,"truncated":true,"observation_complete":false,"health":{"dropped_detail_events":0,"detail_evictions":0,"detail_rejections":0,"clock_failures":0,"callback_faults":0,"destructor_hook_losses":0,"invalid_lifecycle":0,"active_effect_handles":0,"omitted_measurements":0,"invalid_measurements":0,"rejected_series":0,"invalid_labels":0,"arithmetic_errors":0,"imported_ignored":0,"snapshots_evicted":0,"snapshot_rejections":0,"duplicate_observations":0,"duplicate_deliveries":0,"abandoned":0,"pause_interval_evictions":0,"debugger_affected_measurements":0,"retained_detail_records":0,"retained_detail_bytes":0,"effect_handle_bytes":0,"attempt_handle_bytes":0,"delivery_handle_bytes":0,"unobserved_native_debugger_interference_possible":true,"host_metric_health":{"rejected_series":0,"invalid_labels":0,"arithmetic_errors":0,"omitted_measurements":0,"invalid_measurements":0,"imported_ignored":0,"snapshots_evicted":0,"snapshot_rejections":0}},"rows":[{"metric":"effect_requests_total","operation":0,"component":0,"worker_pool":0,"outcome":"None","measurement_origin":"Live","debugger_affected":false,"value":18446744073709551615,"gauge_scope":null,"p50":null,"p95":null,"p99":null}]}"#
        let parsed = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(actual.utf8)) as? NSDictionary)
        let expected = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(golden.utf8)) as? NSDictionary)
        XCTAssertEqual(parsed,expected)
        let all = try Dashboard(snapshot: registry.current(),health: TelemetryHealth(),labels: [MetricLabels()])
        XCTAssertEqual(all.rows.count,18); XCTAssertTrue(all.observationComplete); XCTAssertNil(all.rows[1].value)
        XCTAssertThrowsError(try all.json(maxBytes: 20))
        let full = try all.json(); XCTAssertEqual(try all.json(maxBytes: full.utf8.count),full)
    }
    func testHistogramQuantilesScopesAndPassiveCapture() throws {
        let schema = try HistogramSchema(finiteBoundsNS: [10,20]),registry = try MetricRegistry(epoch: 1,schema: schema,gaugeScope: .hostSnapshot)
        for n: UInt64 in [0,10,11,30] { try registry.observe(.queueWait,quantity: n) }
        try registry.setGauge(.running,value: UInt64.max)
        let revision = registry.revision,windows = registry.availableWindows
        let dashboard = try Dashboard(snapshot: registry.current(),health: TelemetryHealth(),labels: [MetricLabels()])
        let json = try dashboard.json()
        XCTAssertTrue(json.contains("\"upper_bound_ns\":null,\"overflow_bucket\":true")); XCTAssertTrue(json.contains("\"gauge_scope\":\"HostSnapshot\""))
        XCTAssertEqual(registry.revision,revision); XCTAssertEqual(registry.availableWindows,windows)
        XCTAssertEqual(dashboard.rows.first(where: { $0.family == .queueWait })?.p50?.upperBoundNS,10)
    }
    func testFullWidthCorrelationIdentityBoundaries() throws {
        let trace = AuditTrace(metadata: Metadata(name: "join"),config: RunConfig(strategy: "manual"),initialState: [0],initialChecks: [],steps: [TraceStep(input: [1],disposition: .accepted,outputs: [[99]],postState: [1],checks: [])],termination: .completed)
        let evidence = TraceV2(trace: trace,checkpointAfter: UInt64.max-1,provenance: Provenance(run: String(UInt64.max),epoch: UInt64.max),steps: [StepEvidence(sequence: UInt64.max,stateCheckCount: 0,causal: CausalContext(machine: String(UInt64.max)))])
        var options = EffectOptions(); options.run = UInt64.max; options.epoch = UInt64.max; options.timings = false
        let observer = try EffectObserver(options: options,clock: DiagnosticClock { throw MeasurementError.clockUnavailable },schema: HistogramSchema())
        let origin = RequestOrigin(run: UInt64.max,epoch: UInt64.max,machine: UInt64.max,transitionSequence: UInt64.max,outputIndex: 0)
        let handle = try observer.requested(origin: origin)
        XCTAssertEqual(correlate(evidence,try XCTUnwrap(observer.effectDetails(handle.id))),.retained(localSequence: 1,output: 0,detailComplete: true))
        let oldOrigin = RequestOrigin(run: UInt64.max,epoch: UInt64.max,machine: UInt64.max,transitionSequence: UInt64.max-1,outputIndex: 0)
        let old = try observer.requested(origin: oldOrigin)
        XCTAssertEqual(correlate(evidence,try XCTUnwrap(observer.effectDetails(old.id))),.beforeCheckpoint(oldOrigin))
        let wrong = try observer.requested(origin: RequestOrigin(run: UInt64.max,epoch: UInt64.max,machine: 2,transitionSequence: UInt64.max,outputIndex: 0))
        XCTAssertEqual(correlate(evidence,try XCTUnwrap(observer.effectDetails(wrong.id))),.conflict("machine"))
    }
}

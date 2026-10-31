import XCTest
import Statelessness
@testable import StatelessDebug
final class MetricsTests: XCTestCase {
    func testHistogramLookup() throws {
        let registry = try MetricRegistry(epoch: 1,schema: HistogramSchema())
        try registry.observe(.queueWait,quantity: 12)
        try registry.increment(.requests)
        let snapshot = registry.current()
        XCTAssertEqual(snapshot.histogram(.queueWait)?.count,1)
        XCTAssertNil(snapshot.histogram(.requests))
        XCTAssertNil(snapshot.histogram(.attemptElapsed))
    }
    func testExplicitClockAndHistogramOverflowGolden() throws {
        XCTAssertEqual(try LocalInstant(domain: 7,nanos: UInt64.max).elapsed(since: LocalInstant(domain: 7,nanos: UInt64.max-4)),4)
        XCTAssertThrowsError(try LocalInstant(domain: 8,nanos: 9).elapsed(since: LocalInstant(domain: 7,nanos: 4)))
        let schema = try HistogramSchema(id: UInt64.max,finiteBoundsNS: [10,100])
        var h = Histogram(); for n: UInt64 in [0,10,11,101] { try h.add(n,schema: schema) }
        XCTAssertEqual(h.buckets[0],2); XCTAssertEqual(h.buckets[1],1); XCTAssertEqual(h.buckets[2],1)
        XCTAssertEqual(h.quantile(schema: schema,numerator: 50,denominator: 100)?.upperBoundNS,10)
        let p95 = h.quantile(schema: schema,numerator: 95,denominator: 100)
        XCTAssertNotNil(p95); XCTAssertNil(p95?.upperBoundNS); XCTAssertEqual(p95?.observations,4); XCTAssertEqual(p95?.lowSampleWarning,true)
        XCTAssertNil(h.quantile(schema: schema,numerator: 2,denominator: 1))
        XCTAssertThrowsError(try HistogramSchema(finiteBoundsNS: [10,10]))
    }
    func testFullWidthSumWindowsAndImportedExclusion() throws {
        let schema = try HistogramSchema(finiteBoundsNS: [UInt64.max])
        var h = Histogram(); try h.add(UInt64.max,schema: schema); try h.add(UInt64.max,schema: schema)
        XCTAssertEqual(h.sumNS,TraceUInt128(high: 1,low: UInt64.max-1))
        let registry = try MetricRegistry(epoch: UInt64.max,schema: schema)
        try registry.observe(.requests,quantity: UInt64.max-1)
        let start = registry.snapshot(at: LocalInstant(domain: 1,nanos: 1))
        try registry.observe(.requests,quantity: 1)
        let end = registry.snapshot(at: LocalInstant(domain: 1,nanos: 2))
        XCTAssertEqual(try end.window(since: start)[SeriesKey(.requests)],.counter(1))
        XCTAssertThrowsError(try registry.observe(.requests,quantity: 1))
        var labels = MetricLabels(); labels.origin = .replayHarness
        try registry.observe(.requests,quantity: 100,labels: labels)
        XCTAssertEqual(registry.importedIgnored,1)
    }
    func testWindowRetainsNewSeries() throws {
        let registry = try MetricRegistry(epoch: 1,schema: HistogramSchema())
        let start = registry.snapshot(at: LocalInstant(domain: 1,nanos: 0))
        try registry.observe(.requests,quantity: 7)
        let end = registry.snapshot(at: LocalInstant(domain: 1,nanos: 1))
        XCTAssertEqual(try end.window(since: start)[SeriesKey(.requests)],.counter(7))
    }
    func testBoundedEndpointsCatalogAndGaugeScopeCompatibility() throws {
        var limits = MetricLimits(); limits.maxSnapshots = 2
        let registry = try MetricRegistry(epoch: 1,schema: HistogramSchema(),limits: limits)
        for n: UInt64 in 1...3 { try registry.observe(.requests,quantity: 1); _ = try registry.retainSnapshot(at: LocalInstant(domain: 7,nanos: n)) }
        XCTAssertEqual(registry.availableWindows,[4,6]); XCTAssertEqual(registry.health.snapshotsEvicted,1)
        XCTAssertEqual(try registry.window(from: 4,to: 6).series[SeriesKey(.requests)],.counter(1))
        XCTAssertThrowsError(try registry.window(from: 2,to: 6))
        XCTAssertNil(registry.snapshot(at: LocalInstant(domain: 7,nanos: 1)).timestamp)
        XCTAssertEqual(registry.snapshot(at: LocalInstant(domain: 7,nanos: 4)).timestamp?.nanos,4)
        var labels = MetricLabels(); labels.operation = 1
        XCTAssertThrowsError(try registry.observe(.requests,quantity: 1,labels: labels)); XCTAssertEqual(registry.health.invalidLabels,1)
        XCTAssertEqual(metricCatalog().first?.name,"effect_requests_total")
        try registry.observe(.running,quantity: 3)
        let before = registry.snapshot(at: LocalInstant(domain: 7,nanos: 5))
        registry.setGaugeScope(.hostSnapshot)
        let after = registry.snapshot(at: LocalInstant(domain: 7,nanos: 6))
        XCTAssertThrowsError(try after.window(since: before))
    }
}

extension MetricsTests {
    #if DEBUG
    func testNestedSnapshotAtSaturationDoesNotOverwriteCommittedHistory() throws {
        var limits = MetricLimits(); limits.maxSnapshots = 1
        let registry = try MetricRegistry(epoch: 1,schema: HistogramSchema(),limits: limits)
        registry.seedSnapshotSaturationForTesting()
        _ = registry.snapshot(at: nil,completeParticipation: false)
        XCTAssertThrowsError(try registry.trySnapshot(at: nil,completeParticipation: false) { _ in
            _ = registry.snapshot(at: nil,completeParticipation: true)
        })
        let retained = try registry.window(from: UInt64.max,to: UInt64.max)
        XCTAssertTrue(retained.completeParticipation)
    }
    #endif
    func testManualHistogramEmptyOverflowOnlyAndFullWidthCatalog() throws {
        let schema = try HistogramSchema(id: UInt64.max,finiteBoundsNS: [10,20])
        var h = Histogram(); for n: UInt64 in [0,10,11,30] { try h.add(n,schema: schema) }
        XCTAssertEqual(Array(h.buckets.prefix(3)),[2,1,1]); XCTAssertEqual(h.count,4); XCTAssertEqual(h.sumNS,TraceUInt128(high: 0,low: 51))
        XCTAssertEqual(h.quantile(schema: schema,numerator: 50,denominator: 100),Quantile(upperBoundNS: 10,observations: 4,lowSampleWarning: true))
        XCTAssertEqual(h.quantile(schema: schema,numerator: 95,denominator: 100),Quantile(upperBoundNS: nil,observations: 4,lowSampleWarning: true))
        let emptySchema = try HistogramSchema(finiteBoundsNS: [])
        var overflowOnly = Histogram(); try overflowOnly.add(3,schema: emptySchema)
        XCTAssertNil(overflowOnly.quantile(schema: emptySchema,numerator: 50,denominator: 100)?.upperBoundNS)
        XCTAssertThrowsError(try HistogramSchema(id: 0))
        XCTAssertThrowsError(try LabelCatalog(operations: Array(repeating: "x",count: 257)))
        XCTAssertThrowsError(try LabelCatalog(operations: [String(repeating: "x",count: 65)]))
        XCTAssertEqual(metricCatalog().count,32)
    }
    func testTransactionalDisjointMergeProductionAndSumCarry() throws {
        let schema = try HistogramSchema(finiteBoundsNS: [UInt64.max])
        let a = try MetricRegistry(epoch: UInt64.max,schema: schema),b = try MetricRegistry(epoch: UInt64.max,schema: schema)
        for registry in [a,b] { try registry.increment(.requests); try registry.observe(.queueWait,quantity: UInt64.max); try registry.setGauge(.running,value: 1) }
        var merged = a.current(); try merged.mergeDisjoint(b.current())
        XCTAssertEqual(merged.get(.requests),.counter(2)); XCTAssertEqual(merged.get(.running),.gauge(2))
        if case let .histogram(h)? = merged.get(.queueWait) { XCTAssertEqual(h.sumNS,TraceUInt128(high: 1,low: UInt64.max-1)) } else { XCTFail("histogram absent") }
        let over = try MetricRegistry(epoch: 1,schema: schema); try over.increment(.requests,by: UInt64.max)
        var snapshot = over.current(); let prior = snapshot.series
        XCTAssertThrowsError(try snapshot.mergeDisjoint(over.current())); XCTAssertEqual(snapshot.series,prior)
        var labels = MetricLabels(); labels.debuggerAffected = true; try over.increment(.attempts,labels: labels)
        labels.debuggerAffected = false; labels.origin = .testClock; try over.increment(.deliveries,labels: labels)
        XCTAssertEqual(over.current().production().series.count,1)
    }
    func testGaugeUnderflowScopeAndSnapshotTransaction() throws {
        let registry = try MetricRegistry(epoch: 1,schema: HistogramSchema(),gaugeScope: .completeParticipation)
        try registry.setGauge(.running,value: 1)
        XCTAssertThrowsError(try registry.gaugeDelta(.running,delta: -2))
        XCTAssertEqual(registry.current().get(.running),.gauge(1)); XCTAssertEqual(registry.current().gaugeScope,.unknown); XCTAssertEqual(registry.health.arithmeticErrors,1)
        let first = registry.snapshot(at: LocalInstant(domain: 7,nanos: 10)),windows = registry.availableWindows
        XCTAssertThrowsError(try registry.trySnapshot(at: LocalInstant(domain: 7,nanos: 20)) { _ in throw ModelError("transport budget") })
        XCTAssertEqual(registry.revision,first.revision); XCTAssertEqual(registry.availableWindows,windows)
        _ = registry.snapshot(at: nil)
        let bad = registry.snapshot(at: LocalInstant(domain: 7,nanos: 9)); XCTAssertNil(bad.timestamp); XCTAssertEqual(bad.health.invalidMeasurements,1)
        XCTAssertEqual(registry.snapshot(at: LocalInstant(domain: 7,nanos: 30)).timestamp?.nanos,30)
        var tiny = MetricLimits(); tiny.maxSnapshotBytes = 1
        let rejected = try MetricRegistry(epoch: 1,schema: HistogramSchema(),limits: tiny)
        XCTAssertEqual(rejected.snapshot(at: nil).health.snapshotRejections,1); XCTAssertEqual(rejected.availableWindows,[])
    }
    func testImportedInvalidLabelsNeverIngestAndHealthCannotOverflow() throws {
        let registry = try MetricRegistry(epoch: 1,schema: HistogramSchema())
        var labels = MetricLabels(); labels.operation = UInt16.max; labels.origin = .replayHarness
        try registry.increment(.requests,labels: labels)
        XCTAssertEqual(registry.health.importedIgnored,1); XCTAssertEqual(registry.health.invalidLabels,0); XCTAssertTrue(registry.current().series.isEmpty)
        try registry.increment(.requests,by: UInt64.max)
        XCTAssertThrowsError(try registry.increment(.requests)); XCTAssertEqual(registry.current().get(.requests),.counter(UInt64.max)); XCTAssertFalse(registry.current().complete)
    }
}

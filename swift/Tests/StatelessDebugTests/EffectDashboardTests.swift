import XCTest
import Statelessness
@testable import StatelessDebug
final class EffectDashboardTests: XCTestCase {
    func testHostLifecycleExplicitClockAndPassiveReport() throws {
        var tick: UInt64 = 0,clockCalls = 0
        let clock = DiagnosticClock { clockCalls += 1; tick += 10; return LocalInstant(domain: 7,nanos: tick) }
        let observer = try EffectObserver(clock: clock,schema: HistogramSchema(finiteBoundsNS: [10,100]))
        let effect = try observer.requested(origin: RequestOrigin(run: 1,epoch: 1,machine: 9,transitionSequence: 1,outputIndex: 0))
        try observer.admission(effect,accepted: true)
        let attempt = try observer.attemptCreated(effect); try observer.readyQueued(attempt); try observer.attemptStarted(attempt); try observer.attemptFinished(attempt,outcome: .success)
        try observer.resolved(effect,outcome: .success)
        let delivery = try observer.reservePublication(effect); try observer.publicationCommitted(delivery); try observer.deliveryBegun(delivery); try observer.deliveryObserved(delivery,disposition: .ignored); try observer.settled(effect)
        try observer.publicationCommitted(delivery); XCTAssertTrue(try XCTUnwrap(observer.effectDetails(effect.id)).settled)
        let before = clockCalls
        let snapshot = observer.metricSnapshot(at: LocalInstant(domain: 7,nanos: 1000)),dashboard = try Dashboard(snapshot: snapshot,health: observer.health,labels: [MetricLabels()])
        let report = try dashboard.json(); XCTAssertTrue(report.contains("\"value\":null")); XCTAssertEqual(clockCalls,before)
        XCTAssertThrowsError(try dashboard.json(maxBytes: 10))
    }
    func testTimingDisabledReadsNoClockAndForeignTokenRejects() throws {
        var options = EffectOptions(); options.timings = false
        var calls = 0
        let observer = try EffectObserver(options: options,clock: DiagnosticClock { calls += 1; throw MeasurementError.clockUnavailable },schema: HistogramSchema())
        let effect = try observer.requested(); try observer.admission(effect,accepted: true)
        let attempt = try observer.attemptCreated(effect); try observer.attemptStarted(attempt); try observer.attemptFinished(attempt,outcome: .failure)
        XCTAssertEqual(calls,0)
        let other = try EffectObserver(options: options,clock: DiagnosticClock { throw MeasurementError.clockUnavailable },schema: HistogramSchema())
        XCTAssertThrowsError(try other.resolved(effect,outcome: .success))
    }
    func testDetailEvictionPreservesActiveAuthorityAndRetryPauseSamples() throws {
        var options = EffectOptions(); options.maxDetails = 1
        var tick: UInt64 = 0
        let observer = try EffectObserver(options: options,clock: DiagnosticClock { tick += 10; return LocalInstant(domain: 1,nanos: tick) },schema: HistogramSchema())
        let effect = try observer.requested(); try observer.admission(effect,accepted: true)
        _ = try observer.requested()
        XCTAssertNil(observer.effectDetails(effect.id))
        let attempt = try observer.attemptCreated(effect)
        try observer.retryScheduled(attempt,delayNS: UInt64.max)
        try observer.readyQueued(attempt); try observer.debuggerPause(true); try observer.debuggerPause(false)
        try observer.attemptStarted(attempt); try observer.attemptFinished(attempt,outcome: .success)
        try observer.resolved(effect,outcome: .success,selectedAttempt: attempt)
        XCTAssertEqual(observer.pauseIntervals.count,1)
        let values = observer.metricSnapshot(at: LocalInstant(domain: 1,nanos: tick)).series
        XCTAssertNotNil(values[SeriesKey(.resolutionOverhead,labels: { var l = MetricLabels(); l.outcome = .success; l.debuggerAffected = true; return l }())])
        XCTAssertEqual(observer.health.detailEvictions,1)
    }
}

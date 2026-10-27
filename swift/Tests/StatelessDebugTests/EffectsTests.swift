import XCTest
import Statelessness
@testable import StatelessDebug

final class EffectsTests: XCTestCase {
    private func total(_ snapshot: MetricSnapshot,_ family: MetricFamily) -> UInt64 { snapshot.series.reduce(0) { n,pair in guard pair.key.family == family else { return n }; switch pair.value { case let .counter(v),let .gauge(v): return n+v; default: return n } } }
    private func histogram(_ snapshot: MetricSnapshot,_ family: MetricFamily) -> Histogram? { for (key,value) in snapshot.series where key.family == family { if case let .histogram(h) = value { return h } }; return nil }
    // Manually computed before executing real Rust reference: endpoints
    // ready0/start12/finish96/resolve96/post97/begin100/observed102.
    func testIndependentEndpointGoldenAndPassiveSlowSelection() throws {
        var ns: UInt64 = 0,reads = 0
        let observer = try EffectObserver(clock: DiagnosticClock { reads += 1; return LocalInstant(domain: 1,nanos: ns) },schema: HistogramSchema(finiteBoundsNS: [10,100]))
        let effect = try observer.requested(origin: RequestOrigin(run: 1,epoch: 1,machine: UInt64.max,transitionSequence: 9_007_199_254_740_993,outputIndex: 0)); try observer.admission(effect,accepted: true)
        let attempt = try observer.attemptCreated(effect); try observer.readyQueued(attempt)
        ns = 12; try observer.attemptStarted(attempt); ns = 96; try observer.attemptFinished(attempt,outcome: .success); try observer.resolved(effect,outcome: .success,selectedAttempt: attempt)
        ns = 97; let delivery = try observer.reservePublication(effect)
        ns = 100; try observer.deliveryBegun(delivery); try observer.publicationCommitted(delivery); try observer.publicationCommitted(delivery)
        ns = 102; try observer.deliveryObserved(delivery,disposition: .ignored)
        let snapshot = observer.metricSnapshot(at: LocalInstant(domain: 1,nanos: 102))
        for (family,want) in [(MetricFamily.queueWait,UInt64(12)),(.attemptElapsed,84),(.resolution,96),(.completionPreparation,1),(.completionQueueWait,3),(.deliveryLag,4),(.completionProcessing,2),(.endToEnd,100),(.endToEndObserved,102),(.resolutionOverhead,0)] {
            let h = try XCTUnwrap(histogram(snapshot,family)); XCTAssertEqual(h.count,1,"\(family)"); XCTAssertEqual(h.sumNS,TraceUInt128(high: 0,low: want),"\(family)")
        }
        XCTAssertNil(histogram(snapshot,.settlement))
        let before = reads
        let query = SlowTimingQuery(operation: 0,family: .attemptElapsed,thresholdNS: 80),result = try observer.slowTimings(query)
        XCTAssertEqual(result.matches.count,1); XCTAssertEqual(result.matched,1); XCTAssertTrue(result.complete); XCTAssertEqual(reads,before)
        let match = try XCTUnwrap(result.matches.first); XCTAssertEqual(match.timing.attempt,attempt.id); XCTAssertEqual(try match.timing.durationNS.get(),84)
        XCTAssertThrowsError(try observer.deliveryObserved(delivery,disposition: .ignored)); XCTAssertEqual(observer.health.duplicateObservations,1)
        withExtendedLifetime((effect,attempt,delivery)) {}
    }
    func testCancellationAbandonmentLateFinishAndIndependentSettlement() throws {
        var options = EffectOptions(); options.timings = false; var reads = 0
        let observer = try EffectObserver(options: options,clock: DiagnosticClock { reads += 1; throw MeasurementError.clockUnavailable },schema: HistogramSchema())
        let effect = try observer.requested(); try observer.admission(effect,accepted: true); let attempt = try observer.attemptCreated(effect); try observer.attemptStarted(attempt)
        try observer.cancellationRequested(effect); try observer.cancellationAcknowledged(effect); XCTAssertEqual(total(observer.metricSnapshot(at: LocalInstant(domain: 1,nanos: 0)),.running),1)
        try observer.attemptAbandoned(attempt); XCTAssertEqual(total(observer.metricSnapshot(at: LocalInstant(domain: 1,nanos: 0)),.running),1); XCTAssertFalse(observer.metricSnapshot(at: LocalInstant(domain:1,nanos:0)).complete)
        try observer.attemptFinished(attempt,outcome: .cancelled); XCTAssertEqual(total(observer.metricSnapshot(at: LocalInstant(domain: 1,nanos: 0)),.running),0)
        try observer.settled(effect); XCTAssertThrowsError(try observer.settled(effect)); XCTAssertEqual(reads,0)
        let pending = try observer.requested(); XCTAssertThrowsError(try observer.resolved(pending,outcome: .success)); withExtendedLifetime((effect,attempt,pending)) {}
    }
    func testPauseWholeIntervalIsProvenanceNotSubtraction() throws {
        var ns: UInt64 = 0
        let observer = try EffectObserver(clock: DiagnosticClock { LocalInstant(domain: 1,nanos: ns) },schema: HistogramSchema())
        let effect = try observer.requested(); try observer.admission(effect,accepted: true); let attempt = try observer.attemptCreated(effect); try observer.attemptStarted(attempt)
        ns = 2; try observer.debuggerPause(true); ns = 8; try observer.debuggerPause(false); ns = 10; try observer.attemptFinished(attempt,outcome: .success); try observer.resolved(effect,outcome: .success,selectedAttempt: attempt)
        let snapshot = observer.metricSnapshot(at: LocalInstant(domain: 1,nanos: 10))
        var labels = MetricLabels(); labels.outcome = .success; labels.debuggerAffected = true
        guard case let .histogram(h)? = snapshot.series[SeriesKey(.attemptElapsed,labels: labels)] else { return XCTFail("missing pause population") }
        XCTAssertEqual(h.sumNS,TraceUInt128(high: 0,low: 10)); XCTAssertNil(snapshot.production().series[SeriesKey(.attemptElapsed,labels: labels)])
        XCTAssertEqual(observer.pauseIntervals.count,1); XCTAssertNil(histogram(snapshot,.queueWait)); withExtendedLifetime((effect,attempt)) {}
    }
    func testBoundedDetailEvictionPreservesActiveHandlesAndUnknownClock() throws {
        var options = EffectOptions(); options.maxDetails = 1; options.maxDetailBytes = 1024; options.maxEventsPerEffect = 1
        var domain: UInt64 = 1,ns: UInt64 = 0
        let observer = try EffectObserver(options: options,clock: DiagnosticClock { LocalInstant(domain: domain,nanos: ns) },schema: HistogramSchema())
        let first = try observer.requested(); try observer.admission(first,accepted: true); let a = try observer.attemptCreated(first); try observer.readyQueued(a)
        let second = try observer.requested(); XCTAssertNil(observer.effectDetails(first.id)); XCTAssertEqual(observer.health.detailEvictions,1)
        domain = 2; ns = 5; try observer.attemptStarted(a); try observer.attemptFinished(a,outcome: .failure); try observer.resolved(first,outcome: .failure,selectedAttempt: a)
        XCTAssertGreaterThan(observer.health.invalidMeasurements,0); XCTAssertFalse(observer.metricSnapshot(at: LocalInstant(domain: 2,nanos: 5)).complete)
        XCTAssertLessThanOrEqual(observer.health.retainedDetailBytes,UInt64(options.maxDetailBytes)); XCTAssertGreaterThan(observer.health.detailEventDrops,0)
        withExtendedLifetime((first,a,second)) {}
    }
    func testRetryWithoutClockAndResolvedAdmissionBoundary() throws {
        var options = EffectOptions(); options.timings = false
        let observer = try EffectObserver(options: options,clock: DiagnosticClock { throw MeasurementError.clockUnavailable },schema: HistogramSchema())
        let e = try observer.requested(); try observer.admission(e,accepted: true); let a = try observer.attemptCreated(e); try observer.retryScheduled(a,delayNS: UInt64.max)
        XCTAssertThrowsError(try observer.retryScheduled(a,delayNS: UInt64.max)); try observer.resolved(e,outcome: .cancelled)
        XCTAssertThrowsError(try observer.readyQueued(a)); XCTAssertThrowsError(try observer.attemptStarted(a)); withExtendedLifetime((e,a)) {}
    }
    func testFailedPublicationAndForeignHandlesCannotBecomeCompletion() throws {
        var options = EffectOptions(); options.timings = false
        let clock = DiagnosticClock { throw MeasurementError.clockUnavailable },schema = try HistogramSchema()
        let observer = try EffectObserver(options: options,clock: clock,schema: schema),foreign = try EffectObserver(options: options,clock: clock,schema: schema)
        let e = try observer.requested(); try observer.admission(e,accepted: true); try observer.resolved(e,outcome: .success); let d = try observer.reservePublication(e)
        XCTAssertThrowsError(try foreign.publicationCommitted(d)); try observer.publicationFailed(d); XCTAssertThrowsError(try observer.deliveryBegun(d)); XCTAssertThrowsError(try observer.publicationCommitted(d))
        XCTAssertEqual(total(observer.metricSnapshot(at: LocalInstant(domain: 1,nanos: 0)),.completionDepth),0); withExtendedLifetime((e,d)) {}
    }
    func testHostBaselineNormalizesPopulationAndTracksScope() throws {
        var options = EffectOptions(); options.timings = false; options.origin = .testClock
        let observer = try EffectObserver(options: options,clock: DiagnosticClock { throw MeasurementError.clockUnavailable },schema: HistogramSchema())
        var supplied = MetricLabels(); supplied.origin = .importedLive; supplied.outcome = .failure; supplied.debuggerAffected = true
        try observer.hostGaugeSnapshot(labels: supplied,unresolved: UInt64.max,running: 2,ready: 3,completion: 4)
        var expected = MetricLabels(); expected.origin = .testClock
        let snapshot = observer.metricSnapshot(at: LocalInstant(domain: 1,nanos: 0)); XCTAssertEqual(snapshot.series[SeriesKey(.unresolved,labels: expected)],.gauge(UInt64.max)); XCTAssertEqual(snapshot.gaugeScopes[SeriesKey(.unresolved,labels: expected)],.hostSnapshot)
        XCTAssertTrue(snapshot.production().series.isEmpty)
    }
    func testSlowQueryBoundsDisabledDetailsAndPendingAge() throws {
        var ns: UInt64 = 0
        let observer = try EffectObserver(clock: DiagnosticClock { LocalInstant(domain: 1,nanos: ns) },schema: HistogramSchema())
        let e = try observer.requested(); let detail = try XCTUnwrap(observer.effectDetails(e.id)); XCTAssertEqual(try detail.pendingAge(at: LocalInstant(domain: 1,nanos: 5)),5)
        XCTAssertThrowsError(try detail.pendingAge(at: LocalInstant(domain: 2,nanos: 5))); try observer.admission(e,accepted: true); let a = try observer.attemptCreated(e); try observer.readyQueued(a); ns = 7; try observer.attemptStarted(a); ns = 10; try observer.attemptFinished(a,outcome: .success)
        var query = SlowTimingQuery(operation: 0,family: .queueWait,thresholdNS: 1); query.maxResults = 0; let result = try observer.slowTimings(query); XCTAssertEqual(result.matched,1); XCTAssertTrue(result.truncated); XCTAssertFalse(result.complete); XCTAssertTrue(result.matches.isEmpty)
        query.maxResultBytes = 0; XCTAssertThrowsError(try observer.slowTimings(query)); withExtendedLifetime((e,a)) {}
    }
    func testARCAbandonmentNeverInventsAttemptTermination() throws {
        var options = EffectOptions(); options.timings = false
        let observer = try EffectObserver(options: options,clock: DiagnosticClock { throw MeasurementError.clockUnavailable },schema: HistogramSchema())
        let e = try observer.requested(); try observer.admission(e,accepted: true)
        do { let a = try observer.attemptCreated(e); try observer.attemptStarted(a); withExtendedLifetime(a) {} }
        XCTAssertEqual(total(observer.metricSnapshot(at: LocalInstant(domain: 1,nanos: 0)),.running),1); XCTAssertGreaterThan(observer.health.abandoned,0); XCTAssertFalse(observer.metricSnapshot(at: LocalInstant(domain:1,nanos:0)).complete); withExtendedLifetime(e) {}
    }
    func testDroppingUnusedWrapperReportsAbandonmentWhileHostRetainsAttempt() throws {
        var options = EffectOptions(); options.timings = false
        let observer = try EffectObserver(options: options,clock: DiagnosticClock { throw MeasurementError.clockUnavailable },schema: HistogramSchema())
        let effect = try observer.requested(); try observer.admission(effect,accepted: true); let attempt = try observer.attemptCreated(effect)
        var calls = 0
        do { let wrapper = observer.instrumentOperation(attempt,operation: { calls += 1; return 1 },classify: { _ in .success }); withExtendedLifetime(wrapper) {} }
        XCTAssertEqual(calls,0); XCTAssertEqual(observer.health.abandoned,1); XCTAssertFalse(observer.metricSnapshot(at: LocalInstant(domain:1,nanos:0)).complete); XCTAssertThrowsError(try observer.attemptStarted(attempt)); withExtendedLifetime((effect,attempt)) {}
    }
    @MainActor
    func testInstrumentTransfersNonSendableResultsWithoutChangingIdentity() async throws {
        final class HostValue { var count = 1 }
        var options = EffectOptions(); options.timings = false
        let observer = try EffectObserver(options: options,clock: DiagnosticClock { throw MeasurementError.clockUnavailable },schema: HistogramSchema())
        let effect = try observer.requested(); try observer.admission(effect,accepted: true)
        var classified: HostValue?
        let direct = try await observer.instrument(try observer.attemptCreated(effect),operation: { HostValue() },classify: {
            classified = try? $0.get(); classified?.count += 1; return .success
        })
        XCTAssertTrue(direct === classified); XCTAssertEqual(direct.count,2)
        let throwingClassifier = try await observer.instrumentClassifying(try observer.attemptCreated(effect),operation: { HostValue() },classify: {
            classified = try $0.get(); classified?.count += 2; throw MeasurementError.clockUnavailable
        })
        XCTAssertTrue(throwingClassifier === classified); XCTAssertEqual(throwingClassifier.count,3)
        var calls = 0
        let lazy = observer.instrumentOperation(try observer.attemptCreated(effect),operation: {
            MainActor.assertIsolated(); calls += 1
            await Task.yield()
            MainActor.assertIsolated(); return HostValue()
        },classify: {
            MainActor.assertIsolated(); classified = try $0.get(); return .success
        })
        XCTAssertEqual(calls,0)
        let lazyValue = try await lazy.run()
        XCTAssertTrue(lazyValue === classified); XCTAssertEqual(calls,1)
        lazyValue.count = 4; XCTAssertEqual(classified?.count,4)
        XCTAssertEqual(observer.health.callbackFaults,1)
        do { _ = try await lazy.run(); XCTFail("ran wrapper twice") } catch LifecycleError.duplicateObservation {} catch { XCTFail("wrong duplicate error") }
        XCTAssertEqual(calls,1); withExtendedLifetime(effect) {}
    }
    @MainActor
    func testLazyInstrumentPreservesRealResultDespiteForeignHooksAndClassifierFailure() async throws {
        var options = EffectOptions(); options.timings = false
        let clock = DiagnosticClock { throw MeasurementError.clockUnavailable },schema = try HistogramSchema()
        let observer = try EffectObserver(options: options,clock: clock,schema: schema),foreign = try EffectObserver(options: options,clock: clock,schema: schema)
        let e = try observer.requested(); try observer.admission(e,accepted: true); let a = try observer.attemptCreated(e)
        var calls = 0
        let lazy = foreign.instrumentOperation(a,operation: { calls += 1; return 37 },classify: { _ in throw MeasurementError.clockUnavailable })
        XCTAssertEqual(calls,0)
        let value = try await lazy.run(); XCTAssertEqual(value,37); XCTAssertEqual(calls,1); XCTAssertEqual(foreign.health.callbackFaults,1)
        do { _ = try await lazy.run(); XCTFail("ran wrapper twice") } catch { XCTAssertEqual(calls,1) }
        enum HostFailure: Error { case original }
        let failing: ObservedEffectOperation<Int> = foreign.instrumentOperation(a,operation: {
            MainActor.assertIsolated(); calls += 1; await Task.yield(); throw HostFailure.original
        },classify: { _ in MainActor.assertIsolated(); throw MeasurementError.clockUnavailable })
        do { _ = try await failing.run(); XCTFail("lost lazy host error") } catch HostFailure.original {} catch { XCTFail("replaced lazy host error: \(error)") }
        XCTAssertEqual(calls,2)
        do { let _: Int = try await foreign.instrument(a,operation: { calls += 1; throw HostFailure.original },classify: { _ in .failure }); XCTFail("lost host error") } catch HostFailure.original {} catch { XCTFail("replaced host error: \(error)") }
        XCTAssertEqual(calls,3); withExtendedLifetime((e,a)) {}
    }
}

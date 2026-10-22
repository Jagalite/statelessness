import Dispatch
import Foundation
import XCTest
@testable import Statelessness

// Only the test ledger crosses workers. Application state, outputs and feature
// keys below deliberately do not conform to Sendable.
private final class CampaignTestBox<Value: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: Value
    init(_ value: Value) { self.value = value }
    func access<T>(_ body: (inout Value) -> T) -> T {
        lock.lock(); defer { lock.unlock() }
        return body(&value)
    }
    func read() -> Value { access { $0 } }
}

private final class CampaignTestGate: @unchecked Sendable {
    private let semaphore = DispatchSemaphore(value: 0)
    func open() { semaphore.signal() }
    func wait(_ label: String) throws {
        guard semaphore.wait(timeout: .now() + .seconds(10)) == .success else {
            throw ModelError("test handshake timed out: \(label)")
        }
    }
}

private final class CampaignTestBarrier: Sendable {
    private let count = CampaignTestBox(0)
    private let gate = CampaignTestGate()
    private let parties: Int
    init(_ parties: Int) { self.parties = parties }
    func arrive() throws {
        let last = count.access { $0 += 1; return $0 == parties }
        if last { for _ in 0..<parties { gate.open() } }
        try gate.wait("all \(parties) workers entered")
    }
}

private final class CampaignLocalValue {
    let owner: UInt64
    var value: Int
    init(_ owner: UInt64, _ value: Int) { self.owner = owner; self.value = value }
    func copy() -> CampaignLocalValue { CampaignLocalValue(owner, value) }
}

private final class CampaignLocalFeature: Hashable {
    var value: Int
    init(_ value: Int) { self.value = value }
    static func == (a: CampaignLocalFeature, b: CampaignLocalFeature) -> Bool { a.value == b.value }
    func hash(into hasher: inout Hasher) { hasher.combine(0) }
}

private final class CampaignLocalContext {
    let thread = ObjectIdentifier(Thread.current)
    var outputs: [CampaignLocalValue] = []
    var observations = 0
}

private func campaignTestJob(_ id: UInt64) -> CampaignJob<Int, Int, Int> {
    CampaignJob(Generate(model: Model(initialState: { 0 },
        step: { s, i in Transition(state: s + i, outputs: [s + i]) },
        checkState: { _ in [.skipped("state", "fixture")] },
        cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }),
        generate: { _, _ in 1 }, equalInputs: ==),
        metadata: { Metadata(name: "job:\(id)", build: "campaign-runtime-test-v1") })
}

private func campaignTestFeedback() -> Feedback<Int, Int, Int, Int> {
    StateFeedback(metadata: { FeedbackMetadata(name: "counter", build: "v1") },
                  cloneFeature: { $0 }, project: { $0 }).feedback()
}

private func campaignTestReport(_ config: FuzzConfig, termination: String = "cases_completed",
                                transitions: UInt64 = 1, skipped: UInt64 = 2) -> FuzzReport<Int> {
    FuzzReport(termination: termination, seed: config.seed, cases: 1,
               transitions: transitions, skippedChecks: skipped, failure: nil)
}

private func campaignTestCompleted(_ job: JobReport<Int>, file: StaticString = #filePath,
                                  line: UInt = #line) throws -> FuzzReport<Int> {
    guard case let .completed(report) = job.outcome else {
        XCTFail("expected completed job \(job.jobID), got \(job.outcome)", file: file, line: line)
        throw ModelError("unexpected job outcome")
    }
    return report
}

private func campaignTestAccounting(_ report: CampaignReport, file: StaticString = #filePath,
                                    line: UInt = #line) {
    XCTAssertEqual(report.requested, report.started + report.notStarted, file: file, line: line)
    XCTAssertLessThanOrEqual(report.delivered, report.finished, file: file, line: line)
    XCTAssertLessThanOrEqual(report.finished, report.started, file: file, line: line)
}

private struct CampaignTestFingerprint: Equatable {
    var seed: UInt64
    var metadata: Metadata?
    var termination: String
    var cases: Int
    var transitions: UInt64
    var skipped: UInt64
    var corpus: [CorpusEntry<Int>]
    var guidanceConfig: GuidanceConfig?
    var feedback: FeedbackMetadata?
    var uniqueFeatures: Int?
    var corpusInputs: Int?
    var mutationCases: Int?
    var evictions: UInt64?
    var drops: UInt64?
    var featureLimitReached: Bool?
    var corpusLimitReached: Bool?
}

final class CampaignRuntimeTests: XCTestCase {
    func testDefaultsSeedsAndWrappingJobRanges() throws {
        let defaults = CampaignConfig()
        XCTAssertEqual(defaults.masterSeed, 0); XCTAssertEqual(defaults.firstJob, 0)
        XCTAssertEqual(defaults.jobs, 16); XCTAssertEqual(defaults.workers, 1)
        XCTAssertEqual(defaults.resultBuffer, 1); XCTAssertFalse(defaults.stopOnFailure)
        // Published SplitMix64 words, not an expectation computed by jobSeed.
        XCTAssertEqual(jobSeed(masterSeed: 0, jobID: 0), 16_294_208_416_658_607_535)
        XCTAssertEqual(jobSeed(masterSeed: 0, jobID: 1), 10_451_216_379_200_822_465)
        XCTAssertEqual(jobSeed(masterSeed: 0, jobID: 7), 7_191_089_600_892_374_487)
        XCTAssertEqual(jobSeed(masterSeed: .max, jobID: 1), 16_294_208_416_658_607_535)
        XCTAssertEqual(jobSeed(masterSeed: 1, jobID: .max), 16_294_208_416_658_607_535)
        var rows: [JobReport<Int>] = []
        let report = try runCampaign(config: CampaignConfig(masterSeed: 1, firstJob: .max, jobs: 1,
            fuzz: FuzzConfig(seed: 123, cases: 0)), factory: campaignTestJob, sink: { rows.append($0) })
        let job = try XCTUnwrap(rows.first)
        XCTAssertEqual(job.jobID, .max); XCTAssertEqual(job.seed, 16_294_208_416_658_607_535)
        XCTAssertEqual(job.config.seed, job.seed); XCTAssertEqual(try campaignTestCompleted(job).seed, job.seed)
        XCTAssertEqual(report.started, 1); XCTAssertEqual(report.reportedCases, 0)
        XCTAssertEqual(report.termination, .jobsCompleted)
    }

    func testValidationRejectsBeforeControlsFactoriesWorkersAndSink() throws {
        let cases: [(CampaignConfig, String)] = [
            (CampaignConfig(jobs: 0), "campaign jobs must be positive"),
            (CampaignConfig(jobs: -1), "campaign jobs must be positive"),
            (CampaignConfig(workers: 0), "campaign workers must be in 1..=256"),
            (CampaignConfig(workers: 257), "campaign workers must be in 1..=256"),
            (CampaignConfig(resultBuffer: -1), "campaign result_buffer must be in 0..=1024"),
            (CampaignConfig(resultBuffer: 1025), "campaign result_buffer must be in 0..=1024"),
            (CampaignConfig(fuzz: FuzzConfig(mutationPercent: 101)), "mutation_percent must be in 0..=100"),
            (CampaignConfig(fuzz: FuzzConfig(mutationPercent: -1)), "mutation_percent must be in 0..=100"),
            (CampaignConfig(firstJob: .max, jobs: 2), "campaign job ID range overflows u64"),
            (CampaignConfig(fuzz: FuzzConfig(cases: -1)), "campaign per-job cases must be nonnegative"),
            (CampaignConfig(fuzz: FuzzConfig(maxSteps: -1)), "campaign per-job steps must be nonnegative"),
            (CampaignConfig(jobs: 3, fuzz: FuzzConfig(cases: Int.max, maxSteps: 0)), "campaign aggregate cases overflow u64"),
            (CampaignConfig(jobs: 3, fuzz: FuzzConfig(cases: 1, maxSteps: Int.max, maxTransitions: .max)), "campaign aggregate transitions overflow u64")
        ]
        for (config, expected) in cases {
            let calls = CampaignTestBox<[String]>([])
            let hooks = CampaignWorkerHooks(beforeStart: { _ in calls.access { $0.append("start") } })
            XCTAssertThrowsError(try runCampaignUsing(config: config,
                limits: CampaignLimits(isCancelled: { calls.access { $0.append("control") }; return true }),
                factory: { id in calls.access { $0.append("factory") }; return campaignTestJob(id) },
                sink: { _ in calls.access { $0.append("sink") } }, hooks: hooks,
                runner: { _, _, c, _ in campaignTestReport(c) })) {
                XCTAssertEqual(String(describing: $0), expected)
            }
            XCTAssertEqual(calls.read(), [], expected)
        }
        // The wide per-job product exceeds u64, but its cap and aggregate fit.
        XCTAssertNoThrow(try CampaignConfig(jobs: 2,
            fuzz: FuzzConfig(cases: Int.max, maxSteps: Int.max, maxTransitions: 3)).validate())
        XCTAssertNoThrow(try CampaignConfig(firstJob: .max, jobs: 1, workers: 256,
            resultBuffer: 1024, fuzz: FuzzConfig(cases: 0, maxSteps: 0, maxTransitions: 0)).validate())
    }

    func testGuidanceAdmissionPrecedesCampaignAndAllCallbacks() throws {
        for guidance in [GuidanceConfig(maxCorpusEntries: 0), GuidanceConfig(maxCorpusInputs: 0),
                         GuidanceConfig(maxFeatures: 0), GuidanceConfig(maxFeaturesPerObservation: 0)] {
            let calls = CampaignTestBox<[String]>([])
            XCTAssertThrowsError(try runGuidedCampaign(config: CampaignConfig(jobs: 0),
                limits: CampaignLimits(isCancelled: { calls.access { $0.append("control") }; return true }),
                guidance: guidance,
                factory: { id in calls.access { $0.append("factory") }; return campaignTestJob(id) },
                feedbackFactory: { _, _ in calls.access { $0.append("feedback") }; return campaignTestFeedback() },
                sink: { _ in calls.access { $0.append("sink") } })) {
                XCTAssertEqual(String(describing: $0), "guidance limits must be positive")
            }
            XCTAssertEqual(calls.read(), [])
        }
    }

    func testPreflightControlsAndInvalidDurationsConstructNothing() throws {
        for workers in [1, 2] {
            for (duration, cancelled, termination) in [(nil as TimeInterval?, true, CampaignTermination.cancelled),
                (0, false, .deadline), (0, true, .cancelled)] {
                let calls = CampaignTestBox<[String]>([])
                let report = try runGuidedCampaign(config: CampaignConfig(jobs: 3, workers: workers),
                    limits: CampaignLimits(maxDuration: duration, isCancelled: { cancelled }),
                    factory: { id in calls.access { $0.append("factory") }; return campaignTestJob(id) },
                    feedbackFactory: { _, _ in calls.access { $0.append("feedback") }; return campaignTestFeedback() },
                    sink: { _ in calls.access { $0.append("sink") } })
                XCTAssertEqual(calls.read(), []); XCTAssertEqual(report.termination, termination)
                XCTAssertEqual(report.started, 0); XCTAssertEqual(report.notStarted, 3)
                XCTAssertEqual(report.finished, 0); XCTAssertEqual(report.delivered, 0)
                XCTAssertTrue(report.accountingComplete); campaignTestAccounting(report)
            }
        }
        for duration in [TimeInterval.nan, .infinity, -1] {
            let calls = CampaignTestBox(0)
            XCTAssertThrowsError(try runCampaign(config: CampaignConfig(jobs: 1),
                limits: CampaignLimits(maxDuration: duration),
                factory: { id in calls.access { $0 += 1 }; return campaignTestJob(id) },
                sink: { _ in calls.access { $0 += 1 } })) {
                XCTAssertEqual(String(describing: $0), "invalid run duration")
            }
            XCTAssertEqual(calls.read(), 0)
        }
    }

    func testConfiguredWorkersSelectCallerOrSpawnedPathEvenForOneJob() throws {
        let caller = ObjectIdentifier(Thread.current)
        for workers in [1, 2, 8] {
            let callbacks = CampaignTestBox<[ObjectIdentifier]>([])
            var sinks = 0
            let report = try runCampaign(config: CampaignConfig(jobs: 1, workers: workers,
                resultBuffer: 0, fuzz: FuzzConfig(cases: 1, maxSteps: 1)), factory: { id in
                callbacks.access { $0.append(ObjectIdentifier(Thread.current)) }
                let job = campaignTestJob(id)
                return CampaignJob(job.generated, metadata: {
                    callbacks.access { $0.append(ObjectIdentifier(Thread.current)) }; return try job.metadata()
                }, observer: SearchObserver(initial: { _, _ in
                    callbacks.access { $0.append(ObjectIdentifier(Thread.current)) }
                }), tearDown: { callbacks.access { $0.append(ObjectIdentifier(Thread.current)) } })
            }, sink: { _ in sinks += 1; XCTAssertEqual(ObjectIdentifier(Thread.current), caller) })
            let threads = callbacks.read()
            XCTAssertEqual(threads.count, 4); XCTAssertEqual(Set(threads).count, 1)
            if workers == 1 { XCTAssertEqual(threads.first, caller) }
            else { XCTAssertNotEqual(threads.first, caller) }
            XCTAssertEqual(sinks, 1); XCTAssertEqual(report.delivered, 1)
        }
    }

    func testOrdinaryAndGuidedJobsReproduceAcrossWorkersAndJobSlices() throws {
        for guided in [false, true] {
            func collect(workers: Int, first: UInt64, jobs: Int) throws
                -> ([UInt64: CampaignTestFingerprint], [UInt64: [String]], CampaignReport) {
                let events = CampaignTestBox<[UInt64: [String]]>([:])
                let factory: @Sendable (UInt64) throws -> CampaignJob<Int, Int, Int> = { id in
                    var g = campaignTestJob(id).generated
                    g.generate = { s, rng in
                        let input = Int(rng.index(5) ?? 0) + 1
                        events.access { $0[id, default: []].append("generate:\(s):\(input)") }
                        return input
                    }
                    let observer = SearchObserver<Int, Int, Int>(initial: { _, _ in
                        events.access { $0[id, default: []].append("initial") }
                    }, transition: { s, i, t, _, _, prefix in
                        events.access { $0[id, default: []].append("step:\(s):\(i):\(t.state):\(prefix)") }
                    })
                    return CampaignJob(g, metadata: { Metadata(name: "job:\(id)", build: "v1") }, observer: observer)
                }
                let config = CampaignConfig(masterSeed: .max - 10, firstJob: first, jobs: jobs,
                    workers: workers, resultBuffer: 0,
                    fuzz: FuzzConfig(seed: 999, cases: 5, maxSteps: 4, maxTransitions: 20, mutationPercent: 100))
                var values: [UInt64: CampaignTestFingerprint] = [:]
                let sink: (JobReport<Int>) throws -> Void = { job in
                    XCTAssertNil(values[job.jobID], "duplicate delivery")
                    let r = try campaignTestCompleted(job)
                    XCTAssertEqual(r.termination, "cases_completed"); XCTAssertNil(r.failure)
                    XCTAssertEqual(job.config.seed, job.seed); XCTAssertEqual(r.seed, job.seed)
                    XCTAssertEqual(job.config.cases, 5); XCTAssertEqual(job.config.maxSteps, 4)
                    XCTAssertEqual(job.config.maxTransitions, 20); XCTAssertEqual(job.config.mutationPercent, 100)
                    values[job.jobID] = CampaignTestFingerprint(seed: job.seed, metadata: job.metadata,
                        termination: r.termination, cases: r.cases, transitions: r.transitions,
                        skipped: r.skippedChecks, corpus: r.guidance?.corpus ?? [],
                        guidanceConfig: r.guidance?.config, feedback: r.guidance?.feedback,
                        uniqueFeatures: r.guidance?.uniqueFeatures, corpusInputs: r.guidance?.corpusInputs,
                        mutationCases: r.guidance?.mutationCases, evictions: r.guidance?.evictedPrefixes,
                        drops: r.guidance?.droppedPrefixes, featureLimitReached: r.guidance?.featureLimitReached,
                        corpusLimitReached: r.guidance?.corpusLimitReached)
                }
                let result: CampaignReport
                if guided {
                    result = try runGuidedCampaign(config: config, factory: factory,
                        feedbackFactory: { _, _ in campaignTestFeedback() }, sink: sink)
                } else { result = try runCampaign(config: config, factory: factory, sink: sink) }
                XCTAssertEqual(values.count, jobs); XCTAssertEqual(result.termination, .jobsCompleted)
                XCTAssertEqual(result.reportedCases, UInt64(jobs * 5))
                XCTAssertEqual(result.reportedTransitions, UInt64(jobs * 20))
                XCTAssertEqual(result.reportedSkippedChecks, UInt64(jobs * 25))
                XCTAssertTrue(result.accountingComplete); campaignTestAccounting(result)
                return (values, events.read(), result)
            }
            let serial = try collect(workers: 1, first: 7, jobs: 6)
            let parallel = try collect(workers: 3, first: 7, jobs: 6)
            XCTAssertEqual(serial.0, parallel.0); XCTAssertEqual(serial.1, parallel.1)
            XCTAssertEqual(serial.2, parallel.2)
            let slice = try collect(workers: 2, first: 9, jobs: 2)
            for id in UInt64(9)...10 {
                XCTAssertEqual(slice.0[id], serial.0[id]); XCTAssertEqual(slice.1[id], serial.1[id])
            }
        }
    }

    func testGuidedCallbackOrderAndFactoryCancellationPreserveMetadataBoundaries() throws {
        for cancelInFactory in [false, true] {
            let events = CampaignTestBox<[String]>([]), cancelled = CampaignTestBox(false)
            var rows: [JobReport<Int>] = []
            let r = try runGuidedCampaign(config: CampaignConfig(jobs: 1, workers: 2,
                fuzz: FuzzConfig(cases: 1, maxSteps: 1)), limits: CampaignLimits(isCancelled: { cancelled.read() }),
                factory: { id in
                    events.access { $0.append("factory") }
                    if cancelInFactory { cancelled.access { $0 = true } }
                    var g = campaignTestJob(id).generated
                    g.model.initialState = { events.access { $0.append("initial") }; return 0 }
                    g.model.step = { s, i in events.access { $0.append("step") }; return Transition(state: s + i) }
                    g.model.checkState = { _ in events.access { $0.append("state-check") }; return [] }
                    g.model.checkTransition = { _, _, _ in events.access { $0.append("edge-check") }; return [] }
                    g.generate = { _, _ in events.access { $0.append("generate") }; return 1 }
                    g.isEnabled = { _, _ in events.access { $0.append("enabled") }; return true }
                    return CampaignJob(g, metadata: { events.access { $0.append("metadata") }; return Metadata(name: "order") },
                        observer: SearchObserver(initial: { _, _ in events.access { $0.append("observe-initial") } },
                            transition: { _, _, _, _, _, _ in events.access { $0.append("observe-edge") } }),
                        tearDown: { events.access { $0.append("tear-down") } })
                }, feedbackFactory: { _, _ -> Feedback<Int, Int, Int, Int> in
                    events.access { $0.append("feedback-factory") }
                    return Feedback(metadata: { events.access { $0.append("feedback-metadata") }; return FeedbackMetadata(name: "order", build: "v1") },
                        cloneFeature: { $0 }, initialFeatures: { _, out in events.access { $0.append("initial-feature") }; try out.push(0) },
                        transitionFeatures: { _, _, t, out in events.access { $0.append("edge-feature") }; try out.push(t.state) })
                }, sink: { rows.append($0); events.access { $0.append("sink") } })
            let prefix = ["factory", "metadata", "feedback-factory", "feedback-metadata"]
            let work = ["initial", "state-check", "observe-initial", "initial-feature", "generate", "enabled", "step", "state-check", "edge-check", "observe-edge", "edge-feature"]
            XCTAssertEqual(events.read(), prefix + (cancelInFactory ? [] : work) + ["tear-down", "sink"])
            let fuzz = try campaignTestCompleted(XCTUnwrap(rows.first))
            XCTAssertEqual(fuzz.cases, cancelInFactory ? 0 : 1)
            XCTAssertEqual(fuzz.guidance?.uniqueFeatures, cancelInFactory ? 0 : 2)
            XCTAssertEqual(r.termination, cancelInFactory ? .cancelled : .jobsCompleted)
        }
    }

    func testJobErrorsKeepTheirMetadataAccountingAndDoNotStopOnFailure() throws {
        let tornDown = CampaignTestBox<[UInt64]>([])
        var rows: [JobReport<Int>] = []
        let r = try runCampaignUsing(config: CampaignConfig(jobs: 7, workers: 2, stopOnFailure: true),
            limits: CampaignLimits(), factory: { id in
                if id == 0 { throw ModelError("factory error") }
                if id == 1 { throw CampaignPanic("factory panic") }
                let job = campaignTestJob(id)
                return CampaignJob(job.generated, metadata: {
                    if id == 2 { throw ModelError("metadata error") }
                    return try job.metadata()
                }, tearDown: {
                    tornDown.access { $0.append(id) }
                    if id == 5 { throw ModelError("cleanup error") }
                })
            }, sink: { rows.append($0) }, runner: { id, _, c, _ in
                if id == 3 { throw ModelError("runner error") }
                if id == 4 { throw CampaignPanic("runner panic") }
                return campaignTestReport(c)
            })
        var byID: [UInt64: JobReport<Int>] = [:]
        for row in rows {
            XCTAssertNil(byID[row.jobID], "duplicate delivery")
            byID[row.jobID] = row
        }
        XCTAssertEqual(rows.count, 7); XCTAssertEqual(Set(rows.map(\.jobID)).count, 7)
        for id in UInt64(0)...6 {
            let job = try XCTUnwrap(byID[id])
            if id <= 2 { XCTAssertNil(job.metadata) } else { XCTAssertEqual(job.metadata?.name, "job:\(id)") }
            switch (id, job.outcome) {
            case (0, .factoryError(let e)): XCTAssertEqual(e.description, "factory error")
            case (1, .panicked(let e)): XCTAssertEqual(e, "factory panic")
            case (2, .panicked(let e)): XCTAssertEqual(e, "metadata error")
            case (3, .modelError(let e)): XCTAssertEqual(e.description, "runner error")
            case (4, .panicked(let e)): XCTAssertEqual(e, "runner panic")
            case (5, .panicked(let e)): XCTAssertEqual(e, "cleanup error")
            case (6, .completed): break
            default: XCTFail("wrong outcome for \(id): \(job.outcome)")
            }
        }
        XCTAssertEqual(tornDown.read().sorted(), [2, 3, 4, 5, 6])
        XCTAssertEqual(r.started, 7); XCTAssertEqual(r.finished, 7); XCTAssertEqual(r.delivered, 7)
        XCTAssertEqual(r.errors, 6); XCTAssertEqual(r.failures, 0); XCTAssertFalse(r.accountingComplete)
        XCTAssertEqual(r.reportedTransitions, 1); XCTAssertEqual(r.reportedCases, 1)
        XCTAssertEqual(r.reportedSkippedChecks, 2); XCTAssertEqual(r.termination, .jobsCompleted)
    }

    func testFactoryErrorHasKnownZeroWorkAndFeedbackErrorHasLostAccounting() throws {
        var ordinary: [JobReport<Int>] = []
        let a = try runCampaign(config: CampaignConfig(jobs: 2, stopOnFailure: true),
            factory: { _ -> CampaignJob<Int, Int, Int> in throw ModelError("cannot construct") }, sink: { ordinary.append($0) })
        XCTAssertEqual(a.errors, 2); XCTAssertTrue(a.accountingComplete)
        XCTAssertEqual(a.reportedCases, 0); XCTAssertEqual(a.termination, .jobsCompleted)
        XCTAssertTrue(ordinary.allSatisfy { $0.metadata == nil })
        var guided: [JobReport<Int>] = []
        let b = try runGuidedCampaign(config: CampaignConfig(jobs: 2, stopOnFailure: true), factory: campaignTestJob,
            feedbackFactory: { _, _ -> Feedback<Int, Int, Int, Int> in throw ModelError("feedback construction") },
            sink: { guided.append($0) })
        XCTAssertEqual(b.errors, 2); XCTAssertFalse(b.accountingComplete)
        XCTAssertEqual(b.reportedCases, 0); XCTAssertEqual(b.termination, .jobsCompleted)
        for job in guided {
            XCTAssertNotNil(job.metadata)
            guard case let .modelError(e) = job.outcome else { XCTFail("feedback factory must be ModelError"); continue }
            XCTAssertEqual(e.description, "feedback construction")
        }
    }

    func testN05WrappedPanicMarkerIsModelErrorAndCleanupReplacesIt() throws {
        for cleanupThrows in [false, true] {
            var rows: [JobReport<Int>] = []
            let r = try runCampaign(config: CampaignConfig(jobs: 1), factory: { id in
                var g = campaignTestJob(id).generated
                g.generate = { _, _ in throw CampaignPanic("wrapped generation") }
                return CampaignJob(g, metadata: { Metadata(name: "retained") }, tearDown: {
                    if cleanupThrows { throw ModelError("cleanup replaces runner") }
                })
            }, sink: { rows.append($0) })
            let row = try XCTUnwrap(rows.first)
            XCTAssertEqual(row.metadata?.name, "retained")
            switch row.outcome {
            case let .modelError(e) where !cleanupThrows: XCTAssertEqual(e.description, "generate: wrapped generation")
            case let .panicked(e) where cleanupThrows: XCTAssertEqual(e, "cleanup replaces runner")
            default: XCTFail("wrong callback containment outcome")
            }
            XCTAssertEqual(r.errors, 1); XCTAssertFalse(r.accountingComplete)
            XCTAssertEqual(r.reportedCases, 0, "hidden partial work is not guessed")
        }
    }

    func testFeedbackMetadataPanicMarkerRetainsNativeN05ModelErrorPolicy() throws {
        let events = CampaignTestBox<[String]>([])
        var rows: [JobReport<Int>] = []
        let r = try runGuidedCampaign(config: CampaignConfig(jobs: 1, workers: 2), factory: { id in
            let job = campaignTestJob(id)
            return CampaignJob(job.generated, metadata: job.metadata,
                tearDown: { events.access { $0.append("tear-down") } })
        }, feedbackFactory: { _, _ -> Feedback<Int, Int, Int, Int> in
            var feedback = campaignTestFeedback()
            feedback.metadata = {
                events.access { $0.append("feedback-metadata") }
                throw CampaignPanic("feedback metadata panic")
            }
            return feedback
        }, sink: { rows.append($0); events.access { $0.append("sink") } })
        let row = try XCTUnwrap(rows.first)
        XCTAssertNotNil(row.metadata)
        guard case let .modelError(error) = row.outcome else {
            return XCTFail("N05 normalizes this marker; do not fabricate Rust Panicked parity")
        }
        XCTAssertEqual(error.description, "feedback metadata: feedback metadata panic")
        XCTAssertEqual(events.read(), ["feedback-metadata", "tear-down", "sink"])
        XCTAssertEqual(r.errors, 1); XCTAssertFalse(r.accountingComplete)
        XCTAssertEqual(r.reportedCases, 0); XCTAssertEqual(r.reportedTransitions, 0)
        XCTAssertEqual(r.termination, .jobsCompleted)
    }

    func testSimultaneousWorkersKeepNonSendableStateOutputsFeaturesAndObserversLocal() throws {
        let barrier = CampaignTestBarrier(2)
        let identities = CampaignTestBox<[ObjectIdentifier]>([])
        let threads = CampaignTestBox<[ObjectIdentifier]>([])
        let violations = CampaignTestBox<[String]>([])
        let teardown = CampaignTestBox<[UInt64: Int]>([:])
        var rows: [JobReport<Int>] = []
        let report = try runGuidedCampaign(config: CampaignConfig(jobs: 2, workers: 2, resultBuffer: 0,
            fuzz: FuzzConfig(cases: 1, maxSteps: 3)), factory: { id in
            let local = CampaignLocalContext()
            identities.access { $0.append(ObjectIdentifier(local)) }
            threads.access { $0.append(local.thread) }
            // Both contexts must exist concurrently. A serial substitute times
            // out and produces a failing job instead of passing this witness.
            try barrier.arrive()
            func verifyThread() {
                if ObjectIdentifier(Thread.current) != local.thread {
                    violations.access { $0.append("job \(id) moved threads") }
                }
            }
            let model = Model<CampaignLocalValue, Int, CampaignLocalValue>(
                initialState: { verifyThread(); return CampaignLocalValue(id, 0) },
                step: { s, i in
                    verifyThread()
                    let output = CampaignLocalValue(id, s.value + i)
                    local.outputs.append(output)
                    return Transition(state: CampaignLocalValue(id, s.value + i), outputs: [output])
                }, checkState: { s in
                    verifyThread()
                    return s.owner == id && (0...3).contains(s.value) ? [] : [.failed("isolation", "foreign or mutated state")]
                }, cloneState: { $0.copy() }, cloneInput: { $0 }, cloneOutput: { $0.copy() },
                checkTransition: { _, _, t in
                    verifyThread()
                    return t.outputs.allSatisfy { $0.owner == id && $0.value == t.state.value }
                        ? [] : [.failed("output", "foreign or mutated output")]
                })
            let observer = SearchObserver<CampaignLocalValue, Int, CampaignLocalValue>(
                initial: { s, _ in verifyThread(); s.value = -999 },
                transition: { before, _, t, _, _, _ in
                    verifyThread(); local.observations += 1
                    before.value = -999; t.state.value = -999
                    for output in t.outputs { output.value = -999 }
                })
            return CampaignJob(Generate(model: model, generate: { _, _ in verifyThread(); return 1 }, equalInputs: ==),
                metadata: { verifyThread(); return Metadata(name: "local:\(id)") }, observer: observer,
                tearDown: {
                    verifyThread()
                    if local.outputs.map(\.value) != [1, 2, 3] { violations.access { $0.append("observer aliased original outputs") } }
                    teardown.access { $0[id] = local.observations }
                })
        }, feedbackFactory: { id, _ -> Feedback<CampaignLocalValue, Int, CampaignLocalValue, CampaignLocalFeature> in
            let local = CampaignLocalContext(), key = CampaignLocalFeature(0)
            func emit(_ value: Int, _ out: FeatureBuffer<CampaignLocalFeature>) throws {
                if ObjectIdentifier(Thread.current) != local.thread { violations.access { $0.append("feedback \(id) moved threads") } }
                key.value = value; try out.push(key); key.value = -999
            }
            return Feedback(metadata: { FeedbackMetadata(name: "local-feedback:\(id)", build: "v1") },
                cloneFeature: { CampaignLocalFeature($0.value) },
                initialFeatures: { s, out in try emit(s.value, out) },
                transitionFeatures: { _, _, t, out in try emit(t.state.value, out) })
        }, sink: { rows.append($0) })
        XCTAssertEqual(Set(identities.read()).count, 2)
        XCTAssertEqual(Set(threads.read()).count, 2)
        XCTAssertEqual(violations.read(), []); XCTAssertEqual(teardown.read(), [0: 3, 1: 3])
        XCTAssertEqual(rows.count, 2)
        for job in rows {
            let r = try campaignTestCompleted(job), h = try XCTUnwrap(campaignTestCompleted(job).guidance)
            XCTAssertEqual(r.termination, "cases_completed"); XCTAssertNil(r.failure)
            XCTAssertEqual(h.feedback.name, "local-feedback:\(job.jobID)")
            XCTAssertEqual(h.uniqueFeatures, 4)
            XCTAssertEqual(h.corpus.map(\.inputs), [[1], [1, 1], [1, 1, 1]])
            XCTAssertEqual(h.corpus.map(\.newFeatures), [1, 1, 1])
        }
        XCTAssertEqual(report.delivered, 2); XCTAssertEqual(report.reportedTransitions, 6)
        XCTAssertTrue(report.accountingComplete)
    }

    func testReceiptOrderCanDifferFromCompletionAndJobIDOrder() throws {
        let zeroCompleted = CampaignTestGate(), oneDelivered = CampaignTestGate()
        let completions = CampaignTestBox<[UInt64]>([]), errors = CampaignTestBox<[String]>([])
        let hooks = CampaignWorkerHooks(afterClaim: { id in
            if id == 1 { try zeroCompleted.wait("job zero completed before job one runs") }
        }, afterComplete: { id in
            completions.access { $0.append(id) }
            if id == 0 {
                zeroCompleted.open()
                do { try oneDelivered.wait("job one reaches sink before job zero sends") }
                catch { errors.access { $0.append(String(describing: error)) } }
            }
        })
        var receipts: [UInt64] = []
        let r = try runCampaignUsing(config: CampaignConfig(jobs: 2, workers: 2, resultBuffer: 0),
            limits: CampaignLimits(), factory: campaignTestJob, sink: { job in
                receipts.append(job.jobID)
                if job.jobID == 1 { oneDelivered.open() }
            }, hooks: hooks, runner: { _, _, c, _ in campaignTestReport(c) })
        XCTAssertEqual(errors.read(), []); XCTAssertEqual(completions.read(), [0, 1])
        XCTAssertEqual(receipts, [1, 0], "receipt order must not be sorted or coupled to completion order")
        XCTAssertEqual(r.started, 2); XCTAssertEqual(r.finished, 2); XCTAssertEqual(r.delivered, 2)
    }

    func testSlowSinkObservedAllowanceAndClaimAfterReceiptBeforeSinkReturn() throws {
        for buffer in [0, 1] {
            struct Ledger: Sendable {
                var claimed: [UInt64] = []
                var completed: [UInt64] = []
                var successfulSinkBodies = 0
                var maximumOutstanding = 0
                var violations: [String] = []
            }
            let ledger = CampaignTestBox(Ledger()), full = CampaignTestGate()
            let bound = buffer + 2 + 1
            let hooks = CampaignWorkerHooks(afterClaim: { id in
                ledger.access {
                    $0.claimed.append(id)
                    // This tracks observed callback boundaries. The runtime's
                    // SinkReturned event occurs after our sink closure returns.
                    let live = $0.claimed.count - $0.successfulSinkBodies
                    $0.maximumOutstanding = max($0.maximumOutstanding, live)
                    if live > bound { $0.violations.append("live work exceeded \(bound)") }
                }
            }, afterComplete: { id in
                let reached = ledger.access { $0.completed.append(id); return $0.completed.count == bound }
                if reached { full.open() }
            })
            var receipts: [UInt64] = [], blockedSnapshot: Ledger?
            let r = try runCampaignUsing(config: CampaignConfig(jobs: 12, workers: 2, resultBuffer: buffer),
                limits: CampaignLimits(), factory: campaignTestJob, sink: { job in
                    receipts.append(job.jobID)
                    if receipts.count == 1 {
                        // No successful sink return yet. Completion precedes
                        // send; this positive snapshot witnesses full allowance
                        // and receipt-before-return progress. It cannot prove
                        // blocked send attempts: afterComplete is before send.
                        // The separate channel-probe tests establish that
                        // stronger witness without upgrading this proof class.
                        try full.wait("all outstanding slots filled while first sink blocks")
                        blockedSnapshot = ledger.read()
                    }
                    ledger.access { $0.successfulSinkBodies += 1 }
                }, hooks: hooks, runner: { _, _, c, _ in campaignTestReport(c) })
            let snapshot = try XCTUnwrap(blockedSnapshot), final = ledger.read()
            XCTAssertEqual(snapshot.successfulSinkBodies, 0)
            XCTAssertEqual(snapshot.claimed.count, bound); XCTAssertEqual(snapshot.completed.count, bound)
            XCTAssertEqual(final.violations, []); XCTAssertEqual(final.maximumOutstanding, bound)
            XCTAssertEqual(receipts.count, 12); XCTAssertEqual(Set(receipts).count, 12)
            XCTAssertEqual(Set(final.claimed), Set(receipts)); XCTAssertEqual(Set(final.completed), Set(receipts))
            XCTAssertGreaterThan(r.started, bound, "bound is outstanding work, not lifetime job starts")
            XCTAssertEqual(r.started, 12); XCTAssertEqual(r.finished, 12); XCTAssertEqual(r.delivered, 12)
            XCTAssertEqual(r.reportedTransitions, 12); XCTAssertEqual(r.reportedSkippedChecks, 24)
            XCTAssertTrue(r.accountingComplete); campaignTestAccounting(r)
        }
    }

    func testRendezvousBlocksSendersUntilReceiptAndThenPermitsNextClaim() throws {
        try checkBlockedSenders(buffer: 0)
    }

    func testFullBufferBlocksSendersUntilReceiptAndThenPermitsNextClaim() throws {
        try checkBlockedSenders(buffer: 1)
    }

    private func checkBlockedSenders(buffer: Int) throws {
        let probe = CampaignChannelProbe()
        let exited = CampaignTestBox<[Int]>([])
        let hooks = CampaignWorkerHooks(beforeExit: { index in exited.access { $0.append(index) } },
                                        channelProbe: probe)
        var receipts: [UInt64] = [], held: [CampaignChannelProbe.Snapshot] = []
        let report = try runCampaignUsing(config: CampaignConfig(jobs: 12, workers: 2, resultBuffer: buffer),
            limits: CampaignLimits(), factory: campaignTestJob, sink: { job in
                receipts.append(job.jobID)
                if receipts.count <= 2 {
                    // This acquires the actual channel condition after both
                    // workers released it inside full/rendezvous condition.wait.
                    // Receipt invalidates earlier wait observations, so the
                    // second hold requires fresh blocked paths after receipt 2.
                    // Timeout is failure only; elapsed time is never evidence.
                    held.append(try probe.waitForBlockedSenders(2))
                }
            }, hooks: hooks, runner: { _, _, c, _ in campaignTestReport(c) })

        XCTAssertEqual(held.count, 2)
        for (offset, snapshot) in held.enumerated() {
            let received = offset + 1
            XCTAssertEqual(Set(snapshot.waiting.keys), Set([0, 1]))
            XCTAssertEqual(Set(snapshot.waiting.values.map(\.jobID)).count, 2)
            XCTAssertEqual(snapshot.events.filter { $0.kind == .received }.count, received)
            XCTAssertEqual(snapshot.events.filter { $0.kind == .claimed }.count, buffer + 2 + received)
            XCTAssertEqual(snapshot.events.filter { $0.kind == .sendReturned }.count, buffer + received)
            XCTAssertEqual(snapshot.joinedWorkers, [])
            if buffer == 0 {
                XCTAssertEqual(snapshot.waiting.values.filter { $0.kind == .waitingForSlot }.count, 1)
                XCTAssertEqual(snapshot.waiting.values.filter { $0.kind == .waitingForReceipt }.count, 1)
            } else {
                XCTAssertTrue(snapshot.waiting.values.allSatisfy { $0.kind == .waitingForCapacity })
            }
            for blocked in snapshot.waiting.values {
                let claims = snapshot.events.filter { $0.kind == .claimed && $0.workerID == blocked.workerID }
                XCTAssertEqual(claims.last?.jobID, blocked.jobID)
                XCTAssertFalse(snapshot.events.contains { $0.kind == .sendReturned && $0.jobID == blocked.jobID })
            }
        }

        let first = try XCTUnwrap(held.first), second = try XCTUnwrap(held.dropFirst().first)
        let nextReceipt = try XCTUnwrap(second.events.indices.first {
            $0 >= first.events.count && second.events[$0].kind == .received
        })
        let resumed = first.waiting.values.filter { blocked in
            second.events.contains { $0.kind == .sendReturned && $0.jobID == blocked.jobID }
        }
        // Exactly one channel slot is released by receipt 2. Its blocked worker
        // must positively return from send, claim again, and reach another wait
        // before sink 2 returns. The other blocked worker cannot claim yet.
        XCTAssertEqual(resumed.count, 1)
        let resumedWorker = try XCTUnwrap(resumed.first)
        let sendReturn = try XCTUnwrap(second.events.firstIndex {
            $0.kind == .sendReturned && $0.jobID == resumedWorker.jobID
        })
        let nextClaim = try XCTUnwrap(second.events.indices.first {
            $0 > sendReturn && second.events[$0].kind == .claimed && second.events[$0].workerID == resumedWorker.workerID
        })
        XCTAssertLessThan(nextReceipt, sendReturn)
        XCTAssertLessThan(sendReturn, nextClaim)
        XCTAssertNotEqual(second.events[nextClaim].jobID, resumedWorker.jobID)
        if buffer == 0 { XCTAssertEqual(second.events[nextReceipt].jobID, resumedWorker.jobID) }
        else {
            // Buffered send is freed by receipt of the older queued report,
            // and can return before the newly enqueued report is received.
            XCTAssertNotEqual(second.events[nextReceipt].jobID, resumedWorker.jobID)
            XCTAssertFalse(second.events.contains { $0.kind == .received && $0.jobID == resumedWorker.jobID })
        }

        let final = probe.snapshot(), events = final.events
        var active: [Int: UInt64] = [:]
        for (index, event) in events.enumerated() {
            switch event.kind {
            case .claimed:
                XCTAssertNil(active[event.workerID], "worker claimed again before its previous send returned")
                active[event.workerID] = event.jobID
            case .sendReturned:
                XCTAssertEqual(active.removeValue(forKey: event.workerID), event.jobID)
                if buffer == 0 {
                    let receipt = try XCTUnwrap(events.firstIndex { $0.kind == .received && $0.jobID == event.jobID })
                    XCTAssertLessThan(receipt, index, "rendezvous send must return after its own receipt")
                }
            case .waitingForCapacity, .waitingForSlot, .waitingForReceipt:
                XCTAssertEqual(active[event.workerID], event.jobID)
                let endKind: CampaignChannelProbe.Event.Kind = event.kind == .waitingForReceipt ? .sendReturned : .enqueued
                let end = try XCTUnwrap(events.indices.first {
                    $0 > index && events[$0].kind == endKind && events[$0].jobID == event.jobID
                })
                let freeingReceipt = try XCTUnwrap(events.indices.first {
                    $0 > index && $0 < end && events[$0].kind == .received
                        && (event.kind != .waitingForReceipt || events[$0].jobID == event.jobID)
                })
                XCTAssertLessThan(freeingReceipt, end, "every actual blocked path requires a freeing receipt")
            case .enqueued, .received: break
            }
        }
        XCTAssertEqual(active, [:]); XCTAssertTrue(final.waiting.isEmpty)
        XCTAssertEqual(final.joinedWorkers.sorted(), [0, 1])
        XCTAssertEqual(exited.read().sorted(), [0, 1])
        for kind in [CampaignChannelProbe.Event.Kind.claimed, .enqueued, .received, .sendReturned] {
            let ids = events.filter { $0.kind == kind }.map(\.jobID)
            XCTAssertEqual(ids.count, 12); XCTAssertEqual(Set(ids), Set((0..<12).map { UInt64($0) }))
        }
        XCTAssertEqual(receipts, events.filter { $0.kind == .received }.map(\.jobID))
        XCTAssertEqual(report.termination, .jobsCompleted)
        XCTAssertEqual(report.requested, 12); XCTAssertEqual(report.started, 12)
        XCTAssertEqual(report.finished, 12); XCTAssertEqual(report.delivered, 12); XCTAssertEqual(report.notStarted, 0)
        XCTAssertEqual(report.errors, 0); XCTAssertEqual(report.failures, 0)
        XCTAssertEqual(report.reportedCases, 12); XCTAssertEqual(report.reportedTransitions, 12)
        XCTAssertEqual(report.reportedSkippedChecks, 24)
        XCTAssertTrue(report.accountingComplete); campaignTestAccounting(report)
    }

    func testFailureStopsClaimsButAlreadyClaimedJobRunsToCompletion() throws {
        let bothClaimed = CampaignTestGate(), failureCompleted = CampaignTestGate()
        let hooks = CampaignWorkerHooks(afterClaim: { id in
            if id == 0 { try bothClaimed.wait("second job was claimed") }
            if id == 1 { bothClaimed.open(); try failureCompleted.wait("first failure completed") }
        }, afterComplete: { id in if id == 0 { failureCompleted.open() } })
        var rows: [JobReport<Int>] = []
        let r = try runCampaignUsing(config: CampaignConfig(jobs: 8, workers: 2, resultBuffer: 0,
            fuzz: FuzzConfig(cases: 2, maxSteps: 2), stopOnFailure: true), limits: CampaignLimits(),
            factory: { id in
                var g = campaignTestJob(id).generated
                if id == 0 { g.model.checkState = { _ in [.failed("stop", "initial finding")] } }
                return CampaignJob(g, metadata: { Metadata(name: "job:\(id)") })
            }, sink: { rows.append($0) }, hooks: hooks,
            runner: { _, job, c, limits in try fuzz(job.generated, config: c, limits: limits) })
        XCTAssertEqual(Set(rows.map(\.jobID)), [0, 1])
        let passing = try XCTUnwrap(rows.first { $0.jobID == 1 })
        XCTAssertEqual(try campaignTestCompleted(passing).termination, "cases_completed")
        XCTAssertEqual(try campaignTestCompleted(passing).transitions, 4)
        XCTAssertEqual(r.termination, .failureFound); XCTAssertEqual(r.failures, 1)
        XCTAssertEqual(r.started, 2); XCTAssertEqual(r.finished, 2); XCTAssertEqual(r.notStarted, 6)
        XCTAssertEqual(r.reportedCases, 3); XCTAssertEqual(r.reportedTransitions, 4)
        XCTAssertEqual(r.reportedSkippedChecks, 6); XCTAssertTrue(r.accountingComplete)
    }

    func testPropertyFailuresCanCompleteAllJobsWithoutStopping() throws {
        let r = try runCampaign(config: CampaignConfig(jobs: 3, stopOnFailure: false), factory: { id in
            var g = campaignTestJob(id).generated
            g.model.checkState = { _ in [.failed("property", "fixture")] }
            return CampaignJob(g, metadata: { Metadata(name: "failure") })
        }, sink: { job in
            let report = try campaignTestCompleted(job)
            XCTAssertEqual(report.failure?.inputs, []); XCTAssertEqual(report.termination, "failure_found")
        })
        XCTAssertEqual(r.termination, .jobsCompleted); XCTAssertEqual(r.failures, 3)
        XCTAssertEqual(r.errors, 0); XCTAssertEqual(r.delivered, 3); XCTAssertTrue(r.accountingComplete)
    }

    func testAcceptedCancellationCompletionWinsDeadlineAndPropertyStops() throws {
        let barrier = CampaignTestBarrier(3)
        let hooks = CampaignWorkerHooks(afterClaim: { _ in try barrier.arrive() })
        var rows: [JobReport<Int>] = []
        let r = try runCampaignUsing(config: CampaignConfig(jobs: 12, workers: 3, stopOnFailure: true),
            limits: CampaignLimits(), factory: campaignTestJob, sink: { rows.append($0) }, hooks: hooks,
            runner: { id, _, c, _ in
                if id == 0 {
                    var r = campaignTestReport(c, termination: "failure_found")
                    r.failure = Failure(inputs: [1], violations: [Violation(phase: "state", check: .failed("bad", "fixture"))])
                    return r
                }
                return campaignTestReport(c, termination: id == 1 ? "deadline" : "cancelled")
            })
        XCTAssertEqual(Set(rows.map(\.jobID)), [0, 1, 2]); XCTAssertEqual(r.started, 3)
        XCTAssertEqual(r.finished, 3); XCTAssertEqual(r.delivered, 3); XCTAssertEqual(r.notStarted, 9)
        XCTAssertEqual(r.termination, .cancelled); XCTAssertEqual(r.failures, 1)
        XCTAssertEqual(r.reportedCases, 3); XCTAssertTrue(r.accountingComplete)
    }

    func testCancellationInsideGenerationStopsCooperativelyWithoutApplyingInput() throws {
        let cancelled = CampaignTestBox(false), events = CampaignTestBox<[String]>([])
        var rows: [JobReport<Int>] = []
        let r = try runCampaign(config: CampaignConfig(jobs: 4),
            limits: CampaignLimits(isCancelled: { cancelled.read() }), factory: { id in
                var g = campaignTestJob(id).generated
                g.generate = { _, _ in events.access { $0.append("generate") }; cancelled.access { $0 = true }; return 1 }
                g.model.step = { s, _ in events.access { $0.append("step") }; return Transition(state: s) }
                return CampaignJob(g, metadata: { Metadata(name: "cancel") }, tearDown: { events.access { $0.append("tear-down") } })
            }, sink: { rows.append($0); events.access { $0.append("sink") } })
        XCTAssertEqual(events.read(), ["generate", "tear-down", "sink"])
        let fuzz = try campaignTestCompleted(XCTUnwrap(rows.first))
        XCTAssertEqual(fuzz.termination, "cancelled"); XCTAssertEqual(fuzz.cases, 1)
        XCTAssertEqual(fuzz.transitions, 0); XCTAssertEqual(fuzz.skippedChecks, 1)
        XCTAssertEqual(r.termination, .cancelled); XCTAssertEqual(r.started, 1); XCTAssertEqual(r.notStarted, 3)
    }

    func testCancellationByLastSinkCannotRewriteAlreadyClaimedCampaign() throws {
        for workers in [1, 2] {
            let cancelled = CampaignTestBox(false)
            let r = try runCampaign(config: CampaignConfig(jobs: 1, workers: workers, fuzz: FuzzConfig(cases: 0)),
                limits: CampaignLimits(isCancelled: { cancelled.read() }), factory: campaignTestJob,
                sink: { _ in cancelled.access { $0 = true } })
            XCTAssertTrue(cancelled.read()); XCTAssertEqual(r.termination, .jobsCompleted)
            XCTAssertEqual(r.delivered, 1)
        }
    }

    func testFactoryTimeConsumesGlobalDeadlineButCannotSkipSubsequentMetadata() throws {
        let events = CampaignTestBox<[String]>([]), release = CampaignTestGate()
        var rows: [JobReport<Int>] = []
        let r = try runGuidedCampaign(config: CampaignConfig(jobs: 2),
            limits: CampaignLimits(maxDuration: 0.25), factory: { id in
                events.access { $0.append("factory-enter") }
                // A finite timer releases a blocking callback after the budget.
                // The callback's return, metadata and delivered report, not a
                // sleep or negative timeout, establish the observed boundary.
                DispatchQueue.global().asyncAfter(deadline: .now() + .milliseconds(350)) { release.open() }
                try release.wait("factory released by timer")
                events.access { $0.append("factory-return") }
                let job = campaignTestJob(id)
                return CampaignJob(job.generated, metadata: {
                    events.access { $0.append("metadata") }; return try job.metadata()
                }, tearDown: { events.access { $0.append("tear-down") } })
            }, feedbackFactory: { _, _ -> Feedback<Int, Int, Int, Int> in
                events.access { $0.append("feedback-factory") }
                var feedback = campaignTestFeedback()
                feedback.metadata = { events.access { $0.append("feedback-metadata") }; return FeedbackMetadata(name: "deadline", build: "v1") }
                return feedback
            }, sink: { rows.append($0); events.access { $0.append("sink") } })
        XCTAssertEqual(events.read(), ["factory-enter", "factory-return", "metadata", "feedback-factory", "feedback-metadata", "tear-down", "sink"])
        let fuzz = try campaignTestCompleted(XCTUnwrap(rows.first))
        XCTAssertEqual(fuzz.termination, "deadline"); XCTAssertEqual(fuzz.cases, 0)
        XCTAssertEqual(fuzz.transitions, 0); XCTAssertNotNil(fuzz.guidance)
        XCTAssertEqual(r.termination, .deadline); XCTAssertEqual(r.started, 1); XCTAssertEqual(r.notStarted, 1)
        XCTAssertTrue(r.accountingComplete)
    }

    func testFeedbackFactoryAndFeedbackMetadataTimeConsumeRemainingDeadline() throws {
        for delayFactory in [true, false] {
            let events = CampaignTestBox<[String]>([]), release = CampaignTestGate()
            let initialCalls = CampaignTestBox(0)
            var rows: [JobReport<Int>] = []
            let r = try runGuidedCampaign(config: CampaignConfig(jobs: 2),
                limits: CampaignLimits(maxDuration: 0.25), factory: { id in
                    var generated = campaignTestJob(id).generated
                    generated.model.initialState = { initialCalls.access { $0 += 1 }; return 0 }
                    return CampaignJob(generated, metadata: {
                        events.access { $0.append("model-metadata") }; return Metadata(name: "deadline")
                    }, tearDown: { events.access { $0.append("tear-down") } })
                }, feedbackFactory: { _, _ -> Feedback<Int, Int, Int, Int> in
                    events.access { $0.append("feedback-factory-enter") }
                    if delayFactory {
                        DispatchQueue.global().asyncAfter(deadline: .now() + .milliseconds(350)) { release.open() }
                        try release.wait("feedback factory released after global deadline")
                    }
                    events.access { $0.append("feedback-factory-return") }
                    var feedback = campaignTestFeedback()
                    feedback.metadata = {
                        events.access { $0.append("feedback-metadata-enter") }
                        if !delayFactory {
                            DispatchQueue.global().asyncAfter(deadline: .now() + .milliseconds(350)) { release.open() }
                            try release.wait("feedback metadata released after remaining deadline")
                        }
                        events.access { $0.append("feedback-metadata-return") }
                        return FeedbackMetadata(name: "deadline", build: "v1")
                    }
                    return feedback
                }, sink: { rows.append($0); events.access { $0.append("sink") } })
            XCTAssertEqual(events.read(), ["model-metadata", "feedback-factory-enter", "feedback-factory-return",
                "feedback-metadata-enter", "feedback-metadata-return", "tear-down", "sink"])
            XCTAssertEqual(initialCalls.read(), 0, "elapsed feedback time must not be replaced by a fresh full budget")
            let fuzz = try campaignTestCompleted(XCTUnwrap(rows.first))
            XCTAssertEqual(fuzz.termination, "deadline"); XCTAssertEqual(fuzz.cases, 0)
            XCTAssertEqual(fuzz.transitions, 0); XCTAssertEqual(fuzz.guidance?.uniqueFeatures, 0)
            XCTAssertEqual(r.termination, .deadline); XCTAssertEqual(r.started, 1); XCTAssertEqual(r.notStarted, 1)
            XCTAssertEqual(r.delivered, 1); XCTAssertTrue(r.accountingComplete)
        }
    }

    func testSinkErrorsDrainFullChannelJoinWorkersAndPreserveFirstError() throws {
        for buffer in [0, 1] {
            for successfulSinks in [0, 1] {
                for panicAndExitFailure in [false, true] {
                    let target = buffer + 2 + 1 + successfulSinks
                    let full = CampaignTestGate(), completed = CampaignTestBox<[UInt64]>([])
                    let tornDown = CampaignTestBox<[UInt64]>([]), exited = CampaignTestBox<[Int]>([])
                    let hooks = CampaignWorkerHooks(afterComplete: { id in
                        let reached = completed.access { $0.append(id); return $0.count == target }
                        if reached { full.open() }
                    }, beforeExit: { index in
                        exited.access { $0.append(index) }
                        if panicAndExitFailure { throw ModelError("late exit \(index)") }
                    })
                    var attempted: [UInt64] = []
                    let r = try runCampaignUsing(config: CampaignConfig(jobs: 12, workers: 2, resultBuffer: buffer),
                        limits: CampaignLimits(), factory: { id in
                            let job = campaignTestJob(id)
                            return CampaignJob(job.generated, metadata: job.metadata,
                                tearDown: { tornDown.access { $0.append(id) } })
                        }, sink: { job in
                            attempted.append(job.jobID) // an externally visible side effect
                            if attempted.count > successfulSinks {
                                try full.wait("full channel before sink error")
                                if panicAndExitFailure { throw CampaignPanic("sink owns termination") }
                                throw ModelError("sink owns termination")
                            }
                        }, hooks: hooks, runner: { _, _, c, _ in campaignTestReport(c) })
                    XCTAssertEqual(attempted.count, successfulSinks + 1, "no sink calls during drain")
                    XCTAssertEqual(Set(attempted).count, attempted.count, "no retry of side effects")
                    XCTAssertEqual(r.termination, .callbackError(panicAndExitFailure
                        ? "campaign callback panicked: sink owns termination" : "sink owns termination"))
                    XCTAssertEqual(r.started, target); XCTAssertEqual(r.finished, target)
                    XCTAssertEqual(r.notStarted, 12 - target); XCTAssertEqual(r.delivered, successfulSinks)
                    XCTAssertEqual(r.errors, 0, "sink/worker infrastructure is not a job error")
                    XCTAssertEqual(r.reportedCases, UInt64(target)); XCTAssertEqual(r.reportedTransitions, UInt64(target))
                    XCTAssertEqual(r.reportedSkippedChecks, UInt64(target * 2))
                    XCTAssertEqual(r.accountingComplete, !panicAndExitFailure)
                    XCTAssertEqual(Set(completed.read()), Set(tornDown.read()))
                    XCTAssertEqual(tornDown.read().count, target); XCTAssertEqual(exited.read().sorted(), [0, 1])
                    campaignTestAccounting(r)
                }
            }
        }
    }

    func testFirstWorkerStartFailureLosesNoWorkAndCallsNoApplications() throws {
        let calls = CampaignTestBox<[String]>([])
        let hooks = CampaignWorkerHooks(beforeStart: { index in
            calls.access { $0.append("start:\(index)") }; throw ModelError("start denied")
        })
        let r = try runCampaignUsing(config: CampaignConfig(jobs: 5, workers: 2), limits: CampaignLimits(),
            factory: { id in calls.access { $0.append("factory") }; return campaignTestJob(id) },
            sink: { _ in calls.access { $0.append("sink") } }, hooks: hooks,
            runner: { _, _, c, _ in campaignTestReport(c) })
        XCTAssertEqual(calls.read(), ["start:0"])
        XCTAssertEqual(r.termination, .workerError("unable to start campaign worker: start denied"))
        XCTAssertEqual(r.started, 0); XCTAssertEqual(r.finished, 0); XCTAssertEqual(r.notStarted, 5)
        XCTAssertEqual(r.errors, 0); XCTAssertTrue(r.accountingComplete)
    }

    func testLaterWorkerStartFailureStillDeliversAlreadyClaimedReport() throws {
        let claimed = CampaignTestGate(), release = CampaignTestGate(), cancelled = CampaignTestBox(false)
        let hooks = CampaignWorkerHooks(beforeStart: { index in
            if index == 1 {
                try claimed.wait("first worker claimed before second start fails")
                cancelled.access { $0 = true }; release.open()
                throw ModelError("second start denied")
            }
        }, afterClaim: { _ in
            claimed.open(); try release.wait("second start attempt happened")
        })
        var rows: [JobReport<Int>] = []
        let r = try runCampaignUsing(config: CampaignConfig(jobs: 5, workers: 2, resultBuffer: 0),
            limits: CampaignLimits(isCancelled: { cancelled.read() }), factory: campaignTestJob,
            sink: { rows.append($0) }, hooks: hooks, runner: { _, _, c, _ in campaignTestReport(c) })
        XCTAssertEqual(rows.map(\.jobID), [0], "WorkerError does not disable delivery")
        XCTAssertEqual(r.started, 1); XCTAssertEqual(r.finished, 1); XCTAssertEqual(r.delivered, 1)
        XCTAssertEqual(r.notStarted, 4); XCTAssertEqual(r.errors, 0); XCTAssertTrue(r.accountingComplete)
        XCTAssertEqual(r.termination, .workerError("unable to start campaign worker: second start denied"))
    }

    func testLostClaimIsObservedAfterDrainAndDoesNotStopSurvivingWorkerEarly() throws {
        let lost = CampaignTestGate(), claimed = CampaignTestBox<[UInt64]>([])
        let hooks = CampaignWorkerHooks(beforeStart: { index in
            if index == 1 { try lost.wait("first worker lost job zero") }
        }, afterClaim: { id in
            claimed.access { $0.append(id) }
            if id == 0 { lost.open(); throw ModelError("lost claimed job") }
        })
        var received: [UInt64] = []
        let r = try runCampaignUsing(config: CampaignConfig(jobs: 5, workers: 2, resultBuffer: 0),
            limits: CampaignLimits(), factory: campaignTestJob, sink: { received.append($0.jobID) }, hooks: hooks,
            runner: { _, _, c, _ in campaignTestReport(c) })
        XCTAssertEqual(claimed.read(), [0, 1, 2, 3, 4])
        XCTAssertEqual(received, [1, 2, 3, 4], "no synthetic outcome for the lost claim")
        XCTAssertEqual(r.started, 5); XCTAssertEqual(r.finished, 4); XCTAssertEqual(r.delivered, 4)
        XCTAssertEqual(r.notStarted, 0); XCTAssertEqual(r.errors, 0); XCTAssertFalse(r.accountingComplete)
        XCTAssertEqual(r.reportedCases, 4); XCTAssertEqual(r.reportedTransitions, 4)
        XCTAssertEqual(r.termination, .workerError("campaign worker failed: lost claimed job"))
        campaignTestAccounting(r)
    }

    func testExitFailureAfterEveryOutcomeStillConservativelyLosesAccounting() throws {
        let exited = CampaignTestBox<[Int]>([]), both = CampaignTestBarrier(2)
        let hooks = CampaignWorkerHooks(afterClaim: { _ in try both.arrive() }, beforeExit: { index in
            exited.access { $0.append(index) }; throw ModelError("exit \(index)")
        })
        var received: [UInt64] = []
        let r = try runCampaignUsing(config: CampaignConfig(jobs: 2, workers: 2, resultBuffer: 0),
            limits: CampaignLimits(), factory: campaignTestJob, sink: { received.append($0.jobID) }, hooks: hooks,
            runner: { _, _, c, _ in campaignTestReport(c) })
        XCTAssertEqual(Set(received), [0, 1]); XCTAssertEqual(exited.read().sorted(), [0, 1])
        XCTAssertEqual(r.started, 2); XCTAssertEqual(r.finished, 2); XCTAssertEqual(r.delivered, 2)
        XCTAssertEqual(r.errors, 0); XCTAssertFalse(r.accountingComplete)
        // Joins are processed by start index, independent of exit timing.
        XCTAssertEqual(r.termination, .workerError("campaign worker failed: exit 1"))
    }

    func testCompletedCountersSaturateWithoutInventingJobErrors() throws {
        let r = try runCampaignUsing(config: CampaignConfig(jobs: 2), limits: CampaignLimits(),
            factory: campaignTestJob, sink: { _ in }, runner: { id, _, c, _ in
                campaignTestReport(c, transitions: id == 0 ? .max : 1, skipped: id == 0 ? .max : 1)
            })
        XCTAssertEqual(r.reportedTransitions, .max); XCTAssertEqual(r.reportedSkippedChecks, .max)
        XCTAssertEqual(r.reportedCases, 2); XCTAssertEqual(r.errors, 0)
        XCTAssertFalse(r.accountingComplete); XCTAssertEqual(r.termination, .jobsCompleted)
    }

    func testZeroBudgetsRetainOrdinaryAndGuidedKernelMeanings() throws {
        for guided in [false, true] {
            for (config, cases, skipped, termination, features) in [
                (FuzzConfig(cases: 0), 0, UInt64(0), "cases_completed", 0),
                (FuzzConfig(cases: 2, maxSteps: 0, maxTransitions: 0), 2, 2, "cases_completed", 1),
                (FuzzConfig(cases: 2, maxSteps: 1, maxTransitions: 0), 1, 1, "transition_limit", 1)
            ] {
                var rows: [JobReport<Int>] = []
                let campaign = CampaignConfig(jobs: 1, workers: 2, fuzz: config)
                let report: CampaignReport
                if guided {
                    report = try runGuidedCampaign(config: campaign, factory: campaignTestJob,
                        feedbackFactory: { _, _ in campaignTestFeedback() }, sink: { rows.append($0) })
                } else { report = try runCampaign(config: campaign, factory: campaignTestJob, sink: { rows.append($0) }) }
                let r = try campaignTestCompleted(XCTUnwrap(rows.first))
                XCTAssertEqual(r.cases, cases); XCTAssertEqual(r.transitions, 0)
                XCTAssertEqual(r.skippedChecks, skipped); XCTAssertEqual(r.termination, termination)
                if guided { XCTAssertEqual(r.guidance?.uniqueFeatures, features) }
                else { XCTAssertNil(r.guidance) }
                XCTAssertEqual(report.reportedCases, UInt64(cases)); XCTAssertEqual(report.reportedSkippedChecks, skipped)
                XCTAssertEqual(report.termination, .jobsCompleted)
            }
        }
    }

    func testCallerSinkCanRunIndependentCampaign() throws {
        var inner: [CampaignReport] = []
        let outer = try runCampaign(config: CampaignConfig(jobs: 2, workers: 2,
            fuzz: FuzzConfig(cases: 0)), factory: campaignTestJob, sink: { _ in
            inner.append(try runCampaign(config: CampaignConfig(firstJob: 100, jobs: 1,
                fuzz: FuzzConfig(cases: 0)), factory: campaignTestJob, sink: { _ in }))
        })
        XCTAssertEqual(outer.delivered, 2); XCTAssertEqual(inner.count, 2)
        XCTAssertTrue(inner.allSatisfy { $0.termination == .jobsCompleted && $0.delivered == 1 })
    }

    func testBlockedSinkAllowsAnotherJobToCompleteThroughSameCoordinator() throws {
        let secondClaimed = CampaignTestGate(), releaseSecond = CampaignTestGate()
        let secondCompleted = CampaignTestGate()
        let hooks = CampaignWorkerHooks(afterClaim: { id in
            if id == 0 { try secondClaimed.wait("second job claimed before first completes") }
            if id == 1 {
                secondClaimed.open()
                try releaseSecond.wait("first sink releases second job")
            }
        }, afterComplete: { id in
            if id == 1 { secondCompleted.open() }
        })
        var receipts: [UInt64] = [], sawCompletionInSink = false
        let r = try runCampaignUsing(config: CampaignConfig(jobs: 2, workers: 2, resultBuffer: 0),
            limits: CampaignLimits(), factory: campaignTestJob, sink: { job in
                receipts.append(job.jobID)
                if job.jobID == 0 {
                    releaseSecond.open()
                    // This is the outer campaign's Complete event, requiring
                    // its actual coordinator mutex while this sink is blocked.
                    try secondCompleted.wait("second Complete progresses through same coordinator")
                    sawCompletionInSink = true
                }
            }, hooks: hooks, runner: { _, _, c, _ in campaignTestReport(c) })
        XCTAssertTrue(sawCompletionInSink); XCTAssertEqual(receipts, [0, 1])
        XCTAssertEqual(r.termination, .jobsCompleted)
        XCTAssertEqual(r.finished, 2); XCTAssertEqual(r.delivered, 2)
    }
}

import Foundation
import Statelessness

private func completedFuzz(_ job: JobReport<UInt64>) throws -> FuzzReport<UInt64> {
    guard case let .completed(fuzz) = job.outcome else {
        throw AdapterError("host campaign did not complete job \(job.jobID)")
    }
    return fuzz
}

private func searchCallbacks(_ events: [CallbackEvent]) -> [CallbackEvent] {
    // Only campaign construction/metadata/cleanup/delivery lack a matching
    // bare-run boundary. Keep every model, feedback and observer event in order.
    let boundaries = ["factory", "feedback_factory", "metadata", "model_drop", "feedback_drop", "sink"]
    return events.filter { !boundaries.contains($0.name) }
}

private func requireObserverEvidence(_ events: [CallbackEvent], jobID: UInt64, guided: Bool,
                                     source: String) throws {
    let observers = events.filter { $0.name.hasPrefix("observer_") }
    let initialCount = observers.filter { $0.name == "observer_initial" }.count
    let transitionCount = observers.filter { $0.name == "observer_transition" }.count
    let errorCount = observers.filter { $0.name.hasPrefix("observer_error:") }.count
    let context = "\(source) job \(jobID)"
    // Independent of either returned report or the other run: the healthy
    // fixture always enables an input and completes 3 cases of 4 transitions.
    // Bare and campaign share a kernel; equality alone could miss a shared loss.
    try require(initialCount == 3 && transitionCount == 12 && errorCount == 0,
                "\(context) observer counts: initial=\(initialCount), transition=\(transitionCount), error=\(errorCount); expected 3/12/0")
    let expectedOrder = (0..<3).flatMap { _ in
        ["observer_initial"] + Array(repeating: "observer_transition", count: 4)
    }
    try require(observers.map(\.name) == expectedOrder,
                "\(context) observer order differs from three initial/four-transition batches")
    let steps = events.filter { $0.name.hasPrefix("step:") }
    let transitions = observers.filter { $0.name == "observer_transition" }
    try require(steps.count == 12 && transitions.allSatisfy { $0.input != nil }
                && transitions.map(\.input) == steps.map(\.input),
                "\(context) transition observers do not match the applied inputs in order")
    // Fix the check/observer/feedback placement independently of the shared
    // kernel as well. Mutation may reuse inputs, so generate counts vary.
    var expectedCallbacks = guided ? ["feedback_metadata"] : []
    for caseIndex in 0..<3 {
        expectedCallbacks += ["initial", "check_state:0", "observer_initial"]
        if guided { expectedCallbacks.append("feedback_initial") }
        for step in 0..<4 {
            guard let input = steps[caseIndex * 4 + step].input else {
                throw AdapterError("\(context) applied input missing from audit")
            }
            expectedCallbacks += ["step:\(step):\(input)", "check_state:\(step + 1)",
                                  "check_transition:\(step):\(input)", "observer_transition"]
            if guided { expectedCallbacks.append("feedback_transition") }
        }
    }
    let observedCallbacks = searchCallbacks(events).filter {
        !$0.name.hasPrefix("generate:") && !$0.name.hasPrefix("enabled:")
    }.map(\.name)
    try require(observedCallbacks == expectedCallbacks,
                "\(context) check/observer/feedback placement differs from the healthy fixture")
}

private func observerEvidence(_ events: [CallbackEvent]) -> [String: Any] {
    // Raw native evidence retains roles, optional inputs and positions among
    // all callbacks. It never enters native-campaign-v1's Rust projection.
    return ["counts": ["initial": String(events.filter { $0.name == "observer_initial" }.count),
                       "transition": String(events.filter { $0.name == "observer_transition" }.count),
                       "check_error": String(events.filter { $0.name.hasPrefix("observer_error:") }.count)],
            "events": events.enumerated().map { index, event in
                ["index": String(index), "job_id": String(event.jobID), "callback": event.name,
                 "role": event.role, "input": nullable(event.input.map { String($0) })] as [String: Any]
            }]
}

private func hostRun(guided: Bool, workers: Int, jobs count: Int) throws -> [String: Any] {
    let audit = CallbackAudit()
    let config = fixtureConfig(masterSeed: .max, jobs: count, workers: workers,
                               cases: 3, maxSteps: 4, maxTransitions: 12, mutationPercent: 100)
    let factory: @Sendable (UInt64) throws -> CampaignJob<CounterState, UInt64, CounterOutput> = { id in
        audit.note(id, "factory")
        return counterJob(id, mode: .random, audit: audit)
    }
    var jobs: [JobReport<UInt64>] = []
    let sink: (JobReport<UInt64>) throws -> Void = { job in
        audit.note(job.jobID, "sink")
        jobs.append(job)
    }
    var aggregate: CampaignReport?
    do {
        let report: CampaignReport
        if guided {
            report = try runGuidedCampaign(config: config, guidance: fixtureGuidance(), factory: factory,
                feedbackFactory: { id, _ in
                    audit.note(id, "feedback_factory")
                    return counterFeedback(id, audit: audit)
                }, sink: sink)
        } else {
            report = try runCampaign(config: config, factory: factory, sink: sink)
        }
        aggregate = report
        try require(report.started == count && report.finished == count && report.delivered == count,
                    "host run lost a requested job")
        try require(report.errors == 0 && report.failures == 0 && report.accountingComplete,
                    "host run lost work accounting or returned errors")
        try require(report.termination == .jobsCompleted, "host run stopped unexpectedly")
        try require(jobs.count == count && Set(jobs.map(\.jobID)).count == count,
                    "host sink received missing or duplicate reports")
        let snapshot = audit.snapshot()
        let workerRole = workers == 1 ? "calling" : "spawned"
        try require(snapshot.events.filter { $0.name == "factory" }.count == count,
                    "host did not construct one fresh model per job")
        for event in snapshot.events {
            try require(event.role == (event.name == "sink" ? "calling" : workerRole),
                        "callback \(event.name) ran on the wrong caller/worker role")
        }

        // Compare each identity-bearing result to a fresh public bare fuzz run.
        // This is an application collection check, not a CampaignReport merge.
        var nativeObserverEvidence: [[String: Any]] = []
        for job in jobs {
            try require(job.jobID >= config.firstJob && job.jobID - config.firstJob < UInt64(count),
                        "host sink received an unrequested job ID")
            let expectedSeed = jobSeed(masterSeed: config.masterSeed, jobID: job.jobID)
            try require(job.seed == expectedSeed && job.config.seed == expectedSeed,
                        "job seed/config does not match public derivation")
            let referenceAudit = CallbackAudit()
            let reference = counterJob(job.jobID, mode: .random, audit: referenceAudit)
            let localEvents = snapshot.events.filter { $0.jobID == job.jobID }
            func evidence() -> [String: Any] {
                ["job_id": String(job.jobID), "reference_api": guided ? "guidedFuzz" : "fuzz",
                 "expected_counts": ["initial": "3", "transition": "12", "check_error": "0"],
                 "campaign": observerEvidence(localEvents),
                 "bare": observerEvidence(referenceAudit.snapshot().events)]
            }
            let fuzzConfig = FuzzConfig(seed: expectedSeed, cases: config.fuzz.cases,
                maxSteps: config.fuzz.maxSteps, maxTransitions: config.fuzz.maxTransitions,
                mutationPercent: config.fuzz.mutationPercent)
            do {
                let expected: FuzzReport<UInt64>
                if guided {
                    expected = try guidedFuzz(reference.generated, config: fuzzConfig, guidance: fixtureGuidance(),
                        feedback: counterFeedback(job.jobID, audit: referenceAudit), observer: reference.observer)
                } else {
                    expected = try fuzz(reference.generated, config: fuzzConfig, observer: reference.observer)
                }
                let actual = try completedFuzz(job)
                for (source, result) in [("campaign", actual), ("bare", expected)] {
                    try require(result.cases == 3 && result.transitions == 12 && result.skippedChecks == 15
                                && result.termination == "cases_completed",
                                "\(source) job \(job.jobID) did not complete the independent healthy-fixture budget")
                }
                let expectedBytes = try JSONSerialization.data(withJSONObject: fuzzJSON(expected), options: [.sortedKeys])
                let actualBytes = try JSONSerialization.data(withJSONObject: fuzzJSON(actual), options: [.sortedKeys])
                try require(actualBytes == expectedBytes, "campaign result disagrees with independent bare fuzz call")
                let referenceEvents = referenceAudit.snapshot().events
                try require(referenceEvents.allSatisfy { $0.jobID == job.jobID },
                            "fresh bare run recorded another job's callbacks")
                try requireObserverEvidence(localEvents, jobID: job.jobID, guided: guided, source: "campaign")
                try requireObserverEvidence(referenceEvents, jobID: job.jobID, guided: guided, source: "bare")
                for prefix in ["generate:", "step:", "enabled:"] {
                    let observed = localEvents.filter { $0.name.hasPrefix(prefix) }.map(\.name)
                    let referenceNames = referenceEvents.filter { $0.name.hasPrefix(prefix) }.map(\.name)
                    try require(observed == referenceNames, "campaign changed \(prefix) callback order or inputs")
                }
                let observedSearch = searchCallbacks(localEvents)
                let referenceSearch = searchCallbacks(referenceEvents)
                try require(observedSearch.count == referenceSearch.count
                            && zip(observedSearch, referenceSearch).allSatisfy { pair in
                                pair.0.name == pair.1.name && pair.0.input == pair.1.input
                            }, "job \(job.jobID) campaign changed observer/model/feedback callback order or inputs")
                nativeObserverEvidence.append(evidence())
            } catch let comparisonError {
                // Preserve the failing traces too, without emitting a successful
                // host row or adding native observer entries to Rust projections.
                let failure: [String: Any] = ["schema": "native-campaign-example-observer-failure-v1",
                                             "error": String(describing: comparisonError), "evidence": evidence()]
                do {
                    var data = try JSONSerialization.data(withJSONObject: failure, options: [.sortedKeys])
                    data.append(10)
                    FileHandle.standardError.write(data)
                } catch {
                    FileHandle.standardError.write(Data("observer evidence serialization failed: \(error)\n".utf8))
                }
                throw comparisonError
            }
        }
        return ["config": campaignConfigJSON(config), "guided": guided,
                "jobs": try jobs.map(jobJSON), "callbacks": canonicalCallbacks(snapshot),
                "native_observer_evidence": nativeObserverEvidence,
                "roles": snapshot.events.filter { $0.name == "factory" || $0.name == "sink" }.map {
                    ["job_id": String($0.jobID), "callback": $0.name, "role": $0.role]
                }, "report": reportJSON(report)]
    } catch let hostError {
        // Campaign observer failures become job ModelErrors. Preserve their
        // actual messages and every available callback even when aggregate,
        // identity or seed assertions fail before a bare run is constructed.
        let outcomes: [[String: Any]] = jobs.map { job in
            let outcome: String
            let message: String?
            switch job.outcome {
            case .completed: outcome = "Completed"; message = nil
            case let .factoryError(error): outcome = "FactoryError"; message = error.description
            case let .modelError(error): outcome = "ModelError"; message = error.description
            case let .panicked(error): outcome = "Panicked"; message = error
            }
            return ["job_id": String(job.jobID), "seed": String(job.seed),
                    "config_seed": String(job.config.seed), "outcome": outcome,
                    "error": nullable(message)]
        }
        let failure: [String: Any] = ["schema": "native-campaign-example-host-failure-v1",
            "error": String(describing: hostError), "config": campaignConfigJSON(config),
            "guided": guided, "jobs": outcomes, "report": nullable(aggregate.map(reportJSON)),
            "campaign": observerEvidence(audit.snapshot().events)]
        do {
            var data = try JSONSerialization.data(withJSONObject: failure, options: [.sortedKeys])
            data.append(10)
            FileHandle.standardError.write(data)
        } catch {
            FileHandle.standardError.write(Data("host evidence serialization failed: \(error)\n".utf8))
        }
        throw hostError
    }
}

func hostChecks() throws -> [String: Any] {
    let specifications: [(String, Bool, Int, Int)] = [
        ("ordinary-calling", false, 1, 1), ("ordinary-workers2-jobs1", false, 2, 1),
        ("guided-calling", true, 1, 1), ("guided-workers2-jobs1", true, 2, 1),
        ("ordinary-isolation", false, 3, 7), ("guided-isolation", true, 3, 7)
    ]
    var passed: [String] = []
    var checks: [String: Any] = [:]
    for (name, guided, workers, jobs) in specifications {
        checks[name] = try hostRun(guided: guided, workers: workers, jobs: jobs)
        passed.append(name)
    }
    for capacity in [0, 1] {
        let orderName = "out-of-order-buffer\(capacity)"
        checks[orderName] = try outOfOrderCheck(capacity: capacity)
        passed.append(orderName)
        let boundName = "slow-sink-buffer\(capacity)"
        checks[boundName] = try slowSinkCheck(capacity: capacity)
        passed.append(boundName)
        let drainName = "sink-error-drain-buffer\(capacity)"
        checks[drainName] = try sinkDrainCheck(capacity: capacity)
        passed.append(drainName)
    }
    checks["cancellation-drain"] = try cancellationDrainCheck()
    passed.append("cancellation-drain")
    checks["native-policy"] = try nativePolicyChecks()
    passed.append("native-policy")
    return ["schema": "native-campaign-example-host-v1", "engine": "swift",
            "passed": passed, "checks": checks]
}

private struct BarrierState: Sendable {
    var started: Set<UInt64> = []
    var completed: Set<UInt64> = []
    var threads: Set<UInt64> = []
    var signals: Set<String> = []
    var protocolErrors: [String] = []
    var holding = false
    var heldBound: Int?
    var maximumWhileHeld = 0
}

// Explicit application handshakes, separate from the runtime coordinator. All
// shared fields are condition-protected. Timeouts fail; they never open gates or
// supply successful progress evidence. Failed sinks are allowed to drain.
private final class BarrierLedger: @unchecked Sendable {
    private let condition = NSCondition()
    private var state = BarrierState()

    func start(_ id: UInt64) {
        condition.lock(); defer { condition.unlock() }
        if !state.started.insert(id).inserted { state.protocolErrors.append("duplicate factory \(id)") }
        state.threads.insert(currentThreadID())
        if state.holding {
            state.maximumWhileHeld = max(state.maximumWhileHeld, state.started.count)
            if let bound = state.heldBound, state.started.count > bound {
                state.protocolErrors.append("factory \(id) exceeded outstanding bound while sink held")
            }
        }
        condition.broadcast()
    }

    func complete(_ id: UInt64) {
        condition.lock(); defer { condition.unlock() }
        if !state.started.contains(id) { state.protocolErrors.append("completion without factory \(id)") }
        if !state.completed.insert(id).inserted { state.protocolErrors.append("duplicate teardown \(id)") }
        condition.broadcast()
    }

    func open(_ signal: String) {
        condition.lock(); defer { condition.unlock() }
        state.signals.insert(signal); condition.broadcast()
    }

    func snapshot() -> BarrierState {
        condition.lock(); defer { condition.unlock() }
        return state
    }

    func wait(_ label: String, until predicate: (BarrierState) -> Bool) throws {
        let deadline = Date(timeIntervalSinceNow: 10)
        condition.lock(); defer { condition.unlock() }
        while !predicate(state) {
            guard condition.wait(until: deadline) || predicate(state) else {
                throw AdapterError("host handshake timed out: \(label)")
            }
        }
        try require(state.protocolErrors.isEmpty, state.protocolErrors.joined(separator: "; "))
    }

    func beginHold(bound: Int) {
        condition.lock(); defer { condition.unlock() }
        state.holding = true; state.heldBound = bound; state.maximumWhileHeld = state.started.count
        if state.started.count > bound { state.protocolErrors.append("outstanding bound already exceeded at sink entry") }
    }

    func endHold() {
        condition.lock(); defer { condition.unlock() }
        state.holding = false; state.heldBound = nil; condition.broadcast()
    }
}

private func trackedJob(_ id: UInt64, audit: CallbackAudit, ledger: BarrierLedger)
    -> CampaignJob<CounterState, UInt64, CounterOutput> {
    let job = counterJob(id, mode: .pass, audit: audit)
    return CampaignJob(job.generated, metadata: job.metadata, observer: job.observer, tearDown: {
        try job.tearDown()
        ledger.complete(id)
    })
}

private func requireDrained(_ report: CampaignReport, ledger: BarrierLedger) throws -> BarrierState {
    let state = ledger.snapshot()
    try require(state.protocolErrors.isEmpty, state.protocolErrors.joined(separator: "; "))
    try require(report.started == state.started.count && state.completed == state.started,
                "factories and completed teardowns disagree with started jobs")
    try require(report.finished == report.started && report.errors == 0 && report.accountingComplete,
                "campaign returned without draining and accounting every started job")
    return state
}

private func outOfOrderCheck(capacity: Int) throws -> [String: Any] {
    let audit = CallbackAudit(), ledger = BarrierLedger()
    let config = fixtureConfig(firstJob: 0, jobs: 3, workers: 2, resultBuffer: capacity)
    var jobs: [JobReport<UInt64>] = []
    var handshakeError: String?
    var progressedInsideSink = false
    let report = try runCampaign(config: config, factory: { id in
        ledger.start(id); audit.note(id, "factory")
        if id == 0 { try ledger.wait("release first factory", until: { $0.signals.contains("release-first") }) }
        return trackedJob(id, audit: audit, ledger: ledger)
    }, sink: { job in
        audit.note(job.jobID, "sink"); jobs.append(job)
        if jobs.count == 1 {
            // Always free the deliberately blocked factory, including failures.
            defer { ledger.open("release-first") }
            do {
                try require(job.jobID == 1, "sink sorted or delayed the forced out-of-order receipt")
                try ledger.wait("sender claims job 2 before first sink return", until: { $0.started.contains(2) })
                progressedInsideSink = true
            } catch {
                handshakeError = String(describing: error)
                throw error
            }
        }
    })
    let state = try requireDrained(report, ledger: ledger)
    try require(handshakeError == nil && progressedInsideSink, "out-of-order handshake failed: \(handshakeError ?? "missing progress")")
    try require(state.threads.count == 2 && report.delivered == 3 && report.termination == .jobsCompleted,
                "out-of-order run did not use both workers and successfully deliver all jobs")
    return ["config": campaignConfigJSON(config), "jobs": try jobs.map(jobJSON),
            "receipt_order": jobs.map { String($0.jobID) }, "worker_count": String(state.threads.count),
            "sender_progress_before_sink_return": progressedInsideSink,
            "callbacks": canonicalCallbacks(audit.snapshot()), "report": reportJSON(report)]
}

private func slowSinkCheck(capacity: Int) throws -> [String: Any] {
    let audit = CallbackAudit(), ledger = BarrierLedger()
    let config = fixtureConfig(firstJob: 0, jobs: 8, workers: 2, resultBuffer: capacity)
    let bound = capacity + 2 + 1
    var jobs: [JobReport<UInt64>] = []
    var observedMaximum = 0
    var completedAtHold = 0
    var handshakeError: String?
    let report = try runCampaign(config: config, factory: { id in
        ledger.start(id); audit.note(id, "factory")
        if id < 2 { try ledger.wait("both workers admitted", until: { $0.started.count >= 2 }) }
        return trackedJob(id, audit: audit, ledger: ledger)
    }, sink: { job in
        audit.note(job.jobID, "sink"); jobs.append(job)
        if jobs.count == 1 {
            ledger.beginHold(bound: bound)
            defer { ledger.endHold() }
            do {
                // Application teardown precedes the transport send. This is a
                // progress snapshot, not a witness that any sender is blocked.
                try ledger.wait("application teardowns reach expected progress count", until: { $0.completed.count >= bound })
                let held = ledger.snapshot()
                completedAtHold = held.completed.count
                observedMaximum = held.maximumWhileHeld
                try require(held.protocolErrors.isEmpty && held.started.count == bound && held.completed.count == bound,
                            "held sink's claimed/completed snapshot exceeded or failed to fill its bound")
            } catch {
                handshakeError = String(describing: error)
                throw error
            }
        }
    })
    let state = try requireDrained(report, ledger: ledger)
    try require(handshakeError == nil, "slow-sink handshake failed: \(handshakeError ?? "")")
    try require(completedAtHold == bound && observedMaximum == bound,
                "slow-sink did not establish positive progress to its finite bound")
    try require(state.threads.count == 2 && report.delivered == 8 && report.termination == .jobsCompleted,
                "slow-sink run did not complete after sink release")
    return ["config": campaignConfigJSON(config), "receipt_order": jobs.map { String($0.jobID) },
            "worker_count": String(state.threads.count), "outstanding_bound": String(bound),
            "completed_while_first_sink_held": String(completedAtHold),
            "maximum_started_while_first_sink_held": String(observedMaximum),
            "bound_witness": "claimed-and-completed-snapshot-before-first-sink-return",
            "proof_class": "public-application-progress", "blocked_send_observed": false,
            "callbacks": canonicalCallbacks(audit.snapshot()),
            "report": reportJSON(report)]
}

private func sinkDrainCheck(capacity: Int) throws -> [String: Any] {
    let audit = CallbackAudit(), ledger = BarrierLedger()
    let config = fixtureConfig(firstJob: 0, jobs: 12, workers: 3, resultBuffer: capacity)
    var receipts: [UInt64] = []
    let report = try runCampaign(config: config, factory: { id in
        ledger.start(id); audit.note(id, "factory")
        if id < 3 { try ledger.wait("three already-claimed jobs", until: { $0.started.count >= 3 }) }
        return trackedJob(id, audit: audit, ledger: ledger)
    }, sink: { job in
        audit.note(job.jobID, "sink"); receipts.append(job.jobID)
        throw ModelError("first sink error")
    })
    let state = try requireDrained(report, ledger: ledger)
    try require(state.threads.count == 3 && report.started >= 3, "sink failure did not have three already-claimed jobs")
    try require(receipts.count == 1 && report.delivered == 0,
                "failed sink was counted delivered or invoked again during drain")
    try require(report.termination == .callbackError("first sink error"), "drain replaced the first sink error")
    return ["config": campaignConfigJSON(config), "sink_attempts": receipts.map(String.init),
            "sink_calls": String(receipts.count), "worker_count": String(state.threads.count),
            "completed_teardowns": String(state.completed.count),
            "callbacks": canonicalCallbacks(audit.snapshot()), "report": reportJSON(report)]
}

private func cancellationDrainCheck() throws -> [String: Any] {
    let audit = CallbackAudit(), ledger = BarrierLedger()
    let config = fixtureConfig(firstJob: 0, jobs: 12, workers: 2)
    var jobs: [JobReport<UInt64>] = []
    let report = try runCampaign(config: config, limits: CampaignLimits(isCancelled: { audit.isCancelled }),
        factory: { id in
            ledger.start(id); audit.note(id, "factory")
            try ledger.wait("two jobs claimed before cancellation", until: { $0.started.count >= 2 })
            audit.cancel()
            return trackedJob(id, audit: audit, ledger: ledger)
        }, sink: { job in
            audit.note(job.jobID, "sink"); jobs.append(job)
        })
    let state = try requireDrained(report, ledger: ledger)
    try require(state.threads.count == 2 && report.started == 2 && report.delivered == 2,
                "cancellation failed to preserve already-claimed jobs")
    try require(report.requested == 12 && report.notStarted == 10
                && Set(jobs.map(\.jobID)) == state.started && jobs.count == state.started.count,
                "cancellation delivered duplicate or missing claimed jobs, or started later jobs")
    for job in jobs {
        let fuzz = try completedFuzz(job)
        try require(fuzz.termination == "cancelled" && fuzz.cases == 0 && fuzz.transitions == 0,
                    "claimed job \(job.jobID) did not report cancellation before work")
    }
    try require(report.termination == .cancelled && report.reportedCases == 0,
                "factory cancellation skipped preflight or failed to stop scheduling")
    return ["config": campaignConfigJSON(config), "jobs": try jobs.map(jobJSON),
            "worker_count": String(state.threads.count), "callbacks": canonicalCallbacks(audit.snapshot()),
            "report": reportJSON(report)]
}

import Statelessness

enum CounterMode: Equatable, Sendable {
    case pass, random, property, initialError, stepError, checkError, metadataPanic, dropPanic
}

private final class CounterLocal {
    let thread = currentThreadID()
    var steps: UInt64 = 0
    func checkThread() throws {
        try require(currentThreadID() == thread, "worker-local model migrated across threads")
    }
}

// A nonthrowing application lifetime hook models the reference feedback's Drop
// log. Its closures retain it until feedback is released by the public runner.
// Throwing destruction is modeled only by CampaignJob.tearDown, never deinit.
private final class FeedbackLifetime {
    let id: UInt64
    let audit: CallbackAudit
    let thread = currentThreadID()
    init(_ id: UInt64, _ audit: CallbackAudit) { self.id = id; self.audit = audit }
    func checkThread() throws {
        try require(currentThreadID() == thread, "worker-local feedback migrated across threads")
    }
    deinit { audit.note(id, "feedback_drop") }
}

func counterJob(_ id: UInt64, mode: CounterMode, audit: CallbackAudit)
    -> CampaignJob<CounterState, UInt64, CounterOutput> {
    let local = CounterLocal()
    let model = Model<CounterState, UInt64, CounterOutput>(
        initialState: {
            try local.checkThread()
            audit.note(id, "initial")
            local.steps = 0
            if mode == .initialError { throw ModelError("initial error") }
            return CounterState(0)
        },
        step: { before, input in
            try local.checkThread()
            audit.note(id, "step:\(before.value):\(input)", input: input)
            let next = before.value.addingReportingOverflow(1)
            try require(!next.overflow, "counter state overflow")
            local.steps = next.partialValue
            if mode == .stepError { throw ModelError("step error") }
            return Transition(state: CounterState(next.partialValue), outputs: [CounterOutput(input)])
        },
        checkState: { state in
            try local.checkThread()
            audit.note(id, "check_state:\(state.value)")
            if state.value > 0 && mode == .checkError { throw ModelError("check error") }
            return [state.value == 2 && mode == .property
                    ? .failed("n06.counter", "second transition") : .passed("n06.counter"),
                    .skipped("n06.optional", "fixture")]
        },
        cloneState: { CounterState($0.value) }, cloneInput: { $0 },
        cloneOutput: { CounterOutput($0.value) },
        checkTransition: { before, input, _ in
            try local.checkThread()
            audit.note(id, "check_transition:\(before.value):\(input)")
            return [.passed("n06.edge")]
        }
    )
    let generated = Generate(model: model, generate: { state, rng in
        try local.checkThread()
        let value: UInt64
        if mode == .random { value = rng.nextU64() }
        else {
            let next = state.value.addingReportingOverflow(1)
            try require(!next.overflow, "counter input overflow")
            value = next.partialValue
        }
        audit.note(id, "generate:\(state.value):\(value)", input: value)
        return value
    }, isEnabled: { state, input in
        try local.checkThread()
        audit.note(id, "enabled:\(state.value):\(input)")
        return true
    }, equalInputs: { $0 == $1 })
    let observer = SearchObserver<CounterState, UInt64, CounterOutput>(initial: { state, checks in
        try local.checkThread()
        try require(state.value == 0 && checks.count == 2, "initial observer snapshot disagrees with model")
        audit.note(id, "observer_initial")
    }, checkError: { phase, _, complete in
        try local.checkThread()
        audit.note(id, "observer_error:\(phase):\(complete)")
    }, transition: { before, input, after, stateChecks, edgeChecks, history in
        try local.checkThread()
        let next = before.value.addingReportingOverflow(1)
        try require(!next.overflow && after.state.value == local.steps && next.partialValue == after.state.value,
                    "transition observer state disagrees with reducer")
        try require(after.outputs.map(\.value) == [input] && history.last == input,
                    "transition observer input/output snapshot disagrees with reducer")
        try require(stateChecks.count == 2 && edgeChecks.count == 1,
                    "transition observer did not receive both check batches")
        audit.note(id, "observer_transition", input: input)
    })
    return CampaignJob(generated, metadata: {
        try local.checkThread()
        audit.note(id, "metadata")
        if mode == .metadataPanic { throw CampaignPanic("metadata panic") }
        return Metadata(name: "n06-counter:\(id)", build: "n06-counter-v1")
    }, observer: observer, tearDown: {
        try local.checkThread()
        audit.note(id, "model_drop")
        if mode == .dropPanic { throw CampaignPanic("model drop") }
    })
}

func counterFeedback(_ id: UInt64, audit: CallbackAudit, metadataPanic: Bool = false)
    -> Feedback<CounterState, UInt64, CounterOutput, CounterFeature> {
    let lifetime = FeedbackLifetime(id, audit)
    return Feedback(metadata: {
        try lifetime.checkThread()
        audit.note(id, "feedback_metadata")
        if metadataPanic { throw CampaignPanic("feedback metadata panic") }
        return FeedbackMetadata(name: "n06-depth", build: "n06-depth-v1")
    }, cloneFeature: { CounterFeature($0.value) }, initialFeatures: { state, out in
        try lifetime.checkThread()
        audit.note(id, "feedback_initial")
        try out.push(CounterFeature(state.value))
    }, transitionFeatures: { _, _, after, out in
        try lifetime.checkThread()
        audit.note(id, "feedback_transition")
        try out.push(CounterFeature(after.state.value))
    })
}

func canonicalCallbacks(_ snapshot: AuditSnapshot) -> [String] {
    // Rust's campaign runner has no observer parameter. The native observer
    // verifies snapshots separately; it must not invent Rust callback entries.
    snapshot.events.filter { !$0.name.hasPrefix("observer_") }.map { "\($0.jobID):\($0.name)" }
}

func fixtureConfig(masterSeed: UInt64 = 0, firstJob: UInt64 = 7, jobs: Int = 1,
                   workers: Int = 1, resultBuffer: Int = 0, seed: UInt64 = 999,
                   cases: Int = 1, maxSteps: Int = 2, maxTransitions: UInt64 = 2,
                   mutationPercent: Int = 0, stopOnFailure: Bool = false) -> CampaignConfig {
    CampaignConfig(masterSeed: masterSeed, firstJob: firstJob, jobs: jobs, workers: workers,
                   resultBuffer: resultBuffer,
                   fuzz: FuzzConfig(seed: seed, cases: cases, maxSteps: maxSteps,
                                    maxTransitions: maxTransitions, mutationPercent: mutationPercent),
                   stopOnFailure: stopOnFailure)
}

func fixtureGuidance() -> GuidanceConfig {
    GuidanceConfig(maxCorpusEntries: 2, maxCorpusInputs: 3, maxFeatures: 3, maxFeaturesPerObservation: 1)
}

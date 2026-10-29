/// The admission, FIFO retention and mutation policy, independent of feedback identity.
public let guidedAlgorithm = "feature-corpus-v2"

public struct FeedbackMetadata: Equatable, Sendable {
    public let name: String
    public let version: UInt32
    public let build: String
    public init(name: String, version: UInt32 = 1, build: String) {
        self.name = name; self.version = version; self.build = build
    }
    public static func == (a: Self, b: Self) -> Bool {
        exactText(a.name, b.name) && a.version == b.version && exactText(a.build, b.build)
    }
}

private final class FeatureStorage<K> {
    var values: [K] = []
    var failure: Error?
}

/// A synchronous append-only lease, revoked when its callback returns or throws.
/// Every attempted emission, including duplicates, spends the observation limit.
/// Keys are copied at push time. The clone must isolate mutable reference storage.
/// This type is intentionally not Sendable; never retain or use it concurrently.
public final class FeatureBuffer<K> {
    private var owner: FeatureStorage<K>?
    private let maximum: Int
    private var clone: ((K) throws -> K)?
    private var pushing = false
    private init(_ owner: FeatureStorage<K>, maximum: Int, clone: @escaping (K) throws -> K) {
        self.owner = owner; self.maximum = maximum; self.clone = clone
    }
    public func push(_ feature: K) throws {
        guard let owner, let clone else { throw ModelError("feature buffer lease expired") }
        if let failure = owner.failure { throw failure }
        guard !pushing else {
            let error = ModelError("feature buffer push is reentrant")
            owner.failure = error; throw error
        }
        guard owner.values.count < maximum else {
            let error = ModelError("feedback exceeds per-observation feature limit")
            owner.failure = error; throw error
        }
        pushing = true
        defer { pushing = false }
        // No array mutation/exclusive access may span application clone code.
        // A nested push poisons this observation even if the clone swallows it.
        let value: K
        do { value = try copied(clone, feature) }
        catch { if owner.failure == nil { owner.failure = error }; throw error }
        if let failure = owner.failure { throw failure }
        owner.values.append(value)
    }
    fileprivate static func collect(_ scratch: inout [K], maximum: Int, clone: @escaping (K) throws -> K,
                                    _ callback: (FeatureBuffer<K>) throws -> Void) throws {
        let storage = FeatureStorage<K>()
        swap(&scratch, &storage.values)
        storage.values.removeAll(keepingCapacity: true)
        let lease = FeatureBuffer(storage, maximum: maximum, clone: clone)
        defer { lease.owner = nil; lease.clone = nil; swap(&scratch, &storage.values) }
        // The callback's own error takes precedence over an earlier ignored push error.
        try callback(lease)
        if let failure = storage.failure { throw failure }
    }
}

/// Feature identity is K's lawful, stable Hashable equality, never model equality.
/// Hash collisions are resolved with ==. Emission order must be deterministic.
/// All closures are synchronous and throwing, confined to the calling actor/thread.
public struct Feedback<S, I, O, K: Hashable> {
    public var metadata: () throws -> FeedbackMetadata
    public var cloneFeature: (K) throws -> K
    public var initialFeatures: (S, FeatureBuffer<K>) throws -> Void
    public var transitionFeatures: (S, I, Transition<S, O>, FeatureBuffer<K>) throws -> Void
    public init(metadata: @escaping () throws -> FeedbackMetadata,
                cloneFeature: @escaping (K) throws -> K,
                initialFeatures: @escaping (S, FeatureBuffer<K>) throws -> Void = { _, _ in },
                transitionFeatures: @escaping (S, I, Transition<S, O>, FeatureBuffer<K>) throws -> Void) {
        self.metadata = metadata; self.cloneFeature = cloneFeature
        self.initialFeatures = initialFeatures; self.transitionFeatures = transitionFeatures
    }
}

/// Projects initial and successor states, without changing checking or replay equality.
public struct StateFeedback<S, K: Hashable> {
    public let metadata: () throws -> FeedbackMetadata
    public let cloneFeature: (K) throws -> K
    public let project: (S) throws -> K
    public init(metadata: @escaping () throws -> FeedbackMetadata,
                cloneFeature: @escaping (K) throws -> K, project: @escaping (S) throws -> K) {
        self.metadata = metadata; self.cloneFeature = cloneFeature; self.project = project
    }
    public func feedback<I, O>() -> Feedback<S, I, O, K> {
        Feedback(metadata: metadata, cloneFeature: cloneFeature,
                 initialFeatures: { state, out in try out.push(self.project(state)) },
                 transitionFeatures: { _, _, after, out in try out.push(self.project(after.state)) })
    }
}

/// Full-state novelty (including any represented oracle history). State's Hashable
/// conformance defines exactness; Model.equalStates is not consulted. cloneState
/// must faithfully copy every field and isolate mutable references.
public func exactStateFeedback<S: Hashable, I, O>(_ model: Model<S, I, O>) -> Feedback<S, I, O, S> {
    StateFeedback(metadata: { FeedbackMetadata(name: "exact-state", build: "clone-eq-hash-v1") },
                  cloneFeature: model.cloneState, project: { $0 }).feedback()
}

public struct GuidanceConfig: Equatable, Sendable {
    public let maxCorpusEntries: Int
    public let maxCorpusInputs: Int
    public let maxFeatures: Int
    public let maxFeaturesPerObservation: Int
    public init(maxCorpusEntries: Int = 256, maxCorpusInputs: Int = 25_600,
                maxFeatures: Int = 100_000, maxFeaturesPerObservation: Int = 256) {
        self.maxCorpusEntries = maxCorpusEntries; self.maxCorpusInputs = maxCorpusInputs
        self.maxFeatures = maxFeatures; self.maxFeaturesPerObservation = maxFeaturesPerObservation
    }
    public func validate() throws {
        guard maxCorpusEntries > 0, maxCorpusInputs > 0, maxFeatures > 0, maxFeaturesPerObservation > 0 else {
            throw ConfigError("guidance limits must be positive")
        }
    }
}
public struct CorpusEntry<I> {
    public let inputs: [I]
    /// Zero when recovering a previously tracked but unwitnessed discovery.
    public let newFeatures: Int
    public let discoveredCase: Int
    public let discoveredTransition: UInt64
}
extension CorpusEntry: Equatable where I: Equatable {}
extension CorpusEntry: Sendable where I: Sendable {}
public struct GuidanceReport<I> {
    public let algorithm: String
    public let config: GuidanceConfig
    public let feedback: FeedbackMetadata
    public internal(set) var uniqueFeatures: Int = 0
    public internal(set) var corpusInputs: Int = 0
    public internal(set) var corpus: [CorpusEntry<I>] = []
    public internal(set) var mutationCases: Int = 0
    public internal(set) var droppedPrefixes: UInt64 = 0
    public internal(set) var evictedPrefixes: UInt64 = 0
    public internal(set) var featureLimitReached = false
    public internal(set) var corpusLimitReached = false
}
extension GuidanceReport: Equatable where I: Equatable {}
extension GuidanceReport: Sendable where I: Sendable {}

private final class GuidedDriver<S, I, O, K: Hashable> {
    let model: Model<S, I, O>
    let feedback: Feedback<S, I, O, K>
    var report: GuidanceReport<I>
    // Eviction deliberately retains witnessed=true; overlong discovery uses false.
    var seen: [K: Bool] = [:]
    var scratch: [K] = []
    init(model: Model<S, I, O>, feedback: Feedback<S, I, O, K>, config: GuidanceConfig,
         metadata: FeedbackMetadata) {
        self.model = model; self.feedback = feedback
        report = GuidanceReport(algorithm: guidedAlgorithm, config: config, feedback: metadata)
    }
    func learn(_ inputs: [I], caseNumber: Int, transition: UInt64) throws {
        var novel = 0, recovered = false
        let retainable = inputs.count <= report.config.maxCorpusInputs
        defer { scratch.removeAll(keepingCapacity: true) }
        for key in scratch {
            if let witnessed = seen[key] {
                if retainable && !witnessed { seen[key] = true; recovered = true }
            } else if seen.count == report.config.maxFeatures {
                report.featureLimitReached = true
            } else { seen[key] = retainable; novel += 1 }
        }
        report.uniqueFeatures = seen.count
        guard (novel > 0 || recovered) && !inputs.isEmpty else { return }
        guard retainable else {
            report.corpusLimitReached = true; report.droppedPrefixes += 1; return
        }
        let allowance = report.config.maxCorpusInputs - inputs.count
        while report.corpus.count >= report.config.maxCorpusEntries || report.corpusInputs > allowance {
            report.corpusInputs -= report.corpus.removeFirst().inputs.count
            report.evictedPrefixes += 1; report.corpusLimitReached = true
        }
        let snapshot = try inputs.map { try copied(model.cloneInput, $0) }
        report.corpusInputs += snapshot.count
        report.corpus.append(CorpusEntry(inputs: snapshot, newFeatures: novel,
                                        discoveredCase: caseNumber, discoveredTransition: transition))
    }
    func initial(_ state: S, caseNumber: Int) throws {
        try FeatureBuffer.collect(&scratch, maximum: report.config.maxFeaturesPerObservation, clone: feedback.cloneFeature) {
            try feedback.initialFeatures(copied(model.cloneState, state), $0)
        }
        try learn([], caseNumber: caseNumber, transition: 0)
    }
    func transition(_ before: S, input: I, after: Transition<S, O>, inputs: [I], caseNumber: Int, transition: UInt64) throws {
        try FeatureBuffer.collect(&scratch, maximum: report.config.maxFeaturesPerObservation, clone: feedback.cloneFeature) {
            try feedback.transitionFeatures(copied(model.cloneState, before), copied(model.cloneInput, input), cloneTransition(model, after), $0)
        }
        try learn(inputs, caseNumber: caseNumber, transition: transition)
    }
    func candidate(_ rng: inout Rng, mutationPercent: Int) throws -> ([I], Int?) {
        guard !report.corpus.isEmpty, rng.index(100)! < UInt64(mutationPercent) else { return ([], nil) }
        report.mutationCases += 1
        let index = Int(rng.index(UInt64(report.corpus.count))!)
        var candidate = try report.corpus[index].inputs.map { try copied(model.cloneInput, $0) }
        switch rng.index(3)! {
        case 0: return (candidate, nil)
        case 1:
            let start = Int(rng.index(UInt64(candidate.count))!)
            let length = 1 + Int(rng.index(UInt64(candidate.count - start))!)
            candidate.removeSubrange(start..<(start + length))
            return (candidate, Int(rng.index(UInt64(candidate.count) + 1)!))
        default: return (candidate, Int(rng.index(UInt64(candidate.count))!))
        }
    }
}

/// Guided, observed and cooperatively controlled search share the ordinary fuzz
/// kernel. Bounds count retained keys/inputs/emissions, not bytes or process RSS.
/// Callback errors throw without returning a partial report. Each run starts fresh.
public func guidedFuzz<S, I, O, K: Hashable>(_ generated: Generate<S, I, O>,
    config: FuzzConfig = FuzzConfig(), guidance: GuidanceConfig = GuidanceConfig(),
    feedback: Feedback<S, I, O, K>, limits: RunLimits = RunLimits(),
    observer: SearchObserver<S, I, O>? = nil) throws -> FuzzReport<I> {
    try guidance.validate()
    guard (0...100).contains(config.mutationPercent) else { throw ConfigError("mutation_percent must be in 0..=100") }
    try validateFuzzConfig(config)
    let control = try RunControl(limits)
    let metadata: FeedbackMetadata
    do { metadata = try feedback.metadata() }
    catch let panic as CampaignPanic { throw panic }
    catch { throw ModelError("feedback metadata: \(error)") }
    let driver = GuidedDriver(model: generated.model, feedback: feedback, config: guidance, metadata: metadata)
    var result = try fuzzKernel(generated, config: config, control: control, observer: observer,
        candidate: { _, rng in try driver.candidate(&rng, mutationPercent: config.mutationPercent) },
        initialFeedback: { state, c in try driver.initial(state, caseNumber: c) },
        transitionFeedback: { before, input, after, inputs, c, t in
            try driver.transition(before, input: input, after: after, inputs: inputs, caseNumber: c, transition: t)
        })
    result.guidance = driver.report
    return result
}

public func guidedFuzz<S, I, O, K: Hashable>(_ generated: Generate<S, I, O>,
    config: FuzzConfig = FuzzConfig(), guidance: GuidanceConfig = GuidanceConfig(),
    feedback: StateFeedback<S, K>, limits: RunLimits = RunLimits(),
    observer: SearchObserver<S, I, O>? = nil) throws -> FuzzReport<I> {
    try guidedFuzz(generated, config: config, guidance: guidance, feedback: feedback.feedback(), limits: limits, observer: observer)
}

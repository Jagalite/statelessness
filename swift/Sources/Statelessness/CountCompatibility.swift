// Source-compatible Int conveniences. All execution uses the UInt64 kernels.
// Conversion is exact and throws; a count is never rounded, clamped, or truncated.
import Foundation

public struct FuzzConfig: Sendable {
    public let seed: UInt64
    public let cases: Int
    public let maxSteps: Int
    public let maxTransitions: UInt64
    public let mutationPercent: Int
    public init(seed: UInt64 = 0, cases: Int = 1_000, maxSteps: Int = 100, maxTransitions: UInt64 = 100_000, mutationPercent: Int = 50) {
        self.seed = seed; self.cases = cases; self.maxSteps = maxSteps; self.maxTransitions = maxTransitions; self.mutationPercent = mutationPercent
    }
}
public struct FuzzReport<I> {
    public var termination: String
    public let seed: UInt64
    public var cases: Int
    public var transitions: UInt64
    public var skippedChecks: UInt64
    public var failure: Failure<I>?
    public var guidance: GuidanceReport<I>? = nil
}
extension FuzzReport: Sendable where I: Sendable {}
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

public struct CampaignConfig: Sendable {
    public let masterSeed: UInt64
    public let firstJob: UInt64
    public let jobs: Int
    public let workers: Int
    public let resultBuffer: Int
    public let fuzz: FuzzConfig
    public let stopOnFailure: Bool
    public init(masterSeed: UInt64 = 0, firstJob: UInt64 = 0, jobs: Int = 16,
                workers: Int = 1, resultBuffer: Int = 1, fuzz: FuzzConfig = FuzzConfig(),
                stopOnFailure: Bool = false) {
        self.masterSeed = masterSeed; self.firstJob = firstJob; self.jobs = jobs
        self.workers = workers; self.resultBuffer = resultBuffer; self.fuzz = fuzz
        self.stopOnFailure = stopOnFailure
    }
    public func validate() throws {
        guard jobs > 0 else { throw ModelError("campaign jobs must be positive") }
        guard (1...256).contains(workers) else { throw ModelError("campaign workers must be in 1..=256") }
        guard (0...1024).contains(resultBuffer) else { throw ModelError("campaign result_buffer must be in 0..=1024") }
        guard (0...100).contains(fuzz.mutationPercent) else { throw ModelError("mutation_percent must be in 0..=100") }
        let n = UInt64(jobs)
        guard !firstJob.addingReportingOverflow(n - 1).overflow else { throw ModelError("campaign job ID range overflows u64") }
        guard fuzz.cases >= 0 else { throw ModelError("campaign per-job cases must be nonnegative") }
        guard fuzz.maxSteps >= 0 else { throw ModelError("campaign per-job steps must be nonnegative") }
        let cases = UInt64(fuzz.cases), steps = UInt64(fuzz.maxSteps)
        guard !n.multipliedReportingOverflow(by: cases).overflow else { throw ModelError("campaign aggregate cases overflow u64") }
        // A product wider than u64 necessarily exceeds the u64 transition cap.
        let product = cases.multipliedReportingOverflow(by: steps)
        let transitions = product.overflow ? fuzz.maxTransitions : min(product.partialValue, fuzz.maxTransitions)
        guard !n.multipliedReportingOverflow(by: transitions).overflow else { throw ModelError("campaign aggregate transitions overflow u64") }
    }
}

public enum JobOutcome<I> {
    case completed(FuzzReport<I>)
    case factoryError(ModelError)
    case modelError(ModelError)
    case panicked(String)
}
public struct JobReport<I> {
    public let jobID: UInt64
    public let seed: UInt64
    public let config: FuzzConfig
    public let metadata: Metadata?
    public let outcome: JobOutcome<I>
}

public struct CampaignReport: Equatable, Sendable {
    public internal(set) var requested: Int
    public internal(set) var started = 0
    public internal(set) var finished = 0
    public internal(set) var notStarted: Int
    public internal(set) var delivered = 0
    public internal(set) var failures = 0
    public internal(set) var errors = 0
    public internal(set) var reportedTransitions: UInt64 = 0
    public internal(set) var reportedCases: UInt64 = 0
    public internal(set) var reportedSkippedChecks: UInt64 = 0
    public internal(set) var accountingComplete = true
    public internal(set) var termination = CampaignTermination.jobsCompleted
    init(requested: Int) { self.requested = requested; self.notStarted = requested }
}

// Only immutable/value-safe application inputs cross the channel. The engine
// never asserts Sendable for models, generators, feedback, or feature keys.
extension JobOutcome: Sendable where I: Sendable {}
extension JobReport: Sendable where I: Sendable {}


func saturatingCountAdd(_ a: UInt64, _ b: UInt64) -> UInt64 {
    let sum = a.addingReportingOverflow(b)
    return sum.overflow ? .max : sum.partialValue
}
func nativeCount(_ count: UInt64) throws -> Int {
    guard let result = Int(exactly: count) else { throw ConfigError("count exceeds Int; use the UInt64 API") }
    return result
}
func unsignedCount(_ count: Int) throws -> UInt64 {
    guard let result = UInt64(exactly: count) else { throw ConfigError("count must be nonnegative") }
    return result
}

extension FuzzConfig {
    public func exact64() throws -> FuzzConfig64 {
        guard cases >= 0, maxSteps >= 0, (0...100).contains(mutationPercent) else { throw ConfigError("invalid fuzz configuration") }
        return FuzzConfig64(seed: seed, cases: UInt64(cases), maxSteps: UInt64(maxSteps), maxTransitions: maxTransitions, mutationPercent: mutationPercent)
    }
}
extension FuzzConfig64 {
    public func checkedInt() throws -> FuzzConfig {
        FuzzConfig(seed: seed, cases: try nativeCount(cases), maxSteps: try nativeCount(maxSteps), maxTransitions: maxTransitions, mutationPercent: mutationPercent)
    }
}
extension GuidanceConfig {
    public func exact64() throws -> GuidanceConfig64 {
        try validate()
        return GuidanceConfig64(maxCorpusEntries: UInt64(maxCorpusEntries), maxCorpusInputs: UInt64(maxCorpusInputs), maxFeatures: UInt64(maxFeatures), maxFeaturesPerObservation: UInt64(maxFeaturesPerObservation))
    }
}
extension GuidanceConfig64 {
    public func checkedInt() throws -> GuidanceConfig {
        GuidanceConfig(maxCorpusEntries: try nativeCount(maxCorpusEntries), maxCorpusInputs: try nativeCount(maxCorpusInputs), maxFeatures: try nativeCount(maxFeatures), maxFeaturesPerObservation: try nativeCount(maxFeaturesPerObservation))
    }
}
extension GuidanceReport64 {
    public func checkedInt() throws -> GuidanceReport<I> {
        GuidanceReport(algorithm: algorithm, config: try config.checkedInt(), feedback: feedback,
            uniqueFeatures: try nativeCount(uniqueFeatures), corpusInputs: try nativeCount(corpusInputs),
            corpus: try corpus.map { CorpusEntry(inputs: $0.inputs, newFeatures: try nativeCount($0.newFeatures), discoveredCase: try nativeCount($0.discoveredCase), discoveredTransition: $0.discoveredTransition) },
            mutationCases: try nativeCount(mutationCases), droppedPrefixes: droppedPrefixes, evictedPrefixes: evictedPrefixes,
            featureLimitReached: featureLimitReached, corpusLimitReached: corpusLimitReached)
    }
}
extension GuidanceReport {
    func exact64() throws -> GuidanceReport64<I> {
        GuidanceReport64(algorithm: algorithm, config: try config.exact64(), feedback: feedback,
            uniqueFeatures: try unsignedCount(uniqueFeatures), corpusInputs: try unsignedCount(corpusInputs),
            corpus: try corpus.map { CorpusEntry64(inputs: $0.inputs, newFeatures: try unsignedCount($0.newFeatures), discoveredCase: try unsignedCount($0.discoveredCase), discoveredTransition: $0.discoveredTransition) },
            mutationCases: try unsignedCount(mutationCases), droppedPrefixes: droppedPrefixes, evictedPrefixes: evictedPrefixes,
            featureLimitReached: featureLimitReached, corpusLimitReached: corpusLimitReached)
    }
}
extension FuzzReport64 {
    public func checkedInt() throws -> FuzzReport<I> {
        FuzzReport(termination: termination, seed: seed, cases: try nativeCount(cases), transitions: transitions, skippedChecks: skippedChecks, failure: failure, guidance: try guidance?.checkedInt())
    }
}
extension FuzzReport {
    func exact64() throws -> FuzzReport64<I> {
        FuzzReport64(termination: termination, seed: seed, cases: try unsignedCount(cases), transitions: transitions, skippedChecks: skippedChecks, failure: failure, guidance: try guidance?.exact64())
    }
}
extension CampaignConfig {
    public func exact64() throws -> CampaignConfig64 {
        // Keep legacy admission order, including mutation before signed count guards.
        try validate()
        return CampaignConfig64(masterSeed: masterSeed, firstJob: firstJob, jobs: UInt64(jobs), workers: UInt64(workers), resultBuffer: UInt64(resultBuffer), fuzz: try fuzz.exact64(), stopOnFailure: stopOnFailure)
    }
}
extension CampaignConfig64 {
    public func checkedInt() throws -> CampaignConfig {
        CampaignConfig(masterSeed: masterSeed, firstJob: firstJob, jobs: try nativeCount(jobs), workers: try nativeCount(workers), resultBuffer: try nativeCount(resultBuffer), fuzz: try fuzz.checkedInt(), stopOnFailure: stopOnFailure)
    }
}
extension CampaignReport64 {
    public func checkedInt() throws -> CampaignReport {
        var r = CampaignReport(requested: try nativeCount(requested))
        r.started = try nativeCount(started); r.finished = try nativeCount(finished); r.notStarted = try nativeCount(notStarted)
        r.delivered = try nativeCount(delivered); r.failures = try nativeCount(failures); r.errors = try nativeCount(errors)
        r.reportedTransitions = reportedTransitions; r.reportedCases = reportedCases; r.reportedSkippedChecks = reportedSkippedChecks
        r.accountingComplete = accountingComplete; r.termination = termination
        return r
    }
}
extension JobReport64 {
    public func checkedInt() throws -> JobReport<I> {
        let result: JobOutcome<I>
        switch outcome {
        case let .completed(report): result = .completed(try report.checkedInt())
        case let .factoryError(error): result = .factoryError(error)
        case let .modelError(error): result = .modelError(error)
        case let .panicked(message): result = .panicked(message)
        }
        return JobReport(jobID: jobID, seed: seed, config: try config.checkedInt(), metadata: metadata, outcome: result)
    }
}

public func fuzz<S, I, O>(_ g: Generate<S, I, O>, config: FuzzConfig = FuzzConfig(), limits: RunLimits = RunLimits(), observer: SearchObserver<S, I, O>? = nil) throws -> FuzzReport<I> {
    try fuzz64(g, config: config.exact64(), limits: limits, observer: observer).checkedInt()
}
public func guidedFuzz<S, I, O, K: Hashable>(_ g: Generate<S, I, O>, config: FuzzConfig = FuzzConfig(), guidance: GuidanceConfig = GuidanceConfig(), feedback: Feedback<S, I, O, K>, limits: RunLimits = RunLimits(), observer: SearchObserver<S, I, O>? = nil) throws -> FuzzReport<I> {
    try guidance.validate()
    guard (0...100).contains(config.mutationPercent) else { throw ConfigError("mutation_percent must be in 0..=100") }
    return try guidedFuzz64(g, config: config.exact64(), guidance: guidance.exact64(), feedback: feedback, limits: limits, observer: observer).checkedInt()
}
public func guidedFuzz<S, I, O, K: Hashable>(_ g: Generate<S, I, O>, config: FuzzConfig = FuzzConfig(), guidance: GuidanceConfig = GuidanceConfig(), feedback: StateFeedback<S, K>, limits: RunLimits = RunLimits(), observer: SearchObserver<S, I, O>? = nil) throws -> FuzzReport<I> {
    try guidedFuzz(g, config: config, guidance: guidance, feedback: feedback.feedback(), limits: limits, observer: observer)
}
public func runCampaign<S, I: Sendable, O>(config: CampaignConfig = CampaignConfig(), limits: CampaignLimits = CampaignLimits(), factory: @escaping @Sendable (UInt64) throws -> CampaignJob<S, I, O>, sink: (JobReport<I>) throws -> Void) throws -> CampaignReport {
    try runCampaign64(config: config.exact64(), limits: limits, factory: factory, sink: { try sink($0.checkedInt()) }).checkedInt()
}
public func runGuidedCampaign<S, I: Sendable, O, K: Hashable>(config: CampaignConfig = CampaignConfig(), limits: CampaignLimits = CampaignLimits(), guidance: GuidanceConfig = GuidanceConfig(), factory: @escaping @Sendable (UInt64) throws -> CampaignJob<S, I, O>, feedbackFactory: @escaping @Sendable (UInt64, Generate<S, I, O>) throws -> Feedback<S, I, O, K>, sink: (JobReport<I>) throws -> Void) throws -> CampaignReport {
    try guidance.validate()
    return try runGuidedCampaign64(config: config.exact64(), limits: limits, guidance: guidance.exact64(), factory: factory, feedbackFactory: feedbackFactory, sink: { try sink($0.checkedInt()) }).checkedInt()
}
func runCampaignUsing<S, I: Sendable, O>(config: CampaignConfig, limits: CampaignLimits, factory: @escaping @Sendable (UInt64) throws -> CampaignJob<S, I, O>, sink: (JobReport<I>) throws -> Void, hooks: CampaignWorkerHooks = CampaignWorkerHooks(), runner: @escaping @Sendable (UInt64, CampaignJob<S, I, O>, FuzzConfig, RunLimits) throws -> FuzzReport<I>) throws -> CampaignReport {
    try runCampaignUsing64(config: config.exact64(), limits: limits, factory: factory, sink: { try sink($0.checkedInt()) }, hooks: hooks,
        runner: { id, job, config, limits in try runner(id, job, config.checkedInt(), limits).exact64() }).checkedInt()
}

public struct AutoOptions: Sendable {
    public let maxCandidates: Int
    public init(maxCandidates: Int = 100_000) { self.maxCandidates = maxCandidates }
}
public struct Auto<S, I, O> {
    public let model: Model<S, I, O>
    public let options: AutoOptions
    public let equalInputs: (I, I) throws -> Bool
    public init(_ model: Model<S, I, O>, options: AutoOptions = AutoOptions(), equalInputs: @escaping (I, I) throws -> Bool) throws {
        guard options.maxCandidates > 0 else { throw ConfigError("automatic generation max_candidates must be positive") }
        self.model = model; self.options = options; self.equalInputs = equalInputs
    }
    public var generated: Generate<S, I, O> {
        Generate(model: model, generate: { state, rng in try sampleEnumerated(self.model, state: state, rng: &rng, options: self.options) },
                 isEnabled: { state, input in try containsEnumerated(self.model, state: state, input: input, equalInputs: self.equalInputs, options: self.options) }, equalInputs: equalInputs)
    }
}
public struct ShrinkConfig: Sendable {
    public let maxAttempts: Int
    /// Includes validation of the original failure.
    public init(maxAttempts: Int = 10_000) { self.maxAttempts = maxAttempts }
}
public enum ShrinkOperation: Equatable, Sendable {
    case validateOriginal
    case remove(start: Int, end: Int)
    case simplify(index: Int, alternative: Int)
}
public struct ShrinkAttempt: Equatable, Sendable {
    public let attempt: Int
    public let candidateLength: Int
    public let operation: ShrinkOperation
    public let outcome: ShrinkOutcome
}
public struct ShrinkReport<I> {
    public let original: Failure<I>
    public var minimized: Failure<I>
    public var attempts: Int
    /// search_complete means only this deletion/simplification search finished.
    public var termination: String
    public var history: [ShrinkAttempt]
    public var replayedTransitions: UInt64
    public let validatedOriginal: Bool
}

extension AutoOptions {
    public func exact64() throws -> AutoOptions64 {
        guard maxCandidates > 0 else { throw ConfigError("automatic generation max_candidates must be positive") }
        return AutoOptions64(maxCandidates: UInt64(maxCandidates))
    }
}
extension AutoOptions64 {
    public func checkedInt() throws -> AutoOptions { AutoOptions(maxCandidates: try nativeCount(maxCandidates)) }
}
public func sampleEnumerated<S, I, O>(_ model: Model<S, I, O>, state: S, rng: inout Rng, options: AutoOptions = AutoOptions()) throws -> I? {
    try sampleEnumerated64(model, state: state, rng: &rng, options: options.exact64())
}
public func containsEnumerated<S, I, O>(_ model: Model<S, I, O>, state: S, input: I, equalInputs: (I, I) throws -> Bool, options: AutoOptions = AutoOptions()) throws -> Bool {
    try containsEnumerated64(model, state: state, input: input, equalInputs: equalInputs, options: options.exact64())
}
extension ShrinkConfig {
    public func exact64() throws -> ShrinkConfig64 {
        guard maxAttempts > 0 else { throw ConfigError("shrink max_attempts must be greater than zero") }
        return ShrinkConfig64(maxAttempts: UInt64(maxAttempts))
    }
}
extension ShrinkConfig64 {
    public func checkedInt() throws -> ShrinkConfig { ShrinkConfig(maxAttempts: try nativeCount(maxAttempts)) }
}
extension ShrinkOperation64 {
    public func checkedInt() throws -> ShrinkOperation {
        switch self {
        case .validateOriginal: return .validateOriginal
        case let .remove(start, end): return .remove(start: try nativeCount(start), end: try nativeCount(end))
        case let .simplify(index, alternative): return .simplify(index: try nativeCount(index), alternative: try nativeCount(alternative))
        }
    }
}
extension ShrinkReport64 {
    public func checkedInt() throws -> ShrinkReport<I> {
        ShrinkReport(original: original, minimized: minimized, attempts: try nativeCount(attempts), termination: termination,
            history: try history.map { ShrinkAttempt(attempt: try nativeCount($0.attempt), candidateLength: try nativeCount($0.candidateLength), operation: try $0.operation.checkedInt(), outcome: $0.outcome) },
            replayedTransitions: replayedTransitions, validatedOriginal: validatedOriginal)
    }
}
public func shrink<S, I, O>(_ g: Generate<S, I, O>, original: Failure<I>, config: ShrinkConfig = ShrinkConfig(), limits: ShrinkLimits = ShrinkLimits()) throws -> ShrinkReport<I> {
    try shrink64(g, original: original, config: config.exact64(), limits: limits).checkedInt()
}

// Counts are checked at actual collection growth, never from advertised budgets.
func requireNativeGrowth(_ count: Int, elementStride: Int = 1, context: String) throws {
    guard count >= 0, elementStride >= 0, count < Int.max / max(1, elementStride) else {
        throw ModelError("\(context) exceeds native collection capacity")
    }
}

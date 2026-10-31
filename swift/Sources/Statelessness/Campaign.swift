import Foundation

/// Independent jobs. Per-job seeds replace `fuzz.seed`; zero fuzz budgets retain
/// the ordinary search meanings. Counts bound retained work, not process memory.
public struct CampaignConfig64: Sendable {
    public let masterSeed: UInt64
    public let firstJob: UInt64
    public let jobs: UInt64
    public let workers: UInt64
    public let resultBuffer: UInt64
    public let fuzz: FuzzConfig64
    public let stopOnFailure: Bool
    public init(masterSeed: UInt64 = 0, firstJob: UInt64 = 0, jobs: UInt64 = 16,
                workers: UInt64 = 1, resultBuffer: UInt64 = 1, fuzz: FuzzConfig64 = FuzzConfig64(),
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
        let cases = UInt64(fuzz.cases), steps = UInt64(fuzz.maxSteps)
        guard !n.multipliedReportingOverflow(by: cases).overflow else { throw ModelError("campaign aggregate cases overflow u64") }
        // A product wider than u64 necessarily exceeds the u64 transition cap.
        let product = cases.multipliedReportingOverflow(by: steps)
        let transitions = product.overflow ? fuzz.maxTransitions : min(product.partialValue, fuzz.maxTransitions)
        guard !n.multipliedReportingOverflow(by: transitions).overflow else { throw ModelError("campaign aggregate transitions overflow u64") }
    }
}

public func jobSeed(masterSeed: UInt64, jobID: UInt64) -> UInt64 {
    var rng = Rng(seed: masterSeed &+ jobID)
    return rng.nextU64()
}

/// Cancellation is sampled on multiple workers. The closure must be safe for
/// simultaneous calls. Unlike bare RunLimits this enforces a Sendable callback.
public struct CampaignLimits: Sendable {
    public let maxDuration: TimeInterval?
    public let maxDurationNanoseconds: DurationNanoseconds?
    public let isCancelled: @Sendable () -> Bool
    public let cancellationEnabled: Bool
    public init(maxDuration: TimeInterval? = nil, isCancelled: (@Sendable () -> Bool)? = nil) {
        self.maxDuration = maxDuration; self.maxDurationNanoseconds = nil; self.cancellationEnabled = isCancelled != nil; self.isCancelled = isCancelled ?? { false }
    }
    public init(maxDurationNanoseconds: DurationNanoseconds, isCancelled: (@Sendable () -> Bool)? = nil) {
        self.maxDuration = nil; self.maxDurationNanoseconds = maxDurationNanoseconds; self.cancellationEnabled = isCancelled != nil; self.isCancelled = isCancelled ?? { false }
    }
}

/// A fresh worker-local model, with metadata independent of any optional codec.
/// Neither this value nor its model/observer/feedback closures cross workers.
/// `tearDown` runs once after metadata/search, before completion and delivery,
/// including when those callbacks throw. Its error is a recoverable cleanup
/// failure, represented by Panicked; Swift fatal traps/deinit failures are not.
public struct CampaignJob<S, I, O> {
    public let generated: Generate<S, I, O>
    public let metadata: () throws -> Metadata
    public let observer: SearchObserver<S, I, O>?
    public let tearDown: () throws -> Void
    public init(_ generated: Generate<S, I, O>, metadata: @escaping () throws -> Metadata,
                observer: SearchObserver<S, I, O>? = nil, tearDown: @escaping () throws -> Void = {}) {
        self.generated = generated; self.metadata = metadata
        self.observer = observer; self.tearDown = tearDown
    }
}

/// An explicit, catchable host failure at a campaign boundary. This does not
/// catch fatalError, precondition traps, Objective-C exceptions or process death.
/// Existing fuzz/guided callback wrappers normalize thrown errors to ModelError;
/// a marker thrown inside those wrappers therefore remains a model error.
public struct CampaignPanic: Error, Sendable, CustomStringConvertible {
    public let description: String
    public init(_ description: String) { self.description = description }
}

public enum JobOutcome64<I> {
    case completed(FuzzReport64<I>)
    case factoryError(ModelError)
    case modelError(ModelError)
    case panicked(String)
}
public struct JobReport64<I> {
    public let jobID: UInt64
    public let seed: UInt64
    public let config: FuzzConfig64
    public let metadata: Metadata?
    public let outcome: JobOutcome64<I>
}

public enum CampaignTermination: Equatable, Sendable {
    case jobsCompleted, failureFound, cancelled, deadline
    case callbackError(String), workerError(String)
    public static func == (a: Self, b: Self) -> Bool {
        switch (a, b) {
        case (.jobsCompleted, .jobsCompleted), (.failureFound, .failureFound),
             (.cancelled, .cancelled), (.deadline, .deadline): return true
        case let (.callbackError(x), .callbackError(y)), let (.workerError(x), .workerError(y)): return exactText(x, y)
        default: return false
        }
    }
}
public struct CampaignReport64: Equatable, Sendable {
    public internal(set) var requested: UInt64
    public internal(set) var started: UInt64 = 0
    public internal(set) var finished: UInt64 = 0
    public internal(set) var notStarted: UInt64
    public internal(set) var delivered: UInt64 = 0
    public internal(set) var failures: UInt64 = 0
    public internal(set) var errors: UInt64 = 0
    public internal(set) var reportedTransitions: UInt64 = 0
    public internal(set) var reportedCases: UInt64 = 0
    public internal(set) var reportedSkippedChecks: UInt64 = 0
    public internal(set) var accountingComplete = true
    public internal(set) var termination = CampaignTermination.jobsCompleted
    init(requested: UInt64) { self.requested = requested; self.notStarted = requested }
}

// Only immutable/value-safe application inputs cross the channel. The engine
// never asserts Sendable for models, generators, feedback, or feature keys.
extension JobOutcome64: Sendable where I: Sendable {}
extension JobReport64: Sendable where I: Sendable {}

/// Serial sinks run on the caller. Workers=1 also constructs/runs each model on
/// that caller; configured workers>1 always uses spawned workers, even for jobs=1.
/// Reports arrive in channel receipt order. A sink error stops new sink calls;
/// queued/active jobs are drained and counted, and workers joined before return.
public func runCampaign64<S, I: Sendable, O>(config: CampaignConfig64 = CampaignConfig64(),
    limits: CampaignLimits = CampaignLimits(),
    factory: @escaping @Sendable (UInt64) throws -> CampaignJob<S, I, O>,
    sink: (JobReport64<I>) throws -> Void) throws -> CampaignReport64 {
    try runCampaignUsing64(config: config, limits: limits, factory: factory, sink: sink) { _, job, fuzzConfig, remaining in
        try fuzz64(job.generated, config: fuzzConfig, limits: remaining, observer: job.observer)
    }
}

/// Each claimed job constructs fresh feedback and corpus on its own worker.
/// Feedback factory errors are ModelError with model metadata already present.
public func runGuidedCampaign64<S, I: Sendable, O, K: Hashable>(
    config: CampaignConfig64 = CampaignConfig64(), limits: CampaignLimits = CampaignLimits(),
    guidance: GuidanceConfig64 = GuidanceConfig64(),
    factory: @escaping @Sendable (UInt64) throws -> CampaignJob<S, I, O>,
    feedbackFactory: @escaping @Sendable (UInt64, Generate<S, I, O>) throws -> Feedback<S, I, O, K>,
    sink: (JobReport64<I>) throws -> Void) throws -> CampaignReport64 {
    try guidance.validate()
    return try runCampaignUsing64(config: config, limits: limits, factory: factory, sink: sink) { id, job, fuzzConfig, remaining in
        let control = try RunControl(remaining)
        let feedback = try feedbackFactory(id, job.generated)
        return try guidedFuzz64(job.generated, config: fuzzConfig, guidance: guidance, feedback: feedback,
                              limits: control.remainingLimits(), observer: job.observer)
    }
}

/// Campaign variant whose jobs use finite-domain automatic generation and poll
/// each job's campaign control between domain iterator calls. The job's model
/// and input equality are used; its custom generation callbacks are not used.
public func runAutoCampaign64<S, I: Sendable, O>(config: CampaignConfig64 = CampaignConfig64(), limits: CampaignLimits = CampaignLimits(), options: AutoOptions64 = AutoOptions64(), factory: @escaping @Sendable (UInt64) throws -> CampaignJob<S, I, O>, sink: (JobReport64<I>) throws -> Void) throws -> CampaignReport64 {
    guard options.maxCandidates > 0 else { throw ConfigError("automatic generation max_candidates must be positive") }
    return try runCampaignUsing64(config: config, limits: limits, factory: factory, sink: sink) { _, job, fuzzConfig, remaining in
        try fuzzAuto64(job.generated.model, options: options, equalInputs: job.generated.equalInputs, config: fuzzConfig, limits: remaining, observer: job.observer)
    }
}

/// Guided automatic campaign. Work performed inside feedbackFactory remains a
/// user callback and cannot be preempted by scan-level control.
public func runGuidedAutoCampaign64<S, I: Sendable, O, K: Hashable>(config: CampaignConfig64 = CampaignConfig64(), limits: CampaignLimits = CampaignLimits(), options: AutoOptions64 = AutoOptions64(), guidance: GuidanceConfig64 = GuidanceConfig64(), factory: @escaping @Sendable (UInt64) throws -> CampaignJob<S, I, O>, feedbackFactory: @escaping @Sendable (UInt64, Generate<S, I, O>) throws -> Feedback<S, I, O, K>, sink: (JobReport64<I>) throws -> Void) throws -> CampaignReport64 {
    try guidance.validate()
    guard options.maxCandidates > 0 else { throw ConfigError("automatic generation max_candidates must be positive") }
    return try runCampaignUsing64(config: config, limits: limits, factory: factory, sink: sink) { id, job, fuzzConfig, remaining in
        let control = try RunControl(remaining)
        let feedback = try feedbackFactory(id, job.generated)
        return try guidedFuzzAuto64(job.generated.model, options: options, equalInputs: job.generated.equalInputs, config: fuzzConfig, guidance: guidance, feedback: feedback, limits: control.remainingLimits(), observer: job.observer)
    }
}

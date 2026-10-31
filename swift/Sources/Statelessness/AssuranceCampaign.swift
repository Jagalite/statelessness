import Foundation

public struct AssuredCampaign64 {
    public let campaign: CampaignReport64
    public let assurance: MergedAssuranceReport
}

// Campaign workers own their observers. Only finished value reports cross this lock.
final class AssuranceCampaignReports<S,I,O>: @unchecked Sendable {
    let manifest: AssuranceManifest
    let metadata: Metadata
    let observationLimits: AssuranceObservationLimits
    let coverageFactory: @Sendable (UInt64,Generate<S,I,O>) throws -> (S,I,Transition<S,O>,[I],inout AssuranceHits) throws -> Void
    private let lock = NSLock()
    private var reports: [UInt64:AssuranceReport] = [:]
    private var retainedBytes: UInt64 = 0
    private var sizes: [UInt64:UInt64] = [:]
    private var exhausted = false
    private let maximumBytes: UInt64
    init(manifest: AssuranceManifest,metadata: Metadata,observationLimits: AssuranceObservationLimits,maximumBytes: UInt64,coverageFactory: @escaping @Sendable (UInt64,Generate<S,I,O>) throws -> (S,I,Transition<S,O>,[I],inout AssuranceHits) throws -> Void) {
        self.manifest = manifest; self.metadata = metadata; self.coverageFactory = coverageFactory; self.observationLimits = observationLimits; self.maximumBytes = maximumBytes
    }
    // Deterministic logical byte accounting; this is not a process RSS limit.
    private func cost(_ report: AssuranceReport,within limit: UInt64) -> UInt64? {
        var used: UInt64 = 0
        func add(_ amount: UInt64) -> Bool { guard amount <= limit-used else { return false }; used += amount; return true }
        func text(_ value: String) -> Bool { add(UInt64(value.utf8.count)) }
        guard add(128),text(report.manifest.model),text(report.manifest.provenance.manifest),text(report.manifest.provenance.model),text(report.manifest.provenance.properties),text(report.metadata.name),text(report.metadata.build),text(report.termination) else { return nil }
        for p in report.manifest.properties { guard add(16),text(p.id.value) else { return nil } }
        for f in report.manifest.families { guard add(16),text(f.name) else { return nil }; if case let .excluded(reason) = f.coverage, !text(reason) { return nil } }
        for g in report.manifest.goals { guard add(16),text(g.id) else { return nil } }
        for (k,v) in report.manifest.bounds { guard add(16),text(k),text(v) else { return nil } }
        for (k,v) in report.budgets { guard add(16),text(k),text(v) else { return nil } }
        for (k,v) in report.nativeBudgets { guard add(16),text(k),text(v) else { return nil } }
        for (p,_) in report.observedProperties { guard add(48),text(p.id.value) else { return nil } }
        for name in report.observedFamilies { guard add(16),text(name.value) else { return nil } }
        for name in report.observedGoals { guard add(16),text(name.value) else { return nil } }
        for error in report.callbackErrors { guard add(16),text(error) else { return nil } }
        return used
    }
    private func putLocked(_ id: UInt64,_ report: AssuranceReport) {
        guard reports[id] == nil,let size = cost(report,within:maximumBytes-retainedBytes) else { exhausted = true; return }
        retainedBytes += size; sizes[id] = size; reports[id] = report
    }
    func put(_ id: UInt64,_ report: AssuranceReport) { lock.lock(); defer { lock.unlock() }; putLocked(id,report) }
    func snapshot() -> ([(UInt64,AssuranceReport)],UInt64,Bool) { lock.lock(); defer { lock.unlock() }; return (reports.map { ($0.key,$0.value) },retainedBytes,exhausted) }
    func fail(_ id: UInt64,_ error: Error) {
        lock.lock(); defer { lock.unlock() }
        guard let r = reports[id] else { return }
        let replacement = AssuranceReport(manifest:r.manifest,metadata:r.metadata,outcome:.failed,termination:"ModelError",budgets:r.budgets,nativeBudgets:r.nativeBudgets,checkedTransitions:r.checkedTransitions,unclassifiedTransitions:r.unclassifiedTransitions,observedProperties:r.observedProperties,observedFamilies:r.observedFamilies,observedGoals:r.observedGoals,callbackErrors:r.callbackErrors + ["job teardown: \(error)"])
        guard let previous = sizes[id] else { exhausted = true; return }
        let otherBytes = retainedBytes-previous
        guard let replacementBytes = cost(replacement,within:maximumBytes-otherBytes) else { exhausted = true; return }
        retainedBytes = otherBytes + replacementBytes; sizes[id] = replacementBytes; reports[id] = replacement
    }
}
/// Executes independent worker-local observations and preserves completed job
/// evidence even when delivery later fails. Factory, metadata and coverage
/// failures without an observer remain explicitly missing jobs. The retained
/// job count is bounded independently of worker and result-buffer limits.
public func assuredCampaign64<S,I:Sendable,O>(config: CampaignConfig64 = CampaignConfig64(),limits: CampaignLimits = CampaignLimits(),
    manifest: AssuranceManifest,metadata: Metadata,
    factory: @escaping @Sendable (UInt64) throws -> CampaignJob<S,I,O>,
    coverageFactory: @escaping @Sendable (UInt64,Generate<S,I,O>) throws -> (S,I,Transition<S,O>,[I],inout AssuranceHits) throws -> Void,
    maxRetainedJobs: UInt64 = 100_000,maxRetainedEvidenceBytes: UInt64 = 256*1024*1024,observationLimits: AssuranceObservationLimits = AssuranceObservationLimits(),
    sink: (JobReport64<I>) throws -> Void) throws -> AssuredCampaign64 {
    try assuredCampaignUsing64(config:config,limits:limits,manifest:manifest,metadata:metadata,factory:factory,coverageFactory:coverageFactory,maxRetainedJobs:maxRetainedJobs,maxRetainedEvidenceBytes:maxRetainedEvidenceBytes,observationLimits:observationLimits,sink:sink) { _,job,fuzz,remaining,observer,_ in
        try fuzz64(job.generated,config:fuzz,limits:remaining,observer:observer)
    }
}

/// Builds fresh feedback on each worker and retains its actual identity and
/// partial observation evidence even if feedback or result delivery fails.
public func assuredGuidedCampaign64<S,I:Sendable,O,K:Hashable>(config: CampaignConfig64 = CampaignConfig64(),limits: CampaignLimits = CampaignLimits(),
    guidance: GuidanceConfig64 = GuidanceConfig64(),manifest: AssuranceManifest,metadata: Metadata,
    factory: @escaping @Sendable (UInt64) throws -> CampaignJob<S,I,O>,
    feedbackFactory: @escaping @Sendable (UInt64,Generate<S,I,O>) throws -> Feedback<S,I,O,K>,
    coverageFactory: @escaping @Sendable (UInt64,Generate<S,I,O>) throws -> (S,I,Transition<S,O>,[I],inout AssuranceHits) throws -> Void,
    maxRetainedJobs: UInt64 = 100_000,maxRetainedEvidenceBytes: UInt64 = 256*1024*1024,observationLimits: AssuranceObservationLimits = AssuranceObservationLimits(),
    sink: (JobReport64<I>) throws -> Void) throws -> AssuredCampaign64 {
    try guidance.validate()
    return try assuredCampaignUsing64(config:config,limits:limits,manifest:manifest,metadata:metadata,factory:factory,coverageFactory:coverageFactory,maxRetainedJobs:maxRetainedJobs,maxRetainedEvidenceBytes:maxRetainedEvidenceBytes,observationLimits:observationLimits,sink:sink) { id,job,fuzz,remaining,observer,budgets in
        try assuredGuidedCampaignJob(id:id,job:job,fuzz:fuzz,remaining:remaining,observer:observer,budgets:budgets,guidance:guidance,options:nil,feedbackFactory:feedbackFactory)
    }
}

/// Automatic campaign variant with per-job cooperative domain scanning.
public func assuredAutoCampaign64<S,I:Sendable,O>(config: CampaignConfig64 = CampaignConfig64(), limits: CampaignLimits = CampaignLimits(), options: AutoOptions64 = AutoOptions64(), manifest: AssuranceManifest, metadata: Metadata, factory: @escaping @Sendable (UInt64) throws -> CampaignJob<S,I,O>, coverageFactory: @escaping @Sendable (UInt64,Generate<S,I,O>) throws -> (S,I,Transition<S,O>,[I],inout AssuranceHits) throws -> Void, maxRetainedJobs: UInt64 = 100_000, maxRetainedEvidenceBytes: UInt64 = 256*1024*1024, observationLimits: AssuranceObservationLimits = AssuranceObservationLimits(), sink: (JobReport64<I>) throws -> Void) throws -> AssuredCampaign64 {
    guard options.maxCandidates > 0 else { throw ConfigError("automatic generation max_candidates must be positive") }
    return try assuredCampaignUsing64(config:config,limits:limits,manifest:manifest,metadata:metadata,factory:factory,coverageFactory:coverageFactory,maxRetainedJobs:maxRetainedJobs,maxRetainedEvidenceBytes:maxRetainedEvidenceBytes,observationLimits:observationLimits,sink:sink) { _,job,fuzz,remaining,observer,budgets in
        budgets.native["auto.max_candidates"] = String(options.maxCandidates)
        return try fuzzAuto64(job.generated.model,options:options,equalInputs:job.generated.equalInputs,config:fuzz,limits:remaining,observer:observer)
    }
}

/// Guided automatic campaign; feedback factory work remains a caller callback.
public func assuredGuidedAutoCampaign64<S,I:Sendable,O,K:Hashable>(config: CampaignConfig64 = CampaignConfig64(), limits: CampaignLimits = CampaignLimits(), options: AutoOptions64 = AutoOptions64(), guidance: GuidanceConfig64 = GuidanceConfig64(), manifest: AssuranceManifest, metadata: Metadata, factory: @escaping @Sendable (UInt64) throws -> CampaignJob<S,I,O>, feedbackFactory: @escaping @Sendable (UInt64,Generate<S,I,O>) throws -> Feedback<S,I,O,K>, coverageFactory: @escaping @Sendable (UInt64,Generate<S,I,O>) throws -> (S,I,Transition<S,O>,[I],inout AssuranceHits) throws -> Void, maxRetainedJobs: UInt64 = 100_000, maxRetainedEvidenceBytes: UInt64 = 256*1024*1024, observationLimits: AssuranceObservationLimits = AssuranceObservationLimits(), sink: (JobReport64<I>) throws -> Void) throws -> AssuredCampaign64 {
    try guidance.validate()
    guard options.maxCandidates > 0 else { throw ConfigError("automatic generation max_candidates must be positive") }
    return try assuredCampaignUsing64(config:config,limits:limits,manifest:manifest,metadata:metadata,factory:factory,coverageFactory:coverageFactory,maxRetainedJobs:maxRetainedJobs,maxRetainedEvidenceBytes:maxRetainedEvidenceBytes,observationLimits:observationLimits,sink:sink) { id,job,fuzz,remaining,observer,budgets in
        try assuredGuidedCampaignJob(id:id,job:job,fuzz:fuzz,remaining:remaining,observer:observer,budgets:budgets,guidance:guidance,options:options,feedbackFactory:feedbackFactory)
    }
}

/// Worker-local budget accumulator passed by reference to campaign runners, so
/// runner closures neither take inout parameters nor mutate captured state.
final class AssuranceJobBudgets {
    var budgets: [String:String]
    var native: [String:String] = [:]
    init(_ budgets: [String:String]) { self.budgets = budgets }
}
/// Holds the feedback identity requested by the guided engine; replaces a
/// mutable captured variable.
private final class AssuranceFeedbackIdentity { var value: FeedbackMetadata? }

/// Guided (optionally automatic) per-job body as a plain generic function.
/// The per-job `max_duration_ns` budget was already recorded once from the
/// limits handed to the job, as Rust records `fuzz_budgets(&config, limits)`.
private func assuredGuidedCampaignJob<S,I,O,K:Hashable>(id: UInt64,job: CampaignJob<S,I,O>,fuzz: FuzzConfig64,remaining: RunLimits,observer: SearchObserver<S,I,O>,budgets: AssuranceJobBudgets,guidance: GuidanceConfig64,options: AutoOptions64?,feedbackFactory: (UInt64,Generate<S,I,O>) throws -> Feedback<S,I,O,K>) throws -> FuzzReport64<I> {
    if let options { budgets.native["auto.max_candidates"] = String(options.maxCandidates) }
    budgets.budgets["guidance.algorithm"] = guidedAlgorithm
    budgets.budgets["guidance.max_corpus_entries"] = String(guidance.maxCorpusEntries)
    budgets.budgets["guidance.max_corpus_inputs"] = String(guidance.maxCorpusInputs)
    budgets.budgets["guidance.max_features"] = String(guidance.maxFeatures)
    budgets.budgets["guidance.max_features_per_observation"] = String(guidance.maxFeaturesPerObservation)
    // Feedback construction time counts against the job's remaining budget.
    let control = try RunControl(remaining)
    let feedback = try feedbackFactory(id,job.generated)
    let identity = AssuranceFeedbackIdentity()
    var instrumented = feedback
    instrumented.metadata = { let value = try feedback.metadata(); identity.value = value; return value }
    let searchLimits = control.remainingLimits(), guided = instrumented
    let result: Result<FuzzReport64<I>,Error>
    if let options { result = Result { try guidedFuzzAuto64(job.generated.model,options:options,equalInputs:job.generated.equalInputs,config:fuzz,guidance:guidance,feedback:guided,limits:searchLimits,observer:observer) } }
    else { result = Result { try guidedFuzz64(job.generated,config:fuzz,guidance:guidance,feedback:guided,limits:searchLimits,observer:observer) } }
    if let actual = identity.value { budgets.budgets["guidance.feedback.name"] = actual.name; budgets.budgets["guidance.feedback.version"] = String(actual.version); budgets.budgets["guidance.feedback.build"] = actual.build }
    return try result.get()
}

private func assuredCampaignUsing64<S,I:Sendable,O>(config: CampaignConfig64,limits: CampaignLimits,
    manifest: AssuranceManifest,metadata: Metadata,
    factory: @escaping @Sendable (UInt64) throws -> CampaignJob<S,I,O>,
    coverageFactory: @escaping @Sendable (UInt64,Generate<S,I,O>) throws -> (S,I,Transition<S,O>,[I],inout AssuranceHits) throws -> Void,
    maxRetainedJobs: UInt64,maxRetainedEvidenceBytes: UInt64,
    observationLimits: AssuranceObservationLimits,
    sink: (JobReport64<I>) throws -> Void,
    run: @escaping @Sendable (UInt64,CampaignJob<S,I,O>,FuzzConfig64,RunLimits,SearchObserver<S,I,O>,AssuranceJobBudgets) throws -> FuzzReport64<I>) throws -> AssuredCampaign64 {
    try config.validate()
    guard exactText(manifest.model,metadata.name) else { throw ModelError("campaign model identity mismatch") }
    guard config.jobs <= UInt64(Int.max) else { throw ModelError("campaign assurance job count is not representable") }
    guard config.jobs <= maxRetainedJobs else { throw ModelError("campaign assurance retained job limit exceeded") }
    // Validate before starting workers, so malformed limits cannot become a
    // collection of misleading missing-job reports.
    _ = try AssuranceObserver<S,I,O>(manifest:manifest,metadata:metadata,limits:observationLimits) { _,_,_,_,_ in }
    let box = AssuranceCampaignReports(manifest:manifest,metadata:metadata,observationLimits:observationLimits,maximumBytes:maxRetainedEvidenceBytes,coverageFactory:coverageFactory)
    let identities = AssuranceCampaignIdentities()
    let campaign = try runCampaignUsing64(config:config,limits:limits,factory:{ id in try assuranceIdentifiedJob(id:id,factory:factory,identities:identities,box:box) },sink:sink) { id,job,fuzz,remaining in
        try assuredCampaignJob(id:id,job:job,fuzz:fuzz,remaining:remaining,box:box,identities:identities,run:run)
    }
    let expected = (0..<config.jobs).map { config.firstJob + $0 }
    let (reports,retainedBytes,evidenceExhausted) = box.snapshot()
    let merged = try mergeAssuranceReports(manifest:manifest,metadata:metadata,expectedJobs:expected,reports:reports)
    let r = merged.report
    // Rust run_campaign: merged budgets, then campaign configuration and
    // control budgets, then campaign accounting.
    var budgets = r.budgets, native = r.nativeBudgets
    budgets["campaign.master_seed"] = String(config.masterSeed); budgets["campaign.first_job"] = String(config.firstJob)
    budgets["campaign.workers"] = String(config.workers); budgets["campaign.result_buffer"] = String(config.resultBuffer)
    budgets["campaign.stop_on_failure"] = String(config.stopOnFailure)
    assuranceControlBudgets(limits.maxDuration,cancellationEnabled:limits.cancellationEnabled,into:&budgets,native:&native)
    if let duration = limits.maxDurationNanoseconds { budgets["max_duration_ns"] = duration.decimal }
    budgets["campaign.started"] = String(campaign.started)
    budgets["campaign.finished"] = String(campaign.finished); budgets["campaign.delivered"] = String(campaign.delivered)
    budgets["campaign.accounting_complete"] = String(campaign.accountingComplete)
    native["campaign.max_retained_jobs"] = String(maxRetainedJobs)
    native["campaign.max_retained_evidence_bytes"] = String(maxRetainedEvidenceBytes)
    native["campaign.retained_evidence_bytes"] = String(retainedBytes)
    assuranceObservationBudgets(observationLimits,into:&native)
    var errors = r.callbackErrors,outcome = r.outcome
    let termination: String
    switch campaign.termination {
    case .jobsCompleted: termination = "Campaign::JobsCompleted"
    case .failureFound: termination = "Campaign::FailureFound"; outcome = .failed
    case .cancelled: termination = "Campaign::Cancelled"; if outcome != .failed { outcome = .interrupted }
    case .deadline: termination = "Campaign::Deadline"; if outcome != .failed { outcome = .timedOut }
    case let .callbackError(error):
        termination = "Campaign::CallbackError(ModelError(\(assuranceRustDebug(error))))"; outcome = .failed
        errors.append("campaign completion/delivery error: \(error)")
    case let .workerError(error):
        termination = "Campaign::WorkerError(ModelError(\(assuranceRustDebug(error))))"; outcome = .failed
        errors.append("campaign completion/delivery error: \(error)")
    }
    if evidenceExhausted { errors.append("campaign retained evidence limit exceeded; some job evidence was not retained") }
    if !campaign.accountingComplete { errors.append("campaign accounting is incomplete") }
    if campaign.delivered != campaign.requested { errors.append("not every requested campaign report was delivered successfully") }
    let aggregate = AssuranceReport(manifest:r.manifest,metadata:r.metadata,outcome:outcome,termination:termination,budgets:budgets,nativeBudgets:native,checkedTransitions:r.checkedTransitions,unclassifiedTransitions:r.unclassifiedTransitions,observedProperties:r.observedProperties,observedFamilies:r.observedFamilies,observedGoals:r.observedGoals,callbackErrors:Array(Set(errors)).sorted())
    return AssuredCampaign64(campaign:campaign,assurance:MergedAssuranceReport(report:aggregate,jobs:merged.jobs,missingJobs:merged.missingJobs))
}
private final class AssuranceCampaignIdentities: @unchecked Sendable {
    private let lock = NSLock()
    private var values: [UInt64:Metadata] = [:]
    func put(_ id: UInt64,_ value: Metadata) { lock.lock(); defer { lock.unlock() }; values[id] = value }
    func get(_ id: UInt64) -> Metadata? { lock.lock(); defer { lock.unlock() }; return values[id] }
}

/// Wraps the caller factory so the job's actual metadata is recorded and
/// teardown failures replace the retained job report. Plain generic function,
/// called from the campaign's @Sendable factory closure.
private func assuranceIdentifiedJob<S,I,O>(id: UInt64,factory: (UInt64) throws -> CampaignJob<S,I,O>,identities: AssuranceCampaignIdentities,box: AssuranceCampaignReports<S,I,O>) throws -> CampaignJob<S,I,O> {
    let job = try factory(id)
    let metadata = job.metadata, tearDown = job.tearDown
    return CampaignJob(job.generated,metadata: { let value = try metadata(); identities.put(id,value); return value },observer:job.observer,tearDown: {
        do { try tearDown() } catch { box.fail(id,error); throw error }
    })
}
/// Combined observer: assurance observation first, then the job's existing
/// observer, each receiving independent snapshots.
private func assuranceCombinedObserver<S,I,O>(_ ours: SearchObserver<S,I,O>,_ existing: SearchObserver<S,I,O>?,_ model: Model<S,I,O>) -> SearchObserver<S,I,O> {
    SearchObserver<S,I,O>(initial: { state,checks in
        try ours.initial(copied(model.cloneState,state),checks)
        if let existing { try existing.initial(copied(model.cloneState,state),checks) }
    },checkError: { phase,checks,complete in
        try ours.checkError(phase,checks,complete); try existing?.checkError(phase,checks,complete)
    },transition: { before,input,turn,stateChecks,edgeChecks,history in
        func deliver(_ target: SearchObserver<S,I,O>) throws {
            try target.transition(copied(model.cloneState,before),copied(model.cloneInput,input),cloneTransition(model,turn),stateChecks,edgeChecks,history.map { try copied(model.cloneInput,$0) })
        }
        try deliver(ours); if let existing { try deliver(existing) }
    },needsHistory: { ours.needsHistory() || (existing?.needsHistory() ?? false) })
}
/// Per-job assurance body as a plain generic function (no mutable captures or
/// inout-mutating defer inside the campaign's @Sendable runner). Rust records
/// `fuzz_budgets(&config, limits)` with the limits handed to the job, i.e. the
/// campaign's remaining budget at job start (src/assurance.rs run_campaign).
private func assuredCampaignJob<S,I:Sendable,O>(id: UInt64,job: CampaignJob<S,I,O>,fuzz: FuzzConfig64,remaining: RunLimits,box: AssuranceCampaignReports<S,I,O>,identities: AssuranceCampaignIdentities,run: (UInt64,CampaignJob<S,I,O>,FuzzConfig64,RunLimits,SearchObserver<S,I,O>,AssuranceJobBudgets) throws -> FuzzReport64<I>) throws -> FuzzReport64<I> {
    guard let actual = identities.get(id),actual == box.metadata else { throw ModelError("campaign assurance executable identity mismatch") }
    let observer = try AssuranceObserver(manifest:box.manifest,metadata:actual,limits:box.observationLimits,coverage:box.coverageFactory(id,job.generated))
    let combined = assuranceCombinedObserver(observer.observer(),job.observer,job.generated.model)
    let budgets = AssuranceJobBudgets(["seed":String(fuzz.seed),"cases":String(fuzz.cases),"max_steps":String(fuzz.maxSteps),"max_transitions":String(fuzz.maxTransitions),"mutation_percent":String(fuzz.mutationPercent)])
    var rustBudgets = budgets.budgets, nativeBudgets = budgets.native
    assuranceControlBudgets(remaining,into:&rustBudgets,native:&nativeBudgets)
    assuranceObservationBudgets(box.observationLimits,into:&nativeBudgets)
    budgets.budgets = rustBudgets; budgets.native = nativeBudgets
    let result = Result { try run(id,job,fuzz,remaining,combined,budgets) }
    switch result {
    case let .success(report): box.put(id,try observer.finish(termination:report.termination,budgets:budgets.budgets,nativeBudgets:budgets.native))
    case let .failure(error): box.put(id,try observer.finish(outcome:.failed,termination:"ModelError",budgets:budgets.budgets,nativeBudgets:budgets.native,error:error))
    }
    return try result.get()
}

public struct ShrinkConfig64: Sendable {
    public let maxAttempts: UInt64
    /// Includes validation of the original failure.
    public init(maxAttempts: UInt64 = 10_000) { self.maxAttempts = maxAttempts }
}
public struct ShrinkLimits {
    public let control: RunLimits
    public let maxReplayedTransitions: UInt64?
    /// Swift-only extension (no Rust counterpart): bounds yielded
    /// simplification hints across the whole run, including duplicate hints
    /// that do not consume a replay attempt, ending with
    /// `simplification_limit`. Nil (the default) matches Rust exactly.
    public let maxSimplificationCandidates: UInt64?
    public init(control: RunLimits = RunLimits(), maxReplayedTransitions: UInt64? = nil,maxSimplificationCandidates: UInt64? = nil) { self.control = control; self.maxReplayedTransitions = maxReplayedTransitions; self.maxSimplificationCandidates = maxSimplificationCandidates }
}
public enum ShrinkOperation64: Equatable, Sendable {
    case validateOriginal
    case remove(start: UInt64, end: UInt64)
    case simplify(index: UInt64, alternative: UInt64)
}
public enum ShrinkOutcome: String, Sendable {
    case retained, invalidCausality = "invalid_causality", differentFailure = "different_failure"
    case noFailure = "no_failure", alreadySeen = "already_seen", resourceLimit = "resource_limit"
}
public struct ShrinkAttempt64: Equatable, Sendable {
    public let attempt: UInt64
    public let candidateLength: UInt64
    public let operation: ShrinkOperation64
    public let outcome: ShrinkOutcome
}
public struct ShrinkReport64<I> {
    public let original: Failure<I>
    public var minimized: Failure<I>
    public var attempts: UInt64
    /// search_complete means only this deletion/simplification search finished.
    public var termination: String
    public var history: [ShrinkAttempt64]
    public var replayedTransitions: UInt64
    public var simplificationCandidates: UInt64 = 0
    public let validatedOriginal: Bool
}
private final class ShrinkWork {
    let checkStorage = SearchCheckStorage()
    let control: RunControl
    let maximum: UInt64?
    let maxAttempts: UInt64
    let maxSimplificationCandidates: UInt64?
    var transitions: UInt64 = 0
    init(config: ShrinkConfig64, limits: ShrinkLimits) throws { control = try RunControl(limits.control); maximum = limits.maxReplayedTransitions; maxAttempts = config.maxAttempts; maxSimplificationCandidates = limits.maxSimplificationCandidates }
}
private enum ShrinkCandidate<I> {
    case pass, invalid, failure(Failure<I>), stopped(String)
}
private func clonedFailure<S, I, O>(_ m: Model<S, I, O>, _ failure: Failure<I>) throws -> Failure<I> {
    Failure(inputs: try failure.inputs.map { try copied(m.cloneInput, $0) }, violations: failure.violations)
}
private func shrinkHasTarget<I>(_ failure: Failure<I>, _ target: Violation) -> Bool {
    failure.violations.contains { exactText($0.phase, target.phase) && exactText($0.check.id, target.check.id) }
}
private func seenSequence<S, I, O>(_ g: Generate<S, I, O>, seen: [[I]], inputs: [I], control: RunControl) throws -> Bool {
    for prior in seen where prior.count == inputs.count {
        var equal = true
        for i in prior.indices {
            if control.stop() != nil { return false }
            let same = try call("input_equal") { try g.equalInputs(copied(g.model.cloneInput, prior[i]), copied(g.model.cloneInput, inputs[i])) }
            if control.stop() != nil { return false }
            if !same { equal = false; break }
        }
        if equal { return true }
    }
    return false
}
private func replayCandidate<S, I, O>(_ g: Generate<S, I, O>, inputs: [I], work: ShrinkWork) throws -> ShrinkCandidate<I> {
    if let stop = work.control.stop() { return .stopped(stop) }
    let m = g.model
    // Snapshot the replayable prefix before initial/check callbacks can alter
    // later inputs through aliases. Preserve limit and earlier-error priority by
    // retaining copy failures until their position is actually reached.
    let budget = work.maximum.map { $0 > work.transitions ? $0 - work.transitions : 0 }
    let count = budget.map { Int(min(UInt64(inputs.count), $0)) } ?? inputs.count
    var candidates: [Result<I, Error>] = []
    for input in inputs.prefix(count) {
        try requireNativeGrowth(candidates.count, elementStride: MemoryLayout<Result<I, Error>>.stride, context: "shrink replay candidates")
        candidates.append(Result { try copied(m.cloneInput, input) })
    }
    let initialState = try searchInitial(m)
    // Checks can invoke application code which holds an alias to the live
    // state. Retain the continuation before those callbacks, but do not let
    // an unused copy error preempt a finding, stop, or transition limit.
    let checkedInitial = Result { try copied(m.cloneState, initialState) }
    var continuation = Result { try copied(m.cloneState, initialState) }
    var skipped: UInt64 = 0
    var retainedInputs: [Result<I, Error>] = []
    let initial = try searchInitialChecks(m, state: checkedInitial.get(), skipped: &skipped, storage: work.checkStorage)
    if !initial.isEmpty { return .failure(Failure(inputs: [], violations: initial)) }
    if let stop = work.control.stop() { return .stopped(stop) }
    for index in inputs.indices {
        if let stop = work.control.stop() { return .stopped(stop) }
        if let maximum = work.maximum, work.transitions >= maximum { return .stopped("transition_limit") }
        let state = try continuation.get()
        let retained = try candidates[index].get()
        let stepInput = Result { try copied(m.cloneInput, retained) }
        let checkInput = Result { try copied(m.cloneInput, retained) }
        let witnessInput = Result { try copied(m.cloneInput, retained) }
        let enabled = try call("shrink is_enabled") {
            let query = try copied(m.cloneInput, retained)
            return try g.isEnabled(copied(m.cloneState, state), query)
        }
        if let stop = work.control.stop() { return .stopped(stop) }
        if !enabled { return .invalid }
        let t = try perform(m, state, stepInput.get())
        // Retain check observations before the continuation copy hook can
        // mutate application aliases. Report failures only at their stage.
        let checkedState = Result { try copied(m.cloneState, t.state) }
        let checkedTransition = Result { try cloneTransition(m, t) }
        let checkedBefore = Result { try copied(m.cloneState, state) }
        let next = Result { try copied(m.cloneState, t.state) }
        try requireNativeGrowth(retainedInputs.count, elementStride: MemoryLayout<Result<I, Error>>.stride, context: "shrink replay history")
        retainedInputs.append(witnessInput)
        let (count, overflow) = work.transitions.addingReportingOverflow(1)
        guard !overflow else { throw ModelError("replayed transition count overflow") }; work.transitions = count
        let bad = try searchEdge(m, state: state, input: retained, transition: t, skipped: &skipped, storage: work.checkStorage, capturedTransition: checkedTransition, capturedState: checkedState, capturedInput: checkInput, capturedBefore: checkedBefore)
        if !bad.isEmpty { return .failure(Failure(inputs: try retainedInputs.map { try $0.get() }, violations: bad)) }
        if let stop = work.control.stop() { return .stopped(stop) }
        continuation = next
    }
    return .pass
}
private func shrinkAttempt<S, I, O>(_ g: Generate<S, I, O>, target: Violation, candidate: [I], operation: ShrinkOperation64,
                                    report: inout ShrinkReport64<I>, seen: inout [[I]], work: ShrinkWork) throws -> Bool {
    if report.attempts == work.maxAttempts { report.termination = "attempt_limit"; return false }
    report.attempts += 1
    let replayed = try replayCandidate(g, inputs: candidate, work: work)
    report.replayedTransitions = work.transitions
    var stopped = false
    let outcome: ShrinkOutcome
    switch replayed {
    case let .stopped(reason): report.termination = reason; stopped = true; outcome = .resourceLimit
    case .invalid: outcome = .invalidCausality
    case .pass: outcome = .noFailure
    case let .failure(failure):
        if !shrinkHasTarget(failure, target) { outcome = .differentFailure }
        else {
            let known = try seenSequence(g, seen: seen, inputs: failure.inputs, control: work.control)
            if let stop = work.control.stop() { report.termination = stop; stopped = true; outcome = .resourceLimit }
            else if known { outcome = .alreadySeen }
            else {
                try requireNativeGrowth(seen.count, elementStride: MemoryLayout<[I]>.stride, context: "shrink seen storage")
                seen.append(try failure.inputs.map { try copied(g.model.cloneInput, $0) })
                report.minimized = failure; outcome = .retained
            }
        }
    }
    try requireNativeGrowth(report.history.count, elementStride: MemoryLayout<ShrinkAttempt64>.stride, context: "shrink history")
    report.history.append(ShrinkAttempt64(attempt: report.attempts, candidateLength: UInt64(candidate.count), operation: operation, outcome: outcome))
    return !stopped
}
/// Causal replay preserves the original first violation's exact ID and phase.
/// Chunk deletion precedes lazy simplification; accepted-prefix history bounds
/// cyclic hints. Reports retain supplied evidence if validation is interrupted.
public func shrink64<S, I, O>(_ g: Generate<S, I, O>, original: Failure<I>, config: ShrinkConfig64 = ShrinkConfig64(), limits: ShrinkLimits = ShrinkLimits()) throws -> ShrinkReport64<I> {
    guard config.maxAttempts > 0 else { throw ConfigError("shrink max_attempts must be greater than zero") }
    guard let target = original.violations.first, target.check.status == .failed else { throw ModelError("cannot shrink a sequence without a target violation") }
    let work = try ShrinkWork(config: config, limits: limits)
    let saved = try clonedFailure(g.model, original)
    let validation = try replayCandidate(g, inputs: saved.inputs, work: work)
    let minimized: Failure<I>, validated: Bool, termination: String, outcome: ShrinkOutcome
    switch validation {
    case let .stopped(reason): minimized = try clonedFailure(g.model, saved); validated = false; termination = reason; outcome = .resourceLimit
    case let .failure(failure):
        guard shrinkHasTarget(failure, target) else { throw ModelError("original failure does not reproduce the target check and phase") }
        minimized = failure; validated = true; termination = "search_complete"; outcome = .retained
    default: throw ModelError("original failure does not reproduce with valid causality")
    }
    var report = ShrinkReport64(original: saved, minimized: minimized, attempts: 1, termination: termination,
                              history: [ShrinkAttempt64(attempt: 1, candidateLength: UInt64(original.inputs.count), operation: .validateOriginal, outcome: outcome)], replayedTransitions: work.transitions, validatedOriginal: validated)
    if !validated { return report }
    var seen = [try report.minimized.inputs.map { try copied(g.model.cloneInput, $0) }]
    while true {
        var groups = 2
        while !report.minimized.inputs.isEmpty {
            let length = report.minimized.inputs.count, chunk = (length - 1) / groups + 1
            var accepted = false, start = 0
            while start < length {
                if let stop = work.control.stop() { report.termination = stop; return report }
                let end = start + min(chunk, length - start)
                var candidate = try report.minimized.inputs.map { try copied(g.model.cloneInput, $0) }
                candidate.removeSubrange(start..<end)
                let known = try seenSequence(g, seen: seen, inputs: candidate, control: work.control)
                if let stop = work.control.stop() { report.termination = stop; return report }
                if !known {
                    if try !shrinkAttempt(g, target: target, candidate: candidate, operation: .remove(start: UInt64(start), end: UInt64(end)), report: &report, seen: &seen, work: work) { return report }
                    if report.history.last?.outcome == .retained { accepted = true; groups = max(2, groups - 1); break }
                }
                start = end
            }
            if accepted { continue }
            if groups >= length { break }
            groups = groups > length / 2 ? length : groups * 2
        }
        var simplified = false
        positions: for index in report.minimized.inputs.indices {
            if let stop = work.control.stop() { report.termination = stop; return report }
            let alternatives = try call("simpler_inputs") { try g.simplerInputs(copied(g.model.cloneInput, report.minimized.inputs[index])) }
            if let stop = work.control.stop() { report.termination = stop; return report }
            var alternative: UInt64 = 0
            // Duplicate hints do not create replay attempts or history entries,
            // but a lazy host iterator can produce them forever. Bound
            // consecutive skips per iterator (Rust shrink_ops) so finite runs
            // are unaffected.
            var duplicateHints: UInt64 = 0
            while true {
                if let stop = work.control.stop() { report.termination = stop; return report }
                if let limit = work.maxSimplificationCandidates,report.simplificationCandidates >= limit { report.termination = "simplification_limit"; return report }
                let value = try call("simpler_inputs next", alternatives.next)
                if let stop = work.control.stop() { report.termination = stop; return report }
                guard let value else { break }
                let (candidateCount,countOverflow) = report.simplificationCandidates.addingReportingOverflow(1)
                guard !countOverflow else { throw ModelError("simplification candidate count overflow") }
                report.simplificationCandidates = candidateCount
                let (nextIndex, overflow) = alternative.addingReportingOverflow(1)
                guard !overflow else { throw ModelError("simplification index overflow") }
                let alternativeIndex = alternative; alternative = nextIndex
                var candidate = try report.minimized.inputs.map { try copied(g.model.cloneInput, $0) }
                candidate[index] = try copied(g.model.cloneInput, value)
                let known = try seenSequence(g, seen: seen, inputs: candidate, control: work.control)
                if let stop = work.control.stop() { report.termination = stop; return report }
                if known {
                    if duplicateHints == work.maxAttempts { report.termination = "attempt_limit"; return report }
                    duplicateHints += 1
                    continue
                }
                duplicateHints = 0
                if try !shrinkAttempt(g, target: target, candidate: candidate, operation: .simplify(index: UInt64(index), alternative: alternativeIndex), report: &report, seen: &seen, work: work) { return report }
                if report.history.last?.outcome == .retained { simplified = true; break positions }
            }
        }
        if !simplified { return report }
    }
}

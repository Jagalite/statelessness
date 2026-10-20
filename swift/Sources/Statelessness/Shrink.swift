public struct ShrinkConfig: Sendable {
    public let maxAttempts: Int
    /// Includes validation of the original failure.
    public init(maxAttempts: Int = 10_000) { self.maxAttempts = maxAttempts }
}
public struct ShrinkLimits {
    public let control: RunLimits
    public let maxReplayedTransitions: UInt64?
    public init(control: RunLimits = RunLimits(), maxReplayedTransitions: UInt64? = nil) { self.control = control; self.maxReplayedTransitions = maxReplayedTransitions }
}
public enum ShrinkOperation: Equatable, Sendable {
    case validateOriginal
    case remove(start: Int, end: Int)
    case simplify(index: Int, alternative: Int)
}
public enum ShrinkOutcome: String, Sendable {
    case retained, invalidCausality = "invalid_causality", differentFailure = "different_failure"
    case noFailure = "no_failure", alreadySeen = "already_seen", resourceLimit = "resource_limit"
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
private final class ShrinkWork {
    let control: RunControl
    let maximum: UInt64?
    let maxAttempts: Int
    var transitions: UInt64 = 0
    init(config: ShrinkConfig, limits: ShrinkLimits) throws { control = try RunControl(limits.control); maximum = limits.maxReplayedTransitions; maxAttempts = config.maxAttempts }
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
    var state = try searchInitial(m), skipped: UInt64 = 0
    let checks = try stateChecks(m, state)
    let initial = try searchFindings("initial_state", checks, skipped: &skipped)
    if !initial.isEmpty { return .failure(Failure(inputs: [], violations: initial)) }
    if let stop = work.control.stop() { return .stopped(stop) }
    for (index, input) in inputs.enumerated() {
        if let stop = work.control.stop() { return .stopped(stop) }
        if let maximum = work.maximum, work.transitions >= maximum { return .stopped("transition_limit") }
        let enabled = try call("shrink is_enabled") { try g.isEnabled(copied(m.cloneState, state), copied(m.cloneInput, input)) }
        if let stop = work.control.stop() { return .stopped(stop) }
        if !enabled { return .invalid }
        let t = try perform(m, state, input)
        let (count, overflow) = work.transitions.addingReportingOverflow(1)
        guard !overflow else { throw ModelError("replayed transition count overflow") }; work.transitions = count
        let bad = try searchEdge(m, state: state, input: input, transition: t, skipped: &skipped)
        if !bad.isEmpty { return .failure(Failure(inputs: try inputs[...index].map { try copied(m.cloneInput, $0) }, violations: bad)) }
        if let stop = work.control.stop() { return .stopped(stop) }
        state = t.state
    }
    return .pass
}
private func shrinkAttempt<S, I, O>(_ g: Generate<S, I, O>, target: Violation, candidate: [I], operation: ShrinkOperation,
                                    report: inout ShrinkReport<I>, seen: inout [[I]], work: ShrinkWork) throws -> Bool {
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
                seen.append(try failure.inputs.map { try copied(g.model.cloneInput, $0) })
                report.minimized = failure; outcome = .retained
            }
        }
    }
    report.history.append(ShrinkAttempt(attempt: report.attempts, candidateLength: candidate.count, operation: operation, outcome: outcome))
    return !stopped
}
/// Causal replay preserves the original first violation's exact ID and phase.
/// Chunk deletion precedes lazy simplification; accepted-prefix history bounds
/// cyclic hints. Reports retain supplied evidence if validation is interrupted.
public func shrink<S, I, O>(_ g: Generate<S, I, O>, original: Failure<I>, config: ShrinkConfig = ShrinkConfig(), limits: ShrinkLimits = ShrinkLimits()) throws -> ShrinkReport<I> {
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
    var report = ShrinkReport(original: saved, minimized: minimized, attempts: 1, termination: termination,
                              history: [ShrinkAttempt(attempt: 1, candidateLength: original.inputs.count, operation: .validateOriginal, outcome: outcome)], replayedTransitions: work.transitions, validatedOriginal: validated)
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
                    if try !shrinkAttempt(g, target: target, candidate: candidate, operation: .remove(start: start, end: end), report: &report, seen: &seen, work: work) { return report }
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
            var alternative = 0
            while true {
                if let stop = work.control.stop() { report.termination = stop; return report }
                let value = try call("simpler_inputs next", alternatives.next)
                if let stop = work.control.stop() { report.termination = stop; return report }
                guard let value else { break }
                let (nextIndex, overflow) = alternative.addingReportingOverflow(1)
                guard !overflow else { throw ModelError("simplification index overflow") }
                let alternativeIndex = alternative; alternative = nextIndex
                var candidate = try report.minimized.inputs.map { try copied(g.model.cloneInput, $0) }
                candidate[index] = try copied(g.model.cloneInput, value)
                let known = try seenSequence(g, seen: seen, inputs: candidate, control: work.control)
                if let stop = work.control.stop() { report.termination = stop; return report }
                if known { continue }
                if try !shrinkAttempt(g, target: target, candidate: candidate, operation: .simplify(index: index, alternative: alternativeIndex), report: &report, seen: &seen, work: work) { return report }
                if report.history.last?.outcome == .retained { simplified = true; break positions }
            }
        }
        if !simplified { return report }
    }
}

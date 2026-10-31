import Foundation
import Statelessness

/// Exact stored encodings can contain secrets. No historical output decoder is inferred.
public struct EvidenceBoundary {
    public let before: [UInt8]
    public let after: [UInt8]
    public let input: [UInt8]?
    public let outputs: [[UInt8]]
    public let checks: [Check]
    public let causal: StepEvidence?
}
public struct EncodedPreview {
    public let bytes: [UInt8]
    public let totalBytes: Int
    public let complete: Bool
    public let provenance = "stored-exact-encoding"
    public init(_ bytes: [UInt8], maximum: Int) throws {
        guard maximum >= 0 else { throw ConfigError("negative preview budget") }
        self.bytes = Array(bytes.prefix(maximum)); totalBytes = bytes.count; complete = bytes.count <= maximum
    }
}
/// Cooperative boundaries only; arbitrary application callbacks cannot be preempted.
public struct VerificationLimits {
    public let maxTransitions: UInt64
    public let control: RunLimits
    public init(maxTransitions: UInt64, control: RunLimits = RunLimits()) { self.maxTransitions = maxTransitions; self.control = control }
}
public struct InputPolicy<S,I,O> {
    public let label: String
    public let unrestricted: Bool
    public let permits: (Model<S,I,O>, S, I, Int) throws -> Bool
    public init(label: String, permits: @escaping (Model<S,I,O>, S, I, Int) throws -> Bool) throws {
        guard !label.isEmpty else { throw ConfigError("empty policy label") }
        self.label = label; unrestricted = false; self.permits = permits
    }
    private init() { label = "out-of-domain-injection"; unrestricted = true; permits = { _,_,_,_ in true } }
    public static var outOfDomainInjection: Self { Self() }
}

// Capture the admission query before an application state-copy hook can mutate
// it through an alias. Shared by minimization's policy adapter and its tests.
func policyPermitsSnapshot<S,I,O>(_ policy: InputPolicy<S,I,O>, model: Model<S,I,O>, state: S, input: I, maximum: Int) throws -> Bool {
    let policyInput = try model.cloneInput(input)
    let policyState = try model.cloneState(state)
    return try policy.permits(model, policyState, policyInput, maximum)
}
private final class Deadline {
    let limits: RunLimits
    let start: TimeInterval?
    let integerClock: IntegerDeadlineClock?
    init(_ limits: RunLimits) throws {
        if let n = limits.maxDuration, !n.isFinite || n < 0 { throw ConfigError("invalid duration") }
        self.limits = limits; start = limits.maxDuration == nil ? nil : ProcessInfo.processInfo.systemUptime
        integerClock = limits.maxDurationNanoseconds == nil ? nil : IntegerDeadlineClock()
    }
    func check() throws {
        if limits.isCancelled() { throw ModelError("debug operation cancelled") }
        if let clock = integerClock, let duration = limits.maxDurationNanoseconds, clock.reached(duration) { throw ModelError("debug operation deadline") }
        if let start, let n = limits.maxDuration, ProcessInfo.processInfo.systemUptime - start >= n { throw ModelError("debug operation deadline") }
    }
    func remaining() throws -> RunLimits {
        try check()
        if let clock = integerClock, let duration = limits.maxDurationNanoseconds {
            return RunLimits(maxDurationNanoseconds: clock.remaining(duration), isCancelled: limits.cancellationEnabled ? limits.isCancelled : nil)
        }
        return RunLimits(maxDuration: start.flatMap { s in limits.maxDuration.map { max(0, $0 - (ProcessInfo.processInfo.systemUptime - s)) } }, isCancelled: limits.isCancelled)
    }
}

// Declared environment and origin are a single provenance record. An absent
// pair denotes legacy evidence; partial, repeated and unknown values fail closed.
func recordedEnvironment(_ trace: AuditTrace) throws -> (String,Bool)? {
    func unique(_ key: String) throws -> String? {
        let values = trace.config.parameters.filter { exactText($0.key,key) }
        guard values.count <= 1 else { throw ModelError("ambiguous recorded environmental policy") }
        return values.first?.value
    }
    switch (try unique("stateless.debug.environment"),try unique("stateless.debug.origin")) {
    case (nil,nil): return nil
    case let (environment?,origin?):
        guard exactText(origin,"simulation") || exactText(origin,"out-of-domain-injection") else { throw ModelError("unknown policy origin") }
        return (environment,exactText(origin,"out-of-domain-injection"))
    default: throw ModelError("incomplete environmental policy provenance")
    }
}

/// Passive construction validates semantics and bounds without calling a model,
/// clock, watcher or transport. Local boundary zero is the retained checkpoint.
public struct EvidenceViewer {
    public let artifact: Artifact
    public let limits: EvidenceLimits
    public let checkpointAfter: UInt64
    public init(_ artifact: Artifact, limits: EvidenceLimits = EvidenceLimits()) throws {
        switch artifact {
        case let .v1(trace):
            try validateRecording(trace.portable().trace)
            try writeAuditTrace(trace, limits: limits.trace) { _ in }
            func counter(_ key: String) throws -> UInt64 {
                var found: UInt64?
                for parameter in trace.config.parameters where exactText(parameter.key,key) {
                    guard found == nil else { throw ModelError("ambiguous retained checkpoint sequence") }
                    guard let value = checkpointInteger(parameter.value) else { throw ModelError("invalid checkpoint") }
                    found = value
                }
                return found ?? 0
            }
            let origin = try counter("stateless.monitor.sequence_origin")
            let relative = try counter("stateless.monitor.checkpoint_after_sequence")
            let (absolute,overflow) = origin.addingReportingOverflow(relative)
            guard !overflow else { throw ModelError("retained sequence overflow") }
            checkpointAfter = absolute
        case let .v2(evidence): try evidence.validate(limits: limits); checkpointAfter = evidence.checkpointAfter
        }
        guard !checkpointAfter.addingReportingOverflow(UInt64(artifact.trace.steps.count)).overflow else { throw ModelError("retained sequence overflow") }
        self.artifact = artifact; self.limits = limits
    }
    public static func read(limits: EvidenceLimits = EvidenceLimits(), from read: (Int) throws -> [UInt8]) throws -> Self {
        try Self(Artifact.read(limits: limits, from: read), limits: limits)
    }
    public var retainedRange: ClosedRange<UInt64> { checkpointAfter...(checkpointAfter + UInt64(artifact.trace.steps.count)) }
    public func boundary(_ sequence: Int) -> EvidenceBoundary? {
        let trace = artifact.trace
        guard sequence >= 0, sequence <= trace.steps.count else { return nil }
        if sequence == 0 { return EvidenceBoundary(before: trace.initialState, after: trace.initialState, input: nil, outputs: [], checks: trace.initialChecks, causal: nil) }
        let step = trace.steps[sequence-1]
        let causal: StepEvidence?
        if case let .v2(v) = artifact { causal = v.steps[sequence-1] } else { causal = nil }
        return EvidenceBoundary(before: sequence == 1 ? trace.initialState : trace.steps[sequence-2].postState, after: step.postState, input: step.input, outputs: step.outputs, checks: step.checks, causal: causal)
    }
    public func failure(sequence: Int, check: Int, legacyPhase: CheckPhase? = nil) throws -> Violation {
        guard let row = boundary(sequence), check >= 0, check < row.checks.count else { throw ModelError("unknown evidence check") }
        let selected = row.checks[check]
        guard selected.status == .failed else { throw ModelError("selected check is not a failure") }
        let phase = artifact.checkPhase(sequence: UInt64(sequence), checkIndex: check) ?? legacyPhase
        guard let phase, phase != .unknownLegacy, sequence == 0 ? phase == .initialState : phase != .initialState else { throw ModelError("v1 phase unknown; verify and upgrade or explicitly supply phase") }
        return Violation(phase: phase.rawValue, check: selected)
    }
    /// Explicit execution. Budget is checked before model callbacks, then between
    /// observations; v2 stored phase boundaries must also match actual checks.
    public func verify<S,I,O>(_ model: Model<S,I,O>, budget: VerificationLimits, allowBuildMismatch: Bool = false) throws -> ReplayReport {
        let deadline = try Deadline(budget.control)
        guard UInt64(artifact.trace.steps.count) <= budget.maxTransitions else { throw ModelError("verification transition budget") }
        try deadline.check()
        do {
            let report = try replayWithObservationsBounded(model, trace: artifact.trace, allowBuildMismatch: allowBuildMismatch, limits: limits.trace) { observation, report in
                try deadline.check()
                if case let .v2(evidence) = artifact, case let .turn(turn) = observation, report.outcome == "exact", UInt64(evidence.steps[Int(turn.sequence-1)].stateCheckCount) != UInt64(turn.stateCheckCount) {
                    throw ModelError("v2 phase evidence differs from replay")
                }
            }
            try deadline.check(); return report
        } catch let error as ReplayError<S,I,O> { throw error.error }
    }
    private func validatePolicy<S,I,O>(_ policy: InputPolicy<S,I,O>?) throws {
        guard let (environment,unrestricted) = try recordedEnvironment(artifact.trace) else { return }
        if let policy {
            guard exactText(policy.label,environment),policy.unrestricted == unrestricted else { throw ModelError("minimization policy differs") }
        } else if !(unrestricted && exactText(environment,"out-of-domain-injection")) { throw ModelError("declared policy requires explicit policy callback") }
    }
    /// Each candidate decodes a fresh retained checkpoint. Both environmental
    /// admission and generator causality apply. Verification consumes this same
    /// overall transition/time budget before shrinking begins.
    public func minimize<S,I,O>(_ generator: Generate<S,I,O>, sequence: Int, check: Int, legacyPhase: CheckPhase? = nil, policy: InputPolicy<S,I,O>? = nil, maxAdmissionCandidates: Int = 100_000, config: ShrinkConfig = ShrinkConfig(), limits shrinkLimits: ShrinkLimits) throws -> ShrinkReport<I> {
        guard maxAdmissionCandidates > 0, config.maxAttempts > 0 else { throw ConfigError("positive minimization budgets required") }
        try validatePolicy(policy)
        let target = try failure(sequence: sequence, check: check, legacyPhase: legacyPhase)
        guard sequence == artifact.trace.steps.count || sequence == 0 else { throw ModelError("failure must terminate selected artifact") }
        guard let codec = generator.model.codec else { throw ModelError("minimization requires codec") }
        let passes: UInt64 = { if case .v2 = artifact { return 2 }; return 1 }()
        let (count, countOverflow) = UInt64(artifact.trace.steps.count).multipliedReportingOverflow(by: passes)
        guard !countOverflow else { throw ModelError("verification count overflow") }
        let maximum = shrinkLimits.maxReplayedTransitions ?? UInt64.max
        let verificationSteps = UInt64(artifact.trace.steps.count)
        if passes == 2 {
            guard verificationSteps <= maximum else { throw ModelError("minimization budget cannot cover v2 phase verification") }
        } else {
            guard verificationSteps == 0 || verificationSteps < maximum else { throw ModelError("minimization budget must cover verification and original validation") }
        }
        let deadline = try Deadline(shrinkLimits.control)
        let verified = try verify(generator.model, budget: VerificationLimits(maxTransitions: maximum, control: try deadline.remaining()))
        guard verified.outcome == "exact", verified.failureReproduced else { throw ModelError("minimization requires exact failure replay") }
        if case .v2 = artifact {
            guard verificationSteps == 0 || count < maximum else { throw ModelError("minimization budget must cover exact verification and original validation") }
            let second = try verify(generator.model, budget: VerificationLimits(maxTransitions: maximum, control: try deadline.remaining()))
            guard second.outcome == "exact", second.failureReproduced else { throw ModelError("minimization requires exact failure replay") }
        }
        var model = generator.model
        let checkpoint = artifact.trace.initialState
        model.initialState = { try codec.decodeState(checkpoint) }
        let original = generator
        var adapted = generator; adapted.model = model
        adapted.isEnabled = { state,input in
            if let policy, try !policyPermitsSnapshot(policy, model: original.model, state: state, input: input, maximum: maxAdmissionCandidates) { return false }
            return try original.isEnabled(state,input)
        }
        let inputs = try artifact.trace.steps.map { try codec.decodeInput($0.input) }
        var report = try shrink(adapted, original: Failure(inputs: inputs, violations: [target]), config: config, limits: ShrinkLimits(control: try deadline.remaining(), maxReplayedTransitions: maximum - count,maxSimplificationCandidates:shrinkLimits.maxSimplificationCandidates))
        let (total,overflow) = report.replayedTransitions.addingReportingOverflow(count)
        guard !overflow else { throw ModelError("minimization transition counter overflow") }
        report.replayedTransitions = total; return report
    }
}

private func checkpointInteger(_ text: String) -> UInt64? {
    var iterator = text.utf8.makeIterator()
    guard var byte = iterator.next() else { return nil }
    if byte == 43 { guard let next = iterator.next() else { return nil }; byte = next }
    var result: UInt64 = 0
    while true {
        guard byte >= 48,byte <= 57 else { return nil }
        let (product,a) = result.multipliedReportingOverflow(by: 10), (sum,b) = product.addingReportingOverflow(UInt64(byte-48))
        guard !a,!b else { return nil }; result = sum
        guard let next = iterator.next() else { return result }; byte = next
    }
}

/// Detached diagnostics. This publicly constructible value is not authority to
/// bypass checking; exact recording accepts only sealed CheckedTurn results.
public struct TurnObservation<S,I,O> {
    public let sequence: UInt64
    public let before: S
    public let input: I
    public let transition: Transition<S,O>
    public let checks: [Check]
    public let stateCheckCount: Int
    public init(sequence: UInt64, before: S, input: I, transition: Transition<S,O>, checks: [Check], stateCheckCount: Int) {
        self.sequence = sequence; self.before = before; self.input = input
        self.transition = transition; self.checks = checks; self.stateCheckCount = stateCheckCount
    }
}
/// Complete checking of a supplied checkpoint, including failed/skipped checks.
/// Engine-only construction binds a pristine detached checkpoint before checking.
/// Accessors return deep detached values using the model's copy contract and may
/// throw; no additional Sendable/codec/hash/output-decoder constraint is imposed.
public final class CheckedInitial<S,I,O> {
    private let certifiedModel: Model<S,I,O>
    private let certifiedState: S
    private let certifiedChecks: [Check]
    private init(model: Model<S,I,O>, state: S, checks: [Check]) {
        certifiedModel = model; certifiedState = state; certifiedChecks = checks
    }
    public var model: Model<S,I,O> { certifiedModel }
    public var state: S { get throws { try copied(certifiedModel.cloneState, certifiedState) } }
    public var checks: [Check] { certifiedChecks }
    public var stateCheckCount: Int { certifiedChecks.count }
    /// Returns detached value storage. This native API does not globally consume
    /// the certificate; independent recorders may borrow the same result.
    public func intoChecks() -> [Check] { certifiedChecks }
    fileprivate static func checked(_ model: Model<S,I,O>, state: S) throws -> CheckedInitial<S,I,O> {
        let retained = try copied(model.cloneState, state)
        var checks: [Check] = []
        try stateChecksInto(model, retained, checks: &checks, stage: "initial check")
        return CheckedInitial(model: model, state: retained, checks: checks)
    }
}
/// Complete checking of an already executed turn. Private context, sequence,
/// checks and coverage cannot be replaced with a diagnostic observation.
/// Application clone callbacks must deeply copy reference payloads and remain
/// deterministic; native API sealing is not a security sandbox for application
/// callbacks that deliberately violate their ownership contract.
public final class CheckedTurn<S,I,O> {
    private let certifiedModel: Model<S,I,O>
    private let certifiedBefore: S
    private let certifiedInput: I
    private let certifiedTransition: Transition<S,O>
    private let certifiedChecks: [Check]
    public let sequence: UInt64
    public let stateCheckCount: Int
    public let fullChecking: Bool
    private init(model: Model<S,I,O>, before: S, input: I, transition: Transition<S,O>, sequence: UInt64, checks: [Check], stateCheckCount: Int, fullChecking: Bool) {
        certifiedModel = model; certifiedBefore = before; certifiedInput = input
        certifiedTransition = transition; self.sequence = sequence
        certifiedChecks = checks; self.stateCheckCount = stateCheckCount; self.fullChecking = fullChecking
    }
    public var model: Model<S,I,O> { certifiedModel }
    public var before: S { get throws { try copied(certifiedModel.cloneState, certifiedBefore) } }
    public var input: I { get throws { try copied(certifiedModel.cloneInput, certifiedInput) } }
    public var transition: Transition<S,O> { get throws { try cloneTransition(certifiedModel, certifiedTransition) } }
    public var checks: [Check] { certifiedChecks }
    public func intoChecks() -> [Check] { certifiedChecks }
    public func observation() throws -> TurnObservation<S,I,O> {
        TurnObservation(sequence: sequence, before: try copied(certifiedModel.cloneState, certifiedBefore), input: try copied(certifiedModel.cloneInput, certifiedInput), transition: try cloneTransition(certifiedModel, certifiedTransition), checks: certifiedChecks, stateCheckCount: stateCheckCount)
    }
    fileprivate static func checked(_ model: Model<S,I,O>, before: S, input: I, transition: Transition<S,O>, sequence: UInt64, policy: CheckPolicy) throws -> CheckedTurn<S,I,O> {
        guard sequence > 0 else { throw ModelError("transition sequence must be positive") }
        guard policy.stateEvery > 0 else { throw ConfigError("state period must be positive") }
        let retainedBefore = try copied(model.cloneState, before)
        let retainedInput = try copied(model.cloneInput, input)
        let retainedTransition = try cloneTransition(model, transition)
        var checks: [Check] = []
        let boundary = try checkObservedCountInto(model, before: retainedBefore, input: retainedInput, transition: retainedTransition, sequence: sequence, policy: policy, checks: &checks)
        return CheckedTurn(model: model, before: retainedBefore, input: retainedInput, transition: retainedTransition, sequence: sequence, checks: checks, stateCheckCount: boundary, fullChecking: policy.stateEvery == 1 && policy.transitionChecks)
    }
}
/// Never invokes initialState or the reducer; codecs are not required.
public func checkInitial<S,I,O>(_ model: Model<S,I,O>, state: S) throws -> CheckedInitial<S,I,O> {
    try CheckedInitial.checked(model, state: state)
}
/// Never invokes the reducer; failures in a complete batch still yield a token.
public func checkTurn<S,I,O>(_ model: Model<S,I,O>, before: S, input: I, transition: Transition<S,O>, sequence: UInt64, policy: CheckPolicy = CheckPolicy()) throws -> CheckedTurn<S,I,O> {
    try CheckedTurn.checked(model, before: before, input: input, transition: transition, sequence: sequence, policy: policy)
}

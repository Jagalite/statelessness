/// Persistent application execution of one model (production-execution-v1).
///
/// A session owns the committed model state and turn sequence for an
/// application that runs the model as its live functional core. Each
/// `submit` clones the input and prior state, invokes `step` exactly once,
/// checks the candidate without re-running the reducer, detaches the outputs,
/// and only then commits state and sequence together. A thrown step, clone or
/// check leaves the prior committed state and sequence intact and exposes no
/// outputs. A complete batch with failed checks is still a committed turn: the
/// application's transition owns its own safety policy (for example entering a
/// failed lifecycle), and the library never guesses a rollback.
///
/// Sessions perform no effects. Committed outputs are returned once per
/// successful submission for the host to execute; this is local hand-off, not
/// durable exactly-once I/O. Access is synchronous and exclusive: a submission
/// made while another is executing (for example from a model callback) fails
/// with `ExecutionFault.reentrantSubmission` without disturbing the outer turn.
/// Hosts queue callbacks that arrive during dispatch as later inputs.
///
/// The class is deliberately not `Sendable`. Confine it to one isolation
/// domain (for example a main-actor host); model closures are not required to
/// be thread-safe.
public final class ExecutionSession<S, I, O> {
    public let model: Model<S, I, O>
    public let policy: CheckPolicy
    /// Checks of the initial or restored checkpoint, run once at creation.
    public let initialChecks: [Check]
    /// Number of the last committed turn. Zero before the first turn, or the
    /// restored checkpoint's sequence.
    public private(set) var sequence: UInt64
    /// Optional bounded audit recorder. Recorder failure after commit is a
    /// diagnostic (`CommittedTurn.recorderFault`); the turn stays committed.
    public let recorder: Recorder?
    /// Whether each committed turn carries a sealed `CheckedTurn` certificate.
    /// Always true with a recorder. Without one, the session checks the turn
    /// on its own detached values and hands the step's result off directly,
    /// avoiding the certificate's retained copies.
    public let certifiesTurns: Bool
    private var committed: S
    private var executing = false

    /// Starts from `model.initialState()`.
    public convenience init(model: Model<S, I, O>, policy: CheckPolicy = CheckPolicy(), recorder: Recorder? = nil, certifiesTurns: Bool = false) throws {
        let state = try call("initial state") { try model.initialState() }
        try self.init(model: model, checkpoint: state, sequence: 0, policy: policy, recorder: recorder, certifiesTurns: certifiesTurns)
    }

    /// Restores a checkpoint. `sequence` is the number of the last turn that
    /// produced it; the next submission is `sequence + 1`. Restoration is
    /// effect-free: no outputs are replayed.
    public init(model: Model<S, I, O>, checkpoint: S, sequence: UInt64, policy: CheckPolicy = CheckPolicy(), recorder: Recorder? = nil, certifiesTurns: Bool = false) throws {
        guard policy.stateEvery > 0 else { throw ConfigError("state period must be positive") }
        let checked = try checkInitial(model, state: checkpoint)
        self.model = model
        self.policy = policy
        self.recorder = recorder
        self.certifiesTurns = certifiesTurns || recorder != nil
        self.sequence = sequence
        self.committed = try checked.state
        self.initialChecks = checked.checks
    }

    /// A detached copy of the committed state (the model's clone contract).
    /// Exclusive like `read`: a clone callback cannot submit meanwhile.
    public func snapshot() throws -> S {
        guard !executing else { throw ExecutionFault.reentrantSubmission }
        executing = true
        defer { executing = false }
        return try copied(model.cloneState, committed)
    }

    /// Read-only access to the committed state without cloning. The body
    /// must not retain mutable references beyond the call; it cannot submit.
    public func read<R>(_ body: (S) throws -> R) throws -> R {
        guard !executing else { throw ExecutionFault.reentrantSubmission }
        executing = true
        defer { executing = false }
        return try body(committed)
    }

    /// Executes one input. See the type documentation for the commit contract.
    @discardableResult
    public func submit(_ input: I) throws -> CommittedTurn<S, I, O> {
        guard !executing else { throw ExecutionFault.reentrantSubmission }
        guard sequence < UInt64.max else { throw ExecutionFault.sequenceExhausted }
        executing = true
        defer { executing = false }
        let next = sequence + 1
        guard certifiesTurns else { return try submitUncertified(input, sequence: next) }
        let checked: CheckedTurn<S, I, O>
        let state: S
        let outputs: [O]
        let disposition: Disposition
        do {
            let (before, checkedInput, owned) = try execute(input, before: try copied(model.cloneState, committed))
            checked = try CheckedTurn.adopting(model, before: before, input: checkedInput, transition: owned, sequence: next, policy: policy)
            // The certificate retains `owned`; commit and hand off copies.
            state = try copied(model.cloneState, owned.state)
            outputs = try owned.outputs.map { try copied(model.cloneOutput, $0) }
            disposition = owned.disposition
        } catch {
            throw ExecutionFault.model(String(describing: error))
        }
        committed = state
        sequence = next
        var recorderFault: String?
        if let recorder {
            do { _ = try recorder.observeChecked(checked) } catch { recorderFault = String(describing: error) }
        }
        return CommittedTurn(checked: checked, checks: checked.checks, sequence: next, outputs: outputs, disposition: disposition, recorderFault: recorderFault)
    }

    /// Runs the step on its own detached state and input, then detaches the
    /// returned transition with the model's clone contract, so the session
    /// owns the result even if the step retained references to it. Returns
    /// the prior state, a pristine input for checks (the step may have
    /// mutated its own copy), and the owned transition.
    private func execute(_ input: I, before: S) throws -> (S, I, Transition<S, O>) {
        let checkedInput = try copied(model.cloneInput, input)
        let candidate = try call("transition") {
            try model.step(copied(model.cloneState, before), copied(model.cloneInput, checkedInput))
        }
        return (before, checkedInput, try cloneTransition(model, candidate))
    }

    /// Checks read the committed prior state in place; the owned transition
    /// is retained by nothing else, so it is committed and handed off as is.
    private func submitUncertified(_ input: I, sequence next: UInt64) throws -> CommittedTurn<S, I, O> {
        let owned: Transition<S, O>
        var checks: [Check] = []
        do {
            let (_, checkedInput, result) = try execute(input, before: committed)
            owned = result
            _ = try checkObservedCountInto(model, before: committed, input: checkedInput, transition: owned, sequence: next, policy: policy, checks: &checks)
        } catch {
            throw ExecutionFault.model(String(describing: error))
        }
        committed = owned.state
        sequence = next
        return CommittedTurn(checked: nil, checks: checks, sequence: next, outputs: owned.outputs, disposition: owned.disposition, recorderFault: nil)
    }
}

public enum ExecutionFault: Error, Equatable, CustomStringConvertible {
    /// A submission or read was attempted while a turn was executing.
    case reentrantSubmission
    /// The committed sequence is `UInt64.max`; no further turn can be numbered.
    case sequenceExhausted
    /// The step, a clone, or a check threw. Nothing was committed.
    case model(String)
    public var description: String {
        switch self {
        case .reentrantSubmission: "reentrant submission"
        case .sequenceExhausted: "turn sequence exhausted"
        case let .model(message): "model fault: \(message)"
        }
    }
}

/// A committed turn. `outputs` are detached values owned by the caller and
/// returned exactly once; `checked` is the sealed checking certificate when
/// the session certifies turns.
public struct CommittedTurn<S, I, O> {
    public let checked: CheckedTurn<S, I, O>?
    /// The complete check batch of this turn.
    public let checks: [Check]
    public let sequence: UInt64
    public let outputs: [O]
    public let disposition: Disposition
    /// Set when the optional recorder rejected the committed turn.
    public let recorderFault: String?
    public var failedChecks: [Check] { checks.filter { $0.status == .failed } }
}

import Statelessness

public enum SessionPhase: String { case ready, running, paused, ended, disconnected }
public enum StopReason: Equatable {
    case preBreakpoint(UInt64), postBreakpoint(UInt64), propertyFailure
    case initialCheckError(String), modelError(String), checkerError(String), observerError(String), recordingError(String)
    case budgetExhausted, cancelled
}
public enum DebugError: Error, Equatable { case staleRevision(expected: UInt64, actual: UInt64), ended, inputNotPermitted, reentrant, invalidCommand(String), unsupported(String) }
public struct SessionLimits {
    public var maxTurns: UInt64 = 100_000
    public var maxContinue: Int = 1000
    public var maxBreakpoints: Int = 64
    public var maxCandidates: Int = 1000
    public init() {}
}
public struct StepResult {
    public let sequence: UInt64
    public let delivered: Bool
    public let stop: StopReason?
}
public struct SessionCapabilities {
    public let typedInput = true
    public let exactRecording: Bool
    public let effectsDispatched = false
    public let liveRestore = false
}
public struct CandidateToken<I> {
    fileprivate let owner: UInt64
    public let revision: UInt64
    fileprivate let domain: String
    fileprivate let value: I
}
public struct CandidatePage<I> {
    public let candidates: [CandidateToken<I>]
    public let nextOffset: Int?
    public let complete: Bool
}
private final class SessionEpoch: @unchecked Sendable {
    static let shared = SessionEpoch()
    private let lock = NSLock()
    private var next: UInt64 = 1
    func take() throws -> UInt64 {
        lock.lock(); defer { lock.unlock() }
        guard next != UInt64.max else { throw DebugError.invalidCommand("session identity exhausted") }
        defer { next += 1 }; return next
    }
}
import Foundation

/// Synchronous single-owner simulation. Not Sendable. Reentrant mutations reject;
/// model copy callbacks must deeply detach mutable application references.
public final class DebugSession<S,I,O> {
    public let id: String
    public let epoch: UInt64
    private let model: Model<S,I,O>
    private let policy: InputPolicy<S,I,O>
    public let limits: SessionLimits
    private var current: S
    public private(set) var initialChecks: [Check] = []
    public private(set) var sequence: UInt64 = 0
    public private(set) var revision: UInt64 = 0
    public private(set) var phase: SessionPhase = .ready
    public private(set) var stop: StopReason?
    public private(set) var issues: [String] = []
    public private(set) var checksComplete = true
    private var busy = false
    private var nextBreakpoint: UInt64 = 1
    private var pre: [(UInt64,(S,I) throws -> Bool)] = []
    private var post: [(UInt64,(TurnObservation<S,I,O>) throws -> Bool)] = []
    private var pending: (UInt64,I)?
    private var last: TurnObservation<S,I,O>?
    private let equalInputs: (I,I) throws -> Bool
    private var recorder: Recorder?
    private var checkPolicy = CheckPolicy()
    public var capabilities: SessionCapabilities { SessionCapabilities(exactRecording: recorder != nil) }
    public var inputPolicy: String { policy.label }
    public var state: S {
        get throws {
            // Clone callbacks may read recursively, but cannot mutate the owner.
            let wasBusy = busy; busy = true; defer { busy = wasBusy }
            return try model.cloneState(current)
        }
    }
    public convenience init(id: String, model: Model<S,I,O>, policy: InputPolicy<S,I,O>, limits: SessionLimits = SessionLimits(), equalInputs: @escaping (I,I) throws -> Bool, recording: RecorderOptions? = nil) throws {
        let state = try model.initialState()
        try self.init(id: id, model: model, state: state, policy: policy, limits: limits, equalInputs: equalInputs, recording: recording)
    }
    public convenience init(id: String, model: Model<S,I,O>, state: S, policy: InputPolicy<S,I,O>, limits: SessionLimits = SessionLimits(), equalInputs: @escaping (I,I) throws -> Bool, recording: RecorderOptions? = nil) throws {
        try self.init(id: id,model: model,state: state,policy: policy,limits: limits,equalInputs: equalInputs,recording: recording,recordingParameters: [])
    }
    // Only verified workbench construction can append debugger provenance.
    init(id: String, model: Model<S,I,O>, state: S, policy: InputPolicy<S,I,O>, limits: SessionLimits = SessionLimits(), equalInputs: @escaping (I,I) throws -> Bool, recording: RecorderOptions? = nil, recordingParameters: [AuditParameter]) throws {
        guard !id.isEmpty, limits.maxTurns > 0, limits.maxContinue > 0, limits.maxCandidates > 0, limits.maxBreakpoints >= 0 else { throw DebugError.invalidCommand("nonempty identity and positive budgets required") }
        self.id = id; epoch = try SessionEpoch.shared.take(); self.model = model; self.policy = policy; self.limits = limits; self.equalInputs = equalInputs
        current = try model.cloneState(state)
        do {
            let checked = try checkInitial(model, state: current); initialChecks = checked.checks
            if initialChecks.contains(where: { $0.status == .failed }) { stop = .propertyFailure; phase = .ended }
            if let recording {
                let config = RunConfig(strategy: "debugger-simulation", parameters: [AuditParameter("stateless.debug.environment",policy.label), AuditParameter("stateless.debug.origin",policy.unrestricted ? "out-of-domain-injection" : "simulation")] + recordingParameters)
                do { recorder = try Recorder(checkedInitial: checked, config: config, options: recording) }
                catch { issues.append(String(describing: error)); if stop == nil { stop = .recordingError(String(describing: error)); phase = .ended } }
            }
        } catch { checksComplete = false; stop = .initialCheckError(String(describing: error)); phase = .ended }
    }
    public func checkRevision(_ expected: UInt64) throws {
        guard expected == revision else { throw DebugError.staleRevision(expected: expected, actual: revision) }
    }
    private func enter() throws { guard !busy else { throw DebugError.reentrant }; busy = true }
    public func addPreBreakpoint(_ predicate: @escaping (S,I) throws -> Bool) throws -> UInt64 {
        try enter(); defer { busy = false }
        let id = try breakpointID(); pre.append((id,predicate)); return id
    }
    public func addPostBreakpoint(_ predicate: @escaping (TurnObservation<S,I,O>) throws -> Bool) throws -> UInt64 {
        try enter(); defer { busy = false }
        let id = try breakpointID(); post.append((id,predicate)); return id
    }
    private func breakpointID() throws -> UInt64 {
        guard pre.count + post.count < limits.maxBreakpoints, nextBreakpoint != UInt64.max else { throw DebugError.invalidCommand("breakpoint budget exhausted") }
        defer { nextBreakpoint += 1 }; return nextBreakpoint
    }
    public func removeBreakpoint(_ id: UInt64) throws -> Bool {
        try enter(); defer { busy = false }
        let count = pre.count + post.count; pre.removeAll { $0.0 == id }; post.removeAll { $0.0 == id }; return count != pre.count + post.count
    }
    public func exportTrace() throws -> AuditTrace {
        guard !busy else { throw DebugError.reentrant }
        guard let recorder else { throw DebugError.unsupported("exact recording not enabled") }; return recorder.snapshot()
    }
    public func observation() throws -> TurnObservation<S,I,O>? {
        let wasBusy = busy; busy = true; defer { busy = wasBusy }
        guard let last else { return nil }
        return try detached(last)
    }
    private func detached(_ t: TurnObservation<S,I,O>) throws -> TurnObservation<S,I,O> {
        TurnObservation(sequence: t.sequence, before: try model.cloneState(t.before), input: try model.cloneInput(t.input), transition: Transition(state: try model.cloneState(t.transition.state), outputs: try t.transition.outputs.map(model.cloneOutput), disposition: t.transition.disposition), checks: t.checks, stateCheckCount: t.stateCheckCount)
    }
    public func step(expectedRevision: UInt64, input: I, observer: (TurnObservation<S,I,O>) throws -> Void = { _ in }) throws -> StepResult {
        try enter(); defer { busy = false }
        try checkRevision(expectedRevision)
        guard phase != .ended, phase != .disconnected else { throw DebugError.ended }
        func result(_ delivered: Bool) -> StepResult { StepResult(sequence: sequence, delivered: delivered, stop: stop) }
        guard sequence < limits.maxTurns, revision != UInt64.max else { phase = .ended; stop = .budgetExhausted; return result(false) }
        let input = try model.cloneInput(input)
        guard try policy.permits(model, model.cloneState(current), model.cloneInput(input), limits.maxCandidates) else { throw DebugError.inputNotPermitted }
        let bypass = try pending.map { try $0.0 == revision && equalInputs(model.cloneInput($0.1), model.cloneInput(input)) } ?? false
        pending = nil
        if !bypass {
            for (id,predicate) in pre where try predicate(model.cloneState(current),model.cloneInput(input)) {
                pending = (revision,try model.cloneInput(input)); phase = .paused; stop = .preBreakpoint(id); return result(false)
            }
        }
        phase = .ended; stop = nil
        let before = try model.cloneState(current)
        let transition: Transition<S,O>
        do {
            let t = try model.step(model.cloneState(before),model.cloneInput(input))
            transition = Transition(state: try model.cloneState(t.state), outputs: try t.outputs.map(model.cloneOutput), disposition: t.disposition)
        } catch { stop = .modelError(String(describing: error)); if let recorder { _ = try? recorder.stopWithError(error) }; return result(false) }
        sequence += 1; revision += 1
        var checks: [Check] = [], count = 0
        checksComplete = true
        do {
            let checked = try checkTurn(model, before: before, input: input, transition: transition, sequence: sequence, policy: checkPolicy)
            checks = checked.checks; count = checked.stateCheckCount
            if checks.contains(where: { $0.status == .failed }) { stop = .propertyFailure }
            if let recorder { do { _ = try recorder.observeChecked(checked) } catch { issues.append(String(describing: error)); if stop == nil { stop = .recordingError(String(describing: error)) } } }
        } catch { checksComplete = false; stop = .checkerError(String(describing: error)); if let recorder { _ = try? recorder.stopWithError(error) } }
        let actual = TurnObservation(sequence: sequence, before: before, input: input, transition: transition, checks: checks, stateCheckCount: count)
        if stop == nil {
            do { for (id,predicate) in post where try predicate(detached(actual)) { stop = .postBreakpoint(id); break } }
            catch { stop = .observerError(String(describing: error)) }
        }
        current = transition.state; last = actual
        switch stop { case nil, .preBreakpoint, .postBreakpoint: phase = .paused; default: phase = .ended }
        do { try observer(detached(actual)) }
        catch { issues.append(String(describing: error)); if stop == nil { stop = .observerError(String(describing: error)) }; phase = .ended }
        return result(true)
    }
    public func runBounded(expectedRevision: UInt64, inputs: InputIterator<I>, maxTurns: Int) throws -> [StepResult] {
        try checkRevision(expectedRevision)
        guard maxTurns > 0, maxTurns <= limits.maxContinue else { throw DebugError.invalidCommand("positive bounded continue required") }
        var results: [StepResult] = []
        for _ in 0..<maxTurns {
            try enter()
            let next: I?
            do { next = try inputs.next(); busy = false } catch { busy = false; throw error }
            guard let input = next else { return results }
            let result = try step(expectedRevision: revision,input: input); results.append(result)
            if result.stop != nil { return results }
        }
        stop = .budgetExhausted; phase = .paused; return results
    }
    public func cancel(expectedRevision: UInt64) throws {
        try enter(); defer { busy = false }; try checkRevision(expectedRevision)
        guard phase != .ended, phase != .disconnected else { throw DebugError.ended }
        stop = .cancelled; phase = .ended
    }
    public func inputs(expectedRevision: UInt64, offset: Int, limit: Int) throws -> CandidatePage<I> {
        try enter(); defer { busy = false }; try checkRevision(expectedRevision)
        guard offset >= 0, limit > 0, offset <= limits.maxCandidates, limit <= limits.maxCandidates-offset else { throw DebugError.invalidCommand("candidate work budget") }
        guard let enumerate = model.inputs else { throw DebugError.unsupported("model does not enumerate inputs") }
        let iterator = try enumerate(model.cloneState(current))
        for _ in 0..<offset { if try iterator.next() == nil { return CandidatePage(candidates: [],nextOffset: nil,complete: true) } }
        var candidates: [CandidateToken<I>] = []
        for _ in 0..<limit {
            guard let value = try iterator.next() else { return CandidatePage(candidates: candidates,nextOffset: nil,complete: true) }
            candidates.append(CandidateToken(owner: epoch,revision: revision,domain: policy.label,value: try model.cloneInput(value)))
        }
        let complete = try iterator.next() == nil
        return CandidatePage(candidates: candidates,nextOffset: complete ? nil : offset+limit,complete: complete)
    }
    public func step(_ token: CandidateToken<I>) throws -> StepResult {
        guard token.owner == epoch, exactText(token.domain,policy.label) else { throw DebugError.invalidCommand("candidate belongs to another session/domain") }
        return try step(expectedRevision: token.revision,input: token.value)
    }
}

extension InputPolicy {
    public static func enumerated(equalInputs: @escaping (I,I) throws -> Bool) throws -> Self {
        try Self(label: "enumerated-domain") { model,state,input,maximum in
            guard maximum > 0, let enumerate = model.inputs else { throw DebugError.unsupported("enumerated admission unavailable") }
            let iterator = try enumerate(model.cloneState(state))
            for _ in 0..<maximum {
                guard let candidate = try iterator.next() else { return false }
                if try equalInputs(model.cloneInput(candidate),model.cloneInput(input)) { return true }
            }
            guard try iterator.next() == nil else { throw ModelError("environmental admission budget exhausted") }
            return false
        }
    }
}

extension DebugSession {
    // The protocol uses the model's native codec; a fixture must not synthesize
    // checks, outputs, or wire fields through a custom host binding.
    public func stepEncoded(expectedRevision: UInt64, bytes: Data, decodeInput: ((Data) throws -> I)? = nil) throws -> StepResult {
        let input: I
        do {
            try enter(); defer { busy = false }
            try checkRevision(expectedRevision)
            // As in Rust step_encoded, decoding precedes the terminal phase
            // check, but a decoder cannot reenter and mutate this session.
            if let decodeInput {
                do { input = try decodeInput(bytes) } catch { throw ProtocolError.invalidInput }
            } else {
                guard let codec = model.codec else { throw ProtocolError.unsupported }
                do { input = try codec.decodeInput(Array(bytes)) } catch { throw ProtocolError.invalidInput }
            }
        }
        return try step(expectedRevision: expectedRevision, input: input)
    }
    func encodedOutput(_ output: O, maximum: Int) throws -> Data {
        try enter(); defer { busy = false }
        guard let codec = model.codec else { throw ProtocolError.unsupported }
        var bytes: [UInt8] = []
        try EncodeBuffer.withBuffer(&bytes, maximum: maximum) { sink in
            // Rust distinguishes an encoder error from a rejected write which
            // the encoder swallows and only exposes at buffer finalization.
            do {
                if let encode = codec.encodeOutputInto { try encode(output, sink) }
                else { try sink.extendFromVec(codec.encodeOutput(output)) }
            } catch { throw ProtocolError.callback }
            do { try sink.finish() } catch { throw ProtocolError.limit }
        }
        return Data(bytes)
    }

    /// Encode a validated detached candidate for explicit transport. Tokens retain
    /// their private typed value; stale/cross-session handles reject before codec.
    public func encodedCandidate(_ token: CandidateToken<I>,encode: (I) throws -> Data) throws -> Data {
        try enter(); defer { busy = false }
        guard token.owner == epoch,exactText(token.domain,policy.label) else { throw DebugError.invalidCommand("candidate belongs to another session/domain") }
        try checkRevision(token.revision)
        return try encode(model.cloneInput(token.value))
    }
}

import Foundation
import StatelessV2C
@_exported import Statelessness

/// A host failure, transport error, or misuse; property findings are result data.
public struct NativeError: Error, CustomStringConvertible {
    public let status: Int32
    public let operation: UInt32
    public let phase: UInt32
    public let sequence: UInt64
    public let description: String
    public init(_ description: String, status: Int32 = 10, operation: UInt32 = 0, phase: UInt32 = 0, sequence: UInt64 = 0) {
        self.description = description; self.status = status; self.operation = operation
        self.phase = phase; self.sequence = sequence
    }
}

private func engineError(_ status: Int32) -> NativeError {
    var code: Int32 = 0, operation: UInt32 = 0, phase: UInt32 = 0, sequence: UInt64 = 0, length: UInt64 = 0
    _ = stateless_v2_last_error(&code, &operation, &phase, &sequence, nil, 0, &length)
    guard length <= 64 * 1024 * 1024 else { return NativeError("ABI error \(status)", status: status) }
    var bytes = [UInt8](repeating: 0, count: Int(length))
    _ = bytes.withUnsafeMutableBufferPointer { stateless_v2_last_error(&code, &operation, &phase, &sequence, $0.baseAddress, length, &length) }
    return NativeError(String(decoding: bytes, as: UTF8.self), status: status, operation: operation, phase: phase, sequence: sequence)
}
private func requireOK(_ status: Int32) throws { if status != 0 { throw engineError(status) } }
private func span<T>(_ bytes: [UInt8], _ body: (UnsafePointer<UInt8>?, UInt64) throws -> T) rethrows -> T {
    try bytes.withUnsafeBufferPointer { try body($0.baseAddress, UInt64($0.count)) }
}
private func dispositionParts(_ disposition: Disposition) -> (UInt32, [UInt8]) {
    switch disposition {
    case .accepted: return (0, [])
    case .rejected(let text): return (1, Array(text.utf8))
    case .ignored(let text): return (2, Array(text.utf8))
    }
}
private func dispositionValue(_ code: UInt64, _ bytes: [UInt8]) throws -> Disposition {
    guard String(bytes: bytes, encoding: .utf8) != nil else { throw NativeError("invalid UTF-8 disposition") }
    let text = String(decoding: bytes, as: UTF8.self)
    switch code {
    case 0 where bytes.isEmpty: return .accepted
    case 1: return .rejected(text)
    case 2: return .ignored(text)
    default: throw NativeError("invalid disposition")
    }
}

/// Valid only for the duration of a generate callback. Retaining it is harmless;
/// calls after that invocation fail through the ABI's invocation validation.
public struct NativeRandom {
    fileprivate let session: UInt64
    fileprivate let invocation: UInt64
    public func next() throws -> UInt64 {
        var value: UInt64 = 0
        try requireOK(stateless_v2_callback_rng(session, invocation, &value)); return value
    }
    public func index(upper: UInt64) throws -> UInt64? {
        var value: UInt64 = 0, present: UInt32 = 0
        try requireOK(stateless_v2_callback_rng_index(session, invocation, upper, &present, &value))
        return present == 0 ? nil : value
    }
}

/// SplitMix64 implemented by the Rust engine, including rejection sampling.
public struct NativeRng {
    public private(set) var state: UInt64
    public init(seed: UInt64) { state = seed }
    public mutating func next() throws -> UInt64 {
        var value: UInt64 = 0; try requireOK(stateless_v2_rng_next(&state, &value)); return value
    }
    public mutating func index(upper: UInt64) throws -> UInt64? {
        var value: UInt64 = 0, present: UInt32 = 0
        try requireOK(stateless_v2_rng_index(&state, upper, &present, &value)); return present == 0 ? nil : value
    }
}

/// Portable model closures plus optional Rust-engine capabilities. Clone hooks
/// must deeply copy mutable references, including values emitted by iterators.
public struct NativeModel<State, Input, Output> {
    public var model: Model<State, Input, Output>
    public var generate: ((State, NativeRandom) throws -> Input?)?
    public var isEnabled: ((State, Input) throws -> Bool)?
    public var equalInputs: ((Input, Input) throws -> Bool)?
    public var simplerInputs: ((Input) throws -> InputIterator<Input>)?
    public var estimatedStateBytes: ((State) throws -> UInt64?)?
    public var automaticGeneration: Bool
    public init(_ model: Model<State, Input, Output>,
                generate: ((State, NativeRandom) throws -> Input?)? = nil,
                isEnabled: ((State, Input) throws -> Bool)? = nil,
                equalInputs: ((Input, Input) throws -> Bool)? = nil,
                simplerInputs: ((Input) throws -> InputIterator<Input>)? = nil,
                estimatedStateBytes: ((State) throws -> UInt64?)? = nil,
                automaticGeneration: Bool = false) {
        self.model = model; self.generate = generate; self.isEnabled = isEnabled
        self.equalInputs = equalInputs; self.simplerInputs = simplerInputs
        self.estimatedStateBytes = estimatedStateBytes
        self.automaticGeneration = automaticGeneration
    }
}

private final class CursorRoot {
    let next: () throws -> Any?
    let anchor: Any?
    init<T>(_ iterator: InputIterator<T>, anchor: Any? = nil) {
        self.anchor = anchor; next = { try iterator.next().map { $0 as Any } }
    }
}
private class CallbackContext {
    var roots: [UInt64: (UInt32, Any)] = [:]
    private var nextToken: UInt64 = 1
    func retain(_ kind: UInt32, _ value: Any) throws -> UInt64 {
        guard nextToken < UInt64.max else { throw NativeError("host token space exhausted", status: 18) }
        let token = nextToken; nextToken += 1; roots[token] = (kind, value); return token
    }
    func value<T>(_ token: UInt64, _ kind: UInt32, as: T.Type = T.self) throws -> T {
        guard let root = roots[token], root.0 == kind, let value = root.1 as? T else { throw NativeError("invalid host root", status: 14) }
        return value
    }
    func publish(_ session: UInt64, _ invocation: UInt64, _ kind: UInt32, _ value: Any) throws {
        let token = try retain(kind, value)
        let status = stateless_v2_callback_value(session, invocation, kind, token)
        if status != 0 { roots.removeValue(forKey: token); throw engineError(status) }
    }
    func dispatch(_ session: UInt64, _ invocation: UInt64, _ operation: UInt32, _ args: [UInt64], _ data: [UInt8]) throws {
        throw NativeError("unimplemented callback", status: 17)
    }
}
private final class TypedContext<S, I, O>: CallbackContext {
    let native: NativeModel<S, I, O>
    init(_ native: NativeModel<S, I, O>) { self.native = native }
    var model: Model<S, I, O> { native.model }
    func state(_ token: UInt64) throws -> S { try model.cloneState(value(token, 1)) }
    func input(_ token: UInt64) throws -> I { try model.cloneInput(value(token, 2)) }
    func output(_ token: UInt64) throws -> O { try model.cloneOutput(value(token, 3)) }
    override func dispatch(_ session: UInt64, _ invocation: UInt64, _ operation: UInt32, _ args: [UInt64], _ data: [UInt8]) throws {
        func scalar(_ value: UInt64) throws { try requireOK(stateless_v2_callback_scalar(session, invocation, value)) }
        func checks(_ values: [Check]) throws {
            for check in values {
                try check.validate()
                let status: UInt32 = check.status == .passed ? 0 : check.status == .failed ? 1 : 2
                try span(Array(check.id.utf8)) { id, idCount in
                    try span(Array(check.details.utf8)) { details, detailsCount in
                        try requireOK(stateless_v2_callback_check(session, invocation, status, id, idCount, details, detailsCount))
                    }
                }
            }
        }
        func bytes(_ value: [UInt8]) throws { try span(value) { try requireOK(stateless_v2_callback_bytes(session, invocation, $0, $1)) } }
        // The engine validates its argument shapes. Defend the Swift boundary as
        // well so a malformed dispatcher call cannot become an indexing trap.
        let expected: Int
        switch operation {
        case 1, 11, 13: expected = 0
        case 2, 5, 16, 17: expected = 2
        case 4: expected = 4
        default: expected = 1
        }
        guard args.count >= expected else { throw NativeError("invalid callback argument count") }
        switch operation {
        case 1:
            let value = try model.initialState()
            try publish(session, invocation, 1, model.cloneState(value))
        case 2:
            let before = try state(args[0]), inputValue = try input(args[1])
            let transition = try model.step(before, inputValue)
            try publish(session, invocation, 1, model.cloneState(transition.state))
            for output in transition.outputs { try publish(session, invocation, 3, model.cloneOutput(output)) }
            let (code, reason) = dispositionParts(transition.disposition)
            try span(reason) { try requireOK(stateless_v2_callback_disposition(session, invocation, code, $0, $1)) }
        case 3: try checks(model.checkState(state(args[0])))
        case 4:
            if let callback = model.checkTransition {
                let before = try state(args[0]), inputValue = try input(args[1]), after = try state(args[2])
                let outputs = try args.dropFirst(4).map { try output($0) }
                let transition = Transition(state: after, outputs: outputs, disposition: try dispositionValue(args[3], data))
                try checks(callback(before, inputValue, transition))
            }
        case 5:
            guard let callback = model.equalStates else { throw NativeError("state equality unavailable", status: 17) }
            try scalar(callback(state(args[0]), state(args[1])) ? 1 : 0)
        case 6:
            guard let callback = model.hashState else { throw NativeError("state hash unavailable", status: 17) }
            try scalar(callback(state(args[0])))
        case 7:
            if let callback = native.estimatedStateBytes, let size = try callback(state(args[0])) { try scalar(size) }
        case 8:
            guard let callback = model.inputs else { throw NativeError("input enumeration unavailable", status: 17) }
            let source = try state(args[0])
            let iterator = try callback(source)
            try publish(session, invocation, 4, CursorRoot(iterator, anchor: source))
        case 9:
            let cursor: CursorRoot = try value(args[0], 4)
            if let next = try cursor.next() {
                guard let input = next as? I else { throw NativeError("cursor input type mismatch") }
                try publish(session, invocation, 2, model.cloneInput(input))
            }
        case 10...14:
            guard let codec = model.codec else { throw NativeError("codec unavailable", status: 17) }
            switch operation {
            case 10: try bytes(codec.encodeState(state(args[0])))
            case 11: try publish(session, invocation, 1, model.cloneState(codec.decodeState(data)))
            case 12: try bytes(codec.encodeInput(input(args[0])))
            case 13: try publish(session, invocation, 2, model.cloneInput(codec.decodeInput(data)))
            default: try bytes(codec.encodeOutput(output(args[0])))
            }
        case 15:
            guard let callback = native.generate else { throw NativeError("generation unavailable", status: 17) }
            if let value = try callback(state(args[0]), NativeRandom(session: session, invocation: invocation)) {
                try publish(session, invocation, 2, model.cloneInput(value))
            }
        case 16:
            let enabled = try native.isEnabled?(state(args[0]), input(args[1])) ?? true
            try scalar(enabled ? 1 : 0)
        case 17:
            guard let callback = native.equalInputs else { throw NativeError("input equality unavailable", status: 17) }
            try scalar(callback(input(args[0]), input(args[1])) ? 1 : 0)
        case 18:
            guard let callback = native.simplerInputs else { throw NativeError("simpler inputs unavailable", status: 17) }
            let source = try input(args[0])
            try publish(session, invocation, 4, CursorRoot(callback(source), anchor: source))
        default: throw NativeError("unknown callback operation", status: 17)
        }
    }
}
private func callbackContext(_ token: UInt64) -> CallbackContext? {
    guard let pointer = UnsafeRawPointer(bitPattern: UInt(token)) else { return nil }
    return Unmanaged<CallbackContext>.fromOpaque(pointer).takeUnretainedValue()
}
private let dispatchCallback: StatelessV2Dispatch = { context, session, invocation, operation, args, count, data, length in
    guard let context = callbackContext(context), length <= UInt64(Int.max), count == 0 || args != nil, length == 0 || data != nil else { return 10 }
    do {
        let values = count == 0 ? [] : Array(UnsafeBufferPointer(start: args, count: Int(count)))
        let bytes = length == 0 ? [] : Array(UnsafeBufferPointer(start: data, count: Int(length)))
        try context.dispatch(session, invocation, operation, values, bytes); return 0
    } catch {
        span(Array(String(describing: error).utf8)) { _ = stateless_v2_callback_error(session, invocation, $0, $1) }
        return 11
    }
}
private let releaseCallback: StatelessV2Release = { context, kind, token in
    guard let context = callbackContext(context), context.roots[token]?.0 == kind else { return }
    context.roots.removeValue(forKey: token)
}

/// Synchronous, owner-thread-confined engine. Call close on the creating thread.
/// Explicit close disposes all outstanding results and retained host roots.
public final class NativeSession<S, I, O> {
    fileprivate let owner = Thread.current
    private let cancellationLock = NSLock()
    fileprivate var handle: UInt64 = 0
    private var busy = false
    private let context: TypedContext<S, I, O>
    private var retainedContext: Unmanaged<CallbackContext>?
    public let capabilities: UInt64
    public init(_ native: NativeModel<S, I, O>, metadata: Metadata? = nil,
                maxValues: UInt64 = 1_000_000, maxItems: UInt64 = 1_000_000, maxBytes: UInt64 = 64 * 1024 * 1024) throws {
        guard stateless_v2_abi_version() == 2 else { throw NativeError("ABI v2 required", status: 17) }
        guard maxValues > 0, maxItems > 0, maxBytes > 0 else { throw NativeError("quotas must be positive") }
        var offered: UInt64 = 1
        if native.model.inputs != nil && native.model.equalStates != nil { offered |= 2 }
        if native.model.codec != nil { offered |= 4 }
        if native.generate != nil { offered |= 8 }
        if native.automaticGeneration {
            guard native.generate == nil, offered & 2 != 0, native.equalInputs != nil else {
                throw NativeError("automatic generation requires enumeration and input equality, without a custom generator")
            }
            offered |= 8 | 256
        }
        if offered & 8 != 0 && native.simplerInputs != nil && native.equalInputs != nil { offered |= 16 }
        if native.model.hashState != nil { offered |= 32 }
        if native.estimatedStateBytes != nil { offered |= 64 }
        if native.model.codec != nil && stateless_v2_capabilities() & 128 != 0 { offered |= 128 }
        capabilities = offered
        context = TypedContext(native)
        let identity: Metadata
        if let metadata { identity = metadata }
        else if let codec = native.model.codec { identity = try codec.metadata() }
        else { identity = Metadata(name: "swift") }
        let retained = Unmanaged<CallbackContext>.passRetained(context)
        retainedContext = retained
        var descriptor = StatelessV2Callbacks()
        descriptor.abi_version = 2; descriptor.struct_size = UInt32(MemoryLayout<StatelessV2Callbacks>.size)
        descriptor.capabilities = offered; descriptor.context = UInt64(UInt(bitPattern: retained.toOpaque()))
        descriptor.dispatch = dispatchCallback; descriptor.release = releaseCallback
        descriptor.max_values = maxValues; descriptor.max_items = maxItems; descriptor.max_bytes = maxBytes
        let status = span(Array(identity.name.utf8)) { name, nameCount in
            span(Array(identity.build.utf8)) { build, buildCount in
                stateless_v2_session_new(&descriptor, name, nameCount, build, buildCount,
                    identity.modelVersion, identity.propertiesVersion, identity.codecVersion, &handle)
            }
        }
        if status != 0 { let error = engineError(status); retained.release(); retainedContext = nil; throw error }
    }
    public convenience init(_ model: Model<S, I, O>, metadata: Metadata? = nil) throws {
        try self.init(NativeModel(model), metadata: metadata)
    }
    deinit {
        // A wrong-thread teardown cannot safely enter the engine. Its retained
        // context deliberately survives; deterministic close avoids this leak.
        if Thread.current === owner && handle != 0 { try? close() }
    }
    fileprivate func withCall<T>(_ body: (UInt64) throws -> T) throws -> T {
        guard Thread.current === owner else { throw NativeError("session belongs to another thread", status: 15) }
        guard handle != 0 else { throw NativeError("session is closed", status: 14) }
        guard !busy else { throw NativeError("reentrant engine operation", status: 16) }
        busy = true; defer { busy = false }; return try body(handle)
    }
    public func close() throws {
        try withCall { session in
            cancellationLock.lock(); defer { cancellationLock.unlock() }
            try requireOK(stateless_v2_session_close(session)); handle = 0
            retainedContext?.release(); retainedContext = nil
        }
    }
    /// The only operation allowed from another thread. Do not close concurrently
    /// through any raw ABI handle; this wrapper serializes cancellation and close.
    public func cancel() throws {
        cancellationLock.lock(); defer { cancellationLock.unlock() }
        guard handle != 0 else { throw NativeError("session is closed", status: 14) }
        try requireOK(stateless_v2_cancel(handle))
    }
    public func resetCancellation() throws { try withCall { try requireOK(stateless_v2_cancel_reset($0)) } }
    fileprivate func imported(_ session: UInt64, _ kind: UInt32, _ value: Any) throws -> UInt64 {
        let token = try context.retain(kind, value)
        var handle: UInt64 = 0
        let status = stateless_v2_value_import(session, kind, token, &handle)
        if status != 0 { context.roots.removeValue(forKey: token); throw engineError(status) }
        return handle
    }
    fileprivate func result(_ session: UInt64, _ call: (UnsafeMutablePointer<UInt64>) -> Int32) throws -> NativeResult<S, I, O> {
        var handle: UInt64 = 0
        let status = call(&handle)
        // Record's model-error prefix is evidence and must remain disposable.
        if handle != 0 && ([0, 1, 2, 3, 11] as [Int32]).contains(status) { return NativeResult(session: self, handle: handle, status: status) }
        let error = engineError(status)
        if handle != 0 { _ = stateless_v2_result_release(session, handle) }
        throw error
    }
    public func checkInitial(_ state: S) throws -> [Check] {
        let result = try withCall { session in
            let value = try imported(session, 1, context.model.cloneState(state)); defer { _ = stateless_v2_value_release(session, value) }
            return try self.result(session) { stateless_v2_check_initial(session, value, $0) }
        }
        defer { try? result.close() }; return try result.checks().map(\.check)
    }
    fileprivate func observed<T>(_ session: UInt64, before: S, input: I, transition: Transition<S, O>,
                                 _ body: (UInt64, UInt64, UInt64, UnsafePointer<UInt64>?, UInt64, UInt32, UnsafePointer<UInt8>?, UInt64) throws -> T) throws -> T {
        var values: [UInt64] = []
        defer { for value in values { _ = stateless_v2_value_release(session, value) } }
        let beforeValue = try imported(session, 1, context.model.cloneState(before)); values.append(beforeValue)
        let inputValue = try imported(session, 2, context.model.cloneInput(input)); values.append(inputValue)
        let afterValue = try imported(session, 1, context.model.cloneState(transition.state)); values.append(afterValue)
        var outputs: [UInt64] = []
        for value in transition.outputs {
            let output = try imported(session, 3, context.model.cloneOutput(value)); values.append(output); outputs.append(output)
        }
        let (code, reason) = dispositionParts(transition.disposition)
        return try outputs.withUnsafeBufferPointer { buffer in
            try span(reason) { try body(beforeValue, inputValue, afterValue, buffer.baseAddress, UInt64(buffer.count), code, $0, $1) }
        }
    }
    public func checkObserved(before: S, input: I, transition: Transition<S, O>, sequence: UInt64, policy: CheckPolicy = CheckPolicy()) throws -> [Check] {
        let result = try withCall { session in
            try observed(session, before: before, input: input, transition: transition) { before, input, after, outputs, count, code, reason, length in
                try self.result(session) { stateless_v2_check_observed(session, before, input, after, outputs, count, code, reason, length, sequence, policy.stateEvery, policy.transitionChecks ? 1 : 0, $0) }
            }
        }
        defer { try? result.close() }; return try result.checks().map(\.check)
    }
    public func enumerate(maxStates: UInt64 = 100_000, maxTransitions: UInt64 = 1_000_000, maxDepth: UInt64 = 100,
                          maxStateBytes: UInt64 = .max, maxDurationMilliseconds: UInt64 = 0) throws -> NativeResult<S, I, O> {
        try withCall { session in try result(session) { stateless_v2_enumerate(session, maxStates, maxTransitions, maxDepth, maxStateBytes, maxDurationMilliseconds, $0) } }
    }
    public func recordBinary(inputs: InputIterator<I>, maxSteps: UInt64 = 100_000) throws -> NativeResult<S, I, O> {
        try withCall { session in
            let cursor = try imported(session, 4, CursorRoot(inputs)); defer { _ = stateless_v2_value_release(session, cursor) }
            return try result(session) { stateless_v2_record_cursor(session, cursor, maxSteps, $0) }
        }
    }
    public func record(inputs: InputIterator<I>, maxSteps: UInt64 = 100_000, limits: TraceLimits = TraceLimits()) throws -> Trace {
        for limit in [limits.maxSteps, limits.maxBlobBytes, limits.maxItems, limits.maxPayloadBytes, limits.maxJSONBytes] {
            guard limit >= 0, limit <= Int.max / 2 else { throw ConfigError("invalid trace limit") }
        }
        let result = try withCall { session in
            let cursor = try imported(session, 4, CursorRoot(inputs)); defer { _ = stateless_v2_value_release(session, cursor) }
            return try self.result(session) {
                stateless_v2_record_cursor_limited(session, cursor, min(maxSteps, UInt64(limits.maxSteps)),
                    UInt64(limits.maxBlobBytes), UInt64(limits.maxItems), UInt64(limits.maxPayloadBytes), $0)
            }
        }
        defer { try? result.close() }
        return try portableTrace(artifact: result.bytes(), limits: limits)
    }
    public func portableTrace(artifact: [UInt8], limits: TraceLimits = TraceLimits()) throws -> Trace {
        let converted = try convert(artifact, format: 1); defer { try? converted.close() }
        return try decodePacket(converted.bytes(), limits: limits)
    }
    fileprivate func convert(_ bytes: [UInt8], format: UInt32) throws -> NativeResult<S, I, O> {
        try withCall { session in try span(bytes) { pointer, count in
            try result(session) { stateless_v2_trace_convert(session, format, pointer, count, $0) }
        } }
    }
    public func replayBinary(_ artifact: [UInt8], allowBuildMismatch: Bool = false) throws -> NativeResult<S, I, O> {
        try withCall { session in try span(artifact) { pointer, count in
            try result(session) { stateless_v2_replay(session, pointer, count, allowBuildMismatch ? 1 : 0, $0) }
        } }
    }
    public func replay(_ trace: Trace, allowBuildMismatch: Bool = false, limits: TraceLimits = TraceLimits()) throws -> NativeReplayReport {
        let converted = try convert(encodePacket(trace, limits: limits), format: 2); defer { try? converted.close() }
        let result = try replayBinary(converted.bytes(), allowBuildMismatch: allowBuildMismatch); defer { try? result.close() }
        return try result.replayReport()
    }
    public func fuzz(seed: UInt64, cases: UInt64 = 1_000, maxSteps: UInt64 = 100, maxTransitions: UInt64 = 1_000_000,
                     mutationPercent: UInt32 = 0, maxDurationMilliseconds: UInt64 = 0) throws -> NativeResult<S, I, O> {
        try withCall { session in try result(session) { stateless_v2_fuzz(session, seed, cases, maxSteps, maxTransitions, mutationPercent, maxDurationMilliseconds, $0) } }
    }
    public func shrink(_ failure: NativeResult<S, I, O>, maxAttempts: UInt64 = 10_000, maxTransitions: UInt64 = 1_000_000,
                       maxDurationMilliseconds: UInt64 = 0) throws -> NativeResult<S, I, O> {
        try withCall { session in
            guard failure.session === self, failure.handle != 0 else { throw NativeError("failure belongs to another or closed session", status: 14) }
            return try result(session) { stateless_v2_shrink(session, failure.handle, maxAttempts, maxTransitions, maxDurationMilliseconds, $0) }
        }
    }
    public func ring(initialState: S, maxSteps: UInt64 = 1_000, maxRetainedBytes: UInt64 = 32 * 1024 * 1024) throws -> NativeRing<S, I, O> {
        try withCall { session in
            let state = try imported(session, 1, context.model.cloneState(initialState)); defer { _ = stateless_v2_value_release(session, state) }
            var ring: UInt64 = 0; try requireOK(stateless_v2_ring_new(session, state, maxSteps, maxRetainedBytes, &ring))
            return NativeRing(session: self, handle: ring)
        }
    }
    fileprivate func copiedInput(_ session: UInt64, _ value: UInt64) throws -> I {
        var kind: UInt32 = 0, token: UInt64 = 0
        try requireOK(stateless_v2_value_host_token(session, value, &kind, &token))
        guard kind == 2 else { throw NativeError("result input has wrong kind") }
        return try context.input(token)
    }
}

public struct NativeCheck {
    public let check: Check
    /// 0 unspecified, 1 initial, 2 state, 3 transition.
    public let phase: UInt32
}
public struct NativeReplayReport {
    public let outcome: String
    public let stepsVerified: UInt64
    public let failureReproduced: Bool
    public let buildMatches: Bool
    public let step: UInt64?
    public let field: String?
}
public enum NativeResultField: UInt32 {
    case status = 1, kind, termination, states, transitions, depth, skipped, estimatedBytes, hasEstimate
    case checkCount, inputCount, byteCount, stateCheckCount, stepsVerified, buildMatches, failureReproduced
    case cases, attempts, validated, hasDivergenceStep, divergenceStep
}
/// Owns Rust result data. Values returned by checks/inputs/bytes are owned Swift
/// snapshots and survive disposal. Closing the session also invalidates this result.
public final class NativeResult<S, I, O> {
    fileprivate let session: NativeSession<S, I, O>
    fileprivate var handle: UInt64
    public let status: Int32
    fileprivate init(session: NativeSession<S, I, O>, handle: UInt64, status: Int32) { self.session = session; self.handle = handle; self.status = status }
    deinit { if Thread.current === session.owner { try? close() } }
    private func call<T>(_ body: (UInt64, UInt64) throws -> T) throws -> T {
        try session.withCall { session in
            guard handle != 0 else { throw NativeError("result is closed", status: 14) }
            return try body(session, handle)
        }
    }
    public func close() throws {
        if handle == 0 { return }
        if session.handle == 0 { handle = 0; return }
        try call { session, result in try requireOK(stateless_v2_result_release(session, result)); handle = 0 }
    }
    public func scalar(_ field: NativeResultField) throws -> UInt64 {
        try call { session, result in var value: UInt64 = 0; try requireOK(stateless_v2_result_scalar(session, result, field.rawValue, &value)); return value }
    }
    private func copy(_ field: UInt32, _ index: UInt64 = 0) throws -> [UInt8] {
        try call { session, result in
            var length: UInt64 = 0
            let status = stateless_v2_result_copy(session, result, field, index, nil, 0, &length)
            guard status == 0 || status == 18 else { throw engineError(status) }
            guard length <= UInt64(Int.max) else { throw NativeError("result cannot fit in host memory", status: 18) }
            var bytes = [UInt8](repeating: 0, count: Int(length))
            try bytes.withUnsafeMutableBufferPointer { try requireOK(stateless_v2_result_copy(session, result, field, index, $0.baseAddress, length, &length)) }
            return bytes
        }
    }
    private func text(_ field: UInt32, _ index: UInt64 = 0) throws -> String {
        let bytes = try copy(field, index)
        guard String(bytes: bytes, encoding: .utf8) != nil else { throw NativeError("invalid UTF-8 result") }
        return String(decoding: bytes, as: UTF8.self)
    }
    public func bytes() throws -> [UInt8] { try copy(1) }
    public func diagnostic() throws -> String { try text(4) }
    public func checks() throws -> [NativeCheck] {
        let count = try scalar(.checkCount)
        var checks: [NativeCheck] = []
        for index in 0..<count {
            let (status, phase) = try call { session, result in
                var status: UInt32 = 0, phase: UInt32 = 0
                try requireOK(stateless_v2_result_check(session, result, index, &status, &phase)); return (status, phase)
            }
            guard status <= 2 else { throw NativeError("invalid check status") }
            checks.append(NativeCheck(check: Check(try text(2, index), status == 0 ? .passed : status == 1 ? .failed : .skipped, try text(3, index)), phase: phase))
        }
        return checks
    }
    public func inputs() throws -> [I] {
        let count = try scalar(.inputCount)
        return try (0..<count).map { index in try call { sessionHandle, result in
            var value: UInt64 = 0
            try requireOK(stateless_v2_result_input(sessionHandle, result, index, &value))
            defer { _ = stateless_v2_value_release(sessionHandle, value) }
            return try session.copiedInput(sessionHandle, value)
        } }
    }
    public func replayReport() throws -> NativeReplayReport {
        guard try scalar(.kind) == 4 else { throw NativeError("result is not a replay report") }
        let field = try text(5)
        return NativeReplayReport(outcome: status == 3 ? "incompatible" : status == 2 ? "diverged" : "exact",
                                  stepsVerified: try scalar(.stepsVerified), failureReproduced: try scalar(.failureReproduced) != 0,
                                  buildMatches: try scalar(.buildMatches) != 0,
                                  step: try scalar(.hasDivergenceStep) == 0 ? nil : scalar(.divergenceStep), field: field.isEmpty ? nil : field)
    }
}

/// Bounded observed-evidence recorder. observe checks supplied transitions and
/// does not invoke the model reducer. Snapshot is a binary Rust trace result.
public final class NativeRing<S, I, O> {
    private let session: NativeSession<S, I, O>
    private var handle: UInt64
    fileprivate init(session: NativeSession<S, I, O>, handle: UInt64) { self.session = session; self.handle = handle }
    deinit { if Thread.current === session.owner { try? close() } }
    public func close() throws {
        if handle == 0 { return }; if session.handle == 0 { handle = 0; return }
        try session.withCall { try requireOK(stateless_v2_ring_release($0, handle)); handle = 0 }
    }
    public func observe(before: S, input: I, transition: Transition<S, O>) throws -> [Check] {
        let result = try session.withCall { sessionHandle in
            guard handle != 0 else { throw NativeError("ring is closed", status: 14) }
            return try session.observed(sessionHandle, before: before, input: input, transition: transition) { before, input, after, outputs, count, code, reason, length in
                try session.result(sessionHandle) { stateless_v2_ring_observe(sessionHandle, handle, before, input, after, outputs, count, code, reason, length, $0) }
            }
        }
        defer { try? result.close() }; return try result.checks().map(\.check)
    }
    public func snapshot() throws -> NativeResult<S, I, O> {
        try session.withCall { sessionHandle in
            guard handle != 0 else { throw NativeError("ring is closed", status: 14) }
            return try session.result(sessionHandle) { stateless_v2_ring_snapshot(sessionHandle, handle, $0) }
        }
    }
}

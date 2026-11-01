/// Native model checking. No Rust/C/Wasm runtime or third-party packages.
public let statelessnessVersion = "0.1.0"
public let specificationVersion = "1.0.0"
public let conformanceProfiles = ["core-v1", "bfs-v1", "rng-splitmix64-v1", "trace-json-v1"]
/// Swift-only experimental extensions (S1-S5 of the Illiquid migration plan).
/// They have no cross-language qualification and are deliberately not
/// advertised in `conformanceProfiles`; see spec/graph-search-v1.md,
/// progress-analysis-v1.md, reduction-v1.md and continued-failure-evidence-v1.md.
public let swiftExtensionProfiles = ["graph-search-v1", "progress-analysis-v1", "reduction-v1", "continued-failure-evidence-v1"]

public struct ModelError: Error, CustomStringConvertible {
    public let description: String
    let snapshotFailure: Bool
    public init(_ message: String) { description = message; snapshotFailure = false }
    init(_ message: String, snapshotFailure: Bool) { description = message; self.snapshotFailure = snapshotFailure }
}
public struct ConfigError: Error, CustomStringConvertible {
    public let description: String
    public init(_ message: String) { description = message }
}
/// Swift's normal String equality is canonically equivalent, not scalar-exact.
/// Normative check/identity text must instead compare its exact UTF-8 sequence.
public func exactText(_ left: String, _ right: String) -> Bool {
    left.utf8.elementsEqual(right.utf8)
}
public struct ExactString: Hashable, Sendable {
    public let value: String
    public init(_ value: String) { self.value = value }
    public static func == (lhs: Self, rhs: Self) -> Bool { exactText(lhs.value, rhs.value) }
    public func hash(into hasher: inout Hasher) { for byte in value.utf8 { hasher.combine(byte) } }
}
public enum CheckStatus: String, Sendable { case passed, failed, skipped }
public struct Check: Equatable, Sendable {
    public let id: String
    public let status: CheckStatus
    public let details: String
    public init(_ id: String, _ status: CheckStatus = .passed, _ details: String = "") {
        self.id = id; self.status = status; self.details = details
    }
    public static func passed(_ id: String) -> Self { Self(id) }
    public static func failed(_ id: String, _ details: String) -> Self { Self(id, .failed, details) }
    public static func skipped(_ id: String, _ reason: String) -> Self { Self(id, .skipped, reason) }
    public func validate() throws { if status == .passed && !details.isEmpty { throw ModelError("passing checks have no details") } }
    public static func == (a: Self, b: Self) -> Bool {
        exactText(a.id, b.id) && a.status == b.status && exactText(a.details, b.details)
    }
}
public enum Disposition: Equatable, Sendable {
    case accepted
    case rejected(String)
    case ignored(String)
    public static func == (a: Self, b: Self) -> Bool {
        switch (a, b) {
        case (.accepted, .accepted): return true
        case let (.rejected(x), .rejected(y)), let (.ignored(x), .ignored(y)): return exactText(x, y)
        default: return false
        }
    }
}
public struct Transition<State, Output> {
    public let state: State
    public let outputs: [Output]
    public let disposition: Disposition
    public init(state: State, outputs: [Output] = [], disposition: Disposition = .accepted) {
        self.state = state; self.outputs = outputs; self.disposition = disposition
    }
}
public struct Metadata: Equatable, Sendable {
    public let name: String
    public let modelVersion: UInt32
    public let propertiesVersion: UInt32
    public let codecVersion: UInt32
    public let build: String
    public init(name: String, modelVersion: UInt32 = 1, propertiesVersion: UInt32 = 1, codecVersion: UInt32 = 1, build: String = "unqualified") {
        self.name = name; self.modelVersion = modelVersion; self.propertiesVersion = propertiesVersion; self.codecVersion = codecVersion; self.build = build
    }
    public static func == (a: Self, b: Self) -> Bool {
        exactText(a.name, b.name) && a.modelVersion == b.modelVersion && a.propertiesVersion == b.propertiesVersion && a.codecVersion == b.codecVersion && exactText(a.build, b.build)
    }
}
/// A single-use, synchronous iterator supporting lazy domains and callback errors.
public struct InputIterator<Value> {
    public let next: () throws -> Value?
    public init(_ next: @escaping () throws -> Value?) { self.next = next }
    public init(values: [Value]) {
        var index = 0
        self.next = { guard index < values.count else { return nil }; defer { index += 1 }; return values[index] }
    }
}
public struct Codec<State, Input, Output> {
    public var metadata: () throws -> Metadata
    public var encodeState: (State) throws -> [UInt8]
    public var decodeState: ([UInt8]) throws -> State
    public var encodeInput: (Input) throws -> [UInt8]
    public var decodeInput: ([UInt8]) throws -> Input
    public var encodeOutput: (Output) throws -> [UInt8]
    public var encodeStateInto: ((State, EncodeBuffer) throws -> Void)?
    public var encodeInputInto: ((Input, EncodeBuffer) throws -> Void)?
    public var encodeOutputInto: ((Output, EncodeBuffer) throws -> Void)?
    public init(metadata: @escaping () throws -> Metadata,
                encodeState: @escaping (State) throws -> [UInt8], decodeState: @escaping ([UInt8]) throws -> State,
                encodeInput: @escaping (Input) throws -> [UInt8], decodeInput: @escaping ([UInt8]) throws -> Input,
                encodeOutput: @escaping (Output) throws -> [UInt8],
                encodeStateInto: ((State, EncodeBuffer) throws -> Void)? = nil,
                encodeInputInto: ((Input, EncodeBuffer) throws -> Void)? = nil,
                encodeOutputInto: ((Output, EncodeBuffer) throws -> Void)? = nil) {
        self.metadata = metadata; self.encodeState = encodeState; self.decodeState = decodeState
        self.encodeInput = encodeInput; self.decodeInput = decodeInput; self.encodeOutput = encodeOutput
        self.encodeStateInto = encodeStateInto; self.encodeInputInto = encodeInputInto; self.encodeOutputInto = encodeOutputInto
    }
}
/// Applications retain their own types. Copy closures must deeply copy mutable
/// references; `{ $0 }` is appropriate only for immutable/value-only types.
/// Callbacks are deterministic, synchronous, and not a sandbox. Swift traps are
/// process failures, not catchable throwing errors or property findings.
public struct Model<State, Input, Output> {
    public var initialState: () throws -> State
    public var step: (State, Input) throws -> Transition<State, Output>
    public var checkState: (State) throws -> [Check]
    public var checkTransition: ((State, Input, Transition<State, Output>) throws -> [Check])?
    public var cloneState: (State) throws -> State
    public var cloneInput: (Input) throws -> Input
    public var cloneOutput: (Output) throws -> Output
    public var inputs: ((State) throws -> InputIterator<Input>)?
    public var equalStates: ((State, State) throws -> Bool)?
    public var hashState: ((State) throws -> UInt64)?
    public var codec: Codec<State, Input, Output>?
    /// Application estimate of retained state bytes, not process RSS. Unknown
    /// estimates are nil; callback allocations and engine bookkeeping are excluded.
    public var estimatedStateBytes: ((State) throws -> UInt64?)?
    public var checkStateInto: ((State, CheckSink) throws -> Void)?
    public var checkTransitionInto: ((State, Input, Transition<State, Output>, CheckSink) throws -> Void)?
    public init(initialState: @escaping () throws -> State,
                step: @escaping (State, Input) throws -> Transition<State, Output>,
                checkState: @escaping (State) throws -> [Check],
                cloneState: @escaping (State) throws -> State,
                cloneInput: @escaping (Input) throws -> Input,
                cloneOutput: @escaping (Output) throws -> Output,
                checkTransition: ((State, Input, Transition<State, Output>) throws -> [Check])? = nil,
                inputs: ((State) throws -> InputIterator<Input>)? = nil,
                equalStates: ((State, State) throws -> Bool)? = nil,
                hashState: ((State) throws -> UInt64)? = nil,
                codec: Codec<State, Input, Output>? = nil,
                estimatedStateBytes: ((State) throws -> UInt64?)? = nil,
                checkStateInto: ((State, CheckSink) throws -> Void)? = nil,
                checkTransitionInto: ((State, Input, Transition<State, Output>, CheckSink) throws -> Void)? = nil) {
        self.initialState = initialState; self.step = step; self.checkState = checkState
        self.cloneState = cloneState; self.cloneInput = cloneInput; self.cloneOutput = cloneOutput
        self.checkTransition = checkTransition; self.inputs = inputs; self.equalStates = equalStates
        self.hashState = hashState; self.codec = codec; self.estimatedStateBytes = estimatedStateBytes
        self.checkStateInto = checkStateInto; self.checkTransitionInto = checkTransitionInto
    }
}
func call<T>(_ stage: String, _ callback: () throws -> T) throws -> T {
    do { return try callback() } catch {
        if let error = error as? ModelError, error.snapshotFailure { throw ModelError("\(stage): \(error)", snapshotFailure: true) }
        throw ModelError("\(stage): \(error)")
    }
}
func copied<T>(_ clone: (T) throws -> T, _ value: T) throws -> T {
    do { return try clone(value) } catch { throw ModelError("snapshot: \(error)", snapshotFailure: true) }
}
func collect(_ stage: String, _ callback: () throws -> [Check]) throws -> [Check] {
    try call(stage) { let checks = try callback(); for check in checks { try check.validate() }; return checks }
}
func stateChecks<S, I, O>(_ m: Model<S, I, O>, _ state: S) throws -> [Check] {
    var checks: [Check] = []; try stateChecksInto(m, state, checks: &checks); return checks
}
func cloneTransition<S, I, O>(_ m: Model<S, I, O>, _ t: Transition<S, O>) throws -> Transition<S, O> {
    Transition(state: try copied(m.cloneState, t.state), outputs: try t.outputs.map { try copied(m.cloneOutput, $0) }, disposition: t.disposition)
}
func edgeChecks<S, I, O>(_ m: Model<S, I, O>, _ before: S, _ input: I, _ t: Transition<S, O>) throws -> [Check] {
    var checks: [Check] = []; try edgeChecksInto(m, before, input, t, checks: &checks); return checks
}
func perform<S, I, O>(_ m: Model<S, I, O>, _ state: S, _ input: I, stage: String = "transition") throws -> Transition<S, O> {
    let t = try call(stage) { try m.step(copied(m.cloneState, state), copied(m.cloneInput, input)) }
    return try cloneTransition(m, t)
}
public struct CheckPolicy: Sendable {
    public let stateEvery: UInt64
    public let transitionChecks: Bool
    public init(stateEvery: UInt64 = 1, transitionChecks: Bool = true) { self.stateEvery = stateEvery; self.transitionChecks = transitionChecks }
}
/// Checks already-observed data; never calls the reducer. Initial checks are separate.
public func checkObserved<S, I, O>(_ m: Model<S, I, O>, before: S, input: I, transition: Transition<S, O>, sequence: UInt64, policy: CheckPolicy = CheckPolicy()) throws -> [Check] {
    var checks: [Check] = []
    try checkObservedInto(m, before: before, input: input, transition: transition, sequence: sequence, policy: policy, checks: &checks)
    return checks
}
/// Reuses caller storage. Clears at entry and every error, preserving capacity.
/// This checks an already executed turn and never invokes the reducer.
public func checkObservedInto<S, I, O>(_ m: Model<S, I, O>, before: S, input: I, transition: Transition<S, O>, sequence: UInt64, policy: CheckPolicy = CheckPolicy(), checks: inout [Check]) throws {
    _ = try checkObservedCountInto(m, before: before, input: input, transition: transition, sequence: sequence, policy: policy, checks: &checks)
}
func checkObservedCountInto<S,I,O>(_ m: Model<S,I,O>, before: S, input: I, transition: Transition<S,O>, sequence: UInt64, policy: CheckPolicy = CheckPolicy(), checks: inout [Check]) throws -> Int {
    checks.removeAll(keepingCapacity: true)
    do {
        guard sequence > 0, policy.stateEvery > 0 else { throw ConfigError("sequence and state period must be positive") }
        if sequence % policy.stateEvery == 0 { try stateChecksInto(m, transition.state, checks: &checks) }
        else { checks.append(.skipped("stateless.state_checks", "periodic checking policy")) }
        let stateCheckCount = checks.count
        if policy.transitionChecks { try edgeChecksInto(m, before, input, transition, checks: &checks) }
        else { checks.append(.skipped("stateless.transition_checks", "disabled by policy")) }
        return stateCheckCount
    } catch { checks.removeAll(keepingCapacity: true); throw error }
}
func hasFailure(_ checks: [Check]) -> Bool { checks.contains { $0.status == .failed } }

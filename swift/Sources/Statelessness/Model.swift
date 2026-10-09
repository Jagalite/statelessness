/// Native model checking. No Rust/C/Wasm runtime or third-party packages.
public let statelessnessVersion = "0.1.0"
public let specificationVersion = "1.0.0"
public let conformanceProfiles = ["core-v1", "bfs-v1", "rng-splitmix64-v1", "trace-json-v1"]

public struct ModelError: Error, CustomStringConvertible {
    public let description: String
    public init(_ message: String) { description = message }
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
    public init(metadata: @escaping () throws -> Metadata,
                encodeState: @escaping (State) throws -> [UInt8], decodeState: @escaping ([UInt8]) throws -> State,
                encodeInput: @escaping (Input) throws -> [UInt8], decodeInput: @escaping ([UInt8]) throws -> Input,
                encodeOutput: @escaping (Output) throws -> [UInt8]) {
        self.metadata = metadata; self.encodeState = encodeState; self.decodeState = decodeState
        self.encodeInput = encodeInput; self.decodeInput = decodeInput; self.encodeOutput = encodeOutput
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
                codec: Codec<State, Input, Output>? = nil) {
        self.initialState = initialState; self.step = step; self.checkState = checkState
        self.cloneState = cloneState; self.cloneInput = cloneInput; self.cloneOutput = cloneOutput
        self.checkTransition = checkTransition; self.inputs = inputs; self.equalStates = equalStates
        self.hashState = hashState; self.codec = codec
    }
}
func call<T>(_ stage: String, _ callback: () throws -> T) throws -> T {
    do { return try callback() } catch { throw ModelError("\(stage): \(error)") }
}
func copied<T>(_ clone: (T) throws -> T, _ value: T) throws -> T { try call("snapshot") { try clone(value) } }
func collect(_ stage: String, _ callback: () throws -> [Check]) throws -> [Check] {
    try call(stage) { let checks = try callback(); for check in checks { try check.validate() }; return checks }
}
func stateChecks<S, I, O>(_ m: Model<S, I, O>, _ state: S) throws -> [Check] {
    try collect("state check") { try m.checkState(copied(m.cloneState, state)) }
}
func cloneTransition<S, I, O>(_ m: Model<S, I, O>, _ t: Transition<S, O>) throws -> Transition<S, O> {
    try call("snapshot") { Transition(state: try m.cloneState(t.state), outputs: try t.outputs.map(m.cloneOutput), disposition: t.disposition) }
}
func edgeChecks<S, I, O>(_ m: Model<S, I, O>, _ before: S, _ input: I, _ t: Transition<S, O>) throws -> [Check] {
    guard let callback = m.checkTransition else { return [] }
    return try collect("transition check") { try callback(copied(m.cloneState, before), copied(m.cloneInput, input), cloneTransition(m, t)) }
}
func perform<S, I, O>(_ m: Model<S, I, O>, _ state: S, _ input: I) throws -> Transition<S, O> {
    let t = try call("transition") { try m.step(copied(m.cloneState, state), copied(m.cloneInput, input)) }
    return try cloneTransition(m, t)
}
public struct CheckPolicy: Sendable {
    public let stateEvery: UInt64
    public let transitionChecks: Bool
    public init(stateEvery: UInt64 = 1, transitionChecks: Bool = true) { self.stateEvery = stateEvery; self.transitionChecks = transitionChecks }
}
/// Checks already-observed data; never calls the reducer. Initial checks are separate.
public func checkObserved<S, I, O>(_ m: Model<S, I, O>, before: S, input: I, transition: Transition<S, O>, sequence: UInt64, policy: CheckPolicy = CheckPolicy()) throws -> [Check] {
    guard sequence > 0, policy.stateEvery > 0 else { throw ConfigError("sequence and state period must be positive") }
    let sc = sequence % policy.stateEvery == 0 ? try stateChecks(m, transition.state) : [.skipped("stateless.state_checks", "periodic checking policy")]
    let ec = policy.transitionChecks ? try edgeChecks(m, before, input, transition) : [.skipped("stateless.transition_checks", "disabled by policy")]
    return sc + ec
}
func hasFailure(_ checks: [Check]) -> Bool { checks.contains { $0.status == .failed } }

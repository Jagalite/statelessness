public struct TraceStep: Equatable, Sendable {
    public let input: [UInt8]
    public let disposition: Disposition
    public let outputs: [[UInt8]]
    public let postState: [UInt8]
    public let checks: [Check]
    public init(input: [UInt8], disposition: Disposition, outputs: [[UInt8]], postState: [UInt8], checks: [Check]) {
        self.input = input; self.disposition = disposition; self.outputs = outputs; self.postState = postState; self.checks = checks
    }
}
public enum Termination: String, Sendable { case completed; case propertyFailed = "property_failed"; case stepLimit = "step_limit"; case interrupted; case modelError = "model_error" }
public struct Trace: Equatable, Sendable {
    public let metadata: Metadata
    public let initialState: [UInt8]
    public let initialChecks: [Check]
    public let steps: [TraceStep]
    public let termination: Termination
    public let error: String
    public init(metadata: Metadata, initialState: [UInt8], initialChecks: [Check], steps: [TraceStep], termination: Termination, error: String = "") {
        self.metadata = metadata; self.initialState = initialState; self.initialChecks = initialChecks; self.steps = steps; self.termination = termination; self.error = error
    }
    public static func == (a: Self, b: Self) -> Bool {
        a.metadata == b.metadata && a.initialState == b.initialState && a.initialChecks == b.initialChecks && a.steps == b.steps && a.termination == b.termination && exactText(a.error, b.error)
    }
}
/// Retained payload/item limits, not process-memory or hidden callback allocations.
public struct TraceLimits: Sendable {
    public var maxSteps = 100_000
    public var maxBlobBytes = 4 * 1024 * 1024
    public var maxItems = 250_000
    public var maxPayloadBytes = 32 * 1024 * 1024
    public var maxJSONBytes = 64 * 1024 * 1024
    public init() {}
    func validate() throws { for n in [maxSteps, maxBlobBytes, maxItems, maxPayloadBytes, maxJSONBytes] { if n < 0 || n > Int.max / 2 { throw ConfigError("invalid trace limit") } } }
}
public func record<S, I, O>(_ m: Model<S, I, O>, inputs: InputIterator<I>, maxSteps: Int = 100_000, limits: TraceLimits = TraceLimits()) throws -> Trace {
    guard maxSteps >= 0 else { throw ConfigError("negative step limit") }; try limits.validate()
    guard let codec = m.codec else { throw ModelError("record requires a codec") }
    // Evaluate throwing arguments separately. Swift 6.0.3 on Darwin otherwise
    // crashes its SIL ownership verifier when an initial-state error bypasses
    // cleanup of the generic clone closure. No verifier flags are disabled.
    let initialValue = try call("initial state", m.initialState)
    var state = try copied(m.cloneState, initialValue)
    var checkBuffer: [Check] = []
    try call("initial check") { try stateChecksInto(m, state, checks: &checkBuffer) }
    let initialChecks = checkBuffer
    let initialSnapshot = try copied(m.cloneState, state)
    let initialState = try encodedValue("encode initial state", codec.encodeState, codec.encodeStateInto, initialSnapshot, maximum: limits.maxBlobBytes)
    let metadata = try call("metadata", codec.metadata)
    var items = initialChecks.count, payload = initialState.count, steps: [TraceStep] = []
    guard items <= limits.maxItems, payload <= limits.maxPayloadBytes else { throw ModelError("recording limit: initial checkpoint") }
    func result(_ termination: Termination, _ error: String = "") -> Trace {
        Trace(metadata: metadata, initialState: initialState, initialChecks: initialChecks, steps: steps, termination: termination, error: error)
    }
    if hasFailure(initialChecks) { return result(.propertyFailed) }
    do {
        while let input = try call("inputs", inputs.next) {
            if steps.count >= min(maxSteps, limits.maxSteps) { return result(.stepLimit) }
            let inputSnapshot = try copied(m.cloneInput, input)
            let bytes = try encodedValue("encode input", codec.encodeInput, codec.encodeInputInto, inputSnapshot, maximum: limits.maxBlobBytes)
            let t = try perform(m, state, input)
            try checkObservedInto(m, before: state, input: input, transition: t, sequence: UInt64(steps.count + 1), checks: &checkBuffer)
            let checks = checkBuffer
            let nextItems = items + 1 + checks.count + t.outputs.count
            if nextItems > limits.maxItems { throw ModelError("recording limit: aggregate items") }
            let outputs = try t.outputs.map { output in
                let snapshot = try copied(m.cloneOutput, output)
                return try encodedValue("encode output", codec.encodeOutput, codec.encodeOutputInto, snapshot, maximum: limits.maxBlobBytes)
            }
            let nextSnapshot = try copied(m.cloneState, t.state)
            let postState = try encodedValue("encode state", codec.encodeState, codec.encodeStateInto, nextSnapshot, maximum: limits.maxBlobBytes)
            let nextPayload = payload + bytes.count + postState.count + outputs.reduce(0) { $0 + $1.count }
            if nextPayload > limits.maxPayloadBytes { throw ModelError("recording limit: aggregate payload bytes") }
            steps.append(TraceStep(input: bytes, disposition: t.disposition, outputs: outputs, postState: postState, checks: checks))
            state = t.state; items = nextItems; payload = nextPayload
            if hasFailure(checks) { return result(.propertyFailed) }
        }
        return result(.completed)
    } catch { return result(.modelError, String(describing: error)) }
}
public struct ReplayReport: Equatable, Sendable {
    public var outcome: String
    public var stepsVerified: Int
    public var failureReproduced: Bool
    public var buildMatches: Bool
    public var step: Int?
    public var field: String?
}
private func sameFailure(_ expected: [Check], _ actual: [Check]) -> Bool {
    expected.contains { e in e.status == .failed && actual.contains { a in a.status == .failed && exactText(e.id, a.id) } }
}
public func validateRecording(_ t: Trace) throws {
    if !t.error.isEmpty && t.termination != .modelError { throw ModelError("error text requires model_error termination") }
    let initialFailed = hasFailure(t.initialChecks)
    if initialFailed && !t.steps.isEmpty { throw ModelError("trace continues after initial property failure") }
    if t.steps.dropLast().contains(where: { hasFailure($0.checks) }) { throw ModelError("trace continues after property failure") }
    let failed = initialFailed || (t.steps.last.map { hasFailure($0.checks) } ?? false)
    if failed != (t.termination == .propertyFailed) { throw ModelError("trace termination disagrees with recorded checks") }
}
/// Restores saved checkpoints; InitialState is never called. Build overrides do
/// not waive model/property/codec identity. Exact verifies only the stored prefix.
public func replay<S, I, O>(_ m: Model<S, I, O>, trace: Trace, allowBuildMismatch: Bool = false) throws -> ReplayReport {
    do { return try replayBasic(m, trace: trace, allowBuildMismatch: allowBuildMismatch) }
    catch let error as ReplayError<S,I,O> { throw error.error }
}

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
private func encoded<T>(_ stage: String, _ callback: (T) throws -> [UInt8], _ value: T, maximum: Int = Int.max) throws -> [UInt8] {
    try call(stage) { let bytes = try callback(value); if bytes.count > maximum { throw ModelError("encoded blob exceeds byte limit") }; return bytes }
}
public func record<S, I, O>(_ m: Model<S, I, O>, inputs: InputIterator<I>, maxSteps: Int = 100_000, limits: TraceLimits = TraceLimits()) throws -> Trace {
    guard maxSteps >= 0 else { throw ConfigError("negative step limit") }; try limits.validate()
    guard let codec = m.codec else { throw ModelError("record requires a codec") }
    var state = try copied(m.cloneState, call("initial state", m.initialState))
    let initialChecks = try collect("initial check") { try m.checkState(copied(m.cloneState, state)) }
    let initialState = try encoded("encode initial state", codec.encodeState, copied(m.cloneState, state), maximum: limits.maxBlobBytes)
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
            let bytes = try encoded("encode input", codec.encodeInput, copied(m.cloneInput, input), maximum: limits.maxBlobBytes)
            let t = try perform(m, state, input)
            let checks = try checkObserved(m, before: state, input: input, transition: t, sequence: UInt64(steps.count + 1))
            let nextItems = items + 1 + checks.count + t.outputs.count
            if nextItems > limits.maxItems { throw ModelError("recording limit: aggregate items") }
            let outputs = try t.outputs.map { try encoded("encode output", codec.encodeOutput, copied(m.cloneOutput, $0), maximum: limits.maxBlobBytes) }
            let postState = try encoded("encode state", codec.encodeState, copied(m.cloneState, t.state), maximum: limits.maxBlobBytes)
            let nextPayload = payload + bytes.count + postState.count + outputs.reduce(0) { $0 + $1.count }
            if nextPayload > limits.maxPayloadBytes { throw ModelError("recording limit: aggregate payload bytes") }
            steps.append(TraceStep(input: bytes, disposition: t.disposition, outputs: outputs, postState: postState, checks: checks))
            state = t.state; items = nextItems; payload = nextPayload
            if hasFailure(checks) { return result(.propertyFailed) }
        }
        return result(.completed)
    } catch let error as ModelError { return result(.modelError, error.description) }
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
    guard let codec = m.codec else { throw ModelError("replay requires a codec") }
    let identity = try call("metadata", codec.metadata)
    var r = ReplayReport(outcome: "exact", stepsVerified: 0, failureReproduced: false, buildMatches: exactText(identity.build, trace.metadata.build), step: nil, field: nil)
    if !exactText(identity.name, trace.metadata.name) || identity.modelVersion != trace.metadata.modelVersion || identity.propertiesVersion != trace.metadata.propertiesVersion || identity.codecVersion != trace.metadata.codecVersion || (!r.buildMatches && !allowBuildMismatch) {
        r.outcome = "incompatible"; return r
    }
    try validateRecording(trace)
    var state = try call("decode initial state") { try codec.decodeState(trace.initialState) }
    if try encoded("encode initial state", codec.encodeState, copied(m.cloneState, state)) != trace.initialState { throw ModelError("initial state encoding is not canonical") }
    let initial = try collect("initial check") { try m.checkState(copied(m.cloneState, state)) }
    r.failureReproduced = sameFailure(trace.initialChecks, initial)
    if initial != trace.initialChecks { r.outcome = "diverged"; r.field = "initial checks"; return r }
    for (index, expected) in trace.steps.enumerated() {
        let input = try call("decode input") { try codec.decodeInput(expected.input) }
        if try encoded("encode input", codec.encodeInput, copied(m.cloneInput, input)) != expected.input { throw ModelError("input \(index + 1) encoding is not canonical") }
        let actual = try perform(m, state, input)
        let checks = try checkObserved(m, before: state, input: input, transition: actual, sequence: UInt64(index + 1))
        r.failureReproduced = r.failureReproduced || sameFailure(expected.checks, checks)
        let outputs = try actual.outputs.map { try encoded("encode output", codec.encodeOutput, copied(m.cloneOutput, $0)) }
        let postState = try encoded("encode state", codec.encodeState, copied(m.cloneState, actual.state))
        let field: String?
        if actual.disposition != expected.disposition { field = "disposition" }
        else if outputs != expected.outputs { field = "outputs" }
        else if postState != expected.postState { field = "state" }
        else if checks != expected.checks { field = "checks" }
        else { field = nil }
        if let field { r.outcome = "diverged"; r.step = index + 1; r.field = field; return r }
        r.stepsVerified += 1; state = actual.state
    }
    return r
}

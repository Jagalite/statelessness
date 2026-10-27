public struct ReplayDifferences: Equatable, Sendable {
    public let disposition: Bool
    public let outputs: Bool
    public let state: Bool
    public let checks: Bool
    public init(disposition: Bool = false, outputs: Bool = false, state: Bool = false, checks: Bool = false) {
        self.disposition = disposition; self.outputs = outputs; self.state = state; self.checks = checks
    }
    public var isEmpty: Bool { primary == nil }
    public var primary: String? { disposition ? "disposition" : outputs ? "outputs" : state ? "state" : checks ? "checks" : nil }
}
/// Recovery evidence owns the produced result. nil checks means unknown, never
/// an empty passing batch. This value is synchronous and must not cross tasks.
public enum ReplayActual<S, I, O> {
    case initial(state: S, checks: [Check]?)
    case turn(sequence: UInt64, before: S, input: I, transition: Transition<S, O>, checks: [Check]?, stateCheckCount: Int?)
}
public struct ReplayInitialObservation<S> {
    public let state: S
    public let checks: [Check]
    public let expectedState: [UInt8]
    public let actualState: [UInt8]
    public let expectedChecks: [Check]
    public let differences: ReplayDifferences
}
public struct ReplayTurnObservation<S, I, O> {
    public let sequence: UInt64
    public let before: S
    public let input: I
    public let transition: Transition<S, O>
    public let checks: [Check]
    public let stateCheckCount: Int
    public let expected: TraceStep
    public let actualInput: [UInt8]
    public let actualOutputs: [[UInt8]]
    public let actualState: [UInt8]
    public let differences: ReplayDifferences
}
public enum ReplayObservation<S, I, O> {
    case initial(ReplayInitialObservation<S>)
    case turn(ReplayTurnObservation<S, I, O>)
    case error(sequence: UInt64, actual: ReplayActual<S, I, O>?, error: Error, actualUnavailableReason: String?)
}
/// Swift Error requires Sendable conformance. Generic recovery values remain
/// synchronous caller-owned values; unchecked conformance grants no safe task
/// transfer. No Sendable constraints are imposed on application types.
public struct ReplayError<S, I, O>: Error, CustomStringConvertible, @unchecked Sendable {
    public let error: Error
    public let observerError: Error?
    public let report: ReplayReport
    public let actual: ReplayActual<S, I, O>?
    public var description: String { String(describing: error) }
}
private func replaySameFailure(_ expected: [Check], _ actual: [Check]) -> Bool {
    expected.contains { e in e.status == .failed && actual.contains { $0.status == .failed && exactText(e.id, $0.id) } }
}
private func replayCopyActual<S,I,O>(_ m: Model<S,I,O>, _ actual: ReplayActual<S,I,O>) throws -> ReplayActual<S,I,O> {
    switch actual {
    case let .initial(state, checks): return .initial(state: try copied(m.cloneState, state), checks: checks)
    case let .turn(sequence, before, input, transition, checks, count):
        return .turn(sequence: sequence, before: try copied(m.cloneState, before), input: try copied(m.cloneInput, input), transition: try cloneTransition(m, transition), checks: checks, stateCheckCount: count)
    }
}
public func replayWithObservations<S,I,O>(_ model: Model<S,I,O>, trace: Trace, allowBuildMismatch: Bool = false, observer: (ReplayObservation<S,I,O>, ReplayReport) throws -> Void) throws -> ReplayReport {
    try replayKernel(model, trace: trace, config: nil, limits: nil, allowBuildMismatch: allowBuildMismatch, observer: observer)
}
public func replayWithObservationsBounded<S,I,O>(_ model: Model<S,I,O>, trace: AuditTrace, allowBuildMismatch: Bool = false, limits: BinaryTraceLimits = BinaryTraceLimits(), observer: (ReplayObservation<S,I,O>, ReplayReport) throws -> Void) throws -> ReplayReport {
    try replayKernel(model, trace: trace.portable().trace, config: trace.config, limits: limits, allowBuildMismatch: allowBuildMismatch, observer: observer)
}
func replayBasic<S,I,O>(_ m: Model<S,I,O>, trace: Trace, allowBuildMismatch: Bool) throws -> ReplayReport {
    try replayKernel(m, trace: trace, config: nil, limits: nil, allowBuildMismatch: allowBuildMismatch, observe: false) { _, _ in }
}
private func replayKernel<S,I,O>(_ m: Model<S,I,O>, trace: Trace, config: RunConfig?, limits: BinaryTraceLimits?, allowBuildMismatch: Bool, observe: Bool = true, observer: (ReplayObservation<S,I,O>, ReplayReport) throws -> Void) throws -> ReplayReport {
    var report = ReplayReport(outcome: "exact", stepsVerified: 0, failureReproduced: false, buildMatches: false, step: nil, field: nil)
    func failed(_ error: Error, _ actual: ReplayActual<S,I,O>?, _ sequence: UInt64) -> ReplayError<S,I,O> {
        if !observe { return ReplayError(error: error, observerError: nil, report: report, actual: actual) }
        var secondary: Error?, observationActual: ReplayActual<S,I,O>?
        var unavailable: String?
        if (error as? ModelError)?.snapshotFailure == true { unavailable = "snapshot failed; original actual transferred to ReplayError" }
        if let actual, unavailable == nil {
            do { observationActual = try replayCopyActual(m, actual) }
            catch { secondary = ModelError("replay error observation snapshot: \(error)"); unavailable = "error observation snapshot failed; original actual transferred to ReplayError" }
        }
        do { try observer(.error(sequence: sequence, actual: observationActual, error: error, actualUnavailableReason: unavailable), report) }
        catch { secondary = secondary.map { ModelError("\($0); replay error observer: \(error)") } ?? error }
        return ReplayError(error: error, observerError: secondary, report: report, actual: actual)
    }
    guard let codec = m.codec else { throw failed(ModelError("replay requires a codec"), nil, 0) }
    let identity: Metadata
    do { identity = try call("metadata", codec.metadata) } catch { throw failed(error, nil, 0) }
    report.buildMatches = exactText(identity.build, trace.metadata.build)
    if !exactText(identity.name, trace.metadata.name) || identity.modelVersion != trace.metadata.modelVersion || identity.propertiesVersion != trace.metadata.propertiesVersion || identity.codecVersion != trace.metadata.codecVersion || (!report.buildMatches && !allowBuildMismatch) {
        report.outcome = "incompatible"; return report
    }
    do {
        if let limits, let config { try call("bounded replay artifact") { try auditPreflight(AuditTrace(portable: trace, config: config), limits) } }
        try validateRecording(trace)
    } catch { throw failed(error, nil, 0) }
    let maximum = limits.map(executionBlobLimit) ?? Int.max
    var state: S
    do { state = try call("decode initial state") { try codec.decodeState(trace.initialState) } }
    catch { throw failed(error, nil, 0) }
    var stateBytes: [UInt8] = [], inputBytes: [UInt8] = [], checks: [Check] = []
    var initialComplete = false, boundedBytes: UInt64 = 0, boundedItems: UInt64 = 0
    do {
        let snapshot = try copied(m.cloneState, state)
        try encodeValue("encode initial state", codec.encodeState, codec.encodeStateInto, snapshot, maximum: maximum, bytes: &stateBytes)
        guard stateBytes == trace.initialState else { throw ModelError("initial state encoding is not canonical") }
        try call("initial check") { try stateChecksInto(m, state, checks: &checks) }; initialComplete = true
        if let limits, let config {
            let size = try call("bounded replay initial") { try executionRunSize(identity, config, stateBytes, checks, limits) }
            boundedBytes = try auditAdd(12, size.bytes, "total bytes"); boundedItems = size.items
            try auditBound(try auditAdd(boundedBytes, 18, "total bytes"), limits.maxTotalBytes, "total bytes")
        }
        report.failureReproduced = replaySameFailure(trace.initialChecks, checks)
        if checks != trace.initialChecks { report.outcome = "diverged"; report.field = "initial checks" }
    } catch { throw failed(error, .initial(state: state, checks: initialComplete ? checks : nil), 0) }
    if observe {
    let initialObservation: ReplayInitialObservation<S>
    do {
        initialObservation = ReplayInitialObservation(state: try copied(m.cloneState, state), checks: checks, expectedState: trace.initialState, actualState: stateBytes, expectedChecks: trace.initialChecks, differences: ReplayDifferences(checks: checks != trace.initialChecks))
    } catch { throw failed(error, .initial(state: state, checks: checks), 0) }
    do { try observer(.initial(initialObservation), report) }
    catch { throw ReplayError(error: ModelError("replay observer: \(error)"), observerError: nil, report: report, actual: ReplayActual<S,I,O>.initial(state: state, checks: checks)) }
    }
    if report.outcome != "exact" { return report }
    for (index, expected) in trace.steps.enumerated() {
        let sequence = UInt64(index + 1), input: I, turn: Transition<S,O>
        do {
            input = try call("decode input") { try codec.decodeInput(expected.input) }
            let snapshot = try copied(m.cloneInput, input)
            try encodeValue("encode input", codec.encodeInput, codec.encodeInputInto, snapshot, maximum: maximum, bytes: &inputBytes)
            guard inputBytes == expected.input else { throw ModelError("input \(sequence) encoding is not canonical") }
        } catch { throw failed(error, nil, sequence) }
        let rawTurn: Transition<S,O>
        do { rawTurn = try call("transition") { try m.step(copied(m.cloneState, state), copied(m.cloneInput, input)) } }
        catch { throw failed(error, nil, sequence) }
        do { turn = try cloneTransition(m, rawTurn) }
        catch { throw failed(error, .turn(sequence: sequence, before: state, input: input, transition: rawTurn, checks: nil, stateCheckCount: nil), sequence) }
        var count: Int?, outputs: [[UInt8]] = []
        do {
            checks.removeAll(keepingCapacity: true)
            count = try checkObservedCountInto(m, before: state, input: input, transition: turn, sequence: sequence, checks: &checks)
            var remaining: UInt64?
            if let limits {
                let fixed = try executionStepFixed(inputBytes, turn.disposition, turn.outputs.count, checks, limits)
                boundedItems = try auditAdd(boundedItems, fixed.items, "aggregate items")
                try auditBound(boundedItems, limits.maxItems, "aggregate items")
                remaining = try executionRemaining(fixed.bytes, boundedBytes, footer: 18, limits: limits)
                boundedBytes = try auditAdd(boundedBytes, try auditAdd(fixed.bytes, 9, "total bytes"), "total bytes")
            }
            report.failureReproduced = report.failureReproduced || replaySameFailure(expected.checks, checks)
            outputs.reserveCapacity(turn.outputs.count)
            for output in turn.outputs {
                let snapshot = try copied(m.cloneOutput, output)
                let encoded = try encodedValue("encode output", codec.encodeOutput, codec.encodeOutputInto, snapshot, maximum: min(maximum, Int(min(remaining ?? UInt64(Int.max), UInt64(Int.max)))))
                outputs.append(encoded)
                if let available = remaining { remaining = available - UInt64(encoded.count); boundedBytes = try auditAdd(boundedBytes, UInt64(encoded.count), "total bytes") }
            }
            let nextSnapshot = try copied(m.cloneState, turn.state)
            try encodeValue("encode state", codec.encodeState, codec.encodeStateInto, nextSnapshot, maximum: min(maximum, Int(min(remaining ?? UInt64(Int.max), UInt64(Int.max)))), bytes: &stateBytes)
            if limits != nil { boundedBytes = try auditAdd(boundedBytes, UInt64(stateBytes.count), "total bytes") }
        } catch { throw failed(error, .turn(sequence: sequence, before: state, input: input, transition: turn, checks: count == nil ? nil : checks, stateCheckCount: count), sequence) }
        let differences = ReplayDifferences(disposition: turn.disposition != expected.disposition, outputs: outputs != expected.outputs, state: stateBytes != expected.postState, checks: checks != expected.checks)
        if let primary = differences.primary { report.outcome = "diverged"; report.step = index + 1; report.field = primary }
        else { report.stepsVerified += 1 }
        if observe {
        let observation: ReplayTurnObservation<S,I,O>
        do {
            observation = ReplayTurnObservation(sequence: sequence, before: try copied(m.cloneState, state), input: try copied(m.cloneInput, input), transition: try cloneTransition(m, turn), checks: checks, stateCheckCount: count!, expected: expected, actualInput: inputBytes, actualOutputs: outputs, actualState: stateBytes, differences: differences)
        } catch { throw failed(error, .turn(sequence: sequence, before: state, input: input, transition: turn, checks: checks, stateCheckCount: count), sequence) }
        do { try observer(.turn(observation), report) }
        catch { throw ReplayError(error: ModelError("replay observer: \(error)"), observerError: nil, report: report, actual: ReplayActual<S,I,O>.turn(sequence: sequence, before: state, input: input, transition: turn, checks: checks, stateCheckCount: count)) }
        }
        if !differences.isEmpty { return report }
        state = turn.state
    }
    return report
}

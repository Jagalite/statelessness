/// Swift-only extension `continued-failure-evidence-v1` (S5); see
/// spec/continued-failure-evidence-v1.md. A continued trace may record steps
/// after a failed check. It is a distinct versioned format: `validateRecording`,
/// `replay`, and `parseTrace` (trace-json v1) reject it rather than silently
/// reinterpreting a later failure as the end of a stop-at-failure trace.
public let continuedTraceFormat = "stateless.continued-trace-json"
public let continuedTraceVersion: UInt64 = 1
public struct ContinuedTrace: Equatable, Sendable {
    /// Termination is completed, step_limit, interrupted or model_error;
    /// property_failed is never used because failures do not end recording.
    public let trace: Trace
    public init(trace: Trace) { self.trace = trace }
    /// 0 for the initial state; otherwise 1-based steps whose checks failed.
    public var failingSequences: [UInt64] {
        (hasFailure(trace.initialChecks) ? [0] : []) + trace.steps.indices.filter { hasFailure(trace.steps[$0].checks) }.map { UInt64($0 + 1) }
    }
}
public func validateContinuedRecording(_ t: ContinuedTrace) throws {
    if !t.trace.error.isEmpty && t.trace.termination != .modelError { throw ModelError("error text requires model_error termination") }
    if t.trace.termination == .propertyFailed { throw ModelError("continued traces do not terminate on property failure") }
}
/// Records every input until the iterator ends, a step limit, or a model error,
/// continuing past failed checks. Checker errors after earlier failures yield
/// model_error with every completed step retained.
public func recordContinued<S, I, O>(_ m: Model<S, I, O>, inputs: InputIterator<I>, maxSteps: Int = 100_000, limits: TraceLimits = TraceLimits()) throws -> ContinuedTrace {
    ContinuedTrace(trace: try recordKernel(m, inputs: inputs, maxSteps: maxSteps, limits: limits, continued: true))
}
public struct ContinuedReplayReport: Equatable, Sendable {
    public var replay: ReplayReport
    public var recordedFailures: [UInt64]
    /// Recorded failing sequences whose replayed checks failed with an equal ID.
    public var reproducedFailures: [UInt64]
    /// Exact outcome, every recorded step verified (states, dispositions,
    /// outputs and checks), and every recorded failure reproduced. A verified
    /// prefix is never complete reproduction.
    public var completeReproduction: Bool
}
public func replayContinued<S, I, O>(_ m: Model<S, I, O>, trace: ContinuedTrace, allowBuildMismatch: Bool = false) throws -> ContinuedReplayReport {
    var reproduced: [UInt64] = []
    func same(_ expected: [Check], _ actual: [Check]) -> Bool {
        let failed = expected.filter { $0.status == .failed }
        return !failed.isEmpty && failed.allSatisfy { e in actual.contains { $0.status == .failed && exactText(e.id, $0.id) } }
    }
    let report: ReplayReport
    do {
        report = try replayKernel(m, trace: trace.trace, config: nil, limits: nil, allowBuildMismatch: allowBuildMismatch, validate: { try validateContinuedRecording(ContinuedTrace(trace: $0)) }) { (observation: ReplayObservation<S, I, O>, _) in
            switch observation {
            case let .initial(o): if same(o.expectedChecks, o.checks) { reproduced.append(0) }
            case let .turn(o): if same(o.expected.checks, o.checks) { reproduced.append(o.sequence) }
            case .error: break
            }
        }
    } catch let error as ReplayError<S, I, O> { throw error.error }
    let recorded = trace.failingSequences
    let complete = report.outcome == "exact" && report.stepsVerified == trace.trace.steps.count && reproduced == recorded
    return ContinuedReplayReport(replay: report, recordedFailures: recorded, reproducedFailures: reproduced, completeReproduction: complete)
}
public func encodeContinuedTrace(_ t: ContinuedTrace, limits: TraceLimits = TraceLimits()) throws -> [UInt8] {
    try validateContinuedRecording(t)
    guard case var .object(fields) = try parseJSON(try encodeTrace(t.trace, limits: limits), maximum: limits.maxJSONBytes) else { throw FormatError("expected trace object") }
    fields[ExactString("format")] = .string(continuedTraceFormat)
    fields[ExactString("version")] = .uint(continuedTraceVersion)
    fields[ExactString("failing_sequences")] = .array(t.failingSequences.map(JSON.uint))
    return try renderJSON(.object(fields), maximum: limits.maxJSONBytes)
}
public func parseContinuedTrace(_ bytes: [UInt8], limits: TraceLimits = TraceLimits()) throws -> ContinuedTrace {
    guard case var .object(fields) = try parseJSON(bytes, maximum: limits.maxJSONBytes) else { throw FormatError("expected object") }
    guard try readString(fields[ExactString("format")]) == continuedTraceFormat, try readInteger(fields[ExactString("version")]) == continuedTraceVersion else { throw FormatError("unsupported continued trace format") }
    let declared = try readArray(fields.removeValue(forKey: ExactString("failing_sequences")), maximum: limits.maxSteps + 1).map { try readInteger($0) }
    fields[ExactString("format")] = .string("stateless.trace-json"); fields[ExactString("version")] = .int(1)
    let t = ContinuedTrace(trace: try traceFromJSON(.object(fields), limits: limits))
    try validateContinuedRecording(t)
    guard declared == t.failingSequences else { throw FormatError("failing_sequences disagree with recorded checks") }
    return t
}

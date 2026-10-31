/// Serialized evidence limits; callback allocations and process RSS are outside
/// these bounds. Recorder instances require synchronous, exclusive access.
public struct RecorderOptions {
    public var maxSteps: Int
    public var maxRetainedBytes: UInt64
    public var limits: BinaryTraceLimits
    public init(maxSteps: Int = 1024, maxRetainedBytes: UInt64 = 67_108_864, limits: BinaryTraceLimits = BinaryTraceLimits()) {
        self.maxSteps = maxSteps; self.maxRetainedBytes = maxRetainedBytes; self.limits = limits
    }
}

/// A contiguous encoded window of application-executed transitions. Neither
/// reducers nor output effects run here. First failure freezes coherent evidence.
public final class Recorder {
    private let metadata: Metadata
    private let baseConfig: RunConfig
    private let options: RecorderOptions
    public let sequenceOrigin: UInt64
    private var initialState: [UInt8]
    private var initialChecks: [Check]
    private var mutationInProgress = false
    private var stopInProgress = false
    private var transferred = false
    private var entries: [(step: TraceStep, stateCount: Int)] = []
    public private(set) var observedSteps: UInt64 = 0
    public private(set) var evictedSteps: UInt64 = 0
    public private(set) var termination: AuditTermination
    public var retainedSteps: Int { entries.count }
    public var isFrozen: Bool { transferred || stopInProgress || termination != .interrupted }
    private var limits: BinaryTraceLimits { options.limits }
    private var encodingLimits: BinaryTraceLimits {
        var result = options.limits
        result.maxTotalBytes = min(result.maxTotalBytes, options.maxRetainedBytes)
        return result
    }
    private init<S,I,O>(model: Model<S,I,O>, state: S, checked: [Check]?, sequenceOrigin: UInt64, config: RunConfig, options: RecorderOptions) throws {
        guard options.maxSteps > 0, UInt64(options.maxSteps) <= options.limits.maxSteps else { throw ModelError("runtime recorder max_steps must be positive and within trace limits") }
        guard options.maxRetainedBytes > 0 else { throw ModelError("runtime recorder byte budget must be positive") }
        guard !config.parameters.contains(where: { $0.key.hasPrefix("stateless.monitor.") || $0.key.hasPrefix("stateless.engine.") }) else { throw ModelError("stateless.monitor. and stateless.engine. parameters are reserved") }
        guard let codec = model.codec else { throw ModelError("runtime recorder requires a codec") }
        self.options = options; self.sequenceOrigin = sequenceOrigin
        var parameters = config.parameters + [AuditParameter("stateless.engine.version", statelessnessVersion), AuditParameter("stateless.engine.build", "native-swift-v1")]
        if sequenceOrigin != 0 { parameters.append(AuditParameter("stateless.monitor.sequence_origin", String(sequenceOrigin))) }
        parameters += [AuditParameter("stateless.monitor.max_steps", String(options.maxSteps)), AuditParameter("stateless.monitor.max_retained_bytes", String(options.maxRetainedBytes)), AuditParameter("stateless.monitor.checking", "full")]
        self.baseConfig = RunConfig(strategy: config.strategy, seed: config.seed, parameters: parameters)
        let pristineCheckpoint = try copied(model.cloneState, state)
        self.metadata = try call("runtime metadata", codec.metadata)
        // Rust validates the reserved error footer after metadata, before any
        // checkpoint encoding/checking, even if that checkpoint will fail.
        try auditBound(13 + UInt64(executionErrorTextLimit(options.limits)), min(options.limits.maxFrameBytes, UInt64(UInt32.max)), "frame bytes")
        var effective = options.limits; effective.maxTotalBytes = min(effective.maxTotalBytes, options.maxRetainedBytes)
        let checkpoint = try copied(model.cloneState, pristineCheckpoint)
        self.initialState = try encodedValue("runtime encode checkpoint", codec.encodeState, codec.encodeStateInto, checkpoint, maximum: executionBlobLimit(effective))
        if let checked {
            var size = AuditSizer(limits: effective)
            let batchBytes = try size.checks(checked)
            try auditBound(batchBytes, min(effective.maxFrameBytes, UInt64(UInt32.max)), "frame bytes")
            try auditBound(batchBytes, options.maxRetainedBytes, "retained bytes")
            self.initialChecks = checked
        }
        else { self.initialChecks = try stateChecks(model, pristineCheckpoint) }
        self.termination = hasFailure(initialChecks) ? .propertyFailed : .interrupted
        try validateBudget(snapshot())
    }
    public convenience init<S,I,O>(model: Model<S,I,O>, state: S, config: RunConfig, options: RecorderOptions = RecorderOptions()) throws {
        do { try self.init(model: model, state: state, checked: nil, sequenceOrigin: 0, config: config, options: options) }
        catch {
            if case let BinaryTraceError.limitExceeded(name) = error { throw ModelError("runtime recording limit: trace exceeds \(name) limit") }
            throw ModelError("runtime recording: \(error)")
        }
    }
    public convenience init<S,I,O>(checkedInitial: CheckedInitial<S,I,O>, sequenceOrigin: UInt64 = 0, config: RunConfig, options: RecorderOptions = RecorderOptions()) throws {
        let state = try checkedInitial.state
        do { try self.init(model: checkedInitial.model, state: state, checked: checkedInitial.checks, sequenceOrigin: sequenceOrigin, config: config, options: options) }
        catch {
            if case let BinaryTraceError.limitExceeded(name) = error { throw ModelError("runtime recording limit: trace exceeds \(name) limit") }
            throw ModelError("runtime recording: \(error)")
        }
    }
    private func config(observed: UInt64, evicted: UInt64) -> RunConfig {
        RunConfig(strategy: baseConfig.strategy, seed: baseConfig.seed, parameters: baseConfig.parameters + [AuditParameter("stateless.monitor.observed_steps", String(observed)), AuditParameter("stateless.monitor.evicted_steps", String(evicted)), AuditParameter("stateless.monitor.checkpoint_after_sequence", String(evicted))])
    }
    public func snapshot() -> AuditTrace {
        AuditTrace(metadata: metadata, config: config(observed: observedSteps, evicted: evictedSteps), initialState: initialState, initialChecks: initialChecks, steps: entries.map { $0.step }, termination: termination)
    }
    /// Transfers this recorder exactly once. Read-only snapshots and counters
    /// remain available as a native extension; no later capture is permitted.
    public func intoTrace() throws -> AuditTrace {
        guard !transferred else { throw ModelError("runtime recorder has transferred its trace") }
        guard !mutationInProgress, !stopInProgress else { throw freezeWithError(ModelError("runtime recorder transfer is reentrant")) }
        let trace = snapshot()
        transferred = true
        return trace
    }
    public func writeWithLimits(_ limits: BinaryTraceLimits, to write: ([UInt8]) throws -> Void) throws { try writeAuditTrace(snapshot(), limits: limits, to: write) }
    public func writeTo(_ write: ([UInt8]) throws -> Void) throws { try writeWithLimits(limits, to: write) }
    public var retainedBytes: UInt64 { get throws { try wireSize(snapshot()) } }
    private func wireSize(_ trace: AuditTrace) throws -> UInt64 { try measure(trace).bytes }
    private func measure(_ trace: AuditTrace) throws -> (bytes: UInt64, items: UInt64) {
        // Rust measures each frame independently before testing cumulative
        // total bytes, items, step count and finally runtime retention limits.
        var frameLimits = limits; frameLimits.maxTotalBytes = UInt64.max
        let run = try executionRunSize(trace.metadata, trace.config, trace.initialState, trace.initialChecks, frameLimits)
        var bytes = try auditAdd(12, run.bytes, "total bytes"), items = run.items
        let size = AuditSizer(limits: frameLimits)
        for step in trace.steps {
            let fixed = try executionStepFixed(step.input, step.disposition, step.outputs.count, step.checks, frameLimits)
            var payload = try auditAdd(fixed.bytes, try size.blob(step.postState) - 4, "frame bytes")
            for output in step.outputs { payload = try auditAdd(payload, try size.blob(output) - 4, "frame bytes") }
            try auditBound(payload, min(limits.maxFrameBytes, UInt64(UInt32.max)), "frame bytes")
            bytes = try auditAdd(bytes, try auditAdd(payload, 9, "total bytes"), "total bytes")
            items = try auditAdd(items, fixed.items, "aggregate items")
        }
        var footer: UInt64 = 18
        if case let .modelError(reason) = trace.termination { footer = try auditAdd(18, try size.string(reason), "frame bytes") }
        try auditBound(footer - 9, min(limits.maxFrameBytes, UInt64(UInt32.max)), "frame bytes")
        return (try auditAdd(bytes, footer, "total bytes"), items)
    }
    private func validateBudget(_ trace: AuditTrace) throws {
        let measured = try measure(trace)
        var budgetBytes = measured.bytes
        if trace.termination == .interrupted {
            try auditBound(13 + UInt64(executionErrorTextLimit(limits)), min(limits.maxFrameBytes, UInt64(UInt32.max)), "frame bytes")
            budgetBytes = try auditAdd(budgetBytes - 18, 22 + UInt64(executionErrorTextLimit(limits)), "total bytes")
        }
        try auditBound(budgetBytes, limits.maxTotalBytes, "total bytes")
        try auditBound(measured.items, limits.maxItems, "aggregate items")
        try auditBound(UInt64(trace.steps.count), limits.maxSteps, "steps")
        guard budgetBytes <= options.maxRetainedBytes else { throw ModelError("runtime recording exceeds retained byte budget") }
        guard trace.steps.count <= options.maxSteps else { throw ModelError("runtime recording exceeds retained step budget") }
    }
    @discardableResult public func stopWithError(_ error: Error) throws -> Error {
        guard !transferred else { throw ModelError("runtime recorder has transferred its trace") }
        return freezeWithError(error)
    }
    private func freezeWithError(_ error: Error) -> Error {
        if !isFrozen {
            let previous = mutationInProgress
            mutationInProgress = true; stopInProgress = true
            defer { mutationInProgress = previous; stopInProgress = false }
            let reason = executionErrorText(error, limits)
            termination = .modelError(reason)
        }
        return error
    }
    private func engineError(_ error: Error) -> Error {
        if case let BinaryTraceError.limitExceeded(name) = error { return ModelError("runtime recording limit: trace exceeds \(name) limit") }
        if error is BinaryTraceError { return ModelError("runtime recording limit: \(error)") }
        return error
    }
    private func beginMutation() throws {
        guard !transferred else { throw ModelError("runtime recorder has transferred its trace") }
        guard !isFrozen else { throw ModelError("runtime recorder is frozen; export its retained trace") }
        guard !mutationInProgress else { throw freezeWithError(ModelError("runtime recorder mutation is reentrant")) }
        mutationInProgress = true
    }
    private func requireActive() throws {
        guard !isFrozen else { throw ModelError("runtime recorder froze during observation") }
    }
    @discardableResult public func observeChecked<S,I,O>(_ checked: CheckedTurn<S,I,O>) throws -> [Check] {
        try observeCheckedRetaining(checked, minimumEvictions: 0)
    }
    func retainedOutputCount(sequence: UInt64) -> Int? {
        let (checkpoint, overflow) = sequenceOrigin.addingReportingOverflow(evictedSteps)
        guard !overflow, sequence > checkpoint else { return nil }
        let position = sequence - checkpoint - 1
        guard position < UInt64(entries.count) else { return nil }
        return entries[Int(position)].step.outputs.count
    }
    @discardableResult func observeCheckedRetaining<S,I,O>(_ checked: CheckedTurn<S,I,O>, minimumEvictions: Int) throws -> [Check] {
        try beginMutation()
        defer { mutationInProgress = false }
        do {
            guard checked.fullChecking else { throw ModelError("runtime recorder requires full checking") }
            let (base, over1) = sequenceOrigin.addingReportingOverflow(observedSteps)
            let (next, over2) = base.addingReportingOverflow(1)
            guard !over1, !over2, next == checked.sequence else { throw ModelError("runtime checked observation sequence is missing or reordered") }
            let before = try checked.before; try requireActive()
            let input = try checked.input; try requireActive()
            let transition = try checked.transition; try requireActive()
            return try prepare(model: checked.model, before: before, input: input, transition: transition, checks: checked.checks, stateCount: checked.stateCheckCount, minimumEvictions: minimumEvictions)
        } catch { throw freezeWithError(engineError(error)) }
    }
    @discardableResult public func observe<S,I,O>(model: Model<S,I,O>, before: S, input: I, transition: Transition<S,O>) throws -> [Check] {
        try beginMutation()
        defer { mutationInProgress = false }
        do {
            let (observed, overflow) = observedSteps.addingReportingOverflow(1)
            guard !overflow, !sequenceOrigin.addingReportingOverflow(observed).overflow else {
                throw ModelError("runtime observation sequence overflow")
            }
            let capturedBefore = try copied(model.cloneState, before); try requireActive()
            let capturedInput = try copied(model.cloneInput, input); try requireActive()
            let capturedTransition = try cloneTransition(model, transition); try requireActive()
            return try prepare(model: model, before: capturedBefore, input: capturedInput, transition: capturedTransition, checks: nil, stateCount: nil)
        }
        catch { throw freezeWithError(engineError(error)) }
    }
    private func prepare<S,I,O>(model: Model<S,I,O>, before: S, input: I, transition: Transition<S,O>, checks certified: [Check]?, stateCount certifiedCount: Int?, minimumEvictions: Int = 0) throws -> [Check] {
        try requireActive()
        guard let codec = model.codec else { throw ModelError("runtime recorder requires a codec") }
        guard try call("runtime metadata", codec.metadata) == metadata else { throw ModelError("runtime recorder model identity changed") }
        try requireActive()
        let (observed, overflow) = observedSteps.addingReportingOverflow(1)
        guard !overflow else { throw ModelError("runtime observation sequence overflow") }
        let (_, absoluteOverflow) = sequenceOrigin.addingReportingOverflow(observed)
        guard !absoluteOverflow else { throw ModelError("runtime observation sequence overflow") }
        let maximum = executionBlobLimit(encodingLimits)
        let beforeSnapshot = try copied(model.cloneState, before); try requireActive()
        let beforeBytes = try encodedValue("runtime encode before state", codec.encodeState, codec.encodeStateInto, beforeSnapshot, maximum: maximum)
        try requireActive()
        guard beforeBytes == (entries.last?.step.postState ?? initialState) else { throw ModelError("runtime before state is discontinuous; observations are missing or reordered") }
        try auditBound(UInt64(transition.outputs.count), limits.maxOutputsPerStep, "outputs per step")
        try auditBound(UInt64(transition.outputs.count), limits.maxItems, "aggregate items")
        let fullFrameBudget = min(try auditAdd(min(limits.maxFrameBytes, UInt64(UInt32.max)), 9, "frame bytes"), limits.maxTotalBytes, options.maxRetainedBytes)
        guard fullFrameBudget >= 9 else { throw BinaryTraceError.limitExceeded("frame bytes") }
        let frameBudget = fullFrameBudget - 9
        let preliminaryPayloadBudget = min(limits.maxFrameBytes, UInt64(UInt32.max), limits.maxTotalBytes, options.maxRetainedBytes)
        try auditBound(UInt64(transition.outputs.count), preliminaryPayloadBudget / 4, "frame bytes")
        switch transition.disposition { case .accepted: break; case let .ignored(reason), let .rejected(reason): let s = AuditSizer(limits: limits); _ = try s.string(reason); try auditBound(UInt64(reason.utf8.count), preliminaryPayloadBudget, "frame bytes") }
        let inputSnapshot = try copied(model.cloneInput, input); try requireActive()
        let inputBytes = try encodedValue("runtime encode input", codec.encodeInput, codec.encodeInputInto, inputSnapshot, maximum: maximum)
        try requireActive()
        var checks: [Check] = [], stateCount: Int
        if let certified {
            var size = AuditSizer(limits: limits)
            try auditBound(try size.checks(certified), preliminaryPayloadBudget, "frame bytes")
            checks = certified; stateCount = certifiedCount!
        }
        else {
            try stateChecksInto(model, transition.state, checks: &checks); try requireActive(); stateCount = checks.count
            try edgeChecksInto(model, before, input, transition, checks: &checks); try requireActive()
        }
        let fixed = try executionStepFixed(inputBytes, transition.disposition, transition.outputs.count, checks, limits)
        try auditBound(fixed.bytes, frameBudget, "frame bytes")
        var remaining = frameBudget - fixed.bytes, outputs: [[UInt8]] = []
        for output in transition.outputs {
            let value = try copied(model.cloneOutput, output); try requireActive()
            let encoded = try encodedValue("runtime encode output", codec.encodeOutput, codec.encodeOutputInto, value, maximum: min(maximum, Int(min(remaining, UInt64(Int.max)))))
            try requireActive()
            remaining -= UInt64(encoded.count); outputs.append(encoded)
        }
        let state = try copied(model.cloneState, transition.state); try requireActive()
        let post = try encodedValue("runtime encode post state", codec.encodeState, codec.encodeStateInto, state, maximum: min(maximum, Int(min(remaining, UInt64(Int.max)))))
        try requireActive()
        let step = TraceStep(input: inputBytes, disposition: transition.disposition, outputs: outputs, postState: post, checks: checks)
        let end: AuditTermination = hasFailure(checks) ? .propertyFailed : .interrupted
        var lastError: Error = ModelError("runtime recording exceeds retention budget")
        guard minimumEvictions >= 0, minimumEvictions <= entries.count else { throw ModelError("invalid envelope retention request") }
        let minimum = max(minimumEvictions, entries.count == options.maxSteps ? 1 : 0)
        for count in minimum...entries.count {
            let checkpoint = count == 0 ? initialState : entries[count - 1].step.postState
            let checkpointChecks = count == 0 ? initialChecks : Array(entries[count - 1].step.checks.prefix(entries[count - 1].stateCount))
            let evicted = try auditAdd(evictedSteps, UInt64(count), "evicted steps")
            let candidate = AuditTrace(metadata: metadata, config: config(observed: observed, evicted: evicted), initialState: checkpoint, initialChecks: checkpointChecks, steps: entries.dropFirst(count).map { $0.step } + [step], termination: end)
            do { try validateBudget(candidate) } catch { lastError = error; continue }
            try requireActive()
            entries.removeFirst(count); initialState = checkpoint; initialChecks = checkpointChecks
            entries.append((step, stateCount)); observedSteps = observed; evictedSteps = evicted; termination = end
            return checks
        }
        throw lastError
    }
}

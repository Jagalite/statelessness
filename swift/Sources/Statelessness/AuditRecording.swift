/// Binary-equivalent bounded recording. Unlike portable record, configuration
/// and the nine wire quotas are retained throughout execution, not reconstructed
/// after unbounded codecs have run. Callback allocation is cooperative.
public func recordAudit<S,I,O>(_ m: Model<S,I,O>, inputs: InputIterator<I>, config: RunConfig, maxSteps: Int = 100_000, limits: BinaryTraceLimits = BinaryTraceLimits()) throws -> AuditTrace {
    guard maxSteps >= 0 else { throw ConfigError("negative step limit") }
    guard let codec = m.codec else { throw ModelError("record requires a codec") }
    guard !config.parameters.contains(where: { $0.key.utf8.starts(with: "stateless.engine.".utf8) }) else { throw ModelError("stateless.engine. parameters are reserved") }
    let config = RunConfig(strategy: config.strategy, seed: config.seed, parameters: config.parameters + [AuditParameter("stateless.engine.version", statelessnessVersion), AuditParameter("stateless.engine.build", "native-swift-v1")])
    let initialValue = try call("initial state", m.initialState)
    var state = try copied(m.cloneState, initialValue), checks: [Check] = []
    try call("initial check") { try stateChecksInto(m, state, checks: &checks) }
    let initialChecks = checks, maximum = executionBlobLimit(limits)
    let snapshot = try copied(m.cloneState, state)
    let initialState = try encodedValue("encode initial state", codec.encodeState, codec.encodeStateInto, snapshot, maximum: maximum)
    let metadata = try call("metadata", codec.metadata)
    let run = try executionRunSize(metadata, config, initialState, initialChecks, limits)
    var bytes = try auditAdd(12, run.bytes, "total bytes"), items = run.items, steps: [TraceStep] = []
    let failedInitial = hasFailure(initialChecks)
    let errorReserve = try auditAdd(22, UInt64(executionErrorTextLimit(limits)), "total bytes")
    try auditBound(13 + UInt64(executionErrorTextLimit(limits)), min(limits.maxFrameBytes, UInt64(UInt32.max)), "frame bytes")
    try auditBound(try auditAdd(bytes, failedInitial ? 18 : errorReserve, "total bytes"), limits.maxTotalBytes, "total bytes")
    func result(_ termination: AuditTermination) -> AuditTrace {
        AuditTrace(metadata: metadata, config: config, initialState: initialState, initialChecks: initialChecks, steps: steps, termination: termination)
    }
    if failedInitial { return result(.propertyFailed) }
    do {
        while let input = try call("inputs", inputs.next) {
            if UInt64(steps.count) >= min(UInt64(maxSteps), limits.maxSteps) { return result(.stepLimit) }
            // Reserve a terminal footer for the candidate; passing candidates
            // restore the larger future-error reserve before admission.
            let available = limits.maxTotalBytes > bytes + 18 ? limits.maxTotalBytes - bytes - 18 : 0
            let frameBudget = min(available, try auditAdd(min(limits.maxFrameBytes, UInt64(UInt32.max)), 9, "frame bytes"))
            let inputSnapshot = try copied(m.cloneInput, input)
            let encodedInput = try encodedValue("encode input", codec.encodeInput, codec.encodeInputInto, inputSnapshot, maximum: min(maximum, Int(min(frameBudget, UInt64(Int.max)))))
            let turn = try perform(m, state, input)
            try checkObservedInto(m, before: state, input: input, transition: turn, sequence: UInt64(steps.count + 1), checks: &checks)
            try auditBound(UInt64(turn.outputs.count), min(limits.maxOutputsPerStep, UInt64(UInt32.max)), "outputs per step")
            // Rust recording admits cumulative check + STEP + output items
            // before measuring the provisional frame or inspecting check text.
            let fixedItems = try auditAdd(try auditAdd(items, UInt64(checks.count), "aggregate items"), 1, "aggregate items")
            let nextItems = try auditAdd(fixedItems, UInt64(turn.outputs.count), "aggregate items")
            try auditBound(nextItems, limits.maxItems, "aggregate items")
            let fixed = try executionStepFixed(encodedInput, turn.disposition, 0, checks, limits)
            var remaining = try executionRemaining(fixed.bytes, bytes, footer: 18, limits: limits)
            var encodedOutputs: [[UInt8]] = []
            for output in turn.outputs {
                guard remaining >= 4 else { throw BinaryTraceError.limitExceeded("output frame bytes") }; remaining -= 4
                let snapshot = try copied(m.cloneOutput, output)
                let encoded = try encodedValue("encode output", codec.encodeOutput, codec.encodeOutputInto, snapshot, maximum: min(maximum, Int(min(remaining, UInt64(Int.max)))))
                remaining -= UInt64(encoded.count); encodedOutputs.append(encoded)
            }
            let nextSnapshot = try copied(m.cloneState, turn.state)
            let postState = try encodedValue("encode state", codec.encodeState, codec.encodeStateInto, nextSnapshot, maximum: min(maximum, Int(min(remaining, UInt64(Int.max)))))
            var size = fixed.bytes
            for output in encodedOutputs { size = try auditAdd(size, UInt64(output.count) + 4, "frame bytes") }
            size = try auditAdd(size, UInt64(postState.count), "frame bytes")
            let nextBytes = try auditAdd(bytes, try auditAdd(size, 9, "total bytes"), "total bytes")
            let failed = hasFailure(checks)
            try auditBound(try auditAdd(nextBytes, failed ? 18 : errorReserve, "total bytes"), limits.maxTotalBytes, "total bytes")
            steps.append(TraceStep(input: encodedInput, disposition: turn.disposition, outputs: encodedOutputs, postState: postState, checks: checks))
            bytes = nextBytes; items = nextItems; state = turn.state
            if failed { return result(.propertyFailed) }
        }
        return result(.completed)
    } catch { return result(.modelError(executionErrorText(error, limits))) }
}

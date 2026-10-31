private struct AutoScanInterrupted: Error {}
final class AutoScanControl {
    let control: RunControl
    var reason: String?
    init(_ control: RunControl) { self.control = control }
    func poll() throws {
        if let stop = control.stop() { reason = stop; throw AutoScanInterrupted() }
    }
}

/// Automatic finite-domain fuzzing with the run's cancellation and deadline
/// polled between iterator callbacks. A callback already running cannot be
/// interrupted. An incomplete reservoir scan does not commit its RNG state.
public func fuzzAuto64<S, I, O>(_ model: Model<S, I, O>, options: AutoOptions64 = AutoOptions64(), equalInputs: @escaping (I, I) throws -> Bool, config: FuzzConfig64 = FuzzConfig64(), limits: RunLimits = RunLimits(), observer: SearchObserver<S, I, O>? = nil) throws -> FuzzReport64<I> {
    guard options.maxCandidates > 0 else { throw ConfigError("automatic generation max_candidates must be positive") }
    try validateFuzzConfig(config)
    let control = try RunControl(limits), scan = AutoScanControl(control)
    let g = autoControlled(model, options: options, equalInputs: equalInputs, scan: scan)
    return try fuzzKernel(g, config: config, control: control, observer: observer, candidate: { previous, rng in
        guard !previous.isEmpty, rng.index(100)! < UInt64(config.mutationPercent) else { return ([], nil) }
        var mutated = try previous.map { try copied(model.cloneInput, $0) }
        let start = Int(rng.index(UInt64(mutated.count))!)
        let length = 1 + Int(rng.index(UInt64(mutated.count - start))!)
        mutated.removeSubrange(start..<(start + length))
        return (mutated, Int(rng.index(UInt64(mutated.count) + 1)!))
    }, scanStop: { scan.reason })
}

func autoControlled<S, I, O>(_ model: Model<S, I, O>, options: AutoOptions64, equalInputs: @escaping (I, I) throws -> Bool, scan: AutoScanControl) -> Generate<S, I, O> {
    Generate(model: model, generate: { state, rng in
        try sampleEnumeratedControlled(model, state: state, rng: &rng, options: options, poll: scan.poll)
    }, isEnabled: { state, input in
        try containsEnumeratedControlled(model, state: state, input: input, equalInputs: equalInputs, options: options, poll: scan.poll)
    }, equalInputs: equalInputs)
}

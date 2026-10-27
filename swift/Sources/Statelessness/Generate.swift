/// Explicit native generator capability. Callback values stay synchronous and
/// actor/thread confined; clone callbacks obey Model's deep snapshot contract.
public struct Generate<S, I, O> {
    public var model: Model<S, I, O>
    public var generate: (S, inout Rng) throws -> I?
    public var isEnabled: (S, I) throws -> Bool
    public var simplerInputs: (I) throws -> InputIterator<I>
    public var equalInputs: (I, I) throws -> Bool
    public init(model: Model<S, I, O>, generate: @escaping (S, inout Rng) throws -> I?,
                isEnabled: @escaping (S, I) throws -> Bool = { _, _ in true },
                simplerInputs: @escaping (I) throws -> InputIterator<I> = { _ in InputIterator(values: []) },
                equalInputs: @escaping (I, I) throws -> Bool) {
        self.model = model; self.generate = generate; self.isEnabled = isEnabled
        self.simplerInputs = simplerInputs; self.equalInputs = equalInputs
    }
}
public struct AutoOptions: Sendable {
    public let maxCandidates: Int
    public init(maxCandidates: Int = 100_000) { self.maxCandidates = maxCandidates }
}
/// Finite reservoir scan: retains one candidate and commits RNG only after the
/// whole domain succeeds. The first candidate consumes no random word.
public func sampleEnumerated<S, I, O>(_ model: Model<S, I, O>, state: S, rng: inout Rng, options: AutoOptions = AutoOptions()) throws -> I? {
    guard options.maxCandidates > 0 else { throw ConfigError("automatic generation max_candidates must be positive") }
    guard let inputs = model.inputs else { throw ModelError("automatic generation requires finite inputs") }
    let iterator = try call("automatic inputs") { try inputs(copied(model.cloneState, state)) }
    var candidateRng = rng, selected: I?, count = 0
    while let input = try call("automatic inputs next", iterator.next) {
        guard count < options.maxCandidates else { throw ModelError("automatic input domain exceeds max_candidates") }
        count += 1
        if count == 1 || candidateRng.index(UInt64(count)) == 0 { selected = try copied(model.cloneInput, input) }
    }
    rng = candidateRng
    return selected
}
public func containsEnumerated<S, I, O>(_ model: Model<S, I, O>, state: S, input: I, equalInputs: (I, I) throws -> Bool, options: AutoOptions = AutoOptions()) throws -> Bool {
    guard options.maxCandidates > 0 else { throw ConfigError("automatic generation max_candidates must be positive") }
    guard let inputs = model.inputs else { throw ModelError("automatic generation requires finite inputs") }
    let iterator = try call("automatic inputs") { try inputs(copied(model.cloneState, state)) }
    var count = 0
    while let candidate = try call("automatic inputs next", iterator.next) {
        guard count < options.maxCandidates else { throw ModelError("automatic input domain exceeds max_candidates") }
        count += 1
        if try call("input equality", { try equalInputs(copied(model.cloneInput, candidate), copied(model.cloneInput, input)) }) { return true }
    }
    return false
}
/// Explicit opt-in; ordinary Model instances retain their existing behavior.
public struct Auto<S, I, O> {
    public let model: Model<S, I, O>
    public let options: AutoOptions
    public let equalInputs: (I, I) throws -> Bool
    public init(_ model: Model<S, I, O>, options: AutoOptions = AutoOptions(), equalInputs: @escaping (I, I) throws -> Bool) throws {
        guard options.maxCandidates > 0 else { throw ConfigError("automatic generation max_candidates must be positive") }
        self.model = model; self.options = options; self.equalInputs = equalInputs
    }
    public var generated: Generate<S, I, O> {
        Generate(model: model, generate: { state, rng in try sampleEnumerated(self.model, state: state, rng: &rng, options: self.options) },
                 isEnabled: { state, input in try containsEnumerated(self.model, state: state, input: input, equalInputs: self.equalInputs, options: self.options) }, equalInputs: equalInputs)
    }
}

extension Generate {
    /// Preserve generation capabilities when adding independent replayable
    /// history. Generation and enabled checks see only actual application state.
    public func withOracle<H>(_ oracle: Oracle<S, H, I, O>) -> Generate<OracleState<S, H>, I, O> {
        let original = self, adapter = WithOracle(model, oracle: oracle)
        return Generate<OracleState<S, H>, I, O>(model: adapter.model,
            generate: { state, rng in try original.generate(copied(original.model.cloneState, state.model), &rng) },
            isEnabled: { state, input in try original.isEnabled(copied(original.model.cloneState, state.model), copied(original.model.cloneInput, input)) },
            simplerInputs: original.simplerInputs, equalInputs: original.equalInputs)
    }
}

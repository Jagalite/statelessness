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
public struct AutoOptions64: Sendable {
    public let maxCandidates: UInt64
    public init(maxCandidates: UInt64 = 100_000) { self.maxCandidates = maxCandidates }
}
/// Finite reservoir scan: retains one candidate and commits RNG only after the
/// whole domain succeeds. The first candidate consumes no random word.
public func sampleEnumerated64<S, I, O>(_ model: Model<S, I, O>, state: S, rng: inout Rng, options: AutoOptions64 = AutoOptions64()) throws -> I? {
    return try sampleEnumeratedControlled(model, state: state, rng: &rng, options: options, poll: {})
}
func sampleEnumeratedControlled<S, I, O>(_ model: Model<S, I, O>, state: S, rng: inout Rng, options: AutoOptions64, poll: () throws -> Void) throws -> I? {
    guard options.maxCandidates > 0 else { throw ConfigError("automatic generation max_candidates must be positive") }
    guard let inputs = model.inputs else { throw ModelError("automatic generation requires finite inputs") }
    let iterator = try call("automatic inputs") { try inputs(copied(model.cloneState, state)) }
    var candidateRng = rng, selected: I?
    var count: UInt64 = 0
    while true {
        try poll()
        guard let input = try call("automatic inputs next", iterator.next) else { break }
        guard count < options.maxCandidates else { throw ModelError("automatic input domain exceeds max_candidates") }
        count += 1
        if count == 1 || candidateRng.index(UInt64(count)) == 0 { selected = try copied(model.cloneInput, input) }
    }
    try poll()
    rng = candidateRng
    return selected
}
public func containsEnumerated64<S, I, O>(_ model: Model<S, I, O>, state: S, input: I, equalInputs: (I, I) throws -> Bool, options: AutoOptions64 = AutoOptions64()) throws -> Bool {
    return try containsEnumeratedControlled(model, state: state, input: input, equalInputs: equalInputs, options: options, poll: {})
}
func containsEnumeratedControlled<S, I, O>(_ model: Model<S, I, O>, state: S, input: I, equalInputs: (I, I) throws -> Bool, options: AutoOptions64, poll: () throws -> Void) throws -> Bool {
    guard options.maxCandidates > 0 else { throw ConfigError("automatic generation max_candidates must be positive") }
    guard let inputs = model.inputs else { throw ModelError("automatic generation requires finite inputs") }
    // Preserve the queried input before state clone or domain callbacks can mutate
    // an external alias. Each comparison still receives its own detached copy.
    // Defer copy errors until a candidate needs comparison, preserving empty
    // domain and iterator error behavior. The copy hook itself runs eagerly.
    let sought = Result { try copied(model.cloneInput, input) }
    let iterator = try call("automatic inputs") { try inputs(copied(model.cloneState, state)) }
    var count: UInt64 = 0
    while true {
        try poll()
        guard let candidate = try call("automatic inputs next", iterator.next) else { break }
        guard count < options.maxCandidates else { throw ModelError("automatic input domain exceeds max_candidates") }
        count += 1
        if try call("input equality", { let target = try sought.get(); return try equalInputs(copied(model.cloneInput, candidate), copied(model.cloneInput, target)) }) { try poll(); return true }
    }
    try poll()
    return false
}
/// Explicit opt-in; ordinary Model instances retain their existing behavior.
public struct Auto64<S, I, O> {
    public let model: Model<S, I, O>
    public let options: AutoOptions64
    public let equalInputs: (I, I) throws -> Bool
    public init(_ model: Model<S, I, O>, options: AutoOptions64 = AutoOptions64(), equalInputs: @escaping (I, I) throws -> Bool) throws {
        guard options.maxCandidates > 0 else { throw ConfigError("automatic generation max_candidates must be positive") }
        self.model = model; self.options = options; self.equalInputs = equalInputs
    }
    public var generated: Generate<S, I, O> {
        Generate(model: model, generate: { state, rng in try sampleEnumerated64(self.model, state: state, rng: &rng, options: self.options) },
                 isEnabled: { state, input in try containsEnumerated64(self.model, state: state, input: input, equalInputs: self.equalInputs, options: self.options) }, equalInputs: equalInputs)
    }
}

extension Generate {
    /// Preserve generation capabilities when adding independent replayable
    /// history. Generation and enabled checks see only actual application state.
    public func withOracle<H>(_ oracle: Oracle<S, H, I, O>) -> Generate<OracleState<S, H>, I, O> {
        let original = self, adapter = WithOracle(model, oracle: oracle)
        return Generate<OracleState<S, H>, I, O>(model: adapter.model,
            generate: { state, rng in try original.generate(copied(original.model.cloneState, state.model), &rng) },
            isEnabled: { state, input in
                // Keep the query stable before the state-copy hook runs.
                let query = try copied(original.model.cloneInput, input)
                return try original.isEnabled(copied(original.model.cloneState, state.model), query)
            },
            simplerInputs: original.simplerInputs, equalInputs: original.equalInputs)
    }
}

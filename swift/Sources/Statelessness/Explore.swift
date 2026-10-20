public struct SearchConfig: Sendable {
    public let maxStates: Int
    public let maxTransitions: UInt64
    public let maxDepth: Int
    public init(maxStates: Int = 100_000, maxTransitions: UInt64 = 1_000_000, maxDepth: Int = 100) {
        self.maxStates = maxStates; self.maxTransitions = maxTransitions; self.maxDepth = maxDepth
    }
}
/// Admission bounds reuse the existing cooperative RunLimits. Estimates cover
/// admitted model states only: not temporary successors, predecessor inputs,
/// frontier/maps, callback allocation, allocator overhead or process RSS.
public struct SearchLimits {
    public let control: RunLimits
    public let maxEstimatedStateBytes: UInt64?
    public init(control: RunLimits = RunLimits(), maxEstimatedStateBytes: UInt64? = nil) {
        self.control = control; self.maxEstimatedStateBytes = maxEstimatedStateBytes
    }
}
public struct Violation: Equatable, Sendable {
    public let phase: String; public let check: Check
    public init(phase: String, check: Check) { self.phase = phase; self.check = check }
}
public struct Failure<Input> {
    public let inputs: [Input]; public let violations: [Violation]
    public init(inputs: [Input], violations: [Violation]) { self.inputs = inputs; self.violations = violations }
}
public struct SearchReport<Input> {
    public var termination: String
    public var states: Int
    public var transitions: UInt64
    public var maxDepthReached: Int
    public var skippedChecks: UInt64
    public var estimatedRetainedStateBytes: UInt64? = nil
    public var failure: Failure<Input>?
}
private struct Node<S, I> { let state: S; let parent: Int; let input: I?; let depth: Int }
/// FIFO exploration with exact equality and cooperative admission limits.
/// A checked failure wins over a stop requested during the checking boundary.
public func enumerateStates<S, I, O>(_ m: Model<S, I, O>, config: SearchConfig = SearchConfig(), limits: SearchLimits = SearchLimits(), observer: SearchObserver<S, I, O>? = nil) throws -> SearchReport<I> {
    guard config.maxStates > 0 else { throw ConfigError("enumeration max_states must be greater than zero") }
    guard config.maxDepth >= 0 else { throw ConfigError("enumeration max_depth must be nonnegative") }
    guard let inputs = m.inputs, let equal = m.equalStates else { throw ModelError("enumeration requires inputs and equality") }
    let control = try RunControl(limits.control)
    var r = SearchReport<I>(termination: "graph_exhausted", states: 0, transitions: 0, maxDepthReached: 0, skippedChecks: 0, failure: nil)
    func stopped() -> Bool {
        if let reason = control.stop() { r.termination = reason; return true }
        return false
    }
    func estimate(_ state: S) throws -> UInt64? {
        guard let callback = m.estimatedStateBytes else { return nil }
        return try call("estimated_state_bytes") { try callback(copied(m.cloneState, state)) }
    }
    func hash(_ state: S) throws -> UInt64 { try call("state hash") { try m.hashState?(copied(m.cloneState, state)) ?? 0 } }
    if stopped() { return r }
    let initial = try searchInitial(m)
    r.states = 1
    let initialBad = try searchInitialChecks(m, state: initial, skipped: &r.skippedChecks, observer: observer)
    if !initialBad.isEmpty { r.termination = "failure_found"; r.failure = Failure(inputs: [], violations: initialBad); return r }
    if stopped() { return r }
    let initialBytes = try estimate(initial)
    r.estimatedRetainedStateBytes = initialBytes
    if stopped() { return r }
    if let maximum = limits.maxEstimatedStateBytes {
        guard let bytes = initialBytes else { throw ModelError("state byte budget requires estimated_state_bytes") }
        if bytes > maximum { r.states = 0; r.estimatedRetainedStateBytes = 0; r.termination = "estimated_state_byte_limit"; return r }
    }
    let initialHash = try hash(initial)
    if stopped() { return r }
    var nodes = [Node<S, I>(state: initial, parent: -1, input: nil, depth: 0)]
    var visited: [UInt64: [Int]] = [initialHash: [0]]
    func path(_ index: Int) throws -> [I] {
        var result: [I] = [], k = index
        while nodes[k].parent >= 0 {
            guard let input = nodes[k].input else { throw ModelError("missing predecessor input") }
            result.append(try copied(m.cloneInput, input)); k = nodes[k].parent
        }
        return result.reversed()
    }
    var cutoff = false, cursor = 0
    while cursor < nodes.count {
        if stopped() { return r }
        let current = nodes[cursor]
        let iterator = try call("enumerate inputs") { try inputs(copied(m.cloneState, current.state)) }
        if stopped() { return r }
        func next() throws -> I? { try call("enumerate inputs", iterator.next) }
        if current.depth == config.maxDepth {
            let item = try next()
            if stopped() { return r }
            cutoff = item != nil || cutoff; cursor += 1; continue
        }
        while true {
            if stopped() { return r }
            let item = try next()
            if stopped() { return r }
            guard let input = item else { break }
            if r.transitions == config.maxTransitions { r.termination = "transition_limit"; return r }
            let transition = try perform(m, current.state, input)
            r.transitions += 1; r.maxDepthReached = max(r.maxDepthReached, current.depth + 1)
            var history: [I] = []
            if observer?.needsHistory() == true { history = try path(cursor); history.append(try copied(m.cloneInput, input)) }
            let bad = try searchEdge(m, state: current.state, input: input, transition: transition, skipped: &r.skippedChecks, observer: observer, history: history)
            if !bad.isEmpty {
                var witness = try path(cursor); witness.append(try copied(m.cloneInput, input))
                r.failure = Failure(inputs: witness, violations: bad); r.termination = "failure_found"; return r
            }
            if stopped() { return r }
            let key = try hash(transition.state)
            if stopped() { return r }
            var duplicate = false
            // Rust collision chains visit the most recently admitted state first.
            for index in (visited[key] ?? []).reversed() {
                if stopped() { return r }
                let same = try call("state equality") { try equal(copied(m.cloneState, nodes[index].state), copied(m.cloneState, transition.state)) }
                if stopped() { return r }
                if same { duplicate = true; break }
            }
            if duplicate { continue }
            if nodes.count == config.maxStates { r.termination = "state_limit"; return r }
            let successorBytes = try estimate(transition.state)
            if stopped() { return r }
            let total: UInt64?
            if let retained = r.estimatedRetainedStateBytes, let bytes = successorBytes {
                let (sum, overflow) = retained.addingReportingOverflow(bytes); total = overflow ? nil : sum
            } else { total = nil }
            if let maximum = limits.maxEstimatedStateBytes {
                guard successorBytes != nil else { throw ModelError("state byte budget requires estimated_state_bytes") }
                guard let sum = total, sum <= maximum else { r.termination = "estimated_state_byte_limit"; return r }
            }
            let ownedInput = try copied(m.cloneInput, input)
            visited[key, default: []].append(nodes.count)
            nodes.append(Node(state: transition.state, parent: cursor, input: ownedInput, depth: current.depth + 1))
            r.states = nodes.count; r.estimatedRetainedStateBytes = total
        }
        cursor += 1
    }
    if cutoff { r.termination = "depth_bound" }
    return r
}
/// Explicit observed entrypoint; ordinary defaults remain available above.
public func enumerateObserved<S, I, O>(_ model: Model<S, I, O>, config: SearchConfig = SearchConfig(), limits: SearchLimits = SearchLimits(), observer: SearchObserver<S, I, O>) throws -> SearchReport<I> {
    try enumerateStates(model, config: config, limits: limits, observer: observer)
}

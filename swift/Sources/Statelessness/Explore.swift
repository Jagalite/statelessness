public struct SearchConfig: Sendable {
    public let maxStates: Int
    public let maxTransitions: UInt64
    public let maxDepth: Int
    public init(maxStates: Int = 100_000, maxTransitions: UInt64 = 1_000_000, maxDepth: Int = 100) {
        self.maxStates = maxStates; self.maxTransitions = maxTransitions; self.maxDepth = maxDepth
    }
}
public struct Violation: Equatable, Sendable { public let phase: String; public let check: Check }
public struct Failure<Input> { public let inputs: [Input]; public let violations: [Violation] }
public struct SearchReport<Input> {
    public var termination: String
    public var states: Int
    public var transitions: UInt64
    public var maxDepthReached: Int
    public var skippedChecks: UInt64
    public var failure: Failure<Input>?
}
private struct Node<S, I> { let state: S; let parent: Int; let input: I?; let depth: Int }
private func findings(_ phase: String, _ checks: [Check], _ skipped: inout UInt64) -> [Violation] {
    skipped += UInt64(checks.filter { $0.status == .skipped }.count)
    return checks.filter { $0.status == .failed }.map { Violation(phase: phase, check: $0) }
}
/// FIFO exploration; hash collisions never stand in for state equality. Without
/// a hash callback the engine uses an equality bucket, which is correct but slower.
public func enumerateStates<S, I, O>(_ m: Model<S, I, O>, config: SearchConfig = SearchConfig()) throws -> SearchReport<I> {
    guard config.maxStates > 0, config.maxDepth >= 0 else { throw ConfigError("invalid search configuration") }
    guard let inputs = m.inputs, let equal = m.equalStates else { throw ModelError("enumeration requires inputs and equality") }
    let initial = try copied(m.cloneState, call("initial_state", m.initialState))
    let checks = try stateChecks(m, initial)
    var r = SearchReport<I>(termination: "graph_exhausted", states: 1, transitions: 0, maxDepthReached: 0, skippedChecks: 0, failure: nil)
    let bad = findings("initial_state", checks, &r.skippedChecks)
    if !bad.isEmpty { r.termination = "failure_found"; r.failure = Failure(inputs: [], violations: bad); return r }
    var nodes = [Node<S, I>(state: initial, parent: -1, input: nil, depth: 0)]
    func hash(_ state: S) throws -> UInt64 { try call("state hash") { try m.hashState?(copied(m.cloneState, state)) ?? 0 } }
    var visited: [UInt64: [Int]] = [try hash(initial): [0]]
    var cutoff = false, cursor = 0
    while cursor < nodes.count {
        let current = nodes[cursor]
        let iterator = try call("enumerate inputs") { try inputs(copied(m.cloneState, current.state)) }
        func next() throws -> I? { try call("enumerate inputs", iterator.next) }
        if current.depth == config.maxDepth { let item = try next(); cutoff = item != nil || cutoff; cursor += 1; continue }
        while let input = try next() {
            if r.transitions == config.maxTransitions { r.termination = "transition_limit"; return r }
            let t = try perform(m, current.state, input)
            r.transitions += 1; r.maxDepthReached = max(r.maxDepthReached, current.depth + 1)
            let sc = try stateChecks(m, t.state), ec = try edgeChecks(m, current.state, input, t)
            let bad = findings("state", sc, &r.skippedChecks) + findings("transition", ec, &r.skippedChecks)
            if !bad.isEmpty {
                var path = [try copied(m.cloneInput, input)], k = cursor
                while nodes[k].parent >= 0 {
                    guard let prior = nodes[k].input else { throw ModelError("missing predecessor input") }
                    path.append(try copied(m.cloneInput, prior)); k = nodes[k].parent
                }
                r.failure = Failure(inputs: path.reversed(), violations: bad); r.termination = "failure_found"; return r
            }
            let key = try hash(t.state)
            var duplicate = false
            for index in visited[key] ?? [] {
                if try call("state equality", { try equal(copied(m.cloneState, nodes[index].state), copied(m.cloneState, t.state)) }) { duplicate = true; break }
            }
            if duplicate { continue }
            if nodes.count == config.maxStates { r.termination = "state_limit"; return r }
            visited[key, default: []].append(nodes.count)
            nodes.append(Node(state: t.state, parent: cursor, input: try copied(m.cloneInput, input), depth: current.depth + 1))
            r.states = nodes.count
        }
        cursor += 1
    }
    if cutoff { r.termination = "depth_bound" }
    return r
}

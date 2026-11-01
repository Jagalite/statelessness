/// Swift-only extension `reduction-v1` (S4); see spec/reduction-v1.md.
/// Reduction collapses an evaluated edge into an earlier representative edge of
/// the same eligible class from the same node. It never skips executing or
/// checking an edge. Collapse requires equal disposition, every check, the
/// application projection, and full successor equality; a digest is not used.
/// `Research.compareEquivalence` evidence does not authorize reduction.
public struct EquivalenceReduction<S, I, O> {
    /// The eligible equivalence class, or nil when the edge is not a candidate.
    public var eligibleClass: (S, I) throws -> String?
    /// Deterministic bytes for ordered effects and progress annotations that
    /// equivalence must preserve. Receives detached copies and edge labels.
    public var projection: (I, Transition<S, O>, [String]) throws -> [UInt8]
    public init(eligibleClass: @escaping (S, I) throws -> String?, projection: @escaping (I, Transition<S, O>, [String]) throws -> [UInt8]) {
        self.eligibleClass = eligibleClass; self.projection = projection
    }
}
public struct RejectedEquivalence: Equatable, Sendable {
    public let representative: GraphEdgeID
    /// disposition_differs, checks_differ, projection_differs or successor_differs.
    public let reason: String
    public init(representative: GraphEdgeID, reason: String) { self.representative = representative; self.reason = reason }
    public static func == (a: Self, b: Self) -> Bool { a.representative == b.representative && exactText(a.reason, b.reason) }
}
public struct ReductionDecision: Equatable, Sendable {
    public let edge: GraphEdgeID
    public let source: GraphNodeID
    public let equivalenceClass: String
    /// nil: the edge became a representative of its class.
    public let reducedTo: GraphEdgeID?
    /// Representatives compared before the decision, in comparison order.
    public let rejected: [RejectedEquivalence]
    public init(edge: GraphEdgeID, source: GraphNodeID, equivalenceClass: String, reducedTo: GraphEdgeID?, rejected: [RejectedEquivalence]) {
        self.edge = edge; self.source = source; self.equivalenceClass = equivalenceClass; self.reducedTo = reducedTo; self.rejected = rejected
    }
    public static func == (a: Self, b: Self) -> Bool {
        a.edge == b.edge && a.source == b.source && exactText(a.equivalenceClass, b.equivalenceClass) && a.reducedTo == b.reducedTo && a.rejected == b.rejected
    }
}
// MARK: Diamond validation
public struct DiamondWitness<I> {
    public let node: GraphNodeID
    public let first: I
    public let second: I
    /// state_differs or observations_differ.
    public let reason: String
}
public struct DiamondReport<I> {
    /// search_complete, or the RunLimits stop reason.
    public var termination: String
    public var checkedNodes: Int
    public var candidatePairs: UInt64
    /// Pairs where each input stayed enabled after the other.
    public var validated: UInt64
    public var nonCommuting: [DiamondWitness<I>]
}
/// For each completely expanded node, executes both orders of every pair of
/// candidate inputs that remain enabled after each other, and compares the
/// final states by model equality and the two steps' observations: the set of
/// failed check (phase, ID) pairs and the multiset of projections. This is
/// evidence about independence, never permission to prune.
public func validateDiamonds<S, I, O>(_ m: Model<S, I, O>, report: GraphSearchReport<S, I, O>, candidate: (S, I) throws -> Bool, equalInputs: (I, I) throws -> Bool,
                                      projection: (I, Transition<S, O>) throws -> [UInt8], limits: RunLimits = RunLimits()) throws -> DiamondReport<I> {
    guard let inputs = m.inputs, let equal = m.equalStates else { throw ModelError("diamond validation requires inputs and equality") }
    let control = try RunControl(limits)
    var out = DiamondReport<I>(termination: "search_complete", checkedNodes: 0, candidatePairs: 0, validated: 0, nonCommuting: [])
    func enumerate(_ state: S) throws -> [I] {
        let iterator = try call("enumerate inputs") { try inputs(copied(m.cloneState, state)) }
        var values: [I] = []
        while let value = try call("enumerate inputs", iterator.next) { values.append(try copied(m.cloneInput, value)) }
        return values
    }
    func enabled(_ input: I, at state: S) throws -> Bool {
        for other in try enumerate(state) where try call("input_equal", { try equalInputs(copied(m.cloneInput, other), copied(m.cloneInput, input)) }) { return true }
        return false
    }
    func observe(_ before: S, _ input: I, _ t: Transition<S, O>) throws -> (failed: [String], projection: [UInt8]) {
        let checks = try stateChecks(m, t.state).map { Violation(phase: "state", check: $0) } + edgeChecks(m, before, input, t).map { Violation(phase: "transition", check: $0) }
        let failed = checks.filter { $0.check.status == .failed }.map { "\($0.phase)\u{0}\($0.check.id)" }
        return (failed, try call("diamond projection") { try projection(copied(m.cloneInput, input), cloneTransition(m, t)) })
    }
    for node in report.nodes where node.expansion == .complete {
        if let stop = control.stop() { out.termination = stop; return out }
        out.checkedNodes += 1
        var candidates: [I] = []
        for input in try enumerate(node.state) where try call("diamond candidate", { try candidate(copied(m.cloneState, node.state), copied(m.cloneInput, input)) }) { candidates.append(input) }
        for i in candidates.indices {
            for j in candidates.indices where j > i {
                if let stop = control.stop() { out.termination = stop; return out }
                out.candidatePairs += 1
                let a = candidates[i], b = candidates[j]
                let ta = try perform(m, node.state, a), tb = try perform(m, node.state, b)
                guard try enabled(b, at: ta.state), try enabled(a, at: tb.state) else { continue }
                let tab = try perform(m, ta.state, b), tba = try perform(m, tb.state, a)
                out.validated += 1
                let same = try call("state equality") { try equal(copied(m.cloneState, tab.state), copied(m.cloneState, tba.state)) }
                let left = [try observe(node.state, a, ta), try observe(ta.state, b, tab)], right = [try observe(node.state, b, tb), try observe(tb.state, a, tba)]
                let sameFailures = Set(left.flatMap(\.failed)) == Set(right.flatMap(\.failed))
                let sameProjections = left.map(\.projection).sorted { $0.lexicographicallyPrecedes($1) } == right.map(\.projection).sorted { $0.lexicographicallyPrecedes($1) }
                if !same || !sameFailures || !sameProjections {
                    out.nonCommuting.append(DiamondWitness(node: node.id, first: try copied(m.cloneInput, a), second: try copied(m.cloneInput, b), reason: same ? "observations_differ" : "state_differs"))
                }
            }
        }
    }
    return out
}
// MARK: Reduced versus unreduced comparison
public struct ReductionComparison: Equatable, Sendable {
    /// Unreduced node IDs with no equal reduced node, and the reverse.
    public var missingInReduced: [GraphNodeID]
    public var extraInReduced: [GraphNodeID]
    /// Unreduced edges whose (source, target, disposition, checks) class has no
    /// reduced representative, and the reverse.
    public var unrepresentedEdges: [GraphEdgeID]
    public var extraReducedEdges: [GraphEdgeID]
    public var findingsMatch: Bool
    public var coverageMatch: Bool
    public var equivalent: Bool { missingInReduced.isEmpty && extraInReduced.isEmpty && unrepresentedEdges.isEmpty && extraReducedEdges.isEmpty && findingsMatch && coverageMatch }
}
/// Compares reachable states by model equality, represented edge classes
/// (raw edge counts may differ), failed property counts and coverage. Both
/// reports must retain edges and must not be incomplete.
public func compareReduction<S, I, O>(_ m: Model<S, I, O>, unreduced: GraphSearchReport<S, I, O>, reduced: GraphSearchReport<S, I, O>) throws -> ReductionComparison {
    guard let equal = m.equalStates else { throw ModelError("reduction comparison requires equality") }
    for report in [unreduced, reduced] {
        guard report.edgesRetained else { throw ModelError("reduction comparison requires retained edges") }
        if case .incomplete = report.coverage { throw ModelError("reduction comparison requires finished searches") }
    }
    func hash(_ s: S) throws -> UInt64 { try call("state hash") { try m.hashState?(copied(m.cloneState, s)) ?? 0 } }
    var buckets: [UInt64: [Int]] = [:]
    for node in reduced.nodes { buckets[try hash(node.state), default: []].append(node.id) }
    var mapping = [Int?](repeating: nil, count: unreduced.nodes.count), matched = Set<Int>()
    var out = ReductionComparison(missingInReduced: [], extraInReduced: [], unrepresentedEdges: [], extraReducedEdges: [], findingsMatch: true, coverageMatch: unreduced.coverage == reduced.coverage)
    for node in unreduced.nodes {
        for other in buckets[try hash(node.state)] ?? [] where try call("state equality", { try equal(copied(m.cloneState, node.state), copied(m.cloneState, reduced.nodes[other].state)) }) {
            mapping[node.id] = other; matched.insert(other); break
        }
        if mapping[node.id] == nil { out.missingInReduced.append(node.id) }
    }
    out.extraInReduced = reduced.nodes.map(\.id).filter { !matched.contains($0) }
    func resolved(_ report: GraphSearchReport<S, I, O>, _ edge: GraphEdge<I, O>) -> GraphNodeID? {
        var target = edge.target, hops = 0
        while case let .reduced(rep) = target, report.edges.indices.contains(rep), hops <= report.edges.count { target = report.edges[rep].target; hops += 1 }
        switch target { case let .admitted(n), let .duplicate(n): return n; default: return nil }
    }
    func same(_ a: GraphEdge<I, O>, _ b: GraphEdge<I, O>) -> Bool { a.disposition == b.disposition && a.stateChecks == b.stateChecks && a.transitionChecks == b.transitionChecks }
    let reducedEdges = reduced.edges.filter { if case .reduced = $0.target { return false }; return true }
    // An edge resolved to a node that has no counterpart is never represented.
    func mapped(_ report: GraphSearchReport<S, I, O>, _ edge: GraphEdge<I, O>, _ map: (Int) -> Int?) -> (known: Bool, node: Int?) {
        guard let node = resolved(report, edge) else { return (true, nil) }
        guard let other = map(node) else { return (false, nil) }
        return (true, other)
    }
    var reverse = [Int?](repeating: nil, count: reduced.nodes.count)
    for (u, r) in mapping.enumerated() { if let r, reverse.indices.contains(r) { reverse[r] = u } }
    func at(_ table: [Int?], _ index: Int) -> Int? { table.indices.contains(index) ? table[index] : nil }
    for edge in unreduced.edges {
        let target = mapped(unreduced, edge) { at(mapping, $0) }
        guard let source = at(mapping, edge.source), target.known else { out.unrepresentedEdges.append(edge.id); continue }
        if !reducedEdges.contains(where: { $0.source == source && resolved(reduced, $0) == target.node && same($0, edge) }) { out.unrepresentedEdges.append(edge.id) }
    }
    for edge in reducedEdges {
        let target = mapped(reduced, edge) { at(reverse, $0) }
        let source = at(reverse, edge.source)
        let represented = target.known && source != nil && unreduced.edges.contains { other in
            other.source == source && resolved(unreduced, other) == target.node && same(other, edge)
        }
        if !represented { out.extraReducedEdges.append(edge.id) }
    }
    out.findingsMatch = unreduced.findingCount == reduced.findingCount && unreduced.failedProperties == reduced.failedProperties
    return out
}

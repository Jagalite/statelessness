/// Swift-only extension `progress-analysis-v1` (S3); see
/// spec/progress-analysis-v1.md. Applications supply node kinds, ranks and
/// edge fairness; this file owns only graph algorithms. Fair-path reachability
/// and rank checks are bounded evidence, not a proof that every scheduler
/// execution eventually completes.
public struct ProgressEdge: Equatable, Sendable {
    public let source: Int
    public let target: Int
    public let fair: Bool
    public init(source: Int, target: Int, fair: Bool) { self.source = source; self.target = target; self.fair = fair }
}
public struct ProgressGraph: Equatable, Sendable {
    public let nodeCount: Int
    public let edges: [ProgressEdge]
    /// False for a node whose outgoing edges are not all known: a depth
    /// frontier, an interrupted or unexpanded node, or a rejected successor.
    public let measured: [Bool]
    public init(nodeCount: Int, edges: [ProgressEdge], measured: [Bool]) throws {
        guard nodeCount >= 0, measured.count == nodeCount else { throw ConfigError("progress graph needs one measured flag per node") }
        for e in edges where !(0..<nodeCount).contains(e.source) || !(0..<nodeCount).contains(e.target) { throw ConfigError("progress edge endpoint out of range") }
        self.nodeCount = nodeCount; self.edges = edges; self.measured = measured
    }
    /// Admitted and duplicate edges keep their targets; reduced edges use their
    /// representative's target. Requires retained edges.
    public init<S, I, O>(report: GraphSearchReport<S, I, O>, fair: (GraphEdge<I, O>) throws -> Bool) throws {
        guard report.edgesRetained else { throw ModelError("progress analysis requires retained edges") }
        var measured = report.nodes.map { $0.expansion == .complete }, edges: [ProgressEdge] = []
        for edge in report.edges {
            var target = edge.target, hops = 0
            while case let .reduced(rep) = target, report.edges.indices.contains(rep), hops <= report.edges.count { target = report.edges[rep].target; hops += 1 }
            switch target {
            case let .admitted(n), let .duplicate(n): edges.append(ProgressEdge(source: edge.source, target: n, fair: try call("progress fairness") { try fair(edge) }))
            default: measured[edge.source] = false
            }
        }
        try self.init(nodeCount: report.nodes.count, edges: edges, measured: measured)
    }
}
public enum ProgressKind: Equatable, Sendable { case terminal, intentionalWait, externalWait, transaction, other }
public enum ProgressClass: String, Equatable, Sendable {
    case terminal, stableIntentionalWait, declaredExternalWait, transactionProgress, stuck, unmeasuredCutoff
}
public struct ProgressReport: Equatable, Sendable {
    public let classes: [ProgressClass]
    public let terminalStates: Int
    public let stableIntentionalWaitStates: Int
    public let externalWaitStates: Int
    public let unmeasuredStates: Int
    public let transactionStates: Int
    public let settledTransactionStates: Int
    /// Transaction nodes with no fair path to a settled node.
    public let unsettledTransactionStates: [Int]
    public let stuckStates: [Int]
    /// Members sorted ascending; components sorted by first member.
    public let closedNonterminalComponents: [[Int]]
    /// Closed candidates excluded because a member is unmeasured.
    public let unmeasuredComponents: Int
    public let maximumRank: UInt64
    /// Every node is measured; otherwise absence of a finding is not evidence.
    public let graphComplete: Bool
}
/// Tarjan's algorithm with an explicit stack. Members are sorted ascending and
/// components are ordered by their smallest member.
public func stronglyConnectedComponents(nodeCount: Int, edges: [(Int, Int)]) -> [[Int]] {
    var adjacency = [[Int]](repeating: [], count: nodeCount)
    for (source, target) in edges { adjacency[source].append(target) }
    var index = [Int](repeating: -1, count: nodeCount), low = [Int](repeating: 0, count: nodeCount), onStack = [Bool](repeating: false, count: nodeCount)
    var stack: [Int] = [], components: [[Int]] = [], counter = 0
    var work: [(node: Int, next: Int)] = []
    for root in 0..<nodeCount where index[root] < 0 {
        work.append((root, 0))
        while let frame = work.last {
            let v = frame.node
            if frame.next == 0 && index[v] < 0 { index[v] = counter; low[v] = counter; counter += 1; stack.append(v); onStack[v] = true }
            if frame.next < adjacency[v].count {
                let w = adjacency[v][frame.next]; work[work.count - 1].next += 1
                if index[w] < 0 { work.append((w, 0)) } else if onStack[w] { low[v] = min(low[v], index[w]) }
                continue
            }
            work.removeLast()
            if let parent = work.last { low[parent.node] = min(low[parent.node], low[v]) }
            if low[v] == index[v] {
                var component: [Int] = []
                while let w = stack.popLast() { onStack[w] = false; component.append(w); if w == v { break } }
                components.append(component.sorted())
            }
        }
    }
    return components.sorted { $0[0] < $1[0] }
}
/// Nodes with a path to any target using edges accepted by `include`.
public func reverseReachable(nodeCount: Int, edges: [ProgressEdge], targets: [Int], include: (ProgressEdge) -> Bool = { _ in true }) -> [Bool] {
    var reverse = [[Int]](repeating: [], count: nodeCount)
    for e in edges where include(e) { reverse[e.target].append(e.source) }
    var reached = [Bool](repeating: false, count: nodeCount), queue: [Int] = []
    for t in targets where !reached[t] { reached[t] = true; queue.append(t) }
    var head = 0
    while head < queue.count {
        let v = queue[head]; head += 1
        for u in reverse[v] where !reached[u] { reached[u] = true; queue.append(u) }
    }
    return reached
}
/// Classification order: unmeasured, terminal, external wait, intentional
/// wait, transaction with a fair outgoing edge, otherwise stuck. A closed
/// nonterminal component contains a transaction or stuck node, has no exit
/// edge, no fair rank-decreasing edge, and no unmeasured member.
public func analyzeProgress(_ graph: ProgressGraph, kind: (Int) throws -> ProgressKind, rank: ((Int) throws -> UInt64)? = nil) throws -> ProgressReport {
    let n = graph.nodeCount
    var outgoing = [[ProgressEdge]](repeating: [], count: n)
    for e in graph.edges { outgoing[e.source].append(e) }
    var classes: [ProgressClass] = [], ranks: [UInt64] = []
    classes.reserveCapacity(n); ranks.reserveCapacity(n)
    for v in 0..<n {
        ranks.append(try rank.map { r in try call("progress rank") { try r(v) } } ?? 0)
        if !graph.measured[v] { classes.append(.unmeasuredCutoff); continue }
        switch try call("progress kind", { try kind(v) }) {
        case .terminal: classes.append(.terminal)
        case .externalWait: classes.append(.declaredExternalWait)
        case .intentionalWait: classes.append(.stableIntentionalWait)
        case .transaction: classes.append(outgoing[v].contains(where: \.fair) ? .transactionProgress : .stuck)
        case .other: classes.append(.stuck)
        }
    }
    let settled = (0..<n).filter { [.terminal, .stableIntentionalWait, .declaredExternalWait].contains(classes[$0]) }
    let canSettle = reverseReachable(nodeCount: n, edges: graph.edges, targets: settled, include: \.fair)
    var closed: [[Int]] = [], unmeasuredComponents = 0
    for component in stronglyConnectedComponents(nodeCount: n, edges: graph.edges.map { ($0.source, $0.target) }) {
        guard component.contains(where: { classes[$0] == .transactionProgress || classes[$0] == .stuck }) else { continue }
        let members = Set(component), exits = component.flatMap { outgoing[$0] }
        if exits.contains(where: { !members.contains($0.target) }) || exits.contains(where: { $0.fair && ranks[$0.target] < ranks[$0.source] }) { continue }
        if component.contains(where: { !graph.measured[$0] }) { unmeasuredComponents += 1; continue }
        closed.append(component)
    }
    let transactions = (0..<n).filter { classes[$0] == .transactionProgress }
    func count(_ c: ProgressClass) -> Int { classes.reduce(0) { $0 + ($1 == c ? 1 : 0) } }
    return ProgressReport(classes: classes, terminalStates: count(.terminal), stableIntentionalWaitStates: count(.stableIntentionalWait), externalWaitStates: count(.declaredExternalWait),
                          unmeasuredStates: count(.unmeasuredCutoff), transactionStates: transactions.count, settledTransactionStates: transactions.filter { canSettle[$0] }.count,
                          unsettledTransactionStates: transactions.filter { !canSettle[$0] }, stuckStates: (0..<n).filter { classes[$0] == .stuck },
                          closedNonterminalComponents: closed, unmeasuredComponents: unmeasuredComponents, maximumRank: ranks.max() ?? 0, graphComplete: !graph.measured.contains(false))
}

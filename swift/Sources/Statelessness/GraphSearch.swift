/// Candidate extension `graph-search-v1` (S1 admitted graph, S2 failure
/// collection); see spec/graph-search-v1.md. With default options the model
/// callback sequence is exactly bfs-controls-v1. `enumerateStates` is unchanged
/// and remains the portable bfs-v1 entry point.
public typealias GraphNodeID = Int
public typealias GraphEdgeID = Int

public enum FailurePolicy: Equatable, Sendable {
    /// bfs-v1: the first failing check batch ends the search.
    case stopAtFirst
    /// Record every failing batch. `maxRetainedFindings` bounds retained
    /// witnesses only; counts and failed property identities are never dropped.
    /// Without `expandFailingSuccessors`, a new successor reached by a failing
    /// edge is rejected (`failing_successor`) and coverage is failure-pruned.
    case collect(maxRetainedFindings: Int, expandFailingSuccessors: Bool)
}
/// Exceeding either cap ends the search with `graph_retention_limit`.
/// `maxGraphBytes` bounds engine bookkeeping for nodes and retained edges; it
/// excludes model state representation (see `estimatedRetainedStateBytes`).
public struct GraphRetention: Equatable, Sendable {
    public var retainEdges: Bool
    public var maxRetainedEdges: Int?
    public var maxGraphBytes: UInt64?
    public init(retainEdges: Bool = true, maxRetainedEdges: Int? = nil, maxGraphBytes: UInt64? = nil) {
        self.retainEdges = retainEdges; self.maxRetainedEdges = maxRetainedEdges; self.maxGraphBytes = maxGraphBytes
    }
}
public enum NodeExpansion: Equatable, Sendable {
    /// Still queued when the search ended.
    case unexpanded
    /// Every enumerated input was evaluated and its successor resolved.
    case complete
    /// At max_depth with at least one unexamined input.
    case depthFrontier
    /// A failing initial state under `collect(expandFailingSuccessors: false)`.
    case failurePruned
    /// Expansion started but the search ended before it finished.
    case interrupted(String)
}
public enum EdgeTarget: Equatable, Sendable {
    /// Only observed in `edgeEvaluated` events; resolved before reports return.
    case pending
    case admitted(GraphNodeID)
    case duplicate(GraphNodeID)
    /// S4: collapsed into an equivalent representative edge from the same node.
    case reduced(GraphEdgeID)
    /// Evaluated but not admitted: a cap, `failing_successor`, or a stop reason.
    case rejected(String)
}
public struct GraphNode<S, I> {
    public let id: GraphNodeID
    public let state: S
    public let parent: GraphNodeID?
    public let input: I?
    public let admittingEdge: GraphEdgeID?
    public let depth: Int
    /// Initial checks for the root; otherwise the admitting edge's state checks.
    public let checks: [Check]
    /// True when any check batch on the root path, including this node's, failed.
    public let pathFailed: Bool
    public internal(set) var expansion: NodeExpansion
}
public struct GraphEdge<I, O> {
    public let id: GraphEdgeID
    public let source: GraphNodeID
    public let input: I
    public let disposition: Disposition
    public let outputs: [O]
    public let stateChecks: [Check]
    public let transitionChecks: [Check]
    public let annotations: [String]
    /// Reports are values; callers may rewrite a copy, e.g. for negative controls.
    public var target: EdgeTarget
    public var failed: Bool { hasFailure(stateChecks) || hasFailure(transitionChecks) }
}
public struct GraphFinding<I> {
    public let index: UInt64
    /// Root-to-edge inputs; empty for an initial-state finding.
    public let witness: [I]
    /// Every failed check of the batch, state checks before transition checks.
    public let violations: [Violation]
    /// nil for an initial-state finding.
    public let edge: GraphEdgeID?
    /// The witness prefix crosses an earlier failure; reproducing it requires
    /// continued-failure-evidence-v1 rather than stop-at-failure replay.
    public let requiresContinuedEvidence: Bool
}
public struct PropertyFailureCount: Equatable, Sendable {
    public let phase: String
    public let id: String
    public internal(set) var count: UInt64
    public let firstFinding: UInt64
    public init(phase: String, id: String, count: UInt64, firstFinding: UInt64) { self.phase = phase; self.id = id; self.count = count; self.firstFinding = firstFinding }
    public static func == (a: Self, b: Self) -> Bool { exactText(a.phase, b.phase) && exactText(a.id, b.id) && a.count == b.count && a.firstFinding == b.firstFinding }
}
public enum GraphCoverage: Equatable, Sendable {
    /// The whole reachable finite graph was expanded.
    case exhaustive
    /// Exploration finished, but only within the declared depth and/or with
    /// failing successors left unexpanded. Admissible only as bounded coverage.
    case bounded(depthCutoff: Bool, failurePruned: Bool)
    /// Stopped by a property failure, cap, cancellation, deadline or retention.
    case incomplete(String)
}
public enum GraphEvent<I, O> {
    case rootAdmitted(GraphNodeID)
    case expansionStarted(GraphNodeID)
    case edgeEvaluated(GraphEdge<I, O>)
    case finding(index: UInt64, edge: GraphEdgeID?, violations: [Violation])
    case successorAdmitted(edge: GraphEdgeID, node: GraphNodeID)
    case duplicateEdge(edge: GraphEdgeID, existing: GraphNodeID)
    case edgeReduced(edge: GraphEdgeID, representative: GraphEdgeID)
    case successorRejected(edge: GraphEdgeID, reason: String)
    case expansionFinished(GraphNodeID, NodeExpansion)
    case layerCompleted(depth: Int)
    case searchFinished(termination: String)
}
public struct GraphSearchOptions<S, I, O> {
    public var failurePolicy: FailurePolicy
    public var retention: GraphRetention
    public var observer: SearchObserver<S, I, O>?
    /// Synchronous event sink. Payloads are detached copies; errors abort.
    public var events: ((GraphEvent<I, O>) throws -> Void)?
    /// User edge labels, called with detached copies after all checks.
    public var annotateEdge: ((S, I, Transition<S, O>) throws -> [String])?
    public var reduction: EquivalenceReduction<S, I, O>?
    public init(failurePolicy: FailurePolicy = .stopAtFirst, retention: GraphRetention = GraphRetention(), observer: SearchObserver<S, I, O>? = nil,
                events: ((GraphEvent<I, O>) throws -> Void)? = nil, annotateEdge: ((S, I, Transition<S, O>) throws -> [String])? = nil,
                reduction: EquivalenceReduction<S, I, O>? = nil) {
        self.failurePolicy = failurePolicy; self.retention = retention; self.observer = observer
        self.events = events; self.annotateEdge = annotateEdge; self.reduction = reduction
    }
}
public struct GraphSearchReport<S, I, O> {
    public var termination: String
    public var coverage: GraphCoverage
    /// bfs-v1 meaning: admitted states, or 1 once the initial state exists.
    public var states: Int
    /// Evaluated transitions, including duplicate, reduced and rejected edges.
    public var transitions: UInt64
    public var maxDepthReached: Int
    public var skippedChecks: UInt64
    public var estimatedRetainedStateBytes: UInt64?
    public var nodes: [GraphNode<S, I>]
    /// Indexed by edge ID while `edgesRetained`.
    public var edges: [GraphEdge<I, O>]
    public var edgesRetained: Bool
    /// Edges between admitted nodes (admitted plus duplicate targets).
    public var graphEdges: UInt64
    public var duplicateEdges: UInt64
    public var reducedEdges: UInt64
    public var rejectedSuccessors: UInt64
    /// Admitted nodes whose expansion is not `complete`.
    public var frontierSize: Int
    public var completedLayers: Int
    public var depthCutoff: Bool
    public var failurePruned: Bool
    /// Engine bookkeeping for nodes and retained edges (saturating).
    public var estimatedGraphBytes: UInt64
    public var findings: [GraphFinding<I>]
    public var findingCount: UInt64
    public var failedProperties: [PropertyFailureCount]
    public var reductionDecisions: [ReductionDecision]
    public var findingsTruncated: Bool { findingCount > UInt64(findings.count) }
    public var failure: Failure<I>? { findings.first.map { Failure(inputs: $0.witness, violations: $0.violations) } }
}
/// A model, checker, observer or sink error after the search started. The
/// report holds the evidence gathered before the error and is incomplete.
public struct GraphSearchError<S, I, O>: Error, CustomStringConvertible, @unchecked Sendable {
    public let error: Error
    public let report: GraphSearchReport<S, I, O>
    public var description: String { String(describing: error) }
}
/// Breadth-first exploration that retains the admitted graph and its evidence.
public func exploreGraph<S, I, O>(_ m: Model<S, I, O>, config: SearchConfig = SearchConfig(), limits: SearchLimits = SearchLimits(), options: GraphSearchOptions<S, I, O> = GraphSearchOptions()) throws -> GraphSearchReport<S, I, O> {
    guard config.maxStates > 0 else { throw ConfigError("enumeration max_states must be greater than zero") }
    guard config.maxDepth >= 0 else { throw ConfigError("enumeration max_depth must be nonnegative") }
    guard let inputs = m.inputs, let equal = m.equalStates else { throw ModelError("enumeration requires inputs and equality") }
    if case let .collect(retained, _) = options.failurePolicy, retained < 0 { throw ConfigError("max_retained_findings must be nonnegative") }
    if let maximum = options.retention.maxRetainedEdges, maximum < 0 { throw ConfigError("max_retained_edges must be nonnegative") }
    let control = try RunControl(limits.control)
    let run = GraphSearchRun(m, config: config, limits: limits, options: options, inputs: inputs, equal: equal, control: control)
    do { try run.execute() } catch {
        run.r.termination = "model_error"; run.finalize(); throw GraphSearchError(error: error, report: run.r)
    }
    run.finalize()
    do { try run.emit(.searchFinished(termination: run.r.termination)) } catch {
        run.r.termination = "model_error"; run.finalize(); throw GraphSearchError(error: error, report: run.r)
    }
    return run.r
}
private struct Representative<S> { let edge: GraphEdgeID; let state: S; let disposition: Disposition; let checks: [Check]; let projection: [UInt8] }
private func saturatingAdd(_ a: UInt64, _ b: UInt64) -> UInt64 { let (s, o) = a.addingReportingOverflow(b); return o ? .max : s }
private func checkBytes(_ checks: [Check]) -> UInt64 {
    checks.reduce(UInt64(0)) { saturatingAdd($0, UInt64(MemoryLayout<Check>.stride + $1.id.utf8.count + $1.details.utf8.count)) }
}
private final class GraphSearchRun<S, I, O> {
    let m: Model<S, I, O>, config: SearchConfig, limits: SearchLimits, options: GraphSearchOptions<S, I, O>
    let inputs: (S) throws -> InputIterator<I>, equal: (S, S) throws -> Bool, control: RunControl
    let storage = SearchCheckStorage()
    let collect: Bool, expandFailing: Bool, maxFindings: Int, needsEdgeRecord: Bool
    var r: GraphSearchReport<S, I, O>
    var visited: [UInt64: [Int]] = [:]
    var nextEdge = 0, currentLayer = 0, activeNode: Int?
    /// The evaluated edge whose successor is not yet resolved, retained or not.
    var pendingEdge: GraphEdgeID?
    init(_ m: Model<S, I, O>, config: SearchConfig, limits: SearchLimits, options: GraphSearchOptions<S, I, O>, inputs: @escaping (S) throws -> InputIterator<I>, equal: @escaping (S, S) throws -> Bool, control: RunControl) {
        self.m = m; self.config = config; self.limits = limits; self.options = options; self.inputs = inputs; self.equal = equal; self.control = control
        switch options.failurePolicy {
        case .stopAtFirst: collect = false; expandFailing = false; maxFindings = 1
        case let .collect(retained, expand): collect = true; expandFailing = expand; maxFindings = retained
        }
        needsEdgeRecord = options.retention.retainEdges || options.events != nil
        r = GraphSearchReport(termination: "graph_exhausted", coverage: .exhaustive, states: 0, transitions: 0, maxDepthReached: 0, skippedChecks: 0, estimatedRetainedStateBytes: nil,
                              nodes: [], edges: [], edgesRetained: options.retention.retainEdges, graphEdges: 0, duplicateEdges: 0, reducedEdges: 0, rejectedSuccessors: 0,
                              frontierSize: 0, completedLayers: 0, depthCutoff: false, failurePruned: false, estimatedGraphBytes: 0, findings: [], findingCount: 0,
                              failedProperties: [], reductionDecisions: [])
    }
    func stopped() -> Bool {
        if let reason = control.stop() { r.termination = reason; return true }
        return false
    }
    func emit(_ event: @autoclosure () throws -> GraphEvent<I, O>) throws {
        guard let sink = options.events else { return }
        let value = try event()
        try call("graph event sink") { try sink(value) }
    }
    func detached(_ e: GraphEdge<I, O>) throws -> GraphEdge<I, O> {
        GraphEdge(id: e.id, source: e.source, input: try copied(m.cloneInput, e.input), disposition: e.disposition, outputs: try e.outputs.map { try copied(m.cloneOutput, $0) },
                  stateChecks: e.stateChecks, transitionChecks: e.transitionChecks, annotations: e.annotations, target: e.target)
    }
    func estimate(_ state: S) throws -> UInt64? {
        guard let callback = m.estimatedStateBytes else { return nil }
        return try call("estimated_state_bytes") { try callback(copied(m.cloneState, state)) }
    }
    func hash(_ state: S) throws -> UInt64 { try call("state hash") { try m.hashState?(copied(m.cloneState, state)) ?? 0 } }
    func path(_ index: Int) throws -> [I] {
        var result: [I] = [], k = index
        while let parent = r.nodes[k].parent {
            guard let input = r.nodes[k].input else { throw ModelError("missing predecessor input") }
            result.append(try copied(m.cloneInput, input)); k = parent
        }
        return result.reversed()
    }
    /// False when the bytes would exceed the declared graph byte cap.
    func chargeGraph(_ bytes: UInt64) -> Bool {
        let total = saturatingAdd(r.estimatedGraphBytes, bytes)
        if let maximum = options.retention.maxGraphBytes, total > maximum { return false }
        r.estimatedGraphBytes = total; return true
    }
    func edgeBytes(_ e: GraphEdge<I, O>) -> UInt64 {
        var bytes = UInt64(MemoryLayout<GraphEdge<I, O>>.stride)
        bytes = saturatingAdd(bytes, checkBytes(e.stateChecks)); bytes = saturatingAdd(bytes, checkBytes(e.transitionChecks))
        bytes = saturatingAdd(bytes, UInt64(e.outputs.count * MemoryLayout<O>.stride))
        for label in e.annotations { bytes = saturatingAdd(bytes, UInt64(MemoryLayout<String>.stride + label.utf8.count)) }
        return bytes
    }
    func setTarget(_ edge: GraphEdgeID, _ target: EdgeTarget) {
        if pendingEdge == edge { pendingEdge = nil }
        if r.edgesRetained, edge < r.edges.count { r.edges[edge].target = target }
    }
    func reject(_ edge: GraphEdgeID, _ reason: String) throws {
        setTarget(edge, .rejected(reason)); r.rejectedSuccessors += 1
        try emit(.successorRejected(edge: edge, reason: reason))
    }
    func finishNode(_ id: GraphNodeID, _ status: NodeExpansion) throws {
        r.nodes[id].expansion = status; activeNode = nil
        try emit(.expansionFinished(id, status))
    }
    func recordFinding(_ violations: [Violation], edge: GraphEdgeID?, earlier: Bool, witness: () throws -> [I]) throws {
        let index = r.findingCount
        r.findingCount = saturatingAdd(r.findingCount, 1)
        for v in violations {
            if let k = r.failedProperties.firstIndex(where: { exactText($0.phase, v.phase) && exactText($0.id, v.check.id) }) { r.failedProperties[k].count = saturatingAdd(r.failedProperties[k].count, 1) }
            else { r.failedProperties.append(PropertyFailureCount(phase: v.phase, id: v.check.id, count: 1, firstFinding: index)) }
        }
        if r.findings.count < maxFindings {
            r.findings.append(GraphFinding(index: index, witness: try witness(), violations: violations, edge: edge, requiresContinuedEvidence: earlier))
        }
        try emit(.finding(index: index, edge: edge, violations: violations))
    }
    func completeLayer() throws { r.completedLayers += 1; try emit(.layerCompleted(depth: currentLayer)) }
    /// Resolves interrupted state on every exit path, including errors.
    func finalize() {
        if let active = activeNode, active < r.nodes.count, r.nodes[active].expansion == .unexpanded {
            r.nodes[active].expansion = .interrupted(r.termination); activeNode = nil
        }
        // An edge evaluated when the search ended (stop-at-first failure, error)
        // is a rejected successor whether or not its record was retained.
        if let edge = pendingEdge { setTarget(edge, .rejected(r.termination)); r.rejectedSuccessors += 1 }
        r.frontierSize = r.nodes.reduce(0) { $0 + ($1.expansion == .complete ? 0 : 1) }
        if r.termination == "graph_exhausted" || r.termination == "depth_bound" {
            r.coverage = !r.depthCutoff && !r.failurePruned ? .exhaustive : .bounded(depthCutoff: r.depthCutoff, failurePruned: r.failurePruned)
        } else { r.coverage = .incomplete(r.termination) }
    }
    // Throwing clone closures live in these small methods rather than inside
    // the long search loop: the Swift 6.0.3 SIL ownership verifier otherwise
    // reports a leaked copy of the generic clone closure on early exits.
    func detach(_ state: S) -> Result<S, Error> { Result { try copied(m.cloneState, state) } }
    func copyState(_ state: S) throws -> S { try copied(m.cloneState, state) }
    func copyInput(_ input: I) throws -> I { try copied(m.cloneInput, input) }
    func copyOutputs(_ outputs: [O]) throws -> [O] { try outputs.map { try copied(m.cloneOutput, $0) } }
    func enumerate(_ state: S) throws -> InputIterator<I> { try call("enumerate inputs") { try inputs(copied(m.cloneState, state)) } }
    func pull(_ iterator: InputIterator<I>) throws -> I? { try call("enumerate inputs", iterator.next) }
    func sameState(_ admitted: S, _ candidate: S) throws -> Bool { try call("state equality") { try equal(copied(m.cloneState, admitted), copied(m.cloneState, candidate)) } }
    func payload(_ input: I, _ outputs: [O]) -> Result<(I, [O]), Error> { Result { (try copyInput(input), try copyOutputs(outputs)) } }
    func witness(_ cursor: Int, _ input: I) throws -> [I] { var w = try path(cursor); w.append(try copyInput(input)); return w }
    func annotations(_ before: S, _ input: I, _ successor: Result<S, Error>, _ t: Transition<S, O>) throws -> [String] {
        guard let annotate = options.annotateEdge else { return [] }
        let snapshot = Transition(state: try copyState(successor.get()), outputs: try copyOutputs(t.outputs), disposition: t.disposition)
        return try call("annotate edge") { try annotate(copied(m.cloneState, before), copied(m.cloneInput, input), snapshot) }
    }
    func reductionClass(_ reduction: EquivalenceReduction<S, I, O>, _ before: S, _ input: I) throws -> String? {
        try call("reduction eligible_class") { try reduction.eligibleClass(copied(m.cloneState, before), copied(m.cloneInput, input)) }
    }
    func reductionProjection(_ reduction: EquivalenceReduction<S, I, O>, _ input: I, _ successor: S, _ t: Transition<S, O>, _ labels: [String]) throws -> [UInt8] {
        let observed = Transition(state: try copyState(successor), outputs: try copyOutputs(t.outputs), disposition: t.disposition)
        return try call("reduction projection") { try reduction.projection(copied(m.cloneInput, input), observed, labels) }
    }
    func execute() throws {
        if stopped() { return }
        let initial = try searchInitial(m)
        // Checks can invoke application code with aliases to the returned value.
        let initialSnapshot = detach(initial)
        r.states = 1
        let initialBad = try searchInitialChecks(m, state: initial, skipped: &r.skippedChecks, observer: options.observer, storage: storage)
        let initialChecks = storage.checks
        if !initialBad.isEmpty {
            try recordFinding(initialBad, edge: nil, earlier: false) { [] }
            if !collect { r.termination = "failure_found"; return }
        }
        if stopped() { return }
        let retainedInitial = try initialSnapshot.get()
        let initialBytes = try estimate(retainedInitial)
        r.estimatedRetainedStateBytes = initialBytes
        if stopped() { return }
        if let maximum = limits.maxEstimatedStateBytes {
            guard let bytes = initialBytes else { throw ModelError("state byte budget requires estimated_state_bytes") }
            if bytes > maximum { r.states = 0; r.estimatedRetainedStateBytes = 0; r.termination = "estimated_state_byte_limit"; return }
        }
        let initialHash = try hash(retainedInitial)
        if stopped() { return }
        guard chargeGraph(saturatingAdd(UInt64(MemoryLayout<GraphNode<S, I>>.stride), checkBytes(initialChecks))) else {
            r.states = 0; r.estimatedRetainedStateBytes = 0; r.termination = "graph_retention_limit"; return
        }
        r.nodes.append(GraphNode(id: 0, state: retainedInitial, parent: nil, input: nil, admittingEdge: nil, depth: 0, checks: initialChecks, pathFailed: !initialBad.isEmpty, expansion: .unexpanded))
        visited[initialHash] = [0]
        try emit(.rootAdmitted(0))
        if !initialBad.isEmpty && !expandFailing { r.failurePruned = true; try finishNode(0, .failurePruned) }
        var cursor = 0
        while cursor < r.nodes.count {
            let current = r.nodes[cursor]
            if current.depth > currentLayer { try completeLayer(); currentLayer = current.depth }
            if current.expansion == .failurePruned { cursor += 1; continue }
            guard try expand(cursor, current) else { return }
            cursor += 1
        }
        try completeLayer()
        if r.depthCutoff { r.termination = "depth_bound" }
    }
    /// Expands one dequeued node. False when the search ends.
    func expand(_ cursor: Int, _ current: GraphNode<S, I>) throws -> Bool {
        if stopped() { return false }
        activeNode = cursor
        try emit(.expansionStarted(cursor))
        let iterator = try enumerate(current.state)
        if stopped() { return false }
        if current.depth == config.maxDepth {
            let item = try pull(iterator)
            if stopped() { return false }
            if item != nil { r.depthCutoff = true }
            try finishNode(cursor, item != nil ? .depthFrontier : .complete); return true
        }
        var representatives: [ExactString: [Representative<S>]] = [:]
        while true {
            if stopped() { return false }
            let item = try pull(iterator)
            if stopped() { return false }
            guard let input = item else { break }
            if r.transitions == config.maxTransitions { r.termination = "transition_limit"; return false }
            guard try evaluate(cursor, current, input, &representatives) else { return false }
        }
        try finishNode(cursor, .complete)
        return true
    }
    /// Evaluates and resolves one edge. False when the search ends.
    func evaluate(_ cursor: Int, _ current: GraphNode<S, I>, _ input: I, _ representatives: inout [ExactString: [Representative<S>]]) throws -> Bool {
        let retainedInput = try copyInput(input)
        let transition = try perform(m, current.state, retainedInput)
        r.transitions += 1; r.maxDepthReached = max(r.maxDepthReached, current.depth + 1)
        // Detach the successor and edge payload before checks/observers can
        // mutate an external alias. Copy failures surface only when used.
        let successor = detach(transition.state)
        let edgePayload = needsEdgeRecord ? payload(retainedInput, transition.outputs) : nil
        var history: [I] = []
        if options.observer?.needsHistory() == true { history = try witness(cursor, retainedInput) }
        let bad = try searchEdge(m, state: current.state, input: retainedInput, transition: transition, skipped: &r.skippedChecks, observer: options.observer, history: history, storage: storage)
        let stateChecks = Array(storage.checks.prefix(storage.stateCount)), transitionChecks = Array(storage.checks.dropFirst(storage.stateCount))
        let labels = try annotations(current.state, retainedInput, successor, transition)
        let edgeID = nextEdge; nextEdge += 1; pendingEdge = edgeID
        var retained = false
        if let edgePayload {
            let (edgeInput, outputs) = try edgePayload.get()
            let edge = GraphEdge(id: edgeID, source: cursor, input: edgeInput, disposition: transition.disposition, outputs: outputs, stateChecks: stateChecks, transitionChecks: transitionChecks, annotations: labels, target: .pending)
            if r.edgesRetained {
                if let maximum = options.retention.maxRetainedEdges, r.edges.count >= maximum { r.edgesRetained = false }
                else if !chargeGraph(edgeBytes(edge)) { r.edgesRetained = false }
                else { r.edges.append(edge); retained = true }
            }
            try emit(.edgeEvaluated(try detached(edge)))
        }
        if !bad.isEmpty {
            try recordFinding(bad, edge: edgeID, earlier: current.pathFailed) { try witness(cursor, retainedInput) }
            if !collect { r.termination = "failure_found"; return false }
        }
        if options.retention.retainEdges && !retained {
            // The graph can no longer be retained completely.
            r.termination = "graph_retention_limit"; try reject(edgeID, "graph_retention_limit"); return false
        }
        if stopped() { try reject(edgeID, r.termination); return false }
        let retainedSuccessor = try successor.get()
        if let reduction = options.reduction {
            let label = try reductionClass(reduction, current.state, retainedInput)
            if stopped() { try reject(edgeID, r.termination); return false }
            if let label {
                let key = ExactString(label)
                let projection = try reductionProjection(reduction, retainedInput, retainedSuccessor, transition, labels)
                if stopped() { try reject(edgeID, r.termination); return false }
                var rejected: [RejectedEquivalence] = [], reducedTo: GraphEdgeID?
                for rep in representatives[key] ?? [] {
                    let reason: String?
                    if rep.disposition != transition.disposition { reason = "disposition_differs" }
                    else if rep.checks != storage.checks { reason = "checks_differ" }
                    else if rep.projection != projection { reason = "projection_differs" }
                    else {
                        if stopped() { try reject(edgeID, r.termination); return false }
                        let same = try sameState(rep.state, retainedSuccessor)
                        if stopped() { try reject(edgeID, r.termination); return false }
                        reason = same ? nil : "successor_differs"
                    }
                    if let reason { rejected.append(RejectedEquivalence(representative: rep.edge, reason: reason)) } else { reducedTo = rep.edge; break }
                }
                r.reductionDecisions.append(ReductionDecision(edge: edgeID, source: cursor, equivalenceClass: label, reducedTo: reducedTo, rejected: rejected))
                if let rep = reducedTo {
                    setTarget(edgeID, .reduced(rep)); r.reducedEdges += 1
                    try emit(.edgeReduced(edge: edgeID, representative: rep)); return true
                }
                representatives[key, default: []].append(Representative(edge: edgeID, state: try copyState(retainedSuccessor), disposition: transition.disposition, checks: storage.checks, projection: projection))
            }
        }
        let key = try hash(retainedSuccessor)
        if stopped() { try reject(edgeID, r.termination); return false }
        var duplicate: Int?
        // Rust collision chains visit the most recently admitted state first.
        for index in (visited[key] ?? []).reversed() {
            if stopped() { try reject(edgeID, r.termination); return false }
            let same = try sameState(r.nodes[index].state, retainedSuccessor)
            if stopped() { try reject(edgeID, r.termination); return false }
            if same { duplicate = index; break }
        }
        if let existing = duplicate {
            setTarget(edgeID, .duplicate(existing)); r.duplicateEdges += 1; r.graphEdges += 1
            try emit(.duplicateEdge(edge: edgeID, existing: existing)); return true
        }
        if !bad.isEmpty && !expandFailing { r.failurePruned = true; try reject(edgeID, "failing_successor"); return true }
        if r.nodes.count == config.maxStates { r.termination = "state_limit"; try reject(edgeID, "state_limit"); return false }
        let successorBytes = try estimate(retainedSuccessor)
        if stopped() { try reject(edgeID, r.termination); return false }
        let total: UInt64?
        if let retainedBytes = r.estimatedRetainedStateBytes, let bytes = successorBytes {
            let (sum, overflow) = retainedBytes.addingReportingOverflow(bytes); total = overflow ? nil : sum
        } else { total = nil }
        if let maximum = limits.maxEstimatedStateBytes {
            guard successorBytes != nil else { throw ModelError("state byte budget requires estimated_state_bytes") }
            guard let sum = total, sum <= maximum else { r.termination = "estimated_state_byte_limit"; try reject(edgeID, "estimated_state_byte_limit"); return false }
        }
        guard chargeGraph(saturatingAdd(UInt64(MemoryLayout<GraphNode<S, I>>.stride), checkBytes(stateChecks))) else {
            r.termination = "graph_retention_limit"; try reject(edgeID, "graph_retention_limit"); return false
        }
        let ownedInput = try copyInput(retainedInput)
        let id = r.nodes.count
        visited[key, default: []].append(id)
        r.nodes.append(GraphNode(id: id, state: retainedSuccessor, parent: cursor, input: ownedInput, admittingEdge: edgeID, depth: current.depth + 1, checks: stateChecks, pathFailed: current.pathFailed || !bad.isEmpty, expansion: .unexpanded))
        r.states = r.nodes.count; r.estimatedRetainedStateBytes = total; r.graphEdges += 1
        setTarget(edgeID, .admitted(id))
        try emit(.successorAdmitted(edge: edgeID, node: id))
        return true
    }
}

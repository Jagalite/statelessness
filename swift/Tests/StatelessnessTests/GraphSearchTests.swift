import Foundation
import XCTest
import Statelessness

/// Hand-derived expectations for graph-search-v1 (S1/S2). Graphs are small
/// integer models; step uses the input as the successor unless noted.
private func graphModel(_ edges: [Int: [Int]], hash: ((Int) -> UInt64)? = nil, failState: Set<Int> = [], failEdge: Set<[Int]> = []) -> Model<Int, Int, Int> {
    Model(initialState: { 0 }, step: { _, input in Transition(state: input, outputs: [input * 10]) },
          checkState: { failState.contains($0) ? [.passed("ok"), .failed("bad_state", "state \($0)")] : [.passed("ok")] },
          cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
          checkTransition: { before, input, _ in failEdge.contains([before, input]) ? [.failed("bad_edge", "\(before)->\(input)")] : [] },
          inputs: { InputIterator(values: edges[$0] ?? []) }, equalStates: ==, hashState: { hash?($0) ?? UInt64($0) })
}
private let diamond: [Int: [Int]] = [0: [1, 2, 1], 1: [3], 2: [3]]
private func describe(_ e: GraphEvent<Int, Int>) -> String {
    switch e {
    case let .rootAdmitted(n): return "root:\(n)"
    case let .expansionStarted(n): return "expand:\(n)"
    case let .edgeEvaluated(edge): return "edge:\(edge.id):\(edge.source):\(edge.input)"
    case let .finding(index, edge, violations): return "finding:\(index):\(edge.map(String.init) ?? "-"):\(violations.map { "\($0.phase)/\($0.check.id)" }.joined(separator: ","))"
    case let .successorAdmitted(edge, node): return "admit:\(edge):\(node)"
    case let .duplicateEdge(edge, existing): return "dup:\(edge):\(existing)"
    case let .edgeReduced(edge, rep): return "reduced:\(edge):\(rep)"
    case let .successorRejected(edge, reason): return "reject:\(edge):\(reason)"
    case let .expansionFinished(n, status): return "finish:\(n):\(status)"
    case let .layerCompleted(depth): return "layer:\(depth)"
    case let .searchFinished(termination): return "end:\(termination)"
    }
}
private func search(_ m: Model<Int, Int, Int>, config: SearchConfig = SearchConfig(), limits: SearchLimits = SearchLimits(), policy: FailurePolicy = .stopAtFirst,
                 retention: GraphRetention = GraphRetention(), cancelAfter: String? = nil) throws -> (GraphSearchReport<Int, Int, Int>, [String]) {
    var events: [String] = [], cancelled = false
    let control = RunLimits(maxDuration: limits.control.maxDuration, isCancelled: { cancelled })
    let options = GraphSearchOptions<Int, Int, Int>(failurePolicy: policy, retention: retention, events: { e in
        let text = describe(e); events.append(text); if text == cancelAfter { cancelled = true }
    })
    let r = try exploreGraph(m, config: config, limits: SearchLimits(control: control, maxEstimatedStateBytes: limits.maxEstimatedStateBytes), options: options)
    return (r, events)
}
private func targets(_ r: GraphSearchReport<Int, Int, Int>) -> [EdgeTarget] { r.edges.map(\.target) }
private func corpus() throws -> [[String: Any]] {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    return (try JSONSerialization.jsonObject(with: Data(contentsOf: root.appendingPathComponent("conformance/bfs-controls-v1.json"))) as! [String: Any])["cases"] as! [[String: Any]]
}

final class GraphSearchTests: XCTestCase {
    /// Default options must execute the bfs-controls-v1 callback sequence and
    /// report the same counters and witness as the portable kernel.
    func testDefaultOptionsReproduceBFSControlsCorpus() throws {
        for c in try corpus() {
            let name = c["case"] as! String, fixture = c["fixture"] as! String
            func execute(graph: Bool) -> (String, [String]) {
                let cancelAt = c["cancel_at"] as? String, errorAt = c["error_at"] as? String, entry = c["entry_control"] as! String
                var cancelled = entry == "cancelled" || entry == "both", events: [String] = []
                func event(_ text: String) throws { events.append(text); if text == cancelAt { cancelled = true }; if text == errorAt { throw ModelError("fixture check error") } }
                let estimates = c["estimates"] as! [Any]
                let m = Model<Int, Int, Int>(initialState: { try event("initial"); return 0 }, step: { state, input in
                    try event("step:\(state):\(input)"); return Transition(state: fixture == "diamond" ? input : state + input)
                }, checkState: { state in
                    try event("state:\(state)")
                    var checks = [Check.passed("ok")]
                    if c["skipped"] as! Bool { checks.append(.skipped("skip", "not run")) }
                    if (state == 0 && c["fail_initial"] as! Bool) || state == c["fail_state"] as? Int { checks.append(.failed("bad", "broken")) }
                    return checks
                }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, checkTransition: { before, input, t in
                    try event("transition:\(before):\(input):\(t.state)"); return []
                }, inputs: { state in
                    try event("inputs:\(state)")
                    let values: [Int]
                    switch fixture {
                    case "chain": values = state < 2 ? [1] : []
                    case "loop": values = state == 0 ? [0, 0] : []
                    case "diamond": values = state == 0 ? [1, 2, 1] : state == 1 || state == 2 ? [3] : []
                    default: values = []
                    }
                    var index = 0
                    return InputIterator {
                        if index == values.count { try event("next:\(state):end"); return nil }
                        let value = values[index]; index += 1; try event("next:\(state):\(value)"); return value
                    }
                }, equalStates: { a, b in try event("equal:\(a):\(b)"); return a == b }, hashState: { state in try event("hash:\(state)"); return 0 }, estimatedStateBytes: { state in
                    try event("estimate:\(state)"); return (estimates[state] as? String).flatMap(UInt64.init)
                })
                let observer = SearchObserver<Int, Int, Int>(initial: { state, _ in try event("observe_initial:\(state)") }, checkError: { phase, checks, complete in
                    try event("check_error:\(phase):\(complete):\(checks.count)")
                }, transition: { before, input, t, _, _, history in
                    try event("observe:\(before):\(input):\(t.state):\(history.isEmpty ? "-" : history.map(String.init).joined(separator: ","))")
                }, needsHistory: { c["needs_history"] as! Bool })
                let control = RunLimits(maxDuration: entry == "deadline" || entry == "both" ? 0 : nil, isCancelled: { cancelled })
                let limits = SearchLimits(control: control, maxEstimatedStateBytes: (c["max_estimated_state_bytes"] as? String).flatMap(UInt64.init))
                let config = SearchConfig(maxStates: c["max_states"] as! Int, maxTransitions: (c["max_transitions"] as! NSNumber).uint64Value, maxDepth: c["max_depth"] as! Int)
                do {
                    if graph {
                        let r = try exploreGraph(m, config: config, limits: limits, options: GraphSearchOptions(observer: observer))
                        return ("\(r.termination) \(r.states) \(r.transitions) \(r.maxDepthReached) \(r.skippedChecks) \(String(describing: r.estimatedRetainedStateBytes)) \(String(describing: r.failure?.inputs)) \(String(describing: r.failure?.violations))", events)
                    }
                    let r = try enumerateObserved(m, config: config, limits: limits, observer: observer)
                    return ("\(r.termination) \(r.states) \(r.transitions) \(r.maxDepthReached) \(r.skippedChecks) \(String(describing: r.estimatedRetainedStateBytes)) \(String(describing: r.failure?.inputs)) \(String(describing: r.failure?.violations))", events)
                } catch { return ("error \(String(describing: error))", events) }
            }
            let portable = execute(graph: false), extended = execute(graph: true)
            XCTAssertEqual(extended.0, portable.0, name); XCTAssertEqual(extended.1, portable.1, name)
        }
    }
    func testDiamondGraphIDsAndEventOrder() throws {
        let (r, events) = try search(graphModel(diamond))
        XCTAssertEqual(events, ["root:0", "expand:0", "edge:0:0:1", "admit:0:1", "edge:1:0:2", "admit:1:2", "edge:2:0:1", "dup:2:1", "finish:0:complete", "layer:0",
                                "expand:1", "edge:3:1:3", "admit:3:3", "finish:1:complete", "expand:2", "edge:4:2:3", "dup:4:3", "finish:2:complete", "layer:1",
                                "expand:3", "finish:3:complete", "layer:2", "end:graph_exhausted"])
        XCTAssertEqual(r.coverage, .exhaustive); XCTAssertEqual(r.termination, "graph_exhausted")
        XCTAssertEqual(r.nodes.map(\.state), [0, 1, 2, 3]); XCTAssertEqual(r.nodes.map(\.parent), [nil, 0, 0, 1]); XCTAssertEqual(r.nodes.map(\.depth), [0, 1, 1, 2])
        XCTAssertEqual(r.nodes.map(\.admittingEdge), [nil, 0, 1, 3])
        XCTAssertEqual(targets(r), [.admitted(1), .admitted(2), .duplicate(1), .admitted(3), .duplicate(3)])
        XCTAssertEqual(r.edges.map(\.outputs), [[10], [20], [10], [30], [30]])
        XCTAssertEqual(r.transitions, 5); XCTAssertEqual(r.graphEdges, 5); XCTAssertEqual(r.duplicateEdges, 2); XCTAssertEqual(r.rejectedSuccessors, 0)
        XCTAssertEqual(r.frontierSize, 0); XCTAssertEqual(r.completedLayers, 3); XCTAssertEqual(r.states, 4); XCTAssertNil(r.failure)
        XCTAssertGreaterThan(r.estimatedGraphBytes, 0)
    }
    func testSelfLoopAndHashCollisionsKeepStructure() throws {
        let (loop, _) = try search(graphModel([0: [0, 0]]))
        XCTAssertEqual(targets(loop), [.duplicate(0), .duplicate(0)]); XCTAssertEqual(loop.nodes.count, 1); XCTAssertEqual(loop.coverage, .exhaustive)
        let (collide, events) = try search(graphModel(diamond, hash: { _ in 7 }))
        let (distinct, expected) = try search(graphModel(diamond))
        XCTAssertEqual(events, expected); XCTAssertEqual(targets(collide), targets(distinct)); XCTAssertEqual(collide.nodes.map(\.state), distinct.nodes.map(\.state))
    }
    func testDuplicateFailingEdgeKeepsEvidence() throws {
        let (r, events) = try search(graphModel(diamond, failEdge: [[2, 3]]), policy: .collect(maxRetainedFindings: 10, expandFailingSuccessors: false))
        XCTAssertTrue(events.contains("finding:0:4:transition/bad_edge"))
        XCTAssertEqual(r.edges[4].target, .duplicate(3)); XCTAssertTrue(r.edges[4].failed)
        XCTAssertEqual(r.findings.map(\.witness), [[2, 3]]); XCTAssertEqual(r.findings[0].edge, 4)
        // A failing edge to an admitted state prunes nothing.
        XCTAssertEqual(r.coverage, .exhaustive); XCTAssertEqual(r.termination, "graph_exhausted")
        let (stop, _) = try search(graphModel(diamond, failEdge: [[2, 3]]))
        XCTAssertEqual(stop.termination, "failure_found"); XCTAssertEqual(stop.coverage, .incomplete("failure_found"))
        XCTAssertEqual(stop.edges.last?.target, .rejected("failure_found")); XCTAssertEqual(stop.nodes[2].expansion, .interrupted("failure_found"))
        XCTAssertEqual(stop.failure?.inputs, [2, 3]); XCTAssertEqual(stop.rejectedSuccessors, 1)
        // The unresolved edge is counted even when only streamed.
        let (streamed, _) = try search(graphModel(diamond, failEdge: [[2, 3]]), retention: GraphRetention(retainEdges: false))
        XCTAssertEqual(streamed.rejectedSuccessors, 1); XCTAssertTrue(streamed.edges.isEmpty)
    }
    func testStateCapOnNewSuccessor() throws {
        let (r, events) = try search(graphModel(diamond), config: SearchConfig(maxStates: 2))
        XCTAssertEqual(Array(events.suffix(3)), ["edge:1:0:2", "reject:1:state_limit", "end:state_limit"])
        XCTAssertEqual(r.termination, "state_limit"); XCTAssertEqual(r.coverage, .incomplete("state_limit"))
        XCTAssertEqual(targets(r), [.admitted(1), .rejected("state_limit")]); XCTAssertEqual(r.rejectedSuccessors, 1)
        XCTAssertEqual(r.nodes.map(\.expansion), [.interrupted("state_limit"), .unexpanded]); XCTAssertEqual(r.frontierSize, 2); XCTAssertEqual(r.completedLayers, 0)
    }
    func testEdgeCapMidwayThroughNode() throws {
        let (r, _) = try search(graphModel(diamond), config: SearchConfig(maxTransitions: 2))
        XCTAssertEqual(r.termination, "transition_limit"); XCTAssertEqual(r.transitions, 2); XCTAssertEqual(r.edges.count, 2)
        XCTAssertEqual(r.nodes[0].expansion, .interrupted("transition_limit")); XCTAssertEqual(r.coverage, .incomplete("transition_limit"))
    }
    func testCancellationAndDeadline() throws {
        let (r, events) = try search(graphModel(diamond), cancelAfter: "admit:3:3")
        XCTAssertEqual(r.termination, "cancelled"); XCTAssertEqual(events.last, "end:cancelled")
        // The admission completes; the following poll stops node 1 before it finishes.
        XCTAssertEqual(r.nodes.map(\.expansion), [.complete, .interrupted("cancelled"), .unexpanded, .unexpanded]); XCTAssertEqual(r.frontierSize, 3)
        let (late, _) = try search(graphModel(diamond), cancelAfter: "edge:4:2:3")
        XCTAssertEqual(late.edges[4].target, .rejected("cancelled")); XCTAssertEqual(late.rejectedSuccessors, 1)
        let (expired, none) = try search(graphModel(diamond), limits: SearchLimits(control: RunLimits(maxDuration: 0)))
        XCTAssertEqual(expired.termination, "deadline"); XCTAssertEqual(expired.states, 0); XCTAssertTrue(expired.nodes.isEmpty); XCTAssertEqual(none, ["end:deadline"])
    }
    func testZeroDepthAndDepthBoundCoverage() throws {
        let (zero, events) = try search(graphModel(diamond), config: SearchConfig(maxDepth: 0))
        XCTAssertEqual(events, ["root:0", "expand:0", "finish:0:depthFrontier", "layer:0", "end:depth_bound"])
        XCTAssertEqual(zero.termination, "depth_bound"); XCTAssertEqual(zero.coverage, .bounded(depthCutoff: true, failurePruned: false)); XCTAssertEqual(zero.transitions, 0)
        let (one, _) = try search(graphModel(diamond), config: SearchConfig(maxDepth: 1))
        XCTAssertEqual(one.coverage, .bounded(depthCutoff: true, failurePruned: false)); XCTAssertEqual(one.frontierSize, 2)
        // Reaching max_depth at a leaf is not a cutoff.
        let (leaf, _) = try search(graphModel(diamond), config: SearchConfig(maxDepth: 2))
        XCTAssertEqual(leaf.coverage, .exhaustive); XCTAssertEqual(leaf.termination, "graph_exhausted")
    }
    func testRetentionLimitsAreVisible() throws {
        let (capped, _) = try search(graphModel(diamond), retention: GraphRetention(maxRetainedEdges: 3))
        XCTAssertEqual(capped.termination, "graph_retention_limit"); XCTAssertEqual(capped.edges.count, 3); XCTAssertFalse(capped.edgesRetained)
        XCTAssertEqual(capped.coverage, .incomplete("graph_retention_limit"))
        let (streamed, events) = try search(graphModel(diamond), retention: GraphRetention(retainEdges: false))
        XCTAssertTrue(streamed.edges.isEmpty); XCTAssertFalse(streamed.edgesRetained); XCTAssertEqual(streamed.coverage, .exhaustive)
        XCTAssertEqual(streamed.graphEdges, 5); XCTAssertEqual(events.filter { $0.hasPrefix("edge:") }.count, 5)
        let (tiny, _) = try search(graphModel(diamond), retention: GraphRetention(maxGraphBytes: 1))
        XCTAssertEqual(tiny.termination, "graph_retention_limit"); XCTAssertEqual(tiny.states, 0)
        XCTAssertThrowsError(try search(graphModel(diamond), retention: GraphRetention(maxRetainedEdges: -1)))
        XCTAssertThrowsError(try search(graphModel(diamond), policy: .collect(maxRetainedFindings: -1, expandFailingSuccessors: true)))
    }
    // MARK: S2 failure collection
    private let chain: [Int: [Int]] = [0: [1], 1: [2], 2: [3], 3: [4]]
    func testFirstAndSubsequentFailures() throws {
        let (r, _) = try search(graphModel(chain, failState: [2, 3, 4]), policy: .collect(maxRetainedFindings: 10, expandFailingSuccessors: true))
        XCTAssertEqual(r.findings.map(\.witness), [[1, 2], [1, 2, 3], [1, 2, 3, 4]])
        XCTAssertEqual(r.findings.map(\.requiresContinuedEvidence), [false, true, true])
        XCTAssertEqual(r.findings.map(\.index), [0, 1, 2]); XCTAssertEqual(r.findingCount, 3); XCTAssertFalse(r.findingsTruncated)
        XCTAssertEqual(r.failedProperties, [PropertyFailureCount(phase: "state", id: "bad_state", count: 3, firstFinding: 0)])
        XCTAssertEqual(r.coverage, .exhaustive); XCTAssertEqual(r.nodes.map(\.pathFailed), [false, false, true, true, true])
        XCTAssertEqual(r.failure?.inputs, [1, 2])
        let (pruned, _) = try search(graphModel(chain, failState: [2, 3, 4]), policy: .collect(maxRetainedFindings: 10, expandFailingSuccessors: false))
        XCTAssertEqual(pruned.findingCount, 1); XCTAssertEqual(pruned.nodes.count, 2); XCTAssertEqual(pruned.edges.last?.target, .rejected("failing_successor"))
        XCTAssertEqual(pruned.coverage, .bounded(depthCutoff: false, failurePruned: true)); XCTAssertEqual(pruned.termination, "graph_exhausted")
    }
    func testMultiplePhasesOnOneEdgeAreOrdered() throws {
        let (r, events) = try search(graphModel(chain, failState: [1], failEdge: [[0, 1]]), policy: .collect(maxRetainedFindings: 10, expandFailingSuccessors: true))
        XCTAssertEqual(r.findings.count, 1)
        XCTAssertEqual(r.findings[0].violations.map { "\($0.phase)/\($0.check.id)" }, ["state/bad_state", "transition/bad_edge"])
        XCTAssertTrue(events.contains("finding:0:0:state/bad_state,transition/bad_edge"))
        XCTAssertEqual(r.failedProperties.map(\.id), ["bad_state", "bad_edge"])
        XCTAssertEqual(r.edges[0].stateChecks, [.passed("ok"), .failed("bad_state", "state 1")]); XCTAssertEqual(r.edges[0].transitionChecks, [.failed("bad_edge", "0->1")])
    }
    func testInitialFailureUnderCollection() throws {
        let (expand, _) = try search(graphModel(chain, failState: [0]), policy: .collect(maxRetainedFindings: 10, expandFailingSuccessors: true))
        XCTAssertEqual(expand.findings.map(\.witness), [[]]); XCTAssertNil(expand.findings[0].edge); XCTAssertEqual(expand.nodes.count, 5)
        XCTAssertEqual(expand.findings[0].violations.first?.phase, "initial_state"); XCTAssertEqual(expand.coverage, .exhaustive)
        let (pruned, events) = try search(graphModel(chain, failState: [0]), policy: .collect(maxRetainedFindings: 10, expandFailingSuccessors: false))
        XCTAssertEqual(events, ["finding:0:-:initial_state/bad_state", "root:0", "finish:0:failurePruned", "layer:0", "end:graph_exhausted"])
        XCTAssertEqual(pruned.coverage, .bounded(depthCutoff: false, failurePruned: true))
    }
    func testFailurePlusCutoff() throws {
        let (r, _) = try search(graphModel(chain, failState: [2]), config: SearchConfig(maxStates: 3), policy: .collect(maxRetainedFindings: 10, expandFailingSuccessors: true))
        XCTAssertEqual(r.findingCount, 1); XCTAssertEqual(r.termination, "state_limit"); XCTAssertEqual(r.coverage, .incomplete("state_limit"))
        XCTAssertEqual(r.findings[0].witness, [1, 2])
    }
    func testCheckerErrorAfterPartialEvidence() throws {
        var m = graphModel(chain, failState: [2])
        let base = m.checkState
        m.checkState = { state in if state == 3 { throw ModelError("checker exploded") }; return try base(state) }
        XCTAssertThrowsError(try exploreGraph(m, options: GraphSearchOptions(failurePolicy: .collect(maxRetainedFindings: 10, expandFailingSuccessors: true)))) { error in
            guard let e = error as? GraphSearchError<Int, Int, Int> else { return XCTFail("\(error)") }
            XCTAssertTrue(e.description.contains("checker exploded"))
            XCTAssertEqual(e.report.termination, "model_error"); XCTAssertEqual(e.report.coverage, .incomplete("model_error"))
            XCTAssertEqual(e.report.findings.map(\.witness), [[1, 2]]); XCTAssertEqual(e.report.nodes[2].expansion, .interrupted("model_error"))
            // The failing batch never completed, so no record exists for its edge.
            XCTAssertEqual(e.report.edges.count, 2); XCTAssertEqual(e.report.edges.last?.target, .admitted(2))
            XCTAssertEqual(e.report.rejectedSuccessors, 0)
        }
    }
    func testDeterministicFindingTruncation() throws {
        let policy = FailurePolicy.collect(maxRetainedFindings: 1, expandFailingSuccessors: true)
        let (a, eventsA) = try search(graphModel(chain, failState: [2, 3, 4]), policy: policy)
        let (b, eventsB) = try search(graphModel(chain, failState: [2, 3, 4]), policy: policy)
        XCTAssertEqual(a.findings.map(\.witness), [[1, 2]]); XCTAssertEqual(a.findingCount, 3); XCTAssertTrue(a.findingsTruncated)
        XCTAssertEqual(a.failedProperties.first?.count, 3); XCTAssertEqual(eventsA, eventsB); XCTAssertEqual(b.findings.map(\.witness), [[1, 2]])
        let (none, _) = try search(graphModel(chain, failState: [2]), policy: .collect(maxRetainedFindings: 0, expandFailingSuccessors: true))
        XCTAssertTrue(none.findings.isEmpty); XCTAssertNil(none.failure); XCTAssertEqual(none.findingCount, 1); XCTAssertEqual(none.failedProperties.map(\.id), ["bad_state"])
    }
    func testAnnotationsAndDetachedEventPayloads() throws {
        final class Box { var n: Int; init(_ n: Int) { self.n = n } }
        let m = Model<Box, Box, Box>(initialState: { Box(0) }, step: { _, i in Transition(state: Box(i.n), outputs: [Box(i.n)]) }, checkState: { _ in [] },
                                     cloneState: { Box($0.n) }, cloneInput: { Box($0.n) }, cloneOutput: { Box($0.n) },
                                     inputs: { s in InputIterator(values: (s.n == 0 ? [1, 2] : []).map(Box.init)) }, equalStates: { $0.n == $1.n })
        let options = GraphSearchOptions<Box, Box, Box>(events: { e in if case let .edgeEvaluated(edge) = e { edge.input.n = 99; edge.outputs.forEach { $0.n = 99 } } },
                                                        annotateEdge: { before, input, t in input.n = 77; return ["from \(before.n) to \(t.state.n)"] })
        let r = try exploreGraph(m, options: options)
        XCTAssertEqual(r.edges.map(\.input.n), [1, 2]); XCTAssertEqual(r.edges.map { $0.outputs.map(\.n) }, [[1], [2]])
        XCTAssertEqual(r.edges.map(\.annotations), [["from 0 to 1"], ["from 0 to 2"]]); XCTAssertEqual(r.nodes.map(\.state.n), [0, 1, 2])
    }
    func testSinkErrorAbortsWithPartialReport() throws {
        let options = GraphSearchOptions<Int, Int, Int>(events: { e in if case .successorAdmitted(_, 2) = e { throw ModelError("sink full") } })
        XCTAssertThrowsError(try exploreGraph(graphModel(diamond), options: options)) { error in
            let e = error as! GraphSearchError<Int, Int, Int>
            XCTAssertTrue(e.description.contains("sink full")); XCTAssertEqual(e.report.nodes.count, 3); XCTAssertEqual(e.report.coverage, .incomplete("model_error"))
        }
    }
}

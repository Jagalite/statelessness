import Foundation
import XCTest
import Statelessness

/// Consumes the published graph-search-v1 and progress-analysis-v1 fixtures.
private func fixture(_ name: String) throws -> [[String: Any]] {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    let doc = try JSONSerialization.jsonObject(with: Data(contentsOf: root.appendingPathComponent("conformance/\(name)"))) as! [String: Any]
    XCTAssertEqual(doc["version"] as? Int, 1)
    return doc["cases"] as! [[String: Any]]
}
private func spelled(_ e: GraphEvent<Int, Int>) -> String {
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
private func spelled(_ t: EdgeTarget) -> String {
    switch t {
    case .pending: return "pending"
    case let .admitted(n): return "admitted:\(n)"
    case let .duplicate(n): return "duplicate:\(n)"
    case let .reduced(e): return "reduced:\(e)"
    case let .rejected(reason): return "rejected:\(reason)"
    }
}
private func spelled(_ c: GraphCoverage) -> String {
    switch c {
    case .exhaustive: return "exhaustive"
    case let .bounded(depth, failure): return "bounded:" + [depth ? "depth_cutoff" : nil, failure ? "failure_pruned" : nil].compactMap { $0 }.joined(separator: "+")
    case let .incomplete(reason): return "incomplete:\(reason)"
    }
}
final class MigrationFixtureTests: XCTestCase {
    func testGraphSearchFixtures() throws {
        let cases = try fixture("graph-search-v1.json")
        XCTAssertEqual(cases.count, 10)
        for c in cases {
            let name = c["case"] as! String
            let edges = Dictionary(uniqueKeysWithValues: (c["edges"] as! [String: [Int]]).map { (Int($0.key)!, $0.value) })
            let constant = c["hash"] as! String == "constant"
            let failState = Set(c["fail_state"] as! [Int]), failEdge = Set(c["fail_edge"] as! [[Int]])
            let m = Model<Int, Int, Int>(initialState: { 0 }, step: { _, i in Transition(state: i, outputs: [i * 10]) },
                                         checkState: { failState.contains($0) ? [.passed("ok"), .failed("bad_state", "state \($0)")] : [.passed("ok")] },
                                         cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
                                         checkTransition: { b, i, _ in failEdge.contains([b, i]) ? [.failed("bad_edge", "\(b)->\(i)")] : [] },
                                         inputs: { InputIterator(values: edges[$0] ?? []) }, equalStates: ==, hashState: { constant ? 0 : UInt64($0) })
            let cfg = c["config"] as! [String: Int], p = c["policy"] as! [String: Any]
            let policy: FailurePolicy = p["kind"] as! String == "collect" ? .collect(maxRetainedFindings: p["max_retained_findings"] as! Int, expandFailingSuccessors: p["expand_failing_successors"] as! Bool) : .stopAtFirst
            var events: [String] = []
            let r = try exploreGraph(m, config: SearchConfig(maxStates: cfg["max_states"]!, maxTransitions: UInt64(cfg["max_transitions"]!), maxDepth: cfg["max_depth"]!),
                                     options: GraphSearchOptions(failurePolicy: policy, events: { events.append(spelled($0)) }))
            let x = c["expected"] as! [String: Any]
            XCTAssertEqual(events, x["events"] as! [String], name)
            XCTAssertEqual(r.edges.map { spelled($0.target) }, x["targets"] as! [String], name)
            XCTAssertEqual(r.termination, x["termination"] as! String, name); XCTAssertEqual(spelled(r.coverage), x["coverage"] as! String, name)
            XCTAssertEqual(r.states, x["states"] as! Int, name); XCTAssertEqual(r.transitions, UInt64(x["transitions"] as! Int), name)
            XCTAssertEqual(r.findings.map(\.witness), x["finding_witnesses"] as! [[Int]], name)
            XCTAssertEqual(r.findings.map(\.requiresContinuedEvidence), x["requires_continued_evidence"] as! [Bool], name)
            XCTAssertEqual(r.findingCount, UInt64(x["finding_count"] as! Int), name)
        }
    }
    func testProgressAnalysisFixtures() throws {
        let cases = try fixture("progress-analysis-v1.json")
        XCTAssertEqual(cases.count, 9)
        for c in cases {
            let name = c["case"] as! String, n = c["nodes"] as! Int
            let edges = (c["edges"] as! [[Any]]).map { ProgressEdge(source: $0[0] as! Int, target: $0[1] as! Int, fair: $0[2] as! Bool) }
            let unmeasured = Set(c["unmeasured"] as! [Int])
            let graph = try ProgressGraph(nodeCount: n, edges: edges, measured: (0..<n).map { !unmeasured.contains($0) })
            let kinds = (c["kinds"] as! [String]).map { ["terminal": ProgressKind.terminal, "intentionalWait": .intentionalWait, "externalWait": .externalWait, "transaction": .transaction, "other": .other][$0]! }
            let ranks = c["ranks"] as? [Int]
            let r = try analyzeProgress(graph, kind: { kinds[$0] }, rank: ranks.map { values in { UInt64(values[$0]) } })
            let x = c["expected"] as! [String: Any]
            XCTAssertEqual(r.classes.map(\.rawValue), x["classes"] as! [String], name)
            XCTAssertEqual(r.closedNonterminalComponents, x["closed"] as! [[Int]], name)
            XCTAssertEqual(r.unsettledTransactionStates, x["unsettled"] as! [Int], name)
            XCTAssertEqual(r.settledTransactionStates, x["settled_transactions"] as! Int, name)
            XCTAssertEqual(r.unmeasuredComponents, x["unmeasured_components"] as! Int, name)
            XCTAssertEqual(r.graphComplete, x["graph_complete"] as! Bool, name)
        }
    }
}

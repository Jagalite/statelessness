import XCTest
import Statelessness

/// Hand-authored progress-analysis-v1 graphs, independent of any application.
private func graph(_ n: Int, _ edges: [(Int, Int, Bool)], unmeasured: Set<Int> = []) throws -> ProgressGraph {
    try ProgressGraph(nodeCount: n, edges: edges.map { ProgressEdge(source: $0.0, target: $0.1, fair: $0.2) }, measured: (0..<n).map { !unmeasured.contains($0) })
}
final class ProgressAnalysisTests: XCTestCase {
    func testSettlingTransaction() throws {
        let r = try analyzeProgress(try graph(2, [(0, 1, true)]), kind: { [.transaction, .terminal][$0] })
        XCTAssertEqual(r.classes, [.transactionProgress, .terminal]); XCTAssertEqual(r.transactionStates, 1); XCTAssertEqual(r.settledTransactionStates, 1)
        XCTAssertTrue(r.closedNonterminalComponents.isEmpty); XCTAssertTrue(r.unsettledTransactionStates.isEmpty); XCTAssertTrue(r.graphComplete)
    }
    func testIntentionalAndExternalWaits() throws {
        let r = try analyzeProgress(try graph(3, [(0, 1, true), (0, 2, true), (1, 1, false)]), kind: { [.transaction, .intentionalWait, .externalWait][$0] })
        XCTAssertEqual(r.classes, [.transactionProgress, .stableIntentionalWait, .declaredExternalWait])
        XCTAssertEqual(r.stableIntentionalWaitStates, 1); XCTAssertEqual(r.externalWaitStates, 1); XCTAssertEqual(r.settledTransactionStates, 1)
        // A self-looping wait is settled, not a closed nonterminal component.
        XCTAssertTrue(r.closedNonterminalComponents.isEmpty)
    }
    func testClosedNonterminalCycle() throws {
        let r = try analyzeProgress(try graph(3, [(0, 1, true), (1, 0, true), (2, 0, true)]), kind: { _ in .transaction }, rank: { _ in 4 })
        XCTAssertEqual(r.closedNonterminalComponents, [[0, 1]]); XCTAssertEqual(r.unsettledTransactionStates, [0, 1, 2]); XCTAssertEqual(r.maximumRank, 4)
    }
    func testUnfairOnlyExit() throws {
        let only = try analyzeProgress(try graph(2, [(0, 1, false)]), kind: { [.transaction, .terminal][$0] })
        XCTAssertEqual(only.classes, [.stuck, .terminal]); XCTAssertEqual(only.stuckStates, [0]); XCTAssertTrue(only.closedNonterminalComponents.isEmpty)
        let loop = try analyzeProgress(try graph(2, [(0, 0, true), (0, 1, false)]), kind: { [.transaction, .terminal][$0] })
        XCTAssertEqual(loop.classes, [.transactionProgress, .terminal]); XCTAssertEqual(loop.unsettledTransactionStates, [0])
        XCTAssertTrue(loop.closedNonterminalComponents.isEmpty, "an unfair exit still leaves the component")
    }
    func testFairRankDecreaseOpensComponent() throws {
        let ranks: [UInt64] = [2, 1]
        let decreasing = try analyzeProgress(try graph(2, [(0, 1, true), (1, 0, true)]), kind: { _ in .transaction }, rank: { ranks[$0] })
        XCTAssertTrue(decreasing.closedNonterminalComponents.isEmpty)
        let unfair = try analyzeProgress(try graph(2, [(0, 1, false), (1, 0, true)]), kind: { _ in .transaction }, rank: { ranks[$0] })
        XCTAssertEqual(unfair.closedNonterminalComponents, [[0, 1]], "an unfair decrease does not count")
    }
    func testDepthCutoffIsUnmeasured() throws {
        let r = try analyzeProgress(try graph(2, [(0, 1, true)], unmeasured: [1]), kind: { _ in .transaction })
        XCTAssertEqual(r.classes, [.transactionProgress, .unmeasuredCutoff]); XCTAssertEqual(r.unmeasuredStates, 1)
        XCTAssertEqual(r.unsettledTransactionStates, [0]); XCTAssertTrue(r.closedNonterminalComponents.isEmpty); XCTAssertFalse(r.graphComplete)
    }
    func testPartialExpansionBlocksClosedClaim() throws {
        // Node 1 was interrupted: its known edge closes a cycle but other edges are unknown.
        let r = try analyzeProgress(try graph(2, [(0, 1, true), (1, 0, true)], unmeasured: [1]), kind: { _ in .transaction })
        XCTAssertTrue(r.closedNonterminalComponents.isEmpty); XCTAssertEqual(r.unmeasuredComponents, 1)
    }
    func testIterativeSCCHandlesDeepGraphs() throws {
        let n = 200_000
        let chain = (0..<(n - 1)).map { ($0, $0 + 1) } + [(n - 1, 0)]
        let components = stronglyConnectedComponents(nodeCount: n, edges: chain)
        XCTAssertEqual(components.count, 1); XCTAssertEqual(components[0].count, n)
        XCTAssertEqual(stronglyConnectedComponents(nodeCount: 4, edges: [(0, 1), (1, 0), (2, 3)]), [[0, 1], [2], [3]])
        XCTAssertEqual(reverseReachable(nodeCount: 4, edges: [ProgressEdge(source: 0, target: 1, fair: true), ProgressEdge(source: 2, target: 1, fair: false)], targets: [1], include: \.fair), [true, true, false, false])
    }
    func testGraphFromSearchReport() throws {
        let m = Model<Int, Int, Int>(initialState: { 0 }, step: { _, i in Transition(state: i) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
                                     inputs: { InputIterator(values: [0: [1, 2], 1: [3], 2: [2]][$0] ?? []) }, equalStates: ==, hashState: { UInt64($0) })
        let report = try exploreGraph(m)
        let g = try ProgressGraph(report: report, fair: { $0.input != 2 })
        XCTAssertEqual(g.edges, [ProgressEdge(source: 0, target: 1, fair: true), ProgressEdge(source: 0, target: 2, fair: false), ProgressEdge(source: 1, target: 3, fair: true), ProgressEdge(source: 2, target: 2, fair: false)])
        let r = try analyzeProgress(g, kind: { report.nodes[$0].state == 3 ? .terminal : .transaction })
        XCTAssertEqual(r.classes, [.transactionProgress, .transactionProgress, .stuck, .terminal]); XCTAssertEqual(r.closedNonterminalComponents, [[2]])
        let bounded = try exploreGraph(m, config: SearchConfig(maxDepth: 1))
        XCTAssertEqual(try ProgressGraph(report: bounded, fair: { _ in true }).measured, [true, false, false])
        XCTAssertThrowsError(try ProgressGraph(nodeCount: 1, edges: [ProgressEdge(source: 0, target: 1, fair: true)], measured: [true]))
    }
}

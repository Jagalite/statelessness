import XCTest
import Statelessness

/// Each control introduces one defect and requires the library evidence to
/// expose it. Reduction tampering and replay corruption controls live in
/// ReductionTests and ContinuedEvidenceTests.
private func model(edges: [Int: [Int]] = [0: [1, 2], 1: [3], 2: [3]], equal: @escaping (Int, Int) -> Bool = (==), hash: @escaping (Int) -> UInt64 = { UInt64($0) },
                   failing: Set<Int> = [3]) -> Model<Int, Int, Int> {
    Model(initialState: { 0 }, step: { _, i in Transition(state: i) }, checkState: { failing.contains($0) ? [.failed("bad", "\($0)")] : [.passed("bad")] },
          cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { InputIterator(values: edges[$0] ?? []) }, equalStates: { equal($0, $1) }, hashState: hash)
}
private var collect: GraphSearchOptions<Int, Int, Int> { GraphSearchOptions(failurePolicy: .collect(maxRetainedFindings: 8, expandFailingSuccessors: true)) }
final class MigrationNegativeControlTests: XCTestCase {
    func testMissingTransitionIsDetected() throws {
        let full = try exploreGraph(model(), options: collect), missing = try exploreGraph(model(edges: [0: [1, 2], 1: [3]]), options: collect)
        let c = try compareReduction(model(), unreduced: full, reduced: missing)
        XCTAssertFalse(c.equivalent); XCTAssertEqual(c.unrepresentedEdges, [3]); XCTAssertFalse(c.findingsMatch)
    }
    func testIncorrectIdentityMergingIsDetected() throws {
        // States 1 and 2 are merged by a lossy equality with a colliding hash.
        let merged = model(equal: { $0 == $1 || ($0 > 0 && $0 < 3 && $1 > 0 && $1 < 3) }, hash: { $0 == 2 ? 1 : UInt64($0) })
        let correct = try exploreGraph(model(), options: collect), lossy = try exploreGraph(merged, options: collect)
        XCTAssertEqual(lossy.nodes.count, 3)
        let c = try compareReduction(model(), unreduced: correct, reduced: lossy)
        XCTAssertFalse(c.equivalent); XCTAssertEqual(c.missingInReduced, [2])
    }
    func testSuppressedCheckIsDetected() throws {
        let checked = try exploreGraph(model(), options: collect), suppressed = try exploreGraph(model(failing: []), options: collect)
        XCTAssertEqual(checked.findingCount, 2); XCTAssertEqual(suppressed.findingCount, 0)
        let c = try compareReduction(model(), unreduced: checked, reduced: suppressed)
        XCTAssertFalse(c.findingsMatch); XCTAssertEqual(c.unrepresentedEdges, [2, 3], "edges carry their check batches")
    }
    func testFalseProgressIsDetected() throws {
        // 0 <-> 1 is a closed transaction cycle. Omitting the back edge or
        // claiming an unmeasured node is complete changes the evidence.
        let truth = try analyzeProgress(try ProgressGraph(nodeCount: 2, edges: [ProgressEdge(source: 0, target: 1, fair: true), ProgressEdge(source: 1, target: 0, fair: true)], measured: [true, true]), kind: { _ in .transaction })
        XCTAssertEqual(truth.closedNonterminalComponents, [[0, 1]])
        let truncated = try analyzeProgress(try ProgressGraph(nodeCount: 2, edges: [ProgressEdge(source: 0, target: 1, fair: true)], measured: [true, false]), kind: { _ in .transaction })
        XCTAssertTrue(truncated.closedNonterminalComponents.isEmpty); XCTAssertFalse(truncated.graphComplete, "a cutoff is not reported as progress")
        XCTAssertNotEqual(truth, truncated)
        // A depth-bounded search cannot be mistaken for a complete graph.
        let bounded = try exploreGraph(model(), config: SearchConfig(maxDepth: 1), options: collect)
        XCTAssertFalse(try analyzeProgress(try ProgressGraph(report: bounded, fair: { _ in true }), kind: { _ in .transaction }).graphComplete)
    }
}

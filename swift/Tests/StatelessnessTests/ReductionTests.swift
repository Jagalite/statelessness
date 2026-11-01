import XCTest
import Statelessness

/// reduction-v1 fixtures. Inputs 10...19 are "late result" inputs; the class
/// is input/10. Unless a test overrides step, input k leads to state k%10.
private func model(step: @escaping (Int, Int) -> Transition<Int, Int> = { _, i in Transition(state: i % 10, outputs: [i % 10]) }, failEdge: Set<Int> = [],
                   inputs: [Int: [Int]] = [0: [10, 11, 12, 1], 1: [2], 2: []]) -> Model<Int, Int, Int> {
    Model(initialState: { 0 }, step: { step($0, $1) }, checkState: { _ in [.passed("ok")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
          checkTransition: { _, i, _ in failEdge.contains(i) ? [.failed("late", "\(i)")] : [] },
          inputs: { InputIterator(values: inputs[$0] ?? []) }, equalStates: ==, hashState: { UInt64($0) })
}
private func byDecade() -> EquivalenceReduction<Int, Int, Int> { EquivalenceReduction<Int, Int, Int>(eligibleClass: { _, i in i >= 10 ? "late" : nil }, projection: { _, t, _ in t.outputs.map(UInt8.init) }) }
private func explore(_ m: Model<Int, Int, Int>, reduction: EquivalenceReduction<Int, Int, Int>? = nil, policy: FailurePolicy = .collect(maxRetainedFindings: 10, expandFailingSuccessors: true)) throws -> GraphSearchReport<Int, Int, Int> {
    try exploreGraph(m, options: GraphSearchOptions(failurePolicy: policy, reduction: reduction))
}
final class ReductionTests: XCTestCase {
    func testEquivalentEdgesCollapseIntoRepresentative() throws {
        // 10 -> 0, 11 -> 1, 12 -> 2: distinct successors, so no collapse.
        let distinct = try explore(model(), reduction: byDecade())
        XCTAssertEqual(distinct.reducedEdges, 0); XCTAssertEqual(distinct.reductionDecisions.map(\.rejected.count), [0, 1, 2])
        XCTAssertEqual(distinct.reductionDecisions[1].rejected, [RejectedEquivalence(representative: 0, reason: "projection_differs")])
        // Every late input reaches state 1 with identical observations.
        let same = model(step: { _, i in Transition(state: i >= 10 ? 1 : i, outputs: [7]) })
        let unreduced = try explore(same), reduced = try explore(same, reduction: byDecade())
        XCTAssertEqual(reduced.edges.map(\.target), [.admitted(1), .reduced(0), .reduced(0), .duplicate(1), .admitted(2)])
        XCTAssertEqual(reduced.reducedEdges, 2); XCTAssertEqual(reduced.transitions, unreduced.transitions, "reduction never skips execution")
        XCTAssertEqual(reduced.reductionDecisions, [ReductionDecision(edge: 0, source: 0, equivalenceClass: "late", reducedTo: nil, rejected: []),
                                                    ReductionDecision(edge: 1, source: 0, equivalenceClass: "late", reducedTo: 0, rejected: []),
                                                    ReductionDecision(edge: 2, source: 0, equivalenceClass: "late", reducedTo: 0, rejected: [])])
        let comparison = try compareReduction(same, unreduced: unreduced, reduced: reduced)
        XCTAssertTrue(comparison.equivalent, "\(comparison)")
        // Progress results agree on the reduced graph.
        func progress(_ r: GraphSearchReport<Int, Int, Int>) throws -> ProgressReport {
            try analyzeProgress(try ProgressGraph(report: r, fair: { _ in true }), kind: { r.nodes[$0].state == 2 ? .terminal : .transaction })
        }
        XCTAssertEqual(try progress(reduced), try progress(unreduced))
        let predicates = ReductionProgressPredicates<Int, Int, Int>(fair: { _ in true }, kind: { $0 == 2 ? .terminal : .transaction })
        let withProgress = try compareReduction(same, unreduced: unreduced, reduced: reduced, progress: predicates)
        XCTAssertEqual(withProgress.progressMatch, true); XCTAssertTrue(withProgress.equivalent)
        XCTAssertNil(comparison.progressMatch, "progress is not compared without predicates")
    }
    func testProgressDifferenceIsNotEquivalent() throws {
        // Same states 0 -> 1, same dispositions and checks; only the input
        // differs, and fairness depends on it.
        func oneEdge(_ input: Int) -> Model<Int, Int, Int> { model(step: { _, _ in Transition(state: 1) }, inputs: [0: [input]]) }
        let a = try explore(oneEdge(5)), b = try explore(oneEdge(6))
        XCTAssertTrue(try compareReduction(oneEdge(5), unreduced: a, reduced: b).equivalent, "structure alone matches")
        let predicates = ReductionProgressPredicates<Int, Int, Int>(fair: { $0.input == 5 }, kind: { $0 == 1 ? .terminal : .transaction })
        let c = try compareReduction(oneEdge(5), unreduced: a, reduced: b, progress: predicates)
        XCTAssertEqual(c.progressMatch, false); XCTAssertEqual(c.progressMismatches, [0]); XCTAssertFalse(c.equivalent)
    }
    func testRejectedEquivalencesAreInspectable() throws {
        // Same projection, different successor: successor equality is required.
        let successor = model(step: { _, i in Transition(state: i % 10, outputs: [7]) })
        XCTAssertEqual(try explore(successor, reduction: byDecade()).reductionDecisions[1].rejected, [RejectedEquivalence(representative: 0, reason: "successor_differs")])
        // Same successor, a failing check on one edge only.
        let same = model(step: { _, i in Transition(state: i >= 10 ? 1 : i, outputs: [7]) }, failEdge: [11])
        let checks = try explore(same, reduction: byDecade())
        XCTAssertEqual(checks.reductionDecisions[1].rejected, [RejectedEquivalence(representative: 0, reason: "checks_differ")])
        XCTAssertEqual(checks.reductionDecisions[2].reducedTo, 0); XCTAssertEqual(checks.findingCount, 1)
        // Dispositions must match.
        let rejected = model(step: { _, i in Transition(state: i >= 10 ? 1 : i, outputs: [7], disposition: i == 11 ? .rejected("stale") : .accepted) })
        XCTAssertEqual(try explore(rejected, reduction: byDecade()).reductionDecisions[1].rejected.map(\.reason), ["disposition_differs"])
        XCTAssertTrue(try compareReduction(same, unreduced: try explore(same), reduced: checks).equivalent)
    }
    func testIncorrectReductionIsDetected() throws {
        // Negative control: a reduced report missing a state, an edge class, or a
        // finding must not compare equivalent to the unreduced search.
        let same = model(step: { _, i in Transition(state: i >= 10 ? 1 : i, outputs: [7]) }, failEdge: [12])
        let unreduced = try explore(same), reduced = try explore(same, reduction: byDecade())
        XCTAssertTrue(try compareReduction(same, unreduced: unreduced, reduced: reduced).equivalent)
        var missingState = reduced; missingState.nodes.removeLast(); missingState.edges[4].target = .rejected("tampered")
        let a = try compareReduction(same, unreduced: unreduced, reduced: missingState)
        XCTAssertFalse(a.equivalent); XCTAssertEqual(a.missingInReduced, [2]); XCTAssertEqual(a.unrepresentedEdges, [4])
        var lostFinding = reduced; lostFinding.findingCount = 0
        XCTAssertFalse(try compareReduction(same, unreduced: unreduced, reduced: lostFinding).findingsMatch)
        var wrongTarget = reduced; wrongTarget.edges[3].target = .duplicate(2)
        let c = try compareReduction(same, unreduced: unreduced, reduced: wrongTarget)
        // Unreduced 0->1 edges remain represented by edge 0; the 0->2 edge has no unreduced counterpart.
        XCTAssertFalse(c.equivalent); XCTAssertEqual(c.unrepresentedEdges, []); XCTAssertEqual(c.extraReducedEdges, [3])
        let stopped = try explore(same, policy: .stopAtFirst)
        XCTAssertThrowsError(try compareReduction(same, unreduced: stopped, reduced: reduced))
    }
    func testDiamondValidation() throws {
        // Two independent counters commute.
        let counters = Model<[Int], Int, Int>(initialState: { [0, 0] }, step: { s, i in var n = s; n[i] += 1; return Transition(state: n) }, checkState: { _ in [] },
                                              cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { s in InputIterator(values: [0, 1].filter { s[$0] < 2 }) },
                                              equalStates: ==, hashState: { UInt64($0[0] * 3 + $0[1]) })
        let report = try exploreGraph(counters)
        let ok = try validateDiamonds(counters, report: report, candidate: { _, _ in true }, equalInputs: ==, projection: { _, _ in [] })
        // Pairs are checked at (0,0) (1,0) (0,1) (1,1); one input is disabled at the other bound.
        XCTAssertEqual(ok.termination, "search_complete"); XCTAssertEqual(ok.validated, 4); XCTAssertTrue(ok.nonCommuting.isEmpty)
        // Doubling and incrementing do not commute.
        let arithmetic = Model<Int, Int, Int>(initialState: { 1 }, step: { s, i in Transition(state: i == 0 ? s * 2 : s + 1, outputs: [i]) }, checkState: { _ in [] },
                                              cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { s in InputIterator(values: s < 3 ? [0, 1] : []) },
                                              equalStates: ==, hashState: { UInt64($0) })
        let bad = try validateDiamonds(arithmetic, report: try exploreGraph(arithmetic, config: SearchConfig(maxDepth: 0)), candidate: { _, _ in true }, equalInputs: ==, projection: { _, _ in [] })
        // Depth-frontier nodes are not completely expanded and are not validated.
        XCTAssertEqual(bad.checkedNodes, 0); XCTAssertEqual(bad.validated, 0)
        let deep = try validateDiamonds(arithmetic, report: try exploreGraph(arithmetic), candidate: { _, _ in true }, equalInputs: ==, projection: { _, _ in [] })
        // At 1: 1*2+1=3 but (1+1)*2=4. At 2, doubling disables both orders.
        XCTAssertEqual(deep.validated, 1); XCTAssertEqual(deep.nonCommuting.count, 1)
        XCTAssertEqual(deep.nonCommuting.first.map { [$0.node, $0.first, $0.second] }, [0, 0, 1]); XCTAssertEqual(deep.nonCommuting.first?.reason, "state_differs")
        // Same final state, different observations.
        let observed = try validateDiamonds(counters, report: report, candidate: { _, _ in true }, equalInputs: ==, projection: { i, t in [UInt8(i), UInt8(t.state[0])] })
        XCTAssertEqual(observed.nonCommuting.map(\.reason), ["observations_differ", "observations_differ", "observations_differ", "observations_differ"])
        var cancelled = true
        XCTAssertEqual(try validateDiamonds(counters, report: report, candidate: { _, _ in true }, equalInputs: ==, projection: { _, _ in [] }, limits: RunLimits(isCancelled: { cancelled })).termination, "cancelled")
        cancelled = false
    }
}

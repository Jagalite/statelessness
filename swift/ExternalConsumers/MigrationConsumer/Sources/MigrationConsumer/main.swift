import Foundation
import Statelessness

// Exercises graph-search-v1, progress-analysis-v1, reduction-v1 and
// continued-failure-evidence-v1 through public API only. Exits nonzero on the
// first mismatch so qualification records an executed failure.
func bytes(_ v: Int) -> [UInt8] { withUnsafeBytes(of: Int64(v).littleEndian, Array.init) }
func int(_ b: [UInt8]) throws -> Int {
    guard b.count == 8 else { throw ModelError("bad integer") }
    return Int(b.enumerated().reduce(Int64(0)) { $0 | Int64($1.element) << (8 * Int64($1.offset)) })
}
func checks() throws -> [String] {
    var failures: [String] = []
    // 0 unarmed, 1 armed (transition warning), 2 fired (state failure).
    let trigger = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: i == 1 && s == 0 ? 1 : i == 2 && s == 1 ? 2 : s, outputs: [i]) },
                                       checkState: { [$0 == 2 ? .failed("fired", "boom") : .passed("fired")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
                                       checkTransition: { s, i, _ in i == 1 && s == 0 ? [.failed("armed_warning", "armed")] : [] },
                                       inputs: { s in InputIterator(values: s == 0 ? [0, 1] : s == 1 ? [0, 2] : []) }, equalStates: ==, hashState: { UInt64($0) },
                                       codec: Codec(metadata: { Metadata(name: "trigger") }, encodeState: bytes, decodeState: int, encodeInput: bytes, decodeInput: int, encodeOutput: bytes))
    func expect(_ condition: @autoclosure () throws -> Bool, _ label: String) {
        do { if try !condition() { failures.append(label) } } catch { failures.append("\(label): \(error)") }
    }
    expect(swiftExtensionProfiles == ["graph-search-v1", "progress-analysis-v1", "reduction-v1", "continued-failure-evidence-v1"], "declared profiles")
    expect(!conformanceProfiles.contains("graph-search-v1"), "extensions are not portable profiles")

    // S1/S2
    let stop = try exploreGraph(trigger)
    expect(stop.termination == "failure_found" && stop.coverage == .incomplete("failure_found") && stop.failure?.inputs == [1], "stop at first")
    var events = 0
    let report = try exploreGraph(trigger, options: GraphSearchOptions(failurePolicy: .collect(maxRetainedFindings: 8, expandFailingSuccessors: true), events: { _ in events += 1 }))
    expect(report.coverage == .exhaustive && report.nodes.count == 3 && report.edges.count == 4 && report.graphEdges == 4, "admitted graph")
    expect(report.findings.map(\.witness) == [[1], [1, 2]] && report.findings.map(\.requiresContinuedEvidence) == [false, true], "collected findings")
    expect(report.failedProperties.map(\.id) == ["armed_warning", "fired"] && events > 0, "property identities and events")
    let bounded = try exploreGraph(trigger, config: SearchConfig(maxDepth: 1))
    expect(bounded.coverage == .bounded(depthCutoff: true, failurePruned: false) || bounded.coverage == .incomplete("failure_found"), "bounded coverage")

    // S3
    let progress = try analyzeProgress(try ProgressGraph(report: report, fair: { $0.input != 0 }), kind: { report.nodes[$0].state == 2 ? .terminal : .transaction })
    expect(progress.classes == [.transactionProgress, .transactionProgress, .terminal] && progress.settledTransactionStates == 2 && progress.graphComplete, "progress")
    expect(stronglyConnectedComponents(nodeCount: 3, edges: [(0, 1), (1, 0), (1, 2)]) == [[0, 1], [2]], "scc")

    // S4: input 0 waits in place; group both waits from node 1 with a second wait input 3.
    let waits = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: i == 1 ? 1 : s, outputs: []) }, checkState: { _ in [] },
                                     cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { s in InputIterator(values: s == 0 ? [0, 3, 1] : []) },
                                     equalStates: ==, hashState: { UInt64($0) })
    let reduction = EquivalenceReduction<Int, Int, Int>(eligibleClass: { _, i in i == 0 || i == 3 ? "wait" : nil }, projection: { _, _, _ in [] })
    let unreduced = try exploreGraph(waits), reduced = try exploreGraph(waits, options: GraphSearchOptions(reduction: reduction))
    expect(reduced.reducedEdges == 1 && reduced.edges[1].target == .reduced(0) && reduced.transitions == unreduced.transitions, "reduction")
    expect(try compareReduction(waits, unreduced: unreduced, reduced: reduced).equivalent, "reduction comparison")
    let diamonds = try validateDiamonds(trigger, report: report, candidate: { _, _ in true }, equalInputs: ==, projection: { _, _ in [] })
    expect(diamonds.termination == "search_complete", "diamond validation")

    // S5
    let generate = Generate(model: trigger, generate: { _, _ in nil }, isEnabled: { s, i in i != 2 || s == 1 }, equalInputs: ==)
    let shrunk = try shrinkContinued(generate, inputs: [0, 1, 0, 2], target: report.findings[1].violations[0])
    expect(shrunk.base.minimized.inputs == [1, 2] && shrunk.minimizedFindings.map(\.sequence) == [1, 2], "continued shrink")
    let trace = try recordContinued(trigger, inputs: InputIterator(values: [0, 1, 0, 2]))
    expect(trace.failingSequences == [2, 4], "continued record")
    let restored = try parseContinuedTrace(try encodeContinuedTrace(trace))
    expect(try restored == trace && replayContinued(trigger, trace: restored).completeReproduction, "continued replay")
    expect((try? validateRecording(trace.trace)) == nil, "old reader rejects continued trace")

    return failures
}
let failures = try checks()
if failures.isEmpty { print("migration-consumer: ok \(swiftExtensionProfiles.joined(separator: ","))") }
else { print("migration-consumer: FAILED \(failures.joined(separator: "; "))"); exit(1) }

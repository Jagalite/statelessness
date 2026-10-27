import Foundation
import XCTest
import Statelessness

private final class CaseLog { var cases: [[Int]] = [] }
private func searchFixture(_ name: String, log: CaseLog = CaseLog(), cap: Int = 100) throws -> Generate<Int, Int, Int> {
    let m = Model<Int, Int, Int>(initialState: { log.cases.append([]); return 0 }, step: { s, i in
        log.cases[log.cases.count - 1].append(i)
        return Transition(state: ["causal", "phase", "cycle"].contains(name) ? i : s + i)
    }, checkState: { s in
        let fails = name == "initial" || (name == "threshold" && s >= 3) || (name == "causal" && s == 2) || (name == "phase" && s == 1) || (name == "cycle" && s != 0)
        return [Check(name == "phase" ? "same" : "bad", fails ? .failed : .passed, fails ? "fixture violation" : "")]
    }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, checkTransition: { _, _, t in
        name == "phase" && t.state == 2 ? [.failed("same", "transition violation")] : []
    }, inputs: { s in
        let domain: [Int]
        if name == "empty" { domain = [] }
        else if ["binary", "phase", "cycle"].contains(name) { domain = [1,2] }
        else if name == "digits" { domain = Array(0...9) }
        else if name == "causal" { domain = s == 0 ? [1] : (s == 1 ? [2] : []) }
        else { domain = [1] }
        return InputIterator(values: domain)
    })
    var g = try Auto(m, options: AutoOptions(maxCandidates: cap), equalInputs: { $0 == $1 }).generated
    if name == "digits" { g.generate = { _, rng in Int(rng.nextU64() % 10) } }
    g.simplerInputs = { i in
        InputIterator(values: name == "phase" && i == 1 ? [2] : (name == "cycle" ? [i == 1 ? 2 : 1] : []))
    }
    return g
}
private func searchRoot() -> URL {
    URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
}
private func jsonInts(_ value: String) throws -> [Int] { try JSONSerialization.jsonObject(with: Data(value.utf8)) as! [Int] }
private func historyJSON(_ h: [ShrinkAttempt]) -> [[String: Any]] {
    h.map { a in
        var op: [String: Any]
        switch a.operation {
        case .validateOriginal: op = ["kind": "validate_original"]
        case let .remove(start, end): op = ["kind": "remove", "start": start, "end": end]
        case let .simplify(index, alternative): op = ["kind": "simplify", "index": index, "alternative": alternative]
        }
        return ["attempt": a.attempt, "candidate_length": a.candidateLength, "operation": op, "outcome": a.outcome.rawValue]
    }
}
final class SearchTests: XCTestCase {
    func testSharedSearchCorpusAndShrinkHistory() throws {
        let fixture = ProcessInfo.processInfo.environment["STATELESS_SEARCH_CORPUS"].map { URL(fileURLWithPath: $0) } ?? searchRoot().appendingPathComponent("conformance/search-v1.tsv")
        let historyURL = ProcessInfo.processInfo.environment["STATELESS_SEARCH_HISTORY"].map { URL(fileURLWithPath: $0) } ?? fixture.deletingLastPathComponent().appendingPathComponent("search-v1-history.json")
        let expectedHistory = try JSONSerialization.jsonObject(with: Data(contentsOf: historyURL)) as! [String: [[String: Any]]]
        let text = try String(contentsOf: fixture, encoding: .utf8)
        for row in text.split(separator: "\n").dropFirst() {
            let c = row.split(separator: "\t").map(String.init), log = CaseLog()
            let limits = RunLimits(maxDuration: c[14] == "deadline" ? 0 : nil, isCancelled: { c[14] == "cancelled" })
            if c[1] == "fuzz", c[24] != "-" {
                XCTAssertThrowsError(try fuzz(searchFixture(c[2], log: log, cap: Int(c[8])!), config: FuzzConfig(seed: UInt64(c[3])!, cases: Int(c[4])!, maxSteps: Int(c[5])!, maxTransitions: UInt64(c[6])!, mutationPercent: Int(c[7])!), limits: limits), c[0])
                continue
            }
            let g = try searchFixture(c[2], log: log, cap: Int(c[8]) ?? 100)
            if c[1] == "fuzz" {
                let r = try fuzz(g, config: FuzzConfig(seed: UInt64(c[3])!, cases: Int(c[4])!, maxSteps: Int(c[5])!, maxTransitions: UInt64(c[6])!, mutationPercent: Int(c[7])!), limits: limits)
                XCTAssertEqual(r.termination, c[15], c[0]); XCTAssertEqual(r.seed, UInt64(c[3])!, c[0]); XCTAssertEqual(r.cases, Int(c[16])!, c[0]); XCTAssertEqual(r.transitions, UInt64(c[17])!, c[0]); XCTAssertEqual(r.skippedChecks, 0, c[0])
                let expectedTrace = try JSONSerialization.jsonObject(with: Data(c[23].utf8)) as! [[Int]]
                XCTAssertEqual(log.cases, expectedTrace, c[0])
                if c[20] == "-" { XCTAssertNil(r.failure, c[0]) }
                else {
                    XCTAssertEqual(r.failure?.inputs, try jsonInts(c[20]), c[0]); XCTAssertEqual(r.failure?.violations.map { $0.phase }, [c[21]], c[0]); XCTAssertEqual(r.failure?.violations.map { $0.check.id }, [c[22]], c[0])
                }
            } else {
                let original = Failure(inputs: try jsonInts(c[9]), violations: [Violation(phase: c[10], check: .failed(c[11], "original"))])
                if c[24] != "-" {
                    XCTAssertThrowsError(try shrink(g, original: original, config: ShrinkConfig(maxAttempts: Int(c[12])!), limits: ShrinkLimits(control: limits, maxReplayedTransitions: UInt64(c[13]))), c[0])
                    continue
                }
                let r = try shrink(g, original: original, config: ShrinkConfig(maxAttempts: Int(c[12])!), limits: ShrinkLimits(control: limits, maxReplayedTransitions: UInt64(c[13])))
                XCTAssertEqual(r.termination, c[15], c[0]); XCTAssertEqual(r.replayedTransitions, UInt64(c[17])!, c[0]); XCTAssertEqual(r.attempts, Int(c[18])!, c[0]); XCTAssertEqual(r.validatedOriginal, c[19] == "true", c[0]); XCTAssertEqual(r.original.inputs, original.inputs, c[0]); XCTAssertEqual(r.minimized.inputs, try jsonInts(c[20]), c[0]); XCTAssertEqual(r.minimized.violations.map { $0.phase }, [c[21]], c[0]); XCTAssertEqual(r.minimized.violations.map { $0.check.id }, [c[22]], c[0])
                XCTAssertTrue(NSDictionary(dictionary: ["history": historyJSON(r.history)]).isEqual(to: ["history": try XCTUnwrap(expectedHistory[c[0]])]), c[0])
            }
        }
    }
    func testReservoirRngCommitRollbackAndOneExtraProbe() throws {
        var m = try searchFixture("binary").model, pulls = 0
        m.inputs = { _ in InputIterator { pulls += 1; return pulls <= 3 ? pulls : nil } }
        var rng = Rng(seed: UInt64.max), baseline = rng
        XCTAssertThrowsError(try sampleEnumerated(m, state: 0, rng: &rng, options: AutoOptions(maxCandidates: 2)))
        XCTAssertEqual(pulls, 3); XCTAssertEqual(rng.nextU64(), baseline.nextU64())
        pulls = 0
        XCTAssertTrue(try containsEnumerated(m, state: 0, input: 1, equalInputs: { $0 == $1 }, options: AutoOptions(maxCandidates: 1)))
        XCTAssertEqual(pulls, 1)
        pulls = 0
        XCTAssertThrowsError(try containsEnumerated(m, state: 0, input: 9, equalInputs: { $0 == $1 }, options: AutoOptions(maxCandidates: 2)))
        XCTAssertEqual(pulls, 3)
        m.inputs = { _ in InputIterator(values: [9]) }; rng = Rng(seed: 1); baseline = rng
        XCTAssertEqual(try sampleEnumerated(m, state: 0, rng: &rng), 9); XCTAssertEqual(rng.nextU64(), baseline.nextU64())
        m.inputs = { _ in InputIterator(values: []) }; rng = Rng(seed: 2); baseline = rng
        XCTAssertNil(try sampleEnumerated(m, state: 0, rng: &rng)); XCTAssertEqual(rng.nextU64(), baseline.nextU64())
        pulls = 0; rng = Rng(seed: 3); baseline = rng
        m.inputs = { _ in InputIterator { pulls += 1; if pulls == 3 { throw ModelError("iterator failed") }; return pulls } }
        XCTAssertThrowsError(try sampleEnumerated(m, state: 0, rng: &rng)); XCTAssertEqual(pulls, 3); XCTAssertEqual(rng.nextU64(), baseline.nextU64())
        XCTAssertThrowsError(try Auto(m, options: AutoOptions(maxCandidates: 0), equalInputs: { $0 == $1 }))
    }
    func testNestedOptionalInputIsPresent() throws {
        var calls = 0
        let m = Model<Int, Int?, Int>(initialState: { 0 }, step: { s, input in calls += 1; XCTAssertNil(input); return Transition(state: s + 1) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 })
        let g = Generate(model: m, generate: { _, _ in .some(nil) }, equalInputs: { $0 == $1 })
        XCTAssertEqual(try fuzz(g, config: FuzzConfig(cases: 1, maxSteps: 2)).transitions, 2); XCTAssertEqual(calls, 2)
    }
    func testDisabledGeneratedAndThrowingHooksAreErrors() throws {
        var g = try searchFixture("singleton")
        g.isEnabled = { _, _ in false }
        XCTAssertThrowsError(try fuzz(g, config: FuzzConfig(cases: 1)))
        g = try searchFixture("singleton"); g.generate = { _, _ in throw ModelError("generation failed") }
        XCTAssertThrowsError(try fuzz(g, config: FuzzConfig(cases: 1)))
        g = try searchFixture("cycle"); g.simplerInputs = { _ in InputIterator { throw ModelError("simplification failed") } }
        XCTAssertThrowsError(try shrink(g, original: Failure(inputs: [2], violations: [Violation(phase: "state", check: .failed("bad", ""))])))
        XCTAssertThrowsError(try fuzz(g, config: FuzzConfig(mutationPercent: 101)))
        let exactSeed = try fuzz(searchFixture("singleton"), config: FuzzConfig(seed: UInt64.max, cases: 1, maxSteps: 1, maxTransitions: UInt64.max))
        XCTAssertEqual(exactSeed.seed, UInt64.max); XCTAssertEqual(exactSeed.transitions, 1)
        XCTAssertThrowsError(try fuzz(g, limits: RunLimits(maxDuration: .nan)))
    }
    func testControlAndFailurePriority() throws {
        var cancelled = false, steps = 0
        var g = try searchFixture("singleton")
        g.generate = { _, _ in cancelled = true; return 1 }
        g.model.step = { _, _ in steps += 1; return Transition(state: 1) }
        XCTAssertEqual(try fuzz(g, limits: RunLimits(isCancelled: { cancelled })).termination, "cancelled"); XCTAssertEqual(steps, 0)
        cancelled = false; g.generate = { _, _ in cancelled = true; throw ModelError("error wins") }
        XCTAssertThrowsError(try fuzz(g, limits: RunLimits(isCancelled: { cancelled })))
        cancelled = false; g.generate = { _, _ in 1 }; g.model.step = { _, _ in cancelled = true; return Transition(state: 3) }; g.model.checkState = { s in s == 3 ? [.failed("bad", "failure wins")] : [] }
        let r = try fuzz(g, limits: RunLimits(isCancelled: { cancelled }))
        XCTAssertEqual(r.termination, "failure_found"); XCTAssertEqual(r.transitions, 1)
        let priority = try fuzz(g, limits: RunLimits(maxDuration: 0, isCancelled: { true }))
        XCTAssertEqual(priority.termination, "cancelled"); XCTAssertEqual(priority.cases, 0)
    }
    func testShrinkRejectsInvalidOriginalAndPreservesSuppliedEvidenceOnBudgetStop() throws {
        let g = try searchFixture("threshold"), target = Violation(phase: "state", check: .failed("bad", ""))
        XCTAssertThrowsError(try shrink(g, original: Failure(inputs: [1], violations: [target])))
        XCTAssertThrowsError(try shrink(g, original: Failure(inputs: [1,1,1], violations: [Violation(phase: "transition", check: .failed("bad", ""))])))
        XCTAssertThrowsError(try shrink(g, original: Failure(inputs: [1,1,1], violations: [])))
        let r = try shrink(g, original: Failure(inputs: [1,1,1], violations: [target]), limits: ShrinkLimits(maxReplayedTransitions: 2))
        XCTAssertFalse(r.validatedOriginal); XCTAssertEqual(r.minimized.inputs, [1,1,1]); XCTAssertEqual(r.replayedTransitions, 2); XCTAssertEqual(r.history.last?.outcome, .resourceLimit)
        let during = try shrink(g, original: Failure(inputs: [1,1,1], violations: [target]), limits: ShrinkLimits(maxReplayedTransitions: 4))
        XCTAssertTrue(during.validatedOriginal); XCTAssertEqual(during.termination, "transition_limit"); XCTAssertEqual(during.replayedTransitions, 4); XCTAssertEqual(during.history.last?.outcome, .resourceLimit)
    }
    func testExactMutationRngAndDisabledReuseFallback() throws {
        let log = CaseLog()
        var g = try searchFixture("digits", log: log), draws: [Int] = []
        g.generate = { _, rng in let input = Int(rng.nextU64() % 10); draws.append(input); return input }
        g.isEnabled = { _, input in !(log.cases.count > 1 && input == 5) }
        let r = try fuzz(g, config: FuzzConfig(seed: 0, cases: 3, maxSteps: 3, mutationPercent: 100))
        XCTAssertEqual(r.termination, "cases_completed")
        XCTAssertEqual(log.cases, [[5,0,9], [0,9,9], [1,9,7]])
        XCTAssertEqual(draws, [5,0,9,0,9,1,7])
    }
    func testObserverGetsFailingPrefixAndCannotMutateExecution() throws {
        final class Box { var value: Int; init(_ value: Int) { self.value = value } }
        let m = Model<Box, Box, Box>(initialState: { Box(0) }, step: { s, i in Transition(state: Box(s.value + i.value), outputs: [Box(i.value)]) }, checkState: { s in s.value >= 2 ? [.failed("bad", "two")] : [.passed("bad")] }, cloneState: { Box($0.value) }, cloneInput: { Box($0.value) }, cloneOutput: { Box($0.value) })
        let g = Generate(model: m, generate: { _, _ in Box(1) }, equalInputs: { $0.value == $1.value })
        var initialCalls = 0, beforeValues: [Int] = [], prefixes: [[Int]] = [], failed: [Bool] = []
        let observer = SearchObserver<Box, Box, Box>(initial: { s, _ in initialCalls += 1; s.value = 999 }, transition: { before, input, t, stateChecks, _, history in
            beforeValues.append(before.value); prefixes.append(history.map { $0.value }); failed.append(stateChecks.contains { $0.status == .failed })
            before.value = 999; input.value = 999; t.state.value = 999; t.outputs[0].value = 999
            for i in history { i.value = 999 }
        })
        let r = try fuzz(g, config: FuzzConfig(cases: 1, maxSteps: 4), observer: observer)
        XCTAssertEqual(initialCalls, 1); XCTAssertEqual(beforeValues, [0,1]); XCTAssertEqual(prefixes, [[1],[1,1]]); XCTAssertEqual(failed, [false,true])
        XCTAssertEqual(r.termination, "failure_found"); XCTAssertEqual(r.failure?.inputs.map { $0.value }, [1,1])
    }
    func testObserverCheckErrorBatchesAndObserverErrors() throws {
        var events: [String] = []
        let observer = SearchObserver<Int, Int, Int>(checkError: { phase, checks, complete in events.append("\(phase):\(checks.count):\(complete)") })
        var g = try searchFixture("singleton")
        g.model.checkState = { _ in throw ModelError("initial checker") }
        XCTAssertThrowsError(try fuzz(g, observer: observer)); XCTAssertEqual(events, ["initial_state:0:false"])
        events = []; g = try searchFixture("singleton")
        g.model.checkState = { s in if s > 0 { throw ModelError("successor checker") }; return [] }
        XCTAssertThrowsError(try fuzz(g, observer: observer)); XCTAssertEqual(events, ["state:0:false"])
        events = []; g = try searchFixture("singleton")
        g.model.checkTransition = { _, _, _ in throw ModelError("edge checker") }
        XCTAssertThrowsError(try fuzz(g, observer: observer)); XCTAssertEqual(events, ["state:1:true", "transition:0:false"])
        let throwing = SearchObserver<Int, Int, Int>(initial: { _, _ in throw ModelError("observer failed") })
        XCTAssertThrowsError(try fuzz(searchFixture("singleton"), observer: throwing))
        events = []
        let secondary = SearchObserver<Int, Int, Int>(checkError: { phase, checks, complete in
            events.append("\(phase):\(checks.count):\(complete)")
            if phase == "state" { throw ModelError("secondary observer") }
        })
        do { _ = try fuzz(g, observer: secondary); XCTFail("expected checker error") }
        catch {
            let message = String(describing: error)
            XCTAssertTrue(message.contains("edge checker")); XCTAssertTrue(message.contains("secondary observer"))
        }
        XCTAssertEqual(events, ["state:1:true", "transition:0:false"])
    }
    func testLazySimplificationCancellationAndErrorPriority() throws {
        var g = try searchFixture("cycle"), cancelled = false, pulls = 0
        let original = Failure(inputs: [2], violations: [Violation(phase: "state", check: .failed("bad", ""))])
        g.simplerInputs = { _ in InputIterator { pulls += 1; cancelled = true; return 1 } }
        let r = try shrink(g, original: original, limits: ShrinkLimits(control: RunLimits(isCancelled: { cancelled })))
        XCTAssertEqual(r.termination, "cancelled"); XCTAssertEqual(r.attempts, 2); XCTAssertEqual(pulls, 1)
        cancelled = false
        g.simplerInputs = { _ in InputIterator { cancelled = true; throw ModelError("pull failed") } }
        XCTAssertThrowsError(try shrink(g, original: original, limits: ShrinkLimits(control: RunLimits(isCancelled: { cancelled }))))
    }

    func testGeneratedPairedFuzzShrinkRecordReplay() throws {
        let codec = Codec<Int, Int, Int>(metadata: { Metadata(name: "counter") }, encodeState: { [UInt8($0)] }, decodeState: { bytes in guard bytes.count == 1 else { throw ModelError("bad state") }; return Int(bytes[0]) }, encodeInput: { [UInt8($0)] }, decodeInput: { bytes in guard bytes.count == 1 else { throw ModelError("bad input") }; return Int(bytes[0]) }, encodeOutput: { [UInt8($0)] })
        let actual = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: s + i + (s >= 1 ? 1 : 0), outputs: [i]) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, equalStates: { $0 == $1 }, codec: codec)
        var reference = actual
        reference.step = { s, i in Transition(state: s + i, outputs: [i]) }
        let projection = Projection<Int, Int, Int, Int, Int, Int>(metadata: { Metadata(name: "projection") }, actualState: { $0 }, referenceState: { $0 }, actualEffect: { $0 }, referenceEffect: { $0 }, equalState: { $0 == $1 }, equalEffect: { $0 == $1 })
        var generationStates: [Int] = []
        let base = Generate(model: actual, generate: { state, _ in generationStates.append(state); return 1 }, isEnabled: { _, input in input == 1 }, equalInputs: { $0 == $1 })
        let g = base.withOracle(Paired(reference: reference, projection: projection).oracle)
        let f = try fuzz(g, config: FuzzConfig(cases: 1, maxSteps: 10))
        XCTAssertEqual(generationStates, [0,1]); XCTAssertEqual(f.termination, "failure_found"); XCTAssertEqual(f.failure?.violations.first?.check.id, "pair.state")
        let minimized = try shrink(g, original: XCTUnwrap(f.failure))
        XCTAssertTrue(minimized.validatedOriginal); XCTAssertEqual(minimized.minimized.inputs, [1,1])
        let trace = try Statelessness.record(g.model, inputs: InputIterator(values: minimized.minimized.inputs))
        XCTAssertEqual(trace.termination, .propertyFailed)
        XCTAssertEqual(try replay(g.model, trace: parseTrace(encodeTrace(trace))).outcome, "exact")
    }

}

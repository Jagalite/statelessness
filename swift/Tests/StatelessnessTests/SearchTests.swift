import Foundation
import XCTest
import Statelessness

private final class DomainInputBox { var value: Int; init(_ value: Int) { self.value = value } }
private final class ShrinkStateBox { var value: Int; init(_ value: Int) { self.value = value } }
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
    func testEnumerationDefersUnusedInitialSnapshotFailure() throws {
        var copies = 0
        let model = Model<Int, Int, Int>(
            initialState: { 0 }, step: { state, _ in Transition(state: state) },
            checkState: { _ in [.failed("initial", "failure")] },
            cloneState: { state in copies += 1; if copies == 2 { throw ModelError("snapshot") }; return state },
            cloneInput: { $0 }, cloneOutput: { $0 },
            inputs: { _ in InputIterator(values: []) }, equalStates: { $0 == $1 })
        let report = try enumerateStates(model)
        XCTAssertEqual(report.termination, "failure_found")
        XCTAssertEqual(report.failure?.violations.first?.check.id, "initial")
    }
    func testEnumerationDefersUnusedSuccessorSnapshotFailure() throws {
        var raw: DomainInputBox?, transitionState: DomainInputBox?, failed = false
        let model = Model<DomainInputBox, Int, Int>(
            initialState: { DomainInputBox(0) },
            step: { _, _ in let next = DomainInputBox(1); raw = next; return Transition(state: next) },
            checkState: { state in state.value == 1 ? [.failed("edge", "failure")] : [] },
            cloneState: { state in
                if state === transitionState && !failed { failed = true; throw ModelError("snapshot") }
                let result = DomainInputBox(state.value)
                if state === raw { transitionState = result }
                return result
            }, cloneInput: { $0 }, cloneOutput: { $0 },
            inputs: { state in InputIterator(values: state.value == 0 ? [1] : []) },
            equalStates: { $0.value == $1.value })
        let report = try enumerateStates(model)
        XCTAssertTrue(failed)
        XCTAssertEqual(report.termination, "failure_found")
        XCTAssertEqual(report.failure?.violations.first?.check.id, "edge")
    }
    func testEnumerationCancellationPrecedesUnusedSnapshotFailure() throws {
        var copies = 0, cancelled = false
        let model = Model<Int, Int, Int>(
            initialState: { 0 }, step: { state, _ in Transition(state: state) },
            checkState: { _ in cancelled = true; return [] },
            cloneState: { state in copies += 1; if copies == 2 { throw ModelError("snapshot") }; return state },
            cloneInput: { $0 }, cloneOutput: { $0 },
            inputs: { _ in InputIterator(values: []) }, equalStates: { $0 == $1 })
        let report = try enumerateStates(model, limits: SearchLimits(control: RunLimits(isCancelled: { cancelled })))
        XCTAssertEqual(report.termination, "cancelled")
    }
    func testEnumerationRetainsInitialAcrossCheckAliasMutation() throws {
        let raw = DomainInputBox(0)
        var initialAlias: DomainInputBox?
        let model = Model<DomainInputBox, Int, Int>(
            initialState: { raw }, step: { _, input in Transition(state: DomainInputBox(input)) },
            checkState: { state in if state.value == 0 { initialAlias?.value = 99 }; return [] },
            cloneState: { state in
                let result = DomainInputBox(state.value)
                if state === raw { initialAlias = result }
                return result
            }, cloneInput: { $0 }, cloneOutput: { $0 },
            inputs: { state in InputIterator(values: state.value == 0 ? [1] : []) },
            equalStates: { $0.value == $1.value })
        let report = try enumerateStates(model)
        XCTAssertEqual(report.states, 2)
        XCTAssertEqual(report.transitions, 1)
    }
    func testEnumerationRetainsSuccessorAcrossCheckAliasMutation() throws {
        var raw: DomainInputBox?, returned: DomainInputBox?
        let model = Model<DomainInputBox, Int, Int>(
            initialState: { DomainInputBox(0) },
            step: { _, input in let next = DomainInputBox(input); raw = next; return Transition(state: next) },
            checkState: { state in
                if state.value == 1 { returned?.value = 99 }
                return []
            },
            cloneState: { state in
                let result = DomainInputBox(state.value)
                if state === raw { returned = result }
                return result
            }, cloneInput: { $0 }, cloneOutput: { $0 },
            inputs: { state in InputIterator(values: state.value == 0 ? [1] : (state.value == 1 ? [2] : [])) },
            equalStates: { $0.value == $1.value })
        let report = try enumerateStates(model)
        XCTAssertEqual(report.termination, "graph_exhausted")
        XCTAssertEqual(report.states, 3)
        XCTAssertEqual(report.transitions, 2)
    }
    func testEnumerationRetainsInputAcrossReducerAliasMutation() throws {
        let domainInput = DomainInputBox(7)
        let model = Model<Int, DomainInputBox, Int>(
            initialState: { 0 },
            step: { _, _ in domainInput.value = 99; return Transition(state: 1) },
            checkState: { _ in [] }, cloneState: { $0 },
            cloneInput: { DomainInputBox($0.value) }, cloneOutput: { $0 },
            checkTransition: { _, input, _ in
                input.value == 7 ? [.failed("input", "original input observed")] : []
            }, inputs: { _ in InputIterator(values: [domainInput]) },
            equalStates: { $0 == $1 })
        let report = try enumerateStates(model)
        XCTAssertEqual(report.termination, "failure_found")
        XCTAssertEqual(report.failure?.inputs.first?.value, 7)
        XCTAssertEqual(domainInput.value, 99)
    }
    func testEnumerationInputCopiesPrecedeReducer() throws {
        var copies = 0, steps = 0
        let model = Model<Int, Int, Int>(
            initialState: { 0 },
            step: { state, _ in steps += 1; return Transition(state: state) },
            checkState: { _ in [] }, cloneState: { $0 },
            cloneInput: { value in
                copies += 1
                if copies == 2 { throw ModelError("second input copy") }
                return value
            }, cloneOutput: { $0 }, inputs: { _ in InputIterator(values: [7]) },
            equalStates: { $0 == $1 })
        XCTAssertThrowsError(try enumerateStates(model))
        XCTAssertEqual(copies, 2)
        XCTAssertEqual(steps, 0)
    }
    func testContainsCapturesQueriedInputBeforeDomainCallbacks() throws {
        let sought = DomainInputBox(7)
        let model = Model<Int, DomainInputBox, Int>(
            initialState: { 0 }, step: { state, _ in Transition(state: state) },
            checkState: { _ in [] }, cloneState: { value in sought.value = 99; return value },
            cloneInput: { DomainInputBox($0.value) }, cloneOutput: { $0 },
            inputs: { _ in InputIterator(values: [DomainInputBox(7)]) })
        XCTAssertTrue(try containsEnumerated64(model, state: 0, input: sought,
            equalInputs: { $0.value == $1.value }))
        XCTAssertEqual(sought.value, 99)
        var empty = model
        empty.cloneInput = { _ in throw ModelError("copy failed") }
        empty.inputs = { _ in InputIterator(values: []) }
        XCTAssertFalse(try containsEnumerated64(empty, state: 0, input: sought,
            equalInputs: { $0.value == $1.value }))
        empty.inputs = { _ in InputIterator(values: [DomainInputBox(7)]) }
        XCTAssertThrowsError(try containsEnumerated64(empty, state: 0, input: sought,
            equalInputs: { $0.value == $1.value }))
    }
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
    func testShrinkCapturesFutureInputsBeforeInitialCallback() throws {
        var saved: [DomainInputBox] = [], candidates: [DomainInputBox] = [], copies = 0, observed: [Int] = []
        let m = Model<Int, DomainInputBox, Int>(initialState: {
            saved[1].value = 99
            return 0
        }, step: { state, input in
            observed.append(input.value)
            return Transition(state: state + input.value)
        }, checkState: { state in state == 3 ? [.failed("bad", "target")] : [] },
        cloneState: { $0 }, cloneInput: { input in
            let value = DomainInputBox(input.value)
            copies += 1
            if saved.count < 2 { saved.append(value) }
            else if copies <= 4 { candidates.append(value) }
            return value
        }, cloneOutput: { $0 })
        let g = Generate(model: m, generate: { _, _ in nil }, isEnabled: { _, _ in
            candidates[0].value = 88
            return true
        }, equalInputs: { $0.value == $1.value })
        let original = Failure(inputs: [DomainInputBox(1), DomainInputBox(2)], violations: [Violation(phase: "state", check: .failed("bad", "target"))])
        let result = try shrink64(g, original: original, config: ShrinkConfig64(maxAttempts: 1))
        XCTAssertTrue(result.validatedOriginal)
        XCTAssertEqual(observed, [1, 2])
        XCTAssertEqual(result.minimized.inputs.map(\.value), [1, 2])
    }
    func testShrinkRetainsContinuationBeforeChecks() throws {
        let initial = ShrinkStateBox(0), successor = ShrinkStateBox(1)
        var liveInitial: ShrinkStateBox?, liveSuccessor: ShrinkStateBox?
        var steps: [Int] = []
        let model = Model<ShrinkStateBox, Int, Int>(initialState: { initial }, step: { state, _ in
            steps.append(state.value)
            return Transition(state: state.value == 0 ? successor : ShrinkStateBox(state.value + 1))
        }, checkState: { state in
            if state.value == 0 { liveInitial?.value = 90 }
            if state.value == 1 { liveSuccessor?.value = 91 }
            return state.value == 2 ? [.failed("bad", "target")] : []
        }, cloneState: { state in
            let copy = ShrinkStateBox(state.value)
            if state === initial { liveInitial = copy }
            if state === successor { liveSuccessor = copy }
            return copy
        }, cloneInput: { $0 }, cloneOutput: { $0 })
        let generated = Generate(model: model, generate: { _, _ in nil }, isEnabled: { _, _ in true }, equalInputs: { $0 == $1 })
        let original = Failure(inputs: [1, 1], violations: [Violation(phase: "state", check: .failed("bad", "target"))])
        let result = try shrink64(generated, original: original, config: ShrinkConfig64(maxAttempts: 1))
        XCTAssertTrue(result.validatedOriginal)
        XCTAssertEqual(steps, [0, 1])
    }
    func testShrinkRetainsStateCheckBeforeContinuationCopyHook() throws {
        let successor = ShrinkStateBox(1)
        var liveSuccessor: ShrinkStateBox?
        let model = Model<ShrinkStateBox, Int, Int>(initialState: { ShrinkStateBox(0) }, step: { _, _ in
            Transition(state: successor)
        }, checkState: { state in
            state.value == 1 ? [.failed("bad", "target")] : []
        }, cloneState: { state in
            let copy = ShrinkStateBox(state.value)
            if state === successor { liveSuccessor = copy }
            else if state === liveSuccessor { state.value = 99 }
            return copy
        }, cloneInput: { $0 }, cloneOutput: { $0 })
        let generated = Generate(model: model, generate: { _, _ in nil }, isEnabled: { _, _ in true }, equalInputs: { $0 == $1 })
        let original = Failure(inputs: [1], violations: [Violation(phase: "state", check: .failed("bad", "target"))])
        let result = try shrink64(generated, original: original, config: ShrinkConfig64(maxAttempts: 1))
        XCTAssertTrue(result.validatedOriginal)
    }
    func testShrinkBoundsRepeatedAlreadySeenSimplifications() throws {
        var g = try searchFixture("cycle"), pulls = 0
        g.simplerInputs = { input in InputIterator { pulls += 1; if pulls > 3 { throw ModelError("hint limit was not enforced") }; return input } }
        let original = Failure(inputs:[1],violations:[Violation(phase:"state",check:.failed("bad",""))])
        let bounded = try shrink(g,original:original,limits:ShrinkLimits(maxSimplificationCandidates:3))
        XCTAssertTrue(bounded.validatedOriginal); XCTAssertEqual(bounded.minimized.inputs,[1])
        XCTAssertEqual(bounded.termination,"simplification_limit"); XCTAssertEqual(bounded.simplificationCandidates,3); XCTAssertEqual(pulls,3)
        pulls = 0
        let zero = try shrink(g,original:original,limits:ShrinkLimits(maxSimplificationCandidates:0))
        XCTAssertEqual(zero.termination,"simplification_limit"); XCTAssertEqual(zero.simplificationCandidates,0); XCTAssertEqual(pulls,0)
    }
    // Rust tests/exploration.rs skipped_hint_bound_is_per_iterator_not_per_run.
    func testSkippedHintBoundIsPerIteratorNotPerRun() throws {
        let m = Model<[Int], Int, Int>(initialState: { [] }, step: { s, i in Transition(state: s + [i]) },
            checkState: { s in s.count >= 6 ? [.failed("long", "long")] : [] },
            cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 })
        // Every simplified position re-yields an already-seen zero. The total
        // skipped across positions exceeds max_attempts; no single iterator does.
        let g = Generate(model: m, generate: { _, _ in 1 },
                         simplerInputs: { i in InputIterator(values: i == 0 ? Array(repeating: 0, count: 10) : [0]) },
                         equalInputs: { $0 == $1 })
        let original = Failure(inputs: Array(repeating: 1, count: 6), violations: [Violation(phase: "state", check: .failed("long", "long"))])
        let report = try shrink(g, original: original, config: ShrinkConfig(maxAttempts: 150))
        XCTAssertEqual(report.termination, "search_complete")
        XCTAssertLessThan(report.attempts, 150)
        XCTAssertEqual(report.minimized.inputs, Array(repeating: 0, count: 6))
    }
    func testShrinkAttemptLimitBoundsRepeatedSimplificationsWithoutExtraLimit() throws {
        var g = try searchFixture("cycle"), pulls = 0
        g.simplerInputs = { input in InputIterator { pulls += 1; if pulls > 5 { throw ModelError("attempt limit was not enforced") }; return input } }
        let original = Failure(inputs: [1], violations: [Violation(phase: "state", check: .failed("bad", ""))])
        let result = try shrink(g, original: original, config: ShrinkConfig(maxAttempts: 4))
        XCTAssertEqual(result.termination, "attempt_limit")
        XCTAssertEqual(result.attempts, 2)
        XCTAssertEqual(result.minimized.inputs, [1])
        XCTAssertEqual(result.history.filter { $0.outcome == .alreadySeen }.count, 0)
        XCTAssertEqual(pulls, 5)
        pulls = 0
        g.simplerInputs = { input in InputIterator { pulls += 1; return pulls <= 4 ? input : nil } }
        let finite = try shrink(g, original: original, config: ShrinkConfig(maxAttempts: 4))
        XCTAssertEqual(finite.termination, "search_complete")
        XCTAssertEqual(finite.attempts, 2)
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

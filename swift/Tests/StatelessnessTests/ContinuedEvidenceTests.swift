import XCTest
import Statelessness

/// continued-failure-evidence-v1. Counter: state += input, outputs
/// [input, state]; state check `high` fails at state >= 3; transition check
/// `big_step` fails for input >= 5.
private func bytes(_ v: Int) -> [UInt8] { withUnsafeBytes(of: Int64(v).littleEndian, Array.init) }
private func int(_ b: [UInt8]) throws -> Int {
    guard b.count == 8 else { throw ModelError("bad integer") }
    return Int(b.enumerated().reduce(Int64(0)) { $0 | Int64($1.element) << (8 * Int64($1.offset)) })
}
private func codec(_ name: String, version: UInt32 = 1) -> Codec<Int, Int, Int> {
    Codec(metadata: { Metadata(name: name, modelVersion: version) }, encodeState: bytes, decodeState: int, encodeInput: bytes, decodeInput: int, encodeOutput: bytes)
}
private func counter(version: UInt32 = 1, throwAt: Int? = nil) -> Model<Int, Int, Int> {
    Model(initialState: { 0 }, step: { s, i in Transition(state: s + i, outputs: [i, s + i]) }, checkState: { s in
        if s == throwAt { throw ModelError("checker exploded") }
        return [s >= 3 ? .failed("high", "\(s)") : .passed("high")]
    }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, checkTransition: { _, i, _ in i >= 5 ? [.failed("big_step", "\(i)")] : [] },
          inputs: { _ in InputIterator(values: [1, 5]) }, equalStates: ==, hashState: { UInt64($0) }, codec: codec("counter", version: version))
}
/// 0 unarmed, 1 armed, 2 fired. Input 0 waits, 1 arms (warning), 2 fires and is enabled only when armed.
private func trigger() -> Generate<Int, Int, Int> {
    let m = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: i == 1 && s == 0 ? 1 : i == 2 && s == 1 ? 2 : s) },
                                 checkState: { [$0 == 2 ? .failed("fired", "boom") : .passed("fired")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
                                 checkTransition: { s, i, _ in i == 1 && s == 0 ? [.failed("armed_warning", "armed")] : [] },
                                 inputs: { s in InputIterator(values: s == 0 ? [0, 1] : s == 1 ? [0, 2] : []) }, equalStates: ==, hashState: { UInt64($0) }, codec: codec("trigger"))
    return Generate(model: m, generate: { _, _ in nil }, isEnabled: { s, i in i != 2 || s == 1 }, equalInputs: ==)
}
private let witness = [1, 1, 1, 1, 5, 0, 7]
private func rebuilt(_ t: Trace, steps: [TraceStep]) -> ContinuedTrace {
    ContinuedTrace(trace: Trace(metadata: t.metadata, initialState: t.initialState, initialChecks: t.initialChecks, steps: steps, termination: t.termination, error: t.error))
}
final class ContinuedEvidenceTests: XCTestCase {
    func testRecordContinuesPastFailuresAndOldReadersReject() throws {
        let t = try recordContinued(counter(), inputs: InputIterator(values: witness))
        XCTAssertEqual(t.trace.steps.count, 7); XCTAssertEqual(t.trace.termination, .completed); XCTAssertEqual(t.failingSequences, [3, 4, 5, 6, 7])
        XCTAssertNoThrow(try validateContinuedRecording(t))
        XCTAssertThrowsError(try validateRecording(t.trace)) { XCTAssertTrue(String(describing: $0).contains("continues after property failure")) }
        XCTAssertThrowsError(try replay(counter(), trace: t.trace))
        // The stop-at-failure recorder ends at the first failure, unchanged.
        let legacy = try Statelessness.record(counter(), inputs: InputIterator(values: witness))
        XCTAssertEqual(legacy.steps.count, 3); XCTAssertEqual(legacy.termination, .propertyFailed)
        let encoded = try encodeContinuedTrace(t)
        XCTAssertEqual(try parseContinuedTrace(encoded), t)
        // trace-json v1 readers reject the artifact (unknown field or format), never reinterpret it.
        XCTAssertThrowsError(try parseTrace(encoded)) { XCTAssertTrue($0 is FormatError, "\($0)") }
        XCTAssertThrowsError(try parseContinuedTrace(try encodeTrace(legacy)))
    }
    func testReplayComparesEveryStep() throws {
        let t = try recordContinued(counter(), inputs: InputIterator(values: witness))
        let exact = try replayContinued(counter(), trace: t)
        XCTAssertTrue(exact.completeReproduction); XCTAssertEqual(exact.reproducedFailures, [3, 4, 5, 6, 7]); XCTAssertEqual(exact.replay.stepsVerified, 7)
        // Corrupted outputs at a later step: the verified prefix is not complete reproduction.
        var steps = t.trace.steps
        steps[4] = TraceStep(input: steps[4].input, disposition: steps[4].disposition, outputs: [bytes(5), bytes(0)], postState: steps[4].postState, checks: steps[4].checks)
        let corrupt = try replayContinued(counter(), trace: rebuilt(t.trace, steps: steps))
        XCTAssertFalse(corrupt.completeReproduction); XCTAssertEqual(corrupt.replay.outcome, "diverged"); XCTAssertEqual(corrupt.replay.step, 5); XCTAssertEqual(corrupt.replay.field, "outputs")
        // Reordered effects diverge.
        steps = t.trace.steps; steps[1] = TraceStep(input: steps[1].input, disposition: steps[1].disposition, outputs: steps[1].outputs.reversed(), postState: steps[1].postState, checks: steps[1].checks)
        XCTAssertEqual(try replayContinued(counter(), trace: rebuilt(t.trace, steps: steps)).replay.field, "outputs")
        // A suppressed later failure diverges on checks.
        steps = t.trace.steps; steps[6] = TraceStep(input: steps[6].input, disposition: steps[6].disposition, outputs: steps[6].outputs, postState: steps[6].postState, checks: [.passed("high")])
        let suppressed = try replayContinued(counter(), trace: rebuilt(t.trace, steps: steps))
        XCTAssertFalse(suppressed.completeReproduction); XCTAssertEqual(suppressed.replay.field, "checks"); XCTAssertEqual(suppressed.recordedFailures, [3, 4, 5, 6])
        // Incompatible model version.
        let incompatible = try replayContinued(counter(version: 2), trace: t)
        XCTAssertEqual(incompatible.replay.outcome, "incompatible"); XCTAssertFalse(incompatible.completeReproduction)
    }
    func testCorruptedAndTruncatedArtifactsAreRejected() throws {
        let encoded = try encodeContinuedTrace(try recordContinued(counter(), inputs: InputIterator(values: witness)))
        XCTAssertThrowsError(try parseContinuedTrace(Array(encoded.prefix(encoded.count / 2))))
        let text = String(decoding: encoded, as: UTF8.self)
        XCTAssertThrowsError(try parseContinuedTrace(Array(text.replacingOccurrences(of: "\"failing_sequences\":[3,4,5,6,7]", with: "\"failing_sequences\":[3,4]").utf8))) {
            XCTAssertTrue(String(describing: $0).contains("failing_sequences disagree"))
        }
        XCTAssertThrowsError(try parseContinuedTrace(Array(text.replacingOccurrences(of: "\"version\":1", with: "\"version\":2").utf8)))
        XCTAssertTrue(text.contains("\"failing_sequences\":[3,4,5,6,7]"))
        let failed = ContinuedTrace(trace: Trace(metadata: Metadata(name: "counter"), initialState: bytes(0), initialChecks: [], steps: [], termination: .propertyFailed))
        XCTAssertThrowsError(try encodeContinuedTrace(failed))
    }
    func testIncompleteChecksAfterFailure() throws {
        let t = try recordContinued(counter(throwAt: 4), inputs: InputIterator(values: [1, 1, 1, 1, 1]))
        XCTAssertEqual(t.trace.termination, .modelError); XCTAssertEqual(t.trace.steps.count, 3); XCTAssertEqual(t.failingSequences, [3])
        XCTAssertTrue(t.trace.error.contains("checker exploded"))
        XCTAssertEqual(try parseContinuedTrace(try encodeContinuedTrace(t)), t)
    }
    func testTwoFailuresOnOneTurn() throws {
        let g = Generate(model: counter(), generate: { _, _ in nil }, equalInputs: ==)
        let target = Violation(phase: "transition", check: .failed("big_step", ""))
        let r = try shrinkContinued(g, inputs: witness, target: target)
        XCTAssertTrue(r.base.validatedOriginal); XCTAssertEqual(r.base.termination, "search_complete")
        XCTAssertEqual(r.base.original.inputs, witness); XCTAssertEqual(r.base.minimized.inputs, [7])
        XCTAssertEqual(r.originalFindings.map(\.sequence), [3, 4, 5, 6, 7])
        XCTAssertEqual(r.minimizedFindings, [ContinuedFinding(sequence: 1, violations: [Violation(phase: "state", check: .failed("high", "7")), Violation(phase: "transition", check: .failed("big_step", "7"))])])
        // Stop-at-failure shrinking cannot reach the later target.
        XCTAssertThrowsError(try shrink64(g, original: Failure(inputs: witness, violations: [target])))
    }
    func testSelectedLaterTargetOnSuccessiveTurns() throws {
        let r = try shrinkContinued(trigger(), inputs: [0, 1, 0, 2], target: Violation(phase: "state", check: .failed("fired", "")))
        XCTAssertEqual(r.base.minimized.inputs, [1, 2])
        XCTAssertEqual(r.minimizedFindings, [ContinuedFinding(sequence: 1, violations: [Violation(phase: "transition", check: .failed("armed_warning", "armed"))]),
                                             ContinuedFinding(sequence: 2, violations: [Violation(phase: "state", check: .failed("fired", "boom"))])])
        let outcomes = r.base.history.map(\.outcome)
        XCTAssertTrue(outcomes.contains(.invalidCausality)); XCTAssertTrue(outcomes.contains(.differentFailure))
        // A target that does not terminate the witness is a wrong target.
        XCTAssertThrowsError(try shrinkContinued(trigger(), inputs: [0, 1, 0, 2], target: Violation(phase: "transition", check: .failed("armed_warning", "")))) {
            XCTAssertTrue(String(describing: $0).contains("does not reproduce"))
        }
    }
    func testBudgetKeepsOriginalAndMinimizedWitnesses() throws {
        let g = Generate(model: counter(), generate: { _, _ in nil }, equalInputs: ==)
        let r = try shrinkContinued(g, inputs: witness, target: Violation(phase: "transition", check: .failed("big_step", "")), config: ShrinkConfig64(maxAttempts: 2))
        XCTAssertEqual(r.base.termination, "attempt_limit"); XCTAssertEqual(r.base.original.inputs, witness); XCTAssertEqual(r.base.minimized.inputs, [5, 0, 7])
        XCTAssertEqual(r.minimizedFindings.last?.sequence, 3)
    }
    func testGraphFindingsFlowIntoContinuedEvidence() throws {
        let g = trigger()
        let report = try exploreGraph(g.model, options: GraphSearchOptions(failurePolicy: .collect(maxRetainedFindings: 10, expandFailingSuccessors: true)))
        XCTAssertEqual(report.findings.map(\.witness), [[1], [1, 2]]); XCTAssertEqual(report.findings.map(\.requiresContinuedEvidence), [false, true])
        let later = report.findings[1]
        let shrunk = try shrinkContinued(g, inputs: later.witness, target: later.violations[0])
        XCTAssertEqual(shrunk.base.minimized.inputs, [1, 2])
        let trace = try recordContinued(g.model, inputs: InputIterator(values: later.witness))
        XCTAssertTrue(try replayContinued(g.model, trace: trace).completeReproduction); XCTAssertEqual(trace.failingSequences, [1, 2])
    }
}

import Foundation
import XCTest
import Statelessness
import StatelessConformance

private struct CounterState: Equatable { var value = 0 }
private func counter() -> Model<CounterState, String, String> {
    Model(initialState: { CounterState() },
          step: { s, i in guard i == "increment" else { throw ModelError("unknown input") }; return Transition(state: CounterState(value: s.value + 1), outputs: ["changed"]) },
          checkState: { s in [s.value > 2 ? .failed("bound", "above two") : .passed("bound")] },
          cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
          inputs: { _ in InputIterator(values: ["increment"]) }, equalStates: { $0 == $1 }, hashState: { _ in 0 },
          codec: Codec(metadata: { Metadata(name: "counter", build: "native-test-v1") },
                       encodeState: { Array(String($0.value).utf8) },
                       decodeState: { bytes in guard let s = String(bytes: bytes, encoding: .utf8), let n = Int(s) else { throw ModelError("invalid counter") }; return CounterState(value: n) },
                       encodeInput: { Array($0.utf8) },
                       decodeInput: { bytes in guard let s = String(bytes: bytes, encoding: .utf8) else { throw ModelError("invalid UTF-8") }; return s },
                       encodeOutput: { Array($0.utf8) }))
}
private func recording(_ inputs: [String] = []) throws -> Trace { try Statelessness.record(counter(), inputs: InputIterator(values: inputs)) }
private func altered(_ t: Trace, initial: [UInt8]? = nil, steps: [TraceStep]? = nil, termination: Termination? = nil) -> Trace {
    Trace(metadata: t.metadata, initialState: initial ?? t.initialState, initialChecks: t.initialChecks, steps: steps ?? t.steps, termination: termination ?? t.termination, error: t.error)
}
final class NativeTests: XCTestCase {
    func testObjectFieldsPreserveExactUnicodeKeys() throws {
        let composed = "\u{e9}", decomposed = "e\u{301}"
        let value = JSON.object([ExactString(composed): .number("1"), ExactString(decomposed): .number("2")])
        let decoded = try parseJSON(renderJSON(value))
        let fields = try objectFields(decoded, required: [composed], optional: [decomposed])
        XCTAssertEqual(fields.count, 2)
        XCTAssertEqual(fields[composed], .number("1"))
        XCTAssertEqual(fields[decomposed], .number("2"))
        XCTAssertEqual(try objectFields(decoded, required: [composed, decomposed]).count, 2)
        XCTAssertThrowsError(try objectFields(decoded, required: [composed]))
        XCTAssertThrowsError(try objectFields(.object([ExactString(composed): .null]), required: [decomposed]))
    }

    func testNativeStructCollisionWitness() throws {
        let r = try enumerateStates(counter()); XCTAssertEqual(r.termination, "failure_found"); XCTAssertEqual(r.states, 3); XCTAssertEqual(r.transitions, 3)
        XCTAssertEqual(r.failure?.inputs, ["increment", "increment", "increment"]); XCTAssertEqual(r.failure?.violations.first?.phase, "state")
    }
    func testRecordReplayJSONRoundtrip() throws {
        let t = try recording(Array(repeating: "increment", count: 5)); XCTAssertEqual(t.steps.count, 3); XCTAssertEqual(t.termination, .propertyFailed)
        let decoded = try parseTrace(encodeTrace(t)); XCTAssertEqual(decoded, t)
        let r = try replay(counter(), trace: decoded); XCTAssertEqual(r.outcome, "exact"); XCTAssertTrue(r.failureReproduced)
    }
    func testObservationNeverSteps() throws {
        var m = counter(); m.step = { _, _ in XCTFail("step must not be called"); throw ModelError("step must not be called") }
        let checks = try checkObserved(m, before: CounterState(), input: "increment", transition: Transition(state: CounterState(value: 1)), sequence: 1)
        XCTAssertEqual(checks, [.passed("bound")])
    }
    func testPeriodicPolicy() throws {
        let checks = try checkObserved(counter(), before: CounterState(), input: "increment", transition: Transition(state: CounterState(value: 3)), sequence: 1, policy: CheckPolicy(stateEvery: 2, transitionChecks: false))
        XCTAssertEqual(checks.map(\.status), [.skipped, .skipped])
    }
    func testCheckerErrorDiscardsPartialFindings() throws {
        var m = counter(); m.checkTransition = { _, _, _ in throw ModelError("broken checker") }
        XCTAssertThrowsError(try checkObserved(m, before: CounterState(), input: "increment", transition: Transition(state: CounterState(value: 3)), sequence: 1)) { XCTAssertTrue($0 is ModelError) }
    }
    func testLazyIteratorError() throws {
        var m = counter(); m.inputs = { _ in var n = 0; return InputIterator { n += 1; if n == 1 { return "increment" }; throw ModelError("domain failed") } }
        XCTAssertThrowsError(try enumerateStates(m)) { XCTAssertTrue(String(describing: $0).contains("enumerate inputs: domain failed")) }
    }
    func testDepthBoundaryOnlyProbesOnce() throws {
        var m = counter(); var calls = 0
        m.inputs = { _ in InputIterator { calls += 1; if calls > 1 { throw ModelError("extra probe") }; return "increment" } }
        let r = try enumerateStates(m, config: SearchConfig(maxDepth: 0)); XCTAssertEqual(r.termination, "depth_bound"); XCTAssertEqual(calls, 1)
    }
    func testInitialErrorHasNoTrace() throws {
        var m = counter(); m.initialState = { throw ModelError("initial failed") }
        XCTAssertThrowsError(try Statelessness.record(m, inputs: InputIterator(values: []))) { XCTAssertTrue($0 is ModelError) }
    }
    func testLaterErrorRetainsOnlyCoherentPrefix() throws {
        let t = try recording(["increment", "unknown"]); XCTAssertEqual(t.termination, .modelError); XCTAssertEqual(t.steps.count, 1)
        XCTAssertEqual(try replay(counter(), trace: t).outcome, "exact")
    }
    func testCodecError() throws {
        var m = counter(); m.codec?.encodeState = { _ in throw ModelError("broken codec") }
        XCTAssertThrowsError(try Statelessness.record(m, inputs: InputIterator(values: []))) { XCTAssertTrue($0 is ModelError) }
    }
    func testCanonicalInitialBytes() throws {
        let t = try altered(recording(), initial: Array("00".utf8)); XCTAssertThrowsError(try replay(counter(), trace: t)) { XCTAssertTrue(String(describing: $0).contains("not canonical")) }
    }
    func testCanonicalInputBytes() throws {
        var m = counter(); m.codec?.decodeInput = { String(decoding: $0, as: UTF8.self).trimmingCharacters(in: .whitespaces) }
        let t = try recording(["increment"]), s = try XCTUnwrap(t.steps.first)
        let changed = altered(t, steps: [TraceStep(input: Array("increment ".utf8), disposition: s.disposition, outputs: s.outputs, postState: s.postState, checks: s.checks)])
        XCTAssertThrowsError(try replay(m, trace: changed)) { XCTAssertTrue(String(describing: $0).contains("not canonical")) }
    }
    func testReplayDoesNotReconstructInitialState() throws {
        let t = try recording(["increment"]); var m = counter(); m.initialState = { throw ModelError("must not call") }
        XCTAssertEqual(try replay(m, trace: t).outcome, "exact")
    }
    func testBuildOverrideDoesNotWaiveVersions() throws {
        let t = try recording(); var m = counter(); m.codec?.metadata = { Metadata(name: "counter", build: "different") }
        XCTAssertEqual(try replay(m, trace: t).outcome, "incompatible")
        let r = try replay(m, trace: t, allowBuildMismatch: true); XCTAssertEqual(r.outcome, "exact"); XCTAssertFalse(r.buildMatches)
        m.codec?.metadata = { Metadata(name: "counter", modelVersion: 2, build: "different") }
        XCTAssertEqual(try replay(m, trace: t, allowBuildMismatch: true).outcome, "incompatible")
    }
    func testDuplicateFailureIDs() throws {
        var m = counter(); m.checkState = { _ in [.failed("same", "one"), .failed("same", "two")] }
        XCTAssertEqual(try enumerateStates(m).failure?.violations.map { $0.check.details }, ["one", "two"])
    }
    func testMutableReferenceBranchesAreIndependent() throws {
        final class Box { var items: [String]; init(_ items: [String] = []) { self.items = items } }
        let start = Box()
        let m = Model<Box, String, String>(initialState: { start },
            step: { s, i in s.items.append(i); return Transition(state: s) },
            checkState: { s in [s.items.count > 1 ? .failed("one", "aliased") : .passed("one")] },
            cloneState: { Box($0.items) }, cloneInput: { $0 }, cloneOutput: { $0 },
            inputs: { InputIterator(values: $0.items.isEmpty ? ["a", "b"] : []) }, equalStates: { $0.items == $1.items })
        let r = try enumerateStates(m); XCTAssertEqual(r.termination, "graph_exhausted"); XCTAssertEqual(r.states, 3); XCTAssertEqual(start.items, [])
    }
    func testReturnedCodecArraysAreIndependent() throws {
        var m = counter(), buffer: [UInt8] = [0]
        m.codec?.encodeState = { s in buffer[0] = UInt8(s.value + 48); return buffer }
        m.codec?.encodeOutput = { _ in buffer[0] = 120; return buffer }
        let t = try Statelessness.record(m, inputs: InputIterator(values: ["increment", "increment"])); buffer[0] = 255
        XCTAssertEqual(t.initialState, [48]); XCTAssertEqual(t.steps[0].postState, [49]); XCTAssertEqual(t.steps[0].outputs[0], [120])
        XCTAssertEqual(try replay(m, trace: t).outcome, "exact")
    }
    func testInvalidConfiguration() throws {
        for c in [SearchConfig(maxStates: 0), SearchConfig(maxStates: -1), SearchConfig(maxDepth: -1)] { XCTAssertThrowsError(try enumerateStates(counter(), config: c)) { XCTAssertTrue($0 is ConfigError) } }
        XCTAssertThrowsError(try checkObserved(counter(), before: CounterState(), input: "increment", transition: Transition(state: CounterState()), sequence: 0))
    }
    func testRngEmptyDomainAndVectors() {
        var a = Rng(seed: 123), b = Rng(seed: 123), c = Rng()
        XCTAssertNil(a.index(0)); XCTAssertEqual(a.nextU64(), b.nextU64()); XCTAssertEqual(c.nextU64(), 16294208416658607535)
        let bounds: [UInt64] = [1, 2, 3, (UInt64(1) << 63) + 1, UInt64.max]
        for upper in bounds { let n = a.index(upper); XCTAssertNotNil(n); XCTAssertLessThan(n ?? upper, upper) }
    }
    func testRetentionLimits() throws {
        var l = TraceLimits(); l.maxPayloadBytes = 1
        let t = try Statelessness.record(counter(), inputs: InputIterator(values: ["increment"]), limits: l); XCTAssertEqual(t.termination, .modelError); XCTAssertEqual(t.steps, [])
        l = TraceLimits(); l.maxBlobBytes = 0; XCTAssertThrowsError(try Statelessness.record(counter(), inputs: InputIterator(values: []), limits: l))
        l = TraceLimits(); l.maxItems = 0; XCTAssertThrowsError(try Statelessness.record(counter(), inputs: InputIterator(values: []), limits: l))
    }
    func testJSONLimits() throws {
        let bytes = try encodeTrace(recording(["increment"]))
        var l = TraceLimits(); l.maxJSONBytes = 1; XCTAssertThrowsError(try parseTrace(bytes, limits: l))
        l = TraceLimits(); l.maxBlobBytes = 0; XCTAssertThrowsError(try parseTrace(bytes, limits: l))
        l = TraceLimits(); l.maxItems = 1; XCTAssertThrowsError(try parseTrace(bytes, limits: l))
        l = TraceLimits(); l.maxPayloadBytes = 1; XCTAssertThrowsError(try parseTrace(bytes, limits: l))
    }
    func testEncodeTraceChecksPayloadBeforeJSONExpansion() throws {
        let trace = try recording(["increment"])
        var limits = TraceLimits()
        limits.maxBlobBytes = 0
        XCTAssertThrowsError(try encodeTrace(trace, limits: limits))
        limits = TraceLimits(); limits.maxPayloadBytes = 0
        XCTAssertThrowsError(try encodeTrace(trace, limits: limits))
        limits = TraceLimits(); limits.maxSteps = 0
        XCTAssertThrowsError(try encodeTrace(trace, limits: limits))
        limits = TraceLimits(); limits.maxItems = 0
        XCTAssertThrowsError(try encodeTrace(trace, limits: limits))
        XCTAssertEqual(try parseTrace(encodeTrace(trace)), trace)
    }
    func testEncodeTracePreflightsJSONTextAndPreservesExactLimit() throws {
        let huge = String(repeating: "é", count: 100_000)
        let invalid = Trace(metadata: Metadata(name: huge), initialState: [],
            initialChecks: [Check("invalid", .passed, "details")], steps: [], termination: .completed)
        var limits = TraceLimits(); limits.maxJSONBytes = 32
        // The lower-bound check rejects the oversized metadata before building
        // and validating the JSON tree (whose check is also invalid).
        XCTAssertThrowsError(try encodeTrace(invalid, limits: limits)) { error in
            XCTAssertTrue(String(describing: error).contains("JSON output byte limit"))
        }
        let ordinary = try recording(["increment"])
        let expected = try encodeTrace(ordinary)
        limits = TraceLimits(); limits.maxJSONBytes = expected.count
        XCTAssertEqual(try encodeTrace(ordinary, limits: limits), expected)
        limits.maxJSONBytes = expected.count - 1
        XCTAssertThrowsError(try encodeTrace(ordinary, limits: limits))
        let checkHeavy = Trace(metadata: Metadata(name: "x"), initialState: [],
            initialChecks: [Check.failed("id", huge)], steps: [], termination: .propertyFailed)
        limits.maxJSONBytes = 64
        XCTAssertThrowsError(try encodeTrace(checkHeavy, limits: limits))
    }
    func testDirectTraceJSONBlobLimits() throws {
        let metadata = Metadata(name: "bounded", modelVersion: 1, propertiesVersion: 1, codecVersion: 1, build: "test")
        func document(_ hex: String) -> JSON {
            .obj(["format": .string("stateless.trace-json"), "version": .int(1),
                  "metadata": metadataJSON(metadata), "initial_state": .string(hex),
                  "initial_checks": .array([]), "steps": .array([]),
                  "termination": .string("completed"), "error": .string("")])
        }
        var limits = TraceLimits(); limits.maxBlobBytes = 2; limits.maxPayloadBytes = 2
        XCTAssertEqual(try traceFromJSON(document("00ff"), limits: limits).initialState, [0, 255])
        for invalid in ["0", "00ff00", "00FF", "é", String(repeating: "0", count: 1_000_000)] {
            XCTAssertThrowsError(try traceFromJSON(document(invalid), limits: limits))
        }
        limits.maxPayloadBytes = 1
        XCTAssertThrowsError(try traceFromJSON(document("00ff"), limits: limits))
        limits.maxBlobBytes = 0
        XCTAssertEqual(try traceFromJSON(document(""), limits: limits).initialState, [])
        XCTAssertThrowsError(try traceFromJSON(document("00"), limits: limits))
    }
    func testMalformedJSONRejected() throws {
        for raw in [#"{"x":1,"x":2}"#, #"{"x":"\ud800"}"#, #"{"x":NaN}"#, #"{"x":1.0}"#, #"{"x":1e0}"#, "{} trailing", "\u{feff}{}"] { XCTAssertThrowsError(try parseJSON(Array(raw.utf8))) }
        XCTAssertThrowsError(try parseJSON([255])); XCTAssertEqual(try parseJSON(Array(#""\ud83d\ude00""#.utf8)), .string("😀"))
    }
    func testExactStringEqualityNotCanonicalEquivalence() {
        XCTAssertEqual("é", "e\u{301}") // Swift's default is intentionally different.
        XCTAssertNotEqual(ExactString("é"), ExactString("e\u{301}"))
        XCTAssertNotEqual(Check.failed("x", "é"), Check.failed("x", "e\u{301}"))
        XCTAssertNotEqual(Disposition.rejected("é"), .rejected("e\u{301}"))
        XCTAssertNotEqual(Metadata(name: "é"), Metadata(name: "e\u{301}"))
    }
    func testJSONKeysPreserveScalarIdentity() throws {
        let parsed = try parseJSON(Array(#"{"é":1,"e\u0301":2}"#.utf8))
        guard case let .object(o) = parsed else { return XCTFail("expected object") }; XCTAssertEqual(o.count, 2)
        XCTAssertEqual(try parseJSON(renderJSON(parsed)), parsed)
    }
    func testByteOrderMarkInsideAStringIsNotRemoved() throws {
        let json = try parseJSON(Array(#""\ufeffx""#.utf8)); XCTAssertEqual(json, .string("\u{feff}x"))
        let value = try parseJSON(Array(#"{"id":"bom","initial":"\ufeffx","states":[{"id":"\ufeffx"}]}"#.utf8))
        let m = try TableModel(value).model(), codec = try XCTUnwrap(m.codec)
        XCTAssertTrue(exactText(try codec.decodeState(Array("\u{feff}x".utf8)), "\u{feff}x"))
    }
    func testUnicodeReplayDetailsStayExact() throws {
        var m = counter(); m.checkState = { _ in [.failed("p", "é")] }
        let t = try Statelessness.record(m, inputs: InputIterator(values: [])); m.checkState = { _ in [.failed("p", "e\u{301}")] }
        let r = try replay(m, trace: t); XCTAssertEqual(r.outcome, "diverged"); XCTAssertTrue(r.failureReproduced)
    }
    func testFalseTerminationAndContinuation() throws {
        let t = try recording(Array(repeating: "increment", count: 4))
        XCTAssertThrowsError(try replay(counter(), trace: altered(t, termination: .completed)))
        XCTAssertThrowsError(try replay(counter(), trace: altered(t, steps: t.steps + [try XCTUnwrap(t.steps.last)])))
    }
    func testZeroAndExactStepLimits() throws {
        XCTAssertEqual(try Statelessness.record(counter(), inputs: InputIterator(values: []), maxSteps: 0).termination, .completed)
        XCTAssertEqual(try Statelessness.record(counter(), inputs: InputIterator(values: ["increment"]), maxSteps: 0).termination, .stepLimit)
        XCTAssertEqual(try Statelessness.record(counter(), inputs: InputIterator(values: ["increment"]), maxSteps: 1).termination, .completed)
        XCTAssertEqual(try Statelessness.record(counter(), inputs: InputIterator(values: ["increment", "increment"]), maxSteps: 1).termination, .stepLimit)
    }
    func testInvalidChecksAreErrors() throws {
        var m = counter(); m.checkState = { _ in [Check("x", .passed, "wrong")] }
        XCTAssertThrowsError(try enumerateStates(m)) { XCTAssertTrue($0 is ModelError) }
    }
}

import Foundation
import XCTest
import Statelessness

private func intCodec(_ name: String) -> Codec<Int, Int, Int> {
    Codec(metadata: { Metadata(name: name, build: "é") }, encodeState: { Array(String($0).utf8) }, decodeState: { b in
        guard let s = String(bytes: b, encoding: .utf8), let v = Int(s) else { throw ModelError("bad integer") }; return v
    }, encodeInput: { [UInt8($0)] }, decodeInput: { b in guard b.count == 1, b[0] < 3 else { throw ModelError("bad input") }; return Int(b[0]) }, encodeOutput: { [UInt8($0)] })
}
private func queue(_ mutation: String = "none") -> WithOracle<[Int], Int, Int, String> {
    let actual = Model<[Int], Int, String>(initialState: { [] }, step: { q, i in
        var next = q
        if i < 2 {
            if q.count == 2 { return Transition(state: q, disposition: .rejected("full")) }
            next.append(i)
            var effects = [String(10 + i), String(20 + next.count)]
            if mutation == "order" { effects.reverse() }
            if mutation == "omit" { effects.removeLast() }
            return Transition(state: mutation == "state" ? q : next, outputs: effects)
        }
        if next.isEmpty { return Transition(state: q, disposition: .ignored(mutation == "reason" ? "different" : "empty")) }
        let first = next.removeFirst()
        return Transition(state: next, outputs: [String(30 + first), String(20 + next.count)])
    }, checkState: { _ in [.passed("actual.state")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
    checkTransition: { _, _, _ in [.passed("actual.edge")] }, inputs: { _ in InputIterator(values: [0, 1, 2]) }, equalStates: { $0 == $1 },
    codec: Codec(metadata: { Metadata(name: "queue", build: "é") }, encodeState: { $0.map(UInt8.init) }, decodeState: { b in
        guard b.count <= 2, b.allSatisfy({ $0 < 2 }) else { throw ModelError("bad queue") }; return b.map(Int.init)
    }, encodeInput: { [UInt8($0)] }, decodeInput: { b in guard b.count == 1, b[0] < 3 else { throw ModelError("bad input") }; return Int(b[0]) }, encodeOutput: { Array($0.utf8) }))
    let reference = Model<Int, Int, Int>(initialState: { mutation == "initial" ? 1 : 0 }, step: { digits, i in
        let count = digits == 0 ? 0 : (digits < 3 ? 1 : 2)
        if i < 2 {
            if count == 2 { return Transition(state: digits, disposition: .rejected("full")) }
            return Transition(state: digits + (i + 1) * (count == 0 ? 1 : 3), outputs: [10 + i, 21 + count])
        }
        if count == 0 { return Transition(state: digits, disposition: .ignored("empty")) }
        return Transition(state: digits / 3, outputs: [29 + digits % 3, 19 + count])
    }, checkState: { _ in [.passed("reference.state")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
    checkTransition: { _, _, _ in [.passed("reference.edge")] }, equalStates: { $0 == $1 }, codec: intCodec("reference"))
    let projection = Projection<[Int], Int, String, Int, [Int], Int>(metadata: { Metadata(name: "projection", build: "é") }, actualState: { $0 }, referenceState: { h in
        var h = h, result: [Int] = []; while h > 0 { result.append(h % 3 - 1); h /= 3 }; return result
    }, actualEffect: { s in guard let v = Int(s) else { throw ModelError("bad effect") }; return v }, referenceEffect: { $0 }, equalState: { $0 == $1 }, equalEffect: { $0 == $1 })
    return WithOracle(actual, oracle: Paired(reference: reference, projection: projection).oracle)
}

final class OracleTests: XCTestCase {
    func testSharedGoldenFixture() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let fixture = ProcessInfo.processInfo.environment["STATELESS_PAIRED_CORPUS"].map { URL(fileURLWithPath: $0) } ?? root.appendingPathComponent("conformance/paired-v1.tsv")
        let text = try String(contentsOf: fixture, encoding: .utf8)
        for line in text.split(separator: "\n").dropFirst() {
            let c = line.split(separator: "\t").map(String.init)
            let report = try enumerateStates(queue(c[1]).model, config: SearchConfig(maxStates: Int(c[2])!, maxTransitions: UInt64(c[3])!, maxDepth: Int(c[4])!))
            XCTAssertEqual(report.termination, c[5], c[0]); XCTAssertEqual(report.states, Int(c[6])!, c[0]); XCTAssertEqual(report.transitions, UInt64(c[7])!, c[0])
            let violations = report.failure?.violations ?? []
            XCTAssertEqual(violations.map { $0.check.id }.joined(separator: ","), c[8] == "-" ? "" : c[8], c[0])
            XCTAssertEqual((report.failure?.inputs ?? []).map(String.init).joined(separator: ","), c[9] == "-" ? "" : c[9], c[0])
            XCTAssertEqual(violations.map { $0.phase }.joined(separator: ","), c[10] == "-" ? "" : c[10], c[0])
            XCTAssertEqual(report.skippedChecks, UInt64(c[11])!, c[0])
        }
    }
    func testCheckOrderAndExactFailureReplay() throws {
        let adapter = queue("order"), model = adapter.model
        let trace = try Statelessness.record(model, inputs: InputIterator(values: [0, 1, 2]))
        XCTAssertEqual(trace.initialChecks.map { $0.id }, ["actual.state", "reference.state", "pair.state"])
        XCTAssertEqual(trace.steps[0].checks.map { $0.id }, ["actual.state", "reference.state", "pair.state", "actual.edge", "reference.edge", "pair.effects", "pair.outcome"])
        XCTAssertEqual(trace.steps.count, 1); XCTAssertEqual(trace.termination, .propertyFailed)
        let report = try replay(model, trace: parseTrace(encodeTrace(trace)))
        XCTAssertEqual(report.outcome, "exact"); XCTAssertTrue(report.failureReproduced)
    }
    func testHistoryEqualityFramingAndRestart() throws {
        let adapter = queue(), m = adapter.model
        XCTAssertFalse(try XCTUnwrap(m.equalStates)(adapter.attach(model: [], oracle: 0), adapter.attach(model: [], oracle: 1)))
        let saved = try adapter.attach(model: [0, 1], oracle: 7)
        let bytes = try adapter.encodeState(saved)
        XCTAssertEqual(bytes, [79,82,67,49,2,0,0,0,0,0,0,0,0,1,1,0,0,0,0,0,0,0,55])
        let restored = try adapter.decodeState(bytes)
        XCTAssertEqual(restored.model, [0,1]); XCTAssertEqual(restored.oracle, 7)
        var restarted = m; restarted.initialState = { restored }
        let t = try Statelessness.record(restarted, inputs: InputIterator(values: [2,2,2]))
        XCTAssertEqual(t.termination, .completed); XCTAssertEqual(try replay(m, trace: t).outcome, "exact")
        for size in 0..<bytes.count { XCTAssertThrowsError(try adapter.decodeState(Array(bytes.prefix(size)))) }
        XCTAssertThrowsError(try adapter.decodeState(bytes + [0]))
        XCTAssertThrowsError(try adapter.decodeState([79,82,67,49] + Array(repeating: 255, count: 8)))
        XCTAssertTrue(try adapter.metadata().build.hasPrefix("2:é:"))
    }
    func testHistoryOnlyBFSDoesNotMerge() throws {
        let actual = Model<Int, Int, Int>(initialState: { 0 }, step: { _, _ in Transition(state: 0) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, inputs: { _ in InputIterator(values: [0]) }, equalStates: { $0 == $1 })
        let oracle = Oracle<Int, Int, Int, Int>(metadata: { Metadata(name: "history") }, initialState: { 0 }, advance: { h, _, _, _ in (h + 1) % 3 }, checkState: { _, _ in [] }, cloneHistory: { $0 }, equalHistory: { $0 == $1 })
        let report = try enumerateStates(WithOracle(actual, oracle: oracle).model)
        XCTAssertEqual(report.termination, "graph_exhausted"); XCTAssertEqual(report.states, 3); XCTAssertEqual(report.transitions, 3)
    }
    func testObservationErrorRetainsActualAndDoesNotExecute() throws {
        final class Box { var value: Int; init(_ value: Int) { self.value = value } }
        var executions = 0
        let actual = Model<Box, Box, Box>(initialState: { Box(0) }, step: { _, _ in executions += 1; return Transition(state: Box(1)) }, checkState: { _ in [] }, cloneState: { Box($0.value) }, cloneInput: { Box($0.value) }, cloneOutput: { Box($0.value) })
        let oracle = Oracle<Box, Box, Box, Box>(metadata: { Metadata(name: "error") }, initialState: { Box(0) }, advance: { h, i, outputs, _ in h.value = 9; i.value = 9; outputs[0].value = 9; throw ModelError("history failed") }, checkState: { _, _ in [] }, cloneHistory: { Box($0.value) })
        let a = WithOracle(actual, oracle: oracle), history = Box(0), input = Box(2), output = Box(3), state = Box(4)
        do {
            _ = try a.observeTransition(before: a.attach(model: Box(0), oracle: history), input: input, transition: Transition(state: state, outputs: [output], disposition: .ignored("é")))
            XCTFail("expected observation failure")
        } catch let e as ObservationError<Box, Box> {
            XCTAssertEqual(e.transition.state.value, 4); XCTAssertEqual(e.transition.outputs[0].value, 3); XCTAssertEqual(e.transition.disposition, .ignored("é"))
        }
        XCTAssertEqual(executions, 0); XCTAssertEqual(history.value, 0); XCTAssertEqual(input.value, 2); XCTAssertEqual(output.value, 3); XCTAssertEqual(state.value, 4)
    }
    func testExactUnicodeOutcomeAndIdentity() throws {
        var actual = Model<Int, Int, Int>(initialState: { 0 }, step: { _, _ in Transition(state: 0, disposition: .ignored("e\u{301}")) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, codec: intCodec("é"))
        var reference = actual
        reference.step = { _, _ in Transition(state: 0, disposition: .ignored("é")) }
        let p = Projection<Int, Int, Int, Int, Int, Int>(metadata: { Metadata(name: "projection") }, actualState: { $0 }, referenceState: { $0 }, actualEffect: { $0 }, referenceEffect: { $0 }, equalState: { $0 == $1 }, equalEffect: { $0 == $1 })
        let a = WithOracle(actual, oracle: Paired(reference: reference, projection: p).oracle)
        let trace = try Statelessness.record(a.model, inputs: InputIterator(values: [0]))
        XCTAssertEqual(trace.steps[0].checks.last?.id, "pair.outcome")
        XCTAssertEqual(trace.steps[0].checks.last?.status, .failed)
        XCTAssertEqual(try replay(a.model, trace: trace).outcome, "exact")
        actual.codec = intCodec("e\u{301}")
        let changed = WithOracle(actual, oracle: Paired(reference: reference, projection: p).oracle)
        XCTAssertEqual(try replay(changed.model, trace: trace, allowBuildMismatch: true).outcome, "incompatible")
        var projectionChanged = p
        projectionChanged = Projection(metadata: { Metadata(name: "projection", propertiesVersion: 2) }, actualState: p.actualState, referenceState: p.referenceState, actualEffect: p.actualEffect, referenceEffect: p.referenceEffect, equalState: p.equalState, equalEffect: p.equalEffect)
        let changedProperties = WithOracle(a.actual, oracle: Paired(reference: reference, projection: projectionChanged).oracle)
        XCTAssertEqual(try replay(changedProperties.model, trace: trace, allowBuildMismatch: true).outcome, "incompatible")
    }
    func testObservationSnapshotFailureRetainsCompletedTransition() throws {
        var actual = queue().actual
        actual.cloneState = { _ in throw ModelError("snapshot failed") }
        let a = WithOracle(actual, oracle: queue().oracle)
        let completed = Transition(state: [1], outputs: ["11"], disposition: Disposition.accepted)
        do {
            _ = try a.observeTransition(before: OracleState(model: [], oracle: 0), input: 1, transition: completed)
            XCTFail("expected snapshot error")
        } catch let error as ObservationError<[Int], String> {
            XCTAssertEqual(error.transition.state, [1]); XCTAssertEqual(error.transition.outputs, ["11"])
        }
    }
    func testReplayRejectsNoncanonicalInitialAndInput() throws {
        let a = queue(), m = a.model
        let trace = try Statelessness.record(m, inputs: InputIterator(values: [0]))
        // The reference integer decoder accepts 00; exact replay requires 0.
        var initial = Array(trace.initialState.dropLast())
        initial[12] = 2
        initial += Array("00".utf8)
        let alias = Trace(metadata: trace.metadata, initialState: initial, initialChecks: trace.initialChecks, steps: trace.steps, termination: trace.termination)
        XCTAssertEqual(try a.decodeState(initial).oracle, 0)
        XCTAssertThrowsError(try replay(m, trace: alias))
        var lenient = a.actual, c = try XCTUnwrap(lenient.codec)
        c.decodeInput = { b in guard let first = b.first, first < 3 else { throw ModelError("bad input") }; return Int(first) }
        lenient.codec = c
        let first = trace.steps[0]
        let step = TraceStep(input: first.input + [0], disposition: first.disposition, outputs: first.outputs, postState: first.postState, checks: first.checks)
        let inputAlias = Trace(metadata: trace.metadata, initialState: trace.initialState, initialChecks: trace.initialChecks, steps: [step], termination: trace.termination)
        XCTAssertThrowsError(try replay(WithOracle(lenient, oracle: a.oracle).model, trace: inputAlias))
    }
    func testReferenceSemanticsRemainStrictWithBuildOverride() throws {
        let a = queue(), trace = try Statelessness.record(a.model, inputs: InputIterator(values: []))
        var history = a.oracle
        let original = history.metadata
        history.metadata = {
            let m = try original()
            return Metadata(name: m.name + ":reference-change", modelVersion: m.modelVersion, propertiesVersion: m.propertiesVersion, codecVersion: m.codecVersion, build: "new-build")
        }
        XCTAssertEqual(try replay(WithOracle(a.actual, oracle: history).model, trace: trace, allowBuildMismatch: true).outcome, "incompatible")
        history = a.oracle
        history.metadata = {
            let m = try original()
            return Metadata(name: m.name, modelVersion: m.modelVersion, propertiesVersion: m.propertiesVersion, codecVersion: m.codecVersion, build: "new-build")
        }
        let changedBuild = WithOracle(a.actual, oracle: history).model
        XCTAssertEqual(try replay(changedBuild, trace: trace).outcome, "incompatible")
        XCTAssertEqual(try replay(changedBuild, trace: trace, allowBuildMismatch: true).outcome, "exact")
    }
    func testUnsupportedCapabilitiesAndCallbackErrors() throws {
        var actual = queue().actual
        actual.codec = nil; actual.inputs = nil; actual.equalStates = nil
        let a = WithOracle(actual, oracle: queue().oracle)
        XCTAssertNil(a.model.codec); XCTAssertNil(a.model.inputs); XCTAssertNil(a.model.equalStates)
        XCTAssertThrowsError(try a.metadata()); XCTAssertThrowsError(try a.encodeState(a.model.initialState()))
        XCTAssertThrowsError(try enumerateStates(a.model))
        var failing = queue().oracle
        failing.checkState = { _, _ in throw ModelError("check callback failed") }
        failing.checkStateInto = nil // Explicitly select the legacy hook for this regression.
        XCTAssertThrowsError(try enumerateStates(WithOracle(queue().actual, oracle: failing).model))
        failing = queue().oracle
        failing.advance = { _, _, _, _ in throw ModelError("advance callback failed") }
        let trace = try Statelessness.record(WithOracle(queue().actual, oracle: failing).model, inputs: InputIterator(values: [0]))
        XCTAssertEqual(trace.termination, .modelError); XCTAssertEqual(trace.steps.count, 0)
    }
}

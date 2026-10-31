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
    func testObservationCapturesInputAndHistoryBeforeTransitionClone() throws {
        final class Box { var value: Int; init(_ value: Int) { self.value = value } }
        let input = Box(2), history = Box(3)
        var observed: [Int] = []
        let actual = Model<Int, Box, Int>(initialState: { 0 }, step: { s, _ in Transition(state: s) }, checkState: { _ in [] }, cloneState: { s in input.value = 20; history.value = 30; return s }, cloneInput: { Box($0.value) }, cloneOutput: { $0 })
        let oracle = Oracle<Int, Box, Box, Int>(metadata: { Metadata(name: "history") }, initialState: { Box(0) }, advance: { h, i, _, _ in observed = [h.value, i.value]; return h }, checkState: { _, _ in [] }, cloneHistory: { Box($0.value) })
        let adapter = WithOracle(actual, oracle: oracle)
        _ = try adapter.observeTransition(before: OracleState(model: 0, oracle: history), input: input, transition: Transition(state: 1))
        XCTAssertEqual(observed, [3, 2])
    }
    func testObservationInputCopyFailureRetainsCompletedTransition() throws {
        final class Box { var value: Int; init(_ value: Int) { self.value = value } }
        let state = Box(4)
        let actual = Model<Box, Int, Int>(initialState: { Box(0) }, step: { s, _ in Transition(state: s) }, checkState: { _ in [] }, cloneState: { Box($0.value) }, cloneInput: { _ in throw ModelError("input copy") }, cloneOutput: { $0 })
        let oracle = Oracle<Box, Int, Int, Int>(metadata: { Metadata(name: "history") }, initialState: { 0 }, advance: { h, _, _, _ in h }, checkState: { _, _ in [] }, cloneHistory: { $0 })
        do {
            _ = try WithOracle(actual, oracle: oracle).observeTransition(before: OracleState(model: Box(0), oracle: 0), input: 1, transition: Transition(state: state, outputs: [7]))
            XCTFail("expected observation failure")
        } catch let error as ObservationError<Box, Int> {
            state.value = 40
            XCTAssertEqual(error.transition.state.value, 4)
            XCTAssertEqual(error.transition.outputs, [7])
        }
    }
    func testGeneratorEnabledCapturesInputBeforeStateCopy() throws {
        final class Box { var value: Int; init(_ value: Int) { self.value = value } }
        let input = Box(3)
        let actual = Model<Int, Box, Int>(initialState: { 0 }, step: { state, _ in Transition(state: state) }, checkState: { _ in [] }, cloneState: { state in input.value = 30; return state }, cloneInput: { Box($0.value) }, cloneOutput: { $0 })
        let oracle = Oracle<Int, Int, Box, Int>(metadata: { Metadata(name: "oracle") }, initialState: { 0 }, advance: { history, _, _, _ in history }, checkState: { _, _ in [] }, cloneHistory: { $0 })
        let generator = Generate(model: actual, generate: { _, _ in nil }, isEnabled: { _, value in value.value == 3 }, equalInputs: { $0.value == $1.value }).withOracle(oracle)
        XCTAssertTrue(try generator.isEnabled(OracleState(model: 0, oracle: 0), input))
        XCTAssertEqual(input.value, 30)
    }
    func testStateEncodingCapturesHistoryBeforeModelEncoder() throws {
        final class Box { var value: UInt8; init(_ value: UInt8) { self.value = value } }
        let history = Box(7)
        var actual = Model<Int, Int, Int>(initialState: { 0 }, step: { s, _ in Transition(state: s) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 })
        actual.codec = Codec(metadata: { Metadata(name: "actual") }, encodeState: { _ in history.value = 99; return [1] }, decodeState: { _ in 0 }, encodeInput: { _ in [] }, decodeInput: { _ in 0 }, encodeOutput: { _ in [] })
        let oracle = Oracle<Int, Box, Int, Int>(metadata: { Metadata(name: "oracle") }, initialState: { Box(0) }, advance: { h, _, _, _ in h }, checkState: { _, _ in [] }, cloneHistory: { Box($0.value) }, encodeHistory: { [$0.value] }, decodeHistory: { Box($0.first ?? 0) })
        let bytes = try WithOracle(actual, oracle: oracle).encodeState(OracleState(model: 0, oracle: history))
        XCTAssertEqual(bytes.last, 7)
        XCTAssertEqual(history.value, 99)
    }

    func testStateEncodingPreservesModelFailureBeforeHistoryCaptureFailure() throws {
        var actual = Model<Int, Int, Int>(initialState: { 0 }, step: { s, _ in Transition(state: s) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 })
        actual.codec = Codec(metadata: { Metadata(name: "actual") }, encodeState: { _ in throw ModelError("model encode") }, decodeState: { _ in 0 }, encodeInput: { _ in [] }, decodeInput: { _ in 0 }, encodeOutput: { _ in [] })
        let oracle = Oracle<Int, Int, Int, Int>(metadata: { Metadata(name: "oracle") }, initialState: { 0 }, advance: { h, _, _, _ in h }, checkState: { _, _ in [] }, cloneHistory: { _ in throw ModelError("history clone") }, encodeHistory: { _ in [] }, decodeHistory: { _ in 0 })
        XCTAssertThrowsError(try WithOracle(actual, oracle: oracle).encodeState(OracleState(model: 0, oracle: 0))) { error in
            XCTAssertTrue(String(describing: error).contains("model encode"))
        }
    }

    func testStateEncodingPreservesHistoryFrameCapacityBeforeCaptureFailure() throws {
        var actual = Model<Int, Int, Int>(initialState: { 0 }, step: { s, _ in Transition(state: s) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 })
        actual.codec = Codec(metadata: { Metadata(name: "actual") }, encodeState: { _ in [1] }, decodeState: { _ in 0 }, encodeInput: { _ in [] }, decodeInput: { _ in 0 }, encodeOutput: { _ in [] })
        let oracle = Oracle<Int, Int, Int, Int>(metadata: { Metadata(name: "oracle") }, initialState: { 0 }, advance: { h, _, _, _ in h }, checkState: { _, _ in [] }, cloneHistory: { _ in throw ModelError("history clone") }, encodeHistory: { _ in [] }, decodeHistory: { _ in 0 })
        var bytes: [UInt8] = []
        XCTAssertThrowsError(try EncodeBuffer.withBuffer(&bytes, maximum: 13) { sink in
            try WithOracle(actual, oracle: oracle).encodeStateInto(OracleState(model: 0, oracle: 0), sink)
        }) { error in XCTAssertTrue(String(describing: error).contains("byte limit")) }
    }
    func testPairedCheckFailurePrecedesCapturedCopyFailure() throws {
        var copies = 0
        let reference = Model<Int, Int, Int>(initialState: { 0 }, step: { s, _ in Transition(state: s) }, checkState: { _ in throw ModelError("reference check failure") }, cloneState: { value in
            copies += 1
            if copies == 1 { throw ModelError("capture failure") }
            return value
        }, cloneInput: { $0 }, cloneOutput: { $0 })
        let projection = Projection<Int, Int, Int, Int, Int, Int>(metadata: { Metadata(name: "projection") }, actualState: { $0 }, referenceState: { $0 }, actualEffect: { $0 }, referenceEffect: { $0 }, equalState: { $0 == $1 }, equalEffect: { $0 == $1 })
        let oracle = Paired(reference: reference, projection: projection).oracle
        XCTAssertThrowsError(try oracle.checkState(0, 0)) { error in
            XCTAssertTrue(String(describing: error).contains("reference check failure"))
        }
    }
    func testPairedCapturesReferenceBeforeCheckCallbacks() throws {
        final class Box { var value: Int; init(_ value: Int) { self.value = value } }
        let history = Box(7)
        var expectedOutput: Box?
        var outputCopies = 0
        let reference = Model<Box, Int, Box>(initialState: { Box(0) }, step: { _, _ in Transition(state: Box(0), outputs: [Box(3)]) },
            checkState: { _ in history.value = 99; return [] }, cloneState: { Box($0.value) }, cloneInput: { $0 }, cloneOutput: { value in
                let copy = Box(value.value); outputCopies += 1
                if outputCopies == 1 { expectedOutput = copy }
                return copy
            }, checkTransition: { _, _, _ in expectedOutput?.value = 88; return [] })
        let projection = Projection<Int, Box, Int, Box, Int, Int>(metadata: { Metadata(name: "projection") }, actualState: { $0 }, referenceState: { $0.value }, actualEffect: { $0 }, referenceEffect: { $0.value }, equalState: { $0 == $1 }, equalEffect: { $0 == $1 })
        let oracle = Paired(reference: reference, projection: projection).oracle
        XCTAssertEqual(try oracle.checkState(history, 7).last?.status, .passed)
        let turn = Transition(state: OracleState(model: 0, oracle: history), outputs: [3])
        let checks = try XCTUnwrap(oracle.checkTransition)(OracleState(model: 0, oracle: history), 0, turn)
        XCTAssertEqual(checks.first(where: { $0.id == "pair.effects" })?.status, .passed)
        XCTAssertEqual(expectedOutput?.value, 88)
    }
    func testEstimateCapturesHistoryBeforeActualEstimator() throws {
        final class Box { var value: UInt64; init(_ value: UInt64) { self.value = value } }
        let history = Box(7)
        var actual = Model<Int, Int, Int>(initialState: { 0 }, step: { s, _ in Transition(state: s) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 })
        actual.estimatedStateBytes = { _ in history.value = 90; return 3 }
        let oracle = Oracle<Int, Box, Int, Int>(metadata: { Metadata(name: "oracle") }, initialState: { Box(0) }, advance: { h, _, _, _ in h }, checkState: { _, _ in [] }, cloneHistory: { Box($0.value) }, estimatedStateBytes: { $0.value })
        let wrapper = WithOracle(actual, oracle: oracle)
        let estimate = try XCTUnwrap(wrapper.model.estimatedStateBytes)
        XCTAssertEqual(try estimate(OracleState(model: 0, oracle: history)), 10 + (wrapper.estimatedWrapperBytes ?? 0))
        XCTAssertEqual(history.value, 90)
    }

    func testEstimatePreservesActualUnknownAndErrorPrecedence() throws {
        var actual = Model<Int, Int, Int>(initialState: { 0 }, step: { s, _ in Transition(state: s) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 })
        let oracle = Oracle<Int, Int, Int, Int>(metadata: { Metadata(name: "oracle") }, initialState: { 0 }, advance: { h, _, _, _ in h }, checkState: { _, _ in [] }, cloneHistory: { _ in throw ModelError("history clone") }, estimatedStateBytes: { _ in 1 })
        actual.estimatedStateBytes = { _ in nil }
        XCTAssertNil(try XCTUnwrap(WithOracle(actual, oracle: oracle).model.estimatedStateBytes)(OracleState(model: 0, oracle: 0)))
        actual.estimatedStateBytes = { _ in throw ModelError("actual estimate") }
        XCTAssertThrowsError(try XCTUnwrap(WithOracle(actual, oracle: oracle).model.estimatedStateBytes)(OracleState(model: 0, oracle: 0))) { XCTAssertTrue(String(describing: $0).contains("actual estimate")) }
        actual.estimatedStateBytes = { _ in 2 }
        XCTAssertThrowsError(try XCTUnwrap(WithOracle(actual, oracle: oracle).model.estimatedStateBytes)(OracleState(model: 0, oracle: 0))) { XCTAssertTrue(String(describing: $0).contains("history clone")) }
        var copyCount = 0
        let noHistoryEstimate = Oracle<Int, Int, Int, Int>(metadata: { Metadata(name: "oracle") }, initialState: { 0 }, advance: { h, _, _, _ in h }, checkState: { _, _ in [] }, cloneHistory: { h in copyCount += 1; return h })
        XCTAssertNil(try XCTUnwrap(WithOracle(actual, oracle: noHistoryEstimate).model.estimatedStateBytes)(OracleState(model: 0, oracle: 0)))
        XCTAssertEqual(copyCount, 0)
    }
    func testEqualityCapturesHistoryBeforeActualComparator() throws {
        final class Box { var value: Int; init(_ value: Int) { self.value = value } }
        let left = Box(1), right = Box(1)
        let actual = Model<Int, Int, Int>(initialState: { 0 }, step: { s, _ in Transition(state: s) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, equalStates: { _, _ in right.value = 2; return true })
        let oracle = Oracle<Int, Box, Int, Int>(metadata: { Metadata(name: "oracle") }, initialState: { Box(0) }, advance: { h, _, _, _ in h }, checkState: { _, _ in [] }, cloneHistory: { Box($0.value) }, equalHistory: { $0.value == $1.value })
        let equal = try XCTUnwrap(WithOracle(actual, oracle: oracle).model.equalStates)
        XCTAssertTrue(try equal(OracleState(model: 0, oracle: left), OracleState(model: 0, oracle: right)))
        XCTAssertEqual(right.value, 2)
    }
    func testEqualityDefersUnusedHistoryCopyFailure() throws {
        var actual = Model<Int, Int, Int>(initialState: { 0 }, step: { s, _ in Transition(state: s) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, equalStates: { $0 == $1 })
        let oracle = Oracle<Int, Int, Int, Int>(metadata: { Metadata(name: "oracle") }, initialState: { 0 }, advance: { h, _, _, _ in h }, checkState: { _, _ in [] }, cloneHistory: { _ in throw ModelError("history copy") }, equalHistory: { $0 == $1 })
        let a = OracleState(model: 0, oracle: 1), b = OracleState(model: 1, oracle: 2)
        XCTAssertFalse(try XCTUnwrap(WithOracle(actual, oracle: oracle).model.equalStates)(a, b))
        actual.equalStates = { _, _ in throw ModelError("actual comparison") }
        XCTAssertThrowsError(try XCTUnwrap(WithOracle(actual, oracle: oracle).model.equalStates)(a, b)) { XCTAssertTrue(String(describing: $0).contains("actual comparison")) }
        actual.equalStates = { _, _ in true }
        XCTAssertThrowsError(try XCTUnwrap(WithOracle(actual, oracle: oracle).model.equalStates)(a, b)) { XCTAssertTrue(String(describing: $0).contains("history copy")) }
    }
    func testStepCapturesOracleInputAndHistoryBeforeReducer() throws {
        final class Box { var value: Int; init(_ value: Int) { self.value = value } }
        let history = Box(2), input = Box(3)
        var seen: [Int] = []
        let actual = Model<Int, Box, Int>(initialState: { 0 }, step: { state, _ in
            history.value = 20; input.value = 30
            return Transition(state: state + 1)
        }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { Box($0.value) }, cloneOutput: { $0 })
        let oracle = Oracle<Int, Box, Box, Int>(metadata: { Metadata(name: "oracle") }, initialState: { Box(0) }, advance: { h, i, _, _ in
            seen = [h.value, i.value]; return h
        }, checkState: { _, _ in [] }, cloneHistory: { Box($0.value) })
        let result = try WithOracle(actual, oracle: oracle).model.step(OracleState(model: 1, oracle: history), input)
        XCTAssertEqual(seen, [2, 3]); XCTAssertEqual(result.state.oracle.value, 2)
    }
    func testTransitionWithoutOracleCheckDoesNotCloneHistory() throws {
        var cloned = 0, checked = 0
        let actual = Model<Int, Int, Int>(initialState: { 0 }, step: { s, _ in Transition(state: s) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, checkTransition: { _, _, _ in checked += 1; return [] })
        let oracle = Oracle<Int, Int, Int, Int>(metadata: { Metadata(name: "oracle") }, initialState: { 0 }, advance: { h, _, _, _ in h }, checkState: { _, _ in [] }, cloneHistory: { _ in cloned += 1; throw ModelError("unexpected clone") })
        let model = WithOracle(actual, oracle: oracle).model
        let before = OracleState(model: 1, oracle: 2)
        _ = try model.checkTransition!(before, 3, Transition(state: OracleState(model: 4, oracle: 5), outputs: [6]))
        XCTAssertEqual(checked, 1); XCTAssertEqual(cloned, 0)
    }
    func testChecksCaptureObservationsBeforeChildCallbacks() throws {
        final class Box { var value: Int; init(_ value: Int) { self.value = value } }
        let before = Box(1), history = Box(2), input = Box(3), after = Box(4), afterHistory = Box(5), output = Box(6)
        var seenState: [Int] = [], seenTransition: [Int] = []
        let actual = Model<Box, Box, Box>(initialState: { Box(0) }, step: { s, _ in Transition(state: s) }, checkState: { _ in
            before.value = 11; history.value = 12; return []
        }, cloneState: { Box($0.value) }, cloneInput: { Box($0.value) }, cloneOutput: { Box($0.value) }, checkTransition: { _, _, _ in
            before.value = 11; history.value = 12; input.value = 13; after.value = 14; afterHistory.value = 15; output.value = 16; return []
        })
        let oracle = Oracle<Box, Box, Box, Box>(metadata: { Metadata(name: "oracle") }, initialState: { Box(0) }, advance: { h, _, _, _ in h }, checkState: { h, s in
            seenState = [s.value, h.value]; return []
        }, cloneHistory: { Box($0.value) }, checkTransition: { b, i, t in
            seenTransition = [b.model.value, b.oracle.value, i.value, t.state.model.value, t.state.oracle.value, t.outputs[0].value]; return []
        })
        let model = WithOracle(actual, oracle: oracle).model
        let state = OracleState(model: before, oracle: history)
        _ = try model.checkState(state)
        XCTAssertEqual(seenState, [1, 2])
        before.value = 1; history.value = 2
        let transition = Transition(state: OracleState(model: after, oracle: afterHistory), outputs: [output])
        _ = try model.checkTransition!(state, input, transition)
        XCTAssertEqual(seenTransition, [1, 2, 3, 4, 5, 6])
    }
    func testModelSnapshotsPrecedeOracleCallbacks() throws {
        final class Box { var value: Int; init(_ value: Int) { self.value = value } }
        let shared = Box(1)
        let codec = Codec<Box, Int, Int>(metadata: { Metadata(name: "actual") }, encodeState: { [UInt8($0.value)] }, decodeState: { _ in shared }, encodeInput: { [UInt8($0)] }, decodeInput: { Int($0[0]) }, encodeOutput: { [UInt8($0)] })
        let actual = Model<Box, Int, Int>(initialState: { shared }, step: { _, _ in Transition(state: shared) }, checkState: { _ in [] }, cloneState: { Box($0.value) }, cloneInput: { $0 }, cloneOutput: { $0 }, codec: codec)
        let oracle = Oracle<Box, Int, Int, Int>(metadata: { Metadata(name: "oracle") }, initialState: { shared.value = 9; return 0 }, advance: { h, _, _, _ in shared.value = 9; return h }, checkState: { _, _ in [] }, cloneHistory: { $0 }, encodeHistory: { [UInt8($0)] }, decodeHistory: { _ in shared.value = 9; return 0 })
        let adapter = WithOracle(actual, oracle: oracle)
        XCTAssertEqual(try adapter.model.initialState().model.value, 1)
        shared.value = 2
        let bytes = try adapter.encodeState(adapter.attach(model: shared, oracle: 0))
        XCTAssertEqual(try adapter.decodeState(bytes).model.value, 2)
        shared.value = 3
        let before = try adapter.attach(model: Box(0), oracle: 0)
        let observed = try adapter.observeTransition(before: before, input: 0, transition: Transition(state: shared))
        XCTAssertEqual(observed.state.model.value, 3)
    }
    func testObservationSnapshotsInputBeforeHistoryClone() throws {
        final class Box { var value: Int; init(_ value: Int) { self.value = value } }
        let input = Box(2)
        var observed = -1
        let actual = Model<Int, Box, Int>(initialState: { 0 }, step: { state, _ in Transition(state: state) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { Box($0.value) }, cloneOutput: { $0 })
        let oracle = Oracle<Int, Int, Box, Int>(metadata: { Metadata(name: "oracle") }, initialState: { 0 }, advance: { history, value, _, _ in observed = value.value; return history }, checkState: { _, _ in [] }, cloneHistory: { history in input.value = 9; return history })
        let adapter = WithOracle(actual, oracle: oracle)
        let before = try adapter.attach(model: 0, oracle: 0)
        input.value = 2
        _ = try adapter.observeTransition(before: before, input: input, transition: Transition(state: 0))
        XCTAssertEqual(observed, 2)
    }
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

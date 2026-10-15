import XCTest
@testable import StatelessNativeV2

final class NativeV2Tests: XCTestCase {
    private func counter(failAt: Int = 3) -> Model<Int, Int, Int> {
        let codec = Codec<Int, Int, Int>(metadata: { Metadata(name: "swift-v2-counter", build: "fixture") },
            encodeState: { [UInt8($0)] }, decodeState: { Int($0[0]) },
            encodeInput: { [UInt8($0)] }, decodeInput: { Int($0[0]) }, encodeOutput: { [UInt8($0)] })
        return Model(initialState: { 0 }, step: { state, input in Transition(state: state + input, outputs: [state + input]) },
            checkState: { [.init("bound", $0 < failAt ? .passed : .failed, $0 < failAt ? "" : "too high")] },
            cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
            checkTransition: { _, _, _ in [.skipped("transition", "fixture")] },
            inputs: { _ in InputIterator(values: [1]) }, equalStates: { $0 == $1 }, hashState: { _ in 0 }, codec: codec)
    }

    func testObservedDoesNotStepAndPreservesExactUTF8() throws {
        var model = counter()
        model.step = { _, _ in XCTFail("observations must not step"); throw ModelError("unexpected step") }
        model.checkState = { _ in [.passed(""), .failed("e\u{301}", "left\0right"), .skipped("é", "理由"), .skipped("\u{feff}id", "\u{feff}details")] }
        model.checkTransition = { _, _, transition in
            XCTAssertEqual(transition.disposition, .rejected("\u{feff}reason"))
            return [.passed("transition")]
        }
        let session = try NativeSession(model); defer { try? session.close() }
        let checks = try session.checkObserved(before: 0, input: 1, transition: Transition(state: 1, disposition: .rejected("\u{feff}reason")), sequence: 1)
        XCTAssertEqual(checks.count, 5)
        XCTAssertEqual(Array(checks[1].id.utf8), Array("e\u{301}".utf8))
        XCTAssertEqual(checks[1].details, "left\0right")
        XCTAssertFalse(exactText(checks[1].id, checks[2].id))
        XCTAssertTrue(exactText(checks[3].id, "\u{feff}id"))
        XCTAssertTrue(exactText(checks[3].details, "\u{feff}details"))
    }

    func testCollisionSafeBFSAndOwnedWitness() throws {
        let session = try NativeSession(counter())
        let result = try session.enumerate()
        XCTAssertEqual(result.status, 1)
        XCTAssertEqual(try result.scalar(.termination), 4)
        let inputs = try result.inputs()
        XCTAssertEqual(inputs, [1, 1, 1])
        try result.close(); try session.close()
        XCTAssertEqual(inputs, [1, 1, 1])
        XCTAssertThrowsError(try result.scalar(.states))
    }

    func testRecordReplayPortableAndThrowingCursorPrefix() throws {
        let session = try NativeSession(counter()); defer { try? session.close() }
        let trace = try session.record(inputs: InputIterator(values: [1, 1, 1]))
        XCTAssertEqual(trace.termination, .propertyFailed)
        let imported = try parseTrace(encodeTrace(trace))
        let replay = try session.replay(imported)
        XCTAssertEqual(replay.outcome, "exact")
        XCTAssertTrue(replay.failureReproduced)
        var next = 0
        let prefix = try session.record(inputs: InputIterator {
            next += 1
            if next == 2 { throw ModelError("cursor failed") }
            return 1
        })
        XCTAssertEqual(prefix.termination, .modelError)
        XCTAssertEqual(prefix.steps.count, 1)
        XCTAssertTrue(prefix.error.contains("cursor failed"))
    }

    func testThrowingCheckIsAnErrorAndSessionRemainsUsable() throws {
        var model = counter()
        var throwsNow = true
        model.checkState = { _ in if throwsNow { throw ModelError("host check failed") }; return [.passed("ok")] }
        let session = try NativeSession(model); defer { try? session.close() }
        XCTAssertThrowsError(try session.checkInitial(0)) { error in
            XCTAssertEqual((error as? NativeError)?.status, 11)
            XCTAssertTrue(String(describing: error).contains("host check failed"))
        }
        throwsNow = false
        XCTAssertEqual(try session.checkInitial(0), [.passed("ok")])
    }

    func testMutableRootsAreIsolatedAndDisposed() throws {
        final class Box { var value: Int; init(_ value: Int) { self.value = value } }
        weak var returned: Box?
        let model = Model<Box, Box, Box>(initialState: { Box(0) },
            step: { state, input in state.value += input.value; returned = state; return Transition(state: state) },
            checkState: { state in let value = state.value; state.value = 999; return [.passed("value-\(value)")] },
            cloneState: { Box($0.value) }, cloneInput: { Box($0.value) }, cloneOutput: { Box($0.value) },
            inputs: { _ in InputIterator(values: [Box(1)]) }, equalStates: { $0.value == $1.value })
        let session = try NativeSession(model)
        let input = Box(1), before = Box(0), after = Box(1)
        _ = try session.checkObserved(before: before, input: input, transition: Transition(state: after), sequence: 1)
        XCTAssertEqual(before.value, 0); XCTAssertEqual(input.value, 1); XCTAssertEqual(after.value, 1)
        let result = try session.enumerate(maxStates: 2)
        try result.close(); try session.close()
        XCTAssertNil(returned)
    }

    func testReentryIsRejectedWithoutInvalidatingOuterCall() throws {
        var model = counter()
        var session: NativeSession<Int, Int, Int>!
        model.checkState = { _ in
            XCTAssertThrowsError(try session.checkInitial(0)) { XCTAssertEqual(($0 as? NativeError)?.status, 16) }
            return [.passed("outer")]
        }
        session = try NativeSession(model)
        XCTAssertEqual(try session.checkInitial(0), [.passed("outer")])
        try session.close(); session = nil
    }

    func testFuzzShrinkAndRingUseRustOperations() throws {
        let native = NativeModel(counter(), generate: { _, rng in _ = try rng.next(); return 1 },
                                 equalInputs: { $0 == $1 }, simplerInputs: { _ in InputIterator(values: []) })
        let session = try NativeSession(native); defer { try? session.close() }
        let failure = try session.fuzz(seed: 7, cases: 2, maxSteps: 4); defer { try? failure.close() }
        XCTAssertEqual(failure.status, 1)
        let minimized = try session.shrink(failure); defer { try? minimized.close() }
        XCTAssertEqual(try minimized.inputs(), [1, 1, 1])
        let ring = try session.ring(initialState: 0, maxSteps: 2); defer { try? ring.close() }
        _ = try ring.observe(before: 0, input: 1, transition: Transition(state: 1, outputs: [1]))
        let snapshot = try ring.snapshot(); defer { try? snapshot.close() }
        let trace = try session.portableTrace(artifact: snapshot.bytes())
        XCTAssertEqual(trace.steps.count, 1)
    }

    func testRngProfileUsesEngine() throws {
        var rng = NativeRng(seed: 0)
        XCTAssertEqual(try rng.next(), 0xe220a8397b1dcdaf)
        let state = rng.state
        XCTAssertNil(try rng.index(upper: 0))
        XCTAssertEqual(rng.state, state)
    }

    func testNeutralPacketRejectsTruncationTrailingDataAndBounds() throws {
        let trace = Trace(metadata: Metadata(name: "\u{feff}é", build: "\u{feff}e\u{301}"), initialState: [],
                          initialChecks: [.passed(""), .skipped("\u{feff}id", "\u{feff}reason")], steps: [], termination: .completed)
        let bytes = try encodePacket(trace)
        XCTAssertEqual(try decodePacket(bytes), trace)
        XCTAssertThrowsError(try decodePacket(Array(bytes.dropLast())))
        XCTAssertThrowsError(try decodePacket(bytes + [0]))
        var limits = TraceLimits(); limits.maxItems = 0
        XCTAssertThrowsError(try decodePacket(bytes, limits: limits))
    }

    func testDiscardedProvenanceDoesNotConsumePortableItemBudget() throws {
        let trace = Trace(metadata: Metadata(name: "m", build: "b"), initialState: [],
                          initialChecks: [.passed("ok")], steps: [], termination: .completed)
        let packet = try encodePacket(trace)
        // Magic, two length-prefixed metadata strings, versions, "manual", seed.
        let offset = 8 + 5 + 5 + 12 + 10 + 1
        XCTAssertEqual(Array(packet[offset..<(offset + 4)]), [0, 0, 0, 0])
        let label: [UInt8] = [1, 0, 0, 0, 107, 1, 0, 0, 0, 118]
        let enriched = Array(packet.prefix(offset)) + [2, 0, 0, 0] + label + label + Array(packet.dropFirst(offset + 4))
        var limits = TraceLimits(); limits.maxItems = 1
        XCTAssertEqual(try decodePacket(enriched, limits: limits), trace)
    }
}

import XCTest
import Statelessness

private func model() -> Model<Int, Int, Int> {
    Model(initialState: { 0 },
          step: { s, i in Transition(state: s + i) },
          checkState: { _ in [] },
          cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
          codec: Codec(metadata: { Metadata(name: "clone-errors", build: "test-v1") },
                       encodeState: { [UInt8($0)] }, decodeState: { Int($0[0]) },
                       encodeInput: { [UInt8($0)] }, decodeInput: { Int($0[0]) },
                       encodeOutput: { [UInt8($0)] }))
}

final class RecordingErrorTests: XCTestCase {
    func testInitialCloneErrorHasNoTrace() throws {
        var m = model(); m.cloneState = { _ in throw ModelError("clone failed") }
        XCTAssertThrowsError(try Statelessness.record(m, inputs: InputIterator(values: []))) {
            XCTAssertTrue(String(describing: $0).contains("snapshot: clone failed"))
        }
    }
    func testLaterCloneErrorRetainsOnlyCoherentPrefix() throws {
        var m = model()
        m.cloneState = { state in
            if state == 2 { throw ModelError("later clone failed") }
            return state
        }
        let t = try Statelessness.record(m, inputs: InputIterator(values: [1, 1]))
        XCTAssertEqual(t.termination, .modelError)
        XCTAssertEqual(t.steps.count, 1)
        XCTAssertTrue(t.error.contains("later clone failed"))
        XCTAssertEqual(try replay(model(), trace: t).outcome, "exact")
    }
}

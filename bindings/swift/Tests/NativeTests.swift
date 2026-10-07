import XCTest
@testable import StatelessNative

private struct Counter: ByteModel {
    var changed = false
    var broken = false
    var build = "test"
    var metadata: Metadata { Metadata(name: "native-tests", build: build) }
    func initial() throws -> [UInt8] { [0] }
    func step(state: [UInt8], input: [UInt8]) throws -> Transition {
        guard !broken, state.count == 1, input == [1], state[0] < 200 else { throw BindingError("bad callback") }
        return Transition(state: [state[0] + (changed ? 2 : 1)], outputs: [input])
    }
    func checkState(_ state: [UInt8]) throws -> [Check] {
        guard state.count == 1 else { throw BindingError("bad state") }
        return [Check("bound", state[0] <= 2 ? .passed : .failed)]
    }
    func checkTransition(before: [UInt8], input: [UInt8], transition: Transition) throws -> [Check] {
        [Check("output", transition.outputs == [input] ? .passed : .failed)]
    }
    func inputs(state: [UInt8]) throws -> [[UInt8]] { [[1]] }
}

final class NativeTests: XCTestCase {
    func testRecordFreshReplayDivergenceIdentityAndCorruption() throws {
        let result = try Session(Counter()).record([[1], [1], [1]])
        XCTAssertEqual(result.status, 1)
        XCTAssertEqual(try Session(Counter()).replay(result.artifact).status, 1)
        XCTAssertEqual(try Session(Counter(changed: true)).replay(result.artifact).status, 2)
        XCTAssertEqual(try Session(Counter(build: "other")).replay(result.artifact).status, 3)
        XCTAssertEqual(try Session(Counter()).replay(Array(result.artifact.dropLast())).status, 12)
    }
    func testEnumerationAndLimits() throws {
        let session = try Session(Counter())
        let result = try session.enumerate()
        XCTAssertEqual(result.status, 1)
        XCTAssertEqual(try session.replay(result.artifact).status, 1)
        let bounded = try session.enumerate(maxDepth: 1)
        XCTAssertEqual(bounded.status, 0)
        XCTAssertTrue(bounded.report.contains("DepthBound"))
        XCTAssertTrue(bounded.artifact.isEmpty)
        XCTAssertThrowsError(try session.record([[1]], maxSteps: -1))
    }
    func testCallbackErrorIsContained() throws {
        XCTAssertThrowsError(try Session(Counter(broken: true)).record([[1]])) {
            XCTAssertTrue(String(describing: $0).contains("bad callback"))
        }
    }
}

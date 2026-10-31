import XCTest
@testable import StatelessDebug

final class MarkerTests: XCTestCase {
    func testLazyDisabledFullAndFinished() throws {
        var calls = 0
        let disabled = MarkerCollector.disabled()
        disabled.marker("site") { calls += 1; return "ignored" }
        XCTAssertEqual(calls, 0)
        XCTAssertEqual(try disabled.finish().markers, [])
        let collector = try MarkerCollector(enabled: true, maxRecords: 1, maxBytes: 1024)
        collector.marker("site") { calls += 1; return "value" }
        collector.marker("site") { calls += 1; return "ignored" }
        let batch = try collector.finish()
        collector.marker("site") { calls += 1; return "ignored" }
        XCTAssertEqual(calls, 1)
        XCTAssertEqual(batch.markers, [DiagnosticMarker(id: "site", detail: "value")])
        XCTAssertEqual(batch.dropped, 1)
        XCTAssertFalse(collector.interested)
    }
    func testUTF8TruncationAndTooLargeIDDoNotRunDetail() throws {
        let collector = try MarkerCollector(enabled: true, maxRecords: 3, maxBytes: MarkerCollector.markerOverhead + 1 + 3)
        var calls = 0
        collector.marker("x") { calls += 1; return "é🙂" }
        let result = try collector.finish()
        XCTAssertEqual(result.markers[0].detail, "é")
        XCTAssertEqual(result.retainedBytes, MarkerCollector.markerOverhead + 3)
        XCTAssertEqual(result.truncated, 1)
        let small = try MarkerCollector(enabled: true, maxRecords: 1, maxBytes: MarkerCollector.markerOverhead)
        small.marker("x") { calls += 1; return "unreachable" }
        XCTAssertEqual(calls, 1)
        XCTAssertEqual(try small.finish().dropped, 1)
        XCTAssertThrowsError(try MarkerCollector(enabled: true, maxRecords: -1, maxBytes: 1))
    }
    func testReentrantMarkerIsDroppedWithoutExceedingBudget() throws {
        let collector = try MarkerCollector(enabled: true, maxRecords: 1, maxBytes: 128)
        collector.marker("outer") {
            collector.marker("inner") { XCTFail("reentrant callback"); return "" }
            do { _ = try collector.finish(); XCTFail("reentrant finish succeeded") }
            catch DebugError.reentrant {}
            catch { XCTFail("unexpected error: \(error)") }
            return "ok"
        }
        let result = try collector.finish()
        XCTAssertEqual(result.markers.map(\.id), ["outer"])
        XCTAssertEqual(result.dropped, 1)
        XCTAssertLessThanOrEqual(result.retainedBytes, 128)
    }
}

import XCTest
@testable import Statelessness

final class ValueCodecDecoderTests: XCTestCase {
    private func error(_ message: String, _ body: () throws -> Void, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(try body(), file: file, line: line) { caught in
            XCTAssertEqual(String(describing: caught), message, file: file, line: line)
        }
    }
    func testAdmissionOwnedSlicesAndReusableFinish() throws {
        error("decode byte limit") { _ = try ValueDecoder([1,2], limits: DecodeLimits(bytes: 1)) }
        var input: [UInt8] = [1,2,3]
        let decoder = try ValueDecoder(input)
        input[0] = 99
        var first = try decoder.take(1)
        XCTAssertEqual(Array(first), [1]); first[first.startIndex] = 88
        error("trailing value bytes") { try decoder.finish() }
        let remaining = try decoder.take(2)
        XCTAssertEqual(Array(remaining), [2,3]); XCTAssertEqual(remaining.startIndex, 1)
        try decoder.finish(); try decoder.finish()
        XCTAssertTrue(try decoder.take(0).isEmpty)
        error("truncated value") { _ = try decoder.take(1) }
        error("truncated value") { try decoder.finish() }
        error("truncated value") { _ = try decoder.take(0) }
        XCTAssertEqual(input, [99,2,3]); XCTAssertEqual(Array(first), [88])
    }
    func testRawTakeHasNoWorkOrDepthChargeAndHugeCountsCannotNarrow() throws {
        let raw = try ValueDecoder([1], limits: DecodeLimits(depth: 0, work: 0))
        XCTAssertEqual(Array(try raw.take(1)), [1]); try raw.finish()
        let huge = try ValueDecoder([1])
        error("truncated value") { _ = try huge.take(UInt64.max) }
        error("truncated value") { _ = try huge.value { _ in 7 } }
    }
    func testGuardedFirstFailurePrecedenceAndNestedDepthRestoration() throws {
        let workFirst = try ValueDecoder([], limits: DecodeLimits(depth: 0, work: 0))
        var invoked = false
        error("decode work limit") { _ = try workFirst.value { _ in invoked = true; return 0 } }
        XCTAssertFalse(invoked)
        let depth = try ValueDecoder([], limits: DecodeLimits(depth: 1, work: 10))
        error("decode nesting limit") {
            _ = try depth.value { nested in
                do { _ = try nested.value { _ in invoked = true; return 1 } } catch {}
                throw ModelError("later callback error")
            } as Int
        }
        XCTAssertFalse(invoked)
        error("decode nesting limit") { try depth.finish() }
        let reusable = try ValueDecoder([], limits: DecodeLimits(depth: 1, work: 2))
        XCTAssertEqual(try reusable.value { _ in 1 }, 1)
        XCTAssertEqual(try reusable.value { _ in 2 }, 2)
        error("decode work limit") { _ = try reusable.value { _ in 3 } }
        let swallowed = try ValueDecoder([])
        error("truncated value") {
            _ = try swallowed.value { r in do { _ = try r.take(1) } catch {}; return 7 }
        }
    }
    func testCallbackErrorIsStoredWithoutFormattingAndCannotBeReplaced() throws {
        // Error is Sendable; the only mutable state is protected by this lock.
        final class FormattingCounter: @unchecked Sendable {
            private let lock = NSLock()
            private var count = 0
            var value: Int { lock.lock(); defer { lock.unlock() }; return count }
            func increment() { lock.lock(); defer { lock.unlock() }; count += 1 }
        }
        final class CallbackError: Error, CustomStringConvertible {
            private let counter = FormattingCounter()
            var formatted: Int { counter.value }
            var description: String { counter.increment(); return "callback failure" }
        }
        let first = CallbackError(), decoder = try ValueDecoder([])
        do { _ = try decoder.value { _ -> Int in throw first }; XCTFail("expected callback error") }
        catch { XCTAssertTrue((error as? CallbackError) === first) }
        XCTAssertEqual(first.formatted, 0)
        var called = false
        do { _ = try decoder.value { _ in called = true; throw ModelError("later") } as Int; XCTFail("expected first error") }
        catch { XCTAssertTrue((error as? CallbackError) === first) }
        XCTAssertFalse(called)
        do { try decoder.finish(); XCTFail("expected first error") }
        catch { XCTAssertTrue((error as? CallbackError) === first) }
        XCTAssertEqual(first.formatted, 0)
    }
    func testCollectionExactArithmeticOrderAndIndependentCounters() throws {
        let elementFirst = try ValueDecoder([], limits: DecodeLimits(allocation: 0, elements: 0, work: 0))
        error("decode element limit") { try elementFirst.collection(UInt64.max, elementSize: UInt64.max) }
        let overflow = try ValueDecoder([], limits: DecodeLimits(allocation: UInt64.max, elements: UInt64.max, work: 0))
        error("decode allocation overflow") { try overflow.collection(UInt64.max, elementSize: 2) }
        let allocation = try ValueDecoder([], limits: DecodeLimits(allocation: 1, elements: 2, work: 0))
        error("decode allocation limit") { try allocation.collection(2, elementSize: 1) }
        let work = try ValueDecoder([], limits: DecodeLimits(allocation: 2, elements: 2, work: 1))
        error("decode work limit") { try work.collection(2, elementSize: 1) }
        error("decode work limit") { try work.collection(0, elementSize: 0) }
        let zero = try ValueDecoder([], limits: DecodeLimits(allocation: 0, elements: UInt64.max, work: UInt64.max))
        try zero.collection(0, elementSize: UInt64.max)
        try zero.collection(UInt64.max, elementSize: 0)
        _ = try zero.value { _ in () } // Admission did not subtract element work.
        error("decode element limit") { try zero.collection(1, elementSize: 0) }
        let nested = try ValueDecoder([], limits: DecodeLimits(allocation: 3, elements: 10, work: 10))
        try nested.collection(2, elementSize: 1)
        error("decode allocation limit") { try nested.collection(2, elementSize: 1) }
    }
    func testStringByteWorkAndRawValidationStayDistinct() throws {
        let string = try ValueDecoder([65,66], limits: DecodeLimits(allocation: 2, elements: 2, work: 3))
        try string.collection(2, elementSize: 1); try string.consumeStringWork(2)
        XCTAssertEqual(Array(try string.take(2)), [65,66])
        _ = try string.value { _ in () }
        error("decode work limit") { _ = try string.value { _ in () } }
        let raw = try ValueDecoder([])
        // Raw schema validation has no guard to register this application error.
        do { throw ModelError("invalid raw boolean") } catch {}
        XCTAssertEqual(try raw.value { _ in 42 }, 42); try raw.finish()
        let guarded = try ValueDecoder([])
        error("invalid raw boolean") { _ = try guarded.value { _ -> Int in throw ModelError("invalid raw boolean") } }
        error("invalid raw boolean") { _ = try guarded.take(0) }
    }
}

import XCTest
@testable import Statelessness

final class LifecycleRustDiagnosticsTests: XCTestCase {
    func testIndexedRustDiagnosticsAndDefault() throws {
        let formatter = LifecycleRustDebug<Int>(identity: "int-v1", formatKey: { String($0) }, formatString: { "\"\($0)\"" })
        let monitor = indexedLifecycleMonitor(cloneKey: { $0 }, rustDebug: formatter)
        let ordinary = indexedLifecycleMonitor(cloneKey: { (x: Int) in x })
        let key = ScopedKey(epoch: 2, generation: 3, key: 7)
        var history = LifecycleHistory<ScopedKey<Int>>()
        history.obligations = [(key, LifecycleObligation(deadline: 4))]
        history.violations = [LifecycleViolation(key: key, code: "late", event: "tick", observedAt: 5, prior: LifecycleObligation(deadline: 4))]
        let retention = "RetentionStatus { active_obligations: 1, historical_obligations: 0, acquired_resources: 0, historical_resources: 0, oldest_unresolved: Some((ScopedKey { epoch: 2, generation: 3, key: 7 }, 4)), retired_before: 0 }"
        let checks = try monitor.checks(history)
        XCTAssertEqual(checks[0], .failed("lifecycle.indexed.safety", "violations=[Violation { key: Some(ScopedKey { epoch: 2, generation: 3, key: 7 }), code: \"late\", event: \"tick\", observed_at: 5, prior: Some(Obligation { deadline: 4, cancelled: false, settled: false, published: false }) }]; retention=\(retention)"))
        XCTAssertEqual(checks[1], .skipped("lifecycle.indexed.obligations", "finite history: \(retention)"))
        XCTAssertEqual(try ordinary.checks(history)[0].details, "late")
        let oracle: Oracle<Int, LifecycleHistory<ScopedKey<Int>>, Int, Int> = monitor.oracle(metadata: { Metadata(name: "m", build: "b") }, inputEvents: { _,_ in [] }, outputEvents: { _ in [] })
        XCTAssertTrue(try oracle.metadata().name.hasSuffix(";diagnostics=rust-debug-v1;formatter=6:int-v1"))
    }

    func testDiagnosticsCallbackFailureAndEmptyIdentity() throws {
        let bad = indexedLifecycleMonitor(cloneKey: { $0 }, rustDebug: LifecycleRustDebug<Int>(identity: "f", formatKey: { _ in throw ModelError("formatter failed") }, formatString: { $0 }))
        var history = LifecycleHistory<ScopedKey<Int>>()
        history.obligations = [(ScopedKey(epoch: 1, generation: 0, key: 1), LifecycleObligation(deadline: 3))]
        XCTAssertThrowsError(try bad.checks(history))
        let empty = indexedLifecycleMonitor(cloneKey: { $0 }, rustDebug: LifecycleRustDebug<Int>(identity: "", formatKey: { String($0) }, formatString: { $0 }))
        XCTAssertThrowsError(try empty.checks(LifecycleHistory<ScopedKey<Int>>()))
    }
    func testNonModelFormatterErrorsBecomeModelErrors() throws {
        enum FormatterFailure: Error { case failed }
        let key = ScopedKey(epoch: 1, generation: 0, key: 1)
        var history = LifecycleHistory<ScopedKey<Int>>()
        history.obligations = [(key, LifecycleObligation(deadline: 3))]
        history.violations = [LifecycleViolation(key: key, code: "bad", event: "event", observedAt: 0, prior: nil)]
        let keyFailure = indexedLifecycleMonitor(cloneKey: { $0 }, rustDebug: LifecycleRustDebug<Int>(identity: "f", formatKey: { _ in throw FormatterFailure.failed }, formatString: { $0 }))
        XCTAssertThrowsError(try keyFailure.checks(history)) { XCTAssertTrue($0 is ModelError) }
        for failing in ["bad", "event"] {
            let stringFailure = indexedLifecycleMonitor(cloneKey: { $0 }, rustDebug: LifecycleRustDebug<Int>(identity: "f", formatKey: { String($0) }, formatString: { value in
                if value == failing { throw FormatterFailure.failed }
                return value
            }))
            XCTAssertThrowsError(try stringFailure.checks(history)) { XCTAssertTrue($0 is ModelError) }
        }
    }
    func testOptionalDetailsTiesAndUTF8Identity() throws {
        let formatter = LifecycleRustDebug<Int>(identity: "é", formatKey: { String($0) }, formatString: {
            switch $0 {
            case "bad\n\"\\": return "\"bad\\n\\\"\\\\\""
            case "é\t": return "\"é\\t\""
            default: return "\"\($0)\""
            }
        })
        let monitor = indexedLifecycleMonitor(cloneKey: { $0 }, rustDebug: formatter)
        let first = ScopedKey(epoch: 2, generation: 0, key: 1)
        let second = ScopedKey(epoch: 2, generation: 0, key: 2)
        var history = LifecycleHistory<ScopedKey<Int>>()
        history.obligations = [(first, LifecycleObligation(deadline: 8)), (second, LifecycleObligation(deadline: 8))]
        history.violations = [LifecycleViolation(key: nil, code: "bad\n\"\\", event: "é\t", observedAt: 4, prior: nil)]
        let checks = try monitor.checks(history)
        XCTAssertTrue(checks[0].details.contains("key: None"))
        XCTAssertTrue(checks[0].details.contains("prior: None"))
        XCTAssertTrue(checks[0].details.contains("code: \(try formatter.formatString(history.violations[0].code))"))
        XCTAssertTrue(checks[1].details.contains("oldest_unresolved: Some((ScopedKey { epoch: 2, generation: 0, key: 1 }, 8))"))
        let oracle: Oracle<Int, LifecycleHistory<ScopedKey<Int>>, Int, Int> = monitor.oracle(metadata: { Metadata(name: "m", build: "b") }, inputEvents: { _,_ in [] }, outputEvents: { _ in [] })
        XCTAssertTrue(try oracle.metadata().name.hasSuffix(";formatter=2:é"))
    }

    func testRustMonitorReference() throws {
        let monitor = indexedLifecycleMonitor(cloneKey: { $0 }, rustDebug: LifecycleRustDebug<Int>(identity: "u8-v1", formatKey: { String($0) }, formatString: { "\"\($0)\"" }))
        let key = ScopedKey(epoch: 2, generation: 3, key: 7)
        let history = try monitor.advance(LifecycleHistory<ScopedKey<Int>>(), events: [.begin(key, deadline: 9), .begin(key, deadline: 9)])
        let retention = "RetentionStatus { active_obligations: 1, historical_obligations: 0, acquired_resources: 0, historical_resources: 0, oldest_unresolved: Some((ScopedKey { epoch: 2, generation: 3, key: 7 }, 9)), retired_before: 0 }"
        let checks = try monitor.checks(history)
        XCTAssertEqual(checks, [.failed("lifecycle.indexed.safety", "violations=[Violation { key: Some(ScopedKey { epoch: 2, generation: 3, key: 7 }), code: \"key_reused\", event: \"begin\", observed_at: 0, prior: Some(Obligation { deadline: 9, cancelled: false, settled: false, published: false }) }]; retention=\(retention)"), .skipped("lifecycle.indexed.obligations", "finite history: \(retention)")])
    }

}

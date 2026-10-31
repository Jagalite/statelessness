import Foundation
import XCTest
import Statelessness

private func appendJSON(_ checks: [Check]) -> [[String: Any]] {
    checks.map { ["id": $0.id, "status": $0.status.rawValue, "details": $0.details] }
}
private let appendState = [Check.passed("same"), .skipped("skip", "not run")]
private let appendEdge = [Check.passed("same")]
private func appendCodec() -> Codec<Int, Int, Int> {
    Codec(metadata: { Metadata(name: "append") }, encodeState: { Array(String($0).utf8) }, decodeState: { Int(String(decoding: $0, as: UTF8.self))! }, encodeInput: { Array(String($0).utf8) }, decodeInput: { Int(String(decoding: $0, as: UTF8.self))! }, encodeOutput: { Array(String($0).utf8) })
}
private func appendBase() -> Model<Int, Int, Int> {
    Model(initialState: { 0 }, step: { Transition(state: $0 + $1) }, checkState: { _ in appendState }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, checkTransition: { _, _, _ in appendEdge }, inputs: { InputIterator(values: $0 == 0 ? [1] : []) }, equalStates: ==, hashState: { UInt64($0) }, codec: appendCodec())
}
final class AppendChecksTests: XCTestCase {
    func testSharedAppendCorpus() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let path = ProcessInfo.processInfo.environment["STATELESS_APPEND_CORPUS"].map { URL(fileURLWithPath: $0) } ?? root.appendingPathComponent("conformance/append-checks-v1.json")
        let cases = (try JSONSerialization.jsonObject(with: Data(contentsOf: path)) as! [String: Any])["cases"] as! [[String: Any]]
        for c in cases {
            let name = c["case"] as! String, op = c["operation"] as! String
            let append = c["mode"] as! String == "append", errorAt = c["error_at"] as? String, wrapperError = c["wrapper_error"] as? String
            var events: [String] = [], observations: [[String: Any]] = []
            func fail() throws { throw ModelError("fixture append error") }
            func state(_ s: Int) -> [Check] { appendState + (s == c["fail_state"] as? Int ? [.failed("bad", "broken")] : []) }
            func fixture(_ prefix: String = "") -> Model<Int, Int, Int> {
                var m = appendBase()
                m.initialState = { events.append(prefix + "initial"); return 0 }
                m.step = { s, i in events.append(prefix + "step:\(s):\(i)"); return Transition(state: s + i) }
                m.checkState = { s in
                    if append { throw ModelError("legacy state called in append mode") }
                    events.append(prefix + "legacy_state:\(s)")
                    if errorAt == "state:\(s)" { try fail() }; return state(s)
                }
                m.checkTransition = { _, _, t in
                    if append { throw ModelError("legacy transition called in append mode") }
                    events.append(prefix + "legacy_transition:\(t.state)")
                    if errorAt == "transition:\(t.state)" { try fail() }; return appendEdge
                }
                if append {
                    m.checkStateInto = { s, sink in
                        events.append(prefix + "append_state:\(s):\(try sink.count)")
                        try sink.push(.passed("same"))
                        if errorAt == "state:\(s)" || (prefix == "reference_" && wrapperError == "reference_state") { try fail() }
                        try sink.extend(state(s).dropFirst())
                    }
                    m.checkTransitionInto = { _, _, t, sink in
                        events.append(prefix + "append_transition:\(t.state):\(try sink.count)")
                        try sink.extend(appendEdge)
                        if errorAt == "transition:\(t.state)" { try fail() }
                    }
                }
                return m
            }
            let m = fixture()
            let observer = SearchObserver<Int, Int, Int>(initial: { _, checks in
                events.append("observe_initial"); observations.append(["phase": "initial_state", "complete": true, "checks": appendJSON(checks)])
            }, checkError: { phase, checks, complete in
                events.append("check_error:\(phase):\(complete)"); observations.append(["phase": phase, "complete": complete, "checks": appendJSON(checks)])
            }, transition: { _, _, _, sc, ec, _ in
                events.append("observe_transition")
                observations.append(["phase": "state", "complete": true, "checks": appendJSON(sc)])
                observations.append(["phase": "transition", "complete": true, "checks": appendJSON(ec)])
            })
            var buffer = [Check.passed("sentinel")], outputChecks: Any = NSNull(), witness: Any = NSNull(), attempts: Any = NSNull(), outcome = "ok"
            let returnsBuffer = ["direct_state", "direct_transition", "observed", "oracle_state", "oracle_transition", "paired_state", "paired_transition"].contains(op)
            do {
                switch op {
                case "direct_state": try checkStateInto(m, state: 1, checks: &buffer)
                case "direct_transition": try checkTransitionInto(m, before: 0, input: 1, transition: Transition(state: 1), checks: &buffer)
                case "observed": try checkObservedInto(m, before: 0, input: 1, transition: Transition(state: 1), sequence: (c["sequence"] as! NSNumber).uint64Value, policy: CheckPolicy(stateEvery: (c["state_every"] as! NSNumber).uint64Value, transitionChecks: c["transition_checks"] as! Bool), checks: &buffer)
                case "bfs":
                    let r = try enumerateStates(m, observer: observer); outcome = r.termination; witness = r.failure?.inputs as Any? ?? NSNull()
                case "fuzz":
                    let g = Generate(model: m, generate: { _, _ in 1 }, equalInputs: ==)
                    let r = try fuzz(g, config: FuzzConfig(cases: 1, maxSteps: 1), observer: observer)
                    outcome = r.termination; witness = r.failure?.inputs as Any? ?? NSNull()
                case "shrink":
                    let g = Generate(model: m, generate: { _, _ in 1 }, equalInputs: ==)
                    let r = try shrink(g, original: Failure(inputs: [1], violations: [Violation(phase: "state", check: .failed("bad", "broken"))]))
                    outcome = r.termination; witness = r.minimized.inputs; attempts = r.attempts
                case "record":
                    let t = try Statelessness.record(m, inputs: InputIterator(values: [1])); outcome = t.termination.rawValue
                    outputChecks = appendJSON(t.initialChecks + t.steps.flatMap { $0.checks })
                case "replay":
                    let t = Trace(metadata: Metadata(name: "append"), initialState: [48], initialChecks: appendState, steps: [TraceStep(input: [49], disposition: .accepted, outputs: [], postState: [49], checks: appendState + appendEdge)], termination: .completed)
                    outcome = try replay(m, trace: t).outcome
                case "oracle_state", "oracle_transition", "paired_state", "paired_transition":
                    let oracle: Oracle<Int, Int, Int, Int>
                    if op.hasPrefix("paired") {
                        let p = Projection<Int, Int, Int, Int, Int, Int>(metadata: { Metadata(name: "pair") }, actualState: { s in events.append("projection_actual_state"); if wrapperError == "projection_actual_state" { try fail() }; return s }, referenceState: { s in events.append("projection_reference_state"); return s }, actualEffect: { $0 }, referenceEffect: { $0 }, equalState: ==, equalEffect: ==)
                        oracle = Paired(reference: fixture("reference_"), projection: p).oracle
                    } else {
                        oracle = Oracle(metadata: { Metadata(name: "oracle") }, initialState: { 0 }, advance: { h, i, _, _ in h + i }, checkState: { _, _ in throw ModelError("oracle legacy called") }, cloneHistory: { $0 }, checkStateInto: { _, s, sink in
                            events.append("oracle_state:\(s):\(try sink.count)"); try sink.push(.passed("oracle.state"))
                            if wrapperError == "oracle_state" { try fail() }; try sink.push(.skipped("oracle.skip", "not run"))
                        }, checkTransitionInto: { _, _, t, sink in
                            events.append("oracle_transition:\(t.state.model):\(try sink.count)"); try sink.push(.passed("oracle.transition"))
                            if wrapperError == "oracle_transition" { try fail() }; try sink.push(.skipped("oracle.skip", "not run"))
                        })
                    }
                    let wrapped = WithOracle(m, oracle: oracle).model
                    if op.hasSuffix("state") { try checkStateInto(wrapped, state: OracleState(model: 1, oracle: 1), checks: &buffer) }
                    else { try checkTransitionInto(wrapped, before: OracleState(model: 0, oracle: 0), input: 1, transition: Transition(state: OracleState(model: 1, oracle: 1)), checks: &buffer) }
                default: XCTFail("unknown append operation \(op)")
                }
            } catch {
                outcome = "error"
                if let expected = c["error_contains"] as? String { XCTAssertTrue(String(describing: error).contains(expected), "\(name): \(error)") }
                else { XCTFail("\(name): unexpected \(error)") }
            }
            if returnsBuffer { outputChecks = appendJSON(buffer) }
            let result: [String: Any] = ["outcome": outcome, "checks": outputChecks, "observations": observations, "witness": witness, "attempts": attempts]
            XCTAssertEqual(try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]), try JSONSerialization.data(withJSONObject: c["expected"]!, options: [.sortedKeys]), name + ": " + String(describing: result))
            XCTAssertEqual(events, c["events"] as! [String], name)
        }
    }
    private func assertExpired(_ sink: CheckSink, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(try sink.push(.passed("late")), file: file, line: line)
        XCTAssertThrowsError(try sink.extend([.passed("late")] ), file: file, line: line)
        XCTAssertThrowsError(try sink.reserve(0), file: file, line: line)
        XCTAssertThrowsError(try sink.count, file: file, line: line)
        XCTAssertThrowsError(try sink.isEmpty, file: file, line: line)
        XCTAssertThrowsError(try sink.snapshot(), file: file, line: line)
    }
    func testLeasesExpireAndSnapshotsDetachOnEveryExit() throws {
        var m = appendBase(), saved: CheckSink?, snapshot: [Check] = []
        m.checkStateInto = { _, sink in
            saved = sink; try sink.reserve(32)
            XCTAssertEqual(try sink.count, 1); XCTAssertFalse(try sink.isEmpty)
            snapshot = try sink.snapshot(); try sink.push(.passed("new"))
        }
        var buffer = [Check.passed("prefix")]
        try checkStateInto(m, state: 0, checks: &buffer)
        XCTAssertEqual(buffer, [.passed("prefix"), .passed("new")]); XCTAssertEqual(snapshot, [.passed("prefix")])
        assertExpired(saved!)
        snapshot.append(.passed("external")); XCTAssertEqual(buffer.count, 2)
        let old = saved!
        m.checkStateInto = { _, sink in
            self.assertExpired(old) // Old lease cannot poison the new active batch.
            saved = sink; try sink.push(.passed("partial")); throw ModelError("checker primary")
        }
        XCTAssertThrowsError(try checkStateInto(m, state: 1, checks: &buffer))
        XCTAssertEqual(buffer.last, .passed("partial")); assertExpired(saved!)
        m.checkStateInto = { _, sink in saved = sink; try sink.push(.passed("fresh")) }
        try checkStateInto(m, state: 2, checks: &buffer)
        XCTAssertEqual(buffer.last, .passed("fresh")); assertExpired(saved!)
    }
    func testInvalidOperationsAreStickyAndKeepOnlyValidPrefix() throws {
        for kind in ["push", "extend", "reserve-negative", "reserve-overflow"] {
            var m = appendBase(), saved: CheckSink?
            m.checkStateInto = { _, sink in
                saved = sink; try sink.push(.passed("valid"))
                do {
                    switch kind {
                    case "push": try sink.push(Check("bad", .passed, "invalid"))
                    case "extend": try sink.extend([.passed("extended"), Check("bad", .passed, "invalid"), .passed("unreachable")])
                    case "reserve-negative": try sink.reserve(-1)
                    default: try sink.reserve(Int.max)
                    }
                    XCTFail("invalid operation accepted")
                } catch { }
                self.assertExpired(sink)
            }
            var checks = [Check.passed("prefix")]
            XCTAssertThrowsError(try checkStateInto(m, state: 0, checks: &checks), kind)
            XCTAssertEqual(checks, [.passed("prefix"), .passed("valid")] + (kind == "extend" ? [.passed("extended")] : []), kind)
            assertExpired(saved!)
        }
    }
    func testEmptySinkRejectsUnrepresentableReservationAndPoisonsLease() throws {
        var m = appendBase()
        m.checkStateInto = { _,sink in
            XCTAssertThrowsError(try sink.reserve(Int.max))
            XCTAssertThrowsError(try sink.push(.passed("not retained")))
        }
        var checks: [Check] = []
        XCTAssertThrowsError(try checkStateInto(m,state:0,checks:&checks))
        XCTAssertTrue(checks.isEmpty)
    }
    func testFirstValidationFailureWinsOverLaterCallbackError() throws {
        var m = appendBase()
        m.checkStateInto = { _, sink in
            try sink.push(.passed("valid"))
            do { try sink.push(Check("bad", .passed, "invalid")) } catch { }
            throw ModelError("later callback error")
        }
        var checks: [Check] = []
        XCTAssertThrowsError(try checkStateInto(m, state: 0, checks: &checks)) { error in
            XCTAssertTrue(String(describing: error).contains("passing checks have no details"))
            XCTAssertFalse(String(describing: error).contains("later callback error"))
        }
        XCTAssertEqual(checks, [.passed("valid")])
    }
    func testObservedIntoClearsEveryErrorAndRetainsCapacity() throws {
        var m = appendBase(), calls = 0
        m.step = { _, _ in XCTFail("observed must not run reducer"); throw ModelError("step") }
        m.checkStateInto = { _, sink in calls += 1; try sink.push(.passed("state")) }
        m.checkTransitionInto = { _, _, _, sink in
            XCTAssertEqual(try sink.snapshot(), [.passed("state")]); try sink.push(.passed("edge"))
        }
        var checks: [Check] = []; checks.reserveCapacity(128)
        let capacity = checks.capacity
        try checkObservedInto(m, before: 0, input: 1, transition: Transition(state: 1), sequence: 1, checks: &checks)
        let old = checks
        XCTAssertEqual(checks, [.passed("state"), .passed("edge")]); XCTAssertGreaterThanOrEqual(checks.capacity, capacity)
        XCTAssertThrowsError(try checkObservedInto(m, before: 0, input: 1, transition: Transition(state: 1), sequence: 0, checks: &checks))
        XCTAssertTrue(checks.isEmpty); XCTAssertEqual(calls, 1)
        XCTAssertThrowsError(try checkObservedInto(m, before: 0, input: 1, transition: Transition(state: 1), sequence: 1, policy: CheckPolicy(stateEvery: 0), checks: &checks))
        m.checkTransitionInto = { _, _, _, sink in try sink.push(.passed("partial")); throw ModelError("edge failure") }
        XCTAssertThrowsError(try checkObservedInto(m, before: 0, input: 1, transition: Transition(state: 1), sequence: 1, checks: &checks))
        XCTAssertTrue(checks.isEmpty); XCTAssertGreaterThanOrEqual(checks.capacity, capacity)
        m.cloneState = { _ in throw ModelError("clone failure") }
        checks.append(.passed("dirty"))
        XCTAssertThrowsError(try checkObservedInto(m, before: 0, input: 1, transition: Transition(state: 1), sequence: 1, checks: &checks))
        XCTAssertTrue(checks.isEmpty); XCTAssertEqual(old, [.passed("state"), .passed("edge")])
    }
    func testNestedWrapperLeasesExpireWithoutRevokingOuter() throws {
        var actual = appendBase(), actualLease: CheckSink?, oracleLease: CheckSink?
        actual.checkStateInto = { _, sink in actualLease = sink; try sink.push(.passed("actual")) }
        let oracle = Oracle<Int, Int, Int, Int>(metadata: { Metadata(name: "oracle") }, initialState: { 0 }, advance: { h, _, _, _ in h }, checkState: { _, _ in XCTFail("legacy oracle"); return [] }, cloneHistory: { $0 }, checkStateInto: { _, _, sink in
            self.assertExpired(actualLease!)
            XCTAssertEqual(try sink.snapshot(), [.passed("prefix"), .passed("actual")])
            oracleLease = sink; try sink.push(.passed("oracle"))
        })
        let wrapped = WithOracle(actual, oracle: oracle).model
        var checks = [Check.passed("prefix")]
        try checkStateInto(wrapped, state: OracleState(model: 0, oracle: 0), checks: &checks)
        XCTAssertEqual(checks, [.passed("prefix"), .passed("actual"), .passed("oracle")])
        assertExpired(actualLease!); assertExpired(oracleLease!)
        // Nest another dispatch inside an application callback. The enclosing
        // lease remains usable after the child callback has expired.
        var outer = appendBase()
        outer.checkStateInto = { _, sink in
            var nested: [Check] = []
            try checkStateInto(actual, state: 0, checks: &nested)
            self.assertExpired(actualLease!)
            try sink.push(.passed("outer survives"))
        }
        checks = []; try checkStateInto(outer, state: 0, checks: &checks)
        XCTAssertEqual(checks, [.passed("outer survives")])
    }
    func testSearchPartialBatchesAndPrimaryErrorSurviveObserverErrors() throws {
        var m = appendBase(), stateCalls = 0, edgeCalls = 0, callbacks: [(String, [Check], Bool)] = []
        m.checkStateInto = { _, sink in stateCalls += 1; try sink.extend(appendState) }
        m.checkTransitionInto = { _, _, _, sink in edgeCalls += 1; try sink.push(.passed("partial")); throw ModelError("primary edge failure") }
        let observer = SearchObserver<Int, Int, Int>(checkError: { phase, checks, complete in
            callbacks.append((phase, checks, complete)); throw ModelError("secondary observer failure")
        })
        XCTAssertThrowsError(try enumerateStates(m, observer: observer)) { error in
            XCTAssertTrue(String(describing: error).contains("primary edge failure"))
            XCTAssertTrue(String(describing: error).contains("secondary observer failure"))
        }
        XCTAssertEqual(stateCalls, 2); XCTAssertEqual(edgeCalls, 1)
        XCTAssertEqual(callbacks.map { $0.0 }, ["state", "transition"])
        XCTAssertEqual(callbacks.map { $0.2 }, [true, false])
        XCTAssertEqual(callbacks[0].1, appendState); XCTAssertEqual(callbacks[1].1, [.passed("partial")])
        m.checkStateInto = { _, sink in
            try sink.push(.passed("before invalid"))
            do { try sink.push(Check("invalid", .passed, "details")) } catch { }
        }
        callbacks = []
        XCTAssertThrowsError(try enumerateStates(m, observer: observer))
        XCTAssertEqual(callbacks.count, 1); XCTAssertEqual(callbacks[0].1, [.passed("before invalid")]); XCTAssertFalse(callbacks[0].2)
    }
}

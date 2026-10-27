import Foundation
import XCTest
import Statelessness

private func checkedCheckJSON(_ values: [Check]) -> [[String: Any]] { values.map { ["id": $0.id, "status": $0.status.rawValue, "details": $0.details] } }
private func checkedDispositionJSON(_ d: Disposition) -> [String: Any] {
    switch d { case .accepted: return ["kind": "accepted", "reason": ""]; case let .rejected(s): return ["kind": "rejected", "reason": s]; case let .ignored(s): return ["kind": "ignored", "reason": s] }
}
private func checkedChecks(_ rows: [[String: Any]]) -> [Check] { rows.map { Check($0["id"] as! String, CheckStatus(rawValue: $0["status"] as! String)!, $0["details"] as! String) } }
private final class CheckedBox { var value: Int; init(_ value: Int) { self.value = value } }
final class CheckedResultsTests: XCTestCase {
    func testSharedCheckedResultsCorpus() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let path = ProcessInfo.processInfo.environment["STATELESS_CHECKED_CORPUS"].map { URL(fileURLWithPath: $0) } ?? root.appendingPathComponent("conformance/checked-results-v1.json")
        let cases = (try JSONSerialization.jsonObject(with: Data(contentsOf: path)) as! [String: Any])["cases"] as! [[String: Any]]
        for c in cases {
            let name = c["case"] as! String, settings = c["model"] as! [String: Any], expected = c["expected"] as! [String: Any]
            var events: [String] = [], initialCalls = 0, stepCalls = 0
            let model = Model<Int,Int,Int>(initialState: { initialCalls += 1; return 0 }, step: { s, i in stepCalls += 1; return Transition(state: s+i) }, checkState: { _ in throw ModelError("legacy state called") }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, checkStateInto: { state, sink in
                events.append("state:\(state)")
                let checks = checkedChecks(settings["state_checks"] as! [[String: Any]])
                if let error = settings["state_error"] as? String { if let first = checks.first { try sink.push(first) }; throw ModelError(error) }
                try sink.extend(checks)
            }, checkTransitionInto: { before, input, t, sink in
                events.append("transition:\(before):\(input):\(t.state)")
                let checks = checkedChecks(settings["transition_checks"] as! [[String: Any]])
                if let error = settings["transition_error"] as? String { if let first = checks.first { try sink.push(first) }; throw ModelError(error) }
                try sink.extend(checks)
            })
            var actual: Any = NSNull()
            do {
                if c["kind"] as! String == "initial" {
                    let token = try checkInitial(model, state: c["state"] as! Int)
                    actual = ["kind": "initial", "checks": checkedCheckJSON(token.checks), "state_check_count": token.stateCheckCount, "state": try token.state] as [String: Any]
                } else {
                    let td = c["transition"] as! [String: Any], dd = td["disposition"] as! [String: Any], policy = c["policy"] as! [String: Any]
                    let disposition: Disposition = dd["kind"] as! String == "accepted" ? .accepted : dd["kind"] as! String == "rejected" ? .rejected(dd["reason"] as! String) : .ignored(dd["reason"] as! String)
                    let turn = Transition(state: td["state"] as! Int, outputs: td["outputs"] as! [Int], disposition: disposition)
                    let token = try checkTurn(model, before: c["before"] as! Int, input: c["input"] as! Int, transition: turn, sequence: UInt64(c["sequence"] as! String)!, policy: CheckPolicy(stateEvery: UInt64(policy["state_every"] as! String)!, transitionChecks: policy["transition_checks"] as! Bool))
                    let transition = try token.transition
                    actual = ["kind": "turn", "checks": checkedCheckJSON(token.checks), "state_check_count": token.stateCheckCount, "before": try token.before, "input": try token.input, "transition": ["state": transition.state, "outputs": transition.outputs, "disposition": checkedDispositionJSON(transition.disposition)] as [String: Any], "sequence": String(token.sequence), "full_checking": token.fullChecking] as [String: Any]
                    let diagnostic = try token.observation()
                    XCTAssertEqual(diagnostic.sequence, token.sequence); XCTAssertEqual(diagnostic.checks, token.checks)
                    XCTAssertEqual(diagnostic.stateCheckCount, token.stateCheckCount)
                }
                XCTAssertNil(expected["error_contains"] as? String, name)
            } catch {
                if let message = expected["error_contains"] as? String { XCTAssertTrue(String(describing: error).contains(message), "\(name): \(error)") }
                else { XCTFail("\(name): unexpected \(error)") }
            }
            XCTAssertEqual(try JSONSerialization.data(withJSONObject: ["token": actual], options: [.sortedKeys]), try JSONSerialization.data(withJSONObject: ["token": expected["token"]!], options: [.sortedKeys]), name)
            XCTAssertEqual(events, expected["events"] as! [String], name)
            XCTAssertEqual(initialCalls, expected["initial_state_calls"] as! Int, name); XCTAssertEqual(stepCalls, expected["step_calls"] as! Int, name)
        }
    }
    func testTokensCertifyPristineSnapshotsAndGettersDetach() throws {
        let supplied = CheckedBox(7), before = CheckedBox(7), input = CheckedBox(2), next = CheckedBox(9), output = CheckedBox(90)
        var states = 0, edges = 0
        let model = Model<CheckedBox,CheckedBox,CheckedBox>(initialState: { XCTFail("initial called"); return CheckedBox(0) }, step: { _, _ in XCTFail("reducer called"); return Transition(state: CheckedBox(0)) }, checkState: { _ in XCTFail("legacy called"); return [] }, cloneState: { CheckedBox($0.value) }, cloneInput: { CheckedBox($0.value) }, cloneOutput: { CheckedBox($0.value) }, checkStateInto: { state, sink in
            states += 1; XCTAssertTrue(state.value == 7 || state.value == 9)
            state.value = 99 // Only the callback's clone changes.
            supplied.value = 55; next.value = 55 // Capturing caller aliases cannot change the certified snapshot.
            try sink.push(.failed("bad", "failure still certifies completed checking"))
        }, checkTransitionInto: { b, i, t, sink in
            edges += 1; XCTAssertEqual(b.value, 7); XCTAssertEqual(i.value, 2); XCTAssertEqual(t.state.value, 55)
            b.value = 99; i.value = 99; t.state.value = 99; t.outputs[0].value = 99
            try sink.push(.skipped("edge", "not run"))
        })
        let initial = try checkInitial(model, state: supplied)
        XCTAssertEqual(try initial.state.value, 7)
        let exposed = try initial.state; exposed.value = 1
        XCTAssertEqual(try initial.state.value, 7); XCTAssertEqual(initial.stateCheckCount, 1)
        var checks = initial.intoChecks(); checks.removeAll(); XCTAssertEqual(initial.checks.count, 1)
        // next was changed by the initial callback, so the turn's consistent
        // captured successor is now55, independent of later caller mutation.
        var turnModel = model
        turnModel.checkStateInto = { s, sink in states += 1; XCTAssertEqual(s.value, 55); s.value = 99; next.value = 8; try sink.push(.passed("state")) }
        let turn = Transition(state: next, outputs: [output], disposition: .ignored("host"))
        let token = try checkTurn(turnModel, before: before, input: input, transition: turn, sequence: UInt64.max)
        before.value = 1; input.value = 1; next.value = 1; output.value = 1
        XCTAssertEqual(try token.before.value, 7); XCTAssertEqual(try token.input.value, 2); XCTAssertEqual(try token.transition.state.value, 55); XCTAssertEqual(try token.transition.outputs[0].value, 90)
        let diagnostic = try token.observation(); diagnostic.before.value = 3; diagnostic.input.value = 3; diagnostic.transition.state.value = 3; diagnostic.transition.outputs[0].value = 3
        XCTAssertEqual(try token.before.value, 7); XCTAssertEqual(try token.transition.outputs[0].value, 90)
        XCTAssertEqual(token.sequence, UInt64.max); XCTAssertTrue(token.fullChecking); XCTAssertEqual(token.stateCheckCount, 1)
        var replacedModel = token.model; replacedModel.checkState = { _ in [] }
        XCTAssertEqual(token.checks, [.passed("state"), .skipped("edge", "not run")])
        XCTAssertEqual(states, 2); XCTAssertEqual(edges, 1)
    }
    func testErrorsNeverMintPartialTokensOrRepeatCallbacks() throws {
        for stage in ["initial-snapshot", "turn-snapshot", "state", "edge", "invalid", "ignored-invalid", "zero-period"] {
            var stateCalls = 0, edgeCalls = 0, clones = 0
            var model = Model<Int,Int,Int>(initialState: { XCTFail("initial called"); return 0 }, step: { _, _ in XCTFail("step called"); return Transition(state: 0) }, checkState: { _ in XCTFail("legacy called"); return [] }, cloneState: { value in
                clones += 1
                if stage == "initial-snapshot" || stage == "turn-snapshot" { throw ModelError("snapshot failure") }; return value
            }, cloneInput: { $0 }, cloneOutput: { $0 }, checkStateInto: { _, sink in
                stateCalls += 1; try sink.push(.passed("prefix"))
                if stage == "state" { throw ModelError("state error") }
                if stage == "invalid" { try sink.push(Check("bad", .passed, "invalid")) }
                if stage == "ignored-invalid" { do { try sink.push(Check("bad", .passed, "invalid")) } catch {} }
            }, checkTransitionInto: { _, _, _, sink in edgeCalls += 1; try sink.push(.passed("edge prefix")); if stage == "edge" { throw ModelError("edge error") } })
            let actual = Transition<Int,Int>(state: 9, outputs: [90])
            if stage == "initial-snapshot" { XCTAssertThrowsError(try checkInitial(model, state: 7)); XCTAssertEqual(clones, 1) }
            else {
                XCTAssertThrowsError(try checkTurn(model, before: 7, input: 2, transition: actual, sequence: 1, policy: CheckPolicy(stateEvery: stage == "zero-period" ? 0 : 1)))
                if stage == "turn-snapshot" { XCTAssertEqual(clones, 1) }
            }
            XCTAssertEqual(actual.state, 9); XCTAssertEqual(actual.outputs, [90]); XCTAssertLessThanOrEqual(stateCalls, 1); XCTAssertLessThanOrEqual(edgeCalls, 1)
            if ["initial-snapshot", "turn-snapshot", "zero-period"].contains(stage) { XCTAssertEqual(stateCalls, 0); XCTAssertEqual(edgeCalls, 0) }
            model.checkStateInto = nil // A copied model cannot mutate any previously sealed result.
        }
    }
}

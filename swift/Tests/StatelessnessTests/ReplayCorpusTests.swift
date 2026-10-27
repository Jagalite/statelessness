import Foundation
import XCTest
import Statelessness

private func replayHex(_ bytes: [UInt8]) -> String { bytes.map { String(format: "%02x", $0) }.joined() }
private func replayBytes(_ hex: String) -> [UInt8] { stride(from: 0, to: hex.count, by: 2).map { i in let start = hex.index(hex.startIndex, offsetBy: i); return UInt8(hex[start..<hex.index(start, offsetBy: 2)], radix: 16)! } }
private func replayChecks(_ values: [[String: Any]]) -> [Check] { values.map { Check($0["id"] as! String, CheckStatus(rawValue: $0["status"] as! String)!, $0["reason"] as? String ?? "") } }
private func replayChecksJSON(_ values: [Check]) -> [[String: Any]] { values.map { ["id": $0.id, "status": $0.status.rawValue, "details": $0.details] } }
private func replayDisposition(_ d: [String: Any]) -> Disposition {
    switch d["kind"] as! String { case "rejected": return .rejected(d["reason"] as? String ?? ""); case "ignored": return .ignored(d["reason"] as? String ?? ""); default: return .accepted }
}
private func replayDispositionJSON(_ d: Disposition) -> [String: Any] {
    switch d { case .accepted: return ["kind": "accepted", "reason": ""]; case let .rejected(s): return ["kind": "rejected", "reason": s]; case let .ignored(s): return ["kind": "ignored", "reason": s] }
}
private func replayStep(_ d: [String: Any]) -> TraceStep {
    TraceStep(input: replayBytes(d["input"] as! String), disposition: replayDisposition(d["disposition"] as! [String: Any]), outputs: (d["outputs"] as! [String]).map(replayBytes), postState: replayBytes(d["post_state"] as! String), checks: replayChecks(d["checks"] as! [[String: Any]]))
}
private func replayStepJSON(_ step: TraceStep) -> [String: Any] { ["input": replayHex(step.input), "disposition": replayDispositionJSON(step.disposition), "outputs": step.outputs.map(replayHex), "post_state": replayHex(step.postState), "checks": replayChecksJSON(step.checks)] }
private func replayConfig(_ d: [String: Any]) -> RunConfig { RunConfig(strategy: d["strategy"] as! String, seed: (d["seed"] as? String).flatMap(UInt64.init), parameters: (d["parameters"] as! [[String]]).map { AuditParameter($0[0],$0[1]) }) }
private func replayAudit(_ d: [String: Any]) -> AuditTrace {
    let meta = d["metadata"] as! [String: Any], end = d["termination"] as! [String: Any]
    let termination: AuditTermination
    switch end["kind"] as! String { case "property_failed": termination = .propertyFailed; case "step_limit": termination = .stepLimit; case "interrupted": termination = .interrupted; case "model_error": termination = .modelError(end["reason"] as? String ?? ""); default: termination = .completed }
    return AuditTrace(metadata: Metadata(name: meta["name"] as! String, modelVersion: UInt32(meta["model_version"] as! Int), propertiesVersion: UInt32(meta["properties_version"] as! Int), codecVersion: UInt32(meta["codec_version"] as! Int), build: meta["build"] as! String), config: replayConfig(d["config"] as! [String: Any]), initialState: replayBytes(d["initial_state"] as! String), initialChecks: replayChecks(d["initial_checks"] as! [[String: Any]]), steps: (d["steps"] as! [[String: Any]]).map(replayStep), termination: termination)
}
private func replayLimits(_ d: [String: Any]) -> BinaryTraceLimits {
    var limits = BinaryTraceLimits()
    for (key, value) in d {
        let n = (value as! NSNumber).uint64Value
        switch key { case "max_total_bytes": limits.maxTotalBytes = n; case "max_frame_bytes": limits.maxFrameBytes = n; case "max_blob_bytes": limits.maxBlobBytes = n; case "max_string_bytes": limits.maxStringBytes = n; case "max_steps": limits.maxSteps = n; case "max_checks_per_step": limits.maxChecksPerStep = n; case "max_outputs_per_step": limits.maxOutputsPerStep = n; case "max_parameters": limits.maxParameters = n; case "max_items": limits.maxItems = n; default: preconditionFailure("unknown quota") }
    }
    return limits
}
private func replayReportJSON(_ r: ReplayReport) -> [String: Any] { ["outcome": r.outcome, "divergence_step": r.step as Any? ?? NSNull(), "divergence_field": r.field as Any? ?? NSNull(), "steps_verified": r.stepsVerified, "failure_reproduced": r.failureReproduced, "build_matches": r.buildMatches] }
private func replayDifferencesJSON(_ d: ReplayDifferences) -> [String: Any] { ["disposition": d.disposition, "outputs": d.outputs, "state": d.state, "checks": d.checks] }
private func replayTransitionJSON(_ t: Transition<Int,Int>) -> [String: Any] { ["state": t.state, "outputs": t.outputs, "disposition": replayDispositionJSON(t.disposition)] }
private func replayActualJSON(_ value: ReplayActual<Int,Int,Int>?) -> Any {
    guard let value else { return NSNull() }
    switch value {
    case let .initial(state, checks): return ["kind": "initial", "sequence": 0, "state": state, "checks": checks.map(replayChecksJSON) as Any? ?? NSNull()] as [String: Any]
    case let .turn(sequence, before, input, transition, checks, count): return ["kind": "turn", "sequence": sequence, "before": before, "input": input, "transition": replayTransitionJSON(transition), "checks": checks.map(replayChecksJSON) as Any? ?? NSNull(), "state_check_count": count as Any? ?? NSNull()] as [String: Any]
    }
}
private func replayCorpus() throws -> [String: Any] {
    let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
    let path = ProcessInfo.processInfo.environment["STATELESS_REPLAY_CORPUS"].map { URL(fileURLWithPath: $0) } ?? root.appendingPathComponent("conformance/replay-evidence-v1.json")
    return try JSONSerialization.jsonObject(with: Data(contentsOf: path)) as! [String: Any]
}
private func replayFixture(_ settings: [String: Any], event: @escaping (String) throws -> Void) -> Model<Int,Int,Int> {
    func check(_ key: String, _ fallback: String) -> [Check] { (settings[key] as? [String: Any]).map { replayChecks([$0]) } ?? [.passed(fallback)] }
    let codec = Codec<Int,Int,Int>(metadata: { try event("metadata"); return Metadata(name: settings["name"] as? String ?? "codec-fixture", build: settings["build"] as? String ?? "candidate-v1") }, encodeState: { _ in throw ModelError("legacy state called") }, decodeState: { b in try event("decode_state:\(replayHex(b))"); guard let n = Int(String(decoding: b, as: UTF8.self)) else { throw ModelError("invalid state") }; return n }, encodeInput: { _ in throw ModelError("legacy input called") }, decodeInput: { b in try event("decode_input:\(replayHex(b))"); guard let n = Int(String(decoding: b, as: UTF8.self)) else { throw ModelError("invalid input") }; return n }, encodeOutput: { _ in throw ModelError("legacy output called") }, encodeStateInto: { n, sink in try event("encode_state:\(n)"); try sink.extend(Array(String(n).utf8)) }, encodeInputInto: { n, sink in try event("encode_input:\(n)"); try sink.extend(Array(String(n).utf8)) }, encodeOutputInto: { n, sink in try event("encode_output:\(n)"); try sink.extend(Array(String(n).utf8) + [UInt8](repeating: 120, count: settings["output_padding"] as? Int ?? 0)) })
    return Model(initialState: { try event("initial"); return 0 }, step: { before, input in
        try event("step:\(before):\(input)"); let next = before+input
        let disposition: Disposition = settings["disposition"] as? String == "rejected" ? .rejected(settings["disposition_reason"] as? String ?? "why") : .accepted
        return Transition(state: next + (settings["state_delta"] as? Int ?? 0), outputs: [Int](repeating: next + (settings["output_delta"] as? Int ?? 0), count: settings["output_count"] as? Int ?? 1), disposition: disposition)
    }, checkState: { _ in throw ModelError("legacy checker called") }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, codec: codec, checkStateInto: { n, sink in try event("check_state:\(n)"); try sink.extend(check(n == 0 ? "initial_check" : "turn_state_check", "ok")) }, checkTransitionInto: { before, input, t, sink in try event("check_transition:\(before):\(input):\(t.state)"); try sink.extend(check("turn_transition_check", "edge")) })
}
final class ReplayCorpusTests: XCTestCase {
    func testSharedReplayEvidenceCorpus() throws {
        for c in try replayCorpus()["cases"] as! [[String: Any]] {
            let name = c["case"] as! String, settings = c["model"] as! [String: Any], errorAt = settings["error_at"] as? String, observerError = c["observer_error_at"] as? String
            var events: [String] = [], observations: [[String: Any]] = []
            func event(_ text: String) throws { events.append(text); if text == errorAt { throw ModelError("fixture codec replay error") } }
            let m = replayFixture(settings, event: event), trace = replayAudit(c["trace"] as! [String: Any])
            let observer: (ReplayObservation<Int,Int,Int>,ReplayReport) throws -> Void = { value, report in
                let row: [String: Any], boundary: String
                switch value {
                case let .initial(v):
                    try event("observe_initial"); boundary = "initial"
                    row = ["kind": "initial", "sequence": 0, "state": v.state, "checks": replayChecksJSON(v.checks), "expected_state": replayHex(v.expectedState), "actual_state": replayHex(v.actualState), "expected_checks": replayChecksJSON(v.expectedChecks), "differences": replayDifferencesJSON(v.differences), "report": replayReportJSON(report)]
                case let .turn(v):
                    try event("observe_turn:\(v.sequence)"); boundary = "turn:\(v.sequence)"
                    row = ["kind": "turn", "sequence": v.sequence, "before": v.before, "input": v.input, "transition": replayTransitionJSON(v.transition), "checks": replayChecksJSON(v.checks), "state_check_count": v.stateCheckCount, "expected": replayStepJSON(v.expected), "actual_input": replayHex(v.actualInput), "actual_outputs": v.actualOutputs.map(replayHex), "actual_state": replayHex(v.actualState), "differences": replayDifferencesJSON(v.differences), "report": replayReportJSON(report)]
                case let .error(sequence, actual, _, reason):
                    XCTAssertNil(reason, name); try event("observe_error:\(sequence)"); boundary = "error"
                    row = ["kind": "error", "sequence": sequence, "actual": replayActualJSON(actual), "report": replayReportJSON(report)]
                }
                observations.append(row)
                if observerError == boundary { throw ModelError("fixture observer error") }
            }
            var result: [String: Any]
            do {
                let report: ReplayReport
                if c["bounded"] as! Bool { report = try replayWithObservationsBounded(m, trace: trace, allowBuildMismatch: c["allow_build_mismatch"] as! Bool, limits: replayLimits(c["limits"] as! [String: Any]), observer: observer) }
                else { report = try replayWithObservations(m, trace: trace.portable().trace, allowBuildMismatch: c["allow_build_mismatch"] as! Bool, observer: observer) }
                result = ["report": replayReportJSON(report), "observations": observations, "error": false, "actual": NSNull(), "observer_error": false]
            } catch let error as ReplayError<Int,Int,Int> {
                result = ["report": replayReportJSON(error.report), "observations": observations, "error": true, "actual": replayActualJSON(error.actual), "observer_error": error.observerError != nil]
                if let expected = c["error_contains"] as? String { XCTAssertTrue(String(describing: error.error).contains(expected), "\(name): \(error.error)") }
            }
            XCTAssertEqual(try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]), try JSONSerialization.data(withJSONObject: c["expected"]!, options: [.sortedKeys]), "\(name): \(result)")
            XCTAssertEqual(events, c["events"] as! [String], name)
        }
    }
    func testSharedAuditRecordingCorpus() throws {
        for c in try replayCorpus()["record_cases"] as! [[String: Any]] {
            let name = c["case"] as! String, errorAt = c["error_at"] as? String
            let m = replayFixture([:], event: { if $0 == errorAt { throw ModelError("fixture codec replay error") } })
            let config = replayConfig(c["config"] as! [String: Any]); var limits = replayLimits(c["limits"] as! [String: Any])
            if let formula = c["budget_formula"] as? String {
                let stamp = RunConfig(strategy: config.strategy, seed: config.seed, parameters: config.parameters + [AuditParameter("stateless.engine.version", statelessnessVersion), AuditParameter("stateless.engine.build", "native-swift-v1")])
                let empty = AuditTrace(metadata: Metadata(name: "codec-fixture", build: "candidate-v1"), config: stamp, initialState: [48], initialChecks: [.passed("ok")], steps: [], termination: .completed)
                // Independent fixture formula: encoded header+RUN excludes END18;
                // two 57-byte STEP frames and reserved error END278 follow.
                limits.maxTotalBytes = UInt64(try encodeAuditTrace(empty).count - 18 + 2 * 57 + 278 - (formula.hasSuffix("-1") ? 1 : 0))
            }
            var result: [String: Any]
            do {
                let trace = try recordAudit(m, inputs: InputIterator(values: c["inputs"] as! [Int]), config: config, limits: limits)
                let termination: String
                switch trace.termination { case .completed: termination = "completed"; case .propertyFailed: termination = "property_failed"; case .stepLimit: termination = "step_limit"; case .interrupted: termination = "interrupted"; case .modelError: termination = "model_error" }
                result = ["error": false, "termination": termination, "steps": trace.steps.count]
                _ = try encodeAuditTrace(trace, limits: limits)
            } catch { result = ["error": true, "termination": NSNull(), "steps": NSNull()] }
            XCTAssertEqual(try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]), try JSONSerialization.data(withJSONObject: c["expected"]!, options: [.sortedKeys]), "\(name): \(result)")
        }
    }
}

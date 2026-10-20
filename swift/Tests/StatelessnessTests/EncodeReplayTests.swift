import Foundation
import XCTest
import Statelessness

private func codecHex(_ bytes: [UInt8]) -> String { bytes.map { String(format: "%02x", $0) }.joined() }
private func codecBytes(_ hex: String) -> [UInt8] {
    stride(from: 0, to: hex.count, by: 2).map { i in let a = hex.index(hex.startIndex, offsetBy: i); return UInt8(hex[a..<hex.index(a, offsetBy: 2)], radix: 16)! }
}
private func codecCounter() -> Model<Int,Int,Int> {
    Model(initialState: { 0 }, step: { Transition(state: $0 + $1, outputs: [$0]) }, checkState: { _ in [.passed("state")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, checkTransition: { _, _, _ in [.passed("edge")] }, inputs: { InputIterator(values: $0 == 0 ? [1] : []) }, equalStates: ==, hashState: { UInt64($0) }, codec: Codec(metadata: { Metadata(name: "codec", build: "b") }, encodeState: { Array(String($0).utf8) }, decodeState: { Int(String(decoding: $0, as: UTF8.self))! }, encodeInput: { Array(String($0).utf8) }, decodeInput: { Int(String(decoding: $0, as: UTF8.self))! }, encodeOutput: { Array(String($0).utf8) }))
}
private final class ReplayBox { var value: Int; init(_ value: Int) { self.value = value } }
final class EncodeReplayTests: XCTestCase {
    func testSharedEncodeCorpus() throws {
        let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let path = ProcessInfo.processInfo.environment["STATELESS_ENCODE_CORPUS"].map { URL(fileURLWithPath: $0) } ?? root.appendingPathComponent("conformance/encode-buffers-v1.json")
        let cases = (try JSONSerialization.jsonObject(with: Data(contentsOf: path)) as! [String: Any])["cases"] as! [[String: Any]]
        for c in cases {
            let name = c["case"] as! String, op = c["operation"] as! String
            let maximum = Int(min(UInt64(c["maximum"] as! String)!, UInt64(Int.max)))
            var bytes = codecBytes(c["initial_hex"] as? String ?? ""), events: [String] = [], results: [[String: Any]] = []
            func model(_ mode: String) -> Model<Int,Int,Int> {
                var m = codecCounter(), codec = m.codec!
                codec.encodeState = { value in events.append("legacy_state"); return Array(String(value).utf8) }
                codec.encodeInput = { value in events.append("legacy_input"); return Array(String(value).utf8) }
                codec.encodeOutput = { value in events.append("legacy_output"); return Array(String(value).utf8) }
                if mode == "into" {
                    codec.encodeState = { _ in throw ModelError("unexpected legacy state") }
                    codec.encodeInput = { _ in throw ModelError("unexpected legacy input") }
                    codec.encodeOutput = { _ in throw ModelError("unexpected legacy output") }
                    codec.encodeStateInto = { value, sink in events.append("into_state"); try sink.extend(Array(String(value).utf8)) }
                    codec.encodeInputInto = { value, sink in events.append("into_input"); try sink.extend(Array(String(value).utf8)) }
                    codec.encodeOutputInto = { value, sink in events.append("into_output"); try sink.extend(Array(String(value).utf8)) }
                }
                m.codec = codec; return m
            }
            func oracle() -> Oracle<Int,Int,Int,Int> {
                var o = Oracle<Int,Int,Int,Int>(metadata: { Metadata(name: "history") }, initialState: { 34 }, advance: { h, _, _, _ in h }, checkState: { _, _ in [] }, cloneHistory: { $0 }, encodeHistory: { h in events.append("legacy_history"); return Array(String(h).utf8) }, decodeHistory: { Int(String(decoding: $0, as: UTF8.self))! })
                if c["history_mode"] as? String == "into" {
                    o.encodeHistory = { _ in throw ModelError("unexpected legacy history") }
                    o.encodeHistoryInto = { h, sink in events.append("into_history"); try sink.extend(Array(String(h).utf8)) }
                }
                return o
            }
            func actions(_ values: [[String: Any]], _ sink: EncodeBuffer) throws -> [[String: Any]] {
                var out: [[String: Any]] = []
                for a in values {
                    var ok = true, children: [[String: Any]]? = nil
                    do {
                        switch a["kind"] as! String {
                        case "append": try sink.extend(codecBytes(a["hex"] as! String))
                        case "legacy": try sink.extendFromVec(codecBytes(a["hex"] as! String))
                        default:
                            try sink.framed { child in
                                children = try actions(a["actions"] as! [[String: Any]], child)
                                if a["callback_error"] as? Bool == true { throw ModelError("fixture codec error") }
                                if a["ignore_child_errors"] as? Bool != true, children!.contains(where: { $0["ok"] as? Bool == false }) { throw ModelError("child failed") }
                            }
                        }
                    } catch { ok = false }
                    var row: [String: Any] = ["ok": ok]
                    row["children"] = children ?? []
                    out.append(row)
                }
                return out
            }
            var finish = true
            do {
                try EncodeBuffer.withBuffer(&bytes, maximum: maximum) { sink in
                    switch op {
                    case "buffer": results = try actions(c["actions"] as! [[String: Any]], sink)
                    case "dispatch", "automatic":
                        var m = model(c["mode"] as! String)
                        if op == "automatic" { m = try Auto(m, equalInputs: ==).generated.model }
                        let codec = m.codec!
                        switch c["encoder"] as! String {
                        case "state": try encodeStateInto(codec, state: 12, into: sink)
                        case "input": try encodeInputInto(codec, input: 12, into: sink)
                        default: try encodeOutputInto(codec, output: 12, into: sink)
                        }
                    case "oracle", "nested":
                        let adapter = WithOracle(model(c["mode"] as! String), oracle: oracle())
                        if op == "nested" {
                            let inner = adapter.model
                            let outer = Oracle<OracleState<Int,Int>,Int,Int,Int>(metadata: { Metadata(name: "outer") }, initialState: { 56 }, advance: { h, _, _, _ in h }, checkState: { _, _ in [] }, cloneHistory: { $0 }, encodeHistory: { _ in throw ModelError("legacy") }, decodeHistory: { Int(String(decoding: $0, as: UTF8.self))! }, encodeHistoryInto: { h, child in events.append("into_history"); try child.extend(Array(String(h).utf8)) })
                            try WithOracle(inner, oracle: outer).encodeStateInto(OracleState(model: OracleState(model: 12, oracle: 34), oracle: 56), sink)
                        } else { try adapter.encodeStateInto(OracleState(model: 12, oracle: 34), sink) }
                    case "paired":
                        let p = Projection<Int,Int,Int,Int,Int,Int>(metadata: { Metadata(name: "projection") }, actualState: { $0 }, referenceState: { $0 }, actualEffect: { $0 }, referenceEffect: { $0 }, equalState: ==, equalEffect: ==)
                        let paired = Paired(reference: model("into"), projection: p)
                        try WithOracle(model("into"), oracle: paired.oracle).encodeStateInto(OracleState(model: 12, oracle: 34), sink)
                    default: XCTFail("unknown \(op)")
                    }
                }
            } catch { finish = false }
            let result: [String: Any] = ["finish_ok": finish, "encoded_hex": finish ? codecHex(bytes) as Any : NSNull(), "results": results, "events": events]
            XCTAssertEqual(try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]), try JSONSerialization.data(withJSONObject: c["expected"]!, options: [.sortedKeys]), "\(name): \(result)")
        }
    }
    func testEncodeLeasesAndCallbackFailureCannotPublish() throws {
        var bytes: [UInt8] = []; bytes.reserveCapacity(128)
        var rootLease: EncodeBuffer?, childLease: EncodeBuffer?
        try EncodeBuffer.withBuffer(&bytes, maximum: 32) { sink in
            rootLease = sink
            try sink.framed { child in childLease = child; try child.extend([7]) }
            XCTAssertThrowsError(try childLease!.extend([8]))
            try sink.extend([9]); try sink.finish()
        }
        XCTAssertEqual(bytes, [1,0,0,0,0,0,0,0,7,9]); XCTAssertGreaterThanOrEqual(bytes.capacity, 128)
        for lease in [rootLease!,childLease!] {
            XCTAssertThrowsError(try lease.extend([])); XCTAssertThrowsError(try lease.finish()); XCTAssertThrowsError(try lease.count)
            XCTAssertThrowsError(try lease.framed { _ in XCTFail("expired frame invoked") })
        }
        let old = rootLease!
        XCTAssertThrowsError(try EncodeBuffer.withBuffer(&bytes, maximum: 32) { sink in
            XCTAssertThrowsError(try old.extend([1]))
            do { try sink.framed { child in childLease = child; try child.extend([1]); throw ModelError("nested failure") } } catch { }
            XCTAssertThrowsError(try sink.extend([])); XCTAssertThrowsError(try sink.finish())
        })
        XCTAssertThrowsError(try childLease!.extend([]))
        // Swallowing a nested codec callback error must poison the outer batch.
        var codec = codecCounter().codec!
        codec.encodeStateInto = { _, _ in throw ModelError("child codec error") }
        XCTAssertThrowsError(try EncodeBuffer.withBuffer(&bytes, maximum: 32) { sink in
            do { try encodeStateInto(codec, state: 1, into: sink) } catch { }
        })
    }
    func testRichReplayAllFlagsFailureIdentityAndObserverPriority() throws {
        var original = codecCounter()
        original.checkState = { $0 == 0 ? [.passed("state")] : [.failed("bad", "old")] }
        let trace = try Statelessness.record(original, inputs: InputIterator(values: [1, 9]))
        var changed = original, steps = 0, observations: [ReplayObservation<Int,Int,Int>] = []
        changed.step = { s, i in steps += 1; return Transition(state: s + i + 1, outputs: [99], disposition: .rejected("changed")) }
        changed.checkState = { $0 == 0 ? [.passed("state")] : [.failed("bad", "new")] }
        let report = try replayWithObservations(changed, trace: trace) { observation, report in
            observations.append(observation)
            if case let .turn(turn) = observation {
                XCTAssertEqual(turn.stateCheckCount, 1); XCTAssertEqual(turn.before, 0); XCTAssertEqual(turn.input, 1)
                XCTAssertEqual(turn.transition.state, 2); XCTAssertEqual(turn.actualOutputs, [[57,57]])
                XCTAssertEqual(turn.differences, ReplayDifferences(disposition: true, outputs: true, state: true, checks: true))
                XCTAssertEqual(report.field, "disposition"); XCTAssertEqual(report.stepsVerified, 0); XCTAssertTrue(report.failureReproduced)
            }
        }
        XCTAssertEqual(report.outcome, "diverged"); XCTAssertEqual(steps, 1); XCTAssertEqual(observations.count, 2)
        var errors = 0
        XCTAssertThrowsError(try replayWithObservations(original, trace: trace) { observation, _ in
            switch observation {
            case .initial: throw ModelError("direct observer failure")
            case .error: errors += 1
            case .turn: XCTFail("continued after observer failure")
            }
        }) { error in
            let error = error as! ReplayError<Int,Int,Int>
            XCTAssertNil(error.observerError); XCTAssertTrue(String(describing: error.error).contains("replay observer"))
            guard case let .initial(_, checks) = error.actual else { return XCTFail("lost initial actual") }
            XCTAssertEqual(checks, [.passed("state")])
        }
        XCTAssertEqual(errors, 0)
    }
    func testReplayErrorEvidenceStagesAndNoRepeatedExecution() throws {
        let baseline = codecCounter(), trace = try Statelessness.record(baseline, inputs: InputIterator(values: [1]))
        for stage in ["decode", "initial-encoding", "initial-check", "input-encoding", "step", "state-check", "edge-check", "output-encoding", "post-encoding", "return-snapshot", "user-snapshot-text"] {
            var m = baseline, reducerCalls = 0, stateCalls = 0, outputClones = 0, notified = 0
            m.step = { s, i in reducerCalls += 1; if stage == "step" { throw ModelError("fixture failure") }; return Transition(state: s+i, outputs: [s]) }
            m.checkStateInto = { s, sink in
                stateCalls += 1
                if (stage == "initial-check" && s == 0) || (stage == "state-check" && s == 1) { try sink.push(.passed("partial")); throw ModelError("fixture failure") }
                if stage == "user-snapshot-text" && s == 1 { throw ModelError("user snapshot: failed") }
                try sink.push(.passed("state"))
            }
            m.checkTransitionInto = { _, _, _, sink in if stage == "edge-check" { throw ModelError("fixture failure") }; try sink.push(.passed("edge")) }
            if stage == "decode" { m.codec!.decodeState = { _ in throw ModelError("fixture failure") } }
            m.codec!.encodeStateInto = { s, sink in
                if (stage == "initial-encoding" && s == 0) || (stage == "post-encoding" && s == 1) { throw ModelError("fixture failure") }
                try sink.extend(Array(String(s).utf8))
            }
            if stage == "input-encoding" { m.codec!.encodeInputInto = { _, _ in throw ModelError("fixture failure") } }
            if stage == "output-encoding" { m.codec!.encodeOutputInto = { _, _ in throw ModelError("fixture failure") } }
            if stage == "return-snapshot" { m.cloneOutput = { value in outputClones += 1; throw ModelError("snapshot unavailable \(value)") } }
            XCTAssertThrowsError(try replayWithObservations(m, trace: trace) { observation, _ in
                if case let .error(_, actual, _, reason) = observation {
                    notified += 1
                    if stage == "return-snapshot" { XCTAssertNil(actual); XCTAssertNotNil(reason) }
                    if stage == "user-snapshot-text" { XCTAssertNotNil(actual); XCTAssertNil(reason) }
                    throw ModelError("secondary observer failure")
                }
            }, stage) { error in
                let error = error as! ReplayError<Int,Int,Int>
                XCTAssertNotNil(error.observerError, stage)
                switch stage {
                case "decode", "input-encoding", "step": XCTAssertNil(error.actual, stage)
                case "initial-encoding", "initial-check":
                    guard case let .initial(_, checks) = error.actual else { return XCTFail("lost initial \(stage)") }
                    XCTAssertNil(checks)
                default:
                    guard case let .turn(_, before, input, turn, checks, count) = error.actual else { return XCTFail("lost turn \(stage)") }
                    XCTAssertEqual(before, 0); XCTAssertEqual(input, 1); XCTAssertEqual(turn.state, 1)
                    if ["output-encoding", "post-encoding"].contains(stage) { XCTAssertEqual(checks, [.passed("state"), .passed("edge")]); XCTAssertEqual(count, 1) }
                    else { XCTAssertNil(checks); XCTAssertNil(count) }
                }
            }
            XCTAssertEqual(notified, 1, stage); XCTAssertLessThanOrEqual(reducerCalls, 1, stage); XCTAssertLessThanOrEqual(stateCalls, 2, stage)
            if stage == "return-snapshot" { XCTAssertEqual(outputClones, 1) }
        }
    }
    func testBoundedArtifactAllNineQuotasBeforeDecodeAndIdentityFirst() throws {
        var m = codecCounter(), decodes = 0
        let portable = try Statelessness.record(m, inputs: InputIterator(values: [1]))
        let trace = try AuditTrace(portable: portable, config: RunConfig(strategy: "x", parameters: [AuditParameter("k", "v")]))
        m.codec!.decodeState = { bytes in decodes += 1; return Int(String(decoding: bytes, as: UTF8.self))! }
        for quota in ["total", "frame", "blob", "string", "steps", "checks", "outputs", "parameters", "items"] {
            var limits = BinaryTraceLimits()
            switch quota {
            case "total": limits.maxTotalBytes = 0
            case "frame": limits.maxFrameBytes = 0
            case "blob": limits.maxBlobBytes = 0
            case "string": limits.maxStringBytes = 0
            case "steps": limits.maxSteps = 0
            case "checks": limits.maxChecksPerStep = 0
            case "outputs": limits.maxOutputsPerStep = 0
            case "parameters": limits.maxParameters = 0
            default: limits.maxItems = 0
            }
            var notifications = 0
            XCTAssertThrowsError(try replayWithObservationsBounded(m, trace: trace, limits: limits) { observation, _ in
                if case let .error(sequence, actual, _, _) = observation { notifications += 1; XCTAssertEqual(sequence, 0); XCTAssertNil(actual) }
                else { XCTFail("artifact executed") }
            }, quota)
            XCTAssertEqual(notifications, 1); XCTAssertEqual(decodes, 0)
        }
        m.codec!.metadata = { Metadata(name: "foreign") }
        var none = BinaryTraceLimits(); none.maxTotalBytes = 0
        let incompatible = try replayWithObservationsBounded(m, trace: trace, limits: none) { _, _ in XCTFail("incompatible notified") }
        XCTAssertEqual(incompatible.outcome, "incompatible"); XCTAssertEqual(decodes, 0)
    }
    func testAuditRecordPreferredCodecsStampAndCoherentFailurePrefix() throws {
        var m = codecCounter(), events: [String] = []
        m.codec!.encodeState = { _ in throw ModelError("legacy state") }; m.codec!.encodeInput = { _ in throw ModelError("legacy input") }; m.codec!.encodeOutput = { _ in throw ModelError("legacy output") }
        m.codec!.encodeStateInto = { s, sink in events.append("state:\(s)"); try sink.extend(Array(String(s).utf8)) }
        m.codec!.encodeInputInto = { i, sink in events.append("input:\(i)"); try sink.extend(Array(String(i).utf8)) }
        m.codec!.encodeOutputInto = { o, sink in events.append("output:\(o)"); try sink.extend(Array(String(o).utf8)) }
        let config = RunConfig(strategy: "native", seed: 7, parameters: [AuditParameter("k", "v"), AuditParameter("k", "v2")])
        let trace = try recordAudit(m, inputs: InputIterator(values: [1]), config: config)
        XCTAssertEqual(trace.config.parameters, config.parameters + [AuditParameter("stateless.engine.version", statelessnessVersion), AuditParameter("stateless.engine.build", "native-swift-v1")])
        XCTAssertEqual(events, ["state:0", "input:1", "output:0", "state:1"])
        _ = try encodeAuditTrace(trace)
        events = []
        let report = try replayWithObservationsBounded(m, trace: trace) { _, _ in }
        XCTAssertEqual(report.stepsVerified, 1); XCTAssertEqual(report.outcome, "exact")
        XCTAssertEqual(events, ["state:0", "input:1", "output:0", "state:1"])
        m.codec!.encodeOutputInto = { o, sink in
            if o == 1 { try sink.extend([0]); throw ModelError("failed second output") }
            try sink.extend(Array(String(o).utf8))
        }
        let failed = try recordAudit(m, inputs: InputIterator(values: [1,1]), config: config)
        XCTAssertEqual(failed.steps.count, 1)
        guard case let .modelError(reason) = failed.termination else { return XCTFail("lost recording error") }
        XCTAssertTrue(reason.contains("failed second output")); _ = try encodeAuditTrace(failed)
        XCTAssertThrowsError(try recordAudit(m, inputs: InputIterator(values: []), config: RunConfig(strategy: "x", parameters: [AuditParameter("stateless.engine.user", "forged")])))
    }
    func testBoundedActualSharedFrameTotalFooterAndItemBudgets() throws {
        let base = codecCounter(), portable = try Statelessness.record(base, inputs: InputIterator(values: [1]))
        let config = RunConfig(strategy: "x")
        let expected = try AuditTrace(portable: portable, config: config)
        let body = [UInt8](repeating: 7, count: 30)
        let actualStep = TraceStep(input: [49], disposition: .accepted, outputs: [body, body], postState: [49], checks: [.passed("state"), .passed("edge")])
        let actualWire = try encodeAuditTrace(AuditTrace(metadata: expected.metadata, config: config, initialState: expected.initialState, initialChecks: expected.initialChecks, steps: [actualStep], termination: .completed))
        func u32(_ offset: Int) -> UInt64 { (0..<4).reduce(0) { $0 | UInt64(actualWire[offset + $1]) << ($1 * 8) } }
        let runPayload = u32(13), stepOffset = 12 + 9 + Int(runPayload), stepPayload = u32(stepOffset + 1)
        XCTAssertGreaterThan(stepPayload, runPayload)
        for quota in ["frame-fit", "frame-over", "total-fit", "total-over", "outputs", "items", "checks", "reason"] {
            var m = base, calls = 0, postEncodes = 0
            m.step = { s, i in Transition(state: s+i, outputs: [0,1], disposition: quota == "reason" ? .rejected(String(repeating: "é", count: 20)) : .accepted) }
            m.codec!.encodeOutputInto = { _, sink in calls += 1; try sink.extend(body) }
            m.codec!.encodeStateInto = { s, sink in if s != 0 { postEncodes += 1 }; try sink.extend(Array(String(s).utf8)) }
            var limits = BinaryTraceLimits()
            switch quota {
            case "frame-fit": limits.maxFrameBytes = stepPayload
            case "frame-over": limits.maxFrameBytes = stepPayload - 1
            case "total-fit": limits.maxTotalBytes = UInt64(actualWire.count)
            case "total-over": limits.maxTotalBytes = UInt64(actualWire.count - 1)
            case "outputs": limits.maxOutputsPerStep = 1
            case "items": limits.maxItems = 5
            case "checks":
                limits.maxChecksPerStep = 2
                m.checkState = { $0 == 0 ? [.passed("state")] : [.passed("state"), .passed("extra"), .passed("extra2")] }
            default: limits.maxStringBytes = 32
            }
            var completed = 0
            do {
                let report = try replayWithObservationsBounded(m, trace: expected, limits: limits) { observation, _ in if case .turn = observation { completed += 1 } }
                XCTAssertTrue(quota.hasSuffix("fit"), quota); XCTAssertEqual(report.outcome, "diverged"); XCTAssertEqual(report.field, "outputs")
            } catch {
                XCTAssertFalse(quota.hasSuffix("fit"), "\(quota): \(error)")
                let error = error as! ReplayError<Int,Int,Int>
                guard case let .turn(_, _, _, _, checks, count) = error.actual else { XCTFail("lost actual \(quota)"); continue }
                XCTAssertNotNil(checks); XCTAssertNotNil(count)
            }
            if ["outputs", "items", "checks", "reason"].contains(quota) { XCTAssertEqual(calls, 0, quota); XCTAssertEqual(postEncodes, 0) }
            else { XCTAssertEqual(calls, 2, quota); XCTAssertEqual(postEncodes, 1) }
            XCTAssertEqual(completed, quota.hasSuffix("fit") ? 1 : 0, quota)
        }
    }
    func testRichObservationsDetachMutableValuesAndEncodedStorage() throws {
        let codec = Codec<ReplayBox,ReplayBox,ReplayBox>(metadata: { Metadata(name: "box") }, encodeState: { [UInt8($0.value)] }, decodeState: { ReplayBox(Int($0[0])) }, encodeInput: { [UInt8($0.value)] }, decodeInput: { ReplayBox(Int($0[0])) }, encodeOutput: { [UInt8($0.value)] })
        let m = Model<ReplayBox,ReplayBox,ReplayBox>(initialState: { ReplayBox(0) }, step: { state, input in Transition(state: ReplayBox(state.value + input.value), outputs: [ReplayBox(state.value)]) }, checkState: { _ in [.passed("ok")] }, cloneState: { ReplayBox($0.value) }, cloneInput: { ReplayBox($0.value) }, cloneOutput: { ReplayBox($0.value) }, codec: codec)
        let trace = try Statelessness.record(m, inputs: InputIterator(values: [ReplayBox(1),ReplayBox(1)]))
        var saved: [ReplayObservation<ReplayBox,ReplayBox,ReplayBox>] = []
        let report = try replayWithObservations(m, trace: trace) { observation, report in
            saved.append(observation)
            switch observation {
            case let .initial(value): value.state.value = 99
            case let .turn(value):
                XCTAssertEqual(value.before.value, report.stepsVerified - 1)
                value.before.value = 99; value.input.value = 99; value.transition.state.value = 99; value.transition.outputs[0].value = 99
            case .error: XCTFail("unexpected error")
            }
        }
        XCTAssertEqual(report.stepsVerified, 2); XCTAssertEqual(report.outcome, "exact")
        guard case let .turn(first) = saved[1], case let .turn(second) = saved[2] else { return XCTFail("missing turns") }
        XCTAssertEqual(first.actualInput, [1]); XCTAssertEqual(first.actualOutputs, [[0]]); XCTAssertEqual(first.actualState, [1])
        XCTAssertEqual(second.actualOutputs, [[1]]); XCTAssertEqual(second.actualState, [2])
        XCTAssertEqual(first.checks, [.passed("ok")]); XCTAssertEqual(first.stateCheckCount, 1)
        // Normal error notifications must likewise be detached from the recovery
        // values returned by the engine after an already produced turn.
        var failing = m
        failing.codec!.encodeOutputInto = { _, _ in throw ModelError("encode failure") }
        XCTAssertThrowsError(try replayWithObservations(failing, trace: trace) { observation, _ in
            if case let .error(_, actual, _, _) = observation, case let .turn(_, before, input, turn, _, _) = actual {
                before.value = 88; input.value = 88; turn.state.value = 88; turn.outputs[0].value = 88
            }
        }) { error in
            let error = error as! ReplayError<ReplayBox,ReplayBox,ReplayBox>
            guard case let .turn(_, before, input, turn, checks, count) = error.actual else { return XCTFail("lost recovery") }
            XCTAssertEqual(before.value, 0); XCTAssertEqual(input.value, 1); XCTAssertEqual(turn.state.value, 1); XCTAssertEqual(turn.outputs[0].value, 0)
            XCTAssertEqual(checks, [.passed("ok")]); XCTAssertEqual(count, 1)
        }
    }
}

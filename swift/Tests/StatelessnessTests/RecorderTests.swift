import Foundation
import XCTest
import Statelessness

private final class RecorderFormattingError: Error, CustomStringConvertible, @unchecked Sendable {
    let render: () -> String
    init(_ render: @escaping () -> String) { self.render = render }
    var description: String { render() }
}
final class RecorderTests: XCTestCase {
    private func model() -> Model<Int,Int,Int> {
        Model(initialState: { throw ModelError("initial must not run") }, step: { _,_ in throw ModelError("reducer must not run") }, checkState: { [.passed("state-\($0)")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, checkTransition: { _,_,_ in [.passed("edge")] }, codec: Codec(metadata: { Metadata(name: "recorder", build: "test") }, encodeState: { [UInt8($0)] }, decodeState: { Int($0[0]) }, encodeInput: { [UInt8($0)] }, decodeInput: { Int($0[0]) }, encodeOutput: { [UInt8($0)] }))
    }
    func testCertifiedBatchPreflightBeforeOutputEncodingAndAdmission() throws {
        var m = model(), outputCalls = 0, inputCalls = 0, stateCalls = 0
        m.checkState = { state in stateCalls += 1; return [.passed(state == 4 ? "initial" : String(repeating: "x", count: 1100))] }
        m.codec!.encodeInputInto = { i, sink in inputCalls += 1; try sink.extend([UInt8(i)]) }
        m.codec!.encodeOutputInto = { o, sink in outputCalls += 1; try sink.extend([UInt8(o)]) }
        let initial = try checkInitial(m, state: 4)
        let checked = try checkTurn(m, before: 4, input: 1, transition: Transition(state: 5, outputs: [50]), sequence: 1)
        var limits = BinaryTraceLimits(); limits.maxFrameBytes = 1024
        let recorder = try Recorder(checkedInitial: initial, config: RunConfig(strategy: "runtime"), options: RecorderOptions(limits: limits))
        XCTAssertThrowsError(try recorder.observeChecked(checked)) { XCTAssertTrue($0 is ModelError) }
        XCTAssertEqual(inputCalls, 1); XCTAssertEqual(outputCalls, 0); XCTAssertEqual(stateCalls, 2)
        XCTAssertEqual(recorder.observedSteps, 0); XCTAssertEqual(recorder.retainedSteps, 0); XCTAssertTrue(recorder.isFrozen)
        XCTAssertEqual(recorder.snapshot().initialChecks, [.passed("initial")])
        _ = try encodeAuditTrace(recorder.snapshot(), limits: limits)
        var wide = model()
        wide.checkState = { _ in [.passed(String(repeating: "x", count: 1100))] }
        let huge = try checkInitial(wide, state: 4)
        XCTAssertThrowsError(try Recorder(checkedInitial: huge, config: RunConfig(strategy: "runtime"), options: RecorderOptions(maxRetainedBytes: 1000)))
        XCTAssertThrowsError(try Recorder(checkedInitial: huge, config: RunConfig(strategy: "runtime"), options: RecorderOptions(limits: limits)))
    }
    func testCallbackStopsAndNestedMutationCannotPublishOrReplaceFirstCause() throws {
        for mode in ["encode-return", "encode-throw", "check-return", "nested-observe"] {
            var m = model(), recorder: Recorder?, token: CheckedTurn<Int,Int,Int>?, armed = false
            m.codec!.encodeInputInto = { value, sink in
                if armed && mode.hasPrefix("encode") {
                    _ = try recorder!.stopWithError(ModelError("first cause"))
                    if mode == "encode-throw" { throw ModelError("later encode error") }
                }
                if armed && mode == "nested-observe" {
                    XCTAssertThrowsError(try recorder!.observeChecked(token!))
                }
                try sink.extend([UInt8(value)])
            }
            m.checkStateInto = { _, sink in
                if armed && mode == "check-return" { _ = try recorder!.stopWithError(ModelError("first cause")) }
                try sink.push(.passed("state"))
            }
            token = try checkTurn(m, before: 4, input: 1, transition: Transition(state: 5), sequence: 1)
            recorder = try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"))
            let initial = recorder!.snapshot()
            armed = true
            XCTAssertThrowsError(try recorder!.observe(model: m, before: 4, input: 1, transition: Transition(state: 5)))
            XCTAssertEqual(recorder!.observedSteps, 0, mode); XCTAssertEqual(recorder!.retainedSteps, 0, mode); XCTAssertTrue(recorder!.isFrozen)
            let expected = mode == "nested-observe" ? "reentrant" : "first cause"
            guard case let .modelError(first) = recorder!.snapshot().termination else { XCTFail("lost stop \(mode)"); continue }
            XCTAssertTrue(first.contains(expected), mode)
            _ = try recorder!.stopWithError(ModelError("last cause"))
            XCTAssertThrowsError(try recorder!.observeChecked(token!))
            XCTAssertEqual(recorder!.snapshot().termination, .modelError(first))
            XCTAssertEqual(recorder!.snapshot().initialState, initial.initialState); XCTAssertEqual(recorder!.snapshot().initialChecks, initial.initialChecks)
            _ = try encodeAuditTrace(recorder!.snapshot())
        }
    }
    func testStopReasonFormatterCannotReenterOrReplaceFirstFreeze() throws {
        let m = model(), recorder = try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"))
        let token = try checkTurn(m, before: 4, input: 1, transition: Transition(state: 5), sequence: 1)
        var descriptions = 0
        let error = RecorderFormattingError {
            descriptions += 1; XCTAssertTrue(recorder.isFrozen)
            do { _ = try recorder.observeChecked(token); XCTFail("formatter reentered recorder") } catch { }
            do { _ = try recorder.stopWithError(ModelError("nested replacement")) } catch { XCTFail("nested stop unexpectedly failed") }
            XCTAssertEqual(recorder.snapshot().steps.count, 0)
            return "first formatter cause"
        }
        _ = try recorder.stopWithError(error)
        XCTAssertEqual(descriptions, 1); XCTAssertTrue(recorder.isFrozen)
        XCTAssertEqual(recorder.observedSteps, 0); XCTAssertEqual(recorder.retainedSteps, 0)
        XCTAssertEqual(recorder.snapshot().termination, .modelError("first formatter cause"))
        _ = try encodeAuditTrace(recorder.snapshot())
    }
    func testCumulativeTotalBytesPrecedeCumulativeItems() throws {
        let m = model()
        let baseline = try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"))
        let actual = UInt64(try encodeAuditTrace(baseline.snapshot()).count)
        var limits = BinaryTraceLimits()
        limits.maxTotalBytes = actual + 260 + 57 - 1
        limits.maxItems = 10 // RUN has9; STEP has3, each individually fits.
        let recorder = try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"), options: RecorderOptions(limits: limits))
        XCTAssertThrowsError(try recorder.observe(model: m, before: 4, input: 1, transition: Transition(state: 5))) { error in
            XCTAssertTrue(String(describing: error).contains("trace exceeds total bytes limit"), String(describing: error))
            XCTAssertFalse(String(describing: error).contains("aggregate items"))
        }
        XCTAssertTrue(recorder.isFrozen); XCTAssertEqual(recorder.observedSteps, 0)
        _ = try encodeAuditTrace(recorder.snapshot(), limits: limits)
    }
    func testIntoTraceTransfersOnceAndRejectsAllLaterMutations() throws {
        let m = model(), recorder = try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"))
        let checked = try checkTurn(m, before: 4, input: 1, transition: Transition(state: 5), sequence: 1)
        try recorder.observeChecked(checked)
        let prefix = recorder.snapshot(), transferred = try recorder.intoTrace()
        XCTAssertEqual(transferred, prefix); XCTAssertTrue(recorder.isFrozen)
        XCTAssertEqual(recorder.observedSteps, 1); XCTAssertEqual(recorder.retainedSteps, 1)
        for operation: () throws -> Void in [
            { _ = try recorder.intoTrace() },
            { _ = try recorder.observeChecked(checked) },
            { _ = try recorder.observe(model: m, before: 5, input: 1, transition: Transition(state: 6)) },
            { _ = try recorder.stopWithError(ModelError("late stop")) }
        ] {
            XCTAssertThrowsError(try operation()) { error in
                XCTAssertTrue(error is ModelError); XCTAssertTrue(String(describing: error).contains("transferred"))
            }
            XCTAssertEqual(recorder.snapshot(), transferred)
        }
        var outside = transferred.initialChecks; outside.removeAll()
        XCTAssertEqual(recorder.snapshot(), transferred)
        XCTAssertEqual(try encodeAuditTrace(transferred), try encodeAuditTrace(recorder.snapshot()))
        let alreadyFailed = try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"))
        _ = try alreadyFailed.stopWithError(ModelError("first"))
        let failed = try alreadyFailed.intoTrace()
        XCTAssertEqual(failed.termination, .modelError("first")); XCTAssertThrowsError(try alreadyFailed.intoTrace())
    }
    func testCallbackTransferIsRejectedWithoutPublishingCandidate() throws {
        var m = model(), recorder: Recorder?, armed = false
        m.codec!.encodeInputInto = { value, sink in
            if armed { XCTAssertThrowsError(try recorder!.intoTrace()) }
            try sink.extend([UInt8(value)])
        }
        recorder = try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"))
        armed = true
        XCTAssertThrowsError(try recorder!.observe(model: m, before: 4, input: 1, transition: Transition(state: 5)))
        XCTAssertTrue(recorder!.isFrozen); XCTAssertEqual(recorder!.observedSteps, 0)
        guard case let .modelError(reason) = recorder!.snapshot().termination else { return XCTFail("transfer rejection did not freeze") }
        XCTAssertTrue(reason.contains("transfer is reentrant"))
        let trace = try recorder!.intoTrace()
        XCTAssertEqual(trace.steps.count, 0); XCTAssertThrowsError(try recorder!.intoTrace())
    }
    func testRingPromotesOnlyStatePrefixAndWireAccounting() throws {
        let m = model(), recorder = try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"), options: RecorderOptions(maxSteps: 1))
        try recorder.observe(model: m, before: 4, input: 1, transition: Transition(state: 5))
        try recorder.observe(model: m, before: 5, input: 1, transition: Transition(state: 6))
        let trace = recorder.snapshot()
        XCTAssertEqual(trace.initialState, [5]); XCTAssertEqual(trace.initialChecks, [.passed("state-5")])
        XCTAssertEqual(trace.steps.count, 1); XCTAssertEqual(recorder.observedSteps, 2); XCTAssertEqual(recorder.evictedSteps, 1)
        let bytes = try encodeAuditTrace(trace)
        XCTAssertEqual(try recorder.retainedBytes, UInt64(bytes.count)); XCTAssertEqual(try decodeAuditTrace(bytes), trace)
    }
    func testCheckedHandoffDoesNotRecheckAndTokenCanFeedTwoRecorders() throws {
        var m = model(); var stateCalls = 0, edgeCalls = 0
        m.checkState = { _ in stateCalls += 1; return [.passed("state")] }
        m.checkTransition = { _,_,_ in edgeCalls += 1; return [.passed("edge")] }
        let initial = try checkInitial(m, state: 4)
        let turn = try checkTurn(m, before: 4, input: 1, transition: Transition(state: 5), sequence: 11)
        let a = try Recorder(checkedInitial: initial, sequenceOrigin: 10, config: RunConfig(strategy: "runtime"))
        let b = try Recorder(checkedInitial: initial, sequenceOrigin: 10, config: RunConfig(strategy: "runtime"))
        try a.observeChecked(turn); try b.observeChecked(turn)
        XCTAssertEqual(stateCalls, 2); XCTAssertEqual(edgeCalls, 1)
        XCTAssertThrowsError(try a.observeChecked(turn)); XCTAssertTrue(a.isFrozen)
        XCTAssertEqual(a.retainedSteps, 1); XCTAssertEqual(b.retainedSteps, 1)
        XCTAssertEqual(a.snapshot().config.parameters.first { $0.key == "stateless.monitor.sequence_origin" }?.value, "10")
    }
    func testCoverageOrderingOriginOverflowAndMetadataAreEnforced() throws {
        let m = model(), initial = try checkInitial(m, state: 4)
        let sampled = try checkTurn(m, before: 4, input: 1, transition: Transition(state: 5), sequence: 1, policy: CheckPolicy(stateEvery: 2))
        let recorder = try Recorder(checkedInitial: initial, config: RunConfig(strategy: "runtime"))
        XCTAssertThrowsError(try recorder.observeChecked(sampled)); XCTAssertEqual(recorder.retainedSteps, 0)
        let overflow = try Recorder(checkedInitial: initial, sequenceOrigin: UInt64.max, config: RunConfig(strategy: "runtime"))
        let one = try checkTurn(m, before: 4, input: 1, transition: Transition(state: 5), sequence: 1)
        XCTAssertThrowsError(try overflow.observeChecked(one))
        var changed = m
        changed.codec = Codec(metadata: { Metadata(name: "different", build: "test") }, encodeState: { [UInt8($0)] }, decodeState: { Int($0[0]) }, encodeInput: { [UInt8($0)] }, decodeInput: { Int($0[0]) }, encodeOutput: { [UInt8($0)] })
        let identity = try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"))
        XCTAssertThrowsError(try identity.observe(model: changed, before: 4, input: 1, transition: Transition(state: 5)))
        XCTAssertEqual(identity.retainedSteps, 0)
    }
    func testErrorsFreezeCoherentPrefixAndCannotOverwriteFailure() throws {
        let m = model(), recorder = try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"))
        try recorder.observe(model: m, before: 4, input: 1, transition: Transition(state: 5))
        XCTAssertThrowsError(try recorder.observe(model: m, before: 8, input: 1, transition: Transition(state: 9)))
        let frozen = recorder.snapshot()
        try recorder.stopWithError(ModelError("later")); XCTAssertEqual(recorder.snapshot(), frozen)
        var failing = model(); failing.checkState = { _ in [.failed("bad", "property")] }
        let initialFailure = try Recorder(model: failing, state: 4, config: RunConfig(strategy: "runtime"))
        XCTAssertEqual(initialFailure.termination, .propertyFailed)
        try initialFailure.stopWithError(ModelError("observer")); XCTAssertEqual(initialFailure.termination, .propertyFailed)
        XCTAssertThrowsError(try initialFailure.observe(model: m, before: 4, input: 1, transition: Transition(state: 5)))
    }
    func testAllFormatQuotasAndRetainedBudgetRejectWithoutEviction() throws {
        let m = model()
        for quota in 0..<9 {
            var limits = BinaryTraceLimits()
            switch quota {
            case 0: limits.maxTotalBytes = 1
            case 1: limits.maxFrameBytes = 1
            case 2: limits.maxBlobBytes = 0
            case 3: limits.maxStringBytes = 0
            case 4: limits.maxSteps = 0
            case 5: limits.maxChecksPerStep = 0
            case 6: limits.maxOutputsPerStep = 0
            case 7: limits.maxParameters = 0
            default: limits.maxItems = 0
            }
            if quota == 6 {
                let r = try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"), options: RecorderOptions(limits: limits))
                XCTAssertThrowsError(try r.observe(model: m, before: 4, input: 1, transition: Transition(state: 5, outputs: [1])))
                XCTAssertEqual(r.retainedSteps, 0)
            } else {
                XCTAssertThrowsError(try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"), options: RecorderOptions(limits: limits)), "quota \(quota)")
            }
        }
        XCTAssertThrowsError(try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"), options: RecorderOptions(maxRetainedBytes: 1)))
        var limits = BinaryTraceLimits(); limits.maxBlobBytes = 1
        let r = try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"), options: RecorderOptions(maxSteps: 1, limits: limits))
        try r.observe(model: m, before: 4, input: 1, transition: Transition(state: 5))
        var oversized = m; oversized.codec?.encodeState = { $0 == 6 ? [6,6] : [UInt8($0)] }
        XCTAssertThrowsError(try r.observe(model: oversized, before: 5, input: 1, transition: Transition(state: 6)))
        XCTAssertEqual(r.retainedSteps, 1); XCTAssertEqual(r.evictedSteps, 0); XCTAssertEqual(r.snapshot().initialState, [4])
    }
}


extension RecorderTests {
    func testDispositionFramePreflightRunsBeforeInputAndChecks() throws {
        var m = model(); var inputCalls = 0, checkCalls = 0
        m.codec?.encodeInput = { inputCalls += 1; return [UInt8($0)] }
        m.checkState = { _ in checkCalls += 1; return [] }
        var limits = BinaryTraceLimits(); limits.maxFrameBytes = 1024
        let r = try Recorder(model: m, state: 4, config: RunConfig(strategy: "runtime"), options: RecorderOptions(limits: limits))
        XCTAssertEqual(checkCalls, 1)
        XCTAssertThrowsError(try r.observe(model: m, before: 4, input: 1, transition: Transition(state: 5, disposition: .rejected(String(repeating: "x", count: 1025)))))
        XCTAssertEqual(inputCalls, 0); XCTAssertEqual(checkCalls, 1)
        XCTAssertEqual(r.retainedSteps, 0)
    }
    func testDifferentContextRepresentationsShareCanonicalEncoding() throws {
        let m = model(), initial = try checkInitial(m, state: 4)
        var calls = 0
        let text = Model<String,String,String>(initialState: { throw ModelError("initial") }, step: { _,_ in throw ModelError("step") }, checkState: { _ in calls += 1; return [.passed("state")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, codec: Codec(metadata: { Metadata(name: "recorder", build: "test") }, encodeState: { [UInt8($0)!] }, decodeState: { String($0[0]) }, encodeInput: { [UInt8($0)!] }, decodeInput: { String($0[0]) }, encodeOutput: { [UInt8($0)!] }))
        let turn = try checkTurn(text, before: "4", input: "1", transition: Transition(state: "5"), sequence: 1)
        let a = try Recorder(checkedInitial: initial, config: RunConfig(strategy: "runtime"))
        let b = try Recorder(checkedInitial: initial, config: RunConfig(strategy: "runtime"))
        try a.observeChecked(turn); try b.observeChecked(turn)
        XCTAssertEqual(calls, 1); XCTAssertEqual(a.snapshot().steps.first?.postState, [5]); XCTAssertEqual(a.snapshot(), b.snapshot())
    }
    func testOrdinaryCaptureKeepsPristineReferenceContext() throws {
        final class Box { var value: Int; init(_ value: Int) { self.value = value } }
        let checkpoint = Box(4), before = Box(4), input = Box(1), successor = Box(5), output = Box(90)
        var checkedValues: [Int] = [], edgeValues: [Int] = []
        let m = Model<Box,Box,Box>(initialState: { throw ModelError("initial") }, step: { _,_ in throw ModelError("step") }, checkState: { checkedValues.append($0.value); return [] }, cloneState: { Box($0.value) }, cloneInput: { Box($0.value) }, cloneOutput: { Box($0.value) }, checkTransition: { b,i,t in edgeValues = [b.value,i.value,t.state.value,t.outputs[0].value]; return [] }, codec: Codec(metadata: { Metadata(name: "reference", build: "test") }, encodeState: { value in
            checkpoint.value = 99; before.value = 99; input.value = 99; successor.value = 99; output.value = 99
            let result = UInt8(value.value); value.value = 88; return [result]
        }, decodeState: { Box(Int($0[0])) }, encodeInput: { [UInt8($0.value)] }, decodeInput: { Box(Int($0[0])) }, encodeOutput: { [UInt8($0.value)] }))
        let recorder = try Recorder(model: m, state: checkpoint, config: RunConfig(strategy: "runtime"))
        XCTAssertEqual(checkedValues, [4])
        before.value = 4; input.value = 1; successor.value = 5; output.value = 90
        try recorder.observe(model: m, before: before, input: input, transition: Transition(state: successor, outputs: [output]))
        XCTAssertEqual(checkedValues, [4,5]); XCTAssertEqual(edgeValues, [4,1,5,90])
        XCTAssertEqual(recorder.snapshot().initialState, [4]); XCTAssertEqual(recorder.snapshot().steps[0].postState, [5]); XCTAssertEqual(recorder.snapshot().steps[0].input, [1])
    }
    func testSharedRecorderCorpus() throws {
        let fallback = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("conformance/checked-results-v1.json").path
        let path = ProcessInfo.processInfo.environment["STATELESS_CHECKED_CORPUS"] ?? fallback
        let root = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as! [String:Any]
        let cases = root["recorder_cases"] as! [[String:Any]]
        func integer(_ value: Any) -> UInt64 { UInt64(String(describing: value))! }
        func checks(_ raw: Any) -> [Check] {
            (raw as! [[String:Any]]).map { x in
                let id = x["id"] as! String, details = x["details"] as! String
                switch x["status"] as! String { case "failed": return .failed(id, details); case "skipped": return .skipped(id, details); default: return .passed(id) }
            }
        }
        func turn(_ raw: [String:Any]) -> Transition<Int,Int> {
            let d = raw["disposition"] as! [String:Any], reason = d["reason"] as! String
            let disposition: Disposition
            switch d["kind"] as! String { case "ignored": disposition = .ignored(reason); case "rejected": disposition = .rejected(reason); default: disposition = .accepted }
            return Transition(state: raw["state"] as! Int, outputs: raw["outputs"] as! [Int], disposition: disposition)
        }
        for c in cases {
            let name = c["case"] as! String, rawModel = c["model"] as! [String:Any]
            func configured(_ definition: [String:Any], name: String = "same", encodeError: String? = nil) -> Model<Int,Int,Int> {
                var m = model()
                m.checkState = { _ in if let e = definition["state_error"] as? String { throw ModelError(e) }; return checks(definition["state_checks"]!) }
                m.checkTransition = { _,_,_ in if let e = definition["transition_error"] as? String { throw ModelError(e) }; return checks(definition["transition_checks"]!) }
                m.codec = Codec(metadata: { Metadata(name: name, build: "test") }, encodeState: { if encodeError == "state" { throw ModelError("encode state") }; return [UInt8($0)] }, decodeState: { Int($0[0]) }, encodeInput: { if encodeError == "input" { throw ModelError("encode input") }; return [UInt8($0)] }, decodeInput: { Int($0[0]) }, encodeOutput: { if encodeError == "output" { throw ModelError("encode output") }; return [UInt8($0)] })
                return m
            }
            let m = configured(rawModel), opts = c["options"] as! [String:Any], rawLimits = opts["limits"] as! [String:Any]
            var limits = BinaryTraceLimits()
            for (key,value) in rawLimits {
                let n = integer(value)
                switch key {
                case "max_total_bytes": limits.maxTotalBytes = n
                case "max_frame_bytes": limits.maxFrameBytes = n
                case "max_blob_bytes": limits.maxBlobBytes = n
                case "max_string_bytes": limits.maxStringBytes = n
                case "max_steps": limits.maxSteps = n
                case "max_checks_per_batch": limits.maxChecksPerStep = n
                case "max_outputs_per_step": limits.maxOutputsPerStep = n
                case "max_parameters": limits.maxParameters = n
                case "max_items": limits.maxItems = n
                default: XCTFail("unknown limit \(key)")
                }
            }
            let options = RecorderOptions(maxSteps: opts["max_steps"] as! Int, maxRetainedBytes: integer(opts["max_retained_bytes"]!), limits: limits)
            let cfg = c["config"] as! [String:Any]
            let config = RunConfig(strategy: cfg["strategy"] as! String, seed: (cfg["seed"] as? NSNumber)?.uint64Value, parameters: (cfg["parameters"] as! [[String]]).map { AuditParameter($0[0], $0[1]) })
            let expected = c["expected"] as! [String:Any]
            let recorder: Recorder
            do {
                if c["entry_mode"] as? String == "ordinary" {
                    recorder = try Recorder(model: m, state: c["initial_state"] as! Int, config: config, options: options)
                } else {
                    recorder = try Recorder(checkedInitial: checkInitial(m, state: c["initial_state"] as! Int), sequenceOrigin: integer(c["origin"]!), config: config, options: options)
                }
                XCTAssertNil(expected["initial_error_contains"] as? String, name)
            } catch {
                guard let expectedError = expected["initial_error_contains"] as? String else { XCTFail("\(name): unexpected initial \(error)"); continue }
                XCTAssertTrue(String(describing: error).contains(expectedError), "\(name): \(error)"); continue
            }
            for (action, expectedState) in zip(c["actions"] as! [[String:Any]], expected["states"] as! [[String:Any]]) {
                var error: Error?
                if let stop = action["stop_error"] as? String { try recorder.stopWithError(ModelError(stop)) }
                else {
                    do {
                        let active = configured(action["checks_model"] as? [String:Any] ?? rawModel, name: action["model_name"] as? String ?? "same", encodeError: action["encode_error"] as? String)
                        let before = action["before"] as! Int, input = action["input"] as! Int, transition = turn(action["transition"] as! [String:Any])
                        if action["mode"] as? String == "ordinary" { try recorder.observe(model: active, before: before, input: input, transition: transition) }
                        else {
                            let p = action["policy"] as! [String:Any]
                            let policy = CheckPolicy(stateEvery: integer(p["state_every"]!), transitionChecks: p["transition_checks"] as! Bool)
                            if action["representation"] as? String == "decimal-string" {
                                let codec = active.codec!
                                let text = Model<String,String,String>(initialState: { throw ModelError("initial must not run") }, step: { _,_ in throw ModelError("step must not run") }, checkState: { try active.checkState(Int($0)!) }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, checkTransition: { b,i,t in try active.checkTransition!(Int(b)!, Int(i)!, Transition(state: Int(t.state)!, outputs: t.outputs.map { Int($0)! }, disposition: t.disposition)) }, codec: Codec(metadata: codec.metadata, encodeState: { try codec.encodeState(Int($0)!) }, decodeState: { String(try codec.decodeState($0)) }, encodeInput: { try codec.encodeInput(Int($0)!) }, decodeInput: { String(try codec.decodeInput($0)) }, encodeOutput: { try codec.encodeOutput(Int($0)!) }))
                                let token = try checkTurn(text, before: String(before), input: String(input), transition: Transition(state: String(transition.state), outputs: transition.outputs.map(String.init), disposition: transition.disposition), sequence: integer(action["sequence"]!), policy: policy)
                                try recorder.observeChecked(token)
                            } else {
                                let token = try checkTurn(active, before: before, input: input, transition: transition, sequence: integer(action["sequence"]!), policy: policy)
                                try recorder.observeChecked(token)
                            }
                        }
                    } catch let caught { error = caught }
                }
                if let text = expectedState["error_contains"] as? String { XCTAssertTrue(error.map { String(describing: $0).contains(text) } ?? false, "\(name): \(String(describing: error)) expected \(text)") }
                else { XCTAssertNil(error, "\(name): \(String(describing: error))") }
                XCTAssertEqual(recorder.observedSteps, integer(expectedState["observed_steps"]!), name)
                XCTAssertEqual(recorder.evictedSteps, integer(expectedState["evicted_steps"]!), name)
                XCTAssertEqual(recorder.retainedSteps, expectedState["retained_steps"] as! Int, name)
                XCTAssertEqual(recorder.isFrozen, expectedState["frozen"] as! Bool, name)
                let trace = recorder.snapshot()
                XCTAssertEqual(trace.initialState, [UInt8(expectedState["initial_state"] as! Int)], name)
                XCTAssertEqual(trace.initialChecks, checks(expectedState["initial_checks"]!), name)
                let termination: String
                switch trace.termination { case .interrupted: termination = "interrupted"; case .propertyFailed: termination = "property_failed"; case .modelError: termination = "model_error"; default: termination = "other" }
                XCTAssertEqual(termination, expectedState["termination"] as! String, name)
                let expectedSteps = expectedState["steps"] as! [[String:Any]]
                XCTAssertEqual(trace.steps.count, expectedSteps.count, name)
                for (step,want) in zip(trace.steps, expectedSteps) {
                    let t = turn(want["transition"] as! [String:Any])
                    XCTAssertEqual(step.input, [UInt8(want["input"] as! Int)], name)
                    XCTAssertEqual(step.postState, [UInt8(t.state)], name)
                    XCTAssertEqual(step.outputs, t.outputs.map { [UInt8($0)] }, name)
                    XCTAssertEqual(step.disposition, t.disposition, name)
                    XCTAssertEqual(step.checks, checks(want["checks"]!), name)
                }
                XCTAssertEqual(try recorder.retainedBytes, UInt64(try encodeAuditTrace(trace).count), name)
            }
        }
    }
}

import Foundation
import XCTest
@testable import Statelessness

final class EvidenceRecorderTests: XCTestCase {
    private func model() -> Model<Int,Int,Int> {
        Model(initialState: { throw ModelError("initial must not run") }, step: { s,i in Transition(state: s+i, outputs: [s+i]) }, checkState: { _ in [.passed("s")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, checkTransition: { _,_,_ in [.passed("t")] }, codec: Codec(metadata: { Metadata(name: "causal", build: "test") }, encodeState: { [UInt8($0)] }, decodeState: { Int($0[0]) }, encodeInput: { [UInt8($0)] }, decodeInput: { Int($0[0]) }, encodeOutput: { [UInt8($0)] }))
    }
    private func turn(_ m: Model<Int,Int,Int>, _ sequence: UInt64, before: Int? = nil) throws -> CheckedTurn<Int,Int,Int> {
        let b = before ?? Int(sequence)-1
        return try checkTurn(m, before: b, input: 1, transition: Transition(state: b+1, outputs: [b+1]), sequence: sequence)
    }
    func testNoRecheckingAndTraceEvictionsAlignJournal() throws {
        var m = model(), stateCalls = 0, edgeCalls = 0
        m.checkState = { _ in stateCalls += 1; return [.passed("s")] }
        m.checkTransition = { _,_,_ in edgeCalls += 1; return [.passed("t")] }
        let initial = try checkInitial(m, state: 0)
        let r = try EvidenceRecorder(checkedInitial: initial, config: RunConfig(strategy: "runtime"), options: RecorderOptions(maxSteps: 1))
        for sequence in 1...3 {
            let token = try turn(m, UInt64(sequence))
            try r.observe(token, causal: CausalContext(causes: sequence == 1 ? [] : [OutputOrigin(sequence: UInt64(sequence-1), output: 0)]))
            let snapshot = r.snapshot(); try snapshot.validate()
            XCTAssertEqual(snapshot.checkpointAfter, UInt64(sequence-1)); XCTAssertEqual(snapshot.steps.count, 1)
            XCTAssertEqual(snapshot.steps[0].sequence, UInt64(sequence)); XCTAssertEqual(snapshot.steps[0].stateCheckCount, 1)
            XCTAssertEqual(snapshot.trace.initialChecks, [.passed("s")])
        }
        XCTAssertEqual(stateCalls, 4); XCTAssertEqual(edgeCalls, 3)
        let artifact = try decodeArtifact(encodeEvidence(r.snapshot()))
        XCTAssertEqual(artifact, .v2(r.snapshot()))
        XCTAssertEqual(try r.snapshot().replay(model()).outcome, "exact")
    }
    func testMetadataAndCauseOnlyEvictionsAreCoordinated() throws {
        let m = model(), initial = try checkInitial(m, state: 0)
        let base = UInt64(try evidenceProvenanceBytes(Provenance(), maximum: 4096).count)+12
        let row = UInt64(try evidenceStepBytes(StepEvidence(sequence: 1, stateCheckCount: 1), maximum: 4096).count)
        var limits = EvidenceLimits(); limits.maxMetadataBytes = base+row*2
        let metadata = try EvidenceRecorder(checkedInitial: initial, config: RunConfig(strategy: "runtime"), limits: limits)
        for s in 1...4 { try metadata.observe(turn(m, UInt64(s))) }
        XCTAssertEqual(metadata.snapshot().checkpointAfter, 2); XCTAssertEqual(metadata.snapshot().steps.count, 2)
        try metadata.snapshot().validate(limits: limits)
        limits.maxMetadataBytes = 4096; limits.maxTotalCauses = 1
        let causes = try EvidenceRecorder(checkedInitial: initial, config: RunConfig(strategy: "runtime"), limits: limits)
        try causes.observe(turn(m, 1))
        try causes.observe(turn(m, 2), causal: CausalContext(causes: [OutputOrigin(sequence: 1, output: 0)]))
        try causes.observe(turn(m, 3), causal: CausalContext(causes: [OutputOrigin(sequence: 2, output: 0)]))
        XCTAssertEqual(causes.snapshot().checkpointAfter, 2); XCTAssertEqual(causes.snapshot().steps.count, 1)
        try causes.snapshot().validate(limits: limits)
    }
    func testCurrentRetainedOriginsCannotBeExcusedByPlannedEviction() throws {
        let m = model(), initial = try checkInitial(m, state: 0)
        var limits = EvidenceLimits(); limits.maxMetadataBytes = 128
        let r = try EvidenceRecorder(checkedInitial: initial, config: RunConfig(strategy: "runtime"), options: RecorderOptions(maxSteps: 1), limits: limits)
        try r.observe(turn(m, 1))
        XCTAssertThrowsError(try r.observe(turn(m, 2), causal: CausalContext(causes: [OutputOrigin(sequence: 1, output: 9)]))) {
            XCTAssertTrue(String(describing: $0).contains("retained output"))
        }
        XCTAssertEqual(r.snapshot().checkpointAfter, 0); XCTAssertEqual(r.snapshot().steps.count, 1); XCTAssertTrue(r.isFrozen)
        try r.snapshot().validate(limits: limits)
    }
    func testPreparationAndCommitFailuresPreserveAtomicJournal() throws {
        let m = model(), initial = try checkInitial(m, state: 0)
        var limits = EvidenceLimits(); limits.maxMetadataBytes = 128
        let r = try EvidenceRecorder(checkedInitial: initial, config: RunConfig(strategy: "runtime"), options: RecorderOptions(maxSteps: 1), limits: limits)
        try r.observe(turn(m, 1))
        var broken = m; broken.codec?.encodeOutput = { _ in throw ModelError("output failed") }
        XCTAssertThrowsError(try r.observe(turn(broken, 2), causal: CausalContext(machine: "label")))
        let frozen = r.snapshot(); XCTAssertEqual(frozen.steps.count, 1); XCTAssertEqual(frozen.checkpointAfter, 0)
        XCTAssertThrowsError(try r.observe(turn(m, 2))); XCTAssertEqual(r.snapshot(), frozen)
        try frozen.validate(limits: limits)
        let tooLarge = try EvidenceRecorder(checkedInitial: initial, config: RunConfig(strategy: "runtime"), limits: limits)
        XCTAssertThrowsError(try tooLarge.observe(turn(m, 1), causal: CausalContext(machine: String(repeating: "x", count: 200))))
        XCTAssertEqual(tooLarge.snapshot().steps.count, 0)
        var reserved = EvidenceLimits(); reserved.maxMetadataBytes = 1000
        XCTAssertThrowsError(try EvidenceRecorder(checkedInitial: initial, config: RunConfig(strategy: "runtime"), options: RecorderOptions(maxRetainedBytes: 100), limits: reserved))
    }
    func testOriginPropertyFailureTransferAndReentry() throws {
        let m = model(), initial = try checkInitial(m, state: 0)
        let attached = try EvidenceRecorder(checkedInitial: initial, origin: 40, config: RunConfig(strategy: "runtime"))
        try attached.observe(turn(m, 41, before: 0), causal: CausalContext(causes: [OutputOrigin(sequence: 40, output: UInt32.max)]))
        XCTAssertEqual(attached.snapshot().checkpointAfter, 40); try attached.snapshot().validate()
        let moved = try attached.intoTrace(); XCTAssertEqual(moved, attached.snapshot()); XCTAssertTrue(attached.isFrozen)
        XCTAssertThrowsError(try attached.intoTrace()); XCTAssertThrowsError(try attached.observe(turn(m, 42, before: 1)))
        var failed = m; failed.checkState = { _ in [.failed("bad", "reason")] }
        let failure = try EvidenceRecorder(checkedInitial: checkInitial(failed, state: 0), config: RunConfig(strategy: "runtime"))
        XCTAssertThrowsError(try failure.observe(turn(m, 1))); XCTAssertEqual(failure.snapshot().trace.termination, .propertyFailed)
        var callbackModel = m
        var reentrant: EvidenceRecorder!
        callbackModel.codec?.encodeOutput = { value in
            XCTAssertThrowsError(try reentrant.intoTrace())
            return [UInt8(value)]
        }
        reentrant = try EvidenceRecorder(checkedInitial: initial, config: RunConfig(strategy: "runtime"))
        XCTAssertThrowsError(try reentrant.observe(turn(callbackModel, 1)))
        XCTAssertTrue(reentrant.isFrozen); XCTAssertTrue(reentrant.snapshot().steps.isEmpty)
        if case let .modelError(error) = reentrant.snapshot().trace.termination { XCTAssertTrue(error.contains("reentrant")) } else { XCTFail("expected first reentry error") }
    }
}


extension EvidenceRecorderTests {
    func testSharedEvidenceRecorderCorpus() throws {
        let cases = try evidenceTestCorpus()["recorder_cases"] as! [[String:Any]]
        XCTAssertEqual(cases.count, 20)
        for c in cases {
            let name = c["case"] as! String, settings = c["model"] as! [String:Any], expected = c["expected"] as! [String:Any]
            var checksCalled = 0, reducersCalled = 0
            func configured(_ encodeError: String? = nil) -> Model<Int,Int,Int> {
                Model(initialState: { XCTFail("initial must not run"); return 0 }, step: { s,i in reducersCalled += 1; return Transition(state: s+i, outputs: [s+i]) }, checkState: { state in
                    checksCalled += 1
                    if let threshold = settings["fail_at"] as? Int, state >= threshold { return [.failed("s", "at threshold")] }
                    return [.passed("s")]
                }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, checkTransition: { _,_,_ in checksCalled += 1; return [.passed("t")] }, codec: Codec(metadata: { Metadata(name: settings["name"] as! String, build: settings["build"] as! String) }, encodeState: { Array(String($0).utf8) }, decodeState: { Int(String(decoding: $0, as: UTF8.self))! }, encodeInput: { Array(String($0).utf8) }, decodeInput: { Int(String(decoding: $0, as: UTF8.self))! }, encodeOutput: { if encodeError == "output" { throw ModelError("fixture output error") }; return Array(String($0).utf8) }))
            }
            let m = configured(), initial = try checkInitial(m, state: c["initial_state"] as! Int)
            let limits = evidenceTestLimits(c["limits"] as! [String:Any]), o = c["options"] as! [String:Any], cfg = c["config"] as! [String:Any]
            let options = RecorderOptions(maxSteps: o["max_steps"] as! Int, maxRetainedBytes: evidenceTestInteger(o["max_retained_bytes"]!), limits: evidenceTestLimits(["trace":o["limits"]!]).trace)
            let config = RunConfig(strategy: cfg["strategy"] as! String, seed: cfg["seed"] is NSNull ? nil : evidenceTestInteger(cfg["seed"]!), parameters: (cfg["parameters"] as! [[String]]).map { AuditParameter($0[0],$0[1]) })
            let r: EvidenceRecorder
            do {
                r = try EvidenceRecorder(checkedInitial: initial, origin: evidenceTestInteger(c["origin"]!), config: config, options: options, provenance: evidenceTestProvenance(c["provenance"] as! [String:Any]), limits: limits)
                XCTAssertNil(expected["initial_error_contains"] as? String, name)
            } catch {
                guard let text = expected["initial_error_contains"] as? String else { XCTFail("\(name): \(error)"); continue }
                XCTAssertTrue(String(describing: error).contains(text), "\(name): \(error)"); continue
            }
            for (action,want) in zip(c["actions"] as! [[String:Any]], expected["states"] as! [[String:Any]]) {
                let t = action["transition"] as! [String:Any], d = t["disposition"] as! [String:Any], disposition: Disposition
                switch d["kind"] as! String { case "rejected": disposition = .rejected(d["reason"] as? String ?? ""); case "ignored": disposition = .ignored(d["reason"] as? String ?? ""); default: disposition = .accepted }
                let policy = action["policy"] as! [String:Any]
                let token = try checkTurn(configured(action["encode_error"] as? String), before: action["before"] as! Int, input: action["input"] as! Int, transition: Transition(state: t["state"] as! Int, outputs: t["outputs"] as! [Int], disposition: disposition), sequence: evidenceTestInteger(action["sequence"]!), policy: CheckPolicy(stateEvery: evidenceTestInteger(policy["state_every"]!), transitionChecks: policy["transition_checks"] as! Bool))
                let calls = checksCalled
                do {
                    try r.observe(token, causal: evidenceTestContext(action["causal"] as! [String:Any]))
                    XCTAssertNil(want["error_contains"] as? String, name)
                } catch {
                    guard let text = want["error_contains"] as? String else { XCTFail("\(name): unexpected \(error)"); continue }
                    XCTAssertTrue(String(describing: error).contains(text), "\(name): \(error)")
                }
                XCTAssertEqual(checksCalled, calls, name); XCTAssertEqual(reducersCalled, 0, name)
                let value = r.snapshot(), raw = value.trace
                XCTAssertEqual(value.checkpointAfter, evidenceTestInteger(want["checkpoint_after"]!), name)
                XCTAssertEqual(raw.initialState, Array(String(want["initial_state"] as! Int).utf8), name)
                XCTAssertEqual(raw.initialChecks, evidenceTestChecks(want["initial_checks"]!), name)
                XCTAssertEqual(r.isFrozen, want["frozen"] as! Bool, name)
                let rows = (want["evidence_steps"] as! [[String:Any]]).map { x in StepEvidence(sequence: evidenceTestInteger(x["sequence"]!), stateCheckCount: UInt32(evidenceTestInteger(x["state_check_count"]!)), causal: evidenceTestContext(x["causal"] as! [String:Any])) }
                XCTAssertEqual(value.steps, rows, name)
                let termination: String
                switch raw.termination { case .interrupted: termination = "interrupted"; case .propertyFailed: termination = "property_failed"; case .modelError: termination = "model_error"; default: termination = "other" }
                XCTAssertEqual(termination, want["termination"] as! String, name)
                let expectedSteps = want["steps"] as! [[String:Any]]
                XCTAssertEqual(raw.steps.count, expectedSteps.count, name)
                for (step, expectedStep) in zip(raw.steps, expectedSteps) {
                    XCTAssertEqual(step.input, Array(String(expectedStep["input"] as! Int).utf8), name)
                    XCTAssertEqual(step.postState, Array(String(expectedStep["post_state"] as! Int).utf8), name)
                    XCTAssertEqual(step.outputs, (expectedStep["outputs"] as! [Int]).map { Array(String($0).utf8) }, name)
                    XCTAssertEqual(step.checks, evidenceTestChecks(expectedStep["checks"]!), name)
                }
                for key in ["observed_steps", "evicted_steps"] {
                    XCTAssertEqual(raw.config.parameters.first { $0.key == "stateless.monitor."+key }?.value, want[key] as? String, name)
                }
                try value.validate(limits: limits)
                XCTAssertEqual(try decodeArtifact(encodeEvidence(value, limits: limits), limits: limits), .v2(value), name)
            }
        }
    }
}

import Foundation
import XCTest
@testable import Statelessness

func evidenceTestHex(_ bytes: [UInt8]) -> String { bytes.map { String(format: "%02x", $0) }.joined() }
func evidenceTestBytes(_ text: String) -> [UInt8] { stride(from: 0, to: text.count, by: 2).map { n in let i = text.index(text.startIndex, offsetBy: n); return UInt8(text[i..<text.index(i, offsetBy: 2)], radix: 16)! } }
func evidenceTestInteger(_ value: Any) -> UInt64 { if let s = value as? String { return UInt64(s)! }; return (value as! NSNumber).uint64Value }
func evidenceTestChecks(_ rows: Any) -> [Check] { (rows as! [[String:Any]]).map { Check($0["id"] as! String, CheckStatus(rawValue: $0["status"] as! String)!, $0["reason"] as? String ?? $0["details"] as? String ?? "") } }
func evidenceTestTrace(_ value: [String:Any]) -> AuditTrace {
    let meta = value["metadata"] as! [String:Any], config = value["config"] as! [String:Any], end = value["termination"] as! [String:Any]
    let termination: AuditTermination
    switch end["kind"] as! String { case "property_failed": termination = .propertyFailed; case "interrupted": termination = .interrupted; case "step_limit": termination = .stepLimit; case "model_error": termination = .modelError(end["reason"] as? String ?? ""); default: termination = .completed }
    return AuditTrace(metadata: Metadata(name: meta["name"] as! String, modelVersion: UInt32(evidenceTestInteger(meta["model_version"]!)), propertiesVersion: UInt32(evidenceTestInteger(meta["properties_version"]!)), codecVersion: UInt32(evidenceTestInteger(meta["codec_version"]!)), build: meta["build"] as! String), config: RunConfig(strategy: config["strategy"] as! String, seed: config["seed"] is NSNull ? nil : evidenceTestInteger(config["seed"]!), parameters: (config["parameters"] as! [[String]]).map { AuditParameter($0[0],$0[1]) }), initialState: evidenceTestBytes(value["initial_state"] as! String), initialChecks: evidenceTestChecks(value["initial_checks"]!), steps: (value["steps"] as! [[String:Any]]).map { step in
        let d = step["disposition"] as! [String:Any], disposition: Disposition
        switch d["kind"] as! String { case "rejected": disposition = .rejected(d["reason"] as? String ?? ""); case "ignored": disposition = .ignored(d["reason"] as? String ?? ""); default: disposition = .accepted }
        return TraceStep(input: evidenceTestBytes(step["input"] as! String), disposition: disposition, outputs: (step["outputs"] as! [String]).map(evidenceTestBytes), postState: evidenceTestBytes(step["post_state"] as! String), checks: evidenceTestChecks(step["checks"]!))
    }, termination: termination)
}
func evidenceTestProvenance(_ p: [String:Any]) -> Provenance { Provenance(run: p["run"] as! String, epoch: evidenceTestInteger(p["epoch"]!), manifest: p["manifest"] as? String, source: p["source"] as? String, environment: (p["environment"] as! [[String]]).map { AuditParameter($0[0],$0[1]) }) }
func evidenceTestContext(_ c: [String:Any]) -> CausalContext {
    CausalContext(machine: c["machine"] as? String, generation: c["generation"] is NSNull ? nil : evidenceTestInteger(c["generation"]!), correlation: c["correlation"] as? String, logicalTime: c["logical_time"] is NSNull ? nil : evidenceTestInteger(c["logical_time"]!), causes: (c["causes"] as! [[String:Any]]).map { OutputOrigin(sequence: evidenceTestInteger($0["sequence"]!), output: UInt32(evidenceTestInteger($0["output"]!))) })
}
func evidenceTestV2(_ value: [String:Any]) -> TraceV2 {
    TraceV2(trace: evidenceTestTrace(value["trace"] as! [String:Any]), checkpointAfter: evidenceTestInteger(value["checkpoint_after"]!), provenance: evidenceTestProvenance(value["provenance"] as! [String:Any]), steps: (value["steps"] as! [[String:Any]]).map { StepEvidence(sequence: evidenceTestInteger($0["sequence"]!), stateCheckCount: UInt32(evidenceTestInteger($0["state_check_count"]!)), causal: evidenceTestContext($0["causal"] as! [String:Any])) })
}
func evidenceTestLimits(_ value: [String:Any]) -> EvidenceLimits {
    var limits = EvidenceLimits()
    for (key,value) in value {
        if key == "trace" {
            for (name,value) in value as! [String:Any] {
                let n = evidenceTestInteger(value)
                switch name { case "max_total_bytes": limits.trace.maxTotalBytes = n; case "max_frame_bytes": limits.trace.maxFrameBytes = n; case "max_blob_bytes": limits.trace.maxBlobBytes = n; case "max_string_bytes": limits.trace.maxStringBytes = n; case "max_steps": limits.trace.maxSteps = n; case "max_checks_per_step": limits.trace.maxChecksPerStep = n; case "max_outputs_per_step": limits.trace.maxOutputsPerStep = n; case "max_parameters": limits.trace.maxParameters = n; case "max_items": limits.trace.maxItems = n; default: preconditionFailure(name) }
            }
        } else {
            let n = evidenceTestInteger(value)
            switch key { case "max_metadata_bytes": limits.maxMetadataBytes = n; case "max_labels": limits.maxLabels = n; case "max_label_bytes": limits.maxLabelBytes = n; case "max_causes_per_step": limits.maxCausesPerStep = n; case "max_total_causes": limits.maxTotalCauses = n; default: preconditionFailure(key) }
        }
    }
    return limits
}
func evidenceTestCorpus() throws -> [String:Any] {
    let fallback = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("conformance/causal-evidence-v2.json")
    let path = ProcessInfo.processInfo.environment["STATELESS_CAUSAL_CORPUS"].map { URL(fileURLWithPath: $0) } ?? fallback
    return try JSONSerialization.jsonObject(with: Data(contentsOf: path)) as! [String:Any]
}
private func evidenceTestReport(_ r: ReplayReport) -> [String:Any] { ["outcome":r.outcome,"divergence_step":r.step as Any? ?? NSNull(),"divergence_field":r.field as Any? ?? NSNull(),"steps_verified":r.stepsVerified,"failure_reproduced":r.failureReproduced,"build_matches":r.buildMatches] }
final class EvidenceTests: XCTestCase {
    func testSharedWireAndEveryPrefixReader() throws {
        for c in try evidenceTestCorpus()["wire_cases"] as! [[String:Any]] {
            let name = c["case"] as! String, bytes = evidenceTestBytes(c["bytes_hex"] as! String), valid = c["valid"] as! Bool, limits = evidenceTestLimits(c["limits"] as? [String:Any] ?? [:])
            if valid {
                let artifact = try decodeArtifact(bytes, limits: limits)
                let expected: Artifact = c["artifact_kind"] as! String == "v1" ? .v1(evidenceTestTrace(c["trace"] as! [String:Any])) : .v2(evidenceTestV2(c["evidence"] as! [String:Any]))
                XCTAssertEqual(artifact, expected, name)
                if case let .v2(value) = artifact { XCTAssertEqual(try encodeEvidence(value, limits: limits), bytes, name) }
                var cursor = 0
                let streamed = try Artifact.read(limits: limits) { maximum in let end = min(cursor+min(maximum,7),bytes.count); defer {cursor=end}; return Array(bytes[cursor..<end]) }
                XCTAssertEqual(streamed, artifact)
                for end in 0..<bytes.count { XCTAssertThrowsError(try decodeArtifact(Array(bytes.prefix(end)), limits: limits), "\(name):\(end)") }
            } else {
                XCTAssertThrowsError(try decodeArtifact(bytes, limits: limits), name) { error in
                    if let message = c["error_contains"] as? String { XCTAssertTrue(String(describing:error).contains(message), "\(name): \(error)") }
                }
            }
        }
    }
    func testSharedReplayAndUpgrade() throws {
        for c in try evidenceTestCorpus()["replay_cases"] as! [[String:Any]] {
            let name = c["case"] as! String, settings = c["model"] as! [String:Any], evidence = evidenceTestV2(c["evidence"] as! [String:Any]), expected = c["expected"] as! [String:Any]
            var events:[String] = []
            func event(_ text:String)throws { events.append(text); if text == settings["error_at"] as? String { throw ModelError("fixture causal error") } }
            let model = Model<Int,Int,Int>(initialState:{ XCTFail("passive operation called initial"); return 0 }, step:{s,i in try event("step:\(s):\(i)"); return Transition(state:s+i+(settings["state_delta"] as? Int ?? 0),outputs:[s+i+(settings["output_delta"] as? Int ?? 0)])}, checkState:{s in try event("state:\(s)"); return settings["phase_shift"] as? Bool == true && s != 0 ? [] : [.passed("s")]}, cloneState:{$0},cloneInput:{$0},cloneOutput:{$0},checkTransition:{_,_,_ in settings["phase_shift"] as? Bool == true ? [.passed("s"),.passed("t")] : [.passed("t")]},codec:Codec(metadata:{Metadata(name:settings["name"] as! String,build:settings["build"] as! String)},encodeState:{Array(String($0).utf8)},decodeState:{Int(String(decoding:$0,as:UTF8.self))!},encodeInput:{Array(String($0).utf8)},decodeInput:{Int(String(decoding:$0,as:UTF8.self))!},encodeOutput:{Array(String($0).utf8)}))
            do {
                if c["operation"] as! String == "upgrade" {
                    let upgraded = try TraceV2.verifyUpgrade(model, trace:evidence.trace, provenance:evidence.provenance, checkpointAfter:evidence.checkpointAfter, allowBuildMismatch:c["allow_build_mismatch"] as? Bool ?? false)
                    XCTAssertNil(expected["error_contains"] as? String,name)
                    if let rows = expected["steps"] as? [[String:Any]] {
                        let wanted = rows.map { StepEvidence(sequence:evidenceTestInteger($0["sequence"]!),stateCheckCount:UInt32(evidenceTestInteger($0["state_check_count"]!)),causal:evidenceTestContext($0["causal"] as! [String:Any])) }
                        XCTAssertEqual(upgraded.steps,wanted,name)
                    }
                    if let checkpoint = expected["checkpoint_after"] { XCTAssertEqual(upgraded.checkpointAfter,evidenceTestInteger(checkpoint),name) }
                } else {
                    let report = try evidence.replay(model,allowBuildMismatch:c["allow_build_mismatch"] as? Bool ?? false)
                    XCTAssertNil(expected["error_contains"] as? String,name)
                    XCTAssertEqual(try JSONSerialization.data(withJSONObject:evidenceTestReport(report),options:[.sortedKeys]),try JSONSerialization.data(withJSONObject:expected["report"]!,options:[.sortedKeys]),name)
                }
            } catch {
                guard let message = expected["error_contains"] as? String else { XCTFail("\(name):unexpected \(error)"); continue }
                XCTAssertTrue(String(describing:error).contains(message),"\(name):\(error)")
            }
        }
    }
    func testStructuralLogicalAllocationAndExactBudgetBoundaries() throws {
        // String root charges its UTF-8 allocation, two elements, four work
        // units (String, u32 length, two UTF-8 bytes), and depth two.
        let bytes: [UInt8] = [2,0,0,0,0xc3,0xa9]
        var exact = EvidenceDecodeLimits(metadataBytes: 6)
        exact.allocation = 2; exact.elements = 2; exact.work = 4; exact.depth = 2
        let decoder = try EvidenceDecoder(bytes, limits: exact)
        XCTAssertTrue(exactText(try decoder.string(), "é")); try decoder.finish()
        for kind in ["bytes", "allocation", "elements", "work", "depth"] {
            var limit = exact
            switch kind { case "bytes": limit.bytes -= 1; case "allocation": limit.allocation -= 1; case "elements": limit.elements -= 1; case "work": limit.work -= 1; default: limit.depth -= 1 }
            XCTAssertThrowsError(try EvidenceDecoder(bytes, limits: limit).string(), kind)
        }
        for weight: UInt64 in [48,16,120] {
            var limits = EvidenceDecodeLimits(metadataBytes: 64)
            limits.allocation = weight * 2 - 1
            let reader = try EvidenceDecoder([2,0,0,0],limits: limits)
            XCTAssertThrowsError(try reader.vector(allocationSize: weight) { _ -> UInt64 in XCTFail("allocation was not preflighted"); return 0 }) { error in
                XCTAssertTrue(String(describing:error).contains("decode allocation limit"))
            }
            XCTAssertThrowsError(try reader.finish())
        }
    }
    func testWriterPreflightsBeforeUserSinkAndOwnedMetadataRemainsIndependent() throws {
        let row = try (evidenceTestCorpus()["wire_cases"] as! [[String:Any]]).first { $0["case"] as? String == "two-turn-causal" }!
        let original = evidenceTestV2(row["evidence"] as! [String:Any])
        var environment = original.provenance.environment, causes = original.steps[1].causal.causes
        let provenance = Provenance(run:original.provenance.run,epoch:original.provenance.epoch,environment:environment)
        let context = CausalContext(causes:causes)
        environment.append(AuditParameter("later","mutation")); causes.removeAll()
        XCTAssertEqual(provenance.environment,original.provenance.environment); XCTAssertEqual(context.causes,original.steps[1].causal.causes)
        let invalid = TraceV2(trace:original.trace,checkpointAfter:original.checkpointAfter,provenance:original.provenance,steps:[])
        var writes = 0
        XCTAssertThrowsError(try invalid.write { _ in writes += 1 }); XCTAssertEqual(writes,0)
        var limits = EvidenceLimits(); limits.trace.maxTotalBytes = UInt64(try encodeEvidence(original).count - 1)
        XCTAssertThrowsError(try original.write(limits:limits) { _ in writes += 1 }); XCTAssertEqual(writes,0)
        let valid = try encodeEvidence(original)
        var cursor = 0
        XCTAssertEqual(try Artifact.read { maximum in
            let end = min(cursor+maximum,valid.count); defer { cursor=end }; return Array(valid[cursor..<end])
        },.v2(original))
        XCTAssertThrowsError(try Artifact.read { maximum in [UInt8](repeating:0,count:maximum+1) })
    }
    func testIndependentStructuralDecoderBudgetsAndStickyFailure() throws {
        let trace = AuditTrace(metadata:Metadata(name:""),config:RunConfig(strategy:""),initialState:[],initialChecks:[],steps:[],termination:.completed)
        let value = TraceV2(trace:trace,provenance:Provenance(run:"label",environment:[AuditParameter("a","b")]),steps:[])
        let bytes = try evidenceMetadataBytes(value,maximum:4096)
        for kind in ["bytes","allocation","elements","depth","work"] {
            var limits = EvidenceDecodeLimits(metadataBytes:4096)
            switch kind {case "bytes": limits.bytes=0;case "allocation":limits.allocation=0;case "elements":limits.elements=0;case "depth":limits.depth=0;default:limits.work=0}
            XCTAssertThrowsError(try evidenceDecodeMetadata(bytes,trace:trace,limits:limits),kind)
        }
        let decoder = try EvidenceDecoder([2],limits:EvidenceDecodeLimits(metadataBytes:16))
        XCTAssertThrowsError(try decoder.optional {try $0.integer(8)} as UInt64?)
        XCTAssertThrowsError(try decoder.integer(1))
        XCTAssertThrowsError(try decoder.finish())
    }
}

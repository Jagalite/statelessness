import Foundation
import XCTest
@testable import Statelessness
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif

final class EvidenceExportsTests: XCTestCase {
    private func trace() -> TraceV2 {
        let raw = AuditTrace(metadata: Metadata(name: "secret-model", build: "secret-build"), config: RunConfig(strategy: "secret-strategy"), initialState: [99], initialChecks: [.passed("safe")], steps: [TraceStep(input: [98], disposition: .accepted, outputs: [[97]], postState: [96], checks: [.skipped("private", "secret-detail")])], termination: .interrupted)
        return TraceV2(trace: raw, checkpointAfter: 40, provenance: Provenance(run: "secret-run"), steps: [StepEvidence(sequence: 41, stateCheckCount: 1, causal: CausalContext(machine: "secret-machine"))])
    }
    func testPrivateExclusiveFilesAndPreflight() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: dir) }
        let raw = dir.appendingPathComponent("evidence.bin").path, diagnostic = dir.appendingPathComponent("diagnostic.json").path
        let evidence = trace()
        try evidence.writeRawNew(path: raw)
        let bytes = try Data(contentsOf: URL(fileURLWithPath: raw))
        XCTAssertEqual(try decodeArtifact(Array(bytes)), .v2(evidence))
        XCTAssertThrowsError(try evidence.writeRawNew(path: raw)); XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: raw)), bytes)
        let attributes = try FileManager.default.attributesOfItem(atPath: raw)
        XCTAssertEqual((attributes[.posixPermissions] as! NSNumber).intValue & 0o777, 0o600)
        try exportDiagnosticNew(.v2(evidence), path: diagnostic, policy: ExportPolicy(propertyIDs: ["safe"]))
        let text = try String(contentsOfFile: diagnostic, encoding: .utf8)
        XCTAssertTrue(text.contains("safe")); XCTAssertFalse(text.contains("secret")); XCTAssertFalse(text.contains("private"))
        XCTAssertThrowsError(try exportDiagnosticNew(.v2(evidence), path: diagnostic))
        let absent = dir.appendingPathComponent("absent").path
        XCTAssertThrowsError(try exportDiagnosticNew(.v2(evidence), path: absent, policy: ExportPolicy(maxBytes: 1)))
        XCTAssertFalse(FileManager.default.fileExists(atPath: absent))
        let incomplete = dir.appendingPathComponent("incomplete").path
        XCTAssertThrowsError(try evidencePrivateNew(path: incomplete, bytes: [1,2], sync: { _ in errno = EIO; return -1 })) {
            XCTAssertTrue(String(describing: $0).contains("incomplete newly created evidence file"))
        }
        XCTAssertEqual(try Data(contentsOf: URL(fileURLWithPath: incomplete)), Data([1,2]))
        XCTAssertThrowsError(try evidencePrivateNew(path: absent + "\0ignored", bytes: [])); XCTAssertFalse(FileManager.default.fileExists(atPath: absent))
    }
    func testBoundedDiagnosticPhasesAndExactAllowlist() throws {
        let value = trace()
        let full = try diagnosticJSON(.v2(value), policy: ExportPolicy(propertyIDs: ["safe"]))
        XCTAssertEqual(try diagnosticJSON(.v2(value), policy: ExportPolicy(maxBytes: full.count, propertyIDs: ["safe"])), full)
        XCTAssertThrowsError(try diagnosticJSON(.v2(value), policy: ExportPolicy(maxBytes: full.count-1, propertyIDs: ["safe"])))
        let zero = try JSONSerialization.jsonObject(with: Data(diagnosticJSON(.v2(value), policy: ExportPolicy(maxSteps: 0)))) as! [String:Any]
        XCTAssertEqual((zero["observations"] as! [Any]).count, 1); XCTAssertEqual(zero["truncated"] as? Bool, true)
        let legacy = String(decoding: try diagnosticJSON(.v1(value.trace)), as: UTF8.self)
        XCTAssertTrue(legacy.contains("unknown_legacy")); XCTAssertTrue(legacy.contains("\"checkpoint_after\":null"))
    }
    func testSharedExportCorpus() throws {
        let corpus = try evidenceTestCorpus()
        let cases = corpus["export_cases"] as! [[String:Any]]; XCTAssertEqual(cases.count, 13)
        for c in cases {
            let value = evidenceTestV2(c["evidence"] as! [String:Any]), policy = c["policy"] as! [String:Any], expected = c["expected"] as! [String:Any]
            let artifact: Artifact = c["artifact_kind"] as! String == "v1" ? .v1(value.trace) : .v2(value)
            do {
                let bytes = try diagnosticJSON(artifact, policy: ExportPolicy(maxSteps: policy["max_steps"] as! Int, maxBytes: policy["max_bytes"] as! Int, propertyIDs: policy["property_ids"] as! [String]))
                XCTAssertNil(expected["error_contains"] as? String, c["case"] as! String)
                XCTAssertEqual(bytes, Array((expected["json_text"] as! String).utf8), c["case"] as! String)
            } catch {
                guard let text = expected["error_contains"] as? String else { XCTFail("\(c["case"]!): \(error)"); continue }
                XCTAssertTrue(String(describing: error).contains(text))
            }
        }
    }
}

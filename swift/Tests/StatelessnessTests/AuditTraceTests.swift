import Foundation
import XCTest
import Statelessness

private enum AuditTestIO: Error { case failed }
private func auditHex(_ text: String) -> [UInt8] {
    let chars = Array(text.utf8)
    func digit(_ c: UInt8) -> UInt8 { c <= 57 ? c - 48 : c - 87 }
    return stride(from: 0, to: chars.count, by: 2).map { digit(chars[$0]) * 16 + digit(chars[$0 + 1]) }
}
private func auditFixtures() throws -> [[String: Any]] {
    let fallback = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("conformance/native-binary-v1.json")
    let url = ProcessInfo.processInfo.environment["STATELESS_BINARY_CORPUS"].map { URL(fileURLWithPath: $0) } ?? fallback
    let object = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String: Any]
    return object["fixtures"] as! [[String: Any]]
}
private func auditLimits(_ fixture: [String: Any]) -> BinaryTraceLimits {
    var l = BinaryTraceLimits()
    for (key, n) in fixture["limits"] as? [String: NSNumber] ?? [:] {
        let n = n.uint64Value
        switch key {
        case "max_total_bytes": l.maxTotalBytes = n
        case "max_frame_bytes": l.maxFrameBytes = n
        case "max_blob_bytes": l.maxBlobBytes = n
        case "max_string_bytes": l.maxStringBytes = n
        case "max_steps": l.maxSteps = n
        case "max_checks_per_step": l.maxChecksPerStep = n
        case "max_outputs_per_step": l.maxOutputsPerStep = n
        case "max_parameters": l.maxParameters = n
        case "max_items": l.maxItems = n
        default: preconditionFailure("unrecognized fixture limit")
        }
    }
    return l
}
private func auditExpected(_ object: [String: Any]) -> AuditTrace {
    let m = object["metadata"] as! [String: Any], c = object["config"] as! [String: Any], t = object["termination"] as! [String: Any]
    func checks(_ value: Any) -> [Check] { (value as! [[String: Any]]).map { Check($0["id"] as! String, CheckStatus(rawValue: $0["status"] as! String)!, $0["reason"] as? String ?? "") } }
    let steps = (object["steps"] as! [[String: Any]]).map { step -> TraceStep in
        let d = step["disposition"] as! [String: Any], name = d["kind"] as! String, reason = d["reason"] as? String ?? ""
        return TraceStep(input: auditHex(step["input"] as! String), disposition: name == "accepted" ? .accepted : name == "rejected" ? .rejected(reason) : .ignored(reason), outputs: (step["outputs"] as! [String]).map(auditHex), postState: auditHex(step["post_state"] as! String), checks: checks(step["checks"]!))
    }
    let termination: AuditTermination
    switch t["kind"] as! String {
    case "completed": termination = .completed
    case "property_failed": termination = .propertyFailed
    case "step_limit": termination = .stepLimit
    case "interrupted": termination = .interrupted
    default: termination = .modelError(t["reason"] as! String)
    }
    return AuditTrace(metadata: Metadata(name: m["name"] as! String, modelVersion: (m["model_version"] as! NSNumber).uint32Value, propertiesVersion: (m["properties_version"] as! NSNumber).uint32Value, codecVersion: (m["codec_version"] as! NSNumber).uint32Value, build: m["build"] as! String), config: RunConfig(strategy: c["strategy"] as! String, seed: (c["seed"] as? String).flatMap(UInt64.init), parameters: (c["parameters"] as! [[String]]).map { AuditParameter($0[0], $0[1]) }), initialState: auditHex(object["initial_state"] as! String), initialChecks: checks(object["initial_checks"]!), steps: steps, termination: termination)
}
final class AuditTraceTests: XCTestCase {
    func testSharedGoldenAndNegativeCorpus() throws {
        for fixture in try auditFixtures() {
            let name = fixture["case"] as! String, bytes = auditHex(fixture["bytes_hex"] as! String), limits = auditLimits(fixture)
            if fixture["valid"] as! Bool {
                let expected = auditExpected(fixture["trace"] as! [String: Any])
                XCTAssertEqual(try decodeAuditTrace(bytes, limits: limits), expected, name)
                XCTAssertEqual(try encodeAuditTrace(expected, limits: limits), bytes, name)
                let portable = expected.portable()
                XCTAssertEqual(try AuditTrace(portable: portable.trace, config: portable.config), expected, name)
            } else {
                let expected = fixture["expected_error"] as! [String: Any]
                XCTAssertThrowsError(try decodeAuditTrace(bytes, limits: limits), name) { error in
                    guard let e = error as? BinaryTraceError else { return XCTFail("unexpected error \(error)") }
                    XCTAssertEqual(e.kind, expected["kind"] as? String, name)
                    XCTAssertEqual(e.detail, expected["detail"] as? String, name)
                }
            }
        }
    }
    func testEveryValidFixturePrefixIsTruncated() throws {
        for fixture in try auditFixtures() where fixture["valid"] as! Bool {
            let bytes = auditHex(fixture["bytes_hex"] as! String)
            for end in 0..<bytes.count {
                let prefix = Array(bytes.prefix(end))
                XCTAssertThrowsError(try decodeAuditTrace(prefix)) { error in
                    XCTAssertEqual(error as? BinaryTraceError, .truncated, "\(fixture["case"]!) prefix \(end)")
                }
                var offset = 0
                XCTAssertThrowsError(try readAuditTrace { requested in
                    let next = offset + min(requested, min(7, prefix.count - offset)); defer { offset = next }
                    return Array(prefix[offset..<next])
                }) { error in XCTAssertEqual(error as? BinaryTraceError, .truncated) }
            }
        }
    }
    func testIncrementalReaderAllFixturesAndChunkSizes() throws {
        for fixture in try auditFixtures() {
            let bytes = auditHex(fixture["bytes_hex"] as! String)
            for chunkSize in [1, 2, 7, 4096] {
                var offset = 0
                func read(_ requested: Int) -> [UInt8] {
                    let end = offset + min(requested, min(chunkSize, bytes.count - offset)); defer { offset = end }; return Array(bytes[offset..<end])
                }
                if fixture["valid"] as! Bool {
                    XCTAssertEqual(try readAuditTrace(limits: auditLimits(fixture), from: read), auditExpected(fixture["trace"] as! [String: Any]))
                } else {
                    let expected = fixture["expected_error"] as! [String: Any]
                    XCTAssertThrowsError(try readAuditTrace(limits: auditLimits(fixture), from: read)) { error in
                        guard let e = error as? BinaryTraceError else { return XCTFail("unexpected error \(error)") }
                        XCTAssertEqual(e.kind, expected["kind"] as? String, "\(fixture["case"]!) chunk \(chunkSize)")
                        XCTAssertEqual(e.detail, expected["detail"] as? String)
                    }
                }
            }
        }
    }
    func testAllNineWriterQuotasAndLateFailurePublishNothing() throws {
        let trace = AuditTrace(metadata: Metadata(name: "m"), config: RunConfig(strategy: "s", parameters: [AuditParameter("k", "v")]), initialState: [0], initialChecks: [.passed("p")], steps: [TraceStep(input: [1], disposition: .rejected(""), outputs: [[2]], postState: [3], checks: [.skipped("q", "")])], termination: .completed)
        let quotas: [WritableKeyPath<BinaryTraceLimits, UInt64>] = [\.maxTotalBytes, \.maxFrameBytes, \.maxBlobBytes, \.maxStringBytes, \.maxSteps, \.maxChecksPerStep, \.maxOutputsPerStep, \.maxParameters, \.maxItems]
        for quota in quotas {
            var limits = BinaryTraceLimits(); limits[keyPath: quota] = 0; var writes = 0
            XCTAssertThrowsError(try writeAuditTrace(trace, limits: limits) { _ in writes += 1 })
            XCTAssertEqual(writes, 0)
        }
        let late = AuditTrace(metadata: Metadata(name: ""), config: RunConfig(strategy: ""), initialState: [], initialChecks: [], steps: [], termination: .modelError("late reason"))
        var limits = BinaryTraceLimits(); limits.maxStringBytes = 1; var writes = 0
        XCTAssertThrowsError(try writeAuditTrace(late, limits: limits) { _ in writes += 1 })
        XCTAssertEqual(writes, 0)
        let invalid = AuditTrace(metadata: trace.metadata, config: trace.config, initialState: [], initialChecks: [], steps: [TraceStep(input: [], disposition: .accepted, outputs: [], postState: [], checks: [Check("late", .passed, "illegal")])], termination: .completed)
        XCTAssertThrowsError(try writeAuditTrace(invalid) { _ in writes += 1 }); XCTAssertEqual(writes, 0)
    }
    func testFrameLimitBeforePayloadReadAndIOFailurePropagation() throws {
        var calls = 0
        let badHeader: [UInt8] = [83,84,76,69,83,83,26,10,1,0,0,0]
        XCTAssertThrowsError(try readAuditTrace { requested in
            calls += 1
            if calls == 1 { XCTAssertEqual(requested, 12); return badHeader }
            if calls == 2 { return [1, 1, 0, 128, 0] }
            throw AuditTestIO.failed
        }) { error in XCTAssertEqual(error as? BinaryTraceError, .limitExceeded("frame bytes")) }
        XCTAssertEqual(calls, 2)
        calls = 0
        var tight = BinaryTraceLimits(); tight.maxTotalBytes = 17
        XCTAssertThrowsError(try readAuditTrace(limits: tight) { _ in
            calls += 1
            if calls == 1 { return badHeader }
            if calls == 2 { return [1, 0, 0, 0, 0] }
            throw AuditTestIO.failed
        }) { error in XCTAssertEqual(error as? BinaryTraceError, .limitExceeded("total bytes")) }
        XCTAssertEqual(calls, 2)
        XCTAssertThrowsError(try readAuditTrace { _ in throw AuditTestIO.failed }) { XCTAssertTrue($0 is AuditTestIO) }
        let trace = AuditTrace(metadata: Metadata(name: ""), config: RunConfig(strategy: ""), initialState: [], initialChecks: [], steps: [], termination: .completed)
        var writes = 0
        XCTAssertThrowsError(try writeAuditTrace(trace) { _ in writes += 1; if writes == 2 { throw AuditTestIO.failed } }) { XCTAssertTrue($0 is AuditTestIO) }
        XCTAssertEqual(writes, 2)
    }
    func testCRCAndExplicitPortableLossRules() throws {
        XCTAssertEqual(auditCRC32(Array("123456789".utf8)), 0xcbf43926)
        let portable = Trace(metadata: Metadata(name: ""), initialState: [], initialChecks: [], steps: [], termination: .completed, error: "would be lost")
        XCTAssertThrowsError(try AuditTrace(portable: portable, config: RunConfig(strategy: "explicit")))
        let emptyError = AuditTrace(metadata: Metadata(name: ""), config: RunConfig(strategy: "", seed: 0, parameters: [AuditParameter("x", "1"), AuditParameter("x", "2")]), initialState: [], initialChecks: [], steps: [], termination: .modelError(""))
        XCTAssertEqual(try decodeAuditTrace(encodeAuditTrace(emptyError)), emptyError)
        XCTAssertNotEqual(RunConfig(strategy: "", seed: nil), RunConfig(strategy: "", seed: 0))
        XCTAssertNotEqual(AuditParameter("\u{e9}", ""), AuditParameter("e\u{301}", ""))
    }
}

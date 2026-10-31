import Foundation
import XCTest
import Statelessness
import StatelessDebug

final class WireParityTests: XCTestCase {
    final class Calls { var values: [String] = [] }
    func testRustGeneratedInspectionWireFixture() throws {
        let root = URL(fileURLWithPath:#filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let path = ProcessInfo.processInfo.environment["STATELESS_SWIFT_INSPECTION_WIRE"] .map { URL(fileURLWithPath:$0) } ?? root.appendingPathComponent("conformance/swift-inspection-wire-v1.tsv")
        let fixture = try String(contentsOf:path,encoding:.utf8)
        let expected = Dictionary(uniqueKeysWithValues:fixture.split(separator:"\n").map { line -> (String,[Substring]) in
            let fields = line.split(separator:"\t",omittingEmptySubsequences:false)
            return (String(fields[0]),Array(fields.dropFirst(9)))
        })
        let model = Model<Int,Int,Int>(initialState:{0},step:{s,_ in Transition(state:s)},checkState:{_ in []},cloneState:{$0},cloneInput:{$0},cloneOutput:{$0})
        let session = try DebugSession(id:"test",model:model,policy:.outOfDomainInjection,equalInputs: ==)
        let adapter = try ProtocolAdapter(session:session)
        var visitedFields: [Int] = []
        try adapter.enableInspection(schema:{ _ in DisplaySchema(name:"request-lifecycle.State") }) { state,snapshot,path,page,limits in
            let context = try InspectContext(limits:limits)
            let node: InspectNode
            if path.isEmpty {
                node = try context.object("request-lifecycle.State",total:4,page:page) { index,c in
                    visitedFields.append(index)
                    guard index == 0 else { throw InspectError.pathNotFound }
                    return try c.field("generation") { try $0.scalar(.integer(kind:ExactString("u8"),decimal:ExactString(String(state)))) }
                }
            } else if path == [.field(ExactString("generation"))] {
                node = try context.scalar(.integer(kind:ExactString("u8"),decimal:ExactString(String(state))))
            } else if path == [.field(ExactString("pending"))] {
                node = try context.sequence(name:"slice",total:0,page:page) { _,_ in throw InspectError.pathNotFound }
            } else { throw InspectError.pathNotFound }
            return InspectResult(snapshot:snapshot,schema:DisplaySchema(name:"request-lifecycle.State"),path:path,node:node)
        }
        func request(_ id: UInt64,_ command: String,_ args: [String] = []) -> ProtocolResponse { adapter.handle(ProtocolRequest(session:"test",epoch:adapter.epoch,requestID:id,command:command,arguments:args)) }
        XCTAssertNil(request(1,"snapshot").error)
        let cases: [(String,[String])] = [("root",[]),("scalar",["field:generation"]),("page",["field:pending"]),("object_past_end",[]),("sequence_past_end",["field:pending"]),("missing",["field:missing"]),("schema",[])]
        for (index,item) in cases.enumerated() {
            let schema = item.0 == "schema" ? "u64:2" : "u64:1"
            let offset = item.0.hasSuffix("past_end") ? "u64:9" : "u64:0"
            let actual = request(UInt64(index+2),"inspect",["u64:1",schema,offset,"u64:1"]+item.1)
            let wire = String(decoding:try ProtocolWire.encodeResponse(actual),as:UTF8.self)
            XCTAssertEqual(Array(wire.split(separator:"\t",omittingEmptySubsequences:false).dropFirst(8)),expected[item.0],item.0)
        }
        XCTAssertEqual(visitedFields,[0]) // Schema mismatch is rejected before field projection.
    }

    func adapter(_ calls: Calls, exact: Bool = false, delivery: Bool = true) throws -> ProtocolAdapter<UInt64, UInt8, UInt64> {
        func bytes(_ value: UInt64) -> [UInt8] {
            (0..<8).map { UInt8(truncatingIfNeeded: value >> ($0 * 8)) }
        }
        let codec = Codec<UInt64, UInt8, UInt64>(
            metadata: { Metadata(name: "wire-regression") },
            encodeState: bytes, decodeState: { _ in 0 }, encodeInput: { [$0] },
            decodeInput: { bytes in
                calls.values.append("decode " + bytes.map { String(format: "%02x", $0) }.joined())
                guard bytes.count == 1, bytes[0] <= 3 else { throw ModelError("decode") }
                return bytes[0]
            }, encodeOutput: bytes,
            encodeOutputInto: { value, sink in try sink.extend(bytes(value)) }
        )
        let model = Model<UInt64, UInt8, UInt64>(
            initialState: { calls.values.append("initial"); return 0 },
            step: { before, input in
                calls.values.append("step \(before) \(input)")
                switch input {
                case 0: return Transition(state: before, disposition: .ignored("private ignored"))
                case 1: return Transition(state: before + 1, outputs: [before + 1])
                case 2: return Transition(state: before, disposition: .rejected("private rejection"))
                default: throw ModelError("private reducer detail")
                }
            },
            checkState: { state in
                calls.values.append("state \(state)")
                return [state <= 2 ? .passed("bounded") : .failed("bounded", "private state detail")]
            },
            cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
            checkTransition: { before, input, transition in
                calls.values.append("transition \(before) \(input) \(transition.state)")
                return [.passed("transition")]
            }, codec: codec
        )
        var authority = ProtocolAuthority()
        authority.deliverInputs = delivery; authority.readExactValues = exact
        return try ProtocolAdapter(session: DebugSession(id: "wire\té😀", model: model, policy: .outOfDomainInjection, equalInputs: ==), authority: authority)
    }

    func request(_ adapter: ProtocolAdapter<UInt64, UInt8, UInt64>, _ id: UInt64, _ command: String, _ arguments: [String] = [], revision: UInt64 = 0) -> ProtocolRequest {
        ProtocolRequest(session: adapter.session.id, epoch: adapter.epoch, requestID: id, expectedRevision: revision, command: command, arguments: arguments)
    }

    func testCompleteStatusSchemaAndNativeInitialQueries() throws {
        let calls = Calls(), a = try adapter(calls)
        XCTAssertEqual(calls.values, ["initial", "state 0"])
        let status = a.handle(request(a, 1, "status", revision: .max))
        XCTAssertNil(status.error)
        XCTAssertEqual(status.fields, [
            "mode":"enum:simulation", "phase":"enum:Ready", "sequence":"u64:0",
            "environment":"string:out-of-domain-injection", "stop":"enum:none",
            "capability.input_delivery":"bool:true", "capability.inspection":"bool:false",
            "capability.watch_configuration":"bool:false", "capability.exact_values":"bool:false",
            "capability.live_control":"bool:false", "capability.live_restore":"bool:false",
            "capability.effects":"bool:false", "capability.probe_queries":"bool:false",
            "capability.probe_configuration":"bool:false", "capability.trace_export":"bool:false",
            "capability.effect_telemetry":"bool:false", "capability.metric_queries":"bool:false",
            "limits.telemetry_snapshot_bytes":"u64:8388608", "limits.frame_bytes":"u64:65536",
            "limits.dedup_entries":"u64:64", "limits.dedup_bytes":"u64:4194304",
            "limits.page_size":"u64:100", "events.dropped":"u64:0"
        ])
        XCTAssertEqual(a.handle(request(a, 2, "snapshot")).fields,
                       ["handle":"u64:1", "snapshot.revision":"u64:0", "snapshot.sequence":"u64:0"])
        XCTAssertEqual(a.handle(request(a, 3, "checks", ["u64:1"], revision: .max)).fields, [
            "checks.complete":"bool:true", "checks.total":"u64:1", "checks.returned":"u64:1",
            "checks.truncated":"bool:false", "checks.first_failure":"none",
            "checks.0.id":"string:bounded", "checks.0.status":"enum:passed"
        ])
        XCTAssertEqual(a.handle(request(a, 4, "outputs", ["u64:1"], revision: .max)).fields, ["outputs.total":"u64:0"])
        XCTAssertEqual(calls.values, ["initial", "state 0"])
    }

    func testNativeOutputEncodingDispositionAndCallbackOrder() throws {
        let calls = Calls(), a = try adapter(calls, exact: true)
        for (index, input) in [1, 0, 2].enumerated() {
            let id = UInt64(index * 4 + 1), revision = UInt64(index)
            XCTAssertEqual(a.handle(request(a, id, "step", [String(format: "bytes:%02x", input)], revision: revision)).fields,
                           ["delivered":"bool:true", "sequence":"u64:\(index + 1)"])
            XCTAssertNil(a.handle(request(a, id + 1, "snapshot", revision: revision + 1)).error)
            var expected = ["outputs.total": index == 0 ? "u64:1" : "u64:0",
                            "disposition":"enum:" + ["accepted", "ignored", "rejected"][index],
                            "outputs.provenance":"enum:simulated", "outputs.payloads":"enum:exact_sensitive",
                            "outputs.truncated":"bool:false"]
            if index == 0 { expected["outputs.0.encoded"] = "bytes:0100000000000000" }
            XCTAssertEqual(a.handle(request(a, id + 2, "outputs", ["u64:\(index + 1)"], revision: .max)).fields, expected)
        }
        XCTAssertEqual(calls.values, ["initial", "state 0",
            "decode 01", "step 0 1", "state 1", "transition 0 1 1",
            "decode 00", "step 1 0", "state 1", "transition 1 0 1",
            "decode 02", "step 1 2", "state 1", "transition 1 2 1"])
        XCTAssertEqual(a.handle(request(a, 20, "checks", ["u64:1"])).error, .staleHandle)
    }

    func testFailureIdentityAndDecodeBeforeEnded() throws {
        let calls = Calls(), a = try adapter(calls)
        for index in 0..<3 {
            XCTAssertNil(a.handle(request(a, UInt64(index + 1), "step", ["bytes:01"], revision: UInt64(index))).error)
        }
        XCTAssertNil(a.handle(request(a, 4, "snapshot", revision: 3)).error)
        XCTAssertEqual(a.handle(request(a, 5, "checks", ["u64:1"], revision: .max)).fields, [
            "checks.complete":"bool:true", "checks.total":"u64:2", "checks.returned":"u64:2", "checks.truncated":"bool:false",
            "checks.first_failure.id":"string:bounded", "checks.first_failure.index":"u64:0",
            "checks.first_failure.phase":"enum:post_state", "checks.first_failure.sequence":"u64:3",
            "checks.0.id":"string:bounded", "checks.0.status":"enum:failed",
            "checks.1.id":"string:transition", "checks.1.status":"enum:passed"
        ])
        XCTAssertEqual(a.handle(request(a, 6, "status")).fields["stop"], "enum:property_failure")
        XCTAssertEqual(a.handle(request(a, 7, "step", ["bytes:01"], revision: 3)).error, .ended)
        XCTAssertEqual(calls.values.last, "decode 01")
        XCTAssertEqual(a.handle(request(a, 8, "step", ["bytes:ff"], revision: 3)).error, .invalidInput)
        XCTAssertEqual(calls.values.last, "decode ff")

        let failedCalls = Calls(), failed = try adapter(failedCalls)
        XCTAssertEqual(failed.handle(request(failed, 1, "step", ["bytes:03"])).fields, ["delivered":"bool:false", "sequence":"u64:0"])
        XCTAssertEqual(failed.handle(request(failed, 2, "status")).fields["stop"], "enum:model_error")
        XCTAssertEqual(failedCalls.values, ["initial", "state 0", "decode 03", "step 0 3"])
    }

    func testTypedDecodeCanonicalizationAndStrictUTF8() throws {
        let a = try adapter(Calls())
        let lower = request(a, 2, "step", ["bytes:0a"]), upper = request(a, 2, "step", ["bytes:0A"])
        XCTAssertEqual(try ProtocolWire.decodeRequest(ProtocolWire.encodeRequest(lower)),
                       try ProtocolWire.decodeRequest(ProtocolWire.encodeRequest(upper)))
        for (command, arguments, error) in [("step", ["bytes:+1"], ProtocolError.malformed),
                                          ("status", ["extra"], .malformed),
                                          ("eval", [], .unsupported),
                                          ("\u{feff}status", [], .unsupported)] {
            XCTAssertThrowsError(try ProtocolWire.decodeRequest(ProtocolWire.encodeRequest(request(a, 2, command, arguments)))) {
                XCTAssertEqual($0 as? ProtocolError, error)
            }
        }
        for raw in ["%FF", "%C0%AF", "%ED%A0%80", "%F4%90%80%80", "%GG", "%0"] {
            XCTAssertThrowsError(try ProtocolWire.unescape(raw, maximum: 8192))
        }
        XCTAssertTrue(try ProtocolWire.unescape("%EF%BB%BF", maximum: 8192).utf8.elementsEqual([0xef, 0xbb, 0xbf]))
        let payload = try ProtocolWire.encodeRequest(request(a, .max, "status", revision: .max))
        XCTAssertEqual(try ProtocolWire.decodeRequest(payload).requestID, UInt64.max)
        XCTAssertThrowsError(try ProtocolWire.decodeRequest(Data([0xef, 0xbb, 0xbf]) + payload))
    }

    func testTerminalStopsRetainCommittedHandleChecksAndExactOutput() throws {
        for cancel in [false, true] {
            let calls = Calls(), a = try adapter(calls, exact: true)
            XCTAssertNil(a.handle(request(a, 1, "step", ["bytes:01"])).error)
            XCTAssertNil(a.handle(request(a, 2, "snapshot", revision: 1)).error)
            let checks = a.handle(request(a, 3, "checks", ["u64:1"])).fields
            let outputs = a.handle(request(a, 4, "outputs", ["u64:1"])).fields
            XCTAssertEqual(outputs["outputs.0.encoded"], "bytes:0100000000000000")
            let stop = a.handle(request(a, 5, cancel ? "cancel" : "step", cancel ? [] : ["bytes:03"], revision: 1))
            XCTAssertNil(stop.error); XCTAssertEqual(stop.revision, 1)
            XCTAssertEqual(a.handle(request(a, 6, "checks", ["u64:1"], revision: .max)).fields, checks)
            XCTAssertEqual(a.handle(request(a, 7, "outputs", ["u64:1"], revision: .max)).fields, outputs)
            XCTAssertEqual(a.handle(request(a, 8, "status")).fields["stop"], cancel ? "enum:cancelled" : "enum:model_error")
            XCTAssertEqual(calls.values, ["initial", "state 0", "decode 01", "step 0 1", "state 1", "transition 0 1 1"] + (cancel ? [] : ["decode 03", "step 1 3"]))
        }
    }

    func testMalformedPipeRequestsDoNotRetireIDsAndEOFDisconnects() throws {
        let calls = Calls(), a = try adapter(calls)
        let incoming = Pipe(), outgoing = Pipe()
        let requests = [request(a, 2, "eval"), request(a, 2, "step", ["bytes:gg"]),
                        request(a, 2, "step", ["bytes:01"]), request(a, 2, "step", ["bytes:01"])]
        for value in requests { try incoming.fileHandleForWriting.write(contentsOf: ProtocolWire.frame(ProtocolWire.encodeRequest(value))) }
        try incoming.fileHandleForWriting.close()
        XCTAssertEqual(try a.serve(input: incoming.fileHandleForReading, output: outgoing.fileHandleForWriting, maxRequests: 5), 4)
        try outgoing.fileHandleForWriting.close()
        var responses: [ProtocolResponse] = []
        let reader = try ProtocolFrameReader()
        try reader.receive(outgoing.fileHandleForReading.readToEnd() ?? Data()) { responses.append(try ProtocolWire.decodeResponse($0)) }
        try reader.finish()
        XCTAssertEqual(responses.map(\.requestID), [0, 0, 2, 2])
        XCTAssertEqual(responses.map(\.error), [.unsupported, .malformed, nil, nil])
        XCTAssertEqual(responses[2], responses[3])
        XCTAssertEqual(calls.values, ["initial", "state 0", "decode 01", "step 0 1", "state 1", "transition 0 1 1"])
        XCTAssertEqual(a.handle(request(a, 3, "status")).error, .disconnected)

        let broken = try adapter(Calls()), partial = Pipe()
        try partial.fileHandleForWriting.write(contentsOf: Data([0, 0, 0, 4, 65]))
        try partial.fileHandleForWriting.close()
        XCTAssertThrowsError(try broken.serve(input: partial.fileHandleForReading, output: .nullDevice, maxRequests: 1))
        XCTAssertEqual(broken.handle(request(broken, 1, "status")).error, .disconnected)
    }

    func testDecoderCannotReenterSessionAndAuthorityPrecedesRevision() throws {
        let base = try adapter(Calls()), session = base.session
        var authority = ProtocolAuthority(); authority.deliverInputs = true
        var calls = 0
        let a = try ProtocolAdapter(session: session, authority: authority, decodeInput: { _ in
            calls += 1
            XCTAssertThrowsError(try session.cancel(expectedRevision: 0)) { XCTAssertEqual($0 as? DebugError, .reentrant) }
            return UInt8(1)
        })
        XCTAssertNil(a.handle(request(a, 1, "step", ["bytes:01"])).error)
        XCTAssertEqual(calls, 1); XCTAssertEqual(session.revision, 1)
        let readOnlyCalls = Calls(), readOnly = try adapter(readOnlyCalls, delivery: false)
        XCTAssertEqual(readOnly.handle(request(readOnly, 1, "step", ["bytes:01"], revision: .max)).error, .unauthorized)
        XCTAssertEqual(readOnly.handle(request(readOnly, 2, "cancel", revision: .max)).error, .unauthorized)
        XCTAssertEqual(readOnlyCalls.values, ["initial", "state 0"])
    }
}

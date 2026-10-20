import Foundation
import XCTest
import Statelessness

final class ValueCodecEncoderCorpusTests: XCTestCase {
    func testSharedRawEncoderScriptsAndStickyWriter() throws {
        let cases = try VCFixture.corpus()["encoder_cases"] as! [[String:Any]]
        XCTAssertGreaterThanOrEqual(cases.count, 6)
        for c in cases {
            let name = c["case"] as! String, expected = c["expected"] as! [String:Any]
            var bytes: [UInt8] = [], results: [[String:Any]] = [], outerError: String?, customCalls = 0
            do {
                try EncodeBuffer.withBuffer(&bytes, maximum: Int(c["max_bytes"] as! String)!) { sink in
                    for action in c["actions"] as! [[String:Any]] {
                        do {
                            switch action["kind"] as! String {
                            case "direct_length":
                                XCTAssertEqual((action["schema"] as! [String:Any])["kind"] as? String, "string")
                                try ValueCodecs.string.encodeLengthRaw(action["value"] as! String, width: UInt8(action["width"] as! Int), into: sink)
                            case "direct_encode":
                                XCTAssertEqual((action["schema"] as! [String:Any])["kind"] as? String, "u16")
                                try ValueCodecs.u16.encodeRaw(UInt16(action["value"] as! String)!, into: sink)
                            case "direct_encode_custom":
                                let codec = TraceValueCodec<TraceUnit>(schema: "script-custom", encode: { _, writer in
                                    customCalls += 1
                                    do { try writer.extend(VCFixture.bytes(action["prefix_hex"] as! String)) }
                                    catch { if action["ignore_prefix_error"] as? Bool != true { throw error } }
                                    if let message = action["returns_error"] as? String { throw ModelError(message) }
                                }, decode: { _ in TraceUnit() })
                                try codec.encodeRaw(TraceUnit(), into: sink)
                            case "append": try sink.extend(VCFixture.bytes(action["bytes_hex"] as! String))
                            case "finish": try sink.finish()
                            default: XCTFail("unsupported encoder fixture action")
                            }
                            results.append(["value":NSNull(), "error":NSNull(), "error_family":NSNull()])
                        } catch {
                            let message = String(describing: error), family: String
                            switch message {
                            case "unsupported length width": family = "unsupported_length_width"
                            case "length overflow": family = "length_overflow"
                            case "encoded blob exceeds byte limit", "encoded blob exceeds byte or allocation limit": family = "encode_limit"
                            default: family = "custom_callback"
                            }
                            results.append(["value":NSNull(), "error":message, "error_family":family])
                        }
                    }
                }
            } catch { outerError = String(describing: error) }
            XCTAssertEqual(VCFixture.hex(bytes), expected["bytes_hex"] as? String, name)
            XCTAssertEqual(try JSONSerialization.data(withJSONObject: results, options: [.sortedKeys]), try JSONSerialization.data(withJSONObject: expected["results"]!, options: [.sortedKeys]), name)
            let finalError = (expected["results"] as! [[String:Any]]).last!["error"] as? String
            XCTAssertEqual(outerError, finalError, name)
            let customActions = (c["actions"] as! [[String:Any]]).filter { $0["kind"] as? String == "direct_encode_custom" }.count
            XCTAssertEqual(customCalls, customActions, name)
        }
    }
    func testRawCallbackLeaseExpiresWithoutPoisoningCaller() throws {
        var escaped: EncodeBuffer?, bytes: [UInt8] = []
        let codec = TraceValueCodec<TraceUnit>(schema: "raw-lease", encode: { _, sink in
            escaped = sink
            try sink.extend([0xaa])
            throw ModelError("raw callback failure")
        }, decode: { _ in TraceUnit() })
        try EncodeBuffer.withBuffer(&bytes, maximum: 4) { caller in
            XCTAssertThrowsError(try codec.encodeRaw(TraceUnit(), into: caller)) {
                XCTAssertEqual(String(describing: $0), "raw callback failure")
            }
            XCTAssertThrowsError(try escaped!.extend([0xff])) { XCTAssertEqual(String(describing: $0), "encode buffer lease expired") }
            XCTAssertThrowsError(try escaped!.finish())
            XCTAssertThrowsError(try escaped!.count)
            try caller.extend([0xbb]); try caller.finish()
        }
        XCTAssertEqual(bytes, [0xaa,0xbb])
        XCTAssertThrowsError(try escaped!.extend([0xff]))
        XCTAssertEqual(bytes, [0xaa,0xbb])
    }

}

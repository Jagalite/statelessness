import Foundation
import XCTest
import Statelessness

final class ValueCodecReaderCorpusTests: XCTestCase {
    func testSharedReaderScriptsAndExactCallbackEvents() throws {
        let fallback = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("conformance/canonical-value-codec-v1.json").path
        let path = ProcessInfo.processInfo.environment["STATELESS_VALUE_CORPUS"] ?? fallback
        let corpus = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: path))) as! [String:Any]
        let cases = corpus["reader_cases"] as! [[String:Any]]
        XCTAssertEqual(cases.count, 19)
        for c in cases {
            let name = c["case"] as! String, expected = c["expected"] as! [String:Any]
            var limits = DecodeLimits()
            for (key,value) in c["limits"] as? [String:Any] ?? [:] {
                let n = UInt64(value as! String)!
                switch key { case "bytes": limits.bytes = n; case "allocation": limits.allocation = n; case "elements": limits.elements = n; case "depth": limits.depth = n; case "work": limits.work = n; default: XCTFail("unknown limit \(key)") }
            }
            let hex = c["bytes_hex"] as! String
            var input: [UInt8] = []
            for n in stride(from: 0, to: hex.count, by: 2) {
                let start = hex.index(hex.startIndex, offsetBy: n)
                input.append(UInt8(hex[start..<hex.index(start, offsetBy: 2)], radix: 16)!)
            }
            var events: [String] = [], results: [[String:Any]] = [], constructionError: String?
            func decode(_ schema: [String:Any], from reader: ValueDecoder, width: UInt8? = nil) throws -> Any {
                switch schema["kind"] as! String {
                case "u8": return String(try ValueCodecs.u8.decode(from: reader))
                case "bool": return try ValueCodecs.bool.decode(from: reader)
                case "unit": _ = try ValueCodecs.unit.decode(from: reader); return NSNull()
                case "string":
                    if let width { return try ValueCodecs.string.decodeLength(width: width, from: reader) }
                    return try ValueCodecs.string.decode(from: reader)
                case "vector":
                    XCTAssertEqual((schema["element"] as! [String:Any])["kind"] as? String, "u8")
                    let codec = ValueCodecs.vector(ValueCodecs.u8), values: [UInt8]
                    if let width { values = try codec.decodeLength(width: width, from: reader) }
                    else { values = try codec.decode(from: reader) }
                    return values.map(String.init)
                default: throw ModelError("unsupported reader fixture schema")
                }
            }
            func execute(_ action: [String:Any], on reader: ValueDecoder) throws -> Any {
                switch action["kind"] as! String {
                case "take": return try reader.take(UInt64(action["count"] as! String)!).map { String(format: "%02x", $0) }.joined()
                case "collection": try reader.collection(UInt64(action["count"] as! String)!, elementSize: UInt64(action["element_size"] as! String)!); return NSNull()
                case "finish": try reader.finish(); return NSNull()
                case "value": return try reader.value { try decode(action["schema"] as! [String:Any], from: $0) }
                case "direct_decode": return try decode(action["schema"] as! [String:Any], from: reader)
                case "direct_length": return try decode(action["schema"] as! [String:Any], from: reader, width: UInt8(action["width"] as! Int))
                case "value_custom":
                    return try reader.value { child -> Any in
                        events.append("enter")
                        for operation in action["body"] as! [[String:Any]] {
                            let kind = operation["kind"] as! String
                            do { _ = try execute(operation, on: child); events.append(kind + ":ok") }
                            catch {
                                let message = String(describing: error)
                                events.append(kind + ":error:" + message)
                                if operation["on_error"] as? String == "swallow" { events.append("swallow") }
                                else { events.append("propagate:" + message); throw error }
                            }
                        }
                        let returned = action["returns"] as! [String:Any]
                        if let message = returned["error"] as? String { events.append("return_error:" + message); throw ModelError(message) }
                        events.append("return_ok"); return returned["ok"] ?? NSNull()
                    }
                default: throw ModelError("unsupported reader fixture action")
                }
            }
            do {
                let reader = try ValueDecoder(input, limits: limits)
                for action in c["actions"] as! [[String:Any]] {
                    do { results.append(["value":try execute(action, on: reader), "error":NSNull()]) }
                    catch { results.append(["value":NSNull(), "error":String(describing: error)]) }
                }
            } catch { constructionError = String(describing: error) }
            XCTAssertEqual(constructionError, expected["construction_error"] as? String, name)
            XCTAssertEqual(events, expected["callback_events"] as! [String], name)
            XCTAssertEqual(try JSONSerialization.data(withJSONObject: results, options: [.sortedKeys]), try JSONSerialization.data(withJSONObject: expected["results"]!, options: [.sortedKeys]), name)
        }
    }
}

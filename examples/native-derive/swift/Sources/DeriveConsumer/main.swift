import Foundation
import Statelessness
struct Point: Equatable { let x: UInt32; let y: String }
enum Variant: Equatable { case empty, point(Point) }
struct Row: Codable { let id: String; let hex: String }
func hex(_ bytes: [UInt8]) -> String { bytes.map { String(format: "%02x", $0) }.joined() }
func bytes(_ hex: String) throws -> [UInt8] {
    guard hex.count % 2 == 0 else { throw ModelError("invalid hex") }
    var result: [UInt8] = [], index = hex.startIndex
    while index < hex.endIndex { let end = hex.index(index, offsetBy: 2); guard let byte = UInt8(hex[index..<end], radix: 16) else { throw ModelError("invalid hex") }; result.append(byte); index = end }
    return result
}
let point = try TraceRecords.codec(schema: "Point", fields: TraceFields.first(TraceField<Point, UInt32>.ordinary("x", ValueCodecs.u32, get: { $0.x })).appending(TraceField<Point, String>.ordinary("y", ValueCodecs.string, get: { $0.y })), construct: { Point(x: $0.prefix, y: $0.last) })
let empty = TraceEnumCase<Variant, TraceUnit>(tag: 0, label: "Empty", payload: ValueCodecs.unit, construct: { _ in .empty })
let item = TraceEnumCase<Variant, Point>(tag: 1, label: "Point", payload: point, construct: { .point($0) })
let variant = try TraceEnums.codec(schema: "Variant", tagWidth: .u8, cases: [empty.erased(), item.erased()], select: { value in switch value { case .empty: return empty.select(TraceUnit()); case .point(let p): return item.select(p) } })
let args = CommandLine.arguments
let expected = Point(x: 7, y: "hi")
var rows: [Row]
if args.count == 4 && args[1] == "transcode" {
    rows = try JSONDecoder().decode([Row].self, from: Data(contentsOf: URL(fileURLWithPath: args[2])))
    guard rows.count == 3 else { throw ModelError("wrong count") }
    rows = try rows.map { row in
        let input = try bytes(row.hex)
        switch row.id {
        case "point": let value = try point.fromTrace(input); guard value == expected else { throw ModelError("point mismatch") }; return Row(id: row.id, hex: hex(try point.traceBytes(value, maximum: 1024)))
        case "empty", "variant": let value = try variant.fromTrace(input); guard value == (row.id == "empty" ? .empty : .point(expected)) else { throw ModelError("variant mismatch") }; return Row(id: row.id, hex: hex(try variant.traceBytes(value, maximum: 1024)))
        default: throw ModelError("unknown case")
        }
    }
} else if args.count == 3 && args[1] == "produce" {
    rows = [Row(id: "point", hex: hex(try point.traceBytes(expected, maximum: 1024))), Row(id: "empty", hex: hex(try variant.traceBytes(.empty, maximum: 1024))), Row(id: "variant", hex: hex(try variant.traceBytes(.point(expected), maximum: 1024)))]
} else { throw ModelError("usage: produce DEST | transcode SOURCE DEST") }
try JSONEncoder().encode(rows).write(to: URL(fileURLWithPath: args.last!))
print("{\"case_count\":3}")

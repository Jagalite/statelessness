import Foundation

public struct FormatError: Error, CustomStringConvertible {
    public let description: String
    public init(_ message: String) { description = message }
}
/// JSON's strings and object keys use exact scalar equality, unlike Swift's
/// default canonically-equivalent String dictionaries. Numbers retain integer
/// spelling and are never routed through Double or NSNumber.
public indirect enum JSON: Equatable, Sendable {
    case null
    case bool(Bool)
    case number(String)
    case string(String)
    case array([JSON])
    case object([ExactString: JSON])
    public static func obj(_ values: [String: JSON]) -> JSON { .object(Dictionary(uniqueKeysWithValues: values.map { (ExactString($0.key), $0.value) })) }
    public static func int(_ value: Int) -> JSON { .number(String(value)) }
    public static func uint(_ value: UInt64) -> JSON { .number(String(value)) }
    public static func == (a: JSON, b: JSON) -> Bool {
        switch (a, b) {
        case (.null, .null): return true
        case let (.bool(x), .bool(y)): return x == y
        case let (.number(x), .number(y)), let (.string(x), .string(y)): return exactText(x, y)
        case let (.array(x), .array(y)): return x == y
        case let (.object(x), .object(y)): return x == y
        default: return false
        }
    }
}
private func validNumber(_ s: String) -> Bool {
    let bytes = Array(s.utf8)
    guard !bytes.isEmpty else { return false }
    let start = bytes[0] == 45 ? 1 : 0
    guard start < bytes.count, bytes[start...].allSatisfy({ $0 >= 48 && $0 <= 57 }) else { return false }
    if bytes[start] == 48 && bytes.count > start + 1 { return false }
    return start == 1 ? Int64(s) != nil : UInt64(s) != nil
}
private struct JSONParser {
    let bytes: [UInt8]
    var index = 0
    mutating func space() { while index < bytes.count && [UInt8(32), 9, 10, 13].contains(bytes[index]) { index += 1 } }
    mutating func take(_ byte: UInt8) -> Bool { if index < bytes.count && bytes[index] == byte { index += 1; return true }; return false }
    mutating func expect(_ byte: UInt8) throws { if !take(byte) { throw FormatError("unexpected JSON byte at \(index)") } }
    mutating func hex4() throws -> UInt32 {
        guard bytes.count - index >= 4 else { throw FormatError("truncated Unicode escape") }
        var result: UInt32 = 0
        for _ in 0..<4 {
            let b = bytes[index]; index += 1
            let v: UInt32
            switch b { case 48...57: v = UInt32(b - 48); case 65...70: v = UInt32(b - 55); case 97...102: v = UInt32(b - 87); default: throw FormatError("invalid Unicode escape") }
            result = result * 16 + v
        }
        return result
    }
    mutating func quoted() throws -> String {
        try expect(34)
        var result: [UInt8] = []
        while index < bytes.count {
            let b = bytes[index]; index += 1
            if b == 34 { guard String(bytes: result, encoding: .utf8) != nil else { throw FormatError("invalid string UTF-8") }; return String(decoding: result, as: UTF8.self) }
            if b < 32 { throw FormatError("unescaped control character") }
            if b != 92 { result.append(b); continue }
            guard index < bytes.count else { throw FormatError("truncated escape") }
            let e = bytes[index]; index += 1
            switch e {
            case 34, 92, 47: result.append(e)
            case 98: result.append(8)
            case 102: result.append(12)
            case 110: result.append(10)
            case 114: result.append(13)
            case 116: result.append(9)
            case 117:
                var value = try hex4()
                if (0xdc00...0xdfff).contains(value) { throw FormatError("unpaired low surrogate") }
                if (0xd800...0xdbff).contains(value) {
                    try expect(92); try expect(117); let low = try hex4()
                    guard (0xdc00...0xdfff).contains(low) else { throw FormatError("unpaired high surrogate") }
                    value = 0x10000 + (value - 0xd800) * 1024 + low - 0xdc00
                }
                guard let scalar = Unicode.Scalar(value) else { throw FormatError("invalid Unicode scalar") }
                result.append(contentsOf: String(scalar).utf8)
            default: throw FormatError("invalid escape")
            }
        }
        throw FormatError("unterminated string")
    }
    mutating func value(_ depth: Int) throws -> JSON {
        guard depth <= 64 else { throw FormatError("JSON nesting limit") }; space()
        guard index < bytes.count else { throw FormatError("missing JSON value") }
        if bytes[index] == 34 { return .string(try quoted()) }
        if take(123) {
            var result: [ExactString: JSON] = [:]; space()
            if take(125) { return .object(result) }
            while true {
                space(); let key = ExactString(try quoted()); space(); try expect(58)
                if result[key] != nil { throw FormatError("duplicate object key") }
                result[key] = try value(depth + 1); space()
                if take(125) { return .object(result) }; try expect(44)
            }
        }
        if take(91) {
            var result: [JSON] = []; space(); if take(93) { return .array(result) }
            while true { result.append(try value(depth + 1)); space(); if take(93) { return .array(result) }; try expect(44) }
        }
        for (word, result) in [("null", JSON.null), ("true", .bool(true)), ("false", .bool(false))] {
            let token = Array(word.utf8)
            if bytes[index...].starts(with: token) { index += token.count; return result }
        }
        let start = index; _ = take(45)
        while index < bytes.count && bytes[index] >= 48 && bytes[index] <= 57 { index += 1 }
        let token = String(decoding: bytes[start..<index], as: UTF8.self)
        guard validNumber(token) else { throw FormatError("invalid integer") }
        if index < bytes.count && [UInt8(46), 69, 101].contains(bytes[index]) { throw FormatError("floating numbers are outside the profile") }
        return .number(token)
    }
}
public func parseJSON(_ data: [UInt8], maximum: Int = 64 * 1024 * 1024) throws -> JSON {
    guard maximum >= 0, data.count <= maximum, String(bytes: data, encoding: .utf8) != nil else { throw FormatError("JSON byte limit or invalid UTF-8") }
    var parser = JSONParser(bytes: data)
    let result = try parser.value(0); parser.space()
    guard parser.index == data.count else { throw FormatError("trailing JSON data") }; return result
}
private struct JSONEncoder {
    var output: [UInt8] = []
    let maximum: Int
    mutating func write(_ bytes: [UInt8]) throws {
        guard bytes.count <= maximum - output.count else { throw FormatError("JSON output byte limit") }
        output.append(contentsOf: bytes)
    }
    mutating func text(_ value: String) throws {
        try write([34])
        let hex = Array("0123456789abcdef".utf8)
        for b in value.utf8 {
            if b == 34 || b == 92 { try write([92, b]) }
            else if b < 32 { try write([92, 117, 48, 48, hex[Int(b >> 4)], hex[Int(b & 15)]]) }
            else { try write([b]) }
        }
        try write([34])
    }
    mutating func value(_ v: JSON, _ depth: Int) throws {
        guard depth <= 64 else { throw FormatError("JSON nesting limit") }
        switch v {
        case .null: try write(Array("null".utf8))
        case let .bool(b): try write(Array((b ? "true" : "false").utf8))
        case let .number(n): guard validNumber(n) else { throw FormatError("invalid integer") }; try write(Array(n.utf8))
        case let .string(s): try text(s)
        case let .array(a):
            try write([91]); for (i, x) in a.enumerated() { if i > 0 { try write([44]) }; try value(x, depth + 1) }; try write([93])
        case let .object(o):
            try write([123])
            // Stable byte ordering, without prescribing JSON object iteration semantics.
            let keys = o.keys.sorted { $0.value.utf8.lexicographicallyPrecedes($1.value.utf8) }
            for (i, k) in keys.enumerated() { if i > 0 { try write([44]) }; try text(k.value); try write([58]); if let x = o[k] { try value(x, depth + 1) } }
            try write([125])
        }
    }
}
public func renderJSON(_ value: JSON, maximum: Int = 64 * 1024 * 1024) throws -> [UInt8] {
    guard maximum >= 0 else { throw FormatError("negative JSON byte limit") }
    var encoder = JSONEncoder(maximum: maximum); try encoder.value(value, 0); return encoder.output
}
public func objectFields(_ value: JSON?, required: [String], optional: [String] = []) throws -> [String: JSON] {
    guard case let .object(o) = value else { throw FormatError("expected object") }
    let allowed = Set((required + optional).map(ExactString.init))
    guard required.allSatisfy({ o[ExactString($0)] != nil }), o.keys.allSatisfy({ allowed.contains($0) }) else { throw FormatError("missing or unknown object field") }
    return Dictionary(uniqueKeysWithValues: o.map { ($0.key.value, $0.value) })
}
public func readString(_ value: JSON?) throws -> String { guard case let .string(s) = value else { throw FormatError("expected string") }; return s }
public func readInteger(_ value: JSON?, maximum: UInt64 = (1 << 53) - 1) throws -> UInt64 {
    guard case let .number(s) = value, validNumber(s), let n = UInt64(s == "-0" ? "0" : s), n <= maximum else { throw FormatError("invalid integer") }; return n
}
public func readBool(_ value: JSON?) throws -> Bool { guard case let .bool(b) = value else { throw FormatError("expected boolean") }; return b }
public func readArray(_ value: JSON?, maximum: Int = 250_000) throws -> [JSON] { guard case let .array(a) = value, a.count <= maximum else { throw FormatError("expected bounded array") }; return a }
public func readU64(_ value: JSON?) throws -> UInt64 {
    let s = try readString(value), bytes = Array(s.utf8)
    guard !bytes.isEmpty, bytes.count <= 20, (bytes.count == 1 || bytes[0] != 48), bytes.allSatisfy({ $0 >= 48 && $0 <= 57 }), let n = UInt64(s) else { throw FormatError("invalid decimal u64") }; return n
}

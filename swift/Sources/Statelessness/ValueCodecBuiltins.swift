import Foundation

/// Explicit typed schemas. No dynamic tags, Codable inference, or host heap weights.
public enum ValueCodecs {
    private static func integer<T: FixedWidthInteger>(_ type: T.Type, schema: String) -> TraceValueCodec<T> {
        TraceValueCodec(schema: schema, layout: Rust64ValueLayout(size: UInt64(T.bitWidth / 8), identity: schema), encode: { value, sink in
            var bits = T.Magnitude(truncatingIfNeeded: value), bytes: [UInt8] = []
            for _ in 0..<(T.bitWidth / 8) { bytes.append(UInt8(truncatingIfNeeded: bits)); bits >>= 8 }
            try sink.extend(bytes)
        }, decode: { reader in
            let bytes = try reader.take(UInt64(T.bitWidth / 8)); var bits: T.Magnitude = 0
            for (offset, byte) in bytes.enumerated() { bits |= T.Magnitude(byte) << (offset * 8) }
            return T(truncatingIfNeeded: bits)
        })
    }
    public static var u8: TraceValueCodec<UInt8> { integer(UInt8.self, schema: "u8") }
    public static var u16: TraceValueCodec<UInt16> { integer(UInt16.self, schema: "u16") }
    public static var u32: TraceValueCodec<UInt32> { integer(UInt32.self, schema: "u32") }
    public static var u64: TraceValueCodec<UInt64> { integer(UInt64.self, schema: "u64") }
    public static var i8: TraceValueCodec<Int8> { integer(Int8.self, schema: "i8") }
    public static var i16: TraceValueCodec<Int16> { integer(Int16.self, schema: "i16") }
    public static var i32: TraceValueCodec<Int32> { integer(Int32.self, schema: "i32") }
    public static var i64: TraceValueCodec<Int64> { integer(Int64.self, schema: "i64") }
    public static var u128: TraceValueCodec<TraceUInt128> {
        TraceValueCodec(schema: "u128", layout: Rust64ValueLayout(size: 16, identity: "u128"), encode: { value, sink in
            var bytes: [UInt8] = []
            for limb in [value.low,value.high] { for i in 0..<8 { bytes.append(UInt8(truncatingIfNeeded: limb >> (i * 8))) } }; try sink.extend(bytes)
        }, decode: { reader in
            // Raw limb reads preserve the scalar's single value-entry budget.
            let low = try u64.decode(from: reader), high = try u64.decode(from: reader)
            return TraceUInt128(high: high, low: low)
        })
    }
    public static var i128: TraceValueCodec<TraceInt128> {
        TraceValueCodec(schema: "i128", layout: Rust64ValueLayout(size: 16, identity: "i128"), encode: { value, sink in
            var bytes: [UInt8] = []
            for limb in [value.low,UInt64(bitPattern:value.high)] { for i in 0..<8 { bytes.append(UInt8(truncatingIfNeeded: limb >> (i * 8))) } }; try sink.extend(bytes)
        }, decode: { reader in
            let low = try u64.decode(from: reader), high = try i64.decode(from: reader)
            return TraceInt128(high: high, low: low)
        })
    }
    public static var bool: TraceValueCodec<Bool> {
        TraceValueCodec(schema: "bool", layout: Rust64ValueLayout(size: 1, identity: "bool"), encode: { try u8.encodeRaw($0 ? 1 : 0, into: $1) }, decode: { reader in
            switch try reader.value(u8) { case 0: return false; case 1: return true; default: throw ModelError("invalid boolean") }
        })
    }
    public static var unit: TraceValueCodec<TraceUnit> {
        TraceValueCodec(schema: "unit", layout: Rust64ValueLayout(size: 0, identity: "unit"), encode: { _,_ in }, decode: { _ in TraceUnit() })
    }
    private static func writeLength(_ count: Int, width: UInt8, sink: EncodeBuffer) throws {
        switch width {
        case 1: guard let n = UInt8(exactly: count) else { throw ModelError("length overflow") }; try u8.encodeRaw(n, into: sink)
        case 2: guard let n = UInt16(exactly: count) else { throw ModelError("length overflow") }; try u16.encodeRaw(n, into: sink)
        case 4: guard let n = UInt32(exactly: count) else { throw ModelError("length overflow") }; try u32.encodeRaw(n, into: sink)
        default: throw ModelError("unsupported length width")
        }
    }
    private static func readLength(_ width: UInt8, reader: ValueDecoder) throws -> UInt64 {
        switch width { case 1: return UInt64(try reader.value(u8)); case 2: return UInt64(try reader.value(u16)); case 4: return UInt64(try reader.value(u32)); default: throw ModelError("unsupported length width") }
    }
    public static var string: TraceValueCodec<String> {
        let encode: (String,UInt8,EncodeBuffer)throws->Void = { text,width,sink in
            let count = text.utf8.count
            try writeLength(count, width: width, sink: sink)
            try sink.requireAdditional(UInt64(count))
            try sink.extend(Array(text.utf8))
        }
        let decode: (UInt8,ValueDecoder)throws->String = { width,reader in
            let count = try readLength(width, reader: reader)
            try reader.collection(count, elementSize: 1); try reader.consumeStringWork(count)
            let bytes = try reader.take(count)
            guard String(bytes: bytes, encoding: .utf8) != nil else { throw ModelError("invalid UTF-8") }
            return String(decoding: bytes, as: UTF8.self)
        }
        return TraceValueCodec(schema: "String", layout: Rust64ValueLayout(size: 24, identity: "String"), encode: { try encode($0,4,$1) }, decode: { try decode(4,$0) }, encodeLength: encode, decodeLength: decode)
    }
    public static func vector<T>(_ element: TraceValueCodec<T>) -> TraceValueCodec<[T]> {
        let encode: ([T],UInt8,EncodeBuffer)throws->Void = { values,width,sink in
            try writeLength(values.count, width: width, sink: sink)
            for value in values { try element.encodeRaw(value, into: sink) }
        }
        let decode: (UInt8,ValueDecoder)throws->[T] = { width,reader in
            let count = try readLength(width, reader: reader)
            try reader.collection(count, elementSize: element.elementSize())
            guard let n = Int(exactly: count),n < Int.max / max(1,MemoryLayout<T>.stride) else { throw ModelError("decode allocation failed") }
            var values: [T] = []; values.reserveCapacity(n)
            for _ in 0..<n { values.append(try reader.value(element)) }; return values
        }
        return TraceValueCodec(schema: "Vec<\(element.schema)>", layout: Rust64ValueLayout(size: 24, identity: "Vec header"), encode: { try encode($0,4,$1) }, decode: { try decode(4,$0) }, encodeLength: encode, decodeLength: decode).restrictingDirections(encoding: element.supportsEncoding, decoding: element.supportsDecoding)
    }
    public static func option<T>(_ element: TraceValueCodec<T>, layout: Rust64ValueLayout? = nil) -> TraceValueCodec<T?> {
        TraceValueCodec(schema: "Option<\(element.schema)>", layout: layout, encode: { value,sink in
            switch value { case .none: try u8.encodeRaw(0, into: sink); case .some(let value): try u8.encodeRaw(1, into: sink); try element.encodeRaw(value, into: sink) }
        }, decode: { reader in
            switch try reader.value(u8) { case 0: return .none; case 1: return .some(try reader.value(element)); default: throw ModelError("invalid option tag") }
        }).restrictingDirections(encoding: element.supportsEncoding, decoding: element.supportsDecoding)
    }
    public static func array<T>(_ element: TraceValueCodec<T>, count: UInt64, layout: Rust64ValueLayout? = nil) -> TraceValueCodec<[T]> {
        TraceValueCodec(schema: "[\(element.schema);\(count)]", layout: layout, encode: { values,sink in
            guard UInt64(values.count) == count else { throw ModelError("array length") }
            for value in values { try element.encodeRaw(value, into: sink) }
        }, decode: { reader in
            try reader.collection(count, elementSize: element.elementSize())
            guard let n = Int(exactly: count),n < Int.max / max(1,MemoryLayout<T>.stride) else { throw ModelError("decode allocation failed") }
            var values: [T] = []; values.reserveCapacity(n)
            for _ in 0..<n { values.append(try reader.value(element)) }; return values
        }).restrictingDirections(encoding: element.supportsEncoding, decoding: element.supportsDecoding)
    }
    public static func tuple1<A>(_ a: TraceValueCodec<A>, layout: Rust64ValueLayout? = nil) -> TraceValueCodec<TraceTuple1<A>> {
        TraceValueCodec(schema: "(\(a.schema),)", layout: layout, encode: { try a.encodeRaw($0.value, into: $1) }, decode: { TraceTuple1(try $0.value(a)) }).restrictingDirections(encoding: a.supportsEncoding, decoding: a.supportsDecoding)
    }
    public static func tuple2<A,B>(_ a: TraceValueCodec<A>, _ b: TraceValueCodec<B>, layout: Rust64ValueLayout? = nil) -> TraceValueCodec<(A,B)> {
        TraceValueCodec(schema: "(\(a.schema),\(b.schema))", layout: layout, encode: { try a.encodeRaw($0.0, into: $1); try b.encodeRaw($0.1, into: $1) }, decode: { (try $0.value(a),try $0.value(b)) }).restrictingDirections(encoding: a.supportsEncoding && b.supportsEncoding, decoding: a.supportsDecoding && b.supportsDecoding)
    }
    public static func tuple3<A,B,C>(_ a: TraceValueCodec<A>, _ b: TraceValueCodec<B>, _ c: TraceValueCodec<C>, layout: Rust64ValueLayout? = nil) -> TraceValueCodec<(A,B,C)> {
        TraceValueCodec(schema: "(\(a.schema),\(b.schema),\(c.schema))", layout: layout, encode: { try a.encodeRaw($0.0, into: $1); try b.encodeRaw($0.1, into: $1); try c.encodeRaw($0.2, into: $1) }, decode: { (try $0.value(a),try $0.value(b),try $0.value(c)) }).restrictingDirections(encoding: a.supportsEncoding && b.supportsEncoding && c.supportsEncoding, decoding: a.supportsDecoding && b.supportsDecoding && c.supportsDecoding)
    }
    public static func tuple4<A,B,C,D>(_ a: TraceValueCodec<A>, _ b: TraceValueCodec<B>, _ c: TraceValueCodec<C>, _ d: TraceValueCodec<D>, layout: Rust64ValueLayout? = nil) -> TraceValueCodec<(A,B,C,D)> {
        TraceValueCodec(schema: "(\(a.schema),\(b.schema),\(c.schema),\(d.schema))", layout: layout, encode: { try a.encodeRaw($0.0, into: $1); try b.encodeRaw($0.1, into: $1); try c.encodeRaw($0.2, into: $1); try d.encodeRaw($0.3, into: $1) }, decode: { (try $0.value(a),try $0.value(b),try $0.value(c),try $0.value(d)) }).restrictingDirections(encoding: a.supportsEncoding && b.supportsEncoding && c.supportsEncoding && d.supportsEncoding, decoding: a.supportsDecoding && b.supportsDecoding && c.supportsDecoding && d.supportsDecoding)
    }
    public static func oracleState<S,H>(_ model: TraceValueCodec<S>, _ history: TraceValueCodec<H>, layout: Rust64ValueLayout? = nil) -> TraceValueCodec<OracleState<S,H>> {
        TraceValueCodec(schema: "OracleState<\(model.schema),\(history.schema)>", layout: layout, encode: { value,sink in try model.encodeRaw(value.model, into: sink); try history.encodeRaw(value.oracle, into: sink) }, decode: { OracleState(model: try $0.value(model), oracle: try $0.value(history)) }).restrictingDirections(encoding: model.supportsEncoding && history.supportsEncoding, decoding: model.supportsDecoding && history.supportsDecoding)
    }
}

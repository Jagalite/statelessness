/// Logical structural request weights under an explicitly selected Rust64 layout.
/// This is not Swift MemoryLayout, allocation measurement, or a compatibility hash.
public struct Rust64ValueLayout: Equatable {
    public let size: UInt64
    public let identity: String
    public let alignment: UInt64?
    public init(size: UInt64, identity: String, alignment: UInt64? = nil) { self.size = size; self.identity = identity; self.alignment = alignment }
}
public struct TraceUnit: Equatable { public init() {} }
public struct TraceTuple1<A> { public let value: A; public init(_ value: A) { self.value = value } }

/// Handwritten composition is cooperative. Its callbacks must not mutate inputs
/// concurrently, and arbitrary callback allocation/work lies outside decode limits.
public struct TraceValueCodec<Value> {
    public let schema: String
    public let layout: Rust64ValueLayout?
    public var supportsEncoding: Bool { encoder != nil }
    public var supportsDecoding: Bool { decoder != nil }
    public var supportsLengthEncoding: Bool { lengthEncoder != nil }
    public var supportsLengthDecoding: Bool { lengthDecoder != nil }
    private let variant: (Value) -> String?
    public func traceVariant(_ value: Value) -> String? { variant(value) }
    private let encoder: ((Value, EncodeBuffer) throws -> Void)?
    private let decoder: ((ValueDecoder) throws -> Value)?
    private let lengthEncoder: ((Value, UInt8, EncodeBuffer) throws -> Void)?
    private let lengthDecoder: ((UInt8, ValueDecoder) throws -> Value)?
    public init(schema: String, layout: Rust64ValueLayout? = nil, variant: @escaping (Value) -> String? = { _ in nil },
                encode: @escaping (Value, EncodeBuffer) throws -> Void,
                decode: @escaping (ValueDecoder) throws -> Value) {
        self.init(schema: schema, layout: layout, variant: variant, encoder: encode, decoder: decode)
    }
    public init(schema: String, layout: Rust64ValueLayout? = nil, variant: @escaping (Value) -> String? = { _ in nil },
                encodeOnly: @escaping (Value, EncodeBuffer) throws -> Void) {
        self.init(schema: schema, layout: layout, variant: variant, encoder: encodeOnly, decoder: nil)
    }
    public init(schema: String, layout: Rust64ValueLayout? = nil,
                decodeOnly: @escaping (ValueDecoder) throws -> Value) {
        self.init(schema: schema, layout: layout, encoder: nil, decoder: decodeOnly)
    }
    /// Each Rust trait is independent; reject only when all four callbacks are absent.
    public init(schema: String, layout: Rust64ValueLayout? = nil, variant: @escaping (Value) -> String? = { _ in nil },
                encoding: ((Value, EncodeBuffer) throws -> Void)? = nil,
                decoding: ((ValueDecoder) throws -> Value)? = nil,
                encodingLength: ((Value, UInt8, EncodeBuffer) throws -> Void)? = nil,
                decodingLength: ((UInt8, ValueDecoder) throws -> Value)? = nil) throws {
        guard encoding != nil || decoding != nil || encodingLength != nil || decodingLength != nil else {
            throw ModelError("value codec requires an encoding or decoding direction")
        }
        self.init(schema: schema, layout: layout, variant: variant, encoder: encoding, decoder: decoding,
                  lengthEncoder: encodingLength, lengthDecoder: decodingLength)
    }
    init(schema: String, layout: Rust64ValueLayout?, variant: @escaping (Value) -> String? = { _ in nil },
                 encoder: ((Value, EncodeBuffer) throws -> Void)?, decoder: ((ValueDecoder) throws -> Value)?,
                 lengthEncoder: ((Value, UInt8, EncodeBuffer) throws -> Void)? = nil,
                 lengthDecoder: ((UInt8, ValueDecoder) throws -> Value)? = nil) {
        self.schema = schema; self.layout = layout; self.variant = variant
        self.encoder = encoder; self.decoder = decoder
        self.lengthEncoder = lengthEncoder; self.lengthDecoder = lengthDecoder
    }
    init(schema: String, layout: Rust64ValueLayout?, variant: @escaping (Value) -> String? = { _ in nil },
         encode: @escaping (Value, EncodeBuffer) throws -> Void, decode: @escaping (ValueDecoder) throws -> Value,
         encodeLength: @escaping (Value, UInt8, EncodeBuffer) throws -> Void,
         decodeLength: @escaping (UInt8, ValueDecoder) throws -> Value) {
        self.init(schema: schema, layout: layout, variant: variant, encoder: encode, decoder: decode, lengthEncoder: encodeLength, lengthDecoder: decodeLength)
    }
    func requireEncoding() throws {
        guard supportsEncoding else { throw ModelError("value codec does not support encoding") }
    }
    func requireDecoding() throws {
        guard supportsDecoding else { throw ModelError("value codec does not support decoding") }
    }
    func requireLengthEncoding() throws {
        guard supportsLengthEncoding else { throw ModelError("value codec does not support length encoding") }
    }
    func requireLengthDecoding() throws {
        guard supportsLengthDecoding else { throw ModelError("value codec does not support length decoding") }
    }
    /// Composition may have neither trait (for example an encode-only field
    /// paired with a decode-only field). No placeholder callback is installed.
    func restrictingDirections(encoding: Bool, decoding: Bool) -> Self {
        Self(schema: schema, layout: layout, variant: variant,
             encoder: encoding ? encoder : nil, decoder: decoding ? decoder : nil,
             lengthEncoder: encoding ? lengthEncoder : nil, lengthDecoder: decoding ? lengthDecoder : nil)
    }
    /// Appends; does not clear the destination. The callback lease expires on return.
    public func encode(_ value: Value, into sink: EncodeBuffer) throws {
        try requireEncoding(); try sink.sublease { try encoder!(value, $0) }
    }
    /// Direct trait-style encoding: returned errors alone do not poison the writer.
    public func encodeRaw(_ value: Value, into sink: EncodeBuffer) throws {
        try requireEncoding(); try sink.rawSublease { try encoder!(value, $0) }
    }
    public func encodeLengthRaw(_ value: Value, width: UInt8, into sink: EncodeBuffer) throws {
        try requireLengthEncoding()
        let lengthEncoder = lengthEncoder!
        try sink.rawSublease { try lengthEncoder(value, width, $0) }
    }
    /// Raw concrete decode, deliberately without an additional value guard.
    public func decode(from reader: ValueDecoder) throws -> Value {
        try requireDecoding(); return try decoder!(reader)
    }
    public func encodeLength(_ value: Value, width: UInt8, into sink: EncodeBuffer) throws {
        try requireLengthEncoding()
        let lengthEncoder = lengthEncoder!
        try sink.sublease { try lengthEncoder(value, width, $0) }
    }
    /// Direct length override; the guarded prefix is its only automatic entry.
    public func decodeLength(width: UInt8, from reader: ValueDecoder) throws -> Value {
        try requireLengthDecoding()
        let lengthDecoder = lengthDecoder!
        return try lengthDecoder(width, reader)
    }
    public func traceBytes(_ value: Value, maximum: Int) throws -> [UInt8] {
        try requireEncoding()
        var bytes: [UInt8] = []
        try EncodeBuffer.withBuffer(&bytes, maximum: maximum) { try encode(value, into: $0) }
        return bytes
    }
    public func fromTrace(_ bytes: [UInt8], limits: DecodeLimits = DecodeLimits()) throws -> Value {
        try requireDecoding()
        let reader = try ValueDecoder(bytes, limits: limits)
        let value = try reader.value(self)
        try reader.finish(); return value
    }
    /// Select a length override as the ordinary codec, without adding a raw
    /// reader value guard. Top-level fromTrace still enters its normal guard.
    public func withLength(_ width: UInt8) -> Self {
        let encode: ((Value, EncodeBuffer) throws -> Void)? = supportsLengthEncoding ? { value,sink in
            try self.encodeLengthRaw(value,width:width,into:sink)
        } : nil
        let decode: ((ValueDecoder) throws -> Value)? = supportsLengthDecoding ? { reader in
            try self.decodeLength(width:width,from:reader)
        } : nil
        return Self(schema: "length\(width)<\(schema)>", layout: layout, variant: variant,
                    encoder: encode, decoder: decode, lengthEncoder: lengthEncoder, lengthDecoder: lengthDecoder)
    }
    public func withLayout(_ layout: Rust64ValueLayout) -> Self {
        Self(schema: schema, layout: layout, variant: variant, encoder: encoder, decoder: decoder,
             lengthEncoder: lengthEncoder, lengthDecoder: lengthDecoder)
    }
    func elementSize() throws -> UInt64 {
        guard let layout else { throw ModelError("explicit Rust64 layout required for collection element") }
        return layout.size
    }
}
public extension ValueDecoder {
    /// Direction admission precedes the guard; an already sticky reader error wins.
    func value<T>(_ schema: TraceValueCodec<T>) throws -> T {
        try checkFailure(); try schema.requireDecoding()
        return try value { try schema.decode(from: $0) }
    }
}

/// Convenience protocol; descriptors also support tuples and foreign application types.
public protocol TraceEncodable {
    static var traceSchema: String { get }
    var traceVariant: String? { get }
    func traceEncode(into: EncodeBuffer) throws
}
public extension TraceEncodable {
    static var traceSchema: String { String(reflecting: Self.self) }
    var traceVariant: String? { nil }
    func traceBytes(maximum: Int) throws -> [UInt8] {
        var bytes: [UInt8] = []
        try EncodeBuffer.withBuffer(&bytes, maximum: maximum) { try traceEncode(into: $0) }
        return bytes
    }
}
public protocol TraceDecodable { static func traceDecode(from: ValueDecoder) throws -> Self }
public extension TraceDecodable {
    static func fromTrace(_ bytes: [UInt8], limits: DecodeLimits = DecodeLimits()) throws -> Self {
        let reader = try ValueDecoder(bytes, limits: limits)
        let value = try reader.value { try Self.traceDecode(from: $0) }; try reader.finish(); return value
    }
}

public extension TraceValueCodec where Value: TraceEncodable {
    static func encoding(layout: Rust64ValueLayout? = nil) -> Self {
        Self(schema: Value.traceSchema, layout: layout, variant: { $0.traceVariant }, encodeOnly: { try $0.traceEncode(into: $1) })
    }
}
public extension TraceValueCodec where Value: TraceDecodable {
    static func decoding(layout: Rust64ValueLayout? = nil) -> Self {
        Self(schema: String(reflecting: Value.self), layout: layout, decodeOnly: { try Value.traceDecode(from: $0) })
    }
}

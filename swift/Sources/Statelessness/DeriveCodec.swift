import Foundation

public enum TraceLengthWidth: UInt8 { case u8 = 1, u16 = 2, u32 = 4 }
public enum TraceCodecRequirement { case none, encoding, decoding, both }
private func requireDirections(_ requirement: TraceCodecRequirement, _ encoding: Bool, _ decoding: Bool) throws {
    if (requirement == .encoding || requirement == .both) && !encoding { throw ModelError("value codec does not support encoding") }
    if (requirement == .decoding || requirement == .both) && !decoding { throw ModelError("value codec does not support decoding") }
}
private func validateLayout(_ layout: Rust64ValueLayout?) throws {
    if let layout {
        guard !layout.identity.isEmpty else { throw ModelError("layout identity required") }
        if let alignment = layout.alignment {
            guard alignment > 0, alignment & (alignment - 1) == 0, layout.size % alignment == 0 else { throw ModelError("invalid logical layout") }
        }
    }
}
public struct TraceProduct<Prefix, Last> {
    public let prefix: Prefix
    public let last: Last
    public init(prefix: Prefix, last: Last) { self.prefix = prefix; self.last = last }
}
public struct TraceField<Root, Field> {
    public let label: String
    fileprivate let encoding: ((Root, EncodeBuffer) throws -> Void)?
    fileprivate let decoding: ((ValueDecoder) throws -> Field)?
    private init(_ label: String, codec: TraceValueCodec<Field>, width: TraceLengthWidth?, guarded: Bool, get: ((Root) throws -> Field)?) {
        self.label = label
        let encodable = width == nil ? codec.supportsEncoding : codec.supportsLengthEncoding
        let decodable = width == nil ? codec.supportsDecoding : codec.supportsLengthDecoding
        if let get, encodable {
            encoding = { root, sink in
                let value = try get(root)
                if let width { try codec.encodeLengthRaw(value, width: width.rawValue, into: sink) }
                else { try codec.encodeRaw(value, into: sink) }
            }
        } else { encoding = nil }
        decoding = decodable ? { reader in
            if let width { return try codec.decodeLength(width: width.rawValue, from: reader) }
            return try guarded ? reader.value(codec) : codec.decode(from: reader)
        } : nil
    }
    public static func ordinary(_ label: String, _ codec: TraceValueCodec<Field>, get: ((Root) throws -> Field)? = nil) -> Self { Self(label, codec: codec, width: nil, guarded: true, get: get) }
    public static func length(_ label: String, _ codec: TraceValueCodec<Field>, width: TraceLengthWidth, get: ((Root) throws -> Field)? = nil) -> Self { Self(label, codec: codec, width: width, guarded: false, get: get) }
    public static func adapter(_ label: String, _ codec: TraceValueCodec<Field>, get: ((Root) throws -> Field)? = nil) -> Self { Self(label, codec: codec, width: nil, guarded: false, get: get) }
}
public struct TraceFields<Root, Decoded> {
    fileprivate let encoding: ((Root, EncodeBuffer) throws -> Void)?
    fileprivate let decoding: ((ValueDecoder) throws -> Decoded)?
    public static func first(_ field: TraceField<Root, Decoded>) -> Self { Self(encoding: field.encoding, decoding: field.decoding) }
    public func appending<Next>(_ field: TraceField<Root, Next>) -> TraceFields<Root, TraceProduct<Decoded, Next>> {
        let encode: ((Root, EncodeBuffer) throws -> Void)?
        if let a = encoding, let b = field.encoding { encode = { root, sink in try a(root, sink); try b(root, sink) } } else { encode = nil }
        let decode: ((ValueDecoder) throws -> TraceProduct<Decoded, Next>)?
        if let a = decoding, let b = field.decoding { decode = { reader in TraceProduct(prefix: try a(reader), last: try b(reader)) } } else { decode = nil }
        return TraceFields<Root, TraceProduct<Decoded, Next>>(encoding: encode, decoding: decode)
    }
}
public enum TraceRecords {
    public static func codec<Root, Decoded>(schema: String, fields: TraceFields<Root, Decoded>, construct: ((Decoded) throws -> Root)? = nil, require: TraceCodecRequirement = .none, layout: Rust64ValueLayout? = nil) throws -> TraceValueCodec<Root> {
        try validateLayout(layout)
        let decodable = fields.decoding != nil && construct != nil
        try requireDirections(require, fields.encoding != nil, decodable)
        let decode: ((ValueDecoder) throws -> Root)?
        if let fieldsDecode = fields.decoding, let construct { decode = { try construct(fieldsDecode($0)) } } else { decode = nil }
        return TraceValueCodec(schema: schema, layout: layout, encoder: fields.encoding, decoder: decode)
    }
    public static func unit<Root>(schema: String, construct: (() throws -> Root)? = nil, require: TraceCodecRequirement = .none, layout: Rust64ValueLayout? = nil) throws -> TraceValueCodec<Root> {
        try validateLayout(layout); try requireDirections(require, true, construct != nil)
        let decode: ((ValueDecoder) throws -> Root)? = construct.map { construct in { _ in try construct() } }
        return TraceValueCodec(schema: schema, layout: layout, encoder: { _,_ in }, decoder: decode)
    }
}
private final class TraceCaseIdentity {}
public struct TraceEnumSelection<Enum> {
    fileprivate let identity: TraceCaseIdentity
    fileprivate let encode: (EncodeBuffer) throws -> Void
}
public struct AnyTraceEnumCase<Enum> {
    fileprivate let identity: TraceCaseIdentity
    public let tag: UInt32
    public let label: String
    fileprivate let encodable: Bool
    fileprivate let decode: ((ValueDecoder) throws -> Enum)?
}
public struct TraceEnumCase<Enum, Payload> {
    private let identity = TraceCaseIdentity()
    public let tag: UInt32
    public let label: String
    private let payload: TraceValueCodec<Payload>
    private let construct: ((Payload) throws -> Enum)?
    public init(tag: UInt32, label: String, payload: TraceValueCodec<Payload>, construct: ((Payload) throws -> Enum)? = nil) { self.tag = tag; self.label = label; self.payload = payload; self.construct = construct }
    public func erased() -> AnyTraceEnumCase<Enum> {
        let decode: ((ValueDecoder) throws -> Enum)?
        if payload.supportsDecoding, let construct { decode = { try construct(payload.decode(from: $0)) } } else { decode = nil }
        return AnyTraceEnumCase(identity: identity, tag: tag, label: label, encodable: payload.supportsEncoding, decode: decode)
    }
    public func select(_ value: Payload) -> TraceEnumSelection<Enum> { TraceEnumSelection(identity: identity, encode: { try payload.encodeRaw(value, into: $0) }) }
}
public enum TraceEnums {
    public static func codec<Enum>(schema: String, tagWidth: TraceLengthWidth, cases: [AnyTraceEnumCase<Enum>], select: ((Enum) -> TraceEnumSelection<Enum>)? = nil, require: TraceCodecRequirement = .none, layout: Rust64ValueLayout? = nil) throws -> TraceValueCodec<Enum> {
        try validateLayout(layout)
        let maximum: UInt32 = tagWidth == .u8 ? 255 : tagWidth == .u16 ? 65535 : .max
        var tags = Set<UInt32>()
        for item in cases { guard item.tag <= maximum, tags.insert(item.tag).inserted else { throw ModelError("duplicate or out-of-range enum tag") } }
        let encoding = select != nil && cases.allSatisfy { $0.encodable }
        let decoding = cases.allSatisfy { $0.decode != nil }
        try requireDirections(require, encoding, decoding)
        let encode: ((Enum, EncodeBuffer) throws -> Void)?
        if encoding, let select { encode = { value, sink in
            let selection = select(value)
            guard let item = cases.first(where: { $0.identity === selection.identity }) else { throw ModelError("unregistered enum selection") }
            switch tagWidth { case .u8: try ValueCodecs.u8.encodeRaw(UInt8(item.tag), into: sink); case .u16: try ValueCodecs.u16.encodeRaw(UInt16(item.tag), into: sink); case .u32: try ValueCodecs.u32.encodeRaw(item.tag, into: sink) }
            try selection.encode(sink)
        } } else { encode = nil }
        let decode: ((ValueDecoder) throws -> Enum)?
        if decoding { decode = { reader in
            let tag: UInt32
            switch tagWidth { case .u8: tag = UInt32(try reader.value(ValueCodecs.u8)); case .u16: tag = UInt32(try reader.value(ValueCodecs.u16)); case .u32: tag = try reader.value(ValueCodecs.u32) }
            guard let item = cases.first(where: { $0.tag == tag }), let decode = item.decode else { throw ModelError("unknown enum tag") }
            return try decode(reader)
        } } else { decode = nil }
        return TraceValueCodec(schema: schema, layout: layout, variant: { value in
            guard let selection = select?(value) else { return nil }
            return cases.first { $0.identity === selection.identity }?.label
        }, encoder: encode, decoder: decode)
    }
}

public struct ModelCodecBinding<State, Input, Output> {
    private let bound: Codec<State, Input, Output>
    public init(metadata: @escaping () throws -> Metadata, version: UInt32 = 1, state: TraceValueCodec<State>, input: TraceValueCodec<Input>, output: TraceValueCodec<Output>, decodeLimits: @escaping () throws -> DecodeLimits = { DecodeLimits() }) throws {
        guard version > 0 else { throw ModelError("codec version must be positive") }
        try state.requireEncoding(); try state.requireDecoding(); try input.requireEncoding(); try input.requireDecoding(); try output.requireEncoding()
        let gate = { guard try metadata().codecVersion == version else { throw ModelError("ModelCodec derive version does not match Model metadata") } }
        bound = Codec(metadata: metadata,
            encodeState: { try gate(); return try state.traceBytes($0, maximum: Int.max) },
            decodeState: { try gate(); return try state.fromTrace($0, limits: decodeLimits()) },
            encodeInput: { try gate(); return try input.traceBytes($0, maximum: Int.max) },
            decodeInput: { try gate(); return try input.fromTrace($0, limits: decodeLimits()) },
            encodeOutput: { try gate(); return try output.traceBytes($0, maximum: Int.max) },
            encodeStateInto: { try gate(); try state.encodeRaw($0, into: $1) },
            encodeInputInto: { try gate(); try input.encodeRaw($0, into: $1) },
            encodeOutputInto: { try gate(); try output.encodeRaw($0, into: $1) })
    }
    public func codec() -> Codec<State, Input, Output> { bound }
}

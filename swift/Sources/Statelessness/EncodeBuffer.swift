private final class EncodeStorage {
    var bytes: [UInt8] = []
    let maximum: Int
    var failed = false
    init(_ maximum: Int) { self.maximum = maximum }
}
/// Synchronous revocable byte sink. Logical length is bounded before growth;
/// allocation failures/traps and allocations inside legacy codecs are not caught.
public final class EncodeBuffer {
    private var owner: EncodeStorage?
    private init(_ owner: EncodeStorage) { self.owner = owner }
    private func storage() throws -> EncodeStorage {
        guard let owner else { throw ModelError("encode buffer lease expired") }
        return owner
    }
    public var count: Int { get throws { try storage().bytes.count } }
    public func finish() throws {
        if try storage().failed { throw ModelError("encoded blob exceeds byte or allocation limit") }
    }
    /// Internal no-growth admission for codecs that acquire owned payloads later.
    func requireAdditional(_ count: UInt64) throws {
        let storage = try storage()
        guard !storage.failed, storage.bytes.count <= storage.maximum,
              count <= UInt64(storage.maximum - storage.bytes.count) else {
            storage.failed = true; throw ModelError("encoded blob exceeds byte limit")
        }
    }
    public func extend(_ bytes: [UInt8]) throws {
        let storage = try storage()
        let (length, overflow) = storage.bytes.count.addingReportingOverflow(bytes.count)
        guard !storage.failed, !overflow, length <= storage.maximum else {
            storage.failed = true; throw ModelError("encoded blob exceeds byte limit")
        }
        storage.bytes.append(contentsOf: bytes)
    }
    /// The allocating legacy result was produced outside this sink's bound.
    public func extendFromVec(_ bytes: [UInt8]) throws { try extend(bytes) }
    public func framed(_ encode: (EncodeBuffer) throws -> Void) throws {
        let storage = try storage(), start = storage.bytes.count
        try extend([UInt8](repeating: 0, count: 8))
        do {
            try sublease(encode)
            try finish()
            let length = UInt64(storage.bytes.count - start - 8)
            for shift in 0..<8 { storage.bytes[start + shift] = UInt8(truncatingIfNeeded: length >> (shift * 8)) }
        } catch { storage.failed = true; throw error }
    }
    /// Direct trait-style callback lease: returned errors alone do not poison.
    /// Rejected writes and framed callbacks retain their existing sticky state.
    func rawSublease(_ encode: (EncodeBuffer) throws -> Void) throws {
        let child = EncodeBuffer(try storage())
        defer { child.owner = nil }
        try encode(child)
    }
    func sublease(_ encode: (EncodeBuffer) throws -> Void) throws {
        let storage = try storage()
        let child = EncodeBuffer(storage)
        defer { child.owner = nil }
        do { try encode(child); try child.finish() }
        catch { storage.failed = true; throw error }
    }
    /// Clears logical destination and restores reusable storage on every exit.
    /// Do not alias, reenter, or concurrently mutate the caller array.
    public static func withBuffer(_ bytes: inout [UInt8], maximum: Int, encode: (EncodeBuffer) throws -> Void) throws {
        bytes.removeAll(keepingCapacity: true)
        guard maximum >= 0 else { throw ConfigError("negative encode maximum") }
        let storage = EncodeStorage(maximum)
        swap(&bytes, &storage.bytes)
        let root = EncodeBuffer(storage)
        defer { root.owner = nil; swap(&bytes, &storage.bytes) }
        try root.sublease(encode)
        try root.finish()
    }
}
func encodeValue<T>(_ stage: String, _ legacy: (T) throws -> [UInt8], _ incremental: ((T, EncodeBuffer) throws -> Void)?, _ value: T, maximum: Int = Int.max, bytes: inout [UInt8]) throws {
    try call(stage) {
        try EncodeBuffer.withBuffer(&bytes, maximum: maximum) { sink in
            if let incremental { try incremental(value, sink) }
            else { try sink.extendFromVec(legacy(value)) }
        }
    }
}
func encodedValue<T>(_ stage: String, _ legacy: (T) throws -> [UInt8], _ incremental: ((T, EncodeBuffer) throws -> Void)?, _ value: T, maximum: Int = Int.max) throws -> [UInt8] {
    var bytes: [UInt8] = []
    try encodeValue(stage, legacy, incremental, value, maximum: maximum, bytes: &bytes)
    return bytes
}
public func encodeStateInto<S,I,O>(_ codec: Codec<S,I,O>, state: S, into sink: EncodeBuffer) throws {
    try sink.sublease { child in
        if let callback = codec.encodeStateInto { try callback(state, child) }
        else { try child.extendFromVec(codec.encodeState(state)) }
    }
}
public func encodeInputInto<S,I,O>(_ codec: Codec<S,I,O>, input: I, into sink: EncodeBuffer) throws {
    try sink.sublease { child in
        if let callback = codec.encodeInputInto { try callback(input, child) }
        else { try child.extendFromVec(codec.encodeInput(input)) }
    }
}
public func encodeOutputInto<S,I,O>(_ codec: Codec<S,I,O>, output: O, into sink: EncodeBuffer) throws {
    try sink.sublease { child in
        if let callback = codec.encodeOutputInto { try callback(output, child) }
        else { try child.extendFromVec(codec.encodeOutput(output)) }
    }
}

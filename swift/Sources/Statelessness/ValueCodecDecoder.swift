/// Independent canonical-value decoding budgets. Allocation weights describe
/// explicit logical Rust64 requests, not Swift heap storage or process RSS.
public struct DecodeLimits {
    public var bytes: UInt64
    public var allocation: UInt64
    public var elements: UInt64
    public var depth: UInt64
    public var work: UInt64
    public init(bytes: UInt64 = 16_777_216, allocation: UInt64 = 33_554_432, elements: UInt64 = 1_000_000, depth: UInt64 = 64, work: UInt64 = 2_000_000) {
        self.bytes = bytes; self.allocation = allocation; self.elements = elements; self.depth = depth; self.work = work
    }
}

/// An owned, reusable cooperative reader. Guarded nested value calls are valid;
/// callers must confine access synchronously. Raw validation performed outside
/// a value guard is not automatically sticky. Catchable callback errors restore
/// depth and preserve the first structural failure; fatal Swift traps do not.
public final class ValueDecoder {
    private let bytes: [UInt8]
    private var offset = 0
    private var limits: DecodeLimits
    private var depth: UInt64 = 0
    private var failure: Error?

    /// Admission precedes ownership of the array's copy-on-write storage. Later
    /// caller mutations and returned slice mutations cannot rewrite this reader.
    public init(_ bytes: [UInt8], limits: DecodeLimits = DecodeLimits()) throws {
        guard UInt64(bytes.count) <= limits.bytes else { throw ModelError("decode byte limit") }
        self.bytes = bytes; self.limits = limits
    }
    /// Budget-free admission for a typed schema's native direction boundary.
    func checkFailure() throws { if let failure { throw failure } }
    private func reject<T>(_ error: Error) throws -> T {
        if failure == nil { failure = error }
        throw failure!
    }
    /// The value slice owns its storage and exposes no mutable reader backing.
    /// Raw reads consume neither byte work nor guarded nesting budget.
    public func take(_ count: UInt64) throws -> ArraySlice<UInt8> {
        if let failure { throw failure }
        guard count <= UInt64(bytes.count - offset) else { return try reject(ModelError("truncated value")) }
        let end = offset + Int(count), result = bytes[offset..<end]
        offset = end
        return result
    }
    public func value<T>(_ body: (ValueDecoder) throws -> T) throws -> T {
        if let failure { throw failure }
        guard limits.work > 0 else { return try reject(ModelError("decode work limit")) }
        limits.work -= 1
        guard depth < limits.depth else { return try reject(ModelError("decode nesting limit")) }
        depth += 1
        defer { depth -= 1 }
        do {
            let result = try body(self)
            if let failure { throw failure }
            return result
        } catch { return try reject(error) }
    }
    /// Charges collection requests before callers reserve storage. The work
    /// check admits future guarded elements without charging them twice.
    public func collection(_ count: UInt64, elementSize: UInt64) throws {
        if let failure { throw failure }
        guard count <= limits.elements else { return try reject(ModelError("decode element limit")) }
        limits.elements -= count
        let (allocation, overflow) = count.multipliedReportingOverflow(by: elementSize)
        guard !overflow else { return try reject(ModelError("decode allocation overflow")) }
        guard allocation <= limits.allocation else { return try reject(ModelError("decode allocation limit")) }
        limits.allocation -= allocation
        guard count <= limits.work else { return try reject(ModelError("decode work limit")) }
    }
    /// Called by string decoding immediately after collection(count, 1).
    func consumeStringWork(_ count: UInt64) throws {
        if let failure { throw failure }
        guard count <= limits.work else { return try reject(ModelError("decode work limit")) }
        limits.work -= count
    }
    /// Trailing input is a reusable-reader error, not a recorded failure. A
    /// caller can consume the remainder and call finish again successfully.
    public func finish() throws {
        if let failure { throw failure }
        guard offset == bytes.count else { throw ModelError("trailing value bytes") }
    }
}

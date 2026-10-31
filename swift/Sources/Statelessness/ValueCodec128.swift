/// Portable exact limbs; requires no newer OS runtime or BigInt dependency.
public struct TraceUInt128: Equatable, CustomStringConvertible {
    public let high: UInt64
    public let low: UInt64
    public init(high: UInt64, low: UInt64) { self.high = high; self.low = low }
    public init(decimal: String) throws {
        guard !decimal.isEmpty else { throw ModelError("invalid unsigned 128-bit integer") }
        var high: UInt64 = 0, low: UInt64 = 0
        for byte in decimal.utf8 {
            guard byte >= 48 && byte <= 57 else { throw ModelError("invalid unsigned 128-bit integer") }
            let product = low.multipliedFullWidth(by: 10)
            let (h, overflow1) = high.multipliedReportingOverflow(by: 10)
            let (h2, overflow2) = h.addingReportingOverflow(product.high)
            let (l, carry) = product.low.addingReportingOverflow(UInt64(byte - 48))
            let (h3, overflow3) = h2.addingReportingOverflow(carry ? 1 : 0)
            guard !overflow1 && !overflow2 && !overflow3 else { throw ModelError("integer out of range") }
            high = h3; low = l
        }
        self.init(high: high, low: low)
    }
    public var description: String {
        if high == 0 { return String(low) }
        var h = high, l = low, digits: [UInt8] = []
        while h != 0 || l != 0 {
            let qh = h / 10, remainder = h % 10
            let ql = UInt64(10).dividingFullWidth((high: remainder, low: l))
            digits.append(UInt8(ql.remainder) + 48); h = qh; l = ql.quotient
        }
        return String(decoding: digits.reversed(), as: UTF8.self)
    }
}
public struct TraceInt128: Equatable, CustomStringConvertible {
    public let high: Int64
    public let low: UInt64
    public init(high: Int64, low: UInt64) { self.high = high; self.low = low }
    public init(decimal: String) throws {
        let negative = decimal.first == "-"
        let magnitude = try TraceUInt128(decimal: negative ? String(decimal.dropFirst()) : decimal)
        guard magnitude.high < (UInt64(1) << 63) || (negative && magnitude.high == UInt64(1) << 63 && magnitude.low == 0) else { throw ModelError("integer out of range") }
        if negative {
            let (low, carry) = (~magnitude.low).addingReportingOverflow(1)
            self.init(high: Int64(bitPattern: ~magnitude.high &+ (carry ? 1 : 0)), low: low)
        } else { self.init(high: Int64(bitPattern: magnitude.high), low: magnitude.low) }
    }
    public var description: String {
        if high >= 0 { return TraceUInt128(high: UInt64(high), low: low).description }
        let (l, carry) = (~low).addingReportingOverflow(1)
        return "-" + TraceUInt128(high: ~UInt64(bitPattern: high) &+ (carry ? 1 : 0), low: l).description
    }
}

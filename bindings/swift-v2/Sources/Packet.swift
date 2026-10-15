import Foundation
import Statelessness

// Structural transport only. Rust owns binary traces and replay. This packet
// carries the portable observation envelope; exporting drops Rust run metadata,
// and importing creates a manual run configuration, as documented by the ABI.
private let packetMaximum = 64 * 1024 * 1024
private let packetMagic = Array("STV2P001".utf8)

private struct PacketWriter {
    var data = packetMagic
    mutating func raw(_ bytes: [UInt8]) throws {
        guard bytes.count <= packetMaximum - data.count else { throw FormatError("native packet byte limit") }
        data.append(contentsOf: bytes)
    }
    mutating func number(_ value: UInt32) throws {
        try raw((0..<4).map { UInt8(truncatingIfNeeded: value >> ($0 * 8)) })
    }
    mutating func count(_ value: Int) throws {
        guard let number = UInt32(exactly: value) else { throw FormatError("native packet count overflow") }
        try self.number(number)
    }
    mutating func blob(_ value: [UInt8]) throws { try count(value.count); try raw(value) }
    mutating func text(_ value: String) throws { try blob(Array(value.utf8)) }
    mutating func checks(_ values: [Check]) throws {
        try count(values.count)
        for check in values {
            try text(check.id); try raw([check.status == .passed ? 0 : check.status == .failed ? 1 : 2]); try text(check.details)
        }
    }
}

func encodePacket(_ trace: Trace, limits: TraceLimits = TraceLimits()) throws -> [UInt8] {
    // Reuse strict portable schema validation; this does not execute a model.
    let trace = try traceFromJSON(traceToJSON(trace), limits: limits)
    var writer = PacketWriter()
    try writer.text(trace.metadata.name); try writer.text(trace.metadata.build)
    try writer.number(trace.metadata.modelVersion); try writer.number(trace.metadata.propertiesVersion); try writer.number(trace.metadata.codecVersion)
    try writer.text("manual"); try writer.raw([0]); try writer.number(0)
    try writer.blob(trace.initialState); try writer.checks(trace.initialChecks); try writer.count(trace.steps.count)
    for step in trace.steps {
        try writer.blob(step.input)
        switch step.disposition {
        case .accepted: try writer.raw([0]); try writer.text("")
        case .rejected(let reason): try writer.raw([1]); try writer.text(reason)
        case .ignored(let reason): try writer.raw([2]); try writer.text(reason)
        }
        try writer.count(step.outputs.count)
        for output in step.outputs { try writer.blob(output) }
        try writer.blob(step.postState); try writer.checks(step.checks)
    }
    let tag: UInt8
    switch trace.termination { case .completed: tag = 0; case .propertyFailed: tag = 1; case .stepLimit: tag = 2; case .interrupted: tag = 3; case .modelError: tag = 4 }
    try writer.raw([tag]); try writer.text(trace.error)
    return writer.data
}

private struct PacketReader {
    let data: [UInt8]
    let limits: TraceLimits
    var offset = 0
    var items = 0
    var payload = 0
    mutating func raw(_ count: Int) throws -> [UInt8] {
        guard count >= 0, count <= data.count - offset else { throw FormatError("truncated native packet") }
        defer { offset += count }; return Array(data[offset..<(offset + count)])
    }
    mutating func byte() throws -> UInt8 { try raw(1)[0] }
    mutating func number() throws -> UInt32 {
        let bytes = try raw(4)
        return bytes.enumerated().reduce(UInt32(0)) { $0 | UInt32($1.element) << ($1.offset * 8) }
    }
    mutating func count(maximum: Int? = nil, aggregate: Bool = true) throws -> Int {
        let value = Int(try number())
        guard value <= (maximum ?? limits.maxItems) else { throw FormatError("native packet item limit") }
        if aggregate {
            guard value <= limits.maxItems - items else { throw FormatError("native packet aggregate item limit") }
            items += value
        }
        return value
    }
    mutating func blob(payload retained: Bool = true) throws -> [UInt8] {
        let count = Int(try number())
        if retained {
            guard count <= limits.maxBlobBytes, count <= limits.maxPayloadBytes - payload else { throw FormatError("native packet payload limit") }
            payload += count
        }
        return try raw(count)
    }
    mutating func text() throws -> String {
        let bytes = try blob(payload: false)
        guard String(bytes: bytes, encoding: .utf8) != nil else { throw FormatError("invalid UTF-8 native packet") }
        return String(decoding: bytes, as: UTF8.self)
    }
    mutating func checks() throws -> [Check] {
        let count = try count()
        var result: [Check] = []
        for _ in 0..<count {
            let id = try text(), status = try byte(), details = try text()
            guard status <= 2 else { throw FormatError("invalid native packet check status") }
            result.append(Check(id, status == 0 ? .passed : status == 1 ? .failed : .skipped, details))
        }
        return result
    }
}

func decodePacket(_ data: [UInt8], limits: TraceLimits = TraceLimits()) throws -> Trace {
    guard data.count <= packetMaximum else { throw FormatError("native packet byte limit") }
    for limit in [limits.maxSteps, limits.maxBlobBytes, limits.maxItems, limits.maxPayloadBytes, limits.maxJSONBytes] {
        guard limit >= 0, limit <= Int.max / 2 else { throw ConfigError("invalid trace limit") }
    }
    var reader = PacketReader(data: data, limits: limits)
    guard try reader.raw(8) == packetMagic else { throw FormatError("unsupported native packet") }
    let name = try reader.text(), build = try reader.text()
    let modelVersion = try reader.number(), propertiesVersion = try reader.number(), codecVersion = try reader.number()
    let metadata = Metadata(name: name, modelVersion: modelVersion, propertiesVersion: propertiesVersion, codecVersion: codecVersion, build: build)
    _ = try reader.text() // Rust strategy deliberately omitted from portable JSON.
    let seed = try reader.byte()
    guard seed <= 1 else { throw FormatError("invalid native packet seed tag") }
    if seed == 1 { _ = try reader.raw(8) }
    // Discarded Rust provenance is transport data, not portable retained items.
    // Each label needs at least two u32 string lengths in the remaining packet.
    let labels = try reader.count(maximum: (data.count - reader.offset) / 8, aggregate: false)
    for _ in 0..<labels { _ = try reader.text(); _ = try reader.text() }
    let initialState = try reader.blob(), initialChecks = try reader.checks()
    let count = try reader.count(maximum: limits.maxSteps)
    var steps: [TraceStep] = []
    for _ in 0..<count {
        let input = try reader.blob(), tag = try reader.byte(), reason = try reader.text()
        let disposition: Disposition
        switch tag {
        case 0 where reason.isEmpty: disposition = .accepted
        case 1: disposition = .rejected(reason)
        case 2: disposition = .ignored(reason)
        default: throw FormatError("invalid native packet disposition")
        }
        let outputCount = try reader.count()
        var outputs: [[UInt8]] = []
        for _ in 0..<outputCount { outputs.append(try reader.blob()) }
        let postState = try reader.blob(), checks = try reader.checks()
        steps.append(TraceStep(input: input, disposition: disposition, outputs: outputs, postState: postState, checks: checks))
    }
    let termination: Termination
    switch try reader.byte() {
    case 0: termination = .completed
    case 1: termination = .propertyFailed
    case 2: termination = .stepLimit
    case 3: termination = .interrupted
    case 4: termination = .modelError
    default: throw FormatError("invalid native packet termination")
    }
    let error = try reader.text()
    guard reader.offset == data.count else { throw FormatError("trailing native packet bytes") }
    return try traceFromJSON(traceToJSON(Trace(metadata: metadata, initialState: initialState, initialChecks: initialChecks, steps: steps, termination: termination, error: error)), limits: limits)
}

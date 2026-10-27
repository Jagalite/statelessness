import Foundation

private struct EvidenceEncoder {
    let sink: EncodeBuffer
    let maximum: Int
    func integer(_ value: UInt64, width: Int) throws { try sink.extend(evidenceInteger(value, width: width)) }
    func length(_ count: Int) throws { guard let value = UInt32(exactly: count) else { throw ModelError("length overflow") }; try integer(UInt64(value), width: 4) }
    func string(_ value: String) throws {
        let bytes = value.utf8.count
        guard UInt32(exactly: bytes) != nil else { throw ModelError("length overflow") }
        let count = try sink.count, (sum,overflow) = bytes.addingReportingOverflow(4)
        guard !overflow, count <= maximum, sum <= maximum - count else { throw ModelError("encoded blob exceeds byte limit") }
        try length(bytes); try sink.extend(Array(value.utf8))
    }
    func optionalString(_ value: String?) throws { try integer(value == nil ? 0 : 1, width: 1); if let value { try string(value) } }
    func optionalInteger(_ value: UInt64?) throws { try integer(value == nil ? 0 : 1, width: 1); if let value { try integer(value, width: 8) } }
    func provenance(_ value: Provenance) throws {
        try string(value.run); try integer(value.epoch, width: 8); try optionalString(value.manifest); try optionalString(value.source)
        try length(value.environment.count)
        for pair in value.environment { try string(pair.key); try string(pair.value) }
    }
    func context(_ value: CausalContext) throws {
        try optionalString(value.machine); try optionalInteger(value.generation); try optionalString(value.correlation); try optionalInteger(value.logicalTime)
        try length(value.causes.count)
        for origin in value.causes { try integer(origin.sequence, width: 8); try integer(UInt64(origin.output), width: 4) }
    }
    func step(_ value: StepEvidence) throws { try integer(value.sequence, width: 8); try integer(UInt64(value.stateCheckCount), width: 4); try context(value.causal) }
}
private func evidenceEncode(maximum: UInt64, _ encode: (EvidenceEncoder) throws -> Void) throws -> [UInt8] {
    let cap = Int(min(maximum, UInt64(Int.max))); var bytes: [UInt8] = []
    try EncodeBuffer.withBuffer(&bytes, maximum: cap) { sink in try encode(EvidenceEncoder(sink: sink, maximum: cap)) }
    return bytes
}
func evidenceProvenanceBytes(_ value: Provenance, maximum: UInt64) throws -> [UInt8] { try evidenceEncode(maximum: maximum) { try $0.provenance(value) } }
func evidenceStepBytes(_ value: StepEvidence, maximum: UInt64) throws -> [UInt8] { try evidenceEncode(maximum: maximum) { try $0.step(value) } }
func evidenceMetadataBytes(_ value: TraceV2, maximum: UInt64) throws -> [UInt8] {
    try evidenceEncode(maximum: maximum) { e in
        try e.integer(value.checkpointAfter, width: 8); try e.provenance(value.provenance); try e.length(value.steps.count)
        for step in value.steps { try e.step(step) }
    }
}
/// Decoder budgets account logical requests, independently of the embedded
/// trace's item quota. Logical vector requests use the shared Rust64 schema
/// weights: environment pair48, OutputOrigin16, StepEvidence120; strings charge
/// UTF-8 bytes. These are logical units, not Swift heap sizes or process RSS.
struct EvidenceDecodeLimits {
    var bytes: UInt64
    var allocation: UInt64
    var elements: UInt64
    var depth: Int
    var work: UInt64
    init(metadataBytes: UInt64) {
        bytes = metadataBytes; elements = metadataBytes; depth = 16
        let (scaled,overflow) = metadataBytes.multipliedReportingOverflow(by: 16)
        allocation = overflow ? UInt64.max : scaled; work = allocation
    }
}
final class EvidenceDecoder {
    private let bytes: [UInt8]
    private var offset = 0
    private var limits: EvidenceDecodeLimits
    private var depth = 0
    private var failure: Error?
    init(_ bytes: [UInt8], limits: EvidenceDecodeLimits) throws {
        guard UInt64(bytes.count) <= limits.bytes else { throw ModelError("decode byte limit") }
        self.bytes = bytes; self.limits = limits
    }
    private func reject(_ error: Error) throws -> Never { if failure == nil { failure = error }; throw failure! }
    func value<T>(_ decode: (EvidenceDecoder) throws -> T) throws -> T {
        if let failure { throw failure }
        guard limits.work > 0 else { try reject(ModelError("decode work limit")) }
        limits.work -= 1
        guard depth < limits.depth else { try reject(ModelError("decode nesting limit")) }
        depth += 1; defer { depth -= 1 }
        do { let result = try decode(self); if let failure { throw failure }; return result }
        catch { try reject(error) }
    }
    private func take(_ count: Int) throws -> ArraySlice<UInt8> {
        if let failure { throw failure }
        guard count >= 0, count <= bytes.count - offset else { try reject(ModelError("truncated value")) }
        let part = bytes[offset..<offset+count]; offset += count; return part
    }
    func integer(_ width: Int) throws -> UInt64 {
        try value { d in
            let part = try d.take(width)
            return part.enumerated().reduce(UInt64(0)) { $0 | UInt64($1.element) << ($1.offset * 8) }
        }
    }
    private func collection(_ count: UInt64, size: UInt64) throws {
        guard count <= limits.elements else { try reject(ModelError("decode element limit")) }; limits.elements -= count
        let (allocation,overflow) = count.multipliedReportingOverflow(by: size)
        guard !overflow else { try reject(ModelError("decode allocation overflow")) }
        guard allocation <= limits.allocation else { try reject(ModelError("decode allocation limit")) }; limits.allocation -= allocation
        guard count <= limits.work else { try reject(ModelError("decode work limit")) }
    }
    func string() throws -> String {
        try value { d in
            let length = try d.integer(4); try d.collection(length, size: 1); d.limits.work -= length
            guard let count = Int(exactly: length) else { try d.reject(ModelError("length overflow")) }
            let bytes = try d.take(count)
            guard let text = String(bytes: bytes, encoding: .utf8) else { try d.reject(ModelError("invalid UTF-8")) }; return text
        }
    }
    func optional<T>(_ decode: (EvidenceDecoder) throws -> T) throws -> T? {
        try value { d in
            switch try d.integer(1) { case 0: return nil; case 1: return try decode(d); default: try d.reject(ModelError("invalid option tag")) }
        }
    }
    func vector<T>(allocationSize: UInt64, _ decode: (EvidenceDecoder) throws -> T) throws -> [T] {
        try value { d in
            let length = try d.integer(4)
            try d.collection(length, size: allocationSize)
            guard let count = Int(exactly: length) else { try d.reject(ModelError("length overflow")) }
            var values: [T] = []; values.reserveCapacity(count)
            for _ in 0..<count { values.append(try decode(d)) }; return values
        }
    }
    func provenance() throws -> Provenance {
        try value { d in
            let run = try d.string(), epoch = try d.integer(8), manifest = try d.optional { try $0.string() }, source = try d.optional { try $0.string() }
            let environment: [AuditParameter] = try d.vector(allocationSize: 48) { d in try d.value { d in AuditParameter(try d.string(), try d.string()) } }
            return Provenance(run: run, epoch: epoch, manifest: manifest, source: source, environment: environment)
        }
    }
    func context() throws -> CausalContext {
        try value { d in
            let machine = try d.optional { try $0.string() }, generation = try d.optional { try $0.integer(8) }, correlation = try d.optional { try $0.string() }, logical = try d.optional { try $0.integer(8) }
            let causes: [OutputOrigin] = try d.vector(allocationSize: 16) { d in try d.value { d in OutputOrigin(sequence: try d.integer(8), output: UInt32(try d.integer(4))) } }
            return CausalContext(machine: machine, generation: generation, correlation: correlation, logicalTime: logical, causes: causes)
        }
    }
    func step() throws -> StepEvidence { try value { d in StepEvidence(sequence: try d.integer(8), stateCheckCount: UInt32(try d.integer(4)), causal: try d.context()) } }
    func finish() throws { if let failure { throw failure }; guard offset == bytes.count else { throw ModelError("trailing value bytes") } }
}
func evidenceDecodeMetadata(_ bytes: [UInt8], trace: AuditTrace, limits: EvidenceDecodeLimits) throws -> TraceV2 {
    let decoder = try EvidenceDecoder(bytes, limits: limits)
    let checkpoint = try decoder.integer(8), provenance = try decoder.provenance()
    let steps = try decoder.vector(allocationSize: 120) { try $0.step() }; try decoder.finish()
    return TraceV2(trace: trace, checkpointAfter: checkpoint, provenance: provenance, steps: steps)
}
public func decodeArtifact(_ bytes: [UInt8], limits: EvidenceLimits = EvidenceLimits()) throws -> Artifact {
    guard UInt64(bytes.count) <= limits.trace.maxTotalBytes else { throw ModelError("evidence byte limit") }
    let magic: [UInt8] = [83,84,76,69,83,83,26,10]
    guard bytes.count >= 12, bytes.starts(with: magic) else { throw ModelError("truncated or invalid evidence header") }
    let version = UInt16(bytes[8]) | UInt16(bytes[9]) << 8
    if version == 1 { return .v1(try evidenceTrace { try decodeAuditTrace(bytes, limits: limits.trace) }) }
    guard version == 2 else { throw ModelError("unsupported evidence version") }
    guard bytes.count >= 32, bytes[10] == 0, bytes[11] == 0 else { throw ModelError("invalid or truncated v2 header") }
    func integer(_ start: Int, width: Int) -> UInt64 { (0..<width).reduce(0) { $0 | UInt64(bytes[start+$1]) << ($1*8) } }
    let traceLength = integer(12, width: 8), metadataLength = integer(20, width: 8)
    let (body,overflow) = traceLength.addingReportingOverflow(metadataLength), (total,over2) = body.addingReportingOverflow(32)
    guard !overflow, !over2, total == UInt64(bytes.count), metadataLength <= limits.maxMetadataBytes, let embedded = Int(exactly: traceLength) else { throw ModelError("invalid evidence lengths or metadata limit") }
    let end = bytes.count - 4
    guard UInt64(auditCRC32(Array(bytes.prefix(end)))) == integer(end, width: 4) else { throw ModelError("evidence checksum mismatch") }
    // Exact total accounting above proves both indices fit the retained array.
    let split = 28 + embedded
    let trace = try evidenceTrace { try decodeAuditTrace(Array(bytes[28..<split]), limits: limits.trace) }
    let result = try evidenceDecodeMetadata(Array(bytes[split..<end]), trace: trace, limits: EvidenceDecodeLimits(metadataBytes: limits.maxMetadataBytes))
    try result.validate(limits: limits); return .v2(result)
}

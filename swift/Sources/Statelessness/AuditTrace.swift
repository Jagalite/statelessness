/// Lossless Rust binary-v1 audit configuration. Parameter order and duplicates matter.
public struct AuditParameter: Equatable, Sendable {
    public let key: String
    public let value: String
    public init(_ key: String, _ value: String) { self.key = key; self.value = value }
    public static func == (a: Self, b: Self) -> Bool { exactText(a.key, b.key) && exactText(a.value, b.value) }
}
public struct RunConfig: Equatable, Sendable {
    public let strategy: String
    public let seed: UInt64?
    public let parameters: [AuditParameter]
    public init(strategy: String, seed: UInt64? = nil, parameters: [AuditParameter] = []) {
        self.strategy = strategy; self.seed = seed; self.parameters = parameters
    }
    public static func == (a: Self, b: Self) -> Bool { exactText(a.strategy, b.strategy) && a.seed == b.seed && a.parameters == b.parameters }
}
public enum AuditTermination: Equatable, Sendable {
    case completed, propertyFailed, stepLimit, interrupted
    case modelError(String)
    public static func == (a: Self, b: Self) -> Bool {
        switch (a, b) {
        case (.completed, .completed), (.propertyFailed, .propertyFailed), (.stepLimit, .stepLimit), (.interrupted, .interrupted): return true
        case let (.modelError(x), .modelError(y)): return exactText(x, y)
        default: return false
        }
    }
}
/// Binary v1 preserves flat checks; successor/transition phase boundaries are unknown_legacy.
public struct AuditTrace: Equatable, Sendable {
    public let metadata: Metadata
    public let config: RunConfig
    public let initialState: [UInt8]
    public let initialChecks: [Check]
    public let steps: [TraceStep]
    public let termination: AuditTermination
    public init(metadata: Metadata, config: RunConfig, initialState: [UInt8], initialChecks: [Check], steps: [TraceStep], termination: AuditTermination) {
        self.metadata = metadata; self.config = config; self.initialState = initialState
        self.initialChecks = initialChecks; self.steps = steps; self.termination = termination
    }
    /// Caller must explicitly supply the configuration missing from portable Trace.
    public init(portable trace: Trace, config: RunConfig) throws {
        guard trace.termination == .modelError || trace.error.isEmpty else { throw BinaryTraceError.invalidData("portable error outside model_error") }
        let end: AuditTermination
        switch trace.termination {
        case .completed: end = .completed
        case .propertyFailed: end = .propertyFailed
        case .stepLimit: end = .stepLimit
        case .interrupted: end = .interrupted
        case .modelError: end = .modelError(trace.error)
        }
        self.init(metadata: trace.metadata, config: config, initialState: trace.initialState, initialChecks: trace.initialChecks, steps: trace.steps, termination: end)
    }
    /// Configuration is returned explicitly, never silently discarded.
    public func portable() -> (trace: Trace, config: RunConfig) {
        let end: Termination, error: String
        switch termination {
        case .completed: end = .completed; error = ""
        case .propertyFailed: end = .propertyFailed; error = ""
        case .stepLimit: end = .stepLimit; error = ""
        case .interrupted: end = .interrupted; error = ""
        case let .modelError(reason): end = .modelError; error = reason
        }
        return (Trace(metadata: metadata, initialState: initialState, initialChecks: initialChecks, steps: steps, termination: end, error: error), config)
    }
}
public struct BinaryTraceLimits: Sendable {
    public var maxTotalBytes: UInt64 = 67_108_864
    public var maxFrameBytes: UInt64 = 8_388_608
    public var maxBlobBytes: UInt64 = 4_194_304
    public var maxStringBytes: UInt64 = 65_536
    public var maxSteps: UInt64 = 100_000
    public var maxChecksPerStep: UInt64 = 4096
    public var maxOutputsPerStep: UInt64 = 4096
    public var maxParameters: UInt64 = 1024
    public var maxItems: UInt64 = 250_000
    public init() {}
}
public enum BinaryTraceError: Error, Equatable {
    case truncated, checksumMismatch
    case unsupportedVersion(UInt16)
    case invalidData(String), limitExceeded(String)
    public var kind: String {
        switch self {
        case .truncated: return "truncated"
        case .checksumMismatch: return "checksum_mismatch"
        case .unsupportedVersion: return "unsupported_version"
        case .invalidData: return "invalid_data"
        case .limitExceeded: return "limit_exceeded"
        }
    }
    public var detail: String? {
        switch self {
        case let .unsupportedVersion(n): return String(n)
        case let .invalidData(s), let .limitExceeded(s): return s
        default: return nil
        }
    }
}
private let auditHeader: [UInt8] = [83, 84, 76, 69, 83, 83, 26, 10, 1, 0, 0, 0]
/// IEEE reflected CRC32; corruption detection only, not authentication.
public func auditCRC32(_ bytes: [UInt8]) -> UInt32 { auditCRC(bytes, 0..<bytes.count) }
private func auditCRC(_ bytes: [UInt8], _ range: Range<Int>) -> UInt32 {
    var value: UInt32 = 0xffffffff
    for index in range {
        value ^= UInt32(bytes[index])
        for _ in 0..<8 { value = (value >> 1) ^ ((value & 1) == 1 ? 0xedb88320 : 0) }
    }
    return value ^ 0xffffffff
}
func auditAdd(_ a: UInt64, _ b: UInt64, _ name: String) throws -> UInt64 {
    let (sum, overflow) = a.addingReportingOverflow(b)
    guard !overflow else { throw BinaryTraceError.limitExceeded(name) }; return sum
}
func auditBound(_ value: UInt64, _ maximum: UInt64, _ name: String) throws {
    if value > maximum { throw BinaryTraceError.limitExceeded(name) }
}
struct AuditSizer {
    let limits: BinaryTraceLimits
    var items: UInt64 = 0
    mutating func count(_ count: Int, _ maximum: UInt64, _ name: String) throws {
        try auditBound(UInt64(count), min(maximum, UInt64(UInt32.max)), name)
        items = try auditAdd(items, UInt64(count), "aggregate items")
        try auditBound(items, limits.maxItems, "aggregate items")
    }
    func blob(_ bytes: [UInt8]) throws -> UInt64 {
        try auditBound(UInt64(bytes.count), min(limits.maxBlobBytes, UInt64(UInt32.max)), "blob bytes")
        return UInt64(bytes.count) + 4
    }
    func string(_ text: String) throws -> UInt64 {
        let size = UInt64(text.utf8.count)
        try auditBound(size, min(limits.maxStringBytes, UInt64(UInt32.max)), "string bytes")
        return size + 4
    }
    mutating func checks(_ checks: [Check]) throws -> UInt64 {
        try count(checks.count, limits.maxChecksPerStep, "checks per step")
        var size: UInt64 = 4
        for check in checks {
            guard check.status != .passed || check.details.isEmpty else { throw BinaryTraceError.invalidData("passing checks have no details") }
            size = try auditAdd(size, try string(check.id), "frame bytes")
            size = try auditAdd(size, 1, "frame bytes")
            if check.status != .passed { size = try auditAdd(size, try string(check.details), "frame bytes") }
        }
        return size
    }
    func frame(_ payload: UInt64, total: inout UInt64) throws {
        try auditBound(payload, min(limits.maxFrameBytes, UInt64(UInt32.max)), "frame bytes")
        total = try auditAdd(total, try auditAdd(payload, 9, "total bytes"), "total bytes")
        try auditBound(total, limits.maxTotalBytes, "total bytes")
    }
}
func auditPreflight(_ trace: AuditTrace, _ limits: BinaryTraceLimits) throws {
    var size = AuditSizer(limits: limits), total: UInt64 = 12
    try auditBound(total, limits.maxTotalBytes, "total bytes")
    try auditBound(UInt64(trace.steps.count), limits.maxSteps, "steps")
    var run = try size.string(trace.metadata.name)
    run = try auditAdd(run, 12, "frame bytes")
    run = try auditAdd(run, try size.string(trace.metadata.build), "frame bytes")
    run = try auditAdd(run, try size.string(trace.config.strategy), "frame bytes")
    run = try auditAdd(run, trace.config.seed == nil ? 1 : 9, "frame bytes")
    try size.count(trace.config.parameters.count, limits.maxParameters, "parameters")
    run = try auditAdd(run, 4, "frame bytes")
    for pair in trace.config.parameters {
        run = try auditAdd(run, try size.string(pair.key), "frame bytes")
        run = try auditAdd(run, try size.string(pair.value), "frame bytes")
    }
    run = try auditAdd(run, try size.blob(trace.initialState), "frame bytes")
    run = try auditAdd(run, try size.checks(trace.initialChecks), "frame bytes")
    try size.frame(run, total: &total)
    for step in trace.steps {
        size.items = try auditAdd(size.items, 1, "aggregate items")
        try auditBound(size.items, limits.maxItems, "aggregate items")
        var n = try auditAdd(8, try size.blob(step.input), "frame bytes")
        n = try auditAdd(n, 1, "frame bytes")
        switch step.disposition {
        case .accepted: break
        case let .rejected(reason), let .ignored(reason): n = try auditAdd(n, try size.string(reason), "frame bytes")
        }
        try size.count(step.outputs.count, limits.maxOutputsPerStep, "outputs per step")
        n = try auditAdd(n, 4, "frame bytes")
        for output in step.outputs { n = try auditAdd(n, try size.blob(output), "frame bytes") }
        n = try auditAdd(n, try size.blob(step.postState), "frame bytes")
        n = try auditAdd(n, try size.checks(step.checks), "frame bytes")
        try size.frame(n, total: &total)
    }
    var footer: UInt64 = 9
    if case let .modelError(reason) = trace.termination { footer = try auditAdd(footer, try size.string(reason), "frame bytes") }
    try size.frame(footer, total: &total)
}
private struct AuditEncoder {
    var bytes: [UInt8] = []
    mutating func integer(_ n: UInt64, _ width: Int) { for shift in 0..<width { bytes.append(UInt8(truncatingIfNeeded: n >> (shift * 8))) } }
    mutating func blob(_ b: [UInt8]) { integer(UInt64(b.count), 4); bytes.append(contentsOf: b) }
    mutating func string(_ s: String) { integer(UInt64(s.utf8.count), 4); bytes.append(contentsOf: s.utf8) }
    mutating func checks(_ values: [Check]) {
        integer(UInt64(values.count), 4)
        for check in values {
            string(check.id)
            switch check.status {
            case .passed: bytes.append(0)
            case .failed: bytes.append(1); string(check.details)
            case .skipped: bytes.append(2); string(check.details)
            }
        }
    }
}
/// Validates the entire trace before the first sink callback. I/O errors may leave
/// a prefix; the writer neither flushes/closes nor durably publishes the stream.
public func writeAuditTrace(_ trace: AuditTrace, limits: BinaryTraceLimits = BinaryTraceLimits(), to write: ([UInt8]) throws -> Void) throws {
    try auditPreflight(trace, limits)
    func frame(_ tag: UInt8, _ payload: [UInt8]) throws {
        var e = AuditEncoder(); e.bytes.append(tag); e.integer(UInt64(payload.count), 4); e.bytes.append(contentsOf: payload)
        let crc = auditCRC32(e.bytes); e.integer(UInt64(crc), 4); try write(e.bytes)
    }
    try write(auditHeader)
    var run = AuditEncoder(); run.string(trace.metadata.name)
    run.integer(UInt64(trace.metadata.modelVersion), 4); run.integer(UInt64(trace.metadata.propertiesVersion), 4); run.integer(UInt64(trace.metadata.codecVersion), 4)
    run.string(trace.metadata.build); run.string(trace.config.strategy)
    if let seed = trace.config.seed { run.bytes.append(1); run.integer(seed, 8) } else { run.bytes.append(0) }
    run.integer(UInt64(trace.config.parameters.count), 4)
    for pair in trace.config.parameters { run.string(pair.key); run.string(pair.value) }
    run.blob(trace.initialState); run.checks(trace.initialChecks); try frame(1, run.bytes)
    for (index, step) in trace.steps.enumerated() {
        var e = AuditEncoder(); e.integer(UInt64(index), 8); e.blob(step.input)
        switch step.disposition {
        case .accepted: e.bytes.append(0)
        case let .rejected(s): e.bytes.append(1); e.string(s)
        case let .ignored(s): e.bytes.append(2); e.string(s)
        }
        e.integer(UInt64(step.outputs.count), 4); for output in step.outputs { e.blob(output) }
        e.blob(step.postState); e.checks(step.checks); try frame(2, e.bytes)
    }
    var end = AuditEncoder(); end.integer(UInt64(trace.steps.count), 8)
    switch trace.termination {
    case .completed: end.bytes.append(0)
    case .propertyFailed: end.bytes.append(1)
    case .stepLimit: end.bytes.append(2)
    case .interrupted: end.bytes.append(3)
    case let .modelError(s): end.bytes.append(4); end.string(s)
    }
    try frame(3, end.bytes)
}
public func encodeAuditTrace(_ trace: AuditTrace, limits: BinaryTraceLimits = BinaryTraceLimits()) throws -> [UInt8] {
    var bytes: [UInt8] = []; try writeAuditTrace(trace, limits: limits) { bytes.append(contentsOf: $0) }; return bytes
}
private struct AuditCursor {
    let bytes: [UInt8]
    var offset: Int
    let end: Int
    let limits: BinaryTraceLimits
    mutating func integer(_ width: Int) throws -> UInt64 {
        guard width <= end - offset else { throw BinaryTraceError.invalidData("field exceeds frame") }
        var value: UInt64 = 0
        for shift in 0..<width { value |= UInt64(bytes[offset + shift]) << (shift * 8) }
        offset += width; return value
    }
    mutating func blob(_ string: Bool = false) throws -> [UInt8] {
        let count = try integer(4)
        try auditBound(count, string ? limits.maxStringBytes : limits.maxBlobBytes, string ? "string bytes" : "blob bytes")
        guard count <= UInt64(end - offset) else { throw BinaryTraceError.invalidData("field exceeds frame") }
        let next = offset + Int(count), result = Array(bytes[offset..<next]); offset = next; return result
    }
    mutating func string() throws -> String {
        let raw = try blob(true), value = String(decoding: raw, as: UTF8.self)
        guard Array(value.utf8) == raw else { throw BinaryTraceError.invalidData("invalid UTF-8") }; return value
    }
    mutating func count(_ maximum: UInt64, _ minimum: UInt64, _ name: String, items: inout UInt64) throws -> Int {
        let count = try integer(4); try auditBound(count, maximum, name)
        items = try auditAdd(items, count, "aggregate items"); try auditBound(items, limits.maxItems, "aggregate items")
        guard count <= UInt64(end - offset) / minimum else { throw BinaryTraceError.invalidData("collection count exceeds frame") }
        return Int(count)
    }
    mutating func checks(items: inout UInt64) throws -> [Check] {
        let count = try count(limits.maxChecksPerStep, 5, "checks per step", items: &items)
        var checks: [Check] = []
        for _ in 0..<count {
            let id = try string(), tag = try integer(1)
            switch tag {
            case 0: checks.append(.passed(id))
            case 1: checks.append(.failed(id, try string()))
            case 2: checks.append(.skipped(id, try string()))
            default: throw BinaryTraceError.invalidData("check status tag")
            }
        }
        return checks
    }
    func finished() throws { guard offset == end else { throw BinaryTraceError.invalidData("trailing record fields") } }
}
private func auditValidateHeader(_ bytes: [UInt8]) throws {
    guard bytes.count == 12 else { throw BinaryTraceError.truncated }
    guard Array(bytes.prefix(8)) == Array(auditHeader.prefix(8)) else { throw BinaryTraceError.invalidData("file magic") }
    let version = UInt16(bytes[8]) | UInt16(bytes[9]) << 8
    guard version == 1 else { throw BinaryTraceError.unsupportedVersion(version) }
    guard bytes[10] == 0 && bytes[11] == 0 else { throw BinaryTraceError.invalidData("unsupported header flags") }
}
private struct AuditDecoderState {
    let limits: BinaryTraceLimits
    var items: UInt64 = 0
    var steps: [TraceStep] = []
    var metadata: Metadata?
    var config: RunConfig?
    var initial: [UInt8] = []
    var checks: [Check] = []
    mutating func consume(_ tag: UInt8, _ cursor: AuditCursor) throws -> AuditTrace? {
        var c = cursor
        if metadata == nil {
            guard tag == 1 else { throw BinaryTraceError.invalidData("first record must be run metadata") }
            let name = try c.string(), model = UInt32(try c.integer(4)), properties = UInt32(try c.integer(4)), codec = UInt32(try c.integer(4)), build = try c.string()
            metadata = Metadata(name: name, modelVersion: model, propertiesVersion: properties, codecVersion: codec, build: build)
            let strategy = try c.string(), seedTag = try c.integer(1)
            let seed: UInt64?
            switch seedTag { case 0: seed = nil; case 1: seed = try c.integer(8); default: throw BinaryTraceError.invalidData("seed tag") }
            let count = try c.count(limits.maxParameters, 8, "parameters", items: &items)
            var parameters: [AuditParameter] = []
            for _ in 0..<count { parameters.append(AuditParameter(try c.string(), try c.string())) }
            config = RunConfig(strategy: strategy, seed: seed, parameters: parameters)
            initial = try c.blob(); checks = try c.checks(items: &items); try c.finished()
        } else if tag == 2 {
            try auditBound(UInt64(steps.count) + 1, limits.maxSteps, "steps")
            items = try auditAdd(items, 1, "aggregate items"); try auditBound(items, limits.maxItems, "aggregate items")
            guard try c.integer(8) == UInt64(steps.count) else { throw BinaryTraceError.invalidData("transition index") }
            let input = try c.blob(), dispositionTag = try c.integer(1)
            let disposition: Disposition
            switch dispositionTag { case 0: disposition = .accepted; case 1: disposition = .rejected(try c.string()); case 2: disposition = .ignored(try c.string()); default: throw BinaryTraceError.invalidData("disposition tag") }
            let count = try c.count(limits.maxOutputsPerStep, 4, "outputs per step", items: &items)
            var outputs: [[UInt8]] = []; for _ in 0..<count { outputs.append(try c.blob()) }
            let post = try c.blob(), stepChecks = try c.checks(items: &items); try c.finished()
            steps.append(TraceStep(input: input, disposition: disposition, outputs: outputs, postState: post, checks: stepChecks))
        } else if tag == 3 {
            guard try c.integer(8) == UInt64(steps.count) else { throw BinaryTraceError.invalidData("footer transition count") }
            let tag = try c.integer(1), termination: AuditTermination
            switch tag { case 0: termination = .completed; case 1: termination = .propertyFailed; case 2: termination = .stepLimit; case 3: termination = .interrupted; case 4: termination = .modelError(try c.string()); default: throw BinaryTraceError.invalidData("termination tag") }
            try c.finished()
            return AuditTrace(metadata: metadata!, config: config!, initialState: initial, initialChecks: checks, steps: steps, termination: termination)
        } else { throw BinaryTraceError.invalidData("unknown or misplaced record") }
        return nil
    }
}
public func decodeAuditTrace(_ bytes: [UInt8], limits: BinaryTraceLimits = BinaryTraceLimits()) throws -> AuditTrace {
    try auditBound(UInt64(bytes.count), limits.maxTotalBytes, "total bytes")
    guard bytes.count >= 12 else { throw BinaryTraceError.truncated }
    try auditValidateHeader(Array(bytes.prefix(12)))
    var offset = 12, state = AuditDecoderState(limits: limits)
    while true {
        guard bytes.count - offset >= 5 else { throw BinaryTraceError.truncated }
        let start = offset, tag = bytes[offset]; offset += 1
        var head = AuditCursor(bytes: bytes, offset: offset, end: bytes.count, limits: limits)
        let length = try head.integer(4); offset = head.offset
        try auditBound(length, limits.maxFrameBytes, "frame bytes")
        let required = try auditAdd(UInt64(offset), try auditAdd(length, 4, "total bytes"), "total bytes")
        try auditBound(required, limits.maxTotalBytes, "total bytes")
        guard required <= UInt64(bytes.count) else { throw BinaryTraceError.truncated }
        let payloadEnd = offset + Int(length)
        var tail = AuditCursor(bytes: bytes, offset: payloadEnd, end: bytes.count, limits: limits)
        let checksum = try tail.integer(4)
        guard UInt64(auditCRC(bytes, start..<payloadEnd)) == checksum else { throw BinaryTraceError.checksumMismatch }
        let c = AuditCursor(bytes: bytes, offset: offset, end: payloadEnd, limits: limits)
        offset = tail.offset
        if let trace = try state.consume(tag, c) {
            guard offset == bytes.count else { throw BinaryTraceError.invalidData("trailing data after footer") }
            return trace
        }
    }
}
/// Incremental bounded reader: validates frame/remaining-total quotas before
/// requesting payload bytes, verifies CRC before decoding, and probes EOF after
/// the footer. Callback blocking and allocator overhead are outside codec bounds.
public func readAuditTrace(limits: BinaryTraceLimits = BinaryTraceLimits(), from read: (Int) throws -> [UInt8]) throws -> AuditTrace {
    var total: UInt64 = 0
    func exact(_ count: Int) throws -> [UInt8] {
        try auditBound(try auditAdd(total, UInt64(count), "total bytes"), limits.maxTotalBytes, "total bytes")
        var bytes: [UInt8] = []
        while bytes.count < count {
            let requested = count - bytes.count, chunk = try read(requested)
            guard chunk.count <= requested else { throw BinaryTraceError.invalidData("reader exceeded requested chunk size") }
            guard !chunk.isEmpty else { throw BinaryTraceError.truncated }
            bytes.append(contentsOf: chunk); total += UInt64(chunk.count)
        }
        return bytes
    }
    try auditValidateHeader(exact(12))
    var state = AuditDecoderState(limits: limits)
    while true {
        var frame = try exact(5)
        var head = AuditCursor(bytes: frame, offset: 1, end: 5, limits: limits)
        let length = try head.integer(4)
        try auditBound(length, limits.maxFrameBytes, "frame bytes")
        try auditBound(length, UInt64(Int.max), "frame bytes")
        let remaining = try auditAdd(length, 4, "total bytes")
        try auditBound(try auditAdd(total, remaining, "total bytes"), limits.maxTotalBytes, "total bytes")
        frame.append(contentsOf: try exact(Int(length)))
        let crcBytes = try exact(4)
        var crc = AuditCursor(bytes: crcBytes, offset: 0, end: 4, limits: limits)
        guard try crc.integer(4) == UInt64(auditCRC32(frame)) else { throw BinaryTraceError.checksumMismatch }
        if let trace = try state.consume(frame[0], AuditCursor(bytes: frame, offset: 5, end: frame.count, limits: limits)) {
            let trailing = try read(1)
            guard trailing.isEmpty else { throw BinaryTraceError.invalidData("trailing data after footer") }
            return trace
        }
    }
}

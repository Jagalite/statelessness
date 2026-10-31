import Foundation
/// Exact binary-v1 wire accounting shared by bounded execution, with no codecs
/// or serialized payload allocation. Counts framing, UTF-8 text and all items.
func executionBlobLimit(_ limits: BinaryTraceLimits) -> Int {
    Int(min(UInt64(Int.max), limits.maxBlobBytes, limits.maxFrameBytes, limits.maxTotalBytes, UInt64(UInt32.max)))
}
func executionRunSize(_ metadata: Metadata, _ config: RunConfig, _ state: [UInt8], _ checks: [Check], _ limits: BinaryTraceLimits) throws -> (bytes: UInt64, items: UInt64) {
    var size = AuditSizer(limits: limits)
    var run = try size.string(metadata.name)
    run = try auditAdd(run, 12, "frame bytes")
    run = try auditAdd(run, try size.string(metadata.build), "frame bytes")
    run = try auditAdd(run, try size.string(config.strategy), "frame bytes")
    run = try auditAdd(run, config.seed == nil ? 1 : 9, "frame bytes")
    try size.count(config.parameters.count, limits.maxParameters, "parameters")
    run = try auditAdd(run, 4, "frame bytes")
    for pair in config.parameters {
        run = try auditAdd(run, try size.string(pair.key), "frame bytes")
        run = try auditAdd(run, try size.string(pair.value), "frame bytes")
    }
    run = try auditAdd(run, try size.blob(state), "frame bytes")
    run = try auditAdd(run, try size.checks(checks), "frame bytes")
    var total: UInt64 = 0; try size.frame(run, total: &total)
    return (total, size.items)
}
func executionStepFixed(_ input: [UInt8], _ disposition: Disposition, _ outputCount: Int, _ checks: [Check], _ limits: BinaryTraceLimits) throws -> (bytes: UInt64, items: UInt64) {
    var size = AuditSizer(limits: limits)
    // Rust trace::step_size admits the STEP item before its collections. This
    // affects which limit wins when a check also has an oversized string.
    size.items = 1
    try auditBound(size.items, limits.maxItems, "aggregate items")
    var fixed: UInt64 = 0
    func append(_ bytes: UInt64) throws {
        fixed = try auditAdd(fixed, bytes, "frame bytes")
        try auditBound(fixed, min(limits.maxFrameBytes, UInt64(UInt32.max)), "frame bytes")
    }
    // Admit fields in wire order: an earlier frame overflow must win over a
    // later collection or string limit, just as trace::Encoder::bytes does.
    try append(8)
    try append(size.blob(input))
    try append(1)
    switch disposition {
    case .accepted: break
    case let .rejected(s), let .ignored(s): try append(size.string(s))
    }
    try size.count(outputCount, limits.maxOutputsPerStep, "outputs per step")
    try append(4)
    try append(UInt64(outputCount) * 4) // empty provisional output blobs
    try append(4) // empty provisional post-state blob
    try size.count(checks.count, limits.maxChecksPerStep, "checks per step")
    try append(4)
    for check in checks {
        guard check.status != .passed || check.details.isEmpty else {
            throw BinaryTraceError.invalidData("passing checks have no details")
        }
        try append(size.string(check.id))
        try append(1)
        if check.status != .passed { try append(size.string(check.details)) }
    }
    return (fixed, size.items)
}
/// Replay has a different preflight from monitor STEP serialization: validate
/// the isolated check batch before combining its items with outputs and STEP.
func executionReplayStepFixed(_ input: [UInt8], _ disposition: Disposition, _ outputCount: Int, _ checks: [Check], _ limits: BinaryTraceLimits) throws -> (bytes: UInt64, items: UInt64) {
    try auditBound(UInt64(outputCount), min(limits.maxOutputsPerStep, UInt64(UInt32.max)), "outputs per step")
    var size = AuditSizer(limits: limits)
    var batch: UInt64 = 0
    func appendCheckBytes(_ bytes: UInt64) throws {
        batch = try auditAdd(batch, bytes, "frame bytes")
        try auditBound(batch, min(limits.maxFrameBytes, UInt64(UInt32.max)), "frame bytes")
    }
    // Match trace::checked_batch_size, including each field's frame admission.
    // A frame overflow at a status byte precedes a later oversized detail/ID.
    try size.count(checks.count, limits.maxChecksPerStep, "checks per step")
    try appendCheckBytes(4)
    for check in checks {
        guard check.status != .passed || check.details.isEmpty else {
            throw BinaryTraceError.invalidData("passing checks have no details")
        }
        try appendCheckBytes(size.string(check.id))
        try appendCheckBytes(1)
        if check.status != .passed { try appendCheckBytes(size.string(check.details)) }
    }
    let items = try auditAdd(try auditAdd(size.items, UInt64(outputCount), "aggregate items"), 1, "aggregate items")
    try auditBound(items, limits.maxItems, "aggregate items")
    let reason: UInt64
    switch disposition {
    case .accepted: reason = 0
    case let .rejected(text), let .ignored(text): reason = try size.string(text)
    }
    // sequence + input length + disposition + output count + post-state length,
    // followed by input/reason bodies, empty output prefixes and check batch.
    var fixed = try auditAdd(21, UInt64(input.count), "frame bytes")
    fixed = try auditAdd(fixed, reason, "frame bytes")
    fixed = try auditAdd(fixed, UInt64(outputCount) * 4, "frame bytes")
    fixed = try auditAdd(fixed, batch, "frame bytes")
    try auditBound(fixed, min(limits.maxFrameBytes, UInt64(UInt32.max)), "frame bytes")
    return (fixed, items)
}
func executionRemaining(_ fixed: UInt64, _ bytes: UInt64, footer: UInt64, limits: BinaryTraceLimits) throws -> UInt64 {
    let reserved = try auditAdd(try auditAdd(bytes, footer, "total bytes"), 9, "total bytes")
    try auditBound(reserved, limits.maxTotalBytes, "total bytes")
    let frame = min(limits.maxFrameBytes, UInt64(UInt32.max), limits.maxTotalBytes - reserved)
    try auditBound(fixed, frame, "frame bytes")
    return frame - fixed
}
func executionErrorTextLimit(_ limits: BinaryTraceLimits) -> Int {
    Int(min(256, limits.maxStringBytes, limits.maxFrameBytes > 13 ? limits.maxFrameBytes - 13 : 0))
}
func executionErrorText(_ error: Error, _ limits: BinaryTraceLimits) -> String {
    let maximum = executionErrorTextLimit(limits)
    // The error formatter is caller-owned, but the engine need only copy one
    // byte beyond the wire limit to distinguish a complete value from a prefix.
    let bytes = Array(String(describing:error).utf8.prefix(maximum+1))
    if bytes.count <= maximum { return String(decoding: bytes, as: UTF8.self) }
    var end = maximum >= 3 ? maximum - 3 : maximum
    while end > 0 && String(bytes: bytes.prefix(end), encoding: .utf8) == nil { end -= 1 }
    return String(decoding: bytes.prefix(end), as: UTF8.self) + (maximum >= 3 ? "..." : "")
}

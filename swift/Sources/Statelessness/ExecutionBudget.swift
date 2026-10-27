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
    try size.count(outputCount, limits.maxOutputsPerStep, "outputs per step")
    var fixed = try auditAdd(8, try size.blob(input), "frame bytes")
    fixed = try auditAdd(fixed, 1, "frame bytes")
    switch disposition {
    case .accepted: break
    case let .rejected(s), let .ignored(s): fixed = try auditAdd(fixed, try size.string(s), "frame bytes")
    }
    fixed = try auditAdd(fixed, 8, "frame bytes") // output count, state length
    fixed = try auditAdd(fixed, UInt64(outputCount) * 4, "frame bytes")
    fixed = try auditAdd(fixed, try size.checks(checks), "frame bytes")
    try auditBound(fixed, min(limits.maxFrameBytes, UInt64(UInt32.max)), "frame bytes")
    let items = try auditAdd(size.items, 1, "aggregate items")
    try auditBound(items, limits.maxItems, "aggregate items")
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
    let bytes = Array(String(describing: error).utf8), maximum = executionErrorTextLimit(limits)
    if bytes.count <= maximum { return String(decoding: bytes, as: UTF8.self) }
    var end = maximum >= 3 ? maximum - 3 : maximum
    while end > 0 && String(bytes: bytes.prefix(end), encoding: .utf8) == nil { end -= 1 }
    return String(decoding: bytes.prefix(end), as: UTF8.self) + (maximum >= 3 ? "..." : "")
}

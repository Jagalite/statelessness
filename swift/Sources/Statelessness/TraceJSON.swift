public func checkFromJSON(_ value: JSON) throws -> Check {
    let o = try objectFields(value, required: ["id", "status"], optional: ["details"])
    guard let status = CheckStatus(rawValue: try readString(o["status"])) else { throw FormatError("invalid check status") }
    let result = Check(try readString(o["id"]), status, try readString(o["details"] ?? .string("")))
    if status == .passed && !result.details.isEmpty { throw FormatError("passing check has details") }
    return result
}
public func checkJSON(_ c: Check) -> JSON { .obj(["id": .string(c.id), "status": .string(c.status.rawValue), "details": .string(c.details)]) }
public func dispositionFromJSON(_ value: JSON?) throws -> Disposition {
    let o = try objectFields(value, required: ["kind"], optional: ["reason"])
    let reason = try readString(o["reason"] ?? .string(""))
    switch try readString(o["kind"]) {
    case "accepted" where reason.isEmpty: return .accepted
    case "rejected": return .rejected(reason)
    case "ignored": return .ignored(reason)
    default: throw FormatError("invalid disposition")
    }
}
public func dispositionJSON(_ d: Disposition) -> JSON {
    let kind: String, reason: String
    switch d { case .accepted: kind = "accepted"; reason = ""; case let .rejected(s): kind = "rejected"; reason = s; case let .ignored(s): kind = "ignored"; reason = s }
    return .obj(["kind": .string(kind), "reason": .string(reason)])
}
public func metadataFromJSON(_ value: JSON?) throws -> Metadata {
    let o = try objectFields(value, required: ["name", "model_version", "properties_version", "codec_version", "build"])
    return Metadata(name: try readString(o["name"]), modelVersion: UInt32(try readInteger(o["model_version"], maximum: UInt64(UInt32.max))), propertiesVersion: UInt32(try readInteger(o["properties_version"], maximum: UInt64(UInt32.max))), codecVersion: UInt32(try readInteger(o["codec_version"], maximum: UInt64(UInt32.max))), build: try readString(o["build"]))
}
public func metadataJSON(_ m: Metadata) -> JSON {
    .obj(["name": .string(m.name), "model_version": .uint(UInt64(m.modelVersion)), "properties_version": .uint(UInt64(m.propertiesVersion)), "codec_version": .uint(UInt64(m.codecVersion)), "build": .string(m.build)])
}
public func hexString(_ bytes: [UInt8]) -> String {
    let digits = Array("0123456789abcdef".utf8)
    return String(decoding: bytes.flatMap { [digits[Int($0 >> 4)], digits[Int($0 & 15)]] }, as: UTF8.self)
}
/// Observation envelope only. This does not convert or preserve Rust audit metadata.
public func traceToJSON(_ t: Trace) -> JSON {
    .obj(["format": .string("stateless.trace-json"), "version": .int(1), "metadata": metadataJSON(t.metadata),
          "initial_state": .string(hexString(t.initialState)), "initial_checks": .array(t.initialChecks.map(checkJSON)),
          "steps": .array(t.steps.map { s in .obj(["input": .string(hexString(s.input)), "disposition": dispositionJSON(s.disposition), "outputs": .array(s.outputs.map { .string(hexString($0)) }), "post_state": .string(hexString(s.postState)), "checks": .array(s.checks.map(checkJSON))]) }),
          "termination": .string(t.termination.rawValue), "error": .string(t.error)])
}
public func traceFromJSON(_ value: JSON, limits: TraceLimits = TraceLimits()) throws -> Trace {
    try limits.validate()
    let o = try objectFields(value, required: ["format", "version", "metadata", "initial_state", "initial_checks", "steps", "termination", "error"])
    guard try readString(o["format"]) == "stateless.trace-json", try readInteger(o["version"]) == 1 else { throw FormatError("unsupported trace format") }
    var items = 0, payload = 0
    func count(_ n: Int) throws { guard n <= limits.maxItems - items else { throw FormatError("aggregate trace item limit") }; items += n }
    func checks(_ value: JSON?) throws -> [Check] { let a = try readArray(value, maximum: limits.maxItems); try count(a.count); return try a.map(checkFromJSON) }
    func blob(_ value: JSON?) throws -> [UInt8] {
        let text = try readString(value)
        // Inspect only up to the first byte beyond the blob limit. Constructing
        // an array before this check lets a direct JSON caller force an
        // allocation proportional to an otherwise rejected hexadecimal value.
        let count = text.utf8.prefix(limits.maxBlobBytes * 2 + 1).count
        guard count <= limits.maxBlobBytes * 2, count % 2 == 0 else { throw FormatError("invalid hexadecimal bytes") }
        guard count / 2 <= limits.maxPayloadBytes - payload else { throw FormatError("aggregate trace payload limit") }; payload += count / 2
        let bytes = Array(text.utf8)
        func digit(_ c: UInt8) throws -> UInt8 { switch c { case 48...57: return c - 48; case 97...102: return c - 87; default: throw FormatError("invalid lowercase hex") } }
        var result: [UInt8] = []; result.reserveCapacity(bytes.count / 2)
        for i in stride(from: 0, to: bytes.count, by: 2) { result.append(try digit(bytes[i]) * 16 + digit(bytes[i + 1])) }; return result
    }
    let initial = try blob(o["initial_state"]), initialChecks = try checks(o["initial_checks"])
    var steps: [TraceStep] = []
    for value in try readArray(o["steps"], maximum: limits.maxSteps) {
        let s = try objectFields(value, required: ["input", "disposition", "outputs", "post_state", "checks"])
        let a = try readArray(s["outputs"], maximum: limits.maxItems); try count(1 + a.count)
        steps.append(TraceStep(input: try blob(s["input"]), disposition: try dispositionFromJSON(s["disposition"]), outputs: try a.map(blob), postState: try blob(s["post_state"]), checks: try checks(s["checks"])))
    }
    guard let termination = Termination(rawValue: try readString(o["termination"])) else { throw FormatError("invalid termination") }
    let error = try readString(o["error"])
    guard error.isEmpty || termination == .modelError else { throw FormatError("error text requires model_error termination") }
    return Trace(metadata: try metadataFromJSON(o["metadata"]), initialState: initial, initialChecks: initialChecks, steps: steps, termination: termination, error: error)
}
public func parseTrace(_ bytes: [UInt8], limits: TraceLimits = TraceLimits()) throws -> Trace { try traceFromJSON(parseJSON(bytes, maximum: limits.maxJSONBytes), limits: limits) }
public func encodeTrace(_ trace: Trace, limits: TraceLimits = TraceLimits()) throws -> [UInt8] {
    try limits.validate()
    // Check retained collection and payload limits before constructing the JSON
    // tree. In particular, hexString temporarily expands each blob, so doing
    // this after traceToJSON would allocate for data that must be rejected.
    // On a multiply-invalid trace, a later limit can precede an earlier
    // semantic error. Count a lower bound on rendered JSON before creating the
    // intermediate tree: each retained text byte, each hex digit, and at
    // least one byte for every check, output, and step must appear in it.
    // Escaping and JSON syntax may add bytes, so renderJSON remains the exact
    // limit check. This bounds construction by maxJSONBytes but is not a bound
    // on process memory or allocations already owned by the caller.
    guard trace.steps.count <= limits.maxSteps else { throw FormatError("trace step limit") }
    var items = 0, payload = 0, jsonMinimum = 0
    func jsonBytes(_ n: Int) throws {
        guard n <= limits.maxJSONBytes - jsonMinimum else { throw FormatError("JSON output byte limit") }
        jsonMinimum += n
    }
    func text(_ value: String) throws {
        // Stop one byte after the remaining allowance without materializing
        // a full UTF-8 copy of an otherwise rejected string.
        let remaining = limits.maxJSONBytes - jsonMinimum
        let n = value.utf8.prefix(remaining + 1).count
        try jsonBytes(n)
    }
    func checks(_ values: [Check]) throws {
        try count(values.count)
        for value in values { try text(value.id); try text(value.details) }
    }
    func count(_ n: Int) throws {
        guard n <= limits.maxItems - items else { throw FormatError("aggregate trace item limit") }
        items += n
        try jsonBytes(n)
    }
    func blob(_ bytes: [UInt8]) throws {
        guard bytes.count <= limits.maxBlobBytes else { throw FormatError("invalid hexadecimal bytes") }
        guard bytes.count <= limits.maxPayloadBytes - payload else { throw FormatError("aggregate trace payload limit") }
        payload += bytes.count
        // TraceLimits.validate() keeps maxBlobBytes <= Int.max/2.
        try jsonBytes(bytes.count * 2)
    }
    try text(trace.metadata.name); try text(trace.metadata.build); try text(trace.error)
    try blob(trace.initialState); try checks(trace.initialChecks)
    for step in trace.steps {
        try count(1); try count(step.outputs.count)
        try blob(step.input)
        switch step.disposition {
        case .accepted: break
        case let .rejected(reason), let .ignored(reason): try text(reason)
        }
        for output in step.outputs { try blob(output) }
        try blob(step.postState); try checks(step.checks)
    }
    let value = traceToJSON(trace); _ = try traceFromJSON(value, limits: limits)
    return try renderJSON(value, maximum: limits.maxJSONBytes)
}

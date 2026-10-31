import Statelessness

/// Project the detached current simulation state at the requested bounded path.
public typealias ProtocolWatchProjection<S> = (S,SnapshotID,[PathSegment],InspectLimits) throws -> InspectResult
public typealias ProtocolPagedWatchProjection<S> = (S,SnapshotID,[PathSegment],PageRequest,InspectLimits) throws -> InspectResult
struct ProtocolWatchAttachment<S> {
    let registry: WatchRegistry
    let project: ProtocolPagedWatchProjection<S>
    let schema: ((S) throws -> DisplaySchema)?
    let supportsPaging: Bool
}

/// Rust `decode_segment` (statelessness-debug protocol.rs): booleans are only
/// `true`/`false`; integer keys are limited to Rust's integer kinds, parsed with
/// Rust `str::parse` rules (one optional sign, `-` only for signed kinds,
/// ASCII digits, range checked) and retained in canonical decimal form.
func watchPath(_ encoded: [String]) throws -> [PathSegment] {
    try encoded.map { text in
        guard let colon = text.firstIndex(of: ":") else { throw ProtocolError.malformed }
        let kind = String(text[..<colon]), value = String(text[text.index(after:colon)...])
        switch kind {
        case "field": return .field(ExactString(value))
        case "variant": return .variant(ExactString(value))
        case "index": guard let n = UInt64(try watchCanonicalInteger(value,signed:false,bits:Int.bitWidth)) else { throw ProtocolError.malformed }; return .index(n)
        case "key.string": return .typedMapKey(.string(ExactString(value)))
        case "key.bool":
            if value.utf8.elementsEqual("true".utf8) { return .typedMapKey(.bool(true)) }
            if value.utf8.elementsEqual("false".utf8) { return .typedMapKey(.bool(false)) }
            throw ProtocolError.malformed
        case "key.char": guard let scalar = value.unicodeScalars.first, value.unicodeScalars.count == 1 else { throw ProtocolError.malformed }; return .typedMapKey(.character(scalar.value))
        default:
            let integerKinds: [String:(Bool,Int)] = ["key.i8":(true,8),"key.i16":(true,16),"key.i32":(true,32),"key.i64":(true,64),"key.i128":(true,128),"key.isize":(true,Int.bitWidth),
                                                    "key.u8":(false,8),"key.u16":(false,16),"key.u32":(false,32),"key.u64":(false,64),"key.u128":(false,128),"key.usize":(false,Int.bitWidth)]
            guard kind.utf8.allSatisfy({ $0 < 0x80 }),let entry = integerKinds[kind] else { throw ProtocolError.malformed }
            let (signed,bits) = entry
            return .typedMapKey(.integer(kind:ExactString(String(kind.dropFirst(4))),decimal:ExactString(try watchCanonicalInteger(value,signed:signed,bits:bits))))
        }
    }
}
/// Decimal value of 2^exponent, without fixed-width arithmetic.
private func watchPowerOfTwo(_ exponent: Int) -> [UInt8] {
    var digits: [UInt8] = [1] // least significant first
    for _ in 0..<exponent {
        var carry: UInt8 = 0
        for i in digits.indices { let v = digits[i]*2 + carry; digits[i] = v % 10; carry = v / 10 }
        if carry > 0 { digits.append(carry) }
    }
    return digits.reversed()
}
private func watchDecrement(_ digits: [UInt8]) -> [UInt8] {
    var result = digits, i = result.count-1
    while result[i] == 0 { result[i] = 9; i -= 1 }
    result[i] -= 1
    let trimmed = Array(result.drop { $0 == 0 })
    return trimmed.isEmpty ? [0] : trimmed
}
private func watchNotGreater(_ a: [UInt8],_ b: [UInt8]) -> Bool {
    if a.count != b.count { return a.count < b.count }
    return a == b || a.lexicographicallyPrecedes(b)
}
/// Rust `str::parse::<i128/u128>()` followed by the per-type range check and
/// `to_string()`: leading zeros and a `+` sign are accepted and canonicalized.
func watchCanonicalInteger(_ value: String,signed: Bool,bits: Int) throws -> String {
    var bytes = Array(value.utf8)[...]
    var negative = false
    if let first = bytes.first, first == UInt8(ascii:"+") || first == UInt8(ascii:"-") {
        guard first == UInt8(ascii:"+") || signed else { throw ProtocolError.malformed }
        negative = first == UInt8(ascii:"-"); bytes = bytes.dropFirst()
    }
    guard !bytes.isEmpty,bytes.allSatisfy({ (UInt8(ascii:"0")...UInt8(ascii:"9")).contains($0) }) else { throw ProtocolError.malformed }
    var magnitude = Array(bytes.drop { $0 == UInt8(ascii:"0") }.map { $0 - UInt8(ascii:"0") })
    if magnitude.isEmpty { magnitude = [0]; negative = false }
    let limit: [UInt8]
    if signed { let half = watchPowerOfTwo(bits-1); limit = negative ? half : watchDecrement(half) }
    else { limit = watchDecrement(watchPowerOfTwo(bits)) }
    guard watchNotGreater(magnitude,limit) else { throw ProtocolError.malformed }
    return (negative ? "-" : "") + String(decoding:magnitude.map { $0 + UInt8(ascii:"0") },as:UTF8.self)
}
private func watchVariant(_ value: String) -> String { value.prefix(1).uppercased() + value.dropFirst() }
private func watchKind(_ event: WatchEvent) -> String {
    switch event.kind {
    case .baseline: return "Baseline(\(watchVariant(event.baselineReason?.rawValue ?? "initial")))"
    case .schemaChanged: return "SchemaChanged"
    case .enabled: return "Enabled"
    case .disabled: return "Disabled"
    case .paused: return "Paused"
    case .removed: return "Removed"
    case .expired: return "Expired"
    case let .eventsDropped(count): return "EventsDropped { count: \(count) }"
    case .changed: return "Changed"
    case .unchanged: return "Unchanged"
    case let .changeSinceLastSample(changed): return "ChangeSinceLastSample { changed: \(changed) }"
    case .unknown: return "Unknown"
    case .propertyFailure: return "PropertyFailure"
    case .valueMatched: return "ValueMatched"
    case .inspectorError: return "InspectorError(\(event.inspectionError ?? "InvalidMetadata"))"
    case .observationGap: return "ObservationGap"
    case .snapshot: return "Snapshot"
    }
}
private func watchType(_ node: InspectNode) -> String {
    switch node.kind {
    case .scalar(.unit): return "Unit"
    case .scalar(.bool): return "Bool"
    case .scalar(.character): return "Char"
    case .scalar(.integer): return "Integer"
    case .scalar(.float): return "Float"
    case .string: return "String"
    case .bytes: return "Bytes"
    case .object: return "Object"
    case .enumeration: return "Enum"
    case .sequence: return "Sequence"
    case .map: return "Map"
    case .opaque: return "Opaque"
    case .redacted: return "Redacted"
    case .unavailable: return "Unavailable"
    case .truncated: return "Truncated"
    }
}
func protocolWatchError(_ error: Error) -> ProtocolError {
    if let error = error as? ProtocolError { return error }
    if let error = error as? WatchError {
        switch error { case .capacity: return .limit; case .unauthorized,.redactedPath: return .unauthorized; case .unknownWatch: return .staleHandle; case .revisionExhausted: return .exhausted; case .invalidConfig,.schemaMismatch: return .invalidInput }
    }
    if let error = error as? DebugError, case .reentrant = error { return .busy }
    if let error = error as? InspectError { switch error { case .budgetExceeded: return .limit; case .redactedPath: return .unauthorized; default: return .invalidInput } }
    return .callback
}
func prepareWatch<S,I,O>(_ attachment: ProtocolWatchAttachment<S>, session: DebugSession<S,I,O>, command: ProtocolHostCommand, limits: ProtocolLimits) throws -> PreparedProtocolResponse {
    let registry = attachment.registry
    let snapshot = SnapshotID(session:session.epoch,revision:session.revision,sequence:session.sequence)
    do {
        switch command {
        case let .watch(schema,baseline,path):
            let segments = try watchPath(path)
            var config = WatchConfig(); config.path = segments; config.schemaVersion = schema
            config.limits.maxBytes = min(registry.protocolMaxBytes,max(1,limits.maxFrameBytes/8))
            config.limits.maxValueBytes = limits.maxFieldBytes/4
            config.limits.maxNodes = min(config.limits.maxNodes,128); config.limits.maxDepth = min(config.limits.maxDepth,16); config.limits.maxPageSize = limits.maxPageSize
            config.page = PageRequest(offset:0,limit:attachment.supportsPaging ? config.limits.maxPageSize : min(100,config.limits.maxPageSize))
            let schema: (() throws -> DisplaySchema)? = attachment.schema.map { declared in { try session.withInspectionState { try declared($0) } } }
            let prepared = try registry.prepareProtocolAdd(config,snapshot:snapshot,schema:schema) { path,limits in
                try session.withInspectionState { state in try attachment.project(state,snapshot,path,config.page,limits) }
            }
            let ack = prepared.0, result = prepared.1
            let fields = ["watch.id":"u64:\(ack.watchID)","watch.generation":"u64:\(ack.generation)","watch.effective_sequence":"u64:\(ack.effectiveSequence)","watch.installed_at_revision":"u64:\(baseline)","watch.baseline":"enum:pending_at_effective_boundary","watch.metadata_complete":"bool:false","watch.display_schema":"string:\(result.schema.name)","watch.display_schema_version":"u64:\(result.schema.version)","watch.resolved_type":"enum:\(watchType(result.node))"]
            let order = ["watch.id","watch.generation","watch.effective_sequence","watch.installed_at_revision","watch.baseline","watch.metadata_complete","watch.display_schema_version","watch.display_schema","watch.resolved_type"]
            return try PreparedProtocolResponse(orderedFields:order.compactMap { key in fields[key].map { (key,$0) } },fit:{ fields,fits in
                var result = fields
                if !fits(result) { result.removeValue(forKey:"watch.display_schema"); result.removeValue(forKey:"watch.display_schema_version"); result.removeValue(forKey:"watch.resolved_type") }
                else { result["watch.metadata_complete"] = "bool:true" }
                guard fits(result) else { throw ProtocolError.limit }; return result
            },commit:prepared.2)
        case let .unwatch(id):
            let (generation,sequence,commit) = try registry.prepareProtocolRemove(id)
            return try PreparedProtocolResponse(orderedFields:[("watch.id","u64:\(id)"),("watch.generation","u64:\(generation)"),("watch.effective_sequence","u64:\(sequence)")],commit:commit)
        case .watchStatus:
            let count = registry.watchCount
            // Reserve the five summary fields before materializing the page.
            // A smaller field budget yields a truthful truncated page.
            guard limits.maxResponseFields >= 5 else { throw ProtocolError.limit }
            let requested = min(count,limits.maxPageSize,(limits.maxResponseFields-5)/5)
            let page = registry.protocolStatuses(limit:requested)
            var fields = ["watches.total":"u64:\(count)","watches.truncated":"bool:\(page.count < count)","watches.dropped_events":"u64:\(registry.health.droppedEvents)","watches.observation_gaps":"u64:\(registry.health.observationGaps)","watches.inspection_errors":"u64:\(registry.health.inspectionErrors)"]
            for (i,status) in page.enumerated() { let key = "watches.\(i)"; fields[key+".id"] = "u64:\(status.id)"; fields[key+".generation"] = "u64:\(status.generation)"; fields[key+".enabled"] = "bool:\(status.enabled)"; fields[key+".has_baseline"] = "bool:\(status.hasBaseline)"; fields[key+".effective_sequence"] = "u64:\(status.effectiveSequence)" }
            let order = ["watches.total","watches.truncated","watches.dropped_events","watches.observation_gaps","watches.inspection_errors"] + page.indices.flatMap { index in ["id","generation","enabled","has_baseline","effective_sequence"].map { "watches.\(index).\($0)" } }
            return try PreparedProtocolResponse(orderedFields:order.compactMap { key in fields[key].map { (key,$0) } },fit:{ fields,fits in
                if fits(fields) { return fields }
                func prefix(_ length: Int) -> [String:String] {
                    var result = fields
                    for index in length..<page.count {
                        for suffix in ["id","generation","enabled","has_baseline","effective_sequence"] { result.removeValue(forKey:"watches.\(index).\(suffix)") }
                    }
                    if length < count { result["watches.truncated"] = "bool:true" }
                    return result
                }
                guard fits(prefix(0)) else { throw ProtocolError.limit }
                var low = 0,high = page.count
                while low < high {
                    let middle = low + (high-low+1)/2
                    if fits(prefix(middle)) { low = middle } else { high = middle-1 }
                }
                return prefix(low)
            })
        case let .watchCurrent(id):
            guard let event = registry.currentEvent(id) else { return PreparedProtocolResponse(fields:["watch.value":"enum:unavailable"]) }
            var fields = ["watch.id":"u64:\(id)","watch.generation":"u64:\(event.generation)","watch.capture_revision":"u64:\(event.captureRevision)","watch.snapshot_revision":"u64:\(event.snapshot.revision)","watch.snapshot_sequence":"u64:\(event.snapshot.sequence)","watch.kind":"enum:\(watchKind(event))","watch.origin":"enum:\(watchVariant(event.origin.rawValue))"]
            switch event.completeness { case .complete: fields["watch.completeness"] = "enum:Complete"; case let .partial(reason): fields["watch.completeness"] = "enum:Partial(\(watchVariant(reason.rawValue)))" }
            if let value = event.value {
                try probeNode(value,prefix:"watch.node",depth:0,fields:&fields)
            }
            var order = ["watch.id","watch.generation","watch.capture_revision","watch.snapshot_revision","watch.snapshot_sequence","watch.kind","watch.completeness","watch.origin"]
            if let value = event.value { order += probeNodeOrder(value,prefix:"watch.node") }
            return try PreparedProtocolResponse(orderedFields:order.compactMap { key in fields[key].map { (key,$0) } })
        default: throw ProtocolError.unsupported
        }
    } catch { throw protocolWatchError(error) }
}

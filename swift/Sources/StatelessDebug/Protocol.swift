import Foundation

public enum ProtocolError: String, Error, Equatable {
    case malformed, unsupportedVersion = "unsupported_version", wrongSession = "wrong_session", wrongEpoch = "wrong_epoch", unauthorized, staleRevision = "stale_revision", staleConfiguration = "stale_configuration", staleHandle = "stale_handle", duplicateConflict = "duplicate_conflict", retiredRequest = "retired_request", limit, unsupported, invalidInput = "invalid_input", ended, disconnected, callback, exhausted, busy
}
public struct ProtocolLimits {
    public var maxFrameBytes = 65536
    public var maxRequestFields = 64
    public var maxResponseFields = 4096
    public var maxFieldBytes = 8192
    public var maxInputBytes = 8192
    public var maxCacheEntries = 64
    public var maxCacheBytes = 4 * 1024 * 1024
    public var maxCandidates = 256
    public var maxCandidateBytes = 256 * 1024
    public var maxEventEntries = 1024
    public var maxEventBytes = 256 * 1024
    public var maxHandles = 64
    public var maxPageSize = 100
    public var maxTelemetrySnapshotBytes = 8 * 1024 * 1024
    public var maxProbeProfiles = 64
    public init() {}
}
public struct ProtocolAuthority {
    public var deliverInputs = false
    public var configureWatches = false
    public var configureDiagnostics = false
    public var readExactValues = false
    public var exportExactTrace = false
    public init() {}
}
public struct ProtocolRequest: Equatable {
    public var version: UInt32 = 1
    public var session: String
    public var epoch: UInt64
    public var requestID: UInt64
    public var expectedRevision: UInt64
    public var expectedConfiguration: UInt64
    /// Known unsupported commands remain explicit wire commands, never callbacks.
    public var command: String
    public var arguments: [String]
    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.version == rhs.version && lhs.session.utf8.elementsEqual(rhs.session.utf8) && lhs.epoch == rhs.epoch && lhs.requestID == rhs.requestID && lhs.expectedRevision == rhs.expectedRevision && lhs.expectedConfiguration == rhs.expectedConfiguration && lhs.command.utf8.elementsEqual(rhs.command.utf8) && lhs.arguments.count == rhs.arguments.count && zip(lhs.arguments,rhs.arguments).allSatisfy { $0.utf8.elementsEqual($1.utf8) }
    }
    public init(session: String, epoch: UInt64, requestID: UInt64, expectedRevision: UInt64 = 0, expectedConfiguration: UInt64 = 0, command: String, arguments: [String] = []) {
        self.session = session; self.epoch = epoch; self.requestID = requestID; self.expectedRevision = expectedRevision; self.expectedConfiguration = expectedConfiguration; self.command = command; self.arguments = arguments
    }
}
public struct ProtocolResponse: Equatable {
    public let session: String
    public let epoch: UInt64
    public let requestID: UInt64
    public let revision: UInt64
    public let configuration: UInt64
    public let error: ProtocolError?
    public let fields: [String: String]
    /// Wire field order, when supplied by a producer or retained by decoding.
    public let fieldOrder: [String]?
    init(session: String,epoch: UInt64,requestID: UInt64,revision: UInt64,configuration: UInt64,error: ProtocolError?,fields: [String:String],fieldOrder: [String]? = nil) {
        self.session = session; self.epoch = epoch; self.requestID = requestID; self.revision = revision; self.configuration = configuration; self.error = error; self.fields = fields; self.fieldOrder = fieldOrder
    }
    public static func == (lhs: Self,rhs: Self) -> Bool {
        lhs.session.utf8.elementsEqual(rhs.session.utf8) && lhs.epoch == rhs.epoch && lhs.requestID == rhs.requestID && lhs.revision == rhs.revision && lhs.configuration == rhs.configuration && lhs.error == rhs.error && lhs.fields.count == rhs.fields.count && lhs.fields.allSatisfy { key,value in
            rhs.fields.contains { $0.key.utf8.elementsEqual(key.utf8) && $0.value.utf8.elementsEqual(value.utf8) }
        }
    }
}
public enum ProtocolWire {
    private static func safe(_ b: UInt8) -> Bool { (48...57).contains(b) || (65...90).contains(b) || (97...122).contains(b) || [45,95,46,58].contains(b) }
    public static func escape(_ value: String) -> String { value.utf8.map { safe($0) ? String(UnicodeScalar($0)) : String(format: "%%%02X", $0) }.joined() }
    public static func unescape(_ value: String, maximum: Int) throws -> String {
        guard maximum >= 0, maximum > Int.max / 3 || value.utf8.count <= maximum * 3 else { throw ProtocolError.limit }
        var out: [UInt8] = []; let bytes = Array(value.utf8); var i = 0
        func nibble(_ b: UInt8) throws -> UInt8 {
            switch b { case 48...57: return b-48; case 65...70: return b-55; case 97...102: return b-87; default: throw ProtocolError.malformed }
        }
        while i < bytes.count {
            if bytes[i] == 37 { guard i+2 < bytes.count else { throw ProtocolError.malformed }; out.append(try nibble(bytes[i+1])*16+nibble(bytes[i+2])); i += 3 }
            else { guard safe(bytes[i]) else { throw ProtocolError.malformed }; out.append(bytes[i]); i += 1 }
            guard out.count <= maximum else { throw ProtocolError.limit }
        }
        guard String(bytes: out, encoding: .utf8) != nil else { throw ProtocolError.malformed }
        return String(decoding: out, as: UTF8.self)
    }
    public static func quantity(_ value: String) throws -> UInt64 {
        guard value.hasPrefix("u64:") else { throw ProtocolError.malformed }
        let digits = value.dropFirst(4)
        guard !digits.isEmpty, digits.utf8.allSatisfy({ (48...57).contains($0) }), let number = UInt64(digits) else { throw ProtocolError.malformed }; return number
    }
    static func inputBytes(_ value: String, maximum: Int) throws -> Data {
        guard value.hasPrefix("bytes:") else { throw ProtocolError.malformed }
        let encoded = value.dropFirst(6).utf8
        guard encoded.count % 2 == 0 else { throw ProtocolError.malformed }
        guard maximum >= 0, encoded.count / 2 <= maximum else { throw ProtocolError.limit }
        let chars = Array(encoded)
        func nibble(_ byte: UInt8) throws -> UInt8 {
            switch byte {
            case 48...57: return byte - 48
            case 65...70: return byte - 55
            case 97...102: return byte - 87
            default: throw ProtocolError.malformed
            }
        }
        var bytes = Data()
        for index in stride(from: 0, to: chars.count, by: 2) {
            bytes.append(try nibble(chars[index]) * 16 + nibble(chars[index + 1]))
        }
        return bytes
    }
    // Rust decodes its typed Command before parsing the envelope or admitting
    // its ID. Keep malformed syntax out of the deduplication/revision machinery.
    private static func commandArguments(_ command: String, _ a: [String], limits: ProtocolLimits) throws -> [String] {
        func exact(_ count: Int) throws { guard a.count == count else { throw ProtocolError.malformed } }
        func q(_ index: Int) throws -> String { "u64:\(try quantity(a[index]))" }
        func schema(_ index: Int) throws -> String {
            let value = try quantity(a[index]); guard value <= UInt64(UInt32.max) else { throw ProtocolError.malformed }
            return "u64:\(value)"
        }
        switch command {
        case "status", "snapshot", "cancel", "watch_status", "telemetry_health":
            try exact(0); return []
        case "step":
            try exact(1)
            return ["bytes:" + (try inputBytes(a[0], maximum: limits.maxInputBytes)).map { String(format: "%02x", $0) }.joined()]
        case "checks", "outputs", "select", "events", "unwatch", "watch_current":
            try exact(1); return [try q(0)]
        case "inputs", "metric_catalog", "probe_status":
            try exact(2); return try [q(0), q(1)]
        case "export_trace", "metric_window", "effect_details":
            try exact(3); return try [q(0), q(1), q(2)]
        case "metric_snapshot":
            try exact(3); return [a[0] == "none" ? "none" : try q(0), try q(1), try q(2)]
        case "effect_timeline":
            try exact(4); return try [q(0), q(1), q(2), q(3)]
        case "probe_events":
            try exact(3)
            let sink = try q(0), limit = try q(1)
            guard a[2] == "bool:true" || a[2] == "bool:false" else { throw ProtocolError.malformed }
            return [sink, limit, a[2]]
        case "probe_configure":
            guard !a.isEmpty else { throw ProtocolError.malformed }; return try a.indices.map(q)
        case "inspect":
            guard a.count >= 4 else { throw ProtocolError.malformed }
            return try [q(0), schema(1), q(2), q(3)] + decodedPath(Array(a.dropFirst(4)))
        case "watch":
            guard a.count >= 2 else { throw ProtocolError.malformed }
            return try [schema(0), q(1)] + decodedPath(Array(a.dropFirst(2)))
        default: throw ProtocolError.unsupported
        }
    }
    static func decodedPath(_ segments: [String]) throws -> [String] {
        guard segments.count <= 32 else { throw ProtocolError.limit }
        return try segments.map { segment in
            guard let colon = segment.firstIndex(of: ":") else { throw ProtocolError.malformed }
            let kind = String(segment[..<colon]), value = String(segment[segment.index(after: colon)...])
            switch kind {
            case "field", "variant", "key.string": return segment
            case "index":
                let digits = value.hasPrefix("+") ? String(value.dropFirst()) : value
                let number = try quantity("u64:" + digits); return "index:\(number)"
            case "key.bool":
                guard value == "true" || value == "false" else { throw ProtocolError.malformed }; return segment
            case "key.char":
                guard value.unicodeScalars.count == 1 else { throw ProtocolError.malformed }; return segment
            default:
                let unsignedBounds = ["key.u8":"255","key.u16":"65535","key.u32":"4294967295","key.u64":"18446744073709551615","key.usize":"18446744073709551615","key.u128":"340282366920938463463374607431768211455"]
                let signedBounds = ["key.i8":("127","128"),"key.i16":("32767","32768"),"key.i32":("2147483647","2147483648"),"key.i64":("9223372036854775807","9223372036854775808"),"key.isize":("9223372036854775807","9223372036854775808"),"key.i128":("170141183460469231731687303715884105727","170141183460469231731687303715884105728")]
                let negative = value.hasPrefix("-")
                let raw = value.hasPrefix("+") || negative ? String(value.dropFirst()) : value
                guard !raw.isEmpty, raw.utf8.allSatisfy({ (48...57).contains($0) }) else { throw ProtocolError.malformed }
                let digits = String(raw.drop(while: { $0 == "0" })), bound: String
                let canonical = digits.isEmpty ? "0" : digits
                if let unsigned = unsignedBounds[kind], !negative { bound = unsigned }
                else if let pair = signedBounds[kind] { bound = negative ? pair.1 : pair.0 }
                else { throw ProtocolError.malformed }
                guard canonical.count < bound.count || (canonical.count == bound.count && !bound.lexicographicallyPrecedes(canonical)) else { throw ProtocolError.malformed }
                return kind + ":" + (negative && canonical != "0" ? "-" : "") + canonical
            }
        }
    }
    private static func encode(_ fields: [String], limits: ProtocolLimits, maximum: Int) throws -> Data {
        guard limits.maxFrameBytes > 0, limits.maxFieldBytes > 0, fields.count <= maximum else { throw ProtocolError.limit }
        // Admit the escaped size before allocating the frame or individual fields.
        var size = 0
        for (index, field) in fields.enumerated() {
            guard field.utf8.count <= limits.maxFieldBytes else { throw ProtocolError.limit }
            if index != 0 { guard size < limits.maxFrameBytes else { throw ProtocolError.limit }; size += 1 }
            for byte in field.utf8 {
                let width = safe(byte) ? 1 : 3
                guard width <= limits.maxFrameBytes - size else { throw ProtocolError.limit }
                size += width
            }
        }
        var result = Data()
        result.reserveCapacity(size)
        let hex = Array("0123456789ABCDEF".utf8)
        for (index, field) in fields.enumerated() {
            if index != 0 { result.append(9) }
            for byte in field.utf8 {
                if safe(byte) { result.append(byte) }
                else { result.append(37); result.append(hex[Int(byte >> 4)]); result.append(hex[Int(byte & 15)]) }
            }
        }; return result
    }
    public static func encodeRequest(_ r: ProtocolRequest, limits: ProtocolLimits = ProtocolLimits()) throws -> Data {
        guard limits.maxRequestFields >= 9, r.arguments.count <= limits.maxRequestFields - 9,
              limits.maxFieldBytes >= 7, r.session.utf8.count <= limits.maxFieldBytes - 7 else { throw ProtocolError.limit }
        return try encode(["DDBG","u32:\(r.version)","request","string:\(r.session)","u64:\(r.epoch)","u64:\(r.requestID)","u64:\(r.expectedRevision)","u64:\(r.expectedConfiguration)",r.command]+r.arguments, limits: limits, maximum: limits.maxRequestFields)
    }
    public static func decodeRequest(_ data: Data, limits: ProtocolLimits = ProtocolLimits()) throws -> ProtocolRequest {
        guard data.count <= limits.maxFrameBytes else { throw ProtocolError.limit }
        guard String(data: data, encoding: .utf8) != nil else { throw ProtocolError.malformed }
        let raw = String(decoding: data, as: UTF8.self)
        let parts = raw.split(separator: "\t", omittingEmptySubsequences: false)
        var f: [String] = []
        for part in parts {
            guard f.count < limits.maxRequestFields else { throw ProtocolError.limit }
            f.append(try unescape(String(part), maximum: limits.maxFieldBytes))
        }
        guard f.count >= 9, f[0] == "DDBG", f[2] == "request", f[1].hasPrefix("u32:"), let version = UInt32(f[1].dropFirst(4)) else { throw ProtocolError.malformed }
        guard version == 1 else { throw ProtocolError.unsupportedVersion }
        let arguments = try commandArguments(f[8], Array(f.dropFirst(9)), limits: limits)
        guard f[3].hasPrefix("string:") else { throw ProtocolError.malformed }
        return ProtocolRequest(session: String(f[3].dropFirst(7)),epoch: try quantity(f[4]),requestID: try quantity(f[5]),expectedRevision: try quantity(f[6]),expectedConfiguration: try quantity(f[7]),command: f[8],arguments: arguments)
    }
    public static func encodeResponse(_ r: ProtocolResponse, limits: ProtocolLimits = ProtocolLimits()) throws -> Data {
        guard limits.maxResponseFields >= 0, limits.maxResponseFields <= (Int.max-11)/2, r.fields.count <= limits.maxResponseFields,
              limits.maxFieldBytes >= 7, r.session.utf8.count <= limits.maxFieldBytes - 7 else { throw ProtocolError.limit }
        var f = ["DDBG","u32:1","response","string:\(r.session)","u64:\(r.epoch)","u64:\(r.requestID)","u64:\(r.revision)","u64:\(r.configuration)",r.error == nil ? "ok" : "error",r.error?.rawValue ?? "none","u64:\(r.fields.count)"]
        let keys: [String]
        if let order = r.fieldOrder {
            guard order.count == r.fields.count,Set(order).count == order.count,order.allSatisfy({ r.fields[$0] != nil }) else { throw ProtocolError.malformed }
            keys = order
        } else { keys = r.fields.keys.sorted() }
        for key in keys { f += [key,r.fields[key]!] }
        return try encode(f,limits: limits,maximum: 11 + limits.maxResponseFields*2)
    }
    public static func decodeResponse(_ data: Data, limits: ProtocolLimits = ProtocolLimits()) throws -> ProtocolResponse {
        guard data.count <= limits.maxFrameBytes else { throw ProtocolError.limit }
        guard String(data:data,encoding:.utf8) != nil else { throw ProtocolError.malformed }
        let raw = String(decoding: data, as: UTF8.self)
        let parts = raw.split(separator:"\t",omittingEmptySubsequences:false)
        guard limits.maxResponseFields >= 0, limits.maxResponseFields <= (Int.max-11)/2, parts.count <= 11+limits.maxResponseFields*2 else { throw ProtocolError.limit }
        let f = try parts.map { try unescape(String($0),maximum:limits.maxFieldBytes) }
        guard f.count >= 11, f[0] == "DDBG", f[1] == "u32:1", f[2] == "response", f[3].hasPrefix("string:") else { throw ProtocolError.malformed }
        let count = try quantity(f[10]); guard count <= UInt64(limits.maxResponseFields), f.count == 11+Int(count)*2 else { throw ProtocolError.malformed }
        let error: ProtocolError?
        if f[8] == "ok", f[9] == "none" { error = nil }
        else if f[8] == "error", let value = ProtocolError(rawValue:f[9]) { error = value }
        else { throw ProtocolError.malformed }
        var fields: [String:String] = [:], order: [String] = []
        for index in 0..<Int(count) {
            let key = f[11+index*2]; guard fields[key] == nil else { throw ProtocolError.malformed }; fields[key] = f[12+index*2]; order.append(key)
        }
        return ProtocolResponse(session:String(f[3].dropFirst(7)),epoch:try quantity(f[4]),requestID:try quantity(f[5]),revision:try quantity(f[6]),configuration:try quantity(f[7]),error:error,fields:fields,fieldOrder:order)
    }
    public static func frame(_ payload: Data, maximum: Int = 65536) throws -> Data {
        guard !payload.isEmpty, payload.count <= maximum, payload.count <= Int(UInt32.max) else { throw ProtocolError.limit }
        let n = UInt32(payload.count); var result = Data([UInt8(n >> 24),UInt8((n >> 16)&255),UInt8((n >> 8)&255),UInt8(n&255)]); result.append(payload); return result
    }
}
/// Incremental framing. An invalid length poisons this reader; no partial payload is dispatched.
public final class ProtocolFrameReader {
    private var prefix: [UInt8] = [], body = Data(), length: Int?, failed = false
    public let maximum: Int
    public init(maximum: Int = 65536) throws { guard maximum > 0, maximum <= Int(UInt32.max) else { throw ProtocolError.limit }; self.maximum = maximum }
    public func receive(_ bytes: Data, dispatch: (Data) throws -> Void) throws {
        guard !failed else { throw ProtocolError.disconnected }
        do { for byte in bytes {
            if length == nil {
                prefix.append(byte)
                if prefix.count == 4 { let n = prefix.reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }; guard n > 0, UInt64(n) <= UInt64(maximum) else { throw ProtocolError.limit }; length = Int(n); prefix.removeAll(keepingCapacity: true) }
            } else {
                body.append(byte)
                if body.count == length { let complete = body; body = Data(); length = nil; try dispatch(complete) }
            }
        } } catch { failed = true; throw error }
    }
    public func finish() throws { guard !failed, prefix.isEmpty, body.isEmpty, length == nil else { throw ProtocolError.malformed } }
}
private final class ProtocolEpochs: @unchecked Sendable {
    static let shared = ProtocolEpochs(); private let lock = NSLock(); private var next: UInt64 = 1
    func take() throws -> UInt64 { lock.lock(); defer { lock.unlock() }; guard next < .max else { throw ProtocolError.exhausted }; defer { next += 1 }; return next }
}
/// Explicit typed host bridge. Callbacks are cooperative; they must enforce their
/// own projection/event/metric budgets and detached snapshot ownership.
/// Explicit, detached state projection for the built-in inspection command.
public typealias ProtocolInspectionProjection<S> = (S,SnapshotID,[PathSegment],PageRequest,InspectLimits) throws -> InspectResult
public enum ProtocolHostCommand {
    case exportTrace(handle: UInt64, offset: UInt64, limit: UInt64)
    case checks(UInt64), outputs(handle: UInt64, includeExact: Bool)
    case inspect(handle: UInt64, schema: UInt32, offset: UInt64, limit: UInt64, path: [String])
    case inputs(offset: UInt64, limit: UInt64), select(UInt64), events(UInt64)
    case watch(schema: UInt32, baseline: UInt64, path: [String]), unwatch(UInt64), watchStatus, watchCurrent(UInt64)
    case metricCatalog(offset: UInt64, limit: UInt64), metricSnapshot(handle: UInt64?, offset: UInt64, limit: UInt64), metricWindow(from: UInt64, to: UInt64, limit: UInt64)
    case effectDetails(run: UInt64, epoch: UInt64, serial: UInt64), effectTimeline(run: UInt64, epoch: UInt64, serial: UInt64, limit: UInt64)
    case telemetryHealth, probeStatus(offset: UInt64, limit: UInt64), probeConfigure(expectedCaptureRevision: UInt64, profiles: [UInt64]), probeEvents(sink: UInt64, limit: UInt64, includePayload: Bool)
}
/// Preparation must be passive. Commit runs once only after the complete reply
/// fits the wire limits; it must apply exactly the prepared operation.
public struct PreparedProtocolResponse {
    public let fields: [String:String]
    public let fieldOrder: [String]?
    public let commit: () throws -> Void
    public let fit: (([String:String], ([String:String]) -> Bool) throws -> [String:String])?
    public init(fields: [String:String],commit: @escaping () throws -> Void = {}) { self.fields = fields; self.fieldOrder = nil; self.commit = commit; self.fit = nil }
    public init(fields: [String:String],fit: @escaping ([String:String], ([String:String]) -> Bool) throws -> [String:String],commit: @escaping () throws -> Void = {}) { self.fields = fields; self.fieldOrder = nil; self.commit = commit; self.fit = fit }
    public init(orderedFields: [(String,String)],commit: @escaping () throws -> Void = {}) throws {
        var fields: [String:String] = [:], order: [String] = []
        for (key,value) in orderedFields { guard fields[key] == nil else { throw ProtocolError.malformed }; fields[key] = value; order.append(key) }
        self.fields = fields; self.fieldOrder = order; self.fit = nil; self.commit = commit
    }
    public init(orderedFields: [(String,String)],fit: @escaping ([String:String], ([String:String]) -> Bool) throws -> [String:String],commit: @escaping () throws -> Void = {}) throws {
        var fields: [String:String] = [:], order: [String] = []
        for (key,value) in orderedFields { guard fields[key] == nil else { throw ProtocolError.malformed }; fields[key] = value; order.append(key) }
        self.fields = fields; self.fieldOrder = order; self.fit = fit; self.commit = commit
    }
}
public struct ProtocolHostBindings {
    public let commands: Set<String>
    public let exposedProbeSinks: Set<UInt64>
    public let approvedProbeProfiles: Set<UInt64>
    public let prepare: (ProtocolHostCommand) throws -> PreparedProtocolResponse
    let sharedHandleCommands: Set<String>
    let prepareWithHandle: ((ProtocolHostCommand,UInt64?) throws -> PreparedProtocolResponse)?
    let reset: () -> Void
    public init(commands: Set<String>, exposedProbeSinks: Set<UInt64> = [], approvedProbeProfiles: Set<UInt64> = [], prepare: @escaping (ProtocolHostCommand) throws -> PreparedProtocolResponse) { self.commands = commands; self.exposedProbeSinks = exposedProbeSinks; self.approvedProbeProfiles = approvedProbeProfiles; self.prepare = prepare; self.sharedHandleCommands = []; self.prepareWithHandle = nil; self.reset = {} }
    init(commands: Set<String>,exposedProbeSinks: Set<UInt64> = [],approvedProbeProfiles: Set<UInt64> = [],sharedHandleCommands: Set<String>,reset: @escaping () -> Void,prepareWithHandle: @escaping (ProtocolHostCommand,UInt64?) throws -> PreparedProtocolResponse) {
        self.commands = commands; self.exposedProbeSinks = exposedProbeSinks; self.approvedProbeProfiles = approvedProbeProfiles; self.sharedHandleCommands = sharedHandleCommands; self.reset = reset; self.prepareWithHandle = prepareWithHandle; self.prepare = { try prepareWithHandle($0,nil) }
    }

}
private struct ProtocolStreamEvent {
    let epoch, sequence, revision, configuration: UInt64
    let kind: String
    let bytes: Int
}
/// Reference emission order for concrete simulation commands. Host-provided
/// response ordering remains explicit through PreparedProtocolResponse.
private func nativeResponseOrder(_ command: String,_ fields: [String:String]) -> [String]? {
    func indexed(_ prefix: String,_ suffixes: [String]) -> [String] {
        let marker = prefix + "."
        let indices = Set(fields.keys.compactMap { key -> Int? in
            guard key.hasPrefix(marker) else { return nil }
            let rest = key.dropFirst(marker.count)
            guard let dot = rest.firstIndex(of:".") else { return nil }
            return Int(rest[..<dot])
        })
        return indices.sorted().flatMap { index in suffixes.map { "\(prefix).\(index).\($0)" } }
    }
    let order: [String]
    switch command {
    case "status": order = ["mode","phase","sequence","environment","capability.input_delivery","capability.inspection","capability.watch_configuration","capability.exact_values","capability.live_control","capability.live_restore","capability.effects","capability.probe_queries","capability.probe_configuration","capability.trace_export","capability.effect_telemetry","capability.metric_queries","limits.telemetry_snapshot_bytes","limits.frame_bytes","limits.dedup_entries","limits.dedup_bytes","limits.page_size","events.dropped","stop"]
    case "snapshot": order = ["handle","snapshot.revision","snapshot.sequence"]
    case "step","select": order = ["delivered","sequence"]
    case "checks": order = ["checks.complete","checks.total","checks.first_failure.id","checks.first_failure.index","checks.first_failure.phase","checks.first_failure.sequence","checks.first_failure","checks.returned","checks.truncated"] + indexed("checks",["id","status"])
    case "outputs": order = ["outputs.total","disposition","outputs.provenance","outputs.payloads"] + indexed("outputs",["encoded"]) + ["outputs.truncated"]
    case "inputs": order = ["complete","next_offset"] + indexed("candidates",["token","encoded"]) + ["candidates.returned"]
    case "events": order = ["events.dropped","events.returned"] + indexed("events",["version","session","epoch","sequence","revision","configuration","kind"])
    case "export_trace": order = ["export.kind","export.redacted","export.snapshot_revision","export.snapshot_sequence","export.total_bytes","export.offset","export.returned_bytes","export.next_offset","export.complete","export.retained_steps","export.evicted_steps","export.capture_outcome","export.bytes"]
    case "cancel": order = []
    default: return nil
    }
    let present = order.filter { fields[$0] != nil }, selected = Set(present)
    return present + fields.keys.filter { !selected.contains($0) }.sorted()
}
/// Single-owner local simulation adapter. No listener, model loading, clocks or host effects.
public final class ProtocolAdapter<S,I,O> {
    public let session: DebugSession<S,I,O>
    public let limits: ProtocolLimits
    public let authority: ProtocolAuthority
    public private(set) var epoch: UInt64
    private var connected = true, busy = false
    private var highWater: UInt64 = 0, nextHandle: UInt64 = 0
    private var handles: [(UInt64,UInt64,UInt64)] = []
    private var cache: [(ProtocolRequest,ProtocolResponse,Int)] = []
    private var cacheBytes = 0
    public private(set) var configuration: UInt64 = 0
    public private(set) var captureRevision: UInt64 = 0
    private let host: ProtocolHostBindings?
    private var watches: ProtocolWatchAttachment<S>?
    private var responseFieldOrder: [String]?
    private var inspection: ProtocolInspectionProjection<S>?
    private var inspectionSchema: ((S) throws -> DisplaySchema)?
    private let decodeInput: ((Data) throws -> I)?
    private let encodeInput: ((I) throws -> Data)?
    private var candidates: [(UInt64,UInt64,Data,Int)] = []
    private var candidateBytes = 0
    private var nextCandidate: UInt64 = 0
    private var events: [ProtocolStreamEvent] = []
    private var eventBytes = 0
    private var eventSequence: UInt64 = 0, eventDrops: UInt64 = 0
    public init(session: DebugSession<S,I,O>, authority: ProtocolAuthority = ProtocolAuthority(), limits: ProtocolLimits = ProtocolLimits(), decodeInput: ((Data) throws -> I)? = nil, encodeInput: ((I) throws -> Data)? = nil, host: ProtocolHostBindings? = nil) throws {
        guard limits.maxFrameBytes > 0, limits.maxFieldBytes > 0, limits.maxRequestFields >= 9, limits.maxResponseFields > 0, limits.maxInputBytes >= 0, limits.maxCacheEntries > 0, limits.maxCacheBytes > 0, limits.maxHandles > 0, limits.maxCandidates > 0, limits.maxCandidateBytes > 0, limits.maxEventEntries > 0, limits.maxEventBytes >= 512, limits.maxPageSize > 0, limits.maxTelemetrySnapshotBytes > 0, limits.maxProbeProfiles >= 0 else { throw ProtocolError.limit }
        self.session = session; self.authority = authority; self.limits = limits; self.decodeInput = decodeInput; self.encodeInput = encodeInput; self.host = host; epoch = try ProtocolEpochs.shared.take()
    }
    public func disconnect() throws { guard !busy else { throw ProtocolError.busy }; busy = true; defer { busy = false }; try session.withProtocolCallback { host?.reset() }; connected = false; handles.removeAll(); candidates.removeAll(); candidateBytes = 0; cache.removeAll(); cacheBytes = 0 }
    public func reconnect() throws -> UInt64 {
        guard !busy else { throw ProtocolError.busy }; guard !connected else { throw ProtocolError.invalidInput }; epoch = try ProtocolEpochs.shared.take(); connected = true; highWater = 0; handles.removeAll(); candidates.removeAll(); candidateBytes = 0; cache.removeAll(); cacheBytes = 0; events.removeAll(); eventBytes = 0; eventSequence = 0; publish("reconnected"); return epoch
    }
    private func publish(_ kind: String) {
        guard eventSequence < UInt64.max else { if eventDrops < UInt64.max { eventDrops += 1 }; return }
        eventSequence += 1
        let (a,overflow1) = MemoryLayout<ProtocolStreamEvent>.stride.addingReportingOverflow(session.id.utf8.count)
        let (size,overflow2) = a.addingReportingOverflow(kind.utf8.count)
        guard !overflow1,!overflow2,events.count < limits.maxEventEntries,size <= limits.maxEventBytes-eventBytes else { if eventDrops < UInt64.max { eventDrops += 1 }; return }
        events.append(ProtocolStreamEvent(epoch:epoch,sequence:eventSequence,revision:session.revision,configuration:configuration,kind:kind,bytes:size)); eventBytes += size
    }
    public func validateHandle(_ handle: UInt64) throws {
        guard let h = handles.first(where: { $0.0 == handle }), h.1 == session.revision, h.2 == session.sequence else { throw ProtocolError.staleHandle }
    }
    /// Attach a dedicated, host-authorized watch registry to this simulation.
    public func enableWatches(_ registry: WatchRegistry, project: @escaping ProtocolWatchProjection<S>) throws {
        try attachWatches(registry,schema:nil,supportsPaging:false) { state,snapshot,path,page,limits in
            guard page.offset == 0,page.limit == min(100,limits.maxPageSize) else { throw WatchError.invalidConfig }
            return try project(state,snapshot,path,limits)
        }
    }
    public func enableWatches(_ registry: WatchRegistry,schema: @escaping (S) throws -> DisplaySchema,project: @escaping ProtocolWatchProjection<S>) throws {
        try attachWatches(registry,schema:schema,supportsPaging:false) { state,snapshot,path,page,limits in
            guard page.offset == 0,page.limit == min(100,limits.maxPageSize) else { throw WatchError.invalidConfig }
            return try project(state,snapshot,path,limits)
        }
    }
    public func enablePagedWatches(_ registry: WatchRegistry, project: @escaping ProtocolPagedWatchProjection<S>) throws {
        try attachWatches(registry,schema:nil,supportsPaging:true,project:project)
    }
    public func enablePagedWatches(_ registry: WatchRegistry,schema: @escaping (S) throws -> DisplaySchema,project: @escaping ProtocolPagedWatchProjection<S>) throws {
        try attachWatches(registry,schema:schema,supportsPaging:true,project:project)
    }
    private func attachWatches(_ registry: WatchRegistry,schema: ((S) throws -> DisplaySchema)?,supportsPaging: Bool,project: @escaping ProtocolPagedWatchProjection<S>) throws {
        guard !busy else { throw ProtocolError.busy }
        guard watches == nil, host?.commands.contains("watch") != true else { throw ProtocolError.invalidInput }
        busy = true; defer { busy = false }
        watches = ProtocolWatchAttachment(registry:registry,project:project,schema:schema,supportsPaging:supportsPaging)
        configuration = registry.captureRevision
        observeWatches()
    }
    public func enableInspection(project: @escaping ProtocolInspectionProjection<S>) throws {
        guard !busy else { throw ProtocolError.busy }
        guard inspection == nil, host?.commands.contains("inspect") != true else { throw ProtocolError.invalidInput }
        inspection = project
    }
    /// A separately supplied schema permits mismatch rejection before projection.
    /// Schema and projection each receive independently detached state.
    public func enableInspection(schema: @escaping (S) throws -> DisplaySchema,project: @escaping ProtocolInspectionProjection<S>) throws {
        try enableInspection(project:project)
        inspectionSchema = schema
    }
    private func observeWatches() {
        guard let watches else { return }
        let snapshot = SnapshotID(session:session.epoch,revision:session.revision,sequence:session.sequence)
        let failed = session.stop == .propertyFailure
        var context = WatchObservationContext(snapshot:snapshot,origin:session.watchOrigin)
        context.propertyFailure = failed
        if failed {
            let failures = session.watchFailingProperties
            context.failingProperties = failures.ids
            context.failingPropertiesComplete = failures.complete
        }
        do {
            let schema: (() throws -> DisplaySchema)? = watches.schema.map { declared in { try self.session.withInspectionState { try declared($0) } } }
            try watches.registry.observePaged(context:context,schema:schema) { path,page,limits in
                try session.withInspectionState { state in try watches.project(state,snapshot,path,page,limits) }
            }
        } catch { watches.registry.recordObservationGap(snapshot:snapshot,origin:session.watchOrigin) }
    }
    public func handle(_ original: ProtocolRequest) -> ProtocolResponse {
        var r = original
        func response(_ error: ProtocolError? = nil, _ fields: [String:String] = [:]) -> ProtocolResponse { ProtocolResponse(session: session.id,epoch: epoch,requestID: r.requestID,revision: session.revision,configuration: configuration,error: error,fields: fields) }
        if busy { return response(.busy) }; busy = true; defer { busy = false }
        if r.epoch == 0 && r.command == "status" { r.epoch = epoch }
        guard r.version == 1 else { return response(.unsupportedVersion) }
        guard r.session.utf8.elementsEqual(session.id.utf8) else { return response(.wrongSession) }
        guard r.epoch == epoch else { return response(.wrongEpoch) }
        guard connected else { return response(.disconnected) }
        guard r.requestID > 0 else { return response(.malformed) }
        let requestBytes: Int
        do { requestBytes = try ProtocolWire.encodeRequest(r,limits: limits).count } catch { return response(.limit) }
        if let cached = cache.first(where: { $0.0.requestID == r.requestID }) { return cached.0 == r ? cached.1 : response(.duplicateConflict) }
        guard r.requestID > highWater else { return response(.retiredRequest) }; highWater = r.requestID
        var result: ProtocolResponse
        responseFieldOrder = nil
        do {
            let fields = try execute(r)
            if responseFieldOrder == nil && host?.commands.contains(r.command) != true { responseFieldOrder = nativeResponseOrder(r.command,fields) }
            result = ProtocolResponse(session:session.id,epoch:epoch,requestID:r.requestID,revision:session.revision,configuration:configuration,error:nil,fields:fields,fieldOrder:responseFieldOrder)
        }
        catch let error as ProtocolError { result = response(error) }
        catch let error as DebugError {
            switch error { case .staleRevision: result = response(.staleRevision); case .ended: result = response(.ended); case .reentrant: result = response(.busy); case .unsupported: result = response(.unsupported); default: result = response(.invalidInput) }
        } catch { result = response(.callback) }
        if (try? ProtocolWire.encodeResponse(result,limits: limits)) == nil { result = response(.limit) }
        // An unencodable response cannot be cached. Preserve the request high
        // water mark without inventing an overflowing cache-size sentinel.
        guard let responseBytes = try? ProtocolWire.encodeResponse(result,limits:limits).count else { return result }
        let (payloadBytes,payloadOverflow) = requestBytes.addingReportingOverflow(responseBytes)
        let (size,sizeOverflow) = payloadBytes.addingReportingOverflow(256)
        guard !payloadOverflow,!sizeOverflow else { return result }
        while !cache.isEmpty && (cache.count >= limits.maxCacheEntries || size > limits.maxCacheBytes-cacheBytes) { cacheBytes -= cache.removeFirst().2 }
        if size <= limits.maxCacheBytes { cache.append((r,result,size)); cacheBytes += size }; return result
    }
    /// Explicit bounded blocking local stdio pump. No listener or authentication.
    /// Hosts own scheduling, pipe closure and cancellation of blocking OS I/O.
    @discardableResult public func serve(input: FileHandle, output: FileHandle, maxRequests: Int) throws -> Int {
        guard maxRequests > 0 else { throw ProtocolError.limit }
        defer { try? disconnect() }
        func read(_ count: Int, cleanEOF: Bool = false) throws -> Data? {
            var data = Data()
            while data.count < count {
                let chunk = try input.read(upToCount:count-data.count) ?? Data()
                if chunk.isEmpty { if cleanEOF && data.isEmpty { return nil }; throw ProtocolError.malformed }
                data.append(chunk)
            }; return data
        }
        var processed = 0
        for _ in 0..<maxRequests {
            guard let header = try read(4,cleanEOF:true) else { return processed }
            let n = header.reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
            guard n > 0, UInt64(n) <= UInt64(limits.maxFrameBytes) else { throw ProtocolError.limit }
            guard let payload = try read(Int(n)) else { throw ProtocolError.malformed }
            let result: ProtocolResponse
            do { result = handle(try ProtocolWire.decodeRequest(payload,limits:limits)) }
            catch { result = ProtocolResponse(session:session.id,epoch:epoch,requestID:0,revision:session.revision,configuration:configuration,error:(error as? ProtocolError) ?? .malformed,fields:[:]) }
            try output.write(contentsOf:ProtocolWire.frame(ProtocolWire.encodeResponse(result,limits:limits),maximum:limits.maxFrameBytes)); processed += 1
        }; return processed
    }
    private func execute(_ r: ProtocolRequest) throws -> [String:String] {
        switch r.command {
        case "status":
            guard r.arguments.isEmpty else { throw ProtocolError.malformed }
            return [
                "mode":"enum:simulation",
                "phase":"enum:\(session.phase.rawValue.prefix(1).uppercased()+session.phase.rawValue.dropFirst())",
                "sequence":"u64:\(session.sequence)",
                "environment":"string:\(session.inputPolicy)",
                "capability.input_delivery":"bool:\(authority.deliverInputs)",
                "capability.inspection":"bool:\(inspection != nil || host?.commands.contains("inspect") == true)",
                "capability.watch_configuration":"bool:\(authority.configureWatches && (watches != nil || host?.commands.contains("watch") == true))",
                "capability.exact_values":"bool:\(authority.readExactValues)",
                "capability.live_control":"bool:false",
                "capability.live_restore":"bool:false",
                "capability.effects":"bool:false",
                "capability.probe_queries":"bool:\(host?.commands.contains("probe_status") == true)",
                "capability.probe_configuration":"bool:\(authority.configureDiagnostics && host?.commands.contains("probe_status") == true)",
                "capability.trace_export":"bool:\(authority.exportExactTrace && session.capabilities.exactRecording)",
                "capability.effect_telemetry":"bool:\(host?.commands.contains("telemetry_health") == true)",
                "capability.metric_queries":"bool:\(host?.commands.contains("metric_catalog") == true)",
                "limits.telemetry_snapshot_bytes":"u64:\(limits.maxTelemetrySnapshotBytes)",
                "limits.frame_bytes":"u64:\(limits.maxFrameBytes)",
                "limits.dedup_entries":"u64:\(limits.maxCacheEntries)",
                "limits.dedup_bytes":"u64:\(limits.maxCacheBytes)",
                "limits.page_size":"u64:\(limits.maxPageSize)",
                "events.dropped":"u64:\(eventDrops)",
                "stop":"enum:\(stopCode)"
            ]
        case "snapshot":
            guard r.arguments.isEmpty else { throw ProtocolError.malformed }; try session.checkRevision(r.expectedRevision)
            guard nextHandle < .max else { throw ProtocolError.exhausted }; nextHandle += 1
            if handles.count == limits.maxHandles { handles.removeFirst() }; handles.append((nextHandle,session.revision,session.sequence))
            return ["handle":"u64:\(nextHandle)","snapshot.revision":"u64:\(session.revision)","snapshot.sequence":"u64:\(session.sequence)"]
        case "checks" where host?.commands.contains("checks") != true:
            guard r.arguments.count == 1 else { throw ProtocolError.malformed }
            try validateHandle(ProtocolWire.quantity(r.arguments[0]))
            let observation = try session.observation()
            let checks = observation?.checks ?? session.initialChecks
            let count = min(checks.count, limits.maxPageSize)
            var fields = ["checks.complete":"bool:\(session.checksComplete)","checks.total":"u64:\(checks.count)","checks.returned":"u64:\(count)","checks.truncated":"bool:\(count < checks.count)"]
            if let index = checks.firstIndex(where: { $0.status == .failed }) {
                fields["checks.first_failure.id"] = "string:\(checks[index].id)"
                fields["checks.first_failure.index"] = "u64:\(index)"
                fields["checks.first_failure.phase"] = "enum:" + (observation.map { index < $0.stateCheckCount ? "post_state" : "transition" } ?? "initial_state")
                fields["checks.first_failure.sequence"] = "u64:\(session.sequence)"
            } else { fields["checks.first_failure"] = "none" }
            for (index, check) in checks.prefix(count).enumerated() {
                fields["checks.\(index).id"] = "string:\(check.id)"
                fields["checks.\(index).status"] = "enum:\(check.status.rawValue)"
            }
            return fields
        case "outputs" where host?.commands.contains("outputs") != true:
            guard r.arguments.count == 1 else { throw ProtocolError.malformed }
            try validateHandle(ProtocolWire.quantity(r.arguments[0]))
            guard let observation = try session.observation() else { return ["outputs.total":"u64:0"] }
            let outputs = observation.transition.outputs, disposition: String
            switch observation.transition.disposition {
            case .accepted: disposition = "accepted"
            case .ignored: disposition = "ignored"
            case .rejected: disposition = "rejected"
            }
            var fields = ["outputs.total":"u64:\(outputs.count)","disposition":"enum:\(disposition)","outputs.provenance":"enum:simulated","outputs.payloads":authority.readExactValues ? "enum:exact_sensitive" : "enum:unavailable"]
            if authority.readExactValues {
                for (index, output) in outputs.prefix(limits.maxPageSize).enumerated() {
                    let bytes = try session.encodedOutput(output, maximum: limits.maxInputBytes)
                    fields["outputs.\(index).encoded"] = "bytes:" + bytes.map { String(format:"%02x", $0) }.joined()
                }
                fields["outputs.truncated"] = "bool:\(outputs.count > limits.maxPageSize)"
            }
            return fields
        case "export_trace" where host?.commands.contains("export_trace") != true:
            guard authority.exportExactTrace else { throw ProtocolError.unauthorized }
            try validateHandle(ProtocolWire.quantity(r.arguments[0])); try session.checkRevision(r.expectedRevision)
            let offset = try ProtocolWire.quantity(r.arguments[1]), count = try ProtocolWire.quantity(r.arguments[2])
            guard count > 0, count <= UInt64((limits.maxFieldBytes-6)/2), offset <= UInt64.max-count else { throw ProtocolError.limit }
            guard session.capabilities.exactRecording else { throw ProtocolError.unsupported }
            let window = try session.traceWindow(offset: offset, limit: Int(count))
            guard offset <= window.total else { throw ProtocolError.invalidInput }
            let next = offset + UInt64(window.bytes.count)
            return ["export.kind":"enum:exact_trace_sensitive","export.redacted":"bool:false","export.snapshot_revision":"u64:\(session.revision)","export.snapshot_sequence":"u64:\(session.sequence)","export.total_bytes":"u64:\(window.total)","export.offset":"u64:\(offset)","export.returned_bytes":"u64:\(window.bytes.count)","export.next_offset":next == window.total ? "none" : "u64:\(next)","export.complete":"bool:\(next == window.total)","export.retained_steps":"u64:\(window.retained)","export.evicted_steps":"u64:\(window.evicted)","export.capture_outcome":"enum:\(stopCode)","export.bytes":"bytes:" + window.bytes.map {String(format:"%02x",$0)}.joined()]
        case "step":
            guard r.arguments.count == 1 else { throw ProtocolError.malformed }
            guard authority.deliverInputs else { throw ProtocolError.unauthorized }; try session.checkRevision(r.expectedRevision)
            let data = try ProtocolWire.inputBytes(r.arguments[0], maximum: limits.maxInputBytes)
            let result = try session.stepEncoded(expectedRevision: r.expectedRevision, bytes: data, decodeInput: decodeInput)
            if result.delivered { observeWatches(); publish("transition") }
            return ["delivered":"bool:\(result.delivered)","sequence":"u64:\(result.sequence)"]
        case "inputs" where encodeInput != nil:
            guard r.arguments.count == 2 else { throw ProtocolError.malformed }; try session.checkRevision(r.expectedRevision)
            let offset = try ProtocolWire.quantity(r.arguments[0]), limit = try ProtocolWire.quantity(r.arguments[1])
            guard let o = Int(exactly:offset), let n = Int(exactly:limit), n > 0, n <= limits.maxPageSize, n <= limits.maxCandidates else { throw ProtocolError.limit }
            let page = try session.inputs(expectedRevision:r.expectedRevision,offset:o,limit:n)
            var fields = ["complete":"bool:\(page.complete)","next_offset":page.nextOffset.map {"u64:\($0)"} ?? "none"]
            var pending: [(UInt64,UInt64,Data,Int)] = []; var pendingBytes = 0
            for (index,token) in page.candidates.enumerated() {
                guard nextCandidate < .max else { throw ProtocolError.exhausted }
                let encoded = try session.encodedCandidate(token,encode:encodeInput!)
                guard encoded.count <= limits.maxInputBytes, encoded.count <= limits.maxCandidateBytes else { throw ProtocolError.limit }
                guard encoded.count+64 <= limits.maxCandidateBytes-pendingBytes else { throw ProtocolError.limit }; pendingBytes += encoded.count+64
                nextCandidate += 1; pending.append((nextCandidate,token.revision,encoded,encoded.count+64))
                fields["candidates.\(index).token"] = "u64:\(nextCandidate)"
                if authority.readExactValues { fields["candidates.\(index).encoded"] = "bytes:" + encoded.map {String(format:"%02x",$0)}.joined() }
            }
            fields["candidates.returned"] = "u64:\(pending.count)"
            _ = try ProtocolWire.encodeResponse(ProtocolResponse(session:session.id,epoch:epoch,requestID:r.requestID,revision:session.revision,configuration:configuration,error:nil,fields:fields),limits:limits)
            for item in pending {
                while !candidates.isEmpty && (candidates.count >= limits.maxCandidates || item.3 > limits.maxCandidateBytes-candidateBytes) { candidateBytes -= candidates.removeFirst().3 }
                candidates.append(item); candidateBytes += item.3
            }; return fields
        case "select" where encodeInput != nil:
            guard r.arguments.count == 1 else { throw ProtocolError.malformed }; guard authority.deliverInputs else { throw ProtocolError.unauthorized }; try session.checkRevision(r.expectedRevision)
            let id = try ProtocolWire.quantity(r.arguments[0]); guard let candidate = candidates.first(where: {$0.0 == id}), candidate.1 == session.revision else { throw ProtocolError.staleHandle }
            let result = try session.stepCanonicalCandidate(expectedRevision: r.expectedRevision, bytes: candidate.2, decodeInput: decodeInput); if result.delivered { observeWatches(); publish("transition") }; return ["delivered":"bool:\(result.delivered)","sequence":"u64:\(result.sequence)"]
        case "events" where host?.commands.contains("events") != true:
            guard r.arguments.count == 1 else { throw ProtocolError.malformed }
            let limit = try ProtocolWire.quantity(r.arguments[0])
            guard limit > 0,limit <= UInt64(limits.maxPageSize),let n = Int(exactly:limit) else { throw ProtocolError.limit }
            let count = min(n,events.count)
            var fields = ["events.dropped":"u64:\(eventDrops)","events.returned":"u64:\(count)"]
            for (i,event) in events.prefix(count).enumerated() {
                let key = "events.\(i)"
                fields[key+".version"] = "u64:1"; fields[key+".session"] = "string:"+session.id; fields[key+".epoch"] = "u64:\(event.epoch)"; fields[key+".sequence"] = "u64:\(event.sequence)"; fields[key+".revision"] = "u64:\(event.revision)"; fields[key+".configuration"] = "u64:\(event.configuration)"; fields[key+".kind"] = "enum:"+event.kind
            }
            let predicted = ProtocolResponse(session:session.id,epoch:epoch,requestID:r.requestID,revision:UInt64.max,configuration:UInt64.max,error:nil,fields:fields)
            _ = try ProtocolWire.encodeResponse(predicted,limits:limits)
            for event in events.prefix(count) { eventBytes -= event.bytes }
            events.removeFirst(count)
            return fields
        case "cancel":
            guard r.arguments.isEmpty else { throw ProtocolError.malformed }; guard authority.deliverInputs else { throw ProtocolError.unauthorized }; try session.cancel(expectedRevision: r.expectedRevision); publish("cancelled"); return [:]
        default: return try executeHost(r)
        }
    }
    private var stopCode: String {
        switch session.stop {
        case nil: return "none"
        case .preBreakpoint: return "pre_breakpoint"
        case .postBreakpoint: return "post_breakpoint"
        case .propertyFailure: return "property_failure"
        case .initialCheckError: return "initial_check_error"
        case .modelError: return "model_error"
        case .checkerError: return "checker_error"
        case .observerError: return "observer_error"
        case .recordingError: return "recording_error"
        case .budgetExhausted: return "budget_exhausted"
        case .cancelled: return "cancelled"
        }
    }
    private func executeHost(_ r: ProtocolRequest) throws -> [String:String] {
        let a = r.arguments
        func exact(_ n: Int) throws { guard a.count == n else { throw ProtocolError.malformed } }
        func q(_ i: Int) throws -> UInt64 { guard i < a.count else { throw ProtocolError.malformed }; return try ProtocolWire.quantity(a[i]) }
        func page(_ value: UInt64) throws -> UInt64 { guard value > 0, value <= UInt64(limits.maxPageSize) else { throw ProtocolError.limit }; return value }
        func schema(_ i: Int) throws -> UInt32 { let v = try q(i); guard let s = UInt32(exactly: v) else { throw ProtocolError.malformed }; return s }
        func path(_ start: Int) throws -> [String] {
            guard a.count >= start else { throw ProtocolError.limit }
            return try ProtocolWire.decodedPath(Array(a.dropFirst(start)))
        }
        let command: ProtocolHostCommand
        var changesConfiguration = false, changesCapture = false
        switch r.command {
        case "export_trace":
            try exact(3); guard authority.exportExactTrace else { throw ProtocolError.unauthorized }; try session.checkRevision(r.expectedRevision); let h = try q(0); try validateHandle(h); let offset = try q(1), limit = try q(2); guard limit > 0, limit <= UInt64(max(0,limits.maxFieldBytes-6)/2), !offset.addingReportingOverflow(limit).overflow else { throw ProtocolError.limit }; command = .exportTrace(handle:h,offset:offset,limit:limit)
        case "checks", "outputs":
            try exact(1); let h = try q(0); try validateHandle(h)
            if r.command == "outputs" { command = .outputs(handle:h,includeExact:authority.readExactValues) } else { command = .checks(h) }
        case "inspect":
            guard a.count >= 4 else { throw ProtocolError.malformed }; let h = try q(0); try validateHandle(h); try session.checkRevision(r.expectedRevision); command = .inspect(handle:h,schema:try schema(1),offset:try q(2),limit:try page(q(3)),path:try path(4))
        case "inputs": try exact(2); try session.checkRevision(r.expectedRevision); command = .inputs(offset:try q(0),limit:try page(q(1)))
        case "select": try exact(1); guard authority.deliverInputs else { throw ProtocolError.unauthorized }; try session.checkRevision(r.expectedRevision); command = .select(try q(0))
        case "events": try exact(1); command = .events(try page(q(0)))
        case "watch":
            guard a.count >= 2 else { throw ProtocolError.malformed }; guard authority.configureWatches else { throw ProtocolError.unauthorized }; guard r.expectedConfiguration == configuration else { throw ProtocolError.staleConfiguration }; try session.checkRevision(r.expectedRevision); try session.checkRevision(q(1)); command = .watch(schema:try schema(0),baseline:try q(1),path:try path(2)); changesConfiguration = true
        case "unwatch":
            try exact(1); guard authority.configureWatches else { throw ProtocolError.unauthorized }; guard r.expectedConfiguration == configuration else { throw ProtocolError.staleConfiguration }; command = .unwatch(try q(0)); changesConfiguration = true
        case "watch_status": try exact(0); command = .watchStatus
        case "watch_current": try exact(1); command = .watchCurrent(try q(0))
        case "metric_catalog": try exact(2); command = .metricCatalog(offset:try q(0),limit:try page(q(1)))
        case "metric_snapshot": try exact(3); command = .metricSnapshot(handle:a[0] == "none" ? nil : try q(0),offset:try q(1),limit:try page(q(2)))
        case "metric_window": try exact(3); command = .metricWindow(from:try q(0),to:try q(1),limit:try page(q(2)))
        case "effect_details": try exact(3); command = .effectDetails(run:try q(0),epoch:try q(1),serial:try q(2))
        case "effect_timeline": try exact(4); command = .effectTimeline(run:try q(0),epoch:try q(1),serial:try q(2),limit:try page(q(3)))
        case "telemetry_health": try exact(0); command = .telemetryHealth
        case "probe_status": try exact(2); command = .probeStatus(offset:try q(0),limit:try page(q(1)))
        case "probe_configure":
            guard !a.isEmpty else { throw ProtocolError.malformed }; guard authority.configureDiagnostics else { throw ProtocolError.unauthorized }; let expected = try q(0); guard expected == captureRevision else { throw ProtocolError.staleConfiguration }; let profiles = try (1..<a.count).map(q); guard profiles.count <= limits.maxProbeProfiles, Set(profiles).count == profiles.count else { throw ProtocolError.invalidInput }; guard profiles.allSatisfy({host?.approvedProbeProfiles.contains($0) == true}) else { throw ProtocolError.unauthorized }; command = .probeConfigure(expectedCaptureRevision:expected,profiles:profiles); changesCapture = true
        case "probe_events":
            try exact(3); guard a[2] == "bool:true" || a[2] == "bool:false" else { throw ProtocolError.malformed }; let payload = a[2] == "bool:true", sink = try q(0); guard host?.exposedProbeSinks.contains(sink) == true else { throw ProtocolError.unauthorized }; command = .probeEvents(sink:sink,limit:try page(q(1)),includePayload:payload)
        default: throw ProtocolError.unsupported
        }
        let isWatch = ["watch","unwatch","watch_status","watch_current"].contains(r.command)
        guard !changesConfiguration || configuration < .max, !changesCapture || captureRevision < .max else { throw ProtocolError.exhausted }
        let prepared: PreparedProtocolResponse
        var reservedHandle: UInt64?
        if case let .inspect(_,schema,offset,limit,path) = command, let inspection {
            guard let pageOffset = Int(exactly:offset),let pageLimit = Int(exactly:limit) else { throw ProtocolError.limit }
            let segments = try watchPath(path)
            let snapshot = SnapshotID(session:session.epoch,revision:session.revision,sequence:session.sequence)
            var inspectLimits = InspectLimits(); inspectLimits.maxDepth = 16; inspectLimits.maxNodes = 128
            inspectLimits.maxBytes = limits.maxFrameBytes / 8; inspectLimits.maxValueBytes = limits.maxFieldBytes / 4; inspectLimits.maxPageSize = limits.maxPageSize
            let result: InspectResult
            var preflightSchema: DisplaySchema?
            do {
                if let inspectionSchema {
                    let declared = try session.withInspectionState { state in try inspectionSchema(state) }
                    guard declared.version == schema else { throw InspectError.schemaMismatch }
                    preflightSchema = declared
                }
                result = try session.withInspectionState { state in try inspection(state,snapshot,segments,PageRequest(offset:pageOffset,limit:pageLimit),inspectLimits) }
                guard result.snapshot == snapshot,result.schema.version == schema,result.path == segments else { throw InspectError.invalidMetadata }
                guard preflightSchema == nil || preflightSchema == result.schema else { throw InspectError.invalidMetadata }
                try validateProjection(result.node,limits:inspectLimits)
            } catch is InspectError { throw ProtocolError.invalidInput }
            catch let error as ProtocolError { throw error }
            catch let error as DebugError { if case .reentrant = error { throw ProtocolError.busy }; throw ProtocolError.callback }
            catch { throw ProtocolError.callback }
            guard result.schema.name.utf8.count <= limits.maxFieldBytes - 7 else { throw ProtocolError.limit }
            var fields = ["schema.name":"string:"+result.schema.name,"schema.version":"u64:\(result.schema.version)","snapshot.revision":"u64:\(snapshot.revision)","snapshot.sequence":"u64:\(snapshot.sequence)"]
            try probeNode(result.node,prefix:"node",depth:0,fields:&fields)
            let order = ["schema.name","schema.version","snapshot.revision","snapshot.sequence"] + probeNodeOrder(result.node,prefix:"node")
            prepared = try PreparedProtocolResponse(orderedFields:order.map { ($0,fields[$0]!) })
        } else if isWatch, let watches {
            guard watches.registry.captureRevision == configuration else { throw ProtocolError.staleConfiguration }
            prepared = try prepareWatch(watches,session:session,command:command,limits:limits)
        } else {
            guard let host,host.commands.contains(r.command) else { throw ProtocolError.unsupported }
            if host.sharedHandleCommands.contains(r.command) {
                switch command {
                case .metricSnapshot(handle:nil,offset:_,limit:_), .metricWindow:
                    guard nextHandle < .max else { throw ProtocolError.exhausted }; reservedHandle = nextHandle+1
                default: break
                }
            }
            prepared = try session.withProtocolCallback {
                if let prepare = host.prepareWithHandle { return try prepare(command,reservedHandle) }
                return try host.prepare(command)
            }
        }
        func fits(_ fields: [String:String]) -> Bool {
            let predicted = ProtocolResponse(session:session.id,epoch:epoch,requestID:r.requestID,revision:UInt64.max,configuration:configuration + (changesConfiguration ? 1 : 0),error:nil,fields:fields)
            return (try? ProtocolWire.encodeResponse(predicted,limits:limits)) != nil
        }
        let fields = try session.withProtocolCallback { try prepared.fit?(prepared.fields,fits) ?? prepared.fields }
        guard fits(fields) else { throw ProtocolError.limit }
        try session.withProtocolCallback { try prepared.commit() }
        if let order = prepared.fieldOrder {
            let retained = order.filter { fields[$0] != nil }, known = Set(retained)
            responseFieldOrder = retained + fields.keys.filter { !known.contains($0) }.sorted()
        }
        if let reservedHandle { nextHandle = reservedHandle }
        if changesConfiguration { configuration += 1; publish(r.command == "watch" ? "watch_configured" : "watch_removed") }; if changesCapture { captureRevision += 1 }
        return fields
    }

}

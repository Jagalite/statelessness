import Foundation
import Statelessness

// Public installed-package APIs only. Expectations are read, never synthesized
// from the implementation, and no Rust/ABI/executable oracle is involved.
private struct Fixture: Decodable {
    let schema: String
    let cases: [FixtureCase]
}
private struct FixtureCase: Decodable {
    let id: String
    let stream: String
    let options: FixtureOptions
    let payloads: FixturePayloads
    let events: [FixtureEvent]
    let sink: FixtureSink?
    let binding: String?
    let expected: Expected
}
private struct FixtureOptions: Decodable {
    var level: String?
    var snapshots: FixtureSnapshots?
    var maxRecordBytes: Int?
    enum CodingKeys: String, CodingKey { case level, snapshots; case maxRecordBytes = "max_record_bytes" }
}
private enum FixtureSnapshots: Decodable {
    case none, everyTransition, every(String)
    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let text = try? container.decode(String.self) {
            switch text {
            case "None": self = .none
            case "EveryTransition": self = .everyTransition
            default: throw ModelError("unknown snapshots \(text)")
            }
        } else {
            let values = try container.decode([String:String].self)
            guard let period = values["every"] else { throw ModelError("missing snapshot period") }
            self = .every(period)
        }
    }
    func policy() throws -> SnapshotPolicy {
        switch self {
        case .none: return .none
        case .everyTransition: return .everyTransition
        case let .every(period): return try .every(u64(period))
        }
    }
}
private struct FixturePayloads: Decodable {
    let kind: String
    var maxBlobBytes: Int?
    var text: String?
    enum CodingKeys: String, CodingKey { case kind, text; case maxBlobBytes = "max_blob_bytes" }
}
private struct FixtureSink: Decodable {
    var failAfterBytes: Int?
    var failFlush: Bool?
    enum CodingKeys: String, CodingKey { case failAfterBytes = "fail_after_bytes"; case failFlush = "fail_flush" }
}
private struct FixtureCheck: Decodable {
    let id: String
    let status: String
    var message: String?
    func check() throws -> Check {
        switch status {
        case "Passed": return .passed(id)
        case "Failed": return .failed(id, message ?? "")
        case "Skipped": return .skipped(id, message ?? "")
        default: throw ModelError("unknown check status \(status)")
        }
    }
}
private struct FixtureDisposition: Decodable {
    let kind: String
    var reason: String?
    func disposition() throws -> Disposition {
        switch kind {
        case "Accepted": return .accepted
        case "Rejected": return .rejected(reason ?? "")
        case "Ignored": return .ignored(reason ?? "")
        default: throw ModelError("unknown disposition \(kind)")
        }
    }
}
private struct FixtureMetadata: Decodable {
    let name: String
    let modelVersion: UInt32
    let propertiesVersion: UInt32
    let codecVersion: UInt32
    let build: String
    enum CodingKeys: String, CodingKey {
        case name, build
        case modelVersion = "model_version", propertiesVersion = "properties_version", codecVersion = "codec_version"
    }
    func metadata() -> Metadata {
        Metadata(name: name, modelVersion: modelVersion, propertiesVersion: propertiesVersion, codecVersion: codecVersion, build: build)
    }
}
private struct FixtureConfig: Decodable {
    let strategy: String
    var seed: String?
    let parameters: [[String]]
    func config() throws -> RunConfig {
        RunConfig(strategy: strategy, seed: try seed.map(u64), parameters: try parameters.map {
            guard $0.count == 2 else { throw ModelError("invalid parameter pair") }
            return AuditParameter($0[0], $0[1])
        })
    }
}
private struct FixtureEvent: Decodable {
    let kind: String
    var sequence: String?
    var before: UInt8?
    var input: UInt8?
    var state: UInt8?
    var outputs: [UInt8]?
    var disposition: FixtureDisposition?
    var checks: [FixtureCheck]?
    var level: String?
    var snapshots: FixtureSnapshots?
    var message: String?
    var transitions: String?
    var reason: String?
    var metadata: FixtureMetadata?
    var config: FixtureConfig?
    var fault: String?
    func event() throws -> Event<UInt8,UInt8,UInt8> {
        switch kind {
        case "transition":
            return .transition(sequence: try ObservationSequence(u64(required(sequence, "sequence"))),
                before: try required(before, "before"), input: try required(input, "input"),
                transition: Transition(state: try required(state, "state"), outputs: outputs ?? [], disposition: try required(disposition, "disposition").disposition()),
                checks: try (checks ?? []).map { try $0.check() })
        case "checkpoint": return .checkpoint(sequence: try u64(required(sequence, "sequence")), state: try required(state, "state"), checks: try (checks ?? []).map { try $0.check() })
        case "message": return .message(level: try logLevel(required(level, "level")), sequence: try sequence.map(u64), message: try required(message, "message"))
        case "finished": return .finished(transitions: try u64(required(transitions, "transitions")), reason: try required(reason, "reason"))
        case "started": return .started(metadata: try required(metadata, "metadata").metadata(), config: try required(config, "config").config())
        default: throw ModelError("not a diagnostic event: \(kind)")
        }
    }
}
private struct Expected: Codable, Equatable {
    let recordHex: String
    let callbacks: [String]
    let errorStage: String?
    let sinkPoisoned: Bool
    enum CodingKeys: String, CodingKey {
        case recordHex = "record_hex", callbacks, errorStage = "error_stage", sinkPoisoned = "sink_poisoned"
    }
    func encode(to encoder: Encoder) throws {
        var out = encoder.container(keyedBy: CodingKeys.self)
        try out.encode(recordHex, forKey: .recordHex); try out.encode(callbacks, forKey: .callbacks)
        if let errorStage { try out.encode(errorStage, forKey: .errorStage) } else { try out.encodeNil(forKey: .errorStage) }
        try out.encode(sinkPoisoned, forKey: .sinkPoisoned)
    }
}
private struct ResultCase: Encodable {
    let id: String
    let result: Expected
    private enum CodingKeys: String, CodingKey { case id }
    func encode(to encoder: Encoder) throws {
        var out = encoder.container(keyedBy: CodingKeys.self); try out.encode(id, forKey: .id)
        try result.encode(to: encoder)
    }
}
private struct Results: Encodable {
    let schema = "statelessness.observation-results.v1"
    let language = "swift"
    let cases: [ResultCase]
}
private func required<T>(_ value: T?, _ name: String) throws -> T {
    guard let value else { throw ModelError("missing \(name)") }; return value
}
private func u64(_ value: String) throws -> UInt64 {
    guard !value.isEmpty, value.utf8.allSatisfy({ $0 >= 48 && $0 <= 57 }), let result = UInt64(value) else { throw ModelError("invalid u64 \(value)") }
    return result
}
private func logLevel(_ value: String) throws -> LogLevel {
    switch value {
    case "Off": return .off
    case "Error": return .error
    case "Warn": return .warn
    case "Info": return .info
    case "Debug": return .debug
    case "Trace": return .trace
    default: throw ModelError("unknown level \(value)")
    }
}
private final class Context {
    var callbacks: [String] = []
    var fault = ""
    var forbiddenCalls = 0
    func forbidden<T>() throws -> T { forbiddenCalls += 1; throw ModelError("diagnostics executed model work") }
    func encode(_ kind: String, _ value: UInt8, _ sink: EncodeBuffer) throws {
        let name = "encode_\(kind)"; callbacks.append("\(name):\(value)")
        if fault == name { throw ModelError("injected codec failure") }
        try sink.extend([value])
    }
    func model(binding: String?) throws -> Model<UInt8,UInt8,UInt8> {
        func direct(_ kind: String, _ value: UInt8, _ sink: EncodeBuffer) throws {
            // A declaration case must require the binder below. Removing that
            // assignment leaves failing hooks, never an equivalent byte path.
            guard binding == nil else {
                self.forbiddenCalls += 1
                throw ModelError("declaration codec hook bypassed binding")
            }
            try self.encode(kind, value, sink)
        }
        var model = Model<UInt8,UInt8,UInt8>(initialState: { try self.forbidden() }, step: { _,_ in try self.forbidden() }, checkState: { _ in try self.forbidden() },
              cloneState: { _ in try self.forbidden() }, cloneInput: { _ in try self.forbidden() }, cloneOutput: { _ in try self.forbidden() },
              checkTransition: { _,_,_ in try self.forbidden() },
              codec: Codec(metadata: { try self.forbidden() }, encodeState: { _ in try self.forbidden() }, decodeState: { _ in try self.forbidden() },
                           encodeInput: { _ in try self.forbidden() }, decodeInput: { _ in try self.forbidden() }, encodeOutput: { _ in try self.forbidden() },
                           encodeStateInto: { try direct("state", $0, $1) }, encodeInputInto: { try direct("input", $0, $1) }, encodeOutputInto: { try direct("output", $0, $1) }))
        if let binding {
            guard binding == "declaration" else { throw ModelError("unknown codec binding \(binding)") }
            func declared(_ kind: String) throws -> TraceValueCodec<UInt8> {
                let byte = TraceValueCodec<UInt8>(schema: "observation.byte.\(kind)", encode: { try self.encode(kind, $0, $1) }, decode: { _ in try self.forbidden() })
                return try TraceRecords.codec(schema: "Observation\(kind)", fields: TraceFields.first(TraceField<UInt8,UInt8>.ordinary("value", byte, get: { $0 })), construct: { $0 }, require: .both)
            }
            let output = TraceValueCodec<UInt8>(schema: "observation.output", encodeOnly: { try self.encode("output", $0, $1) })
            model.codec = try ModelCodecBinding(metadata: { Metadata(name: "observation-binding", build: "source-derived") }, state: declared("state"), input: declared("input"), output: output).codec()
        }
        return model
    }
}
private struct CustomPayloads: PayloadFormat {
    typealias State = UInt8
    typealias Input = UInt8
    typealias Output = UInt8
    let context: Context
    let text: String
    func render(_ kind: String, _ value: UInt8, _ out: ObservationBuffer) throws {
        let name = "format_\(kind)"; context.callbacks.append("\(name):\(value)")
        if context.fault == name { throw ModelError("injected formatter failure") }
        if context.fault == "swallow_input" && kind == "input" {
            try? out.write([UInt8](repeating: 120, count: 1024)); return
        }
        try out.write(text)
    }
    func state(_ model: Model<UInt8,UInt8,UInt8>, _ state: UInt8, into out: ObservationBuffer) throws { try render("state", state, out) }
    func input(_ model: Model<UInt8,UInt8,UInt8>, _ input: UInt8, into out: ObservationBuffer) throws { try render("input", input, out) }
    func output(_ model: Model<UInt8,UInt8,UInt8>, _ output: UInt8, into out: ObservationBuffer) throws { try render("output", output, out) }
}
private struct Sink: ObservationWriter {
    var bytes: [UInt8] = []
    var writes = 0
    var flushes = 0
    let options: FixtureSink?
    mutating func write(_ bytes: ArraySlice<UInt8>) throws -> Int {
        writes += 1
        if let limit = options?.failAfterBytes {
            guard self.bytes.count < limit else { throw ModelError("injected sink failure") }
            let count = min(bytes.count, limit - self.bytes.count)
            self.bytes.append(contentsOf: bytes.prefix(count)); return count
        }
        self.bytes.append(contentsOf: bytes); return bytes.count
    }
    mutating func flush() throws {
        flushes += 1
        if options?.failFlush == true { throw ModelError("injected flush failure") }
    }
}
private func normalized(_ error: any Error) throws -> String {
    guard let error = error as? TextObservationError else { throw error }
    switch error.stage {
    case .record: return "record_limit"
    case .formatInput: return "format_input"
    case .formatOutput: return "format_output"
    case .formatState: return "format_state"
    case .encodeInput: return "encode_input"
    case .encodeOutput: return "encode_output"
    case .encodeState: return "encode_state"
    case .write: return "sink_write"
    case .flush: return "sink_flush"
    case .poisoned: return "sink_poisoned"
    case .consumed, .reentrant: throw error
    }
}
private func observe<F: PayloadFormat>(_ row: FixtureCase, context: Context, payloads: F) throws -> Expected where F.State == UInt8, F.Input == UInt8, F.Output == UInt8 {
    let options = ObservationOptions(level: try logLevel(row.options.level ?? "Off"), snapshots: try row.options.snapshots?.policy() ?? .none,
                                     maxRecordBytes: row.options.maxRecordBytes ?? 1_048_576)
    let log = try TextObserver(writer: Sink(options: row.sink), streamID: row.stream, options: options, payloads: payloads)
    let model = try context.model(binding: row.binding)
    var firstError: String?
    for event in row.events {
        context.fault = event.fault ?? ""
        do {
            switch event.kind {
            case "set_level": log.setLevel(try logLevel(required(event.level, "level")))
            case "set_snapshots": log.setSnapshots(try required(event.snapshots, "snapshots").policy())
            case "flush": try log.flush()
            default: try log.observe(model, event: event.event())
            }
        } catch {
            let stage = try normalized(error)
            if firstError == nil { firstError = stage }
        }
    }
    let beforeProbe = try log.getRef()
    let callbacksBeforeProbe = context.callbacks
    log.setLevel(.off)
    var poisoned = false
    do { try log.observe(model, event: .message(level: .off, sequence: nil, message: "health probe")) }
    catch {
        guard try normalized(error) == "sink_poisoned" else { throw error }
        poisoned = true
    }
    guard context.forbiddenCalls == 0 else { throw ModelError("model work executed during diagnostics") }
    guard poisoned == log.isPoisoned else { throw ModelError("health probe disagrees") }
    let writer = try log.intoInner() // Intentionally no implicit flush.
    guard writer.writes == beforeProbe.writes, writer.flushes == beforeProbe.flushes,
          writer.bytes == beforeProbe.bytes, context.callbacks == callbacksBeforeProbe else {
        throw ModelError("Off probe or writer extraction performed I/O or formatting")
    }
    return Expected(recordHex: writer.bytes.map { String(format: "%02x", $0) }.joined(), callbacks: context.callbacks, errorStage: firstError, sinkPoisoned: poisoned)
}
private func run(_ arguments: [String]) throws {
    let initialDebug: DebugObservation<Int,Int,String> = .initial(state: 7, checks: [])
    let turnDebug: DebugObservation<Int,Int,String> = .turn(TurnObservation(sequence: UInt64.max, before: 7, input: 1, transition: Transition(state: 8), checks: [], stateCheckCount: 0))
    guard case .initial(let state, _) = initialDebug, state == 7,
          case .turn(let view) = turnDebug, view.sequence == UInt64.max else { throw ModelError("debug variants lost payload") }

    guard arguments.count == 3, ["observe", "self-test"].contains(arguments[1]) else { throw ModelError("usage: ObservationConsumer observe|self-test fixturePath") }
    let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: URL(fileURLWithPath: arguments[2])))
    guard fixture.schema == "statelessness.observation.v1", !fixture.cases.isEmpty else { throw ModelError("empty or unsupported observation fixture") }
    var results: [ResultCase] = []
    for row in fixture.cases {
        let context = Context(), result: Expected
        switch row.payloads.kind {
        case "none": result = try observe(row, context: context, payloads: NoPayloads<UInt8,UInt8,UInt8>())
        case "encoded": result = try observe(row, context: context, payloads: EncodedPayloads<UInt8,UInt8,UInt8>(maxBlobBytes: row.payloads.maxBlobBytes ?? 16))
        case "custom": result = try observe(row, context: context, payloads: CustomPayloads(context: context, text: row.payloads.text ?? ""))
        default: throw ModelError("unknown payload kind \(row.payloads.kind)")
        }
        if arguments[1] == "self-test", result != row.expected { throw ModelError("observation mismatch: \(row.id)") }
        results.append(ResultCase(id: row.id, result: result))
    }
    let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
    let data = try encoder.encode(Results(cases: results))
    FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data([10]))
}
do { try run(CommandLine.arguments) }
catch {
    FileHandle.standardError.write(Data("\(error)\n".utf8))
    exit(1)
}

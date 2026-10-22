/// Increasing diagnostic verbosity. Rejected and ignored turns remain ordinary
/// debug events unless their supplied checks fail or were skipped.
public enum LogLevel: Int, CaseIterable, Comparable, Sendable {
    case off, error, warn, info, debug, trace
    public static func < (lhs: Self, rhs: Self) -> Bool { lhs.rawValue < rhs.rawValue }
    fileprivate var text: String {
        switch self {
        case .off: return "Off"
        case .error: return "Error"
        case .warn: return "Warn"
        case .info: return "Info"
        case .debug: return "Debug"
        case .trace: return "Trace"
        }
    }
}

/// The full positive UInt64 range, validated before an event is constructed.
public struct ObservationSequence: Equatable, Sendable {
    public let value: UInt64
    public init(_ value: UInt64) throws {
        guard value > 0 else { throw ConfigError("observation sequence must be positive") }
        self.value = value
    }
}

public struct SnapshotPolicy: Equatable, Sendable {
    private let period: UInt64?
    private let allTransitions: Bool
    private init(period: UInt64?, allTransitions: Bool = false) { self.period = period; self.allTransitions = allTransitions }
    public static let none = Self(period: nil)
    public static let everyTransition = Self(period: 1, allTransitions: true)
    public static func every(_ period: UInt64) throws -> Self {
        guard period > 0 else { throw ConfigError("snapshot period must be positive") }
        return Self(period: period)
    }
    fileprivate func includes(_ sequence: UInt64) -> Bool {
        guard let period else { return false }
        return sequence % period == 0
    }
}

public struct ObservationOptions: Sendable {
    public var level: LogLevel
    public var snapshots: SnapshotPolicy
    /// UTF-8 bytes per complete record, including its newline. Callback memory,
    /// legacy codec allocations and backing capacities are outside this bound.
    public let maxRecordBytes: Int
    public init(level: LogLevel = .off, snapshots: SnapshotPolicy = .none,
                maxRecordBytes: Int = 1024 * 1024) {
        self.level = level; self.snapshots = snapshots; self.maxRecordBytes = maxRecordBytes
    }
}

/// Application-owned synchronous diagnostics. Struct/array values use ordinary
/// Swift value semantics; reference payloads are borrowed by convention, never
/// cloned. Keep them stable throughout observe. Events confer neither continuity
/// nor checked-token authority, and may describe rejected/ignored actual turns.
public enum Event<State, Input, Output> {
    case started(metadata: Metadata, config: RunConfig)
    case checkpoint(sequence: UInt64, state: State, checks: [Check])
    case transition(sequence: ObservationSequence, before: State, input: Input,
                    transition: Transition<State, Output>, checks: [Check])
    case message(level: LogLevel, sequence: UInt64?, message: String)
    case finished(transitions: UInt64, reason: String)
}

public protocol Observer {
    associatedtype State
    associatedtype Input
    associatedtype Output
    mutating func observe(_ model: Model<State, Input, Output>, event: Event<State, Input, Output>) throws
}

/// A synchronous writer accepts a prefix and returns its exact byte count.
/// Zero progress or a count outside 1...bytes.count is a permanent sink failure.
/// Throw ObservationWriteError.interrupted only when no bytes were accepted.
public protocol ObservationWriter {
    mutating func write(_ bytes: ArraySlice<UInt8>) throws -> Int
    mutating func flush() throws
}
public enum ObservationWriteError: Error { case interrupted }

/// A simple owning writer; useful for in-memory diagnostics and custom adapters.
public struct ObservationBytes: ObservationWriter {
    public private(set) var bytes: [UInt8] = []
    public init() {}
    public mutating func write(_ bytes: ArraySlice<UInt8>) throws -> Int {
        self.bytes.append(contentsOf: bytes); return bytes.count
    }
    public func flush() throws {}
}

// Retained by the error, so identity cannot be recycled while an old error lives.
// Each formatter lease gets its own identity, including within the same record.
fileprivate final class ObservationErrorOrigin: Sendable {}

public struct TextObservationError: Error, CustomStringConvertible, Sendable {
    public enum Stage: String, Sendable {
        case record, formatState = "format state", formatInput = "format input", formatOutput = "format output"
        case encodeState = "encode state", encodeInput = "encode input", encodeOutput = "encode output"
        case write, flush, poisoned, consumed, reentrant
    }
    public let stage: Stage
    public let detail: String
    fileprivate let origin: ObservationErrorOrigin?
    public var description: String { "\(stage.rawValue): \(detail)" }
    public init(stage: Stage, detail: String) { self.stage = stage; self.detail = detail; origin = nil }
    fileprivate init(stage: Stage, detail: String, origin: ObservationErrorOrigin) {
        self.stage = stage; self.detail = detail; self.origin = origin
    }
}

fileprivate final class ObservationRecordStorage {
    var bytes: [UInt8] = []
    let maximum: Int
    var failed = false
    init(maximum: Int) { self.maximum = maximum }
}

/// Revocable formatter lease. A rejected write is sticky even if the formatter
/// catches it. Retaining the lease cannot mutate later records. Custom formatters
/// own escaping; library-supplied fields are always escaped to one line.
public final class ObservationBuffer {
    private var owner: ObservationRecordStorage?
    private let origin = ObservationErrorOrigin()
    fileprivate init(_ owner: ObservationRecordStorage) { self.owner = owner }
    fileprivate func expire() { owner = nil }
    fileprivate func failure(_ stage: TextObservationError.Stage, _ detail: String) -> TextObservationError {
        TextObservationError(stage: stage, detail: detail, origin: origin)
    }
    private func storage() throws -> ObservationRecordStorage {
        guard let owner else { throw failure(.record, "observation buffer lease expired") }
        return owner
    }
    private func admit(_ count: Int) throws -> ObservationRecordStorage {
        let storage = try storage()
        guard !storage.failed, storage.bytes.count <= storage.maximum,
              count <= storage.maximum - storage.bytes.count else {
            storage.failed = true
            throw failure(.record, "observation record exceeds byte limit")
        }
        return storage
    }
    public func write(_ text: String) throws {
        let storage = try admit(text.utf8.count)
        storage.bytes.append(contentsOf: text.utf8)
    }
    public func write(_ bytes: [UInt8]) throws {
        let storage = try admit(bytes.count)
        storage.bytes.append(contentsOf: bytes)
    }
    public func finish() throws {
        if try storage().failed { throw failure(.record, "observation record exceeds byte limit") }
    }
    fileprivate func lease(stage: TextObservationError.Stage, _ callback: (ObservationBuffer) throws -> Void) throws {
        let child = ObservationBuffer(try storage())
        defer { child.expire() }
        do {
            try callback(child)
            try child.finish()
        } catch let error as TextObservationError where error.origin === child.origin {
            throw error
        } catch {
            // Public staged values, expired leases and nested observers do not
            // establish the provenance of this formatter's failure.
            throw TextObservationError(stage: stage, detail: String(describing: error))
        }
    }
    /// Rust-style quotes/control escaping, without allocating a second complete
    /// escaped string. Printable non-ASCII scalars retain their UTF-8 spelling.
    fileprivate func quoted(_ text: String) throws {
        try write("\"")
        for scalar in text.unicodeScalars {
            switch scalar.value {
            case 0: try write("\\0")
            case 9: try write("\\t")
            case 10: try write("\\n")
            case 13: try write("\\r")
            case 34: try write("\\\"")
            case 92: try write("\\\\")
            default:
                switch scalar.properties.generalCategory {
                case .control, .format, .lineSeparator, .paragraphSeparator, .unassigned, .privateUse, .surrogate:
                    try write("\\u{\(String(scalar.value, radix: 16))}")
                case .spaceSeparator where scalar.value != 32:
                    try write("\\u{\(String(scalar.value, radix: 16))}")
                default: try write(String(scalar))
                }
            }
        }
        try write("\"")
    }
}

/// Presentation only; callbacks must not mutate application state, execute effects
/// or run checks. No codec or concurrency requirement is added to base Model.
public protocol PayloadFormat {
    associatedtype State
    associatedtype Input
    associatedtype Output
    mutating func state(_ model: Model<State, Input, Output>, _ state: State, into out: ObservationBuffer) throws
    mutating func input(_ model: Model<State, Input, Output>, _ input: Input, into out: ObservationBuffer) throws
    mutating func output(_ model: Model<State, Input, Output>, _ output: Output, into out: ObservationBuffer) throws
}

public struct NoPayloads<State, Input, Output>: PayloadFormat {
    public init() {}
    public func state(_ model: Model<State, Input, Output>, _ state: State, into out: ObservationBuffer) throws { try out.write("unavailable") }
    public func input(_ model: Model<State, Input, Output>, _ input: Input, into out: ObservationBuffer) throws { try out.write("unavailable") }
    public func output(_ model: Model<State, Input, Output>, _ output: Output, into out: ObservationBuffer) throws { try out.write("unavailable") }
}

/// Canonical codec bytes in lowercase hex. Uses the existing preferred into/legacy
/// dispatch, including ModelCodecBinding hooks, and one reusable bounded buffer.
/// Legacy allocating codecs necessarily allocate before admission into that buffer.
public struct EncodedPayloads<State, Input, Output>: PayloadFormat {
    private var bytes: [UInt8] = []
    public let maxBlobBytes: Int
    public init(maxBlobBytes: Int) { self.maxBlobBytes = maxBlobBytes }
    private mutating func encode(_ stage: TextObservationError.Stage, out: ObservationBuffer,
                                _ callback: (EncodeBuffer) throws -> Void) throws {
        do {
            try EncodeBuffer.withBuffer(&bytes, maximum: maxBlobBytes, encode: callback)
        } catch { throw out.failure(stage, String(describing: error)) }
        try out.write("hex:")
        let digits: [UInt8] = Array("0123456789abcdef".utf8)
        for byte in bytes { try out.write([digits[Int(byte >> 4)], digits[Int(byte & 15)]]) }
    }
    private func codec(_ model: Model<State, Input, Output>, stage: TextObservationError.Stage, out: ObservationBuffer) throws -> Codec<State, Input, Output> {
        guard let codec = model.codec else { throw out.failure(stage, "model has no codec") }
        return codec
    }
    public mutating func state(_ model: Model<State, Input, Output>, _ state: State, into out: ObservationBuffer) throws {
        let c = try codec(model, stage: .encodeState, out: out)
        try encode(.encodeState, out: out) { try encodeStateInto(c, state: state, into: $0) }
    }
    public mutating func input(_ model: Model<State, Input, Output>, _ input: Input, into out: ObservationBuffer) throws {
        let c = try codec(model, stage: .encodeInput, out: out)
        try encode(.encodeInput, out: out) { try encodeInputInto(c, input: input, into: $0) }
    }
    public mutating func output(_ model: Model<State, Input, Output>, _ output: Output, into out: ObservationBuffer) throws {
        let c = try codec(model, stage: .encodeOutput, out: out)
        try encode(.encodeOutput, out: out) { try encodeOutputInto(c, output: output, into: $0) }
    }
}

/// Synchronous and application-owned; deliberately not Sendable. Formatting or
/// encoding failure publishes no bytes and the next event can recover. Writer or
/// flush failure permanently poisons even an Off observer. A partial write may
/// remain in the destination. No implicit flush occurs, including on destruction.
public final class TextObserver<Writer: ObservationWriter, Payloads: PayloadFormat>: Observer {
    public typealias State = Payloads.State
    public typealias Input = Payloads.Input
    public typealias Output = Payloads.Output
    private var writer: Writer?
    private var payloads: Payloads
    public let streamID: String
    public private(set) var options: ObservationOptions
    public private(set) var isPoisoned = false
    private var active = false
    private var record: [UInt8] = []

    public init(writer: Writer, streamID: String, options: ObservationOptions = ObservationOptions(), payloads: Payloads) throws {
        guard options.maxRecordBytes >= 0 else { throw ConfigError("negative observation record maximum") }
        self.writer = writer; self.streamID = streamID; self.options = options; self.payloads = payloads
    }
    /// Enabling verbosity cannot recover history; emit an explicit checkpoint.
    public func setLevel(_ level: LogLevel) { options.level = level }
    public func setSnapshots(_ snapshots: SnapshotPolicy) { options.snapshots = snapshots }
    /// Value writers return a value copy; reference writers remain caller aliases.
    public func getRef() throws -> Writer {
        guard !active else { throw TextObservationError(stage: .reentrant, detail: "observation operation already active") }
        guard let writer else { throw TextObservationError(stage: .consumed, detail: "writer was transferred") }
        return writer
    }
    /// Transfers the writer once, even when poisoned. Does not flush.
    public func intoInner() throws -> Writer {
        guard !active else { throw TextObservationError(stage: .reentrant, detail: "observation operation already active") }
        let result = try getRef(); writer = nil; return result
    }
    private func ensureHealthy() throws {
        if isPoisoned { throw TextObservationError(stage: .poisoned, detail: "observation sink failed; output may be incomplete") }
        guard !active else { throw TextObservationError(stage: .reentrant, detail: "observation operation already active") }
        guard writer != nil else { throw TextObservationError(stage: .consumed, detail: "writer was transferred") }
    }
    public func flush() throws {
        try ensureHealthy(); active = true; defer { active = false }
        do { try writer!.flush() }
        catch { isPoisoned = true; throw TextObservationError(stage: .flush, detail: String(describing: error)) }
    }
    private func level(_ checks: [Check], ordinary: LogLevel) -> LogLevel {
        if checks.contains(where: { $0.status == .failed }) { return .error }
        if checks.contains(where: { $0.status == .skipped }) { return .warn }
        return ordinary
    }
    private func checks(_ checks: [Check], level: LogLevel, out: ObservationBuffer) throws {
        try out.write(" checks=\(checks.count)")
        for check in checks where level == .trace || check.status != .passed {
            try out.write(" check="); try out.quoted(check.id); try out.write(":")
            switch check.status {
            case .passed: try out.write("Passed")
            case .failed: try out.write("Failed("); try out.quoted(check.details); try out.write(")")
            case .skipped: try out.write("Skipped("); try out.quoted(check.details); try out.write(")")
            }
        }
    }
    private func format(_ stage: TextObservationError.Stage, out: ObservationBuffer,
                        _ callback: (inout Payloads, ObservationBuffer) throws -> Void) throws {
        try out.lease(stage: stage) { try callback(&payloads, $0) }
    }
    public func observe(_ model: Model<State, Input, Output>, event: Event<State, Input, Output>) throws {
        try ensureHealthy()
        let options = self.options // Freeze configuration through all callbacks.
        guard options.level != .off else { return }
        let severity: LogLevel
        switch event {
        case .started, .finished: severity = .info
        case let .checkpoint(_, _, values): severity = level(values, ordinary: .trace)
        case let .transition(_, _, _, _, values): severity = level(values, ordinary: .debug)
        case let .message(level, _, _): severity = level
        }
        guard severity != .off, severity <= options.level else { return }
        active = true; defer { active = false }
        record.removeAll(keepingCapacity: true)
        let storage = ObservationRecordStorage(maximum: options.maxRecordBytes)
        swap(&record, &storage.bytes)
        let out = ObservationBuffer(storage)
        defer { out.expire(); swap(&record, &storage.bytes) }
        try out.write("\(severity.text) stream="); try out.quoted(streamID)
        switch event {
        case let .started(metadata, config):
            try out.write(" event=started metadata=ModelMetadata { name: "); try out.quoted(metadata.name)
            try out.write(", model_version: \(metadata.modelVersion), properties_version: \(metadata.propertiesVersion), codec_version: \(metadata.codecVersion), build: ")
            try out.quoted(metadata.build); try out.write(" } config=RunConfig { strategy: "); try out.quoted(config.strategy)
            try out.write(", seed: \(config.seed.map { "Some(\($0))" } ?? "None"), parameters: [")
            for (index, parameter) in config.parameters.enumerated() {
                if index > 0 { try out.write(", ") }
                try out.write("("); try out.quoted(parameter.key); try out.write(", "); try out.quoted(parameter.value); try out.write(")")
            }
            try out.write("] }")
        case let .finished(transitions, reason):
            try out.write(" event=finished transitions=\(transitions) reason="); try out.quoted(reason)
        case let .message(_, sequence, message):
            try out.write(" event=message sequence=\(sequence.map { "Some(\($0))" } ?? "None") message="); try out.quoted(message)
        case let .checkpoint(sequence, state, values):
            try out.write(" event=checkpoint sequence=\(sequence)"); try checks(values, level: options.level, out: out)
            if options.level == .trace && options.snapshots != .none {
                try out.write(" state="); try format(.formatState, out: out) { try $0.state(model, state, into: $1) }
            }
        case let .transition(sequence, _, input, transition, values):
            try out.write(" event=transition sequence=\(sequence.value) disposition=")
            switch transition.disposition {
            case .accepted: try out.write("Accepted")
            case let .rejected(reason): try out.write("Rejected("); try out.quoted(reason); try out.write(")")
            case let .ignored(reason): try out.write("Ignored("); try out.quoted(reason); try out.write(")")
            }
            try out.write(" outputs=\(transition.outputs.count)"); try checks(values, level: options.level, out: out)
            if options.level == .trace {
                try out.write(" input="); try format(.formatInput, out: out) { try $0.input(model, input, into: $1) }
                for (index, output) in transition.outputs.enumerated() {
                    try out.write(" output[\(index)]="); try format(.formatOutput, out: out) { try $0.output(model, output, into: $1) }
                }
                if options.snapshots.includes(sequence.value) {
                    try out.write(" post_state="); try format(.formatState, out: out) { try $0.state(model, transition.state, into: $1) }
                }
            }
        }
        try out.write("\n"); try out.finish()
        do {
            var offset = 0
            while offset < storage.bytes.count {
                let count: Int
                do { count = try writer!.write(storage.bytes[offset...]) }
                catch ObservationWriteError.interrupted { continue }
                guard count > 0, count <= storage.bytes.count - offset else {
                    throw TextObservationError(stage: .write, detail: "writer returned invalid progress")
                }
                offset += count
            }
        } catch {
            isPoisoned = true
            throw TextObservationError(stage: .write, detail: String(describing: error))
        }
    }
}

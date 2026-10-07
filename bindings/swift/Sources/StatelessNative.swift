import Foundation
import Stateless

public struct Metadata {
    public let name: String
    public let build: String
    public let modelVersion: UInt32
    public let propertiesVersion: UInt32
    public let codecVersion: UInt32
    public init(name: String, build: String, modelVersion: UInt32 = 1,
                propertiesVersion: UInt32 = 1, codecVersion: UInt32 = 1) {
        self.name = name; self.build = build; self.modelVersion = modelVersion
        self.propertiesVersion = propertiesVersion; self.codecVersion = codecVersion
    }
}

public struct Check {
    public enum Status: UInt8 { case passed = 0, failed = 1, skipped = 2 }
    public let id: String
    public let status: Status
    public let details: String
    public init(_ id: String, _ status: Status, _ details: String = "") {
        self.id = id; self.status = status; self.details = details
    }
}

public struct Transition {
    public enum Disposition: UInt8 { case accepted = 0, rejected = 1, ignored = 2 }
    public let state: [UInt8]
    public let outputs: [[UInt8]]
    public let disposition: Disposition
    public let reason: String
    public init(state: [UInt8], outputs: [[UInt8]] = [],
                disposition: Disposition = .accepted, reason: String = "") {
        self.state = state; self.outputs = outputs; self.disposition = disposition; self.reason = reason
    }
}

/// Canonical bytes must contain all mutable logical state. Callbacks must be
/// deterministic and must not perform real effects. Errors never cross the ABI.
public protocol ByteModel {
    var metadata: Metadata { get }
    func initial() throws -> [UInt8]
    func step(state: [UInt8], input: [UInt8]) throws -> Transition
    func checkState(_ state: [UInt8]) throws -> [Check]
    func checkTransition(before: [UInt8], input: [UInt8], transition: Transition) throws -> [Check]
    func inputs(state: [UInt8]) throws -> [[UInt8]]
}

public extension ByteModel {
    func checkTransition(before: [UInt8], input: [UInt8], transition: Transition) throws -> [Check] { [] }
    func inputs(state: [UInt8]) throws -> [[UInt8]] { throw BindingError("enumeration is not implemented") }
}

public struct BindingError: Error, CustomStringConvertible {
    public let description: String
    public init(_ message: String) { description = message }
}

private let maximumBytes = 64 * 1024 * 1024
private struct Packet {
    var data: [UInt8] = []
    mutating func append(_ bytes: [UInt8]) throws {
        guard bytes.count <= maximumBytes - data.count else { throw BindingError("packet exceeds 64 MiB") }
        data += bytes
    }
    mutating func number(_ value: Int) throws {
        guard let n = UInt32(exactly: value) else { throw BindingError("invalid packet length") }
        try append((0..<4).map { UInt8(truncatingIfNeeded: n >> ($0 * 8)) })
    }
    mutating func blob(_ bytes: [UInt8]) throws { try number(bytes.count); try append(bytes) }
    mutating func string(_ value: String) throws { try blob(Array(value.utf8)) }
    mutating func batch(_ values: [[UInt8]]) throws {
        guard values.count <= 1_000_000 else { throw BindingError("too many values") }
        try number(values.count)
        for value in values { try blob(value) }
    }
    mutating func checks(_ values: [Check]) throws {
        guard values.count <= 1_000_000 else { throw BindingError("too many checks") }
        try number(values.count)
        for check in values {
            guard !check.id.isEmpty else { throw BindingError("empty property ID") }
            try string(check.id); try append([check.status.rawValue]); try string(check.details)
        }
    }
    mutating func transition(_ value: Transition) throws {
        try append([value.disposition.rawValue]); try string(value.reason)
        try blob(value.state); try batch(value.outputs)
    }
}
private struct Reader {
    var data: [UInt8]
    var offset = 0
    mutating func take(_ n: Int) throws -> [UInt8] {
        guard n >= 0 && n <= data.count - offset else { throw BindingError("truncated observation") }
        defer { offset += n }; return Array(data[offset..<(offset + n)])
    }
    mutating func number() throws -> Int {
        try take(4).enumerated().reduce(0) { $0 | Int($1.element) << ($1.offset * 8) }
    }
    mutating func blob() throws -> [UInt8] { let n = try number(); return try take(n) }
    mutating func transition() throws -> Transition {
        guard let tag = Transition.Disposition(rawValue: try take(1)[0]),
              let reason = String(bytes: try blob(), encoding: .utf8) else { throw BindingError("invalid transition") }
        let state = try blob(), count = try number()
        guard count <= 1_000_000 else { throw BindingError("too many outputs") }
        var outputs: [[UInt8]] = []
        for _ in 0..<count { outputs.append(try blob()) }
        return Transition(state: state, outputs: outputs, disposition: tag, reason: reason)
    }
}
private final class Context {
    let model: any ByteModel
    var error = ""
    init(_ model: any ByteModel) { self.model = model }
    func dispatch(_ operation: UInt32, _ state: [UInt8], _ input: [UInt8]) throws -> [UInt8] {
        var packet = Packet()
        switch operation {
        case 0: try packet.append(model.initial())
        case 1: try packet.transition(model.step(state: state, input: input))
        case 2: try packet.checks(model.checkState(state))
        case 3:
            var reader = Reader(data: input)
            let action = try reader.blob(), transition = try reader.transition()
            guard reader.offset == input.count else { throw BindingError("trailing observation data") }
            try packet.checks(model.checkTransition(before: state, input: action, transition: transition))
        case 4: try packet.batch(model.inputs(state: state))
        default: throw BindingError("unknown callback")
        }
        return packet.data
    }
}

/// Synchronous, thread-confined owner. Do not transfer it between threads or
/// reenter it from a callback. Returned data is copied before native buffers free.
public final class Session {
    private let context: Context
    private let thread = Thread.current
    private var busy = false
    private var handle: OpaquePointer?

    public init(_ model: any ByteModel) throws {
        context = Context(model)
        guard stateless_abi_version() == 1 else { throw BindingError("unsupported ABI") }
        let metadata = model.metadata
        var callbacks = StatelessCallbacks(
            abi_version: 1, struct_size: UInt32(MemoryLayout<StatelessCallbacks>.size),
            context: Unmanaged.passUnretained(context).toOpaque(),
            dispatch: { pointer, operation, state, stateLength, input, inputLength, response in
                guard let pointer else { return 11 }
                let context = Unmanaged<Context>.fromOpaque(pointer).takeUnretainedValue()
                do {
                    let result = try context.dispatch(operation,
                        Array(UnsafeBufferPointer(start: state, count: stateLength)),
                        Array(UnsafeBufferPointer(start: input, count: inputLength)))
                    return result.withUnsafeBufferPointer { stateless_buffer_assign(response, $0.baseAddress, $0.count) }
                } catch { context.error = String(describing: error); return 11 }
            })
        let status = Array(metadata.name.utf8).withUnsafeBufferPointer { name in
            Array(metadata.build.utf8).withUnsafeBufferPointer { build in
                stateless_model_new(&callbacks, name.baseAddress, name.count, build.baseAddress, build.count,
                    metadata.modelVersion, metadata.propertiesVersion, metadata.codecVersion, &handle)
            }
        }
        guard status == 0 else { throw BindingError("model creation status \(status): \(lastError())") }
    }
    deinit { stateless_model_free(handle) }

    private func lastError() -> String {
        guard let buffer = stateless_buffer_new(0) else { return "allocation failed" }
        defer { stateless_buffer_free(buffer) }
        _ = stateless_last_error(buffer)
        return String(decoding: copy(buffer), as: UTF8.self) + (context.error.isEmpty ? "" : ": " + context.error)
    }
    private func copy(_ buffer: OpaquePointer) -> [UInt8] {
        Array(UnsafeBufferPointer(start: stateless_buffer_data(buffer), count: stateless_buffer_len(buffer)))
    }
    private func operation<T>(_ body: () throws -> T) throws -> T {
        guard Thread.current === thread && !busy else { throw BindingError("session must remain on its thread and cannot reenter") }
        busy = true; context.error = ""
        defer { busy = false }
        return try body()
    }
    public func record(_ inputs: [[UInt8]], maxSteps: Int? = nil) throws -> (status: Int32, artifact: [UInt8]) {
        try operation {
            let limit = maxSteps ?? inputs.count
            guard limit >= 0 else { throw BindingError("negative step limit") }
            var packet = Packet(); try packet.batch(inputs)
            guard let output = stateless_buffer_new(0) else { throw BindingError("allocation failed") }
            defer { stateless_buffer_free(output) }
            let status = packet.data.withUnsafeBufferPointer {
                stateless_record(handle, $0.baseAddress, $0.count, limit, output)
            }
            guard status == 0 || status == 1 else { throw BindingError("record status \(status): \(lastError())") }
            return (status, copy(output))
        }
    }
    public func replay(_ artifact: [UInt8]) throws -> (status: Int32, detail: String) {
        try operation {
            let status = artifact.withUnsafeBufferPointer { stateless_replay(handle, $0.baseAddress, $0.count) }
            return (status, status == 0 ? "Exact replay" : status == 1 ? "Exact replay; property failure reproduced" : lastError())
        }
    }
    public func enumerate(maxStates: Int = 100_000, maxTransitions: UInt64 = 1_000_000,
                          maxDepth: Int = 100) throws -> (status: Int32, report: String, artifact: [UInt8]) {
        try operation {
            guard maxStates > 0 && maxDepth >= 0 else { throw BindingError("invalid search limit") }
            guard let output = stateless_buffer_new(0) else { throw BindingError("allocation failed") }
            defer { stateless_buffer_free(output) }
            guard let report = stateless_buffer_new(0) else { throw BindingError("allocation failed") }
            defer { stateless_buffer_free(report) }
            let status = stateless_enumerate(handle, maxStates, maxTransitions, maxDepth, report, output)
            guard status == 0 || status == 1 else { throw BindingError("enumerate status \(status): \(lastError())") }
            return (status, String(decoding: copy(report), as: UTF8.self), copy(output))
        }
    }
}

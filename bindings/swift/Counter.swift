import Foundation
import Stateless

func integer(_ value: UInt32) -> [UInt8] {
    (0..<4).map { UInt8(truncatingIfNeeded: value >> ($0 * 8)) }
}
func blob(_ bytes: [UInt8]) -> [UInt8] { integer(UInt32(bytes.count)) + bytes }
func string(_ value: String) -> [UInt8] { blob(Array(value.utf8)) }
func decode(_ bytes: [UInt8]) -> UInt32? {
    guard bytes.count == 4 else { return nil }
    return bytes.enumerated().reduce(0) { $0 | UInt32($1.element) << ($1.offset * 8) }
}

// This reducer and its property remain entirely in Swift. Rust drives the
// execution, records the observations, and reruns the callback during replay.
let callback: StatelessDispatch = { context, operation, state, stateLength, input, inputLength, response in
    let stateBytes = Array(UnsafeBufferPointer(start: state, count: stateLength))
    let inputBytes = Array(UnsafeBufferPointer(start: input, count: inputLength))
    let bytes: [UInt8]
    switch operation {
    case 0:
        bytes = integer(0)
    case 1:
        guard let count = decode(stateBytes), let increment = decode(inputBytes) else { return 11 }
        let amount = increment.addingReportingOverflow(context == nil ? 0 : 1)
        guard !amount.overflow else { return 11 }
        let next = count.addingReportingOverflow(amount.partialValue)
        guard !next.overflow else { return 11 }
        bytes = [0] + string("") + blob(integer(next.partialValue)) + integer(1) + blob(integer(next.partialValue))
    case 2:
        guard let count = decode(stateBytes) else { return 11 }
        let failed = count > 2
        bytes = integer(1) + string("counter_bound") + [failed ? 1 : 0]
            + string(failed ? "counter exceeded 2" : "")
    case 3:
        bytes = integer(0)
    case 4:
        guard decode(stateBytes) != nil else { return 11 }
        bytes = integer(1) + blob(integer(1))
    default:
        return 11
    }
    return bytes.withUnsafeBufferPointer {
        stateless_buffer_assign(response, $0.baseAddress, $0.count)
    }
}

func lastError() -> String {
    guard let buffer = stateless_buffer_new(0) else { return "allocation failed" }
    defer { stateless_buffer_free(buffer) }
    _ = stateless_last_error(buffer)
    return String(decoding: UnsafeBufferPointer(start: stateless_buffer_data(buffer), count: stateless_buffer_len(buffer)), as: UTF8.self)
}

func require(_ actual: Int32, _ expected: Int32) {
    guard actual == expected else {
        FileHandle.standardError.write(Data("expected status \(expected), got \(actual): \(lastError())\n".utf8))
        exit(1)
    }
}

let args = CommandLine.arguments
guard args.count == 3, ["record", "replay", "changed", "enumerate"].contains(args[1]) else {
    FileHandle.standardError.write(Data("usage: swift-counter record|replay|changed|enumerate trace-path\n".utf8))
    exit(2)
}

var callbacks = StatelessCallbacks(
    abi_version: stateless_abi_version(),
    struct_size: UInt32(MemoryLayout<StatelessCallbacks>.size),
    context: args[1] == "changed" ? UnsafeMutableRawPointer(bitPattern: 1) : nil,
    dispatch: callback
)
var model: OpaquePointer?
let name = Array("swift-counter".utf8)
let build = Array("swift-counter-fixture-v1".utf8)
let createStatus = name.withUnsafeBufferPointer { n in
    build.withUnsafeBufferPointer { b in
        stateless_model_new(&callbacks, n.baseAddress, n.count, b.baseAddress, b.count, 1, 1, 1, &model)
    }
}
require(createStatus, 0)
defer { stateless_model_free(model) }

if args[1] == "enumerate" {
    guard let artifact = stateless_buffer_new(0), let report = stateless_buffer_new(0) else { fatalError("allocation failed") }
    defer { stateless_buffer_free(artifact); stateless_buffer_free(report) }
    require(stateless_enumerate(model, 20, 100, 10, report, artifact), 1)
    let text = String(decoding: UnsafeBufferPointer(start: stateless_buffer_data(report), count: stateless_buffer_len(report)), as: UTF8.self)
    let data = Data(bytes: stateless_buffer_data(artifact)!, count: stateless_buffer_len(artifact))
    try data.write(to: URL(fileURLWithPath: args[2]), options: .atomic)
    print(text)
    print("Saved enumerated failure: \(data.count) bytes")
} else if args[1] == "record" {
    guard let artifact = stateless_buffer_new(0) else { fatalError("allocation failed") }
    defer { stateless_buffer_free(artifact) }
    let batch = integer(3) + blob(integer(1)) + blob(integer(1)) + blob(integer(1))
    let status = batch.withUnsafeBufferPointer {
        stateless_record(model, $0.baseAddress, $0.count, 10, artifact)
    }
    require(status, 1)
    let data = Data(bytes: stateless_buffer_data(artifact)!, count: stateless_buffer_len(artifact))
    try data.write(to: URL(fileURLWithPath: args[2]), options: .atomic)
    print("Recorded counter_bound failure: 3 transitions, \(data.count) bytes")
} else {
    let data = try Data(contentsOf: URL(fileURLWithPath: args[2]))
    let status = data.withUnsafeBytes {
        stateless_replay(model, $0.bindMemory(to: UInt8.self).baseAddress, $0.count)
    }
    require(status, args[1] == "changed" ? 2 : 1)
    print(args[1] == "changed" ? "Detected deliberate divergence: \(lastError())" : "Exact replay matched and counter_bound failure reproduced")
}

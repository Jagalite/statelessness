import Foundation
import StatelessNative

func integer(_ n: UInt32) -> [UInt8] {
    (0..<4).map { UInt8(truncatingIfNeeded: n >> ($0 * 8)) }
}
func decode(_ bytes: [UInt8]) throws -> UInt32 {
    guard bytes.count == 4 else { throw BindingError("invalid counter value") }
    return bytes.enumerated().reduce(0) { $0 | UInt32($1.element) << ($1.offset * 8) }
}
struct Counter: ByteModel {
    let changed: Bool
    // Deliberately retain identity for the changed-reducer divergence control.
    var metadata: Metadata { Metadata(name: "swift-counter", build: "swift-counter-fixture-v1") }
    func initial() throws -> [UInt8] { integer(0) }
    func step(state: [UInt8], input: [UInt8]) throws -> Transition {
        let a = try decode(input).addingReportingOverflow(changed ? 1 : 0)
        let b = try decode(state).addingReportingOverflow(a.partialValue)
        guard !a.overflow && !b.overflow else { throw BindingError("counter overflow") }
        return Transition(state: integer(b.partialValue), outputs: [integer(b.partialValue)])
    }
    func checkState(_ state: [UInt8]) throws -> [Check] {
        let failed = try decode(state) > 2
        return [Check("counter_bound", failed ? .failed : .passed, failed ? "counter exceeded 2" : "")]
    }
    func inputs(state: [UInt8]) throws -> [[UInt8]] { _ = try decode(state); return [integer(1)] }
}
let args = CommandLine.arguments
func require(_ status: Int32, _ expected: Int32) throws {
    guard status == expected else { throw BindingError("expected status \(expected), got \(status)") }
}
do {
    guard args.count == 3, ["record", "replay", "changed", "enumerate"].contains(args[1]) else {
        throw BindingError("usage: swift-counter record|replay|changed|enumerate trace-path")
    }
    let session = try Session(Counter(changed: args[1] == "changed"))
    let path = URL(fileURLWithPath: args[2])
    switch args[1] {
    case "record":
        let result = try session.record([integer(1), integer(1), integer(1)])
        try require(result.status, 1)
        try Data(result.artifact).write(to: path, options: .withoutOverwriting)
        print("Recorded counter_bound failure: \(result.artifact.count) bytes")
    case "enumerate":
        let result = try session.enumerate(maxStates: 20, maxTransitions: 100, maxDepth: 10)
        try require(result.status, 1)
        try Data(result.artifact).write(to: path, options: .withoutOverwriting)
        print(result.report)
    default:
        let result = try session.replay(Array(Data(contentsOf: path)))
        try require(result.status, args[1] == "changed" ? 2 : 1)
        print(result.detail)
    }
} catch {
    FileHandle.standardError.write(Data("\(error)\n".utf8)); exit(2)
}

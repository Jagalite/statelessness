import Foundation
import StatelessNativeV2

// Explicitly invoked workload source. Building this executable does not run it.
// External profiling must supply allocation/RSS measurements and source/package
// identities before these timing samples can become a qualification receipt.
struct State { let value: UInt32; let payload: [UInt8] }
final class Counters {
    var callbacks: UInt64 = 0, steps: UInt64 = 0, snapshots: UInt64 = 0, snapshotBytes: UInt64 = 0, encodedBytes: UInt64 = 0, sink: UInt64 = 0
    func reset() { callbacks = 0; steps = 0; snapshots = 0; snapshotBytes = 0; encodedBytes = 0; sink = 0 }
}
let operations = ["observation", "bfs", "record", "replay", "fuzz", "shrink", "ring"]
let costs = ["cheap", "large", "expensive"]

func fixture(_ cost: String, fail: Bool, counters: Counters) -> NativeModel<State, UInt8, UInt32> {
    let payload = [UInt8](repeating: 0, count: cost == "large" ? 65_536 : 0)
    func hit() { counters.callbacks += 1 }
    func word(_ value: UInt32) -> [UInt8] { (0..<4).map { UInt8(truncatingIfNeeded: value >> ($0 * 8)) } }
    let codec = Codec<State, UInt8, UInt32>(metadata: { Metadata(name: "abi-v2-matched-counter", build: "benchmark-v1") },
        encodeState: { hit(); let bytes = word($0.value) + $0.payload; counters.encodedBytes += UInt64(bytes.count); return bytes }, decodeState: { bytes in
            hit(); guard bytes.count == payload.count + 4 else { throw ModelError("invalid state encoding") }
            let value = bytes.prefix(4).enumerated().reduce(UInt32(0)) { $0 | UInt32($1.element) << ($1.offset * 8) }
            return State(value: value, payload: Array(bytes.dropFirst(4)))
        }, encodeInput: { hit(); counters.encodedBytes += 1; return [$0] }, decodeInput: { bytes in
            hit(); guard bytes.count == 1 else { throw ModelError("invalid input encoding") }; return bytes[0]
        }, encodeOutput: { hit(); counters.encodedBytes += 4; return word($0) })
    let model = Model<State, UInt8, UInt32>(initialState: { hit(); return State(value: 0, payload: payload) },
        step: { state, input in hit(); counters.steps += 1; return Transition(state: State(value: (state.value + UInt32(input)) % 32, payload: state.payload), outputs: [state.value]) },
        checkState: { state in
            hit()
            var checksum = UInt64(state.value)
            if cost == "expensive" { for index in 0..<4096 { checksum = (checksum ^ UInt64(index)) &* 0x9e3779b97f4a7c15 &+ 1 } }
            counters.sink ^= checksum
            if state.value >= 32 || state.payload.count != payload.count { return [.failed("valid", "invalid state")] }
            if fail && state.value >= 24 { return [.failed("bound", "state reached failure region")] }
            return [.passed("valid")]
        }, cloneState: { hit(); counters.snapshots += 1; counters.snapshotBytes += UInt64($0.payload.count + 4); return State(value: $0.value, payload: $0.payload.map { $0 }) },
        cloneInput: { hit(); counters.snapshots += 1; counters.snapshotBytes += 1; return $0 },
        cloneOutput: { hit(); counters.snapshots += 1; counters.snapshotBytes += 4; return $0 },
        checkTransition: { before, input, transition in
            hit()
            guard transition.state.value == (before.value + UInt32(input)) % 32, transition.outputs == [before.value] else { return [.failed("transition", "inconsistent transition")] }
            return [.passed("transition")]
        }, inputs: { _ in hit(); var next: UInt8 = 0; return InputIterator { hit(); if next == 2 { return nil }; next += 1; return next } },
        equalStates: { hit(); return $0.value == $1.value }, hashState: { hit(); return UInt64($0.value) }, codec: codec)
    return NativeModel(model, generate: { _, rng in hit(); guard let value = try rng.index(upper: 2) else { return nil }; return UInt8(value) + 1 },
        isEnabled: { _, input in hit(); return input == 1 || input == 2 }, equalInputs: { hit(); return $0 == $1 },
        simplerInputs: { hit(); return InputIterator(values: $0 == 2 ? [1] : []) },
        estimatedStateBytes: { hit(); return UInt64($0.payload.count + 4) })
}

func workload(_ operation: String, cost: String, counters: Counters) throws -> () throws -> UInt64 {
    let native = fixture(cost, fail: operation == "fuzz" || operation == "shrink", counters: counters)
    let session = try NativeSession(native)
    let initial = try native.model.initialState()
    let inputs = (0..<64).map { UInt8($0 % 2 + 1) }
    let replayArtifact: [UInt8]
    if operation == "replay" {
        let result = try session.recordBinary(inputs: InputIterator(values: inputs), maxSteps: 64); defer { try? result.close() }
        replayArtifact = try result.bytes()
    } else { replayArtifact = [] }
    let failure = operation == "shrink" ? try session.fuzz(seed: 0, cases: 1, maxSteps: 64, maxTransitions: 256, mutationPercent: 0) : nil
    // Captured owners are disposed on this executable's single main thread.
    return {
        switch operation {
        case "observation":
            let transition = Transition(state: State(value: 2, payload: initial.payload), outputs: [UInt32(0)])
            let checks = try session.checkObserved(before: initial, input: 2, transition: transition, sequence: 1)
            guard checks.count == 2 else { throw ModelError("observation workload changed") }; return 1
        case "bfs":
            let result = try session.enumerate(maxStates: 64, maxTransitions: 256, maxDepth: 64); defer { try? result.close() }
            guard result.status == 0, try result.scalar(.states) == 32, try result.scalar(.transitions) == 64 else { throw ModelError("BFS workload changed") }
            return try result.scalar(.transitions)
        case "record":
            let result = try session.recordBinary(inputs: InputIterator(values: inputs), maxSteps: 64); defer { try? result.close() }
            let artifact = try result.bytes()
            guard result.status == 0, !artifact.isEmpty, try result.scalar(.termination) == 0 else { throw ModelError("record workload changed") }; return 64
        case "replay":
            let result = try session.replayBinary(replayArtifact); defer { try? result.close() }
            guard result.status == 0, try result.scalar(.stepsVerified) == 64 else { throw ModelError("replay workload diverged") }; return 64
        case "fuzz":
            let result = try session.fuzz(seed: 0, cases: 1, maxSteps: 64, maxTransitions: 256, mutationPercent: 0); defer { try? result.close() }
            guard result.status == 1 else { throw ModelError("fuzz workload did not find expected failure") }; return try result.scalar(.transitions)
        case "shrink":
            guard let failure else { throw ModelError("missing prepared shrink witness") }
            let result = try session.shrink(failure, maxAttempts: 256, maxTransitions: 4096); defer { try? result.close() }
            guard result.status == 1, try result.scalar(.validated) == 1 else { throw ModelError("shrink workload lost failure") }; return try result.scalar(.transitions)
        default:
            let ring = try session.ring(initialState: initial, maxSteps: 16, maxRetainedBytes: 16 * 1024 * 1024); defer { try? ring.close() }
            var state = initial
            for input in inputs {
                let next = Transition(state: State(value: (state.value + UInt32(input)) % 32, payload: state.payload), outputs: [state.value])
                _ = try ring.observe(before: state, input: input, transition: next); state = next.state
            }
            let snapshot = try ring.snapshot(); defer { try? snapshot.close() }
            let artifact = try snapshot.bytes()
            guard snapshot.status == 0, !artifact.isEmpty else { throw ModelError("ring workload changed") }; return 64
        }
    }
}

do {
    let arguments = Array(CommandLine.arguments.dropFirst())
    guard arguments.count == 3, operations.contains(arguments[0]), costs.contains(arguments[1]), let count = Int(arguments[2]), count >= 2, count <= 10_000 else {
        throw ConfigError("usage: stateless-v2-bench <observation|bfs|record|replay|fuzz|shrink|ring> <cheap|large|expensive> <samples 2...10000>")
    }
    let counters = Counters()
    let run = try workload(arguments[0], cost: arguments[1], counters: counters)
    var samples: [UInt64] = [], counts: [UInt64] = []
    _ = try run() // explicit untimed warmup
    counters.reset()
    for _ in 0..<count {
        let start = DispatchTime.now().uptimeNanoseconds
        let work = try run()
        samples.append(DispatchTime.now().uptimeNanoseconds - start); counts.append(work)
    }
    let report: [String: Any] = ["schema": "stateless.binding-timing-samples.v1", "implementation": "swift-abi-v2", "engine_family": "rust-shared",
        "workload": arguments[0], "model": arguments[1], "seed": 0, "samples_ns": samples, "work_counts": counts,
        "model_callbacks": counters.callbacks, "snapshots": counters.snapshots, "snapshot_bytes": counters.snapshotBytes,
        "encoded_bytes": counters.encodedBytes, "model_steps": counters.steps, "digest_sink": counters.sink,
        "qualification": "incomplete: allocation/RSS profiling and identity receipt required"]
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])); FileHandle.standardOutput.write(Data([10]))
} catch {
    try? FileHandle.standardError.write(contentsOf: Data("\(error)\n".utf8)); exit(1)
}

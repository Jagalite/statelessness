import Foundation
import StatelessNativeV2
import StatelessConformance

// Only fixture/validation/protocol code is shared with the independent runner.
// Every model operation below enters the Rust ABI through the typed wrapper.
func withSession<T>(_ model: FixtureModel, _ body: (NativeSession<String, String, String>) throws -> T) throws -> T {
    do {
        let session = try NativeSession(model); defer { try? session.close() }
        return try body(session)
    } catch let error as NativeError { throw ModelError(error.description) }
}
let backend = ExecutionBackend(implementation: "swift-abi-v2", engineFamily: "rust-shared", abiVersion: 2, rng: { seed, draws, bounds in
    var rng = NativeRng(seed: seed), raw: [JSON] = [], indices: [JSON] = []
    for _ in 0..<draws { raw.append(.string(String(try rng.next()))) }
    for bound in bounds { indices.append(try rng.index(upper: bound).map { .string(String($0)) } ?? .null) }
    return .obj(["raw": .array(raw), "indices": .array(indices), "next": .string(String(try rng.next()))])
}, enumerate: { model, config in
    try withSession(model) { session in
        let result = try session.enumerate(maxStates: UInt64(config.maxStates), maxTransitions: config.maxTransitions, maxDepth: UInt64(config.maxDepth))
        defer { try? result.close() }
        let terminations = ["graph_exhausted", "depth_bound", "state_limit", "transition_limit", "failure_found", "estimated_byte_limit", "deadline", "cancelled"]
        let termination = try result.scalar(.termination)
        guard termination < UInt64(terminations.count) else { throw ModelError("unknown ABI termination") }
        let failure: JSON
        if result.status == 1 {
            let phases = ["unspecified", "initial_state", "state", "transition"]
            let violations = try result.checks().filter { $0.check.status == .failed }.map { finding -> JSON in
                guard finding.phase < UInt32(phases.count) else { throw ModelError("unknown check phase") }
                return .obj(["phase": .string(phases[Int(finding.phase)]), "check": checkJSON(finding.check)])
            }
            failure = .obj(["inputs": .array(try result.inputs().map(JSON.string)), "violations": .array(violations)])
        } else { failure = .null }
        return .obj(["termination": .string(terminations[Int(termination)]), "states": .uint(try result.scalar(.states)),
                     "transitions": .uint(try result.scalar(.transitions)), "max_depth_reached": .uint(try result.scalar(.depth)),
                     "skipped_checks": .uint(try result.scalar(.skipped)), "failure": failure])
    }
}, record: { model, inputs, maximum in
    try withSession(model) { try $0.record(inputs: inputs, maxSteps: UInt64(maximum)) }
}, observe: { model, before, input, transition, sequence, policy in
    try withSession(model) { try $0.checkObserved(before: before, input: input, transition: transition, sequence: sequence, policy: policy) }
}, replay: { model, trace, allow in
    try withSession(model) { session in
        let report = try session.replay(trace, allowBuildMismatch: allow)
        return .obj(["outcome": .string(report.outcome), "steps_verified": .uint(report.stepsVerified),
                     "failure_reproduced": .bool(report.failureReproduced), "build_matches": .bool(report.buildMatches),
                     "step": report.step.map(JSON.uint) ?? .null, "field": report.field.map(JSON.string) ?? .null])
    }
})

do { try run(backend: backend) }
catch { try? FileHandle.standardError.write(contentsOf: Data("\(error)\n".utf8)); exit(1) }

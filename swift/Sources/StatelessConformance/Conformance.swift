import Foundation
import Statelessness

public let maximumLineBytes = 4 * 1024 * 1024
private struct Edge: Equatable {
    let input: String, to: String
    let outputs: [String]
    let disposition: Disposition
    let checks: [Check]
    let stepError: Bool, checkError: Bool
    static func == (a: Self, b: Self) -> Bool {
        exactText(a.input, b.input) && exactText(a.to, b.to) && a.outputs.map(ExactString.init) == b.outputs.map(ExactString.init) && a.disposition == b.disposition && a.checks == b.checks && a.stepError == b.stepError && a.checkError == b.checkError
    }
}
private struct Row { let checks: [Check]; let edges: [Edge]; let checkError: Bool, inputsError: Bool }
/// A fixture adapter, not an alternate engine. All algorithm operations use the
/// public native Statelessness library and its application-owned callback API.
public final class TableModel {
    private let initial: String, identity: Metadata
    private let initialError: Bool
    private var rows: [ExactString: Row] = [:]
    public private(set) var stepCalls = 0
    public init(_ value: JSON) throws {
        let o = try objectFields(value, required: ["id", "initial", "states"], optional: ["metadata", "initial_error"])
        let id = try readString(o["id"]); initial = try readString(o["initial"])
        if let metadata = o["metadata"] { identity = try metadataFromJSON(metadata) } else { identity = Metadata(name: id, build: "fixture-v1") }
        initialError = try readBool(o["initial_error"] ?? .bool(false))
        var count = 0
        for value in try readArray(o["states"], maximum: 1000) {
            let r = try objectFields(value, required: ["id"], optional: ["checks", "edges", "check_error", "inputs_error"])
            let id = ExactString(try readString(r["id"])); guard rows[id] == nil else { throw FormatError("duplicate state") }
            let checks = try readArray(r["checks"] ?? .array([]), maximum: 4096).map(checkFromJSON)
            var edges: [Edge] = [], seen: [ExactString: Edge] = [:]
            for value in try readArray(r["edges"] ?? .array([]), maximum: 10_000) {
                let e = try objectFields(value, required: ["input", "to"], optional: ["outputs", "disposition", "checks", "step_error", "check_error"])
                let edge = Edge(input: try readString(e["input"]), to: try readString(e["to"]), outputs: try readArray(e["outputs"] ?? .array([]), maximum: 4096).map(readString), disposition: try dispositionFromJSON(e["disposition"] ?? .obj(["kind": .string("accepted")])), checks: try readArray(e["checks"] ?? .array([]), maximum: 4096).map(checkFromJSON), stepError: try readBool(e["step_error"] ?? .bool(false)), checkError: try readBool(e["check_error"] ?? .bool(false)))
                let key = ExactString(edge.input)
                if let old = seen[key], old != edge { throw FormatError("conflicting transitions") }
                seen[key] = edge; edges.append(edge); count += 1
            }
            rows[id] = Row(checks: checks, edges: edges, checkError: try readBool(r["check_error"] ?? .bool(false)), inputsError: try readBool(r["inputs_error"] ?? .bool(false)))
        }
        guard count <= 10_000, rows[ExactString(initial)] != nil else { throw FormatError("invalid model size or initial state") }
        for r in rows.values { for e in r.edges { if rows[ExactString(e.to)] == nil { throw FormatError("unknown target state") } } }
    }
    private func row(_ state: String) throws -> Row { guard let r = rows[ExactString(state)] else { throw ModelError("unknown state") }; return r }
    private func edge(_ state: String, _ input: String) throws -> Edge {
        guard let e = try row(state).edges.first(where: { exactText($0.input, input) }) else { throw ModelError("input is not in this state's domain") }; return e
    }
    public func model() -> Model<String, String, String> {
        Model(initialState: { if self.initialError { throw ModelError("injected initial_state") }; return self.initial },
              step: { state, input in
                  self.stepCalls += 1; let e = try self.edge(state, input)
                  if e.stepError { throw ModelError("injected step") }
                  return Transition(state: e.to, outputs: e.outputs, disposition: e.disposition)
              },
              checkState: { state in let r = try self.row(state); if r.checkError { throw ModelError("injected check_state") }; return r.checks },
              cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
              checkTransition: { state, input, _ in let e = try self.edge(state, input); if e.checkError { throw ModelError("injected check_transition") }; return e.checks },
              inputs: { state in let r = try self.row(state); if r.inputsError { throw ModelError("injected inputs") }; return InputIterator(values: r.edges.map { $0.input }) },
              equalStates: exactText, hashState: { _ in 0 },
              codec: Codec(metadata: { self.identity },
                           encodeState: { state in _ = try self.row(state); return Array(state.utf8) },
                           decodeState: { bytes in guard String(bytes: bytes, encoding: .utf8) != nil else { throw ModelError("invalid UTF-8") }; let s = String(decoding: bytes, as: UTF8.self); _ = try self.row(s); return s },
                           encodeInput: { Array($0.utf8) },
                           decodeInput: { bytes in guard String(bytes: bytes, encoding: .utf8) != nil else { throw ModelError("invalid UTF-8") }; let s = String(decoding: bytes, as: UTF8.self); return s },
                           encodeOutput: { Array($0.utf8) }))
    }
}
private func errorJSON(_ value: String) -> JSON { .obj(["error": .string(value)]) }
public func execute(_ value: JSON) throws -> JSON {
    guard case let .object(raw) = value else { throw FormatError("expected request") }
    let version = try readInteger(raw[ExactString("version")]); if version != 1 { return errorJSON("unsupported_version") }
    let operation = try readString(raw[ExactString("operation")])
    if operation == "hello" {
        _ = try objectFields(value, required: ["version", "operation"])
        return .obj(["implementation": .string("swift"), "package_version": .string(statelessnessVersion), "spec_version": .string(specificationVersion), "profiles": .array(conformanceProfiles.map(JSON.string)), "protocol_version": .int(1)])
    }
    if operation == "rng" {
        let o = try objectFields(value, required: ["version", "operation", "seed", "draws", "bounds"])
        var rng = Rng(seed: try readU64(o["seed"]))
        let draws = try readInteger(o["draws"], maximum: 10_000), bounds = try readArray(o["bounds"], maximum: 10_000).map(readU64)
        var raw: [JSON] = [], indices: [JSON] = []
        for _ in 0..<draws { raw.append(.string(String(rng.nextU64()))) }
        for b in bounds { indices.append(rng.index(b).map { .string(String($0)) } ?? .null) }
        return .obj(["raw": .array(raw), "indices": .array(indices), "next": .string(String(rng.nextU64()))])
    }
    let extras = ["enumerate": ["config"], "record": ["inputs", "max_steps"], "observe": ["before", "input", "transition", "sequence", "policy"], "replay": ["trace", "allow_build_mismatch"]]
    guard let required = extras[operation] else { return errorJSON("unsupported_operation") }
    let o = try objectFields(value, required: ["version", "operation", "model"] + required)
    guard let definition = o["model"] else { throw FormatError("missing model") }
    let table = try TableModel(definition), m = table.model()
    switch operation {
    case "enumerate":
        let c = try objectFields(o["config"], required: ["max_states", "max_transitions", "max_depth"])
        let states = try readInteger(c["max_states"], maximum: 100_000), transitions = try readInteger(c["max_transitions"], maximum: 100_000), depth = try readInteger(c["max_depth"], maximum: 100_000)
        if states == 0 { return errorJSON("invalid_config") }
        let r = try enumerateStates(m, config: SearchConfig(maxStates: Int(states), maxTransitions: transitions, maxDepth: Int(depth)))
        let failure: JSON = r.failure.map { .obj(["inputs": .array($0.inputs.map(JSON.string)), "violations": .array($0.violations.map { .obj(["phase": .string($0.phase), "check": checkJSON($0.check)]) })]) } ?? .null
        return .obj(["termination": .string(r.termination), "states": .int(r.states), "transitions": .uint(r.transitions), "max_depth_reached": .int(r.maxDepthReached), "skipped_checks": .uint(r.skippedChecks), "failure": failure])
    case "record":
        let inputs = try readArray(o["inputs"], maximum: 100_000).map(readString), maximum = try readInteger(o["max_steps"], maximum: 100_000)
        return .obj(["trace": traceToJSON(try record(m, inputs: InputIterator(values: inputs), maxSteps: Int(maximum)))])
    case "observe":
        let before = try readString(o["before"]), input = try readString(o["input"])
        let t = try objectFields(o["transition"], required: ["state", "outputs", "disposition"])
        let transition = Transition(state: try readString(t["state"]), outputs: try readArray(t["outputs"], maximum: 4096).map(readString), disposition: try dispositionFromJSON(t["disposition"]))
        let p = try objectFields(o["policy"], required: ["state_every", "transition_checks"])
        let sequence = try readInteger(o["sequence"], maximum: 100_000), every = try readInteger(p["state_every"], maximum: 100_000), enabled = try readBool(p["transition_checks"])
        if sequence == 0 || every == 0 { return errorJSON("invalid_config") }
        let checks = try checkObserved(m, before: before, input: input, transition: transition, sequence: sequence, policy: CheckPolicy(stateEvery: every, transitionChecks: enabled))
        return .obj(["checks": .array(checks.map(checkJSON)), "step_calls": .int(table.stepCalls)])
    default:
        guard let value = o["trace"] else { throw FormatError("missing trace") }
        let trace = try traceFromJSON(value), allow = try readBool(o["allow_build_mismatch"])
        let r = try replay(m, trace: trace, allowBuildMismatch: allow)
        return .obj(["outcome": .string(r.outcome), "steps_verified": .int(r.stepsVerified), "failure_reproduced": .bool(r.failureReproduced), "build_matches": .bool(r.buildMatches), "step": r.step.map(JSON.int) ?? .null, "field": r.field.map(JSON.string) ?? .null])
    }
}
public func response(_ bytes: [UInt8]) throws -> JSON {
    do { return try execute(parseJSON(bytes, maximum: maximumLineBytes)) }
    catch is FormatError { return errorJSON("invalid_request") }
    catch is ModelError { return errorJSON("model_error") }
}
/// Reads bounded byte chunks rather than readLine(), which can lose malformed
/// UTF-8 and allocate an unbounded line. Oversize errors recover at the next LF.
public func run(input: FileHandle = .standardInput, output: FileHandle = .standardOutput) throws {
    var buffer: [UInt8] = [], oversized = false
    func send() throws {
        let reply = oversized ? errorJSON("invalid_request") : try response(buffer)
        try output.write(contentsOf: Data(try renderJSON(reply) + [10]))
    }
    while let chunk = try input.read(upToCount: 65536), !chunk.isEmpty {
        for b in chunk {
            if !oversized {
                if buffer.count == maximumLineBytes { oversized = true; buffer.removeAll(keepingCapacity: false) }
                else { buffer.append(b) }
            }
            if b == 10 { try send(); buffer.removeAll(keepingCapacity: true); oversized = false }
        }
    }
    if !buffer.isEmpty || oversized { try send() }
}

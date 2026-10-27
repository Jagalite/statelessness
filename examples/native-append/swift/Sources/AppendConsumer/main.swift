import Foundation
import Statelessness
let stateIDs = ["threshold.bound", "state.appended"], allIDs = ["threshold.bound", "state.appended", "edge.checked", "edge.appended"]
func inspect(_ sink: CheckSink, _ expected: [String]) throws {
 let values = try sink.snapshot(), count = try sink.count, empty = try sink.isEmpty
 guard values.map(\.id) == expected, count == expected.count, empty == expected.isEmpty else { throw ModelError("sink inspection/order mismatch") }
}
func expired(_ sink: CheckSink) throws {
 let operations: [() throws -> Void] = [{ try sink.push(.passed("expired")) }, { try sink.extend([.passed("expired")]) },
  { _ = try sink.snapshot() }, { _ = try sink.count }, { try sink.reserve(1) }, { _ = try sink.isEmpty }]
 for operation in operations {
  var rejected = false; do { try operation() } catch { guard String(describing: error).lowercased().contains("expired") else { throw error }; rejected = true }
  guard rejected else { throw ModelError("escaped sink operation succeeded") }
 }
}
final class Stats {
 var stateCalls = 0, transitionCalls = 0, stepCalls = 0, legacyCalls = 0
 var leases: [CheckSink] = []; var nestedExpired = false, outerActive = false
}
struct App { let model: Model<Int, Int, String>; let generated: Generate<Int, Int, String>; let stats: Stats }
func integer(_ data: [UInt8]) throws -> Int {
 guard let text = String(bytes: data, encoding: .utf8), let result = Int(text) else { throw ModelError("invalid integer") }; return result
}
func input(_ data: [UInt8]) throws -> Int {
 guard data == [48] || data == [49] else { throw ModelError("expected zero or one") }; return try integer(data)
}
func application(failTransition: Bool = false, nested: Bool = false) -> App {
 let stats = Stats()
 let model = Model<Int, Int, String>(initialState: { 0 }, step: { state, input in
  stats.stepCalls += 1; guard input == 0 || input == 1 else { throw ModelError("expected zero or one") }
  return Transition(state: state + input, outputs: input == 1 ? ["changed"] : [])
 }, checkState: { _ in stats.legacyCalls += 1; throw ModelError("legacy state checker invoked") },
 cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
 checkTransition: { _, _, _ in stats.legacyCalls += 1; throw ModelError("legacy transition checker invoked") },
 inputs: { _ in InputIterator(values: [0, 1]) }, equalStates: { $0 == $1 },
 codec: Codec(metadata: { Metadata(name: "native-append-threshold", build: "application-v1") },
  encodeState: { Array(String($0).utf8) }, decodeState: integer,
  encodeInput: { Array(String($0).utf8) }, decodeInput: input, encodeOutput: { Array($0.utf8) }),
 checkStateInto: { state, sink in
  stats.stateCalls += 1; stats.leases.append(sink); try inspect(sink, []); try sink.reserve(2)
  try sink.push(state >= 3 ? .failed("threshold.bound", "at least three") : .passed("threshold.bound")); try inspect(sink, ["threshold.bound"])
  if nested {
   let child = application(); var storage: [Check] = []
   try checkObservedInto(child.model, before: 0, input: 1, transition: Transition(state: 1, outputs: ["changed"]), sequence: 1, checks: &storage)
   for lease in child.stats.leases { try expired(lease) }; stats.nestedExpired = true
   try inspect(sink, ["threshold.bound"]); stats.outerActive = true
  }
  try sink.extend([.passed("state.appended")]); try inspect(sink, stateIDs)
 }, checkTransitionInto: { _, _, _, sink in
  stats.transitionCalls += 1; stats.leases.append(sink); try inspect(sink, stateIDs); try sink.reserve(2)
  try sink.push(.passed("edge.checked")); try inspect(sink, Array(allIDs.prefix(3)))
  if failTransition { throw ModelError("application checker failure") }
  try sink.extend([.passed("edge.appended")]); try inspect(sink, allIDs)
 })
 let generated = Generate(model: model, generate: { _, rng in 1 - Int(rng.index(2)!) }, isEnabled: { _, i in i == 0 || i == 1 }, equalInputs: { $0 == $1 })
 return App(model: model, generated: generated, stats: stats)
}
func controls() throws -> [String: Any] {
 let app = application(); var checks = [Check.passed("stale")]
 try checkObservedInto(app.model, before: 0, input: 1, transition: Transition(state: 1, outputs: ["changed"]), sequence: 1, checks: &checks)
 let ids = checks.map(\.id); for lease in app.stats.leases { try expired(lease) }
 let failed = application(failTransition: true); var failedChecks = [Check.passed("stale")]; var threw = false
 do { try checkObservedInto(failed.model, before: 0, input: 1, transition: Transition(state: 1, outputs: ["changed"]), sequence: 1, checks: &failedChecks) }
 catch { guard String(describing: error).contains("application checker failure") else { throw error }; threw = true }
 guard threw else { throw ModelError("checker error missing") }; for lease in failed.stats.leases { try expired(lease) }
 let nested = application(nested: true); var nestedChecks: [Check] = []
 try checkObservedInto(nested.model, before: 0, input: 1, transition: Transition(state: 1, outputs: ["changed"]), sequence: 1, checks: &nestedChecks)
 return ["ids": ids, "step_calls": app.stats.stepCalls, "state_calls": app.stats.stateCalls, "transition_calls": app.stats.transitionCalls,
  "error_cleared": failedChecks.isEmpty, "error_step_calls": failed.stats.stepCalls, "error_state_calls": failed.stats.stateCalls, "error_transition_calls": failed.stats.transitionCalls,
  "escaped_sink_expired": true, "escaped_operations": ["push", "extend", "snapshot", "length", "reserve", "is_empty"], "nested_child_expired": nested.stats.nestedExpired,
  "outer_active_after_nested": nested.stats.outerActive, "legacy_calls": app.stats.legacyCalls + failed.stats.legacyCalls + nested.stats.legacyCalls]
}
guard CommandLine.arguments.count == 3 else { throw ModelError("expected record|replay PATH") }
let mode = CommandLine.arguments[1], path = CommandLine.arguments[2]
let receipt: [String: Any]
if mode == "record" {
 let control = try controls(), app = application(), bfs = try enumerateStates(app.model)
 let found = try fuzz(app.generated, config: FuzzConfig(seed: 0, cases: 4, maxSteps: 32, maxTransitions: 128, mutationPercent: 0))
 guard let bfsFailure = bfs.failure, let failure = found.failure else { throw ModelError("search witness missing") }
 let reduced = try shrink(app.generated, original: failure)
 guard reduced.validatedOriginal, reduced.minimized.inputs == [1, 1, 1] else { throw ModelError("shrink witness mismatch") }
 let trace = try record(app.model, inputs: InputIterator(values: reduced.minimized.inputs)), restored = try parseTrace(encodeTrace(trace)), result = try replay(app.model, trace: restored)
 guard result.outcome == "exact", result.failureReproduced else { throw ModelError("local witness replay mismatch") }
 try Data(encodeTrace(trace)).write(to: URL(fileURLWithPath: path))
 receipt = ["controls": control, "bfs": ["states": bfs.states, "transitions": bfs.transitions, "witness": bfsFailure.inputs] as [String: Any],
  "fuzz": ["original_steps": failure.inputs.count, "minimized_steps": reduced.minimized.inputs.count, "shrink_attempts": reduced.attempts, "validated_original": reduced.validatedOriginal] as [String: Any],
  "legacy_calls": app.stats.legacyCalls, "append_state_calls": app.stats.stateCalls, "append_transition_calls": app.stats.transitionCalls]
} else if mode == "replay" {
 let app = application(), trace = try parseTrace(Array(Data(contentsOf: URL(fileURLWithPath: path)))), result = try replay(app.model, trace: trace)
 receipt = ["outcome": result.outcome, "failure_reproduced": result.failureReproduced, "steps": trace.steps.count, "legacy_calls": app.stats.legacyCalls,
  "append_state_calls": app.stats.stateCalls, "append_transition_calls": app.stats.transitionCalls]
} else { throw ModelError("expected record or replay") }
print(String(decoding: try JSONSerialization.data(withJSONObject: receipt, options: [.sortedKeys]), as: UTF8.self))

import Foundation
import Statelessness
final class Counter { var calls = 0 }
func integer(_ data: [UInt8]) throws -> Int {
 guard let text = String(bytes: data, encoding: .utf8), let n = Int(text) else { throw ModelError("invalid integer") }; return n
}
func input(_ data: [UInt8]) throws -> Int {
 guard data == [48] || data == [49] else { throw ModelError("expected zero or one") }; return try integer(data)
}
func model(_ counter: Counter) -> Model<Int, Int, String> {
 Model(initialState: { counter.calls += 1; return 0 }, step: { state, input in
  counter.calls += 1; guard input == 0 || input == 1 else { throw ModelError("expected zero or one") }
  return Transition(state: state + input, outputs: input == 1 ? ["changed"] : [])
 }, checkState: { state in counter.calls += 1; return [state >= 3 ? .failed("threshold.bound", "at least three") : .passed("threshold.bound")] },
 cloneState: { counter.calls += 1; return $0 }, cloneInput: { counter.calls += 1; return $0 }, cloneOutput: { counter.calls += 1; return $0 },
 inputs: { _ in counter.calls += 1; return InputIterator(values: [0, 1]) }, equalStates: { counter.calls += 1; return $0 == $1 },
 codec: Codec(metadata: { Metadata(name: "native-bfs-threshold", build: "application-v1") },
  encodeState: { Array(String($0).utf8) }, decodeState: integer,
  encodeInput: { Array(String($0).utf8) }, decodeInput: input, encodeOutput: { Array($0.utf8) }),
 estimatedStateBytes: { _ in counter.calls += 1; return 5 })
}
func search(cap: UInt64? = nil, cancelled: Bool = false) throws -> ([String: Any], Failure<Int>?) {
 let counter = Counter(); var initials = 0, errors = 0; var histories: [[Int]] = []
 let observer = SearchObserver<Int, Int, String>(initial: { _, _ in initials += 1 }, checkError: { _, _, _ in errors += 1 },
  transition: { _, _, _, _, _, history in histories.append(history) }, needsHistory: { true })
 let result = try enumerateStates(model(counter), config: SearchConfig(maxStates: 100, maxTransitions: 100, maxDepth: 10),
  limits: SearchLimits(control: RunLimits(isCancelled: { cancelled }), maxEstimatedStateBytes: cap), observer: observer)
 let violation = result.failure?.violations.first
 let receipt: [String: Any] = ["termination": result.termination, "states": result.states, "transitions": result.transitions,
  "estimated_retained_state_bytes": result.estimatedRetainedStateBytes.map { $0 as Any } ?? NSNull(),
  "witness": result.failure.map { $0.inputs as Any } ?? NSNull(),
  "failure_phase": violation.map { $0.phase as Any } ?? NSNull(), "failure_property": violation.map { $0.check.id as Any } ?? NSNull(),
  "observed_histories": histories, "initial_observations": initials, "check_errors": errors,
  "model_callbacks": counter.calls, "observer_callbacks": initials + histories.count + errors]
 return (receipt, result.failure)
}
guard CommandLine.arguments.count == 3 else { throw ModelError("expected record|replay PATH") }
let mode = CommandLine.arguments[1], path = CommandLine.arguments[2]
let receipt: [String: Any]
if mode == "record" {
 let (base, failure) = try search(), (cap, _) = try search(cap: 14), (precancel, _) = try search(cancelled: true)
 guard let failure else { throw ModelError("expected BFS witness") }
 let trace = try record(model(Counter()), inputs: InputIterator(values: failure.inputs))
 try Data(encodeTrace(trace)).write(to: URL(fileURLWithPath: path))
 receipt = ["base": base, "cap": cap, "precancel": precancel]
} else if mode == "replay" {
 let trace = try parseTrace(Array(Data(contentsOf: URL(fileURLWithPath: path)))), result = try replay(model(Counter()), trace: trace)
 receipt = ["outcome": result.outcome, "failure_reproduced": result.failureReproduced, "steps": trace.steps.count]
} else { throw ModelError("expected record or replay") }
print(String(decoding: try JSONSerialization.data(withJSONObject: receipt, options: [.sortedKeys]), as: UTF8.self))

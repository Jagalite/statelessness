import Foundation
import Statelessness

func text(_ bytes: [UInt8]) throws -> String {
 guard let result = String(bytes: bytes, encoding: .utf8) else { throw ModelError("invalid UTF-8") }
 return result
}
func integer(_ bytes: [UInt8]) throws -> Int {
 guard let result = Int(try text(bytes)) else { throw ModelError("invalid integer") }; return result
}
func input(_ bytes: [UInt8]) throws -> Int {
 guard bytes == [48] || bytes == [49] else { throw ModelError("expected zero or one") }
 return try integer(bytes)
}
guard CommandLine.arguments.count == 3 else { throw ModelError("usage: consumer record|replay PATH") }
let mode = CommandLine.arguments[1], path = CommandLine.arguments[2]
let model = Model<Int, Int, String>(initialState: { 0 }, step: { state, input in
 guard input == 0 || input == 1 else { throw ModelError("expected zero or one") }
 return Transition(state: state + input, outputs: input == 1 ? ["changed"] : [])
}, checkState: { state in [state >= 3 ? .failed("threshold.bound", "at least three") : .passed("threshold.bound")] },
 cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
 codec: Codec(metadata: { Metadata(name: "native-search-threshold", modelVersion: 2, build: "application-v2") },
 encodeState: { Array(String($0).utf8) }, decodeState: integer,
 encodeInput: { Array(String($0).utf8) }, decodeInput: input, encodeOutput: { Array($0.utf8) }))
let generated = Generate(model: model, generate: { _, rng in 1 - Int(rng.index(2)!) }, isEnabled: { _, input in input == 0 || input == 1 }, equalInputs: { $0 == $1 })
var searchReceipt: [String: Any] = [:]
if mode == "record" {
 let found = try fuzz(generated, config: FuzzConfig(seed: 0, cases: 4, maxSteps: 32, maxTransitions: 128, mutationPercent: 0))
 guard found.termination == "failure_found", let failure = found.failure else { throw ModelError("failure missing") }
 let originalTrace = try record(model, inputs: InputIterator(values: failure.inputs))
 try Data(encodeTrace(originalTrace)).write(to: URL(fileURLWithPath: path + ".original.json"))
 let reduced = try shrink(generated, original: failure)
 let target: (Failure<Int>) -> Bool = { f in f.violations.count == 1 && f.violations[0].phase == "state" && f.violations[0].check.id == "threshold.bound" }
 guard reduced.validatedOriginal, reduced.termination == "search_complete", reduced.minimized.inputs == [1, 1, 1],
  failure.inputs.count > 3, reduced.attempts > 0, target(failure), target(reduced.minimized) else { throw ModelError("shrink incomplete") }
 searchReceipt = ["original_steps": failure.inputs.count, "minimized_steps": reduced.minimized.inputs.count,
  "shrink_attempts": reduced.attempts, "validated_original": reduced.validatedOriginal, "phase": "state", "property": "threshold.bound"]
 let trace = try record(model, inputs: InputIterator(values: reduced.minimized.inputs))
 try Data(encodeTrace(trace)).write(to: URL(fileURLWithPath: path))
} else if mode != "replay" { throw ModelError("expected record or replay") }
let trace = try parseTrace(Array(Data(contentsOf: URL(fileURLWithPath: path))))
let result = try replay(model, trace: trace)
guard result.outcome == "exact", result.failureReproduced else { throw ModelError("failure did not replay") }
var receipt: [String: Any] = ["mode": mode, "outcome": result.outcome, "failure_reproduced": result.failureReproduced, "steps": trace.steps.count]
receipt.merge(searchReceipt) { _, new in new }
print(String(decoding: try JSONSerialization.data(withJSONObject: receipt, options: [.sortedKeys]), as: UTF8.self))

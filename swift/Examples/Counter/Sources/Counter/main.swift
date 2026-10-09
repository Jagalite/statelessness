import Foundation
import Statelessness

func text(_ bytes: [UInt8]) throws -> String {
    guard String(bytes: bytes, encoding: .utf8) != nil else { throw ModelError("invalid UTF-8") }
    // Validating and then decoding preserves a BOM that is part of the value.
    return String(decoding: bytes, as: UTF8.self)
}
let model = Model<Int, String, String>(
    initialState: { 0 },
    step: { state, input in
        guard input == "increment" else { throw ModelError("unknown input") }
        return Transition(state: state + 1, outputs: ["changed"])
    },
    checkState: { state in [state > 2 ? .failed("counter.bound", "above two") : .passed("counter.bound")] },
    cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
    inputs: { _ in InputIterator(values: ["increment"]) }, equalStates: { $0 == $1 },
    codec: Codec(
        metadata: { Metadata(name: "counter", build: "example-v1") },
        encodeState: { Array(String($0).utf8) },
        decodeState: { bytes in guard let n = Int(try text(bytes)) else { throw ModelError("invalid integer") }; return n },
        encodeInput: { Array($0.utf8) }, decodeInput: text, encodeOutput: { Array($0.utf8) }
    )
)
let result = try enumerateStates(model)
guard let failure = result.failure, failure.inputs.count == 3 else { throw ModelError("expected three-step witness") }
let trace = try record(model, inputs: InputIterator(values: failure.inputs))
let replayed = try replay(model, trace: parseTrace(encodeTrace(trace)))
guard replayed.outcome == "exact", replayed.failureReproduced else { throw ModelError("failure did not replay") }
print(result.termination, failure.inputs, replayed.outcome)

import Statelessness

@main
enum GuidedExample {
    static func main() throws {
        func decode(_ bytes: [UInt8]) throws -> Int {
            guard bytes.count == 1 else { throw ModelError("expected one byte") }
            return Int(bytes[0])
        }
        let model = Model<Int, Int, Int>(initialState: { 0 }, step: { s, i in Transition(state: s + i, outputs: [s + i]) },
            checkState: { s in [s >= 3 ? .failed("depth", "three reached") : .passed("depth")] },
            cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, equalStates: ==,
            codec: Codec(metadata: { Metadata(name: "guided-counter", build: "example-v1") },
                encodeState: { [UInt8($0)] }, decodeState: decode,
                encodeInput: { [UInt8($0)] }, decodeInput: decode, encodeOutput: { [UInt8($0)] }))
        let generator = Generate(model: model, generate: { _, _ in 1 }, equalInputs: ==)
        let feedback = StateFeedback<Int, Int>(metadata: { FeedbackMetadata(name: "depth", build: "example-v1") },
            cloneFeature: { $0 }, project: { $0 })
        let report = try guidedFuzz(generator, config: FuzzConfig(seed: 42, cases: 10, maxSteps: 5), feedback: feedback)
        guard let guidance = report.guidance, let failure = report.failure else { throw ModelError("expected guided failure") }
        for witness in guidance.corpus {
            let trace = try record(model, inputs: InputIterator(values: witness.inputs))
            guard trace.termination == .completed,
                  try replay(model, trace: trace).outcome == "exact" else { throw ModelError("passing witness failed replay") }
        }
        let reduced = try shrink(generator, original: failure)
        let trace = try record(model, inputs: InputIterator(values: reduced.minimized.inputs))
        let replayed = try replay(model, trace: parseTrace(encodeTrace(trace)))
        guard replayed.failureReproduced else { throw ModelError("failure did not replay") }
        print(guidance.algorithm, guidance.uniqueFeatures, guidance.corpus.count, report.termination, replayed.outcome)
    }
}

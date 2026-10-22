import Foundation
import Statelessness

func require(_ condition: Bool, _ message: String) throws { if !condition { throw ModelError(message) } }
func rejectsVersion(_ operation: () throws -> Void) throws { do { try operation() } catch { try require(String(describing: error).contains("version does not match"), "wrong version rejection"); return }; throw ModelError("expected version rejection") }
func rejects(_ operation: () throws -> Void) throws { do { try operation() } catch { return }; throw ModelError("expected rejection") }
struct State { let count: UInt32 }
enum Input { case add(UInt8) }
struct Output { let count: UInt32 }
final class Counters {
    var reducer = 0, state = 0, transition = 0, initial = 0
    var stateEncode = 0, stateDecode = 0, inputEncode = 0, inputDecode = 0, outputEncode = 0, limits = 0
    var checks: [Int] { [reducer, state, transition, initial] }
    var codecs: [Int] { [stateEncode, stateDecode, inputEncode, inputDecode, outputEncode, limits] }
}
final class Application {
    let c = Counters()
    var version: UInt32 = 1
    var limits = DecodeLimits()
    var model: Model<State,Input,Output>!
    var codec: Codec<State,Input,Output>!
    init(customLimits: Bool = true) throws {
        let fields = TraceFields.first(TraceField<State,UInt32>.ordinary("count", ValueCodecs.u32, get: { $0.count }))
        let record = try TraceRecords.codec(schema: "State", fields: fields, construct: { State(count: $0) }, require: .both)
        let state = TraceValueCodec<State>(schema: "State", encode: { [self] value,sink in c.stateEncode += 1; try record.encodeRaw(value, into: sink) }, decode: { [self] reader in c.stateDecode += 1; return try record.decode(from: reader) })
        let add = TraceEnumCase<Input,UInt8>(tag: 1, label: "Add", payload: ValueCodecs.u8, construct: { .add($0) })
        let enumeration = try TraceEnums.codec(schema: "Input", tagWidth: .u8, cases: [add.erased()], select: { value in switch value { case .add(let amount): return add.select(amount) } }, require: .both)
        let input = TraceValueCodec<Input>(schema: "Input", encode: { [self] value,sink in c.inputEncode += 1; try enumeration.encodeRaw(value, into: sink) }, decode: { [self] reader in c.inputDecode += 1; return try enumeration.decode(from: reader) })
        let output = TraceValueCodec<Output>(schema: "Output", encodeOnly: { [self] value,sink in c.outputEncode += 1; try ValueCodecs.u32.encodeRaw(value.count, into: sink) })
        try require(!output.supportsDecoding, "Output must have no decoder")
        let metadata = { [self] in Metadata(name: "n13-installed-model", codecVersion: version, build: "application-v1") }
        if customLimits {
            codec = try ModelCodecBinding(metadata: metadata, state: state, input: input, output: output, decodeLimits: { [self] in c.limits += 1; return limits }).codec()
        } else {
            codec = try ModelCodecBinding(metadata: metadata, state: state, input: input, output: output).codec()
        }
        model = Model(initialState: { [self] in c.initial += 1; return State(count: 0) }, step: { [self] state,input in
            c.reducer += 1
            switch input { case .add(let amount): let n = state.count + UInt32(amount); return Transition(state: State(count: n), outputs: [Output(count: n)]) }
        }, checkState: { [self] state in c.state += 1; return [state.count >= 3 ? .failed("threshold.bound", "at least three") : .passed("threshold.bound")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, checkTransition: { [self] _,_,_ in c.transition += 1; return [.passed("edge.checked")] }, codec: codec)
    }
}
let config = RunConfig(strategy: "manual", seed: 7, parameters: [AuditParameter("region", "test")])
let provenance = Provenance(run: "n13-installed-run", epoch: 7, manifest: "manifest-v1", source: "consumer-source", environment: [AuditParameter("region", "test")])
func binaryLimits() -> BinaryTraceLimits {
    var result = BinaryTraceLimits()
    result.maxTotalBytes = 16384; result.maxFrameBytes = 4096; result.maxBlobBytes = 64
    result.maxStringBytes = 256; result.maxSteps = 16; result.maxChecksPerStep = 16
    result.maxOutputsPerStep = 16; result.maxParameters = 32; result.maxItems = 256
    return result
}
func evidenceLimits() -> EvidenceLimits {
    var result = EvidenceLimits(); result.trace = binaryLimits(); result.maxMetadataBytes = 1024
    result.maxLabels = 8; result.maxLabelBytes = 128; result.maxCausesPerStep = 4; result.maxTotalCauses = 8
    return result
}
func gates() throws {
    let defaults = try Application(customLimits: false)
    _ = try defaults.codec.decodeState([0,0,0,0]); _ = try defaults.codec.decodeInput([1,1])
    try require(defaults.c.limits == 0, "default limits")
    let app = try Application(); let codec = app.codec!
    app.version = 2
    for operation in [{ _ = try codec.encodeState(State(count: .max)) }, { _ = try codec.encodeInput(.add(.max)) }, { _ = try codec.encodeOutput(Output(count: .max)) }, { _ = try codec.decodeState([]) }, { _ = try codec.decodeInput([255]) }] { try rejectsVersion(operation) }
    var bytes: [UInt8] = []
    try EncodeBuffer.withBuffer(&bytes, maximum: 1) { sink in
        try sink.extend([99])
        try rejectsVersion { try codec.encodeStateInto!(State(count: .max), sink) }
        try rejectsVersion { try codec.encodeInputInto!(.add(.max), sink) }
        try rejectsVersion { try codec.encodeOutputInto!(Output(count: .max), sink) }
    }
    try require(bytes == [99] && app.c.codecs == [0,0,0,0,0,0], "version gate performed codec work")
    app.version = 1
    _ = try codec.decodeState([0,0,0,0]); _ = try codec.decodeInput([1,1])
    app.limits = DecodeLimits(bytes: 4, depth: 2, work: 2)
    _ = try codec.decodeState([0,0,0,0])
    app.limits = DecodeLimits(bytes: 3)
    try rejects { _ = try codec.decodeState([0,0,0,0]) }
    app.limits = DecodeLimits(bytes: 2)
    _ = try codec.decodeInput([1,1])
    app.limits = DecodeLimits(bytes: 1)
    try rejects { _ = try codec.decodeInput([1,1]) }
    app.limits = DecodeLimits()
    for value in [[UInt8](), [0,0,0], [0,0,0,0,0]] { try rejects { _ = try codec.decodeState(value) } }
    for value in [[UInt8](), [1], [1,1,0], [255]] { try rejects { _ = try codec.decodeInput(value) } }
    let before = app.c.limits
    app.limits = DecodeLimits(bytes: 0, depth: 0, work: 0)
    _ = try codec.encodeState(State(count: 1)); _ = try codec.encodeInput(.add(1)); _ = try codec.encodeOutput(Output(count: 1))
    try EncodeBuffer.withBuffer(&bytes, maximum: 20) { sink in try codec.encodeStateInto!(State(count: 1), sink); try codec.encodeInputInto!(.add(1), sink); try codec.encodeOutputInto!(Output(count: 1), sink) }
    try require(app.c.limits == before, "encoders consulted limits")
}
@MainActor func produce() throws -> [String:String] {
    let app = try Application()
    var state = State(count: 0)
    let initial = try checkInitial(app.model, state: state)
    let recorder = try Recorder(checkedInitial: initial, config: config, options: RecorderOptions(maxSteps: 3, maxRetainedBytes: 16384, limits: binaryLimits()))
    let evidence = try EvidenceRecorder(checkedInitial: initial, config: config, options: RecorderOptions(maxSteps: 3, maxRetainedBytes: 16384, limits: binaryLimits()), provenance: provenance, limits: evidenceLimits())
    try require(app.c.checks == [0,1,0,0], "initial handoff repeated checks")
    for sequence in 1...3 {
        let input = Input.add(1), turn = try app.model.step(state,input)
        let checked = try checkTurn(app.model, before: state, input: input, transition: turn, sequence: UInt64(sequence))
        let counts = app.c.checks
        try recorder.observeChecked(checked)
        try evidence.observe(checked, causal: CausalContext(machine: "worker-a", generation: 9, correlation: "request-1", logicalTime: UInt64(10 + sequence), causes: sequence == 1 ? [] : [OutputOrigin(sequence: UInt64(sequence - 1), output: 0)]))
        try require(app.c.checks == counts, "checked handoff reran reducer/checker")
        state = turn.state
    }
    try require(app.c.checks == [3,4,3,0], "producer callback counts")
    try require(app.c.codecs == [14,0,6,0,6,0], "producer codec counts")
    let v1 = try recorder.intoTrace(), v2 = try evidence.intoTrace()
    try require(v1.steps.map { $0.outputs } == [[[1,0,0,0]],[[2,0,0,0]],[[3,0,0,0]]], "output bytes")
    try require(v2.checkPhase(sequence: 3, checkIndex: 0) == .state && v2.checkPhase(sequence: 3, checkIndex: 1) == .transition, "phase evidence")
    return ["v1": hex(try encodeAuditTrace(v1, limits: binaryLimits())), "v2": hex(try encodeEvidence(v2, limits: evidenceLimits()))]
}
func hex(_ bytes: [UInt8]) -> String { bytes.map { String(format: "%02x", $0) }.joined() }
func unhex(_ value: String) throws -> [UInt8] { let a = Array(value.utf8); try require(a.count % 2 == 0, "odd hex"); return try stride(from: 0, to: a.count, by: 2).map { i in guard let value = UInt8(String(decoding: a[i..<i+2], as: UTF8.self), radix: 16) else { throw ModelError("invalid hex") }; return value } }
@MainActor func transcode(_ source: [String:String]) throws -> [String:String] {
    var result: [String:String] = [:]
    for version in ["v1", "v2"] {
        let bytes = try unhex(source[version]!), artifact = try decodeArtifact(bytes, limits: evidenceLimits()), app = try Application()
        let trace = artifact.trace
        try require(trace.metadata == Metadata(name: "n13-installed-model", build: "application-v1"), "metadata")
        try require(trace.config.strategy == "manual" && trace.config.seed == 7 && trace.config.parameters.contains(AuditParameter("region", "test")), "producer config")
        var observations = 0
        let report = try replayWithObservationsBounded(app.model, trace: trace, limits: binaryLimits()) { observation,_ in
            observations += 1
            if case let .turn(turn) = observation { try require(turn.transition.state.count == UInt32(turn.sequence), "meaningful turn observation") }
        }
        try require(report.outcome == "exact" && report.stepsVerified == 3 && report.failureReproduced && observations == 4, "replay report")
        try require(app.c.checks == [3,4,3,0] && app.c.codecs == [4,1,3,3,3,4], "replay callback counts")
        let rewritten: [UInt8]
        switch artifact {
        case .v1: rewritten = try encodeAuditTrace(trace, limits: binaryLimits())
        case .v2(let evidence):
            try require(evidence.provenance == provenance && evidence.steps.count == 3 && evidence.checkPhase(sequence: 3, checkIndex: 0) == .state, "causal provenance/phase")
            let fresh = try Application(), report = try evidence.replay(fresh.model, limits: evidenceLimits())
            try require(report.outcome == "exact" && report.failureReproduced && fresh.c.checks == [3,4,3,0] && fresh.c.codecs == [4,1,3,3,3,4], "causal replay/counts")
            rewritten = try encodeEvidence(evidence, limits: evidenceLimits())
        }
        try require(rewritten == bytes, "artifact rewrite changed bytes/config")
        result[version] = hex(rewritten)
    }
    return result
}
do {
    try gates()
    let args = CommandLine.arguments
    let result: [String:String]
    if args.count == 3 && args[1] == "produce" { result = try produce() }
    else if args.count == 4 && args[1] == "transcode" { result = try transcode(JSONDecoder().decode([String:String].self, from: Data(contentsOf: URL(fileURLWithPath: args[2])))) }
    else { throw ModelError("usage: ModelConsumer produce DEST | transcode SOURCE DEST") }
    let destination = args.last!
    try JSONEncoder().encode(result).write(to: URL(fileURLWithPath: destination))
    print("{\"engine\":\"swift\",\"version_gates\":8,\"output_decoder\":false,\"checked_handoff\":true}")
} catch { FileHandle.standardError.write(Data("\(error)\n".utf8)); exit(1) }

import XCTest
@testable import Statelessness

final class DeriveCodecTests: XCTestCase {
    struct Pair: Equatable { let first: UInt8; let second: UInt16 }
    func pair(_ constructed: @escaping () -> Void = {}) throws -> TraceValueCodec<Pair> {
        let fields = TraceFields.first(TraceField<Pair, UInt8>.ordinary("first", ValueCodecs.u8, get: { $0.first }))
            .appending(TraceField<Pair, UInt16>.ordinary("second", ValueCodecs.u16, get: { $0.second }))
        return try TraceRecords.codec(schema: "Pair", fields: fields, construct: { constructed(); return Pair(first: $0.prefix, second: $0.last) }, require: .both)
    }
    func testOrderedRecordConstructorAndLimits() throws {
        var calls = 0
        let codec = try pair { calls += 1 }
        XCTAssertNil(codec.layout)
        XCTAssertFalse(codec.supportsLengthEncoding)
        XCTAssertEqual(try codec.traceBytes(Pair(first: 3, second: 0x1234), maximum: 3), [3, 0x34, 0x12])
        XCTAssertEqual(try codec.fromTrace([3, 0x34, 0x12], limits: DecodeLimits(depth: 2, work: 3)), Pair(first: 3, second: 0x1234))
        XCTAssertEqual(calls, 1)
        for bytes in [[UInt8](), [3], [3, 0x34]] { XCTAssertThrowsError(try codec.fromTrace(bytes)) }
        XCTAssertEqual(calls, 1)
        XCTAssertThrowsError(try codec.fromTrace([3, 0x34, 0x12], limits: DecodeLimits(depth: 1)))
        XCTAssertThrowsError(try codec.fromTrace([3, 0x34, 0x12, 0]))
    }
    func testIndependentCapabilitiesAndRawFieldModes() throws {
        let enc = TraceValueCodec<UInt8>(schema: "enc", encodeOnly: { try $1.extend([$0]) })
        let dec = TraceValueCodec<UInt8>(schema: "dec", decodeOnly: { UInt8(try $0.take(1).first!) })
        let fields = TraceFields.first(TraceField<Pair, UInt8>.ordinary("a", enc, get: { $0.first }))
            .appending(TraceField<Pair, UInt8>.ordinary("b", dec))
        let neither = try TraceRecords.codec(schema: "neither", fields: fields, construct: { Pair(first: $0.prefix, second: UInt16($0.last)) })
        XCTAssertFalse(neither.supportsEncoding); XCTAssertFalse(neither.supportsDecoding)
        XCTAssertThrowsError(try TraceRecords.codec(schema: "bad", fields: fields, require: .encoding))
        let adapter = TraceFields.first(TraceField<UInt8, UInt8>.adapter("raw", ValueCodecs.u8, get: { $0 }))
        let raw = try TraceRecords.codec(schema: "raw", fields: adapter, construct: { $0 }, require: .encoding)
        XCTAssertTrue(raw.supportsDecoding)
        XCTAssertEqual(try raw.fromTrace([7], limits: DecodeLimits(depth: 1, work: 1)), 7)
        let lengthOnly = try TraceValueCodec<String>(schema: "length", encodingLength: { try ValueCodecs.string.encodeLengthRaw($0, width: $1, into: $2) }, decodingLength: { try ValueCodecs.string.decodeLength(width: $0, from: $1) })
        let lengthFields = TraceFields.first(TraceField<String, String>.length("text", lengthOnly, width: .u8, get: { $0 }))
        let text = try TraceRecords.codec(schema: "text", fields: lengthFields, construct: { $0 })
        XCTAssertEqual(try text.traceBytes("x", maximum: 2), [1,120])
        XCTAssertEqual(try text.fromTrace([1,120], limits: DecodeLimits(depth: 2, work: 3)), "x")
    }
    enum Choice: Equatable { case unit, number(UInt16) }
    func testExplicitEnumRawPayloadAndMembership() throws {
        let unit = TraceEnumCase<Choice, TraceUnit>(tag: 255, label: "idle", payload: ValueCodecs.unit, construct: { _ in .unit })
        let number = TraceEnumCase<Choice, UInt16>(tag: 7, label: "n", payload: ValueCodecs.u16, construct: { .number($0) })
        let codec = try TraceEnums.codec(schema: "Choice", tagWidth: .u8, cases: [unit.erased(), number.erased()], select: { value in switch value { case .unit: return unit.select(TraceUnit()); case .number(let n): return number.select(n) } })
        XCTAssertEqual(try codec.traceBytes(.number(0x1234), maximum: 3), [7,52,18])
        XCTAssertEqual(try codec.fromTrace([7,52,18], limits: DecodeLimits(depth: 2, work: 2)), .number(0x1234))
        XCTAssertEqual(try codec.fromTrace([255], limits: DecodeLimits(depth: 2, work: 2)), .unit)
        XCTAssertThrowsError(try codec.fromTrace([9])) { XCTAssertTrue(String(describing: $0).contains("unknown enum tag")) }
        XCTAssertEqual(codec.traceVariant(.unit), "idle")
        XCTAssertThrowsError(try TraceEnums.codec(schema: "duplicate", tagWidth: .u8, cases: [unit.erased(), unit.erased()]))
        let foreign = TraceEnumCase<Choice, TraceUnit>(tag: 255, label: "foreign", payload: ValueCodecs.unit)
        let invalid = try TraceEnums.codec(schema: "invalid", tagWidth: .u8, cases: [unit.erased()], select: { _ in foreign.select(TraceUnit()) })
        var bytes: [UInt8] = []
        try EncodeBuffer.withBuffer(&bytes, maximum: 10) { sink in XCTAssertThrowsError(try invalid.encodeRaw(.unit, into: sink)) }
        XCTAssertTrue(bytes.isEmpty)
    }

    func testRawErrorsAndUnavailableCaseAdmission() throws {
        let bad = TraceValueCodec<UInt8>(schema: "bad", encodeOnly: { _, sink in try sink.extend([8]); throw ModelError("application callback") })
        let fields = TraceFields.first(TraceField<UInt8, UInt8>.ordinary("bad", bad, get: { $0 }))
        let record = try TraceRecords.codec(schema: "bad", fields: fields)
        var bytes: [UInt8] = []
        try EncodeBuffer.withBuffer(&bytes, maximum: 8) { sink in
            XCTAssertThrowsError(try record.encodeRaw(1, into: sink)) { XCTAssertTrue(String(describing: $0).contains("application callback")) }
            try sink.extend([9])
        }
        XCTAssertEqual(bytes, [8,9])
        let unit: TraceValueCodec<UInt8> = try TraceRecords.unit(schema: "unit", construct: { 4 })
        XCTAssertEqual(try unit.fromTrace([], limits: DecodeLimits(depth: 1, work: 1)), 4)
        let unsupported = TraceEnumCase<Choice, UInt8>(tag: 1, label: "unsupported", payload: bad)
        let available = TraceEnumCase<Choice, TraceUnit>(tag: 0, label: "unit", payload: ValueCodecs.unit, construct: { _ in .unit })
        let enumCodec = try TraceEnums.codec(schema: "mixed", tagWidth: .u8, cases: [available.erased(), unsupported.erased()], select: { _ in available.select(TraceUnit()) })
        XCTAssertTrue(enumCodec.supportsEncoding); XCTAssertFalse(enumCodec.supportsDecoding)
        XCTAssertThrowsError(try TraceEnums.codec(schema: "mixed", tagWidth: .u8, cases: [available.erased(), unsupported.erased()], require: .decoding))
        let reader = try ValueDecoder([0], limits: DecodeLimits(work: 0))
        XCTAssertThrowsError(try reader.value(enumCodec)) { XCTAssertTrue(String(describing: $0).contains("does not support decoding")) }
        XCTAssertEqual(try reader.take(1).first, 0)
        XCTAssertThrowsError(try TraceRecords.unit(schema: "invalid", construct: { 0 }, layout: Rust64ValueLayout(size: 3, identity: "bad", alignment: 2)))
    }

    func testEncodeOnlyOutputThroughRecorderAndReplay() throws {
        let output = TraceValueCodec<UInt8>(schema: "output", encodeOnly: { try $1.extend([$0]) })
        let codec = try ModelCodecBinding(metadata: { Metadata(name: "bound", build: "test") }, state: ValueCodecs.u8, input: ValueCodecs.u8, output: output).codec()
        let model = Model<UInt8, UInt8, UInt8>(initialState: { 1 }, step: { state, input in Transition(state: state + input, outputs: [input]) }, checkState: { _ in [] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, codec: codec)
        let recorder = try Recorder(model: model, state: 1, config: RunConfig(strategy: "runtime"))
        try recorder.observe(model: model, before: 1, input: 2, transition: Transition(state: 3, outputs: [2]))
        let trace = recorder.snapshot()
        XCTAssertEqual(trace.steps[0].outputs, [[2]])
        let encoded = try encodeAuditTrace(trace)
        XCTAssertFalse(encoded.isEmpty)
        var observations = 0
        _ = try replayWithObservationsBounded(model, trace: trace) { _,_ in observations += 1 }
        XCTAssertEqual(observations, 2)
    }
    func testBinderAllEightVersionGatesAndFreshLimits() throws {
        var metadataCalls = 0, limitsCalls = 0, version: UInt32 = 2, valueCalls = 0
        let value = TraceValueCodec<UInt8>(schema: "value", encode: { value, sink in valueCalls += 1; try sink.extend([value]) }, decode: { reader in valueCalls += 1; return try ValueCodecs.u8.decode(from: reader) })
        let output = TraceValueCodec<UInt8>(schema: "output", encodeOnly: { try $1.extend([$0]) })
        let binding = try ModelCodecBinding(metadata: { metadataCalls += 1; return Metadata(name: "test", codecVersion: version) }, state: value, input: value, output: output, decodeLimits: { limitsCalls += 1; return DecodeLimits(work: UInt64(limitsCalls)) })
        XCTAssertEqual(metadataCalls, 0); XCTAssertEqual(limitsCalls, 0)
        let codec = binding.codec()
        for operation in [codec.encodeState, codec.encodeInput, codec.encodeOutput] { XCTAssertThrowsError(try operation(4)) }
        XCTAssertThrowsError(try codec.decodeState([])); XCTAssertThrowsError(try codec.decodeInput([]))
        var bytes: [UInt8] = []
        try EncodeBuffer.withBuffer(&bytes, maximum: 10) { sink in
            try sink.extend([99])
            for operation in [codec.encodeStateInto!, codec.encodeInputInto!, codec.encodeOutputInto!] { XCTAssertThrowsError(try operation(4, sink)) }
        }
        XCTAssertEqual(metadataCalls, 8); XCTAssertEqual(valueCalls, 0); XCTAssertEqual(limitsCalls, 0); XCTAssertEqual(bytes, [99])
        version = 1
        XCTAssertEqual(try codec.decodeState([4]), 4); XCTAssertEqual(try codec.decodeInput([5]), 5)
        XCTAssertEqual(limitsCalls, 2)
        try EncodeBuffer.withBuffer(&bytes, maximum: 10) { try $0.extend([99]); try codec.encodeOutputInto!(6, $0) }
        XCTAssertEqual(bytes, [99, 6]); XCTAssertEqual(limitsCalls, 2)
    }
}

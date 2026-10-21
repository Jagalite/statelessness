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

extension DeriveCodecTests {
    func testLiteralLengthFieldsAndIndependentBudgetBoundaries() throws {
        for (width, text, bytes, work) in [(TraceLengthWidth.u8, "abc", [UInt8(3),97,98,99], UInt64(5)), (.u16, "é", [2,0,195,169], 4), (.u32, "\u{feff}", [3,0,0,0,239,187,191], 5), (.u8, "", [0], 2)] {
            var constructors = 0
            let fields = TraceFields.first(TraceField<String,String>.length("text", ValueCodecs.string, width: width, get: { $0 }))
            let codec = try TraceRecords.codec(schema: "Text", fields: fields, construct: { constructors += 1; return $0 })
            XCTAssertEqual(try codec.traceBytes(text, maximum: 100), bytes)
            let n = UInt64(text.utf8.count)
            XCTAssertEqual(try codec.fromTrace(bytes, limits: DecodeLimits(bytes: UInt64(bytes.count), allocation: n, elements: n, depth: 2, work: work)), text)
            var limits = [DecodeLimits(bytes: UInt64(bytes.count-1)), DecodeLimits(depth: 1), DecodeLimits(work: work-1)]
            if n > 0 { limits += [DecodeLimits(allocation: n-1), DecodeLimits(elements: n-1)] }
            for limit in limits { XCTAssertThrowsError(try codec.fromTrace(bytes, limits: limit)); XCTAssertEqual(constructors, 1) }
        }
        let fields = TraceFields.first(TraceField<[UInt16],[UInt16]>.length("words", ValueCodecs.vector(ValueCodecs.u16), width: .u8, get: { $0 }))
        let words = try TraceRecords.codec(schema: "Words8", fields: fields, construct: { $0 })
        XCTAssertEqual(try words.traceBytes([258,65535], maximum: 5), [2,2,1,255,255])
        XCTAssertEqual(try words.fromTrace([2,2,1,255,255], limits: DecodeLimits(allocation: 4, elements: 2, depth: 2, work: 4)), [258,65535])
        XCTAssertEqual(try words.fromTrace([0], limits: DecodeLimits(allocation: 0, elements: 0, depth: 2, work: 2)), [])
        let raw = try ValueDecoder([7,52,18], limits: DecodeLimits(depth: 1, work: 2))
        XCTAssertEqual(try pair().decode(from: raw), Pair(first: 7, second: 4660)); try raw.finish()
        let unit: TraceValueCodec<TraceUnit> = try TraceRecords.unit(schema: "RawUnit", construct: { TraceUnit() })
        let zero = try ValueDecoder([], limits: DecodeLimits(depth: 0, work: 0)); _ = try unit.decode(from: zero); try zero.finish()
        var callbacks = 0
        let admissionFields = TraceFields.first(TraceField<UInt8,UInt8>.adapter("x", TraceValueCodec(schema: "counter", decodeOnly: { r in callbacks += 1; return try ValueCodecs.u8.decode(from: r) })))
        let admission = try TraceRecords.codec(schema: "Admission", fields: admissionFields, construct: { $0 })
        XCTAssertThrowsError(try admission.fromTrace([1,2,3,4], limits: DecodeLimits(bytes: 3))); XCTAssertEqual(callbacks, 0)
        let weightedFields = TraceFields.first(TraceField<Pair,UInt8>.ordinary("a", ValueCodecs.u8, get: { $0.first })).appending(TraceField<Pair,UInt16>.ordinary("b", ValueCodecs.u16, get: { $0.second }))
        let weighted = try TraceRecords.codec(schema: "Pair", fields: weightedFields, construct: { Pair(first: $0.prefix, second: $0.last) }, layout: Rust64ValueLayout(size: 4, identity: "explicit cooperative Pair"))
        let vector = ValueCodecs.vector(weighted), bytes: [UInt8] = [2,0,0,0,7,52,18,1,2,1]
        XCTAssertEqual(try vector.fromTrace(bytes, limits: DecodeLimits(allocation: 8, elements: 2, depth: 3, work: 8)), [Pair(first: 7, second: 4660),Pair(first: 1, second: 258)])
        XCTAssertThrowsError(try vector.fromTrace(bytes, limits: DecodeLimits(allocation: 7)))
    }
    func testAllRawAndGuardedReaderScripts() throws {
        let idle = TraceEnumCase<Choice,TraceUnit>(tag: 7, label: "Idle", payload: ValueCodecs.unit, construct: { _ in .unit })
        let codec = try TraceEnums.codec(schema: "Event8", tagWidth: .u8, cases: [idle.erased()], select: { _ in idle.select(TraceUnit()) })
        let raw = try ValueDecoder([8,9]); XCTAssertThrowsError(try codec.decode(from: raw)); XCTAssertEqual(try raw.value(ValueCodecs.u8), 9); try raw.finish()
        let guarded = try ValueDecoder([8,9])
        XCTAssertThrowsError(try guarded.value(codec)); XCTAssertThrowsError(try guarded.value(ValueCodecs.u8)) { XCTAssertTrue(String(describing:$0).contains("unknown enum tag")) }; XCTAssertThrowsError(try guarded.finish())
        for rawFirst in [true,false] {
            var calls = 0
            let reader = try ValueDecoder([])
            if rawFirst { XCTAssertThrowsError(try codec.decode(from: reader)) }
            XCTAssertThrowsError(try reader.value { r -> UInt8 in calls += 1; XCTAssertThrowsError(try codec.decode(from: r)); throw ModelError("later callback error") }) { XCTAssertTrue(String(describing:$0).contains("truncated value")) }
            XCTAssertEqual(calls, rawFirst ? 0 : 1)
            XCTAssertThrowsError(try reader.finish()) { XCTAssertTrue(String(describing:$0).contains("truncated value")) }
        }
    }
    func testBoundCausalEvidenceUsesEncodeOnlyOutputWithoutRechecking() throws {
        var stateChecks = 0, edgeChecks = 0, reductions = 0, outputCalls = 0
        let output = TraceValueCodec<UInt8>(schema: "OnlyOutput", encodeOnly: { value, sink in outputCalls += 1; try sink.extend([value]) })
        let codec = try ModelCodecBinding(metadata: { Metadata(name: "bound-causal", build: "test") }, state: ValueCodecs.u8, input: ValueCodecs.u8, output: output).codec()
        let model = Model<UInt8,UInt8,UInt8>(initialState: { 0 }, step: { s,i in reductions += 1; return Transition(state: s+i, outputs: [s+i]) }, checkState: { _ in stateChecks += 1; return [.passed("state")] }, cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 }, checkTransition: { _,_,_ in edgeChecks += 1; return [.passed("edge")] }, codec: codec)
        let initial = try checkInitial(model, state: 0)
        let turn1 = try checkTurn(model, before: 0, input: 1, transition: Transition(state: 1, outputs: [1]), sequence: 1)
        let turn2 = try checkTurn(model, before: 1, input: 2, transition: Transition(state: 3, outputs: [3]), sequence: 2)
        XCTAssertEqual(stateChecks, 3); XCTAssertEqual(edgeChecks, 2); XCTAssertEqual(reductions, 0)
        let config = RunConfig(strategy: "manual")
        let evidence = try EvidenceRecorder(checkedInitial: initial, config: config)
        try evidence.observe(turn1); try evidence.observe(turn2, causal: CausalContext(causes: [OutputOrigin(sequence: 1, output: 0)]))
        XCTAssertEqual(stateChecks, 3); XCTAssertEqual(edgeChecks, 2); XCTAssertEqual(reductions, 0); XCTAssertEqual(outputCalls, 2)
        let snapshot = evidence.snapshot()
        XCTAssertEqual(snapshot.trace.config, config); XCTAssertEqual(snapshot.steps.map { $0.stateCheckCount }, [1,1]); XCTAssertEqual(snapshot.steps[1].causal.causes, [OutputOrigin(sequence: 1, output: 0)])
        XCTAssertEqual(snapshot.trace.steps.map { $0.outputs }, [[[1]],[[3]]])
        let bytes = try encodeEvidence(snapshot); XCTAssertEqual(try decodeArtifact(bytes), .v2(snapshot))
        XCTAssertEqual(try snapshot.replay(model).outcome, "exact")
        XCTAssertEqual(stateChecks, 6); XCTAssertEqual(edgeChecks, 4); XCTAssertEqual(reductions, 2); XCTAssertEqual(outputCalls, 4)
    }
}
extension DeriveCodecTests {
    func testBinderExactCustomFreshLimitsMalformedBothDirections() throws {
        var calls = 0, work: UInt64 = 3
        let p = try pair()
        let codec = try ModelCodecBinding(metadata: { Metadata(name: "limits") }, state: p, input: p, output: p, decodeLimits: { calls += 1; return DecodeLimits(bytes: 3, allocation: 0, elements: 0, depth: 2, work: work) }).codec()
        for decode in [codec.decodeState, codec.decodeInput] {
            XCTAssertEqual(try decode([7,52,18]), Pair(first: 7, second: 4660))
            work = 2; XCTAssertThrowsError(try decode([7,52,18])); work = 3
            XCTAssertEqual(try decode([7,52,18]), Pair(first: 7, second: 4660))
            for bytes in [[UInt8](),[7],[7,52],[7,52,18,0]] { XCTAssertThrowsError(try decode(bytes)) }
        }
        XCTAssertEqual(calls, 14)
        for encode in [codec.encodeState,codec.encodeInput,codec.encodeOutput] { XCTAssertEqual(try encode(Pair(first: 7, second: 4660)), [7,52,18]) }
        for encode in [codec.encodeStateInto!,codec.encodeInputInto!,codec.encodeOutputInto!] { var bytes: [UInt8] = []; try EncodeBuffer.withBuffer(&bytes, maximum: 4) { sink in try sink.extend([99]); try encode(Pair(first: 7, second: 4660),sink) }; XCTAssertEqual(bytes,[99,7,52,18]) }
        XCTAssertEqual(calls,14)
        let defaults = try ModelCodecBinding(metadata: { Metadata(name: "default") },state: p,input: p,output: p).codec()
        XCTAssertEqual(try defaults.decodeState([7,52,18]),Pair(first: 7,second: 4660))
        for decode in [defaults.decodeState, defaults.decodeInput] { XCTAssertThrowsError(try decode([7,52,18,0])) { XCTAssertTrue(String(describing: $0).contains("trailing value bytes")) } }
        let idle = TraceEnumCase<Choice,TraceUnit>(tag: 7,label:"Idle",payload:ValueCodecs.unit,construct:{ _ in .unit })
        let event = try TraceEnums.codec(schema:"Event8",tagWidth:.u8,cases:[idle.erased()],select:{ _ in idle.select(TraceUnit()) })
        let eventModel = try ModelCodecBinding(metadata:{ Metadata(name:"trailing") },state:event,input:event,output:event).codec()
        for decode in [eventModel.decodeState,eventModel.decodeInput] { XCTAssertThrowsError(try decode([7,0])) { XCTAssertTrue(String(describing:$0).contains("trailing value bytes")) } }
    }
    func testDerivedInvalidFieldsAndRawEncodingPrefixes() throws {
        var constructors = 0
        let boolFields = TraceFields.first(TraceField<Bool,Bool>.ordinary("ready", ValueCodecs.bool, get: { $0 }))
        let boolean = try TraceRecords.codec(schema: "Boolean",fields: boolFields,construct: { constructors += 1; return $0 })
        XCTAssertThrowsError(try boolean.fromTrace([2])); XCTAssertEqual(constructors,0)
        let textFields = TraceFields.first(TraceField<String,String>.length("text",ValueCodecs.string,width: .u8,get: { $0 }))
        let text = try TraceRecords.codec(schema: "Text8",fields: textFields,construct: { constructors += 1; return $0 })
        for bytes in [[UInt8(1),255],[3,97]] { XCTAssertThrowsError(try text.fromTrace(bytes)) }; XCTAssertEqual(constructors,0)
        var overflow: [UInt8] = []
        XCTAssertThrowsError(try EncodeBuffer.withBuffer(&overflow,maximum: 1024) { try text.encodeRaw(String(repeating:"a",count:256),into:$0) }); XCTAssertEqual(overflow,[])
        var capped: [UInt8] = []
        XCTAssertThrowsError(try EncodeBuffer.withBuffer(&capped,maximum: 2) { try pair().encodeRaw(Pair(first:7,second:4660),into:$0) }); XCTAssertEqual(capped,[7])
        for label in ["Idle","Waiting"] {
            let idle = TraceEnumCase<Choice,TraceUnit>(tag:7,label:label,payload:ValueCodecs.unit,construct:{ _ in .unit })
            let e = try TraceEnums.codec(schema:"labels",tagWidth:.u8,cases:[idle.erased()],select:{ _ in idle.select(TraceUnit()) })
            XCTAssertEqual(e.traceVariant(.unit),label); XCTAssertEqual(try e.traceBytes(.unit,maximum:1),[7])
        }
        XCTAssertNil(try pair().traceVariant(Pair(first:7,second:4660)))
        let bad = TraceEnumCase<Choice,TraceUnit>(tag:256,label:"bad",payload:ValueCodecs.unit,construct:{ _ in .unit })
        XCTAssertThrowsError(try TraceEnums.codec(schema:"overflow",tagWidth:.u8,cases:[bad.erased()]))
        XCTAssertThrowsError(try ModelCodecBinding(metadata:{ Metadata(name:"bad") },version:0,state:ValueCodecs.u8,input:ValueCodecs.u8,output:ValueCodecs.u8))
    }
}
extension DeriveCodecTests {
    struct DraftRecord: Equatable { let byte: UInt8; let ready: Bool; let count: UInt16 }
    struct DraftNested: Equatable { let marker: UInt8; let pair: Pair }
    struct DraftReady: Equatable { let ready: Bool; let byte: UInt8 }
    enum DraftEvent: Equatable { case idle, set(UInt16), ready(DraftReady), maximum(UInt8) }
    func testDraftNamedNestedOrdinaryAndDirectionalRows() throws {
        let recordFields = TraceFields.first(TraceField<DraftRecord,UInt8>.ordinary("byte",ValueCodecs.u8,get:{$0.byte}))
            .appending(TraceField<DraftRecord,Bool>.ordinary("ready",ValueCodecs.bool,get:{$0.ready}))
            .appending(TraceField<DraftRecord,UInt16>.ordinary("count",ValueCodecs.u16,get:{$0.count}))
        let record = try TraceRecords.codec(schema:"Record",fields:recordFields,construct:{ DraftRecord(byte:$0.prefix.prefix,ready:$0.prefix.last,count:$0.last) })
        let value = DraftRecord(byte:7,ready:true,count:4660)
        XCTAssertEqual(try record.traceBytes(value,maximum:4),[7,1,52,18]); XCTAssertEqual(try record.fromTrace([7,1,52,18],limits:DecodeLimits(allocation:0,elements:0,depth:3,work:5)),value)
        XCTAssertThrowsError(try record.fromTrace([7,2,52,18])); XCTAssertNil(record.traceVariant(value))
        var bytes: [UInt8] = []; XCTAssertThrowsError(try EncodeBuffer.withBuffer(&bytes,maximum:3) { try record.encodeRaw(value,into:$0) }); XCTAssertEqual(bytes,[7,1])
        let nestedFields = TraceFields.first(TraceField<DraftNested,UInt8>.ordinary("marker",ValueCodecs.u8,get:{$0.marker})).appending(TraceField<DraftNested,Pair>.ordinary("pair",try pair(),get:{$0.pair}))
        let nested = try TraceRecords.codec(schema:"Nested",fields:nestedFields,construct:{ DraftNested(marker:$0.prefix,pair:$0.last) })
        let n = DraftNested(marker:9,pair:Pair(first:1,second:258))
        XCTAssertEqual(try nested.traceBytes(n,maximum:4),[9,1,2,1]); XCTAssertEqual(try nested.fromTrace([9,1,2,1],limits:DecodeLimits(allocation:0,elements:0,depth:3,work:5)),n)
        let empty: TraceValueCodec<TraceUnit> = try TraceRecords.unit(schema:"Empty",construct:{ TraceUnit() }); XCTAssertEqual(try empty.traceBytes(TraceUnit(),maximum:0),[]); _ = try empty.fromTrace([],limits:DecodeLimits(allocation:0,elements:0,depth:1,work:1))
        let textFields = TraceFields.first(TraceField<String,String>.ordinary("text",ValueCodecs.string,get:{$0}))
        let text = try TraceRecords.codec(schema:"Text",fields:textFields,construct:{$0})
        XCTAssertEqual(try text.traceBytes("abc",maximum:7),[3,0,0,0,97,98,99]); XCTAssertEqual(try text.fromTrace([3,0,0,0,97,98,99],limits:DecodeLimits(allocation:3,elements:3,depth:3,work:6)),"abc")
        XCTAssertThrowsError(try text.fromTrace([3,0,0,0,97,98,99],limits:DecodeLimits(depth:2))); XCTAssertThrowsError(try text.fromTrace([3,0,0,0,97,98,99],limits:DecodeLimits(work:5)))
        let enc = TraceValueCodec<UInt8>(schema:"enc",encodeOnly:{ try $1.extend([$0]) }), dec = TraceValueCodec<UInt8>(schema:"dec",decodeOnly:{ try ValueCodecs.u8.decode(from:$0) })
        let encodeFields = TraceFields.first(TraceField<UInt8,UInt8>.ordinary("0",enc,get:{$0})), decodeFields = TraceFields.first(TraceField<UInt8,UInt8>.ordinary("0",dec,get:{$0}))
        let encodeRecord = try TraceRecords.codec(schema:"EncodeOnlyRecord",fields:encodeFields,require:.encoding), decodeRecord = try TraceRecords.codec(schema:"DecodeOnlyRecord",fields:decodeFields,construct:{$0},require:.decoding)
        XCTAssertTrue(encodeRecord.supportsEncoding); XCTAssertFalse(encodeRecord.supportsDecoding); XCTAssertEqual(try encodeRecord.traceBytes(7,maximum:1),[7])
        XCTAssertFalse(decodeRecord.supportsEncoding); XCTAssertTrue(decodeRecord.supportsDecoding); XCTAssertEqual(try decodeRecord.fromTrace([7],limits:DecodeLimits(allocation:0,elements:0,depth:2,work:2)),7)
        for payload in [encodeRecord,decodeRecord] {
            let idle = TraceEnumCase<Choice,TraceUnit>(tag:7,label:"Idle",payload:ValueCodecs.unit,construct:{ _ in .unit })
            let number = TraceEnumCase<Choice,UInt8>(tag:2,label:"Value",payload:payload,construct:{ .number(UInt16($0)) })
            let e = try TraceEnums.codec(schema:"Directional",tagWidth:.u8,cases:[idle.erased(),number.erased()],select:{ _ in idle.select(TraceUnit()) })
            XCTAssertEqual(e.supportsEncoding,payload.supportsEncoding); XCTAssertEqual(e.supportsDecoding,payload.supportsDecoding)
            if payload.supportsEncoding { XCTAssertEqual(try e.traceBytes(.unit,maximum:1),[7]); XCTAssertThrowsError(try e.fromTrace([7])); XCTAssertThrowsError(try TraceEnums.codec(schema:"bad",tagWidth:.u8,cases:[idle.erased(),number.erased()],require:.decoding)) }
            else { XCTAssertEqual(try e.fromTrace([7],limits:DecodeLimits(allocation:0,elements:0,depth:2,work:2)),.unit); XCTAssertThrowsError(try e.traceBytes(.unit,maximum:1)) }
        }
    }
    func testDraftEnumLiteralWidthAndNamedPayloadRows() throws {
        let scalarFields = TraceFields.first(TraceField<UInt16,UInt16>.ordinary("0",ValueCodecs.u16,get:{$0}))
        let scalar = try TraceRecords.codec(schema:"Set",fields:scalarFields,construct:{$0})
        let readyFields = TraceFields.first(TraceField<DraftReady,Bool>.ordinary("ready",ValueCodecs.bool,get:{$0.ready})).appending(TraceField<DraftReady,UInt8>.ordinary("byte",ValueCodecs.u8,get:{$0.byte}))
        let ready = try TraceRecords.codec(schema:"Ready",fields:readyFields,construct:{ DraftReady(ready:$0.prefix,byte:$0.last) })
        let idle = TraceEnumCase<DraftEvent,TraceUnit>(tag:7,label:"Idle",payload:ValueCodecs.unit,construct:{ _ in .idle }), set = TraceEnumCase<DraftEvent,UInt16>(tag:2,label:"Set",payload:scalar,construct:{ .set($0) }), named = TraceEnumCase<DraftEvent,DraftReady>(tag:255,label:"Ready",payload:ready,construct:{ .ready($0) })
        let event = try TraceEnums.codec(schema:"Event8",tagWidth:.u8,cases:[idle.erased(),set.erased(),named.erased()],select:{ v in switch v { case .idle:return idle.select(TraceUnit());case .set(let n):return set.select(n);case .ready(let p):return named.select(p);case .maximum:return idle.select(TraceUnit()) } })
        XCTAssertEqual(try event.traceBytes(.idle,maximum:1),[7]); XCTAssertEqual(try event.fromTrace([7],limits:DecodeLimits(allocation:0,elements:0,depth:2,work:2)),.idle)
        XCTAssertEqual(try event.traceBytes(.set(4660),maximum:3),[2,52,18]); XCTAssertEqual(try event.fromTrace([2,52,18],limits:DecodeLimits(allocation:0,elements:0,depth:2,work:3)),.set(4660))
        XCTAssertEqual(try event.traceBytes(.ready(DraftReady(ready:true,byte:9)),maximum:3),[255,1,9]); XCTAssertEqual(try event.fromTrace([255,1,9],limits:DecodeLimits(allocation:0,elements:0,depth:3,work:5)),.ready(DraftReady(ready:true,byte:9))); XCTAssertEqual(event.traceVariant(.ready(DraftReady(ready:true,byte:9))),"Ready")
        let high = TraceEnumCase<DraftEvent,UInt16>(tag:400,label:"High",payload:scalar,construct:{ .set($0) })
        let event16 = try TraceEnums.codec(schema:"Event16",tagWidth:.u16,cases:[high.erased()],select:{ _ in high.select(4660) })
        XCTAssertEqual(try event16.traceBytes(.set(4660),maximum:4),[144,1,52,18]); XCTAssertEqual(try event16.fromTrace([144,1,52,18],limits:DecodeLimits(allocation:0,elements:0,depth:2,work:3)),.set(4660))
        let max16 = TraceEnumCase<DraftEvent,TraceUnit>(tag:65535,label:"Maximum",payload:ValueCodecs.unit,construct:{ _ in .idle })
        let eventMax16 = try TraceEnums.codec(schema:"Event16Maximum",tagWidth:.u16,cases:[max16.erased()],select:{ _ in max16.select(TraceUnit()) })
        XCTAssertEqual(try eventMax16.traceBytes(.idle,maximum:2),[255,255]); XCTAssertEqual(try eventMax16.fromTrace([255,255],limits:DecodeLimits(allocation:0,elements:0,depth:2,work:2)),.idle)
        let byteFields = TraceFields.first(TraceField<UInt8,UInt8>.ordinary("0",ValueCodecs.u8,get:{$0})), byte = try TraceRecords.codec(schema:"Maximum",fields:byteFields,construct:{$0})
        let maximum = TraceEnumCase<DraftEvent,UInt8>(tag:UInt32.max,label:"Maximum",payload:byte,construct:{ .maximum($0) })
        let event32 = try TraceEnums.codec(schema:"Event32",tagWidth:.u32,cases:[maximum.erased()],select:{ _ in maximum.select(7) })
        XCTAssertEqual(try event32.traceBytes(.maximum(7),maximum:5),[255,255,255,255,7]); XCTAssertEqual(try event32.fromTrace([255,255,255,255,7],limits:DecodeLimits(allocation:0,elements:0,depth:2,work:3)),.maximum(7))
    }
}
extension DeriveCodecTests {
    func testRawBigEndianAdapterAndStickySinkScripts() throws {
        let be = TraceValueCodec<UInt16>(schema:"be_u16",encode:{ value,sink in try sink.extend([UInt8(value >> 8),UInt8(value & 255)]) },decode:{ r in let b = Array(try r.take(2)); return UInt16(b[0])*256+UInt16(b[1]) })
        let fields = TraceFields.first(TraceField<UInt16,UInt16>.adapter("0",be,get:{$0}))
        let codec = try TraceRecords.codec(schema:"BigEndian",fields:fields,construct:{$0})
        XCTAssertEqual(try codec.traceBytes(4660,maximum:2),[18,52]); XCTAssertEqual(try codec.fromTrace([18,52],limits:DecodeLimits(allocation:0,elements:0,depth:1,work:1)),4660)
        var rejected: [UInt8] = []
        XCTAssertThrowsError(try EncodeBuffer.withBuffer(&rejected,maximum:1) { sink in
            XCTAssertThrowsError(try codec.encodeRaw(4660,into:sink)); XCTAssertThrowsError(try sink.extend([187]))
        }); XCTAssertEqual(rejected,[])
        let failing = TraceValueCodec<UInt8>(schema:"append_aa_then_error",encodeOnly:{ _,sink in try sink.extend([170]); throw ModelError("adapter rejected") })
        let errorFields = TraceFields.first(TraceField<Pair,UInt8>.ordinary("prefix",ValueCodecs.u8,get:{$0.first})).appending(TraceField<Pair,UInt8>.adapter("value",failing,get:{_ in 1}))
        let errorRecord = try TraceRecords.codec(schema:"AdapterError",fields:errorFields)
        var accepted: [UInt8] = []
        try EncodeBuffer.withBuffer(&accepted,maximum:10) { sink in XCTAssertThrowsError(try errorRecord.encodeRaw(Pair(first:7,second:1),into:sink)); try sink.extend([187]) }; XCTAssertEqual(accepted,[7,170,187])
    }
}
extension DeriveCodecTests {
    func testLengthAndAdapterRequiredDirectionBuilderBoundaries() throws {
        let length = try TraceValueCodec<UInt8>(schema:"decode-length-only",decodingLength:{ _,r in try ValueCodecs.u8.decode(from:r) })
        let fields = TraceFields.first(TraceField<UInt8,UInt8>.length("x",length,width:.u8,get:{$0}))
        XCTAssertThrowsError(try TraceRecords.codec(schema:"bad",fields:fields,construct:{$0},require:.encoding))
        let enc = TraceValueCodec<UInt8>(schema:"encode-only",encodeOnly:{ try $1.extend([$0]) })
        let adapter = TraceFields.first(TraceField<UInt8,UInt8>.adapter("x",enc,get:{$0}))
        XCTAssertThrowsError(try TraceRecords.codec(schema:"bad",fields:adapter,construct:{$0},require:.decoding))
        XCTAssertThrowsError(try pair().fromTrace([7,52,18],limits:DecodeLimits(work:2)))
        var payloads = 0, constructors = 0
        let payload = TraceValueCodec<UInt8>(schema:"payload",decodeOnly:{ r in payloads += 1; return try ValueCodecs.u8.decode(from:r) })
        let item = TraceEnumCase<Choice,UInt8>(tag:7,label:"Value",payload:payload,construct:{ constructors += 1; return .number(UInt16($0)) })
        let event = try TraceEnums.codec(schema:"unknown",tagWidth:.u8,cases:[item.erased()])
        XCTAssertThrowsError(try event.fromTrace([8,255,255])); XCTAssertEqual(payloads,0); XCTAssertEqual(constructors,0)
    }
}

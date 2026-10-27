import XCTest
import Statelessness

private struct EncodeOnlyValue: TraceEncodable {
    let byte: UInt8
    var traceVariant: String? { "encode-only" }
    func traceEncode(into sink: EncodeBuffer) throws { try ValueCodecs.u8.encodeRaw(byte,into:sink) }
}
private struct DecodeOnlyValue: TraceDecodable {
    let byte: UInt8
    static func traceDecode(from reader: ValueDecoder) throws -> Self { Self(byte:try reader.value(ValueCodecs.u8)) }
}
final class ValueCodecDirectionsTests: XCTestCase {
    func testEncodeOnlyProtocolAndAllBuiltinCompositionWithoutLayouts() throws {
        let e = TraceValueCodec<EncodeOnlyValue>.encoding()
        XCTAssertTrue(e.supportsEncoding); XCTAssertFalse(e.supportsDecoding)
        XCTAssertEqual(e.traceVariant(EncodeOnlyValue(byte:7)),"encode-only")
        XCTAssertEqual(try ValueCodecs.vector(e).traceBytes([EncodeOnlyValue(byte:7)],maximum:5),[1,0,0,0,7])
        XCTAssertEqual(try ValueCodecs.vector(e).traceBytes([],maximum:4),[0,0,0,0])
        XCTAssertEqual(try ValueCodecs.option(e).traceBytes(.none,maximum:1),[0])
        XCTAssertEqual(try ValueCodecs.option(e).traceBytes(.some(EncodeOnlyValue(byte:7)),maximum:2),[1,7])
        XCTAssertEqual(try ValueCodecs.array(e,count:0).traceBytes([],maximum:0),[])
        XCTAssertEqual(try ValueCodecs.array(e,count:1).traceBytes([EncodeOnlyValue(byte:7)],maximum:1),[7])
        XCTAssertEqual(try ValueCodecs.tuple1(e).traceBytes(TraceTuple1(EncodeOnlyValue(byte:7)),maximum:1),[7])
        XCTAssertEqual(try ValueCodecs.tuple2(e,ValueCodecs.unit).traceBytes((EncodeOnlyValue(byte:7),TraceUnit()),maximum:1),[7])
        XCTAssertEqual(try ValueCodecs.tuple3(e,ValueCodecs.unit,ValueCodecs.bool).traceBytes((EncodeOnlyValue(byte:7),TraceUnit(),true),maximum:2),[7,1])
        XCTAssertEqual(try ValueCodecs.tuple4(e,ValueCodecs.unit,ValueCodecs.bool,ValueCodecs.u8).traceBytes((EncodeOnlyValue(byte:7),TraceUnit(),true,9),maximum:3),[7,1,9])
        XCTAssertEqual(try ValueCodecs.oracleState(e,ValueCodecs.unit).traceBytes(OracleState(model:EncodeOnlyValue(byte:7),oracle:TraceUnit()),maximum:1),[7])
        XCTAssertFalse(e.withLayout(Rust64ValueLayout(size:1,identity:"declared")).supportsDecoding)
    }
    func testDecodeOnlyProtocolAndAllBuiltinComposition() throws {
        let d = TraceValueCodec<DecodeOnlyValue>.decoding(layout:Rust64ValueLayouts.measured("u8"))
        XCTAssertFalse(d.supportsEncoding); XCTAssertTrue(d.supportsDecoding)
        XCTAssertEqual(try ValueCodecs.vector(d).fromTrace([1,0,0,0,7]).map(\.byte),[7])
        XCTAssertEqual(try ValueCodecs.vector(d).fromTrace([0,0,0,0]).count,0)
        XCTAssertNil(try ValueCodecs.option(d).fromTrace([0]))
        XCTAssertEqual(try ValueCodecs.option(d).fromTrace([1,7])?.byte,7)
        XCTAssertEqual(try ValueCodecs.array(d,count:0).fromTrace([]).count,0)
        XCTAssertEqual(try ValueCodecs.array(d,count:1).fromTrace([7]).first?.byte,7)
        XCTAssertEqual(try ValueCodecs.tuple1(d).fromTrace([7]).value.byte,7)
        XCTAssertEqual(try ValueCodecs.tuple2(d,ValueCodecs.unit).fromTrace([7]).0.byte,7)
        XCTAssertEqual(try ValueCodecs.tuple3(d,ValueCodecs.unit,ValueCodecs.bool).fromTrace([7,1]).0.byte,7)
        XCTAssertEqual(try ValueCodecs.tuple4(d,ValueCodecs.unit,ValueCodecs.bool,ValueCodecs.u8).fromTrace([7,1,9]).3,9)
        XCTAssertEqual(try ValueCodecs.oracleState(d,ValueCodecs.unit).fromTrace([7]).model.byte,7)
        XCTAssertFalse(d.withLayout(Rust64ValueLayout(size:1,identity:"declared")).supportsEncoding)
    }
    func testUnavailableDirectionsRejectEmptyPayloadsBeforeOperations() throws {
        let e = TraceValueCodec<EncodeOnlyValue>.encoding(), d = TraceValueCodec<DecodeOnlyValue>.decoding()
        XCTAssertThrowsError(try ValueCodecs.option(e).fromTrace([0]))
        XCTAssertThrowsError(try ValueCodecs.vector(e).fromTrace([0,0,0,0]))
        XCTAssertThrowsError(try ValueCodecs.array(e,count:0).fromTrace([]))
        XCTAssertThrowsError(try ValueCodecs.option(d).traceBytes(.none,maximum:1))
        XCTAssertThrowsError(try ValueCodecs.vector(d).traceBytes([],maximum:4))
        XCTAssertThrowsError(try ValueCodecs.array(d,count:0).traceBytes([],maximum:0))
        let reader = try ValueDecoder([0,0,0,0],limits:DecodeLimits(depth:1,work:1))
        XCTAssertThrowsError(try reader.value(ValueCodecs.vector(e))) { XCTAssertEqual(String(describing:$0),"value codec does not support decoding") }
        // Admission consumed no guard work and recorded no structural failure.
        _ = try reader.value(ValueCodecs.unit)
        XCTAssertEqual(Array(try reader.take(4)),[0,0,0,0]); try reader.finish()
        let direct = try ValueDecoder([0],limits:DecodeLimits(work:0))
        XCTAssertThrowsError(try ValueCodecs.vector(e).decodeLength(width:1,from:direct))
        XCTAssertEqual(Array(try direct.take(1)),[0])
        XCTAssertThrowsError(try e.fromTrace([7],limits:DecodeLimits(bytes:0,work:0))) { XCTAssertEqual(String(describing:$0),"value codec does not support decoding") }
        var bytes: [UInt8] = []
        try EncodeBuffer.withBuffer(&bytes,maximum:3) { sink in
            try sink.extend([9])
            XCTAssertThrowsError(try ValueCodecs.vector(d).encode([],into:sink))
            XCTAssertThrowsError(try ValueCodecs.vector(d).encodeLengthRaw([],width:1,into:sink))
            try sink.extend([8]); try sink.finish()
        }
        XCTAssertEqual(bytes,[9,8])
        let failed = try ValueDecoder([])
        XCTAssertThrowsError(try failed.take(1))
        XCTAssertThrowsError(try failed.value(e)) { XCTAssertEqual(String(describing:$0),"truncated value") }
    }
    func testIndependentCustomLengthTraitsAndSelectedWidthAdapter() throws {
        let length = try TraceValueCodec<String>(schema:"custom-length",variant:{ _ in "length-variant" },
            encodingLength:{ value,width,sink in try ValueCodecs.string.encodeLengthRaw(value,width:width,into:sink) },
            decodingLength:{ width,reader in try ValueCodecs.string.decodeLength(width:width,from:reader) })
        XCTAssertFalse(length.supportsEncoding); XCTAssertFalse(length.supportsDecoding)
        XCTAssertTrue(length.supportsLengthEncoding); XCTAssertTrue(length.supportsLengthDecoding)
        let selected = length.withLength(1)
        XCTAssertTrue(selected.supportsEncoding); XCTAssertTrue(selected.supportsDecoding)
        XCTAssertEqual(try selected.traceBytes("x",maximum:2),[1,120])
        XCTAssertEqual(try selected.fromTrace([1,120],limits:DecodeLimits(depth:2,work:3)),"x")
        XCTAssertThrowsError(try selected.fromTrace([1,120],limits:DecodeLimits(depth:2,work:2)))
        let rawReader = try ValueDecoder([1,120],limits:DecodeLimits(depth:1,work:2))
        XCTAssertEqual(try selected.decode(from:rawReader),"x"); try rawReader.finish()
        let directReader = try ValueDecoder([1,120],limits:DecodeLimits(depth:1,work:2))
        XCTAssertEqual(try length.decodeLength(width:1,from:directReader),"x"); try directReader.finish()
        XCTAssertThrowsError(try length.traceBytes("",maximum:4))
        XCTAssertThrowsError(try length.fromTrace([]))
        let kept = length.withLayout(Rust64ValueLayouts.measured("String")!)
        XCTAssertTrue(kept.supportsLengthEncoding); XCTAssertTrue(kept.supportsLengthDecoding)
        XCTAssertFalse(kept.supportsEncoding); XCTAssertFalse(kept.supportsDecoding)
        XCTAssertEqual(kept.withLength(1).traceVariant("x"),"length-variant")
        // Vec's length trait requires ordinary child TraceEncode/TraceDecode,
        // even when the selected runtime vector has no children.
        let vector = ValueCodecs.vector(length)
        XCTAssertFalse(vector.supportsLengthEncoding); XCTAssertFalse(vector.supportsLengthDecoding)
        var bytes: [UInt8] = []
        try EncodeBuffer.withBuffer(&bytes,maximum:4) { sink in
            XCTAssertThrowsError(try vector.encodeLengthRaw([],width:1,into:sink))
            try sink.extend([9])
        }
        XCTAssertEqual(bytes,[9])
        let encodeLengthOnly = try TraceValueCodec<String>(schema:"length-encode-only",encodingLength:{ value,width,sink in try ValueCodecs.string.encodeLengthRaw(value,width:width,into:sink) })
        XCTAssertEqual(try encodeLengthOnly.withLength(1).traceBytes("",maximum:1),[0])
        XCTAssertFalse(encodeLengthOnly.withLength(1).supportsDecoding)
        let decodeLengthOnly = try TraceValueCodec<String>(schema:"length-decode-only",decodingLength:{ width,reader in try ValueCodecs.string.decodeLength(width:width,from:reader) })
        XCTAssertEqual(try decodeLengthOnly.withLength(1).fromTrace([0]),"")
        XCTAssertFalse(decodeLengthOnly.withLength(1).supportsEncoding)
        XCTAssertFalse(ValueCodecs.unit.withLength(1).supportsEncoding)
        XCTAssertTrue(ValueCodecs.string.supportsLengthEncoding)
        XCTAssertTrue(ValueCodecs.string.supportsLengthDecoding)
    }
    func testMixedDirectionTupleAndOptionalCallbackAdmission() throws {
        XCTAssertThrowsError(try TraceValueCodec<UInt8>(schema:"none",encoding:nil,decoding:nil))
        var encodeCalls = 0, decodeCalls = 0
        let e = TraceValueCodec<UInt8>(schema:"only-encode",encodeOnly:{ value,sink in encodeCalls += 1; try sink.extend([value]) })
        let d = TraceValueCodec<UInt8>(schema:"only-decode",decodeOnly:{ reader in decodeCalls += 1; return try reader.value(ValueCodecs.u8) })
        let mixed = ValueCodecs.tuple2(e,d)
        XCTAssertFalse(mixed.supportsEncoding); XCTAssertFalse(mixed.supportsDecoding)
        XCTAssertFalse(mixed.withLayout(Rust64ValueLayout(size:2,identity:"declared")).supportsEncoding)
        var bytes: [UInt8] = []
        try EncodeBuffer.withBuffer(&bytes,maximum:4) { sink in
            try sink.extend([9]); XCTAssertThrowsError(try mixed.encodeRaw((1,2),into:sink)); try sink.extend([8])
        }
        XCTAssertEqual(bytes,[9,8]); XCTAssertEqual(encodeCalls,0)
        let reader = try ValueDecoder([1,2],limits:DecodeLimits(work:0))
        XCTAssertThrowsError(try reader.value(mixed)) { XCTAssertEqual(String(describing:$0),"value codec does not support decoding") }
        XCTAssertEqual(Array(try reader.take(2)),[1,2]); XCTAssertEqual(decodeCalls,0)
        let optional = try TraceValueCodec<UInt8>(schema:"optional-encode",encoding:{ try $1.extend([$0]) },decoding:nil)
        XCTAssertTrue(optional.supportsEncoding); XCTAssertFalse(optional.supportsDecoding)
        XCTAssertEqual(try ValueCodecs.vector(optional).traceBytes([3],maximum:5),[1,0,0,0,3])
    }
}

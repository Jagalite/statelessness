import Foundation
import XCTest
@testable import Statelessness

// This adapter preserves explicit schema types at the public codec boundary.
// Native-only impossible typed inputs and arithmetic-only rows are not Rust execution.
enum VCFixture {
    static func bytes(_ text: String) -> [UInt8] { stride(from: 0, to: text.count, by: 2).map { n in let i = text.index(text.startIndex, offsetBy: n); return UInt8(text[i..<text.index(i, offsetBy: 2)], radix: 16)! } }
    static func hex(_ bytes: [UInt8]) -> String { bytes.map { String(format: "%02x", $0) }.joined() }
    static func limits(_ rows: [String:Any]) -> DecodeLimits {
        var l = DecodeLimits()
        for (k,v) in rows { let n = UInt64(v as! String)!; switch k { case "bytes": l.bytes = n; case "allocation": l.allocation = n; case "elements": l.elements = n; case "depth": l.depth = n; case "work": l.work = n; default: preconditionFailure(k) } }; return l
    }
    static func erase<T>(_ codec: TraceValueCodec<T>, input: @escaping (Any)throws->T, output: @escaping (T)->Any) -> TraceValueCodec<Any> {
        TraceValueCodec(schema: codec.schema, layout: codec.layout, encode: { try codec.encodeRaw(input($0), into: $1) }, decode: { output(try codec.decode(from: $0)) })
    }
    static func number<T: FixedWidthInteger>(_ codec: TraceValueCodec<T>) -> TraceValueCodec<Any> {
        erase(codec, input: { guard let n = T($0 as! String) else { throw ModelError("integer out of range") }; return n }, output: { String($0) })
    }
    static func schema(_ d: [String:Any]) -> TraceValueCodec<Any> {
        let kind = d["kind"] as! String
        switch kind {
        case "u8": return number(ValueCodecs.u8)
        case "u16": return number(ValueCodecs.u16)
        case "u32": return number(ValueCodecs.u32)
        case "u64": return number(ValueCodecs.u64)
        case "i8": return number(ValueCodecs.i8)
        case "i16": return number(ValueCodecs.i16)
        case "i32": return number(ValueCodecs.i32)
        case "i64": return number(ValueCodecs.i64)
        case "u128": return erase(ValueCodecs.u128, input: { try TraceUInt128(decimal: $0 as! String) }, output: { $0.description })
        case "i128": return erase(ValueCodecs.i128, input: { try TraceInt128(decimal: $0 as! String) }, output: { $0.description })
        case "bool": return erase(ValueCodecs.bool, input: { $0 as! Bool }, output: { $0 })
        case "unit": return erase(ValueCodecs.unit, input: { _ in TraceUnit() }, output: { _ in NSNull() })
        case "string":
            let codec = ValueCodecs.string, width = UInt8(d["length_width"] as? Int ?? 4)
            return TraceValueCodec<Any>(schema: codec.schema, layout: codec.layout, encode: { try codec.encodeLengthRaw($0 as! String, width: width, into: $1) }, decode: { try codec.decodeLength(width: width, from: $0) }, encodeLength: { try codec.encodeLengthRaw($0 as! String, width: $1, into: $2) }, decodeLength: { try codec.decodeLength(width: $0, from: $1) })
        case "vector":
            let codec = ValueCodecs.vector(schema(d["element"] as! [String:Any])), width = UInt8(d["length_width"] as? Int ?? 4)
            return TraceValueCodec<Any>(schema: codec.schema, layout: codec.layout, encode: { try codec.encodeLengthRaw($0 as! [Any], width: width, into: $1) }, decode: { try codec.decodeLength(width: width, from: $0) }, encodeLength: { try codec.encodeLengthRaw($0 as! [Any], width: $1, into: $2) }, decodeLength: { try codec.decodeLength(width: $0, from: $1) })
        case "array":
            let element = schema(d["element"] as! [String:Any]), count = UInt64(d["length"] as! Int)
            // Arrays have an exact multiplicative Rust layout, not inferred tuple/enum layout.
            let size = element.layout!.size.multipliedReportingOverflow(by: count)
            let codec = ValueCodecs.array(element, count: count, layout: size.overflow ? nil : Rust64ValueLayout(size: size.partialValue, identity: "explicit fixture array"))
            return erase(codec, input: { $0 as! [Any] }, output: { $0 })
        case "option":
            let element = schema(d["element"] as! [String:Any])
            // Explicit finite measured Rust64 descriptor, without a layout inference formula.
            let layout: Rust64ValueLayout? = element.schema == "u64" ? Rust64ValueLayouts.optionU64 : nil
            let codec = ValueCodecs.option(element, layout: layout)
            return erase(codec, input: { value in let row = value as! [String:Any]; return row["some"] as! Bool ? .some(row["value"]!) : .none }, output: { value in
                switch value { case .none: return ["some": false]; case .some(let value): return ["some": true, "value": value] as [String:Any] }
            })
        case "tuple":
            let fields = (d["fields"] as! [[String:Any]]).map(schema)
            switch fields.count {
            case 1: return erase(ValueCodecs.tuple1(fields[0]), input: { TraceTuple1(($0 as! [Any])[0]) }, output: { [$0.value] })
            case 2: return erase(ValueCodecs.tuple2(fields[0],fields[1]), input: { let a = $0 as! [Any]; return (a[0],a[1]) }, output: { [$0.0,$0.1] })
            case 3: return erase(ValueCodecs.tuple3(fields[0],fields[1],fields[2]), input: { let a = $0 as! [Any]; return (a[0],a[1],a[2]) }, output: { [$0.0,$0.1,$0.2] })
            case 4: return erase(ValueCodecs.tuple4(fields[0],fields[1],fields[2],fields[3]), input: { let a = $0 as! [Any]; return (a[0],a[1],a[2],a[3]) }, output: { [$0.0,$0.1,$0.2,$0.3] })
            default: preconditionFailure("unsupported tuple")
            }
        case "oracle_state":
            return erase(ValueCodecs.oracleState(schema(d["model"] as! [String:Any]),schema(d["oracle"] as! [String:Any])), input: { let a = $0 as! [String:Any]; return OracleState(model: a["model"]!,oracle: a["oracle"]!) }, output: { ["model": $0.model, "oracle": $0.oracle] })
        default: preconditionFailure(kind)
        }
    }
    static func corpus() throws -> [String:Any] {
        let fallback = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("conformance/canonical-value-codec-v1.json").path
        func convert(_ value: JSON) -> Any {
            switch value {
            case .null: return NSNull()
            case .bool(let b): return b
            case .number(let n): return Int(n)!
            case .string(let s): return s
            case .array(let a): return a.map(convert)
            case .object(let o): return Dictionary(uniqueKeysWithValues: o.map { ($0.key.value,convert($0.value)) })
            }
        }
        // Foundation NSString bridging can strip an initial BOM. The package's
        // exact JSON parser preserves scalar identity in fixture input strings.
        return try convert(parseJSON(Array(Data(contentsOf: URL(fileURLWithPath: ProcessInfo.processInfo.environment["STATELESS_VALUE_CORPUS"] ?? fallback))))) as! [String:Any]
    }
    static func same(_ a: Any, _ b: Any) -> Bool { NSDictionary(dictionary: ["v":a]).isEqual(to: ["v":b]) }
}
final class ValueCodecTests: XCTestCase {
    func testSharedValuesErrorsAndAllPrefixes() throws {
        let corpus = try VCFixture.corpus()
        for row in corpus["value_cases"] as! [[String:Any]] {
            let name = row["case"] as! String, codec = VCFixture.schema(row["schema"] as! [String:Any]), bytes = VCFixture.bytes(row["bytes_hex"] as! String), entry = row["entry"] as! String
            var encoded: [UInt8] = []
            try EncodeBuffer.withBuffer(&encoded, maximum: Int(row["encode_max_bytes"] as! String)!) { try codec.encode(row["value"]!, into: $0) }
            XCTAssertEqual(encoded,bytes,name)
            let reader = try ValueDecoder(bytes, limits: VCFixture.limits(row["limits"] as? [String:Any] ?? [:]))
            let actual = try entry == "direct_length" ? codec.decode(from: reader) : reader.value(codec)
            try reader.finish(); XCTAssertTrue(VCFixture.same(actual,(row["expected"] as! [String:Any])["value"]!),name)
            for n in 0..<bytes.count { XCTAssertThrowsError(try codec.fromTrace(Array(bytes.prefix(n))),name) }
            XCTAssertThrowsError(try codec.fromTrace(bytes + [0]),name)
            var source = [UInt8(99)] + bytes + [UInt8(88)]
            let subview = Array(source[1..<(1+bytes.count)]); let owned = try codec.fromTrace(subview)
            source.removeAll(); XCTAssertTrue(VCFixture.same(owned,actual),name)
        }
        for row in corpus["decode_error_cases"] as! [[String:Any]] {
            let codec = VCFixture.schema(row["schema"] as! [String:Any])
            XCTAssertThrowsError(try { let reader = try ValueDecoder(VCFixture.bytes(row["bytes_hex"] as! String),limits:VCFixture.limits(row["limits"] as? [String:Any] ?? [:])); let value = try row["entry"] as! String == "direct_length" ? codec.decode(from: reader) : reader.value(codec); _ = value; try reader.finish() }()) { error in
                XCTAssertEqual(String(describing:error),(row["expected"] as! [String:Any])["error"] as? String,row["case"] as! String)
            }
        }
        for row in corpus["encode_error_cases"] as! [[String:Any]] {
            let codec = VCFixture.schema(row["schema"] as! [String:Any])
            XCTAssertThrowsError(try codec.traceBytes(row["value"]!, maximum:Int(row["encode_max_bytes"] as! String)!)) { error in XCTAssertEqual(String(describing:error),(row["expected"] as! [String:Any])["error"] as? String,row["case"] as! String) }
        }
        print("canonical values executable: \((corpus["value_cases"] as! [Any]).count) values, \((corpus["decode_error_cases"] as! [Any]).count) decode errors, \((corpus["encode_error_cases"] as! [Any]).count) encode errors; length rows \((corpus["length_cases"] as! [Any]).count) separately executed/classified; native-only \((corpus["native_admission_cases"] as! [Any]).count) separately classified")
    }
    func testNative128AdmissionTypedOptionsAndUnknownLayouts() throws {
        XCTAssertThrowsError(try TraceUInt128(decimal:"340282366920938463463374607431768211456"))
        XCTAssertThrowsError(try TraceInt128(decimal:"170141183460469231731687303715884105728"))
        XCTAssertThrowsError(try TraceInt128(decimal:"-170141183460469231731687303715884105729"))
        XCTAssertThrowsError(try TraceUInt128(decimal:"-1"))
        let unit = ValueCodecs.option(ValueCodecs.unit)
        XCTAssertEqual(try unit.traceBytes(.none,maximum:1),[0]); XCTAssertEqual(try unit.traceBytes(.some(TraceUnit()),maximum:1),[1])
        let nested = ValueCodecs.option(unit)
        XCTAssertEqual(try nested.traceBytes(.some(.none),maximum:2),[1,0])
        XCTAssertEqual(try nested.traceBytes(.some(.some(TraceUnit())),maximum:2),[1,1])
        XCTAssertThrowsError(try ValueCodecs.vector(unit).fromTrace([0,0,0,0]))
        XCTAssertThrowsError(try ValueCodecs.array(ValueCodecs.u8,count:2).traceBytes([1],maximum:2))
    }
    func testNativeAdmissionRowsAreClassifiedAtTheirActualBoundary() throws {
        let rows = try VCFixture.corpus()["native_admission_cases"] as! [[String:Any]]
        var runtime = 0, excluded = 0
        for row in rows {
            let name = row["case"] as! String, kind = (row["schema"] as! [String:Any])["kind"] as! String
            if kind == "u128", row["expected_error"] as! String == "integer_range" {
                XCTAssertThrowsError(try TraceUInt128(decimal:row["value"] as! String),name); runtime += 1
            } else if kind == "i128", row["expected_error"] as! String == "integer_range" {
                XCTAssertThrowsError(try TraceInt128(decimal:row["value"] as! String),name); runtime += 1
            } else if kind == "array" {
                XCTAssertThrowsError(try ValueCodecs.array(ValueCodecs.u8,count:2).traceBytes([1],maximum:2),name); runtime += 1
            } else {
                // UInt8...Int64, Bool and String admit only their Swift values.
                // Fixture string/NSNumber conversion is not a codec admission test.
                XCTAssertTrue(["integer_range","boolean_type","integer_type","integer_type_or_unsafe_number","invalid_unicode"].contains(row["expected_error"] as! String),name)
                excluded += 1
            }
        }
        XCTAssertEqual(runtime,5); XCTAssertEqual(runtime+excluded,rows.count)
        print("native admission: \(runtime) public runtime rejections; \(excluded) statically excluded by typed Swift API; not Rust execution")
        let lengths = try VCFixture.corpus()["length_cases"] as! [[String:Any]]
        var arithmetic = 0, executed = 0
        for row in lengths {
            if row["operation"] as! String == "encode_length_empty" {
                var bytes: [UInt8] = []
                XCTAssertThrowsError(try EncodeBuffer.withBuffer(&bytes,maximum:16) { sink in
                    try ValueCodecs.string.encodeLengthRaw("",width:UInt8(row["width"] as! Int),into:sink)
                }) { error in XCTAssertEqual(String(describing:error),(row["expected"] as! [String:Any])["error"] as? String) }
                XCTAssertEqual(bytes,[]); executed += 1
            } else {
                XCTAssertEqual(row["scope"] as? String,"arithmetic_only_not_an_exported_rust_primitive")
                arithmetic += 1
            }
        }
        XCTAssertEqual(executed,3); XCTAssertEqual(arithmetic,6)
        print("length rows: \(executed) actual public length encodes, \(arithmetic) arithmetic-only witnesses not executed")
        XCTAssertEqual(Rust64ValueLayouts.measured("Option<Option<unit>>")?.size,1)
        XCTAssertEqual(Rust64ValueLayouts.measured("(u16,i32,bool,Option<u8>)")?.size,12)
        XCTAssertNil(Rust64ValueLayouts.measured("Option<custom-unmeasured>"))
    }
    func testRawCallbacksRunOnFailedSinkAndKeepOwnReturnPrimary() throws {
        var bytes: [UInt8] = [], calls = 0
        let failure = TraceValueCodec<TraceUnit>(schema:"failed-writer-callback",encode:{ _,_ in calls += 1; throw ModelError("own callback error") },decode:{ _ in TraceUnit() })
        let swallow = TraceValueCodec<TraceUnit>(schema:"swallowed-writer-callback",encode:{ _,sink in calls += 1; do { try sink.extend([2]) } catch {} },decode:{ _ in TraceUnit() })
        XCTAssertThrowsError(try EncodeBuffer.withBuffer(&bytes,maximum:0) { sink in
            do { try sink.extend([1]) } catch {}
            XCTAssertThrowsError(try failure.encodeRaw(TraceUnit(),into:sink)) { XCTAssertEqual(String(describing:$0),"own callback error") }
            try swallow.encodeRaw(TraceUnit(),into:sink)
            XCTAssertEqual(calls,2)
            // Raw success does not automatically finish an already failed sink.
            XCTAssertThrowsError(try sink.finish()) { XCTAssertEqual(String(describing:$0),"encoded blob exceeds byte or allocation limit") }
        })
        XCTAssertEqual(bytes,[]); XCTAssertEqual(calls,2)
    }
    func testCustomEncoderLeasesAndAppend() throws {
        var escaped: EncodeBuffer?, bytes: [UInt8] = []
        let custom = TraceValueCodec<UInt8>(schema:"custom",encode:{ value,sink in escaped = sink; try ValueCodecs.u8.encode(value,into:sink) },decode:{ try $0.value(ValueCodecs.u8) })
        try EncodeBuffer.withBuffer(&bytes,maximum:2) { sink in try sink.extend([9]); try custom.encode(7,into:sink); XCTAssertThrowsError(try escaped!.extend([1])); try sink.finish() }
        XCTAssertEqual(bytes,[9,7]); XCTAssertThrowsError(try escaped!.count)
        let literal = "\u{feff}x"
        XCTAssertEqual(try ValueCodecs.string.traceBytes(literal,maximum:8),[4,0,0,0,239,187,191,120])
        XCTAssertEqual(Array(try ValueCodecs.string.fromTrace([4,0,0,0,239,187,191,120]).utf8),Array(literal.utf8))
        let failure = TraceValueCodec<UInt8>(schema:"failure",encode:{ _,_ in throw ModelError("codec failure") },decode:{ _ in 0 })
        XCTAssertThrowsError(try EncodeBuffer.withBuffer(&bytes,maximum:8) { sink in do { try failure.encode(1,into:sink) } catch {}; try sink.finish() })
    }
}

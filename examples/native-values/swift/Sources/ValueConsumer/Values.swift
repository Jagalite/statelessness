import Foundation
import Statelessness
enum ValueAdapter {
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
            let codec=ValueCodecs.string.withLength(UInt8(d["length_width"] as? Int ?? 4))
            return erase(codec,input:{$0 as! String},output:{$0})
        case "vector":
            let codec=ValueCodecs.vector(schema(d["element"] as! [String:Any])).withLength(UInt8(d["length_width"] as? Int ?? 4))
            return erase(codec,input:{$0 as! [Any]},output:{$0})
        case "array":
            let element = schema(d["element"] as! [String:Any]), count = UInt64(d["length"] as! Int)
            let codec = ValueCodecs.array(element, count: count, layout: measured(d))
            return erase(codec, input: { $0 as! [Any] }, output: { $0 })
        case "option":
            let element = schema(d["element"] as! [String:Any])
            let layout = measured(d)
            let codec = ValueCodecs.option(element, layout: layout)
            return erase(codec, input: { value in let row = value as! [String:Any]; return row["some"] as! Bool ? .some(row["value"]!) : .none }, output: { value in
                switch value { case .none: return ["some": false]; case .some(let value): return ["some": true, "value": value] as [String:Any] }
            })
        case "tuple":
            let fields = (d["fields"] as! [[String:Any]]).map(schema)
            switch fields.count {
            case 1: return erase(ValueCodecs.tuple1(fields[0],layout:measured(d)), input: { TraceTuple1(($0 as! [Any])[0]) }, output: { [$0.value] })
            case 2: return erase(ValueCodecs.tuple2(fields[0],fields[1],layout:measured(d)), input: { let a = $0 as! [Any]; return (a[0],a[1]) }, output: { [$0.0,$0.1] })
            case 3: return erase(ValueCodecs.tuple3(fields[0],fields[1],fields[2],layout:measured(d)), input: { let a = $0 as! [Any]; return (a[0],a[1],a[2]) }, output: { [$0.0,$0.1,$0.2] })
            case 4: return erase(ValueCodecs.tuple4(fields[0],fields[1],fields[2],fields[3],layout:measured(d)), input: { let a = $0 as! [Any]; return (a[0],a[1],a[2],a[3]) }, output: { [$0.0,$0.1,$0.2,$0.3] })
            default: preconditionFailure("unsupported tuple")
            }
        case "oracle_state":
            return erase(ValueCodecs.oracleState(schema(d["model"] as! [String:Any]),schema(d["oracle"] as! [String:Any])), input: { let a = $0 as! [String:Any]; return OracleState(model: a["model"]!,oracle: a["oracle"]!) }, output: { ["model": $0.model, "oracle": $0.oracle] })
        default: preconditionFailure(kind)
        }
    }
    static func measured(_ d:[String:Any])->Rust64ValueLayout? {for row in (contract["layouts"] as! [String:Any])["registry"] as! [[String:Any]] {if NSDictionary(dictionary:row["schema"] as! [String:Any]).isEqual(to:d){return Rust64ValueLayout(size:UInt64(row["size"] as! Int),identity:"measured finite Rust64 shape",alignment:UInt64(row["alignment"] as! Int))}};return nil}
    static func same(_ a:Any,_ b:Any)->Bool {NSDictionary(dictionary:["v":a]).isEqual(to:["v":b])}
    static func decode(_ row:[String:Any],_ data:[UInt8],exact:Bool=true)throws->Any {let c=schema(row["schema"] as! [String:Any]),r=try ValueDecoder(data,limits:exact ? limits(row["limits"] as? [String:Any] ?? [:]):DecodeLimits());let v=try row["entry"] as! String == "direct_length" ? c.decode(from:r):r.value(c);try r.finish();return v}
}
func parseOwnedJSON(_ path:String)throws->Any {
 func convert(_ j:JSON)->Any {switch j{case .null:return NSNull();case let .bool(b):return b;case let .number(n):return Int(n)!;case let .string(s):return s;case let .array(a):return a.map(convert);case let .object(o):return Dictionary(uniqueKeysWithValues:o.map{($0.key.value,convert($0.value))})}}
 return try convert(parseJSON(Array(Data(contentsOf:URL(fileURLWithPath:path)))))
}
let contract:[String:Any] = { do { return try parseOwnedJSON(ProcessInfo.processInfo.environment["STATELESS_VALUES_CONTRACT"]!) as! [String:Any] } catch { fatalError("invalid trusted value contract: \(error)") } }()
func valueReject(_ needle:String="",_ callback:()throws->Void)throws->String {do{try callback()}catch{let text=String(describing:error);try require(needle.isEmpty || text.contains(needle),"wrong codec error: \(text)");return text};throw ModelError("expected rejection")}
func extraValueControls()throws->[String:Any]{var l=DecodeLimits();l.elements=16;let unitError=try valueReject("decode element limit"){_ = try ValueCodecs.vector(ValueCodecs.unit).fromTrace([255,255,255,255],limits:l)}
 let reader=try ValueDecoder([1]);var calls=0;let custom=TraceValueCodec<TraceUnit>(schema:"swallowed-reader-error",decodeOnly:{r in calls += 1;do{_ = try r.take(2)}catch{};return TraceUnit()});let sticky=try valueReject("truncated value"){_ = try reader.value(custom)};_ = try valueReject("truncated value"){try reader.finish()};try require(calls==1,"custom callback repeated")
 l=DecodeLimits();l.work=3;l.depth=1;let direct=try ValueDecoder([2,1,2],limits:l),v=try ValueCodecs.vector(ValueCodecs.u8).decodeLength(width:1,from:direct);try direct.finish();try require(v==[1,2],"direct values differ");l.depth=2;let guarded=try ValueDecoder([2,1,2],limits:l);_ = try valueReject("decode work limit"){_ = try guarded.value{try ValueCodecs.vector(ValueCodecs.u8).decodeLength(width:1,from:$0)}}
 var storage:[UInt8]=[];try EncodeBuffer.withBuffer(&storage,maximum:3){out in try out.extend([170]);try ValueCodecs.u16.encode(4660,into:out);try out.finish()};try require(storage==[170,52,18],"schema cleared prefix")
 return ["unit_budget_rejected":true,"unit_budget_error":unitError,"sticky_swallowed_error":true,"sticky_error":sticky,"direct_length_work_exact":true,"append_preserved":true]
}
func runValues(_ mode:String,_ source:String?,_ target:String)throws->[String:Any]{let rows=contract["value_cases"] as! [[String:Any]];var incoming:[[String:Any]]?=nil
 if mode=="transcode"{let bundle=try parseOwnedJSON(source!) as! [String:Any];try require(bundle["schema"] as? String==contract["schema"] as? String,"bundle schema differs");let cases=bundle["cases"] as! [[String:Any]];try require(cases.map{$0["id"] as! String}==rows.map{$0["case"] as! String},"closed IDs differ");incoming=cases}
 var output:[[String:Any]]=[],decoded:[[String:Any]]=[],counts:[String:Int]=["prefix_rejections":0,"trailing_rejections":0,"subview_decodes":0,"owned_results":0,"exact_encode_caps":0,"short_encode_caps":0]
 for (index,row) in rows.enumerated(){let c=ValueAdapter.schema(row["schema"] as! [String:Any]),wire:[UInt8];if let incoming{wire=ValueAdapter.bytes(incoming[index]["hex"] as! String)}else{wire=try c.traceBytes(row["value"]!,maximum:Int(row["encode_max_bytes"] as! String)!)};let value=try ValueAdapter.decode(row,wire),again=try c.traceBytes(value,maximum:wire.count);try require(again==wire,"noncanonical bytes");counts["exact_encode_caps"]! += 1
 if !wire.isEmpty{_ = try valueReject{_ = try c.traceBytes(value,maximum:wire.count-1)};counts["short_encode_caps"]! += 1};for n in 0..<wire.count{_ = try valueReject{_ = try ValueAdapter.decode(row,Array(wire.prefix(n)),exact:false)};counts["prefix_rejections"]! += 1};_ = try valueReject("trailing"){_ = try ValueAdapter.decode(row,wire+[0],exact:false)};counts["trailing_rejections"]! += 1
 var backing=[UInt8(255)]+wire+[254];let owned=try ValueAdapter.decode(row,Array(backing[1..<(1+wire.count)]),exact:false);counts["subview_decodes"]! += 1;backing=Array(repeating:0,count:backing.count);try require(ValueAdapter.same(value,owned),"decoded result aliases input");counts["owned_results"]! += 1;decoded.append(["id":row["case"]!,"value":value]);output.append(["id":row["case"]!,"hex":ValueAdapter.hex(again)])}
 let bundle:[String:Any]=["schema":contract["schema"]!,"cases":output];try Data(JSONSerialization.data(withJSONObject:bundle,options:[.sortedKeys])).write(to:URL(fileURLWithPath:target));var result=try extraValueControls();for(k,v)in counts{result[k]=v};result["subview_policy"]="owned-subrange";return ["case_count":rows.count,"decoded_values":decoded,"controls":result]}

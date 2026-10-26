//! Actual public Rust value-codec reference; JSON conversion is fixture-only.
use serde_json::{Value, json};
use stateless::{
    model::{EncodeBuffer, ModelError},
    oracle::OracleState,
    value_codec::{DecodeLimits, Decoder, LengthDecode, LengthEncode, TraceDecode, TraceEncode},
};
use std::{
    cell::{Cell, RefCell},
    io::Read,
    path::Path,
};
type R<T> = Result<T, ModelError>;
trait FixtureValue: TraceEncode + TraceDecode + Sized {
    fn parse(v: &Value) -> R<Self>;
    fn json(&self) -> Value;
    fn decode_length(_w: u8, _r: &mut Decoder<'_>) -> R<Self> {
        Err(ModelError::new("fixture schema has no length override"))
    }
    fn encode_length(&self, _w: u8, _o: &mut EncodeBuffer<'_>) -> R<()> {
        Err(ModelError::new("fixture schema has no length override"))
    }
}
macro_rules! integer {($($t:ty),*)=>{$(impl FixtureValue for $t{fn parse(v:&Value)->R<Self>{v.as_str().ok_or_else(||err("integer decimal string"))?.parse().map_err(|_|err("typed integer range"))}fn json(&self)->Value{json!(self.to_string())}})*};}
integer!(u8, u16, u32, u64, u128, i8, i16, i32, i64, i128);
fn err(s: impl Into<String>) -> ModelError {
    ModelError::new(s)
}
impl FixtureValue for bool {
    fn parse(v: &Value) -> R<Self> {
        v.as_bool().ok_or_else(|| err("typed boolean"))
    }
    fn json(&self) -> Value {
        json!(self)
    }
}
impl FixtureValue for () {
    fn parse(v: &Value) -> R<Self> {
        if v.is_null() {
            Ok(())
        } else {
            Err(err("unit null"))
        }
    }
    fn json(&self) -> Value {
        Value::Null
    }
}
impl FixtureValue for String {
    fn parse(v: &Value) -> R<Self> {
        v.as_str()
            .map(ToOwned::to_owned)
            .ok_or_else(|| err("typed string"))
    }
    fn json(&self) -> Value {
        json!(self)
    }
    fn decode_length(w: u8, r: &mut Decoder<'_>) -> R<Self> {
        <Self as LengthDecode>::decode_length(w, r)
    }
    fn encode_length(&self, w: u8, o: &mut EncodeBuffer<'_>) -> R<()> {
        <Self as LengthEncode>::encode_length(self, w, o)
    }
}
impl<T: FixtureValue> FixtureValue for Vec<T> {
    fn parse(v: &Value) -> R<Self> {
        v.as_array()
            .ok_or_else(|| err("typed vector"))?
            .iter()
            .map(T::parse)
            .collect()
    }
    fn json(&self) -> Value {
        json!(self.iter().map(FixtureValue::json).collect::<Vec<_>>())
    }
    fn decode_length(w: u8, r: &mut Decoder<'_>) -> R<Self> {
        <Self as LengthDecode>::decode_length(w, r)
    }
    fn encode_length(&self, w: u8, o: &mut EncodeBuffer<'_>) -> R<()> {
        <Self as LengthEncode>::encode_length(self, w, o)
    }
}
impl<T: FixtureValue> FixtureValue for Option<T> {
    fn parse(v: &Value) -> R<Self> {
        match v["some"].as_bool() {
            Some(false) => Ok(None),
            Some(true) => Ok(Some(T::parse(&v["value"])?)),
            _ => Err(err("typed option")),
        }
    }
    fn json(&self) -> Value {
        match self {
            Some(v) => json!({"some":true,"value":v.json()}),
            None => json!({"some":false}),
        }
    }
}
impl<T: FixtureValue, const N: usize> FixtureValue for [T; N] {
    fn parse(v: &Value) -> R<Self> {
        let values = Vec::<T>::parse(v)?;
        values.try_into().map_err(|_| err("typed array length"))
    }
    fn json(&self) -> Value {
        json!(self.iter().map(FixtureValue::json).collect::<Vec<_>>())
    }
}
macro_rules! tuple {($($t:ident:$i:tt),+)=>{impl<$($t:FixtureValue),+> FixtureValue for ($($t,)+){fn parse(v:&Value)->R<Self>{let a=v.as_array().ok_or_else(||err("typed tuple"))?;if a.len()!=[$(stringify!($t)),+].len(){return Err(err("typed tuple arity"));}Ok(($($t::parse(&a[$i])?,)+))}fn json(&self)->Value{json!([$ (self.$i.json()),+])}}};}
tuple!(A:0);
tuple!(A:0,B:1);
tuple!(A:0,B:1,C:2);
tuple!(A:0,B:1,C:2,D:3);
impl<S: FixtureValue, H: FixtureValue> FixtureValue for OracleState<S, H> {
    fn parse(v: &Value) -> R<Self> {
        Ok(Self {
            model: S::parse(&v["model"])?,
            oracle: H::parse(&v["oracle"])?,
        })
    }
    fn json(&self) -> Value {
        json!({"model":self.model.json(),"oracle":self.oracle.json()})
    }
}
struct LengthValue<T, const W: u8>(T);
impl<T: LengthEncode, const W: u8> TraceEncode for LengthValue<T, W> {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> R<()> {
        <T as LengthEncode>::encode_length(&self.0, W, o)
    }
}
impl<T: LengthDecode, const W: u8> TraceDecode for LengthValue<T, W> {
    fn trace_decode(r: &mut Decoder<'_>) -> R<Self> {
        Ok(Self(<T as LengthDecode>::decode_length(W, r)?))
    }
}
impl<T: FixtureValue + LengthEncode + LengthDecode, const W: u8> FixtureValue
    for LengthValue<T, W>
{
    fn parse(v: &Value) -> R<Self> {
        Ok(Self(T::parse(v)?))
    }
    fn json(&self) -> Value {
        self.0.json()
    }
    fn decode_length(w: u8, r: &mut Decoder<'_>) -> R<Self> {
        Ok(Self(<T as LengthDecode>::decode_length(w, r)?))
    }
    fn encode_length(&self, w: u8, o: &mut EncodeBuffer<'_>) -> R<()> {
        <T as LengthEncode>::encode_length(&self.0, w, o)
    }
}
fn text<'a>(v: &'a Value, k: &str) -> R<&'a str> {
    v[k].as_str()
        .ok_or_else(|| err(format!("missing string {k}")))
}
fn number(v: &Value) -> R<usize> {
    let n = if let Some(s) = v.as_str() {
        s.parse::<u64>().map_err(|_| err("unsigned decimal"))?
    } else {
        v.as_u64().ok_or_else(|| err("unsigned integer"))?
    };
    usize::try_from(n).map_err(|_| err("usize overflow"))
}
fn limits(v: &Value) -> R<DecodeLimits> {
    let mut l = DecodeLimits::default();
    if v.is_null() {
        return Ok(l);
    }
    for (k, v) in v.as_object().ok_or_else(|| err("limits object"))? {
        let n = number(v)?;
        match k.as_str() {
            "bytes" => l.bytes = n,
            "allocation" => l.allocation = n,
            "elements" => l.elements = n,
            "depth" => l.depth = n,
            "work" => l.work = n,
            _ => return Err(err("unknown decoder limit")),
        }
    }
    Ok(l)
}
fn hex(b: &[u8]) -> String {
    b.iter().map(|b| format!("{b:02x}")).collect()
}
fn unhex(s: &str) -> R<Vec<u8>> {
    if !s.len().is_multiple_of(2)
        || !s
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(err("noncanonical hex"));
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).map_err(|e| err(e.to_string())))
        .collect()
}
fn signature(s: &Value) -> R<String> {
    let k = text(s, "kind")?;
    let width = if s["length_width"].is_null() || s["length_width"] == 4 {
        String::new()
    } else {
        format!("@{}", number(&s["length_width"])?)
    };
    Ok(match k {
        "string" => format!("String{width}"),
        "vector" => format!("Vec<{}>{width}", signature(&s["element"])?),
        "option" => format!("Option<{}>", signature(&s["element"])?),
        "array" => format!("[{};{}]", signature(&s["element"])?, number(&s["length"])?),
        "tuple" => format!(
            "({})",
            s["fields"]
                .as_array()
                .ok_or_else(|| err("tuple schemas"))?
                .iter()
                .map(signature)
                .collect::<R<Vec<_>>>()?
                .join(",")
        ),
        "oracle_state" => format!(
            "OracleState<{},{}>",
            signature(&s["model"])?,
            signature(&s["oracle"])?
        ),
        _ => k.into(),
    })
}
// Every registry branch names a concrete Rust type and calls its public traits.
macro_rules! dispatch {($schema:expr,$function:ident $(,$args:expr)*)=>{{let key=signature($schema)?;match key.as_str(){
"u8"=>$function::<u8>($($args),*),"u16"=>$function::<u16>($($args),*),"u32"=>$function::<u32>($($args),*),"u64"=>$function::<u64>($($args),*),"u128"=>$function::<u128>($($args),*),"i8"=>$function::<i8>($($args),*),"i16"=>$function::<i16>($($args),*),"i32"=>$function::<i32>($($args),*),"i64"=>$function::<i64>($($args),*),"i128"=>$function::<i128>($($args),*),"bool"=>$function::<bool>($($args),*),"unit"=>$function::<()>($($args),*),
"String"=>$function::<String>($($args),*),"String@0"=>$function::<LengthValue<String,0>>($($args),*),"String@1"=>$function::<LengthValue<String,1>>($($args),*),"String@2"=>$function::<LengthValue<String,2>>($($args),*),"String@3"=>$function::<LengthValue<String,3>>($($args),*),"String@8"=>$function::<LengthValue<String,8>>($($args),*),
"Vec<u8>"=>$function::<Vec<u8>>($($args),*),"Vec<u8>@1"=>$function::<LengthValue<Vec<u8>,1>>($($args),*),"Vec<u8>@2"=>$function::<LengthValue<Vec<u8>,2>>($($args),*),"Vec<u16>"=>$function::<Vec<u16>>($($args),*),"Vec<u16>@1"=>$function::<LengthValue<Vec<u16>,1>>($($args),*),"Vec<u16>@2"=>$function::<LengthValue<Vec<u16>,2>>($($args),*),"Vec<unit>"=>$function::<Vec<()>>($($args),*),"Vec<Vec<u8>>"=>$function::<Vec<Vec<u8>>>($($args),*),"Vec<Vec<u8>>@1"=>$function::<LengthValue<Vec<Vec<u8>>,1>>($($args),*),"Vec<[u16;3]>"=>$function::<Vec<[u16;3]>>($($args),*),"Vec<Option<u64>>"=>$function::<Vec<Option<u64>>>($($args),*),
"Option<unit>"=>$function::<Option<()>>($($args),*),"Option<Option<unit>>"=>$function::<Option<Option<()>>>($($args),*),"Option<String>"=>$function::<Option<String>>($($args),*),"[u8;0]"=>$function::<[u8;0]>($($args),*),"[u16;1]"=>$function::<[u16;1]>($($args),*),"[u16;3]"=>$function::<[u16;3]>($($args),*),
"(u16)"=>$function::<(u16,)>($($args),*),"(u8,u64)"=>$function::<(u8,u64)>($($args),*),"(unit,i8,bool)"=>$function::<((),i8,bool)>($($args),*),"(u16,i32,bool,Option<u8>)"=>$function::<(u16,i32,bool,Option<u8>)>($($args),*),"OracleState<u16,String>"=>$function::<OracleState<u16,String>>($($args),*),_=>Err(err(format!("unregistered concrete schema {key}")))}}};}
fn width(schema: &Value) -> R<u8> {
    u8::try_from(schema["length_width"].as_u64().unwrap_or(4)).map_err(|_| err("width u8"))
}
fn decode<T: FixtureValue>(bytes: &[u8], entry: &str, l: DecodeLimits, w: u8) -> R<T> {
    if entry == "direct_length" {
        let mut r = Decoder::new(bytes, l)?;
        let value = T::decode_length(w, &mut r)?;
        r.finish()?;
        Ok(value)
    } else {
        T::from_trace(bytes, l)
    }
}
fn encode<T: FixtureValue>(value: &Value, maximum: usize, entry: &str, w: u8) -> R<Vec<u8>> {
    let value = T::parse(value)?;
    if entry == "direct_length" {
        let mut bytes = vec![];
        let mut out = EncodeBuffer::new(&mut bytes, maximum);
        FixtureValue::encode_length(&value, w, &mut out)?;
        out.finish()?;
        Ok(bytes)
    } else {
        value.trace_bytes(maximum)
    }
}
#[derive(Default)]
struct Counts {
    values: usize,
    decode_errors: usize,
    encode_errors: usize,
    prefixes: usize,
    trailing: usize,
    subviews: usize,
    owned: usize,
}
fn value_case<T: FixtureValue>(v: &Value, c: &mut Counts) -> R<()> {
    let bytes = unhex(text(v, "bytes_hex")?)?;
    let entry = text(v, "entry")?;
    let w = width(&v["schema"])?;
    let encoded = encode::<T>(&v["value"], number(&v["encode_max_bytes"])?, entry, w)?;
    if encoded != bytes {
        return Err(err("encoded bytes differ from independent golden"));
    }
    let l = limits(&v["limits"])?;
    let actual = decode::<T>(&bytes, entry, l, w)?;
    if actual.json() != v["expected"]["value"] {
        return Err(err(format!(
            "decoded value {}, expected {}",
            actual.json(),
            v["expected"]["value"]
        )));
    }
    for end in 0..bytes.len() {
        let error = decode::<T>(&bytes[..end], entry, l, w)
            .err()
            .ok_or_else(|| err(format!("prefix {end} accepted")))?;
        if error.to_string() != "truncated value" {
            return Err(err(format!("prefix {end}: {error}")));
        }
        c.prefixes += 1;
    }
    let mut trailing = bytes.clone();
    trailing.push(0);
    let error = decode::<T>(&trailing, entry, DecodeLimits::default(), w)
        .err()
        .ok_or_else(|| err("trailing byte accepted"))?;
    if error.to_string() != "trailing value bytes" {
        return Err(err(format!("trailing: {error}")));
    }
    c.trailing += 1;
    let mut backing = vec![0xff, 0x80, 0x01];
    backing.extend(&bytes);
    backing.extend([0x80, 0xff]);
    let owned = decode::<T>(&backing[3..3 + bytes.len()], entry, l, w)?;
    if owned.json() != actual.json() {
        return Err(err("bounded subview differs"));
    }
    c.subviews += 1;
    backing.fill(0x55);
    if owned.json() != actual.json() {
        return Err(err("decoded result aliases mutable input"));
    }
    c.owned += 1;
    // Encoding appends to caller's existing sink. No callback clears prior bytes.
    let typed = T::parse(&v["value"])?;
    let mut prefixed = vec![];
    {
        let mut sink = EncodeBuffer::new(&mut prefixed, bytes.len() + 2);
        sink.extend_from_slice(b"xy")?;
        if entry == "direct_length" {
            FixtureValue::encode_length(&typed, w, &mut sink)?;
        } else {
            typed.trace_encode(&mut sink)?;
        }
        sink.finish()?;
    }
    let mut wanted = b"xy".to_vec();
    wanted.extend(&bytes);
    if prefixed != wanted {
        return Err(err("encoder replaced caller prefix"));
    }
    c.values += 1;
    Ok(())
}
fn decode_error_case<T: FixtureValue>(v: &Value, c: &mut Counts) -> R<()> {
    let bytes = unhex(text(v, "bytes_hex")?)?;
    let error = decode::<T>(
        &bytes,
        text(v, "entry")?,
        limits(&v["limits"])?,
        width(&v["schema"])?,
    )
    .err()
    .ok_or_else(|| err("expected decode error"))?;
    if error.to_string() != text(&v["expected"], "error")? {
        return Err(err(format!(
            "decode error {error}, expected {}",
            v["expected"]["error"]
        )));
    }
    c.decode_errors += 1;
    Ok(())
}
fn encode_error_case<T: FixtureValue>(v: &Value, c: &mut Counts) -> R<()> {
    let error = encode::<T>(
        &v["value"],
        number(&v["encode_max_bytes"])?,
        text(v, "entry")?,
        width(&v["schema"])?,
    )
    .err()
    .ok_or_else(|| err("expected encode error"))?;
    if error.to_string() != text(&v["expected"], "error")? {
        return Err(err(format!("encode error {error}")));
    }
    c.encode_errors += 1;
    Ok(())
}
thread_local! {static ACTIONS:RefCell<Vec<Value>>=const{RefCell::new(Vec::new())};static EVENTS:RefCell<Vec<String>>=const{RefCell::new(Vec::new())};}
fn event(s: String) {
    EVENTS.with(|e| e.borrow_mut().push(s));
}
struct Scripted(Value);
impl TraceDecode for Scripted {
    fn trace_decode(r: &mut Decoder<'_>) -> R<Self> {
        let action = ACTIONS
            .with(|a| a.borrow().last().cloned())
            .ok_or_else(|| err("missing custom script"))?;
        event("enter".into());
        for op in action["body"]
            .as_array()
            .ok_or_else(|| err("custom body"))?
        {
            let kind = text(op, "kind")?;
            let result = reader_action(r, op);
            match result {
                Ok(_) => event(format!("{kind}:ok")),
                Err(e) => {
                    event(format!("{kind}:error:{e}"));
                    if op["on_error"] == "swallow" {
                        event("swallow".into());
                    } else {
                        event(format!("propagate:{e}"));
                        return Err(e);
                    }
                }
            }
        }
        if let Some(message) = action["returns"]["error"].as_str() {
            event(format!("return_error:{message}"));
            Err(err(message.to_owned()))
        } else {
            event("return_ok".into());
            Ok(Self(action["returns"]["ok"].clone()))
        }
    }
}
fn reader_decode<T: FixtureValue>(r: &mut Decoder<'_>, mode: &str, w: u8) -> R<Value> {
    let v = match mode {
        "value" => r.value::<T>()?,
        "direct_decode" => T::trace_decode(r)?,
        "direct_length" => T::decode_length(w, r)?,
        _ => return Err(err("reader decode mode")),
    };
    Ok(v.json())
}
fn reader_action(r: &mut Decoder<'_>, a: &Value) -> R<Value> {
    let kind = text(a, "kind")?;
    match kind {
        "take" => Ok(json!(hex(r.take(number(&a["count"])?)?))),
        "collection" => {
            r.collection(number(&a["count"])?, number(&a["element_size"])?)?;
            Ok(Value::Null)
        }
        "finish" => {
            r.finish()?;
            Ok(Value::Null)
        }
        "value" | "direct_decode" | "direct_length" => {
            let w = if a["width"].is_null() {
                width(&a["schema"])?
            } else {
                u8::try_from(number(&a["width"])?).map_err(|_| err("width u8"))?
            };
            dispatch!(&a["schema"], reader_decode, r, kind, w)
        }
        "value_custom" => {
            ACTIONS.with(|actions| actions.borrow_mut().push(a.clone()));
            let result = r.value::<Scripted>().map(|v| v.0);
            ACTIONS.with(|actions| {
                actions.borrow_mut().pop();
            });
            result
        }
        _ => Err(err(format!("unknown reader operation {kind}"))),
    }
}
fn normalized_result(result: R<Value>) -> Value {
    match result {
        Ok(value) => json!({"value":value,"error":null}),
        Err(e) => json!({"value":null,"error":e.to_string()}),
    }
}
fn reader_case(v: &Value) -> R<()> {
    EVENTS.with(|e| e.borrow_mut().clear());
    ACTIONS.with(|a| a.borrow_mut().clear());
    let bytes = unhex(text(v, "bytes_hex")?)?;
    let mut results = vec![];
    let actual = match Decoder::new(&bytes, limits(&v["limits"])?) {
        Err(e) => json!({"construction_error":e.to_string(),"results":[],"callback_events":[]}),
        Ok(mut r) => {
            for a in v["actions"]
                .as_array()
                .ok_or_else(|| err("reader actions"))?
            {
                results.push(normalized_result(reader_action(&mut r, a)));
            }
            let events = EVENTS.with(|e| e.borrow().clone());
            json!({"results":results,"callback_events":events})
        }
    };
    if actual != v["expected"] {
        return Err(err(format!(
            "reader expected {}, actual {actual}",
            v["expected"]
        )));
    }
    Ok(())
}
fn measure<T>(name: &str) -> Value {
    json!({"name":name,"rust_type":std::any::type_name::<T>(),"size":std::mem::size_of::<T>(),"alignment":std::mem::align_of::<T>()})
}
fn layout_case<T: FixtureValue>(schema: &Value) -> R<Value> {
    Ok(
        json!({"schema":schema,"rust_type":std::any::type_name::<T>(),"size":std::mem::size_of::<T>(),"alignment":std::mem::align_of::<T>()}),
    )
}
fn layouts(root: &Value) -> R<Value> {
    let mut schemas = std::collections::BTreeMap::<String, Value>::new();
    for k in ["value_cases", "decode_error_cases", "encode_error_cases"] {
        for row in root[k]
            .as_array()
            .ok_or_else(|| err("layout corpus cases"))?
        {
            let schema = &row["schema"];
            schemas
                .entry(serde_json::to_string(schema).map_err(|e| err(e.to_string()))?)
                .or_insert_with(|| schema.clone());
        }
    }
    let mut registry = vec![];
    for schema in schemas.values() {
        registry.push(dispatch!(schema, layout_case, schema)?);
    }
    let named = vec![
        measure::<u8>("u8"),
        measure::<u16>("u16"),
        measure::<u32>("u32"),
        measure::<u64>("u64"),
        measure::<u128>("u128"),
        measure::<i8>("i8"),
        measure::<i16>("i16"),
        measure::<i32>("i32"),
        measure::<i64>("i64"),
        measure::<i128>("i128"),
        measure::<bool>("bool"),
        measure::<()>("unit"),
        measure::<String>("String"),
        measure::<Vec<u8>>("Vec<T> header"),
        measure::<Option<()>>("Option<()>"),
        measure::<Option<bool>>("Option<bool>"),
        measure::<Option<u8>>("Option<u8>"),
        measure::<Option<u64>>("Option<u64>"),
        measure::<Option<String>>("Option<String>"),
        measure::<[u16; 3]>("[u16;3]"),
        measure::<(u8, u64)>("(u8,u64)"),
    ];
    for row in &named {
        let want = &root["layout_convention"]["expected_sizes"][row["name"].as_str().unwrap()];
        if number(want)? != row["size"].as_u64().unwrap() as usize {
            return Err(err(format!("layout differs from candidate expected {row}")));
        }
    }
    let additional_measured = vec![
        measure::<Option<i8>>("Option<i8>"),
        measure::<Option<u16>>("Option<u16>"),
        measure::<Option<i16>>("Option<i16>"),
        measure::<Option<u32>>("Option<u32>"),
        measure::<Option<i32>>("Option<i32>"),
        measure::<Option<i64>>("Option<i64>"),
        measure::<Option<u128>>("Option<u128>"),
        measure::<Option<i128>>("Option<i128>"),
        measure::<((),)>("((),)"),
        measure::<(u8, u8)>("(u8,u8)"),
        measure::<((), ())>("((),())"),
    ];
    Ok(
        json!({"usize_bits":usize::BITS,"registry":registry,"named":named,"additional_measured":additional_measured}),
    )
}
fn json_file(path: &Path) -> R<Value> {
    let mut bytes = vec![];
    std::fs::File::open(path)
        .map_err(|e| err(e.to_string()))?
        .take(8388609)
        .read_to_end(&mut bytes)
        .map_err(|e| err(e.to_string()))?;
    if bytes.len() > 8388608 {
        return Err(err("JSON byte limit"));
    }
    serde_json::from_slice(&bytes).map_err(|e| err(e.to_string()))
}
pub fn layout_receipt(path: &Path) -> Result<Value, String> {
    layouts(&json_file(path).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}
fn length_cases(root: &Value) -> R<Value> {
    let mut arithmetic = 0;
    let mut executed = 0;
    let mut skipped = vec![];
    for row in root["length_cases"]
        .as_array()
        .ok_or_else(|| err("length cases"))?
    {
        match text(row, "operation")? {
            "write_length_primitive" => {
                arithmetic += 1;
                skipped.push(json!({"case":row["case"],"scope":"arithmetic_only_not_an_exported_rust_primitive","public_rust_executed":false}));
            }
            "encode_length_empty" => {
                let mut bytes = vec![];
                let mut sink = EncodeBuffer::new(&mut bytes, 64);
                let error = <String as LengthEncode>::encode_length(
                    &String::new(),
                    u8::try_from(number(&row["width"])?).map_err(|_| err("width u8"))?,
                    &mut sink,
                )
                .err()
                .ok_or_else(|| err("unsupported length width accepted"))?;
                if error.to_string() != text(&row["expected"], "error")? {
                    return Err(err("length override error differs"));
                }
                executed += 1;
            }
            _ => return Err(err("length operation")),
        }
    }
    Ok(
        json!({"arithmetic_only_not_executed":arithmetic,"public_unsupported_width_executed":executed,"arithmetic_cases":skipped}),
    )
}
pub fn qualify(path: &Path) -> Result<Value, String> {
    let result = (|| -> R<Value> {
        let root = json_file(path)?;
        failed_sink_callback()?;
        length_only_control()?;
        if root["profile"] != "canonical-value-codec-v1" {
            return Err(err("profile"));
        }
        let mut c = Counts::default();
        for row in root["value_cases"]
            .as_array()
            .ok_or_else(|| err("value cases"))?
        {
            dispatch!(&row["schema"], value_case, row, &mut c)
                .map_err(|e| err(format!("{}: {e}", row["case"])))?;
        }
        for row in root["decode_error_cases"]
            .as_array()
            .ok_or_else(|| err("decode cases"))?
        {
            dispatch!(&row["schema"], decode_error_case, row, &mut c)
                .map_err(|e| err(format!("{}: {e}", row["case"])))?;
        }
        for row in root["encode_error_cases"]
            .as_array()
            .ok_or_else(|| err("encode cases"))?
        {
            dispatch!(&row["schema"], encode_error_case, row, &mut c)
                .map_err(|e| err(format!("{}: {e}", row["case"])))?;
        }
        let mut readers = 0;
        for row in root["reader_cases"]
            .as_array()
            .ok_or_else(|| err("reader cases"))?
        {
            reader_case(row).map_err(|e| err(format!("{}: {e}", row["case"])))?;
            readers += 1;
        }
        let mut encoders = 0;
        for row in root["encoder_cases"]
            .as_array()
            .ok_or_else(|| err("encoder cases"))?
        {
            encoder_case(row).map_err(|e| err(format!("{}: {e}", row["case"])))?;
            encoders += 1;
        }
        let native=root["native_admission_cases"].as_array().ok_or_else(||err("native cases"))?.iter().map(|r|json!({"case":r["case"],"scope":"native_only","public_rust_executed":false,"reason":"Not representable as the specified valid Rust typed input; no narrowing cast or fabricated trait execution."})).collect::<Vec<_>>();
        Ok(
            json!({"profile":"canonical-value-codec-v1","value_cases":c.values,"decode_error_cases":c.decode_errors,"encode_error_cases":c.encode_errors,"reader_cases":readers,"encoder_cases":encoders,"failed_sink_callback_invoked":true,"length_only_without_trace_traits":true,"proper_prefixes":c.prefixes,"trailing_byte_rejections":c.trailing,"bounded_subviews":c.subviews,"owned_results":c.owned,"native_only_skipped":native,"length_cases":length_cases(&root)?,"layouts":layouts(&root)?}),
        )
    })();
    result.map_err(|e| e.to_string())
}
struct EncodeFailure {
    prefix: Vec<u8>,
    message: Option<String>,
    ignore_prefix_error: bool,
}
impl TraceEncode for EncodeFailure {
    fn trace_encode(&self, out: &mut EncodeBuffer<'_>) -> R<()> {
        let result = out.extend_from_slice(&self.prefix);
        if !self.ignore_prefix_error {
            result?;
        }
        match &self.message {
            Some(message) => Err(err(message.clone())),
            None => Ok(()),
        }
    }
}
fn encode_action<T: FixtureValue>(action: &Value, out: &mut EncodeBuffer<'_>) -> R<Value> {
    let value = T::parse(&action["value"])?;
    match text(action, "kind")? {
        "direct_length" => FixtureValue::encode_length(
            &value,
            u8::try_from(number(&action["width"])?).map_err(|_| err("width u8"))?,
            out,
        )?,
        "direct_encode" => value.trace_encode(out)?,
        _ => return Err(err("encode action")),
    };
    Ok(Value::Null)
}
fn encoder_case(v: &Value) -> R<()> {
    let mut bytes = vec![];
    let mut results = vec![];
    {
        let mut out = EncodeBuffer::new(&mut bytes, number(&v["max_bytes"])?);
        for a in v["actions"]
            .as_array()
            .ok_or_else(|| err("encoder actions"))?
        {
            let result = match text(a, "kind")? {
                "direct_length" | "direct_encode" => {
                    dispatch!(&a["schema"], encode_action, a, &mut out)
                }
                "direct_encode_custom" => EncodeFailure {
                    prefix: unhex(text(a, "prefix_hex")?)?,
                    message: a["returns_error"].as_str().map(ToOwned::to_owned),
                    ignore_prefix_error: a["ignore_prefix_error"] == true,
                }
                .trace_encode(&mut out)
                .map(|_| Value::Null),
                "append" => out
                    .extend_from_slice(&unhex(text(a, "bytes_hex")?)?)
                    .map(|_| Value::Null),
                "finish" => out.finish().map(|_| Value::Null),
                _ => return Err(err("encoder operation")),
            };
            results.push(normalized_result(result));
        }
    }
    let actual = json!({"results":results,"bytes_hex":hex(&bytes)});
    let mut expected = v["expected"].clone();
    for result in expected["results"]
        .as_array_mut()
        .ok_or_else(|| err("encoder expected results"))?
    {
        result
            .as_object_mut()
            .ok_or_else(|| err("encoder result object"))?
            .remove("error_family");
    }
    if actual != expected {
        return Err(err(format!(
            "encoder expected {}, actual {actual}",
            v["expected"]
        )));
    }
    Ok(())
}
fn trusted_contract() -> R<Value> {
    serde_json::from_str(include_str!(
        "../../../conformance/canonical-value-codec-v1.json"
    ))
    .map_err(|e| err(e.to_string()))
}
#[derive(Default)]
struct BundleStats {
    checks: Counts,
    short_caps: usize,
    decoded: Vec<Value>,
}
fn bundle_case<T: FixtureValue>(
    row: &Value,
    incoming: Option<&Value>,
    stats: &mut BundleStats,
) -> R<Value> {
    let entry = text(row, "entry")?;
    let w = width(&row["schema"])?;
    let bytes = match incoming {
        Some(v) => unhex(text(v, "hex")?)?,
        None => encode::<T>(&row["value"], number(&row["encode_max_bytes"])?, entry, w)?,
    };
    let actual = decode::<T>(&bytes, entry, limits(&row["limits"])?, w)?;
    let value = actual.json();
    let encoded = encode::<T>(&value, bytes.len(), entry, w)?;
    if encoded != bytes {
        return Err(err("noncanonical typed bundle encoding"));
    }
    stats.decoded.push(json!({"id":row["case"],"value":value}));
    value_case::<T>(row, &mut stats.checks)?;
    if !bytes.is_empty() {
        if encode::<T>(&value, bytes.len() - 1, entry, w).is_ok() {
            return Err(err("one-under typed encode cap accepted"));
        }
        stats.short_caps += 1;
    }
    Ok(json!({"id":row["case"],"hex":hex(&encoded)}))
}
fn bundle_controls(stats: &BundleStats) -> R<Value> {
    let bytes = [0xff; 4];
    let failure = <Vec<()> as TraceDecode>::from_trace(
        &bytes,
        DecodeLimits {
            elements: 16,
            ..DecodeLimits::default()
        },
    )
    .err()
    .ok_or_else(|| err("huge unit vector admitted"))?;
    if failure.to_string() != "decode element limit" {
        return Err(err(format!("unit vector error {failure}")));
    }
    let unit_error = failure.to_string();
    let script = json!({"kind":"value_custom","body":[{"kind":"take","count":"2","on_error":"swallow"}],"returns":{"ok":null}});
    let mut reader = Decoder::new(&[0], DecodeLimits::default())?;
    let sticky = reader_action(&mut reader, &script)
        .err()
        .ok_or_else(|| err("swallowed take did not remain sticky"))?;
    if sticky.to_string() != "truncated value"
        || reader.finish().err().map(|e| e.to_string()).as_deref() != Some("truncated value")
    {
        return Err(err("sticky first error changed"));
    }
    let bytes = [2, 1, 2];
    let l = DecodeLimits {
        work: 3,
        depth: 1,
        ..DecodeLimits::default()
    };
    let mut reader = Decoder::new(&bytes, l)?;
    let value = <Vec<u8> as LengthDecode>::decode_length(1, &mut reader)?;
    reader.finish()?;
    if value != [1, 2] {
        return Err(err("direct length value"));
    }
    let failure = <LengthValue<Vec<u8>, 1> as TraceDecode>::from_trace(
        &bytes,
        DecodeLimits { depth: 2, ..l },
    )
    .err()
    .ok_or_else(|| err("guarded length omitted own guard"))?;
    if failure.to_string() != "decode work limit" {
        return Err(err(format!("guarded length error {failure}")));
    }
    let mut appended = vec![];
    {
        let mut out = EncodeBuffer::new(&mut appended, 3);
        out.extend_from_slice(&[0xaa])?;
        4660u16.trace_encode(&mut out)?;
        out.finish()?;
    }
    if appended != [0xaa, 0x34, 0x12] {
        return Err(err("typed append replaced prefix"));
    }
    Ok(
        json!({"prefix_rejections":stats.checks.prefixes,"trailing_rejections":stats.checks.trailing,"subview_decodes":stats.checks.subviews,"owned_results":stats.checks.owned,"exact_encode_caps":stats.checks.values,"short_encode_caps":stats.short_caps,"append_preserved":true,"unit_budget_rejected":true,"unit_budget_error":unit_error,"sticky_swallowed_error":true,"sticky_error":sticky.to_string(),"direct_length_work_exact":true}),
    )
}
fn private_bytes_new(path: &Path, bytes: &[u8]) -> R<()> {
    use std::io::Write;
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut f = options.open(path).map_err(|e| err(e.to_string()))?;
    f.write_all(bytes)
        .and_then(|_| f.sync_all())
        .map_err(|e| err(e.to_string()))
}
fn bundle(input: Option<&Path>, output: &Path) -> R<Value> {
    let contract = trusted_contract()?;
    let rows = contract["value_cases"]
        .as_array()
        .ok_or_else(|| err("trusted value cases"))?;
    let incoming = if let Some(path) = input {
        Some(json_file(path)?)
    } else {
        None
    };
    if let Some(v) = &incoming
        && (v["schema"] != "statelessness.canonical-value-consumer/1"
            || v["cases"].as_array().map(Vec::len) != Some(rows.len()))
    {
        return Err(err("closed typed bundle schema/count"));
    }
    let mut stats = BundleStats::default();
    let mut encoded = vec![];
    for (index, row) in rows.iter().enumerate() {
        let received = incoming.as_ref().map(|v| &v["cases"][index]);
        if let Some(v) = received
            && (v["id"] != row["case"])
        {
            return Err(err("closed typed bundle ID/order"));
        }
        encoded.push(dispatch!(
            &row["schema"],
            bundle_case,
            row,
            received,
            &mut stats
        )?);
    }
    let value = json!({"schema":"statelessness.canonical-value-consumer/1","cases":encoded});
    private_bytes_new(
        output,
        &serde_json::to_vec(&value).map_err(|e| err(e.to_string()))?,
    )?;
    Ok(
        json!({"producer":"rust","mode":if input.is_some(){"transcode"}else{"values"},"case_count":rows.len(),"decoded_values":stats.decoded,"controls":bundle_controls(&stats)?}),
    )
}
pub fn values(output: &Path) -> Result<Value, String> {
    bundle(None, output).map_err(|e| e.to_string())
}
pub fn transcode(input: &Path, output: &Path) -> Result<Value, String> {
    bundle(Some(input), output).map_err(|e| e.to_string())
}

use stateless::{
    evidence::{
        Artifact, CausalContext, EvidenceLimits, EvidenceRecorder, OutputOrigin, Provenance,
        TraceV2,
    },
    execution::{self, CheckPolicy, ReplayOptions, ReplayOutcome, ReplayReport},
    model::{
        Check, CheckSink, CheckStatus, Disposition, Model, ModelCodec, ModelMetadata, Transition,
        TransitionRef,
    },
    monitor::RecorderOptions,
    trace::{ReadLimits, RunConfig, Termination, Trace},
};
fn checks_json(checks: &[Check]) -> Vec<Value> {
    checks
        .iter()
        .map(|c| {
            let (status, reason) = match &c.status {
                CheckStatus::Passed => ("passed", None),
                CheckStatus::Failed(s) => ("failed", Some(s)),
                CheckStatus::Skipped(s) => ("skipped", Some(s)),
            };
            json!({"id":c.id.as_str(),"status":status,"reason":reason})
        })
        .collect()
}
fn to_json(t: &Trace) -> Value {
    let (kind, reason) = match &t.termination {
        Termination::Completed => ("completed", None),
        Termination::PropertyFailed => ("property_failed", None),
        Termination::StepLimit => ("step_limit", None),
        Termination::Interrupted => ("interrupted", None),
        Termination::ModelError(s) => ("model_error", Some(s)),
    };
    let steps = t.steps.iter().map(|s| {
        let (kind,reason) = match &s.disposition { Disposition::Accepted => ("accepted",None), Disposition::Rejected(s) => ("rejected",Some(s)), Disposition::Ignored(s) => ("ignored",Some(s)) };
        json!({"input":hex(&s.input),"disposition":{"kind":kind,"reason":reason},"outputs":s.outputs.iter().map(|b|hex(b)).collect::<Vec<_>>(),"post_state":hex(&s.post_state),"checks":checks_json(&s.checks)})
    }).collect::<Vec<_>>();
    json!({"metadata":{"name":t.metadata.name,"model_version":t.metadata.model_version,"properties_version":t.metadata.properties_version,"codec_version":t.metadata.codec_version,"build":t.metadata.build},
        "config":{"strategy":t.config.strategy,"seed":t.config.seed.map(|n|n.to_string()),"parameters":t.config.parameters},
        "initial_state":hex(&t.initial_state),"initial_checks":checks_json(&t.initial_checks),"steps":steps,"termination":{"kind":kind,"reason":reason}})
}
fn provenance_json(v: &Provenance) -> Value {
    json!({"run":v.run,"epoch":v.epoch.to_string(),"manifest":v.manifest,"source":v.source,"environment":v.environment})
}
fn context_json(v: &CausalContext) -> Value {
    json!({"machine":v.machine,"generation":v.generation.map(|n|n.to_string()),"correlation":v.correlation,"logical_time":v.logical_time.map(|n|n.to_string()),"causes":v.causes.iter().map(|o|json!({"sequence":o.sequence.to_string(),"output":o.output})).collect::<Vec<_>>()})
}
fn evidence_json(v: &TraceV2) -> Value {
    json!({"trace":to_json(&v.trace),"checkpoint_after":v.checkpoint_after.to_string(),"provenance":provenance_json(&v.provenance),"steps":v.steps.iter().map(|s|json!({"sequence":s.sequence.to_string(),"state_check_count":s.state_check_count,"causal":context_json(&s.causal)})).collect::<Vec<_>>()})
}
fn report_json(r: &ReplayReport) -> Value {
    let (outcome, step, field) = match &r.outcome {
        ReplayOutcome::Exact => ("exact", None, None),
        ReplayOutcome::Diverged { step, field } => ("diverged", *step, Some(*field)),
        ReplayOutcome::Incompatible { .. } => ("incompatible", None, None),
    };
    json!({"outcome":outcome,"divergence_step":step,"divergence_field":field,"steps_verified":r.steps_verified,"failure_reproduced":r.failure_reproduced,"build_matches":r.build_matches})
}
struct CountedEncode<'a>(&'a Cell<usize>);
impl TraceEncode for CountedEncode<'_> {
    fn trace_encode(&self, _: &mut EncodeBuffer<'_>) -> R<()> {
        self.0.set(self.0.get() + 1);
        Err(err("callback after sink failure"))
    }
}
fn failed_sink_callback() -> R<()> {
    let mut bytes = vec![];
    let mut sink = EncodeBuffer::new(&mut bytes, 1);
    if sink.extend_from_slice(&[1, 2]).is_ok() {
        return Err(err("failed-sink setup accepted"));
    }
    let calls = Cell::new(0);
    let error = CountedEncode(&calls)
        .trace_encode(&mut sink)
        .err()
        .ok_or_else(|| err("expected callback error"))?;
    if calls.get() != 1
        || error.to_string() != "callback after sink failure"
        || sink.finish().err().map(|e| e.to_string()).as_deref()
            != Some("encoded blob exceeds byte or allocation limit")
    {
        return Err(err("raw callback after failure semantics"));
    }
    Ok(())
}
#[derive(Clone, PartialEq, Eq)]
struct EncodeOnlyOutput {
    code: u8,
    message: String,
}
// Deliberately no TraceDecode implementation or historical-output decoder.
impl TraceEncode for EncodeOnlyOutput {
    fn trace_encode(&self, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.code.trace_encode(o)?;
        self.message.trace_encode(o)
    }
}
#[derive(Default)]
struct TypedModel {
    initial: Cell<usize>,
    reducer: Cell<usize>,
    states: Cell<usize>,
    turns: Cell<usize>,
    legacy: Cell<usize>,
    into: Cell<usize>,
}
impl TypedModel {
    fn legacy<T>(&self) -> R<T> {
        self.legacy.set(self.legacy.get() + 1);
        Err(err("legacy application callback called"))
    }
    fn counts(&self) -> Value {
        json!({"initial_state":self.initial.get(),"reducer":self.reducer.get(),"state":self.states.get(),"transition":self.turns.get()})
    }
    fn total(state: &[Option<u64>]) -> R<u64> {
        state.iter().flatten().try_fold(0u64, |sum, n| {
            sum.checked_add(*n).ok_or_else(|| err("model sum overflow"))
        })
    }
}
impl Model for TypedModel {
    type State = Vec<Option<u64>>;
    type Input = Option<u64>;
    type Output = EncodeOnlyOutput;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "native-values-threshold".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "application-v1".into(),
        }
    }
    fn initial_state(&self) -> R<Self::State> {
        self.initial.set(self.initial.get() + 1);
        Ok(vec![])
    }
    fn step(&self, s: &Self::State, i: &Self::Input) -> R<Transition<Self::State, Self::Output>> {
        self.reducer.set(self.reducer.get() + 1);
        let mut state = s.clone();
        state.push(*i);
        Ok(Transition::accepted(
            state,
            vec![EncodeOnlyOutput {
                code: 7,
                message: "\u{feff}changed".into(),
            }],
        ))
    }
    fn check_state(&self, _: &Self::State) -> R<Vec<Check>> {
        self.legacy()
    }
    fn check_state_into(&self, s: &Self::State, c: &mut CheckSink<'_>) -> R<()> {
        self.states.set(self.states.get() + 1);
        c.push(if Self::total(s)? >= 3 {
            Check::failed("threshold.bound", "at least three")
        } else {
            Check::passed("threshold.bound")
        });
        c.push(Check::passed("state.appended"));
        Ok(())
    }
    fn check_transition(
        &self,
        _: &Self::State,
        _: &Self::Input,
        _: &TransitionRef<'_, Self::State, Self::Output>,
    ) -> R<Vec<Check>> {
        self.legacy()
    }
    fn check_transition_into(
        &self,
        _: &Self::State,
        _: &Self::Input,
        _: &TransitionRef<'_, Self::State, Self::Output>,
        c: &mut CheckSink<'_>,
    ) -> R<()> {
        self.turns.set(self.turns.get() + 1);
        c.push(Check::passed("edge.checked"));
        Ok(())
    }
}
impl ModelCodec for TypedModel {
    fn encode_state(&self, _: &Self::State) -> R<Vec<u8>> {
        self.legacy()
    }
    fn encode_input(&self, _: &Self::Input) -> R<Vec<u8>> {
        self.legacy()
    }
    fn encode_output(&self, _: &Self::Output) -> R<Vec<u8>> {
        self.legacy()
    }
    fn encode_state_into(&self, s: &Self::State, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.into.set(self.into.get() + 1);
        s.trace_encode(o)
    }
    fn encode_input_into(&self, i: &Self::Input, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.into.set(self.into.get() + 1);
        i.trace_encode(o)
    }
    fn encode_output_into(&self, v: &Self::Output, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.into.set(self.into.get() + 1);
        v.trace_encode(o)
    }
    fn decode_state(&self, b: &[u8]) -> R<Self::State> {
        <Vec<Option<u64>> as TraceDecode>::from_trace(b, DecodeLimits::default())
    }
    fn decode_input(&self, b: &[u8]) -> R<Self::Input> {
        <Option<u64> as TraceDecode>::from_trace(b, DecodeLimits::default())
    }
}
fn evidence_limits() -> EvidenceLimits {
    EvidenceLimits {
        trace: ReadLimits {
            max_total_bytes: 16384,
            max_frame_bytes: 4096,
            max_blob_bytes: 64,
            max_string_bytes: 256,
            max_steps: 16,
            max_checks_per_step: 16,
            max_outputs_per_step: 16,
            max_parameters: 32,
            max_items: 256,
        },
        max_metadata_bytes: 1024,
        max_labels: 8,
        max_label_bytes: 128,
        max_causes_per_step: 4,
        max_total_causes: 8,
    }
}
fn retained_config(c: &RunConfig) -> Value {
    json!({"strategy":c.strategy,"seed":c.seed.map(|n|n.to_string()),"parameters":c.parameters})
}
fn typed_report(e: &TraceV2) -> R<Value> {
    let m = TypedModel::default();
    let report = e.replay(&m, ReplayOptions::default(), &evidence_limits())?;
    if report.outcome != ReplayOutcome::Exact || m.legacy.get() != 0 {
        return Err(err("typed evidence failed exact replay/preferred dispatch"));
    }
    Ok(report_json(&report))
}
fn portable(t: &Trace) -> Value {
    let (kind, error) = match &t.termination {
        Termination::Completed => ("completed", ""),
        Termination::PropertyFailed => ("property_failed", ""),
        Termination::Interrupted => ("interrupted", ""),
        Termination::StepLimit => ("step_limit", ""),
        Termination::ModelError(s) => ("model_error", s.as_str()),
    };
    json!({"format":"stateless.trace-json","version":1,"metadata":{"name":t.metadata.name,"model_version":t.metadata.model_version,"properties_version":t.metadata.properties_version,"codec_version":t.metadata.codec_version,"build":t.metadata.build},"initial_state":hex(&t.initial_state),"initial_checks":t.initial_checks.iter().map(check_details).collect::<Vec<_>>(),"steps":t.steps.iter().map(|s|json!({"input":hex(&s.input),"outputs":s.outputs.iter().map(|b|hex(b)).collect::<Vec<_>>(),"post_state":hex(&s.post_state),"disposition":{"kind":"accepted","reason":""},"checks":s.checks.iter().map(check_details).collect::<Vec<_>>() })).collect::<Vec<_>>(),"termination":kind,"error":error})
}
fn check_details(c: &Check) -> Value {
    let (status, details) = match &c.status {
        CheckStatus::Passed => ("passed", ""),
        CheckStatus::Failed(s) => ("failed", s.as_str()),
        CheckStatus::Skipped(s) => ("skipped", s.as_str()),
    };
    json!({"id":c.id.as_str(),"status":status,"details":details})
}
pub fn record(prefix: &str) -> Result<Value, String> {
    (function_record(prefix)).map_err(|e| e.to_string())
}
fn function_record(prefix: &str) -> R<Value> {
    let model = TypedModel::default();
    let checkpoint = Vec::<Option<u64>>::new();
    let initial = execution::check_initial(&model, &checkpoint)?;
    let provenance = Provenance {
        run: "installed-values-run".into(),
        epoch: 1,
        manifest: Some("canonical-value-codec-v1".into()),
        source: Some("consumer-source".into()),
        environment: vec![("schema".into(), "vec-option-u64".into())],
    };
    let before = model.counts();
    let mut recorder = EvidenceRecorder::new(
        &initial,
        0,
        RunConfig {
            strategy: "manual".into(),
            seed: None,
            parameters: vec![],
        },
        RecorderOptions {
            max_steps: 3,
            max_retained_bytes: 16384,
            limits: evidence_limits().trace,
        },
        provenance,
        evidence_limits(),
    )?;
    if model.counts() != before {
        return Err(err("typed attachment rechecked"));
    }
    let mut state = vec![];
    for seq in 1..=3 {
        let input = Some(1);
        let transition = model.step(&state, &input)?;
        let token = execution::check_turn(
            &model,
            &state,
            &input,
            &transition,
            seq,
            CheckPolicy::default(),
        )?;
        let before = model.counts();
        recorder.observe(
            &token,
            CausalContext {
                machine: Some("worker-a".into()),
                generation: Some(9),
                correlation: Some("request-1".into()),
                logical_time: Some(10 + seq),
                causes: if seq == 1 {
                    vec![]
                } else {
                    vec![OutputOrigin {
                        sequence: seq - 1,
                        output: 0,
                    }]
                },
            },
        )?;
        if model.counts() != before {
            return Err(err("typed handoff rechecked"));
        }
        state = transition.state;
    }
    let counts = model.counts();
    if counts != json!({"initial_state":0,"reducer":3,"state":4,"transition":3}) {
        return Err(err("typed execution counts"));
    }
    let evidence = recorder.into_trace();
    evidence.write_raw_new(Path::new(&format!("{prefix}.v2")), &evidence_limits())?;
    let mut bytes = vec![];
    evidence
        .trace
        .write_with_limits(&mut bytes, &evidence_limits().trace)
        .map_err(|e| err(e.to_string()))?;
    private_bytes_new(Path::new(&format!("{prefix}.v1")), &bytes)?;
    private_bytes_new(
        Path::new(&format!("{prefix}.json")),
        &serde_json::to_vec(&portable(&evidence.trace)).map_err(|e| err(e.to_string()))?,
    )?;
    Ok(
        json!({"producer":"rust","mode":"record","version":2,"main_counts":counts,"output_encode_only":true,"legacy_encoder_calls":model.legacy.get(),"codec_into_calls":model.into.get(),"report":typed_report(&evidence)?,"evidence":evidence_json(&evidence),"retained_config":retained_config(&evidence.trace.config)}),
    )
}
pub fn replay(input: &Path, output: &Path) -> Result<Value, String> {
    (function_replay(input, output)).map_err(|e| e.to_string())
}
fn function_replay(input: &Path, output: &Path) -> R<Value> {
    let evidence = match Artifact::read(
        std::fs::File::open(input).map_err(|e| err(e.to_string()))?,
        &evidence_limits(),
    )? {
        Artifact::V2(e) => e,
        Artifact::V1(_) => return Err(err("expected typed v2 artifact")),
    };
    let report = typed_report(&evidence)?;
    evidence.write_raw_new(output, &evidence_limits())?;
    Ok(
        json!({"producer":"rust","mode":"replay","version":2,"report":report,"evidence":evidence_json(&evidence),"retained_config":retained_config(&evidence.trace.config)}),
    )
}
// The public length traits deliberately have no TraceEncode/TraceDecode
// supertraits. This concrete type implements only those independent surfaces.
struct LengthOnly(Vec<u8>);
impl LengthEncode for LengthOnly {
    fn encode_length(&self, w: u8, o: &mut EncodeBuffer<'_>) -> R<()> {
        <Vec<u8> as LengthEncode>::encode_length(&self.0, w, o)
    }
}
impl LengthDecode for LengthOnly {
    fn decode_length(w: u8, r: &mut Decoder<'_>) -> R<Self> {
        Ok(Self(<Vec<u8> as LengthDecode>::decode_length(w, r)?))
    }
}
fn length_only_control() -> R<()> {
    let value = LengthOnly(vec![1, 2]);
    let mut bytes = vec![];
    {
        let mut sink = EncodeBuffer::new(&mut bytes, 3);
        value.encode_length(1, &mut sink)?;
        sink.finish()?;
    }
    if bytes != [2, 1, 2] {
        return Err(err("length-only direct encode"));
    }
    let mut reader = Decoder::new(
        &bytes,
        DecodeLimits {
            work: 3,
            depth: 1,
            ..DecodeLimits::default()
        },
    )?;
    let direct = LengthOnly::decode_length(1, &mut reader)?;
    reader.finish()?;
    if direct.0 != [1, 2] {
        return Err(err("length-only direct decode"));
    }
    let wrapped = LengthValue::<LengthOnly, 1>(value);
    if wrapped.trace_bytes(3)? != bytes {
        return Err(err("length-only guarded encode"));
    }
    let guarded = <LengthValue<LengthOnly, 1> as TraceDecode>::from_trace(
        &bytes,
        DecodeLimits {
            work: 4,
            depth: 2,
            ..DecodeLimits::default()
        },
    )?;
    if guarded.0.0 != [1, 2] {
        return Err(err("length-only guarded decode"));
    }
    Ok(())
}

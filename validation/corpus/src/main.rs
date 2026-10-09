//! JSONL qualification adapter over the real Rust engine, not a reimplementation.
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{json, Map, Value};
use stateless::execution::{self, CheckPolicy, ReplayOptions, ReplayOutcome};
use stateless::explore::{self, CheckPhase, SearchConfig, SearchTermination};
use stateless::model::*;
use stateless::trace::{RunConfig, Termination, Trace, TraceStep};
use std::cell::Cell;
use std::collections::BTreeMap;
use std::fmt;
use std::hash::{Hash, Hasher};
use std::io::{self, BufRead, Read, Write};
use std::num::NonZeroU64;

const MAX_LINE: usize = 4 * 1024 * 1024;
type Result<T> = std::result::Result<T, Error>;
#[derive(Debug)]
enum Error { Invalid(String), Model(String) }
impl From<ModelError> for Error {
    fn from(value: ModelError) -> Self { Self::Model(value.to_string()) }
}
fn invalid(message: &str) -> Error { Error::Invalid(message.into()) }

// serde_json::Value alone silently accepts duplicate keys. Reject them, floats,
// and non-scalar Unicode at the transport boundary instead of normalizing them.
struct Strict(Value);
impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Strict;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result { f.write_str("portable JSON") }
            fn visit_bool<E: de::Error>(self, v: bool) -> std::result::Result<Strict,E> { Ok(Strict(json!(v))) }
            fn visit_i64<E: de::Error>(self, v: i64) -> std::result::Result<Strict,E> { Ok(Strict(json!(v))) }
            fn visit_u64<E: de::Error>(self, v: u64) -> std::result::Result<Strict,E> { Ok(Strict(json!(v))) }
            fn visit_str<E: de::Error>(self, v: &str) -> std::result::Result<Strict,E> { Ok(Strict(json!(v))) }
            fn visit_string<E: de::Error>(self, v: String) -> std::result::Result<Strict,E> { Ok(Strict(json!(v))) }
            fn visit_unit<E: de::Error>(self) -> std::result::Result<Strict,E> { Ok(Strict(Value::Null)) }
            fn visit_seq<A: SeqAccess<'de>>(self, mut a: A) -> std::result::Result<Strict,A::Error> {
                let mut items = Vec::new();
                while let Some(Strict(v)) = a.next_element()? { items.push(v); }
                Ok(Strict(Value::Array(items)))
            }
            fn visit_map<A: MapAccess<'de>>(self, mut a: A) -> std::result::Result<Strict,A::Error> {
                let mut items = Map::new();
                while let Some((key, Strict(value))) = a.next_entry::<String,Strict>()? {
                    if items.insert(key, value).is_some() { return Err(de::Error::custom("duplicate key")); }
                }
                Ok(Strict(Value::Object(items)))
            }
        }
        d.deserialize_any(V)
    }
}
fn depth(v: &Value, n: usize) -> Result<()> {
    if n > 64 { return Err(invalid("JSON nesting limit")); }
    match v {
        Value::Array(a) => for x in a { depth(x,n+1)?; },
        Value::Object(o) => for x in o.values() { depth(x,n+1)?; },
        _ => (),
    }
    Ok(())
}
fn fields<'a>(v: &'a Value, required: &[&str], optional: &[&str]) -> Result<&'a Map<String,Value>> {
    let o = v.as_object().ok_or_else(|| invalid("expected object"))?;
    if required.iter().any(|k| !o.contains_key(*k)) || o.keys().any(|k| !required.contains(&k.as_str()) && !optional.contains(&k.as_str())) {
        return Err(invalid("unknown or missing object field"));
    }
    Ok(o)
}
fn text(v: &Value) -> Result<String> { v.as_str().map(str::to_owned).ok_or_else(|| invalid("expected string")) }
fn number(v: &Value, max: u64) -> Result<u64> { v.as_u64().filter(|n| *n <= max).ok_or_else(|| invalid("invalid integer")) }
fn boolean(v: &Value) -> Result<bool> { v.as_bool().ok_or_else(|| invalid("expected boolean")) }
fn list(v: &Value, max: usize) -> Result<&Vec<Value>> { v.as_array().filter(|a| a.len() <= max).ok_or_else(|| invalid("expected bounded array")) }
fn strings(v: &Value, max: usize) -> Result<Vec<String>> { list(v,max)?.iter().map(text).collect() }
fn flag(o: &Map<String,Value>, key: &str) -> Result<bool> { o.get(key).map(boolean).transpose().map(|x| x.unwrap_or(false)) }
fn decimal(v: &Value) -> Result<u64> {
    let s = text(v)?;
    if s.is_empty() || (s.len() > 1 && s.starts_with('0')) || !s.bytes().all(|b| b.is_ascii_digit()) { return Err(invalid("expected decimal u64")); }
    s.parse().map_err(|_| invalid("u64 overflow"))
}
fn checks(v: &Value) -> Result<Vec<Check>> {
    list(v,250_000)?.iter().map(|v| {
        let o = fields(v,&["id","status"],&["details"])?;
        let id = text(&o["id"])?;
        let details = o.get("details").map(text).transpose()?.unwrap_or_default();
        Ok(match text(&o["status"])?.as_str() {
            "passed" if details.is_empty() => Check::passed(id),
            "failed" => Check::failed(id,details),
            "skipped" => Check::skipped(id,details),
            _ => return Err(invalid("invalid check")),
        })
    }).collect()
}
fn disposition(v: &Value) -> Result<Disposition> {
    let o = fields(v,&["kind"],&["reason"])?;
    let reason = o.get("reason").map(text).transpose()?.unwrap_or_default();
    Ok(match text(&o["kind"])?.as_str() {
        "accepted" if reason.is_empty() => Disposition::Accepted,
        "rejected" => Disposition::Rejected(reason),
        "ignored" => Disposition::Ignored(reason),
        _ => return Err(invalid("invalid disposition")),
    })
}
fn metadata(v: &Value) -> Result<ModelMetadata> {
    let o = fields(v,&["name","model_version","properties_version","codec_version","build"],&[])?;
    Ok(ModelMetadata { name: text(&o["name"])?, build: text(&o["build"])?,
        model_version: number(&o["model_version"],u32::MAX as u64)? as u32,
        properties_version: number(&o["properties_version"],u32::MAX as u64)? as u32,
        codec_version: number(&o["codec_version"],u32::MAX as u64)? as u32 })
}
fn check_json(c: &Check) -> Value {
    let (status,details) = match &c.status { CheckStatus::Passed => ("passed",""), CheckStatus::Failed(s) => ("failed",s.as_str()), CheckStatus::Skipped(s) => ("skipped",s.as_str()) };
    json!({"id":c.id.as_str(),"status":status,"details":details})
}
fn checks_json(c: &[Check]) -> Value { Value::Array(c.iter().map(check_json).collect()) }
fn disposition_json(d: &Disposition) -> Value {
    let (kind,reason) = match d { Disposition::Accepted => ("accepted",""), Disposition::Rejected(s) => ("rejected",s.as_str()), Disposition::Ignored(s) => ("ignored",s.as_str()) };
    json!({"kind":kind,"reason":reason})
}
fn hex(bytes: &[u8]) -> String { bytes.iter().map(|b| format!("{b:02x}")).collect() }
fn unhex(v: &Value) -> Result<Vec<u8>> {
    let s = text(v)?;
    if s.len() > 8*1024*1024 || !s.len().is_multiple_of(2) || !s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) { return Err(invalid("invalid hexadecimal bytes")); }
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i+2],16).map_err(|_| invalid("invalid hex"))).collect()
}
fn trace_json(t: &Trace) -> Value {
    let (termination,error) = match &t.termination { Termination::Completed => ("completed",""), Termination::PropertyFailed => ("property_failed",""), Termination::StepLimit => ("step_limit",""), Termination::Interrupted => ("interrupted",""), Termination::ModelError(s) => ("model_error",s.as_str()) };
    let m = &t.metadata;
    json!({"format":"stateless.trace-json","version":1,
        "metadata":{"name":m.name,"model_version":m.model_version,"properties_version":m.properties_version,"codec_version":m.codec_version,"build":m.build},
        "initial_state":hex(&t.initial_state),"initial_checks":checks_json(&t.initial_checks),
        "steps":t.steps.iter().map(|s| json!({"input":hex(&s.input),"disposition":disposition_json(&s.disposition),"outputs":s.outputs.iter().map(|o| hex(o)).collect::<Vec<_>>(),"post_state":hex(&s.post_state),"checks":checks_json(&s.checks)})).collect::<Vec<_>>(),
        "termination":termination,"error":error})
}
fn parse_trace(v: &Value) -> Result<Trace> {
    let o = fields(v,&["format","version","metadata","initial_state","initial_checks","steps","termination","error"],&[])?;
    if o["format"] != "stateless.trace-json" || number(&o["version"],u64::MAX)? != 1 { return Err(invalid("unsupported trace format")); }
    let initial_checks = checks(&o["initial_checks"])?;
    let mut items = initial_checks.len();
    let mut steps = Vec::new();
    for value in list(&o["steps"],100_000)? {
        let s = fields(value,&["input","disposition","outputs","post_state","checks"],&[])?;
        let outputs: Vec<_> = list(&s["outputs"],250_000)?.iter().map(unhex).collect::<Result<_>>()?;
        let checks = checks(&s["checks"])?;
        items += 1 + outputs.len() + checks.len();
        if items > 250_000 { return Err(invalid("aggregate item limit")); }
        steps.push(TraceStep { input:unhex(&s["input"])?, disposition:disposition(&s["disposition"])?, outputs, post_state:unhex(&s["post_state"])?, checks });
    }
    let error = text(&o["error"])?;
    let kind = text(&o["termination"])?;
    if !error.is_empty() && kind != "model_error" { return Err(invalid("unexpected terminal error")); }
    let termination = match kind.as_str() { "completed" => Termination::Completed, "property_failed" => Termination::PropertyFailed, "step_limit" => Termination::StepLimit, "interrupted" => Termination::Interrupted, "model_error" => Termination::ModelError(error), _ => return Err(invalid("invalid termination")) };
    Ok(Trace { metadata:metadata(&o["metadata"])?, config:RunConfig::default(), initial_state:unhex(&o["initial_state"])?, initial_checks, steps, termination })
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct State(String);
impl Hash for State { fn hash<H: Hasher>(&self, state: &mut H) { 0u8.hash(state); } }
#[derive(Clone, Debug, PartialEq, Eq)]
struct Edge { input:String, to:String, outputs:Vec<String>, disposition:Disposition, checks:Vec<Check>, step_error:bool, check_error:bool }
struct Row { checks:Vec<Check>, edges:Vec<Edge>, check_error:bool, inputs_error:bool }
struct Table { metadata:ModelMetadata, initial:String, rows:BTreeMap<String,Row>, initial_error:bool, calls:Cell<usize> }
impl Table {
    fn parse(v: &Value) -> Result<Self> {
        let o = fields(v,&["id","initial","states"],&["metadata","initial_error"])?;
        let id = text(&o["id"])?;
        let metadata = if let Some(v) = o.get("metadata") { metadata(v)? } else { ModelMetadata {name:id,model_version:1,properties_version:1,codec_version:1,build:"fixture-v1".into()} };
        let initial = text(&o["initial"])?;
        let mut rows = BTreeMap::new();
        let empty = json!([]);
        let accepted = json!({"kind":"accepted"});
        let mut edge_count = 0;
        for value in list(&o["states"],1_000)? {
            let row = fields(value,&["id"],&["checks","edges","check_error","inputs_error"])?;
            let id = text(&row["id"])?;
            let mut edges:Vec<Edge> = Vec::new();
            let row_checks = checks(row.get("checks").unwrap_or(&empty))?;
            if row_checks.len() > 4096 { return Err(invalid("check limit")); }
            for value in list(row.get("edges").unwrap_or(&empty),10_000)? {
                let e = fields(value,&["input","to"],&["outputs","disposition","checks","step_error","check_error"])?;
                let edge = Edge { input:text(&e["input"])?, to:text(&e["to"])?, outputs:strings(e.get("outputs").unwrap_or(&empty),4096)?, disposition:disposition(e.get("disposition").unwrap_or(&accepted))?, checks:checks(e.get("checks").unwrap_or(&empty))?, step_error:flag(e,"step_error")?, check_error:flag(e,"check_error")? };
                if edge.checks.len() > 4096 || edges.iter().any(|old| old.input == edge.input && *old != edge) { return Err(invalid("conflicting transition or check limit")); }
                edges.push(edge);
                edge_count += 1;
            }
            if rows.insert(id,Row { checks:row_checks,edges,check_error:flag(row,"check_error")?,inputs_error:flag(row,"inputs_error")? }).is_some() { return Err(invalid("duplicate state")); }
        }
        if !rows.contains_key(&initial) || edge_count > 10_000 || rows.values().any(|r| r.edges.iter().any(|e| !rows.contains_key(&e.to))) { return Err(invalid("invalid initial/target state or edge limit")); }
        Ok(Self {metadata,initial,rows,initial_error:flag(o,"initial_error")?,calls:Cell::new(0)})
    }
    fn row(&self,s:&State) -> std::result::Result<&Row,ModelError> { self.rows.get(&s.0).ok_or_else(|| ModelError::new("unknown state")) }
    fn edge(&self,s:&State,i:&str) -> std::result::Result<&Edge,ModelError> { self.row(s)?.edges.iter().find(|e| e.input == i).ok_or_else(|| ModelError::new("input is not in this state's domain")) }
}
impl Model for Table {
    type State = State;
    type Input = String;
    type Output = String;
    fn metadata(&self) -> ModelMetadata { self.metadata.clone() }
    fn initial_state(&self) -> std::result::Result<State,ModelError> { if self.initial_error { Err(ModelError::new("injected initial_state")) } else { Ok(State(self.initial.clone())) } }
    fn step(&self,s:&State,i:&String) -> std::result::Result<Transition<State,String>,ModelError> {
        self.calls.set(self.calls.get()+1);
        let e = self.edge(s,i)?;
        if e.step_error { return Err(ModelError::new("injected step")); }
        Ok(Transition {state:State(e.to.clone()),outputs:e.outputs.clone(),disposition:e.disposition.clone()})
    }
    fn check_state(&self,s:&State) -> std::result::Result<Vec<Check>,ModelError> { let r=self.row(s)?; if r.check_error { Err(ModelError::new("injected check_state")) } else { Ok(r.checks.clone()) } }
    fn check_transition(&self,b:&State,i:&String,_t:&TransitionRef<'_,State,String>) -> std::result::Result<Vec<Check>,ModelError> { let e=self.edge(b,i)?; if e.check_error { Err(ModelError::new("injected check_transition")) } else { Ok(e.checks.clone()) } }
}
impl Enumerate for Table {
    fn inputs(&self,s:&State) -> std::result::Result<Vec<String>,ModelError> { let r=self.row(s)?; if r.inputs_error { Err(ModelError::new("injected inputs")) } else { Ok(r.edges.iter().map(|e| e.input.clone()).collect()) } }
}
impl ModelCodec for Table {
    fn encode_state(&self,s:&State) -> std::result::Result<Vec<u8>,ModelError> { self.row(s)?; Ok(s.0.as_bytes().to_vec()) }
    fn decode_state(&self,b:&[u8]) -> std::result::Result<State,ModelError> { let s=State(String::from_utf8(b.to_vec()).map_err(|_| ModelError::new("invalid UTF-8"))?); self.row(&s)?; Ok(s) }
    fn encode_input(&self,i:&String) -> std::result::Result<Vec<u8>,ModelError> { Ok(i.as_bytes().to_vec()) }
    fn decode_input(&self,b:&[u8]) -> std::result::Result<String,ModelError> { String::from_utf8(b.to_vec()).map_err(|_| ModelError::new("invalid UTF-8")) }
    fn encode_output(&self,o:&String) -> std::result::Result<Vec<u8>,ModelError> { Ok(o.as_bytes().to_vec()) }
}
fn execute(v:&Value) -> Result<Value> {
    let obj=v.as_object().ok_or_else(|| invalid("expected request"))?;
    let version=number(obj.get("version").ok_or_else(|| invalid("missing version"))?,(1u64<<53)-1)?;
    if version != 1 { return Ok(json!({"error":"unsupported_version"})); }
    let op=text(obj.get("operation").ok_or_else(|| invalid("missing operation"))?)?;
    if op == "hello" {
        fields(v,&["version","operation"],&[])?;
        return Ok(json!({"implementation":"rust","package_version":env!("CARGO_PKG_VERSION"),"spec_version":"1.0.0","profiles":["core-v1","bfs-v1","rng-splitmix64-v1","trace-json-v1"],"protocol_version":1}));
    }
    if op == "rng" {
        let o=fields(v,&["version","operation","seed","draws","bounds"],&[])?;
        let mut rng=Rng::new(decimal(&o["seed"])?);
        let draws=number(&o["draws"],10_000)?;
        let bounds:Vec<u64>=list(&o["bounds"],10_000)?.iter().map(decimal).collect::<Result<_>>()?;
        let raw:Vec<_>=(0..draws).map(|_| rng.next_u64().to_string()).collect();
        let mut indices=Vec::new();
        for upper in bounds { let upper=usize::try_from(upper).map_err(|_| invalid("bound exceeds target usize"))?; indices.push(rng.index(upper).map(|v| v.to_string())); }
        return Ok(json!({"raw":raw,"indices":indices,"next":rng.next_u64().to_string()}));
    }
    let required: &[&str] = match op.as_str() {
        "enumerate" => &["version","operation","model","config"],
        "record" => &["version","operation","model","inputs","max_steps"],
        "observe" => &["version","operation","model","before","input","transition","sequence","policy"],
        "replay" => &["version","operation","model","trace","allow_build_mismatch"],
        _ => return Ok(json!({"error":"unsupported_operation"})),
    };
    let o=fields(v,required,&[])?;
    let model=Table::parse(&o["model"])?;
    match op.as_str() {
        "enumerate" => {
            let c=fields(&o["config"],&["max_states","max_transitions","max_depth"],&[])?;
            let max_states=number(&c["max_states"],100_000)? as usize;
            let max_transitions=number(&c["max_transitions"],100_000)?;
            let max_depth=number(&c["max_depth"],100_000)? as usize;
            if max_states == 0 { return Ok(json!({"error":"invalid_config"})); }
            let r=explore::enumerate(&model,SearchConfig {max_states,max_transitions,max_depth})?;
            let termination=match r.termination { SearchTermination::GraphExhausted=>"graph_exhausted",SearchTermination::DepthBound=>"depth_bound",SearchTermination::StateLimit=>"state_limit",SearchTermination::TransitionLimit=>"transition_limit",SearchTermination::FailureFound=>"failure_found",_=>return Err(invalid("unexpected termination outside BFS profile")) };
            let failure=r.failure.map(|f| json!({"inputs":f.inputs,"violations":f.violations.iter().map(|v| json!({"phase":match v.phase {CheckPhase::InitialState=>"initial_state",CheckPhase::State=>"state",CheckPhase::Transition=>"transition"},"check":check_json(&v.check)})).collect::<Vec<_>>()}));
            Ok(json!({"termination":termination,"states":r.states,"transitions":r.transitions,"max_depth_reached":r.max_depth_reached,"skipped_checks":r.skipped_checks,"failure":failure}))
        }
        "record" => {
            let inputs=strings(&o["inputs"],100_000)?;
            let max=number(&o["max_steps"],100_000)? as usize;
            Ok(json!({"trace":trace_json(&execution::record(&model,inputs,RunConfig::default(),max)?)}))
        }
        "observe" => {
            let b=State(text(&o["before"])?); let input=text(&o["input"])?;
            let t=fields(&o["transition"],&["state","outputs","disposition"],&[])?;
            let transition=Transition {state:State(text(&t["state"])?),outputs:strings(&t["outputs"],4096)?,disposition:disposition(&t["disposition"])?};
            let p=fields(&o["policy"],&["state_every","transition_checks"],&[])?;
            let seq=number(&o["sequence"],100_000)?;
            let every=number(&p["state_every"],100_000)?;
            let enabled=boolean(&p["transition_checks"])?;
            let Some(every)=NonZeroU64::new(every) else {return Ok(json!({"error":"invalid_config"}));};
            if seq==0 {return Ok(json!({"error":"invalid_config"}));}
            let checks=execution::check_observed(&model,&b,&input,&transition,seq,CheckPolicy {state_every:every,transition_checks:enabled})?;
            Ok(json!({"checks":checks_json(&checks),"step_calls":model.calls.get()}))
        }
        "replay" => {
            let trace=parse_trace(&o["trace"])?;
            let r=execution::replay(&model,&trace,ReplayOptions {allow_build_mismatch:boolean(&o["allow_build_mismatch"])?})?;
            let (outcome,step,field)=match r.outcome {ReplayOutcome::Exact=>("exact",None,None),ReplayOutcome::Incompatible {..}=>("incompatible",None,None),ReplayOutcome::Diverged {step,field}=>("diverged",step,Some(field))};
            Ok(json!({"outcome":outcome,"steps_verified":r.steps_verified,"failure_reproduced":r.failure_reproduced,"build_matches":r.build_matches,"step":step,"field":field}))
        }
        _ => unreachable!(),
    }
}
fn response(raw:&[u8]) -> Value {
    let parsed=(|| {
        let s=std::str::from_utf8(raw).map_err(|_| invalid("invalid UTF-8"))?;
        let Strict(v)=serde_json::from_str::<Strict>(s).map_err(|e| Error::Invalid(e.to_string()))?;
        depth(&v,0)?;
        execute(&v)
    })();
    match parsed {Ok(v)=>v,Err(Error::Invalid(s))=>{eprintln!("{s}");json!({"error":"invalid_request"})},Err(Error::Model(s))=>{eprintln!("{s}");json!({"error":"model_error"})}}
}
fn main() -> io::Result<()> {
    let stdin=io::stdin(); let mut input=stdin.lock(); let stdout=io::stdout(); let mut output=stdout.lock();
    loop {
        let mut raw=Vec::new();
        (&mut input).take((MAX_LINE+1) as u64).read_until(b'\n',&mut raw)?;
        if raw.is_empty() { break; }
        let result=if raw.len()>MAX_LINE {
            while !raw.ends_with(b"\n") { raw.clear(); (&mut input).take((MAX_LINE+1) as u64).read_until(b'\n',&mut raw)?; if raw.is_empty() {break;} }
            json!({"error":"invalid_request"})
        } else { response(&raw) };
        serde_json::to_writer(&mut output,&result)?;
        output.write_all(b"\n")?; output.flush()?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn malformed_transport() { for raw in [r#"{"version":1,"version":1}"#,r#"{"version":1.0}"#,r#"{"version":true}"#,r#"{"x":"\ud800"}"#] { assert_eq!(response(raw.as_bytes()),json!({"error":"invalid_request"})); } }
    #[test] fn hello() { assert_eq!(response(br#"{"version":1,"operation":"hello"}"#)["implementation"],"rust"); }
    #[test] fn empty_graph() { assert_eq!(response(br#"{"version":1,"operation":"enumerate","model":{"id":"empty","initial":"s","states":[{"id":"s"}]},"config":{"max_states":1,"max_transitions":0,"max_depth":0}}"#)["termination"],"graph_exhausted"); }
}

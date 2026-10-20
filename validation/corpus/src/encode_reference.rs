//! Public Rust EncodeBuffer/codec dispatch reference for independently authored vectors.
use serde_json::{Value, json};
use stateless::{
    automatic::Auto,
    conformance::{Paired, Projection},
    model::*,
    oracle::{Oracle, OracleCodec, OracleState, WithOracle},
};
use std::{cell::RefCell, path::Path, rc::Rc};
type Events = Rc<RefCell<Vec<String>>>;
type R<T> = Result<T, ModelError>;
fn metadata() -> ModelMetadata {
    ModelMetadata {
        name: "encode-reference".into(),
        model_version: 1,
        properties_version: 1,
        codec_version: 1,
        build: "candidate".into(),
    }
}
fn decode(b: &[u8]) -> R<i32> {
    let s = std::str::from_utf8(b).map_err(|e| ModelError::new(e.to_string()))?;
    let n: i32 = s.parse().map_err(|_| ModelError::new("integer"))?;
    if n.to_string() != s {
        return Err(ModelError::new("noncanonical integer"));
    }
    Ok(n)
}
struct Legacy(Events);
struct Into(Legacy);
macro_rules! model {
    ($t:ty) => {
        impl Model for $t {
            type State = i32;
            type Input = i32;
            type Output = i32;
            fn metadata(&self) -> ModelMetadata {
                metadata()
            }
            fn initial_state(&self) -> R<i32> {
                Ok(12)
            }
            fn step(&self, s: &i32, _: &i32) -> R<Transition<i32, i32>> {
                Ok(Transition::accepted(*s, vec![]))
            }
            fn check_state(&self, _: &i32) -> R<Vec<Check>> {
                Ok(vec![])
            }
        }
    };
}
model!(Legacy);
model!(Into);
impl Legacy {
    fn encode(&self, k: &str, n: &i32) -> R<Vec<u8>> {
        self.0.borrow_mut().push(format!("legacy_{k}"));
        Ok(n.to_string().into_bytes())
    }
}
impl Into {
    fn encode(&self, k: &str, n: &i32, out: &mut EncodeBuffer<'_>) -> R<()> {
        self.0.0.borrow_mut().push(format!("into_{k}"));
        for b in n.to_string().as_bytes() {
            out.extend_from_slice(&[*b])?;
        }
        Ok(())
    }
}
impl ModelCodec for Legacy {
    fn encode_state(&self, n: &i32) -> R<Vec<u8>> {
        self.encode("state", n)
    }
    fn encode_input(&self, n: &i32) -> R<Vec<u8>> {
        self.encode("input", n)
    }
    fn encode_output(&self, n: &i32) -> R<Vec<u8>> {
        self.encode("output", n)
    }
    fn decode_state(&self, b: &[u8]) -> R<i32> {
        decode(b)
    }
    fn decode_input(&self, b: &[u8]) -> R<i32> {
        decode(b)
    }
}
impl ModelCodec for Into {
    fn encode_state(&self, _: &i32) -> R<Vec<u8>> {
        Err(ModelError::new("legacy state called"))
    }
    fn encode_input(&self, _: &i32) -> R<Vec<u8>> {
        Err(ModelError::new("legacy input called"))
    }
    fn encode_output(&self, _: &i32) -> R<Vec<u8>> {
        Err(ModelError::new("legacy output called"))
    }
    fn decode_state(&self, b: &[u8]) -> R<i32> {
        decode(b)
    }
    fn decode_input(&self, b: &[u8]) -> R<i32> {
        decode(b)
    }
    fn encode_state_into(&self, n: &i32, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.encode("state", n, o)
    }
    fn encode_input_into(&self, n: &i32, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.encode("input", n, o)
    }
    fn encode_output_into(&self, n: &i32, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.encode("output", n, o)
    }
}
struct HistoryLegacy(Events);
struct HistoryInto(Events);
macro_rules! oracle {
    ($t:ty) => {
        impl<M: Model> Oracle<M> for $t {
            type State = i32;
            fn metadata(&self) -> ModelMetadata {
                metadata()
            }
            fn initial_state(&self) -> R<i32> {
                Ok(34)
            }
            fn advance(&self, s: &i32, _: &M::Input, _: &[M::Output], _: &Disposition) -> R<i32> {
                Ok(*s)
            }
            fn check_state_into(&self, _: &i32, _: &M::State, _: &mut CheckSink<'_>) -> R<()> {
                Ok(())
            }
        }
    };
}
oracle!(HistoryLegacy);
oracle!(HistoryInto);
impl<M: Model> OracleCodec<M> for HistoryLegacy {
    fn encode_history(&self, n: &i32) -> R<Vec<u8>> {
        self.0.borrow_mut().push("legacy_history".into());
        Ok(n.to_string().into_bytes())
    }
    fn decode_history(&self, b: &[u8]) -> R<i32> {
        decode(b)
    }
}
impl<M: Model> OracleCodec<M> for HistoryInto {
    fn encode_history(&self, _: &i32) -> R<Vec<u8>> {
        Err(ModelError::new("legacy history called"))
    }
    fn decode_history(&self, b: &[u8]) -> R<i32> {
        decode(b)
    }
    fn encode_history_into(&self, n: &i32, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.0.borrow_mut().push("into_history".into());
        for b in n.to_string().as_bytes() {
            o.extend_from_slice(&[*b])?;
        }
        Ok(())
    }
}
struct Identity;
impl<
    M: Model<State = i32, Input = i32, Output = i32>,
    N: Model<State = i32, Input = i32, Output = i32>,
> Projection<M, N> for Identity
{
    type State = i32;
    type Effect = i32;
    fn metadata(&self) -> ModelMetadata {
        metadata()
    }
    fn actual_state(&self, s: &i32) -> R<i32> {
        Ok(*s)
    }
    fn reference_state(&self, s: &i32) -> R<i32> {
        Ok(*s)
    }
    fn actual_effect(&self, s: &i32) -> R<i32> {
        Ok(*s)
    }
    fn reference_effect(&self, s: &i32) -> R<i32> {
        Ok(*s)
    }
}
fn field<'a>(v: &'a Value, k: &str) -> Result<&'a str, String> {
    v[k].as_str().ok_or_else(|| format!("missing {k}"))
}
fn unhex(s: &str) -> Result<Vec<u8>, String> {
    if s.len() % 2 != 0
        || !s
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err("invalid hex".into());
    }
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).map_err(|e| e.to_string()))
        .collect()
}
fn hex(b: &[u8]) -> String {
    b.iter().map(|b| format!("{b:02x}")).collect()
}
fn action(a: &Value, out: &mut EncodeBuffer<'_>) -> Result<Value, String> {
    let mut children = vec![];
    let result = match field(a, "kind")? {
        "append" => out.extend_from_slice(&unhex(field(a, "hex")?)?),
        "legacy" => out.extend_from_vec(unhex(field(a, "hex")?)?),
        "framed" => out.framed(|out| {
            for child in a["actions"]
                .as_array()
                .ok_or_else(|| ModelError::new("actions"))?
            {
                let r = action(child, out).map_err(ModelError::new)?;
                let ok = r["ok"] == true;
                children.push(r);
                if !ok && a["ignore_child_errors"] != true {
                    return Err(ModelError::new("child failed"));
                }
            }
            if a["callback_error"] == true {
                Err(ModelError::new("callback failed"))
            } else {
                Ok(())
            }
        }),
        k => return Err(format!("unknown action {k}")),
    };
    Ok(json!({"ok":result.is_ok(),"children":children}))
}
fn dispatch<M: ModelCodec<State = i32, Input = i32, Output = i32>>(
    m: &M,
    encoder: &str,
    out: &mut EncodeBuffer<'_>,
) -> R<()> {
    match encoder {
        "state" => m.encode_state_into(&12, out),
        "input" => m.encode_input_into(&12, out),
        "output" => m.encode_output_into(&12, out),
        _ => Err(ModelError::new("encoder")),
    }
}
fn wrapper<
    M: ModelCodec<State = i32, Input = i32, Output = i32>,
    H: OracleCodec<M, State = i32>,
>(
    m: M,
    h: H,
    out: &mut EncodeBuffer<'_>,
) -> R<()> {
    WithOracle::new(m, h).encode_state_into(
        &OracleState {
            model: 12,
            oracle: 34,
        },
        out,
    )
}
fn execute(v: &Value) -> Result<Value, String> {
    let maximum = field(v, "maximum")?
        .parse::<usize>()
        .map_err(|e| e.to_string())?;
    let mut bytes = unhex(v["initial_hex"].as_str().unwrap_or("ffff"))?;
    let events = Rc::new(RefCell::new(vec![]));
    let mut out = EncodeBuffer::new(&mut bytes, maximum);
    let mut results = vec![];
    let op = field(v, "operation")?;
    let legacy = || Legacy(events.clone());
    let into = || Into(legacy());
    let mut callback_ok = true;
    match op {
        "buffer" => {
            for a in v["actions"].as_array().ok_or("actions")? {
                results.push(action(a, &mut out)?);
            }
        }
        "dispatch" | "automatic" => {
            let encoder = field(v, "encoder")?;
            callback_ok = match (op, field(v, "mode")?) {
                ("dispatch", "legacy") => dispatch(&legacy(), encoder, &mut out),
                ("dispatch", "into") => dispatch(&into(), encoder, &mut out),
                ("automatic", "legacy") => dispatch(&Auto::new(legacy()), encoder, &mut out),
                ("automatic", "into") => dispatch(&Auto::new(into()), encoder, &mut out),
                _ => return Err("mode".into()),
            }
            .is_ok();
        }
        "oracle" => {
            callback_ok = match (field(v, "mode")?, field(v, "history_mode")?) {
                ("legacy", "legacy") => wrapper(legacy(), HistoryLegacy(events.clone()), &mut out),
                ("legacy", "into") => wrapper(legacy(), HistoryInto(events.clone()), &mut out),
                ("into", "legacy") => wrapper(into(), HistoryLegacy(events.clone()), &mut out),
                ("into", "into") => wrapper(into(), HistoryInto(events.clone()), &mut out),
                _ => return Err("mode".into()),
            }
            .is_ok();
        }
        "nested" => {
            let m = WithOracle::new(
                WithOracle::new(into(), HistoryInto(events.clone())),
                HistoryInto(events.clone()),
            );
            callback_ok = m
                .encode_state_into(
                    &OracleState {
                        model: OracleState {
                            model: 12,
                            oracle: 34,
                        },
                        oracle: 56,
                    },
                    &mut out,
                )
                .is_ok();
        }
        "paired" => {
            callback_ok = wrapper(
                into(),
                Paired {
                    reference: into(),
                    projection: Identity,
                },
                &mut out,
            )
            .is_ok();
        }
        _ => return Err(format!("operation {op}")),
    }
    let finish_ok = callback_ok && out.finish().is_ok();
    drop(out);
    let encoded = if finish_ok { Some(hex(&bytes)) } else { None };
    let e = events.borrow().clone();
    Ok(json!({"finish_ok":finish_ok,"encoded_hex":encoded,"results":results,"events":e}))
}
pub fn qualify(path: &Path) -> Result<Value, String> {
    let root: Value = serde_json::from_slice(&std::fs::read(path).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    if root["profile"] != "encode-buffers-v1" {
        return Err("profile".into());
    }
    let mut passed = 0;
    let mut skipped = 0;
    for v in root["cases"].as_array().ok_or("cases")? {
        if v["requires_64bit"] == true && usize::BITS < 64 {
            skipped += 1;
            continue;
        }
        let actual = execute(v).map_err(|e| format!("{}: {e}", v["case"]))?;
        if actual != v["expected"] {
            return Err(format!(
                "{}: expected {}, actual {}",
                v["case"], v["expected"], actual
            ));
        }
        passed += 1;
    }
    Ok(json!({"profile":"encode-buffers-v1","passed":passed,"skipped":skipped}))
}

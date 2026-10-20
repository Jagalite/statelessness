//! Real public Rust rich/bounded replay and nine-quota recorder qualification.
use serde_json::{Value, json};
use stateless::{
    execution::{
        self, ReplayActual, ReplayDifferences, ReplayObservation, ReplayOptions, ReplayOutcome,
        ReplayReport,
    },
    model::*,
    trace::{ReadLimits, RunConfig, Termination, Trace, TraceStep},
};
use std::{cell::RefCell, io::Read, path::Path, rc::Rc};
type Result<T> = std::result::Result<T, String>;
fn string<'a>(v: &'a Value, key: &str) -> Result<&'a str> {
    v.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("missing string {key}"))
}
fn number(v: &Value, key: &str) -> Result<u64> {
    v.get(key)
        .and_then(Value::as_u64)
        .ok_or_else(|| format!("missing unsigned integer {key}"))
}
fn array<'a>(v: &'a Value, key: &str) -> Result<&'a [Value]> {
    v.get(key)
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .ok_or_else(|| format!("missing array {key}"))
}
fn decode_hex(s: &str) -> Result<Vec<u8>> {
    if s.len() % 2 != 0 {
        return Err("odd hex length".into());
    }
    s.as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            let digit = |b: u8| match b {
                b'0'..=b'9' => Ok(b - b'0'),
                b'a'..=b'f' => Ok(b - b'a' + 10),
                _ => Err("noncanonical hex".to_owned()),
            };
            Ok(digit(pair[0])? * 16 + digit(pair[1])?)
        })
        .collect()
}
fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8] = b"0123456789abcdef";
    let mut result = String::with_capacity(bytes.len() * 2);
    for &byte in bytes {
        result.push(DIGITS[(byte >> 4) as usize] as char);
        result.push(DIGITS[(byte & 15) as usize] as char);
    }
    result
}
fn json_file(path: impl AsRef<Path>) -> Result<Value> {
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .map_err(|e| e.to_string())?
        .take(MAX_JSON + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_JSON {
        return Err("qualification JSON byte limit".into());
    }
    serde_json::from_slice(&bytes).map_err(|e| e.to_string())
}
fn no_reason(v: &Value) -> Result<()> {
    if !v.get("reason").is_some_and(Value::is_null) {
        return Err("unexpected reason on fieldless variant".into());
    }
    Ok(())
}
fn checks(v: &Value, key: &str) -> Result<Vec<Check>> {
    array(v, key)?
        .iter()
        .map(|c| {
            let id = string(c, "id")?.to_owned();
            let check = match string(c, "status")? {
                "passed" => {
                    no_reason(c)?;
                    Check::passed(id)
                }
                "failed" => Check::failed(id, string(c, "reason")?),
                "skipped" => Check::skipped(id, string(c, "reason")?),
                _ => return Err("unknown check status".into()),
            };
            Ok(check)
        })
        .collect()
}
fn from_json(v: &Value) -> Result<Trace> {
    let m = &v["metadata"];
    let c = &v["config"];
    let version = |key| -> Result<u32> {
        u32::try_from(number(m, key)?).map_err(|_| format!("metadata {key} exceeds u32"))
    };
    let seed = match c.get("seed") {
        Some(Value::Null) => None,
        Some(Value::String(s)) => {
            let n = s
                .parse::<u64>()
                .map_err(|_| "invalid seed decimal".to_owned())?;
            if n.to_string() != *s {
                return Err("noncanonical seed decimal".into());
            }
            Some(n)
        }
        _ => return Err("seed must be null or decimal string".into()),
    };
    let parameters = array(c, "parameters")?
        .iter()
        .map(|p| {
            let pair = p
                .as_array()
                .filter(|p| p.len() == 2)
                .ok_or_else(|| "invalid parameter pair".to_owned())?;
            Ok((
                pair[0]
                    .as_str()
                    .ok_or("parameter key must be string")?
                    .to_owned(),
                pair[1]
                    .as_str()
                    .ok_or("parameter value must be string")?
                    .to_owned(),
            ))
        })
        .collect::<Result<Vec<_>>>()?;
    let steps = array(v, "steps")?
        .iter()
        .map(|s| {
            let d = &s["disposition"];
            let disposition = match string(d, "kind")? {
                "accepted" => {
                    no_reason(d)?;
                    Disposition::Accepted
                }
                "rejected" => Disposition::Rejected(string(d, "reason")?.into()),
                "ignored" => Disposition::Ignored(string(d, "reason")?.into()),
                _ => return Err("unknown disposition".into()),
            };
            Ok(TraceStep {
                input: decode_hex(string(s, "input")?)?,
                disposition,
                outputs: array(s, "outputs")?
                    .iter()
                    .map(|b| decode_hex(b.as_str().ok_or("output hex must be string")?))
                    .collect::<Result<Vec<_>>>()?,
                post_state: decode_hex(string(s, "post_state")?)?,
                checks: checks(s, "checks")?,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let t = &v["termination"];
    let termination = match string(t, "kind")? {
        "completed" => {
            no_reason(t)?;
            Termination::Completed
        }
        "property_failed" => {
            no_reason(t)?;
            Termination::PropertyFailed
        }
        "step_limit" => {
            no_reason(t)?;
            Termination::StepLimit
        }
        "interrupted" => {
            no_reason(t)?;
            Termination::Interrupted
        }
        "model_error" => Termination::ModelError(string(t, "reason")?.into()),
        _ => return Err("unknown termination".into()),
    };
    Ok(Trace {
        metadata: ModelMetadata {
            name: string(m, "name")?.into(),
            model_version: version("model_version")?,
            properties_version: version("properties_version")?,
            codec_version: version("codec_version")?,
            build: string(m, "build")?.into(),
        },
        config: RunConfig {
            strategy: string(c, "strategy")?.into(),
            seed,
            parameters,
        },
        initial_state: decode_hex(string(v, "initial_state")?)?,
        initial_checks: checks(v, "initial_checks")?,
        steps,
        termination,
    })
}
fn limits(v: &Value) -> Result<ReadLimits> {
    let mut l = ReadLimits::default();
    if let Some(overrides) = v.get("limits") {
        for (key, value) in overrides.as_object().ok_or("limits must be object")? {
            let n = value.as_u64().ok_or("limit must be unsigned integer")?;
            if key == "max_total_bytes" {
                l.max_total_bytes = n;
                continue;
            }
            let n = usize::try_from(n).map_err(|_| "limit exceeds usize")?;
            match key.as_str() {
                "max_frame_bytes" => l.max_frame_bytes = n,
                "max_blob_bytes" => l.max_blob_bytes = n,
                "max_string_bytes" => l.max_string_bytes = n,
                "max_steps" => l.max_steps = n,
                "max_checks_per_step" => l.max_checks_per_step = n,
                "max_outputs_per_step" => l.max_outputs_per_step = n,
                "max_parameters" => l.max_parameters = n,
                "max_items" => l.max_items = n,
                _ => return Err(format!("unknown binary limit {key}")),
            }
        }
    }
    Ok(l)
}
const MAX_JSON: u64 = 4 * 1024 * 1024;
type R<T> = std::result::Result<T, ModelError>;
struct Fixture {
    config: Value,
    events: Rc<RefCell<Vec<String>>>,
}
impl Fixture {
    fn event(&self, e: String) -> R<()> {
        self.events.borrow_mut().push(e.clone());
        if self.config["error_at"].as_str() == Some(&e) {
            Err(ModelError::new("fixture codec replay error"))
        } else {
            Ok(())
        }
    }
    fn num(&self, k: &str, default: i32) -> i32 {
        self.config[k].as_i64().map(|n| n as i32).unwrap_or(default)
    }
    fn check(&self, k: &str, default: &str) -> R<Check> {
        if self.config[k].is_null() {
            Ok(Check::passed(default.to_owned()))
        } else {
            parse_check(&self.config[k]).map_err(ModelError::new)
        }
    }
    fn encode(&self, k: &str, n: &i32, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.event(format!("encode_{k}:{n}"))?;
        o.extend_from_slice(n.to_string().as_bytes())?;
        if k == "output" {
            for _ in 0..self.num("output_padding", 0) {
                o.extend_from_slice(b"x")?;
            }
        }
        Ok(())
    }
    fn decode(&self, k: &str, b: &[u8]) -> R<i32> {
        self.event(format!("decode_{k}:{}", hex(b)))?;
        std::str::from_utf8(b)
            .map_err(|e| ModelError::new(e.to_string()))?
            .parse()
            .map_err(|_| ModelError::new("fixture integer"))
    }
}
fn parse_check(v: &Value) -> Result<Check> {
    let id = string(v, "id")?.to_owned();
    Ok(match string(v, "status")? {
        "passed" => Check::passed(id),
        "failed" => Check::failed(id, string(v, "reason")?),
        "skipped" => Check::skipped(id, string(v, "reason")?),
        _ => return Err("check status".into()),
    })
}
impl Model for Fixture {
    type State = i32;
    type Input = i32;
    type Output = i32;
    fn metadata(&self) -> ModelMetadata {
        self.events.borrow_mut().push("metadata".into());
        ModelMetadata {
            name: self.config["name"]
                .as_str()
                .unwrap_or("codec-fixture")
                .into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: self.config["build"]
                .as_str()
                .unwrap_or("candidate-v1")
                .into(),
        }
    }
    fn initial_state(&self) -> R<i32> {
        Ok(0)
    }
    fn step(&self, b: &i32, i: &i32) -> R<Transition<i32, i32>> {
        self.event(format!("step:{b}:{i}"))?;
        let base = b + i;
        let reason = self.config["disposition_reason"]
            .as_str()
            .unwrap_or("why")
            .to_owned();
        let disposition = match self.config["disposition"].as_str().unwrap_or("accepted") {
            "accepted" => Disposition::Accepted,
            "rejected" => Disposition::Rejected(reason),
            "ignored" => Disposition::Ignored(reason),
            _ => return Err(ModelError::new("disposition")),
        };
        Ok(Transition {
            state: base + self.num("state_delta", 0),
            outputs: vec![base + self.num("output_delta", 0); self.num("output_count", 1) as usize],
            disposition,
        })
    }
    fn check_state(&self, _: &i32) -> R<Vec<Check>> {
        Err(ModelError::new("legacy checker called"))
    }
    fn check_state_into(&self, s: &i32, c: &mut CheckSink<'_>) -> R<()> {
        self.event(format!("check_state:{s}"))?;
        c.push(self.check(
            if *s == 0 {
                "initial_check"
            } else {
                "turn_state_check"
            },
            "ok",
        )?);
        Ok(())
    }
    fn check_transition(&self, _: &i32, _: &i32, _: &TransitionRef<'_, i32, i32>) -> R<Vec<Check>> {
        Err(ModelError::new("legacy transition checker called"))
    }
    fn check_transition_into(
        &self,
        b: &i32,
        i: &i32,
        t: &TransitionRef<'_, i32, i32>,
        c: &mut CheckSink<'_>,
    ) -> R<()> {
        self.event(format!("check_transition:{b}:{i}:{}", t.state))?;
        c.push(self.check("turn_transition_check", "edge")?);
        Ok(())
    }
}
impl ModelCodec for Fixture {
    fn encode_state(&self, _: &i32) -> R<Vec<u8>> {
        Err(ModelError::new("legacy state codec called"))
    }
    fn encode_input(&self, _: &i32) -> R<Vec<u8>> {
        Err(ModelError::new("legacy input codec called"))
    }
    fn encode_output(&self, _: &i32) -> R<Vec<u8>> {
        Err(ModelError::new("legacy output codec called"))
    }
    fn decode_state(&self, b: &[u8]) -> R<i32> {
        self.decode("state", b)
    }
    fn decode_input(&self, b: &[u8]) -> R<i32> {
        self.decode("input", b)
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
fn checks_value(c: &[Check]) -> Value {
    json!(
        c.iter()
            .map(|c| {
                let (status, details) = match &c.status {
                    CheckStatus::Passed => ("passed", ""),
                    CheckStatus::Failed(s) => ("failed", s.as_str()),
                    CheckStatus::Skipped(s) => ("skipped", s.as_str()),
                };
                json!({"id":c.id.as_str(),"status":status,"details":details})
            })
            .collect::<Vec<_>>()
    )
}
fn disposition_value(d: &Disposition) -> Value {
    let (kind, reason) = match d {
        Disposition::Accepted => ("accepted", ""),
        Disposition::Rejected(s) => ("rejected", s.as_str()),
        Disposition::Ignored(s) => ("ignored", s.as_str()),
    };
    json!({"kind":kind,"reason":reason})
}
fn transition_value(s: &i32, o: &[i32], d: &Disposition) -> Value {
    json!({"state":s,"outputs":o,"disposition":disposition_value(d)})
}
fn report_value(r: &ReplayReport) -> Value {
    let (outcome, step, field) = match &r.outcome {
        ReplayOutcome::Exact => ("exact", None, None),
        ReplayOutcome::Diverged { step, field } => ("diverged", *step, Some(*field)),
        ReplayOutcome::Incompatible { .. } => ("incompatible", None, None),
    };
    json!({"outcome":outcome,"divergence_step":step,"divergence_field":field,"steps_verified":r.steps_verified,"failure_reproduced":r.failure_reproduced,"build_matches":r.build_matches})
}
fn actual_value(a: &ReplayActual<Fixture>) -> Value {
    match a {
        ReplayActual::Initial { state, checks } => {
            json!({"kind":"initial","sequence":0,"state":state,"checks":checks.as_ref().map(|c|checks_value(c))})
        }
        ReplayActual::Turn {
            sequence,
            before,
            input,
            transition,
            checks,
            state_check_count,
        } => {
            json!({"kind":"turn","sequence":sequence,"before":before,"input":input,"transition":transition_value(&transition.state,&transition.outputs,&transition.disposition),"checks":checks.as_ref().map(|c|checks_value(c)),"state_check_count":state_check_count})
        }
    }
}
fn differences_value(d: ReplayDifferences) -> Value {
    json!({"disposition":d.disposition,"outputs":d.outputs,"state":d.state,"checks":d.checks})
}
fn step_value(s: &TraceStep) -> Value {
    json!({"input":hex(&s.input),"outputs":s.outputs.iter().map(|b|hex(b)).collect::<Vec<_>>(),"post_state":hex(&s.post_state),"disposition":disposition_value(&s.disposition),"checks":checks_value(&s.checks)})
}
fn replay_case(v: &Value) -> Result<Value> {
    let trace = from_json(&v["trace"])?;
    let events = Rc::new(RefCell::new(vec![]));
    let model = Fixture {
        config: v["model"].clone(),
        events: events.clone(),
    };
    let mut observations = vec![];
    let options = ReplayOptions {
        allow_build_mismatch: v["allow_build_mismatch"] == true,
    };
    let mut observer = |o: ReplayObservation<'_, Fixture>, r: &ReplayReport| -> R<()> {
        let (key, event, value) = match o {
            ReplayObservation::Initial(i) => (
                "initial".to_owned(),
                "observe_initial".to_owned(),
                json!({"kind":"initial","sequence":0,"state":i.state,"checks":checks_value(i.checks),"expected_state":hex(i.expected_state),"actual_state":hex(i.actual_state),"expected_checks":checks_value(i.expected_checks),"differences":differences_value(i.differences),"report":report_value(r)}),
            ),
            ReplayObservation::Turn(t) => {
                let sequence = t.actual.sequence;
                let key = format!("turn:{sequence}");
                (
                    key.clone(),
                    format!("observe_{key}"),
                    json!({"kind":"turn","sequence":sequence,"before":t.actual.before,"input":t.actual.input,"transition":transition_value(t.actual.transition.state,t.actual.transition.outputs,t.actual.transition.disposition),"checks":checks_value(t.actual.checks),"state_check_count":t.actual.state_check_count,"expected":step_value(t.expected),"actual_input":hex(t.actual_input),"actual_outputs":t.actual_outputs.iter().map(|b|hex(b)).collect::<Vec<_>>(),"actual_state":hex(t.actual_state),"differences":differences_value(t.differences),"report":report_value(r)}),
                )
            }
            ReplayObservation::Error {
                sequence, actual, ..
            } => (
                "error".to_owned(),
                format!("observe_error:{sequence}"),
                json!({"kind":"error","sequence":sequence,"actual":actual.map(actual_value),"report":report_value(r)}),
            ),
        };
        events.borrow_mut().push(event);
        observations.push(value);
        if v["observer_error_at"].as_str() == Some(&key) {
            Err(ModelError::new("fixture observer error"))
        } else {
            Ok(())
        }
    };
    let result = if v["bounded"] == true {
        execution::replay_with_observations_bounded(
            &model,
            &trace,
            options,
            &limits(v)?,
            &mut observer,
        )
    } else {
        execution::replay_with_observations(&model, &trace, options, &mut observer)
    };
    let normalized = match result {
        Ok(r) => {
            json!({"report":report_value(&r),"observations":observations,"error":false,"actual":null,"observer_error":false})
        }
        Err(e) => {
            if let Some(needle) = v["error_contains"].as_str() {
                if !e.error.to_string().contains(needle) {
                    return Err(format!(
                        "primary error {:?} lacks {needle}",
                        e.error.to_string()
                    ));
                }
            }
            json!({"report":report_value(&e.report),"observations":observations,"error":true,"actual":e.actual.as_deref().map(actual_value),"observer_error":e.observer_error.is_some()})
        }
    };
    if normalized != v["expected"] {
        return Err(format!(
            "normalized mismatch: expected {}, actual {}",
            v["expected"], normalized
        ));
    }
    if *events.borrow()
        != serde_json::from_value::<Vec<String>>(v["events"].clone()).map_err(|e| e.to_string())?
    {
        return Err(format!(
            "events mismatch: expected {}, actual {:?}",
            v["events"],
            events.borrow()
        ));
    }
    Ok(normalized)
}
// Independent format arithmetic, not an engine-produced golden. All strings are UTF-8 bytes.
fn core_version() -> Result<&'static str> {
    include_str!("../../../Cargo.toml")
        .lines()
        .find_map(|l| {
            l.strip_prefix("version = \"")
                .and_then(|s| s.strip_suffix('"'))
        })
        .ok_or("core package version".into())
}
fn expected_stamped(config: &RunConfig) -> Result<RunConfig> {
    let mut c = config.clone();
    c.parameters
        .push(("stateless.engine.version".into(), core_version()?.into()));
    c.parameters.push((
        "stateless.engine.build".into(),
        stateless::modeling::RUNTIME_BUILD_ID.into(),
    ));
    Ok(c)
}
fn reserved_budget(config: &RunConfig) -> Result<u64> {
    let c = expected_stamped(config)?;
    let str_size = |s: &str| 4 + s.len() as u64;
    let metadata = str_size("codec-fixture") + 12 + str_size("candidate-v1");
    let config_size = str_size(&c.strategy)
        + 1
        + if c.seed.is_some() { 8 } else { 0 }
        + 4
        + c.parameters
            .iter()
            .map(|(k, v)| str_size(k) + str_size(v))
            .sum::<u64>();
    let initial_blob = 4 + 1;
    let initial_checks = 4 + str_size("ok") + 1;
    let run = 9 + metadata + config_size + initial_blob + initial_checks;
    Ok(12 + run + 2 * 57 + 278)
}
fn config_from_value(v: &Value) -> Result<RunConfig> {
    let template = json!({"metadata":{"name":"codec-fixture","model_version":1,"properties_version":1,"codec_version":1,"build":"candidate-v1"},"config":v,"initial_state":"30","initial_checks":[],"steps":[],"termination":{"kind":"completed","reason":null}});
    Ok(from_json(&template)?.config)
}
fn record_case(v: &Value) -> Result<()> {
    let events = Rc::new(RefCell::new(vec![]));
    let model = Fixture {
        config: json!({"error_at":v["error_at"]}),
        events,
    };
    let config = config_from_value(&v["config"])?;
    let mut lim = limits(v)?;
    if let Some(formula) = v["budget_formula"].as_str() {
        lim.max_total_bytes = reserved_budget(&config)?;
        if formula.ends_with("-1") {
            lim.max_total_bytes -= 1;
        } else if formula != "header+stamped_run+two_steps+reserved_error_footer" {
            return Err("unknown budget formula".into());
        }
    }
    let inputs = array(v, "inputs")?
        .iter()
        .map(|i| {
            i.as_i64()
                .and_then(|n| i32::try_from(n).ok())
                .ok_or("input integer".to_owned())
        })
        .collect::<Result<Vec<_>>>()?;
    let result = execution::record_with_limits(&model, inputs, config.clone(), usize::MAX, &lim);
    let summary = match result {
        Err(_) => json!({"error":true,"termination":null,"steps":null}),
        Ok(t) => {
            if t.config != expected_stamped(&config)? {
                return Err("record producer config stamp or order differs".into());
            }
            let kind = match &t.termination {
                Termination::Completed => "completed",
                Termination::PropertyFailed => "property_failed",
                Termination::StepLimit => "step_limit",
                Termination::Interrupted => "interrupted",
                Termination::ModelError(reason) => {
                    if reason.is_empty() {
                        return Err("empty model error".into());
                    }
                    "model_error"
                }
            };
            let mut wire = vec![];
            t.write_with_limits(&mut wire, &lim)
                .map_err(|e| format!("recorded prefix violates limits: {e}"))?;
            let decoded = Trace::read_from(wire.as_slice(), &lim)
                .map_err(|e| format!("recorded prefix read: {e}"))?;
            if decoded != t {
                return Err("recorded prefix binary roundtrip differs".into());
            }
            json!({"error":false,"termination":kind,"steps":t.steps.len()})
        }
    };
    if summary != v["expected"] {
        return Err(format!(
            "record summary expected {}, actual {summary}",
            v["expected"]
        ));
    }
    Ok(())
}
pub fn qualify(path: &Path) -> Result<Value> {
    let root = json_file(path)?;
    if root["profile"] != "replay-evidence-v1" {
        return Err("profile".into());
    }
    let mut replay_count = 0;
    let mut record_count = 0;
    for v in array(&root, "cases")? {
        replay_case(v).map_err(|e| format!("{}: {e}", v["case"]))?;
        replay_count += 1;
    }
    for v in array(&root, "record_cases")? {
        record_case(v).map_err(|e| format!("{}: {e}", v["case"]))?;
        record_count += 1;
    }
    Ok(
        json!({"profile":"replay-evidence-v1","replay_cases":replay_count,"record_cases":record_count}),
    )
}

//! Public Rust checked-result and runtime-recorder qualification.
use serde_json::{Value, json};
use stateless::execution::{
    ReplayDifferences, ReplayObservation, ReplayOptions, ReplayOutcome, ReplayReport,
};
use stateless::{
    execution::{self, CheckPolicy},
    model::*,
    monitor::{Recorder, RecorderOptions},
    trace::{ReadLimits, RunConfig, Termination, Trace, TraceStep},
};
use std::{
    cell::{Cell, RefCell},
    io::{Read, Write},
    num::NonZeroU64,
    path::Path,
    rc::Rc,
};
type Result<T> = std::result::Result<T, String>;
type R<T> = std::result::Result<T, ModelError>;
struct Basic {
    config: Value,
    events: Rc<RefCell<Vec<String>>>,
    order: RefCell<Vec<String>>,
    initial_calls: Cell<usize>,
    step_calls: Cell<usize>,
}
impl Basic {
    fn new(config: Value) -> Self {
        Self {
            config,
            events: Rc::new(RefCell::new(vec![])),
            order: RefCell::new(vec![]),
            initial_calls: Cell::new(0),
            step_calls: Cell::new(0),
        }
    }
    fn batch(&self, k: &str, out: &mut CheckSink<'_>) -> R<()> {
        for c in self.config[k]
            .as_array()
            .ok_or_else(|| ModelError::new("missing checks"))?
        {
            out.push(parse_check(c).map_err(ModelError::new)?);
        }
        Ok(())
    }
}
fn parse_check(v: &Value) -> Result<Check> {
    let id = text(v, "id")?.to_owned();
    Ok(match text(v, "status")? {
        "passed" => Check::passed(id),
        "failed" => Check::failed(id, text(v, "details")?),
        "skipped" => Check::skipped(id, text(v, "details")?),
        _ => return Err("unknown check".into()),
    })
}
impl Model for Basic {
    type State = i32;
    type Input = i32;
    type Output = i32;
    fn metadata(&self) -> ModelMetadata {
        self.order.borrow_mut().push("metadata".into());
        ModelMetadata {
            name: self.config["name"]
                .as_str()
                .unwrap_or("checked-fixture")
                .into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "candidate-v1".into(),
        }
    }
    fn initial_state(&self) -> R<i32> {
        self.initial_calls.set(self.initial_calls.get() + 1);
        Ok(0)
    }
    fn step(&self, s: &i32, i: &i32) -> R<Transition<i32, i32>> {
        self.step_calls.set(self.step_calls.get() + 1);
        Ok(Transition::accepted(s + i, vec![90]))
    }
    fn check_state(&self, _: &i32) -> R<Vec<Check>> {
        Err(ModelError::new("legacy state checker called"))
    }
    fn check_state_into(&self, s: &i32, c: &mut CheckSink<'_>) -> R<()> {
        self.events.borrow_mut().push(format!("state:{s}"));
        self.order.borrow_mut().push(format!("state:{s}"));
        self.batch("state_checks", c)?;
        if let Some(e) = self.config["state_error"].as_str() {
            return Err(ModelError::new(e.to_owned()));
        }
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
        self.events
            .borrow_mut()
            .push(format!("transition:{b}:{i}:{}", t.state));
        self.order
            .borrow_mut()
            .push(format!("transition:{b}:{i}:{}", t.state));
        self.batch("transition_checks", c)?;
        if let Some(e) = self.config["transition_error"].as_str() {
            return Err(ModelError::new(e.to_owned()));
        }
        Ok(())
    }
}
struct Codec(Basic);
impl Model for Codec {
    type State = i32;
    type Input = i32;
    type Output = i32;
    fn metadata(&self) -> ModelMetadata {
        self.0.metadata()
    }
    fn initial_state(&self) -> R<i32> {
        self.0.initial_state()
    }
    fn step(&self, s: &i32, i: &i32) -> R<Transition<i32, i32>> {
        self.0.step(s, i)
    }
    fn check_state(&self, s: &i32) -> R<Vec<Check>> {
        self.0.check_state(s)
    }
    fn check_state_into(&self, s: &i32, c: &mut CheckSink<'_>) -> R<()> {
        self.0.check_state_into(s, c)
    }
    fn check_transition_into(
        &self,
        b: &i32,
        i: &i32,
        t: &TransitionRef<'_, i32, i32>,
        c: &mut CheckSink<'_>,
    ) -> R<()> {
        self.0.check_transition_into(b, i, t, c)
    }
}
impl Codec {
    fn encode(&self, k: &str, n: &i32, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.0.order.borrow_mut().push(format!("encode_{k}:{n}"));
        if self.0.config["codec_error"].as_str() == Some(k) {
            return Err(ModelError::new("fixture codec error"));
        }
        o.extend_from_slice(n.to_string().as_bytes())
    }
    fn decode(b: &[u8]) -> R<i32> {
        std::str::from_utf8(b)
            .map_err(|e| ModelError::new(e.to_string()))?
            .parse()
            .map_err(|_| ModelError::new("integer"))
    }
}
impl ModelCodec for Codec {
    fn encode_state(&self, _: &i32) -> R<Vec<u8>> {
        Err(ModelError::new("legacy state codec called"))
    }
    fn encode_input(&self, _: &i32) -> R<Vec<u8>> {
        Err(ModelError::new("legacy input codec called"))
    }
    fn encode_output(&self, _: &i32) -> R<Vec<u8>> {
        Err(ModelError::new("legacy output codec called"))
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
    fn decode_state(&self, b: &[u8]) -> R<i32> {
        Self::decode(b)
    }
    fn decode_input(&self, b: &[u8]) -> R<i32> {
        Self::decode(b)
    }
}
fn text<'a>(v: &'a Value, k: &str) -> Result<&'a str> {
    v[k].as_str().ok_or_else(|| format!("missing string {k}"))
}
fn int(v: &Value, k: &str) -> Result<i32> {
    v[k].as_i64()
        .and_then(|n| i32::try_from(n).ok())
        .ok_or_else(|| format!("invalid {k}"))
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
fn transition_value(t: &Transition<i32, i32>) -> Value {
    json!({"state":t.state,"outputs":t.outputs,"disposition":disposition_value(&t.disposition)})
}
fn transition(v: &Value) -> Result<Transition<i32, i32>> {
    let disposition = match text(&v["disposition"], "kind")? {
        "accepted" => Disposition::Accepted,
        "rejected" => Disposition::Rejected(text(&v["disposition"], "reason")?.into()),
        "ignored" => Disposition::Ignored(text(&v["disposition"], "reason")?.into()),
        _ => return Err("disposition".into()),
    };
    Ok(Transition {
        state: int(v, "state")?,
        outputs: v["outputs"]
            .as_array()
            .ok_or("outputs")?
            .iter()
            .map(|n| {
                n.as_i64()
                    .and_then(|n| i32::try_from(n).ok())
                    .ok_or("output integer".into())
            })
            .collect::<Result<_>>()?,
        disposition,
    })
}
fn policy(v: &Value) -> Result<CheckPolicy> {
    Ok(CheckPolicy {
        state_every: NonZeroU64::new(
            text(v, "state_every")?
                .parse()
                .map_err(|_| "state period")?,
        )
        .ok_or("zero state period")?,
        transition_checks: v["transition_checks"]
            .as_bool()
            .ok_or("transition policy")?,
    })
}
fn token_case(v: &Value) -> Result<()> {
    let model = Basic::new(v["model"].clone());
    let state = int(v, "state")?;
    let before = int(v, "before")?;
    let input = int(v, "input")?;
    let turn = transition(&v["transition"])?;
    let original = turn.clone();
    let expected = &v["expected"];
    let result=match text(v,"kind")? {
 "initial"=>execution::check_initial(&model,&state).map(|token|{assert!(std::ptr::eq(token.model(),&model));assert!(std::ptr::eq(token.state(),&state));let value=json!({"kind":"initial","checks":checks_value(token.checks()),"state_check_count":token.state_check_count(),"state":token.state()});assert_eq!(checks_value(&token.into_checks()),value["checks"]);value}),
 "turn"=>{let sequence=text(v,"sequence")?.parse::<u64>().map_err(|_|"sequence")?;execution::check_turn(&model,&before,&input,&turn,sequence,policy(&v["policy"])?).map(|token|{assert!(std::ptr::eq(token.model(),&model));assert!(std::ptr::eq(token.before(),&before));assert!(std::ptr::eq(token.input(),&input));assert!(std::ptr::eq(token.transition(),&turn));let value=json!({"kind":"turn","checks":checks_value(token.checks()),"state_check_count":token.state_check_count(),"before":token.before(),"input":token.input(),"transition":transition_value(token.transition()),"sequence":token.sequence().to_string(),"full_checking":token.full_checking()});let o=token.observation();assert_eq!(o.sequence,token.sequence());assert_eq!(o.before,token.before());assert_eq!(o.input,token.input());assert_eq!(o.transition.state,&token.transition().state);assert_eq!(o.transition.outputs,token.transition().outputs.as_slice());assert_eq!(o.transition.disposition,&token.transition().disposition);assert_eq!(o.checks,token.checks());assert_eq!(o.state_check_count,token.state_check_count());assert_eq!(checks_value(&token.into_checks()),value["checks"]);value})},_=>return Err("kind".into())};
    let actual_token = match result {
        Ok(value) => {
            if !expected["error_contains"].is_null() {
                return Err("expected token error but succeeded".into());
            }
            value
        }
        Err(error) => {
            let needle = text(expected, "error_contains")?;
            if !error.to_string().contains(needle) {
                return Err(format!("error {error} lacks {needle}"));
            }
            Value::Null
        }
    };
    let actual = json!({"error_contains":expected["error_contains"],"events":*model.events.borrow(),"token":actual_token,"initial_state_calls":model.initial_calls.get(),"step_calls":model.step_calls.get()});
    if actual != *expected {
        return Err(format!("expected {expected}, actual {actual}"));
    }
    if turn != original {
        return Err("checking mutated supplied transition".into());
    }
    Ok(())
}
fn json_file(path: &Path) -> Result<Value> {
    let mut bytes = vec![];
    std::fs::File::open(path)
        .map_err(|e| e.to_string())?
        .take(4194305)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > 4194304 {
        return Err("JSON byte limit".into());
    }
    serde_json::from_slice(&bytes).map_err(|e| e.to_string())
}
pub fn qualify(path: &Path) -> Result<Value> {
    let root = json_file(path)?;
    if root["profile"] != "checked-results-v1" {
        return Err("profile".into());
    }
    let mut count = 0;
    for v in root["cases"].as_array().ok_or("cases")? {
        token_case(v).map_err(|e| format!("{}: {e}", v["case"]))?;
        count += 1;
    }
    let mut recorder_count = 0;
    for v in root["recorder_cases"].as_array().ok_or("recorder_cases")? {
        recorder_case(v).map_err(|e| format!("{}: {e}", v["case"]))?;
        recorder_count += 1;
    }
    Ok(json!({"profile":"checked-results-v1","token_cases":count,"recorder_cases":recorder_count}))
}
fn unsigned(v: &Value) -> Result<u64> {
    match v {
        Value::String(s) => {
            let n = s.parse::<u64>().map_err(|_| "unsigned decimal")?;
            if n.to_string() != *s {
                return Err("noncanonical decimal".into());
            }
            Ok(n)
        }
        _ => v.as_u64().ok_or("unsigned integer".into()),
    }
}
fn limits(v: &Value) -> Result<ReadLimits> {
    let mut l = ReadLimits::default();
    for (k, v) in v.as_object().ok_or("limits")? {
        let n = unsigned(v)?;
        if k == "max_total_bytes" {
            l.max_total_bytes = n;
            continue;
        }
        let n = usize::try_from(n).map_err(|_| "usize overflow")?;
        match k.as_str() {
            "max_frame_bytes" => l.max_frame_bytes = n,
            "max_blob_bytes" => l.max_blob_bytes = n,
            "max_string_bytes" => l.max_string_bytes = n,
            "max_steps" => l.max_steps = n,
            "max_checks_per_batch" | "max_checks_per_step" => l.max_checks_per_step = n,
            "max_outputs_per_step" => l.max_outputs_per_step = n,
            "max_parameters" => l.max_parameters = n,
            "max_items" => l.max_items = n,
            _ => return Err(format!("unknown limit {k}")),
        }
    }
    Ok(l)
}
fn run_config(v: &Value) -> Result<RunConfig> {
    Ok(RunConfig {
        strategy: text(v, "strategy")?.into(),
        seed: if v["seed"].is_null() {
            None
        } else {
            Some(unsigned(&v["seed"])?)
        },
        parameters: v["parameters"]
            .as_array()
            .ok_or("parameters")?
            .iter()
            .map(|p| {
                let a = p
                    .as_array()
                    .filter(|a| a.len() == 2)
                    .ok_or("parameter pair")?;
                Ok((
                    a[0].as_str().ok_or("parameter key")?.into(),
                    a[1].as_str().ok_or("parameter value")?.into(),
                ))
            })
            .collect::<Result<_>>()?,
    })
}
fn options(v: &Value) -> Result<RecorderOptions> {
    Ok(RecorderOptions {
        max_steps: usize::try_from(unsigned(&v["max_steps"])?).map_err(|_| "max_steps usize")?,
        max_retained_bytes: unsigned(&v["max_retained_bytes"])?,
        limits: limits(&v["limits"])?,
    })
}
fn termination_name(t: &Termination) -> &'static str {
    match t {
        Termination::Completed => "completed",
        Termination::PropertyFailed => "property_failed",
        Termination::Interrupted => "interrupted",
        Termination::StepLimit => "step_limit",
        Termination::ModelError(_) => "model_error",
    }
}
fn check_error(actual: Option<&ModelError>, expected: &Value) -> Result<()> {
    match (expected.as_str(), actual) {
        (None, None) => Ok(()),
        (Some(needle), Some(error)) if error.to_string().contains(needle) => Ok(()),
        _ => Err(format!(
            "expected error {expected}, actual {}",
            actual.map(ToString::to_string).unwrap_or_default()
        )),
    }
}
fn trace_step_value(s: &TraceStep) -> Result<Value> {
    Ok(
        json!({"input":Codec::decode(&s.input).map_err(|e|e.to_string())?,"transition":{"state":Codec::decode(&s.post_state).map_err(|e|e.to_string())?,"outputs":s.outputs.iter().map(|b|Codec::decode(b).map_err(|e|e.to_string())).collect::<Result<Vec<_>>>()?,"disposition":disposition_value(&s.disposition)},"checks":checks_value(&s.checks)}),
    )
}
fn state_value(r: &Recorder, error: &Value) -> Result<Value> {
    let t = r.snapshot();
    Ok(
        json!({"error_contains":error,"observed_steps":r.observed_steps().to_string(),"evicted_steps":r.evicted_steps().to_string(),"retained_steps":r.retained_steps(),"frozen":r.is_frozen(),"termination":termination_name(&t.termination),"initial_state":Codec::decode(&t.initial_state).map_err(|e|e.to_string())?,"initial_checks":checks_value(&t.initial_checks),"steps":t.steps.iter().map(trace_step_value).collect::<Result<Vec<_>>>()?}),
    )
}
fn verify_recorder(
    r: &Recorder,
    options: &RecorderOptions,
    origin: u64,
    config: &RunConfig,
) -> Result<()> {
    let t = r.snapshot();
    let mut bytes = vec![];
    r.write_to(&mut bytes).map_err(|e| e.to_string())?;
    if bytes.len() as u64 != r.retained_bytes() || r.retained_bytes() > options.max_retained_bytes {
        return Err("retained byte accounting".into());
    }
    if Trace::read_from(bytes.as_slice(), &options.limits).map_err(|e| e.to_string())? != t {
        return Err("runtime binary roundtrip".into());
    }
    let mut expected = config.parameters.clone();
    let core_version = include_str!("../../../Cargo.toml")
        .lines()
        .find_map(|l| {
            l.strip_prefix("version = \"")
                .and_then(|s| s.strip_suffix('"'))
        })
        .ok_or("core version")?;
    expected.push(("stateless.engine.version".into(), core_version.into()));
    expected.push((
        "stateless.engine.build".into(),
        stateless::modeling::RUNTIME_BUILD_ID.into(),
    ));
    if origin != 0 {
        expected.push((
            "stateless.monitor.sequence_origin".into(),
            origin.to_string(),
        ));
    }
    for (k, v) in [
        ("max_steps", options.max_steps.to_string()),
        ("max_retained_bytes", options.max_retained_bytes.to_string()),
        ("checking", "full".into()),
        ("observed_steps", r.observed_steps().to_string()),
        ("evicted_steps", r.evicted_steps().to_string()),
        ("checkpoint_after_sequence", r.evicted_steps().to_string()),
    ] {
        expected.push((format!("stateless.monitor.{k}"), v));
    }
    if t.config.strategy != config.strategy
        || t.config.seed != config.seed
        || t.config.parameters != expected
    {
        return Err("runtime producer configuration/counters differ".into());
    }
    Ok(())
}
fn recorder_case(v: &Value) -> Result<()> {
    let model = Codec(Basic::new(v["model"].clone()));
    let initial_state = int(v, "initial_state")?;
    let initial = if v["entry_mode"] == "ordinary" {
        None
    } else {
        Some(execution::check_initial(&model, &initial_state).map_err(|e| e.to_string())?)
    };
    let config = run_config(&v["config"])?;
    let options = options(&v["options"])?;
    let origin = unsigned(&v["origin"])?;
    let before_checks = model.0.events.borrow().len();
    model.0.order.borrow_mut().clear();
    let result = if let Some(initial) = &initial {
        if origin == 0 {
            Recorder::with_checked_initial(initial, config.clone(), options.clone())
        } else {
            Recorder::with_checked_initial_at(initial, origin, config.clone(), options.clone())
        }
    } else {
        if origin != 0 {
            return Err("ordinary origin must be zero".into());
        }
        Recorder::with_options(&model, &initial_state, config.clone(), options.clone())
    };
    if model.0.events.borrow().len()
        != before_checks + usize::from(v["entry_mode"] == "ordinary" && result.is_ok())
    {
        return Err("initial handoff repeated checking".into());
    }
    let expected = &v["expected"];
    check_error(result.as_ref().err(), &expected["initial_error_contains"])?;
    let mut recorder = match result {
        Err(_) => return Ok(()),
        Ok(r) => r,
    };
    let expected_order = if v["entry_mode"] == "ordinary" {
        vec![
            "metadata".to_owned(),
            format!("encode_state:{initial_state}"),
            format!("state:{initial_state}"),
        ]
    } else {
        vec![
            "metadata".to_owned(),
            format!("encode_state:{initial_state}"),
        ]
    };
    if *model.0.order.borrow() != expected_order {
        return Err(format!(
            "constructor callback order {:?}",
            model.0.order.borrow()
        ));
    }
    verify_recorder(&recorder, &options, origin, &config)?;
    let actions = v["actions"].as_array().ok_or("actions")?;
    let states = expected["states"].as_array().ok_or("states")?;
    if actions.len() != states.len() {
        return Err("action/state count".into());
    }
    for (a, expected) in actions.iter().zip(states) {
        let error = if let Some(error) = a["stop_error"].as_str() {
            recorder.stop_with_error(ModelError::new(error.to_owned()));
            None
        } else {
            let mut cfg = a.get("checks_model").unwrap_or(&v["model"]).clone();
            if a["model_name"] == "different" {
                cfg["name"] = json!("different");
            }
            if let Some(k) = a["encode_error"].as_str() {
                cfg["codec_error"] = json!(k);
            }
            let action_model = Codec(Basic::new(cfg));
            let before = int(a, "before")?;
            let input = int(a, "input")?;
            let turn = transition(&a["transition"])?;
            let result_error = if a["representation"] == "decimal-string" {
                let string_model = StringCodec(&action_model);
                let b = before.to_string();
                let i = input.to_string();
                let t = Transition {
                    state: turn.state.to_string(),
                    outputs: turn.outputs.iter().map(ToString::to_string).collect(),
                    disposition: turn.disposition.clone(),
                };
                let checked = execution::check_turn(
                    &string_model,
                    &b,
                    &i,
                    &t,
                    unsigned(&a["sequence"])?,
                    policy(&a["policy"])?,
                )
                .map_err(|e| e.to_string())?;
                let prior = action_model.0.events.borrow().len();
                let result = recorder.observe_checked(&checked);
                if action_model.0.events.borrow().len() != prior {
                    return Err("string handoff repeated checking".into());
                }
                result.err()
            } else if a["mode"] == "ordinary" {
                let prior = action_model.0.events.borrow().len();
                let result = recorder.observe(&action_model, &before, &input, &turn);
                if result.is_ok() && action_model.0.events.borrow().len() != prior + 2 {
                    return Err("ordinary observation did not check exactly once".into());
                }
                result.err()
            } else {
                let checked = execution::check_turn(
                    &action_model,
                    &before,
                    &input,
                    &turn,
                    unsigned(&a["sequence"])?,
                    policy(&a["policy"])?,
                )
                .map_err(|e| e.to_string())?;
                let checks = checked.checks().to_vec();
                let prior = action_model.0.events.borrow().len();
                let result = recorder.observe_checked(&checked);
                if action_model.0.events.borrow().len() != prior {
                    return Err("turn handoff repeated checking".into());
                }
                if let Ok(actual) = &result {
                    if **actual != checks {
                        return Err("handoff checks differ".into());
                    }
                }
                result.err()
            };
            if action_model.0.initial_calls.get() != 0 || action_model.0.step_calls.get() != 0 {
                return Err("handoff executes initial/reducer".into());
            }
            result_error
        };
        check_error(error.as_ref(), &expected["error_contains"])?;
        let actual = state_value(&recorder, &expected["error_contains"])?;
        let mut expected = expected.clone();
        for step in expected["steps"].as_array_mut().ok_or("expected steps")? {
            step.as_object_mut()
                .ok_or("expected step")?
                .remove("state_check_count");
        }
        if actual != expected {
            return Err(format!(
                "runtime state expected {expected}, actual {actual}"
            ));
        }
        verify_recorder(&recorder, &options, origin, &config)?;
    }
    if model.0.initial_calls.get() != 0 || model.0.step_calls.get() != 0 {
        return Err("initial handoff executed model".into());
    }
    Ok(())
}
// A distinct Rust model representation can feed the nongeneric Recorder when
// metadata and canonical evidence agree; the recorder has no pointer/type gate.
struct StringCodec<'a>(&'a Codec);
impl Model for StringCodec<'_> {
    type State = String;
    type Input = String;
    type Output = String;
    fn metadata(&self) -> ModelMetadata {
        self.0.metadata()
    }
    fn initial_state(&self) -> R<String> {
        Err(ModelError::new("unexpected string initial"))
    }
    fn step(&self, _: &String, _: &String) -> R<Transition<String, String>> {
        Err(ModelError::new("unexpected string reducer"))
    }
    fn check_state(&self, _: &String) -> R<Vec<Check>> {
        Err(ModelError::new("unexpected legacy string checker"))
    }
    fn check_state_into(&self, s: &String, c: &mut CheckSink<'_>) -> R<()> {
        self.0.check_state_into(&Codec::decode(s.as_bytes())?, c)
    }
    fn check_transition_into(
        &self,
        b: &String,
        i: &String,
        t: &TransitionRef<'_, String, String>,
        c: &mut CheckSink<'_>,
    ) -> R<()> {
        let b = Codec::decode(b.as_bytes())?;
        let i = Codec::decode(i.as_bytes())?;
        let state = Codec::decode(t.state.as_bytes())?;
        let outputs = t
            .outputs
            .iter()
            .map(|s| Codec::decode(s.as_bytes()))
            .collect::<R<Vec<_>>>()?;
        self.0.check_transition_into(
            &b,
            &i,
            &TransitionRef {
                state: &state,
                outputs: &outputs,
                disposition: t.disposition,
            },
            c,
        )
    }
}
impl ModelCodec for StringCodec<'_> {
    fn encode_state(&self, _: &String) -> R<Vec<u8>> {
        Err(ModelError::new("legacy string encoder"))
    }
    fn encode_input(&self, _: &String) -> R<Vec<u8>> {
        Err(ModelError::new("legacy string encoder"))
    }
    fn encode_output(&self, _: &String) -> R<Vec<u8>> {
        Err(ModelError::new("legacy string encoder"))
    }
    fn encode_state_into(&self, s: &String, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.0.encode_state_into(&Codec::decode(s.as_bytes())?, o)
    }
    fn encode_input_into(&self, s: &String, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.0.encode_input_into(&Codec::decode(s.as_bytes())?, o)
    }
    fn encode_output_into(&self, s: &String, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.0.encode_output_into(&Codec::decode(s.as_bytes())?, o)
    }
    fn decode_state(&self, b: &[u8]) -> R<String> {
        Ok(Codec::decode(b)?.to_string())
    }
    fn decode_input(&self, b: &[u8]) -> R<String> {
        self.decode_state(b)
    }
}

fn require(ok: bool, msg: &str) -> Result<()> {
    if ok { Ok(()) } else { Err(msg.into()) }
}
fn number(v: &Value, k: &str) -> Result<u64> {
    v[k].as_u64().ok_or_else(|| format!("missing {k}"))
}
fn list_values<'a>(v: &'a Value, k: &str) -> Result<&'a [Value]> {
    v[k].as_array()
        .map(Vec::as_slice)
        .ok_or_else(|| format!("missing {k}"))
}
fn hex(b: &[u8]) -> String {
    b.iter().map(|n| format!("{n:02x}")).collect()
}
fn unhex(v: &Value) -> Result<Vec<u8>> {
    let s = v.as_str().ok_or("hex must be string")?;
    require(
        s.len() <= 8388608
            && s.len() % 2 == 0
            && s.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        "invalid hex",
    )?;
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).map_err(|e| e.to_string()))
        .collect()
}
fn check_json(c: &Check) -> Value {
    let (status, details) = match &c.status {
        CheckStatus::Passed => ("passed", ""),
        CheckStatus::Failed(s) => ("failed", s.as_str()),
        CheckStatus::Skipped(s) => ("skipped", s.as_str()),
    };
    json!({"id":c.id.as_str(),"status":status,"details":details})
}
fn portable_json(t: &Trace) -> Value {
    let (kind, error) = match &t.termination {
        Termination::Completed => ("completed", ""),
        Termination::PropertyFailed => ("property_failed", ""),
        Termination::StepLimit => ("step_limit", ""),
        Termination::Interrupted => ("interrupted", ""),
        Termination::ModelError(s) => ("model_error", s.as_str()),
    };
    json!({"format":"stateless.trace-json","version":1,"metadata":{"name":t.metadata.name,"model_version":t.metadata.model_version,"properties_version":t.metadata.properties_version,"codec_version":t.metadata.codec_version,"build":t.metadata.build},"initial_state":hex(&t.initial_state),"initial_checks":t.initial_checks.iter().map(check_json).collect::<Vec<_>>(),"steps":t.steps.iter().map(|s|json!({"input":hex(&s.input),"disposition":{"kind":"accepted","reason":""},"outputs":s.outputs.iter().map(|b|hex(b)).collect::<Vec<_>>(),"post_state":hex(&s.post_state),"checks":s.checks.iter().map(check_json).collect::<Vec<_>>() })).collect::<Vec<_>>(),"termination":kind,"error":error})
}
fn parse_checks(v: &Value) -> Result<Vec<Check>> {
    v.as_array()
        .ok_or("checks must be array")?
        .iter()
        .map(|c| {
            Ok(match text(c, "status")? {
                "passed" => Check::passed(text(c, "id")?.to_owned()),
                "failed" => Check::failed(text(c, "id")?.to_owned(), text(c, "details")?),
                "skipped" => Check::skipped(text(c, "id")?.to_owned(), text(c, "details")?),
                _ => return Err("invalid check".into()),
            })
        })
        .collect()
}
fn parse_portable(v: &Value) -> Result<Trace> {
    require(
        v["format"] == "stateless.trace-json" && v["version"] == 1,
        "invalid portable trace",
    )?;
    let m = &v["metadata"];
    let mut steps = Vec::new();
    for s in list_values(v, "steps")? {
        require(
            s["disposition"]["kind"] == "accepted"
                && s["disposition"]["reason"].as_str().unwrap_or("").is_empty(),
            "application requires accepted disposition",
        )?;
        steps.push(TraceStep {
            input: unhex(&s["input"])?,
            disposition: Disposition::Accepted,
            outputs: list_values(s, "outputs")?
                .iter()
                .map(unhex)
                .collect::<Result<Vec<_>>>()?,
            post_state: unhex(&s["post_state"])?,
            checks: parse_checks(&s["checks"])?,
        });
    }
    require(
        v["termination"] == "property_failed" && v["error"] == "",
        "application requires property failure without terminal error",
    )?;
    Ok(Trace {
        metadata: ModelMetadata {
            name: text(m, "name")?.into(),
            model_version: u32::try_from(number(m, "model_version")?).map_err(|e| e.to_string())?,
            properties_version: u32::try_from(number(m, "properties_version")?)
                .map_err(|e| e.to_string())?,
            codec_version: u32::try_from(number(m, "codec_version")?).map_err(|e| e.to_string())?,
            build: text(m, "build")?.into(),
        },
        config: RunConfig::default(),
        initial_state: unhex(&v["initial_state"])?,
        initial_checks: parse_checks(&v["initial_checks"])?,
        steps,
        termination: Termination::PropertyFailed,
    })
}
fn report_value(r: &ReplayReport) -> Value {
    let (outcome, step, field) = match &r.outcome {
        ReplayOutcome::Exact => ("exact", None, None),
        ReplayOutcome::Diverged { step, field } => ("diverged", *step, Some(*field)),
        ReplayOutcome::Incompatible { .. } => ("incompatible", None, None),
    };
    json!({"outcome":outcome,"divergence_step":step,"divergence_field":field,"steps_verified":r.steps_verified,"failure_reproduced":r.failure_reproduced,"build_matches":r.build_matches})
}
fn differences_value(d: ReplayDifferences) -> Value {
    json!({"disposition":d.disposition,"outputs":d.outputs,"state":d.state,"checks":d.checks})
}
fn step_value(s: &TraceStep) -> Value {
    json!({"input":hex(&s.input),"outputs":s.outputs.iter().map(|b|hex(b)).collect::<Vec<_>>(),"post_state":hex(&s.post_state),"disposition":disposition_value(&s.disposition),"checks":checks_value(&s.checks)})
}
fn consumer_session(model: &Threshold, t: &Trace, bounded: bool) -> Result<Value> {
    let mut observations = vec![];
    let mut observer = |o: ReplayObservation<'_, Threshold>, r: &ReplayReport| -> R<()> {
        observations.push(match o{
 ReplayObservation::Initial(i)=>json!({"kind":"initial","sequence":0,"state":i.state,"checks":checks_value(i.checks),"expected_state":hex(i.expected_state),"actual_state":hex(i.actual_state),"expected_checks":checks_value(i.expected_checks),"differences":differences_value(i.differences),"report":report_value(r)}),
 ReplayObservation::Turn(t)=>json!({"kind":"turn","sequence":t.actual.sequence,"before":t.actual.before,"input":t.actual.input,"transition":{"state":t.actual.transition.state,"outputs":t.actual.transition.outputs,"disposition":disposition_value(t.actual.transition.disposition)},"checks":checks_value(t.actual.checks),"state_check_count":t.actual.state_check_count,"expected":step_value(t.expected),"actual_input":hex(t.actual_input),"actual_outputs":t.actual_outputs.iter().map(|b|hex(b)).collect::<Vec<_>>(),"actual_state":hex(t.actual_state),"differences":differences_value(t.differences),"report":report_value(r)}),
 ReplayObservation::Error{..}=>return Err(ModelError::new("unexpected replay error observation"))});
        Ok(())
    };
    let report = if bounded {
        execution::replay_with_observations_bounded(
            model,
            t,
            ReplayOptions::default(),
            &consumer_limits(),
            &mut observer,
        )
    } else {
        execution::replay_with_observations(model, t, ReplayOptions::default(), &mut observer)
    }
    .map_err(|e| e.to_string())?;
    Ok(json!({"report":report_value(&report),"observations":observations}))
}
#[derive(Default)]
struct Threshold {
    initial: Cell<usize>,
    reducer: Cell<usize>,
    state: Cell<usize>,
    edge: Cell<usize>,
    legacy: Cell<usize>,
    into: Cell<usize>,
}
impl Threshold {
    fn legacy<T>(&self) -> R<T> {
        self.legacy.set(self.legacy.get() + 1);
        Err(ModelError::new("legacy callback called"))
    }
    fn encode(&self, s: &str, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.into.set(self.into.get() + 1);
        for b in s.as_bytes() {
            o.extend_from_slice(&[*b])?;
        }
        Ok(())
    }
    fn counts(&self) -> Value {
        json!({"initial_state":self.initial.get(),"reducer":self.reducer.get(),"state":self.state.get(),"transition":self.edge.get()})
    }
}
impl Model for Threshold {
    type State = i32;
    type Input = i32;
    type Output = String;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "native-checked-threshold".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "application-v1".into(),
        }
    }
    fn initial_state(&self) -> R<i32> {
        self.initial.set(self.initial.get() + 1);
        Ok(0)
    }
    fn step(&self, s: &i32, i: &i32) -> R<Transition<i32, String>> {
        self.reducer.set(self.reducer.get() + 1);
        if *i != 1 {
            return Err(ModelError::new("expected one"));
        }
        Ok(Transition::accepted(s + i, vec!["changed".into()]))
    }
    fn check_state(&self, _: &i32) -> R<Vec<Check>> {
        self.legacy()
    }
    fn check_state_into(&self, s: &i32, c: &mut CheckSink<'_>) -> R<()> {
        self.state.set(self.state.get() + 1);
        c.push(if *s >= 3 {
            Check::failed("threshold.bound", "at least three")
        } else {
            Check::passed("threshold.bound")
        });
        c.push(Check::passed("state.appended"));
        Ok(())
    }
    fn check_transition(
        &self,
        _: &i32,
        _: &i32,
        _: &TransitionRef<'_, i32, String>,
    ) -> R<Vec<Check>> {
        self.legacy()
    }
    fn check_transition_into(
        &self,
        _: &i32,
        _: &i32,
        _: &TransitionRef<'_, i32, String>,
        c: &mut CheckSink<'_>,
    ) -> R<()> {
        self.edge.set(self.edge.get() + 1);
        c.push(Check::passed("edge.checked"));
        Ok(())
    }
}
impl ModelCodec for Threshold {
    fn encode_state(&self, _: &i32) -> R<Vec<u8>> {
        self.legacy()
    }
    fn encode_input(&self, _: &i32) -> R<Vec<u8>> {
        self.legacy()
    }
    fn encode_output(&self, _: &String) -> R<Vec<u8>> {
        self.legacy()
    }
    fn encode_state_into(&self, n: &i32, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.encode(&n.to_string(), o)
    }
    fn encode_input_into(&self, n: &i32, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.encode(&n.to_string(), o)
    }
    fn encode_output_into(&self, s: &String, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.encode(s, o)
    }
    fn decode_state(&self, b: &[u8]) -> R<i32> {
        Codec::decode(b)
    }
    fn decode_input(&self, b: &[u8]) -> R<i32> {
        let n = Codec::decode(b)?;
        if n != 1 {
            return Err(ModelError::new("expected one"));
        }
        Ok(n)
    }
}
fn consumer_limits() -> ReadLimits {
    ReadLimits {
        max_total_bytes: 8192,
        max_frame_bytes: 2048,
        max_blob_bytes: 64,
        max_string_bytes: 256,
        max_steps: 16,
        max_checks_per_step: 16,
        max_outputs_per_step: 16,
        max_parameters: 32,
        max_items: 256,
    }
}
fn consumer_options(steps: usize) -> RecorderOptions {
    RecorderOptions {
        max_steps: steps,
        max_retained_bytes: 8192,
        limits: consumer_limits(),
    }
}
fn manual() -> RunConfig {
    RunConfig {
        strategy: "manual".into(),
        seed: None,
        parameters: vec![],
    }
}
fn consumer_config(c: &RunConfig) -> Value {
    json!({"strategy":c.strategy,"seed":c.seed.map(|n|n.to_string()),"parameters":c.parameters})
}
fn sessions(t: &Trace) -> Result<Value> {
    let rich = Threshold::default();
    let bounded = Threshold::default();
    Ok(
        json!({"rich":consumer_session(&rich,t,false)?,"bounded":consumer_session(&bounded,t,true)?,"retained_config":consumer_config(&t.config),"legacy_encoder_calls":rich.legacy.get()+bounded.legacy.get(),"codec_into_calls":rich.into.get()+bounded.into.get()}),
    )
}
fn checked_consumer_controls() -> Result<(Value, Value)> {
    let m = Threshold::default();
    let s0 = 0;
    let initial = execution::check_initial(&m, &s0).map_err(|e| e.to_string())?;
    let counts = m.counts();
    let mut first = Recorder::with_checked_initial(&initial, manual(), consumer_options(3))
        .map_err(|e| e.to_string())?;
    let mut second = Recorder::with_checked_initial(&initial, manual(), consumer_options(3))
        .map_err(|e| e.to_string())?;
    require(m.counts() == counts, "attach rechecked")?;
    let input = 1;
    let t1 = m.step(&s0, &input).map_err(|e| e.to_string())?;
    let token = execution::check_turn(&m, &s0, &input, &t1, 1, CheckPolicy::default())
        .map_err(|e| e.to_string())?;
    let counts = m.counts();
    first.observe_checked(&token).map_err(|e| e.to_string())?;
    second.observe_checked(&token).map_err(|e| e.to_string())?;
    require(m.counts() == counts, "handoff rechecked")?;
    require(first.snapshot() == second.snapshot(), "token reuse differs")?;
    require(
        first.observe_checked(&token).is_err() && first.is_frozen() && first.retained_steps() == 1,
        "duplicate not rejected",
    )?;
    let mut sampled = Recorder::with_checked_initial(&initial, manual(), consumer_options(3))
        .map_err(|e| e.to_string())?;
    let sampled_token = execution::check_turn(
        &m,
        &s0,
        &input,
        &t1,
        1,
        CheckPolicy {
            state_every: NonZeroU64::new(2).unwrap(),
            transition_checks: true,
        },
    )
    .map_err(|e| e.to_string())?;
    require(
        sampled.observe_checked(&sampled_token).is_err()
            && sampled.is_frozen()
            && sampled.retained_steps() == 0,
        "sampled accepted",
    )?;
    let mut gap = Recorder::with_checked_initial(&initial, manual(), consumer_options(3))
        .map_err(|e| e.to_string())?;
    let gap_token = execution::check_turn(&m, &s0, &input, &t1, 2, CheckPolicy::default())
        .map_err(|e| e.to_string())?;
    require(
        gap.observe_checked(&gap_token).is_err() && gap.is_frozen() && gap.retained_steps() == 0,
        "gap accepted",
    )?;
    let s1 = 1;
    let origin_initial = execution::check_initial(&m, &s1).map_err(|e| e.to_string())?;
    let mut origin =
        Recorder::with_checked_initial_at(&origin_initial, 10, manual(), consumer_options(3))
            .map_err(|e| e.to_string())?;
    let t2 = m.step(&s1, &input).map_err(|e| e.to_string())?;
    let c11 = execution::check_turn(&m, &s1, &input, &t2, 11, CheckPolicy::default())
        .map_err(|e| e.to_string())?;
    origin.observe_checked(&c11).map_err(|e| e.to_string())?;
    let t3 = m.step(&t2.state, &input).map_err(|e| e.to_string())?;
    let c12 = execution::check_turn(&m, &t2.state, &input, &t3, 12, CheckPolicy::default())
        .map_err(|e| e.to_string())?;
    origin.observe_checked(&c12).map_err(|e| e.to_string())?;
    require(
        origin.observed_steps() == 2
            && origin
                .snapshot()
                .config
                .parameters
                .iter()
                .any(|(k, v)| k == "stateless.monitor.sequence_origin" && v == "10"),
        "origin lost",
    )?;
    let mut ring = Recorder::with_checked_initial(&initial, manual(), consumer_options(1))
        .map_err(|e| e.to_string())?;
    ring.observe_checked(&token).map_err(|e| e.to_string())?;
    let c2 = execution::check_turn(&m, &t1.state, &input, &t2, 2, CheckPolicy::default())
        .map_err(|e| e.to_string())?;
    ring.observe_checked(&c2).map_err(|e| e.to_string())?;
    let ring_trace = ring.snapshot();
    require(
        ring.evicted_steps() == 1
            && ring_trace.initial_state == b"1"
            && ring_trace.initial_checks.len() == 2
            && ring_trace
                .initial_checks
                .iter()
                .all(|c| c.id.as_str() != "edge.checked")
            && ring_trace.steps.len() == 1
            && ring_trace.steps[0].checks.len() == 3,
        "ring promoted transition checks",
    )?;
    let mut ring_sessions = sessions(&ring_trace)?;
    ring_sessions
        .as_object_mut()
        .unwrap()
        .remove("legacy_encoder_calls");
    ring_sessions
        .as_object_mut()
        .unwrap()
        .remove("codec_into_calls");
    let over = Transition::accepted(2, vec!["x".repeat(65)]);
    let over_token = execution::check_turn(&m, &t1.state, &input, &over, 2, CheckPolicy::default())
        .map_err(|e| e.to_string())?;
    require(
        second.observe_checked(&over_token).is_err()
            && second.is_frozen()
            && second.retained_steps() == 1,
        "oversize prefix lost",
    )?;
    let trace = second.snapshot();
    require(
        matches!(trace.termination, Termination::ModelError(_)),
        "oversize termination",
    )?;
    let mut wire = vec![];
    second.write_to(&mut wire).map_err(|e| e.to_string())?;
    let decoded =
        Trace::read_from(wire.as_slice(), &consumer_limits()).map_err(|e| e.to_string())?;
    let mut wire2 = vec![];
    decoded
        .write_with_limits(&mut wire2, &consumer_limits())
        .map_err(|e| e.to_string())?;
    require(
        decoded == trace && wire == wire2,
        "bounded prefix roundtrip",
    )?;
    require(m.legacy.get() == 0, "control legacy callback")?;
    Ok((
        json!({"check_once":true,"attach_rechecks":false,"handoff_rechecks":false,"token_reused":true,"sampled_rejected":true,"duplicate_rejected":true,"gap_rejected":true,"origin_preserved":true,"ring_state_prefix_only":true,"bounded_error_prefix":true,"static_borrow":true}),
        ring_sessions,
    ))
}
fn write_new(path: &str, data: &[u8]) -> Result<()> {
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    f.write_all(data).map_err(|e| e.to_string())
}
pub fn record(prefix: &str) -> Result<Value> {
    let m = Threshold::default();
    let mut state = 0;
    let checkpoint = 0;
    let initial = execution::check_initial(&m, &checkpoint).map_err(|e| e.to_string())?;
    let prior = m.counts();
    let mut recorder = Recorder::with_checked_initial(&initial, manual(), consumer_options(3))
        .map_err(|e| e.to_string())?;
    require(prior == m.counts(), "main attach rechecked")?;
    let mut checked_value = Value::Null;
    for sequence in 1..=3 {
        let input = 1;
        let turn = m.step(&state, &input).map_err(|e| e.to_string())?;
        let checked =
            execution::check_turn(&m, &state, &input, &turn, sequence, CheckPolicy::default())
                .map_err(|e| e.to_string())?;
        if sequence == 1 {
            checked_value = json!({"initial_checks":initial.checks().len(),"state_check_count":checked.state_check_count(),"full_checking":checked.full_checking(),"sequence":checked.sequence().to_string()});
        }
        let prior = m.counts();
        recorder
            .observe_checked(&checked)
            .map_err(|e| e.to_string())?;
        require(m.counts() == prior, "main handoff rechecked")?;
        state = turn.state;
    }
    let counts = m.counts();
    require(
        counts == json!({"initial_state":0,"reducer":3,"state":4,"transition":3}),
        "main check counts",
    )?;
    let trace = recorder.into_trace();
    let mut wire = vec![];
    trace
        .write_with_limits(&mut wire, &consumer_limits())
        .map_err(|e| e.to_string())?;
    write_new(&format!("{prefix}.bin"), &wire)?;
    write_new(
        &format!("{prefix}.json"),
        &serde_json::to_vec(&portable_json(&trace)).map_err(|e| e.to_string())?,
    )?;
    let mut receipt = sessions(&trace)?;
    let (controls, ring) = checked_consumer_controls()?;
    receipt["controls"] = controls;
    receipt["ring"] = ring;
    receipt["checked"] = checked_value;
    receipt["main_counts"] = counts;
    receipt["legacy_encoder_calls"] =
        json!(receipt["legacy_encoder_calls"].as_u64().unwrap() + m.legacy.get() as u64);
    receipt["codec_into_calls"] =
        json!(receipt["codec_into_calls"].as_u64().unwrap() + m.into.get() as u64);
    receipt["producer"] = json!("rust");
    receipt["mode"] = json!("record");
    Ok(receipt)
}
pub fn replay(path: &Path) -> Result<Value> {
    let trace = Trace::read_from(
        std::fs::File::open(path).map_err(|e| e.to_string())?,
        &consumer_limits(),
    )
    .map_err(|e| e.to_string())?;
    let mut receipt = sessions(&trace)?;
    receipt["producer"] = json!("rust");
    receipt["mode"] = json!("replay");
    Ok(receipt)
}
pub fn replay_json(path: &Path) -> Result<Value> {
    let trace = parse_portable(&json_file(path)?)?;
    let m = Threshold::default();
    let report =
        execution::replay(&m, &trace, ReplayOptions::default()).map_err(|e| e.to_string())?;
    let mut receipt = report_value(&report);
    receipt["legacy_encoder_calls"] = json!(m.legacy.get());
    receipt["codec_into_calls"] = json!(m.into.get());
    receipt["producer"] = json!("rust");
    receipt["mode"] = json!("replay-json");
    Ok(receipt)
}

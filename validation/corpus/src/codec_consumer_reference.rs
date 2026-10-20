//! Installed-consumer application using the actual Rust engine.
use serde_json::{Value, json};
use stateless::{
    execution::{
        self, ReplayActual, ReplayDifferences, ReplayObservation, ReplayOptions, ReplayOutcome,
        ReplayReport,
    },
    model::*,
    trace::{ReadLimits, RunConfig, Termination, Trace, TraceStep},
};
use std::{
    cell::Cell,
    io::{Read, Write},
    path::Path,
};
type Result<T> = std::result::Result<T, String>;
type R<T> = std::result::Result<T, ModelError>;
fn require(b: bool, e: &str) -> Result<()> {
    if b { Ok(()) } else { Err(e.into()) }
}
fn text<'a>(v: &'a Value, k: &str) -> Result<&'a str> {
    v[k].as_str().ok_or_else(|| format!("missing {k}"))
}
fn num(v: &Value, k: &str) -> Result<u64> {
    v[k].as_u64().ok_or_else(|| format!("missing {k}"))
}
fn list<'a>(v: &'a Value, k: &str) -> Result<&'a [Value]> {
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
fn trace_json(t: &Trace) -> Value {
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
fn parse_trace(v: &Value) -> Result<Trace> {
    require(
        v["format"] == "stateless.trace-json" && v["version"] == 1,
        "invalid portable trace",
    )?;
    let m = &v["metadata"];
    let mut steps = Vec::new();
    for s in list(v, "steps")? {
        require(
            s["disposition"]["kind"] == "accepted"
                && s["disposition"]["reason"].as_str().unwrap_or("").is_empty(),
            "application requires accepted disposition",
        )?;
        steps.push(TraceStep {
            input: unhex(&s["input"])?,
            disposition: Disposition::Accepted,
            outputs: list(s, "outputs")?
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
            model_version: u32::try_from(num(m, "model_version")?).map_err(|e| e.to_string())?,
            properties_version: u32::try_from(num(m, "properties_version")?)
                .map_err(|e| e.to_string())?,
            codec_version: u32::try_from(num(m, "codec_version")?).map_err(|e| e.to_string())?,
            build: text(m, "build")?.into(),
        },
        config: RunConfig::default(),
        initial_state: unhex(&v["initial_state"])?,
        initial_checks: parse_checks(&v["initial_checks"])?,
        steps,
        termination: Termination::PropertyFailed,
    })
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
#[derive(Default)]
struct Application {
    legacy: Cell<usize>,
    into: Cell<usize>,
    decode: Cell<usize>,
    steps: Cell<usize>,
    oversize: bool,
}
impl Application {
    fn legacy<T>(&self) -> R<T> {
        self.legacy.set(self.legacy.get() + 1);
        Err(ModelError::new("legacy encoder called"))
    }
    fn encode(&self, s: &str, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.into.set(self.into.get() + 1);
        for b in s.as_bytes() {
            o.extend_from_slice(&[*b])?;
        }
        Ok(())
    }
    fn decode(&self, b: &[u8]) -> R<i32> {
        self.decode.set(self.decode.get() + 1);
        std::str::from_utf8(b)
            .map_err(|e| ModelError::new(e.to_string()))?
            .parse()
            .map_err(|_| ModelError::new("invalid integer"))
    }
}
impl Model for Application {
    type State = i32;
    type Input = i32;
    type Output = String;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "native-codec-replay-threshold".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "application-v1".into(),
        }
    }
    fn initial_state(&self) -> R<i32> {
        Ok(0)
    }
    fn step(&self, s: &i32, i: &i32) -> R<Transition<i32, String>> {
        self.steps.set(self.steps.get() + 1);
        if ![0, 1].contains(i) {
            return Err(ModelError::new("expected zero or one"));
        }
        Ok(Transition::accepted(
            s + i,
            if *i == 1 {
                vec![if self.oversize {
                    "x".repeat(65)
                } else {
                    "changed".into()
                }]
            } else {
                vec![]
            },
        ))
    }
    fn check_state(&self, s: &i32) -> R<Vec<Check>> {
        Ok(vec![if *s >= 3 {
            Check::failed("threshold.bound", "at least three")
        } else {
            Check::passed("threshold.bound")
        }])
    }
}
impl ModelCodec for Application {
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
    fn encode_output_into(&self, n: &String, o: &mut EncodeBuffer<'_>) -> R<()> {
        self.encode(n, o)
    }
    fn decode_state(&self, b: &[u8]) -> R<i32> {
        self.decode(b)
    }
    fn decode_input(&self, b: &[u8]) -> R<i32> {
        let n = self.decode(b)?;
        if ![0, 1].contains(&n) {
            return Err(ModelError::new("expected zero or one"));
        }
        Ok(n)
    }
}
fn limits() -> ReadLimits {
    ReadLimits {
        max_total_bytes: 4096,
        max_frame_bytes: 1024,
        max_blob_bytes: 64,
        max_string_bytes: 128,
        max_steps: 16,
        max_checks_per_step: 16,
        max_outputs_per_step: 16,
        max_parameters: 16,
        max_items: 128,
    }
}
fn config_value(c: &RunConfig) -> Value {
    json!({"strategy":c.strategy,"seed":c.seed.map(|n|n.to_string()),"parameters":c.parameters})
}
fn session(model: &Application, t: &Trace, bounded: bool) -> Result<Value> {
    let mut observations = vec![];
    let mut observer = |o: ReplayObservation<'_, Application>, r: &ReplayReport| -> R<()> {
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
            &limits(),
            &mut observer,
        )
    } else {
        execution::replay_with_observations(model, t, ReplayOptions::default(), &mut observer)
    }
    .map_err(|e| e.to_string())?;
    Ok(json!({"report":report_value(&report),"observations":observations}))
}
fn controls(trace: &Trace) -> Result<Value> {
    let mut storage = b"stale".to_vec();
    let cleared = {
        let s = EncodeBuffer::new(&mut storage, 32);
        s.finish().map_err(|e| e.to_string())?;
        drop(s);
        storage.is_empty()
    };
    require(cleared, "entry not cleared")?;
    let mut b = vec![];
    let mut sink = EncodeBuffer::new(&mut b, 32);
    sink.framed(|child| child.framed(|grandchild| grandchild.extend_from_slice(b"ab")))
        .map_err(|e| e.to_string())?;
    sink.extend_from_slice(b"z").map_err(|e| e.to_string())?;
    sink.finish().map_err(|e| e.to_string())?;
    drop(sink);
    let mut wanted = 10u64.to_le_bytes().to_vec();
    wanted.extend(2u64.to_le_bytes());
    wanted.extend(b"abz");
    require(b == wanted, "nested frame bytes")?;
    let mut b = vec![];
    let mut sink = EncodeBuffer::new(&mut b, 1);
    require(
        sink.extend_from_slice(b"ab").is_err()
            && sink.extend_from_slice(b"").is_err()
            && sink.finish().is_err(),
        "sticky failure lost",
    )?;
    let mut b = vec![];
    let mut sink = EncodeBuffer::new(&mut b, 7);
    let mut called = false;
    require(
        sink.framed(|_| {
            called = true;
            Ok(())
        })
        .is_err()
            && !called,
        "prefix invoked callback",
    )?;
    let mut b = vec![];
    let mut sink = EncodeBuffer::new(&mut b, 9);
    require(
        sink.framed(|c| c.extend_from_slice(b"ab")).is_err() && sink.finish().is_err(),
        "frame body gained budget",
    )?;
    let preflight = Application::default();
    let mut l = limits();
    l.max_steps = 2;
    require(
        execution::replay_with_observations_bounded(
            &preflight,
            trace,
            ReplayOptions::default(),
            &l,
            |_, _| Ok(()),
        )
        .is_err()
            && preflight.decode.get() == 0,
        "preflight decoded or succeeded",
    )?;
    let over = Application {
        oversize: true,
        ..Default::default()
    };
    let error = execution::replay_with_observations_bounded(
        &over,
        trace,
        ReplayOptions::default(),
        &limits(),
        |_, _| Ok(()),
    )
    .err()
    .ok_or("oversize output accepted")?;
    let retained = match error.actual.as_deref() {
        Some(ReplayActual::Turn {
            sequence,
            before,
            input,
            transition,
            checks,
            state_check_count,
        }) => {
            *sequence == 1
                && *before == 0
                && *input == 1
                && transition.state == 1
                && transition.outputs == vec!["x".repeat(65)]
                && checks
                    .as_ref()
                    .is_some_and(|c| c == &vec![Check::passed("threshold.bound")])
                && *state_check_count == Some(1)
        }
        _ => false,
    };
    require(retained && over.steps.get() == 1, "oversize actual lost")?;
    let mut changed = trace.clone();
    let last = changed.steps.last_mut().ok_or("missing failing step")?;
    let check = last.checks.first_mut().ok_or("missing failing check")?;
    check.status = CheckStatus::Failed("different expected detail".into());
    let divergent = Application::default();
    let varied = session(&divergent, &changed, false)?;
    let r = &varied["report"];
    require(
        r["outcome"] == "diverged"
            && r["steps_verified"] == 2
            && r["divergence_step"] == 3
            && r["divergence_field"] == "checks"
            && r["failure_reproduced"] == true,
        "rich divergence mismatch",
    )?;
    Ok(
        json!({"clear_entry":true,"sticky_finish":true,"framed_shared_budget":true,"nested_framing":true,"preflight_decode_calls":preflight.decode.get(),"oversize_reducer_calls":over.steps.get(),"oversize_actual_retained":retained,"legacy_encoder_calls":preflight.legacy.get()+over.legacy.get()+divergent.legacy.get(),"codec_into_calls":preflight.into.get()+over.into.get()+divergent.into.get(),"rich_divergence":true,"static_borrow":true}),
    )
}
fn receipt(trace: &Trace) -> Result<Value> {
    let rich_model = Application::default();
    let bounded_model = Application::default();
    Ok(
        json!({"rich":session(&rich_model,trace,false)?,"bounded":session(&bounded_model,trace,true)?,"retained_config":config_value(&trace.config),"legacy_encoder_calls":rich_model.legacy.get()+bounded_model.legacy.get(),"codec_into_calls":rich_model.into.get()+bounded_model.into.get(),"producer":"rust"}),
    )
}
fn write_new(path: &str, b: &[u8]) -> Result<()> {
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    f.write_all(b).map_err(|e| e.to_string())
}
pub fn record(prefix: &str) -> Result<Value> {
    let model = Application::default();
    let t = execution::record_with_limits(
        &model,
        [1, 1, 1],
        RunConfig {
            strategy: "manual".into(),
            seed: None,
            parameters: vec![],
        },
        16,
        &limits(),
    )
    .map_err(|e| e.to_string())?;
    require(
        t.termination == Termination::PropertyFailed && t.steps.len() == 3,
        "missing threshold witness",
    )?;
    let mut wire = vec![];
    t.write_with_limits(&mut wire, &limits())
        .map_err(|e| e.to_string())?;
    let portable = serde_json::to_vec(&trace_json(&t)).map_err(|e| e.to_string())?;
    write_new(&format!("{prefix}.bin"), &wire)?;
    write_new(&format!("{prefix}.json"), &portable)?;
    let mut value = receipt(&t)?;
    value["controls"] = controls(&t)?;
    value["legacy_encoder_calls"] =
        json!(value["legacy_encoder_calls"].as_u64().unwrap() + model.legacy.get() as u64);
    value["codec_into_calls"] =
        json!(value["codec_into_calls"].as_u64().unwrap() + model.into.get() as u64);
    value["mode"] = json!("record");
    value["steps"] = json!(3);
    value["termination"] = json!("property_failed");
    Ok(value)
}
pub fn replay(path: &Path) -> Result<Value> {
    let f = std::fs::File::open(path).map_err(|e| e.to_string())?;
    let t = Trace::read_from(f, &limits()).map_err(|e| e.to_string())?;
    let mut value = receipt(&t)?;
    value["mode"] = json!("replay");
    Ok(value)
}
pub fn replay_json(path: &Path) -> Result<Value> {
    let mut bytes = vec![];
    std::fs::File::open(path)
        .map_err(|e| e.to_string())?
        .take(4097)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    require(bytes.len() <= 4096, "JSON too large")?;
    let v: Value = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    let trace = parse_trace(&v)?;
    let model = Application::default();
    let report =
        execution::replay(&model, &trace, ReplayOptions::default()).map_err(|e| e.to_string())?;
    let mut v = report_value(&report);
    v["legacy_encoder_calls"] = json!(model.legacy.get());
    v["codec_into_calls"] = json!(model.into.get());
    v["producer"] = json!("rust");
    v["mode"] = json!("replay-json");
    Ok(v)
}

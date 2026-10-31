//! Real public Rust BFS qualification; JSON dependencies remain isolated here.
use serde_json::{Value, json};
use stateless::execution::{ReplayOptions, ReplayOutcome, record, replay};
use stateless::explore::*;
use stateless::model::*;
use stateless::trace::{RunConfig, Termination, Trace, TraceStep};
use std::{
    cell::RefCell,
    hash::{Hash, Hasher},
    io::{Read, Write},
    path::Path,
    rc::Rc,
    sync::atomic::{AtomicBool, Ordering},
    time::Duration,
};
type Result<T> = std::result::Result<T, String>;
fn require(ok: bool, msg: impl Into<String>) -> Result<()> {
    if ok { Ok(()) } else { Err(msg.into()) }
}
fn text<'a>(v: &'a Value, key: &str) -> Result<&'a str> {
    v[key]
        .as_str()
        .ok_or_else(|| format!("missing string {key}"))
}
fn num(v: &Value, key: &str) -> Result<u64> {
    v[key]
        .as_u64()
        .ok_or_else(|| format!("missing integer {key}"))
}
fn list<'a>(v: &'a Value, key: &str) -> Result<&'a [Value]> {
    v[key]
        .as_array()
        .map(Vec::as_slice)
        .ok_or_else(|| format!("missing array {key}"))
}
fn json_file(path: &Path) -> Result<Value> {
    let mut b = Vec::new();
    std::fs::File::open(path)
        .map_err(|e| e.to_string())?
        .take(4194305)
        .read_to_end(&mut b)
        .map_err(|e| e.to_string())?;
    require(b.len() <= 4194304, "qualification JSON byte limit")?;
    serde_json::from_slice(&b).map_err(|e| e.to_string())
}
fn optional_usize(v: &Value) -> Result<Option<usize>> {
    if v.is_null() {
        return Ok(None);
    };
    let n = v
        .as_str()
        .ok_or("estimate must be decimal string")?
        .parse::<u64>()
        .map_err(|e| e.to_string())?;
    Ok(Some(
        usize::try_from(n).map_err(|_| "estimate exceeds platform usize")?,
    ))
}
struct Environment {
    events: RefCell<Vec<String>>,
    cancel: AtomicBool,
    cancel_at: Option<String>,
    error_at: Option<String>,
}
impl Environment {
    fn event(&self, event: String) {
        self.events.borrow_mut().push(event.clone());
        if self.cancel_at.as_ref() == Some(&event) {
            self.cancel.store(true, Ordering::Relaxed);
        }
    }
}
#[derive(Clone)]
struct State {
    n: i32,
    env: Rc<Environment>,
}
impl PartialEq for State {
    fn eq(&self, other: &Self) -> bool {
        self.env.event(format!("equal:{}:{}", self.n, other.n));
        self.n == other.n
    }
}
impl Eq for State {}
impl Hash for State {
    fn hash<H: Hasher>(&self, h: &mut H) {
        self.env.event(format!("hash:{}", self.n));
        0u8.hash(h);
    }
}
struct Fixture {
    env: Rc<Environment>,
    kind: String,
    estimates: Vec<Option<usize>>,
    fail_initial: bool,
    fail_state: Option<i32>,
    skipped: bool,
}
impl Model for Fixture {
    type State = State;
    type Input = i32;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "bfs-controls-fixture".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "independent-v1".into(),
        }
    }
    fn initial_state(&self) -> std::result::Result<State, ModelError> {
        self.env.event("initial".into());
        Ok(State {
            n: 0,
            env: Rc::clone(&self.env),
        })
    }
    fn step(
        &self,
        before: &State,
        input: &i32,
    ) -> std::result::Result<Transition<State, ()>, ModelError> {
        self.env.event(format!("step:{}:{}", before.n, input));
        let n = match self.kind.as_str() {
            "loop" => 0,
            "diamond" => *input,
            _ => before.n + input,
        };
        Ok(Transition::accepted(
            State {
                n,
                env: Rc::clone(&self.env),
            },
            vec![],
        ))
    }
    fn check_state(&self, state: &State) -> std::result::Result<Vec<Check>, ModelError> {
        let mut checks = Vec::new();
        self.check_state_into(state, &mut CheckSink::new(&mut checks))?;
        Ok(checks)
    }
    fn check_state_into(
        &self,
        state: &State,
        sink: &mut CheckSink<'_>,
    ) -> std::result::Result<(), ModelError> {
        let e = format!("state:{}", state.n);
        self.env.event(e.clone());
        if self.env.error_at.as_ref() == Some(&e) {
            return Err(ModelError::new("fixture check error"));
        }
        sink.push(Check::passed("ok"));
        if self.skipped {
            sink.push(Check::skipped("skip", "not run"));
        }
        if (state.n == 0 && self.fail_initial) || self.fail_state == Some(state.n) {
            sink.push(Check::failed("bad", "broken"));
        }
        Ok(())
    }
    fn check_transition_into(
        &self,
        before: &State,
        input: &i32,
        after: &TransitionRef<'_, State, ()>,
        _sink: &mut CheckSink<'_>,
    ) -> std::result::Result<(), ModelError> {
        let e = format!("transition:{}:{}:{}", before.n, input, after.state.n);
        self.env.event(e.clone());
        if self.env.error_at.as_ref() == Some(&e) {
            return Err(ModelError::new("fixture check error"));
        }
        Ok(())
    }
    fn estimated_state_bytes(&self, state: &State) -> Option<usize> {
        self.env.event(format!("estimate:{}", state.n));
        self.estimates[state.n as usize]
    }
}
struct Cursor {
    env: Rc<Environment>,
    state: i32,
    values: std::vec::IntoIter<i32>,
}
impl Iterator for Cursor {
    type Item = i32;
    fn next(&mut self) -> Option<i32> {
        let n = self.values.next();
        self.env.event(format!(
            "next:{}:{}",
            self.state,
            n.map(|n| n.to_string()).unwrap_or("end".into())
        ));
        n
    }
}
impl Enumerate for Fixture {
    fn inputs(&self, state: &State) -> std::result::Result<Vec<i32>, ModelError> {
        Ok(match self.kind.as_str() {
            "empty" => vec![],
            "loop" => vec![0, 0],
            "diamond" => match state.n {
                0 => vec![1, 2, 1],
                1 | 2 => vec![3],
                _ => vec![],
            },
            _ => {
                if state.n < 2 {
                    vec![1]
                } else {
                    vec![]
                }
            }
        })
    }
    fn input_iter<'a>(
        &'a self,
        state: &'a State,
    ) -> std::result::Result<Box<dyn Iterator<Item = i32> + 'a>, ModelError> {
        self.env.event(format!("inputs:{}", state.n));
        Ok(Box::new(Cursor {
            env: Rc::clone(&self.env),
            state: state.n,
            values: self.inputs(state)?.into_iter(),
        }))
    }
}
struct Observer {
    env: Rc<Environment>,
    history: bool,
}
impl SearchObserver<Fixture> for Observer {
    fn needs_history(&self) -> bool {
        self.history
    }
    fn initial(&mut self, state: &State, _checks: &[Check]) {
        self.env.event(format!("observe_initial:{}", state.n));
    }
    fn check_error(&mut self, phase: CheckPhase, checks: &[Check], complete: bool) {
        self.env.event(format!(
            "check_error:{}:{}:{}",
            phase_name(phase),
            complete,
            checks.len()
        ));
    }
    fn transition(
        &mut self,
        before: &State,
        input: &i32,
        after: &TransitionRef<'_, State, ()>,
        _state: &[Check],
        _edge: &[Check],
        history: &[i32],
    ) {
        let h = if history.is_empty() {
            "-".into()
        } else {
            history
                .iter()
                .map(|i| i.to_string())
                .collect::<Vec<_>>()
                .join(",")
        };
        self.env.event(format!(
            "observe:{}:{}:{}:{}",
            before.n, input, after.state.n, h
        ));
    }
}
fn phase_name(p: CheckPhase) -> &'static str {
    match p {
        CheckPhase::InitialState => "initial_state",
        CheckPhase::State => "state",
        CheckPhase::Transition => "transition",
    }
}
fn term_name(t: SearchTermination) -> &'static str {
    match t {
        SearchTermination::GraphExhausted => "graph_exhausted",
        SearchTermination::DepthBound => "depth_bound",
        SearchTermination::StateLimit => "state_limit",
        SearchTermination::TransitionLimit => "transition_limit",
        SearchTermination::FailureFound => "failure_found",
        SearchTermination::EstimatedStateByteLimit => "estimated_state_byte_limit",
        SearchTermination::Deadline => "deadline",
        SearchTermination::Cancelled => "cancelled",
    }
}
fn report_value(r: &SearchReport<i32>) -> Value {
    let target = r.failure.as_ref().and_then(|f| f.violations.first());
    json!({"termination":term_name(r.termination),"states":r.states,"transitions":r.transitions,"max_depth_reached":r.max_depth_reached,"skipped_checks":r.skipped_checks,"estimated_retained_state_bytes":r.estimated_retained_state_bytes.map(|n|n.to_string()),"witness":r.failure.as_ref().map(|f|&f.inputs),"failed_phase":target.map(|v|phase_name(v.phase)),"failed_id":target.map(|v|v.check.id.as_str())})
}
fn corpus(path: &Path) -> Result<Value> {
    let data = json_file(path)?;
    require(data["profile"] == "bfs-controls-v1", "wrong profile")?;
    let mut checked = 0;
    let mut errors = 0;
    let mut skipped = 0;
    for row in list(&data, "cases")? {
        let name = text(row, "case")?;
        if row["requires_64bit"] == true && usize::BITS < 64 {
            skipped += 1;
            continue;
        }
        let entry = text(row, "entry_control")?;
        let env = Rc::new(Environment {
            events: RefCell::new(vec![]),
            cancel: AtomicBool::new(matches!(entry, "cancelled" | "both")),
            cancel_at: row["cancel_at"].as_str().map(str::to_owned),
            error_at: row["error_at"].as_str().map(str::to_owned),
        });
        let model = Fixture {
            env: Rc::clone(&env),
            kind: text(row, "fixture")?.into(),
            estimates: list(row, "estimates")?
                .iter()
                .map(optional_usize)
                .collect::<Result<Vec<_>>>()?,
            fail_initial: row["fail_initial"] == true,
            fail_state: row["fail_state"].as_i64().map(|n| n as i32),
            skipped: row["skipped"] == true,
        };
        let mut observer = Observer {
            env: Rc::clone(&env),
            history: row["needs_history"] == true,
        };
        let config = SearchConfig {
            max_states: usize::try_from(num(row, "max_states")?).map_err(|e| e.to_string())?,
            max_transitions: num(row, "max_transitions")?,
            max_depth: usize::try_from(num(row, "max_depth")?).map_err(|e| e.to_string())?,
        };
        let limits = SearchLimits {
            control: RunLimits {
                max_duration: matches!(entry, "deadline" | "both").then_some(Duration::ZERO),
                cancellation: Some(&env.cancel),
            },
            max_estimated_state_bytes: optional_usize(&row["max_estimated_state_bytes"])?,
        };
        let result = enumerate_observed(&model, config, limits, &mut observer);
        if let Some(message) = row["error_contains"].as_str() {
            let error = result
                .err()
                .ok_or_else(|| format!("{name}: expected error"))?;
            require(
                error.to_string().contains(message),
                format!("{name}: wrong error {error}"),
            )?;
            errors += 1;
        } else {
            let report = result.map_err(|e| format!("{name}: {e}"))?;
            require(
                report_value(&report) == row["expected"],
                format!(
                    "{name}: report {} expected {}",
                    report_value(&report),
                    row["expected"]
                ),
            )?;
        }
        require(
            json!(*env.events.borrow()) == row["events"],
            format!(
                "{name}: events {} expected {}",
                json!(*env.events.borrow()),
                row["events"]
            ),
        )?;
        checked += 1;
    }
    Ok(
        json!({"producer":"rust","profile":"bfs-controls-v1","cases":checked,"errors":errors,"unsupported_pointer_width_cases":skipped,"outcome":"passed"}),
    )
}

struct Application {
    calls: RefCell<usize>,
}
impl Application {
    fn new() -> Self {
        Self {
            calls: RefCell::new(0),
        }
    }
    fn called(&self) {
        *self.calls.borrow_mut() += 1;
    }
}
impl Model for Application {
    type State = i32;
    type Input = i32;
    type Output = String;
    fn metadata(&self) -> ModelMetadata {
        self.called();
        ModelMetadata {
            name: "native-bfs-threshold".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "application-v1".into(),
        }
    }
    fn initial_state(&self) -> std::result::Result<i32, ModelError> {
        self.called();
        Ok(0)
    }
    fn step(&self, s: &i32, i: &i32) -> std::result::Result<Transition<i32, String>, ModelError> {
        self.called();
        if !matches!(i, 0 | 1) {
            return Err(ModelError::new("expected zero or one"));
        }
        Ok(Transition::accepted(
            s + i,
            if *i == 1 {
                vec!["changed".into()]
            } else {
                vec![]
            },
        ))
    }
    fn check_state(&self, s: &i32) -> std::result::Result<Vec<Check>, ModelError> {
        self.called();
        Ok(vec![if *s >= 3 {
            Check::failed("threshold.bound", "at least three")
        } else {
            Check::passed("threshold.bound")
        }])
    }
    fn estimated_state_bytes(&self, _: &i32) -> Option<usize> {
        self.called();
        Some(5)
    }
}
impl Enumerate for Application {
    fn inputs(&self, _: &i32) -> std::result::Result<Vec<i32>, ModelError> {
        self.called();
        Ok(vec![0, 1])
    }
}
impl ModelCodec for Application {
    fn encode_state(&self, s: &i32) -> std::result::Result<Vec<u8>, ModelError> {
        Ok(s.to_string().into_bytes())
    }
    fn decode_state(&self, b: &[u8]) -> std::result::Result<i32, ModelError> {
        std::str::from_utf8(b)
            .map_err(|e| ModelError::new(e.to_string()))?
            .parse()
            .map_err(|e: std::num::ParseIntError| ModelError::new(e.to_string()))
    }
    fn encode_input(&self, i: &i32) -> std::result::Result<Vec<u8>, ModelError> {
        Ok(i.to_string().into_bytes())
    }
    fn decode_input(&self, b: &[u8]) -> std::result::Result<i32, ModelError> {
        match b {
            b"0" => Ok(0),
            b"1" => Ok(1),
            _ => Err(ModelError::new("expected zero or one")),
        }
    }
    fn encode_output(&self, o: &String) -> std::result::Result<Vec<u8>, ModelError> {
        Ok(o.as_bytes().to_vec())
    }
}
#[derive(Default)]
struct AppObserver {
    histories: Vec<Vec<i32>>,
    initial: usize,
    errors: usize,
}
impl SearchObserver<Application> for AppObserver {
    fn needs_history(&self) -> bool {
        true
    }
    fn initial(&mut self, _: &i32, _: &[Check]) {
        self.initial += 1;
    }
    fn transition(
        &mut self,
        _: &i32,
        _: &i32,
        _: &TransitionRef<'_, i32, String>,
        _: &[Check],
        _: &[Check],
        history: &[i32],
    ) {
        self.histories.push(history.to_vec());
    }
    fn check_error(&mut self, _: CheckPhase, _: &[Check], _: bool) {
        self.errors += 1;
    }
}
fn app_run(cap: Option<usize>, cancelled: bool) -> Result<(Value, Option<Failure<i32>>)> {
    let model = Application::new();
    let flag = AtomicBool::new(cancelled);
    let mut observer = AppObserver::default();
    let r = enumerate_observed(
        &model,
        SearchConfig {
            max_states: 100,
            max_transitions: 100,
            max_depth: 10,
        },
        SearchLimits {
            control: RunLimits {
                max_duration: None,
                cancellation: Some(&flag),
            },
            max_estimated_state_bytes: cap,
        },
        &mut observer,
    )
    .map_err(|e| e.to_string())?;
    let target = r.failure.as_ref().and_then(|f| f.violations.first());
    let value = json!({"termination":term_name(r.termination),"states":r.states,"transitions":r.transitions,"estimated_retained_state_bytes":r.estimated_retained_state_bytes,"witness":r.failure.as_ref().map(|f|&f.inputs),"failure_phase":target.map(|v|phase_name(v.phase)),"failure_property":target.map(|v|v.check.id.as_str()),"observed_histories":observer.histories,"initial_observations":observer.initial,"check_errors":observer.errors,"model_callbacks":*model.calls.borrow(),"observer_callbacks":observer.initial+observer.histories.len()+observer.errors});
    Ok((value, r.failure))
}
fn hex(b: &[u8]) -> String {
    b.iter().map(|n| format!("{n:02x}")).collect()
}
fn unhex(v: &Value) -> Result<Vec<u8>> {
    let s = v.as_str().ok_or("hex must be string")?;
    require(
        s.len() <= 8388608
            && s.len().is_multiple_of(2)
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
fn run() -> Result<Value> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let default =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../conformance/bfs-controls-v1.json");
    match args.as_slice() {
        [] => corpus(&default),
        [m, p] if m == "--corpus" => corpus(Path::new(p)),
        [m, p] if m == "record" => {
            let (base, failure) = app_run(None, false)?;
            let (cap, _) = app_run(Some(14), false)?;
            let (precancel, _) = app_run(None, true)?;
            let model = Application::new();
            let trace = record(
                &model,
                failure.ok_or("application failure missing")?.inputs,
                RunConfig::default(),
                100000,
            )
            .map_err(|e| e.to_string())?;
            let bytes = serde_json::to_vec(&trace_json(&trace)).map_err(|e| e.to_string())?;
            let mut output = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(p)
                .map_err(|e| e.to_string())?;
            output.write_all(&bytes).map_err(|e| e.to_string())?;
            Ok(
                json!({"producer":"rust","mode":"record","base":base,"cap":cap,"precancel":precancel,"audit_config":{"strategy":trace.config.strategy,"seed":trace.config.seed.map(|n|n.to_string()),"parameters":trace.config.parameters}}),
            )
        }
        [m, p] if m == "replay" => {
            let trace = parse_trace(&json_file(Path::new(p))?)?;
            let r = replay(&Application::new(), &trace, ReplayOptions::default())
                .map_err(|e| e.to_string())?;
            let outcome = match r.outcome {
                ReplayOutcome::Exact => "exact",
                ReplayOutcome::Diverged { .. } => "diverged",
                ReplayOutcome::Incompatible { .. } => "incompatible",
            };
            Ok(
                json!({"producer":"rust","mode":"replay","outcome":outcome,"failure_reproduced":r.failure_reproduced,"steps":r.steps_verified,"build_matches":r.build_matches}),
            )
        }
        _ => Err("usage: native-bfs [--corpus PATH | record OUTPUT | replay INPUT]".into()),
    }
}
fn main() {
    match run() {
        Ok(v) => println!("{v}"),
        Err(e) => {
            eprintln!("native-bfs: {e}");
            std::process::exit(1)
        }
    }
}

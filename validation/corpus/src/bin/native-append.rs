//! Real Rust append-check dispatch and error-boundary qualification.
use serde_json::{Value, json};
use stateless::{
    conformance::{Paired, Projection},
    execution::{self, CheckPolicy, ReplayOptions, ReplayOutcome},
    explore::*,
    model::*,
    oracle::{Oracle, OracleState, WithOracle},
    trace::{RunConfig, Termination, Trace, TraceStep},
};
use std::{
    cell::{Cell, RefCell},
    io::{Read, Write},
    num::NonZeroU64,
    path::Path,
    rc::Rc,
};
type Result<T> = std::result::Result<T, String>;
fn require(ok: bool, msg: impl Into<String>) -> Result<()> {
    if ok { Ok(()) } else { Err(msg.into()) }
}
fn text<'a>(v: &'a Value, k: &str) -> Result<&'a str> {
    v[k].as_str().ok_or_else(|| format!("missing string {k}"))
}
fn number(v: &Value, k: &str) -> Result<u64> {
    v[k].as_u64().ok_or_else(|| format!("missing integer {k}"))
}
fn list<'a>(v: &'a Value, k: &str) -> Result<&'a [Value]> {
    v[k].as_array()
        .map(Vec::as_slice)
        .ok_or_else(|| format!("missing array {k}"))
}
fn json_file(path: &Path) -> Result<Value> {
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .map_err(|e| e.to_string())?
        .take(4194305)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    require(bytes.len() <= 4194304, "qualification JSON byte limit")?;
    serde_json::from_slice(&bytes).map_err(|e| e.to_string())
}
fn metadata(name: &str) -> ModelMetadata {
    ModelMetadata {
        name: name.into(),
        model_version: 1,
        properties_version: 1,
        codec_version: 1,
        build: "candidate-v1".into(),
    }
}
fn state_checks(n: i32, fail: Option<i32>) -> Vec<Check> {
    let mut c = vec![Check::passed("same"), Check::skipped("skip", "not run")];
    if fail == Some(n) {
        c.push(Check::failed("bad", "broken"));
    }
    c
}
fn check_json(c: &Check) -> Value {
    let (status, details) = match &c.status {
        CheckStatus::Passed => ("passed", ""),
        CheckStatus::Failed(s) => ("failed", s.as_str()),
        CheckStatus::Skipped(s) => ("skipped", s.as_str()),
    };
    json!({"id":c.id.as_str(),"status":status,"details":details})
}
fn batch_json(c: &[Check]) -> Value {
    json!(c.iter().map(check_json).collect::<Vec<_>>())
}
fn expected(
    outcome: &str,
    checks: Option<&[Check]>,
    observations: Vec<Value>,
    witness: Option<Vec<i32>>,
    attempts: Option<usize>,
) -> Value {
    json!({"outcome":outcome,"checks":checks.map(batch_json),"observations":observations,"witness":witness,"attempts":attempts})
}
struct Data {
    events: Rc<RefCell<Vec<String>>>,
    observations: RefCell<Vec<Value>>,
    error_at: Option<String>,
    fail: Option<i32>,
    label: String,
    append: bool,
    wrapper_error: Option<String>,
}
impl Data {
    fn event(&self, e: String) {
        self.events.borrow_mut().push(e);
    }
    fn initial(&self) -> std::result::Result<i32, ModelError> {
        self.event(format!("{}initial", self.label));
        Ok(0)
    }
    fn step(&self, b: &i32, i: &i32) -> std::result::Result<Transition<i32, ()>, ModelError> {
        self.event(format!("{}step:{b}:{i}", self.label));
        Ok(Transition::accepted(b + i, vec![]))
    }
    fn legacy(&self, stage: &str, n: i32) -> std::result::Result<Vec<Check>, ModelError> {
        self.event(format!("{}legacy_{stage}:{n}", self.label));
        if self.append {
            return Err(ModelError::new("legacy called despite append hook"));
        }
        if self.error_at.as_deref() == Some(format!("{stage}:{n}").as_str()) {
            return Err(ModelError::new("fixture append error"));
        }
        Ok(if stage == "state" {
            state_checks(n, self.fail)
        } else {
            vec![Check::passed("same")]
        })
    }
    fn append(
        &self,
        stage: &str,
        n: i32,
        c: &mut CheckSink<'_>,
    ) -> std::result::Result<(), ModelError> {
        self.event(format!("{}append_{stage}:{n}:{}", self.label, c.len()));
        let prefix = c.as_slice().to_vec();
        let length = c.len();
        c.reserve(2);
        assert_eq!(length, c.len());
        assert_eq!(prefix, c.as_slice());
        c.push(Check::passed("same"));
        assert_eq!(c.len(), length + 1);
        assert_eq!(c.as_slice().last().unwrap().id.as_str(), "same");
        if self.error_at.as_deref() == Some(format!("{stage}:{n}").as_str()) {
            return Err(ModelError::new("fixture append error"));
        }
        if stage == "state" {
            c.extend(state_checks(n, self.fail).into_iter().skip(1));
        }
        Ok(())
    }
}
struct Legacy(Rc<Data>);
struct Append(Rc<Data>);
macro_rules! model_impl{($name:ident $(,$extra:item)*)=>{impl Model for $name{type State=i32;type Input=i32;type Output=();fn metadata(&self)->ModelMetadata{metadata("append-fixture")}fn initial_state(&self)->std::result::Result<i32,ModelError>{self.0.initial()}fn step(&self,b:&i32,i:&i32)->std::result::Result<Transition<i32,()>,ModelError>{self.0.step(b,i)}fn check_state(&self,n:&i32)->std::result::Result<Vec<Check>,ModelError>{self.0.legacy("state",*n)}fn check_transition(&self,_:&i32,_:&i32,t:&TransitionRef<'_,i32,()>)->std::result::Result<Vec<Check>,ModelError>{self.0.legacy("transition",*t.state)}$($extra)*}impl Enumerate for $name{fn inputs(&self,n:&i32)->std::result::Result<Vec<i32>,ModelError>{Ok(if *n==0{vec![1]}else{vec![]})}}impl Generate for $name{fn generate(&self,_:&i32,_:&mut Rng)->std::result::Result<Option<i32>,ModelError>{Ok(Some(1))}}impl ModelCodec for $name{fn encode_state(&self,n:&i32)->std::result::Result<Vec<u8>,ModelError>{Ok(n.to_string().into_bytes())}fn decode_state(&self,b:&[u8])->std::result::Result<i32,ModelError>{decode_integer(b)}fn encode_input(&self,n:&i32)->std::result::Result<Vec<u8>,ModelError>{Ok(n.to_string().into_bytes())}fn decode_input(&self,b:&[u8])->std::result::Result<i32,ModelError>{decode_integer(b)}fn encode_output(&self,_:&())->std::result::Result<Vec<u8>,ModelError>{Ok(vec![])}}};}
model_impl!(Legacy);
model_impl!(
    Append,
    fn check_state_into(
        &self,
        n: &i32,
        c: &mut CheckSink<'_>,
    ) -> std::result::Result<(), ModelError> {
        self.0.append("state", *n, c)
    },
    fn check_transition_into(
        &self,
        _: &i32,
        _: &i32,
        t: &TransitionRef<'_, i32, ()>,
        c: &mut CheckSink<'_>,
    ) -> std::result::Result<(), ModelError> {
        self.0.append("transition", *t.state, c)
    }
);
fn decode_integer(b: &[u8]) -> std::result::Result<i32, ModelError> {
    std::str::from_utf8(b)
        .map_err(|e| ModelError::new(e.to_string()))?
        .parse()
        .map_err(|e: std::num::ParseIntError| ModelError::new(e.to_string()))
}
struct Observer(Rc<Data>);
fn phase(p: CheckPhase) -> &'static str {
    match p {
        CheckPhase::InitialState => "initial_state",
        CheckPhase::State => "state",
        CheckPhase::Transition => "transition",
    }
}
impl Observer {
    fn batch(&self, p: &str, c: &[Check], complete: bool) {
        self.0
            .observations
            .borrow_mut()
            .push(json!({"phase":p,"complete":complete,"checks":batch_json(c)}));
    }
}
impl<M: Model<State = i32, Input = i32, Output = ()>> SearchObserver<M> for Observer {
    fn initial(&mut self, _: &i32, c: &[Check]) {
        self.0.event("observe_initial".into());
        self.batch("initial_state", c, true);
    }
    fn transition(
        &mut self,
        _: &i32,
        _: &i32,
        _: &TransitionRef<'_, i32, ()>,
        s: &[Check],
        t: &[Check],
        _: &[i32],
    ) {
        self.0.event("observe_transition".into());
        self.batch("state", s, true);
        self.batch("transition", t, true);
    }
    fn check_error(&mut self, p: CheckPhase, c: &[Check], complete: bool) {
        self.0.event(format!("check_error:{}:{complete}", phase(p)));
        self.batch(phase(p), c, complete);
    }
}
fn search_name(t: SearchTermination) -> &'static str {
    match t {
        SearchTermination::GraphExhausted => "graph_exhausted",
        SearchTermination::FailureFound => "failure_found",
        _ => "unexpected",
    }
}
fn fuzz_name(t: FuzzTermination) -> &'static str {
    match t {
        FuzzTermination::CasesCompleted => "cases_completed",
        FuzzTermination::FailureFound => "failure_found",
        _ => "unexpected",
    }
}
fn fixed_trace() -> Trace {
    Trace {
        metadata: metadata("append-fixture"),
        config: RunConfig::default(),
        initial_state: b"0".to_vec(),
        initial_checks: state_checks(0, None),
        steps: vec![TraceStep {
            input: b"1".to_vec(),
            disposition: Disposition::Accepted,
            outputs: vec![],
            post_state: b"1".to_vec(),
            checks: state_checks(1, None)
                .into_iter()
                .chain([Check::passed("same")])
                .collect(),
        }],
        termination: Termination::Completed,
    }
}
fn run_model<
    M: Model<State = i32, Input = i32, Output = ()> + Enumerate + Generate + ModelCodec,
>(
    m: &M,
    d: &Rc<Data>,
    row: &Value,
) -> Result<Value> {
    let op = text(row, "operation")?;
    let mut checks = vec![Check::passed("sentinel")];
    let mut observer = Observer(Rc::clone(d));
    let mut error = None;
    let mut result = match op {
        "direct_state" => {
            if let Err(e) = m.check_state_into(&1, &mut CheckSink::new(&mut checks)) {
                error = Some(e);
            }
            expected(
                if error.is_some() { "error" } else { "ok" },
                Some(&checks),
                vec![],
                None,
                None,
            )
        }
        "direct_transition" => {
            if let Err(e) = m.check_transition_into(
                &0,
                &1,
                &Transition::accepted(1, vec![]).as_ref(),
                &mut CheckSink::new(&mut checks),
            ) {
                error = Some(e);
            }
            expected(
                if error.is_some() { "error" } else { "ok" },
                Some(&checks),
                vec![],
                None,
                None,
            )
        }
        "observed" => {
            let mut owned = Vec::with_capacity(32);
            owned.push(Check::passed("sentinel"));
            let ptr = owned.as_ptr();
            let r = execution::check_observed_into(
                m,
                &0,
                &1,
                &Transition::accepted(1, vec![]),
                number(row, "sequence")?,
                CheckPolicy {
                    state_every: NonZeroU64::new(number(row, "state_every")?)
                        .ok_or("zero state period native-only case")?,
                    transition_checks: row["transition_checks"] == true,
                },
                &mut owned,
            );
            if let Err(e) = r {
                error = Some(e);
            }
            require(
                owned.as_ptr() == ptr,
                "Rust observed storage unexpectedly reallocated within reserved capacity",
            )?;
            expected(
                if error.is_some() { "error" } else { "ok" },
                Some(&owned),
                vec![],
                None,
                None,
            )
        }
        "bfs" => {
            match enumerate_observed(
                m,
                SearchConfig {
                    max_states: 10,
                    max_transitions: 10,
                    max_depth: 10,
                },
                SearchLimits::default(),
                &mut observer,
            ) {
                Ok(r) => expected(
                    search_name(r.termination),
                    None,
                    vec![],
                    r.failure.map(|f| f.inputs),
                    None,
                ),
                Err(e) => {
                    error = Some(e);
                    expected("error", None, vec![], None, None)
                }
            }
        }
        "fuzz" => {
            match fuzz_observed(
                m,
                FuzzConfig {
                    seed: 0,
                    cases: 1,
                    max_steps: 1,
                    max_transitions: 1,
                    mutation_percent: 0,
                },
                RunLimits::default(),
                &mut observer,
            ) {
                Ok(r) => expected(
                    fuzz_name(r.termination),
                    None,
                    vec![],
                    r.failure.map(|f| f.inputs),
                    None,
                ),
                Err(e) => {
                    error = Some(e);
                    expected("error", None, vec![], None, None)
                }
            }
        }
        "shrink" => {
            let f = Failure {
                inputs: vec![1],
                violations: vec![PropertyFailure {
                    phase: CheckPhase::State,
                    check: Check::failed("bad", "broken"),
                }],
            };
            match shrink(m, &f, ShrinkConfig { max_attempts: 100 }) {
                Ok(r) => {
                    require(
                        r.termination == ShrinkTermination::SearchComplete,
                        "unexpected shrink termination",
                    )?;
                    expected(
                        "search_complete",
                        None,
                        vec![],
                        Some(r.minimized.inputs),
                        Some(r.attempts),
                    )
                }
                Err(e) => {
                    error = Some(e);
                    expected("error", None, vec![], None, None)
                }
            }
        }
        "record" => match execution::record(m, [1], RunConfig::default(), 100) {
            Ok(t) => {
                let outcome = match &t.termination {
                    Termination::Completed => "completed",
                    Termination::PropertyFailed => "property_failed",
                    Termination::ModelError(s) => {
                        require(
                            s.contains("fixture append error"),
                            "record hid expected checker error",
                        )?;
                        "model_error"
                    }
                    _ => "unexpected",
                };
                let all = t
                    .initial_checks
                    .into_iter()
                    .chain(t.steps.into_iter().flat_map(|s| s.checks))
                    .collect::<Vec<_>>();
                expected(outcome, Some(&all), vec![], None, None)
            }
            Err(e) => {
                error = Some(e);
                expected("error", None, vec![], None, None)
            }
        },
        "replay" => match execution::replay(m, &fixed_trace(), ReplayOptions::default()) {
            Ok(r) => {
                require(
                    r.outcome == ReplayOutcome::Exact,
                    "independent trace replay diverged",
                )?;
                expected("exact", None, vec![], None, None)
            }
            Err(e) => {
                error = Some(e);
                expected("error", None, vec![], None, None)
            }
        },
        _ => return Err(format!("unsupported model operation {op}")),
    };
    if let Some(needle) = row["error_contains"].as_str() {
        let e = error.ok_or("expected Result error")?;
        require(e.to_string().contains(needle), format!("wrong error {e}"))?;
    } else {
        require(error.is_none(), format!("unexpected error {error:?}"))?;
    }
    result["observations"] = json!(*d.observations.borrow());
    Ok(result)
}
struct TestOracle(Rc<Data>);
impl<M: Model<State = i32, Input = i32, Output = ()>> Oracle<M> for TestOracle {
    type State = i32;
    fn metadata(&self) -> ModelMetadata {
        metadata("append-oracle")
    }
    fn initial_state(&self) -> std::result::Result<i32, ModelError> {
        Ok(0)
    }
    fn advance(
        &self,
        b: &i32,
        i: &i32,
        _: &[()],
        _: &Disposition,
    ) -> std::result::Result<i32, ModelError> {
        Ok(b + i)
    }
    fn check_state_into(
        &self,
        h: &i32,
        _: &i32,
        c: &mut CheckSink<'_>,
    ) -> std::result::Result<(), ModelError> {
        self.emit("state", *h, c)
    }
    fn check_transition_into(
        &self,
        _: &OracleState<i32, i32>,
        _: &i32,
        t: &TransitionRef<'_, OracleState<i32, i32>, ()>,
        c: &mut CheckSink<'_>,
    ) -> std::result::Result<(), ModelError> {
        self.emit("transition", t.state.oracle, c)
    }
}
impl TestOracle {
    fn emit(
        &self,
        stage: &str,
        n: i32,
        c: &mut CheckSink<'_>,
    ) -> std::result::Result<(), ModelError> {
        self.0.event(format!("oracle_{stage}:{n}:{}", c.len()));
        c.push(Check::passed(format!("oracle.{stage}")));
        if self.0.wrapper_error.as_deref() == Some(format!("oracle_{stage}").as_str()) {
            return Err(ModelError::new("fixture append error"));
        }
        c.push(Check::skipped("oracle.skip", "not run"));
        Ok(())
    }
}
struct TestProjection(Rc<Data>);
impl Projection<Append, Append> for TestProjection {
    type State = i32;
    type Effect = ();
    fn metadata(&self) -> ModelMetadata {
        metadata("append-projection")
    }
    fn actual_state(&self, s: &i32) -> std::result::Result<i32, ModelError> {
        self.0.event("projection_actual_state".into());
        if self.0.wrapper_error.as_deref() == Some("projection_actual_state") {
            return Err(ModelError::new("fixture append error"));
        }
        Ok(*s)
    }
    fn reference_state(&self, s: &i32) -> std::result::Result<i32, ModelError> {
        self.0.event("projection_reference_state".into());
        Ok(*s)
    }
    fn actual_effect(&self, _: &()) -> std::result::Result<(), ModelError> {
        Ok(())
    }
    fn reference_effect(&self, _: &()) -> std::result::Result<(), ModelError> {
        Ok(())
    }
}
fn wrapper(d: &Rc<Data>, row: &Value) -> Result<Value> {
    let op = text(row, "operation")?;
    let mut checks = vec![Check::passed("sentinel")];
    let state = OracleState {
        model: 1,
        oracle: 1,
    };
    let before = OracleState {
        model: 0,
        oracle: 0,
    };
    let after = Transition::accepted(state.clone(), vec![]);
    let r = if op.starts_with("oracle_") {
        let model = WithOracle::new(Append(Rc::clone(d)), TestOracle(Rc::clone(d)));
        if op == "oracle_state" {
            model.check_state_into(&state, &mut CheckSink::new(&mut checks))
        } else {
            model.check_transition_into(
                &before,
                &1,
                &after.as_ref(),
                &mut CheckSink::new(&mut checks),
            )
        }
    } else {
        let reference = Rc::new(Data {
            events: Rc::clone(&d.events),
            observations: RefCell::new(vec![]),
            error_at: (row["wrapper_error"] == "reference_state").then_some("state:1".into()),
            fail: None,
            label: "reference_".into(),
            append: true,
            wrapper_error: None,
        });
        let paired = WithOracle::new(
            Append(Rc::clone(d)),
            Paired {
                reference: Append(Rc::clone(&reference)),
                projection: TestProjection(Rc::clone(d)),
            },
        );
        if op == "paired_state" {
            paired.check_state_into(&state, &mut CheckSink::new(&mut checks))
        } else {
            paired.check_transition_into(
                &before,
                &1,
                &after.as_ref(),
                &mut CheckSink::new(&mut checks),
            )
        }
    };
    let outcome = if let Err(e) = r {
        require(
            e.to_string().contains(
                row["error_contains"]
                    .as_str()
                    .ok_or("unexpected wrapper error")?,
            ),
            format!("wrong wrapper error {e}"),
        )?;
        "error"
    } else {
        require(row["error_contains"].is_null(), "expected wrapper error")?;
        "ok"
    };
    Ok(expected(outcome, Some(&checks), vec![], None, None))
}
fn corpus(path: &Path) -> Result<Value> {
    let data = json_file(path)?;
    require(
        data["profile"] == "append-checks-v1",
        "wrong append profile",
    )?;
    let mut count = 0;
    for row in list(&data, "cases")? {
        let name = text(row, "case")?;
        let d = Rc::new(Data {
            events: Rc::new(RefCell::new(vec![])),
            observations: RefCell::new(vec![]),
            error_at: row["error_at"].as_str().map(str::to_owned),
            fail: row["fail_state"].as_i64().map(|n| n as i32),
            label: String::new(),
            append: row["mode"] == "append",
            wrapper_error: row["wrapper_error"].as_str().map(str::to_owned),
        });
        let op = text(row, "operation")?;
        let actual = if op.starts_with("oracle_") || op.starts_with("paired_") {
            wrapper(&d, row)
        } else if d.append {
            run_model(&Append(Rc::clone(&d)), &d, row)
        } else {
            run_model(&Legacy(Rc::clone(&d)), &d, row)
        }
        .map_err(|e| format!("{name}: {e}"))?;
        require(
            actual == row["expected"],
            format!("{name}: result {actual} expected {}", row["expected"]),
        )?;
        require(
            json!(*d.events.borrow()) == row["events"],
            format!(
                "{name}: events {} expected {}",
                json!(*d.events.borrow()),
                row["events"]
            ),
        )?;
        count += 1;
    }
    Ok(
        json!({"producer":"rust","profile":"append-checks-v1","cases":count,"outcome":"passed","native_lease_tests":false,"rust_borrow_confinement":true}),
    )
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
#[derive(Default)]
struct Counts {
    step: Cell<usize>,
    state: Cell<usize>,
    edge: Cell<usize>,
    legacy: Cell<usize>,
}
struct Application {
    counts: Rc<Counts>,
    error_edge: bool,
}
impl Application {
    fn new(error_edge: bool) -> Self {
        Self {
            counts: Rc::new(Counts::default()),
            error_edge,
        }
    }
}
impl Model for Application {
    type State = i32;
    type Input = i32;
    type Output = String;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "native-append-threshold".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "application-v1".into(),
        }
    }
    fn initial_state(&self) -> std::result::Result<i32, ModelError> {
        Ok(0)
    }
    fn step(&self, s: &i32, i: &i32) -> std::result::Result<Transition<i32, String>, ModelError> {
        self.counts.step.set(self.counts.step.get() + 1);
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
    fn check_state(&self, _: &i32) -> std::result::Result<Vec<Check>, ModelError> {
        self.counts.legacy.set(self.counts.legacy.get() + 1);
        Err(ModelError::new("legacy state checker must not run"))
    }
    fn check_transition(
        &self,
        _: &i32,
        _: &i32,
        _: &TransitionRef<'_, i32, String>,
    ) -> std::result::Result<Vec<Check>, ModelError> {
        self.counts.legacy.set(self.counts.legacy.get() + 1);
        Err(ModelError::new("legacy transition checker must not run"))
    }
    fn check_state_into(
        &self,
        state: &i32,
        c: &mut CheckSink<'_>,
    ) -> std::result::Result<(), ModelError> {
        self.counts.state.set(self.counts.state.get() + 1);
        if !c.is_empty() {
            return Err(ModelError::new("application state batch must start empty"));
        }
        c.reserve(2);
        if !c.is_empty() {
            return Err(ModelError::new("reserve changed length"));
        }
        c.push(if *state >= 3 {
            Check::failed("threshold.bound", "at least three")
        } else {
            Check::passed("threshold.bound")
        });
        if c.len() != 1 || c.as_slice()[0].id.as_str() != "threshold.bound" {
            return Err(ModelError::new("state inspection mismatch"));
        }
        c.extend([Check::passed("state.appended")]);
        Ok(())
    }
    fn check_transition_into(
        &self,
        _: &i32,
        _: &i32,
        _: &TransitionRef<'_, i32, String>,
        c: &mut CheckSink<'_>,
    ) -> std::result::Result<(), ModelError> {
        self.counts.edge.set(self.counts.edge.get() + 1);
        if c.len() != 2
            || c.as_slice()[0].id.as_str() != "threshold.bound"
            || c.as_slice()[1].id.as_str() != "state.appended"
        {
            return Err(ModelError::new(
                "transition did not receive completed state prefix",
            ));
        }
        c.reserve(2);
        if c.len() != 2 {
            return Err(ModelError::new("reserve changed prefix"));
        }
        if self.error_edge {
            c.push(Check::passed("edge.partial"));
            return Err(ModelError::new("application append error"));
        }
        c.push(Check::passed("edge.checked"));
        if c.len() != 3 || c.as_slice()[2].id.as_str() != "edge.checked" {
            return Err(ModelError::new("transition inspection mismatch"));
        }
        c.extend([Check::passed("edge.appended")]);
        Ok(())
    }
}
impl Enumerate for Application {
    fn inputs(&self, _: &i32) -> std::result::Result<Vec<i32>, ModelError> {
        Ok(vec![0, 1])
    }
}
impl Generate for Application {
    fn generate(&self, _: &i32, rng: &mut Rng) -> std::result::Result<Option<i32>, ModelError> {
        Ok(Some(1 - rng.index(2).unwrap() as i32))
    }
    fn is_enabled(&self, _: &i32, i: &i32) -> std::result::Result<bool, ModelError> {
        Ok(matches!(i, 0 | 1))
    }
}
impl ModelCodec for Application {
    fn encode_state(&self, s: &i32) -> std::result::Result<Vec<u8>, ModelError> {
        Ok(s.to_string().into_bytes())
    }
    fn decode_state(&self, b: &[u8]) -> std::result::Result<i32, ModelError> {
        decode_integer(b)
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
fn controls() -> Result<Value> {
    let m = Application::new(false);
    let t = Transition::accepted(1, vec!["changed".into()]);
    let mut checks = Vec::with_capacity(32);
    checks.push(Check::passed("stale"));
    let ptr = checks.as_ptr();
    execution::check_observed_into(&m, &0, &1, &t, 1, CheckPolicy::default(), &mut checks)
        .map_err(|e| e.to_string())?;
    require(checks.as_ptr() == ptr, "caller capacity not reused")?;
    let ids = checks
        .iter()
        .map(|c| c.id.as_str().to_owned())
        .collect::<Vec<_>>();
    let error_model = Application::new(true);
    let result = execution::check_observed_into(
        &error_model,
        &0,
        &1,
        &t,
        1,
        CheckPolicy::default(),
        &mut checks,
    );
    require(
        result.is_err() && checks.is_empty(),
        "checker error did not clear observed storage",
    )?;
    Ok(
        json!({"ids":ids,"step_calls":m.counts.step.get(),"state_calls":m.counts.state.get(),"transition_calls":m.counts.edge.get(),"error_cleared":true,"error_step_calls":error_model.counts.step.get(),"error_state_calls":error_model.counts.state.get(),"error_transition_calls":error_model.counts.edge.get(),"legacy_calls":m.counts.legacy.get()+error_model.counts.legacy.get(),"static_borrow":true}),
    )
}
fn app_record(path: &str) -> Result<Value> {
    let controls = controls()?;
    let m = Application::new(false);
    let bfs = enumerate(
        &m,
        SearchConfig {
            max_states: 100,
            max_transitions: 100,
            max_depth: 10,
        },
    )
    .map_err(|e| e.to_string())?;
    require(
        bfs.termination == SearchTermination::FailureFound,
        "BFS failure missing",
    )?;
    let witness = bfs.failure.ok_or("BFS witness missing")?.inputs;
    let found = fuzz(
        &m,
        FuzzConfig {
            seed: 0,
            cases: 4,
            max_steps: 32,
            max_transitions: 128,
            mutation_percent: 0,
        },
    )
    .map_err(|e| e.to_string())?;
    let original = found.failure.ok_or("fuzz failure missing")?;
    let reduced = shrink(&m, &original, ShrinkConfig::default()).map_err(|e| e.to_string())?;
    require(
        reduced.termination == ShrinkTermination::SearchComplete && reduced.validated_original,
        "shrink did not qualify",
    )?;
    let trace = execution::record(
        &m,
        reduced.minimized.inputs.clone(),
        RunConfig::default(),
        100000,
    )
    .map_err(|e| e.to_string())?;
    let report =
        execution::replay(&m, &trace, ReplayOptions::default()).map_err(|e| e.to_string())?;
    require(
        report.outcome == ReplayOutcome::Exact && report.failure_reproduced,
        "fresh application trace did not replay",
    )?;
    let bytes = serde_json::to_vec(&trace_json(&trace)).map_err(|e| e.to_string())?;
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| e.to_string())?;
    f.write_all(&bytes).map_err(|e| e.to_string())?;
    Ok(
        json!({"producer":"rust","mode":"record","controls":controls,"bfs":{"states":bfs.states,"transitions":bfs.transitions,"witness":witness},"fuzz":{"original_steps":original.inputs.len(),"minimized_steps":reduced.minimized.inputs.len(),"shrink_attempts":reduced.attempts,"validated_original":reduced.validated_original},"legacy_calls":m.counts.legacy.get(),"append_state_calls":m.counts.state.get(),"append_transition_calls":m.counts.edge.get(),"audit_config":{"strategy":trace.config.strategy,"seed":trace.config.seed.map(|n|n.to_string()),"parameters":trace.config.parameters}}),
    )
}
fn app_replay(path: &Path) -> Result<Value> {
    let m = Application::new(false);
    let trace = parse_trace(&json_file(path)?)?;
    let r = execution::replay(&m, &trace, ReplayOptions::default()).map_err(|e| e.to_string())?;
    let outcome = match r.outcome {
        ReplayOutcome::Exact => "exact",
        ReplayOutcome::Diverged { .. } => "diverged",
        ReplayOutcome::Incompatible { .. } => "incompatible",
    };
    Ok(
        json!({"producer":"rust","mode":"replay","outcome":outcome,"failure_reproduced":r.failure_reproduced,"steps":r.steps_verified,"legacy_calls":m.counts.legacy.get(),"append_state_calls":m.counts.state.get(),"append_transition_calls":m.counts.edge.get()}),
    )
}
fn run() -> Result<Value> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let default =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../conformance/append-checks-v1.json");
    match args.as_slice() {
        [] => corpus(&default),
        [m, p] if m == "--corpus" => corpus(Path::new(p)),
        [m, p] if m == "record" => app_record(p),
        [m, p] if m == "replay" => app_replay(Path::new(p)),
        _ => Err("usage: native-append [--corpus PATH | record OUTPUT | replay INPUT]".into()),
    }
}
fn main() {
    match run() {
        Ok(v) => println!("{v}"),
        Err(e) => {
            eprintln!("native-append: {e}");
            std::process::exit(1)
        }
    }
}

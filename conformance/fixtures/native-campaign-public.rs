//! Public-only N06 consumer. Also included by tests/native_campaign_reference.rs.
//! No ModelCodec, runtime internals, or arbitrary closure serialization.
#![allow(dead_code)]
use stateless::campaign::*;
use stateless::explore::{FuzzConfig, FuzzReport, RunLimits};
use stateless::guided::{FeatureBuffer, Feedback, FeedbackMetadata, GuidanceConfig};
use stateless::{
    Check, Generate, Model, ModelError, ModelMetadata, Rng, Transition, TransitionRef,
};
use std::cell::Cell;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

pub type Log = Arc<Mutex<Vec<String>>>;
fn log(events: &Log, value: impl Into<String>) {
    events.lock().unwrap().push(value.into());
}
pub fn q(value: &str) -> String {
    let mut out = String::from("\"");
    for c in value.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c < ' ' => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}
fn arr(values: impl IntoIterator<Item = String>) -> String {
    format!("[{}]", values.into_iter().collect::<Vec<_>>().join(","))
}
fn strings(values: &[String]) -> String {
    arr(values.iter().map(|v| q(v)))
}
fn nums(values: &[u64]) -> String {
    arr(values.iter().map(|v| q(&v.to_string())))
}
pub fn obj(fields: &[(&str, String)]) -> String {
    format!(
        "{{{}}}",
        fields
            .iter()
            .map(|(k, v)| format!("{}:{v}", q(k)))
            .collect::<Vec<_>>()
            .join(",")
    )
}
fn n(value: impl ToString) -> String {
    q(&value.to_string())
}
fn err(value: Option<&str>) -> String {
    value.map(q).unwrap_or_else(|| "null".into())
}
pub fn config() -> CampaignConfig {
    CampaignConfig {
        master_seed: 0,
        first_job: 7,
        jobs: 1,
        workers: 1,
        result_buffer: 0,
        fuzz: FuzzConfig {
            seed: 999,
            cases: 1,
            max_steps: 2,
            max_transitions: 2,
            mutation_percent: 0,
        },
        stop_on_failure: false,
    }
}
fn fuzz_config(c: &FuzzConfig) -> String {
    obj(&[
        ("seed", n(c.seed)),
        ("cases", n(c.cases)),
        ("max_steps", n(c.max_steps)),
        ("max_transitions", n(c.max_transitions)),
        ("mutation_percent", n(c.mutation_percent)),
    ])
}
pub fn campaign_config(c: &CampaignConfig) -> String {
    obj(&[
        ("master_seed", n(c.master_seed)),
        ("first_job", n(c.first_job)),
        ("jobs", n(c.jobs)),
        ("workers", n(c.workers)),
        ("result_buffer", n(c.result_buffer)),
        ("fuzz", fuzz_config(&c.fuzz)),
        ("stop_on_failure", c.stop_on_failure.to_string()),
    ])
}
pub fn guidance_config(c: GuidanceConfig) -> String {
    obj(&[
        ("max_corpus_entries", n(c.max_corpus_entries)),
        ("max_corpus_inputs", n(c.max_corpus_inputs)),
        ("max_features", n(c.max_features)),
        (
            "max_features_per_observation",
            n(c.max_features_per_observation),
        ),
    ])
}
fn metadata(m: &ModelMetadata) -> String {
    obj(&[
        ("name", q(&m.name)),
        ("model_version", n(m.model_version)),
        ("properties_version", n(m.properties_version)),
        ("codec_version", n(m.codec_version)),
        ("build", q(&m.build)),
    ])
}
pub fn report(r: &CampaignReport) -> String {
    let (termination, error) = match &r.termination {
        CampaignTermination::JobsCompleted => ("JobsCompleted", None),
        CampaignTermination::FailureFound => ("FailureFound", None),
        CampaignTermination::Cancelled => ("Cancelled", None),
        CampaignTermination::Deadline => ("Deadline", None),
        CampaignTermination::CallbackError(e) => ("CallbackError", Some(e.0.as_str())),
        CampaignTermination::WorkerError(e) => ("WorkerError", Some(e.0.as_str())),
    };
    obj(&[
        ("requested", n(r.requested)),
        ("started", n(r.started)),
        ("finished", n(r.finished)),
        ("not_started", n(r.not_started)),
        ("delivered", n(r.delivered)),
        ("failures", n(r.failures)),
        ("errors", n(r.errors)),
        ("reported_transitions", n(r.reported_transitions)),
        ("reported_cases", n(r.reported_cases)),
        ("reported_skipped_checks", n(r.reported_skipped_checks)),
        ("accounting_complete", r.accounting_complete.to_string()),
        ("termination", q(termination)),
        ("error", err(error)),
    ])
}
pub fn fuzz_report(r: &FuzzReport<u64>) -> String {
    let failure = r
        .failure
        .as_ref()
        .map(|f| {
            obj(&[
                ("inputs", nums(&f.inputs)),
                (
                    "violations",
                    arr(f.violations.iter().map(|v| {
                        obj(&[
                            ("phase", q(&format!("{:?}", v.phase))),
                            ("id", q(v.check.id.as_str())),
                            ("status", q(&format!("{:?}", v.check.status))),
                        ])
                    })),
                ),
            ])
        })
        .unwrap_or_else(|| "null".into());
    let guidance = r
        .guidance
        .as_ref()
        .map(|g| {
            obj(&[
                ("algorithm", q(g.algorithm)),
                ("config", guidance_config(g.config)),
                (
                    "feedback",
                    obj(&[
                        ("name", q(&g.feedback.name)),
                        ("version", n(g.feedback.version)),
                        ("build", q(&g.feedback.build)),
                    ]),
                ),
                ("unique_features", n(g.unique_features)),
                ("corpus_inputs", n(g.corpus_inputs)),
                (
                    "corpus",
                    arr(g.corpus.iter().map(|c| {
                        obj(&[
                            ("inputs", nums(&c.inputs)),
                            ("new_features", n(c.new_features)),
                            ("discovered_case", n(c.discovered_case)),
                            ("discovered_transition", n(c.discovered_transition)),
                        ])
                    })),
                ),
                ("mutation_cases", n(g.mutation_cases)),
                ("dropped_prefixes", n(g.dropped_prefixes)),
                ("evicted_prefixes", n(g.evicted_prefixes)),
                ("feature_limit_reached", g.feature_limit_reached.to_string()),
                ("corpus_limit_reached", g.corpus_limit_reached.to_string()),
            ])
        })
        .unwrap_or_else(|| "null".into());
    obj(&[
        ("termination", q(&format!("{:?}", r.termination))),
        ("seed", n(r.seed)),
        ("cases", n(r.cases)),
        ("transitions", n(r.transitions)),
        ("skipped_checks", n(r.skipped_checks)),
        ("failure", failure),
        ("guidance", guidance),
    ])
}
pub fn job(j: &JobReport<u64>) -> String {
    let (outcome, error, fuzz) = match &j.outcome {
        JobOutcome::Completed(r) => ("Completed", None, fuzz_report(r)),
        JobOutcome::FactoryError(e) => ("FactoryError", Some(e.0.as_str()), "null".into()),
        JobOutcome::ModelError(e) => ("ModelError", Some(e.0.as_str()), "null".into()),
        JobOutcome::Panicked(e) => ("Panicked", Some(e.as_str()), "null".into()),
    };
    obj(&[
        ("job_id", n(j.job_id)),
        ("seed", n(j.seed)),
        ("config", fuzz_config(&j.config)),
        (
            "metadata",
            j.metadata
                .as_ref()
                .map(metadata)
                .unwrap_or_else(|| "null".into()),
        ),
        ("outcome", q(outcome)),
        ("error", err(error)),
        ("fuzz", fuzz),
    ])
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Pass,
    Random,
    Property,
    InitialError,
    StepError,
    CheckError,
    MetadataPanic,
    DropPanic,
}
pub struct Counter {
    pub id: u64,
    pub mode: Mode,
    pub log: Log,
    // Deliberately non-Send local model storage. Each factory creates its own.
    owner: Rc<Cell<usize>>,
}
impl Counter {
    pub fn new(id: u64, mode: Mode, events: &Log) -> Self {
        Self {
            id,
            mode,
            log: events.clone(),
            owner: Rc::new(Cell::new(0)),
        }
    }
    fn event(&self, value: impl AsRef<str>) {
        log(&self.log, format!("{}:{}", self.id, value.as_ref()));
    }
}
impl Drop for Counter {
    fn drop(&mut self) {
        self.event("model_drop");
        if self.mode == Mode::DropPanic {
            panic!("model drop");
        }
    }
}
impl Model for Counter {
    type State = u64;
    type Input = u64;
    type Output = Rc<u64>;
    fn metadata(&self) -> ModelMetadata {
        self.event("metadata");
        if self.mode == Mode::MetadataPanic {
            panic!("metadata panic");
        }
        ModelMetadata {
            name: format!("n06-counter:{}", self.id),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "n06-counter-v1".into(),
        }
    }
    fn initial_state(&self) -> Result<u64, ModelError> {
        self.event("initial");
        self.owner.set(0);
        if self.mode == Mode::InitialError {
            return Err(ModelError::new("initial error"));
        }
        Ok(0)
    }
    fn step(&self, before: &u64, input: &u64) -> Result<Transition<u64, Rc<u64>>, ModelError> {
        self.event(format!("step:{before}:{input}"));
        self.owner.set(self.owner.get() + 1);
        if self.mode == Mode::StepError {
            return Err(ModelError::new("step error"));
        }
        Ok(Transition::accepted(before + 1, vec![Rc::new(*input)]))
    }
    fn check_state(&self, state: &u64) -> Result<Vec<Check>, ModelError> {
        self.event(format!("check_state:{state}"));
        if *state > 0 && self.mode == Mode::CheckError {
            return Err(ModelError::new("check error"));
        }
        Ok(vec![
            if *state == 2 && self.mode == Mode::Property {
                Check::failed("n06.counter", "second transition")
            } else {
                Check::passed("n06.counter")
            },
            Check::skipped("n06.optional", "fixture"),
        ])
    }
    fn check_transition(
        &self,
        before: &u64,
        input: &u64,
        _: &TransitionRef<'_, u64, Rc<u64>>,
    ) -> Result<Vec<Check>, ModelError> {
        self.event(format!("check_transition:{before}:{input}"));
        Ok(vec![Check::passed("n06.edge")])
    }
}
impl Generate for Counter {
    fn generate(&self, state: &u64, rng: &mut Rng) -> Result<Option<u64>, ModelError> {
        let value = if self.mode == Mode::Random {
            rng.next_u64()
        } else {
            state + 1
        };
        self.event(format!("generate:{state}:{value}"));
        Ok(Some(value))
    }
    fn is_enabled(&self, state: &u64, input: &u64) -> Result<bool, ModelError> {
        self.event(format!("enabled:{state}:{input}"));
        Ok(true)
    }
}
pub struct CounterFeedback {
    id: u64,
    log: Log,
    panic_metadata: bool,
}
impl CounterFeedback {
    pub fn new(id: u64, events: &Log) -> Self {
        Self {
            id,
            log: events.clone(),
            panic_metadata: false,
        }
    }
    pub fn with_metadata_panic(mut self) -> Self {
        self.panic_metadata = true;
        self
    }
    fn event(&self, s: &str) {
        log(&self.log, format!("{}:{s}", self.id));
    }
}
impl Drop for CounterFeedback {
    fn drop(&mut self) {
        self.event("feedback_drop");
    }
}
impl Feedback<Counter> for CounterFeedback {
    type Feature = u64;
    fn metadata(&self) -> FeedbackMetadata {
        self.event("feedback_metadata");
        if self.panic_metadata {
            panic!("feedback metadata panic");
        }
        FeedbackMetadata {
            name: "n06-depth".into(),
            version: 1,
            build: "n06-depth-v1".into(),
        }
    }
    fn initial_features(
        &self,
        state: &u64,
        out: &mut FeatureBuffer<'_, u64>,
    ) -> Result<(), ModelError> {
        self.event("feedback_initial");
        out.push(*state)
    }
    fn transition_features(
        &self,
        _: &u64,
        _: &u64,
        after: &TransitionRef<'_, u64, Rc<u64>>,
        out: &mut FeatureBuffer<'_, u64>,
    ) -> Result<(), ModelError> {
        self.event("feedback_transition");
        out.push(*after.state)
    }
}
pub fn guidance() -> GuidanceConfig {
    GuidanceConfig {
        max_corpus_entries: 2,
        max_corpus_inputs: 3,
        max_features: 3,
        max_features_per_observation: 1,
    }
}

pub const SEEDS: &[(u64, u64, u64)] = &[
    (0, 0, 16294208416658607535),
    (0, 1, 10451216379200822465),
    (0, 2, 10905525725756348110),
    (0, 7, 7191089600892374487),
    (0, 42, 13679457532755275413),
    (0, u64::MAX, 16490336266968443936),
    (u64::MAX, 0, 16490336266968443936),
    (u64::MAX, 1, 16294208416658607535),
    (1, u64::MAX, 16294208416658607535),
    (u64::MAX, 2, 10451216379200822465),
    (2, u64::MAX, 10451216379200822465),
    (1 << 63, 1 << 63, 16294208416658607535),
    (1 << 63, (1 << 63) + 1, 10451216379200822465),
    (1 << 53, u64::MAX - (1 << 53) + 1, 16294208416658607535),
    ((1 << 53) + 1, u64::MAX - (1 << 53), 16294208416658607535),
];
pub fn seed_rows() -> Vec<String> {
    SEEDS
        .iter()
        .enumerate()
        .map(|(i, &(m, id, want))| {
            let actual = job_seed(m, id);
            assert_eq!(actual, want, "independent seed {i}");
            obj(&[
                ("schema", q("native-campaign-v1")),
                ("kind", q("seed")),
                ("case", q(&format!("seed-{i:02}"))),
                ("master_seed", n(m)),
                ("job_id", n(id)),
                ("seed", n(actual)),
            ])
        })
        .collect()
}

pub fn deterministic_case(name: &str) -> String {
    let events = Log::default();
    let cancellation = AtomicBool::new(name == "pre-cancelled");
    let guided = name.starts_with("guided")
        || name == "feedback-error"
        || name == "factory-cancelled"
        || name == "feedback-metadata-panic";
    let mut c = config();
    if name == "factory-error" || name == "feedback-error" {
        c.jobs = 2;
        c.stop_on_failure = true;
    }
    if name == "property-stop" {
        c.jobs = 3;
        c.stop_on_failure = true;
    }
    if name == "cases-zero" {
        c.fuzz.cases = 0;
    }
    if name == "steps-zero" {
        c.fuzz.cases = 2;
        c.fuzz.max_steps = 0;
        c.fuzz.max_transitions = 0;
    }
    if name == "transitions-zero" {
        c.fuzz.max_transitions = 0;
    }
    let mode = match name {
        "property-stop" => Mode::Property,
        "initial-error" => Mode::InitialError,
        "step-error" => Mode::StepError,
        "check-error" => Mode::CheckError,
        "metadata-panic" => Mode::MetadataPanic,
        "drop-panic" => Mode::DropPanic,
        _ => Mode::Pass,
    };
    let limits = RunLimits {
        cancellation: Some(&cancellation),
        max_duration: if name == "deadline-zero" {
            Some(Duration::ZERO)
        } else {
            None
        },
    };
    let factory = |id| {
        log(&events, format!("{id}:factory"));
        if name == "factory-error" {
            return Err(ModelError::new("factory error"));
        }
        if name == "factory-cancelled" {
            cancellation.store(true, Ordering::Relaxed);
        }
        Ok(Counter::new(id, mode, &events))
    };
    let mut jobs = Vec::new();
    let sink = |j: JobReport<u64>| {
        log(&events, format!("{}:sink", j.job_id));
        jobs.push(job(&j));
        if name == "sink-error" {
            Err(ModelError::new("first sink error"))
        } else if name == "sink-panic" {
            panic!("sink panic")
        } else {
            Ok(())
        }
    };
    let result = if guided {
        run_guided_campaign(
            c.clone(),
            limits,
            guidance(),
            factory,
            |id, _| {
                log(&events, format!("{id}:feedback_factory"));
                if name == "feedback-error" {
                    return Err(ModelError::new("feedback factory error"));
                }
                let feedback = CounterFeedback::new(id, &events);
                Ok(if name == "feedback-metadata-panic" {
                    feedback.with_metadata_panic()
                } else {
                    feedback
                })
            },
            sink,
        )
    } else {
        run_campaign(c.clone(), limits, factory, sink)
    }
    .unwrap();
    // Expectations authored independently from the result projection.
    if name == "factory-error" {
        assert_eq!((result.started, result.errors, result.delivered), (2, 2, 2));
        assert!(result.accounting_complete);
        assert_eq!(result.termination, CampaignTermination::JobsCompleted);
    }
    if name == "feedback-error" {
        assert_eq!((result.started, result.errors, result.delivered), (2, 2, 2));
        assert!(!result.accounting_complete);
        assert_eq!(result.termination, CampaignTermination::JobsCompleted);
    }
    if name == "property-stop" {
        assert_eq!(
            (result.started, result.failures, result.not_started),
            (1, 1, 2)
        );
    }
    if name == "pre-cancelled" || name == "deadline-zero" {
        assert!(events.lock().unwrap().is_empty());
        assert_eq!(result.started, 0);
    }
    obj(&[
        ("schema", q("native-campaign-v1")),
        ("kind", q("campaign")),
        ("case", q(name)),
        ("config", campaign_config(&c)),
        ("guided", guided.to_string()),
        ("jobs", arr(jobs)),
        ("callbacks", strings(&events.lock().unwrap())),
        ("report", report(&result)),
    ])
}
pub const CASES: &[&str] = &[
    "ordinary-basic",
    "guided-basic",
    "factory-error",
    "feedback-error",
    "factory-cancelled",
    "pre-cancelled",
    "deadline-zero",
    "cases-zero",
    "steps-zero",
    "transitions-zero",
    "initial-error",
    "step-error",
    "check-error",
    "metadata-panic",
    "feedback-metadata-panic",
    "drop-panic",
    "sink-error",
    "sink-panic",
    "property-stop",
];

pub fn validation_rows() -> Vec<String> {
    let base = config();
    let mut cases = Vec::new();
    macro_rules! case {
        ($name:expr,$field:ident,$value:expr,$expected:expr) => {{
            let mut c = base.clone();
            c.$field = $value;
            cases.push(($name, c, $expected));
        }};
    }
    case!("jobs-zero", jobs, 0, Some("campaign jobs must be positive"));
    case!(
        "workers-zero",
        workers,
        0,
        Some("campaign workers must be in 1..=256")
    );
    case!(
        "workers-257",
        workers,
        257,
        Some("campaign workers must be in 1..=256")
    );
    case!(
        "buffer-1025",
        result_buffer,
        1025,
        Some("campaign result_buffer must be in 0..=1024")
    );
    case!("workers-256", workers, 256, None);
    case!("buffer-1024", result_buffer, 1024, None);
    case!("max-id", first_job, u64::MAX, None);
    let mut c = base.clone();
    c.first_job = u64::MAX;
    c.jobs = 2;
    cases.push((
        "id-overflow",
        c,
        Some("campaign job ID range overflows u64"),
    ));
    let mut c = base.clone();
    c.fuzz.mutation_percent = 101;
    cases.push((
        "mutation-101",
        c,
        Some("mutation_percent must be in 0..=100"),
    ));
    let mut c = base.clone();
    c.fuzz.mutation_percent = 100;
    cases.push(("mutation-100", c, None));
    // These exercise the 64-bit Rust host range, never execute their huge budgets.
    if usize::BITS == 64 {
        let portable_max = usize::try_from((1_u64 << 53) - 1).unwrap();
        let mut c = base.clone();
        c.jobs = 2049;
        c.fuzz.cases = portable_max;
        cases.push((
            "aggregate-cases",
            c,
            Some("campaign aggregate cases overflow u64"),
        ));
        let mut c = base.clone();
        c.jobs = 2049;
        c.fuzz.cases = 1;
        c.fuzz.max_steps = portable_max;
        c.fuzz.max_transitions = u64::MAX;
        cases.push((
            "aggregate-transitions",
            c,
            Some("campaign aggregate transitions overflow u64"),
        ));
        let mut c = base.clone();
        c.jobs = 2;
        c.fuzz.cases = portable_max;
        c.fuzz.max_steps = portable_max;
        c.fuzz.max_transitions = 1;
        cases.push(("wide-product-capped", c, None));
        let mut c = base.clone();
        c.first_job = 0;
        c.jobs = portable_max;
        c.fuzz.cases = 1;
        c.fuzz.max_steps = 1;
        c.fuzz.max_transitions = 1;
        cases.push(("max-job-count-valid", c, None));
    }
    let mut c = base.clone();
    c.jobs = 0;
    c.workers = 0;
    c.result_buffer = 1025;
    c.fuzz.mutation_percent = 101;
    cases.push((
        "validation-first-error",
        c,
        Some("campaign jobs must be positive"),
    ));
    cases
        .into_iter()
        .map(|(name, c, want)| {
            let actual = c.validate().err().map(|e| e.0);
            assert_eq!(actual.as_deref(), want, "{name}");
            if let Some(expected) = want {
                let r = run_campaign(
                    c.clone(),
                    RunLimits::default(),
                    |_| -> Result<Counter, ModelError> { panic!("invalid factory called") },
                    |_| panic!("invalid sink called"),
                );
                assert_eq!(r.unwrap_err().0, expected);
            }
            obj(&[
                ("schema", q("native-campaign-v1")),
                ("kind", q("validation")),
                ("case", q(name)),
                ("config", campaign_config(&c)),
                ("error", err(actual.as_deref())),
            ])
        })
        .collect()
}
pub fn assert_public_goldens(path: impl AsRef<std::ffi::OsStr>, rows: &[String]) {
    use std::io::Write;
    use std::process::{Command, Stdio};
    let mut child = Command::new("python3")
        .arg(path)
        .arg("--compare")
        .stdin(Stdio::piped())
        .spawn()
        .expect("independent strict JSON comparator");
    child
        .stdin
        .take()
        .unwrap()
        .write_all((rows.join("\n") + "\n").as_bytes())
        .unwrap();
    assert!(
        child.wait().unwrap().success(),
        "independent strict JSON comparison failed"
    );
}
pub fn main() {
    for row in seed_rows().into_iter().chain(validation_rows()) {
        println!("{row}");
    }
    let rows: Vec<_> = CASES.iter().map(|name| deterministic_case(name)).collect();
    for row in &rows {
        println!("{row}");
    }
    if let Some(path) = std::env::var_os("N06_EXPECTED_AUTHOR") {
        assert_public_goldens(path, &rows);
    }
}

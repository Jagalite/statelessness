mod feedback;
mod fixture;
mod host;
mod normalize;

use fixture::{ControlSpec, Events, ModelSpec, State, TableModel};
use serde::Deserialize;
use serde_json::{Value, json};
use stateless::execution::{self, ReplayObservation, ReplayOptions, ReplayOutcome};
use stateless::explore::{
    self, CheckPhase, FuzzConfig, FuzzReport, RunLimits, SearchObserver, ShrinkConfig,
};
use stateless::guided::{self, Feedback, GuidanceConfig, StateFeedback};
use stateless::trace::{RunConfig, Termination};
use stateless::{Check, ModelError, TransitionRef};
use std::rc::Rc;
use std::time::Duration;

// Expected output is intentionally absent from these types. Deserialize ignores
// it; no expected report, witness, trace, or shrink result can drive execution.
#[derive(Deserialize)]
struct Corpus {
    schema: String,
    source_commit: String,
    cases: Vec<Case>,
}
#[derive(Deserialize)]
struct Case {
    id: String,
    mode: String,
    config: Config,
    guidance: Guidance,
    model: ModelSpec,
    feedback: feedback::FeedbackSpec,
    control: ControlSpec,
    shrink_config: Option<Shrinking>,
}
#[derive(Deserialize)]
struct Config {
    seed: String,
    cases: usize,
    max_steps: usize,
    max_transitions: u64,
    mutation_percent: u8,
}
impl Config {
    fn native(&self) -> Result<FuzzConfig, Box<dyn std::error::Error>> {
        Ok(FuzzConfig {
            seed: self.seed.parse()?,
            cases: self.cases,
            max_steps: self.max_steps,
            max_transitions: self.max_transitions,
            mutation_percent: self.mutation_percent,
        })
    }
}
#[derive(Deserialize)]
struct Guidance {
    max_corpus_entries: usize,
    max_corpus_inputs: usize,
    max_features: usize,
    max_features_per_observation: usize,
}
impl Guidance {
    fn native(&self) -> GuidanceConfig {
        GuidanceConfig {
            max_corpus_entries: self.max_corpus_entries,
            max_corpus_inputs: self.max_corpus_inputs,
            max_features: self.max_features,
            max_features_per_observation: self.max_features_per_observation,
        }
    }
}
#[derive(Deserialize)]
struct Shrinking {
    max_attempts: usize,
}

struct Observer(Rc<Events>);
impl SearchObserver<TableModel> for Observer {
    fn needs_history(&self) -> bool {
        true
    }
    fn initial(&mut self, state: &State, checks: &[Check]) {
        self.0.log(json!([
            "observer_initial",
            self.0.case.get(),
            state,
            normalize::checks(checks)
        ]));
        self.0.done("observer_initial");
    }
    fn check_error(&mut self, phase: CheckPhase, checks: &[Check], complete: bool) {
        self.0.log(json!([
            "observer_check_error",
            self.0.case.get(),
            format!("{phase:?}"),
            normalize::checks(checks),
            complete
        ]));
        self.0.done("observer_check_error");
    }
    fn transition(
        &mut self,
        before: &State,
        input: &i64,
        after: &TransitionRef<'_, State, i64>,
        state_checks: &[Check],
        transition_checks: &[Check],
        history: &[i64],
    ) {
        self.0.log(json!([
            "observer_transition",
            self.0.case.get(),
            before,
            input,
            after.state,
            after.outputs,
            normalize::checks(state_checks),
            normalize::checks(transition_checks),
            history,
            normalize::disposition(after.disposition)
        ]));
        self.0.done("observer_transition");
    }
}

fn guided_run<F: Feedback<TableModel>>(
    case: &Case,
    model: &TableModel,
    feedback: F,
    config: FuzzConfig,
    limits: RunLimits<'_>,
    observer: &mut Observer,
) -> Result<FuzzReport<i64>, ModelError> {
    guided::fuzz_observed(
        model,
        config,
        case.guidance.native(),
        feedback,
        limits,
        observer,
    )
}

fn run(case: &Case) -> Result<Value, Box<dyn std::error::Error>> {
    let events = Events::new(&case.control);
    let model = TableModel {
        spec: case.model.clone(),
        events: events.clone(),
    };
    let mut observer = Observer(events.clone());
    let limits = RunLimits {
        cancellation: Some(&events.cancelled),
        max_duration: case.control.zero_duration.then_some(Duration::ZERO),
    };
    let config = case.config.native()?;
    let result = match case.mode.as_str() {
        "ordinary" => explore::fuzz_observed(&model, config, limits, &mut observer),
        "guided" => match case.feedback.kind.as_str() {
            "custom" => {
                let feedback = feedback::Custom {
                    spec: case.feedback.clone(),
                    events: events.clone(),
                };
                if case.feedback.initial.is_none() {
                    guided_run(
                        case,
                        &model,
                        feedback::DefaultInitial(feedback),
                        config,
                        limits,
                        &mut observer,
                    )
                } else {
                    guided_run(case, &model, feedback, config, limits, &mut observer)
                }
            }
            "state" => {
                let project = case.feedback.project;
                if project > 1 {
                    return Err("state feedback project must be 0 or 1".into());
                }
                let constant_hash = case.feedback.hash == "constant";
                let feedback =
                    StateFeedback::new(feedback::metadata(), move |state: &State| feedback::Key {
                        value: json!(state[project]),
                        constant_hash,
                    });
                guided_run(
                    case,
                    &model,
                    feedback::Projected {
                        inner: feedback,
                        events: events.clone(),
                    },
                    config,
                    limits,
                    &mut observer,
                )
            }
            "exact" => guided_run(
                case,
                &model,
                feedback::Projected {
                    inner: guided::exact_state::<TableModel>(),
                    events: events.clone(),
                },
                config,
                limits,
                &mut observer,
            ),
            other => return Err(format!("unsupported feedback kind {other}").into()),
        },
        other => return Err(format!("unsupported search mode {other}").into()),
    };
    let mut witnesses = Vec::new();
    let mut shrink = Value::Null;
    if let Ok(report) = &result {
        if let Some(guidance) = &report.guidance {
            for entry in &guidance.corpus {
                witnesses.push(replay_witness(&case.model, &entry.inputs, false)?);
            }
        }
        if let Some(failure) = &report.failure {
            // Passing witnesses are never shrink targets. Selected failure
            // witnesses follow the retained passing entries in stable order.
            let original_replay = replay_witness(&case.model, &failure.inputs, true)?;
            if let Some(config) = &case.shrink_config {
                witnesses.push(original_replay);
                let shrunk = explore::shrink(
                    &TableModel::fresh(&case.model),
                    failure,
                    ShrinkConfig {
                        max_attempts: config.max_attempts,
                    },
                )?;
                witnesses.push(replay_witness(&case.model, &shrunk.minimized.inputs, true)?);
                shrink = normalize::shrink(&shrunk);
            }
        } else if case.shrink_config.is_some() {
            return Err(format!("{} requested shrink without failure", case.id).into());
        }
    }
    let (report, error) = match result {
        Ok(r) => (normalize::report(&r), Value::Null),
        Err(e) => (Value::Null, json!(e.to_string())),
    };
    Ok(
        json!({"report": report, "error": error, "trace": *events.trace.borrow(),
        "generator_rng": *events.rng.borrow(), "witness_replays": witnesses, "shrink": shrink}),
    )
}

fn replay_witness(
    spec: &ModelSpec,
    inputs: &[i64],
    failing: bool,
) -> Result<Value, Box<dyn std::error::Error>> {
    let recorded = execution::record(
        &TableModel::fresh(spec),
        inputs.iter().copied(),
        RunConfig::default(),
        inputs.len(),
    )?;
    let expected_termination = if failing {
        Termination::PropertyFailed
    } else {
        Termination::Completed
    };
    if recorded.termination != expected_termination {
        return Err(format!(
            "witness recording termination {:?}; expected {:?}",
            recorded.termination, expected_termination
        )
        .into());
    }
    let replay_model = TableModel::fresh(spec);
    // Replay restores the encoded initial checkpoint; it intentionally does not
    // call initial_state. Its fixture callback context is still fresh case 1.
    replay_model.events.case.set(1);
    let mut states = Vec::new();
    let mut outputs = Vec::new();
    let mut dispositions = Vec::new();
    let mut checks = Vec::new();
    let replay = execution::replay_with_observations(
        &replay_model,
        &recorded,
        ReplayOptions::default(),
        |observation, _| {
            match observation {
                ReplayObservation::Initial(initial) => {
                    states.push(json!(initial.state));
                    checks.push(json!({"phase": "InitialState", "checks": normalize::checks(initial.checks)}));
                }
                ReplayObservation::Turn(turn) => {
                    states.push(json!(turn.actual.transition.state));
                    outputs.push(json!(turn.actual.transition.outputs));
                    dispositions.push(normalize::disposition(turn.actual.transition.disposition));
                    let (state_checks, transition_checks) =
                        turn.actual.checks.split_at(turn.actual.state_check_count);
                    checks
                        .push(json!({"phase": "State", "checks": normalize::checks(state_checks)}));
                    checks.push(json!({"phase": "Transition", "checks": normalize::checks(transition_checks)}));
                }
                ReplayObservation::Error { error, .. } => return Err(error.clone()),
            }
            Ok(())
        },
    )?;
    if replay.outcome != ReplayOutcome::Exact
        || replay.failure_reproduced != failing
        || replay.steps_verified != inputs.len()
    {
        return Err(
            format!("witness replay did not reproduce recorded evidence: {replay:?}").into(),
        );
    }
    Ok(
        json!({"inputs": inputs, "states": states, "outputs": outputs, "checks": checks,
        "dispositions": dispositions,
        "termination": format!("{:?}", recorded.termination),
        "replay": {"outcome": format!("{:?}", replay.outcome), "failure_reproduced": replay.failure_reproduced,
            "steps_verified": replay.steps_verified}}),
    )
}

fn main() {
    if let Err(error) = command() {
        eprintln!("native-guided-rust: {error}");
        std::process::exit(1);
    }
}
fn command() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let mut corpus_path = None;
    let mut output = None;
    let mut selected = None;
    let mut host_checks = false;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--corpus" => corpus_path = Some(args.next().ok_or("--corpus needs a path")?),
            "--output" => output = Some(args.next().ok_or("--output needs a path")?),
            "--case" => selected = Some(args.next().ok_or("--case needs an ID")?),
            "--host-checks" => host_checks = true,
            "--help" | "-h" => {
                println!(
                    "native-guided-rust --corpus PATH [--output PATH] [--case ID]\nnative-guided-rust --host-checks [--output PATH]"
                );
                return Ok(());
            }
            _ => return Err(format!("unrecognized argument {arg}").into()),
        }
    }
    let value = if host_checks {
        if corpus_path.is_some() || selected.is_some() {
            return Err("--host-checks is separate from corpus execution".into());
        }
        host::run()?
    } else {
        let path = corpus_path.ok_or("--corpus PATH or --host-checks is required")?;
        let corpus: Corpus = serde_json::from_slice(&std::fs::read(&path)?)?;
        if corpus.schema != "stateless-guided-v2/1" {
            return Err("unsupported corpus schema".into());
        }
        if corpus.source_commit != "a4cc11eea8db62115b452eee3c6610c04fa06b96" {
            return Err("unexpected corpus source authority".into());
        }
        let mut cases = Vec::new();
        for case in &corpus.cases {
            if selected.as_ref().is_none_or(|id| id == &case.id) {
                let actual = run(case).map_err(|e| format!("case {}: {e}", case.id))?;
                cases.push(json!({"id": case.id, "actual": actual}));
            }
        }
        if cases.is_empty() {
            return Err("no selected cases".into());
        }
        json!({"schema": "stateless-guided-v2-results/1", "engine": "rust", "cases": cases})
    };
    let data = serde_json::to_string_pretty(&value)? + "\n";
    if let Some(path) = output {
        std::fs::write(path, data)?;
    } else {
        print!("{data}");
    }
    Ok(())
}

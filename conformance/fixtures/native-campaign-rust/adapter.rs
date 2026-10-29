//! Appended inside a byte-for-byte source copy of campaign.rs by build.rs.
//! Production source and visibility are unchanged.
use super::lifecycle::*;
use super::{CampaignConfig, CampaignReport, CampaignTermination};
use crate::{
    Check, Disposition, Enumerate, Generate, Model, ModelCodec, ModelError, Rng, Transition,
    TransitionRef,
};
use std::collections::{BTreeMap, BTreeSet};

fn q(v: &str) -> String {
    format!("{:?}", v)
}
fn n(v: impl ToString) -> String {
    q(&v.to_string())
}
fn obj(v: &[(&str, String)]) -> String {
    format!(
        "{{{}}}",
        v.iter()
            .map(|(k, v)| format!("{}:{v}", q(k)))
            .collect::<Vec<_>>()
            .join(",")
    )
}
fn arr(v: impl IntoIterator<Item = String>) -> String {
    format!("[{}]", v.into_iter().collect::<Vec<_>>().join(","))
}
fn optional<T>(v: Option<T>, f: impl FnOnce(T) -> String) -> String {
    v.map(f).unwrap_or_else(|| "null".into())
}
fn summary(o: Outcome) -> Summary {
    let normal = !matches!(
        o,
        Outcome::FactoryError | Outcome::ModelError | Outcome::Panicked
    );
    Summary {
        outcome: o,
        failure: o == Outcome::Failure,
        transitions: if normal { 2 } else { 0 },
        cases: u64::from(normal),
        skipped_checks: u64::from(normal),
    }
}
fn sjson(s: &Summary) -> String {
    obj(&[
        ("outcome", q(&format!("{:?}", s.outcome))),
        ("failure", s.failure.to_string()),
        ("transitions", n(s.transitions)),
        ("cases", n(s.cases)),
        ("skipped_checks", n(s.skipped_checks)),
    ])
}
fn report(r: &CampaignReport) -> String {
    let (term, error) = match &r.termination {
        CampaignTermination::CallbackError(e) => ("CallbackError".into(), Some(e)),
        CampaignTermination::WorkerError(e) => ("WorkerError".into(), Some(e)),
        other => (format!("{other:?}"), None),
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
        ("termination", q(&term)),
        ("error", optional(error, |e| q(&e.0))),
    ])
}
fn state(s: &State) -> String {
    obj(&[
        ("report", report(&s.report)),
        ("stop", optional(s.stop, |s| q(&format!("{s:?}")))),
        (
            "pending",
            arr(s.pending.iter().map(|(id, p)| {
                obj(&[
                    ("job_id", n(id)),
                    (
                        "status",
                        q(if matches!(p, Pending::Running) {
                            "Running"
                        } else {
                            "Ready"
                        }),
                    ),
                    (
                        "summary",
                        if let Pending::Ready(s) = p {
                            sjson(s)
                        } else {
                            "null".into()
                        },
                    ),
                ])
            })),
        ),
        ("delivering", optional(s.delivering, n)),
        ("lost_worker", s.lost_worker.to_string()),
        ("finalized", s.finalized.to_string()),
    ])
}
fn event(i: &Input) -> String {
    match i {
        Input::Claim { stop } => obj(&[(
            "Claim",
            obj(&[("stop", optional(*stop, |s| q(&format!("{s:?}"))))]),
        )]),
        Input::Complete { job_id, summary } => obj(&[(
            "Complete",
            obj(&[("job_id", n(job_id)), ("summary", sjson(summary))]),
        )]),
        Input::Receive { job_id } => obj(&[("Receive", obj(&[("job_id", n(job_id))]))]),
        Input::SinkReturned { job_id, error } => obj(&[(
            "SinkReturned",
            obj(&[
                ("job_id", n(job_id)),
                ("error", optional(error.as_ref(), |e| q(&e.0))),
            ]),
        )]),
        Input::WorkerFailed { error, lost_work } => obj(&[(
            "WorkerFailed",
            obj(&[("error", q(&error.0)), ("lost_work", lost_work.to_string())]),
        )]),
        Input::Finish => "{\"Finish\":{}}".into(),
    }
}
fn effect(o: &Output) -> String {
    match o {
        Output::RunJob(id) => obj(&[("RunJob", n(id))]),
        Output::Deliver(id) => obj(&[("Deliver", n(id))]),
        Output::Discard(id) => obj(&[("Discard", n(id))]),
    }
}
fn hex(b: &[u8]) -> String {
    q(&b.iter().map(|b| format!("{b:02x}")).collect::<String>())
}
fn c() -> Input {
    Input::Claim { stop: None }
}
fn complete(id: u64, o: Outcome) -> Input {
    Input::Complete {
        job_id: id,
        summary: summary(o),
    }
}
fn receive(id: u64) -> Input {
    Input::Receive { job_id: id }
}
fn ack(id: u64, error: Option<&str>) -> Input {
    Input::SinkReturned {
        job_id: id,
        error: error.map(ModelError::new),
    }
}
fn worker(error: &str, lost_work: bool) -> Input {
    Input::WorkerFailed {
        error: ModelError::new(error),
        lost_work,
    }
}
fn model(jobs: usize, stop_on_failure: bool) -> Lifecycle {
    Lifecycle {
        first_job: 50,
        jobs,
        stop_on_failure,
    }
}

#[derive(Default)]
struct Ledger {
    claims: BTreeSet<u64>,
    done: BTreeMap<u64, Summary>,
    receipts: BTreeSet<u64>,
    attempts: BTreeSet<u64>,
    returns: BTreeSet<u64>,
    success: BTreeSet<u64>,
    discards: BTreeSet<u64>,
    stopped: bool,
    lost: bool,
    finalized: bool,
}
impl Ledger {
    fn loss_matches(&self, state: &State) -> bool {
        state.lost_worker == self.lost
    }
    fn admissible(&self, m: &Lifecycle, i: &Input) -> bool {
        let active: Vec<_> = self.attempts.difference(&self.returns).copied().collect();
        !self.finalized
            && match i {
                Input::Claim { .. } => !self.stopped && self.claims.len() < m.jobs,
                Input::Complete { job_id, summary: s } => {
                    let valid = match s.outcome {
                        Outcome::FactoryError | Outcome::ModelError | Outcome::Panicked => {
                            !s.failure && [s.transitions, s.cases, s.skipped_checks] == [0, 0, 0]
                        }
                        _ => s.failure == (s.outcome == Outcome::Failure),
                    };
                    valid && self.claims.contains(job_id) && !self.done.contains_key(job_id)
                }
                Input::Receive { job_id } => {
                    active.is_empty()
                        && self.done.contains_key(job_id)
                        && !self.receipts.contains(job_id)
                }
                Input::SinkReturned { job_id, .. } => active == [*job_id],
                Input::WorkerFailed { .. } => true,
                Input::Finish => {
                    active.is_empty()
                        && (self.claims == self.receipts || self.lost)
                        && (self.stopped || self.claims.len() == m.jobs)
                }
            }
    }
    fn observe(&mut self, m: &Lifecycle, i: &Input, t: &Transition<State, Output>) {
        assert_eq!(
            t.disposition == Disposition::Accepted,
            self.admissible(m, i),
            "independent admission biconditional"
        );
        if t.disposition == Disposition::Accepted {
            match i {
                Input::Claim { stop: Some(_) } | Input::SinkReturned { error: Some(_), .. } => {
                    self.stopped = true
                }
                Input::Complete { summary: s, .. } => {
                    self.stopped |= matches!(s.outcome, Outcome::Cancelled | Outcome::Deadline)
                        || (m.stop_on_failure && s.outcome == Outcome::Failure)
                }
                Input::WorkerFailed { lost_work, .. } => {
                    self.stopped = true;
                    self.lost |= *lost_work;
                }
                Input::Finish => self.finalized = true,
                _ => {}
            }
            match i {
                Input::Complete { job_id, summary } => {
                    assert!(self.claims.contains(job_id));
                    assert!(self.done.insert(*job_id, summary.clone()).is_none());
                }
                Input::Receive { job_id } => {
                    assert!(self.done.contains_key(job_id));
                    assert!(self.receipts.insert(*job_id));
                }
                Input::SinkReturned { job_id, error } => {
                    assert!(self.attempts.contains(job_id));
                    assert!(self.returns.insert(*job_id));
                    if error.is_none() {
                        assert!(self.success.insert(*job_id));
                    }
                }
                _ => {}
            }
        }
        for o in &t.outputs {
            match o {
                Output::RunJob(id) => assert!(self.claims.insert(*id)),
                Output::Deliver(id) => {
                    assert!(self.receipts.contains(id));
                    assert!(self.attempts.insert(*id));
                }
                Output::Discard(id) => {
                    assert!(self.receipts.contains(id));
                    assert!(self.discards.insert(*id));
                }
            }
        }
        assert_eq!(t.state.report.started, self.claims.len());
        assert!(
            self.loss_matches(&t.state),
            "independent worker-loss identity"
        );
        assert_eq!(t.state.report.finished, self.receipts.len());
        assert_eq!(t.state.report.delivered, self.success.len());
        let active: BTreeSet<_> = self.attempts.difference(&self.returns).copied().collect();
        assert_eq!(active, t.state.delivering.into_iter().collect());
        assert!(self.attempts.is_disjoint(&self.discards));
        assert_eq!(
            self.attempts
                .union(&self.discards)
                .copied()
                .collect::<BTreeSet<_>>(),
            self.receipts
        );
        for (actual, field) in [
            (t.state.report.reported_transitions, 0),
            (t.state.report.reported_cases, 1),
            (t.state.report.reported_skipped_checks, 2),
        ] {
            let sum: u128 = self
                .receipts
                .iter()
                .map(|id| {
                    let s = &self.done[id];
                    u128::from([s.transitions, s.cases, s.skipped_checks][field])
                })
                .sum();
            assert_eq!(actual, sum.min(u64::MAX.into()) as u64);
        }
    }
}
fn run(
    name: &str,
    m: &Lifecycle,
    inputs: Vec<Input>,
    expected_accept: &[bool],
    expected_effects: &[Vec<Output>],
) -> State {
    assert_eq!(inputs.len(), expected_accept.len());
    assert_eq!(inputs.len(), expected_effects.len());
    let mut current = m.initial_state().unwrap();
    let initial = state(&current);
    let mut ledger = Ledger::default();
    let mut steps = Vec::new();
    for (index, input) in inputs.iter().enumerate() {
        let bytes = m.encode_input(input).unwrap();
        assert_eq!(m.decode_input(&bytes).unwrap(), *input);
        let t = m.step(&current, input).unwrap();
        assert_eq!(
            t.disposition == Disposition::Accepted,
            expected_accept[index],
            "{name}:{index}"
        );
        assert_eq!(t.outputs, expected_effects[index], "{name}:{index}");
        if !expected_accept[index] {
            assert_eq!(current, t.state);
            assert!(t.outputs.is_empty());
        }
        assert!(
            m.check_state(&t.state)
                .unwrap()
                .iter()
                .all(|c| !c.is_failure())
        );
        assert!(
            m.check_transition(&current, input, &t.as_ref())
                .unwrap()
                .iter()
                .all(|c| !c.is_failure())
        );
        ledger.observe(m, input, &t);
        let state_bytes = m.encode_state(&t.state).unwrap();
        assert_eq!(m.decode_state(&state_bytes).unwrap(), t.state);
        steps.push(obj(&[
            ("accepted", expected_accept[index].to_string()),
            (
                "reason",
                if let Disposition::Rejected(r) = &t.disposition {
                    q(r)
                } else {
                    "null".into()
                },
            ),
            ("effects", arr(t.outputs.iter().map(effect))),
            ("state", state(&t.state)),
            ("input_hex", hex(&bytes)),
            ("state_hex", hex(&state_bytes)),
            (
                "output_hex",
                arr(t.outputs.iter().map(|o| hex(&m.encode_output(o).unwrap()))),
            ),
        ]));
        current = t.state;
    }
    println!(
        "{}",
        obj(&[
            ("schema", q("native-campaign-v1")),
            ("kind", q("lifecycle")),
            ("case", q(name)),
            (
                "config",
                obj(&[
                    ("first_job", n(m.first_job)),
                    ("jobs", n(m.jobs)),
                    ("stop_on_failure", m.stop_on_failure.to_string())
                ])
            ),
            ("initial", initial),
            ("events", arr(inputs.iter().map(event))),
            ("steps", arr(steps))
        ])
    );
    current
}

#[test]
fn n06_duplicate_stale_ack_full_state_and_canonical_bytes() {
    let m = model(2, false);
    let inputs = vec![
        c(),
        c(),
        complete(50, Outcome::Completed),
        complete(51, Outcome::Completed),
        complete(50, Outcome::Completed),
        receive(50),
        receive(50),
        receive(51),
        ack(51, None),
        ack(50, None),
        ack(50, None),
        ack(50, Some("stale")),
        receive(51),
        ack(50, Some("stale")),
        ack(51, None),
        Input::Finish,
        c(),
        Input::Finish,
    ];
    let accept = [
        true, true, true, true, false, true, false, false, false, true, false, false, true, false,
        true, true, false, false,
    ];
    let mut effects = vec![vec![]; inputs.len()];
    effects[0] = vec![Output::RunJob(50)];
    effects[1] = vec![Output::RunJob(51)];
    effects[5] = vec![Output::Deliver(50)];
    effects[12] = vec![Output::Deliver(51)];
    let final_state = run("duplicate-stale-ack", &m, inputs, &accept, &effects);
    assert_eq!(
        (
            final_state.report.started,
            final_state.report.finished,
            final_state.report.delivered
        ),
        (2, 2, 2)
    );
    assert_eq!(
        (
            final_state.report.reported_transitions,
            final_state.report.reported_cases,
            final_state.report.reported_skipped_checks
        ),
        (4, 2, 2)
    );
    assert!(final_state.finalized);
    assert_eq!(m.encode_input(&c()).unwrap(), [0, 0]);
    assert_eq!(m.encode_input(&Input::Finish).unwrap(), [5]);
    assert_eq!(
        m.encode_output(&Output::Deliver(50)).unwrap(),
        [1, 50, 0, 0, 0, 0, 0, 0, 0]
    );
}

#[test]
fn n06_first_sink_error_discard_and_missing_worker_projection() {
    let inputs = vec![
        c(),
        c(),
        complete(50, Outcome::Completed),
        complete(51, Outcome::Cancelled),
        receive(50),
        ack(50, Some("first")),
        worker("later", false),
        ack(50, Some("replacement")),
        receive(51),
        ack(51, None),
        Input::Finish,
    ];
    let mut effects = vec![vec![]; inputs.len()];
    effects[0] = vec![Output::RunJob(50)];
    effects[1] = vec![Output::RunJob(51)];
    effects[4] = vec![Output::Deliver(50)];
    effects[8] = vec![Output::Discard(51)];
    let s = run(
        "sink-error-drain",
        &model(3, false),
        inputs,
        &[
            true, true, true, true, true, true, true, false, true, false, true,
        ],
        &effects,
    );
    assert_eq!(
        s.report.termination,
        CampaignTermination::CallbackError(ModelError::new("first"))
    );
    assert_eq!(s.stop, Some(Stop::Worker));
    assert_eq!(
        (
            s.report.started,
            s.report.finished,
            s.report.delivered,
            s.report.not_started
        ),
        (2, 2, 0, 1)
    );
    assert!(s.report.accounting_complete);
    let inputs = vec![
        c(),
        worker("spawn", false),
        Input::Finish,
        worker("lost", true),
        Input::Finish,
    ];
    let s = run(
        "lost-worker-finish",
        &model(2, false),
        inputs,
        &[true, true, false, true, true],
        &[vec![Output::RunJob(50)], vec![], vec![], vec![], vec![]],
    );
    assert_eq!(
        s.report.termination,
        CampaignTermination::WorkerError(ModelError::new("lost"))
    );
    assert!(!s.report.accounting_complete);
    assert_eq!(s.pending, BTreeMap::from([(50, Pending::Running)]));
}

#[test]
fn n06_saturation_exact_limit_and_each_overflow_counter() {
    for overflow in 0..4 {
        let mut inputs = Vec::new();
        let mut effects = Vec::new();
        for id in 50..52 {
            let counts = if id == 50 {
                [u64::MAX; 3]
            } else {
                std::array::from_fn(|index| u64::from(index == overflow))
            };
            inputs.extend([
                c(),
                Input::Complete {
                    job_id: id,
                    summary: Summary {
                        outcome: Outcome::Completed,
                        failure: false,
                        transitions: counts[0],
                        cases: counts[1],
                        skipped_checks: counts[2],
                    },
                },
                receive(id),
                ack(id, None),
            ]);
            effects.extend([
                vec![Output::RunJob(id)],
                vec![],
                vec![Output::Deliver(id)],
                vec![],
            ]);
        }
        inputs.push(Input::Finish);
        effects.push(vec![]);
        let accepts = vec![true; inputs.len()];
        let s = run(
            &format!("saturation-{overflow}"),
            &model(2, false),
            inputs,
            &accepts,
            &effects,
        );
        assert_eq!(s.report.accounting_complete, overflow == 3);
        assert_eq!(
            (
                s.report.reported_transitions,
                s.report.reported_cases,
                s.report.reported_skipped_checks
            ),
            (u64::MAX, u64::MAX, u64::MAX)
        );
    }
}

struct Fault(Lifecycle);
impl Model for Fault {
    type State = State;
    type Input = Input;
    type Output = Output;
    fn metadata(&self) -> crate::ModelMetadata {
        let mut m = self.0.metadata();
        m.build.push_str(":duplicate-delivery");
        m
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        self.0.initial_state()
    }
    fn step(&self, s: &State, i: &Input) -> Result<Transition<State, Output>, ModelError> {
        let mut t = self.0.step(s, i)?;
        if let Some(Output::Deliver(id)) = t.outputs.first() {
            t.outputs.push(Output::Deliver(*id));
        }
        Ok(t)
    }
    fn check_state(&self, s: &State) -> Result<Vec<Check>, ModelError> {
        self.0.check_state(s)
    }
    fn check_transition(
        &self,
        b: &State,
        i: &Input,
        t: &TransitionRef<'_, State, Output>,
    ) -> Result<Vec<Check>, ModelError> {
        self.0.check_transition(b, i, t)
    }
}
impl Enumerate for Fault {
    fn inputs(&self, s: &State) -> Result<Vec<Input>, ModelError> {
        let mut inputs = vec![c()];
        for (id, p) in &s.pending {
            inputs.push(match p {
                Pending::Running => complete(*id, Outcome::Completed),
                Pending::Ready(_) => receive(*id),
            });
        }
        Ok(inputs)
    }
}
impl Generate for Fault {
    fn generate(&self, s: &State, rng: &mut Rng) -> Result<Option<Input>, ModelError> {
        let mut inputs = self.inputs(s)?;
        Ok(rng.index(inputs.len()).map(|i| inputs.swap_remove(i)))
    }
}
#[test]
fn n06_faulted_fuzz_uses_actual_reducer_not_healthy_fuzz_as_substitute() {
    let m = Fault(model(2, false));
    let r = crate::explore::fuzz(
        &m,
        crate::explore::FuzzConfig {
            seed: 0,
            cases: 256,
            max_steps: 64,
            max_transitions: 16384,
            mutation_percent: 50,
        },
    )
    .unwrap();
    assert_eq!(r.termination, crate::explore::FuzzTermination::FailureFound);
    assert!(
        r.failure
            .unwrap()
            .violations
            .iter()
            .any(|v| v.check.id == "campaign.effects")
    );
}

#[test]
fn n06_worker_error_overwrites_internal_stop_but_retains_error_authority() {
    let inputs = vec![
        c(),
        c(),
        complete(50, Outcome::Cancelled),
        worker("w", false),
        complete(51, Outcome::Deadline),
        receive(50),
        ack(50, None),
        receive(51),
        ack(51, None),
        Input::Finish,
    ];
    let expected_stops = [
        None,
        None,
        Some(Stop::Cancelled),
        Some(Stop::Worker),
        Some(Stop::Deadline),
        Some(Stop::Deadline),
        Some(Stop::Deadline),
        Some(Stop::Deadline),
        Some(Stop::Deadline),
        Some(Stop::Deadline),
    ];
    let m = model(2, false);
    let mut s = m.initial_state().unwrap();
    for (input, expected) in inputs.iter().zip(expected_stops) {
        s = m.step(&s, input).unwrap().state;
        assert_eq!(s.stop, expected);
    }
    let mut effects = vec![vec![]; inputs.len()];
    effects[0] = vec![Output::RunJob(50)];
    effects[1] = vec![Output::RunJob(51)];
    effects[5] = vec![Output::Deliver(50)];
    effects[7] = vec![Output::Deliver(51)];
    let s = run("worker-stop-overwrite", &m, inputs, &[true; 10], &effects);
    assert_eq!(
        s.report.termination,
        CampaignTermination::WorkerError(ModelError::new("w"))
    );
    assert_eq!(s.report.delivered, 2);
    assert!(s.report.accounting_complete);
}

#[test]
fn n06_false_rejection_is_not_accepted_as_inert_correctness() {
    let m = model(1, false);
    let ledger = Ledger::default();
    let initial = m.initial_state().unwrap();
    let input = c();
    let wrong = Transition {
        state: initial.clone(),
        outputs: vec![],
        disposition: Disposition::Rejected("injected false rejection".into()),
    };
    assert!(ledger.admissible(&m, &input));
    assert_ne!(
        wrong.disposition == Disposition::Accepted,
        ledger.admissible(&m, &input)
    );
    assert!(
        m.check_transition(&initial, &input, &wrong.as_ref())
            .unwrap()
            .iter()
            .any(|c| c.id == "campaign.admission" && c.is_failure())
    );
}

#[test]
fn n06_fabricated_worker_loss_after_model_error_does_not_enable_finish() {
    let m = model(2, false);
    let mut current = m.initial_state().unwrap();
    let mut ledger = Ledger::default();
    for input in [
        c(),
        c(),
        complete(50, Outcome::ModelError),
        receive(50),
        ack(50, None),
        worker("start failed", false),
    ] {
        let t = m.step(&current, &input).unwrap();
        ledger.observe(&m, &input, &t);
        current = t.state;
    }
    assert!(!current.report.accounting_complete);
    assert!(!current.lost_worker);
    assert!(!ledger.admissible(&m, &Input::Finish));
    assert!(matches!(
        m.step(&current, &Input::Finish).unwrap().disposition,
        Disposition::Rejected(_)
    ));
    let mut corrupted = current.clone();
    corrupted.lost_worker = true;
    // The original state properties alone permit this fabricated history because
    // ModelError already lost counters. The independent ledger must reject it.
    assert!(
        m.check_state(&corrupted)
            .unwrap()
            .iter()
            .all(|c| !c.is_failure())
    );
    assert!(!ledger.loss_matches(&corrupted));
    assert!(!ledger.admissible(&m, &Input::Finish));
    assert_eq!(
        m.step(&corrupted, &Input::Finish).unwrap().disposition,
        Disposition::Accepted
    );
    // A real lost-work event supplies the otherwise missing causal evidence.
    let input = worker("lost", true);
    let t = m.step(&current, &input).unwrap();
    ledger.observe(&m, &input, &t);
    current = t.state;
    assert!(ledger.loss_matches(&current));
    assert!(ledger.admissible(&m, &Input::Finish));
    let t = m.step(&current, &Input::Finish).unwrap();
    ledger.observe(&m, &Input::Finish, &t);
    assert!(t.state.finalized);
}

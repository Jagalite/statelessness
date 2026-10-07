//! Self-exploration runs the production reducer, with a separate event ledger.
//! The ledger records observable protocol facts rather than implementing a
//! second scheduler. Threads, channel capacity, and clocks remain executor tests.

use std::collections::{BTreeMap, BTreeSet};

use super::*;
use crate::execution::{ReplayOptions, ReplayOutcome, record, replay};
use crate::explore::{
    FuzzConfig, FuzzTermination, SearchConfig, SearchTermination, ShrinkConfig, ShrinkTermination,
    enumerate, fuzz, shrink,
};
use crate::trace::{ReadLimits, RunConfig, Termination, Trace};
use crate::{Enumerate, Generate, ModelCodec, Rng};

fn lifecycle(jobs: usize, stop_on_failure: bool) -> Lifecycle {
    Lifecycle {
        first_job: 50,
        jobs,
        stop_on_failure,
    }
}

fn summary(outcome: Outcome) -> Summary {
    let completed = !matches!(
        outcome,
        Outcome::FactoryError | Outcome::ModelError | Outcome::Panicked
    );
    Summary {
        outcome,
        failure: outcome == Outcome::Failure,
        transitions: if completed { 2 } else { 0 },
        cases: u64::from(completed),
        skipped_checks: u64::from(completed),
    }
}

fn complete(job_id: u64, outcome: Outcome) -> Input {
    Input::Complete {
        job_id,
        summary: summary(outcome),
    }
}

fn sink(job_id: u64, error: Option<&str>) -> Input {
    Input::SinkReturned {
        job_id,
        error: error.map(ModelError::new),
    }
}

fn assert_checks(checks: Vec<Check>) {
    assert!(checks.iter().all(|check| !check.is_failure()), "{checks:?}");
}

fn apply(model: &Lifecycle, state: &mut State, input: Input) -> Vec<Output> {
    let transition = model.step(state, &input).unwrap();
    assert_eq!(transition.disposition, Disposition::Accepted, "{input:?}");
    assert_checks(model.check_state(&transition.state).unwrap());
    assert_checks(
        model
            .check_transition(state, &input, &transition.as_ref())
            .unwrap(),
    );
    *state = transition.state;
    transition.outputs
}

fn reject(model: &Lifecycle, state: &State, input: Input) {
    let transition = model.step(state, &input).unwrap();
    assert!(
        matches!(transition.disposition, Disposition::Rejected(_)),
        "{input:?}"
    );
    assert_eq!(&transition.state, state);
    assert!(transition.outputs.is_empty());
    assert_checks(
        model
            .check_transition(state, &input, &transition.as_ref())
            .unwrap(),
    );
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
struct Ledger {
    claims: BTreeSet<u64>,
    completions: BTreeMap<u64, Summary>,
    receipts: BTreeSet<u64>,
    sink_attempts: BTreeSet<u64>,
    sink_returns: BTreeSet<u64>,
    sink_successes: BTreeSet<u64>,
    discards: BTreeSet<u64>,
    first_sink_error: Option<ModelError>,
    last_worker_error: Option<ModelError>,
    cancelled: bool,
    deadline: bool,
    failure_stop: bool,
    lost_work: bool,
    invalid_protocol: bool,
}

impl Ledger {
    fn observed_stop(&self) -> bool {
        self.cancelled
            || self.deadline
            || self.failure_stop
            || self.first_sink_error.is_some()
            || self.last_worker_error.is_some()
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct AuditedState {
    core: State,
    ledger: Ledger,
}

struct AuditedLifecycle {
    core: Lifecycle,
    outcomes: Vec<Outcome>,
}

impl AuditedLifecycle {
    fn new(jobs: usize, stop_on_failure: bool) -> Self {
        Self {
            core: lifecycle(jobs, stop_on_failure),
            outcomes: vec![
                Outcome::Completed,
                Outcome::Failure,
                Outcome::Cancelled,
                Outcome::Deadline,
                Outcome::FactoryError,
                Outcome::ModelError,
                Outcome::Panicked,
            ],
        }
    }

    /// A finite alphabet includes stale, duplicate, and out-of-range events.
    /// There are no event counters in state: inert inputs remain self loops.
    fn alphabet(&self) -> Vec<Input> {
        let mut inputs = vec![
            Input::Claim { stop: None },
            Input::Claim {
                stop: Some(ExternalStop::Cancelled),
            },
            Input::Claim {
                stop: Some(ExternalStop::Deadline),
            },
            Input::WorkerFailed {
                error: ModelError::new("worker"),
                lost_work: false,
            },
            Input::WorkerFailed {
                error: ModelError::new("worker"),
                lost_work: true,
            },
            Input::Finish,
        ];
        // One extra ID was never allocated and must never become a real job.
        for job_id in self.core.first_job..=self.core.first_job + self.core.jobs as u64 {
            inputs.extend(
                self.outcomes
                    .iter()
                    .map(|outcome| complete(job_id, *outcome)),
            );
            inputs.push(Input::Receive { job_id });
            inputs.push(sink(job_id, None));
            inputs.push(sink(job_id, Some("sink")));
        }
        inputs
    }

    /// Advance the independent event ledger from an observed core transition.
    /// Fault controls use this same path without reimplementing the oracle.
    fn observe_transition(
        &self,
        before: &AuditedState,
        input: &Input,
        actual: Transition<State, Output>,
    ) -> Transition<AuditedState, Output> {
        let mut ledger = before.ledger.clone();
        if actual.disposition == Disposition::Accepted {
            match input {
                Input::Claim {
                    stop: Some(ExternalStop::Cancelled),
                } => ledger.cancelled = true,
                Input::Claim {
                    stop: Some(ExternalStop::Deadline),
                } => ledger.deadline = true,
                Input::Complete { job_id, summary } => {
                    ledger.invalid_protocol |= !ledger.claims.contains(job_id)
                        || ledger
                            .completions
                            .insert(*job_id, summary.clone())
                            .is_some();
                    ledger.cancelled |= summary.outcome == Outcome::Cancelled;
                    ledger.deadline |= summary.outcome == Outcome::Deadline;
                    ledger.failure_stop |=
                        self.core.stop_on_failure && summary.outcome == Outcome::Failure;
                }
                Input::Receive { job_id } => {
                    ledger.invalid_protocol |= !ledger.completions.contains_key(job_id)
                        || !ledger.receipts.insert(*job_id);
                }
                Input::SinkReturned { job_id, error } => {
                    ledger.invalid_protocol |= !ledger.sink_attempts.contains(job_id)
                        || !ledger.sink_returns.insert(*job_id);
                    if let Some(error) = error {
                        if ledger.first_sink_error.is_none() {
                            ledger.first_sink_error = Some(error.clone());
                        }
                    } else {
                        ledger.invalid_protocol |= !ledger.sink_successes.insert(*job_id);
                    }
                }
                Input::WorkerFailed { error, lost_work } => {
                    ledger.last_worker_error = Some(error.clone());
                    ledger.lost_work |= lost_work;
                }
                _ => {}
            }
        }
        for effect in &actual.outputs {
            match *effect {
                Output::RunJob(id) => {
                    ledger.invalid_protocol |= !ledger.claims.insert(id)
                        || before.ledger.observed_stop()
                        || before.core.finalized;
                }
                Output::Deliver(id) => {
                    ledger.invalid_protocol |= !ledger.receipts.contains(&id)
                        || !ledger.sink_attempts.insert(id)
                        || ledger.first_sink_error.is_some()
                        || ledger.discards.contains(&id);
                }
                Output::Discard(id) => {
                    ledger.invalid_protocol |= !ledger.receipts.contains(&id)
                        || !ledger.discards.insert(id)
                        || ledger.first_sink_error.is_none()
                        || ledger.sink_attempts.contains(&id);
                }
            }
        }
        Transition {
            state: AuditedState {
                core: actual.state,
                ledger,
            },
            outputs: actual.outputs,
            disposition: actual.disposition,
        }
    }
}

impl Model for AuditedLifecycle {
    type State = AuditedState;
    type Input = Input;
    type Output = Output;

    fn metadata(&self) -> ModelMetadata {
        self.core.metadata()
    }

    fn initial_state(&self) -> Result<Self::State, ModelError> {
        Ok(AuditedState {
            core: self.core.initial_state()?,
            ledger: Ledger::default(),
        })
    }

    fn step(
        &self,
        before: &Self::State,
        input: &Input,
    ) -> Result<Transition<Self::State, Output>, ModelError> {
        let actual = self.core.step(&before.core, input)?;
        Ok(self.observe_transition(before, input, actual))
    }

    fn check_state(&self, state: &Self::State) -> Result<Vec<Check>, ModelError> {
        let mut checks = self.core.check_state(&state.core)?;
        let ledger = &state.ledger;
        let report = &state.core.report;
        let received: Vec<_> = ledger
            .receipts
            .iter()
            .filter_map(|id| ledger.completions.get(id))
            .collect();
        let failures = received.iter().filter(|s| s.failure).count();
        let errors = received
            .iter()
            .filter(|s| {
                matches!(
                    s.outcome,
                    Outcome::FactoryError | Outcome::ModelError | Outcome::Panicked
                )
            })
            .count();
        let transitions: u128 = received.iter().map(|s| u128::from(s.transitions)).sum();
        let cases: u128 = received.iter().map(|s| u128::from(s.cases)).sum();
        let skipped: u128 = received.iter().map(|s| u128::from(s.skipped_checks)).sum();
        let complete = !ledger.lost_work
            && received
                .iter()
                .all(|s| !matches!(s.outcome, Outcome::ModelError | Outcome::Panicked))
            && [transitions, cases, skipped]
                .into_iter()
                .all(|sum| sum <= u128::from(u64::MAX))
            && (!state.core.finalized || ledger.claims == ledger.receipts);
        let expected_termination = if let Some(error) = &ledger.first_sink_error {
            CampaignTermination::CallbackError(error.clone())
        } else if let Some(error) = &ledger.last_worker_error {
            CampaignTermination::WorkerError(error.clone())
        } else if ledger.cancelled {
            CampaignTermination::Cancelled
        } else if ledger.deadline {
            CampaignTermination::Deadline
        } else if ledger.failure_stop {
            CampaignTermination::FailureFound
        } else {
            CampaignTermination::JobsCompleted
        };
        checks.extend([
            check("audit.protocol", !ledger.invalid_protocol),
            check(
                "audit.stop_observed",
                state.core.stop.is_some() == ledger.observed_stop(),
            ),
            check(
                "audit.active_sink",
                ledger.sink_returns.is_subset(&ledger.sink_attempts)
                    && ledger
                        .sink_attempts
                        .difference(&ledger.sink_returns)
                        .copied()
                        .collect::<BTreeSet<_>>()
                        == state.core.delivering.into_iter().collect(),
            ),
            check(
                "audit.identity_accounting",
                report.started == ledger.claims.len()
                    && report.finished == ledger.receipts.len()
                    && report.delivered == ledger.sink_successes.len()
                    && report.failures == failures
                    && report.errors == errors,
            ),
            check(
                "audit.work_accounting",
                report.reported_transitions == transitions.min(u128::from(u64::MAX)) as u64
                    && report.reported_cases == cases.min(u128::from(u64::MAX)) as u64
                    && report.reported_skipped_checks == skipped.min(u128::from(u64::MAX)) as u64
                    && report.accounting_complete == complete,
            ),
            check(
                "audit.receipt_has_one_destination",
                ledger.sink_attempts.is_disjoint(&ledger.discards)
                    && ledger
                        .sink_attempts
                        .union(&ledger.discards)
                        .copied()
                        .collect::<BTreeSet<_>>()
                        == ledger.receipts,
            ),
            check(
                "audit.stop_precedence",
                !state.core.finalized || report.termination == expected_termination,
            ),
        ]);
        Ok(checks)
    }

    fn check_transition(
        &self,
        before: &Self::State,
        input: &Input,
        transition: &TransitionRef<'_, Self::State, Output>,
    ) -> Result<Vec<Check>, ModelError> {
        self.core.check_transition(
            &before.core,
            input,
            &TransitionRef {
                state: &transition.state.core,
                outputs: transition.outputs,
                disposition: transition.disposition,
            },
        )
    }
}

impl Enumerate for AuditedLifecycle {
    fn inputs(&self, state: &Self::State) -> Result<Vec<Input>, ModelError> {
        Ok(if state.core.finalized {
            Vec::new()
        } else {
            self.alphabet()
        })
    }
}

impl Generate for AuditedLifecycle {
    fn generate(&self, state: &Self::State, rng: &mut Rng) -> Result<Option<Input>, ModelError> {
        // Most choices progress a live job; one quarter deliberately select any
        // protocol event, including stale events after finalization.
        let mut choices = Vec::new();
        if !state.core.finalized && rng.index(4) != Some(0) {
            if state.core.stop.is_none() && state.core.report.started < self.core.jobs {
                choices.push(Input::Claim { stop: None });
            }
            for (&id, pending) in &state.core.pending {
                match pending {
                    Pending::Running => {
                        choices.extend(self.outcomes.iter().map(|outcome| complete(id, *outcome)))
                    }
                    Pending::Ready(_) if state.core.delivering.is_none() => {
                        choices.push(Input::Receive { job_id: id })
                    }
                    _ => {}
                }
            }
            if let Some(id) = state.core.delivering {
                choices.push(sink(id, None));
                choices.push(sink(id, Some("sink")));
            } else if state.core.pending.is_empty() {
                choices.push(Input::Finish);
            }
        }
        if choices.is_empty() {
            choices = self.alphabet();
        }
        Ok(rng
            .index(choices.len())
            .map(|index| choices.swap_remove(index)))
    }
}

fn audited_prefix(model: &AuditedLifecycle, inputs: Vec<Input>) -> AuditedState {
    let mut state = model.initial_state().unwrap();
    for input in inputs {
        let transition = model.step(&state, &input).unwrap();
        assert_eq!(transition.disposition, Disposition::Accepted);
        assert_checks(model.check_state(&transition.state).unwrap());
        assert_checks(
            model
                .check_transition(&state, &input, &transition.as_ref())
                .unwrap(),
        );
        state = transition.state;
    }
    state
}

fn assert_failed(checks: Vec<Check>, id: &str) {
    assert!(
        checks
            .iter()
            .any(|check| check.id == id && check.is_failure()),
        "expected failure {id}: {checks:?}"
    );
}

#[test]
fn rejection_of_enabled_lifecycle_events_is_detected() {
    let model = AuditedLifecycle::new(1, false);
    let running = vec![Input::Claim { stop: None }];
    let mut ready = running.clone();
    ready.push(complete(50, Outcome::Completed));
    let mut delivering = ready.clone();
    delivering.push(Input::Receive { job_id: 50 });
    let mut delivered = delivering.clone();
    delivered.push(sink(50, None));
    let cases = [
        (vec![], Input::Claim { stop: None }),
        (
            vec![],
            Input::Claim {
                stop: Some(ExternalStop::Cancelled),
            },
        ),
        (
            vec![],
            Input::Claim {
                stop: Some(ExternalStop::Deadline),
            },
        ),
        (running, complete(50, Outcome::Completed)),
        (ready, Input::Receive { job_id: 50 }),
        (delivering.clone(), sink(50, None)),
        (delivering, sink(50, Some("sink"))),
        (
            vec![],
            Input::WorkerFailed {
                error: ModelError::new("worker"),
                lost_work: false,
            },
        ),
        (delivered, Input::Finish),
    ];
    for (prefix, input) in cases {
        let before = audited_prefix(&model, prefix);
        assert_eq!(
            model.core.step(&before.core, &input).unwrap().disposition,
            Disposition::Accepted,
            "control event must be enabled: {input:?}"
        );
        let faulty = model.observe_transition(
            &before,
            &input,
            Transition {
                state: before.core.clone(),
                outputs: Vec::new(),
                disposition: Disposition::Rejected("injected incorrect rejection".into()),
            },
        );
        assert_failed(
            model
                .check_transition(&before, &input, &faulty.as_ref())
                .unwrap(),
            "campaign.admission",
        );
    }
}

#[test]
fn stale_error_acknowledgement_is_detected_independently() {
    let model = AuditedLifecycle::new(2, false);
    for another_delivery_active in [false, true] {
        let mut prefix = vec![
            Input::Claim { stop: None },
            complete(50, Outcome::Completed),
            Input::Receive { job_id: 50 },
            sink(50, None),
        ];
        if another_delivery_active {
            prefix.extend([
                Input::Claim { stop: None },
                complete(51, Outcome::Completed),
                Input::Receive { job_id: 51 },
            ]);
        }
        let before = audited_prefix(&model, prefix);
        let input = sink(50, Some("stale sink error"));
        let mut core = before.core.clone();
        core.report.termination =
            CampaignTermination::CallbackError(ModelError::new("stale sink error"));
        core.stop = Some(Stop::Sink);
        core.delivering = None;
        let faulty =
            model.observe_transition(&before, &input, Transition::accepted(core, Vec::new()));
        assert_failed(
            model
                .check_transition(&before, &input, &faulty.as_ref())
                .unwrap(),
            "campaign.admission",
        );
        // The independent history catches the repeated acknowledgement even if
        // the core's admission property is accidentally weakened in the future.
        assert_failed(model.check_state(&faulty.state).unwrap(), "audit.protocol");
        if another_delivery_active {
            assert_failed(
                model.check_state(&faulty.state).unwrap(),
                "audit.active_sink",
            );
        }
    }
}

#[test]
fn unobserved_stop_is_detected_before_additional_claims() {
    let model = AuditedLifecycle::new(2, true);
    let cases = [
        (
            vec![],
            Input::Claim {
                stop: Some(ExternalStop::Cancelled),
            },
        ),
        (
            vec![],
            Input::Claim {
                stop: Some(ExternalStop::Deadline),
            },
        ),
        (
            vec![Input::Claim { stop: None }],
            complete(50, Outcome::Failure),
        ),
        (
            vec![Input::Claim { stop: None }],
            complete(50, Outcome::Cancelled),
        ),
        (
            vec![Input::Claim { stop: None }],
            complete(50, Outcome::Deadline),
        ),
        (
            vec![],
            Input::WorkerFailed {
                error: ModelError::new("worker"),
                lost_work: false,
            },
        ),
        (
            vec![
                Input::Claim { stop: None },
                complete(50, Outcome::Completed),
                Input::Receive { job_id: 50 },
            ],
            sink(50, Some("sink")),
        ),
    ];
    for (prefix, input) in cases {
        let before = audited_prefix(&model, prefix);
        let mut faulty_core = model.core.step(&before.core, &input).unwrap();
        assert_eq!(faulty_core.disposition, Disposition::Accepted);
        assert!(faulty_core.state.stop.is_some());
        faulty_core.state.stop = None;
        let faulty = model.observe_transition(&before, &input, faulty_core);
        assert_failed(
            model.check_state(&faulty.state).unwrap(),
            "audit.stop_observed",
        );

        // A scheduler that forgets the stop could still report the right
        // termination at Finish. Catch its extra claim using observed history.
        let claim = Input::Claim { stop: None };
        let extra_core = model.core.step(&faulty.state.core, &claim).unwrap();
        assert_eq!(extra_core.disposition, Disposition::Accepted);
        assert!(matches!(extra_core.outputs.as_slice(), [Output::RunJob(_)]));
        let extra = model.observe_transition(&faulty.state, &claim, extra_core);
        assert_failed(model.check_state(&extra.state).unwrap(), "audit.protocol");
    }
}

#[test]
fn finite_campaign_protocol_graph_is_exhausted() {
    for stop_on_failure in [false, true] {
        let model = AuditedLifecycle::new(2, stop_on_failure);
        let report = enumerate(
            &model,
            SearchConfig {
                max_states: 200_000,
                max_transitions: 8_000_000,
                max_depth: 20,
            },
        )
        .unwrap();
        println!(
            "campaign self-model jobs=2 stop_on_failure={stop_on_failure}: {:?}, states={}, transitions={}, depth={}",
            report.termination, report.states, report.transitions, report.max_depth_reached
        );
        assert!(report.failure.is_none(), "{:?}", report.failure);
        assert_eq!(report.termination, SearchTermination::GraphExhausted);
        assert_eq!(report.skipped_checks, 0);
    }
}

#[test]
fn fixed_seed_campaign_protocol_fuzz_uses_independent_ledger() {
    for stop_on_failure in [false, true] {
        let model = AuditedLifecycle::new(4, stop_on_failure);
        let report = fuzz(
            &model,
            FuzzConfig {
                seed: 0x5354_4154_454c_4553,
                cases: 256,
                max_steps: 64,
                max_transitions: 16_384,
                mutation_percent: 50,
            },
        )
        .unwrap();
        println!(
            "campaign self-fuzz stop_on_failure={stop_on_failure}: {:?}, cases={}, transitions={}",
            report.termination, report.cases, report.transitions
        );
        assert!(report.failure.is_none(), "{:?}", report.failure);
        assert_eq!(report.termination, FuzzTermination::CasesCompleted);
        assert_eq!(report.skipped_checks, 0);
    }
}

#[test]
fn hostile_events_cannot_duplicate_work_delivery_or_mutate_terminal_state() {
    let model = lifecycle(1, false);
    let mut state = model.initial_state().unwrap();
    reject(&model, &state, complete(50, Outcome::Completed));
    reject(&model, &state, Input::Receive { job_id: 50 });
    reject(&model, &state, sink(50, None));
    reject(&model, &state, Input::Finish);
    assert_eq!(
        apply(&model, &mut state, Input::Claim { stop: None }),
        [Output::RunJob(50)]
    );
    reject(&model, &state, Input::Claim { stop: None });
    reject(&model, &state, complete(51, Outcome::Completed));
    reject(&model, &state, Input::Receive { job_id: 50 });
    for invalid in [
        Summary {
            failure: true,
            ..summary(Outcome::FactoryError)
        },
        Summary {
            transitions: 1,
            ..summary(Outcome::Panicked)
        },
        Summary {
            failure: false,
            ..summary(Outcome::Failure)
        },
        Summary {
            failure: true,
            ..summary(Outcome::Completed)
        },
    ] {
        reject(
            &model,
            &state,
            Input::Complete {
                job_id: 50,
                summary: invalid,
            },
        );
    }
    apply(&model, &mut state, complete(50, Outcome::Completed));
    reject(&model, &state, complete(50, Outcome::Panicked));
    assert_eq!(
        apply(&model, &mut state, Input::Receive { job_id: 50 }),
        [Output::Deliver(50)]
    );
    reject(&model, &state, Input::Receive { job_id: 50 });
    reject(&model, &state, sink(51, None));
    reject(&model, &state, Input::Finish);
    apply(&model, &mut state, sink(50, None));
    reject(&model, &state, sink(50, None));
    apply(&model, &mut state, Input::Finish);
    for input in AuditedLifecycle::new(1, false).alphabet() {
        reject(&model, &state, input);
    }
    assert_eq!(
        (
            state.report.started,
            state.report.finished,
            state.report.delivered
        ),
        (1, 1, 1)
    );
}

#[test]
fn active_job_stop_precedence_is_independent_of_completion_order() {
    for order in [
        [0, 1, 2],
        [0, 2, 1],
        [1, 0, 2],
        [1, 2, 0],
        [2, 0, 1],
        [2, 1, 0],
    ] {
        let model = lifecycle(4, true);
        let mut state = model.initial_state().unwrap();
        for _ in 0..3 {
            apply(&model, &mut state, Input::Claim { stop: None });
        }
        let outcomes = [Outcome::Failure, Outcome::Deadline, Outcome::Cancelled];
        for index in order {
            apply(
                &model,
                &mut state,
                complete(50 + index as u64, outcomes[index]),
            );
            reject(&model, &state, Input::Claim { stop: None });
        }
        for job_id in 50..53 {
            apply(&model, &mut state, Input::Receive { job_id });
            apply(&model, &mut state, sink(job_id, None));
        }
        apply(&model, &mut state, Input::Finish);
        assert_eq!(state.report.termination, CampaignTermination::Cancelled);
        assert_eq!(
            (
                state.report.started,
                state.report.finished,
                state.report.not_started
            ),
            (3, 3, 1)
        );
    }
}

#[test]
fn first_sink_error_survives_drain_and_worker_failure() {
    for worker_first in [false, true] {
        let model = lifecycle(3, false);
        let mut state = model.initial_state().unwrap();
        for _ in 0..2 {
            apply(&model, &mut state, Input::Claim { stop: None });
        }
        apply(&model, &mut state, complete(50, Outcome::Completed));
        apply(&model, &mut state, complete(51, Outcome::Cancelled));
        assert_eq!(
            apply(&model, &mut state, Input::Receive { job_id: 50 }),
            [Output::Deliver(50)]
        );
        reject(&model, &state, Input::Receive { job_id: 51 });
        let worker = Input::WorkerFailed {
            error: ModelError::new("worker failure"),
            lost_work: false,
        };
        if worker_first {
            apply(&model, &mut state, worker.clone());
        }
        apply(&model, &mut state, sink(50, Some("first sink error")));
        if !worker_first {
            apply(&model, &mut state, worker);
        }
        reject(&model, &state, sink(50, Some("replacement sink error")));
        reject(&model, &state, Input::Claim { stop: None });
        assert_eq!(
            apply(&model, &mut state, Input::Receive { job_id: 51 }),
            [Output::Discard(51)]
        );
        reject(&model, &state, sink(51, None));
        apply(&model, &mut state, Input::Finish);
        assert_eq!(
            state.report.termination,
            CampaignTermination::CallbackError(ModelError::new("first sink error"))
        );
        assert_eq!(
            (
                state.report.started,
                state.report.finished,
                state.report.delivered
            ),
            (2, 2, 0)
        );
        assert!(state.report.accounting_complete);
    }
}

#[test]
fn missing_worker_outcome_requires_explicit_accounting_loss() {
    let model = lifecycle(2, false);
    let mut state = model.initial_state().unwrap();
    apply(&model, &mut state, Input::Claim { stop: None });
    apply(
        &model,
        &mut state,
        Input::WorkerFailed {
            error: ModelError::new("spawn"),
            lost_work: false,
        },
    );
    reject(&model, &state, Input::Finish);
    apply(
        &model,
        &mut state,
        Input::WorkerFailed {
            error: ModelError::new("lost"),
            lost_work: true,
        },
    );
    apply(&model, &mut state, Input::Finish);
    assert_eq!(
        (
            state.report.started,
            state.report.finished,
            state.report.not_started
        ),
        (1, 0, 1)
    );
    assert!(!state.report.accounting_complete);
    assert_eq!(
        state.report.termination,
        CampaignTermination::WorkerError(ModelError::new("lost"))
    );
}

#[test]
fn accounting_distinguishes_known_zero_work_and_lost_counters_and_saturates() {
    for (outcome, complete_accounting) in [
        (Outcome::FactoryError, true),
        (Outcome::ModelError, false),
        (Outcome::Panicked, false),
    ] {
        let model = lifecycle(1, false);
        let mut state = model.initial_state().unwrap();
        for input in [
            Input::Claim { stop: None },
            complete(50, outcome),
            Input::Receive { job_id: 50 },
            sink(50, None),
            Input::Finish,
        ] {
            apply(&model, &mut state, input);
        }
        assert_eq!(state.report.errors, 1);
        assert_eq!(state.report.failures, 0);
        assert_eq!(state.report.reported_transitions, 0);
        assert_eq!(state.report.accounting_complete, complete_accounting);
    }
    let model = lifecycle(2, false);
    let mut state = model.initial_state().unwrap();
    for (job_id, count) in [(50, u64::MAX), (51, 1)] {
        apply(&model, &mut state, Input::Claim { stop: None });
        apply(
            &model,
            &mut state,
            Input::Complete {
                job_id,
                summary: Summary {
                    outcome: Outcome::Completed,
                    failure: false,
                    transitions: count,
                    cases: count,
                    skipped_checks: count,
                },
            },
        );
        apply(&model, &mut state, Input::Receive { job_id });
        apply(&model, &mut state, sink(job_id, None));
    }
    apply(&model, &mut state, Input::Finish);
    assert_eq!(
        (
            state.report.reported_transitions,
            state.report.reported_cases,
            state.report.reported_skipped_checks
        ),
        (u64::MAX, u64::MAX, u64::MAX)
    );
    assert!(!state.report.accounting_complete);
}

#[test]
fn controls_are_observed_before_claims_and_do_not_rewrite_completed_campaigns() {
    for (stop, termination) in [
        (ExternalStop::Cancelled, CampaignTermination::Cancelled),
        (ExternalStop::Deadline, CampaignTermination::Deadline),
    ] {
        let model = lifecycle(1, false);
        let mut state = model.initial_state().unwrap();
        assert!(apply(&model, &mut state, Input::Claim { stop: Some(stop) }).is_empty());
        apply(&model, &mut state, Input::Finish);
        assert_eq!(state.report.termination, termination);
        assert_eq!(state.report.started, 0);

        let mut state = model.initial_state().unwrap();
        for input in [
            Input::Claim { stop: None },
            complete(50, Outcome::Completed),
            Input::Receive { job_id: 50 },
            sink(50, None),
        ] {
            apply(&model, &mut state, input);
        }
        reject(&model, &state, Input::Claim { stop: Some(stop) });
        apply(&model, &mut state, Input::Finish);
        assert_eq!(state.report.termination, CampaignTermination::JobsCompleted);
    }
}

/// Deliberately corrupt the actual reducer's effect, retaining its properties.
/// This establishes that exploration/replay can detect an observable defect.
struct DuplicateDelivery(Lifecycle);

impl Model for DuplicateDelivery {
    type State = State;
    type Input = Input;
    type Output = Output;
    fn metadata(&self) -> ModelMetadata {
        let mut metadata = self.0.metadata();
        metadata.build.push_str(":duplicate-delivery");
        metadata
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        self.0.initial_state()
    }
    fn step(&self, state: &State, input: &Input) -> Result<Transition<State, Output>, ModelError> {
        let mut result = self.0.step(state, input)?;
        if let Some(Output::Deliver(id)) = result.outputs.first() {
            result.outputs.push(Output::Deliver(*id));
        }
        Ok(result)
    }
    fn check_state(&self, state: &State) -> Result<Vec<Check>, ModelError> {
        self.0.check_state(state)
    }
    fn check_transition(
        &self,
        before: &State,
        input: &Input,
        transition: &TransitionRef<'_, State, Output>,
    ) -> Result<Vec<Check>, ModelError> {
        self.0.check_transition(before, input, transition)
    }
}

impl Enumerate for DuplicateDelivery {
    fn inputs(&self, state: &State) -> Result<Vec<Input>, ModelError> {
        let mut inputs = vec![Input::Claim { stop: None }];
        for (&id, pending) in &state.pending {
            inputs.push(match pending {
                Pending::Running => complete(id, Outcome::Completed),
                Pending::Ready(_) => Input::Receive { job_id: id },
            });
        }
        Ok(inputs)
    }
}

impl Generate for DuplicateDelivery {
    fn generate(&self, state: &State, rng: &mut Rng) -> Result<Option<Input>, ModelError> {
        let mut inputs = self.inputs(state)?;
        Ok(rng
            .index(inputs.len())
            .map(|index| inputs.swap_remove(index)))
    }
}

impl ModelCodec for DuplicateDelivery {
    fn encode_state(&self, state: &State) -> Result<Vec<u8>, ModelError> {
        self.0.encode_state(state)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<State, ModelError> {
        self.0.decode_state(bytes)
    }
    fn encode_input(&self, input: &Input) -> Result<Vec<u8>, ModelError> {
        self.0.encode_input(input)
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Input, ModelError> {
        self.0.decode_input(bytes)
    }
    fn encode_output(&self, output: &Output) -> Result<Vec<u8>, ModelError> {
        self.0.encode_output(output)
    }
}

#[test]
fn duplicate_delivery_fault_is_detected_shrunk_recorded_and_replayed() {
    let faulty = DuplicateDelivery(lifecycle(2, false));
    let explored = enumerate(
        &faulty,
        SearchConfig {
            max_states: 100,
            max_transitions: 1_000,
            max_depth: 10,
        },
    )
    .unwrap();
    assert_eq!(explored.termination, SearchTermination::FailureFound);
    let mut original = explored.failure.unwrap();
    assert!(
        original
            .violations
            .iter()
            .any(|v| v.check.id == "campaign.effects")
    );
    // Rejected and extraneous claims add removable noise without changing the
    // target bug. The shrinker must establish the causal three-event sequence.
    original.inputs.insert(0, sink(999, None));
    original.inputs.insert(1, Input::Claim { stop: None });
    let minimized = shrink(
        &faulty,
        &original,
        ShrinkConfig {
            max_attempts: 1_000,
        },
    )
    .unwrap();
    assert!(minimized.validated_original);
    assert_eq!(minimized.termination, ShrinkTermination::SearchComplete);
    assert_eq!(minimized.minimized.inputs.len(), 3);
    assert!(minimized.minimized.inputs.len() < original.inputs.len());
    let config = RunConfig {
        strategy: "campaign-self-model-fault-control".into(),
        seed: None,
        parameters: vec![("injected_fault".into(), "duplicate Deliver".into())],
    };
    let trace = record(
        &faulty,
        minimized.minimized.inputs.clone(),
        config.clone(),
        100,
    )
    .unwrap();
    // Exercise both recordings without depending on the optional file export.
    // The recorded config contains reserved engine fields and is not a fresh
    // caller config for a second recording.
    let original_trace = record(&faulty, original.inputs.clone(), config, 100).unwrap();
    let original_replay = replay(&faulty, &original_trace, ReplayOptions::default()).unwrap();
    assert_eq!(original_replay.outcome, ReplayOutcome::Exact);
    assert!(original_replay.failure_reproduced);
    assert_eq!(trace.termination, Termination::PropertyFailed);
    let mut bytes = Vec::new();
    trace.write_to(&mut bytes).unwrap();
    let recovered = Trace::read_from(bytes.as_slice(), &ReadLimits::default()).unwrap();
    assert_eq!(trace, recovered);
    let replayed = replay(&faulty, &recovered, ReplayOptions::default()).unwrap();
    assert_eq!(replayed.outcome, ReplayOutcome::Exact);
    assert!(replayed.failure_reproduced);
    let strict_healthy = replay(&faulty.0, &recovered, ReplayOptions::default()).unwrap();
    assert!(matches!(
        strict_healthy.outcome,
        ReplayOutcome::Incompatible { .. }
    ));
    assert!(!strict_healthy.build_matches);
    let healthy = replay(
        &faulty.0,
        &recovered,
        ReplayOptions {
            allow_build_mismatch: true,
        },
    )
    .unwrap();
    assert!(matches!(healthy.outcome, ReplayOutcome::Diverged { .. }));
    assert!(!healthy.failure_reproduced);
    assert!(!healthy.build_matches);
    if let Some(directory) = std::env::var_os("STATELESS_SELF_MODEL_ARTIFACTS") {
        let directory = std::path::PathBuf::from(directory);
        assert!(directory.is_dir(), "artifact directory must already exist");
        for (name, artifact) in [
            ("campaign-fault-original.sttrace", &original_trace),
            ("campaign-fault-minimized.sttrace", &trace),
        ] {
            let path = directory.join(name);
            let file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path)
                .unwrap();
            artifact.write_to(file).unwrap();
            println!("saved campaign fault evidence: {}", path.display());
        }
    }
    println!(
        "campaign fault control: found in {} transitions; shrink {} -> {} inputs, {} attempts; {} audit bytes; exact replay reproduces failure",
        explored.transitions,
        original.inputs.len(),
        minimized.minimized.inputs.len(),
        minimized.attempts,
        bytes.len()
    );
}

#[test]
fn persisted_fault_trace_replays_when_supplied() {
    let Some(path) = std::env::var_os("STATELESS_SELF_MODEL_REPLAY") else {
        return;
    };
    let path = std::path::PathBuf::from(path);
    let trace =
        Trace::read_from(std::fs::File::open(&path).unwrap(), &ReadLimits::default()).unwrap();
    assert_eq!(trace.termination, Termination::PropertyFailed);
    let faulty = DuplicateDelivery(lifecycle(2, false));
    let report = replay(&faulty, &trace, ReplayOptions::default()).unwrap();
    assert_eq!(report.outcome, ReplayOutcome::Exact);
    assert!(report.failure_reproduced);
    assert_eq!(report.steps_verified, trace.steps.len());
    println!(
        "fresh-process campaign replay: {}, {} steps verified, failure reproduced",
        path.display(),
        report.steps_verified
    );
}

#[test]
fn healthy_campaign_audit_roundtrips_and_replays() {
    let model = lifecycle(2, false);
    let inputs = vec![
        Input::Claim { stop: None },
        Input::Claim { stop: None },
        complete(51, Outcome::FactoryError),
        complete(50, Outcome::Failure),
        Input::Receive { job_id: 51 },
        sink(51, None),
        Input::Receive { job_id: 50 },
        sink(50, None),
        Input::Finish,
    ];
    let trace = record(&model, inputs, RunConfig::default(), 100).unwrap();
    assert_eq!(trace.termination, Termination::Completed);
    let mut bytes = Vec::new();
    trace.write_to(&mut bytes).unwrap();
    let recovered = Trace::read_from(bytes.as_slice(), &ReadLimits::default()).unwrap();
    let report = replay(&model, &recovered, ReplayOptions::default()).unwrap();
    assert_eq!(report.outcome, ReplayOutcome::Exact);
    assert_eq!(report.steps_verified, 9);
    assert!(!report.failure_reproduced);
}

#[test]
fn exhausted_job_range_reports_a_bad_claim_without_panicking_in_the_checker() {
    let model = Lifecycle {
        first_job: u64::MAX,
        jobs: 1,
        stop_on_failure: false,
    };
    let mut state = model.initial_state().unwrap();
    apply(&model, &mut state, Input::Claim { stop: None });
    // A deliberately faulty reducer accepts another claim after exhausting the
    // ID range. The checker must report it, even though the next ID overflows.
    let faulty = Transition::accepted(state.clone(), vec![Output::RunJob(u64::MAX)]);
    let checks = model
        .check_transition(&state, &Input::Claim { stop: None }, &faulty.as_ref())
        .unwrap();
    assert!(
        checks
            .iter()
            .any(|check| check.id == "campaign.claim_once" && check.is_failure())
    );
    assert!(
        checks
            .iter()
            .any(|check| check.id == "campaign.effects" && check.is_failure())
    );
}

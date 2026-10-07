//! The campaign's actual decision core. Threads, clocks, channels, and callbacks
//! live in the executor; this model owns claims, completion, delivery, and stops.
//! Pending entries contain only protocol metadata, never application reports.

use std::collections::BTreeMap;

use super::{CampaignConfig, CampaignReport, CampaignTermination, JobOutcome, JobReport};
use crate::explore::FuzzTermination;
use crate::{Check, Disposition, Model, ModelError, ModelMetadata, Transition, TransitionRef};

#[cfg(test)]
mod codec;
#[cfg(test)]
mod tests;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(super) enum Stop {
    Failure,
    Cancelled,
    Deadline,
    Sink,
    Worker,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(super) enum ExternalStop {
    Cancelled,
    Deadline,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(super) enum Outcome {
    Completed,
    Failure,
    Cancelled,
    Deadline,
    FactoryError,
    ModelError,
    Panicked,
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub(super) struct Summary {
    pub outcome: Outcome,
    pub failure: bool,
    pub transitions: u64,
    pub cases: u64,
    pub skipped_checks: u64,
}

impl Summary {
    fn is_valid(&self) -> bool {
        match self.outcome {
            Outcome::FactoryError | Outcome::ModelError | Outcome::Panicked => {
                !self.failure
                    && self.transitions == 0
                    && self.cases == 0
                    && self.skipped_checks == 0
            }
            _ => self.failure == (self.outcome == Outcome::Failure),
        }
    }

    pub fn from_job<I>(job: &JobReport<I>) -> Self {
        let mut summary = Self {
            outcome: Outcome::Completed,
            failure: false,
            transitions: 0,
            cases: 0,
            skipped_checks: 0,
        };
        match &job.outcome {
            JobOutcome::Completed(fuzz) => {
                summary.outcome = match fuzz.termination {
                    FuzzTermination::FailureFound => Outcome::Failure,
                    FuzzTermination::Cancelled => Outcome::Cancelled,
                    FuzzTermination::Deadline => Outcome::Deadline,
                    _ => Outcome::Completed,
                };
                summary.failure = fuzz.failure.is_some();
                summary.transitions = fuzz.transitions;
                summary.cases = fuzz.cases as u64;
                summary.skipped_checks = fuzz.skipped_checks;
            }
            JobOutcome::FactoryError(_) => summary.outcome = Outcome::FactoryError,
            JobOutcome::ModelError(_) => summary.outcome = Outcome::ModelError,
            JobOutcome::Panicked(_) => summary.outcome = Outcome::Panicked,
        }
        summary
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub(super) enum Pending {
    Running,
    Ready(Summary),
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub(super) struct State {
    pub report: CampaignReport,
    pub stop: Option<Stop>,
    pub pending: BTreeMap<u64, Pending>,
    pub delivering: Option<u64>,
    pub lost_worker: bool,
    pub finalized: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) enum Input {
    /// The executor samples its external controls when attempting a claim.
    Claim {
        stop: Option<ExternalStop>,
    },
    /// Observed on the worker before its report is sent through the channel.
    Complete {
        job_id: u64,
        summary: Summary,
    },
    /// Observed by the coordinator after receiving the actual report.
    Receive {
        job_id: u64,
    },
    SinkReturned {
        job_id: u64,
        error: Option<ModelError>,
    },
    WorkerFailed {
        error: ModelError,
        lost_work: bool,
    },
    /// Only submitted after the executor has drained the channel and joined.
    Finish,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(super) enum Output {
    RunJob(u64),
    Deliver(u64),
    Discard(u64),
}

pub(super) struct Lifecycle {
    pub first_job: u64,
    pub jobs: usize,
    pub stop_on_failure: bool,
}

impl Lifecycle {
    pub fn new(config: &CampaignConfig) -> Self {
        Self {
            first_job: config.first_job,
            jobs: config.jobs,
            stop_on_failure: config.stop_on_failure,
        }
    }

    fn rejected(&self, state: State, reason: &str) -> Transition<State, Output> {
        Transition {
            state,
            outputs: Vec::new(),
            disposition: Disposition::Rejected(reason.into()),
        }
    }

    /// The actual transition law, shared by the executor and snapshot-based
    /// Model::step. Moving live state avoids cloning every pending job under the
    /// coordinator mutex; exploration still retains its immutable predecessor.
    pub(super) fn step_owned(&self, mut state: State, input: &Input) -> Transition<State, Output> {
        if state.finalized {
            return self.rejected(state, "campaign is finalized");
        }
        let mut outputs = Vec::new();
        match input {
            Input::Claim { stop } => {
                // Preserve observed-stop semantics: controls sampled after all
                // jobs were claimed do not retroactively change the outcome.
                if state.stop.is_some() || state.report.started == self.jobs {
                    return self.rejected(state, "campaign cannot claim more jobs");
                }
                if let Some(stop) = stop {
                    state.stop = Some(match stop {
                        ExternalStop::Cancelled => Stop::Cancelled,
                        ExternalStop::Deadline => Stop::Deadline,
                    });
                } else {
                    let job_id = self.first_job + state.report.started as u64;
                    state.pending.insert(job_id, Pending::Running);
                    state.report.started += 1;
                    state.report.not_started -= 1;
                    outputs.push(Output::RunJob(job_id));
                }
            }
            Input::Complete { job_id, summary } => {
                if !summary.is_valid() {
                    return self.rejected(state, "inconsistent job summary");
                }
                if state.pending.get(job_id) != Some(&Pending::Running) {
                    return self.rejected(state, "job is not running");
                }
                state
                    .pending
                    .insert(*job_id, Pending::Ready(summary.clone()));
                match summary.outcome {
                    Outcome::Cancelled => state.stop = Some(Stop::Cancelled),
                    Outcome::Deadline if state.stop != Some(Stop::Cancelled) => {
                        state.stop = Some(Stop::Deadline)
                    }
                    Outcome::Failure if self.stop_on_failure && state.stop.is_none() => {
                        state.stop = Some(Stop::Failure)
                    }
                    _ => {}
                }
            }
            Input::Receive { job_id } => {
                let Some(Pending::Ready(summary)) = state.pending.get(job_id) else {
                    return self.rejected(state, "job has no completed report");
                };
                if state.delivering.is_some() {
                    return self.rejected(state, "sink is already delivering a report");
                }
                let summary = summary.clone();
                state.pending.remove(job_id);
                state.report.finished += 1;
                state.report.failures += usize::from(summary.failure);
                match summary.outcome {
                    Outcome::FactoryError => state.report.errors += 1,
                    Outcome::ModelError | Outcome::Panicked => {
                        state.report.errors += 1;
                        state.report.accounting_complete = false;
                    }
                    _ => {}
                }
                for (total, count) in [
                    (&mut state.report.reported_transitions, summary.transitions),
                    (&mut state.report.reported_cases, summary.cases),
                    (
                        &mut state.report.reported_skipped_checks,
                        summary.skipped_checks,
                    ),
                ] {
                    if let Some(sum) = total.checked_add(count) {
                        *total = sum;
                    } else {
                        *total = u64::MAX;
                        state.report.accounting_complete = false;
                    }
                }
                if matches!(
                    state.report.termination,
                    CampaignTermination::CallbackError(_)
                ) {
                    outputs.push(Output::Discard(*job_id));
                } else {
                    state.delivering = Some(*job_id);
                    outputs.push(Output::Deliver(*job_id));
                }
            }
            Input::SinkReturned { job_id, error } => {
                if state.delivering != Some(*job_id) {
                    return self.rejected(state, "job is not awaiting a sink result");
                }
                state.delivering = None;
                if let Some(error) = error {
                    state.report.termination = CampaignTermination::CallbackError(error.clone());
                    state.stop = Some(Stop::Sink);
                } else {
                    state.report.delivered += 1;
                }
            }
            Input::WorkerFailed { error, lost_work } => {
                if !matches!(
                    state.report.termination,
                    CampaignTermination::CallbackError(_)
                ) {
                    state.report.termination = CampaignTermination::WorkerError(error.clone());
                }
                state.stop = Some(Stop::Worker);
                state.lost_worker |= lost_work;
                state.report.accounting_complete &= !lost_work;
            }
            Input::Finish => {
                if state.delivering.is_some()
                    || (!state.pending.is_empty() && !state.lost_worker)
                    || (state.stop.is_none() && state.report.started != self.jobs)
                {
                    return self.rejected(state, "campaign has unfinished work");
                }
                state.report.accounting_complete &= state.report.started == state.report.finished;
                if !matches!(
                    state.report.termination,
                    CampaignTermination::CallbackError(_) | CampaignTermination::WorkerError(_)
                ) {
                    state.report.termination = match state.stop {
                        Some(Stop::Cancelled) => CampaignTermination::Cancelled,
                        Some(Stop::Deadline) => CampaignTermination::Deadline,
                        Some(Stop::Failure) => CampaignTermination::FailureFound,
                        _ => CampaignTermination::JobsCompleted,
                    };
                }
                state.finalized = true;
            }
        }
        Transition::accepted(state, outputs)
    }
}

impl Model for Lifecycle {
    type State = State;
    type Input = Input;
    type Output = Output;

    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: format!(
                "stateless-campaign:{}:{}:{}",
                self.first_job, self.jobs, self.stop_on_failure
            ),
            model_version: 1,
            properties_version: 2,
            codec_version: 1,
            build: env!("STATELESS_BUILD_ID").into(),
        }
    }

    fn initial_state(&self) -> Result<State, ModelError> {
        if self.jobs == 0
            || u64::try_from(self.jobs - 1)
                .ok()
                .and_then(|n| self.first_job.checked_add(n))
                .is_none()
        {
            return Err(ModelError::new("invalid campaign lifecycle job range"));
        }
        Ok(State {
            report: CampaignReport::new(self.jobs),
            stop: None,
            pending: BTreeMap::new(),
            delivering: None,
            lost_worker: false,
            finalized: false,
        })
    }

    fn step(&self, before: &State, input: &Input) -> Result<Transition<State, Output>, ModelError> {
        Ok(self.step_owned(before.clone(), input))
    }

    fn check_state(&self, state: &State) -> Result<Vec<Check>, ModelError> {
        let r = &state.report;
        let error = matches!(
            r.termination,
            CampaignTermination::CallbackError(_) | CampaignTermination::WorkerError(_)
        );
        let reason_consistent = if error {
            match r.termination {
                CampaignTermination::CallbackError(_) => matches!(
                    state.stop,
                    Some(Stop::Sink | Stop::Worker | Stop::Cancelled | Stop::Deadline)
                ),
                CampaignTermination::WorkerError(_) => matches!(
                    state.stop,
                    Some(Stop::Worker | Stop::Cancelled | Stop::Deadline)
                ),
                _ => unreachable!(),
            }
        } else if !state.finalized {
            r.termination == CampaignTermination::JobsCompleted
                && !matches!(state.stop, Some(Stop::Sink | Stop::Worker))
        } else {
            matches!(
                (&state.stop, &r.termination),
                (None, CampaignTermination::JobsCompleted)
                    | (Some(Stop::Failure), CampaignTermination::FailureFound)
                    | (Some(Stop::Cancelled), CampaignTermination::Cancelled)
                    | (Some(Stop::Deadline), CampaignTermination::Deadline)
            )
        };
        let failure_observed = r.failures > 0
            || state
                .pending
                .values()
                .any(|pending| matches!(pending, Pending::Ready(summary) if summary.failure));
        Ok(vec![
            check(
                "campaign.accounting",
                r.requested == self.jobs
                    && r.started <= r.requested
                    && r.not_started == r.requested - r.started
                    && r.finished <= r.started
                    && r.delivered <= r.finished
                    && r.failures <= r.finished
                    && r.errors <= r.finished - r.failures,
            ),
            check(
                "campaign.pending",
                r.started.checked_sub(r.finished) == Some(state.pending.len())
                    && state.pending.iter().all(|(id, pending)| {
                        id.checked_sub(self.first_job)
                            .is_some_and(|n| n < r.started as u64)
                            && match pending {
                                Pending::Running => true,
                                Pending::Ready(summary) => summary.is_valid(),
                            }
                    }),
            ),
            check(
                "campaign.delivery",
                (if matches!(r.termination, CampaignTermination::CallbackError(_)) {
                    r.finished > r.delivered
                } else {
                    r.finished.checked_sub(r.delivered)
                        == Some(usize::from(state.delivering.is_some()))
                }) && state.delivering.is_none_or(|id| {
                    !state.pending.contains_key(&id)
                        && id
                            .checked_sub(self.first_job)
                            .is_some_and(|n| n < r.started as u64)
                        && r.delivered < r.finished
                        && !matches!(r.termination, CampaignTermination::CallbackError(_))
                }),
            ),
            check(
                "campaign.finalized",
                !state.finalized
                    || (state.delivering.is_none()
                        && (state.pending.is_empty()
                            || (state.lost_worker && !r.accounting_complete))
                        && (r.termination != CampaignTermination::JobsCompleted
                            || r.started == r.requested)),
            ),
            check(
                "campaign.termination",
                reason_consistent
                    && (!state.lost_worker || (error && !r.accounting_complete))
                    && (state.stop != Some(Stop::Failure)
                        || (self.stop_on_failure && failure_observed)),
            ),
        ])
    }

    fn check_transition(
        &self,
        before: &State,
        input: &Input,
        transition: &TransitionRef<'_, State, Output>,
    ) -> Result<Vec<Check>, ModelError> {
        let after = transition.state;
        let accepted = *transition.disposition == Disposition::Accepted;
        // Check admission from the prior state and event, independently of the
        // reducer's disposition. An inert but incorrect rejection is a bug too.
        let admissible = !before.finalized
            && match input {
                Input::Claim { .. } => before.stop.is_none() && before.report.started < self.jobs,
                Input::Complete { job_id, summary } => {
                    summary.is_valid() && before.pending.get(job_id) == Some(&Pending::Running)
                }
                Input::Receive { job_id } => {
                    before.delivering.is_none()
                        && matches!(before.pending.get(job_id), Some(Pending::Ready(_)))
                }
                Input::SinkReturned { job_id, .. } => before.delivering == Some(*job_id),
                Input::WorkerFailed { .. } => true,
                Input::Finish => {
                    before.delivering.is_none()
                        && (before.pending.is_empty() || before.lost_worker)
                        && (before.stop.is_some() || before.report.started == self.jobs)
                }
            };
        let observes_stop = match input {
            Input::Claim { stop: Some(_) }
            | Input::SinkReturned { error: Some(_), .. }
            | Input::WorkerFailed { .. } => true,
            Input::Complete { summary, .. } => {
                matches!(summary.outcome, Outcome::Cancelled | Outcome::Deadline)
                    || (self.stop_on_failure && summary.outcome == Outcome::Failure)
            }
            _ => false,
        };
        let claim = matches!(input, Input::Claim { stop: None }) && accepted;
        let received = matches!(input, Input::Receive { .. }) && accepted;
        let delivered = matches!(input, Input::SinkReturned { error: None, .. }) && accepted;
        let expected_outputs = if claim {
            self.first_job
                .checked_add(before.report.started as u64)
                .map(Output::RunJob)
                .into_iter()
                .collect()
        } else if received {
            let Input::Receive { job_id } = input else {
                unreachable!()
            };
            vec![if matches!(
                before.report.termination,
                CampaignTermination::CallbackError(_)
            ) {
                Output::Discard(*job_id)
            } else {
                Output::Deliver(*job_id)
            }]
        } else {
            Vec::new()
        };
        Ok(vec![
            check("campaign.admission", accepted == admissible),
            check(
                "campaign.stop_is_observed",
                after.stop.is_some() == (before.stop.is_some() || (accepted && observes_stop)),
            ),
            check(
                "campaign.rejection_is_inert",
                accepted || (after == before && transition.outputs.is_empty()),
            ),
            check(
                "campaign.claim_once",
                before.report.started.checked_add(usize::from(claim)) == Some(after.report.started)
                    && (!claim || (before.stop.is_none() && before.report.started < self.jobs)),
            ),
            check(
                "campaign.receive_once",
                before.report.finished.checked_add(usize::from(received))
                    == Some(after.report.finished),
            ),
            check(
                "campaign.deliver_once",
                before.report.delivered.checked_add(usize::from(delivered))
                    == Some(after.report.delivered),
            ),
            check("campaign.effects", transition.outputs == expected_outputs),
            check(
                "campaign.first_sink_error",
                !matches!(
                    before.report.termination,
                    CampaignTermination::CallbackError(_)
                ) || after.report.termination == before.report.termination,
            ),
            check(
                "campaign.accounting_loss_is_sticky",
                before.report.accounting_complete || !after.report.accounting_complete,
            ),
            check(
                "campaign.finalization_is_terminal",
                !before.finalized || after == before,
            ),
        ])
    }
}

fn check(id: &'static str, passed: bool) -> Check {
    if passed {
        Check::passed(id)
    } else {
        Check::failed(id, "campaign lifecycle contract violated")
    }
}

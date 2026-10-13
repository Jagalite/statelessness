//! Application-owned request controller and opt-in fault controls.
//!
//! This reference is deliberately a normal reducer: the host executes its ordered
//! effects, while the model checker only observes them. Bounds below describe the
//! example's environment, not a universal guarantee about services.

use crate::explore::{Failure, SearchConfig, SearchTermination, ShrinkConfig, enumerate, shrink};
use crate::{
    Check, Disposition, Enumerate, Generate, Model, ModelCodec, ModelError, ModelMetadata, Rng,
    Transition, TransitionRef,
};

pub mod codec;
pub mod coverage;
pub mod evidence;
pub mod host;

pub const PROPERTY_IDS: &[&str] = &[
    "controller.state_valid",
    "controller.publication_current",
    "controller.success_published",
    "controller.cleanup_required",
    "controller.settlement_once",
    "controller.cancellation_ack",
    "controller.duplicate_inert",
    "controller.release_after_settlement",
    "controller.retry_deadline",
    "controller.ignored_inert",
    "controller.effect_order",
    "controller.completion_progress",
    "controller.cancellation_progress",
    "controller.submission_progress",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Outcome {
    Success,
    RetryableFailure,
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum Input {
    Submit,
    Cancel {
        generation: u64,
    },
    Complete {
        generation: u64,
        attempt: u8,
        outcome: Outcome,
    },
    Cancelled {
        generation: u64,
        attempt: u8,
    },
    Tick {
        now: u64,
    },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Settlement {
    Succeeded,
    Cancelled,
    Failed,
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum Effect {
    Start { generation: u64, attempt: u8 },
    Cancel { generation: u64, attempt: u8 },
    Publish { generation: u64 },
    Settle { generation: u64, result: Settlement },
    Release { generation: u64 },
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Phase {
    Running,
    Cancelling,
    Waiting { deadline: u64 },
    Settled(Settlement),
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct Job {
    pub generation: u64,
    pub attempt: u8,
    pub phase: Phase,
    pub resource_owned: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub struct State {
    pub now: u64,
    pub current: Option<u64>,
    /// Retained tombstones fence duplicate deliveries. Production retirement
    /// requires an explicit delivery fence; elapsed time alone is insufficient.
    pub jobs: Vec<Job>,
}
impl State {
    pub fn job(&self, generation: u64) -> Option<&Job> {
        self.jobs.iter().find(|j| j.generation == generation)
    }
    fn job_mut(&mut self, generation: u64) -> Option<&mut Job> {
        self.jobs.iter_mut().find(|j| j.generation == generation)
    }
    pub fn pending(&self) -> usize {
        self.jobs
            .iter()
            .filter(|j| !matches!(j.phase, Phase::Settled(_)))
            .count()
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Bounds {
    pub generations: u64,
    pub attempts: u8,
    pub time: u64,
}
impl Default for Bounds {
    fn default() -> Self {
        Self {
            generations: 2,
            attempts: 2,
            time: 2,
        }
    }
}
impl Bounds {
    fn validate(self) -> Result<(), ModelError> {
        if self.generations == 0 || self.attempts == 0 || self.time == u64::MAX {
            return Err(ModelError::new(
                "positive generations/attempts and finite retry-clock headroom required",
            ));
        }
        Ok(())
    }
}

/// Named, stable fault-control identities. These are opt-in and never enabled by
/// the normal controller constructor or the effect executor.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mutation {
    ObsoletePublication,
    MissingCleanup,
    DoubleSettlement,
    DroppedCancellationAck,
    DuplicateCompletion,
    EarlyRelease,
    SkippedRetryDeadline,
    IgnoredMutation,
    ReorderedEffects,
}
impl Mutation {
    pub const ALL: [Self; 9] = [
        Self::ObsoletePublication,
        Self::MissingCleanup,
        Self::DoubleSettlement,
        Self::DroppedCancellationAck,
        Self::DuplicateCompletion,
        Self::EarlyRelease,
        Self::SkippedRetryDeadline,
        Self::IgnoredMutation,
        Self::ReorderedEffects,
    ];
    pub fn name(self) -> &'static str {
        match self {
            Self::ObsoletePublication => "obsolete-publication",
            Self::MissingCleanup => "missing-cleanup",
            Self::DoubleSettlement => "double-settlement",
            Self::DroppedCancellationAck => "dropped-cancellation-ack",
            Self::DuplicateCompletion => "duplicate-completion",
            Self::EarlyRelease => "early-release",
            Self::SkippedRetryDeadline => "skipped-retry-deadline",
            Self::IgnoredMutation => "ignored-mutation",
            Self::ReorderedEffects => "reordered-effects",
        }
    }
    pub fn target(self) -> &'static str {
        match self {
            Self::ObsoletePublication => "controller.publication_current",
            Self::MissingCleanup => "controller.cleanup_required",
            Self::DoubleSettlement => "controller.settlement_once",
            Self::DroppedCancellationAck => "controller.cancellation_ack",
            Self::DuplicateCompletion => "controller.duplicate_inert",
            Self::EarlyRelease => "controller.release_after_settlement",
            Self::SkippedRetryDeadline => "controller.retry_deadline",
            Self::IgnoredMutation => "controller.ignored_inert",
            Self::ReorderedEffects => "controller.effect_order",
        }
    }
    pub fn named(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|m| m.name() == name)
    }
}

#[derive(Clone, Debug, Default)]
pub struct Controller {
    pub bounds: Bounds,
    pub mutation: Option<Mutation>,
}

fn finish(state: &mut State, generation: u64, result: Settlement, effects: &mut Vec<Effect>) {
    let job = state.job_mut(generation).expect("finish has a known job");
    job.phase = Phase::Settled(result);
    job.resource_owned = false;
    effects.push(Effect::Settle { generation, result });
    effects.push(Effect::Release { generation });
    if state.current == Some(generation) {
        state.current = None;
    }
}
fn cancel(state: &mut State, generation: u64, effects: &mut Vec<Effect>) -> bool {
    let Some(job) = state.job_mut(generation) else {
        return false;
    };
    match job.phase {
        Phase::Running => {
            job.phase = Phase::Cancelling;
            effects.push(Effect::Cancel {
                generation,
                attempt: job.attempt,
            });
            if state.current == Some(generation) {
                state.current = None;
            }
            true
        }
        Phase::Waiting { .. } => {
            finish(state, generation, Settlement::Cancelled, effects);
            true
        }
        _ => false,
    }
}

/// The single production reducer. No clocks, threads, files, handles or I/O live
/// here. All outcomes are delivered by the host as generation-qualified inputs.
pub fn reduce(
    before: &State,
    input: &Input,
    bounds: Bounds,
) -> Result<Transition<State, Effect>, ModelError> {
    bounds.validate()?;
    let mut state = before.clone();
    let mut effects = Vec::new();
    let accepted = match *input {
        Input::Submit if (state.jobs.len() as u64) < bounds.generations => {
            if let Some(old) = state.current {
                cancel(&mut state, old, &mut effects);
            }
            let generation = state.jobs.len() as u64 + 1;
            state.jobs.push(Job {
                generation,
                attempt: 1,
                phase: Phase::Running,
                resource_owned: true,
            });
            state.current = Some(generation);
            effects.push(Effect::Start {
                generation,
                attempt: 1,
            });
            true
        }
        Input::Cancel { generation } => cancel(&mut state, generation, &mut effects),
        Input::Complete {
            generation,
            attempt,
            outcome,
        } => {
            match state
                .job(generation)
                .filter(|j| j.attempt == attempt)
                .map(|j| j.phase)
            {
                Some(Phase::Cancelling) => {
                    finish(&mut state, generation, Settlement::Cancelled, &mut effects);
                    true
                }
                Some(Phase::Running) => {
                    if outcome == Outcome::Success {
                        if state.current == Some(generation) {
                            effects.push(Effect::Publish { generation });
                        }
                        finish(&mut state, generation, Settlement::Succeeded, &mut effects);
                    } else if attempt < bounds.attempts && state.now < bounds.time {
                        let deadline = state.now + 1;
                        state.job_mut(generation).expect("known running job").phase =
                            Phase::Waiting { deadline };
                    } else {
                        finish(&mut state, generation, Settlement::Failed, &mut effects);
                    }
                    true
                }
                _ => false,
            }
        }
        Input::Cancelled {
            generation,
            attempt,
        } if state
            .job(generation)
            .is_some_and(|j| j.attempt == attempt && j.phase == Phase::Cancelling) =>
        {
            finish(&mut state, generation, Settlement::Cancelled, &mut effects);
            true
        }
        Input::Tick { now } if now > state.now && now <= bounds.time => {
            state.now = now;
            for job in &mut state.jobs {
                if matches!(job.phase, Phase::Waiting { deadline } if deadline <= now) {
                    job.attempt += 1;
                    job.phase = Phase::Running;
                    effects.push(Effect::Start {
                        generation: job.generation,
                        attempt: job.attempt,
                    });
                }
            }
            true
        }
        _ => false,
    };
    Ok(Transition {
        state,
        outputs: effects,
        disposition: if accepted {
            Disposition::Accepted
        } else {
            Disposition::Ignored("outside active request/attempt/time domain".into())
        },
    })
}

impl Controller {
    pub fn with_mutation(mutation: Mutation) -> Self {
        Self {
            mutation: Some(mutation),
            ..Self::default()
        }
    }
    fn inject(&self, before: &State, input: &Input, after: &mut Transition<State, Effect>) {
        match self.mutation {
            Some(Mutation::ObsoletePublication) => {
                if let Input::Complete {
                    generation,
                    outcome: Outcome::Success,
                    ..
                } = input
                    && before
                        .job(*generation)
                        .is_some_and(|j| j.phase == Phase::Cancelling)
                    && before.current != Some(*generation)
                {
                    after.outputs.insert(
                        0,
                        Effect::Publish {
                            generation: *generation,
                        },
                    );
                }
            }
            Some(Mutation::MissingCleanup) => after
                .outputs
                .retain(|e| !matches!(e, Effect::Release { .. })),
            Some(Mutation::DoubleSettlement) => {
                if let Some(effect) = after
                    .outputs
                    .iter()
                    .find(|e| matches!(e, Effect::Settle { .. }))
                    .cloned()
                {
                    after.outputs.push(effect);
                }
            }
            Some(Mutation::DroppedCancellationAck) => {
                if matches!(input, Input::Cancelled { .. })
                    && after.disposition == Disposition::Accepted
                {
                    after.state = before.clone();
                    after.outputs.clear();
                }
            }
            Some(Mutation::DuplicateCompletion) => {
                if let Input::Complete { generation, .. } = input
                    && before
                        .job(*generation)
                        .is_some_and(|j| matches!(j.phase, Phase::Settled(_)))
                {
                    after.outputs.push(Effect::Settle {
                        generation: *generation,
                        result: Settlement::Succeeded,
                    });
                    after.disposition = Disposition::Accepted;
                }
            }
            Some(Mutation::EarlyRelease) => {
                if let Some(generation) = after.outputs.iter().find_map(|e| match e {
                    Effect::Cancel { generation, .. } => Some(*generation),
                    _ => None,
                }) {
                    after.outputs.push(Effect::Release { generation });
                }
            }
            Some(Mutation::SkippedRetryDeadline) => {
                if matches!(input, Input::Tick { .. })
                    && after
                        .outputs
                        .iter()
                        .any(|e| matches!(e, Effect::Start { .. }))
                {
                    after.state.jobs = before.jobs.clone();
                    after.outputs.clear();
                }
            }
            Some(Mutation::IgnoredMutation) => {
                if matches!(after.disposition, Disposition::Ignored(_))
                    && after.state.now < self.bounds.time
                {
                    after.state.now += 1;
                }
            }
            Some(Mutation::ReorderedEffects) => {
                if after
                    .outputs
                    .iter()
                    .any(|e| matches!(e, Effect::Cancel { .. }))
                    && after
                        .outputs
                        .iter()
                        .any(|e| matches!(e, Effect::Start { .. }))
                {
                    after.outputs.reverse();
                }
            }
            None => {}
        }
    }
}

fn property(id: &'static str, holds: bool) -> Check {
    if holds {
        Check::passed(id)
    } else {
        Check::failed(id, "independent controller obligation was violated")
    }
}

impl Model for Controller {
    type State = State;
    type Input = Input;
    type Output = Effect;
    fn metadata(&self) -> ModelMetadata {
        let hash = [
            include_bytes!("reference.rs").as_slice(),
            include_bytes!("reference/codec.rs").as_slice(),
            include_bytes!("../docs/requirements/controller-properties.txt").as_slice(),
            include_bytes!("../docs/requirements/controller.json").as_slice(),
        ]
        .into_iter()
        .flatten()
        .fold(0xcbf29ce484222325u64, |h, b| {
            (h ^ u64::from(*b)).wrapping_mul(0x100000001b3)
        });
        ModelMetadata {
            name: "reference-controller".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: format!(
                "reference-{hash:016x}-{:?}-{:?}",
                self.bounds, self.mutation
            ),
        }
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        self.bounds.validate()?;
        Ok(State::default())
    }
    fn step(&self, state: &State, input: &Input) -> Result<Transition<State, Effect>, ModelError> {
        let mut after = reduce(state, input, self.bounds)?;
        self.inject(state, input, &mut after);
        Ok(after)
    }
    fn check_state(&self, state: &State) -> Result<Vec<Check>, ModelError> {
        let valid = state.now <= self.bounds.time && state.jobs.len() as u64 <= self.bounds.generations
            && state.jobs.iter().enumerate().all(|(i, j)| j.generation == i as u64 + 1 && j.attempt > 0 && j.attempt <= self.bounds.attempts
                && j.resource_owned != matches!(j.phase, Phase::Settled(_))
                && !matches!(j.phase, Phase::Waiting { deadline } if deadline <= state.now || deadline > self.bounds.time))
            && state.current.is_none_or(|g| state.job(g).is_some_and(|j| matches!(j.phase, Phase::Running | Phase::Waiting { .. })));
        Ok(vec![property(PROPERTY_IDS[0], valid)])
    }
    fn check_transition(
        &self,
        before: &State,
        input: &Input,
        after: &TransitionRef<'_, State, Effect>,
    ) -> Result<Vec<Check>, ModelError> {
        // These predicates are requirement assertions over observations; they do
        // not call reduce(), reuse its branch result, or compare two reducers.
        let count = |effect: &Effect| after.outputs.iter().filter(|e| *e == effect).count();
        let publication = after.outputs.iter().all(|e| match e {
            Effect::Publish { generation } => before.current == Some(*generation)
                && matches!(input, Input::Complete { generation: g, attempt, outcome: Outcome::Success } if g == generation && before.job(*g).is_some_and(|j| j.phase == Phase::Running && j.attempt == *attempt)),
            _ => true,
        });
        let successful = match input {
            Input::Complete {
                generation,
                attempt,
                outcome: Outcome::Success,
            } if before.current == Some(*generation)
                && before
                    .job(*generation)
                    .is_some_and(|j| j.phase == Phase::Running && j.attempt == *attempt) =>
            {
                count(&Effect::Publish {
                    generation: *generation,
                }) == 1
            }
            _ => true,
        };
        let cleanup = after.state.jobs.iter().all(|j| {
            let just_settled = matches!(j.phase, Phase::Settled(_))
                && before
                    .job(j.generation)
                    .is_some_and(|old| !matches!(old.phase, Phase::Settled(_)));
            !just_settled
                || count(&Effect::Release {
                    generation: j.generation,
                }) == 1
        });
        let settlement = after.outputs.iter().all(|e| match e {
            Effect::Settle { generation, result } => before
                .job(*generation)
                .is_some_and(|j| !matches!(j.phase, Phase::Settled(_)))
                && after
                    .state
                    .job(*generation)
                    .is_some_and(|j| j.phase == Phase::Settled(*result))
                && after
                    .outputs
                    .iter()
                    .filter(
                        |e| matches!(e, Effect::Settle { generation: g, .. } if g == generation),
                    )
                    .count()
                    == 1,
            _ => true,
        }) && after.state.jobs.iter().all(|j| match j.phase {
            Phase::Settled(result)
                if before
                    .job(j.generation)
                    .is_some_and(|old| !matches!(old.phase, Phase::Settled(_))) =>
            {
                count(&Effect::Settle {
                    generation: j.generation,
                    result,
                }) == 1
            }
            _ => true,
        });
        let ack = match input {
            Input::Cancelled {
                generation,
                attempt,
            } if before
                .job(*generation)
                .is_some_and(|j| j.phase == Phase::Cancelling && j.attempt == *attempt) =>
            {
                after
                    .state
                    .job(*generation)
                    .is_some_and(|j| j.phase == Phase::Settled(Settlement::Cancelled))
                    && count(&Effect::Settle {
                        generation: *generation,
                        result: Settlement::Cancelled,
                    }) == 1
            }
            _ => true,
        };
        let duplicate = match input {
            Input::Complete {
                generation,
                attempt,
                ..
            } if before.job(*generation).is_none_or(|j| {
                j.attempt != *attempt || !matches!(j.phase, Phase::Running | Phase::Cancelling)
            }) =>
            {
                before == after.state
                    && after.outputs.is_empty()
                    && matches!(after.disposition, Disposition::Ignored(_))
            }
            _ => true,
        };
        let release = after.outputs.iter().enumerate().all(|(index, e)| match e {
            Effect::Release { generation } => {
                after.outputs[..index]
                    .iter()
                    .any(|e| matches!(e, Effect::Settle { generation: g, .. } if g == generation))
                    && after
                        .state
                        .job(*generation)
                        .is_some_and(|j| matches!(j.phase, Phase::Settled(_)) && !j.resource_owned)
            }
            _ => true,
        });
        let retry = match input {
            Input::Tick { now } if *now > before.now && *now <= self.bounds.time => {
                before.jobs.iter().all(|j| match j.phase {
                    Phase::Waiting { deadline } if deadline <= *now => {
                        after.state.job(j.generation).is_some_and(|new| {
                            new.attempt == j.attempt + 1 && new.phase == Phase::Running
                        }) && count(&Effect::Start {
                            generation: j.generation,
                            attempt: j.attempt + 1,
                        }) == 1
                    }
                    _ => true,
                })
            }
            _ => true,
        };
        let ignored = !matches!(after.disposition, Disposition::Ignored(_))
            || (before == after.state && after.outputs.is_empty());
        let ordering = !matches!(input, Input::Submit) || before.current.is_none_or(|old| {
            if before.job(old).is_none_or(|j| j.phase != Phase::Running) || after.state.jobs.len() == before.jobs.len() { return true; }
            let cancel = after.outputs.iter().position(|e| matches!(e, Effect::Cancel { generation, .. } if *generation == old));
            let start = after.outputs.iter().position(|e| matches!(e, Effect::Start { generation, .. } if Some(*generation) == after.state.current));
            matches!((cancel, start), (Some(c), Some(s)) if c < s)
        });
        // Derive progress obligations from the delivered input and prior state,
        // not from whether the implementation chose to become terminal. Otherwise
        // a reducer can omit both its state update and required effects and make
        // output/state-conditioned safety predicates pass vacuously.
        let terminal = |generation, result| {
            after
                .state
                .job(generation)
                .is_some_and(|job| job.phase == Phase::Settled(result) && !job.resource_owned)
                && after.state.current != Some(generation)
                && count(&Effect::Settle { generation, result }) == 1
                && count(&Effect::Release { generation }) == 1
        };
        let cancellation = |generation| {
            before.job(generation).is_none_or(|old| match old.phase {
                Phase::Running => {
                    after.state.job(generation).is_some_and(|job| {
                        job.phase == Phase::Cancelling
                            && job.attempt == old.attempt
                            && job.resource_owned
                    }) && after.state.current != Some(generation)
                        && count(&Effect::Cancel {
                            generation,
                            attempt: old.attempt,
                        }) == 1
                }
                Phase::Waiting { .. } => terminal(generation, Settlement::Cancelled),
                _ => true,
            })
        };
        let completion_progress = match input {
            Input::Complete {
                generation,
                attempt,
                outcome,
            } => before
                .job(*generation)
                .filter(|job| job.attempt == *attempt)
                .is_none_or(|job| match job.phase {
                    Phase::Cancelling => {
                        after.disposition == &Disposition::Accepted
                            && terminal(*generation, Settlement::Cancelled)
                    }
                    Phase::Running => {
                        let progress = if *outcome == Outcome::Success {
                            terminal(*generation, Settlement::Succeeded)
                        } else if *attempt < self.bounds.attempts && before.now < self.bounds.time {
                            after.state.job(*generation).is_some_and(|next| {
                                next.phase
                                    == Phase::Waiting {
                                        deadline: before.now + 1,
                                    }
                                    && next.attempt == *attempt
                                    && next.resource_owned
                            }) && after.state.current == before.current
                        } else {
                            terminal(*generation, Settlement::Failed)
                        };
                        after.disposition == &Disposition::Accepted && progress
                    }
                    _ => true,
                }),
            _ => true,
        };
        let cancellation_progress = match input {
            Input::Cancel { generation }
                if before.job(*generation).is_some_and(|job| {
                    matches!(job.phase, Phase::Running | Phase::Waiting { .. })
                }) =>
            {
                after.disposition == &Disposition::Accepted && cancellation(*generation)
            }
            _ => true,
        };
        let submission_progress = if matches!(input, Input::Submit)
            && (before.jobs.len() as u64) < self.bounds.generations
        {
            let generation = before.jobs.len() as u64 + 1;
            after.disposition == &Disposition::Accepted
                && after.state.jobs.len() == before.jobs.len() + 1
                && after.state.current == Some(generation)
                && after.state.job(generation).is_some_and(|job| {
                    job.attempt == 1 && job.phase == Phase::Running && job.resource_owned
                })
                && count(&Effect::Start {
                    generation,
                    attempt: 1,
                }) == 1
                && before.current.is_none_or(cancellation)
        } else {
            true
        };
        Ok([
            publication,
            successful,
            cleanup,
            settlement,
            ack,
            duplicate,
            release,
            retry,
            ignored,
            ordering,
            completion_progress,
            cancellation_progress,
            submission_progress,
        ]
        .into_iter()
        .enumerate()
        .map(|(i, holds)| property(PROPERTY_IDS[i + 1], holds))
        .collect())
    }
}

impl Enumerate for Controller {
    fn inputs(&self, state: &State) -> Result<Vec<Input>, ModelError> {
        // Include unknown generation zero and stale attempts explicitly; omit
        // large payloads because only their success/failure class is relevant.
        let mut inputs = vec![Input::Submit];
        for generation in 0..=state.jobs.len() as u64 {
            inputs.push(Input::Cancel { generation });
            for attempt in 1..=self.bounds.attempts {
                inputs.push(Input::Cancelled {
                    generation,
                    attempt,
                });
                for outcome in [Outcome::Success, Outcome::RetryableFailure] {
                    inputs.push(Input::Complete {
                        generation,
                        attempt,
                        outcome,
                    });
                }
            }
        }
        for now in 0..=self.bounds.time {
            inputs.push(Input::Tick { now });
        }
        Ok(inputs)
    }
}
impl Generate for Controller {
    fn generate(&self, state: &State, rng: &mut Rng) -> Result<Option<Input>, ModelError> {
        let inputs = self.inputs(state)?;
        Ok(rng.index(inputs.len()).map(|i| inputs[i].clone()))
    }
    fn is_enabled(&self, state: &State, input: &Input) -> Result<bool, ModelError> {
        Ok(self.inputs(state)?.contains(input))
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum MutationStatus {
    Killed,
    /// Exhaustion without the target failing signals insufficient sensitivity,
    /// never proof that the corresponding real application is incorrect.
    Survives,
    NotEvaluated {
        reason: String,
    },
}
#[derive(Clone, Debug)]
pub struct MutationResult {
    pub mutation: Mutation,
    pub status: MutationStatus,
    pub search: Option<SearchTermination>,
    pub original: Option<Failure<Input>>,
    pub minimized: Option<Failure<Input>>,
    pub shrink_complete: bool,
}

/// Search through the engine, preserving the specific property's phase and ID.
/// A resource cutoff, callback error or unrelated finding cannot become a kill.
pub fn evaluate_mutation(mutation: Mutation, config: SearchConfig) -> MutationResult {
    let mut result = MutationResult {
        mutation,
        status: MutationStatus::NotEvaluated {
            reason: "evaluation not started".into(),
        },
        search: None,
        original: None,
        minimized: None,
        shrink_complete: false,
    };
    let attempt = (|| -> Result<(), ModelError> {
        if !PROPERTY_IDS.contains(&mutation.target()) {
            return Err(ModelError::new(
                "mutation target was removed from the property catalog",
            ));
        }
        let model = Controller::with_mutation(mutation);
        let report = enumerate(&model, config)?;
        result.search = Some(report.termination);
        match report.failure {
            Some(mut failure) => {
                let target = failure
                    .violations
                    .iter()
                    .position(|v| v.check.id == mutation.target())
                    .ok_or_else(|| {
                        ModelError::new(
                            "search found an unrelated failure before the required target",
                        )
                    })?;
                failure.violations.swap(0, target);
                result.original = Some(failure.clone());
                let minimized = shrink(&model, &failure, ShrinkConfig::default())?;
                if !minimized.validated_original
                    || !minimized
                        .minimized
                        .violations
                        .iter()
                        .any(|v| v.check.id == mutation.target())
                {
                    return Err(ModelError::new(
                        "target property did not survive causal replay/shrinking",
                    ));
                }
                result.shrink_complete =
                    minimized.termination == crate::explore::ShrinkTermination::SearchComplete;
                result.minimized = Some(minimized.minimized);
                result.status = MutationStatus::Killed;
            }
            None if report.termination == SearchTermination::GraphExhausted
                && report.skipped_checks == 0 =>
            {
                result.status = MutationStatus::Survives
            }
            None => {
                result.status = MutationStatus::NotEvaluated {
                    reason: format!(
                        "incomplete search: {:?}; skipped={}",
                        report.termination, report.skipped_checks
                    ),
                }
            }
        }
        Ok(())
    })();
    if let Err(error) = attempt {
        result.status = MutationStatus::NotEvaluated {
            reason: error.to_string(),
        };
    }
    result
}

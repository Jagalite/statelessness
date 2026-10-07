//! Bounded CPU campaigns of independent, reproducible fuzz jobs.
//!
//! Each job owns its model and mutation corpus. Job seeds depend only on the
//! master seed and job ID; worker count and completion order do not change a
//! job's inputs for a deterministic model. Completion order, deadline coverage,
//! and the subset started with `stop_on_failure` are scheduling dependent.

use std::any::Any;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::Mutex;
use std::sync::atomic::Ordering;
use std::sync::mpsc::sync_channel;
use std::time::Instant;

use crate::explore::{FuzzConfig, FuzzReport, RunLimits, fuzz_with_limits};
use crate::model::{Disposition, Generate, Model, ModelError, ModelMetadata, Rng};
use lifecycle::{ExternalStop, Input, Lifecycle, Output, State, Summary};

mod lifecycle;

#[derive(Clone, Debug)]
pub struct CampaignConfig {
    pub master_seed: u64,
    pub first_job: u64,
    pub jobs: usize,
    /// Between 1 and 256. At most `jobs` workers are started.
    pub workers: usize,
    /// Completed reports queued between workers and the sink, in 0..=1024.
    /// Zero uses a rendezvous channel. Each worker may also hold one report.
    pub result_buffer: usize,
    /// Per-job limits. Its seed is replaced with the derived job seed.
    pub fuzz: FuzzConfig,
    /// Stop claiming new jobs when a property failure is found. Already claimed
    /// jobs finish unless the external cancellation flag or deadline stops them.
    pub stop_on_failure: bool,
}

impl Default for CampaignConfig {
    fn default() -> Self {
        Self {
            master_seed: 0,
            first_job: 0,
            jobs: 16,
            workers: 1,
            result_buffer: 1,
            fuzz: FuzzConfig::default(),
            stop_on_failure: false,
        }
    }
}

impl CampaignConfig {
    /// Validate before constructing models, starting workers, or calling a sink.
    /// Zero per-job cases, steps, or transitions retain the meanings in `fuzz`.
    pub fn validate(&self) -> Result<(), ModelError> {
        if self.jobs == 0 {
            return Err(ModelError::new("campaign jobs must be positive"));
        }
        if !(1..=256).contains(&self.workers) {
            return Err(ModelError::new("campaign workers must be in 1..=256"));
        }
        if self.result_buffer > 1024 {
            return Err(ModelError::new(
                "campaign result_buffer must be in 0..=1024",
            ));
        }
        if self.fuzz.mutation_percent > 100 {
            return Err(ModelError::new("mutation_percent must be in 0..=100"));
        }
        let jobs = u64::try_from(self.jobs)
            .map_err(|_| ModelError::new("campaign job count exceeds u64"))?;
        self.first_job
            .checked_add(jobs - 1)
            .ok_or_else(|| ModelError::new("campaign job ID range overflows u64"))?;
        let cases = u64::try_from(self.fuzz.cases)
            .map_err(|_| ModelError::new("campaign per-job cases exceed u64"))?;
        let steps = u64::try_from(self.fuzz.max_steps)
            .map_err(|_| ModelError::new("campaign per-job steps exceed u64"))?;
        jobs.checked_mul(cases)
            .ok_or_else(|| ModelError::new("campaign aggregate cases overflow u64"))?;
        let transitions = (u128::from(cases) * u128::from(steps))
            .min(u128::from(self.fuzz.max_transitions)) as u64;
        jobs.checked_mul(transitions)
            .ok_or_else(|| ModelError::new("campaign aggregate transitions overflow u64"))?;
        Ok(())
    }
}

/// Stable SplitMix64 derivation, independent of worker assignment. Distinct job
/// IDs produce distinct seeds for a fixed master seed. This is not cryptographic.
pub fn job_seed(master_seed: u64, job_id: u64) -> u64 {
    Rng::new(master_seed.wrapping_add(job_id)).next_u64()
}

#[derive(Clone, Debug)]
pub struct JobReport<I> {
    pub job_id: u64,
    pub seed: u64,
    pub config: FuzzConfig,
    /// Absent when the factory or metadata callback failed or panicked.
    pub metadata: Option<ModelMetadata>,
    pub outcome: JobOutcome<I>,
}

#[derive(Clone, Debug)]
pub enum JobOutcome<I> {
    Completed(FuzzReport<I>),
    FactoryError(ModelError),
    ModelError(ModelError),
    /// Panics are captured only when the build uses unwinding. Abort, allocation
    /// failure, and a second panic during unwinding cannot be recovered here.
    Panicked(String),
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum CampaignTermination {
    JobsCompleted,
    FailureFound,
    Cancelled,
    Deadline,
    /// The sink returned an error or panicked; the aggregate report is retained.
    CallbackError(ModelError),
    WorkerError(ModelError),
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct CampaignReport {
    pub requested: usize,
    /// Claimed jobs, including failed factories and panicked jobs.
    pub started: usize,
    /// Outcomes received, including outcomes drained after a sink error.
    pub finished: usize,
    pub not_started: usize,
    /// Reports whose sink call returned Ok. A failing sink may already have
    /// produced external side effects before it returned an error.
    pub delivered: usize,
    pub failures: usize,
    pub errors: usize,
    /// Only counts carried by completed FuzzReports. Fuzz errors and panics do
    /// not expose their partial work; these fields never estimate that work.
    pub reported_transitions: u64,
    pub reported_cases: u64,
    pub reported_skipped_checks: u64,
    /// False if work counters were lost to a model error, panic, missing worker
    /// outcome, or skipped-check aggregate overflow. Factory errors count zero
    /// fuzz work. This does not mean every requested job ran or found no bugs.
    pub accounting_complete: bool,
    pub termination: CampaignTermination,
}

impl CampaignReport {
    fn new(requested: usize) -> Self {
        Self {
            requested,
            started: 0,
            finished: 0,
            not_started: requested,
            delivered: 0,
            failures: 0,
            errors: 0,
            reported_transitions: 0,
            reported_cases: 0,
            reported_skipped_checks: 0,
            accounting_complete: true,
            termination: CampaignTermination::JobsCompleted,
        }
    }
}

struct Control<'a> {
    limits: RunLimits<'a>,
    started: Option<Instant>,
}

impl<'a> Control<'a> {
    fn new(limits: RunLimits<'a>) -> Result<Self, ModelError> {
        #[cfg(all(target_family = "wasm", target_os = "unknown"))]
        if limits.max_duration.is_some() {
            return Err(ModelError::new(
                "wall-clock campaign limits are unavailable on this Wasm target",
            ));
        }
        Ok(Self {
            started: limits.max_duration.map(|_| Instant::now()),
            limits,
        })
    }

    fn stop(&self) -> Option<ExternalStop> {
        if self
            .limits
            .cancellation
            .is_some_and(|flag| flag.load(Ordering::Relaxed))
        {
            Some(ExternalStop::Cancelled)
        } else if self
            .started
            .zip(self.limits.max_duration)
            .is_some_and(|(start, duration)| start.elapsed() >= duration)
        {
            Some(ExternalStop::Deadline)
        } else {
            None
        }
    }

    fn remaining(&self) -> RunLimits<'a> {
        RunLimits {
            max_duration: self
                .started
                .zip(self.limits.max_duration)
                .map(|(start, duration)| duration.saturating_sub(start.elapsed())),
            cancellation: self.limits.cancellation,
        }
    }
}

/// Serializes the real lifecycle inputs at their observation points. No model,
/// sink, report destructor, or other application callback runs under this lock.
struct Coordinator {
    model: Lifecycle,
    state: Mutex<Option<State>>,
}

impl Coordinator {
    fn new(config: &CampaignConfig) -> Result<Self, ModelError> {
        let model = Lifecycle::new(config);
        let state = Mutex::new(Some(model.initial_state()?));
        Ok(Self { model, state })
    }

    fn transition(&self, state: &mut Option<State>, input: Input) -> Vec<Output> {
        // The executor owns its only live state. Branching exploration clones
        // snapshots, but runtime events must not copy every outstanding job.
        let before = state.take().expect("campaign state missing");
        #[cfg(debug_assertions)]
        let snapshot = before.clone();
        let transition = self.model.step_owned(before, &input);
        assert!(
            transition.disposition == Disposition::Accepted || matches!(input, Input::Claim { .. }),
            "invalid campaign lifecycle event: {input:?}: {:?}",
            transition.disposition
        );
        #[cfg(debug_assertions)]
        {
            assert!(
                self.model
                    .check_state(&transition.state)
                    .unwrap()
                    .iter()
                    .all(|c| !c.is_failure())
            );
            assert!(
                self.model
                    .check_transition(&snapshot, &input, &transition.as_ref())
                    .unwrap()
                    .iter()
                    .all(|c| !c.is_failure())
            );
        }
        *state = Some(transition.state);
        transition.outputs
    }

    fn apply(&self, input: Input) -> Vec<Output> {
        self.transition(
            &mut self.state.lock().expect("campaign state poisoned"),
            input,
        )
    }

    fn claim(&self, control: &Control<'_>) -> Option<u64> {
        let mut state = self.state.lock().expect("campaign state poisoned");
        let outputs = self.transition(
            &mut state,
            Input::Claim {
                stop: control.stop(),
            },
        );
        match outputs.as_slice() {
            [Output::RunJob(job_id)] => Some(*job_id),
            [] => None,
            _ => unreachable!("claim emitted a non-job effect"),
        }
    }

    fn complete<I>(&self, job: &JobReport<I>) {
        self.apply(Input::Complete {
            job_id: job.job_id,
            summary: Summary::from_job(job),
        });
    }

    fn finish(self) -> CampaignReport {
        self.apply(Input::Finish);
        self.state
            .into_inner()
            .expect("campaign state poisoned")
            .expect("campaign state missing")
            .report
    }
}

fn panic_message(payload: Box<dyn Any + Send>) -> String {
    let message = if let Some(message) = payload.downcast_ref::<String>() {
        message.clone()
    } else if let Some(message) = payload.downcast_ref::<&str>() {
        (*message).into()
    } else {
        "non-string panic payload".into()
    };
    // A panic payload may have an arbitrary destructor. Do not allow its cleanup
    // to unwind out of the coordinator or bypass a worker's outcome accounting.
    // If that destructor panics, leak only the secondary payload: dropping an
    // arbitrary chain of panicking payloads cannot be made to terminate safely.
    if let Err(secondary) = catch_unwind(AssertUnwindSafe(|| drop(payload))) {
        std::mem::forget(secondary);
    }
    message
}

fn execute_job<M, F, R>(
    campaign: &CampaignConfig,
    control: &Control<'_>,
    factory: &F,
    job_id: u64,
    runner: &R,
) -> JobReport<M::Input>
where
    M: Generate,
    F: Fn(u64) -> Result<M, ModelError>,
    R: Fn(u64, &M, FuzzConfig, RunLimits<'_>) -> Result<FuzzReport<M::Input>, ModelError>,
{
    let seed = job_seed(campaign.master_seed, job_id);
    let mut config = campaign.fuzz.clone();
    config.seed = seed;
    let mut metadata = None;
    let outcome = catch_unwind(AssertUnwindSafe(|| {
        let model = match factory(job_id) {
            Ok(model) => model,
            Err(error) => return JobOutcome::FactoryError(error),
        };
        metadata = Some(model.metadata());
        match runner(job_id, &model, config.clone(), control.remaining()) {
            Ok(report) => JobOutcome::Completed(report),
            Err(error) => JobOutcome::ModelError(error),
        }
    }))
    .unwrap_or_else(|payload| JobOutcome::Panicked(panic_message(payload)));
    JobReport {
        job_id,
        seed,
        config,
        metadata,
        outcome,
    }
}

fn deliver<I, C>(job: JobReport<I>, coordinator: &Coordinator, sink: &mut C) -> bool
where
    C: FnMut(JobReport<I>) -> Result<(), ModelError>,
{
    let job_id = job.job_id;
    let outputs = coordinator.apply(Input::Receive { job_id });
    match outputs.as_slice() {
        [Output::Discard(id)] if *id == job_id => {
            if let Err(payload) = catch_unwind(AssertUnwindSafe(|| drop(job))) {
                let _ = panic_message(payload);
            }
            false
        }
        [Output::Deliver(id)] if *id == job_id => {
            let result = catch_unwind(AssertUnwindSafe(|| sink(job))).unwrap_or_else(|payload| {
                Err(ModelError::new(format!(
                    "campaign callback panicked: {}",
                    panic_message(payload)
                )))
            });
            let succeeded = result.is_ok();
            coordinator.apply(Input::SinkReturned {
                job_id,
                error: result.err(),
            });
            succeeded
        }
        _ => unreachable!("receipt emitted an invalid delivery effect"),
    }
}

/// Execute independent fuzz jobs and stream their reports in completion order.
///
/// The factory receives the stable job ID and constructs a fresh model on the
/// worker that uses it. The model, state, and outputs need not be Send or Sync;
/// only inputs in reports cross threads. The factory must itself be Sync.
/// Reproducibility requires that the same job ID constructs the same model and
/// initial behavior, independently of scheduling. Shared mutable factory state,
/// clocks, and external entropy can defeat reproducibility despite stable seeds.
///
/// The deadline covers scheduling, factory time, and search. It is cooperative:
/// callbacks, model construction, destruction, and sink I/O cannot be preempted.
/// Every started outcome is counted and sent to the sink, even after a property
/// failure or global stop. If the sink errors or panics, no further sink calls
/// are made: new scheduling stops, active jobs finish, and queued/active reports
/// are drained and counted but discarded. The failing sink report is not counted
/// as delivered. Persisted evidence may therefore cover fewer than finished jobs.
/// The original sink error is retained if disposing a drained input panics. A
/// panic payload whose destructor also panics can leak the secondary payload;
/// this avoids unwinding past the drain and losing the aggregate report.
///
/// One worker executes on the calling thread without creating threads. Unknown-OS
/// Wasm supports this path without a wall-clock limit; multiple workers return an
/// error there. Invalid configurations cause no factory or sink calls.
pub fn run_campaign<M, F, C>(
    config: CampaignConfig,
    limits: RunLimits<'_>,
    factory: F,
    sink: C,
) -> Result<CampaignReport, ModelError>
where
    M: Generate,
    M::Input: Send,
    F: Fn(u64) -> Result<M, ModelError> + Sync,
    C: FnMut(JobReport<M::Input>) -> Result<(), ModelError>,
{
    run_campaign_using(config, limits, factory, sink, |_, model, fuzz, limits| {
        fuzz_with_limits(model, fuzz, limits)
    })
}

/// Feedback-guided CPU jobs with fresh job-local feedback, feature sets and corpora.
/// The feedback factory runs on its worker; feature keys need not be Send/Sync.
/// Fixed job IDs remain reproducible across worker counts. No corpus is shared.
pub fn run_guided_campaign<M, F, B, G, C>(
    config: CampaignConfig,
    limits: RunLimits<'_>,
    guidance: crate::guided::GuidanceConfig,
    factory: F,
    feedback_factory: B,
    sink: C,
) -> Result<CampaignReport, ModelError>
where
    M: Generate,
    M::Input: Send,
    F: Fn(u64) -> Result<M, ModelError> + Sync,
    B: Fn(u64, &M) -> Result<G, ModelError> + Sync,
    G: crate::guided::Feedback<M>,
    C: FnMut(JobReport<M::Input>) -> Result<(), ModelError>,
{
    guidance.validate()?;
    run_campaign_using(config, limits, factory, sink, |id, model, fuzz, limits| {
        let control = Control::new(limits)?;
        let feedback = feedback_factory(id, model)?;
        crate::guided::fuzz_with_limits(model, fuzz, guidance, feedback, control.remaining())
    })
}

fn run_campaign_using<M, F, C, R>(
    config: CampaignConfig,
    limits: RunLimits<'_>,
    factory: F,
    mut sink: C,
    runner: R,
) -> Result<CampaignReport, ModelError>
where
    M: Generate,
    M::Input: Send,
    F: Fn(u64) -> Result<M, ModelError> + Sync,
    C: FnMut(JobReport<M::Input>) -> Result<(), ModelError>,
    R: Fn(u64, &M, FuzzConfig, RunLimits<'_>) -> Result<FuzzReport<M::Input>, ModelError> + Sync,
{
    config.validate()?;
    #[cfg(all(target_family = "wasm", target_os = "unknown"))]
    if config.workers > 1 {
        return Err(ModelError::new(
            "multiple campaign workers are unavailable on this Wasm target",
        ));
    }
    let control = Control::new(limits)?;
    let coordinator = Coordinator::new(&config)?;
    if let Some(stop) = control.stop() {
        coordinator.apply(Input::Claim { stop: Some(stop) });
    } else if config.workers == 1 {
        while let Some(job_id) = coordinator.claim(&control) {
            let job = execute_job(&config, &control, &factory, job_id, &runner);
            coordinator.complete(&job);
            if !deliver(job, &coordinator, &mut sink) {
                break;
            }
        }
    } else {
        std::thread::scope(|scope| {
            let (sender, receiver) = sync_channel(config.result_buffer);
            let mut workers = Vec::with_capacity(config.workers.min(config.jobs));
            for _ in 0..config.workers.min(config.jobs) {
                let sender = sender.clone();
                let config = &config;
                let control = &control;
                let factory = &factory;
                let runner = &runner;
                let coordinator = &coordinator;
                match std::thread::Builder::new().spawn_scoped(scope, move || {
                    while let Some(job_id) = coordinator.claim(control) {
                        let job = execute_job(config, control, factory, job_id, runner);
                        coordinator.complete(&job);
                        if sender.send(job).is_err() {
                            break;
                        }
                    }
                }) {
                    Ok(worker) => workers.push(worker),
                    Err(error) => {
                        coordinator.apply(Input::WorkerFailed {
                            error: ModelError::new(format!(
                                "unable to start campaign worker: {error}"
                            )),
                            lost_work: false,
                        });
                        break;
                    }
                }
            }
            drop(sender);
            for job in receiver {
                deliver(job, &coordinator, &mut sink);
            }
            for worker in workers {
                if let Err(payload) = worker.join() {
                    coordinator.apply(Input::WorkerFailed {
                        error: ModelError::new(format!(
                            "campaign worker panicked: {}",
                            panic_message(payload)
                        )),
                        lost_work: true,
                    });
                }
            }
        });
    }
    Ok(coordinator.finish())
}

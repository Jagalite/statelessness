use std::cell::Cell;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Barrier, mpsc};
use std::time::Duration;

use stateless::campaign::*;
use stateless::explore::{FuzzConfig, FuzzTermination, RunLimits};
use stateless::{Check, Generate, Model, ModelError, ModelMetadata, Rng, Transition};

#[derive(Clone, Copy)]
enum Mode {
    Pass,
    Fail,
    CheckerError,
    Panic,
}

// Model, state and output deliberately use Rc/Cell: none need to cross threads.
struct LocalModel {
    job_id: u64,
    mode: Mode,
    checks: Rc<Cell<usize>>,
}

impl LocalModel {
    fn new(job_id: u64, mode: Mode) -> Self {
        Self {
            job_id,
            mode,
            checks: Rc::new(Cell::new(0)),
        }
    }
}

impl Model for LocalModel {
    type State = Rc<(usize, u64)>;
    type Input = u64;
    type Output = Rc<u64>;

    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: format!("job-{}", self.job_id),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "fixture".into(),
        }
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        Ok(Rc::new((0, 0)))
    }
    fn step(
        &self,
        state: &Self::State,
        input: &u64,
    ) -> Result<Transition<Self::State, Self::Output>, ModelError> {
        if matches!(self.mode, Mode::Panic) {
            panic!("fixture model panic");
        }
        Ok(Transition::accepted(
            Rc::new((state.0 + 1, *input)),
            vec![Rc::new(*input)],
        ))
    }
    fn check_state(&self, state: &Self::State) -> Result<Vec<Check>, ModelError> {
        self.checks.set(self.checks.get() + 1);
        if state.0 > 0 && matches!(self.mode, Mode::CheckerError) {
            return Err(ModelError::new("fixture checker error"));
        }
        Ok(vec![
            if state.0 == 3 && matches!(self.mode, Mode::Fail) {
                Check::failed("fixture", "third step")
            } else {
                Check::passed("fixture")
            },
            Check::skipped("optional", "fixture"),
        ])
    }
}

impl Generate for LocalModel {
    fn generate(&self, _: &Self::State, rng: &mut Rng) -> Result<Option<u64>, ModelError> {
        Ok(Some(rng.next_u64()))
    }
}

fn config(workers: usize) -> CampaignConfig {
    CampaignConfig {
        master_seed: 17,
        first_job: 50,
        jobs: 12,
        workers,
        result_buffer: 1,
        fuzz: FuzzConfig {
            seed: 999,
            cases: 2,
            max_steps: 4,
            max_transitions: 8,
            mutation_percent: 50,
        },
        stop_on_failure: false,
    }
}

#[test]
fn fixed_jobs_have_identical_failures_with_one_or_four_workers() {
    fn run(workers: usize) -> (CampaignReport, Vec<JobReport<u64>>) {
        let mut jobs = Vec::new();
        let report = run_campaign(
            config(workers),
            RunLimits::default(),
            |id| Ok(LocalModel::new(id, Mode::Fail)),
            |job| {
                jobs.push(job);
                Ok(())
            },
        )
        .unwrap();
        jobs.sort_by_key(|job| job.job_id);
        (report, jobs)
    }
    let (serial, serial_jobs) = run(1);
    let (parallel, parallel_jobs) = run(4);
    assert_eq!(serial.termination, CampaignTermination::JobsCompleted);
    assert_eq!(parallel.termination, CampaignTermination::JobsCompleted);
    assert_eq!(
        (
            parallel.started,
            parallel.finished,
            parallel.delivered,
            parallel.failures
        ),
        (12, 12, 12, 12)
    );
    assert_eq!(
        (
            parallel.reported_cases,
            parallel.reported_transitions,
            parallel.reported_skipped_checks
        ),
        (12, 36, 48)
    );
    assert!(parallel.accounting_complete);
    for (left, right) in serial_jobs.iter().zip(&parallel_jobs) {
        assert_eq!(left.job_id, right.job_id);
        assert_eq!(left.seed, job_seed(17, left.job_id));
        assert_eq!(left.seed, right.seed);
        assert_eq!(left.config.seed, left.seed);
        assert_eq!(left.metadata, right.metadata);
        assert_eq!(
            left.metadata.as_ref().unwrap().name,
            format!("job-{}", left.job_id)
        );
        let (JobOutcome::Completed(left), JobOutcome::Completed(right)) =
            (&left.outcome, &right.outcome)
        else {
            panic!("completed jobs expected");
        };
        assert_eq!(left.failure, right.failure);
        assert_eq!(left.termination, FuzzTermination::FailureFound);
        assert_eq!(left.transitions, right.transitions);
    }
}

#[test]
fn job_ranges_reproduce_the_same_seeds_and_inputs() {
    let mut whole = Vec::new();
    run_campaign(
        config(3),
        RunLimits::default(),
        |id| Ok(LocalModel::new(id, Mode::Fail)),
        |job| {
            whole.push(job);
            Ok(())
        },
    )
    .unwrap();
    let mut subset_config = config(1);
    subset_config.first_job = 54;
    subset_config.jobs = 1;
    run_campaign(
        subset_config,
        RunLimits::default(),
        |id| Ok(LocalModel::new(id, Mode::Fail)),
        |job| {
            let original = whole
                .iter()
                .find(|candidate| candidate.job_id == job.job_id)
                .unwrap();
            let (JobOutcome::Completed(left), JobOutcome::Completed(right)) =
                (&original.outcome, &job.outcome)
            else {
                panic!();
            };
            assert_eq!(left.failure, right.failure);
            assert_eq!(original.seed, job.seed);
            Ok(())
        },
    )
    .unwrap();
    let mut seeds: Vec<_> = (0..1000).map(|id| job_seed(u64::MAX - 2, id)).collect();
    seeds.sort_unstable();
    seeds.dedup();
    assert_eq!(seeds.len(), 1000);
}

#[test]
fn invalid_configuration_has_no_factory_or_sink_side_effects() {
    let mut invalid = Vec::new();
    let mut next = config(1);
    next.jobs = 0;
    invalid.push(next);
    let mut next = config(1);
    next.workers = 0;
    invalid.push(next);
    let mut next = config(1);
    next.workers = 257;
    invalid.push(next);
    let mut next = config(1);
    next.result_buffer = 1025;
    invalid.push(next);
    let mut next = config(1);
    next.first_job = u64::MAX;
    invalid.push(next);
    let mut next = config(1);
    next.fuzz.mutation_percent = 101;
    invalid.push(next);
    #[cfg(target_pointer_width = "64")]
    {
        let mut next = config(1);
        next.fuzz.cases = usize::MAX;
        invalid.push(next);
        let mut next = config(1);
        next.fuzz.cases = 1;
        next.fuzz.max_steps = usize::MAX;
        next.fuzz.max_transitions = u64::MAX;
        invalid.push(next);
    }
    for config in invalid {
        assert!(
            run_campaign(
                config,
                RunLimits::default(),
                |_| -> Result<LocalModel, ModelError> { panic!("invalid config called factory") },
                |_| { panic!("invalid config called sink") }
            )
            .is_err()
        );
    }
}

#[test]
fn budget_validation_uses_the_actual_per_job_transition_upper_bound() {
    let mut config = config(4);
    config.fuzz.max_transitions = u64::MAX;
    assert!(config.validate().is_ok());
    config.fuzz.cases = 0;
    config.fuzz.max_steps = usize::MAX;
    assert!(config.validate().is_ok());
}

#[test]
fn pre_cancelled_or_zero_duration_campaign_does_no_work() {
    let cancelled = AtomicBool::new(true);
    for (limits, termination) in [
        (
            RunLimits {
                cancellation: Some(&cancelled),
                max_duration: None,
            },
            CampaignTermination::Cancelled,
        ),
        (
            RunLimits {
                max_duration: Some(Duration::ZERO),
                cancellation: None,
            },
            CampaignTermination::Deadline,
        ),
    ] {
        let report = run_campaign(
            config(4),
            limits,
            |_| -> Result<LocalModel, ModelError> { panic!("stopped campaign called factory") },
            |_| panic!("stopped campaign called sink"),
        )
        .unwrap();
        assert_eq!(report.termination, termination);
        assert_eq!(
            (report.started, report.finished, report.not_started),
            (0, 0, 12)
        );
        assert!(report.accounting_complete);
    }
}

#[test]
fn serial_execution_stays_on_calling_thread_and_uses_fresh_models() {
    let thread_id = std::thread::current().id();
    let factories = AtomicUsize::new(0);
    let mut sinks = 0;
    let report = run_campaign(
        config(1),
        RunLimits::default(),
        |id| {
            assert_eq!(std::thread::current().id(), thread_id);
            factories.fetch_add(1, Ordering::Relaxed);
            Ok(LocalModel::new(id, Mode::Pass))
        },
        |_| {
            assert_eq!(std::thread::current().id(), thread_id);
            sinks += 1;
            Ok(())
        },
    )
    .unwrap();
    assert_eq!(factories.load(Ordering::Relaxed), 12);
    assert_eq!(sinks, 12);
    assert_eq!(report.reported_transitions, 96);
}

#[test]
fn factory_checker_and_panic_outcomes_are_distinct_from_failures() {
    let mut config = config(4);
    config.jobs = 4;
    config.first_job = 0;
    let mut jobs = Vec::new();
    let report = run_campaign(
        config,
        RunLimits::default(),
        |id| match id {
            0 => Err(ModelError::new("fixture factory error")),
            1 => Ok(LocalModel::new(id, Mode::CheckerError)),
            2 => Ok(LocalModel::new(id, Mode::Panic)),
            _ => Ok(LocalModel::new(id, Mode::Fail)),
        },
        |job| {
            jobs.push(job);
            Ok(())
        },
    )
    .unwrap();
    jobs.sort_by_key(|job| job.job_id);
    assert!(matches!(jobs[0].outcome, JobOutcome::FactoryError(_)));
    assert!(jobs[0].metadata.is_none());
    assert!(matches!(jobs[1].outcome, JobOutcome::ModelError(_)));
    assert!(
        matches!(&jobs[2].outcome, JobOutcome::Panicked(message) if message == "fixture model panic")
    );
    assert!(jobs[2].metadata.is_some());
    assert!(matches!(jobs[3].outcome, JobOutcome::Completed(_)));
    assert_eq!(
        (
            report.started,
            report.finished,
            report.delivered,
            report.errors,
            report.failures
        ),
        (4, 4, 4, 3, 1)
    );
    assert_eq!(report.reported_transitions, 3);
    assert!(!report.accounting_complete);
}

#[test]
fn factory_panics_are_reported_and_other_jobs_continue() {
    let mut config = config(2);
    config.jobs = 2;
    let mut jobs = Vec::new();
    let report = run_campaign(
        config,
        RunLimits::default(),
        |id| {
            if id == 50 {
                panic!("factory panic");
            }
            Ok(LocalModel::new(id, Mode::Pass))
        },
        |job| {
            jobs.push(job);
            Ok(())
        },
    )
    .unwrap();
    assert_eq!((report.errors, report.finished), (1, 2));
    assert!(
        jobs.iter()
            .any(|job| job.metadata.is_none() && matches!(job.outcome, JobOutcome::Panicked(_)))
    );
}

#[test]
fn factory_errors_have_known_zero_fuzz_work() {
    let report = run_campaign(
        config(4),
        RunLimits::default(),
        |_| -> Result<LocalModel, ModelError> { Err(ModelError::new("unavailable")) },
        |_| Ok(()),
    )
    .unwrap();
    assert_eq!(report.errors, 12);
    assert_eq!(report.reported_transitions, 0);
    assert!(report.accounting_complete);
}

#[test]
fn cancellation_from_factory_is_observed_before_search_and_drains_active_jobs() {
    let flag = AtomicBool::new(false);
    let report = run_campaign(
        config(4),
        RunLimits {
            cancellation: Some(&flag),
            max_duration: None,
        },
        |id| {
            flag.store(true, Ordering::Relaxed);
            Ok(LocalModel::new(id, Mode::Panic))
        },
        |job| {
            let JobOutcome::Completed(fuzz) = job.outcome else {
                panic!("search should have been cancelled before model callbacks");
            };
            assert_eq!(fuzz.termination, FuzzTermination::Cancelled);
            assert_eq!(fuzz.transitions, 0);
            Ok(())
        },
    )
    .unwrap();
    assert_eq!(report.termination, CampaignTermination::Cancelled);
    assert_eq!(report.started, report.finished);
    assert!(report.not_started > 0);
    assert!(report.accounting_complete);
}

#[test]
fn last_zero_case_job_observes_factory_cancellation_and_expired_deadline() {
    for workers in [1, 4] {
        let mut config = config(workers);
        config.jobs = 1;
        config.fuzz.cases = 0;
        let cancelled = AtomicBool::new(false);
        let report = run_campaign(
            config.clone(),
            RunLimits {
                cancellation: Some(&cancelled),
                max_duration: None,
            },
            |id| {
                cancelled.store(true, Ordering::Relaxed);
                Ok(LocalModel::new(id, Mode::Panic))
            },
            |job| {
                let JobOutcome::Completed(fuzz) = job.outcome else {
                    panic!("cancelled fuzz report expected");
                };
                assert_eq!(fuzz.termination, FuzzTermination::Cancelled);
                assert_eq!((fuzz.cases, fuzz.transitions), (0, 0));
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(report.termination, CampaignTermination::Cancelled);
        assert_eq!(
            (report.started, report.finished, report.not_started),
            (1, 1, 0)
        );

        let report = run_campaign(
            config,
            RunLimits {
                cancellation: None,
                max_duration: Some(Duration::from_millis(50)),
            },
            |id| {
                std::thread::sleep(Duration::from_millis(75));
                Ok(LocalModel::new(id, Mode::Panic))
            },
            |job| {
                let JobOutcome::Completed(fuzz) = job.outcome else {
                    panic!("expired fuzz report expected");
                };
                assert_eq!(fuzz.termination, FuzzTermination::Deadline);
                assert_eq!((fuzz.cases, fuzz.transitions), (0, 0));
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(report.termination, CampaignTermination::Deadline);
        assert_eq!(
            (report.started, report.finished, report.not_started),
            (1, 1, 0)
        );
    }
}

#[test]
fn deadline_includes_factory_time_and_is_not_reset_per_job() {
    let mut config = config(1);
    config.jobs = 2;
    let report = run_campaign(
        config,
        RunLimits {
            max_duration: Some(Duration::from_millis(50)),
            cancellation: None,
        },
        |id| {
            std::thread::sleep(Duration::from_millis(75));
            Ok(LocalModel::new(id, Mode::Panic))
        },
        |job| {
            let JobOutcome::Completed(fuzz) = job.outcome else {
                panic!("deadline should precede model callbacks");
            };
            assert_eq!(fuzz.termination, FuzzTermination::Deadline);
            assert_eq!(fuzz.cases, 0);
            Ok(())
        },
    )
    .unwrap();
    assert_eq!(report.termination, CampaignTermination::Deadline);
    assert_eq!(
        (report.started, report.finished, report.not_started),
        (1, 1, 1)
    );
}

#[test]
fn stop_on_failure_finishes_already_claimed_jobs() {
    let mut config = config(4);
    config.stop_on_failure = true;
    let barrier = Barrier::new(4);
    let report = run_campaign(
        config,
        RunLimits::default(),
        |id| {
            barrier.wait();
            Ok(LocalModel::new(id, Mode::Fail))
        },
        |_| Ok(()),
    )
    .unwrap();
    assert_eq!(report.termination, CampaignTermination::FailureFound);
    assert_eq!(
        (
            report.started,
            report.finished,
            report.delivered,
            report.failures
        ),
        (4, 4, 4, 4)
    );
    assert_eq!(report.not_started, 8);
}

#[test]
fn callback_error_stops_calls_and_drains_workers_with_rendezvous_channel() {
    let mut config = config(4);
    config.jobs = 100;
    config.result_buffer = 0;
    let barrier = Barrier::new(4);
    let mut calls = 0;
    let report = run_campaign(
        config,
        RunLimits::default(),
        |id| {
            if id < 54 {
                barrier.wait();
            }
            Ok(LocalModel::new(id, Mode::Pass))
        },
        |_| {
            calls += 1;
            Err(ModelError::new("disk full"))
        },
    )
    .unwrap();
    assert_eq!(calls, 1);
    assert_eq!(
        report.termination,
        CampaignTermination::CallbackError(ModelError::new("disk full"))
    );
    assert!(report.started >= 4);
    assert_eq!(report.started, report.finished);
    assert_eq!(report.delivered, 0);
    assert!(report.not_started > 0);
    assert!(report.accounting_complete);
}

#[test]
fn callback_panics_also_drain_workers() {
    let mut config = config(4);
    config.result_buffer = 0;
    let report = run_campaign(
        config,
        RunLimits::default(),
        |id| Ok(LocalModel::new(id, Mode::Pass)),
        |_| panic!("sink panic"),
    )
    .unwrap();
    assert!(
        matches!(&report.termination, CampaignTermination::CallbackError(error) if error.0.contains("sink panic"))
    );
    assert_eq!(report.started, report.finished);
    assert_eq!(report.delivered, 0);
}

#[test]
fn a_blocked_sink_bounds_started_jobs_by_queue_plus_workers_plus_sink() {
    let mut config = config(3);
    config.jobs = 100;
    config.result_buffer = 1;
    let (factory_tx, factory_rx) = mpsc::channel();
    let (sink_tx, sink_rx) = mpsc::sync_channel(0);
    let (release_tx, release_rx) = mpsc::sync_channel(0);
    std::thread::scope(|scope| {
        let handle = scope.spawn(move || {
            let mut first = true;
            run_campaign(
                config,
                RunLimits::default(),
                |id| {
                    factory_tx.send(id).unwrap();
                    Ok(LocalModel::new(id, Mode::Pass))
                },
                |_| {
                    if first {
                        first = false;
                        sink_tx.send(()).unwrap();
                        release_rx.recv().unwrap();
                    }
                    Ok(())
                },
            )
            .unwrap()
        });
        sink_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        for _ in 0..5 {
            factory_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        }
        assert!(matches!(
            factory_rx.recv_timeout(Duration::from_millis(20)),
            Err(mpsc::RecvTimeoutError::Timeout)
        ));
        release_tx.send(()).unwrap();
        let report = handle.join().unwrap();
        assert_eq!(
            (report.started, report.finished, report.delivered),
            (100, 100, 100)
        );
    });
}

#[test]
fn more_workers_than_jobs_and_zero_work_jobs_are_supported() {
    let mut config = config(4);
    config.jobs = 1;
    config.first_job = u64::MAX;
    config.fuzz.cases = 0;
    let report = run_campaign(
        config,
        RunLimits::default(),
        |id| {
            assert_eq!(id, u64::MAX);
            Ok(LocalModel::new(id, Mode::Panic))
        },
        |_| Ok(()),
    )
    .unwrap();
    assert_eq!(report.termination, CampaignTermination::JobsCompleted);
    assert_eq!(
        (
            report.started,
            report.finished,
            report.reported_transitions,
            report.reported_cases
        ),
        (1, 1, 0, 0)
    );
}

#[test]
fn panicking_panic_payload_cleanup_does_not_escape_job_or_sink_reporting() {
    struct Payload;
    impl Drop for Payload {
        fn drop(&mut self) {
            panic!("panic payload cleanup");
        }
    }
    for workers in [1, 2] {
        let mut config = config(workers);
        config.jobs = 2;
        let report = run_campaign(
            config,
            RunLimits::default(),
            |_| -> Result<LocalModel, ModelError> { std::panic::panic_any(Payload) },
            |job| {
                assert!(matches!(job.outcome, JobOutcome::Panicked(_)));
                Ok(())
            },
        )
        .unwrap();
        assert_eq!((report.started, report.finished, report.errors), (2, 2, 2));
        assert!(!report.accounting_complete);
    }
    let report = run_campaign(
        config(2),
        RunLimits::default(),
        |id| Ok(LocalModel::new(id, Mode::Pass)),
        |_| std::panic::panic_any(Payload),
    )
    .unwrap();
    assert!(matches!(
        report.termination,
        CampaignTermination::CallbackError(_)
    ));
    assert_eq!(report.started, report.finished);
}

#[test]
fn disposing_a_drained_report_cannot_unwind_past_the_original_sink_error() {
    use std::sync::Arc;

    #[derive(Clone)]
    struct Input(Arc<AtomicBool>);
    impl PartialEq for Input {
        fn eq(&self, _: &Self) -> bool {
            true
        }
    }
    impl Eq for Input {}
    impl Drop for Input {
        fn drop(&mut self) {
            if self.0.swap(false, Ordering::SeqCst) {
                panic!("drained input cleanup");
            }
        }
    }
    struct DropModel {
        armed: Arc<AtomicBool>,
        finished: Arc<Barrier>,
    }
    impl Drop for DropModel {
        fn drop(&mut self) {
            self.finished.wait();
        }
    }
    impl Model for DropModel {
        type State = bool;
        type Input = Input;
        type Output = ();
        fn metadata(&self) -> ModelMetadata {
            LocalModel::new(0, Mode::Pass).metadata()
        }
        fn initial_state(&self) -> Result<bool, ModelError> {
            Ok(false)
        }
        fn step(&self, _: &bool, _: &Input) -> Result<Transition<bool, ()>, ModelError> {
            Ok(Transition::accepted(true, Vec::new()))
        }
        fn check_state(&self, state: &bool) -> Result<Vec<Check>, ModelError> {
            Ok(vec![if *state {
                Check::failed("failure", "fixture")
            } else {
                Check::passed("failure")
            }])
        }
    }
    impl Generate for DropModel {
        fn generate(&self, _: &bool, _: &mut Rng) -> Result<Option<Input>, ModelError> {
            Ok(Some(Input(Arc::clone(&self.armed))))
        }
    }
    let armed = Arc::new(AtomicBool::new(false));
    let finished = Arc::new(Barrier::new(2));
    let mut config = config(2);
    config.jobs = 2;
    config.result_buffer = 0;
    let report = run_campaign(
        config,
        RunLimits::default(),
        |_| {
            Ok(DropModel {
                armed: Arc::clone(&armed),
                finished: Arc::clone(&finished),
            })
        },
        |job| {
            drop(job);
            armed.store(true, Ordering::SeqCst);
            Err(ModelError::new("original sink error"))
        },
    )
    .unwrap();
    assert_eq!(
        report.termination,
        CampaignTermination::CallbackError(ModelError::new("original sink error"))
    );
    assert_eq!(
        (report.started, report.finished, report.failures),
        (2, 2, 2)
    );
    assert!(!armed.load(Ordering::SeqCst));
}

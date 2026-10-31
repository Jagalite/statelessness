#[path = "../conformance/fixtures/native-campaign-public.rs"]
mod reference;
use reference::*;
use stateless::campaign::*;
use stateless::explore::{self, RunLimits};
use stateless::guided;
use std::sync::{Arc, Mutex, mpsc};
use std::time::Duration;

#[test]
fn independently_authored_wrapping_seeds_and_range_validation() {
    for row in seed_rows().into_iter().chain(validation_rows()) {
        println!("{row}");
    }
    if usize::BITS == 64 {
        let mut c = config();
        c.first_job = 0;
        c.jobs = usize::MAX;
        c.fuzz.cases = 1;
        c.fuzz.max_steps = 1;
        c.fuzz.max_transitions = 1;
        assert!(c.validate().is_ok()); // Rust-only usize domain; not a shared JSON row.
        c.fuzz.cases = 2;
        assert_eq!(
            c.validate().unwrap_err().0,
            "campaign aggregate cases overflow u64"
        );
    }
}

#[test]
fn exact_one_worker_public_callback_vectors() {
    let rows: Vec<_> = CASES.iter().map(|name| deterministic_case(name)).collect();
    for row in &rows {
        println!("{row}");
    }
    assert_public_goldens(
        concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/conformance/fixtures/native-campaign-golden.py"
        ),
        &rows,
    );
}

#[test]
fn each_public_campaign_job_matches_fresh_public_bare_reference() {
    for is_guided in [false, true] {
        for workers in [1, 3] {
            let mut c = config();
            c.first_job = 50;
            c.jobs = 7;
            c.workers = workers;
            c.result_buffer = 0;
            c.master_seed = u64::MAX;
            c.fuzz.cases = 3;
            c.fuzz.max_steps = 4;
            c.fuzz.max_transitions = 12;
            c.fuzz.mutation_percent = 100;
            let events = Log::default();
            let mut actual = Vec::new();
            let factory = |id| Ok(Counter::new(id, Mode::Random, &events));
            let sink = |j| {
                actual.push(j);
                Ok(())
            };
            let aggregate = if is_guided {
                run_guided_campaign(
                    c.clone(),
                    RunLimits::default(),
                    guidance(),
                    factory,
                    |id, _| Ok(CounterFeedback::new(id, &events)),
                    sink,
                )
            } else {
                run_campaign(c.clone(), RunLimits::default(), factory, sink)
            }
            .unwrap();
            assert_eq!(
                (aggregate.started, aggregate.finished, aggregate.delivered),
                (7, 7, 7)
            );
            // Preserve original receipt order; independently index the collected jobs.
            let mut ids = std::collections::BTreeSet::new();
            for j in &actual {
                assert!(ids.insert(j.job_id));
                let mut fc = c.fuzz.clone();
                fc.seed = job_seed(c.master_seed, j.job_id);
                let reference_events = Log::default();
                let m = Counter::new(j.job_id, Mode::Random, &reference_events);
                let expected = if is_guided {
                    guided::fuzz(
                        &m,
                        fc,
                        guidance(),
                        CounterFeedback::new(j.job_id, &reference_events),
                    )
                } else {
                    explore::fuzz(&m, fc)
                }
                .unwrap();
                let JobOutcome::Completed(actual_report) = &j.outcome else {
                    panic!("job did not complete")
                };
                assert_eq!(fuzz_report(actual_report), fuzz_report(&expected));
                // Compare every actual input/callback, not just endpoint state or RNG.
                let prefix = format!("{}:", j.job_id);
                let selected: Vec<_> = events
                    .lock()
                    .unwrap()
                    .iter()
                    .filter(|e| {
                        e.starts_with(&prefix)
                            && !e.ends_with(":metadata")
                            && !e.ends_with(":model_drop")
                    })
                    .cloned()
                    .collect();
                assert_eq!(selected, *reference_events.lock().unwrap());
            }
            assert_eq!(ids, (50..57).collect());
        }
    }
}

#[test]
fn guidance_validation_precedes_campaign_validation_without_callbacks() {
    for field in 0..4 {
        let mut g = guidance();
        match field {
            0 => g.max_corpus_entries = 0,
            1 => g.max_corpus_inputs = 0,
            2 => g.max_features = 0,
            _ => g.max_features_per_observation = 0,
        }
        let mut c = config();
        c.jobs = 0;
        let result = run_guided_campaign(
            c,
            RunLimits::default(),
            g,
            |_| -> Result<Counter, stateless::ModelError> { panic!("factory") },
            |_, _| -> Result<CounterFeedback, stateless::ModelError> { panic!("feedback factory") },
            |_| panic!("sink"),
        );
        assert_eq!(result.unwrap_err().0, "guidance limits must be positive");
    }
}

#[test]
fn configured_workers_select_caller_or_spawn_even_for_one_job() {
    let caller = std::thread::current().id();
    for workers in [1, 2, 256] {
        let mut c = config();
        c.workers = workers;
        let events = Log::default();
        run_campaign(
            c,
            RunLimits::default(),
            |id| {
                assert_eq!(std::thread::current().id() == caller, workers == 1);
                Ok(Counter::new(id, Mode::Pass, &events))
            },
            |_| {
                assert_eq!(std::thread::current().id(), caller);
                Ok(())
            },
        )
        .unwrap();
    }
}

#[test]
fn rendezvous_receipt_releases_worker_before_sink_and_forces_out_of_order() {
    let caller = std::thread::current().id();
    let events = Log::default();
    let (release_tx, release_rx) = mpsc::channel();
    let release_rx = Mutex::new(release_rx);
    let (claimed_tx, claimed_rx) = mpsc::channel();
    let threads = Arc::new(Mutex::new(std::collections::HashSet::new()));
    let mut c = config();
    c.first_job = 0;
    c.jobs = 3;
    c.workers = 2;
    c.result_buffer = 0;
    let mut received = Vec::new();
    let result = run_campaign(
        c,
        RunLimits::default(),
        |id| {
            threads.lock().unwrap().insert(std::thread::current().id());
            assert_ne!(std::thread::current().id(), caller);
            if id == 0 {
                release_rx
                    .lock()
                    .unwrap()
                    .recv_timeout(Duration::from_secs(10))
                    .unwrap();
            }
            if id == 2 {
                claimed_tx.send(()).unwrap();
            }
            Ok(Counter::new(id, Mode::Pass, &events))
        },
        |job| {
            assert_eq!(std::thread::current().id(), caller);
            received.push(job.job_id);
            if received.len() == 1 {
                assert_eq!(job.job_id, 1);
                // Only the worker whose rendezvous send was received can now claim2;
                // worker0 remains blocked. A sink-ack-before-reclaim bug times out.
                let progressed = claimed_rx.recv_timeout(Duration::from_secs(10));
                release_tx.send(()).unwrap();
                assert!(progressed.is_ok());
            }
            Ok(())
        },
    )
    .unwrap();
    assert_eq!(threads.lock().unwrap().len(), 2);
    assert_eq!(received[0], 1);
    assert_eq!(
        (result.started, result.finished, result.delivered),
        (3, 3, 3)
    );
}

//! Identical fixed jobs at different worker counts; no dependencies or I/O in
//! measured model callbacks. Checks scan real payloads or inspect single cells.
use std::cell::Cell;
use std::hint::black_box;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use stateless::automatic::Auto;
use stateless::campaign::{CampaignConfig, JobOutcome, run_campaign};
use stateless::explore::{FuzzConfig, RunLimits};
use stateless::{Check, Enumerate, Generate, Model, ModelError, ModelMetadata, Rng, Transition};

const JOBS: usize = 64;
const CASES: usize = 4;
const STEPS: usize = 64;
const WORDS: usize = 4096;
const IDS: [&str; 8] = ["a", "b", "c", "d", "e", "f", "g", "h"];

#[derive(Clone, PartialEq, Eq)]
struct State {
    tick: u64,
    data: Vec<u64>,
}

struct Fixture {
    scans: bool,
    job: usize,
    signature: Cell<u64>,
    completed_signatures: Arc<Vec<AtomicU64>>,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        // One write per job, outside its transition loop. This instrumentation
        // does not affect modeled results and verifies the executed sequences.
        self.completed_signatures[self.job].store(self.signature.get(), Ordering::Relaxed);
    }
}

impl Model for Fixture {
    type State = State;
    type Input = u8;
    type Output = ();

    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "campaign-scaling-fixture".into(),
            build: "v1".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
        }
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        Ok(State {
            tick: 0,
            data: vec![0; WORDS],
        })
    }
    fn step(&self, before: &State, input: &u8) -> Result<Transition<State, ()>, ModelError> {
        let mut state = before.clone();
        let index = (before.tick as usize * 17 + usize::from(*input)) % WORDS;
        state.data[index] = (state.data[index] + u64::from(*input)) & 255;
        state.tick += 1;
        self.signature.set(
            self.signature
                .get()
                .rotate_left(7)
                .wrapping_add(u64::from(*input) + before.tick * 13 + 1),
        );
        Ok(Transition::accepted(state, Vec::new()))
    }
    fn check_state(&self, state: &State) -> Result<Vec<Check>, ModelError> {
        let mut checks = Vec::new();
        self.check_state_into(state, &mut checks)?;
        Ok(checks)
    }
    fn check_state_into(&self, state: &State, checks: &mut Vec<Check>) -> Result<(), ModelError> {
        for (index, id) in IDS.iter().enumerate() {
            let threshold = black_box(255 + index as u64);
            let valid = if self.scans {
                black_box(&state.data)
                    .iter()
                    .all(|value| *value <= threshold)
            } else {
                black_box(&state.data)[index] <= threshold
            };
            checks.push(if valid {
                Check::passed(*id)
            } else {
                Check::failed(*id, "out of range")
            });
        }
        Ok(())
    }
}

impl Enumerate for Fixture {
    fn inputs(&self, _: &State) -> Result<Vec<u8>, ModelError> {
        Ok((0..8).collect())
    }
    fn input_iter<'a>(
        &'a self,
        _: &'a State,
    ) -> Result<Box<dyn Iterator<Item = u8> + 'a>, ModelError> {
        Ok(Box::new(0..8))
    }
}

impl Generate for Fixture {
    fn generate(&self, _: &State, rng: &mut Rng) -> Result<Option<u8>, ModelError> {
        Ok(rng.index(8).map(|index| index as u8))
    }
    fn is_enabled(&self, _: &State, input: &u8) -> Result<bool, ModelError> {
        Ok(*input < 8)
    }
}

type Observation = (u64, u64, usize, u64);

fn run<M, F>(workers: usize, factory: F) -> (Duration, Vec<Observation>)
where
    M: Generate<Input = u8>,
    F: Fn(u64) -> Result<M, ModelError> + Sync,
{
    let config = CampaignConfig {
        master_seed: 42,
        jobs: JOBS,
        workers,
        result_buffer: workers,
        stop_on_failure: false,
        fuzz: FuzzConfig {
            seed: 0,
            cases: CASES,
            max_steps: STEPS,
            max_transitions: (CASES * STEPS) as u64,
            mutation_percent: 50,
        },
        ..CampaignConfig::default()
    };
    let mut observations = Vec::with_capacity(JOBS);
    let started = Instant::now();
    let report = run_campaign(config, RunLimits::default(), factory, |job| {
        let JobOutcome::Completed(result) = job.outcome else {
            panic!("job failed")
        };
        assert!(result.failure.is_none());
        assert_eq!(result.transitions, (CASES * STEPS) as u64);
        observations.push((job.job_id, job.seed, result.cases, result.transitions));
        Ok(())
    })
    .unwrap();
    let elapsed = started.elapsed();
    assert_eq!(report.finished, JOBS);
    assert_eq!(report.failures, 0);
    assert_eq!(report.errors, 0);
    observations.sort_unstable();
    (elapsed, observations)
}

fn main() {
    eprintln!(
        "Fixed 64 jobs x 4 cases x 64 steps; 32 KiB states; eight local checks or full scans."
    );
    eprintln!(
        "One warmup and five whole-campaign timings; includes worker startup and report delivery."
    );
    eprintln!(
        "Each sample asserts identical job reports AND executed input signatures across worker counts."
    );
    println!(
        "generation,checking,workers,jobs,transitions,median_ns,min_ns,max_ns,transitions_per_second,speedup"
    );
    for scans in [false, true] {
        for automatic in [false, true] {
            let mut baseline = None;
            let mut expected = None;
            for workers in [1, 2, 4] {
                let mut samples = Vec::new();
                for iteration in 0..6 {
                    let audit = Arc::new((0..JOBS).map(|_| AtomicU64::new(0)).collect::<Vec<_>>());
                    let factory = |job| Fixture {
                        scans,
                        job: job as usize,
                        signature: Cell::new(0),
                        completed_signatures: Arc::clone(&audit),
                    };
                    let (elapsed, observations) = if automatic {
                        run(workers, |id| Ok(Auto::new(factory(id))))
                    } else {
                        run(workers, |id| Ok(factory(id)))
                    };
                    let signatures: Vec<_> =
                        audit.iter().map(|v| v.load(Ordering::Relaxed)).collect();
                    let actual = (observations, signatures);
                    if let Some(expected) = &expected {
                        assert_eq!(&actual, expected);
                    } else {
                        expected = Some(actual);
                    }
                    if iteration != 0 {
                        samples.push(elapsed.as_nanos());
                    }
                }
                samples.sort_unstable();
                let median = samples[2] as f64;
                let first = *baseline.get_or_insert(median);
                let transitions = JOBS * CASES * STEPS;
                println!(
                    "{},{},{workers},{JOBS},{transitions},{},{},{},{:.1},{:.3}",
                    if automatic { "automatic" } else { "custom" },
                    if scans { "full_scan" } else { "local" },
                    samples[2],
                    samples[0],
                    samples[4],
                    transitions as f64 * 1e9 / median,
                    first / median
                );
            }
        }
    }
}

//! Native Rust baseline for the ABI2 language workloads. Compile separately from
//! execution; raw timing is not an allocation/RSS or performance qualification.
//! STATELESS_BENCH_ITERS (default 5), STATELESS_BENCH_SAMPLES (default 7).
use std::cell::Cell;
use std::hash::{Hash, Hasher};
use std::hint::black_box;
use std::rc::Rc;
use std::time::Instant;

use stateless::execution::{
    CheckPolicy, ReplayOptions, ReplayOutcome, check_observed, record, replay,
};
use stateless::explore::{
    Failure, FuzzConfig, SearchConfig, SearchTermination, ShrinkConfig, ShrinkLimits, enumerate,
    fuzz, shrink_with_limits,
};
use stateless::monitor::{Recorder, RecorderOptions};
use stateless::trace::{ReadLimits, RunConfig, Trace};
use stateless::{
    Check, Enumerate, Generate, Model, ModelCodec, ModelError, ModelMetadata, Rng, Transition,
    TransitionRef,
};

struct State {
    value: u32,
    payload: Vec<u8>,
    callbacks: Rc<Cell<u64>>,
}
impl Clone for State {
    fn clone(&self) -> Self {
        self.callbacks.set(self.callbacks.get() + 1);
        Self {
            value: self.value,
            payload: self.payload.clone(),
            callbacks: Rc::clone(&self.callbacks),
        }
    }
}
impl PartialEq for State {
    fn eq(&self, other: &Self) -> bool {
        self.callbacks.set(self.callbacks.get() + 1);
        self.value == other.value
    }
}
impl Eq for State {}
impl Hash for State {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.callbacks.set(self.callbacks.get() + 1);
        self.value.hash(state);
    }
}

struct Counter {
    payload: usize,
    iterations: u64,
    fail: bool,
    callbacks: Rc<Cell<u64>>,
    sink: Cell<u64>,
}
impl Counter {
    fn new(kind: &str, fail: bool) -> Self {
        Self {
            payload: if kind == "large" { 65_536 } else { 0 },
            iterations: if kind == "expensive" { 4096 } else { 0 },
            fail,
            callbacks: Rc::new(Cell::new(0)),
            sink: Cell::new(0),
        }
    }
    fn hit(&self) {
        self.callbacks.set(self.callbacks.get() + 1);
    }
    fn state(&self, value: u32) -> State {
        State {
            value,
            payload: vec![0; self.payload],
            callbacks: Rc::clone(&self.callbacks),
        }
    }
}
impl Model for Counter {
    type State = State;
    type Input = u8;
    type Output = u32;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "abi-v2-matched-counter".into(),
            build: "benchmark-v1".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
        }
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        self.hit();
        Ok(self.state(0))
    }
    fn step(&self, state: &State, input: &u8) -> Result<Transition<State, u32>, ModelError> {
        self.hit();
        Ok(Transition::accepted(
            State {
                value: (state.value + u32::from(*input)) % 32,
                payload: state.payload.clone(),
                callbacks: Rc::clone(&self.callbacks),
            },
            vec![state.value],
        ))
    }
    fn check_state(&self, state: &State) -> Result<Vec<Check>, ModelError> {
        self.hit();
        let mut digest = u64::from(black_box(state.value));
        for i in 0..self.iterations {
            digest = (digest ^ i)
                .wrapping_mul(0x9e3779b97f4a7c15)
                .wrapping_add(1);
        }
        self.sink.set(self.sink.get() ^ digest);
        Ok(vec![
            if state.value >= 32 || state.payload.len() != self.payload {
                Check::failed("valid", "invalid state")
            } else if self.fail && state.value >= 24 {
                Check::failed("bound", "state reached failure region")
            } else {
                Check::passed("valid")
            },
        ])
    }
    fn check_transition(
        &self,
        before: &State,
        input: &u8,
        transition: &TransitionRef<'_, State, u32>,
    ) -> Result<Vec<Check>, ModelError> {
        self.hit();
        Ok(vec![
            if transition.state.value == (before.value + u32::from(*input)) % 32
                && transition.outputs == [before.value]
            {
                Check::passed("transition")
            } else {
                Check::failed("transition", "inconsistent transition")
            },
        ])
    }
    fn estimated_state_bytes(&self, state: &State) -> Option<usize> {
        self.hit();
        Some(4 + state.payload.len())
    }
}
impl Enumerate for Counter {
    fn inputs(&self, _: &State) -> Result<Vec<u8>, ModelError> {
        self.hit();
        Ok(vec![1, 2])
    }
}
impl Generate for Counter {
    fn generate(&self, _: &State, rng: &mut Rng) -> Result<Option<u8>, ModelError> {
        self.hit();
        Ok(rng.index(2).map(|i| i as u8 + 1))
    }
    fn is_enabled(&self, _: &State, input: &u8) -> Result<bool, ModelError> {
        self.hit();
        Ok(matches!(input, 1 | 2))
    }
    fn simpler_inputs(&self, input: &u8) -> Vec<u8> {
        self.hit();
        if *input == 2 { vec![1] } else { vec![] }
    }
}
impl ModelCodec for Counter {
    fn encode_state(&self, state: &State) -> Result<Vec<u8>, ModelError> {
        self.hit();
        let mut bytes = state.value.to_le_bytes().to_vec();
        bytes.extend_from_slice(&state.payload);
        Ok(bytes)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<State, ModelError> {
        self.hit();
        if bytes.len() != 4 + self.payload {
            return Err(ModelError::new("state length"));
        }
        Ok(State {
            value: u32::from_le_bytes(bytes[..4].try_into().expect("four bytes")),
            payload: bytes[4..].to_vec(),
            callbacks: Rc::clone(&self.callbacks),
        })
    }
    fn encode_input(&self, input: &u8) -> Result<Vec<u8>, ModelError> {
        self.hit();
        Ok(vec![*input])
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        self.hit();
        match bytes {
            [value] => Ok(*value),
            _ => Err(ModelError::new("input length")),
        }
    }
    fn encode_output(&self, output: &u32) -> Result<Vec<u8>, ModelError> {
        self.hit();
        Ok(output.to_le_bytes().to_vec())
    }
}

fn fuzz_config() -> FuzzConfig {
    FuzzConfig {
        seed: 0,
        cases: 1,
        max_steps: 64,
        max_transitions: 256,
        mutation_percent: 0,
    }
}
fn record_binary(model: &Counter, inputs: &[u8]) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    let trace = record(model, inputs.iter().copied(), RunConfig::default(), 64)?;
    let mut artifact = Vec::new();
    trace.write_to(&mut artifact)?;
    Ok(artifact)
}

struct Workload {
    initial: State,
    inputs: Vec<u8>,
    observations: Vec<Transition<State, u32>>,
    artifact: Option<Vec<u8>>,
    failure: Option<Failure<u8>>,
}
impl Workload {
    fn prepare(model: &Counter, operation: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let inputs: Vec<_> = (0..64).map(|i| (i % 2 + 1) as u8).collect();
        let artifact = if operation == "replay" {
            Some(record_binary(model, &inputs)?)
        } else {
            None
        };
        let failure = if operation == "shrink" {
            Some(
                fuzz(model, fuzz_config())?
                    .failure
                    .expect("missing seed failure"),
            )
        } else {
            None
        };
        let mut observations = Vec::new();
        if operation == "observation" {
            observations.push(Transition::accepted(model.state(2), vec![0]));
        } else if operation == "ring" {
            let mut before = 0;
            for input in &inputs {
                let after = (before + u32::from(*input)) % 32;
                observations.push(Transition::accepted(model.state(after), vec![before]));
                before = after;
            }
        }
        Ok(Self {
            initial: model.state(0),
            inputs,
            observations,
            artifact,
            failure,
        })
    }
    fn run(&self, model: &Counter, operation: &str) -> Result<(), Box<dyn std::error::Error>> {
        match operation {
            "observation" => {
                let checks = check_observed(
                    model,
                    &self.initial,
                    &2,
                    &self.observations[0],
                    1,
                    CheckPolicy::default(),
                )?;
                assert_eq!(checks.len(), 2);
                assert!(checks.iter().all(|c| !c.is_failure()));
                black_box(checks);
            }
            "bfs" => {
                let report = enumerate(
                    model,
                    SearchConfig {
                        max_states: 64,
                        max_transitions: 256,
                        max_depth: 64,
                    },
                )?;
                assert_eq!(
                    (report.states, report.transitions, report.termination),
                    (32, 64, SearchTermination::GraphExhausted)
                );
                black_box(report);
            }
            "record" => {
                assert!(!black_box(record_binary(model, &self.inputs)?).is_empty());
            }
            "replay" => {
                let artifact = self.artifact.as_ref().expect("prepared artifact");
                let trace = Trace::read_from(artifact.as_slice(), &ReadLimits::default())?;
                let report = replay(model, &trace, ReplayOptions::default())?;
                assert_eq!(report.outcome, ReplayOutcome::Exact);
                assert_eq!(report.steps_verified, 64);
                black_box(report);
            }
            "fuzz" => {
                assert!(black_box(fuzz(model, fuzz_config())?).failure.is_some());
            }
            "shrink" => {
                let report = shrink_with_limits(
                    model,
                    self.failure.as_ref().expect("prepared failure"),
                    ShrinkConfig { max_attempts: 256 },
                    ShrinkLimits {
                        max_replayed_transitions: Some(4096),
                        ..ShrinkLimits::default()
                    },
                )?;
                assert!(report.validated_original);
                black_box(report);
            }
            "ring" => {
                let mut recorder = Recorder::with_options(
                    model,
                    &self.initial,
                    RunConfig::default(),
                    RecorderOptions {
                        max_steps: 16,
                        max_retained_bytes: 16 * 1024 * 1024,
                        ..RecorderOptions::default()
                    },
                )?;
                let mut before = &self.initial;
                for (input, transition) in self.inputs.iter().zip(&self.observations) {
                    recorder.observe(model, before, input, transition)?;
                    before = &transition.state;
                }
                let mut artifact = Vec::new();
                recorder.snapshot().write_to(&mut artifact)?;
                assert!(!black_box(artifact).is_empty());
            }
            _ => panic!("unknown workload"),
        }
        Ok(())
    }
}

fn positive_env(name: &str, default: usize) -> Result<usize, Box<dyn std::error::Error>> {
    let value = std::env::var(name).map_or(Ok(default), |v| v.parse::<usize>())?;
    if value == 0 {
        return Err(format!("{name} must be positive").into());
    }
    Ok(value)
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let iterations = positive_env("STATELESS_BENCH_ITERS", 5)?;
    let samples = positive_env("STATELESS_BENCH_SAMPLES", 7)?;
    if samples < 2 {
        return Err("at least two samples required".into());
    }
    let mut cells = Vec::new();
    for kind in ["cheap", "large", "expensive"] {
        for operation in [
            "observation",
            "bfs",
            "record",
            "replay",
            "fuzz",
            "shrink",
            "ring",
        ] {
            let model = Counter::new(kind, matches!(operation, "fuzz" | "shrink"));
            let work = Workload::prepare(&model, operation)?;
            let mut times = Vec::new();
            let mut callbacks = Vec::new();
            for _ in 0..samples {
                model.callbacks.set(0);
                let started = Instant::now();
                for _ in 0..iterations {
                    work.run(black_box(&model), operation)?;
                }
                times.push(started.elapsed().as_nanos() as f64 / iterations as f64);
                callbacks.push(model.callbacks.get() as f64 / iterations as f64);
                black_box(model.sink.get());
            }
            cells.push(format!("{{\"model\":\"{kind}\",\"workload\":\"{operation}\",\"samples_ns\":{times:?},\"callbacks_per_operation\":{callbacks:?}}}"));
        }
    }
    println!(
        "{{\"schema\":\"stateless.binding-raw-timing.v1\",\"engine_family\":\"rust-shared\",\"implementation\":\"rust-native\",\"source_build\":\"{}\",\"parameters\":{{\"states\":32,\"inputs\":[1,2],\"trace_steps\":64,\"large_payload_bytes\":65536,\"expensive_iterations\":4096,\"failure_state\":24,\"seed\":0}},\"callback_counter_scope\":\"model methods plus state Clone/Eq/Hash; scalar input/output traits uninstrumented\",\"qualification\":\"incomplete: external allocation/RSS evidence and baseline budgets required\",\"cells\":[{}]}}",
        env!("STATELESS_BUILD_ID"),
        cells.join(",")
    );
    Ok(())
}

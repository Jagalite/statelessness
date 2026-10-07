//! Synthetic scaling review. No third-party dependencies or production claims.
//! Timings and requested-heap probes are separate; output is CSV on stdout.

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;
use std::hint::black_box;
use std::io;
use std::num::NonZeroU64;
use std::sync::atomic::{AtomicBool, AtomicIsize, AtomicUsize, Ordering::Relaxed};
use std::time::Instant;

use stateless::execution::{
    CheckPolicy, ReplayOptions, ReplayOutcome, check_observed, check_observed_into, record, replay,
};
use stateless::explore::{FuzzConfig, SearchConfig, ShrinkConfig, enumerate, fuzz, shrink};
use stateless::monitor::{Recorder, RecorderOptions};
use stateless::trace::{ReadLimits, RunConfig};
use stateless::{
    Check, EncodeBuffer, Enumerate, Generate, Model, ModelCodec, ModelError, ModelMetadata,
    PropertyId, Rng, Transition,
};

struct MeteredSystem;

// Make serialized bytes observable to the optimizer without retaining a second
// output copy. io::sink alone can let CRC/serialization work be optimized away.
#[derive(Default)]
struct ObservedSink {
    bytes: usize,
}

impl io::Write for ObservedSink {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        black_box(bytes);
        self.bytes += bytes.len();
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[global_allocator]
static ALLOCATOR: MeteredSystem = MeteredSystem;
static PROBING: AtomicBool = AtomicBool::new(false);
static ALLOCS: AtomicUsize = AtomicUsize::new(0);
static BYTES: AtomicUsize = AtomicUsize::new(0);
static LIVE_DELTA: AtomicIsize = AtomicIsize::new(0);
static PEAK_DELTA: AtomicIsize = AtomicIsize::new(0);

fn allocated(size: usize, prior: usize) {
    if PROBING.load(Relaxed) {
        ALLOCS.fetch_add(1, Relaxed);
        BYTES.fetch_add(size, Relaxed);
        let delta = size as isize - prior as isize;
        let live = LIVE_DELTA.fetch_add(delta, Relaxed) + delta;
        PEAK_DELTA.fetch_max(live, Relaxed);
    }
}

// SAFETY: Every operation delegates the original pointer, layout and requested
// size to System. Accounting uses atomics only and never allocates or unwinds.
unsafe impl GlobalAlloc for MeteredSystem {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        // SAFETY: This is the identical allocation request received by this allocator.
        let ptr = unsafe { System.alloc(layout) };
        if !ptr.is_null() {
            allocated(layout.size(), 0);
        }
        ptr
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        // SAFETY: Layout is forwarded unchanged to the backing allocator.
        let ptr = unsafe { System.alloc_zeroed(layout) };
        if !ptr.is_null() {
            allocated(layout.size(), 0);
        }
        ptr
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        if PROBING.load(Relaxed) {
            LIVE_DELTA.fetch_sub(layout.size() as isize, Relaxed);
        }
        // SAFETY: All allocations originate from System; pointer/layout are unchanged.
        unsafe {
            System.dealloc(ptr, layout);
        }
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        // SAFETY: Pointer, old layout and new size are forwarded unchanged to System.
        let next = unsafe { System.realloc(ptr, layout, size) };
        if !next.is_null() {
            allocated(size, layout.size());
        }
        next
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct State {
    counter: u64,
    blob: Vec<u8>,
}

struct Synthetic {
    bytes: usize,
    ids: Vec<PropertyId>,
    scan: bool,
    graph_states: u64,
    fanout: usize,
    lazy_inputs: bool,
    fail_after: Option<u64>,
    steps: Cell<u64>, // instrumentation only; does not affect transition results
}

impl Synthetic {
    fn new(bytes: usize, checks: usize, scan: bool) -> Self {
        Self {
            bytes,
            ids: (0..checks)
                .map(|i| format!("property.{i}").into())
                .collect(),
            scan,
            graph_states: 128,
            fanout: 1,
            lazy_inputs: true,
            fail_after: None,
            steps: Cell::new(0),
        }
    }
}

impl Model for Synthetic {
    type State = State;
    type Input = u64;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "synthetic-scaling".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: format!(
                "bytes={};checks={};scan={}",
                self.bytes,
                self.ids.len(),
                self.scan
            ),
        }
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        Ok(State {
            counter: 0,
            blob: vec![0; self.bytes],
        })
    }
    fn step(&self, state: &State, input: &u64) -> Result<Transition<State, ()>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        let mut next = state.clone(); // deliberate application-side full copy
        next.counter += input;
        Ok(Transition::accepted(next, vec![]))
    }
    fn check_state(&self, state: &State) -> Result<Vec<Check>, ModelError> {
        let mut checks =
            Vec::with_capacity(self.ids.len() + usize::from(self.fail_after.is_some()));
        self.check_state_into(state, &mut checks)?;
        Ok(checks)
    }
    fn check_state_into(&self, state: &State, checks: &mut Vec<Check>) -> Result<(), ModelError> {
        checks.reserve(self.ids.len() + usize::from(self.fail_after.is_some()));
        for (index, id) in self.ids.iter().enumerate() {
            let valid = if self.scan {
                // Distinct thresholds and opaque slice prevent common-result
                // hoisting: each property deliberately walks the whole blob.
                let threshold = black_box(128 + (index % 127) as u8);
                black_box(&state.blob)
                    .iter()
                    .all(|&value| value <= threshold)
            } else {
                black_box(&state.blob)[index % state.blob.len()] == 0
            };
            checks.push(if valid {
                Check::passed(id.clone())
            } else {
                Check::failed(id.clone(), "synthetic payload constraint")
            });
        }
        if let Some(limit) = self.fail_after {
            checks.push(if state.counter >= limit {
                Check::failed("counter.limit", "synthetic shrink target")
            } else {
                Check::passed("counter.limit")
            });
        }
        Ok(())
    }
    fn estimated_state_bytes(&self, state: &State) -> Option<usize> {
        Some(size_of::<State>() + state.blob.capacity())
    }
}

impl Generate for Synthetic {
    fn generate(&self, _: &State, _: &mut Rng) -> Result<Option<u64>, ModelError> {
        Ok(Some(1))
    }
}
impl Enumerate for Synthetic {
    fn input_iter<'a>(
        &'a self,
        state: &'a State,
    ) -> Result<Box<dyn Iterator<Item = u64> + 'a>, ModelError> {
        if self.lazy_inputs {
            let count = if state.counter + 1 < self.graph_states {
                self.fanout
            } else {
                0
            };
            Ok(Box::new(std::iter::repeat_n(1, count)))
        } else {
            Ok(Box::new(self.inputs(state)?.into_iter()))
        }
    }
    fn inputs(&self, state: &State) -> Result<Vec<u64>, ModelError> {
        Ok(if state.counter + 1 < self.graph_states {
            vec![1; self.fanout]
        } else {
            vec![]
        })
    }
}
impl ModelCodec for Synthetic {
    fn encode_state_into(
        &self,
        state: &State,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_slice(&state.counter.to_le_bytes())?;
        out.extend_from_slice(&(state.blob.len() as u64).to_le_bytes())?;
        out.extend_from_slice(&state.blob)
    }
    fn encode_input_into(&self, input: &u64, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        out.extend_from_slice(&input.to_le_bytes())
    }
    fn encode_output_into(&self, _: &(), _: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        Ok(())
    }
    fn encode_state(&self, state: &State) -> Result<Vec<u8>, ModelError> {
        let mut bytes = Vec::with_capacity(16 + state.blob.len());
        bytes.extend_from_slice(&state.counter.to_le_bytes());
        bytes.extend_from_slice(&(state.blob.len() as u64).to_le_bytes());
        bytes.extend_from_slice(&state.blob);
        Ok(bytes)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<State, ModelError> {
        if bytes.len() != self.bytes + 16 {
            return Err(ModelError::new("wrong synthetic state length"));
        }
        let counter = u64::from_le_bytes(bytes[..8].try_into().unwrap());
        let length = u64::from_le_bytes(bytes[8..16].try_into().unwrap());
        if length != self.bytes as u64 {
            return Err(ModelError::new("wrong synthetic payload length"));
        }
        Ok(State {
            counter,
            blob: bytes[16..].to_vec(),
        })
    }
    fn encode_input(&self, input: &u64) -> Result<Vec<u8>, ModelError> {
        Ok(input.to_le_bytes().to_vec())
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<u64, ModelError> {
        Ok(u64::from_le_bytes(bytes.try_into().map_err(|_| {
            ModelError::new("wrong synthetic input length")
        })?))
    }
    fn encode_output(&self, _: &()) -> Result<Vec<u8>, ModelError> {
        Ok(vec![])
    }
}

fn measure<T>(name: &str, model: &Synthetic, units: usize, mut operation: impl FnMut() -> T) -> T {
    for _ in 0..3 {
        black_box(operation());
    }
    let start = Instant::now();
    black_box(operation());
    let estimate = start.elapsed().as_nanos().max(1);
    let iterations = (15_000_000 / estimate).clamp(1, 20_000) as usize;
    let mut samples = Vec::with_capacity(5);
    for _ in 0..5 {
        let start = Instant::now();
        for _ in 0..iterations {
            black_box(operation());
        }
        samples.push(start.elapsed().as_nanos() as f64 / iterations as f64);
    }
    samples.sort_by(f64::total_cmp);

    model.steps.set(0);
    ALLOCS.store(0, Relaxed);
    BYTES.store(0, Relaxed);
    LIVE_DELTA.store(0, Relaxed);
    PEAK_DELTA.store(0, Relaxed);
    PROBING.store(true, Relaxed);
    let result = operation();
    PROBING.store(false, Relaxed);
    let allocations = ALLOCS.load(Relaxed);
    let allocated_bytes = BYTES.load(Relaxed);
    let peak = PEAK_DELTA.load(Relaxed);
    let retained = LIVE_DELTA.load(Relaxed);
    println!(
        "{name},{},{},{},{units},{iterations},{:.1},{:.1},{:.1},{allocations},{allocated_bytes},{peak},{retained},{}",
        model.bytes,
        model.ids.len(),
        model.scan,
        samples[2],
        samples[0],
        samples[4],
        model.steps.get()
    );
    black_box(result)
}

fn main() {
    eprintln!(
        "Synthetic scaling review: Apple/platform metadata is recorded by the invoking runner."
    );
    eprintln!(
        "Five warmed sample means; allocator counters disabled during timing (wrapper branch remains)."
    );
    eprintln!(
        "Allocation probe is a separate single operation: requested bytes, not RSS or allocator metadata; peak is additional live heap relative to operation start."
    );
    eprintln!(
        "sizeof(Check)={} sizeof(State)={}",
        size_of::<Check>(),
        size_of::<State>()
    );
    println!(
        "operation,state_payload_bytes,checks,scan,units,iterations,median_ns,min_ns,max_ns,allocation_calls,allocated_bytes,peak_additional_live_bytes,retained_delta_bytes,reducer_calls"
    );

    for bytes in [4 * 1024, 64 * 1024, 1024 * 1024, 8 * 1024 * 1024] {
        let model = Synthetic::new(bytes, 10, false);
        let before = model.initial_state().unwrap();
        let transition = model.step(&before, &0).unwrap();
        measure("reducer_full_clone", &model, 1, || {
            model.step(black_box(&before), &1).unwrap()
        });
        measure("check_local", &model, 1, || {
            check_observed(&model, &before, &0, &transition, 1, CheckPolicy::default()).unwrap()
        });
        let scanning = Synthetic::new(bytes, 10, true);
        measure("check_full_scan", &scanning, 1, || {
            check_observed(
                &scanning,
                &before,
                &0,
                &transition,
                1,
                CheckPolicy::default(),
            )
            .unwrap()
        });
    }
    for count in [1, 100, 1_000, 10_000] {
        let model = Synthetic::new(64 * 1024, count, false);
        let before = model.initial_state().unwrap();
        let transition = model.step(&before, &0).unwrap();
        measure("check_count", &model, 1, || {
            check_observed(&model, &before, &0, &transition, 1, CheckPolicy::default()).unwrap()
        });
        let mut checks = Vec::new();
        measure("check_count_reused", &model, 1, || {
            check_observed_into(
                &model,
                &before,
                &0,
                &transition,
                1,
                CheckPolicy::default(),
                &mut checks,
            )
            .unwrap();
            black_box(&checks);
        });
        assert_eq!(
            ALLOCS.load(Relaxed),
            0,
            "warmed passing checks must not allocate"
        );
    }
    for count in [1, 100] {
        let model = Synthetic::new(1024 * 1024, count, true);
        let before = model.initial_state().unwrap();
        let transition = model.step(&before, &0).unwrap();
        measure("scan_count", &model, 1, || {
            check_observed(&model, &before, &0, &transition, 1, CheckPolicy::default()).unwrap()
        });
    }
    let model = Synthetic::new(1024 * 1024, 100, true);
    let before = model.initial_state().unwrap();
    let transition = model.step(&before, &0).unwrap();
    measure("periodic_skipped_step", &model, 1, || {
        check_observed(
            &model,
            &before,
            &0,
            &transition,
            1,
            CheckPolicy {
                state_every: NonZeroU64::new(100).unwrap(),
                transition_checks: true,
            },
        )
        .unwrap()
    });

    for bytes in [4 * 1024, 64 * 1024, 1024 * 1024, 8 * 1024 * 1024] {
        let model = Synthetic::new(bytes, 10, false);
        let before = model.initial_state().unwrap();
        let transition = model.step(&before, &0).unwrap();
        let limits = ReadLimits {
            max_total_bytes: 256 * 1024 * 1024,
            max_frame_bytes: 16 * 1024 * 1024,
            max_blob_bytes: 16 * 1024 * 1024,
            ..ReadLimits::default()
        };
        eprintln!(
            "default_capture state_bytes={bytes}: {:?}",
            Recorder::new(&model, &before, RunConfig::default(), 8).map(|_| ())
        );
        let mut recorder = Recorder::with_options(
            &model,
            &before,
            RunConfig::default(),
            RecorderOptions {
                max_steps: 8,
                max_retained_bytes: limits.max_total_bytes,
                limits: limits.clone(),
            },
        )
        .unwrap();
        for _ in 0..8 {
            recorder.observe(&model, &before, &0, &transition).unwrap();
        }
        measure("recorder_observe_full_window", &model, 1, || {
            black_box(recorder.observe(&model, &before, &0, &transition).unwrap());
        });
        measure("recorder_snapshot_8", &model, 8, || recorder.snapshot());
        measure("recorder_export_direct_8", &model, 8, || {
            let mut sink = ObservedSink::default();
            recorder.write_with_limits(&mut sink, &limits).unwrap();
            sink.bytes
        });
        let trace = recorder.snapshot();
        eprintln!(
            "default_export state_bytes={bytes} checks=10 retained=8: {:?}",
            trace.write_to(io::sink())
        );
        measure("trace_encode_sink_8", &model, 8, || {
            let mut sink = ObservedSink::default();
            trace.write_with_limits(&mut sink, &limits).unwrap();
            sink.bytes
        });
        let replayed = measure("trace_replay_8", &model, 8, || {
            replay(&model, &trace, ReplayOptions::default()).unwrap()
        });
        assert_eq!(replayed.outcome, ReplayOutcome::Exact);
        assert_eq!(replayed.steps_verified, 8);
    }
    for bytes in [4 * 1024, 64 * 1024, 1024 * 1024] {
        let mut model = Synthetic::new(bytes, 10, false);
        model.graph_states = if bytes < 1024 * 1024 { 512 } else { 128 };
        let config = SearchConfig {
            max_states: model.graph_states as usize + 1,
            max_transitions: model.graph_states * 2,
            max_depth: model.graph_states as usize,
        };
        measure(
            "enumerate_linear",
            &model,
            model.graph_states as usize,
            || {
                let report = enumerate(&model, config.clone()).unwrap();
                assert_eq!(report.states, model.graph_states as usize);
                black_box(report)
            },
        );
        let fuzzed = measure("fuzz_128", &model, 128, || {
            fuzz(
                &model,
                FuzzConfig {
                    seed: 7,
                    cases: 1,
                    max_steps: 128,
                    max_transitions: 128,
                    mutation_percent: 0,
                },
            )
            .unwrap()
        });
        assert_eq!(fuzzed.transitions, 128);
        assert!(fuzzed.failure.is_none());
    }
    let mut broad = Synthetic::new(4096, 1, false);
    broad.fanout = 100_000;
    broad.lazy_inputs = false;
    let depth_zero = measure(
        "enumerate_depth_zero_eager_inputs",
        &broad,
        broad.fanout,
        || {
            enumerate(
                &broad,
                SearchConfig {
                    max_depth: 0,
                    ..SearchConfig::default()
                },
            )
            .unwrap()
        },
    );
    assert_eq!(depth_zero.states, 1);
    assert_eq!(depth_zero.transitions, 0);
    broad.lazy_inputs = true;
    let lazy = measure(
        "enumerate_depth_zero_lazy_inputs",
        &broad,
        broad.fanout,
        || {
            enumerate(
                &broad,
                SearchConfig {
                    max_depth: 0,
                    ..SearchConfig::default()
                },
            )
            .unwrap()
        },
    );
    assert_eq!(lazy.states, 1);
    assert_eq!(lazy.transitions, 0);

    for length in [64, 256] {
        let mut model = Synthetic::new(4096, 10, false);
        model.fail_after = Some(length);
        let failure = fuzz(
            &model,
            FuzzConfig {
                seed: 7,
                cases: 1,
                max_steps: length as usize,
                max_transitions: length,
                mutation_percent: 0,
            },
        )
        .unwrap()
        .failure
        .unwrap();
        let shrunk = measure("shrink_budget_64", &model, length as usize, || {
            shrink(&model, &failure, ShrinkConfig { max_attempts: 64 }).unwrap()
        });
        assert_eq!(shrunk.attempts, 64);
        assert_eq!(shrunk.minimized.inputs, failure.inputs);
        assert_eq!(shrunk.minimized.violations, failure.violations);
    }
    let many = Synthetic::new(4096, 10_000, false);
    let capture = record(&many, [0], RunConfig::default(), 1);
    eprintln!(
        "default_capture state_bytes=4096 checks=10000 retained=1: {:?}",
        capture.as_ref().map(|trace| trace.steps.len())
    );
    assert!(capture.is_err());
    let large = Synthetic::new(1024 * 1024, 10, false);
    let recorded = measure("record_32", &large, 32, || {
        record(&large, [0; 32], RunConfig::default(), 32).unwrap()
    });
    assert_eq!(recorded.steps.len(), 32);
}

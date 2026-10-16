use super::*;
use crate::execution::{ReplayOptions, ReplayOutcome, record_ops, replay_ops};
use crate::explore::{
    CheckPhase, Failure, FuzzConfig, FuzzTermination, PropertyFailure, RunLimits, ShrinkConfig,
    ShrinkLimits, fuzz_ops, shrink_ops,
};
use crate::model::{Check, Disposition};
use crate::monitor::{Recorder, RecorderOptions};
use crate::trace::{ReadLimits, RunConfig, Termination};
use std::cell::Cell;
use std::sync::atomic::{AtomicBool, Ordering};

// No Eq, Hash, or Clone on state/output; input cloning owns an immutable value.
struct State(u8);
struct Output(u8);
#[derive(Clone, Debug)]
struct Input(u8);

#[derive(Clone, Copy, PartialEq, Eq)]
enum Fault {
    None,
    Metadata,
    EncodeState(u8),
    Decode,
    Generate,
    Enabled,
    Equal,
    SimplifyOpen,
    SimplifyNext,
}

struct Counter {
    fault: Cell<Fault>,
    steps: Cell<usize>,
    input_comparisons: Cell<usize>,
    live_cursors: Cell<usize>,
    changed: bool,
    canonical: bool,
    cancelled: AtomicBool,
    cancel_equal: bool,
}

impl Default for Counter {
    fn default() -> Self {
        Self {
            fault: Cell::new(Fault::None),
            steps: Cell::new(0),
            input_comparisons: Cell::new(0),
            live_cursors: Cell::new(0),
            changed: false,
            canonical: true,
            cancelled: AtomicBool::new(false),
            cancel_equal: false,
        }
    }
}

impl Counter {
    fn fail(&self, fault: Fault) -> Result<(), ModelError> {
        if self.fault.get() == fault {
            Err(ModelError::new("injected callback"))
        } else {
            Ok(())
        }
    }
    fn original() -> Failure<Input> {
        Failure {
            inputs: vec![Input(0), Input(3)],
            violations: vec![PropertyFailure {
                phase: CheckPhase::State,
                check: Check::failed("bound", "overflow"),
            }],
        }
    }
}

impl ModelOps for Counter {
    type State = State;
    type Input = Input;
    type Output = Output;

    fn initial_state(&self) -> Result<State, ModelError> {
        Ok(State(0))
    }
    fn step(&self, state: &State, input: &Input) -> Result<Transition<State, Output>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        let value = state
            .0
            .checked_add(input.0)
            .ok_or_else(|| ModelError::new("counter overflow"))?;
        Ok(Transition::accepted(
            State(value),
            vec![Output(value + u8::from(self.changed))],
        ))
    }
    fn check_state_into(&self, state: &State, out: &mut CheckSink<'_>) -> Result<(), ModelError> {
        out.push(if state.0 < 3 {
            Check::passed("bound")
        } else {
            Check::failed("bound", "overflow")
        });
        Ok(())
    }
}

impl CodecOps for Counter {
    fn metadata(&self) -> Result<ModelMetadata, ModelError> {
        self.fail(Fault::Metadata)?;
        Ok(ModelMetadata {
            name: "ops-counter".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "test".into(),
        })
    }
    fn encode_state_into(
        &self,
        value: &State,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        self.fail(Fault::EncodeState(value.0))?;
        out.extend_from_slice(&[value.0])
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<State, ModelError> {
        self.fail(Fault::Decode)?;
        match bytes {
            [value] => Ok(State(*value + u8::from(!self.canonical))),
            _ => Err(ModelError::new("invalid state")),
        }
    }
    fn encode_input_into(
        &self,
        value: &Input,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_slice(&[value.0])
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Input, ModelError> {
        match bytes {
            [value] => Ok(Input(*value)),
            _ => Err(ModelError::new("invalid input")),
        }
    }
    fn encode_output_into(
        &self,
        value: &Output,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_slice(&[value.0])
    }
}

impl InputIdentityOps for Counter {
    fn input_equal(&self, left: &Input, right: &Input) -> Result<bool, ModelError> {
        self.input_comparisons.set(self.input_comparisons.get() + 1);
        if self.cancel_equal {
            self.cancelled.store(true, Ordering::Relaxed);
        }
        self.fail(Fault::Equal)?;
        Ok(left.0 == right.0)
    }
}

struct Simplifications<'a> {
    model: &'a Counter,
    position: usize,
    input: u8,
}
impl Drop for Simplifications<'_> {
    fn drop(&mut self) {
        self.model
            .live_cursors
            .set(self.model.live_cursors.get() - 1);
    }
}
impl InputCursor for Simplifications<'_> {
    type Input = Input;
    fn next(&mut self) -> Result<Option<Input>, ModelError> {
        self.model.fail(Fault::SimplifyNext)?;
        let value = match self.position {
            0 => Some(Input(self.input)),
            1 => Some(Input(self.input.saturating_sub(1))),
            _ => None,
        };
        self.position += 1;
        Ok(value)
    }
}
impl GenerateOps for Counter {
    type Simplifications<'a> = Simplifications<'a>;
    fn generate(&self, _state: &State, rng: &mut Rng) -> Result<Option<Input>, ModelError> {
        let mut candidate = rng.clone();
        let _draw = candidate.next_u64();
        self.fail(Fault::Generate)?;
        *rng = candidate;
        Ok(Some(Input(1)))
    }
    fn is_enabled(&self, _state: &State, _input: &Input) -> Result<bool, ModelError> {
        self.fail(Fault::Enabled)?;
        Ok(true)
    }
    fn simpler_inputs<'a>(
        &'a self,
        input: &Input,
    ) -> Result<Self::Simplifications<'a>, ModelError> {
        self.fail(Fault::SimplifyOpen)?;
        self.live_cursors.set(self.live_cursors.get() + 1);
        Ok(Simplifications {
            model: self,
            position: 0,
            input: input.0,
        })
    }
}

#[test]
fn binary_recording_and_replay_need_no_native_value_equality() {
    let model = Counter::default();
    let trace = record_ops(
        &model,
        (0..4).map(|_| Ok(Input(1))),
        RunConfig::default(),
        10,
        &ReadLimits::default(),
    )
    .unwrap();
    assert_eq!(trace.steps.len(), 3);
    assert_eq!(trace.termination, Termination::PropertyFailed);
    let replay = replay_ops(
        &model,
        &trace,
        ReplayOptions::default(),
        &ReadLimits::default(),
    )
    .unwrap();
    assert_eq!(replay.outcome, ReplayOutcome::Exact);
    assert!(replay.failure_reproduced);
    assert_eq!(replay.steps_verified, 3);
}

#[test]
fn codec_failure_and_input_error_preserve_only_coherent_prefixes() {
    let model = Counter::default();
    model.fault.set(Fault::EncodeState(2));
    let trace = record_ops(
        &model,
        vec![Ok(Input(1)), Ok(Input(1))],
        RunConfig::default(),
        10,
        &ReadLimits::default(),
    )
    .unwrap();
    assert_eq!(trace.steps.len(), 1);
    assert!(matches!(trace.termination, Termination::ModelError(_)));
    model.fault.set(Fault::None);
    let trace = record_ops(
        &model,
        vec![Ok(Input(1)), Err(ModelError::new("stream failed"))],
        RunConfig::default(),
        10,
        &ReadLimits::default(),
    )
    .unwrap();
    assert_eq!(trace.steps.len(), 1);
    assert!(matches!(trace.termination, Termination::ModelError(_)));
    assert_eq!(
        replay_ops(
            &model,
            &trace,
            ReplayOptions::default(),
            &ReadLimits::default()
        )
        .unwrap()
        .steps_verified,
        1
    );
}

#[test]
fn replay_classifies_identity_divergence_and_codec_errors_separately() {
    let model = Counter::default();
    let mut trace = record_ops(
        &model,
        vec![Ok(Input(1))],
        RunConfig::default(),
        10,
        &ReadLimits::default(),
    )
    .unwrap();
    let changed = Counter {
        changed: true,
        ..Counter::default()
    };
    let report = replay_ops(
        &changed,
        &trace,
        ReplayOptions::default(),
        &ReadLimits::default(),
    )
    .unwrap();
    assert_eq!(
        report.outcome,
        ReplayOutcome::Diverged {
            step: Some(1),
            field: "outputs"
        }
    );
    let noncanonical = Counter {
        canonical: false,
        ..Counter::default()
    };
    assert!(
        replay_ops(
            &noncanonical,
            &trace,
            ReplayOptions::default(),
            &ReadLimits::default()
        )
        .is_err()
    );
    for fault in [Fault::Metadata, Fault::Decode, Fault::EncodeState(0)] {
        model.fault.set(fault);
        assert!(
            replay_ops(
                &model,
                &trace,
                ReplayOptions::default(),
                &ReadLimits::default()
            )
            .is_err()
        );
    }
    model.fault.set(Fault::None);
    trace.metadata.codec_version += 1;
    assert!(matches!(
        replay_ops(
            &model,
            &trace,
            ReplayOptions::default(),
            &ReadLimits::default()
        )
        .unwrap()
        .outcome,
        ReplayOutcome::Incompatible { .. }
    ));
}

#[test]
fn fuzz_and_shrink_use_the_same_foreign_callbacks_without_input_eq() {
    let model = Counter::default();
    let report = fuzz_ops(
        &model,
        FuzzConfig {
            cases: 1,
            max_steps: 4,
            ..FuzzConfig::default()
        },
        RunLimits::default(),
    )
    .unwrap();
    assert_eq!(report.termination, FuzzTermination::FailureFound);
    assert_eq!(report.transitions, 3);
    let shrunk = shrink_ops(
        &model,
        &Counter::original(),
        ShrinkConfig::default(),
        ShrinkLimits::default(),
    )
    .unwrap();
    assert!(shrunk.validated_original);
    assert_eq!(shrunk.minimized.inputs.len(), 1);
    assert_eq!(shrunk.minimized.inputs[0].0, 3);
    assert!(model.input_comparisons.get() > 0);
    assert_eq!(model.live_cursors.get(), 0);
}

#[test]
fn generation_and_shrink_callback_failures_are_errors_and_close_cursors() {
    for fault in [Fault::Generate, Fault::Enabled] {
        let model = Counter::default();
        model.fault.set(fault);
        assert!(fuzz_ops(&model, FuzzConfig::default(), RunLimits::default()).is_err());
    }
    for fault in [
        Fault::Enabled,
        Fault::Equal,
        Fault::SimplifyOpen,
        Fault::SimplifyNext,
    ] {
        let model = Counter::default();
        model.fault.set(fault);
        assert!(
            shrink_ops(
                &model,
                &Counter::original(),
                ShrinkConfig::default(),
                ShrinkLimits::default()
            )
            .is_err()
        );
        assert_eq!(model.live_cursors.get(), 0);
    }
}

#[test]
fn equality_error_wins_over_simultaneous_shrink_cancellation() {
    let model = Counter {
        cancel_equal: true,
        ..Counter::default()
    };
    model.fault.set(Fault::Equal);
    let limits = ShrinkLimits {
        control: RunLimits {
            cancellation: Some(&model.cancelled),
            ..RunLimits::default()
        },
        ..ShrinkLimits::default()
    };
    let error = shrink_ops(
        &model,
        &Counter::original(),
        ShrinkConfig::default(),
        limits,
    )
    .unwrap_err();
    assert!(error.0.contains("input_equal"));
    assert_eq!(model.live_cursors.get(), 0);
}

#[test]
fn ring_observation_does_not_execute_the_reducer_and_replays_its_window() {
    let model = Counter::default();
    let mut recorder = Recorder::with_ops_options(
        &model,
        &State(0),
        RunConfig::default(),
        RecorderOptions {
            max_steps: 2,
            ..RecorderOptions::default()
        },
    )
    .unwrap();
    for before in 0..3 {
        let actual = Transition {
            state: State(before + 1),
            outputs: vec![Output(before + 1)],
            disposition: Disposition::Accepted,
        };
        recorder
            .observe_ops(&model, &State(before), &Input(1), &actual)
            .unwrap();
    }
    assert_eq!(model.steps.get(), 0);
    assert!(recorder.is_frozen());
    let trace = recorder.snapshot();
    assert_eq!(trace.initial_state, vec![1]);
    assert_eq!(trace.steps.len(), 2);
    let report = replay_ops(
        &model,
        &trace,
        ReplayOptions::default(),
        &ReadLimits::default(),
    )
    .unwrap();
    assert_eq!(report.outcome, ReplayOutcome::Exact);
    assert!(report.failure_reproduced);
}

#[test]
fn ring_codec_failure_freezes_the_previous_checkpoint() {
    let model = Counter::default();
    let mut recorder = Recorder::with_ops_options(
        &model,
        &State(0),
        RunConfig::default(),
        RecorderOptions::default(),
    )
    .unwrap();
    model.fault.set(Fault::EncodeState(1));
    let actual = Transition::accepted(State(1), vec![Output(1)]);
    assert!(
        recorder
            .observe_ops(&model, &State(0), &Input(1), &actual)
            .is_err()
    );
    assert_eq!(model.steps.get(), 0);
    assert!(recorder.is_frozen());
    assert!(recorder.snapshot().steps.is_empty());
}

#[test]
fn full_width_rng_bound_and_empty_domain_preserve_algorithm_and_state() {
    let mut rng = Rng::new(0);
    assert_eq!(rng.index_u64(0), None);
    assert_eq!(rng.state(), 0);
    assert_eq!(rng.index_u64(u64::MAX), Some(0xe220_a839_7b1d_cdaf));
    assert_eq!(rng.state(), 0x9e37_79b9_7f4a_7c15);
    let mut bounded = Rng::new(0);
    assert_eq!(bounded.index_u64(1 << 63), Some(0x6220_a839_7b1d_cdaf));
}

use stateless::TransitionRef;
use stateless::execution::{ReplayOptions, ReplayOutcome, replay};
use stateless::monitor::{Recorder, RecorderOptions};
use stateless::trace::{ReadLimits, RunConfig, Termination, Trace};
use stateless::{Check, Disposition, Model, ModelCodec, ModelError, ModelMetadata, Transition};
use std::cell::Cell;

#[derive(Default)]
struct Counter {
    initial_calls: Cell<usize>,
    step_calls: Cell<usize>,
    state_checks: Cell<usize>,
    transition_checks: Cell<usize>,
    fail_at: Option<u64>,
    check_error_at: Option<u64>,
    encode_error_at: Option<u64>,
}

impl Model for Counter {
    type State = u64;
    type Input = u64;
    type Output = u64;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "monitor-counter".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "counter-v1".into(),
        }
    }
    fn initial_state(&self) -> Result<u64, ModelError> {
        self.initial_calls.set(self.initial_calls.get() + 1);
        Ok(0)
    }
    fn step(&self, before: &u64, input: &u64) -> Result<Transition<u64, u64>, ModelError> {
        self.step_calls.set(self.step_calls.get() + 1);
        let next = before + input;
        Ok(Transition::accepted(next, vec![next]))
    }
    fn check_state(&self, state: &u64) -> Result<Vec<Check>, ModelError> {
        self.state_checks.set(self.state_checks.get() + 1);
        if self.check_error_at == Some(*state) {
            return Err(ModelError::new("checker unavailable"));
        }
        let mut checks = vec![if self.fail_at.is_some_and(|limit| *state >= limit) {
            Check::failed("state_limit", "counter exceeded bound")
        } else {
            Check::passed("state_limit")
        }];
        // Variable check counts ensure eviction saves each observation's boundary.
        if state.is_multiple_of(2) {
            checks.push(Check::passed("even_state"));
        }
        Ok(checks)
    }
    fn check_transition(
        &self,
        before: &u64,
        input: &u64,
        after: &TransitionRef<'_, u64, u64>,
    ) -> Result<Vec<Check>, ModelError> {
        self.transition_checks.set(self.transition_checks.get() + 1);
        Ok(vec![
            if *after.state == before + input && after.outputs == [*after.state] {
                Check::passed("sum_output")
            } else {
                Check::failed("sum_output", "observed output disagrees")
            },
        ])
    }
}

fn decode_u64(bytes: &[u8]) -> Result<u64, ModelError> {
    Ok(u64::from_le_bytes(
        bytes
            .try_into()
            .map_err(|_| ModelError::new("invalid counter bytes"))?,
    ))
}
impl ModelCodec for Counter {
    fn encode_state(&self, state: &u64) -> Result<Vec<u8>, ModelError> {
        if self.encode_error_at == Some(*state) {
            return Err(ModelError::new("state codec unavailable"));
        }
        Ok(state.to_le_bytes().to_vec())
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<u64, ModelError> {
        decode_u64(bytes)
    }
    fn encode_input(&self, input: &u64) -> Result<Vec<u8>, ModelError> {
        Ok(input.to_le_bytes().to_vec())
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<u64, ModelError> {
        decode_u64(bytes)
    }
    fn encode_output(&self, output: &u64) -> Result<Vec<u8>, ModelError> {
        Ok(output.to_le_bytes().to_vec())
    }
}

fn apply(model: &Counter, recorder: &mut Recorder, state: &mut u64, input: u64) {
    let transition = model.step(state, &input).unwrap();
    let step_calls = model.step_calls.get();
    recorder.observe(model, state, &input, &transition).unwrap();
    assert_eq!(
        model.step_calls.get(),
        step_calls,
        "recorder repeated application transition"
    );
    *state = transition.state;
}

fn parameter<'a>(trace: &'a Trace, key: &str) -> &'a str {
    &trace
        .config
        .parameters
        .iter()
        .find(|(k, _)| k == key)
        .unwrap()
        .1
}

#[test]
fn evicted_history_keeps_an_exact_replayable_checkpoint_without_rechecking() {
    let model = Counter::default();
    let mut state = 0;
    let mut recorder = Recorder::new(&model, &state, RunConfig::default(), 2).unwrap();
    for _ in 0..5 {
        apply(&model, &mut recorder, &mut state, 1);
    }
    assert_eq!(model.initial_calls.get(), 0);
    assert_eq!(model.step_calls.get(), 5);
    assert_eq!(model.state_checks.get(), 6);
    assert_eq!(model.transition_checks.get(), 5);
    assert_eq!(recorder.retained_steps(), 2);
    assert_eq!(recorder.evicted_steps(), 3);
    assert_eq!(recorder.observed_steps(), 5);
    assert!(!recorder.is_frozen());

    let trace = recorder.snapshot();
    assert_eq!(trace.termination, Termination::Interrupted);
    assert_eq!(model.decode_state(&trace.initial_state).unwrap(), 3);
    assert_eq!(trace.initial_checks, [Check::passed("state_limit")]);
    assert_eq!(parameter(&trace, "stateless.monitor.evicted_steps"), "3");
    assert_eq!(parameter(&trace, "stateless.monitor.observed_steps"), "5");
    assert_eq!(
        parameter(&trace, "stateless.monitor.checkpoint_after_sequence"),
        "3"
    );
    assert_eq!(parameter(&trace, "stateless.monitor.checking"), "full");

    let mut bytes = Vec::new();
    trace.write_to(&mut bytes).unwrap();
    let decoded = Trace::read_from(bytes.as_slice(), &ReadLimits::default()).unwrap();
    let report = replay(&model, &decoded, ReplayOptions::default()).unwrap();
    assert_eq!(report.outcome, ReplayOutcome::Exact);
    assert_eq!(report.steps_verified, 2);
    assert!(!report.failure_reproduced);
}

#[test]
fn freeze_on_failure_preserves_last_window_and_reproduces_failure() {
    let model = Counter {
        fail_at: Some(4),
        ..Counter::default()
    };
    let mut state = 0;
    let mut recorder = Recorder::new(&model, &state, RunConfig::default(), 2).unwrap();
    for _ in 0..4 {
        apply(&model, &mut recorder, &mut state, 1);
    }
    assert!(recorder.is_frozen());
    assert_eq!(recorder.evicted_steps(), 2);
    let frozen = recorder.snapshot();
    assert_eq!(frozen.termination, Termination::PropertyFailed);
    let checks_before = model.state_checks.get();
    let transition = model.step(&state, &1).unwrap();
    assert!(recorder.observe(&model, &state, &1, &transition).is_err());
    assert_eq!(recorder.snapshot(), frozen);
    assert_eq!(model.state_checks.get(), checks_before);
    let report = replay(&model, &frozen, ReplayOptions::default()).unwrap();
    assert_eq!(report.outcome, ReplayOutcome::Exact);
    assert!(report.failure_reproduced);
}

#[test]
fn initial_failure_freezes_without_executing_a_transition() {
    let model = Counter {
        fail_at: Some(7),
        ..Counter::default()
    };
    let recorder = Recorder::new(&model, &7, RunConfig::default(), 1).unwrap();
    assert!(recorder.is_frozen());
    assert_eq!(recorder.observed_steps(), 0);
    let trace = recorder.snapshot();
    assert_eq!(trace.termination, Termination::PropertyFailed);
    assert!(trace.steps.is_empty());
    assert_eq!(model.initial_calls.get(), 0);
    assert_eq!(model.step_calls.get(), 0);
    assert!(
        replay(&model, &trace, ReplayOptions::default())
            .unwrap()
            .failure_reproduced
    );
}

#[test]
fn a_missing_observation_freezes_the_coherent_prefix_and_declares_the_gap() {
    let model = Counter::default();
    let mut recorder = Recorder::new(&model, &0, RunConfig::default(), 2).unwrap();
    let mut state = 0;
    apply(&model, &mut recorder, &mut state, 1);
    let before_gap = recorder.snapshot();
    let state_checks = model.state_checks.get();
    let unrecorded = model.step(&state, &1).unwrap();
    let later = model.step(&unrecorded.state, &1).unwrap();
    let error = recorder
        .observe(&model, &unrecorded.state, &1, &later)
        .unwrap_err();
    assert!(error.0.contains("discontinuous"));
    assert_eq!(model.state_checks.get(), state_checks);
    let frozen = recorder.snapshot();
    assert_eq!(frozen.initial_state, before_gap.initial_state);
    assert_eq!(frozen.steps, before_gap.steps);
    assert!(
        matches!(&frozen.termination, Termination::ModelError(reason) if reason.contains("discontinuous"))
    );
    assert!(recorder.observe(&model, &state, &1, &unrecorded).is_err());
    assert_eq!(recorder.snapshot(), frozen);
    assert_eq!(
        replay(&model, &frozen, ReplayOptions::default())
            .unwrap()
            .outcome,
        ReplayOutcome::Exact
    );
}

#[test]
fn checker_and_codec_errors_do_not_evict_the_last_coherent_window() {
    for codec_error in [false, true] {
        let model = Counter {
            check_error_at: (!codec_error).then_some(2),
            encode_error_at: codec_error.then_some(2),
            ..Counter::default()
        };
        let mut recorder = Recorder::new(&model, &0, RunConfig::default(), 1).unwrap();
        let mut state = 0;
        apply(&model, &mut recorder, &mut state, 1);
        let coherent = recorder.snapshot();
        let transition = model.step(&state, &1).unwrap();
        assert!(recorder.observe(&model, &state, &1, &transition).is_err());
        assert!(recorder.is_frozen());
        assert_eq!(recorder.evicted_steps(), 0);
        assert_eq!(recorder.observed_steps(), 1);
        let trace = recorder.snapshot();
        assert_eq!(trace.steps, coherent.steps);
        assert_eq!(trace.initial_state, coherent.initial_state);
        assert!(matches!(trace.termination, Termination::ModelError(_)));
        assert_eq!(
            replay(&model, &trace, ReplayOptions::default())
                .unwrap()
                .outcome,
            ReplayOutcome::Exact
        );
    }
}

#[test]
fn recorder_checks_actual_outputs_instead_of_recomputing_the_transition() {
    let model = Counter::default();
    let mut recorder = Recorder::new(&model, &0, RunConfig::default(), 1).unwrap();
    let transition = Transition::accepted(1, vec![999]);
    let checks = recorder.observe(&model, &0, &1, &transition).unwrap();
    assert!(
        checks
            .iter()
            .any(|check| check.id == "sum_output" && check.is_failure())
    );
    assert_eq!(model.step_calls.get(), 0);
    assert!(recorder.is_frozen());
    let trace = recorder.snapshot();
    assert_eq!(trace.steps[0].outputs, [999u64.to_le_bytes().to_vec()]);
    // A production output inconsistent with its model remains useful evidence;
    // replay truthfully reports that the pure reducer produced different output.
    let report = replay(&model, &trace, ReplayOptions::default()).unwrap();
    assert_eq!(
        report.outcome,
        ReplayOutcome::Diverged {
            step: Some(1),
            field: "outputs"
        }
    );
    assert!(!report.failure_reproduced);
}

#[test]
fn capacity_one_and_midrun_start_replay_after_repeated_eviction() {
    let model = Counter::default();
    let mut state = 50;
    let mut recorder = Recorder::new(&model, &state, RunConfig::default(), 1).unwrap();
    for _ in 0..4 {
        apply(&model, &mut recorder, &mut state, 1);
        let trace = recorder.snapshot();
        assert_eq!(trace.steps.len(), 1);
        assert_eq!(model.decode_state(&trace.initial_state).unwrap(), state - 1);
        assert_eq!(
            replay(&model, &trace, ReplayOptions::default())
                .unwrap()
                .outcome,
            ReplayOutcome::Exact
        );
    }
    assert_eq!(recorder.evicted_steps(), 3);
}

#[test]
fn ignored_inputs_are_still_recorded_and_snapshots_do_not_stop_recording() {
    let model = Counter::default();
    let mut recorder = Recorder::new(&model, &0, RunConfig::default(), 2).unwrap();
    let first = recorder.snapshot();
    assert_eq!(first.termination, Termination::Interrupted);
    let ignored = Transition {
        state: 0,
        outputs: vec![0],
        disposition: Disposition::Ignored("duplicate".into()),
    };
    recorder.observe(&model, &0, &0, &ignored).unwrap();
    assert_eq!(
        recorder.snapshot().steps[0].disposition,
        ignored.disposition
    );
    assert!(!recorder.is_frozen());
    let mut state = 0;
    apply(&model, &mut recorder, &mut state, 1);
    assert_eq!(recorder.retained_steps(), 2);
}

#[test]
fn invalid_limits_and_reserved_audit_keys_are_rejected() {
    let model = Counter::default();
    assert!(Recorder::new(&model, &0, RunConfig::default(), 0).is_err());
    let config = RunConfig {
        parameters: vec![("stateless.monitor.evicted_steps".into(), "false".into())],
        ..RunConfig::default()
    };
    assert!(Recorder::new(&model, &0, config, 2).is_err());
    assert_eq!(model.state_checks.get(), 0);
}

#[test]
fn too_many_checks_freezes_before_evicting_the_previous_observation() {
    let model = Counter::default();
    let options = RecorderOptions {
        max_steps: 1,
        limits: ReadLimits {
            max_checks_per_step: 2,
            ..ReadLimits::default()
        },
        ..RecorderOptions::default()
    };
    let mut recorder = Recorder::with_options(&model, &0, RunConfig::default(), options).unwrap();
    let mut state = 0;
    apply(&model, &mut recorder, &mut state, 1);
    let prefix = recorder.snapshot();
    // State two adds an extra state check: two state checks plus the edge check
    // exceed the format limit even though each callback alone fits.
    let next = model.step(&state, &1).unwrap();
    let error = recorder.observe(&model, &state, &1, &next).unwrap_err();
    assert!(error.0.contains("checks per step"));
    let frozen = recorder.snapshot();
    assert_eq!(frozen.steps, prefix.steps);
    assert_eq!(frozen.initial_state, prefix.initial_state);
    assert_eq!(recorder.evicted_steps(), 0);
    assert_eq!(recorder.observed_steps(), 1);
    let mut bytes = Vec::new();
    recorder.write_to(&mut bytes).unwrap();
    assert_eq!(recorder.retained_bytes(), bytes.len() as u64);
    assert_eq!(
        replay(&model, &frozen, ReplayOptions::default())
            .unwrap()
            .outcome,
        ReplayOutcome::Exact
    );
}

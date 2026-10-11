//! Shared checked evidence and rich replay must not repeat application work.
use stateless::execution::*;
use stateless::monitor::{Recorder, RecorderOptions};
use stateless::trace::{RunConfig, Termination};
use stateless::*;
use std::cell::Cell;
use std::num::NonZeroU64;

#[derive(Default)]
struct Counter {
    steps: Cell<usize>,
    states: Cell<usize>,
    turns: Cell<usize>,
    fault: Cell<&'static str>,
}
impl Model for Counter {
    type State = u8;
    type Input = u8;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "checked-counter".into(),
            build: "fixture".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
        }
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u8, input: &u8) -> Result<Transition<u8, u8>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        if self.fault.get() == "step" {
            return Err(ModelError::new("step fault"));
        }
        Ok(Transition {
            state: state + input,
            outputs: vec![*input],
            disposition: match input {
                0 => Disposition::Ignored("zero".into()),
                2 => Disposition::Rejected("two".into()),
                _ => Disposition::Accepted,
            },
        })
    }
    fn check_state(&self, _: &u8) -> Result<Vec<Check>, ModelError> {
        panic!("append-style checks only")
    }
    fn check_state_into(&self, state: &u8, checks: &mut CheckSink<'_>) -> Result<(), ModelError> {
        self.states.set(self.states.get() + 1);
        checks.push(if self.fault.get() == "failure" {
            Check::failed("state", "injected")
        } else {
            Check::passed("state")
        });
        if self.fault.get() == "initial" || (self.fault.get() == "state" && *state > 0) {
            return Err(ModelError::new("state fault"));
        }
        Ok(())
    }
    fn check_transition_into(
        &self,
        _: &u8,
        _: &u8,
        _: &TransitionRef<'_, u8, u8>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.turns.set(self.turns.get() + 1);
        checks.push(Check::passed("transition"));
        if self.fault.get() == "checks" {
            return Err(ModelError::new("transition check fault"));
        }
        Ok(())
    }
}
impl ModelCodec for Counter {
    fn encode_state(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
        if self.fault.get() == "encode-state" && *state > 0 {
            return Err(ModelError::new("codec fault"));
        }
        Ok(vec![*state])
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        match bytes {
            [n] => Ok(*n),
            _ => Err(ModelError::new("invalid number")),
        }
    }
    fn encode_input(&self, input: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*input])
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        self.decode_state(bytes)
    }
    fn encode_output(&self, output: &u8) -> Result<Vec<u8>, ModelError> {
        if self.fault.get() == "encode-output" {
            return Err(ModelError::new("codec fault"));
        }
        Ok(vec![*output])
    }
}

#[derive(Default)]
struct CountingOracle {
    advances: Cell<usize>,
    checks: Cell<usize>,
}
impl Oracle<Counter> for CountingOracle {
    type State = u8;
    fn metadata(&self) -> ModelMetadata {
        let mut metadata = Counter::default().metadata();
        metadata.name = "oracle".into();
        metadata
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn advance(
        &self,
        before: &u8,
        input: &u8,
        _: &[u8],
        _: &Disposition,
    ) -> Result<u8, ModelError> {
        self.advances.set(self.advances.get() + 1);
        Ok(before + input)
    }
    fn check_state_into(
        &self,
        expected: &u8,
        actual: &u8,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.checks.set(self.checks.get() + 1);
        assert_eq!(actual, expected);
        checks.push(Check::passed("oracle"));
        Ok(())
    }
}
impl OracleCodec<Counter> for CountingOracle {
    fn encode_history(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*state])
    }
    fn decode_history(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        Counter::default().decode_state(bytes)
    }
}

#[test]
fn shared_tokens_record_and_observe_one_reducer_check_and_oracle_advance() {
    let model = WithOracle::new(Counter::default(), CountingOracle::default());
    let mut state = model.initial_state().unwrap();
    let initial = check_initial(&model, &state).unwrap();
    assert_eq!(initial.state_check_count(), 2);
    let mut recorder = Recorder::with_checked_initial(
        &initial,
        RunConfig::default(),
        RecorderOptions {
            max_steps: 1,
            ..RecorderOptions::default()
        },
    )
    .unwrap();
    drop(initial);
    for (index, input) in [1, 0, 2].into_iter().enumerate() {
        let turn = model.step(&state, &input).unwrap();
        let checked = check_turn(
            &model,
            &state,
            &input,
            &turn,
            index as u64 + 1,
            CheckPolicy::default(),
        )
        .unwrap();
        assert!(std::ptr::eq(checked.model(), &model));
        assert!(std::ptr::eq(checked.before(), &state));
        assert!(std::ptr::eq(checked.input(), &input));
        assert!(std::ptr::eq(checked.transition(), &turn));
        assert_eq!(checked.state_check_count(), 2);
        assert_eq!(checked.observation().checks.len(), 3);
        assert_eq!(
            recorder.observe_checked(&checked).unwrap(),
            checked.checks()
        );
        let checks = checked.into_checks();
        assert_eq!(checks.len(), 3);
        state = turn.state;
    }
    assert_eq!(model.model().steps.get(), 3);
    assert_eq!(model.model().states.get(), 4);
    assert_eq!(model.model().turns.get(), 3);
    assert_eq!(model.oracle().advances.get(), 3);
    assert_eq!(model.oracle().checks.get(), 4);
    let trace = recorder.snapshot();
    assert_eq!(trace.initial_checks.len(), 2);
    assert_eq!(trace.steps.len(), 1);
    assert!(matches!(
        trace.steps[0].disposition,
        Disposition::Rejected(_)
    ));
    assert_eq!(
        replay(&model, &trace, ReplayOptions::default())
            .unwrap()
            .outcome,
        ReplayOutcome::Exact
    );
}

#[test]
fn checked_recorder_bytes_match_legacy_including_eviction_and_freeze() {
    let model = Counter::default();
    let mut state = 0;
    let options = RecorderOptions {
        max_steps: 2,
        ..RecorderOptions::default()
    };
    let initial = check_initial(&model, &state).unwrap();
    let mut checked_recorder =
        Recorder::with_checked_initial(&initial, RunConfig::default(), options.clone()).unwrap();
    let mut legacy = Recorder::with_options(&model, &state, RunConfig::default(), options).unwrap();
    drop(initial);
    for (index, input) in [0, 1, 2, 1].into_iter().enumerate() {
        if index == 3 {
            model.fault.set("failure");
        }
        let turn = model.step(&state, &input).unwrap();
        let checked = check_turn(
            &model,
            &state,
            &input,
            &turn,
            index as u64 + 1,
            CheckPolicy::default(),
        )
        .unwrap();
        checked_recorder.observe_checked(&checked).unwrap();
        legacy.observe(&model, &state, &input, &turn).unwrap();
        assert_eq!(checked_recorder.snapshot(), legacy.snapshot());
        let mut a = Vec::new();
        let mut b = Vec::new();
        checked_recorder.write_to(&mut a).unwrap();
        legacy.write_to(&mut b).unwrap();
        assert_eq!(a, b);
        drop(checked);
        state = turn.state;
    }
    assert!(checked_recorder.is_frozen());
    assert_eq!(
        checked_recorder.snapshot().termination,
        Termination::PropertyFailed
    );
}

#[test]
fn non_full_and_out_of_order_tokens_cannot_bypass_recording_checks() {
    for policy in [
        CheckPolicy {
            state_every: NonZeroU64::new(2).unwrap(),
            transition_checks: true,
        },
        CheckPolicy {
            state_every: NonZeroU64::new(1).unwrap(),
            transition_checks: false,
        },
    ] {
        let model = Counter::default();
        let state = 0;
        let input = 1;
        let initial = check_initial(&model, &state).unwrap();
        let mut recorder = Recorder::with_checked_initial(
            &initial,
            RunConfig::default(),
            RecorderOptions::default(),
        )
        .unwrap();
        let turn = model.step(&state, &input).unwrap();
        let checked = check_turn(&model, &state, &input, &turn, 1, policy).unwrap();
        assert!(!checked.full_checking());
        assert!(
            recorder
                .observe_checked(&checked)
                .unwrap_err()
                .0
                .contains("full checking")
        );
        assert!(recorder.snapshot().steps.is_empty());
    }
    let model = Counter::default();
    let state = 0;
    let input = 1;
    let initial = check_initial(&model, &state).unwrap();
    let mut recorder =
        Recorder::with_checked_initial(&initial, RunConfig::default(), RecorderOptions::default())
            .unwrap();
    let turn = model.step(&state, &input).unwrap();
    assert!(check_turn(&model, &state, &input, &turn, 0, CheckPolicy::default()).is_err());
    let checked = check_turn(&model, &state, &input, &turn, 2, CheckPolicy::default()).unwrap();
    assert!(
        recorder
            .observe_checked(&checked)
            .unwrap_err()
            .0
            .contains("sequence")
    );
}

#[test]
fn initial_failure_freezes_recorder_without_rechecking() {
    let model = Counter::default();
    model.fault.set("failure");
    let checked = check_initial(&model, &0).unwrap();
    let recorder =
        Recorder::with_checked_initial(&checked, RunConfig::default(), RecorderOptions::default())
            .unwrap();
    assert_eq!(model.states.get(), 1);
    assert!(recorder.is_frozen());
    assert_eq!(recorder.snapshot().termination, Termination::PropertyFailed);
}

#[test]
fn rich_replay_exposes_every_difference_but_preserves_primary_and_legacy_timing() {
    let model = Counter::default();
    let mut trace = record(&model, [1, 1], RunConfig::default(), 2).unwrap();
    trace.steps[0].disposition = Disposition::Rejected("changed".into());
    trace.steps[0].outputs = vec![vec![99]];
    trace.steps[0].post_state = vec![99];
    trace.steps[0].checks.push(Check::passed("extra"));
    model.steps.set(0);
    model.states.set(0);
    model.turns.set(0);
    let mut seen = Vec::new();
    let rich =
        replay_with_observations(&model, &trace, ReplayOptions::default(), |event, report| {
            match event {
                ReplayObservation::Initial(initial) => {
                    seen.push(0);
                    assert_eq!(*initial.state, 0);
                    assert_eq!(initial.expected_state, initial.actual_state);
                    assert!(initial.differences.is_empty());
                }
                ReplayObservation::Turn(turn) => {
                    seen.push(turn.actual.sequence);
                    assert_eq!(
                        turn.differences,
                        ReplayDifferences {
                            disposition: true,
                            outputs: true,
                            state: true,
                            checks: true
                        }
                    );
                    assert_eq!(turn.actual_input, [1]);
                    assert_eq!(turn.actual_outputs, [vec![1]]);
                    assert_eq!(turn.actual_state, [1]);
                    assert_eq!(turn.expected.post_state, [99]);
                    assert_eq!(turn.actual.transition.outputs, [1]);
                    assert_eq!(report.steps_verified, 0);
                }
                ReplayObservation::Error { .. } => panic!("comparison is not a callback error"),
            }
            Ok(())
        })
        .unwrap();
    assert_eq!(seen, [0, 1]);
    assert_eq!(model.steps.get(), 1);
    assert_eq!(model.states.get(), 2);
    assert_eq!(model.turns.get(), 1);
    let mut legacy_seen = Vec::new();
    let legacy = replay_with_observer(
        &model,
        &trace,
        ReplayOptions::default(),
        |sequence, report| legacy_seen.push((sequence, report.clone())),
    )
    .unwrap();
    assert_eq!(legacy, rich);
    assert_eq!(legacy_seen, [(1, rich.clone())]);
    assert_eq!(
        rich.outcome,
        ReplayOutcome::Diverged {
            step: Some(1),
            field: "disposition"
        }
    );
}

#[test]
fn initial_divergence_is_visible_only_to_rich_observer() {
    let model = Counter::default();
    let mut trace = record(&model, [1], RunConfig::default(), 1).unwrap();
    trace.initial_checks.push(Check::passed("extra"));
    model.steps.set(0);
    let mut count = 0;
    let rich =
        replay_with_observations(&model, &trace, ReplayOptions::default(), |event, report| {
            let ReplayObservation::Initial(initial) = event else {
                panic!("initial only")
            };
            assert!(initial.differences.checks);
            assert_eq!(report.steps_verified, 0);
            count += 1;
            Ok(())
        })
        .unwrap();
    let legacy = replay_with_observer(&model, &trace, ReplayOptions::default(), |_, _| {
        panic!("no legacy sequence zero")
    })
    .unwrap();
    assert_eq!(rich, legacy);
    assert_eq!(count, 1);
    assert_eq!(model.steps.get(), 0);
    assert_eq!(
        rich.outcome,
        ReplayOutcome::Diverged {
            step: None,
            field: "initial checks"
        }
    );
}

#[test]
fn replay_checker_and_codec_errors_retain_actual_and_do_not_repeat_delivery() {
    for fault in ["state", "checks", "encode-state", "encode-output"] {
        let model = Counter::default();
        let trace = record(&model, [1, 1], RunConfig::default(), 2).unwrap();
        model.steps.set(0);
        model.fault.set(fault);
        let mut events = Vec::new();
        let error =
            replay_with_observations(&model, &trace, ReplayOptions::default(), |event, _| {
                match event {
                    ReplayObservation::Initial(_) => events.push(0),
                    ReplayObservation::Error {
                        sequence, actual, ..
                    } => {
                        events.push(sequence);
                        assert!(actual.is_some());
                    }
                    ReplayObservation::Turn(_) => panic!("incomplete comparison"),
                }
                Ok(())
            })
            .unwrap_err();
        assert_eq!(events, [0, 1]);
        assert_eq!(model.steps.get(), 1);
        let ReplayActual::Turn {
            sequence,
            before,
            input,
            transition,
            checks,
            state_check_count,
        } = *error.actual.unwrap()
        else {
            panic!("turn")
        };
        assert_eq!(sequence, 1);
        assert_eq!(before, 0);
        assert_eq!(input, 1);
        assert_eq!(transition.state, 1);
        assert_eq!(transition.outputs, [1]);
        assert_eq!(checks.is_some(), fault.starts_with("encode"));
        assert_eq!(state_check_count, fault.starts_with("encode").then_some(1));
        assert_eq!(error.report.steps_verified, 0);
        replay_with_observer(&model, &trace, ReplayOptions::default(), |_, _| {
            panic!("legacy skips failed comparisons")
        })
        .unwrap_err();
    }
}

#[test]
fn observer_errors_keep_the_complete_result_and_verified_prefix() {
    let model = Counter::default();
    let trace = record(&model, [1, 1], RunConfig::default(), 2).unwrap();
    model.steps.set(0);
    let error = replay_with_observations(&model, &trace, ReplayOptions::default(), |event, _| {
        if matches!(event, ReplayObservation::Turn(_)) {
            Err(ModelError::new("display failed"))
        } else {
            Ok(())
        }
    })
    .unwrap_err();
    assert_eq!(model.steps.get(), 1);
    assert_eq!(error.report.steps_verified, 1);
    assert_eq!(error.error.0, "replay observer: display failed");
    assert!(error.observer_error.is_none());
    let ReplayActual::Turn {
        transition, checks, ..
    } = *error.actual.unwrap()
    else {
        panic!("turn")
    };
    assert_eq!(transition.state, 1);
    assert_eq!(checks.unwrap().len(), 2);
}

#[test]
fn initial_checker_error_retains_state_and_error_observer_cannot_mask_it() {
    let model = Counter::default();
    let trace = record(&model, [1], RunConfig::default(), 1).unwrap();
    model.fault.set("initial");
    model.steps.set(0);
    let error = replay_with_observations(&model, &trace, ReplayOptions::default(), |event, _| {
        let ReplayObservation::Error {
            sequence: 0,
            actual: Some(ReplayActual::Initial { state, checks }),
            ..
        } = event
        else {
            panic!("initial error")
        };
        assert_eq!(*state, 0);
        assert!(checks.is_none());
        Err(ModelError::new("error sink failed"))
    })
    .unwrap_err();
    assert_eq!(error.error.0, "initial check: state fault");
    assert_eq!(error.observer_error.unwrap().0, "error sink failed");
    assert!(matches!(
        error.actual.as_deref(),
        Some(ReplayActual::Initial {
            state: 0,
            checks: None
        })
    ));
    assert_eq!(model.steps.get(), 0);
}

#[test]
fn reducer_error_has_no_invented_transition_and_identity_rejection_has_no_events() {
    let model = Counter::default();
    let mut trace = record(&model, [1], RunConfig::default(), 1).unwrap();
    model.fault.set("step");
    model.steps.set(0);
    let error = replay_with_observations(&model, &trace, ReplayOptions::default(), |event, _| {
        if let ReplayObservation::Error { actual, .. } = event {
            assert!(actual.is_none());
        }
        Ok(())
    })
    .unwrap_err();
    assert!(error.actual.is_none());
    assert_eq!(model.steps.get(), 1);
    trace.metadata.name = "different".into();
    trace.initial_state.clear();
    let report = replay_with_observations(&model, &trace, ReplayOptions::default(), |_, _| {
        panic!("identity first")
    })
    .unwrap();
    assert!(matches!(report.outcome, ReplayOutcome::Incompatible { .. }));
}

#[test]
fn checked_attachment_accepts_global_host_sequence_and_rejects_overflow_or_gaps() {
    let model = Counter::default();
    let state = 5;
    let initial = check_initial(&model, &state).unwrap();
    let mut recorder = Recorder::with_checked_initial_at(
        &initial,
        50,
        RunConfig::default(),
        RecorderOptions::default(),
    )
    .unwrap();
    let turn = model.step(&state, &0).unwrap();
    let checked = check_turn(&model, &state, &0, &turn, 51, CheckPolicy::default()).unwrap();
    recorder.observe_checked(&checked).unwrap();
    assert!(
        recorder
            .snapshot()
            .config
            .parameters
            .contains(&("stateless.monitor.sequence_origin".into(), "50".into(),))
    );
    assert_eq!(recorder.snapshot().steps.len(), 1);
    let gap = check_turn(&model, &state, &0, &turn, 53, CheckPolicy::default()).unwrap();
    assert!(
        recorder
            .observe_checked(&gap)
            .unwrap_err()
            .0
            .contains("sequence")
    );
    assert_eq!(recorder.snapshot().steps.len(), 1);
    let mut overflow = Recorder::with_checked_initial_at(
        &initial,
        u64::MAX,
        RunConfig::default(),
        RecorderOptions::default(),
    )
    .unwrap();
    assert!(overflow.observe_checked(&checked).is_err());
}

#[test]
fn replay_initial_observer_failure_keeps_checked_state_without_delivery() {
    let model = Counter::default();
    let trace = record(&model, [1], RunConfig::default(), 1).unwrap();
    model.steps.set(0);
    let error = replay_with_observations(&model, &trace, ReplayOptions::default(), |event, _| {
        assert!(matches!(event, ReplayObservation::Initial(_)));
        Err(ModelError::new("initial observer fault"))
    })
    .unwrap_err();
    assert!(matches!(
        error.actual.as_deref(),
        Some(ReplayActual::Initial {
            state: 0,
            checks: Some(_)
        })
    ));
    assert_eq!(model.steps.get(), 0);
}

#[test]
fn plain_model_check_tokens_require_no_codec_debug_send_or_sync() {
    use std::rc::Rc;
    #[derive(Clone, PartialEq, Eq)]
    struct Value(Rc<u8>);
    struct Plain;
    impl Model for Plain {
        type State = Value;
        type Input = Value;
        type Output = Value;
        fn metadata(&self) -> ModelMetadata {
            Counter::default().metadata()
        }
        fn initial_state(&self) -> Result<Value, ModelError> {
            Ok(Value(Rc::new(0)))
        }
        fn step(&self, _: &Value, input: &Value) -> Result<Transition<Value, Value>, ModelError> {
            Ok(Transition::accepted(input.clone(), vec![input.clone()]))
        }
        fn check_state(&self, _: &Value) -> Result<Vec<Check>, ModelError> {
            Ok(vec![Check::passed("state")])
        }
    }
    let model = Plain;
    let state = model.initial_state().unwrap();
    let initial = check_initial(&model, &state).unwrap();
    assert_eq!(initial.checks().len(), 1);
    let input = Value(Rc::new(1));
    let turn = model.step(&state, &input).unwrap();
    let checked = check_turn(&model, &state, &input, &turn, 1, CheckPolicy::default()).unwrap();
    assert!(checked.transition().state == input);
    assert_eq!(checked.checks().len(), 1);
}

#[test]
fn external_check_error_freezes_coherent_prefix_and_cannot_overwrite_failure() {
    let model = Counter::default();
    let initial = check_initial(&model, &0).unwrap();
    let mut recorder =
        Recorder::with_checked_initial(&initial, RunConfig::default(), RecorderOptions::default())
            .unwrap();
    let turn = model.step(&0, &1).unwrap();
    model.fault.set("checks");
    let error = match check_turn(&model, &0, &1, &turn, 1, CheckPolicy::default()) {
        Err(error) => error,
        Ok(_) => panic!("checker must fail"),
    };
    let original = error.clone();
    assert_eq!(recorder.stop_with_error(error), original);
    assert!(recorder.is_frozen());
    assert!(recorder.snapshot().steps.is_empty());
    assert!(matches!(
        recorder.snapshot().termination,
        Termination::ModelError(_)
    ));
    model.fault.set("");
    let checked = check_turn(&model, &0, &1, &turn, 1, CheckPolicy::default()).unwrap();
    assert!(recorder.observe_checked(&checked).is_err());
    let first = recorder.snapshot().termination;
    recorder.stop_with_error(ModelError::new("later error"));
    assert_eq!(recorder.snapshot().termination, first);
    model.fault.set("failure");
    let initial = check_initial(&model, &0).unwrap();
    let mut failed =
        Recorder::with_checked_initial(&initial, RunConfig::default(), RecorderOptions::default())
            .unwrap();
    failed.stop_with_error(ModelError::new("later display error"));
    assert_eq!(failed.snapshot().termination, Termination::PropertyFailed);
}

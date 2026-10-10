//! Independent pre-PR regression coverage, including adversarial failure paths.
use stateless::execution::{
    CheckPolicy, ReplayActual, ReplayObservation, ReplayOptions, ReplayOutcome, check_initial,
    check_turn, record, replay_with_observations, replay_with_observations_bounded,
    replay_with_observer,
};
use stateless::monitor::{Recorder, RecorderOptions};
use stateless::trace::{ReadLimits, RunConfig, Termination, Trace};
use stateless::{
    Check, CheckSink, Disposition, Model, ModelCodec, ModelError, ModelMetadata, Transition,
    TransitionRef,
};
use std::cell::Cell;
use std::num::NonZeroU64;

#[derive(Default)]
struct Fixture {
    steps: Cell<usize>,
    state_checks: Cell<usize>,
    turn_checks: Cell<usize>,
    decodes: Cell<usize>,
    output_encodes: Cell<usize>,
    checker_error: bool,
    extra_output: bool,
}
impl Model for Fixture {
    type State = u8;
    type Input = u8;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "independent-pr-fixture".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "review".into(),
        }
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u8, input: &u8) -> Result<Transition<u8, u8>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        let mut outputs = vec![*input, *state];
        if self.extra_output {
            outputs.push(99);
        }
        Ok(Transition::accepted(state + input, outputs))
    }
    fn check_state(&self, _: &u8) -> Result<Vec<Check>, ModelError> {
        panic!("append-style checks only")
    }
    fn check_state_into(&self, state: &u8, checks: &mut CheckSink<'_>) -> Result<(), ModelError> {
        self.state_checks.set(self.state_checks.get() + 1);
        checks.push(if *state >= 3 {
            Check::failed("limit", "reached three")
        } else {
            Check::passed("limit")
        });
        if self.checker_error && *state > 0 {
            return Err(ModelError::new("primary checker error"));
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
        self.turn_checks.set(self.turn_checks.get() + 1);
        checks.push(Check::passed("turn"));
        Ok(())
    }
}
impl ModelCodec for Fixture {
    fn encode_state(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*state])
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        self.decodes.set(self.decodes.get() + 1);
        match bytes {
            [state] => Ok(*state),
            _ => Err(ModelError::new("invalid state")),
        }
    }
    fn encode_input(&self, input: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*input])
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        self.decode_state(bytes)
    }
    fn encode_output(&self, output: &u8) -> Result<Vec<u8>, ModelError> {
        self.output_encodes.set(self.output_encodes.get() + 1);
        Ok(vec![*output])
    }
}
fn failure_trace() -> Trace {
    record(&Fixture::default(), [1, 1, 1], RunConfig::default(), 3).unwrap()
}
#[test]
fn legacy_and_rich_replay_agree_on_every_nonempty_difference_combination() {
    for mask in 1..16 {
        let mut trace = failure_trace();
        let last = &mut trace.steps[2];
        if mask & 1 != 0 {
            last.disposition = Disposition::Ignored("different".into());
        }
        if mask & 2 != 0 {
            last.outputs.reverse();
        }
        if mask & 4 != 0 {
            last.post_state = vec![9];
        }
        if mask & 8 != 0 {
            last.checks[0] = Check::failed("limit", "different explanation");
        }
        let legacy_model = Fixture::default();
        let mut legacy_boundaries = vec![];
        let legacy = replay_with_observer(
            &legacy_model,
            &trace,
            ReplayOptions::default(),
            |sequence, _| legacy_boundaries.push(sequence),
        )
        .unwrap();
        let rich_model = Fixture::default();
        let mut rich_boundaries = vec![];
        let rich = replay_with_observations(
            &rich_model,
            &trace,
            ReplayOptions::default(),
            |observation, report| {
                match observation {
                    ReplayObservation::Initial(initial) => {
                        assert!(initial.differences.is_empty());
                        rich_boundaries.push(0);
                    }
                    ReplayObservation::Turn(turn) => {
                        rich_boundaries.push(turn.actual.sequence as usize);
                        if turn.actual.sequence == 3 {
                            assert_eq!(turn.differences.disposition, mask & 1 != 0);
                            assert_eq!(turn.differences.outputs, mask & 2 != 0);
                            assert_eq!(turn.differences.state, mask & 4 != 0);
                            assert_eq!(turn.differences.checks, mask & 8 != 0);
                            assert_eq!(report.steps_verified, 2);
                        }
                    }
                    ReplayObservation::Error { .. } => panic!("unexpected replay error"),
                }
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(legacy, rich);
        assert_eq!(legacy_boundaries, [1, 2, 3]);
        assert_eq!(rich_boundaries, [0, 1, 2, 3]);
        assert_eq!(rich_model.steps.get(), 3);
        assert_eq!(rich_model.state_checks.get(), 4);
        assert_eq!(rich_model.turn_checks.get(), 3);
        let field = if mask & 1 != 0 {
            "disposition"
        } else if mask & 2 != 0 {
            "outputs"
        } else if mask & 4 != 0 {
            "state"
        } else {
            "checks"
        };
        assert_eq!(
            rich.outcome,
            ReplayOutcome::Diverged {
                step: Some(3),
                field
            }
        );
        assert!(rich.failure_reproduced);
    }
}
#[test]
fn checker_failure_retains_actual_once_and_secondary_observer_error() {
    let model = Fixture {
        checker_error: true,
        ..Default::default()
    };
    let mut error_events = 0;
    let error = replay_with_observations(
        &model,
        &failure_trace(),
        ReplayOptions::default(),
        |observation, _| {
            if let ReplayObservation::Error {
                sequence,
                actual,
                error,
            } = observation
            {
                assert_eq!(sequence, 1);
                assert!(error.to_string().contains("primary checker error"));
                assert!(matches!(
                    actual,
                    Some(ReplayActual::Turn {
                        checks: None,
                        state_check_count: None,
                        ..
                    })
                ));
                error_events += 1;
                return Err(ModelError::new("secondary observer error"));
            }
            Ok(())
        },
    )
    .unwrap_err();
    assert_eq!(model.steps.get(), 1);
    assert_eq!(model.state_checks.get(), 2);
    assert_eq!(model.turn_checks.get(), 0);
    assert_eq!(model.output_encodes.get(), 0);
    assert_eq!(error_events, 1);
    assert!(error.error.to_string().contains("primary checker error"));
    assert!(
        error
            .observer_error
            .unwrap()
            .to_string()
            .contains("secondary observer error")
    );
    let Some(actual) = error.actual else {
        panic!("actual transition lost")
    };
    assert!(matches!(
        *actual,
        ReplayActual::Turn {
            transition: Transition { state: 1, .. },
            checks: None,
            ..
        }
    ));
}
#[test]
fn bounded_replay_validates_artifact_before_any_model_decode() {
    let model = Fixture::default();
    let limits = ReadLimits {
        max_checks_per_step: 1,
        ..Default::default()
    };
    let error = replay_with_observations_bounded(
        &model,
        &failure_trace(),
        ReplayOptions::default(),
        &limits,
        |_, _| Ok(()),
    )
    .unwrap_err();
    assert!(error.error.to_string().contains("bounded replay artifact"));
    assert!(error.actual.is_none());
    assert_eq!(model.decodes.get(), 0);
    assert_eq!(model.steps.get(), 0);
    assert_eq!(model.state_checks.get(), 0);
}
#[test]
fn actual_output_overflow_retains_checks_without_starting_output_encoding() {
    let model = Fixture {
        extra_output: true,
        ..Default::default()
    };
    let limits = ReadLimits {
        max_outputs_per_step: 2,
        ..Default::default()
    };
    let error = replay_with_observations_bounded(
        &model,
        &failure_trace(),
        ReplayOptions::default(),
        &limits,
        |_, _| Ok(()),
    )
    .unwrap_err();
    assert!(error.error.to_string().contains("outputs per step"));
    assert_eq!(model.steps.get(), 1);
    assert_eq!(model.state_checks.get(), 2);
    assert_eq!(model.turn_checks.get(), 1);
    assert_eq!(model.output_encodes.get(), 0);
    assert!(matches!(
        *error.actual.unwrap(),
        ReplayActual::Turn {
            checks: Some(_),
            state_check_count: Some(1),
            ..
        }
    ));
}
#[test]
fn sealed_checked_recording_never_rechecks_and_rejects_sampling_policy() {
    let model = Fixture::default();
    let initial = check_initial(&model, &0).unwrap();
    let mut recorder =
        Recorder::with_checked_initial(&initial, RunConfig::default(), RecorderOptions::default())
            .unwrap();
    let turn = model.step(&0, &1).unwrap();
    let checked = check_turn(&model, &0, &1, &turn, 1, CheckPolicy::default()).unwrap();
    recorder.observe_checked(&checked).unwrap();
    assert_eq!(
        (
            model.steps.get(),
            model.state_checks.get(),
            model.turn_checks.get()
        ),
        (1, 2, 1)
    );
    let next = model.step(&1, &1).unwrap();
    let sampled = check_turn(
        &model,
        &1,
        &1,
        &next,
        2,
        CheckPolicy {
            state_every: NonZeroU64::new(2).unwrap(),
            transition_checks: true,
        },
    )
    .unwrap();
    // This particular periodic turn did check everything. The coverage policy is
    // still unsuitable for an exact recorder and cannot silently be trusted.
    assert!(recorder.observe_checked(&sampled).is_err());
    assert_eq!(
        (
            model.steps.get(),
            model.state_checks.get(),
            model.turn_checks.get()
        ),
        (2, 3, 2)
    );
    let trace = recorder.snapshot();
    assert_eq!(trace.steps.len(), 1);
    assert!(matches!(trace.termination, Termination::ModelError(_)));
}
#[test]
fn every_single_bit_artifact_corruption_is_rejected_under_finite_limits() {
    let trace = failure_trace();
    let mut bytes = vec![];
    trace.write_to(&mut bytes).unwrap();
    for index in 0..bytes.len() {
        for bit in 0..8 {
            bytes[index] ^= 1 << bit;
            assert!(
                Trace::read_from(bytes.as_slice(), &ReadLimits::default()).is_err(),
                "accepted corruption at byte {index}, bit {bit}"
            );
            bytes[index] ^= 1 << bit;
        }
    }
    assert_eq!(
        Trace::read_from(bytes.as_slice(), &ReadLimits::default()).unwrap(),
        trace
    );
}

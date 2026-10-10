//! Independent adversarial bounds tests for debugger-facing rich replay.
use stateless::execution::{
    ReplayActual, ReplayObservation, ReplayOptions, ReplayOutcome, record,
    replay_with_observations, replay_with_observations_bounded,
};
use stateless::trace::{ReadLimits, RunConfig, Trace};
use stateless::{
    Check, Disposition, EncodeBuffer, Model, ModelCodec, ModelError, ModelMetadata, Transition,
};
use std::cell::Cell;

#[derive(Default)]
struct Fixture {
    fault: Cell<&'static str>,
    steps: Cell<usize>,
    decodes: Cell<usize>,
    state_checks: Cell<usize>,
    output_encodes: Cell<usize>,
}
impl Fixture {
    fn reset(&self, fault: &'static str) {
        self.fault.set(fault);
        self.steps.set(0);
        self.decodes.set(0);
        self.state_checks.set(0);
        self.output_encodes.set(0);
    }
    fn trace(&self) -> Trace {
        record(self, [1, 1], RunConfig::default(), 2).unwrap()
    }
}
impl Model for Fixture {
    type State = u8;
    type Input = u8;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "bounded-review".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "fixture".into(),
        }
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u8, input: &u8) -> Result<Transition<u8, ()>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        Ok(Transition {
            state: state + input,
            outputs: vec![
                ();
                if self.fault.get() == "count" {
                    1_000_000
                } else {
                    2
                }
            ],
            disposition: if self.fault.get() == "reason" {
                Disposition::Rejected("x".repeat(200))
            } else {
                Disposition::Accepted
            },
        })
    }
    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        self.state_checks.set(self.state_checks.get() + 1);
        Ok(vec![
            Check::passed("s");
            if self.fault.get() == "checks" && *state > 0 {
                100
            } else {
                1
            }
        ])
    }
}
impl ModelCodec for Fixture {
    fn encode_state(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*state])
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        self.decodes.set(self.decodes.get() + 1);
        bytes
            .first()
            .copied()
            .ok_or_else(|| ModelError::new("empty"))
    }
    fn encode_input(&self, input: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*input])
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        self.decode_state(bytes)
    }
    fn encode_output(&self, _: &()) -> Result<Vec<u8>, ModelError> {
        Ok(vec![0])
    }
    fn encode_state_into(&self, state: &u8, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        if self.fault.get() == "initial-large" || (self.fault.get() == "state-large" && *state > 0)
        {
            out.extend_from_slice(&[0; 400])
        } else {
            out.extend_from_slice(&[*state])
        }
    }
    fn encode_output_into(&self, _: &(), out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        self.output_encodes.set(self.output_encodes.get() + 1);
        if self.fault.get() == "output-large" {
            out.extend_from_slice(&[0; 300])
        } else {
            out.extend_from_slice(&[0])
        }
    }
}
#[test]
fn finite_replay_preserves_exact_report_and_callbacks() {
    let model = Fixture::default();
    let trace = model.trace();
    model.reset("");
    let mut bounded = vec![];
    let report = replay_with_observations_bounded(
        &model,
        &trace,
        ReplayOptions::default(),
        &ReadLimits::default(),
        |o, _| {
            bounded.push(match o {
                ReplayObservation::Initial(_) => 0,
                ReplayObservation::Turn(t) => t.actual.sequence,
                ReplayObservation::Error { sequence, .. } => sequence,
            });
            Ok(())
        },
    )
    .unwrap();
    assert_eq!(model.steps.get(), 2);
    assert_eq!(model.state_checks.get(), 3);
    let mut legacy = vec![];
    let prior = replay_with_observations(&model, &trace, ReplayOptions::default(), |o, _| {
        legacy.push(match o {
            ReplayObservation::Initial(_) => 0,
            ReplayObservation::Turn(t) => t.actual.sequence,
            ReplayObservation::Error { sequence, .. } => sequence,
        });
        Ok(())
    })
    .unwrap();
    assert_eq!(report, prior);
    assert_eq!(bounded, legacy);
    assert_eq!(report.outcome, ReplayOutcome::Exact);
}
#[test]
fn oversized_source_rejected_before_decode_or_execution() {
    let model = Fixture::default();
    let trace = model.trace();
    model.reset("");
    let mut events = 0;
    let error = replay_with_observations_bounded(
        &model,
        &trace,
        ReplayOptions::default(),
        &ReadLimits {
            max_steps: 1,
            ..Default::default()
        },
        |o, _| {
            assert!(matches!(
                o,
                ReplayObservation::Error {
                    sequence: 0,
                    actual: None,
                    ..
                }
            ));
            events += 1;
            Ok(())
        },
    )
    .unwrap_err();
    assert!(error.actual.is_none());
    assert_eq!(events, 1);
    assert_eq!(model.decodes.get(), 0);
    assert_eq!(model.steps.get(), 0);
}
#[test]
fn huge_zero_sized_output_count_is_rejected_before_encoded_vector_allocation() {
    let model = Fixture::default();
    let trace = model.trace();
    model.reset("count");
    let error = replay_with_observations_bounded(
        &model,
        &trace,
        ReplayOptions::default(),
        &ReadLimits {
            max_outputs_per_step: 4,
            ..Default::default()
        },
        |_, _| Ok(()),
    )
    .unwrap_err();
    assert_eq!(model.steps.get(), 1);
    assert_eq!(model.output_encodes.get(), 0);
    assert_eq!(model.state_checks.get(), 2);
    match error.actual.as_deref().unwrap() {
        ReplayActual::Turn {
            sequence,
            transition,
            checks,
            ..
        } => {
            assert_eq!(*sequence, 1);
            assert_eq!(transition.outputs.len(), 1_000_000);
            assert!(checks.is_some());
        }
        _ => panic!("actual turn missing"),
    }
}
#[test]
fn excessive_actual_checks_are_retained_without_encoding_outputs() {
    let model = Fixture::default();
    let trace = model.trace();
    model.reset("checks");
    let error = replay_with_observations_bounded(
        &model,
        &trace,
        ReplayOptions::default(),
        &ReadLimits {
            max_checks_per_step: 4,
            ..Default::default()
        },
        |_, _| Ok(()),
    )
    .unwrap_err();
    assert_eq!(model.steps.get(), 1);
    assert_eq!(model.output_encodes.get(), 0);
    assert!(
        matches!(error.actual.as_deref(),Some(ReplayActual::Turn{checks:Some(c),..})if c.len()==100)
    );
}
#[test]
fn actual_reason_limits_are_checked_before_encoding_outputs() {
    let model = Fixture::default();
    let trace = model.trace();
    model.reset("reason");
    let error = replay_with_observations_bounded(
        &model,
        &trace,
        ReplayOptions::default(),
        &ReadLimits {
            max_string_bytes: 128,
            ..Default::default()
        },
        |_, _| Ok(()),
    )
    .unwrap_err();
    assert_eq!(model.steps.get(), 1);
    assert_eq!(model.output_encodes.get(), 0);
    assert!(error.actual.is_some());
}
#[test]
fn output_bodies_share_one_frame_budget() {
    let model = Fixture::default();
    let trace = model.trace();
    model.reset("output-large");
    let error = replay_with_observations_bounded(
        &model,
        &trace,
        ReplayOptions::default(),
        &ReadLimits {
            max_frame_bytes: 512,
            max_blob_bytes: 400,
            ..Default::default()
        },
        |_, _| Ok(()),
    )
    .unwrap_err();
    assert_eq!(model.steps.get(), 1);
    assert_eq!(model.output_encodes.get(), 2);
    assert!(matches!(
        error.actual.as_deref(),
        Some(ReplayActual::Turn {
            checks: Some(_),
            ..
        })
    ));
}
#[test]
fn initial_and_post_state_encoders_have_finite_blob_budgets() {
    for fault in ["initial-large", "state-large"] {
        let model = Fixture::default();
        let trace = model.trace();
        model.reset(fault);
        let error = replay_with_observations_bounded(
            &model,
            &trace,
            ReplayOptions::default(),
            &ReadLimits {
                max_blob_bytes: 128,
                ..Default::default()
            },
            |_, _| Ok(()),
        )
        .unwrap_err();
        assert_eq!(model.steps.get(), usize::from(fault == "state-large"));
        assert!(error.actual.is_some());
    }
}
#[test]
fn exact_artifact_total_budget_and_between_turn_cancellation_are_preserved() {
    let model = Fixture::default();
    let trace = model.trace();
    let mut bytes = vec![];
    trace.write_to(&mut bytes).unwrap();
    model.reset("");
    let limits = ReadLimits {
        max_total_bytes: bytes.len() as u64,
        ..Default::default()
    };
    assert_eq!(
        replay_with_observations_bounded(
            &model,
            &trace,
            ReplayOptions::default(),
            &limits,
            |_, _| Ok(())
        )
        .unwrap()
        .outcome,
        ReplayOutcome::Exact
    );
    model.reset("");
    let error = replay_with_observations_bounded(
        &model,
        &trace,
        ReplayOptions::default(),
        &limits,
        |o, _| {
            if matches!(o, ReplayObservation::Turn(_)) {
                Err(ModelError::new("cancelled"))
            } else {
                Ok(())
            }
        },
    )
    .unwrap_err();
    assert_eq!(model.steps.get(), 1);
    assert_eq!(error.report.steps_verified, 1);
    assert!(error.actual.is_some());
}
#[test]
fn actual_observations_respect_aggregate_total_and_item_limits() {
    let model = Fixture::default();
    let trace = model.trace();
    let mut bytes = vec![];
    trace.write_to(&mut bytes).unwrap();
    model.reset("output-large");
    let error = replay_with_observations_bounded(
        &model,
        &trace,
        ReplayOptions::default(),
        &ReadLimits {
            max_total_bytes: bytes.len() as u64,
            ..Default::default()
        },
        |_, _| Ok(()),
    )
    .unwrap_err();
    assert_eq!(model.steps.get(), 1);
    assert!(error.actual.is_some());
    model.reset("checks");
    let error = replay_with_observations_bounded(
        &model,
        &trace,
        ReplayOptions::default(),
        &ReadLimits {
            max_items: 20,
            max_checks_per_step: 200,
            ..Default::default()
        },
        |_, _| Ok(()),
    )
    .unwrap_err();
    assert_eq!(model.steps.get(), 1);
    assert_eq!(model.output_encodes.get(), 0);
    assert!(error.actual.is_some());
}

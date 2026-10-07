use stateless::TransitionRef;
use std::cell::Cell;

use stateless::execution::{
    CheckPolicy, ReplayOptions, ReplayOutcome, check_observed_into, record, record_with_limits,
    replay,
};
use stateless::trace::{ReadLimits, RunConfig, Termination, Trace};
use stateless::{
    Check, EncodeBuffer, Model, ModelCodec, ModelError, ModelMetadata, PropertyId, Transition,
};

#[derive(Default)]
struct Fixture {
    growth: usize,
    checker_error_at: Option<u8>,
    property_failure_at: Option<u8>,
    clears_state_checks: bool,
    outputs: usize,
    encoded_outputs: Cell<usize>,
}

impl Model for Fixture {
    type State = u8;
    type Input = u8;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "bounded".into(),
            build: "1".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
        }
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u8, input: &u8) -> Result<Transition<u8, u8>, ModelError> {
        Ok(Transition::accepted(state + input, vec![0; self.outputs]))
    }
    fn check_state(&self, _: &u8) -> Result<Vec<Check>, ModelError> {
        panic!("engine should use append-style checking")
    }
    fn check_state_into(&self, state: &u8, checks: &mut Vec<Check>) -> Result<(), ModelError> {
        checks.push(if self.property_failure_at == Some(*state) {
            Check::failed("state", "injected failure")
        } else {
            Check::passed("state")
        });
        if self.checker_error_at == Some(*state) {
            Err(ModelError::new("checker unavailable"))
        } else {
            Ok(())
        }
    }
    fn check_transition_into(
        &self,
        _: &u8,
        _: &u8,
        _: &TransitionRef<'_, u8, u8>,
        checks: &mut Vec<Check>,
    ) -> Result<(), ModelError> {
        if self.clears_state_checks {
            checks.clear();
            return Ok(());
        }
        checks.push(Check::passed("transition"));
        Ok(())
    }
}

impl ModelCodec for Fixture {
    fn encode_state(&self, _: &u8) -> Result<Vec<u8>, ModelError> {
        panic!("use bounded codec")
    }
    fn encode_input(&self, _: &u8) -> Result<Vec<u8>, ModelError> {
        panic!("use bounded codec")
    }
    fn encode_output(&self, _: &u8) -> Result<Vec<u8>, ModelError> {
        panic!("use bounded codec")
    }
    fn encode_state_into(&self, state: &u8, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        for _ in 0..4 + usize::from(*state) * self.growth {
            out.extend_from_slice(&[*state])?;
        }
        Ok(())
    }
    fn encode_input_into(&self, input: &u8, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        out.extend_from_slice(&[*input])
    }
    fn encode_output_into(
        &self,
        output: &u8,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        self.encoded_outputs.set(self.encoded_outputs.get() + 1);
        out.extend_from_slice(&[*output; 32])
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        let state = *bytes
            .first()
            .ok_or_else(|| ModelError::new("empty state"))?;
        if bytes.len() != 4 + usize::from(state) * self.growth || bytes.iter().any(|b| *b != state)
        {
            return Err(ModelError::new("invalid state"));
        }
        Ok(state)
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        match bytes {
            [value] => Ok(*value),
            _ => Err(ModelError::new("invalid input")),
        }
    }
}

#[test]
fn borrowed_and_shared_property_ids_have_identical_identity() {
    let literal: PropertyId = "property".into();
    let shared: PropertyId = "property".to_owned().into();
    assert_eq!(literal, shared);
    assert_eq!(literal.as_str().as_ptr(), "property".as_ptr());
    assert_eq!(shared.as_str().as_ptr(), shared.clone().as_str().as_ptr());
    assert_eq!(Check::passed(literal), Check::passed(shared));
}

#[test]
fn reusable_checking_preserves_order_capacity_and_clears_partial_errors() {
    let model = Fixture::default();
    let transition = model.step(&0, &1).unwrap();
    let mut checks = Vec::with_capacity(8);
    let pointer = checks.as_ptr();
    for sequence in 1..4 {
        check_observed_into(
            &model,
            &0,
            &1,
            &transition,
            sequence,
            CheckPolicy::default(),
            &mut checks,
        )
        .unwrap();
        assert_eq!(
            checks,
            [Check::passed("state"), Check::passed("transition")]
        );
        assert_eq!(checks.as_ptr(), pointer);
    }
    let error_model = Fixture {
        checker_error_at: Some(1),
        ..Fixture::default()
    };
    assert!(
        check_observed_into(
            &error_model,
            &0,
            &1,
            &transition,
            1,
            CheckPolicy::default(),
            &mut checks
        )
        .is_err()
    );
    assert!(checks.is_empty());
    assert!(
        check_observed_into(
            &model,
            &0,
            &1,
            &transition,
            0,
            CheckPolicy::default(),
            &mut checks
        )
        .is_err()
    );
}

#[test]
fn bounded_encoder_rejects_before_growth_and_keeps_errors_sticky() {
    let mut bytes = Vec::new();
    {
        let mut output = EncodeBuffer::new(&mut bytes, 8);
        output.extend_from_slice(&[1; 8]).unwrap();
        assert!(output.extend_from_slice(&[2]).is_err());
        assert!(output.extend_from_slice(&[]).is_err());
        assert!(output.finish().is_err());
    }
    assert_eq!(bytes, [1; 8]);
    assert!(bytes.capacity() <= 8);
    let pointer = bytes.as_ptr();
    let mut output = EncodeBuffer::new(&mut bytes, 8);
    output.extend_from_slice(&[3; 8]).unwrap();
    output.finish().unwrap();
    assert_eq!(bytes.as_ptr(), pointer);
}

fn exported_prefix(model: &Fixture, trace: &Trace, limits: &ReadLimits) {
    let mut bytes = Vec::new();
    trace.write_with_limits(&mut bytes, limits).unwrap();
    assert!(bytes.len() as u64 <= limits.max_total_bytes);
    let decoded = Trace::read_from(bytes.as_slice(), limits).unwrap();
    assert_eq!(decoded, *trace);
    let report = replay(model, &decoded, ReplayOptions::default()).unwrap();
    assert_eq!(report.outcome, ReplayOutcome::Exact);
    assert_eq!(report.steps_verified, trace.steps.len());
}

#[test]
fn capture_rejects_oversized_successor_and_exports_coherent_prefix() {
    let model = Fixture {
        growth: 16,
        ..Fixture::default()
    };
    let limits = ReadLimits {
        max_blob_bytes: 32,
        ..ReadLimits::default()
    };
    let trace = record_with_limits(&model, [1, 1, 1], RunConfig::default(), 3, &limits).unwrap();
    assert_eq!(trace.steps.len(), 1);
    assert!(
        matches!(&trace.termination, Termination::ModelError(reason) if reason.contains("byte limit"))
    );
    exported_prefix(&model, &trace, &limits);
}

#[test]
fn checker_error_preserves_already_recorded_evidence() {
    let model = Fixture {
        checker_error_at: Some(2),
        ..Fixture::default()
    };
    let trace = record(&model, [1, 1, 1], RunConfig::default(), 3).unwrap();
    assert_eq!(trace.steps.len(), 1);
    assert!(
        matches!(&trace.termination, Termination::ModelError(reason) if reason.contains("checker unavailable"))
    );
    exported_prefix(&model, &trace, &ReadLimits::default());
}

#[test]
fn total_budget_terminates_before_export_would_fail() {
    let model = Fixture::default();
    let limits = ReadLimits {
        max_total_bytes: 1024,
        ..ReadLimits::default()
    };
    let trace = record_with_limits(&model, [1; 100], RunConfig::default(), 100, &limits).unwrap();
    assert!(trace.steps.len() < 100);
    assert!(matches!(trace.termination, Termination::ModelError(_)));
    exported_prefix(&model, &trace, &limits);
}

#[test]
fn output_encoding_stops_at_cumulative_frame_budget() {
    let model = Fixture {
        outputs: 100,
        ..Fixture::default()
    };
    let limits = ReadLimits {
        max_frame_bytes: 512,
        ..ReadLimits::default()
    };
    let trace = record_with_limits(&model, [1], RunConfig::default(), 1, &limits).unwrap();
    assert!(trace.steps.is_empty());
    assert!(matches!(trace.termination, Termination::ModelError(_)));
    assert!(model.encoded_outputs.get() < 20);
    exported_prefix(&model, &trace, &limits);
}

#[test]
fn terminal_property_failures_use_their_actual_footer_at_exact_byte_limit() {
    for failure_at in [0, 1, 2] {
        let model = Fixture {
            growth: 512,
            property_failure_at: Some(failure_at),
            ..Fixture::default()
        };
        let baseline = record(&model, [1, 1], RunConfig::default(), 2).unwrap();
        assert_eq!(baseline.termination, Termination::PropertyFailed);
        let mut bytes = Vec::new();
        baseline.write_to(&mut bytes).unwrap();
        let limits = ReadLimits {
            max_total_bytes: bytes.len() as u64,
            ..ReadLimits::default()
        };
        let bounded = record_with_limits(&model, [1, 1], RunConfig::default(), 2, &limits).unwrap();
        assert_eq!(bounded, baseline, "failure at {failure_at}");
        exported_prefix(&model, &bounded, &limits);
    }
}

#[test]
fn transition_checker_cannot_silently_discard_state_observations() {
    let model = Fixture {
        clears_state_checks: true,
        ..Fixture::default()
    };
    let transition = model.step(&0, &1).unwrap();
    let mut checks = Vec::new();
    let error = check_observed_into(
        &model,
        &0,
        &1,
        &transition,
        1,
        CheckPolicy::default(),
        &mut checks,
    )
    .unwrap_err();
    assert!(error.0.contains("removed state observations"));
    assert!(checks.is_empty());
    let mut recorder =
        stateless::monitor::Recorder::new(&model, &0, RunConfig::default(), 1).unwrap();
    assert!(recorder.observe(&model, &0, &1, &transition).is_err());
    assert!(recorder.is_frozen());
    assert_eq!(recorder.retained_steps(), 0);
}

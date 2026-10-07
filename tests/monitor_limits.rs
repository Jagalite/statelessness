use stateless::execution::{ReplayOptions, ReplayOutcome, replay};
use stateless::monitor::{Recorder, RecorderOptions};
use stateless::trace::{ReadLimits, RunConfig, Termination, Trace};
use stateless::{Check, EncodeBuffer, Model, ModelCodec, ModelError, ModelMetadata, Transition};
use std::cell::Cell;

#[derive(Clone, Debug, PartialEq, Eq)]
struct Command {
    state_bytes: usize,
    outputs: usize,
    output_bytes: usize,
}

#[derive(Default)]
struct Blobs {
    output_encodings: Cell<usize>,
    state_checks: Cell<usize>,
    ignore_output_limit: bool,
    error_at: Option<usize>,
    fail_at: Option<usize>,
}

impl Model for Blobs {
    type State = Vec<u8>;
    type Input = Command;
    type Output = Vec<u8>;

    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "monitor-limit-fixture".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "bounded-blobs-v1".into(),
        }
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        Ok(vec![7; 64])
    }
    fn step(
        &self,
        _: &Self::State,
        input: &Command,
    ) -> Result<Transition<Self::State, Self::Output>, ModelError> {
        Ok(Transition::accepted(
            vec![7; input.state_bytes],
            vec![vec![9; input.output_bytes]; input.outputs],
        ))
    }
    fn check_state(&self, state: &Self::State) -> Result<Vec<Check>, ModelError> {
        let mut checks = Vec::new();
        self.check_state_into(state, &mut checks)?;
        Ok(checks)
    }
    fn check_state_into(
        &self,
        state: &Self::State,
        checks: &mut Vec<Check>,
    ) -> Result<(), ModelError> {
        self.state_checks.set(self.state_checks.get() + 1);
        if self.error_at == Some(state.len()) {
            return Err(ModelError::new(
                "oversized checker detail: ".to_owned() + &"é".repeat(1000),
            ));
        }
        checks.push(
            if self.fail_at != Some(state.len()) && state.iter().all(|byte| *byte == 7) {
                Check::passed("state-bytes")
            } else {
                Check::failed("state-bytes", "unexpected state bytes")
            },
        );
        Ok(())
    }
}

impl ModelCodec for Blobs {
    fn encode_state(&self, _: &Self::State) -> Result<Vec<u8>, ModelError> {
        panic!("bounded sink must be used")
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<Self::State, ModelError> {
        Ok(bytes.to_vec())
    }
    fn encode_input(&self, _: &Command) -> Result<Vec<u8>, ModelError> {
        panic!("bounded sink must be used")
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Command, ModelError> {
        if bytes.len() != 24 {
            return Err(ModelError::new("invalid command"));
        }
        let mut values = bytes
            .as_chunks::<8>()
            .0
            .iter()
            .map(|chunk| u64::from_le_bytes(*chunk) as usize);
        Ok(Command {
            state_bytes: values.next().unwrap(),
            outputs: values.next().unwrap(),
            output_bytes: values.next().unwrap(),
        })
    }
    fn encode_output(&self, _: &Self::Output) -> Result<Vec<u8>, ModelError> {
        panic!("bounded sink must be used")
    }
    fn encode_state_into(
        &self,
        state: &Self::State,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_slice(state)
    }
    fn encode_input_into(
        &self,
        input: &Command,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        for value in [input.state_bytes, input.outputs, input.output_bytes] {
            out.extend_from_slice(&(value as u64).to_le_bytes())?;
        }
        Ok(())
    }
    fn encode_output_into(
        &self,
        output: &Self::Output,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        self.output_encodings.set(self.output_encodings.get() + 1);
        let result = out.extend_from_slice(output);
        if self.ignore_output_limit {
            Ok(())
        } else {
            result
        }
    }
}

fn observe(model: &Blobs, recorder: &mut Recorder, state: &mut Vec<u8>, input: &Command) {
    let next = model.step(state, input).unwrap();
    recorder.observe(model, state, input, &next).unwrap();
    *state = next.state;
}

#[test]
fn byte_eviction_preserves_replay_and_borrowed_consuming_exports_match() {
    let model = Blobs::default();
    let mut state = model.initial_state().unwrap();
    let options = RecorderOptions {
        max_steps: 100,
        max_retained_bytes: 1_400,
        ..RecorderOptions::default()
    };
    let mut recorder =
        Recorder::with_options(&model, &state, RunConfig::default(), options).unwrap();
    let input = Command {
        state_bytes: 64,
        outputs: 1,
        output_bytes: 32,
    };
    for _ in 0..30 {
        observe(&model, &mut recorder, &mut state, &input);
        let mut bytes = Vec::new();
        recorder.write_to(&mut bytes).unwrap();
        assert_eq!(recorder.retained_bytes(), bytes.len() as u64);
        assert!(bytes.len() <= 1_400);
        let restored = Trace::read_from(bytes.as_slice(), &ReadLimits::default()).unwrap();
        assert_eq!(
            replay(&model, &restored, ReplayOptions::default())
                .unwrap()
                .outcome,
            ReplayOutcome::Exact
        );
    }
    assert!(recorder.evicted_steps() > 0);
    assert!(recorder.retained_steps() < 30);
    let copied = recorder.snapshot();
    let mut copied_bytes = Vec::new();
    copied.write_to(&mut copied_bytes).unwrap();
    let mut borrowed_bytes = Vec::new();
    recorder.write_to(&mut borrowed_bytes).unwrap();
    assert_eq!(borrowed_bytes, copied_bytes);
    assert_eq!(recorder.into_trace(), copied);
}

#[test]
fn candidate_that_cannot_fit_with_its_checkpoint_does_not_evict_the_prefix() {
    let model = Blobs::default();
    let mut state = model.initial_state().unwrap();
    let options = RecorderOptions {
        max_steps: 1,
        max_retained_bytes: 1_400,
        ..RecorderOptions::default()
    };
    let mut recorder =
        Recorder::with_options(&model, &state, RunConfig::default(), options).unwrap();
    observe(
        &model,
        &mut recorder,
        &mut state,
        &Command {
            state_bytes: 64,
            outputs: 0,
            output_bytes: 0,
        },
    );
    let prefix = recorder.snapshot();
    let input = Command {
        state_bytes: 600,
        outputs: 0,
        output_bytes: 0,
    };
    let next = model.step(&state, &input).unwrap();
    let error = recorder.observe(&model, &state, &input, &next).unwrap_err();
    assert!(error.0.contains("byte budget"));
    assert!(recorder.is_frozen());
    assert_eq!(recorder.evicted_steps(), 0);
    assert_eq!(recorder.observed_steps(), 1);
    let frozen = recorder.snapshot();
    assert_eq!(frozen.initial_state, prefix.initial_state);
    assert_eq!(frozen.steps, prefix.steps);
    assert!(matches!(frozen.termination, Termination::ModelError(_)));
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

#[test]
fn cumulative_frame_budget_stops_outputs_before_collecting_all_of_them() {
    for ignore_output_limit in [false, true] {
        let model = Blobs {
            ignore_output_limit,
            ..Blobs::default()
        };
        let state = model.initial_state().unwrap();
        let options = RecorderOptions {
            limits: ReadLimits {
                max_frame_bytes: 1_024,
                max_blob_bytes: 512,
                ..ReadLimits::default()
            },
            ..RecorderOptions::default()
        };
        let mut recorder =
            Recorder::with_options(&model, &state, RunConfig::default(), options).unwrap();
        let input = Command {
            state_bytes: 64,
            outputs: 20,
            output_bytes: 128,
        };
        let next = model.step(&state, &input).unwrap();
        assert!(recorder.observe(&model, &state, &input, &next).is_err());
        assert!(model.output_encodings.get() < input.outputs);
        assert_eq!(recorder.observed_steps(), 0);
        let mut bytes = Vec::new();
        recorder.write_to(&mut bytes).unwrap();
        assert_eq!(recorder.retained_bytes(), bytes.len() as u64);
    }
}

#[test]
fn bounded_utf8_error_footer_preserves_full_returned_error_and_exportability() {
    let model = Blobs {
        error_at: Some(65),
        ..Blobs::default()
    };
    let state = model.initial_state().unwrap();
    let mut recorder = Recorder::new(&model, &state, RunConfig::default(), 1).unwrap();
    let input = Command {
        state_bytes: 65,
        outputs: 0,
        output_bytes: 0,
    };
    let next = model.step(&state, &input).unwrap();
    let error = recorder.observe(&model, &state, &input, &next).unwrap_err();
    assert!(error.0.len() > 2_000);
    let frozen = recorder.snapshot();
    let Termination::ModelError(reason) = &frozen.termination else {
        panic!("expected recorded error");
    };
    assert!(reason.len() <= 256);
    assert!(reason.ends_with("..."));
    let mut bytes = Vec::new();
    recorder.write_to(&mut bytes).unwrap();
    assert_eq!(recorder.retained_bytes(), bytes.len() as u64);
    let restored = Trace::read_from(bytes.as_slice(), &ReadLimits::default()).unwrap();
    assert_eq!(restored, frozen);
    assert_eq!(
        replay(&model, &restored, ReplayOptions::default())
            .unwrap()
            .outcome,
        ReplayOutcome::Exact
    );
}

#[test]
fn initial_checkpoint_and_format_limits_are_enforced_before_capture() {
    let model = Blobs::default();
    for options in [
        RecorderOptions {
            max_retained_bytes: 32,
            ..RecorderOptions::default()
        },
        RecorderOptions {
            limits: ReadLimits {
                max_blob_bytes: 32,
                ..ReadLimits::default()
            },
            ..RecorderOptions::default()
        },
        RecorderOptions {
            limits: ReadLimits {
                max_checks_per_step: 0,
                ..ReadLimits::default()
            },
            ..RecorderOptions::default()
        },
        RecorderOptions {
            limits: ReadLimits {
                max_parameters: 1,
                ..ReadLimits::default()
            },
            ..RecorderOptions::default()
        },
    ] {
        assert!(
            Recorder::with_options(&model, &vec![7; 64], RunConfig::default(), options).is_err()
        );
    }
}

fn exact_monitor_budget(mut trace: Trace) -> (Trace, u64) {
    let mut budget = 1_u64;
    loop {
        trace
            .config
            .parameters
            .iter_mut()
            .find(|(key, _)| key == "stateless.monitor.max_retained_bytes")
            .unwrap()
            .1 = budget.to_string();
        let mut bytes = Vec::new();
        trace.write_to(&mut bytes).unwrap();
        let size = bytes.len() as u64;
        if size == budget {
            return (trace, budget);
        }
        budget = size;
    }
}

#[test]
fn initial_property_failure_fits_without_reserving_an_unreachable_future_error() {
    let model = Blobs {
        fail_at: Some(64),
        ..Blobs::default()
    };
    let state = model.initial_state().unwrap();
    let probe = Recorder::new(&model, &state, RunConfig::default(), 1).unwrap();
    let (expected, budget) = exact_monitor_budget(probe.into_trace());
    let options = RecorderOptions {
        max_steps: 1,
        max_retained_bytes: budget,
        limits: ReadLimits {
            max_total_bytes: budget,
            ..ReadLimits::default()
        },
    };
    let recorder = Recorder::with_options(&model, &state, RunConfig::default(), options).unwrap();
    assert!(recorder.is_frozen());
    assert_eq!(recorder.snapshot(), expected);
    let mut bytes = Vec::new();
    recorder.write_to(&mut bytes).unwrap();
    assert_eq!(bytes.len() as u64, budget);
    assert_eq!(recorder.retained_bytes(), budget);
}

#[test]
fn failing_observation_fits_its_complete_terminal_artifact_at_the_exact_byte_limit() {
    let model = Blobs {
        fail_at: Some(400),
        ..Blobs::default()
    };
    let state = model.initial_state().unwrap();
    let input = Command {
        state_bytes: 400,
        outputs: 0,
        output_bytes: 0,
    };
    let transition = model.step(&state, &input).unwrap();
    let mut probe = Recorder::new(&model, &state, RunConfig::default(), 1).unwrap();
    probe.observe(&model, &state, &input, &transition).unwrap();
    let (expected, budget) = exact_monitor_budget(probe.into_trace());
    let options = RecorderOptions {
        max_steps: 1,
        max_retained_bytes: budget,
        limits: ReadLimits {
            max_total_bytes: budget,
            ..ReadLimits::default()
        },
    };
    let mut recorder =
        Recorder::with_options(&model, &state, RunConfig::default(), options).unwrap();
    recorder
        .observe(&model, &state, &input, &transition)
        .unwrap();
    assert_eq!(recorder.snapshot(), expected);
    assert_eq!(recorder.retained_bytes(), budget);
    let mut bytes = Vec::new();
    recorder.write_to(&mut bytes).unwrap();
    assert_eq!(bytes.len() as u64, budget);
    let report = replay(&model, &expected, ReplayOptions::default()).unwrap();
    assert_eq!(report.outcome, ReplayOutcome::Exact);
    assert!(report.failure_reproduced);
}

#[test]
fn oversized_disposition_is_rejected_before_copying_or_checking_the_candidate() {
    for disposition in [
        stateless::Disposition::Ignored("ignored".repeat(20_000)),
        stateless::Disposition::Rejected("rejected".repeat(20_000)),
    ] {
        let model = Blobs::default();
        let state = model.initial_state().unwrap();
        let mut recorder = Recorder::new(&model, &state, RunConfig::default(), 1).unwrap();
        let input = Command {
            state_bytes: 64,
            outputs: 0,
            output_bytes: 0,
        };
        let mut transition = model.step(&state, &input).unwrap();
        transition.disposition = disposition;
        let error = recorder
            .observe(&model, &state, &input, &transition)
            .unwrap_err();
        assert!(error.0.contains("string bytes"));
        assert_eq!(
            model.state_checks.get(),
            1,
            "only the initial checkpoint was checked"
        );
        assert_eq!(recorder.observed_steps(), 0);
        let mut bytes = Vec::new();
        recorder.write_to(&mut bytes).unwrap();
        assert_eq!(recorder.retained_bytes(), bytes.len() as u64);
    }
}

#[test]
fn changing_checkpoint_sizes_and_multi_evictions_preserve_exact_history() {
    let model = Blobs::default();
    let mut state = model.initial_state().unwrap();
    let mut states = vec![state.clone()];
    let options = RecorderOptions {
        max_steps: 20,
        max_retained_bytes: 2_200,
        ..RecorderOptions::default()
    };
    let mut recorder =
        Recorder::with_options(&model, &state, RunConfig::default(), options).unwrap();
    let mut multiple_evictions = false;
    for index in 0..120 {
        let input = Command {
            state_bytes: [16, 16, 16, 16, 256, 32, 128, 16, 192][index % 9],
            outputs: index % 4,
            output_bytes: index % 3 * 48,
        };
        let old_evicted = recorder.evicted_steps();
        observe(&model, &mut recorder, &mut state, &input);
        states.push(state.clone());
        multiple_evictions |= recorder.evicted_steps() - old_evicted > 1;
        let trace = recorder.snapshot();
        assert_eq!(
            trace.initial_state,
            states[recorder.evicted_steps() as usize]
        );
        assert_eq!(
            recorder.evicted_steps() + recorder.retained_steps() as u64,
            index as u64 + 1
        );
        let mut bytes = Vec::new();
        recorder.write_to(&mut bytes).unwrap();
        assert_eq!(recorder.retained_bytes(), bytes.len() as u64);
        assert!(bytes.len() <= 2_200);
        assert_eq!(
            replay(&model, &trace, ReplayOptions::default())
                .unwrap()
                .outcome,
            ReplayOutcome::Exact
        );
    }
    assert!(
        multiple_evictions,
        "the fixture must exercise more than one eviction at a time"
    );
}

#[test]
fn counter_digit_growth_that_exceeds_the_run_frame_freezes_the_old_config() {
    let model = Blobs::default();
    let mut state = model.initial_state().unwrap();
    let input = Command {
        state_bytes: 64,
        outputs: 0,
        output_bytes: 0,
    };
    let mut probe = Recorder::new(&model, &state, RunConfig::default(), 1).unwrap();
    for _ in 0..9 {
        observe(&model, &mut probe, &mut state, &input);
    }
    let mut bytes = Vec::new();
    probe.write_to(&mut bytes).unwrap();
    let run_payload_bytes = u32::from_le_bytes(bytes[13..17].try_into().unwrap()) as usize;
    let options = RecorderOptions {
        max_steps: 1,
        limits: ReadLimits {
            max_frame_bytes: run_payload_bytes,
            ..ReadLimits::default()
        },
        ..RecorderOptions::default()
    };
    let mut recorder =
        Recorder::with_options(&model, &state, RunConfig::default(), options).unwrap();
    for _ in 0..9 {
        observe(&model, &mut recorder, &mut state, &input);
    }
    let prefix = recorder.snapshot();
    let transition = model.step(&state, &input).unwrap();
    let error = recorder
        .observe(&model, &state, &input, &transition)
        .unwrap_err();
    assert!(error.0.contains("frame bytes"));
    let frozen = recorder.snapshot();
    assert_eq!(frozen.config, prefix.config);
    assert_eq!(frozen.initial_state, prefix.initial_state);
    assert_eq!(frozen.steps, prefix.steps);
    assert_eq!(recorder.observed_steps(), 9);
    assert_eq!(recorder.evicted_steps(), 8);
    let mut bytes = Vec::new();
    recorder.write_to(&mut bytes).unwrap();
    assert_eq!(recorder.retained_bytes(), bytes.len() as u64);
}

#[test]
fn error_footer_respects_a_small_string_limit_and_keeps_utf8_valid() {
    let model = Blobs {
        error_at: Some(65),
        ..Blobs::default()
    };
    let state = model.initial_state().unwrap();
    let options = RecorderOptions {
        max_steps: 1,
        limits: ReadLimits {
            max_string_bytes: 48,
            ..ReadLimits::default()
        },
        ..RecorderOptions::default()
    };
    let limits = options.limits.clone();
    let mut recorder =
        Recorder::with_options(&model, &state, RunConfig::default(), options).unwrap();
    let input = Command {
        state_bytes: 65,
        outputs: 0,
        output_bytes: 0,
    };
    let transition = model.step(&state, &input).unwrap();
    assert!(
        recorder
            .observe(&model, &state, &input, &transition)
            .is_err()
    );
    let trace = recorder.snapshot();
    let Termination::ModelError(reason) = &trace.termination else {
        panic!("expected error footer")
    };
    assert!(reason.len() <= 48);
    assert!(reason.ends_with("..."));
    let mut bytes = Vec::new();
    recorder.write_to(&mut bytes).unwrap();
    assert_eq!(Trace::read_from(bytes.as_slice(), &limits).unwrap(), trace);
}

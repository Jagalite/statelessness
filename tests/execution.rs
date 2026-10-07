use stateless::TransitionRef;
use stateless::{
    demo::{Input, Output, RequestModel, State},
    execution::*,
    trace::{RunConfig, Termination},
    *,
};
use std::num::NonZeroU64;

#[test]
fn records_and_exactly_replays_failure_and_detects_fixed_behavior() {
    let model = RequestModel::buggy();
    let trace = record(
        &model,
        [
            Input::Start,
            Input::Cancel,
            Input::Complete(1),
            Input::Start,
        ],
        RunConfig::default(),
        10,
    )
    .unwrap();
    assert_eq!(trace.steps.len(), 3);
    assert_eq!(trace.termination, Termination::PropertyFailed);
    let report = replay(&model, &trace, ReplayOptions::default()).unwrap();
    assert_eq!(report.outcome, ReplayOutcome::Exact);
    assert!(report.failure_reproduced);
    let fixed = RequestModel::fixed();
    assert!(matches!(
        replay(&fixed, &trace, ReplayOptions::default())
            .unwrap()
            .outcome,
        ReplayOutcome::Incompatible { .. }
    ));
    let report = replay(
        &fixed,
        &trace,
        ReplayOptions {
            allow_build_mismatch: true,
        },
    )
    .unwrap();
    assert_eq!(
        report.outcome,
        ReplayOutcome::Diverged {
            step: Some(3),
            field: "outputs"
        }
    );
    assert_eq!(report.steps_verified, 2);
    assert!(!report.failure_reproduced);
    assert!(!report.build_matches);
}

#[test]
fn all_observable_fields_are_compared() {
    let model = RequestModel::fixed();
    let original = record(&model, [Input::Start], RunConfig::default(), 1).unwrap();
    for field in ["state", "outputs", "checks", "disposition"] {
        let mut trace = original.clone();
        match field {
            "state" => trace.steps[0].post_state.push(9),
            "outputs" => trace.steps[0].outputs.clear(),
            "checks" => trace.steps[0].checks.push(Check::passed("extra")),
            "disposition" => trace.steps[0].disposition = Disposition::Ignored("changed".into()),
            _ => unreachable!(),
        }
        assert_eq!(
            replay(&model, &trace, ReplayOptions::default())
                .unwrap()
                .outcome,
            ReplayOutcome::Diverged {
                step: Some(1),
                field
            }
        );
    }
}

#[test]
fn reproducing_a_failure_is_independent_of_exact_observation_matching() {
    let model = RequestModel::buggy();
    let mut trace = record(
        &model,
        [Input::Start, Input::Cancel, Input::Complete(1)],
        RunConfig::default(),
        3,
    )
    .unwrap();
    let check = trace
        .steps
        .last_mut()
        .unwrap()
        .checks
        .iter_mut()
        .find(|check| check.is_failure())
        .unwrap();
    check.status = CheckStatus::Failed("different recorded diagnostic".into());
    let report = replay(&model, &trace, ReplayOptions::default()).unwrap();
    assert_eq!(
        report.outcome,
        ReplayOutcome::Diverged {
            step: Some(3),
            field: "checks"
        }
    );
    assert_eq!(report.steps_verified, 2);
    assert!(report.failure_reproduced);
}

#[test]
fn identity_mismatches_cannot_be_overridden_by_the_build_option() {
    let model = RequestModel::fixed();
    let original = record(&model, [Input::Start], RunConfig::default(), 1).unwrap();
    for identity in ["name", "model", "properties", "codec"] {
        let mut trace = original.clone();
        match identity {
            "name" => trace.metadata.name.push_str("-other"),
            "model" => trace.metadata.model_version += 1,
            "properties" => trace.metadata.properties_version += 1,
            "codec" => trace.metadata.codec_version += 1,
            _ => unreachable!(),
        }
        // Incompatibility must be detected before interpreting foreign payloads.
        trace.initial_state.clear();
        let report = replay(
            &model,
            &trace,
            ReplayOptions {
                allow_build_mismatch: true,
            },
        )
        .unwrap();
        assert!(matches!(report.outcome, ReplayOutcome::Incompatible { .. }));
        assert_eq!(report.steps_verified, 0);
        assert!(!report.failure_reproduced);
    }
}

#[test]
fn malformed_or_noncanonical_payloads_cannot_replay_successfully() {
    let model = RequestModel::fixed();
    let original = record(&model, [Input::Start], RunConfig::default(), 1).unwrap();
    let mut bad_state = original.clone();
    bad_state.initial_state.clear();
    assert!(
        replay(&model, &bad_state, ReplayOptions::default())
            .unwrap_err()
            .0
            .contains("decode initial state")
    );
    let mut bad_input = original.clone();
    bad_input.steps[0].input.push(255);
    assert!(
        replay(&model, &bad_input, ReplayOptions::default())
            .unwrap_err()
            .0
            .contains("decode input")
    );

    let mut state_alias = original.clone();
    state_alias.initial_state.insert(0, 255);
    assert!(
        replay(&AliasCodec, &state_alias, ReplayOptions::default())
            .unwrap_err()
            .0
            .contains("initial state encoding is not canonical")
    );
    let mut input_alias = original;
    input_alias.steps[0].input.insert(0, 255);
    assert!(
        replay(&AliasCodec, &input_alias, ReplayOptions::default())
            .unwrap_err()
            .0
            .contains("input 1 encoding is not canonical")
    );
}

#[test]
fn budgets_and_initial_failures_are_reported_honestly() {
    let model = RequestModel::fixed();
    let inputs = [Input::Start, Input::Complete(1)];
    let trace = record(&model, inputs.clone(), RunConfig::default(), 1).unwrap();
    assert_eq!(trace.termination, Termination::StepLimit);
    assert_eq!(trace.steps.len(), 1);
    assert_eq!(
        replay(&model, &trace, ReplayOptions::default())
            .unwrap()
            .outcome,
        ReplayOutcome::Exact
    );
    let trace = record(&model, inputs, RunConfig::default(), 2).unwrap();
    assert_eq!(trace.termination, Termination::Completed);
    let trace = record(&model, [Input::Start], RunConfig::default(), 0).unwrap();
    assert!(trace.steps.is_empty());
    assert_eq!(trace.termination, Termination::StepLimit);
    let trace = record(
        &FaultModel {
            initial_failure: true,
            checker_error: false,
        },
        [Input::Start],
        RunConfig::default(),
        10,
    )
    .unwrap();
    assert!(trace.steps.is_empty());
    assert_eq!(trace.termination, Termination::PropertyFailed);
    assert!(
        replay(
            &FaultModel {
                initial_failure: true,
                checker_error: false
            },
            &trace,
            ReplayOptions::default()
        )
        .unwrap()
        .failure_reproduced
    );
}

#[test]
fn checker_errors_and_invalid_trace_semantics_cannot_pass() {
    assert!(
        record(
            &FaultModel {
                initial_failure: false,
                checker_error: true
            },
            [Input::Start],
            RunConfig::default(),
            10
        )
        .is_err()
    );
    let model = RequestModel::buggy();
    let mut trace = record(
        &model,
        [Input::Start, Input::Cancel, Input::Complete(1)],
        RunConfig::default(),
        3,
    )
    .unwrap();
    trace.termination = Termination::Completed;
    assert!(replay(&model, &trace, ReplayOptions::default()).is_err());
    trace.termination = Termination::PropertyFailed;
    trace.steps.push(trace.steps[0].clone());
    assert!(replay(&model, &trace, ReplayOptions::default()).is_err());
}

#[test]
fn runtime_checking_does_not_repeat_transition_and_declares_skips() {
    let model = RequestModel::buggy();
    let before = model.initial_state().unwrap();
    let transition = model.step(&before, &Input::Start).unwrap();
    let policy = CheckPolicy {
        state_every: NonZeroU64::new(2).unwrap(),
        transition_checks: false,
    };
    let skipped = check_observed(&model, &before, &Input::Start, &transition, 1, policy).unwrap();
    assert_eq!(skipped.len(), 2);
    assert!(
        skipped
            .iter()
            .all(|c| matches!(c.status, CheckStatus::Skipped(_)))
    );
    let checked = check_observed(&model, &before, &Input::Start, &transition, 2, policy).unwrap();
    assert_eq!(
        checked
            .iter()
            .filter(|c| c.status == CheckStatus::Passed)
            .count(),
        2
    );
    assert!(check_observed(&model, &before, &Input::Start, &transition, 0, policy).is_err());
}

#[test]
fn replay_observer_sees_first_divergence_and_never_changes_logical_time() {
    let model = RequestModel::buggy();
    let trace = record(
        &model,
        [Input::Start, Input::Cancel, Input::Complete(1)],
        RunConfig::default(),
        3,
    )
    .unwrap();
    let mut observed = Vec::new();
    let report = replay_with_observer(
        &RequestModel::fixed(),
        &trace,
        ReplayOptions {
            allow_build_mismatch: true,
        },
        |step, _| observed.push(step),
    )
    .unwrap();
    assert_eq!(observed, [1, 2, 3]);
    assert_eq!(report.steps_verified, 2);
}

#[test]
fn generator_algorithm_has_a_fixed_known_vector() {
    let mut rng = Rng::new(0);
    assert_eq!(rng.next_u64(), 0xe220a8397b1dcdaf);
    assert_eq!(rng.next_u64(), 0x6e789e6aa1b965f4);
    assert_eq!(rng.index(0), None);
    for _ in 0..100 {
        assert!(rng.index(3).unwrap() < 3);
    }
}

struct FaultModel {
    initial_failure: bool,
    checker_error: bool,
}
impl Model for FaultModel {
    type State = State;
    type Input = Input;
    type Output = Output;
    fn metadata(&self) -> ModelMetadata {
        RequestModel::fixed().metadata()
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        RequestModel::fixed().initial_state()
    }
    fn step(&self, s: &State, i: &Input) -> Result<Transition<State, Output>, ModelError> {
        RequestModel::fixed().step(s, i)
    }
    fn check_state(&self, _: &State) -> Result<Vec<Check>, ModelError> {
        if self.checker_error {
            Err(ModelError::new("checker offline"))
        } else if self.initial_failure {
            Ok(vec![Check::failed("initial", "bad initial state")])
        } else {
            Ok(vec![])
        }
    }
}
impl ModelCodec for FaultModel {
    fn encode_state(&self, s: &State) -> Result<Vec<u8>, ModelError> {
        RequestModel::fixed().encode_state(s)
    }
    fn decode_state(&self, b: &[u8]) -> Result<State, ModelError> {
        RequestModel::fixed().decode_state(b)
    }
    fn encode_input(&self, i: &Input) -> Result<Vec<u8>, ModelError> {
        RequestModel::fixed().encode_input(i)
    }
    fn decode_input(&self, b: &[u8]) -> Result<Input, ModelError> {
        RequestModel::fixed().decode_input(b)
    }
    fn encode_output(&self, o: &Output) -> Result<Vec<u8>, ModelError> {
        RequestModel::fixed().encode_output(o)
    }
}

// Deliberately permissive decoder: replay must reject alternate representations
// even when the application's decoder accepts them as the same typed value.
struct AliasCodec;
impl Model for AliasCodec {
    type State = State;
    type Input = Input;
    type Output = Output;
    fn metadata(&self) -> ModelMetadata {
        RequestModel::fixed().metadata()
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        RequestModel::fixed().initial_state()
    }
    fn step(&self, state: &State, input: &Input) -> Result<Transition<State, Output>, ModelError> {
        RequestModel::fixed().step(state, input)
    }
    fn check_state(&self, state: &State) -> Result<Vec<Check>, ModelError> {
        RequestModel::fixed().check_state(state)
    }
    fn check_transition(
        &self,
        state: &State,
        input: &Input,
        transition: &TransitionRef<'_, State, Output>,
    ) -> Result<Vec<Check>, ModelError> {
        RequestModel::fixed().check_transition(state, input, transition)
    }
}
impl ModelCodec for AliasCodec {
    fn encode_state(&self, state: &State) -> Result<Vec<u8>, ModelError> {
        RequestModel::fixed().encode_state(state)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<State, ModelError> {
        RequestModel::fixed().decode_state(bytes.strip_prefix(&[255]).unwrap_or(bytes))
    }
    fn encode_input(&self, input: &Input) -> Result<Vec<u8>, ModelError> {
        RequestModel::fixed().encode_input(input)
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Input, ModelError> {
        RequestModel::fixed().decode_input(bytes.strip_prefix(&[255]).unwrap_or(bytes))
    }
    fn encode_output(&self, output: &Output) -> Result<Vec<u8>, ModelError> {
        RequestModel::fixed().encode_output(output)
    }
}

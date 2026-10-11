//! Independent pre-PR adversarial tests of session and evidence contracts.
use stateless::execution::{ReplayOptions, ReplayOutcome};
use stateless::explore::{CheckPhase, PropertyFailure, ShrinkConfig, ShrinkLimits};
use stateless::monitor::RecorderOptions;
use stateless::trace::RunConfig;
use stateless::{
    Check, Enumerate, Generate, Model, ModelCodec, ModelError, ModelMetadata, Rng, Transition,
};
use statelessness_debug::workbench::{TraceViewer, minimize, minimize_with_policy};
use statelessness_debug::{DebugSession, InputPolicy, SessionLimits};

struct Domain;
impl Model for Domain {
    type State = u8;
    type Input = u8;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "review-domain".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "review".into(),
        }
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, _: &u8, input: &u8) -> Result<Transition<u8, ()>, ModelError> {
        Ok(Transition::accepted(input + 1, vec![]))
    }
    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if *state == 2 {
            Check::failed("failure", "delivered failing input")
        } else {
            Check::passed("failure")
        }])
    }
}
impl ModelCodec for Domain {
    fn encode_state(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*state])
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<u8, ModelError> {
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
    fn encode_output(&self, _: &()) -> Result<Vec<u8>, ModelError> {
        Ok(vec![])
    }
}
impl Enumerate for Domain {
    fn inputs(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(if *state == 0 { vec![0] } else { vec![1] })
    }
}
impl Generate for Domain {
    fn generate(&self, state: &u8, _: &mut Rng) -> Result<Option<u8>, ModelError> {
        Ok(self.inputs(state)?.first().copied())
    }
    // Deliberately rely on Generate's documented permissive default. The recorded
    // session's actual policy is Enumerate, a different capability.
}
fn domain_failure() -> stateless::trace::Trace {
    let mut session = DebugSession::recording(
        "review-domain",
        Domain,
        InputPolicy::enumerated(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions::default(),
    )
    .unwrap();
    session.step(0, 0).unwrap();
    session.step(1, 1).unwrap();
    session.export_trace().unwrap()
}
#[test]
fn minimization_preserves_recorded_enumerated_environment() {
    let trace = domain_failure();
    let report = minimize_with_policy(
        &Domain,
        &trace,
        &InputPolicy::enumerated(),
        100,
        PropertyFailure {
            phase: CheckPhase::State,
            check: trace.steps[1].checks[0].clone(),
        },
        ShrinkConfig::default(),
        ShrinkLimits::default(),
    )
    .unwrap();
    assert_eq!(
        report.minimized.inputs,
        [0, 1],
        "input 1 is forbidden at checkpoint 0 by the recorded environment"
    );
}
#[test]
fn verified_fork_and_parent_binding_remain_independent() {
    let trace = domain_failure();
    let viewer = TraceViewer::new(trace.clone()).unwrap();
    let branch = viewer
        .fork_verified(
            "fork",
            Domain,
            1,
            ReplayOptions::default(),
            InputPolicy::enumerated(),
            SessionLimits::default(),
            RecorderOptions::default(),
        )
        .unwrap();
    branch.provenance.validate_parent(&trace).unwrap();
    assert_eq!(viewer.trace(), &trace);
    assert_eq!(*branch.session.state(), 1);
    assert_eq!(
        viewer
            .verify(&Domain, ReplayOptions::default())
            .unwrap()
            .outcome,
        ReplayOutcome::Exact
    );
    let mut altered = trace;
    altered.steps[0].post_state[0] ^= 1;
    assert!(branch.provenance.validate_parent(&altered).is_err());
}

fn target(trace: &stateless::trace::Trace) -> PropertyFailure {
    PropertyFailure {
        phase: CheckPhase::State,
        check: trace.steps.last().unwrap().checks[0].clone(),
    }
}
#[test]
fn legacy_minimization_rejects_recorded_strict_policy_without_execution() {
    let trace = domain_failure();
    let cancellation = std::sync::atomic::AtomicBool::new(false);
    let model = AdmissionModel {
        examined: 0.into(),
        steps: 0.into(),
        cancellation: &cancellation,
    };
    let error = minimize(
        &model,
        &trace,
        target(&trace),
        ShrinkConfig::default(),
        ShrinkLimits::default(),
    )
    .unwrap_err();
    assert!(error.to_string().contains("requires minimize_with_policy"));
    assert_eq!(model.steps.get(), 0);
    assert_eq!(model.examined.get(), 0);
}
#[test]
fn explicit_policy_can_shrink_without_leaving_the_recorded_domain() {
    let policy = InputPolicy::declared("armed-only", |_: &Domain, state, input| {
        Ok(*input == 0 || *state == 1)
    });
    let mut session = DebugSession::recording(
        "redundant",
        Domain,
        policy.clone(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions::default(),
    )
    .unwrap();
    for (revision, input) in [0, 0, 1].into_iter().enumerate() {
        session.step(revision as u64, input).unwrap();
    }
    let trace = session.export_trace().unwrap();
    let report = minimize_with_policy(
        &Domain,
        &trace,
        &policy,
        4,
        target(&trace),
        ShrinkConfig::default(),
        ShrinkLimits::default(),
    )
    .unwrap();
    assert!(report.validated_original);
    assert_eq!(report.minimized.inputs, [0, 1]);
}
#[test]
fn unrestricted_and_legacy_traces_retain_explicit_generate_causality() {
    let mut session = DebugSession::recording(
        "injection",
        Domain,
        InputPolicy::unrestricted(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions::default(),
    )
    .unwrap();
    session.step(0, 0).unwrap();
    session.step(1, 1).unwrap();
    let unrestricted = session.export_trace().unwrap();
    let legacy = stateless::execution::record(&Domain, [0, 1], RunConfig::default(), 2).unwrap();
    for trace in [unrestricted, legacy] {
        let report = minimize(
            &Domain,
            &trace,
            target(&trace),
            ShrinkConfig::default(),
            ShrinkLimits::default(),
        )
        .unwrap();
        assert_eq!(report.minimized.inputs, [1]);
    }
}
#[test]
fn mismatched_ambiguous_and_incomplete_policy_provenance_is_rejected() {
    let trace = domain_failure();
    let wrong = InputPolicy::declared("wrong-label", |_: &Domain, _, _| Ok(true));
    assert!(
        minimize_with_policy(
            &Domain,
            &trace,
            &wrong,
            4,
            target(&trace),
            ShrinkConfig::default(),
            ShrinkLimits::default()
        )
        .unwrap_err()
        .to_string()
        .contains("differs")
    );
    assert!(
        minimize_with_policy(
            &Domain,
            &trace,
            &InputPolicy::unrestricted(),
            4,
            target(&trace),
            ShrinkConfig::default(),
            ShrinkLimits::default()
        )
        .is_err()
    );
    for malformed in [0, 1, 2] {
        let mut trace = trace.clone();
        match malformed {
            0 => trace.config.parameters.push((
                "stateless.debug.environment".into(),
                "enumerated-domain".into(),
            )),
            1 => trace
                .config
                .parameters
                .retain(|(key, _)| key != "stateless.debug.origin"),
            _ => {
                trace
                    .config
                    .parameters
                    .iter_mut()
                    .find(|(key, _)| key == "stateless.debug.origin")
                    .unwrap()
                    .1 = "unknown".into()
            }
        }
        assert!(
            minimize_with_policy(
                &Domain,
                &trace,
                &InputPolicy::enumerated(),
                4,
                target(&trace),
                ShrinkConfig::default(),
                ShrinkLimits::default()
            )
            .is_err()
        );
    }
}

struct AdmissionModel<'a> {
    examined: std::cell::Cell<usize>,
    steps: std::cell::Cell<usize>,
    cancellation: &'a std::sync::atomic::AtomicBool,
}
impl Model for AdmissionModel<'_> {
    type State = u8;
    type Input = u8;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        Domain.metadata()
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Domain.initial_state()
    }
    fn step(&self, state: &u8, input: &u8) -> Result<Transition<u8, ()>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        Domain.step(state, input)
    }
    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        Domain.check_state(state)
    }
}
impl ModelCodec for AdmissionModel<'_> {
    fn encode_state(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
        Domain.encode_state(state)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        Domain.decode_state(bytes)
    }
    fn encode_input(&self, input: &u8) -> Result<Vec<u8>, ModelError> {
        Domain.encode_input(input)
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        Domain.decode_input(bytes)
    }
    fn encode_output(&self, output: &()) -> Result<Vec<u8>, ModelError> {
        Domain.encode_output(output)
    }
}
impl Enumerate for AdmissionModel<'_> {
    fn inputs(&self, _: &u8) -> Result<Vec<u8>, ModelError> {
        panic!("must use lazy enumeration")
    }
    fn input_iter<'a>(
        &'a self,
        _: &'a u8,
    ) -> Result<Box<dyn Iterator<Item = u8> + 'a>, ModelError> {
        Ok(Box::new(std::iter::repeat_with(|| {
            self.examined.set(self.examined.get() + 1);
            99
        })))
    }
}
impl Generate for AdmissionModel<'_> {
    fn generate(&self, state: &u8, rng: &mut Rng) -> Result<Option<u8>, ModelError> {
        Domain.generate(state, rng)
    }
}
#[test]
fn minimization_admission_has_finite_enumeration_work() {
    let trace = domain_failure();
    let cancellation = std::sync::atomic::AtomicBool::new(false);
    let model = AdmissionModel {
        examined: 0.into(),
        steps: 0.into(),
        cancellation: &cancellation,
    };
    let error = minimize_with_policy(
        &model,
        &trace,
        &InputPolicy::enumerated(),
        4,
        target(&trace),
        ShrinkConfig::default(),
        ShrinkLimits::default(),
    )
    .unwrap_err();
    assert!(
        error
            .to_string()
            .contains("admission work budget exhausted")
    );
    assert_eq!(
        model.examined.get(),
        5,
        "four candidates plus one finite overflow lookahead"
    );
    assert_eq!(
        model.steps.get(),
        2,
        "only exact verification executes, admission refuses the candidate"
    );
}
#[test]
fn cancellation_raised_by_policy_prevents_candidate_delivery() {
    use stateless::explore::{RunLimits, ShrinkTermination};
    use std::sync::atomic::{AtomicBool, Ordering};
    let trace = domain_failure();
    let cancellation = AtomicBool::new(false);
    let model = AdmissionModel {
        examined: 0.into(),
        steps: 0.into(),
        cancellation: &cancellation,
    };
    let policy = InputPolicy::declared("enumerated-domain", |model: &AdmissionModel<'_>, _, _| {
        model.cancellation.store(true, Ordering::Relaxed);
        Ok(true)
    });
    let report = minimize_with_policy(
        &model,
        &trace,
        &policy,
        4,
        target(&trace),
        ShrinkConfig::default(),
        ShrinkLimits {
            control: RunLimits {
                cancellation: Some(&cancellation),
                ..Default::default()
            },
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(
        model.steps.get(),
        2,
        "no transition after the policy cancels original candidate validation"
    );
    assert_eq!(report.termination, ShrinkTermination::Cancelled);
    assert!(!report.validated_original);
    assert_eq!(report.replayed_transitions, 2);
}

struct BrokenCapture;
impl Model for BrokenCapture {
    type State = u8;
    type Input = u8;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        Domain.metadata()
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Domain.initial_state()
    }
    fn step(&self, state: &u8, input: &u8) -> Result<Transition<u8, ()>, ModelError> {
        Domain.step(state, input)
    }
    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        Domain.check_state(state)
    }
}
impl ModelCodec for BrokenCapture {
    fn encode_state(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
        if *state == 2 {
            Err(ModelError::new("failed state encoding"))
        } else {
            Domain.encode_state(state)
        }
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        Domain.decode_state(bytes)
    }
    fn encode_input(&self, input: &u8) -> Result<Vec<u8>, ModelError> {
        Domain.encode_input(input)
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        Domain.decode_input(bytes)
    }
    fn encode_output(&self, output: &()) -> Result<Vec<u8>, ModelError> {
        Domain.encode_output(output)
    }
}
#[test]
fn simultaneous_property_capture_and_frontend_failures_preserve_actual_and_prefix() {
    use statelessness_debug::session::{DebugError, Phase, StopReason};
    let mut session = DebugSession::recording(
        "triple-fault",
        BrokenCapture,
        InputPolicy::unrestricted(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions::default(),
    )
    .unwrap();
    session.step(0, 0).unwrap();
    let result = session
        .step_with_observer(1, 1, |observation| {
            let stateless::observation::DebugObservation::Turn(turn) = observation else {
                panic!("expected completed turn")
            };
            assert_eq!(*turn.transition.state, 2);
            assert!(turn.checks[0].is_failure());
            Err(ModelError::new("frontend failed"))
        })
        .unwrap();
    assert!(result.delivered);
    assert_eq!(result.stop, Some(StopReason::PropertyFailure));
    assert_eq!(session.phase(), Phase::Ended);
    assert_eq!(*session.state(), 2);
    assert_eq!(session.revision(), 2);
    assert_eq!(session.diagnostic_errors().len(), 2);
    assert!(session.checks_complete());
    assert_eq!(session.step(2, 0), Err(DebugError::Ended));
    let trace = session.export_trace().unwrap();
    assert_eq!(trace.steps.len(), 1);
    assert!(matches!(
        trace.termination,
        stateless::trace::Termination::ModelError(_)
    ));
    let report =
        stateless::execution::replay(&BrokenCapture, &trace, ReplayOptions::default()).unwrap();
    assert_eq!(report.outcome, ReplayOutcome::Exact);
    assert_eq!(report.steps_verified, 1);
    assert!(
        !report.failure_reproduced,
        "the uncaptured failure is not part of the coherent exact prefix"
    );
}

#[derive(Clone, PartialEq, Eq)]
struct BorrowedValue<'a>(&'a str, std::rc::Rc<()>);
struct BorrowedModel<'a>(&'a str);
impl<'a> Model for BorrowedModel<'a> {
    type State = BorrowedValue<'a>;
    type Input = ();
    type Output = BorrowedValue<'a>;
    fn metadata(&self) -> ModelMetadata {
        Domain.metadata()
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        Ok(BorrowedValue(self.0, std::rc::Rc::new(())))
    }
    fn step(
        &self,
        state: &Self::State,
        _: &(),
    ) -> Result<Transition<Self::State, Self::Output>, ModelError> {
        Ok(Transition::accepted(state.clone(), vec![state.clone()]))
    }
    fn check_state(&self, _: &Self::State) -> Result<Vec<Check>, ModelError> {
        Ok(vec![])
    }
}
#[test]
fn nonstatic_non_debug_non_send_values_remain_source_compatible() {
    let owned = String::from("borrowed application data");
    let mut session = DebugSession::new(
        "borrowed",
        BorrowedModel(&owned),
        InputPolicy::unrestricted(),
        SessionLimits::default(),
    )
    .unwrap();
    assert!(session.step(0, ()).unwrap().delivered);
    assert_eq!(session.state().0, "borrowed application data");
}

#[test]
fn invalid_minimization_attempt_budget_and_target_do_not_execute_model() {
    let trace = domain_failure();
    let cancellation = std::sync::atomic::AtomicBool::new(false);
    for invalid_target in [false, true] {
        let model = AdmissionModel {
            examined: 0.into(),
            steps: 0.into(),
            cancellation: &cancellation,
        };
        let mut target = target(&trace);
        let config = if invalid_target {
            target.check = Check::passed("failure");
            ShrinkConfig::default()
        } else {
            ShrinkConfig { max_attempts: 0 }
        };
        assert!(
            minimize_with_policy(
                &model,
                &trace,
                &InputPolicy::enumerated(),
                4,
                target,
                config,
                ShrinkLimits::default()
            )
            .is_err()
        );
        assert_eq!(
            model.steps.get(),
            0,
            "invalid minimization request must be rejected before verification executes reducers"
        );
    }
}

#[test]
fn initial_failure_minimization_needs_no_transition_budget() {
    let mut trace = domain_failure();
    trace.initial_state = vec![2];
    trace.initial_checks = trace.steps[1].checks.clone();
    trace.steps.clear();
    let report = minimize_with_policy(
        &Domain,
        &trace,
        &InputPolicy::enumerated(),
        4,
        PropertyFailure {
            phase: CheckPhase::InitialState,
            check: trace.initial_checks[0].clone(),
        },
        ShrinkConfig { max_attempts: 1 },
        ShrinkLimits {
            max_replayed_transitions: Some(0),
            ..Default::default()
        },
    )
    .unwrap();
    assert!(report.validated_original);
    assert_eq!(report.replayed_transitions, 0);
    assert!(report.minimized.inputs.is_empty());
}

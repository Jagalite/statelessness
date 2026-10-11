use stateless::demo::{Input, RequestModel};
use stateless::execution::{ReplayOptions, replay};
use stateless::monitor::RecorderOptions;
use stateless::observation::DebugObservation;
use stateless::trace::RunConfig;
use stateless::{Check, Model, ModelError, ModelMetadata, Transition};
use statelessness_debug::session::{
    DebugError, DebugSession, InputPolicy, Phase, SessionLimits, StopReason,
};
use std::cell::Cell;
use std::rc::Rc;

fn fixture() -> DebugSession<RequestModel> {
    DebugSession::recording(
        "test",
        RequestModel::buggy(),
        InputPolicy::enumerated(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions::default(),
    )
    .unwrap()
}
#[test]
fn lifecycle_failure_is_exact_and_cannot_continue() {
    let mut s = fixture();
    s.step(0, Input::Start).unwrap();
    s.step(1, Input::Cancel).unwrap();
    let result = s.step(2, Input::Complete(1)).unwrap();
    assert_eq!(result.sequence, 3);
    assert_eq!(result.stop, Some(StopReason::PropertyFailure));
    assert_eq!(s.phase(), Phase::Ended);
    assert_eq!(s.step(3, Input::Start), Err(DebugError::Ended));
    let trace = s.export_trace().unwrap();
    let report = replay(s.model(), &trace, ReplayOptions::default()).unwrap();
    assert!(report.failure_reproduced);
    assert_eq!(report.steps_verified, 3);
}
#[test]
fn admission_stale_invalid_payload_and_candidates_do_not_deliver() {
    let mut s = fixture();
    assert!(matches!(
        s.step(1, Input::Start),
        Err(DebugError::StaleRevision { .. })
    ));
    assert!(matches!(
        s.step_encoded(0, &[255]),
        Err(DebugError::InvalidCommand(_))
    ));
    assert_eq!(
        s.step(0, Input::Complete(42)),
        Err(DebugError::InputNotPermitted)
    );
    assert_eq!(s.sequence(), 0);
    let page = s.inputs(0, 0, 10).unwrap();
    let old = page.candidates.into_iter().next().unwrap();
    s.step(0, Input::Start).unwrap();
    assert!(matches!(
        s.select(old),
        Err(DebugError::StaleRevision { .. })
    ));
    assert_eq!(s.sequence(), 1);
}
#[test]
fn rejected_inputs_are_deliveries_in_explicit_injection_mode() {
    let mut s = DebugSession::recording(
        "injection",
        RequestModel::fixed(),
        InputPolicy::unrestricted(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions::default(),
    )
    .unwrap();
    assert!(s.step(0, Input::Cancel).unwrap().delivered);
    let trace = s.export_trace().unwrap();
    assert_eq!(trace.steps.len(), 1);
    assert!(matches!(
        trace.steps[0].disposition,
        stateless::Disposition::Rejected(_)
    ));
    assert!(
        trace
            .config
            .parameters
            .iter()
            .any(|(k, v)| k == "stateless.debug.origin" && v == "out-of-domain-injection")
    );
}
#[test]
fn pre_breakpoint_stops_before_delivery_and_bypasses_exact_pending_once() {
    let mut s = fixture();
    let id = s
        .add_pre_breakpoint(|_, input| input == &Input::Start)
        .unwrap();
    let stop = s.step(0, Input::Start).unwrap();
    assert!(!stop.delivered);
    assert_eq!(stop.stop, Some(StopReason::PreBreakpoint(id)));
    assert_eq!(s.revision(), 0);
    assert_eq!(s.export_trace().unwrap().steps.len(), 0);
    assert!(s.step(0, Input::Start).unwrap().delivered);
    assert_eq!(s.sequence(), 1);
    assert!(!s.step(1, Input::Start).unwrap().delivered);
}
#[test]
fn post_breakpoint_observes_completed_turn() {
    let mut s = fixture();
    let id = s
        .add_post_breakpoint(|turn| !turn.transition.outputs.is_empty())
        .unwrap();
    let result = s.step(0, Input::Start).unwrap();
    assert!(result.delivered);
    assert_eq!(result.stop, Some(StopReason::PostBreakpoint(id)));
    assert_eq!(s.sequence(), 1);
}
struct Plain {
    calls: Rc<Cell<u32>>,
    checks: Rc<Cell<u32>>,
    fail_check: bool,
    initial_failure: bool,
}
impl Model for Plain {
    type State = u32;
    type Input = ();
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "plain".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "test".into(),
        }
    }
    fn initial_state(&self) -> Result<u32, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u32, _: &()) -> Result<Transition<u32, ()>, ModelError> {
        self.calls.set(self.calls.get() + 1);
        Ok(Transition::accepted(state + 1, vec![()]))
    }
    fn check_state(&self, state: &u32) -> Result<Vec<Check>, ModelError> {
        self.checks.set(self.checks.get() + 1);
        if self.fail_check && *state > 0 {
            return Err(ModelError::new("intentional checker error"));
        }
        Ok(vec![if self.initial_failure {
            Check::failed("initial", "bad checkpoint")
        } else {
            Check::passed("plain")
        }])
    }
}
fn plain(
    fail_check: bool,
    initial_failure: bool,
) -> (DebugSession<Plain>, Rc<Cell<u32>>, Rc<Cell<u32>>) {
    let calls = Rc::new(Cell::new(0));
    let checks = Rc::new(Cell::new(0));
    let s = DebugSession::new(
        "plain",
        Plain {
            calls: calls.clone(),
            checks: checks.clone(),
            fail_check,
            initial_failure,
        },
        InputPolicy::declared("unit", |_, _, _| Ok(true)),
        SessionLimits::default(),
    )
    .unwrap();
    (s, calls, checks)
}
#[test]
fn codec_debug_and_thread_bounds_are_optional() {
    let (mut s, calls, checks) = plain(false, false);
    assert_eq!(checks.get(), 1);
    let mut observations = 0;
    s.step_with_observer(0, (), |observation| {
        assert!(matches!(observation, DebugObservation::Turn(_)));
        observations += 1;
        Ok(())
    })
    .unwrap();
    assert_eq!(calls.get(), 1);
    assert_eq!(checks.get(), 2);
    assert_eq!(observations, 1);
    assert!(matches!(s.export_trace(), Err(DebugError::Unsupported(_))));
}
#[test]
fn checker_error_retains_actual_state_and_is_not_property_failure() {
    let (mut s, calls, _) = plain(true, false);
    s.step(0, ()).unwrap();
    assert_eq!(calls.get(), 1);
    assert_eq!(*s.state(), 1);
    assert!(!s.checks_complete());
    assert!(matches!(s.stop_reason(), Some(StopReason::CheckerError(_))));
    assert_eq!(s.phase(), Phase::Ended);
}
#[test]
fn initial_failure_is_sequence_zero() {
    let (s, calls, _) = plain(false, true);
    assert_eq!(calls.get(), 0);
    assert_eq!(s.sequence(), 0);
    assert_eq!(s.stop_reason(), Some(&StopReason::PropertyFailure));
    assert!(matches!(s.observation(), DebugObservation::Initial { .. }));
}
#[test]
fn observer_error_cannot_roll_back_or_hide_property_failure() {
    let mut s = fixture();
    s.step(0, Input::Start).unwrap();
    s.step(1, Input::Cancel).unwrap();
    s.step_with_observer(2, Input::Complete(1), |_| {
        Err(ModelError::new("frontend offline"))
    })
    .unwrap();
    assert!(s.state().ready);
    assert_eq!(s.sequence(), 3);
    assert_eq!(s.stop_reason(), Some(&StopReason::PropertyFailure));
    assert_eq!(s.diagnostic_errors().len(), 1);
    assert_eq!(s.export_trace().unwrap().steps.len(), 3);
}
#[test]
fn observer_panic_leaves_controlled_session_ended() {
    let (mut s, calls, _) = plain(false, false);
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        s.step_with_observer(0, (), |_| panic!("diagnostic panic"))
            .unwrap();
    }));
    assert!(result.is_err());
    assert_eq!(calls.get(), 1);
    assert_eq!(*s.state(), 1);
    assert_eq!(s.phase(), Phase::Ended);
}
#[test]
fn bounded_continue_reports_budget_not_success() {
    let (mut s, calls, _) = plain(false, false);
    let results = s.run_bounded(0, std::iter::repeat(()), 3).unwrap();
    assert_eq!(results.len(), 3);
    assert_eq!(calls.get(), 3);
    assert_eq!(s.stop_reason(), Some(&StopReason::BudgetExhausted));
}
#[test]
fn history_eviction_is_visible_and_replayable() {
    let mut s = DebugSession::recording(
        "window",
        RequestModel::fixed(),
        InputPolicy::enumerated(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions {
            max_steps: 1,
            ..Default::default()
        },
    )
    .unwrap();
    s.step(0, Input::Start).unwrap();
    s.step(1, Input::Cancel).unwrap();
    assert_eq!(s.recorder().unwrap().evicted_steps(), 1);
    let report = replay(
        s.model(),
        &s.export_trace().unwrap(),
        ReplayOptions::default(),
    )
    .unwrap();
    assert_eq!(report.steps_verified, 1);
}

#[test]
fn candidate_tokens_do_not_cross_sessions_with_identical_display_ids() {
    let a = fixture();
    let mut b = fixture();
    let token = a.inputs(0, 0, 1).unwrap().candidates.remove(0);
    assert!(matches!(
        b.select(token),
        Err(DebugError::InvalidCommand(_))
    ));
    assert_eq!(b.sequence(), 0);
}
#[test]
fn admission_and_pre_breakpoint_panics_poison_the_controller() {
    let (mut s, _, _) = plain(false, false);
    s.add_pre_breakpoint(|_, _| panic!("pre callback")).unwrap();
    assert!(std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| s.step(0, ()))).is_err());
    assert_eq!(s.phase(), Phase::Ended);
}

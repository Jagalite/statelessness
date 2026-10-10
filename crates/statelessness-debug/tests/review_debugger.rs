//! Independent adversarial review regressions. These exercise public APIs.
use stateless::demo::{Input, RequestModel};
use stateless::monitor::RecorderOptions;
use stateless::trace::RunConfig;
use stateless::{Enumerate, Model, ModelError, ModelMetadata, Transition};
use statelessness_debug::session::{DebugSession, InputPolicy, SessionLimits, StopReason};
use std::cell::Cell;
use std::rc::Rc;

struct MovingDomain {
    calls: Cell<usize>,
    examined: Rc<Cell<usize>>,
}
impl Model for MovingDomain {
    type State = ();
    type Input = u64;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "review-moving-domain".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "review".into(),
        }
    }
    fn initial_state(&self) -> Result<(), ModelError> {
        Ok(())
    }
    fn check_state(&self, _: &()) -> Result<Vec<stateless::Check>, ModelError> {
        Ok(vec![])
    }
    fn step(&self, _: &(), _: &u64) -> Result<Transition<(), ()>, ModelError> {
        Ok(Transition::accepted((), vec![]))
    }
}
impl Enumerate for MovingDomain {
    fn inputs(&self, _: &()) -> Result<Vec<u64>, ModelError> {
        unreachable!("lazy iterator must be used")
    }
    fn input_iter<'a>(
        &'a self,
        _: &'a (),
    ) -> Result<Box<dyn Iterator<Item = u64> + 'a>, ModelError> {
        let call = self.calls.get();
        self.calls.set(call + 1);
        if call == 0 {
            return Ok(Box::new(std::iter::once(42)));
        }
        Ok(Box::new((0..10_000).map(|_| {
            self.examined.set(self.examined.get() + 1);
            0
        })))
    }
}
#[test]
fn candidate_revalidation_respects_session_work_budget() {
    let examined = Rc::new(Cell::new(0));
    let mut session = DebugSession::new(
        "review",
        MovingDomain {
            calls: Cell::new(0),
            examined: examined.clone(),
        },
        InputPolicy::unrestricted(),
        SessionLimits {
            max_candidates: 4,
            ..Default::default()
        },
    )
    .unwrap();
    let token = session.inputs(0, 0, 1).unwrap().candidates.pop().unwrap();
    assert!(session.select(token).is_err());
    assert!(
        examined.get() <= 5,
        "selection examined {} values with a budget of 4",
        examined.get()
    );
    assert_eq!(session.sequence(), 0);
}
#[test]
fn cancel_preserves_terminal_property_failure() {
    let mut session = DebugSession::recording(
        "review",
        RequestModel::buggy(),
        InputPolicy::enumerated(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions::default(),
    )
    .unwrap();
    session.step(0, Input::Start).unwrap();
    session.step(1, Input::Cancel).unwrap();
    session.step(2, Input::Complete(1)).unwrap();
    assert_eq!(session.stop_reason(), Some(&StopReason::PropertyFailure));
    let _ = session.cancel(3);
    assert_eq!(session.stop_reason(), Some(&StopReason::PropertyFailure));
}
#[test]
fn simulation_provenance_cannot_be_shadowed_by_user_config() {
    let config = RunConfig {
        parameters: vec![
            ("stateless.debug.environment".into(), "forged-domain".into()),
            (
                "stateless.debug.origin".into(),
                "recorded-live-observation".into(),
            ),
        ],
        ..Default::default()
    };
    let result = DebugSession::recording(
        "review",
        RequestModel::fixed(),
        InputPolicy::unrestricted(),
        SessionLimits::default(),
        config,
        RecorderOptions::default(),
    );
    if let Ok(session) = result
        && let Ok(trace) = session.export_trace()
    {
        assert_eq!(
            trace
                .config
                .parameters
                .iter()
                .filter(|(key, _)| key == "stateless.debug.environment")
                .count(),
            1,
            "debugger provenance contains duplicate environment keys"
        );
        assert!(
            !trace
                .config
                .parameters
                .iter()
                .any(|(key, value)| key == "stateless.debug.origin"
                    && value == "recorded-live-observation")
        );
    }
}
#[test]
fn diagnostic_permission_entries_consume_configuration_budget() {
    use statelessness_debug::diagnostic::*;
    let mut hub = DiagnosticHub::new(DiagnosticLimits {
        config_bytes: 64,
        ..Default::default()
    });
    let result = hub.add_sink(
        1,
        SinkPermissions {
            sites: vec![String::new(); 100],
            paths: vec![vec![]; 100],
        },
        SinkLimits::default(),
    );
    assert_eq!(
        result,
        Err(DiagnosticError::Capacity),
        "empty entries still retain configuration headers"
    );
}

struct CancelDuringReplay<'a> {
    cancellation: &'a std::sync::atomic::AtomicBool,
    steps: Cell<usize>,
}
impl Model for CancelDuringReplay<'_> {
    type State = stateless::demo::State;
    type Input = Input;
    type Output = stateless::demo::Output;
    fn metadata(&self) -> ModelMetadata {
        RequestModel::buggy().metadata()
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        RequestModel::buggy().initial_state()
    }
    fn step(
        &self,
        state: &Self::State,
        input: &Input,
    ) -> Result<Transition<Self::State, Self::Output>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        self.cancellation
            .store(true, std::sync::atomic::Ordering::Relaxed);
        RequestModel::buggy().step(state, input)
    }
    fn check_state(&self, state: &Self::State) -> Result<Vec<stateless::Check>, ModelError> {
        RequestModel::buggy().check_state(state)
    }
    fn check_transition(
        &self,
        before: &Self::State,
        input: &Input,
        turn: &stateless::TransitionRef<'_, Self::State, Self::Output>,
    ) -> Result<Vec<stateless::Check>, ModelError> {
        RequestModel::buggy().check_transition(before, input, turn)
    }
}
impl stateless::ModelCodec for CancelDuringReplay<'_> {
    fn encode_state(&self, state: &Self::State) -> Result<Vec<u8>, ModelError> {
        RequestModel::buggy().encode_state(state)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<Self::State, ModelError> {
        RequestModel::buggy().decode_state(bytes)
    }
    fn encode_input(&self, input: &Input) -> Result<Vec<u8>, ModelError> {
        RequestModel::buggy().encode_input(input)
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Input, ModelError> {
        RequestModel::buggy().decode_input(bytes)
    }
    fn encode_output(&self, output: &Self::Output) -> Result<Vec<u8>, ModelError> {
        RequestModel::buggy().encode_output(output)
    }
}
impl stateless::Generate for CancelDuringReplay<'_> {
    fn generate(
        &self,
        state: &Self::State,
        rng: &mut stateless::Rng,
    ) -> Result<Option<Input>, ModelError> {
        RequestModel::buggy().generate(state, rng)
    }
}
#[test]
fn minimization_honors_cancellation_raised_during_verification() {
    use stateless::explore::{CheckPhase, PropertyFailure, RunLimits, ShrinkConfig, ShrinkLimits};
    let trace = stateless::execution::record(
        &RequestModel::buggy(),
        [Input::Start, Input::Cancel, Input::Complete(1)],
        RunConfig::default(),
        3,
    )
    .unwrap();
    let check = trace
        .steps
        .last()
        .unwrap()
        .checks
        .iter()
        .find(|check| check.is_failure())
        .unwrap()
        .clone();
    let cancellation = std::sync::atomic::AtomicBool::new(false);
    let model = CancelDuringReplay {
        cancellation: &cancellation,
        steps: Cell::new(0),
    };
    let result = statelessness_debug::workbench::minimize(
        &model,
        &trace,
        PropertyFailure {
            phase: CheckPhase::State,
            check,
        },
        ShrinkConfig::default(),
        ShrinkLimits {
            control: RunLimits {
                cancellation: Some(&cancellation),
                ..Default::default()
            },
            ..Default::default()
        },
    );
    assert_eq!(
        model.steps.get(),
        1,
        "cancellation must stop verification before another delivery"
    );
    assert!(
        result.is_err()
            || result.is_ok_and(
                |report| report.termination == stateless::explore::ShrinkTermination::Cancelled
            )
    );
}

fn threshold_hub() -> statelessness_debug::diagnostic::DiagnosticHub {
    use statelessness_debug::diagnostic::*;
    let mut hub = DiagnosticHub::new(DiagnosticLimits::default());
    hub.add_sink(1, SinkPermissions::local_all(), SinkLimits::default())
        .unwrap();
    hub.register_producer(1, 1).unwrap();
    let ack = hub
        .configure(
            0,
            vec![Subscription {
                sink: 1,
                site: "review".into(),
                kind: SiteKind::Probe,
                path: vec![],
                trigger: Trigger::AboveU128 {
                    threshold: 10,
                    hysteresis: 2,
                    cooldown_turns: 0,
                },
                sample_every: 1,
                minimum_severity: Severity::Debug,
            }],
        )
        .unwrap();
    hub.acknowledge(1, ack.capture_revision, 0).unwrap();
    hub
}
#[test]
fn diagnostic_gap_invalidates_threshold_crossing_baseline() {
    use statelessness_debug::diagnostic::*;
    let mut hub = threshold_hub();
    for turn in [0, 2] {
        let mut scope = hub.begin_turn(1, 1, turn, DiagnosticOrigin::Test).unwrap();
        scope.probe("review", || 11u64);
        scope.finish(true);
    }
    assert!(hub.pop(1).unwrap().baseline);
    let event = hub
        .pop(1)
        .expect("post-gap high value must not be silently suppressed");
    assert_eq!(
        event.baseline_reason,
        Some(ProbeBaselineReason::ObservationGap)
    );
    assert!(event.change_unknown);
    assert_eq!(hub.health(1).unwrap().observation_gaps, 1);
}
struct MaybeInspected(bool);
impl statelessness_debug::inspect::Inspect for MaybeInspected {
    fn inspect(
        &self,
        path: &[statelessness_debug::inspect::PathSegment],
        cx: &mut statelessness_debug::inspect::InspectContext,
    ) -> Result<statelessness_debug::inspect::InspectNode, statelessness_debug::inspect::InspectError>
    {
        if self.0 {
            statelessness_debug::inspect::Inspect::inspect(&11u64, path, cx)
        } else {
            Err(statelessness_debug::inspect::InspectError::PathNotFound)
        }
    }
}
#[test]
fn diagnostic_inspection_failure_invalidates_threshold_crossing_baseline() {
    use statelessness_debug::diagnostic::*;
    let mut hub = threshold_hub();
    for (turn, valid) in [true, false, true].into_iter().enumerate() {
        let mut scope = hub
            .begin_turn(1, 1, turn as u64, DiagnosticOrigin::Test)
            .unwrap();
        scope.probe("review", || MaybeInspected(valid));
        scope.finish(true);
    }
    assert!(hub.pop(1).unwrap().baseline);
    let event = hub
        .pop(1)
        .expect("failed inspection must invalidate threshold comparison");
    assert!(event.baseline);
    assert!(event.change_unknown);
    assert_eq!(hub.health(1).unwrap().inspection_failed, 1);
}

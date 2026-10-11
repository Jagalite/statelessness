//! Independent lifecycle evidence survives debugger observation, replay, and forks.
use stateless::execution::{ReplayOptions, ReplayOutcome};
use stateless::lifecycle::{Event, History, LifecycleMonitor, LifecycleSpec};
use stateless::monitor::RecorderOptions;
use stateless::observation::DebugObservation;
use stateless::oracle::{Oracle, OracleCodec, OracleState, WithOracle};
use stateless::trace::{ReadLimits, RunConfig, Termination};
use stateless::value_codec::{TraceDecode, TraceEncode};
use stateless::{
    Check, CheckSink, CheckStatus, Disposition, EncodeBuffer, Model, ModelCodec, ModelError,
    ModelMetadata, Transition,
};
use statelessness_debug::inspect::{
    FieldView, Inspect, InspectContext, InspectError, InspectNode, InspectQuery, PathSegment,
    inspect,
};
use statelessness_debug::session::{DebugError, Phase, StopReason};
use statelessness_debug::workbench::{TraceViewer, artifact_identity};
use statelessness_debug::{DebugSession, InputPolicy, SessionLimits};
use std::cell::Cell;
use std::rc::Rc;

#[derive(
    Clone,
    Debug,
    PartialEq,
    Eq,
    statelessness_macros::TraceEncode,
    statelessness_macros::TraceDecode,
)]
enum Input {
    #[trace(tag = 0)]
    Start { key: u8, deadline: u64 },
    #[trace(tag = 1)]
    Complete(u8),
    #[trace(tag = 2)]
    Release(u8),
    #[trace(tag = 3)]
    Tick(u64),
}
#[derive(Clone, Debug, PartialEq, Eq, statelessness_macros::TraceEncode)]
enum Output {
    #[trace(tag = 0)]
    Acquired(u8),
    #[trace(tag = 1)]
    Settled(u8),
    #[trace(tag = 2)]
    Released(u8),
}
#[derive(
    Clone,
    Debug,
    Default,
    PartialEq,
    Eq,
    statelessness_macros::Inspect,
    statelessness_macros::TraceEncode,
    statelessness_macros::TraceDecode,
)]
struct State {
    active: Option<u8>,
    resource: Option<u8>,
    deliveries: u64,
}
#[derive(Clone, Default)]
struct Calls {
    reducer: Rc<Cell<usize>>,
    oracle: Rc<Cell<usize>>,
}
impl Calls {
    fn assert_exactly(&self, expected: usize) {
        assert_eq!(self.reducer.get(), expected, "reducer execution count");
        assert_eq!(self.oracle.get(), expected, "oracle advancement count");
    }
}
struct Machine {
    omit_settlement: bool,
    calls: Rc<Cell<usize>>,
}
fn metadata(name: &str, build: &str) -> ModelMetadata {
    ModelMetadata {
        name: name.into(),
        model_version: 1,
        properties_version: 1,
        codec_version: 1,
        build: build.into(),
    }
}
impl Model for Machine {
    type State = State;
    type Input = Input;
    type Output = Output;
    fn metadata(&self) -> ModelMetadata {
        metadata(
            "debugger-lifecycle-fixture",
            if self.omit_settlement {
                "missing-settlement"
            } else {
                "normal"
            },
        )
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        Ok(State::default())
    }
    fn step(&self, before: &State, input: &Input) -> Result<Transition<State, Output>, ModelError> {
        self.calls.set(self.calls.get() + 1);
        let mut state = before.clone();
        state.deliveries += 1;
        let outputs = match *input {
            Input::Start { key, .. } => {
                state.active = Some(key);
                state.resource = Some(key);
                vec![Output::Acquired(key)]
            }
            Input::Complete(key) => {
                state.active = None;
                state.resource = None;
                if self.omit_settlement {
                    vec![]
                } else {
                    vec![Output::Settled(key), Output::Released(key)]
                }
            }
            Input::Release(key) => {
                // Deliberately forget the request too: local consistency cannot
                // prove that the observed release was authorized by its history.
                state.active = None;
                state.resource = None;
                vec![Output::Released(key)]
            }
            Input::Tick(_) => vec![],
        };
        Ok(Transition::accepted(state, outputs))
    }
    fn check_state(&self, state: &State) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if state.active == state.resource {
            Check::passed("application.active_owns_resource")
        } else {
            Check::failed("application.active_owns_resource", "local fields disagree")
        }])
    }
}
impl ModelCodec for Machine {
    fn encode_state(&self, state: &State) -> Result<Vec<u8>, ModelError> {
        state.trace_bytes(1024)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<State, ModelError> {
        State::from_trace(bytes, Default::default())
    }
    fn encode_input(&self, input: &Input) -> Result<Vec<u8>, ModelError> {
        input.trace_bytes(1024)
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Input, ModelError> {
        Input::from_trace(bytes, Default::default())
    }
    fn encode_output(&self, output: &Output) -> Result<Vec<u8>, ModelError> {
        output.trace_bytes(1024)
    }
}
struct Spec;
impl LifecycleSpec<Machine> for Spec {
    type Key = u8;
    fn metadata(&self) -> ModelMetadata {
        metadata("independent-request-lifecycle", "fixture")
    }
    fn input_events(&self, input: &Input, _: &Disposition) -> Result<Vec<Event<u8>>, ModelError> {
        // Requirements come from delivered inputs, never application state or
        // whether the reducer happened to emit the required settlement.
        Ok(match *input {
            Input::Start { key, deadline } => vec![Event::Begin { key, deadline }],
            Input::Tick(now) => vec![Event::Tick(now)],
            Input::Complete(_) | Input::Release(_) => vec![],
        })
    }
    fn output_events(&self, outputs: &[Output]) -> Result<Vec<Event<u8>>, ModelError> {
        Ok(outputs
            .iter()
            .map(|output| match *output {
                Output::Acquired(key) => Event::Acquire(key),
                Output::Settled(key) => Event::Settle(key),
                Output::Released(key) => Event::Release(key),
            })
            .collect())
    }
}
struct CountedOracle {
    lifecycle: LifecycleMonitor<Spec>,
    calls: Rc<Cell<usize>>,
}
impl Oracle<Machine> for CountedOracle {
    type State = History<u8>;
    fn metadata(&self) -> ModelMetadata {
        self.lifecycle.metadata()
    }
    fn initial_state(&self) -> Result<History<u8>, ModelError> {
        self.lifecycle.initial_state()
    }
    fn advance(
        &self,
        before: &History<u8>,
        input: &Input,
        outputs: &[Output],
        disposition: &Disposition,
    ) -> Result<History<u8>, ModelError> {
        self.calls.set(self.calls.get() + 1);
        self.lifecycle.advance(before, input, outputs, disposition)
    }
    fn check_state_into(
        &self,
        history: &History<u8>,
        state: &State,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.lifecycle.check_state_into(history, state, checks)
    }
}
impl OracleCodec<Machine> for CountedOracle {
    fn encode_history(&self, history: &History<u8>) -> Result<Vec<u8>, ModelError> {
        self.lifecycle.encode_history(history)
    }
    fn decode_history(&self, bytes: &[u8]) -> Result<History<u8>, ModelError> {
        self.lifecycle.decode_history(bytes)
    }
    fn encode_history_into(
        &self,
        history: &History<u8>,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        self.lifecycle.encode_history_into(history, out)
    }
}
type Fixture = WithOracle<Machine, CountedOracle>;
type Checkpoint = OracleState<State, History<u8>>;
fn fixture(omit_settlement: bool, calls: &Calls) -> Fixture {
    WithOracle::new(
        Machine {
            omit_settlement,
            calls: calls.reducer.clone(),
        },
        CountedOracle {
            lifecycle: LifecycleMonitor {
                spec: Spec,
                max_history: 32,
            },
            calls: calls.oracle.clone(),
        },
    )
}
fn session(omit_settlement: bool, max_steps: usize) -> (DebugSession<Fixture>, Calls) {
    let calls = Calls::default();
    let session = DebugSession::recording(
        "oracle-lifecycle",
        fixture(omit_settlement, &calls),
        InputPolicy::unrestricted(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions {
            max_steps,
            ..Default::default()
        },
    )
    .unwrap();
    (session, calls)
}
struct Snapshot<'a>(&'a Checkpoint);
impl Inspect for Snapshot<'_> {
    fn inspect(
        &self,
        path: &[PathSegment],
        cx: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        cx.object(
            path,
            "OracleCheckpoint",
            &[
                FieldView::new("application", "Application", &self.0.model),
                FieldView::new("now", "Logical time", &self.0.oracle.now),
                FieldView::new("obligations", "Obligations", &self.0.oracle.obligations),
                FieldView::new("resources", "Resources", &self.0.oracle.resources),
                FieldView::new("violations", "Violations", &self.0.oracle.violations),
            ],
        )
    }
}
fn assert_oracle_failure(session: &DebugSession<Fixture>, details: &str) {
    assert_eq!(session.phase(), Phase::Ended);
    assert_eq!(session.stop_reason(), Some(&StopReason::PropertyFailure));
    let DebugObservation::Turn(turn) = session.observation() else {
        panic!("expected a delivered turn");
    };
    assert!(turn.checks.iter().any(|check| {
        check.id == "application.active_owns_resource" && check.status == CheckStatus::Passed
    }));
    assert!(turn.checks.iter().any(|check| {
        check.id == "lifecycle.safety"
            && matches!(&check.status, CheckStatus::Failed(message) if message.contains(details))
    }));
    assert!(
        turn.checks
            .iter()
            .filter(|check| check.is_failure())
            .all(|check| check.id == "lifecycle.safety")
    );
    assert_eq!(
        session.export_trace().unwrap().termination,
        Termination::PropertyFailed
    );
}
fn assert_exact_replay(session: &DebugSession<Fixture>, failed: bool) {
    let viewer = TraceViewer::new(session.export_trace().unwrap()).unwrap();
    let report = viewer
        .verify(session.model(), ReplayOptions::default())
        .unwrap();
    assert_eq!(report.outcome, ReplayOutcome::Exact);
    assert_eq!(report.steps_verified, viewer.trace().steps.len());
    assert_eq!(report.failure_reproduced, failed);
}

#[test]
fn missing_settlement_is_independently_detected_and_replayed() {
    let (mut session, calls) = session(true, 16);
    session
        .step(
            0,
            Input::Start {
                key: 7,
                deadline: 2,
            },
        )
        .unwrap();
    session.step(1, Input::Complete(7)).unwrap();
    // The reducer claims completion; the oracle still has the obligation.
    assert_eq!(session.state().model.active, None);
    assert_eq!(session.state().oracle.pending(), 1);
    assert!(session.state().oracle.violations.is_empty());
    assert!(session.stop_reason().is_none());
    session.step(2, Input::Tick(2)).unwrap();
    assert_oracle_failure(&session, "required settlement missing at deadline");
    calls.assert_exactly(3);
    assert_eq!(session.step(3, Input::Complete(7)), Err(DebugError::Ended));
    calls.assert_exactly(3);
    assert_exact_replay(&session, true);
    calls.assert_exactly(6);
}

#[test]
fn resource_ownership_faults_fail_even_when_application_checks_pass() {
    for (inputs, reason) in [
        (
            vec![
                Input::Start {
                    key: 7,
                    deadline: 5,
                },
                Input::Release(7),
            ],
            "resource released before settlement",
        ),
        (
            vec![
                Input::Start {
                    key: 7,
                    deadline: 5,
                },
                Input::Complete(7),
                Input::Release(7),
            ],
            "duplicate resource release",
        ),
        (vec![Input::Release(9)], "release without acquisition"),
    ] {
        let (mut session, calls) = session(false, 16);
        let delivered = inputs.len();
        for input in inputs {
            session.step(session.revision(), input).unwrap();
        }
        assert_oracle_failure(&session, reason);
        calls.assert_exactly(delivered);
        assert_exact_replay(&session, true);
        calls.assert_exactly(delivered * 2);
    }
}

#[test]
fn recording_inspection_and_breakpoints_advance_each_machine_exactly_once() {
    let (mut session, calls) = session(false, 16);
    let pre = session
        .add_pre_breakpoint(|state, input| {
            assert!(
                inspect(&Snapshot(state), &InspectQuery::default())
                    .unwrap()
                    .node
                    .is_complete()
            );
            matches!(input, Input::Start { .. })
        })
        .unwrap();
    let post = session
        .add_post_breakpoint(|turn| {
            assert!(
                inspect(&Snapshot(turn.transition.state), &InspectQuery::default())
                    .unwrap()
                    .node
                    .is_complete()
            );
            matches!(turn.input, Input::Start { .. })
        })
        .unwrap();
    let mut observations = 0;
    let mut observer = |observation: DebugObservation<'_, Fixture>| {
        let DebugObservation::Turn(turn) = observation else {
            panic!("expected turn");
        };
        assert!(
            inspect(&Snapshot(turn.transition.state), &InspectQuery::default())
                .unwrap()
                .node
                .is_complete()
        );
        observations += 1;
        Ok(())
    };
    let start = Input::Start {
        key: 7,
        deadline: 5,
    };
    let stopped = session
        .step_with_observer(0, start.clone(), &mut observer)
        .unwrap();
    assert!(!stopped.delivered);
    assert_eq!(stopped.stop, Some(StopReason::PreBreakpoint(pre)));
    assert!(session.export_trace().unwrap().steps.is_empty());
    calls.assert_exactly(0);
    let delivered = session.step_with_observer(0, start, &mut observer).unwrap();
    assert!(delivered.delivered);
    assert_eq!(delivered.stop, Some(StopReason::PostBreakpoint(post)));
    calls.assert_exactly(1);
    session
        .step_with_observer(1, Input::Tick(1), &mut observer)
        .unwrap();
    calls.assert_exactly(2);
    session
        .step_with_observer(2, Input::Complete(7), &mut observer)
        .unwrap();
    calls.assert_exactly(3);
    assert_eq!(observations, 3);
    assert_eq!(session.state().oracle.pending(), 0);
    assert!(session.state().oracle.violations.is_empty());

    let checkpoint = session.state().clone();
    let trace = session.export_trace().unwrap();
    let identity = artifact_identity(&trace).unwrap();
    let mut bytes = vec![];
    trace.write_to(&mut bytes).unwrap();
    let mut viewer = TraceViewer::read(bytes.as_slice(), &ReadLimits::default()).unwrap();
    for sequence in [0, 3, 1, 2, 3] {
        viewer.seek(sequence).unwrap();
        let state = viewer.decode_state(session.model(), sequence).unwrap();
        assert!(
            inspect(&Snapshot(&state), &InspectQuery::default())
                .unwrap()
                .node
                .is_complete()
        );
        assert!(!viewer.checks().iter().any(Check::is_failure));
        assert!(viewer.state_bytes(1024).complete);
        let _ = viewer.outputs(0, 10, 1024).unwrap();
    }
    calls.assert_exactly(3);
    assert_eq!(viewer.decode_state(session.model(), 3).unwrap(), checkpoint);
    let report = viewer
        .verify(session.model(), ReplayOptions::default())
        .unwrap();
    assert_eq!(report.outcome, ReplayOutcome::Exact);
    assert_eq!(report.steps_verified, 3);
    assert!(!report.failure_reproduced);
    calls.assert_exactly(6);
    assert_eq!(session.state(), &checkpoint);
    assert_eq!(viewer.position(), 3);
    assert_eq!(viewer.identity(), &identity);
}

#[test]
fn evicted_composite_checkpoints_preserve_pending_and_completed_branch_history() {
    let (mut parent, calls) = session(false, 2);
    parent
        .step(
            0,
            Input::Start {
                key: 7,
                deadline: 5,
            },
        )
        .unwrap();
    parent.step(1, Input::Tick(1)).unwrap();
    let retained_checkpoint = parent.state().clone();
    parent.step(2, Input::Tick(2)).unwrap();
    let pending_fork = parent.state().clone();
    parent.step(3, Input::Complete(7)).unwrap();
    let completed_fork = parent.state().clone();
    calls.assert_exactly(4);
    assert_eq!(parent.recorder().unwrap().evicted_steps(), 2);

    let trace = parent.export_trace().unwrap();
    let identity = artifact_identity(&trace).unwrap();
    let viewer = TraceViewer::new(trace.clone()).unwrap();
    assert_eq!(viewer.retained_range(), (2, 4));
    assert_eq!(
        viewer.decode_state(parent.model(), 0).unwrap(),
        retained_checkpoint
    );
    assert_eq!(retained_checkpoint.oracle.now, 1);
    assert_eq!(
        retained_checkpoint.oracle.obligations,
        [(7, 5, false, false)]
    );
    assert_eq!(retained_checkpoint.oracle.resources, [(7, true)]);

    for (sequence, expected, input, reason) in [
        (
            1,
            &pending_fork,
            Input::Tick(5),
            "required settlement missing at deadline",
        ),
        (
            2,
            &completed_fork,
            Input::Release(7),
            "duplicate resource release",
        ),
    ] {
        let branch_calls = Calls::default();
        let mut branch = viewer
            .fork_verified(
                "lifecycle-branch",
                fixture(false, &branch_calls),
                sequence,
                ReplayOptions::default(),
                InputPolicy::unrestricted(),
                SessionLimits::default(),
                RecorderOptions::default(),
            )
            .unwrap();
        branch_calls.assert_exactly(sequence);
        assert_eq!(branch.session.state(), expected);
        assert_eq!(branch.session.sequence(), 0);
        assert_eq!(branch.session.revision(), 0);
        assert!(branch.provenance.parent_prefix_verified);
        branch.provenance.validate_parent(&trace).unwrap();
        let initial = branch.session.export_trace().unwrap();
        assert_eq!(
            branch
                .session
                .model()
                .decode_state(&initial.initial_state)
                .unwrap(),
            *expected
        );
        assert_eq!(
            initial.initial_state,
            viewer.snapshot_bytes(sequence).unwrap()
        );
        branch.session.step(0, input).unwrap();
        assert_oracle_failure(&branch.session, reason);
        branch_calls.assert_exactly(sequence + 1);
        assert_exact_replay(&branch.session, true);
        branch_calls.assert_exactly(sequence + 2);
        assert_eq!(artifact_identity(viewer.trace()).unwrap(), identity);
        assert_eq!(parent.state(), &completed_fork);
        calls.assert_exactly(4);
    }
    // Completed tombstones and released ownership are retained, rather than
    // silently replaced with a fresh oracle when creating the second branch.
    assert_eq!(completed_fork.oracle.obligations, [(7, 5, false, true)]);
    assert_eq!(completed_fork.oracle.resources, [(7, false)]);
    assert_exact_replay(&parent, false);
    calls.assert_exactly(6);
}

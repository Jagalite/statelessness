//! Normalized semantic observations must agree across capture and feature builds.
use stateless::demo::{Input, Output, RequestModel, State};
use stateless::monitor::RecorderOptions;
use stateless::trace::{RunConfig, Trace};
use stateless::{Check, Model, ModelCodec, ModelError, ModelMetadata, Transition};
use statelessness_debug::diagnostic::*;
use statelessness_debug::{DebugSession, InputPolicy, SessionLimits};
use std::cell::{Cell, RefCell};

struct Instrumented {
    model: RequestModel,
    hub: RefCell<DiagnosticHub>,
    steps: Cell<u64>,
    checks: Cell<u64>,
    probes: Cell<u64>,
}
impl Model for Instrumented {
    type State = State;
    type Input = Input;
    type Output = Output;
    fn metadata(&self) -> ModelMetadata {
        self.model.metadata()
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        self.model.initial_state()
    }
    fn step(&self, state: &State, input: &Input) -> Result<Transition<State, Output>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        let mut hub = self.hub.borrow_mut();
        let mut scope = hub
            .begin_turn(1, 1, self.steps.get(), DiagnosticOrigin::Test)
            .unwrap();
        let _ = &mut scope; // Both feature variants intentionally share this scope.
        // Both feature builds execute the exact same reducer helper once.
        let result = self.model.step(state, input);
        statelessness_debug::probe!(scope, "turn", || {
            self.probes.set(self.probes.get() + 1);
            self.steps.get()
        });
        scope.finish(result.is_ok());
        result
    }
    fn check_state(&self, state: &State) -> Result<Vec<Check>, ModelError> {
        self.checks.set(self.checks.get() + 1);
        self.model.check_state(state)
    }
    fn check_transition_into(
        &self,
        before: &State,
        input: &Input,
        after: &stateless::TransitionRef<'_, State, Output>,
        checks: &mut stateless::CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.checks.set(self.checks.get() + 1);
        self.model
            .check_transition_into(before, input, after, checks)
    }
}
impl ModelCodec for Instrumented {
    fn encode_state(&self, value: &State) -> Result<Vec<u8>, ModelError> {
        self.model.encode_state(value)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<State, ModelError> {
        self.model.decode_state(bytes)
    }
    fn encode_input(&self, value: &Input) -> Result<Vec<u8>, ModelError> {
        self.model.encode_input(value)
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Input, ModelError> {
        self.model.decode_input(bytes)
    }
    fn encode_output(&self, value: &Output) -> Result<Vec<u8>, ModelError> {
        self.model.encode_output(value)
    }
}
fn run(capture: bool, saturated: bool) -> Trace {
    let mut hub = DiagnosticHub::new(DiagnosticLimits::default());
    hub.add_sink(
        1,
        SinkPermissions::local_all(),
        SinkLimits {
            records: if saturated { 1 } else { 8 },
            ..Default::default()
        },
    )
    .unwrap();
    hub.register_producer(1, 1).unwrap();
    if capture {
        let ack = hub
            .configure(
                0,
                vec![Subscription {
                    sink: 1,
                    site: "turn".into(),
                    kind: SiteKind::Probe,
                    path: vec![],
                    trigger: Trigger::Every,
                    sample_every: 1,
                    minimum_severity: Severity::Debug,
                }],
            )
            .unwrap();
        hub.acknowledge(1, ack.capture_revision, 0).unwrap();
    }
    let model = Instrumented {
        model: RequestModel::buggy(),
        hub: RefCell::new(hub),
        steps: Cell::new(0),
        checks: Cell::new(0),
        probes: Cell::new(0),
    };
    let mut session = DebugSession::recording(
        "parity",
        model,
        InputPolicy::unrestricted(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions::default(),
    )
    .unwrap();
    for input in [Input::Start, Input::Cancel, Input::Complete(1)] {
        session.step(session.revision(), input).unwrap();
    }
    assert_eq!(session.model().steps.get(), 3);
    assert_eq!(session.model().checks.get(), 7);
    let enabled = capture && !cfg!(feature = "compiled-out-diagnostics");
    assert_eq!(session.model().probes.get(), if enabled { 3 } else { 0 });
    assert_eq!(
        session.model().hub.borrow().health(1).unwrap().dropped,
        if enabled && saturated { 2 } else { 0 }
    );
    session.export_trace().unwrap()
}
fn main() {
    let plain = run(false, false);
    for candidate in [run(true, false), run(true, true)] {
        assert_eq!(plain.initial_state, candidate.initial_state);
        assert_eq!(plain.initial_checks, candidate.initial_checks);
        assert_eq!(plain.steps, candidate.steps);
        assert_eq!(plain.termination, candidate.termination);
    }
    // Deliberately exclude build fingerprints and controller-instance identities.
    println!(
        "initial={:?}; checks={:?}; steps={:?}; termination={:?}",
        plain.initial_state, plain.initial_checks, plain.steps, plain.termination
    );
}

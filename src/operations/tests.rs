use super::{EnumerateOps, InputCursor, InputIdentityOps, ModelOps};
use crate::execution::{CheckPolicy, check_observed_count_ops_into};
use crate::explore::{
    RunLimits, SearchConfig, SearchLimits, SearchReport, SearchTermination, enumerate_ops,
};
use crate::model::{Check, CheckSink, CheckStatus, ModelError, Transition, TransitionRef};
use std::cell::{Cell, RefCell};
use std::num::NonZeroU64;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Event {
    Initial,
    StateCheck(usize),
    Open(usize),
    Next(usize, usize),
    Step(usize, usize),
    TransitionCheck(usize, usize),
    Hash(usize),
    Equal,
    Estimate(usize),
}

#[derive(Debug, Default)]
struct Owners {
    states: Cell<usize>,
    inputs: Cell<usize>,
    outputs: Cell<usize>,
    cursors: Cell<usize>,
}

impl Owners {
    fn assert_released(&self) {
        assert_eq!(self.states.get(), 0, "a snapshot owner leaked");
        assert_eq!(self.inputs.get(), 0, "an input owner leaked");
        assert_eq!(self.outputs.get(), 0, "an output owner leaked");
        assert_eq!(self.cursors.get(), 0, "an input cursor leaked");
    }
}

// Deliberately no Clone, Eq or Hash: these stand in for foreign snapshot owners.
struct State {
    value: usize,
    owners: Rc<Owners>,
}

impl Drop for State {
    fn drop(&mut self) {
        self.owners.states.set(self.owners.states.get() - 1);
    }
}

struct Output {
    value: usize,
    owners: Rc<Owners>,
}

impl Drop for Output {
    fn drop(&mut self) {
        self.owners.outputs.set(self.owners.outputs.get() - 1);
    }
}

// Cloning an already owned input is allowed; semantic Eq and Hash are absent.
#[derive(Clone, Debug)]
struct Input(usize, Rc<InputOwner>);

#[derive(Debug)]
struct InputOwner(Rc<Owners>);

impl Drop for InputOwner {
    fn drop(&mut self) {
        self.0.inputs.set(self.0.inputs.get() - 1);
    }
}

struct Fixture {
    edges: Vec<Vec<usize>>,
    owners: Rc<Owners>,
    events: RefCell<Vec<Event>>,
    error_at: Option<Event>,
    cancel_at: Option<Event>,
    cancelled: AtomicBool,
    failing_state: Option<usize>,
    failing_edge: Option<(usize, usize)>,
}

impl Fixture {
    fn new(edges: Vec<Vec<usize>>) -> Self {
        Self {
            edges,
            owners: Rc::default(),
            events: RefCell::default(),
            error_at: None,
            cancel_at: None,
            cancelled: AtomicBool::new(false),
            failing_state: None,
            failing_edge: None,
        }
    }

    fn visit(&self, event: Event) -> Result<(), ModelError> {
        self.events.borrow_mut().push(event);
        if self.cancel_at == Some(event) {
            self.cancelled.store(true, Ordering::Relaxed);
        }
        if self.error_at == Some(event) {
            Err(ModelError::new(format!("injected {event:?}")))
        } else {
            Ok(())
        }
    }

    fn state(&self, value: usize) -> State {
        self.owners.states.set(self.owners.states.get() + 1);
        State {
            value,
            owners: Rc::clone(&self.owners),
        }
    }

    fn output(&self, value: usize) -> Output {
        self.owners.outputs.set(self.owners.outputs.get() + 1);
        Output {
            value,
            owners: Rc::clone(&self.owners),
        }
    }

    fn input(&self, value: usize) -> Input {
        self.owners.inputs.set(self.owners.inputs.get() + 1);
        Input(value, Rc::new(InputOwner(Rc::clone(&self.owners))))
    }

    fn run(&self, config: SearchConfig) -> Result<SearchReport<Input>, ModelError> {
        enumerate_ops(
            self,
            config,
            SearchLimits {
                control: RunLimits {
                    cancellation: Some(&self.cancelled),
                    ..RunLimits::default()
                },
                ..SearchLimits::default()
            },
        )
    }

    fn assert_no_step(&self) {
        assert!(
            self.events
                .borrow()
                .iter()
                .all(|event| !matches!(event, Event::Step(_, _)))
        );
    }
}

impl ModelOps for Fixture {
    type State = State;
    type Input = Input;
    type Output = Output;

    fn initial_state(&self) -> Result<State, ModelError> {
        self.visit(Event::Initial)?;
        Ok(self.state(0))
    }

    fn step(&self, state: &State, input: &Input) -> Result<Transition<State, Output>, ModelError> {
        assert!(Rc::ptr_eq(&input.1.0, &self.owners));
        self.visit(Event::Step(state.value, input.0))?;
        Ok(Transition::accepted(
            self.state(input.0),
            vec![self.output(input.0)],
        ))
    }

    fn check_state_into(
        &self,
        state: &State,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.push(if self.failing_state == Some(state.value) {
            Check::failed("state", "state violation")
        } else {
            Check::passed("state")
        });
        self.visit(Event::StateCheck(state.value))
    }

    fn check_transition_into(
        &self,
        before: &State,
        input: &Input,
        transition: &TransitionRef<'_, State, Output>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        assert_eq!(transition.state.value, input.0);
        assert_eq!(transition.outputs.len(), 1);
        assert_eq!(transition.outputs[0].value, input.0);
        checks.push(if self.failing_edge == Some((before.value, input.0)) {
            Check::failed("edge", "transition violation")
        } else {
            Check::passed("edge")
        });
        self.visit(Event::TransitionCheck(before.value, input.0))
    }

    fn estimated_state_bytes(&self, state: &State) -> Result<Option<usize>, ModelError> {
        self.visit(Event::Estimate(state.value))?;
        Ok(Some(1))
    }
}

struct Cursor<'a> {
    model: &'a Fixture,
    state: &'a State,
    position: usize,
}

impl InputCursor for Cursor<'_> {
    type Input = Input;

    fn next(&mut self) -> Result<Option<Input>, ModelError> {
        self.model
            .visit(Event::Next(self.state.value, self.position))?;
        let next = self.model.edges[self.state.value]
            .get(self.position)
            .copied();
        self.position += 1;
        Ok(next.map(|value| self.model.input(value)))
    }
}

impl Drop for Cursor<'_> {
    fn drop(&mut self) {
        // Cursor teardown still has access to its retained source snapshot.
        assert!(self.state.owners.states.get() > 0);
        let live = &self.model.owners.cursors;
        live.set(live.get() - 1);
    }
}

impl EnumerateOps for Fixture {
    type Cursor<'a> = Cursor<'a>;

    fn input_cursor<'a>(&'a self, state: &'a State) -> Result<Cursor<'a>, ModelError> {
        self.visit(Event::Open(state.value))?;
        self.owners.cursors.set(self.owners.cursors.get() + 1);
        Ok(Cursor {
            model: self,
            state,
            position: 0,
        })
    }

    fn state_equal(&self, left: &State, right: &State) -> Result<bool, ModelError> {
        self.visit(Event::Equal)?;
        Ok(left.value == right.value)
    }

    fn state_hash(&self, state: &State) -> Result<u64, ModelError> {
        self.visit(Event::Hash(state.value))?;
        Ok(0) // Every state collides; only semantic comparison can deduplicate.
    }
}

impl InputIdentityOps for Fixture {
    fn input_equal(&self, left: &Input, right: &Input) -> Result<bool, ModelError> {
        self.visit(Event::Equal)?;
        Ok(left.0 == right.0)
    }
}

#[test]
fn automatic_generation_rolls_back_rng_on_cursor_error_and_releases_values() {
    let mut model = Fixture::new(vec![vec![0, 0]]);
    model.error_at = Some(Event::Next(0, 2));
    let state = model.state(0);
    let mut rng = crate::Rng::new(19);
    let error = crate::automatic::generate_ops(
        &model,
        &state,
        &mut rng,
        crate::automatic::AutoOptions::default(),
    )
    .unwrap_err();
    assert!(error.0.contains("injected"));
    assert_eq!(rng.state(), 19);
    drop(state);
    model.owners.assert_released();
}

#[test]
fn automatic_membership_uses_fallible_input_comparison() {
    let mut model = Fixture::new(vec![vec![0]]);
    model.error_at = Some(Event::Equal);
    let state = model.state(0);
    let input = model.input(0);
    assert!(
        crate::automatic::is_enabled_ops(
            &model,
            &state,
            &input,
            crate::automatic::AutoOptions::default()
        )
        .is_err()
    );
    drop(input);
    drop(state);
    model.owners.assert_released();
}

#[test]
fn foreign_owners_exhaust_a_cyclic_graph_despite_total_hash_collision() {
    let model = Fixture::new(vec![vec![1, 2], vec![0, 3], vec![3], vec![]]);
    let report = model.run(SearchConfig::default()).unwrap();
    assert_eq!(report.termination, SearchTermination::GraphExhausted);
    assert_eq!((report.states, report.transitions), (4, 5));
    assert_eq!(report.estimated_retained_state_bytes, Some(4));
    assert!(report.failure.is_none());
    model.owners.assert_released();
}

#[test]
fn every_fallible_operation_aborts_without_manufacturing_success_or_leaking() {
    for fault in [
        Event::Initial,
        Event::StateCheck(0),
        Event::Estimate(0),
        Event::Hash(0),
        Event::Open(0),
        Event::Next(0, 0),
        Event::Step(0, 1),
        Event::StateCheck(1),
        Event::TransitionCheck(0, 1),
        Event::Hash(1),
        Event::Equal,
        Event::Estimate(1),
        Event::Next(0, 1),
        Event::Open(1),
        Event::Next(1, 0),
    ] {
        let mut model = Fixture::new(vec![vec![1], vec![]]);
        model.error_at = Some(fault);
        let error = model.run(SearchConfig::default()).unwrap_err();
        assert!(error.to_string().contains(&format!("injected {fault:?}")));
        assert_eq!(model.events.borrow().last(), Some(&fault));
        model.owners.assert_released();
    }
}

#[test]
fn cursor_errors_at_depth_and_edge_boundary_probes_are_errors_not_cutoffs() {
    for (fault, max_depth, max_transitions, expected_steps) in [
        (Event::Next(0, 0), 0, 10, 0),
        (Event::Next(0, 0), 10, 0, 0),
        (Event::Next(0, 1), 10, 1, 1),
        (Event::Next(1, 0), 1, 10, 1),
    ] {
        let mut model = Fixture::new(vec![vec![1], vec![]]);
        model.error_at = Some(fault);
        let error = model
            .run(SearchConfig {
                max_depth,
                max_transitions,
                ..SearchConfig::default()
            })
            .unwrap_err();
        assert!(error.to_string().contains("enumerate next"));
        assert_eq!(
            model
                .events
                .borrow()
                .iter()
                .filter(|event| matches!(event, Event::Step(_, _)))
                .count(),
            expected_steps
        );
        model.owners.assert_released();
    }
}

#[test]
fn checks_precede_semantic_identity_and_both_state_admission_caps() {
    for destination in [0, 1] {
        let mut model = Fixture::new(vec![vec![destination], vec![]]);
        model.failing_edge = Some((0, destination));
        model.error_at = Some(if destination == 0 {
            Event::Equal
        } else {
            Event::Hash(1)
        });
        let report = enumerate_ops(
            &model,
            SearchConfig {
                max_states: 1,
                ..SearchConfig::default()
            },
            SearchLimits {
                max_estimated_state_bytes: Some(1),
                ..SearchLimits::default()
            },
        )
        .unwrap();
        assert_eq!(report.termination, SearchTermination::FailureFound);
        let failure = report.failure.unwrap();
        assert_eq!(failure.inputs.len(), 1);
        assert_eq!(failure.inputs[0].0, destination);
        assert_eq!(failure.violations[0].check.id, "edge");
        assert_eq!((report.states, report.transitions), (1, 1));
        drop(failure);
        model.owners.assert_released();
    }
}

#[test]
fn a_later_checker_error_does_not_publish_an_earlier_partial_violation() {
    let mut model = Fixture::new(vec![vec![1], vec![]]);
    model.failing_state = Some(1);
    model.error_at = Some(Event::TransitionCheck(0, 1));
    let error = model.run(SearchConfig::default()).unwrap_err();
    assert!(error.to_string().contains("injected TransitionCheck(0, 1)"));
    model.owners.assert_released();
}

#[test]
fn cancellation_from_callbacks_never_becomes_graph_exhaustion_and_releases_owners() {
    for event in [
        Event::Initial,
        Event::StateCheck(0),
        Event::Estimate(0),
        Event::Hash(0),
        Event::Open(0),
        Event::Next(0, 0),
        Event::Step(0, 1),
        Event::StateCheck(1),
        Event::TransitionCheck(0, 1),
        Event::Hash(1),
        Event::Equal,
        Event::Estimate(1),
        Event::Next(0, 1),
        Event::Open(1),
        Event::Next(1, 0),
    ] {
        let mut model = Fixture::new(vec![vec![1], vec![]]);
        model.cancel_at = Some(event);
        let report = model.run(SearchConfig::default()).unwrap();
        assert_eq!(
            report.termination,
            SearchTermination::Cancelled,
            "{event:?}"
        );
        assert!(report.failure.is_none());
        model.owners.assert_released();
    }
}

#[test]
fn identity_and_estimate_cancellation_prevents_later_callbacks_and_admission() {
    for event in [
        Event::Estimate(0),
        Event::Hash(0),
        Event::Hash(1),
        Event::Equal,
        Event::Estimate(1),
    ] {
        let mut model = Fixture::new(vec![vec![1], vec![]]);
        model.cancel_at = Some(event);
        let report = model.run(SearchConfig::default()).unwrap();
        assert_eq!(report.termination, SearchTermination::Cancelled);
        assert_eq!(model.events.borrow().last(), Some(&event));
        assert_eq!(report.states, 1, "cancelled candidate must not be admitted");
        if matches!(event, Event::Estimate(0) | Event::Hash(0)) {
            assert_eq!(report.transitions, 0);
        } else {
            assert_eq!(report.transitions, 1);
            assert_eq!(report.estimated_retained_state_bytes, Some(1));
        }
        model.owners.assert_released();
    }
}

#[test]
fn callback_error_wins_over_cancellation_from_the_same_callback() {
    for event in [
        Event::Next(0, 0),
        Event::Estimate(0),
        Event::Hash(0),
        Event::Hash(1),
        Event::Equal,
        Event::Estimate(1),
    ] {
        let mut model = Fixture::new(vec![vec![1], vec![]]);
        model.error_at = Some(event);
        model.cancel_at = Some(event);
        let error = model.run(SearchConfig::default()).unwrap_err();
        assert!(error.to_string().contains(&format!("injected {event:?}")));
        assert_eq!(model.events.borrow().last(), Some(&event));
        model.owners.assert_released();
    }
}

#[test]
fn a_started_transition_finishes_checking_and_preserves_failure_on_cancellation() {
    let mut model = Fixture::new(vec![vec![1], vec![]]);
    model.cancel_at = Some(Event::StateCheck(1));
    model.failing_edge = Some((0, 1));
    let report = model.run(SearchConfig::default()).unwrap();
    assert_eq!(report.termination, SearchTermination::FailureFound);
    assert_eq!(report.failure.unwrap().violations[0].check.id, "edge");
    assert_eq!(
        model.events.borrow().last(),
        Some(&Event::TransitionCheck(0, 1))
    );
    model.owners.assert_released();
}

#[test]
fn failure_witness_owns_inputs_after_frontier_cleanup_until_its_last_clone_is_dropped() {
    let mut model = Fixture::new(vec![vec![1, 2], vec![3], vec![3], vec![4], vec![]]);
    model.failing_edge = Some((3, 4));
    let report = model.run(SearchConfig::default()).unwrap();
    assert_eq!(report.termination, SearchTermination::FailureFound);
    let witness = &report.failure.as_ref().unwrap().inputs;
    // Both 1 and 2 reach 3; FIFO exploration must retain the first predecessor.
    assert_eq!(
        witness.iter().map(|input| input.0).collect::<Vec<_>>(),
        [1, 3, 4]
    );
    assert_eq!(model.owners.states.get(), 0);
    assert_eq!(model.owners.outputs.get(), 0);
    assert_eq!(model.owners.cursors.get(), 0);
    assert_eq!(model.owners.inputs.get(), 3);
    let retained = witness.to_vec();
    drop(report);
    assert_eq!(model.owners.inputs.get(), 3);
    assert_eq!(retained[0].0, 1);
    drop(retained);
    model.owners.assert_released();
}

#[test]
fn cutoffs_release_frontier_snapshots_candidates_outputs_and_open_cursors() {
    for (depth, states, transitions, bytes, expected) in [
        (0, 10, 10, None, SearchTermination::DepthBound),
        (10, 1, 10, None, SearchTermination::StateLimit),
        (10, 10, 0, None, SearchTermination::TransitionLimit),
        (
            10,
            10,
            10,
            Some(0),
            SearchTermination::EstimatedStateByteLimit,
        ),
        (
            10,
            10,
            10,
            Some(1),
            SearchTermination::EstimatedStateByteLimit,
        ),
    ] {
        let model = Fixture::new(vec![vec![1], vec![]]);
        let report = enumerate_ops(
            &model,
            SearchConfig {
                max_depth: depth,
                max_states: states,
                max_transitions: transitions,
            },
            SearchLimits {
                max_estimated_state_bytes: bytes,
                ..SearchLimits::default()
            },
        )
        .unwrap();
        assert_eq!(report.termination, expected);
        model.owners.assert_released();
    }
}

#[test]
fn cancellation_before_start_invokes_no_adapter_operation() {
    let model = Fixture::new(vec![vec![]]);
    model.cancelled.store(true, Ordering::Relaxed);
    let report = model.run(SearchConfig::default()).unwrap();
    assert_eq!(report.termination, SearchTermination::Cancelled);
    assert_eq!((report.states, report.transitions), (0, 0));
    assert!(model.events.borrow().is_empty());
    model.owners.assert_released();
}

#[test]
fn observing_foreign_values_borrows_the_existing_transition_without_stepping() {
    let model = Fixture::new(vec![]);
    let before = model.state(4);
    let input = model.input(9);
    let transition = Transition::accepted(model.state(9), vec![model.output(9)]);
    let mut checks = vec![Check::failed("stale", "discard this previous batch")];
    let state_count = check_observed_count_ops_into(
        &model,
        &before,
        &input,
        &transition,
        1,
        CheckPolicy::default(),
        &mut checks,
    )
    .unwrap();
    assert_eq!(state_count, 1);
    assert_eq!(checks, vec![Check::passed("state"), Check::passed("edge")]);
    assert_eq!(
        *model.events.borrow(),
        vec![Event::StateCheck(9), Event::TransitionCheck(4, 9)]
    );
    assert_eq!(
        (model.owners.states.get(), model.owners.outputs.get()),
        (2, 1)
    );
    model.assert_no_step();
    drop(transition);
    drop(before);
    drop(input);
    model.owners.assert_released();
}

#[test]
fn observation_errors_clear_partial_batches_and_preserve_application_values() {
    for fault in [Event::StateCheck(9), Event::TransitionCheck(4, 9)] {
        let mut model = Fixture::new(vec![]);
        model.error_at = Some(fault);
        let before = model.state(4);
        let input = model.input(9);
        let transition = Transition::accepted(model.state(9), vec![model.output(9)]);
        let mut checks = vec![Check::passed("stale")];
        let error = check_observed_count_ops_into(
            &model,
            &before,
            &input,
            &transition,
            1,
            CheckPolicy::default(),
            &mut checks,
        )
        .unwrap_err();
        assert!(error.to_string().contains(&format!("injected {fault:?}")));
        assert!(checks.is_empty());
        assert_eq!((before.value, transition.state.value), (4, 9));
        assert_eq!(transition.outputs[0].value, 9);
        assert_eq!(
            (model.owners.states.get(), model.owners.outputs.get()),
            (2, 1)
        );
        model.assert_no_step();
        drop(transition);
        drop(before);
        drop(input);
        model.owners.assert_released();
    }
}

#[test]
fn observed_sequence_validation_and_skip_policy_require_no_reducer_or_state_identity() {
    let model = Fixture::new(vec![]);
    let before = model.state(4);
    let input = model.input(9);
    let transition = Transition::accepted(model.state(9), vec![model.output(9)]);
    let mut checks = vec![Check::passed("stale")];
    assert!(
        check_observed_count_ops_into(
            &model,
            &before,
            &input,
            &transition,
            0,
            CheckPolicy::default(),
            &mut checks,
        )
        .is_err()
    );
    assert!(checks.is_empty());
    assert!(model.events.borrow().is_empty());
    let state_count = check_observed_count_ops_into(
        &model,
        &before,
        &input,
        &transition,
        1,
        CheckPolicy {
            state_every: NonZeroU64::new(2).unwrap(),
            transition_checks: false,
        },
        &mut checks,
    )
    .unwrap();
    assert_eq!(state_count, 1);
    assert_eq!(checks.len(), 2);
    assert!(
        checks
            .iter()
            .all(|check| matches!(check.status, CheckStatus::Skipped(_)))
    );
    assert!(model.events.borrow().is_empty());
    drop(transition);
    drop(before);
    drop(input);
    model.owners.assert_released();
}

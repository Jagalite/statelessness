use stateless::CheckSink;
use stateless::TransitionRef;
use std::hash::{Hash, Hasher};

use stateless::explore::*;
use stateless::{Check, Enumerate, Generate, Model, ModelError, ModelMetadata, Rng, Transition};

fn metadata() -> ModelMetadata {
    ModelMetadata {
        name: "exploration-tests".into(),
        model_version: 1,
        properties_version: 1,
        codec_version: 1,
        build: "fixture".into(),
    }
}

// All states collide deliberately: a digest alone must never deduplicate them.
#[derive(Clone, Debug, PartialEq, Eq)]
struct Vertex(usize);
impl Hash for Vertex {
    fn hash<H: Hasher>(&self, hasher: &mut H) {
        0_u8.hash(hasher);
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct Edge {
    to: usize,
    bad: bool,
}
fn edge(to: usize) -> Edge {
    Edge { to, bad: false }
}

struct Graph {
    edges: Vec<Vec<Edge>>,
    forbidden: Option<usize>,
    checker_error: bool,
    skipped: bool,
}
impl Graph {
    fn new(edges: Vec<Vec<Edge>>) -> Self {
        Self {
            edges,
            forbidden: None,
            checker_error: false,
            skipped: false,
        }
    }
}
impl Model for Graph {
    type State = Vertex;
    type Input = Edge;
    type Output = bool;
    fn metadata(&self) -> ModelMetadata {
        metadata()
    }
    fn initial_state(&self) -> Result<Vertex, ModelError> {
        Ok(Vertex(0))
    }
    fn step(&self, state: &Vertex, input: &Edge) -> Result<Transition<Vertex, bool>, ModelError> {
        if !self.edges[state.0].contains(input) {
            return Err(ModelError::new("invalid edge"));
        }
        Ok(Transition::accepted(Vertex(input.to), vec![input.bad]))
    }
    fn check_state(&self, state: &Vertex) -> Result<Vec<Check>, ModelError> {
        if self.checker_error {
            return Err(ModelError::new("checker unavailable"));
        }
        let mut checks = vec![if self.forbidden == Some(state.0) {
            Check::failed("allowed-vertex", "forbidden vertex")
        } else {
            Check::passed("allowed-vertex")
        }];
        if self.skipped {
            checks.push(Check::skipped("expensive", "fixture policy"));
        }
        Ok(checks)
    }
    fn check_transition(
        &self,
        _: &Vertex,
        _: &Edge,
        t: &TransitionRef<'_, Vertex, bool>,
    ) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if t.outputs[0] {
            Check::failed("edge-output", "bad output")
        } else {
            Check::passed("edge-output")
        }])
    }
}
impl Enumerate for Graph {
    fn inputs(&self, state: &Vertex) -> Result<Vec<Edge>, ModelError> {
        Ok(self.edges[state.0].clone())
    }
}
impl Generate for Graph {
    fn generate(&self, state: &Vertex, rng: &mut Rng) -> Result<Option<Edge>, ModelError> {
        Ok(rng
            .index(self.edges[state.0].len())
            .map(|index| self.edges[state.0][index].clone()))
    }
    fn is_enabled(&self, state: &Vertex, input: &Edge) -> Result<bool, ModelError> {
        Ok(self.edges[state.0].contains(input))
    }
}

#[test]
fn bfs_exhausts_known_graph_despite_collisions_and_duplicate_paths() {
    let model = Graph::new(vec![
        vec![edge(1), edge(2)],
        vec![edge(3)],
        vec![edge(3)],
        vec![edge(0)],
    ]);
    let report = enumerate(&model, SearchConfig::default()).unwrap();
    assert_eq!(report.termination, SearchTermination::GraphExhausted);
    assert_eq!((report.states, report.transitions), (4, 5));
    assert!(report.failure.is_none());
}

#[test]
fn bfs_checks_transition_to_an_already_visited_state() {
    let model = Graph::new(vec![vec![Edge { to: 0, bad: true }]]);
    let report = enumerate(&model, SearchConfig::default()).unwrap();
    assert_eq!(report.termination, SearchTermination::FailureFound);
    assert_eq!((report.states, report.transitions), (1, 1));
    let failure = report.failure.unwrap();
    assert_eq!(failure.inputs, vec![Edge { to: 0, bad: true }]);
    assert_eq!(failure.violations[0].phase, CheckPhase::Transition);
}

#[test]
fn bfs_checks_a_failing_successor_before_enforcing_state_storage_cap() {
    let mut model = Graph::new(vec![vec![edge(1)], Vec::new()]);
    model.forbidden = Some(1);
    let report = enumerate(
        &model,
        SearchConfig {
            max_states: 1,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(report.termination, SearchTermination::FailureFound);
    assert_eq!(report.states, 1);
    assert_eq!(report.failure.unwrap().inputs, vec![edge(1)]);
    model.forbidden = None;
    let capped = enumerate(
        &model,
        SearchConfig {
            max_states: 1,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(capped.termination, SearchTermination::StateLimit);
    assert_eq!((capped.states, capped.transitions), (1, 1));
}

#[test]
fn initial_failures_precede_transition_budgets_and_need_no_input() {
    let mut model = Graph::new(vec![vec![edge(0)]]);
    model.forbidden = Some(0);
    let report = enumerate(
        &model,
        SearchConfig {
            max_transitions: 0,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(report.termination, SearchTermination::FailureFound);
    assert_eq!(report.transitions, 0);
    let failure = report.failure.unwrap();
    assert!(failure.inputs.is_empty());
    assert_eq!(failure.violations[0].phase, CheckPhase::InitialState);
    let fuzzed = fuzz(
        &model,
        FuzzConfig {
            max_steps: 0,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(fuzzed.termination, FuzzTermination::FailureFound);
    let shrunk = shrink(&model, &failure, ShrinkConfig::default()).unwrap();
    assert_eq!(shrunk.termination, ShrinkTermination::SearchComplete);
    assert_eq!(shrunk.attempts, 1);
}

#[test]
fn graph_completion_depth_cutoffs_and_edge_cutoffs_are_distinct() {
    let model = Graph::new(vec![vec![edge(1)], vec![edge(0)]]);
    let depth = enumerate(
        &model,
        SearchConfig {
            max_depth: 1,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(depth.termination, SearchTermination::DepthBound);
    assert_eq!(depth.transitions, 1);
    let budget = enumerate(
        &model,
        SearchConfig {
            max_transitions: 1,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(budget.termination, SearchTermination::TransitionLimit);
    let exact = enumerate(
        &model,
        SearchConfig {
            max_transitions: 2,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(exact.termination, SearchTermination::GraphExhausted);
    let terminal = Graph::new(vec![Vec::new()]);
    let zero = enumerate(
        &terminal,
        SearchConfig {
            max_transitions: 0,
            max_depth: 0,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(zero.termination, SearchTermination::GraphExhausted);
    assert_eq!(zero.transitions, 0);
    assert!(
        enumerate(
            &model,
            SearchConfig {
                max_states: 0,
                ..Default::default()
            }
        )
        .is_err()
    );
}

#[test]
fn bfs_returns_a_shortest_counterexample() {
    let mut model = Graph::new(vec![
        vec![edge(1), edge(2)],
        vec![edge(3)],
        vec![edge(4)],
        vec![edge(4)],
        Vec::new(),
    ]);
    model.forbidden = Some(4);
    let report = enumerate(&model, SearchConfig::default()).unwrap();
    assert_eq!(report.failure.unwrap().inputs, vec![edge(2), edge(4)]);
}

#[test]
fn skipped_checks_are_counted_and_checker_errors_are_not_violations() {
    let mut model = Graph::new(vec![vec![edge(1)], vec![edge(0)]]);
    model.skipped = true;
    let report = enumerate(&model, SearchConfig::default()).unwrap();
    assert_eq!(report.skipped_checks, 3); // initial state plus every executed edge
    model.checker_error = true;
    let error = enumerate(&model, SearchConfig::default()).unwrap_err();
    assert!(error.0.contains("initial check_state: checker unavailable"));
    assert!(fuzz(&model, FuzzConfig::default()).is_err());
}

#[test]
fn fuzz_is_seeded_and_reuses_only_causally_enabled_inputs() {
    let mut model = Graph::new(vec![
        vec![edge(1), edge(2)],
        vec![edge(0)],
        vec![edge(3)],
        Vec::new(),
    ]);
    model.forbidden = Some(3);
    let config = FuzzConfig {
        seed: 812,
        mutation_percent: 100,
        cases: 100,
        max_steps: 3,
        ..Default::default()
    };
    let one = fuzz(&model, config.clone()).unwrap();
    let two = fuzz(&model, config).unwrap();
    assert_eq!(one.termination, FuzzTermination::FailureFound);
    assert_eq!(one.failure, two.failure);
    assert_eq!(one.transitions, two.transitions);
    // This graph's alternating enabled inputs force mutated stale inputs to be regenerated.
    let legal = Graph::new(vec![vec![edge(1)], vec![edge(0)]]);
    let report = fuzz(
        &legal,
        FuzzConfig {
            seed: 91,
            mutation_percent: 100,
            cases: 100,
            max_steps: 20,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(report.termination, FuzzTermination::CasesCompleted);
    assert_eq!(report.transitions, 2_000);
}

#[test]
fn fuzz_reports_transition_budget_and_does_not_claim_exhaustiveness() {
    let model = Graph::new(vec![vec![edge(0)]]);
    let report = fuzz(
        &model,
        FuzzConfig {
            max_transitions: 7,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(report.termination, FuzzTermination::TransitionLimit);
    assert_eq!(report.transitions, 7);
    let empty = fuzz(
        &model,
        FuzzConfig {
            cases: 0,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(empty.cases, 0);
    assert_eq!(empty.transitions, 0);
    assert!(
        fuzz(
            &model,
            FuzzConfig {
                mutation_percent: 101,
                ..Default::default()
            }
        )
        .is_err()
    );
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum Action {
    Idle,
    Start(u8),
    Complete,
    OtherFailure,
    OtherPhase,
}
#[derive(Clone, Debug, PartialEq, Eq)]
struct Task {
    started: bool,
    failure: u8,
}
struct Causal;
impl Model for Causal {
    type State = Task;
    type Input = Action;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        metadata()
    }
    fn initial_state(&self) -> Result<Task, ModelError> {
        Ok(Task {
            started: false,
            failure: 0,
        })
    }
    fn step(&self, state: &Task, input: &Action) -> Result<Transition<Task, ()>, ModelError> {
        assert!(self.is_enabled(state, input)?);
        let mut next = state.clone();
        match input {
            Action::Idle => {}
            Action::Start(_) => next.started = true,
            Action::Complete => next.failure = 1,
            Action::OtherFailure => next.failure = 2,
            Action::OtherPhase => next.failure = 3,
        }
        Ok(Transition::accepted(next, Vec::new()))
    }
    fn check_state(&self, state: &Task) -> Result<Vec<Check>, ModelError> {
        Ok(match state.failure {
            1 => vec![Check::failed("target", "completion bug")],
            2 => vec![Check::failed("different", "unrelated bug")],
            _ => vec![Check::passed("target")],
        })
    }
    fn check_transition(
        &self,
        _: &Task,
        _: &Action,
        transition: &TransitionRef<'_, Task, ()>,
    ) -> Result<Vec<Check>, ModelError> {
        Ok(if transition.state.failure == 3 {
            vec![Check::failed(
                "target",
                "same ID but a different property phase",
            )]
        } else {
            Vec::new()
        })
    }
}
impl Generate for Causal {
    fn generate(&self, state: &Task, _: &mut Rng) -> Result<Option<Action>, ModelError> {
        Ok(Some(if state.started {
            Action::Complete
        } else {
            Action::Start(9)
        }))
    }
    fn is_enabled(&self, state: &Task, input: &Action) -> Result<bool, ModelError> {
        Ok(!matches!(input, Action::Complete) || state.started)
    }
    fn simpler_inputs(&self, input: &Action) -> Vec<Action> {
        match input {
            Action::Start(value) if *value != 0 => vec![Action::Start(0)],
            Action::Complete => vec![Action::OtherFailure, Action::OtherPhase],
            _ => Vec::new(),
        }
    }
}
fn causal_failure() -> Failure<Action> {
    Failure {
        inputs: vec![
            Action::Idle,
            Action::Start(9),
            Action::Idle,
            Action::Complete,
        ],
        violations: vec![PropertyFailure {
            phase: CheckPhase::State,
            check: Check::failed("target", "completion bug"),
        }],
    }
}

#[test]
fn shrinking_preserves_causality_failure_identity_and_original_evidence() {
    let original = causal_failure();
    let result = shrink(&Causal, &original, ShrinkConfig::default()).unwrap();
    assert_eq!(result.original, original);
    assert_eq!(
        result.minimized.inputs,
        vec![Action::Start(0), Action::Complete]
    );
    assert_eq!(result.termination, ShrinkTermination::SearchComplete);
    assert!(
        result
            .history
            .iter()
            .any(|a| a.outcome == ShrinkOutcome::InvalidCausality)
    );
    assert!(
        result
            .history
            .iter()
            .any(|a| a.outcome == ShrinkOutcome::DifferentFailure)
    );
    assert_eq!(result.history.len(), result.attempts);
}

#[test]
fn shrinking_honors_replay_budget_and_rejects_wrong_or_disabled_originals() {
    let original = causal_failure();
    let limited = shrink(&Causal, &original, ShrinkConfig { max_attempts: 1 }).unwrap();
    assert_eq!(limited.termination, ShrinkTermination::AttemptLimit);
    assert_eq!(limited.attempts, 1);
    assert_eq!(limited.original, original);
    assert_eq!(limited.minimized, original);
    let mut wrong = original.clone();
    wrong.violations[0].phase = CheckPhase::Transition;
    assert!(shrink(&Causal, &wrong, ShrinkConfig::default()).is_err());
    wrong = original;
    wrong.inputs = vec![Action::Complete];
    assert!(shrink(&Causal, &wrong, ShrinkConfig::default()).is_err());
    assert!(shrink(&Causal, &wrong, ShrinkConfig { max_attempts: 0 }).is_err());
}

struct Cyclic;
impl Model for Cyclic {
    type State = u8;
    type Input = u8;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        metadata()
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, _: &u8, input: &u8) -> Result<Transition<u8, ()>, ModelError> {
        Ok(Transition::accepted(*input, Vec::new()))
    }
    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(if *state == 0 {
            vec![]
        } else {
            vec![Check::failed("nonzero", "nonzero")]
        })
    }
}
impl Generate for Cyclic {
    fn generate(&self, _: &u8, _: &mut Rng) -> Result<Option<u8>, ModelError> {
        Ok(Some(1))
    }
    fn simpler_inputs(&self, input: &u8) -> Vec<u8> {
        vec![if *input == 1 { 2 } else { 1 }]
    }
}

#[test]
fn cyclic_simplification_hints_terminate_without_a_budget_cutoff() {
    let failure = fuzz(&Cyclic, FuzzConfig::default())
        .unwrap()
        .failure
        .unwrap();
    let report = shrink(&Cyclic, &failure, ShrinkConfig { max_attempts: 20 }).unwrap();
    assert_eq!(report.termination, ShrinkTermination::SearchComplete);
    assert!(report.attempts < 20);
    assert_eq!(report.minimized.inputs.len(), 1);
}

struct DisabledGenerator;
impl Model for DisabledGenerator {
    type State = ();
    type Input = ();
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        metadata()
    }
    fn initial_state(&self) -> Result<(), ModelError> {
        Ok(())
    }
    fn step(&self, _: &(), _: &()) -> Result<Transition<(), ()>, ModelError> {
        panic!("a disabled generated input must never be stepped")
    }
    fn check_state(&self, _: &()) -> Result<Vec<Check>, ModelError> {
        Ok(Vec::new())
    }
}
impl Generate for DisabledGenerator {
    fn generate(&self, _: &(), _: &mut Rng) -> Result<Option<()>, ModelError> {
        Ok(Some(()))
    }
    fn is_enabled(&self, _: &(), _: &()) -> Result<bool, ModelError> {
        Ok(false)
    }
}

#[test]
fn disabled_generated_inputs_are_adapter_errors_not_findings() {
    let error = fuzz(&DisabledGenerator, FuzzConfig::default()).unwrap_err();
    assert!(error.0.contains("causally disabled"));
}

// Instrumentation never affects modeled outcomes or state identity.
#[derive(Clone, Debug)]
struct CountedState {
    value: usize,
    hashes: std::rc::Rc<std::cell::Cell<usize>>,
}
impl PartialEq for CountedState {
    fn eq(&self, other: &Self) -> bool {
        self.value == other.value
    }
}
impl Eq for CountedState {}
impl Hash for CountedState {
    fn hash<H: Hasher>(&self, hasher: &mut H) {
        self.hashes.set(self.hashes.get() + 1);
        self.value.hash(hasher);
    }
}

struct Instrumented {
    hashes: std::rc::Rc<std::cell::Cell<usize>>,
    yielded: std::cell::Cell<usize>,
    enabled: std::cell::Cell<usize>,
    generated: std::cell::Cell<usize>,
    buffers: std::cell::RefCell<Vec<usize>>,
    cancel: std::sync::atomic::AtomicBool,
    cancel_on_step: bool,
    fail_after: Option<usize>,
    estimate: Option<usize>,
    terminal: usize,
    fanout: usize,
}
impl Default for Instrumented {
    fn default() -> Self {
        Self {
            hashes: Default::default(),
            yielded: Default::default(),
            enabled: Default::default(),
            generated: Default::default(),
            buffers: Default::default(),
            cancel: false.into(),
            cancel_on_step: false,
            fail_after: None,
            estimate: Some(64),
            terminal: 4,
            fanout: 1,
        }
    }
}
impl Model for Instrumented {
    type State = CountedState;
    type Input = usize;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        metadata()
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        Ok(CountedState {
            value: 0,
            hashes: self.hashes.clone(),
        })
    }
    fn step(
        &self,
        state: &Self::State,
        input: &usize,
    ) -> Result<Transition<Self::State, ()>, ModelError> {
        if self.cancel_on_step {
            self.cancel
                .store(true, std::sync::atomic::Ordering::Relaxed);
        }
        Ok(Transition::accepted(
            CountedState {
                value: state.value + input,
                hashes: state.hashes.clone(),
            },
            vec![],
        ))
    }
    fn check_state(&self, _: &Self::State) -> Result<Vec<Check>, ModelError> {
        panic!("search should use the reusable result hook")
    }
    fn check_state_into(
        &self,
        state: &Self::State,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.push(
            if self.fail_after.is_some_and(|limit| state.value >= limit) {
                Check::failed("counted.limit", "limit reached")
            } else {
                Check::passed("counted.limit")
            },
        );
        self.buffers
            .borrow_mut()
            .push(checks.as_slice().as_ptr() as usize);
        Ok(())
    }
    fn check_transition_into(
        &self,
        _: &Self::State,
        _: &usize,
        _: &TransitionRef<'_, Self::State, ()>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.push(Check::passed("counted.edge"));
        Ok(())
    }
    fn estimated_state_bytes(&self, _: &Self::State) -> Option<usize> {
        self.estimate
    }
}
impl Enumerate for Instrumented {
    fn inputs(&self, _: &Self::State) -> Result<Vec<usize>, ModelError> {
        panic!("search should use the incremental iterator")
    }
    fn input_iter<'a>(
        &'a self,
        state: &'a Self::State,
    ) -> Result<Box<dyn Iterator<Item = usize> + 'a>, ModelError> {
        let count = if state.value < self.terminal {
            self.fanout
        } else {
            0
        };
        Ok(Box::new(
            std::iter::repeat_n(1, count).inspect(|_| self.yielded.set(self.yielded.get() + 1)),
        ))
    }
}
impl Generate for Instrumented {
    fn generate(&self, _: &Self::State, _: &mut Rng) -> Result<Option<usize>, ModelError> {
        self.generated.set(self.generated.get() + 1);
        Ok(Some(1))
    }
    fn is_enabled(&self, _: &Self::State, _: &usize) -> Result<bool, ModelError> {
        self.enabled.set(self.enabled.get() + 1);
        Ok(true)
    }
}

#[test]
fn lazy_depth_boundary_probes_one_input_without_materializing_the_domain() {
    let model = Instrumented {
        fanout: usize::MAX,
        ..Default::default()
    };
    let report = enumerate(
        &model,
        SearchConfig {
            max_depth: 0,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(report.termination, SearchTermination::DepthBound);
    assert_eq!(model.yielded.get(), 1);
    assert_eq!(report.transitions, 0);
}

#[test]
fn enumeration_hashes_each_candidate_once_and_reuses_check_storage() {
    let model = Instrumented::default();
    let report = enumerate(&model, SearchConfig::default()).unwrap();
    assert_eq!(report.termination, SearchTermination::GraphExhausted);
    assert_eq!(report.states, 5);
    assert_eq!(model.hashes.get(), 1 + report.transitions as usize);
    let buffers = model.buffers.borrow();
    assert_eq!(buffers.len(), 5);
    assert!(buffers.iter().all(|address| *address == buffers[0]));
    assert_eq!(report.estimated_retained_state_bytes, Some(5 * 64));
}

#[test]
fn state_byte_admission_reports_estimates_and_preserves_failures() {
    let mut model = Instrumented::default();
    let limits = SearchLimits {
        max_estimated_state_bytes: Some(128),
        ..Default::default()
    };
    let report = enumerate_with_limits(&model, SearchConfig::default(), limits).unwrap();
    assert_eq!(
        report.termination,
        SearchTermination::EstimatedStateByteLimit
    );
    assert_eq!((report.states, report.transitions), (2, 2));
    assert_eq!(report.estimated_retained_state_bytes, Some(128));
    model.fail_after = Some(2);
    let failure = enumerate_with_limits(&model, SearchConfig::default(), limits).unwrap();
    assert_eq!(failure.termination, SearchTermination::FailureFound);
    assert_eq!(failure.failure.unwrap().inputs, vec![1, 1]);
    assert_eq!(failure.estimated_retained_state_bytes, Some(128));
    model.fail_after = None;
    let oversized_initial = enumerate_with_limits(
        &model,
        SearchConfig::default(),
        SearchLimits {
            max_estimated_state_bytes: Some(63),
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(
        oversized_initial.termination,
        SearchTermination::EstimatedStateByteLimit
    );
    assert_eq!(oversized_initial.states, 0);
    assert_eq!(oversized_initial.estimated_retained_state_bytes, Some(0));
    model.estimate = None;
    assert!(
        enumerate_with_limits(&model, SearchConfig::default(), limits)
            .unwrap_err()
            .0
            .contains("estimated_state_bytes")
    );
    assert_eq!(
        enumerate(&model, SearchConfig::default())
            .unwrap()
            .estimated_retained_state_bytes,
        None
    );
}

#[test]
fn cooperative_stops_preserve_a_failure_on_the_just_executed_transition() {
    let model = Instrumented {
        cancel_on_step: true,
        fail_after: Some(1),
        ..Default::default()
    };
    let control = RunLimits {
        cancellation: Some(&model.cancel),
        ..Default::default()
    };
    let report = enumerate_with_limits(
        &model,
        SearchConfig::default(),
        SearchLimits {
            control,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(report.termination, SearchTermination::FailureFound);
    assert_eq!(report.transitions, 1);
    model
        .cancel
        .store(false, std::sync::atomic::Ordering::Relaxed);
    let fuzzed = fuzz_with_limits(&model, FuzzConfig::default(), control).unwrap();
    assert_eq!(fuzzed.termination, FuzzTermination::FailureFound);
    assert_eq!(fuzzed.transitions, 1);
}

#[test]
fn cancellation_and_deadline_have_distinct_search_results() {
    let model = Instrumented {
        cancel_on_step: true,
        ..Default::default()
    };
    let control = RunLimits {
        cancellation: Some(&model.cancel),
        ..Default::default()
    };
    let cancelled = enumerate_with_limits(
        &model,
        SearchConfig::default(),
        SearchLimits {
            control,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(cancelled.termination, SearchTermination::Cancelled);
    assert_eq!(cancelled.transitions, 1);
    model
        .cancel
        .store(false, std::sync::atomic::Ordering::Relaxed);
    let fuzzed = fuzz_with_limits(&model, FuzzConfig::default(), control).unwrap();
    assert_eq!(fuzzed.termination, FuzzTermination::Cancelled);
    assert_eq!(fuzzed.transitions, 1);
    let deadline = RunLimits {
        max_duration: Some(std::time::Duration::ZERO),
        cancellation: None,
    };
    let expired = enumerate_with_limits(
        &model,
        SearchConfig::default(),
        SearchLimits {
            control: deadline,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(expired.termination, SearchTermination::Deadline);
    assert_eq!(expired.transitions, 0);
    let expired_fuzz = fuzz_with_limits(&model, FuzzConfig::default(), deadline).unwrap();
    assert_eq!(expired_fuzz.termination, FuzzTermination::Deadline);
    assert_eq!(expired_fuzz.transitions, 0);
}

#[test]
fn fuzz_reused_inputs_are_validated_once_and_exhausted_budget_skips_generation() {
    let model = Instrumented::default();
    let config = FuzzConfig {
        cases: 20,
        max_steps: 10,
        mutation_percent: 100,
        ..Default::default()
    };
    let report = fuzz(&model, config).unwrap();
    assert_eq!(report.transitions, 200);
    assert_eq!(model.enabled.get(), 200);
    let fresh = Instrumented::default();
    let exhausted = fuzz(
        &fresh,
        FuzzConfig {
            max_transitions: 0,
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(exhausted.termination, FuzzTermination::TransitionLimit);
    assert_eq!(fresh.generated.get(), 0);
    assert_eq!(fresh.enabled.get(), 0);
}

#[test]
fn shrink_budget_counts_validation_and_preserves_unvalidated_original_evidence() {
    let original = causal_failure();
    let report = shrink_with_limits(
        &Causal,
        &original,
        ShrinkConfig::default(),
        ShrinkLimits {
            max_replayed_transitions: Some(2),
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(report.termination, ShrinkTermination::TransitionLimit);
    assert_eq!(report.replayed_transitions, 2);
    assert!(!report.validated_original);
    assert_eq!(report.original, original);
    assert_eq!(report.minimized, original);
    assert_eq!(report.history[0].outcome, ShrinkOutcome::ResourceLimit);
    let validated = shrink_with_limits(
        &Causal,
        &original,
        ShrinkConfig::default(),
        ShrinkLimits {
            max_replayed_transitions: Some(6),
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(validated.termination, ShrinkTermination::TransitionLimit);
    assert_eq!(validated.replayed_transitions, 6);
    assert!(validated.validated_original);
    assert_eq!(validated.original, original);
    assert_eq!(validated.minimized.violations, original.violations);
}

#[test]
fn shrinking_reports_cooperative_deadline_without_claiming_original_validation() {
    let original = causal_failure();
    let expired = shrink_with_limits(
        &Causal,
        &original,
        ShrinkConfig::default(),
        ShrinkLimits {
            control: RunLimits {
                max_duration: Some(std::time::Duration::ZERO),
                cancellation: None,
            },
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(expired.termination, ShrinkTermination::Deadline);
    assert!(!expired.validated_original);
    assert_eq!(expired.replayed_transitions, 0);
    assert_eq!(expired.original, original);
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum StopAt {
    Never,
    InputEnd,
    InputYield,
    GenerateEnd,
    Enable,
    InitialCheck,
    Simplification,
}

struct CancellingCallbacks {
    inner: Instrumented,
    point: StopAt,
    calls: std::cell::Cell<usize>,
}
impl CancellingCallbacks {
    fn new(point: StopAt) -> Self {
        Self {
            inner: Instrumented::default(),
            point,
            calls: std::cell::Cell::new(0),
        }
    }
    fn call(&self) {
        self.calls.set(self.calls.get() + 1);
    }
    fn cancel(&self) {
        self.inner
            .cancel
            .store(true, std::sync::atomic::Ordering::Relaxed);
    }
    fn control(&self) -> RunLimits<'_> {
        RunLimits {
            cancellation: Some(&self.inner.cancel),
            ..Default::default()
        }
    }
}
impl Model for CancellingCallbacks {
    type State = CountedState;
    type Input = usize;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        metadata()
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        self.call();
        self.inner.initial_state()
    }
    fn step(
        &self,
        state: &Self::State,
        input: &usize,
    ) -> Result<Transition<Self::State, ()>, ModelError> {
        self.call();
        self.inner.step(state, input)
    }
    fn check_state(&self, _: &Self::State) -> Result<Vec<Check>, ModelError> {
        unreachable!("search uses append-style checking")
    }
    fn check_state_into(
        &self,
        state: &Self::State,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.call();
        self.inner.check_state_into(state, checks)?;
        if self.point == StopAt::InitialCheck && state.value == 0 {
            self.cancel();
        }
        Ok(())
    }
}
impl Enumerate for CancellingCallbacks {
    fn inputs(&self, _: &Self::State) -> Result<Vec<usize>, ModelError> {
        unreachable!("search uses lazy inputs")
    }
    fn input_iter<'a>(
        &'a self,
        state: &'a Self::State,
    ) -> Result<Box<dyn Iterator<Item = usize> + 'a>, ModelError> {
        self.call();
        if matches!(self.point, StopAt::InputEnd | StopAt::InputYield) {
            return Ok(Box::new(std::iter::from_fn(|| {
                self.call();
                self.cancel();
                (self.point == StopAt::InputYield).then_some(1)
            })));
        }
        self.inner.input_iter(state)
    }
}
impl Generate for CancellingCallbacks {
    fn generate(&self, _: &Self::State, _: &mut Rng) -> Result<Option<usize>, ModelError> {
        self.call();
        if self.point == StopAt::GenerateEnd {
            self.cancel();
            Ok(None)
        } else {
            Ok(Some(1))
        }
    }
    fn is_enabled(&self, _: &Self::State, _: &usize) -> Result<bool, ModelError> {
        self.call();
        if self.point == StopAt::Enable {
            self.cancel();
            Ok(false)
        } else {
            Ok(true)
        }
    }
    fn simpler_inputs(&self, _: &usize) -> Vec<usize> {
        self.call();
        if self.point == StopAt::Simplification {
            self.cancel();
        }
        vec![]
    }
}

#[test]
fn pre_cancelled_enumeration_invokes_no_model_callbacks() {
    let model = CancellingCallbacks::new(StopAt::Never);
    model.cancel();
    let report = enumerate_with_limits(
        &model,
        SearchConfig::default(),
        SearchLimits {
            control: model.control(),
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(report.termination, SearchTermination::Cancelled);
    assert_eq!(report.states, 0);
    assert_eq!(report.transitions, 0);
    assert_eq!(model.calls.get(), 0);
}

#[test]
fn terminal_iterator_cancellation_cannot_claim_graph_exhaustion_or_depth_completion() {
    for point in [StopAt::InputEnd, StopAt::InputYield] {
        for depth in [0, 3] {
            let model = CancellingCallbacks::new(point);
            let report = enumerate_with_limits(
                &model,
                SearchConfig {
                    max_depth: depth,
                    ..Default::default()
                },
                SearchLimits {
                    control: model.control(),
                    ..Default::default()
                },
            )
            .unwrap();
            assert_eq!(report.termination, SearchTermination::Cancelled);
            assert_eq!(report.transitions, 0);
            assert_eq!(model.calls.get(), 4); // initial state/check, iterator, next
        }
    }
}

#[test]
fn zero_case_fuzz_still_observes_pre_start_controls_without_model_callbacks() {
    let model = CancellingCallbacks::new(StopAt::InitialCheck);
    model.cancel();
    for (control, termination) in [
        (model.control(), FuzzTermination::Cancelled),
        (
            RunLimits {
                max_duration: Some(std::time::Duration::ZERO),
                cancellation: None,
            },
            FuzzTermination::Deadline,
        ),
    ] {
        let report = fuzz_with_limits(
            &model,
            FuzzConfig {
                cases: 0,
                ..Default::default()
            },
            control,
        )
        .unwrap();
        assert_eq!(report.termination, termination);
        assert_eq!((report.cases, report.transitions), (0, 0));
        assert_eq!(model.calls.get(), 0);
    }
}

#[test]
fn fuzz_honors_cancellation_at_terminal_callbacks_and_the_final_planned_step() {
    for point in [StopAt::GenerateEnd, StopAt::Enable, StopAt::InitialCheck] {
        let model = CancellingCallbacks::new(point);
        let report = fuzz_with_limits(
            &model,
            FuzzConfig {
                cases: 1,
                max_steps: 1,
                ..Default::default()
            },
            model.control(),
        )
        .unwrap();
        assert_eq!(report.termination, FuzzTermination::Cancelled);
        assert_eq!(report.transitions, 0);
    }
    let model = Instrumented {
        cancel_on_step: true,
        ..Default::default()
    };
    let report = fuzz_with_limits(
        &model,
        FuzzConfig {
            cases: 1,
            max_steps: 1,
            ..Default::default()
        },
        RunLimits {
            cancellation: Some(&model.cancel),
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(report.termination, FuzzTermination::Cancelled);
    assert_eq!(report.transitions, 1);
}

fn counted_failure() -> Failure<usize> {
    Failure {
        inputs: vec![1],
        violations: vec![PropertyFailure {
            phase: CheckPhase::State,
            check: Check::failed("counted.limit", "limit reached"),
        }],
    }
}

#[test]
fn shrink_cancellation_keeps_validation_status_and_never_claims_search_complete() {
    let original = counted_failure();
    let model = CancellingCallbacks::new(StopAt::Enable);
    let interrupted = shrink_with_limits(
        &model,
        &original,
        ShrinkConfig::default(),
        ShrinkLimits {
            control: model.control(),
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(interrupted.termination, ShrinkTermination::Cancelled);
    assert!(!interrupted.validated_original);
    assert_eq!(interrupted.replayed_transitions, 0);
    assert_eq!(interrupted.minimized, original);
    let mut model = CancellingCallbacks::new(StopAt::Simplification);
    model.inner.fail_after = Some(1);
    let validated = shrink_with_limits(
        &model,
        &original,
        ShrinkConfig::default(),
        ShrinkLimits {
            control: model.control(),
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(validated.termination, ShrinkTermination::Cancelled);
    assert!(validated.validated_original);
    assert_eq!(validated.minimized, original);
}

#[test]
fn estimated_state_size_overflow_cannot_bypass_an_admission_budget() {
    let model = Instrumented {
        estimate: Some(usize::MAX),
        ..Default::default()
    };
    let report = enumerate_with_limits(
        &model,
        SearchConfig::default(),
        SearchLimits {
            max_estimated_state_bytes: Some(usize::MAX),
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(
        report.termination,
        SearchTermination::EstimatedStateByteLimit
    );
    assert_eq!((report.states, report.transitions), (1, 1));
    assert_eq!(report.estimated_retained_state_bytes, Some(usize::MAX));
    let unlimited = enumerate(&model, SearchConfig::default()).unwrap();
    assert_eq!(unlimited.termination, SearchTermination::GraphExhausted);
    assert_eq!(unlimited.estimated_retained_state_bytes, None);
}

struct StateFailureThenPassingTransition;
impl Model for StateFailureThenPassingTransition {
    type State = bool;
    type Input = ();
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        metadata()
    }
    fn initial_state(&self) -> Result<bool, ModelError> {
        Ok(false)
    }
    fn step(&self, _: &bool, _: &()) -> Result<Transition<bool, ()>, ModelError> {
        Ok(Transition::accepted(true, vec![]))
    }
    fn check_state(&self, state: &bool) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if *state {
            Check::failed("state", "violated")
        } else {
            Check::passed("state")
        }])
    }
    fn check_transition_into(
        &self,
        _: &bool,
        _: &(),
        _: &TransitionRef<'_, bool, ()>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.push(Check::passed("transition"));
        Ok(())
    }
}
impl Generate for StateFailureThenPassingTransition {
    fn generate(&self, _: &bool, _: &mut Rng) -> Result<Option<()>, ModelError> {
        Ok(Some(()))
    }
}
#[test]
fn passing_transition_checker_preserves_state_failure() {
    let report = fuzz(&StateFailureThenPassingTransition, FuzzConfig::default()).unwrap();
    let failure = report.failure.unwrap();
    assert_eq!(failure.violations.len(), 1);
    assert_eq!(
        failure.violations[0].phase,
        stateless::explore::CheckPhase::State
    );
    assert_eq!(
        failure.violations[0].check,
        Check::failed("state", "violated")
    );
}

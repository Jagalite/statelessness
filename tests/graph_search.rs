//! graph-search-v1 (S1/S2): bfs-controls-v1 parity for default options, the
//! shared conformance/graph-search-v1.json fixtures, and hand-derived cases.
#[path = "support/json.rs"]
mod json;

use std::cell::RefCell;
use std::collections::{HashMap, HashSet};
use std::hash::{Hash, Hasher};
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use stateless::explore::{
    CheckPhase, PropertyFailure, RunLimits, SearchConfig, SearchLimits, SearchObserver,
    SearchReport, SearchTermination, enumerate_observed,
};
use stateless::graph::*;
use stateless::model::*;

fn phase_name(phase: CheckPhase) -> &'static str {
    match phase {
        CheckPhase::InitialState => "initial_state",
        CheckPhase::State => "state",
        CheckPhase::Transition => "transition",
    }
}

fn violations_text(violations: &[PropertyFailure]) -> String {
    violations
        .iter()
        .map(|v| format!("{}/{}", phase_name(v.phase), v.check.id))
        .collect::<Vec<_>>()
        .join(",")
}

/// The fixture spellings of spec/graph-search-v1.md.
fn spelled<S, O>(event: &GraphEvent<'_, S, O>) -> String
where
    S: std::fmt::Display,
{
    match event {
        GraphEvent::RootAdmitted(n) => format!("root:{n}"),
        GraphEvent::ExpansionStarted(n) => format!("expand:{n}"),
        GraphEvent::EdgeEvaluated(edge) => {
            format!("edge:{}:{}:{}", edge.id, edge.source, edge.input)
        }
        GraphEvent::Finding {
            index,
            edge,
            violations,
        } => format!(
            "finding:{index}:{}:{}",
            edge.map_or("-".to_owned(), |e| e.to_string()),
            violations_text(violations)
        ),
        GraphEvent::SuccessorAdmitted { edge, node } => format!("admit:{edge}:{node}"),
        GraphEvent::DuplicateEdge { edge, existing } => format!("dup:{edge}:{existing}"),
        GraphEvent::EdgeReduced {
            edge,
            representative,
        } => format!("reduced:{edge}:{representative}"),
        GraphEvent::SuccessorRejected { edge, reason } => format!("reject:{edge}:{reason}"),
        GraphEvent::ExpansionFinished { node, status } => {
            format!("finish:{node}:{}", status.name())
        }
        GraphEvent::LayerCompleted { depth } => format!("layer:{depth}"),
        GraphEvent::SearchFinished { termination } => format!("end:{termination}"),
    }
}

fn spelled_target(target: &EdgeTarget) -> String {
    match target {
        EdgeTarget::Pending => "pending".into(),
        EdgeTarget::Admitted(n) => format!("admitted:{n}"),
        EdgeTarget::Duplicate(n) => format!("duplicate:{n}"),
        EdgeTarget::Reduced(e) => format!("reduced:{e}"),
        EdgeTarget::Rejected(reason) => format!("rejected:{reason}"),
    }
}

fn spelled_coverage(coverage: &GraphCoverage) -> String {
    match coverage {
        GraphCoverage::Exhaustive => "exhaustive".into(),
        GraphCoverage::Bounded {
            depth_cutoff,
            failure_pruned,
        } => {
            let parts: Vec<&str> = [
                depth_cutoff.then_some("depth_cutoff"),
                failure_pruned.then_some("failure_pruned"),
            ]
            .into_iter()
            .flatten()
            .collect();
            format!("bounded:{}", parts.join("+"))
        }
        GraphCoverage::Incomplete(reason) => format!("incomplete:{reason}"),
    }
}

// MARK: bfs-controls-v1 parity (GRAPH-001)

struct Environment {
    events: RefCell<Vec<String>>,
    cancel: AtomicBool,
    cancel_at: Option<String>,
    error_at: Option<String>,
}
impl Environment {
    fn event(&self, event: String) {
        self.events.borrow_mut().push(event.clone());
        if self.cancel_at.as_ref() == Some(&event) {
            self.cancel.store(true, Ordering::Relaxed);
        }
    }
}
#[derive(Clone)]
struct Logged {
    n: i32,
    env: Rc<Environment>,
}
impl PartialEq for Logged {
    fn eq(&self, other: &Self) -> bool {
        self.env.event(format!("equal:{}:{}", self.n, other.n));
        self.n == other.n
    }
}
impl Eq for Logged {}
impl Hash for Logged {
    fn hash<H: Hasher>(&self, h: &mut H) {
        self.env.event(format!("hash:{}", self.n));
        0u8.hash(h);
    }
}
struct Controls {
    env: Rc<Environment>,
    kind: String,
    estimates: Vec<Option<usize>>,
    fail_initial: bool,
    fail_state: Option<i32>,
    skipped: bool,
}
impl Model for Controls {
    type State = Logged;
    type Input = i32;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "bfs-controls-fixture".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "independent-v1".into(),
        }
    }
    fn initial_state(&self) -> Result<Logged, ModelError> {
        self.env.event("initial".into());
        Ok(Logged {
            n: 0,
            env: Rc::clone(&self.env),
        })
    }
    fn step(&self, before: &Logged, input: &i32) -> Result<Transition<Logged, ()>, ModelError> {
        self.env.event(format!("step:{}:{}", before.n, input));
        let n = match self.kind.as_str() {
            "loop" => 0,
            "diamond" => *input,
            _ => before.n + input,
        };
        Ok(Transition::accepted(
            Logged {
                n,
                env: Rc::clone(&self.env),
            },
            vec![],
        ))
    }
    fn check_state(&self, state: &Logged) -> Result<Vec<Check>, ModelError> {
        let mut checks = Vec::new();
        self.check_state_into(state, &mut CheckSink::new(&mut checks))?;
        Ok(checks)
    }
    fn check_state_into(&self, state: &Logged, sink: &mut CheckSink<'_>) -> Result<(), ModelError> {
        let e = format!("state:{}", state.n);
        self.env.event(e.clone());
        if self.env.error_at.as_ref() == Some(&e) {
            return Err(ModelError::new("fixture check error"));
        }
        sink.push(Check::passed("ok"));
        if self.skipped {
            sink.push(Check::skipped("skip", "not run"));
        }
        if (state.n == 0 && self.fail_initial) || self.fail_state == Some(state.n) {
            sink.push(Check::failed("bad", "broken"));
        }
        Ok(())
    }
    fn check_transition_into(
        &self,
        before: &Logged,
        input: &i32,
        after: &TransitionRef<'_, Logged, ()>,
        _sink: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        let e = format!("transition:{}:{}:{}", before.n, input, after.state.n);
        self.env.event(e.clone());
        if self.env.error_at.as_ref() == Some(&e) {
            return Err(ModelError::new("fixture check error"));
        }
        Ok(())
    }
    fn estimated_state_bytes(&self, state: &Logged) -> Option<usize> {
        self.env.event(format!("estimate:{}", state.n));
        self.estimates[state.n as usize]
    }
}
struct Cursor {
    env: Rc<Environment>,
    state: i32,
    values: std::vec::IntoIter<i32>,
}
impl Iterator for Cursor {
    type Item = i32;
    fn next(&mut self) -> Option<i32> {
        let n = self.values.next();
        self.env.event(format!(
            "next:{}:{}",
            self.state,
            n.map_or("end".into(), |n| n.to_string())
        ));
        n
    }
}
impl Enumerate for Controls {
    fn inputs(&self, state: &Logged) -> Result<Vec<i32>, ModelError> {
        Ok(match self.kind.as_str() {
            "empty" => vec![],
            "loop" => vec![0, 0],
            "diamond" => match state.n {
                0 => vec![1, 2, 1],
                1 | 2 => vec![3],
                _ => vec![],
            },
            _ if state.n < 2 => vec![1],
            _ => vec![],
        })
    }
    fn input_iter<'a>(
        &'a self,
        state: &'a Logged,
    ) -> Result<Box<dyn Iterator<Item = i32> + 'a>, ModelError> {
        self.env.event(format!("inputs:{}", state.n));
        Ok(Box::new(Cursor {
            env: Rc::clone(&self.env),
            state: state.n,
            values: self.inputs(state)?.into_iter(),
        }))
    }
}
struct Observer {
    env: Rc<Environment>,
    history: bool,
}
impl SearchObserver<Controls> for Observer {
    fn needs_history(&self) -> bool {
        self.history
    }
    fn initial(&mut self, state: &Logged, _checks: &[Check]) {
        self.env.event(format!("observe_initial:{}", state.n));
    }
    fn check_error(&mut self, phase: CheckPhase, checks: &[Check], complete: bool) {
        self.env.event(format!(
            "check_error:{}:{}:{}",
            phase_name(phase),
            complete,
            checks.len()
        ));
    }
    fn transition(
        &mut self,
        before: &Logged,
        input: &i32,
        after: &TransitionRef<'_, Logged, ()>,
        _state: &[Check],
        _edge: &[Check],
        history: &[i32],
    ) {
        let h = if history.is_empty() {
            "-".into()
        } else {
            history
                .iter()
                .map(i32::to_string)
                .collect::<Vec<_>>()
                .join(",")
        };
        self.env.event(format!(
            "observe:{}:{}:{}:{}",
            before.n, input, after.state.n, h
        ));
    }
}

fn bfs_term_name(t: SearchTermination) -> &'static str {
    match t {
        SearchTermination::GraphExhausted => "graph_exhausted",
        SearchTermination::DepthBound => "depth_bound",
        SearchTermination::StateLimit => "state_limit",
        SearchTermination::TransitionLimit => "transition_limit",
        SearchTermination::FailureFound => "failure_found",
        SearchTermination::EstimatedStateByteLimit => "estimated_state_byte_limit",
        SearchTermination::Deadline => "deadline",
        SearchTermination::Cancelled => "cancelled",
    }
}

fn bfs_summary(r: &SearchReport<i32>) -> String {
    let target = r.failure.as_ref().and_then(|f| f.violations.first());
    format!(
        "{} {} {} {} {} {:?} {:?} {:?} {:?}",
        bfs_term_name(r.termination),
        r.states,
        r.transitions,
        r.max_depth_reached,
        r.skipped_checks,
        r.estimated_retained_state_bytes,
        r.failure.as_ref().map(|f| &f.inputs),
        target.map(|v| phase_name(v.phase)),
        r.failure.as_ref().map(|f| &f.violations),
    )
}

fn graph_summary(r: &GraphSearchReport<Logged, i32, ()>) -> String {
    let failure = r.failure();
    let target = failure.as_ref().and_then(|f| f.violations.first());
    format!(
        "{} {} {} {} {} {:?} {:?} {:?} {:?}",
        r.termination,
        r.states,
        r.transitions,
        r.max_depth_reached,
        r.skipped_checks,
        r.estimated_retained_state_bytes,
        failure.as_ref().map(|f| &f.inputs),
        target.map(|v| phase_name(v.phase)),
        failure.as_ref().map(|f| &f.violations),
    )
}

fn decimal(v: &json::Value) -> Option<usize> {
    v.opt_str().map(|s| s.parse().unwrap())
}

/// Runs one corpus case through `enumerate_observed` (graph = false) or
/// `explore_graph_observed` with default options (graph = true).
fn controls_case(row: &json::Value, graph: bool) -> (String, Vec<String>) {
    let entry = row.get("entry_control").str();
    let env = Rc::new(Environment {
        events: RefCell::new(vec![]),
        cancel: AtomicBool::new(matches!(entry, "cancelled" | "both")),
        cancel_at: row.get("cancel_at").opt_str().map(str::to_owned),
        error_at: row.get("error_at").opt_str().map(str::to_owned),
    });
    let model = Controls {
        env: Rc::clone(&env),
        kind: row.get("fixture").str().into(),
        estimates: row.get("estimates").arr().iter().map(decimal).collect(),
        fail_initial: row.get("fail_initial").bool(),
        fail_state: row.get("fail_state").opt_int().map(|n| n as i32),
        skipped: row.get("skipped").bool(),
    };
    let mut observer = Observer {
        env: Rc::clone(&env),
        history: row.get("needs_history").bool(),
    };
    let config = SearchConfig {
        max_states: row.get("max_states").int() as usize,
        max_transitions: row.get("max_transitions").int() as u64,
        max_depth: row.get("max_depth").int() as usize,
    };
    let limits = SearchLimits {
        control: RunLimits {
            max_duration: matches!(entry, "deadline" | "both").then_some(Duration::ZERO),
            cancellation: Some(&env.cancel),
        },
        max_estimated_state_bytes: decimal(row.get("max_estimated_state_bytes")),
    };
    let summary = if graph {
        match explore_graph_observed(
            &model,
            config,
            limits,
            GraphSearchOptions::default(),
            &mut observer,
        ) {
            Ok(report) => graph_summary(&report),
            Err(error) => format!("error {error}"),
        }
    } else {
        match enumerate_observed(&model, config, limits, &mut observer) {
            Ok(report) => bfs_summary(&report),
            Err(error) => format!("error {error}"),
        }
    };
    let events = env.events.borrow().clone();
    (summary, events)
}

#[test]
fn default_options_reproduce_bfs_controls_corpus() {
    let corpus = json::parse(include_str!("../conformance/bfs-controls-v1.json"));
    assert_eq!(corpus.get("profile").str(), "bfs-controls-v1");
    let cases = corpus.get("cases").arr();
    assert_eq!(
        cases.len(),
        47,
        "shared corpus unexpectedly changed case count"
    );
    for row in cases {
        let name = row.get("case").str();
        if row.get("requires_64bit") == &json::Value::Bool(true) && usize::BITS < 64 {
            continue;
        }
        let portable = controls_case(row, false);
        let extended = controls_case(row, true);
        assert_eq!(extended.0, portable.0, "{name}: report");
        assert_eq!(extended.1, portable.1, "{name}: callback log");
        assert_eq!(
            extended.1,
            row.get("events").strings(),
            "{name}: corpus events"
        );
        if let Some(message) = row.get("error_contains").opt_str() {
            assert!(
                extended.0.starts_with("error ") && extended.0.contains(message),
                "{name}"
            );
        } else {
            let expected = row.get("expected");
            let prefix = format!(
                "{} {} {} {} {}",
                expected.get("termination").str(),
                expected.get("states").int(),
                expected.get("transitions").int(),
                expected.get("max_depth_reached").int(),
                expected.get("skipped_checks").int()
            );
            assert!(
                extended.0.starts_with(&prefix),
                "{name}: {} vs {prefix}",
                extended.0
            );
        }
    }
}

// MARK: Hand-authored graphs

#[derive(Clone, Debug)]
struct N {
    n: i64,
    constant_hash: bool,
}
impl PartialEq for N {
    fn eq(&self, other: &Self) -> bool {
        self.n == other.n
    }
}
impl Eq for N {}
impl Hash for N {
    fn hash<H: Hasher>(&self, h: &mut H) {
        if self.constant_hash {
            7u8.hash(h);
        } else {
            self.n.hash(h);
        }
    }
}

/// step(state, input) = input with outputs [input * 10]; state checks are
/// [ok] plus failed bad_state for fail_state; bad_edge for fail_edge pairs.
#[derive(Default)]
struct Graph {
    edges: HashMap<i64, Vec<i64>>,
    constant_hash: bool,
    fail_state: HashSet<i64>,
    fail_edge: HashSet<(i64, i64)>,
    throw_at: Option<i64>,
}
impl Graph {
    fn new(edges: &[(i64, &[i64])]) -> Self {
        Self {
            edges: edges.iter().map(|(k, v)| (*k, v.to_vec())).collect(),
            ..Self::default()
        }
    }
    fn failing_states(mut self, states: &[i64]) -> Self {
        self.fail_state = states.iter().copied().collect();
        self
    }
    fn failing_edges(mut self, edges: &[(i64, i64)]) -> Self {
        self.fail_edge = edges.iter().copied().collect();
        self
    }
}
impl Model for Graph {
    type State = N;
    type Input = i64;
    type Output = i64;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "graph".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "test".into(),
        }
    }
    fn initial_state(&self) -> Result<N, ModelError> {
        Ok(N {
            n: 0,
            constant_hash: self.constant_hash,
        })
    }
    fn step(&self, _: &N, input: &i64) -> Result<Transition<N, i64>, ModelError> {
        Ok(Transition::accepted(
            N {
                n: *input,
                constant_hash: self.constant_hash,
            },
            vec![input * 10],
        ))
    }
    fn check_state(&self, state: &N) -> Result<Vec<Check>, ModelError> {
        if self.throw_at == Some(state.n) {
            return Err(ModelError::new("checker exploded"));
        }
        let mut checks = vec![Check::passed("ok")];
        if self.fail_state.contains(&state.n) {
            checks.push(Check::failed("bad_state", format!("state {}", state.n)));
        }
        Ok(checks)
    }
    fn check_transition(
        &self,
        before: &N,
        input: &i64,
        _: &TransitionRef<'_, N, i64>,
    ) -> Result<Vec<Check>, ModelError> {
        Ok(if self.fail_edge.contains(&(before.n, *input)) {
            vec![Check::failed(
                "bad_edge",
                format!("{}->{}", before.n, input),
            )]
        } else {
            vec![]
        })
    }
}
impl Enumerate for Graph {
    fn inputs(&self, state: &N) -> Result<Vec<i64>, ModelError> {
        Ok(self.edges.get(&state.n).cloned().unwrap_or_default())
    }
}

type Report = GraphSearchReport<N, i64, i64>;

fn diamond() -> Graph {
    Graph::new(&[(0, &[1, 2, 1]), (1, &[3]), (2, &[3])])
}
fn chain() -> Graph {
    Graph::new(&[(0, &[1]), (1, &[2]), (2, &[3]), (3, &[4])])
}
fn collect(expand: bool) -> FailurePolicy {
    FailurePolicy::Collect {
        max_retained_findings: 10,
        expand_failing_successors: expand,
    }
}

struct Run {
    config: SearchConfig,
    deadline: bool,
    policy: FailurePolicy,
    retention: GraphRetention,
    cancel_after: Option<&'static str>,
}
impl Default for Run {
    fn default() -> Self {
        Self {
            config: SearchConfig::default(),
            deadline: false,
            policy: FailurePolicy::StopAtFirst,
            retention: GraphRetention::default(),
            cancel_after: None,
        }
    }
}

fn search_with(model: &Graph, run: Run) -> (Report, Vec<String>) {
    let cancelled = AtomicBool::new(false);
    let mut events = Vec::new();
    let mut sink = |event: GraphEvent<'_, i64, i64>| {
        let text = spelled(&event);
        if Some(text.as_str()) == run.cancel_after {
            cancelled.store(true, Ordering::Relaxed);
        }
        events.push(text);
        Ok(())
    };
    let limits = SearchLimits {
        control: RunLimits {
            max_duration: run.deadline.then_some(Duration::ZERO),
            cancellation: Some(&cancelled),
        },
        max_estimated_state_bytes: None,
    };
    let options = GraphSearchOptions {
        failure_policy: run.policy,
        retention: run.retention,
        events: Some(&mut sink),
        ..GraphSearchOptions::default()
    };
    let report = explore_graph(model, run.config, limits, options).unwrap();
    (report, events)
}

fn search(model: &Graph) -> (Report, Vec<String>) {
    search_with(model, Run::default())
}

fn targets(r: &Report) -> Vec<EdgeTarget> {
    r.edges.iter().map(|e| e.target).collect()
}

#[test]
fn graph_search_fixtures() {
    let doc = json::parse(include_str!("../conformance/graph-search-v1.json"));
    assert_eq!(doc.get("version").int(), 1);
    let cases = doc.get("cases").arr();
    assert_eq!(cases.len(), 10);
    for c in cases {
        let name = c.get("case").str();
        let mut model = Graph {
            edges: c
                .get("edges")
                .obj()
                .iter()
                .map(|(k, v)| (k.parse().unwrap(), v.ints()))
                .collect(),
            constant_hash: c.get("hash").str() == "constant",
            fail_state: c.get("fail_state").ints().into_iter().collect(),
            ..Graph::default()
        };
        model.fail_edge = c
            .get("fail_edge")
            .arr()
            .iter()
            .map(|pair| (pair.arr()[0].int(), pair.arr()[1].int()))
            .collect();
        let cfg = c.get("config");
        let p = c.get("policy");
        let policy = if p.get("kind").str() == "collect" {
            FailurePolicy::Collect {
                max_retained_findings: p.get("max_retained_findings").int() as usize,
                expand_failing_successors: p.get("expand_failing_successors").bool(),
            }
        } else {
            FailurePolicy::StopAtFirst
        };
        let (r, events) = search_with(
            &model,
            Run {
                config: SearchConfig {
                    max_states: cfg.get("max_states").int() as usize,
                    max_transitions: cfg.get("max_transitions").int() as u64,
                    max_depth: cfg.get("max_depth").int() as usize,
                },
                policy,
                ..Run::default()
            },
        );
        let x = c.get("expected");
        assert_eq!(events, x.get("events").strings(), "{name}");
        assert_eq!(
            r.edges
                .iter()
                .map(|e| spelled_target(&e.target))
                .collect::<Vec<_>>(),
            x.get("targets").strings(),
            "{name}"
        );
        assert_eq!(r.termination.as_str(), x.get("termination").str(), "{name}");
        assert_eq!(
            spelled_coverage(&r.coverage),
            x.get("coverage").str(),
            "{name}"
        );
        assert_eq!(r.states as i64, x.get("states").int(), "{name}");
        assert_eq!(r.transitions as i64, x.get("transitions").int(), "{name}");
        assert_eq!(
            r.findings
                .iter()
                .map(|f| f.witness.clone())
                .collect::<Vec<_>>(),
            x.get("finding_witnesses")
                .arr()
                .iter()
                .map(json::Value::ints)
                .collect::<Vec<_>>(),
            "{name}"
        );
        assert_eq!(
            r.findings
                .iter()
                .map(|f| f.requires_continued_evidence)
                .collect::<Vec<_>>(),
            x.get("requires_continued_evidence")
                .arr()
                .iter()
                .map(json::Value::bool)
                .collect::<Vec<_>>(),
            "{name}"
        );
        assert_eq!(
            r.finding_count as i64,
            x.get("finding_count").int(),
            "{name}"
        );
    }
}

#[test]
fn diamond_graph_ids_and_event_order() {
    let (r, events) = search(&diamond());
    assert_eq!(
        events,
        [
            "root:0",
            "expand:0",
            "edge:0:0:1",
            "admit:0:1",
            "edge:1:0:2",
            "admit:1:2",
            "edge:2:0:1",
            "dup:2:1",
            "finish:0:complete",
            "layer:0",
            "expand:1",
            "edge:3:1:3",
            "admit:3:3",
            "finish:1:complete",
            "expand:2",
            "edge:4:2:3",
            "dup:4:3",
            "finish:2:complete",
            "layer:1",
            "expand:3",
            "finish:3:complete",
            "layer:2",
            "end:graph_exhausted",
        ]
    );
    assert_eq!(r.coverage, GraphCoverage::Exhaustive);
    assert_eq!(r.termination, GraphTermination::GraphExhausted);
    assert_eq!(
        r.nodes.iter().map(|n| n.state.n).collect::<Vec<_>>(),
        [0, 1, 2, 3]
    );
    assert_eq!(
        r.nodes.iter().map(|n| n.parent).collect::<Vec<_>>(),
        [None, Some(0), Some(0), Some(1)]
    );
    assert_eq!(
        r.nodes.iter().map(|n| n.depth).collect::<Vec<_>>(),
        [0, 1, 1, 2]
    );
    assert_eq!(
        r.nodes.iter().map(|n| n.admitting_edge).collect::<Vec<_>>(),
        [None, Some(0), Some(1), Some(3)]
    );
    assert_eq!(
        r.nodes.iter().map(|n| n.id).collect::<Vec<_>>(),
        [0, 1, 2, 3]
    );
    assert_eq!(
        r.edges.iter().map(|e| e.id).collect::<Vec<_>>(),
        [0, 1, 2, 3, 4]
    );
    use EdgeTarget::*;
    assert_eq!(
        targets(&r),
        [
            Admitted(1),
            Admitted(2),
            Duplicate(1),
            Admitted(3),
            Duplicate(3)
        ]
    );
    assert_eq!(
        r.edges
            .iter()
            .map(|e| e.outputs.clone())
            .collect::<Vec<_>>(),
        [vec![10], vec![20], vec![10], vec![30], vec![30]]
    );
    assert_eq!(r.transitions, 5);
    assert_eq!(r.graph_edges, 5);
    assert_eq!(r.duplicate_edges, 2);
    assert_eq!(r.rejected_successors, 0);
    assert_eq!(r.frontier_size, 0);
    assert_eq!(r.completed_layers, 3);
    assert_eq!(r.states, 4);
    assert!(r.failure().is_none());
    assert!(r.estimated_graph_bytes > 0);
}

#[test]
fn self_loop_and_hash_collisions_keep_structure() {
    let (looped, _) = search(&Graph::new(&[(0, &[0, 0])]));
    assert_eq!(
        targets(&looped),
        [EdgeTarget::Duplicate(0), EdgeTarget::Duplicate(0)]
    );
    assert_eq!(looped.nodes.len(), 1);
    assert_eq!(looped.coverage, GraphCoverage::Exhaustive);
    let mut colliding = diamond();
    colliding.constant_hash = true;
    let (collide, events) = search(&colliding);
    let (distinct, expected) = search(&diamond());
    assert_eq!(events, expected);
    assert_eq!(targets(&collide), targets(&distinct));
    assert_eq!(
        collide.nodes.iter().map(|n| n.state.n).collect::<Vec<_>>(),
        distinct.nodes.iter().map(|n| n.state.n).collect::<Vec<_>>()
    );
}

#[test]
fn duplicate_failing_edge_keeps_evidence() {
    let model = diamond().failing_edges(&[(2, 3)]);
    let (r, events) = search_with(
        &model,
        Run {
            policy: collect(false),
            ..Run::default()
        },
    );
    assert!(events.contains(&"finding:0:4:transition/bad_edge".to_owned()));
    assert_eq!(r.edges[4].target, EdgeTarget::Duplicate(3));
    assert!(r.edges[4].failed());
    assert_eq!(
        r.findings
            .iter()
            .map(|f| f.witness.clone())
            .collect::<Vec<_>>(),
        [vec![2, 3]]
    );
    assert_eq!(r.findings[0].edge, Some(4));
    // A failing edge to an admitted state prunes nothing.
    assert_eq!(r.coverage, GraphCoverage::Exhaustive);
    assert_eq!(r.termination, GraphTermination::GraphExhausted);
    let (stop, _) = search(&model);
    assert_eq!(stop.termination, GraphTermination::FailureFound);
    assert_eq!(
        stop.coverage,
        GraphCoverage::Incomplete(GraphTermination::FailureFound)
    );
    assert_eq!(
        stop.edges.last().unwrap().target,
        EdgeTarget::Rejected(GraphRejection::FailureFound)
    );
    assert_eq!(
        stop.nodes[2].expansion,
        NodeExpansion::Interrupted(GraphTermination::FailureFound)
    );
    assert_eq!(stop.failure().unwrap().inputs, [2, 3]);
    assert_eq!(stop.rejected_successors, 1);
}

/// Review regression: the edge unresolved at an early exit is a rejected
/// successor even when edges are only streamed through events.
#[test]
fn unresolved_edge_counts_as_rejected_when_streamed() {
    let model = diamond().failing_edges(&[(2, 3)]);
    let (streamed, events) = search_with(
        &model,
        Run {
            retention: GraphRetention {
                retain_edges: false,
                ..GraphRetention::default()
            },
            ..Run::default()
        },
    );
    assert_eq!(streamed.rejected_successors, 1);
    assert!(streamed.edges.is_empty());
    assert!(!streamed.edges_retained);
    assert_eq!(events.last().unwrap(), "end:failure_found");
    // Without any edge record (no retention, no sink) the edge still counts.
    let report = explore_graph(
        &model,
        SearchConfig::default(),
        SearchLimits::default(),
        GraphSearchOptions {
            retention: GraphRetention {
                retain_edges: false,
                ..GraphRetention::default()
            },
            ..GraphSearchOptions::default()
        },
    )
    .unwrap();
    assert_eq!(report.rejected_successors, 1);
    assert_eq!(report.termination, GraphTermination::FailureFound);
    // An error after the edge was evaluated also leaves it rejected.
    let mut sink = |event: GraphEvent<'_, i64, i64>| match event {
        GraphEvent::EdgeEvaluated(edge) if edge.id == 3 => Err(ModelError::new("sink full")),
        _ => Ok(()),
    };
    let error = explore_graph(
        &diamond(),
        SearchConfig::default(),
        SearchLimits::default(),
        GraphSearchOptions {
            retention: GraphRetention {
                retain_edges: false,
                ..GraphRetention::default()
            },
            events: Some(&mut sink),
            ..GraphSearchOptions::default()
        },
    )
    .unwrap_err();
    let report = error.report.unwrap();
    assert_eq!(report.termination, GraphTermination::ModelError);
    assert_eq!(report.rejected_successors, 1);
}

#[test]
fn state_cap_on_new_successor() {
    let (r, events) = search_with(
        &diamond(),
        Run {
            config: SearchConfig {
                max_states: 2,
                ..SearchConfig::default()
            },
            ..Run::default()
        },
    );
    assert_eq!(
        events[events.len() - 3..],
        ["edge:1:0:2", "reject:1:state_limit", "end:state_limit"]
    );
    assert_eq!(r.termination, GraphTermination::StateLimit);
    assert_eq!(
        r.coverage,
        GraphCoverage::Incomplete(GraphTermination::StateLimit)
    );
    assert_eq!(
        targets(&r),
        [
            EdgeTarget::Admitted(1),
            EdgeTarget::Rejected(GraphRejection::StateLimit)
        ]
    );
    assert_eq!(r.rejected_successors, 1);
    assert_eq!(
        r.nodes.iter().map(|n| n.expansion).collect::<Vec<_>>(),
        [
            NodeExpansion::Interrupted(GraphTermination::StateLimit),
            NodeExpansion::Unexpanded
        ]
    );
    assert_eq!(r.frontier_size, 2);
    assert_eq!(r.completed_layers, 0);
}

#[test]
fn edge_cap_midway_through_node() {
    let (r, _) = search_with(
        &diamond(),
        Run {
            config: SearchConfig {
                max_transitions: 2,
                ..SearchConfig::default()
            },
            ..Run::default()
        },
    );
    assert_eq!(r.termination, GraphTermination::TransitionLimit);
    assert_eq!(r.transitions, 2);
    assert_eq!(r.edges.len(), 2);
    assert_eq!(
        r.nodes[0].expansion,
        NodeExpansion::Interrupted(GraphTermination::TransitionLimit)
    );
    assert_eq!(
        r.coverage,
        GraphCoverage::Incomplete(GraphTermination::TransitionLimit)
    );
}

#[test]
fn cancellation_and_deadline() {
    let (r, events) = search_with(
        &diamond(),
        Run {
            cancel_after: Some("admit:3:3"),
            ..Run::default()
        },
    );
    assert_eq!(r.termination, GraphTermination::Cancelled);
    assert_eq!(events.last().unwrap(), "end:cancelled");
    // The admission completes; the following poll stops node 1 before it finishes.
    use NodeExpansion::*;
    assert_eq!(
        r.nodes.iter().map(|n| n.expansion).collect::<Vec<_>>(),
        [
            Complete,
            Interrupted(GraphTermination::Cancelled),
            Unexpanded,
            Unexpanded
        ]
    );
    assert_eq!(r.frontier_size, 3);
    let (late, _) = search_with(
        &diamond(),
        Run {
            cancel_after: Some("edge:4:2:3"),
            ..Run::default()
        },
    );
    assert_eq!(
        late.edges[4].target,
        EdgeTarget::Rejected(GraphRejection::Cancelled)
    );
    assert_eq!(late.rejected_successors, 1);
    let (expired, none) = search_with(
        &diamond(),
        Run {
            deadline: true,
            ..Run::default()
        },
    );
    assert_eq!(expired.termination, GraphTermination::Deadline);
    assert_eq!(expired.states, 0);
    assert!(expired.nodes.is_empty());
    assert_eq!(none, ["end:deadline"]);
}

#[test]
fn zero_depth_and_depth_bound_coverage() {
    let depth = |max_depth| Run {
        config: SearchConfig {
            max_depth,
            ..SearchConfig::default()
        },
        ..Run::default()
    };
    let (zero, events) = search_with(&diamond(), depth(0));
    assert_eq!(
        events,
        [
            "root:0",
            "expand:0",
            "finish:0:depthFrontier",
            "layer:0",
            "end:depth_bound"
        ]
    );
    assert_eq!(zero.termination, GraphTermination::DepthBound);
    assert_eq!(
        zero.coverage,
        GraphCoverage::Bounded {
            depth_cutoff: true,
            failure_pruned: false
        }
    );
    assert_eq!(zero.transitions, 0);
    let (one, _) = search_with(&diamond(), depth(1));
    assert_eq!(
        one.coverage,
        GraphCoverage::Bounded {
            depth_cutoff: true,
            failure_pruned: false
        }
    );
    assert_eq!(one.frontier_size, 2);
    // Reaching max_depth at a leaf is not a cutoff.
    let (leaf, _) = search_with(&diamond(), depth(2));
    assert_eq!(leaf.coverage, GraphCoverage::Exhaustive);
    assert_eq!(leaf.termination, GraphTermination::GraphExhausted);
}

#[test]
fn retention_limits_are_visible() {
    let (capped, _) = search_with(
        &diamond(),
        Run {
            retention: GraphRetention {
                max_retained_edges: Some(3),
                ..GraphRetention::default()
            },
            ..Run::default()
        },
    );
    assert_eq!(capped.termination, GraphTermination::GraphRetentionLimit);
    assert_eq!(capped.edges.len(), 3);
    assert!(!capped.edges_retained);
    assert_eq!(
        capped.coverage,
        GraphCoverage::Incomplete(GraphTermination::GraphRetentionLimit)
    );
    let (streamed, events) = search_with(
        &diamond(),
        Run {
            retention: GraphRetention {
                retain_edges: false,
                ..GraphRetention::default()
            },
            ..Run::default()
        },
    );
    assert!(streamed.edges.is_empty());
    assert!(!streamed.edges_retained);
    assert_eq!(streamed.coverage, GraphCoverage::Exhaustive);
    assert_eq!(streamed.graph_edges, 5);
    assert_eq!(events.iter().filter(|e| e.starts_with("edge:")).count(), 5);
    let (tiny, _) = search_with(
        &diamond(),
        Run {
            retention: GraphRetention {
                max_graph_bytes: Some(1),
                ..GraphRetention::default()
            },
            ..Run::default()
        },
    );
    assert_eq!(tiny.termination, GraphTermination::GraphRetentionLimit);
    assert_eq!(tiny.states, 0);
    // Rust caps are unsigned, so negative caps cannot be expressed. A zero
    // state cap is still a configuration error before the search starts.
    let error = explore_graph(
        &diamond(),
        SearchConfig {
            max_states: 0,
            ..SearchConfig::default()
        },
        SearchLimits::default(),
        GraphSearchOptions::default(),
    )
    .unwrap_err();
    assert!(error.report.is_none());
    assert!(error.to_string().contains("max_states"));
}

#[test]
fn first_and_subsequent_failures() {
    let model = chain().failing_states(&[2, 3, 4]);
    let (r, _) = search_with(
        &model,
        Run {
            policy: collect(true),
            ..Run::default()
        },
    );
    assert_eq!(
        r.findings
            .iter()
            .map(|f| f.witness.clone())
            .collect::<Vec<_>>(),
        [vec![1, 2], vec![1, 2, 3], vec![1, 2, 3, 4]]
    );
    assert_eq!(
        r.findings
            .iter()
            .map(|f| f.requires_continued_evidence)
            .collect::<Vec<_>>(),
        [false, true, true]
    );
    assert_eq!(
        r.findings.iter().map(|f| f.index).collect::<Vec<_>>(),
        [0, 1, 2]
    );
    assert_eq!(r.finding_count, 3);
    assert!(!r.findings_truncated());
    assert_eq!(
        r.failed_properties,
        [PropertyFailureCount {
            phase: CheckPhase::State,
            id: "bad_state".into(),
            count: 3,
            first_finding: 0
        }]
    );
    assert_eq!(r.coverage, GraphCoverage::Exhaustive);
    assert_eq!(
        r.nodes.iter().map(|n| n.path_failed).collect::<Vec<_>>(),
        [false, false, true, true, true]
    );
    assert_eq!(r.failure().unwrap().inputs, [1, 2]);
    let (pruned, _) = search_with(
        &model,
        Run {
            policy: collect(false),
            ..Run::default()
        },
    );
    assert_eq!(pruned.finding_count, 1);
    assert_eq!(pruned.nodes.len(), 2);
    assert_eq!(
        pruned.edges.last().unwrap().target,
        EdgeTarget::Rejected(GraphRejection::FailingSuccessor)
    );
    assert_eq!(
        pruned.coverage,
        GraphCoverage::Bounded {
            depth_cutoff: false,
            failure_pruned: true
        }
    );
    assert_eq!(pruned.termination, GraphTermination::GraphExhausted);
}

#[test]
fn multiple_phases_on_one_edge_are_ordered() {
    let model = chain().failing_states(&[1]).failing_edges(&[(0, 1)]);
    let (r, events) = search_with(
        &model,
        Run {
            policy: collect(true),
            ..Run::default()
        },
    );
    assert_eq!(r.findings.len(), 1);
    assert_eq!(
        violations_text(&r.findings[0].violations),
        "state/bad_state,transition/bad_edge"
    );
    assert!(events.contains(&"finding:0:0:state/bad_state,transition/bad_edge".to_owned()));
    assert_eq!(
        r.failed_properties
            .iter()
            .map(|p| p.id.as_str())
            .collect::<Vec<_>>(),
        ["bad_state", "bad_edge"]
    );
    assert_eq!(
        r.edges[0].state_checks,
        [Check::passed("ok"), Check::failed("bad_state", "state 1")]
    );
    assert_eq!(
        r.edges[0].transition_checks,
        [Check::failed("bad_edge", "0->1")]
    );
}

#[test]
fn initial_failure_under_collection() {
    let model = chain().failing_states(&[0]);
    let (expand, _) = search_with(
        &model,
        Run {
            policy: collect(true),
            ..Run::default()
        },
    );
    assert_eq!(
        expand
            .findings
            .iter()
            .map(|f| f.witness.clone())
            .collect::<Vec<_>>(),
        [Vec::<i64>::new()]
    );
    assert_eq!(expand.findings[0].edge, None);
    assert_eq!(expand.nodes.len(), 5);
    assert_eq!(
        expand.findings[0].violations[0].phase,
        CheckPhase::InitialState
    );
    assert_eq!(expand.coverage, GraphCoverage::Exhaustive);
    let (pruned, events) = search_with(
        &model,
        Run {
            policy: collect(false),
            ..Run::default()
        },
    );
    assert_eq!(
        events,
        [
            "finding:0:-:initial_state/bad_state",
            "root:0",
            "finish:0:failurePruned",
            "layer:0",
            "end:graph_exhausted"
        ]
    );
    assert_eq!(
        pruned.coverage,
        GraphCoverage::Bounded {
            depth_cutoff: false,
            failure_pruned: true
        }
    );
}

#[test]
fn failure_plus_cutoff() {
    let (r, _) = search_with(
        &chain().failing_states(&[2]),
        Run {
            config: SearchConfig {
                max_states: 3,
                ..SearchConfig::default()
            },
            policy: collect(true),
            ..Run::default()
        },
    );
    assert_eq!(r.finding_count, 1);
    assert_eq!(r.termination, GraphTermination::StateLimit);
    assert_eq!(
        r.coverage,
        GraphCoverage::Incomplete(GraphTermination::StateLimit)
    );
    assert_eq!(r.findings[0].witness, [1, 2]);
}

#[test]
fn checker_error_after_partial_evidence() {
    let mut model = chain().failing_states(&[2]);
    model.throw_at = Some(3);
    let error = explore_graph(
        &model,
        SearchConfig::default(),
        SearchLimits::default(),
        GraphSearchOptions {
            failure_policy: collect(true),
            ..GraphSearchOptions::default()
        },
    )
    .unwrap_err();
    assert!(error.to_string().contains("checker exploded"));
    let r = error.report.unwrap();
    assert_eq!(r.termination, GraphTermination::ModelError);
    assert_eq!(
        r.coverage,
        GraphCoverage::Incomplete(GraphTermination::ModelError)
    );
    assert_eq!(
        r.findings
            .iter()
            .map(|f| f.witness.clone())
            .collect::<Vec<_>>(),
        [vec![1, 2]]
    );
    assert_eq!(
        r.nodes[2].expansion,
        NodeExpansion::Interrupted(GraphTermination::ModelError)
    );
    // The failing batch never completed, so no record exists for its edge.
    assert_eq!(r.edges.len(), 2);
    assert_eq!(r.edges.last().unwrap().target, EdgeTarget::Admitted(2));
    assert_eq!(r.rejected_successors, 0);
}

#[test]
fn deterministic_finding_truncation() {
    let model = chain().failing_states(&[2, 3, 4]);
    let policy = FailurePolicy::Collect {
        max_retained_findings: 1,
        expand_failing_successors: true,
    };
    let run = || {
        search_with(
            &model,
            Run {
                policy,
                ..Run::default()
            },
        )
    };
    let (a, events_a) = run();
    let (b, events_b) = run();
    assert_eq!(
        a.findings
            .iter()
            .map(|f| f.witness.clone())
            .collect::<Vec<_>>(),
        [vec![1, 2]]
    );
    assert_eq!(a.finding_count, 3);
    assert!(a.findings_truncated());
    assert_eq!(a.failed_properties[0].count, 3);
    assert_eq!(events_a, events_b);
    assert_eq!(b.findings[0].witness, [1, 2]);
    let (none, _) = search_with(
        &chain().failing_states(&[2]),
        Run {
            policy: FailurePolicy::Collect {
                max_retained_findings: 0,
                expand_failing_successors: true,
            },
            ..Run::default()
        },
    );
    assert!(none.findings.is_empty());
    assert!(none.failure().is_none());
    assert_eq!(none.finding_count, 1);
    assert_eq!(none.failed_properties[0].id, "bad_state");
}

#[test]
fn annotations_and_event_payloads() {
    let model = Graph::new(&[(0, &[1, 2])]);
    let mut seen = Vec::new();
    let mut sink = |event: GraphEvent<'_, i64, i64>| {
        if let GraphEvent::EdgeEvaluated(edge) = event {
            assert_eq!(edge.target, EdgeTarget::Pending);
            seen.push(edge.clone());
        }
        Ok(())
    };
    let mut annotate = |before: &N, _input: &i64, t: &TransitionRef<'_, N, i64>| {
        Ok(vec![format!("from {} to {}", before.n, t.state.n)])
    };
    let r = explore_graph(
        &model,
        SearchConfig::default(),
        SearchLimits::default(),
        GraphSearchOptions {
            events: Some(&mut sink),
            annotate_edge: Some(&mut annotate),
            ..GraphSearchOptions::default()
        },
    )
    .unwrap();
    assert_eq!(r.edges.iter().map(|e| e.input).collect::<Vec<_>>(), [1, 2]);
    assert_eq!(
        r.edges
            .iter()
            .map(|e| e.annotations.clone())
            .collect::<Vec<_>>(),
        [
            vec!["from 0 to 1".to_owned()],
            vec!["from 0 to 2".to_owned()]
        ]
    );
    assert_eq!(
        r.nodes.iter().map(|n| n.state.n).collect::<Vec<_>>(),
        [0, 1, 2]
    );
    // Events carry the evaluated record before its successor is resolved.
    assert_eq!(seen.len(), 2);
    for (event, retained) in seen.iter().zip(&r.edges) {
        assert_eq!(
            GraphEdge {
                target: retained.target,
                ..event.clone()
            },
            *retained
        );
    }
}

#[test]
fn sink_error_aborts_with_partial_report() {
    let mut sink = |event: GraphEvent<'_, i64, i64>| match event {
        GraphEvent::SuccessorAdmitted { node: 2, .. } => Err(ModelError::new("sink full")),
        _ => Ok(()),
    };
    let error = explore_graph(
        &diamond(),
        SearchConfig::default(),
        SearchLimits::default(),
        GraphSearchOptions {
            events: Some(&mut sink),
            ..GraphSearchOptions::default()
        },
    )
    .unwrap_err();
    assert!(error.to_string().contains("sink full"));
    let r = error.report.unwrap();
    assert_eq!(r.nodes.len(), 3);
    assert_eq!(
        r.coverage,
        GraphCoverage::Incomplete(GraphTermination::ModelError)
    );
}

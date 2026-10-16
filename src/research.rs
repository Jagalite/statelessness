//! Opt-in finite-fixture experiments for proposed state equivalences.
//!
//! This module never changes production exploration or replay equality. It
//! materializes one closed, explicitly supplied finite domain, then compares
//! exact reachability with a candidate quotient of that same transition table.
//! Agreement qualifies only that fixture and its recorded assumptions.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::hash::Hash;

use crate::assurance::{PropertyPhase, RequiredProperty};
use crate::{Check, CheckSink, CheckStatus, Disposition, Enumerate, ModelError, ModelMetadata};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Assumptions {
    /// Caller/reviewer declaration, not a proof supplied by this library.
    pub established: bool,
    pub finite_domain: String,
    pub equivalence: String,
    pub effects: String,
    pub transition_properties: String,
    pub queue_order: String,
    pub oracle_history: String,
    pub visible_timing: String,
}
impl Assumptions {
    fn validate(&self) -> Result<(), ModelError> {
        for statement in [
            &self.finite_domain,
            &self.equivalence,
            &self.effects,
            &self.transition_properties,
            &self.queue_order,
            &self.oracle_history,
            &self.visible_timing,
        ] {
            if statement.trim().is_empty() {
                return Err(ModelError::new(
                    "every reduction assumption needs an explicit statement",
                ));
            }
        }
        Ok(())
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Proposal {
    pub name: String,
    pub version: u32,
    pub build: String,
    pub assumptions: Assumptions,
    pub required_properties: Vec<RequiredProperty>,
}
#[derive(Clone, Copy, Debug)]
pub struct ExperimentConfig {
    /// False by default. Enabling this research never enables production pruning.
    pub enabled: bool,
    pub max_states: usize,
    pub max_edges: u64,
    pub max_equivalence_pairs: u64,
}
impl Default for ExperimentConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            max_states: 256,
            max_edges: 4096,
            max_equivalence_pairs: 65_536,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExperimentStatus {
    Disabled,
    AssumptionsNotEstablished,
    Incomplete,
    /// A concrete difference rejects the proposed equivalence for this fixture.
    Counterexample,
    /// Checked bounded fixture only; no general reduction/completeness claim.
    AgreesWithinDeclaredFixture,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ViolationWitness<I> {
    pub property: RequiredProperty,
    pub details: String,
    pub inputs: Vec<I>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Reachability<I> {
    /// Representative exact-state indexes reached by this traversal.
    pub states: Vec<usize>,
    pub edges: u64,
    /// One deterministic shortest witness per property ID and phase.
    pub violations: Vec<ViolationWitness<I>>,
    pub observed_properties: BTreeSet<RequiredProperty>,
    pub skipped_checks: u64,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EquivalenceCounterexample<I> {
    pub left_state: usize,
    pub right_state: usize,
    pub left_prefix: Vec<I>,
    pub right_prefix: Vec<I>,
    pub reason: String,
}
#[derive(Clone, Debug)]
pub struct ExperimentReport<I> {
    pub model: ModelMetadata,
    pub proposal: Proposal,
    pub config: ExperimentConfig,
    pub status: ExperimentStatus,
    pub declared_states: usize,
    pub materialized_edges: u64,
    pub compared_pairs: u64,
    pub exact: Option<Reachability<I>>,
    pub reduced: Option<Reachability<I>>,
    pub counterexamples: Vec<EquivalenceCounterexample<I>>,
    pub missing_violations: Vec<ViolationWitness<I>>,
    pub missing_properties: Vec<RequiredProperty>,
    pub limitation: String,
}
struct Edge<I, O> {
    input: I,
    target: usize,
    outputs: Vec<O>,
    disposition: Disposition,
    checks: Vec<Check>,
}
struct Graph<I, O> {
    initial: usize,
    checks: Vec<Vec<Check>>,
    edges: Vec<Vec<Edge<I, O>>>,
}

/// Evaluate a candidate projection against every reachable member of each
/// equivalence class in an explicitly closed finite domain. Input order/labels,
/// ordered effects, dispositions, ordered state/transition checks, and projected
/// successors must agree exactly. A lossy guidance feature is not automatically
/// an equivalence. No transition/input pruning or production setting is changed.
///
/// `states` must include every successor of every declared state's inputs.
/// Materialization executes each declared edge once, not once per experiment
/// arm. The candidate and exact traversals then use only that immutable table.
/// Errors and limits return an incomplete report; never a completed comparison.
pub fn compare_equivalence<M, K, F>(
    model: &M,
    states: &[M::State],
    proposal: Proposal,
    config: ExperimentConfig,
    projection: F,
) -> Result<ExperimentReport<M::Input>, ModelError>
where
    M: Enumerate,
    K: Eq + Hash,
    F: Fn(&M::State) -> K,
{
    proposal.assumptions.validate()?;
    if proposal.name.trim().is_empty() || proposal.build.trim().is_empty() || proposal.version == 0
    {
        return Err(ModelError::new(
            "reduction proposal needs a name, positive version, and build identity",
        ));
    }
    let mut unique_properties = BTreeSet::new();
    for property in &proposal.required_properties {
        if property.id.trim().is_empty() || !unique_properties.insert(property.clone()) {
            return Err(ModelError::new(
                "empty or duplicate expected research property",
            ));
        }
    }
    let mut report = ExperimentReport {
        model:model.metadata(), proposal, config, status:ExperimentStatus::Disabled,
        declared_states:states.len(), materialized_edges:0, compared_pairs:0,
        exact:None, reduced:None, counterexamples:Vec::new(), missing_violations:Vec::new(), missing_properties:Vec::new(),
        limitation:"No production reduction is enabled. Evidence applies only to the explicit finite fixture and its assumptions.".into(),
    };
    if !config.enabled {
        return Ok(report);
    }
    if !report.proposal.assumptions.established {
        report.status = ExperimentStatus::AssumptionsNotEstablished;
        return Ok(report);
    }
    report.status = ExperimentStatus::Incomplete;
    if states.is_empty() || states.len() > config.max_states {
        report.limitation =
            "The explicit state domain is empty or exceeds the state budget.".into();
        return Ok(report);
    }
    for (index, state) in states.iter().enumerate() {
        if states[..index].contains(state) {
            return Err(ModelError::new(
                "declared research domain contains duplicate exact states",
            ));
        }
    }
    let graph = match materialize(
        model,
        states,
        config.max_edges,
        &mut report.materialized_edges,
    ) {
        Ok(graph) => graph,
        Err(error) => {
            report.limitation = error.to_string();
            return Ok(report);
        }
    };
    let keys: Vec<_> = states.iter().map(projection).collect();
    let (exact, paths) = reach(&graph, |left, right| left == right);
    let (reduced, _) = reach(&graph, |left, right| keys[left] == keys[right]);
    let reduced_failures: BTreeSet<_> = reduced
        .violations
        .iter()
        .map(|v| v.property.clone())
        .collect();
    report.missing_violations = exact
        .violations
        .iter()
        .filter(|v| !reduced_failures.contains(&v.property))
        .cloned()
        .collect();
    report.missing_properties = report
        .proposal
        .required_properties
        .iter()
        .filter(|p| !exact.observed_properties.contains(*p))
        .cloned()
        .collect();
    let mut pair_limited = false;
    'outer: for (position, &left) in exact.states.iter().enumerate() {
        for &right in &exact.states[position + 1..] {
            if keys[left] != keys[right] {
                continue;
            }
            if report.compared_pairs == config.max_equivalence_pairs {
                pair_limited = true;
                break 'outer;
            }
            report.compared_pairs += 1;
            if let Some(reason) = compare_states(&graph, &keys, left, right) {
                report.counterexamples.push(EquivalenceCounterexample {
                    left_state: left,
                    right_state: right,
                    left_prefix: paths[left].clone().unwrap(),
                    right_prefix: paths[right].clone().unwrap(),
                    reason,
                });
            }
        }
    }
    let projected_reachability_agrees = exact.states.iter().all(|&state| {
        reduced
            .states
            .iter()
            .any(|&other| keys[state] == keys[other])
    });
    let reduced_only_failure = reduced
        .violations
        .iter()
        .any(|v| !exact.violations.iter().any(|e| e.property == v.property));
    let has_counterexample = !report.counterexamples.is_empty()
        || !report.missing_violations.is_empty()
        || !projected_reachability_agrees
        || reduced_only_failure;
    if has_counterexample {
        report.status = ExperimentStatus::Counterexample;
        report.limitation="The proposed equivalence changes observable behavior or property reachability in this finite fixture; it must not be used for pruning.".into();
    } else if pair_limited {
        report.limitation =
            "Equivalence-pair budget reached; unchecked pairs cannot be assumed equivalent.".into();
    } else if !report.missing_properties.is_empty()
        || exact.skipped_checks > 0
        || reduced.skipped_checks > 0
        || report.proposal.required_properties.is_empty()
    {
        report.limitation="Expected properties were absent or checks were skipped; equivalence qualification is incomplete.".into();
    } else {
        report.status = ExperimentStatus::AgreesWithinDeclaredFixture;
    }
    report.exact = Some(exact);
    report.reduced = Some(reduced);
    Ok(report)
}
fn materialize<M: Enumerate>(
    model: &M,
    states: &[M::State],
    max_edges: u64,
    executed: &mut u64,
) -> Result<Graph<M::Input, M::Output>, ModelError> {
    let initial = model.initial_state()?;
    let initial = states
        .iter()
        .position(|s| *s == initial)
        .ok_or_else(|| ModelError::new("initial state is outside the declared finite domain"))?;
    let mut checks = Vec::with_capacity(states.len());
    let mut edges = Vec::with_capacity(states.len());
    for state in states {
        let mut state_checks = Vec::new();
        model.check_state_into(state, &mut CheckSink::new(&mut state_checks))?;
        checks.push(state_checks);
        let mut outgoing = Vec::new();
        for input in model.input_iter(state)? {
            if *executed == max_edges {
                return Err(ModelError::new(
                    "edge materialization budget reached; finite graph not completed",
                ));
            }
            let transition = model.step(state, &input)?;
            *executed += 1;
            let target = states
                .iter()
                .position(|s| *s == transition.state)
                .ok_or_else(|| {
                    ModelError::new("successor is outside the declared finite domain")
                })?;
            let mut observations = Vec::new();
            model.check_transition_into(
                state,
                &input,
                &transition.as_ref(),
                &mut CheckSink::new(&mut observations),
            )?;
            outgoing.push(Edge {
                input,
                target,
                outputs: transition.outputs,
                disposition: transition.disposition,
                checks: observations,
            });
        }
        edges.push(outgoing);
    }
    Ok(Graph {
        initial,
        checks,
        edges,
    })
}
fn compare_states<I: Eq, O: Eq, K: Eq>(
    graph: &Graph<I, O>,
    keys: &[K],
    left: usize,
    right: usize,
) -> Option<String> {
    if graph.checks[left] != graph.checks[right] {
        return Some("state checks differ".into());
    }
    if graph.edges[left].len() != graph.edges[right].len() {
        return Some("enabled input counts differ".into());
    }
    for (a, b) in graph.edges[left].iter().zip(&graph.edges[right]) {
        if a.input != b.input {
            return Some("enabled input labels or order differ".into());
        }
        if a.outputs != b.outputs {
            return Some("ordered effects differ".into());
        }
        if a.disposition != b.disposition {
            return Some("transition disposition differs".into());
        }
        if a.checks != b.checks {
            return Some("transition checks differ".into());
        }
        if keys[a.target] != keys[b.target] {
            return Some("successor equivalence classes differ".into());
        }
    }
    None
}
fn observe_checks<I: Clone>(
    checks: &[Check],
    phase: PropertyPhase,
    inputs: &[I],
    seen: &mut BTreeSet<RequiredProperty>,
    report: &mut Reachability<I>,
) {
    for check in checks {
        let property = RequiredProperty {
            id: check.id.to_string(),
            phase,
        };
        report.observed_properties.insert(property.clone());
        match &check.status {
            CheckStatus::Failed(details) if seen.insert(property.clone()) => {
                report.violations.push(ViolationWitness {
                    property,
                    details: details.clone(),
                    inputs: inputs.to_vec(),
                })
            }
            CheckStatus::Skipped(_) => {
                report.skipped_checks = report.skipped_checks.saturating_add(1)
            }
            _ => {}
        }
    }
}
#[allow(clippy::type_complexity)]
fn reach<I: Clone, O>(
    graph: &Graph<I, O>,
    equivalent: impl Fn(usize, usize) -> bool,
) -> (Reachability<I>, Vec<Option<Vec<I>>>) {
    let mut report = Reachability {
        states: vec![graph.initial],
        edges: 0,
        violations: Vec::new(),
        observed_properties: BTreeSet::new(),
        skipped_checks: 0,
    };
    let mut paths = vec![None; graph.edges.len()];
    paths[graph.initial] = Some(Vec::new());
    let mut queue = VecDeque::from([graph.initial]);
    let mut seen = BTreeSet::new();
    observe_checks(
        &graph.checks[graph.initial],
        PropertyPhase::State,
        &[],
        &mut seen,
        &mut report,
    );
    while let Some(source) = queue.pop_front() {
        for edge in &graph.edges[source] {
            report.edges += 1;
            let mut path = paths[source].clone().unwrap();
            path.push(edge.input.clone());
            observe_checks(
                &graph.checks[edge.target],
                PropertyPhase::State,
                &path,
                &mut seen,
                &mut report,
            );
            observe_checks(
                &edge.checks,
                PropertyPhase::Transition,
                &path,
                &mut seen,
                &mut report,
            );
            if report
                .states
                .iter()
                .any(|&old| equivalent(old, edge.target))
            {
                continue;
            }
            paths[edge.target] = Some(path);
            report.states.push(edge.target);
            queue.push_back(edge.target);
        }
    }
    (report, paths)
}

/// Summarize fixture qualification without silently enabling a proposed reduction.
pub fn qualification_parameters<I>(report: &ExperimentReport<I>) -> BTreeMap<String, String> {
    BTreeMap::from([
        ("research.proposal".into(), report.proposal.name.clone()),
        (
            "research.proposal_version".into(),
            report.proposal.version.to_string(),
        ),
        (
            "research.proposal_build".into(),
            report.proposal.build.clone(),
        ),
        ("research.model_build".into(), report.model.build.clone()),
        ("research.status".into(), format!("{:?}", report.status)),
        (
            "research.declared_states".into(),
            report.declared_states.to_string(),
        ),
        (
            "research.materialized_edges".into(),
            report.materialized_edges.to_string(),
        ),
        (
            "research.compared_pairs".into(),
            report.compared_pairs.to_string(),
        ),
        (
            "research.finite_domain".into(),
            report.proposal.assumptions.finite_domain.clone(),
        ),
        (
            "research.equivalence".into(),
            report.proposal.assumptions.equivalence.clone(),
        ),
        (
            "research.effects".into(),
            report.proposal.assumptions.effects.clone(),
        ),
        (
            "research.transition_properties".into(),
            report.proposal.assumptions.transition_properties.clone(),
        ),
        (
            "research.queue_order".into(),
            report.proposal.assumptions.queue_order.clone(),
        ),
        (
            "research.oracle_history".into(),
            report.proposal.assumptions.oracle_history.clone(),
        ),
        (
            "research.visible_timing".into(),
            report.proposal.assumptions.visible_timing.clone(),
        ),
        ("research.limitation".into(), report.limitation.clone()),
        ("research.production_pruning_enabled".into(), "false".into()),
    ])
}

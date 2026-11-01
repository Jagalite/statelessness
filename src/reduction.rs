//! reduction-v1 (S4); see spec/reduction-v1.md. Action-equivalence grouping of
//! edges from the same node ([`EquivalenceReduction`], applied by
//! [`crate::graph::explore_graph`]), enabled-action diamond validation, and a
//! reduced versus unreduced comparison. No independence-based pruning is
//! introduced. Reduction never skips executing or checking an edge, and
//! research-equivalence-v1 evidence does not authorize reduction.

use std::cell::RefCell;
use std::collections::hash_map::RandomState;
use std::collections::{BTreeSet, HashMap};
use std::fmt;
use std::hash::{BuildHasher, Hash};

use crate::explore::{Control, RunLimits, Stop, context};
use crate::graph::{
    EdgeTarget, GraphCoverage, GraphEdge, GraphEdgeId, GraphNodeId, GraphSearchReport,
    NodeExpansion,
};
use crate::model::{CheckSink, Enumerate, ModelError, Transition, TransitionRef};
use crate::progress::{ProgressGraph, ProgressKind, ProgressReport, analyze_progress};

/// Application-supplied equivalence classes for edges from one node.
pub trait EquivalenceReduction<S, I, O> {
    /// The eligible equivalence class, or None when the edge is not a candidate.
    fn eligible_class(&mut self, state: &S, input: &I) -> Result<Option<String>, ModelError>;
    /// Deterministic bytes for the ordered effects and progress annotations
    /// that equivalence must preserve. Receives the edge's labels.
    fn projection(
        &mut self,
        input: &I,
        transition: &TransitionRef<'_, S, O>,
        labels: &[String],
    ) -> Result<Vec<u8>, ModelError>;
}

/// Why a compared representative did not absorb an edge, in comparison order.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum RejectionCause {
    DispositionDiffers,
    ChecksDiffer,
    ProjectionDiffers,
    SuccessorDiffers,
}

impl RejectionCause {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::DispositionDiffers => "disposition_differs",
            Self::ChecksDiffer => "checks_differ",
            Self::ProjectionDiffers => "projection_differs",
            Self::SuccessorDiffers => "successor_differs",
        }
    }
}

impl fmt::Display for RejectionCause {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RejectedEquivalence {
    pub representative: GraphEdgeId,
    pub reason: RejectionCause,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReductionDecision {
    pub edge: GraphEdgeId,
    pub source: GraphNodeId,
    pub equivalence_class: String,
    /// None: the edge became a representative of its class.
    pub reduced_to: Option<GraphEdgeId>,
    /// Representatives compared before the decision, in comparison order.
    pub rejected: Vec<RejectedEquivalence>,
}

// MARK: Diamond validation

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum DiamondTermination {
    SearchComplete,
    Cancelled,
    Deadline,
}

impl DiamondTermination {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::SearchComplete => "search_complete",
            Self::Cancelled => "cancelled",
            Self::Deadline => "deadline",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum DiamondDifference {
    StateDiffers,
    ObservationsDiffer,
}

impl DiamondDifference {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::StateDiffers => "state_differs",
            Self::ObservationsDiffer => "observations_differ",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DiamondWitness<I> {
    pub node: GraphNodeId,
    pub first: I,
    pub second: I,
    pub reason: DiamondDifference,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DiamondReport<I> {
    pub termination: DiamondTermination,
    pub checked_nodes: usize,
    pub candidate_pairs: u64,
    /// Pairs where each input stayed enabled after the other.
    pub validated: u64,
    pub non_commuting: Vec<DiamondWitness<I>>,
}

type Observation = (Vec<(&'static str, String)>, Vec<u8>);

/// For each completely expanded node, executes both orders of every pair of
/// candidate inputs (in enumeration order) that remain enabled after each
/// other, and compares the final states by model equality and the two steps'
/// observations: the set of failed (phase, check ID) pairs and the multiset of
/// projection bytes. Evidence about independence, never permission to prune.
///
/// Enabled membership uses the input domain and the model's `Eq` on inputs.
pub fn validate_diamonds<M: Enumerate>(
    model: &M,
    report: &GraphSearchReport<M::State, M::Input, M::Output>,
    mut candidate: impl FnMut(&M::State, &M::Input) -> Result<bool, ModelError>,
    mut projection: impl FnMut(
        &M::Input,
        &TransitionRef<'_, M::State, M::Output>,
    ) -> Result<Vec<u8>, ModelError>,
    limits: RunLimits<'_>,
) -> Result<DiamondReport<M::Input>, ModelError> {
    let control = Control::new(limits)?;
    let mut out = DiamondReport {
        termination: DiamondTermination::SearchComplete,
        checked_nodes: 0,
        candidate_pairs: 0,
        validated: 0,
        non_commuting: Vec::new(),
    };
    let stop = |out: &mut DiamondReport<M::Input>| match control.stop() {
        Some(stop) => {
            out.termination = match stop {
                Stop::Deadline => DiamondTermination::Deadline,
                Stop::Cancelled => DiamondTermination::Cancelled,
            };
            true
        }
        None => false,
    };
    let enumerate = |state: &M::State| -> Result<Vec<M::Input>, ModelError> {
        Ok(model
            .input_iter(state)
            .map_err(|e| context("enumerate inputs", e))?
            .collect())
    };
    let enabled = |input: &M::Input, state: &M::State| -> Result<bool, ModelError> {
        Ok(enumerate(state)?.contains(input))
    };
    let step = |state: &M::State, input: &M::Input| {
        model.step(state, input).map_err(|e| context("step", e))
    };
    let mut observe = |before: &M::State,
                       input: &M::Input,
                       t: &Transition<M::State, M::Output>|
     -> Result<Observation, ModelError> {
        let mut checks = Vec::new();
        model
            .check_state_into(&t.state, &mut CheckSink::new(&mut checks))
            .map_err(|e| context("check_state", e))?;
        let state_count = checks.len();
        model
            .check_transition_into(before, input, &t.as_ref(), &mut CheckSink::new(&mut checks))
            .map_err(|e| context("check_transition", e))?;
        let failed = checks
            .iter()
            .enumerate()
            .filter(|(_, check)| check.is_failure())
            .map(|(index, check)| {
                let phase = if index < state_count {
                    "state"
                } else {
                    "transition"
                };
                (phase, check.id.as_str().to_owned())
            })
            .collect();
        let bytes = projection(input, &t.as_ref()).map_err(|e| context("diamond projection", e))?;
        Ok((failed, bytes))
    };
    for node in &report.nodes {
        if node.expansion != NodeExpansion::Complete {
            continue;
        }
        if stop(&mut out) {
            return Ok(out);
        }
        out.checked_nodes += 1;
        let mut candidates = Vec::new();
        for input in enumerate(&node.state)? {
            if candidate(&node.state, &input).map_err(|e| context("diamond candidate", e))? {
                candidates.push(input);
            }
        }
        for i in 0..candidates.len() {
            for j in i + 1..candidates.len() {
                if stop(&mut out) {
                    return Ok(out);
                }
                out.candidate_pairs += 1;
                let (a, b) = (&candidates[i], &candidates[j]);
                let ta = step(&node.state, a)?;
                let tb = step(&node.state, b)?;
                if !(enabled(b, &ta.state)? && enabled(a, &tb.state)?) {
                    continue;
                }
                let tab = step(&ta.state, b)?;
                let tba = step(&tb.state, a)?;
                out.validated += 1;
                let same = tab.state == tba.state;
                let left = [observe(&node.state, a, &ta)?, observe(&ta.state, b, &tab)?];
                let right = [observe(&node.state, b, &tb)?, observe(&tb.state, a, &tba)?];
                let failures = |side: &[Observation; 2]| -> BTreeSet<(&'static str, String)> {
                    side.iter().flat_map(|o| o.0.iter().cloned()).collect()
                };
                let projections = |side: &[Observation; 2]| {
                    let mut bytes: Vec<&Vec<u8>> = side.iter().map(|o| &o.1).collect();
                    bytes.sort();
                    bytes.into_iter().cloned().collect::<Vec<_>>()
                };
                let same_failures = failures(&left) == failures(&right);
                let same_projections = projections(&left) == projections(&right);
                if !same || !same_failures || !same_projections {
                    out.non_commuting.push(DiamondWitness {
                        node: node.id,
                        first: a.clone(),
                        second: b.clone(),
                        reason: if same {
                            DiamondDifference::ObservationsDiffer
                        } else {
                            DiamondDifference::StateDiffers
                        },
                    });
                }
            }
        }
    }
    Ok(out)
}

// MARK: Reduced versus unreduced comparison

/// Application progress predicates (progress-analysis-v1) applied to both
/// graphs. Kind and rank take states so they agree across the two reports.
pub trait ReductionProgressPredicates<S, I, O> {
    fn fair(&mut self, edge: &GraphEdge<I, O>) -> Result<bool, ModelError>;
    fn kind(&mut self, state: &S) -> Result<ProgressKind, ModelError>;
    /// Ranks default to zero.
    fn rank(&mut self, _state: &S) -> Result<u64, ModelError> {
        Ok(0)
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ReductionComparison {
    /// Unreduced node IDs with no equal reduced node, and the reverse.
    pub missing_in_reduced: Vec<GraphNodeId>,
    pub extra_in_reduced: Vec<GraphNodeId>,
    /// Unreduced edges whose (source, target, disposition, checks) class has
    /// no reduced representative, and the reverse.
    pub unrepresented_edges: Vec<GraphEdgeId>,
    pub extra_reduced_edges: Vec<GraphEdgeId>,
    pub findings_match: bool,
    pub coverage_match: bool,
    /// None when no progress predicates were supplied: progress was not
    /// compared and the comparison establishes no progress evidence.
    pub progress_match: Option<bool>,
    /// Unreduced nodes whose progress class, settlement or closed-component
    /// membership differs from their reduced counterpart.
    pub progress_mismatches: Vec<GraphNodeId>,
}

impl ReductionComparison {
    /// Progress that was not compared does not block equivalence; it is absent
    /// evidence and is reported as such through `progress_match == None`.
    pub fn equivalent(&self) -> bool {
        self.missing_in_reduced.is_empty()
            && self.extra_in_reduced.is_empty()
            && self.unrepresented_edges.is_empty()
            && self.extra_reduced_edges.is_empty()
            && self.findings_match
            && self.coverage_match
            && self.progress_match != Some(false)
    }
}

fn resolved<S, I, O>(
    report: &GraphSearchReport<S, I, O>,
    edge: &GraphEdge<I, O>,
) -> Option<GraphNodeId> {
    let mut target = edge.target;
    let mut hops = 0;
    while let EdgeTarget::Reduced(representative) = target {
        if hops > report.edges.len() {
            break;
        }
        let Some(next) = report.edges.get(representative) else {
            break;
        };
        target = next.target;
        hops += 1;
    }
    match target {
        EdgeTarget::Admitted(node) | EdgeTarget::Duplicate(node) => Some(node),
        _ => None,
    }
}

/// Compares reachable states by hash bucket plus model equality (`Eq`),
/// represented edge classes `(source, resolved target, disposition, state
/// checks, transition checks)` in both directions, finding counts, failed
/// property counts, coverage and, when predicates are supplied, progress
/// results. Both reports must retain edges and must not be incomplete. The
/// check is quadratic and intended for exhaustive small bounds.
pub fn compare_reduction<S: Eq + Hash, I, O>(
    unreduced: &GraphSearchReport<S, I, O>,
    reduced: &GraphSearchReport<S, I, O>,
    progress: Option<&mut dyn ReductionProgressPredicates<S, I, O>>,
) -> Result<ReductionComparison, ModelError> {
    for report in [unreduced, reduced] {
        if !report.edges_retained {
            return Err(ModelError::new(
                "reduction comparison requires retained edges",
            ));
        }
        if matches!(report.coverage, GraphCoverage::Incomplete(_)) {
            return Err(ModelError::new(
                "reduction comparison requires finished searches",
            ));
        }
    }
    let hashing = RandomState::new();
    let mut buckets: HashMap<u64, Vec<GraphNodeId>> = HashMap::new();
    for node in &reduced.nodes {
        buckets
            .entry(hashing.hash_one(&node.state))
            .or_default()
            .push(node.id);
    }
    let mut out = ReductionComparison {
        findings_match: true,
        coverage_match: unreduced.coverage == reduced.coverage,
        ..ReductionComparison::default()
    };
    let mut mapping: Vec<Option<GraphNodeId>> = vec![None; unreduced.nodes.len()];
    let mut matched = BTreeSet::new();
    for (index, node) in unreduced.nodes.iter().enumerate() {
        let bucket = buckets.get(&hashing.hash_one(&node.state));
        for &other in bucket.into_iter().flatten() {
            if reduced
                .nodes
                .get(other)
                .is_some_and(|r| r.state == node.state)
            {
                mapping[index] = Some(other);
                matched.insert(other);
                break;
            }
        }
        if mapping[index].is_none() {
            out.missing_in_reduced.push(node.id);
        }
    }
    out.extra_in_reduced = reduced
        .nodes
        .iter()
        .map(|node| node.id)
        .filter(|id| !matched.contains(id))
        .collect();
    let mut reverse: Vec<Option<GraphNodeId>> = vec![None; reduced.nodes.len()];
    for (u, r) in mapping.iter().enumerate() {
        if let Some(r) = *r
            && let Some(slot) = reverse.get_mut(r)
        {
            *slot = Some(u);
        }
    }
    let at =
        |table: &[Option<GraphNodeId>], index: GraphNodeId| table.get(index).copied().flatten();
    let same = |a: &GraphEdge<I, O>, b: &GraphEdge<I, O>| {
        a.disposition == b.disposition
            && a.state_checks == b.state_checks
            && a.transition_checks == b.transition_checks
    };
    // An edge resolved to a node without a counterpart is never represented.
    // Some(None): resolved nowhere (not admitted); None: no counterpart.
    let mapped = |report: &GraphSearchReport<S, I, O>,
                  edge: &GraphEdge<I, O>,
                  table: &[Option<GraphNodeId>]|
     -> Option<Option<GraphNodeId>> {
        match resolved(report, edge) {
            None => Some(None),
            Some(node) => at(table, node).map(Some),
        }
    };
    let reduced_edges: Vec<&GraphEdge<I, O>> = reduced
        .edges
        .iter()
        .filter(|edge| !matches!(edge.target, EdgeTarget::Reduced(_)))
        .collect();
    for edge in &unreduced.edges {
        let (Some(source), Some(target)) =
            (at(&mapping, edge.source), mapped(unreduced, edge, &mapping))
        else {
            out.unrepresented_edges.push(edge.id);
            continue;
        };
        if !reduced_edges.iter().any(|other| {
            other.source == source && resolved(reduced, other) == target && same(other, edge)
        }) {
            out.unrepresented_edges.push(edge.id);
        }
    }
    for edge in &reduced_edges {
        let represented = match (at(&reverse, edge.source), mapped(reduced, edge, &reverse)) {
            (Some(source), Some(target)) => unreduced.edges.iter().any(|other| {
                other.source == source && resolved(unreduced, other) == target && same(other, edge)
            }),
            _ => false,
        };
        if !represented {
            out.extra_reduced_edges.push(edge.id);
        }
    }
    out.findings_match = unreduced.finding_count == reduced.finding_count
        && unreduced.failed_properties == reduced.failed_properties;
    if let Some(predicates) = progress {
        let predicates = RefCell::new(predicates);
        let analyze = |report: &GraphSearchReport<S, I, O>| -> Result<ProgressReport, ModelError> {
            let graph =
                ProgressGraph::from_report(report, |edge| predicates.borrow_mut().fair(edge))?;
            analyze_progress(
                &graph,
                |n| predicates.borrow_mut().kind(&report.nodes[n].state),
                Some(&mut |n| predicates.borrow_mut().rank(&report.nodes[n].state)),
            )
        };
        let u = analyze(unreduced)?;
        let r = analyze(reduced)?;
        let component = |report: &ProgressReport, n: GraphNodeId| {
            report
                .closed_nonterminal_components
                .iter()
                .position(|c| c.contains(&n))
        };
        let u_unsettled: BTreeSet<_> = u.unsettled_transaction_states.iter().copied().collect();
        let r_unsettled: BTreeSet<_> = r.unsettled_transaction_states.iter().copied().collect();
        // Closed components must correspond member for member under the mapping.
        let u_closed: BTreeSet<BTreeSet<GraphNodeId>> = u
            .closed_nonterminal_components
            .iter()
            .map(|c| c.iter().filter_map(|&n| at(&mapping, n)).collect())
            .collect();
        let r_closed: BTreeSet<BTreeSet<GraphNodeId>> = r
            .closed_nonterminal_components
            .iter()
            .map(|c| c.iter().copied().collect())
            .collect();
        for n in 0..unreduced.nodes.len() {
            let Some(k) = at(&mapping, n) else {
                continue;
            };
            if u.classes[n] != r.classes[k]
                || u_unsettled.contains(&n) != r_unsettled.contains(&k)
                || component(&u, n).is_none() != component(&r, k).is_none()
            {
                out.progress_mismatches.push(n);
            }
        }
        out.progress_match = Some(
            out.progress_mismatches.is_empty()
                && u_closed == r_closed
                && u.graph_complete == r.graph_complete,
        );
    }
    Ok(out)
}

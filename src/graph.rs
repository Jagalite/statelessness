//! graph-search-v1 (S1 admitted graph and complete search reporting, S2 failure
//! collection) and the in-search half of reduction-v1 (S4); see
//! spec/graph-search-v1.md and spec/reduction-v1.md.
//!
//! With default [`GraphSearchOptions`] the model callback sequence is exactly
//! that of [`crate::explore::enumerate_observed`] (bfs-controls-v1), and the
//! report carries the same counters and witness. `enumerate*` is unchanged and
//! remains the portable bfs-v1 entry point; this module is additive and opt-in.
//! Graph capture clones retained inputs, outputs and states with `Clone`; those
//! copies are not model callbacks.

use std::collections::HashMap;
use std::collections::hash_map::RandomState;
use std::fmt;
use std::hash::Hash;
use std::rc::Rc;

use crate::explore::{
    CheckPhase, Control, Failure, Observing, OpsObserver, PropertyFailure, SearchConfig,
    SearchLimits, SearchObserver, Stop, Unobserved, context, initial_checks_retained,
    transition_checks_retained,
};
use crate::model::{
    Check, CheckStatus, Disposition, Enumerate, ModelError, PropertyId, Transition, TransitionRef,
};
use crate::operations::{EnumerateOps, InputCursor, ModelOps, NativeOps};
use crate::reduction::{
    EquivalenceReduction, ReductionDecision, RejectedEquivalence, RejectionCause,
};

/// Run-local, dense, equal to admission order; the root is 0.
pub type GraphNodeId = usize;
/// Run-local, dense, in evaluation order, counting every executed edge.
pub type GraphEdgeId = usize;

/// bfs-v1 terminations plus `graph_retention_limit` and `model_error`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum GraphTermination {
    GraphExhausted,
    DepthBound,
    StateLimit,
    TransitionLimit,
    FailureFound,
    EstimatedStateByteLimit,
    Deadline,
    Cancelled,
    GraphRetentionLimit,
    /// Only on error returns ([`GraphSearchError`]).
    ModelError,
}

impl GraphTermination {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::GraphExhausted => "graph_exhausted",
            Self::DepthBound => "depth_bound",
            Self::StateLimit => "state_limit",
            Self::TransitionLimit => "transition_limit",
            Self::FailureFound => "failure_found",
            Self::EstimatedStateByteLimit => "estimated_state_byte_limit",
            Self::Deadline => "deadline",
            Self::Cancelled => "cancelled",
            Self::GraphRetentionLimit => "graph_retention_limit",
            Self::ModelError => "model_error",
        }
    }
    fn from_stop(stop: Stop) -> Self {
        match stop {
            Stop::Deadline => Self::Deadline,
            Stop::Cancelled => Self::Cancelled,
        }
    }
}

impl fmt::Display for GraphTermination {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Why an evaluated successor was not admitted.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum GraphRejection {
    StateLimit,
    EstimatedStateByteLimit,
    GraphRetentionLimit,
    FailingSuccessor,
    Cancelled,
    Deadline,
    FailureFound,
    ModelError,
}

impl GraphRejection {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::StateLimit => "state_limit",
            Self::EstimatedStateByteLimit => "estimated_state_byte_limit",
            Self::GraphRetentionLimit => "graph_retention_limit",
            Self::FailingSuccessor => "failing_successor",
            Self::Cancelled => "cancelled",
            Self::Deadline => "deadline",
            Self::FailureFound => "failure_found",
            Self::ModelError => "model_error",
        }
    }
}

impl fmt::Display for GraphRejection {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FailurePolicy {
    /// bfs-v1: the first failing check batch ends the search.
    StopAtFirst,
    /// Record every failing batch. `max_retained_findings` bounds retained
    /// witnesses only; counts and failed property identities are never dropped.
    /// Without `expand_failing_successors`, a new successor reached by a failing
    /// edge is rejected (`failing_successor`) and coverage is failure-pruned.
    Collect {
        max_retained_findings: usize,
        expand_failing_successors: bool,
    },
}

/// Exceeding either cap ends the search with `graph_retention_limit`.
/// `max_graph_bytes` bounds engine bookkeeping for nodes and retained edges; it
/// excludes model state representation (see `estimated_retained_state_bytes`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct GraphRetention {
    pub retain_edges: bool,
    pub max_retained_edges: Option<usize>,
    pub max_graph_bytes: Option<u64>,
}

impl Default for GraphRetention {
    fn default() -> Self {
        Self {
            retain_edges: true,
            max_retained_edges: None,
            max_graph_bytes: None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum NodeExpansion {
    /// Still queued when the search ended.
    Unexpanded,
    /// Every enumerated input was evaluated and its successor resolved; or at
    /// max_depth the input domain was empty.
    Complete,
    /// At max_depth with at least one unexamined input.
    DepthFrontier,
    /// A failing initial state under `Collect { expand_failing_successors: false }`.
    FailurePruned,
    /// Expansion started but the search ended before it finished.
    Interrupted(GraphTermination),
}

impl NodeExpansion {
    /// Fixture spelling (`finish:NODE:STATUS`): `unexpanded`, `complete`,
    /// `depthFrontier`, `failurePruned` or `interrupted`.
    pub fn name(self) -> &'static str {
        match self {
            Self::Unexpanded => "unexpanded",
            Self::Complete => "complete",
            Self::DepthFrontier => "depthFrontier",
            Self::FailurePruned => "failurePruned",
            Self::Interrupted(_) => "interrupted",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EdgeTarget {
    /// Only observed in `EdgeEvaluated` events; resolved before reports return.
    Pending,
    Admitted(GraphNodeId),
    Duplicate(GraphNodeId),
    /// reduction-v1: collapsed into an equivalent representative edge.
    Reduced(GraphEdgeId),
    /// Evaluated but not admitted: a cap, `failing_successor`, or a stop reason.
    Rejected(GraphRejection),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GraphNode<S, I> {
    pub id: GraphNodeId,
    pub state: S,
    pub parent: Option<GraphNodeId>,
    pub input: Option<I>,
    pub admitting_edge: Option<GraphEdgeId>,
    pub depth: usize,
    /// Initial checks for the root; otherwise the admitting edge's state checks.
    pub checks: Vec<Check>,
    /// True when any check batch on the root path, including this node's, failed.
    pub path_failed: bool,
    pub expansion: NodeExpansion,
}

/// Reports are values; callers may rewrite a copy, e.g. for negative controls.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GraphEdge<I, O> {
    pub id: GraphEdgeId,
    pub source: GraphNodeId,
    pub input: I,
    pub disposition: Disposition,
    pub outputs: Vec<O>,
    pub state_checks: Vec<Check>,
    pub transition_checks: Vec<Check>,
    pub annotations: Vec<String>,
    pub target: EdgeTarget,
}

impl<I, O> GraphEdge<I, O> {
    pub fn failed(&self) -> bool {
        self.state_checks.iter().any(Check::is_failure)
            || self.transition_checks.iter().any(Check::is_failure)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct GraphFinding<I> {
    pub index: u64,
    /// Root-to-edge inputs; empty for an initial-state finding.
    pub witness: Vec<I>,
    /// Every failed check of the batch, state checks before transition checks.
    pub violations: Vec<PropertyFailure>,
    /// None for an initial-state finding.
    pub edge: Option<GraphEdgeId>,
    /// The witness prefix crosses an earlier failure; reproducing it requires
    /// continued-failure-evidence-v1 rather than stop-at-failure replay.
    pub requires_continued_evidence: bool,
}

/// Stable `(phase, check ID)` identity of a failed property, never truncated.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PropertyFailureCount {
    pub phase: CheckPhase,
    pub id: PropertyId,
    pub count: u64,
    pub first_finding: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum GraphCoverage {
    /// The whole reachable finite graph was expanded.
    Exhaustive,
    /// Exploration finished, but only within the declared depth and/or with
    /// failing successors left unexpanded. Admissible only as bounded coverage.
    Bounded {
        depth_cutoff: bool,
        failure_pruned: bool,
    },
    /// Stopped by a property failure, cap, cancellation, deadline, retention or error.
    Incomplete(GraphTermination),
}

/// Synchronous search events, in graph-search-v1 order. Payloads are borrowed
/// for the duration of the callback; the sink cannot change engine evidence.
#[derive(Debug)]
pub enum GraphEvent<'a, I, O> {
    RootAdmitted(GraphNodeId),
    ExpansionStarted(GraphNodeId),
    EdgeEvaluated(&'a GraphEdge<I, O>),
    Finding {
        index: u64,
        edge: Option<GraphEdgeId>,
        violations: &'a [PropertyFailure],
    },
    SuccessorAdmitted {
        edge: GraphEdgeId,
        node: GraphNodeId,
    },
    DuplicateEdge {
        edge: GraphEdgeId,
        existing: GraphNodeId,
    },
    EdgeReduced {
        edge: GraphEdgeId,
        representative: GraphEdgeId,
    },
    SuccessorRejected {
        edge: GraphEdgeId,
        reason: GraphRejection,
    },
    ExpansionFinished {
        node: GraphNodeId,
        status: NodeExpansion,
    },
    LayerCompleted {
        depth: usize,
    },
    SearchFinished {
        termination: GraphTermination,
    },
}

/// Synchronous event sink. An error aborts the search with [`GraphSearchError`].
pub type GraphEventSink<'o, I, O> =
    dyn for<'e> FnMut(GraphEvent<'e, I, O>) -> Result<(), ModelError> + 'o;
/// User edge labels, called after all checks of the edge.
pub type EdgeAnnotator<'o, S, I, O> =
    dyn FnMut(&S, &I, &TransitionRef<'_, S, O>) -> Result<Vec<String>, ModelError> + 'o;

pub struct GraphSearchOptions<'o, S, I, O> {
    pub failure_policy: FailurePolicy,
    pub retention: GraphRetention,
    pub events: Option<&'o mut GraphEventSink<'o, I, O>>,
    pub annotate_edge: Option<&'o mut EdgeAnnotator<'o, S, I, O>>,
    pub reduction: Option<&'o mut (dyn EquivalenceReduction<S, I, O> + 'o)>,
}

impl<S, I, O> Default for GraphSearchOptions<'_, S, I, O> {
    fn default() -> Self {
        Self {
            failure_policy: FailurePolicy::StopAtFirst,
            retention: GraphRetention::default(),
            events: None,
            annotate_edge: None,
            reduction: None,
        }
    }
}

#[derive(Clone, Debug)]
pub struct GraphSearchReport<S, I, O> {
    pub termination: GraphTermination,
    pub coverage: GraphCoverage,
    /// bfs-v1 meaning: admitted states, or 1 once the initial state exists.
    pub states: usize,
    /// Evaluated transitions, including duplicate, reduced and rejected edges.
    pub transitions: u64,
    pub max_depth_reached: usize,
    pub skipped_checks: u64,
    pub estimated_retained_state_bytes: Option<usize>,
    pub nodes: Vec<GraphNode<S, I>>,
    /// Indexed by edge ID while `edges_retained`.
    pub edges: Vec<GraphEdge<I, O>>,
    pub edges_retained: bool,
    /// Edges between admitted nodes (admitted plus duplicate targets).
    pub graph_edges: u64,
    pub duplicate_edges: u64,
    pub reduced_edges: u64,
    /// Includes an edge still unresolved when the search ended, retained or not.
    pub rejected_successors: u64,
    /// Admitted nodes whose expansion is not `Complete`.
    pub frontier_size: usize,
    pub completed_layers: usize,
    pub depth_cutoff: bool,
    pub failure_pruned: bool,
    /// Saturating engine bookkeeping estimate for nodes and retained edges.
    /// Separate from `estimated_retained_state_bytes`; neither is process RSS.
    pub estimated_graph_bytes: u64,
    pub findings: Vec<GraphFinding<I>>,
    pub finding_count: u64,
    pub failed_properties: Vec<PropertyFailureCount>,
    pub reduction_decisions: Vec<ReductionDecision>,
}

impl<S, I: Clone, O> GraphSearchReport<S, I, O> {
    pub fn findings_truncated(&self) -> bool {
        self.finding_count > self.findings.len() as u64
    }
    /// The first retained finding as a bfs-v1 failure.
    pub fn failure(&self) -> Option<Failure<I>> {
        self.findings.first().map(|finding| Failure {
            inputs: finding.witness.clone(),
            violations: finding.violations.clone(),
        })
    }
}

/// A model, checker, observer, annotator, reduction or sink error. The report
/// holds the evidence gathered before the error with termination `model_error`;
/// it is None only for configuration errors before the search started.
pub struct GraphSearchError<S, I, O> {
    pub error: ModelError,
    pub report: Option<Box<GraphSearchReport<S, I, O>>>,
}

impl<S, I, O> fmt::Debug for GraphSearchError<S, I, O> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("GraphSearchError")
            .field("error", &self.error)
            .field("has_report", &self.report.is_some())
            .finish()
    }
}
impl<S, I, O> fmt::Display for GraphSearchError<S, I, O> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.error.fmt(f)
    }
}
impl<S, I, O> std::error::Error for GraphSearchError<S, I, O> {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(&self.error)
    }
}

type GraphResult<M> = Result<
    GraphSearchReport<
        <M as crate::model::Model>::State,
        <M as crate::model::Model>::Input,
        <M as crate::model::Model>::Output,
    >,
    GraphSearchError<
        <M as crate::model::Model>::State,
        <M as crate::model::Model>::Input,
        <M as crate::model::Model>::Output,
    >,
>;

/// Breadth-first exploration that retains the admitted graph and its evidence.
pub fn explore_graph<M: Enumerate>(
    model: &M,
    config: SearchConfig,
    limits: SearchLimits<'_>,
    options: GraphSearchOptions<'_, M::State, M::Input, M::Output>,
) -> GraphResult<M>
where
    M::State: Hash,
{
    run(
        &NativeOps::enumerating(model),
        config,
        limits,
        options,
        &mut Unobserved,
    )
}

/// [`explore_graph`] with the [`SearchObserver`] hooks of `enumerate_observed`.
pub fn explore_graph_observed<M: Enumerate, B: SearchObserver<M>>(
    model: &M,
    config: SearchConfig,
    limits: SearchLimits<'_>,
    options: GraphSearchOptions<'_, M::State, M::Input, M::Output>,
    observer: &mut B,
) -> GraphResult<M>
where
    M::State: Hash,
{
    run(
        &NativeOps::enumerating(model),
        config,
        limits,
        options,
        &mut Observing(observer),
    )
}

fn run<'m, M: Enumerate, B: OpsObserver<NativeOps<'m, M, RandomState>>>(
    model: &NativeOps<'m, M, RandomState>,
    config: SearchConfig,
    limits: SearchLimits<'_>,
    options: GraphSearchOptions<'_, M::State, M::Input, M::Output>,
    observer: &mut B,
) -> GraphResult<M>
where
    M::State: Hash,
{
    if config.max_states == 0 {
        return Err(GraphSearchError {
            error: ModelError::new("enumeration max_states must be greater than zero"),
            report: None,
        });
    }
    let control = Control::new(limits.control).map_err(|error| GraphSearchError {
        error,
        report: None,
    })?;
    let (collect, expand_failing, max_findings) = match options.failure_policy {
        FailurePolicy::StopAtFirst => (false, false, 1),
        FailurePolicy::Collect {
            max_retained_findings,
            expand_failing_successors,
        } => (true, expand_failing_successors, max_retained_findings),
    };
    let mut search = Search {
        model,
        observer,
        config,
        max_estimated_state_bytes: limits.max_estimated_state_bytes,
        control,
        retention: options.retention,
        needs_edge_record: options.retention.retain_edges || options.events.is_some(),
        events: options.events,
        annotate: options.annotate_edge,
        reduction: options.reduction,
        collect,
        expand_failing,
        max_findings,
        checks: Vec::new(),
        nodes: Vec::new(),
        visited: HashMap::new(),
        report: GraphSearchReport {
            termination: GraphTermination::GraphExhausted,
            coverage: GraphCoverage::Exhaustive,
            states: 0,
            transitions: 0,
            max_depth_reached: 0,
            skipped_checks: 0,
            estimated_retained_state_bytes: None,
            nodes: Vec::new(),
            edges: Vec::new(),
            edges_retained: options.retention.retain_edges,
            graph_edges: 0,
            duplicate_edges: 0,
            reduced_edges: 0,
            rejected_successors: 0,
            frontier_size: 0,
            completed_layers: 0,
            depth_cutoff: false,
            failure_pruned: false,
            estimated_graph_bytes: 0,
            findings: Vec::new(),
            finding_count: 0,
            failed_properties: Vec::new(),
            reduction_decisions: Vec::new(),
        },
        next_edge: 0,
        current_layer: 0,
        active_node: None,
        pending_edge: None,
    };
    let mut outcome = search.execute();
    if outcome.is_ok() {
        search.finalize();
        let termination = search.report.termination;
        outcome = search.emit(GraphEvent::SearchFinished { termination });
    }
    match outcome {
        Ok(()) => Ok(search.into_report()),
        Err(error) => {
            search.report.termination = GraphTermination::ModelError;
            search.finalize();
            Err(GraphSearchError {
                error,
                report: Some(Box::new(search.into_report())),
            })
        }
    }
}

struct Node<S, I> {
    state: Rc<S>,
    parent: Option<GraphNodeId>,
    input: Option<I>,
    admitting_edge: Option<GraphEdgeId>,
    depth: usize,
    checks: Vec<Check>,
    path_failed: bool,
    expansion: NodeExpansion,
    // As in the BFS kernel: full hashes select a collision chain, visited most
    // recently admitted first; semantic equality decides identity.
    same_hash: Option<GraphNodeId>,
}

struct Representative<S> {
    edge: GraphEdgeId,
    state: Rc<S>,
    disposition: Disposition,
    checks: Vec<Check>,
    projection: Vec<u8>,
}

fn check_bytes(checks: &[Check]) -> u64 {
    checks.iter().fold(0u64, |total, check| {
        let details = match &check.status {
            CheckStatus::Passed => 0,
            CheckStatus::Failed(text) | CheckStatus::Skipped(text) => text.len(),
        };
        total.saturating_add(
            (std::mem::size_of::<Check>() + check.id.len()).saturating_add(details) as u64,
        )
    })
}

fn edge_bytes<I, O>(edge: &GraphEdge<I, O>) -> u64 {
    let mut bytes = std::mem::size_of::<GraphEdge<I, O>>() as u64;
    bytes = bytes.saturating_add(check_bytes(&edge.state_checks));
    bytes = bytes.saturating_add(check_bytes(&edge.transition_checks));
    bytes = bytes.saturating_add(
        (edge.outputs.len() as u64).saturating_mul(std::mem::size_of::<O>() as u64),
    );
    for label in &edge.annotations {
        bytes = bytes.saturating_add((std::mem::size_of::<String>() + label.len()) as u64);
    }
    bytes
}

/// Outcome of the optional reduction step for one edge.
enum Reduce<S> {
    Stopped,
    Reduced,
    Continue(Option<(String, Representative<S>)>),
}

struct Search<'a, 'm, 'o, M: Enumerate, B>
where
    M::State: Hash,
{
    model: &'a NativeOps<'m, M, RandomState>,
    observer: &'a mut B,
    config: SearchConfig,
    max_estimated_state_bytes: Option<usize>,
    control: Control<'a>,
    retention: GraphRetention,
    needs_edge_record: bool,
    events: Option<&'o mut GraphEventSink<'o, M::Input, M::Output>>,
    annotate: Option<&'o mut EdgeAnnotator<'o, M::State, M::Input, M::Output>>,
    reduction: Option<&'o mut (dyn EquivalenceReduction<M::State, M::Input, M::Output> + 'o)>,
    collect: bool,
    expand_failing: bool,
    max_findings: usize,
    checks: Vec<Check>,
    nodes: Vec<Node<M::State, M::Input>>,
    visited: HashMap<u64, GraphNodeId>,
    report: GraphSearchReport<M::State, M::Input, M::Output>,
    next_edge: GraphEdgeId,
    current_layer: usize,
    active_node: Option<GraphNodeId>,
    /// The evaluated edge whose successor is not yet resolved, retained or not.
    pending_edge: Option<GraphEdgeId>,
}

impl<'m, M: Enumerate, B: OpsObserver<NativeOps<'m, M, RandomState>>> Search<'_, 'm, '_, M, B>
where
    M::State: Hash,
{
    fn stopped(&mut self) -> bool {
        match self.control.stop() {
            Some(stop) => {
                self.report.termination = GraphTermination::from_stop(stop);
                true
            }
            None => false,
        }
    }

    /// The rejection for the current control stop.
    fn stop_rejection(&self) -> GraphRejection {
        match self.report.termination {
            GraphTermination::Deadline => GraphRejection::Deadline,
            _ => GraphRejection::Cancelled,
        }
    }

    fn emit(&mut self, event: GraphEvent<'_, M::Input, M::Output>) -> Result<(), ModelError> {
        match self.events.as_mut() {
            Some(sink) => sink(event).map_err(|e| context("graph event sink", e)),
            None => Ok(()),
        }
    }

    fn path(&self, mut index: GraphNodeId) -> Vec<M::Input> {
        let mut inputs = Vec::new();
        while let (Some(parent), Some(input)) = (self.nodes[index].parent, &self.nodes[index].input)
        {
            inputs.push(input.clone());
            index = parent;
        }
        inputs.reverse();
        inputs
    }

    /// False when the bytes would exceed the declared graph byte cap.
    fn charge_graph(&mut self, bytes: u64) -> bool {
        let total = self.report.estimated_graph_bytes.saturating_add(bytes);
        if self
            .retention
            .max_graph_bytes
            .is_some_and(|maximum| total > maximum)
        {
            return false;
        }
        self.report.estimated_graph_bytes = total;
        true
    }

    fn node_bytes(checks: &[Check]) -> u64 {
        (std::mem::size_of::<GraphNode<M::State, M::Input>>() as u64)
            .saturating_add(check_bytes(checks))
    }

    fn set_target(&mut self, edge: GraphEdgeId, target: EdgeTarget) {
        if self.pending_edge == Some(edge) {
            self.pending_edge = None;
        }
        if self.report.edges_retained
            && let Some(record) = self.report.edges.get_mut(edge)
        {
            record.target = target;
        }
    }

    fn reject(&mut self, edge: GraphEdgeId, reason: GraphRejection) -> Result<(), ModelError> {
        self.set_target(edge, EdgeTarget::Rejected(reason));
        self.report.rejected_successors += 1;
        self.emit(GraphEvent::SuccessorRejected { edge, reason })
    }

    /// Rejects `edge` with the current control stop and ends the search.
    fn reject_stopped(&mut self, edge: GraphEdgeId) -> Result<bool, ModelError> {
        let reason = self.stop_rejection();
        self.reject(edge, reason)?;
        Ok(false)
    }

    fn finish_node(&mut self, node: GraphNodeId, status: NodeExpansion) -> Result<(), ModelError> {
        self.nodes[node].expansion = status;
        self.active_node = None;
        self.emit(GraphEvent::ExpansionFinished { node, status })
    }

    fn record_finding(
        &mut self,
        violations: Vec<PropertyFailure>,
        edge: Option<GraphEdgeId>,
        earlier: bool,
        witness: impl FnOnce(&Self) -> Vec<M::Input>,
    ) -> Result<(), ModelError> {
        let index = self.report.finding_count;
        self.report.finding_count = self.report.finding_count.saturating_add(1);
        for violation in &violations {
            match self
                .report
                .failed_properties
                .iter_mut()
                .find(|p| p.phase == violation.phase && p.id == violation.check.id)
            {
                Some(property) => property.count = property.count.saturating_add(1),
                None => self.report.failed_properties.push(PropertyFailureCount {
                    phase: violation.phase,
                    id: violation.check.id.clone(),
                    count: 1,
                    first_finding: index,
                }),
            }
        }
        let retained = self.report.findings.len() < self.max_findings;
        let result = self.emit(GraphEvent::Finding {
            index,
            edge,
            violations: &violations,
        });
        if retained {
            let witness = witness(self);
            self.report.findings.push(GraphFinding {
                index,
                witness,
                violations,
                edge,
                requires_continued_evidence: earlier,
            });
        }
        result
    }

    fn complete_layer(&mut self) -> Result<(), ModelError> {
        self.report.completed_layers += 1;
        let depth = self.current_layer;
        self.emit(GraphEvent::LayerCompleted { depth })
    }

    /// Resolves interrupted state on every exit path, including errors.
    fn finalize(&mut self) {
        let termination = self.report.termination;
        if let Some(active) = self.active_node.take()
            && let Some(node) = self.nodes.get_mut(active)
            && node.expansion == NodeExpansion::Unexpanded
        {
            node.expansion = NodeExpansion::Interrupted(termination);
        }
        // An edge evaluated when the search ended (stop-at-first failure or an
        // error) is a rejected successor whether or not its record was retained.
        // Cap and control stops reject their edge explicitly.
        if let Some(edge) = self.pending_edge {
            let reason = match termination {
                GraphTermination::FailureFound => GraphRejection::FailureFound,
                _ => GraphRejection::ModelError,
            };
            self.set_target(edge, EdgeTarget::Rejected(reason));
            self.report.rejected_successors += 1;
        }
        self.report.frontier_size = self
            .nodes
            .iter()
            .filter(|node| node.expansion != NodeExpansion::Complete)
            .count();
        self.report.coverage = match termination {
            GraphTermination::GraphExhausted | GraphTermination::DepthBound => {
                if !self.report.depth_cutoff && !self.report.failure_pruned {
                    GraphCoverage::Exhaustive
                } else {
                    GraphCoverage::Bounded {
                        depth_cutoff: self.report.depth_cutoff,
                        failure_pruned: self.report.failure_pruned,
                    }
                }
            }
            other => GraphCoverage::Incomplete(other),
        };
    }

    fn into_report(self) -> GraphSearchReport<M::State, M::Input, M::Output> {
        let mut report = self.report;
        report.nodes = self
            .nodes
            .into_iter()
            .enumerate()
            .map(|(id, node)| GraphNode {
                id,
                state: Rc::try_unwrap(node.state).unwrap_or_else(|state| (*state).clone()),
                parent: node.parent,
                input: node.input,
                admitting_edge: node.admitting_edge,
                depth: node.depth,
                checks: node.checks,
                path_failed: node.path_failed,
                expansion: node.expansion,
            })
            .collect();
        report
    }

    fn execute(&mut self) -> Result<(), ModelError> {
        let model = self.model;
        if self.stopped() {
            return Ok(());
        }
        let state = model
            .initial_state()
            .map_err(|e| context("initial_state", e))?;
        self.report.states = 1;
        let initial_bad = initial_checks_retained(
            model,
            &state,
            &mut self.report.skipped_checks,
            &mut self.checks,
            self.observer,
        )?;
        let initial_checks = self.checks.clone();
        let initial_failed = !initial_bad.is_empty();
        if initial_failed {
            self.record_finding(initial_bad, None, false, |_| Vec::new())?;
            if !self.collect {
                self.report.termination = GraphTermination::FailureFound;
                return Ok(());
            }
        }
        if self.stopped() {
            return Ok(());
        }
        let initial_bytes = model
            .estimated_state_bytes(&state)
            .map_err(|e| context("estimated_state_bytes", e))?;
        self.report.estimated_retained_state_bytes = initial_bytes;
        if self.stopped() {
            return Ok(());
        }
        if let Some(maximum) = self.max_estimated_state_bytes {
            let bytes = initial_bytes.ok_or_else(|| {
                ModelError::new("state byte budget requires estimated_state_bytes")
            })?;
            if bytes > maximum {
                self.report.states = 0;
                self.report.estimated_retained_state_bytes = Some(0);
                self.report.termination = GraphTermination::EstimatedStateByteLimit;
                return Ok(());
            }
        }
        let initial_hash = model
            .state_hash(&state)
            .map_err(|e| context("state_hash", e))?;
        if self.stopped() {
            return Ok(());
        }
        if !self.charge_graph(Self::node_bytes(&initial_checks)) {
            self.report.states = 0;
            self.report.estimated_retained_state_bytes = Some(0);
            self.report.termination = GraphTermination::GraphRetentionLimit;
            return Ok(());
        }
        self.visited.insert(initial_hash, 0);
        self.nodes.push(Node {
            state: Rc::new(state),
            parent: None,
            input: None,
            admitting_edge: None,
            depth: 0,
            checks: initial_checks,
            path_failed: initial_failed,
            expansion: NodeExpansion::Unexpanded,
            same_hash: None,
        });
        self.emit(GraphEvent::RootAdmitted(0))?;
        if initial_failed && !self.expand_failing {
            self.report.failure_pruned = true;
            self.finish_node(0, NodeExpansion::FailurePruned)?;
        }
        let mut cursor = 0;
        while cursor < self.nodes.len() {
            let depth = self.nodes[cursor].depth;
            if depth > self.current_layer {
                self.complete_layer()?;
                self.current_layer = depth;
            }
            if self.nodes[cursor].expansion != NodeExpansion::FailurePruned
                && !self.expand(cursor)?
            {
                return Ok(());
            }
            cursor += 1;
        }
        self.complete_layer()?;
        if self.report.depth_cutoff {
            self.report.termination = GraphTermination::DepthBound;
        }
        Ok(())
    }

    /// Expands one dequeued node. False when the search ends.
    fn expand(&mut self, cursor: GraphNodeId) -> Result<bool, ModelError> {
        let model = self.model;
        if self.stopped() {
            return Ok(false);
        }
        self.active_node = Some(cursor);
        self.emit(GraphEvent::ExpansionStarted(cursor))?;
        let state = Rc::clone(&self.nodes[cursor].state);
        let depth = self.nodes[cursor].depth;
        let mut inputs = model
            .input_cursor(&state)
            .map_err(|e| context("enumerate inputs", e))?;
        if self.stopped() {
            return Ok(false);
        }
        if depth == self.config.max_depth {
            // Probe only one item: no transition is executed at the depth boundary.
            let unexamined = inputs
                .next()
                .map_err(|e| context("enumerate next", e))?
                .is_some();
            if self.stopped() {
                return Ok(false);
            }
            self.report.depth_cutoff |= unexamined;
            let status = if unexamined {
                NodeExpansion::DepthFrontier
            } else {
                NodeExpansion::Complete
            };
            self.finish_node(cursor, status)?;
            return Ok(true);
        }
        let mut representatives: HashMap<String, Vec<Representative<M::State>>> = HashMap::new();
        loop {
            if self.stopped() {
                return Ok(false);
            }
            let next = inputs.next().map_err(|e| context("enumerate next", e))?;
            if self.stopped() {
                return Ok(false);
            }
            let Some(input) = next else {
                break;
            };
            if self.report.transitions == self.config.max_transitions {
                self.report.termination = GraphTermination::TransitionLimit;
                return Ok(false);
            }
            if !self.evaluate(cursor, &state, depth, input, &mut representatives)? {
                return Ok(false);
            }
        }
        self.finish_node(cursor, NodeExpansion::Complete)?;
        Ok(true)
    }

    /// Evaluates and resolves one edge. False when the search ends.
    fn evaluate(
        &mut self,
        cursor: GraphNodeId,
        state: &M::State,
        depth: usize,
        input: M::Input,
        representatives: &mut HashMap<String, Vec<Representative<M::State>>>,
    ) -> Result<bool, ModelError> {
        let model = self.model;
        let path_failed = self.nodes[cursor].path_failed;
        let transition = model.step(state, &input).map_err(|e| context("step", e))?;
        self.report.transitions += 1;
        self.report.max_depth_reached = self.report.max_depth_reached.max(depth + 1);
        let history = if self.observer.needs_history() {
            let mut history = self.path(cursor);
            history.push(input.clone());
            history
        } else {
            Vec::new()
        };
        let (bad, state_count) = transition_checks_retained(
            model,
            state,
            &input,
            &transition,
            &mut self.report.skipped_checks,
            &mut self.checks,
            self.observer,
            &history,
        )?;
        let state_checks = self.checks[..state_count].to_vec();
        let labels = match self.annotate.as_mut() {
            Some(annotate) => annotate(state, &input, &transition.as_ref())
                .map_err(|e| context("annotate edge", e))?,
            None => Vec::new(),
        };
        let edge_id = self.next_edge;
        self.next_edge += 1;
        self.pending_edge = Some(edge_id);
        let mut retained = false;
        if self.needs_edge_record {
            let edge = GraphEdge {
                id: edge_id,
                source: cursor,
                input: input.clone(),
                disposition: transition.disposition.clone(),
                outputs: transition.outputs.clone(),
                state_checks: state_checks.clone(),
                transition_checks: self.checks[state_count..].to_vec(),
                annotations: labels.clone(),
                target: EdgeTarget::Pending,
            };
            if self.report.edges_retained {
                if self
                    .retention
                    .max_retained_edges
                    .is_some_and(|maximum| self.report.edges.len() >= maximum)
                    || !self.charge_graph(edge_bytes(&edge))
                {
                    self.report.edges_retained = false;
                } else {
                    retained = true;
                }
            }
            let emitted = self.emit(GraphEvent::EdgeEvaluated(&edge));
            if retained {
                self.report.edges.push(edge);
            }
            emitted?;
        }
        let failed = !bad.is_empty();
        if failed {
            let witness_input = input.clone();
            self.record_finding(bad, Some(edge_id), path_failed, |search| {
                let mut witness = search.path(cursor);
                witness.push(witness_input);
                witness
            })?;
            if !self.collect {
                self.report.termination = GraphTermination::FailureFound;
                return Ok(false);
            }
        }
        if self.retention.retain_edges && !retained {
            // The graph can no longer be retained completely.
            self.report.termination = GraphTermination::GraphRetentionLimit;
            self.reject(edge_id, GraphRejection::GraphRetentionLimit)?;
            return Ok(false);
        }
        if self.stopped() {
            return self.reject_stopped(edge_id);
        }
        let Transition {
            state: successor,
            outputs,
            disposition,
        } = transition;
        let successor = Rc::new(successor);
        let mut reduction = self.reduction.take();
        let reduced = match reduction.as_deref_mut() {
            Some(reduction) => self.reduce(
                reduction,
                cursor,
                state,
                &input,
                edge_id,
                &successor,
                &outputs,
                &disposition,
                &labels,
                representatives,
            ),
            None => Ok(Reduce::Continue(None)),
        };
        self.reduction = reduction;
        match reduced? {
            Reduce::Stopped => return self.reject_stopped(edge_id),
            Reduce::Reduced => return Ok(true),
            Reduce::Continue(Some((label, representative))) => representatives
                .entry(label)
                .or_default()
                .push(representative),
            Reduce::Continue(None) => {}
        }
        let key = model
            .state_hash(&successor)
            .map_err(|e| context("state_hash", e))?;
        if self.stopped() {
            return self.reject_stopped(edge_id);
        }
        let mut candidate = self.visited.get(&key).copied();
        let mut duplicate = None;
        while let Some(other) = candidate {
            if self.stopped() {
                return self.reject_stopped(edge_id);
            }
            let equal = model
                .state_equal(&self.nodes[other].state, &successor)
                .map_err(|e| context("state_equal", e))?;
            if self.stopped() {
                return self.reject_stopped(edge_id);
            }
            if equal {
                duplicate = Some(other);
                break;
            }
            candidate = self.nodes[other].same_hash;
        }
        if let Some(existing) = duplicate {
            self.set_target(edge_id, EdgeTarget::Duplicate(existing));
            self.report.duplicate_edges += 1;
            self.report.graph_edges += 1;
            self.emit(GraphEvent::DuplicateEdge {
                edge: edge_id,
                existing,
            })?;
            return Ok(true);
        }
        if failed && !self.expand_failing {
            self.report.failure_pruned = true;
            self.reject(edge_id, GraphRejection::FailingSuccessor)?;
            return Ok(true);
        }
        if self.nodes.len() == self.config.max_states {
            self.report.termination = GraphTermination::StateLimit;
            self.reject(edge_id, GraphRejection::StateLimit)?;
            return Ok(false);
        }
        let estimate = model
            .estimated_state_bytes(&successor)
            .map_err(|e| context("estimated_state_bytes", e))?;
        if self.stopped() {
            return self.reject_stopped(edge_id);
        }
        let total = match (self.report.estimated_retained_state_bytes, estimate) {
            (Some(retained), Some(bytes)) => retained.checked_add(bytes),
            _ => None,
        };
        if let Some(maximum) = self.max_estimated_state_bytes {
            if estimate.is_none() {
                return Err(ModelError::new(
                    "state byte budget requires estimated_state_bytes",
                ));
            }
            if total.is_none_or(|bytes| bytes > maximum) {
                self.report.termination = GraphTermination::EstimatedStateByteLimit;
                self.reject(edge_id, GraphRejection::EstimatedStateByteLimit)?;
                return Ok(false);
            }
        }
        if !self.charge_graph(Self::node_bytes(&state_checks)) {
            self.report.termination = GraphTermination::GraphRetentionLimit;
            self.reject(edge_id, GraphRejection::GraphRetentionLimit)?;
            return Ok(false);
        }
        let id = self.nodes.len();
        let same_hash = self.visited.insert(key, id);
        self.nodes.push(Node {
            state: successor,
            parent: Some(cursor),
            input: Some(input),
            admitting_edge: Some(edge_id),
            depth: depth + 1,
            checks: state_checks,
            path_failed: path_failed || failed,
            expansion: NodeExpansion::Unexpanded,
            same_hash,
        });
        self.report.states = self.nodes.len();
        self.report.estimated_retained_state_bytes = total;
        self.report.graph_edges += 1;
        self.set_target(edge_id, EdgeTarget::Admitted(id));
        self.emit(GraphEvent::SuccessorAdmitted {
            edge: edge_id,
            node: id,
        })?;
        Ok(true)
    }

    /// reduction-v1 grouping for one checked edge (REDUCE-002/003).
    #[allow(clippy::too_many_arguments)]
    fn reduce(
        &mut self,
        reduction: &mut dyn EquivalenceReduction<M::State, M::Input, M::Output>,
        cursor: GraphNodeId,
        state: &M::State,
        input: &M::Input,
        edge_id: GraphEdgeId,
        successor: &Rc<M::State>,
        outputs: &[M::Output],
        disposition: &Disposition,
        labels: &[String],
        representatives: &HashMap<String, Vec<Representative<M::State>>>,
    ) -> Result<Reduce<M::State>, ModelError> {
        let model = self.model;
        let label = reduction
            .eligible_class(state, input)
            .map_err(|e| context("reduction eligible_class", e))?;
        if self.stopped() {
            return Ok(Reduce::Stopped);
        }
        let Some(label) = label else {
            return Ok(Reduce::Continue(None));
        };
        let observed = TransitionRef {
            state: &**successor,
            outputs,
            disposition,
        };
        let projection = reduction
            .projection(input, &observed, labels)
            .map_err(|e| context("reduction projection", e))?;
        if self.stopped() {
            return Ok(Reduce::Stopped);
        }
        let mut rejected = Vec::new();
        let mut reduced_to = None;
        for representative in representatives.get(&label).into_iter().flatten() {
            let reason = if representative.disposition != *disposition {
                Some(RejectionCause::DispositionDiffers)
            } else if representative.checks != self.checks {
                Some(RejectionCause::ChecksDiffer)
            } else if representative.projection != projection {
                Some(RejectionCause::ProjectionDiffers)
            } else {
                if self.stopped() {
                    return Ok(Reduce::Stopped);
                }
                let same = model
                    .state_equal(&representative.state, successor)
                    .map_err(|e| context("state_equal", e))?;
                if self.stopped() {
                    return Ok(Reduce::Stopped);
                }
                (!same).then_some(RejectionCause::SuccessorDiffers)
            };
            match reason {
                Some(reason) => rejected.push(RejectedEquivalence {
                    representative: representative.edge,
                    reason,
                }),
                None => {
                    reduced_to = Some(representative.edge);
                    break;
                }
            }
        }
        self.report.reduction_decisions.push(ReductionDecision {
            edge: edge_id,
            source: cursor,
            equivalence_class: label.clone(),
            reduced_to,
            rejected,
        });
        if let Some(representative) = reduced_to {
            self.set_target(edge_id, EdgeTarget::Reduced(representative));
            self.report.reduced_edges += 1;
            self.emit(GraphEvent::EdgeReduced {
                edge: edge_id,
                representative,
            })?;
            return Ok(Reduce::Reduced);
        }
        Ok(Reduce::Continue(Some((
            label,
            Representative {
                edge: edge_id,
                state: Rc::clone(successor),
                disposition: disposition.clone(),
                checks: self.checks.clone(),
                projection,
            },
        ))))
    }
}

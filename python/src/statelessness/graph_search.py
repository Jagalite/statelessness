"""Candidate graph-search-v1 (S1/S2) and reduction-v1 (S4); see spec/*-v1.md.

`explore_graph` retains the admitted BFS graph and its evidence. With default
`GraphSearchOptions` it executes exactly the `enumerate_states` (bfs-controls-v1)
callback sequence and reports the same counters and witness; the extra deepcopy
snapshots it takes for edge records are cooperative copy hooks. State identity is
the native Python one used by `enumerate_states`: `hash()` buckets (unhashable
states share an exhaustive bucket) decided by `==`. `enumerate_states` is
unchanged and remains the portable bfs-v1 entry point.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Generic, Iterable

from .core import (Check, Disposition, Failure, I, ModelError, O, S, SearchConfig, Transition,
                   U64_MAX, Violation, _Captured, _call, _copy, _error_detail, _hash, _natural,
                   _saturating_add_u64, _SnapshotFailure, _state_checks, _step, _transition_checks)
from .search import RunLimits, SearchLimits, _Control, _estimate, _violations

# Engine bookkeeping estimates for estimated_graph_bytes (GRAPH-006). These are
# fixed per-record charges plus text/payload counts, not CPython object sizes.
NODE_RECORD_BYTES = 64
EDGE_RECORD_BYTES = 96
CHECK_RECORD_BYTES = 32
OUTPUT_RECORD_BYTES = 8
LABEL_RECORD_BYTES = 16
_SENTINEL = object()


def _same_text(left: str, right: str) -> bool:
    return str.__eq__(str.__str__(left), str.__str__(right)) is True


@dataclass(frozen=True)
class FailurePolicy:
    """`stop_at_first` (bfs-v1) or `collect`. Collection records every failing
    batch; `max_retained_findings` bounds retained witnesses only. Without
    `expand_failing_successors`, a new successor reached by a failing edge is
    rejected (`failing_successor`) and coverage is failure-pruned."""
    kind: str = "stop_at_first"
    max_retained_findings: int = 0
    expand_failing_successors: bool = False

    def __post_init__(self) -> None:
        if self.kind not in ("stop_at_first", "collect"):
            raise ValueError("failure policy must be stop_at_first or collect")
        if type(self.max_retained_findings) is not int or self.max_retained_findings < 0:
            raise ValueError("max_retained_findings must be nonnegative")
        if type(self.expand_failing_successors) is not bool:
            raise ValueError("expand_failing_successors must be boolean")

    @classmethod
    def stop_at_first(cls) -> FailurePolicy:
        return cls()

    @classmethod
    def collect(cls, max_retained_findings: int, expand_failing_successors: bool) -> FailurePolicy:
        return cls("collect", max_retained_findings, expand_failing_successors)


@dataclass(frozen=True)
class GraphRetention:
    """Exceeding either cap ends the search with `graph_retention_limit`.
    `max_graph_bytes` bounds engine bookkeeping, not model state representation."""
    retain_edges: bool = True
    max_retained_edges: int | None = None
    max_graph_bytes: int | None = None

    def __post_init__(self) -> None:
        if type(self.retain_edges) is not bool:
            raise ValueError("retain_edges must be boolean")
        if self.max_retained_edges is not None and (type(self.max_retained_edges) is not int or self.max_retained_edges < 0):
            raise ValueError("max_retained_edges must be nonnegative")
        if self.max_graph_bytes is not None:
            _natural(self.max_graph_bytes, "max_graph_bytes")


@dataclass(frozen=True)
class EdgeTarget:
    """`pending` (only inside edge_evaluated events), `admitted`/`duplicate`
    (node ID), `reduced` (representative edge ID) or `rejected` (reason)."""
    kind: str
    value: Any = None

    @classmethod
    def pending(cls) -> EdgeTarget: return cls("pending")
    @classmethod
    def admitted(cls, node: int) -> EdgeTarget: return cls("admitted", node)
    @classmethod
    def duplicate(cls, node: int) -> EdgeTarget: return cls("duplicate", node)
    @classmethod
    def reduced(cls, edge: int) -> EdgeTarget: return cls("reduced", edge)
    @classmethod
    def rejected(cls, reason: str) -> EdgeTarget: return cls("rejected", reason)

    def __str__(self) -> str:
        return self.kind if self.kind == "pending" else f"{self.kind}:{self.value}"


@dataclass(frozen=True)
class GraphNode(Generic[S, I]):
    """Expansion is `unexpanded`, `complete`, `depthFrontier`, `failurePruned`
    or `interrupted` (with `interrupted_reason`)."""
    id: int
    state: S
    parent: int | None
    input: I | None
    admitting_edge: int | None
    depth: int
    # Initial checks for the root; otherwise the admitting edge's state checks.
    checks: tuple[Check, ...]
    # True when any check batch on the root path, including this node's, failed.
    path_failed: bool
    expansion: str = "unexpanded"
    interrupted_reason: str | None = None


@dataclass(frozen=True)
class GraphEdge(Generic[I, O]):
    id: int
    source: int
    input: I
    disposition: Disposition
    outputs: tuple[O, ...]
    state_checks: tuple[Check, ...]
    transition_checks: tuple[Check, ...]
    annotations: tuple[str, ...]
    target: EdgeTarget

    @property
    def failed(self) -> bool:
        return any(c.status == "failed" for c in self.state_checks + self.transition_checks)


@dataclass(frozen=True)
class GraphFinding(Generic[I]):
    index: int
    # Root-to-edge inputs; empty for an initial-state finding.
    witness: tuple[I, ...]
    # Every failed check of the batch, state checks before transition checks.
    violations: tuple[Violation, ...]
    # None for an initial-state finding.
    edge: int | None
    # The witness crosses an earlier failure (continued-failure-evidence-v1).
    requires_continued_evidence: bool


@dataclass(frozen=True)
class PropertyFailureCount:
    phase: str
    id: str
    count: int
    first_finding: int


@dataclass(frozen=True)
class GraphCoverage:
    """`exhaustive`, `bounded` (depth cutoff and/or failure pruning) or
    `incomplete` (reason)."""
    kind: str
    depth_cutoff: bool = False
    failure_pruned: bool = False
    reason: str | None = None

    def __str__(self) -> str:
        if self.kind == "bounded":
            parts = [name for name, flag in (("depth_cutoff", self.depth_cutoff),
                     ("failure_pruned", self.failure_pruned)) if flag]
            return "bounded:" + "+".join(parts)
        return "incomplete:" + str(self.reason) if self.kind == "incomplete" else self.kind


@dataclass(frozen=True)
class GraphEvent:
    """Synchronous sink payload (GRAPH-005). Kinds: root_admitted(node),
    expansion_started(node), edge_evaluated(edge: GraphEdge), finding(index,
    edge, violations), successor_admitted(edge, node), duplicate_edge(edge,
    node), edge_reduced(edge, representative), successor_rejected(edge,
    reason), expansion_finished(node, status), layer_completed(depth),
    search_finished(termination). `edge` is an ID except for edge_evaluated."""
    kind: str
    node: int | None = None
    edge: Any = None
    index: int | None = None
    violations: tuple[Violation, ...] = ()
    representative: int | None = None
    reason: str | None = None
    status: str | None = None
    depth: int | None = None
    termination: str | None = None


@dataclass(frozen=True)
class EquivalenceReduction(Generic[S, I, O]):
    """reduction-v1. `eligible_class(state, input)` returns a class name or None;
    `projection(input, transition, labels)` returns deterministic bytes for the
    ordered effects and progress annotations equivalence must preserve."""
    eligible_class: Callable[[S, I], str | None]
    projection: Callable[[I, Transition[S, O], tuple[str, ...]], bytes]


@dataclass(frozen=True)
class RejectedEquivalence:
    representative: int
    # disposition_differs, checks_differ, projection_differs or successor_differs.
    reason: str


@dataclass(frozen=True)
class ReductionDecision:
    edge: int
    source: int
    equivalence_class: str
    # None: the edge became a representative of its class.
    reduced_to: int | None
    rejected: tuple[RejectedEquivalence, ...]


@dataclass(frozen=True)
class GraphSearchOptions:
    failure_policy: FailurePolicy = field(default_factory=FailurePolicy)
    retention: GraphRetention = field(default_factory=GraphRetention)
    observer: Any = None
    # Synchronous event sink; payloads are detached copies and errors abort.
    events: Callable[[GraphEvent], None] | None = None
    # User edge labels, called with detached copies after all checks.
    annotate_edge: Callable[[Any, Any, Transition], Iterable[str]] | None = None
    reduction: EquivalenceReduction | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.failure_policy, FailurePolicy):
            raise ValueError("failure_policy must be FailurePolicy")
        if not isinstance(self.retention, GraphRetention):
            raise ValueError("retention must be GraphRetention")
        for name in ("events", "annotate_edge"):
            if getattr(self, name) is not None and not callable(getattr(self, name)):
                raise ValueError(f"{name} must be callable")
        if self.reduction is not None and not isinstance(self.reduction, EquivalenceReduction):
            raise ValueError("reduction must be EquivalenceReduction")


@dataclass(frozen=True)
class GraphSearchReport(Generic[S, I, O]):
    termination: str
    coverage: GraphCoverage
    # bfs-v1 meaning: admitted states, or 1 once the initial state exists.
    states: int
    # Evaluated transitions, including duplicate, reduced and rejected edges.
    transitions: int
    max_depth_reached: int
    skipped_checks: int
    estimated_retained_state_bytes: int | None
    nodes: tuple[GraphNode[S, I], ...]
    # Indexed by edge ID while edges_retained.
    edges: tuple[GraphEdge[I, O], ...]
    edges_retained: bool
    # Edges between admitted nodes (admitted plus duplicate targets).
    graph_edges: int
    duplicate_edges: int
    reduced_edges: int
    rejected_successors: int
    # Admitted nodes whose expansion is not complete.
    frontier_size: int
    completed_layers: int
    depth_cutoff: bool
    failure_pruned: bool
    # Engine bookkeeping for nodes and retained edges (saturating).
    estimated_graph_bytes: int
    findings: tuple[GraphFinding[I], ...]
    finding_count: int
    failed_properties: tuple[PropertyFailureCount, ...]
    reduction_decisions: tuple[ReductionDecision, ...]

    @property
    def findings_truncated(self) -> bool:
        return self.finding_count > len(self.findings)

    @property
    def failure(self) -> Failure[I] | None:
        return Failure(self.findings[0].witness, self.findings[0].violations) if self.findings else None


class GraphSearchError(ModelError):
    """A model, checker, observer or sink error after the search started. The
    report holds the evidence gathered before the error and is incomplete."""
    def __init__(self, error: BaseException, report: GraphSearchReport):
        super().__init__(_error_detail(error))
        self.error, self.report = error, report
        if hasattr(error, "observer_errors"):
            self.observer_errors = error.observer_errors


def _check_bytes(checks: Iterable[Check]) -> int:
    total = 0
    for check in checks:
        total = _saturating_add_u64(total, CHECK_RECORD_BYTES + len(check.id.encode("utf-8", "surrogatepass")) +
                                    len(check.details.encode("utf-8", "surrogatepass")))
    return total


def _edge_bytes(edge: GraphEdge) -> int:
    total = _saturating_add_u64(EDGE_RECORD_BYTES, _check_bytes(edge.state_checks))
    total = _saturating_add_u64(total, _check_bytes(edge.transition_checks))
    total = _saturating_add_u64(total, OUTPUT_RECORD_BYTES * len(edge.outputs))
    for label in edge.annotations:
        total = _saturating_add_u64(total, LABEL_RECORD_BYTES + len(label.encode("utf-8", "surrogatepass")))
    return total


def _labels(values: Any) -> tuple[str, ...]:
    labels = tuple(values)
    if any(not isinstance(label, str) for label in labels):
        raise ModelError("annotate edge must return strings")
    return tuple(str.__str__(label) for label in labels)


def _same_state(left: Any, right: Any) -> bool:
    return _call("state equality", lambda a, b: bool(a == b), _copy(left), _copy(right))


@dataclass
class _Representative:
    edge: int
    state: Any
    disposition: Disposition
    checks: tuple[Check, ...]
    projection: bytes


class _GraphRun:
    def __init__(self, model, config, limits, options):
        self.model, self.config, self.limits, self.options = model, config, limits, options
        policy, self.retention = options.failure_policy, options.retention
        self.collect = policy.kind == "collect"
        self.expand_failing = self.collect and policy.expand_failing_successors
        self.max_findings = policy.max_retained_findings if self.collect else 1
        self.sink = options.events
        self.needs_edge_record = self.retention.retain_edges or self.sink is not None
        self.control = _Control(limits.control)
        self.storage: list[Check] = []
        self.termination = "graph_exhausted"
        self.states = self.transitions = self.max_depth = self.skipped = 0
        self.estimated = None
        self.nodes: list[GraphNode] = []
        self.edges: list[GraphEdge] = []
        self.edges_retained = self.retention.retain_edges
        self.graph_edges = self.duplicate_edges = self.reduced_edges = self.rejected = 0
        self.completed_layers = 0
        self.depth_cutoff = self.failure_pruned = False
        self.graph_bytes = 0
        self.findings: list[GraphFinding] = []
        self.finding_count = 0
        self.failed: list[PropertyFailureCount] = []
        self.failed_index: dict[tuple[str, str], int] = {}
        self.decisions: list[ReductionDecision] = []
        self.visited: dict[Any, list[int]] = {}
        self.next_edge = self.layer = 0
        self.active: int | None = None
        # The evaluated edge whose successor is not yet resolved, retained or not.
        self.pending: int | None = None

    def stopped(self) -> bool:
        reason = self.control.stop()
        if reason is None:
            return False
        self.termination = reason
        return True

    def emit(self, kind: str, **fields: Any) -> None:
        if self.sink is not None:
            event = _copy(GraphEvent(kind, **fields))
            _call("graph event sink", self.sink, event)

    def path(self, index: int, input: Any) -> tuple[Any, ...]:
        inputs = [_copy(input)]
        while self.nodes[index].parent is not None:
            inputs.append(_copy(self.nodes[index].input))
            index = self.nodes[index].parent
        inputs.reverse()
        return tuple(inputs)

    def charge(self, count: int) -> bool:
        """False when the bytes would exceed the declared graph byte cap."""
        total = _saturating_add_u64(self.graph_bytes, count)
        if self.retention.max_graph_bytes is not None and total > self.retention.max_graph_bytes:
            return False
        self.graph_bytes = total
        return True

    def set_target(self, edge: int, target: EdgeTarget) -> None:
        if self.pending == edge:
            self.pending = None
        if self.edges_retained and edge < len(self.edges):
            self.edges[edge] = replace(self.edges[edge], target=target)

    def reject(self, edge: int, reason: str) -> None:
        self.set_target(edge, EdgeTarget.rejected(reason))
        self.rejected = _saturating_add_u64(self.rejected, 1)
        self.emit("successor_rejected", edge=edge, reason=reason)

    def stop_edge(self, edge: int) -> bool:
        """Poll; on a stop the evaluated edge is rejected with the stop reason."""
        if self.stopped():
            self.reject(edge, self.termination)
            return True
        return False

    def finish(self, node: int, status: str) -> None:
        self.nodes[node] = replace(self.nodes[node], expansion=status)
        self.active = None
        self.emit("expansion_finished", node=node, status=status)

    def complete_layer(self) -> None:
        self.completed_layers += 1
        self.emit("layer_completed", depth=self.layer)

    def record_finding(self, violations, edge, earlier, witness) -> None:
        index = self.finding_count
        self.finding_count = _saturating_add_u64(self.finding_count, 1)
        for v in violations:
            key = (str.__str__(v.phase), str.__str__(v.check.id))
            position = self.failed_index.get(key)
            if position is None:
                self.failed_index[key] = len(self.failed)
                self.failed.append(PropertyFailureCount(key[0], key[1], 1, index))
            else:
                prior = self.failed[position]
                self.failed[position] = replace(prior, count=_saturating_add_u64(prior.count, 1))
        if len(self.findings) < self.max_findings:
            self.findings.append(GraphFinding(index, witness(), tuple(violations), edge, earlier))
        self.emit("finding", index=index, edge=edge, violations=tuple(violations))

    def finalize(self) -> None:
        """Resolves interrupted state on every exit path, including errors."""
        active = self.active
        if active is not None and active < len(self.nodes) and self.nodes[active].expansion == "unexpanded":
            self.nodes[active] = replace(self.nodes[active], expansion="interrupted",
                                         interrupted_reason=self.termination)
            self.active = None
        # An edge evaluated when the search ended (stop-at-first failure, error)
        # is a rejected successor whether or not its record was retained.
        if self.pending is not None:
            self.set_target(self.pending, EdgeTarget.rejected(self.termination))
            self.rejected = _saturating_add_u64(self.rejected, 1)

    def report(self) -> GraphSearchReport:
        if self.termination in ("graph_exhausted", "depth_bound"):
            coverage = (GraphCoverage("exhaustive") if not self.depth_cutoff and not self.failure_pruned else
                        GraphCoverage("bounded", self.depth_cutoff, self.failure_pruned))
        else:
            coverage = GraphCoverage("incomplete", reason=self.termination)
        return GraphSearchReport(
            self.termination, coverage, self.states, self.transitions, self.max_depth, self.skipped,
            self.estimated, tuple(self.nodes), tuple(self.edges), self.edges_retained, self.graph_edges,
            self.duplicate_edges, self.reduced_edges, self.rejected,
            sum(node.expansion != "complete" for node in self.nodes), self.completed_layers,
            self.depth_cutoff, self.failure_pruned, self.graph_bytes, tuple(self.findings),
            self.finding_count, tuple(self.failed), tuple(self.decisions))

    def execute(self) -> None:
        model, limits = self.model, self.limits
        observer = self.options.observer
        if self.stopped():
            return
        state = _copy(_call("initial_state", model.initial_state))
        # Keep the node separate from application check/observer aliases; defer
        # copy failures while an earlier violation or stop decides the run.
        try:
            initial_node, initial_error = _copy(state), None
        except _SnapshotFailure as error:
            initial_node, initial_error = None, error
        self.states = 1
        violations, self.skipped = _violations(model, state, observer=observer, storage=self.storage)
        initial_checks = tuple(self.storage)
        if violations:
            self.record_finding(violations, None, False, tuple)
            if not self.collect:
                self.termination = "failure_found"
                return
        if self.stopped():
            return
        if initial_error is not None:
            raise initial_error
        state = initial_node
        self.estimated = _estimate(model, state)
        if self.stopped():
            return
        if limits.max_estimated_state_bytes is not None:
            if self.estimated is None:
                raise ModelError("state byte budget requires estimated_state_bytes")
            if self.estimated > limits.max_estimated_state_bytes:
                self.states, self.estimated = 0, 0
                self.termination = "estimated_state_byte_limit"
                return
        hashed = _call("state hash", _hash, _copy(state))
        if self.stopped():
            return
        if not self.charge(_saturating_add_u64(NODE_RECORD_BYTES, _check_bytes(initial_checks))):
            self.states, self.estimated = 0, 0
            self.termination = "graph_retention_limit"
            return
        self.nodes.append(GraphNode(0, state, None, None, None, 0, initial_checks, bool(violations)))
        self.visited = {hashed: [0]}
        self.emit("root_admitted", node=0)
        if violations and not self.expand_failing:
            self.failure_pruned = True
            self.finish(0, "failurePruned")
        cursor = 0
        while cursor < len(self.nodes):
            node = self.nodes[cursor]
            if node.depth > self.layer:
                self.complete_layer()
                self.layer = node.depth
            if node.expansion != "failurePruned" and not self.expand(cursor, node):
                return
            cursor += 1
        self.complete_layer()
        if self.depth_cutoff:
            self.termination = "depth_bound"

    def expand(self, cursor: int, node: GraphNode) -> bool:
        """Expands one dequeued node. False when the search ends."""
        if self.stopped():
            return False
        self.active = cursor
        self.emit("expansion_started", node=cursor)
        domain = _call("enumerate inputs", self.model.inputs, _copy(node.state))
        if self.stopped():
            return False
        iterator = _call("enumerate inputs", iter, domain)
        if self.stopped():
            return False
        if node.depth == self.config.max_depth:
            unexamined = _call("enumerate inputs", next, iterator, _SENTINEL) is not _SENTINEL
            if self.stopped():
                return False
            self.depth_cutoff |= unexamined
            self.finish(cursor, "depthFrontier" if unexamined else "complete")
            return True
        representatives: dict[str, list[_Representative]] = {}
        while True:
            if self.stopped():
                return False
            input = _call("enumerate inputs", next, iterator, _SENTINEL)
            if self.stopped():
                return False
            if input is _SENTINEL:
                break
            if self.transitions == self.config.max_transitions:
                self.termination = "transition_limit"
                return False
            if not self.evaluate(cursor, node, input, representatives):
                return False
        self.finish(cursor, "complete")
        return True

    def evaluate(self, cursor: int, node: GraphNode, input: Any, representatives) -> bool:
        """Evaluates and resolves one edge. False when the search ends."""
        model, options, observer = self.model, self.options, self.options.observer
        state, depth = node.state, node.depth
        # Preserve the enumerated value before callbacks can mutate an alias.
        input = _copy(input)
        transition = _step(model, state, input)
        self.transitions += 1
        self.max_depth = max(self.max_depth, depth + 1)
        try:
            successor_state, successor_error = _copy(transition.state), None
        except _SnapshotFailure as error:
            successor_state, successor_error = None, error
        try:
            edge_observation, edge_error = _copy((state, input, transition)), None
        except _SnapshotFailure as error:
            edge_observation, edge_error = None, error
        payload = _Captured((input, transition.outputs)) if self.needs_edge_record else None
        wants_history = getattr(observer, "needs_history", None) if observer is not None else None
        history_requested = False if wants_history is None else _call("search observer needs_history", wants_history)
        if type(history_requested) is not bool:
            raise ModelError("search observer needs_history must return bool")
        history = self.path(cursor, input) if history_requested else ()
        edge_before, edge_input, edge_transition = (edge_observation if edge_error is None
                                                    else (state, input, transition))
        boundary: list[int] = []
        violations, count = _violations(model, edge_before, edge_input, edge_transition,
                                        observer=observer, history=history, storage=self.storage,
                                        edge_capture_error=edge_error,
                                        state_snapshot=(successor_state if successor_error is None
                                                        else edge_transition.state),
                                        state_snapshot_error=(successor_error if successor_error is not None
                                                              and edge_error is not None else None),
                                        boundary_out=boundary)
        self.skipped = _saturating_add_u64(self.skipped, count)
        checks = tuple(self.storage)
        state_checks, transition_checks = checks[:boundary[0]], checks[boundary[0]:]
        labels: tuple[str, ...] = ()
        if options.annotate_edge is not None:
            if successor_error is not None:
                raise successor_error
            snapshot = Transition(_copy(successor_state), _copy(transition.outputs), transition.disposition)
            labels = _call("annotate edge", lambda: _labels(options.annotate_edge(_copy(state), _copy(input), snapshot)))
        edge_id = self.next_edge
        self.next_edge += 1
        self.pending = edge_id
        retained = False
        if payload is not None:
            edge_input, outputs = payload.get()
            edge = GraphEdge(edge_id, cursor, edge_input, transition.disposition, tuple(outputs),
                             state_checks, transition_checks, labels, EdgeTarget.pending())
            if self.edges_retained:
                maximum = self.retention.max_retained_edges
                if maximum is not None and len(self.edges) >= maximum:
                    self.edges_retained = False
                elif not self.charge(_edge_bytes(edge)):
                    self.edges_retained = False
                else:
                    self.edges.append(edge)
                    retained = True
            self.emit("edge_evaluated", edge=edge)
        if violations:
            self.record_finding(violations, edge_id, node.path_failed, lambda: self.path(cursor, input))
            if not self.collect:
                self.termination = "failure_found"
                return False
        if self.retention.retain_edges and not retained:
            # The graph can no longer be retained completely.
            self.termination = "graph_retention_limit"
            self.reject(edge_id, "graph_retention_limit")
            return False
        if self.stop_edge(edge_id):
            return False
        if successor_error is not None:
            raise successor_error
        if options.reduction is not None:
            reduced = self.reduce(options.reduction, cursor, state, input, transition, successor_state,
                                  checks, labels, edge_id, representatives)
            if reduced is not None:
                return reduced
        hashed = _call("state hash", _hash, _copy(successor_state))
        if self.stop_edge(edge_id):
            return False
        # Latest collision first, matching enumerate_states and Rust chains.
        bucket = (range(len(self.nodes) - 1, -1, -1) if hashed is None else
                  sorted(self.visited.get(hashed, []) + self.visited.get(None, []), reverse=True))
        duplicate = None
        for other in bucket:
            if self.stop_edge(edge_id):
                return False
            same = _same_state(self.nodes[other].state, successor_state)
            if self.stop_edge(edge_id):
                return False
            if same:
                duplicate = other
                break
        if duplicate is not None:
            self.set_target(edge_id, EdgeTarget.duplicate(duplicate))
            self.duplicate_edges += 1
            self.graph_edges += 1
            self.emit("duplicate_edge", edge=edge_id, node=duplicate)
            return True
        if violations and not self.expand_failing:
            self.failure_pruned = True
            self.reject(edge_id, "failing_successor")
            return True
        if len(self.nodes) == self.config.max_states:
            self.termination = "state_limit"
            self.reject(edge_id, "state_limit")
            return False
        estimate = _estimate(model, successor_state)
        if self.stop_edge(edge_id):
            return False
        total = None if self.estimated is None or estimate is None else self.estimated + estimate
        if total is not None and total > U64_MAX:
            total = None
        if self.limits.max_estimated_state_bytes is not None:
            if estimate is None:
                raise ModelError("state byte budget requires estimated_state_bytes")
            if total is None or total > self.limits.max_estimated_state_bytes:
                self.termination = "estimated_state_byte_limit"
                self.reject(edge_id, "estimated_state_byte_limit")
                return False
        if not self.charge(_saturating_add_u64(NODE_RECORD_BYTES, _check_bytes(state_checks))):
            self.termination = "graph_retention_limit"
            self.reject(edge_id, "graph_retention_limit")
            return False
        successor = len(self.nodes)
        self.visited.setdefault(hashed, []).append(successor)
        self.nodes.append(GraphNode(successor, _copy(successor_state), cursor, _copy(input), edge_id, depth + 1,
                                    state_checks, node.path_failed or bool(violations)))
        self.states, self.estimated = len(self.nodes), total
        self.graph_edges += 1
        self.set_target(edge_id, EdgeTarget.admitted(successor))
        self.emit("successor_admitted", edge=edge_id, node=successor)
        return True

    def reduce(self, reduction, cursor, state, input, transition, successor_state, checks, labels,
               edge_id, representatives) -> bool | None:
        """REDUCE-002. True when collapsed, False when the search ends, None to
        resolve the edge normally."""
        label = _call("reduction eligible_class", reduction.eligible_class, _copy(state), _copy(input))
        if label is not None and not isinstance(label, str):
            raise ModelError("reduction eligible_class must return a string or None")
        if self.stop_edge(edge_id):
            return False
        if label is None:
            return None
        label = str.__str__(label)
        snapshot = Transition(_copy(successor_state), _copy(transition.outputs), transition.disposition)
        projection = _call("reduction projection", reduction.projection, _copy(input), snapshot, labels)
        if not isinstance(projection, (bytes, bytearray)):
            raise ModelError("reduction projection must return bytes")
        projection = bytes(projection)
        if self.stop_edge(edge_id):
            return False
        rejected, reduced_to = [], None
        for rep in representatives.get(label, []):
            if rep.disposition != transition.disposition:
                reason = "disposition_differs"
            elif rep.checks != checks:
                reason = "checks_differ"
            elif rep.projection != projection:
                reason = "projection_differs"
            else:
                if self.stop_edge(edge_id):
                    return False
                same = _same_state(rep.state, successor_state)
                if self.stop_edge(edge_id):
                    return False
                reason = None if same else "successor_differs"
            if reason is None:
                reduced_to = rep.edge
                break
            rejected.append(RejectedEquivalence(rep.edge, reason))
        self.decisions.append(ReductionDecision(edge_id, cursor, label, reduced_to, tuple(rejected)))
        if reduced_to is not None:
            self.set_target(edge_id, EdgeTarget.reduced(reduced_to))
            self.reduced_edges += 1
            self.emit("edge_reduced", edge=edge_id, representative=reduced_to)
            return True
        representatives.setdefault(label, []).append(
            _Representative(edge_id, _copy(successor_state), transition.disposition, checks, projection))
        return None


def explore_graph(model, config: SearchConfig = SearchConfig(), *, limits: SearchLimits | None = None,
                  options: GraphSearchOptions | None = None) -> GraphSearchReport:
    """Breadth-first exploration retaining the admitted graph and its evidence.

    Errors after the search starts raise GraphSearchError carrying the partial
    report (termination and coverage `model_error`).
    """
    limits = SearchLimits() if limits is None else limits
    options = GraphSearchOptions() if options is None else options
    if not isinstance(limits, SearchLimits):
        raise ValueError("limits must be SearchLimits")
    if not isinstance(config, SearchConfig):
        raise ValueError("config must be SearchConfig")
    if not isinstance(options, GraphSearchOptions):
        raise ValueError("options must be GraphSearchOptions")
    # Snapshot caller-owned configuration before any callback can mutate it.
    config = SearchConfig(config.max_states, config.max_transitions, config.max_depth)
    limits = SearchLimits(RunLimits(limits.control.max_duration, limits.control.cancellation),
                          limits.max_estimated_state_bytes)
    policy, retention = options.failure_policy, options.retention
    options = replace(options,
                      failure_policy=FailurePolicy(policy.kind, policy.max_retained_findings, policy.expand_failing_successors),
                      retention=GraphRetention(retention.retain_edges, retention.max_retained_edges, retention.max_graph_bytes))
    run = _GraphRun(model, config, limits, options)
    try:
        run.execute()
        run.finalize()
        run.emit("search_finished", termination=run.termination)
    except Exception as error:
        run.termination = "model_error"
        run.finalize()
        raise GraphSearchError(error, run.report()) from error
    return run.report()


# MARK: reduction-v1 diamond validation (REDUCE-004)
@dataclass(frozen=True)
class DiamondWitness(Generic[I]):
    node: int
    first: I
    second: I
    # state_differs or observations_differ.
    reason: str


@dataclass(frozen=True)
class DiamondReport(Generic[I]):
    # search_complete, or the RunLimits stop reason.
    termination: str
    checked_nodes: int
    candidate_pairs: int
    # Pairs where each input stayed enabled after the other.
    validated: int
    non_commuting: tuple[DiamondWitness[I], ...]


def validate_diamonds(model, report: GraphSearchReport, *, candidate: Callable[[Any, Any], bool],
                      projection: Callable[[Any, Transition], bytes],
                      equal_inputs: Callable[[Any, Any], bool] | None = None,
                      limits: RunLimits = RunLimits()) -> DiamondReport:
    """Executes both orders of each candidate pair at every completely expanded
    node and compares final states (`==`) and the two steps' observations: the
    set of failed (phase, check ID) pairs and the multiset of projections. This
    is evidence about independence, never permission to prune. `equal_inputs`
    defaults to `==` and decides enabled-set membership."""
    if not isinstance(report, GraphSearchReport):
        raise ValueError("report must be GraphSearchReport")
    if not isinstance(limits, RunLimits):
        raise ValueError("limits must be RunLimits")
    control = _Control(RunLimits(limits.max_duration, limits.cancellation))
    same_input = equal_inputs if equal_inputs is not None else (lambda a, b: a == b)
    termination, checked, pairs, validated, witnesses = "search_complete", 0, 0, 0, []

    end = object()

    def enumerate_inputs(state):
        # Copy each yielded input before pulling the next: a generator may reuse
        # one mutable object across yields.
        domain = _call("enumerate inputs", model.inputs, _copy(state))
        iterator = _call("enumerate inputs", iter, domain)
        values = []
        while (value := _call("enumerate inputs", next, iterator, end)) is not end:
            values.append(_copy(value))
        return values

    def boolean(stage, callback, *args):
        value = _call(stage, callback, *(_copy(arg) for arg in args))
        if type(value) is not bool:
            raise ModelError(f"{stage} must return bool")
        return value

    def enabled(input, state):
        return any(boolean("input_equal", same_input, other, input) for other in enumerate_inputs(state))

    def observe(before, input, transition):
        failed = {("state", c.id) for c in _state_checks(model, transition.state) if c.status == "failed"}
        failed |= {("transition", c.id) for c in _transition_checks(model, before, input, transition) if c.status == "failed"}
        value = _call("diamond projection", projection, _copy(input), _copy(transition))
        if not isinstance(value, (bytes, bytearray)):
            raise ModelError("diamond projection must return bytes")
        return failed, bytes(value)

    def result():
        return DiamondReport(termination, checked, pairs, validated, tuple(witnesses))

    for node in report.nodes:
        if node.expansion != "complete":
            continue
        stop = control.stop()
        if stop:
            termination = stop
            return result()
        checked += 1
        candidates = [value for value in enumerate_inputs(node.state)
                      if boolean("diamond candidate", candidate, node.state, value)]
        for i, a in enumerate(candidates):
            for b in candidates[i + 1:]:
                stop = control.stop()
                if stop:
                    termination = stop
                    return result()
                pairs += 1
                ta, tb = _step(model, node.state, a), _step(model, node.state, b)
                if not enabled(b, ta.state) or not enabled(a, tb.state):
                    continue
                tab, tba = _step(model, ta.state, b), _step(model, tb.state, a)
                validated += 1
                same = _same_state(tab.state, tba.state)
                left = [observe(node.state, a, ta), observe(ta.state, b, tab)]
                right = [observe(node.state, b, tb), observe(tb.state, a, tba)]
                same_failures = set().union(*(f for f, _ in left)) == set().union(*(f for f, _ in right))
                same_projections = sorted(p for _, p in left) == sorted(p for _, p in right)
                if not same or not same_failures or not same_projections:
                    witnesses.append(DiamondWitness(node.id, _copy(a), _copy(b),
                                                    "observations_differ" if same else "state_differs"))
    return result()


# MARK: reduction-v1 reduced versus unreduced comparison (REDUCE-005)
@dataclass(frozen=True)
class ReductionProgressPredicates:
    """progress-analysis-v1 predicates applied to both graphs. Kind and rank
    take states so they agree across the two reports."""
    fair: Callable[[GraphEdge], bool]
    kind: Callable[[Any], str]
    rank: Callable[[Any], int] | None = None


@dataclass(frozen=True)
class ReductionComparison:
    # Unreduced node IDs with no equal reduced node, and the reverse.
    missing_in_reduced: tuple[int, ...]
    extra_in_reduced: tuple[int, ...]
    # Unreduced edges whose (source, target, disposition, checks) class has no
    # reduced representative, and the reverse.
    unrepresented_edges: tuple[int, ...]
    extra_reduced_edges: tuple[int, ...]
    findings_match: bool
    coverage_match: bool
    # None when no progress predicates were supplied: progress was not compared.
    progress_match: bool | None
    # Unreduced nodes whose progress class, settlement or closed-component
    # membership differs from their reduced counterpart.
    progress_mismatches: tuple[int, ...]

    @property
    def equivalent(self) -> bool:
        """Not-compared progress (None) does not block equivalence; it is absent
        evidence and is reported as such."""
        return (not self.missing_in_reduced and not self.extra_in_reduced and not self.unrepresented_edges
                and not self.extra_reduced_edges and self.findings_match and self.coverage_match
                and self.progress_match is not False)


def _resolved(report: GraphSearchReport, edge: GraphEdge) -> int | None:
    target, hops = edge.target, 0
    while target.kind == "reduced" and type(target.value) is int and 0 <= target.value < len(report.edges) \
            and hops <= len(report.edges):
        target, hops = report.edges[target.value].target, hops + 1
    return target.value if target.kind in ("admitted", "duplicate") else None


def compare_reduction(unreduced: GraphSearchReport, reduced: GraphSearchReport, *,
                      progress: ReductionProgressPredicates | None = None) -> ReductionComparison:
    """Compares reachable states (hash bucket plus `==`), represented edge classes
    (raw edge counts may differ), finding and failed property counts, coverage
    and, only when predicates are supplied, progress results. Both reports must
    retain edges and be finished. Quadratic; intended for small exhaustive bounds."""
    for report in (unreduced, reduced):
        if not isinstance(report, GraphSearchReport):
            raise ValueError("reports must be GraphSearchReport")
        if not report.edges_retained:
            raise ModelError("reduction comparison requires retained edges")
        if report.coverage.kind == "incomplete":
            raise ModelError("reduction comparison requires finished searches")
    buckets: dict[Any, list[int]] = {}
    for node in reduced.nodes:
        buckets.setdefault(_call("state hash", _hash, _copy(node.state)), []).append(node.id)
    mapping: list[int | None] = [None] * len(unreduced.nodes)
    matched: set[int] = set()
    missing = []
    for node in unreduced.nodes:
        hashed = _call("state hash", _hash, _copy(node.state))
        candidates = (range(len(reduced.nodes)) if hashed is None else
                      sorted(buckets.get(hashed, []) + buckets.get(None, [])))
        for other in candidates:
            if _same_state(node.state, reduced.nodes[other].state):
                mapping[node.id] = other
                matched.add(other)
                break
        if mapping[node.id] is None:
            missing.append(node.id)
    extra = [node.id for node in reduced.nodes if node.id not in matched]
    reverse: list[int | None] = [None] * len(reduced.nodes)
    for u, r in enumerate(mapping):
        if r is not None and 0 <= r < len(reverse):
            reverse[r] = u

    def at(table, index):
        return table[index] if 0 <= index < len(table) else None

    def same(a: GraphEdge, b: GraphEdge) -> bool:
        return (a.disposition == b.disposition and a.state_checks == b.state_checks
                and a.transition_checks == b.transition_checks)

    def mapped(report, edge, table):
        # An edge resolved to a node without a counterpart is never represented.
        node = _resolved(report, edge)
        if node is None:
            return True, None
        other = at(table, node)
        return (False, None) if other is None else (True, other)

    representatives = [edge for edge in reduced.edges if edge.target.kind != "reduced"]
    unrepresented = []
    for edge in unreduced.edges:
        known, target = mapped(unreduced, edge, mapping)
        source = at(mapping, edge.source)
        if source is None or not known or not any(
                other.source == source and _resolved(reduced, other) == target and same(other, edge)
                for other in representatives):
            unrepresented.append(edge.id)
    extra_edges = []
    for edge in representatives:
        known, target = mapped(reduced, edge, reverse)
        source = at(reverse, edge.source)
        if source is None or not known or not any(
                other.source == source and _resolved(unreduced, other) == target and same(other, edge)
                for other in unreduced.edges):
            extra_edges.append(edge.id)
    findings_match = (unreduced.finding_count == reduced.finding_count
                      and unreduced.failed_properties == reduced.failed_properties)
    progress_match, mismatches = None, []
    if progress is not None:
        from .progress import ProgressGraph, analyze_progress

        def analyze(report):
            graph = ProgressGraph.from_report(report, progress.fair)
            rank = (None if progress.rank is None else
                    lambda n: progress.rank(_copy(report.nodes[n].state)))
            return analyze_progress(graph, lambda n: progress.kind(_copy(report.nodes[n].state)), rank)

        u, r = analyze(unreduced), analyze(reduced)
        u_unsettled, r_unsettled = set(u.unsettled_transaction_states), set(r.unsettled_transaction_states)
        u_closed = {n for component in u.closed_nonterminal_components for n in component}
        r_closed = {n for component in r.closed_nonterminal_components for n in component}
        for n in range(len(unreduced.nodes)):
            k = at(mapping, n)
            if k is None:
                continue
            if (u.classes[n] != r.classes[k] or (n in u_unsettled) != (k in r_unsettled)
                    or (n in u_closed) != (k in r_closed)):
                mismatches.append(n)
        # Closed components must correspond member for member under the mapping.
        mapped_closed = {frozenset(at(mapping, n) for n in component if at(mapping, n) is not None)
                         for component in u.closed_nonterminal_components}
        reduced_closed = {frozenset(component) for component in r.closed_nonterminal_components}
        progress_match = not mismatches and mapped_closed == reduced_closed and u.graph_complete == r.graph_complete
    return ReductionComparison(tuple(missing), tuple(extra), tuple(unrepresented), tuple(extra_edges),
                               findings_match, unreduced.coverage == reduced.coverage, progress_match,
                               tuple(mismatches))

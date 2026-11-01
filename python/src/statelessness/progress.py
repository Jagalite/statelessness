"""Candidate progress-analysis-v1 (S3); see spec/progress-analysis-v1.md.

Applications supply node kinds, ranks and edge fairness; this module owns only
graph algorithms. Fair-path reachability and rank checks over a bounded graph
are evidence, not a proof that every scheduler execution eventually completes.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .core import ModelError, U64_MAX, _call, _copy

# Application-declared node kinds and the resulting classes (PROGRESS-003).
PROGRESS_KINDS = ("terminal", "intentionalWait", "externalWait", "transaction", "other")
PROGRESS_CLASSES = ("terminal", "stableIntentionalWait", "declaredExternalWait",
                    "transactionProgress", "stuck", "unmeasuredCutoff")
_SETTLED = ("terminal", "stableIntentionalWait", "declaredExternalWait")


@dataclass(frozen=True)
class ProgressEdge:
    source: int
    target: int
    fair: bool


@dataclass(frozen=True)
class ProgressGraph:
    """Dense node IDs, directed edges with a fairness flag, and one `measured`
    flag per node. A node is unmeasured when its outgoing edges are not all
    known: a depth frontier, an interrupted or unexpanded node, or a rejected
    successor."""
    node_count: int
    edges: tuple[ProgressEdge, ...]
    measured: tuple[bool, ...]

    def __post_init__(self) -> None:
        if type(self.node_count) is not int or self.node_count < 0:
            raise ValueError("progress graph node_count must be a nonnegative integer")
        object.__setattr__(self, "edges", tuple(self.edges))
        object.__setattr__(self, "measured", tuple(self.measured))
        if len(self.measured) != self.node_count or any(type(flag) is not bool for flag in self.measured):
            raise ValueError("progress graph needs one measured flag per node")
        for edge in self.edges:
            if not isinstance(edge, ProgressEdge) or type(edge.fair) is not bool:
                raise ValueError("progress edges must be ProgressEdge values")
            if any(type(end) is not int or not 0 <= end < self.node_count for end in (edge.source, edge.target)):
                raise ValueError("progress edge endpoint out of range")

    @classmethod
    def from_report(cls, report: Any, fair: Callable[[Any], bool]) -> ProgressGraph:
        """From a graph-search-v1 report with retained edges. Admitted and
        duplicate edges keep their targets; reduced edges take their
        representative's target; a node is measured only when its expansion is
        complete and none of its edges was rejected."""
        if not report.edges_retained:
            raise ModelError("progress analysis requires retained edges")
        measured = [node.expansion == "complete" for node in report.nodes]
        edges = []
        for edge in report.edges:
            target, hops = edge.target, 0
            while target.kind == "reduced" and type(target.value) is int and 0 <= target.value < len(report.edges) \
                    and hops <= len(report.edges):
                target, hops = report.edges[target.value].target, hops + 1
            if target.kind in ("admitted", "duplicate"):
                flag = _call("progress fairness", fair, _copy(edge))
                if type(flag) is not bool:
                    raise ModelError("progress fairness must return bool")
                edges.append(ProgressEdge(edge.source, target.value, flag))
            else:
                measured[edge.source] = False
        return cls(len(report.nodes), tuple(edges), tuple(measured))


@dataclass(frozen=True)
class ProgressReport:
    classes: tuple[str, ...]
    terminal_states: int
    stable_intentional_wait_states: int
    external_wait_states: int
    unmeasured_states: int
    transaction_states: int
    settled_transaction_states: int
    # transactionProgress nodes with no fair path to a settled node.
    unsettled_transaction_states: tuple[int, ...]
    stuck_states: tuple[int, ...]
    # Members ascending; components ordered by smallest member.
    closed_nonterminal_components: tuple[tuple[int, ...], ...]
    # Closed candidates excluded because a member is unmeasured.
    unmeasured_components: int
    maximum_rank: int
    # Every node is measured; otherwise absence of a finding is not evidence.
    graph_complete: bool


def strongly_connected_components(node_count: int, edges: Iterable[tuple[int, int]]) -> tuple[tuple[int, ...], ...]:
    """Tarjan's algorithm with an explicit work stack (no recursion). Members are
    sorted ascending and components ordered by their smallest member."""
    adjacency: list[list[int]] = [[] for _ in range(node_count)]
    for source, target in edges:
        adjacency[source].append(target)
    index = [-1] * node_count
    low = [0] * node_count
    on_stack = [False] * node_count
    stack: list[int] = []
    components: list[tuple[int, ...]] = []
    counter = 0
    for root in range(node_count):
        if index[root] >= 0:
            continue
        work = [[root, 0]]
        while work:
            frame = work[-1]
            v = frame[0]
            if frame[1] == 0 and index[v] < 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack[v] = True
            if frame[1] < len(adjacency[v]):
                w = adjacency[v][frame[1]]
                frame[1] += 1
                if index[w] < 0:
                    work.append([w, 0])
                elif on_stack[w]:
                    low[v] = min(low[v], index[w])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
            if low[v] == index[v]:
                component = []
                while stack:
                    w = stack.pop()
                    on_stack[w] = False
                    component.append(w)
                    if w == v:
                        break
                components.append(tuple(sorted(component)))
    return tuple(sorted(components, key=lambda component: component[0]))


def reverse_reachable(node_count: int, edges: Iterable[ProgressEdge], targets: Iterable[int],
                      include: Callable[[ProgressEdge], bool] | None = None) -> tuple[bool, ...]:
    """Nodes with a path to any target over edges accepted by `include`
    (breadth-first over the reversed edges)."""
    reverse: list[list[int]] = [[] for _ in range(node_count)]
    for edge in edges:
        if include is None or include(edge):
            reverse[edge.target].append(edge.source)
    reached = [False] * node_count
    queue: deque[int] = deque()
    for target in targets:
        if not reached[target]:
            reached[target] = True
            queue.append(target)
    while queue:
        v = queue.popleft()
        for u in reverse[v]:
            if not reached[u]:
                reached[u] = True
                queue.append(u)
    return tuple(reached)


def analyze_progress(graph: ProgressGraph, kind: Callable[[int], str],
                     rank: Callable[[int], int] | None = None) -> ProgressReport:
    """Classifies nodes (unmeasured, terminal, external wait, intentional wait,
    transaction with a fair outgoing edge, otherwise stuck) and reports closed
    nonterminal components: SCCs with a transactionProgress or stuck member, no
    edge leaving, no fair rank-decreasing edge and no unmeasured member."""
    if not isinstance(graph, ProgressGraph):
        raise ValueError("graph must be ProgressGraph")
    n = graph.node_count
    outgoing: list[list[ProgressEdge]] = [[] for _ in range(n)]
    for edge in graph.edges:
        outgoing[edge.source].append(edge)
    classes: list[str] = []
    ranks: list[int] = []
    for v in range(n):
        value = 0 if rank is None else _call("progress rank", rank, v)
        if type(value) is not int or not 0 <= value <= U64_MAX:
            raise ModelError("progress rank must return an unsigned 64-bit integer")
        ranks.append(value)
        if not graph.measured[v]:
            classes.append("unmeasuredCutoff")
            continue
        declared = _call("progress kind", kind, v)
        if type(declared) is not str or declared not in PROGRESS_KINDS:
            raise ModelError("progress kind must be one of " + ", ".join(PROGRESS_KINDS))
        if declared == "terminal":
            classes.append("terminal")
        elif declared == "externalWait":
            classes.append("declaredExternalWait")
        elif declared == "intentionalWait":
            classes.append("stableIntentionalWait")
        elif declared == "transaction":
            classes.append("transactionProgress" if any(e.fair for e in outgoing[v]) else "stuck")
        else:
            classes.append("stuck")
    settled = [v for v in range(n) if classes[v] in _SETTLED]
    can_settle = reverse_reachable(n, graph.edges, settled, lambda e: e.fair)
    closed, unmeasured_components = [], 0
    for component in strongly_connected_components(n, ((e.source, e.target) for e in graph.edges)):
        if not any(classes[v] in ("transactionProgress", "stuck") for v in component):
            continue
        members = set(component)
        exits = [e for v in component for e in outgoing[v]]
        if any(e.target not in members for e in exits) or any(e.fair and ranks[e.target] < ranks[e.source] for e in exits):
            continue
        if any(not graph.measured[v] for v in component):
            unmeasured_components += 1
            continue
        closed.append(component)
    transactions = [v for v in range(n) if classes[v] == "transactionProgress"]
    return ProgressReport(
        tuple(classes), classes.count("terminal"), classes.count("stableIntentionalWait"),
        classes.count("declaredExternalWait"), classes.count("unmeasuredCutoff"), len(transactions),
        sum(1 for v in transactions if can_settle[v]), tuple(v for v in transactions if not can_settle[v]),
        tuple(v for v in range(n) if classes[v] == "stuck"), tuple(closed), unmeasured_components,
        max(ranks, default=0), all(graph.measured))

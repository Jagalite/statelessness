"""reduction-v1 (S4): grouping, rejection reasons, comparison, diamonds, negative controls."""
from dataclasses import replace
import unittest

from statelessness import (Check, Disposition, EdgeTarget, EquivalenceReduction, FailurePolicy,
                           GraphSearchOptions, ModelError, ProgressEdge, ProgressGraph, ReductionDecision,
                           ReductionProgressPredicates, RejectedEquivalence, RunLimits, SearchConfig,
                           Transition, analyze_progress, compare_reduction, explore_graph, validate_diamonds)

COLLECT = FailurePolicy.collect(10, True)


class Late:
    """Inputs 10..19 are late results (class "late"); input k leads to k % 10."""
    def __init__(self, step=None, fail_edge=(), inputs=None):
        self._step = step or (lambda s, i: Transition(i % 10, (i % 10,)))
        self.fail_edge = set(fail_edge)
        self.domain = {0: [10, 11, 12, 1], 1: [2], 2: []} if inputs is None else inputs
    def initial_state(self): return 0
    def inputs(self, state): return list(self.domain.get(state, ()))
    def step(self, state, input): return self._step(state, input)
    def check_state(self, state): return (Check("ok"),)
    def check_transition(self, before, input, transition):
        return (Check("late", "failed", str(input)),) if input in self.fail_edge else ()


BY_DECADE = EquivalenceReduction(lambda s, i: "late" if i >= 10 else None, lambda i, t, labels: bytes(t.outputs))


def explore(model, reduction=None, policy=COLLECT):
    return explore_graph(model, options=GraphSearchOptions(failure_policy=policy, reduction=reduction))


def same_late():
    return Late(lambda s, i: Transition(1 if i >= 10 else i, (7,)))


class ReductionTests(unittest.TestCase):
    def test_equivalent_edges_collapse_into_representative(self):
        distinct = explore(Late(), BY_DECADE)
        self.assertEqual(distinct.reduced_edges, 0)
        self.assertEqual([len(d.rejected) for d in distinct.reduction_decisions], [0, 1, 2])
        self.assertEqual(distinct.reduction_decisions[1].rejected, (RejectedEquivalence(0, "projection_differs"),))
        unreduced, reduced = explore(same_late()), explore(same_late(), BY_DECADE)
        self.assertEqual([e.target for e in reduced.edges], [EdgeTarget.admitted(1), EdgeTarget.reduced(0),
                         EdgeTarget.reduced(0), EdgeTarget.duplicate(1), EdgeTarget.admitted(2)])
        self.assertEqual(reduced.reduced_edges, 2)
        self.assertEqual(reduced.transitions, unreduced.transitions, "reduction never skips execution")
        self.assertEqual(reduced.reduction_decisions, (ReductionDecision(0, 0, "late", None, ()),
                                                       ReductionDecision(1, 0, "late", 0, ()),
                                                       ReductionDecision(2, 0, "late", 0, ())))
        comparison = compare_reduction(unreduced, reduced)
        self.assertTrue(comparison.equivalent, comparison)
        # Review regression: without predicates progress is reported as not compared.
        self.assertIsNone(comparison.progress_match)
        def progress(r):
            return analyze_progress(ProgressGraph.from_report(r, lambda e: True),
                                    lambda n: "terminal" if r.nodes[n].state == 2 else "transaction")
        self.assertEqual(progress(reduced), progress(unreduced))
        predicates = ReductionProgressPredicates(lambda e: True, lambda s: "terminal" if s == 2 else "transaction")
        with_progress = compare_reduction(unreduced, reduced, progress=predicates)
        self.assertIs(with_progress.progress_match, True)
        self.assertTrue(with_progress.equivalent)

    def test_progress_difference_is_not_equivalent(self):
        def one_edge(input): return Late(lambda s, i: Transition(1), inputs={0: [input]})
        a, b = explore(one_edge(5)), explore(one_edge(6))
        self.assertTrue(compare_reduction(a, b).equivalent, "structure alone matches")
        predicates = ReductionProgressPredicates(lambda e: e.input == 5, lambda s: "terminal" if s == 1 else "transaction")
        c = compare_reduction(a, b, progress=predicates)
        self.assertEqual((c.progress_match, c.progress_mismatches, c.equivalent), (False, (0,), False))

    def test_rejected_equivalences_are_inspectable(self):
        successor = Late(lambda s, i: Transition(i % 10, (7,)))
        self.assertEqual(explore(successor, BY_DECADE).reduction_decisions[1].rejected,
                         (RejectedEquivalence(0, "successor_differs"),))
        failing = Late(lambda s, i: Transition(1 if i >= 10 else i, (7,)), fail_edge=[11])
        checks = explore(failing, BY_DECADE)
        self.assertEqual(checks.reduction_decisions[1].rejected, (RejectedEquivalence(0, "checks_differ"),))
        self.assertEqual((checks.reduction_decisions[2].reduced_to, checks.finding_count), (0, 1))
        rejected = Late(lambda s, i: Transition(1 if i >= 10 else i, (7,),
                                                Disposition("rejected", "stale") if i == 11 else Disposition()))
        self.assertEqual([r.reason for r in explore(rejected, BY_DECADE).reduction_decisions[1].rejected],
                         ["disposition_differs"])
        self.assertTrue(compare_reduction(explore(failing), checks).equivalent)

    def test_incorrect_reduction_is_detected(self):
        model = Late(lambda s, i: Transition(1 if i >= 10 else i, (7,)), fail_edge=[12])
        unreduced, reduced = explore(model), explore(model, BY_DECADE)
        self.assertTrue(compare_reduction(unreduced, reduced).equivalent)
        edges = list(reduced.edges)
        edges[4] = replace(edges[4], target=EdgeTarget.rejected("tampered"))
        missing_state = replace(reduced, nodes=reduced.nodes[:-1], edges=tuple(edges))
        a = compare_reduction(unreduced, missing_state)
        self.assertEqual((a.equivalent, a.missing_in_reduced, a.unrepresented_edges), (False, (2,), (4,)))
        lost_finding = replace(reduced, finding_count=0)
        self.assertFalse(compare_reduction(unreduced, lost_finding).findings_match)
        edges = list(reduced.edges)
        edges[3] = replace(edges[3], target=EdgeTarget.duplicate(2))
        c = compare_reduction(unreduced, replace(reduced, edges=tuple(edges)))
        # Unreduced 0->1 edges remain represented by edge 0; the 0->2 edge has no counterpart.
        self.assertEqual((c.equivalent, c.unrepresented_edges, c.extra_reduced_edges), (False, (), (3,)))
        stopped = explore(model, policy=FailurePolicy.stop_at_first())
        with self.assertRaises(ModelError): compare_reduction(stopped, reduced)

    def test_reduction_events_and_stop_order(self):
        events = []
        options = GraphSearchOptions(failure_policy=COLLECT, reduction=BY_DECADE, events=lambda e: events.append(e.kind))
        r = explore_graph(same_late(), options=options)
        self.assertEqual(events.count("edge_reduced"), 2)
        self.assertEqual(r.graph_edges, 3)


class DiamondTests(unittest.TestCase):
    def test_diamond_validation(self):
        class Counters:
            def initial_state(self): return (0, 0)
            def inputs(self, s): return [i for i in (0, 1) if s[i] < 2]
            def step(self, s, i): return Transition(tuple(v + (k == i) for k, v in enumerate(s)))
            def check_state(self, s): return ()
        counters = Counters()
        report = explore_graph(counters)
        ok = validate_diamonds(counters, report, candidate=lambda s, i: True, projection=lambda i, t: b"")
        self.assertEqual((ok.termination, ok.validated, ok.non_commuting), ("search_complete", 4, ()))
        class Arithmetic:
            def initial_state(self): return 1
            def inputs(self, s): return [0, 1] if s < 3 else []
            def step(self, s, i): return Transition(s * 2 if i == 0 else s + 1, (i,))
            def check_state(self, s): return ()
        arithmetic = Arithmetic()
        bad = validate_diamonds(arithmetic, explore_graph(arithmetic, SearchConfig(max_depth=0)),
                                candidate=lambda s, i: True, projection=lambda i, t: b"")
        self.assertEqual((bad.checked_nodes, bad.validated), (0, 0))
        deep = validate_diamonds(arithmetic, explore_graph(arithmetic), candidate=lambda s, i: True,
                                 projection=lambda i, t: b"", equal_inputs=lambda a, b: a == b)
        self.assertEqual((deep.validated, len(deep.non_commuting)), (1, 1))
        w = deep.non_commuting[0]
        self.assertEqual((w.node, w.first, w.second, w.reason), (0, 0, 1, "state_differs"))
        observed = validate_diamonds(counters, report, candidate=lambda s, i: True,
                                     projection=lambda i, t: bytes([i, t.state[0]]))
        self.assertEqual([w.reason for w in observed.non_commuting], ["observations_differ"] * 4)
        cancelled = validate_diamonds(counters, report, candidate=lambda s, i: True, projection=lambda i, t: b"",
                                      limits=RunLimits(cancellation=lambda: True))
        self.assertEqual(cancelled.termination, "cancelled")


class NegativeControlTests(unittest.TestCase):
    """Each control introduces one defect and requires the evidence to expose it."""
    class M:
        def __init__(self, edges=None, failing=(3,), state=int):
            self.edges = {0: [1, 2], 1: [3], 2: [3]} if edges is None else edges
            self.failing, self.state = set(failing), state
        def initial_state(self): return self.state(0)
        def inputs(self, s): return list(self.edges.get(int(s), ()))
        def step(self, s, i): return Transition(self.state(i))
        def check_state(self, s):
            return (Check("bad", "failed", str(int(s))),) if int(s) in self.failing else (Check("bad"),)

    def explore(self, model, config=SearchConfig()):
        return explore_graph(model, config, options=GraphSearchOptions(failure_policy=FailurePolicy.collect(8, True)))

    def test_missing_transition_is_detected(self):
        full, missing = self.explore(self.M()), self.explore(self.M({0: [1, 2], 1: [3]}))
        c = compare_reduction(full, missing)
        self.assertEqual((c.equivalent, c.unrepresented_edges, c.findings_match), (False, (3,), False))

    def test_incorrect_identity_merging_is_detected(self):
        class Lossy(int):
            # States 1 and 2 are merged by a lossy equality with a colliding hash.
            def __eq__(self, other): return int(self) == int(other) or (0 < self < 3 and 0 < other < 3)
            def __hash__(self): return 1 if int(self) == 2 else int(self)
        correct, lossy = self.explore(self.M()), self.explore(self.M(state=Lossy))
        self.assertEqual(len(lossy.nodes), 3)
        c = compare_reduction(correct, lossy)
        self.assertEqual((c.equivalent, c.missing_in_reduced), (False, (2,)))

    def test_suppressed_check_is_detected(self):
        checked, suppressed = self.explore(self.M()), self.explore(self.M(failing=()))
        self.assertEqual((checked.finding_count, suppressed.finding_count), (2, 0))
        c = compare_reduction(checked, suppressed)
        self.assertEqual((c.findings_match, c.unrepresented_edges), (False, (2, 3)), "edges carry their check batches")

    def test_false_progress_is_detected(self):
        truth = analyze_progress(ProgressGraph(2, (ProgressEdge(0, 1, True), ProgressEdge(1, 0, True)), (True, True)),
                                 lambda v: "transaction")
        self.assertEqual(truth.closed_nonterminal_components, ((0, 1),))
        truncated = analyze_progress(ProgressGraph(2, (ProgressEdge(0, 1, True),), (True, False)), lambda v: "transaction")
        self.assertEqual(truncated.closed_nonterminal_components, ())
        self.assertFalse(truncated.graph_complete, "a cutoff is not reported as progress")
        self.assertNotEqual(truth, truncated)
        bounded = self.explore(self.M(), SearchConfig(max_depth=1))
        self.assertFalse(analyze_progress(ProgressGraph.from_report(bounded, lambda e: True),
                                          lambda v: "transaction").graph_complete)


if __name__ == "__main__":
    unittest.main()

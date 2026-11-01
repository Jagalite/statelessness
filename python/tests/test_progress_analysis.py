"""progress-analysis-v1 (S3): shared fixtures, iterative SCCs and graphs from reports."""
import unittest

from statelessness import (ProgressEdge, ProgressGraph, SearchConfig, Transition, analyze_progress,
                           explore_graph, reverse_reachable, strongly_connected_components)
from migration_helpers import corpus


def graph(n, edges, unmeasured=()):
    return ProgressGraph(n, tuple(ProgressEdge(*e) for e in edges), tuple(v not in unmeasured for v in range(n)))


class ProgressFixtureTests(unittest.TestCase):
    def test_progress_analysis_fixtures(self):
        cases = corpus("progress-analysis-v1.json")
        self.assertEqual(len(cases), 9)
        for case in cases:
            with self.subTest(case=case["case"]):
                g = graph(case["nodes"], case["edges"], set(case["unmeasured"]))
                ranks = case["ranks"]
                r = analyze_progress(g, lambda v: case["kinds"][v], None if ranks is None else lambda v: ranks[v])
                x = case["expected"]
                self.assertEqual(list(r.classes), x["classes"])
                self.assertEqual([list(c) for c in r.closed_nonterminal_components], x["closed"])
                self.assertEqual(list(r.unsettled_transaction_states), x["unsettled"])
                self.assertEqual(r.settled_transaction_states, x["settled_transactions"])
                self.assertEqual(r.unmeasured_components, x["unmeasured_components"])
                self.assertEqual(r.graph_complete, x["graph_complete"])


class ProgressAnalysisTests(unittest.TestCase):
    def test_counts_and_ranks(self):
        r = analyze_progress(graph(3, [(0, 1, True), (0, 2, True), (1, 1, False)]),
                             lambda v: ["transaction", "intentionalWait", "externalWait"][v])
        self.assertEqual((r.stable_intentional_wait_states, r.external_wait_states, r.settled_transaction_states), (1, 1, 1))
        self.assertEqual(r.closed_nonterminal_components, ())
        cycle = analyze_progress(graph(3, [(0, 1, True), (1, 0, True), (2, 0, True)]), lambda v: "transaction",
                                 lambda v: (1 << 64) - 1)
        self.assertEqual((cycle.closed_nonterminal_components, cycle.maximum_rank), (((0, 1),), (1 << 64) - 1))
        unfair = analyze_progress(graph(2, [(0, 1, False)]), lambda v: ["transaction", "terminal"][v])
        self.assertEqual((unfair.classes, unfair.stuck_states), (("stuck", "terminal"), (0,)))
        with self.assertRaises(Exception): analyze_progress(graph(1, []), lambda v: "bogus")
        with self.assertRaises(Exception): analyze_progress(graph(1, []), lambda v: "terminal", lambda v: -1)

    def test_iterative_scc_handles_deep_graphs(self):
        n = 200_000
        chain = [(v, v + 1) for v in range(n - 1)] + [(n - 1, 0)]
        components = strongly_connected_components(n, chain)
        self.assertEqual((len(components), len(components[0])), (1, n))
        self.assertEqual(strongly_connected_components(4, [(0, 1), (1, 0), (2, 3)]), ((0, 1), (2,), (3,)))
        self.assertEqual(reverse_reachable(4, [ProgressEdge(0, 1, True), ProgressEdge(2, 1, False)], [1], lambda e: e.fair),
                         (True, True, False, False))

    def test_graph_from_search_report(self):
        class M:
            def initial_state(self): return 0
            def inputs(self, state): return {0: [1, 2], 1: [3], 2: [2]}.get(state, [])
            def step(self, state, input): return Transition(input)
            def check_state(self, state): return ()
        report = explore_graph(M())
        g = ProgressGraph.from_report(report, lambda e: e.input != 2)
        self.assertEqual(g.edges, (ProgressEdge(0, 1, True), ProgressEdge(0, 2, False), ProgressEdge(1, 3, True),
                                   ProgressEdge(2, 2, False)))
        r = analyze_progress(g, lambda v: "terminal" if report.nodes[v].state == 3 else "transaction")
        self.assertEqual(r.classes, ("transactionProgress", "transactionProgress", "stuck", "terminal"))
        self.assertEqual(r.closed_nonterminal_components, ((2,),))
        bounded = explore_graph(M(), SearchConfig(max_depth=1))
        self.assertEqual(ProgressGraph.from_report(bounded, lambda e: True).measured, (True, False, False))
        with self.assertRaises(ValueError): ProgressGraph(1, (ProgressEdge(0, 1, True),), (True,))
        with self.assertRaises(ValueError): ProgressGraph(2, (), (True,))


if __name__ == "__main__":
    unittest.main()

"""graph-search-v1 (S1/S2): bfs-controls equivalence, IDs, events, coverage, collection."""
import unittest

from statelessness import (Check, EdgeTarget, FailurePolicy, GraphCoverage, GraphRetention,
                           GraphSearchError, GraphSearchOptions, ModelError, PropertyFailureCount,
                           RunLimits, SearchConfig, SearchLimits, Transition, enumerate_states,
                           explore_graph)
from migration_helpers import CHAIN, DIAMOND, GraphModel, corpus, search, targets

COLLECT = FailurePolicy.collect(10, True)
PRUNE = FailurePolicy.collect(10, False)


def bfs_fixture(case, log):
    """The bfs-controls-v1 fixture model of test_bfs_controls, logging every callback."""
    class State:
        def __init__(self, value): self.value = value
        def __hash__(self): log(f"hash:{self.value}"); return 0
        def __eq__(self, other):
            log(f"equal:{self.value}:{other.value}")
            return self.value == other.value
    class Fixture:
        def initial_state(self): log("initial"); return State(0)
        def check_state(self, state):
            event = f"state:{state.value}"
            log(event)
            if event == case["error_at"]:
                if case.get("error_prefix", False): yield Check("prefix")
                raise ModelError("fixture check error")
            yield Check("ok")
            if case["skipped"]: yield Check("skip", "skipped", "not run")
            if case["fail_initial"] and state.value == 0 or case["fail_state"] == state.value:
                yield Check("bad", "failed", "broken")
        def estimated_state_bytes(self, state):
            log(f"estimate:{state.value}")
            value = case["estimates"][state.value]
            return None if value is None else int(value)
        def inputs(self, state):
            value = state.value
            log(f"inputs:{value}")
            fixture = case["fixture"]
            domain = ([] if fixture == "empty" else [0, 0] if fixture == "loop" else
                      ([1] if value < 2 else []) if fixture == "chain" else
                      [1, 2, 1] if value == 0 else [3] if value in (1, 2) else [])
            class Cursor:
                def __init__(self): self.position = 0
                def __iter__(self): return self
                def __next__(self):
                    if self.position == len(domain):
                        log(f"next:{value}:end")
                        raise StopIteration()
                    input = domain[self.position]
                    self.position += 1
                    log(f"next:{value}:{input}")
                    return input
            return Cursor()
        def step(self, state, input):
            log(f"step:{state.value}:{input}")
            return Transition(State(input if case["fixture"] == "diamond" else state.value + input))
        def check_transition(self, before, input, transition):
            event = f"transition:{before.value}:{input}:{transition.state.value}"
            log(event)
            if event == case["error_at"]:
                if case.get("error_prefix", False): yield Check("prefix")
                raise ModelError("fixture check error")
            yield from ()
    class Observer:
        def needs_history(self): return case["needs_history"]
        def initial(self, state, checks): log(f"observe_initial:{state.value}")
        def transition(self, before, input, transition, state_checks, edge_checks, history):
            sequence = ",".join(map(str, history)) if history else "-"
            log(f"observe:{before.value}:{input}:{transition.state.value}:{sequence}")
        def check_error(self, phase, checks, complete):
            log(f"check_error:{phase}:{str(complete).lower()}:{len(checks)}")
    return Fixture(), Observer()


def run_bfs_case(case, graph):
    events = []
    cancelled = case["entry_control"] in ("cancelled", "both")
    def log(event):
        nonlocal cancelled
        events.append(event)
        if event == case["cancel_at"]: cancelled = True
    model, observer = bfs_fixture(case, log)
    cap = case["max_estimated_state_bytes"]
    try:
        config = SearchConfig(case["max_states"], case["max_transitions"], case["max_depth"])
        control = RunLimits(max_duration=0 if case["entry_control"] in ("deadline", "both") else None,
                            cancellation=lambda: cancelled)
        limits = SearchLimits(control, None if cap is None else int(cap))
        if graph:
            r = explore_graph(model, config, limits=limits, options=GraphSearchOptions(observer=observer))
        else:
            r = enumerate_states(model, config, limits=limits, observer=observer)
    except (ModelError, ValueError) as error:
        return ("error", str(error), isinstance(error, ModelError)), events
    failure = None if r.failure is None else (r.failure.inputs, r.failure.violations)
    return (r.termination, r.states, r.transitions, r.max_depth_reached, r.skipped_checks,
            r.estimated_retained_state_bytes, failure), events


class BfsControlsEquivalenceTests(unittest.TestCase):
    def test_default_options_reproduce_bfs_controls_corpus(self):
        cases = corpus("bfs-controls-v1.json")
        self.assertGreater(len(cases), 0)
        for case in cases:
            with self.subTest(case=case["case"]):
                portable, portable_events = run_bfs_case(case, graph=False)
                extended, extended_events = run_bfs_case(case, graph=True)
                self.assertEqual(portable_events, case["events"])
                self.assertEqual(extended_events, portable_events)
                self.assertEqual(extended, portable)


class GraphIdentityTests(unittest.TestCase):
    def test_diamond_ids_and_event_order(self):
        r, events = search(GraphModel(DIAMOND))
        self.assertEqual(events, ["root:0", "expand:0", "edge:0:0:1", "admit:0:1", "edge:1:0:2", "admit:1:2",
                                  "edge:2:0:1", "dup:2:1", "finish:0:complete", "layer:0", "expand:1", "edge:3:1:3",
                                  "admit:3:3", "finish:1:complete", "expand:2", "edge:4:2:3", "dup:4:3",
                                  "finish:2:complete", "layer:1", "expand:3", "finish:3:complete", "layer:2",
                                  "end:graph_exhausted"])
        self.assertEqual(r.coverage, GraphCoverage("exhaustive"))
        self.assertEqual([n.state for n in r.nodes], [0, 1, 2, 3])
        self.assertEqual([n.parent for n in r.nodes], [None, 0, 0, 1])
        self.assertEqual([n.depth for n in r.nodes], [0, 1, 1, 2])
        self.assertEqual([n.admitting_edge for n in r.nodes], [None, 0, 1, 3])
        self.assertEqual([e.id for e in r.edges], [0, 1, 2, 3, 4])
        self.assertEqual(targets(r), ["admitted:1", "admitted:2", "duplicate:1", "admitted:3", "duplicate:3"])
        self.assertEqual([e.outputs for e in r.edges], [(10,), (20,), (10,), (30,), (30,)])
        self.assertEqual((r.transitions, r.graph_edges, r.duplicate_edges, r.rejected_successors), (5, 5, 2, 0))
        self.assertEqual((r.frontier_size, r.completed_layers, r.states), (0, 3, 4))
        self.assertIsNone(r.failure)
        self.assertGreater(r.estimated_graph_bytes, 0)

    def test_self_loop_and_hash_collisions_keep_structure(self):
        loop, _ = search(GraphModel({0: [0, 0]}))
        self.assertEqual(targets(loop), ["duplicate:0", "duplicate:0"])
        self.assertEqual((len(loop.nodes), str(loop.coverage)), (1, "exhaustive"))
        collide, events = search(GraphModel(DIAMOND, constant=True))
        distinct, expected = search(GraphModel(DIAMOND))
        self.assertEqual(events, expected)
        self.assertEqual(targets(collide), targets(distinct))
        self.assertEqual([n.state for n in collide.nodes], [n.state for n in distinct.nodes])

    def test_unhashable_states_use_exhaustive_bucket(self):
        class Lists(GraphModel):
            def initial_state(self): return [0]
            def inputs(self, state): return list(self.edges.get(state[0], ()))
            def step(self, state, input): return Transition([input])
            def check_state(self, state): return ()
            def check_transition(self, before, input, transition): return ()
        r, _ = search(Lists(DIAMOND))
        self.assertEqual(targets(r), ["admitted:1", "admitted:2", "duplicate:1", "admitted:3", "duplicate:3"])


class GraphLimitTests(unittest.TestCase):
    def test_duplicate_failing_edge_keeps_evidence_and_unresolved_edge_is_counted(self):
        model = GraphModel(DIAMOND, fail_edge=[(2, 3)])
        r, events = search(model, policy=PRUNE)
        self.assertIn("finding:0:4:transition/bad_edge", events)
        self.assertEqual(r.edges[4].target, EdgeTarget.duplicate(3))
        self.assertTrue(r.edges[4].failed)
        self.assertEqual([f.witness for f in r.findings], [(2, 3)])
        self.assertEqual(r.findings[0].edge, 4)
        self.assertEqual((str(r.coverage), r.termination), ("exhaustive", "graph_exhausted"))
        stop, _ = search(model)
        self.assertEqual((stop.termination, str(stop.coverage)), ("failure_found", "incomplete:failure_found"))
        self.assertEqual(stop.edges[-1].target, EdgeTarget.rejected("failure_found"))
        self.assertEqual((stop.nodes[2].expansion, stop.nodes[2].interrupted_reason), ("interrupted", "failure_found"))
        self.assertEqual((stop.failure.inputs, stop.rejected_successors), ((2, 3), 1))
        # Review regression: the unresolved edge is counted even when only streamed.
        streamed, _ = search(model, retention=GraphRetention(retain_edges=False))
        self.assertEqual((streamed.rejected_successors, streamed.edges), (1, ()))
        silent = explore_graph(model, options=GraphSearchOptions(retention=GraphRetention(retain_edges=False)))
        self.assertEqual((silent.rejected_successors, silent.edges), (1, ()))

    def test_state_cap_on_new_successor(self):
        r, events = search(GraphModel(DIAMOND), SearchConfig(max_states=2))
        self.assertEqual(events[-3:], ["edge:1:0:2", "reject:1:state_limit", "end:state_limit"])
        self.assertEqual((r.termination, str(r.coverage)), ("state_limit", "incomplete:state_limit"))
        self.assertEqual(targets(r), ["admitted:1", "rejected:state_limit"])
        self.assertEqual(r.rejected_successors, 1)
        self.assertEqual([(n.expansion, n.interrupted_reason) for n in r.nodes],
                         [("interrupted", "state_limit"), ("unexpanded", None)])
        self.assertEqual((r.frontier_size, r.completed_layers), (2, 0))

    def test_edge_cap_midway(self):
        r, _ = search(GraphModel(DIAMOND), SearchConfig(max_transitions=2))
        self.assertEqual((r.termination, r.transitions, len(r.edges)), ("transition_limit", 2, 2))
        self.assertEqual(r.nodes[0].expansion, "interrupted")
        self.assertEqual(str(r.coverage), "incomplete:transition_limit")

    def test_cancellation_and_deadline(self):
        r, events = search(GraphModel(DIAMOND), cancel_after="admit:3:3")
        self.assertEqual((r.termination, events[-1]), ("cancelled", "end:cancelled"))
        self.assertEqual([n.expansion for n in r.nodes], ["complete", "interrupted", "unexpanded", "unexpanded"])
        self.assertEqual(r.frontier_size, 3)
        late, _ = search(GraphModel(DIAMOND), cancel_after="edge:4:2:3")
        self.assertEqual((late.edges[4].target, late.rejected_successors), (EdgeTarget.rejected("cancelled"), 1))
        expired, none = search(GraphModel(DIAMOND), max_duration=0)
        self.assertEqual((expired.termination, expired.states, expired.nodes, none), ("deadline", 0, (), ["end:deadline"]))

    def test_zero_depth_and_depth_bound_coverage(self):
        zero, events = search(GraphModel(DIAMOND), SearchConfig(max_depth=0))
        self.assertEqual(events, ["root:0", "expand:0", "finish:0:depthFrontier", "layer:0", "end:depth_bound"])
        self.assertEqual((zero.termination, zero.coverage, zero.transitions),
                         ("depth_bound", GraphCoverage("bounded", True, False), 0))
        one, _ = search(GraphModel(DIAMOND), SearchConfig(max_depth=1))
        self.assertEqual((str(one.coverage), one.frontier_size), ("bounded:depth_cutoff", 2))
        # Reaching max_depth at a leaf is not a cutoff.
        leaf, _ = search(GraphModel(DIAMOND), SearchConfig(max_depth=2))
        self.assertEqual((str(leaf.coverage), leaf.termination), ("exhaustive", "graph_exhausted"))

    def test_retention_limits_are_visible(self):
        capped, _ = search(GraphModel(DIAMOND), retention=GraphRetention(max_retained_edges=3))
        self.assertEqual((capped.termination, len(capped.edges), capped.edges_retained),
                         ("graph_retention_limit", 3, False))
        self.assertEqual(str(capped.coverage), "incomplete:graph_retention_limit")
        streamed, events = search(GraphModel(DIAMOND), retention=GraphRetention(retain_edges=False))
        self.assertEqual((streamed.edges, streamed.edges_retained, str(streamed.coverage)), ((), False, "exhaustive"))
        self.assertEqual(streamed.graph_edges, 5)
        self.assertEqual(sum(e.startswith("edge:") for e in events), 5)
        tiny, _ = search(GraphModel(DIAMOND), retention=GraphRetention(max_graph_bytes=1))
        self.assertEqual((tiny.termination, tiny.states), ("graph_retention_limit", 0))
        with self.assertRaises(ValueError): GraphRetention(max_retained_edges=-1)
        with self.assertRaises(ValueError): FailurePolicy.collect(-1, True)
        with self.assertRaises(ValueError): GraphRetention(max_graph_bytes=1 << 64)

    def test_exact_u64_counters(self):
        big = (1 << 64) - 1
        r, _ = search(GraphModel(DIAMOND), SearchConfig(max_transitions=big))
        self.assertEqual(r.transitions, 5)
        self.assertIsInstance(r.estimated_graph_bytes, int)
        r = explore_graph(GraphModel(DIAMOND), options=GraphSearchOptions(retention=GraphRetention(max_graph_bytes=big)))
        self.assertEqual(r.termination, "graph_exhausted")


class FailureCollectionTests(unittest.TestCase):
    def test_first_and_subsequent_failures(self):
        r, _ = search(GraphModel(CHAIN, fail_state=[2, 3, 4]), policy=COLLECT)
        self.assertEqual([f.witness for f in r.findings], [(1, 2), (1, 2, 3), (1, 2, 3, 4)])
        self.assertEqual([f.requires_continued_evidence for f in r.findings], [False, True, True])
        self.assertEqual([f.index for f in r.findings], [0, 1, 2])
        self.assertEqual((r.finding_count, r.findings_truncated), (3, False))
        self.assertEqual(r.failed_properties, (PropertyFailureCount("state", "bad_state", 3, 0),))
        self.assertEqual(str(r.coverage), "exhaustive")
        self.assertEqual([n.path_failed for n in r.nodes], [False, False, True, True, True])
        self.assertEqual(r.failure.inputs, (1, 2))
        pruned, _ = search(GraphModel(CHAIN, fail_state=[2, 3, 4]), policy=PRUNE)
        self.assertEqual((pruned.finding_count, len(pruned.nodes)), (1, 2))
        self.assertEqual(pruned.edges[-1].target, EdgeTarget.rejected("failing_successor"))
        self.assertEqual((str(pruned.coverage), pruned.termination), ("bounded:failure_pruned", "graph_exhausted"))

    def test_multiple_phases_on_one_edge_are_ordered(self):
        r, events = search(GraphModel(CHAIN, fail_state=[1], fail_edge=[(0, 1)]), policy=COLLECT)
        self.assertEqual(len(r.findings), 1)
        self.assertEqual([f"{v.phase}/{v.check.id}" for v in r.findings[0].violations],
                         ["state/bad_state", "transition/bad_edge"])
        self.assertIn("finding:0:0:state/bad_state,transition/bad_edge", events)
        self.assertEqual([p.id for p in r.failed_properties], ["bad_state", "bad_edge"])
        self.assertEqual(r.edges[0].state_checks, (Check("ok"), Check("bad_state", "failed", "state 1")))
        self.assertEqual(r.edges[0].transition_checks, (Check("bad_edge", "failed", "0->1"),))

    def test_initial_failure_under_collection(self):
        expand, _ = search(GraphModel(CHAIN, fail_state=[0]), policy=COLLECT)
        self.assertEqual(([f.witness for f in expand.findings], expand.findings[0].edge, len(expand.nodes)), ([()], None, 5))
        self.assertEqual((expand.findings[0].violations[0].phase, str(expand.coverage)), ("initial_state", "exhaustive"))
        pruned, events = search(GraphModel(CHAIN, fail_state=[0]), policy=PRUNE)
        self.assertEqual(events, ["finding:0:-:initial_state/bad_state", "root:0", "finish:0:failurePruned",
                                  "layer:0", "end:graph_exhausted"])
        self.assertEqual(str(pruned.coverage), "bounded:failure_pruned")

    def test_failure_plus_cutoff(self):
        r, _ = search(GraphModel(CHAIN, fail_state=[2]), SearchConfig(max_states=3), policy=COLLECT)
        self.assertEqual((r.finding_count, r.termination, str(r.coverage)), (1, "state_limit", "incomplete:state_limit"))
        self.assertEqual(r.findings[0].witness, (1, 2))

    def test_checker_error_after_partial_evidence(self):
        class Exploding(GraphModel):
            def check_state(self, state):
                if state == 3: raise RuntimeError("checker exploded")
                return super().check_state(state)
        with self.assertRaises(GraphSearchError) as caught:
            explore_graph(Exploding(CHAIN, fail_state=[2]), options=GraphSearchOptions(failure_policy=COLLECT))
        error = caught.exception
        self.assertIsInstance(error, ModelError)
        self.assertIn("checker exploded", str(error))
        report = error.report
        self.assertEqual((report.termination, str(report.coverage)), ("model_error", "incomplete:model_error"))
        self.assertEqual([f.witness for f in report.findings], [(1, 2)])
        self.assertEqual((report.nodes[2].expansion, report.nodes[2].interrupted_reason), ("interrupted", "model_error"))
        # The failing batch never completed, so no record exists for its edge.
        self.assertEqual((len(report.edges), report.edges[-1].target, report.rejected_successors),
                         (2, EdgeTarget.admitted(2), 0))

    def test_deterministic_finding_truncation(self):
        policy = FailurePolicy.collect(1, True)
        a, events_a = search(GraphModel(CHAIN, fail_state=[2, 3, 4]), policy=policy)
        b, events_b = search(GraphModel(CHAIN, fail_state=[2, 3, 4]), policy=policy)
        self.assertEqual(([f.witness for f in a.findings], a.finding_count, a.findings_truncated), ([(1, 2)], 3, True))
        self.assertEqual((a.failed_properties[0].count, events_a), (3, events_b))
        self.assertEqual([f.witness for f in b.findings], [(1, 2)])
        none, _ = search(GraphModel(CHAIN, fail_state=[2]), policy=FailurePolicy.collect(0, True))
        self.assertEqual((none.findings, none.failure, none.finding_count), ((), None, 1))
        self.assertEqual([p.id for p in none.failed_properties], ["bad_state"])

    def test_annotations_and_detached_event_payloads(self):
        class Box:
            def __init__(self, n): self.n = n
            def __eq__(self, other): return self.n == other.n
            def __hash__(self): return self.n
        class Boxes:
            def initial_state(self): return Box(0)
            def inputs(self, state): return [Box(1), Box(2)] if state.n == 0 else []
            def step(self, state, input): return Transition(Box(input.n), (Box(input.n),))
            def check_state(self, state): return ()
        def sink(event):
            if event.kind == "edge_evaluated":
                event.edge.input.n = 99
                for output in event.edge.outputs: output.n = 99
        def annotate(before, input, transition):
            input.n = 77
            return [f"from {before.n} to {transition.state.n}"]
        r = explore_graph(Boxes(), options=GraphSearchOptions(events=sink, annotate_edge=annotate))
        self.assertEqual([e.input.n for e in r.edges], [1, 2])
        self.assertEqual([[o.n for o in e.outputs] for e in r.edges], [[1], [2]])
        self.assertEqual([e.annotations for e in r.edges], [("from 0 to 1",), ("from 0 to 2",)])
        self.assertEqual([n.state.n for n in r.nodes], [0, 1, 2])

    def test_sink_error_aborts_with_partial_report(self):
        def sink(event):
            if event.kind == "successor_admitted" and event.node == 2: raise RuntimeError("sink full")
        with self.assertRaises(GraphSearchError) as caught:
            explore_graph(GraphModel(DIAMOND), options=GraphSearchOptions(events=sink))
        self.assertIn("sink full", str(caught.exception))
        self.assertEqual((len(caught.exception.report.nodes), str(caught.exception.report.coverage)), (3, "incomplete:model_error"))


class GraphFixtureTests(unittest.TestCase):
    def test_graph_search_fixtures(self):
        cases = corpus("graph-search-v1.json")
        self.assertEqual(len(cases), 10)
        for case in cases:
            with self.subTest(case=case["case"]):
                model = GraphModel({int(k): v for k, v in case["edges"].items()}, case["hash"] == "constant",
                                   case["fail_state"], case["fail_edge"])
                config = SearchConfig(**case["config"])
                spec = case["policy"]
                policy = (FailurePolicy.collect(spec["max_retained_findings"], spec["expand_failing_successors"])
                          if spec["kind"] == "collect" else FailurePolicy.stop_at_first())
                r, events = search(model, config, policy=policy)
                x = case["expected"]
                self.assertEqual(events, x["events"])
                self.assertEqual(targets(r), x["targets"])
                self.assertEqual((r.termination, str(r.coverage), r.states, r.transitions),
                                 (x["termination"], x["coverage"], x["states"], x["transitions"]))
                self.assertEqual([list(f.witness) for f in r.findings], x["finding_witnesses"])
                self.assertEqual([f.requires_continued_evidence for f in r.findings], x["requires_continued_evidence"])
                self.assertEqual(r.finding_count, x["finding_count"])


if __name__ == "__main__":
    unittest.main()

"""Native BFS controls, estimates, callback boundaries and observation evidence."""
import unittest
from unittest.mock import patch

from statelessness import (Check, ModelError, RunLimits, SearchConfig, SearchLimits,
                          Transition, WithOracle, Paired, enumerate_states)

U64_MAX = (1 << 64) - 1


class Chain:
    def initial_state(self): return 0
    def inputs(self, state): return (1,) if state < 2 else ()
    def step(self, state, input): return Transition(state + input)
    def check_state(self, state): return ()
    def estimated_state_bytes(self, state): return 4


class BfsLimitsTests(unittest.TestCase):
    def test_default_and_exact_admission_cap(self):
        report = enumerate_states(Chain())
        self.assertEqual((report.termination, report.states, report.transitions,
                          report.estimated_retained_state_bytes), ("graph_exhausted", 3, 2, 12))
        exact = enumerate_states(Chain(), limits=SearchLimits(max_estimated_state_bytes=12))
        self.assertEqual(exact, report)
        limited = enumerate_states(Chain(), limits=SearchLimits(max_estimated_state_bytes=8))
        self.assertEqual((limited.termination, limited.states, limited.transitions,
                          limited.estimated_retained_state_bytes), ("estimated_state_byte_limit", 2, 2, 8))
        initial = enumerate_states(Chain(), limits=SearchLimits(max_estimated_state_bytes=3))
        self.assertEqual((initial.termination, initial.states, initial.transitions,
                          initial.estimated_retained_state_bytes), ("estimated_state_byte_limit", 0, 0, 0))

    def test_unknown_overflow_and_invalid_estimates(self):
        class Unknown(Chain):
            def estimated_state_bytes(self, state): return None if state == 1 else 1
        report = enumerate_states(Unknown())
        self.assertIsNone(report.estimated_retained_state_bytes)
        with self.assertRaisesRegex(ModelError, "requires estimated_state_bytes"):
            enumerate_states(Unknown(), limits=SearchLimits(max_estimated_state_bytes=10))
        class Overflow(Chain):
            def estimated_state_bytes(self, state): return U64_MAX if state == 0 else 1
        self.assertIsNone(enumerate_states(Overflow()).estimated_retained_state_bytes)
        report = enumerate_states(Overflow(), limits=SearchLimits(max_estimated_state_bytes=U64_MAX))
        self.assertEqual((report.termination, report.states, report.estimated_retained_state_bytes),
                         ("estimated_state_byte_limit", 1, U64_MAX))
        for invalid in (True, -1, 1 << 64, 1.5):
            class Invalid(Chain):
                def estimated_state_bytes(self, state): return invalid
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ModelError, "unsigned 64-bit"):
                enumerate_states(Invalid())

    def test_duplicate_not_estimated_and_state_cap_precedes_bytes(self):
        estimates = []
        class Duplicate(Chain):
            def inputs(self, state): return (0, 0)
            def step(self, state, input): return Transition(state)
            def estimated_state_bytes(self, state): estimates.append(state); return 1
        result = enumerate_states(Duplicate(), limits=SearchLimits(max_estimated_state_bytes=1))
        self.assertEqual((result.termination, result.transitions, estimates), ("graph_exhausted", 2, [0]))
        class Cap(Chain):
            def estimated_state_bytes(self, state):
                if state: raise AssertionError("state cap must precede estimation")
                return 0
        result = enumerate_states(Cap(), SearchConfig(max_states=1), limits=SearchLimits(max_estimated_state_bytes=0))
        self.assertEqual(result.termination, "state_limit")

    def test_prestart_control_and_no_unrequested_clock(self):
        class Uncalled(Chain):
            def initial_state(self): raise AssertionError("no callbacks")
        result = enumerate_states(Uncalled(), limits=SearchLimits(RunLimits(max_duration=0, cancellation=lambda: True)))
        self.assertEqual((result.termination, result.states, result.transitions), ("cancelled", 0, 0))
        self.assertIsNone(result.estimated_retained_state_bytes)
        result = enumerate_states(Uncalled(), limits=SearchLimits(RunLimits(max_duration=0)))
        self.assertEqual(result.termination, "deadline")
        with patch("statelessness.search.time.monotonic", side_effect=AssertionError("unrequested clock")):
            self.assertEqual(enumerate_states(Chain()).termination, "graph_exhausted")

    def test_failure_wins_over_callback_cancellation_and_does_not_estimate(self):
        cancelled = False
        class Failed(Chain):
            def check_state(self, state):
                nonlocal cancelled
                cancelled = True
                return (Check("bad", "failed", "x"),)
            def estimated_state_bytes(self, state): raise AssertionError("failure precedes estimate")
        result = enumerate_states(Failed(), limits=SearchLimits(RunLimits(cancellation=lambda: cancelled)))
        self.assertEqual((result.termination, result.failure.inputs), ("failure_found", ()))
        cancelled = False
        class FailedTurn(Chain):
            def step(self, state, input):
                nonlocal cancelled
                cancelled = True
                return super().step(state, input)
            def check_state(self, state): return (Check("bad", "failed", "x"),) if state else ()
        result = enumerate_states(FailedTurn(), limits=SearchLimits(RunLimits(cancellation=lambda: cancelled)))
        self.assertEqual((result.termination, result.transitions, result.failure.inputs), ("failure_found", 1, (1,)))

    def test_callback_triggered_stops_and_error_precedence(self):
        for stage in ("inputs", "iterator", "next", "estimate"):
            cancelled = False
            class Cancel(Chain):
                def inputs(self, state):
                    nonlocal cancelled
                    if stage == "inputs": cancelled = True
                    class Cursor:
                        def __iter__(self):
                            nonlocal cancelled
                            if stage == "iterator": cancelled = True
                            return self
                        def __next__(self):
                            nonlocal cancelled
                            if stage == "next": cancelled = True
                            return 1
                    return Cursor()
                def estimated_state_bytes(self, state):
                    nonlocal cancelled
                    if stage == "estimate": cancelled = True
                    return 4
                def step(self, state, input): raise AssertionError("stop before step")
            result = enumerate_states(Cancel(), limits=SearchLimits(RunLimits(cancellation=lambda: cancelled)))
            self.assertEqual((result.termination, result.transitions), ("cancelled", 0))
        cancelled = False
        class Broken(Chain):
            def estimated_state_bytes(self, state):
                nonlocal cancelled
                cancelled = True
                raise RuntimeError("estimate error")
        with self.assertRaisesRegex(ModelError, "estimate error"):
            enumerate_states(Broken(), limits=SearchLimits(RunLimits(cancellation=lambda: cancelled)))

    def test_depth_and_transition_boundary_single_probe(self):
        pulls = []
        class Probe(Chain):
            def inputs(self, state):
                def domain():
                    pulls.append(state)
                    if state == 0: yield 1
                return domain()
        exact = enumerate_states(Probe(), SearchConfig(max_transitions=1))
        self.assertEqual((exact.termination, pulls), ("graph_exhausted", [0, 1]))
        pulls.clear()
        depth = enumerate_states(Probe(), SearchConfig(max_depth=0))
        self.assertEqual((depth.termination, depth.transitions, pulls), ("depth_bound", 0, [0]))


class BfsObserverTests(unittest.TestCase):
    def test_actual_bfs_histories_duplicate_and_failing_edges(self):
        histories = []
        initial = []
        class Branch(Chain):
            def inputs(self, state): return (1, 2, 1) if state == 0 else ((3,) if state == 1 else ())
            def step(self, state, input): return Transition(input)
            def check_state(self, state): return (Check("bad", "failed", "x"),) if state == 3 else ()
        class Observer:
            def needs_history(self): return True
            def initial(self, state, checks): initial.append(state)
            def transition(self, before, input, transition, state_checks, edge_checks, history): histories.append(history)
        result = enumerate_states(Branch(), observer=Observer())
        self.assertEqual(initial, [0])
        self.assertEqual(histories, [(1,), (2,), (1,), (1, 3)])
        self.assertEqual(result.failure.inputs, (1, 3))
        self.assertEqual(result.states, 3)

    def test_detached_snapshots_and_default_no_history(self):
        class Mutable:
            def initial_state(self): return []
            def inputs(self, state): return ([1],) if not state else ()
            def step(self, state, input): return Transition(state + input)
            def check_state(self, state): return ()
        seen = []
        class Observer:
            def initial(self, state, checks): state.append(9)
            def transition(self, before, input, transition, state_checks, edge_checks, history):
                seen.append(history)
                before.append(9); input.append(9); transition.state.append(9)
        result = enumerate_states(Mutable(), observer=Observer())
        self.assertEqual((result.termination, result.states, seen), ("graph_exhausted", 2, [()]))

    def test_partial_batches_and_secondary_observer_errors(self):
        errors = []
        class Broken(Chain):
            def check_state(self, state):
                yield Check("state")
            def check_transition(self, before, input, transition):
                yield Check("partial")
                raise RuntimeError("edge checker")
        class Observer:
            def check_error(self, phase, checks, complete):
                errors.append((phase, checks, complete))
                raise RuntimeError("secondary")
        with self.assertRaisesRegex(ModelError, "edge checker") as caught:
            enumerate_states(Broken(), observer=Observer())
        self.assertEqual(errors, [("state", (Check("state"),), True),
                                  ("transition", (Check("partial"),), False)])
        self.assertEqual(len(caught.exception.observer_errors), 2)


class EstimateWrapperTests(unittest.TestCase):
    def test_explicit_wrapper_overhead_unknown_components_and_overflow(self):
        class History:
            def estimated_state_bytes(self, history): return 7
        actual = Chain()
        unknown = WithOracle(actual, History())
        state = unknown.attach(0, [])
        self.assertIsNone(unknown.estimated_state_bytes(state))
        self.assertEqual(WithOracle(actual, History(), estimated_wrapper_overhead=0).estimated_state_bytes(state), 11)
        self.assertEqual(WithOracle(actual, History(), estimated_wrapper_overhead=3).estimated_state_bytes(state), 14)
        self.assertIsNone(WithOracle(actual, History(), estimated_wrapper_overhead=U64_MAX).estimated_state_bytes(state))
        class Missing: pass
        self.assertIsNone(WithOracle(actual, Missing(), estimated_wrapper_overhead=0).estimated_state_bytes(state))
        for invalid in (True, -1, 1 << 64):
            with self.assertRaises(ValueError): WithOracle(actual, History(), estimated_wrapper_overhead=invalid)

    def test_paired_forwards_reference_estimate(self):
        paired = Paired(Chain(), object())
        self.assertEqual(paired.estimated_state_bytes(2), 4)
        self.assertIsNone(Paired(object(), object()).estimated_state_bytes(2))




class InheritedObserverTests(unittest.TestCase):
    def test_protocol_subclass_inherits_false_history_default(self):
        from statelessness import SearchObserver
        histories = []
        class Observer(SearchObserver):
            def transition(self, before, input, transition, state_checks, edge_checks, history):
                histories.append(history)
        result = enumerate_states(Chain(), observer=Observer())
        self.assertEqual(result.termination, "graph_exhausted")
        self.assertEqual(histories, [(), ()])




class BfsCallbackBoundaryTests(unittest.TestCase):
    def test_hash_and_newest_collision_equality_stop_boundaries(self):
        for stop_stage in ("none", "initial_hash", "successor_hash", "equality"):
            cancelled = False
            comparisons = []
            class State:
                def __init__(self, value): self.value = value
                def __hash__(self):
                    nonlocal cancelled
                    if (stop_stage == "initial_hash" and self.value == 0 or
                            stop_stage == "successor_hash" and self.value == 1): cancelled = True
                    return 0
                def __eq__(self, other):
                    nonlocal cancelled
                    comparisons.append((self.value, other.value))
                    if stop_stage == "equality" and (self.value, other.value) == (1, 2): cancelled = True
                    return self.value == other.value
            class Collision(Chain):
                def initial_state(self): return State(0)
                def inputs(self, state): return (1, 2) if state.value == 0 else ()
                def step(self, state, input): return Transition(State(input))
            result = enumerate_states(Collision(), limits=SearchLimits(RunLimits(cancellation=lambda: cancelled)))
            if stop_stage == "none":
                self.assertEqual(result.termination, "graph_exhausted")
                self.assertEqual(comparisons, [(0, 1), (1, 2), (0, 2)])
            else:
                self.assertEqual(result.termination, "cancelled")
                self.assertEqual((result.states, result.transitions),
                    {"initial_hash": (1, 0), "successor_hash": (1, 1), "equality": (2, 2)}[stop_stage])

    def test_deadline_during_step_finishes_checks_before_stop(self):
        now = 0.0
        checks = []
        class Deadline(Chain):
            def step(self, state, input):
                nonlocal now
                now = 2.0
                return super().step(state, input)
            def check_state(self, state): checks.append(("state", state)); return ()
            def check_transition(self, before, input, transition): checks.append(("edge", transition.state)); return ()
        with patch("statelessness.search.time.monotonic", side_effect=lambda: now):
            result = enumerate_states(Deadline(), limits=SearchLimits(RunLimits(max_duration=1)))
        self.assertEqual((result.termination, result.transitions), ("deadline", 1))
        self.assertEqual(checks, [("state", 0), ("state", 1), ("edge", 1)])

    def test_partial_initial_and_successor_state_errors(self):
        for failing_state in (0, 1):
            errors = []
            class Broken(Chain):
                def check_state(self, state):
                    yield Check("emitted")
                    if state == failing_state: raise RuntimeError("state checker")
            class Observer:
                def check_error(self, phase, checks, complete): errors.append((phase, checks, complete))
            with self.assertRaisesRegex(ModelError, "state checker"):
                enumerate_states(Broken(), observer=Observer())
            self.assertEqual(errors, [("initial_state" if failing_state == 0 else "state", (Check("emitted"),), False)])




class SharedBfsControlsTests(unittest.TestCase):
    def test_independent_callback_report_vectors(self):
        import json
        from pathlib import Path
        corpus = json.loads((Path(__file__).resolve().parents[2] / "conformance/bfs-controls-v1.json").read_text(encoding="utf-8"))
        for case in corpus["cases"]:
            with self.subTest(case=case["case"]):
                events = []
                cancelled = case["entry_control"] in ("cancelled", "both")
                def log(event):
                    nonlocal cancelled
                    events.append(event)
                    if event == case["cancel_at"]: cancelled = True
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
                def run():
                    config = SearchConfig(case["max_states"], case["max_transitions"], case["max_depth"])
                    cap = case["max_estimated_state_bytes"]
                    control = RunLimits(max_duration=0 if case["entry_control"] in ("deadline", "both") else None,
                                        cancellation=lambda: cancelled)
                    return enumerate_states(Fixture(), config,
                        limits=SearchLimits(control, None if cap is None else int(cap)), observer=Observer())
                if case["error_contains"] is not None:
                    with self.assertRaisesRegex((ModelError, ValueError), case["error_contains"]): run()
                else:
                    result = run()
                    actual = {name: getattr(result, name) for name in
                              ("termination", "states", "transitions", "max_depth_reached", "skipped_checks")}
                    actual["estimated_retained_state_bytes"] = (None if result.estimated_retained_state_bytes is None else
                                                                 str(result.estimated_retained_state_bytes))
                    actual["witness"] = None if result.failure is None else list(result.failure.inputs)
                    actual["failed_phase"] = None if result.failure is None else result.failure.violations[0].phase
                    actual["failed_id"] = None if result.failure is None else result.failure.violations[0].check.id
                    self.assertEqual(actual, case["expected"])
                self.assertEqual(events, case["events"])




class SkippedCounterTests(unittest.TestCase):
    def test_unsigned64_saturating_counter_near_maximum(self):
        from statelessness.core import _saturating_add_u64
        self.assertEqual(_saturating_add_u64(0, 0), 0)
        self.assertEqual(_saturating_add_u64(U64_MAX - 2, 1), U64_MAX - 1)
        self.assertEqual(_saturating_add_u64(U64_MAX - 1, 1), U64_MAX)
        self.assertEqual(_saturating_add_u64(U64_MAX - 1, 2), U64_MAX)
        self.assertEqual(_saturating_add_u64(U64_MAX, 1), U64_MAX)
        self.assertEqual(_saturating_add_u64(U64_MAX, U64_MAX), U64_MAX)


if __name__ == "__main__": unittest.main()

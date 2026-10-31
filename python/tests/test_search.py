"""Independent native generation/search regression tests."""
import unittest
from statelessness import (EXHAUSTED, Auto, AutoOptions, Check, Failure, FuzzConfig, Generated,
    ModelError, Rng, RunLimits, ShrinkConfig, ShrinkLimits, Transition, Violation,
    fuzz, shrink)


class Counter:
    def initial_state(self): return 0
    def inputs(self, state): return (1, 2, 3)
    def step(self, state, input): return Transition(state + input)
    def check_state(self, state):
        return (Check("bound", "failed", "large"),) if state >= 5 else ()
    def generate(self, state, rng): return 1 + rng.index(3)
    def is_enabled(self, state, input): return input > 0
    def simpler_inputs(self, input): return range(1, input)


class SearchTests(unittest.TestCase):
    def test_initial_observer_sees_precheck_state(self):
        from statelessness.search import _violations
        original = [1]
        seen = []
        class Model:
            def check_state(self, state):
                original[0] = 99
                return ()
        class Observer:
            def initial(self, state, checks): seen.append(state)
        _violations(Model(), original, observer=Observer())
        self.assertEqual(seen, [[1]])

    def test_initial_observer_descriptor_cannot_change_snapshot(self):
        from statelessness.search import _violations
        original = [1]
        seen = []
        class Model:
            def check_state(self, state): seen.append(("check", state)); return ()
        class Observer:
            @property
            def initial(self):
                original[0] = 99
                return lambda state, checks: seen.append(("observer", state))
        _violations(Model(), original, observer=Observer())
        self.assertEqual(seen, [("check", [1]), ("observer", [1])])

    def test_captured_defers_copy_errors_but_not_base_exceptions(self):
        from statelessness.core import _Captured
        class Broken:
            def __deepcopy__(self, memo): raise RuntimeError("copy hook")
        class Fatal(BaseException): pass
        class Interrupting:
            def __deepcopy__(self, memo): raise Fatal()
        value = [1]
        captured = _Captured(value)
        value.append(2)
        self.assertEqual((captured.get(), captured.error), ([1], None))
        failed = _Captured(Broken())
        self.assertIsInstance(failed.error, ModelError)
        with self.assertRaisesRegex(ModelError, "snapshot: copy hook"): failed.get()
        with self.assertRaises(Fatal): _Captured(Interrupting())

    def test_history_is_copied_only_for_transition_observers(self):
        copies = []
        class Input:
            def __deepcopy__(self, memo):
                copies.append(1)
                return Input()
        class Model:
            def initial_state(self): return 0
            def generate(self, state, rng): return Input()
            def step(self, state, input): return Transition(state + 1)
            def check_state(self, state): return ()
        class InitialOnly:
            def initial(self, state, checks): pass
        config = FuzzConfig(cases=1, max_steps=20)
        fuzz(Model(), config)
        plain = len(copies)
        copies.clear()
        fuzz(Model(), config, observer=InitialOnly())
        self.assertEqual(len(copies), plain)
        histories = []
        class Recording:
            def transition(self, before, input, after, state_checks, transition_checks, history):
                histories.append(len(history))
        copies.clear()
        fuzz(Model(), config, observer=Recording())
        self.assertEqual(histories, list(range(1, 21)))
        self.assertGreater(len(copies), plain)

    def test_transition_observer_lookup_failure_is_a_model_error(self):
        class Model:
            def initial_state(self): return 0
            def generate(self, state, rng): return 1
            def step(self, state, input): return Transition(state + 1)
            def check_state(self, state): return ()
        class Broken:
            @property
            def transition(self): raise RuntimeError("lookup failed")
        with self.assertRaisesRegex(ModelError, "search observer transition: .*lookup failed"):
            fuzz(Model(), FuzzConfig(cases=1, max_steps=1), observer=Broken())

    def test_initial_check_snapshot_survives_observer_snapshot_failure(self):
        from statelessness.search import _violations
        calls, seen = [], []
        class State:
            def __init__(self, value): self.value = value
            def __deepcopy__(self, memo):
                calls.append(1)
                if len(calls) == 2: raise RuntimeError("observer snapshot")
                return State(self.value)
        original = State(1)
        class Model:
            def check_state(self, state):
                seen.append(state.value)
                raise RuntimeError("primary check")
        class Observer:
            @property
            def initial(self):
                original.value = 99
                return lambda state, checks: None
        with self.assertRaisesRegex(ModelError, "primary check"):
            _violations(Model(), original, observer=Observer())
        self.assertEqual(seen, [1])

    def test_initial_check_error_precedes_observer_getter_error(self):
        from statelessness.search import _violations
        class Model:
            def check_state(self, state): raise RuntimeError("primary check")
        class Observer:
            @property
            def initial(self): raise RuntimeError("observer getter")
        with self.assertRaisesRegex(ModelError, "primary check"):
            _violations(Model(), 1, observer=Observer())

    def test_enumeration_edge_precedes_history_query(self):
        from statelessness import SearchConfig, enumerate_states
        import inspect
        seen = []
        class Model:
            def initial_state(self): return 0
            def inputs(self, state): return (1,) if state == 0 else ()
            def step(self, state, value): return Transition([2], ([3],))
            def check_state(self, state): return ()
            def check_transition(self, before, value, transition):
                seen.append((transition.state, transition.outputs))
                return ()
        class Observer:
            def needs_history(self):
                frame = inspect.currentframe().f_back
                while frame is not None and frame.f_code.co_name != "enumerate_states":
                    frame = frame.f_back
                frame.f_locals["transition"].state[0] = 99
                frame.f_locals["transition"].outputs[0][0] = 99
                return False
        enumerate_states(Model(), SearchConfig(max_depth=1), observer=Observer())
        self.assertEqual(seen, [([2], ([3],))])

    def test_enumeration_falls_back_to_captured_none_successor(self):
        from statelessness import SearchConfig, enumerate_states
        from statelessness import core
        from unittest.mock import patch
        import inspect
        real_copy = core._copy
        failed = []
        seen = []
        def copy_once(value, memo=None):
            if value is None and not failed and inspect.currentframe().f_back.f_code.co_name == "enumerate_states":
                failed.append(True)
                raise core._SnapshotFailure("successor copy")
            return real_copy(value, memo)
        class Model:
            def initial_state(self): return 0
            def inputs(self, state): return (1,) if state == 0 else ()
            def step(self, state, value): return Transition(None)
            def check_state(self, state): seen.append(state); return ()
        class Observer:
            def needs_history(self):
                frame = inspect.currentframe().f_back
                while frame is not None and frame.f_code.co_name != "enumerate_states":
                    frame = frame.f_back
                object.__setattr__(frame.f_locals["transition"], "state", 99)
                return False
        # The saved edge supplies the check, while the failed node copy remains
        # a search error after that check has run.
        with patch.object(core, "_copy", new=copy_once):
            with self.assertRaisesRegex(ModelError, "successor copy"):
                enumerate_states(Model(), SearchConfig(max_depth=1), observer=Observer())
        self.assertEqual(seen, [0, None])

    def test_initial_violation_precedes_retained_node_copy_failure(self):
        from statelessness import SearchConfig, enumerate_states
        calls = []
        class State:
            def __deepcopy__(self, memo):
                calls.append(1)
                if len(calls) == 2:
                    raise RuntimeError("retained node")
                return State()
        class Model:
            def initial_state(self): return State()
            def check_state(self, state): return (Check("failure", "failed", "found"),)
            def inputs(self, state): return ()
            def step(self, state, value): return Transition(state)
        report = enumerate_states(Model(), SearchConfig(max_depth=1))
        self.assertEqual(report.termination, "failure_found")
        self.assertEqual(report.failure.violations[0].check.id, "failure")

    def test_enumeration_retains_nodes_before_check_alias_mutation(self):
        from statelessness import SearchConfig, enumerate_states
        import inspect
        seen = []
        class Model:
            def initial_state(self): return [0]
            def inputs(self, state):
                seen.append(state[0])
                return (1,) if state[0] == 0 else ()
            def step(self, state, value): return Transition([1])
            def check_state(self, state):
                frame = inspect.currentframe().f_back
                while frame is not None and frame.f_code.co_name != "enumerate_states":
                    frame = frame.f_back
                if frame is not None:
                    if "transition" in frame.f_locals:
                        frame.f_locals["transition"].state[0] = 99
                    else:
                        frame.f_locals["state"][0] = 99
                return ()
        report = enumerate_states(Model(), SearchConfig(max_depth=2))
        self.assertEqual(report.termination, "graph_exhausted")
        self.assertEqual(seen, [0, 1])

    def test_shrink_replay_retains_candidate_before_enabled_callback(self):
        import inspect
        seen = []
        class Input:
            def __init__(self, value): self.value = value
            def __deepcopy__(self, memo):
                return Input(self.value)
            def __eq__(self, other): return isinstance(other, Input) and self.value == other.value
        class Model:
            def initial_state(self): return 0
            def is_enabled(self, state, value):
                # Simulate an external alias into the candidate under replay.
                frame = inspect.currentframe().f_back
                while frame.f_code.co_name != "replay_candidate": frame = frame.f_back
                if state == 0:
                    frame.f_locals["inputs"][0].value = 99
                    frame.f_locals["inputs"][1].value = 99
                return True
            def step(self, state, value): return Transition(state + 1)
            def check_state(self, state): return ()
            def check_transition(self, state, value, transition):
                seen.append(value.value)
                return (Check("failure", "failed", "observed"),) if transition.state == 2 else ()
        original = Failure((Input(7), Input(8)), (Violation("transition", Check("failure", "failed", "observed")),))
        result = shrink(Model(), original, ShrinkConfig(max_attempts=1))
        self.assertEqual(seen, [7, 8])
        self.assertEqual([value.value for value in result.minimized.inputs], [7, 8])

    def test_enumeration_extra_input_copy_failure_precedes_reducer(self):
        from statelessness import SearchConfig, enumerate_states
        calls = []
        class Input:
            def __deepcopy__(self, memo):
                calls.append("copy")
                if len(calls) == 2: raise RuntimeError("second copy")
                return self
        class Model:
            def initial_state(self): return 0
            def inputs(self, state): return (Input(),)
            def step(self, state, value):
                calls.append("step")
                return Transition(1)
            def check_state(self, state): return ()
        with self.assertRaisesRegex(ModelError, "second copy"):
            enumerate_states(Model(), SearchConfig(max_depth=1))
        self.assertEqual(calls, ["copy", "copy"])

    def test_enumeration_retains_input_before_reducer_alias_mutation(self):
        from statelessness import SearchConfig, enumerate_states
        query = [7]
        observed = []
        class Model:
            def initial_state(self): return 0
            def inputs(self, state): return (query,) if state == 0 else ()
            def step(self, state, value):
                query[0] = 99
                return Transition(1)
            def check_state(self, state): return ()
            def check_transition(self, state, value, transition):
                observed.append(value)
                return (Check("original", "failed", "found"),)
        report = enumerate_states(Model(), SearchConfig(max_depth=1))
        self.assertEqual(observed, [[7]])
        self.assertEqual(report.failure.inputs, ([7],))

    def test_edge_observations_precede_state_check_alias_mutation(self):
        from statelessness.search import _violations
        before, query, after, output, history = [1], [2], [3], [4], [[5]]
        seen = []
        class Model:
            def check_state(self, state):
                before[0] = query[0] = after[0] = output[0] = history[0][0] = 99
                return ()
            def check_transition(self, state, value, transition):
                seen.append((state, value, transition.state, transition.outputs))
                return ()
        class Observer:
            def transition(self, state, value, transition, state_checks, transition_checks, inputs):
                seen.append((state, value, transition.state, transition.outputs, inputs))
        _violations(Model(), before, query, Transition(after, (output,)),
                    observer=Observer(), history=history)
        self.assertEqual(seen, [([1], [2], [3], ([4],)),
                                ([1], [2], [3], ([4],), ([5],))])

    def test_edge_history_copy_failure_does_not_preempt_transition_check(self):
        from statelessness.search import _violations
        class Uncopyable:
            def __deepcopy__(self, memo): raise RuntimeError("history copy")
        class Model:
            def check_state(self, state): return ()
            def check_transition(self, state, value, transition):
                raise RuntimeError("transition failure")
        class Observer:
            def transition(self, *args): pass
        with self.assertRaisesRegex(ModelError, "transition failure"):
            _violations(Model(), 0, 1, Transition(2), observer=Observer(),
                        history=(Uncopyable(),))

    def test_unused_edge_copy_failure_and_observer_only_failure_stage(self):
        from statelessness.search import _violations
        class Uncopyable:
            def __deepcopy__(self, memo): raise RuntimeError("edge copy")
        class Model:
            def check_state(self, state): return ()
        class Observer:
            def __init__(self): self.errors = []
            def check_error(self, *args): self.errors.append(args)
            def transition(self, *args): self.fail = True
        self.assertEqual(_violations(Model(), Uncopyable(), 1, Transition(2)), ((), 0))
        observer = Observer()
        with self.assertRaisesRegex(ModelError, "edge copy"):
            _violations(Model(), Uncopyable(), 1, Transition(2), observer=observer)
        self.assertEqual(observer.errors, [])

    def test_edge_observer_transition_descriptor_is_resolved_once(self):
        from statelessness.search import _violations
        class Model:
            def check_state(self, state): return ()
        class Observer:
            def __init__(self): self.lookups, self.calls = 0, 0
            @property
            def transition(self):
                self.lookups += 1
                return self.receive if self.lookups == 1 else None
            def receive(self, *args): self.calls += 1
        observer = Observer()
        _violations(Model(), 0, 1, Transition(2), observer=observer)
        self.assertEqual((observer.lookups, observer.calls), (1, 1))

    def test_auto_membership_captures_query_before_domain_callbacks(self):
        query = [1]
        class Mutating(Counter):
            def inputs(self, state):
                query[0] = 2
                return ([1],)
        self.assertTrue(Auto(Mutating()).is_enabled(0, query))

    def test_auto_membership_defers_query_copy_failure_until_needed(self):
        class Uncopyable:
            def __deepcopy__(self, memo): raise RuntimeError('query copy')
        class Empty(Counter):
            def inputs(self, state): return ()
        class Broken(Counter):
            def inputs(self, state): raise RuntimeError('domain error')
        self.assertFalse(Auto(Empty()).is_enabled(0, Uncopyable()))
        with self.assertRaisesRegex(ModelError, 'domain error'):
            Auto(Broken()).is_enabled(0, Uncopyable())
        with self.assertRaisesRegex(ModelError, 'query copy'):
            Auto(Counter()).is_enabled(0, Uncopyable())

    def test_generated_enabled_captures_input_before_state_copy(self):
        query = [1]
        class MutatingState:
            def __deepcopy__(self, memo):
                query[0] = 2
                return self
        generated = Generated(Counter(), lambda state, rng: 0,
                              is_enabled=lambda state, value: value == [1])
        self.assertTrue(generated.is_enabled(MutatingState(), query))

    def test_generated_enabled_pins_callback_before_input_copy(self):
        seen = []
        generated = Generated(Counter(), lambda state, rng: 0,
                              is_enabled=lambda state, value: seen.append(value) or True)
        class Input:
            def __deepcopy__(self, memo):
                generated._enabled = lambda state, value: False
                return 'captured'
        self.assertTrue(generated.is_enabled(0, Input()))
        self.assertEqual(seen, ['captured'])
        self.assertFalse(generated.is_enabled(0, 1))

        class Uncopyable:
            def __deepcopy__(self, memo): raise AssertionError('unexpected copy')
        generated._enabled = None
        self.assertTrue(generated.is_enabled(Uncopyable(), Uncopyable()))

    def test_auto_domain_limit_cannot_be_relaxed_during_iteration(self):
        class Mutating(Counter):
            def inputs(self, state):
                yield 1
                object.__setattr__(options, 'max_candidates', 100)
                yield 2
                yield 3
        options = AutoOptions(1)
        auto = Auto(Mutating(), options)
        rng = Rng(19)
        with self.assertRaisesRegex(ModelError, 'max_candidates'):
            auto.generate(0, rng)
        self.assertEqual(rng.state, 19)
        object.__setattr__(options, 'max_candidates', 1)
        with self.assertRaisesRegex(ModelError, 'max_candidates'):
            auto.is_enabled(0, 3)

    def test_auto_full_domain_rollback_and_first_entry(self):
        rng = Rng(17)
        model = Auto(Counter(), AutoOptions(2))
        with self.assertRaisesRegex(ModelError, "max_candidates"):
            model.generate(0, rng)
        self.assertEqual(rng.state, 17)
        self.assertTrue(model.is_enabled(0, 1))
        with self.assertRaisesRegex(ModelError, "max_candidates"):
            model.is_enabled(0, 4)
        class Single(Counter):
            def inputs(self, state): return [9]
        self.assertEqual(Auto(Single()).generate(0, rng), 9)
        self.assertEqual(rng.state, 17)

    def test_auto_iterator_error_rollback_empty_and_duplicate_weight(self):
        class Broken(Counter):
            def inputs(self, state):
                yield 1
                yield 2
                raise RuntimeError("iterator")
        rng = Rng(19)
        with self.assertRaisesRegex(ModelError, "iterator"):
            Auto(Broken()).generate(0, rng)
        self.assertEqual(rng.state, 19)
        class Empty(Counter):
            def inputs(self, state): return ()
        self.assertIs(Auto(Empty()).generate(0, rng), EXHAUSTED)
        self.assertEqual(rng.state, 19)
        # Independently pinned SplitMix64 outputs for seed 0: mod2=1, mod3=0.
        self.assertEqual(Auto(Counter()).generate(0, Rng(0)), 3)

    def test_fuzz_seed_failure_and_limits(self):
        report = fuzz(Counter(), FuzzConfig(seed=0, cases=4, max_steps=5))
        self.assertEqual(report.termination, "failure_found")
        self.assertEqual(report.failure.inputs, (2, 1, 2))
        self.assertEqual((report.cases, report.transitions), (1, 3))
        stopped = fuzz(Counter(), FuzzConfig(max_transitions=0))
        self.assertEqual((stopped.termination, stopped.cases), ("transition_limit", 1))
        self.assertEqual(fuzz(Counter(), limits=RunLimits(cancellation=lambda: True)).cases, 0)
        self.assertEqual(fuzz(Counter(), limits=RunLimits(max_duration=0)).termination, "deadline")

    def test_fuzz_owns_config_and_control_before_model_callbacks(self):
        config = FuzzConfig(seed=7, cases=1, max_steps=5, max_transitions=1)
        limits = RunLimits()
        class Mutating(Counter):
            def initial_state(self):
                object.__setattr__(config, 'max_transitions', 100)
                object.__setattr__(config, 'seed', 99)
                object.__setattr__(limits, 'cancellation', lambda: True)
                return 0
            def check_state(self, state): return ()
        report = fuzz(Mutating(), config, limits=limits)
        self.assertEqual((report.termination, report.seed, report.transitions), ('transition_limit', 7, 1))

    def test_generation_disabled_and_error_are_errors(self):
        disabled = Generated(Counter(), lambda state, rng: -1, is_enabled=lambda state, input: False)
        with self.assertRaisesRegex(ModelError, "disabled"):
            fuzz(disabled)
        def broken(state, rng): raise RuntimeError("generator")
        with self.assertRaisesRegex(ModelError, "generator"):
            fuzz(Generated(Counter(), broken))

    def test_mutation_and_empty_domain(self):
        class Passing(Counter):
            def check_state(self, state): return ()
            def generate(self, state, rng): return 1 + rng.index(3) if state < 10 else EXHAUSTED
        first = fuzz(Passing(), FuzzConfig(seed=42, cases=20, max_steps=7, mutation_percent=100))
        self.assertEqual(first, fuzz(Passing(), FuzzConfig(seed=42, cases=20, max_steps=7, mutation_percent=100)))
        self.assertEqual(first.termination, "cases_completed")
        empty = fuzz(Generated(Counter(), lambda state, rng: EXHAUSTED), FuzzConfig(cases=3))
        self.assertEqual((empty.cases, empty.transitions), (3, 0))

    def test_shrink_replay_target_and_chunk_then_simplify(self):
        original = Failure((3, 3, 3), (Violation("state", Check("bound", "failed", "original")),))
        result = shrink(Counter(), original)
        self.assertTrue(result.validated_original)
        self.assertEqual(result.minimized.inputs, (2, 3))
        self.assertEqual(result.termination, "search_complete")
        self.assertEqual(result.history[0].operation.kind, "validate_original")
        self.assertTrue(any(item.operation.kind == "simplify" for item in result.history))
        bad = Failure((3, 3), (Violation("transition", Check("bound", "failed", "wrong phase")),))
        with self.assertRaisesRegex(ModelError, "target"):
            shrink(Counter(), bad)

    def test_shrink_budget_original_validation_and_attempt(self):
        original = Failure((3, 3), (Violation("state", Check("bound", "failed", "large")),))
        limited = shrink(Counter(), original, limits=ShrinkLimits(max_replayed_transitions=1))
        self.assertFalse(limited.validated_original)
        self.assertEqual(limited.minimized, original)
        self.assertEqual(limited.replayed_transitions, 1)
        self.assertEqual(limited.history[0].outcome, "resource_limit")
        attempt = shrink(Counter(), original, ShrinkConfig(max_attempts=1))
        self.assertTrue(attempt.validated_original)
        self.assertEqual(attempt.termination, "attempt_limit")
        self.assertEqual(attempt.attempts, 1)

    def test_shrink_counts_repeated_simplifications_and_snapshots_budget(self):
        config = ShrinkConfig(max_attempts=5)
        class Repeating(Counter):
            def check_state(self, state):
                object.__setattr__(config, 'max_attempts', 100)
                return super().check_state(state)
            def simpler_inputs(self, input):
                while True:
                    yield input
        original = Failure((3, 3), (Violation('state', Check('bound', 'failed', 'large')),))
        report = shrink(Repeating(), original, config)
        self.assertEqual(report.termination, 'attempt_limit')
        self.assertLess(report.attempts, 5)
        self.assertFalse(any(item.outcome == 'already_seen' for item in report.history))
        class Finite(Counter):
            def simpler_inputs(self, input): return (input,) * 5
        one = Failure((5,), (Violation('state', Check('bound', 'failed', 'large')),))
        self.assertEqual(shrink(Finite(), one, ShrinkConfig(5)).termination, 'search_complete')

    def test_skipped_hint_bound_is_per_iterator_not_per_run(self):
        # Mirrors Rust tests/exploration.rs skipped_hint_bound_is_per_iterator_not_per_run.
        class SpreadDuplicateHints:
            def initial_state(self): return ()
            def step(self, state, input): return Transition(state + (input,))
            def check_state(self, state):
                return (Check("long", "failed", "long"),) if len(state) >= 6 else ()
            def generate(self, state, rng): return 1
            def simpler_inputs(self, input): return (0,) * 10 if input == 0 else (0,)
        failure = fuzz(SpreadDuplicateHints()).failure
        self.assertIsNotNone(failure)
        # Many hints are skipped in total, at most 10 per iterator, and fewer
        # than 150 candidates are replayed.
        report = shrink(SpreadDuplicateHints(), failure, ShrinkConfig(max_attempts=150))
        self.assertEqual(report.termination, "search_complete")
        self.assertLess(report.attempts, 150)
        self.assertTrue(all(input == 0 for input in report.minimized.inputs))

    def test_consecutive_skipped_hints_still_end_with_attempt_limit(self):
        class Duplicates(Counter):
            def simpler_inputs(self, input): return (input,) * 4
        one = Failure((5,), (Violation("state", Check("bound", "failed", "large")),))
        self.assertEqual(shrink(Duplicates(), one, ShrinkConfig(3)).termination, "attempt_limit")
        self.assertEqual(shrink(Duplicates(), one, ShrinkConfig(4)).termination, "search_complete")

    def test_shrink_closes_retained_simplifiers_and_preserves_primary_error(self):
        original = Failure((5,), (Violation("state", Check("bound", "failed", "large")),))
        held, closed = [], []
        class Simplifier(Counter):
            def simpler_inputs(self, value):
                def values():
                    try:
                        if value == 5:
                            yield 6
                    finally:
                        closed.append(value)
                result = values()
                held.append(result)
                return result
        report = shrink(Simplifier(), original)
        self.assertEqual(report.minimized.inputs, (6,))
        self.assertIn(5, closed)

        class BrokenIterator:
            def __iter__(self): return self
            def __next__(self): raise ValueError("primary simplifier error")
            def close(self): raise ValueError("secondary close error")
        class Broken(Counter):
            def simpler_inputs(self, value): return BrokenIterator()
        with self.assertRaisesRegex(ModelError, "primary simplifier error"):
            shrink(Broken(), original)

        class CleanupIterator:
            def __iter__(self): return self
            def __next__(self): return 6
            def close(self): raise ValueError("close failure")
        class Cleanup(Counter):
            def simpler_inputs(self, value): return CleanupIterator()
        with self.assertRaisesRegex(ModelError, "simpler_inputs close: close failure"):
            shrink(Cleanup(), original)

        class TrappedIterator(BrokenIterator):
            @property
            def close(self): raise ValueError("close lookup failure")
        class Trapped(Counter):
            def simpler_inputs(self, value): return TrappedIterator()
        with self.assertRaisesRegex(ModelError, "primary simplifier error"):
            shrink(Trapped(), original)

        cancel, domain_closed = [False], []
        class Unstarted:
            def __iter__(self): raise AssertionError("must not iterate after cancellation")
            def close(self): domain_closed.append(True)
        class Cancelled(Counter):
            def simpler_inputs(self, value):
                cancel[0] = True
                return Unstarted()
        report = shrink(Cancelled(), original, limits=ShrinkLimits(control=RunLimits(cancellation=lambda: cancel[0])))
        self.assertEqual(report.termination, "cancelled")
        self.assertEqual(domain_closed, [True])

        cancel_error, domain_closed = [False], []
        class CancelError(Counter):
            def simpler_inputs(self, value):
                cancel_error[0] = True
                return Unstarted()
        def check_cancel():
            if cancel_error[0]: raise ValueError("cancellation failure")
            return False
        with self.assertRaisesRegex(ModelError, "cancellation failure"):
            shrink(CancelError(), original, limits=ShrinkLimits(control=RunLimits(cancellation=check_cancel)))
        self.assertEqual(domain_closed, [True])

    def test_shrink_cyclic_simplification_and_causality(self):
        class Cyclic(Counter):
            def check_state(self, state): return (Check("bound", "failed", "x"),) if state else ()
            def simpler_inputs(self, input): return (1 if input == 2 else 2,)
        original = Failure((2,), (Violation("state", Check("bound", "failed", "x")),))
        result = shrink(Cyclic(), original)
        self.assertEqual(result.minimized.inputs, (1,))
        self.assertEqual(result.termination, "search_complete")
        self.assertLess(result.attempts, 8)
        class Disabled(Cyclic):
            def is_enabled(self, state, input): return False
        with self.assertRaisesRegex(ModelError, "causality"):
            shrink(Disabled(), original)

    def test_snapshots_and_error_atomicity(self):
        class Mutable:
            def initial_state(self): return []
            def generate(self, state, rng): state.append("corrupt"); return [1]
            def is_enabled(self, state, input): input.append(9); return True
            def step(self, state, input): state.extend(input); return Transition(state)
            def check_state(self, state):
                failed = bool(state)
                state.clear()
                return (Check("owned", "failed", "x"),) if failed else ()
        result = fuzz(Mutable(), FuzzConfig(cases=1))
        self.assertEqual(result.failure.inputs, ([1],))
        class Broken(Counter):
            def check_transition(self, before, input, transition): raise RuntimeError("edge")
        with self.assertRaisesRegex(ModelError, "edge"):
            fuzz(Broken())



class SharedSearchCorpusTests(unittest.TestCase):
    def test_hand_authored_search_corpus(self):
        import csv
        import json
        from pathlib import Path

        class Fixture:
            def __init__(self, name): self.name, self.traces = name, []
            def initial_state(self): self.traces.append([]); return 0
            def inputs(self, state):
                if self.name == "empty": return ()
                if self.name == "causal": return {0: (1,), 1: (2,)}.get(state, ())
                return (1, 2) if self.name in ("binary", "phase", "cycle") else (1,)
            def step(self, state, input):
                self.traces[-1].append(input)
                return Transition(input if self.name in ("phase", "cycle", "causal") else state + input)
            def check_state(self, state):
                failed = (self.name == "initial" or
                          self.name == "threshold" and state >= 3 or
                          self.name == "causal" and state == 2 or
                          self.name == "phase" and state == 1 or
                          self.name == "cycle" and state != 0)
                return (Check("same" if self.name == "phase" else "bad", "failed", "fixture"),) if failed else ()
            def check_transition(self, before, input, transition):
                return (Check("same", "failed", "edge"),) if self.name == "phase" and transition.state == 2 else ()
            def simpler_inputs(self, input):
                if self.name == "phase": return (2,) if input == 1 else ()
                if self.name == "cycle": return (1 if input == 2 else 2,)
                return ()

        path = Path(__file__).resolve().parents[2] / "conformance/search-v1.tsv"
        with path.open(encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        for row in rows:
            with self.subTest(case=row["case"]):
                fixture = Fixture(row["fixture"])
                if row["error"] == "invalid_candidate_budget":
                    with self.assertRaises(ValueError): AutoOptions(int(row["max_candidates"]))
                    continue
                auto = Auto(fixture, AutoOptions(100 if row["max_candidates"] == "-" else int(row["max_candidates"])))
                # Auto intentionally has no simplification hints; explicit generated
                # adapters compose finite sampling and application-owned hints.
                model = Generated(fixture, (lambda state, rng: rng.next_u64() % 10) if fixture.name == "digits" else auto.generate,
                                  is_enabled=(lambda state, input: True) if fixture.name == "digits" else auto.is_enabled,
                                  simpler_inputs=fixture.simpler_inputs)
                controls = (RunLimits(cancellation=lambda: True) if row["control"] == "cancelled" else
                            RunLimits(max_duration=0) if row["control"] == "deadline" else RunLimits())
                if row["operation"] == "fuzz":
                    if row["error"] == "auto_candidate_limit":
                        with self.assertRaisesRegex(ModelError, "max_candidates"):
                            fuzz(model, FuzzConfig(cases=1))
                        continue
                    result = fuzz(model, FuzzConfig(**{name: int(row[name]) for name in
                        ("seed", "cases", "max_steps", "max_transitions", "mutation_percent")}), limits=controls)
                    self.assertEqual(result.cases, int(row["started_cases"]))
                    if row["trace"] != "-": self.assertEqual(fixture.traces, json.loads(row["trace"]))
                    failure = result.failure
                    transitions = result.transitions
                else:
                    original = Failure(tuple(json.loads(row["original"])),
                        (Violation(row["target_phase"], Check(row["target_id"], "failed", "supplied")),))
                    if row["error"] != "-":
                        with self.assertRaises(ValueError): ShrinkConfig(int(row["max_attempts"]))
                        continue
                    result = shrink(model, original, ShrinkConfig(int(row["max_attempts"])),
                        limits=ShrinkLimits(controls, None if row["max_replayed_transitions"] == "-" else int(row["max_replayed_transitions"])))
                    from dataclasses import asdict
                    histories = json.loads(path.with_name("search-v1-history.json").read_text(encoding="utf-8"))
                    actual_history = []
                    for item in result.history:
                        record = asdict(item)
                        record["operation"] = {key: value for key, value in record["operation"].items() if value is not None}
                        actual_history.append(record)
                    self.assertEqual(actual_history, histories[row["case"]])
                    self.assertEqual(result.attempts, int(row["attempts"]))
                    self.assertEqual(result.validated_original, row["validated_original"] == "true")
                    failure = result.minimized
                    transitions = result.replayed_transitions
                self.assertEqual(result.termination, row["termination"])
                self.assertEqual(transitions, int(row["transitions"]))
                if row["witness"] != "-": self.assertEqual(list(failure.inputs), json.loads(row["witness"]))
                if row["failed_phase"] != "-":
                    self.assertEqual((failure.violations[0].phase, failure.violations[0].check.id),
                                     (row["failed_phase"], row["failed_id"]))

class CooperativeSearchTests(unittest.TestCase):
    def test_cancelled_generation_never_steps(self):
        cancelled = False
        class Cancel(Counter):
            def generate(self, state, rng):
                nonlocal cancelled
                cancelled = True
                return 1
            def step(self, state, input): raise AssertionError("must not execute")
        result = fuzz(Cancel(), limits=RunLimits(cancellation=lambda: cancelled))
        self.assertEqual((result.termination, result.transitions), ("cancelled", 0))

    def test_complete_failure_beats_simultaneous_cancellation(self):
        cancelled = False
        class Cancel(Counter):
            def check_state(self, state):
                nonlocal cancelled
                if state:
                    cancelled = True
                    return (Check("stop", "failed", "x"),)
                return ()
        result = fuzz(Cancel(), limits=RunLimits(cancellation=lambda: cancelled))
        self.assertEqual(result.termination, "failure_found")
        self.assertEqual(result.transitions, 1)

    def test_callback_error_beats_cancellation(self):
        cancelled = False
        class Cancel(Counter):
            def generate(self, state, rng):
                nonlocal cancelled
                cancelled = True
                raise RuntimeError("still error")
        with self.assertRaisesRegex(ModelError, "still error"):
            fuzz(Cancel(), limits=RunLimits(cancellation=lambda: cancelled))

    def test_shrink_limits_are_aggregate_not_per_candidate(self):
        original = Failure((1, 1, 1), (Violation("state", Check("bad", "failed", "x")),))
        class Threshold(Counter):
            def check_state(self, state): return (Check("bad", "failed", "x"),) if state >= 3 else ()
            def simpler_inputs(self, input): return ()
        result = shrink(Threshold(), original, limits=ShrinkLimits(max_replayed_transitions=4))
        self.assertTrue(result.validated_original)
        self.assertEqual((result.termination, result.replayed_transitions), ("transition_limit", 4))
        self.assertEqual(result.minimized.inputs, (1, 1, 1))
        self.assertEqual(result.history[-1].outcome, "resource_limit")


class NullableGenerationTests(unittest.TestCase):
    def test_generated_direct_callbacks_own_state_and_input(self):
        state=[1]; input=[2]; rng=Rng(5)
        def generate(snapshot,random):
            snapshot.append(3); random.next_u64(); return snapshot
        def enabled(snapshot,item):
            snapshot.append(4); item.append(5); return True
        def simpler(item):
            item.append(6); return (item,)
        generated=Generated(object(),generate,is_enabled=enabled,simpler_inputs=simpler)
        self.assertEqual(generated.generate(state,rng),[1,3])
        self.assertNotEqual(rng.state,5)
        self.assertTrue(generated.is_enabled(state,input))
        self.assertEqual(tuple(generated.simpler_inputs(input)),([2,6],))
        self.assertEqual((state,input),([1],[2]))
        bad=Generated(object(),generate,is_enabled=lambda state,input:1)
        with self.assertRaisesRegex(ModelError,'is_enabled must return bool'): bad.is_enabled(state,input)

    def test_none_is_present_and_exhaustion_is_explicit(self):
        class Nullable:
            def initial_state(self): return 0
            def inputs(self, state): return [None] if state == 0 else []
            def step(self, state, input):
                assert input is None
                return Transition(state + 1)
            def check_state(self, state): return ()
        auto = Auto(Nullable())
        self.assertIsNone(auto.generate(0, Rng(0)))
        self.assertIs(auto.generate(1, Rng(0)), EXHAUSTED)
        self.assertTrue(auto.is_enabled(0, None))
        report = fuzz(auto, FuzzConfig(cases=1))
        self.assertEqual((report.termination, report.transitions), ("cases_completed", 1))
        custom = Generated(Nullable(), lambda state, rng: None if state == 0 else EXHAUSTED)
        self.assertEqual(fuzz(custom, FuzzConfig(cases=1)).transitions, 1)



class SearchObserverTests(unittest.TestCase):
    def test_observer_sees_initial_failing_transition_and_owned_prefix(self):
        events = []
        class Observer:
            def initial(self, state, checks): events.append(("initial", state, checks))
            def transition(self, before, input, transition, state_checks, edge_checks, history):
                events.append(("edge", before, input, transition, state_checks, edge_checks, history))
        result = fuzz(Counter(), FuzzConfig(seed=0, cases=1), observer=Observer())
        self.assertEqual(events[0], ("initial", 0, ()))
        self.assertEqual(len(events), result.transitions + 1)
        self.assertEqual(events[-1][-1], result.failure.inputs)
        self.assertEqual(events[-1][4], (Check("bound", "failed", "large"),))

    def test_observer_mutation_does_not_change_search(self):
        class Mutable:
            def initial_state(self): return []
            def generate(self, state, rng): return [1]
            def step(self, state, input): return Transition(state + input)
            def check_state(self, state): return (Check("bad", "failed", "x"),) if state else ()
        class Observer:
            def initial(self, state, checks): state.append(9)
            def transition(self, before, input, transition, state_checks, edge_checks, history):
                before.append(9); input.append(9); transition.state.append(9); history[0].append(9)
        report = fuzz(Mutable(), FuzzConfig(cases=1), observer=Observer())
        self.assertEqual(report.failure.inputs, ([1],))
        self.assertEqual(report.transitions, 1)

    def test_partial_batches_and_completed_state_before_edge_error(self):
        errors = []
        class Observer:
            def check_error(self, phase, checks, complete): errors.append((phase, checks, complete))
        class Partial(Counter):
            def check_state(self, state):
                yield Check("partial")
                raise RuntimeError("state stream")
        with self.assertRaisesRegex(ModelError, "state stream"):
            fuzz(Partial(), observer=Observer())
        self.assertEqual(errors, [("initial_state", (Check("partial"),), False)])
        errors.clear()
        class Edge(Counter):
            def check_state(self, state): return (Check("state"),)
            def check_transition(self, before, input, transition):
                yield Check("edge")
                raise RuntimeError("edge stream")
        with self.assertRaisesRegex(ModelError, "edge stream"):
            fuzz(Edge(), observer=Observer())
        self.assertEqual(errors, [("state", (Check("state"),), True),
                                  ("transition", (Check("edge"),), False)])

    def test_observer_failure_is_explicit_error(self):
        class Observer:
            def initial(self, state, checks): raise RuntimeError("observer failed")
        with self.assertRaisesRegex(ModelError, "search observer initial: observer failed"):
            fuzz(Counter(), observer=Observer())




class MutationReuseTests(unittest.TestCase):
    def test_disabled_reuse_falls_back_to_state_aware_generation(self):
        class Digits:
            def initial_state(self): return 0
            def generate(self, state, rng): return rng.next_u64() % 10
            def is_enabled(self, state, input): return not (state == 1 and input == 9)
            def step(self, state, input): return Transition(state + 1)
            def check_state(self, state): return ()
        traces = []
        class Observer:
            def initial(self, state, checks): traces.append([])
            def transition(self, before, input, transition, state_checks, edge_checks, history):
                traces[-1].append(input)
        result = fuzz(Digits(), FuzzConfig(seed=0, cases=2, max_steps=3, mutation_percent=100), observer=Observer())
        # Known words: first case5,0,9. Delete middle0 then reuse9 at depth1
        # is disabled; word8 regenerates0, replacement word9 generates9.
        self.assertEqual(traces, [[5, 0, 9], [5, 0, 9]])
        self.assertEqual(result.termination, "cases_completed")




class ObserverSecondaryErrorTests(unittest.TestCase):
    def test_checker_error_remains_primary_when_error_observer_throws(self):
        class Broken(Counter):
            def check_state(self, state): raise RuntimeError("primary checker")
        class Observer:
            def check_error(self, phase, checks, complete): raise RuntimeError("secondary observer")
        with self.assertRaisesRegex(ModelError, "initial check_state: primary checker") as caught:
            fuzz(Broken(), observer=Observer())
        self.assertEqual(len(caught.exception.observer_errors), 1)
        self.assertIn("secondary observer", str(caught.exception.observer_errors[0]))




class OracleGenerationTests(unittest.TestCase):
    def test_enumerated_and_simpler_inputs_are_owned(self):
        from statelessness import WithOracle
        class Model:
            def __init__(self): self.values = [[1], [2]]
            def inputs(self, state):
                state.append(9)
                return iter(self.values)
            def simpler_inputs(self, value):
                value.append(9)
                return iter(self.values)
        class History: pass
        model = Model()
        wrapper = WithOracle(model, History())
        state = wrapper.attach([], [])
        inputs = list(wrapper.inputs(state))
        simpler = list(wrapper.simpler_inputs([3]))
        inputs[0].append(4)
        simpler[1].append(5)
        self.assertEqual(model.values, [[1], [2]])
        self.assertEqual(state.model, [])
        self.assertEqual(inputs[1], [2])
        self.assertEqual(simpler[0], [1])

        generated = Generated(model, lambda state, rng: None,
                              simpler_inputs=model.simpler_inputs)
        source = [3]
        result = list(generated.simpler_inputs(source))
        result[0].append(7)
        self.assertEqual(source, [3])
        self.assertEqual(model.values, [[1], [2]])

    def test_oracle_forwards_generation_and_shrink_preserving_history(self):
        from statelessness import Metadata, WithOracle, record, replay
        class Actual(Counter):
            def metadata(self): return Metadata("actual", build="search-test")
            def encode_state(self, state): return bytes([state])
            def decode_state(self, data): return data[0]
            def encode_input(self, input): return bytes([input])
            def decode_input(self, data): return data[0]
            def encode_output(self, output): return bytes([output])
        class History:
            def metadata(self): return Metadata("history", build="search-test")
            def initial_state(self): return []
            def advance(self, before, input, outputs, disposition): return before + [input]
            def check_state(self, history, actual):
                return (Check("sum") if sum(history) == actual else Check("sum", "failed", "different"),)
            def encode_history(self, history): return bytes(history)
            def decode_history(self, data): return list(data)
        wrapped = WithOracle(Actual(), History())
        result = fuzz(wrapped, FuzzConfig(seed=0, cases=1))
        self.assertEqual(result.failure.inputs, (2, 1, 2))
        minimized = shrink(wrapped, result.failure)
        self.assertTrue(minimized.validated_original)
        self.assertEqual(sum(minimized.minimized.inputs), 5)
        self.assertEqual(minimized.minimized.violations[0].check.id, "bound")
        trace = record(wrapped, minimized.minimized.inputs)
        self.assertEqual(trace.termination, "property_failed")
        self.assertTrue(replay(wrapped, trace).failure_reproduced)
        self.assertEqual(wrapped.decode_state(trace.steps[-1].post_state).oracle,
                         list(minimized.minimized.inputs))
        self.assertTrue(wrapped.is_enabled(wrapped.initial_state(), 1))
        self.assertFalse(wrapped.is_enabled(wrapped.initial_state(), -1))
        self.assertEqual(list(wrapped.simpler_inputs(3)), [1, 2])

    def test_absent_generation_is_explicit(self):
        from statelessness import WithOracle
        class Bare:
            def initial_state(self): return 0
            def check_state(self, state): return ()
        class History:
            def initial_state(self): return 0
            def check_state(self, history, actual): return ()
        with self.assertRaisesRegex(ModelError, "does not support generation"):
            fuzz(WithOracle(Bare(), History()), FuzzConfig(cases=1))




class OraclePartialObserverTests(unittest.TestCase):
    def test_oracle_composition_retains_model_checks_on_history_error(self):
        from statelessness import WithOracle
        class Actual(Counter):
            def check_state(self, state):
                yield Check("actual")
        class History:
            def initial_state(self): return 0
            def check_state(self, history, actual):
                yield Check("history")
                raise RuntimeError("oracle checker")
        errors = []
        class Observer:
            def check_error(self, phase, checks, complete): errors.append((phase, checks, complete))
        with self.assertRaisesRegex(ModelError, "oracle checker"):
            fuzz(WithOracle(Actual(), History()), observer=Observer())
        self.assertEqual(errors, [("initial_state", (Check("actual"), Check("history")), False)])


if __name__ == "__main__": unittest.main()

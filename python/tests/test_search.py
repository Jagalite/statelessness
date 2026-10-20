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
        with path.open() as stream:
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
                    histories = json.loads(path.with_name("search-v1-history.json").read_text())
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
        with self.assertRaisesRegex(ModelError, "initial check: primary checker") as caught:
            fuzz(Broken(), observer=Observer())
        self.assertEqual(len(caught.exception.observer_errors), 1)
        self.assertIn("secondary observer", str(caught.exception.observer_errors[0]))




class OracleGenerationTests(unittest.TestCase):
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

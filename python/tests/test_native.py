"""Native behavior tests: these do not use the portable table adapter."""
from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
import subprocess
import sys
import unittest

from statelessness import (Check, CheckPolicy, Disposition, Metadata, ModelError,
                          Rng, SearchConfig, TraceLimits, Transition,
                          check_observed, enumerate_states, record, replay)
from statelessness import trace_json


@dataclass(frozen=True)
class CounterState:
    value: int = 0


class Counter:
    def metadata(self):
        return Metadata("counter", build="native-test-v1")

    def initial_state(self):
        return CounterState()

    def inputs(self, state):
        return ("increment",)

    def step(self, state, input):
        if input != "increment":
            raise ValueError("unknown input")
        return Transition(CounterState(state.value + 1), ("changed",))

    def check_state(self, state):
        return (Check("bound", "passed") if state.value <= 2 else Check("bound", "failed", "above two"),)

    def encode_state(self, state):
        return str(state.value).encode("ascii")

    def decode_state(self, data):
        return CounterState(int(data.decode("ascii")))

    def encode_input(self, input):
        return input.encode("ascii")

    def decode_input(self, data):
        return data.decode("ascii")

    def encode_output(self, output):
        return output.encode("ascii")


class NativeTests(unittest.TestCase):
    def test_application_owned_dataclass_and_failure_witness(self):
        result = enumerate_states(Counter(), SearchConfig(10, 20, 10))
        self.assertEqual(result.termination, "failure_found")
        self.assertEqual(result.failure.inputs, ("increment",) * 3)
        self.assertEqual((result.states, result.transitions), (3, 3))
        self.assertEqual(result.failure.violations[0].phase, "state")

    def test_record_replay_and_json_roundtrip(self):
        trace = record(Counter(), ["increment"] * 5)
        self.assertEqual(len(trace.steps), 3)
        self.assertEqual(trace.termination, "property_failed")
        decoded = trace_json.loads(trace_json.dumps(trace))
        self.assertEqual(decoded, trace)
        self.assertTrue(replay(Counter(), decoded).failure_reproduced)

    def test_observation_does_not_call_step(self):
        class Observe(Counter):
            def step(self, *_):
                raise AssertionError("must never execute")
        checks = check_observed(Observe(), CounterState(), "increment", Transition(CounterState(1)), 1)
        self.assertEqual(checks, (Check("bound"),))

    def test_periodic_check_explicitly_skipped(self):
        checks = check_observed(Counter(), CounterState(), "increment", Transition(CounterState(3)), 1, CheckPolicy(2, False))
        self.assertEqual([c.status for c in checks], ["skipped", "skipped"])

    def test_check_error_does_not_return_partial_success(self):
        class Broken(Counter):
            def check_transition(self, *_):
                raise RuntimeError("broken checker")
        with self.assertRaisesRegex(ModelError, "transition check: broken checker"):
            check_observed(Broken(), CounterState(), "increment", Transition(CounterState(1)), 1)

    def test_iterator_error_classified(self):
        class Broken(Counter):
            def inputs(self, state):
                yield "increment"
                raise RuntimeError("domain failed")
        with self.assertRaisesRegex(ModelError, "enumerate inputs: domain failed"):
            enumerate_states(Broken())

    def test_keyboard_interrupt_is_not_swallowed(self):
        class Stop(Counter):
            def initial_state(self):
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            enumerate_states(Stop())

    def test_initial_callback_error_is_not_a_trace(self):
        class Broken(Counter):
            def initial_state(self):
                raise ValueError("initial failed")
        with self.assertRaises(ModelError):
            record(Broken(), [])

    def test_later_callback_error_retains_coherent_prefix(self):
        trace = record(Counter(), ["increment", "unknown"])
        self.assertEqual(trace.termination, "model_error")
        self.assertEqual(len(trace.steps), 1)
        self.assertEqual(replay(Counter(), trace).outcome, "exact")

    def test_bad_codec_type_is_a_model_error(self):
        class Broken(Counter):
            def encode_state(self, state):
                return "not bytes"
        with self.assertRaises(ModelError):
            record(Broken(), [])

    def test_noncanonical_initial_bytes_rejected(self):
        trace = replace(record(Counter(), []), initial_state=b"00")
        with self.assertRaisesRegex(ModelError, "not canonical"):
            replay(Counter(), trace)

    def test_noncanonical_input_bytes_rejected(self):
        class Lenient(Counter):
            def decode_input(self, data):
                return data.decode("ascii").strip()
        trace = record(Lenient(), ["increment"])
        trace = replace(trace, steps=(replace(trace.steps[0], input=b"increment "),))
        with self.assertRaisesRegex(ModelError, "not canonical"):
            replay(Lenient(), trace)

    def test_replay_restores_checkpoint_not_initial(self):
        class DifferentInitial(Counter):
            def initial_state(self):
                raise AssertionError("must not call")
        self.assertEqual(replay(DifferentInitial(), record(Counter(), ["increment"])).outcome, "exact")

    def test_build_mismatch_must_be_explicit(self):
        class DifferentBuild(Counter):
            def metadata(self):
                return Metadata("counter", build="different")
        trace = record(Counter(), [])
        self.assertEqual(replay(DifferentBuild(), trace).outcome, "incompatible")
        accepted = replay(DifferentBuild(), trace, allow_build_mismatch=True)
        self.assertEqual(accepted.outcome, "exact")
        self.assertFalse(accepted.build_matches)

    def test_duplicate_failure_ids_preserve_order(self):
        class Checks(Counter):
            def check_state(self, state):
                return (Check("same", "failed", "one"), Check("same", "failed", "two"))
        result = enumerate_states(Checks())
        self.assertEqual([v.check.details for v in result.failure.violations], ["one", "two"])

    def test_mutable_native_state_branches_are_isolated(self):
        class Mutable:
            def __init__(self):
                self.start = {"items": []}
            def initial_state(self):
                return self.start
            def inputs(self, state):
                return ("a", "b") if not state["items"] else ()
            def step(self, state, input):
                state["items"].append(input)  # mutates only a defensive working copy
                return Transition(state)
            def check_state(self, state):
                return [Check("one", "failed", "aliased") if len(state["items"]) > 1 else Check("one")]
        model = Mutable()
        result = enumerate_states(model)
        self.assertEqual(result.termination, "graph_exhausted")
        self.assertEqual(result.states, 3)
        self.assertEqual(model.start, {"items": []})

    def test_hashable_and_unhashable_equal_states_deduplicate(self):
        class Mixed:
            def initial_state(self):
                return frozenset({1})
            def inputs(self, state):
                return ("same",)
            def step(self, state, input):
                return Transition(set(state))
            def check_state(self, state):
                return ()
        result = enumerate_states(Mixed())
        self.assertEqual((result.states, result.transitions), (1, 1))

    def test_work_limit_validation_rejects_bool_negative(self):
        for value in (True, -1, 1.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SearchConfig(max_states=value)
        for value in (0, True, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CheckPolicy(value)

    def test_zero_rng_domain_does_not_consume_state(self):
        a, b = Rng(123), Rng(123)
        self.assertIsNone(a.index(0))
        self.assertEqual(a.next_u64(), b.next_u64())

    def test_u64_boundaries(self):
        for value in (True, -1, 1 << 64):
            with self.assertRaises(ValueError):
                Rng(value)
        self.assertLess(Rng((1 << 64) - 1).next_u64(), 1 << 64)

    def test_record_payload_limit(self):
        trace = record(Counter(), ["increment"], limits=TraceLimits(max_payload_bytes=1))
        self.assertEqual(trace.termination, "model_error")
        self.assertEqual(trace.steps, ())

    def test_record_blob_limit(self):
        with self.assertRaises(ModelError):
            record(Counter(), [], limits=TraceLimits(max_blob_bytes=0))

    def test_record_item_limit(self):
        with self.assertRaises(ModelError):
            record(Counter(), [], limits=TraceLimits(max_items=0))

    def test_json_byte_and_aggregate_limits(self):
        trace = record(Counter(), ["increment"])
        encoded = trace_json.dumps(trace)
        for limits in (TraceLimits(max_json_bytes=10), TraceLimits(max_blob_bytes=0), TraceLimits(max_items=1), TraceLimits(max_payload_bytes=1)):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                trace_json.loads(encoded, limits=limits)

    def test_json_rejects_duplicates_unicode_floats_and_trailing(self):
        for raw in ('{"x":1,"x":2}', '{"x":"\\ud800"}', '{"x":NaN}', '{"x":1.0}', '{} trailing'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                trace_json.strict_loads(raw)

    def test_trace_false_termination_and_continuation_rejected(self):
        trace = record(Counter(), ["increment"] * 4)
        with self.assertRaises(ModelError):
            replay(Counter(), replace(trace, termination="completed"))
        with self.assertRaises(ModelError):
            replay(Counter(), replace(trace, steps=trace.steps + (trace.steps[-1],)))

    def test_corpus_cli(self):
        corpus = Path(__file__).resolve().parents[2] / "conformance" / "corpus.json"
        result = subprocess.run([sys.executable, "-m", "statelessness.conformance", str(corpus)], text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["failures"], [])


if __name__ == "__main__":
    unittest.main()

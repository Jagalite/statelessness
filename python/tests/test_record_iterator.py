"""Recording must retain its valid checkpoint when input iteration fails."""
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import unittest

from statelessness.core import (Check, Metadata, ModelError, TraceLimits,
                               Transition, record, replay, validate_recording)
from statelessness import trace_json


class Counter:
    """Small deterministic model; independent of portable corpus adapters."""

    def initial_state(self):
        return 7

    def metadata(self):
        return Metadata("record-iterator-errors", build="iterator-regression-v1")

    def step(self, state, input):
        return Transition(state + input, (state + input,))

    def check_state(self, state):
        return (Check("nonnegative"),)

    def encode_state(self, state):
        return str(state).encode("ascii")

    def decode_state(self, data):
        return int(data.decode("ascii"))

    encode_input = encode_state
    decode_input = decode_state
    encode_output = encode_state


class IterError:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    def __iter__(self):
        self.calls += 1
        raise self.error


class NextError:
    def __init__(self, error, prefix=()):
        self.error = error
        self.prefix = iter(prefix)
        self.iter_calls = 0
        self.next_calls = 0

    def __iter__(self):
        self.iter_calls += 1
        if self.iter_calls > 1:
            raise AssertionError("must not restart input iteration")
        return self

    def __next__(self):
        self.next_calls += 1
        try:
            return next(self.prefix)
        except StopIteration:
            raise self.error


class RecordIteratorTests(unittest.TestCase):
    def assert_error_prefix(self, trace, prefix=(), error="inputs: source failed"):
        expected = replace(record(Counter(), prefix), termination="model_error", error=error)
        self.assertEqual(trace, expected)
        validate_recording(trace)
        decoded = trace_json.loads(trace_json.dumps(trace))
        self.assertEqual(decoded, trace)
        result = replay(Counter(), decoded)
        self.assertEqual(result.outcome, "exact")
        self.assertEqual(result.steps_verified, len(prefix))
        self.assertFalse(result.failure_reproduced)

    def test_iter_creation_error_retains_initial_checkpoint(self):
        source = IterError(RuntimeError("source failed"))
        self.assert_error_prefix(record(Counter(), source))
        self.assertEqual(source.calls, 1)

    def test_model_error_from_iter_is_retained(self):
        self.assert_error_prefix(record(Counter(), IterError(ModelError("source failed"))))

    def test_iter_stop_iteration_is_an_error_not_an_empty_sequence(self):
        self.assert_error_prefix(record(Counter(), IterError(StopIteration("source failed"))))

    def test_invalid_iter_return_retains_checkpoint(self):
        class Invalid:
            def __iter__(self):
                return []
        trace = record(Counter(), Invalid())
        self.assertIn("non-iterator", trace.error)
        self.assert_error_prefix(trace, error=trace.error)

    def test_non_iterable_input_retains_checkpoint(self):
        trace = record(Counter(), None)
        self.assertIn("not iterable", trace.error)
        self.assert_error_prefix(trace, error=trace.error)

    def test_first_next_error_matches_iter_creation_error(self):
        early = record(Counter(), IterError(RuntimeError("source failed")))
        later = record(Counter(), NextError(RuntimeError("source failed")))
        self.assertEqual(early, later)
        self.assert_error_prefix(later)

    def test_later_next_error_retains_all_completed_steps(self):
        source = NextError(RuntimeError("source failed"), (1, 2))
        trace = record(Counter(), source)
        self.assert_error_prefix(trace, (1, 2))
        self.assertEqual((source.iter_calls, source.next_calls), (1, 3))

    def test_empty_and_single_pass_inputs_still_complete(self):
        for inputs in ([], iter(()), (x for x in ())):
            with self.subTest(kind=type(inputs).__name__):
                self.assertEqual(record(Counter(), inputs), record(Counter(), []))
        source = NextError(StopIteration(), (1, 2))
        self.assertEqual(record(Counter(), source), record(Counter(), [1, 2]))
        self.assertEqual((source.iter_calls, source.next_calls), (1, 3))

    def test_initial_failure_takes_precedence_without_opening_inputs(self):
        class Failed(Counter):
            def check_state(self, state):
                return (Check("initial", "failed", "initial failure"),)
        source = IterError(RuntimeError("must not open"))
        trace = record(Failed(), source)
        self.assertEqual(trace.termination, "property_failed")
        self.assertEqual(trace.error, "")
        self.assertEqual(trace.steps, ())
        self.assertEqual(source.calls, 0)
        self.assertTrue(replay(Failed(), trace).failure_reproduced)

    def test_initial_callback_errors_still_raise_before_opening_inputs(self):
        def fail(*_):
            raise RuntimeError("checkpoint failed")
        for stage in ("initial_state", "check_state", "encode_state", "metadata"):
            with self.subTest(stage=stage):
                model = Counter()
                setattr(model, stage, fail)
                source = IterError(RuntimeError("must not open"))
                with self.assertRaisesRegex(ModelError, "checkpoint failed"):
                    record(model, source)
                self.assertEqual(source.calls, 0)

    def test_initial_limits_still_raise_before_opening_inputs(self):
        for limits in (TraceLimits(max_blob_bytes=0), TraceLimits(max_items=0),
                       TraceLimits(max_payload_bytes=0)):
            with self.subTest(limits=limits):
                source = IterError(RuntimeError("must not open"))
                with self.assertRaises(ModelError):
                    record(Counter(), source, limits=limits)
                self.assertEqual(source.calls, 0)

    def test_zero_step_limits_retain_iterator_error(self):
        for options in ({"max_steps": 0}, {"limits": TraceLimits(max_steps=0)}):
            with self.subTest(options=options):
                self.assert_error_prefix(record(Counter(), IterError(RuntimeError("source failed")), **options))

    def test_step_limit_stops_before_executing_extra_input(self):
        source = NextError(RuntimeError("must not poll past the lookahead"), (1, 2))
        trace = record(Counter(), source, max_steps=1)
        self.assertEqual(trace, replace(record(Counter(), [1]), termination="step_limit"))
        self.assertEqual((source.iter_calls, source.next_calls), (1, 2))
        self.assertEqual(replay(Counter(), trace).outcome, "exact")

    def test_process_control_exceptions_are_not_swallowed(self):
        for kind in (KeyboardInterrupt, SystemExit, GeneratorExit):
            for factory in (IterError, NextError):
                with self.subTest(kind=kind.__name__, phase=factory.__name__):
                    with self.assertRaises(kind):
                        record(Counter(), factory(kind()))

    def test_retained_error_does_not_rerun_checkpoint_callbacks(self):
        class Counted(Counter):
            def __init__(self):
                self.calls = []
            def initial_state(self):
                self.calls.append("initial")
                return super().initial_state()
            def check_state(self, state):
                self.calls.append("check")
                return super().check_state(state)
            def encode_state(self, state):
                self.calls.append("encode")
                return super().encode_state(state)
            def metadata(self):
                self.calls.append("metadata")
                return super().metadata()
            def step(self, *_):
                raise AssertionError("no transition may execute")
        model = Counted()
        self.assert_error_prefix(record(model, IterError(RuntimeError("source failed"))))
        self.assertEqual(model.calls, ["initial", "check", "encode", "metadata"])

    def test_error_prefix_replays_in_fresh_process(self):
        trace = record(Counter(), IterError(RuntimeError("source failed")))
        env = os.environ.copy()
        paths = [str(Path(__file__).resolve().parent), *sys.path]
        env["PYTHONPATH"] = os.pathsep.join(paths)
        result = subprocess.run([
            sys.executable, "-c",
            "import sys; from test_record_iterator import Counter; "
            "from statelessness.core import replay; "
            "from statelessness.trace_json import loads; "
            "r = replay(Counter(), loads(sys.stdin.read())); "
            "assert r.outcome == 'exact' and r.steps_verified == 0 "
            "and not r.failure_reproduced; print('exact initial prefix')",
        ], input=trace_json.dumps(trace), text=True, capture_output=True, timeout=15, env=env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout.strip(), "exact initial prefix")


if __name__ == "__main__":
    unittest.main()

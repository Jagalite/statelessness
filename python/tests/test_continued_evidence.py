"""continued-failure-evidence-v1 (S5): record, format, replay, shrink, graph findings."""
from dataclasses import replace
import unittest

from statelessness import (Check, ContinuedFinding, ContinuedTrace, Failure, FailurePolicy,
                           GraphSearchOptions, Metadata, ModelError, ShrinkConfig, Trace, Transition,
                           Violation, encode_continued_trace, explore_graph, parse_continued_trace,
                           record, record_continued, replay, replay_continued, shrink, shrink_continued,
                           validate_continued_recording, validate_recording)
from statelessness import trace_json
from statelessness.trace_json import FormatError


def blob(value): return int(value).to_bytes(8, "little", signed=True)
def unblob(data):
    if len(data) != 8: raise ModelError("bad integer")
    return int.from_bytes(data, "little", signed=True)


class Counter:
    """state += input, outputs (input, state); `high` fails at state >= 3;
    `big_step` fails for input >= 5."""
    def __init__(self, version=1, throw_at=None): self.version, self.throw_at = version, throw_at
    def metadata(self): return Metadata("counter", model_version=self.version)
    def initial_state(self): return 0
    def inputs(self, state): return [1, 5]
    def step(self, s, i): return Transition(s + i, (i, s + i))
    def check_state(self, s):
        if s == self.throw_at: raise RuntimeError("checker exploded")
        return (Check("high", "failed", str(s)) if s >= 3 else Check("high"),)
    def check_transition(self, before, i, t): return (Check("big_step", "failed", str(i)),) if i >= 5 else ()
    encode_state = encode_input = encode_output = staticmethod(blob)
    decode_state = decode_input = staticmethod(unblob)


class Trigger:
    """0 unarmed, 1 armed, 2 fired. Input 0 waits, 1 arms (warning), 2 fires and
    is enabled only when armed."""
    def metadata(self): return Metadata("trigger")
    def initial_state(self): return 0
    def inputs(self, s): return [0, 1] if s == 0 else [0, 2] if s == 1 else []
    def step(self, s, i): return Transition(1 if i == 1 and s == 0 else 2 if i == 2 and s == 1 else s)
    def check_state(self, s): return (Check("fired", "failed", "boom") if s == 2 else Check("fired"),)
    def check_transition(self, s, i, t): return (Check("armed_warning", "failed", "armed"),) if i == 1 and s == 0 else ()
    def generate(self, state, rng): raise AssertionError("not used")
    def is_enabled(self, s, i): return i != 2 or s == 1
    encode_state = encode_input = encode_output = staticmethod(blob)
    decode_state = decode_input = staticmethod(unblob)


WITNESS = [1, 1, 1, 1, 5, 0, 7]
BIG_STEP = Violation("transition", Check("big_step", "failed", ""))


def rebuilt(t, steps): return ContinuedTrace(replace(t.trace, steps=tuple(steps)))


class ContinuedRecordTests(unittest.TestCase):
    def test_record_continues_past_failures_and_old_readers_reject(self):
        t = record_continued(Counter(), WITNESS)
        self.assertEqual((len(t.trace.steps), t.trace.termination, t.failing_sequences), (7, "completed", (3, 4, 5, 6, 7)))
        validate_continued_recording(t)
        with self.assertRaisesRegex(ModelError, "continues after property failure"): validate_recording(t.trace)
        with self.assertRaisesRegex(ModelError, "continues after property failure"): replay(Counter(), t.trace)
        # The stop-at-failure recorder ends at the first failure, unchanged.
        legacy = record(Counter(), WITNESS)
        self.assertEqual((len(legacy.steps), legacy.termination), (3, "property_failed"))
        encoded = encode_continued_trace(t)
        self.assertEqual(parse_continued_trace(encoded), t)
        self.assertEqual(parse_continued_trace(encoded.encode("utf-8")), t)
        # trace-json v1 readers reject the artifact, never reinterpret it.
        with self.assertRaises(FormatError): trace_json.loads(encoded)
        with self.assertRaises(FormatError): parse_continued_trace(trace_json.dumps(legacy))

    def test_initial_failure_is_recorded_as_sequence_zero(self):
        class Bad(Counter):
            def check_state(self, s): return (Check("high", "failed", str(s)),)
        t = record_continued(Bad(), [1])
        self.assertEqual((t.failing_sequences, len(t.trace.steps)), ((0, 1), 1))
        self.assertTrue(replay_continued(Bad(), t).complete_reproduction)

    def test_step_limit_and_incomplete_checks_after_failure(self):
        limited = record_continued(Counter(), WITNESS, 4)
        self.assertEqual((limited.trace.termination, len(limited.trace.steps)), ("step_limit", 4))
        t = record_continued(Counter(throw_at=4), [1, 1, 1, 1, 1])
        self.assertEqual((t.trace.termination, len(t.trace.steps), t.failing_sequences), ("model_error", 3, (3,)))
        self.assertIn("checker exploded", t.trace.error)
        self.assertEqual(parse_continued_trace(encode_continued_trace(t)), t)


class ContinuedReplayTests(unittest.TestCase):
    def test_replay_compares_every_step(self):
        t = record_continued(Counter(), WITNESS)
        exact = replay_continued(Counter(), t)
        self.assertTrue(exact.complete_reproduction)
        self.assertEqual((exact.reproduced_failures, exact.replay.steps_verified), ((3, 4, 5, 6, 7), 7))
        steps = list(t.trace.steps)
        steps[4] = replace(steps[4], outputs=(blob(5), blob(0)))
        corrupt = replay_continued(Counter(), rebuilt(t, steps))
        self.assertFalse(corrupt.complete_reproduction)
        self.assertEqual((corrupt.replay.outcome, corrupt.replay.step, corrupt.replay.field), ("diverged", 5, "outputs"))
        steps = list(t.trace.steps)
        steps[1] = replace(steps[1], outputs=tuple(reversed(steps[1].outputs)))
        self.assertEqual(replay_continued(Counter(), rebuilt(t, steps)).replay.field, "outputs")
        steps = list(t.trace.steps)
        steps[6] = replace(steps[6], checks=(Check("high"),))
        suppressed = replay_continued(Counter(), rebuilt(t, steps))
        self.assertFalse(suppressed.complete_reproduction)
        self.assertEqual((suppressed.replay.field, suppressed.recorded_failures), ("checks", (3, 4, 5, 6)))
        incompatible = replay_continued(Counter(version=2), t)
        self.assertEqual((incompatible.replay.outcome, incompatible.complete_reproduction), ("incompatible", False))
        with self.assertRaises(ModelError):
            replay_continued(Counter(), ContinuedTrace(replace(t.trace, termination="property_failed")))

    def test_corrupted_and_truncated_artifacts_are_rejected(self):
        encoded = encode_continued_trace(record_continued(Counter(), WITNESS))
        self.assertIn('"failing_sequences":[3,4,5,6,7]', encoded)
        with self.assertRaises(FormatError): parse_continued_trace(encoded[:len(encoded) // 2])
        with self.assertRaisesRegex(FormatError, "failing_sequences disagree"):
            parse_continued_trace(encoded.replace('"failing_sequences":[3,4,5,6,7]', '"failing_sequences":[3,4]'))
        with self.assertRaises(FormatError): parse_continued_trace(encoded.replace('"version":1', '"version":2'))
        with self.assertRaises(FormatError): parse_continued_trace(encoded.replace('"version":1', '"version":true'))
        with self.assertRaises(FormatError):
            parse_continued_trace(encoded.replace('"termination":"completed"', '"termination":"property_failed"'))
        failed = ContinuedTrace(Trace(Metadata("counter"), blob(0), (), (), "property_failed"))
        with self.assertRaises(ModelError): encode_continued_trace(failed)


class ContinuedShrinkTests(unittest.TestCase):
    def test_two_failures_on_one_turn(self):
        r = shrink_continued(Counter(), WITNESS, BIG_STEP)
        self.assertEqual((r.base.validated_original, r.base.termination), (True, "search_complete"))
        self.assertEqual((r.base.original.inputs, r.base.minimized.inputs), (tuple(WITNESS), (7,)))
        self.assertEqual([f.sequence for f in r.original_findings], [3, 4, 5, 6, 7])
        self.assertEqual(r.minimized_findings, (ContinuedFinding(1, (Violation("state", Check("high", "failed", "7")),
                                                                    Violation("transition", Check("big_step", "failed", "7")))),))
        # Stop-at-failure shrinking cannot reach the later target.
        with self.assertRaises(ModelError): shrink(Counter(), Failure(tuple(WITNESS), (BIG_STEP,)))

    def test_selected_later_target_on_successive_turns(self):
        target = Violation("state", Check("fired", "failed", ""))
        r = shrink_continued(Trigger(), [0, 1, 0, 2], target)
        self.assertEqual(r.base.minimized.inputs, (1, 2))
        self.assertEqual(r.minimized_findings, (
            ContinuedFinding(1, (Violation("transition", Check("armed_warning", "failed", "armed")),)),
            ContinuedFinding(2, (Violation("state", Check("fired", "failed", "boom")),))))
        outcomes = [a.outcome for a in r.base.history]
        self.assertIn("invalid_causality", outcomes)
        self.assertIn("different_failure", outcomes)
        with self.assertRaisesRegex(ModelError, "does not reproduce"):
            shrink_continued(Trigger(), [0, 1, 0, 2], Violation("transition", Check("armed_warning", "failed", "")))
        with self.assertRaises(ModelError): shrink_continued(Trigger(), [1, 2], Violation("state", Check("fired")))

    def test_budget_keeps_original_and_minimized_witnesses(self):
        r = shrink_continued(Counter(), WITNESS, BIG_STEP, ShrinkConfig(max_attempts=2))
        self.assertEqual((r.base.termination, r.base.original.inputs, r.base.minimized.inputs),
                         ("attempt_limit", tuple(WITNESS), (5, 0, 7)))
        self.assertEqual(r.minimized_findings[-1].sequence, 3)

    def test_interrupted_validation_keeps_witness_without_findings(self):
        from statelessness import RunLimits, ShrinkLimits
        r = shrink_continued(Counter(), WITNESS, BIG_STEP, limits=ShrinkLimits(RunLimits(cancellation=lambda: True)))
        self.assertEqual((r.base.validated_original, r.base.minimized.inputs, r.original_findings, r.minimized_findings),
                         (False, tuple(WITNESS), (), ()))

    def test_graph_findings_flow_into_continued_evidence(self):
        g = Trigger()
        report = explore_graph(g, options=GraphSearchOptions(failure_policy=FailurePolicy.collect(10, True)))
        self.assertEqual([f.witness for f in report.findings], [(1,), (1, 2)])
        self.assertEqual([f.requires_continued_evidence for f in report.findings], [False, True])
        later = report.findings[1]
        shrunk = shrink_continued(g, later.witness, later.violations[0])
        self.assertEqual(shrunk.base.minimized.inputs, (1, 2))
        trace = record_continued(g, later.witness)
        self.assertTrue(replay_continued(g, trace).complete_reproduction)
        self.assertEqual(trace.failing_sequences, (1, 2))


if __name__ == "__main__":
    unittest.main()

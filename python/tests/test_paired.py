"""Independent application reducers against hand-reviewed paired-v1 goldens."""
import csv
from dataclasses import replace
from pathlib import Path
import unittest

from statelessness import (Check, Disposition, Metadata, ModelError, ObservationError,
                          Paired, SearchConfig, TraceLimits, Transition, WithOracle,
                          check_observed, enumerate_states, record, replay)
from statelessness import trace_json

ROOT = Path(__file__).resolve().parents[2]


class Queue:
    """Actual application: tuple queue, textual effects, explicit branches."""
    def __init__(self, mutation="none"):
        self.mutation = mutation

    def metadata(self):
        return Metadata("paired-queue-actual:" + self.mutation, build="paired-fixture-v1")

    def initial_state(self):
        return (1,) if self.mutation == "initial" else ()

    def inputs(self, _state):
        return (0, 1, 2)

    def step(self, state, input):
        if input not in (0, 1, 2):
            raise ModelError("invalid queue input")
        if input < 2:
            if len(state) == 2:
                return Transition(state, (), Disposition("rejected", "full"))
            next = state + (input,)
            outputs = (str(10 + input), str(20 + len(next)))
            if self.mutation == "state":
                next = state + (1 - input,)
            if self.mutation == "order":
                outputs = outputs[::-1]
            if self.mutation == "omit":
                outputs = outputs[:1]
            return Transition(next, outputs)
        if not state:
            return Transition(state, (), Disposition("ignored", "empty!" if self.mutation == "reason" else "empty"))
        return Transition(state[1:], (str(30 + state[0]), str(19 + len(state))))

    def check_state(self, state):
        return (Check("queue.bound") if len(state) <= 2 else Check("queue.bound", "failed", "overfull"),)

    def check_transition(self, *_):
        return (Check("queue.transition"),)

    def encode_state(self, state):
        return len(state).to_bytes(4, "little") + bytes(state)

    def decode_state(self, data):
        if len(data) < 4 or int.from_bytes(data[:4], "little") != len(data) - 4 or len(data) > 6 or any(x > 1 for x in data[4:]):
            raise ModelError("invalid queue checkpoint")
        return tuple(data[4:])

    def encode_input(self, input):
        return bytes([input])

    def decode_input(self, data):
        if len(data) != 1 or data[0] > 2:
            raise ModelError("invalid queue input")
        return data[0]

    def encode_output(self, output):
        return output.encode("ascii")


class Reference:
    """Independent arithmetic reducer: base-3 digits, oldest item least significant."""
    def metadata(self):
        return Metadata("paired-queue-reference", build="paired-fixture-v1")

    def initial_state(self):
        return 0

    def step(self, packed, operation):
        size = int(packed != 0) + int(packed >= 3)
        if operation == 2:
            return (Transition(packed // 3, (29 + packed % 3, 19 + size)) if size
                    else Transition(0, (), Disposition("ignored", "empty")))
        if operation not in (0, 1):
            raise ModelError("invalid reference input")
        return (Transition(packed, (), Disposition("rejected", "full")) if size == 2 else
                Transition(packed + (operation + 1) * (3 ** size), (10 + operation, 21 + size)))

    def check_state(self, state):
        return (Check("reference.bound") if state in (0, 1, 2, 4, 5, 7, 8) else Check("reference.bound", "failed", "invalid digits"),)

    def check_transition(self, *_):
        return (Check("reference.transition"),)

    def encode_state(self, state):
        return bytes([state])

    def decode_state(self, data):
        if len(data) != 1 or data[0] not in (0, 1, 2, 4, 5, 7, 8):
            raise ModelError("invalid reference checkpoint")
        return data[0]


class Projection:
    def metadata(self):
        return Metadata("paired-queue-projection", build="paired-fixture-v1")

    def actual_state(self, state):
        return tuple(state)

    def reference_state(self, packed):
        result = []
        while packed:
            result.append(packed % 3 - 1)
            packed //= 3
        return tuple(result)

    def actual_effect(self, effect):
        return int(effect)

    def reference_effect(self, effect):
        return effect


def paired(mutation="none"):
    return WithOracle(Queue(mutation), Paired(Reference(), Projection()))


class PairedTests(unittest.TestCase):
    def test_independent_golden_search_cases(self):
        with (ROOT / "conformance/paired-v1.tsv").open() as file:
            for case in csv.DictReader(file, delimiter="\t"):
                with self.subTest(case=case["case"]):
                    report = enumerate_states(paired(case["mutation"]), SearchConfig(*(int(case[k]) for k in ("max_states", "max_transitions", "max_depth"))))
                    self.assertEqual(report.termination, case["termination"])
                    self.assertEqual((report.states, report.transitions), (int(case["states"]), int(case["transitions"])))
                    ids = [v.check.id for v in report.failure.violations] if report.failure else []
                    witness = report.failure.inputs if report.failure else ()
                    self.assertEqual(ids, [] if case["failed_ids"] == "-" else case["failed_ids"].split(","))
                    self.assertEqual(witness, () if case["witness"] == "-" else tuple(map(int, case["witness"].split(","))))
                    self.assertEqual([v.phase for v in report.failure.violations] if report.failure else [],
                                     [] if case["failed_phases"] == "-" else case["failed_phases"].split(","))
                    self.assertEqual(report.skipped_checks, int(case["skipped_checks"]))
                    if report.failure:
                        trace = record(paired(case["mutation"]), witness)
                        batch = trace.steps[-1].checks if trace.steps else trace.initial_checks
                        expected = ["queue.bound", "reference.bound", "pair.state"]
                        if trace.steps:
                            expected += ["queue.transition", "reference.transition", "pair.effects", "pair.outcome"]
                        self.assertEqual([(c.id, c.status) for c in batch],
                                         [(id, "failed" if id in ids else "passed") for id in expected])

    def test_ordered_checks_codec_restart_and_fail_fast_replay(self):
        model = paired()
        before = model.initial_state()
        transition = model.step(before, 1)
        checks = check_observed(model, before, 1, transition, 1)
        self.assertEqual([c.id for c in checks], ["queue.bound", "reference.bound", "pair.state", "queue.transition", "reference.transition", "pair.effects", "pair.outcome"])
        state = model.decode_state(model.encode_state(transition.state))
        self.assertEqual(state, transition.state)
        self.assertEqual(model.step(state, 2), model.step(transition.state, 2))
        trace = record(paired("order"), [0, 1, 2])
        self.assertEqual(len(trace.steps), 1)
        self.assertEqual(trace.termination, "property_failed")
        replayed = replay(paired("order"), trace_json.loads(trace_json.dumps(trace)))
        self.assertTrue(replayed.failure_reproduced)
        self.assertEqual(replayed.outcome, "exact")
        self.assertEqual(model.encode_state(before).hex(), "4f524331040000000000000000000000010000000000000000")

    def test_metadata_mismatch_remains_strict_with_build_override(self):
        trace = record(paired(), [0])
        self.assertEqual(replay(paired("order"), trace, allow_build_mismatch=True).outcome, "incompatible")
        model = paired()
        class Changed(Projection):
            def metadata(self):
                return replace(super().metadata(), properties_version=2)
        model.oracle.projection = Changed()
        self.assertEqual(replay(model, trace, allow_build_mismatch=True).outcome, "incompatible")

    def test_exact_replay_rejects_permissive_component_decoder_alias(self):
        class PermissiveQueue(Queue):
            def decode_state(self, data):
                return super().decode_state(data[:-1] if data.endswith(b"\xff") else data)
        model = WithOracle(PermissiveQueue(), Paired(Reference(), Projection()))
        trace = record(model, [])
        original = trace.initial_state
        aliased = original[:4] + (5).to_bytes(8, "little") + original[12:16] + b"\xff" + original[16:]
        self.assertEqual(model.decode_state(aliased), model.initial_state())
        with self.assertRaisesRegex(ModelError, "not canonical"):
            replay(model, replace(trace, initial_state=aliased))

    def test_observer_does_not_step_actual_and_retains_completed_transition_on_error(self):
        model = paired()
        before = model.initial_state()
        def never(*_):
            raise AssertionError("actual reducer rerun")
        model.model.step = never
        observed = Transition((0,), ("10", "21"))
        result = model.observe_transition(before, 0, observed)
        self.assertEqual(result.state.oracle, 1)
        def fail(*_):
            raise ValueError("history unavailable")
        model.oracle.advance = fail
        with self.assertRaises(ObservationError) as caught:
            model.observe_transition(before, 0, observed)
        self.assertEqual(caught.exception.transition, observed)

    def test_limits_bad_checkpoint_projection_and_duplicate_checks(self):
        model = paired()
        data = model.encode_state(model.initial_state())
        for bad in (b"", data[:-1], data + b"x", b"ORC1" + b"\xff" * 8):
            with self.subTest(bad=bad), self.assertRaises(ModelError):
                model.decode_state(bad)
        with self.assertRaises(ModelError):
            record(model, [], limits=TraceLimits(max_blob_bytes=4))
        bounded = record(model, [0, 1], limits=TraceLimits(max_steps=1))
        self.assertEqual((len(bounded.steps), bounded.termination), (1, "step_limit"))
        def fail(_):
            raise ValueError("projection failed")
        model.oracle.projection.actual_effect = fail
        with self.assertRaisesRegex(ModelError, "projection failed"):
            enumerate_states(model)
        model = paired()
        model.model.check_state = lambda _: (Check("same"), Check("same", "skipped", "deliberate"))
        self.assertEqual([c.id for c in model.check_state(model.initial_state())][:2], ["same", "same"])

    def test_same_actual_state_keeps_distinct_reference_history(self):
        class Actual:
            def metadata(self): return Metadata("constant-actual")
            def initial_state(self): return 0
            def inputs(self, _state): return (0, 1)
            def step(self, _state, _input): return Transition(0)
            def check_state(self, _state): return ()

        class HiddenReference:
            def metadata(self): return Metadata("hidden-reference")
            def initial_state(self): return False
            def step(self, state, input):
                return Transition(not state if input == 0 else state, (7,) if state and input == 1 else ())
            def check_state(self, _state): return ()

        class HiddenProjection:
            def metadata(self): return Metadata("hidden-projection")
            def actual_state(self, _state): return 0
            def reference_state(self, _state): return 0
            def actual_effect(self, effect): return effect
            def reference_effect(self, effect): return effect

        model = WithOracle(Actual(), Paired(HiddenReference(), HiddenProjection()))
        report = enumerate_states(model)
        self.assertEqual((report.termination, report.states, report.transitions, report.skipped_checks),
                         ("failure_found", 2, 4, 0))
        self.assertEqual(report.failure.inputs, (0, 1))
        self.assertEqual([(v.phase, v.check.id) for v in report.failure.violations],
                         [("transition", "pair.effects")])
        bounded = enumerate_states(model, SearchConfig(10, 3, 10))
        self.assertEqual((bounded.termination, bounded.states, bounded.transitions), ("transition_limit", 2, 3))


if __name__ == "__main__":
    unittest.main()

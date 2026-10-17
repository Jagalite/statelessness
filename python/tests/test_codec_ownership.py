"""Decoder ownership regressions; fixture bytes do not use the paired encoder."""
from copy import deepcopy
from dataclasses import replace
import struct
import unittest

from statelessness.core import Check, Metadata, ModelError, Transition, record, replay
from statelessness.oracle import OracleState, Paired, WithOracle


def box(value):
    return {"values": [value]}


def value(item):
    return item["values"][0]


def wire(actual, history):
    # ORC1 + two length-prefixed single-byte components, authored independently.
    return b"ORC1" + struct.pack("<Q", 1) + bytes([actual]) + struct.pack("<Q", 1) + bytes([history])


class Counter:
    def __init__(self, initial=7, bias=0):
        self.initial = initial
        self.bias = bias

    def metadata(self):
        # Intentionally stable across reducer mutations to exercise exact replay.
        return Metadata("decoder-ownership-counter", build="fixture-v1")

    def initial_state(self):
        return box(self.initial)

    def step(self, state, input):
        return Transition(box(value(state) + value(input) + self.bias))

    def check_state(self, state):
        return (Check("counter.valid"),)

    def encode_state(self, state):
        return bytes([value(state)])

    def decode_state(self, data):
        return box(data[0])

    def encode_input(self, input):
        return bytes([value(input)])

    def decode_input(self, data):
        return box(data[0])

    def encode_output(self, output):
        return bytes([output])


class ScratchCounter(Counter):
    """Each decode is correct on return; subsequent decodes reuse its storage."""
    def __init__(self, scratch=None, **kwargs):
        super().__init__(**kwargs)
        self.scratch = box(0) if scratch is None else scratch

    def decode_state(self, data):
        self.scratch["values"][:] = [data[0]]
        return self.scratch

    decode_input = decode_state


class History:
    def __init__(self, decoder):
        self.decoder = decoder

    def decode_history(self, data):
        return self.decoder.decode_state(data)


class Projection:
    def actual_state(self, state):
        return value(state)

    reference_state = actual_state


class SnapshotError:
    def __deepcopy__(self, memo):
        raise ValueError("snapshot unavailable")


class CodecOwnershipTests(unittest.TestCase):
    def test_oracle_snapshots_model_before_history_reuses_storage(self):
        decoder = ScratchCounter()
        model = WithOracle(decoder, History(decoder))
        restored = model.decode_state(wire(7, 42))
        self.assertEqual(restored, OracleState(box(7), box(42)))
        self.assertIsNot(restored.model, restored.oracle)
        self.assertIsNot(restored.model["values"], restored.oracle["values"])

    def test_oracle_snapshots_shared_nested_storage_with_distinct_roots(self):
        class NestedDecoder(ScratchCounter):
            def decode_state(self, data):
                super().decode_state(data)
                return {"values": self.scratch["values"]}

        decoder = NestedDecoder()
        restored = WithOracle(decoder, History(decoder)).decode_state(wire(7, 42))
        self.assertEqual(restored, OracleState(box(7), box(42)))

    def test_oracle_checkpoint_survives_later_decodes_and_caller_mutation(self):
        model = WithOracle(ScratchCounter(), History(ScratchCounter()))
        first = model.decode_state(wire(7, 42))
        second = model.decode_state(wire(9, 50))
        self.assertEqual(first, OracleState(box(7), box(42)))
        second.model["values"][0] = 100
        second.oracle["values"][0] = 101
        self.assertEqual(first, OracleState(box(7), box(42)))
        self.assertEqual(model.model.scratch, box(9))
        self.assertEqual(model.oracle.decoder.scratch, box(50))

    def test_paired_reference_decoder_gets_same_ownership_boundary(self):
        decoder = ScratchCounter()
        model = WithOracle(decoder, Paired(decoder, Projection()))
        restored = model.decode_state(wire(7, 42))
        self.assertEqual(restored, OracleState(box(7), box(42)))

    def test_oracle_model_snapshot_failure_stops_before_history_decode(self):
        calls = []
        class BadActual:
            def decode_state(self, data):
                calls.append("actual")
                return SnapshotError()
        class GoodHistory:
            def decode_history(self, data):
                calls.append("history")
                return box(data[0])

        with self.assertRaisesRegex(ModelError, "snapshot.*snapshot unavailable"):
            WithOracle(BadActual(), GoodHistory()).decode_state(wire(7, 42))
        self.assertEqual(calls, ["actual"])

    def test_oracle_history_snapshot_failure_is_not_returned_as_checkpoint(self):
        class BadHistory:
            def decode_history(self, data):
                return SnapshotError()

        with self.assertRaisesRegex(ModelError, "snapshot.*snapshot unavailable"):
            WithOracle(Counter(), BadHistory()).decode_state(wire(7, 42))

    def test_replay_snapshots_initial_state_before_input_decode(self):
        trace = record(Counter(), [box(1), box(2), box(3)])
        self.assertEqual(trace.termination, "completed")
        result = replay(ScratchCounter(), trace)
        self.assertEqual((result.outcome, result.steps_verified), ("exact", 3))

    def test_replay_does_not_hide_changed_reducer_with_shared_decoder_storage(self):
        trace = record(Counter(), [box(2)])
        # Without an initial-state snapshot, decode_input changes 7 to 2.
        # The changed reducer then computes 2 + 2 + 5 = 9, accidentally matching
        # the original reducer's 7 + 2 = 9 and falsely reporting an exact replay.
        result = replay(ScratchCounter(bias=5), trace)
        self.assertEqual((result.outcome, result.step, result.field), ("diverged", 1, "state"))
        self.assertEqual(result.steps_verified, 0)

    def test_replay_snapshots_state_before_canonical_encoder_uses_workspace(self):
        class StateEncoderWorkspace(ScratchCounter):
            def encode_state(self, state):
                encoded = super().encode_state(state)
                self.scratch["values"][:] = [99]
                return encoded
            def decode_input(self, data):
                return box(data[0])

        trace = record(Counter(), [box(1)])
        self.assertEqual(replay(StateEncoderWorkspace(), trace).outcome, "exact")

    def test_replay_snapshots_input_before_canonical_encoder_uses_workspace(self):
        class InputEncoderWorkspace(ScratchCounter):
            def decode_state(self, data):
                return box(data[0])
            def encode_input(self, input):
                encoded = super().encode_input(input)
                self.scratch["values"][:] = [99]
                return encoded

        trace = record(Counter(), [box(1), box(2)])
        self.assertEqual(replay(InputEncoderWorkspace(), trace).outcome, "exact")

    def test_replay_retains_decoded_input_for_transition_checks(self):
        class CheckingCounter(Counter):
            def check_transition(self, before, input, transition):
                ok = value(transition.state) == value(before) + value(input)
                return (Check("counter.edge") if ok else Check("counter.edge", "failed", "input changed"),)

        class ReducerWorkspace(ScratchCounter, CheckingCounter):
            def decode_state(self, data):
                return box(data[0])
            def step(self, state, input):
                transition = super().step(state, input)
                self.decode_input(b"\x63")
                return transition

        trace = record(CheckingCounter(), [box(1), box(2)])
        self.assertEqual(replay(ReducerWorkspace(), trace).outcome, "exact")

    def test_oracle_validates_entire_frame_before_calling_decoders(self):
        calls = []
        class Decoder(ScratchCounter):
            def decode_state(self, data):
                calls.append(data)
                return super().decode_state(data)
        decoder = Decoder()
        model = WithOracle(decoder, History(decoder))
        valid = wire(7, 42)
        bad_frames = [valid[:i] for i in range(len(valid))]
        bad_frames += [b"BAD!" + valid[4:], valid + b"x", b"ORC1" + b"\xff" * 8,
                       bytearray(valid), memoryview(valid)]
        for data in bad_frames:
            with self.subTest(data=data), self.assertRaises(ModelError):
                model.decode_state(data)
        self.assertEqual(calls, [])

    def test_immutable_bytes_encoder_contract_is_unchanged(self):
        calls = []
        class Encoder(Counter):
            def encode_state(self, state):
                calls.append("actual")
                return bytearray([value(state)])
        class EncoderHistory:
            def encode_history(self, history):
                calls.append("history")
                return b"\x2a"

        with self.assertRaisesRegex(ModelError, "encode model: codec must return bytes"):
            WithOracle(Encoder(), EncoderHistory()).encode_state(OracleState(box(7), box(42)))
        self.assertEqual(calls, ["actual"])

    def test_canonical_state_validation_remains_strict(self):
        class PermissiveState(ScratchCounter):
            def decode_state(self, data):
                return super().decode_state(data[:1])

        trace = record(Counter(), [])
        with self.assertRaisesRegex(ModelError, "initial state encoding is not canonical"):
            replay(PermissiveState(), replace(trace, initial_state=b"\x07\xff"))

    def test_canonical_input_validation_remains_strict(self):
        trace = record(Counter(), [box(1)])
        altered = replace(trace, steps=(replace(trace.steps[0], input=b"\x01\xff"),))
        with self.assertRaisesRegex(ModelError, "input 1 encoding is not canonical"):
            replay(ScratchCounter(), altered)

    def test_replay_leaves_recorded_trace_unchanged(self):
        trace = record(Counter(), [box(1), box(2)])
        before = deepcopy(trace)
        replay(ScratchCounter(), trace)
        self.assertEqual(trace, before)


if __name__ == "__main__":
    unittest.main()

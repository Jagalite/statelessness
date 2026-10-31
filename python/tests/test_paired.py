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
    def test_oracle_raw_encoding_captures_history_and_preserves_error_order(self):
        from statelessness import EncodeBuffer, OracleState
        history = [7]
        class Actual:
            def encode_state(self, value):
                history[0] = 9
                return b"m"
        class History:
            def encode_history(self, value): return bytes(value)
        wrapper = WithOracle(Actual(), History())
        out = bytearray()
        wrapper.encode_state_into(OracleState(0, history), EncodeBuffer(out, 100))
        self.assertEqual(bytes(out), b'ORC1' + (1).to_bytes(8, 'little') + b'm' + (1).to_bytes(8, 'little') + b'\x07')

        class BadHistory:
            def __deepcopy__(self, memo): raise ValueError('history copy failed')
        class BadModel:
            def encode_state(self, value): raise ModelError('model encode failed')
        with self.assertRaisesRegex(ModelError, 'model encode failed'):
            WithOracle(BadModel(), History()).encode_state_into(
                OracleState(0, BadHistory()), EncodeBuffer(bytearray(), 100))
        # The second frame's capacity check also precedes the deferred copy error.
        with self.assertRaisesRegex(ModelError, 'byte limit'):
            WithOracle(Actual(), History()).encode_state_into(
                OracleState(0, BadHistory()), EncodeBuffer(bytearray(), 13))
        with self.assertRaisesRegex(ModelError, 'history copy failed'):
            WithOracle(Actual(), History()).encode_state_into(
                OracleState(0, BadHistory()), EncodeBuffer(bytearray(), 100))
    def test_oracle_estimate_captures_history_before_model_callback(self):
        from statelessness.oracle import OracleState
        history = [2]
        class Model:
            def estimated_state_bytes(self, state):
                history[0] = 99
                return 3
        class Oracle:
            def estimated_state_bytes(self, value): return value[0]
        wrapped = WithOracle(Model(), Oracle(), estimated_wrapper_overhead=1)
        self.assertEqual(wrapped.estimated_state_bytes(OracleState(0, history)), 6)

    def test_oracle_estimate_preserves_model_unknown_and_error_precedence(self):
        from statelessness.oracle import OracleState
        class BadHistory:
            def __deepcopy__(self, memo): raise ValueError('bad history')
        class Unknown:
            def estimated_state_bytes(self, state): return None
        class Failing:
            def estimated_state_bytes(self, state): raise ValueError('model failure')
        state = OracleState(0, BadHistory())
        self.assertIsNone(WithOracle(Unknown(), object()).estimated_state_bytes(state))
        with self.assertRaisesRegex(ModelError, 'model failure'):
            WithOracle(Failing(), object()).estimated_state_bytes(state)

    def test_paired_checks_capture_observations_before_reference_callbacks(self):
        from statelessness.oracle import OracleState
        history, actual, effect = [1], [1], [7]
        class ReferenceModel:
            def check_state(self, _state):
                history[:] = [9]
                actual[:] = [9]
                return ()
            def step(self, state, _input):
                effect[:] = [9]
                return Transition(state, ([7],))
            def check_transition(self, *_args):
                effect[:] = [10]
                return ()
        class IdentityProjection:
            def actual_state(self, value): return value
            def reference_state(self, value): return value
            def actual_effect(self, value): return value
            def reference_effect(self, value): return value
        pair = Paired(ReferenceModel(), IdentityProjection())
        checks = []
        pair.check_state_into(history, actual, checks)
        self.assertEqual(checks[-1].status, "passed")
        checks = []
        pair.check_transition_into(OracleState([], [1]), 0,
                                   Transition(OracleState([], [1]), (effect,)), checks)
        self.assertEqual(checks[-2].status, "passed")

    def test_oracle_step_captures_input_and_history_before_reducer(self):
        from statelessness.oracle import OracleState
        item, history, model_state = ['input'], ['history'], ['state']
        seen = []
        class Model:
            def step(self, state, value):
                item[:] = ['changed input']
                history[:] = ['changed history']
                model_state[:] = ['changed state']
                return Transition(state, ())
        class Oracle:
            def advance(self, before, value, outputs, disposition):
                seen.append((before, value))
                return before
        result = WithOracle(Model(), Oracle()).step(OracleState(model_state, history), item)
        self.assertEqual(seen, [(['history'], ['input'])])
        self.assertEqual(result.state.model, ['state'])

    def test_oracle_step_captures_input_before_state_clone_hook(self):
        from statelessness.oracle import OracleState
        item = ['input']
        seen = []
        class MutatingState:
            def __deepcopy__(self, memo):
                item[:] = ['changed']
                return 1
        class Model:
            def step(self, state, value): return Transition(state, ())
        class Oracle:
            def advance(self, before, value, outputs, disposition):
                seen.append(value)
                return before
        WithOracle(Model(), Oracle()).step(OracleState(MutatingState(), 0), item)
        self.assertEqual(seen, [['input']])

    def test_oracle_enabled_captures_input_before_state_copy(self):
        from statelessness.oracle import OracleState
        item = ['input']
        seen = []
        class MutatingState:
            def __deepcopy__(self, memo):
                item[:] = ['changed']
                return 1
        class Model:
            def is_enabled(self, state, value):
                seen.append((state, value))
                return True
        self.assertTrue(WithOracle(Model(), object()).is_enabled(OracleState(MutatingState(), 0), item))
        self.assertEqual(seen, [(1, ['input'])])

    def test_oracle_checks_snapshot_both_sides_before_model_callback(self):
        from statelessness.oracle import OracleState
        state = OracleState(['model'], ['history'])
        observed = []
        class Model:
            def check_state(self, value):
                state.model[:] = ['changed model']
                state.oracle[:] = ['changed history']
                return ()
        class Oracle:
            def check_state(self, history, model):
                observed.append((history, model))
                return ()
        from statelessness import CheckSink
        WithOracle(Model(), Oracle()).check_state_into(state, CheckSink([]))
        self.assertEqual(observed, [(['history'], ['model'])])

    def test_oracle_transition_check_snapshots_before_model_callback(self):
        from statelessness.oracle import OracleState
        before = OracleState(['model'], ['history'])
        item = ['input']
        transition = Transition(OracleState(['next'], ['next history']), (['output'],))
        observed = []
        class Model:
            def check_transition(self, state, value, result):
                before.oracle[:] = ['changed']
                item[:] = ['changed']
                transition.outputs[0][:] = ['changed']
                return ()
        class Oracle:
            def check_transition(self, state, value, result):
                observed.append((state.oracle, value, result.outputs))
                return ()
        from statelessness import CheckSink
        WithOracle(Model(), Oracle()).check_transition_into(before, item, transition, CheckSink([]))
        self.assertEqual(observed, [(['history'], ['input'], (['output'],))])

    def test_oracle_initial_and_decoded_components_are_detached_before_next_callback(self):
        shared = ['model']
        history = ['history']
        class Model:
            def initial_state(self): return shared
            def decode_state(self, data): return shared
        class Oracle:
            def initial_state(self):
                shared.append('changed')
                return history
            def decode_history(self, data):
                shared.append('changed')
                return history
        wrapper = WithOracle(Model(), Oracle())
        initial = wrapper.initial_state()
        self.assertEqual(initial.model, ['model'])
        shared[:] = ['model']
        encoded = b'ORC1' + (0).to_bytes(8, 'little') * 2
        decoded = wrapper.decode_state(encoded)
        self.assertEqual(decoded.model, ['model'])
        history.append('later')
        self.assertEqual(decoded.oracle, ['history'])

    def test_observe_uses_retained_outputs_when_copying_history_mutates_original(self):
        output = ['original']
        seen = []
        class MutatingHistory:
            def __deepcopy__(self, memo):
                output[:] = ['changed']
                return None
        class Oracle:
            def advance(self, before, input, effects, disposition):
                seen.extend(effects)
                return None
        wrapper = WithOracle(object(), Oracle())
        before = wrapper.attach(0, None)
        object.__setattr__(before, 'oracle', MutatingHistory())
        actual = Transition(1, (output,))
        result = wrapper.observe_transition(before, 1, actual)
        self.assertEqual(seen, [['original']])
        self.assertEqual(result.outputs, (['original'],))

    def test_observe_snapshots_input_before_history_copy(self):
        input = ['original']
        seen = []
        class MutatingHistory:
            def __deepcopy__(self, memo):
                input[:] = ['changed']
                return None
        class Oracle:
            def advance(self, before, item, outputs, disposition):
                seen.extend(item)
                return None
        wrapper = WithOracle(object(), Oracle())
        before = wrapper.attach(0, None)
        object.__setattr__(before, 'oracle', MutatingHistory())
        wrapper.observe_transition(before, input, Transition(1))
        self.assertEqual(seen, ['original'])

    def test_observe_captures_input_before_transition_copy_and_retains_failure(self):
        query = ['original']
        seen = []
        class Output:
            def __deepcopy__(self, memo):
                query[:] = ['changed']
                return 'output'
        class Oracle:
            def advance(self, before, item, outputs, disposition):
                seen.extend(item)
                return None
        wrapper = WithOracle(object(), Oracle())
        before = wrapper.attach(0, None)
        observed = Transition(1, (Output(),))
        wrapper.observe_transition(before, query, observed)
        self.assertEqual(seen, ['original'])

        class BadInput:
            def __deepcopy__(self, memo):
                raise ValueError('bad input copy')
        with self.assertRaises(ObservationError) as caught:
            wrapper.observe_transition(before, BadInput(), Transition(1, ('kept',)))
        self.assertEqual(caught.exception.transition.outputs, ('kept',))

    def test_observe_freezes_history_and_transition_structure_before_input_copy(self):
        seen = []
        class Oracle:
            def advance(self, history, item, outputs, disposition):
                seen.append((history, outputs, disposition.kind))
                return history
        wrapper = WithOracle(object(), Oracle())
        before = wrapper.attach(0, 'original history')
        observed = Transition(1, ('original output',), Disposition('ignored', 'reason'))
        class Input:
            def __deepcopy__(self, memo):
                object.__setattr__(before, 'oracle', 'replaced history')
                object.__setattr__(observed, 'outputs', ('replaced output',))
                object.__setattr__(observed.disposition, 'kind', 'accepted')
                wrapper.oracle = object()
                return 0
        result = wrapper.observe_transition(before, Input(), observed)
        self.assertEqual(seen, [('original history', ('original output',), 'ignored')])
        self.assertEqual(result.outputs, ('original output',))

    def test_direct_projection_callbacks_cannot_mutate_state_or_effects(self):
        from statelessness import CheckSink, OracleState
        class Ref:
            def check_state(self,state): return ()
            def step(self,state,input): return Transition(state,([1],[1]))
        class MutatingProjection:
            def actual_state(self,state): state.append(2); return tuple(state)
            def reference_state(self,state): state.append(2); return tuple(state)
            def actual_effect(self,effect): effect.append(2); return tuple(effect)
            def reference_effect(self,effect): effect.append(2); return tuple(effect)
        paired=Paired(Ref(),MutatingProjection())
        actual=[1]; history=[1]; checks=[]
        paired.check_state_into(history,actual,CheckSink(checks))
        self.assertEqual((actual,history),([1],[1]))
        self.assertEqual((checks[-1].id,checks[-1].status),('pair.state','passed'))
        shared=[1]
        before=OracleState([0],[0]); transition=Transition(OracleState([1],[1]),(shared,shared))
        checks=[]
        paired.check_transition_into(before,0,transition,CheckSink(checks))
        self.assertEqual(shared,[1])
        self.assertEqual([check.status for check in checks],['passed','passed'])

    def test_paired_checks_keep_adapter_owners_across_callbacks(self):
        from statelessness import CheckSink, OracleState
        class GoodProjection:
            def actual_state(self, value): return value
            def reference_state(self, value): return value
            def actual_effect(self, value): return value
            def reference_effect(self, value): return value
        class BadProjection:
            def __getattr__(self, name):
                raise AssertionError('replacement projection used')
        class Ref:
            def step(self, state, item):
                pair.reference = object()
                return Transition(state, (item,))
            def check_state(self, state):
                pair.projection = BadProjection()
                return ()
            def check_transition(self, before, item, transition):
                pair.projection = BadProjection()
                return ()
        pair = Paired(Ref(), GoodProjection())
        checks = []
        pair.check_state_into(1, 1, CheckSink(checks))
        self.assertEqual(checks[-1].status, 'passed')
        pair.projection = GoodProjection()
        checks = []
        pair.check_transition_into(OracleState(0, 0), 1,
                                   Transition(OracleState(0, 0), (1,)), CheckSink(checks))
        self.assertEqual([check.status for check in checks], ['passed', 'passed'])

    def test_paired_checks_keep_bound_hooks_across_callbacks(self):
        from statelessness import CheckSink, OracleState
        class Projection:
            def actual_state(self, value): return value
            def reference_state(self, value): return value
            def actual_effect(self, value): return value
            def reference_effect(self, value): return value
        class Ref:
            @property
            def check_state(self):
                raise AssertionError('legacy hook must not be consulted')
            def check_state_into(self, state, sink):
                projection.actual_state = lambda value: -1
                projection.reference_state = lambda value: -2
            def step(self, state, item):
                self.check_transition = lambda *args: (_ for _ in ()).throw(AssertionError('replaced'))
                projection.actual_effect = lambda value: -1
                projection.reference_effect = lambda value: -2
                return Transition(state, (item,))
            def check_transition(self, before, item, transition): return ()
        projection = Projection()
        pair = Paired(Ref(), projection)
        checks = []
        pair.check_state_into(1, 1, CheckSink(checks))
        self.assertEqual(checks[-1].status, 'passed')
        projection = Projection()
        pair.projection = projection
        checks = []
        pair.check_transition_into(OracleState(0, 0), 1,
                                   Transition(OracleState(0, 0), (1,)), CheckSink(checks))
        self.assertEqual([check.status for check in checks], ['passed', 'passed'])

    def test_paired_reference_failures_precede_actual_copy_failures(self):
        from statelessness import CheckSink, OracleState
        class Uncopyable:
            def __deepcopy__(self, memo): raise ValueError('actual copy')
        class Reference:
            def check_state(self, state): raise ValueError('reference state')
            def step(self, state, item): raise ValueError('reference step')
        class Projection:
            def actual_state(self, value): return value
            def reference_state(self, value): return value
            def actual_effect(self, value): return value
            def reference_effect(self, value): return value
        pair = Paired(Reference(), Projection())
        with self.assertRaisesRegex(Exception, 'reference state'):
            pair.check_state_into(0, Uncopyable(), CheckSink([]))
        with self.assertRaisesRegex(Exception, 'reference step'):
            pair.check_transition_into(OracleState(0, 0), 1,
                                       Transition(OracleState(0, 0), (Uncopyable(),)), CheckSink([]))
        class WorkingReference:
            def step(self, state, item): return Transition(state, ())
        class FailingProjection(Projection):
            def actual_effect(self, value): raise ValueError('first projection')
        pair = Paired(WorkingReference(), FailingProjection())
        with self.assertRaisesRegex(ValueError, 'first projection'):
            pair.check_transition_into(OracleState(0, 0), 1,
                                       Transition(OracleState(0, 0), (1, Uncopyable())), CheckSink([]))

    def test_independent_golden_search_cases(self):
        with (ROOT / "conformance/paired-v1.tsv").open(encoding="utf-8") as file:
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


class OracleStateEqualitySnapshotTests(unittest.TestCase):
    def test_comparator_cannot_change_later_history_observation(self):
        from statelessness.oracle import OracleState
        left_history, right_history = [1], [1]

        class MutatingModel:
            def __eq__(self, other):
                right_history.append(2)
                return True

        self.assertEqual(OracleState(MutatingModel(), left_history),
                         OracleState(MutatingModel(), right_history))
        self.assertEqual(right_history, [1, 2])

    def test_unused_history_copy_failure_does_not_override_model_inequality(self):
        from statelessness.oracle import OracleState

        class BadHistory:
            def __deepcopy__(self, memo):
                raise RuntimeError('cannot copy')

        left = OracleState(1, BadHistory())
        self.assertFalse(left == OracleState(2, BadHistory()))
        with self.assertRaises(ModelError):
            left == OracleState(1, BadHistory())
        self.assertTrue(left == left)


if __name__ == "__main__":
    unittest.main()

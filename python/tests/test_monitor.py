"""Exact native runtime capture, handoffs and coherent bounded eviction."""
import io
import unittest
from dataclasses import replace
from statelessness import Check, Metadata, ModelError, Transition, RunConfig, BinaryLimits, audit_to_bytes
from statelessness.monitor import Recorder, RecorderOptions
from statelessness.replay_evidence import replay_with_observations_bounded


class Model:
    def __init__(self): self.calls = []; self.fail = False
    def metadata(self): self.calls.append('metadata'); return Metadata('monitor', build='test')
    def initial_state(self): raise AssertionError('must never initialize')
    def step(self, state, input): self.calls.append('step'); return Transition(state + input, (state + input,))
    def check_state(self, state): self.calls.append('state'); return (Check('state', 'failed', 'failure') if self.fail else Check('state'),)
    def check_transition(self, *args): self.calls.append('transition'); return (Check('edge'),)
    def encode_state_into(self, value, out): self.calls.append('encode_state'); out.append(str(value).encode())
    def encode_input_into(self, value, out): self.calls.append('encode_input'); out.append(str(value).encode())
    def encode_output_into(self, value, out): self.calls.append('encode_output'); out.append(str(value).encode())
    def decode_state(self, data): return int(data)
    def decode_input(self, data): return int(data)


class RecorderTests(unittest.TestCase):
    def test_capture_and_exact_checkpoint_promotion(self):
        model = Model(); recorder = Recorder(model, 10, RunConfig(), 1)
        self.assertEqual(model.calls.count('state'), 1)
        for before in (10, 11, 12):
            checks = recorder.observe(model, before, 1, Transition(before + 1, (before + 1,)))
            self.assertEqual(checks, (Check('state'), Check('edge')))
        self.assertNotIn('step', model.calls)
        trace = recorder.snapshot()
        self.assertEqual(trace.initial_state, b'12')
        self.assertEqual(trace.initial_checks, (Check('state'),))
        self.assertEqual((recorder.observed_steps(), recorder.evicted_steps(), recorder.retained_steps()), (3, 2, 1))
        self.assertEqual(dict(trace.config.parameters)['stateless.monitor.checkpoint_after_sequence'], '2')
        self.assertEqual(recorder.retained_bytes(), len(audit_to_bytes(trace)))
        self.assertEqual(replay_with_observations_bounded(model, trace).outcome, 'exact')
        writer = io.BytesIO(); recorder.write_to(writer)
        self.assertEqual(writer.getvalue(), audit_to_bytes(trace))

    def test_continuity_error_freezes_prefix_and_later_error_does_not_replace(self):
        model = Model(); recorder = Recorder(model, 0)
        recorder.observe(model, 0, 1, Transition(1))
        with self.assertRaisesRegex(ModelError, 'discontinuous'):
            recorder.observe(model, 9, 1, Transition(10))
        trace = recorder.snapshot()
        self.assertEqual(len(trace.steps), 1); self.assertEqual(trace.termination, 'model_error')
        recorder.stop_with_error(ModelError('later'))
        self.assertEqual(recorder.snapshot(), trace)
        with self.assertRaisesRegex(ModelError, 'frozen'): recorder.observe(model, 1, 1, Transition(2))

    def test_initial_and_turn_failures_freeze(self):
        model = Model(); model.fail = True
        initial = Recorder(model, 0)
        self.assertTrue(initial.is_frozen()); self.assertEqual(initial.snapshot().termination, 'property_failed')
        model.fail = False; recorder = Recorder(model, 0)
        model.fail = True
        recorder.observe(model, 0, 1, Transition(1))
        recorder.stop_with_error(ModelError('cannot replace failure'))
        self.assertEqual(recorder.snapshot().termination, 'property_failed')

    def test_identity_before_encoding_and_encoded_continuity_before_checks(self):
        model = Model(); recorder = Recorder(model, 0)
        class Other(Model):
            def metadata(self): return Metadata('different')
        other = Other()
        with self.assertRaisesRegex(ModelError, 'identity'): recorder.observe(other, 0, 1, Transition(1))
        self.assertEqual(other.calls, [])
        recorder = Recorder(model, 0); model.calls.clear()
        with self.assertRaisesRegex(ModelError, 'discontinuous'): recorder.observe(model, 7, 1, Transition(8))
        self.assertEqual(model.calls, ['metadata', 'encode_state'])

    def test_candidate_codec_failure_never_evicts(self):
        model = Model(); recorder = Recorder.with_options(model, 0, RunConfig(), RecorderOptions(1, limits=BinaryLimits(max_blob_bytes=2)))
        recorder.observe(model, 0, 1, Transition(1))
        checkpoint = recorder.snapshot()
        with self.assertRaises(ModelError): recorder.observe(model, 1, 1, Transition(200))
        trace = recorder.snapshot()
        self.assertEqual(trace.initial_state, checkpoint.initial_state)
        self.assertEqual(trace.steps, checkpoint.steps)
        self.assertEqual(recorder.evicted_steps(), 0)
        audit_to_bytes(trace)

    def test_checked_handoffs_sequence_origin_and_no_rechecking(self):
        from statelessness.checked import check_initial, check_turn
        model = Model(); initial = check_initial(model, 10)
        recorders = [Recorder.with_checked_initial_at(initial, 20) for _ in range(2)]
        token = check_turn(model, 10, 1, Transition(11), 21)
        count = (model.calls.count('state'), model.calls.count('transition'))
        for recorder in recorders: recorder.observe_checked(token)
        self.assertEqual((model.calls.count('state'), model.calls.count('transition')), count)
        self.assertEqual(dict(recorders[0].snapshot().config.parameters)['stateless.monitor.sequence_origin'], '20')
        with self.assertRaisesRegex(ModelError, 'sequence'): recorders[0].observe_checked(token)
        self.assertTrue(recorders[0].is_frozen())
        self.assertFalse(recorders[1].is_frozen())

    def test_invalid_options_reserved_parameters_and_format_quotas(self):
        for options in (lambda: RecorderOptions(0), lambda: RecorderOptions(2, limits=BinaryLimits(max_steps=1)), lambda: RecorderOptions(max_retained_bytes=0)):
            with self.assertRaises(ValueError): options()
        model = Model()
        with self.assertRaisesRegex(ModelError, 'reserved'): Recorder(model, 0, RunConfig(parameters=(('stateless.monitor.x', ''),)))
        self.assertEqual(model.calls, [])
        for name in BinaryLimits.__dataclass_fields__:
            if name in ('max_steps', 'max_outputs_per_step'): continue
            with self.subTest(name=name):
                with self.assertRaises(Exception):
                    Recorder.with_options(model, 0, RunConfig(), RecorderOptions(1, limits=replace(BinaryLimits(), **{name: 0})))

    def test_oversized_output_rejected_before_checks_or_input(self):
        model = Model(); recorder = Recorder.with_options(model, 0, RunConfig(), RecorderOptions(limits=BinaryLimits(max_outputs_per_step=0)))
        model.calls.clear()
        with self.assertRaises(Exception): recorder.observe(model, 0, 1, Transition(1, (1,)))
        self.assertEqual(model.calls, ['metadata', 'encode_state'])
        self.assertEqual(recorder.observed_steps(), 0)


if __name__ == '__main__': unittest.main()

class SharedRecorderTests(unittest.TestCase):
    def test_shared_recorder_vectors(self):
        import json, os
        from pathlib import Path
        from statelessness import CheckPolicy, Disposition
        from statelessness.checked import check_initial, check_turn
        path = os.environ.get('STATELESS_CHECKED_CORPUS', str(Path(__file__).resolve().parents[2] / 'conformance' / 'checked-results-v1.json'))
        corpus = json.loads(Path(path).read_text())
        def checks(values): return tuple(Check(**c) for c in values)
        def normalize_checks(values): return [dict(id=c.id, status=c.status, details=c.details) for c in values]
        class Fixture(Model):
            def __init__(self, settings, name='same', encode_error=None):
                super().__init__(); self.settings, self.name, self.encode_error = settings, name, encode_error
            def metadata(self): return Metadata(self.name)
            def check_state(self, state):
                self.calls.append('state')
                if self.settings['state_error']: raise ModelError(self.settings['state_error'])
                return checks(self.settings['state_checks'])
            def check_transition(self, *args):
                self.calls.append('transition')
                if self.settings['transition_error']: raise ModelError(self.settings['transition_error'])
                return checks(self.settings['transition_checks'])
            def encode_state_into(self, value, out): self.encode('state', value, out)
            def encode_input_into(self, value, out): self.encode('input', value, out)
            def encode_output_into(self, value, out): self.encode('output', value, out)
            def encode(self, kind, value, out):
                if self.encode_error == kind: raise ModelError('fixture encode error')
                out.append(str(value).encode())
        for row in corpus['recorder_cases']:
            with self.subTest(case=row['case']):
                model = Fixture(row['model']); expected = row['expected']
                try:
                    raw = row['options']; limits = {('max_checks_per_step' if k == 'max_checks_per_batch' else k):int(v) for k,v in raw['limits'].items()}
                    options = RecorderOptions(raw['max_steps'], int(raw['max_retained_bytes']), BinaryLimits(**limits))
                    cfg = row['config']; config = RunConfig(cfg['strategy'], None if cfg['seed'] is None else int(cfg['seed']), cfg['parameters'])
                    if row.get('entry_mode') == 'ordinary':
                        recorder = Recorder.with_options(model, row['initial_state'], config, options)
                    else:
                        initial = check_initial(model, row['initial_state'])
                        recorder = Recorder.with_checked_initial_at(initial, int(row['origin']), config, options)
                except Exception as error:
                    self.assertIsNotNone(expected['initial_error_contains'], str(error))
                    self.assertIn(expected['initial_error_contains'], str(error)); continue
                self.assertIsNone(expected['initial_error_contains'])
                for action, want in zip(row['actions'], expected['states']):
                    error = None
                    try:
                        if 'stop_error' in action: recorder.stop_with_error(ModelError(action['stop_error']))
                        else:
                            actual_model = Fixture(action.get('checks_model', row['model']), action.get('model_name', 'same'), action.get('encode_error'))
                            t = action['transition']
                            convert = str if action.get('representation') == 'decimal-string' else lambda value: value
                            before, input = convert(action['before']), convert(action['input'])
                            transition = Transition(convert(t['state']), tuple(convert(value) for value in t['outputs']), Disposition(**t['disposition']))
                            if action.get('mode') == 'ordinary':
                                recorder.observe(actual_model, before, input, transition)
                            else:
                                policy = action['policy']; token = check_turn(actual_model, before, input, transition, int(action['sequence']), CheckPolicy(int(policy['state_every']), policy['transition_checks']))
                                counts = actual_model.calls[:]
                                recorder.observe_checked(token)
                                self.assertEqual(actual_model.calls, counts, 'checked handoff rechecked token')
                    except Exception as caught: error = caught
                    if want['error_contains'] is None: self.assertIsNone(error, str(error))
                    else:
                        self.assertIsNotNone(error)
                        self.assertIn(want['error_contains'], str(error))
                    trace = recorder.snapshot()
                    actual = dict(observed_steps=str(recorder.observed_steps()), evicted_steps=str(recorder.evicted_steps()), retained_steps=recorder.retained_steps(), frozen=recorder.is_frozen(), termination=trace.termination, initial_state=int(trace.initial_state), initial_checks=normalize_checks(trace.initial_checks), steps=[])
                    # State check boundaries are recorder-internal evidence; exported trace
                    # intentionally has no phase field. Assert boundaries independently.
                    for step, boundary, _, _ in recorder._steps:
                        actual['steps'].append(dict(input=int(step.input), transition=dict(state=int(step.post_state), outputs=[int(v) for v in step.outputs], disposition=dict(kind=step.disposition.kind, reason=step.disposition.reason)), checks=normalize_checks(step.checks), state_check_count=boundary))
                    self.assertEqual(actual, {k:v for k,v in want.items() if k != 'error_contains'})
                    audit_to_bytes(trace, limits=options.limits)

class RecorderOwnershipTests(unittest.TestCase):
    def test_metadata_alias_and_spoof_cannot_change_identity(self):
        class Spoof(str):
            def __eq__(self, other): return True
            def __ne__(self, other): return False
        class Shared(Model):
            def __init__(self): super().__init__(); self.meta = Metadata('monitor')
            def metadata(self): return self.meta
        model = Shared(); recorder = Recorder(model, 0)
        object.__setattr__(model.meta, 'name', Spoof('different'))
        with self.assertRaisesRegex(ModelError, 'identity'): recorder.observe(model, 0, 1, Transition(1))
        self.assertEqual(recorder.snapshot().metadata.name, 'monitor')

    def test_transfer_consumes_native_handle(self):
        model = Model(); recorder = Recorder(model, 0)
        trace = recorder.into_trace()
        self.assertEqual(trace.termination, 'interrupted')
        for operation in (recorder.snapshot, recorder.into_trace, recorder.retained_steps, recorder.observed_steps,
                          recorder.evicted_steps, recorder.is_frozen, lambda: recorder.stop_with_error(ModelError('later'))):
            with self.assertRaisesRegex(ModelError, 'transferred'): operation()

    def test_reentrant_observation_and_external_stop_preserve_prefix(self):
        class Reentrant(Model):
            def encode_output_into(self, value, out):
                if hasattr(self, 'recorder'):
                    with self.test.assertRaisesRegex(ModelError, 'already in progress'):
                        self.recorder.observe(self, 0, 1, Transition(1))
                    self.recorder.stop_with_error(ModelError('external stop'))
                super().encode_output_into(value, out)
        model = Reentrant(); model.test = self
        recorder = Recorder(model, 0); model.recorder = recorder
        with self.assertRaisesRegex(ModelError, 'stopped during observation'):
            recorder.observe(model, 0, 1, Transition(1, (1,)))
        trace = recorder.snapshot()
        self.assertEqual(trace.steps, ())
        self.assertEqual(trace.error, 'external stop')

    def test_error_format_failure_preserves_primary_and_freezes(self):
        class Bad(ModelError):
            def __str__(self): raise RuntimeError('broken diagnostic')
        recorder = Recorder(Model(), 0); primary = Bad()
        self.assertIs(recorder.stop_with_error(primary), primary)
        self.assertTrue(recorder.is_frozen())
        self.assertEqual(recorder.snapshot().termination, 'model_error')

class CheckedAdmissionOrderTests(unittest.TestCase):
    def test_checked_checkpoint_limits_precede_check_copy(self):
        from unittest.mock import patch
        from statelessness import BinaryTraceError, check_initial
        token = check_initial(Model(), 0)
        options = RecorderOptions(limits=BinaryLimits(max_checks_per_step=0))
        with patch('statelessness.monitor._copy', side_effect=AssertionError('copied before quota')):
            with self.assertRaisesRegex(ModelError, 'checks per step'):
                Recorder.with_checked_initial(token, options=options)


class RecorderReinitializationTests(unittest.TestCase):
    def test_public_reinitialization_never_replaces_live_frozen_or_transferred_evidence(self):
        model = Model()
        recorder = Recorder(model, 0)
        recorder.observe(model, 0, 1, Transition(1))
        for frozen in (False, True):
            if frozen: recorder.stop_with_error(ModelError('first stop'))
            checkpoint, calls = recorder.snapshot(), model.calls[:]
            with self.assertRaisesRegex(ModelError, 'reinitialized'): recorder.__init__(model, 100)
            self.assertEqual(recorder.snapshot(), checkpoint)
            self.assertEqual(model.calls, calls)
        transferred = recorder.into_trace()
        with self.assertRaisesRegex(ModelError, 'reinitialized'): recorder.__init__(model, 100)
        self.assertEqual(transferred.error, 'first stop')
        with self.assertRaisesRegex(ModelError, 'transferred'): recorder.snapshot()

    def test_nested_initialization_transfer_and_stop_then_continue_leave_prefix_frozen(self):
        outer = self
        class Nested(Model):
            def encode_output_into(self, value, out):
                with outer.assertRaisesRegex(ModelError, 'reinitialized'):
                    self.recorder.__init__(self, 100)
                with outer.assertRaisesRegex(ModelError, 'already in progress'):
                    self.recorder.into_trace()
                self.recorder.stop_with_error(ModelError('first stop'))
                out.append(str(value).encode())
        model = Nested()
        recorder = Recorder(model, 0)
        model.recorder = recorder
        with self.assertRaisesRegex(ModelError, 'stopped during observation'):
            recorder.observe(model, 0, 1, Transition(1, (1,)))
        self.assertEqual(recorder.observed_steps(), 0)
        self.assertEqual(recorder.snapshot().steps, ())
        self.assertEqual(recorder.snapshot().error, 'first stop')
        with self.assertRaisesRegex(ModelError, 'frozen'): recorder.observe(model, 0, 1, Transition(1))

    def test_external_error_formatting_guards_mutation_and_restores_busy(self):
        model, outer = Model(), self
        recorder = Recorder(model, 0)
        recorder.observe(model, 0, 1, Transition(1))
        class Reentrant(ModelError):
            def __str__(self):
                for operation, message in (
                    (lambda: recorder.into_trace(), 'already in progress'),
                    (lambda: recorder.__init__(model, 100), 'reinitialized'),
                    (lambda: recorder.observe(model, 1, 1, Transition(2)), 'already in progress')):
                    with outer.assertRaisesRegex(ModelError, message): operation()
                recorder.stop_with_error(ModelError('cannot replace first'))
                self.read_only_snapshot = recorder.snapshot()
                return 'original reason'
        error = Reentrant()
        self.assertIs(recorder.stop_with_error(error), error)
        self.assertEqual(recorder.snapshot().error, 'original reason')
        self.assertEqual(recorder.observed_steps(), 1)
        self.assertEqual(error.read_only_snapshot.termination, 'model_error')
        self.assertEqual(len(error.read_only_snapshot.steps), 1)
        # External formatting restores the previous non-busy state, allowing
        # transfer of the finalized artifact after its guard has exited.
        self.assertEqual(recorder.into_trace().error, 'original reason')

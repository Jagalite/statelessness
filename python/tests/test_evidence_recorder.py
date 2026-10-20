"""Native causal journal retention coordinated with exact trace capture."""
import unittest
from dataclasses import replace
from statelessness import (Check, Metadata, ModelError, Transition, RunConfig,
                           BinaryLimits, check_initial, check_turn)
from statelessness.monitor import RecorderOptions
from statelessness.evidence_recorder import EvidenceRecorder


class Model:
    def __init__(self): self.calls = []; self.fail = False; self.encode_error = None
    def metadata(self): return Metadata('causal', build='fixture')
    def initial_state(self): raise AssertionError('runtime capture never initializes')
    def step(self, state, input): self.calls.append('step'); return Transition(state + input, (state + input,))
    def check_state(self, state): self.calls.append('state'); return (Check('s'),)
    def check_transition(self, *args):
        self.calls.append('transition')
        return (Check('t', 'failed', 'broken') if self.fail else Check('t'),)
    def encode_state_into(self, value, out): self.encode('state', value, out)
    def encode_input_into(self, value, out): self.encode('input', value, out)
    def encode_output_into(self, value, out): self.encode('output', value, out)
    def encode(self, kind, value, out):
        if self.encode_error == kind: raise ModelError('fixture codec failure')
        out.append(str(value).encode())
    def decode_state(self, data): return int(data)
    def decode_input(self, data): return int(data)


class EvidenceRecorderTests(unittest.TestCase):
    def make(self, *, model=None, origin=0, max_steps=8, metadata=4096, causes=100,
             retained=1_000_000, trace_limits=None):
        from statelessness.evidence import Provenance, EvidenceLimits
        model = model or Model()
        initial = check_initial(model, 0)
        limits = EvidenceLimits(trace=trace_limits or BinaryLimits(), max_metadata_bytes=metadata,
                                max_total_causes=causes)
        recorder = EvidenceRecorder(initial, origin, RunConfig(), RecorderOptions(max_steps, retained),
                                    Provenance('run'), limits)
        return model, recorder, limits

    def observe(self, model, recorder, before, sequence, causes=(), *, context=None, outputs=None):
        from statelessness.evidence import CausalContext
        transition = Transition(before + 1, (before + 1,) if outputs is None else outputs)
        turn = check_turn(model, before, 1, transition, sequence)
        checked = model.calls[:]
        recorder.observe(turn, context or CausalContext(causes=causes))
        self.assertEqual(model.calls, checked, 'capture must not recheck or execute')

    def aligned(self, recorder, limits):
        trace = recorder.snapshot()
        self.assertEqual(len(trace.steps), len(trace.trace.steps))
        self.assertEqual([s.sequence for s in trace.steps], list(range(trace.checkpoint_after + 1, trace.checkpoint_after + len(trace.steps) + 1)))
        trace.validate(limits)
        return trace

    def test_ring_handoffs_origins_and_checkpoint_phases(self):
        from statelessness.evidence import OutputOrigin
        model, recorder, limits = self.make(origin=40, max_steps=2)
        for index in range(4):
            causes = () if index == 0 else (OutputOrigin(40 + index, 0),)
            self.observe(model, recorder, index, 41 + index, causes)
            trace = self.aligned(recorder, limits)
        self.assertEqual(trace.checkpoint_after, 42)
        self.assertEqual(trace.trace.initial_checks, (Check('s'),))
        self.assertEqual([step.state_check_count for step in trace.steps], [1, 1])
        self.assertNotIn('step', model.calls)
        self.assertEqual(trace.replay(model, limits=limits).outcome, 'exact')

    def test_cause_count_eviction_and_omitted_prefix_links(self):
        from statelessness.evidence import OutputOrigin
        model, recorder, limits = self.make(causes=1)
        self.observe(model, recorder, 0, 1)
        self.observe(model, recorder, 1, 2, (OutputOrigin(1, 0),))
        self.observe(model, recorder, 2, 3, (OutputOrigin(2, 0),))
        trace = self.aligned(recorder, limits)
        self.assertEqual(trace.checkpoint_after, 2)
        self.assertEqual(len(trace.steps), 1)
        self.observe(model, recorder, 3, 4, (OutputOrigin(1, 999),))
        trace = self.aligned(recorder, limits)
        self.assertEqual(trace.steps[-1].causal.causes[0].output, 999)

    def test_metadata_eviction_and_single_oversized_candidate(self):
        from statelessness.evidence import CausalContext, _metadata_bytes, _step_bytes, StepEvidence
        model, recorder, limits = self.make()
        base = len(_metadata_bytes(0, recorder.snapshot().provenance, (), limits.max_metadata_bytes))
        row_size = len(_step_bytes(StepEvidence(1, 1, CausalContext(machine='m')), limits.max_metadata_bytes))
        model, recorder, limits = self.make(metadata=base + row_size)
        self.observe(model, recorder, 0, 1, context=CausalContext(machine='m'))
        self.observe(model, recorder, 1, 2, context=CausalContext(machine='m'))
        trace = self.aligned(recorder, limits)
        self.assertEqual(trace.checkpoint_after, 1); self.assertEqual(len(trace.steps), 1)
        previous = trace
        turn = check_turn(model, 2, 1, Transition(3, (3,)), 3)
        with self.assertRaises(ModelError): recorder.observe(turn, CausalContext(machine='too large'))
        trace = self.aligned(recorder, limits)
        self.assertEqual(trace.steps, previous.steps); self.assertEqual(trace.trace.steps, previous.trace.steps)
        self.assertEqual(trace.trace.termination, 'model_error')

    def test_invalid_current_retained_index_not_excused_by_planned_eviction(self):
        from statelessness.evidence import CausalContext, OutputOrigin
        model, recorder, limits = self.make(causes=1)
        self.observe(model, recorder, 0, 1)
        self.observe(model, recorder, 1, 2, (OutputOrigin(1, 0),))
        before = self.aligned(recorder, limits)
        token = check_turn(model, 2, 1, Transition(3, (3,)), 3)
        with self.assertRaisesRegex(ModelError, 'retained output range'):
            recorder.observe(token, CausalContext(causes=(OutputOrigin(1, 9),)))
        after = self.aligned(recorder, limits)
        self.assertEqual(after.steps, before.steps)
        self.assertEqual(after.checkpoint_after, 0)

    def test_trace_failure_never_commits_planned_journal_eviction(self):
        from statelessness.evidence import CausalContext, OutputOrigin
        model, recorder, limits = self.make(causes=1)
        self.observe(model, recorder, 0, 1)
        self.observe(model, recorder, 1, 2, (OutputOrigin(1, 0),))
        before = self.aligned(recorder, limits)
        token = check_turn(model, 2, 1, Transition(3, (3,)), 3)
        model.encode_error = 'output'
        with self.assertRaises(ModelError): recorder.observe(token, CausalContext(causes=(OutputOrigin(2, 0),)))
        after = self.aligned(recorder, limits)
        self.assertEqual(after.steps, before.steps); self.assertEqual(after.checkpoint_after, before.checkpoint_after)
        model.encode_error = None
        with self.assertRaisesRegex(ModelError, 'frozen'): recorder.observe(token, CausalContext())
        self.assertEqual(recorder.snapshot(), after)

    def test_reservation_and_provenance_fail_before_model_codecs(self):
        from statelessness.evidence import Provenance, EvidenceLimits
        model = Model(); initial = check_initial(model, 0)
        with self.assertRaisesRegex(ModelError, 'reservation'):
            EvidenceRecorder(initial, 0, RunConfig(), RecorderOptions(1, 100), Provenance(''), EvidenceLimits())
        for provenance in (Provenance('run', environment=(('', 'v'),)), Provenance('run', environment=(('x','a'),('x','b')))):
            with self.assertRaises(ModelError):
                EvidenceRecorder(initial, 0, RunConfig(), RecorderOptions(), provenance, EvidenceLimits())

    def test_property_failure_and_invalid_context_keep_first_terminal(self):
        from statelessness.evidence import CausalContext, OutputOrigin
        model, recorder, limits = self.make()
        model.fail = True
        self.observe(model, recorder, 0, 1)
        trace = self.aligned(recorder, limits)
        self.assertEqual(trace.trace.termination, 'property_failed')
        token = check_turn(model, 1, 1, Transition(2), 2)
        with self.assertRaisesRegex(ModelError, 'frozen'): recorder.observe(token, CausalContext(causes=(OutputOrigin(0,0),)))
        self.assertEqual(recorder.snapshot(), trace)

    def test_context_and_snapshot_ownership_and_transfer(self):
        from statelessness.evidence import CausalContext, OutputOrigin
        model, recorder, limits = self.make(origin=10)
        causes = [OutputOrigin(1, 9)]
        context = CausalContext(causes=causes)
        self.observe(model, recorder, 0, 11, context=context)
        causes.clear()
        snapshot = self.aligned(recorder, limits)
        object.__setattr__(snapshot.steps[0].causal, 'machine', 'changed snapshot')
        self.assertIsNone(recorder.snapshot().steps[0].causal.machine)
        moved = recorder.into_trace(); moved.validate(limits)
        for operation in (recorder.snapshot, recorder.into_trace, recorder.is_frozen):
            with self.assertRaisesRegex(ModelError, 'transferred'): operation()

    def test_reentrant_callback_cannot_observe_or_consume(self):
        from statelessness.evidence import CausalContext
        class Reentrant(Model):
            def encode_output_into(self, value, out):
                if hasattr(self, 'recorder'):
                    for operation in (lambda: self.recorder.observe(self.turn, CausalContext()), self.recorder.into_trace):
                        with self.test.assertRaisesRegex(ModelError, 'already in progress'): operation()
                super().encode_output_into(value, out)
        model = Reentrant(); model.test = self
        model, recorder, limits = self.make(model=model)
        model.recorder = recorder; model.turn = check_turn(model, 0, 1, Transition(1,(1,)), 1)
        recorder.observe(model.turn, CausalContext())
        self.aligned(recorder, limits)

    def test_trace_byte_budget_eviction_keeps_phase_journal_aligned(self):
        model, probe, limits = self.make(metadata=512)
        self.observe(model, probe, 0, 1)
        one_step_budget = probe._recorder.retained_bytes() + probe._recorder._reserve - 18
        model, recorder, limits = self.make(metadata=512, retained=one_step_budget + 512 + 32)
        self.observe(model, recorder, 0, 1)
        self.observe(model, recorder, 1, 2)
        trace = self.aligned(recorder, limits)
        self.assertEqual(trace.checkpoint_after, 1)
        self.assertEqual(len(trace.steps), 1)

    def test_bad_causes_and_sampled_checks_freeze_without_phase_rows(self):
        from statelessness import CheckPolicy
        from statelessness.evidence import CausalContext, OutputOrigin
        for causes in ((OutputOrigin(0, 0),), (OutputOrigin(1, 0),),
                       (OutputOrigin(1, 0), OutputOrigin(1, 0))):
            with self.subTest(causes=causes):
                model, recorder, limits = self.make(origin=1)
                token = check_turn(model, 0, 1, Transition(1), 2)
                # The future cause uses sequence2, while duplicate and zero links
                # exercise independent structural origin failures.
                if causes == (OutputOrigin(1, 0),): causes = (OutputOrigin(2, 0),)
                with self.assertRaises(ModelError): recorder.observe(token, CausalContext(causes=causes))
                trace = self.aligned(recorder, limits)
                self.assertEqual(trace.steps, ())
                self.assertEqual(trace.trace.termination, 'model_error')
        model, recorder, limits = self.make()
        token = check_turn(model, 0, 1, Transition(1), 1, CheckPolicy(2))
        with self.assertRaisesRegex(ModelError, 'full checking'):
            recorder.observe(token, CausalContext())
        self.assertEqual(self.aligned(recorder, limits).steps, ())

    def test_closed_metadata_avoids_copy_hooks_and_journal_allocates_before_trace_commit(self):
        from unittest.mock import patch
        from statelessness.evidence import CausalContext
        class Unsnapshotable(str):
            def __deepcopy__(self, memo): raise RuntimeError('snapshot unavailable')
        for context, allocate in ((CausalContext(machine=Unsnapshotable('label')), False), (CausalContext(), True)):
            with self.subTest(allocate=allocate):
                model, recorder, limits = self.make()
                token = check_turn(model, 0, 1, Transition(1), 1)
                before = recorder.snapshot()
                if allocate:
                    with patch('statelessness.evidence_recorder.deque', side_effect=MemoryError('journal allocation')):
                        with self.assertRaises(MemoryError): recorder.observe(token, context)
                else:
                    recorder.observe(token, context)
                    self.assertEqual(type(recorder.snapshot().steps[0].causal.machine), str)
                    self.assertEqual(recorder.snapshot().steps[0].causal.machine, 'label')
                    continue
                after = self.aligned(recorder, limits)
                self.assertEqual(after.steps, before.steps)
                self.assertEqual(after.trace.steps, before.trace.steps)
                self.assertEqual(after.trace.termination, 'model_error')

    def test_initial_failure_and_origin_overflow_do_not_add_phase_rows(self):
        from statelessness.evidence import CausalContext, Provenance, EvidenceLimits
        from statelessness.core import U64_MAX
        class InitiallyFailing(Model):
            def check_state(self, state): self.calls.append('state'); return (Check('s', 'failed', 'initial failure'),)
        model = InitiallyFailing(); initial = check_initial(model, 0)
        recorder = EvidenceRecorder(initial, 0, RunConfig(), RecorderOptions(), Provenance(), EvidenceLimits())
        self.assertEqual(model.calls, ['state'])
        self.assertTrue(recorder.is_frozen())
        with self.assertRaisesRegex(ModelError, 'frozen'):
            recorder.observe(check_turn(model, 0, 1, Transition(1), 1), CausalContext())
        self.assertEqual(recorder.snapshot().trace.termination, 'property_failed')
        model, recorder, limits = self.make(origin=U64_MAX)
        token = check_turn(model, 0, 1, Transition(1), 1)
        with self.assertRaisesRegex(ModelError, 'sequence'): recorder.observe(token, CausalContext())
        trace = self.aligned(recorder, limits)
        self.assertEqual(trace.checkpoint_after, U64_MAX)
        self.assertEqual(trace.steps, ())

    def test_snapshot_callback_cannot_mutate_or_consume_retained_evidence(self):
        from statelessness.evidence import CausalContext
        class Label(str):
            callback = None
            def __deepcopy__(self, memo):
                if self.callback: self.callback()
                return self
        model, recorder, limits = self.make()
        label = Label('machine')
        self.observe(model, recorder, 0, 1, context=CausalContext(machine=label))
        token = check_turn(model, 1, 1, Transition(2), 2)
        def callback():
            for operation in (lambda: recorder.observe(token, CausalContext()), recorder.into_trace):
                with self.assertRaisesRegex(ModelError, 'already in progress'): operation()
        Label.callback = staticmethod(callback)
        try:
            self.aligned(recorder, limits)
            moved = recorder.into_trace()
            moved.validate(limits)
        finally: Label.callback = None

class SharedEvidenceRecorderTests(unittest.TestCase):
    def test_shared_recorder_vectors(self):
        import json, os
        from pathlib import Path
        from statelessness import CheckPolicy, Disposition
        from statelessness.evidence import (Provenance, CausalContext, OutputOrigin,
                                           EvidenceLimits)
        path = os.environ.get('STATELESS_EVIDENCE_CORPUS', str(Path(__file__).resolve().parents[2] / 'conformance' / 'causal-evidence-v2.json'))
        corpus = json.loads(Path(path).read_text())
        def normalize_checks(values): return [dict(id=c.id, status=c.status, details=c.details) for c in values]
        def causal(raw):
            return CausalContext(raw['machine'], None if raw['generation'] is None else int(raw['generation']), raw['correlation'],
                None if raw['logical_time'] is None else int(raw['logical_time']), tuple(OutputOrigin(int(c['sequence']), c['output']) for c in raw['causes']))
        def normalize_causal(value):
            return dict(machine=value.machine, generation=None if value.generation is None else str(value.generation), correlation=value.correlation,
                logical_time=None if value.logical_time is None else str(value.logical_time), causes=[dict(sequence=str(c.sequence), output=c.output) for c in value.causes])
        class Fixture(Model):
            def __init__(self, settings): super().__init__(); self.settings = settings
            def metadata(self): return Metadata(self.settings['name'], build=self.settings['build'])
            def step(self, state, input):
                self.calls.append('step')
                value = state + input
                return Transition(value + self.settings['state_delta'], (value + self.settings['output_delta'],))
            def check_state(self, state):
                self.calls.append('state')
                failed = self.settings['fail_at'] is not None and state >= self.settings['fail_at']
                return (Check('s', 'failed', 'at threshold') if failed else Check('s'),)
            def check_transition(self, *args): self.calls.append('transition'); return (Check('t'),)
        for row in corpus['recorder_cases']:
            with self.subTest(case=row['case']):
                model = Fixture(row['model']); expected = row['expected']
                try:
                    raw = row['limits']; extra = {k:int(v) for k,v in raw.items() if k != 'trace'}
                    limits = EvidenceLimits(trace=BinaryLimits(**{k:int(v) for k,v in raw.get('trace', {}).items()}), **extra)
                    raw = row['options']; options = RecorderOptions(raw['max_steps'], int(raw['max_retained_bytes']), BinaryLimits(**{k:int(v) for k,v in raw.get('limits', {}).items()}))
                    raw = row['provenance']; provenance = Provenance(raw['run'], int(raw['epoch']), raw['manifest'], raw['source'], raw['environment'])
                    raw = row['config']; config = RunConfig(raw['strategy'], None if raw['seed'] is None else int(raw['seed']), raw['parameters'])
                    initial = check_initial(model, row['initial_state'])
                    callbacks = model.calls[:]
                    recorder = EvidenceRecorder(initial, int(row['origin']), config, options, provenance, limits)
                    self.assertEqual(model.calls, callbacks)
                except Exception as error:
                    self.assertIsNotNone(expected['initial_error_contains'], str(error))
                    self.assertIn(expected['initial_error_contains'], str(error)); continue
                self.assertIsNone(expected['initial_error_contains'])
                self.assertEqual(len(row['actions']), len(expected['states']))
                for action, want in zip(row['actions'], expected['states']):
                    raw = action['transition']; transition = Transition(raw['state'], raw['outputs'], Disposition(**raw['disposition']))
                    raw = action['policy']; policy = CheckPolicy(int(raw['state_every']), raw['transition_checks'])
                    token = check_turn(model, action['before'], action['input'], transition, int(action['sequence']), policy)
                    model.encode_error = action.get('encode_error')
                    callbacks = model.calls[:]
                    error = None
                    try: recorder.observe(token, causal(action['causal']))
                    except Exception as caught: error = caught
                    self.assertEqual(model.calls, callbacks, 'evidence capture rechecked/executed')
                    if want['error_contains'] is None: self.assertIsNone(error, str(error))
                    else:
                        self.assertIsNotNone(error)
                        self.assertIn(want['error_contains'], str(error))
                    trace = recorder.snapshot(); trace.validate(limits)
                    actual = dict(checkpoint_after=str(trace.checkpoint_after), initial_state=int(trace.trace.initial_state), initial_checks=normalize_checks(trace.trace.initial_checks),
                        steps=[dict(input=int(s.input), post_state=int(s.post_state), outputs=[int(v) for v in s.outputs], checks=normalize_checks(s.checks)) for s in trace.trace.steps],
                        evidence_steps=[dict(sequence=str(s.sequence), state_check_count=s.state_check_count, causal=normalize_causal(s.causal)) for s in trace.steps],
                        observed_steps=str(recorder._recorder.observed_steps()), evicted_steps=str(recorder._recorder.evicted_steps()), frozen=recorder.is_frozen(), termination=trace.trace.termination)
                    self.assertEqual(actual, {k:v for k,v in want.items() if k != 'error_contains'})
                    model.encode_error = None
                    self.assertEqual(trace.replay(model, limits=limits).outcome, 'exact', 'retained coherent artifact must replay')

class ClosedRecorderMetadataTests(unittest.TestCase):
    def test_closed_type_subclass_getters_are_not_evaluated(self):
        from statelessness.evidence import Provenance, CausalContext, EvidenceLimits
        calls = []
        class HostileProvenance(Provenance):
            def __getattribute__(self, name):
                if name == 'run': calls.append(name); raise AssertionError('subclass getter')
                return super().__getattribute__(name)
        class HostileContext(CausalContext):
            def __getattribute__(self, name):
                if name == 'machine': calls.append(name); raise AssertionError('subclass getter')
                return super().__getattribute__(name)
        model = Model(); initial = check_initial(model, 0)
        with self.assertRaisesRegex(ModelError, 'expected Provenance'):
            EvidenceRecorder(initial, 0, RunConfig(), RecorderOptions(), HostileProvenance(), EvidenceLimits())
        recorder = EvidenceRecorder(initial, 0, RunConfig(), RecorderOptions(), Provenance(), EvidenceLimits())
        with self.assertRaisesRegex(ModelError, 'expected CausalContext'):
            recorder.observe(check_turn(model, 0, 1, Transition(1), 1), HostileContext())
        self.assertEqual(calls, [])
        self.assertEqual(recorder.snapshot().steps, ())

class ProvenanceAdmissionTests(unittest.TestCase):
    def test_known_unfit_environment_is_not_rewrapped_before_admission(self):
        from unittest.mock import patch
        from statelessness import EvidenceLimits, Provenance
        model = Model()
        token = check_initial(model, 0)
        value = Provenance(environment=tuple((str(i), 'v') for i in range(1000)))
        limits = EvidenceLimits(max_metadata_bytes=1_000_000, max_labels=0)
        with patch.object(Provenance, '__post_init__', side_effect=AssertionError('copied before quota')):
            with self.assertRaisesRegex(ModelError, 'environment label limit'):
                EvidenceRecorder(token, 0, RunConfig(), RecorderOptions(max_retained_bytes=2_000_000), value, limits)

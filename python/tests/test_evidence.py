"""Exact independent v2 envelopes, phases, exports and bounded metadata."""
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zlib
from statelessness import (Artifact, AuditTrace, BinaryLimits, CausalContext,
    Check, EvidenceLimits, ExportPolicy, Metadata, ModelError, OutputOrigin,
    Provenance, RunConfig, StepEvidence, TraceStep, TraceV2, Transition,
    diagnostic_json, export_diagnostic_new)
from test_trace_binary import fixture_trace
from test_replay_evidence_vectors import report


def limits(value):
    return EvidenceLimits(**{k: BinaryLimits(**{n:int(v) for n,v in x.items()}) if k == 'trace' else int(x) for k,x in value.items()})


def provenance(value):
    return Provenance(value['run'], int(value['epoch']), value['manifest'], value['source'], value['environment'])


def causal(value):
    return CausalContext(value['machine'], None if value['generation'] is None else int(value['generation']),
        value['correlation'], None if value['logical_time'] is None else int(value['logical_time']),
        [OutputOrigin(int(c['sequence']), c['output']) for c in value['causes']])


def evidence(value):
    return TraceV2(fixture_trace(value['trace']), int(value['checkpoint_after']), provenance(value['provenance']),
        [StepEvidence(int(s['sequence']), s['state_check_count'], causal(s['causal'])) for s in value['steps']])


class Fixture:
    def __init__(self, settings): self.settings, self.steps = settings, 0
    def event(self, name):
        if self.settings.get('error_at') == name: raise ModelError('fixture causal error')
    def metadata(self): return Metadata(self.settings['name'], build=self.settings['build'])
    def decode_state(self, value): self.event('decode_state'); return int(value)
    def decode_input(self, value): self.event('decode_input'); return int(value)
    def encode_state_into(self, value, out): self.event('encode_state'); out.append(str(value).encode())
    def encode_input_into(self, value, out): self.event('encode_input'); out.append(str(value).encode())
    def encode_output_into(self, value, out): self.event('encode_output'); out.append(str(value).encode())
    def step(self, before, input):
        self.event('step'); self.steps += 1
        return Transition(before + input + self.settings['state_delta'], (before + input + self.settings['output_delta'],))
    def check_state_into(self, state, sink):
        self.event('check_state')
        if not state or not self.settings['phase_shift']: sink.append(Check('s'))
    def check_transition_into(self, *args):
        self.event('check_transition')
        args[-1].extend((Check('s'),Check('t')) if self.settings['phase_shift'] else (Check('t'),))


class SharedEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get('STATELESS_CAUSAL_CORPUS') or Path(__file__).resolve().parents[2] / 'conformance/causal-evidence-v2.json'
        cls.corpus = json.loads(Path(path).read_text(encoding="utf-8"))
    def test_shared_wire_and_own_writer(self):
        for row in self.corpus['wire_cases']:
            with self.subTest(case=row['case']):
                config = limits(row.get('limits', {})); data = bytes.fromhex(row['bytes_hex'])
                if row['valid']:
                    result = Artifact.from_bytes(data, limits=config)
                    self.assertEqual(result.version, 1 if row['artifact_kind'] == 'v1' else 2)
                    expected = fixture_trace(row['trace']) if row['artifact_kind'] == 'v1' else evidence(row['evidence'])
                    self.assertEqual(result.value, expected)
                    self.assertEqual(Artifact.read(io.BytesIO(data), limits=config), result)
                    if result.version == 2: self.assertEqual(expected.to_bytes(limits=config), data)
                else:
                    with self.assertRaises(Exception) as caught: Artifact.from_bytes(data, limits=config)
                    self.assertIn(row['error_contains'], str(caught.exception))
                    if 'evidence' in row:
                        writer = io.BytesIO()
                        with self.assertRaises(Exception): evidence(row['evidence']).write(writer, limits=config)
                        self.assertEqual(writer.getvalue(), b'')
    def test_all_valid_prefixes_and_chunked_readers(self):
        for row in self.corpus['wire_cases']:
            if not row['valid']: continue
            data, config = bytes.fromhex(row['bytes_hex']), limits(row.get('limits', {}))
            for size in range(len(data)):
                with self.subTest(case=row['case'], prefix=size):
                    with self.assertRaises(Exception): Artifact.from_bytes(data[:size], limits=config)
            class Reader:
                def __init__(self, count): self.count, self.at = count, 0
                def read(self, requested):
                    amount = min(requested, self.count, len(data) - self.at)
                    value = data[self.at:self.at + amount]; self.at += amount
                    return value
            expected = Artifact.from_bytes(data, limits=config)
            for count in (1, 2, 7, 8192):
                with self.subTest(case=row['case'], chunk=count):
                    self.assertEqual(Artifact.read(Reader(count), limits=config), expected)
    def test_shared_replay_and_upgrade(self):
        for row in self.corpus['replay_cases']:
            with self.subTest(case=row['case']):
                value, model = evidence(row['evidence']), Fixture(row['model'])
                expected = row['expected']
                try:
                    if row['operation'] == 'replay': result = value.replay(model, limits=limits(row.get('limits', {})), allow_build_mismatch=row.get('allow_build_mismatch', False))
                    else:
                        upgraded = TraceV2.verify_upgrade(model, value.trace, value.provenance, value.checkpoint_after, limits=limits(row.get('limits', {})), allow_build_mismatch=row.get('allow_build_mismatch', False))
                        result = None
                        expected_steps = tuple(StepEvidence(int(s['sequence']), s['state_check_count'], causal(s['causal'])) for s in expected['steps'])
                        self.assertEqual(upgraded, replace(value, steps=expected_steps))
                        self.assertEqual(Artifact.from_bytes(upgraded.to_bytes()).value, upgraded)
                except Exception as error:
                    self.assertIsNotNone(expected['error_contains'], str(error))
                    self.assertIn(expected['error_contains'], str(error)); continue
                self.assertIsNone(expected['error_contains'])
                if result is not None: self.assertEqual(report(result), expected['report'])
    def test_shared_diagnostics(self):
        for row in self.corpus['export_cases']:
            with self.subTest(case=row['case']):
                value = evidence(row['evidence']) if row['artifact_kind'] == 'v2' else fixture_trace(row['evidence']['trace'])
                policy = ExportPolicy(**row['policy'])
                expected = row['expected']
                if expected['error_contains']:
                    with self.assertRaisesRegex(ModelError, expected['error_contains']): diagnostic_json(Artifact(value), policy)
                else: self.assertEqual(diagnostic_json(Artifact(value), policy).decode('utf8'), expected['json_text'])


def sample():
    from statelessness import Disposition
    trace = AuditTrace(Metadata('secret'), RunConfig(), b'0', (Check('s'),),
        (TraceStep(b'1', Disposition(), (b'1',), b'1', (Check('s'), Check('t'))),), 'completed')
    return TraceV2(trace, 0, Provenance('secret-run', 1, 'manifest', 'source', [('a', 'b')]), (StepEvidence(1, 1),))


class EvidenceNativeTests(unittest.TestCase):
    def test_every_prefix_and_trailing_checksum_length_tags(self):
        data = sample().to_bytes()
        for size in range(len(data)):
            with self.subTest(prefix=size):
                with self.assertRaises(Exception): Artifact.from_bytes(data[:size])
        with self.assertRaises(ModelError): Artifact.from_bytes(data + b'x')
        for offset in (8, 10, 12, 20, 28, len(data) - 1):
            corrupt = bytearray(data); corrupt[offset] ^= 0x80
            with self.assertRaises(Exception): Artifact.from_bytes(corrupt)

    def test_independent_metadata_decoder_budgets(self):
        from statelessness.evidence import _MetadataDecoder
        for size, maximum, expected in ((100, 10, 'decode byte limit'),):
            with self.assertRaisesRegex(ModelError, expected): _MetadataDecoder(memoryview(b'x' * size), maximum)
        reader = _MetadataDecoder(memoryview((100).to_bytes(4, 'little')), 10)
        with self.assertRaisesRegex(ModelError, 'decode element limit'): reader.text()
        reader = _MetadataDecoder(memoryview(b'\x00\x00\x00\x00'), 4); reader.allocation = 0
        with self.assertRaisesRegex(ModelError, 'allocation'): reader.collection(1, 120)
        reader = _MetadataDecoder(memoryview(b'\0'), 1); reader.work = 0
        with self.assertRaisesRegex(ModelError, 'work'): reader.integer(1)
        reader = _MetadataDecoder(memoryview(b'\0'), 1); reader.depth = 16
        with self.assertRaisesRegex(ModelError, 'nesting'): reader.integer(1)

    def test_writer_preflight_no_publication_and_exact_envelope_budget(self):
        value = sample(); data = value.to_bytes()
        exact = EvidenceLimits(trace=BinaryLimits(max_total_bytes=len(data)))
        self.assertEqual(value.to_bytes(limits=exact), data)
        writer = io.BytesIO()
        with self.assertRaisesRegex(ModelError, 'total byte'):
            value.write(writer, limits=replace(exact, trace=replace(exact.trace, max_total_bytes=len(data)-1)))
        self.assertEqual(writer.getvalue(), b'')
        metadata = len(data) - 32 - len(__import__('statelessness').audit_to_bytes(value.trace))
        self.assertEqual(value.to_bytes(limits=EvidenceLimits(max_metadata_bytes=metadata)), data)
        with self.assertRaises(ModelError): value.to_bytes(limits=EvidenceLimits(max_metadata_bytes=metadata-1))

    def test_owned_collections_and_label_subclasses_do_not_rewrite_evidence(self):
        class Spoof(str):
            def __eq__(self, other): return True
            def __iter__(self): return iter('fake')
            def encode(self, *args): return b'fake'
        environment = [[Spoof('key'), Spoof('value')]]
        p = Provenance(Spoof('run'), environment=environment)
        environment[0][0] = 'changed'; environment.append(['new', 'x'])
        value = replace(sample(), provenance=p)
        decoded = Artifact.from_bytes(value.to_bytes()).value
        self.assertEqual(decoded.provenance, Provenance('run', environment=[('key','value')]))
        with self.assertRaisesRegex(ModelError, 'duplicate'):
            replace(value, provenance=Provenance(environment=[(Spoof('key'),'a'),('key','b')])).validate()
        with self.assertRaisesRegex(ModelError, 'label byte'):
            value.to_bytes(limits=EvidenceLimits(max_label_bytes=2))

    def test_phases_and_legacy_unknown_are_positional(self):
        value = sample()
        self.assertEqual([value.check_phase(0,0), value.check_phase(1,0), value.check_phase(1,1)], ['initial_state','state','transition'])
        self.assertIsNone(value.check_phase(1,2)); self.assertIsNone(value.check_phase(-1,0))
        self.assertIsNone(Artifact(value.trace).check_phase(1,0))
        self.assertEqual(Artifact(value.trace).check_phase(0,0), 'initial_state')

    def test_stream_short_writes_and_reader_ownership(self):
        class Writer:
            def __init__(self): self.data = bytearray()
            def write(self, value): self.data.extend(value[:2]); return min(2,len(value))
        writer = Writer(); sample().write(writer)
        reader = io.BytesIO(writer.data)
        self.assertEqual(Artifact.read(reader).value, sample())
        self.assertFalse(reader.closed)
        self.assertEqual(bytes(writer.data), sample().to_bytes())

    def test_new_file_exclusive_permissions_fsync_and_incomplete_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'evidence.bin'
            sample().write_raw_new(path)
            self.assertEqual(path.read_bytes(), sample().to_bytes())
            if os.name == 'posix': self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(ModelError): sample().write_raw_new(path)
            original = path.read_bytes()
            with self.assertRaises(ModelError): export_diagnostic_new(Artifact(sample()), path)
            self.assertEqual(path.read_bytes(), original)
            broken = Path(folder) / 'incomplete.json'
            with patch('statelessness.evidence.os.fsync', side_effect=OSError('sync failed')):
                with self.assertRaisesRegex(ModelError, 'incomplete newly created evidence file'):
                    export_diagnostic_new(Artifact(sample()), broken)
            self.assertTrue(broken.exists())
            with self.assertRaises(ModelError):
                export_diagnostic_new(Artifact(sample()), Path(folder) / 'never-created', ExportPolicy(max_bytes=0))
            self.assertFalse((Path(folder) / 'never-created').exists())


class EvidenceIntegrityTests(unittest.TestCase):
    def test_closed_schema_subclass_getters_never_run(self):
        touched = []
        class Hostile(Provenance):
            def __getattribute__(self, key):
                touched.append(key)
                raise AssertionError('unsupported schema getter')
        value = replace(sample(), provenance=object.__new__(Hostile))
        with self.assertRaisesRegex(ModelError, 'Provenance'): value.validate()
        self.assertEqual(touched, [])
        class HostileStep(StepEvidence):
            def __getattribute__(self, key):
                touched.append(key)
                raise AssertionError('unsupported step getter')
        with self.assertRaisesRegex(ModelError, 'StepEvidence'):
            replace(sample(), steps=(object.__new__(HostileStep),)).validate()
        self.assertEqual(touched, [])

    def test_label_budget_precedes_encoding_or_canonical_copy(self):
        import tracemalloc
        class Label(str):
            def encode(self, *args): raise AssertionError('override encoder')
            def __str__(self): raise AssertionError('override str')
            def __iter__(self): raise AssertionError('override iterator')
        value = replace(sample(), provenance=Provenance(Label('x' * (8 * 1024 * 1024))))
        tracemalloc.start()
        try:
            with self.assertRaisesRegex(ModelError, 'label byte limit'):
                value.to_bytes(limits=EvidenceLimits(max_label_bytes=1))
            _, peak = tracemalloc.get_traced_memory()
        finally: tracemalloc.stop()
        self.assertLess(peak, 200_000)

    def test_reader_failure_not_retried_and_no_ownership_transfer(self):
        class Reader:
            calls = 0
            def read(self, count): self.calls += 1; raise InterruptedError('interrupted reader')
            def close(self): raise AssertionError('caller reader remains owned')
        reader = Reader()
        with self.assertRaisesRegex(ModelError, 'interrupted reader'): Artifact.read(reader)
        self.assertEqual(reader.calls, 1)

    def test_reader_cannot_return_more_than_requested(self):
        class OverRead:
            def __init__(self): self.calls = 0
            def read(self, count):
                self.calls += 1
                return b'x' * (count + 1)
        reader = OverRead()
        with self.assertRaisesRegex(ModelError, 'more bytes than requested'):
            Artifact.read(reader)
        self.assertEqual(reader.calls, 1)

    def test_allowlist_uses_actual_string_content_and_details_never_escape(self):
        class Spoof(str):
            def __eq__(self, other): return True
        value = sample()
        result = diagnostic_json(Artifact(value), ExportPolicy(property_ids=(Spoof('different'),))).decode()
        self.assertNotIn('secret', result)
        self.assertTrue(all(c['id'] is None for o in json.loads(result)['observations'] for c in o['checks']))

class ClosedPolicyTests(unittest.TestCase):
    def test_budget_and_export_policy_subclass_getters_not_invoked(self):
        touched = []
        class Limits(EvidenceLimits):
            def __getattribute__(self, name): touched.append(name); raise AssertionError('getter')
        class Policy(ExportPolicy):
            def __getattribute__(self, name): touched.append(name); raise AssertionError('getter')
        with self.assertRaisesRegex(ModelError, 'EvidenceLimits'):
            Artifact.from_bytes(b'', limits=object.__new__(Limits))
        with self.assertRaisesRegex(ModelError, 'ExportPolicy'):
            diagnostic_json(Artifact(sample()), object.__new__(Policy))
        self.assertEqual(touched, [])

class EvidencePrecedenceTests(unittest.TestCase):
    def test_validation_does_not_copy_step_prefix(self):
        class NoSlice(tuple):
            def __getitem__(self, key):
                if isinstance(key, slice):
                    raise AssertionError('validation copied the step prefix')
                return super().__getitem__(key)

        value = sample()
        object.__setattr__(value.trace, 'steps', NoSlice(value.trace.steps))
        value.validate()

    def test_semantic_failure_precedes_trace_quota(self):
        value = sample()
        failed = replace(value.trace.steps[0], checks=(Check('s', 'failed', 'bad'), Check('t')))
        trace = replace(value.trace, steps=(failed, value.trace.steps[0]), termination='property_failed')
        value = replace(value, trace=trace, steps=(StepEvidence(1,1),StepEvidence(2,1)))
        with self.assertRaisesRegex(ModelError, 'trace continues after property failure'):
            value.validate(EvidenceLimits(trace=BinaryLimits(max_steps=0)))

class EvidenceSyncOrderTests(unittest.TestCase):
    def test_complete_new_file_reaches_descriptor_before_fsync(self):
        value = sample()
        expected = value.to_bytes()
        original_fsync = os.fsync
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'synced.bin'
            sync_calls = []
            def sync(fd):
                sync_calls.append(fd)
                self.assertEqual(path.read_bytes(), expected)
                original_fsync(fd)
            with patch('statelessness.evidence.os.fsync', side_effect=sync):
                value.write_raw_new(path)
            self.assertEqual(len(sync_calls), 1)

class EvidenceMetaclassTests(unittest.TestCase):
    def test_type_membership_never_dispatches_metaclass_equality_or_iterators(self):
        callbacks = []
        class Pretend(type):
            def __eq__(self, other): callbacks.append('equality'); return True
        class Evil(metaclass=Pretend):
            def __iter__(self): callbacks.append('iterator'); return iter(())
            def __len__(self): callbacks.append('length'); return 0
        for operation in (lambda: Artifact(Evil()), lambda: Artifact.from_bytes(Evil()),
                          lambda: CausalContext(causes=Evil()),
                          lambda: Provenance(environment=[Evil()]),
                          lambda: ExportPolicy(property_ids=Evil())):
            with self.assertRaises((ModelError, ValueError)): operation()
        self.assertEqual(callbacks, [])

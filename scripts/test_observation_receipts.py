"""Receipt/validator regressions. Execute through Kyoto; no engine-generated goldens."""
from __future__ import annotations

import copy
import importlib.util
import marshal
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('observation_receipts', ROOT / 'scripts/check-native-observation-consumers.py')
q = importlib.util.module_from_spec(_spec)
exec(compile(Path(_spec.origin).read_bytes(), _spec.origin, 'exec'), q.__dict__)


def literal_fixture():
    # Hand-authored protocol bytes, not output captured from any implementation.
    record = b'Info stream="s" event=message sequence=None message="ok"\n'
    return dict(schema='statelessness.observation.v1', cases=[dict(
        id='literal-message', stream='s', options=dict(level='Info', max_record_bytes=len(record)),
        payloads=dict(kind='none'), events=[dict(kind='message', level='Info', sequence=None, message='ok')],
        expected=dict(record_hex=record.hex(), record_text=record.decode(), callbacks=[],
                      error_stage=None, sink_poisoned=False))])


class ExactContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = literal_fixture()
        self.actual = q.expected_result(self.fixture, 'python')

    def test_exact_contract_and_all_negative_controls(self):
        self.assertEqual(q.validate_result(self.fixture, 'python', self.actual), 1)
        controls = q.negative_controls(self.fixture, self.actual, 'python')
        self.assertEqual(len(controls), 10)
        self.assertEqual(len(controls), len(set(controls)))
        self.assertIn('independent-record-byte-bound', controls)
        self.assertEqual(self.fixture, literal_fixture(), 'controls must not mutate sealed fixtures')

    def test_committed_fixture_is_nonempty_and_controls_reject(self):
        fixture = q.strict_json((ROOT / q.FIXTURE).read_bytes())
        for language in q.LANGUAGES:
            # Validator-only data, explicitly not a language-execution receipt.
            expected = q.expected_result(fixture, language)
            self.assertEqual(q.validate_result(fixture, language, expected), len(fixture['cases']))
            self.assertEqual(len(q.negative_controls(fixture, expected, language)), 10)

    def test_callbacks_are_ordered_and_not_set_normalized(self):
        fixture = copy.deepcopy(self.fixture)
        fixture['cases'][0]['expected']['callbacks'] = ['format_input:1', 'format_output:2', 'format_input:1']
        actual = q.expected_result(fixture, 'python')
        actual['cases'][0]['callbacks'] = ['format_input:1', 'format_input:1', 'format_output:2']
        with self.assertRaises(ValueError):
            q.validate_result(fixture, 'python', actual)

    def test_utf8_bytes_and_newline_are_not_normalized(self):
        for mutation in ('00', '0d0a', 'ff'):
            actual = copy.deepcopy(self.actual)
            actual['cases'][0]['record_hex'] = actual['cases'][0]['record_hex'][:-2] + mutation
            with self.assertRaises(ValueError):
                q.validate_result(self.fixture, 'python', actual)

    def test_duplicate_json_keys_nonfinite_and_invalid_utf8_fail(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":{"b":0,"b":1}}', b'{"a":NaN}',
                    b'{"a":Infinity}', b'"\xff"', b'{}\n{}'):
            with self.subTest(raw=raw), self.assertRaises((ValueError, UnicodeError)):
                q.strict_json(raw)

    def test_boolean_is_not_numeric_sink_health(self):
        actual = copy.deepcopy(self.actual); actual['cases'][0]['sink_poisoned'] = 0
        with self.assertRaises(ValueError):
            q.validate_result(self.fixture, 'python', actual)

    def test_missing_duplicate_extra_reordered_cases_and_wrong_language_fail(self):
        fixture = copy.deepcopy(self.fixture)
        second = copy.deepcopy(fixture['cases'][0]); second['id'] = 'second'
        fixture['cases'].append(second)
        valid = q.expected_result(fixture, 'python')
        invalid = []
        a = copy.deepcopy(valid); a['cases'].reverse(); invalid.append(a)
        a = copy.deepcopy(valid); a['cases'][1] = copy.deepcopy(a['cases'][0]); invalid.append(a)
        a = copy.deepcopy(valid); del a['cases'][0]['callbacks']; invalid.append(a)
        a = copy.deepcopy(valid); a['cases'][0]['claimed_pass'] = True; invalid.append(a)
        a = copy.deepcopy(valid); a['language'] = 'rust'; invalid.append(a)
        for actual in invalid:
            with self.assertRaises(ValueError):
                q.validate_result(fixture, 'python', actual)

    def test_explanatory_text_cannot_silently_disagree(self):
        fixture = copy.deepcopy(self.fixture)
        fixture['cases'][0]['expected']['record_text'] += 'changed'
        with self.assertRaises(ValueError):
            q.validate_fixture(fixture)

    def test_bound_uses_utf8_bytes_and_is_inclusive(self):
        fixture = copy.deepcopy(self.fixture)
        expected = fixture['cases'][0]['expected']
        expected['record_text'] = 'é\n'; expected['record_hex'] = 'c3a90a'
        fixture['cases'][0]['options']['max_record_bytes'] = 3
        q.validate_fixture(fixture)
        fixture['cases'][0]['options']['max_record_bytes'] = 2
        with self.assertRaises(ValueError):
            q.validate_fixture(fixture)

    def test_empty_fixture_and_noncanonical_hex_are_rejected(self):
        with self.assertRaises(ValueError):
            q.validate_fixture(dict(schema='statelessness.observation.v1', cases=[]))
        for value in ('A0', 'a', 'aa bb', 'gg'):
            actual = copy.deepcopy(self.actual); actual['cases'][0]['record_hex'] = value
            with self.assertRaises(ValueError):
                q.validate_result(self.fixture, 'python', actual)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.raw_hash_attempts = []
        self.temp = tempfile.TemporaryDirectory(prefix='observation-receipt-test-')
        self.root = Path(self.temp.name)
        q.git(self.root, 'init', '-q')
        (self.root / 'src').mkdir()
        (self.root / 'src/input.bin').write_bytes(b'\x00input\xff\n')
        (self.root / '.gitignore').write_text('/target/\n*.hidden\n__pycache__/\n')
        (self.root / 'src/input.bin').chmod(0o644); (self.root / '.gitignore').chmod(0o644)
        (self.root / 'conformance').mkdir()
        (self.root / q.FIXTURE).write_bytes(q.canonical(literal_fixture()))
        (self.root / q.FIXTURE).chmod(0o644)
        q.git(self.root, 'add', 'src/input.bin', '.gitignore', str(q.FIXTURE))
        tree = q.git(self.root, 'write-tree').strip()
        # A synthetic Git object in an isolated fixture repository; no parent
        # checkout commits, branch moves, staging, or configured hooks are used.
        commit = b'tree ' + tree + b'\nauthor Test <test@example.invalid> 1 +0000\ncommitter Test <test@example.invalid> 1 +0000\n\nfixture\n'
        self.commit = q.git(self.root, 'hash-object', '-t', 'commit', '-w', '--stdin', input=commit).decode().strip()
        q.git(self.root, 'update-ref', 'HEAD', self.commit)
        _, self.expected = q.committed_snapshot(self.root, self.commit)

    def tearDown(self):
        self.temp.cleanup()

    def test_binary_committed_content_matches_without_text_loss(self):
        q.assert_sources(q.source_snapshot(self.root), self.expected)
        self.assertEqual(self.expected['src/input.bin']['sha256'], q.sha(b'\x00input\xff\n'))

    def test_addition_missing_mode_and_content_changes_reject(self):
        path = self.root / 'src/input.bin'
        for operation in ('extra', 'missing', 'mode', 'content'):
            with self.subTest(operation=operation):
                if operation == 'extra': (self.root / 'extra').write_bytes(b'new')
                elif operation == 'missing': path.unlink()
                elif operation == 'mode': path.chmod(0o755)
                else: path.write_bytes(b'changed')
                with self.assertRaises(ValueError):
                    q.assert_sources(q.source_snapshot(self.root), self.expected)
                if operation == 'extra': (self.root / 'extra').unlink()
                path.write_bytes(b'\x00input\xff\n'); path.chmod(0o644)

    def test_ignored_source_addition_is_not_a_build_exclusion(self):
        (self.root / 'src/secret.hidden').write_bytes(b'consumed')
        with self.assertRaises(ValueError):
            q.assert_sources(q.source_snapshot(self.root), self.expected)

    def test_build_script_source_root_includes_even_ignored_cache_descendants(self):
        path = self.root / 'src/__pycache__/consumed.bin'
        path.parent.mkdir(); path.write_bytes(b'build.rs hashes this')
        with self.assertRaises(ValueError):
            q.assert_sources(q.source_snapshot(self.root), self.expected)

    def test_only_receipt_family_and_ignored_build_output_are_excluded(self):
        (self.root / 'target').mkdir(); (self.root / 'target/generated').write_bytes(b'build')
        receipt = self.root / q.RECEIPTS / 'attempt/raw.bin'
        receipt.parent.mkdir(parents=True); receipt.write_bytes(b'log')
        q.assert_sources(q.source_snapshot(self.root), self.expected)
        historical = self.root / 'validation/native-parity/another-slice/input.json'
        historical.parent.mkdir(parents=True); historical.write_bytes(b'{}')
        with self.assertRaises(ValueError):
            q.assert_sources(q.source_snapshot(self.root), self.expected)

    def test_type_swap_is_not_hidden_by_identical_target_content(self):
        path = self.root / 'src/input.bin'; path.unlink()
        elsewhere = self.root / 'target'; elsewhere.mkdir()
        (elsewhere / 'same').write_bytes(b'\x00input\xff\n')
        path.symlink_to('../target/same')
        with self.assertRaises(ValueError):
            q.assert_sources(q.source_snapshot(self.root), self.expected)

    def test_manifests_are_content_addressed_and_existing_bytes_checked(self):
        first = q.manifest(self.root, self.expected)
        self.assertEqual(first, q.manifest(self.root, self.expected))
        (self.root / first['path']).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            q.manifest(self.root, self.expected)

    def attempt(self, name, commit=None):
        attempt = q.Attempt(self.root, self.root / q.RECEIPTS / name, commit or self.commit, 'native')
        self.addCleanup(shutil.rmtree, attempt.work)
        return attempt

    def test_real_attempt_rejects_reused_directory(self):
        attempt = self.attempt('exclusive')
        with self.assertRaises(FileExistsError):
            q.Attempt(self.root, attempt.output, self.commit, 'native')

    def advance_head(self):
        tree = q.git(self.root, 'write-tree').strip()
        raw = (b'tree ' + tree + b'\nparent ' + self.commit.encode() +
               b'\nauthor Test <test@example.invalid> 2 +0000\ncommitter Test <test@example.invalid> 2 +0000\n\nadvance\n')
        commit = q.git(self.root, 'hash-object', '-t', 'commit', '-w', '--stdin', input=raw).decode().strip()
        q.git(self.root, 'update-ref', 'HEAD', commit)
        return commit

    def test_receipt_only_head_advance_retains_original_source_pin(self):
        attempt = self.attempt('receipt-head'); attempt.seal()
        path = self.root / q.RECEIPTS / 'retained/evidence.json'
        q.write_new(path, b'{}\n')
        q.git(self.root, 'add', str(path.relative_to(self.root)))
        head = self.advance_head()
        self.assertEqual(attempt.finish(), 0)
        self.assertEqual(attempt.report['expected_commit'], self.commit)
        self.assertEqual(attempt.report['head_after'], head)
        self.assertEqual(attempt.report['source_before'], attempt.report['source_after'])
        after = self.attempt('after-receipt-head'); after.seal()
        self.assertEqual(after.report['head_before'], head)
        self.assertEqual(after.finish(), 0)

    def test_committed_head_source_drift_rejects_even_with_pinned_worktree_bytes(self):
        path = self.root / 'src/input.bin'
        path.write_bytes(b'committed different source')
        q.git(self.root, 'add', 'src/input.bin'); self.advance_head()
        path.write_bytes(b'\x00input\xff\n')
        q.assert_sources(q.source_snapshot(self.root), self.expected)
        attempt = self.attempt('head-source-drift')
        with self.assertRaisesRegex(ValueError, 'source tree differs'):
            attempt.seal()

    def test_invalid_commit_is_not_accepted_as_a_source_pin(self):
        attempt = self.attempt('unknown-pin', '0' * 40)
        with self.assertRaises(RuntimeError):
            attempt.seal()

    def test_seal_rejects_uncommitted_content_and_finish_retains_drift(self):
        attempt = self.attempt('content-drift')
        attempt.seal()
        (self.root / 'src/input.bin').write_bytes(b'changed after sealing')
        self.assertEqual(attempt.finish(), 1)
        receipt = q.strict_json((attempt.output / 'receipt.json').read_bytes())
        self.assertEqual(receipt['status'], 'failed')
        self.assertNotEqual(receipt['source_before'], receipt['source_after'])
        second = self.attempt('uncommitted')
        with self.assertRaisesRegex(ValueError, 'source tree differs'):
            second.seal()

    def test_finish_success_and_watched_file_replacement(self):
        good = self.attempt('unchanged'); good.seal()
        self.assertEqual(good.finish(), 0)
        bad = self.attempt('watched-replacement'); bad.seal()
        executable = bad.work / 'runner'; executable.write_bytes(b'original')
        bad.watch('runner', executable); executable.write_bytes(b'replaced')
        self.assertEqual(bad.finish(), 1)
        receipt = q.strict_json((bad.output / 'receipt.json').read_bytes())
        self.assertIn('input changed', receipt['error'])

    def test_staged_source_mutation_fails_before_any_compiler_runs(self):
        attempt = self.attempt('staged-source-mutation'); attempt.seal()
        dest = attempt.work / 'staged-input'
        q.stage(attempt, 'src/input.bin', dest)
        dest.write_bytes(b'changed by build hook')
        with self.assertRaisesRegex(ValueError, 'source tree differs'):
            attempt.run('must-not-run', [sys.executable, '-c', 'raise AssertionError("ran")'])
        self.assertEqual(attempt.report['commands'], [])
        self.assertEqual(attempt.finish(), 1)

    def test_added_go_or_swift_source_fails_before_any_compiler_runs(self):
        for name in ('injected.go', 'injected.swift'):
            with self.subTest(name=name):
                attempt = self.attempt('added-' + name); attempt.seal()
                dest = attempt.work / 'staged-src'; q.stage(attempt, 'src', dest)
                (dest / name).write_bytes(b'unregistered compiler input')
                with self.assertRaisesRegex(ValueError, 'source tree differs'):
                    attempt.run('must-not-run', [sys.executable, '-c', 'raise AssertionError("ran")'])
                self.assertEqual(attempt.report['commands'], [])

    def test_suffix_build_exclusion_applies_only_to_the_installed_consumer(self):
        output = 'suffix-build/suffix-consumer.js'
        self.assertTrue(q.staged_build_output('examples/native-observation/typescript', output))
        for prefix in ('typescript', 'go', 'swift', 'examples/native-checked/typescript'):
            self.assertFalse(q.staged_build_output(prefix, output))
        self.assertFalse(q.staged_build_output('swift', 'Sources/__pycache__/injected.swift'))

    def test_generated_registration_is_explicit_watched_and_cannot_rebaseline(self):
        attempt = self.attempt('generated-registration'); attempt.seal()
        dest = attempt.work / 'staged-src'; q.stage(attempt, 'src', dest)
        config = dest / 'go.mod'; config.write_bytes(b'module authored\n')
        with self.assertRaises(ValueError):
            q.assert_sources(attempt.staged_snapshot(), attempt.staged)
        attempt.register_generated(config, 'module')
        attempt.register_generated(config, 'module')
        q.assert_sources(attempt.staged_snapshot(), attempt.staged)
        self.assertIn('generated:module', attempt.inputs)
        config.write_bytes(b'module changed\n')
        with self.assertRaisesRegex(ValueError, 'cannot rebaseline'):
            attempt.register_generated(config, 'module')
        with self.assertRaises(ValueError):
            attempt.check_inputs()

    def test_generated_label_cannot_move_and_source_cannot_be_reregistered(self):
        attempt = self.attempt('generated-label'); attempt.seal()
        source = attempt.work / 'committed'; q.stage(attempt, 'src/input.bin', source)
        with self.assertRaisesRegex(ValueError, 'cannot rebaseline'):
            attempt.register_generated(source, 'source')
        one, two = attempt.work / 'one', attempt.work / 'two'
        one.write_bytes(b'one'); two.write_bytes(b'two')
        attempt.register_generated(one, 'unique')
        with self.assertRaises(ValueError):
            attempt.register_generated(two, 'unique')

    def test_native_staging_supplies_public_consumer_and_fixture_to_python_suite(self):
        qualifier = q.source_module('qualification_staging_test', ROOT / 'scripts/qualify-native-observation.py')
        for name in ('python/src/pkg.py', 'python/tests/test_dummy.py', 'go/go.mod',
                     'typescript/src/index.ts', 'typescript/test/check.test.mjs',
                     'swift/Tests/fixture.swift', 'spec/contract.md',
                     'docs/native-parity/derive-model-draft/cases.json',
                     'examples/native-observation/python/consumer.py',
                     'scripts/test_observation_receipts.py', 'Package.swift'):
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        attempt = self.attempt('stage-contract')
        attempt.initial = q.source_snapshot(self.root)
        qualifier.stage_native_sources(attempt)
        consumer = attempt.work / 'examples/native-observation/python/consumer.py'
        self.assertEqual(consumer.read_bytes(), b'examples/native-observation/python/consumer.py')
        self.assertEqual((attempt.work / q.FIXTURE).read_bytes(), (self.root / q.FIXTURE).read_bytes())

    def journal(self, base, name, code=0, stable=True):
        """Literal capture metadata; no executable or language implementation runs."""
        directory = base / 'commands' / ('0001-' + name); directory.mkdir(parents=True)
        executable = dict(path='/synthetic/already-removed-go-test', file={'sha256': 'a' * 64})
        started = dict(name=name, argv=[executable['path']], cwd='/synthetic', started_ns=1,
                       executable_before=executable, environment_overlay={}, environment={'GODEBUG': 'panicnil=0'})
        q.write_new(directory / 'started.json', q.canonical(started))
        finished = dict(started, exit_code=code, finished_ns=2, raw_logs_stable=stable,
                        process_tree_termination_confirmed=stable,
                        executable_after=executable, executable_unchanged=True)
        for stream, raw in (('stdout', b'\x00synthetic-out\xff\n'), ('stderr', b'\xfeerr\x00')):
            path = directory / (stream + '.bin'); q.write_new(path, raw)
            finished[stream] = dict(path=str(path.relative_to(base)))
            if stable:
                finished[stream].update(q.identity(path))
        q.write_new(directory / 'finished.json', q.canonical(finished))
        return finished

    def nested_capture(self, attempt, name, code=0, stable=True):
        base = attempt.output / 'go-executables' / name
        entry = self.journal(base, 'test-executable', code, stable)
        receipt = dict(before=entry['executable_before'], after=entry['executable_after'],
                       commands=[entry], status='failed' if code else 'passed', error=None, exit_code=code)
        q.write_new(base / 'receipt.json', q.canonical(receipt))
        return base, entry, receipt

    def capture_attempt(self, name, stable=True):
        attempt = self.attempt(name); attempt.seal()
        attempt.report['commands'] = [self.journal(attempt.output, 'go-full-suite', stable=stable)]
        return attempt

    def no_raw_hash(self, path):
        if path.name in ('stdout.bin', 'stderr.bin'):
            self.raw_hash_attempts.append(str(path))
            raise AssertionError('attempted to hash unreconciled raw log: ' + str(path))
        return self.original_identity(path)

    def test_nested_stable_success_and_nonzero_failure_retain_lossless_hashes(self):
        for code in (0, 7):
            with self.subTest(code=code):
                attempt = self.capture_attempt('nested-code-' + str(code))
                base, _, _ = self.nested_capture(attempt, 'child', code=code)
                self.assertEqual(attempt.finish(), 0 if code == 0 else 1)
                logs = q.strict_json((self.root / attempt.report['raw_logs']['path']).read_bytes())
                name = str((base / 'commands/0001-test-executable/stdout.bin').relative_to(attempt.output))
                self.assertEqual(logs[name]['sha256'], q.sha(b'\x00synthetic-out\xff\n'))
                self.assertEqual(len(logs), 4)

    def test_nested_unstable_or_missing_stability_never_hashes_even_with_stable_outer(self):
        for flag in ('false', 'missing', 'termination-missing'):
            with self.subTest(flag=flag):
                attempt = self.capture_attempt('nested-unstable-' + flag)
                base, entry, receipt = self.nested_capture(attempt, 'child', stable=False)
                if flag == 'missing':
                    del entry['raw_logs_stable']
                elif flag == 'termination-missing':
                    entry['raw_logs_stable'] = True
                    del entry['process_tree_termination_confirmed']
                (base / 'commands/0001-test-executable/finished.json').write_bytes(q.canonical(entry))
                (base / 'receipt.json').write_bytes(q.canonical(receipt))
                self.original_identity = q.identity
                with patch.object(q, 'identity', side_effect=self.no_raw_hash):
                    self.assertEqual(attempt.finish(), 1)
                self.assertEqual(self.raw_hash_attempts, [])
                self.assertNotIn('raw_logs', attempt.report)
                self.assertEqual(len(attempt.report['raw_log_paths']), 4)
                self.assertIn('confirmed descendant termination', attempt.report['log_collection_error'])

    def test_interrupted_nested_publication_retains_paths_without_hashing(self):
        for missing in ('finished-and-receipt', 'receipt', 'stderr-finished-receipt'):
            with self.subTest(missing=missing):
                attempt = self.capture_attempt('nested-partial-' + missing)
                base, _, _ = self.nested_capture(attempt, 'child')
                (base / 'receipt.json').unlink()
                if 'finished' in missing:
                    (base / 'commands/0001-test-executable/finished.json').unlink()
                if missing.startswith('stderr'):
                    (base / 'commands/0001-test-executable/stderr.bin').unlink()
                self.original_identity = q.identity
                with patch.object(q, 'identity', side_effect=self.no_raw_hash):
                    self.assertEqual(attempt.finish(), 1)
                self.assertEqual(self.raw_hash_attempts, [])
                self.assertNotIn('raw_logs', attempt.report)
                self.assertEqual(len(attempt.report['raw_log_paths']), 3 if missing.startswith('stderr') else 4)

    def test_nested_journal_status_and_raw_byte_disagreements_are_rejected(self):
        for mutation in ('start', 'finished', 'status', 'raw', 'orphan'):
            with self.subTest(mutation=mutation):
                attempt = self.capture_attempt('nested-mismatch-' + mutation)
                base, entry, receipt = self.nested_capture(attempt, 'child')
                directory = base / 'commands/0001-test-executable'
                if mutation == 'start':
                    start = q.strict_json((directory / 'started.json').read_bytes()); start['cwd'] = '/changed'
                    (directory / 'started.json').write_bytes(q.canonical(start))
                elif mutation == 'finished':
                    finished = dict(entry, exit_code=7)
                    (directory / 'finished.json').write_bytes(q.canonical(finished))
                elif mutation == 'status':
                    receipt['status'] = 'failed'
                    (base / 'receipt.json').write_bytes(q.canonical(receipt))
                elif mutation == 'raw':
                    (directory / 'stdout.bin').write_bytes(b'changed after journaling')
                else:
                    q.write_new(attempt.output / 'orphan/stdout.bin', b'unowned')
                self.assertEqual(attempt.finish(), 1)
                self.assertNotIn('raw_logs', attempt.report)

    def test_nested_inventory_is_complete_before_first_failure_and_preserves_original_error(self):
        for outer_stable in (True, False):
            with self.subTest(outer_stable=outer_stable):
                attempt = self.capture_attempt('nested-mixed-' + str(outer_stable), stable=outer_stable)
                self.nested_capture(attempt, 'a-complete')
                self.nested_capture(attempt, 'b-uncertain', stable=False)
                base, _, _ = self.nested_capture(attempt, 'c-partial')
                (base / 'receipt.json').unlink()
                (attempt.output / 'go-executables/d-empty').mkdir()
                self.original_identity = q.identity
                with patch.object(q, 'identity', side_effect=self.no_raw_hash):
                    self.assertEqual(attempt.finish(RuntimeError('original timeout')), 1)
                self.assertEqual(self.raw_hash_attempts, [])
                self.assertEqual(attempt.report['error'], "RuntimeError('original timeout')")
                self.assertEqual(len(attempt.report['raw_log_paths']), 8)
                self.assertEqual(len(attempt.report['nested_go_paths']), 4)
                self.assertNotIn('raw_logs', attempt.report)


class PythonBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='observation-bootstrap-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'scripts').mkdir()
        self.qualifier = q.source_module('runner_bootstrap_test', ROOT / 'scripts/qualify-native-observation.py')

    def poison(self, path, kind, code="origin = 'cache'\n"):
        """Literal synthetic bytecode, never generated from an engine's output."""
        if kind == 'timestamp':
            header = struct.pack('<III', 0, int(path.stat().st_mtime), path.stat().st_size)
        else:
            header = struct.pack('<I', 1) + b'\0' * 8  # Valid unchecked-hash cache.
        cache = Path(importlib.util.cache_from_source(str(path)))
        cache.parent.mkdir(exist_ok=True)
        cache.write_bytes(importlib.util.MAGIC_NUMBER + header +
                          marshal.dumps(compile(code, str(path), 'exec')))

    def test_helpers_ignore_valid_timestamp_and_unchecked_hash_caches(self):
        source = self.root / 'scripts/probe.py'
        source.write_text("origin = 'source'\n")
        for kind in ('timestamp', 'unchecked-hash'):
            with self.subTest(kind=kind):
                self.poison(source, kind)
                # Sensitivity control: the normal loader really accepts this cache.
                spec = importlib.util.spec_from_file_location('cached_probe', source)
                cached = importlib.util.module_from_spec(spec); spec.loader.exec_module(cached)
                self.assertEqual(cached.origin, 'cache')
                with patch.object(q, 'ROOT', self.root):
                    self.assertEqual(q.helper('probe').origin, 'source')

    def test_both_cli_bootstraps_ignore_helper_caches_and_script_directory_shadowing(self):
        for name in ('qualify-native-observation.py', 'check-native-observation-consumers.py'):
            shutil.copy2(ROOT / 'scripts' / name, self.root / 'scripts' / name)
        (self.root / 'scripts/argparse.py').write_text("raise AssertionError('script import shadow')\n")
        for kind in ('timestamp', 'unchecked-hash'):
            for name in ('qualify-native-observation.py', 'check-native-observation-consumers.py'):
                self.poison(self.root / 'scripts' / name, kind, "raise AssertionError('poisoned driver cache')\n")
            for name in ('qualify-native-observation.py', 'check-native-observation-consumers.py'):
                with self.subTest(kind=kind, entry=name):
                    result = subprocess.run([sys.executable, '-I', '-B', self.root / 'scripts' / name, '--help'],
                        cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(b'--expected-commit', result.stdout)

    def test_cli_requires_both_isolated_and_no_bytecode_flags_before_other_imports(self):
        (self.root / 'scripts/argparse.py').write_text("raise AssertionError('shadow loaded')\n")
        for name in ('qualify-native-observation.py', 'check-native-observation-consumers.py'):
            path = self.root / 'scripts' / name; shutil.copy2(ROOT / 'scripts' / name, path)
            for flags in ([], ['-B'], ['-I']):
                with self.subTest(entry=name, flags=flags):
                    result = subprocess.run([sys.executable, *flags, path, '--help'], cwd=self.root,
                        env=dict(os.environ, PYTHONDONTWRITEBYTECODE=''),
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(b'invoke with python3 -I -B', result.stderr)
                    self.assertNotIn(b'shadow loaded', result.stderr)

    def runner(self, body='self.assertEqual(probe.value, 73)'):
        attempt = q.Attempt.__new__(q.Attempt)
        attempt.root = self.root; attempt.work = self.root / 'work'; attempt.work.mkdir()
        attempt.output = self.root / 'output'; attempt.output.mkdir()
        attempt.env = q.clean_environment(attempt.work); attempt.timeout = 10
        attempt.inputs = {}; attempt.staged = {}; attempt.staged_roots = {}; attempt.report = {'commands': []}
        files = {'src/package/probe.py': b'value = 73\n',
                 'src/tests/test_probe.py': ('import unittest,probe\nclass Probe(unittest.TestCase):\n'
                     ' def test_sealed_import(self):\n  ' + body + '\n').encode()}
        for name, raw in files.items():
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        attempt.initial = {name: q.identity(self.root / name) for name in files}
        q.stage(attempt, 'src', attempt.work / 'sealed')
        runner = self.qualifier.python_test_runner(attempt, 'probe', attempt.work / 'sealed/tests',
                                                   (attempt.work / 'sealed/package',))
        attempt.initial = None  # This fixture tests staged import identity, not Git sealing.
        summary = attempt.output / 'summary.json'
        return attempt, runner, summary

    def test_isolated_generated_runner_ignores_workspace_and_pythonpath_shadow_modules(self):
        attempt, runner, summary = self.runner()
        poison = "raise AssertionError('unsealed import location')\n"
        for name in ('unittest.py', 'json.py', 'probe.py'):
            (attempt.work / name).write_text(poison)
        attempt.run('runner', [sys.executable, '-I', '-B', runner, summary],
                    overlay={'PYTHONPATH': str(attempt.work)})
        result = q.strict_json(summary.read_bytes())
        self.assertEqual(result['tests_run'], 1)
        self.assertEqual(result['discovered_names'], ['test_probe.Probe.test_sealed_import'])
        self.assertEqual(result['errors'], [])

    def test_generated_runner_membership_addition_rejects_before_start(self):
        attempt, runner, summary = self.runner()
        (runner.parent / 'unittest.py').write_text('raise AssertionError("shadow")\n')
        with patch.object(q.subprocess, 'Popen', side_effect=AssertionError('must not launch')):
            with self.assertRaisesRegex(ValueError, 'input changed'):
                attempt.run('runner', [sys.executable, '-I', '-B', runner, summary])
        self.assertFalse(summary.exists())

    def test_generated_runner_membership_change_rejects_before_interpreting_summary(self):
        path = self.root / 'work/python-runners/probe/unittest.py'
        attempt, runner, summary = self.runner('__import__("pathlib").Path(' + repr(str(path)) +
                                               ').write_text("# injected after start\\n")')
        with self.assertRaises(ValueError):
            attempt.run('runner', [sys.executable, '-I', '-B', runner, summary])
        self.assertEqual(q.strict_json(summary.read_bytes())['tests_run'], 1,
                         'even a successful reported test cannot hide runner membership drift')

    def test_runner_rejects_unregistered_test_or_source_directories(self):
        attempt, _, _ = self.runner()
        outside = attempt.work / 'unregistered'; outside.mkdir()
        with self.assertRaisesRegex(ValueError, 'not sealed staged source'):
            self.qualifier.python_test_runner(attempt, 'unsealed', outside)


class InstalledWatchTests(unittest.TestCase):
    def test_installed_and_built_outputs_are_watched_before_first_consumer(self):
        # Exercise installed()'s actual ordering. Tool invocations are replaced
        # by literal build outputs; no package implementation or compiler runs.
        cases = (
            ('python-installed-location', 'venv/lib/statelessness/__init__.py'),
            ('node-version', 'go-consumer-bin'),
            ('typescript-pack', 'typescript-package/dist/index.js'),
            ('typescript-strict-declarations', 'typescript-consumer/node_modules/@jagalite/statelessness/dist/index.d.ts'),
            ('typescript-browser-import-graph', 'typescript-consumer/node_modules/@jagalite/statelessness/dist/index.js'),
            ('typescript-browser-import-graph', 'typescript-consumer/build/consumer.js'),
            ('browser-first-consumption', 'typescript-consumer/build/consumer.js'),
            ('authority-first-consumption', 'swift-consumer/.build/debug/ObservationConsumer'),
        )
        class BoundaryReached(Exception):
            pass
        for target, relative in cases:
            for mutate in (False, True):
                with self.subTest(target=target, relative=relative, mutate=mutate), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp).resolve() / 'checkout'; root.mkdir()
                    attempt = q.Attempt.__new__(q.Attempt)
                    attempt.root = root; attempt.work = root.parent / 'work'; attempt.work.mkdir()
                    attempt.output = root.parent / 'output'; attempt.output.mkdir()
                    attempt.inputs = {}; attempt.staged = {}; attempt.staged_roots = {}; attempt.report = {}
                    sources = {'python/src/statelessness/__init__.py': b'# source\n',
                        'examples/native-observation/python/consumer.py': b'# consumer\n',
                        'go/go.mod': b'module synthetic\n', 'examples/native-observation/go/main.go': b'// source\n',
                        'typescript/package.json': b'{}\n', 'typescript/src/index.ts': b'// source\n',
                        'examples/native-observation/typescript/consumer.ts': b'// consumer\n',
                        'Package.swift': b'// package\n', 'swift/Sources/Stub.swift': b'// source\n',
                        'examples/native-observation/swift/Package.swift': b'// consumer package\n',
                        str(q.FIXTURE): q.canonical(literal_fixture())}
                    for name, raw in sources.items():
                        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
                    attempt.initial = {name: q.identity(root / name) for name in sources}
                    rust = root.parent / 'rust'; rust.write_bytes(b'inert compiler artifact')
                    binaries = {'native-observation': q.executable_identity(rust)}
                    def produce(name, raw=b'inert generated output'):
                        path = attempt.work / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
                    def run(name, argv, cwd=None, **kwargs):
                        if name == target and mutate:
                            path = attempt.work / relative
                            self.assertTrue(path.is_file(), 'control must mutate an already produced output')
                            path.write_bytes(b'persistent mutation before first consumer')
                        attempt.check_inputs()
                        q.assert_sources(attempt.staged_snapshot(), attempt.staged)
                        if name == target:
                            raise BoundaryReached(name)
                        if name == 'python-builder-tools':
                            produce('builder/lib/tool.py')
                        elif name == 'python-wheel':
                            with zipfile.ZipFile(attempt.output / 'packages/synthetic.whl', 'w') as archive:
                                archive.writestr('synthetic.dist-info/METADATA', 'Name: synthetic\n')
                        elif name == 'python-install':
                            produce('venv/lib/statelessness/__init__.py')
                        elif name == 'go-module-graph':
                            modules = [dict(Path='observation-consumer', Main=True),
                                dict(Path='github.com/Jagalite/statelessness/go', Version='v0.0.0',
                                     Replace=dict(Dir=str(attempt.work / 'go-package')))]
                            return b''.join(q.canonical(m) for m in modules), b'', {}
                        elif name == 'go-build':
                            produce('go-consumer-bin')
                        elif name == 'typescript-package-build':
                            produce('typescript-package/dist/index.js')
                        elif name == 'typescript-pack':
                            (attempt.output / 'packages/synthetic.tgz').write_bytes(b'inert tarball')
                        elif name == 'typescript-install':
                            produce('typescript-consumer/node_modules/@jagalite/statelessness/dist/index.js')
                            produce('typescript-consumer/node_modules/@jagalite/statelessness/dist/index.d.ts')
                        elif name == 'typescript-strict-declarations':
                            produce('typescript-consumer/build/consumer.js')
                        elif name == 'swift-package-dependencies':
                            return b'{"dependencies":[]}', b'', {}
                        elif name == 'swift-build':
                            produce('swift-consumer/.build/debug/ObservationConsumer')
                        elif name == 'swift-bin-path':
                            return str(attempt.work / 'swift-consumer/.build/debug').encode(), b'', {}
                        return b'', b'', {}
                    attempt.run = run
                    authority = q.SimpleNamespace(qualify=lambda *args: run('authority-first-consumption', []))
                    browser = q.SimpleNamespace(qualify=lambda *args: run('browser-first-consumption', []))
                    helpers = {'observation_authority': authority, 'observation_browser': browser}
                    with (patch.object(q, 'load_compile', return_value=binaries),
                          patch.object(q, 'require_tool', side_effect=lambda _, name: name),
                          patch.object(q, 'typescript_compiler', return_value=['node', str(attempt.work / 'tools/bin/tsc')]),
                          patch.object(q, 'helper', side_effect=helpers.__getitem__)):
                        if mutate:
                            with self.assertRaisesRegex(ValueError, 'input changed'):
                                q.installed(attempt, literal_fixture(), rust, root / 'unused-receipt')
                        else:
                            with self.assertRaises(BoundaryReached):
                                q.installed(attempt, literal_fixture(), rust, root / 'unused-receipt')


class RustToolSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='observation-tool-selection-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.brew = root / 'homebrew'; self.brew.mkdir()
        self.proxies = root / 'cargo-bin'; self.proxies.mkdir()
        for name in ('cargo', 'rustc', 'rustdoc'):
            path = self.brew / name
            path.write_bytes(b'INERT DIFFERENT DIRECT COMPILER\n'); path.chmod(0o755)
        for name in ('rustup', 'cargo', 'rustc', 'rustdoc'):
            path = self.proxies / name
            path.write_bytes(b'INERT MATCHING RUSTUP PROXY\n'); path.chmod(0o755)
        self.original_path = os.pathsep.join(map(str, (self.brew, self.proxies)))
        self.attempt = q.SimpleNamespace(env={'PATH': self.original_path})
        path = ROOT / 'scripts/qualify-native-observation.py'
        spec = importlib.util.spec_from_file_location('observation_tool_selection', path)
        self.qualifier = importlib.util.module_from_spec(spec)
        exec(compile(path.read_bytes(), str(path), 'exec'), self.qualifier.__dict__)

    def test_homebrew_path_precedence_cannot_mix_invoked_and_resolved_tools(self):
        self.assertEqual(shutil.which('cargo', path=self.original_path), str(self.brew / 'cargo'))
        self.qualifier.select_rustup_proxies(self.attempt)
        for name in ('cargo', 'rustc', 'rustdoc', 'rustup'):
            self.assertEqual(shutil.which(name, path=self.attempt.env['PATH']), str(self.proxies / name))
        self.assertEqual(self.attempt.env['PATH'].split(os.pathsep)[1:], self.original_path.split(os.pathsep))

    def test_missing_or_unrelated_proxy_rejects_before_changing_environment(self):
        for name in ('cargo', 'rustc', 'rustdoc'):
            for mutation in ('missing', 'different', 'nonexecutable'):
                with self.subTest(tool=name, mutation=mutation):
                    path = self.proxies / name
                    if mutation == 'missing': path.unlink()
                    elif mutation == 'different': path.write_bytes(b'OTHER TOOL\n')
                    else: path.chmod(0o644)
                    with self.assertRaisesRegex(ValueError, 'matching rustup proxy'):
                        self.qualifier.select_rustup_proxies(self.attempt)
                    self.assertEqual(self.attempt.env['PATH'], self.original_path)
                    path.write_bytes(b'INERT MATCHING RUSTUP PROXY\n'); path.chmod(0o755)

    def test_missing_rustup_does_not_fall_back_to_unbound_direct_compiler(self):
        self.attempt.env['PATH'] = str(self.brew)
        with self.assertRaisesRegex(ValueError, 'rustup is required'):
            self.qualifier.select_rustup_proxies(self.attempt)
        self.assertEqual(self.attempt.env['PATH'], str(self.brew))


class RawCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='observation-command-test-')
        self.root = Path(self.temp.name)
        self.attempt = q.Attempt.__new__(q.Attempt)
        self.attempt.root = self.root; self.attempt.output = self.root / 'attempt'
        self.attempt.output.mkdir(); self.attempt.work = self.root
        self.attempt.env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        self.attempt.timeout = 10; self.attempt.initial = None
        self.attempt.report = {'commands': []}

    def tearDown(self):
        self.temp.cleanup()

    def retained(self, index, stream):
        entry = self.attempt.report['commands'][index]
        info = entry[stream]
        path = self.attempt.output / info['path']
        self.assertEqual(q.identity(path), {k: v for k, v in info.items() if k != 'path'})
        return path.read_bytes()

    def test_nonzero_exit_preserves_invalid_utf8_nul_and_both_streams(self):
        code = "import os,sys; os.write(1,b'\\xff\\x00out\\n'); os.write(2,b'\\xfeerr\\x00'); sys.exit(7)"
        with self.assertRaises(RuntimeError):
            self.attempt.run('failed', [sys.executable, '-c', code])
        self.assertEqual(self.retained(0, 'stdout'), b'\xff\x00out\n')
        self.assertEqual(self.retained(0, 'stderr'), b'\xfeerr\x00')
        self.assertEqual(self.attempt.report['commands'][0]['exit_code'], 7)

    def test_timeout_preserves_complete_already_written_bytes_and_hashes(self):
        code = "import os,time; os.write(1,b'\\xffbegin\\x00'); os.write(2,b'\\xfebegin\\n'); time.sleep(60)"
        with self.assertRaises(subprocess.TimeoutExpired):
            self.attempt.run('timeout', [sys.executable, '-c', code], timeout=2)
        self.assertEqual(self.retained(0, 'stdout'), b'\xffbegin\x00')
        self.assertEqual(self.retained(0, 'stderr'), b'\xfebegin\n')
        self.assertTrue(self.attempt.report['commands'][0]['timed_out'])

    def test_repeated_labels_never_overwrite_prior_logs(self):
        for value in ('one', 'two'):
            self.attempt.run('same-label', [sys.executable, '-c', 'print(' + repr(value) + ')'])
        self.assertEqual(self.retained(0, 'stdout'), b'one\n')
        self.assertEqual(self.retained(1, 'stdout'), b'two\n')
        self.assertNotEqual(self.attempt.report['commands'][0]['stdout']['path'],
                            self.attempt.report['commands'][1]['stdout']['path'])

    def test_new_output_is_exclusive(self):
        path = self.root / 'immutable.bin'
        q.write_new(path, b'first')
        with self.assertRaises(FileExistsError):
            q.write_new(path, b'second')
        self.assertEqual(path.read_bytes(), b'first')

    def test_inherited_panicnil_one_is_overridden_and_recorded(self):
        with patch.dict(os.environ, {'GODEBUG': 'panicnil=1,gctrace=1'}):
            self.attempt.env = q.clean_environment(self.root)
        raw, _, entry = self.attempt.run('go-environment-control', [sys.executable, '-I', '-B', '-c',
            'import os; print(os.environ["GODEBUG"])'])
        self.assertEqual(raw, b'panicnil=0\n')
        self.assertEqual(entry['environment']['GODEBUG'], 'panicnil=0')
        self.assertEqual(self.attempt.env['GODEBUG'], 'panicnil=0')


if __name__ == '__main__':
    unittest.main()

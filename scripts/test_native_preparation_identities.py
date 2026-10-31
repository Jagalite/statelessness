"""Mutation controls for prepared hosts, compiler and XCTest proof; run through Kyoto."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import os
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'native_preparation', Path(__file__).parent / 'native-parity/n12-wire/prepare.py')
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)
INTEGRATION_SPEC = importlib.util.spec_from_file_location(
    'native_integration_seal', Path(__file__).parent / 'qualify-native-integration.py')
INTEGRATION = importlib.util.module_from_spec(INTEGRATION_SPEC)
INTEGRATION_SPEC.loader.exec_module(INTEGRATION)


class PreparationIdentities(unittest.TestCase):
    def test_internal_venv_directory_alias_binds_link_and_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            package, output, executable = root / 'venv', root / 'receipts', root / 'host'
            (package / 'lib').mkdir(parents=True)
            output.mkdir()
            executable.write_bytes(b'host')
            imported = package / 'lib/module.py'
            imported.write_bytes(b'original import')
            alias = package / 'lib64'
            alias.symlink_to('lib', target_is_directory=True)
            guard = PREPARE.PreparedHostArtifacts(output, {})
            guard.bind('python', {'argv': [str(executable)], 'artifacts': [str(package)]})
            baseline = guard.before['python']
            self.assertEqual(baseline[str(alias)]['target'], 'lib')
            self.assertEqual(baseline[str(alias)]['resolved_directory'], str(package / 'lib'))
            self.assertEqual(baseline[str(imported)]['sha256'], hashlib.sha256(b'original import').hexdigest())
            self.assertNotIn(str(alias / 'module.py'), baseline)
            self.assertTrue(guard.check('unchanged'))
            imported.write_bytes(b'changed import')
            self.assertFalse(guard.check('changed-destination'))
            imported.write_bytes(b'original import')
            alias.unlink()
            alias.symlink_to('./lib', target_is_directory=True)
            self.assertFalse(guard.check('changed-link-spelling'))
            self.assertFalse(guard.records['python']['last_matches'])
            alias.unlink()
            alias.symlink_to('lib', target_is_directory=True)
            self.assertFalse(guard.check('restored-sticky-failure'))
            self.assertTrue(guard.records['python']['last_matches'])

    def test_installed_directory_aliases_fail_closed(self):
        for kind in ('absolute-escape', 'relative-escape', 'indirect',
                     'indirect-parent', 'escape-and-return', 'ancestor', 'broken', 'self-cycle', 'pair-cycle'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                package, outside, executable = root / 'venv', root / 'outside', root / 'host'
                (package / 'lib').mkdir(parents=True)
                outside.mkdir()
                (outside / 'untrusted.py').write_bytes(b'never read')
                executable.write_bytes(b'host')
                alias = package / 'lib64'
                target = {'absolute-escape': str(outside), 'relative-escape': '../outside',
                          'indirect': 'indirect', 'indirect-parent': 'indirect/child',
                          'escape-and-return': '../venv/lib', 'ancestor': '.', 'broken': 'missing',
                          'self-cycle': 'lib64', 'pair-cycle': 'other'}[kind]
                if kind in ('indirect', 'indirect-parent'):
                    (package / 'lib/child').mkdir()
                    (package / 'indirect').symlink_to('lib', target_is_directory=True)
                if kind == 'pair-cycle':
                    (package / 'other').symlink_to('lib64', target_is_directory=True)
                alias.symlink_to(target, target_is_directory=True)
                with mock.patch.object(PREPARE.wire, 'file_identity', wraps=PREPARE.wire.file_identity) as identity:
                    with self.assertRaisesRegex(RuntimeError, 'symlink'):
                        PREPARE.wire.artifact_snapshot({'argv': [str(executable)], 'artifacts': [str(package)]})
                    self.assertNotIn(mock.call(outside / 'untrusted.py'), identity.call_args_list)

    def test_directory_alias_retarget_and_mode_changes_invalidate_binding(self):
        for mutation in ('retarget', 'mode', 'broken', 'escape', 'cycle'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                package, output, executable = root / 'venv', root / 'receipts', root / 'host'
                (package / 'lib').mkdir(parents=True)
                (package / 'other').mkdir()
                output.mkdir()
                executable.write_bytes(b'host')
                alias = package / 'lib64'
                alias.symlink_to('lib', target_is_directory=True)
                guard = PREPARE.PreparedHostArtifacts(output, {})
                guard.bind('python', {'argv': [str(executable)], 'artifacts': [str(package)]})
                if mutation == 'mode':
                    (package / 'lib').chmod(0o700)
                else:
                    alias.unlink()
                    alias.symlink_to({'retarget': 'other', 'broken': 'missing',
                                      'escape': '..', 'cycle': 'lib64'}[mutation], target_is_directory=True)
                self.assertFalse(guard.check('mutated-' + mutation))
                self.assertFalse(guard.records['python']['last_matches'])
                self.assertEqual(guard.records['python']['failures'][0]['first_stage'], 'mutated-' + mutation)

    def test_staged_go_build_disables_ancestor_vcs_and_preserves_source_guards(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, output, scratch = root / 'source', root / 'receipts', root / 'artifacts'
            output.mkdir()
            for name, contents in {
                    'conformance/fixture.tsv': 'authored corpus',
                    'docs/native-parity/derive-model-draft/fixture.json': '{}',
                    'scripts/native-parity/n12-wire/go/go.mod': 'module fixture\n',
                    'go/go.mod': 'module package\n',
                    'go/core.go': '// original source',
            }.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents)
            launched = []

            def launch(argv, **kwargs):
                launched.append(argv)
                if argv[:2] == ['go', 'build']:
                    Path(argv[argv.index('-o') + 1]).write_bytes(b'fixture executable')
                elif argv[:2] == ['go', 'test']:
                    (scratch / 'packages/go/core.go').write_text('// changed source')
                    kwargs['stdout'].write(b'github.com/Jagalite/statelessness/go/debug')
                else:
                    self.assertEqual(argv[:2], ['go', 'version'])
                return mock.Mock(wait=mock.Mock(return_value=0))

            with mock.patch.object(PREPARE, 'ROOT', source), \
                    mock.patch.object(PREPARE.wire, 'source_snapshot', side_effect=lambda: PREPARE.wire.tree_snapshot(source)), \
                    mock.patch.object(PREPARE.subprocess, 'check_output', return_value=b'fixture-source\n'), \
                    mock.patch.object(PREPARE.subprocess, 'Popen', side_effect=launch), \
                    mock.patch.dict(os.environ, {'CARGO_HOME': str(root / 'unused-cargo')}):
                self.assertEqual(PREPARE.prepare(SimpleNamespace(artifacts=scratch, output=output)), 1)
            builds = [argv for argv in launched if argv[:2] == ['go', 'build']]
            self.assertEqual(len(builds), 1)
            self.assertEqual(builds[0].count('-buildvcs=false'), 1)
            report = json.loads((output / 'preparation.json').read_text())
            self.assertFalse(report['build_input_copies_stable'])
            self.assertFalse(report['host_artifacts_stable'])
            self.assertTrue(report['source_stable'])
            copies = json.loads((output / 'build-input-copies.json').read_text())
            package, = [row for row in copies if row['path'] == str(scratch / 'packages/go')]
            self.assertEqual(package['unexpected_changes'], ['core.go'])

    def test_first_host_replacement_during_later_preparation_is_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source, output, scratch = root / 'source', root / 'receipts', root / 'artifacts'
            output.mkdir()
            # Only the first two consumer inputs are needed: the replacement
            # must fail during Go preparation, before later hosts even exist.
            for name, contents in {
                    'conformance/fixture.tsv': 'authored corpus',
                    'docs/native-parity/derive-model-draft/fixture.json': '{}',
                    'scripts/native-parity/n12-wire/rust/Cargo.toml': 'path = "../../../.."\n',
                    'scripts/native-parity/n12-wire/go/go.mod': 'module fixture\n',
                    'src/lib.rs': '// root source',
                    'crates/statelessness-debug/src/lib.rs': '// debugger source',
            }.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents)
            binary = scratch / 'builds/rust/debug/n12-wire-rust'
            original, replacement = b'first built host; never executed', b'replaced during later preparation'
            launched = []

            def launch(argv, **_kwargs):
                launched.append(argv[:2])
                if argv[:2] == ['cargo', 'build']:
                    binary.parent.mkdir(parents=True)
                    binary.write_bytes(original)
                    binary.chmod(0o755)
                elif argv[:2] == ['go', 'version']:
                    baseline = json.loads((output / 'artifacts-prepared.json').read_text())
                    self.assertEqual(baseline['rust'][str(binary)]['sha256'], hashlib.sha256(original).hexdigest())
                    binary.write_bytes(replacement)
                else:
                    self.assertEqual(argv[:2], ['rustc', '--version'])
                return mock.Mock(wait=mock.Mock(return_value=0))

            with mock.patch.object(PREPARE, 'ROOT', source), \
                    mock.patch.object(PREPARE.wire, 'source_snapshot', side_effect=lambda: PREPARE.wire.tree_snapshot(source)), \
                    mock.patch.object(PREPARE.subprocess, 'check_output', return_value=b'fixture-source\n'), \
                    mock.patch.object(PREPARE.subprocess, 'Popen', side_effect=launch), \
                    mock.patch.dict(os.environ, {'CARGO_HOME': str(root / 'unused-cargo')}):
                result = PREPARE.prepare(SimpleNamespace(artifacts=scratch, output=output))
            self.assertEqual(launched, [['rustc', '--version'], ['cargo', 'build'], ['go', 'version']])
            self.assertEqual(result, 1)
            report = json.loads((output / 'preparation.json').read_text())
            self.assertFalse(report['passed'])
            self.assertFalse(report['host_artifacts_stable'])
            self.assertIn('host-artifacts-identity', report['failures'])
            record = json.loads((output / 'host-artifact-bindings.json').read_text())['rust']
            self.assertEqual(record['failures'][0]['first_stage'], 'after:02-go-version')
            failed = json.loads((output / record['failures'][0]['evidence']['snapshot']).read_text())
            self.assertEqual(failed[str(binary)]['sha256'], hashlib.sha256(replacement).hexdigest())
            baseline = json.loads((output / 'artifacts-prepared.json').read_text())
            self.assertEqual(baseline['rust'][str(binary)]['sha256'], hashlib.sha256(original).hexdigest())
            receipt = json.loads((output / '02-go-version/receipt.json').read_text())
            self.assertEqual(receipt['exit_code'], 0)
            self.assertFalse(receipt['passed'])

    def test_host_mutate_detect_restore_recollect_is_sticky_and_compact(self):
        for mutation in ('executable', 'import', 'add', 'remove'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                output, package, binary = root / 'receipts', root / 'package', root / 'host'
                output.mkdir()
                package.mkdir()
                binary.write_bytes(b'original executable')
                imported, added = package / 'imported.py', package / 'added.py'
                imported.write_bytes(b'original imported source')
                guard = PREPARE.PreparedHostArtifacts(output, {})
                guard.bind('fixture', {'argv': [str(binary)], 'artifacts': [str(package)]})
                baseline = (output / 'artifacts-prepared.json').read_bytes()
                self.assertTrue(guard.check('unchanged'))
                if mutation == 'executable':
                    binary.write_bytes(b'replaced executable')
                elif mutation == 'import':
                    imported.write_bytes(b'replaced imported source')
                elif mutation == 'add':
                    added.write_bytes(b'new imported source')
                else:
                    imported.unlink()
                offending = PREPARE.wire.artifact_snapshot(guard.hosts['fixture'])
                self.assertFalse(guard.check('detected'))
                self.assertFalse(guard.check('repeated-failure-collection'))
                binary.write_bytes(b'original executable')
                imported.write_bytes(b'original imported source')
                if added.exists():
                    added.unlink()
                self.assertFalse(guard.check('restored-failure-collection'))
                with self.assertRaisesRegex(RuntimeError, 'artifact identity changed'):
                    guard.require('later-command')
                record = json.loads((output / 'host-artifact-bindings.json').read_text())['fixture']
                self.assertTrue(record['last_matches'])
                self.assertFalse(record['stable'])
                self.assertEqual(len(record['failures']), 1)
                self.assertEqual(record['failures'][0]['observations'], 2)
                self.assertEqual(record['failures'][0]['first_stage'], 'detected')
                path = output / record['failures'][0]['evidence']['snapshot']
                self.assertEqual(json.loads(path.read_text()), offending)
                self.assertEqual(path.stem, hashlib.sha256(path.read_bytes()).hexdigest())
                self.assertEqual(len(list((output / 'host-artifact-snapshots').glob('*.json'))), 1)
                self.assertEqual((output / 'artifacts-prepared.json').read_bytes(), baseline)

    def test_host_snapshot_exception_survives_restoration_and_recollection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            binary = root / 'host'
            binary.write_bytes(b'original executable')
            output = root / 'receipts'
            output.mkdir()
            guard = PREPARE.PreparedHostArtifacts(output, {})
            guard.bind('fixture', {'argv': [str(binary)], 'artifacts': [str(binary)]})
            baseline = (output / 'artifacts-prepared.json').read_bytes()
            binary.unlink()
            self.assertFalse(guard.check('missing-executable'))
            binary.write_bytes(b'original executable')
            self.assertFalse(guard.check('restored-failure-collection'))
            record = json.loads((output / 'host-artifact-bindings.json').read_text())['fixture']
            self.assertTrue(record['last_matches'])
            self.assertFalse(record['stable'])
            self.assertEqual(record['failures'][0]['evidence']['error']['type'], 'FileNotFoundError')
            self.assertEqual(record['failures'][0]['first_stage'], 'missing-executable')
            self.assertEqual((output / 'artifacts-prepared.json').read_bytes(), baseline)
            with self.assertRaisesRegex(RuntimeError, 'baseline already established'):
                guard.bind('fixture', guard.hosts['fixture'])

    def test_failed_host_binding_cannot_be_rebased_after_repair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            binary, output = root / 'host', root / 'receipts'
            output.mkdir()
            guard = PREPARE.PreparedHostArtifacts(output, {})
            host = {'argv': [str(binary)], 'artifacts': [str(binary)]}
            with self.assertRaises(FileNotFoundError):
                guard.bind('fixture', host)
            binary.write_bytes(b'late replacement executable')
            self.assertFalse(guard.check('repaired-failure-collection'))
            with self.assertRaisesRegex(RuntimeError, 'baseline already established'):
                guard.bind('fixture', host)
            record = json.loads((output / 'host-artifact-bindings.json').read_text())['fixture']
            self.assertIsNone(record['baseline'])
            self.assertFalse(record['stable'])
            self.assertEqual(record['failures'][0]['first_stage'], 'bind')
            self.assertEqual(record['failures'][0]['evidence']['error']['type'], 'FileNotFoundError')
            self.assertEqual(json.loads((output / 'artifacts-prepared.json').read_text()), {})

    def test_rewritten_source_or_tool_manifest_cannot_rebase_queued_pin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label in ('source', 'tools'):
                with self.subTest(label=label):
                    value = {label: {'sha256': 'original-input'}}
                    raw = (json.dumps(value, sort_keys=True) + '\n').encode()
                    path = root / (hashlib.sha256(raw).hexdigest() + '.json')
                    path.write_bytes(raw)
                    self.assertEqual(INTEGRATION.read_sealed_manifest(path), value)
                    path.write_text(json.dumps({label: {'sha256': 'replacement-input'}}))
                    with self.assertRaisesRegex(RuntimeError, 'queued SHA-256 filename'):
                        INTEGRATION.read_sealed_manifest(path)

    def test_generated_tsc_launcher_cannot_replace_manifested_compiler(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compiler = root / 'node_modules/typescript'
            (compiler / 'bin').mkdir(parents=True)
            # Python stands in for the identified interpreter; no actual TS
            # compiler is needed to check which executable source gets used.
            (compiler / 'bin/tsc').write_text("print('manifested compiler')\n")
            launcher = root / 'node_modules/.bin/tsc'
            launcher.parent.mkdir()
            launcher.write_text("#!/bin/sh\nprintf 'unmanifested replacement'\nexit 9\n")
            launcher.chmod(0o755)
            env = dict(os.environ, PATH=str(launcher.parent) + os.pathsep + os.environ.get('PATH', ''))
            result = subprocess.run(PREPARE.compiler_command(compiler, sys.executable),
                                    cwd=root, env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b'manifested compiler\n')

    def test_private_compiler_mutation_and_additions_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('package.json', 'bin/tsc', 'lib/_tsc.js'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('original ' + name)
            expected = PREPARE.compiler_snapshot(root)
            PREPARE.require_snapshot_equal(expected, PREPARE.compiler_snapshot(root), 'compiler')
            (root / 'lib/_tsc.js').write_text('different compiler implementation')
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                PREPARE.require_snapshot_equal(expected, PREPARE.compiler_snapshot(root), 'compiler')
            (root / 'lib/_tsc.js').write_text('original lib/_tsc.js')
            (root / 'lib/injected.js').write_text('new compiler input')
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                PREPARE.require_snapshot_equal(expected, PREPARE.compiler_snapshot(root), 'compiler')

    def test_missing_or_replaced_xctest_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, 'executable identity missing'):
                PREPARE.swift_test_snapshot(root)
            executable = root / 'Suite.xctest/Contents/MacOS/Suite'
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b'executable fixture; never run')
            executable.chmod(0o755)
            expected = PREPARE.swift_test_snapshot(root)
            PREPARE.require_snapshot_equal(expected, PREPARE.swift_test_snapshot(root), 'XCTest')
            executable.write_bytes(b'replaced executable')
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                PREPARE.require_snapshot_equal(expected, PREPARE.swift_test_snapshot(root), 'XCTest')
            executable.unlink()
            with self.assertRaisesRegex(RuntimeError, 'executable identity missing'):
                PREPARE.swift_test_snapshot(root)

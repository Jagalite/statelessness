"""Negative installed-consumer provenance controls. Execute only through Kyoto."""
import copy
from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P = load('consumer_provenance_test', 'native_consumer_provenance.py')
RESEARCH = load('research_consumer_test', 'check-native-research-consumers.py')
MODEL = load('model_consumer_test', 'check-native-model-consumers.py')


class Files(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def write(self, name, content, executable=False):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        if executable: path.chmod(0o755)
        return path


class PreparedCompiler(Files):
    def setUp(self):
        super().setUp()
        self.node = self.write('tools/node', 'node tool', True)
        self.directory = self.root / 'prepared/packages/typescript/node_modules/typescript'
        self.manifest = self.root / 'prepared/compiler-manifest.json'
        package = {'name': 'candidate', 'devDependencies': {'typescript': '5.8.3'}}
        lock = {'packages': {'node_modules/typescript': {'version': '5.8.3', 'integrity': 'sha512-pinned-fixture'}}}
        for prefix in ('typescript', 'prepared/packages/typescript'):
            self.write(prefix + '/package.json', json.dumps(package))
            self.write(prefix + '/package-lock.json', json.dumps(lock))
        for name, content in {'package.json': json.dumps({'name': 'typescript', 'version': '5.8.3'}),
                              'bin/tsc': 'entry', 'lib/tsc.js': 'loader', 'lib/_tsc.js': 'compiler',
                              'lib/lib.es2022.d.ts': 'declarations'}.items():
            self.write(str(self.directory.relative_to(self.root) / name), content)
        self.seal()

    def seal(self):
        self.manifest.write_text(json.dumps(P.compiler_tree(self.directory)))
        self.manifest_sha256 = hashlib.sha256(self.manifest.read_bytes()).hexdigest()

    def select(self):
        evidence = {}
        with mock.patch.object(P.shutil, 'which', return_value=str(self.node)):
            command = P.select_typescript(self.root, self.directory, self.manifest, self.manifest_sha256, evidence)
        return command, evidence

    def test_explicit_private_compiler_works_without_checkout_dependencies_or_global_tsc(self):
        self.assertFalse((self.root / 'typescript/node_modules').exists())
        with mock.patch.object(P.shutil, 'which', side_effect=lambda name: str(self.node) if name == 'node' else None) as lookup:
            evidence = {}
            command = P.select_typescript(self.root, self.directory, self.manifest, self.manifest_sha256, evidence)
        self.assertEqual(command, [str(self.node), str(self.directory / 'bin/tsc')])
        self.assertEqual(lookup.call_args_list, [mock.call('node')])
        P.require_typescript_version('Version 5.8.3\n', evidence)
        self.assertTrue(evidence['stable'])

    def test_mutated_compiler_is_rejected_against_preparation_before_launch(self):
        (self.directory / 'lib/_tsc.js').write_text('mutated after npm ci')
        with self.assertRaisesRegex(RuntimeError, 'complete manifest'):
            self.select()

    def test_rewritten_compiler_and_manifest_cannot_override_pre_submit_pin(self):
        (self.directory / 'lib/_tsc.js').write_text('mutated after preparation')
        self.manifest.write_text(json.dumps(P.compiler_tree(self.directory)))
        with self.assertRaisesRegex(RuntimeError, 'pre-submit SHA-256 pin'):
            self.select()

    def test_added_removed_changed_input_and_changed_manifest_fail_final_guard(self):
        for change in ('add', 'remove', 'compiler', 'declaration', 'manifest', 'lock', 'node'):
            with self.subTest(change=change):
                _, evidence = self.select()
                path = {'add': self.directory / 'extra.js', 'remove': self.directory / 'lib/tsc.js',
                        'compiler': self.directory / 'lib/_tsc.js',
                        'declaration': self.directory / 'lib/lib.es2022.d.ts', 'manifest': self.manifest,
                        'lock': self.root / 'typescript/package-lock.json', 'node': self.node}[change]
                old = path.read_bytes() if path.exists() else None
                if change == 'remove': path.unlink()
                else: path.write_bytes(b'changed')
                with self.assertRaises(RuntimeError): P.check_typescript(self.root, evidence)
                self.assertFalse(evidence['stable'])
                if old is None: path.unlink()
                else: path.write_bytes(old)

    def test_wrong_version_or_wrong_reported_version_rejected(self):
        _, evidence = self.select()
        with self.assertRaisesRegex(RuntimeError, 'incompatible version'):
            P.require_typescript_version('Version 6.0.0', evidence)
        (self.directory / 'package.json').write_text(json.dumps({'name': 'typescript', 'version': '6.0.0'}))
        self.seal()  # Even a self-consistent manifest cannot override the lock.
        with self.assertRaisesRegex(RuntimeError, 'pinned package'):
            self.select()

    def test_compiler_failures_survive_restoration_and_recollection(self):
        path = self.directory / 'lib/_tsc.js'
        original = path.read_bytes()
        for change in ('mutate', 'remove', 'exception'):
            with self.subTest(change=change):
                _, evidence = self.select()
                with ExitStack() as stack:
                    if change == 'exception':
                        stack.enter_context(mock.patch.object(P, 'typescript_snapshot', side_effect=ValueError('snapshot failed')))
                    elif change == 'remove': path.unlink()
                    else: path.write_bytes(b'changed compiler')
                    with self.assertRaises((RuntimeError, ValueError)): P.check_typescript(self.root, evidence)
                failure = copy.deepcopy(evidence['failures'][0])
                if change == 'mutate':
                    self.assertNotEqual(failure['after'], evidence['before'])
                else:
                    self.assertIn('snapshot_error', failure)
                path.write_bytes(original)
                with self.assertRaises(RuntimeError): P.check_typescript(self.root, evidence)
                self.assertEqual(evidence['after'], evidence['before'])
                self.assertEqual(evidence['failures'][0], failure)
                self.assertFalse(evidence['stable'])

    def test_missing_selection_is_required_by_both_driver_clis(self):
        for driver, args in ((MODEL, ['--output', str(self.root / 'out')]),
                             (RESEARCH, ['--output', str(self.root / 'out'), '--rust', 'old', '--rust-build-receipt', 'receipt'])):
            with self.subTest(driver=driver.__name__), mock.patch.object(sys, 'argv', ['driver', *args]), \
                    mock.patch.object(driver.subprocess, 'Popen') as launch, \
                    mock.patch.object(driver.subprocess, 'run') as run:
                with self.assertRaises(SystemExit): driver.main()
                launch.assert_not_called(); run.assert_not_called()


class ResearchReceipt(Files):
    def setUp(self):
        super().setUp()
        self.source_file = self.write('validation/corpus/src/main.rs', 'candidate source')
        self.source = {'validation/corpus/src/main.rs': P.file_identity(self.source_file)}
        tools = {name: self.write('tools/' + name, name, True) for name in ('cargo', 'rustc', 'rustdoc')}
        self.target = self.root / 'target/research-fresh'
        self.binaries = {name: self.write('target/research-fresh/debug/' + name, name, True)
                         for name in ('native-research', 'stateless-corpus')}
        self.log = self.root / 'build-receipt/command.log'
        self.receipt = self.root / 'build-receipt/receipt.json'
        self.messages = [{'reason': 'compiler-artifact',
                          'package_id': 'path+' + (self.root / 'validation/corpus').as_uri() + '#stateless-corpus@0.1.0',
                          'manifest_path': str(self.root / 'validation/corpus/Cargo.toml'),
                          'target': {'name': name, 'kind': ['bin'],
                                     'src_path': str(self.root / 'validation/corpus/src' /
                                                     ('main.rs' if name == 'stateless-corpus' else 'bin/native-research.rs'))},
                          'profile': {'test': False}, 'fresh': False,
                          'executable': str(path), 'filenames': [str(path)]} for name, path in self.binaries.items()]
        self.messages.append({'reason': 'build-finished', 'success': True})
        self.source_ref = self.manifest(self.source)
        tools_ref = self.manifest({'executables': {name: P.bound_file(path) for name, path in tools.items()},
                                   'cargo_configuration': {}})
        self.report = {'status': 'passed', 'scope': 'command', 'cwd': str(self.root),
                       'source_equal': True, 'tools_equal': True, 'typescript_dependencies_equal': True,
                       'source_before': self.source_ref, 'source_after': self.source_ref,
                       'tools_before': tools_ref, 'tools_after': tools_ref,
                       'typescript_dependencies_before': self.manifest({'missing': True}),
                       'typescript_dependencies_after': self.manifest({'missing': True}),
                       'artifacts_before': self.manifest({}),
                       'artifacts_after': self.manifest({str(path.relative_to(self.root)): P.file_identity(path)
                                                        for path in self.binaries.values()}),
                       'environment': {'PATH': str(self.root / 'tools'), 'CARGO_INCREMENTAL': '0',
                                       'RUSTFLAGS': None, 'CARGO_ENCODED_RUSTFLAGS': None,
                                       'CARGO_TARGET_DIR': str(self.root / 'target/integration'),
                                       'RUSTUP_TOOLCHAIN': None, 'CARGO_HOME': None, 'SDKROOT': None, 'DEVELOPER_DIR': None,
                                       'PYTHONNOUSERSITE': '1', 'PYTHONPATH': ''},
                       'commands': [{'name': 'command', 'command': ['cargo', 'build', '--manifest-path',
                           'validation/corpus/Cargo.toml', '--locked', '--offline', '--bin', 'native-research',
                           '--bin', 'stateless-corpus', '--message-format=json', '--target-dir', str(self.target), '-j', '2'],
                           'cwd': str(self.root), 'exit_code': 0, 'source_equal': True,
                           'source_before': self.source_ref, 'source_after': self.source_ref, 'environment_overlay': {}}]}
        self.seal_log()
        self.save()

    def manifest(self, value):
        raw = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
        digest = hashlib.sha256(raw).hexdigest()
        path = self.write('validation/native-parity/derive-model-v2/integration-oct22/manifests/' + digest + '.json', raw)
        return {'path': str(path.relative_to(self.root)), 'sha256': digest, 'size_bytes': len(raw), 'entries': len(value)}

    def seal_log(self):
        self.write(str(self.log.relative_to(self.root)), '\n'.join(json.dumps(row) for row in self.messages) + '\n')
        self.report['commands'][0]['log'] = P.file_identity(self.log)
        self.report['logs'] = {'command.log': P.file_identity(self.log)}

    def save(self):
        self.receipt.write_text(json.dumps(self.report))

    def validate(self, evidence=None):
        with mock.patch.object(P.integration_api(), 'source_snapshot', return_value=self.source):
            evidence = {} if evidence is None else evidence
            P.validate_research_build(self.root, self.receipt, self.binaries, evidence)
        return evidence

    def test_valid_build_and_copies_remain_bound(self):
        evidence = self.validate()
        for name in self.binaries:
            P.copy_research_executable(evidence, name, self.root / ('installed-' + name))
        P.check_bound_inputs(evidence)
        self.assertTrue(evidence['validated']); self.assertTrue(evidence['inputs_stable'])
        (self.root / 'installed-native-research').write_bytes(b'old otherwise passing binary')
        with self.assertRaises(RuntimeError): P.check_bound_inputs(evidence)
        self.assertIn(str(self.root / 'installed-native-research'), evidence['input_errors'])

    def test_old_original_fails_and_driver_never_launches_it(self):
        self.binaries['native-research'].write_bytes(b'old otherwise passing binary')
        # Exercise actual main() ordering. Git metadata is the only preflight
        # subprocess read; no package builder or supplied executable may launch.
        output = self.root / 'consumer-output'
        argv = ['driver', '--output', str(output), '--rust', str(self.binaries['native-research']),
                '--rust-portable', str(self.binaries['stateless-corpus']), '--rust-build-receipt', str(self.receipt),
                '--typescript-compiler', str(self.root / 'unused'), '--typescript-compiler-manifest', str(self.root / 'unused.json'),
                '--typescript-compiler-manifest-sha256', 'unused']
        work = self.root / 'consumer-work'; work.mkdir()
        with mock.patch.object(RESEARCH, 'ROOT', self.root), mock.patch.object(sys, 'argv', argv), \
                mock.patch.object(RESEARCH.subprocess, 'check_output', side_effect=[b'validation/corpus/src/main.rs\0', 'head\n']), \
                mock.patch.object(RESEARCH.provenance.integration_api(), 'source_snapshot', return_value=self.source), \
                mock.patch.object(RESEARCH.tempfile, 'mkdtemp', return_value=str(work)), \
                mock.patch.object(RESEARCH.subprocess, 'Popen') as launch, \
                mock.patch.object(RESEARCH.provenance, 'select_typescript') as compiler:
            with self.assertRaisesRegex(RuntimeError, 'differs from the build receipt'): RESEARCH.main()
        launch.assert_not_called(); compiler.assert_not_called()
        failure = json.loads((output / 'qualification.json').read_text())
        self.assertEqual(failure['status'], 'failed')
        self.assertFalse(failure['rust_build_provenance']['validated'])

    def test_bound_inputs_and_copies_keep_failed_identities_after_restoration(self):
        for target in ('original', 'copy'):
            for change in ('mutate', 'remove', 'exception'):
                with self.subTest(target=target, change=change):
                    evidence = self.validate()
                    installed = self.root / 'installed-native-research'
                    P.copy_research_executable(evidence, 'native-research', installed)
                    path = self.binaries['native-research'] if target == 'original' else installed
                    original = path.read_bytes()
                    bound_file = P.bound_file
                    def snapshot(item):
                        if Path(item) == path: raise ValueError('bound input unavailable')
                        return bound_file(item)
                    with ExitStack() as stack:
                        if change == 'exception':
                            stack.enter_context(mock.patch.object(P, 'bound_file', side_effect=snapshot))
                        elif change == 'remove': path.unlink()
                        else: path.write_bytes(b'changed binary')
                        with self.assertRaises(RuntimeError): P.check_bound_inputs(evidence)
                    failure = copy.deepcopy(evidence['input_failures'][0])
                    self.assertIn(str(path), failure['errors'])
                    if change == 'mutate':
                        self.assertNotEqual(failure['after'][str(path)], evidence['inputs'][str(path)])
                    survivor = self.binaries['stateless-corpus']
                    self.assertEqual(failure['after'][str(survivor)], evidence['inputs'][str(survivor)])
                    path.write_bytes(original); path.chmod(0o755)
                    with self.assertRaises(RuntimeError): P.check_bound_inputs(evidence)
                    self.assertEqual(evidence['inputs_after'], evidence['inputs'])
                    self.assertEqual(evidence['input_failures'][0], failure)
                    self.assertFalse(evidence['inputs_stable'])

    def test_source_or_receipt_mismatch_rejected_before_copy(self):
        original = copy.deepcopy(self.report)
        mutations = [lambda r: r.update(status='failed'), lambda r: r.update(source_equal=1),
                     lambda r: r.update(tools_equal=False), lambda r: r.update(collection_errors={'tool': 'missing'}),
                     lambda r: r['commands'][0]['command'].__setitem__(1, 'check'),
                     lambda r: r['commands'][0]['command'].__setitem__(3, 'Cargo.toml'),
                     lambda r: r['commands'][0].update(environment_overlay={'RUSTFLAGS': 'override'}),
                     lambda r: r.update(artifacts_before=r['artifacts_after'])]
        for change in mutations:
            self.report = copy.deepcopy(original); change(self.report); self.save()
            with self.subTest(report=self.report), self.assertRaises(RuntimeError): self.validate()
        self.report = original; self.save()
        self.source['added.rs'] = P.file_identity(self.source_file)
        with self.assertRaisesRegex(RuntimeError, 'full current candidate'): self.validate()

    def test_copy_time_failures_remain_sticky_when_bound_inputs_are_restored(self):
        path = self.binaries['native-research']
        original = path.read_bytes()
        destination = self.root / 'installed-native-research'
        for change in ('source', 'copy', 'exception'):
            with self.subTest(change=change):
                evidence = self.validate()
                copy_file = P.shutil.copy2
                def changed_copy(source, target):
                    copy_file(source, target)
                    if change == 'exception': raise OSError('copy interrupted')
                    Path(target).write_bytes(b'changed copied binary')
                with ExitStack() as stack:
                    if change == 'source': path.write_bytes(b'changed source binary')
                    else: stack.enter_context(mock.patch.object(P.shutil, 'copy2', side_effect=changed_copy))
                    with self.assertRaises((RuntimeError, OSError)):
                        P.copy_research_executable(evidence, 'native-research', destination)
                failure = copy.deepcopy(evidence['input_failures'][0])
                if change == 'copy':
                    self.assertNotEqual(failure['copies']['native-research']['sha256'],
                                        evidence['executables']['native-research']['sha256'])
                path.write_bytes(original)
                destination.write_bytes(original); destination.chmod(0o755)
                with self.assertRaises(RuntimeError): P.check_bound_inputs(evidence)
                self.assertEqual(evidence['input_failures'][0], failure)
                self.assertFalse(evidence['inputs_stable'])

    def test_manifest_and_log_bytes_are_verified(self):
        manifest_path = self.root / self.source_ref['path']
        original = manifest_path.read_bytes(); manifest_path.write_bytes(original + b' ')
        with self.assertRaisesRegex(RuntimeError, 'hash/size'): self.validate()
        manifest_path.write_bytes(original)
        self.log.write_bytes(self.log.read_bytes() + b'changed')
        with self.assertRaisesRegex(RuntimeError, 'log identity'): self.validate()

    def test_cargo_artifact_must_be_fresh_actual_non_test_executable(self):
        original = copy.deepcopy(self.messages)
        for change in (lambda m: m[0].update(fresh=True), lambda m: m[0]['profile'].update(test=True),
                       lambda m: m[0]['target'].update(src_path='old/source.rs'),
                       lambda m: m[0].update(package_id='old-package'),
                       lambda m: m.append(copy.deepcopy(m[0])), lambda m: m[-1].update(success=False)):
            self.messages = copy.deepcopy(original); change(self.messages); self.seal_log(); self.save()
            with self.subTest(messages=self.messages), self.assertRaises(RuntimeError): self.validate()


class RunnerEvidence(Files):
    def test_every_baseline_runner_including_late_swift_is_required_and_retained(self):
        paths = {name: self.write(name, name, True) for name in ('python', 'go', 'node', 'swift', 'rust')}
        module = self.write('site-packages/statelessness/conformance.py', 'module')
        script = self.write('node_modules/package/bin/corpus.mjs', 'script')
        runners = {'python': [str(paths['python']), '-I', '-m', 'statelessness.conformance', '--runner'],
                   'typescript': [str(paths['node']), str(script)],
                   **{name: [str(paths[name])] for name in ('go', 'swift', 'rust')}}
        extra = {'python': [module]}
        for mode in ('missing', 'changed'):
            paths['swift'].write_text('swift')
            report = {'status': 'running'}
            P.record_runner_files(report, 'baseline_runner', runners, 'before', extra)
            if mode == 'missing': paths['swift'].unlink()
            else: paths['swift'].write_text('different executable')
            with self.assertRaises(RuntimeError): P.record_runner_files(report, 'baseline_runner', runners, 'after', extra)
            self.assertFalse(report['baseline_runner_files_stable'])
            for survivor in ('python', 'go', 'typescript', 'rust'):
                self.assertEqual(report['baseline_runner_files_before'][survivor], report['baseline_runner_files_after'][survivor])
        paths['swift'].unlink()
        report = {}
        with self.assertRaises(RuntimeError): P.record_runner_files(report, 'baseline_runner', runners, 'before', extra)
        self.assertIn('identity_error', report['baseline_runner_files_before']['swift'][0])

    def test_generic_runner_failures_survive_restoration_and_recollection(self):
        path = self.write('swift', 'original', True)
        survivor = self.write('go', 'unchanged', True)
        runners = {'swift': [str(path)], 'go': [str(survivor)]}
        for prefix in ('research_runner', 'baseline_runner'):
            for change in ('mutate', 'remove', 'exception'):
                with self.subTest(prefix=prefix, change=change):
                    report = {}
                    P.record_runner_files(report, prefix, runners, 'before')
                    bound_file = P.bound_file
                    def snapshot(item):
                        if Path(item) == path: raise ValueError('runner snapshot failed')
                        return bound_file(item)
                    with ExitStack() as stack:
                        if change == 'exception':
                            stack.enter_context(mock.patch.object(P, 'bound_file', side_effect=snapshot))
                        elif change == 'remove': path.unlink()
                        else: path.write_text('changed')
                        with self.assertRaises(RuntimeError): P.record_runner_files(report, prefix, runners, 'after')
                    failure = copy.deepcopy(report[prefix + '_files_failures'][0])
                    self.assertNotEqual(failure['files']['swift'], report[prefix + '_files_before']['swift'])
                    self.assertEqual(failure['files']['go'], report[prefix + '_files_before']['go'])
                    path.write_text('original'); path.chmod(0o755)
                    with self.assertRaises(RuntimeError): P.record_runner_files(report, prefix, runners, 'after')
                    self.assertEqual(report[prefix + '_files_after'], report[prefix + '_files_before'])
                    self.assertEqual(report[prefix + '_files_failures'][0], failure)
                    self.assertFalse(report[prefix + '_files_stable'])

    def test_model_runner_failures_survive_restoration_and_recollection(self):
        path = self.write('swift', 'original', True)
        survivor = self.write('go', 'unchanged', True)
        for change in ('mutate', 'remove', 'exception'):
            with self.subTest(change=change):
                report = {'runners': {'swift': [str(path)], 'go': [str(survivor)]}}
                self.assertTrue(MODEL.record_runner_snapshot(report, 'before'))
                if change == 'exception':
                    with mock.patch.object(MODEL, 'runner_snapshot', side_effect=ValueError('snapshot failed')):
                        with self.assertRaises(ValueError): MODEL.record_runner_snapshot(report, 'after')
                else:
                    if change == 'remove': path.unlink()
                    else: path.write_text('changed')
                    self.assertFalse(MODEL.record_runner_snapshot(report, 'after'))
                failure = copy.deepcopy(report['runner_snapshot_failures'][0])
                if change == 'exception': self.assertIn('snapshot_error', failure)
                else:
                    self.assertNotEqual(failure['files']['swift'], report['runner_files_before']['swift'])
                    self.assertEqual(failure['files']['go'], report['runner_files_before']['go'])
                path.write_text('original'); path.chmod(0o755)
                self.assertFalse(MODEL.record_runner_snapshot(report, 'after'))
                self.assertEqual(report['runner_files_after'], report['runner_files_before'])
                self.assertEqual(report['runner_snapshot_failures'][0], failure)
                self.assertFalse(report['runner_snapshot_unchanged'])


class InputTreeEvidence(Files):
    def test_imported_modules_mutate_add_remove_with_unchanged_entrypoints(self):
        for package, entry, imported in (
                ('python', '__init__.py', 'core.py'),
                ('typescript', 'dist/index.js', 'dist/conformance.js')):
            for change in ('mutate', 'add', 'remove'):
                with self.subTest(package=package, change=change):
                    base = f'{package}-{change}'
                    entrypoint = self.write(base + '/' + entry, 'unchanged entrypoint')
                    module = self.write(base + '/' + imported, 'imported implementation')
                    report = {}
                    P.freeze_input_tree(report, package, self.root / base)
                    if change == 'mutate': module.write_text('different implementation')
                    elif change == 'remove': module.unlink()
                    else: self.write(base + '/added-module.py', 'new imported input')
                    with self.assertRaises(RuntimeError): P.check_input_trees(report)
                    evidence = report['input_trees'][package]
                    self.assertFalse(report['input_trees_stable'])
                    self.assertEqual(evidence['before']['entries'][entry], evidence['after']['entries'][entry])
                    field = {'mutate': 'changed', 'add': 'added', 'remove': 'removed'}[change]
                    self.assertIn('added-module.py' if change == 'add' else imported, evidence['differences'][field])
                    self.assertEqual(entrypoint.read_text(), 'unchanged entrypoint')

    def test_modes_types_sizes_hashes_and_surviving_roots_are_retained(self):
        mode = self.write('inputs/mode.py', 'same bytes')
        changed_type = self.write('inputs/type.py', 'file')
        removed = self.write('missing/fixture.json', '{}')
        self.write('survivor/consumer.py', 'consumer')
        report = {}
        for name in ('inputs', 'missing', 'survivor'):
            P.freeze_input_tree(report, name, self.root / name)
        mode.chmod(0o700 if mode.stat().st_mode & 0o777 != 0o700 else 0o600)
        changed_type.unlink(); changed_type.mkdir()
        removed.unlink(); removed.parent.rmdir()
        with self.assertRaises(RuntimeError): P.check_input_trees(report)
        evidence = report['input_trees']['inputs']
        self.assertEqual(evidence['before']['entries']['mode.py']['sha256'], evidence['after']['entries']['mode.py']['sha256'])
        self.assertIn('mode.py', evidence['differences']['changed'])
        self.assertEqual(evidence['differences']['changed']['type.py']['after']['type'], 'directory')
        self.assertIn('size_bytes', evidence['before']['entries']['type.py'])
        self.assertTrue(report['input_trees']['missing']['after']['errors'])
        self.assertTrue(report['input_trees']['survivor']['stable'])

    def test_installed_bytecode_and_startup_namespace_are_bound(self):
        site = 'venv/Lib/site-packages' if P.os.name == 'nt' else 'venv/lib/python3.13/site-packages'
        self.write(site + '/statelessness/__init__.py', 'entrypoint')
        cached = self.write(site + '/statelessness/__pycache__/core.cpython-313.pyc', b'compiled import')
        self.write(site + '/statelessness-0.1.dist-info/METADATA', 'metadata')
        self.write('venv/pyvenv.cfg', 'include-system-site-packages = false')
        pip = self.write(site + '/pip/bootstrap.py', 'bootstrap only')
        report = {}
        P.freeze_installed_python(report, self.root / 'venv')
        pip.write_text('excluded bootstrap tooling')
        P.check_input_trees(report)
        cached.write_bytes(b'changed executable bytecode')
        self.write(site + '/startup.pth', 'import substituted_startup')
        with self.assertRaises(RuntimeError): P.check_input_trees(report)
        differences = report['input_trees']['installed-python']['differences']
        self.assertIn('statelessness/__pycache__/core.cpython-313.pyc', differences['changed'])
        self.assertIn('startup.pth', differences['added'])

    def test_outputs_are_excluded_only_at_the_explicit_root_and_then_bound(self):
        self.write('package/src/main.ts', 'source')
        report = {}
        P.freeze_input_tree(report, 'source', self.root / 'package', excluded=('dist',))
        output = self.write('package/dist/main.js', 'compiled source')
        P.check_input_trees(report)
        P.freeze_input_tree(report, 'compiled', output.parent)
        output.write_text('mutated compiled source')
        self.write('package/src/dist/injected.ts', 'not excluded')
        with self.assertRaises(RuntimeError): P.check_input_trees(report)
        self.assertIn('src/dist/injected.ts', report['input_trees']['source']['differences']['added'])
        self.assertIn('main.js', report['input_trees']['compiled']['differences']['changed'])

    def test_failed_snapshot_survives_restoration_and_baselines_cannot_be_reset(self):
        path = self.write('conformance/corpus.json', 'original')
        report = {}
        P.freeze_input_tree(report, 'fixtures', path.parent)
        path.write_text('changed')
        with self.assertRaises(RuntimeError): P.check_input_trees(report)
        first_failure = copy.deepcopy(report['input_trees']['fixtures']['failures'][0])
        path.write_text('original')
        with self.assertRaises(RuntimeError): P.check_input_trees(report)
        self.assertEqual(report['input_trees']['fixtures']['failures'][0], first_failure)
        with self.assertRaisesRegex(RuntimeError, 'must not be replaced'):
            P.freeze_input_tree(report, 'fixtures', path.parent)

    def test_producer_input_is_bound_while_pair_outputs_accumulate(self):
        producer = self.write('values/python.json', 'producer envelope')
        report = {}
        P.freeze_input_tree(report, 'producer-python', producer)
        self.write('values/python-to-go.json', 'pair output')
        P.check_input_trees(report)
        producer.write_text('modified transcode input')
        with self.assertRaises(RuntimeError): P.check_input_trees(report)
        self.assertIn('.', report['input_trees']['producer-python']['differences']['changed'])

    def test_symlinked_directories_escape_links_and_unreadable_files_fail_closed(self):
        target = self.write('package/module.js', 'implementation')
        (target.parent / 'link.js').symlink_to('module.js')
        report = {}
        P.freeze_input_tree(report, 'package', target.parent)
        self.assertEqual(report['input_trees']['package']['before']['entries']['link.js']['type'], 'symlink')
        outside = self.write('outside/module.js', 'escaped')
        (target.parent / 'escaped.js').symlink_to(outside)
        (target.parent / 'directory').symlink_to(outside.parent, target_is_directory=True)
        original = Path.open
        def inaccessible(path, *args, **kwargs):
            if path == target: raise PermissionError('simulated unreadable imported module')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'open', inaccessible), self.assertRaises(RuntimeError):
            P.check_input_trees(report)
        errors = report['input_trees']['package']['after']['errors']
        self.assertTrue({'module.js', 'escaped.js', 'directory'} <= errors.keys())


class DriverInputEvidence(Files):
    """Exercise real driver control flow with fake build/install processes.

    No fake consumer is executed. The supplied process doubles create just the
    files each stage promises, then mutate an input at the selected use window.
    """
    def failed_attempt(self, driver, checkpoint, mutate, launch_error=False, *,
                       with_swift=False, with_rust_portable=False):
        case = self.root / ('attempt-' + str(len(list(self.root.iterdir()))))
        root, work, output = case / 'source', case / 'work', case / 'out'
        work.mkdir(parents=True)
        def put(path, content='fixture'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content if isinstance(content, bytes) else content.encode())
            return path
        for name in ('python/src/statelessness/core.py', 'go/go.mod', 'go/core.go',
                     'typescript/src/index.ts', 'typescript/package.json', 'typescript/tsconfig.json',
                     'conformance/run.py', 'conformance/portability.py', 'conformance/corpus.json',
                     'Package.swift', 'swift/Source.swift', 'src/lib.rs',
                     'crates/macro-qualification/Cargo.toml', 'docs/requirements/README.md',
                     'benches/fixture.rs', 'tests/fixture.rs', 'Cargo.toml', 'Cargo.lock',
                     'build.rs', 'README.md', 'LICENSE'):
            put(root / name)
        for family in ('native-model', 'native-research'):
            for name in ('python/consumer.py', 'go/main.go', 'typescript/consumer.ts', 'swift/Package.swift'):
                put(root / 'examples' / family / name)
        put(root / 'conformance/research-equivalence-v1.json', json.dumps(
            {'cases': [{'case': 'tiny', 'expected': {'result': 1}}]}))
        reference = put(case / 'reference')
        portable = put(case / 'portable')
        for name in ('go', 'node', 'npm', 'compiler', 'swift', 'rustc'): put(case / 'tools' / name)
        site = work / 'venv' / ('Lib/site-packages' if P.os.name == 'nt' else 'lib/python3.13/site-packages')
        fired = []

        def trigger(name, command):
            if name == checkpoint and not fired:
                fired.append(command)
                mutate(work, output, site)
                if launch_error: raise RuntimeError('synthetic process collection failure')

        def execute(argv, cwd):
            command = list(map(str, argv)); cwd = Path(cwd)
            name = ''
            result = b''
            if '-I' in command: self.assertIn('-B', command)
            if 'venv' in command:
                env_root = Path(command[-1])
                put(env_root / ('Scripts/python.exe' if P.os.name == 'nt' else 'bin/python'))
                put(env_root / 'pyvenv.cfg', 'include-system-site-packages = false')
            elif 'wheel' in command and '--wheel-dir' in command:
                put(output / 'packages/package.whl')
            elif '--no-index' in command:
                for module in ('__init__.py', 'core.py', 'conformance.py', '__pycache__/core.cpython-313.pyc'):
                    put(site / 'statelessness' / module)
                put(site / 'statelessness-0.1.dist-info/METADATA')
            elif command[0] == str(case / 'tools/go') or command[0] == 'go':
                if 'build' in command:
                    put(Path(command[command.index('-o') + 1]))
                    name = 'baseline-go-build' if './cmd/stateless-corpus' in command else 'go-build'
            elif command[0] == str(case / 'tools/compiler') and '-p' in command:
                if cwd.name == 'typescript-package':
                    put(cwd / 'dist/index.js')
                    name = 'typescript-package-build'
                else:
                    put(cwd / 'build/consumer.js')
                    name = 'typescript-declarations'
            elif Path(command[0]).name == 'npm':
                if 'pack' in command: put(output / 'packages/package.tgz')
                elif 'install' in command:
                    self.assertIn('--no-save', command)
                    for module in ('package.json', 'dist/index.js', 'dist/conformance.js', 'bin/corpus.mjs'):
                        put(cwd / 'node_modules/@jagalite/statelessness' / module)
            elif Path(command[0]).name == 'swift' and 'build' in command:
                package = Path(command[command.index('--package-path') + 1])
                baseline = package.name == 'swift-package'
                product = 'stateless-corpus' if baseline else 'ModelConsumer' if driver is MODEL else 'ResearchConsumer'
                binary_dir = package / '.build/fake-triple/debug'
                if '--show-bin-path' in command:
                    self.assertFalse((binary_dir / product).exists(), 'resolve the output before building')
                    name = 'baseline-swift-bin-path' if baseline else 'swift-bin-path'
                    result = str(binary_dir).encode()
                else:
                    put(binary_dir / product)
                    name = 'baseline-swift-build' if baseline else 'swift-build'
            elif Path(command[0]).name == 'rustc':
                name = 'rustc-host' if '-vV' in command else 'rustc-version'
                result = b'host: fixture-target\n' if '-vV' in command else b'rustc fixture\n'
            elif command[0] == 'cargo' and 'install' in command:
                put(work / 'rust-install/bin/n13-model-interchange')
                name = 'rust-install'
                result = b'[statelessness 0.1.0] cargo:rustc-env=STATELESS_BUILD_ID=source-fnv1a64:0000000000000000\n'
            if 'run' in command and str(work / 'research-equivalence-v1.json') in command:
                name = 'research-consumer'
                put(Path(command[-1]), json.dumps({'profile': 'research-equivalence-v1',
                    'results': [{'case': 'tiny', 'result': 1}]}))
                result = json.dumps({'mode': 'run', 'case_count': 1, 'production_pruning_enabled': False,
                                     'package': str(site / 'statelessness/__init__.py')}).encode()
            if str(work / 'conformance/run.py') in command:
                name = 'baseline'
                self.assertEqual(command[command.index('--artifact-dir') + 1], str(output / 'portable-baseline-receipts'))
            trigger(name, command)
            return result

        def run(argv, cwd, **kwargs):
            return SimpleNamespace(stdout=execute(argv, cwd), returncode=0)
        def popen(argv, cwd, **kwargs):
            return SimpleNamespace(returncode=0, communicate=lambda **kw: (execute(argv, cwd), None))
        def compiler(*args):
            args[-1].update(before={}, version='fixture')
            return [str(case / 'tools/compiler')]
        def validate(_root, _receipt, _inputs, evidence):
            executables = {name: driver.provenance.bound_file(path) for name, path in _inputs.items()}
            evidence.update(manifests={'source': {}}, build_receipt={'source_before': {'sha256': 'source'}},
                            executables=executables, copies={}, validated=True,
                            inputs={row['path']: row for row in executables.values()})
        copy_executable = driver.provenance.copy_research_executable
        def copy_output(evidence, name, path):
            copy_executable(evidence, name, path)
            trigger(name + '-copy', [str(path)])
        snapshot_runners = MODEL.runner_snapshot
        def model_snapshot(*args, **kwargs):
            trigger('model-runner-snapshot', [])
            return snapshot_runners(*args, **kwargs)
        argv = ['driver', '--output', str(output), '--typescript-compiler', 'unused',
                '--typescript-compiler-manifest', 'unused', '--typescript-compiler-manifest-sha256', 'unused']
        if driver is RESEARCH:
            argv += ['--rust', str(reference), '--rust-build-receipt', 'unused']
            if not with_swift: argv += ['--without-swift']
            if with_rust_portable: argv += ['--rust-portable', str(portable)]
        with ExitStack() as stack:
            for obj, name, value in (
                    (driver, 'ROOT', root), (sys, 'argv', argv)):
                stack.enter_context(mock.patch.object(obj, name, value))
            for obj, name, effect in (
                    (driver.tempfile, 'mkdtemp', lambda **kw: str(work)),
                    (driver.subprocess, 'check_output', lambda command, **kw:
                        b'python/src/statelessness/core.py\0' if 'ls-files' in command else 'head\n'),
                    (driver.subprocess, 'run', run), (driver.subprocess, 'Popen', popen),
                    (driver.shutil, 'which', lambda name, **kw: str(case / 'tools' / name)),
                    (driver.provenance, 'select_typescript', compiler),
                    (driver.provenance, 'require_typescript_version', lambda *args: None),
                    (driver.provenance, 'check_typescript', lambda *args: None),
                    (driver.provenance, 'validate_research_build', validate),
                    (driver.provenance, 'copy_research_executable', copy_output),
                    (MODEL._oracle, 'rust_build_id', lambda *args, **kw: 'source-fnv1a64:0000000000000000'),
                    (MODEL, 'runner_snapshot', model_snapshot)):
                stack.enter_context(mock.patch.object(obj, name, side_effect=effect))
            with self.assertRaises(RuntimeError): driver.main()
        self.assertEqual(len(fired), 1, 'the intended use window must be reached')
        report = json.loads((output / 'qualification.json').read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['input_trees_stable'])
        return report

    def assert_replaced_output(self, report, name):
        evidence = report['input_trees'][name]
        original = evidence['before']['entries']['.']
        changed = evidence['failures'][0]['after']['entries']['.']
        self.assertEqual(original['sha256'], hashlib.sha256(b'fixture').hexdigest())
        self.assertEqual(changed['sha256'], hashlib.sha256(b'replaced executable').hexdigest())
        self.assertFalse(evidence['stable'])

    def test_go_output_is_bound_before_typescript_preparation_in_both_drivers(self):
        def mutate(work, output, site):
            (work / ('go-consumer.exe' if P.os.name == 'nt' and driver is RESEARCH else 'go-consumer-bin')).write_bytes(b'replaced executable')
        for driver in (MODEL, RESEARCH):
            with self.subTest(driver=driver.__name__):
                report = self.failed_attempt(driver, 'typescript-package-build', mutate)
                self.assert_replaced_output(report, 'go-executable')

    def test_swift_output_is_bound_before_later_preparation_in_both_drivers(self):
        for driver, checkpoint, product in ((MODEL, 'rustc-version', 'ModelConsumer'),
                                             (RESEARCH, 'native-research-copy', 'ResearchConsumer')):
            def mutate(work, output, site):
                (work / 'swift-consumer/.build/fake-triple/debug' / product).write_bytes(b'replaced executable')
            with self.subTest(driver=driver.__name__):
                report = self.failed_attempt(driver, checkpoint, mutate, with_swift=True)
                self.assert_replaced_output(report, 'swift-executable')

    def test_baseline_go_output_is_bound_before_swift_path_resolution(self):
        def mutate(work, output, site):
            (work / ('go-corpus.exe' if P.os.name == 'nt' else 'go-corpus')).write_bytes(b'replaced executable')
        report = self.failed_attempt(RESEARCH, 'baseline-swift-bin-path', mutate, with_swift=True)
        self.assert_replaced_output(report, 'baseline-go-executable')

    def test_baseline_swift_output_is_bound_before_rust_copy_preparation(self):
        def mutate(work, output, site):
            (work / 'swift-package/.build/fake-triple/debug/stateless-corpus').write_bytes(b'replaced executable')
        report = self.failed_attempt(RESEARCH, 'stateless-corpus-copy', mutate,
                                     with_swift=True, with_rust_portable=True)
        self.assert_replaced_output(report, 'baseline-swift-executable')

    def test_model_rust_output_is_bound_before_final_runner_snapshot(self):
        def mutate(work, output, site):
            (work / 'rust-install/bin/n13-model-interchange').write_bytes(b'replaced executable')
        report = self.failed_attempt(MODEL, 'model-runner-snapshot', mutate)
        self.assert_replaced_output(report, 'rust-executable')

    def test_both_drivers_fail_for_installed_python_mutation_addition_removal(self):
        for driver in (MODEL, RESEARCH):
            for change in ('mutate', 'add', 'remove', 'pyc'):
                def mutate(work, output, site):
                    path = site / 'statelessness' / {
                        'mutate': 'core.py', 'add': 'added.py', 'remove': 'core.py',
                        'pyc': '__pycache__/core.cpython-313.pyc'}[change]
                    if change == 'remove': path.unlink()
                    else: path.write_bytes(b'changed imported input')
                with self.subTest(driver=driver.__name__, change=change):
                    report = self.failed_attempt(driver, 'go-build', mutate)
                    evidence = report['input_trees']['installed-python']
                    self.assertTrue(evidence['failures'])
                    self.assertEqual(evidence['before']['entries']['statelessness/__init__.py'],
                                     evidence['after']['entries']['statelessness/__init__.py'])

    def test_both_drivers_bind_installed_typescript_before_declaration_compilation(self):
        def mutate(work, output, site):
            (work / 'typescript-consumer/node_modules/@jagalite/statelessness/dist/conformance.js').write_text('drift')
        for driver in (MODEL, RESEARCH):
            with self.subTest(driver=driver.__name__):
                report = self.failed_attempt(driver, 'typescript-declarations', mutate)
                self.assertIn('@jagalite/statelessness/dist/conformance.js',
                              report['input_trees']['installed-typescript']['differences']['changed'])

    def test_both_drivers_bind_staged_package_before_its_build(self):
        def mutate(work, output, site):
            (work / 'go-package/core.go').write_text('mutated build input')
        for driver in (MODEL, RESEARCH):
            with self.subTest(driver=driver.__name__):
                report = self.failed_attempt(driver, 'go-build', mutate)
                self.assertIn('core.go', report['input_trees']['go-package']['differences']['changed'])

    def test_copied_research_fixture_is_retained_when_process_collection_raises(self):
        def mutate(work, output, site):
            (work / 'research-equivalence-v1.json').write_text('changed fixture')
        report = self.failed_attempt(RESEARCH, 'research-consumer', mutate, launch_error=True)
        self.assertEqual(report['error'], 'synthetic process collection failure')
        self.assertIn('.', report['input_trees']['research-fixture']['differences']['changed'])

    def test_baseline_input_and_installed_package_drift_preserve_original_application_baseline(self):
        def mutate(work, output, site):
            (work / 'conformance/corpus.json').write_text('changed baseline fixture')
            (site / 'statelessness/core.py').unlink()
        report = self.failed_attempt(RESEARCH, 'baseline', mutate, launch_error=True)
        self.assertIn('corpus.json', report['input_trees']['baseline-inputs']['differences']['changed'])
        self.assertIn('statelessness/core.py', report['input_trees']['installed-python']['differences']['removed'])
        self.assertEqual(set(report['results']), {'python', 'go', 'typescript', 'rust'})
        self.assertIn('-B', report['baseline_runners'][0])


if __name__ == '__main__':
    unittest.main()

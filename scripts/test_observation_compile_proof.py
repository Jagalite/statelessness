"""Synthetic compile-provenance regressions. Run only in parent-frozen Kyoto work.

All files and Cargo messages below are hand-authored validator inputs. The tool
and binary files contain inert text, are never executed, and prove NO successful
compilation, Rust test, public consumer, or engine behavior. No goldens are made
by invoking an implementation. No Git repository or external process is needed.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


q = _module('observation_proof_test_driver', ROOT / 'scripts/check-native-observation-consumers.py')
proof = _module('observation_proof_under_test', ROOT / 'scripts/observation_compile_proof.py')


class SyntheticCompile:
    """Complete structural baseline; success means validator admission only."""
    def __init__(self, *, rustup=False, metadata=False, resolution=True):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-observation-proof-')
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'frozen root'
        self.work = self.base / 'compile-work'
        self.target = self.base / 'cargo-target'
        for directory in (self.root, self.work, self.target):
            directory.mkdir()
        self.output = self.root / q.RECEIPTS / 'synthetic-compile'
        self.output.mkdir(parents=True)
        self.receipt_path = self.output / 'receipt.json'
        self.pin = '1' * 40
        sources = {
            'Cargo.toml': b'[package]\nname = "statelessness"\nversion = "0.2.0"\nedition = "2024"\n',
            'validation/corpus/Cargo.toml': b'[package]\nname = "stateless-corpus"\nversion = "0.1.0"\nedition = "2024"\n',
            'Cargo.lock': b'# Synthetic validator input; never passed to Cargo.\nversion = 4\n',
            'validation/corpus/Cargo.lock': b'# Synthetic validator input.\nversion = 4\n',
            'build.rs': b'// Synthetic source identity, never compiled.\n',
            'src/observation.rs': b'// Synthetic observation source, never compiled.\n',
            'src/monitor.rs': b'// Synthetic monitor source, never compiled.\n',
            str(q.FIXTURE): b'{"synthetic":"validator-only, not an engine fixture"}\n',
        }
        for name in q.RUST_TESTS:
            sources['tests/' + name + '.rs'] = ('// Synthetic ' + name + '\n').encode()
        for name in ('native-observation', 'native-checked'):
            sources['validation/corpus/src/bin/' + name + '.rs'] = ('// Synthetic ' + name + '\n').encode()
        for name, raw in sources.items():
            self.write(self.root / name, raw)
        self.sources = {name: q.identity(self.root / name) for name in sources}
        source = q.manifest(self.root, self.sources)
        fixture = dict(path=str(q.FIXTURE), **q.identity(self.root / q.FIXTURE))
        self.inputs, self.tools = {}, {}
        for name in ('cargo', 'rustc'):
            path = self.base / 'tools' / name
            self.write(path, ('INERT SYNTHETIC TOOL: ' + name + '\n').encode(), executable=True)
            row = q.executable_identity(path)
            self.tools[name] = row
            self.inputs['tool:' + name] = self.watched(row)
        self.resolved = {}
        rustup_path = self.base / 'tools/rustup'
        self.write(rustup_path, b'INERT SYNTHETIC RUSTUP\n', executable=True)
        self.tools['rustup'] = q.executable_identity(rustup_path)
        for name in ('cargo', 'rustc', 'rustdoc'):
            if rustup or name == 'rustdoc':
                resolved = self.base / 'toolchains/synthetic/bin' / name
                self.write(resolved, ('INERT RESOLVED TOOL: ' + name + '\n').encode(), executable=True)
                self.resolved[name] = q.executable_identity(resolved)
                if rustup and name in ('cargo', 'rustc'):
                    shim = self.base / 'tools' / name
                    shim.unlink()
                    if rustup == 'copy':
                        self.write(shim, rustup_path.read_bytes(), executable=True)
                    else:
                        shim.symlink_to(rustup_path)
                    self.tools[name] = q.executable_identity(shim)
                    self.inputs['tool:' + name] = self.watched(self.tools[name])
            else:
                self.resolved[name] = copy.deepcopy(self.tools[name])
            self.inputs['resolved-tool:' + name] = self.watched(self.resolved[name])
        self.binaries, self.artifacts = {}, {}
        for name in (*q.RUST_TESTS, 'native-observation', 'native-checked'):
            is_test = name in q.RUST_TESTS
            path = self.target / 'debug' / ('deps' if is_test else '') / (name + ('-synthetic' if is_test else ''))
            self.write(path, ('INERT SYNTHETIC BINARY: ' + name + '\n').encode(), executable=True)
            row = q.executable_identity(path)
            self.binaries[name] = row
            self.inputs['compiled:' + name] = self.watched(row)
            package_root = self.root if is_test else self.root / 'validation/corpus'
            package = 'statelessness@0.2.0' if is_test else 'stateless-corpus@0.1.0'
            src = self.root / 'tests' / (name + '.rs') if is_test else package_root / 'src/bin' / (name + '.rs')
            self.artifacts[name] = dict(reason='compiler-artifact',
                package_id='path+' + package_root.as_uri() + '#' + package,
                manifest_path=str(package_root / 'Cargo.toml'),
                target=dict(name=name, kind=['test'] if is_test else ['bin'], crate_types=['bin'],
                            src_path=str(src), edition='2024', doc=False, doctest=False, test=True),
                profile=dict(opt_level='0', debuginfo=2, debug_assertions=True,
                             overflow_checks=True, test=is_test),
                features=[], filenames=[str(path)], executable=str(path), fresh=False)
        initial = dict(schema='statelessness.observation-qualification.v1', scope='rust-compile',
            started_ns=1, source_root=str(self.root), workspace=str(self.work), requested_commit=self.pin,
            commands=[], status='running', language_executed_cases={}, validator_controls={}, scope_gaps=[])
        self.write(self.output / 'attempt.json', q.canonical(initial))
        self.receipt = dict(copy.deepcopy(initial), expected_commit=self.pin,
            committed_source=source, source_before=source, source_after=source, fixture=fixture,
            source_unchanged=True, status='passed', runtime_executed=False, finished_ns=1000,
            rust_binaries=copy.deepcopy(self.binaries), rust_compiler_artifacts=copy.deepcopy(self.artifacts),
            input_identities=q.manifest(self.root, self.inputs),
            compile_artifacts=q.manifest(self.root, q.tree_identity(self.target)))
        self.env = dict(PATH=str(self.base / 'tools'), CARGO_TARGET_DIR=str(self.target), CARGO_INCREMENTAL='0')
        if resolution:
            for name in ('cargo', 'rustc', 'rustdoc'):
                self.add_command('resolve-' + name, [self.tools['rustup']['path'], 'which', name],
                                 (self.resolved[name]['path'] + '\n').encode(), tool='rustup')
        self.add_command('cargo-version', [self.tools['cargo']['path'], '-Vv'],
                         b'cargo 0.0.0 (synthetic validator input)\nhost: synthetic\n', tool='cargo')
        self.add_command('rustc-version', [self.tools['rustc']['path'], '-vV'],
                         b'rustc 0.0.0 (synthetic validator input)\nhost: synthetic\n', tool='rustc')
        if metadata:
            self.receipt['cargo_target'] = str(self.target)
            self.receipt['cargo_packages'] = {}
            dependency_root = self.base / 'synthetic-dependency'
            self.write(dependency_root / 'Cargo.toml', b'# Synthetic dependency identity, not compiled.\n')
            dependency = dict(id='registry+https://example.invalid#synthetic@0.0.0', name='synthetic',
                              version='0.0.0', manifest_path=str(dependency_root / 'Cargo.toml'))
            self.inputs['cargo-dependency:' + dependency['id']] = self.watched(
                dict(path=str(dependency_root), tree=q.tree_identity(dependency_root)))
            self.receipt['input_identities'] = q.manifest(self.root, self.inputs)
            for label, relative, binary, package_name, version in (
                    ('tests', 'Cargo.toml', 'observation', 'statelessness', '0.2.0'),
                    ('consumers', 'validation/corpus/Cargo.toml', 'native-observation', 'stateless-corpus', '0.1.0')):
                package = dict(id=self.artifacts[binary]['package_id'], name=package_name, version=version,
                               manifest_path=str(self.root / relative))
                self.receipt['cargo_packages'][label] = package['id']
                self.add_command('cargo-metadata-' + label, [self.tools['cargo']['path'], 'metadata',
                    '--locked', '--offline', '--format-version=1', '--manifest-path', relative],
                    q.canonical(dict(packages=[package, dependency], version=1)))
        argv = [self.tools['cargo']['path'], 'test', '--locked', '--offline', '-p', 'statelessness',
                '-j', '2', '--no-run', '--message-format=json-render-diagnostics']
        for name in q.RUST_TESTS:
            argv += ['--test', name]
        # Legitimate incidental records must not widen the admitted executable set.
        incidental = dict(reason='compiler-artifact', target=dict(name='stateless'),
                          executable=None, filenames=['synthetic-libstateless.rlib'])
        records = [incidental, *(self.artifacts[name] for name in q.RUST_TESTS),
                   dict(reason='build-finished', success=True)]
        self.add_command('rust-tests-compile', argv, b''.join(q.canonical(row) for row in records))
        argv = [self.tools['cargo']['path'], 'build', '--locked', '--offline', '--manifest-path',
                'validation/corpus/Cargo.toml', '--bin', 'native-observation', '--bin', 'native-checked',
                '-j', '2', '--message-format=json-render-diagnostics']
        records = [self.artifacts['native-observation'], self.artifacts['native-checked'],
                   dict(reason='build-finished', success=True)]
        self.add_command('rust-public-consumer-compile', argv, b''.join(q.canonical(row) for row in records))
        self.seal_logs()
        self.watches = {}
        self.attempt = SimpleNamespace(root=self.root, initial=copy.deepcopy(self.sources),
            report=dict(expected_commit=self.pin, committed_source=source, source_before=source, fixture=fixture),
            watch=lambda name, path: self.watches.update({name: Path(path)}))
        self.publish()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.temp.cleanup()

    @staticmethod
    def write(path, raw, executable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(0o755 if executable else 0o644)

    @staticmethod
    def watched(row):
        return dict(before=copy.deepcopy(row), after=copy.deepcopy(row), unchanged=True)

    def add_command(self, name, argv, raw, tool='cargo'):
        index = len(self.receipt['commands']) + 1
        entry = dict(name=name, argv=argv, cwd=str(self.root), started_ns=index * 10,
            executable_before=copy.deepcopy(self.tools[tool]), environment_overlay={}, environment=dict(self.env),
            process_id=123, inherited_process_group=120, owns_process_group=False, timed_out=False,
            raw_logs_stable=True, process_tree_termination_confirmed=True,
            process_ownership=dict(schema='statelessness.owned-process.v1', backend='synthetic', samples=1,
                observed=[dict(pid=123, process_group=120, ancestor=None, state='gone')], signals=[], errors=[]),
            exit_code=0, finished_ns=index * 10 + 1,
            executable_after=copy.deepcopy(self.tools[tool]), executable_unchanged=True,
            source_after=self.receipt['source_before'])
        directory = self.output / 'commands' / f'{index:04d}-{name}'
        for stream, data in (('stdout', raw), ('stderr', b'\x00\xffsynthetic diagnostic\n')):
            path = directory / (stream + '.bin')
            self.write(path, data)
            entry[stream] = dict(path=str(path.relative_to(self.output)), **q.identity(path))
        self.receipt['commands'].append(entry)
        self.journal(entry)

    def command(self, name):
        return next(entry for entry in self.receipt['commands'] if entry['name'] == name)

    def journal(self, entry):
        directory = (self.output / entry['stdout']['path']).parent
        started = {key: copy.deepcopy(entry[key]) for key in
                   ('name', 'argv', 'cwd', 'started_ns', 'executable_before', 'environment_overlay', 'environment')}
        for key in ('staged_inputs_expected', 'staged_inputs_before'):
            if key in entry:
                started[key] = copy.deepcopy(entry[key])
        self.write(directory / 'started.json', q.canonical(started))
        self.write(directory / 'finished.json', q.canonical(entry))

    def seal_logs(self):
        rows = {entry[stream]['path']: {k: v for k, v in entry[stream].items() if k != 'path'}
                for entry in self.receipt['commands'] for stream in ('stdout', 'stderr')}
        self.receipt['raw_logs'] = q.manifest(self.root, rows)

    def rewrite_stdout(self, name, raw):
        entry = self.command(name)
        path = self.output / entry['stdout']['path']
        self.write(path, raw)
        entry['stdout'] = dict(path=entry['stdout']['path'], **q.identity(path))
        self.journal(entry)
        self.seal_logs()

    def records(self, name):
        path = self.output / self.command(name)['stdout']['path']
        return [q.strict_json(line) for line in path.read_bytes().splitlines()]

    def replace_records(self, name, records):
        self.rewrite_stdout(name, b''.join(q.canonical(row) for row in records))

    def publish(self):
        self.write(self.receipt_path, q.canonical(self.receipt))

    def validate(self):
        self.publish()
        # The helper is intentionally pure file admission: prohibit accidental
        # Git, compiler, test or engine process execution even in these tests.
        with (patch.object(q, 'git', side_effect=AssertionError('no git execution')),
              patch.object(q.subprocess, 'run', side_effect=AssertionError('no process execution')),
              patch.object(q.subprocess, 'Popen', side_effect=AssertionError('no process execution'))):
            return proof.validate_compile(q, self.attempt, self.receipt_path, self.receipt)


class CompileProofTests(unittest.TestCase):
    def test_complete_synthetic_chain_admits_exact_eight_rows_without_execution(self):
        with SyntheticCompile() as fixture:
            self.assertEqual(fixture.validate(), fixture.binaries)
            self.assertEqual(set(fixture.binaries), set(q.RUST_TESTS) | {'native-observation', 'native-checked'})
            self.assertIn('rust:native-checked', fixture.watches)
            for filename in ('attempt.json', 'receipt.json'):
                self.assertIn(fixture.output / filename, fixture.watches.values())
            self.assertIn(fixture.output / fixture.command('rust-tests-compile')['stderr']['path'],
                          fixture.watches.values())

    def test_rustup_metadata_and_current_producer_artifact_field_are_admitted(self):
        with SyntheticCompile(rustup=True, metadata=True) as fixture:
            fixture.receipt['rust_artifacts'] = fixture.receipt.pop('rust_compiler_artifacts')
            self.assertEqual(fixture.validate(), fixture.binaries)

    def test_regular_file_rustup_proxies_need_resolution_too(self):
        with SyntheticCompile(rustup='copy') as fixture:
            self.assertFalse(Path(fixture.tools['cargo']['path']).is_symlink())
            self.assertEqual(fixture.validate(), fixture.binaries)
        with SyntheticCompile(rustup='copy', resolution=False) as fixture:
            with self.assertRaisesRegex(ValueError, 'missing complete rustup compiler resolution'):
                fixture.validate()

    def test_artifact_aliases_and_cargo_target_cannot_disagree(self):
        for mutation in ('aliases', 'target'):
            with self.subTest(mutation=mutation), SyntheticCompile(metadata=True) as fixture:
                if mutation == 'aliases':
                    fixture.receipt['rust_artifacts'] = copy.deepcopy(fixture.artifacts)
                    fixture.receipt['rust_artifacts']['observation']['fresh'] = True
                else: fixture.receipt['cargo_target'] = str(fixture.work)
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_rustup_resolution_requires_retained_matching_actual_tools(self):
        for mutation in ('resolved-output', 'missing-resolved-watch', 'wrong-resolution-argv'):
            with self.subTest(mutation=mutation), SyntheticCompile(rustup=True) as fixture:
                if mutation == 'resolved-output':
                    fixture.rewrite_stdout('resolve-rustc', b'/not/the/retained/rustc\n')
                elif mutation == 'missing-resolved-watch':
                    rows = copy.deepcopy(fixture.inputs); del rows['resolved-tool:rustc']
                    fixture.receipt['input_identities'] = q.manifest(fixture.root, rows)
                else:
                    entry = fixture.command('resolve-rustc'); entry['argv'][-1] = 'cargo'
                    fixture.journal(entry)
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_metadata_package_and_dependency_proof_cannot_be_substituted(self):
        for mutation in ('package', 'claimed-package', 'dependency-watch', 'metadata-argv'):
            with self.subTest(mutation=mutation), SyntheticCompile(metadata=True) as fixture:
                if mutation == 'package':
                    entry = fixture.command('cargo-metadata-tests')
                    metadata = q.strict_json((fixture.output / entry['stdout']['path']).read_bytes())
                    metadata['packages'][0]['version'] = '9.9.9'
                    fixture.rewrite_stdout('cargo-metadata-tests', q.canonical(metadata))
                elif mutation == 'claimed-package': fixture.receipt['cargo_packages']['tests'] = 'other'
                elif mutation == 'dependency-watch':
                    rows = {key: value for key, value in fixture.inputs.items() if not key.startswith('cargo-dependency:')}
                    fixture.receipt['input_identities'] = q.manifest(fixture.root, rows)
                else:
                    entry = fixture.command('cargo-metadata-tests'); entry['argv'].remove('--locked')
                    fixture.journal(entry)
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_inherited_group_and_stable_logs_require_confirmed_cleanup(self):
        for key, value in (('owns_process_group', True), ('raw_logs_stable', False),
                           ('raw_logs_stable', 1), ('process_tree_termination_confirmed', False),
                           ('inherited_process_group', None), ('process_ownership', None)):
            with self.subTest(key=key, value=value), SyntheticCompile() as fixture:
                entry = fixture.command('rust-tests-compile'); entry[key] = value
                fixture.journal(entry)
                with self.assertRaises(ValueError):
                    fixture.validate()
        with SyntheticCompile() as fixture:
            entry = fixture.command('rust-tests-compile'); del entry['process_tree_termination_confirmed']
            fixture.journal(entry)
            with self.assertRaisesRegex(ValueError, 'unconfirmed process cleanup'):
                fixture.validate()

    def test_process_ownership_cannot_retain_live_or_unidentified_log_owners(self):
        for mutation in ('live', 'wrong-group', 'errors', 'signals'):
            with self.subTest(mutation=mutation), SyntheticCompile() as fixture:
                entry = fixture.command('rust-tests-compile')
                ownership = entry['process_ownership']
                if mutation == 'live': ownership['observed'][0]['state'] = 'live'
                elif mutation == 'wrong-group': ownership['observed'][0]['process_group'] += 1
                elif mutation == 'errors': ownership['errors'] = ['identity lookup failed']
                else: ownership['signals'] = [dict(pid=123, signal=15)]
                fixture.journal(entry)
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_optional_staged_start_fields_are_bound_to_journal_and_after(self):
        with SyntheticCompile() as fixture:
            entry = fixture.command('rust-tests-compile')
            empty = q.manifest(fixture.root, {})
            entry.update(staged_inputs_expected=empty, staged_inputs_before=empty, staged_inputs_after=empty)
            fixture.journal(entry)
            self.assertEqual(fixture.validate(), fixture.binaries)
            entry['staged_inputs_after'] = q.manifest(fixture.root, {'changed': {'identity': 'synthetic'}})
            fixture.journal(entry)
            with self.assertRaisesRegex(ValueError, 'staged inputs changed'):
                fixture.validate()

    def test_receipt_only_head_fields_may_advance_with_same_pin_and_source(self):
        with SyntheticCompile() as fixture:
            # HEAD/tree verification itself belongs to Attempt.seal/finish. This
            # control ensures the helper does not accidentally add exact-HEAD equality.
            fixture.receipt['head_before'] = fixture.pin
            fixture.attempt.report['head_before'] = '2' * 40
            self.assertEqual(fixture.validate(), fixture.binaries)

    def test_changed_expected_pin_is_rejected(self):
        with SyntheticCompile() as fixture:
            fixture.attempt.report['expected_commit'] = '2' * 40
            with self.assertRaisesRegex(ValueError, 'source pin differs'):
                fixture.validate()

    def test_missing_attempt_and_command_evidence_is_rejected(self):
        for filename in ('attempt.json', 'started.json', 'finished.json', 'stdout.bin', 'stderr.bin'):
            with self.subTest(filename=filename), SyntheticCompile() as fixture:
                directory = (fixture.output / fixture.command('rust-tests-compile')['stdout']['path']).parent
                path = fixture.output / filename if filename == 'attempt.json' else directory / filename
                path.unlink()
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_tampered_raw_streams_are_rejected_without_rewriting_them(self):
        for stream in ('stdout', 'stderr'):
            with self.subTest(stream=stream), SyntheticCompile() as fixture:
                path = fixture.output / fixture.command('rust-tests-compile')[stream]['path']
                raw = b'\xff\x00truncated or altered retained log\n'
                path.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, 'raw log bytes differ'):
                    fixture.validate()
                self.assertEqual(path.read_bytes(), raw)

    def test_failed_command_rejected_even_when_every_journal_and_hash_agrees(self):
        for change in ({'exit_code': 1}, {'exit_code': False}, {'timed_out': True},
                       {'error': 'synthetic compiler failure'}, {'identity_error': 'synthetic identity failure'}):
            with self.subTest(change=change), SyntheticCompile() as fixture:
                entry = fixture.command('rust-tests-compile')
                entry.update(change)
                fixture.journal(entry)
                path = fixture.output / entry['stderr']['path']
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, 'did not finish successfully'):
                    fixture.validate()
                self.assertEqual(path.read_bytes(), before)

    def test_original_attempt_identity_and_journals_must_agree(self):
        for filename, key, value in (
                ('attempt.json', 'workspace', '/different-workspace'),
                ('attempt.json', 'requested_commit', '2' * 40),
                ('started.json', 'cwd', '/different-cwd'),
                ('finished.json', 'exit_code', 9)):
            with self.subTest(filename=filename, key=key), SyntheticCompile() as fixture:
                directory = (fixture.output / fixture.command('rust-tests-compile')['stdout']['path']).parent
                path = fixture.output / filename if filename == 'attempt.json' else directory / filename
                row = q.strict_json(path.read_bytes()); row[key] = value
                path.write_bytes(q.canonical(row))
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_cargo_arguments_cwd_target_and_tool_binding_are_required(self):
        for mutation in ('no-run', 'wrong-suite', 'missing-checked', 'cwd', 'tool', 'target', 'incremental'):
            with self.subTest(mutation=mutation), SyntheticCompile() as fixture:
                entry = fixture.command('rust-tests-compile')
                if mutation == 'no-run': entry['argv'].remove('--no-run')
                elif mutation == 'wrong-suite': entry['argv'][-1] = 'unrelated'
                elif mutation == 'missing-checked':
                    entry = fixture.command('rust-public-consumer-compile')
                    offset = entry['argv'].index('native-checked'); del entry['argv'][offset - 1:offset + 1]
                elif mutation == 'cwd': entry['cwd'] = str(fixture.work)
                elif mutation == 'tool': entry['argv'][0] = fixture.tools['rustc']['path']
                elif mutation == 'target': entry['environment']['CARGO_TARGET_DIR'] = str(fixture.work)
                else: entry['environment']['CARGO_INCREMENTAL'] = '1'
                fixture.journal(entry)
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_tool_resolution_must_share_cargo_checkout_cwd(self):
        with SyntheticCompile() as fixture:
            entry = fixture.command('rustc-version'); entry['cwd'] = str(fixture.work)
            fixture.journal(entry)
            with self.assertRaisesRegex(ValueError, 'identity command used wrong cwd'):
                fixture.validate()

    def test_toolchain_selection_environment_must_match_compilation(self):
        with SyntheticCompile(rustup=True) as fixture:
            entry = fixture.command('resolve-rustc')
            entry['environment']['RUSTUP_TOOLCHAIN'] = 'different-toolchain'
            fixture.journal(entry)
            with self.assertRaisesRegex(ValueError, 'environments differ'):
                fixture.validate()

    def test_missing_and_changed_tool_watch_are_rejected(self):
        for mutation in ('missing', 'changed', 'false-unchanged'):
            with self.subTest(mutation=mutation), SyntheticCompile() as fixture:
                rows = copy.deepcopy(fixture.inputs)
                if mutation == 'missing': del rows['tool:rustc']
                elif mutation == 'changed':
                    rows['tool:rustc']['after']['file']['sha256'] = '0' * 64
                else: rows['tool:rustc']['unchanged'] = 1
                fixture.receipt['input_identities'] = q.manifest(fixture.root, rows)
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_manifests_require_retained_bytes_canonical_content_and_content_address(self):
        for mutation in ('missing', 'changed-bytes', 'wrong-size', 'parent', 'absolute', 'symlink'):
            with self.subTest(mutation=mutation), SyntheticCompile() as fixture:
                ref = fixture.receipt['compile_artifacts']
                path = fixture.root / ref['path']
                if mutation == 'missing': path.unlink()
                elif mutation == 'changed-bytes': path.write_bytes(b'{}\n')
                elif mutation == 'wrong-size': ref['size_bytes'] += 1
                elif mutation == 'parent': ref['path'] = '../' + ref['path']
                elif mutation == 'absolute': ref['path'] = str(path)
                else:
                    other = fixture.base / 'outside-manifest.json'; other.write_bytes(path.read_bytes())
                    path.unlink(); path.symlink_to(other)
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_even_extra_manifest_references_must_be_validated(self):
        with SyntheticCompile() as fixture:
            fixture.receipt['extra_metadata'] = {'nested': dict(path=str(q.RECEIPTS / 'manifests' / ('f' * 64 + '.json')),
                                                               sha256='f' * 64, size_bytes=3)}
            with self.assertRaises(ValueError):
                fixture.validate()

    def test_rehashed_wrong_source_manifests_are_rejected_semantically(self):
        for location in ('committed_source', 'source_before', 'source_after', 'head_before_source', 'command'):
            with self.subTest(location=location), SyntheticCompile() as fixture:
                wrong = copy.deepcopy(fixture.sources)
                wrong['src/monitor.rs']['sha256'] = '0' * 64
                ref = q.manifest(fixture.root, wrong)
                if location == 'command':
                    entry = fixture.command('rust-tests-compile'); entry['source_after'] = ref
                    fixture.journal(entry)
                else: fixture.receipt[location] = ref
                with self.assertRaisesRegex(ValueError, 'source.*differs'):
                    fixture.validate()

    def test_source_and_fixture_current_bytes_modes_and_paths_are_checked(self):
        for mutation in ('source', 'mode', 'fixture', 'fixture-claim', 'unsafe-member'):
            with self.subTest(mutation=mutation), SyntheticCompile() as fixture:
                if mutation == 'source': (fixture.root / 'src/monitor.rs').write_bytes(b'changed\n')
                elif mutation == 'mode': (fixture.root / 'src/monitor.rs').chmod(0o755)
                elif mutation == 'fixture': (fixture.root / q.FIXTURE).write_bytes(b'changed\n')
                elif mutation == 'fixture-claim': fixture.receipt['fixture']['sha256'] = '0' * 64
                else:
                    rows = copy.deepcopy(fixture.sources)
                    rows['../outside'] = rows.pop('src/monitor.rs')
                    ref = q.manifest(fixture.root, rows)
                    for key in ('committed_source', 'source_before', 'source_after'):
                        fixture.receipt[key] = ref
                    fixture.attempt.initial = rows
                    fixture.attempt.report.update(committed_source=ref, source_before=ref)
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_rehashed_cargo_logs_require_strict_json_and_successful_final_record(self):
        for mutation in ('bad-json', 'duplicate-key', 'no-newline', 'missing-finish', 'false-finish', 'numeric-success'):
            with self.subTest(mutation=mutation), SyntheticCompile() as fixture:
                name = 'rust-tests-compile'
                rows = fixture.records(name)
                if mutation == 'bad-json': raw = b'{"reason":\n'
                elif mutation == 'duplicate-key': raw = b'{"reason":"build-finished","success":true,"success":false}\n'
                elif mutation == 'no-newline': raw = b''.join(q.canonical(row) for row in rows).rstrip(b'\n')
                else:
                    if mutation == 'missing-finish': rows.pop()
                    elif mutation == 'false-finish': rows[-1]['success'] = False
                    else: rows[-1]['success'] = 1
                    raw = b''.join(q.canonical(row) for row in rows)
                fixture.rewrite_stdout(name, raw)
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_missing_duplicate_and_fabricated_artifact_claims_are_rejected(self):
        for mutation in ('missing-record', 'duplicate-record', 'fabricated-row', 'missing-checked', 'extra-suite'):
            with self.subTest(mutation=mutation), SyntheticCompile() as fixture:
                name = 'rust-tests-compile'
                rows = fixture.records(name)
                if mutation == 'missing-record':
                    rows = [r for r in rows if r.get('target', {}).get('name') != 'observation']
                elif mutation == 'duplicate-record': rows.insert(1, copy.deepcopy(fixture.artifacts['observation']))
                elif mutation == 'fabricated-row':
                    fixture.receipt['rust_compiler_artifacts']['observation']['fresh'] = True
                elif mutation == 'missing-checked': del fixture.receipt['rust_binaries']['native-checked']
                else: fixture.receipt['rust_binaries']['unrelated'] = copy.deepcopy(fixture.binaries['monitor'])
                fixture.replace_records(name, rows)
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_package_source_target_profile_and_manifest_are_independently_bound(self):
        for mutation in ('package', 'manifest', 'source', 'kind', 'crate-type', 'edition', 'profile', 'profile-int'):
            with self.subTest(mutation=mutation), SyntheticCompile() as fixture:
                name = 'rust-tests-compile'
                rows = fixture.records(name)
                artifact = next(row for row in rows if row.get('target', {}).get('name') == 'observation')
                if mutation == 'package': artifact['package_id'] = artifact['package_id'].replace('statelessness@', 'unrelated@')
                elif mutation == 'manifest': artifact['manifest_path'] = str(fixture.root / 'validation/corpus/Cargo.toml')
                elif mutation == 'source': artifact['target']['src_path'] = str(fixture.root / 'tests/monitor.rs')
                elif mutation == 'kind': artifact['target']['kind'] = ['bin']
                elif mutation == 'crate-type': artifact['target']['crate_types'] = ['rlib']
                elif mutation == 'edition': artifact['target']['edition'] = '2021'
                elif mutation == 'profile': artifact['profile']['test'] = 'true'
                else: artifact['profile']['test'] = 1
                fixture.receipt['rust_compiler_artifacts']['observation'] = copy.deepcopy(artifact)
                fixture.replace_records(name, rows)
                with self.assertRaisesRegex(ValueError, 'wrong Cargo package/source/target/profile'):
                    fixture.validate()

    def test_public_consumer_cannot_be_a_test_profile_or_swap_streams(self):
        for mutation in ('test-profile', 'wrong-stream'):
            with self.subTest(mutation=mutation), SyntheticCompile() as fixture:
                name = 'rust-public-consumer-compile'
                rows = fixture.records(name)
                if mutation == 'test-profile':
                    rows[0]['profile']['test'] = True
                    fixture.receipt['rust_compiler_artifacts']['native-observation'] = copy.deepcopy(rows[0])
                    fixture.replace_records(name, rows)
                else:
                    tests = fixture.records('rust-tests-compile'); tests.insert(-1, rows.pop(0))
                    fixture.replace_records(name, rows)
                    fixture.replace_records('rust-tests-compile', tests)
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_executable_bytes_mode_and_link_changes_are_rejected(self):
        for mutation in ('bytes', 'mode', 'symlink'):
            with self.subTest(mutation=mutation), SyntheticCompile() as fixture:
                path = Path(fixture.binaries['observation']['path'])
                if mutation == 'bytes': path.write_bytes(b'changed executable bytes\n')
                elif mutation == 'mode': path.chmod(0o644)
                else:
                    path.unlink(); path.symlink_to(fixture.binaries['monitor']['path'])
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_artifact_path_filenames_shared_binary_and_compiled_watch_are_bound(self):
        for mutation in ('artifact-path', 'filenames', 'shared-binary', 'watch', 'artifact-manifest'):
            with self.subTest(mutation=mutation), SyntheticCompile() as fixture:
                name = 'rust-tests-compile'
                rows = fixture.records(name)
                artifact = next(row for row in rows if row.get('target', {}).get('name') == 'observation')
                if mutation == 'artifact-path': artifact['executable'] = fixture.binaries['monitor']['path']
                elif mutation == 'filenames': artifact['filenames'] = []
                elif mutation == 'shared-binary':
                    fixture.receipt['rust_binaries']['observation'] = copy.deepcopy(fixture.binaries['monitor'])
                elif mutation == 'watch':
                    watched = copy.deepcopy(fixture.inputs)
                    watched['compiled:observation'] = copy.deepcopy(watched['compiled:monitor'])
                    fixture.receipt['input_identities'] = q.manifest(fixture.root, watched)
                else:
                    tree = q.tree_identity(fixture.target)
                    del tree[str(Path(fixture.binaries['observation']['path']).relative_to(fixture.target))]
                    fixture.receipt['compile_artifacts'] = q.manifest(fixture.root, tree)
                fixture.receipt['rust_compiler_artifacts']['observation'] = copy.deepcopy(artifact)
                fixture.replace_records(name, rows)
                with self.assertRaises(ValueError):
                    fixture.validate()

    def test_complete_log_and_command_inventory_is_required(self):
        for mutation in ('extra-log', 'extra-command', 'omitted-log-row', 'empty-commands'):
            with self.subTest(mutation=mutation), SyntheticCompile() as fixture:
                if mutation == 'extra-log': fixture.write(fixture.output / 'orphan/stdout.bin', b'failed evidence\n')
                elif mutation == 'extra-command': (fixture.output / 'commands/9999-orphan').mkdir()
                elif mutation == 'omitted-log-row':
                    ref = fixture.receipt['raw_logs']
                    rows = q.strict_json((fixture.root / ref['path']).read_bytes()); rows.pop(next(iter(rows)))
                    fixture.receipt['raw_logs'] = q.manifest(fixture.root, rows)
                else: fixture.receipt['commands'] = []
                with self.assertRaises(ValueError):
                    fixture.validate()


if __name__ == '__main__':
    unittest.main()

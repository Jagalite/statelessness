"""Read-only admission of retained N07 compile evidence; never executes a tool.

This checks consistency and retained bytes, not cryptographic attestation. The
caller must seal its current sources first. Synthetic unit receipts exercise this
validator only; they do not prove compilation or any engine behavior.
"""
from __future__ import annotations

from pathlib import Path
import re
import tomllib

PUBLIC_BINS = ('native-observation', 'native-checked')
START_FIELDS = {'name', 'argv', 'cwd', 'started_ns', 'executable_before',
                'environment_overlay', 'environment'}
OPTIONAL_START_FIELDS = {'staged_inputs_expected', 'staged_inputs_before'}
MANIFEST_FIELDS = {'path', 'sha256', 'size_bytes'}
SCHEMA = 'statelessness.observation-qualification.v1'


def _require(condition, message):
    if not condition:
        raise ValueError('compile proof: ' + message)


def _same(q, left, right):
    # JSON booleans must not compare equal to integer identities or exit codes.
    return q.canonical(left) == q.canonical(right)


def _relative(base, name, *, leaf_symlink=False):
    _require(isinstance(name, str) and bool(name), 'missing relative path')
    parts = name.split('/')
    _require(not name.startswith('/') and all(p not in ('', '.', '..') for p in parts),
             'unsafe relative path: ' + name)
    path = base
    for index, part in enumerate(parts):
        path = path / part
        if not (leaf_symlink and index == len(parts) - 1):
            _require(not path.is_symlink(), 'symlink in proof path: ' + name)
    _require(path.parent.resolve(strict=True).is_relative_to(base.resolve(strict=True)),
             'path escapes retained root: ' + name)
    return path


def _absolute(name):
    _require(isinstance(name, str) and Path(name).is_absolute(), 'missing absolute path')
    path = Path(name)
    _require(str(path) == name and '..' not in path.parts, 'noncanonical absolute path: ' + name)
    return path


class _Proof:
    def __init__(self, q, attempt):
        self.q, self.attempt = q, attempt
        self.root = attempt.root.resolve(strict=True)
        self.paths = set()
        self.manifests = {}
        self.loading = set()

    def json_file(self, path):
        _require(path.is_file() and not path.is_symlink(), 'missing regular proof file: ' + str(path))
        self.paths.add(path)
        return self.q.strict_json(path.read_bytes())

    def manifest(self, ref):
        _require(isinstance(ref, dict) and set(ref) == MANIFEST_FIELDS, 'invalid manifest reference')
        digest = ref['sha256']
        _require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), 'invalid manifest digest')
        name = str(self.q.RECEIPTS / 'manifests' / (digest + '.json'))
        _require(ref['path'] == name, 'manifest path does not match content address')
        path = _relative(self.root, name)
        _require(path.is_file(), 'missing regular manifest: ' + name)
        raw = path.read_bytes()
        _require(type(ref['size_bytes']) is int and ref['size_bytes'] == len(raw)
                 and self.q.sha(raw) == digest, 'manifest bytes differ: ' + name)
        self.paths.add(path)
        if name in self.manifests:
            return self.manifests[name]
        _require(name not in self.loading, 'cyclic manifest reference')
        self.loading.add(name)
        value = self.q.strict_json(raw)
        _require(isinstance(value, dict) and self.q.canonical(value) == raw,
                 'manifest is not a canonical object: ' + name)
        self.references(value)
        self.loading.remove(name)
        self.manifests[name] = value
        return value

    def references(self, value):
        if isinstance(value, dict):
            if (set(value) == MANIFEST_FIELDS or
                    (isinstance(value.get('path'), str) and
                     value['path'].startswith(str(self.q.RECEIPTS / 'manifests') + '/'))):
                self.manifest(value)
            else:
                for nested in value.values():
                    self.references(nested)
        elif isinstance(value, list):
            for nested in value:
                self.references(nested)

    def tree(self, base, rows, *, sources=False):
        _require(isinstance(rows, dict) and bool(rows), 'missing retained file manifest')
        for name, row in rows.items():
            if sources:
                _require(not self.q.receipt_path(name), 'receipt included in sealed source manifest')
            path = _relative(base, name, leaf_symlink=True)
            _require(_same(self.q, self.q.identity(path), row), 'manifest file differs: ' + str(path))

    def executable(self, row, label):
        _require(isinstance(row, dict) and set(row) == {'path', 'resolved', 'link', 'file'},
                 'invalid executable identity: ' + label)
        path = _absolute(row['path'])
        _require(_same(self.q, self.q.executable_identity(path), row), 'executable changed: ' + label)
        _require(row['file']['type'] == 'regular' and row['file']['mode'] & 0o111,
                 'file is not executable: ' + label)
        return path


def _command(proof, output, index, entry, sources, previous_ns, finished_ns):
    q = proof.q
    _require(isinstance(entry, dict), 'invalid command row')
    name = entry.get('name')
    _require(isinstance(name, str) and re.fullmatch('[a-z0-9-]+', name), 'invalid command name')
    relative = 'commands/' + f'{index:04d}-{name}'
    directory = _relative(output, relative)
    started = proof.json_file(_relative(directory, 'started.json'))
    finished = proof.json_file(_relative(directory, 'finished.json'))
    start_keys = START_FIELDS | (OPTIONAL_START_FIELDS & entry.keys())
    _require(isinstance(started, dict) and set(started) == start_keys,
             'invalid started command metadata: ' + name)
    _require(_same(q, started, {key: entry.get(key) for key in start_keys}),
             'started command differs from receipt: ' + name)
    _require(_same(q, finished, entry), 'finished command differs from receipt: ' + name)
    _require(type(entry.get('started_ns')) is int and type(entry.get('finished_ns')) is int
             and previous_ns <= entry['started_ns'] <= entry['finished_ns'] <= finished_ns,
             'invalid command ordering/timestamps: ' + name)
    _require(type(entry.get('exit_code')) is int and entry['exit_code'] == 0
             and entry.get('timed_out') is False
             and not any(key in entry for key in ('error', 'identity_error', 'wait_error', 'cleanup_error')),
             'command did not finish successfully: ' + name)
    _require(type(entry.get('process_id')) is int and entry['process_id'] > 0
             and type(entry.get('inherited_process_group')) is int and entry['inherited_process_group'] > 0
             and entry.get('owns_process_group') is False,
             'missing inherited process group metadata: ' + name)
    ownership = entry.get('process_ownership')
    _require(entry.get('raw_logs_stable') is True and entry.get('process_tree_termination_confirmed') is True
             and isinstance(ownership, dict) and ownership.get('schema') == 'statelessness.owned-process.v1'
             and ownership.get('errors') == [] and ownership.get('signals') == []
             and type(ownership.get('samples')) is int and ownership['samples'] > 0
             and isinstance(ownership.get('observed'), list) and ownership['observed']
             and all(row.get('state') in ('gone', 'zombie', 'pid_reused') for row in ownership['observed']),
             'unconfirmed process cleanup or unstable raw logs: ' + name)
    _require(any(row.get('pid') == entry['process_id'] and row.get('ancestor') is None
                 and row.get('process_group') == entry['inherited_process_group'] for row in ownership['observed']),
             'process ownership omits inherited-group command identity: ' + name)
    _require(entry.get('executable_unchanged') is True
             and _same(q, entry.get('executable_before'), entry.get('executable_after')),
             'command executable was not stable: ' + name)
    proof.executable(entry['executable_before'], name)
    _require(_same(q, proof.manifest(entry.get('source_after')), sources),
             'command source manifest differs: ' + name)
    for key in OPTIONAL_START_FIELDS & start_keys:
        expected = proof.manifest(entry[key])
        if expected or 'staged_inputs_after' in entry:
            _require(_same(q, expected, proof.manifest(entry.get('staged_inputs_after'))),
                     'command staged inputs changed: ' + name)
    argv = entry.get('argv')
    _require(isinstance(argv, list) and argv and all(isinstance(s, str) and s for s in argv),
             'invalid command arguments: ' + name)
    _absolute(entry['cwd'])
    _require(isinstance(entry.get('environment'), dict)
             and entry.get('environment_overlay') == {}, 'unexpected compile environment overlay: ' + name)
    logs = {}
    for stream in ('stdout', 'stderr'):
        row = entry.get(stream)
        expected = relative + '/' + stream + '.bin'
        _require(isinstance(row, dict) and row.get('path') == expected, 'wrong raw log path: ' + name)
        path = _relative(output, expected)
        actual = q.identity(path)
        _require(actual['type'] == 'regular' and _same(q, row, dict(path=expected, **actual)),
                 'raw log bytes differ: ' + expected)
        proof.paths.add(path)
        logs[expected] = actual
    return logs


def _toolchain(proof, commands, inputs):
    q = proof.q
    for label, pair in inputs.items():
        _require(isinstance(pair, dict) and set(pair) == {'before', 'after', 'unchanged'}
                 and pair['unchanged'] is True and _same(q, pair['before'], pair['after']),
                 'watched input changed or incomplete: ' + label)
        before = pair['before']
        if 'file' in before:
            # Configuration watches use executable_identity too, but are not executable.
            _require(_same(q, q.executable_identity(_absolute(before['path'])), before),
                     'watched file changed: ' + label)
        else:
            _require(set(before) == {'path', 'tree'}, 'invalid watched tree: ' + label)
            base = _absolute(before['path'])
            proof.tree(base, before['tree'])
            _require(_same(q, q.tree_identity(base), before['tree']), 'watched tree membership changed: ' + label)
    for tool, flags in (('cargo', ['-Vv']), ('rustc', ['-vV'])):
        label = 'tool:' + tool
        _require(label in inputs, 'missing compiler tool identity: ' + tool)
        row = inputs[label]['before']
        proof.executable(row, label)
        version = commands[tool + '-version']
        _require(version['argv'] in ([row['path'], *flags], [tool, *flags])
                 and _same(q, version['executable_before'], row), 'wrong version command/tool: ' + tool)
        version_raw = (proof.output / version['stdout']['path']).read_bytes()
        _require(version_raw.decode('utf-8').startswith(tool + ' ') and version_raw.endswith(b'\n'),
                 'missing compiler version output: ' + tool)
    resolved = {'resolve-' + name for name in ('cargo', 'rustc', 'rustdoc')}
    present = resolved & commands.keys()
    # A hardlinked/copied rustup proxy is a regular file named cargo/rustc.
    # Basenames and symlink status cannot positively identify a direct compiler.
    _require(present == resolved, 'missing complete rustup compiler resolution')
    rustup = None
    for tool in ('cargo', 'rustc', 'rustdoc'):
        command = commands['resolve-' + tool]
        executable = command['executable_before']
        _require(command['argv'] == [executable['path'], 'which', tool], 'wrong rustup resolution command')
        if rustup is not None:
            _require(_same(q, executable, rustup), 'rustup executable changed across resolution')
        rustup = executable
        label = 'resolved-tool:' + tool
        _require(label in inputs, 'missing resolved compiler identity: ' + tool)
        row = inputs[label]['before']
        proof.executable(row, label)
        stdout = proof.output / command['stdout']['path']
        _require(stdout.read_bytes().decode('utf-8').strip() == row['path'],
                 'rustup output differs from resolved compiler: ' + tool)
        if tool in ('cargo', 'rustc'):
            invoked = inputs['tool:' + tool]['before']
            _require(_same(q, invoked['file'], row['file'])
                     or _same(q, invoked['file'], rustup['file']),
                     'invoked tool is neither the resolved tool nor the recorded rustup proxy: ' + tool)


def _artifacts(proof, receipt, commands, inputs, sources):
    q, root = proof.q, proof.root
    required = set(q.RUST_TESTS) | set(PUBLIC_BINS)
    binaries = receipt.get('rust_binaries')
    retained = receipt.get('rust_compiler_artifacts', receipt.get('rust_artifacts'))
    if 'rust_compiler_artifacts' in receipt and 'rust_artifacts' in receipt:
        _require(_same(q, receipt['rust_compiler_artifacts'], receipt['rust_artifacts']),
                 'retained Cargo artifact aliases disagree')
    _require(isinstance(binaries, dict) and set(binaries) == required,
             'missing or extra required Rust binaries')
    _require(isinstance(retained, dict) and set(retained) == required,
             'missing or extra retained Cargo artifact rows')
    _require(len({row['resolved'] for row in binaries.values()}) == len(required),
             'required binaries share an executable path')
    cargo = inputs['tool:cargo']['before']
    test_command = commands['rust-tests-compile']
    consumer_command = commands['rust-public-consumer-compile']
    test_args = ['test', '--locked', '--offline', '-p', 'statelessness', '-j', '2', '--no-run',
                 '--message-format=json-render-diagnostics']
    for name in q.RUST_TESTS:
        test_args += ['--test', name]
    consumer_args = ['build', '--locked', '--offline', '--manifest-path', 'validation/corpus/Cargo.toml']
    for name in PUBLIC_BINS:
        consumer_args += ['--bin', name]
    consumer_args += ['-j', '2', '--message-format=json-render-diagnostics']
    target = None
    for command, arguments in ((test_command, test_args), (consumer_command, consumer_args)):
        _require(command['argv'] == [cargo['path'], *arguments] and command['cwd'] == str(root)
                 and _same(q, command['executable_before'], cargo), 'wrong Cargo command, cwd or tool identity')
        env = command['environment']
        _require(env.get('CARGO_INCREMENTAL') == '0', 'incremental compilation was not disabled')
        directory = _absolute(env.get('CARGO_TARGET_DIR'))
        _require(directory.resolve(strict=True) == directory and not directory.is_relative_to(root),
                 'Cargo target must be a retained directory outside the frozen root')
        if target is not None:
            _require(directory == target and _same(q, env, test_command['environment']),
                     'Cargo commands used different targets or environments')
        target = directory
    artifact_tree = proof.manifest(receipt.get('compile_artifacts'))
    if 'cargo_target' in receipt:
        _require(receipt['cargo_target'] == str(target), 'claimed Cargo target differs from command environment')
    proof.tree(target, artifact_tree)
    _require(_same(q, q.tree_identity(target), artifact_tree), 'compile artifact tree membership differs')
    seen = {}
    for command, names, is_test, package_root in (
            (test_command, tuple(q.RUST_TESTS), True, root),
            (consumer_command, PUBLIC_BINS, False, root / 'validation/corpus')):
        manifest_path = package_root / 'Cargo.toml'
        package_relative = str(manifest_path.relative_to(root))
        _require(package_relative in sources and sources[package_relative]['type'] == 'regular',
                 'package manifest absent from source seal')
        package = tomllib.loads(manifest_path.read_text(encoding='utf-8'))['package']
        package_id = 'path+' + package_root.as_uri() + '#' + package['name'] + '@' + package['version']
        label = 'tests' if is_test else 'consumers'
        metadata_name = 'cargo-metadata-' + label
        if metadata_name in commands:
            metadata_command = commands[metadata_name]
            _require(metadata_command['argv'] == [cargo['path'], 'metadata', '--locked', '--offline',
                     '--format-version=1', '--manifest-path', package_relative]
                     and metadata_command['cwd'] == str(root)
                     and _same(q, metadata_command['environment'], test_command['environment'])
                     and _same(q, metadata_command['executable_before'], cargo),
                     'wrong Cargo metadata command/cwd/environment/tool')
            metadata = q.strict_json((proof.output / metadata_command['stdout']['path']).read_bytes())
            _require(isinstance(metadata, dict) and isinstance(metadata.get('packages'), list),
                     'missing Cargo package metadata')
            matches = [row for row in metadata['packages'] if row.get('manifest_path') == str(manifest_path)]
            _require(len(matches) == 1 and matches[0].get('id') == package_id
                     and matches[0].get('name') == package['name']
                     and matches[0].get('version') == package['version'],
                     'Cargo metadata package differs from sealed manifest')
            _require(receipt.get('cargo_packages', {}).get(label) == package_id,
                     'claimed Cargo package differs from metadata')
            for dependency in metadata['packages']:
                dependency_root = _absolute(dependency['manifest_path']).parent
                if not dependency_root.is_relative_to(root):
                    watch = inputs.get('cargo-dependency:' + dependency['id'], {}).get('before', {})
                    _require(watch.get('path') == str(dependency_root) and 'tree' in watch,
                             'Cargo dependency is missing its retained tree watch')
        raw = (proof.output / command['stdout']['path']).read_bytes()
        _require(raw.endswith(b'\n'), 'truncated Cargo JSON stream')
        records = [q.strict_json(line) for line in raw.splitlines() if line]
        _require(all(isinstance(row, dict) for row in records), 'Cargo stream contains a non-object record')
        completed = [row for row in records if row.get('reason') == 'build-finished']
        _require(len(completed) == 1 and completed[0].get('success') is True
                 and records[-1] == completed[0], 'Cargo stream lacks final successful build-finished record')
        for row in records:
            if row.get('reason') != 'compiler-artifact' or row.get('executable') is None:
                continue
            name = row.get('target', {}).get('name')
            # Cargo can also build root package binary targets for integration tests.
            # They confer no authority for the six selected test executables.
            if name not in required:
                continue
            _require(name in names and name not in seen, 'duplicate or misplaced required Cargo artifact: ' + name)
            src = root / 'tests' / (name + '.rs') if is_test else package_root / 'src/bin' / (name + '.rs')
            _require(str(src.relative_to(root)) in sources, 'artifact source absent from source seal: ' + name)
            target_row = row.get('target', {})
            _require(row.get('package_id') == package_id and row.get('manifest_path') == str(manifest_path)
                     and target_row.get('name') == name and target_row.get('src_path') == str(src)
                     and target_row.get('kind') == (['test'] if is_test else ['bin'])
                     and target_row.get('crate_types') == ['bin']
                     and target_row.get('edition') == package['edition']
                     and row.get('profile', {}).get('test') is is_test,
                     'wrong Cargo package/source/target/profile: ' + name)
            _require(_same(q, retained[name], row), 'retained Cargo artifact differs from raw record: ' + name)
            binary = binaries[name]
            path = proof.executable(binary, name)
            _require(row['executable'] == str(path) == binary['resolved']
                     and path.is_relative_to(target) and path != target
                     and isinstance(row.get('filenames'), list) and row['executable'] in row['filenames'],
                     'Cargo artifact executable differs from admitted binary: ' + name)
            relative = str(path.relative_to(target))
            _require(relative in artifact_tree and _same(q, artifact_tree[relative], binary['file']),
                     'executable absent from compile artifact manifest: ' + name)
            label = 'compiled:' + name
            _require(label in inputs and _same(q, inputs[label]['before'], binary),
                     'compiled executable watch differs: ' + name)
            seen[name] = row
    _require(set(seen) == required, 'Cargo logs omit required compiler-artifact records')
    return binaries


def validate_compile(q, attempt, receipt_path, receipt):
    """Return the exact admitted binary rows or raise ValueError, without execution.

    Called by q.load_compile after the caller's Attempt.seal(). All proof paths
    and admitted binaries are watched through the consuming attempt's finish().
    HEAD itself is deliberately irrelevant: the source pin and nonreceipt tree
    must match. A receipt-only followup therefore keeps the same expected pin.
    """
    try:
        return _validate_compile(q, attempt, receipt_path, receipt)
    except (OSError, KeyError, TypeError, UnicodeError, AttributeError) as error:
        raise ValueError('compile proof: missing or malformed retained evidence: ' + str(error)) from error


def _validate_compile(q, attempt, receipt_path, receipt):
    proof = _Proof(q, attempt)
    path = _absolute(str(receipt_path))
    _require(path.name == 'receipt.json' and path.is_relative_to(proof.root / q.RECEIPTS)
             and not path.is_relative_to(proof.root / q.RECEIPTS / 'manifests'),
             'compile receipt must be retained under the frozen receipt root')
    path = _relative(proof.root, str(path.relative_to(proof.root)))
    proof.output = output = path.parent
    _require(_same(q, proof.json_file(path), receipt), 'supplied receipt differs from retained receipt.json')
    _require(isinstance(receipt, dict) and receipt.get('schema') == SCHEMA
             and receipt.get('scope') == 'rust-compile' and receipt.get('status') == 'passed'
             and receipt.get('source_unchanged') is True and receipt.get('runtime_executed') is False
             and not any(k in receipt for k in ('error', 'source_error', 'log_collection_error')),
             'receipt does not describe a successful compile-only attempt')
    initial = proof.json_file(_relative(output, 'attempt.json'))
    _require(isinstance(initial, dict) and initial.get('schema') == SCHEMA
             and initial.get('scope') == 'rust-compile' and initial.get('status') == 'running'
             and initial.get('commands') == [], 'invalid original attempt.json')
    for key in ('schema', 'scope', 'started_ns', 'source_root', 'workspace', 'requested_commit'):
        _require(key in initial and _same(q, initial[key], receipt.get(key)), 'attempt identity differs: ' + key)
    _require(receipt['source_root'] == str(proof.root), 'compile source root differs')
    workspace = _absolute(receipt['workspace'])
    _require(not workspace.is_relative_to(proof.root), 'compile workspace is inside source root')
    _require(type(receipt.get('started_ns')) is int and type(receipt.get('finished_ns')) is int
             and 0 < receipt['started_ns'] <= receipt['finished_ns'], 'invalid attempt timestamps')
    pin = receipt.get('expected_commit')
    _require(isinstance(pin, str) and re.fullmatch('[0-9a-f]{40}', pin)
             and pin == attempt.report.get('expected_commit'), 'compile source pin differs')
    proof.references(receipt)
    proof.references(initial)
    sources = proof.manifest(receipt.get('committed_source'))
    _require(isinstance(attempt.initial, dict) and _same(q, sources, attempt.initial),
             'compile source tree differs from current seal')
    for document in (receipt, attempt.report):
        for key in ('committed_source', 'source_before'):
            _require(_same(q, proof.manifest(document.get(key)), sources), 'source manifest differs: ' + key)
        for key in ('head_before_source', 'head_latest_source', 'head_after_source'):
            if key in document:
                _require(_same(q, proof.manifest(document[key]), sources), 'HEAD source manifest differs: ' + key)
    _require(_same(q, proof.manifest(receipt.get('source_after')), sources), 'final compile source manifest differs')
    proof.tree(proof.root, sources, sources=True)
    source_files = {'Cargo.toml', 'Cargo.lock', 'build.rs', 'src/observation.rs', 'src/monitor.rs',
                    'validation/corpus/Cargo.toml', 'validation/corpus/Cargo.lock'}
    source_files.update('tests/' + name + '.rs' for name in q.RUST_TESTS)
    source_files.update('validation/corpus/src/bin/' + name + '.rs' for name in PUBLIC_BINS)
    _require(source_files <= sources.keys() and all(sources[name]['type'] == 'regular' for name in source_files),
             'required Rust source/package/lock file missing from seal')
    fixture_path = _relative(proof.root, str(q.FIXTURE))
    fixture = dict(path=str(q.FIXTURE), **q.identity(fixture_path))
    _require(fixture['type'] == 'regular' and str(q.FIXTURE) in sources
             and _same(q, fixture, receipt.get('fixture'))
             and _same(q, fixture, attempt.report.get('fixture')), 'compile fixture identity differs')
    commands = receipt.get('commands')
    _require(isinstance(commands, list) and commands, 'missing compile commands')
    by_name, logs, previous_ns = {}, {}, receipt['started_ns']
    directories = set()
    for index, command in enumerate(commands, 1):
        logs.update(_command(proof, output, index, command, sources, previous_ns, receipt['finished_ns']))
        name = command['name']
        _require(name not in by_name, 'duplicate compile command: ' + name)
        by_name[name] = command
        directories.add(f'{index:04d}-{name}')
        previous_ns = command['finished_ns']
    actual_directories = {p.name for p in (output / 'commands').iterdir()}
    _require(actual_directories == directories, 'unreported or missing compile command journal')
    required = {'cargo-version', 'rustc-version', 'rust-tests-compile', 'rust-public-consumer-compile'}
    metadata_commands = {'cargo-metadata-tests', 'cargo-metadata-consumers'}
    allowed = required | {'resolve-cargo', 'resolve-rustc', 'resolve-rustdoc'} | metadata_commands
    _require(required <= by_name.keys() <= allowed, 'missing or unexpected compile commands')
    metadata_present = metadata_commands & by_name.keys()
    _require(not metadata_present or metadata_present == metadata_commands,
             'incomplete Cargo metadata command pair')
    if 'cargo_packages' in receipt:
        _require(metadata_present == metadata_commands and isinstance(receipt['cargo_packages'], dict)
                 and set(receipt['cargo_packages']) == {'tests', 'consumers'},
                 'claimed Cargo packages lack metadata command proof')
    names = list(by_name)
    _require(names[-2:] == ['rust-tests-compile', 'rust-public-consumer-compile'],
             'compiler identities must precede Cargo compilation')
    for name in names[:-2]:
        _require(by_name[name]['cwd'] == str(proof.root), 'compiler identity command used wrong cwd')
    for command in commands:
        _require(_same(q, command['environment'], by_name['rust-tests-compile']['environment']),
                 'compiler identity and build environments differ')
    _require(_same(q, proof.manifest(receipt.get('raw_logs')), logs), 'raw log manifest differs from commands')
    actual_logs = {str(p.relative_to(output)) for p in output.rglob('*.bin')
                   if p.name in ('stdout.bin', 'stderr.bin')}
    _require(actual_logs == set(logs), 'unreported or missing raw compile logs')
    inputs = proof.manifest(receipt.get('input_identities'))
    _toolchain(proof, by_name, inputs)
    binaries = _artifacts(proof, receipt, by_name, inputs, sources)
    # Register only after admission has succeeded, so malformed receipts cannot
    # make the caller watch arbitrary paths. No executable is invoked here.
    for retained in sorted(proof.paths):
        attempt.watch('compile-proof:' + str(retained.relative_to(proof.root)), retained)
    for name, row in binaries.items():
        attempt.watch('rust:' + name, Path(row['path']))
    return binaries

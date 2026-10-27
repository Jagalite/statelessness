"""Input and executable evidence for the installed consumers (Kyoto only).

The TypeScript manifest is prepare.py's typescript-compiler-before.json; its
SHA-256 must be pinned in the queued consumer command, not computed at launch.
Rust
receipts are emitted by qualify-native-integration.py --scope command around:
  cargo build --manifest-path validation/corpus/Cargo.toml --locked --offline
    --bin native-research --bin stateless-corpus --message-format=json
    --target-dir <ROOT>/target/<fresh-name> -j 2
This module never builds, launches a consumer, or treats a filename as proof.
"""
from __future__ import annotations

import copy
import hashlib
from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat


def input_tree_snapshot(root, excluded=()):
    """Inventory one bounded input closure, retaining unreadable/missing entries.

    Exclusions are exact relative output subtrees, never global basename filters.
    Directory sizes/timestamps are intentionally absent: excluded cache/output
    creation changes them without changing an input. Directory modes are bound.
    File links must resolve inside the root; directory links fail closed rather
    than silently omitting an imported subtree. No special files are opened.
    """
    root = Path(root).absolute()
    result = {'root': str(root), 'entries': {}, 'errors': {}}
    rows, errors = result['entries'], result['errors']
    try:
        result['resolved_root'] = str(root.resolve(strict=True))
    except (OSError, RuntimeError) as error:
        errors['.'] = repr(error)

    def visit(path, name):
        if name in excluded:
            return
        try:
            info = path.lstat()
            row = {'mode': stat.S_IMODE(info.st_mode)}
            rows[name] = row
            if stat.S_ISDIR(info.st_mode):
                row['type'] = 'directory'
                children = sorted(path.iterdir())
                for child in children:
                    visit(child, str(child.relative_to(root)))
                return
            row['size_bytes'] = info.st_size
            if stat.S_ISLNK(info.st_mode):
                row.update(type='symlink', target=os.readlink(path))
                resolved = path.resolve(strict=True)
                row['resolved'] = str(resolved)
                target = resolved.stat()
                row.update(resolved_mode=stat.S_IMODE(target.st_mode), resolved_size_bytes=target.st_size)
                boundary = root if root.is_dir() else root.parent
                if not stat.S_ISREG(target.st_mode) or not resolved.is_relative_to(boundary):
                    raise RuntimeError('input link must resolve to a regular file inside its input root')
            elif stat.S_ISREG(info.st_mode):
                row['type'] = 'regular'
            else:
                row['type'] = 'other'
                raise RuntimeError('unsupported input file type')
            with path.open('rb') as stream:
                row['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
        except (OSError, RuntimeError) as error:
            rows.setdefault(name, {'type': 'unavailable'})['identity_error'] = repr(error)
            errors[name] = repr(error)

    visit(root, '.')
    return result


def freeze_input_tree(report, name, root, *, excluded=()):
    """Register once, after intentional writes and before the first input use."""
    trees = report.setdefault('input_trees', {})
    if name in trees:
        raise RuntimeError(f'input baseline must not be replaced: {name}')
    for entry in excluded:
        if Path(entry).is_absolute() or '..' in Path(entry).parts or entry in ('', '.'):
            raise ValueError(f'input exclusion must be a relative output subtree: {entry}')
    evidence = trees[name] = {'root': str(Path(root).absolute()), 'excluded': list(excluded), 'stable': False}
    evidence['before'] = input_tree_snapshot(root, excluded)
    if evidence['before']['errors']:
        raise RuntimeError(f'input tree unavailable before use: {name}')


def check_input_trees(report):
    """Collect every root even on failure; never erase an earlier failed check."""
    failed = []
    for name, evidence in report.get('input_trees', {}).items():
        try:
            current = input_tree_snapshot(evidence['root'], evidence['excluded'])
        except Exception as error:
            current = {'root': evidence['root'], 'entries': {}, 'errors': {'.': repr(error)}}
        before = evidence['before']
        old, new = before['entries'], current['entries']
        differences = {
            'added': {key: new[key] for key in sorted(new.keys() - old.keys())},
            'removed': {key: old[key] for key in sorted(old.keys() - new.keys())},
            'changed': {key: {'before': old[key], 'after': new[key]}
                        for key in sorted(old.keys() & new.keys()) if old[key] != new[key]}}
        if before.get('resolved_root') != current.get('resolved_root'):
            differences['resolved_root'] = {'before': before.get('resolved_root'), 'after': current.get('resolved_root')}
        evidence.update(after=current, differences=differences)
        if before['errors'] or current['errors'] or before != current:
            evidence.setdefault('failures', []).append({'after': current, 'differences': differences})
        evidence['stable'] = not evidence.get('failures')
        if not evidence['stable']:
            failed.append(name)
    report['input_trees_stable'] = not failed
    if failed:
        raise RuntimeError('installed/copied input trees changed or unavailable: ' + ', '.join(failed))


def freeze_installed_python(report, venv):
    """Bind runtime imports/metadata/startup files, not the bootstrap pip tree.

    Statelessness declares no runtime dependencies. Keep the containing import
    namespace so added modules/.pth files are detected too; interpreter/stdlib
    identity remains the existing tool guard's scope, not an environment copy.
    """
    sites = sorted(venv.glob('Lib/site-packages') if os.name == 'nt'
                   else venv.glob('lib/python*/site-packages'))
    if len(sites) != 1 or not (sites[0] / 'statelessness').is_dir():
        raise RuntimeError('installed Python package root missing or ambiguous')
    bootstrap = [path.name for path in sites[0].iterdir()
                 if path.name == 'pip' or path.name.startswith('pip-') and path.name.endswith('.dist-info')]
    # Existing bytecode is an import input even under -B. Retain it; consumers
    # use explicit -B (also under -I) to suppress new cache writes.
    freeze_input_tree(report, 'installed-python', sites[0], excluded=sorted(bootstrap))
    freeze_input_tree(report, 'python-venv-config', venv / 'pyvenv.cfg')


@lru_cache(maxsize=1)
def integration_api():
    spec = importlib.util.spec_from_file_location(
        'consumer_integration_identity', Path(__file__).with_name('qualify-native-integration.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def file_identity(path):
    row = integration_api().identity(Path(path))
    if row.get('type') not in ('regular', 'symlink') or 'sha256' not in row:
        raise RuntimeError(f'file identity unavailable: {path}: {row}')
    return row


def bound_file(path):
    path = Path(path).absolute()
    return {'path': str(path), 'resolved': str(path.resolve(strict=True)),
            **file_identity(path)}


def compiler_tree(path):
    """Use the preparation manifest's regular-file schema; reject link escapes."""
    if not path.is_dir():
        raise RuntimeError(f'prepared TypeScript compiler directory missing: {path}')
    rows = {}
    for directory, dirs, files in os.walk(path, followlinks=False):
        for name in sorted(dirs + files):
            entry = Path(directory) / name
            if entry.is_symlink():
                raise RuntimeError(f'compiler tree symlink is not a pinned regular input: {entry}')
            if not entry.is_dir():
                row = file_identity(entry)
                row['bytes'] = row.pop('size_bytes')
                rows[str(entry.relative_to(path))] = row
    for name in ('package.json', 'bin/tsc', 'lib/tsc.js', 'lib/_tsc.js'):
        if rows.get(name, {}).get('type') != 'regular':
            raise RuntimeError(f'prepared TypeScript compiler input missing: {name}')
    return rows


def typescript_snapshot(root, directory, manifest_path, node):
    package = directory.parent.parent
    return {'compiler_files': compiler_tree(directory),
            'manifest': bound_file(manifest_path), 'node': bound_file(node),
            'package_inputs': {str(path): bound_file(path) for path in (
                root / 'typescript/package.json', root / 'typescript/package-lock.json',
                package / 'package.json', package / 'package-lock.json')}}


def select_typescript(root, directory, manifest_path, expected_manifest_sha256, evidence):
    directory, manifest_path = directory.resolve(), manifest_path.resolve()
    evidence.update(directory=str(directory), manifest_path=str(manifest_path),
                    expected_manifest_sha256=expected_manifest_sha256, stable=False, validated=False)
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != expected_manifest_sha256:
        raise RuntimeError('prepared TypeScript manifest differs from the pre-submit SHA-256 pin')
    node = shutil.which('node')
    if not node:
        raise RuntimeError('node is required for the explicitly selected TypeScript compiler')
    if directory.name != 'typescript' or directory.parent.name != 'node_modules':
        raise RuntimeError('--typescript-compiler must name the prepared node_modules/typescript directory')
    before = typescript_snapshot(root, directory, manifest_path, Path(node))
    evidence['before'] = before
    if json.loads(manifest_path.read_bytes()) != before['compiler_files']:
        raise RuntimeError('prepared TypeScript compiler differs from its complete manifest')
    source_package = root / 'typescript'
    package = directory.parent.parent
    for name in ('package.json', 'package-lock.json'):
        if file_identity(source_package / name) != file_identity(package / name):
            raise RuntimeError(f'prepared TypeScript package input differs from candidate: {name}')
    locked = json.loads((source_package / 'package-lock.json').read_bytes())['packages']['node_modules/typescript']
    version = json.loads((source_package / 'package.json').read_bytes())['devDependencies']['typescript']
    metadata = json.loads((directory / 'package.json').read_bytes())
    if (not re.fullmatch(r'\d+\.\d+\.\d+', version) or locked.get('version') != version
            or not locked.get('integrity') or metadata.get('name') != 'typescript'
            or metadata.get('version') != version):
        raise RuntimeError('prepared TypeScript compiler version does not match the pinned package and lockfile')
    evidence.update(version=version, lock_entry=locked,
                    command=[str(Path(node).absolute()), str(directory / 'bin/tsc')])
    check_typescript(root, evidence)
    evidence['validated'] = True
    return evidence['command']


def check_typescript(root, evidence):
    evidence['stable'] = False
    try:
        current = typescript_snapshot(root, Path(evidence['directory']),
                                      Path(evidence['manifest_path']), Path(evidence['before']['node']['path']))
        evidence['after'] = current
    except BaseException as error:
        evidence['snapshot_error'] = repr(error)
        evidence.setdefault('failures', []).append({'snapshot_error': repr(error)})
        raise
    if current != evidence['before']:
        evidence.setdefault('failures', []).append({'after': copy.deepcopy(current)})
    evidence['stable'] = not evidence.get('failures')
    if not evidence['stable']:
        raise RuntimeError('prepared TypeScript compiler, manifest, Node, or package inputs changed')


def require_typescript_version(output, evidence):
    if output.strip() != 'Version ' + evidence['version']:
        raise RuntimeError('selected TypeScript compiler reports an incompatible version')


def load_manifest(root, descriptor, evidence):
    path = root / descriptor['path']
    manifest_root = root / 'validation/native-parity/derive-model-v2/integration-oct22/manifests'
    if path.parent != manifest_root or path.name != descriptor.get('sha256', '') + '.json':
        raise RuntimeError('integration manifest is outside the content-addressed manifest store')
    raw = path.read_bytes()
    if (hashlib.sha256(raw).hexdigest() != descriptor.get('sha256')
            or len(raw) != descriptor.get('size_bytes')):
        raise RuntimeError(f'integration manifest hash/size mismatch: {path}')
    value = json.loads(raw)
    canonical = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    if raw != canonical or len(value) != descriptor.get('entries'):
        raise RuntimeError(f'integration manifest encoding/entry mismatch: {path}')
    evidence.setdefault('inputs', {})[str(path)] = bound_file(path)
    evidence.setdefault('manifests', {})[descriptor['sha256']] = value
    return value


def validate_research_build(root, receipt_path, executables, evidence):
    """Validate a real, successful, fresh direct Cargo build before any launch.

Receipts are local provenance, not cryptographic attestations from a third party.
All content-addressed manifests and the actual Cargo JSON log are verified.
"""
    api = integration_api()
    receipt_path = receipt_path.resolve()
    evidence.update(receipt=bound_file(receipt_path), inputs={}, executables={}, copies={}, validated=False)
    evidence['inputs'][str(receipt_path)] = evidence['receipt']
    report = json.loads(receipt_path.read_bytes())
    evidence['build_receipt'] = report
    if (report.get('status') != 'passed' or report.get('scope') != 'command'
            or report.get('cwd') != str(root) or report.get('source_equal') is not True
            or report.get('tools_equal') is not True or report.get('typescript_dependencies_equal') is not True
            or report.get('collection_errors') or report.get('error')):
        raise RuntimeError('research binaries require a successful source/tool-stable command-scope build receipt')
    source = load_manifest(root, report['source_before'], evidence)
    if source != load_manifest(root, report['source_after'], evidence) or source != api.source_snapshot():
        raise RuntimeError('research build source manifest differs from the full current candidate')
    api.require_identities(source)
    tools = load_manifest(root, report['tools_before'], evidence)
    if tools != load_manifest(root, report['tools_after'], evidence):
        raise RuntimeError('research build tool manifests differ')
    dependencies = load_manifest(root, report['typescript_dependencies_before'], evidence)
    if dependencies != load_manifest(root, report['typescript_dependencies_after'], evidence):
        raise RuntimeError('research build dependency manifests differ')
    for name in ('cargo', 'rustc', 'rustdoc'):
        if name not in tools.get('executables', {}):
            raise RuntimeError(f'research build tool identity missing: {name}')
    for row in tools['executables'].values():
        current = bound_file(Path(row['path']))
        if current != row:
            raise RuntimeError(f'research build tool changed: {row["path"]}')
        evidence['inputs'][row['path']] = current
    for path, row in tools['cargo_configuration'].items():
        if file_identity(Path(path)) != row:
            raise RuntimeError(f'research build Cargo configuration changed: {path}')
        evidence['inputs'][path] = bound_file(Path(path))
    entries = [entry for entry in report['commands'] if entry.get('name') == 'command']
    if len(entries) != 1:
        raise RuntimeError('research receipt must contain exactly one direct build command')
    entry = entries[0]
    argv = entry.get('command', [])
    # Deliberately one documented grammar: no shell/wrapper, implicit manifest,
    # caller feature/profile overrides, or already-built/test-only commands.
    if len(argv) != 15 or argv[1:12] != [
            'build', '--manifest-path', 'validation/corpus/Cargo.toml', '--locked',
            '--offline', '--bin', 'native-research', '--bin', 'stateless-corpus',
            '--message-format=json', '--target-dir'] or argv[13:] != ['-j', '2']:
        raise RuntimeError('research receipt command is not the supported direct Cargo research build')
    cargo = shutil.which(argv[0], path=report['environment'].get('PATH'))
    if not cargo or str(Path(cargo).resolve()) != tools['executables']['cargo']['resolved']:
        raise RuntimeError('research build command does not use the recorded Cargo executable')
    target = Path(argv[12])
    if not target.is_absolute() or target.resolve() != target or not target.is_relative_to(root / 'target') or target == root / 'target':
        raise RuntimeError('research build needs an explicit private target below the candidate target directory')
    environment = report['environment']
    if (not {'PATH', 'CARGO_INCREMENTAL', 'RUSTFLAGS', 'CARGO_ENCODED_RUSTFLAGS', 'CARGO_TARGET_DIR',
             'RUSTUP_TOOLCHAIN', 'CARGO_HOME', 'SDKROOT', 'DEVELOPER_DIR'} <= environment.keys()
            or environment.get('CARGO_INCREMENTAL') != '0' or environment.get('RUSTFLAGS') is not None
            or environment.get('CARGO_ENCODED_RUSTFLAGS') is not None
            or environment.get('PYTHONNOUSERSITE') != '1' or environment.get('PYTHONPATH') != ''
            or entry.get('environment_overlay') != {}):
        raise RuntimeError('research build environment differs from the sealed integration protocol')
    if (entry.get('exit_code') != 0 or entry.get('source_equal') is not True or entry.get('cwd') != str(root)
            or entry.get('source_before') != report['source_before'] or entry.get('source_after') != report['source_after']):
        raise RuntimeError('research build command failed or used different source')
    before = load_manifest(root, report['artifacts_before'], evidence)
    after = load_manifest(root, report['artifacts_after'], evidence)
    prefix = str(target.relative_to(root)) + '/'
    if any(name.startswith(prefix) for name in before):
        raise RuntimeError('research build target already contained captured build artifacts')
    log = receipt_path.parent / 'command.log'
    if file_identity(log) != entry.get('log') or report.get('logs', {}).get('command.log') != entry.get('log'):
        raise RuntimeError('research Cargo build log identity mismatch')
    evidence['inputs'][str(log)] = bound_file(log)
    messages = [json.loads(line) for line in log.read_bytes().splitlines() if line.startswith(b'{')]
    finished = [row for row in messages if row.get('reason') == 'build-finished']
    if len(finished) != 1 or finished[0].get('success') is not True:
        raise RuntimeError('research Cargo log lacks successful build-finished evidence')
    for name, path in executables.items():
        expected = target / 'debug' / name
        if path.resolve() != expected:
            raise RuntimeError(f'research executable is not the captured build output: {name}')
        rows = [row for row in messages if row.get('reason') == 'compiler-artifact'
                and row.get('target', {}).get('name') == name and row.get('executable') == str(expected)]
        if len(rows) != 1:
            raise RuntimeError(f'Cargo executable artifact missing or ambiguous: {name}')
        row = rows[0]
        src = root / 'validation/corpus/src' / ('main.rs' if name == 'stateless-corpus' else 'bin/native-research.rs')
        if (row.get('fresh') is not False or row.get('manifest_path') != str(root / 'validation/corpus/Cargo.toml')
                or row.get('package_id') != (root / 'validation/corpus').as_uri().replace('file:', 'path+file:', 1) + '#stateless-corpus@0.1.0'
                or row['target'].get('kind') != ['bin'] or row['target'].get('src_path') != str(src)
                or row.get('profile', {}).get('test') is not False or str(expected) not in row.get('filenames', [])):
            raise RuntimeError(f'Cargo artifact is cached, incompatible, or from another source: {name}')
        identity = file_identity(expected)
        if identity != after.get(str(expected.relative_to(root))) or not identity['mode'] & 0o111:
            raise RuntimeError(f'research executable differs from the build receipt: {name}')
        evidence['executables'][name] = bound_file(expected)
        evidence['inputs'][str(expected)] = bound_file(expected)
    check_bound_inputs(evidence)
    evidence['validated'] = True


def check_bound_inputs(evidence):
    current, errors = {}, {}
    for path, expected in evidence.get('inputs', {}).items():
        try:
            current[path] = bound_file(Path(path))
            if current[path] != expected:
                errors[path] = 'identity changed'
        except BaseException as error:
            errors[path] = repr(error)
    if errors:
        evidence.setdefault('input_failures', []).append(
            {'after': copy.deepcopy(current), 'errors': dict(errors)})
    evidence.update(inputs_after=current, input_errors=errors,
                    inputs_stable=not evidence.get('input_failures'))
    if not evidence['inputs_stable']:
        raise RuntimeError('research build input identity changed or unavailable; see retained input_failures')


def copy_research_executable(evidence, name, destination):
    if evidence.get('validated') is not True:
        raise RuntimeError('research build provenance has not been validated')
    source = evidence['executables'][name]
    check_bound_inputs(evidence)
    try:
        shutil.copy2(source['path'], destination)
        copied = bound_file(destination)
        evidence['copies'][name] = copied
        if any(copied[key] != source[key] for key in ('type', 'mode', 'size_bytes', 'sha256')):
            raise RuntimeError(f'copied research executable differs from build receipt: {name}')
    except BaseException as error:
        evidence.setdefault('input_failures', []).append(
            {'copy': name, 'destination': str(destination), 'copies': copy.deepcopy(evidence['copies']),
             'error': repr(error)})
        evidence['inputs_stable'] = False
        raise
    evidence['inputs'][str(destination)] = copied
    check_bound_inputs(evidence)


def record_runner_files(report, prefix, runners, phase, extra_files=None):
    """Retain all survivors on a missing/changed runner, including Swift .build."""
    if phase not in ('before', 'after'):
        raise ValueError('unknown runner identity phase')
    if phase == 'before' and prefix + '_files_before' in report:
        raise RuntimeError('runner baseline must not be replaced: ' + prefix)
    rows = {}
    for name, argv in runners.items():
        files = []
        try:
            paths = [Path(shutil.which(argv[0]) or argv[0])]
            skip = False
            for arg in argv[1:]:
                if skip:
                    skip = False
                elif arg == '-m':
                    skip = True
                    if not (extra_files or {}).get(name):
                        files.append({'identity_error': f'installed module identity required for runner: {name}'})
                elif not arg.startswith('-'):
                    paths.append(Path(arg))
            paths.extend((extra_files or {}).get(name, []))
            for path in paths:
                try:
                    files.append(bound_file(path))
                except BaseException as error:
                    files.append({'path': str(path), 'identity_error': repr(error)})
        except BaseException as error:
            files.append({'identity_error': repr(error)})
        rows[name] = files
    report[prefix + '_files_' + phase] = rows
    valid = bool(rows) and all(files and all('identity_error' not in row for row in files) for files in rows.values())
    if not valid or phase == 'after' and rows != report.get(prefix + '_files_before'):
        report.setdefault(prefix + '_files_failures', []).append(
            {'phase': phase, 'files': copy.deepcopy(rows)})
    report[prefix + '_files_stable'] = not report.get(prefix + '_files_failures')
    if not report[prefix + '_files_stable']:
        raise RuntimeError(prefix + ' runner identity missing or changed')

#!/usr/bin/env python3
"""Kyoto-only integration runner with shared content-addressed identity manifests.

Every invocation needs a new --output. Run compilation as a direct Kyoto Cargo
job first; --scope rust-runtime executes actual tests, including doctests.
This script never changes queue policy, submits jobs, or deletes prior evidence.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import tempfile
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PROOF = ROOT / 'validation/native-parity/derive-model-v2/integration-oct22'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identity(path):
    try:
        info = path.lstat()
        row = {'mode': stat.S_IMODE(info.st_mode)}
        if stat.S_ISLNK(info.st_mode):
            row.update(type='symlink', target=os.readlink(path))
        elif stat.S_ISREG(info.st_mode):
            row['type'] = 'regular'
        elif stat.S_ISDIR(info.st_mode):
            return dict(row, type='directory')
        else:
            return dict(row, type='other')
        with path.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        return dict(row, size_bytes=path.stat().st_size, sha256=checksum)
    except OSError as error:
        return {'type': 'unavailable', 'error': str(error)}


def source_snapshot():
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others',
                                     '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    # build.rs consumes every descendant, including Git-ignored files.
    names = set(names)
    for directory in (ROOT / 'src', *(ROOT / 'crates').glob('*/src')):
        names.update(str(p.relative_to(ROOT)) for p in directory.rglob('*')
                     if p.is_file() or p.is_symlink())
    # Historical executable fixtures remain source, even inside receipt families.
    # Only receipt data in this pre-existing family and ignored build products
    # are mutable. Newly added executable validation sources are always sealed.
    source_extensions = {'.py', '.go', '.mjs', '.js', '.ts', '.swift', '.rs', '.sh', '.toml'}
    return {name: identity(ROOT / name) for name in sorted(set(names))
            if name and not name.startswith('target/') and (
                not name.startswith('validation/native-parity/derive-model-v2/')
                or Path(name).suffix in source_extensions or os.access(ROOT / name, os.X_OK))}


def tree_snapshot(path):
    if not path.exists():
        return {'root': str(path), 'missing': True}
    return {'root': str(path), 'files': {str(p.relative_to(path)): identity(p)
            for p in sorted(path.rglob('*')) if p.is_file() or p.is_symlink()}}


def atomic_write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.publish-', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def manifest(value):
    raw = (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()
    key = digest(raw)
    dest = PROOF / 'manifests' / (key + '.json')
    if dest.exists():
        if dest.read_bytes() != raw:
            raise RuntimeError('manifest identity collision')
    else:
        atomic_write(dest, raw)
    return {'path': str(dest.relative_to(ROOT)), 'sha256': key, 'size_bytes': len(raw),
            'entries': len(value)}


def require_identities(rows):
    invalid = [name for name, row in rows.items()
               if row.get('type') not in ('regular', 'symlink') or 'sha256' not in row]
    if invalid:
        raise RuntimeError('required input identities unavailable: ' + ', '.join(invalid))


def tool_files(env):
    paths = {name: shutil.which(name, path=env.get('PATH')) for name in
             ('python3', 'go', 'node', 'npm', 'swift', 'swiftc', 'cargo', 'rustc', 'rustdoc')}
    paths['executing-python'] = sys.executable
    if shutil.which('rustup', path=env.get('PATH')):
        for name in ('rustc', 'rustdoc', 'cargo'):
            result = subprocess.run(['rustup', 'which', name], cwd=ROOT, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode == 0:
                paths['rustup-' + name] = result.stdout.decode().strip()
    if sys.platform == 'darwin':
        for name in ('swift', 'swiftc', 'swift-test', 'swift-package', 'clang', 'xctest'):
            result = subprocess.run(['xcrun', '--find', name], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode != 0:
                raise RuntimeError('cannot resolve required xcrun tool: ' + name)
            paths['xcrun-' + name] = result.stdout.decode().strip()
    tool_dir = subprocess.check_output(['go', 'env', 'GOTOOLDIR'], cwd=ROOT, env=env, text=True).strip()
    for path in sorted(Path(tool_dir).iterdir()):
        if path.is_file():
            paths['go-tool-' + path.name] = str(path)
    rows = {name: {'path': path, 'resolved': str(Path(path).resolve()), **identity(Path(path))}
            if path else {'type': 'unavailable'} for name, path in paths.items()}
    require_identities(rows)
    # Cargo reads ancestor and user configuration even for an offline build.
    configs = set()
    for directory in (ROOT, *ROOT.parents):
        for name in ('config', 'config.toml'):
            configs.add(directory / '.cargo' / name)
    for name in ('config', 'config.toml'):
        configs.add(Path(env.get('CARGO_HOME', str(Path.home()/'.cargo'))) / name)
    config_rows = {str(p): identity(p) for p in configs if p.exists()}
    require_identities(config_rows)
    return {'executables': rows, 'cargo_configuration': config_rows}


def test_exec(command, kind):
    """Go -exec or rustdoc --test-runtool entry, inside the existing Kyoto job."""
    out = Path(os.environ['INTEGRATION_' + kind.upper() + '_EXEC_DIR'])
    row = {'command': command, 'started_ns': time.time_ns(), 'before': identity(Path(command[0]))}
    code = 1
    try:
        require_identities({'test executable': row['before']})
        code = subprocess.run(command).returncode
        row['exit_code'] = code
    except BaseException as error:
        row['error'] = repr(error)
    finally:
        row.update(after=identity(Path(command[0])), finished_ns=time.time_ns())
        row['identity_equal'] = row['before'] == row['after'] and 'sha256' in row['after']
        # The enclosing test harness owns expected-failure semantics (e.g. should_panic).
        row['status'] = 'recorded' if 'exit_code' in row and row['identity_equal'] else 'failed'
        atomic_write(out / (str(row['started_ns']) + '-' + str(os.getpid()) + '.json'),
                     (json.dumps(row, indent=2) + '\n').encode())
    return code if code else (0 if row['identity_equal'] else 1)


def artifacts():
    rows = {}
    # Executables and compiler dependency manifests, not a duplicate build tree.
    for directory in (ROOT / 'target', ROOT / '.build'):
        if not directory.exists():
            continue
        for p in sorted(directory.rglob('*')):
            if not p.is_file():
                continue
            if p.suffix == '.d' or (os.access(p, os.X_OK) and p.suffix not in ('.o', '.rlib', '.rmeta')):
                rows[str(p.relative_to(ROOT))] = identity(p)
    require_identities(rows)
    dist = tree_snapshot(ROOT / 'typescript/dist')
    if not dist.get('missing'):
        require_identities(dist['files'])
    rows['typescript/dist'] = dist
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=('native', 'packages', 'rust-runtime', 'command'), default='command')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-source', type=Path, required=True)
    parser.add_argument('--expected-tools', type=Path, help='sealed pre-submit tool/configuration manifest')
    parser.add_argument('--artifacts', type=Path, help='fresh copied package/build directory for packages scope')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to(PROOF):
        parser.error('output must be within the excluded integration receipt tree')
    out.mkdir(parents=True, exist_ok=False)
    report = {'scope': args.scope, 'cwd': str(ROOT), 'started_ns': time.time_ns(),
              'commands': [], 'status': 'running'}
    def save():
        atomic_write(out / 'receipt.json', (json.dumps(report, indent=2) + '\n').encode())
    save()
    # No caller fixture, ABI library or build wrapper can replace the sealed inputs.
    env = {key: value for key, value in os.environ.items() if not key.startswith('STATELESS_')}
    for key in ('RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER', 'RUSTC', 'RUSTDOC',
                'RUSTFLAGS', 'CARGO_ENCODED_RUSTFLAGS', 'RUSTDOCFLAGS', 'CARGO_ENCODED_RUSTDOCFLAGS', 'PYTHONHOME', 'NODE_OPTIONS'):
        env.pop(key, None)
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', PYTHONPATH='',
               CARGO_INCREMENTAL='0', CARGO_TARGET_DIR=str(ROOT / 'target/integration'),
               CGO_ENABLED='0', GOPROXY='off', GOTOOLCHAIN='local', GOWORK='off', GOENV='off', GOFLAGS='',
               INTEGRATION_GO_EXEC_DIR=str(out / 'go-executables'),
               INTEGRATION_RUSTDOC_EXEC_DIR=str(out / 'rustdoc-executables'))
    if args.scope == 'rust-runtime':
        # Stable rustdoc runtool options preserve actual doctest binary identities.
        env['CARGO_ENCODED_RUSTDOCFLAGS'] = '\x1f'.join([
            '--test-runtool', sys.executable,
            '--test-runtool-arg=' + str(Path(__file__).resolve()),
            '--test-runtool-arg=--rustdoc-exec'])
    report['environment'] = {k: env.get(k) for k in ('PATH','PYTHONDONTWRITEBYTECODE',
        'PYTHONNOUSERSITE','PYTHONPATH','CARGO_INCREMENTAL','CARGO_TARGET_DIR','CGO_ENABLED',
        'GOPROXY','GOTOOLCHAIN','GOWORK','GOENV','GOFLAGS','RUSTFLAGS','CARGO_ENCODED_RUSTFLAGS',
        'RUSTUP_TOOLCHAIN','DEVELOPER_DIR','SDKROOT','CARGO_HOME','INTEGRATION_GO_EXEC_DIR',
        'INTEGRATION_RUSTDOC_EXEC_DIR','CARGO_ENCODED_RUSTDOCFLAGS')}
    report['cleared_fixture_overrides'] = sorted(k for k in os.environ if k.startswith('STATELESS_'))
    initial = None
    okay = True
    def run(name, command, cwd=ROOT, changes=None):
        before = source_snapshot()
        if before != initial:
            raise RuntimeError('source changed before ' + name)
        entry = {'name': name, 'command': command, 'cwd': str(cwd), 'started_ns': time.time_ns(),
                 'source_before': manifest(before), 'environment_overlay': changes or {}}
        report['commands'].append(entry); save()
        print('Starting ' + name, flush=True)
        with (out / (name + '.log')).open('wb') as stream:
            result = subprocess.run(command, cwd=cwd, env=dict(env, **(changes or {})),
                                    stdout=stream, stderr=subprocess.STDOUT)
        after = source_snapshot()
        entry.update(exit_code=result.returncode, finished_ns=time.time_ns(),
                     source_after=manifest(after), source_equal=before == after,
                     log=identity(out / (name + '.log')))
        save()
        print(json.dumps({'name':name,'exit_code':result.returncode,'source_equal':before == after}),flush=True)
        if after != initial:
            raise RuntimeError('source drift during ' + name)
        return result.returncode == 0
    try:
        report['source_commit'] = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        initial = source_snapshot()
        report['source_before'] = manifest(initial)
        require_identities(initial)
        report['expected_source'] = str(args.expected_source)
        expected = json.loads(args.expected_source.read_text())
        if initial != expected:
            raise RuntimeError('candidate differs from sealed pre-submit source')
        current_tools = tool_files(env)
        report['tools_before'] = manifest(current_tools)
        if args.expected_tools:
            report['expected_tools'] = str(args.expected_tools)
            if current_tools != json.loads(args.expected_tools.read_text()):
                raise RuntimeError('toolchain/configuration differs from sealed pre-submit identities')
        report['typescript_dependencies_before'] = manifest(tree_snapshot(ROOT / 'typescript/node_modules/typescript'))
        report['artifacts_before'] = manifest(artifacts())
        save()
        for name, command in [('python-version',[sys.executable,'--version']),
                              ('go-version',['go','version']), ('node-version',['node','--version']),
                              ('swift-version',['swift','--version']), ('rustc-version',['rustc','-vV'])]:
            okay = run(name, command) and okay
        if args.scope == 'packages':
            if args.artifacts is None:
                raise RuntimeError('packages scope requires a fresh --artifacts directory')
            okay = run('native-packages', [sys.executable, 'scripts/native-parity/n12-wire/prepare.py',
                       '--artifacts', str(args.artifacts.resolve()), '--output', str(out / 'prepare')]) and okay
            okay = run('n13-driver', [sys.executable, '-m', 'unittest', 'discover', '-s', 'scripts',
                       '-p', 'test_native_model_receipts.py', '-v']) and okay
            okay = run('preparation-identities', [sys.executable, '-m', 'unittest', 'discover', '-s', 'scripts',
                       '-p', 'test_native_preparation_identities.py', '-v']) and okay
            okay = run('consumer-provenance', [sys.executable, '-m', 'unittest', 'discover', '-s', 'scripts',
                       '-p', 'test_native_consumer_provenance.py', '-v']) and okay
            okay = run('dispositions', [sys.executable, 'scripts/check-n13-dispositions.py']) and okay
        elif args.scope == 'native':
            okay = run('python', [sys.executable,'-m','unittest','discover','-s','python/tests','-v'],
                       changes={'PYTHONPATH':str(ROOT / 'python/src')}) and okay
            go_wrapper = shlex.join([sys.executable, str(Path(__file__).resolve()), '--go-exec'])
            okay = run('go', ['go','-C','go','test','-json','-count=1','-exec',go_wrapper,'./...']) and okay
            okay = run('go-vet', ['go','-C','go','vet','./...']) and okay
            deps = run('typescript-dependencies',['npm','ci','--ignore-scripts','--no-audit','--no-fund'],ROOT/'typescript')
            dependencies = tree_snapshot(ROOT / 'typescript/node_modules/typescript')
            if deps:
                require_identities(dependencies.get('files', {}))
                if dependencies.get('missing') or not dependencies.get('files'):
                    raise RuntimeError('installed TypeScript compiler missing')
            report['typescript_dependencies_installed'] = manifest(dependencies)
            okay = deps and okay
            if deps:
                okay = run('typescript',['npm','test'],ROOT/'typescript') and okay
            okay = run('swift',['swift','test','-j','2']) and okay
            okay = run('dispositions',[sys.executable,'scripts/check-n13-dispositions.py']) and okay
        elif args.scope == 'rust-runtime':
            okay = run('rust-workspace-runtime',['cargo','test','--workspace','--locked','--offline','-j','2']) and okay
        else:
            command = args.command[1:] if args.command[:1] == ['--'] else args.command
            if not command:
                raise RuntimeError('command scope needs a command')
            okay = run('command',command) and okay
    except BaseException as error:
        report['error'] = repr(error); okay = False
    finally:
        def collect(key, callback):
            nonlocal okay
            try:
                value = callback()
                report[key] = manifest(value)
                return value
            except BaseException as error:
                report.setdefault('collection_errors', {})[key] = repr(error)
                okay = False
                return None
        final = collect('source_after', source_snapshot)
        report['source_equal'] = initial is not None and final is not None and initial == final
        collect('tools_after', lambda: tool_files(env))
        collect('artifacts_after', artifacts)
        dependencies = collect('typescript_dependencies_after', lambda: tree_snapshot(ROOT / 'typescript/node_modules/typescript'))
        expected_dependencies = report.get('typescript_dependencies_installed', report.get('typescript_dependencies_before'))
        report['typescript_dependencies_equal'] = expected_dependencies is not None and expected_dependencies == report.get('typescript_dependencies_after')
        if dependencies and not dependencies.get('missing'):
            try:
                require_identities(dependencies.get('files', {}))
            except BaseException as error:
                report.setdefault('collection_errors', {})['typescript-identities'] = repr(error)
                okay = False
        report['tools_equal'] = report.get('tools_before') is not None and report.get('tools_before') == report.get('tools_after')
        if not report['source_equal'] or not report['tools_equal'] or not report['typescript_dependencies_equal']:
            okay = False
        try:
            report['logs'] = {str(p.relative_to(out)): identity(p) for p in out.rglob('*.log')}
            require_identities(report['logs'])
        except BaseException as error:
            report.setdefault('collection_errors', {})['logs'] = repr(error)
            okay = False
        if args.scope in ('native', 'packages', 'rust-runtime'):
            kind = 'rustdoc' if args.scope == 'rust-runtime' else 'go'
            def executable_proofs():
                evidence = tree_snapshot(out / (kind + '-executables'))
                if evidence.get('missing') or not evidence.get('files'):
                    raise RuntimeError('missing ' + kind + ' executable identity evidence')
                require_identities(evidence['files'])
                for name in evidence['files']:
                    row = json.loads((out / (kind + '-executables') / name).read_text())
                    require_identities({'before': row['before'], 'after': row['after']})
                    if not row.get('identity_equal') or row.get('status') != 'recorded':
                        raise RuntimeError(kind + ' executable proof failed: ' + name)
                return evidence
            collect(kind + '_executables', executable_proofs)
        report.update(status='passed' if okay else 'failed', finished_ns=time.time_ns())
        save()
        print(json.dumps(report),flush=True)
    return 0 if okay else 1


if __name__ == '__main__':
    if sys.argv[1:2] == ['--go-exec']:
        raise SystemExit(test_exec(sys.argv[2:], 'go'))
    if sys.argv[1:2] == ['--rustdoc-exec']:
        raise SystemExit(test_exec(sys.argv[2:], 'rustdoc'))
    raise SystemExit(main())

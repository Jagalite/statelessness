#!/usr/bin/env python3
"""Build fresh public consumers and run native regressions inside a Kyoto job.

This is an explicit multi-language build/install runner, not a Cargo test-policy
override. Rust is built with cargo build; native suites execute as logged. Every
command and complete stdout/stderr is retained, including failed commands.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import shlex
import signal
import subprocess
import sys
import traceback

spec = importlib.util.spec_from_file_location('n12_compare', Path(__file__).with_name('compare.py'))
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)
ROOT = wire.ROOT


def compiler_snapshot(directory):
    rows = wire.tree_snapshot(directory)
    for name in ('package.json', 'bin/tsc', 'lib/_tsc.js'):
        if rows.get(name, {}).get('type') != 'regular':
            raise RuntimeError(f'prepared TypeScript compiler input missing: {name}')
    return rows


def compiler_command(directory, node):
    # npm's .bin launcher is generated outside the compiler tree we seal.
    # Invoke the sealed compiler entry directly, with the identified Node tool.
    return [str(node), str(directory / 'bin/tsc'), '-p', 'tsconfig.json']


def swift_test_snapshot(directory):
    rows = {}
    for path in sorted(directory.rglob('*')):
        if path.is_file() and os.access(path, os.X_OK) and (
                path.suffix == '.xctest' or
                path.parent.name == 'MacOS' and path.parent.parent.name == 'Contents'
                and path.parent.parent.parent.suffix == '.xctest'):
            rows.update(wire.resolution_snapshot(path))
    if not rows:
        raise RuntimeError('Swift XCTest executable identity missing')
    return rows


def require_snapshot_equal(expected, actual, label):
    if not expected or expected != actual:
        raise RuntimeError(f'{label} identity changed or unavailable')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        return prepare(args)
    except BaseException as error:
        wire.save(args.output / 'preparation-aborted.json', {
            'passed': False, 'exception': {'type': type(error).__name__,
            'message': str(error), 'traceback': traceback.format_exc()}})
        for name, snapshot in (
            ('abort-source-after', wire.source_snapshot),
            ('abort-copy-after', lambda: wire.tree_snapshot(args.artifacts.resolve() / 'source')),
            ('abort-packages-after', lambda: wire.tree_snapshot(args.artifacts.resolve() / 'packages')),
            ('abort-consumers-after', lambda: wire.tree_snapshot(args.artifacts.resolve() / 'consumers')),
        ):
            try:
                wire.save(args.output / (name + '.json'), snapshot())
            except BaseException as failure:
                wire.save(args.output / (name + '-error.json'), {'type': type(failure).__name__, 'message': str(failure)})
        traceback.print_exc()
        return 1


def prepare(args):
    scratch = args.artifacts.resolve()
    scratch.mkdir(parents=True, exist_ok=False)
    before = wire.source_snapshot()
    wire.save(args.output / 'source-before.json', before)
    frozen = scratch / 'source'
    frozen.mkdir()
    for name, identity in before.items():
        if identity['type'] != 'regular':
            raise RuntimeError(f'candidate source must be a regular file: {name}')
        dest = frozen / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dest)
        if wire.file_identity(dest) != identity:
            raise RuntimeError(f'source copy mismatch: {name}')
    packages = scratch / 'packages'
    consumers = scratch / 'consumers'
    builds = scratch / 'builds'
    for folder in (packages, consumers, builds):
        folder.mkdir()
    input_copies = []

    def register_copy(destination, expected, generated=(), mutable=()):
        actual = wire.tree_snapshot(destination)
        record = {'path': str(destination), 'before': actual, 'expected': expected,
                  'expected_after': dict(expected),
                  'generated_prefixes': list(generated), 'mutable_paths': list(mutable),
                  'copy_matched': actual == expected}
        input_copies.append(record)
        wire.save(args.output / 'build-input-copies.json', input_copies)
        if not record['copy_matched']:
            raise RuntimeError(f'copied build input differs from frozen source: {destination}')

    def copy_tree(source, destination, generated=(), mutable=()):
        shutil.copytree(source, destination)
        register_copy(destination, wire.tree_snapshot(source), generated, mutable)

    def rewrite_manifest(path, contents):
        record, = [record for record in input_copies if str(path.parent) == record['path']]
        name = path.name
        if name not in record['mutable_paths'] or wire.file_identity(path) != record['before'][name]:
            raise RuntimeError(f'unexpected manifest before authorized rewrite: {path}')
        data = contents.encode('utf-8')
        expected = {'type': 'regular', 'mode': record['before'][name]['mode'], **wire.digest(data)}
        path.write_bytes(data)
        if wire.file_identity(path) != expected:
            raise RuntimeError(f'manifest rewrite mismatch: {path}')
        record['expected_after'][name] = expected
        wire.save(args.output / 'build-input-copies.json', input_copies)

    copy_tree(frozen / 'conformance', packages / 'conformance')
    # The combined N13 Python/TypeScript suites read the authored draft
    # relative to the copied package, outside typescript/. Preserve that input
    # and guard its membership and bytes alongside the package copies.
    draft = Path('docs/native-parity/derive-model-draft')
    (packages / draft).parent.mkdir(parents=True, exist_ok=True)
    copy_tree(frozen / draft, packages / draft)
    commands, failures, hosts = [], {}, {}
    compiler_before = None
    swift_tests_before = None
    env = os.environ.copy()
    inherited_cargo_home = Path(env.get('CARGO_HOME', str(Path.home() / '.cargo')))
    private_cargo_home = scratch / 'cargo-home'
    private_cargo_home.mkdir()
    if (inherited_cargo_home / 'registry').is_dir():
        (private_cargo_home / 'registry').symlink_to((inherited_cargo_home / 'registry').resolve(), target_is_directory=True)
    env.update({'CARGO_INCREMENTAL': '0', 'PYTHONDONTWRITEBYTECODE': '1', 'GOWORK': 'off',
                'CGO_ENABLED': '0', 'GOPROXY': 'off', 'GOTOOLCHAIN': 'local',
                'GOENV': 'off', 'GOFLAGS': '', 'CARGO_HOME': str(private_cargo_home),
                'GOCACHE': str(scratch / 'go-cache'), 'GOMODCACHE': str(scratch / 'go-mod-cache')})
    removed = sorted({key for key in env if key.startswith('STATELESS_') or
                      key.startswith('CARGO_') and key not in {'CARGO_HOME', 'CARGO_INCREMENTAL'}} |
                     {'PYTHONPATH', 'PYTHONHOME', 'NODE_PATH', 'NODE_OPTIONS',
                      'RUSTC', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER', 'RUSTFLAGS', 'RUSTDOCFLAGS'})
    for key in removed:
        env.pop(key, None)
    corpora = {'PAIRED_CORPUS': 'paired-v1.tsv', 'SEARCH_CORPUS': 'search-v1.tsv',
               'SEARCH_HISTORY': 'search-v1-history.json', 'CAUSAL_CORPUS': 'causal-evidence-v2.json',
               'EVIDENCE_CORPUS': 'causal-evidence-v2.json', 'REPLAY_CORPUS': 'replay-evidence-v1.json',
               'CHECKED_CORPUS': 'checked-results-v1.json', 'BINARY_CORPUS': 'native-binary-v1.json',
               'BFS_CORPUS': 'bfs-controls-v1.json', 'ENCODE_CORPUS': 'encode-buffers-v1.json',
               'VALUE_CORPUS': 'canonical-value-codec-v1.json', 'APPEND_CORPUS': 'append-checks-v1.json'}
    corpus_env = {'STATELESS_' + key: str(frozen / 'conformance' / value) for key, value in corpora.items()}
    env.update(corpus_env)
    env.setdefault('INTEGRATION_GO_EXEC_DIR', str(args.output.resolve() / 'go-executables'))

    def run(label, argv, cwd=scratch, extra_env=None, required=None):
        index = len(commands)
        out = args.output / f'{index:02}-{label}'
        out.mkdir()
        command_env = dict(env)
        command_env.update(extra_env or {})
        receipt = {'label': label, 'argv': list(map(str, argv)), 'cwd': str(cwd),
                   'env_overrides': {key: command_env[key] for key in ('CARGO_HOME', 'CARGO_INCREMENTAL', 'PYTHONDONTWRITEBYTECODE', 'GOWORK', 'GOENV', 'GOFLAGS', 'GOCACHE', 'GOMODCACHE', 'CGO_ENABLED', 'GOPROXY', 'GOTOOLCHAIN')}
                                    | corpus_env | (extra_env or {}),
                   'env_removed': removed, 'passed': False}
        commands.append(receipt)
        try:
            with (out / 'stdout.log').open('wb') as stdout, (out / 'stderr.log').open('wb') as stderr:
                child = subprocess.Popen(receipt['argv'], cwd=cwd, env=command_env, stdout=stdout, stderr=stderr, start_new_session=True)
                try:
                    receipt['exit_code'] = child.wait(timeout=1200)
                except subprocess.TimeoutExpired:
                    receipt['termination_reason'] = 'timeout'
                    os.killpg(child.pid, signal.SIGKILL)
                    receipt['exit_code'] = child.wait()
                    raise RuntimeError(f'{label}: timed out; no automatic retry')
                except BaseException:
                    receipt['termination_reason'] = 'runner interrupted'
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGKILL)
                    receipt['exit_code'] = child.wait()
                    raise
            receipt['passed'] = receipt['exit_code'] == 0
            if required:
                raw = (out / 'stdout.log').read_bytes() + (out / 'stderr.log').read_bytes()
                receipt['required_output'] = required
                receipt['required_output_matched'] = all(value.encode() in raw for value in required)
                receipt['passed'] = receipt['passed'] and receipt['required_output_matched']
            if not receipt['passed']:
                raise RuntimeError(f'{label}: command/assertion failed; inspect {out}')
            return (out / 'stdout.log').read_bytes()
        except BaseException as error:
            receipt['exception'] = {'type': type(error).__name__, 'message': str(error)}
            raise
        finally:
            receipt['logs'] = {name: wire.digest((out / name).read_bytes()) for name in ('stdout.log', 'stderr.log') if (out / name).exists()}
            wire.save(out / 'receipt.json', receipt)
            print(json.dumps({'stage': label, 'passed': receipt['passed']}, sort_keys=True), flush=True)

    def copy_package(name, paths):
        package = packages / name
        package.mkdir()
        for path in paths:
            if (frozen / path).is_dir():
                shutil.copytree(frozen / path, package / path)
            else:
                shutil.copy2(frozen / path, package / path)
        expected = {key: value for key, value in before.items()
                    if any(key == path or key.startswith(path + '/') for path in paths)}
        register_copy(package, expected, generated=('.swiftpm/',))
        return package

    def copy_consumer(language):
        destination = consumers / language
        generated = {'rust': ('Cargo.lock',), 'go': ('go.sum',), 'python': ('venv/',),
                     'typescript': ('node_modules/', 'package-lock.json'), 'swift': ('.swiftpm/',)}
        mutable = {'rust': ('Cargo.toml',), 'go': ('go.mod',), 'typescript': ('package.json',)}
        copy_tree(frozen / 'scripts/native-parity/n12-wire' / language, destination,
                  generated[language], mutable.get(language, ()))
        return destination

    def regression(label, argv, cwd, extra_env=None, required=None):
        # A failed suite is retained while remaining language builds continue.
        try:
            run(label, argv, cwd, extra_env, required)
        except Exception as error:
            failures[label] = str(error)

    report = {'passed': False, 'commands': commands, 'failures': failures,
              'scratch': str(scratch), 'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()}
    try:
        for language in ('rust', 'go', 'python', 'typescript', 'swift'):
            try:
                consumer = copy_consumer(language)
                if language == 'rust':
                    run('rust-version', ['rustc', '--version', '--verbose'])
                    manifest = consumer / 'Cargo.toml'
                    rewrite_manifest(manifest, manifest.read_text().replace('"../../../../crates/statelessness-debug"', json.dumps(str(frozen / 'crates/statelessness-debug')))
                                        .replace('"../../../.."', json.dumps(str(frozen))))
                    target = builds / 'rust'
                    run('rust-consumer-build', ['cargo', 'build', '--offline', '--manifest-path', manifest], consumer,
                        {'CARGO_TARGET_DIR': str(target), 'CARGO_BUILD_JOBS': '2'})
                    binary = target / 'debug/n12-wire-rust'
                    hosts[language] = {'argv': [str(binary)], 'cwd': str(consumer),
                        'artifacts': [str(binary), str(consumer), str(frozen / 'src'), str(frozen / 'crates/statelessness-debug/src')]}
                elif language == 'go':
                    run('go-version', ['go', 'version'])
                    package = packages / 'go'
                    copy_tree(frozen / 'go', package)
                    manifest = consumer / 'go.mod'
                    rewrite_manifest(manifest, manifest.read_text().replace('../../../../go', str(package)))
                    binary = builds / 'n12-wire-go'
                    run('go-consumer-build', ['go', 'build', '-mod=readonly', '-o', binary, '.'], consumer)
                    hosts[language] = {'argv': [str(binary)], 'cwd': str(consumer), 'artifacts': [str(binary), str(consumer), str(package)]}
                    go_exec = shlex.join([sys.executable, str(frozen / 'scripts/qualify-native-integration.py'), '--go-exec'])
                    regression('go-full-tests', ['go', 'test', '-count=1', '-exec', go_exec, './...'], package,
                               {'INTEGRATION_GO_EXEC_DIR': env['INTEGRATION_GO_EXEC_DIR']},
                               required=['github.com/Jagalite/statelessness/go/debug'])
                    regression('go-vet', ['go', 'vet', './...'], package)
                elif language == 'python':
                    run('python-version', [sys.executable, '--version'])
                    package = packages / 'python'
                    copy_tree(frozen / 'python', package, generated=('build/', 'src/statelessness.egg-info/'))
                    builder, installed = scratch / 'python-builder', consumer / 'venv'
                    run('python-builder-venv', [sys.executable, '-m', 'venv', builder])
                    build_python = builder / 'bin/python'
                    run('python-build-dependencies', [build_python, '-m', 'pip', 'install', 'setuptools==80.9.0', 'wheel==0.45.1'])
                    run('python-builder-freeze', [build_python, '-m', 'pip', 'freeze', '--all'])
                    wheels = scratch / 'wheels'
                    wheels.mkdir()
                    run('python-wheel', [build_python, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--wheel-dir', wheels, package])
                    run('python-consumer-venv', [sys.executable, '-m', 'venv', installed])
                    python = installed / 'bin/python'
                    wheel, = wheels.glob('statelessness-*.whl')
                    run('python-consumer-install', [python, '-m', 'pip', 'install', '--no-deps', '--no-index', wheel])
                    raw = run('python-installed-path', [python, '-I', '-B', '-c',
                        'import pathlib,statelessness; print(pathlib.Path(statelessness.__file__).resolve())'])
                    module = Path(raw.decode().strip())
                    if installed not in module.parents:
                        raise RuntimeError('Python resolved outside fresh installation')
                    hosts[language] = {'argv': [str(python), '-I', '-B', str(consumer / 'host.py')], 'cwd': str(consumer),
                        'artifacts': [str(consumer), str(wheel)]}
                    regression('python-full-installed-tests', [python, '-I', '-B', '-m', 'unittest', 'discover', '-s', package / 'tests', '-v'], consumer,
                               required=['test_debugger_wire', '\nOK'])
                elif language == 'typescript':
                    run('node-version', ['node', '--version'])
                    run('npm-version', ['npm', '--version'])
                    package = packages / 'typescript'
                    copy_tree(frozen / 'typescript', package, generated=('node_modules/', 'dist/'))
                    run('typescript-dependencies', ['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], package)
                    compiler_before = compiler_snapshot(package / 'node_modules/typescript')
                    wire.save(args.output / 'typescript-compiler-before.json', compiler_before)
                    run('typescript-build', compiler_command(package / 'node_modules/typescript', shutil.which('node')), package)
                    tarballs = scratch / 'tarballs'
                    tarballs.mkdir()
                    result = run('typescript-pack', ['npm', 'pack', '--json', '--ignore-scripts', '--pack-destination', tarballs], package)
                    tarball = tarballs / json.loads(result)[0]['filename']
                    manifest = consumer / 'package.json'
                    package_metadata = json.loads(manifest.read_text())
                    package_metadata['dependencies'] = {'@jagalite/statelessness': 'file:' + str(tarball)}
                    rewrite_manifest(manifest, json.dumps(package_metadata, indent=2) + '\n')
                    run('typescript-consumer-install', ['npm', 'install', '--ignore-scripts', '--no-audit', '--no-fund'], consumer)
                    run('typescript-installed-path', ['node', '--input-type=module', '-e',
                        "console.log(import.meta.resolve('@jagalite/statelessness/debug/protocol'))"], consumer)
                    hosts[language] = {'argv': [shutil.which('node'), str(consumer / 'host.mjs')], 'cwd': str(consumer),
                        'artifacts': [str(consumer), str(tarball)]}
                    regression('typescript-full-tests', ['node', '--test', '--test-reporter=tap', *sorted((package / 'test').glob('*.test.mjs'))], package,
                               required=['# fail 0'])
                elif language == 'swift':
                    run('swift-version', ['swift', '--version'])
                    # SwiftPM derives local package identity from the directory
                    # basename; it must differ from the consumer's "swift".
                    package = copy_package('statelessness-swift', ['Package.swift', 'swift', 'conformance'])
                    target = builds / 'swift-consumer'
                    swift_env = {'N12_SWIFT_PACKAGE_PATH': str(package)}
                    run('swift-consumer-build', ['swift', 'build', '--package-path', consumer, '--scratch-path', target,
                        '--configuration', 'debug', '--jobs', '2', '--product', 'n12-wire-swift'], consumer, swift_env)
                    binary = target / 'debug/n12-wire-swift'
                    hosts[language] = {'argv': [str(binary.resolve())], 'cwd': str(consumer),
                        'artifacts': [str(binary.resolve()), str(consumer), str(package / 'Package.swift'), str(package / 'swift/Sources')]}
                    run('swift-test-build', ['swift', 'build', '--build-tests', '--package-path', package,
                        '--scratch-path', builds / 'swift-tests', '--configuration', 'debug', '--jobs', '2'], package)
                    swift_tests_before = swift_test_snapshot(builds / 'swift-tests')
                    wire.save(args.output / 'swift-tests-before.json', swift_tests_before)
                    regression('swift-full-tests', ['swift', 'test', '--skip-build', '--package-path', package, '--scratch-path', builds / 'swift-tests',
                        '--configuration', 'debug', '--jobs', '2'], package,
                        required=['Executed ', '0 failures'])
            except Exception as error:
                failures[language] = {'type': type(error).__name__, 'message': str(error), 'traceback': traceback.format_exc()}
            print('KYOTO_PROGRESS ' + json.dumps({'completed': len(hosts), 'total': 5, 'phase': 'installed-hosts'}), flush=True)
        for host in hosts.values():
            host['env'] = {'PATH': os.defpath, 'LANG': 'C', 'LC_ALL': 'C', 'TZ': 'UTC',
                           'PYTHONDONTWRITEBYTECODE': '1'}
        report['launch_specification'] = hosts
        wire.save(args.output / 'hosts.json', hosts)
        wire.save(args.output / 'artifacts-prepared.json', {name: wire.artifact_snapshot(host) for name, host in hosts.items()})
        report['hosts_ready'] = sorted(hosts)
        report['passed'] = not failures and len(hosts) == 5
    finally:
        for label, expected, capture in (
                ('typescript-compiler', compiler_before,
                 lambda: compiler_snapshot(packages / 'typescript/node_modules/typescript')),
                ('swift-tests', swift_tests_before,
                 lambda: swift_test_snapshot(builds / 'swift-tests'))):
            report[label + '-stable'] = False
            try:
                actual = capture()
                wire.save(args.output / (label + '-after.json'), actual)
                require_snapshot_equal(expected, actual, label)
                report[label + '-stable'] = True
            except BaseException as error:
                failures[label + '-identity'] = {'type': type(error).__name__, 'message': str(error)}
        report['build_input_copies_stable'] = True
        for record in input_copies:
            try:
                after_copy = wire.tree_snapshot(Path(record['path']))
                changes = [name for name in sorted(record['before'].keys() | after_copy.keys())
                           if record['before'].get(name) != after_copy.get(name)]
                def permitted_change(name):
                    if name in record['before']:
                        return after_copy.get(name) == record['expected_after'][name]
                    return name not in record['before'] and any(name == prefix or prefix.endswith('/') and name.startswith(prefix) for prefix in record['generated_prefixes'])
                unexpected = [name for name in sorted(record['expected_after'].keys() | after_copy.keys())
                              if record['expected_after'].get(name) != after_copy.get(name) and not permitted_change(name)]
                record.update({'after': after_copy, 'changed_paths': changes, 'unexpected_changes': unexpected,
                               'stable': record['copy_matched'] and not unexpected})
            except BaseException as failure:
                record.update({'stable': False, 'after_error': {'type': type(failure).__name__, 'message': str(failure)}})
            report['build_input_copies_stable'] &= record['stable']
        wire.save(args.output / 'build-input-copies.json', input_copies)
        report['source_stable'] = report['copied_source_stable'] = False
        try:
            after = wire.source_snapshot()
            wire.save(args.output / 'source-after.json', after)
            report['source_stable'] = before == after
            report['changed_source_paths'] = [p for p in sorted(before.keys() | after.keys()) if before.get(p) != after.get(p)]
        except Exception as error:
            failures['source-after'] = {'type': type(error).__name__, 'message': str(error), 'traceback': traceback.format_exc()}
        try:
            copied_after = wire.tree_snapshot(frozen)
            wire.save(args.output / 'copied-source-after.json', copied_after)
            report['copied_source_stable'] = before == copied_after
            report['changed_copied_source_paths'] = [p for p in sorted(before.keys() | copied_after.keys()) if before.get(p) != copied_after.get(p)]
        except Exception as error:
            failures['copied-source-after'] = {'type': type(error).__name__, 'message': str(error), 'traceback': traceback.format_exc()}
        report['passed'] = report['passed'] and not failures and report['source_stable'] and report['copied_source_stable'] and report['build_input_copies_stable']
        wire.save(args.output / 'preparation.json', report)
    print(json.dumps({'passed': report['passed'], 'hosts_ready': report.get('hosts_ready', []), 'failures': failures}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    def interrupted(signum, _frame):
        raise KeyboardInterrupt(f'preparation interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(main())

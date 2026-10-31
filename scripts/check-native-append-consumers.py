#!/usr/bin/env python3
"""Qualify native append-check applications from staged/installed packages.

Submit this driver through Kyoto. It runs no cargo commands. Every producer and
consumer invocation is a new process outside the checkout. Candidate evidence
only: this does not promote append-checks-v1 or claim performance/complete parity.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--without-swift', action='store_true')
    parser.add_argument('--rust', type=Path, required=True, help='Already Kyoto-built native-append Rust reference executable')
    parser.add_argument('--rust-portable', type=Path, help='Optional already-built Rust four-profile corpus runner')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        raise RuntimeError('output must be new or empty; preserve previous receipts')
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='stateless-native-append-')).resolve()
    logs = out / 'logs'; logs.mkdir()
    packages = out / 'packages'; packages.mkdir()
    env = dict(os.environ, PYTHONPATH='', PYTHONNOUSERSITE='1', GOPROXY='off', GOTOOLCHAIN='local', GOWORK='off', CGO_ENABLED='0')
    commands = []
    report = {'status': 'running', 'profile': 'append-checks-v1 candidate', 'workspace': str(work),
              'platform': platform.platform(), 'commands': commands, 'pairs': [], 'producers': {},
              'artifacts': {},
              'scope': 'three native languages plus Rust' if args.without_swift else 'four native languages plus Rust',
              'python_version': sys.version}

    def save():
        (out / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(name, argv, cwd=work):
        command = list(map(str, argv))
        print(f'[{name}] ' + ' '.join(command), flush=True)
        completed = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
        output = completed.stdout.decode('utf-8', 'replace')
        (logs / f'{name}.log').write_text(output)
        commands.append({'name': name, 'argv': command, 'cwd': str(cwd), 'exit_code': completed.returncode,
                         'log_sha256': fingerprint(logs / f'{name}.log')})
        save()
        if completed.returncode:
            print(output, flush=True)
            raise RuntimeError(f'{name} failed ({completed.returncode})')
        return output.strip()

    def stage(source, dest):
        shutil.copytree(source, dest, ignore=shutil.ignore_patterns('.build', '.swiftpm', 'node_modules', 'dist', '__pycache__', '*.egg-info', 'build', '*.tgz'))

    def tool(name):
        result = shutil.which(name)
        if not result: raise RuntimeError(f'{name} is required')
        return result

    try:
        tracked = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
        sources = {p: fingerprint(ROOT / p) for p in tracked if p and (ROOT / p).is_file()
                   and (not p.startswith(('validation/', 'target/')) or p.startswith('validation/corpus/'))}
        report['source_files'] = sources
        report['source_manifest_sha256'] = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
        report['source_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        py_source = work / 'python-package'; stage(ROOT / 'python', py_source)
        run('python-builder-venv', [sys.executable, '-m', 'venv', work / 'builder-venv'])
        builder = work / 'builder-venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        # Build tooling is deliberately isolated from both host and consumer.
        # Match python/pyproject.toml's declared minimum; runtime deps stay zero.
        run('python-builder-tools', [builder, '-m', 'pip', 'install', 'setuptools>=77', 'wheel'])
        report['python_builder_tools'] = run('python-builder-freeze', [builder, '-m', 'pip', 'freeze', '--all'])
        run('python-wheel', [builder, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation', '--wheel-dir', packages, py_source])
        run('python-venv', [sys.executable, '-m', 'venv', work / 'venv'])
        py = work / 'venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        wheel, = packages.glob('*.whl')
        run('python-install', [py, '-m', 'pip', 'install', '--no-index', '--no-deps', wheel])
        shutil.copy2(ROOT / 'examples/native-append/python/consumer.py', work / 'python-consumer.py')
        runners = {'python': [str(py), '-I', str(work / 'python-consumer.py')]}

        go = tool('go'); go_source = work / 'go-package'; stage(ROOT / 'go', go_source)
        report['go_version'] = run('go-version', [go, 'version'])
        go_consumer = work / 'go-consumer'; stage(ROOT / 'examples/native-append/go', go_consumer)
        (go_consumer / 'go.mod').write_text('module append-consumer\n\ngo 1.23.0\n\nrequire github.com/Jagalite/statelessness/go v0.0.0\nreplace github.com/Jagalite/statelessness/go => ../go-package\n')
        go_bin = work / ('go-consumer.exe' if os.name == 'nt' else 'go-consumer-bin')
        run('go-build', [go, 'build', '-trimpath', '-o', go_bin, '.'], go_consumer)
        runners['go'] = [str(go_bin)]

        node, npm = tool('node'), tool('npm')
        report['node_version'] = run('node-version', [node, '--version'])
        report['npm_version'] = run('npm-version', [npm, '--version'])
        ts_source = work / 'typescript-package'; stage(ROOT / 'typescript', ts_source)
        local_tsc = ROOT / 'typescript/node_modules/typescript/bin/tsc'
        compiler = [node, str(local_tsc)] if local_tsc.is_file() else [tool('tsc')]
        report['typescript_version'] = run('typescript-version', compiler + ['--version'])
        run('typescript-package-build', compiler + ['-p', 'tsconfig.json'], ts_source)
        run('typescript-pack', [npm, 'pack', '--ignore-scripts', '--pack-destination', packages], ts_source)
        tarball, = packages.glob('*.tgz')
        ts_consumer = work / 'typescript-consumer'; stage(ROOT / 'examples/native-append/typescript', ts_consumer)
        (ts_consumer / 'package.json').write_text('{"name":"append-consumer","private":true,"type":"module"}\n')
        run('typescript-install', [npm, 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund', '--package-lock=false', '--omit=dev', tarball], ts_consumer)
        (ts_consumer / 'tsconfig.json').write_text(json.dumps({'compilerOptions': {'strict': True, 'target': 'ES2022', 'module': 'NodeNext', 'moduleResolution': 'NodeNext', 'outDir': 'build', 'lib': ['ES2022', 'DOM']}, 'files': ['consumer.ts']}))
        run('typescript-declarations', compiler + ['-p', 'tsconfig.json'], ts_consumer)
        runners['typescript'] = [node, str(ts_consumer / 'build/consumer.js')]

        if not args.without_swift:
            swift = tool('swift'); swift_source = work / 'swift-package'; swift_source.mkdir()
            report['swift_version'] = run('swift-version', [swift, '--version'])
            shutil.copy2(ROOT / 'Package.swift', swift_source / 'Package.swift')
            stage(ROOT / 'swift', swift_source / 'swift')
            swift_consumer = work / 'swift-consumer'; stage(ROOT / 'examples/native-append/swift', swift_consumer)
            run('swift-build', [swift, 'build', '--package-path', swift_consumer, '-j', '2'])
            binary_dir = run('swift-bin-path', [swift, 'build', '--package-path', swift_consumer, '--show-bin-path'])
            runners['swift'] = [str(Path(binary_dir) / 'AppendConsumer')]

        rust = args.rust.resolve()
        if not rust.is_file(): raise RuntimeError(f'Rust reference executable missing: {rust}')
        rust_copy = work / ('rust-append-reference.exe' if os.name == 'nt' else 'rust-append-reference')
        shutil.copy2(rust, rust_copy)
        runners['rust'] = [str(rust_copy)]
        report['rust_executable_sha256'] = fingerprint(rust)
        report['runners'] = runners
        report['executable_sha256'] = {name: fingerprint(Path(runner[0])) for name, runner in runners.items()}
        report['consumer_files_sha256'] = {str(p.relative_to(work)): fingerprint(p) for dirname in ('go-consumer', 'typescript-consumer', 'swift-consumer')
            for p in (work / dirname).rglob('*') if p.is_file() and not any(x in p.parts for x in ('.build', 'node_modules'))}
        report['consumer_files_sha256']['python-consumer.py'] = fingerprint(work / 'python-consumer.py')
        traces = out / 'traces'; traces.mkdir()
        expected_controls = dict(ids=['threshold.bound', 'state.appended', 'edge.checked', 'edge.appended'],
            step_calls=0, state_calls=1, transition_calls=1, error_cleared=True, error_step_calls=0,
            error_state_calls=1, error_transition_calls=1, legacy_calls=0)
        expected_bfs = dict(states=3, transitions=6, witness=[1, 1, 1])
        expected_fuzz = dict(original_steps=6, minimized_steps=3, shrink_attempts=15, validated_original=True)
        report['expected'] = dict(controls=expected_controls, bfs=expected_bfs, fuzz=expected_fuzz)

        def same(value, expected):
            return json.dumps(value, sort_keys=True) == json.dumps(expected, sort_keys=True)

        def preferred(receipt, context):
            if type(receipt.get('legacy_calls')) is not int or receipt['legacy_calls'] != 0:
                raise RuntimeError(f'{context} invoked legacy checker')
            for key in ('append_state_calls', 'append_transition_calls'):
                if type(receipt.get(key)) is not int or receipt[key] <= 0:
                    raise RuntimeError(f'{context} did not exercise actual {key}')

        for name, runner in runners.items():
            trace = traces / f'{name}.json'
            receipt = json.loads(run(f'{name}-record', runner + ['record', str(trace)]))
            for section, fields in report['expected'].items():
                actual = receipt.get(section)
                if not isinstance(actual, dict): raise RuntimeError(f'{name} missing {section} receipt')
                for key, expected in fields.items():
                    if not same(actual.get(key), expected):
                        raise RuntimeError(f'{name} {section} {key}: expected {expected!r}, got {actual.get(key)!r}')
            controls = receipt['controls']
            if name == 'rust':
                if controls.get('static_borrow') is not True:
                    raise RuntimeError('Rust sink ownership must be identified as static borrowing')
            else:
                for key in ('escaped_sink_expired', 'nested_child_expired', 'outer_active_after_nested'):
                    if controls.get(key) is not True: raise RuntimeError(f'{name} failed native sink lifetime control {key}')
                if not set(controls.get('escaped_operations', [])).issuperset({'push', 'extend', 'snapshot', 'length', 'reserve'}):
                    raise RuntimeError(f'{name} did not test every public escaped sink operation')
            preferred(receipt, name)
            if name == 'python' and not Path(receipt['package']).resolve().is_relative_to((work / 'venv').resolve()):
                raise RuntimeError('Python imported outside its installed environment')
            if not trace.is_file(): raise RuntimeError(f'{name} did not persist append-checked witness')
            report['producers'][name] = receipt
            report['artifacts'][str(trace.relative_to(out))] = fingerprint(trace)
            save()
        callback_keys = ('append_state_calls', 'append_transition_calls')
        reference_counts = {key: report['producers']['rust'][key] for key in callback_keys}
        for name, receipt in report['producers'].items():
            if {key: receipt[key] for key in callback_keys} != reference_counts:
                raise RuntimeError(f'{name} preferred checker invocation counts differ from actual Rust pipeline')
        report['preferred_callback_reference'] = dict(pipeline=reference_counts, replay={})
        for producer in runners:
            for consumer, runner in runners.items():
                receipt = json.loads(run(f'{producer}-to-{consumer}', runner + ['replay', str(traces / f'{producer}.json')]))
                if receipt.get('outcome') != 'exact' or receipt.get('failure_reproduced') is not True or receipt.get('steps') != 3:
                    raise RuntimeError(f'{producer} -> {consumer} invalid append-checked witness replay receipt')
                preferred(receipt, f'{producer} -> {consumer}')
                report['pairs'].append({'producer': producer, 'consumer': consumer, 'receipt': receipt})
                save()
            rows = [row for row in report['pairs'] if row['producer'] == producer]
            rust_receipt = next(row['receipt'] for row in rows if row['consumer'] == 'rust')
            replay_counts = {key: rust_receipt[key] for key in callback_keys}
            for row in rows:
                if {key: row['receipt'][key] for key in callback_keys} != replay_counts:
                    raise RuntimeError(f'{producer} -> {row["consumer"]} preferred replay checker counts differ from Rust')
            report['preferred_callback_reference']['replay'][producer] = replay_counts
            save()

        # Preserve the four established portable profiles while exercising the
        # preferred append checker dispatch through installed APIs.
        corpus_source = work / 'conformance'; stage(ROOT / 'conformance', corpus_source)
        env.update(STATELESS_PAIRED_CORPUS=str(corpus_source / 'paired-v1.tsv'),
                   STATELESS_SEARCH_CORPUS=str(corpus_source / 'search-v1.tsv'),
                   STATELESS_SEARCH_HISTORY=str(corpus_source / 'search-v1-history.json'),
                   STATELESS_BINARY_CORPUS=str(corpus_source / 'native-binary-v1.json'),
                   STATELESS_BFS_CORPUS=str(corpus_source / 'bfs-controls-v1.json'))
        go_corpus = work / ('go-corpus.exe' if os.name == 'nt' else 'go-corpus')
        run('baseline-go-corpus-build', [go, 'build', '-trimpath', '-o', go_corpus, './cmd/stateless-corpus'], go_source)
        baseline_runners = [[str(py), '-I', '-m', 'statelessness.conformance', '--runner'],
                            [str(go_corpus)], [node, str(ts_consumer / 'node_modules/@jagalite/statelessness/bin/corpus.mjs')]]
        if not args.without_swift:
            run('baseline-swift-corpus-build', [swift, 'build', '--package-path', swift_source, '--product', 'stateless-corpus', '-j', '2'])
            swift_bin_dir = run('baseline-swift-bin-path', [swift, 'build', '--package-path', swift_source, '--show-bin-path'])
            baseline_runners.append([str(Path(swift_bin_dir) / 'stateless-corpus')])
        if args.rust_portable:
            rust_portable = args.rust_portable.resolve()
            if not rust_portable.is_file(): raise RuntimeError(f'Rust portable runner missing: {rust_portable}')
            rust_portable_copy = work / ('rust-portable.exe' if os.name == 'nt' else 'rust-portable')
            shutil.copy2(rust_portable, rust_portable_copy)
            baseline_runners.append([str(rust_portable_copy)])
            report['rust_portable_executable_sha256'] = fingerprint(rust_portable_copy)
        baseline_command = [sys.executable, '-I', corpus_source / 'run.py', '--report', out / 'portable-baseline.json']
        for runner in baseline_runners: baseline_command += ['--runner', json.dumps(runner)]
        run('installed-four-profile-baseline', baseline_command)
        report['baseline_runners'] = baseline_runners
        report['portable_baseline_sha256'] = fingerprint(out / 'portable-baseline.json')
        report['artifacts'].update({str(p.relative_to(out)): fingerprint(p) for directory in (packages, traces) for p in directory.iterdir() if p.is_file()})
        report['staged_sources'] = {str(p.relative_to(work)): fingerprint(p) for p in work.rglob('*') if p.is_file() and not any(x in p.parts for x in ('.build', 'node_modules', 'venv', 'builder-venv', '__pycache__'))}
        report['status'] = 'passed'; save()
        print(f'Installed append-check qualification passed: {len(runners)} append checker applications, {len(report["pairs"])} witness replay pairs, portable baseline regression; {out}', flush=True)
    except BaseException as exc:
        report['status'] = 'failed'; report['error'] = str(exc); save(); raise


if __name__ == '__main__':
    main()

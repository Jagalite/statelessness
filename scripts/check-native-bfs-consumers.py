#!/usr/bin/env python3
"""Qualify native BFS applications from staged/installed packages.

Submit this driver through Kyoto. It runs no cargo commands. Every producer and
consumer invocation is a new process outside the checkout. Candidate evidence
only: this does not promote bfs-controls-v1 or claim performance/complete parity.
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
    parser.add_argument('--rust', type=Path, required=True, help='Already Kyoto-built native-bfs Rust reference executable')
    parser.add_argument('--rust-portable', type=Path, help='Optional already-built Rust four-profile corpus runner')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        raise RuntimeError('output must be new or empty; preserve previous receipts')
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='stateless-native-bfs-')).resolve()
    logs = out / 'logs'; logs.mkdir()
    packages = out / 'packages'; packages.mkdir()
    env = dict(os.environ, PYTHONPATH='', PYTHONNOUSERSITE='1', GOPROXY='off', GOTOOLCHAIN='local', GOWORK='off', CGO_ENABLED='0')
    commands = []
    report = {'status': 'running', 'profile': 'bfs-controls-v1 candidate', 'workspace': str(work),
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
        shutil.copy2(ROOT / 'examples/native-bfs/python/consumer.py', work / 'python-consumer.py')
        runners = {'python': [str(py), '-I', str(work / 'python-consumer.py')]}

        go = tool('go'); go_source = work / 'go-package'; stage(ROOT / 'go', go_source)
        report['go_version'] = run('go-version', [go, 'version'])
        go_consumer = work / 'go-consumer'; stage(ROOT / 'examples/native-bfs/go', go_consumer)
        (go_consumer / 'go.mod').write_text('module bfs-consumer\n\ngo 1.23.0\n\nrequire github.com/Jagalite/statelessness/go v0.0.0\nreplace github.com/Jagalite/statelessness/go => ../go-package\n')
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
        ts_consumer = work / 'typescript-consumer'; stage(ROOT / 'examples/native-bfs/typescript', ts_consumer)
        (ts_consumer / 'package.json').write_text('{"name":"bfs-consumer","private":true,"type":"module"}\n')
        run('typescript-install', [npm, 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund', '--package-lock=false', '--omit=dev', tarball], ts_consumer)
        (ts_consumer / 'tsconfig.json').write_text(json.dumps({'compilerOptions': {'strict': True, 'target': 'ES2022', 'module': 'NodeNext', 'moduleResolution': 'NodeNext', 'outDir': 'build', 'lib': ['ES2022', 'DOM']}, 'files': ['consumer.ts']}))
        run('typescript-declarations', compiler + ['-p', 'tsconfig.json'], ts_consumer)
        runners['typescript'] = [node, str(ts_consumer / 'build/consumer.js')]

        if not args.without_swift:
            swift = tool('swift'); swift_source = work / 'swift-package'; swift_source.mkdir()
            report['swift_version'] = run('swift-version', [swift, '--version'])
            shutil.copy2(ROOT / 'Package.swift', swift_source / 'Package.swift')
            stage(ROOT / 'swift', swift_source / 'swift')
            swift_consumer = work / 'swift-consumer'; stage(ROOT / 'examples/native-bfs/swift', swift_consumer)
            run('swift-build', [swift, 'build', '--package-path', swift_consumer, '-j', '2'])
            binary_dir = run('swift-bin-path', [swift, 'build', '--package-path', swift_consumer, '--show-bin-path'])
            runners['swift'] = [str(Path(binary_dir) / 'BFSConsumer')]

        rust = args.rust.resolve()
        if not rust.is_file(): raise RuntimeError(f'Rust reference executable missing: {rust}')
        rust_copy = work / ('rust-bfs-reference.exe' if os.name == 'nt' else 'rust-bfs-reference')
        shutil.copy2(rust, rust_copy)
        runners['rust'] = [str(rust_copy)]
        report['rust_executable_sha256'] = fingerprint(rust)
        report['runners'] = runners
        report['executable_sha256'] = {name: fingerprint(Path(runner[0])) for name, runner in runners.items()}
        report['consumer_files_sha256'] = {str(p.relative_to(work)): fingerprint(p) for dirname in ('go-consumer', 'typescript-consumer', 'swift-consumer')
            for p in (work / dirname).rglob('*') if p.is_file() and not any(x in p.parts for x in ('.build', 'node_modules'))}
        report['consumer_files_sha256']['python-consumer.py'] = fingerprint(work / 'python-consumer.py')
        traces = out / 'traces'; traces.mkdir()
        histories = [[0], [1], [1, 0], [1, 1], [1, 1, 0], [1, 1, 1]]
        expected = {
            'base': dict(termination='failure_found', states=3, transitions=6, estimated_retained_state_bytes=15,
                         witness=[1, 1, 1], failure_phase='state', failure_property='threshold.bound',
                         observed_histories=histories, initial_observations=1, check_errors=0, observer_callbacks=7),
            'cap': dict(termination='estimated_state_byte_limit', states=2, transitions=4, estimated_retained_state_bytes=10,
                        witness=None, failure_phase=None, failure_property=None,
                        observed_histories=histories[:4], initial_observations=1, check_errors=0, observer_callbacks=5),
            'precancel': dict(termination='cancelled', states=0, transitions=0, estimated_retained_state_bytes=None,
                              witness=None, failure_phase=None, failure_property=None, observed_histories=[],
                              initial_observations=0, check_errors=0, model_callbacks=0, observer_callbacks=0),
        }
        report['expected'] = expected
        for name, runner in runners.items():
            trace = traces / f'{name}.json'
            receipt = json.loads(run(f'{name}-record', runner + ['record', str(trace)]))
            for scenario, fields in expected.items():
                actual = receipt.get(scenario)
                if not isinstance(actual, dict): raise RuntimeError(f'{name} missing {scenario} receipt')
                for key, value in fields.items():
                    if json.dumps(actual.get(key), sort_keys=True) != json.dumps(value, sort_keys=True):
                        raise RuntimeError(f'{name} {scenario} {key}: expected {value!r}, got {actual.get(key)!r}')
                if type(actual.get('model_callbacks')) is not int or (scenario != 'precancel' and actual['model_callbacks'] <= 0):
                    raise RuntimeError(f'{name} {scenario} missing actual callback accounting')
            if name == 'python' and not Path(receipt['package']).resolve().is_relative_to((work / 'venv').resolve()):
                raise RuntimeError('Python imported outside its installed environment')
            if not trace.is_file(): raise RuntimeError(f'{name} did not persist BFS witness')
            report['producers'][name] = receipt
            report['artifacts'][str(trace.relative_to(out))] = fingerprint(trace)
            save()
        for producer in runners:
            for consumer, runner in runners.items():
                receipt = json.loads(run(f'{producer}-to-{consumer}', runner + ['replay', str(traces / f'{producer}.json')]))
                if receipt.get('outcome') != 'exact' or receipt.get('failure_reproduced') is not True or receipt.get('steps') != 3:
                    raise RuntimeError(f'{producer} -> {consumer} invalid BFS witness replay receipt')
                report['pairs'].append({'producer': producer, 'consumer': consumer, 'receipt': receipt})
                save()

        # Preserve the four established portable profiles while exercising the
        # new BFS kernel and added internal report fields through installed APIs.
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
        print(f'Installed BFS qualification passed: {len(runners)} limit/observer applications, {len(report["pairs"])} witness replay pairs, portable baseline regression; {out}', flush=True)
    except BaseException as exc:
        report['status'] = 'failed'; report['error'] = str(exc); save(); raise


if __name__ == '__main__':
    main()

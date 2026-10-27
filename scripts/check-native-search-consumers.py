#!/usr/bin/env python3
"""Qualify native search applications from staged/installed packages.

Submit this driver through Kyoto. It runs no cargo commands. Every producer and
consumer invocation is a new process outside the checkout. Candidate evidence
only: this does not promote search-v1 or claim performance/complete parity.
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
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        raise RuntimeError('output must be new or empty; preserve previous receipts')
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='stateless-native-search-')).resolve()
    logs = out / 'logs'; logs.mkdir()
    packages = out / 'packages'; packages.mkdir()
    env = dict(os.environ, PYTHONPATH='', PYTHONNOUSERSITE='1', GOPROXY='off', GOTOOLCHAIN='local', GOWORK='off', CGO_ENABLED='0')
    commands = []
    report = {'status': 'running', 'profile': 'search-v1 candidate', 'workspace': str(work),
              'platform': platform.platform(), 'commands': commands, 'pairs': [], 'producers': {},
              'scope': 'three-language subset' if args.without_swift else 'four native languages',
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
        sources = {p: fingerprint(ROOT / p) for p in tracked if p and (ROOT / p).is_file() and not p.startswith(('validation/', 'target/'))}
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
        shutil.copy2(ROOT / 'examples/native-search/python/consumer.py', work / 'python-consumer.py')
        runners = {'python': [str(py), '-I', str(work / 'python-consumer.py')]}

        go = tool('go'); go_source = work / 'go-package'; stage(ROOT / 'go', go_source)
        report['go_version'] = run('go-version', [go, 'version'])
        go_consumer = work / 'go-consumer'; stage(ROOT / 'examples/native-search/go', go_consumer)
        (go_consumer / 'go.mod').write_text('module search-consumer\n\ngo 1.23.0\n\nrequire github.com/Jagalite/statelessness/go v0.0.0\nreplace github.com/Jagalite/statelessness/go => ../go-package\n')
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
        ts_consumer = work / 'typescript-consumer'; stage(ROOT / 'examples/native-search/typescript', ts_consumer)
        (ts_consumer / 'package.json').write_text('{"name":"search-consumer","private":true,"type":"module"}\n')
        run('typescript-install', [npm, 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund', '--package-lock=false', '--omit=dev', tarball], ts_consumer)
        (ts_consumer / 'tsconfig.json').write_text(json.dumps({'compilerOptions': {'strict': True, 'target': 'ES2022', 'module': 'NodeNext', 'moduleResolution': 'NodeNext', 'outDir': 'build', 'lib': ['ES2022', 'DOM']}, 'files': ['consumer.ts']}))
        run('typescript-declarations', compiler + ['-p', 'tsconfig.json'], ts_consumer)
        runners['typescript'] = [node, str(ts_consumer / 'build/consumer.js')]

        if not args.without_swift:
            swift = tool('swift'); swift_source = work / 'swift-package'; swift_source.mkdir()
            report['swift_version'] = run('swift-version', [swift, '--version'])
            shutil.copy2(ROOT / 'Package.swift', swift_source / 'Package.swift')
            stage(ROOT / 'swift', swift_source / 'swift')
            swift_consumer = work / 'swift-consumer'; stage(ROOT / 'examples/native-search/swift', swift_consumer)
            run('swift-build', [swift, 'build', '--package-path', swift_consumer, '-j', '2'])
            binary_dir = run('swift-bin-path', [swift, 'build', '--package-path', swift_consumer, '--show-bin-path'])
            runners['swift'] = [str(Path(binary_dir) / 'SearchConsumer')]

        report['runners'] = runners
        report['executable_sha256'] = {name: fingerprint(Path(runner[0])) for name, runner in runners.items()}
        report['consumer_files_sha256'] = {str(p.relative_to(work)): fingerprint(p) for dirname in ('go-consumer', 'typescript-consumer', 'swift-consumer')
            for p in (work / dirname).rglob('*') if p.is_file() and not any(x in p.parts for x in ('.build', 'node_modules'))}
        report['consumer_files_sha256']['python-consumer.py'] = fingerprint(work / 'python-consumer.py')
        traces = out / 'traces'; traces.mkdir()
        for name, runner in runners.items():
            receipt = json.loads(run(f'{name}-record', runner + ['record', str(traces / f'{name}.json')]))
            if receipt.get('outcome') != 'exact' or receipt.get('failure_reproduced') is not True or receipt.get('steps') != 3:
                raise RuntimeError(f'{name} invalid producer receipt')
            if (type(receipt.get('original_steps')) is not int or receipt['original_steps'] <= receipt['steps']
                    or receipt.get('minimized_steps') != receipt['steps']
                    or type(receipt.get('shrink_attempts')) is not int or receipt['shrink_attempts'] <= 0
                    or receipt.get('validated_original') is not True
                    or receipt.get('phase') != 'state' or receipt.get('property') != 'threshold.bound'):
                raise RuntimeError(f'{name} did not demonstrate actual causal witness reduction')
            if name == 'python' and not Path(receipt['package']).resolve().is_relative_to((work / 'venv').resolve()):
                raise RuntimeError('Python imported outside its installed environment')
            report['producers'][name] = receipt
            save()
        for producer in runners:
            for artifact, filename, expected_steps in (
                ('original', f'{producer}.json.original.json', report['producers'][producer]['original_steps']),
                ('minimized', f'{producer}.json', report['producers'][producer]['minimized_steps']),
            ):
                for consumer, runner in runners.items():
                    receipt = json.loads(run(f'{producer}-{artifact}-to-{consumer}', runner + ['replay', str(traces / filename)]))
                    if receipt.get('outcome') != 'exact' or receipt.get('failure_reproduced') is not True or receipt.get('steps') != expected_steps:
                        raise RuntimeError(f'{producer} {artifact} -> {consumer} invalid replay receipt')
                    report['pairs'].append({'producer': producer, 'consumer': consumer, 'artifact': artifact, 'receipt': receipt})
                    save()
        report['artifacts'] = {str(p.relative_to(out)): fingerprint(p) for directory in (packages, traces) for p in directory.iterdir() if p.is_file()}
        report['staged_sources'] = {str(p.relative_to(work)): fingerprint(p) for p in work.rglob('*') if p.is_file() and not any(x in p.parts for x in ('.build', 'node_modules', 'venv', 'builder-venv', '__pycache__'))}
        report['status'] = 'passed'; save()
        print(f'Fresh-process native search qualification passed: {len(report["pairs"])} ordered artifact replays; {out}', flush=True)
    except BaseException as exc:
        report['status'] = 'failed'; report['error'] = str(exc); save(); raise


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Qualify native finite research applications from staged/installed packages.

Submit this driver through Kyoto. It runs no cargo commands. Each application
runs in a fresh process outside the checkout. Candidate finite evidence only:
this does not enable production pruning or promote research-equivalence-v1.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
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
    parser.add_argument('--rust', type=Path, required=True, help='Already Kyoto-built native-research Rust reference executable')
    parser.add_argument('--rust-portable', type=Path, help='Optional already-built Rust four-profile corpus runner')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        raise RuntimeError('output must be new or empty; preserve previous receipts')
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='stateless-native-research-')).resolve()
    logs = out / 'logs'; logs.mkdir()
    packages = out / 'packages'; packages.mkdir()
    env = dict(os.environ, PYTHONPATH='', PYTHONNOUSERSITE='1', GOPROXY='off', GOTOOLCHAIN='local', GOWORK='off', CGO_ENABLED='0')
    commands = []
    report = {'status': 'running', 'profile': 'research-equivalence-v1 candidate', 'workspace': str(work),
              'platform': platform.platform(), 'commands': commands, 'results': {},
              'artifacts': {},
              'scope': 'three native languages plus Rust' if args.without_swift else 'four native languages plus Rust',
              'python_version': sys.version}

    def save():
        (out / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(name, argv, cwd=work, expect_failure=False):
        unchanged_sources()
        command = list(map(str, argv))
        print(f'[{name}] ' + ' '.join(command), flush=True)
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=os.name == 'posix')
        try:
            raw, _ = process.communicate(timeout=900)
        except subprocess.TimeoutExpired as exc:
            if os.name == 'posix': os.killpg(process.pid, signal.SIGKILL)
            else: process.kill()
            raw, _ = process.communicate()
            log = logs / f'{name}.log'; log.write_bytes(raw)
            commands.append({'name': name, 'argv': command, 'cwd': str(cwd),
                             'timeout_seconds': 900, 'timed_out': True,
                             'log_sha256': fingerprint(log)})
            save()
            raise RuntimeError(f'{name} timed out; partial output preserved') from exc
        output = raw.decode('utf-8', 'replace')
        # Keep exact bytes, including invalid UTF-8 and command failure output.
        (logs / f'{name}.log').write_bytes(raw)
        commands.append({'name': name, 'argv': command, 'cwd': str(cwd), 'exit_code': process.returncode,
                         'log_sha256': fingerprint(logs / f'{name}.log'), 'expected_failure': expect_failure})
        save()
        unchanged_sources()
        if process.returncode:
            print(output, flush=True)
            if not expect_failure: raise RuntimeError(f'{name} failed ({process.returncode})')
        elif expect_failure:
            raise RuntimeError(f'{name} unexpectedly succeeded')
        return output.strip()

    def stage(source, dest):
        shutil.copytree(source, dest, ignore=shutil.ignore_patterns('.build', '.swiftpm', 'node_modules', 'dist', '__pycache__', '*.egg-info', 'build', '*.tgz'))

    def tool(name):
        result = shutil.which(name)
        if not result: raise RuntimeError(f'{name} is required')
        return result

    frozen_sources = {}
    frozen_tools = {}
    def is_frozen_source(name):
        return name.startswith(('python/', 'go/', 'typescript/', 'swift/', 'examples/native-research/', 'conformance/')) or name in (
            'Package.swift', 'scripts/check-native-research-consumers.py',
            'conformance/research-equivalence-v1.json', 'spec/research-equivalence-v1.md')
    def unchanged_sources():
        if not frozen_sources: return
        names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
        current = {name for name in names if name and is_frozen_source(name) and (ROOT/name).is_file()}
        changed = [name for name, expected in frozen_sources.items()
                   if not (ROOT/name).is_file() or fingerprint(ROOT/name) != expected]
        changed.extend(sorted(current - set(frozen_sources)))
        changed.extend(name for name, expected in frozen_tools.items()
                       if not Path(name).is_file() or fingerprint(Path(name)) != expected)
        if changed:
            report['source_drift'] = changed; save()
            raise RuntimeError('frozen qualification sources changed: ' + ', '.join(changed))

    try:
        tracked = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
        sources = {p: fingerprint(ROOT / p) for p in tracked if p and (ROOT / p).is_file()
                   and (not p.startswith(('validation/', 'target/')) or p.startswith('validation/corpus/'))}
        report['source_files'] = sources
        report['source_manifest_sha256'] = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
        report['source_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        frozen_sources.update({name: digest for name, digest in sources.items() if is_frozen_source(name)})
        report['frozen_sources'] = dict(frozen_sources)
        frozen_tools[str(args.rust.resolve())] = fingerprint(args.rust.resolve())
        if args.rust_portable:
            frozen_tools[str(args.rust_portable.resolve())] = fingerprint(args.rust_portable.resolve())
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
        shutil.copy2(ROOT / 'examples/native-research/python/consumer.py', work / 'python-consumer.py')
        runners = {'python': [str(py), '-I', str(work / 'python-consumer.py')]}

        go = tool('go'); go_source = work / 'go-package'; stage(ROOT / 'go', go_source)
        report['go_version'] = run('go-version', [go, 'version'])
        go_consumer = work / 'go-consumer'; stage(ROOT / 'examples/native-research/go', go_consumer)
        (go_consumer / 'go.mod').write_text('module research-consumer\n\ngo 1.23.0\n\nrequire github.com/Jagalite/statelessness/go v0.0.0\nreplace github.com/Jagalite/statelessness/go => ../go-package\n')
        go_bin = work / ('go-consumer.exe' if os.name == 'nt' else 'go-consumer-bin')
        run('go-build', [go, 'build', '-trimpath', '-o', go_bin, '.'], go_consumer)
        runners['go'] = [str(go_bin)]

        node, npm = tool('node'), tool('npm')
        report['node_version'] = run('node-version', [node, '--version'])
        report['npm_version'] = run('npm-version', [npm, '--version'])
        ts_source = work / 'typescript-package'; stage(ROOT / 'typescript', ts_source)
        local_tsc = ROOT / 'typescript/node_modules/typescript/bin/tsc'
        compiler = [node, str(local_tsc)] if local_tsc.is_file() else [tool('tsc')]
        compiler_path = local_tsc if local_tsc.is_file() else Path(compiler[0])
        frozen_tools[str(compiler_path)] = fingerprint(compiler_path)
        report['external_tool_inputs_sha256'] = dict(frozen_tools)
        report['typescript_version'] = run('typescript-version', compiler + ['--version'])
        run('typescript-package-build', compiler + ['-p', 'tsconfig.json'], ts_source)
        run('typescript-pack', [npm, 'pack', '--ignore-scripts', '--pack-destination', packages], ts_source)
        tarball, = packages.glob('*.tgz')
        ts_consumer = work / 'typescript-consumer'; stage(ROOT / 'examples/native-research/typescript', ts_consumer)
        (ts_consumer / 'package.json').write_text('{"name":"research-consumer","private":true,"type":"module"}\n')
        run('typescript-install', [npm, 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund', '--package-lock=false', '--omit=dev', tarball], ts_consumer)
        (ts_consumer / 'tsconfig.json').write_text(json.dumps({'compilerOptions': {'strict': True, 'target': 'ES2022', 'module': 'NodeNext', 'moduleResolution': 'NodeNext', 'outDir': 'build', 'lib': ['ES2022', 'DOM']}, 'files': ['consumer.ts']}))
        run('typescript-declarations', compiler + ['-p', 'tsconfig.json'], ts_consumer)
        runners['typescript'] = [node, str(ts_consumer / 'build/consumer.js')]

        if not args.without_swift:
            swift = tool('swift'); swift_source = work / 'swift-package'; swift_source.mkdir()
            report['swift_version'] = run('swift-version', [swift, '--version'])
            shutil.copy2(ROOT / 'Package.swift', swift_source / 'Package.swift')
            stage(ROOT / 'swift', swift_source / 'swift')
            swift_consumer = work / 'swift-consumer'; stage(ROOT / 'examples/native-research/swift', swift_consumer)
            run('swift-build', [swift, 'build', '--package-path', swift_consumer, '-j', '2'])
            binary_dir = run('swift-bin-path', [swift, 'build', '--package-path', swift_consumer, '--show-bin-path'])
            runners['swift'] = [str(Path(binary_dir) / 'ResearchConsumer')]


        rust = args.rust.resolve()
        if not rust.is_file(): raise RuntimeError(f'Rust reference executable missing: {rust}')
        rust_copy = work / ('rust-research-reference.exe' if os.name == 'nt' else 'rust-research-reference')
        shutil.copy2(rust, rust_copy)
        runners['rust'] = [str(rust_copy)]
        report['rust_executable_sha256'] = fingerprint(rust)
        report['runners'] = runners
        report['executable_sha256'] = {name: fingerprint(Path(runner[0])) for name, runner in runners.items()}
        report['consumer_files_sha256'] = {str(p.relative_to(work)): fingerprint(p) for dirname in ('go-consumer', 'typescript-consumer', 'swift-consumer')
            for p in (work / dirname).rglob('*') if p.is_file() and not any(x in p.parts for x in ('.build', 'node_modules'))}
        report['consumer_files_sha256']['python-consumer.py'] = fingerprint(work / 'python-consumer.py')
        corpus = ROOT / 'conformance/research-equivalence-v1.json'
        staged_corpus = work / 'research-equivalence-v1.json'
        shutil.copy2(corpus, staged_corpus)
        fixture = json.loads(staged_corpus.read_text())
        outputs = out / 'outputs'; outputs.mkdir()
        expected = expected_results(fixture)
        report['case_classification'] = {
            'shared_finite_cases': len(fixture['cases']),
            'host_only_cases_excluded_from_cross_engine_counts': fixture.get('native_admission_cases', []),
            'production_pruning_enabled': False}
        report['fixture_sha256'] = fingerprint(staged_corpus)
        report['expected_results'] = expected
        for name, runner in runners.items():
            destination = outputs / f'{name}.json'
            receipt = json.loads(run(f'{name}-research', runner + ['run', str(staged_corpus), str(destination)]))
            if receipt.get('mode') != 'run' or receipt.get('case_count') != len(expected['results']) or receipt.get('production_pruning_enabled') is not False:
                raise RuntimeError(f'{name} omitted finite research receipt or pruning boundary')
            if name == 'python' and not Path(receipt['package']).resolve().is_relative_to((work/'venv').resolve()):
                raise RuntimeError('Python imported outside installed environment')
            actual = json.loads(destination.read_text())
            if json.dumps(actual, sort_keys=True, ensure_ascii=False) != json.dumps(expected, sort_keys=True, ensure_ascii=False):
                report['results'][name] = actual
                save()
                raise RuntimeError(f'{name} full finite research result differs from independent goldens')
            report['results'][name] = actual
            save()
        # Preserve the four established portable profiles while exercising the
        # preferred codec dispatch and replay evidence through installed APIs.
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
        report['artifacts'] = {str(p.relative_to(out)): fingerprint(p) for directory in (packages, outputs) for p in directory.iterdir() if p.is_file()}
        report['staged_sources'] = {str(p.relative_to(work)): fingerprint(p) for p in work.rglob('*') if p.is_file() and not any(x in p.parts for x in ('.build', 'node_modules', 'venv', 'builder-venv', '__pycache__'))}
        unchanged_sources()
        report['status'] = 'passed'; save()
        print(f'Installed finite research qualification passed: {len(runners)} applications; {out}', flush=True)
    except BaseException as exc:
        report['status'] = 'failed'; report['error'] = str(exc); save(); raise


def expected_results(fixture):
    # The independent fixture owner freezes this closed normalized envelope.
    # Host-only extension results are deliberately not admitted as finite proofs.
    return {'profile': 'research-equivalence-v1',
            'results': [dict(case=row['case'], **row['expected']) for row in fixture['cases']]}


if __name__ == '__main__':
    main()

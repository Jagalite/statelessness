#!/usr/bin/env python3
"""Qualify native checked/Recorder applications from staged/installed packages.

Submit this driver through Kyoto. It runs no cargo commands. Every producer and
consumer invocation is a new process outside the checkout. Candidate evidence
only: this does not promote checked-results-v1 and runtime Recorder or claim performance/complete parity.
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
    parser.add_argument('--rust', type=Path, required=True, help='Already Kyoto-built native-checked Rust reference executable')
    parser.add_argument('--rust-portable', type=Path, help='Optional already-built Rust four-profile corpus runner')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        raise RuntimeError('output must be new or empty; preserve previous receipts')
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='stateless-native-checked-')).resolve()
    logs = out / 'logs'; logs.mkdir()
    packages = out / 'packages'; packages.mkdir()
    env = dict(os.environ, PYTHONPATH='', PYTHONNOUSERSITE='1', GOPROXY='off', GOTOOLCHAIN='local', GOWORK='off', CGO_ENABLED='0')
    commands = []
    report = {'status': 'running', 'profile': 'checked-results-v1 and runtime Recorder candidate', 'workspace': str(work),
              'platform': platform.platform(), 'commands': commands, 'pairs': [], 'producers': {},
              'artifacts': {},
              'scope': 'three native languages plus Rust' if args.without_swift else 'four native languages plus Rust',
              'python_version': sys.version}

    def save():
        (out / 'qualification.json').write_text(json.dumps(report, indent=2) + '\n')

    def run(name, argv, cwd=work, expect_failure=False):
        command = list(map(str, argv))
        print(f'[{name}] ' + ' '.join(command), flush=True)
        completed = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=900)
        output = completed.stdout.decode('utf-8', 'replace')
        (logs / f'{name}.log').write_text(output)
        commands.append({'name': name, 'argv': command, 'cwd': str(cwd), 'exit_code': completed.returncode,
                         'log_sha256': fingerprint(logs / f'{name}.log'), 'expected_failure': expect_failure})
        save()
        if completed.returncode:
            print(output, flush=True)
            if not expect_failure: raise RuntimeError(f'{name} failed ({completed.returncode})')
        elif expect_failure:
            raise RuntimeError(f'{name} accepted a diagnostic as checked authority')
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
        shutil.copy2(ROOT / 'examples/native-checked/python/consumer.py', work / 'python-consumer.py')
        runners = {'python': [str(py), '-I', str(work / 'python-consumer.py')]}

        go = tool('go'); go_source = work / 'go-package'; stage(ROOT / 'go', go_source)
        report['go_version'] = run('go-version', [go, 'version'])
        go_consumer = work / 'go-consumer'; stage(ROOT / 'examples/native-checked/go', go_consumer)
        (go_consumer / 'go.mod').write_text('module checked-consumer\n\ngo 1.23.0\n\nrequire github.com/Jagalite/statelessness/go v0.0.0\nreplace github.com/Jagalite/statelessness/go => ../go-package\n')
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
        ts_consumer = work / 'typescript-consumer'; stage(ROOT / 'examples/native-checked/typescript', ts_consumer)
        (ts_consumer / 'package.json').write_text('{"name":"checked-consumer","private":true,"type":"module"}\n')
        run('typescript-install', [npm, 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund', '--package-lock=false', '--omit=dev', tarball], ts_consumer)
        (ts_consumer / 'tsconfig.json').write_text(json.dumps({'compilerOptions': {'strict': True, 'target': 'ES2022', 'module': 'NodeNext', 'moduleResolution': 'NodeNext', 'outDir': 'build', 'lib': ['ES2022', 'DOM']}, 'files': ['consumer.ts']}))
        run('typescript-declarations', compiler + ['-p', 'tsconfig.json'], ts_consumer)
        runners['typescript'] = [node, str(ts_consumer / 'build/consumer.js')]

        if not args.without_swift:
            swift = tool('swift'); swift_source = work / 'swift-package'; swift_source.mkdir()
            report['swift_version'] = run('swift-version', [swift, '--version'])
            shutil.copy2(ROOT / 'Package.swift', swift_source / 'Package.swift')
            stage(ROOT / 'swift', swift_source / 'swift')
            swift_consumer = work / 'swift-consumer'; stage(ROOT / 'examples/native-checked/swift', swift_consumer)
            run('swift-build', [swift, 'build', '--package-path', swift_consumer, '-j', '2'])
            binary_dir = run('swift-bin-path', [swift, 'build', '--package-path', swift_consumer, '--show-bin-path'])
            runners['swift'] = [str(Path(binary_dir) / 'CheckedConsumer')]
            positive = work / 'swift-authentic-token.swift'
            positive.write_text('import Statelessness\nfunc authentic(_ r: Recorder, _ t: CheckedTurn<Int,Int,String>) throws { try r.observeChecked(t) }\n')
            run('swift-authentic-token-typecheck', [tool('swiftc'), '-typecheck', '-I', Path(binary_dir) / 'Modules', positive])
            negative = work / 'swift-forged-token.swift'
            negative.write_text('import Statelessness\nfunc forged(_ r: Recorder, _ d: TurnObservation<Int,Int,String>) throws { try r.observeChecked(d) }\n')
            diagnostic = run('swift-diagnostic-not-token', [tool('swiftc'), '-typecheck', '-I', Path(binary_dir) / 'Modules', negative], expect_failure=True)
            if not all(fragment in diagnostic for fragment in ('cannot convert value of type', 'expected argument type', 'CheckedTurn', 'TurnObservation')):
                raise RuntimeError('Swift negative compilation failed for an unrelated reason')
            report['swift_static_token_rejection'] = True


        rust = args.rust.resolve()
        if not rust.is_file(): raise RuntimeError(f'Rust reference executable missing: {rust}')
        rust_copy = work / ('rust-checked-reference.exe' if os.name == 'nt' else 'rust-checked-reference')
        shutil.copy2(rust, rust_copy)
        runners['rust'] = [str(rust_copy)]
        report['rust_executable_sha256'] = fingerprint(rust)
        report['runners'] = runners
        report['executable_sha256'] = {name: fingerprint(Path(runner[0])) for name, runner in runners.items()}
        report['consumer_files_sha256'] = {str(p.relative_to(work)): fingerprint(p) for dirname in ('go-consumer', 'typescript-consumer', 'swift-consumer')
            for p in (work / dirname).rglob('*') if p.is_file() and not any(x in p.parts for x in ('.build', 'node_modules'))}
        report['consumer_files_sha256']['python-consumer.py'] = fingerprint(work / 'python-consumer.py')
        traces = out / 'traces'; traces.mkdir()
        contract = json.loads((ROOT / 'examples/native-checked/contract.json').read_text())
        normal_report = contract['normal_report']
        false_differences = dict(disposition=False, outputs=False, state=False, checks=False)
        def checks(state, edge=False):
            values = [dict(id='threshold.bound', status='failed' if state >= 3 else 'passed', details='at least three' if state >= 3 else ''), dict(id='state.appended',status='passed',details='')]
            return values + ([dict(id='edge.checked',status='passed',details='')] if edge else [])
        def report_at(sequence):
            return dict(normal_report, steps_verified=sequence, failure_reproduced=sequence == 3)
        initial = dict(kind='initial', sequence=0, state=0, checks=checks(0), expected_state='30', actual_state='30',
                       expected_checks=checks(0), differences=false_differences, report=report_at(0))
        observations = [initial]
        for sequence in (1, 2, 3):
            expected = dict(input='31', disposition=dict(kind='accepted', reason=''), outputs=['6368616e676564'],
                            post_state=str(sequence).encode('ascii').hex(), checks=checks(sequence, True))
            observations.append(dict(kind='turn', sequence=sequence, before=sequence - 1, input=1,
                transition=dict(state=sequence, outputs=['changed'], disposition=dict(kind='accepted', reason='')),
                checks=checks(sequence, True), state_check_count=2, expected=expected, actual_input='31', actual_outputs=['6368616e676564'],
                actual_state=str(sequence).encode('ascii').hex(), differences=false_differences, report=report_at(sequence)))
        normal_session = dict(report=normal_report, observations=observations)
        ring_report = dict(normal_report,steps_verified=1,failure_reproduced=False)
        ring_initial = dict(initial,state=1,expected_state='31',actual_state='31',checks=checks(1),expected_checks=checks(1))
        ring_turn = dict(observations[1],before=1,transition=dict(state=2,outputs=['changed'],disposition=dict(kind='accepted',reason='')),
                         checks=checks(2,True),actual_state='32',expected=dict(observations[1]['expected'],post_state='32',checks=checks(2,True)),report=ring_report)
        ring_session = dict(report=ring_report,observations=[ring_initial,ring_turn])
        report['expected_ring_session'] = ring_session
        report['expected_session'] = normal_session
        report['finite_binary_limits'] = contract['limits']
        report['json_pairs'] = []

        def same(actual, expected):
            return json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)

        def preferred(receipt, context):
            if type(receipt.get('legacy_encoder_calls')) is not int or receipt['legacy_encoder_calls'] != 0:
                raise RuntimeError(f'{context} invoked legacy allocating encoder')
            if type(receipt.get('codec_into_calls')) is not int or receipt['codec_into_calls'] <= 0:
                raise RuntimeError(f'{context} did not execute preferred incremental codecs')

        def validate_session(receipt, context):
            preferred(receipt, context)
            for kind in ('rich', 'bounded'):
                if not same(receipt.get(kind), normal_session):
                    raise RuntimeError(f'{context} {kind} typed/encoded observations or report differ from hand-authored model expectations')

        for name, runner in runners.items():
            prefix = traces / name
            receipt = json.loads(run(f'{name}-record', runner + ['record', str(prefix)]))
            validate_session(receipt, name)
            controls = receipt.get('controls', {})
            for key in ('check_once','token_reused','sampled_rejected','duplicate_rejected','gap_rejected','origin_preserved','ring_state_prefix_only','bounded_error_prefix'):
                if controls.get(key) is not True: raise RuntimeError(f'{name} failed {key}')
            for key in ('attach_rechecks','handoff_rechecks'):
                if controls.get(key) is not False: raise RuntimeError(f'{name} repeated checks during {key}')
            if not same(receipt.get('main_counts'), dict(initial_state=0,reducer=3,state=4,transition=3)):
                raise RuntimeError(f'{name} did not execute main application/checkers exactly once')
            if not same(receipt.get('checked'),dict(initial_checks=2,state_check_count=2,full_checking=True,sequence='1')):
                raise RuntimeError(f'{name} checked coverage/context differs')
            if name == 'rust':
                if controls.get('static_borrow') is not True: raise RuntimeError('Rust token ownership must identify static borrowing')
            elif name == 'swift':
                if controls.get('statically_sealed') is not True or controls.get('accessor_ownership') is not True:
                    raise RuntimeError('Swift token sealing/ownership control failed')
            elif controls.get('forged_diagnostic_rejected') is not True or controls.get('accessor_ownership') is not True:
                raise RuntimeError(f'{name} native token authority/ownership control failed')
            ring = receipt.get('ring', {})
            for kind in ('rich','bounded'):
                if not same(ring.get(kind), ring_session): raise RuntimeError(f'{name} ring {kind} lost state-check prefix or replay context')
            ring_config = ring.get('retained_config',{})
            if not isinstance(ring_config.get('parameters'),list) or ring_config['parameters'][-3:] != [
                    ['stateless.monitor.observed_steps','2'],['stateless.monitor.evicted_steps','1'],['stateless.monitor.checkpoint_after_sequence','1']]:
                raise RuntimeError(f'{name} ring provenance counters differ')
            config = receipt.get('retained_config')
            if not isinstance(config, dict) or config.get('strategy') != 'manual' or config.get('seed') is not None:
                raise RuntimeError(f'{name} did not retain full actual audit configuration')
            parameters = config.get('parameters')
            if not isinstance(parameters, list) or [p[0] for p in parameters] != ['stateless.engine.version','stateless.engine.build'] + ['stateless.monitor.'+key for key in ('max_steps','max_retained_bytes','checking','observed_steps','evicted_steps','checkpoint_after_sequence')]:
                raise RuntimeError(f'{name} recorder omitted actual engine provenance')
            if parameters[2:] != [['stateless.monitor.max_steps','3'],['stateless.monitor.max_retained_bytes','8192'],['stateless.monitor.checking','full'],['stateless.monitor.observed_steps','3'],['stateless.monitor.evicted_steps','0'],['stateless.monitor.checkpoint_after_sequence','0']]:
                raise RuntimeError(f'{name} runtime Recorder omitted truthful full-checking/counter parameters')
            if name != 'rust' and parameters[1][1] != f'native-{name}-v1':
                raise RuntimeError(f'{name} recorder engine build stamp is not its actual native implementation')
            if name == 'python' and not Path(receipt['package']).resolve().is_relative_to((work / 'venv').resolve()):
                raise RuntimeError('Python imported outside its installed environment')
            for suffix in ('.bin', '.json'):
                artifact = Path(str(prefix) + suffix)
                if not artifact.is_file(): raise RuntimeError(f'{name} omitted {suffix} witness artifact')
                report['artifacts'][str(artifact.relative_to(out))] = fingerprint(artifact)
            if Path(str(prefix) + '.bin').stat().st_size > contract['limits']['max_total_bytes']:
                raise RuntimeError(f'{name} recorded audit exceeds configured total bytes')
            report['producers'][name] = receipt
            save()
        for producer in runners:
            binary = traces / f'{producer}.bin'
            portable = traces / f'{producer}.json'
            for consumer, runner in runners.items():
                receipt = json.loads(run(f'{producer}-audit-to-{consumer}', runner + ['replay', str(binary)]))
                validate_session(receipt, f'{producer} -> {consumer}')
                if not same(receipt.get('retained_config'), report['producers'][producer]['retained_config']):
                    raise RuntimeError(f'{producer} -> {consumer} lost or rewrote actual producer run configuration')
                report['pairs'].append(dict(producer=producer, consumer=consumer, receipt=receipt))
                receipt = json.loads(run(f'{producer}-json-to-{consumer}', runner + ['replay-json', str(portable)]))
                preferred(receipt, f'{producer} JSON -> {consumer}')
                if not same({key: receipt.get(key) for key in normal_report}, normal_report):
                    raise RuntimeError(f'{producer} JSON -> {consumer} did not exactly reproduce failed witness')
                report['json_pairs'].append(dict(producer=producer, consumer=consumer, receipt=receipt))
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
        report['artifacts'].update({str(p.relative_to(out)): fingerprint(p) for directory in (packages, traces) for p in directory.iterdir() if p.is_file()})
        report['staged_sources'] = {str(p.relative_to(work)): fingerprint(p) for p in work.rglob('*') if p.is_file() and not any(x in p.parts for x in ('.build', 'node_modules', 'venv', 'builder-venv', '__pycache__'))}
        report['status'] = 'passed'; save()
        print(f'Installed checked/Recorder qualification passed: {len(runners)} checked/Recorder applications, {len(report["pairs"])} audit replay pairs, portable baseline regression; {out}', flush=True)
    except BaseException as exc:
        report['status'] = 'failed'; report['error'] = str(exc); save(); raise


if __name__ == '__main__':
    main()

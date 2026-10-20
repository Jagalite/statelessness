#!/usr/bin/env python3
"""Qualify native causal evidence applications from staged/installed packages.

Submit this driver through Kyoto. It runs no cargo commands. Every producer and
consumer invocation is a new process outside the checkout. Candidate evidence
only: this does not promote causal-evidence-v2 or claim performance/complete parity.
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
    parser.add_argument('--rust', type=Path, required=True, help='Already Kyoto-built native-evidence Rust reference executable')
    parser.add_argument('--rust-portable', type=Path, help='Optional already-built Rust four-profile corpus runner')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        raise RuntimeError('output must be new or empty; preserve previous receipts')
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='stateless-native-evidence-')).resolve()
    logs = out / 'logs'; logs.mkdir()
    packages = out / 'packages'; packages.mkdir()
    env = dict(os.environ, PYTHONPATH='', PYTHONNOUSERSITE='1', GOPROXY='off', GOTOOLCHAIN='local', GOWORK='off', CGO_ENABLED='0')
    commands = []
    report = {'status': 'running', 'profile': 'causal-evidence-v2 candidate', 'workspace': str(work),
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
            raise RuntimeError(f'{name} unexpectedly succeeded')
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
        shutil.copy2(ROOT / 'examples/native-evidence/python/consumer.py', work / 'python-consumer.py')
        runners = {'python': [str(py), '-I', str(work / 'python-consumer.py')]}

        go = tool('go'); go_source = work / 'go-package'; stage(ROOT / 'go', go_source)
        report['go_version'] = run('go-version', [go, 'version'])
        go_consumer = work / 'go-consumer'; stage(ROOT / 'examples/native-evidence/go', go_consumer)
        (go_consumer / 'go.mod').write_text('module evidence-consumer\n\ngo 1.23.0\n\nrequire github.com/Jagalite/statelessness/go v0.0.0\nreplace github.com/Jagalite/statelessness/go => ../go-package\n')
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
        ts_consumer = work / 'typescript-consumer'; stage(ROOT / 'examples/native-evidence/typescript', ts_consumer)
        (ts_consumer / 'package.json').write_text('{"name":"evidence-consumer","private":true,"type":"module"}\n')
        run('typescript-install', [npm, 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund', '--package-lock=false', '--omit=dev', tarball], ts_consumer)
        (ts_consumer / 'tsconfig.json').write_text(json.dumps({'compilerOptions': {'strict': True, 'target': 'ES2022', 'module': 'NodeNext', 'moduleResolution': 'NodeNext', 'outDir': 'build', 'lib': ['ES2022', 'DOM']}, 'files': ['consumer.ts']}))
        run('typescript-declarations', compiler + ['-p', 'tsconfig.json'], ts_consumer)
        runners['typescript'] = [node, str(ts_consumer / 'build/consumer.js')]

        if not args.without_swift:
            swift = tool('swift'); swift_source = work / 'swift-package'; swift_source.mkdir()
            report['swift_version'] = run('swift-version', [swift, '--version'])
            shutil.copy2(ROOT / 'Package.swift', swift_source / 'Package.swift')
            stage(ROOT / 'swift', swift_source / 'swift')
            swift_consumer = work / 'swift-consumer'; stage(ROOT / 'examples/native-evidence/swift', swift_consumer)
            run('swift-build', [swift, 'build', '--package-path', swift_consumer, '-j', '2'])
            binary_dir = run('swift-bin-path', [swift, 'build', '--package-path', swift_consumer, '--show-bin-path'])
            runners['swift'] = [str(Path(binary_dir) / 'EvidenceConsumer')]


        rust = args.rust.resolve()
        if not rust.is_file(): raise RuntimeError(f'Rust reference executable missing: {rust}')
        rust_copy = work / ('rust-evidence-reference.exe' if os.name == 'nt' else 'rust-evidence-reference')
        shutil.copy2(rust, rust_copy)
        runners['rust'] = [str(rust_copy)]
        report['rust_executable_sha256'] = fingerprint(rust)
        report['runners'] = runners
        report['executable_sha256'] = {name: fingerprint(Path(runner[0])) for name, runner in runners.items()}
        report['consumer_files_sha256'] = {str(p.relative_to(work)): fingerprint(p) for dirname in ('go-consumer', 'typescript-consumer', 'swift-consumer')
            for p in (work / dirname).rglob('*') if p.is_file() and not any(x in p.parts for x in ('.build', 'node_modules'))}
        report['consumer_files_sha256']['python-consumer.py'] = fingerprint(work / 'python-consumer.py')
        traces = out / 'traces'; traces.mkdir()
        contract = json.loads((ROOT / 'examples/native-evidence/contract.json').read_text())
        normal_report = contract['normal_report']
        report['finite_evidence_limits'] = contract['evidence_limits']
        report['upgrade_pairs'] = []; report['diagnostic_pairs'] = []; report['v1_pairs'] = []
        def same(actual, expected):
            return json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)
        def checks(state, edge=False):
            values = [dict(id='threshold.bound',status='failed' if state>=3 else 'passed',reason='at least three' if state>=3 else None),
                      dict(id='state.appended',status='passed',reason=None)]
            return values + ([dict(id='edge.checked',status='passed',reason=None)] if edge else [])
        def expected_evidence(config, upgrade=False):
            trace = dict(metadata=contract['model'],config=config,initial_state='30',initial_checks=checks(0),
                steps=[dict(input='31',disposition=dict(kind='accepted',reason=None),outputs=['6368616e676564'],post_state=str(seq).encode().hex(),checks=checks(seq,True)) for seq in (1,2,3)],
                termination=dict(kind='property_failed',reason=None))
            empty = dict(machine=None,generation=None,correlation=None,logical_time=None,causes=[])
            rows = [dict(sequence=str(seq),state_check_count=2,causal=empty if upgrade else dict(machine='worker-a',generation='9',correlation='request-1',logical_time=str(10+seq),
                        causes=[] if seq==1 else [dict(sequence=str(seq-1),output=0)])) for seq in (1,2,3)]
            return dict(trace=trace,checkpoint_after='0',provenance=contract['provenance'],steps=rows)
        def expected_diagnostic():
            observations=[]
            for seq in range(3):
                rows=[]
                for index,c in enumerate(checks(seq,seq>0)):
                    rows.append(dict(status=c['status'],phase='initial_state' if seq==0 else 'state' if index<2 else 'transition',id=c['id'] if c['id']=='threshold.bound' else None))
                observations.append(dict(sequence=seq,checks=rows))
            return json.dumps(dict(schema='statelessness.diagnostic-export/1',replayable=False,redaction_policy='omit-payloads-v1',raw_payloads_included=False,
                checkpoint_after=0,omitted_prefix=False,termination='property_failure',truncated=True,observations=observations),separators=(',',':'),ensure_ascii=False)
        diagnostic_text = expected_diagnostic(); report['expected_diagnostic'] = diagnostic_text
        def private(path):
            if os.name == 'posix' and path.stat().st_mode & 0o777 != 0o600: raise RuntimeError(f'{path} was not created with private Unix permissions')
        def preferred(receipt, context):
            if type(receipt.get('legacy_encoder_calls')) is not int or receipt['legacy_encoder_calls'] != 0:
                raise RuntimeError(f'{context} invoked legacy allocating encoder')
            if type(receipt.get('codec_into_calls')) is not int or receipt['codec_into_calls'] <= 0:
                raise RuntimeError(f'{context} did not execute preferred incremental codecs')
        def validate(receipt, config, context, upgrade=False):
            if 'codec_into_calls' in receipt or receipt.get('mode')=='record': preferred(receipt,context)
            if not same(receipt.get('report'),normal_report): raise RuntimeError(f'{context} did not exactly phase-replay the failed witness')
            if not same(receipt.get('evidence'),expected_evidence(config,upgrade)): raise RuntimeError(f'{context} lost trace,phase,causal,provenance,or config fields')
            if not same(receipt.get('retained_config'),config): raise RuntimeError(f'{context} rewrote producer run configuration')
        for name, runner in runners.items():
            prefix = traces / name
            receipt = json.loads(run(f'{name}-record', runner + ['record', str(prefix)]))
            config = receipt.get('retained_config')
            if not isinstance(config,dict) or config.get('strategy')!='manual' or config.get('seed') is not None:
                raise RuntimeError(f'{name} omitted actual audit configuration')
            parameters=config.get('parameters')
            keys=['stateless.engine.version','stateless.engine.build']+['stateless.monitor.'+key for key in ('max_steps','max_retained_bytes','checking','observed_steps','evicted_steps','checkpoint_after_sequence')]
            if not isinstance(parameters,list) or [p[0] for p in parameters]!=keys or parameters[2:]!=[
                ['stateless.monitor.max_steps','3'],['stateless.monitor.max_retained_bytes','15328'],['stateless.monitor.checking','full'],
                ['stateless.monitor.observed_steps','3'],['stateless.monitor.evicted_steps','0'],['stateless.monitor.checkpoint_after_sequence','0']]:
                raise RuntimeError(f'{name} did not retain truthful reservation/checking/counter provenance')
            if name!='rust' and parameters[:2]!=[['stateless.engine.version','0.1.0'],['stateless.engine.build',f'native-{name}-v1']]:
                raise RuntimeError(f'{name} recorder engine identity differs')
            validate(receipt,config,name)
            if not same(receipt.get('main_counts'),dict(initial_state=0,reducer=3,state=4,transition=3)): raise RuntimeError(f'{name} reexecuted check-once application')
            controls=receipt.get('controls',{})
            for key in contract['controls']:
                if controls.get(key) is not True: raise RuntimeError(f'{name} failed {key}')
            if receipt.get('diagnostic_text')!=diagnostic_text: raise RuntimeError(f'{name} diagnostic redaction/allowlist/schema differs')
            if name=='python' and not Path(receipt['package']).resolve().is_relative_to((work/'venv').resolve()): raise RuntimeError('Python imported outside installed environment')
            for suffix in ('.v2','.v1','.json','.diagnostic'):
                artifact=Path(str(prefix)+suffix)
                if not artifact.is_file(): raise RuntimeError(f'{name} omitted {suffix}')
                report['artifacts'][str(artifact.relative_to(out))]=fingerprint(artifact)
            envelope=Path(str(prefix)+'.v2').read_bytes();embedded_len=int.from_bytes(envelope[12:20],'little')
            if envelope[28:28+embedded_len]!=Path(str(prefix)+'.v1').read_bytes():raise RuntimeError(f'{name} v1 sidecar differs from exact embedded original')
            private(Path(str(prefix)+'.v2'));private(Path(str(prefix)+'.diagnostic'))
            if Path(str(prefix)+'.v2').stat().st_size>contract['limits']['max_total_bytes']: raise RuntimeError(f'{name} recorded envelope exceeds total quota')
            report['producers'][name]=receipt;save()
        protected=out/'existing-files';protected.mkdir()
        for name,runner in runners.items():
            sentinel=protected/f'{name}.sentinel';sentinel.write_bytes(b'preexisting host content must survive')
            for operation in ('replay','diagnostic'):
                diagnostic=run(f'{name}-{operation}-existing-file',runner+[operation,str(traces/f'{name}.v2'),str(sentinel)],expect_failure=True)
                if 'exist' not in diagnostic.lower() or sentinel.read_bytes()!=b'preexisting host content must survive':
                    raise RuntimeError(f'{name} {operation} failed to preserve a distinct preexisting file')
        outputs=out/'outputs';outputs.mkdir()
        for producer in runners:
            source=traces/f'{producer}.v2';v1=traces/f'{producer}.v1';config=report['producers'][producer]['retained_config']
            for consumer,runner in runners.items():
                target=outputs/f'{producer}-to-{consumer}.v2'
                receipt=json.loads(run(f'{producer}-v2-to-{consumer}',runner+['replay',str(source),str(target)]))
                validate(receipt,config,f'{producer} -> {consumer}')
                if receipt.get('version')!=2 or fingerprint(target)!=fingerprint(source):raise RuntimeError(f'{producer} -> {consumer} did not preserve exact envelope bytes')
                private(target);report['pairs'].append(dict(producer=producer,consumer=consumer,receipt=receipt))
                upgraded=outputs/f'{producer}-upgrade-{consumer}.v2'
                receipt=json.loads(run(f'{producer}-upgrade-to-{consumer}',runner+['upgrade',str(v1),str(upgraded)]))
                validate(receipt,config,f'{producer} upgrade -> {consumer}',True)
                if receipt.get('version')!=2:raise RuntimeError('upgrade did not produce v2')
                private(upgraded);report['upgrade_pairs'].append(dict(producer=producer,consumer=consumer,receipt=receipt))
                diagnostic=outputs/f'{producer}-diagnostic-{consumer}.json'
                receipt=json.loads(run(f'{producer}-diagnostic-to-{consumer}',runner+['diagnostic',str(source),str(diagnostic)]))
                if receipt.get('diagnostic_text')!=diagnostic_text or diagnostic.read_text()!=diagnostic_text or receipt.get('execution_calls')!=0 or receipt.get('no_overwrite') is not True:
                    raise RuntimeError(f'{producer} diagnostic -> {consumer} disclosure/export policy differs')
                private(diagnostic);report['diagnostic_pairs'].append(dict(producer=producer,consumer=consumer,receipt=receipt))
                receipt=json.loads(run(f'{producer}-v1-to-{consumer}',runner+['read-v1',str(v1)]))
                if not same({key:receipt.get(key) for key in ('version','initial_phase','turn_phase','execution_calls','retained_config')},dict(version=1,initial_phase='initial_state',turn_phase=None,execution_calls=0,retained_config=config)):
                    raise RuntimeError(f'{producer} v1 -> {consumer} passively invented phases or executed model')
                if receipt.get('codec_into_calls',0)!=0 or receipt.get('legacy_encoder_calls',0)!=0:raise RuntimeError('passive read invoked model codec')
                report['v1_pairs'].append(dict(producer=producer,consumer=consumer,receipt=receipt));save()

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
        report['artifacts'].update({str(p.relative_to(out)): fingerprint(p) for directory in (packages, traces, outputs, protected) for p in directory.iterdir() if p.is_file()})
        report['staged_sources'] = {str(p.relative_to(work)): fingerprint(p) for p in work.rglob('*') if p.is_file() and not any(x in p.parts for x in ('.build', 'node_modules', 'venv', 'builder-venv', '__pycache__'))}
        report['status'] = 'passed'; save()
        print(f'Installed causal evidence qualification passed: {len(runners)} causal evidence applications, {len(report["pairs"])} audit replay pairs, portable baseline regression; {out}', flush=True)
    except BaseException as exc:
        report['status'] = 'failed'; report['error'] = str(exc); save(); raise


if __name__ == '__main__':
    main()

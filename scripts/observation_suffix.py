"""Fresh-process retained-suffix gate for the installed observation driver.

Call qualify(q, attempt, compile_binaries, compiler) after normal packaging and
before sealing the final installed-runtime-tree. q is check-native-observation-
consumers.py, compiler is its already resolved TypeScript compiler argv. The
caller runs inside Kyoto; this helper never submits jobs or invokes Cargo.
Expected observations are literal source-derived data, never producer output.
"""
from pathlib import Path
import json
import shutil
import zlib


CONTRACTS = {
    'interrupted': ('examples/native-observation/suffix-contract.json', 'record-suffix'),
    'failure': ('examples/native-observation/suffix-failure-contract.json', 'record-failing-suffix'),
}
LANGUAGES = ('python', 'go', 'typescript', 'swift', 'rust')


def _same(q, actual, expected, context):
    # Canonical JSON preserves the distinction between bool and int.
    if q.canonical(actual) != q.canonical(expected):
        raise ValueError(context + ': suffix contract differs')


def _preferred(receipt):
    if type(receipt.get('legacy_encoder_calls')) is not int or receipt['legacy_encoder_calls'] != 0:
        raise ValueError('suffix invoked a legacy encoder')
    if type(receipt.get('codec_into_calls')) is not int or receipt['codec_into_calls'] <= 0:
        raise ValueError('suffix omitted preferred incremental codecs')


def _session(q, receipt, contract):
    _preferred(receipt)
    for kind in ('rich', 'bounded'):
        _same(q, receipt.get(kind), contract['session'], kind)
        _same(q, receipt.get(kind + '_counts'), contract['replay_counts'], kind + ' callback counts')


def _watched(q, attempt, report, phase):
    """Compare all registered runtime inputs around every consumer invocation.

    Attempt owns source membership and compile-input checks. Re-reading these
    runtime identities here also covers failed commands before its final seal.
    """
    rows = {}
    for label, (path, before) in attempt.inputs.items():
        try:
            after = (q.executable_identity(path) if 'file' in before
                     else dict(path=str(path), tree=q.tree_identity(path)))
            rows[label] = dict(before=before, actual=after, unchanged=before == after)
        except (OSError, ValueError) as error:
            rows[label] = dict(before=before, error=repr(error), unchanged=False)
    report[phase] = q.manifest(attempt.root, rows)
    if any(not row['unchanged'] for row in rows.values()):
        raise ValueError('suffix runtime input changed: ' + phase)


def _config(q, config, contract, producer):
    if not isinstance(config, dict) or set(config) != {'strategy', 'seed', 'parameters'}:
        raise ValueError('suffix omitted full audit configuration')
    expected = contract['retained_config']
    _same(q, [config['strategy'], config['seed']], [expected['strategy'], expected['seed']], 'run config')
    parameters = config['parameters']
    if (not isinstance(parameters, list) or len(parameters) != 9
            or any(not isinstance(p, list) or len(p) != 2
                   or any(type(v) is not str for v in p) for p in parameters)):
        raise ValueError('suffix parameter shape differs')
    if [p[0] for p in parameters[:2]] != ['stateless.engine.version', 'stateless.engine.build']:
        raise ValueError('suffix omitted engine stamp')
    if not all(p[1] for p in parameters[:2]):
        raise ValueError('suffix engine stamp is empty')
    if producer != 'rust' and parameters[1][1] != 'native-' + producer + '-v1':
        raise ValueError('suffix native engine identity differs')
    _same(q, parameters[2:], expected['parameters_after_engine_stamp'], 'retained counters/origin')


def _binary_framing(q, raw, contract):
    """Check v1 framing and the persisted footer, independently of replay.

    Replay's exact report intentionally does not distinguish Interrupted from
    Completed. This small stdlib framing reader closes that gap without changing
    the old replay reply or reimplementing the model/config/checks decoder.
    """
    expected = contract['binary']
    header = bytes.fromhex(expected['header_hex'])
    if not raw.startswith(header) or len(raw) > contract['limits']['max_total_bytes']:
        raise ValueError('suffix binary header/total byte bound differs')
    offset = len(header); tags = []; indices = []; footer = None
    while offset < len(raw):
        head = raw[offset:offset + 5]
        if len(head) != 5:
            raise ValueError('truncated suffix frame header')
        size = int.from_bytes(head[1:], 'little')
        end = offset + 5 + size
        if size > contract['limits']['max_frame_bytes'] or end + 4 > len(raw):
            raise ValueError('truncated or oversized suffix frame')
        if int.from_bytes(raw[end:end + 4], 'little') != zlib.crc32(raw[offset:end]):
            raise ValueError('suffix binary frame checksum differs')
        payload = raw[offset + 5:end]
        tags.append(head[0])
        if head[0] == 2:
            if len(payload) < 8:
                raise ValueError('truncated suffix step index')
            indices.append(int.from_bytes(payload[:8], 'little'))
        if head[0] == 3:
            footer = payload.hex()
        offset = end + 4
    _same(q, tags, expected['frame_tags'], 'binary record order/count')
    _same(q, indices, expected['step_indices'], 'binary local step indices')
    _same(q, footer, expected['footer_payload_hex'], 'binary count and termination footer')


def qualify(q, attempt, compile_binaries, compiler):
    """Stage once, then run 5+25+25 fresh processes for each of two contracts.

    Returns and publishes attempt.report['suffix']; failures keep partial rows
    and the Attempt's immutable stdout/stderr/command records. No legacy driver
    or checked-control suite is duplicated here.
    """
    report = dict(schema='statelessness.observation-suffix-qualification.v2',
                  status='running', planned_producers=10, planned_binary_pairs=50,
                  planned_json_pairs=50, cases={})
    attempt.report['suffix'] = report
    try:
        work = attempt.work
        contracts = {}
        for case, (source, mode) in CONTRACTS.items():
            contract_path = work / Path(source).name
            q.stage(attempt, source, contract_path)
            info = attempt.watch('suffix-contract:' + case, contract_path)
            contract = q.strict_json(contract_path.read_bytes())
            if contract.get('schema') != 'statelessness.observation-suffix.v1':
                raise ValueError('unexpected suffix contract schema: ' + case)
            contracts[case] = contract
            report['cases'][case] = dict(status='pending', mode=mode, contract=info,
                scope=contract['scope'], limits=contract['limits'],
                observer_max_record_bytes=contract['observer_max_record_bytes'],
                planned_producers=5, planned_binary_pairs=25, planned_json_pairs=25,
                producers={}, binary_pairs=[], json_pairs=[], artifacts={})
        py = work / 'venv/bin/python'
        ts = work / 'typescript-consumer'
        for path in (py, work / 'go-package/go.mod',
                     ts / 'node_modules/@jagalite/statelessness/package.json',
                     work / 'swift-package/Package.swift'):
            if not path.is_file():
                raise ValueError('normal installed packaging must precede suffix gate: ' + str(path))
        python_consumer = work / 'suffix-python-consumer.py'
        q.stage(attempt, 'examples/native-checked/python/consumer.py', python_consumer)
        # -I ignores PYTHON* environment flags; -B prevents cache writes after
        # the installed package tree has been watched.
        runners = {'python': [str(py), '-I', '-B', str(python_consumer)]}

        go = q.require_tool(attempt, 'go')
        go_consumer = work / 'suffix-go-consumer'
        q.stage(attempt, 'examples/native-checked/go', go_consumer)
        (go_consumer / 'go.mod').write_text('module checked-suffix-consumer\n\ngo 1.23.0\n\n'
            'require github.com/Jagalite/statelessness/go v0.0.0\n'
            'replace github.com/Jagalite/statelessness/go => ../go-package\n', encoding='utf-8')
        attempt.register_generated(go_consumer / 'go.mod', 'suffix-go-config')
        raw, _, _ = attempt.run('suffix-go-module-graph', [go, 'list', '-m', '-json', 'all'], go_consumer)
        decoder = json.JSONDecoder(); remaining = raw.decode('utf-8'); modules = []
        while remaining.strip():
            remaining = remaining.lstrip(); module, end = decoder.raw_decode(remaining)
            modules.append(module); remaining = remaining[end:]
        if len(modules) != 2 or {m['Path'] for m in modules} != {
                'checked-suffix-consumer', 'github.com/Jagalite/statelessness/go'}:
            raise ValueError('unexpected suffix Go module graph')
        dependency, = (m for m in modules if not m.get('Main'))
        if (dependency.get('Version') != 'v0.0.0'
                or Path(dependency.get('Replace', {}).get('Dir', '')).resolve() != work / 'go-package'):
            raise ValueError('suffix Go dependency is not the sealed staged package')
        go_binary = work / 'suffix-go-bin'
        attempt.run('suffix-go-build', [go, 'build', '-trimpath', '-o', go_binary, '.'], go_consumer)
        attempt.watch('suffix-runner:go', go_binary)
        runners['go'] = [str(go_binary)]

        # Reuse the already installed package and compiler; no second npm install.
        q.stage(attempt, 'examples/native-checked/typescript/consumer.ts', ts / 'suffix-consumer.ts')
        (ts / 'suffix-tsconfig.json').write_bytes(q.canonical(dict(compilerOptions=dict(
            strict=True, target='ES2022', module='NodeNext', moduleResolution='NodeNext',
            outDir='suffix-build', lib=['ES2022', 'DOM'], types=[],
            noEmitOnError=True, skipLibCheck=False), files=['suffix-consumer.ts'])))
        attempt.register_generated(ts / 'suffix-tsconfig.json', 'suffix-typescript-config')
        attempt.run('suffix-typescript-build', list(compiler) + ['-p', 'suffix-tsconfig.json'], ts)
        attempt.watch('suffix-application:typescript', ts / 'suffix-build')
        runners['typescript'] = [str(compiler[0]), str(ts / 'suffix-build/suffix-consumer.js')]

        swift = q.require_tool(attempt, 'swift')
        swift_consumer = work / 'suffix-swift-consumer'
        q.stage(attempt, 'examples/native-checked/swift', swift_consumer)
        attempt.run('suffix-swift-build', [swift, 'build', '--package-path', swift_consumer, '-j', '2'])
        raw, _, _ = attempt.run('suffix-swift-bin-path', [swift, 'build', '--package-path',
                                                       swift_consumer, '--show-bin-path'])
        swift_binary = Path(raw.decode('utf-8').strip()) / 'CheckedConsumer'
        if not swift_binary.resolve().is_relative_to(swift_consumer):
            raise ValueError('suffix Swift executable is outside the staged application')
        attempt.watch('suffix-runner:swift', swift_binary)
        runners['swift'] = [str(swift_binary)]

        rust_identity = compile_binaries['native-checked']
        rust = Path(rust_identity['path'])
        if q.executable_identity(rust) != rust_identity:
            raise ValueError('suffix Rust executable changed after frozen compilation')
        attempt.watch('suffix-rust-compiled', rust)
        rust_copy = work / 'suffix-rust-bin'
        shutil.copy2(rust, rust_copy)
        if q.identity(rust_copy) != q.identity(rust):
            raise ValueError('suffix Rust executable copy differs')
        runners['rust'] = [str(rust_copy)]
        report['runners'] = runners
        report['executables'] = {name: attempt.watch('suffix-runner:' + name, runner[0])
                                 for name, runner in runners.items()}
        report['applications'] = {name: attempt.watch('suffix-application:' + name, path)
            for name, path in {'python': python_consumer, 'go': go_consumer,
                               'typescript-source': ts / 'suffix-consumer.ts',
                               'typescript-config': ts / 'suffix-tsconfig.json',
                               'typescript': ts / 'suffix-build', 'swift': swift_consumer}.items()}
        report['packages'] = {name: attempt.watch('suffix-package:' + name, path)
            for name, path in {'artifacts': attempt.output / 'packages',
                               'python': work / 'venv/lib', 'go': work / 'go-package',
                               'typescript': ts / 'node_modules/@jagalite/statelessness',
                               'swift': work / 'swift-package'}.items()}
        traces = attempt.output / 'suffix-traces'
        traces.mkdir(exist_ok=False)

        def invoke(name, language, mode, path, row):
            row.update(completed=False, matched_contract=False, command=name)
            _watched(q, attempt, row, 'runtime_inputs_before')
            try:
                raw, _, entry = attempt.run(name, runners[language] + [mode, str(path)])
            finally:
                _watched(q, attempt, row, 'runtime_inputs_after')
            row['completed'] = True
            row['stdout'] = entry['stdout']
            receipt = q.strict_json(raw)
            row['receipt'] = receipt
            if not isinstance(receipt, dict) or receipt.get('producer') != language or receipt.get('mode') != mode:
                raise ValueError('suffix invocation identity differs: ' + name)
            if language == 'python':
                package = Path(receipt.get('package', '')).resolve(strict=True)
                if not package.is_relative_to((work / 'venv/lib').resolve()):
                    raise ValueError('suffix Python import escaped its installed package')
            return receipt

        for case, contract in contracts.items():
            result = report['cases'][case]
            result['status'] = 'running'
            case_traces = traces / case
            case_traces.mkdir(exist_ok=False)
            for language in LANGUAGES:
                row = {}; result['producers'][language] = row
                prefix = case_traces / language
                receipt = invoke('suffix-' + case + '-' + language + '-record', language,
                                 result['mode'], prefix, row)
                _session(q, receipt, contract)
                _config(q, receipt.get('retained_config'), contract, language)
                for key in ('main_counts', 'checked', 'steps', 'termination', 'observer_prefix'):
                    _same(q, receipt.get(key), contract[key], case + ' ' + language + ' ' + key)
                expected_text = contract['observer_prefix'] + ''.join(contract['observer_records'])
                _same(q, receipt.get('observer_text'), expected_text, language + ' actual observer records')
                records = list(contract['observer_records'])
                if case == 'failure':
                    _same(q, receipt.get('freeze_observer_text'), contract['freeze_observer_text'],
                          language + ' post-freeze diagnostic')
                    _same(q, receipt.get('freeze_verified'), True, language + ' frozen artifact invariant')
                    records.append(contract['freeze_observer_text'])
                elif 'freeze_observer_text' in receipt or 'freeze_verified' in receipt:
                    raise ValueError('interrupted suffix falsely claims failure freeze')
                if any(len(record.encode('utf-8')) > contract['observer_max_record_bytes'] for record in records):
                    raise ValueError('authored observer record exceeds its byte bound')
                for suffix in ('.bin', '.json'):
                    artifact = Path(str(prefix) + suffix)
                    info = q.identity(artifact)
                    result['artifacts'][str(artifact.relative_to(attempt.output))] = info
                    if info['type'] != 'regular' or not 0 < info['size_bytes'] <= contract['limits']['max_total_bytes']:
                        raise ValueError('suffix artifact absent or exceeds finite byte limit')
                    attempt.watch('suffix-artifact:' + case + ':' + language + suffix, artifact)
                    if suffix == '.json':
                        _same(q, q.strict_json(artifact.read_bytes()), contract['portable'], language + ' portable artifact')
                    else:
                        _binary_framing(q, artifact.read_bytes(), contract)
                row['matched_contract'] = True

            # Every invocation starts a new process, including same-language
            # pairs. Independent literals, not a producer replay, are expected.
            for producer in LANGUAGES:
                for consumer in LANGUAGES:
                    row = dict(producer=producer, consumer=consumer)
                    result['binary_pairs'].append(row)
                    receipt = invoke('suffix-' + case + '-' + producer + '-binary-to-' + consumer,
                                     consumer, 'replay-suffix', case_traces / (producer + '.bin'), row)
                    _session(q, receipt, contract)
                    _config(q, receipt.get('retained_config'), contract, producer)
                    _same(q, receipt['retained_config'], result['producers'][producer]['receipt']['retained_config'],
                          producer + ' -> ' + consumer + ' actual producer config')
                    row['matched_contract'] = True
                    row = dict(producer=producer, consumer=consumer)
                    result['json_pairs'].append(row)
                    receipt = invoke('suffix-' + case + '-' + producer + '-json-to-' + consumer,
                                     consumer, 'replay-suffix-json', case_traces / (producer + '.json'), row)
                    _session(q, receipt, contract)
                    if 'retained_config' in receipt:
                        raise ValueError('portable JSON must not claim retained audit configuration')
                    row['matched_contract'] = True
            result['executed_producers'] = len(result['producers'])
            result['executed_binary_pairs'] = len(result['binary_pairs'])
            result['executed_json_pairs'] = len(result['json_pairs'])
            result['status'] = 'passed'
        for kind in ('producers', 'binary_pairs', 'json_pairs'):
            report['executed_' + kind] = sum(case['executed_' + kind] for case in report['cases'].values())
        _watched(q, attempt, report, 'runtime_inputs_final')
        report['status'] = 'passed'
        return report
    except BaseException as error:
        report['status'] = 'failed'
        report['error'] = repr(error)
        for case in report['cases'].values():
            if case['status'] == 'running':
                case['status'] = 'failed'
                case['error'] = repr(error)
        raise

#!/usr/bin/env python3
"""Source-sealed observation qualification. Run ONLY through Kyoto after freeze.

native executes full native suites and receipt regression tests in external staging.
rust-compile compiles, but does not execute, the selected Rust tests/public consumer.
rust-runtime executes those exact binaries from a prior successful compile receipt.
installed builds native packages and runs public consumers, never invoking Cargo.
Invoke as python3 -I -B scripts/qualify-native-observation.py ... .
Every invocation requires a new output directory; failed evidence is retained.
"""
from __future__ import annotations

import sys
if __name__ == '__main__' and (not sys.flags.isolated or not sys.dont_write_bytecode):
    raise SystemExit('invoke with python3 -I -B scripts/qualify-native-observation.py ...')

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]
_driver_path = ROOT / 'scripts/check-native-observation-consumers.py'
_spec = importlib.util.spec_from_file_location('observation_consumers', _driver_path)
q = importlib.util.module_from_spec(_spec)
# This bootstrap precedes sealing: never let an ignored .pyc replace its source.
exec(compile(_driver_path.read_bytes(), str(_driver_path), 'exec'), q.__dict__)
NATIVE_STAGE_PATHS = ('python', 'go', 'typescript', 'swift', 'conformance', 'spec',
                      'docs/native-parity/derive-model-draft', 'examples/native-observation',
                      'scripts', 'Package.swift')


def compile_rust(attempt, target):
    if target is None:
        target = attempt.work / 'cargo-target'
    target = target.resolve()
    if target.is_relative_to(attempt.root):
        raise ValueError('--cargo-target must be outside the checkout')
    target.mkdir(parents=True, exist_ok=False)
    attempt.report['cargo_target'] = str(target)
    attempt.env['CARGO_TARGET_DIR'] = str(target)
    attempt.env['CARGO_INCREMENTAL'] = '0'
    cargo = q.require_tool(attempt, 'cargo')
    q.require_tool(attempt, 'rustc')
    # rustup shims alone do not identify the compiler that actually runs.
    rustup = q.require_tool(attempt, 'rustup')
    for name in ('cargo', 'rustc', 'rustdoc'):
        raw, _, _ = attempt.run('resolve-' + name, [rustup, 'which', name], attempt.root)
        attempt.watch('resolved-tool:' + name, Path(raw.decode('utf-8').strip()))
    attempt.run('cargo-version', [cargo, '-Vv'], attempt.root)
    attempt.run('rustc-version', ['rustc', '-vV'], attempt.root)
    configurations = set()
    for directory in (attempt.root, *attempt.root.parents,
                      Path(attempt.env.get('CARGO_HOME', str(Path.home() / '.cargo')))):
        for relative in ('.cargo/config', '.cargo/config.toml', 'config', 'config.toml'):
            path = directory / relative
            if path.is_file():
                configurations.add(path)
    for path in configurations:
        attempt.watch('cargo-configuration:' + str(path), path)
    arguments = q.rust_compile_arguments()
    raw_tests, _, _ = attempt.run('rust-tests-compile', [cargo, *arguments['rust-tests-compile']], attempt.root)
    raw_consumer, _, _ = attempt.run('rust-public-consumer-compile', [cargo, *arguments['rust-public-consumer-compile']], attempt.root)
    binaries = {}
    artifact_rows = {}
    for raw, require_test in ((raw_tests, True), (raw_consumer, False)):
        for line in raw.splitlines():
            if not line:
                continue
            item = q.strict_json(line)
            if item.get('reason') != 'compiler-artifact' or not item.get('executable'):
                continue
            name = item['target']['name']
            allowed = q.RUST_TESTS if require_test else q.RUST_CONSUMERS
            if name not in allowed or bool(item['profile']['test']) != require_test:
                continue
            if name in binaries:
                raise ValueError('duplicate compiler executable identity: ' + name)
            path = Path(item['executable']).resolve(strict=True)
            if not path.is_relative_to(target):
                raise ValueError('compiler executable outside fresh target directory')
            binaries[name] = q.executable_identity(path)
            artifact_rows[name] = item
            attempt.watch('compiled:' + name, path)
    if set(binaries) != set(q.RUST_TESTS) | set(q.RUST_CONSUMERS):
        raise ValueError('Cargo omitted required executable records')
    attempt.report['rust_binaries'] = binaries
    attempt.report['rust_compiler_artifacts'] = artifact_rows
    attempt.report['compile_artifacts'] = q.manifest(attempt.root, q.tree_identity(target))
    attempt.report['runtime_executed'] = False
    attempt.report['scope_gaps'] = ['Compilation only; no Rust test or consumer was executed.']


def rust_runtime(attempt, fixture, compile_receipt):
    binaries = q.load_compile(attempt, compile_receipt)
    results = {}
    attempt.report['rust_test_execution'] = results
    for name in q.RUST_TESTS:
        argv = [binaries[name]['path']]
        raw, _, _ = attempt.run(name + '-list', argv + ['--list', '--format', 'terse'])
        names = [line.removesuffix(': test') for line in raw.decode('utf-8').splitlines()
                 if line.endswith(': test')]
        if len(names) != q.RUST_TEST_COUNTS[name] or len(names) != len(set(names)):
            raise ValueError('Rust test discovery differs from independently audited source count: ' + name)
        results[name] = dict(discovered_names=names, execution_summary=None)
        raw, _, entry = attempt.run(name + '-runtime', argv + ['--nocapture', '--test-threads=2'], accept_failure=True)
        summaries = re.findall(rb'^test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; '
                               rb'(\d+) measured; (\d+) filtered out;', raw, re.MULTILINE)
        if len(summaries) != 1:
            raise ValueError('missing unique Rust runtime summary: ' + name)
        counts = tuple(map(int, summaries[0]))
        results[name]['execution_summary'] = dict(passed=counts[0], failed=counts[1],
            ignored=counts[2], measured=counts[3], filtered_out=counts[4], exit_code=entry['exit_code'])
        if counts != (len(names), 0, 0, 0, 0) or entry['exit_code']:
            raise ValueError('Rust tests failed/skipped/filtered: ' + name)
    staged_fixture = attempt.work / 'observation-v1.json'
    q.stage(attempt, q.FIXTURE, staged_fixture); attempt.watch('runtime-fixture', staged_fixture)
    raw, _, _ = attempt.run('rust-observe', [binaries['native-observation']['path'], 'observe', staged_fixture])
    actual = q.strict_json(raw)
    attempt.report['language_invocations'] = {'rust': dict(completed=True,
        reported_cases=len(actual['cases']) if isinstance(actual, dict) and isinstance(actual.get('cases'), list) else None,
        matched_contract=False)}
    attempt.report['language_executed_cases']['rust'] = q.validate_result(fixture, 'rust', actual)
    attempt.report['language_invocations']['rust']['matched_contract'] = True
    attempt.report['validator_controls']['rust'] = q.negative_controls(fixture, actual, 'rust')
    attempt.report['scope_gaps'] = [
        'Executes the six selected root integration test binaries, not the full Rust workspace or doctests.',
        'macro-qualification/monitor_release is outside these six root suites; no coverage claim for it.',
    ]


def record_go_exec(argv):
    """Go -exec child; invoked only within the parent Kyoto-managed native run."""
    output = Path(os.environ['OBSERVATION_GO_EXEC_DIR']) / (str(time.time_ns()) + '-' + str(os.getpid()))
    output.mkdir(parents=True, exist_ok=False)
    before = q.executable_identity(Path(argv[0]))
    # Reuse raw process capture without claiming a standalone sealed qualification.
    recorder = q.Attempt.__new__(q.Attempt)
    recorder.root = ROOT; recorder.output = output; recorder.work = Path.cwd()
    # Preserve the inherited Kyoto PGID through Go, its wrapper and its test.
    # The enclosing command owns the timeout and observes this ancestry.
    recorder.env = dict(os.environ); recorder.timeout = None; recorder.initial = None
    recorder.report = {'commands': []}
    error = None; code = 1
    try:
        stdout, stderr, entry = recorder.run('test-executable', argv, accept_failure=True)
        sys.stdout.buffer.write(stdout); sys.stdout.buffer.flush()
        sys.stderr.buffer.write(stderr); sys.stderr.buffer.flush()
        code = entry['exit_code']
    except BaseException as failure:
        error = failure
    try:
        after = q.executable_identity(Path(argv[0]))
        if before != after:
            raise ValueError('Go test executable changed')
    except BaseException as failure:
        after = None; error = error or failure
    q.write_new(output / 'receipt.json', q.canonical(dict(before=before, after=after,
        commands=recorder.report['commands'], status='failed' if error or code else 'passed',
        error=repr(error) if error else None, exit_code=code)))
    return 1 if error else code


def stage_native_sources(attempt):
    work = attempt.work
    for name in NATIVE_STAGE_PATHS:
        q.stage(attempt, name, work / name)
    for name in ('python/src', 'python/tests', 'go', 'typescript/src', 'typescript/test',
                 'swift', 'conformance', 'spec', 'scripts', 'examples/native-observation'):
        attempt.watch('staged-source:' + name, work / name)


def python_test_runner(attempt, name, test_directory, source_directories=(), pattern='test*.py'):
    """Isolated runner; only explicitly sealed test/source roots join sys.path."""
    directories = [Path(test_directory), *map(Path, source_directories)]
    for directory in directories:
        if (not directory.is_dir() or directory.resolve() != directory
                or not any(directory == Path(root) or directory.is_relative_to(Path(root))
                           for root in attempt.staged_roots)):
            raise ValueError('Python import directory is not sealed staged source: ' + str(directory))
        attempt.watch('python-import:' + str(directory), directory)
    q.assert_sources(attempt.staged_snapshot(), attempt.staged)
    directory = attempt.work / 'python-runners' / name
    directory.mkdir(parents=True, exist_ok=False)
    runner = directory / 'suite.py'
    # Import stdlib before introducing sealed project roots. -I removes cwd,
    # the script directory, user site and PYTHONPATH from Python's search path.
    bootstrap = ('import json,pathlib,sys,unittest\n'
                 'assert sys.flags.isolated and sys.dont_write_bytecode\n'
                 'sys.path[:0] = ' + repr(list(map(str, directories))) + '\n')
    discovery = ('suite=loader.discover(' + repr(str(test_directory)) + ',pattern=' +
                 repr(pattern) + ',top_level_dir=' + repr(str(test_directory)) + ')\n')
    runner.write_text(bootstrap + 'loader=unittest.TestLoader()\n' + discovery + """
def ids(node):
 for item in node:
  if isinstance(item,unittest.TestSuite): yield from ids(item)
  else: yield item.id()
names=list(ids(suite))
result=unittest.TextTestRunner(verbosity=2).run(suite)
data=dict(discovered_names=names,loader_errors=loader.errors,tests_run=result.testsRun,
 skipped=[(str(t),why) for t,why in result.skipped],failures=[str(t) for t,_ in result.failures],
 errors=[str(t) for t,_ in result.errors],expected_failures=[str(t) for t,_ in result.expectedFailures],
 unexpected_successes=[str(t) for t in result.unexpectedSuccesses])
with pathlib.Path(sys.argv[1]).open('x') as f: json.dump(data,f,sort_keys=True)
raise SystemExit(0 if result.wasSuccessful() and result.testsRun and not loader.errors else 1)
""")
    attempt.register_generated(runner, name + '-suite-runner')
    attempt.staged_roots[str(directory)] = 'generated-python-runner'
    attempt.watch('python-runner-directory:' + name, directory)
    return runner


def native(attempt):
    work = attempt.work
    stage_native_sources(attempt)
    py = q.require_tool(attempt, 'python3')
    attempt.run('python-version', [py, '--version'])
    # Run every observation proof/receipt/process regression as a mandatory
    # stage. A skipped validator is not successful validator execution.
    validator_summary = attempt.output / 'validator-test-counts.json'
    validator_runner = python_test_runner(attempt, 'validator', work / 'scripts',
                                          pattern='test_observation_*.py')
    _, _, validator_command = attempt.run('receipt-validator-regressions',
        [py, '-I', '-B', validator_runner, validator_summary], work, accept_failure=True)
    validator_counts = q.strict_json(validator_summary.read_bytes())
    attempt.report['validator_unittests'] = validator_counts
    expected_modules = {p.stem for p in (work / 'scripts').glob('test_observation_*.py')}
    executed_modules = {name.split('.', 1)[0] for name in validator_counts['discovered_names']}
    if (validator_command['exit_code'] or validator_counts['tests_run'] <= 0
            or validator_counts['tests_run'] != len(validator_counts['discovered_names'])
            or expected_modules != executed_modules
            or any(validator_counts[k] for k in ('skipped', 'expected_failures', 'unexpected_successes',
                                               'failures', 'errors', 'loader_errors'))):
        raise ValueError('validator regressions failed, skipped, or omitted a discovered module')
    attempt.report['validator_unittest_count'] = validator_counts['tests_run']
    summary = attempt.output / 'python-test-counts.json'
    runner = python_test_runner(attempt, 'python', work / 'python/tests', (work / 'python/src',))
    failures = []
    def suite(name, fn):
        try:
            fn()
        except Exception as error:
            failures.append(dict(suite=name, error=repr(error)))
            attempt.report['suite_failures'] = failures

    def python_suite():
        _, _, entry = attempt.run('python-full-suite', [py, '-I', '-B', runner, summary], work,
                                 accept_failure=True)
        counts = q.strict_json(summary.read_bytes())
        counts['executed_not_skipped'] = counts['tests_run'] - len(counts['skipped'])
        attempt.report.setdefault('native_test_execution', {})['python'] = counts
        if entry['exit_code'] or not counts['executed_not_skipped']:
            raise ValueError('Python full suite failed or executed no tests')
    suite('python', python_suite)

    def go_suite():
        go = q.require_tool(attempt, 'go'); attempt.run('go-version', [go, 'version'])
        exec_dir = attempt.output / 'go-executables'; exec_dir.mkdir()
        wrapper = shlex.join([py, '-I', '-B', str(work / 'scripts/qualify-native-observation.py'), '--go-exec'])
        raw, _, entry = attempt.run('go-full-suite', [go, 'test', '-json', '-count=1',
                     '-exec', wrapper, './...'], work / 'go',
                     overlay={'OBSERVATION_GO_EXEC_DIR': str(exec_dir)}, accept_failure=True)
        events = [q.strict_json(line) for line in raw.splitlines() if line]
        terminal = [e for e in events if e.get('Test') and e.get('Action') in ('pass', 'fail', 'skip')]
        receipts = list(exec_dir.glob('*/receipt.json'))
        attempt.report.setdefault('native_test_execution', {})['go'] = dict(
            terminal_test_events_including_subtests=terminal, executable_processes=len(receipts))
        if entry['exit_code'] or not terminal or any(e['Action'] == 'fail' for e in terminal):
            raise ValueError('no Go test execution or failing Go tests')
        if not receipts or any(q.strict_json(p.read_bytes()).get('status') != 'passed' for p in receipts):
            raise ValueError('missing/failed Go executable identity receipts')
        attempt.run('go-vet', [go, 'vet', './...'], work / 'go')
    suite('go', go_suite)

    def ts_suite():
        # npm ci stays inside this Kyoto stage; Recorder's compiler consumer
        # resolves the same pinned local node_modules/typescript installation.
        compiler = q.typescript_compiler(attempt, work / 'typescript')
        attempt.run('node-version', [compiler[0], '--version'])
        attempt.run('typescript-build', compiler + ['-p', 'tsconfig.json'], work / 'typescript')
        attempt.watch('typescript-runtime-dist', work / 'typescript/dist')
        tests = sorted((work / 'typescript/test').glob('*.test.mjs'))
        if not tests:
            raise ValueError('TypeScript full suite is empty')
        raw, _, entry = attempt.run('typescript-full-suite', [compiler[0], '--test', '--test-reporter=tap', *tests], work / 'typescript', accept_failure=True)
        counts = {}
        for key in ('tests', 'pass', 'fail', 'cancelled', 'skipped', 'todo'):
            values = re.findall(rb'^# ' + key.encode() + rb' (\d+)\s*$', raw, re.MULTILINE)
            if len(values) != 1:
                raise ValueError('missing TypeScript TAP summary: ' + key)
            counts[key] = int(values[0])
        attempt.report.setdefault('native_test_execution', {})['typescript'] = counts
        if entry['exit_code'] or not counts['tests'] or counts['fail'] or counts['cancelled']:
            raise ValueError('TypeScript did not execute a successful full suite')
    suite('typescript', ts_suite)

    def swift_suite():
        swift = q.require_tool(attempt, 'swift')
        attempt.run('swift-version', [swift, '--version'])
        attempt.run('swift-tests-build', [swift, 'build', '--build-tests', '-j', '2'], work)
        # Bind actual XCTest executables before the --skip-build runtime stage.
        executables = [p for p in (work / '.build').rglob('*') if p.is_file()
                       and os.access(p, os.X_OK) and ('xctest' in p.name or
                       any(part.endswith('.xctest') for part in p.parts))]
        if not executables:
            raise ValueError('no Swift XCTest executable found')
        for i, path in enumerate(executables):
            attempt.watch('swift-xctest:' + str(i), path)
        stdout, stderr, entry = attempt.run('swift-full-suite', [swift, 'test', '--skip-build',
                       '--disable-swift-testing', '-j', '2'], work, accept_failure=True)
        summaries = re.findall(rb'Executed (\d+) tests?, with (\d+) failures? '
                               rb'\((\d+) unexpected\)', stdout + b'\n' + stderr)
        if not summaries:
            raise ValueError('missing XCTest runtime summary')
        attempt.report.setdefault('native_test_execution', {})['swift'] = dict(
            executed=int(summaries[-1][0]), failures=int(summaries[-1][1]),
            unexpected=int(summaries[-1][2]), xctest_executables=len(executables))
        if entry['exit_code'] or int(summaries[-1][0]) == 0 or any(int(v) for v in summaries[-1][1:]):
            raise ValueError('missing successful XCTest runtime summary')
    suite('swift', swift_suite)
    attempt.report['staged_source_and_artifacts'] = q.manifest(attempt.root, q.tree_identity(work))
    attempt.report['scope_gaps'].extend([
        'Python ABI opt-in skips are retained separately; this scope claims native independent-engine suites only.',
        'CGO_ENABLED=0; no Go race-instrumented execution is claimed.',
        'Full native suites preserve existing checked-token/Recorder regressions; the installed fresh-process suffix gate belongs to the separate installed scope.',
    ])
    if failures:
        raise RuntimeError('one or more native full suites failed; see suite_failures and raw logs')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=('native', 'rust-compile', 'rust-runtime', 'installed'), required=True)
    parser.add_argument('--expected-commit', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compile-receipt', type=Path)
    parser.add_argument('--rust', type=Path)
    parser.add_argument('--cargo-target', type=Path)
    parser.add_argument('--timeout', type=float, default=900)
    args = parser.parse_args(argv)
    if args.scope in ('rust-runtime', 'installed') and args.compile_receipt is None:
        parser.error('this scope needs --compile-receipt from a prior Kyoto rust-compile attempt')
    if args.scope == 'installed' and args.rust is None:
        parser.error('installed needs --rust from that compile receipt')
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    attempt = q.Attempt(ROOT, args.output, args.expected_commit, args.scope, args.timeout)
    error = None
    try:
        fixture = attempt.seal()
        if args.scope == 'native':
            native(attempt)
        elif args.scope == 'rust-compile':
            compile_rust(attempt, args.cargo_target)
        elif args.scope == 'rust-runtime':
            rust_runtime(attempt, fixture, args.compile_receipt)
        else:
            q.installed(attempt, fixture, args.rust, args.compile_receipt)
    except BaseException as failure:
        error = failure
    return attempt.finish(error)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--go-exec']:
        raise SystemExit(record_go_exec(sys.argv[2:]))
    raise SystemExit(main())

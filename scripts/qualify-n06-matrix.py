#!/usr/bin/env python3
"""Compare five caller-supplied installed N06 consumers against the sealed oracle.

This is an execution/comparison component, not an installer or a source-to-binary
provenance proof. Submit via Kyoto after preparing and independently pinning the
consumer commands and artifacts. A passing result does not qualify platform,
host-fault, mutation, or resource gates.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
ENGINES = ("rust", "python", "go", "typescript", "swift")
CORPUS = ROOT / "validation/native-parity/n06/current-corpus/expected.jsonl"
FREEZE = ROOT / "validation/native-parity/n06/current-corpus/FREEZE.json"
MAX_OUTPUT = 8 * 1024 * 1024


def strict_json(data):
    def pairs(items):
        d = {}
        for k, v in items:
            if k in d:
                raise ValueError("duplicate JSON key: " + k)
            d[k] = v
        return d
    def bad(value):
        raise ValueError("invalid JSON constant: " + value)
    return json.loads(data, object_pairs_hook=pairs, parse_constant=bad)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def canonical(row):
    return json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def load_oracle():
    seal_bytes = FREEZE.read_bytes()
    if hashlib.sha256(seal_bytes).hexdigest() != '60c5f86a7a6abaa4ce63380490086a7c9309f13eefd3e150c2ded5393e9b6516':
        raise ValueError('independent seal digest mismatch')
    seal = strict_json(seal_bytes)
    if (seal.get('schema') != 'n06-independent-spec/1' or seal.get('candidate_output_used') is not False
            or seal.get('source_commit') != '0a26bf5a068e5948d82a3d8bd9eb4063ae85115f'
            or not isinstance(seal.get('derivation'), str) or not seal['derivation']):
        raise ValueError('invalid independent seal')
    files = seal.get('files')
    if not isinstance(files, dict) or str(CORPUS.relative_to(ROOT)) not in files:
        raise ValueError('seal missing corpus')
    for name, digest in files.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('unsafe sealed path')
        path = (ROOT / relative).resolve(strict=True)
        if not path.is_relative_to(ROOT) or not path.is_file() or sha(path) != digest:
            raise ValueError('sealed input mismatch: ' + name)
    rows = [strict_json(line) for line in CORPUS.read_bytes().splitlines() if line.strip()]
    cases = [r.get('case') for r in rows]
    if len(rows) != 49 or len(set(cases)) != 49 or cases != seal.get('cases'):
        raise ValueError('independent case inventory mismatch')
    if any(r.get('schema') != 'native-campaign-v1' or r.get('kind') not in ('seed', 'validation', 'campaign') for r in rows):
        raise ValueError('invalid oracle row')
    return rows


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or set(manifest) != {'schema', 'engines'} or manifest['schema'] != 'n06-installed-commands/1':
        raise ValueError('invalid manifest')
    engines = manifest['engines']
    if not isinstance(engines, dict) or set(engines) != set(ENGINES):
        raise ValueError('all five engines are required')
    for name in ENGINES:
        e = engines[name]
        if not isinstance(e, dict) or set(e) != {'argv', 'cwd', 'artifacts', 'consumer'}:
            raise ValueError(name + ': invalid engine entry')
        argv, cwd, artifacts = e['argv'], e['cwd'], e['artifacts']
        if not isinstance(argv, list) or not argv or any(type(x) is not str or not x or '\x00' in x for x in argv):
            raise ValueError(name + ': invalid argv')
        executable = Path(argv[0])
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError(name + ': executable must be an absolute file')
        directory = Path(cwd)
        if not directory.is_absolute() or not directory.is_dir() or directory.resolve().is_relative_to(ROOT):
            raise ValueError(name + ': cwd must be an external directory')
        if not isinstance(artifacts, dict) or not artifacts or str(executable.resolve()) not in artifacts:
            raise ValueError(name + ': executable identity is required')
        consumer = e['consumer']
        if (type(consumer) is not str or not Path(consumer).is_absolute()
                or consumer not in artifacts or consumer not in argv):
            raise ValueError(name + ': pinned consumer must be an explicit absolute argv member')
        if any(x in ('-c', '-e', '--eval', '--exec') for x in argv[1:]):
            raise ValueError(name + ': inline commands are not supported')
        for p, digest in artifacts.items():
            path = Path(p)
            if (not path.is_absolute() or str(path.resolve()) != p or path.is_symlink() or not path.is_file() or path.resolve().is_relative_to(ROOT)
                    or not isinstance(digest, str) or len(digest) != 64 or sha(path) != digest):
                raise ValueError(name + ': artifact identity mismatch: ' + p)
        for arg in argv[1:]:
            path = Path(arg)
            if path.is_absolute() and path.is_file() and str(path.resolve()) not in artifacts:
                raise ValueError(name + ': unpinned file argument: ' + arg)
            if not path.is_absolute() and (directory / path).is_file():
                raise ValueError(name + ': relative file arguments are not supported')
    if len({engines[n]['artifacts'][engines[n]['consumer']] for n in ENGINES}) != len(ENGINES):
        raise ValueError('consumer artifacts must be distinct across engines')
    return engines


def run_engine(name, entry, out, timeout):
    stdout = out / (name + '.stdout'); stderr = out / (name + '.stderr')
    started = time.monotonic()
    env = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONOPTIMIZE', 'NODE_PATH', 'NODE_OPTIONS'):
        env.pop(key, None)
    env['PYTHONNOUSERSITE'] = '1'
    env['PYTHONOPTIMIZE'] = '0'
    with stdout.open('xb') as so, stderr.open('xb') as se, selectors.DefaultSelector() as sel:
        proc = subprocess.Popen(entry['argv'], cwd=entry['cwd'], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, start_new_session=True)
        for pipe, target in ((proc.stdout, so), (proc.stderr, se)):
            os.set_blocking(pipe.fileno(), False)
            sel.register(pipe, selectors.EVENT_READ, target)
        counts = {so: 0, se: 0}
        group_cleaned = False
        try:
            while sel.get_map():
                if time.monotonic() - started > timeout:
                    raise RuntimeError(name + ': timeout waiting for process and output EOF')
                if proc.poll() is not None and not group_cleaned:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    else:
                        raise RuntimeError(name + ': descendant remained after consumer exit')
                    group_cleaned = True
                for key, _ in sel.select(timeout=0.1):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        sel.unregister(key.fileobj); key.fileobj.close(); continue
                    target = key.data
                    remaining = MAX_OUTPUT - counts[target]
                    target.write(chunk[:max(0, remaining)])
                    counts[target] += len(chunk)
                    if counts[target] > MAX_OUTPUT:
                        raise RuntimeError(name + ': output limit exceeded')
            code = proc.wait(timeout=max(0.001, timeout - (time.monotonic()-started)))
            if code != 0:
                raise RuntimeError(f'{name}: exit {code}')
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            else:
                raise RuntimeError(name + ': descendant remained after consumer exit')
        finally:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            proc.wait()
            for pipe in (proc.stdout, proc.stderr):
                if not pipe.closed: pipe.close()
    rows = [strict_json(line) for line in stdout.read_bytes().splitlines() if line.strip()]
    return rows, {'exit': code, 'elapsed_seconds': time.monotonic()-started,
                  'stdout_sha256': sha(stdout), 'stderr_sha256': sha(stderr)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--commands', required=True, type=Path)
    p.add_argument('--commands-sha256', required=True)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--timeout', type=float, default=300)
    a = p.parse_args()
    if not math.isfinite(a.timeout) or a.timeout <= 0 or not a.commands.is_file() or sha(a.commands) != a.commands_sha256:
        p.error('invalid timeout or caller-pinned manifest digest')
    output = a.output.resolve()
    if output.is_relative_to(ROOT):
        p.error('output must be outside checkout')
    output.mkdir(parents=True, exist_ok=False)
    command_bytes = a.commands.read_bytes()
    if hashlib.sha256(command_bytes).hexdigest() != a.commands_sha256:
        raise ValueError('manifest changed while reading')
    (output / 'commands.json').write_bytes(command_bytes)
    report = {'schema':'n06-five-engine-comparison/1', 'status':'failed',
              'commands_sha256':a.commands_sha256, 'oracle_sha256':sha(CORPUS),
              'seal_sha256':sha(FREEZE), 'scope':'caller-attested installed artifacts; not source-to-binary proof', 'engines':{}}
    try:
        expected = load_oracle()
        engines = validate_manifest(strict_json(command_bytes))
        expected_rows = [canonical(row) for row in expected]
        for name in ENGINES:
            report['engines'][name] = {'argv': engines[name]['argv'], 'cwd': engines[name]['cwd'],
                                       'artifacts': engines[name]['artifacts']}
            rows, receipt = run_engine(name, engines[name], output, a.timeout)
            report['engines'][name].update(receipt)
            if [canonical(row) for row in rows] != expected_rows:
                raise RuntimeError(name + ': rows differ from sealed 49-case oracle')
            validate_manifest(strict_json(a.commands.read_bytes()))
            if sha(a.commands) != a.commands_sha256:
                raise RuntimeError('commands manifest changed')
        if [canonical(row) for row in load_oracle()] != expected_rows:
            raise RuntimeError('oracle changed during execution')
        report['status'] = 'passed_49_case_comparison'
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
        for name, receipt in report['engines'].items():
            for stream in ('stdout', 'stderr'):
                path = output / (name + '.' + stream)
                if path.is_file(): receipt[stream + '_sha256'] = sha(path)
    (output / 'result.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'passed_49_case_comparison' else 1


if __name__ == '__main__':
    sys.exit(main())

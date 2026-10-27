#!/usr/bin/env python3
"""Run explicit N12 candidate commands and precompiled stdio endpoints via Kyoto.

Submit this script through Kyoto. It never submits/retries/cancels other jobs or
builds implicitly. Commands in --spec execute exactly as supplied, so Cargo's
compile-only policy does not apply: supply precompiled test binaries explicitly.
Output must be a NEW validation directory. Any source membership/type/hash drift
fails qualification, including on failed subprocesses. Logs are raw, lossless.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import stat
import struct
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / 'validation/native-parity/n12-recovery/integration/os-pipe-goldens.json'


def identity(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def snapshot(extra_paths=()):
    names = set(os.fsdecode(p) for p in git('ls-files', '--cached', '--others', '--exclude-standard', '-z').split(b'\0') if p)
    # Keep validation executable/example sources; mutable receipts are excluded.
    source_suffixes = {'.py', '.go', '.mjs', '.js', '.ts', '.swift', '.rs', '.sh', '.toml'}
    names = {p for p in names if not p.startswith('validation/') or Path(p).suffix in source_suffixes}
    names.update(str(p.relative_to(ROOT)) for p in GOLDEN.parent.glob('*goldens.json'))
    names.update(str(p.resolve().relative_to(ROOT)) for p in extra_paths)
    rows = {}
    for name in sorted(names):
        path = ROOT / name
        for parent in path.parents:
            if parent == ROOT:
                break
            if parent.is_symlink():
                raise RuntimeError(f'symlink ancestor: {name}')
        try:
            before = path.lstat()
        except FileNotFoundError:
            rows[name] = {'type': 'missing'}
            continue
        if stat.S_ISREG(before.st_mode):
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise RuntimeError(f'identity changed opening {name}')
                data = stream.read()
                after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise RuntimeError(f'changed reading {name}')
            current = path.lstat()
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise RuntimeError(f'pathname changed reading {name}')
            rows[name] = {'type': 'regular', 'mode': stat.S_IMODE(before.st_mode), **identity(data)}
        elif stat.S_ISLNK(before.st_mode):
            rows[name] = {'type': 'symlink', 'target': os.readlink(path)}
        else:
            raise RuntimeError(f'unsupported source type: {name}')
    return rows


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def executable_identity(argv, cwd):
    executable = Path(argv[0])
    if '/' in argv[0]:
        executable = (cwd / executable).resolve()
    else:
        executable = Path(shutil.which(argv[0]) or argv[0]).resolve()
    return {'path': str(executable), **identity(executable.read_bytes())}


def run_command(entry, output):
    argv = entry['argv']
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        raise ValueError('argv must be a nonempty string array')
    cwd = (ROOT / entry.get('cwd', '.')).resolve()
    if cwd != ROOT and ROOT not in cwd.parents:
        raise ValueError('command cwd escapes candidate')
    binary_before = executable_identity(argv, cwd)
    env = os.environ.copy()
    env.update(entry.get('env', {}))
    with (output / 'stdout.log').open('wb') as stdout, (output / 'stderr.log').open('wb') as stderr:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stdout, stderr=stderr)
        try:
            result = process.wait(timeout=entry.get('timeout_seconds', 900))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RuntimeError('qualification subprocess timed out; this script does not resubmit it')
    binary_after = executable_identity(argv, cwd)
    stdout_raw = (output / 'stdout.log').read_bytes()
    required = entry.get('required_stdout', [])
    assertions_matched = all(value.encode('utf-8') in stdout_raw for value in required)
    return {'argv': argv, 'cwd': str(cwd), 'exit_code': result,
            'binary_before': binary_before, 'binary_after': binary_after,
            'passed': result == 0 and binary_before == binary_after and assertions_matched,
            'required_stdout': required, 'required_stdout_matched': assertions_matched,
            'assertion_scope': entry['scope']}


def pipe_fixture(entry, output):
    """Exercise genuine OS pipes against an explicitly configured readonly host."""
    golden = json.loads(GOLDEN.read_bytes())
    binary_before = executable_identity(entry['argv'], ROOT)
    tx = (output / 'stdin.frames').open('wb')
    rx = (output / 'stdout.frames').open('wb')
    stderr = (output / 'stderr.log').open('wb')
    process = subprocess.Popen(entry['argv'], cwd=ROOT, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=stderr, bufsize=0)
    timeout = entry.get('timeout_seconds', 30)
    deadline = time.monotonic() + timeout

    def exact(size):
        value = bytearray()
        while len(value) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], max(0, remaining))[0]:
                raise RuntimeError('OS-pipe response timeout')
            chunk = os.read(process.stdout.fileno(), size - len(value))
            if not chunk:
                raise RuntimeError('unexpected pipe EOF')
            rx.write(chunk)
            rx.flush()
            value.extend(chunk)
        return bytes(value)

    def request(epoch, request_id, command):
        payload = ('DDBG\tu32:1\trequest\tstring:' + golden['session'] +
                   f'\tu64:{epoch}\tu64:{request_id}\tu64:0\tu64:0\t' + command).encode('ascii')
        wire = struct.pack('>I', len(payload)) + payload
        tx.write(wire)
        tx.flush()
        # Split header/payload writes exercise stream framing, not in-memory IO.
        for chunk in (wire[:1], wire[1:3], wire[3:7], wire[7:]):
            process.stdin.write(chunk)
            process.stdin.flush()
        size = struct.unpack('>I', exact(4))[0]
        if not 0 < size <= 65536:
            raise AssertionError('invalid response frame length')
        raw = exact(size)
        fields = raw.decode('utf-8', errors='strict').split('\t')
        assert fields[:4] == ['DDBG', 'u32:1', 'response', 'string:' + golden['session']]
        assert fields[5:8] == [f'u64:{request_id}', 'u64:0', 'u64:0']
        count = int(fields[10].removeprefix('u64:'))
        assert len(fields) == 11 + 2 * count
        pairs = list(zip(fields[11::2], fields[12::2]))
        assert len(dict(pairs)) == count, 'duplicate response fields'
        return raw, fields, dict(pairs)

    try:
        _, status, values = request(0, golden['fullwidth_request_id'], 'status')
        assert status[8:10] == ['ok', 'none']
        assert all(values.get(k) == v for k, v in golden['read_only_status'].items())
        epoch = int(status[4].removeprefix('u64:'))
        assert 0 < epoch <= 18446744073709551615
        raw, fields, values = request(epoch, golden['fullwidth_request_id'] + 1, 'snapshot')
        assert fields[8:10] == ['ok', 'none']
        assert values == golden['snapshot']
        duplicate, _, _ = request(epoch, golden['fullwidth_request_id'] + 1, 'snapshot')
        assert duplicate == raw
        _, fields, _ = request(epoch, golden['fullwidth_request_id'] + 1, 'status')
        assert fields[8:10] == ['error', 'duplicate_conflict']
        # Authorization precedes model input decoding; no input callback can run.
        _, fields, _ = request(epoch, golden['fullwidth_request_id'] + 2, 'step\tbytes:')
        assert fields[8:10] == ['error', 'unauthorized']
        _, _, values = request(epoch, golden['fullwidth_request_id'] + 3, 'status')
        assert values['sequence'] == 'u64:0'
        process.stdin.close()
        assert process.wait(timeout=max(1, deadline - time.monotonic())) == 0
        extra = process.stdout.read()
        rx.write(extra)
        assert extra == b'', 'stdout contains unframed bytes'
        truncated = b'\x00\x00\x00\x03x'
        (output / 'truncated.stdin.frames').write_bytes(truncated)
        with (output / 'truncated.stdout.frames').open('wb') as bad_out, (output / 'truncated.stderr.log').open('wb') as bad_err:
            bad = subprocess.Popen(entry['argv'], cwd=ROOT, stdin=subprocess.PIPE, stdout=bad_out, stderr=bad_err)
            try:
                bad.communicate(truncated, timeout=timeout)
            except subprocess.TimeoutExpired:
                bad.kill()
                bad.communicate()
                raise RuntimeError('truncated-frame process did not exit')
        assert bad.returncode != 0
        assert (output / 'truncated.stdout.frames').stat().st_size == 0
        binary_after = executable_identity(entry['argv'], ROOT)
        assert binary_before == binary_after, 'endpoint binary changed'
        return {'passed': True, 'argv': entry['argv'], 'scope': golden['scope'],
                'binary_before': binary_before, 'binary_after': binary_after,
                'requests': 6, 'delivered_sequence': 0,
                'callback_count': 'not instrumented; sequence and authorization checked'}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if not process.stdout.closed:
            rx.write(process.stdout.read())
        for stream in (process.stdin, process.stdout, tx, rx, stderr):
            stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT / 'validation' not in output.parents:
        parser.error('output must be a new directory beneath candidate validation/')
    output.mkdir(parents=True, exist_ok=False)
    spec_raw = args.spec.read_bytes()
    (output / 'spec.json').write_bytes(spec_raw)
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'worktree': str(ROOT),
              'commit': git('rev-parse', 'HEAD').decode().strip(),
              'branch': git('branch', '--show-current').decode().strip(),
              'spec': identity(spec_raw), 'golden': identity(GOLDEN.read_bytes()),
              'commands': [], 'passed': False, 'broad_qualification_cells_pending': 144,
              'profile_promotion': False}
    before = None
    try:
        spec = json.loads(spec_raw)
        baseline_path = (ROOT / spec['source_baseline']).resolve() if 'source_baseline' in spec else None
        pinned = [args.spec] + ([baseline_path] if baseline_path else [])
        before = snapshot(pinned)
        save_json(output / 'source-before.json', before)
        if baseline_path:
            baseline = json.loads(baseline_path.read_bytes())['source']
            current_names = {name for name in before if name.startswith(('src/', 'crates/')) or name in ('Cargo.toml', 'Cargo.lock')}
            if current_names != set(baseline):
                raise RuntimeError('Rust source baseline membership mismatch')
            for name, expected in baseline.items():
                if any(before[name].get(key) != value for key, value in expected.items()):
                    raise RuntimeError(f'Rust source baseline mismatch: {name}')
            report['source_baseline_matched'] = True
            report['source_baseline'] = identity(baseline_path.read_bytes())
        entries = spec.get('commands', []) + spec.get('stdio_endpoints', [])
        if not entries:
            raise ValueError('empty qualification scope')
        for index, entry in enumerate(entries):
            directory = output / f'{index:02d}'
            directory.mkdir()
            try:
                if index < len(spec.get('commands', [])):
                    result = run_command(entry, directory)
                else:
                    result = pipe_fixture(entry, directory)
            except Exception as error:
                result = {'passed': False, 'error': f'{type(error).__name__}: {error}', 'entry': entry}
            result['artifacts'] = {p.name: identity(p.read_bytes()) for p in directory.iterdir() if p.is_file()}
            report['commands'].append(result)
            save_json(directory / 'receipt.json', result)
        report['passed'] = all(row['passed'] for row in report['commands'])
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    finally:
        try:
            after = snapshot(pinned if before is not None else [args.spec])
            save_json(output / 'source-after.json', after)
            report['source_stable'] = before is not None and before == after
            report['source_changed_paths'] = sorted(p for p in set(before or {}) | set(after) if (before or {}).get(p) != after.get(p))
        except Exception as error:
            report['source_stable'] = False
            report['source_error'] = f'{type(error).__name__}: {error}'
        report['passed'] = report['passed'] and report['source_stable']
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        save_json(output / 'qualification.json', report)
        print(json.dumps(report), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Finite public-Rust/native wire comparison; submit through shared-default Kyoto.

Expected artifacts below are source-derived, never captured from a host. Every
process is fresh and is driven through real pipes. Only epoch identity and field
ordering are normalized. Failed attempts and raw streams are never overwritten.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import stat
import struct
import subprocess
import time
import traceback

ROOT = Path(__file__).resolve().parents[3]
SESSION = 'wire\té😀'
MAX_FRAME = 65536
U64 = (1 << 64) - 1
SAFE = frozenset(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.:')


def digest(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n')


def file_identity(path):
    before = path.lstat()
    mode = stat.S_IMODE(before.st_mode)
    if stat.S_ISLNK(before.st_mode):
        return {'type': 'symlink', 'mode': mode, 'target': os.readlink(path)}
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError(f'unsupported file type: {path}')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError(f'identity changed opening {path}')
        data = stream.read()
        after = os.fstat(stream.fileno())
    current = path.lstat()
    stable = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if stable(before) != stable(after) or stable(after) != stable(current):
        raise RuntimeError(f'file changed while reading: {path}')
    return {'type': 'regular', 'mode': mode, **digest(data)}


def source_snapshot():
    raw = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT)
    names = {os.fsdecode(p) for p in raw.split(b'\0') if p}
    source_extensions = {'.py', '.go', '.mjs', '.js', '.ts', '.swift', '.rs', '.sh', '.toml'}
    # Mutable/historical receipts are not sources. All other tracked and new
    # nonignored paths are included, not merely files touched by this change.
    names = {p for p in names if not p.startswith('validation/') or Path(p).suffix in source_extensions}
    result = {}
    for name in sorted(names):
        path = ROOT / name
        for parent in path.parents:
            if parent == ROOT:
                break
            if parent.is_symlink():
                raise RuntimeError(f'symlink source ancestor: {name}')
        result[name] = file_identity(path) if path.exists() or path.is_symlink() else {'type': 'missing'}
    return result


def tree_snapshot(root):
    # Re-enumeration catches newly added inputs as well as changed originals.
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            if path.is_symlink() or not path.is_dir():
                result[str(path.relative_to(root))] = file_identity(path)
    return result


def resolution_snapshot(path):
    """Record every symlink traversed, including parent components, and bytes."""
    candidate = Path(os.path.abspath(path))
    rows = {}
    for _ in range(64):
        parts = candidate.parts
        prefix = Path(parts[0])
        for index, part in enumerate(parts[1:], 1):
            prefix /= part
            if prefix.is_symlink():
                rows[str(prefix)] = file_identity(prefix)
                target = Path(os.readlink(prefix))
                candidate = Path(os.path.abspath(prefix.parent / target / Path(*parts[index + 1:])))
                break
        else:
            if candidate.is_file():
                rows[str(candidate)] = file_identity(candidate)
            return rows
    raise RuntimeError(f'executable resolution cycle: {path}')


def directory_alias_identity(item, root):
    """Allow internal real-directory aliases (e.g. venv lib64 -> lib), not traversal.

    The ordinary non-following walk already manifests the destination contents.
    Reject every indirect link, escape and ancestor alias instead of following
    an untrusted directory graph. Preserve link spelling and destination mode.
    """
    identity = file_identity(item)
    target = item.parent / identity['target']
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        raise RuntimeError(f'directory symlink escapes installed artifact: {item}')
    destination = root
    for part in parts:
        destination = destination.parent if part == '..' else destination / part
        if not destination.is_relative_to(root):
            raise RuntimeError(f'directory symlink escapes installed artifact: {item}')
        metadata = destination.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError(f'indirect directory symlink in installed artifact: {item}')
    if destination == item.parent or destination in item.parents:
        raise RuntimeError(f'cyclic directory symlink in installed artifact: {item}')
    if file_identity(item) != identity:
        raise RuntimeError(f'directory symlink changed while reading: {item}')
    return {**identity, 'resolved_directory': str(destination),
            'directory_mode': stat.S_IMODE(destination.lstat().st_mode)}


def artifact_snapshot(host):
    paths = {Path(p).absolute() for p in host['artifacts']}
    executable = Path(host['argv'][0])
    if not executable.is_absolute():
        executable = Path(shutil.which(str(executable)) or str(executable)).resolve()
    paths.add(executable)
    paths.add(executable.resolve())
    rows = {}
    for path in sorted(paths):
        rows.update(resolution_snapshot(path))
        if path.is_dir():
            for directory, dirs, files in os.walk(path, followlinks=False):
                directory = Path(directory)
                for name in sorted(dirs + files):
                    item = directory / name
                    if item.is_symlink():
                        if not item.exists():
                            raise RuntimeError(f'broken or cyclic symlink in installed artifact: {item}')
                        if item.is_dir():
                            rows[str(item)] = directory_alias_identity(item, path)
                            continue
                    if item.is_symlink() or item.is_file():
                        rows[str(item)] = file_identity(item)
                        if item.is_symlink():
                            rows.update(resolution_snapshot(item))
        else:
            rows[str(path)] = file_identity(path)
    for argument in host['argv']:
        path = Path(argument)
        if path.is_absolute() and path.is_file() and str(path) not in rows and str(path.resolve()) not in rows:
            raise RuntimeError(f'launch input is not manifested: {path}')
    return rows


def quote(value):
    return ''.join(chr(b) if b in SAFE else f'%{b:02X}' for b in value.encode('utf-8'))


def unquote(value):
    data = bytearray()
    index = 0
    while index < len(value):
        byte = value[index]
        if byte == 37:
            pair = value[index + 1:index + 3]
            if len(pair) != 2 or re.fullmatch(b'[0-9a-fA-F]{2}', pair) is None:
                raise ValueError('invalid response percent escape')
            data.append(int(pair, 16))
            index += 3
        else:
            if byte not in SAFE:
                raise ValueError('unescaped response byte')
            data.append(byte)
            index += 1
    return data.decode('utf-8', errors='strict')


def request(number, revision, command, *args, configuration=0, epoch=None, session=SESSION):
    return {'number': number, 'revision': revision, 'command': command, 'args': args,
            'configuration': configuration, 'epoch': epoch, 'session': session}


def encode_request(value, epoch):
    fields = ['DDBG', 'u32:1', 'request', 'string:' + value['session'],
              f"u64:{epoch if value['epoch'] is None else value['epoch']}",
              f"u64:{value['number']}", f"u64:{value['revision']}",
              f"u64:{value['configuration']}", value['command'], *value['args']]
    return '\t'.join(map(quote, fields)).encode('ascii')


def decode_response(payload):
    fields = list(map(unquote, payload.split(b'\t')))
    if payload != '\t'.join(map(quote, fields)).encode('ascii'):
        raise ValueError('noncanonical response escaping')
    if len(fields) < 11 or fields[:3] != ['DDBG', 'u32:1', 'response']:
        raise ValueError('response envelope')
    if re.fullmatch(r'u64:[0-9]+', fields[10]) is None:
        raise ValueError('response count')
    count = int(fields[10][4:])
    if fields[10] != f'u64:{count}':
        raise ValueError('noncanonical response count')
    if len(fields) != 11 + count * 2:
        raise ValueError('response field count')
    keys = fields[11::2]
    if len(set(keys)) != count:
        raise ValueError('duplicate response keys')
    return {'envelope': fields[:10], 'fields': dict(zip(keys, fields[12::2]))}


def expected(number, revision=0, fields=None, error=None):
    return {'envelope': ['DDBG', 'u32:1', 'response', 'string:' + SESSION, 'u64:EPOCH',
                         f'u64:{number}', f'u64:{revision}', 'u64:0',
                         'error' if error else 'ok', error or 'none'], 'fields': fields or {}}


def status(revision=0, phase='Ready', stop='none', readonly=False, exact=False, cache_one=False):
    # protocol.rs Command::Status. These are independent literal expectations.
    return {'mode': 'enum:simulation', 'phase': 'enum:' + phase,
            'sequence': f'u64:{revision}', 'environment': 'string:out-of-domain-injection',
            'capability.input_delivery': 'bool:' + str(not readonly).lower(),
            'capability.inspection': 'bool:false', 'capability.watch_configuration': 'bool:false',
            'capability.exact_values': 'bool:' + str(exact).lower(),
            'capability.live_control': 'bool:false', 'capability.live_restore': 'bool:false',
            'capability.effects': 'bool:false', 'capability.probe_queries': 'bool:false',
            'capability.probe_configuration': 'bool:false', 'capability.trace_export': 'bool:false',
            'capability.effect_telemetry': 'bool:false', 'capability.metric_queries': 'bool:false',
            'limits.telemetry_snapshot_bytes': 'u64:8388608', 'limits.frame_bytes': 'u64:65536',
            'limits.dedup_entries': 'u64:1' if cache_one else 'u64:64',
            'limits.dedup_bytes': 'u64:4194304', 'limits.page_size': 'u64:100',
            'events.dropped': 'u64:0', 'stop': 'enum:' + stop}


def snapshot(handle, revision):
    return {'handle': f'u64:{handle}', 'snapshot.revision': f'u64:{revision}',
            'snapshot.sequence': f'u64:{revision}'}


def checks(turn=False, failed=False, sequence=0):
    count = 2 if turn else 1
    result = {'checks.complete': 'bool:true', 'checks.total': f'u64:{count}',
              'checks.returned': f'u64:{count}', 'checks.truncated': 'bool:false',
              'checks.0.id': 'string:bounded', 'checks.0.status': 'enum:failed' if failed else 'enum:passed'}
    if turn:
        result.update({'checks.1.id': 'string:transition', 'checks.1.status': 'enum:passed'})
    if failed:
        result.update({'checks.first_failure.id': 'string:bounded', 'checks.first_failure.index': 'u64:0',
                       'checks.first_failure.phase': 'enum:post_state', 'checks.first_failure.sequence': f'u64:{sequence}'})
    else:
        result['checks.first_failure'] = 'none'
    return result


def outputs(disposition, state=0, exact=False):
    count = 1 if disposition == 'accepted' else 0
    result = {'outputs.total': f'u64:{count}', 'disposition': 'enum:' + disposition,
              'outputs.provenance': 'enum:simulated',
              'outputs.payloads': 'enum:exact_sensitive' if exact else 'enum:unavailable'}
    if exact:
        result['outputs.truncated'] = 'bool:false'
        if count:
            result['outputs.0.encoded'] = 'bytes:' + state.to_bytes(8, 'little').hex()
    return result


INITIAL_CALLS = ['CALL initial', 'CALL state 0']


def turn_calls(before, input, after):
    return [f'CALL decode {input:02x}', f'CALL step {before} {input}',
            f'CALL state {after}', f'CALL transition {before} {input} {after}']


def action(req, response, fragmented=False):
    return {'request': req, 'expected': response, 'fragmented': fragmented}


def step(number, revision, value, delivered=True):
    next_revision = revision + int(delivered)
    return action(request(number, revision, 'step', f'bytes:{value:02x}'),
                  expected(number, next_revision, {'delivered': 'bool:' + str(delivered).lower(),
                                                    'sequence': f'u64:{next_revision}'}))


def scenarios():
    cases = []
    def add(name, actions, calls=INITIAL_CALLS, options=(), fatal=None, fatal_fragment=False, coalesced=False, broken_output=False, final=None):
        final_state = int([call for call in calls if call.startswith('CALL state ')][-1].split()[-1])
        final_revision = max([0] + [int(item['expected']['envelope'][6][4:]) for item in actions])
        cases.append({'name': name, 'actions': actions, 'calls': calls, 'options': list(options),
                      'fatal': fatal, 'fatal_fragment': fatal_fragment, 'coalesced': coalesced,
                      'broken_output': broken_output, 'final': final or f'FINAL {final_state} {final_revision} {final_revision}'})

    for exact in (False, True):
        actions = [action(request(2, 0, 'snapshot'), expected(2, fields=snapshot(1, 0))),
                   action(request(3, U64, 'checks', 'u64:1', configuration=U64), expected(3, fields=checks())),
                   action(request(4, U64, 'outputs', 'u64:1'), expected(4, fields={'outputs.total': 'u64:0'}))]
        calls = list(INITIAL_CALLS)
        state = 0
        for index, (input_value, disposition) in enumerate(((1, 'accepted'), (0, 'ignored'), (2, 'rejected'))):
            n = 5 + index * 5
            actions.append(step(n, index, input_value))
            after = state + int(input_value == 1)
            calls += turn_calls(state, input_value, after)
            state = after
            actions += [action(request(n + 1, index + 1, 'snapshot'), expected(n + 1, index + 1, snapshot(index + 2, index + 1))),
                        action(request(n + 2, U64, 'checks', f'u64:{index + 2}'), expected(n + 2, index + 1, checks(turn=True))),
                        action(request(n + 3, U64, 'outputs', f'u64:{index + 2}'), expected(n + 3, index + 1, outputs(disposition, state, exact))),
                        action(request(n + 4, U64, 'status', configuration=U64), expected(n + 4, index + 1, status(index + 1, 'Paused', exact=exact)))]
        add('turns-exact' if exact else 'turns-redacted', actions, calls, ['--exact'] if exact else [])

    add('property-failure', [step(2, 0, 1), step(3, 1, 1), step(4, 2, 1),
        action(request(5, 3, 'snapshot'), expected(5, 3, snapshot(1, 3))),
        action(request(6, 3, 'checks', 'u64:1'), expected(6, 3, checks(True, True, 3))),
        action(request(7, 3, 'outputs', 'u64:1'), expected(7, 3, outputs('accepted', 3))),
        action(request(8, 3, 'status'), expected(8, 3, status(3, 'Ended', 'property_failure'))),
        action(request(9, 3, 'step', 'bytes:01'), expected(9, 3, error='ended'))],
        INITIAL_CALLS + turn_calls(0, 1, 1) + turn_calls(1, 1, 2) + turn_calls(2, 1, 3) + ['CALL decode 01'])

    add('reducer-error', [step(2, 0, 3, False),
        action(request(3, 0, 'status'), expected(3, fields=status(0, 'Ended', 'model_error'))),
        action(request(4, 0, 'snapshot'), expected(4, fields=snapshot(1, 0))),
        action(request(5, 0, 'checks', 'u64:1'), expected(5, fields=checks())),
        action(request(6, 0, 'step', 'bytes:ff'), expected(6, error='invalid_input'))],
        INITIAL_CALLS + ['CALL decode 03', 'CALL step 0 3', 'CALL decode ff'])

    add('cancellation-order', [action(request(2, 0, 'cancel'), expected(2)),
        action(request(3, 1, 'cancel'), expected(3, error='stale_revision')),
        action(request(4, 0, 'cancel'), expected(4, error='ended')),
        action(request(5, 0, 'step', 'bytes:01'), expected(5, error='ended')),
        action(request(6, 0, 'status'), expected(6, fields=status(0, 'Ended', 'cancelled')))],
        INITIAL_CALLS + ['CALL decode 01'])

    add('authority-before-callback', [
        action(request(2, U64, 'step', 'bytes:01'), expected(2, error='unauthorized')),
        action(request(3, U64, 'cancel'), expected(3, error='unauthorized')),
        action(request(4, 0, 'watch', 'u64:1', 'u64:0'), expected(4, error='unauthorized')),
        action(request(5, 0, 'probe_configure', 'u64:0'), expected(5, error='unauthorized')),
        action(request(6, 0, 'export_trace', 'u64:0', 'u64:0', 'u64:1'), expected(6, error='unauthorized')),
        action(request(7, U64, 'status', configuration=U64), expected(7, fields=status(readonly=True)))], options=['--read-only'])

    add('revision-and-handles', [action(request(2, 0, 'snapshot'), expected(2, fields=snapshot(1, 0))),
        action(request(3, U64, 'step', 'bytes:01'), expected(3, error='stale_revision')),
        step(4, 0, 1),
        action(request(5, 0, 'snapshot'), expected(5, 1, error='stale_revision')),
        action(request(6, 0, 'checks', 'u64:1'), expected(6, 1, error='stale_handle')),
        action(request(7, U64, 'outputs', 'u64:1'), expected(7, 1, error='stale_handle')),
        action(request(8, U64, 'status', configuration=U64), expected(8, 1, status(1, 'Paused')))],
        INITIAL_CALLS + turn_calls(0, 1, 1))

    add('deduplication', [step(2, 0, 1), step(2, 0, 1),
        action(request(2, 1, 'status'), expected(2, 1, error='duplicate_conflict')),
        action(request(3, 1, 'status'), expected(3, 1, status(1, 'Paused')))],
        INITIAL_CALLS + turn_calls(0, 1, 1))
    add('cached-replay-after-later-turn', [step(2, 0, 1), step(3, 1, 1), step(2, 0, 1),
        action(request(4, 2, 'status'), expected(4, 2, status(2, 'Paused')))],
        INITIAL_CALLS + turn_calls(0, 1, 1) + turn_calls(1, 1, 2))
    for ending in ('reducer-error', 'cancel'):
        actions = [step(2, 0, 1), action(request(3, 1, 'snapshot'), expected(3, 1, snapshot(1, 1)))]
        actions.append(step(4, 1, 3, False) if ending == 'reducer-error' else action(request(4, 1, 'cancel'), expected(4, 1)))
        actions += [action(request(5, U64, 'checks', 'u64:1'), expected(5, 1, checks(True))),
                    action(request(6, U64, 'outputs', 'u64:1'), expected(6, 1, outputs('accepted', 1, True)))]
        add('retained-evidence-after-' + ending, actions,
            INITIAL_CALLS + turn_calls(0, 1, 1) + (['CALL decode 03', 'CALL step 1 3'] if ending == 'reducer-error' else []), ['--exact'])
    add('retired-ids', [step(2, 0, 1),
        action(request(3, 1, 'status'), expected(3, 1, status(1, 'Paused', cache_one=True))),
        action(request(2, 0, 'step', 'bytes:01'), expected(2, 1, error='retired_request')),
        action(request(4, 0, 'step', 'bytes:01'), expected(4, 1, error='stale_revision')),
        action(request(5, 1, 'status'), expected(5, 1, status(1, 'Paused', cache_one=True))),
        action(request(4, 0, 'step', 'bytes:01'), expected(4, 1, error='retired_request'))],
        INITIAL_CALLS + turn_calls(0, 1, 1), ['--cache-one'])
    add('discovery-canonical-dedup', [action(request(1, 0, 'status'), expected(1, fields=status())),
        action(request(2, 0, 'status', epoch=0), expected(2, fields=status())),
        action(request(2, 0, 'status'), expected(2, fields=status()))])
    add('envelope-admission', [
        action(request(2, 0, 'step', 'bytes:01', session='other'), expected(2, error='wrong_session')),
        action(request(2, 0, 'step', 'bytes:01', epoch=0), expected(2, error='wrong_epoch')),
        action(request(2, 0, 'step', 'bytes:01', epoch=U64), expected(2, error='wrong_epoch')),
        action(request(0, 0, 'status'), expected(0, error='malformed')), step(2, 0, 1)],
        INITIAL_CALLS + turn_calls(0, 1, 1))
    add('full-width-identities', [
        action(request(9007199254740993, U64, 'status', configuration=U64), expected(9007199254740993, fields=status())),
        action(request(U64 - 1, U64, 'step', 'bytes:01'), expected(U64 - 1, error='stale_revision')),
        step(U64, 0, 1), step(U64, 0, 1),
        action(request(U64 - 1, 0, 'status'), expected(U64 - 1, 1, error='duplicate_conflict'))],
        INITIAL_CALLS + turn_calls(0, 1, 1))
    add('invalid-input-decode', [action(request(2, 0, 'step', 'bytes:ff'), expected(2, error='invalid_input')),
        action(request(3, 0, 'step', 'bytes:'), expected(3, error='invalid_input')),
        action(request(4, 0, 'step', 'bytes:0102'), expected(4, error='invalid_input')), step(5, 0, 1)],
        INITIAL_CALLS + ['CALL decode ff', 'CALL decode ', 'CALL decode 0102'] + turn_calls(0, 1, 1))

    add('typed-request-dedup', [
        action(request(2, 0, 'step', 'bytes:0a'), expected(2, error='invalid_input')),
        action(request(2, 0, 'step', 'bytes:0A'), expected(2, error='invalid_input')),
        {'raw': encode_request(request(3, 0, 'status', epoch=0), 0).replace(b'\tu64:3\t', b'\tu64:0003\t'),
         'expected': expected(3, fields=status())},
        action(request(3, 0, 'status'), expected(3, fields=status()))], INITIAL_CALLS + ['CALL decode 0a'])
    handle_actions = [action(request(n + 1, 0, 'snapshot'), expected(n + 1, fields=snapshot(n, 0))) for n in range(1, 66)]
    handle_actions += [action(request(67, 0, 'checks', 'u64:1'), expected(67, error='stale_handle')),
                       action(request(68, U64, 'checks', 'u64:65'), expected(68, fields=checks()))]
    add('handle-eviction', handle_actions)
    add('unicode-scalar-identity', [
        action(request(2, 0, 'status', session='\ufeff' + SESSION), expected(2, error='wrong_session')),
        action(request(2, 0, 'status', session=SESSION.replace('é', 'e\u0301')), expected(2, error='wrong_session')),
        action(request(2, 0, 'status'), expected(2, fields=status()))])

    malformed = [(b'\xff', 'malformed'), (b'%', 'malformed'), (b'x' * 8193, 'limit'),
                 (b'\t'.join([b'x'] * 65), 'limit')]
    template = encode_request(request(2, 0, 'status', epoch=1), 1)
    for bad in (b'%FF', b'%C0%AF', b'%ED%A0%80', b'%F4%90%80%80', b'%GG', b'%0'):
        malformed.append((template.replace(quote('string:' + SESSION).encode(), b'string:' + bad), 'malformed'))
    for number in (b'+2', b'-1', b'18446744073709551616', b'1.0', b''):
        malformed.append((template.replace(b'\tu64:2\t', b'\tu64:' + number + b'\t'), 'malformed'))
    malformed.append((template.replace(b'\tu64:2\t', b'\tu64:%2B2\t'), 'malformed'))
    for command, args, error in [('step', ('bytes:0',), 'malformed'), ('step', ('bytes:gg',), 'malformed'),
                                  ('status', ('extra',), 'malformed'), ('snapshot', ('extra',), 'malformed'),
                                  ('cancel', ('extra',), 'malformed'), ('eval', (), 'unsupported')]:
        malformed.append((encode_request(request(2, 0, command, *args, epoch=1), 1), error))
    malformed.append((template.replace(b'u32:1', b'u32:2'), 'unsupported_version'))
    malformed.append((b'\xef\xbb\xbf' + template, 'malformed'))
    malformed.append((template.removesuffix(b'status') + b'%EF%BB%BFstatus', 'unsupported'))
    for index, (raw, error) in enumerate(malformed):
        add(f'malformed-{index:02}', [{'raw': raw, 'expected': expected(0, error=error)}, step(2, 0, 1)],
            INITIAL_CALLS + turn_calls(0, 1, 1))

    fragmented = step(2, 0, 1)
    fragmented['fragmented'] = True
    add('fragmented-valid-frame', [fragmented], INITIAL_CALLS + turn_calls(0, 1, 1))
    add('coalesced-valid-frames', [step(2, 0, 1),
        action(request(3, 1, 'snapshot'), expected(3, 1, snapshot(1, 1))),
        action(request(4, 1, 'checks', 'u64:1'), expected(4, 1, checks(True))),
        action(request(5, 1, 'status'), expected(5, 1, status(1, 'Paused')))],
        INITIAL_CALLS + turn_calls(0, 1, 1), coalesced=True)
    add('failed-write-after-commit', [step(2, 0, 1)], INITIAL_CALLS + turn_calls(0, 1, 1), broken_output=True)
    add('failed-write-stops-next-turn', [step(2, 0, 1), step(3, 1, 1)],
        INITIAL_CALLS + turn_calls(0, 1, 1), broken_output=True, final='FINAL 1 1 1')
    for suffix_name, suffix in [('header-1', b'\0'), ('header-2', b'\0\0'), ('header-3', b'\0\0\0'),
                                ('payload-empty', struct.pack('>I', 12)),
                                ('payload', struct.pack('>I', 12) + b'DDBG'),
                                ('zero', b'\0\0\0\0'), ('oversize', struct.pack('>I', MAX_FRAME + 1)),
                                ('u32-max', b'\xff\xff\xff\xff')]:
        add('partial-' + suffix_name, [], fatal=suffix, fatal_fragment=True)
        add('committed-then-' + suffix_name, [step(2, 0, 1)], INITIAL_CALLS + turn_calls(0, 1, 1), fatal=suffix)
        add('coalesced-step-then-' + suffix_name, [step(2, 0, 1)], INITIAL_CALLS + turn_calls(0, 1, 1), fatal=suffix, coalesced=True)
    add('clean-eof', [])
    return cases


def run_case(host, case, output):
    try:
        return _run_case(host, case, output)
    except BaseException as failure:
        # Launch, callback decoding and even cleanup errors retain a case receipt.
        output.mkdir(exist_ok=True)
        receipt = {'argv': host['argv'] + case['options'], 'cwd': host['cwd'], 'passed': False,
                   'responses': [], 'callbacks': [], 'final': [], 'exit_code': None,
                   'interrupted': isinstance(failure, (KeyboardInterrupt, SystemExit)),
                   'errors': [{'exception': type(failure).__name__, 'message': str(failure), 'traceback': traceback.format_exc()}],
                   'streams': {name: digest((output / name).read_bytes()) for name in
                               ('requested.frames', 'stdin.frames', 'stdout.frames', 'stderr.log') if (output / name).exists()}}
        save(output / 'receipt.json', receipt)
        return receipt


def _run_case(host, case, output):
    output.mkdir()
    argv = host['argv'] + case['options']
    env = dict(host['env'])
    errors, responses = [], []
    tx = (output / 'stdin.frames').open('wb')
    intended = (output / 'requested.frames').open('wb')
    rx = (output / 'stdout.frames').open('wb')
    err = (output / 'stderr.log').open('wb')
    proc = None
    interrupted = False
    epoch = None

    def exact(size):
        data = bytearray()
        deadline = time.monotonic() + 20
        while len(data) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([proc.stdout], [], [], remaining)[0]:
                raise TimeoutError('response timeout')
            chunk = os.read(proc.stdout.fileno(), size - len(data))
            if not chunk:
                raise EOFError('response EOF')
            rx.write(chunk)
            rx.flush()
            data.extend(chunk)
        return bytes(data)

    def receive(wanted):
        nonlocal epoch
        size = struct.unpack('>I', exact(4))[0]
        if not 0 < size <= MAX_FRAME:
            raise AssertionError('response length bound')
        value = decode_response(exact(size))
        actual_epoch = value['envelope'][4]
        if re.fullmatch(r'u64:[0-9]+', actual_epoch) is None or not 0 < int(actual_epoch[4:]) <= U64:
            raise AssertionError('invalid response epoch')
        if epoch is None:
            epoch = int(actual_epoch[4:])
        if actual_epoch != f'u64:{epoch}':
            raise AssertionError('epoch changed within process')
        value['envelope'][4] = 'u64:EPOCH'
        responses.append(value)
        if value != wanted:
            errors.append({'response_index': len(responses) - 1, 'expected': wanted, 'actual': value})

    def write_all(data):
        remaining = memoryview(data)
        deadline = time.monotonic() + 20
        while remaining:
            timeout = deadline - time.monotonic()
            if timeout <= 0 or not select.select([], [proc.stdin], [], timeout)[1]:
                raise TimeoutError('request write timeout')
            try:
                count = os.write(proc.stdin.fileno(), remaining)
            except BlockingIOError:
                continue
            if not count:
                raise OSError('zero-byte pipe write')
            tx.write(remaining[:count])
            tx.flush()
            remaining = remaining[count:]

    def send(payload, wanted, fragment=False):
        wire = struct.pack('>I', len(payload)) + payload
        intended.write(wire)
        intended.flush()
        if fragment:
            # An actual observable gap between partial prefix and the rest.
            for start, end in ((0, 1), (1, 3), (3, 7)):
                write_all(wire[start:end])
                if select.select([proc.stdout], [], [], 0.03)[0]:
                    raise AssertionError('response before complete frame')
            write_all(wire[7:])
        else:
            write_all(wire)
        receive(wanted)

    try:
        proc = subprocess.Popen(argv, cwd=host['cwd'], env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=err, bufsize=0)
        os.set_blocking(proc.stdin.fileno(), False)
        options = case['options']
        initial = status(readonly='--read-only' in options, exact='--exact' in options, cache_one='--cache-one' in options)
        send(encode_request(request(1, 0, 'status', epoch=0), 0), expected(1, fields=initial))
        if case['coalesced'] or case['broken_output']:
            messages = [encode_request(item['request'], epoch) for item in case['actions']]
            wire = b''.join(struct.pack('>I', len(raw)) + raw for raw in messages)
            if case['broken_output']:
                proc.stdout.close()
                proc.stdout = None
            if case['fatal'] is not None:
                wire += case['fatal']
            intended.write(wire)
            intended.flush()
            write_all(wire)
            if not case['broken_output']:
                for item in case['actions']:
                    receive(item['expected'])
        else:
            for item in case['actions']:
                raw = item['raw'] if 'raw' in item else encode_request(item['request'], epoch)
                send(raw, item['expected'], item.get('fragmented', False))
        if case['fatal'] is not None:
            if not case['coalesced']:
                intended.write(case['fatal'])
                intended.flush()
                write_all(case['fatal'])
            header = case['fatal']
            invalid_size = len(header) == 4 and (struct.unpack('>I', header)[0] == 0 or struct.unpack('>I', header)[0] > MAX_FRAME)
            if invalid_size:
                # A forbidden size must fail from its header, while the input
                # pipe is still open. EOF cannot substitute for preallocation
                # length validation.
                proc.wait(timeout=2)
            elif select.select([proc.stdout], [], [], 0.03)[0] or proc.poll() is not None:
                raise AssertionError('partial frame produced a response or ended before EOF')
        proc.stdin.close()
        proc.stdin = None
        remaining, _ = proc.communicate(timeout=20)
        rx.write(remaining or b'')
        if remaining:
            errors.append({'unexpected_trailing_stdout': digest(remaining)})
        expected_nonzero = case['fatal'] is not None or case['broken_output']
        if (proc.returncode != 0) != expected_nonzero:
            errors.append({'exit_code': proc.returncode, 'expected_nonzero': expected_nonzero})
        if proc.returncode < 0:
            errors.append({'signal_termination': -proc.returncode})
    except BaseException as failure:
        interrupted = isinstance(failure, (KeyboardInterrupt, SystemExit))
        errors.append({'exception': type(failure).__name__, 'message': str(failure), 'traceback': traceback.format_exc()})
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
                proc.stdin = None
            try:
                remaining, _ = proc.communicate(timeout=10)
                rx.write(remaining or b'')
            except BaseException as cleanup_error:
                errors.append({'cleanup_error': type(cleanup_error).__name__, 'message': str(cleanup_error)})
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=10)
    finally:
        tx.close()
        intended.close()
        rx.close()
        err.close()
    stderr = (output / 'stderr.log').read_bytes()
    calls = [line.decode('utf-8', errors='strict') for line in stderr.splitlines() if line.startswith(b'CALL ')]
    if calls != case['calls']:
        errors.append({'expected_callbacks': case['calls'], 'actual_callbacks': calls})
    final_lines = [line.decode('utf-8', errors='strict') for line in stderr.splitlines() if line.startswith(b'FINAL ')]
    if final_lines != [case['final']]:
        errors.append({'expected_final': [case['final']], 'actual_final': final_lines})
    receipt = {'argv': argv, 'cwd': host['cwd'], 'exit_code': proc.returncode if proc else None, 'passed': not errors,
               'interrupted': interrupted,
               'responses': responses, 'callbacks': calls, 'final': final_lines, 'errors': errors,
               'streams': {name: digest((output / name).read_bytes()) for name in ('requested.frames', 'stdin.frames', 'stdout.frames', 'stderr.log')}}
    save(output / 'receipt.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hosts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    hosts, before, artifacts_before = {}, None, None
    report = {'passed': False, 'wire_passed': False, 'cases': {}, 'normalization': ['process-local epoch', 'field order'],
              'scope': 'finite same-model direct wire comparison; full optional N12 remains open'}
    try:
        hosts = json.loads(args.hosts.read_text())
        if set(hosts) != {'rust', 'go', 'python', 'typescript', 'swift'}:
            raise ValueError('require all five installed public hosts')
        before = source_snapshot()
        save(args.output / 'source-before.json', before)
        save(args.output / 'hosts.json', hosts)
        artifacts_before = {name: artifact_snapshot(host) for name, host in hosts.items()}
        save(args.output / 'artifacts-before.json', artifacts_before)
        prepared_source = json.loads((args.hosts.parent / 'source-before.json').read_text())
        prepared_artifacts = json.loads((args.hosts.parent / 'artifacts-prepared.json').read_text())
        preparation = json.loads((args.hosts.parent / 'preparation.json').read_text())
        report.update({'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip(),
                       'build_source_matched': before == prepared_source,
                       'prepared_artifacts_matched': artifacts_before == prepared_artifacts,
                       'launch_specification_matched': hosts == preparation.get('launch_specification'),
                       'preparation_passed': preparation['passed']})
        if not all(report[key] for key in ('build_source_matched', 'prepared_artifacts_matched', 'launch_specification_matched')):
            raise RuntimeError('source, installed artifact or launch specification differs from preparation receipt')
        if not all(preparation.get(key, False) for key in ('source_stable', 'copied_source_stable', 'build_input_copies_stable')):
            raise RuntimeError('source was unstable during preparation')
        cases = scenarios()
        total = len(cases) * 5
        completed = 0
        for case in cases:
            case_output = args.output / case['name']
            case_output.mkdir()
            results = {}
            for language in ('rust', 'go', 'python', 'typescript', 'swift'):
                results[language] = run_case(hosts[language], case, case_output / language)
                if results[language].get('interrupted'):
                    raise KeyboardInterrupt('comparison interrupted; case receipt retained')
                completed += 1
                print('KYOTO_PROGRESS ' + json.dumps({'completed': completed, 'total': total, 'phase': 'direct-wire'}), flush=True)
            reference = {k: results['rust'][k] for k in ('responses', 'callbacks', 'final')}
            parity = {language: {k: results[language][k] for k in reference} == reference for language in results if language != 'rust'}
            report['cases'][case['name']] = {'passed': all(r['passed'] for r in results.values()) and all(parity.values()),
                'source_assertions': {name: r['passed'] for name, r in results.items()}, 'direct_rust_comparison': parity}
        report['wire_passed'] = all(case['passed'] for case in report['cases'].values())
        report['passed'] = report['wire_passed'] and report['preparation_passed']
    except BaseException as failure:
        report['exception'] = {'type': type(failure).__name__, 'message': str(failure), 'traceback': traceback.format_exc()}
    finally:
        report['source_stable'] = report['artifacts_stable'] = False
        try:
            after = source_snapshot()
            save(args.output / 'source-after.json', after)
            report['source_stable'] = before == after
            report['changed_source_paths'] = [p for p in sorted((before or {}).keys() | after.keys()) if (before or {}).get(p) != after.get(p)]
        except BaseException as failure:
            report['source_after_exception'] = {'type': type(failure).__name__, 'message': str(failure), 'traceback': traceback.format_exc()}
        try:
            artifacts_after = {name: artifact_snapshot(host) for name, host in hosts.items()}
            save(args.output / 'artifacts-after.json', artifacts_after)
            report['artifacts_stable'] = artifacts_before == artifacts_after
        except BaseException as failure:
            report['artifacts_after_exception'] = {'type': type(failure).__name__, 'message': str(failure), 'traceback': traceback.format_exc()}
        report['wire_passed'] = report['wire_passed'] and report['source_stable'] and report['artifacts_stable']
        report['passed'] = report['passed'] and report['source_stable'] and report['artifacts_stable']
        save(args.output / 'qualification.json', report)
    print(json.dumps({'passed': report['passed'], 'cases': len(report['cases']),
                      'failed_cases': [name for name, case in report['cases'].items() if not case['passed']],
                      'source_stable': report['source_stable'], 'artifacts_stable': report['artifacts_stable']}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    def interrupted(signum, _frame):
        raise KeyboardInterrupt(f'comparison interrupted by signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(main())

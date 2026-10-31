"""Convert the Rust binary trace to JSON v1 plus a bound configuration sidecar.

JSON v1 has no RunConfig field. The sibling ``.run-config.json`` file retains
the binary strategy, optional seed and ordered parameters; its SHA-256 references
the exact JSON bytes. This is a one-way association, not authentication: two
recordings with identical JSON but different configurations can swap sidecars.
An independently trusted receipt must bind the hashes of both artifacts.
JSON-only producers do not provide configuration and cannot be converted back to
binary without it. No default configuration is invented for them.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from statelessness.core import TraceLimits
from statelessness.trace_binary import AuditTrace, BinaryLimits, RunConfig, audit_from_bytes, audit_to_bytes
from statelessness.trace_json import array, dumps, loads, object_fields, strict_loads, string

_SIDECAR_FORMAT = 'stateful-run-config-v1'
# A binary configuration frame may approach 8 MiB; ASCII JSON escaping can
# expand non-ASCII text. This cap still bounds parsing before decoding.
_MAX_SIDECAR_BYTES = 64 * 1024 * 1024


def sidecar_path(path):
    return Path(str(path) + '.run-config.json')


def write_envelope(path, audit):
    portable, config = audit.to_portable()
    raw = dumps(portable).encode('utf-8')
    data = {
        'format': _SIDECAR_FORMAT,
        'trace_sha256': hashlib.sha256(raw).hexdigest(),
        'strategy': config.strategy,
        'seed': None if config.seed is None else str(config.seed),
        'parameters': [list(pair) for pair in config.parameters],
    }
    encoded = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')
    if len(encoded) > _MAX_SIDECAR_BYTES:
        raise ValueError('run configuration sidecar too large')
    # Validate both formats and their binary resource limits before publishing.
    audit_to_bytes(AuditTrace.from_portable(portable, config))
    # Never leave a stale sidecar attached to a newly written trace, including
    # when identical observations were recorded with a different configuration.
    sidecar_path(path).unlink(missing_ok=True)
    Path(path).write_bytes(raw)
    sidecar_path(path).write_bytes(encoded)


def read_envelope(path):
    with Path(path).open('rb') as source:
        raw = source.read(TraceLimits().max_json_bytes + 1)
    portable = loads(raw)
    with sidecar_path(path).open('rb') as source:
        encoded = source.read(_MAX_SIDECAR_BYTES + 1)
    obj = object_fields(strict_loads(encoded, _MAX_SIDECAR_BYTES),
                        {'format', 'trace_sha256', 'strategy', 'seed', 'parameters'})
    if obj['format'] != _SIDECAR_FORMAT or obj['trace_sha256'] != hashlib.sha256(raw).hexdigest():
        raise ValueError('run configuration sidecar does not match trace')
    seed = obj['seed']
    if seed is not None:
        if type(seed) is not str or re.fullmatch(r'0|[1-9][0-9]{0,19}', seed) is None:
            raise ValueError('invalid run configuration seed')
        seed = int(seed)
    pairs = []
    for pair in array(obj['parameters'], 1024):
        if type(pair) is not list or len(pair) != 2:
            raise ValueError('invalid run configuration parameter')
        pairs.append((string(pair[0]), string(pair[1])))
    config = RunConfig(string(obj['strategy']), seed, tuple(pairs))
    audit = AuditTrace.from_portable(portable, config)
    # Encoder checks u64, UTF-8, aggregate items and binary byte limits.
    audit_to_bytes(audit)
    return audit


def main(mode, path):
    if mode == 'from-binary':
        limit = BinaryLimits().max_total_bytes
        with Path(path + '.audit.bin').open('rb') as source:
            raw = source.read(limit + 1)
        if len(raw) > limit: raise ValueError('binary trace too large')
        audit = audit_from_bytes(raw)
        write_envelope(path, audit)
        if read_envelope(path) != audit: raise ValueError('audit conversion changed trace or configuration')
        return
    binary = os.environ['STATEFUL_RUST_BINARY']
    with tempfile.TemporaryDirectory() as temporary:
        raw = Path(temporary) / 'trace.bin'
        if mode == 'record':
            subprocess.run([binary, 'record', str(raw)], check=True)
            write_envelope(path, audit_from_bytes(raw.read_bytes()))
        elif mode in ('replay', 'compare-cross-build'):
            raw.write_bytes(audit_to_bytes(read_envelope(path)))
            subprocess.run([binary, mode, str(raw)], check=True)
        else:
            raise ValueError('unknown mode')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('usage: bridge.py record|replay|from-binary TRACE.json')
    main(*sys.argv[1:])

#!/usr/bin/env python3
"""Record caller-attested source checks bracketing a direct Kyoto N06 Rust build.

This detects persistent drift across the build window. It cannot detect a change
that is reverted between checks, authenticate the clock or Kyoto service, or prove
that the executable was not replaced after Cargo finished.
"""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import time

KYOTO = '/Volumes/seed1/Projects/kyoto/kyoto.py'
MANIFEST = 'conformance/fixtures/native-campaign-rust/Cargo.toml'


def finite_time(value):
    return type(value) in (int, float) and math.isfinite(value)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def verify(snapshot):
    script = snapshot / 'source/scripts/prepare-n06-snapshot.py'
    spec = importlib.util.spec_from_file_location('n06_snapshot_verify', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.verify(snapshot)
    return digest(snapshot / 'source-manifest.json')


def create(snapshot, checkpoint):
    snapshot = snapshot.resolve(strict=True)
    manifest = verify(snapshot)
    data = {'schema': 'n06-build-window-before/1', 'snapshot': str(snapshot),
            'manifest_sha256': manifest, 'helper_sha256': digest(Path(__file__).resolve()),
            'verified_at': time.time()}
    with checkpoint.open('x') as f:
        json.dump(data, f, sort_keys=True, indent=2)
        f.write('\n')
    return data


def finish(checkpoint, job_id, output):
    before = json.loads(checkpoint.read_text())
    if before.get('schema') != 'n06-build-window-before/1' or not finite_time(before.get('verified_at')):
        raise ValueError('invalid checkpoint schema')
    if before.get('helper_sha256') != digest(Path(__file__).resolve()):
        raise ValueError('helper changed since checkpoint')
    snapshot = Path(before['snapshot']).resolve(strict=True)
    source = (snapshot / 'source').resolve(strict=True)
    job = json.loads(subprocess.check_output([sys.executable, KYOTO, 'show', job_id]))
    requested = job.get('requested_command', [])
    if (len(requested) != 8 or requested[:5] != ['cargo', 'build', '--manifest-path', MANIFEST, '--target-dir']
            or requested[6:] != ['--bin', 'native-campaign-public'] or job.get('command') != requested
            or job.get('status') != 'succeeded' or job.get('exit_code') != 0
            or Path(job.get('cwd', '')).resolve() != source
            or not finite_time(job.get('submitted')) or job['submitted'] < before['verified_at']
            or not finite_time(job.get('finished')) or job['finished'] < job['submitted']):
        raise ValueError('job does not match a subsequent successful direct build')
    if verify(snapshot) != before['manifest_sha256']:
        raise ValueError('snapshot manifest changed')
    verified_after = time.time()
    if verified_after < job['finished']:
        raise ValueError('post-build verification predates job completion')
    if not Path(requested[5]).is_absolute():
        raise ValueError('target must be absolute')
    target = Path(requested[5]).resolve(strict=True)
    if target == snapshot or target.is_relative_to(snapshot):
        raise ValueError('target must be outside snapshot')
    binary = (target / 'debug/native-campaign-public').resolve(strict=True)
    if not binary.is_relative_to(target) or not binary.is_file():
        raise ValueError('missing Rust executable')
    log = Path(job['log']).resolve(strict=True)
    result = {'schema': 'n06-build-window/1', 'snapshot': str(snapshot),
              'manifest_sha256': before['manifest_sha256'], 'checkpoint_sha256': digest(checkpoint),
              'verified_before': before['verified_at'], 'verified_after': verified_after,
              'kyoto_job': job_id, 'requested_command': requested, 'submitted': job['submitted'],
              'finished': job['finished'], 'log_sha256': digest(log),
              'binary': str(binary), 'binary_sha256': digest(binary),
              'scope': 'caller-attested bracket; transient source drift and post-build artifact replacement are not excluded'}
    with output.open('x') as f:
        json.dump(result, f, sort_keys=True, indent=2)
        f.write('\n')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='mode', required=True)
    b = sub.add_parser('before'); b.add_argument('--snapshot', type=Path, required=True); b.add_argument('--checkpoint', type=Path, required=True)
    a = sub.add_parser('after'); a.add_argument('--checkpoint', type=Path, required=True); a.add_argument('--job', required=True); a.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = create(args.snapshot, args.checkpoint) if args.mode == 'before' else finish(args.checkpoint, args.job, args.output)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()

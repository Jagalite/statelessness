#!/usr/bin/env python3
"""Run the internal Swift campaign self-model from an external staged snapshot.

Submit this script to Kyoto. This exercises the package's actual internal core;
it is not an external public-API or installed-release qualification.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile


def digest_tree(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise RuntimeError(f'symlink in staged source: {path}')
        if path.is_file():
            result[str(path.relative_to(root))] = (hashlib.sha256(path.read_bytes()).hexdigest(),
                                                   stat.S_IMODE(path.stat().st_mode))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path, help='prepared N06 snapshot directory')
    args = parser.parse_args()
    snapshot = args.snapshot.resolve(strict=True)
    source = snapshot / 'source'
    verify = source / 'scripts/prepare-n06-snapshot.py'
    subprocess.run(['python3', str(verify), '--verify', str(snapshot)], check=True)
    try:
      with tempfile.TemporaryDirectory(prefix='n06-swift-staged-') as temp:
        root = Path(temp) / 'package'
        root.mkdir()
        shutil.copy2(source / 'Package.swift', root / 'Package.swift')
        shutil.copytree(source / 'swift/Sources', root / 'swift/Sources')
        shutil.copytree(source / 'swift/Tests', root / 'swift/Tests')
        before = digest_tree(root)
        expected = {'Package.swift': (hashlib.sha256((source / 'Package.swift').read_bytes()).hexdigest(),
                                     stat.S_IMODE((source / 'Package.swift').stat().st_mode))}
        for subtree in ('swift/Sources', 'swift/Tests'):
            expected.update({f'{subtree}/{key}': value for key, value in
                             digest_tree(source / subtree).items()})
        if before != expected:
            raise RuntimeError('staged Swift source differs from snapshot')
        print(f'staged_files={len(before)}', flush=True)
        env = os.environ.copy()
        env.pop('SWIFT_EXEC', None)
        run = subprocess.run(['swift', 'test', '--package-path', str(root), '--scratch-path',
                              str(Path(temp) / 'scratch'), '--filter', 'CampaignLifecycleTests'],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, check=False)
        print(run.stdout, end='', flush=True)
        required = ('testFiniteProtocolGraphAndFixedSeedFuzzUseIndependentLedger',
                    'testDuplicateDeliveryBFSFuzzShrinkRecordReadReplayAndHealthyControls',
                    'testHealthyBinaryTraceRoundtripAndReplay')
        if run.returncode or any(not re.search(r"Test Case '[^\n]*[. ]" + re.escape(name)
                                               + r"\]?' passed", run.stdout) for name in required):
            raise RuntimeError('Swift self-model tests did not all execute and pass')
        if digest_tree(root) != before:
            raise RuntimeError('staged Swift source changed during test')
    finally:
        # Preserve an original test exception while still checking snapshot drift.
        import sys
        active = sys.exc_info()[0] is not None
        verification = subprocess.run(['python3', str(verify), '--verify', str(snapshot)])
        if verification.returncode:
            if active:
                print('post-run snapshot verification failed', file=sys.stderr, flush=True)
            else:
                raise RuntimeError('post-run snapshot verification failed')
    print('staged internal Swift self-model complete', flush=True)


if __name__ == '__main__':
    main()

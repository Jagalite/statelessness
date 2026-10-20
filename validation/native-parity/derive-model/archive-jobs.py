#!/usr/bin/env python3
"""Archive Kyoto metadata and lossless terminal logs; never submit/cancel jobs."""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
KYOTO = '/Volumes/seed1/Projects/kyoto/kyoto.py'

def receipt(path):
    data = path.read_bytes()
    return {'path': str(path.relative_to(ROOT)), 'size_bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest()}

def main():
    out = ROOT / 'jobs'
    out.mkdir(exist_ok=True)
    rows = []
    for job in sys.argv[1:]:
        raw = subprocess.check_output([sys.executable, KYOTO, 'show', job])
        state = json.loads(raw)
        dest = out / (job + '.' + state['status'] + '.' + str(time.time_ns()) + '.json')
        dest.write_bytes(raw)
        row = {'job_id': job, 'status': state['status'], 'receipt': receipt(dest)}
        log = Path(state['log'])
        if state['status'] in ('succeeded', 'failed', 'cancelled') and log.exists():
            data = log.read_bytes()
            packed = gzip.compress(data, mtime=0)
            if gzip.decompress(packed) != data:
                raise RuntimeError('lossless log archive verification failed')
            stored = out / (job + '.log.gz')
            if stored.exists() and stored.read_bytes() != packed:
                raise RuntimeError('terminal log changed; preserve prior archive')
            stored.write_bytes(packed)
            row['log'] = {'original_path': str(log), 'original_size_bytes': len(data),
                          'original_sha256': hashlib.sha256(data).hexdigest(),
                          'stored': receipt(stored), 'compression': 'gzip, mtime=0, lossless verified'}
        rows.append(row)
    ledger = out / ('ledger.' + str(time.time_ns()) + '.json')
    ledger.write_text(json.dumps(rows, indent=2) + '\n')
    print(json.dumps([{'job_id': r['job_id'], 'status': r['status']} for r in rows]))

if __name__ == '__main__':
    main()

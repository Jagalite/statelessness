#!/usr/bin/env python3
"""Metadata-only snapshot preparation; this never builds or runs project code."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone


CHECKOUT = Path('/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336')
BASE = '0a26bf5a068e5948d82a3d8bd9eb4063ae85115f'
DESTINATION = CHECKOUT / 'validation/native-parity/n06/typescript/snapshots/focused-r1'
TREES = ('typescript', 'examples/native-campaign/typescript')
EXCLUDED = ('typescript/node_modules', 'typescript/dist')
FILES = ('docs/native-parity/N06-TYPESCRIPT.md',
         'validation/native-parity/n06/typescript/qualify.py',
         'validation/native-parity/n06/typescript/freeze.py',
         'src/campaign.rs', 'src/campaign/lifecycle.rs',
         'src/campaign/lifecycle/codec.rs', 'src/campaign/lifecycle/tests.rs')


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selected() -> list[Path]:
    paths = {CHECKOUT / name for name in FILES}
    for name in TREES:
        root = CHECKOUT / name
        paths.add(root)
        for path in root.rglob('*'):
            relative = path.relative_to(CHECKOUT).as_posix()
            if any(relative == item or relative.startswith(item + '/') for item in EXCLUDED):
                continue
            paths.add(path)
    # Baseline package tests consume top-level conformance JSON/TSV inputs.
    paths.update(path for path in (CHECKOUT / 'conformance').iterdir() if path.suffix in ('.json', '.tsv'))
    for path in list(paths):
        for parent in path.parents:
            if parent == CHECKOUT:
                break
            paths.add(parent)
    return sorted(paths)


def identify(path: Path, root: Path) -> dict:
    info = path.lstat()
    entry = {'path': path.relative_to(root).as_posix(), 'mode': info.st_mode & 0o777}
    if stat.S_ISDIR(info.st_mode):
        entry['type'] = 'directory'
    elif stat.S_ISREG(info.st_mode):
        entry.update(type='file', size=info.st_size, sha256=digest(path))
    else:
        raise RuntimeError(f'nonregular source input: {path}')
    return entry


def main() -> None:
    if Path.cwd().resolve() != CHECKOUT:
        raise RuntimeError('run from the authorized isolated checkout')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    branch = subprocess.check_output(['git', 'branch', '--show-current'], text=True).strip()
    if head != BASE or branch != 't3/native-campaign-runtime':
        raise RuntimeError('checkout identity differs from the authorized base/branch')
    if DESTINATION.exists():
        raise RuntimeError('snapshot already exists; never replace an identity')
    origin = [identify(path, CHECKOUT) for path in selected()]
    source = DESTINATION / 'source'
    source.mkdir(parents=True)
    for entry in origin:
        target = source / entry['path']
        if entry['type'] == 'directory':
            target.mkdir(exist_ok=True)
        else:
            shutil.copyfile(CHECKOUT / entry['path'], target)
            if digest(target) != entry['sha256']:
                raise RuntimeError(f'copy changed: {entry["path"]}')
            target.chmod(0o555 if entry['mode'] & 0o111 else 0o444)
    if origin != [identify(path, CHECKOUT) for path in selected()]:
        raise RuntimeError('live source membership/type/mode/hash changed while freezing')
    for path in sorted(source.rglob('*'), reverse=True):
        if path.is_dir():
            path.chmod(0o555)
    source.chmod(0o555)
    manifest = {
        'format': 'n06-typescript-focused-source-v1', 'checkout': str(CHECKOUT),
        'head': head, 'branch': branch, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'selection': {'trees': TREES, 'excluded_generated_directories': EXCLUDED,
                      'files': FILES, 'conformance': 'top-level *.json and *.tsv'},
        'origin_entries': origin,
        'entries': [identify(path, source) for path in sorted(source.rglob('*'))],
        'freeze_command': [sys.executable, '-B', str(CHECKOUT / FILES[2])],
        'scope': 'source-ready focused candidate; no execution, Rust comparison, browser or N05 closure',
    }
    manifest_path = DESTINATION / 'source-manifest.json'
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    manifest_path.chmod(0o444)
    receipt = {'source': str(source), 'manifest': str(manifest_path),
               'manifest_sha256': digest(manifest_path), 'files': sum(e['type'] == 'file' for e in origin)}
    (DESTINATION / 'freeze-receipt.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()

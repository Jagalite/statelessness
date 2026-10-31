#!/usr/bin/env python3
"""Copy selected N06 source inputs outside the checkout with a verifiable manifest.

This prepares selected inputs only; generated directories are excluded. It does
not make the copy immutable, build packages, or establish executable provenance.
Submit each build directly to Kyoto, preserve its job ID and logs, and verify the
snapshot before and after use. Git status is contextual, not an atomic snapshot.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TREES = ('src', 'crates', 'tests', 'python', 'go', 'typescript', 'swift',
         'examples', 'benches', 'bindings', 'spec', 'conformance', 'docs/requirements',
         'validation/native-parity/n06/current-corpus', 'validation/native-parity/n06/python',
         'validation/native-parity/n06/go', 'validation/native-parity/n06/typescript',
         'validation/native-parity/n06/swift')
FILES = ('Cargo.toml', 'Cargo.lock', 'build.rs', 'Package.swift',
         'README.md', 'LICENSE',
         'docs/native-parity/N06-RUST-CONTRACT.md', 'scripts/qualify-n06-matrix.py',
         'scripts/prepare-n06-snapshot.py', 'scripts/test_prepare_n06_snapshot.py',
         'scripts/run-n06-snapshot.py', 'scripts/test_n06_backend_pin.py',
         'scripts/n06-build-window.py', 'scripts/test_n06_build_window.py',
         'scripts/test_n06_build_window_integration.py',
         'scripts/run-n06-fault-controls.py', 'scripts/test_n06_fault_controls.py')
IGNORED = {'node_modules', 'dist', '__pycache__', '.build', 'target', 'build', '.git', '.venv'}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def selected(root):
    result = {}
    for name in TREES:
        tree = root / name
        if not tree.is_dir() or tree.is_symlink():
            raise ValueError('missing or symlinked source tree: ' + name)
        for directory, dirs, files in os.walk(tree, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED and not d.endswith('.egg-info'))
            for d in dirs:
                if (Path(directory) / d).is_symlink():
                    raise ValueError('symlinked source directory: ' + str(Path(directory) / d))
            for filename in sorted(files):
                p = Path(directory) / filename
                if p.is_symlink() or not p.is_file():
                    raise ValueError('non-regular source: ' + str(p))
                result[p.relative_to(root).as_posix()] = {
                    'sha256': digest(p), 'mode': stat.S_IMODE(p.stat().st_mode)}
    for name in FILES:
        p = root / name
        if p.is_symlink() or not p.is_file():
            raise ValueError('missing or non-regular source: ' + name)
        result[name] = {'sha256': digest(p), 'mode': stat.S_IMODE(p.stat().st_mode)}
    return dict(sorted(result.items()))


def freeze(root, output):
    root = root.resolve(strict=True)
    output = output.resolve()
    if output == root or root in output.parents or output.exists():
        raise ValueError('output must be a new directory outside the source')
    before = selected(root)
    output.mkdir(parents=True)
    snapshot = output / 'source'
    snapshot.mkdir()
    for name, expected in before.items():
        src, dst = root / name, snapshot / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst, follow_symlinks=False)
        if digest(dst) != expected['sha256']:
            raise ValueError('source changed while copying: ' + name)
        os.chmod(dst, expected['mode'])
    if selected(root) != before or selected(snapshot) != before:
        raise ValueError('source file set or content changed while copying')
    head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    status = subprocess.check_output(['git', '-C', str(root), 'status', '--short', '--untracked-files=normal'], text=True)
    manifest = {'schema': 'n06-source-snapshot/1', 'source_root': str(root),
                'head': head, 'git_status': status, 'files': before,
                'scope': 'selected source copy; source-to-binary linkage is not proven'}
    encoded = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    (output / 'source-manifest.json').write_bytes(encoded)
    (output / 'source-manifest.sha256').write_text(hashlib.sha256(encoded).hexdigest() + '  source-manifest.json\n')
    return manifest


def verify(directory):
    directory = directory.resolve(strict=True)
    encoded = (directory / 'source-manifest.json').read_bytes()
    expected = (directory / 'source-manifest.sha256').read_text().split()
    if expected != [hashlib.sha256(encoded).hexdigest(), 'source-manifest.json']:
        raise ValueError('manifest checksum mismatch')
    manifest = json.loads(encoded)
    if manifest.get('schema') != 'n06-source-snapshot/1' or selected(directory / 'source') != manifest.get('files'):
        raise ValueError('snapshot file set or content differs from manifest')
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--output', type=Path)
    mode.add_argument('--verify', type=Path, help='verify a prepared snapshot directory')
    args = p.parse_args()
    if args.verify:
        directory = args.verify.resolve(strict=True)
        manifest = verify(directory)
        print(json.dumps({'verified': str(directory / 'source'), 'files': len(manifest['files'])}))
        return
    result = freeze(ROOT, args.output)
    print(json.dumps({'snapshot': str(args.output.resolve() / 'source'),
                      'manifest': str(args.output.resolve() / 'source-manifest.json'),
                      'files': len(result['files']), 'head': result['head']}))


if __name__ == '__main__':
    main()

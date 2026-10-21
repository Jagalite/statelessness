#!/usr/bin/env python3
"""Capture byte, membership and type identity without following source symlinks."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


def capture(root, names):
    rows = {}

    def visit(relative):
        path = root / relative
        before = path.lstat()
        if stat.S_ISDIR(before.st_mode):
            rows[str(relative)] = {"type": "directory"}
            for name in sorted(os.listdir(path)):
                visit(relative / name)
            after = path.lstat()
        elif stat.S_ISREG(before.st_mode):
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                opened = os.fstat(fd)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise RuntimeError(f"identity changed before read: {path}")
                digest = hashlib.sha256()
                size = 0
                while True:
                    chunk = os.read(fd, 1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    digest.update(chunk)
                after = os.fstat(fd)
            finally:
                os.close(fd)
            rows[str(relative)] = {"type": "file", "bytes": size, "sha256": digest.hexdigest()}
        elif stat.S_ISLNK(before.st_mode):
            rows[str(relative)] = {"type": "symlink", "target": os.readlink(path)}
            after = path.lstat()
        else:
            raise RuntimeError(f"unsupported source type: {path}")
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(after):
            raise RuntimeError(f"source changed while reading: {path}")

    for name in names:
        visit(Path(name))
    return rows


root, output, *names = sys.argv[1:]
root = Path(root)
first = capture(root, names)
second = capture(root, names)
if first != second:
    raise RuntimeError("source membership/type/hash changed during capture")
payload = (json.dumps(first, indent=2, sort_keys=True) + "\n").encode()
Path(output).write_bytes(payload)
print("source identity", output, "entries", len(first), "bytes", len(payload), "sha256", hashlib.sha256(payload).hexdigest())

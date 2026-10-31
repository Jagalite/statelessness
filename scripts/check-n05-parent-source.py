#!/usr/bin/env python3
"""Read-only parent runtime identity guard; execute through the shared Kyoto queue.

This records a compact immutable inventory, including membership and file types.
It never writes to the inspected parent. Repeated identical inventories reuse one
content-addressed object in the caller-selected evidence directory.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import stat
import subprocess

AUTHORITY = "a4cc11eea8db62115b452eee3c6610c04fa06b96"
ROOTS = ("src", "crates", "go", "python/src", "typescript/src", "swift/Sources")
FILES = ("Cargo.toml", "Cargo.lock", "build.rs", "Package.swift", "go/go.mod",
         "python/pyproject.toml", "typescript/package.json", "typescript/package-lock.json",
         "docs/requirements/controller-properties.txt", "docs/requirements/controller.json")
GENERATED = {".git", "target", "node_modules", "dist", "__pycache__", ".build",
             ".swiftpm", "receipts"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    root, out = args.parent.resolve(), args.output.resolve()
    if out == root or out.is_relative_to(root):
        raise ValueError("evidence must be outside inspected parent")
    out.mkdir(parents=True, exist_ok=True)
    result_path = out / (args.label + ".json")
    if result_path.exists():
        raise ValueError("preserve prior observation; use a fresh label")
    tree = subprocess.check_output(["git", "ls-tree", "-rz", AUTHORITY, "--",
                                    *ROOTS, *FILES], cwd=root)
    expected = {}
    for entry in tree.split(b"\0"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.decode().split()
        expected[raw_path.decode()] = (mode, kind, object_id)
    paths = set()

    def visit(path):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink() or not path.is_dir():
            paths.add(rel)
            return
        for child in path.iterdir():
            if child.name in GENERATED or child.name.endswith(".egg-info"):
                continue
            visit(child)

    for name in ROOTS + FILES:
        visit(root / name)
    rows, mismatches = {}, []
    for rel in sorted(paths | expected.keys()):
        path = root / rel
        if not path.exists() and not path.is_symlink():
            rows[rel] = ["missing"]
            mismatches.append(rel)
            continue
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            rows[rel] = ["unsupported_type", stat.S_IFMT(info.st_mode)]
            mismatches.append(rel)
            continue
        data = path.read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        mode = "100755" if info.st_mode & 0o111 else "100644"
        rows[rel] = ["file", mode, len(data), hashlib.sha256(data).hexdigest()]
        if expected.get(rel) != (mode, "blob", blob):
            mismatches.append(rel)
    inventory = canonical({"schema": "n05-parent-runtime/1", "authority": AUTHORITY,
                           "roots": ROOTS, "files": FILES, "entries": rows})
    identity = hashlib.sha256(inventory).hexdigest()
    objects = out / "objects"
    objects.mkdir(exist_ok=True)
    object_path = objects / (identity + ".json")
    if object_path.exists():
        if object_path.read_bytes() != inventory:
            raise RuntimeError("immutable source identity changed")
    else:
        with object_path.open("xb") as stream:
            stream.write(inventory)
    result = {"schema": "n05-parent-observation/1", "parent": str(root),
              "observed_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "authority": AUTHORITY, "inventory_sha256": identity,
              "guard_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "files": len(rows), "mismatches": mismatches,
              "status": "passed" if not mismatches else "failed"}
    with result_path.open("xb") as stream:
        stream.write(canonical(result) + b"\n")
    print(json.dumps(result))
    return bool(mismatches)


if __name__ == "__main__":
    raise SystemExit(main())

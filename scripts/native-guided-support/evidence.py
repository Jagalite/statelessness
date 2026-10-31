"""Lossless, append-only process evidence and content-addressed tree identities."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import time


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def forbidden(value):
        raise ValueError(f"non-integer JSON number: {value}")

    return json.loads(data, object_pairs_hook=pairs, parse_float=forbidden,
                      parse_constant=forbidden)


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    """Exclusive creation: an old receipt is never silently replaced."""
    with Path(path).open("xb") as stream:
        stream.write(canonical(value) + b"\n")


class Objects:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, value):
        raw = canonical(value)
        key = digest(raw)
        path = self.root / (key + ".json")
        if path.exists():
            if path.read_bytes() != raw:
                raise RuntimeError(f"content-addressed object changed: {path}")
        else:
            with path.open("xb") as stream:
                stream.write(raw)
        return key

    def get(self, key):
        raw = (self.root / (key + ".json")).read_bytes()
        if digest(raw) != key:
            raise RuntimeError(f"manifest object hash mismatch: {key}")
        return json.loads(raw)


# Only generated directories are excluded. Files named e.g. build.rs remain.
GENERATED = {".git", "target", ".build", ".swiftpm", "node_modules", "dist",
             "__pycache__", ".pytest_cache", "build", "receipts"}


def tree(root, selections=(".",), *, source=False, exclude_dirs=()):
    """Record membership, lstat type/mode, and bytes without following symlinks.

    Every directory is present, so empty directory additions/removals also drift.
    Symlinks are represented faithfully; source staging rejects them explicitly.
    """
    root = Path(root)
    entries = {}

    def visit(path):
        rel = path.relative_to(root).as_posix()
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            target = os.readlink(path)
            row = ["symlink", mode, digest(os.fsencode(target)), target]
        elif stat.S_ISREG(info.st_mode):
            row = ["file", mode, file_hash(path), info.st_size]
        elif stat.S_ISDIR(info.st_mode):
            row = ["directory", mode]
        else:
            raise RuntimeError(f"unsupported filesystem type: {path}")
        entries[rel] = row
        if row[0] == "directory":
            for child in sorted(path.iterdir()):
                if child.is_dir() and not child.is_symlink() and child.name in exclude_dirs:
                    continue
                if source and child.is_dir() and not child.is_symlink() and (
                        child.name in GENERATED or child.name.endswith(".egg-info")):
                    continue
                if source and child.name in {".DS_Store"}:
                    continue
                visit(child)

    for selection in selections:
        visit(root / selection)
    return {"schema": "n05-tree/1", "entries": entries}


def copy_manifest(source, destination, manifest):
    destination.mkdir(parents=True, exist_ok=False)
    for rel, entry in sorted(manifest["entries"].items(), key=lambda item: (item[0].count("/"), item[0])):
        src, dst = source / rel, destination / rel
        if entry[0] == "directory":
            dst.mkdir(parents=True, exist_ok=True)
        elif entry[0] == "file":
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        else:
            raise RuntimeError(f"source symlink is not an immutable package input: {src}")
        dst.chmod(entry[1])


def readonly(root):
    # Do files first, directories last; never chmod a venv symlink's host target.
    paths = sorted(Path(root).rglob("*"), key=lambda p: len(p.parts), reverse=True)
    for path in paths + [Path(root)]:
        if not path.is_symlink():
            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)


def writable_copy(source, destination):
    shutil.copytree(source, destination)
    for path in [destination, *destination.rglob("*")]:
        if not path.is_symlink():
            path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o200)


def clean_environment(work):
    """Do not inherit package injection, compiler flags, preload hooks or secrets."""
    env = {key: os.environ[key] for key in
           ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "SystemRoot") if key in os.environ}
    env.update(LANG="C.UTF-8", LC_ALL="C.UTF-8", PYTHONNOUSERSITE="1",
               PYTHONDONTWRITEBYTECODE="1", PYTHONPATH="", NODE_PATH="",
               GOPROXY="off", GOTOOLCHAIN="local", GOWORK="off", CGO_ENABLED="0",
               GOCACHE=str(work / "cache/go"), CARGO_INCREMENTAL="0",
               npm_config_userconfig=str(work / "empty.npmrc"),
               PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1")
    return env


class Recorder:
    def __init__(self, out, work, phase, env=None):
        self.out, self.work, self.phase = Path(out), Path(work), phase
        self.directory = self.out / "commands" / phase
        self.directory.mkdir(parents=True, exist_ok=False)
        self.env = clean_environment(self.work) if env is None else env
        self.count = 0

    def run(self, label, argv, cwd=None, *, stdin=b"", timeout=900, check=True):
        self.count += 1
        directory = self.directory / f"{self.count:05d}-{label}"
        directory.mkdir()
        argv = list(map(str, argv))
        cwd = Path(cwd or self.work).resolve()
        (directory / "stdin.bin").write_bytes(stdin)
        record = dict(argv=argv, cwd=str(cwd), environment=self.env,
                      timeout_seconds=timeout, started_unix_ns=time.time_ns(),
                      status="started", peak_rss_bytes=None,
                      peak_rss_scope="unavailable; no process-tree RSS claim or hard RSS cap")
        write_json(directory / "request.json", record)
        print(f"[{self.phase}/{label}] {json.dumps(argv)}", flush=True)
        started = time.monotonic_ns()
        process = None
        error = None
        try:
            with (directory / "stdin.bin").open("rb") as source, \
                    (directory / "stdout.bin").open("xb") as output, \
                    (directory / "stderr.bin").open("xb") as diagnostics:
                process = subprocess.Popen(argv, cwd=cwd, env=self.env, stdin=source,
                                           stdout=output, stderr=diagnostics,
                                           start_new_session=True)
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise RuntimeError(f"{label} timed out after {timeout}s")
            if check and process.returncode != 0:
                raise RuntimeError(f"{label} exited {process.returncode}; raw logs: {directory}")
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        finally:
            result = dict(exit_code=None if process is None else process.returncode,
                          wall_nanoseconds=time.monotonic_ns() - started,
                          finished_unix_ns=time.time_ns(), error=error,
                          streams={p.name: {"sha256": file_hash(p), "bytes": p.stat().st_size}
                                   for p in directory.glob("*.bin")})
            write_json(directory / "result.json", result)
        return (directory / "stdout.bin").read_bytes()

    def text(self, label, argv, cwd=None, **kwargs):
        return self.run(label, argv, cwd, **kwargs).decode("utf-8", "strict").strip()


def require_equal(actual, wanted, context):
    if canonical(actual) != canonical(wanted):
        raise RuntimeError(f"{context} differs; compare the preserved JSON and raw logs")

#!/usr/bin/env python3
"""Build fresh N06 wheel/sdist consumers. Submit this script through Kyoto only.

This standalone stdlib driver never imports statelessness. It copies package
inputs before building, uses separate builder/runtime virtual environments, and
preserves artifacts and logs on every failure. Expected rows are optional, must
be independently sealed when provided, and are never passed to the consumer.
No N05 jobs, corpus, runtime tests, or sources are changed by this driver.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY_COMMIT = "0a26bf5a068e5948d82a3d8bd9eb4063ae85115f"
EXAMPLE = "examples/native-campaign/python/consumer.py"
REFERENCE = "conformance/fixtures/native-campaign-public.rs"
AUTHOR = "conformance/fixtures/native-campaign-golden.py"
CONTRACT = "docs/native-parity/N06-RUST-CONTRACT.md"
# Scenario membership is independently transcribed from the Rust public fixture;
# expected values are never imported from the candidate consumer.
PUBLIC_CASES = tuple(f"seed-{i:02}" for i in range(15)) + (
    "jobs-zero", "workers-zero", "workers-257", "buffer-1025", "workers-256",
    "buffer-1024", "max-id", "id-overflow", "mutation-101", "mutation-100",
    "aggregate-cases", "aggregate-transitions", "wide-product-capped",
    "max-job-count-valid", "validation-first-error",
    "ordinary-basic", "guided-basic", "factory-error", "feedback-error",
    "factory-cancelled", "pre-cancelled", "deadline-zero", "cases-zero",
    "steps-zero", "transitions-zero", "initial-error", "step-error",
    "check-error", "metadata-panic", "feedback-metadata-panic", "drop-panic",
    "sink-error", "sink-panic", "property-stop",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class InventoryError(RuntimeError):
    def __init__(self, entries, errors, interruption=None):
        super().__init__("; ".join(errors))
        self.entries, self.errors = entries, errors
        self.interruption = interruption


def inventory(directory, *, allow_missing=False):
    """Full descriptor-relative inventory; reject links and detect replacement."""
    result, errors = {}, []
    interruption = None
    # Reject traversal before abspath can erase a symlink/.. spelling. In
    # particular, the interpreter's original __file__ must not be rewritten
    # into a different lexical file and then attested as the executing driver.
    if ".." in Path(directory).parts:
        raise InventoryError({}, ["parent traversal is not an admissible inventory path"])
    directory = Path(os.path.abspath(directory))
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    identity = ("st_dev", "st_ino", "st_mode")
    version = identity + ("st_size", "st_mtime_ns", "st_ctime_ns")

    def remember_interruption(error):
        nonlocal interruption
        if not isinstance(error, Exception) and interruption is None:
            interruption = error

    def close_descriptor(descriptor):
        try:
            os.close(descriptor)
        except BaseException as error:
            errors.append(f"descriptor close: {type(error).__name__}: {error}")
            remember_interruption(error)

    def same(before, after, fields=version):
        if any(getattr(before, key) != getattr(after, key) for key in fields):
            raise RuntimeError("entry changed during inventory")

    def visit(parent_fd, basename, name):
        descriptor = None
        try:
            try:
                info = os.stat(basename, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                if allow_missing and name == ".":
                    result[name] = {"type": "missing"}
                    return
                raise
            item = result[name] = {"mode": oct(stat.S_IMODE(info.st_mode))}
            if stat.S_ISDIR(info.st_mode):
                item["type"] = "directory"
                descriptor = os.open(basename, directory_flags, dir_fd=parent_fd)
                same(info, os.fstat(descriptor))
                for child in sorted(os.listdir(descriptor)):
                    visit(descriptor, child, child if name == "." else name + "/" + child)
                same(info, os.fstat(descriptor))
                same(info, os.stat(basename, dir_fd=parent_fd, follow_symlinks=False))
            elif stat.S_ISREG(info.st_mode):
                item["type"] = "file"
                flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
                descriptor = os.open(basename, flags, dir_fd=parent_fd)
                same(info, os.fstat(descriptor))
                hasher, size = hashlib.sha256(), 0
                for chunk in iter(lambda: os.read(descriptor, 1024 * 1024), b""):
                    hasher.update(chunk)
                    size += len(chunk)
                same(info, os.fstat(descriptor))
                same(info, os.stat(basename, dir_fd=parent_fd, follow_symlinks=False))
                item.update(size=size, sha256=hasher.hexdigest())
            else:
                item["type"] = "symlink" if stat.S_ISLNK(info.st_mode) else "special"
                if item["type"] == "symlink":
                    item["target"] = os.readlink(basename, dir_fd=parent_fd)
                else:
                    item["stat_type"] = stat.S_IFMT(info.st_mode)
                raise RuntimeError("unsupported inventory entry type: " + item["type"])
        except InventoryError:
            raise  # Preserve an interruption and its partial recursive scan.
        except BaseException as error:
            errors.append(f"{name}: {type(error).__name__}: {error}")
            if not isinstance(error, Exception):
                remember_interruption(error)
                raise InventoryError(result, errors, interruption) from error
        finally:
            if descriptor is not None:
                close_descriptor(descriptor)

    # Anchor even the ancestors through no-follow descriptors. main() resolves
    # its output/root paths once; inventory itself never resolves away a link.
    descriptors, ancestors = [], []
    try:
        parent_fd = os.open(directory.anchor, directory_flags)
        descriptors.append(parent_fd)
        missing = False
        for part in directory.parts[1:-1]:
            try:
                info = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                if not allow_missing:
                    raise
                result["."] = {"type": "missing"}
                missing = True
                break
            child_fd = os.open(part, directory_flags, dir_fd=parent_fd)
            descriptors.append(child_fd)
            same(info, os.fstat(child_fd), identity)
            ancestors.append((parent_fd, part, info))
            parent_fd = child_fd
        if not missing:
            visit(parent_fd, directory.name or ".", ".")
        for parent_fd, part, info in reversed(ancestors):
            same(info, os.stat(part, dir_fd=parent_fd, follow_symlinks=False), identity)
    except InventoryError:
        raise
    except BaseException as error:
        errors.append(f".: {type(error).__name__}: {error}")
        if not isinstance(error, Exception):
            remember_interruption(error)
            raise InventoryError(result, errors, interruption) from error
    finally:
        for descriptor in reversed(descriptors):
            close_descriptor(descriptor)
    if errors:
        raise InventoryError(result, errors, interruption)
    return result


def observation(path, *, allow_missing=False):
    try:
        entries = inventory(path, allow_missing=allow_missing)
        return {"status": "missing" if entries == {".": {"type": "missing"}} else "complete",
                "entries": entries}
    except InventoryError as error:
        if error.interruption is not None:
            raise
        return {"status": "failed", "entries": error.entries, "errors": error.errors}


class Captures:
    """Register before observations immediately; finish each independently."""
    def __init__(self, output, report):
        self.output, self.report = output, report
        self.scopes = report["captures"] = []
        self.failures = []
        self.interruption = None

    def failed_capture(self, scope, phase, value, primary):
        scope[phase] = value
        try:
            save_json(self.output / f"{scope['name']}-{phase}.json", value)
        except BaseException as error:
            # A receipt-write failure must not replace the observation's first
            # interruption/error. The outer receipt retains this partial value.
            scope[phase + "_save_error"] = f"{type(error).__name__}: {error}"
        raise primary

    def capture(self, scope, phase):
        path = Path(scope["path"])
        try:
            value = observation(path, allow_missing=scope["allow_missing"])
        except InventoryError as error:
            value = {"status": "failed", "entries": error.entries, "errors": error.errors}
            self.failed_capture(scope, phase, value,
                                error.interruption if error.interruption is not None else error)
        except BaseException as error:
            value = {"status": "failed", "entries": {},
                     "errors": [f"{type(error).__name__}: {error}"]}
            self.failed_capture(scope, phase, value, error)
        scope[phase] = value
        save_json(self.output / f"{scope['name']}-{phase}.json", value)
        return value

    def watch(self, name, path, *, immutable=False, allow_missing=False):
        scope = {"name": name, "path": str(path), "immutable": immutable,
                 "allow_missing": allow_missing}
        self.scopes.append(scope)  # Even a failed before-capture gets an after.
        value = self.capture(scope, "before")
        if value["status"] == "failed":
            raise RuntimeError(f"{name}: before inventory failed")
        return scope

    def finish(self):
        for scope in self.scopes:
            try:
                after = self.capture(scope, "after")
                if after["status"] == "failed":
                    self.failures.append(f"{scope['name']}: after inventory failed")
                before = scope.get("before")
                if scope["immutable"]:
                    scope["unchanged"] = before is not None and before == after and after["status"] == "complete"
                    if not scope["unchanged"]:
                        self.failures.append(f"{scope['name']}: immutable identity changed or unavailable")
            except BaseException as error:
                if not isinstance(error, Exception) and self.interruption is None:
                    self.interruption = error
                scope["capture_error"] = f"{type(error).__name__}: {error}"
                self.failures.append(f"{scope['name']}: final capture failed: {scope['capture_error']}")
        return self.failures


def retained_status(process):
    # No poll()/wait() before cleanup: the unreaped leader reserves this PID/PGID.
    # ECHILD is a loss of ownership; propagate it without signalling the number.
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)


def wait_retained(process, timeout):
    deadline = time.monotonic() + timeout
    while True:
        status = retained_status(process)
        if status is not None:
            return status.si_status if status.si_code == os.CLD_EXITED else -status.si_status
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(process.args, timeout)
        time.sleep(0.02)


def group_members(pgid):
    # killpg(..., 0) includes the retained zombie leader; enumerate descendants
    # instead. This probe is our own short-lived child outside the owned group.
    probe = subprocess.run(["ps", "-axo", "pid=,pgid="], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=5)
    rows = [tuple(map(int, line.split())) for line in probe.stdout.splitlines() if line.strip()]
    return sorted(pid for pid, group in rows if group == pgid and pid != pgid)


def signal_owned_group(process):
    retained_status(process)  # Verify child ownership immediately before signal.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def clean_owned_group(process, *, terminate=False, grace=1.0, kill_grace=2.0):
    """Keep leader unreaped through every group probe/signal, including success."""
    status = retained_status(process)
    deadline = time.monotonic() + (0.0 if terminate else grace)
    members = group_members(process.pid)
    while members and time.monotonic() < deadline:
        time.sleep(0.02)
        members = group_members(process.pid)
    forced = bool(members) or (terminate and status is None)
    if forced:
        signal_owned_group(process)
        wait_retained(process, kill_grace)
        deadline = time.monotonic() + kill_grace
        members = group_members(process.pid)
        while members and time.monotonic() < deadline:
            time.sleep(0.02)
            members = group_members(process.pid)
    return {"pgid": process.pid, "leader_retained": True, "forced_cleanup": forced,
            "remaining_members": members, "descendants_absent": not members}


def run_command(name, argv, *, cwd, env, logs, output, timeout, report, save):
    command = list(map(str, argv))
    stdout_path, stderr_path = logs / f"{name}.stdout", logs / f"{name}.stderr"
    entry = {"name": name, "argv": command, "cwd": str(cwd), "status": "running",
             "stdout": str(stdout_path.relative_to(output)), "stderr": str(stderr_path.relative_to(output))}
    report["commands"].append(entry)
    process, primary, returncode = None, None, None
    secondary = []
    started = time.monotonic()
    try:
        save()
        print(f"[{name}]", flush=True)
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, start_new_session=True)
            returncode = wait_retained(process, timeout)
        if returncode:
            raise RuntimeError(f"{name} exited with code {returncode}; logs retained in {logs}")
    except BaseException as error:
        primary = error
        entry.update(status="timed_out" if isinstance(error, subprocess.TimeoutExpired) else "failed",
                     error=f"{type(error).__name__}: {error}")
    finally:
        if process is not None:
            try:
                cleanup = clean_owned_group(process, terminate=primary is not None)
                entry["owned_process_group"] = cleanup
                if cleanup["forced_cleanup"] or not cleanup["descendants_absent"]:
                    secondary.append("owned process group outlived its leader or required termination")
            except BaseException as error:
                if not isinstance(error, Exception) and primary is None:
                    primary = error
                secondary.append(f"process group cleanup failed: {type(error).__name__}: {error}")
                # If inspection itself failed, terminate while ownership is
                # still retained. ECHILD fails closed in signal_owned_group.
                try:
                    signal_owned_group(process)
                    wait_retained(process, 2.0)
                except BaseException as cleanup_error:
                    if not isinstance(cleanup_error, Exception) and primary is None:
                        primary = cleanup_error
                    secondary.append(f"fallback termination failed: {type(cleanup_error).__name__}: {cleanup_error}")
            try:
                # All signalling is finished. Reap only a known-exited leader.
                if retained_status(process) is not None:
                    process.wait()
                else:
                    secondary.append("owned leader did not exit before cleanup deadline")
            except BaseException as error:
                if not isinstance(error, Exception) and primary is None:
                    primary = error
                secondary.append(f"leader reap failed: {type(error).__name__}: {error}")
            entry["exit_code"] = process.returncode
        for label, path in (("stdout", stdout_path), ("stderr", stderr_path)):
            try:
                entry[label + "_sha256"] = digest(path)
            except BaseException as error:
                if not isinstance(error, Exception) and primary is None:
                    primary = error
                secondary.append(f"{label} capture failed: {type(error).__name__}: {error}")
        entry.update(elapsed_seconds=time.monotonic() - started, secondary_errors=secondary)
        if primary is None:
            entry["status"] = "failed" if secondary else "completed"
        else:
            entry.update(status="timed_out" if isinstance(primary, subprocess.TimeoutExpired) else "failed",
                         error=f"{type(primary).__name__}: {primary}")
        try:
            save()
        except BaseException as error:
            if not isinstance(error, Exception) and primary is None:
                primary = error
            secondary.append(f"command receipt save failed: {type(error).__name__}: {error}")
            entry["status"] = "failed"
            if primary is not None:
                entry["error"] = f"{type(primary).__name__}: {primary}"
    if primary is not None:
        raise primary
    if secondary:
        raise RuntimeError(f"{name}: " + "; ".join(secondary))
    return stdout_path.read_bytes()


def python_in(venv):
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"invalid JSON constant: {value}")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def canonical(value):
    # Python dict equality would equate False with 0 and True with 1.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def validate_case_inventory(value):
    if (not isinstance(value, dict)
            or value.get("schema") != "native-campaign-case-inventory-v1"
            or value.get("cases") != list(PUBLIC_CASES)):
        raise RuntimeError("consumer public case inventory differs from the exact 49-case protocol")
    return value["cases"]


def json_rows(raw):
    rows = [strict_json(line) for line in raw.splitlines() if line.strip()]
    ids = [row.get("case") for row in rows]
    if (not rows or any(row.get("schema") != "native-campaign-v1"
                        or row.get("kind") not in {"seed", "validation", "campaign"}
                        for row in rows)
            or any(not isinstance(identifier, str) for identifier in ids)
            or len(ids) != len(set(ids))):
        raise RuntimeError("missing, malformed, or duplicated public fixture rows")
    return rows


def read_expected(expected, freeze):
    """Require the independent Rust-author seal, never generate missing goldens."""
    expected_bytes, freeze_bytes = expected.read_bytes(), freeze.read_bytes()
    seal = strict_json(freeze_bytes)
    relative = str(expected.relative_to(ROOT))
    files = seal.get("files", {})
    if (seal.get("schema") != "n06-independent-spec/1"
            or seal.get("source_commit") != AUTHORITY_COMMIT
            or seal.get("candidate_output_used") is not False
            or not isinstance(seal.get("derivation"), str) or not seal["derivation"]
            or not isinstance(files, dict)
            or not {relative, CONTRACT, REFERENCE} <= files.keys()):
        raise RuntimeError("independent Rust fixture seal is missing required authority fields")
    for name, wanted in files.items():
        if not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts:
            raise RuntimeError("independent seal paths must be checkout-relative")
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or digest(path) != wanted:
            raise RuntimeError(f"independent fixture identity mismatch: {name}")
    if files[relative] != hashlib.sha256(expected_bytes).hexdigest():
        raise RuntimeError("expected rows changed while their seal was being verified")
    rows = json_rows(expected_bytes)
    if seal.get("cases") != [row["case"] for row in rows]:
        raise RuntimeError("independent seal case inventory differs")
    if tuple(row["case"] for row in rows) != PUBLIC_CASES:
        raise RuntimeError("independent seal does not cover the exact current 49-case protocol")
    if any(row["kind"] == "campaign" and row.get("config", {}).get("workers") != "1" for row in rows):
        raise RuntimeError("exact protocol expectations must use one worker")
    return rows, seal, expected_bytes, freeze_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path,
                        help="new output directory outside the checkout; retained after failure")
    parser.add_argument("--driver-sha256", required=True,
                        help="reviewed driver SHA-256 selected/authenticated by the caller, never self-derived")
    parser.add_argument("--expected", type=Path, help="independent public JSONL, optional")
    parser.add_argument("--freeze", type=Path, help="Rust-author n06-independent-spec/1 seal")
    parser.add_argument("--timeout", type=int, default=900, help="per-command seconds")
    args = parser.parse_args()
    if bool(args.expected) != bool(args.freeze):
        parser.error("--expected and --freeze must be supplied together")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if (len(args.driver_sha256) != 64
            or any(char not in "0123456789abcdef" for char in args.driver_sha256)):
        parser.error("--driver-sha256 must be a lowercase 64-digit SHA-256")
    if (os.name != "posix" or not all(hasattr(os, key) for key in
            ("waitid", "WNOWAIT", "WEXITED", "WNOHANG", "P_PID", "O_NOFOLLOW", "O_DIRECTORY"))):
        parser.error("qualification requires POSIX waitid/WNOWAIT and no-follow inventory support")
    out = args.output.resolve()
    if out.is_relative_to(ROOT) or ROOT.is_relative_to(out):
        parser.error("--output must be outside the checkout and cannot contain it")
    out.mkdir(parents=True, exist_ok=False)
    logs, artifacts, work, snapshot = (out / name for name in ("logs", "packages", "work", "snapshot"))
    report = {"schema": "n06-python-installed-example/1", "status": "running",
              "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "source": str(ROOT), "commands": [], "consumers": {},
              "comparison": "pending" if args.expected else "not_requested",
              "scope": "Python application wheel and sdist consumers; not full N06 qualification"}
    report["executing_driver"] = {
        "original_spelling": __file__, "expected_sha256": args.driver_sha256,
        "identity_authority": "caller-selected reviewed driver; authenticate outside this process",
        "loaded_bytes_limit": "file identity checks do not attest interpreter memory or hostile concurrent replacement",
    }
    # Keep an explicit non-secret environment, recorded with each receipt.
    # Builder tool installation is networked; runtime installs use no index.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR")
           if key in os.environ}
    temporary = work / "tmp"
    env.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
               PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1",
               TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary))
    report["environment"] = env

    def save():
        save_json(out / "qualification.json", report)

    def run(name, argv, *, cwd=work):
        return run_command(name, argv, cwd=cwd, env=env, logs=logs, output=out,
                           timeout=args.timeout, report=report, save=save)

    failures = []
    captures = Captures(out, report)
    primary, expected_rows = None, None
    try:
        save()
        # Preserve the original spelling for no-follow validation. Capture
        # precedes every child command and joins the unconditional final pass.
        driver_scope = captures.watch("executing-driver", Path(__file__), immutable=True)
        driver_entry = driver_scope["before"]["entries"].get(".", {})
        report["executing_driver"]["path"] = os.path.abspath(__file__)
        report["executing_driver"]["before"] = driver_scope["before"]
        if driver_entry.get("type") != "file" or driver_entry.get("sha256") != args.driver_sha256:
            raise RuntimeError("executing driver differs from caller-selected reviewed SHA-256")
        captures.watch("snapshot-preparation", snapshot, allow_missing=True)
        captures.watch("package-production", artifacts, allow_missing=True)
        captures.watch("package-inputs", ROOT / "python/src", immutable=True)
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            captures.watch("original-package-" + name, ROOT / "python" / name, immutable=True)
        captures.watch("original-example", ROOT / EXAMPLE, immutable=True)
        for name, relative in (("reference", REFERENCE), ("author", AUTHOR), ("contract", CONTRACT)):
            captures.watch("original-" + name, ROOT / relative, immutable=True)
        for directory in (logs, artifacts, work, snapshot):
            directory.mkdir()
        temporary.mkdir()
        if args.expected:
            expected, freeze = args.expected.resolve(), args.freeze.resolve()
            captures.watch("original-expected", expected, immutable=True)
            captures.watch("original-freeze", freeze, immutable=True)
            expected_rows, seal, expected_bytes, freeze_bytes = read_expected(expected, freeze)
            (snapshot / "expected.jsonl").write_bytes(expected_bytes)
            (snapshot / "FREEZE.json").write_bytes(freeze_bytes)
            report["specification"] = {"expected_sha256": hashlib.sha256(expected_bytes).hexdigest(),
                                       "freeze_sha256": hashlib.sha256(freeze_bytes).hexdigest(),
                                       "files": seal["files"], "cases": seal["cases"]}
            for relative, wanted in seal["files"].items():
                captures.watch("original-sealed-" + str(len(captures.scopes)), ROOT / relative, immutable=True)
                target = snapshot / "specification" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, target)
                if digest(target) != wanted:
                    raise RuntimeError(f"sealed specification changed during copy: {relative}")
        package = snapshot / "python"
        package.mkdir()
        shutil.copytree(ROOT / "python/src", package / "src",
                        symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"))
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            shutil.copy2(ROOT / "python" / name, package / name)
        shutil.copy2(ROOT / EXAMPLE, snapshot / "consumer.py")
        shutil.copy2(Path(__file__), snapshot / "qualify_campaign_install.py")
        if digest(snapshot / "qualify_campaign_install.py") != args.driver_sha256:
            raise RuntimeError("archived driver differs from caller-selected reviewed SHA-256")
        for relative in (REFERENCE, AUTHOR, CONTRACT):
            target = snapshot / Path(relative).name
            shutil.copy2(ROOT / relative, target)
            if args.expected and relative in seal["files"] and digest(target) != seal["files"][relative]:
                raise RuntimeError(f"retained authority differs from its seal: {relative}")
        captures.watch("source-snapshot", snapshot, immutable=True)
        save()
        report["source_commit"] = run("source-commit", ["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
        report["source_status"] = run("source-status", ["git", "status", "--porcelain=v1"], cwd=ROOT).decode()
        builder = work / "builder"
        installed_relative = f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        captures.watch("builder-tools", builder / installed_relative, allow_missing=True)
        run("builder-venv", [sys.executable, "-I", "-B", "-m", "venv", builder])
        build_python = python_in(builder)
        captures.watch("builder-tool-installation", builder / installed_relative)
        run("builder-tools", [build_python, "-I", "-B", "-m", "pip", "install",
                              "--no-cache-dir", "setuptools>=77", "wheel", "build"])
        report["builder_versions"] = run("builder-versions", [build_python, "-I", "-B", "-m", "pip", "freeze", "--all"]).decode()
        # The sealed snapshot itself is never passed to a mutating backend.
        build_source = work / "package"
        captures.watch("build-preparation", build_source, allow_missing=True)
        shutil.copytree(package, build_source, symlinks=True)
        captures.watch("build-source", build_source)
        run("package-build", [build_python, "-I", "-B", "-m", "build", "--no-isolation",
                              "--wheel", "--sdist", "--outdir", artifacts, build_source])
        wheel, = artifacts.glob("*.whl")
        sdist, = artifacts.glob("*.tar.gz")
        report["package_sha256"] = {wheel.name: digest(wheel), sdist.name: digest(sdist)}
        captures.watch("wheel-package", wheel, immutable=True)
        captures.watch("sdist-package", sdist, immutable=True)
        rebuilt = artifacts / "from-sdist"
        rebuilt.mkdir()
        captures.watch("sdist-rebuild", rebuilt)
        run("sdist-wheel", [build_python, "-I", "-B", "-m", "pip", "wheel", "--no-deps",
                            "--no-index", "--no-cache-dir", "--no-build-isolation", "--wheel-dir", rebuilt, sdist])
        rebuilt_wheel, = rebuilt.glob("*.whl")
        report["sdist_rebuilt_wheel_sha256"] = digest(rebuilt_wheel)
        captures.watch("rebuilt-wheel-package", rebuilt_wheel, immutable=True)
        # Distinct clean environments prove neither path borrows builder tools
        # or source imports. The sdist is unpacked/built only by the builder.
        for label, archive in (("wheel", wheel), ("sdist", rebuilt_wheel)):
            runtime = work / f"runtime-{label}"
            consumer_dir = work / f"consumer-{label}"
            installed = runtime / installed_relative
            evidence = report["consumers"][label] = {"status": "preparing", "archive_sha256": digest(archive)}
            captures.watch(f"{label}-consumer-preparation", consumer_dir, allow_missing=True)
            captures.watch(f"{label}-installation", installed, allow_missing=True)
            consumer_dir.mkdir()
            consumer = consumer_dir / "consumer.py"
            shutil.copy2(snapshot / "consumer.py", consumer)
            captures.watch(f"{label}-consumer", consumer_dir, immutable=True)
            executable = python_in(runtime)
            run(f"{label}-venv", [sys.executable, "-I", "-B", "-m", "venv", runtime])
            captures.watch(f"{label}-install-input", installed)
            run(f"{label}-install", [executable, "-I", "-B", "-m", "pip", "install",
                                     "--no-index", "--no-deps", "--no-cache-dir", archive])
            prefix = [executable, "-I", "-B", consumer]
            evidence["consumer_sha256"] = digest(consumer)
            captures.watch(f"{label}-installed", installed, immutable=True)
            try:
                proof = strict_json(run(f"{label}-origin", prefix + ["--installed-proof"], cwd=consumer_dir))
                if proof["archive_sha256"] != digest(archive):
                    raise RuntimeError("runtime archive identity differs from built wheel")
                evidence["installed"] = proof
                evidence["application_evidence"] = f"{label}-application"
                captures.watch(f"{label}-application", out / f"{label}-application", allow_missing=True)
                save()
                application = strict_json(run(f"{label}-demo", prefix + ["--demo", "--evidence", out / f"{label}-application"], cwd=consumer_dir))
                if application.get("schema") != "native-campaign-python-example-v1" or len(application.get("runs", [])) != 6:
                    raise RuntimeError("consumer did not emit all six serial/spawn application runs")
                evidence["application_runs"] = len(application["runs"])
                case_inventory = strict_json(run(f"{label}-inventory", prefix + ["--list-cases"], cwd=consumer_dir))
                identifiers = validate_case_inventory(case_inventory)
                rows, raw_parts = [], []
                for identifier in identifiers:
                    part = run(f"{label}-reference-{identifier}", prefix + ["--case", identifier], cwd=consumer_dir)
                    emitted = json_rows(part)
                    if len(emitted) != 1 or emitted[0]["case"] != identifier:
                        raise RuntimeError(f"consumer emitted the wrong case: {identifier}")
                    rows.extend(emitted)
                    raw_parts.append(part)
                raw = b"".join(raw_parts)
                (out / f"{label}-actual.jsonl").write_bytes(raw)
                evidence["cases"] = [row["case"] for row in rows]
                evidence["actual_sha256"] = hashlib.sha256(raw).hexdigest()
                if expected_rows is not None:
                    actual = {row["case"]: row for row in rows}
                    mismatches = [row["case"] for row in expected_rows
                                  if canonical(actual.get(row["case"])) != canonical(row)]
                    evidence["comparison"] = {"required_cases": len(expected_rows), "mismatches": mismatches,
                                               "additional_actual_cases": sorted(set(actual) - {row["case"] for row in expected_rows})}
                    if mismatches:
                        failures.append(f"{label}: exact Rust comparison differs: {', '.join(mismatches)}")
                evidence["status"] = "completed"
            except Exception as error:
                evidence.update(status="failed", error=str(error))
                failures.append(f"{label}: {error}")
            save()
    except BaseException as error:
        primary = error
        report.update(error=f"{type(error).__name__}: {error}")
    finally:
        # Each registered stage captures independently, including failed copy,
        # build, install, consumer, manifest and interrupted-command paths.
        failures.extend(captures.finish())
        driver_scope = next((scope for scope in captures.scopes if scope["name"] == "executing-driver"), None)
        if driver_scope is not None:
            report["executing_driver"]["after"] = driver_scope.get("after")
            report["executing_driver"]["unchanged"] = driver_scope.get("unchanged", False)
        if primary is None and captures.interruption is not None:
            primary = captures.interruption
            report["error"] = f"{type(primary).__name__}: {primary}"
        report.update(status="failed" if primary is not None or failures else "passed_installed_consumers",
                      failures=failures)
        if args.expected:
            report["comparison"] = "failed" if report["status"] == "failed" else "passed_exact_required_rows"
        try:
            save()
        except BaseException as error:
            failures.append(f"final receipt save failed: {type(error).__name__}: {error}")
            if primary is None:
                primary = error
            report.update(status="failed", error=f"{type(primary).__name__}: {primary}")
            if args.expected:
                report["comparison"] = "failed"
            try:
                save()  # The failed write may already have flushed success JSON.
            except BaseException as correction_error:
                report["receipt_correction_error"] = f"{type(correction_error).__name__}: {correction_error}"
                failures.append("failure receipt correction failed: " + report["receipt_correction_error"])
                try:
                    # Retain any bytes, but remove the authoritative success
                    # pathname when a corrected failure receipt cannot be saved.
                    (out / "qualification.json").rename(out / "qualification-unconfirmed.json")
                except FileNotFoundError:
                    pass
                except BaseException as invalidation_error:
                    report["receipt_invalidation_error"] = f"{type(invalidation_error).__name__}: {invalidation_error}"
    if primary is not None:
        raise primary
    if failures:
        raise RuntimeError("; ".join(failures))
    print(f"Installed Python campaign consumers completed: {out}", flush=True)


if __name__ == "__main__":
    main()

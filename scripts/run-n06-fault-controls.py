#!/usr/bin/env python3
"""Run four N06 engine mutations from one verified selected source snapshot.

Submit this command through Kyoto. It never mutates the snapshot: each mutant is
created in a temporary copy. The snapshot remains writable and the linkage between
the copy, tools and executables is caller-attested, not an independent build proof.
Compilation failures, timeouts, crashes and unrelated test failures are errors.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("n06_snapshot", HERE / "prepare-n06-snapshot.py")
snapshot_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot_module)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(output, name, argv, cwd, env, timeout=600, maximum=8 * 1024 * 1024):
    log = output / (name + ".log")
    with log.open("wb") as stream, selectors.DefaultSelector() as selector:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True)
        os.set_blocking(proc.stdout.fileno(), False)
        selector.register(proc.stdout, selectors.EVENT_READ)
        start = time.monotonic()
        size = 0
        try:
            while selector.get_map():
                if time.monotonic() - start > timeout:
                    raise RuntimeError(name + " timed out; not a detection")
                for key, _ in selector.select(0.1):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    if size + len(chunk) > maximum:
                        stream.write(chunk[:maximum - size])
                        raise RuntimeError(name + " exceeded log limit")
                    stream.write(chunk)
                    size += len(chunk)
            code = proc.wait(timeout=max(.001, timeout - (time.monotonic() - start)))
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            else:
                raise RuntimeError(name + " left a descendant process")
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                # macOS reports EPERM for a group whose members already exited
                # but are not yet reaped; the group is gone either way.
                pass
            proc.wait()
            if not proc.stdout.closed:
                proc.stdout.close()
    data = log.read_bytes()
    return code, data.decode("utf-8", "replace")


def must_pass(result, name, marker=None):
    code, text = result
    if code != 0 or (marker is not None and marker not in text):
        raise RuntimeError(name + " failed or did not execute expected test")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshot", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    snap = args.snapshot.resolve(strict=True)
    root = snap / "source"
    out = args.output.resolve()
    if out.exists() or out == snap or snap in out.parents:
        raise ValueError("output must be new and outside snapshot")
    out.mkdir(parents=True)
    manifest = snapshot_module.verify(snap)
    # The runner must itself be the version in the verified snapshot.
    if Path(__file__).resolve() != (root / "scripts/run-n06-fault-controls.py").resolve():
        raise ValueError("run the runner from the verified snapshot")
    env = os.environ.copy()
    for k in ("PYTHONPATH", "NODE_PATH", "PYTHONHOME", "PYTHONOPTIMIZE"):
        env.pop(k, None)
    env["PYTHONOPTIMIZE"] = "0"
    receipt = {"schema": "n06-snapshot-fault-controls/1", "source_manifest_sha256": sha(snap / "source-manifest.json"),
               "source_files": len(manifest["files"]), "controls": {},
               "scope": "caller-attested writable snapshot; source-tree controls, not installed or full qualification"}
    try:
        for language, path in (("python", "validation/native-parity/n06/python/worker_loss_mutation_control.py"),
                               ("go", "validation/native-parity/n06/go/worker-priority-20261031/control.py")):
            result = command(out, language, [sys.executable, str(root / path)], root, env)
            must_pass(result, language)
            receipt["controls"][language] = {"status": "detected", "log_sha256": sha(out / (language + ".log"))}
        with tempfile.TemporaryDirectory(prefix="n06-fault-controls-") as temp:
            tmp = Path(temp)
            ts = tmp / "typescript"
            shutil.copytree(root / "typescript", ts)
            shutil.copytree(root / "docs/requirements", tmp / "docs/requirements")
            must_pass(command(out, "ts-install", ["npm", "ci", "--ignore-scripts"], ts, env), "ts install")
            must_pass(command(out, "ts-build-clean", ["npm", "run", "build"], ts, env), "ts clean build")
            test = ["node", "--test", "--test-reporter=tap", "--test-name-pattern", "sink rejection drains claimed work once without extra sink calls", "test/campaign.test.mjs"]
            must_pass(command(out, "ts-clean", test, ts, env), "ts clean", "# pass 1")
            lifecycle = ts / "src/campaign_lifecycle.ts"
            source = lifecycle.read_text()
            old = "report.termination.kind === 'CallbackError'"
            if source.count(old) != 1:
                raise RuntimeError("typescript mutation anchor not unique")
            lifecycle.write_text(source.replace(old, "report.termination.kind === 'WorkerError'", 1))
            must_pass(command(out, "ts-build-mutant", ["npm", "run", "build"], ts, env), "ts mutant build")
            code, text = command(out, "ts-mutant", test, ts, env)
            if code != 1 or "ERR_ASSERTION" not in text or "2 !== 1" not in text or "# fail 1" not in text:
                raise RuntimeError("typescript mutant lacked required semantic failure")
            receipt["controls"]["typescript"] = {"status": "detected", "source_sha256": hashlib.sha256(source.encode()).hexdigest(), "mutant_sha256": sha(lifecycle), "log_sha256": sha(out / "ts-mutant.log")}
            swift = tmp / "swift-root"
            swift.mkdir()
            shutil.copy2(root / "Package.swift", swift / "Package.swift")
            shutil.copytree(root / "swift", swift / "swift")
            target = "CampaignRuntimeTests.testExitFailureAfterEveryOutcomeStillConservativelyLosesAccounting"
            clean = ["swift", "test", "--package-path", str(swift), "--scratch-path", str(tmp / "swift-clean"), "--filter", target]
            must_pass(command(out, "swift-clean", clean, swift, env), "swift clean", "Executed 1 test")
            lifecycle = swift / "swift/Sources/Statelessness/CampaignLifecycle.swift"
            source = lifecycle.read_text()
            old = "if lostWork { state.report.accountingComplete = false }"
            if source.count(old) != 1:
                raise RuntimeError("swift mutation anchor not unique")
            lifecycle.write_text(source.replace(old, "if lostWork { /* mutation: omit loss accounting */ }", 1))
            mutant = ["swift", "test", "--package-path", str(swift), "--scratch-path", str(tmp / "swift-mutant"), "--filter", target]
            code, text = command(out, "swift-mutant", mutant, swift, env)
            if (code != 1 or "Build complete!" not in text
                    or "Executed 1 test, with 1 failure (0 unexpected)" not in text
                    or "XCTAssertFalse failed" not in text or "CampaignRuntimeTests.swift:1002" not in text):
                raise RuntimeError("swift mutant lacked required semantic failure")
            receipt["controls"]["swift"] = {"status": "detected", "source_sha256": hashlib.sha256(source.encode()).hexdigest(), "mutant_sha256": sha(lifecycle), "log_sha256": sha(out / "swift-mutant.log")}
        snapshot_module.verify(snap)
        receipt["snapshot_verified_after"] = True
        receipt["status"] = "passed"
    except Exception as exc:
        receipt["status"] = "failed"
        receipt["error"] = str(exc)
        raise
    finally:
        try:
            snapshot_module.verify(snap)
            receipt["snapshot_verified_after"] = True
        except Exception as exc:
            receipt["snapshot_verified_after"] = False
            receipt["snapshot_error"] = str(exc)
            receipt["status"] = "failed"
        receipt["logs"] = {p.name: sha(p) for p in sorted(out.glob("*.log"))}
        (out / "results.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    if receipt["status"] != "passed":
        raise RuntimeError("snapshot verification failed")


if __name__ == "__main__":
    main()

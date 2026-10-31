#!/usr/bin/env python3
"""One focused Swift Kyoto job; immutable inputs, full seals, raw command logs.

This is not a cross-language comparator or a global N06 gate. Execute only through
the shared Kyoto queue. The snapshot and output directory must be distinct.
"""
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
import traceback


SCOPE = "Swift runner guards, package tests and fresh source-package consumer only; no cross-language or global pass"
# Scratch builds, caches, config and security files live OUTSIDE the source copies.
# Only this exact root directory is SwiftPM-generated bookkeeping. A symlink or
# file at this path is never excluded. No .build/source/resource paths are ignored.
GENERATED_DIRECTORIES = frozenset({".swiftpm"})


class QualificationInterrupted(BaseException):
    pass


class InterruptionRequests:
    def __init__(self):
        self.received = []

    def install(self):
        for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(signum, self.observe)

    def observe(self, signum, _frame):
        # Never throw from the signal handler: Popen must first hand back its
        # owned handle, cleanup must finish, and terminal receipts must persist.
        # Repeated signals are evidence, not a second interruption of cleanup.
        self.received.append({"signal": signal.Signals(signum).name, "unix": time.time()})

    def check(self):
        if self.received:
            raise QualificationInterrupted(self.received[0]["signal"])


def freeze_terminal_signals(interruptions):
    # All owned commands have been cleaned up before this boundary. Keep these
    # signals blocked through receipt persistence and process exit: the receipt
    # and exit code must use the SAME frozen decision. Signals arriving after
    # the pending-set sample are outside this terminal interruption window.
    names = ("pthread_sigmask", "sigpending")
    if not all(hasattr(signal, name) for name in names):
        return list(interruptions.received), False
    watched = {signal.SIGTERM, signal.SIGHUP, signal.SIGINT}
    signal.pthread_sigmask(signal.SIG_BLOCK, watched)
    for signum in sorted(signal.sigpending() & watched):
        interruptions.observe(signum, None)
        interruptions.received[-1]["source"] = "pending_at_terminal_boundary"
    return list(interruptions.received), True


def manifest(root, *, generated=False, exclude_identity=False):
    entries = []

    def visit(path, relative):
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if exclude_identity and relative == "source-identity.json":
            return
        if generated and relative in GENERATED_DIRECTORIES:
            if not stat.S_ISDIR(info.st_mode):
                raise RuntimeError(f"generated bookkeeping must be a directory: {relative}")
            return
        entry = {"path": relative, "mode": mode}
        if stat.S_ISLNK(info.st_mode):
            entry.update(type="symlink", target=os.readlink(path))
        elif stat.S_ISREG(info.st_mode):
            entry.update(type="file", size=info.st_size,
                         sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        elif stat.S_ISDIR(info.st_mode):
            entry.update(type="directory")
        else:
            raise RuntimeError(f"unsupported input type: {relative}")
        entries.append(entry)
        if entry["type"] == "directory":
            for child in sorted(path.iterdir()):
                visit(child, child.name if relative == "." else f"{relative}/{child.name}")

    visit(root, ".")
    return entries


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def ordered(entries):
    if len({entry["path"] for entry in entries}) != len(entries):
        raise RuntimeError("duplicate path in source manifest")
    return sorted(entries, key=lambda entry: entry["path"])


def subtree(entries, prefix):
    selected = []
    for entry in entries:
        path = entry["path"]
        if path == prefix:
            if entry["type"] != "directory":
                raise RuntimeError(f"sealed subtree is not a directory: {prefix}")
            selected.append(dict(entry, path="."))
        elif path.startswith(prefix + "/"):
            selected.append(dict(entry, path=path[len(prefix) + 1:]))
    if not any(entry["path"] == "." for entry in selected):
        raise RuntimeError(f"sealed subtree missing: {prefix}")
    return ordered(selected)


def filesystem_id(path):
    try:
        info = path.stat()
        return info.st_dev, info.st_ino
    except FileNotFoundError:
        return None


def require_disjoint(source, output):
    if source == output or source in output.parents or output in source.parents:
        raise RuntimeError("snapshot and output must be disjoint")
    # resolve() handles symlinks, but preserves case aliases on insensitive
    # filesystems. Compare actual existing root/ancestor identities as well.
    source_id, output_id = filesystem_id(source), filesystem_id(output)
    if source_id is not None and any(filesystem_id(path) == source_id for path in (output, *output.parents)):
        raise RuntimeError("output aliases the snapshot or a descendant")
    if output_id is not None and any(filesystem_id(path) == output_id for path in (source, *source.parents)):
        raise RuntimeError("snapshot aliases the output or a descendant")


def executable_identity(path):
    path = Path(path).absolute()
    info = path.lstat()
    resolved = path.resolve(strict=True)
    target = resolved.stat()
    if not stat.S_ISREG(target.st_mode):
        raise RuntimeError(f"executable is not a regular file: {path}")
    return {"path": str(path), "type": "symlink" if path.is_symlink() else "file",
            "link_target": os.readlink(path) if path.is_symlink() else None,
            "mode": stat.S_IMODE(info.st_mode), "resolved_path": str(resolved),
            "resolved_mode": stat.S_IMODE(target.st_mode), "size": target.st_size,
            "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest()}


def peek_exit(process):
    # WNOWAIT observes the child without reaping it. Its retained PID prevents
    # reuse of the numeric process-group ID until all group signalling is done.
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)


def await_exit(process, timeout, interruptions):
    deadline = time.monotonic() + timeout
    while True:
        interruptions.check()
        if peek_exit(process) is not None:
            return
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(process.args, timeout)
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))


def stop_owned_group(process):
    # start_new_session makes this exact child's PID the new session/group ID.
    # Never inspect or kill Kyoto's group, another job, or a process by name.
    pgid = process.pid
    cleanup = {"pgid": pgid, "signals": [], "leader_reaped": False}
    if process.returncode is not None:
        cleanup["ownership_released"] = True
        return cleanup
    try:
        peek_exit(process)
    except ChildProcessError:
        cleanup["ownership_lost"] = True
        return cleanup
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
            cleanup["signals"].append(sig.name)
        except ProcessLookupError:
            break
        if sig == signal.SIGTERM:
            # Keep the leader unreaped for the whole grace interval, including
            # after it exits. Descendants may still require SIGKILL.
            time.sleep(2)
    try:
        process.wait(timeout=3)
        cleanup["leader_reaped"] = True
    except subprocess.TimeoutExpired:
        cleanup["leader_not_reaped"] = True
    # No signal or group probe after releasing the leader's PID ownership.
    return cleanup


def main():
    if len(sys.argv) != 4:
        raise RuntimeError("usage: qualify.py SNAPSHOT NEW_OUTPUT MANIFEST_SHA256")
    # Resolve aliases (notably /tmp versus /private/tmp) before any output write.
    # Missing paths remain legal here so their preflight errors get a receipt.
    source = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    require_disjoint(source, output)
    # Refuse to overwrite any previous receipt. Everything after this point,
    # including manifest preflight and partial copy failures, is captured.
    output.mkdir(parents=True, exist_ok=False)
    result = {"status": "failed", "scope": SCOPE, "source_identity_sha256": sys.argv[3],
              "stage": "preflight", "generated_directory_exclusions": sorted(GENERATED_DIRECTORIES)}
    write_json(output / "result.json", result)
    commands, tools_before = [], {}
    interruptions = InterruptionRequests()
    before = package_before = consumer_before = None
    driver_before = None
    package, consumer = output / "installed" / "statelessness", output / "consumer"
    failure = None
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C",
           "TMPDIR": str(output / "tmp"), "CLANG_MODULE_CACHE_PATH": str(output / "clang-cache"),
           "SWIFTPM_MODULECACHE_OVERRIDE": str(output / "swift-module-cache"),
           "STATELESS_SWIFT_PACKAGE_PATH": str(package)}
    # Preserve the user's actual HOME; do not repurpose it. All SwiftPM writable
    # caches/configuration are passed as explicit output-local paths below.
    if "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    write_json(output / "environment.json", env)

    def pin_tool(name, path):
        tools_before[name] = executable_identity(path)
        write_json(output / "tools-before.json", tools_before)
        return str(Path(path).absolute())

    def run(name, argv, cwd, timeout=1800):
        interruptions.check()
        started = time.monotonic()
        record = {"name": name, "argv": argv, "cwd": str(cwd), "environment": env,
                  "started_unix": time.time(), "stdin": "/dev/null", "timeout_seconds": timeout,
                  "new_session": True, "returncode": None}
        result["stage"] = name
        write_json(output / "result.json", result)
        process = None
        print(json.dumps({"command": name, "argv": argv, "cwd": str(cwd)}), flush=True)
        try:
            with (output / f"{name}.stdout.log").open("wb") as stdout, (output / f"{name}.stderr.log").open("wb") as stderr:
                process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                           stdout=stdout, stderr=stderr, start_new_session=True)
                record["pid"] = record["pgid"] = process.pid
                try:
                    # Signals arriving during Popen were recorded without
                    # throwing, so this handle is now owned before we unwind.
                    await_exit(process, timeout, interruptions)
                except subprocess.TimeoutExpired:
                    record["timed_out"] = True
                    record["cleanup"] = stop_owned_group(process)
                except BaseException:
                    record["cleanup"] = stop_owned_group(process)
                    raise
                else:
                    # Also clean the normal/nonzero command's remaining group
                    # while its exited leader is retained by WNOWAIT. Never
                    # reap and then probe/signal a recyclable numeric PGID.
                    record["exit_observed_before_cleanup"] = True
                    record["cleanup"] = stop_owned_group(process)
                    record["returncode"] = process.returncode
        except BaseException as error:
            record["exception"] = repr(error)
            raise
        finally:
            record["elapsed_seconds"] = time.monotonic() - started
            record["interruptions"] = list(interruptions.received)
            commands.append(record)
            write_json(output / "commands.json", commands)
        interruptions.check()
        if record["returncode"] != 0 or not record.get("cleanup", {}).get("leader_reaped"):
            raise RuntimeError(f"{name} failed; complete stdout/stderr retained")
        print(f'KYOTO_PROGRESS {{"completed": {len(commands)}, "total": 13, "phase": "swift-focused"}}', flush=True)

    interruptions.install()
    try:
        interruptions.check()
        if sys.platform != "darwin":
            raise RuntimeError("this focused toolchain receipt is Darwin-only")
        if not all(hasattr(os, name) for name in ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT")):
            raise RuntimeError("owned-group timeout cleanup requires waitid/WNOWAIT; no unsafe fallback")
        if not all(hasattr(signal, name) for name in ("pthread_sigmask", "sigpending")):
            raise RuntimeError("terminal signal boundary requires pthread_sigmask/sigpending")
        # An inherited SIG_IGN would auto-reap children and release ownership.
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        identity_path = source / "source-identity.json"
        if not stat.S_ISREG(identity_path.lstat().st_mode):
            raise RuntimeError("source identity must be a regular file")
        identity_bytes = identity_path.read_bytes()
        if hashlib.sha256(identity_bytes).hexdigest() != sys.argv[3]:
            raise RuntimeError("sealed source manifest hash mismatch")
        identity = json.loads(identity_bytes)
        if stat.S_IMODE(identity_path.lstat().st_mode) != identity["identity_file_mode"]:
            raise RuntimeError("sealed source manifest mode mismatch")
        # Every accepted baseline comes from these authenticated bytes, never a
        # later live read that could silently adopt source drift during copying.
        sealed_entries = ordered(identity["snapshot_entries"])
        before = ordered(sealed_entries + [{"path": "source-identity.json", "type": "file",
                           "mode": identity["identity_file_mode"], "size": len(identity_bytes),
                           "sha256": sys.argv[3]}])
        package_before = subtree(sealed_entries, "package")
        consumer_before = subtree(sealed_entries, "consumer")
        if ordered(manifest(source)) != before:
            raise RuntimeError("sealed source membership/type/mode/content mismatch")
        executing = Path(__file__).resolve(strict=True)
        if executing != (source / "qualify.py").resolve(strict=True):
            raise RuntimeError("executing qualifier is not the sealed snapshot member")
        expected_driver = next(entry for entry in sealed_entries if entry["path"] == "qualify.py")
        actual_driver = dict(manifest(executing)[0], path="qualify.py")
        if actual_driver != expected_driver or expected_driver["type"] != "file":
            raise RuntimeError("executing qualifier mode/content does not match sealed member")
        driver_before = {"path": str(executing), "entry": expected_driver}
        write_json(output / "driver-before.json", {"identity": driver_before,
                   "argv": sys.argv, "python": executable_identity(sys.executable)})
        write_json(output / "inputs-before.json", before)
        write_json(output / "source-identity.json", identity)
        interruptions.check()
        Path(env["TMPDIR"]).mkdir()
        result["stage"] = "install-copy"
        write_json(output / "result.json", result)
        shutil.copytree(source / "package", package, symlinks=True)
        shutil.copytree(source / "consumer", consumer, symlinks=True)
        write_json(output / "installed-before.json", manifest(package))
        write_json(output / "consumer-before.json", manifest(consumer))
        if ordered(manifest(package)) != package_before or ordered(manifest(consumer)) != consumer_before:
            raise RuntimeError("installed copy membership/type/mode/content mismatch")
        interruptions.check()
        # Preexisting generated paths would weaken the source seal; refuse them.
        for root in (package, consumer):
            if any((root / name).exists() or (root / name).is_symlink() for name in GENERATED_DIRECTORIES):
                raise RuntimeError("sealed source unexpectedly includes generated bookkeeping")
        python = pin_tool("python", sys.executable)
        run("runner-guards", [python, "-B", "-E", "-s", str(source / "guards.py"),
                              str(output / "guard-work")], package, 180)
        xcrun = pin_tool("xcrun", "/usr/bin/xcrun")
        paths = {}
        for name in ("swift", "swiftc", "clang"):
            run(f"find-{name}", [xcrun, "--find", name], package, 60)
            paths[name] = pin_tool(name, (output / f"find-{name}.stdout.log").read_text().strip())
        run("sdk-path", [xcrun, "--show-sdk-path"], package, 60)
        run("sdk-version", [xcrun, "--show-sdk-version"], package, 60)
        run("swift-version", [paths["swift"], "--version"], package, 60)
        run("clang-version", [paths["clang"], "--version"], package, 60)
        swift = paths["swift"]
        package_options = ["--cache-path", str(output / "swiftpm-cache"),
                           "--config-path", str(output / "swiftpm-config"),
                           "--security-path", str(output / "swiftpm-security")]
        run("swift-tests", [swift, "test", "--package-path", str(package), "--scratch-path",
                            str(output / "test-build"), *package_options, "-j", "2"], package)
        run("consumer-build", [swift, "build", "--package-path", str(consumer), "--scratch-path",
                               str(output / "consumer-build"), *package_options, "-j", "2"], consumer)
        run("consumer-bin-path", [swift, "build", "--package-path", str(consumer), "--scratch-path",
                                  str(output / "consumer-build"), *package_options, "--show-bin-path"], consumer)
        binary = Path((output / "consumer-bin-path.stdout.log").read_text().strip()) / "CampaignConsumer"
        pin_tool("consumer", binary)
        run("consumer", [str(binary)], consumer)
        run("consumer-host-checks", [str(binary), "--host-checks"], consumer)
        interruptions.check()
        result["status"] = "passed-focused-swift"
    except BaseException as error:
        failure = error
        result["error"] = repr(error)
        (output / "failure.log").write_text(traceback.format_exc())
    finally:
        checks = {}
        for label, root, original in (("inputs", source, before),
                                      ("installed", package, package_before),
                                      ("consumer", consumer, consumer_before)):
            try:
                after = manifest(root)
                write_json(output / f"{label}-after.json", after)
                compared = manifest(root, generated=True) if label != "inputs" else after
                checks[label] = original is not None and original == ordered(compared)
            except BaseException as error:
                checks[label] = False
                result[f"{label}_seal_error"] = repr(error)
        try:
            tools_after = {name: executable_identity(item["path"]) for name, item in tools_before.items()}
            write_json(output / "tools-after.json", tools_after)
            checks["tools"] = bool(tools_before) and tools_after == tools_before
        except BaseException as error:
            checks["tools"] = False
            result["tools_seal_error"] = repr(error)
        try:
            executing = Path(__file__).resolve(strict=True)
            driver_after = {"path": str(executing), "entry": dict(manifest(executing)[0], path="qualify.py")}
            write_json(output / "driver-after.json", driver_after)
            checks["driver"] = driver_before is not None and driver_before == driver_after
        except BaseException as error:
            checks["driver"] = False
            result["driver_seal_error"] = repr(error)
        result.update(immutable_inputs_unchanged=checks["inputs"],
                      installed_sources_unchanged=checks["installed"],
                      consumer_sources_unchanged=checks["consumer"], tools_unchanged=checks["tools"],
                      executing_driver_unchanged=checks["driver"], commands=len(commands))
        if not all(checks.values()):
            result["status"] = "failed-identity-or-preflight"
        terminal_interruptions, terminal_masked = freeze_terminal_signals(interruptions)
        result["interruptions"] = terminal_interruptions
        result["terminal_signal_boundary"] = {
            "masked": terminal_masked, "phase": "after identity checks, before terminal receipt",
            "policy": "signals after the blocked pending-set sample are outside the frozen exit decision"}
        if not terminal_masked:
            result["status"] = "failed-terminal-signal-boundary"
        if terminal_interruptions:
            result["status"] = "interrupted"
            result["process_loss_boundary"] = "catchable signals only; no SIGKILL or process-loss recovery"
        result["exit_code"] = 0 if failure is None and result["status"] == "passed-focused-swift" else 1
        write_json(output / "result.json", result)
    print(json.dumps(result, sort_keys=True), flush=True)
    raise SystemExit(result["exit_code"])


if __name__ == "__main__":
    main()

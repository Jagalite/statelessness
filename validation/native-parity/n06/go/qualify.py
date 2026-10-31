#!/usr/bin/env python3
"""Prepare immutable inputs, or execute focused Go qualification through Kyoto.

Preparation only copies/hashes files. Execute must be submitted to shared Kyoto;
it never schedules another job, changes queue policy, or retries a failed command.
"""
import argparse
from contextlib import contextmanager
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
import zipfile

ROOT = Path(__file__).resolve().parents[4]
DRIVER_MEMBER = "validation/native-parity/n06/go/qualify.py"
GO_CONFIG_KEYS = (
    "GOOS", "GOARCH", "GOVERSION", "GOROOT", "GOTOOLDIR", "GOTOOLCHAIN",
    "GOENV", "GOWORK", "GOFLAGS", "GOMOD", "GOPATH", "GOCACHE", "GOMODCACHE",
    "GOPROXY", "GOSUMDB", "CGO_ENABLED", "CC", "CXX", "AR", "GCCGO",
    "CGO_CFLAGS", "CGO_CPPFLAGS", "CGO_CXXFLAGS", "CGO_FFLAGS", "CGO_LDFLAGS",
    "GOEXPERIMENT", "GOAMD64", "GOARM", "GOARM64", "GOMIPS", "GOMIPS64",
    "GOPPC64", "GORISCV64", "GOWASM", "GO_EXTLINK_ENABLED",
)
SELECTIONS = (
    "go", "examples/native-campaign/go",
    # Baseline fixtures read by go/**/*_test.go; exclude other authors' N06
    # conformance/fixtures work and unrelated conformance runners/receipts.
    "conformance/append-checks-v1.json",
    "conformance/bfs-controls-v1.json",
    "conformance/canonical-value-codec-v1.json",
    "conformance/causal-evidence-v2.json",
    "conformance/checked-results-v1.json",
    "conformance/encode-buffers-v1.json",
    "conformance/native-binary-v1.json",
    "conformance/paired-v1.tsv",
    "conformance/replay-evidence-v1.json",
    "conformance/research-equivalence-v1.json",
    "conformance/search-v1-history.json",
    "conformance/search-v1.tsv",
)


def read_regular_bytes(path):
    # O_NONBLOCK prevents a substituted FIFO from hanging identity collection.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise RuntimeError(f"unsupported file type: {path}")
        return stream.read()


def sha(path):
    return hashlib.sha256(read_regular_bytes(path)).hexdigest()


def file_identity(path):
    # Hash bytes and mode/type from the same open regular file. No symlink/FIFO
    # following at the final component, including at a command's final check.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        mode = os.fstat(stream.fileno()).st_mode
        if not stat.S_ISREG(mode):
            raise RuntimeError(f"unsupported identity type: {path}")
        data = stream.read()
        return {"type": "file", "mode": oct(stat.S_IMODE(mode)),
                "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def driver_identity(source, frozen):
    report = {"member": DRIVER_MEMBER, "file": str(__file__), "errors": []}
    try:
        sealed = source / DRIVER_MEMBER
        report["expected_path"] = str(sealed)
        expected = frozen["members"][DRIVER_MEMBER] if frozen is not None else None
        report["expected"] = expected
        # Check the literal sealed member and its ancestors, not a resolved
        # replacement. Resolving alone would conceal a substituted symlink.
        observed = manifest(source, (DRIVER_MEMBER,))[DRIVER_MEMBER]
        report["sealed_member"] = observed
        if expected is None or expected.get("type") != "file" or observed != expected:
            report["errors"].append("sealed driver member identity mismatch")
        executing = Path(__file__).resolve(strict=True)
        report["canonical_path"] = str(executing)
        if executing != sealed:
            report["errors"].append("executing driver canonical path differs from sealed member")
        report["executing"] = file_identity(executing)
        if report["executing"] != expected:
            report["errors"].append("executing driver type/mode/bytes differ from sealed member")
    except Exception as error:
        report["errors"].append(f"executing driver identity: {error!r}")
    return report


def tool_identity(selected):
    selected = Path(selected).absolute()
    mode = selected.lstat().st_mode
    if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
        raise RuntimeError(f"unsupported selected tool type: {selected}")
    resolved = selected.resolve(strict=True)
    identity = file_identity(resolved)
    if not int(identity["mode"], 8) & 0o111:
        raise RuntimeError(f"tool is not executable: {resolved}")
    return {"selected_path": str(selected), "path": str(resolved),
            "selected_type": "symlink" if stat.S_ISLNK(mode) else "file",
            "selected_mode": oct(stat.S_IMODE(mode)),
            "selected_link": os.readlink(selected) if stat.S_ISLNK(mode) else None,
            "file": identity}


class Provenance:
    def __init__(self, source, frozen, work, env):
        self.source, self.frozen, self.work = source, frozen, work
        self.tools = {}
        self.env = dict(env)
        self.required = ["python", "go"]

    def pin(self):
        # Select CC explicitly; inherited CC/wrapper arguments are not accepted.
        # Go's quoted-command syntax needs quoting even for an absolute path
        # when the installation directory contains spaces.
        for name, selected in (("python", sys.executable),
                               ("go", shutil.which("go", path=self.env.get("PATH", "")))):
            if not selected:
                raise RuntimeError(f"{name} executable is unavailable")
            self.tools[name] = tool_identity(selected)
        if sys.platform == "darwin":
            self.required.append("xcrun")
            self.tools["xcrun"] = tool_identity("/usr/bin/xcrun")
        else:
            selected = shutil.which("cc", path=self.env.get("PATH", ""))
            if not selected:
                raise RuntimeError("C compiler executable is unavailable")
            self.pin_compiler(selected)

    def pin_compiler(self, selected):
        self.required.append("cc")
        if not Path(selected).is_absolute():
            raise RuntimeError("selected C compiler path is not absolute")
        self.tools["cc"] = tool_identity(selected)
        compiler = self.tools["cc"]["path"]
        if sys.platform == "darwin" and Path(compiler).parent == Path("/usr/bin"):
            raise RuntimeError("developer compiler discovery returned a system selector/shim")
        if any(c in compiler for c in "\"'\n\r"):
            raise RuntimeError("compiler paths containing quotes/newlines are unsupported")
        self.env["CC"] = '"' + compiler + '"' if any(c.isspace() for c in compiler) else compiler

    def argv(self, tool, arguments):
        path = (str(self.work / "campaign-example") if tool == "installed"
                else self.tools[tool]["path"])
        return [path, *arguments]

    def snapshot(self, tool=None, name=None, argv=None, env=None):
        driver = driver_identity(self.source, self.frozen)
        report = {"driver": driver, "tools": {}, "errors": list(driver["errors"])}
        for key in self.required:
            entry = {"expected": self.tools.get(key)}
            report["tools"][key] = entry
            try:
                if entry["expected"] is None:
                    raise RuntimeError("no pinned identity")
                entry["observed"] = tool_identity(entry["expected"]["selected_path"])
                if entry["observed"] != entry["expected"]:
                    raise RuntimeError("path/type/mode/bytes changed")
            except Exception as error:
                entry["error"] = repr(error)
                report["errors"].append(f"{key} tool identity: {error!r}")
        if tool is not None:
            try:
                if not argv or argv[0] != self.argv(tool, [])[0] or not Path(argv[0]).is_absolute():
                    raise RuntimeError("command does not use pinned absolute executable")
                if tool == "python" and argv[1:] != ["-I", str(self.source / DRIVER_MEMBER), "guard-tests", str(self.work)]:
                    raise RuntimeError("nested guard does not invoke sealed inner driver")
                expected_env = dict(self.env)
                if name in ("race-runtime", "toolchain-env-race-before", "toolchain-env-race-after"):
                    expected_env["CGO_ENABLED"] = "1"
                if name in ("installed-build", "installed-modules"):
                    expected_env["GOPROXY"] = (self.work / "proxy").as_uri()
                if name == "all-tests-cgo0":
                    expected_env["STATELESS_GO_CAMPAIGN_ARTIFACTS"] = str(self.work.parent / "artifacts")
                if name in ("fresh-replay-original", "fresh-replay-minimized"):
                    variant = name.removeprefix("fresh-replay-")
                    expected_env["STATELESS_GO_CAMPAIGN_REPLAY"] = str(self.work.parent / "artifacts" / ("campaign-fault-" + variant + ".sttrace"))
                if env != expected_env:
                    raise RuntimeError("command environment differs from pinned configuration")
            except Exception as error:
                report["errors"].append(f"invocation/configuration: {error!r}")
        return report


def require_identity(report):
    if report["errors"]:
        raise RuntimeError("; ".join(report["errors"]))


def manifest(root, selections):
    result = {}

    def visit(path):
        mode = path.lstat().st_mode
        name = path.relative_to(root).as_posix()
        if stat.S_ISDIR(mode):
            result[name] = {"type": "directory", "mode": oct(stat.S_IMODE(mode))}
            for child in sorted(path.iterdir()):
                visit(child)
        elif stat.S_ISREG(mode):
            data = read_regular_bytes(path)
            result[name] = {"type": "file", "mode": oct(stat.S_IMODE(mode)),
                            "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        else:
            raise RuntimeError(f"unsupported source member type: {path}")

    for name in selections:
        source = root / name
        # Explicit file selections must not traverse a symlinked directory.
        for parent in source.relative_to(root).parents:
            if not stat.S_ISDIR((root / parent).lstat().st_mode):
                raise RuntimeError(f"unsupported source directory: {root / parent}")
        visit(source)
    return result


def write_json(path, data):
    # Keep the previous complete command receipt if a write is interrupted.
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("x") as stream:
        stream.write(json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class Cancelled(BaseException):
    def __init__(self, signum):
        self.signum = signum
        super().__init__(f"cancelled by {signal.Signals(signum).name}")


class Cancellation:
    """Defer catchable signals only across spawn handoff and durable cleanup."""
    def __init__(self):
        self.deferred = False
        self.signals = []

    def handle(self, signum, _frame):
        self.signals.append(signum)
        if not self.deferred:
            raise Cancelled(signum)

    def checkpoint(self):
        if self.signals:
            raise Cancelled(self.signals[0])

    @contextmanager
    def defer(self):
        previous = self.deferred
        self.deferred = True
        try:
            yield
        finally:
            self.deferred = previous

    @contextmanager
    def installed(self):
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            for sig in previous:
                signal.signal(sig, self.handle)
            yield self
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def require_nonreaping_wait():
    if not callable(getattr(os, "waitid", None)) or any(not hasattr(os, name) for name in ("P_PID", "WEXITED", "WNOWAIT", "WNOHANG", "CLD_EXITED", "CLD_KILLED", "CLD_DUMPED")):
        raise RuntimeError("owned-group cleanup requires POSIX waitid/WNOWAIT")


def preflight_wait_support():
    require_nonreaping_wait()
    # Our own PID cannot be our child. ECHILD is the expected result, while
    # ENOSYS/EINVAL (unsupported syscall/flags) reject before any child spawn.
    try:
        os.waitid(os.P_PID, os.getpid(), os.WEXITED | os.WNOWAIT | os.WNOHANG)
    except ChildProcessError:
        return
    raise RuntimeError("waitid/WNOWAIT preflight did not reject a non-child")


def child_status(pid):
    # None is a live child, a result is an exited but unreaped child; ECHILD
    # means ownership is lost and MUST prevent any later numeric group signal.
    require_nonreaping_wait()
    return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)


def wait_owned(process, timeout):
    deadline = time.monotonic() + timeout
    while True:
        observed = child_status(process.pid)
        if observed is not None:
            if observed.si_code == os.CLD_EXITED:
                return observed.si_status
            if observed.si_code in (os.CLD_KILLED, os.CLD_DUMPED):
                return -observed.si_status
            raise RuntimeError(f"unexpected waitid result: {observed!r}")
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(process.args, timeout)
        time.sleep(0.05)


def stop_group(process):
    """Signal only the session created for this command, then reap its leader."""
    actions = []
    try:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                child_status(process.pid)
            except OSError as error:
                actions.append({"ownership_error": repr(error)})
                break
            try:
                os.killpg(process.pid, sig)
                actions.append({"signal": signal.Signals(sig).name, "sent": True})
            except ProcessLookupError:
                actions.append({"signal": signal.Signals(sig).name, "sent": False})
            except OSError as error:
                actions.append({"signal": signal.Signals(sig).name, "error": repr(error)})
            if sig == signal.SIGTERM:
                # Do not reap the leader before KILL: its PID keeps the group
                # identity reserved, including when descendants ignore TERM.
                time.sleep(2)
    finally:
        process.wait()
    return actions


def run_command(out, results, env, cancellation, name, argv, cwd, extra=None, timeout=600,
                provenance=None, tool=None):
    command_env = dict(env, **(extra or {}))
    record = {"name": name, "argv": argv, "cwd": str(cwd), "started": time.time(),
              "environment": {key: value for key, value in command_env.items() if key not in ("HOME", "USER", "LOGNAME")}}
    log_path = out / "logs" / (name + ".log")
    process = None
    log = None
    try:
        preflight_wait_support()
        if provenance is not None:
            record["provenance_before"] = provenance.snapshot(tool, name, argv, command_env)
            require_identity(record["provenance_before"])
        # A signal between Popen returning and assignment must not lose the
        # sole handle to this command's new session.
        with cancellation.defer():
            log = log_path.open("xb")
            process = subprocess.Popen(argv, cwd=cwd, env=command_env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            record["pid"] = process.pid
        cancellation.checkpoint()
        record["exit_code"] = wait_owned(process, timeout)
    except subprocess.TimeoutExpired:
        record.update({"exit_code": 124, "timeout": timeout})
    except BaseException as error:
        record.update({"exit_code": 125, "error_type": type(error).__name__, "error": repr(error)})
        if isinstance(error, Cancelled):
            record.update({"exit_code": 128 + error.signum, "signal": error.signum})
        elif isinstance(error, KeyboardInterrupt):
            record["exit_code"] = 130
        raise
    finally:
        with cancellation.defer():
            try:
                if process is not None:
                    # Also clean up after normal/nonzero exit: descendants may
                    # still inhabit the group. The leader has NOT been reaped.
                    try:
                        record["cleanup"] = stop_group(process)
                        record["process_returncode"] = process.returncode
                    except Exception as error:
                        record["cleanup"] = [{"error": repr(error)}]
                    if any("error" in action or "ownership_error" in action for action in record["cleanup"]):
                        record["exit_code"] = 125
            finally:
                if log is not None:
                    log.close()
                record["finished"] = time.time()
                try:
                    record["log_sha256"] = sha(log_path)
                except Exception as error:
                    record["log_error"] = repr(error)
                    record["exit_code"] = 125
                if provenance is not None:
                    record["provenance_after"] = provenance.snapshot(tool, name, argv, command_env)
                    if record["provenance_after"]["errors"]:
                        record["exit_code"] = 125
                record["signals"] = list(cancellation.signals)
                if cancellation.signals and record.get("exit_code", 0) == 0:
                    record["exit_code"] = 128 + cancellation.signals[0]
                results.append(record)
                write_json(out / ("command-" + name + ".json"), record)
                write_json(out / "commands.json", results)
    cancellation.checkpoint()
    print(json.dumps(record), flush=True)
    if record["exit_code"]:
        raise RuntimeError(f"{name} failed: see complete log")


def identity_snapshot(out, expected_sha256, frozen=None, initial=None):
    report = {"expected_frozen_sha256": expected_sha256, "source_unchanged": False, "errors": []}
    data = {}
    for name in ("frozen", "origin"):
        try:
            data[name] = read_regular_bytes(out / (name + ".json"))
            report[name + "_sha256"] = hashlib.sha256(data[name]).hexdigest()
        except Exception as error:
            report["errors"].append(f"{name}: {error!r}")
    if not expected_sha256 or report.get("frozen_sha256") != expected_sha256:
        report["errors"].append("frozen manifest SHA-256 differs from queued identity")
    elif frozen is None:
        try:
            # Parse exactly the bytes hashed above; retain this object to the end.
            candidate = json.loads(data["frozen"])
            if (candidate["schema"] != "n06-go-focused-v1" or candidate["selections"] != ["."]
                    or not isinstance(candidate["members"], dict)
                    or not isinstance(candidate["origin_sha256"], str)):
                raise ValueError("invalid frozen manifest schema")
            frozen = candidate
        except Exception as error:
            report["errors"].append(f"frozen parse: {error!r}")
    if frozen is not None:
        report["expected_origin_sha256"] = frozen["origin_sha256"]
        if report.get("origin_sha256") != frozen["origin_sha256"]:
            report["errors"].append("source origin identity differs from frozen manifest")
        try:
            members = manifest(out / "source", frozen["selections"])
            report["source_unchanged"] = members == frozen["members"]
            if not report["source_unchanged"]:
                report["errors"].append("frozen source identity mismatch")
                report["changed_members"] = {
                    name: {"expected": frozen["members"].get(name), "observed": members.get(name)}
                    for name in sorted(frozen["members"].keys() | members.keys())
                    if frozen["members"].get(name) != members.get(name)}
        except Exception as error:
            report["errors"].append(f"source manifest: {error!r}")
    else:
        report["errors"].append("source identity unavailable without a valid pinned manifest")
    if initial is not None:
        for name in ("frozen_sha256", "origin_sha256"):
            if report.get(name) != initial.get(name):
                report["errors"].append(f"{name} changed since startup")
    return frozen, report


def prepare(out):
    out.mkdir(parents=True, exist_ok=False)
    source = out / "source"
    selected = (*SELECTIONS, DRIVER_MEMBER)
    before = manifest(ROOT, selected)
    for relative, entry in before.items():
        target = source / relative
        if entry["type"] == "directory":
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(int(entry["mode"], 8))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
    if before != manifest(ROOT, selected) or before != manifest(source, selected):
        raise RuntimeError("source changed during preparation")
    write_json(out / "origin.json", {"base": "2bcdd8bfd19744f50b6ed3819ac2451a8de101be", "root": str(ROOT), "files": before})
    for path in source.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
        elif path.is_dir():
            path.chmod(0o555)
    source.chmod(0o555)
    frozen = manifest(source, (".",))
    write_json(out / "frozen.json", {"schema": "n06-go-focused-v1", "selections": ["."], "members": frozen, "origin_sha256": sha(out / "origin.json")})
    print(json.dumps({"out": str(out), "manifest_sha256": sha(out / "frozen.json"), "members": len(frozen), "status": "prepared_not_executed"}))


def execute(out, expected_sha256):
    with Cancellation().installed() as cancellation:
        return execute_once(out, expected_sha256, cancellation)


def execute_once(out, expected_sha256, cancellation):
    # Claim once before inspecting inputs, so even startup identity failures
    # receive durable evidence and a failed result without overwriting a run.
    logs = out / "logs"
    logs.mkdir(exist_ok=False)
    source = out / "source"
    artifacts = out / "artifacts"
    work = out / "work"
    env = {key: value for key, value in os.environ.items() if key in ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "SDKROOT", "DEVELOPER_DIR", "SYSTEMROOT")}
    env.update({"GOWORK": "off", "GOENV": "off", "GOFLAGS": "", "GOTOOLCHAIN": "local", "GOPROXY": "off", "GOSUMDB": "off", "GOPATH": str(work / "gopath"), "GOMODCACHE": str(work / "modcache"), "GOCACHE": str(work / "cache"), "TMPDIR": str(work / "tmp"), "CGO_ENABLED": "0", "GOMAXPROCS": "2"})
    results = []

    def command(name, tool, arguments, cwd, extra=None, timeout=600):
        run_command(out, results, provenance.env, cancellation, name,
                    provenance.argv(tool, arguments), cwd, extra, timeout,
                    provenance=provenance, tool=tool)

    def configuration(label):
        observations = {}
        for profile, enabled in (("cgo0", "0"), ("race", "1")):
            name = "toolchain-env-" + profile + "-" + label
            command(name, "go", ["env", "-json", *GO_CONFIG_KEYS], source / "go", {"CGO_ENABLED": enabled})
            config = json.loads(read_regular_bytes(out / "logs" / (name + ".log")))
            observations[profile] = config
            write_json(out / ("toolchain-config-" + label + ".json"), observations)
            if set(config) != set(GO_CONFIG_KEYS) or not all(isinstance(value, str) for value in config.values()):
                raise RuntimeError("invalid Go toolchain configuration response")
            # Independently assert critical effective values, not just before/
            # after equality of two accidentally uncontrolled invocations.
            expected = {key: provenance.env[key] for key in (
                "CC", "GOENV", "GOFLAGS", "GOTOOLCHAIN", "GOPATH",
                "GOCACHE", "GOMODCACHE", "GOPROXY", "GOSUMDB")}
            expected["CGO_ENABLED"] = enabled
            if any(config[key] != value for key, value in expected.items()):
                raise RuntimeError(f"{profile} Go configuration does not enforce pinned settings")
            # Go may report no selected work file as empty even with the
            # explicitly enforced GOWORK=off environment at every launch.
            if config["GOWORK"] not in ("", "off"):
                raise RuntimeError("Go selected a workspace despite GOWORK=off")
        return observations

    module = "github.com/Jagalite/statelessness/go"
    version = "v0.0.0-n06"
    escaped = "".join("!" + c.lower() if c.isupper() else c for c in module)
    consumer = work / "consumer"
    installed = work / "modcache" / (escaped + "@" + version)

    def capture_installed(label):
        observations = {}
        for name, path in {"consumer": consumer, "module_cache": work / "modcache", "installed_module": installed, "proxy": work / "proxy", "executable": work / "campaign-example"}.items():
            try:
                mode = path.lstat().st_mode
                observations[name] = {"present": True, "members": manifest(path, (".",)) if stat.S_ISDIR(mode) else manifest(path.parent, (path.name,))}
            except FileNotFoundError:
                observations[name] = {"present": False}
            except Exception as error:
                observations[name] = {"present": True, "manifest_error": str(error)}
        write_json(out / (label + ".json"), observations)
        return observations

    status = "failed"
    errors = []
    frozen = None
    initial_identity = None
    before_run = None
    provenance = Provenance(source, None, work, env)
    config_before = None
    config_after = None
    try:
        with cancellation.defer():
            write_json(out / "invocation.json", {"argv": list(sys.argv),
                       "orig_argv": list(getattr(sys, "orig_argv", [])),
                       "file": str(__file__), "executable": sys.executable,
                       "cwd": str(Path.cwd()), "pid": os.getpid(),
                       "expected_frozen_sha256": expected_sha256})
            capture_installed("installed-before")
        frozen, initial_identity = identity_snapshot(out, expected_sha256)
        write_json(out / "identity-before.json", initial_identity)
        provenance.frozen = frozen
        driver_before = driver_identity(source, frozen)
        write_json(out / "driver-before.json", driver_before)
        require_identity(initial_identity)
        require_identity(driver_before)
        provenance.pin()
        toolchain_before = provenance.snapshot()
        write_json(out / "toolchain-before.json", toolchain_before)
        require_identity(toolchain_before)
        cancellation.checkpoint()
        artifacts.mkdir()
        work.mkdir()
        for name in ("cache", "modcache", "gopath", "tmp"):
            (work / name).mkdir()
        if sys.platform == "darwin":
            command("c-compiler-select-before", "xcrun", ["--find", "clang"], source)
            selected = read_regular_bytes(out / "logs/c-compiler-select-before.log").decode().strip()
            provenance.pin_compiler(selected)
            command("sdk-select", "xcrun", ["--show-sdk-path"], source)
            sdk = read_regular_bytes(out / "logs/sdk-select.log").decode().strip()
            if not Path(sdk).is_absolute() or not Path(sdk).is_dir():
                raise RuntimeError("developer SDK discovery did not return an absolute directory")
            provenance.env["SDKROOT"] = str(Path(sdk).resolve(strict=True))
        toolchain_before = provenance.snapshot()
        write_json(out / "toolchain-before.json", toolchain_before)
        require_identity(toolchain_before)
        write_json(out / "toolchain.json", {"tools": provenance.tools,
                   "environment": {k: v for k, v in provenance.env.items() if k not in ("HOME", "USER", "LOGNAME")},
                   "developer_dir": provenance.env.get("DEVELOPER_DIR"),
                   "sdkroot": provenance.env.get("SDKROOT"), "config_keys": GO_CONFIG_KEYS})
        command("driver-guards", "python", ["-I", str(source / DRIVER_MEMBER), "guard-tests", str(work)], source)
        command("toolchain", "go", ["version"], source / "go")
        command("c-compiler", "cc", ["--version"], source / "go")
        config_before = configuration("before")
        command("all-tests-cgo0", "go", ["test", "-v", "-count=1", "-timeout=5m", "./..."], source / "go", {"STATELESS_GO_CAMPAIGN_ARTIFACTS": str(artifacts)})
        for variant in ("original", "minimized"):
            command("fresh-replay-" + variant, "go", ["test", "-v", "-count=1", "-run", "^TestCampaignLifecyclePersistedFaultReplay$", "."], source / "go", {"STATELESS_GO_CAMPAIGN_REPLAY": str(artifacts / ("campaign-fault-" + variant + ".sttrace"))})
        command("race-runtime", "go", ["test", "-race", "-v", "-count=1", "-timeout=3m", "-run", "^TestCampaign(Seeds|Deterministic|Guided|Error|Real|Rendezvous|Controls|Worker|Failure|Report|Zero|Feedback|Auto|Cleanup|Native)", "."], source / "go", {"CGO_ENABLED": "1"})
        command("vet", "go", ["vet", "./..."], source / "go")
        proxy = work / "proxy"
        vdir = proxy / escaped / "@v"
        vdir.mkdir(parents=True)
        (vdir / (version + ".mod")).write_bytes((source / "go/go.mod").read_bytes())
        write_json(vdir / (version + ".info"), {"Version": version, "Time": "2026-10-22T00:00:00Z"})
        (vdir / "list").write_text(version + "\n")
        with zipfile.ZipFile(vdir / (version + ".zip"), "x", zipfile.ZIP_DEFLATED) as package:
            for path in sorted((source / "go").rglob("*")):
                if path.is_file():
                    entry = zipfile.ZipInfo(module + "@" + version + "/" + path.relative_to(source / "go").as_posix(), (2026, 10, 22, 0, 0, 0))
                    entry.external_attr = 0o100444 << 16
                    package.writestr(entry, path.read_bytes())
        write_json(out / "package.json", {"module": module, "version": version, "zip_sha256": sha(vdir / (version + ".zip"))})
        shutil.copytree(source / "examples/native-campaign/go", consumer)
        consumer.chmod(0o755)
        for path in consumer.iterdir():
            path.chmod(0o644)
        proxyenv = {"GOPROXY": proxy.as_uri()}
        command("installed-build", "go", ["build", "-mod=mod", "-o", str(work / "campaign-example"), "."], consumer, proxyenv)
        command("installed-public-host-test", "go", ["test", "-mod=mod", "-v", "-count=1", "-timeout=30s", "-run", "^TestPublicCampaign(CancellationDrainsClaimedWorkers|OutOfOrderAndJobBoundInputs)$", "."], consumer, proxyenv)
        command("installed-modules", "go", ["list", "-m", "-json", "all"], consumer, proxyenv)
        before_run = capture_installed("installed-before-run")
        command("installed-example", "installed", [], consumer)
        command("installed-host", "installed", ["--host-check"], consumer)
        command("installed-ordinary", "installed", ["--ordinary"], consumer)
        command("installed-ordinary-host", "installed", ["--ordinary", "--host-check"], consumer)
        installed_files = manifest(installed, (".",))
        package_files = manifest(source / "go", (".",))
        content = lambda files: {name: (item["type"], item.get("size"), item.get("sha256")) for name, item in files.items()}
        if content(installed_files) != content(package_files):
            raise RuntimeError("installed module differs from frozen package")
        write_json(out / "installed.json", {"binary_sha256": sha(work / "campaign-example"), "module_cache": installed_files, "consumer": manifest(consumer, (".",))})
        config_after = configuration("after")
        if config_after != config_before:
            raise RuntimeError("Go toolchain configuration changed since startup")
        if sys.platform == "darwin":
            command("c-compiler-select-after", "xcrun", ["--find", "clang"], source)
            selected = read_regular_bytes(out / "logs/c-compiler-select-after.log").decode().strip()
            if not Path(selected).is_absolute() or tool_identity(selected) != provenance.tools["cc"]:
                raise RuntimeError("selected developer C compiler changed since startup")
        status = "passed"
    except BaseException as error:
        status = "failed"
        errors.append({"type": type(error).__name__, "message": str(error)})
        print(str(error), file=sys.stderr)
    finally:
        # Repeated TERM/INT during cleanup must not cut off observations or the
        # terminal receipt. SIGKILL / machine loss cannot be made catchable.
        with cancellation.defer():
            final_provenance = provenance.snapshot()
            write_json(out / "driver-after.json", final_provenance["driver"])
            write_json(out / "toolchain-after.json", final_provenance)
            errors.extend({"type": "IdentityError", "message": message} for message in final_provenance["errors"])
            try:
                after_run = capture_installed("installed-after")
                if before_run is not None and before_run != after_run:
                    errors.append({"type": "IdentityError", "message": "installed identities changed during example execution"})
                if any("manifest_error" in item for item in after_run.values()):
                    errors.append({"type": "IdentityError", "message": "installed identity manifest rejected a member"})
            except Exception as error:
                errors.append({"type": "IdentityError", "message": f"installed final observation: {error!r}"})
            _, final_identity = identity_snapshot(out, expected_sha256, frozen, initial_identity)
            write_json(out / "identity-after.json", final_identity)
            if final_identity["errors"]:
                errors.extend({"type": "IdentityError", "message": message} for message in final_identity["errors"])
            if errors or cancellation.signals:
                status = "failed"
            result = {"status": status, "errors": errors, "signals": list(cancellation.signals),
                      "source_unchanged": final_identity["source_unchanged"],
                      "expected_frozen_sha256": expected_sha256,
                      "frozen_sha256": final_identity.get("frozen_sha256"),
                      "origin_sha256": final_identity.get("origin_sha256"), "commands": len(results),
                      "driver_unchanged": not final_provenance["driver"]["errors"],
                      "toolchain_unchanged": not final_provenance["errors"],
                      "toolchain_config_unchanged": config_before is not None and config_before == config_after,
                      "scope": "focused Go package, race/vet, private lifecycle and fresh module example; no Rust/native comparison or N05 closure"}
            write_json(out / "result.json", result)
            if cancellation.signals != result["signals"]:
                result.update({"status": "failed", "signals": list(cancellation.signals)})
                write_json(out / "result.json", result)
    return 0 if result["status"] == "passed" else 1


def guard_tests(work):
    """Driver regressions, run only as an internal queued-job phase."""
    import tempfile
    from types import SimpleNamespace
    import unittest
    from unittest.mock import Mock, call, patch

    class DriverGuards(unittest.TestCase):
        def setUp(self):
            self.temporary = tempfile.TemporaryDirectory(prefix="driver-guard-", dir=work)
            self.addCleanup(self.temporary.cleanup)
            self.root = Path(self.temporary.name)

        def fixture(self, name):
            out = self.root / name
            source = out / "source"
            (source / "go").mkdir(parents=True)
            (source / "go/go.mod").write_text("module github.com/Jagalite/statelessness/go\n\ngo 1.23\n")
            example = source / "examples/native-campaign/go"
            example.mkdir(parents=True)
            (example / "go.mod").write_text("module example\n")
            driver = source / DRIVER_MEMBER
            driver.parent.mkdir(parents=True)
            driver.write_bytes(b"sealed guard driver\n")
            driver.chmod(0o444)
            write_json(out / "origin.json", {"guard_fixture": True})
            write_json(out / "frozen.json", {"schema": "n06-go-focused-v1", "selections": ["."],
                       "members": manifest(source, (".",)), "origin_sha256": sha(out / "origin.json")})
            return out, sha(out / "frozen.json")

        def test_membership_special_modes_and_rejected_types(self):
            member = self.root / "file"
            member.write_bytes(b"fixture")
            member.chmod(0o2640)
            directory = self.root / "empty"
            directory.mkdir()
            directory.chmod(0o1750)
            before = manifest(self.root, (".",))
            self.assertEqual(before["file"]["mode"], "0o2640")
            self.assertEqual(before["empty"]["mode"], "0o1750")
            (self.root / "extra").mkdir()
            self.assertNotEqual(before, manifest(self.root, (".",)))
            link = self.root / "link"
            link.symlink_to(member)
            with self.assertRaises(RuntimeError):
                manifest(self.root, (".",))
            link.unlink()
            os.mkfifo(link)
            with self.assertRaises(RuntimeError):
                manifest(self.root, (".",))

        def test_hash_and_parse_share_bytes_and_end_rechecks_metadata(self):
            out, expected = self.fixture("same-bytes")
            original_read = read_regular_bytes

            def swapped_after_read(path):
                data = original_read(path)
                if path == out / "frozen.json":
                    path.write_bytes(b"changed after read")
                return data

            with patch.dict(globals(), read_regular_bytes=swapped_after_read):
                frozen, initial = identity_snapshot(out, expected)
            self.assertEqual(initial["errors"], [])
            (out / "origin.json").write_bytes(b"changed origin")
            _, final = identity_snapshot(out, expected, frozen, initial)
            self.assertIn("frozen manifest SHA-256 differs from queued identity", final["errors"])
            self.assertIn("source origin identity differs from frozen manifest", final["errors"])
            self.assertEqual(final["expected_frozen_sha256"], expected)

        def test_cancel_timeout_and_spawn_handoff_persist_receipts(self):
            for kind in ("interrupt", "base-exception", "term", "term-during-spawn", "timeout"):
                with self.subTest(kind=kind):
                    out = self.root / kind
                    (out / "logs").mkdir(parents=True)
                    cancellation = Cancellation()
                    process = Mock(pid=987654, returncode=-signal.SIGKILL)
                    error = {"interrupt": KeyboardInterrupt(), "base-exception": SystemExit(7),
                             "timeout": subprocess.TimeoutExpired(["guard"], 1)}.get(kind)

                    def interrupted_wait(_process, timeout=None):
                        if kind == "term":
                            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                        raise error

                    process.wait.return_value = process.returncode

                    def spawn(*_args, **kwargs):
                        self.assertTrue(kwargs["start_new_session"])
                        kwargs["stdout"].write(b"raw output survives\n")
                        kwargs["stdout"].flush()
                        if kind == "term-during-spawn":
                            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                        return process

                    def observe(_kind, pid, _flags):
                        if pid == os.getpid():
                            raise ChildProcessError("preflight non-child")
                        return None

                    with patch.object(subprocess, "Popen", side_effect=spawn), \
                            patch.object(os, "killpg") as kill, patch.object(time, "sleep"), \
                            patch.object(os, "waitid", side_effect=observe), \
                            patch.dict(globals(), wait_owned=interrupted_wait), \
                            cancellation.installed():
                        expected_error = {"interrupt": KeyboardInterrupt, "base-exception": SystemExit,
                                          "term": Cancelled, "term-during-spawn": Cancelled,
                                          "timeout": RuntimeError}[kind]
                        with self.assertRaises(expected_error):
                            run_command(out, [], {}, cancellation, "cancel", ["guard"], out, timeout=1)
                    self.assertEqual(kill.call_args_list, [call(process.pid, signal.SIGTERM), call(process.pid, signal.SIGKILL)])
                    self.assertIn(call(), process.wait.call_args_list)
                    self.assertEqual((out / "logs/cancel.log").read_bytes(), b"raw output survives\n")
                    receipt = json.loads((out / "command-cancel.json").read_bytes())
                    self.assertEqual(receipt["exit_code"], {"interrupt": 130, "base-exception": 125,
                                     "term": 143, "term-during-spawn": 143, "timeout": 124}[kind])
                    self.assertEqual(receipt["log_sha256"], sha(out / "logs/cancel.log"))
                    self.assertEqual(json.loads((out / "commands.json").read_bytes()), [receipt])

        def test_nonreaping_wait_and_all_exit_cleanup_order(self):
            for kind in ("normal", "nonzero", "timeout", "cancel", "lost-ownership"):
                with self.subTest(kind=kind):
                    out = self.root / ("ownership-" + kind)
                    (out / "logs").mkdir(parents=True)
                    cancellation = Cancellation()
                    events = []
                    process = Mock(pid=987654, args=["fixture"], returncode=None)
                    reaped = False
                    observations = 0

                    def observe(_kind, pid, flags):
                        nonlocal observations
                        if pid == os.getpid():
                            raise ChildProcessError("preflight non-child")
                        self.assertFalse(reaped)
                        self.assertTrue(flags & os.WNOWAIT)
                        observations += 1
                        events.append("observe")
                        if kind == "lost-ownership" and observations > 1:
                            raise ChildProcessError("another reaper took the child")
                        if kind == "cancel" and observations == 1:
                            cancellation.handle(signal.SIGTERM, None)
                        if kind in ("cancel", "timeout"):
                            return None
                        return SimpleNamespace(si_code=os.CLD_EXITED, si_status=17 if kind == "nonzero" else 0)

                    def kill(pid, sig):
                        self.assertEqual(pid, process.pid)
                        self.assertFalse(reaped)
                        self.assertEqual(events[-1], "observe")
                        events.append(sig)

                    def reap():
                        nonlocal reaped
                        self.assertFalse(reaped)
                        reaped = True
                        events.append("reap")
                        process.returncode = 17 if kind == "nonzero" else 0
                        return process.returncode

                    process.wait.side_effect = reap
                    with patch.object(subprocess, "Popen", return_value=process), \
                            patch.object(os, "waitid", side_effect=observe), \
                            patch.object(os, "killpg", side_effect=kill), \
                            patch.object(time, "sleep"), patch.object(time, "monotonic", side_effect=[0, 2]):
                        if kind == "normal":
                            run_command(out, [], {}, cancellation, "owned", ["fixture"], out, timeout=1)
                        else:
                            with self.assertRaises(Cancelled if kind == "cancel" else RuntimeError):
                                run_command(out, [], {}, cancellation, "owned", ["fixture"], out, timeout=1)
                    self.assertEqual(events[-1], "reap")
                    if kind == "lost-ownership":
                        self.assertNotIn(signal.SIGTERM, events)
                        self.assertNotIn(signal.SIGKILL, events)
                    else:
                        self.assertLess(events.index(signal.SIGTERM), events.index(signal.SIGKILL))
                        self.assertLess(events.index(signal.SIGKILL), events.index("reap"))
                    receipt = json.loads((out / "command-owned.json").read_bytes())
                    self.assertEqual(receipt["exit_code"], {"normal": 0, "nonzero": 17,
                                     "timeout": 124, "cancel": 143, "lost-ownership": 125}[kind])

        def test_unsupported_wait_fails_before_spawn(self):
            out = self.root / "unsupported-wait"
            (out / "logs").mkdir(parents=True)
            with patch.object(os, "waitid", None), patch.object(subprocess, "Popen") as spawn:
                with self.assertRaisesRegex(RuntimeError, "waitid/WNOWAIT"):
                    run_command(out, [], {}, Cancellation(), "unsupported", ["fixture"], out)
                spawn.assert_not_called()
            receipt = json.loads((out / "command-unsupported.json").read_bytes())
            self.assertEqual(receipt["exit_code"], 125)

        def test_tool_alias_retarget_same_bytes_and_compiler_rejections(self):
            first, second, alias = (self.root / name for name in ("first", "second", "selected"))
            first.write_bytes(b"identical tool bytes")
            first.chmod(0o755)
            shutil.copy2(first, second)
            alias.symlink_to(first)
            before = tool_identity(alias)
            alias.unlink()
            alias.symlink_to(second)
            after = tool_identity(alias)
            self.assertEqual(before["file"], after["file"])
            self.assertNotEqual(before["path"], after["path"])
            out, expected = self.fixture("alias-retarget")
            frozen, _ = identity_snapshot(out, expected)
            provenance = Provenance(out / "source", frozen, out / "work", {})
            provenance.required = ["cc"]
            provenance.tools = {"cc": before}
            with patch.dict(globals(), __file__=str(out / "source" / DRIVER_MEMBER)):
                self.assertIn("error", provenance.snapshot()["tools"]["cc"])
            with self.assertRaisesRegex(RuntimeError, "not absolute"):
                provenance.pin_compiler("cc --wrapper")
            quoted = self.root / "compiler'quote"
            shutil.copy2(first, quoted)
            with self.assertRaisesRegex(RuntimeError, "quotes/newlines"):
                provenance.pin_compiler(str(quoted))
            with patch.object(sys, "platform", "darwin"), \
                    patch.dict(globals(), tool_identity=lambda _: dict(after, path="/usr/bin/clang")):
                with self.assertRaisesRegex(RuntimeError, "selector/shim"):
                    provenance.pin_compiler("/usr/bin/clang")

        def mock_execution(self, out, expected, *, executing=None, fault=None, darwin=False,
                           config_fault=False, launch_fault=None):
            """Keep execute/run_command/identity real; no executable is launched."""
            toolroot = self.root / (out.name + " tool installation")
            toolroot.mkdir()
            paths = {}
            for name in ("python", "go", "cc", "xcrun"):
                paths[name] = toolroot / name
                paths[name].write_bytes((name + " executable fixture").encode())
                paths[name].chmod(0o755)
            sdk = toolroot / "SDK"
            sdk.mkdir()
            driver = out / "source" / DRIVER_MEMBER
            calls, children = [], {}
            original_write, original_identity = write_json, tool_identity
            original_snapshot = Provenance.snapshot

            def snapshot(provenance, tool=None, name=None, argv=None, env=None):
                if launch_fault and name == "race-runtime":
                    # Corrupt the actual launch-bound arguments/environment;
                    # the production check must reject before Popen.
                    if launch_fault == "argv":
                        argv[0] = "go"
                    elif launch_fault == "cc":
                        env["CC"] = "cc"
                    elif launch_fault == "cgo":
                        env["CGO_ENABLED"] = "0"
                    else:
                        env["GOTOOLCHAIN"] = "auto"
                return original_snapshot(provenance, tool, name, argv, env)

            def spawn(argv, **kwargs):
                name = Path(kwargs["stdout"].name).stem
                env = kwargs["env"]
                calls.append((name, list(argv), dict(env)))
                self.assertTrue(kwargs["start_new_session"])
                if name.startswith("toolchain-env-"):
                    config = {key: env.get(key, "fixture-" + key) for key in GO_CONFIG_KEYS}
                    if config_fault and name == "toolchain-env-race-after":
                        config["GOROOT"] = "/different/go/root"
                    kwargs["stdout"].write(json.dumps(config).encode())
                elif name.startswith("c-compiler-select-"):
                    kwargs["stdout"].write(str(paths["cc"]).encode() + b"\n")
                elif name == "sdk-select":
                    kwargs["stdout"].write(str(sdk).encode() + b"\n")
                else:
                    kwargs["stdout"].write(b"raw successful fixture output\n")
                if name == "installed-build":
                    installed = out / "work/modcache/github.com/!jagalite/statelessness/go@v0.0.0-n06"
                    shutil.copytree(out / "source/go", installed)
                    (out / "work/campaign-example").write_bytes(b"binary fixture")
                process = Mock(pid=800000 + len(calls), args=argv, returncode=None)
                state = {"reaped": False, "signals": []}
                children[process.pid] = state

                def reap():
                    self.assertFalse(state["reaped"])
                    self.assertEqual(state["signals"], [signal.SIGTERM, signal.SIGKILL])
                    state["reaped"] = True
                    process.returncode = 0
                    return 0

                process.wait.side_effect = reap
                return process

            def observe(_kind, pid, flags):
                if pid == os.getpid():
                    raise ChildProcessError("preflight non-child")
                self.assertTrue(flags & os.WNOWAIT)
                self.assertFalse(children[pid]["reaped"])
                return SimpleNamespace(si_code=os.CLD_EXITED, si_status=0)

            def kill(pid, sig):
                self.assertFalse(children[pid]["reaped"])
                children[pid]["signals"].append(sig)

            def receipt(path, data):
                original_write(path, data)
                last = "c-compiler-select-after" if darwin else "toolchain-env-race-after"
                if fault and path == out / "commands.json" and data[-1]["name"] == last:
                    fault(driver, paths)

            def identity(selected):
                return original_identity(paths["xcrun"] if str(selected) == "/usr/bin/xcrun" else selected)

            with patch.dict(globals(), __file__=str(executing or driver), write_json=receipt,
                            tool_identity=identity), \
                    patch.object(sys, "executable", str(paths["python"])), \
                    patch.object(sys, "argv", [str(driver), "execute", str(out)]), \
                    patch.object(sys, "platform", "darwin" if darwin else "linux"), \
                    patch.dict(os.environ, {"PATH": "/shadowed/path", "GOFLAGS": "hostile", "GOTOOLCHAIN": "auto",
                                           "CC": "hostile-cc --wrapper", "CGO_CFLAGS": "hostile"}), \
                    patch.object(shutil, "which", side_effect=lambda name, **_: str(paths[name])), \
                    patch.object(Provenance, "snapshot", snapshot), \
                    patch.object(subprocess, "Popen", side_effect=spawn), \
                    patch.object(os, "waitid", side_effect=observe), \
                    patch.object(os, "killpg", side_effect=kill), patch.object(time, "sleep"):
                code = execute(out, expected)
            self.assertTrue(all(child["reaped"] for child in children.values()))
            return code, json.loads((out / "result.json").read_bytes()), calls, paths

        def test_actual_absolute_launches_and_race_configuration(self):
            import shlex
            for darwin in (False, True):
                with self.subTest(darwin=darwin):
                    out, expected = self.fixture("launch-" + str(darwin))
                    code, result, calls, paths = self.mock_execution(out, expected, darwin=darwin)
                    self.assertEqual((code, result["status"], result["errors"]), (0, "passed", []))
                    by_name = {name: (argv, env) for name, argv, env in calls}
                    self.assertEqual(by_name["driver-guards"][0], [str(paths["python"]), "-I",
                                     str(out / "source" / DRIVER_MEMBER), "guard-tests", str(out / "work")])
                    for name, argv, env in calls:
                        self.assertTrue(Path(argv[0]).is_absolute())
                        if name not in ("driver-guards", "c-compiler", "sdk-select") and not name.startswith(("installed-", "c-compiler-select-")):
                            self.assertEqual(argv[0], str(paths["go"]))
                        self.assertEqual(env["GOTOOLCHAIN"], "local")
                        self.assertEqual(env["GOFLAGS"], "")
                        self.assertNotIn("CGO_CFLAGS", env)
                    for name in ("installed-build", "installed-modules"):
                        self.assertEqual(by_name[name][0][0], str(paths["go"]))
                    for name in ("race-runtime", "toolchain-env-race-before", "toolchain-env-race-after"):
                        self.assertEqual(shlex.split(by_name[name][1]["CC"]), [str(paths["cc"])])
                        self.assertEqual(by_name[name][1]["CGO_ENABLED"], "1")
                    self.assertEqual(by_name["all-tests-cgo0"][1]["CGO_ENABLED"], "0")
                    if darwin:
                        self.assertEqual(by_name["c-compiler-select-before"][0], [str(paths["xcrun"]), "--find", "clang"])
                        self.assertEqual(by_name["race-runtime"][1]["SDKROOT"], str(paths["cc"].parent / "SDK"))
                    receipts = json.loads((out / "commands.json").read_bytes())
                    self.assertEqual(len(receipts), len(calls))
                    self.assertTrue(all(record["exit_code"] == 0 for record in receipts))
                    self.assertTrue(result["toolchain_config_unchanged"])

        def test_driver_same_bytes_other_path_is_rejected_before_launch(self):
            for hardlink in (False, True):
                out, expected = self.fixture("other-path-" + str(hardlink))
                other = self.root / (out.name + ".py")
                sealed = out / "source" / DRIVER_MEMBER
                if hardlink:
                    os.link(sealed, other)
                else:
                    shutil.copy2(sealed, other)
                code, result, calls, _ = self.mock_execution(out, expected, executing=other)
                self.assertEqual((code, result["status"], calls), (1, "failed", []))
                driver = json.loads((out / "driver-before.json").read_bytes())
                self.assertIn("executing driver canonical path differs from sealed member", driver["errors"])
                self.assertTrue(result["source_unchanged"])

        def test_driver_and_tool_final_drift_cannot_become_passed(self):
            for target in ("driver", "go", "python", "cc"):
                for kind in ("bytes", "mode", "missing", "directory", "symlink", "fifo"):
                    with self.subTest(target=target, kind=kind):
                        out, expected = self.fixture(target + "-" + kind)

                        def mutate(driver, paths):
                            path = driver if target == "driver" else paths[target]
                            if kind == "mode":
                                path.chmod(0o2755)
                            elif kind == "bytes":
                                data = path.read_bytes()
                                path.chmod(0o755)
                                path.write_bytes(bytes([data[0] ^ 1]) + data[1:])
                                path.chmod(0o444 if target == "driver" else 0o755)
                            else:
                                path.unlink()
                                if kind == "directory":
                                    path.mkdir()
                                elif kind == "symlink":
                                    path.symlink_to(paths["xcrun"])
                                elif kind == "fifo":
                                    os.mkfifo(path)

                        code, result, _, _ = self.mock_execution(out, expected, fault=mutate)
                        self.assertEqual((code, result["status"]), (1, "failed"))
                        receipts = json.loads((out / "commands.json").read_bytes())
                        self.assertTrue(all(record["exit_code"] == 0 for record in receipts))
                        self.assertTrue((out / "installed.json").is_file())
                        final = json.loads((out / "toolchain-after.json").read_bytes())
                        if target == "driver":
                            self.assertTrue(final["driver"]["errors"])
                        else:
                            self.assertIn("error", final["tools"][target])
                            self.assertTrue(result["source_unchanged"])
                        self.assertEqual(json.loads((out / "installed-before-run.json").read_bytes()),
                                         json.loads((out / "installed-after.json").read_bytes()))

        def test_config_and_launch_enforcement_fail_durably(self):
            for kind in ("config", "argv", "cc", "cgo", "toolchain"):
                out, expected = self.fixture("configuration-" + kind)
                code, result, calls, _ = self.mock_execution(
                    out, expected, config_fault=kind == "config",
                    launch_fault=None if kind == "config" else kind)
                self.assertEqual((code, result["status"]), (1, "failed"))
                self.assertTrue(result["source_unchanged"])
                if kind != "config":
                    self.assertNotIn("race-runtime", [name for name, *_ in calls])
                    receipt = json.loads((out / "command-race-runtime.json").read_bytes())
                    self.assertTrue(any("invocation/configuration" in error for error in receipt["provenance_before"]["errors"]))
                else:
                    self.assertIn("Go toolchain configuration changed since startup", [error["message"] for error in result["errors"]])

        def test_identity_failures_are_durable_even_after_successful_phases(self):
            for kind in ("missing-pin", "wrong-pin", "source", "frozen", "origin"):
                with self.subTest(kind=kind):
                    out, expected = self.fixture(kind)

                    def mutate(_driver, _paths):
                        target = {"source": out / "source/added", "frozen": out / "frozen.json",
                                  "origin": out / "origin.json"}.get(kind)
                        if target is not None:
                            target.write_bytes(b"drift")

                    pin = None if kind == "missing-pin" else "0" * 64 if kind == "wrong-pin" else expected
                    code, result, calls, _ = self.mock_execution(out, pin, fault=mutate)
                    self.assertEqual((code, result["status"]), (1, "failed"))
                    self.assertEqual(result["expected_frozen_sha256"], pin)
                    self.assertTrue(result["errors"])
                    self.assertTrue((out / "installed-before.json").is_file())
                    self.assertTrue((out / "installed-after.json").is_file())
                    self.assertTrue(json.loads((out / "identity-after.json").read_bytes())["errors"])
                    if kind in ("source", "frozen", "origin"):
                        self.assertIn("installed-ordinary-host", [name for name, *_ in calls])
                        self.assertTrue((out / "installed.json").is_file())
                    else:
                        self.assertEqual(calls, [])

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DriverGuards)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "execute", "guard-tests"))
    parser.add_argument("out", type=Path)
    parser.add_argument("--expect-frozen-sha256")
    args = parser.parse_args()
    output = args.out.resolve()
    if args.mode == "prepare":
        prepare(output)
    elif args.mode == "guard-tests":
        sys.exit(guard_tests(output))
    else:
        sys.exit(execute(output, args.expect_frozen_sha256))

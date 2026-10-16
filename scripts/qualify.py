#!/usr/bin/env python3
"""Source-bound PR/full qualification, with an explicitly incomplete compile-only mode.

No dependencies beyond Python 3.11+. Does not install tools or change Kyoto policy.
Native-port/binding matrices remain separate CI jobs; --portable adds Rust/Python
profile conformance using an already installed Python package and jsonschema.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
import uuid

from qualification_context import git_context

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("src", "crates", "tests", "examples", "benches", "scripts", "docs",
               ".github", "spec", "conformance", "python", "go", "typescript", "swift",
               "bindings", "validation/corpus", "validation/releases", ".cargo")
GENERATED = {"target", "node_modules", "dist", "build", ".build", ".swiftpm", "__pycache__", ".git"}


def source_hashes(root=ROOT):
    paths = {p for p in root.iterdir() if p.is_file() and
             (p.suffix in {".rs", ".toml", ".lock", ".md", ".swift"} or p.name in {"LICENSE", "rust-toolchain", ".gitattributes", ".gitignore"})}
    for directory in SOURCE_DIRS:
        for parent, dirs, files in os.walk(root / directory):
            dirs[:] = [d for d in dirs if d not in GENERATED and not d.endswith(".egg-info")]
            paths.update(Path(parent) / name for name in files
                         if not name.endswith((".pyc", ".tgz")) and name != ".DS_Store")
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths) if p.is_file()}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Stage:
    name: str
    command: list[str]
    kind: str = "runtime"
    dependencies: tuple[str, ...] = ()
    expected_exit: int = 0
    expected_text: str | None = None
    compile_command: list[str] | None = None
    env: dict[str, str] | None = None


def stages(profile, work, portable=False, allow_dirty=False):
    cargo = ["cargo"]
    flags = ["--locked", "--offline"]
    py = sys.executable
    suffix = ".exe" if os.name == "nt" else ""
    target = Path(os.environ.get("CARGO_TARGET_DIR", str(ROOT / "target")))
    if not target.is_absolute():
        target = ROOT / target
    target = target.resolve()
    app = str(target / "debug/examples" / ("application" + suffix))
    controller = str(target / "debug/examples" / ("controller" + suffix))
    result = [Stage("python-tooling", [py, "-m", "unittest", "discover", "-s", "scripts", "-p", "test_*.py"], "tooling")]
    result += [
        Stage("reference-requirements", [py, "scripts/review_requirements.py"], "tooling"),
        Stage("requirements-change-review", [py, "scripts/check_requirement_changes.py", "--output", str(work / "requirements-review.json")], "tooling"),
        Stage("versioned-schemas", [py, "scripts/check_schemas.py"], "tooling"),
        Stage("native-python-candidate", [py, "-m", "unittest", "discover", "-s", "python/tests", "-v"], "tooling", env={"PYTHONPATH": str(ROOT / "python/src")}),
        Stage("conformance-tooling", [py, "-m", "unittest", "discover", "-s", "conformance", "-p", "test_*.py", "-v"], "tooling", env={"PYTHONPATH": str(ROOT / "python/src")}),
    ]
    result.append(Stage("capability-docs", [py, "scripts/check-capabilities.py"], "tooling"))
    for name, args in (
        ("workspace-tests", ["--workspace"]),
        ("debugger-examples", ["-p", "statelessness-debug", "--examples"]),
        ("compiled-out-tests", ["-p", "statelessness-debug", "--features", "compiled-out-diagnostics"]),
        ("starter-tests", ["--example", "application"]),
        ("reference-example-tests", ["--example", "controller"]),
    ):
        command = cargo + ["test", *flags, *args, "-j", "1"]
        result.append(Stage(name, command, compile_command=command + ["--no-run"]))
    result += [
        Stage("format", cargo + ["fmt", "--all", "--check"], "build"),
        Stage("clippy", cargo + ["clippy", *flags, "--workspace", "--all-targets", "--all-features", "-j", "1", "--", "-D", "warnings"], "build"),
        Stage("rustdoc", cargo + ["doc", *flags, "--workspace", "--all-features", "--no-deps", "-j", "1"], "build", env={"RUSTDOCFLAGS": "-D warnings"}),
        Stage("starter-build", cargo + ["build", *flags, "--example", "application", "-j", "1"], "build"),
        Stage("starter-check", [app, "check"], dependencies=("starter-build",), expected_text="Graph exhausted"),
        Stage("starter-find", [app, "find", str(work / "failure")], dependencies=("starter-build",), expected_exit=1),
    ]
    for name in ("original", "minimized"):
        result.append(Stage("starter-replay-" + name, [app, "replay", str(work / "failure" / (name + ".sttrace"))], dependencies=("starter-find",), expected_text="failure reproduced: true"))
    result.append(Stage("starter-reject-changed-model", [app, "replay-fixed", str(work / "failure/minimized.sttrace")], dependencies=("starter-find",), expected_exit=3, expected_text="Diverged"))
    result += [
        Stage("reference-build", cargo + ["build", *flags, "--example", "controller", "-j", "1"], "build"),
        Stage("reference-assurance", [controller, "check", str(work / "reference-assurance.json")], dependencies=("reference-build",)),
        Stage("reference-assurance-schema", [py, "scripts/check_schemas.py", "--report", str(work / "reference-assurance.json"), "--schema", "spec/assurance-report-v1.schema.json"], dependencies=("reference-assurance", "versioned-schemas")),
        Stage("reference-fault-controls", [controller, "mutations", str(work / "reference-mutations")], dependencies=("reference-build",)),
        Stage("reference-fresh-replay", [py, "scripts/check_reference_mutations.py", str(work / "reference-mutations"), "--controller", controller], dependencies=("reference-fault-controls", "reference-requirements")),
        Stage("reference-live-host", [controller, "live", str(work / "reference-live.sttrace")], dependencies=("reference-build",)),
        Stage("reference-live-replay", [controller, "replay", "fixed", str(work / "reference-live.sttrace")], dependencies=("reference-live-host",)),
    ]
    if profile == "full":
        # Existing deeper gates retain their own source-bound report and exact
        # diagnostics. They include all PR specialist gates below and real pipes.
        result.append(Stage("debugger-full", [py, "scripts/check-debugger.py", "--skip-benchmarks", "--evidence", str(work / "debugger/qualification.json")]))
        result += [
            Stage("counter-example", cargo + ["run", *flags, "--example", "counter"]),
            Stage("macro-jobs-build", cargo + ["build", *flags, "-p", "macro-qualification", "--bin", "jobs", "-j", "1"], "build"),
            Stage("macro-jobs-record", [str(target / "debug" / ("jobs" + suffix)), "record", str(work / "jobs.sttrace")], dependencies=("macro-jobs-build",)),
            Stage("macro-jobs-replay", [str(target / "debug" / ("jobs" + suffix)), "replay", str(work / "jobs.sttrace")], dependencies=("macro-jobs-record",)),
        ]
    else:
        dirty = ["--allow-dirty"] if allow_dirty else []
        result += [
            Stage("inspection-fixtures", [py, "scripts/check-inspect-derive.py"]),
            Stage("macro-consumers", [py, "scripts/check-macros.py", *dirty, "--evidence", str(work / "macros.json")]),
            Stage("packaged-consumer", [py, "scripts/check-consumer.py", *dirty, "--target-dir", str(work / "package-target")]),
        ]
        for name, features in (("parity-enabled", []), ("parity-disabled", ["--features", "compiled-out-diagnostics"])):
            result.append(Stage(name, cargo + ["run", *flags, "--quiet", "-p", "statelessness-debug", "--example", "instrumentation_parity", *features]))
        result.append(Stage("instrumentation-parity", [], "compare", ("parity-enabled", "parity-disabled")))
    if portable:
        manifest = "validation/corpus/Cargo.toml"
        result += [
            Stage("portable-python-before", [py, "-I", "scripts/check-python-source.py"], "tooling"),
            Stage("portable-adapter-build", cargo + ["build", "--locked", "--manifest-path", manifest, "--target-dir", str(work / "corpus-target"), "-j", "1"], "build"),
            Stage("portable-schema", [py, "conformance/validate.py"], "tooling"),
            Stage("portable-conformance", [py, "-I", "conformance/run.py", "--runner", json.dumps([py, "-I", "-m", "statelessness.conformance", "--runner"]), "--runner", json.dumps([str(work / "corpus-target/debug" / ("stateless-corpus" + suffix))]), "--report", str(work / "portable.json")], dependencies=("portable-adapter-build", "portable-python-before", "portable-schema")),
            Stage("portable-python-after", [py, "-I", "scripts/check-python-source.py"], "tooling"),
        ]
    return result


def direct(command, log, timeout, env):
    """Redirect output to disk and reap the process tree after a deadline."""
    with log.open("wb") as stream:
        try:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream,
                                       stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
        except OSError as error:
            stream.write(str(error).encode())
            return {"status": "setup_error", "exit_code": None, "error": str(error), "process_started": False}
        try:
            return {"status": "finished", "exit_code": process.wait(timeout=timeout), "process_started": True}
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
            return {"status": "interrupted" if isinstance(error, KeyboardInterrupt) else "timed_out", "exit_code": process.returncode, "process_started": True}


def queued(command, log, timeout, env, kyoto, owner):
    """Submit a visible command; never change queue policy or execute a fallback."""
    prefix = [sys.executable, str(kyoto)]

    def invoke(arguments):
        completed = subprocess.run(prefix + arguments, capture_output=True, text=True, env=env, timeout=30)
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
        return json.loads(completed.stdout)

    job = None
    def cancel():
        if job is None:
            return None
        try:
            invoke(["cancel", job["id"], "--user-id", owner])
            return None
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            return str(error)

    started = time.monotonic()
    try:
        job = invoke(["submit", "--user-id", owner, "--cwd", str(ROOT), "--", *command])
        while job["status"] in {"queued", "running"}:
            if time.monotonic() - started > timeout:
                cancellation_error = cancel()
                return {"status": "timed_out", "exit_code": None, "kyoto_job": job["id"], "error": "Queue/execution deadline reached", "cancellation_requested": cancellation_error is None, "cancellation_error": cancellation_error}
            time.sleep(2)
            job = invoke(["show", job["id"]])
        source_log = Path(job["log"])
        log.write_bytes(source_log.read_bytes() if source_log.exists() else str(job.get("error") or "").encode())
        completed = ((job["status"] == "succeeded" and job["exit_code"] == 0)
                     or (job["status"] == "failed" and job["exit_code"] not in (None, 0)))
        return {"status": "finished" if completed else "setup_error", "exit_code": job["exit_code"], "kyoto_job": job["id"], "kyoto_status": job["status"], "error": job.get("error") if completed else job.get("error") or f"Kyoto did not confirm command completion: {job['status']}"}
    except KeyboardInterrupt:
        cancellation_error = cancel()
        return {"status": "interrupted", "exit_code": None, "kyoto_job": job["id"] if job else None, "cancellation_requested": job is not None and cancellation_error is None, "cancellation_error": cancellation_error}
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        cancellation_error = cancel()
        log.write_text(str(error), encoding="utf-8")
        return {"status": "setup_error", "exit_code": None, "error": str(error), "kyoto_job": job["id"] if job else None, "cancellation_error": cancellation_error}


def run_stage(stage, work, *, compile_only=False, kyoto=None, owner=None, timeout=1800):
    if compile_only and stage.kind in {"runtime", "compare"} and stage.compile_command is None:
        return {"name": stage.name, "status": "not_run", "reason": "compile_only", "command": stage.command}
    command = stage.compile_command if compile_only and stage.compile_command else stage.command
    log = work / "logs" / (stage.name + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    environment = {**os.environ, **(stage.env or {})}
    # --kyoto is deliberately restricted to compile-only mode by the CLI.
    # No opaque script that could run Rust tests enters the queue in this mode.
    result = (queued(command, log, timeout, environment, kyoto, owner)
              if kyoto and stage.kind != "tooling" else direct(command, log, timeout, environment))
    compiled = compile_only and stage.compile_command is not None
    expected = 0 if compiled else stage.expected_exit
    if result["status"] == "finished":
        output = log.read_text(encoding="utf-8", errors="replace")
        passed = result["exit_code"] == expected and (compiled or stage.expected_text is None or stage.expected_text in output)
        result["status"] = "compiled" if passed and compiled else "passed" if passed else "failed"
    return {**result, "name": stage.name, "command": command, "expected_exit": expected,
            "runtime_executed": stage.kind == "runtime" and not compiled and result.get("process_started", False),
            "seconds": round(time.monotonic() - started, 3), "log": str(log.relative_to(work)),
            "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest() if log.exists() else None}


def summarize(results, source_unchanged, complete):
    errors = {"failed", "setup_error", "timed_out", "interrupted", "blocked"}
    selected_passed = bool(results) and source_unchanged and not any(r["status"] in errors for r in results)
    qualified = complete and selected_passed and all(r["status"] == "passed" for r in results)
    return {"status": "passed" if qualified else "incomplete" if selected_passed else "failed",
            "passed": qualified, "selected_checks_passed": selected_passed,
            "qualification_complete": qualified}


def tool_version(command, env=None):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, env=env)
        return {"command": command, "exit_code": result.returncode, "output": (result.stdout + result.stderr).strip()}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"command": command, "exit_code": None, "error": str(error)}


def build_context(launcher=(), environment=None):
    environment = os.environ if environment is None else environment
    names = {"RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTUP_TOOLCHAIN", "RUSTC",
             "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "RUSTDOC", "RUSTDOCFLAGS",
             "CARGO_BUILD_TARGET", "CARGO_BUILD_JOBS", "CARGO_TARGET_DIR", "CARGO_INCREMENTAL"}
    env = {name: value for name, value in sorted(environment.items())
           if name in names or name.startswith("CARGO_PROFILE_")}
    cargo_home = Path(environment.get("CARGO_HOME", Path.home() / ".cargo"))
    configs = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in (cargo_home / "config", cargo_home / "config.toml") if p.is_file()}
    return {"environment": env, "user_cargo_config_sha256": configs,
            "configured_rustc": tool_version([*launcher, environment.get("RUSTC", "rustc"), "-Vv"], env=environment)}


def toolchain_environment(toolchain):
    """Pin nested compiler/doc invocations as well as Cargo's own executable."""
    if not toolchain:
        return {}
    resolved = {}
    for name in ("cargo", "rustc", "rustdoc"):
        result = subprocess.run(["rustup", "which", "--toolchain", toolchain, name],
                                capture_output=True, text=True, timeout=15, check=True)
        path = Path(result.stdout.strip())
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"rustup returned an unavailable {name}: {path}")
        resolved[name] = str(path)
    return {"PATH": str(Path(resolved["cargo"]).parent) + os.pathsep + os.environ.get("PATH", ""),
            "RUSTUP_TOOLCHAIN": toolchain, "RUSTC": resolved["rustc"], "RUSTDOC": resolved["rustdoc"]}


def with_toolchain(stage, toolchain):
    """Use rustup explicitly even when PATH contains standalone Cargo/rustc."""
    if not toolchain:
        return stage
    launcher = ["rustup", "run", toolchain]
    return replace(stage, command=launcher + stage.command if stage.command else [],
                   compile_command=launcher + stage.compile_command if stage.compile_command else None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("pr", "full"), default="pr")
    parser.add_argument("--evidence", type=Path, default=ROOT / "target/qualification/manifest.json")
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument("--toolchain", help="Explicit rustup toolchain for commands and nested specialist scripts")
    parser.add_argument("--kyoto", type=Path, help="Queue compile-only Rust commands; never modifies shared policy")
    parser.add_argument("--portable", action="store_true", help="Add installed Python/Rust conformance; requires jsonschema")
    parser.add_argument("--allow-dirty", action="store_true", help="Permit local package fixtures from modified source")
    parser.add_argument("--requirements-review-receipt", type=Path, help="External reviewer receipt for exact requirement/corpus/phase changes; never generated by this runner")
    parser.add_argument("--timeout", type=float, default=1800, help="Seconds per stage, including queue time")
    parser.add_argument("--list", action="store_true", help="Print selected commands without executing")
    args = parser.parse_args(argv)
    if args.kyoto and not args.compile_only:
        parser.error("--kyoto requires --compile-only; runtime qualification must not bypass shared queue policy through scripts")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    evidence = args.evidence.resolve()
    if evidence.is_relative_to(ROOT) and evidence.relative_to(ROOT).parts[0] != "target":
        parser.error("write evidence under target/ or outside the source tree")
    work = evidence.parent / (evidence.stem + "-artifacts")
    plan = [with_toolchain(stage, args.toolchain) for stage in stages(args.profile, work, args.portable, args.allow_dirty)]
    if args.requirements_review_receipt:
        plan = [replace(stage, command=stage.command + ["--review-receipt", str(args.requirements_review_receipt.resolve())]) if stage.name == "requirements-change-review" else stage for stage in plan]
    if args.list:
        print(json.dumps([dict(name=s.name, kind=s.kind, command=s.compile_command if args.compile_only and s.compile_command else s.command,
                               will_run=not args.compile_only or s.kind not in {"runtime", "compare"} or s.compile_command is not None) for s in plan], indent=2))
        return 0
    try:
        selected_env = toolchain_environment(args.toolchain)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.error(f"cannot select toolchain: {error}")
    plan = [replace(stage, env={**(stage.env or {}), **selected_env}) for stage in plan]
    # Unique output prevents stale artifacts from satisfying a failed fresh run.
    if work.exists() or evidence.exists():
        parser.error(f"artifact directory already exists: {work}; choose a new --evidence path")
    work.mkdir(parents=True)
    initial = source_hashes()
    results = []
    launcher = ["rustup", "run", args.toolchain] if args.toolchain else []
    environment = {**os.environ, **selected_env}
    report = {"schema_version": 1, "profile": args.profile, "compile_only": args.compile_only,
              "toolchain_selector": args.toolchain,
              "artifacts": work.name,
              "started_at": datetime.now(timezone.utc).isoformat(), "git": git_context(ROOT),
              "source_hashes": initial, "source_manifest_sha256": digest(initial),
              "platform": platform.platform(), "machine": platform.machine(),
              "build_configuration": build_context(launcher, environment),
              "toolchains": {"python": sys.version, "rustc": tool_version([*launcher, "rustc", "-Vv"], environment), "cargo": tool_version([*launcher, "cargo", "-V"], environment)},
              "candidate_profiles": {"paired-v1": "experimental; independent review and cross-language runtime qualification required"},
              "portable_profiles": ["core-v1", "bfs-v1", "rng-splitmix64-v1", "trace-json-v1"] if args.portable else [],
              "limitations": ["Only the recorded host/toolchain is covered", "Native language package and ABI binding matrices are separate jobs", "Benchmark measurements and general application adequacy are not assessed; reference assurance has its own finite requirements", "paired-v1 is candidate only; native Rust/Python/TypeScript tests do not promote its profile", "Independent human review of the reference requirements remains pending", "A failed command is not automatically classified as an application property finding", "No automatic retries; flakiness is not inferred"],
              "results": results}

    def save(complete=False):
        current = source_hashes()
        report["source_unchanged"] = current == initial
        report["changed_source_paths"] = sorted(p for p in set(current) | set(initial) if current.get(p) != initial.get(p))
        report.update(summarize(results, current == initial, complete and not args.compile_only))
        temporary = evidence.with_suffix(evidence.suffix + ".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temporary.replace(evidence)

    save()
    owner = str(uuid.uuid4())
    for stage in plan:
        previous = {r["name"]: r for r in results}
        missing = [name for name in stage.dependencies if previous[name]["status"] != "passed"]
        if args.compile_only and stage.kind in {"runtime", "compare"} and not stage.compile_command:
            result = {"name": stage.name, "status": "not_run", "reason": "compile_only", "command": stage.command}
        elif missing:
            result = {"name": stage.name, "status": "blocked", "dependencies": missing, "command": stage.command}
        elif stage.kind == "compare":
            outputs = [(work / previous[name]["log"]).read_bytes() for name in stage.dependencies]
            result = {"name": stage.name, "status": "passed" if outputs[0] and outputs[0] == outputs[1] else "failed"}
        else:
            result = run_stage(stage, work, compile_only=args.compile_only, kyoto=args.kyoto, owner=owner, timeout=args.timeout)
        results.append(result)
        save()
        print(f"{result['status'].upper()} {stage.name}", flush=True)
        if result["status"] == "interrupted":
            break
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    save(complete=len(results) == len(plan))
    print(f"{report['status'].upper()}: {evidence}", flush=True)
    return 0 if report["passed"] or (args.compile_only and report["selected_checks_passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Reproduce source-bound debugger gates without network or external services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import time
from qualification_context import git_context

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--evidence", type=Path, default=ROOT / "validation/debugger/qualification.json")
parser.add_argument("--skip-benchmarks", action="store_true")
args = parser.parse_args()
args.evidence = args.evidence.resolve()
args.evidence.parent.mkdir(parents=True, exist_ok=True)


def source_hashes():
    paths = [ROOT / p for p in ["Cargo.toml", "Cargo.lock", "build.rs", "scripts/check-debugger.py", "scripts/check-inspect-derive.py"]]
    paths.extend((ROOT / "scripts").glob("*.py"))
    paths.extend((ROOT / ".github/workflows").glob("*.yml"))
    for directory in ["src", "crates", "tests", "examples", "benches"]:
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file() and p.suffix in {".rs", ".toml"} and "target" not in p.parts)
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}


results = []
initial_source = source_hashes()
initial_git = git_context(ROOT)
initial_status = initial_git["status"]
with tempfile.TemporaryDirectory(prefix="stateless-debugger-qualification-") as temp:
    temp = Path(temp)
    log_dir = args.evidence.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    def run(name, command, env=None, expect=None, input_text=None):
        started = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, env=env or os.environ.copy(), capture_output=True, text=True, timeout=900, input=input_text)
        output = result.stdout + result.stderr
        (log_dir / f"{name}.log").write_text(output)
        passed = result.returncode == 0 and (expect is None or expect in output)
        results.append({"name": name, "command": command, "returncode": result.returncode, "passed": passed,
                        "elapsed_seconds": round(time.monotonic()-started, 3), "log": f"logs/{name}.log",
                        "rust_tests_passed": sum(int(n) for n in re.findall(r"test result: ok\. (\d+) passed;", output))})
        print(f"{'PASS' if passed else 'FAIL'} {name}", flush=True)
        if not passed:
            print(output[-6000:], flush=True)
        return output

    run("qualification-provenance", ["python3", "scripts/test_qualification_context.py"])
    run("format", ["cargo", "fmt", "--all", "--", "--check"])
    run("workspace", ["cargo", "test", "--locked", "--offline", "--workspace"])
    run("debugger-examples", ["cargo", "test", "--locked", "--offline", "-p", "statelessness-debug", "--examples"])
    run("compiled-out", ["cargo", "test", "--locked", "--offline", "-p", "statelessness-debug", "--features", "compiled-out-diagnostics"])
    run("clippy", ["cargo", "clippy", "--locked", "--offline", "--workspace", "--all-targets", "--all-features", "--", "-D", "warnings"])
    run("rustdoc", ["cargo", "doc", "--locked", "--offline", "--workspace", "--all-features", "--no-deps"], env={**os.environ, "RUSTDOCFLAGS": "-D warnings"})
    parity_enabled = run("parity-enabled", ["cargo", "run", "--locked", "--offline", "-q", "-p", "statelessness-debug", "--example", "instrumentation_parity"])
    parity_disabled = run("parity-compiled-out", ["cargo", "run", "--locked", "--offline", "-q", "-p", "statelessness-debug", "--example", "instrumentation_parity", "--features", "compiled-out-diagnostics"])
    parity_same = bool(parity_enabled) and parity_enabled == parity_disabled
    results.append({"name": "normalized-instrumentation-parity", "passed": parity_same, "description": "State/input/output/disposition/check observations match across both feature builds; each also compares capture-off, capture-on and saturated queues"})
    print(f"{'PASS' if parity_same else 'FAIL'} normalized-instrumentation-parity", flush=True)
    run("inspect-derive", ["python3", "scripts/check-inspect-derive.py"])
    run("existing-macros", ["python3", "scripts/check-macros.py", "--allow-dirty", "--evidence", str(temp / "macro-results.json")])
    run("packaged-core-consumer", ["python3", "scripts/check-consumer.py", "--allow-dirty", "--target-dir", str(temp / "packaged-core")])
    run("default-engine-dependencies", ["cargo", "tree", "--locked", "--offline", "-p", "statelessness"])
    cold_env = {**os.environ, "CARGO_HOME": str(temp / "cold-cargo"), "CARGO_TARGET_DIR": str(temp / "cold-target")}
    run("cold-offline-core", ["cargo", "check", "--locked", "--offline", "-p", "statelessness", "--lib"], env=cold_env)
    run("harness-build", ["cargo", "build", "--locked", "--offline", "-p", "statelessness-debug", "--example", "debug_request", "--example", "debug_stdio", "--example", "debug_counter", "--example", "debug_composition", "--example", "live_host"])
    target = Path(os.environ.get("CARGO_TARGET_DIR", str(ROOT / "target")))
    if not target.is_absolute():
        target = ROOT / target
    executable_suffix = ".exe" if os.name == "nt" else ""
    run("stdio-real-pipes", ["python3", "scripts/check-debugger-stdio.py", str(target / f"debug/examples/debug_stdio{executable_suffix}")])
    executable = target / f"debug/examples/debug_request{executable_suffix}"
    run("interactive-failure", [str(executable), "interactive"], expect="PropertyFailure", input_text="state\ninputs\nstep Start\nstep Cancel\nstep Complete 1\nstate\nquit\n")
    failure = temp / "failure.sttrace"
    run("fresh-record", [str(executable), "record", str(failure)], expect="PropertyFailure")
    run("fresh-verify", [str(executable), "verify", str(failure)], expect="failure_reproduced: true")
    run("fresh-corrected-comparison", [str(executable), "compare", str(failure)], expect='field: "outputs"')
    run("browse-without-model-execution", [str(executable), "view", str(failure), "3"], expect="unverified stored view")
    run("verified-fork", [str(executable), "fork", str(failure), "2", str(temp / "branch.sttrace")], expect="parent_prefix_verified: true")
    for name in ["debug_counter", "debug_composition", "live_host"]:
        run(f"run-{name}", [str(target / f"debug/examples/{name}{executable_suffix}")])
    if not args.skip_benchmarks:
        run("watch-probe-benchmark", ["cargo", "run", "--locked", "--offline", "--release", "-p", "statelessness-debug", "--example", "measure_watches"])
        run("effect-metric-benchmark", ["cargo", "run", "--locked", "--offline", "--release", "-p", "statelessness-debug", "--example", "effect_overhead"])
        run("engine-benchmark", ["cargo", "bench", "--locked", "--offline", "--bench", "engine"])
    final_source = source_hashes()
    report = {
        "design_revision": "f3e00e79471b928d67ddb530c113d6d51af6f277",
        "design_sha256": "4557a92bc9689da26accf22b651cf0ca70f3e508242296c6f99c820a8dad8877",
        "source_commit": initial_git["commit"],
        "git_provenance_available": initial_git["available"],
        "source_dirty_at_start": bool(initial_status.strip()) if initial_status is not None else None,
        "initial_worktree_status": initial_status.splitlines() if initial_status is not None else None,
        "source_hashes": final_source,
        "source_unchanged_during_qualification": initial_source == final_source,
        "rustc": subprocess.check_output(["rustc", "--version", "--verbose"], text=True).strip(),
        "cargo": subprocess.check_output(["cargo", "--version"], text=True).strip(),
        "platform": platform.platform(), "machine": platform.machine(), "logical_cpu_count": os.cpu_count(),
        "results": results, "benchmarks_run": not args.skip_benchmarks,
        "passed": all(r["passed"] for r in results) and initial_source == final_source,
        "limitations": ["Native runtime qualification covers only the recorded host platform and machine", "No native LLDB/GDB, remote transport or external telemetry collector qualification", "Callbacks and synchronous writer I/O are cooperative, not hard-preemptible", "Microbenchmark load is synthetic and does not establish production scheduling or SLA behavior"],
    }
    args.evidence.write_text(json.dumps(report, indent=2)+"\n")
    print(f"Evidence: {args.evidence}", flush=True)
    if not report["passed"]:
        raise SystemExit(1)

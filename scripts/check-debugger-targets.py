#!/usr/bin/env python3
"""Compile-check debugger sources for installed targets; never claim native execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from qualification_context import git_context

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--target", action="append", dest="targets")
parser.add_argument("--evidence", type=Path, default=ROOT / "validation/debugger/target-checks.json")
args = parser.parse_args()
targets = args.targets or ["x86_64-pc-windows-gnu", "x86_64-apple-darwin", "wasm32-unknown-unknown"]
known = set(subprocess.check_output(["rustc", "--print", "target-list"], text=True).splitlines())
if len(targets) != len(set(targets)) or any(target not in known for target in targets):
    parser.error("targets must be distinct compiler-declared target triples")
args.evidence = args.evidence.resolve()
args.evidence.parent.mkdir(parents=True, exist_ok=True)
logs = args.evidence.parent / "target-logs"
logs.mkdir(exist_ok=True)


def source_hashes():
    paths = [ROOT / name for name in ["Cargo.toml", "Cargo.lock", "build.rs"]]
    paths.extend((ROOT / "scripts").glob("*.py"))
    paths.extend((ROOT / ".github/workflows").glob("*.yml"))
    for directory in ["src", "crates", "tests", "examples", "benches"]:
        paths.extend(path for path in (ROOT / directory).rglob("*")
                     if path.is_file() and path.suffix in {".rs", ".toml"} and "target" not in path.parts)
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(set(paths))}


initial = source_hashes()
context = git_context(ROOT)
results = []
for target in targets:
    command = ["cargo", "check", "--locked", "--offline", "--workspace", "--all-targets",
               "--all-features", "--target", target]
    started = time.monotonic()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=900)
    log = logs / f"{target}.log"
    log.write_text(result.stdout + result.stderr)
    results.append({"target": target, "command": command, "passed": result.returncode == 0,
                    "returncode": result.returncode, "elapsed_seconds": round(time.monotonic() - started, 3),
                    "compile_only": True, "linked": False, "native_tests_run": False,
                    "log": str(log.relative_to(args.evidence.parent))})
    print(f"{'PASS' if result.returncode == 0 else 'FAIL'} compile-only {target}", flush=True)
final = source_hashes()
report = {"source_commit": context["commit"], "git_provenance_available": context["available"],
          "source_hashes": final, "source_unchanged_during_qualification": initial == final,
          "rustc": subprocess.check_output(["rustc", "--version", "--verbose"], text=True).strip(),
          "cargo": subprocess.check_output(["cargo", "--version"], text=True).strip(),
          "results": results, "passed": initial == final and all(result["passed"] for result in results),
          "limitations": ["Requires preinstalled target standard libraries; no downloads attempted",
                          "Compilation only: no cross-linker, native OS execution or runtime qualification",
                          "Successful wasm compilation does not qualify host clocks, threads or stdio on wasm"]}
args.evidence.write_text(json.dumps(report, indent=2) + "\n")
if not report["passed"]:
    raise SystemExit(1)

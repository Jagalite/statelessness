#!/usr/bin/env python3
"""Run through Kyoto, after swift test has built the supplied frozen package."""
import argparse
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--package", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
modules = sorted(args.package.glob(".build/*/debug/Modules/Statelessness.swiftmodule"))
if len(modules) != 1:
    raise SystemExit(f"expected one built native Swift module, found {modules}")
contracts = Path(__file__).resolve().parent / "compile-contracts"
results = []
for name, needle in [("AsyncFeedback.swift", "cannot pass function of type"), ("SendableSink.swift", "does not conform to the 'Sendable' protocol")]:
    command = ["swiftc", "-typecheck", "-parse-as-library", "-swift-version", "6", "-I", str(modules[0].parent), str(contracts / name)]
    process = subprocess.run(command, capture_output=True, text=True)
    (args.output / (name + ".stdout.log")).write_text(process.stdout)
    (args.output / (name + ".stderr.log")).write_text(process.stderr)
    # Reject only the intended compile-time contract, not a missing module/import.
    passed = process.returncode != 0 and needle in process.stderr and "no such module" not in process.stderr
    results.append({"file": name, "command": command, "exit_code": process.returncode, "expected_rejection": passed})
    print(json.dumps(results[-1]), flush=True)
(args.output / "result.json").write_text(json.dumps(results, indent=2) + "\n")
if not all(result["expected_rejection"] for result in results):
    raise SystemExit(1)

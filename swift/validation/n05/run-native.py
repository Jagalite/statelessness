#!/usr/bin/env python3
"""Kyoto-only native suite, compile contracts and public example qualification."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--snapshot", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()
snapshot = args.snapshot.resolve()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
package = snapshot / "package"
commands = [
    ("toolchain", ["swift", "--version"]),
    ("tests", ["swift", "test", "--package-path", str(package), "-j", "2"]),
    ("compile-contracts", [sys.executable, str(snapshot / "check-compile-contracts.py"), "--package", str(package), "--output", str(output / "compile-contracts")]),
    ("example", ["swift", "run", "--package-path", str(package / "swift/Examples/Guided"), "-j", "2", "GuidedExample"]),
]
results = []
for name, command in commands:
    started = time.monotonic()
    print(json.dumps({"phase": name, "command": command}), flush=True)
    with (output / (name + ".log")).open("wb") as log:
        process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    result = {"phase": name, "command": command, "exit_code": process.returncode, "elapsed_seconds": time.monotonic() - started}
    results.append(result)
    (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    if process.returncode:
        print((output / (name + ".log")).read_text(errors="replace"), flush=True)
        raise SystemExit(process.returncode)
print("native Swift N05 suite, compile contracts and example completed", flush=True)

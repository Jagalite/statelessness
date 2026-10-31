#!/usr/bin/env python3
"""Kyoto-only installed SwiftPM consumer comparison against independently frozen inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument("--snapshot", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--authority-freeze", type=Path, required=True)
parser.add_argument("--manifest", type=Path, required=True)
args = parser.parse_args()
snapshot, output = args.snapshot.resolve(), args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
package, consumer = snapshot / "package", snapshot / "consumer"
corpus_path = package / "conformance/guided-v2/corpus.json"
freeze_path = corpus_path.with_name("FREEZE.json")
freeze = json.loads(freeze_path.read_bytes())
manifest_bytes = args.manifest.read_bytes()
source_manifest = json.loads(manifest_bytes)
if source_manifest["schema"] != "stateless-swift-source-snapshot/2":
    raise SystemExit("installed comparison requires a membership/type/hash manifest")
if Path(source_manifest["snapshot_path"]).resolve() != snapshot:
    raise SystemExit("source manifest names a different snapshot")


def source_inventory():
    entries = {}
    excluded = {"package/.build", "package/.swiftpm", "consumer/.build", "consumer/.swiftpm"}
    def visit(directory):
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(snapshot).as_posix()
            if relative in excluded:
                continue
            if path.is_symlink():
                raise SystemExit(f"source snapshot contains a symlink: {relative}")
            if path.is_dir():
                entries[relative] = {"type": "directory"}
                visit(path)
            elif path.is_file():
                entries[relative] = {"type": "file", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            else:
                raise SystemExit(f"source snapshot contains an unsupported file type: {relative}")
    visit(snapshot)
    return entries


def verify_source_and_current_freeze():
    if not args.authority_freeze.is_file() or args.authority_freeze.read_bytes() != freeze_path.read_bytes():
        raise SystemExit("independent freeze withdrawn or changed; comparison forbidden")
    if source_inventory() != source_manifest["entries"]:
        raise SystemExit("frozen Swift source membership, type, or content changed")


verify_source_and_current_freeze()
for relative, expected_hash in freeze["files"].items():
    path = package / relative
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise SystemExit(f"independent freeze mismatch: {relative}")
corpus = json.loads(corpus_path.read_bytes())
if freeze["candidate_output_used"] or len(corpus["cases"]) != freeze["cases"]:
    raise SystemExit("invalid independent corpus freeze")
inputs = {key: value for key, value in corpus.items() if key != "cases"}
inputs["cases"] = [{key: value for key, value in row.items() if key not in {"expected", "rng_audit"}}
                   for row in corpus["cases"]]
input_path = output / "input-only-corpus.json"
input_path.write_text(json.dumps(inputs, sort_keys=True) + "\n")
environment = os.environ.copy()
environment["STATELESS_SWIFT_PACKAGE"] = str(package)
commands = []


def run(label, command):
    started = time.monotonic()
    result = subprocess.run(command, env=environment, capture_output=True)
    (output / (label + ".stdout.log")).write_bytes(result.stdout)
    (output / (label + ".stderr.log")).write_bytes(result.stderr)
    receipt = {"label": label, "command": command, "cwd": str(Path.cwd()),
               "STATELESS_SWIFT_PACKAGE": str(package), "exit_code": result.returncode,
               "elapsed_seconds": time.monotonic() - started}
    commands.append(receipt)
    (output / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")
    print(json.dumps(receipt), flush=True)
    if result.returncode:
        print(result.stderr.decode(errors="replace"), flush=True)
        raise SystemExit(result.returncode)
    return result.stdout


dependencies = json.loads(run("dependencies", ["swift", "package", "--package-path", str(consumer),
                                             "show-dependencies", "--format", "json"]))
resolved = dependencies.get("dependencies", [])
if len(resolved) != 1 or Path(resolved[0]["path"]).resolve() != package:
    raise SystemExit("SwiftPM resolved an unexpected package")
run("build", ["swift", "build", "--package-path", str(consumer), "--product", "GuidedConsumer", "-j", "2"])
binary_path = Path(run("binary-path", ["swift", "build", "--package-path", str(consumer), "--show-bin-path"]).decode().strip()) / "GuidedConsumer"
binary_hash = hashlib.sha256(binary_path.read_bytes()).hexdigest()
failures, results = [], []
fields = {"report", "error", "trace", "generator_rng", "witness_replays", "shrink"}


def first_difference(actual, expected, path="$root"):
    if type(actual) is not type(expected):
        return {"path": path, "actual": actual, "expected": expected}
    if isinstance(actual, dict):
        if actual.keys() != expected.keys():
            return {"path": path, "actual_keys": sorted(actual), "expected_keys": sorted(expected)}
        for key in expected:
            result = first_difference(actual[key], expected[key], path + "." + key)
            if result:
                return result
    elif isinstance(actual, list):
        if len(actual) != len(expected):
            return {"path": path, "actual_length": len(actual), "expected_length": len(expected)}
        for index, (a, e) in enumerate(zip(actual, expected)):
            result = first_difference(a, e, path + f"[{index}]")
            if result:
                return result
    elif actual != expected:
        return {"path": path, "actual": actual, "expected": expected}
    return None


for index, case in enumerate(corpus["cases"]):
    if not args.authority_freeze.is_file() or args.authority_freeze.read_bytes() != freeze_path.read_bytes():
        raise SystemExit("independent freeze withdrawn or changed; comparison forbidden")
    raw = run(f"case-{index:03d}-{case['id']}", [str(binary_path), "--corpus", str(input_path), "--case", case["id"]])
    response = json.loads(raw)
    if set(response) != {"schema", "engine", "cases"} or response["schema"] != "stateless-guided-v2-results/1" or response["engine"] != "swift":
        raise SystemExit("invalid consumer response")
    rows = response["cases"]
    if len(rows) != 1 or set(rows[0]) != {"id", "actual"} or rows[0]["id"] != case["id"] or set(rows[0]["actual"]) != fields:
        raise SystemExit("invalid selected case or result fields")
    expected = {key: value for key, value in case["expected"].items() if key != "rng_audit"}
    difference = first_difference(rows[0]["actual"], expected)
    result = {"id": case["id"], "status": "failed" if difference else "passed"}
    results.append(result)
    if difference:
        failures.append(dict(id=case["id"], **difference))
    (output / "comparison.json").write_text(json.dumps({"engine": "swift", "cases": results, "failures": failures}, indent=2) + "\n")

if hashlib.sha256(binary_path.read_bytes()).hexdigest() != binary_hash:
    raise SystemExit("consumer binary changed during comparison")
verify_source_and_current_freeze()
summary = {"engine": "swift", "passed": len(results) - len(failures), "failed": len(failures),
           "fresh_processes": len(results), "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
           "freeze_sha256": hashlib.sha256(freeze_path.read_bytes()).hexdigest(),
           "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
           "external_snapshot_path": str(snapshot),
           "binary": str(binary_path), "binary_sha256": binary_hash}
(output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary), flush=True)
if failures:
    print(json.dumps(failures, indent=2), flush=True)
    raise SystemExit(1)

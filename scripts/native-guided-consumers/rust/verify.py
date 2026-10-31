#!/usr/bin/env python3
"""Run only this Rust reference against the independently sealed corpus.

Submit this whole command through default shared Kyoto. It never builds, authors
expectations, invokes the oracle, or compares any other language engine.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

FIELDS = ("report", "error", "trace", "generator_rng", "witness_replays", "shrink")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def first_difference(expected, actual, path="$actual"):
    if type(expected) is not type(actual):
        return {"path": path, "expected": expected, "actual": actual}
    if isinstance(expected, dict):
        if expected.keys() != actual.keys():
            return {"path": path, "expected_keys": sorted(expected), "actual_keys": sorted(actual)}
        for key in expected:
            if result := first_difference(expected[key], actual[key], f"{path}.{key}"):
                return result
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            return {"path": path, "expected_length": len(expected), "actual_length": len(actual)}
        for i, (left, right) in enumerate(zip(expected, actual)):
            if result := first_difference(left, right, f"{path}[{i}]"):
                return result
    elif expected != actual:
        return {"path": path, "expected": expected, "actual": actual}
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--host-checks", action="store_true")
    args = parser.parse_args()
    binary, corpus, output = args.binary.resolve(), args.corpus.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)  # Never overwrite an earlier attempt.
    summary = {"status": "failed", "commands": [], "failures": []}
    try:
        freeze_path = corpus.with_name("FREEZE.json")
        freeze = json.loads(freeze_path.read_text())
        assert freeze["schema"] == "stateless-guided-v2-freeze/1", "unexpected freeze schema"
        assert freeze["candidate_output_used"] is False, "candidate-derived expectations forbidden"
        root = corpus.parents[2]
        pinned = {}
        for name, expected_sha in freeze["files"].items():
            path = (root / name).resolve()
            assert path.is_relative_to(root), "freeze path escapes repository"
            assert sha(path) == expected_sha, f"freeze mismatch: {name}"
            pinned[name] = expected_sha
        assert str(corpus.relative_to(root)) in pinned, "corpus not pinned by freeze"
        data = json.loads(corpus.read_text())
        assert data["source_commit"] == freeze["source_commit"], "source identity mismatch"
        assert len(data["cases"]) == freeze["cases"], "freeze case count mismatch"
        summary.update({"freeze_sha256": sha(freeze_path), "binary_sha256": sha(binary),
                        "source_commit": freeze["source_commit"], "pinned_inputs": pinned})
        write(output / "freeze.json", freeze)

        def execute(label, command):
            summary["commands"].append(command)
            write(output / f"{label}-command.json", command)
            with (output / f"{label}-stdout.log").open("wb") as stdout, (output / f"{label}-stderr.log").open("wb") as stderr:
                result = subprocess.run(command, stdout=stdout, stderr=stderr, check=False)
            assert result.returncode == 0, f"{label} exited {result.returncode}; see retained raw logs"

        if args.host_checks:
            execute("host", [str(binary), "--host-checks", "--output", str(output / "host-actual.json")])
            host = json.loads((output / "host-actual.json").read_text())
            assert host["schema"] == "stateless-guided-rust-host/1"
            assert host["count"] == len(host["passed"]) == 9, "incomplete host-check execution"
            summary["host_checks"] = host

        execute("corpus", [str(binary), "--corpus", str(corpus), "--output", str(output / "corpus-actual.json")])
        result = json.loads((output / "corpus-actual.json").read_text())
        assert result["schema"] == "stateless-guided-v2-results/1" and result["engine"] == "rust"
        assert [row["id"] for row in result["cases"]] == [row["id"] for row in data["cases"]], "case identity/order mismatch"
        for expected, actual in zip(data["cases"], result["cases"]):
            selected = {key: expected["expected"][key] for key in FIELDS}
            difference = first_difference(selected, actual["actual"])
            if difference:
                summary["failures"].append({"id": expected["id"], **difference})
        assert sha(freeze_path) == summary["freeze_sha256"], "freeze changed during execution"
        assert all(sha(root / name) == value for name, value in pinned.items()), "frozen input changed during execution"
        assert sha(binary) == summary["binary_sha256"], "binary changed during execution"
        summary["case_count"] = len(data["cases"])
        summary["passing_witnesses"] = sum(len(row["actual"]["report"]["guidance"]["corpus"])
            for row in result["cases"] if row["actual"]["report"] and row["actual"]["report"]["guidance"])
        summary["status"] = "failed" if summary["failures"] else "passed"
    except Exception as error:
        summary["error"] = str(error)
    write(output / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())

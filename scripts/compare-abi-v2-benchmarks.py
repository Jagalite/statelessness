#!/usr/bin/env python3
"""Compare already recorded ABI benchmark measurements; never execute a workload.

Both input files use schema stateless.binding-measurements.v1 and contain the
same benchmark cells. Candidate package/source identity is copied to the gate
receipt. Budgets are explicit inputs, not guessed or silently relaxed here.
"""
import argparse
import json
import math
from pathlib import Path

from binding_evidence import sha256, write_json

REQUIRED_METRICS = {"median_ns", "p95_ns", "peak_rss_bytes", "retained_bytes", "callbacks", "allocations"}
WORKLOADS = {"observation", "bfs", "record", "replay", "fuzz", "shrink", "ring"}
MODELS = {"cheap", "large", "expensive"}
PORTABLE_OPERATIONS = {"observation", "bfs", "record", "replay"}


def cells(document):
    if document.get("schema") != "stateless.binding-measurements.v1":
        raise ValueError("unsupported benchmark measurement schema")
    if document.get("execution") != "completed" or not document.get("command") or not document.get("environment"):
        raise ValueError("measurements require a completed execution, command and environment identity")
    if not document.get("package_files") or not document.get("source_sha256"):
        raise ValueError("measurements require exact package and source identity")
    result = {}
    for cell in document.get("cells", []):
        key = (cell.get("workload"), cell.get("model"), cell.get("case"))
        if any(not isinstance(part, str) or not part for part in key) or key in result:
            raise ValueError("benchmark cells require unique workload/model/case identities")
        metrics = cell.get("metrics", {})
        if not REQUIRED_METRICS.issubset(metrics):
            raise ValueError(f"benchmark cell lacks measurements: {key}")
        for name, value in metrics.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid benchmark metric: {key}/{name}")
        if not cell.get("parameters") or not cell.get("corpus_sha256") or cell.get("samples", 0) < 2 or not cell.get("implementation") or not cell.get("runtime"):
            raise ValueError(f"benchmark cell lacks inputs/implementation/runtime/repeated samples: {key}")
        result[key] = cell
    if not {(workload, model) for workload in WORKLOADS for model in MODELS}.issubset({key[:2] for key in result}):
        raise ValueError("benchmark matrix needs every operation on cheap, large and expensive models")
    return result


def compare(baseline, candidate, budgets):
    before, after = cells(baseline), cells(candidate)
    if before.keys() != after.keys():
        raise ValueError("baseline and candidate benchmark cells differ")
    for field in ("language", "target", "environment"):
        if baseline.get(field) != candidate.get(field):
            raise ValueError(f"benchmark {field} identity differs")
    if budgets.get("schema") != "stateless.binding-budgets.v1" or not budgets.get("approved_by"):
        raise ValueError("budgets require explicit schema and approval attribution")
    limits = budgets.get("metrics", {})
    if not REQUIRED_METRICS.issubset(limits):
        raise ValueError("budgets must cover every required metric")
    comparisons = []
    for key, cell in after.items():
        reference = before[key]
        reference_kind = reference.get("baseline_kind")
        if reference_kind not in {"independent", "rust-native", "prior-v2", "abi1"}:
            raise ValueError(f"baseline cell needs an explicit comparison implementation kind: {key}")
        if reference_kind == "independent" and baseline["language"] in {"python", "typescript", "go", "swift"} and key[0] not in PORTABLE_OPERATIONS:
            raise ValueError(f"independent language baseline does not expose this operation; use measured Rust or prior-v2 reference: {key}")
        if reference_kind == "abi1" and key[0] not in {"bfs", "record", "replay"}:
            raise ValueError(f"ABI 1 baseline does not expose this operation: {key}")
        if any(cell[field] != reference[field] for field in ("parameters", "corpus_sha256")):
            raise ValueError(f"workload inputs differ for {key}")
        for metric in sorted(REQUIRED_METRICS):
            limit = limits[metric]
            ratio, allowance = limit.get("max_ratio"), limit.get("absolute_allowance", 0)
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in (ratio, allowance)):
                raise ValueError(f"invalid budget for {metric}")
            observed, original = cell["metrics"][metric], reference["metrics"][metric]
            maximum = original * ratio + allowance
            comparisons.append({"cell": list(key), "reference_kind": reference_kind,
                                "reference_implementation": reference["implementation"], "candidate_implementation": cell["implementation"],
                                "metric": metric, "baseline": original,
                                "candidate": observed, "maximum": maximum, "passed": observed <= maximum})
    return comparisons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--budgets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text())
    candidate = json.loads(args.candidate.read_text())
    try:
        comparisons = compare(baseline, candidate, json.loads(args.budgets.read_text()))
    except (ValueError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from error
    passed = all(item["passed"] for item in comparisons)
    report = {"schema": "stateless.binding-qualification.v1", "mode": "benchmark",
              "language": candidate["language"], "target": candidate["target"],
              "source_sha256": candidate["source_sha256"], "package_files": candidate["package_files"],
              "gates": {"performance": {"status": "passed" if passed else "failed", "budget_comparison": comparisons}},
              "evidence": {"baseline_sha256": sha256(args.baseline), "candidate_sha256": sha256(args.candidate), "budgets_sha256": sha256(args.budgets)},
              "default_switch_ready": False}
    write_json(args.output, report)
    if not passed:
        raise SystemExit("one or more measured performance budgets failed")


if __name__ == "__main__":
    main()

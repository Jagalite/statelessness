#!/usr/bin/env python3
"""Aggregate source-bound ABI v2 evidence; never runs or switches an engine."""
import argparse
import json
from pathlib import Path

from binding_evidence import content_identity, sha256, source_snapshot, write_json

COMMON = (
    "package_integrity", "runtime_bootstrap", "sdk_negative_loading",
    "installed_model_consumer", "semantic_regression", "fresh_process_replay",
    "lifetime", "abi1_compatibility", "deployment_contract", "performance", "migration_window",
)
RUNTIME = set(COMMON) - {"package_integrity", "performance", "migration_window"}


def aggregate(source, language, target, receipts):
    required = list(COMMON)
    if language != "c":
        required.append("portable_profiles")
    if language == "typescript" and target.endswith("/browser"):
        required.append("actual_browser")
    gates = {name: {"status": "pending", "reason": "no completed matching evidence"} for name in required}
    evidence = []
    package_identity = None
    for path in receipts:
        receipt = json.loads(path.read_text())
        if receipt.get("schema") != "stateless.binding-qualification.v1":
            raise ValueError(f"unsupported qualification receipt: {path}")
        if (receipt.get("source_sha256"), receipt.get("language"), receipt.get("target")) != (source["sha256"], language, target):
            raise ValueError(f"qualification receipt does not match source/language/target: {path}")
        files = receipt.get("package_files")
        if not isinstance(files, dict) or not files:
            raise ValueError(f"qualification receipt lacks package content identity: {path}")
        identity = content_identity(files)
        if package_identity is not None and package_identity != identity:
            raise ValueError(f"qualification receipts describe different package bytes: {path}")
        package_identity = identity
        evidence.append({"path": str(path), "sha256": sha256(path)})
        for name, gate in receipt.get("gates", {}).items():
            if name not in gates:
                continue
            if gate.get("status") == "failed":
                gates[name] = {"status": "failed", "receipt": str(path)}
                continue
            if gate.get("status") != "passed":
                continue
            mode = receipt.get("mode")
            if name in RUNTIME | {"portable_profiles", "actual_browser"} and mode != "runtime":
                raise ValueError(f"{path}: {name} requires executed runtime evidence")
            if name == "performance":
                comparisons = gate.get("budget_comparison")
                if mode != "benchmark" or not isinstance(comparisons, list) or not comparisons or any(item.get("passed") is not True for item in comparisons):
                    raise ValueError(f"{path}: performance requires successful measured budget comparisons")
            if name == "migration_window":
                migration, cutover = gate.get("migration_release"), gate.get("cutover_release")
                if mode != "migration" or not migration or not cutover or migration == cutover or not gate.get("rollback_document"):
                    raise ValueError(f"{path}: migration window requires distinct releases and rollback documentation")
            if gates[name]["status"] != "failed":
                gates[name] = {"status": "passed", "receipt": str(path)}
    return {
        "schema": "stateless.binding-readiness.v1", "source_commit": source["commit"],
        "source_sha256": source["sha256"], "language": language, "target": target,
        "package_sha256": package_identity,
        "gates": gates, "receipts": evidence,
        "default_switch_ready": all(gate["status"] == "passed" for gate in gates.values()),
        "action": "report_only",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--language", choices=("c", "python", "go", "swift", "typescript"), required=True)
    parser.add_argument("--target", required=True, help="native triple or wasm32-unknown-unknown/{node,browser}")
    parser.add_argument("--receipt", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-ready", action="store_true", help="fail if any migration gate remains pending")
    args = parser.parse_args()
    try:
        report = aggregate(source_snapshot(args.root), args.language, args.target, args.receipt)
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from error
    write_json(args.output, report)
    pending = [name for name, gate in report["gates"].items() if gate["status"] != "passed"]
    print("Pending: " + ", ".join(pending) if pending else "All supplied migration gates passed; no default was changed")
    if args.require_ready and pending:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

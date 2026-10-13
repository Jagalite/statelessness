#!/usr/bin/env python3
"""Expose specification changes separately from implementation changes.

This tool validates claimed external review evidence; it cannot authenticate a
reviewer. CI must protect the receipt/reviewer approval with repository rules.
It never issues its own independent-review approval.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

REQUIRED_KEYS = {"schema_version", "model", "codec_version", "properties_version", "review", "properties", "input_domains", "bounds", "coverage_targets", "assumptions", "mutations", "required_search_outcome", "regression_corpus"}
REVIEW_FIELDS = ("properties", "input_domains", "bounds", "coverage_targets", "assumptions", "mutations", "codec_version", "properties_version", "schema_version", "required_search_outcome", "regression_corpus", "model")


def load(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=pairs)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate(catalog):
    if set(catalog) != REQUIRED_KEYS:
        raise ValueError("missing or unknown requirement fields")
    if type(catalog["schema_version"]) is not int or catalog["schema_version"] != 1:
        raise ValueError("unsupported requirement schema version")
    for field in ("codec_version", "properties_version"):
        if type(catalog[field]) is not int or catalog[field] < 1:
            raise ValueError(f"invalid {field}")
    for field in ("model", "regression_corpus"):
        if not isinstance(catalog[field], str) or not catalog[field].strip():
            raise ValueError(f"empty {field}")
    for field in ("properties", "input_domains", "bounds", "mutations"):
        if not isinstance(catalog[field], dict) or not catalog[field]:
            raise ValueError(f"empty {field}")
    for key, description in catalog["properties"].items():
        if not key or not isinstance(description, str) or not description.strip():
            raise ValueError("invalid property requirement")
    for field in ("coverage_targets", "assumptions"):
        values = catalog[field]
        if not isinstance(values, list) or not values or any(not isinstance(x, str) or not x for x in values) or len(set(values)) != len(values):
            raise ValueError(f"empty/duplicate {field}")
    for values in catalog["input_domains"].values():
        if not isinstance(values, list) or not values or len({json.dumps(x, sort_keys=True) for x in values}) != len(values):
            raise ValueError("invalid or duplicate input domain")
    if any(type(v) is not int or v < 1 for v in catalog["bounds"].values()):
        raise ValueError("bounds must be positive integer declarations")
    if any(target not in catalog["properties"] for target in catalog["mutations"].values()):
        raise ValueError("mutation targets missing required property")
    if catalog["required_search_outcome"] not in ("graph_exhausted", "cases_completed"):
        raise ValueError("invalid required search outcome")
    review = catalog["review"]
    if not isinstance(review, dict) or set(review) != {"status", "reviewer"} or review["status"] not in ("awaiting_independent_review", "independently_reviewed"):
        raise ValueError("invalid review declaration")
    if review["status"] == "independently_reviewed" and not review["reviewer"]:
        raise ValueError("reviewer required for review claim")
    return catalog


def compare(base, current, base_corpus=None, current_corpus=None):
    validate(base)
    validate(current)
    changes = []
    for field in REVIEW_FIELDS:
        if base[field] != current[field]:
            detail = {"category": field, "before": base[field], "after": current[field]}
            if field in ("properties", "mutations", "input_domains"):
                detail["removed"] = sorted(set(base[field]) - set(current[field]))
            if field == "bounds":
                detail["raised"] = sorted(k for k, v in current[field].items() if k in base[field] and v > base[field][k])
            changes.append(detail)
    if base_corpus != current_corpus:
        changes.append({"category": "regression_corpus_content", "before": base_corpus, "after": current_corpus})
    return {"schema_version": 1, "base_sha256": digest(base), "current_sha256": digest(current),
            "base_corpus_sha256": base_corpus, "current_corpus_sha256": current_corpus,
            "requires_independent_review": bool(changes), "changes": changes,
            "review_status": "required" if changes else "no_requirements_change"}


def review_matches(report, receipt):
    if not isinstance(receipt, dict) or receipt.get("decision") != "approve_requirements_change":
        return False
    for field in ("base_sha256", "current_sha256", "base_corpus_sha256", "current_corpus_sha256"):
        if field not in receipt or receipt[field] != report[field]:
            return False
    if not isinstance(receipt.get("reviewer"), str) or not receipt["reviewer"].strip():
        return False
    if not isinstance(receipt.get("rationale"), str) or not receipt["rationale"].strip():
        return False
    return isinstance(receipt.get("approved_categories"), list) and set(receipt["approved_categories"]) == {c["category"] for c in report["changes"]}


def check_catalog_files(root, catalog):
    lines = (root / "docs/requirements/controller-properties.txt").read_text().splitlines()
    declared = []
    for line in lines:
        phase, identifier = line.split()
        if phase not in ("state", "transition"):
            raise ValueError("unknown property phase")
        declared.append(identifier)
    if len(declared) != len(set(declared)) or set(declared) != set(catalog["properties"]):
        raise ValueError("independent compiled property catalog differs from requirements")
    corpus_path = root / catalog["regression_corpus"]
    if not corpus_path.resolve().is_relative_to(root.resolve()):
        raise ValueError("regression corpus outside repository")
    sections = [line.strip()[1:-1] for line in corpus_path.read_text().splitlines() if line.strip().startswith("[")]
    if len(sections) != len(set(sections)) or set(sections) != set(catalog["mutations"]):
        raise ValueError("regression mutation removed/duplicated/renamed")


def source_changes(root, base_ref):
    # Report these independently so a reducer-only PR is recognizable. No shell
    # interpretation of a ref or file name; paths are repository-relative.
    result = subprocess.run(["git", "diff", "--name-only", f"{base_ref}...HEAD", "--"], cwd=root, capture_output=True, text=True, check=True)
    categories = {"reducers": [], "properties_and_domains": [], "codec_or_trace": [], "regressions": [], "other": []}
    for path in result.stdout.splitlines():
        if "witness" in path or path.startswith("tests/"):
            category = "regressions"
        elif path.startswith("docs/requirements/") or "assurance" in path:
            category = "properties_and_domains"
        elif "codec" in path or "trace" in path:
            category = "codec_or_trace"
        elif path.startswith("src/") or path.startswith("examples/"):
            category = "reducers"
        else:
            category = "other"
        categories[category].append(path)
    return categories


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, default=Path("docs/requirements/controller.json"))
    parser.add_argument("--base", type=Path)
    parser.add_argument("--base-corpus", type=Path)
    parser.add_argument("--current-corpus", type=Path)
    parser.add_argument("--review-receipt", type=Path)
    parser.add_argument("--base-ref")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    current = validate(load(args.current))
    check_catalog_files(args.root, current)
    if args.base:
        corpus_hash = lambda path: hashlib.sha256(path.read_bytes()).hexdigest() if path else None
        if args.base_corpus is None or args.current_corpus is None:
            raise ValueError("base comparisons require both corpus paths; regression removals must be reviewed")
        report = compare(load(args.base), current, corpus_hash(args.base_corpus), corpus_hash(args.current_corpus))
    else:
        report = {"schema_version": 1, "current_sha256": digest(current), "review_status": current["review"]["status"], "requires_independent_review": False, "validation_only": True}
    if args.base_ref:
        report["source_changes"] = source_changes(args.root, args.base_ref)
    if report["requires_independent_review"] and args.review_receipt:
        if review_matches(report, load(args.review_receipt)):
            report["review_status"] = "external_receipt_matches_changes_identity_not_authenticated"
        else:
            report["review_status"] = "invalid_or_stale_review_receipt"
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["requires_independent_review"] and report["review_status"] != "external_receipt_matches_changes_identity_not_authenticated" else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"requirement review gate: {error}", file=sys.stderr)
        sys.exit(2)

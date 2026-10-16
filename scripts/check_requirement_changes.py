#!/usr/bin/env python3
"""CI requirement-diff entry point. No network, no automatically issued approvals."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from review_requirements import check_catalog_files, compare, load, review_matches, validate

ROOT = Path(__file__).resolve().parents[1]
CATALOG = "docs/requirements/controller.json"
CORPUS = "docs/requirements/controller-witnesses.txt"
PROPERTIES = "docs/requirements/controller-properties.txt"


def git_read(ref, path):
    result = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=ROOT, capture_output=True, check=False)
    if result.returncode:
        raise ValueError(f"base requirement artifact unavailable: {path}; fetch the base revision")
    return result.stdout


def current_base():
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    event = json.loads(Path(event_path).read_text())
    return event.get("pull_request", {}).get("base", {}).get("sha")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default=current_base())
    parser.add_argument("--review-receipt", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    current = validate(load(ROOT / CATALOG))
    check_catalog_files(ROOT, current)
    if args.base_ref:
        # Distinguish absent baseline from inaccessible base commit. Never turn
        # an unavailable repository history into a waived review requirement.
        subprocess.run(["git", "cat-file", "-e", f"{args.base_ref}^{{commit}}"], cwd=ROOT, check=True, capture_output=True)
        tree = subprocess.run(["git", "ls-tree", "--name-only", args.base_ref, "--", CATALOG], cwd=ROOT, check=True, capture_output=True, text=True)
        if tree.stdout.strip():
            before = validate(json.loads(git_read(args.base_ref, CATALOG)))
            sha = lambda data: hashlib.sha256(data).hexdigest()
            report = compare(before, current, sha(git_read(args.base_ref, CORPUS)), sha((ROOT / CORPUS).read_bytes()))
            base_properties = sha(git_read(args.base_ref, PROPERTIES))
            properties = sha((ROOT / PROPERTIES).read_bytes())
            report.update(base_properties_sha256=base_properties, current_properties_sha256=properties)
            if base_properties != properties:
                report["changes"].append({"category": "property_phases", "before": base_properties, "after": properties})
                report["requires_independent_review"] = True
                report["review_status"] = "required"
            if report["requires_independent_review"] and args.review_receipt:
                receipt = load(args.review_receipt)
                matches = review_matches(report, receipt) and all(receipt.get(k) == report[k] for k in ("base_properties_sha256", "current_properties_sha256"))
                if matches:
                    report["review_status"] = "external_receipt_matches_changes_identity_not_authenticated"
        else:
            report = {"schema_version": 1, "review_status": "new_baseline_awaiting_independent_review", "requires_independent_review": False, "validation_only": True}
    else:
        report = {"schema_version": 1, "review_status": current["review"]["status"], "requires_independent_review": False, "validation_only": True, "limitation": "No PR base provided; no change review was performed"}
    text = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(text, end="")
    return int(report["requires_independent_review"] and report["review_status"] != "external_receipt_matches_changes_identity_not_authenticated")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"requirement change gate: {error}", file=sys.stderr)
        sys.exit(2)

#!/usr/bin/env python3
"""Fail-closed application fault sensitivity + fresh-process replay gate.

Requires an already-built controller executable. Do not invoke this script under
Kyoto's compile-only policy: it executes Rust, including each evidence replay.
"""
import argparse
from pathlib import Path
import subprocess
import sys

from review_requirements import load, validate


def check(report, catalog, directory, replay=None):
    validate(catalog)
    if report.get("schema_version") != 1 or report.get("model") != catalog["model"] or not report.get("build"):
        raise ValueError("missing/mismatched source-bound report identity")
    results = report.get("results")
    if not isinstance(results, list):
        raise ValueError("missing mutation results")
    seen = set()
    for result in results:
        name = result.get("mutation")
        if name in seen or name not in catalog["mutations"]:
            raise ValueError("duplicate or unexpected mutation")
        seen.add(name)
        if result.get("target") != catalog["mutations"][name]:
            raise ValueError("required target renamed or removed")
        if result.get("status") != "killed":
            raise ValueError(f"{name}: {result.get('status')}; qualification incomplete")
        for label in ("original", "minimized"):
            expected = f"{name}-{label}.sttrace"
            if result.get(label) != expected:
                raise ValueError("missing/noncanonical replay witness path")
            path = directory / expected
            if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                raise ValueError("missing/empty/symlink witness")
            if replay is not None:
                replay(name, path)
    if seen != set(catalog["mutations"]):
        raise ValueError("required mutation missing; no green from deleted tests")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--catalog", type=Path, default=Path("docs/requirements/controller.json"))
    parser.add_argument("--controller", required=True, type=Path)
    args = parser.parse_args(argv)
    executable = args.controller.resolve()
    def replay(name, path):
        subprocess.run([str(executable), "replay", name, str(path)], check=True, timeout=60)
    check(load(args.directory / "mutations.json"), load(args.catalog), args.directory, replay)
    subprocess.run([str(executable), "regressions"], check=True, timeout=60)
    print("All required mutations killed with both witnesses replayed in fresh processes; retained regressions passed.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"reference mutation gate: {error}", file=sys.stderr)
        sys.exit(1)

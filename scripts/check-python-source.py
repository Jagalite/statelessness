#!/usr/bin/env python3
"""Bind an installed Python port to checkout sources without importing the engine.

Run with the same isolated interpreter used by portable conformance:
python -I scripts/check-python-source.py [--evidence NEW_PATH]
This checks package identity only; it does not execute conformance or install code.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def python_hashes(root):
    """Include the complete Python source set, including extra installed modules."""
    root = Path(root)
    if not (root / "__init__.py").is_file():
        raise ValueError(f"package has no source __init__.py: {root}")
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*.py")) if path.is_file()}


def declared_version(root):
    tree = ast.parse((Path(root) / "__init__.py").read_text(encoding="utf-8"))
    versions = [node.value.value for node in tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
    if len(versions) != 1:
        raise ValueError(f"expected one literal __version__ declaration: {root}")
    return versions[0]


def compare_sources(source, installed, expected_version, distribution_version):
    """Compare code bytes and all independent package version declarations."""
    source, installed = Path(source).resolve(), Path(installed).resolve()
    expected = python_hashes(source)
    observed = python_hashes(installed)
    missing = sorted(expected.keys() - observed.keys())
    extra = sorted(observed.keys() - expected.keys())
    changed = sorted(name for name in expected.keys() & observed.keys()
                     if expected[name] != observed[name])
    versions = {"manifest": expected_version, "source": declared_version(source),
                "installed_source": declared_version(installed), "distribution": distribution_version}
    version_matches = all(value == expected_version for value in versions.values())
    passed = not (missing or extra or changed) and version_matches
    return {"passed": passed, "source_root": str(source), "installed_root": str(installed),
            "versions": versions, "versions_match": version_matches,
            "source_hashes": expected, "installed_hashes": observed,
            "missing_files": missing, "extra_files": extra, "changed_files": changed}


def installed_package():
    # Resolving a top-level spec does not import statelessness or run its callbacks.
    # The same interpreter/path policy will resolve it for the conformance runner.
    spec = importlib.util.find_spec("statelessness")
    if spec is None or spec.origin is None or spec.submodule_search_locations is None:
        raise ValueError("statelessness is not an installed source package in this interpreter")
    origin = Path(spec.origin).resolve()
    roots = [Path(path).resolve() for path in spec.submodule_search_locations]
    if origin.name != "__init__.py" or roots != [origin.parent]:
        raise ValueError("unsupported Python package layout; expected one source package directory")
    distribution = importlib.metadata.distribution("statelessness")
    return origin.parent, distribution.version


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, help="write JSON exclusively to a new path as well as stdout")
    args = parser.parse_args(argv)
    report = {"schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
              "interpreter": sys.executable, "python_version": sys.version,
              "isolated_interpreter": bool(sys.flags.isolated),
              "scope": "Installed Python source identity; no runtime or conformance qualification."}
    try:
        manifest = tomllib.loads((ROOT / "python/pyproject.toml").read_text(encoding="utf-8"))
        installed, version = installed_package()
        report.update(compare_sources(ROOT / "python/src/statelessness", installed,
                                      manifest["project"]["version"], version))
    except (OSError, ValueError, SyntaxError, importlib.metadata.PackageNotFoundError) as error:
        report.update(passed=False, error=str(error))
    encoded = json.dumps(report, indent=2) + "\n"
    if args.evidence:
        try:
            args.evidence.parent.mkdir(parents=True, exist_ok=True)
            with args.evidence.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
        except OSError as error:
            print(f"cannot write source identity evidence: {error}", file=sys.stderr)
            return 1
    print(encoded, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

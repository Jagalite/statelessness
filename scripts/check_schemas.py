#!/usr/bin/env python3
"""Validate versioned schemas and optional actual assurance/diagnostic evidence.

jsonschema is a development tool only. Missing it is an explicit failure of this
selected gate, never a passing skipped schema qualification.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--schema", type=Path)
    args = parser.parse_args(argv)
    try:
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT202012
    except ImportError as error:
        raise RuntimeError("install the development dependency jsonschema to run schema qualification") from error
    schemas = sorted((ROOT / "spec").glob("*.schema.json")) + sorted((ROOT / "docs/requirements").glob("*.schema.json"))
    resources = []
    for path in schemas:
        schema = json.loads(path.read_text())
        Draft202012Validator.check_schema(schema)
        resources.append((schema.get("$id", path.resolve().as_uri()), Resource.from_contents(schema, default_specification=DRAFT202012)))
    # Resolve only the checked local versions; schema IDs are identities, never
    # instructions to download a potentially different schema over the network.
    registry = Registry().with_resources(resources)
    validator = lambda schema: Draft202012Validator(schema, registry=registry)
    if args.report is not None:
        if args.schema is None:
            raise ValueError("--report requires --schema")
        validator(json.loads(args.schema.read_text())).validate(json.loads(args.report.read_text()))
    elif args.schema is not None:
        raise ValueError("--schema requires --report")
    requirements = json.loads((ROOT / "docs/requirements/controller.json").read_text())
    validator(json.loads((ROOT / "docs/requirements/controller.schema.json").read_text())).validate(requirements)
    # The candidate shared corpus remains candidate even when schema-valid.
    sys.path.insert(0, str(ROOT / "conformance"))
    from test_paired_profile import corpus
    validator(json.loads((ROOT / "spec/paired-v1.schema.json").read_text())).validate(corpus())
    print(f"Validated {len(schemas)} schemas, requirements, candidate corpus" + (", and actual evidence" if args.report else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, RuntimeError) as error:
        print(f"schema gate: {error}", file=sys.stderr)
        sys.exit(2)

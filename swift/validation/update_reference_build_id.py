#!/usr/bin/env python3
"""Refresh or check the Swift reference controller's source-bound build identity."""
from pathlib import Path
import argparse

ROOT = Path(__file__).resolve().parents[1] / "Sources" / "Statelessness"
SOURCES = ("ReferenceController.swift", "ReferenceControllerCodec.swift", "ReferenceCoverage.swift", "ReferenceCatalogData.swift")


def expected():
    value = 0xCBF29CE484222325
    for name in SOURCES:
        for byte in (ROOT / name).read_bytes():
            value = ((value ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return ("// Generated from the reference reducer, codec, coverage and catalog source bytes.\n"
            "// ReferenceControllerTests verifies this value against the source tree.\n"
            f'enum ReferenceControllerBuildID {{ static let value = "{value:016x}" }}\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = ROOT / "ReferenceControllerBuildID.swift"
    content = expected()
    if args.check:
        if not output.exists() or output.read_text() != content:
            raise SystemExit("Swift reference controller build identity is stale")
    else:
        output.write_text(content)
    print(content.split('"')[1])


if __name__ == "__main__":
    main()

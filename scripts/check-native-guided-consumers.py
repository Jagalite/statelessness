#!/usr/bin/env python3
"""Run the frozen installed five-engine N05 comparison through shared Kyoto.

Use native-guided-qualification.py prepare/build-native/attach-job first. This
entrypoint is execution-only and never compiles Rust or regenerates expectations.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "native-guided-support"))
from qualification import main

if __name__ == "__main__":
    raise SystemExit(main(["run", *sys.argv[1:]]))

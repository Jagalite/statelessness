#!/usr/bin/env python3
"""Prepare/build/run/seal installed N05 evidence; see N05-QUALIFICATION-HARNESS.md."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "native-guided-support"))
from qualification import main

if __name__ == "__main__":
    raise SystemExit(main())

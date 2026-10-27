#!/usr/bin/env python3
"""Execute the already-compiled finite Rust reference; never builds implicitly."""
import os
import hashlib
from pathlib import Path
import re
import subprocess

root = Path('/Volumes/seed2/Projects/stateless-n12-reference-target/debug/deps')
executables = [p for p in root.glob('n12_reference-*') if re.fullmatch(r'n12_reference-[0-9a-f]+', p.name) and p.is_file() and os.access(p, os.X_OK)]
if len(executables) != 1:
    raise SystemExit(f'expected exactly one matching precompiled reference binary, found {len(executables)}')
print(f'binary={executables[0]} sha256={hashlib.sha256(executables[0].read_bytes()).hexdigest()}', flush=True)
raise SystemExit(subprocess.call([str(executables[0]), '--nocapture']))

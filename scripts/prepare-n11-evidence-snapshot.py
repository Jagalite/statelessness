#!/usr/bin/env python3
"""Prepare the selected N11 evidence inputs using the N06 snapshot verifier.

The copy is writable and its source-to-executable linkage remains caller-attested.
Run verification immediately before and after all builds and tests.
"""
import importlib.util
from pathlib import Path

base = Path(__file__).resolve().with_name('prepare-n06-snapshot.py')
spec = importlib.util.spec_from_file_location('n06_snapshot', base)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.TREES += ('validation/corpus',)
module.FILES += ('scripts/prepare-n11-evidence-snapshot.py',
                 'scripts/check-native-evidence-consumers.py',
                 'scripts/tests/test_native_evidence_existing_file.py')
module.main()

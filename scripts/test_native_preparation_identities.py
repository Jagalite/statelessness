"""Mutation controls for package compiler and XCTest proof; run through Kyoto."""
import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import os
import subprocess
import sys
import unittest

SPEC = importlib.util.spec_from_file_location(
    'native_preparation', Path(__file__).parent / 'native-parity/n12-wire/prepare.py')
PREPARE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREPARE)
INTEGRATION_SPEC = importlib.util.spec_from_file_location(
    'native_integration_seal', Path(__file__).parent / 'qualify-native-integration.py')
INTEGRATION = importlib.util.module_from_spec(INTEGRATION_SPEC)
INTEGRATION_SPEC.loader.exec_module(INTEGRATION)


class PreparationIdentities(unittest.TestCase):
    def test_rewritten_source_or_tool_manifest_cannot_rebase_queued_pin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for label in ('source', 'tools'):
                with self.subTest(label=label):
                    value = {label: {'sha256': 'original-input'}}
                    raw = (json.dumps(value, sort_keys=True) + '\n').encode()
                    path = root / (hashlib.sha256(raw).hexdigest() + '.json')
                    path.write_bytes(raw)
                    self.assertEqual(INTEGRATION.read_sealed_manifest(path), value)
                    path.write_text(json.dumps({label: {'sha256': 'replacement-input'}}))
                    with self.assertRaisesRegex(RuntimeError, 'queued SHA-256 filename'):
                        INTEGRATION.read_sealed_manifest(path)

    def test_generated_tsc_launcher_cannot_replace_manifested_compiler(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compiler = root / 'node_modules/typescript'
            (compiler / 'bin').mkdir(parents=True)
            # Python stands in for the identified interpreter; no actual TS
            # compiler is needed to check which executable source gets used.
            (compiler / 'bin/tsc').write_text("print('manifested compiler')\n")
            launcher = root / 'node_modules/.bin/tsc'
            launcher.parent.mkdir()
            launcher.write_text("#!/bin/sh\nprintf 'unmanifested replacement'\nexit 9\n")
            launcher.chmod(0o755)
            env = dict(os.environ, PATH=str(launcher.parent) + os.pathsep + os.environ.get('PATH', ''))
            result = subprocess.run(PREPARE.compiler_command(compiler, sys.executable),
                                    cwd=root, env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b'manifested compiler\n')

    def test_private_compiler_mutation_and_additions_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('package.json', 'bin/tsc', 'lib/_tsc.js'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('original ' + name)
            expected = PREPARE.compiler_snapshot(root)
            PREPARE.require_snapshot_equal(expected, PREPARE.compiler_snapshot(root), 'compiler')
            (root / 'lib/_tsc.js').write_text('different compiler implementation')
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                PREPARE.require_snapshot_equal(expected, PREPARE.compiler_snapshot(root), 'compiler')
            (root / 'lib/_tsc.js').write_text('original lib/_tsc.js')
            (root / 'lib/injected.js').write_text('new compiler input')
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                PREPARE.require_snapshot_equal(expected, PREPARE.compiler_snapshot(root), 'compiler')

    def test_missing_or_replaced_xctest_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(RuntimeError, 'executable identity missing'):
                PREPARE.swift_test_snapshot(root)
            executable = root / 'Suite.xctest/Contents/MacOS/Suite'
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b'executable fixture; never run')
            executable.chmod(0o755)
            expected = PREPARE.swift_test_snapshot(root)
            PREPARE.require_snapshot_equal(expected, PREPARE.swift_test_snapshot(root), 'XCTest')
            executable.write_bytes(b'replaced executable')
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                PREPARE.require_snapshot_equal(expected, PREPARE.swift_test_snapshot(root), 'XCTest')
            executable.unlink()
            with self.assertRaisesRegex(RuntimeError, 'executable identity missing'):
                PREPARE.swift_test_snapshot(root)

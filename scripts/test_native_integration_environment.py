"""Ensure the integration receipt cannot omit effective Cargo overrides."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    'native_integration_environment', Path(__file__).with_name('qualify-native-integration.py'))
QUALIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUALIFIER)


class IntegrationEnvironment(unittest.TestCase):
    def test_cargo_overrides_are_removed_before_any_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            raw = b'{}\n'
            expected = root / (QUALIFIER.digest(raw) + '.json')
            expected.write_bytes(raw)
            overrides = {
                'CARGO_BUILD_RUSTC': '/unbound/compiler',
                'CARGO_BUILD_RUSTC_WRAPPER': '/unbound/wrapper',
                'CARGO_BUILD_RUSTC_WORKSPACE_WRAPPER': '/unbound/workspace-wrapper',
                'CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS': '--cfg unbound',
                'CARGO_PROFILE_DEV_OPT_LEVEL': '3',
                'CARGO_ENCODED_RUSTFLAGS': '--cfg\x1funbound',
                'CARGO_ENCODED_RUSTDOCFLAGS': '--cfg\x1funbound',
                'CARGO_TARGET_DIR': '/unbound/target',
                'CARGO_INCREMENTAL': '1',
            }
            environment = dict(overrides, CARGO_HOME=str(root / 'cargo-home'))
            launches = []

            def run(command, **kwargs):
                launches.append((command, kwargs['env']))
                return mock.Mock(returncode=0)

            command = ['cargo', 'build', '--manifest-path', 'validation/corpus/Cargo.toml',
                       '--locked', '--offline', '--bin', 'native-research', '--bin',
                       'stateless-corpus', '--message-format=json', '--target-dir',
                       str(root / 'target/fresh-research'), '-j', '2']
            argv = ['qualify', '--output', str(root / 'out'), '--expected-source',
                    str(expected), '--', *command]
            with mock.patch.object(QUALIFIER, 'ROOT', root), \
                    mock.patch.object(QUALIFIER, 'PROOF', root), \
                    mock.patch.object(QUALIFIER, 'source_snapshot', return_value={}), \
                    mock.patch.object(QUALIFIER, 'tool_files', return_value={}), \
                    mock.patch.object(QUALIFIER, 'artifacts', return_value={}), \
                    mock.patch.object(QUALIFIER, 'tree_snapshot', return_value={'missing': True}), \
                    mock.patch.object(QUALIFIER.subprocess, 'check_output', return_value='fixture'), \
                    mock.patch.object(QUALIFIER.subprocess, 'run', side_effect=run), \
                    mock.patch.dict(os.environ, environment), \
                    mock.patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(QUALIFIER.main(), 0)
            self.assertEqual(launches[-1][0], command)
            self.assertEqual(len(launches), 6)
            for _, launched in launches:
                for key in overrides.keys() - {'CARGO_TARGET_DIR', 'CARGO_INCREMENTAL'}:
                    self.assertNotIn(key, launched)
                self.assertEqual(launched['CARGO_HOME'], environment['CARGO_HOME'])
                self.assertEqual(launched['CARGO_TARGET_DIR'], str(root / 'target/integration'))
                self.assertEqual(launched['CARGO_INCREMENTAL'], '0')
            receipt = json.loads((root / 'out/receipt.json').read_text())
            self.assertTrue(set(overrides) <= set(receipt['cleared_cargo_overrides']))
            self.assertNotIn('CARGO_HOME', receipt['cleared_cargo_overrides'])
            self.assertEqual(receipt['environment']['CARGO_HOME'], environment['CARGO_HOME'])


if __name__ == '__main__':
    unittest.main()

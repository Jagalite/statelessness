"""Relocated native fixture bindings must use the same inputs as source tests."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('native_ports', ROOT / 'scripts/check-native-ports.py')
ports = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ports)


class NativeFixtureBindings(unittest.TestCase):
    def test_driver_binds_causal_and_diagnostic_fixtures(self):
        with patch.dict(os.environ, {'STATELESS_CAUSAL_CORPUS': 'wrong', 'STATELESS_DIAGNOSTIC_CORPUS': 'wrong'}, clear=True):
            env = ports.fixture_environment()
        self.assertEqual(env['STATELESS_CAUSAL_CORPUS'], str(ROOT / 'conformance/causal-evidence-v2.json'))
        self.assertEqual(env['STATELESS_DIAGNOSTIC_CORPUS'], str(ROOT / 'conformance/debugger-diagnostics-v1.json'))
        for key, value in env.items():
            if key.startswith('STATELESS_') and key.endswith(('_CORPUS', '_HISTORY')):
                self.assertTrue(Path(value).is_file(), (key, value))

    @unittest.skipUnless(shutil.which('go'), 'Go is required for relocated native tests')
    def test_relocated_go_tests_execute_shared_fixture_assertions(self):
        names = ('TestCausalDiagnosticExportSharedCorpus', 'TestCausalEvidenceRecorderSharedCorpus',
                 'TestCausalReplayAndUpgradeSharedCorpus', 'TestEvidenceWireSharedCorpus',
                 'TestSharedManualHistogramCorpus')
        with tempfile.TemporaryDirectory() as directory:
            relocated = Path(directory) / 'go'
            shutil.copytree(ROOT / 'go', relocated, ignore=shutil.ignore_patterns('bin'))
            self.assertFalse((relocated.parent / 'conformance').exists())
            env = dict(ports.fixture_environment(), CGO_ENABLED='0', GOTOOLCHAIN='local',
                       GOPROXY='off', GOWORK='off', GOFLAGS='-buildvcs=false')
            result = subprocess.run(['go', 'test', '-count=1', '-v', '-run', '^(' + '|'.join(names) + ')$', './...'],
                                    cwd=relocated, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for name in names:
                self.assertIn('--- PASS: ' + name, result.stdout)


if __name__ == '__main__':
    unittest.main()

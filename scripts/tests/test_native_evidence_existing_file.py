import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest


spec = importlib.util.spec_from_file_location(
    'native_evidence', Path(__file__).resolve().parents[1] / 'check-native-evidence-consumers.py'
)
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class ExistingFileDiagnosticTests(unittest.TestCase):
    def test_expected_engine_diagnostics(self):
        examples = {
            'python': "N11_DESTINATION_CREATE_ERROR [Errno 17] File exists: '/tmp/existing-files/x'\n",
            'go': 'N11_DESTINATION_CREATE_ERROR open /tmp/existing-files/x: file exists\n',
            'typescript': "N11_DESTINATION_CREATE_ERROR Error: EEXIST: file already exists, open '/tmp/existing-files/x'\n",
            'swift': 'N11_DESTINATION_CREATE_ERROR evidence file creation failed: File exists\n',
            'rust': 'N11_DESTINATION_CREATE_ERROR File exists (os error 17)\n',
        }
        for engine, diagnostic in examples.items():
            with self.subTest(engine=engine):
                self.assertTrue(driver.is_existing_file_error(engine, diagnostic))

    def test_unrelated_nonzero_with_existing_path_is_rejected(self):
        result = subprocess.run(
            [sys.executable, '-c', 'import sys; print("unrelated failure: " + sys.argv[1]); sys.exit(1)',
             '/tmp/existing-files/sentinel'], capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        for engine in ('python', 'go', 'typescript', 'swift', 'rust'):
            with self.subTest(engine=engine):
                self.assertFalse(driver.is_existing_file_error(engine, result.stdout + result.stderr))
                self.assertFalse(driver.is_existing_file_error(engine, 'File exists (os error 17)\n'))


if __name__ == '__main__':
    unittest.main()

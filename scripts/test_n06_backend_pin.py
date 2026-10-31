"""Fail-closed controls for the offline N06 build-backend pin."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('n06_runner', Path(__file__).with_name('run-n06-snapshot.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class BackendPinTests(unittest.TestCase):
    def test_missing_wrong_name_and_changed_bytes_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                runner.validate_backend(root / runner.SETUPTOOLS_WHEEL)
            wrong = root / 'other.whl'
            wrong.write_bytes(b'fixture')
            with self.assertRaisesRegex(ValueError, 'pinned'):
                runner.validate_backend(wrong)
            wheel = root / runner.SETUPTOOLS_WHEEL
            wheel.write_bytes(b'fixture')
            with mock.patch.object(runner, 'SETUPTOOLS_SHA256', runner.digest(wheel)):
                self.assertEqual(runner.validate_backend(wheel), wheel.resolve())
                wheel.write_bytes(b'tampered')
                with self.assertRaisesRegex(ValueError, 'pinned'):
                    runner.validate_backend(wheel)


if __name__ == '__main__':
    unittest.main()

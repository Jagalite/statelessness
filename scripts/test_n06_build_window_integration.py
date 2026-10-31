import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('n06_runner', Path(__file__).with_name('run-n06-snapshot.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class BuildWindowIntegrationTests(unittest.TestCase):
    def test_binding_and_tamper_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); snapshot = root / 'snapshot'; helper = snapshot / 'source/scripts/n06-build-window.py'
            helper.parent.mkdir(parents=True); helper.write_text('helper')
            manifest = snapshot / 'source-manifest.json'; manifest.write_text('manifest')
            binary = root / 'binary'; binary.write_bytes(b'binary')
            log = root / 'log'; log.write_text('log')
            checkpoint = root / 'before.json'; window = root / 'after.json'
            job = {'requested_command':['cargo', 'build'], 'submitted': 20.0, 'finished': 30.0, 'log': str(log)}
            before = {'schema':'n06-build-window-before/1', 'snapshot':str(snapshot), 'manifest_sha256':mod.digest(manifest),
                      'helper_sha256':mod.digest(helper), 'verified_at':10.0}
            checkpoint.write_text(json.dumps(before))
            after = {'schema':'n06-build-window/1', 'snapshot':str(snapshot), 'manifest_sha256':mod.digest(manifest),
                     'checkpoint_sha256':mod.digest(checkpoint), 'kyoto_job':'job', 'requested_command':job['requested_command'],
                     'binary':str(binary), 'binary_sha256':mod.digest(binary), 'log_sha256':mod.digest(log),
                     'verified_before':10.0, 'verified_after':40.0, 'submitted':20.0, 'finished':30.0}
            window.write_text(json.dumps(after))
            self.assertEqual(mod.validate_build_window(checkpoint, window, snapshot, binary, 'job', job)['window_sha256'], mod.digest(window))
            binary.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'build-window'):
                mod.validate_build_window(checkpoint, window, snapshot, binary, 'job', job)
            binary.write_bytes(b'binary'); after['verified_after'] = 25.0; window.write_text(json.dumps(after))
            with self.assertRaisesRegex(ValueError, 'build-window'):
                mod.validate_build_window(checkpoint, window, snapshot, binary, 'job', job)


if __name__ == '__main__':
    unittest.main()

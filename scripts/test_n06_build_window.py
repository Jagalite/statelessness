import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('build_window', Path(__file__).with_name('n06-build-window.py'))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class BuildWindowTests(unittest.TestCase):
    def test_brackets_job_and_rejects_changed_snapshot_or_command(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); snapshot = root / 'snapshot'; source = snapshot / 'source'
            source.mkdir(parents=True); target = root / 'target'; binary = target / 'debug/native-campaign-public'
            binary.parent.mkdir(parents=True); binary.write_bytes(b'executable')
            log = root / 'cargo.log'; log.write_text('build complete')
            checkpoint = root / 'before.json'; output = root / 'after.json'
            argv = ['cargo', 'build', '--manifest-path', mod.MANIFEST, '--target-dir', str(target), '--bin', 'native-campaign-public']
            job = {'status': 'succeeded', 'exit_code': 0, 'cwd': str(source), 'requested_command': argv,
                   'command': argv, 'submitted': 20, 'finished': 21, 'log': str(log)}
            with patch.object(mod, 'verify', return_value='a' * 64), patch.object(mod.time, 'time', return_value=10):
                mod.create(snapshot, checkpoint)
            with patch.object(mod, 'verify', return_value='a' * 64), patch.object(mod.subprocess, 'check_output', return_value=json.dumps(job).encode()):
                result = mod.finish(checkpoint, 'job', output)
            self.assertEqual(result['binary_sha256'], mod.digest(binary))
            output.unlink()
            with patch.object(mod, 'verify', return_value='b' * 64), patch.object(mod.subprocess, 'check_output', return_value=json.dumps(job).encode()):
                with self.assertRaisesRegex(ValueError, 'manifest changed'):
                    mod.finish(checkpoint, 'job', output)
            job['submitted'] = 9
            with patch.object(mod, 'verify', return_value='a' * 64), patch.object(mod.subprocess, 'check_output', return_value=json.dumps(job).encode()):
                with self.assertRaisesRegex(ValueError, 'subsequent'):
                    mod.finish(checkpoint, 'job', output)
            job['submitted'] = 20; job['command'] = argv + ['--release']
            with patch.object(mod, 'verify', return_value='a' * 64), patch.object(mod.subprocess, 'check_output', return_value=json.dumps(job).encode()):
                with self.assertRaisesRegex(ValueError, 'direct build'):
                    mod.finish(checkpoint, 'job', output)

            job['command'] = argv
            order = []
            def show(*args, **kwargs):
                order.append('job'); return json.dumps(job).encode()
            def verify(snapshot):
                order.append('verify'); return 'a' * 64
            with patch.object(mod, 'verify', side_effect=verify), patch.object(mod.subprocess, 'check_output', side_effect=show):
                mod.finish(checkpoint, 'job', output)
            self.assertEqual(order, ['job', 'verify'])
            output.unlink()
            relative = argv.copy(); relative[5] = 'target'
            job['requested_command'] = relative; job['command'] = relative
            with patch.object(mod, 'verify', return_value='a' * 64), patch.object(mod.subprocess, 'check_output', return_value=json.dumps(job).encode()):
                with self.assertRaisesRegex(ValueError, 'target must be absolute'):
                    mod.finish(checkpoint, 'job', output)


if __name__ == '__main__':
    unittest.main()

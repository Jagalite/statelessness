"""Validation corpus edits must invalidate an explicitly sealed N12 run."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('n12',Path(__file__).with_name('qualify-n12-integration.py'))
n12=importlib.util.module_from_spec(spec)
spec.loader.exec_module(n12)

class SealedCorpusTests(unittest.TestCase):
    def test_noncode_validation_corpus_is_bound_when_declared(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            corpus=root/'validation/reference/manual-goldens.json'
            corpus.parent.mkdir(parents=True)
            corpus.write_text('{"independent_expected": 1}')
            old=(n12.ROOT,n12.GOLDEN,n12.git)
            n12.ROOT=root;n12.GOLDEN=root/'validation/fixed/os-pipe-goldens.json'
            n12.git=lambda *args:b'validation/reference/manual-goldens.json\0'
            try:
                self.assertEqual(n12.snapshot(),{}) # Mutable receipts remain excluded.
                before=n12.snapshot([corpus])
                corpus.write_text('{"independent_expected": 2}')
                self.assertNotEqual(before,n12.snapshot([corpus]))
                corpus.unlink()
                self.assertNotEqual(before,n12.snapshot([corpus]))
                self.assertEqual(n12.snapshot([corpus])['validation/reference/manual-goldens.json']['type'],'missing')
            finally:n12.ROOT,n12.GOLDEN,n12.git=old



class AcceptanceSafetyTests(unittest.TestCase):
    def test_checks_survive_optimization_and_runner_refuses_it(self):
        import subprocess, sys
        with self.assertRaisesRegex(RuntimeError, 'mismatch'):
            n12.require(False, 'mismatch')
        script = Path(__file__).with_name('qualify-n12-integration.py')
        result = subprocess.run([sys.executable, '-O', str(script)], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('python -O', result.stderr)

    def test_drain_is_bounded_when_a_descendant_keeps_the_pipe_open(self):
        import os, time
        read, write = os.pipe()
        try:
            os.write(write, b'tail')
            with os.fdopen(read, 'rb', buffering=0) as stream:
                start = time.monotonic()
                with self.assertRaisesRegex(RuntimeError, 'deadline'):
                    n12.drain(stream, time.monotonic() + 0.2)
                self.assertLess(time.monotonic() - start, 5)
        finally:
            os.close(write)


if __name__=='__main__':unittest.main()

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('n06_snapshot', Path(__file__).with_name('prepare-n06-snapshot.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / 'checkout'
        self.root.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), 'config', 'user.email', 'test@example.invalid'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'config', 'user.name', 'test'], check=True)
        (self.root / 'code').mkdir()
        (self.root / 'code' / 'engine').write_text('original')
        (self.root / 'required').write_text('pinned')
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.root), 'commit', '-qm', 'baseline'], check=True)
        self.old_trees, self.old_files = m.TREES, m.FILES
        m.TREES, m.FILES = ('code',), ('required',)

    def tearDown(self):
        m.TREES, m.FILES = self.old_trees, self.old_files
        self.tmp.cleanup()

    def test_manifest_captures_dirty_and_untracked_inputs_and_detects_drift(self):
        (self.root / 'code' / 'engine').write_text('dirty')
        (self.root / 'code' / 'extra').write_text('untracked')
        manifest = m.freeze(self.root, self.base / 'receipt')
        copy = self.base / 'receipt' / 'source'
        self.assertEqual((copy / 'code' / 'engine').read_text(), 'dirty')
        self.assertEqual(m.selected(copy), manifest['files'])
        self.assertEqual(m.verify(self.base / 'receipt')['files'], manifest['files'])
        self.assertIn(' M code/engine', manifest['git_status'])
        os.chmod(copy / 'code' / 'engine', 0o755)
        with self.assertRaises(ValueError):
            m.verify(self.base / 'receipt')
        os.chmod(copy / 'code' / 'engine', manifest['files']['code/engine']['mode'])
        (self.root / 'code' / 'new').write_text('later')
        self.assertNotEqual(m.selected(self.root), manifest['files'])
        (copy / 'code' / 'new').write_text('injected')
        with self.assertRaises(ValueError):
            m.verify(self.base / 'receipt')

    def test_refuses_in_tree_destination_and_source_symlink(self):
        with self.assertRaises(ValueError):
            m.freeze(self.root, self.root / 'output')
        (self.root / 'code' / 'alias').symlink_to(self.root / 'required')
        with self.assertRaises(ValueError):
            m.freeze(self.root, self.base / 'receipt')


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Regression tests for exported-source and Git provenance detection."""
from pathlib import Path, PureWindowsPath
import runpy
import subprocess
import tomllib
import shutil
import tempfile
import unittest
from unittest.mock import patch
from qualification_context import git_context


class ProvenanceTests(unittest.TestCase):
    def test_source_archive_has_unknown_git_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            context = git_context(directory)
            self.assertFalse(context["available"])
            self.assertIsNone(context["commit"])
            self.assertIsNone(context["status"])

    @unittest.skipUnless(shutil.which("git"), "Git binary unavailable; metadata-free archive tests still run")
    def test_nested_archive_does_not_inherit_parent_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            nested = root / "exported-source"
            nested.mkdir()
            self.assertFalse(git_context(nested)["available"])

    @unittest.skipUnless(shutil.which("git"), "Git binary unavailable; metadata-free archive tests still run")
    def test_exact_repository_reports_clean_and_dirty_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "fixture").write_text("initial\n")
            subprocess.run(["git", "add", "fixture"], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=qualification fixture", "-c",
                            "user.email=fixture@localhost", "commit", "-q", "-m", "fixture"],
                           cwd=root, check=True)
            context = git_context(root)
            self.assertTrue(context["available"])
            self.assertEqual(context["status"], "")
            (root / "fixture").write_text("modified\n")
            changed = git_context(root)
            self.assertEqual(changed["commit"], context["commit"])
            self.assertEqual(changed["status"], " M fixture\n")

    def test_missing_git_is_not_a_source_test_failure(self):
        with patch("qualification_context.subprocess.run", side_effect=FileNotFoundError):
            self.assertFalse(git_context(Path.cwd())["available"])


class ConsumerManifestTests(unittest.TestCase):
    def test_inspect_consumer_paths_round_trip_as_toml(self):
        script = runpy.run_path(str(Path(__file__).with_name("check-inspect-derive.py")))
        for root in (Path('/tmp/space and "quotes"/source'), Path('/tmp/test-🐈'),
                     PureWindowsPath(r'C:\work area\source'),
                     PureWindowsPath(r'\\server\share\source')):
            with self.subTest(root=str(root)):
                manifest = tomllib.loads(script["consumer_manifest"](root))
                for alias, crate in (("macros", "statelessness-macros"),
                                     ("display", "statelessness-debug")):
                    self.assertEqual(manifest["dependencies"][alias]["path"],
                                     str(root / "crates" / crate))


if __name__ == "__main__":
    unittest.main()

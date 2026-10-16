"""Installed-source identity checks using temporary packages; never install code."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("check_python_source", Path(__file__).with_name("check-python-source.py"))
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class SourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.installed = self.root / "installed"
        for package in (self.source, self.installed):
            package.mkdir()
            (package / "__init__.py").write_text('__version__ = "0.1.0"\n', encoding="utf-8")
            (package / "core.py").write_text("def step(): return 1\n", encoding="utf-8")

    def compare(self, distribution_version="0.1.0"):
        return CHECK.compare_sources(self.source, self.installed, "0.1.0", distribution_version)

    def test_exact_sources_and_all_versions_match(self):
        result = self.compare()
        self.assertTrue(result["passed"])
        self.assertEqual(result["source_hashes"], result["installed_hashes"])
        self.assertEqual(set(result["source_hashes"]), {"__init__.py", "core.py"})

    def test_same_version_does_not_hide_changed_missing_or_extra_code(self):
        (self.installed / "core.py").write_text("def step(): return 2\n", encoding="utf-8")
        result = self.compare()
        self.assertFalse(result["passed"])
        self.assertTrue(result["versions_match"])
        self.assertEqual(result["changed_files"], ["core.py"])
        (self.installed / "core.py").unlink()
        self.assertEqual(self.compare()["missing_files"], ["core.py"])
        (self.installed / "unexpected.py").write_text("pass\n", encoding="utf-8")
        self.assertEqual(self.compare()["extra_files"], ["unexpected.py"])

    def test_distribution_and_runtime_version_disagreement_fail(self):
        self.assertFalse(self.compare("0.2.0")["passed"])
        for package in (self.source, self.installed):
            (package / "__init__.py").write_text('__version__ = "0.2.0"\n', encoding="utf-8")
        result = self.compare("0.2.0")
        self.assertEqual(result["source_hashes"], result["installed_hashes"])
        self.assertFalse(result["passed"])

    def test_nested_modules_are_bound_and_cache_files_are_not_sources(self):
        (self.installed / "__pycache__").mkdir()
        (self.installed / "__pycache__/core.cpython-313.pyc").write_bytes(b"cache")
        self.assertTrue(self.compare()["passed"])
        for package in (self.source, self.installed):
            (package / "nested").mkdir()
            (package / "nested/helpers.py").write_text("pass\n", encoding="utf-8")
        self.assertTrue(self.compare()["passed"])
        (self.installed / "nested/helpers.py").write_text("raise RuntimeError\n", encoding="utf-8")
        self.assertEqual(self.compare()["changed_files"], ["nested/helpers.py"])

    def test_versions_are_parsed_without_importing_package_code(self):
        (self.installed / "__init__.py").write_text('raise AssertionError("must not execute")\n__version__ = "0.1.0"\n', encoding="utf-8")
        self.assertEqual(CHECK.declared_version(self.installed), "0.1.0")
        (self.installed / "__init__.py").write_text("__version__ = make_version()\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "literal __version__"):
            CHECK.declared_version(self.installed)

    def test_actual_import_location_is_used_and_missing_package_fails(self):
        spec = SimpleNamespace(origin=str(self.installed / "__init__.py"), submodule_search_locations=[str(self.installed)])
        with patch.object(CHECK.importlib.util, "find_spec", return_value=spec), patch.object(CHECK.importlib.metadata, "distribution", return_value=SimpleNamespace(version="0.1.0")):
            self.assertEqual(CHECK.installed_package(), (self.installed.resolve(), "0.1.0"))
        with patch.object(CHECK.importlib.util, "find_spec", return_value=None):
            with self.assertRaisesRegex(ValueError, "not an installed source package"):
                CHECK.installed_package()

    def test_cli_retains_failure_receipt_and_never_overwrites(self):
        evidence = self.root / "evidence.json"
        with patch.object(CHECK, "installed_package", side_effect=ValueError("package missing")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(CHECK.main(["--evidence", str(evidence)]), 1)
        original = evidence.read_bytes()
        self.assertFalse(json.loads(original)["passed"])
        self.assertEqual(json.loads(original)["error"], "package missing")
        with patch.object(CHECK, "installed_package", side_effect=ValueError("different error")), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(CHECK.main(["--evidence", str(evidence)]), 1)
        self.assertEqual(evidence.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

"""Regression checks for generated binding distributions (stdlib only)."""
from pathlib import Path
import json
import runpy
import tempfile
import unittest
from unittest.mock import patch

builder = runpy.run_path(str(Path(__file__).with_name("build-bindings.py")))


class Bundles(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name, text="fixture"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def stage(self):
        for name in builder["SWIFT_FILES"]:
            self.write("source/bindings/" + name)
        for name in ["LICENSE", "bindings/README.md", "bindings/c/README.md"]:
            self.write("source/" + name)
        for name in ["stateless.mjs", "adapter.mjs", "README.md", "index.html", "self-test.html"]:
            self.write("source/bindings/browser/" + name)
        self.write("source/bindings/browser/package.json", '{"name":"test-package","version":"0.1.0"}')
        self.write("source/bindings/swift/Sources/private-notes.swift")
        self.write("source/bindings/c/.DS_Store")
        stage = self.root / "stage"
        components = builder["stage_sources"](self.root / "source", stage, "all", "9.8.7")
        return stage, components

    def test_version_and_public_file_allowlist(self):
        stage, _ = self.stage()
        self.assertEqual(json.loads((stage / "browser/package.json").read_text())["version"], "9.8.7")
        self.assertFalse((stage / "swift/swift/Sources/private-notes.swift").exists())
        self.assertFalse((stage / "swift/c/.DS_Store").exists())

    def test_full_publish_removes_obsolete_files_and_partial_manifests(self):
        stage, components = self.stage()
        self.write("output/swift/swift/Sources/obsolete.swift")
        self.write("output/manifest-native.json")
        self.write("output/manifest-browser.json")
        builder["publish"](stage, self.root / "output", components, "all", "9.8.7", "test-host")
        self.assertFalse((self.root / "output/swift/swift/Sources/obsolete.swift").exists())
        self.assertFalse((self.root / "output/manifest-native.json").exists())
        manifest = json.loads((self.root / "output/manifest-all.json").read_text())
        actual = {p.relative_to(self.root / "output").as_posix()
                  for p in (self.root / "output").rglob("*") if p.is_file() and p.name != "manifest-all.json"}
        self.assertEqual(set(manifest["files"]), actual)

    def test_partial_publish_preserves_unselected_bundle(self):
        stage, _ = self.stage()
        self.write("output/native/keep", "native")
        self.write("output/manifest-native.json", "native-manifest")
        self.write("output/manifest-all.json", "obsolete")
        builder["publish"](stage, self.root / "output", ["browser", "browser-fixture"], "browser", "9.8.7", "host")
        self.assertEqual((self.root / "output/native/keep").read_text(), "native")
        self.assertEqual((self.root / "output/manifest-native.json").read_text(), "native-manifest")
        self.assertFalse((self.root / "output/manifest-all.json").exists())

    def test_interrupted_publish_restores_previous_bundle_and_manifest(self):
        stage, _ = self.stage()
        self.write("output/browser/old", "old-browser")
        self.write("output/browser-fixture/old", "old-fixture")
        self.write("output/manifest-all.json", "old-manifest")
        rename = Path.rename
        def fail_once(path, target):
            if path == stage / "browser-fixture":
                raise OSError("injected installation failure")
            return rename(path, target)
        with patch.object(Path, "rename", fail_once):
            with self.assertRaisesRegex(OSError, "injected"):
                builder["publish"](stage, self.root / "output", ["browser", "browser-fixture"], "browser", "9.8.7", "host")
        self.assertEqual((self.root / "output/browser/old").read_text(), "old-browser")
        self.assertEqual((self.root / "output/browser-fixture/old").read_text(), "old-fixture")
        self.assertEqual((self.root / "output/manifest-all.json").read_text(), "old-manifest")
        self.assertFalse((self.root / "output/manifest-browser.json").exists())


if __name__ == "__main__":
    unittest.main()

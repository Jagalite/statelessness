"""Regression checks for generated binding distributions (stdlib only)."""
from pathlib import Path
import json
import os
import shutil
import runpy
import subprocess
import sys
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

    def test_python_distribution_lists_every_package_module(self):
        package = builder["ROOT"] / "python/src/statelessness"
        modules = {"src/statelessness/" + path.name for path in package.glob("*.py")}
        self.assertLessEqual(modules, set(builder["PYTHON_FILES"]))

    def test_real_python_distribution_is_complete_and_importable(self):
        stage = self.root / "real-stage"
        builder["stage_sources"](builder["ROOT"], stage, "native", "9.8.7", True)
        package = stage / "python"
        actual = {path.relative_to(package).as_posix()
                  for path in package.rglob("*") if path.is_file()}
        self.assertEqual(actual, set(builder["PYTHON_FILES"]))
        for name in actual:
            self.assertEqual((package / name).read_bytes(),
                             (builder["ROOT"] / "python" / name).read_bytes())
        # An isolated interpreter must resolve every module from the bundle,
        # never an installed package or this checkout's source directory.
        probe = """
import importlib, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
for path in sorted((root / "statelessness").glob("*.py")):
    name = "statelessness" if path.stem == "__init__" else "statelessness." + path.stem
    module = importlib.import_module(name)
    if pathlib.Path(module.__file__).resolve() != path.resolve():
        raise RuntimeError("module escaped staged bundle: " + name)
"""
        result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", probe,
                                 str(package / "src")], cwd=self.root,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_portable_swift_distribution_lists_every_target_source(self):
        root = builder["ROOT"]
        sources = {path.relative_to(root).as_posix()
                   for target in ("Statelessness", "StatelessConformance")
                   for path in (root / "swift/Sources" / target).rglob("*.swift")}
        self.assertEqual(sources, set(builder["PORTABLE_SWIFT_FILES"]))

    def test_real_portable_swift_distribution_is_complete(self):
        stage = self.root / "real-stage"
        builder["stage_sources"](builder["ROOT"], stage, "native", "9.8.7", True)
        package = stage / "portable-swift"
        actual = {path.relative_to(package).as_posix()
                  for path in package.rglob("*") if path.is_file()}
        self.assertEqual(actual, {*builder["PORTABLE_SWIFT_FILES"], "Package.swift", "LICENSE"})
        for name in builder["PORTABLE_SWIFT_FILES"]:
            self.assertEqual((package / name).read_bytes(),
                             (builder["ROOT"] / name).read_bytes())
        self.assertEqual((package / "Package.swift").read_text(),
                         builder["PORTABLE_SWIFT_MANIFEST"])

    @unittest.skipUnless(shutil.which("swift"), "Swift toolchain not installed")
    def test_relocated_swift_v2_target_builds_from_staged_sources(self):
        stage = self.root / "relocated-distribution"
        builder["stage_sources"](builder["ROOT"], stage, "native", "9.8.7", True)
        environment = dict(os.environ)
        environment["CLANG_MODULE_CACHE_PATH"] = str(self.root / "clang-cache")
        # Compiling the wrapper target follows the relocated dependency and
        # proves source closure, without requiring a Rust native library link.
        result = subprocess.run([
            shutil.which("swift"), "build", "--package-path", str(stage / "swift-v2"),
            "--scratch-path", str(self.root / "swift-build"),
            "--cache-path", str(self.root / "swift-cache"),
            "--config-path", str(self.root / "swift-config"),
            "--security-path", str(self.root / "swift-security"),
            "--target", "StatelessNativeV2", "--jobs", "2",
        ], cwd=self.root, env=environment, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_v2_is_an_explicit_additive_distribution(self):
        self.stage()
        self.write("source/bindings/c/stateless_v2.h")
        self.write("source/docs/ABI-V2.md")
        for name in builder["PYTHON_FILES"]:
            self.write("source/python/" + name)
        self.write("source/python/src/statelessness/local_notes.py")
        for name in builder["BROWSER_V2_FILES"]:
            self.write("source/bindings/browser/" + name)
        for name in builder["SWIFT_V2_FILES"]:
            self.write("source/bindings/swift-v2/" + name)
        self.write("source/bindings/swift-v2/Package.swift", '.package(name: "Statelessness", path: "../..")')
        for name in builder["PORTABLE_SWIFT_FILES"]:
            self.write("source/" + name)
        self.write("source/swift/Sources/Statelessness/local_notes.swift")
        self.write("source/swift/Sources/StatelessConformance/local_notes.swift")
        self.write("source/swift/Sources/StatelessDebug/Private.swift")
        for name in builder["GO_V2_FILES"]:
            self.write("source/bindings/go/v2/" + name)
        self.write("source/bindings/go/go.mod", "module github.com/Jagalite/statelessness/bindings/go\n\ngo 1.24\n")
        self.write("source/bindings/go/v2/bridge.h", '#include "../../c/stateless_v2.h"')
        stage = self.root / "v2-stage"
        components = builder["stage_sources"](self.root / "source", stage, "all", "9.8.7", True)
        metadata = json.loads((stage / "browser/package.json").read_text())
        self.assertEqual(metadata["exports"]["./v2"], {"types": "./v2.d.ts", "import": "./v2.mjs"})
        self.assertIn("python", components)
        self.assertTrue((stage / "native/include/stateless.h").exists())
        self.assertTrue((stage / "native/include/stateless_v2.h").exists())
        self.assertTrue((stage / "python/src/statelessness/native.py").exists())
        self.assertFalse((stage / "python/src/statelessness/local_notes.py").exists())
        self.assertIn('path: "../portable-swift"', (stage / "swift-v2/Package.swift").read_text())
        self.assertIn('#include "stateless_v2.h"', (stage / "swift-v2/c/shim.h").read_text())
        self.assertTrue((stage / "portable-swift/swift/Sources/StatelessConformance/Conformance.swift").exists())
        self.assertFalse((stage / "portable-swift/swift/Sources/Statelessness/local_notes.swift").exists())
        self.assertFalse((stage / "portable-swift/swift/Sources/StatelessConformance/local_notes.swift").exists())
        self.assertFalse((stage / "portable-swift/swift/Sources/StatelessDebug").exists())
        self.assertIn('#include "../include/stateless_v2.h"', (stage / "go-v2/v2/bridge.h").read_text())
        self.assertTrue((stage / "go-v2/include/stateless_v2.h").exists())
        self.assertFalse((self.root / "stage/native/include/stateless_v2.h").exists())

    def test_native_rebuild_retires_only_previously_managed_optional_sources(self):
        stage, _ = self.stage()
        self.write("output/python/old-native.py")
        self.write("output/local-notes/keep")
        self.write("output/manifest-native.json", json.dumps({"components": ["native", "swift", "python"]}))
        builder["publish"](stage, self.root / "output", ["native", "swift"], "native", "9.8.7", "host")
        self.assertFalse((self.root / "output/python").exists())
        self.assertTrue((self.root / "output/local-notes/keep").exists())

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

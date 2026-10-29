#!/usr/bin/env python3
"""Bounded stdlib guards, run only inside the immutable Swift Kyoto job.

Usage: python -B -E -s <snapshot>/guards.py <output>/guard-work
All fixtures and expected-negative receipts remain in guard-work. No Swift,
compiler, git, or scheduler command is executed by this suite. main() receipt
tests use scratch seals and block external launches; process tests launch only
the current Python interpreter in owned sessions with independent watchdogs.
"""
import sys

# Set this before importing either sealed runner, including without -B.
sys.dont_write_bytecode = True

import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import signal
import stat
import subprocess
import time
import unittest
from unittest import mock


SCRIPT_DIRECTORY = Path(__file__).resolve(strict=True).parent
WORK_ROOT = None
REAL_POPEN = subprocess.Popen
REAL_WAITID = getattr(os, "waitid", None)
REAL_KILLPG = os.killpg


def load_sealed(name):
    path = SCRIPT_DIRECTORY / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if Path(module.__file__).resolve(strict=True) != path:
        raise RuntimeError("guard imported a runner outside its sealed directory")
    return module


qualify = load_sealed("qualify")
snapshot = load_sealed("snapshot")


class ModuleProxy:
    """Override a runner's module reference, never the shared os module."""

    def __init__(self, module, **overrides):
        self._module = module
        self.__dict__.update(overrides)

    def __getattr__(self, name):
        return getattr(self._module, name)


def emit(event, **details):
    print(json.dumps(dict(event=event, **details), sort_keys=True), flush=True)


def write_file(path, data=b"payload\n", mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)
    return path


@contextlib.contextmanager
def restored_signal_state():
    watched = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGCHLD)
    handlers = {sig: signal.getsignal(sig) for sig in watched}
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    try:
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        yield
    finally:
        # Unblock while the recording handlers are still installed. In-process
        # main() tests inject observations, not actual pending fatal signals.
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.work = WORK_ROOT / self.id().split(".", 1)[-1]
        self.work.mkdir(parents=True, exist_ok=False)
        emit("test-fixture", test=self.id(), path=str(self.work))

    def make_checkout(self, parent=None):
        root = (parent or self.work) / "t3-25250336"
        for relative, data in (
            ("Package.swift", b"package manifest\n"),
            ("swift/Sources/Library/main.swift", b"source\n"),
            ("swift/Tests/LibraryTests/test.swift", b"test\n"),
            ("examples/native-campaign/swift/Package.swift", b"consumer manifest\n"),
            ("examples/native-campaign/swift/Sources/Consumer/main.swift", b"consumer\n"),
            ("conformance/corpus.json", b"{}\n"),
            ("conformance/rows.tsv", b"key\tvalue\n"),
        ):
            write_file(root / relative, data)
        (root / "swift/Sources/empty").mkdir()
        for name in ("qualify.py", "snapshot.py", "guards.py"):
            write_file(root / "validation/native-parity/n06/swift" / name,
                       (SCRIPT_DIRECTORY / name).read_bytes())
        return root

    def make_seal(self, parent=None):
        source = (parent or self.work) / "sealed"
        source.mkdir(parents=True)
        write_file(source / "package/payload.swift", b"original package\n")
        write_file(source / "consumer/payload.swift", b"original consumer\n")
        for name in ("qualify.py", "snapshot.py", "guards.py"):
            write_file(source / name, (SCRIPT_DIRECTORY / name).read_bytes())
        identity = {"identity_file_mode": 0o644,
                    "snapshot_entries": qualify.manifest(source)}
        encoded = (json.dumps(identity, sort_keys=True) + "\n").encode()
        write_file(source / "source-identity.json", encoded)
        return source, hashlib.sha256(encoded).hexdigest()

    def run_qualification(self, source, digest, *, output=None, driver=None,
                          popen=None, interruptions=None, extra_patches=()):
        output = output or source.parent / "output"
        launches = []

        def launch(*args, **kwargs):
            launches.append({"argv": args[0], "new_session": kwargs.get("start_new_session")})
            if popen is None:
                raise RuntimeError("guard launch fence: no external command authorized")
            return popen(*args, **kwargs)

        with contextlib.ExitStack() as stack:
            stack.enter_context(restored_signal_state())
            stack.enter_context(mock.patch.object(qualify, "__file__", str(driver or source / "qualify.py")))
            stack.enter_context(mock.patch.object(sys, "argv", [str(source / "qualify.py"), str(source), str(output), digest]))
            stack.enter_context(mock.patch.object(qualify, "subprocess", ModuleProxy(subprocess, Popen=launch)))
            if interruptions is not None:
                stack.enter_context(mock.patch.object(qualify, "InterruptionRequests", return_value=interruptions))
            for patch in extra_patches:
                stack.enter_context(patch)
            with self.assertRaises(SystemExit) as raised:
                qualify.main()
        result = json.loads((output / "result.json").read_text())
        self.assertEqual(raised.exception.code, result["exit_code"], result)
        self.assertNotEqual(result["exit_code"], 0, result)
        self.assertNotEqual(result["status"], "passed-focused-swift", result)
        emit("expected-qualification-failure", output=str(output), result=result, launches=launches)
        return result, launches

    def run_snapshot(self, root, revision, *, fail=None, extra_patches=()):
        base = root / "validation/native-parity/n06/swift"
        calls = []

        def git_output(argv, **kwargs):
            calls.append(argv)
            self.assertEqual(kwargs["cwd"], root)
            if argv == ["git", "rev-parse", "HEAD"]:
                return "a" * 40 + "\n"
            self.assertEqual(argv, ["git", "branch", "--show-current"])
            return "t3/native-campaign-runtime\n"

        def git_run(argv, **kwargs):
            calls.append(argv)
            self.assertEqual(argv[:3], ["git", "merge-base", "--is-ancestor"])
            return subprocess.CompletedProcess(argv, 0)

        def forbidden(*args, **kwargs):
            raise AssertionError("snapshot guard attempted a real process launch")

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(snapshot, "__file__", str(base / "snapshot.py")))
            stack.enter_context(mock.patch.object(sys, "argv", [str(base / "snapshot.py"), revision]))
            stack.enter_context(mock.patch.object(snapshot, "subprocess", ModuleProxy(
                subprocess, check_output=git_output, run=git_run, Popen=forbidden)))
            for patch in extra_patches:
                stack.enter_context(patch)
            if fail is None:
                snapshot.main()
            else:
                with self.assertRaisesRegex(fail[0], fail[1]):
                    snapshot.main()
        receipt_path = base / "receipts" / (revision + "-snapshot.json")
        receipt = json.loads(receipt_path.read_text())
        emit("snapshot-receipt", path=str(receipt_path), receipt=receipt, mocked_git_calls=calls)
        if fail is not None:
            self.assertEqual(receipt["status"], "failed")
            self.assertIn("traceback", receipt)
            self.assertEqual(calls, [], "negative fixtures must fail before git metadata")
        return receipt


class ManifestGuards(FixtureCase):
    def test_complete_file_directory_symlink_identity(self):
        root = self.work / "tree"
        root.mkdir(mode=0o750)
        root.chmod(0o750)
        write_file(root / "data", b"abc", 0o640)
        (root / "empty").mkdir()
        (root / "empty").chmod(0o711)
        (root / "link").symlink_to("data")
        (root / "dangling").symlink_to("missing")
        expected = [
            {"path": ".", "type": "directory", "mode": 0o750},
            {"path": "dangling", "type": "symlink", "mode": stat.S_IMODE((root / "dangling").lstat().st_mode), "target": "missing"},
            {"path": "data", "type": "file", "mode": 0o640, "size": 3,
             "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"},
            {"path": "empty", "type": "directory", "mode": 0o711},
            {"path": "link", "type": "symlink", "mode": stat.S_IMODE((root / "link").lstat().st_mode), "target": "data"},
        ]
        self.assertEqual(qualify.manifest(root), expected)

    def test_hash_mode_membership_type_and_link_target_changes_are_visible(self):
        for mutation in ("content", "mode", "add", "delete", "type", "target", "directory-mode"):
            with self.subTest(mutation=mutation):
                root = self.work / mutation
                file = write_file(root / "payload", b"abc", 0o644)
                (root / "link").symlink_to("payload")
                before = qualify.manifest(root)
                if mutation == "content":
                    file.write_bytes(b"xyz")  # Same length, different digest.
                elif mutation == "mode":
                    file.chmod(0o600)
                elif mutation == "add":
                    (root / "empty").mkdir()
                elif mutation == "delete":
                    file.unlink()
                elif mutation == "type":
                    file.unlink()
                    file.mkdir()
                elif mutation == "target":
                    (root / "link").unlink()
                    (root / "link").symlink_to("elsewhere")
                else:
                    root.chmod(0o700 if stat.S_IMODE(root.stat().st_mode) != 0o700 else 0o750)
                self.assertNotEqual(before, qualify.manifest(root))

    def test_only_exact_root_swiftpm_directory_is_excluded(self):
        root = self.work / "tree"
        write_file(root / "payload")
        before = qualify.manifest(root)
        write_file(root / ".swiftpm/state.json")
        self.assertEqual(qualify.manifest(root, generated=True), before)
        self.assertNotEqual(qualify.manifest(root), before)
        for relative in (".build/file", "nested/.swiftpm/file", ".swiftpm-extra/file", "Resources/data"):
            write_file(root / relative)
        paths = {entry["path"] for entry in qualify.manifest(root, generated=True)}
        self.assertTrue({".build/file", "nested/.swiftpm/file", ".swiftpm-extra/file", "Resources/data"} <= paths)
        self.assertNotIn(".swiftpm", paths)

    def test_generated_file_or_symlink_is_rejected(self):
        for kind in ("file", "directory-symlink", "dangling-symlink"):
            with self.subTest(kind=kind):
                root = self.work / kind
                root.mkdir()
                if kind == "file":
                    write_file(root / ".swiftpm")
                else:
                    if kind == "directory-symlink":
                        (root / "target").mkdir()
                    (root / ".swiftpm").symlink_to("target")
                with self.assertRaisesRegex(RuntimeError, "bookkeeping must be a directory"):
                    qualify.manifest(root, generated=True)

    def test_identity_exclusion_is_exact_and_fifo_is_rejected(self):
        root = self.work / "tree"
        write_file(root / "source-identity.json")
        write_file(root / "nested/source-identity.json")
        paths = {entry["path"] for entry in qualify.manifest(root, exclude_identity=True)}
        self.assertNotIn("source-identity.json", paths)
        self.assertIn("nested/source-identity.json", paths)
        os.mkfifo(root / "pipe")
        with self.assertRaisesRegex(RuntimeError, "unsupported input type: pipe"):
            qualify.manifest(root)

    def test_ordered_rejects_duplicates_without_mutating_input(self):
        entries = [{"path": "b", "type": "file"}, {"path": "a", "type": "directory"}]
        self.assertEqual([entry["path"] for entry in qualify.ordered(entries)], ["a", "b"])
        self.assertEqual([entry["path"] for entry in entries], ["b", "a"])
        with self.assertRaisesRegex(RuntimeError, "duplicate path"):
            qualify.ordered(entries + [{"path": "a", "type": "file"}])

    def test_subtree_requires_directory_and_exact_prefix(self):
        entries = [{"path": "package", "type": "directory", "mode": 0o755},
                   {"path": "package/file", "type": "file", "mode": 0o644},
                   {"path": "package-other/file", "type": "file"}]
        self.assertEqual(qualify.subtree(entries, "package"),
                         [{"path": ".", "type": "directory", "mode": 0o755},
                          {"path": "file", "type": "file", "mode": 0o644}])
        self.assertEqual(entries[0]["path"], "package")
        with self.assertRaisesRegex(RuntimeError, "subtree missing"):
            qualify.subtree(entries[1:], "package")
        with self.assertRaisesRegex(RuntimeError, "not a directory"):
            qualify.subtree([{"path": "package", "type": "file"}], "package")


class PathAndExecutableGuards(FixtureCase):
    def test_disjoint_siblings_and_missing_outputs(self):
        source = self.work / "source"
        source.mkdir()
        qualify.require_disjoint(source, self.work / "output/not-created")
        for output in (source, source / "child/not-created", self.work):
            with self.subTest(output=str(output)), self.assertRaises(RuntimeError):
                qualify.require_disjoint(source, output)

    def test_symlink_aliases_and_ancestor_aliases(self):
        source = self.work / "source"
        source.mkdir()
        alias = self.work / "alias"
        alias.symlink_to(source, target_is_directory=True)
        for left, right in ((source, alias), (source, alias / "future/deep"),
                            (alias / "future/deep", source)):
            with self.subTest(left=str(left), right=str(right)), self.assertRaises(RuntimeError):
                qualify.require_disjoint(left, right)
        # main() also canonicalizes /tmp and symlink aliases before this helper.
        with self.assertRaises(RuntimeError):
            qualify.require_disjoint(source.resolve(), (alias / "future").resolve())

    def test_case_alias_ancestor_identity_branches(self):
        source = self.work / "Snapshot"
        alias = self.work / "sNAPSHOT"
        source.mkdir()
        actual_id = qualify.filesystem_id(source)
        original_id = qualify.filesystem_id

        def modeled_id(path):
            return actual_id if path == alias else original_id(path)

        with mock.patch.object(qualify, "filesystem_id", side_effect=modeled_id):
            for left, right in ((source, alias / "future/deep"), (alias / "future/deep", source)):
                with self.subTest(direction=str(left)), self.assertRaises(RuntimeError):
                    qualify.require_disjoint(left, right)
        if alias.exists() and alias.samefile(source):
            for left, right in ((source, alias / "future/deep"), (alias / "future/deep", source)):
                with self.assertRaises(RuntimeError):
                    qualify.require_disjoint(left, right)
            emit("case-insensitive-filesystem", exercised=True, path=str(source))
        else:
            emit("case-insensitive-filesystem", exercised=False,
                 limitation="filesystem is case-sensitive; ancestor inode branches exercised with explicit identity fixture")

    def test_executable_identity_binds_path_bytes_modes_and_symlink(self):
        target = write_file(self.work / "tool", b"abc", 0o751)
        first = qualify.executable_identity(target)
        self.assertEqual(first, {"path": str(target), "type": "file", "link_target": None,
                                "mode": 0o751, "resolved_path": str(target), "resolved_mode": 0o751,
                                "size": 3, "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"})
        alias = self.work / "alias"
        alias.symlink_to("tool")
        linked = qualify.executable_identity(alias)
        self.assertEqual(linked["type"], "symlink")
        self.assertEqual(linked["link_target"], "tool")
        self.assertEqual(linked["path"], str(alias))
        self.assertEqual(linked["resolved_path"], str(target))
        self.assertEqual(linked["sha256"], first["sha256"])
        target.write_bytes(b"xyz")
        self.assertNotEqual(qualify.executable_identity(alias)["sha256"], linked["sha256"])
        target.chmod(0o700)
        self.assertEqual(qualify.executable_identity(alias)["resolved_mode"], 0o700)
        with self.assertRaisesRegex(RuntimeError, "not a regular file"):
            qualify.executable_identity(self.work)
        alias.unlink()
        alias.symlink_to("missing")
        with self.assertRaises(FileNotFoundError):
            qualify.executable_identity(alias)


class SnapshotGuards(FixtureCase):
    def test_identity_rejects_links_and_special_files(self):
        path = write_file(self.work / "file", b"abc", 0o640)
        self.assertEqual(snapshot.identity(path, "relative"),
                         {"path": "relative", "type": "file", "mode": 0o640, "size": 3,
                          "sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"})
        directory = self.work / "directory"
        directory.mkdir()
        directory.chmod(0o750)
        self.assertEqual(snapshot.identity(directory, "d"), {"path": "d", "type": "directory", "mode": 0o750})
        (self.work / "link").symlink_to("file")
        os.mkfifo(self.work / "fifo")
        for name in ("link", "fifo"):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "regular file or directory"):
                snapshot.identity(self.work / name, name)

    def test_selection_full_trees_empty_dirs_and_exact_corpus_scope(self):
        root = self.make_checkout()
        write_file(root / "swift/Sources/.hidden/resource.bin")
        write_file(root / "conformance/ignored.txt")
        write_file(root / "conformance/nested/ignored.json")
        write_file(root / "other/ignored.swift")
        selected = snapshot.selected(root)
        relative = [str(path.relative_to(root)) for path in selected]
        expected = ["Package.swift", "conformance", "conformance/corpus.json", "conformance/rows.tsv",
                    "examples/native-campaign/swift/Package.swift", "examples/native-campaign/swift/Sources",
                    "examples/native-campaign/swift/Sources/Consumer",
                    "examples/native-campaign/swift/Sources/Consumer/main.swift",
                    "swift/Sources", "swift/Sources/.hidden", "swift/Sources/.hidden/resource.bin",
                    "swift/Sources/Library", "swift/Sources/Library/main.swift", "swift/Sources/empty",
                    "swift/Tests", "swift/Tests/LibraryTests", "swift/Tests/LibraryTests/test.swift",
                    "validation/native-parity/n06/swift/guards.py",
                    "validation/native-parity/n06/swift/qualify.py",
                    "validation/native-parity/n06/swift/snapshot.py"]
        self.assertEqual(relative, sorted(expected))
        self.assertEqual(len(selected), len(set(selected)))

    def test_selection_failure_has_external_receipt(self):
        root = self.make_checkout()
        denied = root / "swift/Sources/Library"
        original = Path.iterdir

        def listing(path):
            if path == denied:
                raise PermissionError("guard injected enumeration failure")
            return original(path)

        receipt = self.run_snapshot(root, "enumeration-failure", fail=(PermissionError, "enumeration failure"),
                                    extra_patches=[mock.patch.object(Path, "iterdir", listing)])
        self.assertEqual(receipt["stage"], "selection")
        self.assertFalse(Path(receipt["snapshot"]).exists())

    def test_rejected_source_type_has_external_receipt(self):
        root = self.make_checkout()
        (root / "Package.swift").unlink()
        (root / "Package.swift").symlink_to("swift/Sources/Library/main.swift")
        receipt = self.run_snapshot(root, "source-link",
                                    fail=(RuntimeError, "regular file or directory"))
        self.assertEqual(receipt["stage"], "source-manifest")
        self.assertFalse(Path(receipt["snapshot"]).exists())

    def test_copy_failure_and_changed_copy_leave_failure_evidence(self):
        for case in ("copy-error", "changed-before-copy"):
            with self.subTest(case=case):
                root = self.make_checkout(self.work / case)
                original = shutil.copy2

                def copying(source, target, **kwargs):
                    if Path(source) == root / "Package.swift":
                        if case == "copy-error":
                            write_file(Path(target), b"partial copy")
                            raise OSError("guard injected partial copy failure")
                        Path(source).write_bytes(b"changed after source manifest\n")
                    return original(source, target, **kwargs)

                error = (OSError, "partial copy failure") if case == "copy-error" else (RuntimeError, "copy identity mismatch")
                receipt = self.run_snapshot(root, case, fail=error, extra_patches=[
                    mock.patch.object(snapshot, "shutil", ModuleProxy(shutil, copy2=copying))])
                self.assertEqual(receipt["stage"], "copy")
                self.assertTrue(Path(receipt["snapshot"]).is_dir())
                self.assertFalse((Path(receipt["snapshot"]) / "source-identity.json").exists())

    def test_post_copy_source_content_and_membership_drift_not_adopted(self):
        for case in ("content", "membership"):
            with self.subTest(case=case):
                root = self.make_checkout(self.work / case)
                original = shutil.copy2
                changed = []

                def copying(source, target, **kwargs):
                    result = original(source, target, **kwargs)
                    if Path(source) != root / "Package.swift" and not changed:
                        changed.append(True)
                        if case == "content":
                            (root / "Package.swift").write_bytes(b"changed after its copy\n")
                        else:
                            write_file(root / "swift/Sources/new.swift")
                    return result

                receipt = self.run_snapshot(root, "post-copy-" + case,
                    fail=(RuntimeError, "source changed during copy"), extra_patches=[
                        mock.patch.object(snapshot, "shutil", ModuleProxy(shutil, copy2=copying))])
                self.assertEqual(receipt["stage"], "source-recheck")
                self.assertFalse((Path(receipt["snapshot"]) / "source-identity.json").exists())

    def test_success_maps_and_seals_guard_and_refuses_receipt_reuse(self):
        root = self.make_checkout()
        receipt = self.run_snapshot(root, "sealed-fixture")
        destination = Path(receipt["snapshot"])
        identity_bytes = (destination / "source-identity.json").read_bytes()
        identity = json.loads(identity_bytes)
        self.assertEqual(receipt["status"], "sealed")
        self.assertEqual(receipt["source_identity_sha256"], hashlib.sha256(identity_bytes).hexdigest())
        paths = {entry["path"] for entry in identity["snapshot_entries"]}
        self.assertTrue({"guards.py", "qualify.py", "snapshot.py", "package/swift/Sources/empty",
                         "consumer/Package.swift", "consumer/Sources/Consumer/main.swift"} <= paths)
        self.assertIn("guards.py", identity["selection"]["runners"])
        self.assertEqual((destination / "guards.py").read_bytes(), (SCRIPT_DIRECTORY / "guards.py").read_bytes())
        receipt_path = root / "validation/native-parity/n06/swift/receipts/sealed-fixture-snapshot.json"
        original_receipt = receipt_path.read_bytes()
        with mock.patch.object(snapshot, "__file__", str(root / "validation/native-parity/n06/swift/snapshot.py")), \
                mock.patch.object(sys, "argv", ["snapshot.py", "sealed-fixture"]):
            with self.assertRaises(FileExistsError):
                snapshot.main()
        self.assertEqual(receipt_path.read_bytes(), original_receipt)


class QualificationReceiptGuards(FixtureCase):
    def test_missing_source_and_manifest_corruption_have_terminal_receipts(self):
        missing = self.work / "missing-source"
        result, launches = self.run_qualification(missing, "0" * 64, driver=SCRIPT_DIRECTORY / "qualify.py")
        self.assertEqual(launches, [])
        self.assertEqual(result["stage"], "preflight")
        self.assertFalse(result["immutable_inputs_unchanged"])
        source, digest = self.make_seal(self.work / "hash-case")
        result, launches = self.run_qualification(source, "0" * 64)
        self.assertIn("manifest hash mismatch", result["error"])
        self.assertEqual(launches, [])
        self.assertTrue((source.parent / "output/failure.log").is_file())

    def test_source_membership_mode_content_and_type_rejected_before_launch(self):
        for case in ("membership", "mode", "content", "type", "identity-mode"):
            with self.subTest(case=case):
                source, digest = self.make_seal(self.work / case)
                payload = source / "package/payload.swift"
                if case == "membership":
                    (source / "new-directory").mkdir()
                elif case == "mode":
                    payload.chmod(0o600)
                elif case == "content":
                    payload.write_bytes(b"modified package\n")
                elif case == "type":
                    payload.unlink()
                    payload.symlink_to("../consumer/payload.swift")
                else:
                    (source / "source-identity.json").chmod(0o600)
                result, launches = self.run_qualification(source, digest)
                self.assertEqual(launches, [])
                self.assertIn("mismatch", result["error"])

    def test_executing_driver_path_and_post_preflight_bytes_are_bound(self):
        source, digest = self.make_seal(self.work / "wrong-path")
        external = write_file(source.parent / "other-driver.py", (source / "qualify.py").read_bytes())
        result, launches = self.run_qualification(source, digest, driver=external)
        self.assertIn("not the sealed snapshot member", result["error"])
        self.assertEqual(launches, [])
        source, digest = self.make_seal(self.work / "wrong-bytes")
        original = qualify.manifest
        changed = []

        def manifest(path, **kwargs):
            if path == source / "qualify.py" and not changed:
                changed.append(True)
                path.write_bytes(path.read_bytes() + b"\n# injected driver drift\n")
            return original(path, **kwargs)

        result, launches = self.run_qualification(source, digest, extra_patches=[
            mock.patch.object(qualify, "manifest", side_effect=manifest)])
        self.assertIn("mode/content does not match", result["error"])
        self.assertEqual(launches, [])
        self.assertFalse(result["executing_driver_unchanged"])

    def test_copy_error_and_live_source_drift_use_authenticated_baseline(self):
        for case in ("partial-copy", "changed-live-source"):
            with self.subTest(case=case):
                source, digest = self.make_seal(self.work / case)
                original = shutil.copytree

                def copying(src, dst, **kwargs):
                    if Path(src) == source / "package":
                        if case == "partial-copy":
                            write_file(Path(dst) / "partial.swift")
                            raise OSError("guard injected install-copy failure")
                        (source / "package/payload.swift").write_bytes(b"changed live source\n")
                    return original(src, dst, **kwargs)

                result, launches = self.run_qualification(source, digest, extra_patches=[
                    mock.patch.object(qualify, "shutil", ModuleProxy(shutil, copytree=copying))])
                self.assertEqual(result["stage"], "install-copy")
                self.assertEqual(launches, [])
                self.assertFalse(result["installed_sources_unchanged"])
                if case == "changed-live-source":
                    self.assertIn("installed copy membership/type/mode/content mismatch", result["error"])
                    self.assertFalse(result["immutable_inputs_unchanged"])
                self.assertTrue((source.parent / "output/installed-after.json").exists())

    def test_canonical_driver_alias_is_accepted_and_late_driver_drift_fails(self):
        source, digest = self.make_seal(self.work / "canonical-alias")
        alias = source.parent / "driver-alias.py"
        alias.symlink_to(source / "qualify.py")
        result, launches = self.run_qualification(source, digest, driver=alias)
        self.assertEqual(len(launches), 1, "canonical driver alias must reach the fenced guard command")
        self.assertIn("guard launch fence", result["error"])
        self.assertTrue(result["executing_driver_unchanged"])

        source, digest = self.make_seal(self.work / "late-driver-drift")

        def drift_then_fail(*args, **kwargs):
            path = source / "qualify.py"
            path.write_bytes(path.read_bytes() + b"\n# changed after driver-before receipt\n")
            raise OSError("guard injected launch failure after driver drift")

        result, launches = self.run_qualification(source, digest, popen=drift_then_fail)
        self.assertEqual(len(launches), 1)
        self.assertFalse(result["executing_driver_unchanged"])
        self.assertFalse(result["immutable_inputs_unchanged"])
        output = source.parent / "output"
        before = json.loads((output / "driver-before.json").read_text())["identity"]
        after = json.loads((output / "driver-after.json").read_text())
        self.assertEqual(before["path"], after["path"])
        self.assertNotEqual(before["entry"]["sha256"], after["entry"]["sha256"])

    def test_preexisting_swiftpm_rejected_and_output_receipt_not_overwritten(self):
        source, _ = self.make_seal()
        (source / "package/.swiftpm").mkdir()
        identity_path = source / "source-identity.json"
        identity = {"identity_file_mode": 0o644,
                    "snapshot_entries": qualify.manifest(source, exclude_identity=True)}
        identity_path.write_text(json.dumps(identity))
        digest = hashlib.sha256(identity_path.read_bytes()).hexdigest()
        result, launches = self.run_qualification(source, digest)
        self.assertIn("includes generated bookkeeping", result["error"])
        self.assertEqual(launches, [])
        output = source.parent / "output"
        saved = (output / "result.json").read_bytes()
        with mock.patch.object(sys, "argv", ["qualify.py", str(source), str(output), digest]):
            with self.assertRaises(FileExistsError):
                qualify.main()
        self.assertEqual((output / "result.json").read_bytes(), saved)


# The leader waits until its descendant has installed TERM-ignore and owns the
# writer. Neither descendant nor live leader voluntarily closes that writer.
# SIGALRM is an independent 20-second safety bound, not passing cleanup evidence:
# the parent requires EOF within four seconds of a two-second cleanup grace.
CHILD_PROGRAM = r'''
import importlib.util, json, os, signal, sys
fd, mode, code, runner = int(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
signal.signal(signal.SIGALRM, signal.SIG_DFL)
signal.alarm(20)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
r, w = os.pipe()
child = os.fork()
if child == 0:
    os.close(r)
    signal.alarm(20)
    os.write(w, b"R")
    os.close(w)
    while True:
        signal.pause()
os.close(w)
assert os.read(r, 1) == b"R"
os.close(r)
os.write(fd, b"READY\n")
print(json.dumps({"pid": os.getpid(), "pgid": os.getpgrp(), "descendant": child, "mode": mode}), flush=True)
if mode == "exit":
    os._exit(code)
if mode == "self-signal":
    signal.signal(signal.SIGUSR1, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGUSR1)
if mode == "terminal":
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("sealed_qualify", runner)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    requests = module.InterruptionRequests()
    requests.install()
    requests.observe(signal.SIGHUP, None)
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGHUP, signal.SIGINT})
    os.kill(os.getpid(), signal.SIGTERM)
    frozen, supported = module.freeze_terminal_signals(requests)
    os.kill(os.getpid(), signal.SIGINT)
    print(json.dumps({"frozen": frozen, "supported": supported,
                      "pending_after": sorted(signal.Signals(x).name for x in signal.sigpending()),
                      "blocked": sorted(signal.Signals(x).name for x in signal.pthread_sigmask(signal.SIG_BLOCK, set()))}), flush=True)
    os._exit(0)
while True:
    signal.pause()
'''


class OwnedGroup:
    """Real children, syscall audit, retained receipts, and ownership-only rescue."""

    def __init__(self, case, directory, mode="exit", code=0, stdout=None, stderr=None):
        self.case, self.directory = case, directory
        directory.mkdir(parents=True, exist_ok=False)
        self.events = []
        self.process = None
        self.reaped = False
        self.stdout = stdout
        self.stderr = stderr
        self.mode, self.code = mode, code
        self.read_fd = None
        self.open_logs = []
        self.before_kill = None
        self.started = None

    def record(self, event, **details):
        entry = dict(event=event, **details)
        self.events.append(entry)
        with (self.directory / "process.jsonl").open("a") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        emit("owned-child", directory=str(self.directory), operation=entry)

    def start(self):
        self.read_fd, writer = os.pipe()
        argv = [sys.executable, "-B", "-E", "-s", "-c", CHILD_PROGRAM,
                str(writer), self.mode, str(self.code), str(SCRIPT_DIRECTORY / "qualify.py")]
        if self.stdout is None:
            self.stdout = (self.directory / "stdout.log").open("wb")
            self.open_logs.append(self.stdout)
        if self.stderr is None:
            self.stderr = (self.directory / "stderr.log").open("wb")
            self.open_logs.append(self.stderr)
        self.started = time.monotonic()
        try:
            self.process = REAL_POPEN(argv, stdin=subprocess.DEVNULL, stdout=self.stdout,
                                      stderr=self.stderr, pass_fds=(writer,), start_new_session=True)
        finally:
            os.close(writer)
        self.real_wait = self.process.wait
        self.real_poll = self.process.poll
        self.real_communicate = self.process.communicate
        self.process.wait = self.wait
        self.process.poll = self.forbid_reap
        self.process.communicate = self.forbid_reap
        self.record("spawn", argv=argv, pid=self.process.pid, pgid=self.process.pid, start_new_session=True)
        self.case.assertEqual(self.read_ready(), b"READY\n")
        return self.process

    def forbid_reap(self, *args, **kwargs):
        raise AssertionError("poll/communicate may release ownership before group cleanup")

    def read_ready(self):
        data = b""
        deadline = time.monotonic() + 4
        while not data.endswith(b"\n"):
            remaining = deadline - time.monotonic()
            self.case.assertGreater(remaining, 0, "owned Python readiness timed out")
            ready, _, _ = select.select([self.read_fd], [], [], remaining)
            self.case.assertTrue(ready, "owned Python readiness timed out")
            chunk = os.read(self.read_fd, 1024)
            self.case.assertTrue(chunk, "owned Python exited before readiness")
            data += chunk
        return data

    def waitid(self, idtype, pid, flags):
        self.case.assertFalse(self.reaped, "process probe after reap blocked")
        self.case.assertEqual(pid, self.process.pid)
        self.case.assertEqual(idtype, os.P_PID)
        self.case.assertTrue(flags & os.WNOWAIT, "reaping waitid blocked")
        return REAL_WAITID(idtype, pid, flags)

    def killpg(self, pgid, sig):
        self.case.assertFalse(self.reaped, "group signal after reap blocked")
        self.case.assertIsNone(self.process.returncode, "group signal after wait/poll blocked")
        self.case.assertEqual(pgid, self.process.pid, "unknown group signal blocked")
        self.case.assertIn(sig, (signal.SIGTERM, signal.SIGKILL), "group probes are forbidden")
        # A waitid with WNOWAIT can only observe our unreaped child. If ownership
        # was lost unexpectedly, refuse to signal before reaching killpg.
        REAL_WAITID(os.P_PID, self.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        self.record("signal", signal=signal.Signals(sig).name, leader_returncode=self.process.returncode)
        if self.before_kill is not None:
            self.before_kill(sig)
        return REAL_KILLPG(pgid, sig)

    def wait(self, *args, **kwargs):
        delivered = [event["signal"] for event in self.events if event["event"] == "signal"]
        self.case.assertEqual(delivered, ["SIGTERM", "SIGKILL"], "leader wait before complete group signalling blocked")
        self.record("wait", timeout=kwargs.get("timeout"))
        code = self.real_wait(*args, **kwargs)
        self.reaped = True
        self.record("reaped", returncode=code)
        return code

    def os_proxy(self):
        return ModuleProxy(os, killpg=self.killpg, waitid=self.waitid)

    def assert_eof(self):
        ready, _, _ = select.select([self.read_fd], [], [], 4)
        self.case.assertTrue(ready, "descendant still holds the pipe after cleanup")
        self.case.assertEqual(os.read(self.read_fd, 1024), b"", "unexpected descendant pipe data")
        self.case.assertLess(time.monotonic() - self.started, 12,
                             "watchdog exit cannot count as successful group cleanup")
        self.record("descendant-pipe-eof", returncode=self.process.returncode)

    def close(self):
        if self.process is not None:
            self.process.wait = self.real_wait
            self.process.poll = self.real_poll
            self.process.communicate = self.real_communicate
            if not self.reaped and self.process.returncode is None:
                try:
                    REAL_WAITID(os.P_PID, self.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                except ChildProcessError:
                    self.record("rescue-refused", reason="child ownership already lost; no group signal")
                else:
                    self.record("rescue-kill-before-reap", pgid=self.process.pid)
                    try:
                        REAL_KILLPG(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    code = self.real_wait(timeout=3)
                    self.reaped = True
                    self.record("rescue-reaped", returncode=code)
        if self.read_fd is not None:
            os.close(self.read_fd)
            self.read_fd = None
        for handle in self.open_logs:
            handle.close()


class ProcessGuards(FixtureCase):
    def test_observation_preserves_exited_leader_and_descendant_cleanup(self):
        for mode, code, expected in (("exit", 0, 0), ("exit", 23, 23),
                                     ("self-signal", 0, -signal.SIGUSR1)):
            with self.subTest(mode=mode, code=code), restored_signal_state():
                group = OwnedGroup(self, self.work / (mode + str(code)), mode, code)
                try:
                    process = group.start()
                    with mock.patch.object(qualify, "os", group.os_proxy()):
                        qualify.await_exit(process, 4, qualify.InterruptionRequests())
                        first = qualify.peek_exit(process)
                        second = qualify.peek_exit(process)
                        self.assertIsNotNone(first)
                        self.assertEqual((first.si_pid, first.si_code, first.si_status),
                                         (second.si_pid, second.si_code, second.si_status))
                        self.assertEqual(first.si_pid, process.pid)
                        self.assertIsNone(process.returncode)
                        qualify.await_exit(process, 0, qualify.InterruptionRequests())
                        cleanup = qualify.stop_owned_group(process)
                        group.record("helper-cleanup", cleanup=cleanup)
                        self.assertTrue(cleanup["leader_reaped"])
                        self.assertEqual(cleanup["signals"], ["SIGTERM", "SIGKILL"])
                        self.assertEqual(process.returncode, expected)
                        event_count = len(group.events)
                        again = qualify.stop_owned_group(process)
                        self.assertTrue(again["ownership_released"])
                        self.assertEqual(len(group.events), event_count, "released cleanup must not probe or signal")
                    group.assert_eof()
                finally:
                    group.close()

    def test_timeout_and_recorded_interruptions_leave_ownership_for_cleanup(self):
        with restored_signal_state():
            group = OwnedGroup(self, self.work / "held", "hold")
            try:
                process = group.start()
                requests = qualify.InterruptionRequests()
                with mock.patch.object(qualify, "os", group.os_proxy()):
                    self.assertIsNone(qualify.peek_exit(process))
                    with self.assertRaises(subprocess.TimeoutExpired):
                        qualify.await_exit(process, 0, requests)
                    self.assertIsNone(process.returncode)
                    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
                        self.assertIsNone(requests.observe(sig, None))
                    self.assertEqual([entry["signal"] for entry in requests.received],
                                     ["SIGTERM", "SIGHUP", "SIGTERM", "SIGINT"])
                    self.assertTrue(all(isinstance(entry["unix"], (int, float)) for entry in requests.received))
                    with self.assertRaisesRegex(qualify.QualificationInterrupted, "SIGTERM"):
                        qualify.await_exit(process, 0, requests)
                    group.before_kill = lambda sig: requests.observe(signal.SIGHUP, None)
                    cleanup = qualify.stop_owned_group(process)
                    group.record("helper-cleanup", cleanup=cleanup, interruptions=requests.received)
                    self.assertTrue(cleanup["leader_reaped"])
                    self.assertEqual(process.returncode, -signal.SIGKILL)
                    self.assertEqual(len(requests.received), 6)
                group.assert_eof()
            finally:
                group.close()

    def test_real_handlers_and_frozen_terminal_pending_set_in_owned_child(self):
        with restored_signal_state():
            group = OwnedGroup(self, self.work / "terminal", "terminal")
            try:
                process = group.start()
                with mock.patch.object(qualify, "os", group.os_proxy()):
                    qualify.await_exit(process, 4, qualify.InterruptionRequests())
                    cleanup = qualify.stop_owned_group(process)
                group.record("helper-cleanup", cleanup=cleanup)
                self.assertEqual(process.returncode, 0)
                group.assert_eof()
                lines = (group.directory / "stdout.log").read_text().splitlines()
                receipt = json.loads(lines[-1])
                self.assertTrue(receipt["supported"])
                self.assertEqual([entry["signal"] for entry in receipt["frozen"]], ["SIGHUP", "SIGTERM"])
                self.assertEqual(receipt["frozen"][-1]["source"], "pending_at_terminal_boundary")
                self.assertTrue({"SIGTERM", "SIGHUP", "SIGINT"} <= set(receipt["blocked"]))
                self.assertIn("SIGINT", receipt["pending_after"])
            finally:
                group.close()

    def test_main_command_paths_acquire_handle_then_cleanup_before_reap(self):
        for case in ("normal", "nonzero", "signal-exit", "timeout", "spawn-signals", "cleanup-signals"):
            with self.subTest(case=case):
                source, digest = self.make_seal(self.work / case)
                requests = qualify.InterruptionRequests()
                group = OwnedGroup(self, source.parent / "child",
                                   "hold" if case in ("timeout", "spawn-signals") else
                                   "self-signal" if case == "signal-exit" else "exit",
                                   23 if case == "nonzero" else 0)
                original_identity = qualify.executable_identity
                original_await = qualify.await_exit

                def identity(path):
                    if str(path) == "/usr/bin/xcrun":
                        raise RuntimeError("guard fence after first command; Swift tools never launched")
                    return original_identity(path)

                def launch(argv, **kwargs):
                    self.assertEqual(argv, [str(Path(sys.executable).absolute()), "-B", "-E", "-s",
                                            str(source / "guards.py"), str(source.parent / "output/guard-work")])
                    self.assertTrue(kwargs["start_new_session"])
                    self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
                    group.stdout, group.stderr = kwargs["stdout"], kwargs["stderr"]
                    process = group.start()
                    if case == "spawn-signals":
                        # Real handlers, sequential delivery after child ownership
                        # exists but before the Popen wrapper hands its handle back.
                        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
                            signal.raise_signal(sig)
                    return process

                def awaiting(process, timeout, interruptions):
                    return original_await(process, 0 if case == "timeout" else 4, interruptions)

                if case == "cleanup-signals":
                    group.before_kill = lambda sig: signal.raise_signal(signal.SIGHUP)
                try:
                    result, launches = self.run_qualification(source, digest, popen=launch,
                        interruptions=requests, extra_patches=[
                            mock.patch.object(qualify, "os", group.os_proxy()),
                            mock.patch.object(qualify, "executable_identity", side_effect=identity),
                            mock.patch.object(qualify, "await_exit", side_effect=awaiting)])
                    self.assertEqual(len(launches), 1)
                    commands = json.loads((source.parent / "output/commands.json").read_text())
                    self.assertEqual(len(commands), 1)
                    record = commands[0]
                    self.assertEqual(record["name"], "runner-guards")
                    self.assertTrue(record["cleanup"]["leader_reaped"])
                    self.assertEqual(record["cleanup"]["signals"], ["SIGTERM", "SIGKILL"])
                    self.assertEqual(record["pid"], group.process.pid)
                    self.assertTrue(result["immutable_inputs_unchanged"])
                    self.assertTrue(result["executing_driver_unchanged"])
                    if case == "timeout":
                        self.assertTrue(record["timed_out"])
                        self.assertEqual(group.process.returncode, -signal.SIGKILL)
                    elif case in ("spawn-signals", "cleanup-signals"):
                        self.assertEqual(result["status"], "interrupted")
                        expected = ["SIGTERM", "SIGHUP", "SIGTERM", "SIGINT"] if case == "spawn-signals" else ["SIGHUP", "SIGHUP"]
                        self.assertEqual([entry["signal"] for entry in result["interruptions"]], expected)
                    else:
                        expected = 23 if case == "nonzero" else -signal.SIGUSR1 if case == "signal-exit" else 0
                        self.assertEqual(record["returncode"], expected)
                        self.assertTrue(record["exit_observed_before_cleanup"])
                        self.assertNotIn("timed_out", record)
                    group.record("main-command-receipt", command=record, terminal=result)
                    group.assert_eof()
                finally:
                    group.close()

    def test_spawn_failure_has_receipt_and_never_signals_a_group(self):
        source, digest = self.make_seal()

        def fail_spawn(*args, **kwargs):
            raise OSError("guard injected Popen failure before handle acquisition")

        with mock.patch.object(qualify, "stop_owned_group", side_effect=AssertionError("unowned cleanup")) as cleanup:
            result, launches = self.run_qualification(source, digest, popen=fail_spawn)
        self.assertEqual(len(launches), 1)
        cleanup.assert_not_called()
        record = json.loads((source.parent / "output/commands.json").read_text())[0]
        self.assertIn("Popen failure", record["exception"])
        self.assertNotIn("pid", record)
        self.assertNotIn("cleanup", record)
        self.assertIn("Popen failure", result["error"])

    def test_preexisting_interruption_prevents_any_spawn(self):
        source, digest = self.make_seal()
        requests = qualify.InterruptionRequests()
        requests.observe(signal.SIGTERM, None)
        result, launches = self.run_qualification(source, digest, interruptions=requests)
        self.assertEqual(launches, [])
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["commands"], 0)


def main():
    global WORK_ROOT
    if len(sys.argv) != 2:
        raise SystemExit("usage: guards.py NEW_GUARD_WORK")
    if sys.platform != "darwin":
        raise SystemExit("Swift runner guards require the eventual Darwin Kyoto job")
    required = ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "fork")
    if not all(hasattr(os, name) for name in required) or not all(
            hasattr(signal, name) for name in ("pthread_sigmask", "sigpending", "raise_signal")):
        raise SystemExit("guard process ownership and terminal-signal APIs unavailable")
    WORK_ROOT = Path(sys.argv[1]).resolve()
    qualify.require_disjoint(SCRIPT_DIRECTORY, WORK_ROOT)
    WORK_ROOT.mkdir(parents=True, exist_ok=False)
    emit("guard-start", work=str(WORK_ROOT), python=sys.executable,
         sealed_scripts={name: hashlib.sha256((SCRIPT_DIRECTORY / name).read_bytes()).hexdigest()
                         for name in ("qualify.py", "snapshot.py", "guards.py")},
         process_boundary="owned Python sessions only; retained leader until final group signal; no post-reap probes")
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    receipt = {"tests_run": result.testsRun, "failures": len(result.failures),
               "errors": len(result.errors), "skipped": result.skipped,
               "passed": result.wasSuccessful(), "work": str(WORK_ROOT),
               "scope": "runner helper and controlled process guards; no Swift qualification"}
    (WORK_ROOT / "guard-result.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    emit("guard-result", **receipt)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()

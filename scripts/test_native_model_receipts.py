"""Regression controls for installed N13 callback receipts; execute via Kyoto."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import tempfile

SPEC = importlib.util.spec_from_file_location(
    "native_model_consumers", Path(__file__).with_name("check-native-model-consumers.py")
)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)

# Scenario expectations, independently literal here: dual capture and one replay.
CAPTURE = dict(initial=0, reducer=3, state=4, transition=3, encode_state=14,
               encode_input=6, encode_output=6, decode_state=0, decode_input=0, limits=0)
REPLAY = dict(initial=0, reducer=3, state=4, transition=3, encode_state=4,
              encode_input=3, encode_output=3, decode_state=1, decode_input=3, limits=4)


def receipt(engine, mode):
    return dict(engine=engine, capture_failure_controls=True,
                capture_counts=copy.deepcopy(CAPTURE) if mode == "produce" else None,
                replay_counts={} if mode == "produce" else
                {"v1": copy.deepcopy(REPLAY), "v2": copy.deepcopy(REPLAY)})


class InstalledCallbackReceipts(unittest.TestCase):
    def test_engine_build_identity_excludes_macro_crate(self):
        output = (
            "[statelessness-macros 0.2.0] cargo:rustc-env=STATELESS_BUILD_ID=source-fnv1a64:ae9df36b08c1cafc\n"
            "[statelessness 0.2.0] cargo:rustc-env=STATELESS_BUILD_ID=source-fnv1a64:8aa7d4daeb850d74\n"
        )
        self.assertEqual(DRIVER.rust_engine_build_identity(output),
                         "source-fnv1a64:8aa7d4daeb850d74")
        for invalid in (output.splitlines()[0], output + output.splitlines()[1].replace(
                "8aa7d4daeb850d74", "1111111111111111")):
            with self.assertRaises(RuntimeError):
                DRIVER.rust_engine_build_identity(invalid)

    def test_rust_identity_orders_path_components_before_sibling_extensions(self):
        # Literal source read order, independent of filesystem enumeration and
        # the oracle's sort implementation. These names expose '.' versus '/'.
        ordered = ["Cargo.toml", "build.rs",
                   "docs/requirements/controller-properties.txt",
                   "docs/requirements/controller.json",
                   "src/campaign/lifecycle/codec.rs", "src/campaign/lifecycle.rs",
                   "src/campaign.rs", "src/lib.rs"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in reversed(ordered):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(name.encode())
            reads = []
            original = Path.read_bytes
            def record_read(path):
                reads.append(path.relative_to(root).as_posix())
                return original(path)
            with mock.patch.object(Path, "read_bytes", record_read):
                DRIVER._oracle.rust_build_id(root, b"rustc fixture\n", "fixture-target")
            self.assertEqual(reads, ordered)

    def test_all_engines_and_actual_extra_replays(self):
        for engine in ("rust", "python", "go", "typescript", "swift"):
            for mode in ("produce", "transcode"):
                with self.subTest(engine=engine, mode=mode):
                    value = receipt(engine, mode)
                    DRIVER.validate_receipt(engine, mode, value)
                    value["replay_counts"] = {
                        "v1": dict(REPLAY), "v2": dict(REPLAY), "v2_causal": dict(REPLAY)
                    }
                    DRIVER.validate_receipt(engine, mode, value)

    def test_missing_or_wrong_measured_counter_fails_closed(self):
        for mode, bucket in (("produce", "capture_counts"), ("transcode", "v1"),
                             ("transcode", "v2")):
            for key in CAPTURE:
                for mutation in ("missing", "wrong", "boolean"):
                    with self.subTest(mode=mode, bucket=bucket, key=key, mutation=mutation):
                        value = receipt("typescript", mode)
                        counts = value[bucket] if mode == "produce" else value["replay_counts"][bucket]
                        if mutation == "missing":
                            del counts[key]
                        elif mutation == "wrong":
                            counts[key] += 1
                        else:
                            counts[key] = bool(counts[key])
                        with self.assertRaises(AssertionError):
                            DRIVER.validate_receipt("typescript", mode, value)

    def test_old_typescript_zero_provider_count_is_rejected(self):
        value = receipt("typescript", "transcode")
        value["replay_counts"]["v1"]["limits"] = 0
        with self.assertRaises(AssertionError):
            DRIVER.validate_receipt("typescript", "transcode", value)

    def test_missing_controls_engine_replay_or_extra_counts_are_rejected(self):
        mutations = (
            lambda r: r.pop("capture_failure_controls"),
            lambda r: r.update(capture_failure_controls=1),
            lambda r: r.update(engine="different"),
            lambda r: r.pop("replay_counts"),
            lambda r: r["replay_counts"].pop("v2"),
            lambda r: r["replay_counts"].update(unexpected=dict(REPLAY)),
            lambda r: r["replay_counts"]["v2"].update(unexpected=0),
            lambda r: r.update(capture_counts=dict(CAPTURE)),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                value = receipt("swift", "transcode")
                mutate(value)
                with self.assertRaises(AssertionError):
                    DRIVER.validate_receipt("swift", "transcode", value)



class RunnerIdentityEvidence(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.files = {name: root / name for name in ("python", "python-app.py", "node", "ts-app.js", "go", "swift", "rust")}
        for name, path in self.files.items():
            path.write_bytes(name.encode())
        self.runners = {
            "python": [str(self.files["python"]), "-I", str(self.files["python-app.py"])],
            "typescript": [str(self.files["node"]), str(self.files["ts-app.js"])],
            **{name: [str(self.files[name])] for name in ("go", "swift", "rust")},
        }

    def baseline(self, status="running"):
        report = dict(status=status, runners=self.runners)
        self.assertTrue(DRIVER.record_runner_snapshot(report, "before"))
        self.assertEqual(sum(map(len, report["runner_files_before"].values())), 7)
        return report

    def test_unchanged_executables_and_scripts(self):
        report = self.baseline()
        self.assertTrue(DRIVER.record_runner_snapshot(report, "after"))
        self.assertTrue(report["runner_snapshot_unchanged"])
        self.assertEqual(report["runner_files_before"], report["runner_files_after"])

    def test_missing_executable_and_script_retain_all_other_identities(self):
        # The same collector is used after successful commands and by the error
        # handler after a command/receipt failure; neither may lose survivors.
        for status in ("running", "failed"):
            with self.subTest(command_path=status):
                for name in ("go", "python-app.py"):
                    self.files[name].write_bytes(name.encode())
                report = self.baseline(status)
                self.files["go"].unlink(); self.files["python-app.py"].unlink()
                self.assertFalse(DRIVER.record_runner_snapshot(report, "after"))
                self.assertFalse(report["runner_snapshot_unchanged"])
                after = report["runner_files_after"]
                self.assertEqual(sum(map(len, after.values())), 7)
                self.assertIn("identity_error", after["go"][0])
                self.assertIn("identity_error", after["python"][1])
                for engine in ("typescript", "swift", "rust"):
                    self.assertEqual(after[engine], report["runner_files_before"][engine])
                self.assertEqual(report["status"], status)

    def test_unreadable_runner_retains_survivors_in_both_completion_paths(self):
        original_sha = DRIVER.sha
        def unreadable(path):
            if path == self.files["go"].resolve():
                raise PermissionError("controlled unreadable runner")
            return original_sha(path)
        for status in ("running", "failed"):
            with self.subTest(command_path=status):
                report = self.baseline(status)
                with mock.patch.object(DRIVER, "sha", side_effect=unreadable):
                    self.assertFalse(DRIVER.record_runner_snapshot(report, "after"))
                after = report["runner_files_after"]
                self.assertFalse(report["runner_snapshot_unchanged"])
                self.assertIn("PermissionError", after["go"][0]["identity_error"])
                for engine in ("python", "typescript", "swift", "rust"):
                    self.assertEqual(after[engine], report["runner_files_before"][engine])
                self.assertEqual(report["status"], status)

    def test_before_failure_and_content_drift_fail_identity_verification(self):
        self.files["go"].unlink(); self.files["python-app.py"].unlink()
        report = dict(runners=self.runners)
        self.assertFalse(DRIVER.record_runner_snapshot(report, "before"))
        self.assertIn("identity_error", report["runner_files_before"]["go"][0])
        self.assertIn("identity_error", report["runner_files_before"]["python"][1])
        self.files["go"].write_bytes(b"original")
        self.files["python-app.py"].write_bytes(b"application")
        report = self.baseline()
        self.files["go"].write_bytes(b"changed")
        self.assertTrue(DRIVER.record_runner_snapshot(report, "after"))
        self.assertFalse(report["runner_snapshot_unchanged"])


if __name__ == "__main__":
    unittest.main()

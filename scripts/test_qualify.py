"""Failure-sensitive tests for qualification reports; no Rust commands execute."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import qualify
from qualify import Stage, run_stage, source_hashes, summarize


class ReportingTests(unittest.TestCase):
    def test_compilation_cannot_be_runtime_qualification(self):
        result = summarize([{"status": "compiled"}, {"status": "not_run"}], True, False)
        self.assertTrue(result["selected_checks_passed"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "incomplete")

    def test_missing_or_changed_evidence_cannot_pass(self):
        for status in ("failed", "setup_error", "timed_out", "interrupted", "blocked", "not_run", "compiled"):
            self.assertFalse(summarize([{"status": status}], True, True)["passed"])
        self.assertFalse(summarize([{"status": "passed"}], False, True)["passed"])
        self.assertFalse(summarize([], True, True)["passed"])
        self.assertFalse(summarize([{"status": "passed"}], True, False)["passed"])
        self.assertTrue(summarize([{"status": "passed"}], True, True)["passed"])

    def test_compile_only_does_not_invoke_runtime_wrappers(self):
        with tempfile.TemporaryDirectory() as directory, patch("qualify.direct") as direct, patch("qualify.queued") as queued:
            result = run_stage(Stage("nested-tests", [sys.executable, "scripts/check-debugger.py"]), Path(directory), compile_only=True, kyoto=Path("kyoto.py"))
            self.assertEqual(result["status"], "not_run")
            direct.assert_not_called()
            queued.assert_not_called()

    def test_expected_rejection_needs_exit_and_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for code, text, expected in ((3, "Diverged", "passed"), (0, "Diverged", "failed"), (3, "unrelated failure", "failed")):
                stage = Stage("rejection", [sys.executable, "-c", f"print({text!r}); raise SystemExit({code})"], expected_exit=3, expected_text="Diverged")
                result = run_stage(stage, work)
                self.assertEqual(result["status"], expected)
                self.assertTrue(result["log_sha256"])

    def test_setup_and_deadline_have_distinct_statuses(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = run_stage(Stage("missing", [str(Path(directory) / "no-program")]), Path(directory))
            self.assertEqual(missing["status"], "setup_error")
            self.assertFalse(missing["runtime_executed"])
            timeout = run_stage(Stage("timeout", [sys.executable, "-c", "import time; time.sleep(5)"]), Path(directory), timeout=0.05)
            self.assertEqual(timeout["status"], "timed_out")
            self.assertTrue(timeout["runtime_executed"])

    def test_policy_rejection_never_falls_back_to_direct_execution(self):
        rejected = subprocess.CompletedProcess([], 2, "", "policy denies test execution")
        with tempfile.TemporaryDirectory() as directory, patch("qualify.subprocess.run", return_value=rejected), patch("qualify.direct") as direct:
            result = qualify.queued(["cargo", "test", "--no-run"], Path(directory) / "log", 10, {}, Path("kyoto.py"), "fixture")
            self.assertEqual(result["status"], "setup_error")
            self.assertIn("policy denies", result["error"])
            direct.assert_not_called()

    def test_compile_plan_contains_no_rust_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = qualify.stages("full", Path(directory), portable=True)
            for stage in plan:
                if stage.compile_command:
                    self.assertIn("--no-run", stage.compile_command)
                elif stage.kind == "build":
                    self.assertEqual(stage.command[0], "cargo")
                    self.assertIn(stage.command[1], ("build", "fmt", "clippy", "doc"))
                elif stage.kind == "tooling":
                    self.assertNotIn("check-debugger.py", " ".join(stage.command))
            full = {s.name for s in plan}
            self.assertTrue({"starter-reject-changed-model", "debugger-full", "macro-jobs-replay", "counter-example"} <= full)

    def test_queue_terminal_status_must_confirm_process_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source_log = work / "queue.log"
            source_log.write_text("compiler output")
            stage = Stage("tests", ["cargo", "test"], compile_command=["cargo", "test", "--no-run"])
            for status, code, expected in (
                ("succeeded", 0, "compiled"), ("failed", 101, "failed"),
                ("cancelled", 0, "setup_error"), ("cancelled", 101, "setup_error"),
                ("failed", 0, "setup_error"), ("succeeded", None, "setup_error"),
                ("rejected", 0, "setup_error"),
            ):
                with self.subTest(status=status, exit_code=code):
                    job = {"id": "job", "status": status, "exit_code": code, "log": str(source_log)}
                    response = subprocess.CompletedProcess([], 0, json.dumps(job), "")
                    with patch("qualify.subprocess.run", return_value=response):
                        result = run_stage(stage, work, compile_only=True, kyoto=Path("kyoto.py"), owner="fixture")
                    self.assertEqual(result["status"], expected)
                    self.assertEqual(result["kyoto_status"], status)
                    self.assertFalse(result["runtime_executed"])

    def test_corpus_clippy_is_required_without_portable_conformance(self):
        for profile in ("pr", "full"):
            for portable in (False, True):
                with self.subTest(profile=profile, portable=portable):
                    plan = qualify.stages(profile, Path("/tmp/qualification"), portable=portable)
                    names = [stage.name for stage in plan]
                    self.assertEqual(names.count("corpus-clippy"), 1)
                    self.assertEqual(names.index("corpus-clippy"), names.index("clippy") + 1)
                    stage = plan[names.index("corpus-clippy")]
                    self.assertEqual(stage.kind, "build")
                    self.assertEqual(stage.command, ["cargo", "clippy", "--locked", "--manifest-path", "validation/corpus/Cargo.toml", "--", "-D", "warnings"])
                    self.assertIsNone(stage.compile_command)
                    self.assertEqual(stage.expected_exit, 0)
                    self.assertIn("--offline", plan[names.index("clippy")].command)

    def test_corpus_clippy_failure_prevents_pr_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "report.json"
            plan = [stage for stage in qualify.stages("pr", Path(directory))
                    if stage.name in {"clippy", "corpus-clippy"}]
            results = [{"name": "clippy", "status": "passed"},
                       {"name": "corpus-clippy", "status": "failed", "exit_code": 101}]
            with patch("qualify.stages", return_value=plan), patch("qualify.tool_version", return_value={}), patch("qualify.source_hashes", return_value={}), patch("qualify.git_context", return_value={"available": False}), patch("qualify.run_stage", side_effect=results) as run:
                self.assertEqual(qualify.main(["--profile", "pr", "--evidence", str(evidence)]), 1)
            self.assertEqual([call.args[0].name for call in run.call_args_list], ["clippy", "corpus-clippy"])
            report = json.loads(evidence.read_text())
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["qualification_complete"])
            self.assertEqual(report["results"], results)

    def test_reference_mutations_replay_schema_and_review_are_required(self):
        plan = {stage.name: stage for stage in qualify.stages("pr", Path("/tmp/reference-qualification"))}
        self.assertTrue({"reference-requirements", "requirements-change-review", "versioned-schemas", "native-python-candidate", "conformance-tooling", "reference-assurance", "reference-fault-controls", "reference-fresh-replay", "reference-live-replay"} <= plan.keys())
        self.assertIn("reference-fault-controls", plan["reference-fresh-replay"].dependencies)
        self.assertIn("reference-assurance", plan["reference-assurance-schema"].dependencies)
        self.assertEqual(plan["reference-fresh-replay"].kind, "runtime")
        with tempfile.TemporaryDirectory() as directory, patch("qualify.direct") as direct, patch("qualify.queued") as queued:
            for name in ("reference-assurance", "reference-fault-controls", "reference-fresh-replay", "reference-live-replay"):
                result = run_stage(plan[name], Path(directory), compile_only=True, kyoto=Path("kyoto.py"))
                self.assertEqual(result["status"], "not_run")
            direct.assert_not_called()
            queued.assert_not_called()

    def test_queue_deadline_cancels_with_matching_owner(self):
        submitted = subprocess.CompletedProcess([], 0, json.dumps({"id": "job", "status": "queued"}), "")
        cancelled = subprocess.CompletedProcess([], 0, "{}", "")
        with tempfile.TemporaryDirectory() as directory, patch("qualify.subprocess.run", side_effect=[submitted, cancelled]) as run, patch("qualify.time.monotonic", side_effect=[0, 11]):
            result = qualify.queued(["cargo", "test", "--no-run"], Path(directory) / "log", 10, {}, Path("kyoto.py"), "owner")
            self.assertEqual(result["status"], "timed_out")
            self.assertTrue(result["cancellation_requested"])
            self.assertEqual(run.call_args.args[0][-4:], ["cancel", "job", "--user-id", "owner"])

    def test_queue_interrupt_retains_cancellation_error(self):
        submitted = subprocess.CompletedProcess([], 0, json.dumps({"id": "job", "status": "queued"}), "")
        rejected = subprocess.CompletedProcess([], 1, "", "cancellation unavailable")
        with tempfile.TemporaryDirectory() as directory, patch("qualify.subprocess.run", side_effect=[submitted, rejected]), patch("qualify.time.sleep", side_effect=KeyboardInterrupt):
            result = qualify.queued(["cargo", "test", "--no-run"], Path(directory) / "log", 10, {}, Path("kyoto.py"), "owner")
            self.assertEqual(result["status"], "interrupted")
            self.assertFalse(result["cancellation_requested"])
            self.assertEqual(result["cancellation_error"], "cancellation unavailable")

    def test_relative_cargo_target_belongs_to_source_root(self):
        with patch.dict("os.environ", {"CARGO_TARGET_DIR": "target/custom"}):
            plan = qualify.stages("pr", Path("/tmp/qualification"))
            app = next(s for s in plan if s.name == "starter-check")
            self.assertTrue(Path(app.command[0]).is_relative_to(qualify.ROOT / "target/custom"))

    def test_explicit_toolchain_wraps_commands_and_compile_replacement(self):
        stage = qualify.with_toolchain(Stage("test", ["cargo", "test"], compile_command=["cargo", "test", "--no-run"]), "1.90.0")
        self.assertEqual(stage.command, ["rustup", "run", "1.90.0", "cargo", "test"])
        self.assertEqual(stage.compile_command, ["rustup", "run", "1.90.0", "cargo", "test", "--no-run"])
        nested = qualify.with_toolchain(Stage("nested", ["python", "check.py"]), "1.90.0")
        self.assertEqual(nested.command, ["rustup", "run", "1.90.0", "python", "check.py"])
        compare = qualify.with_toolchain(Stage("parity", [], "compare"), "1.90.0")
        self.assertEqual(compare.command, [])

    def test_selected_toolchain_pins_nested_compiler_and_path(self):
        with tempfile.TemporaryDirectory() as directory:
            binaries = Path(directory).resolve() / "selected/bin"
            binaries.mkdir(parents=True)
            responses = []
            for name in ("cargo", "rustc", "rustdoc"):
                (binaries / name).touch()
                responses.append(subprocess.CompletedProcess([], 0, str(binaries / name) + "\n", ""))
            with patch("qualify.subprocess.run", side_effect=responses), patch.dict("os.environ", {"PATH": "/standalone/bin"}):
                env = qualify.toolchain_environment("1.90.0")
            self.assertEqual(env["RUSTC"], str(binaries / "rustc"))
            self.assertEqual(env["RUSTDOC"], str(binaries / "rustdoc"))
            self.assertTrue(env["PATH"].startswith(str(binaries)))

    def test_source_identity_tracks_new_deleted_and_changed_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            model = root / "src/model.rs"
            model.write_text("baseline")
            before = source_hashes(root)
            (root / "target").mkdir()
            (root / "target/report.json").write_text("generated")
            (root / "src/__pycache__").mkdir()
            (root / "src/__pycache__/generated.pyc").write_text("generated")
            self.assertEqual(before, source_hashes(root))
            model.write_text("changed")
            self.assertNotEqual(before, source_hashes(root))
            model.unlink()
            self.assertEqual({}, source_hashes(root))
            (root / ".cargo").mkdir()
            (root / ".cargo/config.toml").write_text("[build]\njobs=1\n")
            (root / "validation/releases").mkdir(parents=True)
            (root / "validation/releases/registry.json").write_text("{}")
            (root / "rust-toolchain").write_text("1.90.0")
            self.assertEqual(set(source_hashes(root)), {".cargo/config.toml", "validation/releases/registry.json", "rust-toolchain"})

    def test_live_cli_writes_partial_evidence_after_failed_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence.json"
            plan = [Stage("success", [sys.executable, "-c", "print('ok')"], "tooling"),
                    Stage("failure", [sys.executable, "-c", "raise SystemExit(7)"], "tooling"),
                    Stage("dependent", [sys.executable, "-c", "raise AssertionError('must not run')"], dependencies=("failure",))]
            with patch("qualify.stages", return_value=plan), patch("qualify.tool_version", return_value={"output": "fixture"}), patch("qualify.source_hashes", return_value={}), patch("qualify.git_context", return_value={"available": False}):
                self.assertEqual(qualify.main(["--evidence", str(evidence)]), 1)
            report = json.loads(evidence.read_text())
            self.assertEqual([s["status"] for s in report["results"]], ["passed", "failed", "blocked"])
            self.assertFalse(report["passed"])
            self.assertTrue((root / report["artifacts"] / report["results"][0]["log"]).exists())

    def test_runtime_queue_mode_is_rejected_before_any_command(self):
        with patch("qualify.tool_version") as version, patch("qualify.queued") as queued:
            with self.assertRaises(SystemExit) as error:
                qualify.main(["--kyoto", "kyoto.py"])
            self.assertEqual(error.exception.code, 2)
            version.assert_not_called()
            queued.assert_not_called()

    def test_runtime_skipped_stage_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as directory, patch("qualify.stages", return_value=[Stage("skipped", ["fixture"])]), patch("qualify.tool_version", return_value={}), patch("qualify.source_hashes", return_value={}), patch("qualify.run_stage", return_value={"name": "skipped", "status": "not_run"}):
            self.assertEqual(qualify.main(["--evidence", str(Path(directory) / "report.json")]), 1)


if __name__ == "__main__":
    unittest.main()

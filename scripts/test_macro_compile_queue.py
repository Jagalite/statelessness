"""Compile-only macro consumer queue behavior; no Cargo or Rust execution."""
import ast
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import os
import shutil
import tempfile
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("check-macros.py")


def functions(optimize=0):
    tree = ast.parse(SCRIPT.read_text())
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {"queued", "run", "write_evidence", "main"}]
    namespace = {"args": SimpleNamespace(kyoto=Path("/fixture/kyoto.py"), compile_only=True, timeout=10, evidence=None),
                 "subprocess": subprocess, "sys": sys, "time": time, "json": json, "uuid": uuid, "Path": Path,
                 "results": [], "cleanup_safe": True, "log_directory": None, "command_failure": None,
                 "CANCEL_WAIT_SECONDS": 30, "hashlib": hashlib, "datetime": datetime, "timezone": timezone,
                 "os": os, "shutil": shutil, "tempfile": tempfile, "archive_hashes": {},
                 "root": Path("/fixture/project"), "initial_hashes": {"src/lib.rs": "one"},
                 "source_hashes": lambda: {"src/lib.rs": "one"}, "compiler_identity": None,
                 "progress_writer": None,
                 "git_context": lambda _: {"commit": None, "available": False}}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SCRIPT), "exec", optimize=optimize), namespace)
    return namespace


def response(value):
    return subprocess.CompletedProcess([], 0, json.dumps(value), "")


def command_result(code=0, output="compiled", status="finished", cleanup_safe=True):
    return {"exit": code, "output": output, "status": status, "cleanup_safe": cleanup_safe, "kyoto_job": "owned-job"}


class MacroCompileQueueTests(unittest.TestCase):
    def test_lost_submission_response_recovers_only_unique_owner(self):
        namespace = functions()
        timeout = subprocess.TimeoutExpired(["kyoto", "submit"], 30)
        owned = subprocess.CompletedProcess([], 0, json.dumps([{"id": "owned-job", "status": "queued"}]), "")
        cancelled = response({"id": "owned-job", "status": "running"})
        waiting = response({"id": "owned-job", "status": "running"})
        terminal = response({"id": "owned-job", "status": "cancelled"})
        with patch.object(subprocess, "run", side_effect=[timeout, owned, cancelled, waiting, terminal]) as invoked, patch.object(time, "sleep"):
            receipt = namespace["queued"](["cargo", "check"], Path("/fixture/project"), {})
        self.assertTrue(receipt["cleanup_safe"])
        self.assertEqual(receipt["status"], "setup_error")
        self.assertEqual(receipt["cancellation_terminal"], {"owned-job": "cancelled"})
        commands = [call.args[0] for call in invoked.call_args_list]
        owner = commands[0][commands[0].index("--user-id") + 1]
        self.assertEqual(commands[1][-3:], ["jobs", "--user-id", owner])
        self.assertEqual(commands[2][-4:], ["cancel", "owned-job", "--user-id", owner])
        self.assertEqual(commands[3][-2:], ["show", "owned-job"])
        self.assertEqual(commands[4][-2:], ["show", "owned-job"])

    def test_runtime_positive_fixture_becomes_visible_compile_command(self):
        namespace = functions()
        calls = []
        def queued(*args):
            calls.append(args)
            return command_result(output="compile-only fixture")
        namespace["queued"] = queued
        namespace["run"](["cargo", "run", "--offline", "--quiet"], Path("/consumer"), {})
        self.assertEqual(calls[0][0], ["cargo", "check", "--offline", "--quiet"])
        self.assertFalse(namespace["results"][0]["runtime_executed"])

    def test_opaque_or_verifying_package_commands_are_refused(self):
        namespace = functions()
        for command in (["sh", "tests.sh"], ["cargo", "test"], ["cargo", "package"]):
            with self.assertRaises(RuntimeError):
                namespace["run"](command, Path("/consumer"), {})

    def test_qualification_gates_survive_python_optimization(self):
        # Python -O removes ast.Assert nodes; compile the real functions at that
        # optimization level and exercise positive and negative command gates.
        namespace = functions(optimize=2)
        for code, output, diagnostic in ((1, "compile error", None),
                                         (0, "SM107", "SM107"),
                                         (1, "wrong diagnostic", "SM107")):
            namespace["queued"] = lambda *_: command_result(code, output)
            with self.subTest(code=code, output=output), self.assertRaises(RuntimeError):
                namespace["run"](["cargo", "check"], Path("/consumer"), {}, diagnostic)
            self.assertEqual(namespace["results"][-1]["status"], "failed")
        tree = ast.parse(SCRIPT.read_text())
        self.assertFalse(any(isinstance(node, ast.Assert) for node in ast.walk(tree)),
                         "qualification, packaging, and source gates cannot use Python assert")

    def test_expected_compile_error_is_a_valid_completed_fixture(self):
        namespace = functions(optimize=2)
        namespace["queued"] = lambda *_: command_result(101, "error: SM107 duplicate tag")
        namespace["run"](["cargo", "check"], Path("/consumer"), {}, "SM107")
        self.assertEqual(namespace["results"][0]["status"], "passed")

    def test_direct_runtime_claim_requires_confirmed_process_start(self):
        for outcome, started in ((OSError("launch unavailable"), False),
                                 (subprocess.CompletedProcess([], 0, "done", ""), True)):
            namespace = functions()
            namespace["args"].kyoto = None
            namespace["args"].compile_only = False
            with patch.object(subprocess, "run", side_effect=[outcome]):
                if started:
                    namespace["run"](["cargo", "run"], Path("/consumer"), {})
                else:
                    with self.assertRaises(RuntimeError):
                        namespace["run"](["cargo", "run"], Path("/consumer"), {})
            self.assertEqual(namespace["results"][0]["runtime_executed"], started)

    def test_cancelled_zero_exit_never_satisfies_positive_or_negative_fixture(self):
        for expected in (None, "SM107"):
            namespace = functions()
            terminal = {"id": "owned-job", "status": "cancelled", "exit_code": 0}
            with patch.object(subprocess, "run", return_value=response(terminal)):
                with self.assertRaises(RuntimeError):
                    namespace["run"](["cargo", "check"], Path("/consumer"), {}, expected)
            self.assertEqual(namespace["results"][0]["status"], "setup_error")
            self.assertTrue(namespace["cleanup_safe"])

    def test_unconfirmed_async_cancellation_keeps_cleanup_unsafe(self):
        namespace = functions()
        namespace["CANCEL_WAIT_SECONDS"] = 0
        responses = [subprocess.TimeoutExpired(["submit"], 30),
                     response([{"id": "owned-job", "status": "running"}]),
                     response({"id": "owned-job", "status": "running"}),
                     response({"id": "owned-job", "status": "running"})]
        with patch.object(subprocess, "run", side_effect=responses):
            receipt = namespace["queued"](["cargo", "check"], Path("/consumer"), {})
        self.assertFalse(receipt["cleanup_safe"])
        self.assertEqual(receipt["cancellation_terminal"], {})
        self.assertIn("unconfirmed", receipt["cancellation_errors"][0])

    def test_missing_owner_or_show_failure_preserves_uncertainty(self):
        for after_submit in ([response([])],
                             [response([{"id": "owned-job", "status": "running"}]),
                              response({}), OSError("show unavailable")]):
            namespace = functions()
            with patch.object(subprocess, "run", side_effect=[KeyboardInterrupt(), *after_submit]):
                receipt = namespace["queued"](["cargo", "check"], Path("/consumer"), {})
            self.assertEqual(receipt["status"], "interrupted")
            self.assertFalse(receipt["cleanup_safe"])
            self.assertTrue(receipt["cancellation_errors"])

    def test_failed_run_retains_partial_results_logs_and_temporary_inputs(self):
        namespace = functions()
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            scratch = parent / "scratch"
            scratch.mkdir()
            destination = parent / "evidence.json"
            namespace["args"].evidence = destination
            outcomes = iter([command_result(), command_result(None, "partial compiler output", "interrupted", False)])
            namespace["queued"] = lambda *_: next(outcomes)
            def fixtures(temp, env):
                (temp / "input.rs").write_text("retained input")
                namespace["run"](["cargo", "check"], temp, env)
                partial = json.loads(destination.read_text())
                self.assertEqual(partial["status"], "running")
                self.assertEqual(len(partial["results"]), 1)
                namespace["run"](["cargo", "check"], temp, env)
            namespace["fixtures"] = fixtures
            compiler = subprocess.CompletedProcess([], 0, "rustc fixture", "")
            with patch.object(tempfile, "mkdtemp", return_value=str(scratch)), patch.object(subprocess, "run", return_value=compiler), patch.object(shutil, "rmtree") as cleanup, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(namespace["main"](), 1)
            cleanup.assert_not_called()
            report = json.loads(destination.read_text())
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["runtime_qualification_complete"])
            self.assertFalse(report["compile_qualification_complete"])
            self.assertTrue(report["temporary_directory_preserved"])
            self.assertFalse(report["cleanup_safe"])
            self.assertEqual([item["status"] for item in report["results"]], ["passed", "interrupted"])
            self.assertEqual((scratch / "input.rs").read_text(), "retained input")
            log = Path(report["results"][1]["log"])
            self.assertEqual(log.read_text(), "partial compiler output")
            self.assertEqual(hashlib.sha256(log.read_bytes()).hexdigest(), report["results"][1]["log_sha256"])

    def test_source_change_never_yields_complete_receipt_under_optimization(self):
        namespace = functions(optimize=2)
        namespace["source_hashes"] = lambda: {"src/lib.rs": "changed"}
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "receipt.json"
            report = namespace["write_evidence"](destination, Path(directory), {}, "compiled_only")
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["source_unchanged"])
            self.assertFalse(report["compile_qualification_complete"])
            self.assertFalse(report["runtime_qualification_complete"])

    def test_success_cleanup_keeps_durable_logs_and_compile_only_receipt(self):
        namespace = functions()
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            scratch = parent / "scratch"
            scratch.mkdir()
            destination = parent / "evidence.json"
            namespace["args"].evidence = destination
            namespace["queued"] = lambda *_: command_result()
            namespace["fixtures"] = lambda temp, env: namespace["run"](["cargo", "check"], temp, env)
            compiler = subprocess.CompletedProcess([], 0, "rustc fixture", "")
            with patch.object(tempfile, "mkdtemp", return_value=str(scratch)), patch.object(subprocess, "run", return_value=compiler), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(namespace["main"](), 0)
            self.assertFalse(scratch.exists())
            report = json.loads(destination.read_text())
            self.assertEqual(report["status"], "compiled_only")
            self.assertTrue(report["compile_qualification_complete"])
            self.assertFalse(report["runtime_qualification_complete"])
            self.assertEqual(Path(report["results"][0]["log"]).read_text(), "compiled")


if __name__ == "__main__":
    unittest.main()

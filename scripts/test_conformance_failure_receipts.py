"""Lossless baseline failure controls. Run through Kyoto, not during source review."""
from contextlib import ExitStack, redirect_stdout
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest import mock
import zlib


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("baseline_failure_receipts", ROOT / "conformance/run.py")
RUN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN)


def command(stdout=b"{}\n", stderr=b"", exit_code=0, *, sleep=False):
    # Real short-lived subprocesses exercise byte capture and observed exits.
    encoded_stdout = base64.b64encode(zlib.compress(stdout))
    encoded_stderr = base64.b64encode(zlib.compress(stderr))
    # Compact transport keeps long binary fixtures below Windows argv limits.
    script = ("import sys,time,base64,zlib\n"
              "sys.stdin.buffer.read()\n"
              f"sys.stdout.buffer.write(zlib.decompress(base64.b64decode({encoded_stdout!r}))); sys.stdout.buffer.flush()\n"
              f"sys.stderr.buffer.write(zlib.decompress(base64.b64decode({encoded_stderr!r}))); sys.stderr.buffer.flush()\n"
              + ("time.sleep(60)\n" if sleep else "")
              + f"raise SystemExit({exit_code})\n")
    return [sys.executable, "-I", "-c", script]


def lines(values):
    return b"".join(json.dumps(value).encode() + b"\n" for value in values)


class ConformanceFailureReceipts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def entries(self, directory):
        return [json.loads(line) for line in (directory / "invocations.jsonl").read_text().splitlines()]

    def assert_stream(self, directory, entry, name, expected):
        reference = entry["streams"][name]
        self.assertEqual(reference["bytes"], len(expected))
        self.assertEqual(reference["sha256"], hashlib.sha256(expected).hexdigest())
        self.assertEqual((directory / reference["path"]).read_bytes(), expected)

    def failed_invocation(self, name, stdout, stderr, *, exit_code=0, sleep=False, timeout=5):
        receipts = RUN.InvocationReceipts(self.directory / name)
        requests = [b'\x00\xff raw', {"unicode": "\U0001f4be"}]
        expected_input = b'\x00\xff raw\n{"unicode": "\\ud83d\\udcbe"}\n'
        with self.assertRaises(RUN.InvocationError) as caught:
            RUN.invoke(command(stdout, stderr, exit_code, sleep=sleep), requests, timeout,
                       receipts=receipts, context={"phase": "failure-control"})
        entry, = self.entries(receipts.directory)
        self.assertEqual(caught.exception.receipt["id"], entry["id"])
        self.assertEqual(entry["context"], {"phase": "failure-control"})
        self.assert_stream(receipts.directory, entry, "input", expected_input)
        self.assert_stream(receipts.directory, entry, "stdout", stdout)
        self.assert_stream(receipts.directory, entry, "stderr", stderr)
        return entry

    def test_nonzero_preserves_long_non_utf8_streams(self):
        entry = self.failed_invocation("nonzero", b"partial\x00\xff\n" * 2000,
                                       bytes(range(256)) * 100, exit_code=23)
        self.assertEqual(entry["status"], "nonzero_exit")
        self.assertEqual(entry["returncode"], 23)
        self.assertFalse(entry["timed_out"])
        self.assertIsNone(entry["termination_requested"])

    def test_malformed_responses_preserve_complete_streams(self):
        for index, output in enumerate((b'{"valid":1}\n{bad\ntrailer\x00\n',
                                        b'\xff\n', b'{"a":1,"a":2}\n', b'{"a":1.0}\n')):
            with self.subTest(output=output):
                entry = self.failed_invocation(f"malformed-{index}", output, b"diagnostic\xfe" * 2000)
                self.assertEqual(entry["status"], "malformed_response")
                self.assertEqual(entry["returncode"], 0)

    def test_timeout_preserves_exact_partial_streams_and_reaped_exit(self):
        entry = self.failed_invocation("timeout", b"partial response\xff\x00",
                                       b"partial diagnostics\xfe\x00", sleep=True, timeout=2)
        self.assertEqual(entry["status"], "timeout")
        self.assertTrue(entry["timed_out"])
        self.assertEqual(entry["timeout_seconds"], 2)
        self.assertEqual(entry["termination_requested"], "kill")
        self.assertIsNotNone(entry["returncode"])
        self.assertNotEqual(entry["returncode"], 0)
        if os.name == "posix":
            self.assertEqual(entry["signal"], 9)

    def test_count_and_size_rejections_retain_bytes(self):
        missing = self.failed_invocation("missing", b"{}\n", b"count\xff")
        self.assertEqual(missing["status"], "response_count")
        with mock.patch.object(RUN, "MAX_OUTPUT", 32):
            oversized = self.failed_invocation("oversized", b"x" * 10000, b"size\xff")
        self.assertEqual(oversized["status"], "output_limit")
        self.assertEqual(oversized["returncode"], 0)

    def test_launch_failure_retains_input_and_empty_streams(self):
        receipts = RUN.InvocationReceipts(self.directory / "launch")
        with self.assertRaises(RUN.InvocationError):
            RUN.invoke([str(self.directory / "missing-executable")], [b"raw\xff"], 5, receipts=receipts)
        entry, = self.entries(receipts.directory)
        self.assertEqual(entry["status"], "spawn_error")
        self.assertFalse(entry["process_started"])
        self.assertFalse(entry["input_submitted"])
        self.assertIsNone(entry["pid"])
        self.assertIsNone(entry["returncode"])
        self.assert_stream(receipts.directory, entry, "input", b"raw\xff\n")
        self.assert_stream(receipts.directory, entry, "stdout", b"")
        self.assert_stream(receipts.directory, entry, "stderr", b"")

    def test_successes_share_blobs_but_keep_distinct_invocations(self):
        receipts = RUN.InvocationReceipts(self.directory / "reuse")
        for _ in range(3):
            self.assertEqual(RUN.invoke(command(), [b"{}"], 5, receipts=receipts), [{}])
        entries = self.entries(receipts.directory)
        self.assertEqual([entry["id"] for entry in entries], [1, 2, 3])
        self.assertTrue(all(entry["status"] == "passed" for entry in entries))
        self.assertEqual(len(list(receipts.blobs.iterdir())), 2)  # {} LF and empty stderr
        self.assertEqual({path.name for path in receipts.directory.iterdir()}, {"blobs", "invocations.jsonl"})
        self.assertEqual(receipts.summary()["journal_sha256"], hashlib.sha256(receipts.journal.read_bytes()).hexdigest())
        for entry in entries:
            self.assert_stream(receipts.directory, entry, "input", b"{}\n")
            self.assert_stream(receipts.directory, entry, "stdout", b"{}\n")
            self.assert_stream(receipts.directory, entry, "stderr", b"")
        before = receipts.journal.read_bytes()
        with self.assertRaises(FileExistsError):
            RUN.InvocationReceipts(receipts.directory)
        self.assertEqual(receipts.journal.read_bytes(), before)

    def aggregate_fixture(self, failure_phase, failure_mode="malformed"):
        corpus = self.directory / "fixture.json"
        trace = {"steps": [], "termination": "completed"}
        corpus.write_text(json.dumps({"corpus_version": 1, "spec_version": "1.0.0", "profiles": [],
                                     "cases": [{"id": "record-one", "request": {"operation": "record", "model": {}},
                                                "expected": {"trace": trace}}]}))
        report = self.directory / f"{failure_phase}-{failure_mode}.json"
        artifact_dir = self.directory / f"{failure_phase}-{failure_mode}-receipts"
        args = ["--corpus", str(corpus), "--report", str(report), "--artifact-dir", str(artifact_dir),
                "--runner", '["fixture-a"]', "--runner", '["fixture-b"]', "--timeout", "2"]
        real_invoke = RUN.invoke
        failed_output, failed_stderr = b'{"partial":true}\n{broken\xff\n', b'error\xfe' * 2000

        def route(argv, requests, timeout, **kwargs):
            context = kwargs["context"]
            phase, index = context["phase"], context["runner_index"]
            hello = dict(protocol_version=1, spec_version="1.0.0", profiles=[], implementation=argv[0])
            if phase == failure_phase and index == 1:
                return real_invoke(command(failed_output, failed_stderr,
                                           19 if failure_mode == "nonzero" else 0,
                                           sleep=failure_mode == "timeout"), requests, timeout, **kwargs)
            if phase == "hello":
                responses = [hello]
            elif phase == "cases":
                response = {"trace": trace}
                if failure_phase == "transport" and index == 1:
                    response["unexpected"] = True  # Must survive the later process failure.
                responses = [response]
            elif phase == "transport":
                responses = [{"error": "invalid_request"}, hello]
            else:
                responses = [dict(outcome="exact", steps_verified=0, failure_reproduced=False,
                                  build_matches=True, step=None, field=None)]
            return real_invoke(command(lines(responses)), requests, timeout, **kwargs)

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(RUN, "generated_graphs", return_value=[]))
            stack.enter_context(mock.patch.object(RUN, "transport_cases", return_value=[(b"bad\xff", {"error": "invalid_request"})]))
            stack.enter_context(mock.patch.object(RUN.runpy, "run_path", return_value={"cases": lambda: [], "transport": lambda: []}))
            stack.enter_context(mock.patch.object(RUN, "invoke", side_effect=route))
            stdout = stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(RUN.main(args), 1)
        result = json.loads(report.read_text())
        self.assertEqual(json.loads(stdout.getvalue()), result)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["complete"])
        entry = self.entries(artifact_dir)[-1]
        self.assert_stream(artifact_dir, entry, "stdout", failed_output)
        self.assert_stream(artifact_dir, entry, "stderr", failed_stderr)
        self.assertEqual(result["failures"][-1]["receipt"]["id"], entry["id"])
        return result, entry

    def test_partial_aggregate_retains_case_mismatches_on_all_process_failures(self):
        for mode, status in (("nonzero", "nonzero_exit"), ("malformed", "malformed_response"), ("timeout", "timeout")):
            with self.subTest(mode=mode):
                result, entry = self.aggregate_fixture("transport", mode)
                self.assertEqual(entry["status"], status)
                self.assertEqual(result["receipts"]["invocations"], 6)
                self.assertEqual([item["status"] for item in result["implementations"]], ["passed", "aborted"])
                self.assertEqual(result["implementations"][1]["completed_phases"], ["hello", "cases"])
                self.assertEqual(result["implementations"][1]["completed_cases"], 1)
                self.assertEqual(result["implementations"][1]["failures"], 1)
                self.assertEqual(result["failures"][0]["id"], "record-one")
                self.assertEqual(result["fresh_process_replays"], 0)
                self.assertEqual(result["active_invocation"], {"phase": "transport", "runner_index": 1})
                self.assert_stream(Path(result["receipts"]["directory"]), entry, "input",
                                   b'bad\xff\n{"version": 1, "operation": "hello"}\n')

    def test_partial_aggregate_retains_completed_replay_counts(self):
        result, entry = self.aggregate_fixture("replay")
        self.assertEqual(result["fresh_process_replays"], 1)
        self.assertEqual(result["completed_replay_pairs"], 1)
        self.assertEqual(result["receipts"]["invocations"], 8)
        self.assertTrue(all(item["status"] == "passed" for item in result["implementations"]))
        self.assertEqual(entry["context"], {"phase": "replay", "runner_index": 1,
                                             "producer": "fixture-a", "consumer": "fixture-b"})

    def test_malformed_hello_shape_writes_partial_report_and_receipt(self):
        report = self.directory / "hello-report.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(RUN.main(["--runner", json.dumps(command(b"[]\n", b"hello\xff")),
                                       "--report", str(report)]), 1)
        result = json.loads(report.read_text())
        self.assertFalse(result["complete"])
        self.assertEqual(result["implementations"], [])
        self.assertEqual(result["active_invocation"]["phase"], "hello")
        directory = Path(result["receipts"]["directory"])
        entry, = self.entries(directory)
        self.assert_stream(directory, entry, "stdout", b"[]\n")
        self.assert_stream(directory, entry, "stderr", b"hello\xff")

    def test_report_can_live_inside_fresh_artifact_directory(self):
        directory = self.directory / "nested-receipts"
        report = directory / "reports/baseline.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(RUN.main(["--runner", json.dumps(command(b"[]\n")),
                                       "--artifact-dir", str(directory), "--report", str(report)]), 1)
        result = json.loads(report.read_text())
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["complete"])
        entry, = self.entries(directory)
        self.assert_stream(directory, entry, "stdout", b"[]\n")
        # A second attempt must not replace the first attempt's receipts.
        before = (directory / "invocations.jsonl").read_bytes()
        with self.assertRaises(FileExistsError), redirect_stdout(io.StringIO()):
            RUN.main(["--runner", json.dumps(command()), "--artifact-dir", str(directory),
                      "--report", str(report)])
        self.assertEqual((directory / "invocations.jsonl").read_bytes(), before)
        self.assertEqual(json.loads(report.read_text()), result)

    def test_full_workload_and_fresh_pairs_remain_intact(self):
        # This is a harness scope control using authored goldens, not engine qualification.
        corpus = json.loads((ROOT / "conformance/corpus.json").read_text())
        portable = runpy.run_path(str(ROOT / "conformance/portability.py"))
        # The expected outputs retain the existing case order; these stand-ins
        # only control harness scope, never qualify an engine's actual behavior.
        generated = list(RUN.generated_graphs())
        ordered = corpus["cases"] + generated + generated[::17] + list(portable["cases"]())
        expected = [case["expected"] for case in ordered]
        observed = []

        def oracle(argv, requests, timeout, *, receipts, context):
            observed.append((argv[0], context.copy(), len(requests)))
            hello = dict(protocol_version=1, spec_version="1.0.0", profiles=corpus["profiles"], implementation=argv[0])
            phase = context["phase"]
            if phase == "hello":
                return [hello]
            if phase == "cases":
                return expected
            if phase == "transport":
                return [value for _, value in list(RUN.transport_cases()) + list(portable["transport"]())] + [hello]
            return [dict(outcome="exact", steps_verified=len(request["trace"]["steps"]),
                         failure_reproduced=request["trace"]["termination"] == "property_failed",
                         build_matches=True, step=None, field=None) for request in requests]

        report = self.directory / "scope.json"
        args = ["--report", str(report)]
        for engine in ("python", "go", "typescript", "swift", "rust"):
            args += ["--runner", json.dumps([engine])]
        with mock.patch.object(RUN, "invoke", side_effect=oracle), redirect_stdout(io.StringIO()):
            self.assertEqual(RUN.main(args), 0)
        result = json.loads(report.read_text())
        self.assertTrue(result["complete"])
        self.assertEqual(result["fresh_process_replays"], 550)
        self.assertEqual(result["completed_replay_pairs"], 25)
        self.assertEqual(len(observed), 40)
        self.assertEqual(len({(context["producer"], context["consumer"])
                              for _, context, _ in observed if context["phase"] == "replay"}), 25)
        for summary in result["implementations"]:
            self.assertEqual(summary["completed_cases"], 694)
            self.assertEqual(summary["completed_transport_cases"], 23)
            self.assertEqual(summary["completed_cases"] + summary["completed_transport_cases"], 717)
        self.assertEqual([size for _, context, size in observed if context["phase"] == "replay"], [22] * 25)


if __name__ == "__main__":
    unittest.main()

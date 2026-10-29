"""Source-only Q1-Q4 regressions for the standalone N06 Python qualifier.

Future execution, from the checkout root and through the shared Kyoto queue:
    python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 -I -B \
        validation/native-parity/n06/python/post-r1/test_install_qualifier.py -v

This file has not been executed, compiled, or imported during authoring. It is
outside frozen r1 and does not qualify N05, installed packages, or Rust parity.
Only the qualifier is loaded, by file path; main() tests use temporary synthetic
sources and mocked commands. Process tests run bounded stdlib-only children.
Every fork branch arms its own alarm. An inherited pipe observes descendant
liveness without ever signalling a PID/PGID after its leader has been reaped.
Orphan zombies can remain visible to ps on hosts with a slow system reaper;
pipe EOF proves closed writers, while the receipt must truthfully report ps.
"""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[5]
QUALIFIER = ROOT / "python/tests/qualify_campaign_install.py"
POSIX = os.name == "posix" and all(
    hasattr(os, name)
    for name in ("fork", "waitid", "WNOWAIT", "WEXITED", "WNOHANG", "P_PID", "O_NOFOLLOW", "O_DIRECTORY")
)

# Independently transcribed from seed_rows(), validation_rows(), and CASES in
# conformance/fixtures/native-campaign-public.rs. Never derive from PUBLIC_CASES
# or the consumer being checked; the equal-count mutations below must fail.
EXPECTED_CASES = (
    "seed-00", "seed-01", "seed-02", "seed-03", "seed-04",
    "seed-05", "seed-06", "seed-07", "seed-08", "seed-09",
    "seed-10", "seed-11", "seed-12", "seed-13", "seed-14",
    "jobs-zero", "workers-zero", "workers-257", "buffer-1025", "workers-256",
    "buffer-1024", "max-id", "id-overflow", "mutation-101", "mutation-100",
    "aggregate-cases", "aggregate-transitions", "wide-product-capped",
    "max-job-count-valid", "validation-first-error",
    "ordinary-basic", "guided-basic", "factory-error", "feedback-error",
    "factory-cancelled", "pre-cancelled", "deadline-zero", "cases-zero",
    "steps-zero", "transitions-zero", "initial-error", "step-error",
    "check-error", "metadata-panic", "feedback-metadata-panic", "drop-panic",
    "sink-error", "sink-panic", "property-stop",
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class QualifierTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("n06_install_qualifier_under_test", QUALIFIER)
        self.q = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, "dont_write_bytecode", True):
            spec.loader.exec_module(self.q)
        self.temporary = tempfile.TemporaryDirectory(prefix="n06-qualifier-regression-")
        self.addCleanup(self.temporary.cleanup)
        # macOS commonly spells temp paths through the /var symlink. Inventory
        # intentionally rejects symlink ancestors, so anchor real test roots.
        self.base = Path(self.temporary.name).resolve()

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))


class CaseInventoryTests(QualifierTest):
    def test_exact_independent_49_case_protocol(self):
        self.assertEqual(len(EXPECTED_CASES), 49)
        self.assertEqual(tuple(self.q.PUBLIC_CASES), EXPECTED_CASES)
        document = {"schema": "native-campaign-case-inventory-v1", "cases": list(EXPECTED_CASES)}
        self.assertEqual(self.q.validate_case_inventory(document), list(EXPECTED_CASES))

    def test_rejects_equal_count_replacement_duplicate_and_order_drift(self):
        replacement, duplicate, reordered = (list(EXPECTED_CASES) for _ in range(3))
        replacement[44] = "unknown-metadata-case"
        duplicate[44] = duplicate[43]
        reordered[43], reordered[44] = reordered[44], reordered[43]
        for label, cases in (("replacement", replacement), ("duplicate", duplicate), ("order", reordered)):
            with self.subTest(label=label):
                self.assertEqual(len(cases), 49)
                with self.assertRaises(RuntimeError):
                    self.q.validate_case_inventory({"schema": "native-campaign-case-inventory-v1", "cases": cases})

    def test_rejects_missing_extra_or_malformed_inventory(self):
        for cases in (list(EXPECTED_CASES[:-1]), [*EXPECTED_CASES, "extra"], None, "49", tuple(EXPECTED_CASES)):
            with self.subTest(cases=cases), self.assertRaises(RuntimeError):
                self.q.validate_case_inventory({"schema": "native-campaign-case-inventory-v1", "cases": cases})
        for value in (None, [], {"cases": list(EXPECTED_CASES)}, {"schema": "wrong", "cases": list(EXPECTED_CASES)}):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                self.q.validate_case_inventory(value)


@unittest.skipUnless(POSIX, "requires POSIX filesystem and retained-child support")
class InventoryTests(QualifierTest):
    def test_root_empty_directory_file_mode_and_deletion_identity(self):
        root = self.base / "tree"
        root.mkdir(mode=0o750)
        root.chmod(0o750)
        empty = root / "empty"
        empty.mkdir()
        empty.chmod(0o700)
        payload = root / "payload.bin"
        payload.write_bytes(b"identity\x00\xff")
        payload.chmod(0o640)
        before = self.q.inventory(root)
        self.assertEqual(set(before), {".", "empty", "payload.bin"})
        self.assertEqual(before["."], {"type": "directory", "mode": "0o750"})
        self.assertEqual(before["empty"], {"type": "directory", "mode": "0o700"})
        self.assertEqual(before["payload.bin"], {
            "type": "file", "mode": "0o640", "size": 10, "sha256": sha(b"identity\x00\xff"),
        })
        root.chmod(0o700)
        empty.chmod(0o750)
        payload.chmod(0o600)
        changed = self.q.inventory(root)
        for name in before:
            self.assertNotEqual(before[name]["mode"], changed[name]["mode"])
        self.assertEqual(changed["payload.bin"]["sha256"], before["payload.bin"]["sha256"])
        empty.rmdir()
        payload.unlink()
        self.assertEqual(set(self.q.inventory(root)), {"."})
        root.rmdir()
        with self.assertRaises(self.q.InventoryError) as caught:
            self.q.inventory(root)
        self.assertTrue(caught.exception.errors)
        self.assertEqual(self.q.observation(root)["status"], "failed")
        missing = self.q.observation(root, allow_missing=True)
        self.assertEqual(missing, {"status": "missing", "entries": {".": {"type": "missing"}}})

    def test_symlink_fifo_and_read_failure_preserve_other_entries(self):
        root = self.base / "tree"
        root.mkdir()
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "must-not-be-read").write_bytes(b"private target")
        (root / "a-link").symlink_to(outside, target_is_directory=True)
        os.mkfifo(root / "b-fifo", 0o600)
        (root / "c-denied").write_bytes(b"unreadable via injected error")
        (root / "z-readable").write_bytes(b"retained")
        real_open = os.open
        opened_special = []

        def guarded_open(path, flags, *args, **kwargs):
            name = os.path.basename(os.fsdecode(path))
            if name in {"a-link", "b-fifo"}:
                opened_special.append(name)
                raise AssertionError("inventory must not open links or FIFOs")
            if name == "c-denied":
                raise PermissionError("injected unreadable file")
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(self.q.os, "open", side_effect=guarded_open):
            with self.assertRaises(self.q.InventoryError) as caught:
                self.q.inventory(root)
        error = caught.exception
        self.assertEqual(opened_special, [])
        self.assertEqual(set(error.entries), {".", "a-link", "b-fifo", "c-denied", "z-readable"})
        self.assertEqual(error.entries["a-link"]["type"], "symlink")
        self.assertEqual(error.entries["a-link"]["target"], str(outside))
        self.assertEqual(error.entries["b-fifo"]["type"], "special")
        self.assertEqual(error.entries["b-fifo"]["stat_type"], stat.S_IFIFO)
        self.assertEqual(error.entries["z-readable"]["sha256"], sha(b"retained"))
        for name in ("a-link", "b-fifo", "c-denied"):
            self.assertTrue(any(name in item for item in error.errors), error.errors)
        self.assertFalse(any("must-not-be-read" in name for name in error.entries))

    def test_symlink_root_and_dangling_symlink_are_failed_not_missing(self):
        target = self.base / "target"
        target.mkdir()
        (target / "secret").write_bytes(b"do not follow")
        link = self.base / "root-link"
        link.symlink_to(target, target_is_directory=True)
        observed = self.q.observation(link, allow_missing=True)
        self.assertEqual(observed["status"], "failed")
        self.assertEqual(set(observed["entries"]), {"."})
        self.assertEqual(observed["entries"]["."]["type"], "symlink")
        link.unlink()
        link.symlink_to(self.base / "absent")
        self.assertEqual(self.q.observation(link, allow_missing=True)["status"], "failed")

    def test_directory_swapped_to_symlink_before_open_never_traverses_target(self):
        for replace_root in (False, True):
            with self.subTest(replace_root=replace_root):
                base = self.base / str(replace_root)
                base.mkdir()
                root, target = base / "tree", base / "outside"
                root.mkdir()
                target.mkdir()
                (target / "must-not-be-read").write_bytes(b"external data")
                victim = root if replace_root else root / "swap-directory"
                if victim != root:
                    victim.mkdir()
                real_open = os.open
                substituted, target_reads = [], []

                def replace_before_open(path, flags, *args, **kwargs):
                    name = os.path.basename(os.fsdecode(path))
                    if name == victim.name and not substituted:
                        victim.rmdir()
                        victim.symlink_to(target, target_is_directory=True)
                        substituted.append(True)
                    if name == "must-not-be-read":
                        target_reads.append(name)
                        raise AssertionError("substituted directory target was traversed")
                    return real_open(path, flags, *args, **kwargs)

                with mock.patch.object(self.q.os, "open", side_effect=replace_before_open):
                    with self.assertRaises(self.q.InventoryError) as caught:
                        self.q.inventory(root)
                self.assertEqual(substituted, [True], "directory-open race injection must be reached")
                self.assertEqual(target_reads, [])
                self.assertFalse(any("must-not-be-read" in name for name in caught.exception.entries))
                self.assertTrue(caught.exception.errors)

    def test_absence_does_not_hide_invalid_or_inaccessible_ancestors(self):
        broken = self.base / "broken"
        broken.symlink_to(self.base / "absent", target_is_directory=True)
        observed = self.q.observation(broken / "site-packages", allow_missing=True)
        self.assertEqual(observed["status"], "failed")
        self.assertTrue(observed["errors"])
        blocked = self.base / "blocked"
        blocked.mkdir()
        real_stat = os.stat

        def denied(path, *args, **kwargs):
            if path == "blocked":
                raise PermissionError("inaccessible ancestor")
            return real_stat(path, *args, **kwargs)

        with mock.patch.object(self.q.os, "stat", side_effect=denied):
            observed = self.q.observation(blocked / "site-packages", allow_missing=True)
        self.assertEqual(observed["status"], "failed")
        self.assertTrue(any("inaccessible ancestor" in error for error in observed["errors"]))
        self.assertEqual(self.q.observation(self.base / "not-created" / "site-packages",
                                           allow_missing=True)["status"], "missing")

    def test_symlink_parent_traversal_is_rejected_before_lexical_normalization(self):
        target = self.base / "outside" / "child"
        target.mkdir(parents=True)
        (self.base / "outside" / "driver.py").write_bytes(b"actual target")
        (self.base / "driver.py").write_bytes(b"different lexical target")
        (self.base / "link").symlink_to(target, target_is_directory=True)
        with mock.patch.object(self.q.os, "open", side_effect=AssertionError("must reject before opening")):
            observed = self.q.observation(self.base / "link" / ".." / "driver.py")
        self.assertEqual(observed["status"], "failed")
        self.assertTrue(any("parent traversal" in error for error in observed["errors"]))
        self.assertEqual(observed["entries"], {})

    def test_interrupted_inventory_keeps_partial_entries_and_original_object(self):
        root = self.base / "tree"
        root.mkdir()
        (root / "a-readable").write_bytes(b"first")
        (root / "z-interrupted").write_bytes(b"second")
        interruption = KeyboardInterrupt("inventory interruption")
        real_open = os.open

        def interrupted_open(path, flags, *args, **kwargs):
            if os.path.basename(os.fsdecode(path)) == "z-interrupted":
                raise interruption
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(self.q.os, "open", side_effect=interrupted_open):
            with self.assertRaises(self.q.InventoryError) as caught:
                self.q.observation(root)
        self.assertIs(caught.exception.interruption, interruption)
        self.assertEqual(caught.exception.entries["a-readable"]["sha256"], sha(b"first"))


@unittest.skipUnless(POSIX, "requires POSIX inventories")
class CaptureTests(QualifierTest):
    def make_captures(self):
        output = self.base / "receipts"
        output.mkdir()
        report = {}
        captures = self.q.Captures(output, report)
        paths = []
        for name in ("first", "second", "last"):
            path = self.base / name
            path.mkdir()
            (path / "content").write_bytes(name.encode())
            captures.watch(name, path, immutable=True)
            paths.append(path)
        return captures, output, report, paths

    def test_failed_after_inventory_does_not_skip_later_mutation_or_deletion(self):
        captures, output, report, paths = self.make_captures()
        (paths[0] / "bad-link").symlink_to(self.base / "absent")
        (paths[1] / "empty-added").mkdir()
        (paths[2] / "content").unlink()
        failures = captures.finish()
        self.assertTrue(failures)
        for scope in report["captures"]:
            self.assertIn("after", scope)
            self.assertFalse(scope["unchanged"])
            self.assertEqual(self.read_json(output / (scope["name"] + "-after.json")), scope["after"])
        self.assertEqual(report["captures"][0]["after"]["status"], "failed")
        self.assertIn("content", report["captures"][0]["after"]["entries"])
        self.assertIn("empty-added", report["captures"][1]["after"]["entries"])
        self.assertNotIn("content", report["captures"][2]["after"]["entries"])

    def test_finish_keeps_first_interruption_and_attempts_every_scope(self):
        for interrupt_type in (KeyboardInterrupt, SystemExit):
            with self.subTest(interrupt_type=interrupt_type.__name__):
                # Each subcase needs distinct paths; remove no evidence in-place.
                original_base = self.base
                self.base = original_base / interrupt_type.__name__
                self.base.mkdir()
                try:
                    captures, output, report, paths = self.make_captures()
                    first = interrupt_type("first final-capture interruption")
                    later = KeyboardInterrupt("later final-capture interruption")
                    original = self.q.observation
                    attempts = []

                    def observe(path, **kwargs):
                        attempts.append(Path(path))
                        if Path(path) == paths[0]:
                            raise first
                        if Path(path) == paths[1]:
                            raise later
                        return original(path, **kwargs)

                    with mock.patch.object(self.q, "observation", side_effect=observe):
                        failures = captures.finish()
                    self.assertIs(captures.interruption, first)
                    self.assertEqual(attempts, paths)
                    self.assertGreaterEqual(len(failures), 2)
                    for scope in report["captures"]:
                        self.assertEqual(self.read_json(output / (scope["name"] + "-after.json")), scope["after"])
                    self.assertTrue(report["captures"][-1]["unchanged"])
                finally:
                    self.base = original_base

    def test_failed_before_is_registered_for_final_partial_evidence(self):
        output, root = self.base / "receipts", self.base / "tree"
        output.mkdir()
        root.mkdir()
        (root / "good").write_bytes(b"keep")
        (root / "bad").symlink_to(self.base / "absent")
        report = {}
        captures = self.q.Captures(output, report)
        with self.assertRaises(RuntimeError):
            captures.watch("attempted", root, immutable=True)
        self.assertEqual(len(report["captures"]), 1)
        self.assertEqual(self.read_json(output / "attempted-before.json")["status"], "failed")
        (root / "bad").unlink()
        self.assertTrue(captures.finish())
        self.assertEqual(self.read_json(output / "attempted-after.json")["status"], "complete")
        self.assertFalse(report["captures"][0]["unchanged"])

    def test_interruption_survives_failed_failure_receipt_and_later_captures_run(self):
        captures, output, report, paths = self.make_captures()
        interruption = KeyboardInterrupt("original observation interruption")
        observe, save = self.q.observation, self.q.save_json

        def fail_observation(path, **kwargs):
            if Path(path) == paths[0]:
                raise interruption
            return observe(path, **kwargs)

        def fail_receipt(path, value):
            if Path(path) == output / "first-after.json":
                raise OSError("failure receipt unavailable")
            return save(path, value)

        with mock.patch.object(self.q, "observation", side_effect=fail_observation), \
                mock.patch.object(self.q, "save_json", side_effect=fail_receipt):
            failures = captures.finish()
        self.assertTrue(failures)
        self.assertIs(captures.interruption, interruption)
        first, second, last = report["captures"]
        self.assertEqual(first["after"]["status"], "failed")
        self.assertIn("failure receipt unavailable", first["after_save_error"])
        self.assertTrue(second["unchanged"])
        self.assertTrue(last["unchanged"])
        self.assertEqual(self.read_json(output / "last-after.json"), last["after"])


# The readiness FD is inherited by both branches. Only the child keeps it open
# after an exiting leader, so EOF independently detects descendant termination.
# alarm() is explicitly rearmed after fork; no child can live indefinitely even
# if the code under test reaps too early and loses its signalling authority.
PROCESS_FIXTURE = r"""
import os, signal, sys, time
fd, fork_child, code, hold = map(int, sys.argv[1:])
signal.alarm(12)
os.write(1, b'leader stdout\n')
os.write(2, b'leader stderr\n')
if fork_child:
    ack_read, ack_write = os.pipe()
    child = os.fork()
    if child == 0:
        signal.alarm(12)
        os.close(ack_read)
        os.write(1, b'descendant stdout\n')
        os.write(2, b'descendant stderr\n')
        os.write(fd, b'R')
        os.write(ack_write, b'R')
        os.close(ack_write)
        time.sleep(12)
        os._exit(0)
    os.close(ack_write)
    if os.read(ack_read, 1) != b'R':
        os._exit(90)
    os.close(ack_read)
else:
    os.write(fd, b'R')
if hold:
    time.sleep(12)
os._exit(code)
"""


@unittest.skipUnless(POSIX, "requires fork, ps, and POSIX waitid/WNOWAIT")
class CommandTests(QualifierTest):
    def exercise(self, *, descendants=False, code=0, mode="exit", faults=False, late=None):
        output = self.base / "command"
        output.mkdir()
        logs = output / "logs"
        logs.mkdir()
        report = {"commands": []}
        ready_read, ready_write = os.pipe()
        real_popen, real_killpg, real_waitid = subprocess.Popen, os.killpg, os.waitid
        real_wait_retained, real_clean, real_digest = self.q.wait_retained, self.q.clean_owned_group, self.q.digest
        owned, events, guards, saves = [], [], [], []
        writer_open, initial_wait = True, True
        interruption = KeyboardInterrupt("injected command interruption")
        late_interruption = (SystemExit("late command interruption") if late == "save" else
                             KeyboardInterrupt("late command interruption"))

        def retained(pid):
            return real_waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)

        def launch(*args, **kwargs):
            nonlocal writer_open
            if not kwargs.get("start_new_session"):
                return real_popen(*args, **kwargs)  # ps probes are not the owned group.
            kwargs["pass_fds"] = (ready_write,)
            process = real_popen(*args, **kwargs)
            real_wait, real_poll = process.wait, process.poll
            owned.append((process, real_wait))
            os.close(ready_write)
            writer_open = False

            def reap(*args, **kwargs):
                events.append(("reap", process.pid))
                result = real_wait(*args, **kwargs)
                if late == "reap":
                    raise late_interruption
                return result

            def poll(*args, **kwargs):
                events.append(("poll", process.pid))
                return real_poll(*args, **kwargs)

            process.wait, process.poll = reap, poll
            return process

        def checked_killpg(pgid, sig):
            # A failure refuses the actual signal. Never forward an unknown or
            # already-reaped numeric ID, even if the helper regresses.
            process = next((p for p, _ in owned if p.pid == pgid), None)
            if process is None:
                guards.append("signal outside owned group")
                raise AssertionError(guards[-1])
            try:
                status = retained(pgid)
            except ChildProcessError:
                guards.append("signal after leader reap")
                raise AssertionError(guards[-1])
            events.append(("signal", pgid, sig, status))
            return real_killpg(pgid, sig)

        def wait(process, timeout):
            nonlocal initial_wait
            if initial_wait:
                initial_wait = False
                readable, _, _ = select.select([ready_read], [], [], 5.0)
                if not readable or os.read(ready_read, 1) != b"R":
                    raise AssertionError("bounded fixture did not publish readiness")
                if mode == "interrupt":
                    raise interruption
            return real_wait_retained(process, timeout)

        def clean(process, **kwargs):
            if late == "cleanup":
                raise late_interruption
            return real_clean(process, **kwargs)

        def digest(path):
            if late == "hash" and Path(path).suffix == ".stdout":
                raise late_interruption
            if faults and Path(path).suffix in {".stdout", ".stderr"}:
                raise PermissionError("injected " + Path(path).suffix + " hash failure")
            return real_digest(path)

        def save():
            saves.append(json.loads(json.dumps(report)))
            if len(saves) > 1:
                if late == "save":
                    raise late_interruption
                if faults:
                    raise OSError("injected receipt save failure")
            (output / "receipt.json").write_text(json.dumps(report), encoding="utf-8")

        result, caught = None, None
        try:
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(self.q.subprocess, "Popen", side_effect=launch))
                stack.enter_context(mock.patch.object(self.q.os, "killpg", side_effect=checked_killpg))
                stack.enter_context(mock.patch.object(self.q, "wait_retained", side_effect=wait))
                stack.enter_context(mock.patch.object(self.q, "clean_owned_group", side_effect=clean))
                stack.enter_context(mock.patch.object(self.q, "digest", side_effect=digest))
                if faults:
                    stack.enter_context(mock.patch.object(self.q, "group_members", side_effect=OSError("injected ps failure")))
                try:
                    result = self.q.run_command(
                        "probe", [sys.executable, "-I", "-B", "-c", PROCESS_FIXTURE,
                                  str(ready_write), str(int(descendants)), str(code), str(int(mode != "exit"))],
                        cwd=output, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                        logs=logs, output=output, timeout=0.10 if mode == "timeout" else 5.0,
                        report=report, save=save,
                    )
                except BaseException as error:
                    caught = error
            self.assertEqual(guards, [], "cleanup attempted an unowned signal")
            self.assertEqual(len(owned), 1)
            process = owned[0][0]
            self.assertFalse(initial_wait, "the real command wait was not reached")
            self.assertIsNotNone(process.returncode, "leader was not reaped")
            self.assertFalse(any(item[0] == "poll" for item in events), events)
            reaps = [index for index, item in enumerate(events) if item[0] == "reap"]
            self.assertEqual(len(reaps), 1, events)
            for index, event in enumerate(events):
                if event[0] == "signal":
                    self.assertLess(index, reaps[0], events)
                    self.assertEqual(event[2], signal.SIGKILL)
            readable, _, _ = select.select([ready_read], [], [], 1.0)
            self.assertTrue(readable, "a descendant still holds the pipe after command cleanup")
            self.assertEqual(os.read(ready_read, 1), b"", "descendant writer did not close")
            entry, = report["commands"]
            expected_out = b"leader stdout\n" + (b"descendant stdout\n" if descendants else b"")
            expected_err = b"leader stderr\n" + (b"descendant stderr\n" if descendants else b"")
            self.assertEqual((logs / "probe.stdout").read_bytes(), expected_out)
            self.assertEqual((logs / "probe.stderr").read_bytes(), expected_err)
            if not faults and late != "hash":
                self.assertEqual(entry["stdout_sha256"], sha(expected_out))
            if not faults:
                self.assertEqual(entry["stderr_sha256"], sha(expected_err))
            self.assertGreaterEqual(len(saves), 2)
            if late != "save" and not faults:
                self.assertEqual(self.read_json(output / "receipt.json")["commands"], report["commands"])
            return result, caught, entry, events, interruption, late_interruption
        finally:
            # Fallback only while waitid proves direct-child ownership. If a
            # broken helper already reaped, rely on the fixture's own alarms.
            for process, original_wait in owned:
                try:
                    retained(process.pid)
                except ChildProcessError:
                    continue
                try:
                    real_killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                original_wait(timeout=3.0)
            if writer_open:
                os.close(ready_write)
            os.close(ready_read)

    def test_normal_command_returns_bytes_and_complete_receipt(self):
        result, error, entry, events, _, _ = self.exercise()
        self.assertIsNone(error)
        self.assertEqual(result, b"leader stdout\n")
        self.assertEqual(entry["status"], "completed")
        self.assertEqual(entry["exit_code"], 0)
        self.assertEqual(entry["secondary_errors"], [])
        self.assertFalse(any(item[0] == "signal" for item in events))
        self.assertFalse(entry["owned_process_group"]["forced_cleanup"])
        self.assertTrue(entry["owned_process_group"]["descendants_absent"])

    def test_nonzero_command_preserves_primary_and_logs(self):
        _, error, entry, events, _, _ = self.exercise(code=23)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn("exited with code 23", str(error))
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(entry["exit_code"], 23)
        self.assertEqual(entry["secondary_errors"], [])
        self.assertFalse(any(item[0] == "signal" for item in events))

    def assert_descendant_cleanup(self, result):
        _, error, entry, events, _, _ = result
        self.assertIsNotNone(error)
        signals = [event for event in events if event[0] == "signal"]
        self.assertTrue(signals, "lingering descendants require an actual owned-group signal")
        group = entry["owned_process_group"]
        self.assertTrue(group["forced_cleanup"])
        self.assertTrue(group["leader_retained"])
        self.assertEqual(group["descendants_absent"], not group["remaining_members"])
        self.assertTrue(any("outlived" in item or "termination" in item for item in entry["secondary_errors"]))
        return error, entry, signals

    def test_exit_zero_with_descendant_fails_and_retains_zombie_leader_until_kill(self):
        error, entry, signals = self.assert_descendant_cleanup(self.exercise(descendants=True))
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(entry["exit_code"], 0)
        self.assertIsNotNone(signals[0][3], "exited leader must remain waitable at kill")
        self.assertEqual(signals[0][3].si_status, 0)

    def test_nonzero_with_descendant_preserves_exit_error(self):
        error, entry, signals = self.assert_descendant_cleanup(self.exercise(descendants=True, code=23))
        self.assertIsInstance(error, RuntimeError)
        self.assertIn("exited with code 23", str(error))
        self.assertEqual(entry["exit_code"], 23)
        self.assertEqual(entry["status"], "failed")
        self.assertIsNotNone(signals[0][3])
        self.assertEqual(signals[0][3].si_status, 23)

    def test_timeout_with_descendant_keeps_timeout_as_primary(self):
        error, entry, _ = self.assert_descendant_cleanup(self.exercise(descendants=True, mode="timeout"))
        self.assertIsInstance(error, subprocess.TimeoutExpired)
        self.assertEqual(entry["status"], "timed_out")
        self.assertEqual(entry["exit_code"], -signal.SIGKILL)
        self.assertIn("TimeoutExpired", entry["error"])

    def test_interruption_with_descendant_preserves_same_object(self):
        result = self.exercise(descendants=True, mode="interrupt")
        error, entry, _ = self.assert_descendant_cleanup(result)
        self.assertIs(error, result[4])
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(entry["exit_code"], -signal.SIGKILL)
        self.assertIn("KeyboardInterrupt", entry["error"])

    def test_cleanup_hash_and_receipt_errors_do_not_replace_primary(self):
        _, error, entry, events, interruption, _ = self.exercise(descendants=True, mode="interrupt", faults=True)
        self.assertIs(error, interruption)
        self.assertEqual(entry["status"], "failed")
        for marker in ("injected ps failure", "stdout hash failure", "stderr hash failure", "receipt save failure"):
            self.assertTrue(any(marker in item for item in entry["secondary_errors"]), entry)
        self.assertTrue(any(item[0] == "signal" for item in events), "fallback must terminate while owned")

    def test_first_interruption_in_cleanup_reap_hash_or_save_is_reraised(self):
        for phase in ("cleanup", "reap", "hash", "save"):
            with self.subTest(phase=phase):
                original_base = self.base
                self.base = original_base / phase
                self.base.mkdir()
                try:
                    _, error, entry, _, _, interruption = self.exercise(late=phase)
                    self.assertIs(error, interruption)
                    self.assertEqual(entry["status"], "failed")
                    self.assertEqual(entry["exit_code"], 0)
                finally:
                    self.base = original_base

    def test_late_interruptions_do_not_replace_prior_nonzero_exit(self):
        for phase in ("cleanup", "reap", "hash", "save"):
            with self.subTest(phase=phase):
                original_base = self.base
                self.base = original_base / phase
                self.base.mkdir()
                try:
                    _, error, entry, _, _, interruption = self.exercise(code=23, late=phase)
                    self.assertIsInstance(error, RuntimeError)
                    self.assertIsNot(error, interruption)
                    self.assertIn("exited with code 23", str(error))
                    self.assertEqual(entry["status"], "failed")
                    self.assertEqual(entry["exit_code"], 23)
                    self.assertTrue(any(str(interruption) in item for item in entry["secondary_errors"]))
                finally:
                    self.base = original_base

    def test_lost_child_ownership_never_signals_reaped_numeric_id(self):
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", "import signal; signal.alarm(3)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        process.wait(timeout=5)
        with mock.patch.object(self.q.os, "killpg", side_effect=AssertionError("unowned signal")) as kill:
            with self.assertRaises(ChildProcessError):
                self.q.signal_owned_group(process)
            with self.assertRaises(ChildProcessError):
                self.q.clean_owned_group(process, terminate=True)
            kill.assert_not_called()


@unittest.skipUnless(POSIX, "main requires POSIX inventory and retained-child support")
class MainFailureTests(QualifierTest):
    def prepare_sources(self):
        root = self.base / "synthetic-checkout"
        root.mkdir()
        sources = {
            "python/src/package/__init__.py": b"# synthetic package; never imported\n",
            "python/pyproject.toml": b"# build is mocked\n",
            "python/README.md": b"synthetic readme\n",
            "python/LICENSE": b"synthetic license\n",
            "python/tests/qualify_campaign_install.py": b"# synthetic executing-file identity; never executed\n",
            self.q.EXAMPLE: b"# synthetic consumer; never executed\n",
            self.q.REFERENCE: b"// retained source placeholder\n",
            self.q.AUTHOR: b"# retained source placeholder\n",
            self.q.CONTRACT: b"retained contract placeholder\n",
        }
        for relative, data in sources.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return root

    def exercise_main(self, stage=None, interrupt=False, final_interrupt=False, final_save_error=None,
                      identity_fault=None):
        root = self.prepare_sources()
        driver = root / "python/tests/qualify_campaign_install.py"
        expected_driver = sha(driver.read_bytes()) if identity_fault != "mismatch" else "0" * 64
        output = self.base / "qualification-output"  # Must not yet exist or contain ROOT.
        sentinel = KeyboardInterrupt("injected " + str(stage)) if interrupt else RuntimeError("injected " + str(stage))
        final_sentinel = SystemExit("injected final observation interruption")
        installed_relative = f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        reached, commands = [], []
        original_copytree, original_observation = self.q.shutil.copytree, self.q.observation
        original_save = self.q.save_json

        def save_json(path, value):
            original_save(path, value)
            if (final_save_error is not None and Path(path) == output / "qualification.json"
                    and value.get("status") == "passed_installed_consumers"):
                raise final_save_error  # A complete success receipt is already on disk.

        def fail_if_target(name):
            if name == stage:
                reached.append(name)
                raise sentinel

        def copytree(source, destination, *args, **kwargs):
            if stage == "source-copy" and Path(source) == root / "python/src":
                Path(destination).mkdir()
                (Path(destination) / "partial-copy").write_bytes(b"partial source")
                fail_if_target("source-copy")
            return original_copytree(source, destination, *args, **kwargs)

        def run(name, argv, *, cwd, env, logs, output, timeout, report, save):
            commands.append(name)
            (logs / (name + ".stdout")).write_bytes((name + " stdout\n").encode())
            (logs / (name + ".stderr")).write_bytes((name + " stderr\n").encode())
            work, packages = output / "work", output / "packages"
            result = b""
            if name == "source-commit":
                result = b"0123456789012345678901234567890123456789\n"
                if identity_fault == "driver-change":
                    driver.write_bytes(b"changed after driver admission\n")
                elif identity_fault == "metadata-change":
                    (root / "python/pyproject.toml").write_bytes(b"changed original metadata\n")
                elif identity_fault == "example-change":
                    (root / self.q.EXAMPLE).write_bytes(b"changed original example\n")
            elif name == "source-status":
                result = b""
            elif name.endswith("-venv"):
                site = Path(argv[-1]) / installed_relative
                site.mkdir(parents=True)
                (site / "venv-created").write_bytes(b"venv baseline")
            elif name == "builder-tools":
                (work / "builder" / installed_relative / "build-tools").write_bytes(b"attempted tools")
            elif name == "builder-versions":
                result = b"synthetic-build-tool==1\n"
            elif name == "package-build":
                (work / "package" / "build-attempt").write_bytes(b"attempted build")
                (packages / "package-1-py3-none-any.whl").write_bytes(b"wheel placeholder")
                (packages / "package-1.tar.gz").write_bytes(b"sdist placeholder")
            elif name == "sdist-wheel":
                (packages / "from-sdist" / "package-1-py3-none-any.whl").write_bytes(b"rebuilt wheel placeholder")
            elif name in {"wheel-install", "sdist-install"}:
                label = name.split("-", 1)[0]
                (work / ("runtime-" + label) / installed_relative / "installed-partial").write_bytes(b"attempted install")
            elif name.endswith("-origin"):
                archive = packages / "package-1-py3-none-any.whl"
                if name.startswith("sdist-"):
                    archive = packages / "from-sdist" / archive.name
                result = json.dumps({"archive_sha256": sha(archive.read_bytes())}).encode()
            elif name.endswith("-demo"):
                application = Path(argv[-1])
                application.mkdir()
                (application / "trace").write_bytes(b"synthetic application evidence")
                result = json.dumps({"schema": "native-campaign-python-example-v1", "runs": [{} for _ in range(6)]}).encode()
            elif name.endswith("-inventory"):
                result = json.dumps({"schema": "native-campaign-case-inventory-v1", "cases": list(EXPECTED_CASES)}).encode()
            elif "-reference-" in name:
                identifier = name.split("-reference-", 1)[1]
                kind = "seed" if identifier.startswith("seed-") else "validation" if identifier in EXPECTED_CASES[15:30] else "campaign"
                result = (json.dumps({"schema": "native-campaign-v1", "case": identifier, "kind": kind}) + "\n").encode()
            else:
                raise AssertionError("unexpected command in main fixture: " + name)
            save()
            fail_if_target(name)
            return result

        final_hits = []

        def observe(path, **kwargs):
            # Snapshot-preparation is observed first while missing, then after
            # every command in finish(). Interrupt its after, not its before.
            path = Path(path)
            if (final_interrupt and not final_hits and path == output / "snapshot"
                    and commands and commands[-1].startswith("sdist-reference-")):
                final_hits.append(path)
                raise final_sentinel
            return original_observation(path, **kwargs)

        caught = None
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(self.q, "ROOT", root))
            stack.enter_context(mock.patch.object(self.q, "__file__", str(driver)))
            stack.enter_context(mock.patch.object(sys, "argv", [str(QUALIFIER), "--output", str(output),
                                                               "--driver-sha256", expected_driver]))
            stack.enter_context(mock.patch.object(self.q, "run_command", side_effect=run))
            stack.enter_context(mock.patch.object(self.q.shutil, "copytree", side_effect=copytree))
            stack.enter_context(mock.patch.object(self.q, "observation", side_effect=observe))
            stack.enter_context(mock.patch.object(self.q, "save_json", side_effect=save_json))
            stack.enter_context(mock.patch.object(self.q.subprocess, "Popen", side_effect=AssertionError("main fixture spawned a process")))
            try:
                self.q.main()
            except BaseException as error:
                caught = error
        if stage is not None:
            self.assertEqual(reached, [stage], "failure target was not reached")
            self.assertIs(caught, sentinel, "an incidental fixture error replaced the intended failure")
        elif final_interrupt:
            self.assertIs(caught, final_sentinel)
            self.assertEqual(final_hits, [output / "snapshot"])
        elif final_save_error is not None:
            self.assertIs(caught, final_save_error)
        elif identity_fault is not None:
            self.assertIsInstance(caught, RuntimeError)
        else:
            self.assertIsNone(caught)
        report = self.read_json(output / "qualification.json")
        self.assertEqual(report["status"], "failed" if stage or final_interrupt or final_save_error is not None or identity_fault
                         else "passed_installed_consumers")
        scopes = {scope["name"]: scope for scope in report["captures"]}
        admitted = scopes["executing-driver"]["before"]
        self.assertEqual(admitted["status"], "complete")
        self.assertEqual(admitted["entries"]["."]["type"], "file")
        self.assertEqual(report["executing_driver"]["before"], admitted)
        self.assertEqual(report["executing_driver"]["after"], scopes["executing-driver"]["after"])
        if identity_fault == "mismatch":
            self.assertEqual(commands, [])
            self.assertEqual(set(scopes), {"executing-driver"})
            self.assertIn("caller-selected reviewed SHA-256", report["error"])
            return report
        self.assertEqual(admitted["entries"]["."]["sha256"], expected_driver)
        self.assertIn("snapshot-preparation", scopes)
        self.assertIn("package-production", scopes)
        for name, scope in scopes.items():
            self.assertIn("before", scope, name)
            self.assertIn("after", scope, name)
            for phase in ("before", "after"):
                self.assertEqual(self.read_json(output / f"{name}-{phase}.json"), scope[phase])
        if stage != "source-copy":
            self.assertTrue(scopes["source-snapshot"]["unchanged"])
        self.assertTrue(scopes["package-inputs"]["unchanged"])
        for name in ("executing-driver", "original-package-pyproject.toml", "original-package-README.md",
                     "original-package-LICENSE", "original-example"):
            changed = {"driver-change": "executing-driver", "metadata-change": "original-package-pyproject.toml",
                       "example-change": "original-example"}.get(identity_fault)
            self.assertEqual(scopes[name]["unchanged"], name != changed, name)
        if stage == "source-copy":
            self.assertIn("python/src/partial-copy", scopes["snapshot-preparation"]["after"]["entries"])
        if stage in {"builder-tools", "package-build", "sdist-wheel", "wheel-install", "sdist-install"}:
            tools = scopes["builder-tool-installation"]
            self.assertNotIn("build-tools", tools["before"]["entries"])
            self.assertIn("build-tools", tools["after"]["entries"])
        if stage in {"package-build", "sdist-wheel", "wheel-install", "sdist-install"}:
            self.assertIn("build-attempt", scopes["build-source"]["after"]["entries"])
            self.assertNotIn("build-attempt", scopes["build-source"]["before"]["entries"])
            self.assertIn("package-1-py3-none-any.whl", scopes["package-production"]["after"]["entries"])
        if stage in {"sdist-wheel", "wheel-install", "sdist-install"}:
            rebuilt = scopes["sdist-rebuild"]
            self.assertEqual(set(rebuilt["before"]["entries"]), {"."})
            self.assertIn("package-1-py3-none-any.whl", rebuilt["after"]["entries"])
            self.assertTrue(scopes["wheel-package"]["unchanged"])
            self.assertTrue(scopes["sdist-package"]["unchanged"])
        if stage in {"wheel-install", "sdist-install"}:
            label = stage.split("-", 1)[0]
            installed = scopes[label + "-install-input"]
            self.assertIn("venv-created", installed["before"]["entries"])
            self.assertNotIn("installed-partial", installed["before"]["entries"])
            self.assertIn("installed-partial", installed["after"]["entries"])
            self.assertEqual(scopes[label + "-installation"]["before"]["status"], "missing")
            self.assertEqual(scopes[label + "-installation"]["after"]["status"], "complete")
        if final_interrupt:
            self.assertEqual(scopes["snapshot-preparation"]["after"]["status"], "failed")
            self.assertEqual(scopes["sdist-installed"]["after"]["status"], "complete")
            self.assertEqual(scopes["sdist-application"]["after"]["status"], "complete")
        if stage is not None and stage != "source-copy":
            self.assertEqual((output / "logs" / (stage + ".stdout")).read_bytes(), (stage + " stdout\n").encode())
            self.assertIn(str(sentinel), report["error"])
        return report

    def test_copy_source_build_rebuild_and_install_failures_get_final_captures(self):
        for stage in ("source-copy", "source-commit", "builder-tools", "package-build", "sdist-wheel", "wheel-install", "sdist-install"):
            with self.subTest(stage=stage):
                original_base = self.base
                self.base = original_base / stage
                self.base.mkdir()
                try:
                    self.exercise_main(stage)
                finally:
                    self.base = original_base

    def test_build_and_install_interruptions_keep_primary_and_final_captures(self):
        for stage in ("package-build", "wheel-install"):
            with self.subTest(stage=stage):
                original_base = self.base
                self.base = original_base / stage
                self.base.mkdir()
                try:
                    self.exercise_main(stage, interrupt=True)
                finally:
                    self.base = original_base

    def test_first_final_capture_interruption_is_promoted_after_all_captures(self):
        self.exercise_main(final_interrupt=True)

    def test_final_write_error_or_interrupt_corrects_already_flushed_success(self):
        for error_type in (OSError, KeyboardInterrupt):
            with self.subTest(error_type=error_type.__name__):
                original_base = self.base
                self.base = original_base / error_type.__name__
                self.base.mkdir()
                error = error_type("failure after success bytes reached disk")
                try:
                    report = self.exercise_main(final_save_error=error)
                    self.assertIn(str(error), report["error"])
                    self.assertTrue(any("final receipt save failed" in item for item in report["failures"]))
                finally:
                    self.base = original_base

    def test_driver_mismatch_prevents_every_child_command_and_retains_observations(self):
        self.exercise_main(identity_fault="mismatch")

    def test_original_driver_metadata_and_example_changes_fail_final_identity(self):
        for fault in ("driver-change", "metadata-change", "example-change"):
            with self.subTest(fault=fault):
                original_base = self.base
                self.base = original_base / fault
                self.base.mkdir()
                try:
                    report = self.exercise_main(identity_fault=fault)
                    self.assertTrue(any("immutable identity changed" in error for error in report["failures"]))
                finally:
                    self.base = original_base


if __name__ == "__main__":
    unittest.main()

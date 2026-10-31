"""Owned-process regressions. Execute ONLY in the parent's frozen Kyoto job.

The small subprocess programs below are failure fixtures, not engine executions
or sources of expected observation records. No test uses a detached group.
"""
from __future__ import annotations

import ctypes
import errno
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import observation_process as owned


class BackendTests(unittest.TestCase):
    def test_linux_stat_parses_parentheses_newlines_and_non_utf8_comm(self):
        fields = [b'S', b'17', b'23'] + [b'0'] * 16 + [b'987654']
        fields[17] = b'1'  # num_threads, stat field 20
        raw = b'123 (compiler (x)\n\xff) ' + b' '.join(fields)
        with patch.object(Path, 'read_bytes', return_value=raw):
            row = owned._LinuxProcesses().read(123)
        self.assertEqual(row, owned._Process(123, 17, 23, (987654,), False))
        with patch.object(Path, 'read_bytes', return_value=raw.replace(b') S ', b') Z ')):
            self.assertTrue(owned._LinuxProcesses().read(123).zombie)
        fields[0] = b'Z'; fields[17] = b'2'
        with patch.object(Path, 'read_bytes', return_value=b'123 (threaded) ' + b' '.join(fields)):
            self.assertFalse(owned._LinuxProcesses().read(123).zombie,
                             'zombie group leader can still have live writer threads')

    def test_linux_missing_is_gone_but_denied_or_truncated_is_not(self):
        with patch.object(Path, 'read_bytes', side_effect=FileNotFoundError):
            self.assertIsNone(owned._LinuxProcesses().read(123))
        with patch.object(Path, 'read_bytes', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                owned._LinuxProcesses().read(123)
        with patch.object(Path, 'read_bytes', return_value=b'123 (short) S 1 2'):
            with self.assertRaises(owned.OwnershipError):
                owned._LinuxProcesses().read(123)

    def test_darwin_start_microseconds_and_zombie_are_preserved(self):
        def read(pid, flavor, arg, buffer, size):
            self.assertEqual((flavor, arg, size), (3, 1, 136))
            info = ctypes.cast(buffer, ctypes.POINTER(owned._BSDInfo)).contents
            info.pid = pid; info.ppid = 17; info.pgid = 23
            info.start_sec = 100; info.start_usec = 456789; info.status = 5
            return size
        backend = owned._DarwinProcesses.__new__(owned._DarwinProcesses)
        backend.lib = SimpleNamespace(proc_pidinfo=read)
        self.assertEqual(backend.read(123), owned._Process(123, 17, 23, (100, 456789), True))

    def test_darwin_truncation_permission_and_disappearance_are_distinct(self):
        backend = owned._DarwinProcesses.__new__(owned._DarwinProcesses)
        backend.lib = SimpleNamespace(proc_pidinfo=lambda *args: 1)
        with self.assertRaises(owned.OwnershipError):
            backend.read(123)
        for error in (errno.EPERM, errno.ESRCH):
            def read(*args):
                ctypes.set_errno(error)
                return 0
            backend.lib = SimpleNamespace(proc_pidinfo=read)
            if error == errno.ESRCH:
                self.assertIsNone(backend.read(123))
            else:
                with self.assertRaises(PermissionError):
                    backend.read(123)


class _FakeTable:
    name = 'test-only-table'

    def __init__(self):
        self.root = owned._Process(900001, os.getpid(), os.getpgrp(), (100,), False)
        self.child = owned._Process(900002, self.root.pid, os.getpgrp(), (110,), False)
        self.rows = {p.pid: p for p in (self.root, self.child)}
        self.denied = set()

    def pids(self):
        return list(self.rows)

    def read(self, pid):
        if pid in self.denied:
            raise PermissionError('injected unreadable owned identity')
        return self.rows.get(pid)


class SimulatedOwnershipTests(unittest.TestCase):
    def run_fake(self, table, *, on_sleep=None, on_signal=None, timeout=0.03):
        clock = [0.0]
        signals = []
        process = SimpleNamespace(pid=table.root.pid, args=['fixture'], returncode=None)
        def poll():
            if table.root.pid not in table.rows:
                process.returncode = 0
            return process.returncode
        process.poll = poll
        def sleep(seconds):
            clock[0] += seconds
            if on_sleep:
                on_sleep(table, clock[0])
        def send(pid, sig):
            signals.append((pid, sig))
            if on_signal:
                on_signal(table, pid, sig)
            else:
                table.rows.pop(pid, None)
        entry = {}
        with patch.object(owned, '_process_table', return_value=table), \
             patch.object(owned.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(owned.time, 'sleep', side_effect=sleep), \
             patch.object(owned.os, 'kill', side_effect=send):
            try:
                owned.wait_owned(process, timeout, entry, poll_interval=0.01,
                                 term_grace=0.02, kill_grace=0.03)
            except BaseException as error:
                return entry, signals, clock[0], error
        self.fail('fixture should fail instead of qualifying')

    def test_reused_pid_neither_receives_signal_nor_seeds_foreign_descendants(self):
        table = _FakeTable()
        def replace(table, elapsed):
            if elapsed < 0.02:
                table.rows[table.child.pid] = table.child._replace(ppid=1, start=(900,))
                table.rows[900003] = owned._Process(900003, table.child.pid, os.getpgrp(), (901,), False)
        entry, signals, _, error = self.run_fake(table, on_sleep=replace)
        self.assertIsInstance(error, subprocess.TimeoutExpired)
        self.assertEqual({pid for pid, _ in signals}, {table.root.pid})
        self.assertEqual(entry['process_ownership']['observed'][1]['state'], 'pid_reused')
        self.assertTrue(entry['raw_logs_stable'])
        self.assertIn(900003, table.rows)

    def test_observed_orphan_remains_owned_after_reparenting(self):
        table = _FakeTable()
        def orphan(table, elapsed):
            if table.child.pid in table.rows:
                table.rows[table.child.pid] = table.child._replace(ppid=1)
        entry, signals, _, error = self.run_fake(table, on_sleep=orphan)
        self.assertIsInstance(error, subprocess.TimeoutExpired)
        self.assertEqual({pid for pid, _ in signals}, {table.root.pid, table.child.pid})
        self.assertTrue(entry['process_tree_termination_confirmed'])

    def test_unreadable_owned_child_fails_closed_but_does_not_block_root_cleanup(self):
        table = _FakeTable()
        def deny(table, elapsed):
            table.denied.add(table.child.pid)
        entry, signals, elapsed, error = self.run_fake(table, on_sleep=deny)
        self.assertIsInstance(error, PermissionError)
        self.assertIn((table.root.pid, signal.SIGTERM), signals)
        self.assertNotIn(table.child.pid, {pid for pid, _ in signals})
        self.assertFalse(entry['raw_logs_stable'])
        self.assertFalse(entry['process_tree_termination_confirmed'])
        self.assertIn('cleanup_error', entry)
        self.assertLessEqual(elapsed, 0.07)
        self.assertTrue(entry['process_ownership']['errors'])

    def test_successful_signal_delivery_is_not_termination_confirmation(self):
        entry, signals, elapsed, error = self.run_fake(_FakeTable(), on_signal=lambda *args: None)
        self.assertIsInstance(error, subprocess.TimeoutExpired)
        self.assertEqual({sig for _, sig in signals}, {signal.SIGTERM, signal.SIGKILL})
        self.assertFalse(entry['process_tree_termination_confirmed'])
        self.assertFalse(entry['raw_logs_stable'])
        self.assertIsNone(entry['exit_code'])
        self.assertLessEqual(elapsed, 0.09)
        retained = json.loads(json.dumps(entry))
        self.assertTrue(retained['timed_out'])
        self.assertIn('cleanup_error', retained)
        self.assertIn('TimeoutExpired', retained['wait_error'])

    def test_cancellation_is_preserved_when_cleanup_also_fails(self):
        failure = KeyboardInterrupt('primary interruption')
        interrupted = [False]
        def cancel(table, elapsed):
            if not interrupted[0]:
                interrupted[0] = True
                table.denied.add(table.child.pid)
                raise failure
        entry, signals, elapsed, error = self.run_fake(_FakeTable(), on_sleep=cancel)
        self.assertIs(error, failure)
        self.assertFalse(entry['raw_logs_stable'])
        self.assertIn('cleanup_error', entry)
        self.assertIn('primary interruption', entry['wait_error'])
        self.assertTrue(entry['process_ownership']['errors'])
        self.assertLessEqual(elapsed, 0.07)

    def test_cleanup_discovers_new_child_without_restarting_deadlines(self):
        table = _FakeTable()
        grandchild = owned._Process(900003, table.child.pid, os.getpgrp(), (120,), False)
        def fork_on_term(table, pid, sig):
            if pid == table.child.pid and sig == signal.SIGTERM:
                table.rows[grandchild.pid] = grandchild
            elif sig == signal.SIGKILL or pid == table.root.pid:
                table.rows.pop(pid, None)
        entry, signals, elapsed, error = self.run_fake(table, on_signal=fork_on_term)
        self.assertIsInstance(error, subprocess.TimeoutExpired)
        self.assertIn((grandchild.pid, signal.SIGTERM), signals)
        self.assertIn((grandchild.pid, signal.SIGKILL), signals)
        self.assertTrue(entry['raw_logs_stable'])
        self.assertLessEqual(elapsed, 0.09)

    def test_parent_lost_between_reads_cannot_hide_a_sampled_live_grandchild(self):
        class RacyTable(_FakeTable):
            def pids(self):
                self.child_reads = 0
                return super().pids()
            def read(self, pid):
                if pid == self.child.pid and 900003 in self.rows:
                    self.child_reads += 1
                    if self.child_reads == 2:
                        self.rows.pop(pid, None)
                        self.rows[900003] = self.rows[900003]._replace(ppid=1)
                return super().read(pid)
        table = RacyTable()
        def fork(table, elapsed):
            if elapsed == 0.01:
                table.rows[900003] = owned._Process(900003, table.child.pid, os.getpgrp(), (120,), False)
        entry, signals, _, error = self.run_fake(table, on_sleep=fork)
        self.assertIsInstance(error, owned.OwnershipError)
        self.assertIn('lost its owned parent', str(error))
        self.assertFalse(entry['raw_logs_stable'])
        self.assertFalse(entry['process_tree_termination_confirmed'])
        self.assertIn(900003, table.rows)
        self.assertEqual(entry['process_ownership']['unconfirmed_links'][0]['child']['pid'], 900003)
        self.assertNotIn(900003, {pid for pid, _ in signals}, 'unconfirmed identity is not a kill target')

    def test_transient_kill_inspection_failure_is_retried_inside_same_deadline(self):
        class TransientTable(_FakeTable):
            fail_next = None
            def read(self, pid):
                if pid == self.fail_next:
                    self.fail_next = None
                    raise OSError('transient fingerprint inspection failure')
                return super().read(pid)
        table = TransientTable(); injected = [False]
        send = owned._Owner.signal
        def inject(owner, sig, sent):
            if sig == signal.SIGKILL and not injected[0]:
                injected[0] = True
                table.fail_next = table.child.pid
            send(owner, sig, sent)
        def ignore_child_term(table, pid, sig):
            if pid == table.root.pid or sig == signal.SIGKILL:
                table.rows.pop(pid, None)
        with patch.object(owned._Owner, 'signal', new=inject):
            entry, signals, elapsed, error = self.run_fake(table, on_signal=ignore_child_term)
        self.assertIsInstance(error, subprocess.TimeoutExpired)
        self.assertIn((table.child.pid, signal.SIGKILL), signals)
        self.assertEqual(table.rows, {})
        self.assertTrue(entry['process_ownership']['errors'])
        self.assertLessEqual(elapsed, 0.09)


@unittest.skipUnless(sys.platform == 'darwin' or sys.platform.startswith('linux'),
                     'ownership backends support macOS and Linux')
class RealOwnedProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='observation-process-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def spawn(self, source, label):
        out = (self.root / (label + '.stdout.bin')).open('xb')
        err = (self.root / (label + '.stderr.bin')).open('xb')
        self.addCleanup(out.close); self.addCleanup(err.close)
        process = subprocess.Popen([sys.executable, '-B', '-c', source], stdout=out, stderr=err)
        def reap():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
        self.addCleanup(reap)
        return process, Path(out.name), Path(err.name)

    def tree_source(self, exit_leader=False):
        # Each descendant has a finite lifetime even if the code under test is
        # broken. Readiness files ensure SIG_IGN is installed before leader exit.
        grandchild = """import json,os,pathlib,signal,time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
root = pathlib.Path(ROOT)
(root / 'grandchild.json').write_text(json.dumps([os.getpid(), os.getpgrp()]))
deadline = time.monotonic() + 8
while time.monotonic() < deadline:
    os.write(1, b'\\xff\\x00pulse\\n'); os.write(2, b'\\xfeerr\\x00\\n'); time.sleep(0.03)
""".replace('ROOT', repr(str(self.root)))
        child = """import json,os,pathlib,signal,subprocess,sys,time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
root = pathlib.Path(ROOT)
subprocess.Popen([sys.executable, '-B', '-c', GRANDCHILD])
(root / 'child.json').write_text(json.dumps([os.getpid(), os.getpgrp()]))
time.sleep(8)
""".replace('ROOT', repr(str(self.root))).replace('GRANDCHILD', repr(grandchild))
        finish = """deadline = time.monotonic() + 5
while not (root / 'tree-observed').exists():
    if time.monotonic() > deadline: raise RuntimeError('ownership handshake timeout')
    time.sleep(0.01)
""" if exit_leader else 'time.sleep(8)\n'
        return """import json,os,pathlib,subprocess,sys,time
root = pathlib.Path(ROOT)
(root / 'leader.json').write_text(json.dumps([os.getpid(), os.getpgrp()]))
subprocess.Popen([sys.executable, '-B', '-c', CHILD])
deadline = time.monotonic() + 5
while not (root / 'grandchild.json').exists():
    if time.monotonic() > deadline: raise RuntimeError('fixture readiness timeout')
    time.sleep(0.01)
FINISH
""".replace('ROOT', repr(str(self.root))).replace('CHILD', repr(child)).replace(
            'FINISH', finish)

    def test_timeout_preserves_pgid_kills_orphans_and_leaves_same_group_sibling_alive(self):
        foreign, _, _ = self.spawn('import time; time.sleep(10)', 'foreign')
        process, out, err = self.spawn(self.tree_source(), 'owned')
        entry = {}
        real_kill = os.kill
        with patch.object(owned.os, 'kill', wraps=real_kill) as calls:
            with self.assertRaises(subprocess.TimeoutExpired):
                owned.wait_owned(process, 2, entry, poll_interval=0.02,
                                 term_grace=0.15, kill_grace=2)
        ids = [json.loads((self.root / (name + '.json')).read_text())
               for name in ('leader', 'child', 'grandchild')]
        owned_pids = {pid for pid, _ in ids}
        self.assertEqual({pgid for _, pgid in ids}, {os.getpgrp()})
        self.assertEqual(os.getpgid(foreign.pid), os.getpgrp())
        self.assertIsNone(foreign.poll(), 'unrelated workload in the same PGID was killed')
        self.assertTrue(all(call.args[0] in owned_pids for call in calls.call_args_list))
        self.assertNotIn(os.getpid(), owned_pids)
        self.assertEqual(process.returncode, -signal.SIGTERM)
        killed = {r['pid'] for r in entry['process_ownership']['signals']
                  if r['signal'] == signal.SIGKILL and r['delivered']}
        self.assertEqual(killed, owned_pids - {process.pid})
        self.assertFalse(entry['owns_process_group'])
        self.assertTrue(entry['process_tree_termination_confirmed'])
        self.assertTrue(entry['raw_logs_stable'])
        before = (out.read_bytes(), err.read_bytes())
        self.assertIn(b'\xff\x00pulse\n', before[0]); self.assertIn(b'\xfeerr\x00\n', before[1])
        time.sleep(0.15)
        self.assertEqual((out.read_bytes(), err.read_bytes()), before)

    def test_normal_exit_returns_status_and_seals_binary_streams_without_signals(self):
        source = "import os,sys; os.write(1,b'\\xff\\x00out'); os.write(2,b'\\xfeerr\\x00'); sys.exit(7)"
        process, out, err = self.spawn(source, 'normal')
        entry = {}
        self.assertEqual(owned.wait_owned(process, 3, entry), 7)
        self.assertTrue(entry['raw_logs_stable'])
        self.assertEqual(entry['process_ownership']['signals'], [])
        self.assertEqual(out.read_bytes(), b'\xff\x00out')
        self.assertEqual(err.read_bytes(), b'\xfeerr\x00')

    def wait_for_darwin_zombie(self, process):
        # Establish the precondition through the raw zombie-inclusive syscall,
        # not backend.read(), so reverting its arg to zero cannot prevent this
        # regression from reaching wait_owned with an already-exited child.
        # Never poll/wait/communicate here: that would reap the evidence.
        backend = owned._DarwinProcesses()
        deadline = time.monotonic() + 3
        while True:
            info = owned._BSDInfo()
            ctypes.set_errno(0)
            used = backend.lib.proc_pidinfo(process.pid, 3, 1,
                                            ctypes.byref(info), ctypes.sizeof(info))
            self.assertEqual(used, ctypes.sizeof(info),
                             f'zombie-inclusive query failed: errno={ctypes.get_errno()}')
            self.assertEqual((info.pid, info.ppid, info.pgid),
                             (process.pid, os.getpid(), os.getpgrp()))
            self.assertIsNone(process.returncode, 'fixture must remain unreaped')
            if info.status == 5:  # SZOMB from the kernel, not an assumed sleep.
                return backend, info
            if time.monotonic() >= deadline:
                self.fail('fixture did not become an unreaped zombie')
            time.sleep(0.01)

    @unittest.skipUnless(sys.platform == 'darwin', 'Darwin libproc syscall control')
    def test_darwin_backend_zombie_lookup_flag_and_live_control(self):
        process, _, _ = self.spawn('import os; os._exit(0)', 'zombie-backend')
        backend, info = self.wait_for_darwin_zombie(process)
        excluded = owned._BSDInfo()
        ctypes.set_errno(0)
        self.assertEqual(backend.lib.proc_pidinfo(process.pid, 3, 0,
                                                 ctypes.byref(excluded), ctypes.sizeof(excluded)), 0)
        self.assertEqual(ctypes.get_errno(), errno.ESRCH,
                         'arg=0 must demonstrate the original zombie lookup failure')
        self.assertEqual(backend.read(process.pid), owned._Process(
            process.pid, os.getpid(), os.getpgrp(), (info.start_sec, info.start_usec), True))
        live = backend.read(os.getpid())
        self.assertIsNotNone(live)
        self.assertEqual((live.pid, live.ppid, live.pgid, live.zombie),
                         (os.getpid(), os.getppid(), os.getpgrp(), False))
        self.assertIsNone(process.returncode)
        self.assertEqual(process.wait(timeout=3), 0)

    @unittest.skipUnless(sys.platform == 'darwin', 'Darwin already-exited Popen regression')
    def test_darwin_already_exited_unreaped_child_binds_without_signals(self):
        for code in (0, 7):
            with self.subTest(exit_code=code):
                source = ("import os; os.write(1,b'\\xff\\x00out'); "
                          "os.write(2,b'\\xfeerr\\x00'); os._exit(" + str(code) + ")")
                process, out, err = self.spawn(source, 'already-exited-' + str(code))
                _, info = self.wait_for_darwin_zombie(process)
                entry = {}
                with patch.object(owned.os, 'kill', wraps=os.kill) as calls:
                    self.assertEqual(owned.wait_owned(process, 3, entry), code)
                calls.assert_not_called()
                self.assertEqual(process.returncode, code)
                self.assertEqual(entry['exit_code'], code)
                self.assertEqual(entry['inherited_process_group'], os.getpgrp())
                self.assertFalse(entry['owns_process_group'])
                self.assertFalse(entry['timed_out'])
                self.assertTrue(entry['process_tree_termination_confirmed'])
                self.assertTrue(entry['raw_logs_stable'])
                self.assertNotIn('wait_error', entry)
                report = entry['process_ownership']
                row, = report['observed']
                self.assertEqual((row['pid'], row['parent_pid'], row['process_group']),
                                 (process.pid, os.getpid(), os.getpgrp()))
                self.assertEqual(row['start'], [info.start_sec, info.start_usec])
                self.assertTrue(row['zombie'])
                self.assertEqual(row['state'], 'zombie')
                self.assertIsNone(row['ancestor'])
                self.assertEqual(report['signals'], [])
                self.assertEqual(report['errors'], [])
                self.assertEqual(out.read_bytes(), b'\xff\x00out')
                self.assertEqual(err.read_bytes(), b'\xfeerr\x00')

    def test_successful_leader_cannot_leave_writers_with_unlimited_command_timeout(self):
        process, out, err = self.spawn(self.tree_source(exit_leader=True), 'early-exit')
        entry = {}; started = time.monotonic()
        scan = owned._Owner.scan
        def acknowledge_tree(owner):
            scan(owner)
            if len(owner.report['observed']) == 3:
                (self.root / 'tree-observed').touch()
        with patch.object(owned._Owner, 'scan', new=acknowledge_tree):
            with self.assertRaises(owned.DescendantsRemaining):
                owned.wait_owned(process, None, entry, poll_interval=0.02,
                                 term_grace=0.15, kill_grace=2)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(entry['exit_code'], 0)
        self.assertFalse(entry['timed_out'])
        self.assertTrue(entry['raw_logs_stable'])
        before = (out.read_bytes(), err.read_bytes())
        time.sleep(0.15)
        self.assertEqual((out.read_bytes(), err.read_bytes()), before)

    def test_keyboard_interrupt_and_system_exit_clean_up_then_preserve_original(self):
        real_sleep = time.sleep
        for index, failure in enumerate((KeyboardInterrupt(), SystemExit(77))):
            with self.subTest(failure=type(failure).__name__):
                process, _, _ = self.spawn('import time; time.sleep(10)', 'cancel-' + str(index))
                interrupted = [False]
                def sleep(seconds):
                    if not interrupted[0]:
                        interrupted[0] = True
                        raise failure
                    real_sleep(seconds)
                entry = {}
                with patch.object(owned.time, 'sleep', side_effect=sleep):
                    with self.assertRaises(type(failure)) as caught:
                        owned.wait_owned(process, None, entry, poll_interval=0.02,
                                         term_grace=0.1, kill_grace=2)
                self.assertIs(caught.exception, failure)
                self.assertTrue(entry['raw_logs_stable'])
                self.assertIsNotNone(process.returncode)

    def test_initialization_failure_reaps_direct_child_and_retains_unconfirmed_metadata(self):
        process, out, err = self.spawn('import time; time.sleep(10)', 'init-error')
        entry = {}; failure = OSError('injected process table failure')
        with patch.object(owned, '_process_table', side_effect=failure):
            with self.assertRaises(OSError) as caught:
                owned.wait_owned(process, 1, entry)
        self.assertIs(caught.exception, failure)
        self.assertIsNotNone(process.returncode)
        self.assertFalse(entry['raw_logs_stable'])
        self.assertFalse(entry['process_tree_termination_confirmed'])
        self.assertTrue(out.exists()); self.assertTrue(err.exists())
        self.assertIn('cleanup_error', json.loads(json.dumps(entry)))

    def test_cancellation_at_first_clock_read_still_cleans_up_started_child(self):
        process, _, _ = self.spawn('import time; time.sleep(10)', 'early-cancel')
        entry = {}; failure = KeyboardInterrupt('first initialization call')
        with patch.object(owned.time, 'monotonic', side_effect=failure):
            with self.assertRaises(KeyboardInterrupt) as caught:
                owned.wait_owned(process, 1, entry)
        self.assertIs(caught.exception, failure)
        self.assertIsNotNone(process.returncode)
        self.assertFalse(entry['raw_logs_stable'])
        self.assertIn('cleanup_error', entry)

    def test_driver_retains_failed_receipt_and_unsealed_paths_when_tracking_fails(self):
        # This integration assertion deliberately requires the parent to wire
        # Attempt.run and its unsealed-log finalizer to wait_owned before running.
        path = Path(__file__).with_name('check-native-observation-consumers.py')
        spec = importlib.util.spec_from_file_location('process_receipt_driver_test', path)
        driver = importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)
        attempt = driver.Attempt.__new__(driver.Attempt)
        attempt.root = self.root; attempt.work = self.root; attempt.output = self.root / 'attempt'
        attempt.output.mkdir(); attempt.env = dict(os.environ)
        attempt.timeout = 1; attempt.initial = None; attempt.report = {'commands': []}
        failure = OSError('injected ownership failure')
        load_helper = driver.helper
        def failing_backend():
            raise failure
        def helper(name):
            module = load_helper(name)
            if name == 'observation_process':
                module._process_table = failing_backend
            return module
        # Attempt uses an isolated dynamic module, so inject into that instance
        # rather than the separately imported unit-test module.
        with patch.object(driver, 'helper', side_effect=helper):
            with self.assertRaises(OSError):
                attempt.run('unconfirmed', [sys.executable, '-B', '-c', 'import time; time.sleep(10)'],
                            accept_failure=True)
        entry, = attempt.report['commands']
        self.assertFalse(entry['raw_logs_stable'])
        self.assertFalse(entry['process_tree_termination_confirmed'])
        self.assertIn('cleanup_error', entry)
        for stream in ('stdout', 'stderr'):
            self.assertTrue((attempt.output / entry[stream]['path']).exists())
            self.assertNotIn('sha256', entry[stream], 'unconfirmed writers cannot have sealed hashes')
        finished, = attempt.output.glob('commands/*/finished.json')
        retained = json.loads(finished.read_text())
        self.assertFalse(retained['raw_logs_stable'])
        self.assertIn('injected ownership failure', retained['error'])


if __name__ == '__main__':
    unittest.main()

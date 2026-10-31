"""Wait for an owned command without leaving Kyoto's process group.

Call wait_owned immediately after Popen, with stdout/stderr already redirected
to exclusive raw files. Do not poll/reap the child first. This is sampled process
ownership, not containment: a double fork between samples can hide ancestry.
No process-group signals, sessions, subprocess probes, or engine calls are used.
"""
from __future__ import annotations

import ctypes
import errno
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import NamedTuple


class OwnershipError(RuntimeError):
    """Ownership or cleanup could not be confirmed; keep the failed receipt."""


class DescendantsRemaining(OwnershipError):
    """The command exited but observed descendants outlived its drain grace."""


class _Process(NamedTuple):
    pid: int
    ppid: int
    pgid: int
    start: tuple[int, ...]
    zombie: bool

    @property
    def key(self):
        return self.pid, self.start

    def receipt(self):
        return dict(pid=self.pid, parent_pid=self.ppid, process_group=self.pgid,
                    start=list(self.start), zombie=self.zombie)


class _LinuxProcesses:
    name = 'linux-proc-stat-startticks'

    def pids(self):
        return [int(name) for name in os.listdir('/proc') if name.isdecimal()]

    def read(self, pid):
        try:
            raw = Path(f'/proc/{pid}/stat').read_bytes()
        except (FileNotFoundError, ProcessLookupError):
            return None
        # comm can contain spaces, parentheses and newlines; fields after its
        # final ')' start at field 3. starttime is field 22, in kernel ticks.
        fields = raw[raw.rindex(b')') + 2:].split()
        if int(raw.split(b' ', 1)[0]) != pid or len(fields) < 20:
            raise OwnershipError('invalid /proc stat identity')
        # A Linux thread-group leader can be a zombie while sibling threads
        # still run and hold the shared descriptors (e.g. pthread_exit in main).
        terminal = fields[0] in (b'Z', b'X', b'x') and int(fields[17]) <= 1
        return _Process(pid, int(fields[1]), int(fields[2]), (int(fields[19]),), terminal)


class _BSDInfo(ctypes.Structure):
    # Darwin <sys/proc_info.h>, struct proc_bsdinfo (PROC_PIDTBSDINFO).
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid',
        'rgid', 'svuid', 'svgid', 'reserved')]
    _fields_ += [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in (
        'nfiles', 'pgid', 'pjobc', 'tdev', 'tpgid')]
    _fields_ += [('nice', ctypes.c_int32), ('start_sec', ctypes.c_uint64),
                ('start_usec', ctypes.c_uint64)]


class _DarwinProcesses:
    name = 'darwin-libproc-start-timeval'

    def __init__(self):
        self.lib = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        self.lib.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32,
                                         ctypes.c_void_p, ctypes.c_int]
        self.lib.proc_listpids.restype = ctypes.c_int
        self.lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                        ctypes.c_void_p, ctypes.c_int]
        self.lib.proc_pidinfo.restype = ctypes.c_int

    def pids(self):
        # PROC_ALL_PIDS=1. Leave spare capacity for concurrent forks, and retry
        # a full result rather than silently treating a truncated table as all.
        needed = self.lib.proc_listpids(1, 0, None, 0)
        if needed <= 0:
            raise OwnershipError('libproc could not size the process table')
        capacity = needed // ctypes.sizeof(ctypes.c_int) + 256
        for _ in range(4):
            buffer = (ctypes.c_int * capacity)()
            size = ctypes.sizeof(buffer)
            used = self.lib.proc_listpids(1, 0, buffer, size)
            if used <= 0 or used % ctypes.sizeof(ctypes.c_int):
                raise OwnershipError('libproc could not enumerate processes')
            if used < size:
                return [pid for pid in buffer[:used // ctypes.sizeof(ctypes.c_int)] if pid > 0]
            capacity *= 2
        raise OwnershipError('process table kept growing during enumeration')

    def read(self, pid):
        info = _BSDInfo()
        ctypes.set_errno(0)
        # PROC_PIDTBSDINFO=3: nonzero arg includes unreaped zombies in XNU's
        # lookup. A fast Popen child may already be one before _Owner binds it.
        used = self.lib.proc_pidinfo(pid, 3, 1, ctypes.byref(info), ctypes.sizeof(info))
        if used <= 0:
            error = ctypes.get_errno()
            if error in (errno.ESRCH, errno.ENOENT):
                return None
            raise OSError(error, 'proc_pidinfo failed', str(pid))
        if used != ctypes.sizeof(info) or info.pid != pid:
            raise OwnershipError('libproc returned incomplete process identity')
        return _Process(pid, info.ppid, info.pgid, (info.start_sec, info.start_usec),
                        info.status == 5)  # SZOMB; zombies cannot retain log FDs.


def _process_table():
    if sys.platform == 'darwin':
        return _DarwinProcesses()
    if sys.platform.startswith('linux'):
        return _LinuxProcesses()
    raise OwnershipError('owned process waiting supports only macOS and Linux')


class _Owner:
    def __init__(self, process, entry):
        self.process = process
        self.entry = entry
        self.table = _process_table()
        self.known = {}
        self.live = {}
        self.rows = {}
        self.report = entry['process_ownership']
        self.report['backend'] = self.table.name
        root = self.table.read(process.pid)
        if root is None or root.ppid != os.getpid():
            raise OwnershipError('Popen child disappeared or was already reaped')
        self.remember(root, None)
        self.root_bound = True
        if root.pgid != os.getpgrp():
            raise OwnershipError('command did not inherit the Kyoto process group')

    def remember(self, process, parent):
        self.known[process.key] = process
        row = process.receipt()
        row.update(ancestor=None if parent is None else parent.receipt(), state='observed')
        self.rows[process.key] = row
        self.report['observed'].append(row)

    def uncertain(self, child, parent, message):
        self.report.setdefault('unconfirmed_links', []).append(
            dict(child=child.receipt(), parent=parent.receipt(), error=message))
        raise OwnershipError(message)

    def scan(self):
        table = {}
        unreadable = set()
        for pid in self.table.pids():
            try:
                current = self.table.read(pid)
            except PermissionError:
                # Foreign users' processes may be inaccessible. An inaccessible
                # known process is retried below and fails closed.
                unreadable.add(pid)
                continue
            if current is not None:
                table[pid] = current
        sampled_owned = {pid: value for pid, value in table.items() if value.key in self.known}
        sampled_children = [value for value in table.values()
                            if value.key not in self.rows and value.ppid in sampled_owned]
        # A process-list snapshot is not atomic. Re-read every known identity,
        # including adopted descendants absent from the enumerated PID list.
        for previous in list(self.known.values()):
            try:
                current = self.table.read(previous.pid)
            except BaseException:
                self.rows[previous.key]['state'] = 'unknown'
                raise
            if current is not None:
                table[current.pid] = current
            else:
                table.pop(previous.pid, None)
        reachable = {pid: value for pid, value in table.items() if value.key in self.known}
        # Preserve evidence of a link seen earlier in this very scan. Overwriting
        # a now-gone parent must not silently turn its sampled child into an
        # unrelated process and subsequently authorize immutable log hashes.
        for candidate in sampled_children:
            parent = reachable.get(candidate.ppid)
            if parent is None or parent.key != sampled_owned[candidate.ppid].key:
                fresh = self.table.read(candidate.pid)
                if fresh is not None and fresh.key == candidate.key:
                    self.uncertain(fresh, sampled_owned[candidate.ppid],
                                   'sampled child lost its owned parent during the scan')
        candidates = list(table.values())
        while True:
            added = False
            for candidate in candidates:
                if candidate.key in self.rows or candidate.ppid not in reachable:
                    continue
                parent = reachable[candidate.ppid]
                # Revalidate both identities before recording a newly observed
                # link. PGID/name alone never establishes ownership.
                fresh_parent = self.table.read(parent.pid)
                fresh = self.table.read(candidate.pid)
                if fresh is None or fresh.key != candidate.key:
                    continue
                if fresh_parent is None or fresh_parent.key != parent.key:
                    self.uncertain(fresh, parent, 'observed ancestry vanished before identity confirmation')
                if fresh.ppid != parent.pid or fresh.start < parent.start:
                    self.uncertain(fresh, parent, 'observed ancestry changed before identity confirmation')
                if fresh.pid in (os.getpid(), os.getppid()) or fresh.pid <= 1:
                    raise OwnershipError('refusing ownership of the caller or supervisor')
                self.remember(fresh, parent)
                table[fresh.pid] = fresh
                reachable[fresh.pid] = fresh
                added = True
            if not added:
                break
        self.live = {}
        for key, previous in list(self.known.items()):
            current = table.get(previous.pid)
            state = ('gone' if current is None else 'pid_reused' if current.key != key
                     else 'zombie' if current.zombie else 'live')
            self.rows[key]['state'] = state
            if state == 'live':
                self.live[key] = current
                if current.pgid != self.entry['inherited_process_group']:
                    raise OwnershipError('owned descendant changed process group')
            else:
                # Keep the evidence row but retire terminated identities from
                # future probes; a long compiler run must not re-probe every
                # exited compiler PID on every sample.
                del self.known[key]
        self.report['samples'] += 1
        # Unknown inaccessible processes are not kill targets. Record this
        # visibility limit without accumulating one row per sample.
        self.report['unreadable_pids'] = sorted(unreadable)

    def signal(self, sig, sent):
        # Children first; keep every fingerprint after the leader has exited.
        for key, previous in reversed(list(self.known.items())):
            if (key, sig) in sent:
                continue
            try:
                current = self.table.read(previous.pid)
                if current is None or current.key != key or current.zombie:
                    continue
                if current.pid <= 1 or current.pid in (os.getpid(), os.getppid()):
                    raise OwnershipError('refusing to signal the caller or supervisor')
                row = next((item for item in self.report['signals']
                            if item['pid'] == current.pid and item['start'] == list(current.start)
                            and item['signal'] == sig), None)
                if row is None:
                    row = dict(pid=current.pid, start=list(current.start), signal=sig, delivered=False)
                    self.report['signals'].append(row)
                row['attempts'] = row.get('attempts', 0) + 1
                try:
                    os.kill(current.pid, sig)
                except ProcessLookupError:
                    row['already_exited'] = True
                except BaseException as error:
                    row['error'] = repr(error)
                    raise
                else:
                    row['delivered'] = True
                sent.add((key, sig))
            except BaseException as error:
                self.rows[key]['state'] = 'unknown'
                _record_error(self.report, error)
                # Unsuccessful inspection/delivery remains eligible for retry
                # within this phase's original deadline. Receipt rows are reused.


def _record_error(report, error):
    message = repr(error)
    if message not in report['errors']:
        if len(report['errors']) < 64:
            report['errors'].append(message)
        else:
            report['additional_errors'] = report.get('additional_errors', 0) + 1


def _cleanup(owner, process, entry, interval, term_grace, kill_grace):
    report = entry['process_ownership']
    sent = set()
    # Each phase has one deadline: repeated forks/errors cannot reset it.
    for sig, grace in ((signal.SIGTERM, term_grace), (signal.SIGKILL, kill_grace)):
        deadline = time.monotonic() + grace
        while True:
            scanned = False
            try:
                owner.scan()
                scanned = True
            except BaseException as error:
                _record_error(report, error)
            try:
                owner.signal(sig, sent)
            except BaseException as error:
                _record_error(report, error)
            try:
                process.poll()  # Reap only this Popen child, never another job.
                if scanned and not owner.live and process.returncode is not None:
                    return not report['errors']
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(interval, remaining))
            except BaseException as error:
                _record_error(report, error)
                if time.monotonic() >= deadline:
                    break
    return False


def wait_owned(process, timeout, entry, *, poll_interval=0.1, term_grace=1.0, kill_grace=3.0):
    """Return the exit code only after all observed owners of raw logs exit.

    Keep the streams open around this call. Any BaseException triggers bounded
    TERM/KILL cleanup and is re-raised; cleanup failure never replaces the original
    exception. entry is updated even on failure. Only raw_logs_stable=True permits
    final log hashes. False requires retaining paths/staging and a failed receipt.
    A zero command exit does not bypass live descendants or tracking failures.
    After leader exit, descendants have term_grace seconds to drain, even when
    timeout is None; remaining descendants then cause failure and cleanup.
    """
    owner = None
    report = None
    try:
        entry.update(process_id=process.pid, inherited_process_group=None,
                     owns_process_group=False, timed_out=False, raw_logs_stable=False,
                     process_tree_termination_confirmed=False)
        report = dict(schema='statelessness.owned-process.v1', backend=None,
                      samples=0, observed=[], signals=[], errors=[],
                      poll_interval=poll_interval, term_grace=term_grace, kill_grace=kill_grace,
                      limit='Sampled ancestry only; not containment against daemonization or PID-reuse races.')
        entry['process_ownership'] = report
        started = time.monotonic()
        entry['inherited_process_group'] = os.getpgrp()
        if any(not math.isfinite(v) or v <= 0 for v in (poll_interval, term_grace, kill_grace)):
            raise ValueError('sampling and cleanup intervals must be finite and positive')
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError('timeout must be finite and nonnegative, or None')
        # Keep a partially initialized tracker for cleanup if initial PGID or
        # identity validation fails after it has remembered the direct child.
        owner = _Owner.__new__(_Owner)
        owner.__init__(process, entry)
        leader_exited_at = None
        while True:
            owner.scan()
            code = process.poll()
            if code is not None and not owner.live:
                entry.update(exit_code=code, process_tree_termination_confirmed=True, raw_logs_stable=True)
                return code
            if code is not None:
                if leader_exited_at is None:
                    leader_exited_at = time.monotonic()
                elif time.monotonic() - leader_exited_at >= term_grace:
                    raise DescendantsRemaining('command exited with live owned descendants')
            remaining = None if timeout is None else timeout - (time.monotonic() - started)
            if remaining is not None and remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            time.sleep(poll_interval if remaining is None else min(poll_interval, remaining))
    except BaseException as error:
        if report is None:
            report = dict(schema='statelessness.owned-process.v1', backend=None,
                          samples=0, observed=[], signals=[], errors=[])
        entry['process_ownership'] = report
        entry.update(process_id=process.pid, owns_process_group=False)
        entry['timed_out'] = isinstance(error, subprocess.TimeoutExpired)
        entry['wait_error'] = repr(error)
        if not isinstance(error, (subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit,
                                  DescendantsRemaining)):
            _record_error(report, error)
        confirmed = False
        if owner is not None and getattr(owner, 'root_bound', False):
            try:
                confirmed = _cleanup(owner, process, entry, poll_interval, term_grace, kill_grace)
            except BaseException as cleanup_error:
                _record_error(report, cleanup_error)
        else:
            # No ancestry proof: only the still-owned, unreaped Popen child can
            # be targeted. Descendant cleanup remains explicitly unconfirmed.
            try:
                process.kill()
                process.wait(timeout=3)
            except BaseException as cleanup_error:
                _record_error(report, cleanup_error)
        entry.update(exit_code=process.returncode, process_tree_termination_confirmed=confirmed,
                     raw_logs_stable=confirmed)
        if not confirmed:
            entry['cleanup_error'] = 'Owned process cleanup unconfirmed; retain raw logs and staging.'
        raise

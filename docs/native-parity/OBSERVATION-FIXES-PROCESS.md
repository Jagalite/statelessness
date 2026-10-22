# Observation qualification process ownership

Source-only implementation, 2026-10-22, based on
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`. No build, test, compilation,
helper execution, or Kyoto submission was performed for this change. The parent
must freeze the integrated sources and run the shared Kyoto qualification.

## Problem and integration

Kyoto `scheduling.py` samples RSS and CPU by job PGID (`ps -axo pgid=,rss=` and
`pgid=,pcpu=`). A command started with `start_new_session=True` leaves that PGID,
so compilers and test children disappear from the job's accounting. Signaling
the inherited PGID would instead kill the job supervisor and unrelated commands.

[observation_process.py](../../scripts/observation_process.py) provides this API:

```python
from observation_process import wait_owned

with stdout.open('xb') as out, stderr.open('xb') as err:
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=out, stderr=err)
    entry['exit_code'] = wait_owned(process, timeout, entry)
```

Integrate immediately after `Popen`, before any `poll`, `wait`, or `communicate`.
Do not create a session or process group. Remove the old group-kill path from
`Attempt.run`; keep ownership failures exceptional even with
`accept_failure=True`. Both the outer Go command and `record_go_exec` use the
same path. The wrapper may use `timeout=None`, which disables only the running
command deadline. Descendants still get a bounded drain grace after leader exit
and bounded TERM/KILL cleanup on every caught `BaseException`.

The helper deliberately owns no files. The caller keeps exclusive raw log files
and its partial command record alive throughout waiting and cleanup. After return
or exception, inspect `entry['raw_logs_stable']`. Only true allows final immutable
raw-file hashes. On false, retain paths and staging, mark files unsealed, avoid
unbounded hashing of potentially growing files, and fail the receipt. Propagate
that condition to the overall attempt and nested Go receipts. Do not let a zero
leader exit or accepted nonzero exit override an ownership/cleanup failure.

Run [test_observation_process.py](../../scripts/test_observation_process.py) in the
parent's shared native regression job alongside `test_observation_receipts.py`.
Update the existing receipt test's obsolete `owns_process_group=True` assertion.
The new integration test requires failed command receipts to preserve raw paths
without `sha256` when cleanup is unconfirmed. Ensure both new Python files are
included in the sealed source manifest and staged `scripts` tree.

## Ownership and cleanup

The helper samples ancestry every 100 ms by default, using Linux `/proc/PID/stat`
start ticks or macOS `libproc` start seconds and microseconds via stdlib `ctypes`.
Darwin's `proc_bsdinfo` layout and API declarations were inspected in the installed
SDK's `sys/proc_info.h` and `libproc.h`; runtime validation remains pending.
PID plus start time identifies an observed process instance; PPID proves a
discovered link, while PGID checks accounting inheritance. Names and group
membership never authorize a kill.

Already discovered descendants stay owned after reparenting or leader exit.
New ancestry links and every signal target are revalidated. A replaced PID is
not signaled and cannot seed ownership of a foreign subtree. Only positive
individual PIDs are signaled; neither the caller nor its parent is a target.
Discovery continues during cleanup, with one monotonic deadline for TERM
(default one second) and another for KILL/confirmation (default three seconds).
New children and failures cannot restart those deadlines. Only the supplied
`Popen` child is reaped. Observed zombies are recorded separately: they cannot
retain open log descriptors and need not disappear before logs are stable. On
Linux, a zombie group leader with remaining threads is still treated as live.
Completed identities retain their receipt rows but leave the active probe set,
so repeated sampling does not repeatedly inspect every historical compiler PID.

Normal completion also waits for observed writers. Once the leader exits, its
descendants have the TERM grace interval to drain naturally. A surviving child
fails the command and triggers cleanup, even when the command timeout is `None`.
Timeout and cancellation preserve the original exception, while the mutable
entry retains cleanup errors and observed process identities. Inspection errors
are uncertainty, not evidence that a process exited. Successful signal delivery
alone never confirms termination. Failed inspection/delivery attempts retry within
the same phase deadline; errors and signal rows are deduplicated separately.
If a newly sampled child's parent disappears during identity verification, retain
that unconfirmed link, fail the attempt, and keep the logs unsealed. Setup calls
(including the first clock read) are inside the cancellation cleanup guard.

## Limits and evidence

This is sampled ownership of ordinary foreground build/test commands, not an OS
containment boundary. A fork and parent exit between samples can hide ancestry,
including non-malicious double-fork daemonization. Unknown inaccessible processes
cannot be proven foreign or owned; they are not targets. Successful cleanup
confirms the **observed** identities, not complete discovery of every possible
descendant. Fingerprint recheck followed by `kill` has a residual PID-reuse race;
portable stdlib code does not make these operations atomic. Linux tick precision
and macOS timeval precision are recorded rather than replaced by weak `ps`
second-resolution start timestamps. Descriptor passing to an unrelated process
is outside this contract.

Bounds apply to polling and escalation; the kernel can still stall an inspection
or signal syscall. Default supervisor SIGTERM and forced supervisor death do not
raise a Python `BaseException`; graceful cancellation requires the enclosing
driver's signal handling. Repeated interrupts during cleanup are retained as
uncertainty, and failed attempts preserve logs/staging for inspection.

The 19 authored tests cover both backend identity formats, same-PGID root/child/grandchild
and foreign sibling isolation, TERM-ignoring writers after leader exit, stable
binary logs, normal leader exit with unlimited running timeout, PID replacement,
reparenting, unreadable identities, successful-but-ineffective signal delivery,
bounded cleanup, initialization failures, KeyboardInterrupt/SystemExit, and
failed unsealed command receipts, ancestry loss between individual process reads,
transient KILL-phase inspection errors, and cancellation at the first setup call.
The early leader-exit fixture waits for an ownership handshake. These tests have
not been executed.

This process helper does not change N07 model semantics. The inspected
`src/observation.rs`, `src/monitor.rs`, `tests/observation.rs`, and monitor tests
retain the diagnostic/checked-authority distinction: observation does not run
reducers, checks, or effects; existing sealed tokens, periodic checks, and bounded
recorders remain in place. `CHECKED-RESULTS-AUDIT.md` describes an older snapshot
and is not evidence that those native APIs are still missing.

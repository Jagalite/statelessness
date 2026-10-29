# TypeScript post-r1 Q2 and executing-driver corrections

Scope: mutable standalone qualifier and owned documentation only, in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336`. Production runtime,
lifecycle tests, public exports and harness-owned files are unchanged by this
correction. Root owns commits and any future source seal.

The complete independent review
`docs/native-parity/N06-REVIEW-PYTHON-TYPESCRIPT-R2.md` was read. Its Q2 finding was
confirmed against actual current source, SHA-256
`8dffa9799a2014def5fa27c7c20bb235beed83609a3a1177da3baf49545ee797`.
Normal and nonzero leader exits bypassed group cleanup; its exception cleanup
could also probe/signal after poll had reaped the leader. The focused Python
runner's all-exit shape was inspected, but its post-reap numeric-PGID probing was
not copied. Local Python capability metadata reports waitid/WNOWAIT availability;
that is not execution of the revised driver or its process protocol.

## Revised source behavior

`qualify.py` observes the child with `waitid(P_PID, WEXITED|WNOHANG|WNOWAIT)`.
The leader's PID remains reserved throughout membership inspection and signalling.
No call to Popen.poll/wait reaps it early. SIGCHLD is set to default rather than
ignored or application-reaped, and SIGINT/SIGTERM handlers record requests without
raising during Popen handle assignment or cleanup.

Direct group inventory uses Darwin libproc `proc_listpids(PROC_PGRP_ONLY)`.
Other hosts, including Linux, fail closed before spawning a child: procfs
enumeration followed by getpgid can omit a newly forked survivor. This qualifier
does not claim Linux execution support. No auxiliary ps command is required.
Unsupported facilities,
incomplete inventory and ownership loss fail closed. Waitid verifies retained
ownership before group signals; a lost/reaped child prevents further signalling.

Every owned invocation, including successful/nonzero leaders, timeouts and
interruptions, passes through the same final cleanup. If the leader has exited
with remaining descendants, they get a one-second natural grace, then bounded
TERM (five seconds) and KILL (five seconds) escalation as needed. The leader is
reaped last with a bounded wait. No group query or signal occurs after that reap.
An exited leader with an empty member inventory cannot create new descendants;
reaping that last retained child completes group disappearance without querying
a potentially recyclable numerical PGID.

Observed descendants after leader exit remain a qualification failure even if
they disappear during the grace period. Receipts retain initial/remaining member
IDs, observed leader exit, signals, cleanup need, final verification and reap
status. The primary nonzero/timeout/interruption error is preserved separately
from cleanup errors. Raw stdout/stderr files stay open through cleanup and are
never replaced by a cleanup diagnostic. Existing source, installed and consumer
final captures still run after command failure.

JSON captures use a same-directory temporary file, flush/fsync and atomic
replacement, preserving the preceding complete receipt if replacement fails.
Final save failures retain the primary command error and append the persistence
failure, attempt a separate `receipt-write-failure.json`, and independently emit
the full failure status to Kyoto stderr. All diagnostic sinks can fail; no
durability claim is made for that case. The process still returns failure.
After final persistence, signal-handler restoration closes the recording window;
the recorded signal list is reconciled again. A signal recorded during final
save/restoration forces failure and a best-effort corrected receipt.

The executing driver's raw `__file__`, `sys.argv`, `sys.orig_argv` and initial
cwd are retained. Before any Path/abspath normalization, that raw file spelling
must be absolute with no empty, dot or dot-dot components. This prevents lexical
normalization from substituting a different file when a symlink precedes `..`.
Before any child command, its canonical path must equal the
exact sealed `source/validation/native-parity/n06/typescript/qualify.py` member.
Regular type, complete permission mode, size and SHA-256 must match that unique
manifest entry, without symlinks or path aliases. The final capture repeats the
binding and compares its observed identity with the preflight identity. Running
another outer driver against sealed source is rejected, including an identical
copy at another path. These are file identity checks, not an in-memory code
attestation against hostile mutation during interpreter loading.

## Immutable r1 boundary

- Frozen source manifest SHA-256:
  `d5fa4f4fe098aaca7389518f542e859e578257777579ee088868255c8af21c41`.
- Frozen runner SHA-256:
  `8dffa9799a2014def5fa27c7c20bb235beed83609a3a1177da3baf49545ee797`.
- Existing queued job: `84048d5f31b4404ba8a6c07e1af98f6a`.
- These payloads and receipts are preserved. No job was submitted, restarted,
  cancelled, watched or polled during this correction.

The newer mutable driver is unexecuted and is not an input to that queued job.
Even a successful old r1 receipt cannot qualify its cleanup guarantees or close
Q2 for the current driver. Any future seal must bind root's then-current HEAD;
the old receipts continue to bind their original base. No syntax compilation,
test, package build or executable fixture was run for this source correction.

## Independent review and pending qualification

The first fresh review, `n06-ts-q2-retained-group-review-r1-20261022`, inspected
runner `69b097c96dfa48e4d48c85147b785dadd8575c2517ecdda1842f7f47985b8c8b`
at HEAD `188a1e4a69938cb4c433a7df8d53f16ab6044b50`. It confirmed all-exit cleanup
and no post-reap signalling, but retained three P2 findings: racy Linux inventory,
unguarded/non-atomic final persistence, and late recorded interruption returning
success. All three are addressed in the revised source described above.

Root additionally identified the executing-driver binding gap shared with the
Go/Swift R2-3 finding; the preflight/final identity checks above address it.
The new independent task `n06-ts-q2-driver-binding-review-r2-20261022` inspected
the complete runner at
`d9f39e09e22be7486fece006a3d4a87f49a55801d4d09ba76b04b2c221a5b6ed`.
It found no further cleanup/receipt/interruption issue, but retained one P2:
abspath collapsed a symlink-plus-dot-dot invocation before checking the executing
file, allowing the sealed path to be inspected instead. The raw-spelling guard
above addresses this source trace; no executable reproduction was run.

A fresh independent task `n06-ts-driver-raw-path-review-r3-20261022` reviewed that
narrow correction, source SHA-256
`70c05fcafac2a2c37d52bcb0d7cfd838af27f1fdbbdcbb19890cfeacabd93882`.
It returned **no actionable findings**: the raw-path guard rejects the reported
symlink-plus-dot-dot invocation before normalization, accepted paths still undergo
the exact sealed-member checks, rejection precedes child commands, and the final
capture retains the failure. Start/end source identities matched at HEAD
`faa5baa6c9ddafbf0ff40bfd28f054709d65313e`; the frozen manifest and submission/show
receipts also remained unchanged. Every round used a new delegated task, not a
continuation through an earlier review's backing thread. These are static source
reviews only; they do not close executable qualification.

Later executable qualification must exercise ordinary successful and nonzero
leaders with no descendants; successful and nonzero leaders leaving descendants;
natural grace exit and TERM-resistant children; timeout; interruption during
startup/wait/cleanup; inventory/ownership failure; and retained stdout/stderr plus
first-error diagnostics. It must also cover atomic replacement failure, fallback
diagnostics, interruption during final persistence, a wrong outer driver path,
symlink-plus-dot-dot and other rejected invocation spellings,
driver mode/content drift, and rejection of unsupported inventory hosts.
It must establish that signals never follow ownership
release and that successful leader exit cannot hide remaining writers. These
cases remain pending; no additional Kyoto job is authorized or submitted here.

Q5 was routed to the installed-harness owner. The in-tree coordinator probe and
the installed encodeInput counter are different evidence: encoding precedes send
admission. The owner must bind a transport witness to the installed package or
narrow the installed acceptance label. No harness-owned file is edited here.

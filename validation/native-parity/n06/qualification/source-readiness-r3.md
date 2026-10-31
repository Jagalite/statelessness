# N06 harness source readiness for independent R3

Status: **source handoff; independent R3 and all execution remain pending**.
This addendum describes the H6–H9 changes after the historical R2 selection.
It does not revise the R2 manifest, R2 readiness text, `N06-QUALIFICATION.md`,
any earlier review, frozen payload or job receipt.

The complete selected-source identity is
[`source-readiness-r3.manifest.json`](source-readiness-r3.manifest.json), SHA-256
`54bff8543f5b9efc1911d4dcca44b3d12c6ce5abbea1bd20b697dbd7516009a7`.
It contains **61 selected files / 72 file-and-directory entries / seven reference
pins**, plus five entries covering the complete admitted N05 helper directory.
The manifest records file/directory membership, types, modes, sizes
and SHA-256, the seven preserved reference pins, the complete admitted N05
helper directory, historical identity checks and exact static-check commands.
There are no exclusions inside these selected roots. This Markdown and its
manifest are metadata outside the selected payload, avoiding self-reference.

Observed checkout: `t3/native-campaign-runtime`, HEAD
`70cc3442304145249d8c4ad9604a98c4d647c6b5`. HEAD is supplemental; the content
manifest identifies the uncommitted harness source. No staging or commits were
performed. No language runtime, public example, Rust production, frozen author
input or N05 helper was edited by this follow-up.

## Finding dispositions

| Finding | Implemented source change | Remaining boundary |
| --- | --- | --- |
| H6 | `driver.py` binds the actual aggregate/focused/suite/guardian interpreter, declared complete installation roots, executable aliases/referents, base executable, prefixes, flags and imported origins. Aggregate preparation captures this separately from its later PATH-selected package builder. Focused control v2 binds exact bytes, SHA-256 supplied in argv, inode/type/mode/link identity and invocation, with final rechecks. `suite_runner.py` requires the exact nonempty source-derived test IDs and actual start/stop/success outcomes. | Authored controls include A/B and no-op driver rejection, source/import escape, control substitution, zero work, skipped/wrong IDs, stale/changed receipts. Synthetic identity controls are not execution of an alternate interpreter. No suite has run. |
| H7 | `receipt_io.py` anchors directories and retains original regular descriptors, with no-follow/nonblocking admission and device/inode/type/link checks. Recorder reserves terminal slots before launch, preserves displaced original streams/journal, reports the first target error separately, and attempts independent recovered terminal pairs. N06 object/receipt reads and tree hashing use the owned safe implementations. | Production leaves substitutions untouched. Authored fixtures bound the concrete FIFO scenarios with outer deadlines; only their exact synthetic FIFOs are retired after witness checks. No hard filesystem/preemption bound, recovery of destructive same-inode writes, guaranteed storage persistence or hostile same-UID containment is claimed. |
| H8 | `capture.py` records partial roots independently, distinguishes missing/dangling/special entries, and continues to readable siblings/later roots. Aggregate/focused before/after stages persist independent observations. Later compiler captures continue after a checkpoint failure. `terminal.py` attempts an exclusive alternate receipt if a canonical slot fails, preserving the primary error. Comparison/host/summary writes are independently attempted. | Storage failure can prevent any receipt from persisting. A fallback preserves failed evidence and cannot qualify as the canonical successful result. Authored failed-artifact/later-root/terminal-FIFO controls remain unexecuted. |
| H9 | Rust `src/host/seeded.rs` derives requested membership and wrapping seeds independently before execution, then checks the full report envelope, every effective config field, fresh model metadata, complete inner reports and ordered callbacks for ordinary/guided workers 1/3. Actual receipt order remains unchanged. | Nineteen validator-rejection controls are authored per path, including wrong membership, seed/config/metadata/outcome/report/callbacks. These are not runtime fault injection. Rust is uncompiled and unexecuted. |

The targeted source review during implementation found an additional H6 import
scope gap: admitting all of `scripts` allowed an unselected helper sibling to
shadow a standard module. The aggregate now admits only its complete N06 support
directory, complete N05 support directory and exact entry point, in both authored
and snapshot locations. Those roots receive strict inventories with **no
generated-directory exclusions**, copy equality and later source/snapshot
verification. The added sibling-module/generated-membership control is authored
without importing its synthetic module. The N05 files remain read-only inputs;
only N06 implementations execute their own recorder, receipt and object logic.

H1/H2 compiler/configuration changes, H4's other installed hosts, H5's actual
public validation domains, raw diagnostic differences and the exact ordered
49-case requirement remain as previously documented. H3/Q5 is still a
**separate actual transport-admission/fault-control execution gate**. Neither
this source identity nor installed encoding/progress/quiet-interval observations
close it. Python's invalid-guidance constructor scope and Swift's N05
feedback-metadata `ModelError` / Some / incomplete versus Rust `Panicked` remain
explicit dependencies. No row or diagnostic is normalized or removed.

## New interfaces for the coordinator

Aggregate preparation now requires the actual driver to run with `-I -S -B`
and one or more `--python-installation-root ABSOLUTE_ROOT` declarations. The
complete roots must cover its executable/base executable, prefixes and loaded
stdlib/runtime inputs. Unsupported or incomplete layouts fail closed. Capture
and verify that actual driver before using a distinct native builder. The
existing corpus/freeze/HEAD/Rust-toolchain arguments are still mandatory.
The historical invocation examples need this R3 addition; they were preserved
byte-for-byte rather than retroactively rewritten.

Focused control is now `n06-focused-harness/2` with the concrete input fields
used by the implementation: `python`, `python_installation`, `driver_manifest`,
`snapshot_manifest` and `suites`. `driver_manifest` is a content-addressed
`capture_driver` object whose declared source roots include the complete frozen
`ATTEMPT/source`. `suites` is the AST-derived inventory of the three fixed modules
from that same snapshot, including each source hash and ordered test IDs. The
controller must retain the exact control bytes and supply their SHA-256 from
the immutable invocation; a digest inside the control does not authenticate it.

The future focused target argv has this shape (not submitted):

```text
ABSOLUTE_CAPTURED_PYTHON -I -S -B ATTEMPT/source/scripts/native-campaign-support/focused_run.py --attempt ATTEMPT --control-sha256 EXACT_CONTROL_SHA256
```

Only root will seal an actual installation/control/snapshot and submit that
single immutable command through shareddefault Kyoto after R3. `suite_runner`
launches the exact three module selections with the same captured interpreter
and isolation flags. Exit zero, discovery counts and version text are
insufficient: receipts must contain each expected actual test outcome, then
survive final control/source/runtime/receipt checks.

The complete runtime installation observations are sequential filesystem
identities, not an attestation of loaded memory or a hermetic operating system.
Kernel, dynamic loader, dyld shared cache and arbitrary transitive system
libraries remain outside scope. Guardian loss, escaped sessions/groups, remaining
zombies and failed storage retain the previously explicit failure boundaries;
the parent never retries a numeric PGID after losing its live anchor.

## Handoff and pending execution

Implementation authors have released all owned files. Static inspection covers
the integration APIs, import graph, partial-capture/error paths and new Rust
envelope validator. Python AST parsing and JSON/file metadata inspection do not
import repository modules or execute tests. Exact source-check commands/results
are retained in the new manifest. All authored test counts and hashes are source
facts only; no compilation or runtime result is implied. The metadata check
parsed **21 Python source files** and counted **75 authored test methods**:
23 harness, 25 recorder and 27 focused. The recorder suite includes 34 bounded
substitution cases and two lstat-to-open race controls inside those methods;
these are authored cases, not completed executions.

The new targeted source follow-up `n06-h8-source-binding-check-r3b-20261022`
reported no new P1/P2 in its narrow scope, confirming the sibling-origin fix,
independent later captures and strict tree adapter. It reread the earlier
finding and response and observed matching source hashes at opening/closing.
Its result does not replace root's fresh full independent R3. The parent made
no further implementation changes after that targeted review.

**New job ID: none. Requested/effective command: none submitted.** The eight
previously reported live handles remain untouched and were not polled. Their
original commands and identities stay in their original receipts. Old r1
payloads cannot qualify this replacement driver/recorder/consumer source, even
if an older queued job later succeeds. No watcher, resubmission, cancellation,
aggregate execution or queue-policy action was performed.

Remaining steps belong to root: fresh independent R3; final source review/seal;
one immutable focused harness job with actual installation and authenticated
control; successful exact suite execution; independent author materialization
and freeze; fresh installed package builds and all five engine comparisons;
baseline checks; separate causal transport/self-model/fault receipts reconciled
against production/package/driver bytes; and aggregate reconciliation.

The aggregate-ready marker is absent in `docs/native-parity/N06-INTEGRATION.md`
at this handoff. The independent N06 author job
`1be30f3a8bea47758dd063d93a8970cb` and N05 author job
`36c3552df326429fb9e76d872c80aafa` were not polled or restarted. Author artifacts
remain an explicit gate. The supplied N05 boundary remains
**materialized78 / reviewed90 / final qualification pending**. This handoff
does not claim author90, N05, N06 aggregate, full-width native-count parity,
N10, default/profile promotion or any 144-cell qualification passed.

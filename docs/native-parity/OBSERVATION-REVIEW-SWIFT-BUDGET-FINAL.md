# Swift observation budget final source review — round 4

Reviewed 2026-10-22 in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, HEAD
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`.

**The previous F1 Recorder regression and F2 consumer measurement gap are
addressed in source. One new P2 remains in the shared bounded-replay caller;
one separate pre-existing P2 remains in bounded audit recording.** The monitor,
replay and recording entry points have different Rust admission orders, so
matching `trace::step_size` alone cannot establish parity for every caller of
`executionStepFixed`.

This is source inspection, not compilation or runtime qualification. No
implementation, fixture generator, compiler/parser, test, executable check,
package installation, or Kyoto job was run or submitted. Expected fixtures were
not generated. Ancestor and relevant in-tree `AGENTS.md` searches found none;
the requested `/Users/jagatranvo/.codex/skills/rust-builds-kyoto/SKILL.md` was read.
Only this new report was written. The parallel consumer/observation reviewer
changed no files. No staging, commits, push, PR, or other-worktree changes were
made. The parent owns the integrated freeze and shared Kyoto qualification.

## Findings

### R1 — P2, new regression: monitor STEP ordering changes bounded replay's primary error

[ExecutionBudget.swift:29](../../swift/Sources/Statelessness/ExecutionBudget.swift#L29)
now admits the STEP before output and check items. This is correct for
`Recorder.prepare`, but
[Replay.swift:146](../../swift/Sources/Statelessness/Replay.swift#L146) calls the
same helper. Rust bounded replay instead calls
[`replay_step_preflight`, execution.rs:723](../../src/execution.rs#L723): it
validates output count, then the complete check batch at line 734, then combines
check, output and STEP items at lines 736–741. This is deliberately distinct
from `trace::step_size`'s monitor ordering.

Source-derived public scenario, not executed:

- Supply a literal, valid `AuditTrace` with short matching metadata/config,
  initial state `[0]`, no initial checks, and one accepted step with input `[1]`,
  no outputs/checks, post-state `[1]`, and completed termination. The artifact
  fits all budgets; it is allowed to differ from actual replay results.
- Use one-byte canonical state/input codecs. Initial checking returns no checks;
  replay's single reducer call returns state 1 and 63 outputs; successor checking
  returns one passed check whose ID is 65 ASCII bytes, with no transition checks.
- Set `maxItems = 64`, `maxOutputsPerStep = 128`, `maxStringBytes = 64`, and leave
  the other budgets ample. Initial identity, artifact preflight, canonical
  encoding and initial-check comparison all pass before actual-turn checking.
- Rust's isolated `checked_batch_size` admits its one check item, then rejects
  the 65-byte ID as `string bytes` before aggregate STEP/output admission.
- Current Swift admits STEP + 63 outputs = 64, then fails at
  [ExecutionBudget.swift:49](../../swift/Sources/Statelessness/ExecutionBudget.swift#L49)
  while adding the check, reporting `aggregate items` before inspecting its ID.
  Base Swift at the stated HEAD counted 63 outputs + one check before adding
  STEP, so this same replay case reached `string bytes`. This specific change
  is therefore a regression, not merely an older mismatch.

Both paths retain actual-turn error evidence and stop before output encoding,
but expose different primary failure categories to the replay caller/observer.
The existing new public Recorder test intentionally expects aggregate items for
this tuple; it cannot also stand in for replay's distinct contract.

**Correction:** retain the corrected monitor wire order and give bounded replay
the admission order of Rust `replay_step_preflight` (or otherwise make the
entry-point policy explicit). Do not revert the Recorder correction. Add a
public bounded-replay competing-limit regression with a source-authored valid
expected artifact, asserting string-byte failure, actual/check evidence,
one reducer call, and no output encoding. Also retain existing bounded replay
frame/total/item, failure-evidence and callback-cutoff regressions.

### R2 — P2, pre-existing adjacent discrepancy: recordAudit measures check text before its cumulative item admission

[AuditRecording.swift:38](../../swift/Sources/Statelessness/AuditRecording.swift#L38)
calls `executionStepFixed` with zero provisional outputs before computing
`nextItems` at lines 39–40. Rust bounded recording first checks cumulative
`items + checks.len() + 1 + outputs.len()` at
[execution.rs:387](../../src/execution.rs#L387), then measures its provisional
step at line 403.

With the same short metadata, one-byte codecs, empty initial checks, 63 outputs,
one 65-byte successor-check ID, and 64-item/64-string-byte caps, Rust rejects
aggregate items before reading the check ID. Swift reaches that ID inside the
helper with only STEP + one check counted and reports string bytes. The result
is stored in the recording's model-error termination. Both algorithms stop
before encoding outputs and retain the prior coherent prefix.

This caller and the relevant ordering already existed at HEAD; neither
`AuditRecording.swift` nor its calling convention was changed by this patch.
It is recorded separately because the requested review includes shared usages.
It must not be described as caused by the new monitor fix, nor may a monitor-only
pass be used as evidence of all shared-caller precedence parity.

**Correction:** preflight bounded recording's cumulative step/check/output
items before its fixed-field/check-text measurement, preserving the existing
coherent-prefix and bounded-output accounting. Add a separate public
`recordAudit` competing-limit regression with a literal/source-derived expected
category and callback counts. This report makes no implementation change.

## Previous F1: Recorder correction and tests are sound in the reviewed source

[ExecutionBudget.swift:25](../../swift/Sources/Statelessness/ExecutionBudget.swift#L25)
now follows Rust
[trace.rs:360](../../src/trace.rs#L360) and
[trace.rs:278](../../src/trace.rs#L278) for the provisional monitor step:
admit STEP; enforce the frame bound for index and framed input; enforce the
disposition tag/reason; admit outputs then their count/prefixes; admit the empty
post-state prefix; admit checks then their count, IDs, statuses and details.

`AuditSizer.blob/string` check their own length limits before the framed append,
matching `Encoder::sized`. `count` checks the collection limit and aggregate
items before its four-byte count append, matching `Encoder::count`. Combining
the empty output prefixes into `outputCount * 4` introduces no competing check
between those prefixes; count admission bounds this product to the u32 range
times four, which fits UInt64. There is no duplicate late STEP count.

The ordinary monitor path still checks continuity and early output count before
input encoding/checking, and measures a provisional step with empty output and
post-state bodies before output codecs, as
[monitor.rs:424](../../src/monitor.rs#L424) and
[monitor.rs:477](../../src/monitor.rs#L477) require. Checked handoffs retain their
earlier certified-batch preflight; the ordinary test must not be generalized to
that distinct route.

The public regression at
[ObservationBudgetPrecedenceTests.swift:5](../../swift/Tests/StatelessnessTests/ObservationBudgetPrecedenceTests.swift#L5)
has valid initialization under its 512-byte frame cap. By direct source
accounting, `frame-order`/`v1`, manual strategy, eight stamped parameters, a
one-byte checkpoint and no checks make a **398-byte RUN payload** (407 with
framing). The reserved error footer is 269 payload bytes; ample default total
and retention budgets admit initialization. All eight run items fit cap 64.

Its 512-byte input is legal at the blob/encoder bound, but index plus framed
input needs `8 + 4 + 512 = 524` bytes. That exceeds the 512-byte frame before
STEP + 64 output items can fail. The asserted frame category, one application
reducer call, two state checks, zero output encodes, zero admitted/retained
steps, frozen state, and unchanged checkpoint checks/bytes/config/metadata are
meaningful and distinguish the previously reported item failure. No expected
fixture is derived from execution. The test does not explicitly assert the
model-error termination's text; its thrown-error assertion and preserved-prefix
assertions should not be described as that additional assertion.

The existing 63-output/65-byte-check-ID case at line 41 remains intact. At item
cap 64, STEP + outputs + check = 65 fails before the ID; at cap 65, the ID's
string bound fails next. Short initialization strings and eight run parameters
do not preempt either case. Its callback/freeze assertions remain meaningful.
These are inspected test sources, not demonstrated passes.

`Recorder.measure` continues to add real output/post-state bodies and frame
overhead to the helper's fixed payload; `executionRemaining` and replay's
byte accumulation continue to add overhead exactly once. `recordAudit` still
adds output prefixes separately because it passes zero provisional outputs.
No byte/item double count was found. The caller-specific error ordering issues
are R1/R2 above.

## Previous F2 and earlier Swift corrections

**F2 addressed in source:**
[consumer main.swift:246](../../examples/native-observation/swift/Sources/ObservationConsumer/main.swift#L246)
counts every destination write/flush attempt before injected failure checks.
Zero-byte writes and successful flushes are observable. Lines 301–315 take
writer/callback snapshots before the final Off probe and compare writes,
flushes, bytes and callbacks after extraction. `Sink` is a struct and `getRef`
returns its value copy, so subsequent mutations cannot update the baseline.
The assertion covers the combined probe/extraction interval, including poison.
It does not separately identify which operation would violate that interval.

Earlier fixes remain present: private per-lease formatter-error provenance;
public/nested/stale error attribution tests; exact arbitrary-byte and final-LF
record bounds; declaration adapters with throwing/counted direct hooks and
binder-omission controls; reserved footer admission after metadata but before
checkpoint codecs/checks; and separate early output/item categories after
continuity. Their relevant sources and regressions were re-inspected, including
the parallel focused consumer/observation review.

Current `CheckedResults.swift` and `Model.swift` retain sealed checked results,
full-checking and sequence policies. Recorder retains authentic-token handoff,
absolute origin/overflow guards, failure freeze, bounded retention, and promotion
of only state-check prefixes. The historical
[CHECKED-RESULTS-AUDIT.md](CHECKED-RESULTS-AUDIT.md) is not a current missing-API
inventory. No diagnostic event gains continuity or checked authority, and no
reducer/check/effect execution was added to logging.

No definite new Swift syntax, access, generic-inference or concurrency mismatch
was found by inspection. The nested throwing `append` captures only synchronous
local accounting; new consumer counters are ordinary instance state. Mutable
observers and leases are not newly Sendable. Root `Package.swift` includes the
entire native test directory, including the new public test file. This does not
substitute for compilation.

## Qualification boundary

Resolve R1 and explicitly disposition R2 before claiming shared-helper parity.
The parent must freeze the integrated sources and run its shared Kyoto
qualification, including actual Swift observation, Recorder, checked-results,
bounded recording and replay tests; installed-consumer and declaration-bypass
checks; and source-bound retained-suffix replay. Compilation-only success is not
test execution. Pinned Rust observation/monitor sources and relevant existing
tests were inspected; none were executed or changed.

This focused review does not close
[N07](../NATIVE-PARITY-PLAN.md#n07-runtime-observation-and-bounded-recording),
installed-package identity, fresh-process replay, or whole-product parity.

## Source identities

SHA-256 readings of the reviewed working files. These are not an atomic whole-tree
seal or runtime receipt; later edits require refreshed review.

```text
3304f944e449ddc34a99e6dddc430306db3f101e022ef121058e218cf66df15c  swift/Sources/Statelessness/ExecutionBudget.swift
a784aec0dfc269dc2b339ce02bd51334ff01f74ad10d6885fb5f561366387c92  swift/Tests/StatelessnessTests/ObservationBudgetPrecedenceTests.swift
759ed796c1614294f4a46ac42a3a9532882297325c91cd4e5469136f0cf59692  swift/Sources/Statelessness/Replay.swift
0c59aa7540cadfa788e13eb5cc04c049667a1b631213bc51711f4a01231a1431  swift/Sources/Statelessness/AuditRecording.swift
0d90f40a48e7580383c0a402a93ad871f9c74031bb83a046e263c102eab94796  swift/Sources/Statelessness/Recorder.swift
de4214e0474f36e6b11d3211b4915b1d047371559a43c4bba224379f56aa4830  examples/native-observation/swift/Sources/ObservationConsumer/main.swift
cbc2a14bca3a09178e8a08dd04f7fc3176404f845704ed8a4e4e14ef550d1b9a  swift/Sources/Statelessness/Observation.swift
d2f92206250ddbc1368364c8c6cce4100074ce599e26bb722125e1d8c7dc2ccb  swift/Sources/Statelessness/CheckedResults.swift
3c00084776a8d530b3edca6d90dcac839d422880b8ba996cda7ebc7d9db6270c  swift/Sources/Statelessness/Model.swift
b70b7b507f408adc89878b3c80b90f09a5902b8e80519b547be1844a1329532f  swift/Tests/StatelessnessTests/ObservationTests.swift
8cc6b0f6645f998bf102281f426872d00f69699d3ed1711a0d9af8fd04ca6473  swift/Tests/StatelessnessTests/ObservationRecorderIntegrationTests.swift
a2b61ffbfbe9d9786adc0f54546352bac5908bfd0e8076a0a0df9681ea887af4  Package.swift
c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870  src/observation.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
157ed1c5e070372220c14a23e6ba9e636e0ab94507160e7197f1419052bfc786  src/trace.rs
acd82232904dc45e5df92c16f32ac3c12840ba3519fe88887737a6e7f05f9df0  src/execution.rs
a7a4c8242408531e10453b0314c11b26c8c469ce2c8eca56e81c2e748d783cd1  src/execution/replay_kernel.rs
842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d  tests/observation.rs
c4d9ad75cc0916eb6bd582cf98e22f3194c54df2be3aa52a6505a81b8dd35c0f  tests/monitor.rs
f21ec4778015ad0ace2e54735dee6a6ed37072db918a00ef975854e1bf3042cc  tests/monitor_review.rs
4a79b2f8fe236c27442a016bf748f254164a96da7d10706c01725ba852b10165  tests/monitor_limits.rs
315a93ab3981db0bfe8bc927c6b6409643eca68d43fec9c79bcf803b23c5601d  docs/native-parity/OBSERVATION-REVIEW-PYSWIFT-FINAL.md
0861c03a9f2ede02d800dfa9ad272f59acf0fa1bd7f53e3d493b8b50a9a0d25e  docs/native-parity/OBSERVATION-FIXES-SWIFT.md
```

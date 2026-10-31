# Swift replay and recording budget caller corrections

Source-only changes on 2026-10-22 in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, based on
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`. This handoff addresses **R1 (new)**
and **R2 (pre-existing)** from
[the round-4 budget review](OBSERVATION-REVIEW-SWIFT-BUDGET-FINAL.md).
The two findings are corrected in source; compiler and runtime qualification
remain the parent's responsibility after the integrated source freeze.

Only `ExecutionBudget.swift`, `Replay.swift`, `AuditRecording.swift`,
`ObservationBudgetPrecedenceTests.swift`, and this new report were edited in
this task. Existing unrelated edits were preserved. Applicable ancestor and
Swift/docs `AGENTS.md` searches found none. The requested
`/Users/jagatranvo/.codex/skills/rust-builds-kyoto/SKILL.md` was read.
No builds, tests, compilation/parser checks, implementation execution, fixture
generation, executable qualification checks, Kyoto submissions, staging,
commits, push, or PR were performed.

## R1: bounded replay uses its own preflight

[ExecutionBudget.swift](../../swift/Sources/Statelessness/ExecutionBudget.swift)
adds `executionReplayStepFixed`, used only by
[Replay.swift](../../swift/Sources/Statelessness/Replay.swift). It follows pinned
`src/execution.rs:723` (`replay_step_preflight`):

1. Admit output count against the per-step/u32 cap.
2. Validate an isolated check batch, with its own item count and frame payload,
   following `trace::checked_batch_size` / `Encoder::checks`. Each count, ID,
   status and optional detail is admitted before the next field. This matters
   when an earlier status byte overflows the frame before a later detail or ID
   exceeds its string cap.
3. Admit the combined check + output + STEP items.
4. Validate the disposition reason's UTF-8 length.
5. Admit the complete fixed STEP payload, including all output length prefixes.

Replay then retains its existing cumulative run/step item admission and shared
frame/total/footer budgeting before encoding outputs or post-state. Its actual
turn/check evidence, observer dispatch, report updates and failure handling are
unchanged. The helper returns payload bytes; the caller adds the nine-byte frame
overhead exactly once.

The corrected monitor `executionStepFixed` remains unchanged from this task's
incoming source. It continues to follow STEP wire order, including admitting
STEP before output/check items and checking an earlier input frame before a
later output count. Replay no longer applies that distinct monitor ordering.

## R2: recording admits cumulative items before measuring check text

[AuditRecording.swift](../../swift/Sources/Statelessness/AuditRecording.swift)
now admits `items + checks.count + 1 + outputs.count` immediately after the
output-count cap and before calling `executionStepFixed`, following pinned
`src/execution.rs:387-403`. The check includes stamped RUN parameters and all
previous accepted steps.

The provisional recording measurement still contains zero output bodies and
prefixes. The existing bounded output loop continues to reserve each four-byte
prefix before its codec, and final byte accounting adds each prefix/body once.
The change preserves the prior coherent prefix on rejection. It does not alter
the recording footer, input/check execution, or success admission policies.

## Public regression sources

[ObservationBudgetPrecedenceTests.swift](../../swift/Tests/StatelessnessTests/ObservationBudgetPrecedenceTests.swift)
retains both existing Recorder regressions and adds:

- Thirteen bounded replay cases covering output cap, isolated batch check/item
  caps, R1's oversized check ID before combined items, status-frame admission
  before oversized detail/next ID, combined items before reason, reason before
  fixed frame, fixed output prefixes, and cumulative RUN/STEP items. Each checks
  the exact `BinaryTraceError` category in both the error observation and returned
  `ReplayError`; both retain the actual turn, complete checks and state-check
  count. Callback sequences assert one reducer, two state checks, one transition
  check, zero output encodes, no post-state encoding, and no published turn.
- Four `recordAudit` cases asserting the literal model-error termination and
  zero output encodes. Two stamped parameters plus STEP, 63 outputs and one
  check total 67 items; a preceding empty-output STEP raises the boundary to 68.
  The tests distinguish item rejection from the next 65-byte check-ID error and
  assert preservation of either an empty or one-step coherent prefix, metadata,
  checkpoint and stamped config, with exact callback cutoffs.

The replay artifact is authored directly, with no recorded outputs/checks:
47-byte RUN payload (57 with its one-parameter case), 27-byte STEP payload and
nine-byte completed-footer payload. These fit even the smallest 64-byte frame
cap, so artifact validation cannot hide the intended actual-turn failure.
For the isolated check-batch cases, count (4) + ID length (4) + ID (56) = 64;
the next status byte fails at cap 64 but fits at 65, exposing the subsequent
65-byte detail/ID's string cap. These are source-derived expectations, not
results generated from execution. Tests have not been compiled or run.

## Preserved contracts and independent review

Pinned `src/observation.rs`, `src/monitor.rs`, `tests/observation.rs`, relevant
monitor tests, `src/execution.rs`, `src/execution/replay_kernel.rs`, and
`src/trace.rs` were inspected. A scoped source diff against the plan's Rust pin
`0876717bee85e335449c2c99ada41f6c6e6112d1` showed no changes in those files.
[N07](../NATIVE-PARITY-PLAN.md#n07-runtime-observation-and-bounded-recording)
and the historical [checked-results audit](CHECKED-RESULTS-AUDIT.md) were read;
current `CheckedResults.swift`, `Model.swift`, and Recorder APIs were inspected
rather than treating the historical missing-API list as current.

Sealed checked results, periodic checking, bounded Recorder, observation APIs
and the installed observation consumer are unchanged by this task. Diagnostic
events establish neither continuity nor checked authority. No model execution
was added to logging.

An independent T3 source review was delegated with the original brief, prior
R1/R2 findings, accepted F1/F2 corrections, report sources and exact source hashes.
It preserves the inherited target `codex` / `gpt-6-astra`,
`reasoningEffort=ultra`, `serviceTier=default`; the returned selection matched.
Task ID:
`node:delegated-task:command%3Amcp%3Aac123a6d-d258-4b02-add3-46ad7c234755%3Adelegate-task%3Aswift-callers-final-review-r1-20261022`.
The review completed with **no source blockers**. It confirmed R1/R2's admission
order, single-count output prefixes/frame overhead, valid fixtures and callback
cutoffs in all thirteen replay and four recording cases, preserved Recorder and
consumer corrections, and no Swift compilation hazards identifiable by source
inspection. HEAD and all four supplied implementation/test hashes matched. The
reviewer changed no files and ran no executable checks or Kyoto jobs; its result
does not establish compilation or runtime qualification.

The parent must freeze integrated sources and qualify actual Swift observation,
Recorder, checked-results, bounded recording/replay and installed consumers
through Kyoto. Compilation alone is not execution. This report does not close
N07, fresh-process retained-suffix replay, installed-package identity, or broad
parity gates.

## Source identities

SHA-256 readings of the working files, not an atomic whole-tree seal or runtime
receipt. The first four files are the implementation/test handoff; the remaining
identities show preserved shared contracts and pinned references.

```text
bd48f7b3af197fba6a07153a0abc5f69a10448f5da9442293f0d33e3873e7d02  swift/Sources/Statelessness/ExecutionBudget.swift
ea8303bfd635f80517b29b384f373ab4ed266b99fe93babcfe5af537aad54662  swift/Sources/Statelessness/Replay.swift
9a94b6d6c747bfd08abcbc7b6e31b6ff49cf2a2eeb4a1bb584e5322738d96dd6  swift/Sources/Statelessness/AuditRecording.swift
71d2d7d9c64a58cc604a4ca888095a056c66e4db9714a22adda1293507298e65  swift/Tests/StatelessnessTests/ObservationBudgetPrecedenceTests.swift
0d90f40a48e7580383c0a402a93ad871f9c74031bb83a046e263c102eab94796  swift/Sources/Statelessness/Recorder.swift
d2f92206250ddbc1368364c8c6cce4100074ce599e26bb722125e1d8c7dc2ccb  swift/Sources/Statelessness/CheckedResults.swift
3c00084776a8d530b3edca6d90dcac839d422880b8ba996cda7ebc7d9db6270c  swift/Sources/Statelessness/Model.swift
cbc2a14bca3a09178e8a08dd04f7fc3176404f845704ed8a4e4e14ef550d1b9a  swift/Sources/Statelessness/Observation.swift
8cc6b0f6645f998bf102281f426872d00f69699d3ed1711a0d9af8fd04ca6473  swift/Tests/StatelessnessTests/ObservationRecorderIntegrationTests.swift
de4214e0474f36e6b11d3211b4915b1d047371559a43c4bba224379f56aa4830  examples/native-observation/swift/Sources/ObservationConsumer/main.swift
acd82232904dc45e5df92c16f32ac3c12840ba3519fe88887737a6e7f05f9df0  src/execution.rs
a7a4c8242408531e10453b0314c11b26c8c469ce2c8eca56e81c2e748d783cd1  src/execution/replay_kernel.rs
157ed1c5e070372220c14a23e6ba9e636e0ab94507160e7197f1419052bfc786  src/trace.rs
c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870  src/observation.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d  tests/observation.rs
c4d9ad75cc0916eb6bd582cf98e22f3194c54df2be3aa52a6505a81b8dd35c0f  tests/monitor.rs
f21ec4778015ad0ace2e54735dee6a6ed37072db918a00ef975854e1bf3042cc  tests/monitor_review.rs
4a79b2f8fe236c27442a016bf748f254164a96da7d10706c01725ba852b10165  tests/monitor_limits.rs
980a045c78921eb24640f7def8917c8893487f0cf09994c6033372070ad52b58  docs/native-parity/OBSERVATION-REVIEW-SWIFT-BUDGET-FINAL.md
```

# N07 suffix observation fixes, source-only

Worktree: `/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`.
Base: `54ba3f32796fc5c71458fb4cda14b2ab9290ee83`, 2026-10-22.
This records source changes for S2/S3/S4 in
[OBSERVATION-REVIEW-SUFFIX.md](OBSERVATION-REVIEW-SUFFIX.md), not compilation,
execution, integrated-source approval, or N07 completion. No applicable
`AGENTS.md` was found. The Kyoto skill was read; the parent's stronger instruction
prohibited all builds, tests, compile/executable checks, and job submissions.
No implementation was executed to generate expectations. No contracts, driver,
native engine libraries, or other worktrees were edited by this implementation
task; no staging, commit, push, or PR was performed.

## S2/S3: complete sessions and separate replay work

The existing checked applications in Python, Go, TypeScript, Swift, and Rust add
`replay-suffix BIN` and `replay-suffix-json JSON`. Both return full `rich` and
`bounded` sessions: initial and turn observations, typed context, expected and
actual encoded bytes, ordered checks, state-check phase count, differences,
intermediate reports, and the final report. Each session uses a fresh model.
The separate `rich_counts` and `bounded_counts` fields expose
`initial_state: 0, reducer: 1, state: 2, transition: 1` and are compared to the
independently authored contract's common `replay_counts` object. Producer-local
replays have the same fields, but are not counted as fresh-process pairs.

Existing `record`, `replay`, and `replay-json` reply shapes remain unchanged.
The old interrupted `record-suffix` still performs two application turns and
retains state 1 followed by one passing turn to state 2. Its producer counts
remain `0/2/3/2`; its receipt now also reports the separate replay counts.

Portable JSON contains no audit configuration. The actual public APIs differ
from the direction suggested in the brief: all four native rich observation
APIs accept portable `Trace` directly, while their bounded observation APIs
require `AuditTrace`. The JSON modes therefore use portable `Trace` for rich
replay and public portable-to-audit conversion with default execution config
(`manual`, no seed, empty parameters) for bounded replay:

- Python: `AuditTrace.from_portable` and `RunConfig()`.
- Go: `AuditFromPortable` and `RunConfig{Strategy: "manual"}`.
- TypeScript: `auditTraceFromPortable` and the default manual config.
- Swift: `AuditTrace(portable:config:)` and `RunConfig(strategy: "manual")`.

Rust exposes both replay variants over public `Trace`; the existing portable
parser constructs that public value with `RunConfig::default()`. These defaults
are only replay inputs. Every JSON suffix receipt omits `retained_config`, and
the helper rejects any JSON receipt that includes it. Binary receipts retain
and compare the actual persisted config, including origin and local counters.
No native API was replaced or expanded to disguise the conversion requirement.

## S4: first failure after eviction

`record-failing-suffix PREFIX` keeps capacity 1, origin 40, and supplied initial
state 0, then performs exactly three application turns at sequences 41, 42, 43.
Each reducer runs once and each completed turn is checked once through the real
checked-token API. The checked token goes to the existing Recorder; an already
checked diagnostic view goes to the observer. No event authorizes recording.

Source ordering independently agrees with the new literal failure contract:

1. `Threshold` increments the state, appends `threshold.bound` first and
   `state.appended` second, then appends transition check `edge.checked`.
   At state 3 the first check is `Failed("at least three")`; the later two pass.
2. `src/monitor.rs` commits an observation only after preparing and fitting it.
   Eviction promotes only the evicted turn's state-check prefix. After three
   observed turns and two evictions, the retained checkpoint is state 2 with
   two passing state checks, followed by state 3's complete failing turn.
3. Failure changes termination to `PropertyFailed`; `observe_checked` rejects
   a subsequent handoff before preparation or callback execution. The local
   `checkpoint_after_sequence` is 2, not absolute 42; origin 40 is separate.
4. `src/execution/replay_kernel.rs` checks the decoded checkpoint once, executes
   one input, then checks state before transition. Replay initial/turn sequences
   are local 0/1. The initial report has zero verified steps and no reproduced
   failure; the turn/final reports have one verified step and reproduce the
   exact first failed check identity/details.
5. `src/observation.rs` gives passing transitions Debug severity and the failing
   transition Error severity. Trace configuration includes the complete ordered
   check batch. `NoPayloads` formats unavailable markers without codec work.

Producer counts are `0/3/4/3`. The main `observer_text` preserves its existing
prefix, checkpoint 40, and the three turn records. A separate observer receives
the supplied checkpoint `(sequence=44, state=999, checks=[])` after failure;
its literal output is returned as failing-only `freeze_observer_text`:

```text
Trace stream="suffix" event=checkpoint sequence=44 checks=0 state=unavailable
```

Each producer compares frozen binary and portable artifact bytes and tracked
callbacks before and after that diagnostic and a rejected handoff of the same
authentic final token. It does not execute/check a fourth application turn.
`freeze_verified: true` is returned only after the actual invariants hold. The
supplied checkpoint is deliberately unrelated to the retained state, making
clear that diagnostic events confer neither continuity nor checked authority.

Relevant inspected Rust regressions include `tests/observation.rs` filtering,
severity, and explicit checkpoint cases; `tests/monitor.rs` retained-checkpoint
and first-failure freeze cases; and `tests/monitor_review.rs` variable state-check
phase promotion. Existing tokens, periodic policies, and bounded Recorder code
were preserved. Historical missing-API statements in
`CHECKED-RESULTS-AUDIT.md` were not treated as descriptions of current APIs.

## Helper and parent integration

`scripts/observation_suffix.py` stages the applications once and runs both
independent contracts. Its v2 report has `cases.interrupted` and `cases.failure`,
each with 5 producers, 25 ordered binary pairs, and 25 ordered JSON pairs;
planned aggregate counts are 10/50/50. Each pair is a fresh process and runs
both rich and bounded sessions internally. These are planned counts until the
parent's runtime qualification actually completes.

Both matrices compare complete sessions and separate callback counts. Producers
also compare main counts, checked-token metadata, actual diagnostic text,
retained config, and full portable artifacts. Failure producers additionally
compare the independently authored freeze diagnostic and require the measured
freeze invariant. Binary framing checks preserve header, frame order, CRC,
local step index, retained count, and exact terminal footer: Interrupted ends in
`03`; PropertyFailed ends in `01`. The two cases remain distinct.

Generated Go and TypeScript build configs are registered immediately through
the parent-owned `Attempt.register_generated(path, label)` before any consuming
command. `q.stage` and the driver own source byte/membership checks. The helper
records and compares watched runtime identities before and after every consumer
invocation, including failure, and again at completion. Immutable raw command
logs and partial rows remain with the parent attempt. A failed case marks the
aggregate failed and leaves later cases pending.

The independent fixture task owns `suffix-contract.json` and
`suffix-failure-contract.json`; this task only reads them. Parent owns final
integrated review, source freeze, exact staged commits, and the single shared
Kyoto qualification. This finite suffix coverage does not replace long-session,
negative-limit, callback/codec/sink failure, or existing Recorder suites, and it
does not certify any evicted prefix.

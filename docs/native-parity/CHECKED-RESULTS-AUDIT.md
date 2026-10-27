# Checked results and provenance-preserving handoffs

Static next-scope audit, 2026-10-20. Inspected worktree HEAD
`8bb4fe3924fdaf0f7227acfb0f4260277ec6b7ee` and concurrent native codec/replay work.
The referenced Rust `observation.rs`, `execution.rs`, and `monitor.rs` are unchanged
from the plan's pinned baseline
`0876717bee85e335449c2c99ada41f6c6e6112d1` (verified by a scoped Git diff).
This document establishes implementation requirements, not implemented parity or
runtime qualification. No builds or tests were run for this audit.

Read with [the append-check audit](APPEND-CHECKS-AUDIT.md),
[bounded-codec/replay audit](ENCODE-BUFFERS-REPLAY-AUDIT.md), and
[overall status](../NATIVE-PARITY-STATUS.md). Those earlier documents describe
their own source snapshots; use current code and qualification receipts to assess
subsequent implementations.

## Existing native behavior and missing authority

All four independent engines now expose collection and append-style checking of
already executed transitions: Python [check_observed_into](../../python/src/statelessness/core.py),
Go [CheckObservedInto](../../go/model.go), TypeScript
[checkObservedCountInto/checkObservedInto](../../typescript/src/core.ts), and Swift
[checkObservedInto](../../swift/Sources/Statelessness/Model.swift). Their policy and
state/transition dispatch can support the next slice. Current rich replay actual
results and observations in Python [replay_evidence.py](../../python/src/statelessness/replay_evidence.py),
Go [replay_evidence.go](../../go/replay_evidence.go), TypeScript
[replay.ts](../../typescript/src/replay.ts), and Swift
[Replay.swift](../../swift/Sources/Statelessness/Replay.swift) provide diagnostic
evidence, not sealed authority to bypass future checks.

No independent native public `CheckedInitial`, `CheckedTurn`, `check_initial` /
`checkInitial`, or `check_turn` / `checkTurn` API was found in the inspected native
surfaces. Python's [native.py check_initial](../../python/src/statelessness/native.py)
is an ABI-backed result returning checks; it does not satisfy this independent
sealed-result requirement. There is also no native exact runtime recorder token
consumer matching the Rust monitor recorder. A public tuple/array of checks or a
caller-constructible replay observation cannot substitute for these APIs.

## Exact Rust checking and result contract

[check_initial](../../src/execution.rs#L30) receives the caller's supplied state,
which may be a mid-run checkpoint, and invokes state checking once. It neither
calls `initial_state` nor executes a transition. A complete batch, even one
containing failed/skipped checks, produces
[CheckedInitial](../../src/observation.rs#L22). A checker error produces no token.
Its private context binds model, checkpoint and checks; accessors expose model,
state, checks, state-check count, and a consuming conversion into checks.

[check_turn](../../src/execution.rs#L48) checks a supplied already executed
transition once, using the existing observed-check policy. Sequence zero is
invalid. It never invokes the reducer. A checker error leaves the application's
actual transition intact and produces no token from the partial batch.
[CheckedTurn](../../src/observation.rs#L61) privately binds:

- The model, before state, input, actual transition and positive host sequence.
- The complete ordered check batch and its state-check count, including explicit
  policy-skipped checks. Transition checking follows state checking.
- `full_checking`, true only when state period is exactly one **and** transition
  checks are enabled. A sampled period is not full checking even at a sequence
  where a state check happens to run.

Its read-only accessors and consuming `into_checks` do not grant replacement of
context or coverage. Its `observation()` constructs a diagnostic
[TurnObservation](../../src/observation.rs#L114), which has no authority to bypass
checks. The complete batch certifies checking occurred, not that every property
passed. Do not refuse to create a token solely because a check failed.

Rust tokens borrow context; they are not independent snapshots and do not defend
against interior mutation violating Model's contract. They are not globally
single-use: recorder methods borrow tokens, so the same token may feed different
recorders. A single recorder enforces ordering. Do not invent universal token
consumption, persistence, or process-wide identity guarantees and call them Rust
requirements.

## Exact handoff consumer and dependency boundary

[Recorder::with_checked_initial](../../src/monitor.rs#L134) and
[with_checked_initial_at](../../src/monitor.rs#L145) initialize from the sealed
checkpoint without rerunning state checks. The explicit attachment sequence is a
host origin, while retained trace indexing/counters remain local. Initial failed
checks freeze the recorder as a property failure without rechecking.

[observe_checked](../../src/monitor.rs#L308) consumes the token's bound context and
checks without running reducers or checkers. It rejects a frozen recorder,
non-full coverage, and any sequence other than origin + observed count + one,
including arithmetic overflow. These recording errors freeze the coherent prefix.
Its [prepare path](../../src/monitor.rs#L398) still validates metadata identity and
encoded before-state continuity, uses bounded codecs, validates format quotas,
and copies complete checks with the phase boundary. The recorder does not merely
trust any token carrying the next integer. Identity comparison is metadata-based;
Rust does not impose additional model-pointer identity equality at handoff.

Ring eviction promotes only the evicted turn's state-check prefix to checkpoint
checks, not its transition checks. Property failure and earlier recording error
cannot be overwritten by later observer errors. These belong to full runtime
recorder qualification, not just token construction.
[EvidenceRecorder](../../src/evidence.rs#L493) is a further consumer for causal
metadata/envelopes; matching it also requires its own budget, phase and provenance
semantics. Tokens alone do not implement N05/N11/evidence recorder parity.

The next coherent slice can implement sealed checked results, diagnostics and
public check-once construction first, using current append dispatch and exact
batch boundaries. Treat handoff compatibility as pending until a real native
recorder consumes them without rechecking. Do not build a placeholder recorder
or a fabricated passing flag simply to claim an end-to-end handoff.

## Native API and ownership requirements

Preserve existing collection/into checking and basic model constructors. Add
language-appropriate result types and check functions; share dispatch so policy,
order, error precedence and phase boundaries do not fork. Models without codecs
must support checked results. No serialization, hashing, Debug, Send/Sync, or
output decoder is required merely to check an application-owned transition.

Keep token constructors and authenticity state private to the engine. Fields must
not be replaceable through public setters, ordinary object literals, exported
initializers, copy/update helpers, or mutable checks arrays. A forged diagnostic
view must not pass as a token to a future recorder. Python/JavaScript privacy is
an API/integrity convention, not a security sandbox against hostile reflection;
document this limitation rather than claiming cryptographic authority.

Choose and document a native ownership model. Detached snapshots follow current
engine ownership conventions but can allocate/fail; borrowed immutable context
requires enforcing model determinism and avoiding accidental alias mutation.
Capture/check a consistent checkpoint/turn: do not check one mutable object and
then attach an unrelated later copy as certified context. Returned check and
diagnostic accessors must not expose mutable token internals. Preserve Go slice
ownership and TypeScript runtime immutability, not only static `readonly` types;
Swift structs do not automatically deep-copy reference payloads.

Snapshot failure must not rerun a reducer/checker or mint a partial token. Token
construction can report an error while the application retains its actual turn.
Native panic/exception containment and clear-on-error behavior are explicit native
extensions: Rust clears observed storage on returned `Result::Err`, not on an
uncaught panic/unwind. Existing revocable check sinks still expose the full prior
prefix while a checker runs and must expire normally after return/error.

Sequence representation must preserve Rust's full positive u64 domain. Existing
TypeScript number-based observed checking cannot simply accept unsafe integers;
provide an exact bigint checked API or explicitly limited compatibility wrapper,
with no silent rounding. Validate native policy period positivity; Rust encodes
it as `NonZeroU64`, so period-zero rejection is a native configuration boundary.

## Finite qualification vectors

Use actual public Rust APIs, including [checked_replay tests](../../tests/checked_replay.rs)
and [core review regressions](../../tests/pr_review_core.rs), to derive shared
semantic fixtures. At minimum cover:

1. Supplied noninitial checkpoint, zero `initial_state`/reducer calls, exactly one
   state check, empty/ordered/passed/failed/skipped batches, and no codec requirement.
2. Supplied accepted/rejected/ignored turns, zero reducer calls, exactly one
   applicable state/transition check, state-first ordering, exact batch boundary
   and positive sequence accessors. Failed properties still yield complete tokens.
3. State period two at matching/nonmatching sequences and disabled transition
   checking: explicit skipped markers and `full_checking == false` in every case.
   Cover sequence zero, u64 maximum, unsafe TypeScript number inputs, and native
   invalid policy configuration without fabricating Rust period-zero execution.
4. Initial/state/transition append errors after valid prefixes, malformed checks,
   snapshot errors and contained native exceptions: no token, no check/reducer
   repetition, original application transition still available. Keep diagnostics
   separate from certification.
5. Mutation attempts on checks/context/coverage/sequence, retained diagnostic
   views, forged objects/default values and cross-token replacement. Verify the
   chosen ownership contract and that merely obtaining a diagnostic view cannot
   authorize unchecked recording. Do not pretend tests defeat hostile reflection.
6. When a real recorder is added, attach origin zero/nonzero, no initial recheck,
   no turn recheck, full-coverage acceptance, sampled-coverage rejection, duplicate/
   reordered/gapped sequences, origin overflow, metadata mismatch and continuity
   failure. Feed one valid token to two independent recorders; reject a duplicate
   in the same recorder by sequence, not a globally consumed bit.
7. Recorder failure freeze, initial/property-failure freeze, bounded encode failure,
   exact phase boundary after eviction, and coherent artifact replay. These are
   later consumer gates; token-only tests do not establish their completion.

Exercise installed package APIs and current legacy suites in all four languages.
Submit executable qualification through Kyoto and retain exact source identities,
requested/effective commands and executed results. Distinguish static API sealing,
native runtime ownership, host/runtime scope, and full recorder/evidence parity.

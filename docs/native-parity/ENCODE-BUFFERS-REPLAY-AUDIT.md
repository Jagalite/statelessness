# Bounded encoding and rich replay: next implementation audit

Static audit, 2026-10-20, inspected worktree HEAD
`3c05c21e42b991b77651e1c898bc0dbefe6a1773` plus the current append-check candidate.
This document proposes implementation and qualification scope; it does not claim
these features exist or are qualified. No builds or tests were run for this audit.

## Dependency order and current boundary

Implement reusable bounded codec buffers and complete codec-hook dispatch first;
then implement rich replay and its bounded variant over a common replay kernel.
Basic rich observations can be developed alongside codec dispatch, but must not be
advertised as bounded replay before shared frame/aggregate accounting and artifact
prevalidation exist. Both variants need the append-check batch-completeness
contract. Keep existing native basic replay behavior and entrypoints compatible.

Current native codecs expose allocating encoders in Python
[ModelCodec](../../python/src/statelessness/core.py#L117), Go
[Codec](../../go/model.go#L102), TypeScript
[CodecModel](../../typescript/src/core.ts#L81), and Swift
[Codec](../../swift/Sources/Statelessness/Model.swift#L83). Their recording engines
apply native `TraceLimits` to allocated payloads. Their binary audit readers and
writers expose distinct `BinaryLimits` in [Python](../../python/src/statelessness/trace_binary.py#L62),
[Go](../../go/audit_binary.go#L30), [TypeScript](../../typescript/src/binary.ts#L14),
and [Swift](../../swift/Sources/Statelessness/AuditTrace.swift).
Neither an allocating blob-length check nor a bounded binary reader implements
incremental model encoding or bounded actual replay evidence.

## EncodeBuffer contract

Use actual [Rust EncodeBuffer](../../src/model.rs#L164) behavior as the reference:

- Construction clears the logical destination while retaining its existing
  allocation. The maximum bounds accepted encoded length, not the capacity of an
  already larger backing allocation. Each append checks addition/limit before
  growth and rejects the entire incoming chunk rather than appending a prefix.
- Limit or buffer-allocation failure is sticky. Once failed, later appends fail,
  and `finish` fails even if a codec swallowed an earlier append error. Do not
  expose a reset operation to application codecs.
- `framed` reserves an eight-byte little-endian length prefix, calls the nested
  encoder against the same destination/budget, checks finish, then patches its
  length. Nested callback failure marks the buffer failed. This operation is not
  transactional rollback: failed partial bytes must never be published as a
  successful encoding.
- The compatibility `extend_from_vec` path validates length before accepting an
  already allocated payload; Rust may move it into an empty destination. Its
  source allocation happened outside the sink's limit. Native implementations
  may copy for ownership safety and must document that distinction.

Add optional `encode_state_into`, `encode_input_into`, `encode_output_into`
(language-appropriate camel-case/exported variants) corresponding to
[ModelCodec defaults](../../src/model.rs#L424). Present hooks take precedence;
otherwise invoke the allocating encoder once and append its result. Keep existing
model constructors and required legacy encoder surfaces compatible unless a
separate explicit extension supports append-only codecs. Every engine call must
finish the sink after the callback returns; callback success does not override a
sticky rejected write. An arbitrary callback error is an engine error regardless
of finish state; do not invent a stronger Rust panic/unwind guarantee.

Prevent retained sinks from writing into a reused buffer. Native references need
per-callback revocable leases or an equivalent proven ownership mechanism, revoked
on every exit path. Nested framed callbacks must not escape with a still-active
view after returning, while the enclosing callback remains legitimately active.
Do not expose mutable backing storage or a writable length-prefix patch facility.
Copy incoming mutable byte views where necessary; later caller mutation must not
change encoded evidence. Rust borrowing supplies these guarantees statically;
Python/TypeScript/Go/Swift public objects do not automatically do so.

### Oracle and wrapper forwarding

Add [OracleCodec::encode_history_into](../../src/oracle.rs#L45) with the same
legacy allocation caveat. [WithOracle encoding](../../src/oracle.rs#L271) writes
`ORC1`, then model and history components as two nested u64-framed encodings into
one budget; it must not first allocate two unbounded component blobs. Preserve
exact existing checkpoint bytes, strict decoding and component order. Forward
input/output encoding into hooks to the underlying actual model and reference
history hooks through Paired. Preserve optional hooks through generated/automatic
wrappers, receiver binding, and nested WithOracle composition.

Enumeration, fuzz and shrink do not need codecs merely to execute. They must
remain usable with non-codec models; generation wrappers should preserve codec
capabilities when present without making them mandatory. Binary/JSON serializers
consume encoded bytes; they must retain their own format quotas and must not
reexecute model codecs to reconstruct historical data.

## Recording and budget integration

Route all initial-state, input, output and post-state model encodings through the
new dispatch in native recording and replay. Audit both initial and per-turn
paths in [Python core](../../python/src/statelessness/core.py),
[Go execution](../../go/execution.go), [TypeScript core](../../typescript/src/core.ts)
and [Swift execution](../../swift/Sources/Statelessness/Execution.swift).
Existing `TraceLimits` count portable evidence/payload/JSON bounds;
`BinaryLimits` cover the exact STLESS wire structure. Do not silently equate their
different fields or replace one with the other.

For binary-equivalent bounded recording/replay, implement the complete nine-quota
[Rust ReadLimits](../../src/trace.rs#L75) semantics, including RunConfig ordered
parameters, run/step/end framing, UTF-8 byte lengths, checks, outputs, footer
reservation, aggregate items and checked arithmetic. Default recording already
uses these limits in [Rust](../../src/execution.rs#L195). Preserve a coherent
record prefix on post-checkpoint errors and do not commit an over-budget step.
An explicit new AuditTrace recording path may be needed because portable Trace
does not carry all RunConfig data; merely converting a finished portable trace
cannot bound its earlier codec work or recover discarded metadata.

## Rich replay: complete evidence contract

The authority is [ReplayOptions through ReplayError](../../src/execution.rs#L495)
and the [actual shared kernel](../../src/execution/replay_kernel.rs#L218).
Introduce basic and bounded rich APIs with these contracts together:

1. Check identity (model/property/codec versions and build policy), preserve
   `build_matches`, and return incompatible rather than treating a foreign trace
   as replayed. Exact replay of a bounded/error prefix says nothing about whether
   the original run was complete.
2. Decode initial state and inputs, re-encode canonically, and reject noncanonical
   encodings. Stored outputs remain encoded; typed actual outputs come only from
   the current execution. Do not add an unnecessary historical-output decoder.
   Initial noncanonical state is an engine error, not a state divergence.
3. Emit a sequence-zero initial observation after successful canonical checking;
   initial check differences use the existing initial-check divergence semantics.
   Emit every attempted successfully compared turn, including the first divergent
   turn, with typed before/input/transition, combined checks, state-check boundary,
   stored expected data, actual encoded data and all available difference flags.
4. Compare disposition, ordered outputs, state and ordered checks. Report all four
   flags while choosing the primary divergence in that order. Increment verified
   steps only for an exact turn, update the report before calling the observer,
   and stop at the first divergence without executing the next input.
5. `failure_reproduced` means an expected failed check ID failed again at the
   corresponding boundary; details may differ and exact replay may diverge. It
   does not mean every recorded failure was reproduced or that execution passed.
6. Error evidence distinguishes no actual result from actual initial state or an
   already produced turn. Decode/canonical-input/reducer errors before a turn
   exists retain no turn result. Once a reducer returns, retain its actual before,
   input and transition without rerunning it. Checker failure retains that turn
   with checks/boundary unknown; later encoding/quota failure retains completed
   checks and their boundary. Unknown checks are not an empty passing batch.
7. Engine errors invoke an error observation with sequence, primary error and
   available actual result. If this notification fails, retain it separately as
   `observer_error`; it must not replace the engine error. An initial/turn observer
   failure directly becomes a replay-observer error with completed actual evidence
   and updated report; Rust does not recursively send it to the error observer.

Rust rich observations borrow engine storage synchronously. Native APIs must
choose detached snapshots or enforce explicit callback-scoped views; escaped
observations must not mutate future replay or become silently overwritten by
buffer reuse. If snapshotting itself fails after execution, preserve the actual
result under a documented recovery ownership rule rather than replaying it. No
extra serialization, Send/Sync, or Debug requirement should narrow allowed models.

Replay has no direct `RunLimits` parameter in Rust. Cancellation/deadline callers
stop via initial/turn observer errors at those boundaries. Do not implement a
different stop algorithm and claim matching replay control semantics.

## Bounded replay is additional work, not just a blob cap

[replay_with_observations_bounded](../../src/execution.rs#L684) validates the stored
artifact against format limits before decoding, after identity checking. It then
accounts actual observations independently using those limits. Initial run bytes,
parameters/checks, file header and reserved footer count. Each turn preflights
fixed framing, input, disposition strings, check batch and item counts before
allocating encoded-output storage; all outputs and state then share the remaining
frame and total budgets. Divergent actual values are charged too. See
[replay_step_preflight](../../src/execution.rs#L723) and
[kernel accounting](../../src/execution/replay_kernel.rs#L398).

The effective blob cap is bounded by blob, frame, wire u32 and total limits, then
further reduced by remaining per-frame/aggregate bytes during actual output/state
encoding. Independent per-output caps cannot substitute for this shared budget.
Existing unbounded replay entrypoints retain their compatibility behavior.

## Qualification gates

- Derive shared witnesses from public Rust APIs: empty/exact-fit/over-limit chunks,
  ignored failed writes, sticky finish, nested framing and nested callback errors,
  u64 lengths, arithmetic overflow, mutable input aliases and expired sink reuse.
  Check exact ORC1 bytes with incremental actual/history encoders and all wrapper
  forwarding combinations. Legacy fallback must execute once and remain supported.
- Prove every record/replay initial/per-turn encoding path uses present hooks.
  Assert callback counts and order, coherent recording prefixes and noncommitted
  over-budget turns. Test actual installed public package APIs, not source imports
  alone. Keep non-codec generation/BFS/fuzz/shrink models working.
- Exercise exact, incompatible and initial/turn divergence; simultaneous four-flag
  differences; failure-ID reproduction with differing details; first-divergence
  stopping; canonical decode failures; every reducer/checker/encoder error stage;
  absent versus incomplete versus complete actual checks; observer failure and
  secondary error-notification failure. Assert no reducer/checker repeats.
- Test each quota independently and in aggregate, including parameters, non-ASCII
  string bytes, footer headroom, divergent oversized actual outputs, output-count
  preflight, initial-frame overflow, exact boundary fits and actual aggregate
  exhaustion. Assert malformed/over-limit artifact rejection precedes decoding.
- Test snapshot/view ownership, mutable byte aliases, retained observers and sinks
  across errors and reuse. Account for JavaScript safe integers, Go/Swift native
  integer widths, Python arbitrary integers, and runtime-specific allocation error
  behavior. Swift traps/process OOM are not catchable model errors; managed heaps
  and backing capacity can exceed logical byte limits.
- Submit builds/tests through Kyoto and retain requested/effective commands,
  source identities, Rust-reference execution and native installed-consumer
  evidence. Legacy codecs, reducers, checkers, decoders and snapshots allocate
  outside incremental sink limits. Do not claim hard RSS, arbitrary callback
  preemption, zero allocation, browser qualification or cross-platform parity
  without separate evidence matching that scope.

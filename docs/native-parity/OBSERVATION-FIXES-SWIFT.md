# Swift observation round 2 corrections

Source-only implementation on 2026-10-22 in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, based on
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`. These changes address Swift R2 in
[the TypeScript/Swift review](OBSERVATION-REVIEW-TSSWIFT.md), its arbitrary-byte
coverage gap and two existing Recorder P2 findings, and Swift R6 in
[the proof review](OBSERVATION-REVIEW-PROOF.md). They are uncommitted candidates,
not a source freeze or qualification receipt.

Applicable ancestor/in-tree `AGENTS.md` searches found none. The requested
`/Users/jagatranvo/.codex/skills/rust-builds-kyoto/SKILL.md` was read. No build,
compile, test, implementation execution, fixture generation, executable check,
Kyoto submission, staging, commit, push, or PR was performed. The parent owns the
integrated source freeze and shared Kyoto qualification. No other worktree or
checked-consumer source was edited.

## Formatter error provenance (R2)

[Observation.swift](../../swift/Sources/Statelessness/Observation.swift) now gives
each formatter lease a private, immutable identity retained by its errors.
`ObservationBuffer` record failures and `EncodedPayloads` codec failures carry
that identity. The formatting boundary preserves their stage only when the error
belongs to that exact lease. Identity is distinct between payload calls, nested
observers, and successive events; retaining an old error also retains its identity
and cannot cause address reuse to authenticate it later.

Public `TextObservationError(stage:detail:)` remains available but creates no
internal provenance. Such values, errors forwarded from another observer, and
errors retained from an earlier operation are contextualized to the current
`formatInput`, `formatOutput`, or `formatState` callback. The change neither
poisons the outer writer nor publishes a partial record. Current-operation record
limits, missing-codec errors, encoding limits, and ordinary codec failures keep
their existing stages. Writer/flush poison and health-before-Off behavior are
unchanged. No model or checked-result authority is represented by this identity.

New source-authored cases in
[ObservationTests.swift](../../swift/Tests/StatelessnessTests/ObservationTests.swift):

- `testPublicStagedErrorsAreAttributedToTheCurrentFormatter` covers each outer
  payload callback against all twelve public error stages. It asserts the actual
  callback prefix, outer stage, zero writer calls/bytes, Off health, successful
  recovery bytes, no implicit flush, and zero forbidden model calls.
- `testNestedObserverErrorsDoNotEstablishOuterFailureStagesOrPoison` forwards real
  nested observer sink-write, record-limit, and state-codec failures from each
  outer payload position. It checks both observers' independent health and writer
  counts, callback cutoffs, and outer recovery.
- `testRetainedRecordErrorCannotAuthenticateALaterFormatterCall` first obtains a
  real current-lease record failure, then throws that error in a later event.
  Only the first operation has record provenance; the second reports input
  formatting failure and the observer subsequently recovers.

Existing swallowed-limit, codec-stage, missing-codec, declaration-version,
poison/reentrancy, snapshot-policy and checked-authority tests remain in place.
The existing `.every(1) != .everyTransition` distinction was preserved.

## Arbitrary payload bytes and record bounds

`testArbitraryPayloadBytesRespectExactRecordAndFinalNewlineBounds` writes the
literal payload `[0xff, 0x00, 0x0a]`. Its expected destination is a source-authored
ASCII header plus those three bytes and the record's final LF, compared as
`[UInt8]` without replacement decoding.

The exact bound admits all bytes. One byte less admits the payload but rejects
the final LF; two bytes less rejects the whole payload write. Both rejected
records have zero destination calls/bytes, preserve health, and permit a later
smaller record. No implementation output was used to generate these expectations.
The byte-writing implementation itself already appends bytes directly and was
retained.

## Declaration bypass control (proof R6)

The declaration branch in
[the Swift consumer](../../examples/native-observation/swift/Sources/ObservationConsumer/main.swift)
now initializes direct incremental model codec hooks that increment the forbidden
counter and throw. Legacy encoders already throw. Only the declaration adapters
supplied through `TraceRecords` / `ModelCodecBinding` can produce the expected
bytes. Omitting the binder assignment therefore fails the consumer instead of
silently reaching an equivalent direct encoding path. Successful declaration
callback names/order and fixture bytes remain unchanged.

`testDeclarationAdaptersAreRequiredWhenDirectCodecHooksFail` exercises an unbound
model with throwing hooks, expects `encodeInput` failure without writes, then
binds real declaration adapters and recovers with literal input/output/state
bytes `11`, `33`, `22`. The original direct-hook count cannot grow during the
successful bound observation. Existing preferred-dispatch and version-gate
coverage remains intact. A consumer mutation/installed execution is still a
parent qualification gate; this source change does not claim it ran.

## Existing Recorder P2 corrections

[Recorder.swift](../../swift/Sources/Statelessness/Recorder.swift) has two narrow
changes against the base:

1. Initialization validates the reserved error-footer payload immediately after
   metadata and before checkpoint encoding or checking, including when initial
   checks would fail. Its payload bound is `13 + executionErrorTextLimit(limits)`:
   the `u64` count, termination byte and `u32` text length require at least 13
   bytes. This follows `src/monitor.rs:213` and `src/trace.rs`'s
   `reserved_end_size`; final run/retention budget validation remains in place.
   Existing detached checkpoint ownership and authentic checked handoffs remain
   intact.
2. Turn preparation checks output count against `maxOutputsPerStep` first and
   `maxItems` second, reporting `outputs per step` and `aggregate items`
   respectively. This follows `src/monitor.rs:424-428`, after encoded before-state
   continuity and before input encoding/checking. Aggregate accounting for the
   entire candidate and retained trace is still performed separately.

New cases in
[ObservationRecorderIntegrationTests.swift](../../swift/Tests/StatelessnessTests/ObservationRecorderIntegrationTests.swift)
cover:

- Footer frame bounds 0, 12, and 13; competing metadata, encoder, checker and
  property failures; callback counts; and metadata error precedence.
- Authentic passing/failing checked checkpoints with an impossible footer, no
  recheck, and reuse of the same certificate after failed admission.
- Ordinary and checked turns at separate output/item limits, both exceeded,
  zero outputs allowed, equality boundaries, and continuity precedence.
- Aggregate items including the stamped run parameters, checkpoint checks,
  per-step checks, and step count; exact admission and later rejection are
  distinguished from early output-count rejection by callback counts.
- Item-driven eviction and failure atomicity, retaining the coherent prefix and
  the state-check-only checkpoint boundary when a new candidate cannot fit.

The tests use existing checked tokens and Recorder APIs. No diagnostic event can
replace their coverage/sequence checks; no reducer/check/effect work was added to
logging. Existing long-window, periodic, missing-turn, replay, property-freeze,
codec/formatter/sink-failure and diagnostic-forgery tests were preserved.

## Adjacent source finding left for the parent

**P2, outside the assigned file ownership:**
[ExecutionBudget.swift:27](../../swift/Sources/Statelessness/ExecutionBudget.swift#L27)
counts outputs and validates check strings before adding the STEP item at line
38. Rust `trace::step_size` starts its item count at one. For ordinary observation
with 63 outputs, `maxItems = 64`, `maxOutputsPerStep = 128`,
`maxStringBytes = 64`, and exactly one returned check with a 65-byte ID, Rust
rejects aggregate items before inspecting that ID; Swift reaches the string-byte
error first. This is separate from the corrected early output-count category.

The initial correction handoff left `ExecutionBudget.swift` unchanged. Parent
integration subsequently admits the STEP item before collections, following
`trace::step_size`, and removes its duplicate late addition. The new
`ObservationBudgetPrecedenceTests.swift` uses the public Recorder with 63 outputs
and one 65-byte check ID: a 64-item cap must report aggregate items, while a
65-item cap reaches the string-byte limit. It asserts no output encoding and
preservation of the frozen checkpoint. This correction and regression remain
unexecuted and are included in the final source review and Kyoto gate.

Round-3 review found that output admission also needs to follow frame checks for
earlier fields. Parent now measures the STEP in wire order and enforces the frame
bound as each field is admitted. The added public regression supplies a 512-byte
input and 64 outputs with a 512-byte frame cap and 64-item cap: the input frame
must fail before output items, with zero output encodes and the original
checkpoint/configuration retained. This preserves the item-versus-string case.
The installed Swift consumer also counts every destination write/flush attempt
and verifies that the final Off probe and writer extraction change neither those
counts nor bytes or formatter callbacks. Both corrections remain source-only.

## Evidence boundary and handoff

The implementation was inspected against pinned `src/observation.rs`,
`src/monitor.rs`, `tests/observation.rs`, and monitor limit/review tests, plus
[N07](../NATIVE-PARITY-PLAN.md#n07-runtime-observation-and-bounded-recording).
[CHECKED-RESULTS-AUDIT.md](CHECKED-RESULTS-AUDIT.md) was treated as historical:
current `CheckedResults.swift`, periodic checking, and bounded Recorder APIs
already exist and were reused, not replaced or removed.

[swift/README.md](../../swift/README.md) describes formatter-local provenance,
raw-byte accounting, the declaration negative control, and Recorder ordering.
Only the six assigned Swift source/test/readme/consumer paths and this report
were edited. Concurrent unrelated edits were preserved. All compiler,
runtime/native-suite, installed-package, deliberate bypass, and full N07 claims
remain pending the parent's source-bound Kyoto qualification.

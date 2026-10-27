# Causal evidence envelopes and recording: next-scope audit

Static audit, 2026-10-20, worktree HEAD
`d35a546410a717d61c031c77e0e82c4153786488` plus current checked-result/runtime-recorder
qualification work. Rust [evidence.rs](../../src/evidence.rs) and
[value_codec.rs](../../src/value_codec.rs) are unchanged from pinned baseline
`0876717bee85e335449c2c99ada41f6c6e6112d1` (scoped Git diff checked).
No builds or tests were run for this audit. This is a proposed implementation and
qualification scope, not an assertion of native causal-evidence parity.

Current independent native recorders are Python [monitor.py](../../python/src/statelessness/monitor.py),
Go [recorder.go](../../go/recorder.go), TypeScript
[runtime_recorder.ts](../../typescript/src/runtime_recorder.ts), and Swift
[Recorder.swift](../../swift/Sources/Statelessness/Recorder.swift). None currently
provides the Rust causal envelope types or EvidenceRecorder. New checked tokens,
runtime recording, bounded codecs and rich replay are prerequisites, not full
N05/N11 completion. See [checked-result audit](CHECKED-RESULTS-AUDIT.md),
[encoding/replay audit](ENCODE-BUFFERS-REPLAY-AUDIT.md), and
[overall status](../NATIVE-PARITY-STATUS.md).

## Complete public data and wire scope

Implement all [evidence data types](../../src/evidence.rs#L24), preserving owned
values, field order, integer widths, optional absence and ordered collections:

- `Provenance`: run string, epoch u64, optional manifest/source, ordered environment
  string pairs. Names must be nonempty and unique; values and run may be empty.
- `OutputOrigin`: absolute host sequence u64 and zero-based output index u32.
- `CausalContext`: optional machine, generation u64, correlation and logical-time
  u64, plus ordered causes. Logical time is modeled time, not a telemetry clock.
- `StepEvidence`: absolute sequence u64, state-check boundary u32, causal context.
- `TraceV2`: exact v1 trace, checkpoint-after u64, provenance and one StepEvidence
  for every retained step. `Artifact` distinguishes original v1 from v2 and exposes
  the embedded trace without manufacturing missing legacy phase information.

The [v2 envelope](../../src/evidence.rs#L315) is exact: STLESS magic, little-endian
version two, two zero reserved bytes, embedded-v1 length u64, metadata length u64,
unchanged complete v1 artifact, metadata, and CRC32 of all preceding envelope
bytes. Total overhead is 32 bytes. Metadata is canonical structural encoding of
checkpoint-after, provenance, then step vector using the actual
[value codec](../../src/value_codec.rs): fixed little-endian integers, strict
option tags, u32 UTF-8/string and collection lengths, no JSON substitution.
Preserve u64 values in all native languages, including exact TypeScript bigint.

Expose bounded Artifact reading, TraceV2 validation/writing/raw-new export,
phase lookup, verified v1 upgrade and replay. Bounded byte APIs are useful but do
not by themselves replace the reader/writer and filesystem APIs. Parsing must run
no model and acquire no ownership of host application resources. Snapshotting
returns independent owned evidence; moving/consuming export must have a documented
native equivalent. Copy incoming mutable provenance/context collections so later
caller mutation cannot rewrite retained history.

## Validation and quota requirements

[EvidenceLimits](../../src/evidence.rs#L60) combines all nine v1 trace quotas with
metadata bytes (default 4 MiB), environment label count (1024), individual UTF-8
label bytes (4096), causes per step (32), and total retained causes (100000).
Increasing one does not implicitly increase the others. Labels and CRC are
assertions/integrity checks, not authentication or verification of host claims.

[TraceV2 validation](../../src/evidence.rs#L217) validates provenance, v1 first-
failure/termination semantics, exact v1 wire quotas and complete-envelope size.
Require a one-to-one step metadata mapping, sequences equal checkpoint + local
index + one with checked overflow, and state-check boundary at most batch length.
Each cause must be unique, positive and strictly earlier than its input sequence.
For sources after the checkpoint, validate the referenced retained output index.
Sources at/before the checkpoint may be omitted-prefix links; do not invent their
outputs or reject them merely because their payloads were evicted.

[Artifact::read](../../src/evidence.rs#L422) bounds the full read, rejects invalid
magic/version/reserved fields, overflow/inexact lengths, extra/truncated bytes,
CRC mismatch and over-limit metadata before decoding it. Decode metadata with
independent bytes/allocation/elements/depth/work accounting: Rust uses metadata
bytes, saturated 16-times bytes for allocation/work, bytes for elements and depth
16. These are structural decoder budgets, not the embedded v1 item quota; do not
reject valid metadata by incorrectly sharing the latter. Require complete decode
with strict UTF-8/options and no trailing metadata.

## EvidenceRecorder: coordinated retention and failure semantics

[EvidenceRecorder::new](../../src/evidence.rs#L507) accepts a sealed checked initial
result, absolute origin, RunConfig, runtime RecorderOptions, provenance and limits.
Reserve **all** selected metadata allowance plus 32-byte envelope overhead from
the smaller retention/format-total budget before creating the underlying recorder.
Provenance encoded size plus 12 bytes (checkpoint and step-vector prefix) must fit
the metadata budget. Small retention needs a correspondingly smaller configured
metadata allowance; do not silently shrink it or change defaults to force fits.

[observe](../../src/evidence.rs#L544) accepts only sealed complete CheckedTurn and
owned causal context. It never runs reducers/checkers. Preflight context, checked
u32 phase boundary, metadata encoding size and aggregate cause counts; plan enough
oldest metadata evictions to fit. A candidate too large even after planned
evictions fails. Resolve retained causal origins against actual retained output
counts **before** acceptance, using the current checkpoint; planned eviction does
not excuse a currently invalid output reference.

Coordinate those minimum evictions with the underlying recorder's byte/step
evictions atomically. Only after successful recorder commit may the phase journal
drop exactly the same evicted prefix and append the candidate. Updating metadata
first and failing trace commit would corrupt evidence. Snapshot checkpoint is
origin + actual recorder evictions, and trace/phase vectors must remain aligned.
Preparation errors freeze the underlying coherent prefix via stop-with-error;
already-frozen recording rejects new observations and preserves the first terminal
reason. Initial/property failure remains property failure. Allocation failures
must not trigger reexecution or fabricate a complete evidence row.

Native recorders therefore need real internal support for minimum planned
evictions and retained output-count lookup. A separate metadata ring beside an
unmodified recorder is insufficient unless commit/eviction synchronization is
proven. Do not make these unsafe bypasses public merely to simplify implementation.

## Replay, legacy upgrade and phases

[check_phase](../../src/evidence.rs#L275) uses local sequence zero for checkpoint
initial checks, then persisted per-step state boundaries. Invalid positions return
no phase. V1 artifacts know initial-state phases only; later checks remain unknown
legacy, regardless of check ID naming.

[TraceV2::replay](../../src/evidence.rs#L180) validates evidence then runs bounded
exact replay. At an otherwise exact turn, compare the actual state-check boundary
with persisted phase. A mismatch returns divergence `check_phase`, verified count
before that turn, and failure-reproduced false. Existing semantic divergence takes
precedence; do not overwrite it with a phase mismatch. Host causal labels are not
executed or authenticated by replay.

[verify_upgrade](../../src/evidence.rs#L347) is an explicit execution operation:
require exact compatible bounded replay, derive boundaries from actual completed
checks, checked-add checkpoint origin, default empty causal contexts and validate
the result. Never infer boundaries while passively reading v1 or silently replay
an application just to display/export an artifact.

## Capture/export policy and filesystem boundary

Pinned evidence.rs exposes `ExportPolicy`, not a separate `CapturePolicy` type.
Runtime capture policy is expressed through recorder retention/options and host-
supplied observations; do not invent an implicit sampling/filter policy that
drops turns or changes checking and call it matching capture behavior.

[diagnostic_json](../../src/evidence.rs#L750) is explicitly nonreplayable schema
`statelessness.diagnostic-export/1`. Omit payloads, details, identity/build and
causal labels; only explicit allowlisted property IDs survive. Preserve statuses,
phase/unknown-legacy, local sequence, termination mapping, checkpoint/omitted-prefix
semantics and truncation flag. Initial observation remains included even at zero
step allowance. Enforce whole-output byte cap and correct escaping. This export
is distinct from raw replayable evidence, not a redacted v2 substitute.

[raw and diagnostic new-file exports](../../src/evidence.rs#L702) precompute bounded
bytes, create exclusively without overwriting, set Unix mode 0600 and sync the
file. Failure after creation may leave an incomplete new file with an explicit
error; Rust does not provide rename-atomic publication or directory fsync here.
Match host-platform guarantees honestly. Browser TypeScript cannot expose an
ordinary filesystem equivalent; provide core bytes plus explicit Node adapter,
and qualify browser scope separately without an implicit download/publication.

## Finite qualification gates and implementation order

1. Implement canonical metadata schema/validation and exact envelope reader/writer
   first, with Rust-produced cross-language fixtures in both directions. Cover
   empty/full optional fields, non-ASCII labels, maximum u64/u32, ordered causes/
   environment, unsupported tags, malformed UTF-8, corrupt CRC, all length fields,
   trailing bytes and every independent quota at exact-fit/over-limit boundaries.
2. Implement owned TraceV2/Artifact and phase-aware replay/explicit upgrade. Cover
   missing/extra phase rows, invalid boundary, checkpoint overflow, v1 unknown
   phases, exact upgrade, incompatible/noncanonical/divergent upgrade rejection,
   identical checks with changed phase and simultaneous semantic/phase mismatch.
3. Integrate EvidenceRecorder with real checked tokens and recorder internals.
   Use counter assertions for no reducers/checkers, nonzero origin, cause links
   inside/outside retained prefix, duplicate/future/zero origins, invalid retained
   index, metadata-only/cause-only/trace-only and simultaneous evictions. Assert
   journal/trace alignment and artifact replay after every commit and error.
4. Test reservation too small, oversized single candidate, byte/count overflow,
   allocation/snapshot failure boundaries, initial and later property failure,
   first-error freeze, mutation of retained caller inputs and independent snapshots.
   Distinguish application allocations and native fatal OOM/traps from bounded
   structural requests; no hard RSS or asynchronous thread-safety claim follows.
5. Implement diagnostic and exclusive filesystem exports separately. Assert every
   sensitive field is absent, IDs require exact allowlisting, bytes/steps cap,
   unknown-v1 behavior, no overwrite, private Unix permissions, file-sync errors
   and truthful incomplete-file diagnostics. Test browser core/Node adapter scope
   according to the actual package/runtime support.
6. Qualify all four installed native packages and actual Rust APIs through Kyoto,
   preserve exact source identities and executed-command receipts, and rerun
   relevant legacy/runtime suites. None of token construction, green monitor
   tests, binary v1 compatibility, or a native v2 round trip alone closes full
   causal evidence interoperability, N05, N11, or the overall N01–N14 plan.

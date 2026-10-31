# replay-evidence-v1 (candidate)

Experimental rich and bounded rich replay plus full audit recording. This extends
independent native APIs without promoting a profile or changing legacy portable
wire projections. Authority: public Rust execution replay APIs and their shared
kernel; full binary limits remain the exact native-binary-v1 contract.

## Order, identity, and semantic artifact admission

Start report exact, steps_verified0, failure_reproducedfalse, build_matchesfalse.
Read model metadata once; compute build_matches, then compare name/model/property/
codec versions and build policy. Incompatible returns without decoder or observer
calls, even if the artifact is otherwise invalid. allow_build_mismatch permits
only build differences and leaves build_matchesfalse. Metadata callback errors
are a native extension where the language permits them; Rust Model::metadata is
infallible and a panic is not converted into ModelError.

Bounded replay next prevalidates the entire stored AuditTrace against all nine
BinaryLimits through the real binary format, before decoding any model value.
Both rich variants then validate recording semantics: no steps after an initial
failed check; no later turn after a nonfinal failed check; property_failed iff
initial or last-turn checks contain failure. A valid model_error prefix with no
failed check is allowed, but replay cannot reproduce/verify its terminal error.
A corrupt semantic record is an engine error with sequence0 and absent actual.
Binary structural validity alone does not imply semantic replay admissibility.

Decode the initial checkpoint; do not call model.initial_state. Re-encode it
canonically before checking. Noncanonical initial state is an engine error, not a
state divergence. Then check initial state, account actual run quotas if bounded,
and compare initial checks. Initial observation sequence0 includes typed state,
complete checks, expected/actual encoded state, expected checks, all difference
flags, and the updated report. Only checks can differ at this boundary; its
primary field is initial checks, divergence_step is null. An initial observer
error retains completed initial evidence and does not invoke the error observer.

For each stored turn, decode input, re-encode canonically, then call step once.
Input decode/encode/noncanonical errors and reducer errors retain no produced turn.
After a reducer succeeds retain its before/input/actual transition immediately.
Check state then transition with default full policy. Checker failure retains the
produced turn with checks=null and state_check_count=null, even if the state batch
completed before the transition checker failed. This is not the search-partial
batch contract. After both checkers complete, later preflight/codec/quota errors
retain complete checks and the boundary count; do not rerun anything for recovery.

Encode ordered actual outputs, then post-state. Compare every available difference:
disposition, outputs, state, checks. Report all four flags; primary divergence uses
that order. Inputs have no divergence flag: noncanonical input is an engine error.
Increment steps_verified only for an exact turn. Update report before invoking
turn observer, including the first divergent turn. Stop after that observer or
its error without executing the next stored input. Turn observation contains typed
before/input/full transition, combined checks and state boundary, stored expected
TraceStep, actual encoded input/outputs/state, differences and updated report.
Stored outputs remain opaque bytes; no historical-output decoder is required.

failure_reproduced matches a failed check ID in expected and actual initial batch
or the same combined STEP batch. It ignores descriptions **and phase**; moving a
failure from state to transition within one turn still reproduces that ID. It does
not require exact replay or reproduction of every recorded failure. Initial flag
updates after actual run quotas. Turn flag updates after preflight/aggregate quota
checks but before output allocation/encoding; thus a complete-check preflight error
can leave it false while a later output-encoding error can already set it true.

## Errors and observation ownership

An engine error invokes one error observation with sequence, primary error, current
report and available actual result. Its additional failure is observer_error and
never replaces the engine error. A normal initial/turn observer error is directly
reported as replay observer error with updated report and completed actual; do not
recursively call the error observer. Replay has no RunLimits parameter in Rust:
callers stop at observation boundaries through observer errors, not an invented
per-callback cancellation algorithm. Rust cannot catch arbitrary callback panics.

Native observations are detached snapshots or explicit scoped read-only views.
They must not alias reused encoded/check storage or mutable model state. No new
serialization/Send/Sync/Debug constraint may narrow model values. If snapshotting
fails after execution, do not retry cloning or repeat a decoder/reducer/checker.
Transfer the original retained actual to ReplayError under explicit recovery
ownership; its error observation may have actual=null plus snapshot_unavailable
and actual_available metadata, distinguishing an unavailable snapshot from no
produced result. This native recovery rule extends Rust's infallible borrows.
Synchronous observer return/error validation follows native append contracts.

## Bounded actual accounting

Use AuditTrace with full RunConfig and ModelError text; a portable Trace cannot
recover discarded metadata. Defaults and inclusive nine limits are exactly
native-binary-v1. Prevalidate the stored artifact independently, then charge actual
observations using current model metadata plus **stored** RunConfig (no restamp).
Initial accounting includes 12-byte header, RUN framing/payload/items, and exactly
18 reserved END bytes (9-byte frame overhead + u64 count + termination tag).
This is distinct from the recorder's larger reserved model-error footer.

Before actual output storage allocation, validate output count, complete check
batch, disposition UTF-8 byte reason, per-step items and fixed STEP payload:
8 index +4 input length +input bytes +1 disposition tag + optional4+reason bytes
+4 output count +4 post-state length +4 per output +encoded check batch. Aggregate
items add1 STEP +outputs +checks, after previously admitted actual run/turn items.
Bounded bytes retain prior actual frames, footer18, next frame overhead9. Remaining
body budget is min(frame_limit,u32MAX,total_remaining) minus fixed payload. Each
output consumes this shared remainder, then state consumes what remains. Effective
per-blob cap also includes blob/frame/u32/total limits. Divergent actual evidence
is charged too; independent per-output caps are insufficient. All additions,
subtractions, string lengths and collection counts use checked exact integers.

Full audit recording uses min(requested max_steps, limits.max_steps); an input
remaining at that boundary yields step_limit before encoding or stepping, rather
than a ModelError.

An oversized actual output/check/disposition/frame may fail before comparison;
retain its typed transition and completed checks, without inventing encoded bytes
or a completed turn observation. Quotas do not bound reducers, checkers, decoders,
legacy allocating encoders, snapshots, backing capacity, or process RSS.

## Full audit recording

recordAudit/record_audit is an explicit nine-quota recording path, not conversion
of a finished portable recording. Prefer into codecs for initial state, each input,
output and post-state, finish every sink, and preflight complete framed byte/item
budgets before committing a step. Keep the last coherent prefix on post-checkpoint
errors and write ModelError reason; initial checker/codec/limit errors fail capture.
Reserve footer room as Rust recorder does: reason allowance
min(256,max_string_bytes,max_frame_bytes.saturating_sub(13)), UTF-8-boundary truncation
and optional ... when truncation with allowance>=3; include all framing/footer bytes.
A property-failing initial/turn uses its actual smaller property_failed footer.

Reject caller parameter keys beginning stateless.engine. before recording. Preserve
ordered caller pairs, then append stateless.engine.version and stateless.engine.build
in that order. Native version is its actual public package version (currently0.1.0);
build labels are native-python-v1, native-go-v1, native-typescript-v1, native-swift-v1.
These identify implementations, **not source fingerprints**; qualification receipts
pin source separately. Rust public recording uses its real core version/build.
Never forge another producer's stamp or strip it to force equal bytes. Replay
preserves producer config exactly without restamping. Static format goldens remain
independent of runtime engine provenance; fresh producer artifacts may differ.

## Shared finite vectors

`conformance/replay-evidence-v1.json` stores normalized AuditTrace values using the
binary-candidate shape: hex blobs, nullable variant reasons, ordered parameters,
exact decimal seed text. Fixture codec model metadata codec-fixture/1/1/1,
buildcandidate-v1; initial0, input1, step adds input and emits its base next value,
state check passed ok, transition check passed edge. Preferred into encoders write
canonical decimal ASCII; legacy encoders fail if called. Model variants alter
actual state/output/disposition/checks or inject a named callback error. Events
log metadata, decode_state:HEX, encode_state:N, check_state:N, decode_input:HEX,
encode_input:N, step:BEFORE:INPUT, check_transition:BEFORE:INPUT:AFTER,
encode_output:N, observe_initial, observe_turn:SEQUENCE, observe_error:SEQUENCE.

Expected normal result has report, observations, error=false, actual=null,
observer_error=false. An error result retains report and normalized ReplayActual
(initial/turn with nullable checks/boundary), observations, error=true, and whether
a secondary observer error exists. Normal observations carry the full typed and
encoded fields above; checks use {id,status,details}, dispositions {kind,reason}.
Difference keys are disposition/outputs/state/checks. Reports use outcome,
divergence_step,divergence_field,steps_verified,failure_reproduced,build_matches.
Native snapshot-unavailable extensions have additional API-specific tests; the
shared Rust vectors never fabricate failed borrows. Static expectations precede
actual public Rust executions and are not derived from engine recordings.

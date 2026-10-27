# checked-results-v1 (candidate)

This experimental extension covers complete checked results and the real runtime
Recorder consuming them. It does not promote the four portable profiles or claim
EvidenceRecorder/envelope parity. The reference is public `execution::check_initial`,
`execution::check_turn`, `observation::{CheckedInitial,CheckedTurn}` and
`monitor::Recorder`. Checking requires Model only; recording additionally requires
canonical codecs, not an output decoder.

A supplied checkpoint is checked once without calling initial_state or the reducer.
A complete ordered batch produces CheckedInitial, including empty batches and
failed/skipped properties. A returned checker error produces no token. The token
binds the model, supplied state and checks; state_check_count is the batch length.
A supplied already executed turn is checked without executing it. Sequence must
be positive u64. State checking runs when sequence modulo positive state_every is
zero; otherwise its batch contains skipped `stateless.state_checks`, details
`periodic checking policy`. Transition checking follows, even after a state
property failure. Disabled transition checking appends skipped
`stateless.transition_checks`, details `disabled by policy`. The boundary is the
length after the state batch. Checker errors suppress token creation, retain the
application's actual transition, and carry initial/state/transition stage context.
The state checker error prevents transition checking. Transition checker error
wins over an earlier failed property. Duplicate property IDs and order survive.

Full checking means period exactly one AND transition checking enabled. A sampled
period never certifies full checking even on a matching turn. U64 maximum and
values above JavaScript safe Number range are exact; compatibility Number APIs
must reject unsafe values. Zero period is a native configuration error; Rust's
NonZeroU64 cannot express it. observation() is diagnostic only. into_checks returns
the same complete ordered batch and cannot replace the token's context or coverage.

Rust tokens borrow context and enforce private construction at the type boundary.
They do not protect against interior mutation violating Model determinism. Native
implementations use private detached context captured before checking; callbacks
receive separate clones of this context, and getters/diagnostics return detached
copies. Check arrays and coverage/context cannot be replaced through supported
public APIs. A snapshot failure cannot mint a token or rerun a checker/reducer.
Clone fidelity and model purity remain caller contracts; checking does not require
hashing, equality, serialization or debug formatting. Python/JavaScript private
integrity is an API convention, not protection against hostile reflection. A
forged diagnostic cannot authorize recording. Tokens are not globally single-use:
the same valid token may feed independent recorders. One recorder enforces order.

## Runtime recording

Recorder retains a contiguous canonical trace window, never executes reducers,
outputs or schedules work. Ordinary construction accepts a supplied checkpoint;
checked construction uses its complete sealed batch without rechecking. Positive
max_steps must fit the format step limit; max_retained_bytes must be positive.
Caller config rejects `stateless.monitor.*` and `stateless.engine.*` keys. Append
truthful engine version/build in the existing recording order, then optional
`stateless.monitor.sequence_origin` for nonzero host origin, followed by monitor
max_steps, max_retained_bytes, checking=`full`, observed_steps, evicted_steps,
checkpoint_after_sequence. Last three values are local decimal counts. In
particular checkpoint_after_sequence equals local evicted_steps, not origin plus
that count. Origin remains separately preserved. Native build identity follows
encode/replay recording conventions; it must not impersonate Rust's build stamp.

Construction validates options/config, obtains metadata, computes a bounded error
footer reservation, encodes checkpoint, then validates/copies trusted checks or
runs ordinary state checking once. This encode-before-check order differs from
recordAudit. Even an initially failed checkpoint requires valid footer limits;
its admission budget then uses the actual property-failed footer. Initial failed
checks freeze immediately without a recheck.

observe_checked checks frozen status first, full coverage second, then requires
exact `origin + observed_steps + 1`, with checked u64 arithmetic. Any error freezes
the last coherent prefix as ModelError. Metadata equality is exact metadata
identity, not pointer identity. A distinct model with identical metadata is
permitted. Recorder storage is representation-independent: checked turns from
a distinct model with different state/input/output types are also admissible when
metadata and canonical bytes agree. A native generic facade must offer a genuine
representation-independent handoff; a pointer/type gate is stronger than Rust
and must not silently reject this compatible case. Encode before-state canonically and compare against the last committed
post-state or retained checkpoint; discontinuity freezes. Continuity cannot detect
an omitted turn that returns to the same state: reliable delivery remains the
application's obligation.

Preparation validates output counts and borrowed disposition reason before
encoding input. Trusted checks are quota-validated/copied with their boundary;
ordinary observe checks once using the same full checking path. Candidate outputs
and post-state share the remaining frame budget. Every format limit from
native-binary-v1 applies: total/frame/blob/string/steps/checks/outputs/parameters/
aggregate items. Active admission reserves the bounded ModelError footer; a
failing candidate uses its actual 18-byte footer. max_retained_bytes counts the
complete encoded artifact, not RSS or callback allocation. retained_bytes reports
actual current serialization, including its actual footer, not reserved headroom.

Only after complete preparation may admission plan ring evictions. It measures
prospective counter strings and checkpoint metadata under all quotas, trying
windows from the required minimum eviction through the entire retained prefix.
At aggregate admission, error precedence is format total bytes, aggregate items,
format steps, retained byte budget, then retained step budget. Keep independent
format and retention caps: replacing them with their minimum loses the error
category. Per-field/frame encoding validation can fail earlier during candidate
preparation; writer preflight is not a replacement for this admission order.
Commit is atomic with respect to semantic evidence: failure leaves the old
checkpoint/steps/counters coherent. Promoting an evicted turn copies only its
state-check prefix to initial checks; transition checks never become checkpoint
checks. Successful commit increments observed_steps and eviction count and appends
the candidate. Property failure freezes only after successful admission. A failing
property whose candidate fails encoding/quota/admission freezes as ModelError,
retaining the earlier prefix.

Active termination is Interrupted. First committed property failure or recording
error freezes capture. Later observe calls fail without changing evidence.
stop_with_error freezes an active recorder without capturing another turn; on a
frozen recorder it returns the incoming error while preserving the earlier
terminal artifact. Snapshot/ownership transfer/direct writer exports preserve
all producer configuration. An artifact with property failure has no continuation.

## Finite shared corpus

`conformance/checked-results-v1.json` contains static expectations authored before
reference execution. Env `STATELESS_CHECKED_CORPUS` selects it for native tests.
`cases` check supplied checkpoint/turn contexts with no codec. Model fields provide
ordered state_checks/transition_checks and optional returned errors; check entries
use id/status/details. Sequences and policy periods are decimal u64 strings.
Expected events include only checker invocation order, plus explicit zero initial
and reducer calls. Expected token is null on error; successful token includes full
context, complete checks, boundary and coverage. Native ownership/forgery/invalid
configuration tests supplement these typed Rust vectors.

`recorder_cases` supply checkpoint, model, origin, ordered config, options with
partial nine-limit overrides, and actions. Default entry/action uses sealed
checked results; entry_mode or action mode `ordinary` uses actual ordinary public
Recorder APIs. Each turn supplies its already executed context. Optional
checks_model changes its check batch, model_name tests same/different metadata,
encode_error injects a preferred codec error; representation=`decimal-string`
uses distinct string state/input/output types with identical decimal canonical
bytes (the expected semantic projection remains numeric); stop_error delivers an external
failure without a turn. Expected states describe every coherent checkpoint,
retained turn/check batch, committed/evicted counts, terminal kind, frozen flag,
and error substring. Per-step state_check_count guides expected checkpoint
promotion; public trace serialization itself contains the combined batch only.
Producer-dependent engine stamps remain truthful and are asserted separately,
not stripped from exported traces or normalized into invented shared bytes.

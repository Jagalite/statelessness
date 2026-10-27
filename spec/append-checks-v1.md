# append-checks-v1 (candidate)

Experimental independent-native check-storage candidate based on Rust CheckSink,
Model/Oracle append hooks, check_observed_into, search checking, and wrapper
forwarding. It does not promote profiles, close rich replay/evidence gaps, or
claim zero allocation, bounded RSS, or thread safety.

## Dispatch and append semantics

A CheckSink wraps existing ordered storage without clearing it. push/append and
extend add validated checks after its current prefix. len/is_empty/inspection
include the entire prefix, including state checks before transition checking and
actual-model checks before oracle/reference checking. reserve/additional capacity
must not change logical length or order. No removal, replacement, clear, mutable
backing storage, or mutable inspection reference is exposed.

Prefer an explicitly supplied append hook exactly once. Otherwise call the
legacy collection-returning checker exactly once and append its validated results
in order. Never call both or rerun a checker to recover diagnostics. Rust's trait
default appends only an Ok legacy Vec: a legacy Result::Err exposes no internal
collection. Native APIs can separately expose a returned prefix plus error (Go),
or a lazy iterator can emit before failing. Preserve only actually observable
valid prefixes. Existing legacy models and optional-empty transition checks stay
compatible. Rust still requires the legacy state method even when into is
overridden; native append-only models are an explicitly documented extension.

All execution paths must use shared preferred dispatch: initial checking, already
executed-turn checking, BFS, fuzz, shrink replay, record initial/turn checking,
and replay initial/turn checking. Codecs/trace serialization must never invoke
checkers. Ordinary collection-returning convenience APIs collect through the same
dispatch. Default legacy callbacks may still allocate their temporary collection.

WithOracle passes the same append position first to the actual model then oracle.
Paired oracle passes it to reference checks before projection comparisons. State
comparison appends pair.state; transition checking recomputes the reference turn,
checks it, then appends pair.effects and pair.outcome comparisons. Existing actual
and reference prefixes survive a later oracle/projection Result error. No reducer
or checker is repeated just to reconstruct partial evidence. Paired's documented
reference recomputation is distinct from accidental double execution.

## Context-specific error ownership

Direct append hooks preserve existing storage and any successfully appended prefix
when returning Result::Err. Search initial/turn helpers clear their reusable
buffer at each new boundary, but deliver actual partial diagnostic batches before
propagating checker error: initial/state incomplete; after a transition-check
error, completed state batch then incomplete transition batch. Both notifications
are attempted in native fallible-observer APIs; preserve checker error as primary
if notification also fails. Do not manufacture a normal completed observation on
these paths or convert an emitted failed check into successful error recovery.

check_observed_into checks a supplied already-executed transition and never calls
step. It clears caller logical storage at entry, including sequence zero, and on
every returned Result error. It preserves capacity where the runtime supports
that. State checks precede transition checks. If sequence is not a multiple of
state_every, append skipped stateless.state_checks / periodic checking policy;
if transition checking is disabled append skipped stateless.transition_checks /
disabled by policy. Policy skips are explicit evidence, never passing properties.
Rust state_every is NonZeroU64, so zero is unrepresentable; native validation must
reject it and clear the buffer. Rust does not catch unwinding panics, so a panic
may leave appended caller storage. Native contained panics/exceptions and snapshot
or configuration failures clear-on-error are an explicit stronger API extension,
not proof that Rust Result guarantees cover unwinding.

Recording retains only a coherent checked prefix: a turn-check error is stored as
model_error after the preceding committed turn, not a partial failing turn.
An initial checker error fails construction. Replay uses append dispatch but its
rich/bounded observation/error contracts remain a separate qualification scope.

## Native leases, validation, and ownership extensions

Rust's mutable borrow prevents sink escape and backing aliases during checking.
A native callback may save its sink object. Give each callback invocation a fresh
revocable lease and revoke it in guaranteed cleanup on success, callback error,
validation failure, or contained panic. Every operation on an expired lease
fails without accessing later engine storage. Do not reopen old leases. Nested
wrapper dispatch must issue child callback leases and expire them when that
callback exits while preserving the outer callback's still-valid lease.

Malformed check/extend/reserve validation has sticky first-failure semantics for
that active callback and its enclosing active batch, including nested child leases: if application code catches or ignores the operation error,
the dispatcher still rejects the batch and preserves only the valid prefix from
before the first failure for search diagnostics. Do not materialize an invalid
check or turn an ignored Go error into passing evidence. An error using an old
expired lease must not poison another fresh callback's active batch. These
malformed-runtime-value guarantees extend Rust's statically typed sink contract;
invalid Check objects cannot be constructed in the corresponding Rust vectors.

Validate/copy check values as they enter storage and expose detached immutable
snapshots for inspection. Mutating original values, snapshots, observer batches,
legacy result arrays, or earlier traces must not mutate retained evidence.
A sink never exposes the mutable backing buffer. If a public observed-into API
accepts a raw caller array/list/slice, caller ownership is exclusive during the
operation: no alias mutation, reentrancy on that same buffer, or concurrent use.
Lease checks cannot sandbox application code that already captured a raw backing
alias. An opaque CheckBuffer with detached read views is another valid API.
Append callbacks are synchronous void operations. Python rejects non-None return
values (including coroutine objects); TypeScript rejects non-undefined returns
and thenables, even where a static void callback type permits an async function.
Reject instead of accepting an asynchronously unfinished empty check batch. Lease
cleanup covers Python BaseException and JavaScript throw undefined as well as
ordinary exceptions. Go/Swift callback types constrain synchronous returns;
escaped asynchronous writes remain invalid.
Callbacks remain synchronous and confined; saved/asynchronous/concurrent sink use
is invalid and receives deterministic expired-use errors where safely observable.
No portable reserve/capacity guarantee exists for Python/JavaScript lists.

## Shared finite vectors and qualification

`conformance/append-checks-v1.json` is a candidate corpus of independently derived
checks, events, batches, and outcomes. Rust reference uses real public APIs; no
engine output creates goldens. All fixtures have integer state initially0,
input1, step adds input, no outputs, decimal-ASCII state/input codecs. State
checks are passed same, then skipped skip / not run; transition checks are
passed same. Optional fail_state=1 appends failed bad / broken after the state
batch. error_at state:N or transition:N emits the first corresponding check then
returns fixture append error in append mode; legacy Result error emits none.
Append mode's legacy callbacks must error if accidentally called. Empty transition
mode uses the legacy default's empty vector. All checks retain ordered duplicate
IDs rather than deduplicating them.

Rows select mode legacy/append and operation direct_state/direct_transition,
observed, bfs, fuzz, shrink, record, replay, oracle_state/oracle_transition,
paired_state/paired_transition. Direct and wrapper operations begin with passed
sentinel; observed begins with it but must clear it. Direct state and observed
turn check state1; direct transition supplies before0,input1,after1. BFS domain
is [1] at state0 and empty at state1; fuzz is one case/one step with generated1;
shrink starts with input[1] and target state bad. Replay uses an independently
constructed completed one-step trace. Search observers collect initial and turn
batches separately; histories/controls retain their existing contracts.

Expected has outcome, checks (null when no public check vector is returned),
observations, witness (null absent), attempts (null except shrink). Checks use
{id,status,details}. Observations use {phase,complete,checks}; check_error batches
have complete=false for the failed checker. Complete state preceding an edge
error remains complete=true. Event strings log initial, step:BEFORE:INPUT,
legacy_state:N, legacy_transition:N, append_state:N:LENGTH,
append_transition:N:LENGTH, observe_initial, observe_transition, or
check_error:PHASE:COMPLETE. Wrapper reference events have reference_ prefix;
oracle hooks log oracle_state:N:LENGTH / oracle_transition:N:LENGTH;
projection logs projection_actual_state then projection_reference_state.
The initial direct sentinel counts toward LENGTH. sequence/state_every/
transition_checks specify observed policy. wrapper_error optionally targets
oracle_state/oracle_transition/reference_state/projection_actual_state.

The native lease/invalid-value/alias-confinement guarantees require native-specific
runtime tests, not fabricated Rust cases. Qualification also needs installed
consumers using append hooks across all paths, wrapper errors, source-bound Kyoto
receipts, and legacy portable-profile regression. Performance/allocation claims
require separate measurements.

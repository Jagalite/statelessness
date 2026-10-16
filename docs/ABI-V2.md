# ABI v2 contract

Status: implemented opt-in candidate, October 12, 2026. Runtime qualification and
performance measurements remain pending by explicit user choice. The C header
and language adapters are reviewable source contracts, not a released stability
promise. See [status](ABI-V2-STATUS.md), [plan](ABI-V2-PLAN.md), and
[distribution](ABI-V2-DISTRIBUTION.md).

This is a versioned **C calling convention implemented in Rust**, with a separate
Wasm transport. It does not expose Rust's native ABI, Rust object layouts,
`Vec`, trait objects, or Rust allocator ownership to other languages. ABI 1 and
the independent language engines remain available with their existing defaults.

## Shared engine

The crate-private `operations` interfaces make foreign operations fallible without
pretending host tokens implement semantic Rust `Eq`, `Hash`, or `Clone`.
`ModelOps` handles initial state, steps, checks and estimates; `EnumerateOps`
handles ordered cursors and semantic equality/hash; `CodecOps` handles optional
persistence; `GenerateOps` and `InputIdentityOps` handle generation, simplification
and input comparison. `NativeOps` adapts existing Rust traits with static dispatch.
Native public entry points keep their signatures and avoid foreign registries.

BFS, initial/observed checks, recording, replay, fuzzing, shrinking, finite-domain
automatic generation and ring recording share Rust implementations. The ABI layer
adapts callbacks and results; wrappers own application snapshots and structural
transport conversion. No wrapper implements another search or replay algorithm.
Guided feedback, arbitrary composition and debugger APIs remain native-only;
their presence in Rust is not an advertised foreign capability.

## C surface and capability negotiation

[stateless_v2.h](../bindings/c/stateless_v2.h) is the authoritative declaration of
symbols, callback arguments, numeric statuses and result fields. Bootstrap queries
return ABI version 2 and available capability bits. Session construction validates
the known callback structure prefix, capability combinations, UTF-8 identities
and nonzero resource quotas. Unknown capability bits are rejected.

| Capability | Interface |
| --- | --- |
| CHECK | Separate initial checks and checking an actual observed transition. |
| ENUMERATE | Shared ordered BFS; optional semantic HASH and ESTIMATE. |
| CODEC | Binary recording/replay and bounded structural portable conversion. |
| GENERATE | Shared fuzzing; fallible generator and membership callbacks. |
| SHRINK | Shared minimization, input equality and fallible simplification cursor. |
| RING | Bounded observation recording and coherent-prefix snapshots. |
| AUTO_GENERATE | Shared reservoir generation and membership from ENUMERATE; semantic input equality is still required. |

Unsupported operations fail before model callbacks. Checking, enumeration and
fuzzing do not require codecs. Callback errors are explicit model errors; they
never become inequality, exhaustion, a passing check or an empty successful result.
A caught Rust panic is a distinct status. Raw pointer validity remains the C
caller's obligation, and callbacks must not unwind across the boundary.

## Ownership and lifetimes

Handles and host tokens are nonzero `uint64_t`. Engine handles are never reused
within a loaded engine and are checked for session and kind. Numeric handles from
different loaded engines can coincide; wrapper engine identity prevents crossing
that boundary. Numbers alone do not authenticate the creating library.

The host creates an immutable application snapshot before publishing a root.
`value_import` and `callback_value` transfer that root only on success. Rejected
publication leaves cleanup with the host. A successfully published partial batch
is released by Rust if the callback later fails. Rust clones an admitted shared
owner without invoking a fallible host clone. Final release removes the host root
on the owner thread. Host token identity is never semantic state/input equality.

Callback arguments are borrowed host tokens and valid only for that invocation.
They are not engine value handles. A callback uses invocation-scoped builders to
publish values, checks, bytes, scalar results and dispositions. Builder errors are
sticky even if the callback ignores a failed call and returns success.

Result handles own their witnesses. `result_input` returns a separately owned
value handle; callers release it even if the result is subsequently closed.
Safe wrappers extract independent snapshots. Session closure invalidates all
subordinate handles and releases retained roots before the host context dies.
Explicit close is required. In Python, unclosed sessions are strongly retained
to keep ctypes callback trampolines alive; garbage collection is not an owner-thread
close mechanism. Go locks its session's OS thread and contains callback panics.

Sessions are owner-thread confined and nonreentrant. During dispatch only
invocation services and cancellation may reenter. Release callbacks perform
nonthrowing bookkeeping, cannot reenter and contain cleanup exceptions. Cancellation
is thread-safe; reset and teardown are owner-thread operations. TLS destruction
removes stale cancellation entries. Each wrapper preserves its context through
native teardown.

## Semantics and bounds

The [portable specification](../spec/README.md) remains authoritative. Empty and
duplicate check IDs, exact Unicode text, check order, outputs and dispositions are
preserved. Hash collisions use semantic equality. Equal states must hash equally;
omitting hash uses equality-only lookup. Snapshots prevent application mutation
from changing retained predecessors and witnesses.

Observed checking receives the completed transition and never calls the reducer.
Successor-state checks precede transition checks, and periodic/disabled checks
produce explicit skips. Partial failed batches are not published as complete
observations. BFS preserves FIFO traversal, input order, first predecessors and
boundary probes; cursor errors remain errors at depth/transition limits.

Session quotas bound live roots, callback/result items and encoded bytes, not
arbitrary host allocations or process RSS. Algorithms additionally expose logical
work and retained-evidence limits. Automatic generation and simplification cursors
have finite scan budgets even if the host repeats values forever. Failed automatic
sampling does not commit partial RNG state. Wasm rejects unsupported wall-clock
limits explicitly; runs without deadlines do not consult an unsupported clock.
Cancellation remains cooperative at defined algorithm boundaries.

## Recording, replay and portable transport

Binary traces keep the existing codec, identities and historical reader. Portable
JSON and binary audit traces remain distinct formats. `STV2P001` is a bounded
internal structural packet: little-endian counts/lengths, UTF-8 text, explicit
value tags and byte spans. Rust converts it to/from binary without running model
callbacks. Language wrappers retain their existing strict portable JSON validators.
Portable JSON omits binary run configuration; importing it uses manual configuration.
Conversion alone does not establish that replay succeeds.

`record_cursor_limited` enforces portable blob/item/payload limits during shared
recording so limit errors preserve a coherent prefix. Initial checkpoint failure
returns an error without a trace. A later recording error returns MODEL_ERROR
**and a nonzero result** containing the error-terminated prefix; it must be read
or released. Ring snapshots follow the same rule. Other failed operations do not
publish success artifacts. Replay reports exact/diverged/incompatible outcomes,
build matching, failure reproduction, divergence step and field separately.

## Wasm and language adapters

The default `stateless.wasm` retains ABI 1 imports. A separate feature build
(`--features abi-v2-wasm`) produces `stateless_v2.wasm`, whose additional
`stateless_v2.dispatch` and `stateless_v2.release` imports route host callbacks.
The v2 loader supplies the legacy import required by the coexisting ABI 1 exports.
Never replace the ABI 1 artifact with the feature build.

JavaScript uses `bigint` for 64-bit handles and RNG state, checks Number conversions,
refreshes memory views after possible memory growth and copies bytes before scratch
allocation. Async engine initialization is explicit; session operations are then
synchronous. Python loads an explicit library path with optional SHA-256 checking.
Go uses an explicitly linked engine; Swift has a separate source package. All
adapters reject missing/incompatible engines without silent fallback.

See the [distribution contract](ABI-V2-DISTRIBUTION.md) for package paths,
linkage choices and unsupported deployment modes. Corpus runners reuse fixture
semantics and label their engine family `rust-shared`; four wrappers are not four
independent semantic implementations.

## Qualification

Compile and package-inspection receipts establish only the recorded checks for
their exact source and artifacts. Runtime regression, callback cleanup, malformed
input recovery, portable parity, installed consumers, actual browsers/mobile
targets, historical replay and speed/memory measurements remain pending.
`check-abi-v2-gates.py` keeps adoption gates closed without matching evidence.
No default switch or independent-engine/ABI 1 retirement is authorized by a build.

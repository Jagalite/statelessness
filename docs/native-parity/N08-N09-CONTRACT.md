# N08/N09 independent stateful candidates

Behavior target: `0876717bee85e335449c2c99ada41f6c6e6112d1`.
Continuation baseline: `4e84cf1a8bf15b2e6b0698d6801a92dc94a45d00`.
These APIs are candidates. Finite tests do not establish complete parity.

## Composition and networks

`Pair`/`NewPair` executes one of two heterogeneous children. Addresses preserve
which child owns an input/output. Input enumeration orders left local inputs,
right local inputs, then pending delivery indices. Pending messages are state.
A delivery removes exactly its selected message before ordered routing appends
new messages. Child outputs remain observable even when routing returns nothing.
Disposition comes from the addressed child, including rejected and ignored turns.

`Network`/`NewNetwork` registers nonempty, unique u32 machine IDs in sorted order.
Registration order cannot change scheduling order. Local inputs precede queue
actions. Each pending index offers deliver (when permitted), drop, then duplicate.
FIFO is per `(source, destination, channel)`, not global. Duplicate appends a copy
with the same channel identity. Machine IDs and channel IDs are stable; the pinned
Rust API selects messages by queue index and has no independent message counter.

State checks append child namespaces (`left`, `right`, or `machine/N`) followed
by `global`. Transition checks invoke only the addressed child before global
checks. Forged outputs addressed to another child fail. Queue-only actions must
have no child output. Duplicate property IDs fail rather than overwrite checks.

Pending, per-channel and fanout limits produce model errors indicating incomplete
verification. Neither saturation nor a callback error is an exhausted search or
an application property pass. Effects are always application-owned data. No
adapter runs host I/O, drains a queue, guesses a clock or spawns a scheduler.

Snapshots use child clone hooks (Python uses the engine's deep-copy contract).
Search identity needs both child state equality and queued input equality. Go's
`WithInputEquality`, optional pair comparator and TypeScript's constructor
comparators supply the latter explicitly; callers must not ignore queued values.

## Lifecycle

`LifecycleMonitor` observes input events, then output events, with the observed
disposition passed to the input classifier. It never reads actual next state to
infer an obligation. A deadline is inclusive: settlement on the transition that
reaches that deadline is permitted. Crossing it first records irreversible
failure. An already overdue prior history cannot be repaired by a later event.

Legacy history retains completed obligations and released resource keys. Reuse
of an obligation key is a violation even after settlement/cancellation. Generation
belongs in application keys. Publication after valid settlement can repeat in
legacy history; indexed history records duplicate publication as a violation.

Indexed keys contain epoch, generation and application key. All fields participate
in identity and canonical order. Fence `before=N` asserts that no future legitimate
event can reference epochs below N. A backward fence, pending obligation or
acquired resource blocks retirement and records a violation. A valid fence removes
only terminal/released details below N. The watermark remains, and every later
reference below it records `retired_epoch_reference`, including begin/acquire.
Violations are retained independently of retired details and have their own quota.
The application must prove the delivery-barrier premise; this library cannot.

Cancellation, settlement, elapsed age and real-time timeout never imply a fence.
Pending finite obligations produce a skipped obligations check, not a liveness
proof. Retention diagnostics distinguish live and historical entries and report
the oldest unresolved deadline. Quotas never silently evict evidence.

Swift callers needing Rust string identity use `LifecycleTextKey`, whose equality
and ordering use exact UTF-8. Generic comparators must be total and stable. Keys
must not change their identity while retained. Go keys must be immutable value
keys; pointer identity and NaN are unsuitable for portable histories.

## Persistence and integration

Lifecycle codecs use the existing bounded canonical value decoders. Legacy fields
are `now:u64`, obligations vector `(key,deadline,cancelled,settled)`, resources
vector `(key,acquired)`, and violation strings. Indexed v1 is version byte 1,
now, retirement fence, sorted obligation map with published bit, resource map,
and structured violations `(optional key, code, event, observed_at, optional prior)`.
No legacy published bit or version byte is added. Restored indexed histories reject
noncanonical keys, details below fences and publication without settlement.
Application key codecs must match for byte interoperability.

Allocation weights are conservative logical charges including collection/node
space; they are not host heap measurements or exact Rust allocator accounting.
Python exposes composition/network canonical schema recipes and a codec facade;
application schemas determine payload identity. Metadata includes child/wiring
versions and queue policy where available. Builds differ across engines.

Use the existing `WithOracle` adapters with lifecycle classifiers; native search,
observation and trace algorithms remain the execution and evidence mechanisms.
Imports do not load Rust, cgo, Wasm or optional debugger packages.

The handoff receipt documents current implementation and qualification boundaries,
including any incomplete APIs. This contract does not mark shared ledger cells
qualified, replace the plan, or authorize PR merge/publication.

## Diagnostic compatibility decision

Legacy failure details preserve historical English strings, including arbitrary
restored strings that happen to equal an indexed code. Indexed native diagnostics
use ordered codes joined by `; ` and advertise `diagnostics=native-codes-v1` in
semantic identity. Pinned Rust's Debug-formatted generic keys and retention data
are not claimed to be identical. This is an explicit remaining parity gap, not
an accepted exception or a reason to mark N09 complete. Identical indexed history
bytes across consumers are a separate claim from exact failure-trace replay.

## Indexed Rust Debug diagnostics candidate (opt-in)

This candidate adds a separate indexed-monitor diagnostic policy; it does not
change legacy monitors or the default `native-codes-v1` policy. It targets the
pinned Rust monitor's check *details*, not automatic equality of monitor metadata
or unconditional compatibility of arbitrary application key types.

The caller supplies both (1) a formatter for the inner application key `K` and
(2) a formatter for `code` and `event` strings. Each returns the complete text
that the pinned Rust toolchain would produce for `Debug`, including any quotes
and escapes. Both are necessary: `K` is application-defined, and Rust string
Debug escaping depends on the Rust toolchain's Unicode tables. Each callback must
return a valid string synchronously; other values are model errors. Native languages
must not silently substitute JSON, their own `repr`, or an approximate Unicode
classification. Callbacks are synchronous, stable for the lifetime of the
monitor, and must not mutate retained history. Failure is a model error, never a
passing check. Compute and validate the diagnostic output before publishing a
partial check batch. Application callback work and returned text allocations are
not covered by the history retention limits.

A nonempty, valid UTF-8 stable formatter identity declares the caller's compatibility promise
for **both** callbacks. The indexed semantic name appends exactly
`;diagnostics=rust-debug-v1;formatter=<N>:<identity>`, where N is the UTF-8 byte
length of the identity written as decimal digits. This disambiguates identities
containing separators; it does not prove the callback is correct and does not
claim byte equality with pinned Rust's own metadata name. Existing native mode
retains its prior identity and behavior. Applications must arrange compatible
model and formatter identities when replaying evidence across engines.

The monitor owns the outer templates, field order, punctuation, and spacing.
`ScopedKey` is `ScopedKey { epoch: E, generation: G, key: KDEBUG }`; u64 and
counts are unsigned decimal and booleans are lowercase. `Obligation` is
`Obligation { deadline: D, cancelled: C, settled: S, published: P }`.
`Violation` is `Violation { key: KEY, code: CODE, event: EVENT, observed_at: T,
prior: PRIOR }`, where optional values use `None` or `Some(VALUE)` and CODE and
EVENT are callback results. Violations retain insertion order, joined by `, ` in
square brackets. Retention is `RetentionStatus { active_obligations: A,
historical_obligations: H, acquired_resources: R, historical_resources: S,
oldest_unresolved: OLD, retired_before: F }`; OLD is `None` or
`Some((SCOPED_KEY, DEADLINE))`. Ties for oldest pending deadline use the first
key in canonical order, matching Rust's BTreeMap traversal.

The failing safety detail is `violations=VECTOR; retention=RETENTION`; this also
applies when the violations vector is empty but an obligation is overdue. A
pending obligations check is skipped with detail `finite history: RETENTION`.
The passing conditions and property IDs remain unchanged. Exact whitespace and
field order are part of this candidate, but callback correctness and toolchain
compatibility are caller responsibilities. The Rust executable reference
`tests/n09_debug_reference.rs` covers empty and populated structures, options,
booleans, escaping, and Unicode. `tests/n09_monitor_debug_reference.rs` asserts exact failure and
skip details from the actual Rust monitor for a duplicate Begin. These reference
cases do not establish cross-engine parity or cover all Unicode code points.
Cross-engine acceptance still requires independently specified positive and
negative fixtures, restored arbitrary strings, callback failure behavior, identity
mismatch controls, and actual monitor execution in every engine.

## Pinned-surface correspondence

The source inventory entries `composition`, `network`, and lifecycle entries in
`capabilities.json` still describe the PR12 baseline. This lane deliberately does
not overwrite their shared status cells; parent integration reconciles them.

| Rust surface | Native correspondence |
| --- | --- |
| composition Address/Input/State/Pair/Wiring | PairAddress/PairInput/PairState/Pair builders and wiring callbacks |
| composition Model/Enumerate/ModelCodec | native model methods/builders, ordered inputs, typed codec recipes |
| network MachineId/Address/Route/Message | u32 IDs and typed native address/route/message records |
| network QueuePolicy/DeliveryOrder/Input | native queue policy and local/deliver/drop/duplicate tagged actions |
| network Model/Enumerate/ModelCodec | native model methods/builders, explicit input equality, typed codec recipes |
| lifecycle Event/History/LifecycleSpec/Monitor | native events, ordered retained history, classifiers and oracle adapters |
| indexed ScopedKey/IndexedEvent/Obligation/Violation | scoped key, fence action, obligation fields and structured violations |
| indexed RetentionLimits/RetentionStatus/IndexedHistory | quotas, retained/live counts, watermark and ordered lookup |
| legacy/indexed OracleCodec | existing native canonical value codecs with historical/versioned layouts |

Python, TypeScript and Swift use sorted retained arrays with binary key lookup for
indexed histories; insertion/retirement and snapshots remain linear. Go builds
private key lookup maps per advancement while preserving canonical retained order.
These representations are not a performance-equivalence claim with Rust BTreeMap.

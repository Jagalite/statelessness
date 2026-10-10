# Inspection, runtime watches, and optional derives

These APIs belong to `statelessness-debug`, not the default dependency-free engine.
They project already committed values. They never invoke a reducer, advance an
oracle, dispatch an output, supply logical time, or alter a canonical trace codec.
Use `diagnostic` for explicit scoped local probes and independently routed sinks.

## Handwritten inspection

```rust
use statelessness_debug::inspect::{
    FieldView, Inspect, InspectContext, InspectError, InspectNode, PathSegment,
};

struct State { count: u128, secret: String }
impl Inspect for State {
    fn inspect(&self, path: &[PathSegment], cx: &mut InspectContext)
        -> Result<InspectNode, InspectError>
    {
        cx.object(path, "State", &[
            FieldView::new("count", "Count", &self.count),
            FieldView::redacted("secret", "Secret"),
        ])
    }
}
```

`Inspect::schema` declares a display name/version and optional source site. The
ordinary handwritten interface and derive use `INSPECT_API_V1`. Schema identity
is a name plus version; change the version when structural IDs or interpretation
change. A caller supplying `InspectQuery.snapshot` is responsible for binding it
to the actual retained state/revision. The protocol/session adapter verifies that
binding; a bare trait cannot prove that an arbitrary supplied ID is genuine.

Paths are structural `Field`, `Variant`, `Index`, and typed `MapKey` segments.
Dotted strings, Rust/JavaScript expressions, wildcard strings and positional
"entity moved" inference are not part of this evaluator. Enum fields live below
the active variant segment. Admission checks the current inspectable shape; a path through an inactive enum
variant must be added when that variant is available. A static all-variant
schema catalog is not claimed. A missing/inactive path is an explicit error. Root
queries have an empty path. Selecting one scalar does not traverse its siblings.

`inspect(&value, &query)` returns a bounded `InspectResult` with snapshot, schema,
path, node kind, available child count, page information, and completeness.
Defaults are 100 children/page, depth 32, 1,024 nodes, 10,000 built-in work units,
4 KiB/value and 256 KiB projected node payload. Watch events have a smaller 32 KiB
budget including owned event metadata. UTF-8 previews stop on character boundaries.
The built-in map adapter bounds key lookup and offset scans; a far page may need
a larger explicit work budget. Exhausted work returns `WorkLimit` or partial
`Work` completeness, never a falsely complete empty collection.

Built-in adapters cover typed integers, booleans, characters, floats with their
exact bits, strings, slices/vectors/arrays, VecDeque, tuples up to arity eight,
Option/Result, boxes/references, and ordered BTreeMap keys. `Bytes` requests a byte
preview rather than a sequence of u8 nodes. `Opaque` never formats its resource.
`DebugView` is an explicit bounded convenience: Debug output is unstable and can
contain secrets, so it is opaque and never used as structured equality.

Integers are tagged with Rust type and decimal text; u128/i128 never pass through
floating point. Maps expose typed keys and an ordering declaration. An index is
only a position. Application-owned stable keys are required for entity identity.

Complete-node comparison produces Added/Removed/Changed/Unchanged. A redacted,
opaque, unavailable, truncated or partial-page node yields Unknown even when its
preview matches. Snapshot/schema/path mismatches also yield Unknown. Projection
comparison is never a reachability key or exact state comparison.

Limits account for debugger-owned retained payload, including node/child headers,
not allocator metadata, whole-process RSS, or arbitrary work inside application
inspectors/formatters. Those callbacks remain cooperative and must be read-only.
All built-in node emission and map/field search work is charged. A malicious or
blocking handwritten inspector requires a process-isolation boundary.

## Optional derive

```rust
#[derive(statelessness_macros::Inspect)]
#[inspect(version = 2, label = "Request state")]
struct RequestState<T> {
    #[inspect(id = "pending-count", label = "Pending")]
    count: u128,
    #[inspect(redact)]
    token: T,
}
```

The separate proc-macro crate adds no engine dependency. Supported shapes include
named, tuple, unit and empty structs/enums; generics, lifetimes, const parameters,
qualified/associated field types, where clauses and unsized tails. Type options
are `crate`, `version`, `label`; field options are `id`, `label`, `redact`; variant
options are `id`, `label`. Field IDs default to their name or tuple position.
Variant IDs default to the Rust variant name. Default schema names include the
qualified concrete Rust type; an explicit type label is an application-owned
stable schema name and should be unique within its registered scope. Source metadata identifies the
macro invocation, not an inferred faulty statement.

Redacted fields are not read, referenced or required to implement Inspect.
Unsupported unions/discriminants, malformed syntax, duplicate IDs/options and
unknown options produce explicit compile-time diagnostics. Recursive field
bounds can require a handwritten implementation. TraceEncode/TraceDecode ignore
inspection metadata and retain exact secret bytes as before: display redaction
never makes a raw trace safe to share.

## State watches

```rust
use statelessness_debug::inspect::{PathSegment, SnapshotId};
use statelessness_debug::watches::*;

let path = vec![PathSegment::Field("count".into())];
let mut registry = WatchRegistry::new(
    WatchLimits::default(),
    WatchAuthorization::allow_paths(vec![path.clone()]),
);
let ack = registry.add_validated(&state, WatchConfig::changed(path, 1))?;
let context = ObservationContext::new(
    SnapshotId { session: 7, revision: 1, sequence: 1 },
    OriginKind::Simulation,
);
registry.observe(&state, context);
registry.disable(ack.watch_id)?;
```

Remote callers use `add_validated`/`update_validated`: authorization is checked
before any inspector callback, then schema and the selected path are checked.
Unknown and directly redacted paths are rejected. The acknowledgement returns
watch ID, control generation, capture revision, effective next sequence, resolved
type and schema identity. Local `add` is explicitly provisional: it validates
configuration/authority but reports missing paths at observation time.

A registry belongs to one ordered producer and one inspected state stream/schema.
Use separate registries for separately ordered machines. A change of supplied
model/machine identity resets a comparison rather than compare unrelated states. Call it at
safe points from the model-owning thread. Configuration requires exclusive mutable
access and cannot change an in-flight observation. An acknowledged update applies
to the next observation boundary; a concurrent host serializes access, as tested.
Cross-producer acknowledgement is supplied by `diagnostic::DiagnosticHub`, not
inferred from one registry acknowledgement. Inspectors add no Send/Sync requirement
to model values.

Lifecycle states are Active, Paused, Expired, Disabled and Removed. Operations are
add/update/enable/disable/pause/resume/remove/list/current/snapshot/snapshot_now.
Removed entries leave a bounded lifecycle event rather than an unbounded tombstone
registry. A disabled/paused watch clears its baseline and current value. Re-enabling
starts a fresh baseline; retain-last-baseline behavior is intentionally unsupported.
Already emitted events remain until ring eviction or explicit ring clearing.

The default is post-boundary Changed with an initial Baseline. EveryObservation,
Equals and PropertyFailure triggers are also available. Selectors can restrict
model, machine, input/output variant and origin before reading fields. Broader
comparison/hysteresis triggers for local probes are in the diagnostic companion;
this state-watch API does not evaluate arbitrary expressions or accept wildcards.

`evaluation_interval` samples value evaluation and labels comparison
`ChangeSinceLastSample`, with incomplete/Sampled status and Unknown diff: an
unchanged sampled pair cannot exclude intervening changes. `delivery_interval`
samples emission after full evaluation, preserving the internal baseline.
Observation gaps, schema changes, unavailable/redacted data and baseline eviction
never establish equality. A schema identity change pauses the watch before payload
access. A transition that changes and restores a local value before commit is
invisible to a state watch; an explicit local probe is needed to see it.

`ttl_observations` expires a subscription after the specified number of selected
observations, including sampled-away evaluation boundaries. It is separate from
`baseline_ttl_sequences`, which resets a stale comparison. Neither option reads a
runtime clock or promises a wall-time TTL. Events carry simulation/replay/live/etc.
origin, snapshot identity, generation, path, schema, phase, trigger, bounded model
and machine labels, completeness, value/diff and previous-snapshot identity.
Control events are labeled `DiagnosticControl`; they are not invented model turns.

A runtime-disabled registry returns without schema access, field lookup, payload
formatting/cloning, allocation, or clocks. Active equal queries share one projection
per boundary. Active scalar capture currently materializes a bounded node per
evaluation; the measurements below include that cost rather than claim a zero-cost
or allocation-free active path.

## Retention and failure

Defaults: 64 registered watches, 1,000 ring records, 4 MiB ring payload, 32 KiB/event,
8 MiB current/baseline payload and 4 KiB configuration per watch. Oldest diagnostic
records are evicted first; oversized records are dropped. Drop health persists
outside the ring. EventsDropped reporting is best effort when a health record can
fit without recursive eviction. ObservationGap is separate from EventsDropped.
The transient per-boundary query cache is separately bounded by the configured
watch count times event size. These are payload budgets, not hard RSS claims.

`property_failure` on an observation forces selected-watch capture despite sampling
and preserves a separate bounded copy of the diagnostic ring. Hosts can supply
`failing_properties` IDs (up to 16 IDs, 256 bytes each); raw error text is not copied.
The saved failure window has its own maximum ring-sized allocation and does not
freeze ongoing application work. It contains only retained projected history.
`failure_snapshot` cannot recover prior unobserved locals or replace exact evidence.
`snapshot_now` explicitly projects a safe-point value without advancing observation
order or replacing the change baseline. Snapshot/export authority is separate from
input/control authority in the protocol.

`MarkerCollector` supplies bounded lazy post-turn classifications. The explicit
scoped diagnostic module supplies real in-reducer probes when a host opts in.
Neither mechanism suspends or reruns a reducer. Privacy tests cover metadata,
redacted field admission, snapshots and diagnostic failures.

## Qualification and measurements

```sh
cargo test -p statelessness-debug --test inspection --test inspect_derive --test watches
python3 scripts/check-inspect-derive.py
cargo run -p statelessness-debug --release --example measure_watches
```

Focused tests cover handwritten/derive parity, large integers, map key/work bounds,
paging/depth/bytes, unknown equality, redaction without access, lifecycle and TTL,
concurrent effective-boundary acknowledgement, sampling versus loss, schema changes,
failure capture, lazy markers, and byte-for-byte exact recording/replay off/on.
The macro qualification script exercises 32 compile-fail fixtures and a cold-offline
renamed runtime dependency consumer. Existing macro tests remain required.

`measure_watches` measures single-producer diagnostic code only, with System
allocator calls counted inside the timed region. It includes no model execution,
real effects, exporter I/O, cross-thread scheduler contention or production load.
See [the measurement record](debugger/INSPECTION-BENCHMARK.md) for compiler/platform,
workload sizes, allocation counts and observed latency. Results are environment
specific and do not establish universal overhead, scheduling equivalence or a hard
process-memory ceiling.

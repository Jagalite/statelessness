# Optional native diagnostics

Import `github.com/Jagalite/statelessness/go/debug` explicitly. The core engine
has no dependency on this package. There are no package initializer clocks,
watchers, goroutines, transports or global model instrumentation.

The offline APIs own their evidence. `NewEvidenceViewer` validates recording
semantics beyond structural wire reads. Boundary zero is the retained checkpoint;
full-width local/host sequences and phase provenance remain distinct. Exact bytes
can contain secrets. `Preview` truncates bytes without an output decoder.
`Failure` rejects unknown noninitial v1 phases; `MinimizeWithPhase` is the explicit
legacy assertion API. Persisted v2 phase contradictions reject.

`Verify` uses explicit finite transition limits (the artifact's finite step quota
when omitted), optional cooperative cancellation/deadline, actual observations and
all replay difference flags. It dispatches no outputs. V2 minimization performs
phase verification, then exact workbench verification, before checkpoint-based
shrinking; all passes charge one transition/time budget. Declared provenance needs
an explicit original policy implementation. Admission and Generate causality are
independent. A smaller tested reproducer is not proof of global minimality.

Typed `Session` owns simulation state under the application's clone contract.
The host serializes calls; reentrant mutation rejects. Pending pre-breakpoint
inputs bypass once at the same revision. Checks and observers cannot undo an
already produced transition. Opaque candidate tokens bind session+revision+input.
Recording sessions hand canonical checked tokens to the recorder without repeated
checking. `ForkWithOptions` verifies the selected prefix before checkpoint decode,
rejects failed boundaries, and creates a recorded independent child with typed
parent/checkpoint FNV+size sidecar bindings. Those bindings detect accidental
mismatch; they are not authentication.

Inspection is an explicit display adapter, independent of codecs and equality.
Queries bound work/depth/nodes/bytes/value previews/pages. Redacted fields never
run their getters. Map ordering is declared stable or unstable; incomplete,
redacted and unstable projections compare Unknown before equality.
An adapter-supplied root truncation reason is limited to 32 bytes; truncation
sentinels remain available even when the node or byte budget is exhausted.
String values and opaque labels are validated as UTF-8 across the full Go string,
charging each decoded rune against the work limit. A long valid value can exhaust
work before its preview is returned; invalid bytes beyond that boundary are not
diagnosed. Rust receives an already validated `str` and does not need this scan.
`SequenceIndexed`, `ObjectIndexed` and `MapIndexed` build page entries lazily;
selected object fields and map keys scan metadata within the work budget to
detect ambiguous identities before calling a value getter. The caller owns and
stabilizes the backing collection.
`Map` and `MapIndexed` retain caller-supplied child labels and source sites as a
Go extension. Use `MapCanonical` or `MapIndexedCanonical` for Rust-compatible
map children with empty labels and no source; these methods ignore supplied
label/source metadata while preserving keys, ordering and value callbacks.
`AddValidated` resolves a permitted watch path under its schema before ack.
Watches retain independent bounded baselines and queues, revisioned generations,
evaluation/delivery sampling, TTLs, source/schema/gap resets, typed thresholds and
explicit queue-loss/health records. Scoped probes skip unselected getters and
share one selected getter across sinks. Configuration uses expected revision and
explicit ordered-producer acknowledgement at an effective boundary. Unknown
property status never matches known successful checks. Exporters run explicitly;
failed exports preserve queue data and health independently of the model.
For protocol polling, prefer `BeginTurn` and `Finish(complete)`: the scope stages
diagnostic events and records the actual turn completeness when it publishes
them. Defer `scope.Close()` immediately after `BeginTurn`; like Rust's `Drop`
for `ScopedDiagnostics`, it publishes an unfinished turn as incomplete and
releases the hub after a panic or early return. `Close` is idempotent, and
`Finish` after `Close` is a no-op. Direct `Probe` and `Marker` calls can instead
supply `TurnComplete` when the caller already knows the result. If that value is
nil the record has no Rust wire envelope: it stays in the host sink only and
never enters the protocol queue, so it cannot block `probe_events`.
The attached protocol queue is the Rust-shaped view of a sink: it holds only
enveloped probe/marker records (Rust has one queue of `DiagnosticEvent`s).
`probe_status` reports that queue's occupancy and its `captured`, `dropped` and
`abandoned` counters together with the per-sink capture counters, as all ten
Rust `SinkHealth` fields in Rust order. Go-only lifecycle notifications
(`capture_configured`, `capture_removed`, `scope_incomplete`) remain visible to
the host sink only; Rust does not publish them either. `Health(sink)` returns the
Rust-shaped `SinkHealth` for the host queue, and `Abandon(sink)` mirrors Rust
`DiagnosticHub::abandon`, clearing both queues and counting abandoned records.
`DetachProbeProfiles` explicitly discards the attached protocol queues while
leaving host queues and subscriptions intact; reattach to start fresh.
`SnapshotNow` projects a watch at an explicit safe point, even when disabled,
without changing its observation order or baseline. Its event budget uses Go
retained-size accounting, and oversized completed events return an error.
A selected watch checks schema identity before evaluation sampling and pauses on
a change. Adapter creation and schema callbacks therefore run on sampled-out
selected observations, while projection remains skipped.
`WatchContext.OutputVariants` matches a selector against any transition output;
the existing singular `OutputVariant` also matches. The collection is copied
before callbacks, with a host admission limit of 4096 entries and 1 MiB of UTF-8
text; callers must keep it stable during admission and own their allocations.

Protocol, cooperative live ownership and host telemetry have separate APIs and
proof boundaries; see `TELEMETRY.md`. No API promises callback preemption, process
RSS limits, remote authentication, LLDB/GDB integration or a new engine default.
The finite regression suite and frozen-source installed-consumer qualifier are
candidates for root review. Broad N12/profile/platform closure is not claimed.

`MarkerCollector` captures bounded post-turn diagnostic markers. Detail callbacks
run only after admission and must not affect application behavior or reenter the
collector. Its byte count uses a 48-byte Rust 64-bit logical marker overhead; it
does not bound Go heap or callback allocations. Use each collector from one
goroutine and call `Finish` once.

Watch ring loss is tracked cumulatively in health. On an interested observation,
a best-effort `events_dropped` marker is appended only if it fits without evicting
another event; otherwise the loss stays pending across drains and exports.
Diagnostic-probe queues retain their separate export behavior. Logical retained
sizes are not Go heap or process-memory bounds.

Session and minimization admission capture the input before state/policy callbacks,
so cooperative clone hooks protect the accepted query from external aliases. The
input clone hook can run even for an empty or failing candidate scan; a clone panic
is deferred until the query is needed. These copies do not provide atomicity
against arbitrary cross-alias or concurrent mutation.

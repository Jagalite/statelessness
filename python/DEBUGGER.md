# Optional native debugger workbench

Import `statelessness.debugger` explicitly. Basic engine imports do not load
this module, session callbacks, clocks, transport, or watchers. This candidate
surface is not a promoted profile or a complete N12 qualification.

`EvidenceViewer(Artifact(...))` or `EvidenceViewer.read(reader)` validates v1
recording semantics and v2 envelopes without executing a model. Local sequence
zero is the retained checkpoint. `boundary`, `preview`, `retained_range`, and
`failure` preserve stored bytes and provenance. Exact previews may contain
secrets; they are not sanitized diagnostic exports. Noninitial v1 check phases
remain unknown. There is no fabricated generic output decoder.

`verify(model, limits=VerificationLimits(...))` explicitly executes bounded
exact replay, including persisted v2 check phase boundaries. Trace byte quotas,
transition quotas, cancellation and deadlines are independent. Callbacks remain
cooperative: no callback preemption or hard RSS bound is promised.

`minimize(model, sequence, check, limits=ShrinkLimits(...), policy=...)` restores
the actual artifact checkpoint and applies both the environmental policy and
the model's causal admission for every candidate. A declared recorded policy
requires an explicitly supplied callback with matching label/origin. A label is
an assertion, not authentication. Explicit legacy phase selection uses `phase=`;
v2 phase selection cannot contradict the stored phase. Verification and shrink
share transition and wall time budgets. V2 phase verification, workbench exact
verification and original shrink validation are separately charged; reports
include verification transitions. A finite shrink is not a global minimality
claim. Enumerated admission has bounded iteration plus one overflow lookahead.

`statelessness.debugger_session` provides a separate typed synchronous simulation
candidate. It owns detached state, revisions, phase, bounded continuation,
pre/post breakpoints, pending-input bypass, and full/check-policy execution.
Observer failures occur after committed transitions and end the session without
undoing model work. Reentrant mutations are rejected. Simulation dispatches no
host effects and has no live restore authority. Exact recording is explicit via
`recorder_options=RecorderOptions(...)`; it consumes sealed check-once results.
`write_trace(writer)` streams exact capture under exclusive ownership.
Breakpoint predicate faults leave the session terminal. Post predicates inspect
completed detached turns while successful callbacks still see precommit session
state. If a post predicate raises or returns a non-bool, the completed state and
observation are committed before the error propagates, matching the advanced
sequence/revision and any exact recording; the stop is `observer_error`.
This candidate intentionally tightens the Rust panic/unwind boundary: diagnostic
faults cannot leave a recorded turn paired with stale simulation state. There is
no rollback of application side effects or promise of panic recovery.

The subsequent optional modules are separately imported. `debugger_workbench`
adds sealed concrete candidate handles bound to session/epoch/revision/domain,
mandatory candidate revalidation, and `fork_verified` with parent artifact and
checkpoint sidecar bindings. Prefix replay precedes checkpoint restore; forks
produce isolated simulation state and never restore OS resources. FNV bindings
detect accidental sidecar mismatch and do not authenticate a build or caller.

`debugger_inspect` provides explicit structural adapters, schema/version and
snapshot identities, field/variant/index/typed-map-key paths, pagination,
redaction, and conservative display diffs. Unstable maps, omitted values,
redaction and incomplete projections compare as unknown before equality checks.
Redacted fields are not evaluated. Display projections are not replay keys.
`debugger_watches` adds local ordered registry revisions, selectors, sampled
change labels, baseline reset/expiry, finite rings and independent failure
snapshots. Projection payload limits charge logical retained representations,
not allocator overhead or RSS. Capture ack applies to one local ordered producer.

`debugger_metrics` exposes `MetricsStore`, `HistogramSchema`, `Labels`,
`MetricFamily`, `SnapshotTime`, `MetricLimits`, and `protocol_adapters`.
`debugger_dashboard` exposes `Dashboard` and `correlate` for passive metric reports
and causal correlation. Quantiles are histogram upper bounds, including a
present overflow estimate whose upper bound is unknown. Full-width quantities
are Python integers; JSON consumers must use lossless integer parsing. Snapshot
validation may read the store, but reentrant store mutations and
prepared capture commits raise `MetricError` until the transaction ends. The
store requires serialized access; this guard is not a thread-safety mechanism.
Metrics do not read a clock implicitly. Explicit clocks are supplied by
`debugger_effects`; host lifecycle hooks never dispatch work or reducer inputs.
`debugger_live` provides cooperative safe-point/permit coordination. Pause
cannot preempt callbacks or confirm actual host dispatch. `debugger_protocol`
is opt-in bounded framing and authority/revision checking for host-owned stdio;
there is no remote authentication or new default listener.

All these modules remain candidate finite surfaces. Check the accompanying
qualification receipts for which exact source snapshot and assertions executed;
module availability alone does not close N12 or the 144 qualification cells.

`debugger_diagnostic` provides explicit `DiagnosticHub`/`ScopedDiagnostics`
probes and markers. `configure(expected_revision, subscriptions)` changes pending
capture selection; each registered producer must acknowledge at a later safe
boundary. Existing producers retain their previous bounded selection until ack.
A probe closure is evaluated once for interested destinations and not evaluated
when none match. Sinks have independent permissions, record/byte quotas, loss
counters and drop policies. Scoped completion marks exceptional turns incomplete.
Exporter I/O is a separate explicit `drain_to` call; exporter faults consume the
failed record and increment health without rerunning capture or model work.
Diagnostic event JSON retains Python envelope field names. Integral identifiers,
counts, path indexes, source coordinates and float bits are decimal strings
(booleans remain booleans). Byte node values use `{hex, total_bytes}`; float node
values use `{type, decimal, bits}`. These display representations preserve raw
float bits, including NaNs and signed zero, and are not canonical value codecs.

Accepted protocol divergence: an unsized Python `int` scalar (the default
`python-int` display kind) is sent as `python-int:<decimal>` with
`kind=enum:integer`. Rust scalars always carry a fixed width (`u64:`, `i32:`,
...); Python cannot infer one without misrepresenting range, so projections that
need exact Rust wire parity must report an explicit width tag (for example
`('u64', '5')`). `watch_status` fields follow Rust order: `watches.total`,
`watches.truncated`, `watches.dropped_events`, `watches.observation_gaps`,
`watches.inspection_errors`, then the per-watch rows.

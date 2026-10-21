# Optional native Swift debugger diagnostics

Link and import the separate `StatelessDebug` library product explicitly. The
`Statelessness` core target has no dependency on it. This is an application-linked
cooperative companion, not LLDB/GDB, a remote authentication service, a model
loader, or a new engine default.

`EvidenceViewer` construction/read is passive and validates recording semantics
in addition to bounded structural decoding. Boundary zero is the retained
checkpoint. Boundary encodings are sensitive raw bytes. Legacy noninitial check
phases remain unknown unless the caller explicitly supplies a phase; v2 phase
boundaries are persisted. No historical output decoder is fabricated.

Explicit `verify` requires a transition budget and optionally cooperative
cancellation/deadline. `minimize` replays exact stored state/output/check evidence
before shrinking from a freshly decoded checkpoint. V2 phase verification is an
additional pass and shares overall transitions/time with the workbench pass and
original shrink validation. Declared environmental provenance requires an
explicit matching `InputPolicy`; generator causality applies independently.
Application callbacks are synchronous/cooperative, cannot be preempted, and may
allocate memory beyond debugger-owned bounds.

`DebugSession` requires explicit environmental input policy and input equality.
Typed state/input/output snapshots use the model's deep-copy hooks. Admission,
reducer execution, checking, recording, breakpoint evaluation, and post-commit
observation remain separate. Property failures survive observer/recording faults.
Sequence/revision/epoch IDs are full-width UInt64. Candidate tokens are private,
session/domain/revision bound. Sessions are confined to one synchronous owner;
reentrant mutations reject and these application values are not Sendable.

Verified forks replay the selected prefix before independently decoding the
checkpoint; failed boundaries reject normal branching. Build mismatch must be
explicit. Branch sidecars bind parent bytes/checkpoint identity with FNV1a64 for
accidental mismatch detection, not authentication. Exact branch recordings carry
parent identity/sequence/build/origin parameters. Forks neither restore a running
host nor dispatch external effects.

Inspection is a separate display schema, never an equality/replay key. Scalar
integer kinds/decimal text and float bits retain typed identities. Context work,
depth, nodes, bytes, value and page limits bound debugger-owned projections.
Redacted fields skip evaluation. Incomplete, opaque, redacted, paged, sampled,
unavailable and unstable-map comparisons remain Unknown. Structural diffs and
path resolution have independent work limits. Manually constructed callback
projections are validated before watch/probe retention.

Watches use explicit path authority, revisioned configuration, evaluation and
delivery sampling, sequence TTL, conservative baselines and bounded lossy rings.
Validated installation rejects redacted paths. Scoped diagnostic probes have no
global capture, clock reads, model calls or exporter I/O. No interested
subscription means the projection closure is not evaluated. Sink permissions,
config revisions, loss and exporter faults are independent from model outcomes.

`LiveBridge` consumes sealed core check results for host-executed turns and keeps
exact recording independent from a metadata-only lossy ring. A gap ends exact
capture; duplicate turns and unknown/duplicate dispatch origins reject.
`LiveController` is an explicit host-owned cooperative gate. Safe-point pause
acknowledgement cannot preempt a callback or pause remote work. Delivery/dispatch
permits have private owner identities and single-consumption bookkeeping.
Dispatch reservation remains uncertain until explicitly confirmed; dropped
permits are never retried. Reconnection changes command epoch while preserving
host effect-origin epoch.

Effect telemetry requires an explicit `DiagnosticClock` and host lifecycle
notifications. Timing-disabled paths read no clock. Missing/cross-domain/reversed
endpoints remain unknown/invalid measurements. Details and events are bounded;
active lifecycle authority is retained by private handle tokens even after
passive detail eviction. Counters/gauges and histogram snapshots are accounting
endpoints, not sums of diagnostic logs. Histogram sums retain 128 bits, overflow
quantiles are present with nil upper bound, and compatible windows subtract
cumulative counters/histograms while retaining final gauges. Numeric catalog
labels bound aggregate cardinality; request identities remain detail only.
Dashboard reports and causal joins are passive and do not prove host execution
or authenticate provenance.

Qualification is incremental. The first actual Swift full-suite job
`4f8647ce29b74caabf17a1dcd3b101c7` passed 144 tests for the then-current offline,
session and inspection slice; it does not qualify subsequently added modules.
All new native/build/installed tests run through Kyoto. Durable raw failed and
superseded logs, source manifests and fresh-process command receipts live under
`validation/native-parity/debugger-diagnostics/`. Broad N12 qualification and
profile promotion remain pending root review. Future instrumentation, full
metric-window retention/health/gauge-scope parity and every host lifecycle
combination require additional acceptance evidence; these APIs alone make no
full N12 closure or broad platform claim.

Host protocol bindings return `PreparedProtocolResponse`: preparation is passive,
with detached fields and a commit closure. The adapter validates the complete
reply against wire limits before invoking commit once. Hosts must reserve any
identities while preparing and keep failed commits from partially publishing
changes; the adapter cannot roll back arbitrary application callbacks.
Enumeration projections retain a variant child before the variant's object
fields, matching the Rust display path contract.

# N12 debugger and diagnostics source audit

This is a read-only map of [N12](../NATIVE-PARITY-PLAN.md#n12-debugger-and-diagnostics)
to the actual optional Rust companion and current native public APIs. No build,
test, benchmark, transport or application was executed for this audit. No runtime,
package, fixture or qualification input changes here. Existing qualification
receipts and historical guide statements are not new cross-language evidence.

## Authority and source boundary

The [companion exports](../../crates/statelessness-debug/src/lib.rs) include
session, workbench, evidence, inspection, watches, diagnostic, protocol, live,
effects, metrics and dashboard modules. It is an optional application-linked
package, not a model loader, effect runner, native process debugger or default
engine dependency. Plain simulation requires Model, not ModelCodec, Inspect,
Debug, Send, Sync or static model ownership. Additional adapters declare their
own bounds; effect telemetry separately uses synchronized host-owned capabilities.

Source authority is the public implementation, with the
[debugger guide](../DEBUGGER.md), [protocol](../DEBUGGER-PROTOCOL.md),
[live guide](../DEBUGGER-LIVE.md), [effect accounting](../DEBUGGER_EFFECT_METRICS.md)
and [passive reports](../DIAGNOSTIC_REPORTS.md) explaining intended boundaries.
Tests under `crates/statelessness-debug/tests/` provide future finite reference
inputs. Their existence or earlier local Rust results do not qualify native
equivalents, production hosts, other transports or platform adapters.

## Actual contracts and native gap map

| Family | Actual Rust boundary | Current native foundation and remaining gap |
| --- | --- | --- |
| Simulation | Typed single-owner DebugSession, explicit InputPolicy, sequence/revision, atomic turns and pre/post breakpoints | Native Model, checks, sealed tokens and Recorder exist; no corresponding optional session/controller surface identified. |
| Stored view/replay/fork | Immutable trace viewer/cursor, explicit bounded verification, verified pre-failure forks with provenance | Native audit/v2 artifacts and rich bounded replay exist; no viewer cursor, verified fork or branch-provenance helper identified. |
| Inspection/diff | Typed structural paths, independent display schema, bounded paged nodes and Unknown comparisons | Native replay differences exist; they are not structural inspection, redaction or field-diff APIs. |
| Watches/probes | Authorized boundary watches and scoped explicit local probes, independent bounded sinks/configuration | Native replay/search observers do not provide watch baselines, probe selection, capture revisions or sink lifecycle. |
| Local protocol | Bounded framed typed stdio, separate authorities/revisions/epochs, dedup and stale handles | Existing native conformance runners are not this debugger protocol. |
| Live observation/control | Already-checked host handoff plus separately authorized cooperative safe-point/permit gate | Native checked Recorder handoff exists; no bridge/controller, permit ownership or live dispatch accounting identified. |
| Effects/metrics | Host lifecycle notifications, explicit clocks, bounded catalogs/ledgers/histograms/windows | Outputs and v2 origins exist; they do not establish actual dispatch, durations, metrics or settlement. |
| Passive reports/offline minimization | Payload-free effect dashboard, causal origin join, passive v1/v2 failure selection then explicit phase-aware minimization | Native diagnostic JSON/v2 phase lookup and shrink exist; no equivalent dashboard, EvidenceViewer or debugger-policy-aware minimization adapter identified. |

This is a scoped public-surface finding from the inspected Python, Go, TypeScript
and Swift runtime sources, not a claim that applications cannot implement similar
logic manually. An optional native debug package should reuse those foundations
without making normal engine imports load a transport, clock, watcher or exporter.

## Simulation, verification and branching

[session.rs](../../crates/statelessness-debug/src/session.rs) separates input
admission from reducer disposition. Declared policy callbacks and bounded finite
enumeration can reject an input before delivery; unrestricted policy explicitly
labels out-of-domain experiments. Enumerated permission can inspect the candidate
budget plus one lookahead. Candidate tokens bind input, session incarnation,
revision and domain, rather than reusing a mutable list index.

Initial checks occur once at sequence/revision zero. New calls initial_state
before initialize validates identity/positive work budgets; from_state avoids
initial_state. This order must be tested rather than claiming all invalid session
arguments precede every application callback. Initial check/capture failure can
produce a terminal session with diagnostic information.

A pre-breakpoint consumes neither sequence nor revision; repeating the same
pending input at the same revision bypasses that boundary once. A successfully
returned transition, including ignored/rejected disposition, increments both
counters and is checked once. Reducer error does not manufacture a turn.
Checker failure retains the actual transition with incomplete checks. Completed
checks are handed to the recorder through the sealed token. Property failure
survives recorder/observer diagnostic faults. The actual state/last turn commits
before the observer; observer error ends the session and records an issue, only
supplying a new stop reason if none already exists. Rust does not promise panic
rollback or recovery from a blocking callback.

Post-breakpoints observe the completed turn. run_bounded requires a positive
explicit count at most max_continue, stops on a stop reason, and otherwise marks
budget exhaustion after the requested count. It does not drain modeled queues on
its own. cancel ends the session without inventing a model transition. Arbitrary
field assignment and continuation past terminal property failure are not exposed.

[workbench.rs](../../crates/statelessness-debug/src/workbench.rs) keeps viewing
passive. TraceViewer validates recording semantics, owns evidence, tracks a local
cursor and retained checkpoint range, and returns bounded byte previews labeled
stored-exact-encoding. Generic ModelCodec cannot decode historical Output;
HistoricalOutputDecoder is an independent optional adapter. Verification executes
only an explicitly supplied model and does not move the cursor or mutate parent
evidence. Existing replay differences retain all mismatch flags and a primary
divergence; inspection differences are a separate diagnostic comparison.

fork_verified verifies the selected pre-failure prefix with the selected build
before decoding its checkpoint and creating a separate recording session. It
checks environmental provenance, allowing only an explicit unrestricted change,
and stores parent/checkpoint bindings, identities, fork position and verified
status. FNV-1a plus byte count detects accidental mismatch, not authenticity.
Snapshot isolation remains the application's responsibility for shared mutable
handles, queues and oracle history; no live OS resource restoration is implied.

## Inspection and capture privacy

[inspect.rs](../../crates/statelessness-debug/src/inspect.rs) uses typed field,
variant, index and map-key segments, not ambiguous dotted expressions. It checks
display schema/version, path/page bounds and retained node budgets. Builtin work,
depth, nodes, total bytes, selected value bytes and page size are independently
bounded. Custom inspectors remain cooperative. Integer nodes retain their type
and decimal precision, including 128-bit values; float display carries type,
decimal and bits and is not permission to add a canonical float codec.

Redacted fields are not evaluated. Opaque, unavailable, sampled, paginated and
truncated data retain explicit incompleteness. compare_nodes returns Unknown
before equality when either node is incomplete; incompatible schemas/paths also
yield Unknown. Equal redacted placeholders cannot establish equality. Maps expose
explicit Stable or Unstable ordering; unstable maps remain incomplete for equality
and positional page indices are not stable identities. Preserve structural keys,
ordering labels and bounded pages rather than silently treating arbitrary host
object enumeration or unconstrained Debug/string conversion as a stable view.

[watches.rs](../../crates/statelessness-debug/src/watches.rs) separates capture
configuration from model revision. Validated installation resolves an authorized
path at a safe boundary; generation/effective sequence, enable/disable/pause/
resume/remove and bounded failure snapshots are observable. Disabled/re-enabled
watches establish a fresh baseline under the configured retention policy. TTL,
sampling and cooldown use explicit observation boundaries, not model/clock time.
Stale observations, gaps, schema/source changes, ring loss and baseline eviction
are separate facts. No interested consumer means no schema/projection callbacks.
A boundary watch cannot see an internal 0→1→0 excursion before commit.

[diagnostic.rs](../../crates/statelessness-debug/src/diagnostic.rs) uses an
explicit DiagnosticHub and ScopedDiagnostics argument, not a global subscriber
or reducer re-execution. Unselected probes skip the closure/inspection/formatting;
a selected closure is evaluated once for multiple sinks. Host-approved typed
selectors are ANDed, unknown metadata does not match, and pre-check property
status cannot be treated as a known successful check. A later trigger cannot
recover a previously unselected in-reducer value. Rust's optional compiled-out
macro path needs separate compilation evidence; native implementations need an
honest zero-evaluation disabled path, not a universal zero-overhead claim.

Configuration requires expected capture revision and producer acknowledgement at
an effective boundary; unacknowledged producers retain old configuration. Sink
authorization/redaction happens before queueing. Each sink has independent
record/byte bounds, drop policy and health outside the queue. Scoped early drop
publishes incomplete data. Draining text/JSONL/custom exporters occurs outside
the reducer and effect locks; sampling, filtering, truncation, abandonment and
exporter failures remain distinct. Exact artifacts can contain secrets even when
these diagnostic projections are redacted.

## Protocol and cooperative live ownership

[protocol.rs](../../crates/statelessness-debug/src/protocol.rs) is version-one
local stdio: nonzero four-byte big-endian frame length, bounded allocation, then
tab-separated percent-escaped UTF-8 typed fields. It is not JSON-RPC or a network
listener. Partial frames deliver no command; complete malformed frames have a
bounded error response. Tagged integers must retain full precision.

Envelope identity/version/epoch, authority, argument budgets and relevant
execution/configuration revisions precede command callbacks. Authorities for
input delivery, watch configuration, diagnostics configuration, exact value
reads and exact trace export are independent and default false. Read-only
simulation is not live observation. No arbitrary path, code evaluation, remote
authentication, unbounded continue or effect-dispatch command is supplied.

Identical cached requests replay their original response; conflicting reuse
fails. Admitted request IDs retire behind an epoch-local high-water mark even
after cache eviction, including failed admitted requests. Old uncertain commands
cannot silently rerun. Reconnect changes epoch and invalidates handles/candidates/
dedup state without widening authority or replacing model state. Epoch allocation
is process-local, not durable across host restart. A write can fail after a real
transition committed; this is bounded local dedup, not global exactly-once I/O.
Snapshot handles are revision-bound; stale views do not read mixed revisions.
Telemetry snapshot/configuration revisions are separately staged, not model turns.

Default wire projections omit callback/failing-check detail strings that may
contain secrets. First-failure ID/index/sequence/phase is reported independently
of a truncated check-list prefix; truncated display must not hide a later first
failure or imply complete checking. Exact trace export is a separately authorized
bounded chunk operation and grants no filesystem-path authority.

[live.rs](../../crates/statelessness-debug/src/live.rs) has two separate surfaces.
LiveBridge attaches a coherent already-checked checkpoint at an explicit host
sequence/nonzero epoch and accepts actual CheckedTurn tokens without reducing or
checking again. Missing/duplicate sequences, identity changes, capture freeze,
retention and bounded event loss remain visible. Output-request and explicit
effect_dispatched facts are separate; replay never dispatches those outputs.

LiveController requires host opt-in. request_pause becomes acknowledged only at
safe_point; authorize_one grants at most one buffered delivery. Pending host work
is not interrupted. Delivery/dispatch permits are unforgeable controller-bound
ownership, not matching integers: stale/cross-controller/consumed permits reject.
Dispatch reservation is not confirmed dispatch. In-flight uncertainty blocks
unsafe replay/reconnect; failure cannot authorize an effect retry. External
arrival rejection returns host-owned input. Queue count/declared-byte/output
bounds, detach/overflow policies and terminate-during-turn accounting are explicit.
Pause does not freeze workers or the world. Native tokens need equivalent private
integrity/lifetime rules without claiming a security sandbox or Rust move types.

## Effect timing, metrics and passive reports

[effects.rs](../../crates/statelessness-debug/src/effects.rs) receives host facts:
request, admission, attempt creation/ready/start/known finish, logical resolution,
publication reservation/confirmation, actual delivery and explicit settlement.
Separate effect/attempt/delivery handles preserve identity and bounded local
dedup. Cancellation request/acknowledgement do not establish physical termination
or decrease running attempts. Publication reservation precedes visibility under
the host's queue synchronization; delivery may race confirmation. Telemetry
failure must not skip/retry real work or replace its actual result.

Clocks have explicit domains and integer nanoseconds. Missing, reversed,
unavailable or cross-domain endpoints are unknown/errors, not zero; observed
coincident endpoints can be zero. Future instrumentation starts at actual first
poll, not construction; abandonment is not success or remote cancellation.
Runtime times stay outside model equality, oracle state, canonical codecs and
logical time. Pause-affected samples retain that label instead of subtracting
overlapping intervals blindly.

[metrics.rs](../../crates/statelessness-debug/src/metrics.rs) aggregates
authoritative lifecycle observations independently of log sampling. Finite
catalog-bound numeric labels, series/detail/window caps and explicit losses
prevent arbitrary payload/ID dimensions. ReplayHarness/ImportedLive do not ingest
live aggregates; TestClock and controlled origins retain their own labels and
are not ordinary production samples. production() filters accordingly. Gauges
distinguish complete-host participation from observed-since-attach; detail eviction
does not terminate active work or manufacture a zero gauge.

Histogram quantiles are bucket-upper-bound estimates with sample counts and
low-sample warning, not exact measured percentiles. Empty or invalid quantiles
are None. Overflow has no finite upper bound. Cumulative/delta temporality,
compatible snapshot epochs/schema and independent clock intervals remain visible.

[dashboard.rs](../../crates/statelessness-debug/src/dashboard.rs) passively
projects existing snapshots/health into bounded payload-free reports. Missing
series stay None/null; row truncation and completeness are explicit. A health
endpoint and metric snapshot are not promised atomic. Numeric labels must not
become request/effect IDs or correlation strings. correlate joins v2 evidence
and a host timeline by decimal run/machine, epoch, sequence and output index;
retained match, before-checkpoint, missing identity and conflict are distinct.
A successful join neither certifies that an effect ran nor reconstructs omitted
history or moves times into modeled logical time.

## Passive evidence and offline minimization

[evidence.rs](../../crates/statelessness-debug/src/evidence.rs) owns a bounded
v1/v2 Artifact. EvidenceViewer adds semantic v1 recording validation and quota
preflight; raw Artifact::read(V1) alone is structural. Viewing boundary zero is
the retained checkpoint, not necessarily run start. Before/after/input/output/
checks and optional causal row remain stored data, not model execution.

Failure selection requires a recorded failed check. Initial phase is known;
noninitial v1 phase is unknown and cannot be invented from its check ID or order.
V2 persists the state/transition split. Before v2 minimization, the viewer performs
exact bounded replay, validates actual split/phase and failure reproduction,
charges verification transitions and elapsed time against the same overall
budget, then delegates remaining budget. Cancellation/deadline checks occur at
cooperative observation boundaries; blocking callbacks are not preempted.

workbench::minimize restores the saved checkpoint for every candidate and
requires a stable property/phase plus Generate causality. Recorded declared
environment policies require minimize_with_policy and a positive bounded
admission scan with one lookahead; matching labels do not authenticate callback
implementation. EvidenceViewer::minimize currently delegates the legacy policy
entry point, so it does not magically supply a declared policy callback. A
policy-bearing workflow must use the explicit API and preserve v2 phase checks.
Attempt/transition/cancellation/deadline limits and raw callback limitations
remain those of the underlying search; a smaller tested reproducer is not a
global-minimum proof.

## Finite implementation and acceptance proposal

Start with an optional **offline evidence workbench**, not a simultaneous port of
all companion modules. Reuse native Artifact/v2 validation, rich bounded replay,
phase lookup, Recorder and shrink. Implement passive boundaries/failure selection,
explicit verification and checkpoint-based policy-aware minimization. Prove these
with independently authored expectations and actual public Rust calls first:

- Passive v1/v2 viewing executes zero initial/reducer/checker/codec callbacks;
  v1 viewer rejects a structurally readable semantic continuation after failure.
- Boundary zero and a retained suffix expose the correct checkpoint, previous
  state, ordered outputs/checks and local versus host sequence. Bounded previews
  mark truncation; output bytes do not require an output decoder.
- Selecting passed/nonexistent checks rejects. V1 noninitial phase remains
  unknown; v2 duplicate property IDs in opposite phases select by persisted split.
- Verification exposes all replay mismatch flags and actual completed results,
  preserves the view cursor/artifact, and never dispatches an output.
- V2 phase verification consumes the same transition/time budget as shrinking;
  cancellation before and during verification returns bounded failure. Declared
  policy omission/label mismatch and candidate-overflow lookahead reject.
- Each shrink candidate starts from the retained checkpoint, respects environmental
  admission and causality, and retains the selected phase/property. Parent bytes
  stay unchanged; no minimality claim is inferred.

Then qualify bounded typed simulation and verified forks with a small counter/
queue fixture: no-codec model, initial failures, accepted/rejected/ignored turns,
one-pass checking/capture, breakpoint bypass, stale candidates/revisions,
reducer/checker/recorder/observer errors, exact-prefix fork and branch isolation.
Keep structural inspection/diff and watches a separate reviewed milestone before
exposing protocol mutations. Real OS-pipe protocol tests must cover malformed and
partial frames, full-width values, authority matrix, duplicate/retired requests,
stale handles/configuration, reconnect and failed writes after commit.

Live gates need independent finite state-machine/reference comparison plus a
synthetic host fixture proving one reducer/check pass and explicit dispatch
counts. Scoped diagnostics need disabled closure=0, selected closure=1 across
multiple sinks, producer acknowledgement, redaction before queueing, independent
drop/loss counters and exporter failure outside model execution.

Use injected test clocks for future metrics proof: request/ready=0, start=12,
finish/resolution=96, publication=97, delivery-begin=100 and observed=102
give queue 12, elapsed 84, preparation 1, completion-queue 3, processing 2 and
delivery-begin end-to-end 100 in the chosen unit. This is arithmetic, not a speed
measurement. With histogram bounds [10,20] and samples [0,10,11,30], counts are
[2,1,1], p50 upper bound 10 and p95 is a present Quantile whose upper_bound_ns is
None (dashboard null bound), observations=4 and low_sample_warning=true. This is
not an absent quantile or lost sample count. Add cross-domain/missing endpoints,
late completion after cancellation,
replay/import non-ingestion, pause labels, gauge participation, series/window
eviction and causal join conflict cases. Passive reports must retain nulls/loss/
truncation and enforce exact output byte caps without publishing partial success.

Every milestone needs separate installed-package and fresh-process evidence with
explicit source/command identities, followed by unchanged baseline regression.
Use Kyoto for eventual builds/executions. Other platforms, browser transports,
production scheduling, LLDB/GDB, remote authentication, arbitrary restoration,
telemetry backends, performance and full N12 closure remain unqualified until
their own explicit acceptance evidence exists.

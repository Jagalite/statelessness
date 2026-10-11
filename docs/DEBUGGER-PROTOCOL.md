# Bounded debugger stdio protocol v1

`statelessness-debug::protocol` is an optional, dependency-free adapter around an
explicitly linked `DebugSession`. It is a local, single-owner simulation harness.
It never loads a Rust model named by input, starts a socket listener, evaluates
code, opens arbitrary paths, restores live state, or dispatches modeled effects.
The separate host APIs are documented in [DEBUGGER-LIVE.md](DEBUGGER-LIVE.md).

## Run the model-linked harness

```sh
cargo run -p statelessness-debug --example debug_stdio --offline
# Optional: -- --fixed, -- --read-only, -- --export-exact, -- --probe-profiles
```

The fixture session identity is `request-lifecycle`, the first connection epoch
is 1, and the initial execution/configuration revisions are both 0. The read-only
flag removes input delivery authority; authorized watch configuration remains
available. Unknown startup options are rejected before creating the session, so
a misspelled authority flag cannot silently enable input delivery. Mode is still
`simulation`, because this harness owns simulated model state. It does not
mislabel a read-only simulation as a running host observer.

stdin and stdout carry only framed protocol bytes. Capture stderr separately.
The typed Rust equivalents are `Request`, `Response`, `Command`, `ProtocolSession`
and `serve`. Clients should use `encode_request`/`decode_response` and
`write_frame`/`read_frame` rather than reconstructing the grammar.

## Framing and envelope

Each frame is a nonzero four-byte big-endian unsigned length followed by exactly
that many bytes. The default maximum is 65,536 bytes. Oversize lengths are rejected
before allocation. Partial headers and payloads are transport errors; no command
is delivered. EOF between frames cleanly disconnects. A malformed complete frame
receives a bounded error response with request ID 0, then the server may continue.

Payloads are tab-separated percent-escaped UTF-8 fields. Only ASCII letters,
digits, hyphen, underscore, dot and colon pass through unescaped. Percent itself,
control characters, terminal escapes, markup delimiters and non-ASCII bytes are
escaped. Decoded strings must still be displayed as text, never interpreted as
HTML or terminal commands. Escaping is framing/display safety, not redaction.

Request fields, in order:

1. `DDBG`
2. `u32:1`
3. `request`
4. `string:<session>`
5. `u64:<connection epoch>`
6. `u64:<request ID>`
7. `u64:<expected execution revision>`
8. `u64:<expected watch configuration revision>`
9. Command identifier
10. Command arguments, if any

Response fields, in order:

1. `DDBG`, `u32:1`, `response`
2. `string:<session>`, `u64:<epoch>`, `u64:<request ID>`
3. `u64:<current execution revision>`, `u64:<configuration revision>`
4. `ok` or `error`, then `none` or the error category
5. `u64:<result field count>`
6. That many key/value pairs

Keys are protocol-defined strings. Values carry tags such as `u64:`, `u128:`,
`i64:`, `bool:`, `string:`, `bytes:` (hex), and `enum:`. Rust integer precision is
preserved, including `u128::MAX`; clients must not route integers through IEEE-754
numbers. Float inspection includes its type, decimal representation and bits.

An event envelope is `DDBG`, `u32:1`, `event`, session, epoch, stream sequence,
execution revision, configuration revision, and event kind. The stdio server
returns events through bounded polling rather than interleaving unsolicited
frames. Stream sequence increases even when a queue is full, making subsequent
gaps visible. Persistent drop counters are outside the saturated queue.

## Commands

Arguments marked `N` use `u64:<decimal>`. Paths are zero or more structural
segments: `field:<id>`, `variant:<id>`, `index:<decimal>`, `key.string:<value>`,
`key.bool:<value>`, `key.char:<value>`, or `key.<integer type>:<decimal>`.
Fields and map keys are not ambiguous dotted expressions.

| Command | Arguments | Behavior |
|---|---|---|
| `status` | none | Mode, phase, sequence, capabilities, stop category, limits and event loss |
| `snapshot` | none | Create an acknowledged revision-bound handle |
| `export_trace` | handle N, byte offset N, byte limit N | Explicitly authorized bounded raw exact artifact chunk |
| `inspect` | handle N, schema N, offset N, limit N, path | Bounded structured projection |
| `checks` | handle N | Check IDs/statuses, completeness, explicit truncation |
| `outputs` | handle N | Ordered output count and disposition; exact bytes only with host opt-in |
| `inputs` | offset N, limit N | Candidate tokens, completeness and next offset |
| `select` | token N | Revalidate and deliver the exact bound candidate once |
| `step` | `bytes:<hex>` | Decode/admit one input through the linked model's codec/environment |
| `events` | limit N | Drain a bounded metadata event batch, with cumulative loss |
| `cancel` | none | End this simulation with cancellation outcome |
| `watch` | schema N, installation revision N, path | Install an authorized change watch |
| `unwatch` | watch ID N | Remove one watch under configuration CAS |
| `watch_status` | none | Bounded watch list and diagnostic health |
| `watch_current` | watch ID N | Latest authorized projected value/event, or unavailable |

Capabilities are authoritative. Missing inspection/watch/telemetry adapters return
`unsupported`. No command in this transport promises unbounded `continue`, filesystem
export paths, branch creation, arbitrary field assignment, remote control, or effect
execution; those capabilities remain false or available through separate typed
APIs. The current fixture codec encodes Start as `00`, Cancel as `01`, and
Complete(1) as `0201`.

`inspect` returns a recursively flattened tree with node kind, typed value,
completeness, child count, page offset/total/next offset, and structural child
segments. Exact state bytes are not a default display format. Redacted/opaque/
unavailable/truncated values remain explicitly distinct and never imply equality.
Error strings and failed-check messages are intentionally omitted from the default
wire projection because they may contain application secrets. `checks` returns
`checks.first_failure.id`, `checks.first_failure.index` (zero-based), and
`checks.first_failure.sequence` independently of the bounded check-list prefix.
`checks.first_failure.phase` distinguishes `initial_state`, `post_state`, and
`transition` from the actual typed check boundary, including when IDs are shared
between phases. Truncation cannot hide a later first failing property. With no failed check,
`checks.first_failure` is `none`; this does not override `checks.complete`.

## Admission, authority, handles and retries

Every call is serialized by exclusive mutable access. Envelope identity, protocol
version, frame/argument limits, authority and expected revision are checked before
input delivery. Session input policy then validates environmental permission.
Application acceptance/rejection/ignoring remains a separate completed outcome.

`ProtocolAuthority` independently grants `deliver_inputs`, `configure_watches`,
`configure_diagnostics`, `read_exact_values`, and `export_exact_trace`. All default
to false. Only the host enables an inspector, provides a watch registry/path
allowlist, attaches a telemetry observer, or exposes approved probe profiles/sinks.
Reconnect never widens these permissions. Exact byte exposure is an explicit host
choice and remains sensitive even when inspection has redacted fields.

Watch updates compare their own configuration revision. Installation also names
the execution revision being inspected; the acknowledgement returns generation,
resolved schema/type where available, and effective sequence. The baseline is
captured at the next effective observation boundary. Watch configuration cannot
advance model sequence, logical time or exact trace contents. A stale execution
view cannot install a watch against a different baseline. Read-only clients may
have watch configuration permission without input delivery permission.

New request IDs must increase strictly within an epoch. Repeating an identical
request still in the cache returns its original response, including the original
revision. Reusing an ID for different content returns `duplicate_conflict`.
Once an ID is behind the high-water mark and absent from the bounded cache,
`retired_request` is returned even if that ID was skipped. Reconciliation is
required; no uncertain old mutation is retried. Failed admitted requests retire
IDs too. Identity/version failures before admission do not retire an ID.

This is bounded local deduplication, not network-wide exactly-once delivery. An
I/O error can occur after the model transition committed. Reconnect changes epoch,
invalidates handles/candidates and dedup state, and preserves model state. Query
status before deciding what to do next; a stale expected revision prevents blind
resubmission. A cached response is not a fresh status query.

Snapshot handles identify the current acknowledged immutable revision, without
cloning arbitrary application state. After an actual turn, old handles return
`stale_handle`; they never read a mixture of old and current fields. Candidate
tokens retain a bounded canonical encoded input bound to that revision/domain;
selection decodes and revalidates that value instead of reusing an old list index.

## Bounds and failure semantics

Default limits: 64 KiB frames, 64 request fields, 4,096 response fields, 8 KiB per
field, 8 KiB input budget (also limited by hex field/frame expansion), 64 cached
requests, 4 MiB aggregate dedup storage accounting, 64 snapshot handles, 256
candidate records, 256 KiB candidate storage accounting, 1,024 stream events,
256 KiB stream storage, 100 items per query page, 64 host probe profiles with
256 KiB profile storage accounting, and an 8 MiB telemetry snapshot capture cap. Serialization rejects an
oversize response rather than sending a misleading partial success. Watch and
inspection adapters have additional independent count/depth/byte budgets.

Cache/candidate byte accounting includes owned payload and record/field headers.
Collection capacities, allocator overhead, model callback allocation and blocking
behavior are not hard process-memory bounds. Only one request is processed at a
time; stdio writes may block. Use process isolation when hard deadlines are needed.

Errors are stable categories: `malformed`, `unsupported_version`, `wrong_session`,
`wrong_epoch`, `unauthorized`, `stale_revision`, `stale_configuration`,
`stale_handle`, `duplicate_conflict`, `retired_request`, `limit`, `unsupported`,
`invalid_input`, `ended`, `disconnected`, `callback`, `exhausted`, and `busy`.
`busy` reports a host-held shared diagnostic borrow without panicking or mutating.

A transport/inspector failure cannot roll back an already executed transition,
rewrite a property failure as success, rerun a reducer, or dispatch an output.
The exact recorder and diagnostic queues remain independent evidence paths.

## Qualification

`cargo test -p statelessness-debug --test protocol --offline` covers framed model
execution, malformed/truncated frames, parser corpus mutation, escaped controls
and markup, full-width integer inspection, stale candidate/view/config revisions,
read-only watch authority, dedup conflicts/eviction, reconnect, saturated streams,
failed writes after delivery, and exact trace equivalence with watches enabled.

The `debug_stdio` executable was also exercised as a real subprocess over OS pipes:
Start, duplicate Start request, Cancel, Complete(1), status and EOF produced three
actual turns, one first property failure and a clean exit. It did not dispatch
real effects. These are local/synthetic integration qualifications; no remote
transport, production application or cross-language debugger qualification is
claimed.

## Optional host telemetry queries

A host may call `attach_telemetry(observer.clone())` independently of modeled-input
permission. This adds bounded diagnostic reads only. The standalone lifecycle
fixture does not pretend that its modeled outputs were real host effects and
therefore does not automatically attach or populate this adapter.

| Command | Arguments | Behavior |
|---|---|---|
| `metric_catalog` | offset N, limit N | Instrument name/kind, units, population and reset contract |
| `metric_snapshot` | `none` or handle N, offset N, limit N | Capture then page one immutable metric snapshot |
| `metric_window` | start revision N, end revision N, limit N | Difference retained compatible endpoints; return a snapshot handle |
| `effect_details` | run N, host epoch N, effect serial N | Host request/admission/resolution/delivery/settlement facts |
| `effect_timeline` | run N, host epoch N, effect serial N, limit N | Bounded lifecycle events and measurements with truncation metadata |
| `telemetry_health` | none | Loss, unknown/invalid data, memory accounting and available window revisions |

A new metric snapshot requires `none` and offset zero. Its returned `metrics.handle`
is used to page without mixing different captures while host activity continues.
Only one such capture is retained; successfully returning another capture or
reconnecting invalidates the old handle. An oversized new snapshot or window
response leaves the previously acknowledged capture and its handle intact, and
does not advance snapshot revisions or evict retained host window endpoints.
Snapshot validation is staged before publication; real clock reads/fault health
are independent observations. Metric revisions/epochs are explicitly separate from execution
revisions and connection epochs. Window errors/evicted effects return
`stale_handle`; they never manufacture empty success or zero-duration samples.

The default separate retained telemetry-capture budget is 8 MiB. Host observers
whose configured metric/detail query bounds exceed it cannot be attached. Query
responses still have the 64 KiB frame bound. Use small pages (one series is always
a useful starting point for histograms); an oversized requested page returns
`limit`, allowing a smaller retry with a fresh request ID and the same handle.

Metric output includes completeness, gauge scope, cumulative/delta temporality,
clock domain and interval, schema ID, finite bucket boundaries, counts, populations,
origins, outcomes, debugger-interference labels and p50/p95/p99 bucket-upper-bound
estimates with sample count/low-sample warning. Histogram storage and durations
are tagged integer nanoseconds; catalog export units are seconds. Unknown endpoints
remain unknown, and empty histogram percentiles are unavailable. A response is a
read of existing accounting, never a new measured effect or replay ingestion.

The timeline limit bounds events and timing observations separately and marks
omitted rows explicitly. IDs, origin sequence/output index and attempt/delivery
identities are serialized as tagged integers. Admission, attempt completion,
logical resolution, observed input disposition and explicit settlement remain
separate fields. Cancellation requests do not imply completion. These queries do
not run effects, reducers, retries or completion delivery.

Watch mutation acknowledgements reserve their identity/generation/boundary fields
before installation. Optional schema/type labels that exceed the remaining wire
budget are omitted with `watch.metadata_complete=false`; an installed watch is
never reported as a failed mutation merely because its label was too large.
The host cannot replace an already attached watch registry in-place and thereby
reset the configuration revision. `enable_watches` reports an error on replacement.

Reproduce the real-pipe checks with:

```sh
cargo build -p statelessness-debug --example debug_stdio --offline
python3 scripts/check-debugger-stdio.py
```

The script additionally launches `--read-only`, verifies input rejection, and
installs an authorized watch without advancing the model.

## Saving exact failure evidence

Raw exact trace export requires the separate `export_exact_trace` authority. It is
not granted by watch configuration, ordinary inspection, or `read_exact_values`.
The example enables it only when launched with `--export-exact`. A client requests
a current `snapshot`, then `export_trace` chunks using that handle and execution
revision until `export.complete=true`. Responses include total bytes, requested
offset, returned bytes, next offset, retained/evicted steps and the capture stop
category. The artifact is explicitly `exact_trace_sensitive` and `redacted=false`.

The requested limit must be positive and fit a hex-encoded field; oversized or
overflowing offsets reject. Each request streams the recorder through a bounded
window writer that owns only its requested chunk, not a whole cloned trace. It
still visits the bounded recorder artifact, so very small chunks can repeat
serialization work. The transport cannot open or choose filesystem paths.
Clients decide where to save received bytes and must apply their own sharing
policy. A turn invalidates the old handle; watch-only updates do not alter the
artifact. Retained history may start at an evicted checkpoint, and a coherent
prefix is not a claim that an entire host lifetime was captured.

Tests reconstruct `.sttrace` bytes from 31-byte chunks, parse them with the strict
trace reader, compare them with the typed export and reproduce the first failure
through verified replay. They also qualify independent raw authority, stale
handles, empty end-of-artifact chunks and oversized/overflowing offsets.

Connection epochs are allocated uniquely within the host process for every new
protocol instance and reconnect, including when a display session ID is reused.
An old incarnation's command cannot be accepted by its replacement. A client may
send `status` with epoch zero to discover the current epoch; epoch zero never
admits mutations. The discovery request participates in ordinary request-ID
ordering, so use the next ID after its response. Process restart is a separate
reconciliation boundary: epochs and dedup history are not durable across process
loss, and clients must discard outstanding commands and establish the new run.


## Optional host-approved scoped probe profiles

`attach_probes(Rc<RefCell<DiagnosticHub>>, profiles, exposed_sinks)` attaches one
explicit capture configuration domain. Each `ProbeProfile` maps a numeric ID and
bounded display label to a host-constructed `SelectedSubscription`. The host
chooses site, projection path, trigger, sampling, severity, sink and typed metadata
selectors. Remote clients select IDs only; they cannot submit expressions, change
selector definitions, add sinks or widen sink projection permissions. Profile
selection still passes the hub's authorization and bounds checks.

| Command | Arguments | Behavior |
|---|---|---|
| `probe_status` | offset N, limit N | Paged profile catalog, producer acknowledgements and exposed-sink health |
| `probe_configure` | expected capture revision N, zero or more profile IDs N | Replace selected profiles under independent capture CAS |
| `probe_events` | sink ID N, limit N, `bool:<include payload>` | Read one explicitly exposed bounded diagnostic queue |

`configure_diagnostics` is separate from input delivery, state-watch configuration
and raw artifact export. Read access comes only from the host attaching approved
profiles and exposed sink IDs. An unattached adapter returns `unsupported`.
Configuration replaces the attached hub's complete selected-subscription set, so
the host must dedicate that domain or explicitly approve this replacement scope.
An empty profile list disables capture in that domain. Unknown IDs, stale capture
revisions, duplicate IDs, missing authority and conflicting shared borrows reject
before capture changes. The adapter reserves its bounded acknowledgement before
mutating the hub and returns the new actual capture revision and pending producers.

The envelope's configuration revision remains the **state-watch** revision.
Probe capture revision is independent, explicitly named in arguments/results and
does not advance model execution. Only the host producer may call `acknowledge`
at its real ordered boundary. A configuration response never claims all workers
have installed the change: status reports producer ID, epoch, acknowledged capture
revision, effective transition and pending count. Static metadata selectors run
before payload closures. Changes from another host owner are visible in the actual
hub revision; selected profile identity is marked unknown until this adapter
performs the next configuration.

Status offset/limit pages profile, producer and exposed-sink lists independently;
it is a fresh live status view, not an immutable multi-page capture. Check each
list's total/truncation metadata and use smaller pages when needed. Event polling
preflights the entire borrowed batch before consuming any queue entry. An oversize
full payload returns `limit` without removing the head event; retry with a smaller
batch or `include payload=false`. The latter is an explicitly lossy summary that
omits payload and optional site/path/schema metadata with `metadata_omitted=true`.
It can consume a large event even at the minimum 2 KiB frame / 64-byte field limits.
Persistent dropped/exporter-failure counters remain visible outside the queue.
A duplicate cached poll returns the original batch without consuming another.

The example's explicit `--probe-profiles` flag attaches a separate synthetic test
producer with profile 1 and sink 1. Host startup configures/acknowledges revision 1
and records one `fixture.seed` value (7). Reconfiguring through the protocol leaves
that producer pending; the fixture does not invent a subsequent safe-point ack.
The real-pipe script verifies the seed projection, revision-2 reconfiguration,
pending status and unchanged model sequence in read-only mode. Typed integration
tests additionally cover selectors before evaluation, denied sink access, stale
and duplicate configuration, shared-borrow contention, paging at maximum catalog
counts, oversized payload retention and metadata-summary consumption. These tests
exercise host callbacks directly; they do not claim remote worker coordination.

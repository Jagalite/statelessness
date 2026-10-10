# Statelessness Rust Transition Debugger

## Design, observability, and implementation plan

**Version:** 1.1 — proposed design; logging, timing, and metrics update  
**Prepared:** October 10, 2026  
**Repository:** Jagalite/statelessness  
**Reviewed baseline:** `cb7f256e4d7cf20cfab6018b9bbba391ec2dd9de`  
**Scope:** Rust debugger, selective logging and probes, host effect timing, bounded metrics, application integration, and optional frontend protocol

> Build a transition debugger and replay workbench, not a replacement for LLDB or GDB. Deliver one modeled input, execute the application transition once, inspect the complete observation, and preserve the result as reproducible evidence.

### Executive decision

Implement an optional, headless-first debugger around the existing `Model`, checking, codec, recording, and replay contracts. Start with simulated execution and trace inspection. Add live observation after those semantics are qualified; make live control a separately gated, cooperative application capability.

Use macros to generate inspection metadata, source mappings, explicit lazy probes, and ordinary adapter code. Do not require macros for debugging, rewrite arbitrary reducers into resumable programs, or make diagnostic instrumentation part of application state equality.

Treat logging, timed spans, and metrics as first-class optional consumers of shared observations, not as a replacement for exact recording. Measure effects at real host boundaries; aggregate metrics before log sampling. Keep runtime clocks and measured durations outside deterministic semantics unless explicitly supplied as modeled inputs.

The application continues to own its state, reducer, environmental model, and effect execution. Statelessness supplies controlled modeled execution, checks, observations, and evidence. “Full state” means the complete modeled state, not a snapshot of every operating-system resource in the process.

### Document status

This is an implementation proposal, not a description of a shipped debugger. Existing capabilities are identified in Section 2 and supported by repository references. Names introduced for new APIs, crates, attributes, protocol messages, and commands are proposed. No debugger implementation, benchmark, or qualification run was performed for this document. The pinned baseline and existing-code inventory are retained from the preceding source review; this revision is a documentation update, not a new repository audit. New observability references were checked on October 10, 2026. [R0–R7, T9–T17]

**Revision 1.1:** integrates runtime subscriptions, state watches, function-local probes, sinks, effect lifecycle timing, metrics, resource budgets, acceptance tests, and two new milestones. Section 21 records the consistency review and resolved design issues. The original replay and exactly-once application-execution boundaries remain intact.

<!-- pagebreak -->

# Reading guide and decision register

### Reading guide

**Product and semantics:** Sections 1–4 define the scope, existing foundation, operating modes, and the atomic transition contract.

**Implementation architecture:** Sections 5–11 define the observation API, inspection layer, input selection, replay model, macros, package boundaries, and command protocol.

**Operations and observability:** Sections 12–16 define live integration, security, resource limits, logging, effect timing, and metrics.

**Qualification and delivery:** Sections 17–19 define regression tests, milestones, and engineering workstreams.

**Worked example, review, and sources:** Section 20 walks through the existing request-lifecycle fixture; Section 21 records the design review and requirement coverage; Section 22 lists technical references.

### Decisions to preserve during implementation

| ID | Decision | Consequence |
|---|---|---|
| D01 | Debug transitions, not CPU instructions. | Reuse native debuggers for reducer internals. |
| D02 | One command delivers at most one modeled input unless explicitly bounded. | No hidden queue draining or effect execution. |
| D03 | Keep observation separate from control. | A viewer cannot silently become a live mutator. |
| D04 | Keep exact state separate from its display projection. | Redaction and pagination do not change replay semantics. |
| D05 | Execute the reducer and advance each oracle once per delivered input. | Recording, inspection, and replay hooks cannot repeat application work. |
| D06 | Preserve existing exact-trace rules. | Post-failure continuation and incompatible-build experiments remain separate evidence. |
| D07 | Macros are optional adapters and metadata generators. | Handwritten models retain equivalent debugger access. |
| D08 | Keep the default engine dependency-free. | Transport and UI dependencies remain in companion packages. |
| D09 | Start with model-specific executables and a headless API. | No dynamic Rust model-loading ABI is required. |
| D10 | Treat live control as application integration, not a generic pause trick. | Safe points, queue policy, and effect dispatch must be explicit. |
| D11 | Shared observation vocabulary, independent evidence paths. | Sampled diagnostics cannot weaken exact recording or bias unsampled metrics. |
| D12 | Measure real effects at the host, not in replay. | Runtime clocks and lifecycle hooks are optional and nonsemantic. |
| D13 | Keep attempts, resolution, delivery, and settlement distinct. | Cancellation requests and incomplete telemetry cannot invent terminal outcomes. |
| D14 | Bound subscriptions, payloads, series, and exporters. | Loss, cardinality overflow, and unknown values remain explicit. |
| D15 | Logging and timing can operate without a debugger UI. | Headless host integration and metric-only operation are release capabilities. |

### Release boundary

The first useful release is complete when a developer can link an ordinary Rust model, choose one input, inspect its state and outputs, stop on a failing property, save the failure, replay it, and compare a corrected build. For the expanded first release, also qualify runtime-selective logging/local probes and host effect timing/metric snapshots (M3a and M3b). A graphical interface, remote live control, arbitrary state editing, and cross-language debugger implementations are not prerequisites.

<!-- pagebreak -->

# 1. Product scope and native debugger relationship

### 1.1 Problem to solve

A state-machine failure is often a sequence problem: a completion arrives after cancellation, a message targets the wrong generation, a resource is released twice, or an obligation remains outstanding. The proposed debugger should explain the modeled sequence that led to the defect and expose the exact transition at which behavior diverged from a property or recorded expectation.

The core interaction is: inspect a state, select an environmental input, execute one turn, inspect the resulting state and ordered outputs, and retain the evidence. An agent should be able to perform the same interaction through structured commands without reading terminal screenshots.

### 1.2 Complement existing tools

| Tool | Existing strength | Recommended use with Statelessness |
|---|---|---|
| LLDB / GDB | Source breakpoints, call stacks, variables, and watchpoints. [T1] | Step inside the selected reducer or checker. |
| CodeLLDB | Rust-oriented visualizers and Cargo integration; reverse execution requires a supporting backend. [T2] | Launch a model harness at a specific replay transition. |
| `tracing` | Structured events and spans. [T3] | Correlate external runtime activity with transition IDs. |
| Tokio Console | Instrumented asynchronous task and resource diagnostics. [T4] | Investigate executor behavior outside the modeled reducer. |
| Proposed debugger | State/input/output/property-oriented execution and evidence. | Find, reproduce, inspect, and minimize sequence failures. |

Do not claim that native tools cannot perform reverse debugging: CodeLLDB documents that capability with a compatible backend. The distinction is semantic control over modeled inputs, not exclusive ownership of time travel. [T2]

Use compiler-generated debug information for source debugging. `#[derive(Debug)]` provides formatting, not native debugger symbols. Cargo profiles control debug information and optimization; optimization can rearrange code and make source debugging harder. [T5, T6]

### 1.3 Explicit non-goals

The initial debugger will not suspend arbitrary Rust statements, inspect every heap object, undo real network or filesystem effects, replace an async runtime, automatically infer a correct environmental model, or prove an application correct outside its supplied properties and bounds.

It will not add runtime reflection as a mandatory requirement, require Serde on application types, or require a new state-machine declaration language. No UI framework becomes part of the core model contract. The observability companions are not a new async runtime, metrics backend, general-purpose profiler, or replacement for existing tracing/exporter ecosystems.

### 1.4 Success measure

The useful outcome is an inspectable, replayable regression case with an identified first failing or divergent transition. A visually attractive state graph is secondary. Report model bounds and evidence quality alongside the result; a finite passing run is not a universal correctness claim. [R1]

<!-- pagebreak -->

# 2. Reviewed baseline and integration gaps

The baseline already contains the central pieces needed for a debugger. This inventory describes source behavior, not a new qualification claim. [R1–R7]

| Existing component | What it provides | Debugger work needed |
|---|---|---|
| `Model` | Application-owned state/input/output types and deterministic `step`. | A persistent interactive session and richer observations. |
| `Transition` / `TransitionRef` | Next state, ordered outputs, and disposition. | Present these together with before-state and checks. |
| `check_observed*` | Check an already executed transition without rerunning it. | Reuse results rather than duplicate checks across consumers. |
| `ModelCodec` | Canonical state/input/output encoding; state/input decoding. | Optional display adapters and explicit historical-output decoding capability. |
| `execution::record` | Fully checked exact traces with bounded evidence. | Interactive capture using the same format and validation rules. |
| `replay_with_observer` | Per-attempt callback, including a divergent transition. | Typed actual observations and expected encoded values. |
| `monitor::Recorder` | Bounded runtime history with a replayable checkpoint. | Stream inspection; retain its independent freeze/error behavior. |
| `WithOracle` | Composite application state and independent oracle history. | Display and restore both; preserve exactly-once advancement. |
| `Enumerate`, `Generate`, `Auto` | Finite domains, causal validation, and generation. | Capability-based input selection without invented validity rules. |
| Modeling macros | Model adapters, value codecs, domains, property catalogs. | Optional structured inspection and source metadata. |
| Composition / lifecycle helpers | Queued messages, routed inputs, and monitored obligations. | Queue/obligation views without changing scheduling semantics. |
| Host effect timing / selective telemetry | No first-class implementation is asserted by this baseline inventory. | Proposed observation hooks, subscriptions, probes, lifecycle accounting, and metric aggregation in Sections 14–16. |

### 2.1 Specific constraints the implementation must respect

`Model` requires `Clone + Eq` for its associated value types; it does not require `Debug`, Serde, or persistence. New optional capabilities must not add mandatory bounds to existing models. [R2]

The current replay observer receives a sequence number and `ReplayReport`, rather than typed before-state, input, or next-state. It runs after attempted comparison, including the first divergent transition. Existing replay stops on that divergence. [R3]

`ModelCodec` does not require output decoding. A generic historical viewer therefore cannot promise typed output reconstruction merely because it can open a trace. It can show encoded bytes, use an optional decoder, or show actual outputs during re-execution with an explicit provenance label. [R2]

The recorder freezes on its first property failure or recording error; it does not stop application execution. It expects every delivered transition in order, including rejected and ignored inputs. [R4]

The replayer rejects a trace containing steps after a property failure. The stock CLI runs the bundled request fixture rather than dynamically loading arbitrary Rust models. Preserve both boundaries unless a separately reviewed format or loading design changes them. [R1, R3]

<!-- pagebreak -->

# 3. Operating modes and capability boundaries

Use separate mode identifiers, not one ambiguous “debug” mode. Each session advertises its supported capabilities and rejects unsupported commands.

| Capability | Trace viewing | Verified replay | Simulation | Live observation | Live control |
|---|---|---|---|---|---|
| Browse recorded state | Yes | Yes | Retained history | Retained history | Retained history |
| Execute a reducer | No | Recorded inputs | Selected inputs | No | Through host |
| Choose a new input | No | Fork first | Yes | No | Explicit host policy |
| Rewind execution | No | Restart replay | Restore/fork model | No | No by default |
| Run real effects | No | No | No | Already host-owned | Host-owned only |
| Stop live dispatch | No | No | Not applicable | No | Cooperative safe point |

Host telemetry is an orthogonal capability: logging, counters, and effect timings can run without an active debugger session or live-control permission. Trace viewing/replay can display imported timing data but must not report it as a fresh effect measurement.

“Retained history” is bounded. A mode may offer fewer capabilities when codecs, inspectors, input domains, or an application bridge are absent.

### 3.1 Trace viewing

Opening a file is inspection of stored evidence. It does not execute the reducer and does not establish that the current build reproduces the trace. Show the stored build identity, retained range, terminal reason, and whether values are exact, projected, redacted, or unavailable.

### 3.2 Verified replay

Restore the starting checkpoint, deliver recorded inputs in order, and compare the actual observations against the trace using existing compatibility checks. Stop at the first mismatch by default. Initial-state checking must also be visible: a failure or divergence can exist before input 1. [R3]

### 3.3 Interactive simulation

The session owns a modeled state and the selection of the next input. Outputs are data, not executed effects. Time advances only through modeled inputs or explicit state transitions. A real wall-clock delay while the developer reads a screen cannot advance model time. This follows the existing deterministic model contract. [R2]

### 3.4 Live observation and live control

Observation consumes the application's already executed transition stream. Control additionally requires the host to support safe-point pausing and explicitly authorized input delivery. They are independently enabled capabilities, even when they share a transport.

Pausing dispatch is not pausing workers or the outside world. The host must declare what happens to incoming completions, timers, and pending effect requests while paused. An observation-only bridge must never imply that those activities are frozen.

### 3.5 Session state and outcome

Separate operational phase from evidence outcome. Proposed phases are `Ready`, `Running`, `Paused`, `Ended`, and `Disconnected`; outcomes include property failure, checker error, replay divergence, budget exhaustion, cancellation, and incomplete recording. “Paused” is not synonymous with “passed.” An empty input domain is not automatically a deadlock or successful completion.

<!-- pagebreak -->

# 4. Atomic turn, observation, and error contract

### 4.1 The unit of execution

A turn delivers one addressed input to one modeled transition boundary. Child calls made synchronously inside that reducer remain part of the same turn. A composition helper may enqueue further messages, but the debugger must not deliver them automatically. A multi-step command must state its input policy and maximum work.

### 4.2 Required execution order

1. Check session phase, capabilities, expected revision, and command limits. Decode the candidate input and apply the selected environmental-validation policy.
2. Evaluate pre-delivery breakpoints. A stop here leaves the input undelivered and does not consume a transition sequence number.
3. Execute the actual reducer once. With `WithOracle`, advance each attached oracle once using its defined input/output/disposition contract.
4. Run the configured checks once, preserving order and distinguishing failures, skipped checks, and callback errors.
5. Make the actual result available to diagnostics. Prepare exact recording and display projections independently within their limits. Diagnostic subscriptions, sampling, and timing may not change the transition/check results; exact recording has its own failure path.
6. Commit the session's next modeled state and coherent evidence where possible. Publish the completed observation and stop reason. Do not execute outputs in simulation or replay.

Initial state and its checks are a distinct observation at sequence 0. Delivered transitions use positive sequence numbers. Rejected and ignored inputs still consume a sequence number because they are actual deliveries. [R2–R4]

### 4.3 No repeated work across integrations

When a single execution feeds checking, recording, and debugging, share the already computed transition and check batch. Do not call `check_observed` and then call `Recorder::observe` expecting that checks will run only once: the current recorder performs its own checks. Refactor toward an internal checked-observation path, or let the recorder supply the checked batch to downstream consumers. Do not expose an unchecked public “trusted checks” bypass without a separate contract. [R3, R4]

For runtime oracle observation, retain the application transition returned by an oracle-advancement error. Never retry the reducer to recover the diagnostic information. The existing wrapper documents that recovery requirement. [R1]

### 4.4 Failure policy

| Failure | Required behavior |
|---|---|
| Invalid command, stale revision, input decode failure | Reject before delivery; state and sequence remain unchanged. |
| Reducer error | Report a model error, not a property failure or success; no successful transition is recorded. |
| Property failure | Preserve the failing observation and stop normal exact execution. |
| Checker/oracle error after application execution | Retain the actual application result where available; mark verification incomplete and stop controlled execution. |
| Exact encoding or retention failure | Preserve the last coherent trace prefix and report the uncaptured transition/result separately. |
| Inspector or frontend failure | Report display/connection failure without rewriting model results or check status. |

Arbitrary callback panics cannot be assumed recoverable. Default controlled sessions terminate; a host that survives a diagnostic panic must explicitly define its boundary. No diagnostic failure can justify rolling back a live application that already executed a transition. Live telemetry is nonblocking by default; any host audit policy that gates new admission must act before work is admitted, not rewrite an already executed outcome (Section 14.6).

<!-- pagebreak -->

# 5. Proposed API and engine integration

### 5.1 Borrowed observations

Add a small public borrowed observation surface. The following is an API sketch, not existing or compiled code:

```rust
pub struct TurnObservation<'a, M: Model> {
    pub sequence: u64,
    pub before: &'a M::State,
    pub input: &'a M::Input,
    pub transition: TransitionRef<'a, M::State, M::Output>,
    pub checks: &'a [Check],
}

pub enum DebugObservation<'a, M: Model> {
    Initial {
        state: &'a M::State,
        checks: &'a [Check],
    },
    Turn(TurnObservation<'a, M>),
}
```

Use a separate replay context for expected encoded observations and comparison results; ordinary simulation should not manufacture expected values. Correlate diagnostic projections and probe batches through an observation identity without adding wall-clock values, subscriptions, or sink state to `Transition`, exact equality, or canonical encoding. Host effect events use a separate lifecycle interface and never manufacture model turns. Define explicit error events for attempts that never produced a complete checked observation.

A borrowed callback must consume values synchronously. A transport cannot retain references after it returns. Cross-thread consumers receive a bounded owned representation prepared by an adapter; this must not impose `Send`, `Sync`, or `'static` on every model.

### 5.2 Session responsibilities

A proposed `DebugSession<M>` owns the simulated model state, execution revision, selected input policy, check policy, breakpoints, budgets, and stop reason. A separate replay cursor owns the recorded expectations and verified-prefix position. A separate trace-view cursor browses snapshots without executing.

The session exposes typed operations first: inspect current state, submit one typed input, list candidates when supported, run a bounded sequence, stop, export, and fork. Persistence requires `ModelCodec`; plain stepping does not. Inspection, input parsing, candidate enumeration, and snapshot branching are independently advertised capabilities.

For checkpoint restore, use state decoding or application-supplied independent snapshots. `Clone` alone does not guarantee independence when a type contains shared mutable handles. Such state violates deterministic isolation unless its application semantics make sharing harmless; the debugger must not claim that cloning arbitrary state creates a safe branch. [R2]

### 5.3 Backward-compatible integration

Add a richer replay entry point and retain `replay_with_observer` as an adapter preserving its existing call timing, first-divergence behavior, and report semantics. Do not silently change its existing callback to a different meaning. [R3]

Extract internal helpers only where required to share transition/check/encoding preparation. Avoid a broad rewrite of fuzzing or enumeration as part of debugger delivery. Existing recording, search, shrinking, and replay tests remain regression gates.

The core observation types can remain dependency-free. Session logic should first be implemented in a companion crate through public seams. Promote functionality into the engine only when it eliminates semantic duplication or serves multiple existing execution paths. Proposed companion seams include a scoped `DiagnosticSink`, an `EffectObserver`, a bounded `MetricSink`/snapshot API, and a subscription registry. They must be usable independently of `DebugSession` and require no new bounds on existing application types.

<!-- pagebreak -->

# 6. Full-state inspection and display schemas

### 6.1 Three different representations

**Exact modeled state** includes all data relevant to future behavior and checks: application fields, pending messages, modeled effects, logical time, generation identifiers, and oracle history. Replay and equality use this representation. Existing composition and oracle helpers already retain relevant queues and histories in their modeled state. [R1, R6]

**Inspection state** is a read-only, bounded projection for people and tools. It may use summaries, pagination, redaction, or opaque resource descriptions. It is not an equality key, a replay codec, or a reachability abstraction.

**Runtime resource descriptions** identify live objects through host-supplied IDs, ownership, status, and relevant observations. Displaying a socket ID does not make the socket checkpointable. Restoring a simulated state must not be presented as restoring the process.

### 6.2 Optional inspection interface

Define an ordinary public `Inspect` trait or model-level inspector adapter. Support scalar, string, byte summary, enum, object, sequence, map, opaque, redacted, unavailable, and truncated nodes. Use a visitor or paged query design so inspecting a large collection does not require constructing an entire JSON tree.

Each query is bound to a snapshot/revision and a typed field path. Return stable field identifiers within the declared display-schema version, labels, node kind, available child counts, pagination information, and completeness status. Use structural path segments rather than ambiguous dotted strings internally.

Primitive values must retain type information. Preserve large integer precision in language-neutral transport, for example through tagged decimal strings; do not silently convert Rust `u64` or `u128` values into JavaScript floating-point numbers. Map display ordering must be deterministic or explicitly reported as unstable.

### 6.3 Diff and watch semantics

Return `Added`, `Removed`, `Changed`, `Unchanged`, or `Unknown` at inspected paths. A redacted, truncated, or unavailable node cannot imply equality. A collection index is not an entity identity: support application-supplied keys before claiming a record moved rather than changed.

Show snapshot identity and completeness with each diff. Logging watches reuse this inspection contract, with explicit baseline, change, and unknown semantics (Section 14.3). Scope arbitrary field selection to paths the inspector exposes; hidden locals require a probe. Whole-state equality and exact replay comparison remain separate from the display diff. Never prune exploration because two display projections look identical.

### 6.4 Formatting and historical values

Provide `Debug` formatting as an explicit convenience fallback, not an automatically parsed schema or safe redaction mechanism. Derived and standard-library `Debug` output is not a stable format. [T6]

Historical outputs require an optional decoder/display adapter because the current core codec only requires output encoding. In its absence, show bounded encoded bytes or an explicit unavailable label. Actual outputs reconstructed during replay must be labeled as replay-produced, particularly at a divergent step. [R2]

Redaction applies before values enter UI or transport buffers. Exact trace bytes may still contain secrets; a redacted display never establishes that the raw trace is safe to share.

<!-- pagebreak -->

# 7. Input construction, environment, and scheduling

### 7.1 Input admission is not application acceptance

Represent three separate decisions: structural decoding succeeded; the input is permitted by the declared environment; the application accepted, rejected, or ignored the delivered input. A legitimate stale completion may be permitted by the environment and still correctly ignored by the application.

The existing `Enumerate` contract supplies a complete finite set of permitted next inputs in stable order. `Generate::is_enabled` supports causal validation but defaults to `true`; that default is not proof of comprehensive environmental validation. Expose which mechanism was actually used. [R2]

### 7.2 Capability-based construction

Begin with typed Rust input submission and exact codec decoding. Add optional text/JSON parsing or schema-based forms in a companion layer. Do not require every input type to derive Serde.

When enumeration exists, expose candidate listing and selection. Paginate for display without falsely reporting that the visible prefix is the full domain. A truncated list must carry a continuation or an explicit incomplete result. Infinite or prohibitively large domains need application-owned generators or parameterized input builders, not exhaustive UI expansion.

Bind candidate tokens to session identity, execution revision, domain identity, and the selected encoded value or stable local token. Revalidate immediately before delivery. Selecting item 3 from an earlier queue view must not deliver whatever now happens to occupy index 3.

### 7.3 Time and pending outcomes

Logical time changes only through the model's own input vocabulary. A generic “advance time” control is available only through a model-provided adapter that constructs valid timer/tick inputs and handles all consequences required by that model. Do not invent timer semantics from numeric fields.

Pending completions are inspectable data. Deliver one chosen completion or message per turn. A “run until idle” operation requires an explicit deterministic selection policy, input count limit, and stop contract; it is not a hidden scheduler.

### 7.4 Fault injection

Delays, duplicates, reordered messages, worker failures, and cancellations are valuable when the environment model explicitly admits them. Strict mode is the default and uses declared environmental validity. A separate unrestricted injection mode may deliver structurally valid inputs outside those assumptions, but its branches and exports must be labeled accordingly.

Do not silently expand a model's input bounds to satisfy a debugger request. Record the environmental assumptions and configuration with the session, branch, and generated regression. A counterexample obtained under a modified environment is a counterexample to that modified model.

### 7.5 Multiple machines

The present composition helper uses explicit local inputs and queued-message delivery, without auto-draining. The debugger should expose those choices rather than install its own schedule. A larger system can supply its own addressed-input adapter. Machine labels are display metadata unless already part of exact model identity. [R6]

<!-- pagebreak -->

# 8. Replay, history, branches, and provenance

### 8.1 Distinguish browsing from verification

Maintain separate positions: the snapshot currently displayed, the last transition verified against a trace, and the execution head of any active simulation branch. Moving a viewing cursor backward does not rewind a live application or verify historical data.

The current trace stores the starting checkpoint and post-state bytes for each step. A viewer can use these for bounded snapshot navigation, subject to file validity and a compatible decoder. That is different from re-executing the path and comparing observations. [R3, R4]

### 8.2 Exact replay

Preserve model, property, codec, and build compatibility checks. An explicitly allowed build mismatch permits comparison, not a silent claim of same-build replay. Report exactness and property-failure reproduction separately: they answer different questions in the existing API. [R3]

At divergence, show the expected and actual disposition, outputs, state, and checks, including every available difference. Preserve the existing primary mismatch order for compatibility, even if the UI displays additional differences. Never label a divergent suffix as verified.

### 8.3 Branch creation

A normal branch starts from a retained checkpoint or a chosen pre-failure state. Record parent trace/session identity, fork sequence, checkpoint identity, model/build/property/codec identity, environment policy, and whether the parent prefix was verified. Keep the parent immutable.

A new build must re-execute from the original checkpoint to establish its own valid prefix before branching from a later state. Loading an old build's intermediate state directly into a new build can be a useful counterfactual experiment, but it is not proof that the new build reaches that state.

Require a coherent composite snapshot: application state alone is insufficient when oracle history or pending messages affect future checks. Never reconstruct oracle history by guessing from application fields. [R1, R6]

### 8.4 Continuation after failure

The existing exact replayer rejects steps after the first property failure. Keep the original failing `.sttrace` intact. Initial releases should stop at failure and offer branching from a preceding state. [R3]

A later “continue despite failure” capability must create a distinct exploratory session or artifact with explicit post-failure status. It must not append steps to an ordinary exact trace, clear the original failure, or advertise a passing verification run.

### 8.5 Artifact policy

Use existing `.sttrace` files for exact evidence and a versioned optional manifest/sidecar for debugger annotations, display schema identity, source mappings, branch relationships, and UI bookmarks. Bind sidecars to the exact artifact content and reject mismatches. A digest detects accidental mismatch; it is not an authenticity guarantee. Supplementary telemetry sidecars may contain runtime durations, span links, metric snapshots, capture policy, producer epochs, gaps, and debugger-pause annotations. They are not replay inputs unless a separate explicit model codec defines them as such. Viewing or importing them must never increment live metrics. Redaction, retention, and access policy apply independently to exact and diagnostic artifacts.

Minimizing a mid-run branch must start from its saved composite checkpoint, not an unrelated default `initial_state()`. Use an explicit checkpoint-root adapter and validate every candidate against the same environmental policy.

Retain origin classifications: recorded live observation, exact replay, simulated input sequence, out-of-domain injection, counterfactual restore, and edited-state experiment. Arbitrary state editing is deferred; it must never inherit the label “reachable” merely because state checks pass.

<!-- pagebreak -->

# 9. Semantic breakpoints and macro design

### 9.1 Breakpoints without macros

Support pre-delivery predicates over state and candidate input; post-turn predicates over state changes, outputs, and disposition; and property-failure breakpoints. Typed Rust predicates are the first implementation. A restricted structured predicate language can later support remote clients without arbitrary code evaluation.

Pre-delivery stops do not consume the input. Post-turn stops observe a completed atomic transition. “Step” bypasses the breakpoint that just stopped the same pending delivery once; “continue” must not repeatedly stop at the identical boundary without progress. Define these semantics in tests.

A field watch evaluates only at transition boundaries. It can miss a value that changes and changes back inside one reducer, unlike a native memory watchpoint. Field-watch results must respect redaction and unknown/truncated values.

### 9.2 Macro additions worth implementing

| Proposed addition | Purpose | Boundary |
|---|---|---|
| Inspection derive | Generate ordinary field/variant traversal and display metadata. | Does not define exact codec or equality. |
| Model/property source metadata | Link a handler or property to source location and stable ID. | Does not claim to identify the precise internal faulty line. |
| Input display metadata | Labels and optional construction information. | Does not infer permitted environmental payloads. |
| Diagnostic marker helper | Name a visited path or classified outcome. | Does not suspend a reducer or become a semantic output. |
| Lazy local `probe!` helper | Observe an explicitly instrumented function-local value. | Does not inspect arbitrary memory; closure skipped when no consumer needs it. |

Keep generated APIs available through handwritten implementations. The existing companion macros already generate adapters and codecs without becoming a default core dependency; extend that separation. [R5, R6]

### 9.3 Marker execution policy

M3 classifies markers after a completed turn using before/input/after/output data. This avoids altering reducers and does not promise internal branch coverage. M3a adds explicit internal probes/markers through the scoped sink defined in Section 14.4.

True in-reducer markers and local probes are opt-in M3a capabilities, not mandatory model instrumentation. An application can pass a local diagnostic collector into an ordinary shared reducer helper. Normal and diagnostic adapters must invoke that same helper exactly once, using a disabled or active collector. Do not maintain a second reducer, rerun the reducer to collect markers, or use hidden global/thread-local diagnostic state as a semantic dependency.

When disabled, marker formatting and payload evaluation should not occur. Marker arguments must not contain behavior required by the application. Instrumentation-enabled and disabled builds require differential semantic tests; source/build provenance may still differ. These tests hold supplied inputs fixed; live execution timing and scheduling are not guaranteed identical.

### 9.4 No resumable reducer transformation

Do not transform arbitrary function bodies into continuations, hold a reducer midway while delivering another input, or inject blocking pauses while application locks are held. Such behavior changes the transition boundary and would require a different execution model.

Procedural macros operate on token streams and can generate adapter code; they do not infer application intent, semantic independence, or complete runtime state. Keep their promises limited to supported syntax and generated behavior. [T7]

<!-- pagebreak -->

# 10. Package structure and integration architecture

### 10.1 Proposed package layout

```text
src/
  execution.rs             shared checked execution / rich replay seam
  observation.rs           borrowed observation types, if core-worthy

crates/
  statelessness-debug/      session, cursor, breakpoints, inspection
  statelessness-observe/    scoped probes, subscriptions, effect timing
  statelessness-metrics/    bounded aggregates / optional ecosystem bridge
  statelessness-debug-cli/  headless / textual client and harness helpers
  statelessness-debug-wire/ optional protocol, owned views, transport
  statelessness-macros/     existing macro companion; extend later

examples/
  debug_request.rs         linked request-lifecycle harness
  debug_composition.rs     controlled queued-message delivery
  observe_effects.rs        host timing / retries / log and metric modes

docs/
  DEBUGGER.md              user and integration guide
  DEBUGGER-PROTOCOL.md     capability and message contract
  OBSERVABILITY.md         logging / probes / effect lifecycle
  METRICS.md               instrument catalog / windows / bounds
```

These are proposed paths. Avoid creating empty companion packages merely to match the diagram: begin with the debug library, an observation companion module, and linked examples; split crates when implementation or optional dependency boundaries warrant it. Never force timing/metrics users to link a TUI or debugger controller.

### 10.2 Dependency direction

The debugger depends on core model/observation contracts. The engine must not depend on a CLI, terminal renderer, WebSocket server, editor protocol, or JSON library. Optional dependencies belong in separate manifests so they do not compromise default cold-offline engine resolution. The existing macro packaging rationale provides the precedent. [R6]

Inspectors and breakpoint predicates execute on the model-owning thread. A bridge sends owned bounded values outward. The model itself remains free of debugger-imposed thread-safety bounds.

### 10.3 Link application models explicitly

The first application integration is a small Rust executable linking the application's model and debug companion. It can run in-process commands or serve a controlled stdio protocol. This avoids an unstable dynamic Rust plugin boundary and matches the current CLI's explicit model limitation. [R1]

Do not suggest that opening a `.sttrace` automatically loads the reducer that produced it. A generic viewer can inspect format metadata and encoded evidence, but verification requires the compatible model implementation.

### 10.4 Frontends and optional ecosystem integration

Begin with a textual CLI and structured machine interface. A Ratatui TUI is a reasonable optional frontend choice, not part of the core contract. A web or editor frontend should consume the same bounded views and command semantics.

Add optional `tracing` event/span and `metrics` adapters over the shared observations, with OpenTelemetry integration at the host boundary. Host timing and bounded metric snapshots remain useful without a tracing subscriber. Record transition/effect/correlation IDs in diagnostic detail, not unbounded metric labels; none of these streams is a lossless exact model trace. The host selects global ecosystem configuration. [T3, T9]

An editor adapter can later use the Debug Adapter Protocol, whose purpose is to separate debugger implementations from development-tool interfaces. Only advertise capabilities with honest transition-level meanings; source stepping remains a native-debugger function. [T8]

<!-- pagebreak -->

# 11. Commands, frontend model, and agent protocol

### 11.1 Headless operations

| Command family | Proposed behavior |
|---|---|
| `status`, `capabilities` | Mode, revision, execution/view positions, check policy, limits, and stop reason. |
| `inspect`, `diff`, `checks`, `outputs` | Bounded read-only queries tied to a specific snapshot. |
| `inputs`, `select`, `inject` | Discover or submit inputs under an explicit admission policy. |
| `step`, `continue`, `pause` | One turn or explicitly bounded progress; cooperative pause where supported. |
| `breakpoint`, `watch` | Create and manage typed or restricted semantic conditions. |
| `open`, `seek`, `verify`, `fork` | Distinct history, replay, and branching operations. |
| `export`, `minimize`, `compare` | Persist evidence, shrink a compatible failure, or compare model builds. |
| `observe`, `subscribe`, `unsubscribe` | Revisioned selectors, triggers, projections, and sink routing. |
| `effect_details`, `effect_timeline` | Actual host lifecycle/elapsed data with provenance and completeness. |
| `metric_catalog`, `metric_snapshot`, `metric_window` | Bounded aggregate queries with population, epoch, interval, and schema. |
| `telemetry_health` | Limits, queue pressure, loss, invalid measurements, and exporter status. |

All names are proposed. Every operation must have a typed library equivalent where appropriate. A UI must not implement its own reducer, scheduler, or replay comparison rules.

### 11.2 Protocol contract

Use a versioned request/response/event envelope. A mutating request includes protocol version, session identity, request ID, expected execution revision, command, arguments, and budgets. Responses include result/error category and the current revision. Events use a separate monotonically increasing stream sequence; reconnects and branches also carry an epoch/session identity.

Make command handling serialized per controlled session. Reject stale mutating requests rather than applying them to a new state. Subscription updates use their own expected capture/configuration revision; changing diagnostics is not permission to step or mutate the model. Snapshot-bound watch installation also identifies its baseline execution revision. Duplicate request IDs are deduplicated within a bounded documented window; after the window expires, reject uncertain re-delivery or require status reconciliation. Do not promise network-wide exactly-once delivery.

Bind view handles and pagination cursors to immutable snapshots or acknowledged revisions. Return stale-handle errors rather than mixing values from different transitions in a single apparent state tree.

### 11.3 Safe evaluation and transport

Start with typed local predicates and a small allowlisted remote condition representation: field access, typed comparisons, boolean operators, input/output variant tests, and property IDs. No arbitrary Rust expressions, shell execution, filesystem access, or uploaded code evaluation through debugger commands.

Use stdio first for a launched model harness. Network transport is optional and must add authentication, authorization, origin protection where relevant, and explicit local/remote exposure policy. Transport choice must not weaken the mode/capability boundary.

### 11.4 Agent-facing evidence

Provide structured first-failure and first-divergence reports containing the exact property ID, transition sequence, input, disposition, ordered outputs, observed field changes, source metadata when available, and artifact provenance. Correlation explanations must reference application-supplied IDs or explicit causal links, not guesses from field names.

Agents can export a regression or request bounded minimization. Report the shrinking algorithm, budgets, preserved failure identity, and whether a candidate remains within the environmental domain. A smaller observed reproducer is not necessarily globally minimal. Timing reports must distinguish queue wait, elapsed execution, delivery, and application settlement and link to actual host boundaries. A causally linked slow effect is a diagnostic observation, not automatic proof of the code-level cause of slowness.

<!-- pagebreak -->

# 12. Live application integration and security

### 12.1 Observation integration

Attach at an application-owned transition boundary with the actual before-state, delivered input, result, and verification history. Mid-run attachment requires a coherent checkpoint; an arbitrary collection of independently read fields is not a snapshot.

The application supplies all transitions in order. The current recorder can detect some discontinuities but cannot detect omitted transitions that return to the same state. Add host sequence IDs and explicit gap reporting rather than treating continuity checks as a complete delivery guarantee. [R4]

Keep recorded modeled outputs distinct from runtime facts such as “effect dispatched” and “effect completed.” A requested effect is not proof that the host performed it. Runtime facts can be optional correlated telemetry or model inputs when relevant to semantics. The effect adapter in Section 15 works independently of model-state streaming: host timings do not require live control. Keep requested cancellation, confirmed termination, result delivery, and application settlement distinct.

### 12.2 Cooperative pause and step

Use a host adapter with an explicit state machine: pause requested, safe point reached, pause acknowledged, one delivery authorized, delivery observed, paused again, or resumed. The acknowledgment identifies the execution revision and whether effects from the last completed turn have already been dispatched. Capture host pause intervals for timing provenance; exclude affected measurements from ordinary production-latency views by default rather than blindly subtracting pause duration.

Choose a documented safe point, normally between complete application turns. A separate pre-effect-dispatch pause is available only when the host explicitly stages outputs and owns dispatch bookkeeping. Never dispatch an output again merely because the debugger reconnects or resumes.

The host must specify what happens to external arrivals while paused: bounded buffering, host-supported backpressure, or an explicit degraded/disconnected policy. Queue overflow cannot silently discard evidence and retain an “exact history” label. For a paused controlled session, detach behavior must be configured by the host: remain paused, resume, or terminate the debug harness.

### 12.3 Side-effect authority

Default to observation-only. Treat input injection, dispatch control, and checkpoint mutation as separate capabilities. Live arbitrary state replacement is out of scope for the initial implementation. Replay and simulation never automatically invoke effect handlers.

Debug commands are application-control authority. Do not enable a network listener or injection endpoint merely because code was compiled with debug symbols. Bind remote control to an explicit runtime opt-in and authorization decision.

### 12.4 Data and artifact safety

Trace files and snapshots may contain tokens, paths, user content, or other secrets. Inspection redaction must precede transport, but raw replay artifacts remain independently sensitive. Mark shareable projected reports as non-exact when they omit replay-relevant data. Logging subscriptions and metric exemplars require the same privacy review; do not export credentials through error strings, raw argument capture, or trace baggage. Consumer permissions bound which fields may be selected at runtime.

Treat imported traces, sidecars, and protocol inputs as untrusted data: enforce frame, count, depth, decoding, and allocation limits; escape terminal control sequences and UI markup; restrict export paths; and never execute a model or helper solely because an artifact names it. Model execution requires an explicitly chosen local harness.

<!-- pagebreak -->

# 13. Debugger resource limits and overhead

### 13.1 Resource policy

Bound retained exact evidence, displayed nodes, strings/bytes per node, collection pages, markers per turn, outstanding requests, transport queues, branches, breakpoints, and multi-step work. Expose the active limits in session status and exports.

Keep the distinction between serialized evidence size and process memory. Existing recorder limits count retained encoded evidence, not all allocations inside application callbacks or legacy codecs. The debugger must not turn those limits into a claim of a hard process-memory cap. [R2, R4]

### 13.2 Proposed initial debugger defaults

The following are design defaults to qualify and revise, not measured performance claims or replacements for existing codec/trace limits.

| Debugger-owned resource | Initial default | Overflow behavior |
|---|---|---|
| Children per inspection page | 100 | Return continuation and completeness metadata. |
| Depth per inspection query | 32 | Return truncated nodes explicitly. |
| Display string/byte preview | 4 KiB per value | Preserve type and report omitted length where known. |
| Diagnostic markers | 256 per turn | Mark diagnostic loss; never alter semantic outputs. |
| Pending protocol requests | 64 per connection | Reject/backpressure before admission. |
| Bounded `continue` | 1,000 turns unless overridden | Stop with budget outcome, not “completed.” |
| Exact history | Reuse configured recorder limits | Preserve coherent checkpoint/prefix or report failure. |

Collection and marker payloads also need aggregate byte budgets. Counts alone do not bound memory. Section 16.6 adds independent subscription, sink, watch-cache, per-effect detail, metric-series, and metric-history budgets; those limits are separate from exact trace retention.

### 13.3 Performance strategy

No inspector, marker, or probe payload formatting runs when its feature has no interested consumer. Runtime-disabled interest checks still have a cost. Clock reads are omitted only when neither timing metrics nor selected diagnostics need them. Reuse scratch buffers and borrow transition data; materialize owned transport views only on demand. Avoid whole-state serialization solely to refresh a selected scalar field. Exact recording still pays its required codec and checking costs.

Run benchmarks separately for engine-only execution, checking, existing recording, debugger attached but idle, scalar inspection, large collection pagination, and sustained transport. Measure throughput, allocations, retained bytes, command latency, and tail latency with small and large state models. Add counters-only, timing-only, targeted local probes, unsampled metric/sampled log combinations, and saturated exporters from Sections 14–16.

Do not set or advertise a universal percentage-overhead guarantee before measurement. The release report should state compiler/profile, hardware, model, state sizes, enabled checks, and recorder settings.

### 13.4 Non-preemptible application work

Cooperative limits cannot interrupt a blocking reducer, checker, codec, or iterator. Isolate untrusted or potentially nonterminating models in a process when hard cancellation is required. Terminating that process is not a clean model-level transition or evidence of a particular property failure.

<!-- pagebreak -->

# 14. First-class logging and observability

### 14.1 One observation vocabulary; separate delivery guarantees

Make logging, debugger inspection, timed traces, and metrics consumers of the same typed observations and causal identities. Do not route every consumer through one lossy queue. The exact recorder consumes authoritative checked transitions directly; metric aggregation consumes admitted lifecycle measurements before diagnostic sampling. Logs, timed spans, and frontend updates may then have separate filters, queues, and exporters.

| Evidence plane | Purpose | Contract |
|---|---|---|
| Exact model evidence: `.sttrace` | Reproduce states, inputs, outputs, checks, and failures. | Canonical, compatibility-checked, bounded coherent prefix; unchanged format semantics. |
| Structured logs and probes | Explain a particular change, branch, value, or error. | Selective and possibly lossy; disclose filtering, gaps, truncation, and provenance. |
| Performance traces: spans | Follow actual operation lifetimes and causal relationships. | Host-measured, possibly sampled or incomplete; not a model replay trace. |
| Aggregated metrics | Count activity and describe distributions and operational health. | Bounded aggregates over a declared population and interval; not a causal history. |

```text
Checked model observation ──> exact recorder (independent)
             │
             └──> selected state watches / diagnostic projection
Host effect boundaries ──> lifecycle accounting / measurements
                                      ├──> aggregate metrics
                                      └──> log / span / UI filtering
Explicit scoped probes ────────────────> log / UI filtering
```

The arrows represent data flow, not a required serialization or queue. Lightweight metrics do not require a JSON event, a full-state snapshot, or an enabled debugger. Turning off console logs must not turn off separately enabled counters or timing. Turning off telemetry must not stop independently configured exact recording. These separations are implementation requirements, not claims that the existing engine already exposes this pipeline.

### 14.2 Structured envelope, provenance, and ordering

Use a versioned envelope with an owned, bounded payload for asynchronous consumers. Proposed fields are `schema_version`, `run_id`, `producer_id`, `producer_epoch`, `producer_sequence`, `origin_kind`, `event_kind`, `severity`, `capture_revision`, `completeness`, and optional model, machine, transition, output-index, effect, attempt, delivery, and trace/span identities. Include `display_schema_version` when a payload contains field paths. Omit inapplicable fields rather than inventing values.

Keep correctness outcome, runtime outcome, model disposition, and telemetry completeness separate. A property violation is not automatically a failed network operation; an ignored completion may be correct application behavior. A sampled span can refer to an exactly recorded transition without becoming exact evidence itself.

| Producer | Example events | Meaning |
|---|---|---|
| Checked transition path | `turn.observed`, `property.failed`, `input.ignored` | Actual delivered input, completed transition, and shared check results. |
| Inspector / scoped instrumentation | `state.changed`, `probe.value`, `marker.hit` | Selected diagnostic projection or explicitly reached probe site. |
| Host effect adapter | `effect.requested`, `attempt.started`, `effect.resolved`, `completion.delivery_observed` | Real runtime boundaries defined in Section 15; never inferred from an output variant alone. |
| Pipeline health | `diagnostic.gap`, `capture.truncated`, `export.failed`, `measurement.invalid` | Loss or degraded observability, not a changed application outcome. |

Ordering is local to a producer epoch. A merged frontend may add its own receive sequence, but cannot manufacture a global causal order from timestamps. Use explicit parent/link identities for cross-task and cross-process relationships. OpenTelemetry context propagation provides an established mechanism for passing trace context; it does not automatically assign Statelessness effect or model identities. [T12]

### 14.3 Runtime-selective subscriptions

Support selecting observations by model, machine, entity/correlation key, input/output variant, property, effect kind, severity, field path, probe ID, or declared source site. Arbitrary state variables means fields exposed by the optional inspector; it does not mean arbitrary memory, uninstrumented locals, or an unrestricted expression evaluator. Function-local values require an explicit probe site.

A subscription includes selector, trigger, projection, redaction policy, optional sampling/throttling, destination sinks, limits, and revision. Initial triggers should include every observation, value comparison, change, threshold crossing, and property failure. Define hysteresis/cooldown for repeated threshold alerts. Conditions are typed, bounded, and diagnostic-only; remote conditions use the allowlisted evaluator from Section 11.

Proposed command examples, not an implemented CLI:

```text
observe state playback.pending_jobs --on-change --sink console
observe probe decoder.queue_depth --above 64 --sink jsonl
observe effects FetchMetadata --timings --slow-above 250ms
observe property no_publication_after_cancel --on-failure
subscribe events --machine playback --max-buffer 1024
metrics query effect_end_to_end_seconds --window 5m
```

A change trigger compares complete values at each selected transition boundary, before delivery sampling. An after-gap or initial attachment is `baseline`/`unknown`, not an invented change. If evaluation itself is sampled, label the result `change_since_last_sample`; never claim it detected every change. Redacted or truncated values cannot establish equality. A display hash is not exact equality unless equality is independently confirmed.

A configuration update returns `capture_revision` and its effective producer boundary. For a live multi-producer system, acknowledge each relevant producer; a controller acknowledgement alone is not proof every worker applied the update. Bound outstanding updates and return partial/pending status when a producer has not acknowledged. Each event identifies the revision that captured it. Disabling a subscription stops future capture at that boundary; it does not retract buffered data or erase another sink's copy.

Apply cheap static selectors before reading selected fields. Evaluate each required value at most once per observation and share only authorized projections. Bound baseline storage and subscription cardinality; eviction resets the affected comparison to unknown. Runtime field watches remain transition-boundary observations, not native memory watchpoints.

### 14.4 Explicit, lazy probes and markers

Add ordinary diagnostic-sink methods first, then optional `probe!` and `marker!` helpers. Existing handwritten models keep their observer-only path. A probe names a supported location and records a local value that may not exist in the model state; a marker records that a named path was reached.

```rust,ignore
// Proposed call shapes; not existing or compiled APIs.
probe!(diag, "decoder.queue_depth", || queue.len());
marker!(diag, "decoder.output.reordered");
```

`diag` is a scoped, explicit capability supplied by the application to a shared reducer helper. The normal and instrumented adapters call that same helper once with a disabled or active sink. Do not add a mandatory context parameter to `Model::step`, maintain two reducers, or rerun a reducer to capture a probe. Diagnostic context must not become mutable semantic state or a hidden global source of model behavior.

When no enabled consumer needs the value, skip the closure, formatting, copying, and serialization. For an enabled value-dependent trigger, the selected value must be evaluated even when the condition ultimately suppresses emission; document that cost. Probe expressions must be side-effect-free and not needed for application correctness. A Rust macro cannot prove arbitrary user closures satisfy that rule. Preserve application ownership by borrowing or explicitly bounded copying; never move a value solely to log it.

Capture internal markers and probes into a bounded per-turn collector; publish after the turn or mark the batch incomplete on a reducer/checker error. They cannot suspend a reducer, recursively acquire application locks, or trigger model/effect execution. Instrumentation faults are diagnostics, not property failures. Compile-time disabled sites must still be covered by enabled-build compilation and semantic parity tests.

### 14.5 Sinks, routing, and privacy

Provide console/text, structured JSONL file, debugger stream, and custom sinks. Optional adapters may emit `tracing` events/spans, `metrics` instruments, or OpenTelemetry data. The host chooses and initializes its ecosystem subscribers/recorders; an optional legacy `log` bridge preserves severity/fields it can represent but does not create span context or exact evidence; the library must not install a global subscriber or recorder on its behalf. `metrics` is a facade whose recorder/exporter determines storage and output behavior, not a guarantee of bounded histogram storage. [T3, T9]

Preserve structured event kinds and typed fields until the sink formats them. Do not parse formatted log strings to recover state, correlate effects, or calculate primary latency metrics. Per-sink filters select authorized projections. The least-privileged consumer must never receive a raw payload that another sink is allowed to see. Redact before values enter shared outbound queues or disk buffers; apply redaction again after any sink-specific enrichment.

State, input, output, error strings, source paths, and baggage/context may contain secrets. Default to allowlisted metadata and bounded summaries, not automatic payload dumping. Explicit trace-context propagation should exclude sensitive baggage and respect trust boundaries. `#[tracing::instrument]` records arguments by default, so integration examples must use `skip_all` or explicit skips and approved fields rather than implicitly capturing credentials or payloads. [T12, T16]

### 14.6 Backpressure, loss, and failure isolation

Give each asynchronous sink independent record and byte budgets. The normal live default is nonblocking `drop_newest`, with optional `drop_oldest` or sampling. Loss affects that sink, not other consumers, lifecycle accounting, or exact recording. Reserve separate health counters/status outside the saturated queue so drop reports cannot be lost indefinitely in the same queue they describe.

A host-owned audit mode may reserve bounded capacity before it admits new work and explicitly backpressure or reject admission. Do not call a finite queue unconditionally lossless: process failure, exhausted storage, and unavailable consumers still require a failure policy. Never block arbitrary I/O under a model/effect lock or inside a probe. Audit failure may affect host admission by explicit policy; it never retroactively rolls back an executed reducer or reruns an effect.

Track dropped, truncated, throttled, filtered, exporter-failed, and unpaired lifecycle data separately. Exact filter/sampling counts may require enabling additional accounting; when not collected, show unavailable rather than zero. Exported event IDs support bounded deduplication, not a promise of end-to-end exactly-once transport. Shutdown drains have a timeout and report abandoned buffers. Observer callbacks are non-reentrant; suppress or separately count telemetry generated by exporters to prevent recursive logging loops.

### 14.7 Capture modes and disabled cost

Expose independent controls for exact recording, metrics, lifecycle timing, logs/spans, field watches, and local probes. Offer explicit configurations such as compiled-out, runtime-disabled, counters-only, timings-without-per-effect-logs, and targeted diagnostic capture. Metric timing stays active when timing metrics are enabled, even if every log sink is disabled.

Compiled-out instrumentation should introduce no diagnostic evaluation path. Runtime-disabled instrumentation has an interest-check cost and must avoid payload evaluation, formatting, state cloning, and unnecessary clock reads. Do not promise universal zero overhead. Enabled instrumentation can alter live scheduling and latency; the semantic equivalence requirement applies to controlled runs with the same supplied inputs, not identical real-world schedules. Benchmark every supported configuration and include export saturation, many subscriptions, and large watched values.

<!-- pagebreak -->

# 15. Effect performance timing and lifecycle

### 15.1 Measurement belongs to the host

Instrument the real application dispatcher, executor, and completion-delivery boundary. The reducer declares an output; that declaration does not prove a task was queued, started, finished, or accepted by the application. Statelessness supplies optional lifecycle accounting, correlation, and measurement hooks without becoming the effect runner.

Keep three separate outcomes: **execution resolution** (the runtime has a result), **completion delivery** (a result input reaches the reducer), and **application settlement** (the application emits an explicitly classified acknowledgement/settlement). A rejected or ignored input is still a delivery. Success in one stage does not imply success in the next. Some fire-and-forget effects have no completion input at all.

Actual measured durations are supplementary telemetry. Viewing a recorded timing sidecar does not remeasure the effect. Replay and simulation do not execute effects; timing their reducer/checker harness is a separately labeled benchmark, not a new production effect measurement.

### 15.2 Identity and explicit lifecycle states

A request origin is `(run, epoch, machine, transition_sequence, output_index)`. The zero-based output index distinguishes identical outputs from one turn. Allocate a host effect-instance token at request observation, before admission, so rejected and never-dispatched requests remain identifiable. Attach application operation/generation keys separately. An origin is not an idempotency key unless the application explicitly defines it as one.

Each actual retry gets a new attempt identity under the same effect instance. Each completion publication/delivery attempt gets its own delivery identity, allowing duplicate delivery to be counted without pretending a second effect executed. Preserve the same accounting token across tasks; do not reconstruct identity by comparing effect payloads. Context propagation is optional transport assistance, not application identity inference. [T12]

| Boundary / state | Host observation | Required distinction |
|---|---|---|
| Requested | Outputs are observed at the host boundary. | May still be rejected or cancelled before admission. |
| Admitted / ready queued | Host accepts work and it becomes eligible for execution. | Backoff or scheduled eligibility is not necessarily ready-queue wait. |
| Attempt started | Worker begins the operation; for async work, at execution/first poll rather than future construction. | Submission to a pool does not mean execution has begun. |
| Attempt finished | That attempt returns a classified result. | A failed attempt can be retried; it is not necessarily a failed effect. |
| Effect resolved | Host fixes the logical operation result. | Hedged losers may still run; physical work and logical result are separate. |
| Completion posted | Result becomes visible in the destination queue. | Work before posting is delivery preparation, not queue lag. |
| Delivery begun / observed | Host begins one input delivery, then observes its transition result. | Beginning delivery is not successful application handling. |
| Settled, when modeled | Application-classified acknowledgement/settlement is observed. | Never infer settlement from reducer acceptance alone. |

Lifecycle events are facts, not mandatory transitions through every row. A synchronous effect may have no queue; a known same-boundary interval can be zero, while an unobserved boundary is unknown. Reject invalid lifecycle updates with diagnostic status; never repair them by inventing events or changing the actual runtime result.

### 15.3 Timing definitions

For a simple single-attempt, single-process path, use these local monotonic instants: `r` request observation, `q` ready-queue admission, `s` attempt start, `f` attempt finish, `z` effect resolution, `p` completion posting, `d` delivery start, and `a` delivery observation after the reducer. They are telemetry-only, not canonical model fields.

| Measurement | Definition | Interpretation |
|---|---|---|
| Admission delay | `q - r` | Host handoff/admission before a ready-queue entry exists. |
| Queue wait | `s - q` | Time eligible work waits for its worker; per attempt. |
| Attempt elapsed | `f - s` | Elapsed attempt time, including async waits, not CPU time. |
| Resolution overhead | `z - f` | Result selection/processing after the last relevant attempt, where that relationship is defined. |
| Effect resolution latency | `z - r` | Request observation until logical runtime result. |
| Completion preparation | `p - z` | Encoding, transport handoff, or other observed pre-post work. |
| Completion queue wait | `d - p` | Posted result waiting to be delivered. |
| Delivery lag | `d - z` | Total resolution-to-delivery delay, not just the queue. |
| Completion processing | `a - d` | Delivery's synchronous reducer interval, with exact instrumentation boundary declared. |
| End-to-end delivery | `d - r` | Until the application can begin reacting; not settlement. |
| End-to-end observed / settled | `a - r`, or a separately observed settlement boundary minus `r`. | Distinct, explicitly named downstream endpoints. |

Define whether `a` precedes or follows checking/recording; the default host adapter uses immediately after the application reducer and measures checker/recorder overhead separately. Do not include debugger formatting in an unlabeled reducer measurement. Multiple outputs can share `r` when the adapter sees the output batch together; it is not a claim of exact internal emission time.

For the complete serial path, `end_to_end_delivery = effect_resolution_latency + delivery_lag`. The stages may be summed only when their boundaries form a contiguous nonoverlapping partition in one clock domain. Do not add overlapping parent/child span durations or assume a retry path has a single queue interval.

### 15.4 Clock domain and determinism

Compute local elapsed durations with `std::time::Instant` or a test-injected clock. Use checked subtraction and mark reversed/invalid boundaries as measurement errors, not zero-latency successes. An `Instant` is opaque and not a portable wire timestamp; export a locally computed duration or a process-epoch-relative offset with a clock-domain identifier. Rust documents platform differences, including unspecified treatment of system suspend, so qualification must declare platform and sleep/suspend policy. [T10]

Optional wall-clock timestamps help display and correlate events but must not determine local elapsed metrics. Never subtract monotonic values from different processes, even on the same host. Measure remote-operation client end-to-end latency in the client domain and server execution in the server domain; connect them with explicit trace links. Cross-host one-way network timing requires an explicit synchronized-clock model and uncertainty, otherwise leave it unknown.

**Determinism rule:** runtime clocks and measured durations do not silently enter model equality, canonical replay encoding, oracle state, or correctness checks. If a timeout or latency threshold changes application behavior, the host supplies an explicit timed/logical input and the model handles it deterministically. A performance threshold that only captures diagnostics cannot mutate the model or cause a hidden timeout.

Use a separate observation clock and model clock in tests. A fake timing clock verifies arithmetic, not production performance. Comparisons of instrumented and noninstrumented model runs use identical supplied inputs and canonical semantic values; build stamps can differ. Do not demand byte-identical whole artifacts across honestly different builds.

### 15.5 Retries, cancellation, duplication, and missing endpoints

**Retries:** record queue, start, finish, and outcome for each attempt. Distinguish scheduled backoff duration, actual waiting until eligibility, ready-queue wait, and total inter-attempt gap. An interval from failed attempt to next start includes more than backoff unless all boundaries are observed. Count one effect resolution and multiple attempt outcomes. Set maximum attempts and retained diagnostic records; telemetry limits do not change the application's retry policy.

**Concurrent/hedged work:** assign separate attempt IDs and record the winning result without closing still-running attempts. The sum of attempt elapsed durations is work-time across attempts, not effect latency and not CPU usage. Default version-one wrappers qualify sequential retries; hosts may describe overlapping attempts with low-level hooks, but no additive critical-path claim is made without additional qualification.

**Cancellation:** record request, acknowledgement, and actual attempt termination separately. A dropped future/scope proves local abandonment, not that a remote service stopped. A cancellation request must not decrement a still-running gauge. A late success may coexist with a cancelled logical operation; preserve both facts and any later completion delivery.

**Duplicates:** account authoritative boundaries once using a token's local lifecycle state, even if diagnostic export retries duplicate events. Maintain separate duplicate-observation and duplicate-delivery counts. Bounded transport deduplication is not global exactly-once delivery. Counter/gauge updates never depend on reconstructing sampled exported events.

**Missing endpoints:** active work has an age or lower-bound duration, not a completed-latency sample. A process crash, detach, timeout of telemetry retention, or dropped scope leaves incomplete evidence unless the host positively observes a terminal outcome. Show unknown/censored operations and counts separately. Evicting a diagnostic record must not make the host believe the effect ended or make gauges silently fall to zero.

### 15.6 Host instrumentation and async safety

A proposed `EffectObserver` supplies bounded typed lifecycle methods; an optional owned handle stores accounting state and enabled timing fields. The methods are diagnostics, not an alternative executor. Disabled handles do not read the clock unless another enabled consumer requires timing. Enabled collection must be established at request/attempt start; turning timing on midway cannot recover earlier instants.

```rust,ignore
// API sketch only: the host owns all execution and delivery.
let effect_token = observer.requested(origin, effect_kind);
observer.ready_queued(&effect_token);
let attempt = observer.attempt_started(&effect_token);
let result = execute_effect(effect).await; // real host work, once
observer.attempt_finished(&effect_token, attempt, classify(&result));
observer.resolved(&effect_token, classify(&result));
// Host separately posts, begins delivery, and reports its result.
// Calls are notifications; none dispatches work or re-delivers input.
```

For a concurrent queue, reserve a publication record and capture the posting instant before the item becomes visible; synchronize the token state before a consumer can report delivery. On failed publication, invalidate that attempt. Do not timestamp posting after publishing the item and then blame a faster consumer for a negative interval. Similar linearization rules apply to worker start and queue gauges.

Use async-safe span instrumentation: `Instrument::instrument` enters a span while its future is polled. Do not hold a `Span::enter()` guard across `.await`; tracing documents that as producing incorrect span context. Explicit start/finish timestamps remain the duration authority; span handle lifetime can outlast actual work. [T11, T17]

Observer errors go to bounded diagnostic health reporting without replacing the host's result. Do not introduce early-return operators that cause an observability failure to skip posting a real completion. Destructor fallbacks must not panic, block, mark success, or claim confirmed cancellation. A hook's own exception cannot justify executing an effect or reducer twice.

### 15.7 Debugger pauses, provenance, and performance interpretation

Store `measurement_origin` such as live, debug-controlled-live, test-clock, replay-harness, or imported-live. Track pause intervals and mark operations whose measured interval overlaps a relevant dispatch/worker pause as `debugger_affected`. Exclude those samples from ordinary production dashboards by default, with an explicit opt-in view. Keep an unfiltered count so this exclusion remains visible.

Do not blindly subtract pause time: workers and remote services may continue while dispatch is paused. Retain observed durations and annotate affected stages. Imported historical measurements stay historical and must not be ingested again as current counter or histogram increments. Detecting arbitrary native-debugger pauses is not guaranteed; support a manual session flag and disclose unobserved interference.

Elapsed operation time, sum of active polling intervals, thread/process CPU time, and queue delay are different quantities. CPU profiling, allocation profiling, and async-runtime diagnostics remain optional integrations. A long elapsed span does not identify the CPU hotspot, and a sum of nested/parallel spans does not prove a critical path.

### 15.8 Illustrative timeline

The following is a synthetic host timeline, not a measurement of the repository fixture:

```text
FetchMetadata / effect 42 / attempt 1 / origin turn 128, output 1
request and ready queue       +0 ms
attempt started             +12 ms     queue wait       12 ms
attempt finished/resolved   +96 ms     attempt elapsed  84 ms
completion posted           +97 ms     preparation       1 ms
delivery begun             +100 ms     queue wait        3 ms
delivery observed          +102 ms     processing        2 ms

resolution latency = 96 ms       delivery lag = 4 ms
end-to-end delivery = 100 ms     end-to-end observed = 102 ms
application settlement = unknown unless explicitly observed
```

Display linked model turns, effect/attempt lanes, retries, pending ages, cancellation facts, and log/probe links. Unknown timing is visibly different from zero. A slow-operation trigger can retain already-buffered history and enable future probes; it cannot recover locals or timestamps that were never captured. Optional pre-trigger rings require explicit memory budgets and privacy policy.

<!-- pagebreak -->

# 16. Metrics, aggregation, and performance queries

### 16.1 Collection before diagnostic sampling

Authoritative lifecycle hooks update enabled counters, gauges, and timing aggregates before optional log/span sampling or enqueue. A metric-only configuration need not retain one event per effect. Slow-only logs are a selected population and must never be presented as the complete latency distribution. If metrics are deliberately sampled or a measurement could not be collected, expose that policy and the omitted count where known.

Metric identity and lifecycle accounting are separate from UI subscriptions. Disconnecting a debugger does not reset host counters; disabling a log sink does not finish spans or attempts. One lifecycle token performs one terminal accounting update even with multiple subscribers. Passive import/replay of a telemetry sidecar does not increment live metrics again.

### 16.2 Proposed metric catalog

Metric names below are proposed public names for a Prometheus-style adapter, not existing instruments or finalized OpenTelemetry conventions. The adapter must declare name, unit, kind, allowed labels, population, inclusion rules, and reset behavior. Use seconds for duration observations and integer nanoseconds or typed durations internally until export.

| Family | Kind / unit | Population and update rule |
|---|---|---|
| `effect_requests_total` | Counter / requests | Each observed output request once, including subsequently rejected requests. |
| `effect_admissions_total` | Counter / requests | Each host admission decision once, with bounded accepted/rejected reason family. |
| `effect_attempts_total` | Counter / attempts | Each actual attempt start, not task submission. |
| `effect_attempt_outcomes_total` | Counter / attempts | Once per known attempt outcome; failures are distinct from final effect failures. |
| `effect_resolutions_total` | Counter / effects | Once per logical effect result, labeled by bounded result family. |
| `effect_deliveries_total` | Counter / deliveries | Each actual completion delivery observed, including duplicate, rejected, or ignored inputs. |
| `effects_unresolved`, `effect_attempts_running` | Gauge / count | Logical pending effects and physically running attempts are separate. |
| `effect_ready_queue_depth`, `completion_queue_depth` | Gauge / count | Host-observed queue state, not number of log records. |
| `effect_queue_wait_seconds` | Histogram / seconds | Per attempt with both ready-queue and execution-start boundaries. |
| `effect_attempt_elapsed_seconds` | Histogram / seconds | Completed attempts with valid start/finish; includes elapsed waits. |
| `effect_resolution_seconds` | Histogram / seconds | Known request-to-resolution endpoints, grouped by result family. |
| `effect_delivery_lag_seconds` | Histogram / seconds | Each linked delivered completion with resolution/delivery endpoints. |
| `effect_end_to_end_seconds` | Histogram / seconds | First delivered completion per effect; subsequent deliveries use the delivery-lag family. |
| `completion_processing_seconds` | Histogram / seconds | Delivered input processing with the declared reducer/check boundary. |
| `telemetry_dropped_total`, `measurement_invalid_total` | Counter / items | Bounded reason family; independent from the failing export queue. |

Additional qualified instruments can cover admission, backoff, completion preparation, application settlement, reducer/checker/codec time, or stale-completion rates. They must retain distinct endpoints and never mix runtime effect failures with property violations. Define error-rate denominators: effect-resolution failure rate uses resolved effects in the interval, not attempted retries; admission rejection rate uses admission decisions.

`effects_unresolved` increments on accepted admission and decrements on logical resolution; rejected requests never enter that gauge. `effect_attempts_running` increments at actual start and decrements only at known attempt termination, even when the logical effect resolves or cancellation is requested earlier. Ready-queue gauges follow synchronized enqueue/dequeue/removal or host snapshots, not log delivery.

Gauges should use host-authoritative snapshots when attaching mid-run. Increment/decrement accounting is valid only with complete lifecycle participation and synchronized boundaries. Otherwise label the scope as observed-since-attach or report unknown; do not claim a complete process gauge. Keep logical unresolved effects separate from telemetry records evicted for space. Show the age of active work separately from completed-duration histograms to avoid hiding stuck operations.

### 16.3 Bounded storage and metric dimensions

Use fixed-size histograms or another explicitly bounded aggregate behind a `MetricSink`/snapshot contract. Start with a small in-process cumulative histogram implementation for headless inspection and an optional `metrics` adapter. Exporters must be qualified for actual memory behavior: the `metrics` facade leaves representation to its recorder, and an instrument called a histogram can be exported as a summary. [T9]

Allow metric labels only from bounded sets: operation kind, component, fixed worker pool, outcome family, and measurement origin. Keep effect IDs, machine instances with unbounded lifetimes, user/entity IDs, trace IDs, paths, URLs, payloads, error strings, and arbitrary field values out of labels. A debugger can filter high-cardinality per-effect detail without turning that filter into a new metric time series.

Cap both label-set cardinality and aggregate bytes after label filtering. On overflow, aggregate to a bounded overflow series or reject additional series with visible loss; state which policy was used. OpenTelemetry's Metrics SDK specifies cardinality controls and overflow aggregation, providing a useful model for an adapter's behavior. [T14]

Exemplars may link selected measurements to a trace/effect detail without adding an ID to every metric label. Use a bounded reservoir and advertise exporter support; not every facade or backend preserves exemplars. The aggregate remains valid when an exemplar is absent. [T14]

### 16.4 Percentiles, windows, and resets

Report p50/p95/p99 only with observation count, population, time interval, units, origin, sampling/completeness status, and histogram schema or approximation method. Zero observations produce unavailable percentiles, not zero latency. Very small populations carry a low-sample warning; a reported p99 is not automatically a statistically reliable service-level estimate.

Do not average worker p99 values. Merge compatible histogram buckets/counts for the same population and interval, then calculate the quantile. Histogram resolution limits the estimate; per-instance summary quantiles are not generally aggregatable. Adapter configuration must therefore explicitly request an aggregatable histogram representation when cross-worker percentiles are required. [T13]

Use cumulative counters/histograms with process epoch and start time for the minimal snapshot API. A time-window query differences compatible snapshots in the same epoch. A bounded ring or external backend may supply longer historical windows; without retained endpoints, return unavailable rather than relabeling lifetime totals as the last five minutes. A histogram observation belongs to the interval in which its terminal measurement is recorded, not an invented request-start cohort.

Declare cumulative versus delta temporality and reset epochs; never sum repeated cumulative exports. Reject incompatible histogram schemas or use an explicitly documented conversion. OpenTelemetry's data model distinguishes these temporalities and histogram interval metadata; preserve those distinctions in snapshots and exporters. [T15]

Count errors, cancellations, successful outcomes, pending work, incomplete measurements, and debugger-affected samples separately. Success-only latency views are allowed when named as such, not as all-effects latency. Do not encode timeouts or unfinished work as zero-duration completions. Retain a lower-bound/age view for censored operations.

### 16.5 Queries, slow-operation capture, and adapters

Expose typed operations equivalent to `metric_catalog`, `metric_snapshot`, `metric_window`, `effect_details`, `effect_timeline`, and `telemetry_health`. Responses include revision, epoch, population, limits, and completeness. An exporter-only integration may lack a local history/query capability; advertise it as absent rather than promising a dashboard it cannot supply.

A slow-threshold trigger uses already enabled timing, configurable per operation kind and endpoint. Distinguish execution-slow from queue-slow or delivery-slow. Completed slow events can retain context; detecting currently stuck work additionally requires a bounded host timer/sweep over active operations. That diagnostic sweep never advances model time or injects an application timeout.

Provide adapters rather than a new observability backend: text/JSONL, `tracing`, `metrics`, and optional OpenTelemetry integration. Do not automatically bridge the same family through both `metrics` and OpenTelemetry and double count it. Prevent event feedback loops between imported tracing events and exported Statelessness telemetry; assign an explicit direction or origin filter.

### 16.6 Initial budgets and performance qualification

The following are proposed defaults to validate, not measured overhead or hard process-memory guarantees. Host callback allocations and third-party exporter memory remain outside these caps.

| Companion-owned resource | Initial limit | Exhaustion / policy |
|---|---|---|
| Active subscriptions | 64 per session | Reject new subscriptions; preserve existing ones. |
| One sink queue | 1,024 records and 4 MiB | Default nonblocking drop-newest; independent health counter. |
| One diagnostic event | 32 KiB, depth 32 | Redact/truncate with explicit status. |
| Watch baseline cache | 8 MiB | Evict/reset affected baseline to unknown. |
| Per-effect detail registry | 4,096 records and 8 MiB | Stop detail admission or evict with a gap; never alter host effects. |
| Local metric aggregation | 128 families, 2,048 total series, 8 MiB | Bounded overflow or explicit metric loss. |
| Histogram storage | 64 configured finite buckets plus overflow | Fixed memory per admitted series; retain schema identity. |
| Metric snapshot history | 12 snapshots and 16 MiB | Evict oldest; restrict available query windows. |

Outstanding effect handles are bounded by host concurrency/admission, not by evicting live accounting. Where the library stores additional per-handle state, report its per-handle cost separately. A detail-registry limit must not be mistaken for a bound on the application's effects or total process memory.

Benchmark engine-only, exact-recording-only, runtime-disabled telemetry, counters-only, timings-only, targeted watches/probes, sampled spans, fully captured events, and saturated sinks. Measure allocation counts, dispatcher latency, throughput, histogram update cost, exporter impact, and memory under adversarial label growth. Use controlled open-loop or otherwise explicitly described load where required to expose queue growth; report offered load, achieved throughput, concurrency, and unfinished operations, not only completed requests. No overhead percentage or performance improvement is claimed until qualified measurements exist.

<!-- pagebreak -->

# 17. Qualification and regression test strategy

The debugger must be tested as an execution controller, not merely as a renderer. Preserve current engine, macro, codec, replay, and conformance coverage while adding focused debugger fixtures.

| Area | Required test or invariant |
|---|---|
| Exactly-once semantics | Count reducer calls and oracle advancement; attach recording, inspection, breakpoints, and replay simultaneously. |
| Initial observation | State failure, checker error, and replay divergence before input 1 are visible. |
| Input admission | Rejected/ignored deliveries are recorded; invalid and stale commands do not execute. |
| Safe points | No additional delivery after pause acknowledgment until an authorized command or resume. |
| Replay parity | Rich observer preserves old reports, callback boundaries, mismatch priority, and first-divergence stop. |
| Recording integrity | No ordinary exact trace continues after failure; encoding errors preserve coherent prefixes. |
| Inspection isolation | Read-only queries do not change semantic state, output order, checks, or logical time. |
| Display correctness | Redacted/truncated/opaque nodes never produce false equality; large integers remain exact. |
| History and branching | Parent traces stay unchanged; coherent oracle/queue history is retained; provenance survives export. |
| Macro parity | Handwritten/generated and enabled/disabled instrumentation produce equivalent semantic observations. |
| Protocol safety | Duplicate/stale requests, malformed messages, reconnects, obsolete handles, and queue pressure are handled explicitly. |
| Live integration | Detach, overflow, dispatch-before/after-pause, missing observations, and host errors preserve honest status. |
| Lazy instrumentation | Disabled probe closure/formatter/clock call counters remain zero unless another consumer requires them; one reducer invocation. |
| Subscription changes | Acknowledged revisions, per-producer boundaries, initial/gap baselines, and sampled-change labels are explicit. |
| Pipeline independence | Dropped or sampled logs do not affect exact recording, lifecycle accounting, or unsampled aggregates. |
| Effect lifecycle | Admission rejection, retries, never-polled futures, cancellation request/confirmation, duplicate delivery, and missing endpoints stay distinct. |
| Timing arithmetic | Validate one-domain endpoints; invalid/reversed clocks are not zero; no cross-process subtraction or overlapping-span addition. |
| Metric statistics | No averaging of p99s; fixed schema, declared windows/populations, resets, no samples, overflow, and partial coverage are tested. |
| Async context / privacy | No span-enter guard across await; task context and skip/redaction policies survive sinks and reconnects. |
| Runtime interference | Same-input semantic parity and distinct live scheduling/latency measurements; debugger pauses and imported metrics do not contaminate production totals. |

### 17.1 Required fixtures

Use the existing request lifecycle for cancellation and stale completion; a composed-machine fixture for delivery order; an oracle-backed lifecycle fixture for missing settlement and resource ownership; and new fixtures for large nested state, unavailable output decoding, invalid input payloads, and intentional callback errors. Source existence is not a claim that debugger versions of these fixtures already exist. [R1, R6, R7]

Add a fake-clock host dispatcher with scripted ready-queue, retry, cancellation, completion-posting, and delivery boundaries. Include the 12 + 84 + 4 ms example, admission delay, delayed posting, retry-plus-queue gaps, cancelled-but-running work, and no completion input. A publish-before-timestamp race must be prevented by the adapter contract. Verify real collector integration separately; synthetic durations are arithmetic tests only.

### 17.2 Test the debugger controller with Statelessness

Model the controller's own decisions using the existing engine: command admission, pause/resume, revision advancement, terminal evidence, duplicate request handling, and disconnect policy. Also model subscription revision admission and the telemetry-only effect-accounting ledger; check valid gauge updates, one terminal count, and explicit unknown states without altering actual host work. Check that one admitted step authorizes at most one delivery, observations never move backward in an epoch, and an acknowledged paused session cannot auto-run.

Keep the test model's assumptions explicit. The real controller and its modeled decision core should share decision logic where feasible; independently check required externally observable outcomes so shared implementation defects do not become tautological passes.

### 17.3 Release evidence

Publish reproducible commands, baseline commit, compiler/profile, platform matrix, tests run, known limitations, and benchmark configurations. Include cold-offline core build checks proving debugger dependencies did not leak into the default engine. Interactive UI behavior requires frontend-specific tests in addition to core conformance. Publish disabled/enabled allocation and clock-read counts, metric storage bounds, log-sampling independence tests, and first-party adapter interoperability for the exact versions claimed. Compare normalized semantic state/input/output/check observations across instrumentation builds; do not require identical build-fingerprinted whole files.

Native-debugger examples must be smoke-tested on the platforms actually claimed. Do not infer universal LLDB/GDB behavior from one operating system or a synthetic fixture.

<!-- pagebreak -->

# 18. Milestones and acceptance gates

Milestones are ordered by semantic dependency, not calendar estimates. A milestone is complete only when its acceptance evidence is checked in with the implementation.

### M0 — Freeze contracts and preserve the baseline

**Deliver:** architecture decisions; distinction between observation and control; capability vocabulary; atomic-turn/error contract; a repository map of required changes; regression fixtures for current replay and recorder behavior; logging/event/clock schemas, privacy defaults, metric populations, and authoritative host timing boundaries.

**Gate:** all existing engine and macro tests used by the project still pass. No new required model trait bounds, default external dependencies, or exact trace-format changes. Unsupported current behaviors are documented before refactoring.

### M1 — Typed observations and interactive simulation

**Deliver:** rich initial/turn observations; typed one-input session; state/output/check display through handwritten adapters; semantic breakpoints; budgets; linked request-lifecycle harness; compatible adapter for the old replay observer.

**Gate:** the fixture's failing sequence is stepped interactively and recorded without duplicate reducer calls or checks. Initial errors, rejected inputs, checker failures, and observer failures are distinguishable. A model without codecs can still step; missing capabilities fail explicitly.

### M2 — Trace workbench and reproducible branches

**Deliver:** browse/verify distinction; exact trace loading; typed replay differences; retained snapshot navigation; compatible-build comparison; branch provenance; regression export; bounded minimization integration where supported.

**Gate:** a saved failure reproduces in a separate harness process; a corrected build shows an honest first divergence; no branch overwrites its parent; incompatible identities are rejected; history eviction and missing output decoders remain visible. Post-failure continuation remains disabled or uses an explicitly separate artifact class.

### M3 — Inspection and macro ergonomics

**Deliver:** stable versioned inspection contract; derive support for qualified Rust shapes; field/variant labels; redaction; source/property metadata; paged collection views; post-turn diagnostic markers. Qualify true in-reducer probes/markers through the explicit sink path in M3a, without changing this inspection/codec contract.

**Gate:** handwritten and generated inspectors agree; semantic observations remain equivalent with instrumentation enabled/disabled; changing display metadata does not change exact codec bytes. Unsupported derives fail clearly. Secrets are absent from redacted transport tests.

### M3a — Runtime-selective logging, probes, and routing

**Deliver:** scoped diagnostic sink; lazy `probe!`/`marker!` helpers; inspector-backed state watches; selectors and value/change triggers; acknowledged subscription revisions; console/JSONL/custom sinks; optional tracing adapter; redaction, byte budgets, and independent loss reporting. Local typed subscriptions ship here; remote control of subscriptions follows M4.

**Gate:** disabling capture skips probe closures/formatters; multiple subscribers do not rerun reducers or checks; log drops cannot weaken exact traces; sampled delivery does not falsely imply complete change detection; unacknowledged producers and stale baselines are explicit. Privacy tests cover every sink and diagnostic fault path.

### M3b — Host effect timing and bounded metrics

**Deliver:** effect/attempt/delivery identities; host-only lifecycle hooks; checked local-clock durations; cancellation and missing-endpoint states; a typed timeline; bounded counters/gauges/histograms and snapshot queries; optional metrics adapter; measurement-origin and pause annotations. Qualify a simple host dispatcher before general live-control integration.

**Gate:** fake-clock fixtures verify the stated endpoint definitions, retries, admission rejection, cancellation-versus-running work, duplicate delivery, and unknown data. Counters/timings still work with logs disabled or sampled; imported/replayed telemetry does not increment live metrics; paused samples are separated; aggregation/cardinality/epoch/window tests pass. Run real async adapter smoke tests and publish an overhead baseline; do not infer overhead from fake-clock tests.

### First release recommendation

Ship M1–M3 plus M3a and M3b with documented limitations as the expanded first release. A textual/headless interface is sufficient. Do not delay the useful core for a large visual state graph, editor extension, or remote live control.

### M4 — Structured protocol and optional frontend

**Deliver:** versioned request/response/event protocol; stdio bridge; bounded owned views; revision-bound commands and query handles; scripted agent workflows; remote subscription revisions, effect timelines, metric catalog/snapshots/available windows, and health queries; optional TUI over the same commands.

**Gate:** clients cannot mutate an observation-only session; retries cannot accidentally deliver an input twice within the declared deduplication contract; stale candidate tokens are rejected; reconnect and truncation tests pass. Frontend output escaping and large-integer handling are qualified.

### M5 — Live observation

**Deliver:** application bridge for actual transitions; coherent mid-run attach; bounded view stream; trace export; sequence/gap reporting; integration of already qualified M3b effect timings with model turns; coherent mid-run gauge baselines and independent metric/log capture controls.

**Gate:** observation never executes the application reducer or effects; instrumentation failure preserves actual host state; missing delivery, history eviction, and recorder freeze are reported honestly. The observer cannot acquire control through reconnect or a UI action.

### M6 — Cooperative live control

**Deliver:** host safe-point adapter; pause acknowledgement; one-turn authorization; bounded external-arrival policy; explicit effect-dispatch phase; detach policy; opt-in input-injection authority.

**Gate:** controller state-machine tests and application integration tests prove no duplicate dispatch, no unintended delivery after pause, no stale-revision mutation, and explicit behavior on overflow/disconnect. Live arbitrary state restoration remains unsupported unless separately designed and qualified.

### Later extensions, not release prerequisites

Editor/DAP integration; side-by-side causal visualization; native-debugger launch helpers; distributed trace adapters; qualified overlapping/hedged-attempt analysis; CPU/allocation profiling links; advanced pre-trigger/tail sampling; specialized logical-time adapters; application-owned input forms; cross-language clients and implementations of the debugger protocol.

Cross-language work must define its own capability and evidence compatibility rules. The presence of native language ports does not by itself make arbitrary Rust model snapshots or debugger inspectors interchangeable. Preserve the existing semantic conformance corpus and add a separate debugger-protocol corpus when that work begins.

### Global definition of done

The documented mode matches actual authority; exact and projected evidence are visibly distinct; all delivered inputs are accounted for or a gap is explicit; check failure never becomes success through display/transport failure; non-debug models remain source-compatible; and the release has reproducible tests for every advertised capability.

Documentation includes a minimal handwritten model harness, a macro-assisted equivalent, a saved failure walkthrough, a corrected-build comparison, and a host timing/logging example and a live-control integration example only for the live capability actually released. The expanded release also documents metric units, populations, unknown/censored data, histogram schema, reset/window behavior, capture privacy, and independent telemetry/evidence reliability.

<!-- pagebreak -->

# 19. Engineering workstreams and risk controls

### 19.1 Dependency graph

```text
M0 contracts -> M1 execution / observations
    -> M2 replay / branches
    -> M3 inspection / macros -> M3a logging / probes
    -> M3b host effect timing / metrics (shared M3a contracts)
        M2 + M3 + M3a + M3b -> M4 protocol / frontend
            -> M5 integrated live observation -> M6 live control
```

After the observation contract is fixed, inspection, replay presentation, and frontend prototyping can proceed in parallel behind test doubles. Only one workstream should own shared execution/checking refactors at a time. M3b can build against frozen M3a sink/identity contracts in parallel with exporter implementation; timing does not depend on acquiring live-control authority or completing M5.

### 19.2 Suggested implementation ownership

| Workstream | Primary ownership | Integration constraint |
|---|---|---|
| Engine | Observation types, shared checked execution, replay adapters. | Preserve old behavior before exposing new consumers. |
| Inspection | Field paths, paged nodes, diffs, redaction, display schema. | No dependency on UI layout or exact codec internals. |
| Session/evidence | Admission, revisions, breakpoints, branches, export. | Uses engine observations, not a second reducer path. |
| Macros | Derives and source metadata. | Wait for handwritten trait contracts and golden tests. |
| Observability | Scoped probes, subscriptions, event schema, privacy, sink routing. | One source observation; independent exact recording and per-sink loss. |
| Effect timing / metrics | Host lifecycle, clock domains, measurement catalog, aggregate storage. | No reducer execution; aggregate before sampled diagnostic output. |
| Protocol/frontend | Messages, stdio, CLI/TUI, agent and performance reports. | Consumes stable session/query operations; no hidden scheduling. |
| Live bridge | Host attachment, safe points, authority, dispatch policy. | Begins only after simulation/evidence gates are met. |

### 19.3 Principal risks

**Semantic duplication:** a new debugger runner may subtly diverge from recording/replay. Share internal primitives and compare outcomes across old and new entry points.

**False completeness:** a pretty state view can hide omitted queues, oracle history, redacted values, or evicted observations. Make completeness and provenance part of every query response.

**Instrumentation changes behavior:** hidden collectors, formatting work, or reentrant callbacks can alter timing or state. Keep diagnostics outside semantic state, forbid reentrant stepping, and qualify disabled/enabled equivalence within the model.

**Live-control ambiguity:** pausing dispatch while workers continue can overflow queues or duplicate effects. Make host policy explicit and treat M6 as a separate integration project.

**Metric distortion:** sampled slow logs, unbounded labels, mixed reset epochs, or treating cancellation as completion can yield convincing but incorrect numbers. Enforce the metric catalog and unknown/partial states in producers and queries.

**Scope expansion:** reflection, a general scripting engine, remote debugging, and a graph editor can overwhelm the useful core. Reject features that do not improve the initial inspect–step–fail–replay workflow until that workflow ships.

### 19.4 Change-control rule

Any proposal to weaken identity checks, omit replay-relevant fields, auto-drain queues, execute effects during replay, or continue exact evidence after failure requires a separate architectural decision and qualification plan. Convenience is not sufficient justification for changing evidence semantics. Also require a versioned decision for changing timing endpoints, metric populations/units, histogram schemas, cancellation accounting, or telemetry admission policy. Dashboard continuity is not a reason to silently reinterpret a metric.

<!-- pagebreak -->

# 20. Worked request-lifecycle walkthrough

This walkthrough is derived from the existing `RequestModel` fixture. The commands are proposed debugger syntax; the displayed transition behavior follows the reviewed source rather than a newly executed debugger. [R7]

### 20.1 Reproduce the defect

```text
> model request-lifecycle --buggy
> state
{ generation: 0, active: false, ready: false, pending: [] }

> step Start
state:   generation=1, active=true, ready=false, pending=[1]
outputs: [Request(1)]
checks:  passed

> step Cancel
state:   generation=1, active=false, ready=false, pending=[1]
outputs: []
checks:  passed

> step Complete(1)
state:   generation=1, active=false, ready=true, pending=[]
outputs: [Publish(1)]
failed:  ready_requires_active
failed:  stale_completion_is_cleanup_only
stop:    property failure at transition 3
```

The pending completion remains deliverable after cancellation. The bug is not that the environment delivers it; the buggy reducer publishes readiness when it should release the stale completion. Both the state rule and the transition-output rule identify the failure. [R7]

### 20.2 Inspect and preserve

The developer selects transition 3 and sees the before-state, delivered generation, removed pending entry, emitted publication, and both failing property IDs. A source link can lead to the reducer or the relevant checker. Export the exact failing trace without extra continuation steps.

Open the trace in viewing mode to inspect it without executing the model. Switch to verified replay through the linked harness to establish whether the selected build reproduces its observations. Show both the verified prefix and the build identity.

### 20.3 Compare the corrected build

Replay with the fixed fixture and explicit build-mismatch permission. Its completion path leaves `ready=false` and emits `Release(1)`. The actual outputs differ from the recorded `Publish(1)`, so exact replay diverges at transition 3; the original failure is not reproduced there. The current replay comparison order reports outputs before state. [R3, R7]

Do not convert “diverged from the failing trace” into “the application is proven correct.” Run the model's broader exploration and regression suite under the same declared assumptions. To investigate a different schedule, fork from a verified state before failure and preserve the original trace.

### 20.4 Native debugger handoff

Use the identified input sequence and transition index to launch the same harness under LLDB/GDB. Set a source or function breakpoint inside `Model::step`, advance to the third delivery, and investigate the branch at instruction/source level. No resumable-reducer macro is necessary.

### 20.5 Add selective diagnostics without inventing runtime measurements

A watch on `ready` can emit a field-change event at transition 3 and route it to a log and debugger panel. A property-failure subscription references the same checked batch; it does not rerun the checker. An explicit internal probe can show a generation comparison only when the application helper exposes that probe site.

The request fixture emits modeled `Request`, `Publish`, and `Release` outputs; it is not a measured asynchronous effect executor. Do not attach the illustrative 100 ms timing to it. Real queue/execution/delivery metrics come from an independently instrumented host as defined in Section 15. The debugger may link those measurements to the fixture-style causal origin when such a host integration exists.

<!-- pagebreak -->

# 21. Revision review and requirement coverage

### 21.1 Review outcome

The logging and metrics proposal is integrated into the architecture, authority model, artifacts, protocol, privacy rules, resource limits, acceptance tests, milestone plan, and engineering workstreams. The original design's transition atomicity, application ownership, no-real-effects replay, and exact-evidence rules are preserved. This is a document consistency review, not evidence that a debugger or timing system has been built or qualified.

### 21.2 Issues resolved during this revision

| Review finding | Resolution in version 1.1 |
|---|---|
| “Shared stream” could put exact evidence and metrics behind sampled logs. | Shared typed boundaries, separate paths; aggregates update before diagnostic sampling. Sections 4, 14, 16. |
| State watches were treated as substitutes for arbitrary local logging. | Exposed fields use the inspector; locals require explicit lazy probes. Sections 6, 9, 14. |
| Runtime disable could promise no work even for enabled value filters/timings. | Interest checks, value-dependent cost, and independent timing consumers are explicit. Sections 13–14. |
| A request output could be mistaken for real execution. | Host owns request/admission/attempt/result/delivery observations. Sections 12, 15. |
| Completion delivery could be mislabeled as application settlement. | Delivery start, observed disposition, and explicit settlement are separate endpoints. Section 15. |
| Failed retries/cancellation/RAII drop could corrupt outcome metrics. | Separate attempts and effect resolution; requested cancellation and abandonment do not confirm termination. Sections 15–16. |
| Queue publication could race timestamp/accounting updates. | Reserve/update token and timestamp before visibility; invalidate failed publication. Section 15.6. |
| Unknown timings, pending work, or paused samples could skew percentiles. | Unknown/censored and debugger-affected populations remain explicit; historical import is read-only. Sections 15–16. |
| An exporter “histogram” could be a nonaggregatable summary or unbounded buffer. | Qualify representation, schema, memory, windows, and merge behavior; no averaging p99s. Section 16. |
| Enabled/disabled tests could incorrectly demand identical live schedules or build-stamped artifacts. | Same-input semantic comparisons; separate real scheduling/overhead tests and provenance. Sections 14, 15, 17. |
| Logging/timing could remain optional appendices with no delivery owner. | M3a/M3b, dedicated workstreams, fixtures, and first-release gates added. Sections 18–19. |

### 21.3 Requested-feature traceability

| Requested capability | Specification | Acceptance gate |
|---|---|---|
| Select state variables at runtime; value/change triggers | 6, 14.3 | M3, M3a; baseline/gap/sampling tests. |
| Function-local probe macros and markers | 9, 14.4 | M3a; skipped-closure and same-input parity tests. |
| Machine/entity/transition scoping and subscriptions | 11, 14.2–14.3 | M3a/M4; revision and capability tests. |
| Console/file/network/custom consumers with routing | 10, 14.5–14.6 | M3a/M4; privacy, queue, loss, and shutdown tests. |
| Effect queue/execution/retry/delivery performance | 15 | M3b; host fake-clock and real async integration fixtures. |
| Counts, queue depth, throughput, latency percentiles | 16 | M3b; population, histogram, cardinality, epoch, and window tests. |
| Disabled-cost control and no mandatory core dependencies | 10, 13, 14.7, 16.6 | M0/M3a/M3b; cold-offline and benchmark evidence. |
| Replay and real-time performance stay independent | 4, 8, 15.4, 15.7 | M2/M3b; telemetry does not alter/re-ingest exact execution. |

### 21.4 Implementation decisions still requiring evidence

Finalize public type names, supported derive shapes, histogram bucket boundaries, platform clock qualifications, and exporter versions with the implementation. The tabled memory limits are starting design budgets, not measured optima. Sophisticated distributed critical-path analysis, overlapping attempts, CPU profiling, and remote live control remain separately qualified work. No benchmark, compilation, network-delivery guarantee, or production safety result is implied by this revision.

<!-- pagebreak -->

# 22. Sources and reviewed baseline

Repository references retain the version 1.0 baseline, `cb7f256e4d7cf20cfab6018b9bbba391ec2dd9de`; this revision did not refresh the code audit. Original references are retained. Observability sources T9–T17 were checked October 10, 2026. References support existing behavior, not implementation of the proposed design.

### Repository references

**[[R0]](https://github.com/Jagalite/statelessness/commit/cb7f256e4d7cf20cfab6018b9bbba391ec2dd9de) Baseline revision.** Jagalite/statelessness; the pinned commit used for the original code review.

**[[R1]](https://github.com/Jagalite/statelessness/blob/cb7f256e4d7cf20cfab6018b9bbba391ec2dd9de/README.md) README.md.** Model limits, CLI capabilities, and oracle runtime observation/error handling.

**[[R2]](https://github.com/Jagalite/statelessness/blob/cb7f256e4d7cf20cfab6018b9bbba391ec2dd9de/src/model.rs) src/model.rs.** Determinism, trait/codec bounds, transition/check values, and input domains.

**[[R3]](https://github.com/Jagalite/statelessness/blob/cb7f256e4d7cf20cfab6018b9bbba391ec2dd9de/src/execution.rs) src/execution.rs.** Checked observation, recording, replay comparison/compatibility, and post-failure restrictions.

**[[R4]](https://github.com/Jagalite/statelessness/blob/cb7f256e4d7cf20cfab6018b9bbba391ec2dd9de/src/monitor.rs) src/monitor.rs.** Bounded recorder, freeze behavior, checkpoint continuity, and observation-delivery limitations.

**[[R5]](https://github.com/Jagalite/statelessness/blob/cb7f256e4d7cf20cfab6018b9bbba391ec2dd9de/crates/statelessness-macros/README.md) crates/statelessness-macros/README.md.** Companion exports and dependency isolation.

**[[R6]](https://github.com/Jagalite/statelessness/blob/cb7f256e4d7cf20cfab6018b9bbba391ec2dd9de/docs/MACROS.md) docs/MACROS.md.** Adapters, property catalogs, codec/display boundaries, lifecycle monitors, composition, and packaging.

**[[R7]](https://github.com/Jagalite/statelessness/blob/cb7f256e4d7cf20cfab6018b9bbba391ec2dd9de/src/demo.rs) src/demo.rs.** Request-lifecycle behavior and property IDs used in Section 20.

### External technical references

**[[T1]](https://lldb.llvm.org/use/map.html) LLDB documentation.** Tutorial and GDB-to-LLDB command map: breakpoints, watchpoints, variable inspection, and stepping.

**[[T2]](https://github.com/vadimcn/codelldb/blob/master/MANUAL.md) CodeLLDB manual.** Rust/Cargo support and reverse execution with compatible backends.

**[[T3]](https://docs.rs/tracing/latest/tracing/) tracing crate documentation.** Structured diagnostic events and spans.

**[[T4]](https://github.com/tokio-rs/console) Tokio Console repository documentation.** Instrumented asynchronous task and resource diagnostics.

**[[T5]](https://doc.rust-lang.org/cargo/reference/profiles.html) Cargo Book — Profiles.** Debug information, development profiles, and optimization/debugging tradeoffs.

**[[T6]](https://doc.rust-lang.org/std/fmt/trait.Debug.html) Rust standard library — std::fmt::Debug.** Programmer-facing formatting and the lack of a stable derived Debug representation.

**[[T7]](https://doc.rust-lang.org/reference/procedural-macros.html) Rust Reference — Procedural macros.** Token-stream-based macro model and generated-code boundaries.

**[[T8]](https://microsoft.github.io/debug-adapter-protocol/overview.html) Debug Adapter Protocol — Overview.** Separation between debugger implementations and development-tool interfaces.

**[[T9]](https://docs.rs/metrics/latest/metrics/) metrics crate documentation.** Counters/gauges/histogram facade; application-owned recorder configuration; exporter-dependent representation and storage.

**[[T10]](https://doc.rust-lang.org/std/time/struct.Instant.html) Rust standard library — std::time::Instant.** Opaque local clock readings, checked subtraction, monotonicity limitations, and platform/suspend caveats.

**[[T11]](https://docs.rs/tracing/latest/tracing/span/struct.Span.html) tracing — Span.** Enter-guard warning across await and span-lifetime semantics.

**[[T12]](https://opentelemetry.io/docs/concepts/context-propagation/) OpenTelemetry — Context propagation.** Explicit cross-boundary trace context and sensitive baggage/trust considerations.

**[[T13]](https://prometheus.io/docs/practices/histograms/) Prometheus — Histograms and summaries.** Quantile estimation and aggregation limitations; histogram versus summary behavior.

**[[T14]](https://opentelemetry.io/docs/specs/otel/metrics/sdk/) OpenTelemetry — Metrics SDK specification.** Attribute filtering, cardinality limits, overflow aggregation, and exemplars.

**[[T15]](https://opentelemetry.io/docs/specs/otel/metrics/data-model/) OpenTelemetry — Metrics data model.** Histogram populations, time intervals, temporality, and aggregation identity.

**[[T16]](https://docs.rs/tracing/latest/tracing/attr.instrument.html) tracing — instrument attribute.** Default argument capture and skip/skip_all controls.

**[[T17]](https://docs.rs/tracing/latest/tracing/trait.Instrument.html) tracing — Instrument trait.** Async future span instrumentation during polling and context propagation.

### Evidence limitations

Section 21 reviews design consistency and coverage, not implementation correctness. No compiled API, benchmark, platform qualification, or live-control safety claim is made. Those claims require the milestone evidence in Sections 17–18.

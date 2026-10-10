# Rust transition debugger

`statelessness-debug` is an optional, headless companion. An application links its
own `Model`; it does not dynamically load models named by a trace. The core engine
remains dependency-free and existing model, codec and trace contracts are intact.
The [v1.1 design](Statelessness-Rust-Debugger-Design-v1.1.md) and
[contract reconciliation](debugger/CONTRACTS.md) describe the implementation scope.

## First run

Use Rust 1.90+ and an unused output filename:

```sh
cargo run --locked --offline -p statelessness-debug --example debug_request -- record /tmp/request-failure.sttrace
cargo run --locked --offline -p statelessness-debug --example debug_request -- verify /tmp/request-failure.sttrace
cargo run --locked --offline -p statelessness-debug --example debug_request -- compare /tmp/request-failure.sttrace
cargo run --locked --offline -p statelessness-debug --example debug_request -- view /tmp/request-failure.sttrace 3
cargo run --locked --offline -p statelessness-debug --example debug_request -- fork /tmp/request-failure.sttrace 2 /tmp/request-branch.sttrace
cargo run --locked --offline -p statelessness-debug --example debug_request -- interactive
```

The interactive fixture accepts `state`, `inputs`, `step Start`, `step Cancel`,
`step Complete 1`, `export NEW_PATH`, and `quit`. Its deliberately buggy sequence
publishes a cancelled completion at turn 3. Both `ready_requires_active` and
`stale_completion_is_cleanup_only` fail. Normal execution ends there. Comparing
the corrected fixture, with explicit build mismatch permission, reports the first
divergence at turn 3: outputs first, also state and checks. The verified prefix is
2; the failure is not reproduced. This is not a proof of general correctness.

`view` does not execute anything. It labels stored encoded values, retained range
and termination. `verify` executes the explicitly linked model. Output decoding
is optional; `ModelCodec` alone does not supply it. A replay's actual output is
labeled replay-produced, including at divergence.

## Link your model

Add the companion as a path dependency while developing:

```toml
[dependencies]
stateless = { package = "statelessness", path = "../statelessness" }
statelessness-debug = { path = "../statelessness/crates/statelessness-debug" }
```

A plain model needs neither a codec, Debug, Send, Sync nor `'static`:

```rust,ignore
let mut session = DebugSession::new(
    "my-session", model,
    InputPolicy::declared("application-domain", |model, state, input| {
        model.environment_permits(state, input)
    }),
    SessionLimits::default(),
)?;
let result = session.step(session.revision(), input)?;
let observation = session.observation();
```

For a finite `Enumerate` implementation, use `InputPolicy::enumerated()`.
Admission is independent of the reducer's accepted/rejected/ignored disposition.
Explicit `unrestricted()` admission is an out-of-domain experiment, labeled in
exports. Candidate tokens bind the value, session instance, revision and domain;
an old list index can never silently select a new value. Callback/iterator work
is cooperative; a blocking application callback cannot be interrupted.

`DebugSession::recording` additionally needs `ModelCodec`, `RunConfig` and
`RecorderOptions`. Checking is performed once and handed to the recorder as a
sealed engine-checked token. Application output effects are never executed.
Initial state is sequence zero. All successfully returned transitions, including
rejections and ignored inputs, consume a sequence. Reducer/checker/observer and
exact-capture errors remain distinct. The actual completed result remains
inspectable after checker, recorder or frontend failure. A property failure is
never replaced with success by a diagnostic fault.

### Breakpoints and progress

Typed pre-breakpoints inspect state/input before delivery. A stop consumes no
sequence or revision; repeating the same pending input bypasses that boundary
once. Post-breakpoints inspect the completed atomic turn. They cannot pause in
an arbitrary reducer statement. `run_bounded` has an explicit sequence and turn
limit and reports budget exhaustion. It never drains a composition queue on its
own. `examples/debug_composition.rs` demonstrates one queued message per command.

## Inspection and field watches

The ordinary `Inspect` trait has a bounded, structural-path query. Built-in
adapters handle primitives, objects/enums, sequences and deterministic maps.
`statelessness_macros::Inspect` is optional; supported shapes and compile-fail
qualification are in [inspection documentation](DEBUGGER-INSPECTION.md).
Redacted fields need not implement Inspect and are never evaluated. Display
schema/version/source metadata is separate from codecs and model equality.
Large integers retain typed decimal strings. Redacted, opaque, incomplete and
truncated nodes produce unknown comparisons, never false equality.

Use `WatchRegistry` with explicit allowed paths and `add_validated` to resolve
an exposed field at a safe boundary. Enable, disable, pause, expire, remove and
snapshot operate on diagnostic configuration, with a generation and effective
boundary. A disabled/re-enabled watch establishes a fresh baseline unless a
supported explicit retention policy says otherwise. Watch TTL is measured in
observation sequences, not model time. Unknown paths/redacted selectors and
unbounded wildcard requests are rejected; selected collections remain paged.

Each host supplies every boundary and an `ObservationContext` containing its
snapshot identity/origin. Sampling, missing observations, schema changes,
redaction, ring drops and baseline eviction remain separately visible. A boundary
watch cannot see an internal 0 → 1 → 0 excursion that returns to 0 before commit.
Failure snapshots contain only the already-retained bounded diagnostic window.

## Scoped local probes and independent sinks

`DiagnosticHub` supplies explicit per-turn `ScopedDiagnostics`; the application
passes that sink into its shared reducer helper. No global subscriber, hidden
thread-local state, re-execution, or mandatory Model parameter is introduced.

```rust,ignore
let mut scope = hub.begin_turn(producer, run, turn, DiagnosticOrigin::Live)?;
let result = reduce_once(state, input, &mut scope);
scope.finish(result.is_ok()); // use the actual check status when available
```

Inside the helper, use `probe!(scope, "decoder.depth", || &depth)` and
`marker!(scope, "decoder.retry")`. Unselected probes skip the closure, inspector,
formatting and clocks. A selected closure is evaluated once for multiple sinks.
Do not put application-essential work or side effects into a probe closure.
The `compiled-out-diagnostics` feature removes the macros' evaluation path;
enabled builds are separately compiled/tested. Early scope drop publishes an
incomplete diagnostic batch, never a successful application result.

Subscriptions select site, severity, structural projection, typed equality,
change or unsigned-integer threshold crossing. Thresholds include hysteresis and
turn-based cooldown. Configure with an expected capture revision, then explicitly
acknowledge each producer's effective boundary. Pending producers retain their
old configuration. Log delivery sampling happens after value/change evaluation.
There is no unrestricted expression evaluator.

`ScopeSelector` additionally matches host-declared producer, origin, model,
machine, entity, correlation, input/output variant, property, effect-kind and
source-site labels. `configure_selected` and `begin_turn_with_context` are the
additive typed entry points. Every populated selector is ANDed; unknown metadata
never matches. Property failure has three states (unknown/failed/not failed),
so pre-check probes cannot masquerade as successful property observations.
Context labels are borrowed, bounded and used for selection only; they are not
automatically exported. A filtered boundary resets comparison continuity.
Property-triggered post-check capture cannot retroactively recover an unselected
in-reducer value; a host must deliberately retain any desired pre-trigger data.

Each sink has independent allowlisted sites/paths, record/byte limits, drop-newest
(default) or drop-oldest policy and health counters outside its queue. Text,
JSONL and custom `DiagnosticSink` writers run only when the host drains a queue,
outside reducers/effect locks. The debugger stream is the same bounded owned
event queue. Redaction precedes queueing, including when another sink has broader
permissions. Text/JSONL escape controls and retain exact integer strings. Custom
sinks/enrichment must preserve that privacy contract. Exporter failures, sampling,
filtering, truncation and abandonment have separate counts. A bounded shutdown
can drain a host-selected amount and explicitly abandon the remainder; host I/O
is not preemptible. No external telemetry destination is installed automatically.

## Evidence and branching

`TraceViewer` owns immutable exact evidence with an independent view cursor.
`fork_verified` re-executes the selected pre-failure prefix using the selected
build and only then decodes its composite checkpoint. No parent is overwritten.
Provenance includes parent artifact binding, checkpoint, fork sequence, identities,
environment and verified-prefix status. FNV-1a plus byte count detects accidental
sidecar mismatch; it is not cryptographic authentication.

`workbench::minimize_with_policy` uses the trace's saved checkpoint for every
candidate, the original `InputPolicy` and a positive admission-candidate budget,
application `Generate::is_enabled` causality, a stable failing property/phase and
cooperative attempt, transition, cancellation and deadline budgets. Its signature
is `(model, trace, &policy, max_admission_candidates, target, config, limits)`.
The policy label and unrestricted/declared origin must match the recorded
provenance; the application is responsible for supplying the original callback.
A matching label alone cannot authenticate its implementation. Enumerated policy
validation is bounded to the candidate budget plus one lookahead value per input.

The legacy `workbench::minimize` remains suitable for traces without debugger
policy provenance and explicitly unrestricted captures, subject to Generate's
causal restrictions. It rejects declared or unknown recorded policies rather than
silently replacing them with Generate's permissive default. Both APIs report the
algorithm's tested smaller reproducer, not global minimality. The caller supplies
the target phase because v1 traces do not persist the state/transition check split.
Arbitrary state editing and exact continuation after property failure remain
unsupported.

Clone is not proof of isolation for shared mutable handles. Application snapshots,
including queues and oracle history, must honor the deterministic Model contract.
A displayed socket/resource ID is not a checkpointable OS object. Raw exact
traces may contain secrets even when display projections are redacted.

## Protocol, effects, and live hosts

The [stdio protocol](DEBUGGER-PROTOCOL.md) has separate execution and capture
revisions, bounded request-ID deduplication, epochs and snapshot handles. Retries
outside the declared retained window require reconciliation; no network-wide
exactly-once promise is made. No network listener or arbitrary export path is
exposed. The host owns authentication/authorization if it adds another transport.

[Live integration](DEBUGGER-LIVE.md) separates observation-only capture from
opt-in cooperative dispatch control. Observation accepts actual checked host
results and never runs a reducer/effect. Mid-run attachment includes a coherent
checkpoint and explicit host sequence. Missing delivery, eviction, duplicate
observation and recorder freeze remain visible. Request outputs and confirmed
effect dispatch are separate facts.

Effect timing/metrics are independent host notifications. See the effects and
[host timing guide](DEBUGGER_EFFECT_METRICS.md), its integration example and
checked-in qualification evidence.
They distinguish admission, ready queue, actual attempt polling, result resolution,
publication, delivery and application settlement. Cancellation request is not
physical termination. Local monotonic clock domains never become model time.
Counters/histograms consume authoritative lifecycle observations before diagnostic
sampling. Imported/replayed measurements do not increment live totals. Debugger-
affected samples and incomplete/unknown endpoints remain separately labeled.

## Qualification and limits

Run `python3 scripts/check-debugger.py` for the source-bound local suite, fresh
process workflows and cold-offline dependency checks. See `validation/debugger/`
for actual commands/results and benchmark configurations. Benchmarks describe
these fixtures and this host; no universal overhead guarantee is claimed.
Native platform adapters and LLDB/GDB integration are not qualified by Linux Rust
tests. Optional TUI, remote transports, tracing/metrics/OpenTelemetry backends,
editor integration and arbitrary live snapshot restoration are not enabled here.

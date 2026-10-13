# Statelessness — Complete Improvement Recommendations and Implementation Roadmap

**Review basis:** Uploaded `statelessness-source(1).zip`, reviewed October 12, 2026  
**Purpose:** A consolidated, implementation-oriented list of the improvements recommended during the source review, with ordering, tradeoffs, risks, and acceptance criteria.  
**Scope:** Rust core, macro companion, debugger/observability, composition, portable specification, native ports, tests, documentation, and application/agent adoption.

> **Status of claims.** A **confirmed finding** means something directly visible in the supplied source or its workflows. An **experiment-backed gap** means the small review experiment demonstrated the limitation. An **optimization candidate** is source-visible work that *might* cost time or memory but was not measured as a bottleneck. A **design proposal** is not implemented merely because it appears in this document. This is an assessment of the uploaded snapshot, not an assertion about any later commits.

## 1. Executive recommendation

Statelessness already has the correct conceptual center:

```text
Application-owned State + explicit Input
    -> Next State + ordered Outputs + Disposition
    -> state/transition/oracle checks
    -> optional exploration, evidence, replay, and diagnostics
```

Keep this architecture. Do **not** turn it into a required application runtime, a replacement for Rust's types and functions, or a mandatory single giant state machine.

The highest-value improvements are to:

1. **Make verification adequacy auditable.** A passing model should not conceal omitted events, requirements, or important scenarios.
2. **Enforce qualification continuously.** Integrate the existing specialized verification suite into ordinary development and release gates.
3. **Measure and reduce costs.** Target unnecessary cloning, allocations, encoding, and growing histories without weakening exact semantics.
4. **Make adoption easier.** Provide a practical production reducer → test host → properties → replay → live-observation path.
5. **Scale selected machine interactions.** Build on `Pair` with explicit, bounded multi-machine composition and global invariants.
6. **Improve evidence and diagnostic precision.** Preserve phases and provenance while keeping debug/metrics controls separate from application behavior.

### 1.1 Ranked work inventory

Effort denotes *relative implementation and qualification effort*, not calendar estimates. `P0` should be addressed before interpreting a broad green result as strong assurance. `P1` is the next product/architecture priority. `P2` can follow demonstrated demand.

| ID | Priority | Recommendation | Status of need | Expected value | Effort / risk |
|---|---|---|---|---|---|
| A01 | P0 | Assurance manifests, coverage goals, and honest completion reports | Experiment-backed limitation | Very high | M / medium |
| A02 | P0 | Application mutation/fault controls and specification review | Experiment-backed limitation | Very high | M / medium |
| A03 | P0 | Automatic specialized CI/release qualification | Confirmed workflow gap | Very high | M / low–medium |
| A04 | P1 | Reducer/composition copy and allocation optimizations | Optimization candidates | High | M–L / medium–high |
| A05 | P1 | Recording, encoding, and runtime overhead optimization | Optimization candidates | High | M–L / medium–high |
| A06 | P1 | Lifecycle history compaction and indexed obligations | Confirmed bound/scaling constraint | High | L / high |
| A07 | P1 | Practical integration path, ergonomic macros, and APIs | Adoption improvement | High | M / medium |
| A08 | P1 | Typed, bounded N-machine composition and global checks | Existing `Pair` scope limitation | High for complex apps | L / high |
| A09 | P1 | Trace v2/provenance and causal diagnostics | Confirmed trace-phase limitation | Medium–high | M–L / medium–high |
| A10 | P1 | Measurement, logging, effect metrics, and parity qualification | Improve existing capabilities | Medium–high | M / medium |
| A11 | P1 | Reproducible performance and scalability benchmark matrix | Measurement gap | High enabling value | M / low |
| A12 | P2 | More portable conformance profiles and native features | Confirmed capability gap | Medium–high | L / medium–high |
| A13 | P2 | Documentation, API/version compatibility, release clarity | Confirmed inconsistency | Medium | S–M / low |
| A14 | P2 | Safer evidence export, access control guidance, decoder hardening | Risk mitigation | Medium | M / medium |
| A15 | P2 | Agent-assisted verification workflow and independent requirements | Process/design proposal | Medium | S–M / low |
| A16 | Research | Sound, optional state-space reductions | Not currently established | Potentially high | L–XL / very high |

These items interrelate. For example, A04–A06 should be driven by A11 benchmarks, and A08 must not silently assume A16 is sound.

---

## 2. Existing foundation to preserve

The proposals below should augment, not accidentally recreate, facilities that exist in the uploaded source:

- Application-owned models and explicit ordered effect outputs (`src/model.rs`, `examples/application.rs`).
- State and **transition** checks with passed/failed/skipped statuses; errors are distinct from failed properties.
- Exact-equality bounded BFS, reproducible fuzzing, guided search, causal shrinking, and campaigns (`src/explore.rs`, `src/guided.rs`, `src/campaign.rs`).
- Independent oracle state, runtime `observe_transition` without reducer re-execution, and lifecycle obligations (`src/oracle.rs`, `src/lifecycle.rs`).
- Exact binary trace/replay, bounded runtime recording, separate text logging (`src/trace.rs`, `src/monitor.rs`).
- Optional modeling macros, input-domain descriptors, codecs, and inspection (`crates/statelessness-macros/`, `docs/MACROS.md`).
- Explicit two-machine composition and pending delivery queue (`src/composition.rs`).
- A headless debugger with stepping, breakpoints, watches, live control, probes, and structured effect timing/metrics (`crates/statelessness-debug/`, `docs/DEBUGGER*.md`).
- A portable specification with native Python, Go, TypeScript, and Swift implementations covering `core-v1`, `bfs-v1`, `rng-splitmix64-v1`, and `trace-json-v1` (`spec/README.md`, `conformance/`).

**Preservation rules:**

1. Application behavior must not depend on enabling a debugger, logger, or recorder.
2. A runtime observer must not rerun a completed reducer or replace the actual application's result when observation fails.
3. Search guidance may be approximate; verification state equality and exact replay must not be approximate.
4. Resource/time limits must remain visible. A truncated or incomplete search must never be called exhaustive.
5. Raw exact evidence and redacted diagnostic views must remain distinguishable.
6. The handwritten model API remains a supported alternative to macros.
7. Keep the Rust engine usable without taking on a new mandatory runtime or macro dependency.

---

## 3. A01 — Assurance manifests and behavioral coverage [P0]

### Problem

A model can exhaust its declared graph yet omit a real-world event, a payload class, or a property. “No failure found” and even “zero skipped checks” do not identify requirements never encoded in the model.

**Experiment-backed example:** A request reducer with missing cleanup passed when only final-state rules were checked. Adding a transition-output property found a three-input failure: `Start(1) → Start(2) → Complete(1)`. Excluding stale completions from the environment also hid the defect. Both insufficient properties and insufficient event domains can create false confidence.

### Proposed design

Add an **optional versioned assurance manifest**, independent from the application's implementation where practical. Its declaration should include:

- Stable expected property IDs and whether they are state or transition properties.
- Event/input families expected to be modeled; explicitly excluded families with rationale.
- Payload/domain bounds, supported generations, queue sizes, timer assumptions, failure classes, and admissible schedules.
- **Cover goals**: specific scenarios that must be reached, such as stale completion, duplicate message, cancellation during replacement, retry after failure, timeout with a pending obligation, and cross-machine handoff.
- Verification budgets and the required termination classification for a particular claim.
- Provenance of the manifest and model/property versions.

**Illustrative draft, not an existing file schema:**

```yaml
assurance_version: 1
model: download_controller
required_properties:
  - { id: ready_requires_active, phase: state }
  - { id: stale_completion_is_cleanup_only, phase: transition }
input_families:
  - { name: start, coverage: required }
  - { name: complete_pending, coverage: required }
  - { name: complete_stale, coverage: required }
  - { name: crash_recovery, coverage: excluded, reason: separate persistence model }
bounds:
  maximum_generations: 3
  maximum_pending: 3
cover_goals:
  - stale_completion_after_replacement
  - cancel_while_work_outstanding
  - duplicate_completion
required_search_outcome: exhausted_within_declared_bounds
```

The cover-goal mechanism should be an observation over actual explored transitions/history. It must not itself decide which transitions exist. Otherwise the search engine and the adequacy assessor can share the same mistake.

### Reporting contract

Report separate dimensions, not one “passed” Boolean:

- **Properties**: passed / failed / skipped / missing expected property / callback error.
- **Search**: exhausted within specified bounds / depth-limited / budget-limited / interrupted / timed out / failed.
- **Coverage**: observed required scenario / unreachable under declared model / not reached before stop / excluded by explicit policy.
- **Environment**: event and payload constraints included in this run.
- **Confidence**: only a scoped statement such as “the declared properties held for the exhausted finite model.”

A user-friendly green status should require an explicitly selected assurance policy, not merely a zero-failure count. Treat unmet reachability goals as failed **adequacy gates**, not as evidence of a violated application invariant.

### Implementation sequence

1. Define a machine-readable schema and stable IDs without changing `Model::step`.
2. Extract existing property metadata and input-domain descriptors where available; allow manual additions.
3. Add observation-only coverage hooks to explorers and campaigns.
4. Merge per-job coverage deterministically and preserve partial-run reporting.
5. Provide a CLI/library report format with unambiguous statuses.
6. Add differential tests covering unreachable, omitted, budget-limited, and satisfied cover goals.

### Acceptance criteria

- A missing required property is explicitly reported rather than silently ignored.
- A deliberately excluded event is listed as excluded; a forgotten event is not automatically certified as modeled.
- Reaching a terminal snapshot without exercising a required scenario cannot satisfy its cover goal.
- Search budget exhaustion leaves unobserved coverage **unknown/incomplete**, never passed.
- Existing models without manifests continue to work and have clear “adequacy not assessed” reporting.

**Relevant source:** `src/model.rs`, `src/explore.rs`, `src/guided.rs`, `src/campaign.rs`, `docs/MACROS.md`.

---

## 4. A02 — Mutation testing and independent requirement validation [P0]

### Problem

A property suite can be internally consistent yet too weak to detect realistic failures. Existing conformance mutation controls are a useful precedent, but application-specific fault sensitivity needs its own qualification.

### Proposed design

Add an opt-in **application fault-control harness** with small, named mutations to lifecycle examples and integration fixtures:

- Publish an obsolete generation.
- Omit a required cleanup/release output.
- Settle an obligation twice.
- Drop a cancellation acknowledgment.
- Accept a duplicate delivery as a new completion.
- Release a resource before its last dependent operation settles.
- Skip a retry deadline or mis-handle an ignored input.
- Break effect ordering across one important boundary.

For each mutation, record whether it is **killed** (a relevant property/adequacy failure is found), **survives**, or **could not be evaluated** (budget, unsuitable environment, error). Save a minimal replayable witness when killed.

The mutation corpus should be deliberately maintained by someone reviewing the required semantics—not generated solely from a model that might share the production reducer's assumptions.

### Additional safeguards for agent-authored code

- Keep an independently reviewed requirements file and property catalog.
- Require explicit review when a change removes or weakens an invariant, reduces an environment domain, alters a protocol version, or raises a bound to silence a failure.
- Retain original and minimized counterexamples as regression artifacts.
- Separate “reducer changed” from “specification changed” in PR reports.
- Prefer a second implementation/reviewer for a small independent oracle when a property is especially important.

### Acceptance criteria

- Seeded representative defects are detected with stable named properties and replayable counterexamples.
- The harness distinguishes a failure to find a witness from incomplete search.
- A build cannot quietly pass by removing the test or renaming the targeted property.
- Reports do not translate mutation survival into proof that the corresponding application is incorrect; they flag insufficient qualification.

**Relevant source:** `conformance/mutations.py`, `conformance/native_mutations.py`, `examples/application.rs`, `crates/macro-qualification/src/jobs.rs`.

---

## 5. A03 — Routine CI and release qualification [P0]

### Confirmed finding

In the uploaded snapshot, `.github/workflows/verify.yml` uses `workflow_dispatch` only. That workflow contains valuable multi-OS Rust, debugger, consumer, macro, and binding qualification. Other workflows already run tests automatically, so the problem is **incomplete automation of specialized qualification**, not an absence of CI.

### Proposal

Split qualification by purpose and cost:

**Pull request gates — affordable and repeatable**

- Rust formatting, clippy, locked tests, compile-fail macro tests, core examples.
- At least one representative feature-off build and a debugger instrumentation-parity test.
- Macro consumer and packaged-crate smoke tests on a primary target.
- Deterministic replay in a fresh process, including expected rejection of incompatible traces.
- Portable corpus/schema validation and a small cross-native profile matrix.
- Assurance-manifest validation and fault controls for the reference application.

**Nightly and release gates — deeper matrix**

- Linux/macOS/Windows and pinned MSRV plus current stable toolchain.
- Full macro/debugger/bindings and packaged-consumer matrices.
- Longer guided/fuzz campaigns with stable job IDs and archived seeds.
- Cross-language independently expected fixtures.
- Long-session recording and lifecycle retention tests.
- Release artifacts and compatibility checks against prior supported versions.

### Engineering details

- Pin compiler/tool and dependency versions where determinism matters.
- Upload machine-readable qualification evidence with source/build identity.
- Differentiate a property finding, environmental setup failure, flake, timeout, and skipped job.
- Ensure “diagnostics enabled” and “diagnostics disabled” produce identical application results and effect requests.
- Keep a fast local verification command that matches the PR's critical assertions.

### Acceptance criteria

- A normal PR automatically exercises the selected specialized gates.
- The full release matrix runs without relying on someone remembering a manual trigger.
- A deliberately broken decoder, macro consumer, or debugger parity fixture fails the appropriate gate.
- A green badge says which OS, compiler, features, and conformance profiles were actually tested.

**Relevant source:** `.github/workflows/verify.yml`, `.github/workflows/native-ports.yml`, `.github/workflows/portable-conformance.yml`, `.github/workflows/release.yml`, `scripts/check-debugger.py`, `scripts/check-macros.py`, `scripts/check-consumer.py`.

---

## 6. A04 — Reduce transition and composition allocations [P1]

### Source-visible optimization candidates

`src/composition.rs` currently clones the entire pair state in `Pair::step`, then replaces the selected child. Its transition checking clones addressed child outputs into a temporary `Vec`. The property check path also builds temporary check vectors and namespaced identities. Whether these are important in practice depends on state size and use frequency.

### Options to benchmark

1. **Selective child update.** Avoid cloning the selected child only to overwrite it. Preserve exact old-state/new-state ownership and failure semantics.
2. **Borrowed addressed-output inspection.** Where the child check interface requires `&[ChildOutput]`, consider an additive view/iterator contract to avoid constructing a cloned child-output vector. Do not change existing trait signatures without compatibility analysis.
3. **Reusable scratch buffers.** Reuse check/output adaptation buffers in explorers and observers when safe.
4. **Stable property prefix representation.** Reduce recurring `left.`, `right.`, or global property-ID formatting/allocation without changing identity or ordering.
5. **Efficient state representations.** Document `Arc`/copy-on-write or persistent-data-structure patterns for large immutable payloads while requiring correct deep semantic equality and isolation.
6. **Avoid eager materialization.** Preserve lazy domains and borrowed `TransitionRef`s where caller APIs permit them.

### Requirements and risks

- Avoid returning borrowed data whose backing storage no longer lives.
- Do not use shared interior-mutable state that causes cloning to disguise aliasing.
- Keep identical ordered outputs, dispositions, property IDs, check order, and replay bytes unless a codec version deliberately changes.
- Validate behavior under child failure, queue-bound failure, and ignored/rejected inputs.

### Acceptance criteria

- Differential tests show semantic parity of optimized and baseline transitions/checks.
- Microbenchmarks cover tiny state, large state, many outputs, and nested composition.
- A change is adopted only if it yields meaningful performance or material memory improvement without undue API complexity.

**Relevant source:** `src/composition.rs`, `src/model.rs`, `src/explore.rs`, `benches/engine.rs`, `benches/scaling.rs`.

---

## 7. A05 — Optimize trace recording and observation cost [P1]

### Source-visible optimization candidates

The recorder encodes the before-state for continuity and the post-state as evidence. It also retains a coherent suffix when evicting old data. Exact trace snapshots/freeze operations can clone retained evidence. These are reasonable correctness choices, but large states and frequent transitions may cause substantial encoding, allocation, and memory pressure.

### Proposed steps

- Measure time per transition attributable to the application reducer, state/transition checks, oracle advancement, encoding, logging, metrics, and debugger capture **separately**.
- Reuse encoder buffers, bounds checks, and scratch storage where safe.
- Evaluate memoized immutable state encoding only when lifetime, codec version, and actual state identity can be validated.
- Consider lossless checkpoint-plus-delta encodings or state sharing as an optional **new trace format**, with deterministic exact reconstruction and bounded decoder work.
- Keep the existing recorder checkpoint-advancement guarantee under eviction.
- Support explicit evidence policies such as full exact, limited exact window, and diagnostics-only. They must have different declared completeness semantics.
- Test retained bytes and process RSS independently; a recorder byte limit is not a process-memory cap.

### Non-negotiable constraints

Do **not** call a sampled, hash-only, outputs-omitted, or lossy trace “exact replay.” Do not accidentally serialize secrets into an allegedly redacted export. Callback allocations and blocking remain application-owned unless separately bounded.

### Acceptance criteria

- Recorded traces still reproduce exact outputs, state, dispositions, and check results for their retained window.
- Eviction retains an internally coherent starting checkpoint.
- Any new encoding has schema/version handling and corruption/size-limit tests.
- Baseline and optimized throughput, allocation count, p95/p99 pause, trace bytes per step, and RSS are captured.

**Relevant source:** `src/monitor.rs`, `src/trace.rs`, `src/value_codec.rs`, `benches/observation.rs`.

---

## 8. A06 — Bounded lifecycle history with sound retirement [P1]

### Confirmed constraint

The generic lifecycle monitor clones the history on advancement, scans vectors of obligations/resources, and retains completed keys. Once `max_history` is reached, verification becomes incomplete. Retention is not an incidental problem: old keys may be needed to detect late duplicate settlement or key reuse.

### Proposed design

1. **Typed scopes/epochs.** An operation may belong to an explicitly modeled scope with identity and lifecycle boundaries.
2. **Safe retirement conditions.** Remove old detailed entries only when the model establishes no future input can legitimately reference them. A drain acknowledgment, generation fence, or explicit bounded-delivery assumption may justify retirement; time alone usually does not.
3. **Optional indexed storage.** Use deterministic lookup structures to reduce scans while retaining canonical iteration/encoding order.
4. **Separate active obligations from archived summaries.** When retaining summaries, specify exactly which duplicate/stale errors can still be detected. If detection weakens, mark the verification scope as weakened/incomplete.
5. **History quotas as diagnostics.** Report active versus historical retention, oldest unresolved obligation, and why retirement is blocked.
6. **Structured failure details.** Include key, scope, generation, event kind, deadline, and prior observation, with stable property identities where appropriate.

### Hard test cases

- Completion arrives immediately before or after scope close.
- A key is reused in another generation.
- Duplicate publication appears after apparent settlement.
- Cancellation is acknowledged but the remote effect is still running.
- A very late event arrives after a model-declared delivery bound.
- Two machines disagree about ownership transfer.
- Historical state round-trips through the codec and participates in exact equality/replay.

### Acceptance criteria

- Under a declared safe-retirement policy, long-running verification has bounded historical memory.
- The optimized monitor catches every violation caught by the baseline within the declared equivalence scope.
- Without a sound retirement condition, the monitor remains bounded-and-incomplete rather than silently evicting evidence.
- Key lookup improves without changing canonical evidence or deterministic check order.

**Relevant source:** `src/lifecycle.rs`, `src/oracle.rs`.

---

## 9. A07 — Lower adoption friction; enhance macros without making a DSL [P1]

### Problem

An application author must decide where state ends and effects begin, expose input events, write checks, describe a domain, and connect the host. This cost is justified for difficult lifecycle behavior but can inhibit adoption when examples only show the component APIs separately.

### Improvements

**A. One complete reference integration**

Ship an end-to-end, production-shaped example containing:

```text
real async host
  -> application reducer (the single production implementation)
  -> effect executor
  -> completion/event delivery
  -> independent verification properties
  -> bounded environment model
  -> discovered failing trace
  -> causal shrink + fresh-process replay
  -> optional runtime recorder/debugger/metrics
```

Start with a request/download/job controller; add a TUI controller and a multi-machine service example afterward. Demonstrate when **not** to model a simple helper or large data buffer.

**B. More ergonomic registration**

- Keep `#[model]`, `input_domain!`, and derived codecs usable with normal structs/enums.
- Improve compile errors for missing callbacks, duplicate property IDs, unsupported signatures, unbounded domains, and incompatible codec versions.
- Add compile-fail qualification for supported/unsupported macro syntax and renamed crate imports.
- Consider a small, opt-in convenience façade for common lifecycle patterns and reusable check registration.
- Make it obvious how to progressively move from handwritten to macro-backed code without changing semantics.

**C. Parser maintenance decision**

The macro crate currently uses a custom Rust token parser with a documented syntax envelope. Either keep it intentionally narrow and well tested, or evaluate a conventional parser library **inside the optional companion**. Do not blindly widen the custom parser to every Rust construct. Preserving a dependency-free core does not require preserving a dependency-free optional procedural macro implementation at any maintenance cost.

**D. Host adapter recipes**

Document examples for Tokio-style services, UI/TUI loops, actor-message handlers, jobs, and external command workers, focusing on:

- Where effects live, and why they are executed **once**.
- Which inputs carry logical time/randomness/generation.
- How to use resource IDs instead of serializing live handles.
- How to attach an oracle at startup or from a saved history; no fabricated midstream history.
- How check, log, debug, recording, and effect telemetry errors should avoid corrupting application execution.

### Acceptance criteria

- A new consumer can run one documented example from real reducer to replay without inventing integration glue.
- Macro-generated and handwritten adapters yield identical modeled observations.
- No required macro import appears in the core's ordinary build path.
- The example includes both a detected fault and an explicit coverage limitation.

**Relevant source:** `examples/application.rs`, `examples/README.md`, `docs/MACROS.md`, `crates/statelessness-macros/src/parse.rs`, `crates/macro-qualification/`.

---

## 10. A08 — Explicit composition for more than two machines [P1]

### Existing starting point

`Pair` already supports two child models, addressed inputs/outputs, wiring, a pending queue, local checks, and global checks. It takes one selected child step or one explicit delivery at a time; it does not silently drain the queue. This is a good deterministic foundation.

### Proposed incremental architecture

Do not begin with a global magical scheduler. Add a typed composition layer *around* explicit child machines:

- `MachineId`/component identity and typed or safely tagged addresses.
- A bounded routing table from a child output to one or more destination messages.
- Explicit queue policies: FIFO per channel, ordered/unordered delivery where modeled, capacity handling, and backpressure.
- Environment inputs for delivery, drop, duplication, retry, cancellation, timeout, and external completion as appropriate.
- A clearly defined atomicity boundary: child reducer step, output routing, and selected delivery.
- Local, pairwise, and global invariants; no assumption that local correctness proves global correctness.
- Independent oracle history for cross-machine obligations such as handoff and ownership.
- Namespaced property IDs and reproducible trace addressing.
- Opt-in hierarchical views for developer ergonomics, without altering underlying transition semantics.

**Illustrative API idea only:**

```rust,ignore
let system = SystemBuilder::new()
    .machine("catalog", catalog_model)
    .machine("worker", worker_model)
    .route("catalog.scan_requested", "worker.scan")
    .bound_pending(32)
    .global_check("no_double_owner", check_ownership)
    .build()?;
```

This is not current Statelessness syntax. The final API should be driven by two or three representative real systems, not by this sketch.

### Scalability strategy

- Favor **focused verification slices**: compose only the machines involved in a specific interaction.
- Make environment assumptions and queue bounds explicit and reviewable.
- Use bounded, deterministic scheduling for exact exploration.
- Explore state canonicalization only when provably future-behavior preserving.
- Compare any proposed partial-order or symmetry reduction against unreduced small models before allowing it to support correctness claims.

### Acceptance criteria

- A 3+ machine fixture finds a cross-machine stale-event or double-ownership defect that individually checked machines do not expose.
- The same fixture can replay a trace with stable machine IDs and ordered routing effects.
- Queue overflow is a modeled, reported event or explicit error—not silent message loss.
- Bounds, assumptions, and global-property coverage appear in reports.
- Existing `Pair` API remains functional or has a documented compatibility migration.

**Relevant source:** `src/composition.rs`, `src/oracle.rs`, `src/explore.rs`, `crates/statelessness-debug/examples/debug_composition.rs`.

---

## 11. A09 — Better trace provenance and causal debugger views [P1]

### Confirmed limitation

The current binary trace v1 does not persist the state-versus-transition phase for every check. Debugger minimization consequently requires the caller to supply the target phase. Preserve v1; design a versioned successor rather than silently changing the old format.

### Proposed trace/evidence additions

- Explicit check phase (`state` / `transition`), stable ID, status, optional structured details, and observation sequence.
- Optional causal context: run/epoch, machine, generation, correlation ID, origin output index, resulting input/delivery, and logical deadline.
- Richer first-divergence reports: expected vs observed output order, disposition, first relevant state difference, and first failed/missing property.
- Summary of search environment, state/domain bounds, skipped/unreached requirements, build IDs, model/codec/property versions, and mutation controls.
- A documented **compatibility table** between v1, v2, portable JSON profiles, and debugger evidence.

### Debugger usability

Continue building on the existing headless protocol rather than requiring a TUI or editor:

- A causal timeline: input → transition → requested effect → admission/attempt → completion → delivery → settlement.
- Side-by-side state inspection and property explanations at an individual transition boundary.
- Named checkpoints, verified forks, watches, and filtered event streams.
- Optional local TUI/web/editor front ends that consume the same protocol.
- Clear separation between **semantic replay** and **live host control**: restoring a modeled snapshot does not roll back OS resources, databases, or network effects.

### Acceptance criteria

- A v2 trace reliably identifies whether a failing property was a state or transition check.
- A trace/replay mismatch reports exactly which semantic field diverged.
- v1 traces remain readable under an explicit compatibility policy.
- The viewer never requires running an effect merely to inspect evidence.
- Every diagnostic field has a clear redaction/privacy policy.

**Relevant source:** `src/trace.rs`, `src/monitor.rs`, `docs/DEBUGGER.md`, `crates/statelessness-debug/src/`.

---

## 12. A10 — Refine logging, metrics, and effect timing [P1]

### Important existing functionality

The debugger companion already contains host effect accounting, bounded metric aggregation, observation modes, request/attempt/delivery IDs, monotonic timing domains, retry and cancellation boundaries, pending-age diagnostics, instrumentation health, and pause provenance. These should be **qualified and made easier to consume**, not reimplemented.

### Remaining improvements

1. **Unified correlation.** Ensure effect/attempt/delivery identifiers connect to model transition IDs in diagnostics without changing model equality or canonical semantic traces.
2. **Default dashboards/reports.** Provide optional views for effect queue wait, attempt elapsed, delivery lag, processing, settlement, pending work, retries, cancellation acknowledgment, and unknown/incomplete samples.
3. **Mode comparison.** Benchmark and test off, counters-only, timed aggregates, retained detail, recording, debugging, and combinations. Metrics themselves must not impose unexplained hidden work.
4. **Diagnostic health.** Surface dropped events, bounded-queue overflow, callback faults, missing endpoints, lock-contention/destructor losses, and censored pending operations.
5. **Safety on observation failure.** Instrumentation errors must not suppress a real effect or completion; never put a fallible telemetry call in the host's success path such that it skips application work.
6. **Redaction and cardinality.** Keep high-cardinality identities in bounded detail, not aggregate metric labels. Never promote sensitive raw payloads to exported metric dimensions.
7. **Clock rigor.** Keep real monotonic time distinct from modeled logical time, record the clock domain/provenance, and avoid fabricating zero durations from unknown endpoints.

### Acceptance criteria

- Disabling all diagnostics preserves requested effects, results, and scheduling decisions under controlled tests.
- Existing effect-boundary timing tests run under the enhanced CI matrix.
- Unknown or partially observed metrics remain **unknown** rather than becoming zero or successful samples.
- A performance run can attribute a slowdown to reducer, checker, recorder, or metrics with reasonable confidence.

**Relevant source:** `docs/DEBUGGER_EFFECT_METRICS.md`, `crates/statelessness-debug/src/effects.rs`, `.../metrics.rs`, `.../diagnostic.rs`, `.../live.rs`, `benches/observation.rs`.

---

## 13. A11 — Build a reproducible performance evidence suite [P1]

### Problem

Optimization discussions are currently vulnerable to conflating architectural cost with a measured regression. “There is a clone here” is not proof that it dominates latency. Tests that show correctness do not automatically establish low overhead.

### Benchmark matrix

| Dimension | Scenarios |
|---|---|
| Execution mode | Normal reducer only; +state checks; +transition checks; +oracle; +recorder; +metrics; +debugger; combinations |
| State size | Tiny enum; medium controller; large nested/persistent structures |
| Output volume | 0, 1, many outputs; sizable effect metadata |
| Workload | Straight line; retry/cancel races; long-lived controller; multi-machine queues |
| Search | BFS nodes/edges/s; fuzz inputs/s; shrink work; guided campaign throughput |
| Retention | Short/long sessions, bounded exact window, varying history quotas |
| Environment | Linux primary; macOS and Windows where available; debug/release profiles |

Measure:

- `ns/transition`, allocations/transition, bytes allocated, CPU time, peak RSS.
- p50/p95/p99 wall time and pauses introduced by observation/encoding.
- Encoded bytes/step, full snapshot bytes, retained window behavior, decode/replay time.
- State/edge exploration speed, queue size, dedup collision/equality work, skipped/budgeted checks.
- Metric health counters and active-handle/unfinished-work behavior.

### Methodology

- Pin inputs, seeds, compiler, optimization flags, machine details, and runtime options.
- Use representative applications as well as microbenchmarks; microbenchmarks can mislead.
- Warm up and run enough repetitions to account for variance.
- Separate initial “baseline collection” from setting gates. Choose regression thresholds from measured noise, not arbitrary promises.
- Report relative speedups, confidence/variance, and absolute resource usage.
- Track correctness and serialized-evidence equivalence alongside performance.

### Acceptance criteria

- Each A04–A06 optimization cites before/after measurements and parity tests.
- Users can see expected overhead by operating mode, rather than one universal performance claim.
- Benchmarks are repeatable locally and a stable subset runs in scheduled CI.

**Relevant source:** `benches/engine.rs`, `benches/observation.rs`, `benches/scaling.rs`, `benches/campaign.rs`, `scripts/`.

---

## 14. A12 — Strengthen portable conformance and cross-language parity [P2]

### Confirmed current scope

The shared portable specification covers **four profiles**: `core-v1`, `bfs-v1`, `rng-splitmix64-v1`, and `trace-json-v1`. The native Python, Go, TypeScript, and Swift implementations pass those profiles in the source review's local validation. They do **not** currently promise full Rust parity for oracle composition, causal shrinking, guided search, campaigns, runtime ring capture, multi-machine helpers, or binary `.sttrace`.

### Recommendations

- Maintain **profile-specific** claims. Do not advertise “feature parity” without profile qualification.
- Extend only in useful increments; potential profiles include `oracle-v1`, `lifecycle-v1`, `shrink-v1`, `recorder-v1`, and `composition-v1`.
- Define canonical observable behavior, error categories, event ordering, model identity, checks, bounds, and replay semantics. Allow internal data structures and performance to differ.
- Use independently reviewed golden expectations, not just Rust output as the sole source of truth.
- Add application-level differential fixtures testing the *same defined behavior* with separately written reducers—not just one table interpreter implemented across languages.
- Include negative fixtures: malformed traces, incompatible identity/version, deadline/budget exhaustion, duplicate property IDs, deterministic RNG edge cases, and semantic mismatch.
- Treat Rust-backed C/Swift/Go/browser bindings separately from independently implemented native engines; they have different deployment and compatibility risks.
- Establish a changelog/deprecation policy for the spec and trace formats.

### Acceptance criteria

- Each native package states its exact qualified profiles.
- A newly advertised profile has independent expected fixtures and a mutation-sensitive negative suite.
- A missing/unsupported capability is explicit and cannot count as a pass.
- Required portable observations match; platform-specific allocation and timing are not falsely claimed equivalent.

**Relevant source:** `spec/README.md`, `PORTABLE.md`, `conformance/`, `python/`, `go/`, `typescript/`, `swift/`, `bindings/`.

---

## 15. A13 — Documentation, API semantics, and release clarity [P2]

### Confirmed issues

The uploaded root manifest reports version `0.2.0`, while the README's registry installation example selects version `0.1`. Some planning/status documents describe earlier implementation stages than the code now contains. This can cause users and agents to build the wrong assumptions about current functionality.

### Changes

1. **Single generated capability matrix.** For every crate/port, show implemented, experimental, planned, qualified, and released features separately.
2. **Version alignment.** Reconcile package, examples, documentation, trace schema, macro adapter protocol, and portable spec versions. Verify actual publication before making registry availability claims.
3. **Guarantee glossary.** Define exact vs guided search; exhausted vs bounded; skipped vs absent property; logical deadline vs real scheduler fairness; exact vs diagnostic trace; history scope and equality contract.
4. **Architecture guide.** Teach where modeled state belongs, what stays in the effect host, how to use strong Rust types, and how to avoid modeling everything.
5. **Recipes and anti-patterns.** Cover async controllers, UI/TUI event loops, workers, cancellation and stale results, multi-machine handoff, plus scenarios where a simple function is better.
6. **Upgrade/migration guide.** Describe supported model/property/codec/trace compatibility and how fresh-process replay should behave across builds.
7. **Release/readiness checklist.** Tie claims to actual CI and consumer qualification, not just self-reported documentation status.

### Acceptance criteria

- A reader can accurately distinguish native port capabilities, Rust core features, debugger features, and Rust-backed bindings.
- Installation instructions match an actually available release (or clearly instruct a local path install).
- Public documentation never equates finite verification with general application correctness.

**Relevant source:** `README.md`, `Cargo.toml`, `CHANGELOG.md`, `PLAN.md`, `SUPPORT.md`, `VALIDATION.md`, `RELEASING.md`, `docs/`.

---

## 16. A14 — Security, privacy, and hostile-input resilience [P2]

### Risks already identified by the architecture

- Exact traces may contain secret state/input/output payloads even if a diagnostic inspector redacts fields.
- Checksums and build stamps support integrity/reproducibility checks but are **not cryptographic authentication**.
- Debugging endpoints that can influence a live host create authorization and safety concerns.
- Malformed, compressed, or huge trace inputs require explicit limits to avoid excessive allocation or CPU work.

### Recommendations

- Provide a **separate safe export** schema that declares its redaction policy and is never represented as exact replay unless the exported representation really preserves all needed semantics.
- Add a trace sensitivity/classification guide, explicit consent for exporting raw evidence, and safe default filenames/permissions where appropriate.
- Harden decoder bounds, nested lengths, pathological counts, unknown versions, mismatched checksums, and resource use under malformed data.
- Treat any external debugger transport as a separately secured host responsibility, with a reference authentication/authorization pattern rather than implying local protocol messages are safe to expose on a network.
- Add fuzz/negative tests for readers, parsers, and diagnostic selectors. Treat application-defined inspection callbacks as untrusted with respect to allocation/time assumptions.
- Separate debug observation authority from live control authority whenever a consumer app exposes remote tooling.

### Acceptance criteria

- Sensitive fields do not leak into a declared redacted export.
- Exact traces retain explicit warnings about their potential raw contents.
- Reader tests reject malicious lengths and unsupported versions without unbounded resource growth.
- The project does not imply that hash-based checksums authenticate provenance.

**Relevant source:** `src/trace.rs`, `src/value_codec.rs`, `docs/DEBUGGER.md`, `docs/DEBUGGER-PROTOCOL.md`, `crates/statelessness-debug/`.

---

## 17. A15 — Make agent-written code easier to verify, not merely easier to generate [P2]

### Rationale

A predictable reducer/property architecture can help coding agents modify behavior within a clear boundary and reproduce failures. However, the same agent may write the production reducer, under-model the environment, and invent a property suite that approves its own mistake.

### Recommended workflow

```text
Human/independent requirement review
    -> bounded model/environment contract
    -> agent changes production reducer
    -> generated exploration + independent oracle
    -> mutation sensitivity test
    -> reproducible minimal failure or scoped green report
    -> reviewer approves changes to requirements separately
```

- Store stable property IDs and adequacy manifests in version control.
- In PRs, explicitly show changes to reducers, input domains, checks, codec versions, trace format, and bound assumptions.
- Automatically replay known counterexamples against every relevant implementation change.
- Require an explanation for changed assumptions or removed failing cases.
- Use second-pass review for missing temporal requirements and incorrect resource/host boundaries.
- Evaluate efficacy rather than assuming a productivity benefit: compare equivalent conventional and Statelessness implementations on escaped defects, review time, code size, test maintenance, and runtime overhead.

### Acceptance criteria

- A PR cannot silently weaken a property catalog or coverage target without making that change visible.
- Regressions recreate the smallest saved failing history across fresh builds/processes.
- Evidence reports are stable enough that a human can review what was proved, not merely see a green check.

---

## 18. A16 — Research: sound state-space reduction [later]

### Why this is attractive

A system of many machines can have an enormous product state space. Ordinary decomposition alone does not mathematically reduce all the relevant interleavings or obligation histories.

### Why this must wait

Incorrect deduplication, partial-order reduction, or approximate equality can hide real violations. Existing guided features are correctly used to prioritize search, **not** to alter the exact state identity that search/replay verifies.

### Research program

1. Build small canonical concurrent fixtures and enumerate the **unreduced** graph.
2. Identify independent transitions and spell out the equivalence conditions, including effects, transition properties, queue order, oracle history, and visible timing.
3. Add experimental reductions behind explicit opt-in switches.
4. Compare outcomes and counterexample reachability against unreduced exploration for each fixture.
5. Record assumptions and disable any reduction when those assumptions are not established.
6. Only then discuss supporting formal completeness claims under the reduction.

Potential ideas include proven commutativity classes, symmetry over truly interchangeable identities, and focused composition slices. None is a general replacement for correctness-preserving equality.

### Acceptance criteria

No state-space reduction is enabled by default on the strength of performance alone. Reduced and unreduced searches must agree on the required properties for qualified test classes, with a documented soundness argument and known limitations.

---

## 19. Cross-cutting architectural guidance

### 19.1 Use Statelessness selectively

**Good fits:** playback controllers, downloads and retries, task schedulers, multi-stage asynchronous requests, cancellation, actor coordination, ownership transfer, teardown, and UI state with competing background results.

**Usually poor fits:** pure computation, one-shot parsing, numerical kernels, straightforward CRUD transformations, and trivial linear `async` sequences without meaningful lifecycle interactions.

**Preferred hybrid:** ordinary Rust functions/types and async workers execute real work; a compact decision layer receives explicit events and emits effect requests. Model resource identity and relevant metadata, not necessarily sockets, decoder buffers, database handles, or OS state.

### 19.2 Types, tests, and models complement one another

- Rust types and ownership prevent structural/local invalid states.
- Unit and integration tests exercise concrete implementation behavior.
- Statelessness explores defined state/event histories and checks dynamic obligations.
- Loom or equivalent low-level concurrency tests explore synchronization beneath atomic model transitions.
- Persistence/crash tests, network fault injection, and end-to-end runs validate external reality.

Do not claim a model has verified a disk, network, clock, or scheduler behavior it did not represent and qualify.

### 19.3 State and oracle equality must preserve future behavior

Counters, logical time, generation keys, and historical obligations may be necessary for correct state identity. Ignoring them in equality can merge semantically distinct situations. Search novelty projections may be lossy; exact verification equality may not be lossy.

### 19.4 Bounded safety is not general liveness

A finite logical deadline can establish a local obligation violation when time advances in the model. It does not prove a real remote job eventually finishes. Treat fairness, eventual progress, crash persistence, and distributed delivery guarantees as separate scopes unless formalized.

---

## 20. Proposed milestone and PR sequence

The order below minimizes architectural risk, creates benchmarks before optimization, and avoids implementing an ambitious new composition DSL before establishing requirements. Effort depends heavily on desired API stability and the scope of release qualification.

### M0 — Lock the baseline and qualification contract

**Goal:** Establish a repeatable, source-bound baseline.

- PR M0.1: run and archive core/macro/debugger/native conformance in a unified manifest.
- PR M0.2: make representative specialized tests PR gates; configure nightly full matrix.
- PR M0.3: publish precise capability/version matrix and fix installation documentation.
- PR M0.4: add reproducible benchmark harness and baseline numbers by operating mode.

**Exit:** Deterministic fixtures, explicit platform/profile claims, and measured baseline overhead exist.

### M1 — Model adequacy and fault sensitivity

**Goal:** Make a passing search result less misleading.

- PR M1.1: optional property/input/assumption manifest with schema validation.
- PR M1.2: coverage-goal observers and honest incomplete/unreachable statuses.
- PR M1.3: application mutation suite for the reference lifecycle model.
- PR M1.4: CI report that combines checks, search bounds, coverage, and mutant outcomes.

**Exit:** The known stale-result and omitted-cleanup mutants are caught; omission of a required property or scenario produces an explicit failed/incomplete assurance gate.

### M2 — Fast and sustainable verification

**Goal:** Reduce measured cost without changing behavior.

- PR M2.1: profiling of composition copying, check adaptation, and namespacing.
- PR M2.2: semantics-preserving copy/allocation improvements that benchmarks justify.
- PR M2.3: encoding/recorder improvements retaining exact replay.
- PR M2.4: lifecycle indexing and a separately specified safe-retirement mechanism.
- PR M2.5: long-session and stress qualifications, including disabled diagnostics parity.

**Exit:** Every optimization has before/after resource figures, semantic differential tests, and a clear no-regression story. Long histories fail explicitly or compact under a sound declared policy.

### M3 — Better consumer integration

**Goal:** Make the best practices the easiest practices.

- PR M3.1: complete production-shaped async controller example and host adapter.
- PR M3.2: macro error/ergonomics improvements and compile-fail suite.
- PR M3.3: TUI/UI and background-job recipes.
- PR M3.4: agent-friendly requirement/property/domain diffs in CI.

**Exit:** A new user can reproduce a bug and replay its fix using a real reducer without writing a parallel fake implementation.

### M4 — Composition and evidence maturity

**Goal:** Verify interactions between selected components and explain failures.

- PR M4.1: N-machine composition requirements based on a real 3+ machine fixture.
- PR M4.2: explicit typed routing, queue bounds, and global checks.
- PR M4.3: cross-machine oracle/ownership fixture and replay qualification.
- PR M4.4: trace v2 phase/provenance design with backward-compatibility reader.
- PR M4.5: causal failure views, metrics integration, and safe diagnostic export.

**Exit:** A cross-machine defect is discoverable, reproducible, and explainable end-to-end; v1 evidence remains supported as declared.

### M5 — Cross-language and deeper research

**Goal:** Expand guarantees where a consumer needs them.

- PR M5.1: choose one next native profile with independent golden cases.
- PR M5.2: implement/qualify the profile across selected languages before generalizing.
- PR M5.3: long-term protocol compatibility and release migration tests.
- Research branch: exact-vs-reduced small-model differential experiments.

**Exit:** Native feature claims are profile-specific and independently qualified; new reductions remain opt-in until soundness is established.

### Suggested dependency map

```text
M0 CI + benchmarks
   ├──> M1 assurance + mutations
   ├──> M2 runtime performance/history
   └──> M3 examples + macro ergonomics
                  └──> M4 composition + traces
                           └──> M5 new portable profiles

A16 reduction research is separate; it does not gate the earlier milestones.
```

---

## 21. Definition of done for every implementation PR

For work on this framework, require the following where applicable:

- [ ] **Scope:** What guarantee, API, performance result, or developer problem changed?
- [ ] **Source-of-truth reducer:** Are tests exercising production decisions rather than a substitute implementation?
- [ ] **Semantic compatibility:** Are state/input/output/disposition/check results and ordering preserved or intentionally versioned?
- [ ] **Model adequacy:** Were assumptions, domains, required properties, or cover goals changed?
- [ ] **Failure contract:** Do callback error, property failure, skip, and budget/incomplete remain distinguishable?
- [ ] **Determinism:** Do seeds, ordering, identities, codecs, and fresh-process replay still behave reproducibly?
- [ ] **Limits:** Are memory, input sizes, queue capacities, cancellation, and timeout assumptions explicit?
- [ ] **Diagnostics parity:** Can logging/recording/debug metrics be disabled without changing application behavior?
- [ ] **Privacy:** Are raw evidence contents and redacted exports handled under their different contracts?
- [ ] **Tests:** Is there a positive fixture, an adversarial/mutation fixture, and a relevant regression test?
- [ ] **Benchmarks:** If a performance claim is made, are baseline, new numbers, hardware/toolchain, and variance recorded?
- [ ] **Compatibility:** Are spec, model, property, codec, macro, and trace versions addressed where relevant?
- [ ] **Documentation:** Are capability claims, examples, and migration guidance accurate for this snapshot/release?

---

## 22. Decisions to make before coding large features

| Decision | Suggested default | Why |
|---|---|---|
| Should assurance manifests be mandatory? | **Optional first; required only for a selected assurance grade** | Preserve lightweight adoption while preventing stronger claims without adequate metadata. |
| Should behavior coverage be inferred fully automatically? | **No** | Missing real-world requirements cannot be derived reliably from the model being checked. |
| Should macros replace handwritten interfaces? | **No** | Keeps the core language-independent conceptually and prevents accidental DSL lock-in. |
| Should the system own execution/effects? | **No** | Preserve host authority, effect-once semantics, and interoperability with existing runtimes. |
| Should `Pair` be deprecated immediately? | **No** | It remains useful as an understandable, already-working primitive. |
| Should fast search change equality? | **No** | Lossy novelty features must not become unsound state deduplication. |
| Should binary traces become lossy for size? | **No for exact mode** | Add a separate diagnostics-only policy instead. |
| Should lifecycle histories evict by age automatically? | **No** | Very late events and identity reuse make arbitrary eviction unsound. |
| Should logging and effect metrics be reimplemented? | **No** | Expand/qualify the existing debugger companion facilities. |
| Should native ports advertise all Rust features? | **No** | Portable conformance must remain profile-specific. |
| Should multi-machine composition launch with POR? | **No** | First establish exact semantics and qualify small unreduced graphs. |
| Should the framework model every function? | **No** | Reserve it for behavior where event history materially affects correctness. |

---

## 23. Source pointers and validation context

**Major uploaded-source entry points**

| Topic | Files |
|---|---|
| Contracts, checks, transition semantics | `src/model.rs`, `src/lib.rs` |
| Exploration and shrinking | `src/explore.rs`, `src/automatic.rs` |
| Guided search and campaigns | `src/guided.rs`, `src/campaign.rs` |
| Oracle and lifecycle history | `src/oracle.rs`, `src/lifecycle.rs` |
| Recording, traces, codecs | `src/monitor.rs`, `src/trace.rs`, `src/value_codec.rs` |
| Composition | `src/composition.rs` |
| Optional macros | `crates/statelessness-macros/`, `docs/MACROS.md` |
| Debugger, logging, timing, effect metrics | `crates/statelessness-debug/`, `docs/DEBUGGER.md`, `docs/DEBUGGER_EFFECT_METRICS.md` |
| Portable spec/native ports | `spec/README.md`, `PORTABLE.md`, `conformance/`, `python/`, `go/`, `typescript/`, `swift/` |
| CI and release | `.github/workflows/`, `RELEASING.md` |
| Benchmarks | `benches/` |
| Production-style application starter | `examples/application.rs`, `examples/README.md` |

**Review validation:** The earlier review ran the native Python, Go, TypeScript, and Swift suites (27, 32, 30, and 32 tests respectively), the shared portable conformance suite, 352 fresh-process replays, and application-review experiments. It did **not** freshly compile the Rust core, macros, or debugger because no usable Rust toolchain was available and a download attempt failed. Therefore Rust recommendations are source-based rather than fresh compiled performance/correctness results. The detailed review/evidence is in `statelessness_review.md` and `statelessness_review_bundle.zip` from the same conversation.

**Review snapshot SHA-256:** `0a6742f26e2fa04403b81ab7aed1c40e7bc88e5ad47f4c6a1622e06e51390519`.

---

## 24. Final recommended order

**Do now:** A03 CI baseline + A11 benchmarks, then A01 assurance coverage + A02 fault sensitivity. These provide the foundation for knowing whether subsequent changes actually improve correctness or performance.

**Do next:** A04/A05 measured performance improvements, A06 sound lifecycle retention, and A07 integration/macro ergonomics. This makes the existing engine easier to use and less costly without growing its conceptual surface dramatically.

**Then expand:** A08 multi-machine composition, A09 causal trace upgrades, and A10 improved diagnostic consumption; release/version clarity and security hardening should be addressed continuously.

**Only after demand and qualification:** A12 additional cross-language profiles and A16 state-space reductions.

The guiding principle for every milestone is the same: **make important real application decisions explicit, make omissions visible, preserve reproducibility, and never overstate what a bounded model has actually checked.**

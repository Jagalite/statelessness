# Native language parity plan

Bring the independent Python, Go, TypeScript and Swift libraries to behavioral
feature parity with the Rust implementation while preserving ordinary native
installation and application-owned types. Each engine must execute its own
algorithms. Calling Rust through ABI v2 does not close an independent-engine gap.

This is an implementation and qualification plan, not a claim that parity exists.
Baseline: Rust source commit `0876717bee85e335449c2c99ada41f6c6e6112d1`, inspected
on 2026-10-20. The target is this source snapshot, including the optional debugger
and modeling conveniences, rather than the older published 0.2.0 archives.
Future Rust additions enter the same gap ledger before parity claims advance.
Track subsequent implementation in [native parity status](NATIVE-PARITY-STATUS.md);
the existing-baseline section below describes the pinned starting point.

## Product commitments

- Python remains usable without a native library or Rust toolchain.
- Go remains usable with `CGO_ENABLED=0` and normal Go cross-compilation.
- TypeScript remains a native JavaScript engine without Wasm initialization or
  Node imports in the browser-compatible library entry point.
- Swift remains a source SwiftPM library without a Rust binary dependency.
- Preserve the current zero-third-party-runtime-dependency contract. Optional
  development tools, code generation and integration packages stay separate.
- Retain ABI 1 and ABI v2 as separate alternatives. This plan neither retires
  them nor changes application defaults or publishes packages.
- Provide equivalent guarantees through idiomatic APIs. Rust ownership syntax,
  procedural macros and compiler diagnostics need language-specific equivalents.

## Existing baseline

All four independent engines implement `core-v1`, `bfs-v1`,
`rng-splitmix64-v1` and `trace-json-v1`: core checks and observed transitions,
finite BFS, deterministic random arithmetic, and JSON observation trace replay.
These are foundations to extend and requalify, not completed full-Rust parity.
Python and TypeScript additionally implement candidate `paired-v1` oracle and
projection APIs. Go and Swift do not implement that candidate; promotion remains
separate from implementation.

The current main commit has passing native-port and portable-conformance CI:
[native packages](https://github.com/Jagalite/statelessness/actions/runs/38941645728)
and [portable conformance](https://github.com/Jagalite/statelessness/actions/runs/38941645890).
Native packages cover Linux/macOS/Windows, with Swift excluded on Windows.
Python conformance covers Linux 3.10/3.13 and macOS/Windows 3.13. These runs qualify
their existing scope, not the extensions below.

See [capabilities](CAPABILITIES.md), [portable entry points](../PORTABLE.md),
[portable contract](../spec/README.md), [conformance kit](../conformance/README.md)
and [native qualification](../spec/NATIVE-PORTS-QUALIFICATION.md).

## What closing a gap means

Every work item below applies to all four languages. Unless explicitly described
as existing above, treat it as open until its public API, implementation and
qualification evidence have been inspected. Do not infer absence solely from a
README, or completion from an exported symbol.

A gap closes for one language only when it has:

1. A reviewed semantic contract and mapping to the pinned Rust public behavior.
2. An idiomatic public API implemented by that language's engine.
3. Independent expected results, shared conformance and language-specific tests.
4. Installed-consumer execution on each claimed platform, with exact source,
   package, toolchain and corpus identities and preserved logs.
5. Documentation, examples, compatibility decisions and measured resource costs.

Use per-language states `not_started`, `specified`, `implemented`, `qualified`
and `released`. Record blockers and receipt paths separately. All four must reach
`qualified` for a capability to be called native-parity complete. Publication
requires a separate release decision. An accepted exception is a visible remaining
difference, not a completed parity cell.

## Gap inventory and acceptance criteria

### N01 Core execution and limits

Audit every public model, execution, observer and search operation, including
initial/state/transition phases, ordered append-only checks, skip versus absence,
accepted/rejected/ignored dispositions, lazy input errors, empty domains, restored
state validation, cloning, equality/hash collisions, cancellation and all limits.
Extend the portable contracts for Rust behavior outside today's four profiles.

Close when every baseline operation has a mapped native API and boundary tests;
callback errors never become property passes; incomplete searches never report
exhaustion; observation of an already executed transition never executes it again.
Count retained engine data separately from callback allocations and process RSS.

Sources: [model](../src/model.rs), [execution](../src/execution.rs),
[exploration](../src/explore.rs), [observation](../src/observation.rs).

### N02 Oracles and paired conformance

Finish and review the Python/TypeScript candidate; implement Go/Swift equivalents
of independent oracle history, `WithOracle`, projected paired state, ordered
outputs and full disposition comparison. Include history in state cloning,
equality, hashing and codecs. Keep property failure distinct from divergence.

Close with all five independent engines passing independently authored positive
and negative cases, including history-only differences and first-divergence
reports. Promote the profile only after its contract and runtime evidence pass.

Source: [oracle](../src/oracle.rs), [paired candidate](../spec/paired-v1.md).

### N03 Generation and fuzzing

Implement explicit generators, finite-domain automatic generation, bounded domain
membership, seeded runs, run/step budgets, cancellation and structured reports.
Specify seed derivation, RNG consumption, rejection sampling, empty domains and
callback error behavior so reproducibility is deliberate.

Close with exact random vectors and reference runs for a standardized generator;
zero/one/max budgets; failing and exhausted domains; snapshot isolation; and
reproduction from retained seed/configuration. User-defined generators need their
own compatibility identities; matching seeds alone cannot ensure matching runs.

Sources: [automatic](../src/automatic.rs), [exploration](../src/explore.rs).

### N04 Shrinking and failure preservation

Implement sequence and input shrinking, explicit attempt/step limits, cancellation,
operation reports, and the baseline failure-selection rules. Preserve the target
property and check phase; do not substitute an unrelated failure or callback error.

Close with original and reduced witnesses replayed in fresh processes, adversarial
shrinkers, no-progress candidates, interrupted runs, and paired/oracle failures.
State the achieved reduction criterion; bounded shrinking is not a claim of global
minimality. Specify candidate order where exact cross-engine witnesses are required.

Source: [shrinking implementation](../src/explore.rs).

### N05 Guided exploration

Implement feature feedback, bounded feature buffers, novelty accounting, corpus
admission/retention and guided mutation/generation. Version feedback identity and
make tie-breaking and corpus order explicit.

Close with deterministic fixture reports, feature collisions/duplicates, full
corpora, failing feedback callbacks, cancellation and retained witness replay.
Feature novelty must not be reported as exhaustive state coverage.

Source: [guided search](../src/guided.rs).

### N06 Campaigns and campaign lifecycle

Implement job seed derivation, ordinary/guided jobs, bounded scheduling, cancellation,
result delivery and acknowledgement, failure handling, lifecycle state and
structured aggregate reports. Native worker mechanisms may differ; define which
results are deterministic and which timing/order observations are host-dependent.

Close with one-worker and multi-worker tests, delayed/out-of-order completions,
worker failures, duplicate delivery, sink rejection, cancellation, lifecycle codec
roundtrips and deterministic merges. Test model factory isolation; no shared
mutable model may leak across jobs. Port the campaign self-model as a real fixture.

Sources: [campaign](../src/campaign.rs),
[campaign lifecycle](../src/campaign/lifecycle.rs).

### N07 Runtime observation and bounded recording

Extend basic observed checks with observer/logging options, snapshot policies,
periodic checking and bounded ring recording. Preserve checkpoints, absolute
sequence numbers, evicted-prefix counts and retained-suffix replay. Keep effect
execution application-owned and disabled instrumentation behaviorally inert.

Close with long sessions exceeding retention limits, zero/small capacities,
callback/codec/sink failures, checkpoint boundaries and fresh-process suffix replay.
An omitted check stays skipped; an evicted prefix stays outside the replay claim.

Sources: [monitor](../src/monitor.rs), [observation](../src/observation.rs).

### N08 Composition and network scheduling

Implement two-model composition and explicit multi-machine networks, stable IDs,
ordered routing, child/global checks, FIFO and unordered channels, and modeled
local/deliver/drop/duplicate actions. Include queue and routing state in snapshots,
identity, generators and traces.

Close with finite two- and three-machine fixtures, ownership handoff, stale messages,
queue saturation, bounded cycles and exact failure replay. Overflow must report
incomplete evidence. Simulation must not silently execute effects or drain queues.

Sources: [composition](../src/composition.rs), [network](../src/network.rs).

### N09 Lifecycle and bounded history

Implement legacy lifecycle monitoring and indexed retention with epochs,
generations, terminal/released entries, explicit fences and stale-reference
rejection. Preserve compatibility with historical lifecycle encodings.

Close with restart/codec tests, identifier reuse, delayed completions, cancellation,
differential legacy/indexed fixtures and long-session retention tests. Never infer
safe retirement from age, cancellation, or a real-time timeout alone.

Sources: [lifecycle](../src/lifecycle.rs), [indexed history](../src/lifecycle/indexed.rs).

### N10 Assurance and coverage

Implement manifests, property phases, applicability, required observations, input
families, cover goals, provenance, completion requirements and report aggregation
across BFS, fuzzing, guided search and campaigns.

Close with absent/skipped/failed/ambiguous checks, incomplete traversal, missing
coverage, report delivery failures and incompatible merge identities. Port a
production-shaped controller and named fault controls; detect weakened properties
using an independently reviewed requirement catalog. Zero failures alone cannot
satisfy an assurance claim.

Sources: [assurance](../src/assurance.rs), [reference model](../src/reference.rs),
[assurance guide](ASSURANCE.md).

### N11 Binary traces and causal evidence

Implement Rust binary trace v1 read/write and the v2 evidence envelope, bounded
canonical value codecs, phase-aware evidence, provenance and causal output links.
Keep existing JSON traces readable. Define exact supported scalar/container
encodings and reject unsupported values instead of coercing them.

Close with all 25 ordered producer/consumer pairs across Rust and four native
engines, fresh-process replay, historical fixtures, truncation/corruption/oversize
cases, identity mismatches and canonical encoding checks. Byte-for-byte equality
applies to formats that require canonical bytes. Interoperability requires the
same application model, codec and property identities; it is not arbitrary object
serialization. V1 cannot manufacture phase or causal data it never recorded.

Sources: [trace](../src/trace.rs), [value codecs](../src/value_codec.rs),
[evidence](../src/evidence.rs), [format guide](EVIDENCE.md).

### N12 Debugger and diagnostics

Provide optional native packages for simulation sessions, exact replay and verified
forks, bounded differences, structural inspection, watches/probes, stdio protocol,
revision/authority checks, cooperative live safe points, effect timing, metrics,
passive reports and offline phase-aware evidence minimization.

Close with protocol compatibility tests, stale requests, authority violations,
replay/fork isolation, probe errors, bounded output, live pause/resume fixtures and
v1/v2 offline inspection. Diagnostic redaction stays separate from exact evidence;
unknown durations, loss and omitted history remain visible. Optional debugger
packages must not burden basic engine imports.

Source: [debugger](../crates/statelessness-debug/src/lib.rs),
[debugger guide](DEBUGGER.md), [reports](DIAGNOSTIC_REPORTS.md).

### N13 Modeling helpers and integration

Provide native equivalents of model/domain/codec/inspection derives, default
initial state, bounded generation, property metadata, lazy checks and useful
validation errors. Prefer Python decorators/dataclasses, Go typed builders or
optional generation, TypeScript typed builders, and Swift protocols/builders;
use additional compiler plugins only when separately justified.

Close with independently installed application examples, invalid-definition tests,
codec compatibility fixtures and inspection examples. Cover UI-style reducers,
background jobs and async effect hosts using the same reducer in production and
verification. Keep nondeterministic host work outside synchronous simulation.

Sources: [modeling](../src/modeling.rs), [macros](MACROS.md),
[integration](INTEGRATION.md), [examples](../examples/README.md).

### N14 Research and tooling coverage

Inventory public research, reference/demo, conformance and CLI surfaces alongside
production modules. Port the finite exact-versus-projected research harness with
its known unsound counterexample as explicitly experimental tooling. Provide native
fixture runners, qualification commands and equivalent runnable teaching examples.

Close when every exported Rust baseline symbol is mapped to a work item, a native
equivalent, or a documented Rust-specific mechanism with an equivalent outcome.
No silently excluded module may hide behind a broad feature-parity claim. General
liveness, general partial-order reduction, a TUI and LLDB/GDB integration are not
requirements of this baseline because Rust does not establish them either.

Sources: [public modules](../src/lib.rs), [research](../src/research.rs),
[research limits](REDUCTION_RESEARCH.md).

## Language-specific obligations

### Python

Preserve deepcopy/snapshot isolation and exact integer/Unicode behavior. Keep
exceptions distinct from property findings. Test mutable aliases, custom copy
hooks, generator failures, interpreter-supported versions and worker process
startup. Do not rely on threads to deliver CPU parallelism; specify campaign worker
factories and serialization constraints. Build and install wheel and source archive
in clean consumers without Rust or an implicitly available checkout.

### Go

Retain explicit clone hooks for slices/maps/pointers and exact full-width arithmetic.
Use context cancellation at defined cooperative boundaries; contain recoverable
callback panics without claiming recovery from data races or process termination.
Run race detection and vet alongside `CGO_ENABLED=0` tests. Publishable module layout
and `go/vX.Y.Z` tags must resolve from an external consumer without local replace
rules. Test worker ownership and sink backpressure.

### TypeScript

Retain BigInt arithmetic and owned Uint8Array snapshots, including Node Buffer
alias tests. Preserve exact keys, class-aware clone policies and exception handling.
Keep browser imports free of Node dependencies. Specify cooperative worker/campaign
integration without changing synchronous reducer semantics. Test exported types and
installed ESM exports in Node and actual Chromium, Firefox and WebKit browser
runtimes; any unqualified browser remains explicitly unsupported.

### Swift

Preserve exact UTF-8 identity through `ExactString`, including BOM and canonical
Unicode equivalents. Test reference-containing value types and throwing callbacks;
traps remain process failures. Define Sendable/actor boundaries for optional worker
and live-debugger APIs without imposing unsafe sharing. Qualify SwiftPM consumers
on Linux/macOS; add separately executed iOS simulator and device integration gates
before claiming mobile support. Other Apple targets and Windows remain distinct
platform expansion items, not implied by package declarations.

## Delivery sequence

Deliver vertical slices across all four languages so one implementation does not
accumulate an unreviewed semantic lead. Each slice includes contract, algorithms,
public APIs, tests, installed examples and receipts. Assigned implementer and
independent reviewer are required before a slice starts; no owner is assigned by
this planning document.

1. **M0 Inventory and contracts:** expand N01/N14 into a symbol-level ledger;
   pin source and current behavior; add profile/capability negotiation and receipt
   fields. Exit: no unmapped public baseline surface and reviewed compatibility rules.
2. **M1 Search and witnesses:** N02–N04 and N13 essentials. Exit: all languages
   discover, reduce, record and replay oracle/property failures independently.
3. **M2 Runtime evidence:** N07/N11. Exit: bounded production observation and
   binary/causal interchange pass cross-engine and hostile-input gates.
4. **M3 Stateful systems:** N08/N09 plus N13 application examples. Exit: composed
   and network/lifecycle failures reproduce with preserved history and ordering.
5. **M4 Campaigns and assurance:** N05/N06/N10, building on M1–M3. Exit: guided,
   parallel and cancelled campaigns produce qualified reports and replayable faults.
6. **M5 Inspection and tooling:** N12 and remaining N13/N14. Exit: optional native
   debugger/diagnostic and research tooling passes its behavioral contracts.
7. **M6 Distribution and release:** complete platform and performance gates below,
   public documentation and migration notes. Exit: release candidates are qualified
   per language; publish only under a separate authorized release action.

Packaging, performance measurements and platform checks begin with M0 and run in
every slice. M6 consolidates them; it must not discover basic deployment constraints
for the first time. Estimate effort after the M0 ledger and one complete vertical
slice; do not equate four ports with four identical implementation tasks.

## Shared qualification design

Extend the existing harness with independently reviewed normative examples and
profile-specific vectors. Keep expected outputs independent of engine execution;
Rust is a differential reference, not the sole source of truth. Add finite graph
and metamorphic cases, malformed input with recovery, snapshot/ownership stress,
error injection and deliberate mutations in each actual engine implementation.
Crashes, compile failures and timeouts do not count as successful mutation detection.

Separate exact semantic comparisons from implementation choices. Require exact
ordered observations, compatible artifacts, failure identities and deterministic
contract fields. Compare invariant outcomes for explicitly nondeterministic worker
completion order. Specify deterministic fixture scheduling when testing exact
campaign traces. Never silently relax assertions to make engines agree.

Every receipt records source commit/dirty status, package hashes, runtime/compiler,
OS/architecture, profile versions, corpus hashes, executed commands and results.
Track compile-only, skipped, failed and executed-pass states separately. Run Rust
compilation through Kyoto under the active execution policy; a queue rejection or
compile-only result cannot be replaced with an undocumented runtime pass.

## Performance and distribution gates

Measure cheap transitions, large states, expensive checks, wide/deep BFS, fuzz and
shrink loops, guidance, campaigns, long-lived rings, codecs, composition and debug
instrumentation. Compare each native engine with its previous version and the
pinned Rust baseline on equivalent models, bounds and machines. Record repeated
samples, throughput, latency distributions where meaningful, peak RSS, retained
bytes and language-appropriate allocation evidence. Include warmup/JIT/GC policy.

Approve workload-specific budgets before acceptance. Semantic parity does not
require equal speed, but performance differences must be measured and documented.
Do not trade state equality, observations or replay guarantees for a benchmark gain.

Required desktop qualification targets are Linux/macOS/Windows for Python, Go and
Node TypeScript, and Linux/macOS for Swift. State actual architectures and runtime
versions; passing one host does not qualify every architecture. Browser and mobile
claims require the additional language-specific gates above. Freeze supported
runtime floors per release and test both the floor and selected current versions.

Deliver clean-consumer Python wheel/sdist, npm tarball/declarations, Go module and
SwiftPM source checks. Verify no Rust engine is loaded or required, no checkout-only
imports leak, and optional tools stay optional. Retain prior-version artifacts for
upgrade/replay tests. Version package APIs, portable profiles and wire formats
independently; document intentional incompatibilities with migration examples.

## Final completion checklist

- [ ] M0 symbol ledger covers the pinned core, macros, debugger and tooling.
- [ ] N01–N14 are qualified separately for Python, Go, TypeScript and Swift.
- [ ] All new profiles have reviewed specifications and declared support.
- [ ] Cross-engine traces and historical replay pass with matching model identities.
- [ ] Real application reducers, fault controls and independent requirements pass.
- [ ] Installed consumers pass on every advertised platform/runtime combination.
- [ ] Performance/resource receipts meet approved budgets or expose an unresolved gap.
- [ ] Documentation states remaining platform differences and compatibility limits.
- [ ] Release candidates have reproducible package identities and migration notes.

A milestone can complete before publication. Full native parity cannot be declared
while a language is missing a required capability or its qualification. Any later
scope reduction must name the remaining gap explicitly and revise the parity claim.

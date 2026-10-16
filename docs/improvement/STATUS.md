# Improvement roadmap implementation

The [supplied roadmap](PLAN.md) is preserved verbatim. Its SHA-256 is
`3dad2d872a83a66905dce42592cd625175b2b490c747dfc91b792eb8307e2543`.
Implementation starts from origin commit
`0949e7992f1be34d2052b22f12b7cbb0ce6b7a95`; the earlier local debugger
implementation is not part of this work.

The checkout now contains implementation work across A01–A16 and M0–M5.
**This is an implementation ledger, not a declaration that every roadmap
acceptance criterion has passed.** In particular, A04/A05 contain measurement
infrastructure and benchmark-only candidates, with no adopted unmeasured
optimization. After the ABI v2 merge, the [merge qualification
receipt](../../validation/improvement/2026-10-16-merge/README.md) records executed
Rust runtime tests, reference assurance, mutation kills with fresh-process replay,
live replay and all macro diagnostics. Measured performance and independent human
requirements review remain separate open gates.

## A01–A16 source implementation matrix

| Item | Implemented source and reviewable artifacts | Remaining qualification or explicit limit |
| --- | --- | --- |
| A01 — Assurance and coverage | [Assurance API](../../src/assurance.rs), observation hooks in [explorers](../../src/explore.rs), [guided search](../../src/guided.rs) and [campaigns](../../src/campaign.rs); [schema](../../spec/assurance-v1.schema.json), [regressions](../../tests/assurance.rs), [guide](../ASSURANCE.md). Required checks must occur in every completed applicable batch; omissions, skips, ambiguity, coverage, completion, provenance and delivery errors remain separate facts. | Rust regressions and finite reference assurance must execute before their results are claimed. A manifest cannot establish callback independence or prove that the real environment was fully modeled. |
| A02 — Fault sensitivity | [Reference reducer/properties](../../src/reference.rs), [independent catalog](../requirements/controller.json), [property list](../requirements/controller-properties.txt), [retained seeds](../requirements/controller-witnesses.txt), [mutation evidence](../../src/reference/evidence.rs), [fresh-process gate](../../scripts/check_reference_mutations.py). Nine named fault controls retain target-specific original/shrunk witness paths. | Controls and regression cases are authored; no local Rust mutation kill, replay or minimality result is asserted. Catalog remains awaiting independent human review. |
| A03 — Qualification | [Unified qualifier](../../scripts/qualify.py), [source provenance helper](../../scripts/qualification_context.py), [schema validation](../../scripts/check_schemas.py), PR/nightly/release gates in [verify](../../.github/workflows/verify.yml), [native ports](../../.github/workflows/native-ports.yml), [portable conformance](../../.github/workflows/portable-conformance.yml) and [release](../../.github/workflows/release.yml). | Configured gates are not completed hosted runs. New current-source runtime and consumer/OS/toolchain matrix receipts are required. Compile-only reports remain incomplete. |
| A04 — Transition/composition cost | [Roadmap benchmark](../../benches/roadmap.rs) and [benchmark support](../../benches/support/mod.rs) cover large owned/shared states, Pair queues, output volume, checks and search. Candidate traces have explicit parity assertions. | **Candidate/framework only.** Production Pair behavior is unchanged. No ownership-sharing optimization is adopted without executed parity and source-bound before/after measurements. |
| A05 — Recorder/observation cost | [Engine](../../benches/engine.rs), [observation](../../benches/observation.rs), [roadmap](../../benches/roadmap.rs) and [debugger diagnostics](../../crates/statelessness-debug/benches/diagnostics.rs) fixtures cover checking modes, encoding, replay, recorder windows and disabled diagnostics. [Measurement contract](../benchmarks.md). | **Candidate/framework only.** No measured checkpoint cadence, recorder speedup, allocation reduction or default behavior change is claimed. Runtime parity, allocation probes and distributions remain unexecuted locally. |
| A06 — Lifecycle retention | [Indexed lifecycle](../../src/lifecycle/indexed.rs), [differential/retirement regressions](../../tests/lifecycle_indexed.rs), [protocol](LIFECYCLE_AND_NETWORK.md). Explicit epoch/generation identity, ordered indexes, terminal/released-entry validation, monotonic fence watermark, stale-reference rejection and bounded structured diagnostics. | A fence is a model-declared delivery assumption, never inferred from age or cancellation. Legacy History/codec remain unchanged. Rust differential/restart/long-session execution and performance measurements remain pending. |
| A07 — Consumer integration/macros | [Production-shaped controller](../../examples/controller.rs), [effect host](../../src/reference/host.rs), [integration recipes](../INTEGRATION.md), macro [adapter](../../crates/statelessness-macros/src/adapter.rs), [documentation](../MACROS.md) and [qualification driver](../../scripts/check-macros.py). Adds explicit default-initial-state/bounded-generation options, typed property metadata and focused diagnostics. | Live worker execution, packaged consumers, compile-fail outcomes and disabled-instrumentation parity need their exact receipts. The host example does not promise durable exactly-once effects or unbounded fairness. |
| A08 — Multi-machine composition | [Network API](../../src/network.rs), [three-machine handoff/oracle fixture](../../tests/network.rs), [contract](LIFECYCLE_AND_NETWORK.md). Stable machine IDs, typed model families, bounded ordered routing, FIFO-per-channel/unordered delivery, explicit local/deliver/drop/duplicate inputs and child/global checks. | Queue overflow is explicit incomplete evidence; application backpressure is a modeled protocol. No hidden queue drain or production reduction. Fault detection, finite safe-graph traversal and exact replay tests await Rust execution. |
| A09 — Causal evidence | [V2 envelope/recorder](../../src/evidence.rs), [offline viewer](../../crates/statelessness-debug/src/evidence.rs), [regressions](../../tests/evidence_v2.rs), [format/compatibility guide](../EVIDENCE.md). Stores check phases, run/epoch, absolute checkpoint sequence, machine/generation/correlation and originating-output links around preserved v1 observations. | Authored compatibility and phase/replay checks are not runtime-qualified locally. V1 readers remain supported; legacy unrecorded phase/causal data is not fabricated. |
| A10 — Diagnostic reporting | [Passive dashboard and causal joins](../../crates/statelessness-debug/src/dashboard.rs), [dashboard tests](../../crates/statelessness-debug/tests/evidence_dashboard.rs), [report guide](../DIAGNOSTIC_REPORTS.md). Bounded payload-free reports preserve unknown durations, health losses, partial gauges, histogram bounds and truncation. | Runtime instrumentation parity and performance need execution. Host labels are assertions, observation endpoints are not promised atomic, and diagnostic reports are not exact replay evidence. |
| A11 — Performance evidence | [Benchmark runner](../../scripts/benchmark.py), [receipt/parser tests](../../scripts/test_benchmark.py), core/debugger suites and [benchmark guide](../benchmarks.md). Repeated samples, configuration/source identity, separate allocation probes, optional Kyoto process metrics, compatible-baseline/noise gates. | No new measured baseline, threshold qualification, speedup or per-operation RSS/tail-latency claim. Compile-only output has no measurements; process-level metrics are not attributed to individual cases. |
| A12 — Portable increments | [paired-v1 candidate contract](../../spec/paired-v1.md), [schema](../../spec/paired-v1.schema.json), [hand-authored goldens](../../conformance/paired-v1.tsv), [Rust fixture](../../tests/paired_profile.rs), native [Python](../../python/src/statelessness/oracle.py) and [TypeScript](../../typescript/src/oracle.ts) adapters/tests. Independently represented queue reducers cover state/effect/disposition mutations and explicit bounds. | Candidate remains outside qualified `PROFILES`; exposed separately as experimental. Rust runtime parity and independent specification review remain open. Go/Swift do not implement the candidate; the four established profile claims are unchanged. |
| A13 — Documentation/version clarity | Generated [capability matrix](../CAPABILITIES.md), [installation guidance](../../README.md), [assurance](../ASSURANCE.md), [integration](../INTEGRATION.md), [evidence migration](../EVIDENCE.md), [release guide](../../RELEASING.md) and this ledger. | Published 0.2.0 archives are an older source snapshot; same-version checkout APIs are not automatically registry APIs. No new package publication or installed-consumer success is asserted. |
| A14 — Hostile-input/privacy boundaries | [Bounded v2 decoder and diagnostic export](../../src/evidence.rs), [negative tests](../../tests/evidence_v2.rs), [classification/export/transport guidance](../EVIDENCE.md). Unknown versions, lengths, checksums, phases, causal links and quotas fail explicitly; private create-new raw files and payload-omitting nonreplayable exports are separate operations. | New Rust adversarial tests await execution. CRC/build identity is not authentication; remote debugger authorization and I/O deadlines remain host responsibilities. |
| A15 — Independent requirement review | [Requirement diff gate](../../scripts/review_requirements.py), [tests](../../scripts/test_reference_requirements.py), separate catalog/phase declarations/witness corpus and [workflow](../../.github/workflows/verify.yml). Input-derived controller progress predicates catch omitted state changes plus effects; weakened requirements require a source-bound external review receipt. | The tool validates receipt scope, not reviewer identity. The agent does not issue its own human-review approval; initial catalog validation is not independent review. |
| A16 — Finite reduction research | [Opt-in research harness](../../src/research.rs), [sound/unsound finite fixtures](../../tests/research.rs), [research contract](../REDUCTION_RESEARCH.md). Compares exact and projected reachability plus ordered inputs/effects/disposition/checks/successor classes, retaining counterexamples and explicit limits. | Disabled by default and never enables production pruning. Authored experiments await Rust execution. Agreement qualifies only the closed declared fixture, not general symmetry/POR, fairness or unencoded history. |

## M0–M5 milestone ledger

The roadmap's PR numbers below are work-item identifiers, not created pull
requests or claims that a milestone exit has passed.

| Milestone | Source work now present | Exit gates still open |
| --- | --- | --- |
| M0.1–M0.4 — Qualification foundation | Unified source-bound runner, specialized CI gates, version/capability matrix, reproducible measurement harness and receipt schemas. | Current-source hosted/runtime matrix and measured overhead baseline. Historical M0 compilation applies only to its recorded source fingerprint. |
| M1.1–M1.4 — Adequacy and fault sensitivity | Typed manifest/schema, per-batch property accounting, observational coverage, deterministic campaign merge, independent controller requirements, seeded controls, witness/replay and combined qualification stages. | Executed finite assurance, mutation kills/replays, and independent human requirements review. |
| M2.1–M2.5 — Sustainable verification | Profiling/allocator/parity fixtures, benchmark-only sharing/window candidates, indexed lifecycle with explicit retirement contract, long-history and diagnostics-mode regression targets. | Measurement-driven optimization selection, before/after resource figures, runtime semantic parity and long-session/stress qualification. M2.2/M2.3 are not claims of adopted performance improvements. |
| M3.1–M3.4 — Consumer integration | Production-shaped reference reducer shared with the worker host, macro options/diagnostics/catalogs, UI/TUI/background/async recipes, independent requirement/domain/corpus diff gate. | Live integration, packaged/compile-fail consumers, and independent review evidence for the exact source. |
| M4.1–M4.5 — Composition/evidence | Three-machine ownership fixture, typed bounded scheduler/routing, global oracle, v2 phase/provenance envelope with v1 reader, offline causal failure views, passive reports and separate safe export. | Executed cross-machine discovery/replay, v1/v2 migration/failure-phase qualification, hostile-input and diagnostics parity runs. |
| M5.1–M5.3 and research — Portable increments | Scoped paired-v1 candidate in Rust/Python/TypeScript with independent goldens, compatibility/negative tests, explicit version policy, opt-in exact-versus-projected finite experiments. | Rust/cross-language runtime qualification, candidate specification review, release migration matrix and broader reduction soundness research. Native Go/Swift candidate parity is not claimed. |

## Allowed local completion commands

The user requires all Rust builds/tests through
`/Volumes/seed1/Projects/kyoto/kyoto.py` and selected **keep policy; compile only**.
Do not add shared policy rules or execute Rust test, example, replay, mutation or
benchmark binaries. Python tooling and native Python/TypeScript tests are separate
permitted checks; they do not qualify Rust runtime behavior.

From the repository root, the final selected toolchain is **Rust 1.90.0**, with a
separate target directory and incremental compilation disabled. The dedicated
Python environment must be provisioned before invoking its interpreter; its
existence alone is not a successful dependency or qualification receipt.

```sh
export CARGO_TARGET_DIR="$PWD/target/roadmap-build"
export CARGO_INCREMENTAL=0
STATELESS_PYTHON="$PWD/target/qualification-venv-roadmap/bin/python"

"$STATELESS_PYTHON" scripts/qualify.py \
  --profile full --compile-only --toolchain 1.90.0 --allow-dirty --timeout 7200 \
  --kyoto /Volumes/seed1/Projects/kyoto/kyoto.py \
  --evidence target/qualification/roadmap-compile-final.json

CARGO_TARGET_DIR="$PWD/target/roadmap-bench-build" \
"$STATELESS_PYTHON" scripts/benchmark.py \
  --compile-only --toolchain 1.90.0 --command-timeout 3600 \
  --kyoto /Volumes/seed1/Projects/kyoto/kyoto.py \
  --output target/qualification/roadmap-bench-final
```

The qualifier and benchmark driver explicitly resolve the chosen rustup compiler,
set `RUSTC`/`RUSTDOC`, and prepend its directory to PATH. Merely setting
`RUSTUP_TOOLCHAIN` is insufficient when a standalone Homebrew compiler precedes
rustup's shims. Each visible Cargo compilation is submitted separately to Kyoto.
Test and benchmark compilation uses `--no-run`; runtime-only stages are retained
as `not_run`. A queue rejection never falls back to direct Rust execution.

The additional macro consumer/negative-fixture compile pass uses the same
explicit compiler environment (the macro driver has no `--toolchain` option):

```sh
STATELESS_RUST_BIN="$(dirname "$(rustup which --toolchain 1.90.0 rustc)")"
export PATH="$STATELESS_RUST_BIN:$PATH"
export RUSTC="$STATELESS_RUST_BIN/rustc"
export RUSTDOC="$STATELESS_RUST_BIN/rustdoc"
export RUSTUP_TOOLCHAIN=1.90.0

"$STATELESS_PYTHON" scripts/check-macros.py \
  --compile-only --allow-dirty --timeout 7200 \
  --kyoto /Volumes/seed1/Projects/kyoto/kyoto.py \
  --evidence target/qualification/roadmap-macros-final.json
```

Do not reuse an existing receipt/artifact destination: choose a new run suffix
when a previous attempt already created it. Queue wait counts toward stage
timeouts. Cancellation is scoped to owned jobs; a request to cancel is not proof
that the worker stopped. `qualify.py --list` describes the selected plan without
executing it. Compile-fail fixtures check expected diagnostics and never run the
compiled Rust application.

## Evidence and completion boundaries

The final receipt targets above are **provisional until the coordinator completes
and inspects them**: `target/qualification/roadmap-compile-final.json`,
`target/qualification/roadmap-bench-final/report.json`, and the separate
`target/qualification/roadmap-macros-final.json`. A path listed here is not a passing
result. Retain each actual report with its sibling raw logs/artifacts and verify
its source-stability flag, command results, compiler environment and skipped work.

The [historical M0 receipt](../../validation/improvement/2026-10-12/README.md)
records successful selected compilation/tooling checks for source fingerprint
`e409554051a07ac59b8b6124aa9f773d3666e1ab925308856f1f0cf32bbfb6cb`.
Its Python test counts, Clippy/Rustdoc results and two-suite benchmark compilation
must **not** be transferred to the later assurance, reference, network, lifecycle,
v2 evidence, native-candidate or expanded benchmark source. Earlier interrupted
or default-compiler attempts are likewise not substituted for final evidence.

| Report observation | Permitted conclusion |
| --- | --- |
| `compiled`, or benchmark `status: compiled_only` with empty measurements | Targets compiled; Rust assertions, mutation behavior and parity prerequisites did not execute. |
| `selected_checks_passed: true` with compile-only `status: incomplete` | The selected allowed compile/tooling checks passed for the recorded source; runtime qualification remains incomplete. |
| `not_run`, `setup_error`, `timed_out`, `interrupted`, `blocked` or missing receipt | No passing evidence for that stage. Later progress does not rewrite the earlier result. |
| Actual Python/TypeScript test receipt | Only the executed native/tooling implementation and recorded source are covered; no Rust parity or profile promotion follows automatically. |
| Runtime `passed: true` and complete current-source receipt in an execution-authorized environment | The named tests/profiles passed within their recorded bounds and platform; finite testing is not general application correctness. |
| Independent review receipt or hosted result | Qualifies only its exact approved requirements/source and observed matrix cell; no inferred human approval, publication or unexecuted platform success. |

No milestone is marked acceptance-complete merely because its APIs and tests are
written. Complete the authorized compilation and retain its receipts now; leave
runtime, measurement, independent-review, hosted and release claims explicitly
unqualified until their own evidence exists.

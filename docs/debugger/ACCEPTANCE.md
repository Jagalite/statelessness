# Debugger v1.1 implementation and acceptance map

## Scope and source identity

Baseline: `f3e00e79471b928d67ddb530c113d6d51af6f277`. Reviewed engine baseline:
`cb7f256e4d7cf20cfab6018b9bbba391ec2dd9de` (the intervening changes are design docs).
The authoritative repository v1.1 has SHA-256
`4557a92bc9689da26accf22b651cf0ca70f3e508242296c6f99c820a8dad8877`.
It expands the supplied Library v1.1 with M3a/M3b; both are included.
[Contract reconciliation](CONTRACTS.md) identifies the original input hash.

This is the optional synchronous Rust companion. It does not turn the core engine
into an async runtime, dynamically load Rust models, execute effects during replay,
or install global collectors. The exact v1 trace format and existing Model bounds
remain unchanged.

## Recorded local outcome

After the [independent pre-PR review](REVIEW.md), source commit
`5ef7f6a555f15641ed22361b915507cc444615ff` passed all **29** reproducible
qualification stages on **both Rust 1.95.0 and the declared Rust 1.90.0 MSRV**,
with clean initial trees and unchanged source hashes. Each run includes:

- **499 workspace tests**, **242 compiled-out tests**, four example tests and
  three packaged-consumer tests
- Strict all-target/all-feature Clippy, warnings-denied Rustdoc and formatting
- Enabled/disabled normalized semantic parity, 32 inspection compile-fail fixtures,
  existing macro consumers, hostile real OS pipes, fresh-process exact workflows
- Cold-offline checks, an actual packaged external consumer, and all three
  synthetic benchmark families

A fresh exported source archive without Git metadata passed all **26**
non-benchmark stages. It reports unknown Git provenance, rather than borrowing an
unrelated enclosing repository's identity; its source hashes exactly match both
native qualification runs. Six additional gates passed for the original starter,
clean packaging without `--allow-dirty`, readiness checks, and macro replay.

Rust 1.90 compile-only checks also pass for Windows GNU (`x86_64-pc-windows-gnu`),
macOS (`x86_64-apple-darwin`) and Wasm (`wasm32-unknown-unknown`). These targets
were **not linked or executed natively**. Remote CI, native LLDB/GDB integration,
external applications and external collectors remain **unrun**. The existing
manual CI matrix now includes the debugger runner; it was not dispatched here.

Evidence index:

- Current Rust 1.95: `validation/debugger/qualification.json`
- MSRV: `validation/debugger/msrv/qualification.json`
- Exported archive: `validation/debugger/source-archive/qualification.json`
- Cross-target compilation: `validation/debugger/cross-targets/qualification.json`
- Additional compatibility gates: `validation/debugger/broader/qualification.json`

All five reports bind the same source files. `validation/debugger/artifacts.json`
binds the evidence files by SHA-256. The delivery commit adds documentation/results
only to the tested source. Earlier results are historical, including the preserved
first run in `validation/debugger/historical/initial-qualification/`. Review
reports retain resolved findings and original failing assertions. Synthetic timing
runs used a shared cloud host, with other qualification work active; they are not
portable/compiler-comparison benchmarks or production safety/overhead guarantees.

## Milestones and executable gates

| Gate | Delivered surfaces | Qualification evidence |
|---|---|---|
| M0 | Contracts, capability/error separation, sealed checked observations, unchanged core defaults | Original `m0-baseline.json`; full workspace and existing macro regression gates; cold empty-cache core check and dependency tree |
| M1 | Initial/turn observations, one-input sessions, handwritten linked harness, pre/post breakpoints, admission/work limits, old-observer adapter | `tests/session.rs`, core `tests/checked_replay.rs`/replay tests, codec-free `debug_counter`, interactive/fresh request fixture |
| M2 | Immutable browse cursor, bounded exact verification, first-divergence comparison, verified coherent forks with protected provenance, saved regression and bounded minimization | `tests/workbench.rs`, `tests/review_debugger.rs`, core `tests/review_replay_bounds.rs`; separate-process record/verify/compare/view/fork |
| M3 | Versioned read-only Inspect, structural paths, exact integer types, paging/work limits, redaction/source metadata, optional derives, watch lifecycles | `tests/inspection.rs`, `inspect_derive.rs`, `watches.rs`; 32 negative derive fixtures and cold offline renamed consumer; handwritten/generated parity |
| M3a | Scoped lazy probes/markers, typed scope selectors, property/value/change/threshold filtering, producer-acknowledged revisions, text/JSONL/custom sinks, independent queues/health, bound sidecars | `tests/diagnostic.rs`, `diagnostic_selectors.rs`, `diagnostic_controller_model.rs`, `review_debugger.rs`; enabled/compiled-out semantic parity and watch/probe overhead |
| M3b | Host-only request/attempt/delivery identities, checked local clocks, cancellation/censoring, bounded metric catalog/gauges/histograms/snapshot windows, typed completed-slow selection | `tests/effects_metrics.rs`, `effect_ledger_model.rs`, destructor unit tests; actual Future polling smoke, source-bound release overhead and metric-schema tests |
| M4 | Versioned bounded stdio, separate authority and revisions, epoch/request dedup, snapshot/candidate handles, explicit raw trace chunks, remote allowlisted probe profiles/watches and metric/effect queries | `tests/protocol.rs`; real OS-pipe client with retry/stale/escaping/reconnect/export scenarios; explicit no-control read-only tests |
| M5 | Checked actual-host observation, coherent mid-run checkpoint, host sequence gaps/eviction/freeze, separate dispatch facts, integrated effect timing | `tests/live.rs`, `examples/live_host.rs`; no reducer/effect execution by observation; metric capture independent of log/trace delivery |
| M6 | Opt-in safe-point pause/step, one-turn permits, dispatch phases, bounded arrival ownership policy, detach/reconnect/injection authority | Live controller finite state exploration plus `live_host` integration; duplicate/stale/overflow/disconnect safety cases |

The filenames in this table are relative to `crates/statelessness-debug/` unless
explicitly described as core. `oracle_lifecycle.rs` additionally qualifies missing
settlement/resource ownership, exactly-once oracle/reducer advancement with
recording/inspection/breakpoints/replay, and retained composite-history branches.
The composition harness makes queued deliveries visible and never auto-drains.

## Reproduce and inspect the evidence

With Rust 1.95 or the qualified Rust 1.90 minimum:

```sh
python3 scripts/check-debugger.py
```

The script writes `validation/debugger/qualification.json` and per-command logs.
It records the actual source hashes, source commit, toolchain/platform, dirty-tree
status, source stability during the run, every command/exit status and whether
benchmarks ran. An all-pass claim requires `passed: true`, including unchanged
source hashes. Evidence-only commits after that run do not change those source
hashes. Earlier `m0-baseline.json` and `m1-m2.json` are historical checkpoints,
not substitutes for the final full qualification.

The suite compares normalized state/input/output/disposition/check observations
across default and compiled-out diagnostic builds, with capture disabled, enabled,
and queue-saturated. It intentionally excludes build fingerprints and runtime
session identities from semantic comparison. Reducer/check/probe counters guard
against replaying work to instrument it.

Finite independent controller models cover:

- Live control: 44,100 states / 732,924 edges across 24 declared configurations
- Subscription revisions: 32,989 states / 571,991 edges for two producers, two
  capture revisions, three boundaries and bounded command classes
- Effect-accounting ledger: 390 states / 6,024 edges

These establish the specified invariants only within the explicit finite models.
The subscription fixture reconstructs and compares actual hub outcomes against an
independent reference at each edge. They are not proofs of arbitrary host code,
OS scheduling or unlimited interleavings.

## Qualified limitations and optional integrations

- Linux/x86_64 native tests on Rust 1.95 and 1.90. Windows/macOS/Wasm are
  compile-checked only; their runtime behavior, native LLDB/GDB, production
  schedulers and external telemetry collectors are not qualified
- Core and companion are external-dependency-free. Optional TUI, `tracing`,
  `metrics` facade/OpenTelemetry exporters, native debugger launch helpers and
  network transports are not installed; custom typed sinks are available
- Callbacks, codecs, iterators, synchronous I/O and app allocations are cooperative;
  debugger budgets do not hard-preempt them or cap total process memory
- Inspect derives support documented Rust shapes. Recursive derive bounds,
  inactive-variant admission, wildcards, retained watch baselines across re-enable
  and wall-clock TTL are not claimed; sequence TTL/exact selectors are supported
- Live control requires a cooperating host and explicit authorities. No arbitrary
  restoration of sockets/resources or live state; no forceful process suspension
- Sequential retries are qualified. Hedged/overlapping critical-path analysis,
  automatic stuck-work sweeps, exemplar reservoirs and automatic OS pause detection
  are not claimed. Completed-slow queries read existing measurements only
- Histograms have declared fixed buckets and approximate quantiles. Missing,
  censored, small-population, partial, reset and dropped data remain labeled
- Runtime-disabled effect handles still have measured host-token allocations;
  full detail has materially higher linear-search overhead. Synthetic closed-loop
  microbenchmarks are not production latency or SLA guarantees
- Raw traces require separate explicit export authority and may contain secrets.
  Display redaction does not make raw exact evidence safe to share. Sidecar FNV
  binding detects accidental mismatches and is not cryptographic authentication

See [inspection](../DEBUGGER-INSPECTION.md), [protocol](../DEBUGGER-PROTOCOL.md),
[live integration](../DEBUGGER-LIVE.md) and [metrics](../DEBUGGER_EFFECT_METRICS.md)
for supported APIs, host responsibilities and detailed limits.

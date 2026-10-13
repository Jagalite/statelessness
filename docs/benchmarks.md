# Reproducible benchmark evidence and optimization gates

The benchmark driver collects warmed raw samples, actual source/build identity,
workload dimensions, and separate allocator probes. It includes the original
small request fixtures and a roadmap matrix. These are executable experiments;
this change supplies **no measured baseline or demonstrated speedup**. Rust
benchmark execution remains unrun under the user's compile-only policy.

## Compile and retain evidence

Every Rust command can be routed directly through Kyoto:

```sh
python3 scripts/benchmark.py \
  --kyoto /Volumes/seed1/Projects/kyoto/kyoto.py \
  --compile-only --toolchain 1.90.0
```

The driver submits visible `cargo bench --no-run` commands. It does not execute
benchmark binaries or change shared policy. The result is `compiled_only` with
an empty measurements list. Toolchain selection pins child `RUSTC`/`RUSTDOC` and
PATH using `rustup which`, preventing a non-shim compiler from silently taking
precedence. Compilation is not evidence that benchmark parity assertions passed.

The core suites are `engine`, `observation`, and `roadmap`. The debugger's
`diagnostics` benchmark is compiled separately with and without
`compiled-out-diagnostics`; select them as `--suite diagnostics_enabled` and
`--suite diagnostics_compiled_out`. Feature variants use separate explicit Cargo
commands so an enabled build is never mistaken for the compiled-out variant.
All suites are selected by default. `--profile dev` selects an explicit
unoptimized comparison; the default is Cargo's `bench` profile.

When execution is permitted in another environment, omit `--compile-only`.
Current local authorization is compilation only. A Kyoto policy rejection stops
the driver; it never retries through an opaque wrapper. Each command has a
30-minute queue/execution deadline (`--command-timeout`). Submissions get unique
owner IDs; interruption/cancellation targets only that owned job.

Evidence goes to a new `artifacts/benchmarks/` directory or a new `--output`
path. Existing directories are refused. Retain `report.json` and every raw log.

## Workload matrix

| Suite | Cases | Measured scope |
| --- | --- | --- |
| engine | Direct reducer, state-only checks, full checks | One fixed small request transition; sampled-check skips remain explicit. |
| engine | Three-turn recording, trace encoding, exact replay | Includes per-operation setup and checks; encoding counts whole traces. |
| observation | Borrowed event; off/info/debug/trace sink formatting | Prepared transition/checks, separate from reducer cost. |
| observation | Preformatted buffered file writes | OS-cached writes; final flush/fsync excluded. |
| roadmap | Reference controller reducer versus full checks | Eight-turn retry/time/cancel/acknowledgment/success/duplicate cycle; actual application reducer, with exact checked preflight outside sampling. |
| roadmap | Owned versus shared 32 KiB states | Local candidate representation; exact canonical trace parity asserted before measurement. |
| roadmap | Pair with owned/shared children and eight pending messages | Two 32 KiB children, four outputs, explicit addressed execution; exact Pair trace parity prerequisite. |
| roadmap | One versus 64 outputs | Output-vector volume with a zero-byte payload. |
| roadmap | Oracle with zero/256 retained obligations | Real `WithOracle` plus lifecycle advancement and checking, including retained-history clone/scan cost. |
| roadmap | Recorder windows of one/16 turns | 64 checked transitions including ring eviction and exact encoding. |
| roadmap | 32 KiB state encoding | Canonical byte generation. |
| roadmap | BFS, fuzz, guided fuzz | Finite 64-state graph; 128 BFS edges or 64 sampled transitions; seed 42. |
| roadmap | Three-machine network | One explicit child turn, three shared 32 KiB states, ordered outputs. |
| diagnostics variants | Disabled/counters/timings/detail effects | Full request/attempt/publication/delivery lifecycle; detail retention bounded. |
| diagnostics variants | Runtime-disabled/enabled probe | Macro expressions assert zero evaluations when disabled/compiled out. |
| diagnostics variants | Cooperative controller, live recorder | Real permits and sealed turn observations; effects remain host-owned. |

The feature flag removes diagnostic probe expressions; it does **not** compile
away direct calls to effect-observer APIs. The report distinguishes those axes.
Metrics and probe workloads use stable bounded retention and may measure steady
state eviction after warmup. Setup is outside timing except when a case
explicitly measures full recording/search setup.

Default sampling is seven batches, 20,000 iterations, 100 warmup operations.
Engine record/encode/replay cases use `iterations / 10 + 1`; roadmap/debugger
cases use `iterations / 100 + 1` because operations contain larger workflows.
Raw JSONL includes units and actual per-case iterations. Environment controls
remain `STATELESS_BENCH_FORMAT`, `STATELESS_BENCH_ITERS`,
`STATELESS_BENCH_SAMPLES`, and `STATELESS_BENCH_WARMUP`.

## Timing, allocations, CPU and memory

Each batch retains elapsed nanoseconds, normalized ns/operation and ns/unit,
mean, sample standard deviation, coefficient of variation, and interpolated
p50/p95/p99. Percentiles describe **batch means**, not individual-operation tail
latencies or pauses. Samples in one process are correlated; repeat full commands
for fresh-process and host-noise assessment.

Roadmap/debugger cases then perform a **separate untimed 32-operation allocator
probe**. It records successful allocations, reallocations, and requested bytes
(the full new request size for reallocations). These are allocator traffic,
not live bytes, RSS, or allocator-reserved memory. Atomic accounting is disabled
during timed samples. Instrumented cases must have non-timed probe evidence and
nonempty workload metadata or the driver rejects the log.

Kyoto CPU seconds, sampled process-tree peak RSS, `wait`-reported maximum RSS,
and RSS sample counts are retained when supplied by the queue. They cover the
**whole final Cargo attempt**, including build/startup/warmup/timed and allocator
passes; they cannot be attributed to an individual case. Deferred attempts are
not silently summed. Missing data is `null`/unavailable. Without Kyoto this
driver does not invent process CPU/RSS measurements. Queue elapsed time includes
waiting/build work and is never used as benchmark execution time.

Reports hash actual source/build inputs, including dirty/new files; record Git,
compiler/Cargo identities, manifests/lockfile, compiler/profile environment,
user Cargo configuration, and available OS/CPU/memory information; and check that
inputs stayed unchanged until completion. The host's thermal state, governor,
and competing applications are not controlled or inferred.

## Optional regression comparison

Compare completed measurements against a retained baseline:

```sh
python3 scripts/benchmark.py --baseline /path/to/prior/report.json \
  --max-regression-percent 10 --max-cv 0.10
```

Use the same queue/toolchain/profile and workload arguments as the baseline.
Comparison rejects compile-only/incomplete/unstable-source receipts, missing
cases/compiler identities, changed benchmark fixtures or Cargo contracts,
different sampling/workload/environment settings, or either run exceeding the
selected coefficient-of-variation limit. Summaries are recomputed from raw
samples; a supplied derived median cannot bypass the gate. Implementation source
may change between baseline and candidate, with both exact identities retained.

The optional threshold compares case median ratios. It is an empirical gate on
this matched environment, not a statistical significance test or production
performance guarantee. A noise/compatibility rejection fails the command rather
than being translated into a passed regression gate. Pick thresholds from
repeated representative measurements; the example values are configurable.

A04/A05 candidate promotion requires independent exact state/effect/check/replay
parity, stable source-bound before/after measurements for relevant workloads,
and reviewed tradeoffs. Within-run owned/shared and history/window comparisons
are reported with **no automatic promotion decision**. The sharing candidate
exists only in benchmark fixtures; no unmeasured production optimization is
introduced by this suite.

## Remaining qualification boundary

Existing `scaling` and `campaign` benches retain larger state/check/shrinker,
worker-count, generator and input-signature experiments in their original CSV
formats. Their results are not mixed into this driver's JSON schema. Networked
applications, foreign-runtime overhead, cross-platform distributions, and real
operation tail latency need separate runs. No runtime benchmark, Rust parity
assertion, or measured baseline from this new matrix has been executed locally
under the current compile-only authorization.

Python receipt/parser/gate tests do not execute Rust:

```sh
python3 -m unittest discover -s scripts -p 'test_benchmark.py'
```

# Host effect accounting and bounded metrics (M3b)

This implementation follows sections 14–18 of
`Statelessness-Rust-Debugger-Design-v1.1.md` from baseline `main@f3e00e7`
(SHA-256 `4557a92bc9689da26accf22b651cf0ca70f3e508242296c6f99c820a8dad8877`).
It adds no dependency to the default engine, changes no model trait or exact
trace encoding, and never executes an effect, input, reducer, or application
clock. The host owns every execution boundary.

## Capture and authority

`effects::EffectObserver` is an explicit cloneable capability. Configure
`EffectOptions` before admitting work. Counters, timing, and retained detail
have separate controls. No log queue is involved in aggregation. A metric-only
observer retains no per-effect history. Runtime-disabled and counters-only
observers make zero observation-clock calls, including construction and metric
snapshot queries. Explicit tokens still have allocation/locking cost; the
benchmark reports it rather than claiming zero overhead.

The host provides `RequestOrigin { run, epoch, machine,
transition_sequence, output_index }`. A request token is allocated before the
admission decision. Actual retries receive new `AttemptId`s and publication
attempts receive new `DeliveryId`s. Identities belong in detail, not metric
labels. The per-handle accounting state is owned by the host's tokens. Evicting
detail cannot close a token, lower a running gauge, or cancel work.

Lifecycle methods are notifications and return diagnostic errors. Hosts must
not propagate an instrumentation error in a way that skips real work or a real
completion. In particular, do not put `?` on a telemetry notification between
executing work and posting its result. Handle an unavailable telemetry token by
continuing the same host operation without that token. Examples and tests use
`unwrap` only on controlled, valid fixture paths.

## Boundaries

1. `requested` observes a declared output; `admission` separately accepts or
   rejects it. Rejection is counted but never enters the unresolved gauge
2. `attempt_created` is allocation/submission, not execution. `ready_queued`
   observes actual eligibility/enqueue; `ready_removed` reports synchronized
   removal without starting
3. `attempt_started` observes worker execution/first poll. `attempt_finished`
   reports actual known termination. A failure can precede another attempt
4. `resolved` fixes the logical result independently of still-running attempts.
   Passing a selected, finished attempt identifies the finish used for
   resolution-overhead measurement
5. Under the destination queue's publication synchronization, call
   `reserve_publication` immediately before making the completion visible.
   The timestamp and token state already exist when a fast consumer sees it.
   `publication_committed` confirms visibility without rereading the clock;
   `delivery_begun` can win that confirmation race. On failure call
   `publication_failed` before releasing synchronization. Failed reservations
   never create a completed preparation sample
6. `delivery_begun` starts input delivery. `delivery_observed` is immediately
   after the reducer returns, before checks/recording. Ignored and rejected
   inputs still count. `settled` is an explicit application acknowledgement

The standard-library `instrument_future` adapter starts only on first poll,
records elapsed time through Pending/Ready, and never holds a span-enter guard
across Pending. Future construction counts no attempt. Dropping a never-polled
or pending future records local abandonment, not success or confirmed remote
cancellation. A classifier panic cannot replace the completed future value:
it records `Unclassified`, known termination, and a callback-fault diagnostic.
A panic from the actual future itself remains an application failure.

`cancellation_requested` and `cancellation_acknowledged` are distinct facts.
Neither decrements running attempts. Logical cancellation resolution can
coexist with late successful attempt termination and a later delivery.

Repeated notifications for one token increment duplicate-observation health
without a second authoritative boundary. Separate observed delivery tokens
count as duplicate deliveries. This is local token deduplication, not a promise
of global exactly-once transport.

Destructors never wait for telemetry locks. Actual last-`Arc` token destruction
updates the active-handle count once. If a destructor cannot obtain an internal
lock, atomic `destructor_hook_losses` survives contention and makes the next
snapshot incomplete. Unknown work is not silently turned into a zero gauge.

## Durations, provenance, and pauses

`Clock` supplies `LocalInstant { domain, nanos }`. `MonotonicClock` uses checked
`Instant` arithmetic; test clocks qualify arithmetic separately. Reversed,
failed, missing, or cross-domain endpoints are unknown/errors, never zero
latency. Positively observed coincident endpoints can be zero. Runtime times
never enter model equality, canonical codecs, oracle state, or logical time.

The duration catalog preserves these separate endpoints:

| Family | Endpoints |
| --- | --- |
| `AdmissionDelay` | request → first ready queue |
| `QueueWait` | ready queue → actual attempt start |
| `AttemptElapsed` | actual start → known termination, including waits |
| `ResolutionOverhead` | selected attempt finish → logical resolution |
| `Resolution` | request → logical resolution |
| `CompletionPreparation` | resolution → successful publication reservation |
| `CompletionQueueWait` | publication reservation → delivery begin |
| `DeliveryLag` | resolution → delivery begin |
| `EndToEnd` | request → first delivery begin |
| `CompletionProcessing` | delivery begin → immediately after reducer |
| `EndToEndObserved` | request → first observed completion |
| `Settlement` | request → explicit acknowledgement |
| `ScheduledBackoff` | host-declared retry delay |
| `EligibilityWait` | retry scheduling observation → ready eligibility |
| `InterAttemptGap` | previous finish → next start, including more than backoff |

The synthetic fixture asserts queue 12 ms + elapsed 84 ms + delivery lag 4 ms =
100 ms to delivery begin; publication at 97 ms separates 1 ms preparation and
3 ms completion-queue wait, and observation at 102 ms adds 2 ms processing.
These are injected arithmetic values, not a speed measurement.

Measurement origins are Live, DebugControlledLive, TestClock, ReplayHarness,
and ImportedLive. Replay/import origins never increment the live aggregate.
`debugger_pause` retains bounded pause intervals (64 by default), increments a
pause generation, and conservatively marks affected operations' later timing
samples. Durations are not blindly reduced by pause duration. `production()`
excludes affected and non-Live series while the unfiltered snapshot and health
retain their counts. Unreported native-debugger interference is explicitly
possible. Retained pending detail supplies `pending_age`; it is censored work,
not a completed histogram sample. Hosts may query/sweep retained detail for
slow pending operations; no internal timer injects application timeouts.

## Metrics, bounds, and queries

`metric_catalog()` declares every typed family's name, unit, kind, population,
allowed labels, and reset semantics. Public names use seconds; aggregation
retains integer nanoseconds. Counter families separately cover requests,
admission decisions, starts, attempt outcomes, resolutions, deliveries,
cancellation request/acknowledgement, duplicates, abandonment, and health.
Unresolved logical effects, running attempts, ready queues, and completion
queues have different gauges.

Labels are registered finite operation/component/worker-pool catalogs plus
bounded outcome, measurement-origin, and affected enums. Raw payloads, errors,
URLs, user IDs, effect IDs, and trace IDs are not dynamically admitted labels.
Each catalog contains at most 256 unique entries of at most 64 bytes. Host
registration should name stable classes, not manufacture one class per entity.

Default caps are:

- Detail: 4,096 records / 8 MiB counted bytes; 128 events and 128 timing
  observations per record. Old records are evicted; history gaps/loss are visible
- Per effect: at most 1,024 instrumented attempt identities and 1,024 delivery
  identities. Exceeding telemetry limits reports an error; it must not change
  the host's actual retry/delivery policy
- Aggregation: 128 families / 2,048 series / 8 MiB counted bytes. New series are
  rejected visibly; existing series remain usable
- Histograms: at most 64 finite boundaries plus overflow, fixed 65-count storage
- Snapshot history: 12 snapshots / 16 MiB counted bytes, oldest evicted

These are companion-owned accounting budgets, not RSS promises. Fixed map
allowances are conservative estimates; host callbacks, allocators, retained
client snapshot clones, and third-party exporters have their own memory.
Outstanding handles remain bounded by host admission/concurrency and report
per-handle type storage separately in health. The overhead example reports
allocations as a separate measurement.

The default gauge scope is `ObservedSinceAttach`. A synchronized
`host_gauge_snapshot` establishes a baseline only for its label group. Each
gauge series carries its own scope in `MetricSnapshot.gauge_scopes`; mixed
scopes have aggregate `Unknown`. New groups keep the original attachment scope.
A snapshot is not reconstructed from logs. A complete-participation host can
explicitly choose that scope before admitting its entire population.

Snapshots are cumulative, with unique revision, process epoch, optional local
start/end, histogram schema, population labels, completeness, limits and health.
`metric_window(from, to)` differences retained compatible endpoints and keeps
end-point gauges. Unknown/evicted endpoints return unavailable. Epoch/schema
or authority changes are incompatible, not silently relabeled five-minute
windows. Invalid snapshot clocks make interval endpoints unknown. New epochs
use a fresh observer/store. Repeated cumulative exports must never be summed.

`Histogram::quantile` is a bucket upper-bound estimate. Zero observations
return unavailable, overflow has no finite upper bound, and fewer than 100
observations carry a low-sample warning. Present quantiles with the enclosing
snapshot's population, interval, origin, completeness, units, and schema.
`merge_disjoint` sums compatible buckets first and enforces all family/series/
byte caps. The caller must establish disjoint producer populations. Never
average worker p99s or merge repeated cumulative exports as independent data.

`MetricSink` is a host-owned export contract. A test adapter receives the full
cumulative snapshot and metadata. This release does not claim qualified
`metrics`, tracing, or OpenTelemetry facade/exporter interoperability and does
not install global recorders. Protocol read-only queries expose the same
bounded local data; they do not reingest historical sidecars.

## Completed slow-operation selection

`slow_timings(SlowTimingQuery::new(operation_id, endpoint_family, threshold_ns))`
selects previously captured completed measurements at or above the threshold.
For example, `QueueWait`, `AttemptElapsed`, and `DeliveryLag` independently
identify queue-slow, execution-slow, and delivery-slow observations. The typed
query can select an outcome and measurement origin; it defaults to Live and
excludes debugger-affected measurements unless explicitly requested.

The result carries effect/attempt/delivery identities, operation labels,
measurement outcome, epoch/detail revision, exact threshold/population,
inspected and matching counts, unknown durations, and completeness. It is
explicitly retained-detail-only, not a complete latency distribution. Count and
byte budgets both apply (defaults 64 results / 64 KiB; maximum 4,096 results /
4 MiB). Truncation, prior detail eviction/loss, unknown endpoint arithmetic,
and disabled timing/detail capture are visible. No clock is read by this query,
no missing history is recreated, and no metric, reducer, effect, or application
time is advanced. A caller can use this bounded result as the completed
slow-threshold diagnostic selection; an automatic timer or pending-work timeout
is not installed. Pending work remains available separately through censored
age/detail views.

## Qualification and limits

Run:

```sh
cargo test -p statelessness-debug --test effects_metrics --test effect_ledger_model --lib --offline
cargo run -p statelessness-debug --example effect_overhead --release --offline -- 20000
```

The independent ledger model exhausts 390 states and 6,024 edges. Focused tests
cover endpoint arithmetic, sequential retry/admission delay, no completion,
logical cancellation versus physical work, duplicate delivery, dropped detail,
unknown/reversed/cross-domain clocks, imported telemetry, pause separation,
bounded cardinality/bytes/history, epoch/schema windows, histogram merges,
per-series gauge baselines, bounded/population-filtered completed slow queries,
publication races, and real standard-library
Future polling. Deterministic destructor-contention tests verify explicit loss.

`validation/debugger/effect-overhead-*.csv` and
`effect-metrics-baseline.json` record compiler/platform, commands, source hashes,
and actual local measurements. The harness is a self-paced closed-loop test:
20,000 offered and completed lightweight host operations, concurrency one,
zero unfinished work. No sleeps/suspend or remote service are induced. The
single warm-up operation initializes aggregate series; allocation/time/clock
counters exclude setup/warm-up, while final storage totals include it. Allocated
bytes include reallocations and are not retained bytes. No exporter is active.

Configurations are compiled-out host work, runtime-disabled tokens,
counters-only, timings-without-detail, full retained detail, and saturated
retained detail. The benchmark demonstrates zero clock reads for disabled and
counter-only paths. Full detail uses bounded linear registry searches and is
substantially more expensive at a full registry; do not extrapolate the small
host-work baseline to a production application. This benchmark makes no CPU,
production percentile, open-loop queue growth, network, or cross-platform SLA
claim. Real async smoke is qualified on the recorded Linux x86-64 toolchain;
`Instant` suspend behavior remains platform-dependent.

Sequential retries are qualified. Raw overlapping attempts preserve independent
facts and running counts, but hedging/winning-attempt critical-path analysis,
distributed one-way timing, automatic slow-operation timers, exemplar
reservoirs, third-party metric backends, and native-debugger pause detection are
not qualified by this release.

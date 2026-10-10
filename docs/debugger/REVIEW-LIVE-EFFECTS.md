# Independent pre-PR review: live control, effects, and metrics

Review date: 2026-10-10. Starting source: `461a369` on `debugger/v1.1`;
implementation baseline: `f3e00e79471b928d67ddb530c113d6d51af6f277`.
The current repository `Statelessness-Rust-Debugger-Design-v1.1.md`, especially
sections 12, 15–17 and gates M3b/M5/M6, was read as the authority. Earlier PASS
reports were not accepted as substitutes for new tests.

Scope: `effects.rs`, `metrics.rs`, `live.rs`, and `examples/live_host.rs` in
`crates/statelessness-debug`, with independent regressions in
[`pr_review_live_effects.rs`](../../crates/statelessness-debug/tests/pr_review_live_effects.rs).
This is a focused review, not a new whole-project/platform qualification claim.
Concurrent review work may change other modules before final qualification.

## Confirmed findings and resolutions

### 1. High: example could lose an actual host result on diagnostic/control error

Before the fix, the checker-error path called `gate.delivered(...)?` and returned
without assigning `state = actual.state`. The normal path also propagated a
controller error before assigning that state. A capture error was propagated
before dispatch, suppressing authorized outputs; a dispatch-observation error
could stop the remaining output loop. Completion availability was represented
only when an effect telemetry token existed.

The example now shares a tested `commit_actual` helper. It consumes the single
actual transition, preserves its state and owned output vector, and only then
handles controller/checker stop policy. Checker/property failure, output-limit
failure, or termination retain outputs for the host's explicit shutdown policy;
they do not grant dispatch authority. Pure capture failure is logged and does
not suppress an otherwise authorized host dispatch. Dispatch facts are reported
only after actual host work, and a failed diagnostic notification cannot retry
that work or prevent its real completion publication. Completion availability
is represented even when telemetry handles are missing.

Example tests exercise the actual helper for checker error, property failure,
output overflow, encoder failure, and in-flight detach termination. They assert
actual state/revision preservation, owned pending outputs, zero unauthorized
dispatch facts, and one actual dispatch fact under encoder failure.

### 2. Medium: debugger-paused backoff samples leaked into production

`retry_scheduled` directly used stable gauge/counter labels for its histogram,
without applying the effect's pause annotation. A scheduled-backoff observation
made during a debugger pause appeared in `production()` and its detail event
was unannotated.

It now updates pause provenance before recording, annotates the histogram/event,
and includes the affected observation in health. The production histogram is
empty while the unfiltered histogram retains its observation.

### 3. Medium: rejected admissions were incorrectly shown as pending

`EffectDetails::pending_age` checked only logical resolution. A rejected request
cannot resolve through the accepted-effect path, so it appeared pending forever.
The method now returns unavailable for rejected admission. Rejection remains an
admission fact, never an unresolved gauge or completed latency sample.

### 4. Medium: stale retry scheduling was accepted

A created attempt could be abandoned, or its logical effect could resolve, and
`retry_scheduled` would still accept it and add a backoff sample. It now rejects
both states without recording a backoff. This does not alter real host retries;
a telemetry error remains a diagnostic error.

### 5. Medium: raw handle loss could falsely leave complete evidence

Only the future wrapper and final effect token had abandonment fallbacks. Dropping
the last raw `AttemptHandle` after logical resolution could leave a running gauge
with `complete=true` even though no token remained to report termination. The
same applied to reserved/published completion handles with no delivery endpoint.
Dropping a request before its admission decision also hid that missing decision.

Final shared attempt/delivery tokens now report missing evidence once, regardless
of clone/drop order. They use `try_lock`, never wait for telemetry locks, and
report a contended fallback through atomic loss accounting. They never decrement
running/queue gauges, invent success, or call abandonment confirmed cancellation.
The future wrapper and raw-token fallback share a once-only abandonment marker.
Failed publications and completed attempts/deliveries need no fallback. Resolved,
rejected, or already-abandoned effects avoid an unnecessary core-lock attempt,
so contention on a completed effect cannot manufacture a lost hook.

Coverage includes raw-token loss, concurrently dropped clones, retained clones,
never-admitted requests, successful/failed terminal paths, and deterministic lock
contention. The handle-size health estimates were updated for the owned shared
token state; old overhead numbers must not be treated as source-current.

### 6. Medium: aggregate merge discarded another producer's loss health

`MetricSnapshot::merge_disjoint` merged series and completeness but left the
other producer's health counts behind. A combined snapshot could be incomplete
with zero reported omissions/invalid measurements despite known loss.

All bounded health counters are now checked-added along with the disjoint
population. Overflow returns an error atomically; the original snapshot remains
unchanged. Merging still requires the caller to establish disjoint populations.

### 7. Medium: a rejected clock reading erased monotonic history

After a valid snapshot at 10, a reversed reading at 9 produced an unknown
endpoint, but a later reading at 8 was accepted because the previous endpoint
was already `None`. A missing reading had the same effect.

The store now retains its last valid endpoint independently of the displayed
snapshot. Invalid/missing readings cannot erase that domain/time watermark.
Later readings can recover only in the same domain at or beyond it; prior
incompleteness remains visible. Observer-construction clock failure is also
counted in health instead of being silently discarded.

### 8. Medium: snapshot timestamp raced with metric population capture

The observer read a timestamp and then acquired the metric lock. A concurrent
hook could record a completed measurement at 20 before the snapshot acquired
that lock, placing the measurement under a snapshot endpoint of 10.

The observer now checks the metric revision around the clock read. If recording
changed in between, it retains the cumulative values but exposes an unknown end
time and an omitted-measurement health count. It does not call an arbitrary
application-provided clock while holding the metric lock. A channel-synchronized
regression deterministically reproduces the old race without sleeps. New lifecycle
updates also clear `MetricsStore::current()`'s previous endpoint, preventing fresh
values from inheriting a timestamp from before their observation.

### 9. Medium: a foreign future adapter could mutate the wrong observer

Ordinary lifecycle hooks rejected foreign tokens, but `ObservedFuture::drop`
directly updated the wrapper observer's metrics using another observer's effect
state. Dropping a foreign pending wrapper incremented the wrong abandoned count
and marked the owner's effect abandoned.

The destructor now checks ownership before touching lifecycle state. Foreign
adapter hooks remain diagnostic no-ops with respect to that foreign token; the
future's actual behavior is not replaced. Its valid retained owner token remains
usable. If the final raw owner token is lost, its own ordinary abandonment
fallback still applies.

### 10. Shared protocol fix: rejected snapshots must not evict accepted windows

The independent protocol reviewer found that a metric query could mutate snapshot
revision/history before its response-size preflight rejected the wire response.
Repeated failed queries could therefore evict previously acknowledged windows.

The effects/metrics implementation now provides a crate-private transactional
snapshot path: it stages one prospective snapshot plus the history-eviction plan,
validates the complete response, and commits revision/history/watermark only on
acceptance. It does not clone the retained history. Clock reads and clock-health
reporting can still occur, but 32 rejected snapshot requests leave acknowledged
revisions and window endpoints intact. The protocol reviewer owns the external
response preflight and its independently reproduced integration regression.

## Preserved failing evidence

The first independent test run against the original reviewed implementation was:

```text
cargo test -p statelessness-debug --test pr_review_live_effects --offline
result: FAILED. 3 passed; 6 failed

last_raw_attempt_handle_loss_marks_running_evidence_incomplete
  failed: snapshot.complete was true after the last raw attempt token was lost
last_raw_delivery_handle_loss_marks_unobserved_publication_incomplete
  failed: snapshot.complete was true after the last raw delivery token was lost
abandoned_or_resolved_attempt_cannot_be_scheduled_as_a_retry
  left: Ok(())
  right: Err(WrongPhase)
merging_disjoint_producers_preserves_loss_health
  left: 0
  right: 3
rejected_admission_is_not_censored_pending_work
  left: Some(Ok(1000))
  right: None
retry_scheduled_during_pause_never_enters_production_histograms
  left: 1
  right: 0
```

These are the actual assertions/results preserved from the initial tool output;
the original command output was not separately saved to a repository log. The
three initially passing tests were the integrated actual-host lifecycle/replay,
encoder-error host-preservation pattern, and concurrent publication-confirmation
race. They were controls, not evidence that the six failing paths worked.

Two additional pre-fix reproductions were run after the first six fixes:

```text
cargo test -p statelessness-debug --test pr_review_live_effects snapshot_ --offline
result: FAILED. 0 passed; 2 failed

invalid_snapshot_clock_does_not_erase_the_previous_valid_watermark
  left: Some(SnapshotTime { domain: 73, nanos: 8 })
  right: None
snapshot_timestamp_never_predates_a_concurrently_included_measurement
  included a t=20 measurement under a t=10 endpoint:
  Some(SnapshotTime { domain: 73, nanos: 10 })
```

The foreign-adapter reproduction was also run before its fix:

```text
cargo test -p statelessness-debug --test pr_review_live_effects foreign_future --offline
result: FAILED. 0 passed; 1 failed
foreign_future_adapter_cannot_write_another_observers_accounting
  foreign observer abandoned count: left 1, right 0
```

## New independent coverage

The integrated fixture uses the real request-lifecycle model through a counting
wrapper, not a second implementation of its reducer. It combines `LiveBridge`,
`LiveController`, `EffectObserver`, bounded metric snapshots, exact trace export,
and actual replay in one test. It covers:

- One real reducer invocation and one initial/state/transition check batch per
  host turn, with explicit requested-output versus actual-dispatch correlation
- Pause acknowledgement, buffered arrivals, one-turn permission, no extra input
  after that turn, and dispatch bookkeeping before the next safe point
- Logical cancellation while an attempt is still physically running, late
  successful completion, accepted cleanup, and a rejected duplicate delivery
- Stable effect-origin epochs across controller reconnect and rejection of old
  command epochs
- One end-to-end latency sample, two actual delivery samples, one duplicate
  delivery, pause-separated production measurements, and coherent zero gauges
  only after known resolution/termination/consumption boundaries
- Exact replay invoking no host dispatcher and incrementing no live metric

Additional adversarial tests cover two simultaneous dispatch reservations,
reverse-order confirmations, disconnect during dispatch, staged pre-dispatch
pause, race-safe publication confirmation with real threads, health-merge
overflow atomicity, and missing endpoint accounting under cloned-token drops.
Existing live-controller and effect-ledger finite reference tests were rerun;
these still establish only their declared finite populations/interleavings.

## Validation result and limits

The focused verification commands are:

```sh
source /workspace/shared/render-rust-1.95.0/activate.sh
cargo test -p statelessness-debug --test pr_review_live_effects \
  --test effects_metrics --test effect_ledger_model --test live \
  --example live_host --lib --offline
cargo clippy -p statelessness-debug --test pr_review_live_effects \
  --example live_host --lib --offline -- -D warnings
cargo run -p statelessness-debug --example live_host --offline
```

Final focused result: **82 passed, zero failed** in the default build and **82
passed, zero failed** with `--features compiled-out-diagnostics`. The population
was 20 new integration/adversarial tests, 3 actual-example tests, 7 effect/snapshot
unit tests, 28 existing effect/metric tests, 23 existing live tests, and the
independent effect-ledger test. Strict Clippy passed with warnings denied. The
live example completed with exactly 2 actual dispatches and 3 replayed turns.
All five reviewed source/test hashes were unchanged across the final run.

Persisted focused evidence:

- [Default tests](../../validation/debugger/pr-review-live-effects-after.log)
- [Compiled-out tests](../../validation/debugger/pr-review-live-effects-compiled-out.log)
- [Strict Clippy](../../validation/debugger/pr-review-live-effects-clippy.log)
- [Live example](../../validation/debugger/pr-review-live-effects-example.log)
- [Toolchain/platform](../../validation/debugger/pr-review-live-effects-environment.log)
- [Reviewed source hashes](../../validation/debugger/pr-review-live-effects-source.sha256)

The parent review owns full-workspace, MSRV, cross-platform, documentation, and
source-bound overhead requalification. These focused hashes cover the reviewed
files, not independently changing modules from concurrent reviewers. Earlier
source-bound benchmark results remain historical until that rerun finishes.

Remaining explicit limitations:

- The host must serialize queue publication/removal and call real boundaries;
  no token can reconstruct external facts the host never observed
- A clock/metric capture race exposes an unknown endpoint; it cannot provide a
  timed window or trustworthy wall-clock percentile interval for that snapshot
  without another stable endpoint. Counter deltas can retain unknown time fields
- The host chooses whether checker/property failure stops real dispatch. The
  example retains pending outputs and terminates the synthetic harness; it is
  not a persistence/recovery implementation for production host queues
- Missing raw delivery evidence is retained as effect-level abandonment using
  the existing event shape; it does not assert where a remote completion went
- Full-detail lookup is bounded linear work, and count/byte limits are accounting
  estimates rather than allocator/RSS or arbitrary callback memory guarantees
- No real external collector, cross-process clock subtraction, uncontrolled OS
  scheduling proof, remote effect cancellation, or unrestricted hedged critical
  path is qualified by these tests

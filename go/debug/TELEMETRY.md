# Optional host diagnostics

Import `github.com/Jagalite/statelessness/go/debug` explicitly. The basic engine
imports no debugger, clock, transport, watcher or goroutine. All APIs here are
native Go; no Rust runtime is loaded.

`LiveController[I]` is a cooperative single-owner gate, not a scheduler or remote
server. Requesting pause does not acknowledge it: the host calls `SafePoint` at a
complete boundary. `BeginNext` removes one bounded arrival and returns a private
owner-bound permit. The host executes its real turn once, then calls `Delivered`
or `DeliveryFailed`. Output dispatch requires `TakeDispatch` before the external
action and `Dispatched` after it. `DispatchFailed` preserves uncertainty and
terminates the gate. Copying any Go permit never grants another delivery or
confirmation. An unreported permit blocks subsequent work. Disconnect retains
already admitted work; reconnect changes the client epoch while stable effect
origins retain their original host epoch. Authorities default off.

`AttachLiveBridge` accepts sealed engine checked tokens, including a coherent
checked initial checkpoint. `Observe` does not execute the reducer or checker.
Its exact recorder, bounded metadata queue, and bounded effect origins have
independent loss/freeze facts. A host gap freezes exact capture; later host output
facts are still metadata observations. Raw exports contain sensitive model bytes.

`EffectObserver` records explicit host boundaries using owner-bound effect,
attempt and delivery handles. Call `ReservePublication` under the host queue's
publication synchronization immediately before making a completion visible;
confirm it with `PublicationCommitted` or report `PublicationFailed`. A fast
consumer's `DeliveryBegun` also confirms successful publication. Capture
`DeliveryObserved` immediately after the real reducer returns, before checks and
recording. Neither hook calls the reducer. `Settled` is an independent explicit
host acknowledgement and need not follow a completion delivery.

Clock domains and quantities use full-width `uint64` integer nanoseconds. Missing,
reversed and cross-domain endpoints never become zero samples. A supplied clock
runs outside observer locks, and its panic/error becomes missing evidence. The
optional monotonic clock is constructed explicitly. Cancellation requests and
acknowledgements do not imply actual attempt termination. Report that with
`AttemptFinished`; explicit `AttemptAbandoned` marks observation incomplete and
keeps an unknown running attempt counted until a later known termination. Go has
no deterministic destruction hook: dropping a handle or goroutine is not an
observed termination, and this package installs no finalizer. Host adapters must
report abandonment explicitly.

Metric catalogs have finite numeric labels, per-family kinds, bounded series and
serialized detail/history estimates. Duration sums are exact unsigned 128-bit
`big.Int` values; overflow rejects the update. Histogram quantiles are bucket
upper bounds, including a present quantile with a null bound for overflow.
Snapshots own detached data. Compatible cumulative endpoints produce deltas;
gauges use the final endpoint. Cross-epoch/schema/clock/reset windows are rejected.
`MergeDisjoint` requires host-established disjoint populations and rejects
transactionally on incompatibility/overflow. `Production` excludes test, replay,
imported and debugger-affected populations. Catalog strings and identities never
become arbitrary per-request metric labels.

`DashboardFromSnapshot`, `SlowTimings`, `EffectDetails` and `Correlate` are passive.
Reports preserve missing series as null and contain no raw payload. `JSON` caps
returned output incrementally. Slow selections only cover retained completed
observations and report truncation/loss; pending ages require an explicit supplied
instant and are not completed histogram observations. Causal joins validate
numeric host labels against retained v2 outputs, including before-checkpoint and
missing/conflicting identities; they do not authenticate external execution.
`Dashboard.JSON` follows Rust `dashboard.rs` `to_json` key order and nesting:
snapshot metric counters are flattened into `health`, the observer's metric
health is `health.host_metric_health`, and rows follow.

`telemetry_health` emits Rust's `append_telemetry_health` field list in Rust
order. Go has no destructors and cannot observe a handle dropped without a
terminal lifecycle call, so `TelemetryHealth.ActiveEffectHandles` and
`DestructorHookLosses` are nil; the wire reports
`unknown:explicit_disposal_only` for both (the TypeScript precedent) and the
dashboard reports `null`. `ObservationComplete` therefore cannot include Rust's
`destructor_hook_losses == 0` term. `EffectHandleBytes`, `AttemptHandleBytes`
and `DeliveryHandleBytes` are the Go sizes of a handle value plus its shared
lifecycle state, the analogue of Rust's token-plus-handle `size_of`.
`ExportMetrics` invokes a host-selected sink outside locks and contains exporter
errors/panics without retrying host work.

Limits bound retained logical records and host-declared payload estimates, not
allocator RSS, arbitrary callback allocation, callback duration, or goroutine
preemption. No LLDB/GDB integration, remote authentication, process-wide exporter,
new default, profile promotion or broad qualification is implied.

# Default effect reports and causal attachment

`statelessness_debug::dashboard` provides a passive typed report over the existing
effect ledger and metric store. It does not introduce clocks into the reducer,
run effects, or place telemetry success on the application's execution path.
`Dashboard::from_snapshot` selects bounded numeric label sets and includes queue
wait, attempt elapsed, delivery lag, completion processing, settlement, pending
gauges, retry intervals and cancellation counts. The supplied metric/health
endpoints are not promised to be an atomic snapshot.

Missing series and empty histograms remain `None`/JSON null. Quantiles are
histogram upper-bound estimates with sample counts and low-sample warnings.
An infinite overflow bucket has a null upper bound and an explicit marker;
it does not become zero. Real snapshot clock domains remain separate from modeled
logical time. Cumulative and delta temporality are preserved.

The payload-free bounded JSON report includes health losses, callback/clock
faults, invalid/omitted measurements, series rejection and active handles.
Typed reports retain the full health structures. Row truncation is explicit and
cannot claim complete observation. No raw payload, correlation ID, effect ID or
unbounded string becomes an aggregate dimension. Gauge scope and complete versus
observed-since-attach participation remain available in the source snapshot;
consumers must not treat partial gauges as all-host totals.

`correlate` joins an existing `EffectTimeline` to a v2 origin by run, epoch,
machine, transition sequence and output index. The bridge uses decimal run and
machine labels. It distinguishes a retained matching origin, evicted prefix,
missing identity and conflict. It does not reconstruct missing detail or certify
that a host effect happened. Existing lifecycle events then show request,
admission, attempts, completion delivery and settlement; effect times stay in
their original clock domain.

The existing diagnostic-off, effect-boundary, pending-age, instrumentation-loss
and semantic parity suites remain part of workspace qualification. The new
dashboard tests cover unknown durations, bounded output, loss visibility, causal
conflicts and phase-derived offline failure selection. Runtime qualification and
benchmark measurements are pending locally; test compilation is not a result.

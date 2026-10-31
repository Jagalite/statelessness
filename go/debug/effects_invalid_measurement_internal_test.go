package debug

import "testing"

func TestInvalidTimingMetricDistinguishesMissingEndpoints(t *testing.T) {
	for _, counters := range []bool{false, true} {
		opts := DefaultEffectOptions()
		opts.Counters = counters
		opts.Details = false
		o, err := NewEffectObserver(opts, fixedOrderingClock{})
		if err != nil {
			t.Fatal(err)
		}
		s := &effectState{labels: Labels{Origin: OriginLive}, affected: true}
		start := LocalInstant{Domain: 1, Nanos: 5}
		reversed := LocalInstant{Domain: 1, Nanos: 4}
		otherDomain := LocalInstant{Domain: 2, Nanos: 7}
		o.measure(s, QueueWait, &start, &reversed, OutcomeFailure)
		o.measure(s, QueueWait, &start, &otherDomain, OutcomeFailure)
		o.measure(s, QueueWait, nil, &start, OutcomeFailure)
		snapshot := o.metrics.Current()
		if snapshot.Health.InvalidMeasurements != 2 || snapshot.Health.OmittedMeasurements != 1 || snapshot.Complete {
			t.Fatalf("wrong health for counters=%v: %+v", counters, snapshot.Health)
		}
		key := SeriesKey{Family: InvalidMeasurements, Labels: Labels{Origin: OriginLive, Outcome: OutcomeFailure, DebuggerAffected: true}}
		value, ok := snapshot.Series[key]
		if counters && (!ok || value.Number != 2) {
			t.Fatalf("missing invalid counter: %+v", snapshot.Series)
		}
		if !counters && ok {
			t.Fatal("disabled counters emitted a metric")
		}
	}
}

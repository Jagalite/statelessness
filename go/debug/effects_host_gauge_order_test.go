package debug_test

import (
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

func TestHostGaugeSnapshotRetainsReferenceOrderAtSeriesLimit(t *testing.T) {
	for i := 0; i < 32; i++ {
		opts := d.DefaultEffectOptions()
		opts.Timings = false
		opts.MetricLimits.MaxSeries = 2
		o, err := d.NewEffectObserver(opts, nil)
		if err != nil {
			t.Fatal(err)
		}
		if err = o.HostGaugeSnapshot(d.Labels{}, 11, 22, 33, 44); err != nil {
			t.Fatal(err)
		}
		s, err := o.MetricSnapshot()
		if err != nil {
			t.Fatal(err)
		}
		labels := d.Labels{Origin: d.OriginLive}
		for _, expected := range []struct {
			family d.MetricFamily
			value  uint64
		}{{d.Unresolved, 11}, {d.Running, 22}} {
			key := d.SeriesKey{Family: expected.family, Labels: labels}
			value, ok := s.Series[key]
			if !ok || value.Number != expected.value || s.GaugeScopes[key] != d.HostSnapshot {
				t.Fatalf("missing ordered host gauge %s: %+v", expected.family, s)
			}
		}
		if len(s.Series) != 2 || s.Health.RejectedSeries != 2 || s.Complete {
			t.Fatalf("limit health or series mismatch: %+v", s)
		}
	}
}

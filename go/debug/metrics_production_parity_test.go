package debug_test

import (
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

func TestProductionRemovesExcludedGaugeScopeAndKeepsWindowCompatible(t *testing.T) {
	live := d.SeriesKey{Family: d.Unresolved, Labels: d.Labels{Origin: d.OriginLive}}
	test := d.SeriesKey{Family: d.Unresolved, Labels: d.Labels{Origin: d.OriginTestClock}}
	debugged := d.SeriesKey{Family: d.Running, Labels: d.Labels{Origin: d.OriginLive, DebuggerAffected: true}}
	value := func(n uint64) d.MetricValue { return d.MetricValue{Kind: d.GaugeMetric, Number: n} }
	start := d.MetricSnapshot{Revision: 1, Epoch: 1, Schema: d.DefaultHistogramSchema(), Labels: d.DefaultLabelCatalog(), Limits: d.DefaultMetricLimits(), GaugeScope: d.ObservedSinceAttach, Temporality: "cumulative", Complete: true,
		Series:      map[d.SeriesKey]d.MetricValue{live: value(1), test: value(9), debugged: value(3)},
		GaugeScopes: map[d.SeriesKey]d.GaugeScope{live: d.ObservedSinceAttach, test: d.ObservedSinceAttach, debugged: d.ObservedSinceAttach}}
	end := d.MetricSnapshot{Revision: 2, Epoch: 1, Schema: d.DefaultHistogramSchema(), Labels: d.DefaultLabelCatalog(), Limits: d.DefaultMetricLimits(), GaugeScope: d.ObservedSinceAttach, Temporality: "cumulative", Complete: true,
		Series:      map[d.SeriesKey]d.MetricValue{live: value(2), test: value(10), debugged: value(4)},
		GaugeScopes: map[d.SeriesKey]d.GaugeScope{live: d.ObservedSinceAttach, test: d.HostSnapshot, debugged: d.UnknownGaugeScope}}
	a, b := start.Production(), end.Production()
	if len(a.Series) != 1 || len(a.GaugeScopes) != 1 || a.GaugeScopes[live] != d.ObservedSinceAttach || len(b.Series) != 1 || len(b.GaugeScopes) != 1 {
		t.Fatal(a, b)
	}
	if len(start.Series) != 3 || len(start.GaugeScopes) != 3 || len(end.Series) != 3 || len(end.GaugeScopes) != 3 {
		t.Fatal("production view mutated source")
	}
	window, err := d.MetricWindow(a, b)
	if err != nil || len(window.Series) != 1 || window.Series[live].Number != 2 || len(window.GaugeScopes) != 1 {
		t.Fatal(window, err)
	}
	// The unfiltered views really are incompatible: excluding the gauges must
	// not silently authorize their incompatible scope change in raw windows.
	if _, err = d.MetricWindow(start, end); err == nil {
		t.Fatal("raw incompatible gauge scopes accepted")
	}
}

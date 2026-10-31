package debug_test

import (
	"reflect"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

func TestMetricMergeChargesCatalogAndSchema(t *testing.T) {
	catalog := d.DefaultLabelCatalog()
	catalog.Operations = []string{"one", "two"}
	schema := d.DefaultHistogramSchema()
	base := 512 + 8*len(schema.FiniteBoundsNS)
	for _, names := range [][]string{catalog.Operations, catalog.Components, catalog.WorkerPools} {
		for _, name := range names {
			base += 24 + len(name)
		}
	}
	for _, tc := range []struct {
		name       string
		histogram  bool
		secondCost int
	}{{"counter", false, 128}, {"histogram", true, 768}} {
		t.Run(tc.name, func(t *testing.T) {
			for _, exact := range []bool{false, true} {
				limits := d.DefaultMetricLimits()
				limits.MaxBytes = base + 128 + tc.secondCost
				if !exact {
					limits.MaxBytes--
				}
				a, err := d.NewMetricsStore(1, catalog, schema, limits)
				if err != nil {
					t.Fatal(err)
				}
				b, err := d.NewMetricsStore(1, catalog, schema, limits)
				if err != nil {
					t.Fatal(err)
				}
				a.Increment(d.Requests, d.Labels{Origin: d.OriginLive}, 1)
				labels := d.Labels{Origin: d.OriginLive, Operation: 1}
				if tc.histogram {
					b.Observe(d.QueueWait, labels, 10)
				} else {
					b.Increment(d.Requests, labels, 2)
				}
				merged, before, other := a.Current(), a.Current(), b.Current()
				if len(before.Series) != 1 || len(other.Series) != 1 {
					t.Fatal("fixture did not retain both series")
				}
				err = merged.MergeDisjoint(other)
				if exact {
					if err != nil || len(merged.Series) != 2 {
						t.Fatalf("exact budget: %v, series %d", err, len(merged.Series))
					}
				} else if err == nil || !reflect.DeepEqual(merged, before) {
					t.Fatalf("under budget must reject without mutation: %v", err)
				}
			}
		})
	}
}

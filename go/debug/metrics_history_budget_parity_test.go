package debug_test

import (
	"fmt"
	"reflect"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

func TestMetricHistoryAccountsForEachSnapshotSize(t *testing.T) {
	catalog := d.DefaultLabelCatalog()
	catalog.Operations = []string{"one", "two", "three"}
	schema := d.DefaultHistogramSchema()
	base := 1024 + 8*len(schema.FiniteBoundsNS)
	for _, names := range [][]string{catalog.Operations, catalog.Components, catalog.WorkerPools} {
		for _, name := range names {
			base += 24 + len(name)
		}
	}
	limits := d.DefaultMetricLimits()
	limits.MaxSnapshotBytes = (base + 128) + (base + 256)
	m, err := d.NewMetricsStore(1, catalog, schema, limits)
	if err != nil {
		t.Fatal(err)
	}
	first, err := m.Snapshot(nil)
	if err != nil {
		t.Fatal(err)
	}
	m.Increment(d.Requests, d.Labels{Origin: d.OriginLive}, 1)
	second, err := m.Snapshot(nil)
	if err != nil {
		t.Fatal(err)
	}
	m.Increment(d.Requests, d.Labels{Origin: d.OriginLive, Operation: 1}, 1)
	third, err := m.Snapshot(nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := m.AvailableWindows(); !reflect.DeepEqual(got, []uint64{second.Revision, third.Revision}) {
		t.Fatalf("retained windows %v; first %d should be the only eviction", got, first.Revision)
	}
	if third.Health.SnapshotsEvicted != 1 {
		t.Fatalf("eviction count: %d", third.Health.SnapshotsEvicted)
	}
	if _, err := m.Window(second.Revision, third.Revision); err != nil {
		t.Fatalf("retained window: %v", err)
	}
	if _, err := m.Window(first.Revision, third.Revision); err == nil {
		t.Fatal("evicted window remained available")
	}
}

func TestMetricHistoryRetainsSmallWindowsAfterLiveGrowth(t *testing.T) {
	catalog := d.DefaultLabelCatalog()
	catalog.Operations = make([]string, 32)
	for i := range catalog.Operations {
		catalog.Operations[i] = fmt.Sprintf("operation-%d", i)
	}
	schema := d.DefaultHistogramSchema()
	base := 1024 + 8*len(schema.FiniteBoundsNS)
	for _, names := range [][]string{catalog.Operations, catalog.Components, catalog.WorkerPools} {
		for _, name := range names {
			base += 24 + len(name)
		}
	}
	limits := d.DefaultMetricLimits()
	limits.MaxSnapshotBytes = 2 * base
	m, err := d.NewMetricsStore(1, catalog, schema, limits)
	if err != nil {
		t.Fatal(err)
	}
	first, err := m.Snapshot(nil)
	if err != nil {
		t.Fatal(err)
	}
	second, err := m.Snapshot(nil)
	if err != nil {
		t.Fatal(err)
	}
	for i := range catalog.Operations {
		m.Increment(d.Requests, d.Labels{Origin: d.OriginLive, Operation: uint16(i)}, 1)
	}
	if len(m.Current().Series) != len(catalog.Operations) || base+128*len(catalog.Operations) <= limits.MaxSnapshotBytes {
		t.Fatal("fixture did not grow beyond the snapshot budget")
	}
	rejected, err := m.Snapshot(nil)
	if err != nil {
		t.Fatal(err)
	}
	if rejected.Health.SnapshotRejections != 1 || rejected.Health.SnapshotsEvicted != 0 {
		t.Fatalf("unexpected history health: %+v", rejected.Health)
	}
	if got := m.AvailableWindows(); !reflect.DeepEqual(got, []uint64{first.Revision, second.Revision}) {
		t.Fatalf("historical snapshots lost: %v", got)
	}
	window, err := m.Window(first.Revision, second.Revision)
	if err != nil || len(window.Series) != 0 {
		t.Fatalf("historical window changed: %+v, %v", window, err)
	}
}

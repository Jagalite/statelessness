package debug

import (
	"math"
	"testing"
)

func TestSnapshotRevisionSaturates(t *testing.T) {
	store, err := NewMetricsStore(1, DefaultLabelCatalog(), DefaultHistogramSchema(), DefaultMetricLimits())
	if err != nil {
		t.Fatal(err)
	}
	store.s.Revision = math.MaxUint64 - 1
	firstEnd := LocalInstant{Domain: 1, Nanos: 10}
	first, err := store.Snapshot(&firstEnd)
	if err != nil || first.Revision != math.MaxUint64 {
		t.Fatalf("first: revision=%d err=%v", first.Revision, err)
	}
	lastEnd := LocalInstant{Domain: 1, Nanos: 20}
	last, err := store.Snapshot(&lastEnd)
	if err != nil || last.Revision != math.MaxUint64 {
		t.Fatalf("saturated: revision=%d err=%v", last.Revision, err)
	}
	windows := store.AvailableWindows()
	if len(windows) != 2 || windows[0] != math.MaxUint64 || windows[1] != math.MaxUint64 {
		t.Fatalf("windows: %v", windows)
	}
	window, err := store.Window(math.MaxUint64, math.MaxUint64)
	if err != nil || window.Start == nil || window.End == nil || *window.Start != firstEnd || *window.End != firstEnd {
		t.Fatalf("window should use first retained matching revision: %#v, %v", window, err)
	}
}

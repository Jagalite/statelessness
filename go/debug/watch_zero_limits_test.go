package debug_test

import (
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

func TestWatchZeroCapacityLimits(t *testing.T) {
	for _, field := range []string{"watches", "ringRecords", "ringBytes", "eventBytes", "baselineBytes", "configBytes"} {
		t.Run(field, func(t *testing.T) {
			limits := d.DefaultWatchLimits()
			switch field {
			case "watches":
				limits.MaxWatches = 0
			case "ringRecords":
				limits.MaxRingRecords = 0
			case "ringBytes":
				limits.MaxRingBytes = 0
			case "eventBytes":
				limits.MaxEventBytes = 0
			case "baselineBytes":
				limits.MaxBaselineBytes = 0
			case "configBytes":
				limits.MaxConfigBytes = 0
			}
			r, err := d.NewWatchRegistry(limits, d.AllowAllWatches())
			if err != nil {
				t.Fatal(err)
			}
			_, err = r.Add(d.ChangedWatch([]d.Segment{d.FieldPath("name")}))
			if field == "watches" || field == "configBytes" || field == "eventBytes" {
				if err == nil {
					t.Fatal("expected no watch capacity")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if err = r.Observe(d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 1}}, func() d.InspectAdapter { return inspected{new(int), "x", false} }); err != nil {
				t.Fatal(err)
			}
			if field == "ringRecords" || field == "ringBytes" {
				if r.Health().Records != 0 || r.Health().DroppedTotal == 0 {
					t.Fatal(r.Health())
				}
			}
			if field == "baselineBytes" {
				_, _ = r.Drain()
				if err = r.Observe(d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 2}}, func() d.InspectAdapter { return inspected{new(int), "x", false} }); err != nil {
					t.Fatal(err)
				}
				events, err := r.Drain()
				if err != nil || len(events) != 1 || events[0].Kind != "baseline_evicted" {
					t.Fatal(events, err)
				}
			}
		})
	}
	limits := d.DefaultWatchLimits()
	limits.MaxRingRecords = -1
	if _, err := d.NewWatchRegistry(limits, d.AllowAllWatches()); err == nil {
		t.Fatal("negative capacity accepted")
	}
}

func TestWatchAddValidatedCapacityBeforeProjection(t *testing.T) {
	limits := d.DefaultWatchLimits()
	limits.MaxWatches = 0
	r, err := d.NewWatchRegistry(limits, d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	_, err = r.AddValidated(0, d.ChangedWatch(nil), namedWatchAdapter{"a", &calls}, d.SnapshotID{})
	if err == nil || calls != 0 {
		t.Fatalf("capacity: err=%v projection calls=%d", err, calls)
	}
}

func TestWatchRejectsInvalidProjectionBeforeAdmission(t *testing.T) {
	for _, change := range []func(*d.WatchConfig){
		func(c *d.WatchConfig) { c.Limits.MaxBytes = 0 },
		func(c *d.WatchConfig) { c.Limits.MaxBytes = 32769 },
		func(c *d.WatchConfig) { c.Limits.MaxDepth = 33 },
		func(c *d.WatchConfig) { c.Limits.MaxDepth = 0; c.Selector.Path = []d.Segment{d.FieldPath("x")} },
	} {
		r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
		if err != nil {
			t.Fatal(err)
		}
		config := d.ChangedWatch(nil)
		change(&config)
		calls := 0
		if _, err = r.AddValidated(0, config, namedWatchAdapter{"a", &calls}, d.SnapshotID{}); err == nil || calls != 0 || r.CaptureRevision() != 0 {
			t.Fatalf("invalid admitted: err=%v calls=%d revision=%d", err, calls, r.CaptureRevision())
		}
	}
}

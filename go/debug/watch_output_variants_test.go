package debug_test

import (
	"strings"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

func TestWatchOutputVariantsMatchAndSnapshot(t *testing.T) {
	r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"first", "second", "legacy", "absent"} {
		c := d.ChangedWatch(nil)
		c.Selector.OutputVariant = name
		if _, err = r.Add(c); err != nil {
			t.Fatal(err)
		}
	}
	r.Drain()
	variants := []string{"first", "second"}
	calls := 0
	err = r.Observe(d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 1}, OutputVariant: "legacy", OutputVariants: variants}, func() d.InspectAdapter {
		calls++
		variants[1] = "changed"
		return numericProjection("1")
	})
	if err != nil {
		t.Fatal(err)
	}
	events, err := r.Drain()
	if err != nil || calls != 1 || len(events) != 3 {
		t.Fatalf("calls=%d events=%v err=%v", calls, events, err)
	}
}

func TestWatchOutputVariantsInvalidInputDoesNotAdvance(t *testing.T) {
	for _, variants := range [][]string{{string([]byte{0xff})}, {strings.Repeat("x", 1024*1024+1)}, make([]string, 4097)} {
		r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
		if err != nil {
			t.Fatal(err)
		}
		c := d.ChangedWatch(nil)
		c.Selector.OutputVariant = "ok"
		if _, err = r.Add(c); err != nil {
			t.Fatal(err)
		}
		r.Drain()
		ctx := d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 1}, OutputVariants: variants}
		if err = r.Observe(ctx, func() d.InspectAdapter { t.Fatal("invalid input invoked callback"); return nil }); err == nil {
			t.Fatal("invalid input accepted")
		}
		ctx.OutputVariants = []string{"ok"}
		if err = r.Observe(ctx, func() d.InspectAdapter { return numericProjection("1") }); err != nil {
			t.Fatal(err)
		}
		events, err := r.Drain()
		if err != nil || len(events) != 1 || events[0].Kind != "baseline_initial" {
			t.Fatalf("events=%v err=%v", events, err)
		}
	}
}

func TestWatchGapRejectsInvalidOutputVariantsWithoutMutation(t *testing.T) {
	for _, variants := range [][]string{{string([]byte{0xff})}, {strings.Repeat("x", 1024*1024+1)}, make([]string, 4097)} {
		r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
		if err != nil {
			t.Fatal(err)
		}
		c := d.ChangedWatch(nil)
		c.Selector.OutputVariant = "ok"
		if _, err = r.Add(c); err != nil {
			t.Fatal(err)
		}
		r.Drain()
		ctx := d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 1}, OutputVariants: []string{"ok"}}
		if err = r.Observe(ctx, func() d.InspectAdapter { return numericProjection("1") }); err != nil {
			t.Fatal(err)
		}
		r.Drain()
		ctx.OutputVariants = variants
		if err = r.Gap(ctx); err == nil {
			t.Fatal("invalid gap accepted")
		}
		records, err := r.Drain()
		if err != nil || len(records) != 0 {
			t.Fatalf("records=%v err=%v", records, err)
		}
		ctx.OutputVariants = []string{"ok"}
		ctx.Snapshot.Sequence++
		if err = r.Observe(ctx, func() d.InspectAdapter { return numericProjection("1") }); err != nil {
			t.Fatal(err)
		}
		records, err = r.Drain()
		if err != nil || len(records) != 0 {
			t.Fatalf("baseline changed: %v err=%v", records, err)
		}
	}
}

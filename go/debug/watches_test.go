package debug_test

import (
	"errors"
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

type faultWriter struct{}

func (faultWriter) Write([]byte) (int, error) { return 0, errors.New("export fault") }
func TestWatchNoInterestRevisionsAndFaultIsolation(t *testing.T) {
	r, e := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if e != nil {
		t.Fatal(e)
	}
	calls := 0
	value := func() d.InspectAdapter { calls++; return inspected{new(int), "one", false} }
	c := d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 1}}
	if e = r.Observe(c, value); e != nil || calls != 0 {
		t.Fatal(e, calls)
	}
	config := d.ChangedWatch([]d.Segment{d.FieldPath("name")})
	ack, e := r.Add(config)
	if e != nil || ack.CaptureRevision != 1 {
		t.Fatal(ack, e)
	}
	if e = r.Observe(c, value); e != nil || calls != 1 {
		t.Fatal(e, calls)
	}
	if e = r.Export(faultWriter{}); e == nil {
		t.Fatal("export fault hidden")
	}
	events, e := r.Drain()
	if e != nil || len(events) != 2 || events[1].Kind != "baseline_initial" {
		t.Fatal(events, e)
	}
	c.Snapshot.Sequence = 2
	if e = r.Observe(c, value); e != nil {
		t.Fatal(e)
	}
	events, _ = r.Drain()
	if len(events) != 0 {
		t.Fatal(events)
	}
	config.Enabled = false
	ack, e = r.Update(ack.WatchID, config)
	if e != nil || ack.CaptureRevision != 2 || ack.Generation != 2 {
		t.Fatal(ack, e)
	}
	if e = r.Observe(c, value); e != nil || calls != 2 {
		t.Fatal(e, calls)
	}
}
func TestWatchLossAndAuthorization(t *testing.T) {
	limits := d.DefaultWatchLimits()
	limits.MaxRingRecords = 1
	r, e := d.NewWatchRegistry(limits, d.AllowWatchPaths([][]d.Segment{{d.FieldPath("name")}}))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = r.Add(d.ChangedWatch([]d.Segment{d.FieldPath("secret")})); e == nil {
		t.Fatal("unauthorized")
	}
	ack, e := r.Add(d.ChangedWatch([]d.Segment{d.FieldPath("name")}))
	if e != nil {
		t.Fatal(e)
	}
	if e = r.Remove(ack.WatchID); e != nil {
		t.Fatal(e)
	}
	events, e := r.Drain()
	if e != nil || len(events) != 2 || events[0].Kind != "events_dropped" || events[0].Dropped != 1 {
		t.Fatal(events, e)
	}
}

type numericProjection string

func (numericProjection) DisplaySchema() d.DisplaySchema { return d.DisplaySchema{Version: 1} }
func (v numericProjection) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	return c.Scalar(path, d.Scalar{Kind: "u128", Decimal: string(v)})
}
func TestThresholdHysteresisAndFullWidth(t *testing.T) {
	registry, e := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if e != nil {
		t.Fatal(e)
	}
	config := d.ChangedWatch(nil)
	config.Trigger = "above_u128"
	config.Threshold = d.UInt128{Hi: 1, Lo: 0}
	config.Hysteresis = d.UInt128{Lo: 2}
	config.CooldownTurns = 3
	if _, e = registry.Add(config); e != nil {
		t.Fatal(e)
	}
	registry.Drain()
	values := []string{"18446744073709551617", "18446744073709551618", "18446744073709551614", "18446744073709551617"}
	events := 0
	for i, value := range values {
		context := d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: uint64(i + 1)}}
		if e = registry.Observe(context, func() d.InspectAdapter { return numericProjection(value) }); e != nil {
			t.Fatal(e)
		}
		records, _ := registry.Drain()
		events += len(records)
	}
	if events != 2 {
		t.Fatal("threshold expected two alerts", events)
	}
}
func TestValidatedPathAndSourceResetSnapshots(t *testing.T) {
	registry, e := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if e != nil {
		t.Fatal(e)
	}
	calls := 0
	adapter := inspected{&calls, "name", false}
	if _, e = registry.AddValidated(0, d.ChangedWatch([]d.Segment{d.FieldPath("secret")}), adapter, d.SnapshotID{}); e == nil || calls != 0 {
		t.Fatal("redacted path accepted", e, calls)
	}
	ack, e := registry.AddValidated(0, d.ChangedWatch([]d.Segment{d.FieldPath("name")}), adapter, d.SnapshotID{})
	if e != nil || ack.ResolvedKind != "string" || ack.DisplaySchema.Name != "manual" {
		t.Fatal(ack, e)
	}
	registry.Drain()
	context := d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 1}, Model: "model", Machine: "one", Origin: "simulation"}
	value := func() d.InspectAdapter { return adapter }
	registry.Observe(context, value)
	events, _ := registry.Drain()
	if len(events) != 1 || events[0].Snapshot.Sequence != 1 {
		t.Fatal(events)
	}
	context.Snapshot.Sequence = 2
	context.Machine = "two"
	registry.Observe(context, value)
	events, _ = registry.Drain()
	if len(events) != 1 || events[0].Kind != "baseline_source_changed" || events[0].PreviousSnapshot == nil || events[0].PreviousSnapshot.Sequence != 1 {
		t.Fatal(events)
	}
	registry.Gap(context)
	events, _ = registry.Drain()
	if len(events) != 1 || events[0].Kind != "observation_gap" {
		t.Fatal(events)
	}
}

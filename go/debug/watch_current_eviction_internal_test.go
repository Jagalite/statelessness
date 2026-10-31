package debug

import (
	"fmt"
	"testing"
)

func TestWatchEvictsBaselineWhenCurrentCannotFit(t *testing.T) {
	result, err := Inspect(evictionScalar{}, DefaultInspectQuery())
	if err != nil {
		t.Fatal(err)
	}
	limits := DefaultWatchLimits()
	// Allow the baseline alone but not a second retained current event.
	limits.MaxBaselineBytes = retainedNode(result.Node) + 128 + uint64(len(result.Schema.Name))
	r, err := NewWatchRegistry(limits, AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	ack, err := r.Add(ChangedWatch(nil))
	if err != nil {
		t.Fatal(err)
	}
	_, _ = r.Drain()
	ctx := WatchContext{Snapshot: SnapshotID{Session: 1, Sequence: 1}}
	if err = r.Observe(ctx, func() InspectAdapter { return evictionScalar{} }); err != nil {
		t.Fatal(err)
	}
	entry := r.entries[ack.WatchID]
	if entry.baseline != nil || entry.current != nil || entry.pendingBaseline != "evicted" || r.baselineBytes != 0 {
		t.Fatalf("orphaned baseline: %+v", entry)
	}
	_, _ = r.Drain()
	ctx.Snapshot.Sequence++
	if err = r.Observe(ctx, func() InspectAdapter { return evictionScalar{} }); err != nil {
		t.Fatal(err)
	}
	events, err := r.Drain()
	if err != nil || len(events) != 1 || events[0].Kind != "baseline_evicted" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
}

type evictedFault struct{ fail bool }

func (evictedFault) DisplaySchema() DisplaySchema { return DisplaySchema{Name: "counter", Version: 1} }
func (a evictedFault) Inspect(path []Segment, c *InspectContext) (InspectNode, error) {
	if a.fail {
		return InspectNode{}, fmt.Errorf("fault")
	}
	return c.Unavailable(path)
}
func TestWatchEvictsUnretainedIncompleteOrErrorCurrent(t *testing.T) {
	for _, fail := range []bool{false, true} {
		limits := DefaultWatchLimits()
		limits.MaxBaselineBytes = 1
		r, err := NewWatchRegistry(limits, AllowAllWatches())
		if err != nil {
			t.Fatal(err)
		}
		ack, err := r.Add(ChangedWatch(nil))
		if err != nil {
			t.Fatal(err)
		}
		_, _ = r.Drain()
		ctx := WatchContext{Snapshot: SnapshotID{Session: 1, Sequence: 1}}
		if err = r.Observe(ctx, func() InspectAdapter { return evictedFault{fail: fail} }); err != nil {
			t.Fatal(err)
		}
		entry := r.entries[ack.WatchID]
		if entry.current != nil || entry.pendingBaseline != "evicted" || r.baselineEvictions != 1 {
			t.Fatalf("fail=%v state=%+v", fail, entry)
		}
		_, _ = r.Drain()
		ctx.Snapshot.Sequence++
		if err = r.Observe(ctx, func() InspectAdapter { return evictionScalar{} }); err != nil {
			t.Fatal(err)
		}
		events, err := r.Drain()
		if err != nil || len(events) != 1 || events[0].Kind != "baseline_evicted" {
			t.Fatalf("fail=%v events=%+v err=%v", fail, events, err)
		}
	}
}

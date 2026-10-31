package debug_test

import (
	"errors"
	d "github.com/Jagalite/statelessness/go/debug"
	"strings"
	"testing"
)

func TestWatchSnapshotNowDoesNotChangeBaselineAndOwnsEvent(t *testing.T) {
	r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	config := d.ChangedWatch([]d.Segment{d.FieldPath("name")})
	ack, err := r.Add(config)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = r.Drain()
	c := d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 1}}
	if err = r.Observe(c, func() d.InspectAdapter { return inspected{new(int), "before", false} }); err != nil {
		t.Fatal(err)
	}
	_, _ = r.Drain()
	c.Snapshot.Sequence = 100
	c.PropertyFailed, c.PropertyStatusKnown = true, true
	c.FailingProperties = []string{strings.Repeat("é", 129)}
	event, err := r.SnapshotNow(ack.WatchID, inspected{new(int), "snapshot", false}, c)
	if err != nil || event.Kind != "snapshot" || len(event.FailingProperties) != 1 || len(event.FailingProperties[0]) != 256 || !event.PropertyFailed {
		t.Fatal(event, err)
	}
	event.FailingProperties[0] = "changed"
	event.Node.Text = "changed"
	events, err := r.Drain()
	if err != nil || len(events) != 1 || events[0].FailingProperties[0] == "changed" || events[0].Node.Text == "changed" {
		t.Fatal(events, err)
	}
	c.Snapshot.Sequence = 2
	c.PropertyFailed = false
	c.FailingProperties = nil
	if err = r.Observe(c, func() d.InspectAdapter { return inspected{new(int), "before", false} }); err != nil {
		t.Fatal(err)
	}
	events, err = r.Drain()
	if err != nil || len(events) != 0 {
		t.Fatal(events, err)
	}
	config.Enabled = false
	if _, err = r.Update(ack.WatchID, config); err != nil {
		t.Fatal(err)
	}
	if _, err = r.SnapshotNow(ack.WatchID, inspected{new(int), "disabled", false}, c); err != nil {
		t.Fatal(err)
	}
	if _, err = r.SnapshotNow(999, inspected{new(int), "x", false}, c); err == nil {
		t.Fatal("unknown watch accepted")
	}
}

func TestWatchSnapshotNowReportsTruncatedFailureMetadata(t *testing.T) {
	r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	ack, err := r.Add(d.ChangedWatch(nil))
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name string
		ids  []string
		want string
	}{
		{"complete", []string{"id"}, ""},
		{"long utf8", []string{strings.Repeat("é", 129)}, "value_limit"},
		{"too many", make([]string, 17), "value_limit"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			event, err := r.SnapshotNow(ack.WatchID, namedWatchAdapter{"schema", new(int)}, d.WatchContext{FailingProperties: tc.ids})
			if err != nil || event.Completeness != tc.want {
				t.Fatalf("completeness=%q want=%q err=%v", event.Completeness, tc.want, err)
			}
		})
	}
}

type namedWatchAdapter struct {
	name  string
	calls *int
}

func (a namedWatchAdapter) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: a.name, Version: 1}
}
func (a namedWatchAdapter) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	*a.calls++
	return c.String(path, "value")
}

func TestSnapshotNowValidatedSchemaIdentityAndUpdate(t *testing.T) {
	r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	config := d.ChangedWatch(nil)
	calls := 0
	a := namedWatchAdapter{"a", &calls}
	b := namedWatchAdapter{"b", &calls}
	ack, err := r.AddValidated(0, config, a, d.SnapshotID{})
	if err != nil {
		t.Fatal(err)
	}
	calls = 0
	if _, err = r.SnapshotNow(ack.WatchID, b, d.WatchContext{}); err == nil || calls != 0 {
		t.Fatal("mismatched schema projected", err, calls)
	}
	if _, err = r.SnapshotNow(ack.WatchID, a, d.WatchContext{}); err != nil {
		t.Fatal(err)
	}
	if _, err = r.Update(ack.WatchID, config); err != nil {
		t.Fatal(err)
	}
	if _, err = r.SnapshotNow(ack.WatchID, b, d.WatchContext{}); err != nil {
		t.Fatal("provisional update retained old schema", err)
	}
}

func TestWatchObservePausesOnSchemaIdentityChange(t *testing.T) {
	for _, validated := range []bool{false, true} {
		t.Run(map[bool]string{false: "provisional", true: "validated"}[validated], func(t *testing.T) {
			r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
			if err != nil {
				t.Fatal(err)
			}
			config := d.ChangedWatch(nil)
			calls := 0
			a, b := namedWatchAdapter{"a", &calls}, namedWatchAdapter{"b", &calls}
			var id uint64
			if validated {
				ack, e := r.AddValidated(0, config, a, d.SnapshotID{})
				if e != nil {
					t.Fatal(e)
				}
				id = ack.WatchID
			} else {
				ack, e := r.Add(config)
				if e != nil {
					t.Fatal(e)
				}
				id = ack.WatchID
			}
			_, _ = r.Drain()
			c := d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 1}}
			if err = r.Observe(c, func() d.InspectAdapter { return a }); err != nil {
				t.Fatal(err)
			}
			_, _ = r.Drain()
			calls = 0
			c.Snapshot.Sequence = 2
			if err = r.Observe(c, func() d.InspectAdapter { return b }); err != nil {
				t.Fatal(err)
			}
			events, e := r.Drain()
			if e != nil || len(events) != 1 || events[0].Kind != "schema_changed" || events[0].WatchID != id || calls != 0 {
				t.Fatal(events, e, calls)
			}
			c.Snapshot.Sequence = 3
			if err = r.Observe(c, func() d.InspectAdapter { t.Fatal("paused watch evaluated"); return a }); err != nil {
				t.Fatal(err)
			}
			if _, err = r.Update(id, config); err != nil {
				t.Fatal(err)
			}
			c.Snapshot.Sequence = 4
			if err = r.Observe(c, func() d.InspectAdapter { return b }); err != nil {
				t.Fatal(err)
			}
		})
	}
}

type failingNamedWatchAdapter struct {
	name  string
	calls *int
}

func (a failingNamedWatchAdapter) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: a.name, Version: 1}
}
func (a failingNamedWatchAdapter) Inspect(_ []d.Segment, _ *d.InspectContext) (d.InspectNode, error) {
	*a.calls++
	return d.InspectNode{}, errors.New("failure")
}

func TestWatchLearnsSchemaBeforeFailedProjection(t *testing.T) {
	r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	if _, err = r.Add(d.ChangedWatch(nil)); err != nil {
		t.Fatal(err)
	}
	_, _ = r.Drain()
	calls := 0
	c := d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 1}}
	if err = r.Observe(c, func() d.InspectAdapter { return failingNamedWatchAdapter{"a", &calls} }); err != nil {
		t.Fatal(err)
	}
	_, _ = r.Drain()
	calls = 0
	c.Snapshot.Sequence = 2
	if err = r.Observe(c, func() d.InspectAdapter { return namedWatchAdapter{"b", &calls} }); err != nil {
		t.Fatal(err)
	}
	events, e := r.Drain()
	if e != nil || len(events) != 1 || events[0].Kind != "schema_changed" || calls != 0 {
		t.Fatal(events, e, calls)
	}
}

func TestWatchDetectsSchemaChangeOnSampledBoundary(t *testing.T) {
	r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	config := d.ChangedWatch(nil)
	config.EvaluationInterval = 3
	ack, err := r.Add(config)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = r.Drain()
	calls := 0
	context := d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 1}}
	if err = r.Observe(context, func() d.InspectAdapter { return namedWatchAdapter{"a", &calls} }); err != nil {
		t.Fatal(err)
	}
	_, _ = r.Drain()
	calls = 0
	context.Snapshot.Sequence++
	if err = r.Observe(context, func() d.InspectAdapter { return namedWatchAdapter{"b", &calls} }); err != nil {
		t.Fatal(err)
	}
	events, err := r.Drain()
	if err != nil || len(events) != 1 || events[0].Kind != "schema_changed" || events[0].WatchID != ack.WatchID || calls != 0 {
		t.Fatalf("schema change at sampled boundary: events=%#v err=%v projections=%d", events, err, calls)
	}
}

func TestWatchRetainsSampledSourceChange(t *testing.T) {
	for _, expire := range []bool{false, true} {
		r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
		if err != nil {
			t.Fatal(err)
		}
		config := d.ChangedWatch(nil)
		config.EvaluationInterval = 2
		if expire {
			ttl := uint64(1)
			config.BaselineTTLSequences = &ttl
		}
		if _, err = r.Add(config); err != nil {
			t.Fatal(err)
		}
		_, _ = r.Drain()
		calls := 0
		adapter := namedWatchAdapter{"a", &calls}
		for i, model := range []string{"a", "b", "a"} {
			context := d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: uint64(i + 1)}, Model: model}
			if err = r.Observe(context, func() d.InspectAdapter { return adapter }); err != nil {
				t.Fatal(err)
			}
		}
		events, err := r.Drain()
		want := "baseline_source_changed"
		if expire {
			want = "baseline_expired"
		}
		if err != nil || len(events) != 2 || events[1].Kind != want || calls != 2 {
			t.Fatalf("expire=%v events=%#v err=%v projections=%d", expire, events, err, calls)
		}
	}
}

func TestWatchSchemaMetadataIsBounded(t *testing.T) {
	r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	ack, err := r.Add(d.ChangedWatch(nil))
	if err != nil {
		t.Fatal(err)
	}
	_, _ = r.Drain()
	adapter := namedWatchAdapter{strings.Repeat("a", 1023) + "éx", new(int)}
	event, err := r.SnapshotNow(ack.WatchID, adapter, d.WatchContext{})
	if err != nil || len(event.SchemaName) != 1023 || event.Completeness != "value_limit" {
		t.Fatalf("snapshot schema bytes=%d completeness=%q err=%v", len(event.SchemaName), event.Completeness, err)
	}
	_, _ = r.Drain()
	if err := r.Observe(d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 1}}, func() d.InspectAdapter { return adapter }); err != nil {
		t.Fatal(err)
	}
	events, err := r.Drain()
	if err != nil || len(events) != 1 || len(events[0].SchemaName) != 1023 || events[0].Completeness != "value_limit" {
		t.Fatalf("observation=%+v err=%v", events, err)
	}
	// Names with the same displayed prefix still have distinct identities.
	other := namedWatchAdapter{strings.Repeat("a", 1023) + "éy", new(int)}
	if err := r.Observe(d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 2}}, func() d.InspectAdapter { return other }); err != nil {
		t.Fatal(err)
	}
	events, err = r.Drain()
	if err != nil || len(events) != 1 || events[0].Kind != "schema_changed" {
		t.Fatalf("identity change=%+v err=%v", events, err)
	}
}

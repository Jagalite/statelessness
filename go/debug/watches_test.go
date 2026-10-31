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
	c.Snapshot.Sequence = 2
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
	c.Snapshot.Sequence = 3
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
	if e != nil || len(events) != 1 || events[0].Kind != "watch_removed" || r.Health().DroppedTotal != 1 {
		t.Fatal(events, e)
	}
	// An uninterested observation leaves the loss pending.
	if err := r.Observe(d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 1}}, nil); err != nil {
		t.Fatal(err)
	}
	events, e = r.Drain()
	if e != nil || len(events) != 0 {
		t.Fatal(events, e)
	}
	config := d.ChangedWatch([]d.Segment{d.FieldPath("name")})
	config.Selector.Model = "selected"
	if _, err := r.Add(config); err != nil {
		t.Fatal(err)
	}
	r.Drain()
	if err := r.Observe(d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 2, Revision: 7}}, nil); err != nil {
		t.Fatal(err)
	}
	events, e = r.Drain()
	if e != nil || len(events) != 1 || events[0].Kind != "events_dropped" || events[0].Dropped != 1 || events[0].Snapshot.Revision != 7 {
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

func TestPropertyFailureBypassesSampling(t *testing.T) {
	for _, trigger := range []string{"property_failure", "every", "changed", "equals", "above_u128"} {
		t.Run(trigger, func(t *testing.T) {
			r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
			if err != nil {
				t.Fatal(err)
			}
			config := d.ChangedWatch(nil)
			config.Trigger = trigger
			config.EvaluationInterval = 5
			config.DeliveryInterval = 5
			if trigger == "equals" {
				config.Equals = &d.Scalar{Kind: "u128", Decimal: "999"}
			}
			if trigger == "above_u128" {
				config.Threshold = d.UInt128{Lo: 999}
			}
			if _, err = r.Add(config); err != nil {
				t.Fatal(err)
			}
			r.Drain()
			calls := 0
			value := func() d.InspectAdapter { calls++; return numericProjection("1") }
			for sequence := uint64(0); sequence < 3; sequence++ {
				context := d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: sequence}, PropertyStatusKnown: true, PropertyFailed: true, FailingProperties: []string{"p"}}
				if err = r.Observe(context, value); err != nil {
					t.Fatal(err)
				}
				events, _ := r.Drain()
				if len(events) != 1 || !events[0].PropertyFailed || events[0].Node == nil {
					t.Fatalf("sequence %d: %#v", sequence, events)
				}
			}
			if calls != 3 {
				t.Fatal(calls)
			}
		})
	}
}

func TestFailureCaptureKeepsPolicyGates(t *testing.T) {
	for _, policy := range []string{"disabled", "selector", "ttl", "unknown", "healthy"} {
		t.Run(policy, func(t *testing.T) {
			r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
			if err != nil {
				t.Fatal(err)
			}
			config := d.ChangedWatch(nil)
			config.Trigger = "property_failure"
			config.EvaluationInterval = 5
			config.DeliveryInterval = 5
			switch policy {
			case "disabled":
				config.Enabled = false
			case "selector":
				config.Selector.Machine = "other"
			case "ttl":
				ttl := uint64(1)
				config.TTLObservations = &ttl
			}
			if _, err = r.Add(config); err != nil {
				t.Fatal(err)
			}
			r.Drain()
			calls := 0
			value := func() d.InspectAdapter { calls++; return numericProjection("1") }
			context := d.WatchContext{Snapshot: d.SnapshotID{Session: 1}, PropertyStatusKnown: true, PropertyFailed: true}
			if policy == "unknown" {
				context.PropertyStatusKnown = false
			}
			if policy == "healthy" {
				context.PropertyFailed = false
			}
			if err = r.Observe(context, value); err != nil {
				t.Fatal(err)
			}
			firstEvents, _ := r.Drain()
			if policy == "ttl" && (len(firstEvents) < 1 || firstEvents[len(firstEvents)-1].Kind != "watch_expired" || firstEvents[len(firstEvents)-1].Node != nil) {
				t.Fatal("TTL did not expire at final observation", firstEvents)
			}
			before := calls
			context.Snapshot.Sequence = 1
			if err = r.Observe(context, value); err != nil {
				t.Fatal(err)
			}
			want := before
			if policy == "unknown" || policy == "healthy" {
				want++ // selected sampled boundaries still check schema
			}
			if calls != want {
				t.Fatalf("policy %s: callback count %d, want %d", policy, calls, want)
			}
			events, _ := r.Drain()
			if len(events) != 0 {
				t.Fatal(events)
			}
		})
	}
}

func TestFailureCaptureReusesValueAndHonorsRetentionBudget(t *testing.T) {
	limits := d.DefaultWatchLimits()
	limits.MaxRingRecords = 1
	r, err := d.NewWatchRegistry(limits, d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	config := d.ChangedWatch(nil)
	config.EvaluationInterval = 9
	config.DeliveryInterval = 9
	for i := 0; i < 3; i++ {
		if _, err = r.Add(config); err != nil {
			t.Fatal(err)
		}
	}
	r.Drain()
	calls := 0
	value := func() d.InspectAdapter { calls++; return numericProjection("1") }
	context := d.WatchContext{Snapshot: d.SnapshotID{Session: 1}, PropertyStatusKnown: true, PropertyFailed: true}
	if err = r.Observe(context, value); err != nil {
		t.Fatal(err)
	}
	if calls != 1 {
		t.Fatal(calls)
	}
	events, _ := r.Drain()
	if len(events) != 1 || !events[0].PropertyFailed || r.Health().DroppedTotal != 4 {
		t.Fatal(events, r.Health())
	}
}

type watchGapProjection struct {
	fault string
	value string
}

func (watchGapProjection) DisplaySchema() d.DisplaySchema { return d.DisplaySchema{Version: 1} }
func (v watchGapProjection) Inspect(path []d.Segment, c *d.InspectContext) (d.InspectNode, error) {
	switch v.fault {
	case "error":
		return d.InspectNode{}, errors.New("projection unavailable")
	case "redacted":
		return d.InspectNode{Kind: "redacted", Incomplete: "redacted"}, nil
	case "unavailable":
		return c.Unavailable(path)
	}
	return c.Scalar(path, d.Scalar{Kind: "u128", Decimal: v.value})
}

func TestFailureForcedEqualsWatchRetainsInspectionError(t *testing.T) {
	r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	config := d.ChangedWatch(nil)
	config.Trigger, config.Equals = "equals", &d.Scalar{Kind: "u128", Decimal: "7"}
	config.EvaluationInterval, config.DeliveryInterval = 5, 5
	if _, err = r.Add(config); err != nil {
		t.Fatal(err)
	}
	r.Drain()
	err = r.Observe(d.WatchContext{Snapshot: d.SnapshotID{Session: 1}, PropertyStatusKnown: true, PropertyFailed: true, FailingProperties: []string{"p"}}, func() d.InspectAdapter { return watchGapProjection{fault: "error"} })
	if err != nil {
		t.Fatal(err)
	}
	events, err := r.Drain()
	if err != nil || len(events) != 1 || events[0].Kind != "inspection_error" || !events[0].PropertyFailed || events[0].Node != nil {
		t.Fatalf("events=%#v err=%v", events, err)
	}
}
func TestWatchProjectionGapInvalidatesBaseline(t *testing.T) {
	for _, fault := range []string{"error", "redacted", "unavailable"} {
		for _, trigger := range []string{"changed", "above_u128"} {
			t.Run(fault+"/"+trigger, func(t *testing.T) {
				r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
				if err != nil {
					t.Fatal(err)
				}
				config := d.ChangedWatch(nil)
				config.Trigger = trigger
				config.EvaluationInterval = 2
				config.Threshold = d.UInt128{Lo: 1}
				if _, err = r.Add(config); err != nil {
					t.Fatal(err)
				}
				r.Drain()
				for i, projection := range []watchGapProjection{{value: "2"}, {fault: fault}, {value: "3"}} {
					context := d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: uint64(i)}, PropertyStatusKnown: true, PropertyFailed: i == 1}
					if err = r.Observe(context, func() d.InspectAdapter { return projection }); err != nil {
						t.Fatal(err)
					}
				}
				events, _ := r.Drain()
				if len(events) != 3 {
					t.Fatalf("history lost: %#v", events)
				}
				event := events[2]
				if event.Kind != "baseline_observation_gap" || event.Diff != d.DiffUnknown || event.PreviousSnapshot != nil {
					t.Fatalf("false comparison across %s: %#v", fault, event)
				}
				if events[0].Kind != "baseline_initial" || events[0].Node == nil {
					t.Fatal("initial history changed", events[0])
				}
				if fault == "error" && r.Health().InspectionFailures != 1 {
					t.Fatal(r.Health())
				}
			})
		}
	}
}

func TestWatchExpiresOnSampledFinalObservation(t *testing.T) {
	r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	zero := uint64(0)
	invalid := d.ChangedWatch(nil)
	invalid.TTLObservations = &zero
	if _, err = r.Add(invalid); err == nil {
		t.Fatal("zero TTL accepted")
	}
	ttl := uint64(2)
	config := d.ChangedWatch(nil)
	config.TTLObservations = &ttl
	config.EvaluationInterval = 2
	if _, err = r.Add(config); err != nil {
		t.Fatal(err)
	}
	r.Drain()
	calls := 0
	value := func() d.InspectAdapter { calls++; return numericProjection("1") }
	for seq := uint64(1); seq <= 2; seq++ {
		if err = r.Observe(d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: seq}}, value); err != nil {
			t.Fatal(err)
		}
		events, _ := r.Drain()
		if seq == 2 && (len(events) != 1 || events[0].Kind != "watch_expired") {
			t.Fatal("final sampled boundary", events)
		}
	}
	if calls != 2 {
		t.Fatal("schema was not checked at sampled boundary", calls)
	}
	if err = r.Observe(d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: 3}}, value); err != nil {
		t.Fatal(err)
	}
	if events, _ := r.Drain(); len(events) != 0 || calls != 2 {
		t.Fatal("expired watch remained active", events, calls)
	}
}

func TestSampledSourceResetFollowedByIncompleteProjection(t *testing.T) {
	r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	config := d.ChangedWatch(nil)
	config.EvaluationInterval = 2
	if _, err = r.Add(config); err != nil {
		t.Fatal(err)
	}
	_, _ = r.Drain()
	for i, step := range []struct{ model, fault string }{
		{"a", ""}, {"b", ""}, {"a", "unavailable"}, {"a", ""}, {"a", ""},
	} {
		ctx := d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: uint64(i + 1)}, Model: step.model}
		if err = r.Observe(ctx, func() d.InspectAdapter { return watchGapProjection{fault: step.fault, value: "1"} }); err != nil {
			t.Fatal(err)
		}
	}
	events, err := r.Drain()
	if err != nil || len(events) != 3 || events[1].Kind != "unknown" || events[2].Kind != "baseline_observation_gap" {
		t.Fatalf("events=%#v err=%v", events, err)
	}
}

func TestWatchTTLUsesRetainedIncompleteOrErrorObservation(t *testing.T) {
	for _, fault := range []string{"error", "unavailable"} {
		t.Run(fault, func(t *testing.T) {
			r, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
			if err != nil {
				t.Fatal(err)
			}
			config := d.ChangedWatch(nil)
			config.EvaluationInterval = 3
			ttl := uint64(1)
			config.BaselineTTLSequences = &ttl
			if _, err = r.Add(config); err != nil {
				t.Fatal(err)
			}
			_, _ = r.Drain()
			for sequence := uint64(1); sequence <= 4; sequence++ {
				projection := watchGapProjection{value: "1"}
				if sequence == 1 {
					projection.fault = fault
				}
				ctx := d.WatchContext{Snapshot: d.SnapshotID{Session: 1, Sequence: sequence}}
				if err := r.Observe(ctx, func() d.InspectAdapter { return projection }); err != nil {
					t.Fatal(err)
				}
			}
			events, err := r.Drain()
			if err != nil || len(events) != 2 || events[1].Kind != "baseline_expired" {
				t.Fatalf("events=%#v err=%v", events, err)
			}
		})
	}
}

func TestWatchAuthorizationUsesTypedSegmentIdentity(t *testing.T) {
	key := d.Segment{Kind: "map_key", KeyType: "u8", Decimal: "label"}
	auth := d.AllowWatchPaths([][]d.Segment{{key}})
	registry, err := d.NewWatchRegistry(d.DefaultWatchLimits(), auth)
	if err != nil {
		t.Fatal(err)
	}
	// Fields outside the active key variant are not part of Rust MapKey identity.
	decorated := key
	decorated.Name, decorated.Index = "ignored", 17
	if _, err := registry.Add(d.ChangedWatch([]d.Segment{decorated})); err != nil {
		t.Fatalf("same typed key rejected: %v", err)
	}
	different := key
	different.Decimal = "other"
	if _, err := registry.Add(d.ChangedWatch([]d.Segment{different})); err == nil {
		t.Fatal("different key authorized")
	}
	malformed := d.Segment{Kind: "map_key", KeyType: "bool", Decimal: "not-bool"}
	all, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := all.Add(d.ChangedWatch([]d.Segment{malformed})); err == nil {
		t.Fatal("malformed key accepted")
	}
}

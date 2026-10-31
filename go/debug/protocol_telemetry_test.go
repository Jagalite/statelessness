package debug_test

import (
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

func TestObserverAttachmentRejectsOversizedBudgetsAtomically(t *testing.T) {
	for _, detail := range []bool{false, true} {
		p, _, _ := probeFixture(t, d.DefaultProtocolLimits())
		options := d.DefaultEffectOptions()
		options.Timings = false
		if detail {
			options.MaxDetailBytes++
		} else {
			options.MetricLimits.MaxBytes++
		}
		observer, err := d.NewEffectObserver(options, nil)
		if err != nil {
			t.Fatal(err)
		}
		if err = p.AttachEffectObserver(observer); err != d.ProtocolError("limit") {
			t.Fatal("oversized observer accepted", err)
		}
		status := p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: 1, Command: "status"})
		for _, f := range status.Fields {
			if (f.Key == "capability.metric_queries" || f.Key == "capability.effect_telemetry") && f.Value != "bool:false" {
				t.Fatal("rejected attachment changed capabilities", status)
			}
		}
		options = d.DefaultEffectOptions()
		options.Timings = false
		observer, err = d.NewEffectObserver(options, nil)
		if err != nil {
			t.Fatal(err)
		}
		if err = p.AttachEffectObserver(observer); err != nil {
			t.Fatal("rejected attachment occupied host", err)
		}
	}
}

func TestObserverAttachmentMetricsAndHealth(t *testing.T) {
	p, _, _ := probeFixture(t, d.DefaultProtocolLimits())
	options := d.DefaultEffectOptions()
	options.Timings = false
	observer, err := d.NewEffectObserver(options, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err = p.AttachEffectObserver(observer); err != nil {
		t.Fatal(err)
	}
	if err = p.AttachEffectObserver(observer); err == nil {
		t.Fatal("duplicate attachment accepted")
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	fields := func(r d.ProtocolResponse) map[string]string {
		out := map[string]string{}
		for _, f := range r.Fields {
			out[f.Key] = f.Value
		}
		return out
	}
	status := send("status")
	s := fields(status)
	if status.Error != "" || s["capability.metric_queries"] != "bool:true" || s["capability.effect_telemetry"] != "bool:true" {
		t.Fatal(status)
	}
	catalog := send("metric_catalog", "u64:0", "u64:1")
	c := fields(catalog)
	if catalog.Error != "" || c["catalog.0.family"] != "enum:Requests" || c["catalog.0.name"] != "string:effect_requests_total" || c["catalog.next_offset"] != "u64:1" {
		t.Fatal(catalog)
	}
	snap := send("metric_snapshot", "none", "u64:0", "u64:1")
	f := fields(snap)
	if snap.Error != "" || f["metrics.revision"] != "u64:1" || f["metrics.series.returned"] != "u64:0" {
		t.Fatal(snap)
	}
	handle := f["metrics.handle"]
	replay := send("metric_snapshot", handle, "u64:0", "u64:1")
	if replay.Error != "" || fields(replay)["metrics.revision"] != "u64:1" {
		t.Fatal(replay)
	}
	if r := send("metric_snapshot", "u64:999", "u64:0", "u64:1"); r.Error != "stale_handle" {
		t.Fatal(r)
	}
	snap2 := send("metric_snapshot", "none", "u64:0", "u64:1")
	if snap2.Error != "" || fields(snap2)["metrics.revision"] != "u64:2" {
		t.Fatal(snap2)
	}
	window := send("metric_window", "u64:1", "u64:2", "u64:1")
	if window.Error != "" || fields(window)["metrics.temporality"] != "enum:Delta" {
		t.Fatal(window)
	}
	health := send("telemetry_health")
	h := fields(health)
	if health.Error != "" || h["metric_windows.total"] != "u64:2" || h["telemetry.clock_failures"] != "u64:0" {
		t.Fatal(health)
	}
}

func TestObserverSnapshotPreflightDoesNotCommit(t *testing.T) {
	limits := d.DefaultProtocolLimits()
	limits.MaxFrame = 2048
	limits.MaxFields = 64
	p, _, _ := probeFixture(t, limits)
	options := d.DefaultEffectOptions()
	options.Timings = false
	options.Labels.Operations = []string{"one", "two", "three", "four"}
	observer, err := d.NewEffectObserver(options, nil)
	if err != nil {
		t.Fatal(err)
	}
	// Distinct series make a four-item page exceed the field budget.
	for i := uint16(0); i < 4; i++ {
		if _, err = observer.Requested(d.RequestOrigin{Run: 1, Epoch: 1, TransitionSequence: 1}, d.Labels{Origin: d.OriginLive, Operation: i}); err != nil {
			t.Fatal(err)
		}
	}
	if err = p.AttachEffectObserver(observer); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: "metric_snapshot", Args: args})
	}
	before := observer.AvailableWindows()
	// Rejection occurs at response encoding, after the capture was staged.
	rejected := send("none", "u64:0", "u64:4")
	if rejected.Error != "limit" {
		t.Fatal(rejected)
	}
	if len(observer.AvailableWindows()) != len(before) {
		t.Fatal("rejection committed window")
	}
	ok := send("none", "u64:0", "u64:1")
	if ok.Error != "" {
		t.Fatal(ok)
	}
	if len(observer.AvailableWindows()) != len(before)+1 {
		t.Fatal("successful capture missing window")
	}
}

func TestObserverAttachmentEffectEvidence(t *testing.T) {
	p, _, _ := probeFixture(t, d.DefaultProtocolLimits())
	options := d.DefaultEffectOptions()
	options.Timings = false
	o, err := d.NewEffectObserver(options, nil)
	if err != nil {
		t.Fatal(err)
	}
	h, err := o.Requested(d.RequestOrigin{Run: 1, Epoch: 1, Machine: 7, TransitionSequence: 3}, d.Labels{})
	if err != nil {
		t.Fatal(err)
	}
	if err = o.Admission(h, true); err != nil {
		t.Fatal(err)
	}
	attempt, err := o.AttemptCreated(h)
	if err != nil {
		t.Fatal(err)
	}
	if err = o.RetryScheduled(attempt, 17); err != nil {
		t.Fatal(err)
	}
	if err = o.ReadyQueued(attempt); err != nil {
		t.Fatal(err)
	}
	if err = o.AttemptStarted(attempt); err != nil {
		t.Fatal(err)
	}
	if err = o.AttemptFinished(attempt, d.OutcomeSuccess); err != nil {
		t.Fatal(err)
	}
	if err = o.Resolved(h, d.OutcomeSuccess, &attempt); err != nil {
		t.Fatal(err)
	}
	delivery, err := o.ReservePublication(h)
	if err != nil {
		t.Fatal(err)
	}
	if err = o.DeliveryBegun(delivery); err != nil {
		t.Fatal(err)
	}
	if err = o.DeliveryObserved(delivery, d.OutcomeIgnored); err != nil {
		t.Fatal(err)
	}
	if err = o.Settled(h); err != nil {
		t.Fatal(err)
	}
	if err = p.AttachEffectObserver(o); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	fields := func(r d.ProtocolResponse) map[string]string {
		result := map[string]string{}
		for _, f := range r.Fields {
			result[f.Key] = f.Value
		}
		return result
	}
	result := send("effect_details", "u64:1", "u64:1", "u64:1")
	f := fields(result)
	for key, want := range map[string]string{"effect.admission": "enum:Accepted", "effect.resolution": "enum:Success", "effect.running_attempts": "u64:0", "effect.deliveries_observed": "u64:1", "effect.settled": "bool:true", "effect.complete": "bool:true", "effect.origin.machine": "u64:7"} {
		if result.Error != "" || f[key] != want {
			t.Fatal(key, result)
		}
	}
	timeline := send("effect_timeline", "u64:1", "u64:1", "u64:1", "u64:20")
	f = fields(timeline)
	for key, want := range map[string]string{"timeline.events.total": "u64:12", "timeline.events.2.attempt": "u64:1", "timeline.events.3.delay_ns": "u64:17", "timeline.events.5.attempt": "u64:1", "timeline.events.6.outcome": "enum:Success", "timeline.events.8.delivery": "u64:1", "timeline.events.10.disposition": "enum:Ignored"} {
		if timeline.Error != "" || f[key] != want {
			t.Fatal(key, timeline)
		}
	}
	if r := send("effect_details", "u64:99", "u64:1", "u64:1"); r.Error != "wrong_epoch" {
		t.Fatal(r)
	}
	if r := send("effect_details", "u64:1", "u64:1", "u64:99"); r.Error != "stale_handle" {
		t.Fatal(r)
	}
	snapshot, ok := o.EffectDetails(h.ID())
	if !ok || snapshot.Events[2].Attempt == nil {
		t.Fatal(snapshot)
	}
	snapshot.Events[2].Attempt.Serial = 999
	again, _ := o.EffectDetails(h.ID())
	if again.Events[2].Attempt.Serial != 1 {
		t.Fatal("event identity alias")
	}
}

func TestObserverDetailsRemainTruthfulAfterEventLoss(t *testing.T) {
	p, _, _ := probeFixture(t, d.DefaultProtocolLimits())
	options := d.DefaultEffectOptions()
	options.Timings = false
	options.MaxEventsPerEffect = 1
	o, err := d.NewEffectObserver(options, nil)
	if err != nil {
		t.Fatal(err)
	}
	h, err := o.Requested(d.RequestOrigin{Run: 1, Epoch: 1, TransitionSequence: 1}, d.Labels{})
	if err != nil {
		t.Fatal(err)
	}
	if err = o.Admission(h, true); err != nil {
		t.Fatal(err)
	}
	attempt, err := o.AttemptCreated(h)
	if err != nil {
		t.Fatal(err)
	}
	if err = o.AttemptStarted(attempt); err != nil {
		t.Fatal(err)
	}
	if err = p.AttachEffectObserver(o); err != nil {
		t.Fatal(err)
	}
	req := d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: 1, Command: "effect_details", Args: []string{"u64:1", "u64:1", "u64:1"}}
	r := p.Handle(req)
	fields := map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["effect.complete"] != "bool:false" || fields["effect.admission"] != "enum:Accepted" || fields["effect.running_attempts"] != "u64:1" {
		t.Fatal(r)
	}
	health := o.TelemetryHealth()
	if health.DetailEventDrops == 0 {
		t.Fatal("event loss hidden", health)
	}
}

func TestObserverReadyRemovalPreservesReferenceTimeline(t *testing.T) {
	p, _, _ := probeFixture(t, d.DefaultProtocolLimits())
	clock := &fakeClock{domain: 1}
	options := d.DefaultEffectOptions()
	options.MaxEventsPerEffect = 6
	observer, err := d.NewEffectObserver(options, clock)
	if err != nil {
		t.Fatal(err)
	}
	h, err := observer.Requested(d.RequestOrigin{Run: 1, Epoch: 1, TransitionSequence: 1}, d.Labels{})
	if err != nil {
		t.Fatal(err)
	}
	if err = observer.Admission(h, true); err != nil {
		t.Fatal(err)
	}
	attempt, err := observer.AttemptCreated(h)
	if err != nil {
		t.Fatal(err)
	}
	if err = observer.ReadyQueued(attempt); err != nil {
		t.Fatal(err)
	}
	reads := clock.reads
	if err = observer.ReadyRemoved(attempt); err != nil {
		t.Fatal(err)
	}
	if clock.reads != reads {
		t.Fatal("ready removal read clock", reads, clock.reads)
	}
	if err = observer.ReadyQueued(attempt); err != nil {
		t.Fatal(err)
	}
	if err = observer.AttemptStarted(attempt); err != nil {
		t.Fatal(err)
	}
	if health := observer.TelemetryHealth(); health.DetailEventDrops != 0 {
		t.Fatal("ready removal consumed event capacity", health)
	}
	if err = p.AttachEffectObserver(observer); err != nil {
		t.Fatal(err)
	}
	r := p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: 1, Command: "effect_timeline", Args: []string{"u64:1", "u64:1", "u64:1", "u64:10"}})
	fields := map[string]string{}
	for _, field := range r.Fields {
		fields[field.Key] = field.Value
	}
	for key, want := range map[string]string{
		"effect.complete": "bool:true", "timeline.events.total": "u64:6",
		"timeline.events.3.kind": "enum:ready", "timeline.events.3.sequence": "u64:4",
		"timeline.events.4.kind": "enum:ready", "timeline.events.4.sequence": "u64:5",
		"timeline.events.5.kind": "enum:attempt_started", "timeline.events.5.sequence": "u64:6",
	} {
		if r.Error != "" || fields[key] != want {
			t.Fatal(key, r)
		}
	}
}

func TestObserverRetainedSmallWindowAfterLiveGrowth(t *testing.T) {
	limits := d.DefaultProtocolLimits()
	limits.MaxTelemetrySnapshot = 2048
	p, _, _ := probeFixture(t, limits)
	options := d.DefaultEffectOptions()
	options.Timings = false
	options.MaxDetailBytes = limits.MaxTelemetrySnapshot
	options.MetricLimits.MaxBytes = limits.MaxTelemetrySnapshot
	options.Labels.Operations = []string{"a", "b", "c", "d", "e", "f", "g", "h"}
	observer, err := d.NewEffectObserver(options, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err = p.AttachEffectObserver(observer); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	for i := 0; i < 2; i++ {
		if r := send("metric_snapshot", "none", "u64:0", "u64:1"); r.Error != "" {
			t.Fatal(r)
		}
	}
	for operation := uint16(0); operation < 8; operation++ {
		_, err = observer.Requested(d.RequestOrigin{Run: 1, Epoch: 1, TransitionSequence: 1}, d.Labels{Operation: operation})
		if err != nil {
			t.Fatal(err)
		}
	}
	if r := send("metric_snapshot", "none", "u64:0", "u64:1"); r.Error != "limit" {
		t.Fatal("expected live snapshot budget rejection", r)
	}
	window := send("metric_window", "u64:1", "u64:2", "u64:1")
	fields := map[string]string{}
	for _, f := range window.Fields {
		fields[f.Key] = f.Value
	}
	if window.Error != "" || fields["metrics.temporality"] != "enum:Delta" || fields["metrics.series.total"] != "u64:0" {
		t.Fatal("small retained window rejected after live growth", window)
	}
}

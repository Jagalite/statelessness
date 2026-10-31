package debug_test

import (
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

func TestScopedProbeNoInterestAndIndependentSinks(t *testing.T) {
	diagnostics, e := d.NewDiagnostics(d.DefaultDiagnosticLimits())
	if e != nil {
		t.Fatal(e)
	}
	a, e := diagnostics.AddSink(d.AllowAllWatches())
	if e != nil {
		t.Fatal(e)
	}
	b, e := diagnostics.AddSink(d.AllowAllWatches())
	if e != nil {
		t.Fatal(e)
	}
	config := d.ProbeSubscription{Sink: a, Site: "counter", MinimumSeverity: d.InfoSeverity, Scope: d.ScopeSelector{Machine: "selected"}, Watch: d.ChangedWatch(nil)}
	if _, e = diagnostics.Subscribe(diagnostics.CaptureRevision(), config); e != nil {
		t.Fatal(e)
	}
	config.Sink = b
	if _, e = diagnostics.Subscribe(diagnostics.CaptureRevision(), config); e != nil {
		t.Fatal(e)
	}
	diagnostics.DrainSink(a)
	diagnostics.DrainSink(b)
	calls := 0
	value := func() d.InspectAdapter { calls++; return textProjection("one") }
	if e = diagnostics.RegisterProducer(0, 1); e != nil {
		t.Fatal(e)
	}
	context := d.CaptureContext{Machine: "other", Snapshot: d.SnapshotID{Session: 1, Sequence: 0}}
	if e = diagnostics.Probe(context, "counter", d.InfoSeverity, value); e != nil || calls != 0 {
		t.Fatal(e, calls)
	}
	if _, e = diagnostics.Acknowledge(0, diagnostics.CaptureRevision(), 1); e != nil {
		t.Fatal(e)
	}
	context.Snapshot.Sequence = 1
	context.Machine = "selected"
	if e = diagnostics.Probe(context, "counter", d.DebugSeverity, value); e != nil || calls != 0 {
		t.Fatal(e, calls)
	}
	if e = diagnostics.Probe(context, "counter", d.InfoSeverity, value); e != nil || calls != 1 {
		t.Fatal(e, calls)
	}
	if e = diagnostics.ExportSink(a, faultWriter{}); e == nil {
		t.Fatal("export fault")
	}
	events, e := diagnostics.DrainSink(b)
	if e != nil || len(events) != 1 || events[0].CaptureRevision != 2 {
		t.Fatal(events, e)
	}
	events, e = diagnostics.DrainSink(a)
	if e != nil || len(events) != 1 {
		t.Fatal(events, e)
	}
}
func TestProducerAcknowledgementAndUnknownProperty(t *testing.T) {
	diagnostics, e := d.NewDiagnostics(d.DefaultDiagnosticLimits())
	if e != nil {
		t.Fatal(e)
	}
	sink, e := diagnostics.AddSink(d.AllowAllWatches())
	if e != nil {
		t.Fatal(e)
	}
	success := false
	config := d.ProbeSubscription{Sink: sink, Site: "site", Scope: d.ScopeSelector{PropertyFailed: &success}, Watch: d.ChangedWatch(nil)}
	id, e := diagnostics.Subscribe(0, config)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = diagnostics.Subscribe(0, config); e == nil {
		t.Fatal("stale config revision")
	}
	if e = diagnostics.RegisterProducer(9, 1); e != nil {
		t.Fatal(e)
	}
	if _, e = diagnostics.Acknowledge(9, 1, 10); e != nil {
		t.Fatal(e)
	}
	calls := 0
	value := func() d.InspectAdapter { calls++; return textProjection("one") }
	context := d.CaptureContext{Producer: 9, Snapshot: d.SnapshotID{Session: 1, Sequence: 9}}
	diagnostics.Probe(context, "site", d.InfoSeverity, value)
	if calls != 0 {
		t.Fatal("before effective boundary")
	}
	context.Snapshot.Sequence = 10
	diagnostics.Probe(context, "site", d.InfoSeverity, value)
	if calls != 0 {
		t.Fatal("unknown property treated passing")
	}
	context.PropertyStatusKnown = true
	diagnostics.Probe(context, "site", d.InfoSeverity, value)
	if calls != 1 {
		t.Fatal(calls)
	}
	if e = diagnostics.Unsubscribe(1, id); e != nil {
		t.Fatal(e)
	}
	context.Snapshot.Sequence = 11
	diagnostics.Probe(context, "site", d.InfoSeverity, value)
	if calls != 2 {
		t.Fatal("unacknowledged producer did not retain config", calls)
	}
	if _, e = diagnostics.Acknowledge(9, 2, 12); e != nil {
		t.Fatal(e)
	}
	context.Snapshot.Sequence = 12
	diagnostics.Probe(context, "site", d.InfoSeverity, value)
	if calls != 2 {
		t.Fatal("acknowledged removal", calls)
	}
	if e = diagnostics.ExportSink(sink, faultWriter{}); e == nil {
		t.Fatal("fault hidden")
	}
	health, e := diagnostics.SinkHealth(sink)
	if e != nil || health.ExporterFailures != 1 {
		t.Fatal(health, e)
	}
}
func TestProducerBaselineIsolationAndPostEvaluationSampling(t *testing.T) {
	diagnostics, e := d.NewDiagnostics(d.DefaultDiagnosticLimits())
	if e != nil {
		t.Fatal(e)
	}
	sink, e := diagnostics.AddSink(d.AllowAllWatches())
	if e != nil {
		t.Fatal(e)
	}
	config := d.ProbeSubscription{Sink: sink, Site: "site", SampleEvery: 2, Watch: d.ChangedWatch(nil)}
	config.Watch.EvaluationInterval = 2
	id, e := diagnostics.Subscribe(0, config)
	if e != nil {
		t.Fatal(e)
	}
	diagnostics.DrainSink(sink)
	for _, producer := range []uint64{1, 2} {
		if e = diagnostics.RegisterProducer(producer, 1); e != nil {
			t.Fatal(e)
		}
		if _, e = diagnostics.Acknowledge(producer, 1, 0); e != nil {
			t.Fatal(e)
		}
	}
	calls := 0
	value := func() d.InspectAdapter { calls++; return textProjection("same") }
	for _, producer := range []uint64{1, 2} {
		context := d.CaptureContext{Producer: producer, Snapshot: d.SnapshotID{Session: producer, Sequence: 1}}
		if e = diagnostics.Probe(context, "site", d.InfoSeverity, value); e != nil {
			t.Fatal(e)
		}
	}
	events, e := diagnostics.DrainSink(sink)
	if e != nil || len(events) != 2 || calls != 2 {
		t.Fatal("producer baseline isolation", events, e, calls)
	}
	context := d.CaptureContext{Producer: 1, Snapshot: d.SnapshotID{Session: 1, Sequence: 2}}
	if e = diagnostics.Probe(context, "site", d.InfoSeverity, value); e != nil || calls != 3 {
		t.Fatal("sampling skipped evaluation", e, calls)
	}
	if e = diagnostics.Unsubscribe(1, id); e != nil {
		t.Fatal(e)
	}
	for _, producer := range []uint64{1, 2} {
		if _, e = diagnostics.Acknowledge(producer, 2, 3); e != nil {
			t.Fatal(e)
		}
		context.Producer = producer
		context.Snapshot = d.SnapshotID{Session: producer, Sequence: 3}
		diagnostics.Probe(context, "site", d.InfoSeverity, value)
	}
	if _, e = diagnostics.Subscribe(2, config); e != nil {
		t.Fatal("retired subscriptions not reclaimed", e)
	}
}

func TestAtomicConfigurePreservesProducerBoundary(t *testing.T) {
	hub, err := d.NewDiagnostics(d.DefaultDiagnosticLimits())
	if err != nil {
		t.Fatal(err)
	}
	sink, err := hub.AddSink(d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	first := d.ProbeSubscription{Sink: sink, Site: "first", Watch: d.ChangedWatch(nil)}
	second := d.ProbeSubscription{Sink: sink, Site: "second", Watch: d.ChangedWatch(nil)}
	ack, err := hub.ConfigureSubscriptions(0, []d.ProbeSubscription{first})
	if err != nil || ack.CaptureRevision != 1 {
		t.Fatal(ack, err)
	}
	if err = hub.RegisterProducer(7, 1); err != nil {
		t.Fatal(err)
	}
	if _, err = hub.Acknowledge(7, 1, 1); err != nil {
		t.Fatal(err)
	}
	calls := 0
	value := func() d.InspectAdapter { calls++; return textProjection("one") }
	context := d.CaptureContext{Producer: 7, Snapshot: d.SnapshotID{Session: 1, Sequence: 1}}
	if err = hub.Probe(context, "first", d.InfoSeverity, value); err != nil || calls != 1 {
		t.Fatal(err, calls)
	}
	invalid := second
	invalid.Sink = sink + 1
	if _, err = hub.ConfigureSubscriptions(1, []d.ProbeSubscription{second, invalid}); err == nil || hub.CaptureRevision() != 1 {
		t.Fatal("partial invalid configuration", err)
	}
	ack, err = hub.ConfigureSubscriptions(1, []d.ProbeSubscription{second})
	if err != nil || ack.CaptureRevision != 2 || len(ack.PendingProducers) != 1 || ack.PendingProducers[0] != 7 {
		t.Fatal(ack, err)
	}
	context.Snapshot.Sequence = 2
	if err = hub.Probe(context, "first", d.InfoSeverity, value); err != nil || calls != 2 {
		t.Fatal("previous configuration was lost", err, calls)
	}
	if err = hub.Probe(context, "second", d.InfoSeverity, value); err != nil || calls != 2 {
		t.Fatal("configuration activated before acknowledgement", err, calls)
	}
	if _, err = hub.Acknowledge(7, 2, 3); err != nil {
		t.Fatal(err)
	}
	context.Snapshot.Sequence = 3
	if err = hub.Probe(context, "first", d.InfoSeverity, value); err != nil || calls != 2 {
		t.Fatal("old configuration survived boundary", err, calls)
	}
	if err = hub.Probe(context, "second", d.InfoSeverity, value); err != nil || calls != 3 {
		t.Fatal("new configuration absent", err, calls)
	}
}

func TestAcknowledgementRejectsPastBoundaries(t *testing.T) {
	hub, err := d.NewDiagnostics(d.DefaultDiagnosticLimits())
	if err != nil {
		t.Fatal(err)
	}
	sink, err := hub.AddSink(d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	config := d.ProbeSubscription{Sink: sink, Site: "site", Watch: d.ChangedWatch(nil)}
	if _, err = hub.ConfigureSubscriptions(0, []d.ProbeSubscription{config}); err != nil {
		t.Fatal(err)
	}
	if err = hub.RegisterProducer(5, 1); err != nil {
		t.Fatal(err)
	}
	if _, err = hub.Acknowledge(5, 1, 10); err != nil {
		t.Fatal(err)
	}
	context := d.CaptureContext{Producer: 5, Snapshot: d.SnapshotID{Session: 1, Sequence: 9}}
	calls := 0
	project := func() d.InspectAdapter { calls++; return textProjection("x") }
	if err = hub.Probe(context, "site", d.InfoSeverity, project); err == nil || calls != 0 {
		t.Fatal("pre-boundary capture", err, calls)
	}
	context.Snapshot.Sequence = 10
	if err = hub.Probe(context, "site", d.InfoSeverity, project); err != nil || calls != 1 {
		t.Fatal(err, calls)
	}
	if _, err = hub.Acknowledge(5, 1, 10); err == nil {
		t.Fatal("accepted non-advancing acknowledgement")
	}
	context.Snapshot.Sequence = 11
	if err = hub.Probe(context, "site", d.InfoSeverity, project); err != nil || calls != 2 {
		t.Fatal("failed acknowledgement changed producer", err, calls)
	}
}

func TestExplicitProducerRegistrationAndStatus(t *testing.T) {
	hub, err := d.NewDiagnostics(d.DefaultDiagnosticLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err = hub.RegisterProducer(9, 0); err == nil {
		t.Fatal("zero epoch accepted")
	}
	if err = hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	if err = hub.RegisterProducer(9, 8); err == nil {
		t.Fatal("duplicate producer accepted")
	}
	if err = hub.RegisterProducer(2, 4); err != nil {
		t.Fatal(err)
	}
	ack, err := hub.ConfigureSubscriptions(0, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(ack.PendingProducers) != 2 || ack.PendingProducers[0] != 2 || ack.PendingProducers[1] != 9 {
		t.Fatal(ack)
	}
	if _, err = hub.Acknowledge(9, 1, 11); err != nil {
		t.Fatal(err)
	}
	statuses := hub.ProducerStatus()
	if len(statuses) != 2 || statuses[0].Producer != 2 || statuses[1].Epoch != 7 || statuses[1].CaptureRevision != 1 || statuses[1].EffectiveSequence != 11 {
		t.Fatal(statuses)
	}
	statuses[1].Epoch = 99
	if hub.ProducerStatus()[1].Epoch != 7 {
		t.Fatal("status aliases producer")
	}
}

func TestUnacknowledgedProducerDoesNotRetainUnseenConfigurations(t *testing.T) {
	limits := d.DefaultDiagnosticLimits()
	limits.MaxSubscriptions = 1
	limits.MaxProducers = 1
	hub, err := d.NewDiagnostics(limits)
	if err != nil {
		t.Fatal(err)
	}
	sink, err := hub.AddSink(d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	if err = hub.RegisterProducer(1, 1); err != nil {
		t.Fatal(err)
	}
	config := d.ProbeSubscription{Sink: sink, Site: "site", Watch: d.ChangedWatch(nil)}
	for revision := uint64(0); revision < 10; revision++ {
		ack, err := hub.ConfigureSubscriptions(revision, []d.ProbeSubscription{config})
		if err != nil || ack.CaptureRevision != revision+1 {
			t.Fatalf("revision %d: %v", revision, err)
		}
	}
	if _, err = hub.Acknowledge(1, 10, 1); err != nil {
		t.Fatal(err)
	}
	calls := 0
	project := func() d.InspectAdapter { calls++; return textProjection("current") }
	ctx := d.CaptureContext{Producer: 1, Snapshot: d.SnapshotID{Session: 1, Sequence: 1}}
	if err = hub.Probe(ctx, "site", d.InfoSeverity, project); err != nil || calls != 1 {
		t.Fatal(err, calls)
	}
}

func TestAtomicConfigurationOwnsHostSelectors(t *testing.T) {
	hub, err := d.NewDiagnostics(d.DefaultDiagnosticLimits())
	if err != nil {
		t.Fatal(err)
	}
	sink, err := hub.AddSink(d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	producer := uint64(7)
	config := d.ProbeSubscription{Sink: sink, Site: "site", Scope: d.ScopeSelector{Producer: &producer}, Watch: d.ChangedWatch(nil)}
	if _, err = hub.ConfigureSubscriptions(0, []d.ProbeSubscription{config}); err != nil {
		t.Fatal(err)
	}
	producer = 8
	if err = hub.RegisterProducer(7, 1); err != nil {
		t.Fatal(err)
	}
	if _, err = hub.Acknowledge(7, 1, 1); err != nil {
		t.Fatal(err)
	}
	calls := 0
	if err = hub.Probe(d.CaptureContext{Producer: 7, Snapshot: d.SnapshotID{Session: 1, Sequence: 1}}, "site", d.InfoSeverity, func() d.InspectAdapter { calls++; return textProjection("owned") }); err != nil || calls != 1 {
		t.Fatal("selector was not owned", err, calls)
	}
}

func TestMarkerSamplingIsPerProducerAndResetsOnAcknowledgement(t *testing.T) {
	hub, err := d.NewDiagnostics(d.DefaultDiagnosticLimits())
	if err != nil {
		t.Fatal(err)
	}
	sink, err := hub.AddSink(d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	config := d.ProbeSubscription{Sink: sink, Site: "marker", Kind: "marker", SampleEvery: 2, Watch: d.ChangedWatch(nil)}
	if _, err = hub.ConfigureSubscriptions(0, []d.ProbeSubscription{config}); err != nil {
		t.Fatal(err)
	}
	for _, id := range []uint64{1, 2} {
		if err = hub.RegisterProducer(id, 1); err != nil {
			t.Fatal(err)
		}
		if _, err = hub.Acknowledge(id, 1, 0); err != nil {
			t.Fatal(err)
		}
	}
	for _, id := range []uint64{1, 2} {
		for seq := uint64(1); seq <= 4; seq++ {
			if err = hub.Marker(d.CaptureContext{Producer: id, RunID: 99, Origin: "simulation", Snapshot: d.SnapshotID{Session: id, Sequence: seq}}, "marker", d.InfoSeverity); err != nil {
				t.Fatal(err)
			}
		}
	}
	events, err := hub.DrainSink(sink)
	if err != nil || len(events) != 4 {
		t.Fatal(events, err)
	}
	for index, event := range events {
		metadata := event.Diagnostic
		if metadata == nil || metadata.RunID != 99 || metadata.Epoch != 1 || metadata.Origin != "simulation" || metadata.Site != "marker" || metadata.Producer != uint64(index/2+1) || metadata.ProducerSequence != uint64((index%2)*2+1) {
			t.Fatal("marker provenance", event)
		}
	}
	if _, err = hub.Acknowledge(1, 1, 5); err != nil {
		t.Fatal(err)
	}
	if err = hub.Marker(d.CaptureContext{Producer: 1, Snapshot: d.SnapshotID{Session: 1, Sequence: 5}}, "marker", d.InfoSeverity); err != nil {
		t.Fatal(err)
	}
	events, err = hub.DrainSink(sink)
	if err != nil || len(events) != 1 {
		t.Fatal("acknowledgement must reset sampling", events, err)
	}
	if events[0].Diagnostic == nil || events[0].Diagnostic.BaselineReason != "reconfigured" {
		t.Fatal("acknowledgement must retain reconfiguration provenance", events)
	}
}

func TestUnregisteredProducerCannotAcknowledgeOrCapture(t *testing.T) {
	hub, err := d.NewDiagnostics(d.DefaultDiagnosticLimits())
	if err != nil {
		t.Fatal(err)
	}
	if _, err = hub.Acknowledge(42, 0, 0); err == nil {
		t.Fatal("unregistered acknowledgement accepted")
	}
	calls := 0
	ctx := d.CaptureContext{Producer: 42, Snapshot: d.SnapshotID{Session: 1, Sequence: 1}}
	if err = hub.Probe(ctx, "site", d.InfoSeverity, func() d.InspectAdapter { calls++; return textProjection("unexpected") }); err == nil || calls != 0 {
		t.Fatal("unregistered probe", err, calls)
	}
	if err = hub.Marker(ctx, "site", d.InfoSeverity); err == nil {
		t.Fatal("unregistered marker accepted")
	}
	if err = hub.DropScope(ctx, "site"); err == nil {
		t.Fatal("unregistered gap accepted")
	}
	if len(hub.ProducerStatus()) != 0 {
		t.Fatal("unknown producer was implicitly registered")
	}
}

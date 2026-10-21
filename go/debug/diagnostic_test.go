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
	context := d.CaptureContext{Machine: "other", Snapshot: d.SnapshotID{Session: 1, Sequence: 1}}
	if e = diagnostics.Probe(context, "counter", d.InfoSeverity, value); e != nil || calls != 0 {
		t.Fatal(e, calls)
	}
	if _, e = diagnostics.Acknowledge(0, diagnostics.CaptureRevision(), 1); e != nil {
		t.Fatal(e)
	}
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

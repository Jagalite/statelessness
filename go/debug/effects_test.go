package debug_test

import (
	"errors"
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

type fakeClock struct {
	ns, domain, reads uint64
	fail              bool
}

func (c *fakeClock) Now() (d.LocalInstant, error) {
	c.reads++
	if c.fail {
		return d.LocalInstant{}, errors.New("unavailable")
	}
	return d.LocalInstant{Domain: c.domain, Nanos: c.ns}, nil
}
func effectObserver(t *testing.T) (*d.EffectObserver, *fakeClock) {
	t.Helper()
	c := &fakeClock{domain: 1}
	o, e := d.NewEffectObserver(d.DefaultEffectOptions(), c)
	if e != nil {
		t.Fatal(e)
	}
	return o, c
}
func request(t *testing.T, o *d.EffectObserver) d.EffectHandle {
	t.Helper()
	h, e := o.Requested(d.RequestOrigin{Run: 1, Epoch: 1, Machine: 7, TransitionSequence: 1}, d.Labels{})
	if e != nil {
		t.Fatal(e)
	}
	return h
}
func histSum(s d.MetricSnapshot, f d.MetricFamily) (uint64, string) {
	for k, v := range s.Series {
		if k.Family == f && v.Histogram != nil {
			return v.Histogram.Count, v.Histogram.SumNS.String()
		}
	}
	return 0, "0"
}
func number(s d.MetricSnapshot, f d.MetricFamily) uint64 {
	var n uint64
	for k, v := range s.Series {
		if k.Family == f {
			n += v.Number
		}
	}
	return n
}

// Manual endpoint expectations, independently authored before Rust execution:
// ready=0,start=12ms,finish=resolve=96ms,post=97ms,begin=100ms,observed=102ms.
func TestEffectsManualEndpointGolden(t *testing.T) {
	o, c := effectObserver(t)
	h := request(t, o)
	if e := o.Admission(h, true); e != nil {
		t.Fatal(e)
	}
	a, e := o.AttemptCreated(h)
	if e != nil {
		t.Fatal(e)
	}
	if e = o.ReadyQueued(a); e != nil {
		t.Fatal(e)
	}
	c.ns = 12000000
	if e = o.AttemptStarted(a); e != nil {
		t.Fatal(e)
	}
	c.ns = 96000000
	if e = o.AttemptFinished(a, d.OutcomeSuccess); e != nil {
		t.Fatal(e)
	}
	if e = o.Resolved(h, d.OutcomeSuccess, &a); e != nil {
		t.Fatal(e)
	}
	c.ns = 97000000
	del, e := o.ReservePublication(h)
	if e != nil {
		t.Fatal(e)
	}
	if e = o.PublicationCommitted(del); e != nil {
		t.Fatal(e)
	}
	c.ns = 100000000
	if e = o.DeliveryBegun(del); e != nil {
		t.Fatal(e)
	}
	c.ns = 102000000
	if e = o.DeliveryObserved(del, d.OutcomeIgnored); e != nil {
		t.Fatal(e)
	}
	s, e := o.MetricSnapshot()
	if e != nil {
		t.Fatal(e)
	}
	for f, want := range map[d.MetricFamily]string{d.QueueWait: "12000000", d.AttemptElapsed: "84000000", d.Resolution: "96000000", d.CompletionPreparation: "1000000", d.CompletionQueueWait: "3000000", d.DeliveryLag: "4000000", d.CompletionProcessing: "2000000", d.EndToEnd: "100000000", d.EndToEndObserved: "102000000", d.ResolutionOverhead: "0"} {
		n, sum := histSum(s, f)
		if n != 1 || sum != want {
			t.Fatalf("%s count=%d sum=%s want=%s", f, n, sum, want)
		}
	}
	if n, _ := histSum(s, d.Settlement); n != 0 {
		t.Fatal("fabricated settlement")
	}
	before := c.reads
	timeline, ok := o.EffectTimeline(h.ID())
	if !ok || len(timeline.Events) == 0 {
		t.Fatal("missing details")
	}
	timeline.Events[0].Kind = "mutation"
	again, _ := o.EffectTimeline(h.ID())
	if again.Events[0].Kind == "mutation" {
		t.Fatal("alias")
	}
	q, e := o.SlowTimings(d.SlowTimingQuery{Operation: 0, Family: d.AttemptElapsed, ThresholdNS: 80000000, MaxResults: 1, MaxResultBytes: 1024})
	if e != nil || len(q.Matches) != 1 || c.reads != before {
		t.Fatal(q, e)
	}
	if e = o.DeliveryObserved(del, d.OutcomeIgnored); e == nil {
		t.Fatal("duplicate reducer observation")
	}
}
func TestEffectsCancellationIsNotTerminationAndEvictionIsNotAccounting(t *testing.T) {
	opts := d.DefaultEffectOptions()
	opts.MaxDetails = 1
	c := &fakeClock{}
	o, e := d.NewEffectObserver(opts, c)
	if e != nil {
		t.Fatal(e)
	}
	h := request(t, o)
	o.Admission(h, true)
	a, _ := o.AttemptCreated(h)
	o.AttemptStarted(a)
	o.CancellationRequested(h)
	o.CancellationAcknowledged(h)
	request(t, o)
	s, _ := o.MetricSnapshot()
	if number(s, d.Running) != 1 || number(s, d.Unresolved) != 1 || o.TelemetryHealth().DetailEvictions != 1 {
		t.Fatal(s, o.TelemetryHealth())
	}
	if e = o.AttemptFinished(a, d.OutcomeCancelled); e != nil {
		t.Fatal(e)
	}
	s, _ = o.MetricSnapshot()
	if number(s, d.Running) != 0 {
		t.Fatal("unfinished running")
	}
	other, _ := effectObserver(t)
	if e = other.CancellationRequested(h); e == nil {
		t.Fatal("foreign handle")
	}
}
func TestEffectsUnknownClockAndFailurePublication(t *testing.T) {
	o, c := effectObserver(t)
	h := request(t, o)
	o.Admission(h, true)
	a, _ := o.AttemptCreated(h)
	o.ReadyQueued(a)
	c.domain = 2
	c.ns = 5
	o.AttemptStarted(a)
	c.fail = true
	o.AttemptFinished(a, d.OutcomeFailure)
	o.Resolved(h, d.OutcomeFailure, &a)
	c.fail = false
	del, _ := o.ReservePublication(h)
	if e := o.PublicationFailed(del); e != nil {
		t.Fatal(e)
	}
	if e := o.DeliveryBegun(del); e == nil {
		t.Fatal("failed publication consumed")
	}
	s, _ := o.MetricSnapshot()
	if n, _ := histSum(s, d.QueueWait); n != 0 {
		t.Fatal("cross domain sample")
	}
	if number(s, d.CompletionDepth) != 0 || s.Complete || s.Health.InvalidMeasurements == 0 || o.TelemetryHealth().ClockFailures == 0 {
		t.Fatal(s.Health, o.TelemetryHealth())
	}
}
func TestEffectsBoundedDetailAndExplicitAbandonment(t *testing.T) {
	opts := d.DefaultEffectOptions()
	opts.MaxEventsPerEffect = 1
	opts.MaxDetailBytes = 512
	c := &fakeClock{}
	o, _ := d.NewEffectObserver(opts, c)
	h := request(t, o)
	o.Admission(h, true)
	a, _ := o.AttemptCreated(h)
	o.AttemptStarted(a)
	o.AttemptAbandoned(a)
	s, _ := o.MetricSnapshot()
	if number(s, d.Running) != 1 || s.Complete || o.TelemetryHealth().DetailEventDrops == 0 || o.TelemetryHealth().Abandoned != 1 {
		t.Fatal(s, o.TelemetryHealth())
	}
	if e := o.AttemptFinished(a, d.OutcomeCancelled); e != nil {
		t.Fatal("known later termination rejected", e)
	}
	s, _ = o.MetricSnapshot()
	if number(s, d.Running) != 0 {
		t.Fatal("later termination not accounted")
	}
}
func TestPauseGenerationAndExplicitSettlement(t *testing.T) {
	o, c := effectObserver(t)
	h := request(t, o)
	o.Admission(h, true)
	a, _ := o.AttemptCreated(h)
	o.ReadyQueued(a)
	o.AttemptStarted(a)
	c.ns = 1
	o.DebuggerPause(true)
	c.ns = 2
	o.DebuggerPause(false)
	c.ns = 3
	o.AttemptFinished(a, d.OutcomeSuccess)
	s, _ := o.MetricSnapshot()
	found := false
	for k, v := range s.Series {
		if k.Family == d.AttemptElapsed {
			found = k.Labels.DebuggerAffected && v.Histogram.Count == 1
		}
	}
	if !found {
		t.Fatal("missed pause wholly between host boundaries")
	}
	if e := o.Settled(h); e != nil {
		t.Fatal("explicit settlement rejected before completion", e)
	}
	if e := o.Settled(h); e == nil {
		t.Fatal("duplicate settlement")
	}
}
func TestRetryScheduleWithoutClockIsStillOnceOnly(t *testing.T) {
	opts := d.DefaultEffectOptions()
	opts.Timings = false
	o, e := d.NewEffectObserver(opts, nil)
	if e != nil {
		t.Fatal(e)
	}
	h := request(t, o)
	o.Admission(h, true)
	a, _ := o.AttemptCreated(h)
	if e = o.RetryScheduled(a, 7); e != nil {
		t.Fatal(e)
	}
	if e = o.RetryScheduled(a, 7); e == nil {
		t.Fatal("missing clock erased schedule fact")
	}
	o.AttemptAbandoned(a)
	if e = o.ReadyQueued(a); e == nil {
		t.Fatal("abandoned attempt queued")
	}
	unadmitted := request(t, o)
	if e = o.Resolved(unadmitted, d.OutcomeSuccess, nil); e == nil {
		t.Fatal("unadmitted resolution")
	}
}
func TestExporterFaultAndPendingAgeArePassive(t *testing.T) {
	o, c := effectObserver(t)
	h := request(t, o)
	detail, ok := o.EffectDetails(h.ID())
	if !ok {
		t.Fatal("no detail")
	}
	age, e := detail.PendingAge(d.LocalInstant{Domain: 1, Nanos: 7})
	if e != nil || age == nil || *age != 7 {
		t.Fatal(age, e)
	}
	if _, e = detail.PendingAge(d.LocalInstant{Domain: 2, Nanos: 7}); e == nil {
		t.Fatal("domain")
	}
	if e = o.ExportMetrics(func(s d.MetricSnapshot) error { panic("broken exporter") }); e == nil || o.TelemetryHealth().CallbackFaults != 1 {
		t.Fatal(e)
	}
	c.ns = 8
	if e = o.Settled(h); e != nil {
		t.Fatal(e)
	}
	if o.TelemetryHealth().InvalidLifecycle != 0 {
		t.Fatal("export mutated lifecycle")
	}
}

func TestRequestRejectsInvalidIdentityBeforeClock(t *testing.T) {
	o, clock := effectObserver(t)
	clock.fail = true
	before := clock.reads
	for _, origin := range []d.RequestOrigin{
		{Run: 2, Epoch: 1, TransitionSequence: 1},
		{Run: 1, Epoch: 2, TransitionSequence: 1},
		{Run: 1, Epoch: 1, TransitionSequence: 0},
	} {
		if _, err := o.Requested(origin, d.Labels{}); err == nil {
			t.Fatal("invalid origin accepted", origin)
		}
	}
	if clock.reads != before || o.TelemetryHealth().ClockFailures != 0 {
		t.Fatal("rejected request sampled clock", clock.reads, o.TelemetryHealth())
	}
	if _, err := o.Requested(d.RequestOrigin{Run: 1, Epoch: 1, TransitionSequence: 1}, d.Labels{}); err != nil {
		t.Fatal(err)
	}
	if clock.reads != before+1 || o.TelemetryHealth().ClockFailures != 1 {
		t.Fatal("valid request did not sample clock")
	}
}

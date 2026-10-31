package debug_test

import (
	d "github.com/Jagalite/statelessness/go/debug"
	"sync/atomic"
	"testing"
)

type admissionGateClock struct {
	gate    atomic.Bool
	entered chan struct{}
	release chan struct{}
}

func (c *admissionGateClock) Now() (d.LocalInstant, error) {
	if c.gate.CompareAndSwap(true, false) {
		close(c.entered)
		<-c.release
	}
	return d.LocalInstant{Domain: 1, Nanos: 1}, nil
}

func TestEffectInvalidLifecycleDoesNotSampleClock(t *testing.T) {
	o, clock := effectObserver(t)
	other, _ := effectObserver(t)
	foreign := request(t, other)
	h := request(t, o)
	if err := o.Admission(h, true); err != nil {
		t.Fatal(err)
	}
	a, err := o.AttemptCreated(h)
	if err != nil {
		t.Fatal(err)
	}
	before := clock.reads
	tests := []func() error{
		func() error { return o.Admission(foreign, true) },
		func() error { return o.Admission(h, true) },
		func() error { return o.AttemptFinished(a, d.OutcomeSuccess) },
		func() error { return o.CancellationAcknowledged(h) },
		func() error { _, e := o.ReservePublication(h); return e },
		func() error { return o.PublicationFailed(d.DeliveryHandle{}) },
		func() error { return o.DeliveryBegun(d.DeliveryHandle{}) },
		func() error { return o.DeliveryObserved(d.DeliveryHandle{}, d.OutcomeAccepted) },
	}
	for i, check := range tests {
		if err := check(); err == nil {
			t.Fatal("accepted invalid", i)
		}
	}
	if clock.reads != before {
		t.Fatalf("invalid operations sampled clock: %d -> %d", before, clock.reads)
	}
}

func TestEffectRevalidatesAfterClockCallback(t *testing.T) {
	clock := &admissionGateClock{entered: make(chan struct{}), release: make(chan struct{})}
	o, err := d.NewEffectObserver(d.DefaultEffectOptions(), clock)
	if err != nil {
		t.Fatal(err)
	}
	h := request(t, o)
	clock.gate.Store(true)
	result := make(chan error, 1)
	go func() { result <- o.Admission(h, true) }()
	<-clock.entered
	if err := o.Admission(h, true); err != nil {
		t.Fatal(err)
	}
	close(clock.release)
	if err := <-result; err == nil {
		t.Fatal("concurrent duplicate committed")
	}
	if health := o.TelemetryHealth(); health.DuplicateObservations != 1 {
		t.Fatal(health)
	}
}

type mutatingClock struct{ callback func() }

func (c *mutatingClock) Now() (d.LocalInstant, error) {
	if c.callback != nil {
		callback := c.callback
		c.callback = nil
		callback()
	}
	return d.LocalInstant{Domain: 1, Nanos: 1}, nil
}
func TestResolutionSnapshotsSelectedAttemptBeforeClock(t *testing.T) {
	clock := &mutatingClock{}
	o, err := d.NewEffectObserver(d.DefaultEffectOptions(), clock)
	if err != nil {
		t.Fatal(err)
	}
	h := request(t, o)
	if err = o.Admission(h, true); err != nil {
		t.Fatal(err)
	}
	attempts := make([]d.AttemptHandle, 2)
	for i := range attempts {
		attempts[i], err = o.AttemptCreated(h)
		if err != nil {
			t.Fatal(err)
		}
		if err = o.AttemptStarted(attempts[i]); err != nil {
			t.Fatal(err)
		}
		if err = o.AttemptFinished(attempts[i], d.OutcomeSuccess); err != nil {
			t.Fatal(err)
		}
	}
	selected := attempts[0]
	clock.callback = func() { selected = attempts[1] }
	if err = o.Resolved(h, d.OutcomeSuccess, &selected); err != nil {
		t.Fatal(err)
	}
	detail, ok := o.EffectDetails(h.ID())
	if !ok {
		t.Fatal("missing detail")
	}
	for _, timing := range detail.Timings {
		if timing.Family == d.ResolutionOverhead {
			if timing.Attempt == nil || *timing.Attempt != attempts[0].ID() {
				t.Fatal("misattributed", timing)
			}
			return
		}
	}
	t.Fatal("missing resolution overhead")
}

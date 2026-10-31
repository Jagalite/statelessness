package debug_test

import (
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

func TestClockFailureMarksMetricsIncompleteOnlyForOmittedMeasurement(t *testing.T) {
	o, clock := effectObserver(t)
	clock.fail = true
	o.DebuggerPause(true)
	snapshot, err := o.MetricSnapshot()
	if err != nil {
		t.Fatal(err)
	}
	if !snapshot.Complete || snapshot.End != nil || snapshot.Health.OmittedMeasurements != 0 || o.TelemetryHealth().ClockFailures != 2 {
		t.Fatalf("failed metadata reads altered metric completeness: %+v", snapshot)
	}
	h, err := o.Requested(d.RequestOrigin{Run: 1, Epoch: 1, TransitionSequence: 1}, d.Labels{})
	if err != nil {
		t.Fatal(err)
	}
	if err = o.Admission(h, true); err != nil {
		t.Fatal(err)
	}
	if err = o.Resolved(h, d.OutcomeSuccess, nil); err != nil {
		t.Fatal(err)
	}
	snapshot, err = o.MetricSnapshot()
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Complete || snapshot.Health.OmittedMeasurements != 1 {
		t.Fatalf("missing timing endpoints not reflected in health: %+v", snapshot)
	}
}

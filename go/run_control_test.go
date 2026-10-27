package statelessness

import (
	"sync/atomic"
	"testing"
	"time"
)

func TestRunControlSnapshotsDurationAndKeepsCancellationLive(t *testing.T) {
	for _, test := range []struct {
		name             string
		initial, changed time.Duration
		want             string
	}{
		{"long-entry-budget-stays-long", time.Hour, 0, ""},
		{"expired-entry-budget-stays-expired", 0, time.Hour, "deadline"},
	} {
		t.Run(test.name, func(t *testing.T) {
			duration := test.initial
			var cancelled atomic.Bool
			control, e := newRunControl(RunLimits{MaxDuration: &duration, Cancellation: &cancelled})
			if e != nil {
				t.Fatal(e)
			}
			duration = test.changed
			if actual := control.stop(); actual != test.want {
				t.Fatal("duration mutation changed run policy", actual, test.want)
			}
			cancelled.Store(true)
			if actual := control.stop(); actual != "cancelled" {
				t.Fatal("cancellation no longer live", actual)
			}
		})
	}
}

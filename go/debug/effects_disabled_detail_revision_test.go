package debug_test

import (
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

func TestDisabledDetailsDoNotAdvanceDetailRevision(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		opts := d.DefaultEffectOptions()
		opts.Timings = false
		opts.Details = enabled
		o, err := d.NewEffectObserver(opts, nil)
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
		result, err := o.SlowTimings(d.SlowTimingQuery{Family: d.QueueWait, MaxResults: 1, MaxResultBytes: 4096})
		if err != nil {
			t.Fatal(err)
		}
		if (!enabled && result.DetailRevision != 0) || (enabled && result.DetailRevision != 2) {
			t.Fatalf("details=%v revision=%d", enabled, result.DetailRevision)
		}
	}
}

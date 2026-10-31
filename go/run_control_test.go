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

func TestRunControlExactNanosecondsAndCarry(t *testing.T) {
	budget := Uint128{Hi: 1, Lo: 30}
	control, err := newRunControl(RunLimits{MaxDurationNS: &budget})
	if err != nil {
		t.Fatal(err)
	}
	budget = Uint128{}
	control.exact.mu.Lock()
	control.exact.elapsed = Uint128{Lo: ^uint64(0) - 9}
	control.exact.last = time.Now().Add(-1000 * time.Nanosecond)
	control.exact.mu.Unlock()
	if got := control.stop(); got != "deadline" {
		t.Fatal("u128 carry did not expire", got)
	}
	long := Uint128{Hi: 1}
	control, err = newRunControl(RunLimits{MaxDurationNS: &long})
	if err != nil || control.stop() != "" {
		t.Fatal("large deadline expired immediately", err)
	}
	zero := Uint128{}
	var cancelled atomic.Bool
	cancelled.Store(true)
	control, err = newRunControl(RunLimits{MaxDurationNS: &zero, Cancellation: &cancelled})
	if err != nil || control.stop() != "cancelled" {
		t.Fatal("cancellation precedence", err)
	}
	cancelled.Store(false)
	if control.stop() != "deadline" {
		t.Fatal("zero deadline was ignored")
	}
	d := time.Second
	if _, err = newRunControl(RunLimits{MaxDuration: &d, MaxDurationNS: &zero}); err == nil {
		t.Fatal("conflicting duration forms accepted")
	}
}

func TestCampaignRemainingExactBorrowsAndExpires(t *testing.T) {
	maximum := Uint128{Hi: 1}
	control, err := newRunControl(RunLimits{MaxDurationNS: &maximum})
	if err != nil {
		t.Fatal(err)
	}
	control.exact.mu.Lock()
	control.exact.elapsed = Uint128{Lo: 5}
	control.exact.mu.Unlock()
	remaining := campaignRemaining(control)
	if remaining.MaxDurationNS == nil || remaining.MaxDurationNS.Hi != 0 || remaining.MaxDurationNS.Lo > ^uint64(0)-4 {
		t.Fatal("bad u128 subtraction", remaining.MaxDurationNS)
	}
	control.exact.mu.Lock()
	control.exact.elapsed = maximum
	control.exact.mu.Unlock()
	remaining = campaignRemaining(control)
	if remaining.MaxDurationNS == nil || *remaining.MaxDurationNS != (Uint128{}) {
		t.Fatal("exhaustion did not saturate", remaining.MaxDurationNS)
	}
}

package statelessness

import (
	"errors"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
)

func autoGuidedFeedback() Feedback[int, int, int, int] {
	return StateFeedback[int, int, int](FeedbackMetadata{"test", 1, "test"}, FeatureKey[int]{Clone: ValueCopy[int], Equal: func(a, b int) bool { return a == b }}, func(s int) (int, error) { return s, nil })
}
func TestGuidedAutoCancellationDuringScans(t *testing.T) {
	for _, target := range []int{1, 2} {
		t.Run(map[int]string{1: "generation", 2: "membership"}[target], func(t *testing.T) {
			var cancelled atomic.Bool
			scans, calls := 0, 0
			m := autoFuzzTestModel(func(int) (Iterator[int], error) {
				scans++
				n := 0
				return func() (int, bool, error) {
					n++
					calls++
					if scans == target && n == 2 {
						cancelled.Store(true)
					}
					if n > 4 {
						return 0, false, nil
					}
					return n, true, nil
				}, nil
			})
			r, e := GuidedFuzzAutoWithLimits64(m, func(a, b int) (bool, error) { return a == b, nil }, AutoOptions64{10}, autoFuzzTestConfig(), DefaultGuidanceConfig64(), autoGuidedFeedback(), RunLimits{Cancellation: &cancelled})
			if e != nil || r.Termination != "cancelled" || r.Transitions != 0 || r.Guidance == nil || scans != target {
				t.Fatalf("report=%+v err=%v scans=%d calls=%d", r, e, scans, calls)
			}
			want := 2
			if target == 2 {
				want += 5
			}
			if calls != want {
				t.Fatalf("calls=%d want %d", calls, want)
			}
		})
	}
}
func TestGuidedAutoDeadlineAndCallbackPrecedence(t *testing.T) {
	c := &autoTestControl{}
	calls := 0
	m := autoFuzzTestModel(func(int) (Iterator[int], error) {
		return func() (int, bool, error) {
			calls++
			if calls == 3 {
				c.stopped = true
			}
			return calls, true, nil
		}, nil
	})
	eq := func(a, b int) (bool, error) { return a == b, nil }
	r, e := guidedAutoKernel64(m, eq, AutoOptions64{100}, autoFuzzTestConfig(), DefaultGuidanceConfig64(), autoGuidedFeedback(), c, nil)
	if e != nil || r.Termination != "deadline" || r.Transitions != 0 || r.Guidance == nil || calls != 3 {
		t.Fatalf("report=%+v err=%v calls=%d", r, e, calls)
	}
	var cancel atomic.Bool
	m = autoFuzzTestModel(func(int) (Iterator[int], error) {
		return func() (int, bool, error) { cancel.Store(true); return 0, false, errors.New("domain broke") }, nil
	})
	r, e = GuidedFuzzAutoWithLimits64(m, eq, AutoOptions64{10}, autoFuzzTestConfig(), DefaultGuidanceConfig64(), autoGuidedFeedback(), RunLimits{Cancellation: &cancel})
	if r != nil || e == nil || !strings.Contains(e.Error(), "domain broke") {
		t.Fatalf("report=%+v err=%v", r, e)
	}
}
func TestGuidedAutoCompletedMatchesOrdinary(t *testing.T) {
	m := autoFuzzTestModel(func(int) (Iterator[int], error) { return Values([]int{1, 2, 3}), nil })
	eq := func(a, b int) (bool, error) { return a == b, nil }
	config := FuzzConfig64{Seed: 23, Cases: 4, MaxSteps: 3, MaxTransitions: 20, MutationPercent: 50}
	guidance := DefaultGuidanceConfig64()
	feedback := autoGuidedFeedback()
	g, e := Auto64(m, eq, AutoOptions64{10})
	if e != nil {
		t.Fatal(e)
	}
	want, e := GuidedFuzzWithLimits64(g, config, guidance, feedback, RunLimits{})
	if e != nil {
		t.Fatal(e)
	}
	got, e := GuidedFuzzAutoWithLimits64(m, eq, AutoOptions64{10}, config, guidance, feedback, RunLimits{})
	if e != nil {
		t.Fatal(e)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got=%+v want=%+v", got, want)
	}
}

// Reuse is seed-dependent: find a deterministic corpus mutation that checks a
// retained prefix before generating its replacement, then interrupt that scan.
func TestGuidedAutoObservedCancellationDuringReusedMembership(t *testing.T) {
	for seed := uint64(0); seed < 100; seed++ {
		var cancel atomic.Bool
		cases, scans, edges := 0, 0, 0
		m := autoFuzzTestModel(func(int) (Iterator[int], error) {
			if cases == 2 {
				scans++
			}
			return Values([]int{1, 2, 3}), nil
		})
		m.InitialState = func() (int, error) { cases++; return 0, nil }
		hit := false
		equal := func(a, b int) (bool, error) {
			if cases == 2 && scans == 1 {
				hit = true
				cancel.Store(true)
				return false, nil
			}
			return a == b, nil
		}
		observer := SearchObserver[int, int, int]{Transition: func(int, int, Transition[int, int], []Check, []Check, []int) error { edges++; return nil }}
		cfg := FuzzConfig64{Seed: seed, Cases: 2, MaxSteps: 3, MaxTransitions: 10, MutationPercent: 100}
		r, e := GuidedFuzzAutoObserved64(m, equal, AutoOptions64{10}, cfg, DefaultGuidanceConfig64(), autoGuidedFeedback(), RunLimits{Cancellation: &cancel}, observer)
		if e != nil {
			t.Fatal(e)
		}
		if hit {
			if r.Termination != "cancelled" || r.Cases != 2 || r.Transitions != 3 || edges != 3 || r.Guidance == nil {
				t.Fatalf("seed=%d report=%+v edges=%d", seed, r, edges)
			}
			return
		}
	}
	t.Fatal("no retained-prefix membership encountered")
}

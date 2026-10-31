package statelessness

import (
	"errors"
	"strings"
	"sync/atomic"
	"testing"
)

func autoFuzzTestModel(inputs func(int) (Iterator[int], error)) Model[int, int, int] {
	return Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil },
		Step:         func(s, i int) (Transition[int, int], error) { return Accept[int, int](s + i), nil },
		CheckState:   func(int) ([]Check, error) { return nil, nil },
		CloneState:   ValueCopy[int], CloneInput: ValueCopy[int], CloneOutput: ValueCopy[int], Inputs: inputs,
	}
}
func autoFuzzTestConfig() FuzzConfig64 {
	return FuzzConfig64{Seed: 7, Cases: 1, MaxSteps: 1, MaxTransitions: 1}
}
func TestAutoFuzzCancellationDuringGenerationAndMembership(t *testing.T) {
	for _, scan := range []int{1, 2} {
		t.Run(map[int]string{1: "generation", 2: "membership"}[scan], func(t *testing.T) {
			var cancelled atomic.Bool
			scans, calls := 0, 0
			m := autoFuzzTestModel(func(int) (Iterator[int], error) {
				scans++
				local := 0
				return func() (int, bool, error) {
					local++
					calls++
					if scans == scan && local == 2 {
						cancelled.Store(true)
					}
					if local > 4 {
						return 0, false, nil
					}
					return local, true, nil
				}, nil
			})
			r, e := FuzzAutoWithLimits64(m, func(a, b int) (bool, error) { return a == b, nil }, AutoOptions64{10}, autoFuzzTestConfig(), RunLimits{Cancellation: &cancelled})
			if e != nil || r.Termination != "cancelled" || r.Cases != 1 || r.Transitions != 0 || scans != scan {
				t.Fatalf("report=%+v error=%v scans=%d calls=%d", r, e, scans, calls)
			}
			want := 2
			if scan == 2 {
				want += 5
			}
			if calls != want {
				t.Fatalf("calls=%d want=%d", calls, want)
			}
		})
	}
}
func TestAutoScanInterruptionDoesNotCommitRNG(t *testing.T) {
	n := 0
	m := autoFuzzTestModel(func(int) (Iterator[int], error) { return func() (int, bool, error) { n++; return n, true, nil }, nil })
	polls := 0
	g, e := auto64(m, func(a, b int) (bool, error) { return a == b, nil }, AutoOptions64{10}, func() error {
		polls++
		if polls == 5 {
			return autoScanStopped{}
		}
		return nil
	})
	if e != nil {
		t.Fatal(e)
	}
	rng := NewRng(123)
	before := *rng
	_, _, e = g.Generate(0, rng)
	if e == nil || *rng != before {
		t.Fatalf("err=%v before=%+v after=%+v", e, before, *rng)
	}
}
func TestAutoFuzzCallbackErrorPrecedesCancellation(t *testing.T) {
	var cancelled atomic.Bool
	m := autoFuzzTestModel(func(int) (Iterator[int], error) {
		return func() (int, bool, error) { cancelled.Store(true); return 0, false, errors.New("domain broke") }, nil
	})
	r, e := FuzzAutoWithLimits64(m, func(a, b int) (bool, error) { return a == b, nil }, AutoOptions64{2}, autoFuzzTestConfig(), RunLimits{Cancellation: &cancelled})
	if r != nil || e == nil || !strings.Contains(e.Error(), "domain broke") {
		t.Fatalf("report=%+v err=%v", r, e)
	}
}

type autoTestControl struct{ stopped bool }

func (c *autoTestControl) stop() string {
	if c.stopped {
		return "deadline"
	}
	return ""
}
func TestAutoFuzzDeadlineDuringScan(t *testing.T) {
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
	r, e := fuzzAutoKernel64(m, func(a, b int) (bool, error) { return a == b, nil }, AutoOptions64{100}, autoFuzzTestConfig(), c)
	if e != nil || r.Termination != "deadline" || r.Transitions != 0 || calls != 3 {
		t.Fatalf("report=%+v err=%v calls=%d", r, e, calls)
	}
}

func TestAutoFuzzCompletedMatchesOrdinary(t *testing.T) {
	m := autoFuzzTestModel(func(int) (Iterator[int], error) { return Values([]int{1, 2, 3}), nil })
	eq := func(a, b int) (bool, error) { return a == b, nil }
	cfg := FuzzConfig64{Seed: 23, Cases: 4, MaxSteps: 3, MaxTransitions: 20, MutationPercent: 50}
	regular, e := Auto64(m, eq, AutoOptions64{10})
	if e != nil {
		t.Fatal(e)
	}
	want, e := FuzzWithLimits64(regular, cfg, RunLimits{})
	if e != nil {
		t.Fatal(e)
	}
	got, e := FuzzAutoWithLimits64(m, eq, AutoOptions64{10}, cfg, RunLimits{})
	if e != nil {
		t.Fatal(e)
	}
	if got.Termination != want.Termination || got.Cases != want.Cases || got.Transitions != want.Transitions || got.SkippedChecks != want.SkippedChecks {
		t.Fatalf("got=%+v want=%+v", got, want)
	}
}

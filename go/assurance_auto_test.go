package statelessness_test

import (
	s "github.com/Jagalite/statelessness/go"
	"sync/atomic"
	"testing"
)

func TestAssuredAutoScanReportsInterruptionWithoutAdequacy(t *testing.T) {
	for _, guided := range []bool{false, true} {
		var cancelled atomic.Bool
		g := integerSearch(t, "singleton", nil)
		g.Codec = &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("example", "build"), nil }}
		g.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("state")}, nil }
		calls := 0
		g.Inputs = func(int) (s.Iterator[int], error) {
			return func() (int, bool, error) {
				calls++
				if calls == 2 {
					cancelled.Store(true)
				}
				return calls, true, nil
			}, nil
		}
		equal := func(a, b int) (bool, error) { return a == b, nil }
		cfg := s.FuzzConfig64{Seed: 42, Cases: 1, MaxSteps: 1, MaxTransitions: 10}
		options := s.AutoOptions64{MaxCandidates: 10}
		var run s.AssuredRun[s.FuzzReport64[int]]
		var err error
		if guided {
			run, err = s.AssuranceGuidedFuzzAuto64(g.Model, equal, options, assuranceManifest(), nil, cfg, s.DefaultGuidanceConfig64(), counterFeedback(), s.RunLimits{Cancellation: &cancelled})
		} else {
			run, err = s.AssuranceFuzzAuto64(g.Model, equal, options, assuranceManifest(), nil, cfg, s.RunLimits{Cancellation: &cancelled})
		}
		if err != nil || run.SearchError != nil || run.Search == nil || run.Search.Termination != "cancelled" || run.Search.Transitions != 0 || calls != 2 || run.Assurance.AssessStrict().Adequate || run.Assurance.Budgets["auto.max_candidates"] != "" {
			t.Fatalf("guided=%v run=%+v err=%v calls=%d", guided, run, err, calls)
		}
	}
}

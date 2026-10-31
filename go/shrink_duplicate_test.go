package statelessness_test

import (
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

func TestShrinkBoundsRepeatedSimplifierCandidates(t *testing.T) {
	for _, tc := range []struct {
		name        string
		finite      bool
		termination string
		pulls       int
	}{
		{"endless", false, "attempt_limit", 4},
		{"exactly_at_cap", true, "search_complete", 4},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := integerSearch(t, "cycle", nil)
			pulls := 0
			g.SimplerInputs = func(input int) (s.Iterator[int], error) {
				return func() (int, bool, error) {
					pulls++
					if tc.finite && pulls > 3 {
						return 0, false, nil
					}
					if pulls > 4 {
						t.Fatal("unbounded simplifier")
					}
					return input, true, nil
				}, nil
			}
			original := s.Failure[int]{Inputs: []int{1}, Violations: []s.Violation{{Phase: "state", Check: s.Failed("bad", "")}}}
			r, err := s.ShrinkWithLimits64(g, original, s.ShrinkConfig64{MaxAttempts: 3}, s.ShrinkLimits{})
			if err != nil {
				t.Fatal(err)
			}
			if r.Termination != tc.termination || pulls != tc.pulls || r.Attempts != 2 || len(r.History) != 2 || r.ReplayedTransitions != 1 || !r.ValidatedOriginal {
				t.Fatalf("unexpected result: %+v, pulls=%d", r, pulls)
			}
		})
	}
}

// Mirrors Rust tests/exploration.rs skipped_hint_bound_is_per_iterator_not_per_run:
// every position re-yields already-seen zeros once simplified, so the total
// number of skipped hints across positions exceeds MaxAttempts while no single
// iterator does. The run must still complete.
func TestShrinkSkippedHintBoundIsPerIteratorNotPerRun(t *testing.T) {
	m := s.Model[int, int, int]{InitialState: func() (int, error) { return 0, nil }, Step: func(state, _ int) (s.Transition[int, int], error) {
		return s.Accept[int, int](state + 1), nil
	}, CheckState: func(length int) ([]s.Check, error) {
		if length >= 6 {
			return []s.Check{s.Failed("long", "long")}, nil
		}
		return nil, nil
	}, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], Inputs: func(int) (s.Iterator[int], error) {
		// Zero must be an enabled input, or every simplification is rejected
		// before replay and the per-iterator bound is never exercised.
		return s.Values([]int{0, 1}), nil
	}}
	g, err := s.Auto(m, func(a, b int) (bool, error) { return a == b, nil }, s.AutoOptions{MaxCandidates: 100})
	if err != nil {
		t.Fatal(err)
	}
	g.SimplerInputs = func(input int) (s.Iterator[int], error) {
		if input == 0 {
			return s.Values(make([]int, 10)), nil
		}
		return s.Values([]int{0}), nil
	}
	original := s.Failure[int]{Inputs: []int{1, 1, 1, 1, 1, 1}, Violations: []s.Violation{{Phase: "state", Check: s.Failed("long", "long")}}}
	r, err := s.ShrinkWithLimits64(g, original, s.ShrinkConfig64{MaxAttempts: 150}, s.ShrinkLimits{})
	if err != nil {
		t.Fatal(err)
	}
	if r.Termination != "search_complete" || r.Attempts >= 150 {
		t.Fatalf("unexpected termination: %s after %d attempts", r.Termination, r.Attempts)
	}
	for _, input := range r.Minimized.Inputs {
		if input != 0 {
			t.Fatalf("not minimized: %v", r.Minimized.Inputs)
		}
	}
}

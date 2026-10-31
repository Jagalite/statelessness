package statelessness_test

import (
	s "github.com/Jagalite/statelessness/go"
	"testing"
)

func TestEnumerateRetainsInputBeforeReducerAliases(t *testing.T) {
	shared := 7
	observed := 0
	m := s.Model[int, *int, int]{
		InitialState: func() (int, error) { return 0, nil },
		Inputs:       func(int) (s.Iterator[*int], error) { return s.Values([]*int{&shared}), nil },
		CloneState:   s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		CloneInput: func(v *int) *int { n := *v; return &n },
		Step:       func(_ int, _ *int) (s.Transition[int, int], error) { shared = 99; return s.Accept[int, int](1), nil },
		CheckState: func(int) ([]s.Check, error) { return nil, nil },
		CheckTransition: func(_ int, input *int, _ s.Transition[int, int]) ([]s.Check, error) {
			observed = *input
			return []s.Check{s.Failed("edge", "failure")}, nil
		},
		EqualStates: func(a, b int) bool { return a == b },
	}
	report, err := s.Enumerate(m, s.SearchConfig{MaxStates: 10, MaxTransitions: 10, MaxDepth: 2})
	if err != nil {
		t.Fatal(err)
	}
	if observed != 7 || report.Failure == nil || len(report.Failure.Inputs) != 1 || *report.Failure.Inputs[0] != 7 {
		t.Fatalf("observed=%d report=%+v", observed, report)
	}
}

func TestEnumerateRetainedInputCopyFailurePrecedesReducer(t *testing.T) {
	clones, steps := 0, 0
	m := s.Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil },
		Inputs:       func(int) (s.Iterator[int], error) { return s.Values([]int{1}), nil },
		CloneState:   s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		CloneInput: func(v int) int {
			clones++
			if clones == 2 {
				panic("copy failed")
			}
			return v
		},
		Step: func(state, input int) (s.Transition[int, int], error) {
			steps++
			return s.Accept[int, int](state + input), nil
		},
		CheckState:  func(int) ([]s.Check, error) { return nil, nil },
		EqualStates: func(a, b int) bool { return a == b },
	}
	_, err := s.Enumerate(m, s.SearchConfig{MaxStates: 10, MaxTransitions: 10, MaxDepth: 2})
	if err == nil || clones != 2 || steps != 0 {
		t.Fatalf("err=%v clones=%d steps=%d", err, clones, steps)
	}
}

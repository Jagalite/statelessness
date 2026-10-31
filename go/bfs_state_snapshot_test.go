package statelessness_test

import (
	s "github.com/Jagalite/statelessness/go"
	"testing"
)

func TestEnumerateRetainsNodesBeforeCheckAliases(t *testing.T) {
	initial, successor := 0, 1
	seen := []int{}
	m := s.Model[*int, int, int]{
		InitialState: func() (*int, error) { return &initial, nil },
		CloneState:   func(v *int) *int { n := *v; return &n }, CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		Inputs: func(v *int) (s.Iterator[int], error) {
			seen = append(seen, *v)
			if *v == 0 {
				return s.Values([]int{1}), nil
			}
			return s.Values([]int{}), nil
		},
		Step: func(_ *int, _ int) (s.Transition[*int, int], error) { return s.Accept[*int, int](&successor), nil },
		CheckState: func(v *int) ([]s.Check, error) {
			if *v == 0 {
				initial = 90
			} else {
				successor = 91
			}
			return nil, nil
		},
		EqualStates: func(a, b *int) bool { return *a == *b },
	}
	report, err := s.Enumerate(m, s.SearchConfig{MaxStates: 10, MaxTransitions: 10, MaxDepth: 2})
	if err != nil {
		t.Fatal(err)
	}
	if report.States != 2 || len(seen) != 2 || seen[0] != 0 || seen[1] != 1 {
		t.Fatalf("states=%d inputs saw %v", report.States, seen)
	}
}

func TestEnumerateUnusedInitialNodeCopyFailureDoesNotPreemptFinding(t *testing.T) {
	copies := 0
	m := s.Model[int, int, int]{InitialState: func() (int, error) { return 0, nil }, CloneState: func(v int) int {
		copies++
		if copies == 2 {
			panic("node copy")
		}
		return v
	}, CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], Inputs: func(int) (s.Iterator[int], error) { return s.Values([]int{}), nil }, Step: func(a, b int) (s.Transition[int, int], error) { return s.Accept[int, int](a), nil }, CheckState: func(int) ([]s.Check, error) { return []s.Check{s.Failed("initial", "failure")}, nil }, EqualStates: func(a, b int) bool { return a == b }}
	report, err := s.Enumerate(m, s.SearchConfig{MaxStates: 2, MaxTransitions: 2, MaxDepth: 1})
	if err != nil || report.Failure == nil {
		t.Fatalf("report=%+v err=%v copies=%d", report, err, copies)
	}
}

func TestEnumerateCapturesEdgeBeforeHistoryCallbacks(t *testing.T) {
	successor, output := 1, 2
	checked, observed := 0, 0
	obs := s.SearchObserver[*int, int, *int]{}
	m := s.Model[*int, int, *int]{
		InitialState: func() (*int, error) { v := 0; return &v, nil }, CloneState: func(v *int) *int { n := *v; return &n }, CloneInput: s.ValueCopy[int], CloneOutput: func(v *int) *int { n := *v; return &n },
		Inputs: func(v *int) (s.Iterator[int], error) {
			if *v == 0 {
				return s.Values([]int{1}), nil
			}
			return s.Values([]int{}), nil
		},
		Step: func(_ *int, _ int) (s.Transition[*int, *int], error) {
			obs.Transition = func(*int, int, s.Transition[*int, *int], []s.Check, []s.Check, []int) error {
				t.Fatal("replacement observer called")
				return nil
			}
			return s.Accept[*int, *int](&successor, &output), nil
		},
		CheckState: func(*int) ([]s.Check, error) { return nil, nil }, CheckTransition: func(_ *int, _ int, tr s.Transition[*int, *int]) ([]s.Check, error) {
			checked = *tr.State + *tr.Outputs[0]
			return nil, nil
		}, EqualStates: func(a, b *int) bool { return *a == *b },
	}
	obs.NeedsHistory = func() bool { successor = 80; output = 90; return false }
	obs.Transition = func(_ *int, _ int, tr s.Transition[*int, *int], _ []s.Check, _ []s.Check, _ []int) error {
		observed = *tr.State + *tr.Outputs[0]
		return nil
	}
	_, err := s.EnumerateObserved(m, s.SearchConfig{MaxStates: 5, MaxTransitions: 5, MaxDepth: 2}, s.SearchLimits{}, obs)
	if err != nil || checked != 3 || observed != 3 {
		t.Fatalf("err=%v checked=%d observed=%d", err, checked, observed)
	}
}

func TestEnumerateUnusedSuccessorCopyFailureDoesNotPreemptFinding(t *testing.T) {
	copies := 0
	m := s.Model[int, int, int]{InitialState: func() (int, error) { return 0, nil }, CloneState: func(v int) int {
		if v == 1 {
			copies++
			if copies == 3 {
				panic("successor copy")
			}
		}
		return v
	}, CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], Inputs: func(v int) (s.Iterator[int], error) {
		if v == 0 {
			return s.Values([]int{1}), nil
		}
		return s.Values([]int{}), nil
	}, Step: func(int, int) (s.Transition[int, int], error) { return s.Accept[int, int](1), nil }, CheckState: func(v int) ([]s.Check, error) {
		if v == 1 {
			return []s.Check{s.Failed("state", "failure")}, nil
		}
		return nil, nil
	}, EqualStates: func(a, b int) bool { return a == b }}
	report, err := s.Enumerate(m, s.SearchConfig{MaxStates: 2, MaxTransitions: 2, MaxDepth: 1})
	if err != nil || report.Failure == nil || copies < 3 {
		t.Fatalf("report=%+v err=%v copies=%d", report, err, copies)
	}
}

func TestEnumerateCheckStateCaptureFailurePrecedesCheck(t *testing.T) {
	copies, checked := 0, 0
	m := s.Model[int, int, int]{InitialState: func() (int, error) { return 0, nil }, CloneState: func(v int) int {
		if v == 1 {
			copies++
			if copies == 2 {
				panic("check state capture")
			}
		}
		return v
	}, CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], Inputs: func(v int) (s.Iterator[int], error) {
		if v == 0 {
			return s.Values([]int{1}), nil
		}
		return s.Values([]int{}), nil
	}, Step: func(int, int) (s.Transition[int, int], error) { return s.Accept[int, int](1), nil }, CheckState: func(v int) ([]s.Check, error) {
		if v == 1 {
			checked++
		}
		return nil, nil
	}, EqualStates: func(a, b int) bool { return a == b }}
	_, err := s.Enumerate(m, s.SearchConfig{MaxStates: 2, MaxTransitions: 2, MaxDepth: 1})
	if err == nil || checked != 0 {
		t.Fatalf("err=%v checked=%d copies=%d", err, checked, copies)
	}
}

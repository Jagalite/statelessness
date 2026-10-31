package statelessness_test

import (
	s "github.com/Jagalite/statelessness/go"
	"testing"
)

func TestFuzzRetainsSuccessorBeforeChecks(t *testing.T) {
	var transitionAlias *int
	var initialAlias *int
	seen := []int{}
	clone := func(p *int) *int {
		v := *p
		q := &v
		if v == 0 && initialAlias == nil {
			initialAlias = q
		}
		if v == 1 && transitionAlias == nil {
			transitionAlias = q
		}
		return q
	}
	g := s.GenerateModel[*int, int, int]{Model: s.Model[*int, int, int]{
		InitialState: func() (*int, error) { x := 0; return &x, nil },
		CloneState:   clone, CloneInput: func(i int) int { return i }, CloneOutput: func(i int) int { return i },
		Step: func(state *int, input int) (s.Transition[*int, int], error) {
			seen = append(seen, *state)
			n := *state + 1
			return s.Accept[*int, int](&n), nil
		},
		CheckState: func(state *int) ([]s.Check, error) {
			if *state == 0 && initialAlias != nil {
				*initialAlias = 99
			}
			if *state == 1 && transitionAlias != nil {
				*transitionAlias = 99
			}
			return nil, nil
		},
	}, Generate: func(*int, *s.Rng) (int, bool, error) { return 1, true, nil }}
	report, err := s.Fuzz64(g, s.FuzzConfig64{Cases: 1, MaxSteps: 2, MaxTransitions: 2})
	if err != nil || report.Transitions != 2 || len(seen) != 2 || seen[0] != 0 || seen[1] != 1 {
		t.Fatalf("report=%+v err=%v seen=%v", report, err, seen)
	}
}

func TestFuzzUnusedInitialCaptureFailureDefersToLimit(t *testing.T) {
	copies := 0
	g := s.GenerateModel[int, int, int]{Model: s.Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil },
		CloneState: func(v int) int {
			copies++
			if copies == 2 {
				panic("unused snapshot")
			}
			return v
		},
		CloneInput: func(v int) int { return v }, CloneOutput: func(v int) int { return v },
		Step: func(v, i int) (s.Transition[int, int], error) {
			t.Fatal("unexpected step")
			return s.Accept[int, int](v), nil
		},
		CheckState: func(int) ([]s.Check, error) { return nil, nil },
	}, Generate: func(int, *s.Rng) (int, bool, error) { return 1, true, nil }}
	report, err := s.Fuzz64(g, s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 0})
	if err != nil || report.Termination != "transition_limit" {
		t.Fatalf("report=%+v err=%v", report, err)
	}
}

func TestFuzzAdmissionCannotChangeExecutionState(t *testing.T) {
	var alias *int
	copies := 0
	seen := -1
	g := s.GenerateModel[*int, int, int]{Model: s.Model[*int, int, int]{
		InitialState: func() (*int, error) { v := 1; return &v, nil },
		CloneState: func(p *int) *int {
			copies++
			v := *p
			q := &v
			if copies == 2 {
				alias = q
			}
			return q
		},
		CloneInput: func(v int) int { return v }, CloneOutput: func(v int) int { return v },
		Step:       func(p *int, _ int) (s.Transition[*int, int], error) { seen = *p; return s.Accept[*int, int](p), nil },
		CheckState: func(*int) ([]s.Check, error) { return nil, nil },
	}, Generate: func(_ *int, _ *s.Rng) (int, bool, error) { *alias = 99; return 1, true, nil }}
	_, err := s.Fuzz64(g, s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 1})
	if err != nil || seen != 1 {
		t.Fatalf("seen=%d err=%v", seen, err)
	}
}

func TestFuzzEdgeCapturedBeforeHistoryCopy(t *testing.T) {
	var next *int
	afterStep := false
	copies := 0
	seen := -1
	g := s.GenerateModel[*int, int, int]{Model: s.Model[*int, int, int]{
		InitialState: func() (*int, error) { v := 0; return &v, nil },
		CloneState:   func(p *int) *int { v := *p; return &v },
		CloneInput: func(v int) int {
			if afterStep {
				copies++
				if copies == 2 {
					*next = 99
				}
			}
			return v
		},
		CloneOutput: func(v int) int { return v },
		Step: func(_ *int, _ int) (s.Transition[*int, int], error) {
			v := 1
			next = &v
			afterStep = true
			return s.Accept[*int, int](next), nil
		},
		CheckState: func(p *int) ([]s.Check, error) {
			if afterStep {
				seen = *p
			}
			return nil, nil
		},
		CheckTransition: func(_ *int, _ int, _ s.Transition[*int, int]) ([]s.Check, error) { return nil, nil },
	}, Generate: func(*int, *s.Rng) (int, bool, error) { return 1, true, nil }}
	_, err := s.Fuzz64(g, s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 1})
	if err != nil || seen != 1 {
		t.Fatalf("seen=%d copies=%d err=%v", seen, copies, err)
	}
}

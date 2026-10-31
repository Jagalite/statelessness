package statelessness_test

import (
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"slices"
	"strings"
	"testing"
)

func TestPairedReferenceObservationsBeforeChecks(t *testing.T) {
	history := []int{7}
	var firstOutput []int
	clones := 0
	ref := s.Model[[]int, int, []int]{
		InitialState: func() ([]int, error) { return []int{7}, nil },
		CloneState:   slices.Clone[[]int], CloneInput: s.ValueCopy[int],
		CloneOutput: func(v []int) []int {
			x := slices.Clone(v)
			clones++
			if clones == 1 {
				firstOutput = x
			}
			return x
		},
		EqualStates:     slices.Equal[[]int],
		Step:            func(h []int, _ int) (s.Transition[[]int, []int], error) { return s.Accept(h, []int{9}), nil },
		CheckState:      func([]int) ([]s.Check, error) { history[0] = 99; return nil, nil },
		CheckTransition: func([]int, int, s.Transition[[]int, []int]) ([]s.Check, error) { firstOutput[0] = 99; return nil, nil },
	}
	proj := s.Projection[int, []int, int, []int, int, int]{
		Metadata:    func() (s.Metadata, error) { return s.Identity("projection", "test"), nil },
		ActualState: s.ValueCopy[int], ReferenceState: func(v []int) int { return v[0] },
		ActualEffect: s.ValueCopy[int], ReferenceEffect: func(v []int) int { return v[0] },
		EqualStates: func(a, b int) bool { return a == b }, EqualEffects: func(a, b int) bool { return a == b },
	}
	oracle, err := s.Paired(ref, proj)
	if err != nil {
		t.Fatal(err)
	}
	checks, err := oracle.CheckState(history, 7)
	if err != nil {
		t.Fatal(err)
	}
	if len(checks) != 1 || checks[0].Status != "passed" {
		t.Fatalf("state checks: %+v", checks)
	}
	history[0] = 7
	state := s.OracleState[int, []int]{Model: 7, Oracle: history}
	result := s.Accept(state, 9)
	checks, err = oracle.CheckTransition(state, 0, result)
	if err != nil {
		t.Fatal(err)
	}
	if len(checks) != 2 || checks[0].Status != "passed" || checks[1].Status != "passed" {
		t.Fatalf("transition checks: %+v", checks)
	}
}

func TestPairedCaptureFailureKeepsReferenceCheckErrorPrecedence(t *testing.T) {
	stateClones, outputClones := 0, 0
	ref := s.Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil },
		CloneState: func(v int) int {
			stateClones++
			if stateClones == 1 {
				panic("capture-state")
			}
			return v
		},
		CloneInput: s.ValueCopy[int],
		CloneOutput: func(v int) int {
			outputClones++
			if outputClones == 2 {
				panic("capture-output")
			}
			return v
		},
		EqualStates:     func(a, b int) bool { return a == b },
		Step:            func(v, i int) (s.Transition[int, int], error) { return s.Accept(v, i), nil },
		CheckState:      func(int) ([]s.Check, error) { return nil, fmt.Errorf("state-check") },
		CheckTransition: func(int, int, s.Transition[int, int]) ([]s.Check, error) { return nil, fmt.Errorf("edge-check") },
	}
	proj := s.Projection[int, int, int, int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("p", "t"), nil }, ActualState: s.ValueCopy[int], ReferenceState: s.ValueCopy[int], ActualEffect: s.ValueCopy[int], ReferenceEffect: s.ValueCopy[int], EqualStates: func(a, b int) bool { return a == b }, EqualEffects: func(a, b int) bool { return a == b }}
	oracle, err := s.Paired(ref, proj)
	if err != nil {
		t.Fatal(err)
	}
	_, err = oracle.CheckState(1, 1)
	if err == nil || !strings.Contains(err.Error(), "state-check") {
		t.Fatalf("state error: %v", err)
	}
	state := s.OracleState[int, int]{Model: 1, Oracle: 1}
	_, err = oracle.CheckTransition(state, 1, s.Accept(state, 1))
	if err == nil || !strings.Contains(err.Error(), "edge-check") {
		t.Fatalf("transition error: %v", err)
	}
}

func TestPairedActualProjectionFailurePrecedesReferenceCaptureFailure(t *testing.T) {
	stateClones, outputClones := 0, 0
	ref := s.Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil },
		CloneState: func(v int) int {
			stateClones++
			if stateClones == 1 {
				panic("capture-state")
			}
			return v
		},
		CloneInput: s.ValueCopy[int],
		CloneOutput: func(v int) int {
			outputClones++
			if outputClones == 2 {
				panic("capture-output")
			}
			return v
		},
		EqualStates: func(a, b int) bool { return a == b },
		Step:        func(v, i int) (s.Transition[int, int], error) { return s.Accept(v, i), nil },
		CheckState:  func(int) ([]s.Check, error) { return nil, nil },
	}
	proj := s.Projection[int, int, int, int, int, int]{
		Metadata:    func() (s.Metadata, error) { return s.Identity("p", "t"), nil },
		ActualState: func(int) int { panic("actual-state") }, ReferenceState: s.ValueCopy[int],
		ActualEffect: func(int) int { panic("actual-effect") }, ReferenceEffect: s.ValueCopy[int],
		EqualStates: func(a, b int) bool { return a == b }, EqualEffects: func(a, b int) bool { return a == b },
	}
	oracle, err := s.Paired(ref, proj)
	if err != nil {
		t.Fatal(err)
	}
	_, err = oracle.CheckState(1, 1)
	if err == nil || !strings.Contains(err.Error(), "actual-state") {
		t.Fatalf("state error: %v", err)
	}
	state := s.OracleState[int, int]{Model: 1, Oracle: 1}
	_, err = oracle.CheckTransition(state, 1, s.Accept(state, 1))
	if err == nil || !strings.Contains(err.Error(), "actual-effect") {
		t.Fatalf("transition error: %v", err)
	}
}

func TestPairedReferenceProjectionFailurePrecedesLaterCaptureFailure(t *testing.T) {
	outputClones := 0
	ref := s.Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil },
		CloneState:   s.ValueCopy[int], CloneInput: s.ValueCopy[int],
		CloneOutput: func(v int) int {
			outputClones++
			if outputClones == 4 {
				panic("later-capture")
			}
			return v
		},
		EqualStates: func(a, b int) bool { return a == b },
		Step:        func(v, i int) (s.Transition[int, int], error) { return s.Accept(v, 1, 2), nil },
		CheckState:  func(int) ([]s.Check, error) { return nil, nil },
	}
	proj := s.Projection[int, int, int, int, int, int]{
		Metadata:    func() (s.Metadata, error) { return s.Identity("p", "t"), nil },
		ActualState: s.ValueCopy[int], ReferenceState: s.ValueCopy[int],
		ActualEffect: s.ValueCopy[int], ReferenceEffect: func(int) int { panic("first-reference-effect") },
		EqualStates: func(a, b int) bool { return a == b }, EqualEffects: func(a, b int) bool { return a == b },
	}
	oracle, err := s.Paired(ref, proj)
	if err != nil {
		t.Fatal(err)
	}
	state := s.OracleState[int, int]{Model: 1, Oracle: 1}
	_, err = oracle.CheckTransition(state, 1, s.Accept(state, 1, 2))
	if err == nil || !strings.Contains(err.Error(), "first-reference-effect") {
		t.Fatalf("transition error: %v (output clones %d)", err, outputClones)
	}
}

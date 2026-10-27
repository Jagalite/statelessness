package statelessness_test

import (
	"errors"
	s "github.com/Jagalite/statelessness/go"
	"reflect"
	"slices"
	"strings"
	"testing"
)

func TestFuzzObserverSeesFailingHistoryAndOwnsSnapshots(t *testing.T) {
	shared := []int{1}
	g := s.GenerateModel[[]int, []int, []int]{Model: s.Model[[]int, []int, []int]{InitialState: func() ([]int, error) { return []int{0}, nil }, Step: func(state, input []int) (s.Transition[[]int, []int], error) {
		state[0] += input[0]
		return s.Accept(state, []int{state[0]}), nil
	}, CheckState: func(state []int) ([]s.Check, error) {
		if state[0] >= 2 {
			return []s.Check{s.Failed("bad", "two")}, nil
		}
		return []s.Check{s.Passed("bad")}, nil
	}, CheckTransition: func([]int, []int, s.Transition[[]int, []int]) ([]s.Check, error) {
		return []s.Check{s.Passed("edge")}, nil
	}, CloneState: slices.Clone[[]int], CloneInput: slices.Clone[[]int], CloneOutput: slices.Clone[[]int]}, Generate: func([]int, *s.Rng) ([]int, bool, error) { return shared, true, nil }}
	var events []string
	var observed [][]int
	observer := s.SearchObserver[[]int, []int, []int]{Initial: func(state []int, checks []s.Check) error {
		events = append(events, "initial")
		state[0] = 100
		checks[0] = s.Failed("changed", "observer")
		return nil
	}, Transition: func(before, input []int, tr s.Transition[[]int, []int], state, edge []s.Check, history [][]int) error {
		events = append(events, "transition")
		if before[0]+1 != tr.State[0] || input[0] != 1 || len(edge) != 1 || len(history) != tr.State[0] {
			t.Fatal(before, input, tr, history)
		}
		if tr.State[0] == 2 && state[0].Status != "failed" {
			t.Fatal("failing batch omitted")
		}
		observed = append(observed, []int{before[0], tr.State[0], len(history)})
		before[0] = 100
		input[0] = 100
		tr.State[0] = 100
		tr.Outputs[0][0] = 100
		history[0][0] = 100
		state[0] = s.Passed("changed")
		edge[0] = s.Passed("changed")
		return nil
	}}
	r, e := s.FuzzObserved(g, s.FuzzConfig{Cases: 1, MaxSteps: 10, MaxTransitions: 10}, s.RunLimits{}, observer)
	if e != nil || r.Termination != "failure_found" || r.Transitions != 2 || r.Failure.Violations[0].Check.ID != "bad" {
		t.Fatal(r, e)
	}
	if !reflect.DeepEqual(events, []string{"initial", "transition", "transition"}) || !reflect.DeepEqual(observed, [][]int{{0, 1, 1}, {1, 2, 2}}) || shared[0] != 1 || r.Failure.Inputs[0][0] != 1 || r.Failure.Inputs[1][0] != 1 {
		t.Fatal(events, observed, r, shared)
	}
}
func TestFuzzObserverIncompleteChecks(t *testing.T) {
	type event struct {
		Phase    string
		Checks   []s.Check
		Complete bool
	}
	for _, phase := range []string{"initial_state", "state", "transition"} {
		t.Run(phase, func(t *testing.T) {
			g := integerSearch(t, "singleton", nil)
			g.CheckState = func(state int) ([]s.Check, error) {
				checks := []s.Check{s.Passed("state")}
				if phase == "initial_state" && state == 0 || phase == "state" && state > 0 {
					return checks, errors.New("state incomplete")
				}
				return checks, nil
			}
			g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) {
				return []s.Check{s.Passed("partial"), {ID: "invalid", Status: "bogus"}, s.Passed("after invalid")}, errors.New("transition incomplete")
			}
			var events []event
			transitions := 0
			observer := s.SearchObserver[int, int, int]{CheckError: func(phase string, checks []s.Check, complete bool) error {
				events = append(events, event{phase, slices.Clone(checks), complete})
				if len(checks) > 0 {
					checks[0] = s.Passed("mutated")
				}
				return nil
			}, Transition: func(int, int, s.Transition[int, int], []s.Check, []s.Check, []int) error { transitions++; return nil }}
			_, e := s.FuzzObserved(g, s.FuzzConfig{Cases: 1, MaxSteps: 1, MaxTransitions: 1}, s.RunLimits{}, observer)
			if e == nil || transitions != 0 {
				t.Fatal("incomplete batch succeeded", e, transitions)
			}
			expected := []event{{phase, []s.Check{s.Passed("state")}, false}}
			if phase == "transition" {
				expected = []event{{"state", []s.Check{s.Passed("state")}, true}, {"transition", []s.Check{s.Passed("partial")}, false}}
			}
			if !reflect.DeepEqual(events, expected) {
				t.Fatal(events, expected)
			}
		})
	}
}
func TestFuzzObserverErrorsAndPanics(t *testing.T) {
	g := integerSearch(t, "singleton", nil)
	observers := []s.SearchObserver[int, int, int]{
		{Initial: func(int, []s.Check) error { return errors.New("observer error") }},
		{Transition: func(int, int, s.Transition[int, int], []s.Check, []s.Check, []int) error { panic("observer panic") }},
	}
	for _, observer := range observers {
		if _, e := s.FuzzObserved(g, s.FuzzConfig{Cases: 1, MaxSteps: 1, MaxTransitions: 1}, s.RunLimits{}, observer); e == nil || !strings.Contains(e.Error(), "observer") {
			t.Fatal(e)
		}
	}
}

func TestOracleObserverRetainsCompletedCheckPrefixes(t *testing.T) {
	m := counter()
	m.CheckState = func(counterState) ([]s.Check, error) { return []s.Check{s.Passed("actual-state")}, nil }
	m.CheckTransition = func(counterState, string, s.Transition[counterState, string]) ([]s.Check, error) {
		return []s.Check{s.Passed("actual-edge")}, nil
	}
	g := s.GenerateModel[counterState, string, string]{Model: m, Generate: func(counterState, *s.Rng) (string, bool, error) { return "increment", true, nil }}
	oracle := s.Oracle[counterState, int, string, string]{InitialState: func() (int, error) { return 0, nil }, CloneHistory: s.ValueCopy[int], Advance: func(h int, _ string, _ []string, _ s.Disposition) (int, error) { return h + 1, nil }, CheckState: func(h int, _ counterState) ([]s.Check, error) {
		if h > 0 {
			return []s.Check{s.Passed("oracle-partial")}, errors.New("oracle checker failed")
		}
		return nil, nil
	}}
	combined, e := s.WithOracleGenerate(g, oracle)
	if e != nil {
		t.Fatal(e)
	}
	var observed []s.Check
	observer := s.SearchObserver[s.OracleState[counterState, int], string, string]{CheckError: func(phase string, checks []s.Check, complete bool) error {
		if phase != "state" || complete {
			t.Fatal(phase, complete)
		}
		observed = checks
		return nil
	}}
	_, e = s.FuzzObserved(combined, s.FuzzConfig{Cases: 1, MaxSteps: 1, MaxTransitions: 1}, s.RunLimits{}, observer)
	if e == nil || !reflect.DeepEqual(observed, []s.Check{s.Passed("actual-state"), s.Passed("oracle-partial")}) {
		t.Fatal(observed, e)
	}
	// Reference checks completed before a projection panic must remain diagnostics.
	reference := counter()
	reference.CheckState = func(counterState) ([]s.Check, error) { return []s.Check{s.Passed("reference-state")}, nil }
	projection := s.Projection[counterState, counterState, string, string, int, string]{Metadata: func() (s.Metadata, error) { return s.Identity("projection", "go"), nil }, ActualState: func(state counterState) int {
		if state.value > 0 {
			panic("projection failed")
		}
		return state.value
	}, ReferenceState: func(state counterState) int { return state.value }, ActualEffect: s.ValueCopy[string], ReferenceEffect: s.ValueCopy[string], EqualStates: func(a, b int) bool { return a == b }, EqualEffects: func(a, b string) bool { return a == b }}
	paired, e := s.Paired(reference, projection)
	if e != nil {
		t.Fatal(e)
	}
	pairedModel, e := s.WithOracleGenerate(g, paired)
	if e != nil {
		t.Fatal(e)
	}
	observed = nil
	pairedObserver := s.SearchObserver[s.OracleState[counterState, counterState], string, string]{CheckError: func(phase string, checks []s.Check, complete bool) error {
		if phase != "state" || complete {
			t.Fatal(phase, complete)
		}
		observed = checks
		return nil
	}}
	_, e = s.FuzzObserved(pairedModel, s.FuzzConfig{Cases: 1, MaxSteps: 1, MaxTransitions: 1}, s.RunLimits{}, pairedObserver)
	if e == nil || !reflect.DeepEqual(observed, []s.Check{s.Passed("actual-state"), s.Passed("reference-state")}) {
		t.Fatal(observed, e)
	}
	// Transition prefixes retain actual checks and valid reference partial returns.
	projection.ActualState = func(state counterState) int { return state.value }
	reference.CheckTransition = func(counterState, string, s.Transition[counterState, string]) ([]s.Check, error) {
		return []s.Check{s.Passed("reference-edge")}, errors.New("reference edge failed")
	}
	paired, e = s.Paired(reference, projection)
	if e != nil {
		t.Fatal(e)
	}
	pairedModel, e = s.WithOracleGenerate(g, paired)
	if e != nil {
		t.Fatal(e)
	}
	observed = nil
	phases := []string{}
	pairedObserver.CheckError = func(phase string, checks []s.Check, complete bool) error {
		phases = append(phases, phase)
		if phase == "transition" {
			if complete {
				t.Fatal("incomplete transition marked complete")
			}
			observed = checks
		} else if !complete {
			t.Fatal("state batch lost completion")
		}
		return nil
	}
	_, e = s.FuzzObserved(pairedModel, s.FuzzConfig{Cases: 1, MaxSteps: 1, MaxTransitions: 1}, s.RunLimits{}, pairedObserver)
	if e == nil || !reflect.DeepEqual(phases, []string{"state", "transition"}) || !reflect.DeepEqual(observed, []s.Check{s.Passed("actual-edge"), s.Passed("reference-edge")}) {
		t.Fatal(phases, observed, e)
	}
}

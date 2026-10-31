package statelessness_test

import (
	"slices"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

func TestOracleStepCapturesHistoryAndInputBeforeReducer(t *testing.T) {
	state := s.OracleState[[]int, []int]{Model: []int{1}, Oracle: []int{2}}
	input := []int{3}
	model := s.Model[[]int, []int, int]{
		CloneState: func(v []int) []int { input[0] = 30; return slices.Clone(v) }, CloneInput: slices.Clone[[]int], CloneOutput: s.ValueCopy[int],
		CheckState: func([]int) ([]s.Check, error) { return nil, nil },
		Step: func(actual, action []int) (s.Transition[[]int, int], error) {
			state.Model[0], state.Oracle[0], input[0] = 10, 20, 30
			return s.Accept(actual, action[0]), nil
		},
	}
	oracle := s.Oracle[[]int, []int, []int, int]{
		CloneHistory: slices.Clone[[]int], InitialState: func() ([]int, error) { return nil, nil },
		CheckState: func([]int, []int) ([]s.Check, error) { return nil, nil },
		Advance: func(history, action []int, outputs []int, _ s.Disposition) ([]int, error) {
			if history[0] != 2 || action[0] != 3 || outputs[0] != 3 {
				t.Errorf("oracle saw mutated inputs: %v %v %v", history, action, outputs)
			}
			return history, nil
		},
	}
	w, err := s.WithOracle(model, oracle)
	if err != nil {
		t.Fatal(err)
	}
	result, err := w.Step(state, input)
	if err != nil {
		t.Fatal(err)
	}
	if result.State.Model[0] != 1 || result.State.Oracle[0] != 2 {
		t.Fatalf("unexpected state: %v", result.State)
	}
}

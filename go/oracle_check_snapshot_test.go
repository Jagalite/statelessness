package statelessness_test

import (
	s "github.com/Jagalite/statelessness/go"
	"slices"
	"testing"
)

func TestOracleChecksCaptureBeforeChildCallbacks(t *testing.T) {
	before := s.OracleState[[]int, []int]{Model: []int{1}, Oracle: []int{2}}
	input := []int{3}
	after := s.OracleState[[]int, []int]{Model: []int{4}, Oracle: []int{5}}
	output := []int{6}
	tr := s.Accept(after, output)
	m := s.Model[[]int, []int, []int]{
		CloneState: slices.Clone[[]int], CloneInput: slices.Clone[[]int], CloneOutput: slices.Clone[[]int],
		CheckState: func([]int) ([]s.Check, error) { before.Model[0] = 10; before.Oracle[0] = 20; return nil, nil },
		CheckTransition: func([]int, []int, s.Transition[[]int, []int]) ([]s.Check, error) {
			before.Model[0] = 10
			before.Oracle[0] = 20
			input[0] = 30
			after.Model[0] = 40
			after.Oracle[0] = 50
			output[0] = 60
			return nil, nil
		},
	}
	o := s.Oracle[[]int, []int, []int, []int]{
		InitialState: func() ([]int, error) { return nil, nil }, CloneHistory: slices.Clone[[]int],
		Advance: func(h, _ []int, _ [][]int, _ s.Disposition) ([]int, error) { return h, nil },
		CheckState: func(h, actual []int) ([]s.Check, error) {
			if h[0] != 2 || actual[0] != 1 {
				t.Errorf("state changed: %v %v", h, actual)
			}
			return nil, nil
		},
		CheckTransition: func(b s.OracleState[[]int, []int], i []int, a s.Transition[s.OracleState[[]int, []int], []int]) ([]s.Check, error) {
			if b.Model[0] != 1 || b.Oracle[0] != 2 || i[0] != 3 || a.State.Model[0] != 4 || a.State.Oracle[0] != 5 || a.Outputs[0][0] != 6 {
				t.Errorf("transition changed: %v %v %v", b, i, a)
			}
			return nil, nil
		},
	}
	w, err := s.WithOracle(m, o)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := w.CheckState(before); err != nil {
		t.Fatal(err)
	}
	before.Model[0], before.Oracle[0] = 1, 2
	if _, err := w.CheckTransition(before, input, tr); err != nil {
		t.Fatal(err)
	}
}

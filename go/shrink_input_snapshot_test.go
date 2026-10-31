package statelessness

import "testing"

func TestShrinkReplayCapturesFutureInputsBeforeCallbacks(t *testing.T) {
	a, b := 7, 8
	inputs := []*int{&a, &b}
	g := GenerateModel[int, *int, int]{Model: Model[int, *int, int]{
		InitialState: func() (int, error) { a, b = 99, 99; return 0, nil },
		CloneState:   ValueCopy[int], CloneInput: func(v *int) *int { n := *v; return &n }, CloneOutput: ValueCopy[int],
		Step: func(s int, v *int) (Transition[int, int], error) { return Accept[int, int](s + *v), nil },
		CheckState: func(s int) ([]Check, error) {
			if s == 15 {
				return []Check{Failed("target", "")}, nil
			}
			return nil, nil
		},
	}}
	w := shrinkWork[int, *int, int]{g: g}
	result, err := w.replay(inputs)
	if err != nil || result.kind != "failure" || len(result.failure.Inputs) != 2 || *result.failure.Inputs[0] != 7 || *result.failure.Inputs[1] != 8 {
		t.Fatalf("replay = %#v, %v", result, err)
	}
}

func TestShrinkReplayDefersUnreachedCaptureFailure(t *testing.T) {
	limit := uint64(1)
	clones := 0
	g := GenerateModel[int, int, int]{Model: Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil }, CloneState: ValueCopy[int], CloneOutput: ValueCopy[int],
		CloneInput: func(v int) int {
			clones++
			if v == 2 {
				panic("unreached")
			}
			return v
		},
		Step:       func(s, v int) (Transition[int, int], error) { return Accept[int, int](s + v), nil },
		CheckState: func(int) ([]Check, error) { return nil, nil },
	}}
	w := shrinkWork[int, int, int]{g: g, maximum: &limit}
	result, err := w.replay([]int{1, 2})
	if err != nil || result.kind != "stopped" || result.stop != "transition_limit" || clones == 0 {
		t.Fatalf("replay = %#v, %v, clones %d", result, err, clones)
	}
}

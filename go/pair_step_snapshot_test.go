package statelessness

import "testing"

func TestPairCloneStateCapturesPendingAddressesBeforeInputHooks(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	state := PairState[[]int, []int, []int, []int]{Left: []int{1}, Right: []int{2}, Pending: []PairAddress[[]int, []int]{PairLeft[[]int, []int]([]int{3}), PairRight[[]int, []int]([]int{4})}}
	child.CloneInput = func(v []int) []int {
		state.Pending[1].Side = "invalid"
		return append([]int(nil), v...)
	}
	wiring := PairWiring[[]int, []int, []int, []int, int, int]{Messages: func(PairAddress[int, int]) ([]PairAddress[[]int, []int], error) { return nil, nil }, CheckState: func(PairState[[]int, []int, []int, []int], *CheckSink) error { return nil }}
	pair, err := NewPair(child, child, wiring, 2)
	if err != nil {
		t.Fatal(err)
	}
	got := pair.CloneState(state)
	if got.Pending[1].Side != "right" || got.Pending[1].Right[0] != 4 {
		t.Fatalf("pending address changed: %+v", got.Pending[1])
	}
}

func TestPairStepCapturesSelectedInputAndChildStateBeforeCloneCallbacks(t *testing.T) {
	for _, kind := range []string{"local", "deliver"} {
		t.Run(kind, func(t *testing.T) {
			var counts lazyCounts
			child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
			originalState, originalInput := []int{1}, []int{2}
			right := child
			right.CloneState = func(s []int) []int { originalState[0] = 91; originalInput[0] = 92; return append([]int(nil), s...) }
			var stateSeen, inputSeen int
			child.Step = func(s, i []int) (Transition[[]int, int], error) {
				stateSeen, inputSeen = s[0], i[0]
				return Transition[[]int, int]{State: s, Disposition: Accepted()}, nil
			}
			wiring := PairWiring[[]int, []int, []int, []int, int, int]{Messages: func(PairAddress[int, int]) ([]PairAddress[[]int, []int], error) { return nil, nil }, CheckState: func(PairState[[]int, []int, []int, []int], *CheckSink) error { return nil }}
			pair, err := NewPair(child, right, wiring, 2)
			if err != nil {
				t.Fatal(err)
			}
			state := PairState[[]int, []int, []int, []int]{Left: originalState, Right: []int{3}}
			input := PairInput[[]int, []int]{Kind: "local", Address: PairLeft[[]int, []int](originalInput)}
			if kind == "deliver" {
				state.Pending = []PairAddress[[]int, []int]{PairLeft[[]int, []int](originalInput)}
				input = PairInput[[]int, []int]{Kind: "deliver", Index: 0}
			}
			_, err = pair.Step(state, input)
			if err != nil {
				t.Fatal(err)
			}
			if stateSeen != 1 || inputSeen != 2 {
				t.Fatalf("reducer observed state=%d input=%d", stateSeen, inputSeen)
			}
		})
	}
}

func TestPairStepCapturesPendingTagsBeforeInputClone(t *testing.T) {
	for _, kind := range []string{"local", "deliver"} {
		t.Run(kind, func(t *testing.T) {
			var counts lazyCounts
			child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
			state := PairState[[]int, []int, []int, []int]{Left: []int{1}, Right: []int{2}, Pending: []PairAddress[[]int, []int]{PairLeft[[]int, []int]([]int{3}), PairRight[[]int, []int]([]int{4})}}
			child.CloneInput = func(v []int) []int { state.Pending[1].Side = "invalid"; return append([]int(nil), v...) }
			wiring := PairWiring[[]int, []int, []int, []int, int, int]{Messages: func(PairAddress[int, int]) ([]PairAddress[[]int, []int], error) { return nil, nil }, CheckState: func(PairState[[]int, []int, []int, []int], *CheckSink) error { return nil }}
			pair, err := NewPair(child, child, wiring, 2)
			if err != nil {
				t.Fatal(err)
			}
			input := PairInput[[]int, []int]{Kind: "local", Address: PairLeft[[]int, []int]([]int{5})}
			if kind == "deliver" {
				input = PairInput[[]int, []int]{Kind: "deliver", Index: 0}
			}
			tr, err := pair.Step(state, input)
			if err != nil {
				t.Fatal(err)
			}
			if tr.State.Pending[len(tr.State.Pending)-1].Side != "right" {
				t.Fatalf("pending tag changed: %+v", tr.State.Pending)
			}
		})
	}
}

func TestPairStepCapturesOutputsBeforeWiringCallbacks(t *testing.T) {
	outputs := [][]int{{1}, {2}}
	clone := func(v []int) []int { return append([]int(nil), v...) }
	var childOutputs [][]int
	cloneOutput := func(v []int) []int {
		copy := clone(v)
		childOutputs = append(childOutputs, copy)
		return copy
	}
	child := Model[[]int, []int, []int]{
		CloneState: clone, CloneInput: clone, CloneOutput: cloneOutput,
		CheckState: func([]int) ([]Check, error) { return nil, nil },
		Step: func(s, _ []int) (Transition[[]int, []int], error) {
			return Transition[[]int, []int]{State: s, Outputs: outputs, Disposition: Accepted()}, nil
		},
	}
	var seen []int
	wiring := PairWiring[[]int, []int, []int, []int, []int, []int]{
		Messages: func(output PairAddress[[]int, []int]) ([]PairAddress[[]int, []int], error) {
			seen = append(seen, output.Left[0])
			childOutputs[1][0] = 99
			return nil, nil
		},
		CheckState: func(PairState[[]int, []int, []int, []int], *CheckSink) error { return nil },
	}
	pair, err := NewPair(child, child, wiring, 2)
	if err != nil {
		t.Fatal(err)
	}
	result, err := pair.Step(PairState[[]int, []int, []int, []int]{Left: []int{0}, Right: []int{0}}, PairInput[[]int, []int]{Kind: "local", Address: PairLeft[[]int, []int]([]int{0})})
	if err != nil {
		t.Fatal(err)
	}
	if len(seen) != 2 || seen[0] != 1 || seen[1] != 2 {
		t.Fatalf("wiring observed %v", seen)
	}
	if result.Outputs[1].Left[0] != 2 {
		t.Fatalf("returned outputs changed: %+v", result.Outputs)
	}
}

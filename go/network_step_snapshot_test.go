package statelessness

import "testing"

func TestNetworkStepCapturesInputBeforeStateClone(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	state, input := []int{3}, []int{7}
	child.CloneState = func(v []int) []int { out := append([]int(nil), v...); input[0] = 99; state[0] = 88; return out }
	var observedState, observedInput int
	child.Step = func(s, i []int) (Transition[[]int, int], error) {
		observedState, observedInput = s[0], i[0]
		return Accept[[]int, int](s), nil
	}
	w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
	n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, child}}, w, DefaultQueuePolicy())
	if err != nil {
		t.Fatal(err)
	}
	_, err = n.Step(NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, state}}}, NetworkInput[[]int]{Kind: "local", Local: NetworkAddress[[]int]{1, input}})
	if err != nil {
		t.Fatal(err)
	}
	if observedState != 3 || observedInput != 7 {
		t.Fatalf("observed state=%d input=%d", observedState, observedInput)
	}
}

func TestNetworkDuplicateCapturesPayloadBeforeStateClone(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	payload := []int{7}
	child.CloneState = func(v []int) []int { out := append([]int(nil), v...); payload[0] = 99; return out }
	w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
	policy := DefaultQueuePolicy()
	policy.AllowDuplicate = true
	n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, child}}, w, policy)
	if err != nil {
		t.Fatal(err)
	}
	state := NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, []int{3}}}, Pending: []NetworkMessage[[]int]{{1, NetworkRoute[[]int]{1, 0, payload}}}}
	result, err := n.Step(state, NetworkInput[[]int]{Kind: "duplicate", Index: 0})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.State.Pending) != 2 || result.State.Pending[0].Route.Input[0] != 7 || result.State.Pending[1].Route.Input[0] != 7 {
		t.Fatalf("duplicate payloads: %+v", result.State.Pending)
	}
}

func TestNetworkLocalStepPreservesPendingBeforeStateClone(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	payload := []int{7}
	child.CloneState = func(v []int) []int { out := append([]int(nil), v...); payload[0] = 99; return out }
	w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
	n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, child}}, w, DefaultQueuePolicy())
	if err != nil {
		t.Fatal(err)
	}
	state := NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, []int{3}}}, Pending: []NetworkMessage[[]int]{{1, NetworkRoute[[]int]{1, 0, payload}}}}
	result, err := n.Step(state, NetworkInput[[]int]{Kind: "local", Local: NetworkAddress[[]int]{1, []int{2}}})
	if err != nil {
		t.Fatal(err)
	}
	if result.State.Pending[0].Route.Input[0] != 7 {
		t.Fatalf("pending: %+v", result.State.Pending)
	}
}

func TestNetworkCloneAndChecksCapturePendingBeforeMachineClone(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	payload := []int{7}
	child.CloneState = func(v []int) []int { payload[0] = 99; return append([]int(nil), v...) }
	observed := 0
	w := NetworkWiring[[]int, []int, int]{
		Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil },
		CheckState: func(s NetworkState[[]int, []int], _ *CheckSink) error {
			observed = s.Pending[0].Route.Input[0]
			return nil
		},
	}
	n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, child}}, w, DefaultQueuePolicy())
	if err != nil {
		t.Fatal(err)
	}
	state := NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, []int{3}}}, Pending: []NetworkMessage[[]int]{{1, NetworkRoute[[]int]{1, 0, payload}}}}
	if got := n.CloneState(state).Pending[0].Route.Input[0]; got != 7 {
		t.Fatalf("clone pending = %d", got)
	}
	payload[0] = 7
	if _, err := n.CheckState(state); err != nil {
		t.Fatal(err)
	}
	if observed != 7 {
		t.Fatalf("global check pending = %d", observed)
	}
}


package statelessness

import "testing"

func TestNetworkInputsCapturesChannelsBeforeStateClone(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	state := NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, []int{3}}}, Pending: []NetworkMessage[[]int]{{1, NetworkRoute[[]int]{1, 0, []int{1}}}, {1, NetworkRoute[[]int]{1, 0, []int{2}}}}}
	child.CloneState = func(v []int) []int { state.Pending[1].Route.Channel = 1; return append([]int(nil), v...) }
	wiring := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
	n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, child}}, wiring, DefaultQueuePolicy())
	if err != nil {
		t.Fatal(err)
	}
	iter, err := n.Inputs(state)
	if err != nil {
		t.Fatal(err)
	}
	input, ok, err := iter()
	if err != nil || !ok || input.Kind != "deliver" || input.Index != 0 {
		t.Fatalf("first input=%+v ok=%v err=%v", input, ok, err)
	}
	input, ok, err = iter()
	if err != nil || ok {
		t.Fatalf("unexpected input=%+v ok=%v err=%v", input, ok, err)
	}
}

func TestNetworkInputsCapturesMachineIDsBeforeStateClone(t *testing.T) {
	var counts lazyCounts
	first := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	second := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	state := NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, []int{1}}, {2, []int{2}}}}
	first.CloneState = func(v []int) []int { state.Machines[1].Machine = 999; return append([]int(nil), v...) }
	wiring := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
	n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, first}, {2, second}}, wiring, DefaultQueuePolicy())
	if err != nil {
		t.Fatal(err)
	}
	iter, err := n.Inputs(state)
	if err != nil {
		t.Fatal(err)
	}
	_, ok, err := iter()
	if err != nil || ok {
		t.Fatalf("ok=%v err=%v", ok, err)
	}
}

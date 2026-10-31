package statelessness

import (
	"slices"
	"testing"
)

func TestComposedEqualityRequiresAllCapabilities(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	child.EqualStates = slices.Equal[[]int]
	for _, mode := range []string{"left", "right", "input", "none"} {
		children := [2]Model[[]int, []int, int]{child, child}
		if mode == "left" {
			children[0].EqualStates = nil
		}
		if mode == "right" {
			children[1].EqualStates = nil
		}
		inputEqual := func(a, b []int) bool { return slices.Equal(a, b) }
		pairEqual := func(a, b PairAddress[[]int, []int]) bool {
			return a.Side == b.Side && slices.Equal(a.Left, b.Left) && slices.Equal(a.Right, b.Right)
		}
		if mode == "input" {
			inputEqual, pairEqual = nil, nil
		}
		w := PairWiring[[]int, []int, []int, []int, int, int]{Messages: func(PairAddress[int, int]) ([]PairAddress[[]int, []int], error) { return nil, nil }, CheckState: func(PairState[[]int, []int, []int, []int], *CheckSink) error { return nil }}
		p, err := NewPair(children[0], children[1], w, 1, pairEqual)
		if err != nil {
			t.Fatal(err)
		}
		nw := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
		n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, children[0]}, {2, children[1]}}, nw, DefaultQueuePolicy())
		if err != nil {
			t.Fatal(err)
		}
		m := n.WithInputEquality(inputEqual)
		if (p.EqualStates != nil) != (mode == "none") || (m.EqualStates != nil) != (mode == "none") {
			t.Fatalf("mode %s: incorrect equality availability", mode)
		}
	}
}

func TestPairSnapshotsInitialStateBeforeOtherChildAndIsolatesEquality(t *testing.T) {
	shared := []int{1}
	child := Model[[]int, []int, int]{InitialState: func() ([]int, error) { return shared, nil }, CloneState: slices.Clone[[]int], CloneInput: slices.Clone[[]int], CloneOutput: ValueCopy[int], CheckState: func([]int) ([]Check, error) { return nil, nil }, EqualStates: func(a, b []int) bool { same := slices.Equal(a, b); a[0], b[0] = 8, 9; return same }}
	right := child
	right.InitialState = func() ([]int, error) { shared[0] = 2; return []int{2}, nil }
	wiring := PairWiring[[]int, []int, []int, []int, int, int]{Messages: func(PairAddress[int, int]) ([]PairAddress[[]int, []int], error) { return nil, nil }, CheckState: func(PairState[[]int, []int, []int, []int], *CheckSink) error { return nil }}
	pair, err := NewPair(child, right, wiring, 3, func(a, b PairAddress[[]int, []int]) bool {
		same := slices.Equal(a.Left, b.Left)
		a.Left[0], b.Left[0] = 6, 7
		return same
	})
	if err != nil {
		t.Fatal(err)
	}
	initial, err := pair.InitialState()
	if err != nil || initial.Left[0] != 1 || initial.Right[0] != 2 {
		t.Fatalf("initial: %+v %v", initial, err)
	}
	a, b := []int{1}, []int{1}
	left := PairState[[]int, []int, []int, []int]{Left: a, Right: a, Pending: []PairAddress[[]int, []int]{PairLeft[[]int, []int](a), PairLeft[[]int, []int](a)}}
	rightState := PairState[[]int, []int, []int, []int]{Left: b, Right: b, Pending: []PairAddress[[]int, []int]{PairLeft[[]int, []int](b), PairLeft[[]int, []int](b)}}
	if !pair.EqualStates(left, rightState) || a[0] != 1 || b[0] != 1 {
		t.Fatalf("pair equality mutated or diverged: %v %v", a, b)
	}
}

func TestNetworkEqualityIsolatesMachineAndPendingCallbacks(t *testing.T) {
	child := Model[[]int, []int, int]{CloneState: slices.Clone[[]int], CloneInput: slices.Clone[[]int], CloneOutput: ValueCopy[int], CheckState: func([]int) ([]Check, error) { return nil, nil }, EqualStates: func(a, b []int) bool { same := slices.Equal(a, b); a[0], b[0] = 8, 9; return same }}
	w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
	n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, child}, {2, child}}, w, DefaultQueuePolicy())
	if err != nil {
		t.Fatal(err)
	}
	m := n.WithInputEquality(func(a, b []int) bool { same := slices.Equal(a, b); a[0], b[0] = 6, 7; return same })
	makeState := func(v []int) NetworkState[[]int, []int] {
		return NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, v}, {2, v}}, Pending: []NetworkMessage[[]int]{{Source: 1, Route: NetworkRoute[[]int]{Destination: 2, Input: v}}, {Source: 1, Route: NetworkRoute[[]int]{Destination: 2, Input: v}}}}
	}
	a, b := []int{1}, []int{1}
	if !m.EqualStates(makeState(a), makeState(b)) || a[0] != 1 || b[0] != 1 {
		t.Fatalf("network equality mutated or diverged: %v %v", a, b)
	}
}

func TestNetworkEqualityCapturesQueueBeforeMachineClone(t *testing.T) {
	queued := []int{3}
	child := Model[[]int, []int, int]{
		CloneState: func(v []int) []int { queued[0] = 9; return slices.Clone(v) },
		CloneInput: slices.Clone[[]int], CloneOutput: ValueCopy[int],
		CheckState: func([]int) ([]Check, error) { return nil, nil }, EqualStates: slices.Equal[[]int],
	}
	w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
	n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, child}}, w, DefaultQueuePolicy())
	if err != nil {
		t.Fatal(err)
	}
	state := func(q []int) NetworkState[[]int, []int] {
		return NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, []int{1}}}, Pending: []NetworkMessage[[]int]{{Source: 1, Route: NetworkRoute[[]int]{Destination: 1, Input: q}}}}
	}
	if !n.WithInputEquality(slices.Equal[[]int]).EqualStates(state([]int{3}), state(queued)) {
		t.Fatal("machine clone changed queued input before it was captured")
	}
}

func TestPairEqualityCapturesQueueBeforeChildClone(t *testing.T) {
	queued := []int{3}
	child := Model[[]int, []int, int]{
		CloneState: func(v []int) []int { queued[0] = 9; return slices.Clone(v) },
		CloneInput: slices.Clone[[]int], CloneOutput: ValueCopy[int],
		CheckState: func([]int) ([]Check, error) { return nil, nil }, EqualStates: slices.Equal[[]int],
	}
	w := PairWiring[[]int, []int, []int, []int, int, int]{Messages: func(PairAddress[int, int]) ([]PairAddress[[]int, []int], error) { return nil, nil }, CheckState: func(PairState[[]int, []int, []int, []int], *CheckSink) error { return nil }}
	p, err := NewPair(child, child, w, 1, func(a, b PairAddress[[]int, []int]) bool { return a.Side == b.Side && slices.Equal(a.Left, b.Left) })
	if err != nil {
		t.Fatal(err)
	}
	state := func(q []int) PairState[[]int, []int, []int, []int] {
		return PairState[[]int, []int, []int, []int]{Left: []int{1}, Right: []int{2}, Pending: []PairAddress[[]int, []int]{PairLeft[[]int, []int](q)}}
	}
	if !p.EqualStates(state([]int{3}), state(queued)) {
		t.Fatal("child clone changed pending input before capture")
	}
}

func TestPairEqualityComparesChildrenBeforePendingLength(t *testing.T) {
	var compared []string
	child := Model[int, int, int]{CloneState: ValueCopy[int], CloneInput: ValueCopy[int], CloneOutput: ValueCopy[int], CheckState: func(int) ([]Check, error) { return nil, nil }}
	left, right := child, child
	left.EqualStates = func(a, b int) bool { compared = append(compared, "left"); return a == b }
	right.EqualStates = func(a, b int) bool { compared = append(compared, "right"); return a == b }
	w := PairWiring[int, int, int, int, int, int]{Messages: func(PairAddress[int, int]) ([]PairAddress[int, int], error) { return nil, nil }, CheckState: func(PairState[int, int, int, int], *CheckSink) error { return nil }}
	p, err := NewPair(left, right, w, 1, func(a, b PairAddress[int, int]) bool { t.Fatal("unexpected input comparison"); return true })
	if err != nil {
		t.Fatal(err)
	}
	a := PairState[int, int, int, int]{Left: 1, Right: 2}
	b := PairState[int, int, int, int]{Left: 1, Right: 2, Pending: []PairAddress[int, int]{PairLeft[int, int](3)}}
	if p.EqualStates(a, b) || !slices.Equal(compared, []string{"left", "right"}) {
		t.Fatalf("comparison order: %v", compared)
	}
}

func TestComposedEqualitySnapshotsAllOperandsBeforeCallbacks(t *testing.T) {
	for _, kind := range []string{"pair", "network"} {
		t.Run(kind, func(t *testing.T) {
			var counts lazyCounts
			child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
			later, queued := []int{2}, []int{3}
			child.EqualStates = func(a, b []int) bool { equal := slices.Equal(a, b); later[0], queued[0] = 20, 30; return equal }
			if kind == "pair" {
				w := PairWiring[[]int, []int, []int, []int, int, int]{Messages: func(PairAddress[int, int]) ([]PairAddress[[]int, []int], error) { return nil, nil }, CheckState: func(PairState[[]int, []int, []int, []int], *CheckSink) error { return nil }}
				p, err := NewPair(child, child, w, 2, func(a, b PairAddress[[]int, []int]) bool { return slices.Equal(a.Left, b.Left) })
				if err != nil {
					t.Fatal(err)
				}
				a := PairState[[]int, []int, []int, []int]{Left: []int{1}, Right: []int{2}, Pending: []PairAddress[[]int, []int]{PairLeft[[]int, []int]([]int{3})}}
				b := PairState[[]int, []int, []int, []int]{Left: []int{1}, Right: later, Pending: []PairAddress[[]int, []int]{PairLeft[[]int, []int](queued)}}
				if !p.EqualStates(a, b) {
					t.Fatal("callback altered later equality input")
				}
			} else {
				w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
				n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, child}, {2, child}}, w, DefaultQueuePolicy())
				if err != nil {
					t.Fatal(err)
				}
				makeState := func(r, q []int) NetworkState[[]int, []int] {
					return NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, []int{1}}, {2, r}}, Pending: []NetworkMessage[[]int]{{Source: 1, Route: NetworkRoute[[]int]{Destination: 2, Input: q}}}}
				}
				if !n.WithInputEquality(slices.Equal[[]int]).EqualStates(makeState([]int{2}, []int{3}), makeState(later, queued)) {
					t.Fatal("callback altered later equality input")
				}
			}
		})
	}
}

func TestPairSnapshotsVariadicEqualityCallback(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	child.EqualStates = slices.Equal[[]int]
	w := PairWiring[[]int, []int, []int, []int, int, int]{Messages: func(PairAddress[int, int]) ([]PairAddress[[]int, []int], error) { return nil, nil }, CheckState: func(PairState[[]int, []int, []int, []int], *CheckSink) error { return nil }}
	equality := []func(PairAddress[[]int, []int], PairAddress[[]int, []int]) bool{func(a, b PairAddress[[]int, []int]) bool { return slices.Equal(a.Left, b.Left) }}
	p, err := NewPair(child, child, w, 1, equality...)
	if err != nil {
		t.Fatal(err)
	}
	equality[0] = nil
	s := PairState[[]int, []int, []int, []int]{Left: []int{1}, Right: []int{2}, Pending: []PairAddress[[]int, []int]{PairLeft[[]int, []int]([]int{3})}}
	if !p.EqualStates(s, s) {
		t.Fatal("caller changed the retained equality callback")
	}
}

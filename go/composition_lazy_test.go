package statelessness

import (
	"errors"
	"reflect"
	"testing"
)

type lazyCounts struct{ init, next int }

func lazyChild(count *lazyCounts, makeIterator func([]int) (Iterator[[]int], error)) Model[[]int, []int, int] {
	clone := func(v []int) []int { return append([]int(nil), v...) }
	return Model[[]int, []int, int]{
		InitialState: func() ([]int, error) { return []int{0}, nil },
		CloneState:   clone, CloneInput: clone, CloneOutput: ValueCopy[int],
		Step:       func(s []int, _ []int) (Transition[[]int, int], error) { return Accept[[]int, int](s), nil },
		CheckState: func([]int) ([]Check, error) { return nil, nil },
		Inputs: func(s []int) (Iterator[[]int], error) {
			count.init++
			it, err := makeIterator(s)
			if err != nil || it == nil {
				return it, err
			}
			return func() ([]int, bool, error) { count.next++; return it() }, nil
		},
	}
}
func lazyPair(t *testing.T, left, right Model[[]int, []int, int], bound int) Model[PairState[[]int, []int, []int, []int], PairInput[[]int, []int], PairAddress[int, int]] {
	t.Helper()
	w := PairWiring[[]int, []int, []int, []int, int, int]{Messages: func(PairAddress[int, int]) ([]PairAddress[[]int, []int], error) { return nil, nil }, CheckState: func(PairState[[]int, []int, []int, []int], *CheckSink) error { return nil }}
	pair, err := NewPair(left, right, w, bound)
	if err != nil {
		t.Fatal(err)
	}
	return pair
}

func TestComposedInitialStateFollowsChildAvailability(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	missing := child
	missing.InitialState = nil
	for _, children := range [][2]Model[[]int, []int, int]{{missing, child}, {child, missing}, {child, child}} {
		want := children[0].InitialState != nil && children[1].InitialState != nil
		pair := lazyPair(t, children[0], children[1], 1)
		if (pair.InitialState != nil) != want {
			t.Fatal("pair initial hook availability")
		}
		w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
		network, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, children[0]}, {2, children[1]}}, w, DefaultQueuePolicy())
		if err != nil || (network.InitialState != nil) != want {
			t.Fatal("network initial hook availability", err)
		}
		if want {
			if _, err = pair.InitialState(); err != nil {
				t.Fatal(err)
			}
			if _, err = network.InitialState(); err != nil {
				t.Fatal(err)
			}
		}
	}
}

func TestComposedOptionalStepAndInputsFollowChildren(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	for _, missingHook := range []string{"step", "inputs", "none"} {
		for _, missingSide := range []int{0, 1} {
			children := [2]Model[[]int, []int, int]{child, child}
			switch missingHook {
			case "step":
				children[missingSide].Step = nil
			case "inputs":
				children[missingSide].Inputs = nil
			}
			pair := lazyPair(t, children[0], children[1], 1)
			w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
			n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, children[0]}, {2, children[1]}}, w, DefaultQueuePolicy())
			if err != nil {
				t.Fatal(err)
			}
			if (pair.Step != nil) != (missingHook != "step") || (n.Step != nil) != (missingHook != "step") || (pair.Inputs != nil) != (missingHook != "inputs") || (n.Inputs != nil) != (missingHook != "inputs") {
				t.Fatalf("missing %s side %d: incorrect composite hooks", missingHook, missingSide)
			}
		}
	}
}
func TestPairInputsLazyOwnedAndErrors(t *testing.T) {
	var l, r lazyCounts
	failure := errors.New("right init failure")
	left := lazyChild(&l, func(s []int) (Iterator[[]int], error) { return Values([][]int{{s[0]}}), nil })
	right := lazyChild(&r, func([]int) (Iterator[[]int], error) { return nil, failure })
	pair := lazyPair(t, left, right, 1)
	state := PairState[[]int, []int, []int, []int]{Left: []int{3}, Right: []int{4}, Pending: []PairAddress[[]int, []int]{PairLeft[[]int, []int]([]int{8})}}
	it, err := pair.Inputs(state)
	if err != nil {
		t.Fatal(err)
	}
	if l.init != 0 || r.init != 0 {
		t.Fatal("child initialized eagerly", l, r)
	}
	state.Left[0] = 30
	state.Right[0] = 40
	state.Pending = nil
	value, ok, err := it()
	if err != nil || !ok || value.Address.Side != "left" || !reflect.DeepEqual(value.Address.Left, []int{3}) || l.next != 1 || r.init != 0 {
		t.Fatal(value, ok, err, l, r)
	}
	_, ok, err = it()
	if ok || !errors.Is(err, failure) || r.init != 1 {
		t.Fatal("late child error", ok, err, l, r)
	}
	_, ok, err = it()
	if ok || err != nil {
		t.Fatal("iterator resumed after error", ok, err)
	}
}
func TestPairInputsIncrementalOrderAndSnapshots(t *testing.T) {
	var l, r lazyCounts
	shared := []int{0}
	left := lazyChild(&l, func(s []int) (Iterator[[]int], error) {
		i := 0
		return func() ([]int, bool, error) {
			if i == 2 {
				return nil, false, nil
			}
			shared[0] = s[0] + i
			i++
			return shared, true, nil
		}, nil
	})
	right := lazyChild(&r, func(s []int) (Iterator[[]int], error) { return Values([][]int{{s[0]}}), nil })
	pair := lazyPair(t, left, right, 2)
	state := PairState[[]int, []int, []int, []int]{Left: []int{3}, Right: []int{7}, Pending: []PairAddress[[]int, []int]{PairLeft[[]int, []int]([]int{1}), PairRight[[]int, []int]([]int{2})}}
	it, err := pair.Inputs(state)
	if err != nil {
		t.Fatal(err)
	}
	state.Right[0] = 99
	state.Pending = nil
	first, ok, err := it()
	if !ok || err != nil || l.next != 1 || r.init != 0 {
		t.Fatal(first, ok, err, l, r)
	}
	second, _, _ := it()
	third, _, _ := it()
	a, _, _ := it()
	b, _, _ := it()
	_, ok, err = it()
	if err != nil || ok || !reflect.DeepEqual(first.Address.Left, []int{3}) || !reflect.DeepEqual(second.Address.Left, []int{4}) || !reflect.DeepEqual(third.Address.Right, []int{7}) || a.Kind != "deliver" || a.Index != 0 || b.Kind != "deliver" || b.Index != 1 {
		t.Fatal(first, second, third, a, b, ok, err)
	}
}
func TestNetworkInputsLazyQueueOrderAndSnapshots(t *testing.T) {
	var a, b lazyCounts
	first := lazyChild(&a, func(s []int) (Iterator[[]int], error) { return Values([][]int{{s[0]}}), nil })
	second := lazyChild(&b, func(s []int) (Iterator[[]int], error) { return Values([][]int{{s[0]}}), nil })
	policy := DefaultQueuePolicy()
	policy.MaxPending = 5
	policy.MaxPerChannel = 3
	policy.AllowDrop = true
	policy.AllowDuplicate = true
	w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
	n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{2, second}, {1, first}}, w, policy)
	if err != nil {
		t.Fatal(err)
	}
	state := NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, []int{10}}, {2, []int{20}}}, Pending: []NetworkMessage[[]int]{{Source: 1, Route: NetworkRoute[[]int]{Destination: 2, Channel: 1, Input: []int{1}}}, {Source: 1, Route: NetworkRoute[[]int]{Destination: 2, Channel: 1, Input: []int{2}}}, {Source: 2, Route: NetworkRoute[[]int]{Destination: 1, Channel: 2, Input: []int{3}}}}}
	it, err := n.Inputs(state)
	if err != nil {
		t.Fatal(err)
	}
	if a.init != 0 || b.init != 0 {
		t.Fatal("eager child initialization")
	}
	state.Machines[0].Value[0] = 99
	state.Machines[1].Value[0] = 98
	state.Pending[1].Route.Channel = 99
	state.Pending = nil
	var got []NetworkInput[[]int]
	for {
		value, ok, e := it()
		if e != nil {
			t.Fatal(e)
		}
		if !ok {
			break
		}
		got = append(got, value)
	}
	if len(got) != 10 || got[0].Local.Machine != 1 || !reflect.DeepEqual(got[0].Local.Value, []int{10}) || got[1].Local.Machine != 2 || !reflect.DeepEqual(got[1].Local.Value, []int{20}) {
		t.Fatal(got)
	}
	wantKinds := []string{"deliver", "drop", "duplicate", "drop", "duplicate", "deliver", "drop", "duplicate"}
	wantIndices := []uint32{0, 0, 0, 1, 1, 2, 2, 2}
	for i := range wantKinds {
		v := got[i+2]
		if v.Kind != wantKinds[i] || v.Index != wantIndices[i] {
			t.Fatalf("queue[%d] = %#v", i, v)
		}
	}
}
func TestPairInfiniteChildProducesWithoutMaterialization(t *testing.T) {
	var l, r lazyCounts
	left := lazyChild(&l, func([]int) (Iterator[[]int], error) {
		return func() ([]int, bool, error) { return []int{1}, true, nil }, nil
	})
	right := lazyChild(&r, func([]int) (Iterator[[]int], error) { return Values([][]int{{2}}), nil })
	pair := lazyPair(t, left, right, 0)
	it, err := pair.Inputs(PairState[[]int, []int, []int, []int]{Left: []int{0}, Right: []int{0}})
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		v, ok, e := it()
		if e != nil || !ok || v.Address.Side != "left" {
			t.Fatal(v, ok, e)
		}
	}
	if l.next != 3 || r.init != 0 {
		t.Fatal("unexpected enumeration", l, r)
	}
}

func TestNetworkInputsPropagatesDeferredIteratorFailure(t *testing.T) {
	var a, b lazyCounts
	failure := errors.New("iterator failure")
	first := lazyChild(&a, func([]int) (Iterator[[]int], error) {
		calls := 0
		return func() ([]int, bool, error) {
			calls++
			if calls == 1 {
				return []int{7}, true, nil
			}
			return nil, false, failure
		}, nil
	})
	second := lazyChild(&b, func([]int) (Iterator[[]int], error) { return Values([][]int{{8}}), nil })
	w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
	network, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, first}, {2, second}}, w, DefaultQueuePolicy())
	if err != nil {
		t.Fatal(err)
	}
	state, err := network.InitialState()
	if err != nil {
		t.Fatal(err)
	}
	it, err := network.Inputs(state)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok, err := it(); !ok || err != nil {
		t.Fatal(ok, err)
	}
	if _, ok, err := it(); ok || !errors.Is(err, failure) || b.init != 0 {
		t.Fatal(ok, err, b)
	}
	if _, ok, err := it(); ok || err != nil {
		t.Fatal("iterator resumed after failure", ok, err)
	}
}

func TestPairInfiniteChildHonorsSearchTransitionLimit(t *testing.T) {
	var l, r lazyCounts
	left := lazyChild(&l, func([]int) (Iterator[[]int], error) {
		return func() ([]int, bool, error) { return []int{1}, true, nil }, nil
	})
	right := lazyChild(&r, func([]int) (Iterator[[]int], error) { return Values([][]int{{2}}), nil })
	pair := lazyPair(t, left, right, 0)
	pair.EqualStates = func(a, b PairState[[]int, []int, []int, []int]) bool { return reflect.DeepEqual(a, b) }
	report, err := Enumerate(pair, SearchConfig{MaxStates: 2, MaxDepth: 2, MaxTransitions: 1})
	if err != nil || report.Termination != "transition_limit" || report.Transitions != 1 || l.next != 2 || r.init != 0 {
		t.Fatal(report, err, l, r)
	}
}

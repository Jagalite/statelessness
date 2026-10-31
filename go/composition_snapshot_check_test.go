package statelessness

import (
	"strings"
	"testing"
)

func TestPairTransitionCheckCapturesInputBeforeStateClone(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	input := []int{2}
	child.CloneState = func(v []int) []int { input[0] = 99; return append([]int(nil), v...) }
	seen := 0
	child.CheckTransition = func(_ []int, i []int, _ Transition[[]int, int]) ([]Check, error) { seen = i[0]; return nil, nil }
	w := PairWiring[[]int, []int, []int, []int, int, int]{Messages: func(PairAddress[int, int]) ([]PairAddress[[]int, []int], error) { return nil, nil }, CheckState: func(PairState[[]int, []int, []int, []int], *CheckSink) error { return nil }}
	p, err := NewPair(child, child, w, 1)
	if err != nil {
		t.Fatal(err)
	}
	s := PairState[[]int, []int, []int, []int]{Left: []int{1}, Right: []int{3}}
	i := PairInput[[]int, []int]{Kind: "local", Address: PairLeft[[]int, []int](input)}
	tr := Transition[PairState[[]int, []int, []int, []int], PairAddress[int, int]]{State: s, Disposition: Accepted()}
	checks := []Check{}
	err = invokeChecks("test", &checks, func(sink *CheckSink) error { return p.CheckTransitionInto(s, i, tr, sink) })
	if err != nil || seen != 2 {
		t.Fatalf("seen=%d err=%v", seen, err)
	}
}

func TestComposedStateChecksSnapshotAllChildrenBeforeCallbacks(t *testing.T) {
	for _, kind := range []string{"pair", "network"} {
		t.Run(kind, func(t *testing.T) {
			var counts lazyCounts
			left := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
			right := left
			originalRight := []int{7}
			observed, global := 0, 0
			left.CheckState = func([]int) ([]Check, error) { originalRight[0] = 99; return nil, nil }
			right.CheckState = func(s []int) ([]Check, error) { observed = s[0]; return nil, nil }
			if kind == "pair" {
				w := PairWiring[[]int, []int, []int, []int, int, int]{Messages: func(PairAddress[int, int]) ([]PairAddress[[]int, []int], error) { return nil, nil }, CheckState: func(s PairState[[]int, []int, []int, []int], _ *CheckSink) error { global = s.Right[0]; return nil }}
				pair, err := NewPair(left, right, w, 1)
				if err != nil {
					t.Fatal(err)
				}
				_, err = pair.CheckState(PairState[[]int, []int, []int, []int]{Left: []int{1}, Right: originalRight})
				if err != nil {
					t.Fatal(err)
				}
			} else {
				w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(s NetworkState[[]int, []int], _ *CheckSink) error { global = s.Machines[1].Value[0]; return nil }}
				n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, left}, {2, right}}, w, DefaultQueuePolicy())
				if err != nil {
					t.Fatal(err)
				}
				_, err = n.CheckState(NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, []int{1}}, {2, originalRight}}})
				if err != nil {
					t.Fatal(err)
				}
			}
			if observed != 7 || global != 7 {
				t.Fatalf("later checks observed mutated state: child=%d global=%d", observed, global)
			}
		})
	}
}

func TestComposedTransitionChecksSnapshotBeforeChildCallback(t *testing.T) {
	for _, kind := range []string{"pair", "network"} {
		t.Run(kind, func(t *testing.T) {
			var counts lazyCounts
			child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
			before, input, after := []int{1}, []int{2}, []int{3}
			seen := [4]int{}
			var mutateOutput func()
			child.CheckTransition = func([]int, []int, Transition[[]int, int]) ([]Check, error) {
				before[0], input[0], after[0] = 91, 92, 93
				mutateOutput()
				return nil, nil
			}
			if kind == "pair" {
				w := PairWiring[[]int, []int, []int, []int, int, int]{Messages: func(PairAddress[int, int]) ([]PairAddress[[]int, []int], error) { return nil, nil }, CheckState: func(PairState[[]int, []int, []int, []int], *CheckSink) error { return nil }, CheckTransition: func(s PairState[[]int, []int, []int, []int], i PairInput[[]int, []int], tr Transition[PairState[[]int, []int, []int, []int], PairAddress[int, int]], _ *CheckSink) error {
					seen = [4]int{s.Left[0], i.Address.Left[0], tr.State.Left[0], tr.Outputs[0].Left}
					return nil
				}}
				p, err := NewPair(child, child, w, 1)
				if err != nil {
					t.Fatal(err)
				}
				out := []PairAddress[int, int]{PairLeft[int, int](4)}
				mutateOutput = func() { out[0].Left = 94 }
				s := PairState[[]int, []int, []int, []int]{Left: before, Right: []int{0}}
				i := PairInput[[]int, []int]{Kind: "local", Address: PairLeft[[]int, []int](input)}
				tr := Transition[PairState[[]int, []int, []int, []int], PairAddress[int, int]]{State: PairState[[]int, []int, []int, []int]{Left: after, Right: []int{0}}, Outputs: out, Disposition: Accepted()}
				checks := []Check{}
				err = invokeChecks("test", &checks, func(sink *CheckSink) error { return p.CheckTransitionInto(s, i, tr, sink) })
				if err != nil {
					t.Fatal(err)
				}
			} else {
				w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }, CheckTransition: func(s NetworkState[[]int, []int], i NetworkInput[[]int], tr Transition[NetworkState[[]int, []int], NetworkAddress[int]], _ *CheckSink) error {
					seen = [4]int{s.Machines[0].Value[0], i.Local.Value[0], tr.State.Machines[0].Value[0], tr.Outputs[0].Value}
					return nil
				}}
				n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, child}}, w, DefaultQueuePolicy())
				if err != nil {
					t.Fatal(err)
				}
				out := []NetworkAddress[int]{{Machine: 1, Value: 4}}
				mutateOutput = func() { out[0].Value = 94 }
				s := NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{Machine: 1, Value: before}}}
				i := NetworkInput[[]int]{Kind: "local", Local: NetworkAddress[[]int]{Machine: 1, Value: input}}
				tr := Transition[NetworkState[[]int, []int], NetworkAddress[int]]{State: NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{Machine: 1, Value: after}}}, Outputs: out, Disposition: Accepted()}
				_, err = n.CheckTransition(s, i, tr)
				if err != nil {
					t.Fatal(err)
				}
			}
			if seen != [4]int{1, 2, 3, 4} {
				t.Fatalf("global check observed mutation: %v", seen)
			}
		})
	}
}

func TestNetworkTransitionCheckCapturesInputAndStructureBeforeClones(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	input := []int{2}
	var before NetworkState[[]int, []int]
	var after NetworkState[[]int, []int]
	outputs := []NetworkAddress[int]{{Machine: 1, Value: 4}}
	child.CloneInput = func(v []int) []int {
		before.Machines[0].Machine = 9
		after.Machines[0].Machine = 9
		outputs[0].Machine = 9
		return append([]int(nil), v...)
	}
	child.CloneState = func(v []int) []int { input[0] = 99; return append([]int(nil), v...) }
	seen := 0
	child.CheckTransition = func(_ []int, i []int, _ Transition[[]int, int]) ([]Check, error) { seen = i[0]; return nil, nil }
	w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
	n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, child}}, w, DefaultQueuePolicy())
	if err != nil {
		t.Fatal(err)
	}
	before = NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, []int{1}}}}
	after = NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, []int{3}}}}
	i := NetworkInput[[]int]{Kind: "local", Local: NetworkAddress[[]int]{1, input}}
	tr := Transition[NetworkState[[]int, []int], NetworkAddress[int]]{State: after, Outputs: outputs, Disposition: Accepted()}
	_, err = n.CheckTransition(before, i, tr)
	if err != nil || seen != 2 {
		t.Fatalf("seen=%d err=%v", seen, err)
	}
}

func TestNetworkQueueTransitionWithoutGlobalCheckDoesNotClone(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	child.CloneState = func([]int) []int { panic("unexpected state clone") }
	child.CloneInput = func([]int) []int { panic("unexpected input clone") }
	w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
	policy := DefaultQueuePolicy()
	policy.AllowDrop, policy.AllowDuplicate = true, true
	n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, child}}, w, policy)
	if err != nil {
		t.Fatal(err)
	}
	s := NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, []int{1}}}, Pending: []NetworkMessage[[]int]{{1, NetworkRoute[[]int]{1, 0, []int{2}}}}}
	for _, kind := range []string{"drop", "duplicate"} {
		tr := Transition[NetworkState[[]int, []int], NetworkAddress[int]]{State: s, Disposition: Accepted()}
		if _, err := n.CheckTransition(s, NetworkInput[[]int]{Kind: kind, Index: 0}, tr); err != nil {
			t.Fatalf("%s: %v", kind, err)
		}
	}
}

func TestNetworkTransitionOutputsMapInOrder(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	clones := 0
	child.CloneOutput = func(v int) int {
		clones++
		if v == 7 {
			panic("first output clone")
		}
		return v
	}
	w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) { return nil, nil }, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
	n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{1, child}}, w, DefaultQueuePolicy())
	if err != nil {
		t.Fatal(err)
	}
	s := NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, []int{1}}}}
	i := NetworkInput[[]int]{Kind: "local", Local: NetworkAddress[[]int]{1, []int{2}}}
	tr := Transition[NetworkState[[]int, []int], NetworkAddress[int]]{State: s, Outputs: []NetworkAddress[int]{{1, 7}, {9, 8}}, Disposition: Accepted()}
	_, err = n.CheckTransition(s, i, tr)
	if err == nil || !strings.Contains(err.Error(), "first output clone") || clones != 1 {
		t.Fatalf("err=%v clones=%d", err, clones)
	}
	clones = 0
	tr.Outputs = []NetworkAddress[int]{{9, 8}, {1, 7}}
	_, err = n.CheckTransition(s, i, tr)
	if err == nil || !strings.Contains(err.Error(), "wrong network child output address") || clones != 0 {
		t.Fatalf("err=%v clones=%d", err, clones)
	}
	clones = 0
	tr.Outputs = []NetworkAddress[int]{{1, 3}}
	_, err = n.CheckTransition(s, i, tr)
	if err != nil || clones != 1 {
		t.Fatalf("err=%v clones=%d", err, clones)
	}
}

func TestPairTransitionOutputsMapInOrder(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	clones := 0
	child.CloneOutput = func(v int) int {
		clones++
		if v == 7 {
			panic("first output clone")
		}
		return v
	}
	w := PairWiring[[]int, []int, []int, []int, int, int]{
		Messages:   func(PairAddress[int, int]) ([]PairAddress[[]int, []int], error) { return nil, nil },
		CheckState: func(PairState[[]int, []int, []int, []int], *CheckSink) error { return nil },
	}
	p, err := NewPair(child, child, w, 4)
	if err != nil {
		t.Fatal(err)
	}
	s := PairState[[]int, []int, []int, []int]{Left: []int{1}, Right: []int{2}}
	i := PairInput[[]int, []int]{Kind: "local", Address: PairLeft[[]int, []int]([]int{3})}
	tr := Transition[PairState[[]int, []int, []int, []int], PairAddress[int, int]]{State: s, Outputs: []PairAddress[int, int]{PairLeft[int, int](7), PairRight[int, int](8)}, Disposition: Accepted()}
	err = invokeChecks("pair test", &[]Check{}, func(sink *CheckSink) error { return p.CheckTransitionInto(s, i, tr, sink) })
	if err == nil || !strings.Contains(err.Error(), "first output clone") || clones != 1 {
		t.Fatalf("err=%v clones=%d", err, clones)
	}
	clones = 0
	tr.Outputs = []PairAddress[int, int]{PairRight[int, int](8), PairLeft[int, int](7)}
	err = invokeChecks("pair test", &[]Check{}, func(sink *CheckSink) error { return p.CheckTransitionInto(s, i, tr, sink) })
	if err == nil || !strings.Contains(err.Error(), "wrong child output address") || clones != 0 {
		t.Fatalf("err=%v clones=%d", err, clones)
	}
	clones = 0
	tr.Outputs = []PairAddress[int, int]{PairLeft[int, int](3)}
	err = invokeChecks("pair test", &[]Check{}, func(sink *CheckSink) error { return p.CheckTransitionInto(s, i, tr, sink) })
	if err != nil || clones != 1 {
		t.Fatalf("err=%v clones=%d", err, clones)
	}
}

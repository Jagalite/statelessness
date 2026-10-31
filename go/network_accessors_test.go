package statelessness

import (
	"slices"
	"testing"
)

func TestNetworkReferenceAccessors(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	w := NetworkWiring[[]int, []int, int]{Routes: func(NetworkAddress[int]) ([]NetworkRoute[[]int], error) {
		return []NetworkRoute[[]int]{{Destination: 1}}, nil
	}, CheckState: func(NetworkState[[]int, []int], *CheckSink) error { return nil }}
	policy := DefaultQueuePolicy()
	policy.AllowDrop = true
	n, err := NewNetwork([]NetworkAddress[Model[[]int, []int, int]]{{7, child}, {1, child}}, w, policy)
	if err != nil {
		t.Fatal(err)
	}
	if n.Policy() != policy {
		t.Fatal("policy changed")
	}
	ids := n.MachineIDs()
	if !slices.Equal(ids, []MachineID{1, 7}) {
		t.Fatal(ids)
	}
	ids[0] = 99
	if !slices.Equal(n.MachineIDs(), []MachineID{1, 7}) {
		t.Fatal("returned IDs alias network")
	}
	routes, err := n.Wiring().Routes(NetworkAddress[int]{})
	if err != nil || len(routes) != 1 || routes[0].Destination != 1 {
		t.Fatal(routes, err)
	}
	s := NetworkState[[]int, []int]{Machines: []NetworkAddress[[]int]{{1, []int{3}}, {7, []int{9}}}}
	if v, ok := s.Machine(7); !ok || !slices.Equal(v, []int{9}) {
		t.Fatal(v, ok)
	}
	if _, ok := s.Machine(3); ok {
		t.Fatal("missing machine found")
	}
}

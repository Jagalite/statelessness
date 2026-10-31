package statelessness

import "testing"

func TestPairRejectsInvalidPendingTagsBeforeCallbacks(t *testing.T) {
	var counts lazyCounts
	child := lazyChild(&counts, func([]int) (Iterator[[]int], error) { return Values([][]int{}), nil })
	wiringCalls := 0
	w := PairWiring[[]int, []int, []int, []int, int, int]{
		Messages:   func(PairAddress[int, int]) ([]PairAddress[[]int, []int], error) { wiringCalls++; return nil, nil },
		CheckState: func(PairState[[]int, []int, []int, []int], *CheckSink) error { wiringCalls++; return nil },
	}
	p, err := NewPair(child, child, w, 3)
	if err != nil {
		t.Fatal(err)
	}
	bad := PairState[[]int, []int, []int, []int]{Left: []int{1}, Right: []int{2}, Pending: []PairAddress[[]int, []int]{{Side: "invalid"}}}
	good := PairState[[]int, []int, []int, []int]{Left: []int{1}, Right: []int{2}}
	input := PairInput[[]int, []int]{Kind: "local", Address: PairLeft[[]int, []int]([]int{1})}
	if _, err := p.Step(bad, input); err == nil {
		t.Fatal("step accepted invalid pending address")
	}
	if _, err := p.CheckState(bad); err == nil {
		t.Fatal("checks accepted invalid pending address")
	}
	if _, err := p.Inputs(bad); err == nil {
		t.Fatal("enumeration accepted invalid pending address")
	}
	checks := []Check{}
	transition := Transition[PairState[[]int, []int, []int, []int], PairAddress[int, int]]{State: bad, Disposition: Accepted()}
	if err := invokeChecks("test", &checks, func(s *CheckSink) error { return p.CheckTransitionInto(good, input, transition, s) }); err == nil {
		t.Fatal("transition checks accepted invalid post-state")
	}
	if wiringCalls != 0 {
		t.Fatalf("wiring invoked on malformed state: %d", wiringCalls)
	}
}

package statelessness_test

import (
	"fmt"
	"sync/atomic"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

// reduction-v1 fixtures. Inputs 10...19 are "late result" inputs whose class is
// "late". Unless a test overrides step, input k leads to state k%10.
type reductionSpec struct {
	step     func(state, input int) s.Transition[int, int]
	failEdge map[int]bool
	inputs   map[int][]int
}

func reductionModel(spec reductionSpec) s.Model[int, int, int] {
	if spec.step == nil {
		spec.step = func(_, i int) s.Transition[int, int] { return s.Accept(i%10, i%10) }
	}
	if spec.inputs == nil {
		spec.inputs = map[int][]int{0: {10, 11, 12, 1}, 1: {2}, 2: {}}
	}
	return s.Model[int, int, int]{InitialState: func() (int, error) { return 0, nil },
		Step:       func(state, input int) (s.Transition[int, int], error) { return spec.step(state, input), nil },
		CheckState: func(int) ([]s.Check, error) { return []s.Check{s.Passed("ok")}, nil },
		CheckTransition: func(_, i int, _ s.Transition[int, int]) ([]s.Check, error) {
			if spec.failEdge[i] {
				return []s.Check{s.Failed("late", fmt.Sprint(i))}, nil
			}
			return nil, nil
		},
		CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		Inputs:      func(state int) (s.Iterator[int], error) { return s.Values(spec.inputs[state]), nil },
		EqualStates: func(a, b int) bool { return a == b }, HashState: func(state int) uint64 { return uint64(state) }}
}
func byDecade() *s.EquivalenceReduction[int, int, int] {
	return &s.EquivalenceReduction[int, int, int]{
		EligibleClass: func(_, i int) (string, bool, error) { return "late", i >= 10, nil },
		Projection: func(_ int, t s.Transition[int, int], _ []string) ([]byte, error) {
			result := []byte{}
			for _, o := range t.Outputs {
				result = append(result, byte(o))
			}
			return result, nil
		},
	}
}
func exploreReduced(t *testing.T, m s.Model[int, int, int], reduction *s.EquivalenceReduction[int, int, int], policy *s.FailurePolicy) *s.GraphSearchReport[int, int, int] {
	t.Helper()
	p := s.CollectFailures(10, true)
	if policy != nil {
		p = *policy
	}
	r, e := s.ExploreGraph(m, s.DefaultSearchConfig(), s.SearchLimits{}, s.GraphSearchOptions[int, int, int]{FailurePolicy: p, Reduction: reduction})
	if e != nil {
		t.Fatal(e)
	}
	return r
}
func compare(t *testing.T, m s.Model[int, int, int], unreduced, reduced *s.GraphSearchReport[int, int, int], progress *s.ReductionProgressPredicates[int, int, int]) *s.ReductionComparison {
	t.Helper()
	c, e := s.CompareReduction(m, unreduced, reduced, progress)
	if e != nil {
		t.Fatal(e)
	}
	return c
}
func intPointer(v int) *int { return &v }

func sameLate(failEdge map[int]bool) s.Model[int, int, int] {
	return reductionModel(reductionSpec{step: func(_, i int) s.Transition[int, int] {
		if i >= 10 {
			return s.Accept(1, 7)
		}
		return s.Accept(i, 7)
	}, failEdge: failEdge})
}

func TestReductionEquivalentEdgesCollapseIntoRepresentative(t *testing.T) {
	// 10 -> 0, 11 -> 1, 12 -> 2: distinct successors, so no collapse.
	distinct := exploreReduced(t, reductionModel(reductionSpec{}), byDecade(), nil)
	counts := []int{}
	for _, d := range distinct.ReductionDecisions {
		counts = append(counts, len(d.Rejected))
	}
	expectEqual(t, "rejected counts", counts, []int{0, 1, 2})
	expectEqual(t, "projection", distinct.ReductionDecisions[1].Rejected, []s.RejectedEquivalence{{Representative: 0, Reason: "projection_differs"}})
	if distinct.ReducedEdges != 0 {
		t.Fatal(distinct.ReducedEdges)
	}
	// Every late input reaches state 1 with identical observations.
	same := sameLate(nil)
	unreduced, reduced := exploreReduced(t, same, nil, nil), exploreReduced(t, same, byDecade(), nil)
	expectEqual(t, "targets", targets(reduced), []string{"admitted:1", "reduced:0", "reduced:0", "duplicate:1", "admitted:2"})
	if reduced.ReducedEdges != 2 || reduced.Transitions != unreduced.Transitions {
		t.Fatal("reduction never skips execution", reduced.ReducedEdges, reduced.Transitions, unreduced.Transitions)
	}
	expectEqual(t, "decisions", reduced.ReductionDecisions, []s.ReductionDecision{
		{Edge: 0, Source: 0, EquivalenceClass: "late", Rejected: []s.RejectedEquivalence{}},
		{Edge: 1, Source: 0, EquivalenceClass: "late", ReducedTo: intPointer(0), Rejected: []s.RejectedEquivalence{}},
		{Edge: 2, Source: 0, EquivalenceClass: "late", ReducedTo: intPointer(0), Rejected: []s.RejectedEquivalence{}}})
	comparison := compare(t, same, unreduced, reduced, nil)
	if !comparison.Equivalent() || comparison.ProgressMatch != nil {
		t.Fatalf("progress is not compared without predicates: %+v", comparison)
	}
	// Progress results agree on the reduced graph.
	progress := func(r *s.GraphSearchReport[int, int, int]) *s.ProgressReport {
		g, e := s.ProgressGraphFromReport(r, func(s.GraphEdge[int, int]) (bool, error) { return true, nil })
		if e != nil {
			t.Fatal(e)
		}
		return analyze(t, g, func(n int) (s.ProgressKind, error) {
			if r.Nodes[n].State == 2 {
				return s.ProgressTerminal, nil
			}
			return s.ProgressTransaction, nil
		}, nil)
	}
	expectEqual(t, "progress", progress(reduced), progress(unreduced))
	predicates := &s.ReductionProgressPredicates[int, int, int]{Fair: func(s.GraphEdge[int, int]) (bool, error) { return true, nil }, Kind: func(state int) (s.ProgressKind, error) {
		if state == 2 {
			return s.ProgressTerminal, nil
		}
		return s.ProgressTransaction, nil
	}}
	withProgress := compare(t, same, unreduced, reduced, predicates)
	if withProgress.ProgressMatch == nil || !*withProgress.ProgressMatch || !withProgress.Equivalent() {
		t.Fatalf("%+v", withProgress)
	}
}

// Review regression: progress is compared only when predicates are supplied.
func TestReductionProgressDifferenceIsNotEquivalent(t *testing.T) {
	// Same states 0 -> 1, same dispositions and checks; only the input differs,
	// and fairness depends on it.
	oneEdge := func(input int) s.Model[int, int, int] {
		return reductionModel(reductionSpec{step: func(int, int) s.Transition[int, int] { return s.Accept[int, int](1) }, inputs: map[int][]int{0: {input}}})
	}
	a, b := exploreReduced(t, oneEdge(5), nil, nil), exploreReduced(t, oneEdge(6), nil, nil)
	if c := compare(t, oneEdge(5), a, b, nil); !c.Equivalent() || c.ProgressMatch != nil {
		t.Fatalf("structure alone matches and progress was not compared: %+v", c)
	}
	predicates := &s.ReductionProgressPredicates[int, int, int]{Fair: func(e s.GraphEdge[int, int]) (bool, error) { return e.Input == 5, nil }, Kind: func(state int) (s.ProgressKind, error) {
		if state == 1 {
			return s.ProgressTerminal, nil
		}
		return s.ProgressTransaction, nil
	}}
	c := compare(t, oneEdge(5), a, b, predicates)
	if c.ProgressMatch == nil || *c.ProgressMatch || c.Equivalent() {
		t.Fatalf("%+v", c)
	}
	expectEqual(t, "mismatches", c.ProgressMismatches, []int{0})
}

func TestReductionRejectedEquivalencesAreInspectable(t *testing.T) {
	// Same projection, different successor: successor equality is required.
	successor := reductionModel(reductionSpec{step: func(_, i int) s.Transition[int, int] { return s.Accept(i%10, 7) }})
	expectEqual(t, "successor", exploreReduced(t, successor, byDecade(), nil).ReductionDecisions[1].Rejected, []s.RejectedEquivalence{{Representative: 0, Reason: "successor_differs"}})
	// Same successor, a failing check on one edge only.
	same := sameLate(map[int]bool{11: true})
	checks := exploreReduced(t, same, byDecade(), nil)
	expectEqual(t, "checks", checks.ReductionDecisions[1].Rejected, []s.RejectedEquivalence{{Representative: 0, Reason: "checks_differ"}})
	if *checks.ReductionDecisions[2].ReducedTo != 0 || checks.FindingCount != 1 {
		t.Fatalf("%+v", checks)
	}
	// Dispositions must match.
	rejected := reductionModel(reductionSpec{step: func(_, i int) s.Transition[int, int] {
		if i >= 10 {
			tr := s.Accept(1, 7)
			if i == 11 {
				tr.Disposition = s.Rejected("stale")
			}
			return tr
		}
		return s.Accept(i, 7)
	}})
	expectEqual(t, "disposition", exploreReduced(t, rejected, byDecade(), nil).ReductionDecisions[1].Rejected, []s.RejectedEquivalence{{Representative: 0, Reason: "disposition_differs"}})
	if !compare(t, same, exploreReduced(t, same, nil, nil), checks, nil).Equivalent() {
		t.Fatal("reduced report with a kept finding should compare equivalent")
	}
}

func TestReductionIncorrectReductionIsDetected(t *testing.T) {
	// Negative control: a reduced report missing a state, an edge class, or a
	// finding must not compare equivalent to the unreduced search.
	same := sameLate(map[int]bool{12: true})
	unreduced, reduced := exploreReduced(t, same, nil, nil), exploreReduced(t, same, byDecade(), nil)
	if !compare(t, same, unreduced, reduced, nil).Equivalent() {
		t.Fatal("untampered reduction should be equivalent")
	}
	clone := func(r *s.GraphSearchReport[int, int, int]) *s.GraphSearchReport[int, int, int] {
		duplicate := *r
		duplicate.Nodes = append([]s.GraphNode[int, int]{}, r.Nodes...)
		duplicate.Edges = append([]s.GraphEdge[int, int]{}, r.Edges...)
		return &duplicate
	}
	missingState := clone(reduced)
	missingState.Nodes = missingState.Nodes[:len(missingState.Nodes)-1]
	missingState.Edges[4].Target = s.EdgeTarget{Kind: s.TargetRejected, Reason: "tampered"}
	a := compare(t, same, unreduced, missingState, nil)
	if a.Equivalent() {
		t.Fatal("missing state accepted")
	}
	expectEqual(t, "missing", a.MissingInReduced, []int{2})
	expectEqual(t, "unrepresented", a.UnrepresentedEdges, []int{4})
	lostFinding := clone(reduced)
	lostFinding.FindingCount = 0
	if compare(t, same, unreduced, lostFinding, nil).FindingsMatch {
		t.Fatal("dropped finding accepted")
	}
	wrongTarget := clone(reduced)
	wrongTarget.Edges[3].Target = s.EdgeTarget{Kind: s.TargetDuplicate, ID: 2}
	c := compare(t, same, unreduced, wrongTarget, nil)
	// Unreduced 0->1 edges remain represented by edge 0; the 0->2 edge has no unreduced counterpart.
	if c.Equivalent() {
		t.Fatal("rewritten target accepted")
	}
	expectEqual(t, "unrepresented", c.UnrepresentedEdges, []int{})
	expectEqual(t, "extra", c.ExtraReducedEdges, []int{3})
	stopAtFirst := s.StopAtFirst()
	stopped := exploreReduced(t, same, nil, &stopAtFirst)
	if _, e := s.CompareReduction(same, stopped, reduced, nil); e == nil {
		t.Fatal("incomplete search accepted")
	}
}

func TestReductionDiamondValidation(t *testing.T) {
	type pair = [2]int
	counters := s.Model[pair, int, int]{InitialState: func() (pair, error) { return pair{}, nil }, Step: func(state pair, i int) (s.Transition[pair, int], error) {
		state[i]++
		return s.Accept[pair, int](state), nil
	}, CheckState: func(pair) ([]s.Check, error) { return nil, nil }, CloneState: s.ValueCopy[pair], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		Inputs: func(state pair) (s.Iterator[int], error) {
			values := []int{}
			for _, i := range []int{0, 1} {
				if state[i] < 2 {
					values = append(values, i)
				}
			}
			return s.Values(values), nil
		}, EqualStates: func(a, b pair) bool { return a == b }, HashState: func(state pair) uint64 { return uint64(state[0]*3 + state[1]) }}
	report, e := s.ExploreGraph(counters, s.DefaultSearchConfig(), s.SearchLimits{}, s.GraphSearchOptions[pair, int, int]{})
	if e != nil {
		t.Fatal(e)
	}
	always := func(pair, int) (bool, error) { return true, nil }
	equalInputs := func(a, b int) (bool, error) { return a == b, nil }
	none := func(int, s.Transition[pair, int]) ([]byte, error) { return nil, nil }
	ok, e := s.ValidateDiamonds(counters, report, always, equalInputs, none, s.RunLimits{})
	if e != nil {
		t.Fatal(e)
	}
	// Pairs are checked at (0,0) (1,0) (0,1) (1,1); one input is disabled at the other bound.
	if ok.Termination != "search_complete" || ok.Validated != 4 || len(ok.NonCommuting) != 0 {
		t.Fatalf("%+v", ok)
	}
	// Doubling and incrementing do not commute.
	arithmetic := s.Model[int, int, int]{InitialState: func() (int, error) { return 1, nil }, Step: func(state, i int) (s.Transition[int, int], error) {
		if i == 0 {
			return s.Accept(state*2, i), nil
		}
		return s.Accept(state+1, i), nil
	}, CheckState: func(int) ([]s.Check, error) { return nil, nil }, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		Inputs: func(state int) (s.Iterator[int], error) {
			if state < 3 {
				return s.Values([]int{0, 1}), nil
			}
			return s.Values[int](nil), nil
		}, EqualStates: func(a, b int) bool { return a == b }, HashState: func(state int) uint64 { return uint64(state) }}
	alwaysInt := func(int, int) (bool, error) { return true, nil }
	noneInt := func(int, s.Transition[int, int]) ([]byte, error) { return nil, nil }
	shallow, e := s.ExploreGraph(arithmetic, s.SearchConfig{MaxStates: 100, MaxTransitions: 100, MaxDepth: 0}, s.SearchLimits{}, s.GraphSearchOptions[int, int, int]{})
	if e != nil {
		t.Fatal(e)
	}
	bad, e := s.ValidateDiamonds(arithmetic, shallow, alwaysInt, equalInputs, noneInt, s.RunLimits{})
	// Depth-frontier nodes are not completely expanded and are not validated.
	if e != nil || bad.CheckedNodes != 0 || bad.Validated != 0 {
		t.Fatal(e, bad)
	}
	full, e := s.ExploreGraph(arithmetic, s.DefaultSearchConfig(), s.SearchLimits{}, s.GraphSearchOptions[int, int, int]{})
	if e != nil {
		t.Fatal(e)
	}
	deep, e := s.ValidateDiamonds(arithmetic, full, alwaysInt, equalInputs, noneInt, s.RunLimits{})
	// At 1: 1*2+1=3 but (1+1)*2=4. At 2, doubling disables both orders.
	if e != nil || deep.Validated != 1 || len(deep.NonCommuting) != 1 {
		t.Fatal(e, deep)
	}
	expectEqual(t, "witness", deep.NonCommuting[0], s.DiamondWitness[int]{Node: 0, First: 0, Second: 1, Reason: "state_differs"})
	// Same final state, different observations.
	observed, e := s.ValidateDiamonds(counters, report, always, equalInputs, func(i int, t s.Transition[pair, int]) ([]byte, error) { return []byte{byte(i), byte(t.State[0])}, nil }, s.RunLimits{})
	if e != nil {
		t.Fatal(e)
	}
	reasons := []string{}
	for _, w := range observed.NonCommuting {
		reasons = append(reasons, w.Reason)
	}
	expectEqual(t, "reasons", reasons, []string{"observations_differ", "observations_differ", "observations_differ", "observations_differ"})
	var cancelled atomic.Bool
	cancelled.Store(true)
	stopped, e := s.ValidateDiamonds(counters, report, always, equalInputs, none, s.RunLimits{Cancellation: &cancelled})
	if e != nil || stopped.Termination != "cancelled" {
		t.Fatal(e, stopped)
	}
}

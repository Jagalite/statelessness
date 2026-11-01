package statelessness_test

import (
	"fmt"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

// Each control introduces one defect and requires the library evidence to
// expose it. Reduction tampering and replay corruption controls live in
// reduction_test.go and continued_evidence_test.go.
type controlSpec struct {
	edges   map[int][]int
	equal   func(a, b int) bool
	hash    func(int) uint64
	failing map[int]bool
}

func controlModel(spec controlSpec) s.Model[int, int, int] {
	if spec.edges == nil {
		spec.edges = map[int][]int{0: {1, 2}, 1: {3}, 2: {3}}
	}
	if spec.equal == nil {
		spec.equal = func(a, b int) bool { return a == b }
	}
	if spec.hash == nil {
		spec.hash = func(v int) uint64 { return uint64(v) }
	}
	if spec.failing == nil {
		spec.failing = states(3)
	}
	return s.Model[int, int, int]{InitialState: func() (int, error) { return 0, nil },
		Step: func(_, i int) (s.Transition[int, int], error) { return s.Accept[int, int](i), nil },
		CheckState: func(state int) ([]s.Check, error) {
			if spec.failing[state] {
				return []s.Check{s.Failed("bad", fmt.Sprint(state))}, nil
			}
			return []s.Check{s.Passed("bad")}, nil
		},
		CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		Inputs:      func(state int) (s.Iterator[int], error) { return s.Values(spec.edges[state]), nil },
		EqualStates: spec.equal, HashState: spec.hash}
}
func collectGraph(t *testing.T, m s.Model[int, int, int], config s.SearchConfig) *s.GraphSearchReport[int, int, int] {
	t.Helper()
	r, e := s.ExploreGraph(m, config, s.SearchLimits{}, s.GraphSearchOptions[int, int, int]{FailurePolicy: s.CollectFailures(8, true)})
	if e != nil {
		t.Fatal(e)
	}
	return r
}

func TestNegativeControlMissingTransitionIsDetected(t *testing.T) {
	full := collectGraph(t, controlModel(controlSpec{}), s.DefaultSearchConfig())
	missing := collectGraph(t, controlModel(controlSpec{edges: map[int][]int{0: {1, 2}, 1: {3}}}), s.DefaultSearchConfig())
	c := compare(t, controlModel(controlSpec{}), full, missing, nil)
	if c.Equivalent() || c.FindingsMatch {
		t.Fatalf("%+v", c)
	}
	expectEqual(t, "unrepresented", c.UnrepresentedEdges, []int{3})
}

func TestNegativeControlIncorrectIdentityMergingIsDetected(t *testing.T) {
	// States 1 and 2 are merged by a lossy equality with a colliding hash.
	merged := controlModel(controlSpec{equal: func(a, b int) bool { return a == b || a > 0 && a < 3 && b > 0 && b < 3 }, hash: func(v int) uint64 {
		if v == 2 {
			return 1
		}
		return uint64(v)
	}})
	correct, lossy := collectGraph(t, controlModel(controlSpec{}), s.DefaultSearchConfig()), collectGraph(t, merged, s.DefaultSearchConfig())
	if len(lossy.Nodes) != 3 {
		t.Fatal(len(lossy.Nodes))
	}
	c := compare(t, controlModel(controlSpec{}), correct, lossy, nil)
	if c.Equivalent() {
		t.Fatal("lossy identity accepted")
	}
	expectEqual(t, "missing", c.MissingInReduced, []int{2})
}

func TestNegativeControlSuppressedCheckIsDetected(t *testing.T) {
	checked := collectGraph(t, controlModel(controlSpec{}), s.DefaultSearchConfig())
	suppressed := collectGraph(t, controlModel(controlSpec{failing: map[int]bool{}}), s.DefaultSearchConfig())
	if checked.FindingCount != 2 || suppressed.FindingCount != 0 {
		t.Fatal(checked.FindingCount, suppressed.FindingCount)
	}
	c := compare(t, controlModel(controlSpec{}), checked, suppressed, nil)
	if c.FindingsMatch {
		t.Fatal("suppressed check accepted")
	}
	expectEqual(t, "edges carry their check batches", c.UnrepresentedEdges, []int{2, 3})
}

func TestNegativeControlFalseProgressIsDetected(t *testing.T) {
	// 0 <-> 1 is a closed transaction cycle. Omitting the back edge or claiming
	// an unmeasured node is complete changes the evidence.
	truth := analyze(t, progressGraph(t, 2, [][3]int{{0, 1, fair}, {1, 0, fair}}), allKind(s.ProgressTransaction), nil)
	expectEqual(t, "closed", truth.ClosedNonterminalComponents, [][]int{{0, 1}})
	truncated := analyze(t, progressGraph(t, 2, [][3]int{{0, 1, fair}}, 1), allKind(s.ProgressTransaction), nil)
	if len(truncated.ClosedNonterminalComponents) != 0 || truncated.GraphComplete {
		t.Fatal("a cutoff is not reported as progress", truncated)
	}
	// A depth-bounded search cannot be mistaken for a complete graph.
	bounded := collectGraph(t, controlModel(controlSpec{}), s.SearchConfig{MaxStates: 100, MaxTransitions: 100, MaxDepth: 1})
	g, e := s.ProgressGraphFromReport(bounded, func(s.GraphEdge[int, int]) (bool, error) { return true, nil })
	if e != nil {
		t.Fatal(e)
	}
	if analyze(t, g, allKind(s.ProgressTransaction), nil).GraphComplete {
		t.Fatal("bounded graph reported complete")
	}
}

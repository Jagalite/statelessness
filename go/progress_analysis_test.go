package statelessness_test

import (
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

// Hand-authored progress-analysis-v1 graphs, independent of any application.
func progressGraph(t *testing.T, n int, edges [][3]int, unmeasured ...int) *s.ProgressGraph {
	t.Helper()
	list := []s.ProgressEdge{}
	for _, e := range edges {
		list = append(list, s.ProgressEdge{Source: e[0], Target: e[1], Fair: e[2] == 1})
	}
	skip := states(unmeasured...)
	measured := make([]bool, n)
	for k := range measured {
		measured[k] = !skip[k]
	}
	g, e := s.NewProgressGraph(n, list, measured)
	if e != nil {
		t.Fatal(e)
	}
	return g
}
func kinds(values ...s.ProgressKind) func(int) (s.ProgressKind, error) {
	return func(n int) (s.ProgressKind, error) { return values[n], nil }
}
func allKind(kind s.ProgressKind) func(int) (s.ProgressKind, error) {
	return func(int) (s.ProgressKind, error) { return kind, nil }
}
func analyze(t *testing.T, g *s.ProgressGraph, kind func(int) (s.ProgressKind, error), rank func(int) (uint64, error)) *s.ProgressReport {
	t.Helper()
	r, e := s.AnalyzeProgress(g, kind, rank)
	if e != nil {
		t.Fatal(e)
	}
	return r
}

const fair, unfair = 1, 0

func TestProgressSettlingTransaction(t *testing.T) {
	r := analyze(t, progressGraph(t, 2, [][3]int{{0, 1, fair}}), kinds(s.ProgressTransaction, s.ProgressTerminal), nil)
	expectEqual(t, "classes", r.Classes, []s.ProgressClass{s.ClassTransactionProgress, s.ClassTerminal})
	if r.TransactionStates != 1 || r.SettledTransactionStates != 1 || len(r.ClosedNonterminalComponents) != 0 || len(r.UnsettledTransactionStates) != 0 || !r.GraphComplete {
		t.Fatalf("%+v", r)
	}
}

func TestProgressIntentionalAndExternalWaits(t *testing.T) {
	r := analyze(t, progressGraph(t, 3, [][3]int{{0, 1, fair}, {0, 2, fair}, {1, 1, unfair}}), kinds(s.ProgressTransaction, s.ProgressIntentionalWait, s.ProgressExternalWait), nil)
	expectEqual(t, "classes", r.Classes, []s.ProgressClass{s.ClassTransactionProgress, s.ClassStableIntentionalWait, s.ClassDeclaredExternalWait})
	// A self-looping wait is settled, not a closed nonterminal component.
	if r.StableIntentionalWaitStates != 1 || r.ExternalWaitStates != 1 || r.SettledTransactionStates != 1 || len(r.ClosedNonterminalComponents) != 0 {
		t.Fatalf("%+v", r)
	}
}

func TestProgressClosedNonterminalCycle(t *testing.T) {
	r := analyze(t, progressGraph(t, 3, [][3]int{{0, 1, fair}, {1, 0, fair}, {2, 0, fair}}), allKind(s.ProgressTransaction), func(int) (uint64, error) { return 4, nil })
	expectEqual(t, "closed", r.ClosedNonterminalComponents, [][]int{{0, 1}})
	expectEqual(t, "unsettled", r.UnsettledTransactionStates, []int{0, 1, 2})
	if r.MaximumRank != 4 {
		t.Fatal(r.MaximumRank)
	}
}

func TestProgressUnfairOnlyExit(t *testing.T) {
	only := analyze(t, progressGraph(t, 2, [][3]int{{0, 1, unfair}}), kinds(s.ProgressTransaction, s.ProgressTerminal), nil)
	expectEqual(t, "classes", only.Classes, []s.ProgressClass{s.ClassStuck, s.ClassTerminal})
	expectEqual(t, "stuck", only.StuckStates, []int{0})
	if len(only.ClosedNonterminalComponents) != 0 || len(only.UnsettledTransactionStates) != 0 {
		t.Fatalf("%+v", only)
	}
	loop := analyze(t, progressGraph(t, 2, [][3]int{{0, 0, fair}, {0, 1, unfair}}), kinds(s.ProgressTransaction, s.ProgressTerminal), nil)
	expectEqual(t, "classes", loop.Classes, []s.ProgressClass{s.ClassTransactionProgress, s.ClassTerminal})
	expectEqual(t, "unsettled", loop.UnsettledTransactionStates, []int{0})
	if len(loop.ClosedNonterminalComponents) != 0 {
		t.Fatal("an unfair exit still leaves the component")
	}
}

func TestProgressFairRankDecreaseOpensComponent(t *testing.T) {
	ranks := func(n int) (uint64, error) { return []uint64{2, 1}[n], nil }
	decreasing := analyze(t, progressGraph(t, 2, [][3]int{{0, 1, fair}, {1, 0, fair}}), allKind(s.ProgressTransaction), ranks)
	if len(decreasing.ClosedNonterminalComponents) != 0 {
		t.Fatal(decreasing.ClosedNonterminalComponents)
	}
	unfairDecrease := analyze(t, progressGraph(t, 2, [][3]int{{0, 1, unfair}, {1, 0, fair}}), allKind(s.ProgressTransaction), ranks)
	expectEqual(t, "an unfair decrease does not count", unfairDecrease.ClosedNonterminalComponents, [][]int{{0, 1}})
}

func TestProgressDepthCutoffIsUnmeasured(t *testing.T) {
	r := analyze(t, progressGraph(t, 2, [][3]int{{0, 1, fair}}, 1), allKind(s.ProgressTransaction), nil)
	expectEqual(t, "classes", r.Classes, []s.ProgressClass{s.ClassTransactionProgress, s.ClassUnmeasuredCutoff})
	expectEqual(t, "unsettled", r.UnsettledTransactionStates, []int{0})
	if r.UnmeasuredStates != 1 || len(r.ClosedNonterminalComponents) != 0 || r.GraphComplete {
		t.Fatalf("%+v", r)
	}
}

func TestProgressPartialExpansionBlocksClosedClaim(t *testing.T) {
	// Node 1 was interrupted: its known edge closes a cycle but other edges are unknown.
	r := analyze(t, progressGraph(t, 2, [][3]int{{0, 1, fair}, {1, 0, fair}}, 1), allKind(s.ProgressTransaction), nil)
	if len(r.ClosedNonterminalComponents) != 0 || r.UnmeasuredComponents != 1 {
		t.Fatalf("%+v", r)
	}
}

func TestProgressIterativeSCCHandlesDeepGraphs(t *testing.T) {
	n := 200_000
	chain := make([][2]int, 0, n)
	for k := 0; k < n-1; k++ {
		chain = append(chain, [2]int{k, k + 1})
	}
	chain = append(chain, [2]int{n - 1, 0})
	components := s.StronglyConnectedComponents(n, chain)
	if len(components) != 1 || len(components[0]) != n {
		t.Fatal(len(components))
	}
	expectEqual(t, "small", s.StronglyConnectedComponents(4, [][2]int{{0, 1}, {1, 0}, {2, 3}}), [][]int{{0, 1}, {2}, {3}})
	reached := s.ReverseReachable(4, []s.ProgressEdge{{Source: 0, Target: 1, Fair: true}, {Source: 2, Target: 1, Fair: false}}, []int{1}, func(e s.ProgressEdge) bool { return e.Fair })
	expectEqual(t, "reachable", reached, []bool{true, true, false, false})
}

func TestProgressGraphFromSearchReport(t *testing.T) {
	m := graphModel(graphSpec{edges: map[int][]int{0: {1, 2}, 1: {3}, 2: {2}}})
	report, e := s.ExploreGraph(m, s.DefaultSearchConfig(), s.SearchLimits{}, s.GraphSearchOptions[int, int, int]{})
	if e != nil {
		t.Fatal(e)
	}
	g, e := s.ProgressGraphFromReport(report, func(edge s.GraphEdge[int, int]) (bool, error) { return edge.Input != 2, nil })
	if e != nil {
		t.Fatal(e)
	}
	expectEqual(t, "edges", g.Edges, []s.ProgressEdge{{Source: 0, Target: 1, Fair: true}, {Source: 0, Target: 2, Fair: false}, {Source: 1, Target: 3, Fair: true}, {Source: 2, Target: 2, Fair: false}})
	r := analyze(t, g, func(n int) (s.ProgressKind, error) {
		if report.Nodes[n].State == 3 {
			return s.ProgressTerminal, nil
		}
		return s.ProgressTransaction, nil
	}, nil)
	expectEqual(t, "classes", r.Classes, []s.ProgressClass{s.ClassTransactionProgress, s.ClassTransactionProgress, s.ClassStuck, s.ClassTerminal})
	expectEqual(t, "closed", r.ClosedNonterminalComponents, [][]int{{2}})
	bounded, e := s.ExploreGraph(m, s.SearchConfig{MaxStates: 100, MaxTransitions: 100, MaxDepth: 1}, s.SearchLimits{}, s.GraphSearchOptions[int, int, int]{})
	if e != nil {
		t.Fatal(e)
	}
	bg, e := s.ProgressGraphFromReport(bounded, func(s.GraphEdge[int, int]) (bool, error) { return true, nil })
	if e != nil {
		t.Fatal(e)
	}
	expectEqual(t, "measured", bg.Measured, []bool{true, false, false})
	if _, e := s.NewProgressGraph(1, []s.ProgressEdge{{Source: 0, Target: 1, Fair: true}}, []bool{true}); e == nil {
		t.Fatal("out-of-range endpoint accepted")
	}
	streamed, e := s.ExploreGraph(m, s.DefaultSearchConfig(), s.SearchLimits{}, s.GraphSearchOptions[int, int, int]{Retention: s.GraphRetention{StreamEdgesOnly: true}})
	if e != nil {
		t.Fatal(e)
	}
	if _, e := s.ProgressGraphFromReport(streamed, func(s.GraphEdge[int, int]) (bool, error) { return true, nil }); e == nil {
		t.Fatal("streamed report accepted")
	}
}

package statelessness

import "slices"

// Extension progress-analysis-v1 (S3); see spec/progress-analysis-v1.md.
// Applications supply node kinds, ranks and edge fairness; this file owns only
// graph algorithms. Fair-path reachability and rank checks are bounded
// evidence, not a proof that every scheduler execution eventually completes.

type ProgressEdge struct {
	Source, Target int
	Fair           bool
}
type ProgressGraph struct {
	NodeCount int
	Edges     []ProgressEdge
	// False for a node whose outgoing edges are not all known: a depth
	// frontier, an interrupted or unexpanded node, or a rejected successor.
	Measured []bool
}

func NewProgressGraph(nodeCount int, edges []ProgressEdge, measured []bool) (*ProgressGraph, error) {
	if nodeCount < 0 || len(measured) != nodeCount {
		return nil, &ConfigError{"progress graph needs one measured flag per node"}
	}
	for _, e := range edges {
		if e.Source < 0 || e.Source >= nodeCount || e.Target < 0 || e.Target >= nodeCount {
			return nil, &ConfigError{"progress edge endpoint out of range"}
		}
	}
	return &ProgressGraph{nodeCount, append([]ProgressEdge{}, edges...), append([]bool{}, measured...)}, nil
}

// resolvedTarget follows reduced edges to their representative's target.
func resolvedTarget[I, O any](edges []GraphEdge[I, O], edge GraphEdge[I, O]) (int, bool) {
	target := edge.Target
	for hops := 0; target.Kind == TargetReduced && target.ID >= 0 && target.ID < len(edges) && hops <= len(edges); hops++ {
		target = edges[target.ID].Target
	}
	if target.Kind == TargetAdmitted || target.Kind == TargetDuplicate {
		return target.ID, true
	}
	return 0, false
}

// ProgressGraphFromReport keeps admitted and duplicate targets and resolves
// reduced edges to their representative's target. A node is measured only when
// its expansion is complete and none of its edges was rejected. Requires
// retained edges.
func ProgressGraphFromReport[S, I, O any](report *GraphSearchReport[S, I, O], fair func(GraphEdge[I, O]) (bool, error)) (*ProgressGraph, error) {
	if !report.EdgesRetained {
		return nil, &ModelError{"progress analysis requires retained edges"}
	}
	measured := make([]bool, len(report.Nodes))
	for k, n := range report.Nodes {
		measured[k] = n.Expansion.Kind == ExpansionComplete
	}
	edges := []ProgressEdge{}
	for _, edge := range report.Edges {
		target, ok := resolvedTarget(report.Edges, edge)
		if !ok {
			if edge.Source >= 0 && edge.Source < len(measured) {
				measured[edge.Source] = false
			}
			continue
		}
		isFair, e := call("progress fairness", func() (bool, error) { return fair(edge) })
		if e != nil {
			return nil, e
		}
		edges = append(edges, ProgressEdge{edge.Source, target, isFair})
	}
	return NewProgressGraph(len(report.Nodes), edges, measured)
}

type ProgressKind string

const (
	ProgressTerminal        ProgressKind = "terminal"
	ProgressIntentionalWait ProgressKind = "intentionalWait"
	ProgressExternalWait    ProgressKind = "externalWait"
	ProgressTransaction     ProgressKind = "transaction"
	ProgressOther           ProgressKind = "other"
)

type ProgressClass string

const (
	ClassTerminal              ProgressClass = "terminal"
	ClassStableIntentionalWait ProgressClass = "stableIntentionalWait"
	ClassDeclaredExternalWait  ProgressClass = "declaredExternalWait"
	ClassTransactionProgress   ProgressClass = "transactionProgress"
	ClassStuck                 ProgressClass = "stuck"
	ClassUnmeasuredCutoff      ProgressClass = "unmeasuredCutoff"
)

type ProgressReport struct {
	Classes                     []ProgressClass
	TerminalStates              int
	StableIntentionalWaitStates int
	ExternalWaitStates          int
	UnmeasuredStates            int
	TransactionStates           int
	SettledTransactionStates    int
	// Transaction nodes with no fair path to a settled node.
	UnsettledTransactionStates []int
	StuckStates                []int
	// Members sorted ascending; components sorted by first member.
	ClosedNonterminalComponents [][]int
	// Closed candidates excluded because a member is unmeasured.
	UnmeasuredComponents int
	MaximumRank          uint64
	// Every node is measured; otherwise absence of a finding is not evidence.
	GraphComplete bool
}

// StronglyConnectedComponents is Tarjan's algorithm with an explicit work
// stack. Members are sorted ascending and components ordered by smallest member.
func StronglyConnectedComponents(nodeCount int, edges [][2]int) [][]int {
	adjacency := make([][]int, nodeCount)
	for _, e := range edges {
		adjacency[e[0]] = append(adjacency[e[0]], e[1])
	}
	index, low, onStack := make([]int, nodeCount), make([]int, nodeCount), make([]bool, nodeCount)
	for k := range index {
		index[k] = -1
	}
	stack, components, counter := []int{}, [][]int{}, 0
	type frame struct{ node, next int }
	work := []frame{}
	for root := 0; root < nodeCount; root++ {
		if index[root] >= 0 {
			continue
		}
		work = append(work, frame{root, 0})
		for len(work) > 0 {
			top := &work[len(work)-1]
			v := top.node
			if top.next == 0 && index[v] < 0 {
				index[v], low[v] = counter, counter
				counter++
				stack = append(stack, v)
				onStack[v] = true
			}
			if top.next < len(adjacency[v]) {
				w := adjacency[v][top.next]
				top.next++
				if index[w] < 0 {
					work = append(work, frame{w, 0})
				} else if onStack[w] {
					low[v] = min(low[v], index[w])
				}
				continue
			}
			work = work[:len(work)-1]
			if len(work) > 0 {
				parent := work[len(work)-1].node
				low[parent] = min(low[parent], low[v])
			}
			if low[v] == index[v] {
				component := []int{}
				for len(stack) > 0 {
					w := stack[len(stack)-1]
					stack = stack[:len(stack)-1]
					onStack[w] = false
					component = append(component, w)
					if w == v {
						break
					}
				}
				slices.Sort(component)
				components = append(components, component)
			}
		}
	}
	slices.SortFunc(components, func(a, b []int) int { return a[0] - b[0] })
	return components
}

// ReverseReachable marks nodes with a path to any target using edges accepted
// by include (nil accepts every edge).
func ReverseReachable(nodeCount int, edges []ProgressEdge, targets []int, include func(ProgressEdge) bool) []bool {
	reverse := make([][]int, nodeCount)
	for _, e := range edges {
		if include == nil || include(e) {
			reverse[e.Target] = append(reverse[e.Target], e.Source)
		}
	}
	reached, queue := make([]bool, nodeCount), []int{}
	for _, t := range targets {
		if !reached[t] {
			reached[t] = true
			queue = append(queue, t)
		}
	}
	for head := 0; head < len(queue); head++ {
		for _, u := range reverse[queue[head]] {
			if !reached[u] {
				reached[u] = true
				queue = append(queue, u)
			}
		}
	}
	return reached
}

// AnalyzeProgress classifies in order: unmeasured, terminal, external wait,
// intentional wait, transaction with a fair outgoing edge, otherwise stuck. A
// closed nonterminal component contains a transaction or stuck node, has no
// exit edge, no fair rank-decreasing edge, and no unmeasured member. A nil rank
// ranks every node zero.
func AnalyzeProgress(graph *ProgressGraph, kind func(int) (ProgressKind, error), rank func(int) (uint64, error)) (*ProgressReport, error) {
	n := graph.NodeCount
	outgoing := make([][]ProgressEdge, n)
	for _, e := range graph.Edges {
		outgoing[e.Source] = append(outgoing[e.Source], e)
	}
	classes, ranks := make([]ProgressClass, n), make([]uint64, n)
	for v := 0; v < n; v++ {
		if rank != nil {
			value, e := call("progress rank", func() (uint64, error) { return rank(v) })
			if e != nil {
				return nil, e
			}
			ranks[v] = value
		}
		if !graph.Measured[v] {
			classes[v] = ClassUnmeasuredCutoff
			continue
		}
		k, e := call("progress kind", func() (ProgressKind, error) { return kind(v) })
		if e != nil {
			return nil, e
		}
		switch k {
		case ProgressTerminal:
			classes[v] = ClassTerminal
		case ProgressExternalWait:
			classes[v] = ClassDeclaredExternalWait
		case ProgressIntentionalWait:
			classes[v] = ClassStableIntentionalWait
		case ProgressTransaction:
			classes[v] = ClassStuck
			for _, edge := range outgoing[v] {
				if edge.Fair {
					classes[v] = ClassTransactionProgress
					break
				}
			}
		case ProgressOther:
			classes[v] = ClassStuck
		default:
			return nil, &ModelError{"progress kind: unknown kind " + string(k)}
		}
	}
	settled := []int{}
	for v, c := range classes {
		if c == ClassTerminal || c == ClassStableIntentionalWait || c == ClassDeclaredExternalWait {
			settled = append(settled, v)
		}
	}
	canSettle := ReverseReachable(n, graph.Edges, settled, func(e ProgressEdge) bool { return e.Fair })
	pairs := make([][2]int, len(graph.Edges))
	for k, e := range graph.Edges {
		pairs[k] = [2]int{e.Source, e.Target}
	}
	report := &ProgressReport{Classes: classes, UnsettledTransactionStates: []int{}, StuckStates: []int{}, ClosedNonterminalComponents: [][]int{}, GraphComplete: true}
	for _, component := range StronglyConnectedComponents(n, pairs) {
		members := map[int]bool{}
		candidate, unmeasured := false, false
		for _, v := range component {
			members[v] = true
			candidate = candidate || classes[v] == ClassTransactionProgress || classes[v] == ClassStuck
			unmeasured = unmeasured || !graph.Measured[v]
		}
		if !candidate {
			continue
		}
		open := false
		for _, v := range component {
			for _, e := range outgoing[v] {
				if !members[e.Target] || e.Fair && ranks[e.Target] < ranks[e.Source] {
					open = true
				}
			}
		}
		if open {
			continue
		}
		if unmeasured {
			report.UnmeasuredComponents++
			continue
		}
		report.ClosedNonterminalComponents = append(report.ClosedNonterminalComponents, component)
	}
	for v, c := range classes {
		switch c {
		case ClassTerminal:
			report.TerminalStates++
		case ClassStableIntentionalWait:
			report.StableIntentionalWaitStates++
		case ClassDeclaredExternalWait:
			report.ExternalWaitStates++
		case ClassUnmeasuredCutoff:
			report.UnmeasuredStates++
		case ClassStuck:
			report.StuckStates = append(report.StuckStates, v)
		case ClassTransactionProgress:
			report.TransactionStates++
			if canSettle[v] {
				report.SettledTransactionStates++
			} else {
				report.UnsettledTransactionStates = append(report.UnsettledTransactionStates, v)
			}
		}
		report.MaximumRank = max(report.MaximumRank, ranks[v])
		report.GraphComplete = report.GraphComplete && graph.Measured[v]
	}
	return report, nil
}

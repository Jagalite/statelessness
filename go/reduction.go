package statelessness

import (
	"bytes"
	"slices"
	"strconv"
)

// Extension reduction-v1 (S4); see spec/reduction-v1.md. Reduction collapses an
// evaluated edge into an earlier representative edge of the same eligible class
// from the same node. It never skips executing or checking an edge. Collapse
// requires equal disposition, every check, the application projection, and
// full successor equality; a digest is not used. Research equivalence evidence
// does not authorize reduction.

type EquivalenceReduction[S, I, O any] struct {
	// EligibleClass returns the equivalence class, or ok=false when the edge is
	// not a candidate. It receives detached copies.
	EligibleClass func(S, I) (class string, ok bool, err error)
	// Projection returns deterministic bytes for ordered effects and progress
	// annotations that equivalence must preserve. It receives detached copies
	// and the edge labels.
	Projection func(I, Transition[S, O], []string) ([]byte, error)
}
type RejectedEquivalence struct {
	Representative int
	// disposition_differs, checks_differ, projection_differs or successor_differs.
	Reason string
}
type ReductionDecision struct {
	Edge             int
	Source           int
	EquivalenceClass string
	// Nil: the edge became a representative of its class.
	ReducedTo *int
	// Representatives compared before the decision, in comparison order.
	Rejected []RejectedEquivalence
}

type DiamondWitness[I any] struct {
	Node          int
	First, Second I
	// state_differs or observations_differ.
	Reason string
}
type DiamondReport[I any] struct {
	// search_complete, or the RunLimits stop reason.
	Termination    string
	CheckedNodes   int
	CandidatePairs uint64
	// Pairs where each input stayed enabled after the other.
	Validated    uint64
	NonCommuting []DiamondWitness[I]
}

// ValidateDiamonds executes, for each completely expanded node, both orders of
// every pair of candidate inputs that remain enabled after each other, and
// compares the final states by model equality and the two steps' observations:
// the set of failed check (phase, ID) pairs and the multiset of projections.
// This is evidence about independence, never permission to prune.
func ValidateDiamonds[S, I, O any](m Model[S, I, O], report *GraphSearchReport[S, I, O], candidate func(S, I) (bool, error), equalInputs func(I, I) (bool, error),
	projection func(I, Transition[S, O]) ([]byte, error), limits RunLimits) (*DiamondReport[I], error) {
	if e := m.validate(); e != nil {
		return nil, e
	}
	if m.Step == nil || m.Inputs == nil || m.EqualStates == nil || candidate == nil || equalInputs == nil || projection == nil {
		return nil, &ModelError{"diamond validation requires step, inputs, equality, candidate, input equality and projection callbacks"}
	}
	control, e := newRunControl(limits)
	if e != nil {
		return nil, e
	}
	out := &DiamondReport[I]{Termination: "search_complete", NonCommuting: []DiamondWitness[I]{}}
	enumerate := func(state S) ([]I, error) {
		iterator, e := call("enumerate inputs", func() (Iterator[I], error) { return m.Inputs(m.CloneState(state)) })
		if e != nil {
			return nil, e
		}
		values := []I{}
		for {
			value, ok, e := next(iterator)
			if e != nil {
				return nil, e
			}
			if !ok {
				return values, nil
			}
			owned, e := copied(m.CloneInput, value)
			if e != nil {
				return nil, e
			}
			values = append(values, owned)
		}
	}
	enabled := func(input I, state S) (bool, error) {
		domain, e := enumerate(state)
		if e != nil {
			return false, e
		}
		for _, other := range domain {
			same, e := call("input_equal", func() (bool, error) { return equalInputs(m.CloneInput(other), m.CloneInput(input)) })
			if e != nil || same {
				return same, e
			}
		}
		return false, nil
	}
	type observation struct {
		failed     []string
		projection []byte
	}
	observe := func(before S, input I, t Transition[S, O]) (observation, error) {
		states, e := stateChecks(m, t.State)
		if e != nil {
			return observation{}, e
		}
		edges, e := edgeChecks(m, before, input, t)
		if e != nil {
			return observation{}, e
		}
		failed := []string{}
		for _, c := range states {
			if c.Status == "failed" {
				failed = append(failed, "state\x00"+c.ID)
			}
		}
		for _, c := range edges {
			if c.Status == "failed" {
				failed = append(failed, "transition\x00"+c.ID)
			}
		}
		snapshot, e := cloneTransition(m, t)
		if e != nil {
			return observation{}, e
		}
		bytes, e := call("diamond projection", func() ([]byte, error) { return projection(m.CloneInput(input), snapshot) })
		return observation{failed, append([]byte{}, bytes...)}, e
	}
	failureSet := func(observations ...observation) []string {
		set := []string{}
		for _, o := range observations {
			set = append(set, o.failed...)
		}
		slices.Sort(set)
		return slices.Compact(set)
	}
	projections := func(observations ...observation) [][]byte {
		values := [][]byte{}
		for _, o := range observations {
			values = append(values, o.projection)
		}
		slices.SortFunc(values, bytes.Compare)
		return values
	}
	for _, node := range report.Nodes {
		if node.Expansion.Kind != ExpansionComplete {
			continue
		}
		if reason := control.stop(); reason != "" {
			out.Termination = reason
			return out, nil
		}
		out.CheckedNodes++
		domain, e := enumerate(node.State)
		if e != nil {
			return nil, e
		}
		candidates := []I{}
		for _, input := range domain {
			ok, e := call("diamond candidate", func() (bool, error) { return candidate(m.CloneState(node.State), m.CloneInput(input)) })
			if e != nil {
				return nil, e
			}
			if ok {
				candidates = append(candidates, input)
			}
		}
		for i := range candidates {
			for j := i + 1; j < len(candidates); j++ {
				if reason := control.stop(); reason != "" {
					out.Termination = reason
					return out, nil
				}
				out.CandidatePairs++
				a, b := candidates[i], candidates[j]
				ta, e := perform(m, node.State, a)
				if e != nil {
					return nil, e
				}
				tb, e := perform(m, node.State, b)
				if e != nil {
					return nil, e
				}
				bAfterA, e := enabled(b, ta.State)
				if e != nil {
					return nil, e
				}
				if !bAfterA {
					continue
				}
				aAfterB, e := enabled(a, tb.State)
				if e != nil {
					return nil, e
				}
				if !aAfterB {
					continue
				}
				tab, e := perform(m, ta.State, b)
				if e != nil {
					return nil, e
				}
				tba, e := perform(m, tb.State, a)
				if e != nil {
					return nil, e
				}
				out.Validated++
				same, e := call("state equality", func() (bool, error) { return m.EqualStates(m.CloneState(tab.State), m.CloneState(tba.State)), nil })
				if e != nil {
					return nil, e
				}
				var seen [4]observation
				for k, step := range []struct {
					before S
					input  I
					t      Transition[S, O]
				}{{node.State, a, ta}, {ta.State, b, tab}, {node.State, b, tb}, {tb.State, a, tba}} {
					if seen[k], e = observe(step.before, step.input, step.t); e != nil {
						return nil, e
					}
				}
				sameFailures := slices.Equal(failureSet(seen[0], seen[1]), failureSet(seen[2], seen[3]))
				sameProjections := slices.EqualFunc(projections(seen[0], seen[1]), projections(seen[2], seen[3]), bytes.Equal)
				if !same || !sameFailures || !sameProjections {
					first, e := copied(m.CloneInput, a)
					if e != nil {
						return nil, e
					}
					second, e := copied(m.CloneInput, b)
					if e != nil {
						return nil, e
					}
					reason := "observations_differ"
					if !same {
						reason = "state_differs"
					}
					out.NonCommuting = append(out.NonCommuting, DiamondWitness[I]{node.ID, first, second, reason})
				}
			}
		}
	}
	return out, nil
}

// ReductionProgressPredicates are application progress predicates
// (progress-analysis-v1) applied to both graphs. Kind and rank take states so
// they agree across the two reports; a nil Rank ranks every state zero.
type ReductionProgressPredicates[S, I, O any] struct {
	Fair func(GraphEdge[I, O]) (bool, error)
	Kind func(S) (ProgressKind, error)
	Rank func(S) (uint64, error)
}
type ReductionComparison struct {
	// Unreduced node IDs with no equal reduced node, and the reverse.
	MissingInReduced []int
	ExtraInReduced   []int
	// Unreduced edges whose (source, target, disposition, checks) class has no
	// reduced representative, and the reverse.
	UnrepresentedEdges []int
	ExtraReducedEdges  []int
	FindingsMatch      bool
	CoverageMatch      bool
	// Nil when no progress predicates were supplied: progress was not compared.
	ProgressMatch *bool
	// Unreduced nodes whose progress class, settlement or closed-component
	// membership differs from their reduced counterpart.
	ProgressMismatches []int
}

// Equivalent treats not-compared progress (ProgressMatch == nil) as absent
// evidence, not as a mismatch.
func (c *ReductionComparison) Equivalent() bool {
	return len(c.MissingInReduced) == 0 && len(c.ExtraInReduced) == 0 && len(c.UnrepresentedEdges) == 0 && len(c.ExtraReducedEdges) == 0 &&
		c.FindingsMatch && c.CoverageMatch && (c.ProgressMatch == nil || *c.ProgressMatch)
}

// CompareReduction compares reachable states by model equality, represented
// edge classes (raw edge counts may differ), failed property counts, coverage
// and, when progress predicates are supplied, progress results. Both reports
// must retain edges and must not be incomplete. The check is quadratic and
// intended for exhaustive small bounds.
func CompareReduction[S, I, O any](m Model[S, I, O], unreduced, reduced *GraphSearchReport[S, I, O], progress *ReductionProgressPredicates[S, I, O]) (*ReductionComparison, error) {
	if m.EqualStates == nil || m.CloneState == nil {
		return nil, &ModelError{"reduction comparison requires equality and clone callbacks"}
	}
	for _, report := range []*GraphSearchReport[S, I, O]{unreduced, reduced} {
		if !report.EdgesRetained {
			return nil, &ModelError{"reduction comparison requires retained edges"}
		}
		if report.Coverage.Kind == CoverageIncomplete {
			return nil, &ModelError{"reduction comparison requires finished searches"}
		}
	}
	hash := func(state S) (uint64, error) {
		return call("state hash", func() (uint64, error) {
			if m.HashState == nil {
				return 0, nil
			}
			return m.HashState(m.CloneState(state)), nil
		})
	}
	buckets := map[uint64][]int{}
	for k, node := range reduced.Nodes {
		h, e := hash(node.State)
		if e != nil {
			return nil, e
		}
		buckets[h] = append(buckets[h], k)
	}
	out := &ReductionComparison{MissingInReduced: []int{}, ExtraInReduced: []int{}, UnrepresentedEdges: []int{}, ExtraReducedEdges: []int{}, ProgressMismatches: []int{},
		CoverageMatch: unreduced.Coverage == reduced.Coverage}
	mapping, reverse := make([]int, len(unreduced.Nodes)), make([]int, len(reduced.Nodes))
	for k := range mapping {
		mapping[k] = -1
	}
	for k := range reverse {
		reverse[k] = -1
	}
	matched := make([]bool, len(reduced.Nodes))
	for k, node := range unreduced.Nodes {
		h, e := hash(node.State)
		if e != nil {
			return nil, e
		}
		for _, other := range buckets[h] {
			same, e := call("state equality", func() (bool, error) {
				return m.EqualStates(m.CloneState(node.State), m.CloneState(reduced.Nodes[other].State)), nil
			})
			if e != nil {
				return nil, e
			}
			if same {
				mapping[k] = other
				matched[other] = true
				break
			}
		}
		if mapping[k] < 0 {
			out.MissingInReduced = append(out.MissingInReduced, k)
		}
	}
	for k, ok := range matched {
		if !ok {
			out.ExtraInReduced = append(out.ExtraInReduced, k)
		}
	}
	for u, r := range mapping {
		if r >= 0 {
			reverse[r] = u
		}
	}
	at := func(table []int, index int) int {
		if index < 0 || index >= len(table) {
			return -1
		}
		return table[index]
	}
	// mapped resolves an edge's target through table. known is false when the
	// edge resolves to a node without counterpart: such an edge is never
	// represented. node is -1 for an edge without a resolved target.
	mapped := func(report *GraphSearchReport[S, I, O], edge GraphEdge[I, O], table []int) (known bool, node int) {
		target, ok := resolvedTarget(report.Edges, edge)
		if !ok {
			return true, -1
		}
		other := at(table, target)
		return other >= 0, other
	}
	resolved := func(report *GraphSearchReport[S, I, O], edge GraphEdge[I, O]) int {
		if target, ok := resolvedTarget(report.Edges, edge); ok {
			return target
		}
		return -1
	}
	same := func(a, b GraphEdge[I, O]) bool {
		return a.Disposition == b.Disposition && slices.Equal(a.StateChecks, b.StateChecks) && slices.Equal(a.TransitionChecks, b.TransitionChecks)
	}
	reducedEdges := []GraphEdge[I, O]{}
	for _, edge := range reduced.Edges {
		if edge.Target.Kind != TargetReduced {
			reducedEdges = append(reducedEdges, edge)
		}
	}
	for _, edge := range unreduced.Edges {
		known, target := mapped(unreduced, edge, mapping)
		source := at(mapping, edge.Source)
		represented := false
		if known && source >= 0 {
			for _, other := range reducedEdges {
				if other.Source == source && resolved(reduced, other) == target && same(other, edge) {
					represented = true
					break
				}
			}
		}
		if !represented {
			out.UnrepresentedEdges = append(out.UnrepresentedEdges, edge.ID)
		}
	}
	for _, edge := range reducedEdges {
		known, target := mapped(reduced, edge, reverse)
		source := at(reverse, edge.Source)
		represented := false
		if known && source >= 0 {
			for _, other := range unreduced.Edges {
				if other.Source == source && resolved(unreduced, other) == target && same(other, edge) {
					represented = true
					break
				}
			}
		}
		if !represented {
			out.ExtraReducedEdges = append(out.ExtraReducedEdges, edge.ID)
		}
	}
	out.FindingsMatch = unreduced.FindingCount == reduced.FindingCount && slices.Equal(unreduced.FailedProperties, reduced.FailedProperties)
	if progress == nil {
		return out, nil
	}
	if progress.Fair == nil || progress.Kind == nil {
		return nil, &ConfigError{"progress predicates require fairness and kind callbacks"}
	}
	analyze := func(report *GraphSearchReport[S, I, O]) (*ProgressReport, error) {
		graph, e := ProgressGraphFromReport(report, progress.Fair)
		if e != nil {
			return nil, e
		}
		var rank func(int) (uint64, error)
		if progress.Rank != nil {
			rank = func(n int) (uint64, error) { return progress.Rank(m.CloneState(report.Nodes[n].State)) }
		}
		return AnalyzeProgress(graph, func(n int) (ProgressKind, error) { return progress.Kind(m.CloneState(report.Nodes[n].State)) }, rank)
	}
	u, e := analyze(unreduced)
	if e != nil {
		return nil, e
	}
	r, e := analyze(reduced)
	if e != nil {
		return nil, e
	}
	members := func(report *ProgressReport, n int) bool {
		for _, component := range report.ClosedNonterminalComponents {
			if slices.Contains(component, n) {
				return true
			}
		}
		return false
	}
	for n := range unreduced.Nodes {
		k := at(mapping, n)
		if k < 0 {
			continue
		}
		if u.Classes[n] != r.Classes[k] || slices.Contains(u.UnsettledTransactionStates, n) != slices.Contains(r.UnsettledTransactionStates, k) || members(u, n) != members(r, k) {
			out.ProgressMismatches = append(out.ProgressMismatches, n)
		}
	}
	// Closed components must correspond member for member under the mapping.
	key := func(component []int) string {
		sorted := slices.Clone(component)
		slices.Sort(sorted)
		text := ""
		for _, v := range slices.Compact(sorted) {
			text += "," + strconv.Itoa(v)
		}
		return text
	}
	componentSet := func(components [][]int, translate func(int) int) map[string]bool {
		set := map[string]bool{}
		for _, component := range components {
			mappedMembers := []int{}
			for _, v := range component {
				if w := translate(v); w >= 0 {
					mappedMembers = append(mappedMembers, w)
				}
			}
			set[key(mappedMembers)] = true
		}
		return set
	}
	uClosed := componentSet(u.ClosedNonterminalComponents, func(v int) int { return at(mapping, v) })
	rClosed := componentSet(r.ClosedNonterminalComponents, func(v int) int { return v })
	sameClosed := len(uClosed) == len(rClosed)
	for k := range uClosed {
		sameClosed = sameClosed && rClosed[k]
	}
	match := len(out.ProgressMismatches) == 0 && sameClosed && u.GraphComplete == r.GraphComplete
	out.ProgressMatch = &match
	return out, nil
}

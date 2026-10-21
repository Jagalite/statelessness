package statelessness

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
)

// PropertyPhase distinguishes properties with the same ID at different phases.
type PropertyPhase string

const (
	PropertyState      PropertyPhase = "state"
	PropertyTransition PropertyPhase = "transition"
)

type RequiredProperty struct {
	ID    string
	Phase PropertyPhase
}
type ResearchAssumptions struct {
	Established                                                                                        bool
	FiniteDomain, Equivalence, Effects, TransitionProperties, QueueOrder, OracleHistory, VisibleTiming string
}
type ResearchProposal struct {
	Name               string
	Version            uint32
	Build              string
	Assumptions        ResearchAssumptions
	RequiredProperties []RequiredProperty
}
type ResearchConfig struct {
	Enabled                                  bool
	MaxStates, MaxEdges, MaxEquivalencePairs uint64
}

func DefaultResearchConfig() ResearchConfig { return ResearchConfig{false, 256, 4096, 65536} }

type ExperimentStatus string

const (
	ExperimentDisabled                    ExperimentStatus = "Disabled"
	ExperimentAssumptionsNotEstablished   ExperimentStatus = "AssumptionsNotEstablished"
	ExperimentIncomplete                  ExperimentStatus = "Incomplete"
	ExperimentCounterexample              ExperimentStatus = "Counterexample"
	ExperimentAgreesWithinDeclaredFixture ExperimentStatus = "AgreesWithinDeclaredFixture"
)

type ViolationWitness[I any] struct {
	Property RequiredProperty
	Details  string
	Inputs   []I
}
type ResearchReachability[I any] struct {
	States             []int
	Edges              uint64
	Violations         []ViolationWitness[I]
	ObservedProperties []RequiredProperty
	SkippedChecks      uint64
}
type EquivalenceCounterexample[I any] struct {
	LeftState, RightState   int
	LeftPrefix, RightPrefix []I
	Reason                  string
}
type ExperimentReport[I any] struct {
	Model                            Metadata
	Proposal                         ResearchProposal
	Config                           ResearchConfig
	Status                           ExperimentStatus
	DeclaredStates                   uint64
	MaterializedEdges, ComparedPairs uint64
	Exact, Reduced                   *ResearchReachability[I]
	Counterexamples                  []EquivalenceCounterexample[I]
	MissingViolations                []ViolationWitness[I]
	MissingProperties                []RequiredProperty
	Limitation                       string
}

// ResearchEquality supplies exact input/output comparison without serialization.
// These callbacks, model equality, and projection must be deterministic and pure.
type ResearchEquality[I, O any] struct {
	Input  func(I, I) bool
	Output func(O, O) bool
}
type researchEdge[I, O any] struct {
	input       I
	target      int
	outputs     []O
	disposition Disposition
	checks      []Check
}
type researchGraph[I, O any] struct {
	initial int
	checks  [][]Check
	edges   [][]researchEdge[I, O]
}

// CompareEquivalence materializes every declared edge once, including unreachable
// states, then traverses only that table. It never changes production equality.
// Projection keys use Go's exact comparable equality, not encoded bytes or hashes.
func CompareEquivalence[S, I, O any, K comparable](m Model[S, I, O], states []S, proposal ResearchProposal, config ResearchConfig, projection func(S) (K, error), equality ResearchEquality[I, O], metadataSource ...func() (Metadata, error)) (report *ExperimentReport[I], err error) {
	for _, statement := range []string{proposal.Assumptions.FiniteDomain, proposal.Assumptions.Equivalence, proposal.Assumptions.Effects, proposal.Assumptions.TransitionProperties, proposal.Assumptions.QueueOrder, proposal.Assumptions.OracleHistory, proposal.Assumptions.VisibleTiming} {
		if strings.TrimSpace(statement) == "" {
			return nil, &ConfigError{"every reduction assumption needs an explicit statement"}
		}
	}
	if strings.TrimSpace(proposal.Name) == "" || strings.TrimSpace(proposal.Build) == "" || proposal.Version == 0 {
		return nil, &ConfigError{"reduction proposal needs a name, positive version, and build identity"}
	}
	required := make(map[RequiredProperty]bool)
	for _, p := range proposal.RequiredProperties {
		if strings.TrimSpace(p.ID) == "" || required[p] {
			return nil, &ConfigError{"empty or duplicate expected research property"}
		}
		if p.Phase != PropertyState && p.Phase != PropertyTransition {
			return nil, &ConfigError{"invalid research property phase"}
		}
		required[p] = true
	}
	proposal.RequiredProperties = append([]RequiredProperty{}, proposal.RequiredProperties...)
	if len(metadataSource) > 1 {
		return nil, &ConfigError{"research accepts one metadata supplier"}
	}
	var metadataCallback func() (Metadata, error)
	if len(metadataSource) == 1 {
		metadataCallback = metadataSource[0]
	} else if m.Codec != nil {
		metadataCallback = m.Codec.Metadata
	}
	if metadataCallback == nil {
		return nil, &ConfigError{"research requires a metadata supplier"}
	}
	metadata, e := researchCall("research metadata", metadataCallback)
	if e != nil {
		return nil, e
	}
	report = &ExperimentReport[I]{Model: metadata, Proposal: proposal, Config: config, Status: ExperimentDisabled, DeclaredStates: uint64(len(states)), Counterexamples: []EquivalenceCounterexample[I]{}, MissingViolations: []ViolationWitness[I]{}, MissingProperties: []RequiredProperty{}, Limitation: "No production reduction is enabled. Evidence applies only to the explicit finite fixture and its assumptions."}
	if !config.Enabled {
		return report, nil
	}
	if !proposal.Assumptions.Established {
		report.Status = ExperimentAssumptionsNotEstablished
		return report, nil
	}
	report.Status = ExperimentIncomplete
	// Native callback panics are incomplete experiments, never passing evidence.
	defer func() {
		if recover() != nil {
			report.Status = ExperimentIncomplete
			report.Limitation = "research callback panic; comparison incomplete"
			err = nil
		}
	}()
	if len(states) == 0 || uint64(len(states)) > config.MaxStates {
		report.Limitation = "The explicit state domain is empty or exceeds the state budget."
		return report, nil
	}
	if e := m.validate(); e != nil {
		return nil, e
	}
	if m.InitialState == nil || m.Step == nil || m.Inputs == nil || m.EqualStates == nil || equality.Input == nil || equality.Output == nil || projection == nil {
		return nil, &ConfigError{"research requires initial, step, enumeration, equality, and projection callbacks"}
	}
	owned := make([]S, len(states))
	for j, s := range states {
		owned[j], e = researchCopied(m.CloneState, s)
		if e != nil {
			report.Limitation = e.Error()
			return report, nil
		}
	}
	states = owned
	equalState := func(a, b S) bool { return m.EqualStates(m.CloneState(a), m.CloneState(b)) }
	for j, s := range states {
		for _, prior := range states[:j] {
			if equalState(prior, s) {
				return nil, &ConfigError{"declared research domain contains duplicate exact states"}
			}
		}
	}
	graph, e := researchMaterialize(m, states, config.MaxEdges, &report.MaterializedEdges, equalState)
	if e != nil {
		report.Limitation = e.Error()
		return report, nil
	}
	keys := make([]K, len(states))
	for j, s := range states {
		snapshot, e := researchCopied(m.CloneState, s)
		if e != nil {
			report.Limitation = e.Error()
			return report, nil
		}
		keys[j], e = researchCall("research projection", func() (K, error) { return projection(snapshot) })
		if e != nil {
			report.Limitation = e.Error()
			return report, nil
		}
		if keys[j] != keys[j] {
			report.Limitation = "research projection key is not reflexive; equality must satisfy equivalence laws"
			return report, nil
		}
	}
	clonePath := func(path []I) []I {
		result := make([]I, len(path))
		for j, v := range path {
			result[j] = m.CloneInput(v)
		}
		return result
	}
	exact, paths := researchReach(graph, func(a, b int) bool { return a == b }, clonePath)
	reduced, _ := researchReach(graph, func(a, b int) bool { return keys[a] == keys[b] }, clonePath)
	reducedFailures := map[RequiredProperty]bool{}
	for _, v := range reduced.Violations {
		reducedFailures[v.Property] = true
	}
	for _, v := range exact.Violations {
		if !reducedFailures[v.Property] {
			v.Inputs = clonePath(v.Inputs)
			report.MissingViolations = append(report.MissingViolations, v)
		}
	}
	observed := map[RequiredProperty]bool{}
	for _, p := range exact.ObservedProperties {
		observed[p] = true
	}
	for _, p := range proposal.RequiredProperties {
		if !observed[p] {
			report.MissingProperties = append(report.MissingProperties, p)
		}
	}
	pairLimited := false
outer:
	for position, left := range exact.States {
		for _, right := range exact.States[position+1:] {
			if keys[left] != keys[right] {
				continue
			}
			if report.ComparedPairs == config.MaxEquivalencePairs {
				pairLimited = true
				break outer
			}
			report.ComparedPairs++
			reason := researchCompare(graph, keys, left, right, func(a, b I) bool { return equality.Input(m.CloneInput(a), m.CloneInput(b)) }, func(a, b O) bool { return equality.Output(m.CloneOutput(a), m.CloneOutput(b)) })
			if reason != "" {
				report.Counterexamples = append(report.Counterexamples, EquivalenceCounterexample[I]{left, right, clonePath(paths[left]), clonePath(paths[right]), reason})
			}
		}
	}
	projectedAgrees := true
	for _, s := range exact.States {
		found := false
		for _, other := range reduced.States {
			if keys[s] == keys[other] {
				found = true
				break
			}
		}
		if !found {
			projectedAgrees = false
		}
	}
	exactFailures := map[RequiredProperty]bool{}
	for _, v := range exact.Violations {
		exactFailures[v.Property] = true
	}
	reducedOnly := false
	for _, v := range reduced.Violations {
		if !exactFailures[v.Property] {
			reducedOnly = true
		}
	}
	switch {
	case len(report.Counterexamples) > 0 || len(report.MissingViolations) > 0 || !projectedAgrees || reducedOnly:
		report.Status = ExperimentCounterexample
		report.Limitation = "The proposed equivalence changes observable behavior or property reachability in this finite fixture; it must not be used for pruning."
	case pairLimited:
		report.Limitation = "Equivalence-pair budget reached; unchecked pairs cannot be assumed equivalent."
	case len(report.MissingProperties) > 0 || exact.SkippedChecks > 0 || reduced.SkippedChecks > 0 || len(proposal.RequiredProperties) == 0:
		report.Limitation = "Expected properties were absent or checks were skipped; equivalence qualification is incomplete."
	default:
		report.Status = ExperimentAgreesWithinDeclaredFixture
	}
	report.Exact = &exact
	report.Reduced = &reduced
	return report, nil
}

func researchMaterialize[S, I, O any](m Model[S, I, O], states []S, max uint64, executed *uint64, equal func(S, S) bool) (researchGraph[I, O], error) {
	graph := researchGraph[I, O]{checks: make([][]Check, len(states)), edges: make([][]researchEdge[I, O], len(states))}
	initial, e := researchCall("initial state", m.InitialState)
	if e != nil {
		return graph, e
	}
	find := func(state S) int {
		for j, s := range states {
			if equal(s, state) {
				return j
			}
		}
		return -1
	}
	graph.initial = find(initial)
	if graph.initial < 0 {
		return graph, &ModelError{"initial state is outside the declared finite domain"}
	}
	for j, state := range states {
		graph.checks[j], e = researchStateChecks(m, state)
		if e != nil {
			return graph, e
		}
		snapshot, e := researchCopied(m.CloneState, state)
		if e != nil {
			return graph, e
		}
		iterator, e := researchCall("inputs", func() (Iterator[I], error) { return m.Inputs(snapshot) })
		if e != nil {
			return graph, e
		}
		if iterator == nil {
			return graph, &ModelError{"nil research input iterator"}
		}
		for {
			type pull struct {
				input I
				ok    bool
			}
			next, e := researchCall("input iterator", func() (pull, error) { v, ok, e := iterator(); return pull{v, ok}, e })
			if e != nil {
				return graph, e
			}
			if !next.ok {
				break
			}
			if *executed == max {
				return graph, &ModelError{"edge materialization budget reached; finite graph not completed"}
			}
			input, e := researchCopied(m.CloneInput, next.input)
			if e != nil {
				return graph, e
			}
			before, e := researchCopied(m.CloneState, state)
			if e != nil {
				return graph, e
			}
			reducerInput, e := researchCopied(m.CloneInput, input)
			if e != nil {
				return graph, e
			}
			actual, e := researchCall("transition", func() (Transition[S, O], error) { return m.Step(before, reducerInput) })
			if e != nil {
				return graph, e
			}
			// The reducer succeeded even if native snapshot validation fails.
			*executed++
			transition, e := researchCloneTransition(m, actual)
			if e != nil {
				return graph, e
			}
			target := find(transition.State)
			if target < 0 {
				return graph, &ModelError{"successor is outside the declared finite domain"}
			}
			checks, e := researchEdgeChecks(m, state, input, transition)
			if e != nil {
				return graph, e
			}
			graph.edges[j] = append(graph.edges[j], researchEdge[I, O]{input, target, transition.Outputs, transition.Disposition, checks})
		}
	}
	return graph, nil
}
func researchCompare[I, O any, K comparable](g researchGraph[I, O], keys []K, left, right int, inputEq func(I, I) bool, outputEq func(O, O) bool) string {
	checksEqual := func(a, b []Check) bool {
		if len(a) != len(b) {
			return false
		}
		for j := range a {
			if a[j] != b[j] {
				return false
			}
		}
		return true
	}
	if !checksEqual(g.checks[left], g.checks[right]) {
		return "state checks differ"
	}
	if len(g.edges[left]) != len(g.edges[right]) {
		return "enabled input counts differ"
	}
	for j, a := range g.edges[left] {
		b := g.edges[right][j]
		if !inputEq(a.input, b.input) {
			return "enabled input labels or order differ"
		}
		if len(a.outputs) != len(b.outputs) {
			return "ordered effects differ"
		}
		for k := range a.outputs {
			if !outputEq(a.outputs[k], b.outputs[k]) {
				return "ordered effects differ"
			}
		}
		if a.disposition != b.disposition {
			return "transition disposition differs"
		}
		if !checksEqual(a.checks, b.checks) {
			return "transition checks differ"
		}
		if keys[a.target] != keys[b.target] {
			return "successor equivalence classes differ"
		}
	}
	return ""
}
func researchPropertyLess(a, b RequiredProperty) bool {
	if a.ID != b.ID {
		return a.ID < b.ID
	}
	return a.Phase == PropertyState && b.Phase == PropertyTransition
}
func researchReach[I, O any](g researchGraph[I, O], equal func(int, int) bool, clone func([]I) []I) (ResearchReachability[I], [][]I) {
	r := ResearchReachability[I]{States: []int{g.initial}, Violations: []ViolationWitness[I]{}, ObservedProperties: []RequiredProperty{}}
	paths := make([][]I, len(g.edges))
	admitted := make([]bool, len(g.edges))
	admitted[g.initial] = true
	paths[g.initial] = []I{}
	queue := []int{g.initial}
	seen := map[RequiredProperty]bool{}
	observed := map[RequiredProperty]bool{}
	observe := func(checks []Check, phase PropertyPhase, path []I) {
		for _, check := range checks {
			p := RequiredProperty{check.ID, phase}
			observed[p] = true
			switch check.Status {
			case "failed":
				if !seen[p] {
					seen[p] = true
					r.Violations = append(r.Violations, ViolationWitness[I]{p, check.Details, clone(path)})
				}
			case "skipped":
				if r.SkippedChecks < math.MaxUint64 {
					r.SkippedChecks++
				}
			}
		}
	}
	observe(g.checks[g.initial], PropertyState, nil)
	for len(queue) > 0 {
		source := queue[0]
		queue = queue[1:]
		for _, edge := range g.edges[source] {
			r.Edges++
			path := append(clone(paths[source]), clone([]I{edge.input})...)
			observe(g.checks[edge.target], PropertyState, path)
			observe(edge.checks, PropertyTransition, path)
			found := false
			for _, old := range r.States {
				if equal(old, edge.target) {
					found = true
					break
				}
			}
			if found || admitted[edge.target] {
				continue
			}
			admitted[edge.target] = true
			paths[edge.target] = path
			r.States = append(r.States, edge.target)
			queue = append(queue, edge.target)
		}
	}
	for p := range observed {
		r.ObservedProperties = append(r.ObservedProperties, p)
	}
	sort.Slice(r.ObservedProperties, func(i, j int) bool { return researchPropertyLess(r.ObservedProperties[i], r.ObservedProperties[j]) })
	return r, paths
}

// QualificationParameters records assumptions and finite evidence, never a switch.
func QualificationParameters[I any](r ExperimentReport[I]) map[string]string {
	a := r.Proposal.Assumptions
	return map[string]string{"research.proposal": r.Proposal.Name, "research.proposal_version": strconv.FormatUint(uint64(r.Proposal.Version), 10), "research.proposal_build": r.Proposal.Build, "research.model_build": r.Model.Build, "research.status": string(r.Status), "research.declared_states": strconv.FormatUint(r.DeclaredStates, 10), "research.materialized_edges": strconv.FormatUint(r.MaterializedEdges, 10), "research.compared_pairs": strconv.FormatUint(r.ComparedPairs, 10), "research.finite_domain": a.FiniteDomain, "research.equivalence": a.Equivalence, "research.effects": a.Effects, "research.transition_properties": a.TransitionProperties, "research.queue_order": a.QueueOrder, "research.oracle_history": a.OracleHistory, "research.visible_timing": a.VisibleTiming, "research.limitation": r.Limitation, "research.production_pruning_enabled": fmt.Sprint(false)}
}

// Research preserves raw ModelError text, unlike contextual production engine
// helpers. Panic payloads are never formatted or exposed as passing evidence.
func researchCall[T any](stage string, callback func() (T, error)) (value T, err error) {
	defer func() {
		if recover() != nil {
			var zero T
			value = zero
			err = &ModelError{stage + ": callback panic"}
		}
	}()
	return callback()
}
func researchCopied[T any](clone func(T) T, value T) (T, error) {
	return researchCall("snapshot", func() (T, error) { return clone(value), nil })
}
func researchCloneTransition[S, I, O any](m Model[S, I, O], t Transition[S, O]) (Transition[S, O], error) {
	return researchCall("snapshot", func() (Transition[S, O], error) {
		if e := t.Disposition.Validate(); e != nil {
			return Transition[S, O]{}, e
		}
		result := Transition[S, O]{State: m.CloneState(t.State), Outputs: make([]O, len(t.Outputs)), Disposition: t.Disposition}
		for j, v := range t.Outputs {
			result.Outputs[j] = m.CloneOutput(v)
		}
		return result, nil
	})
}
func researchCheckCallback(stage string, callback func(*CheckSink) error) ([]Check, error) {
	checks := []Check{}
	batch := &checkBatch{checks: &checks}
	lease := &checkLease{batch: batch, active: true}
	sink := &CheckSink{lease: lease}
	defer func() { lease.active = false; lease.batch = nil }()
	_, err := researchCall(stage, func() (struct{}, error) { return struct{}{}, callback(sink) })
	if batch.failure != nil {
		return nil, batch.failure
	}
	if err != nil {
		return nil, err
	}
	return checks, nil
}
func researchStateChecks[S, I, O any](m Model[S, I, O], state S) ([]Check, error) {
	return researchCheckCallback("state check", func(sink *CheckSink) error {
		snapshot := m.CloneState(state)
		if m.CheckStateInto != nil {
			return m.CheckStateInto(snapshot, sink)
		}
		return legacyChecks(sink, func() ([]Check, error) { return m.CheckState(snapshot) })
	})
}
func researchEdgeChecks[S, I, O any](m Model[S, I, O], before S, input I, t Transition[S, O]) ([]Check, error) {
	if m.CheckTransitionInto == nil && m.CheckTransition == nil {
		return []Check{}, nil
	}
	return researchCheckCallback("transition check", func(sink *CheckSink) error {
		snapshot, e := researchCloneTransition(m, t)
		if e != nil {
			return e
		}
		b, i := m.CloneState(before), m.CloneInput(input)
		if m.CheckTransitionInto != nil {
			return m.CheckTransitionInto(b, i, snapshot, sink)
		}
		return legacyChecks(sink, func() ([]Check, error) { return m.CheckTransition(b, i, snapshot) })
	})
}

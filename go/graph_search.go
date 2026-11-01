package statelessness

import (
	"bytes"
	"fmt"
	"math"
	"slices"
	"unsafe"
)

// Extension graph-search-v1 (S1 admitted graph, S2 failure collection); see
// spec/graph-search-v1.md. With default options the model callback sequence is
// exactly bfs-controls-v1 (EnumerateObserved). Enumerate and EnumerateObserved
// are unchanged and remain the portable bfs-v1 entry points.

// FailurePolicy selects stop-at-first (the zero value, bfs-v1) or collection.
// Under collection, MaxRetainedFindings bounds retained witnesses only; counts
// and failed property identities are never dropped. Without
// ExpandFailingSuccessors, a new successor reached by a failing edge is
// rejected (failing_successor) and coverage is failure-pruned.
type FailurePolicy struct {
	Collect                 bool
	MaxRetainedFindings     int
	ExpandFailingSuccessors bool
}

func StopAtFirst() FailurePolicy { return FailurePolicy{} }
func CollectFailures(maxRetainedFindings int, expandFailingSuccessors bool) FailurePolicy {
	return FailurePolicy{true, maxRetainedFindings, expandFailingSuccessors}
}

// GraphRetention retains edges by default. StreamEdgesOnly streams edges
// through events only. Exceeding either cap ends the search with
// graph_retention_limit. MaxGraphBytes bounds engine bookkeeping for nodes and
// retained edges; it excludes model state representation.
type GraphRetention struct {
	StreamEdgesOnly  bool
	MaxRetainedEdges *int
	MaxGraphBytes    *uint64
}

const (
	ExpansionUnexpanded    = "unexpanded"
	ExpansionComplete      = "complete"
	ExpansionDepthFrontier = "depthFrontier"
	ExpansionFailurePruned = "failurePruned"
	ExpansionInterrupted   = "interrupted"
)

// NodeExpansion is one of the Expansion* kinds; Reason is set only for
// interrupted expansions (the termination that ended the search).
type NodeExpansion struct {
	Kind   string
	Reason string
}

func (e NodeExpansion) String() string {
	if e.Kind == ExpansionInterrupted {
		return e.Kind + "(" + e.Reason + ")"
	}
	return e.Kind
}

const (
	// TargetPending is only observed in edgeEvaluated events.
	TargetPending   = "pending"
	TargetAdmitted  = "admitted"
	TargetDuplicate = "duplicate"
	// TargetReduced (reduction-v1) collapses into representative edge ID.
	TargetReduced  = "reduced"
	TargetRejected = "rejected"
)

// EdgeTarget: ID is the node for admitted/duplicate and the representative edge
// for reduced; Reason is set for rejected.
type EdgeTarget struct {
	Kind   string
	ID     int
	Reason string
}

// String uses the graph-search-v1 fixture spelling.
func (t EdgeTarget) String() string {
	switch t.Kind {
	case TargetAdmitted, TargetDuplicate, TargetReduced:
		return fmt.Sprintf("%s:%d", t.Kind, t.ID)
	case TargetRejected:
		return t.Kind + ":" + t.Reason
	}
	return t.Kind
}

type GraphNode[S, I any] struct {
	ID    int
	State S
	// Parent, Input and AdmittingEdge are absent (nil/zero) for the root.
	Parent        *int
	Input         I
	AdmittingEdge *int
	Depth         int
	// Initial checks for the root; otherwise the admitting edge's state checks.
	Checks []Check
	// True when any check batch on the root path, including this node's, failed.
	PathFailed bool
	Expansion  NodeExpansion
}
type GraphEdge[I, O any] struct {
	ID               int
	Source           int
	Input            I
	Disposition      Disposition
	Outputs          []O
	StateChecks      []Check
	TransitionChecks []Check
	Annotations      []string
	// Reports are values; callers may rewrite a copy, e.g. for negative controls.
	Target EdgeTarget
}

func (e GraphEdge[I, O]) Failed() bool {
	return hasFailure(e.StateChecks) || hasFailure(e.TransitionChecks)
}

type GraphFinding[I any] struct {
	Index uint64
	// Root-to-edge inputs; empty for an initial-state finding.
	Witness []I
	// Every failed check of the batch, state checks before transition checks.
	Violations []Violation
	// Nil for an initial-state finding.
	Edge *int
	// The witness prefix crosses an earlier failure; reproducing it requires
	// continued-failure-evidence-v1 rather than stop-at-failure replay.
	RequiresContinuedEvidence bool
}
type PropertyFailureCount struct {
	Phase, ID    string
	Count        uint64
	FirstFinding uint64
}

const (
	CoverageExhaustive = "exhaustive"
	CoverageBounded    = "bounded"
	CoverageIncomplete = "incomplete"
)

// GraphCoverage: bounded carries DepthCutoff/FailurePruned; incomplete carries
// the termination Reason.
type GraphCoverage struct {
	Kind                       string
	DepthCutoff, FailurePruned bool
	Reason                     string
}

// String uses the graph-search-v1 fixture spelling.
func (c GraphCoverage) String() string {
	switch c.Kind {
	case CoverageBounded:
		text := "bounded:"
		if c.DepthCutoff {
			text += "depth_cutoff"
		}
		if c.DepthCutoff && c.FailurePruned {
			text += "+"
		}
		if c.FailurePruned {
			text += "failure_pruned"
		}
		return text
	case CoverageIncomplete:
		return c.Kind + ":" + c.Reason
	}
	return c.Kind
}

const (
	EventRootAdmitted      = "rootAdmitted"
	EventExpansionStarted  = "expansionStarted"
	EventEdgeEvaluated     = "edgeEvaluated"
	EventFinding           = "finding"
	EventSuccessorAdmitted = "successorAdmitted"
	EventDuplicateEdge     = "duplicateEdge"
	EventEdgeReduced       = "edgeReduced"
	EventSuccessorRejected = "successorRejected"
	EventExpansionFinished = "expansionFinished"
	EventLayerCompleted    = "layerCompleted"
	EventSearchFinished    = "searchFinished"
)

// GraphEvent is a tagged value. Node is set for root/expansion/admission
// events, Existing for duplicates, Edge for edge events, EdgeRecord (a detached
// copy) for edgeEvaluated, FindingIndex/FindingEdge/Violations for findings,
// Representative for reductions, Reason for rejections, Expansion for
// expansionFinished, Depth for layerCompleted and Termination for
// searchFinished.
type GraphEvent[I, O any] struct {
	Kind           string
	Node           int
	Existing       int
	Edge           int
	EdgeRecord     *GraphEdge[I, O]
	FindingIndex   uint64
	FindingEdge    *int
	Violations     []Violation
	Representative int
	Reason         string
	Expansion      NodeExpansion
	Depth          int
	Termination    string
}

// String uses the graph-search-v1 fixture spelling; inputs are formatted with %v.
func (e GraphEvent[I, O]) String() string {
	switch e.Kind {
	case EventRootAdmitted:
		return fmt.Sprintf("root:%d", e.Node)
	case EventExpansionStarted:
		return fmt.Sprintf("expand:%d", e.Node)
	case EventEdgeEvaluated:
		return fmt.Sprintf("edge:%d:%d:%v", e.EdgeRecord.ID, e.EdgeRecord.Source, e.EdgeRecord.Input)
	case EventFinding:
		edge := "-"
		if e.FindingEdge != nil {
			edge = fmt.Sprint(*e.FindingEdge)
		}
		text := fmt.Sprintf("finding:%d:%s:", e.FindingIndex, edge)
		for k, v := range e.Violations {
			if k > 0 {
				text += ","
			}
			text += v.Phase + "/" + v.Check.ID
		}
		return text
	case EventSuccessorAdmitted:
		return fmt.Sprintf("admit:%d:%d", e.Edge, e.Node)
	case EventDuplicateEdge:
		return fmt.Sprintf("dup:%d:%d", e.Edge, e.Existing)
	case EventEdgeReduced:
		return fmt.Sprintf("reduced:%d:%d", e.Edge, e.Representative)
	case EventSuccessorRejected:
		return fmt.Sprintf("reject:%d:%s", e.Edge, e.Reason)
	case EventExpansionFinished:
		return fmt.Sprintf("finish:%d:%s", e.Node, e.Expansion)
	case EventLayerCompleted:
		return fmt.Sprintf("layer:%d", e.Depth)
	case EventSearchFinished:
		return "end:" + e.Termination
	}
	return e.Kind
}

type GraphSearchOptions[S, I, O any] struct {
	FailurePolicy FailurePolicy
	Retention     GraphRetention
	Observer      *SearchObserver[S, I, O]
	// Events is a synchronous sink. Payloads are detached copies; errors abort.
	Events func(GraphEvent[I, O]) error
	// AnnotateEdge returns user edge labels; called with detached copies after all checks.
	AnnotateEdge func(S, I, Transition[S, O]) ([]string, error)
	Reduction    *EquivalenceReduction[S, I, O]
}
type GraphSearchReport[S, I, O any] struct {
	Termination string
	Coverage    GraphCoverage
	// bfs-v1 meaning: admitted states, or 1 once the initial state exists.
	States int
	// Evaluated transitions, including duplicate, reduced and rejected edges.
	Transitions                 uint64
	MaxDepthReached             int
	SkippedChecks               uint64
	EstimatedRetainedStateBytes *uint64
	Nodes                       []GraphNode[S, I]
	// Indexed by edge ID while EdgesRetained.
	Edges         []GraphEdge[I, O]
	EdgesRetained bool
	// Edges between admitted nodes (admitted plus duplicate targets).
	GraphEdges         uint64
	DuplicateEdges     uint64
	ReducedEdges       uint64
	RejectedSuccessors uint64
	// Admitted nodes whose expansion is not complete.
	FrontierSize    int
	CompletedLayers int
	DepthCutoff     bool
	FailurePruned   bool
	// Engine bookkeeping for nodes and retained edges (saturating).
	EstimatedGraphBytes uint64
	Findings            []GraphFinding[I]
	FindingCount        uint64
	FailedProperties    []PropertyFailureCount
	ReductionDecisions  []ReductionDecision
}

func (r *GraphSearchReport[S, I, O]) FindingsTruncated() bool {
	return r.FindingCount > uint64(len(r.Findings))
}

// Failure is the first retained finding in bfs-v1 form, or nil.
func (r *GraphSearchReport[S, I, O]) Failure() *Failure[I] {
	if len(r.Findings) == 0 {
		return nil
	}
	return &Failure[I]{r.Findings[0].Witness, r.Findings[0].Violations}
}

// GraphSearchError is a model, checker, observer or sink error after the search
// started. Report holds the evidence gathered before the error (incomplete).
type GraphSearchError[S, I, O any] struct {
	Err    error
	Report *GraphSearchReport[S, I, O]
}

func (e *GraphSearchError[S, I, O]) Error() string { return e.Err.Error() }
func (e *GraphSearchError[S, I, O]) Unwrap() error { return e.Err }

// ExploreGraph is breadth-first exploration that retains the admitted graph and
// its evidence. Configuration errors are returned before the search starts;
// later errors are *GraphSearchError with a partial report.
func ExploreGraph[S, I, O any](m Model[S, I, O], config SearchConfig, limits SearchLimits, options GraphSearchOptions[S, I, O]) (*GraphSearchReport[S, I, O], error) {
	if limits.MaxEstimatedStateBytes != nil {
		maximum := *limits.MaxEstimatedStateBytes
		limits.MaxEstimatedStateBytes = &maximum
	}
	if options.Retention.MaxRetainedEdges != nil {
		maximum := *options.Retention.MaxRetainedEdges
		options.Retention.MaxRetainedEdges = &maximum
	}
	if options.Retention.MaxGraphBytes != nil {
		maximum := *options.Retention.MaxGraphBytes
		options.Retention.MaxGraphBytes = &maximum
	}
	if config.MaxStates <= 0 {
		return nil, &ConfigError{"enumeration max_states must be greater than zero"}
	}
	if config.MaxDepth < 0 {
		return nil, &ConfigError{"invalid enumeration configuration"}
	}
	if options.FailurePolicy.Collect && options.FailurePolicy.MaxRetainedFindings < 0 {
		return nil, &ConfigError{"max_retained_findings must be nonnegative"}
	}
	if options.Retention.MaxRetainedEdges != nil && *options.Retention.MaxRetainedEdges < 0 {
		return nil, &ConfigError{"max_retained_edges must be nonnegative"}
	}
	if e := m.validate(); e != nil {
		return nil, e
	}
	if m.InitialState == nil || m.Step == nil || m.Inputs == nil || m.EqualStates == nil {
		return nil, &ModelError{"enumeration requires initial, step, inputs, and equality callbacks"}
	}
	if options.Reduction != nil && (options.Reduction.EligibleClass == nil || options.Reduction.Projection == nil) {
		return nil, &ConfigError{"reduction requires eligible class and projection callbacks"}
	}
	control, e := newRunControl(limits.Control)
	if e != nil {
		return nil, e
	}
	run := newGraphSearchRun(m, config, limits, options, control)
	if e := run.execute(); e != nil {
		run.r.Termination = "model_error"
		run.finalize()
		return nil, &GraphSearchError[S, I, O]{e, run.r}
	}
	run.finalize()
	if e := run.emit(GraphEvent[I, O]{Kind: EventSearchFinished, Termination: run.r.Termination}); e != nil {
		run.r.Termination = "model_error"
		run.finalize()
		return nil, &GraphSearchError[S, I, O]{e, run.r}
	}
	return run.r, nil
}

type graphRepresentative[S any] struct {
	edge                     int
	state                    S
	disposition              Disposition
	stateChecks, transChecks []Check
	projection               []byte
}

func saturatingAdd(a, b uint64) uint64 {
	if a > math.MaxUint64-b {
		return math.MaxUint64
	}
	return a + b
}
func checkBytes(checks []Check) uint64 {
	total := uint64(0)
	for _, c := range checks {
		total = saturatingAdd(total, uint64(unsafe.Sizeof(c))+uint64(len(c.ID))+uint64(len(c.Status))+uint64(len(c.Details)))
	}
	return total
}

type graphSearchRun[S, I, O any] struct {
	m                       Model[S, I, O]
	g                       GenerateModel[S, I, O]
	config                  SearchConfig
	limits                  SearchLimits
	options                 GraphSearchOptions[S, I, O]
	control                 runControl
	checkStorage            []Check
	collect, expandFailing  bool
	maxFindings             int
	needsEdgeRecord, retain bool
	r                       *GraphSearchReport[S, I, O]
	visited                 map[uint64][]int
	nextEdge, currentLayer  int
	activeNode              *int
	// The evaluated edge whose successor is not yet resolved, retained or not.
	pendingEdge *int
}

func newGraphSearchRun[S, I, O any](m Model[S, I, O], config SearchConfig, limits SearchLimits, options GraphSearchOptions[S, I, O], control runControl) *graphSearchRun[S, I, O] {
	run := &graphSearchRun[S, I, O]{m: m, g: GenerateModel[S, I, O]{Model: m}, config: config, limits: limits, options: options, control: control, visited: map[uint64][]int{}}
	run.collect = options.FailurePolicy.Collect
	run.maxFindings = 1
	if run.collect {
		run.expandFailing = options.FailurePolicy.ExpandFailingSuccessors
		run.maxFindings = options.FailurePolicy.MaxRetainedFindings
	}
	run.retain = !options.Retention.StreamEdgesOnly
	run.needsEdgeRecord = run.retain || options.Events != nil
	run.r = &GraphSearchReport[S, I, O]{Termination: "graph_exhausted", Coverage: GraphCoverage{Kind: CoverageExhaustive}, EdgesRetained: run.retain,
		Nodes: []GraphNode[S, I]{}, Edges: []GraphEdge[I, O]{}, Findings: []GraphFinding[I]{}, FailedProperties: []PropertyFailureCount{}, ReductionDecisions: []ReductionDecision{}}
	return run
}
func (run *graphSearchRun[S, I, O]) stop() bool {
	if reason := run.control.stop(); reason != "" {
		run.r.Termination = reason
		return true
	}
	return false
}
func (run *graphSearchRun[S, I, O]) emit(event GraphEvent[I, O]) error {
	if run.options.Events == nil {
		return nil
	}
	_, e := call("graph event sink", func() (struct{}, error) { return struct{}{}, run.options.Events(event) })
	return e
}
func (run *graphSearchRun[S, I, O]) detached(edge GraphEdge[I, O]) (*GraphEdge[I, O], error) {
	input, e := copied(run.m.CloneInput, edge.Input)
	if e != nil {
		return nil, e
	}
	outputs, e := run.copyOutputs(edge.Outputs)
	if e != nil {
		return nil, e
	}
	edge.Input, edge.Outputs = input, outputs
	edge.StateChecks, edge.TransitionChecks = copyChecks(edge.StateChecks), copyChecks(edge.TransitionChecks)
	edge.Annotations = append([]string{}, edge.Annotations...)
	return &edge, nil
}
func (run *graphSearchRun[S, I, O]) copyOutputs(outputs []O) ([]O, error) {
	slots := append([]O(nil), outputs...)
	return call("snapshot", func() ([]O, error) {
		result := make([]O, len(slots))
		for k, o := range slots {
			result[k] = run.m.CloneOutput(o)
		}
		return result, nil
	})
}
func (run *graphSearchRun[S, I, O]) hash(state S) (uint64, error) {
	return call("state hash", func() (uint64, error) {
		if run.m.HashState == nil {
			return 0, nil
		}
		return run.m.HashState(run.m.CloneState(state)), nil
	})
}

// path returns detached root-to-node inputs followed by input.
func (run *graphSearchRun[S, I, O]) path(index int, input I) ([]I, error) {
	return call("search history", func() ([]I, error) {
		path := []I{run.m.CloneInput(input)}
		for current := index; run.r.Nodes[current].Parent != nil; current = *run.r.Nodes[current].Parent {
			path = append(path, run.m.CloneInput(run.r.Nodes[current].Input))
		}
		for l, r := 0, len(path)-1; l < r; l, r = l+1, r-1 {
			path[l], path[r] = path[r], path[l]
		}
		return path, nil
	})
}

// chargeGraph is false when the bytes would exceed the declared graph byte cap.
func (run *graphSearchRun[S, I, O]) chargeGraph(bytes uint64) bool {
	total := saturatingAdd(run.r.EstimatedGraphBytes, bytes)
	if run.options.Retention.MaxGraphBytes != nil && total > *run.options.Retention.MaxGraphBytes {
		return false
	}
	run.r.EstimatedGraphBytes = total
	return true
}
func (run *graphSearchRun[S, I, O]) nodeBytes(checks []Check) uint64 {
	var node GraphNode[S, I]
	return saturatingAdd(uint64(unsafe.Sizeof(node)), checkBytes(checks))
}
func (run *graphSearchRun[S, I, O]) edgeBytes(edge GraphEdge[I, O]) uint64 {
	var output O
	bytes := uint64(unsafe.Sizeof(edge))
	bytes = saturatingAdd(bytes, checkBytes(edge.StateChecks))
	bytes = saturatingAdd(bytes, checkBytes(edge.TransitionChecks))
	bytes = saturatingAdd(bytes, uint64(len(edge.Outputs))*uint64(unsafe.Sizeof(output)))
	for _, label := range edge.Annotations {
		bytes = saturatingAdd(bytes, uint64(unsafe.Sizeof(label))+uint64(len(label)))
	}
	return bytes
}
func (run *graphSearchRun[S, I, O]) setTarget(edge int, target EdgeTarget) {
	if run.pendingEdge != nil && *run.pendingEdge == edge {
		run.pendingEdge = nil
	}
	if run.r.EdgesRetained && edge < len(run.r.Edges) {
		run.r.Edges[edge].Target = target
	}
}
func (run *graphSearchRun[S, I, O]) reject(edge int, reason string) error {
	run.setTarget(edge, EdgeTarget{Kind: TargetRejected, Reason: reason})
	run.r.RejectedSuccessors++
	return run.emit(GraphEvent[I, O]{Kind: EventSuccessorRejected, Edge: edge, Reason: reason})
}

// rejectStop rejects edge with the current stop termination and ends the search.
func (run *graphSearchRun[S, I, O]) rejectStop(edge int) (bool, error) {
	return false, run.reject(edge, run.r.Termination)
}
func (run *graphSearchRun[S, I, O]) finishNode(id int, status NodeExpansion) error {
	run.r.Nodes[id].Expansion = status
	run.activeNode = nil
	return run.emit(GraphEvent[I, O]{Kind: EventExpansionFinished, Node: id, Expansion: status})
}
func (run *graphSearchRun[S, I, O]) recordFinding(violations []Violation, edge *int, earlier bool, witness func() ([]I, error)) error {
	index := run.r.FindingCount
	run.r.FindingCount = saturatingAdd(run.r.FindingCount, 1)
	for _, v := range violations {
		found := false
		for k := range run.r.FailedProperties {
			p := &run.r.FailedProperties[k]
			if p.Phase == v.Phase && p.ID == v.Check.ID {
				p.Count = saturatingAdd(p.Count, 1)
				found = true
				break
			}
		}
		if !found {
			run.r.FailedProperties = append(run.r.FailedProperties, PropertyFailureCount{v.Phase, v.Check.ID, 1, index})
		}
	}
	if len(run.r.Findings) < run.maxFindings {
		inputs, e := witness()
		if e != nil {
			return e
		}
		run.r.Findings = append(run.r.Findings, GraphFinding[I]{index, inputs, violations, edge, earlier})
	}
	return run.emit(GraphEvent[I, O]{Kind: EventFinding, FindingIndex: index, FindingEdge: edge, Violations: append([]Violation{}, violations...)})
}
func (run *graphSearchRun[S, I, O]) completeLayer() error {
	run.r.CompletedLayers++
	return run.emit(GraphEvent[I, O]{Kind: EventLayerCompleted, Depth: run.currentLayer})
}

// finalize resolves interrupted state on every exit path, including errors.
func (run *graphSearchRun[S, I, O]) finalize() {
	r := run.r
	if run.activeNode != nil && *run.activeNode < len(r.Nodes) && r.Nodes[*run.activeNode].Expansion.Kind == ExpansionUnexpanded {
		r.Nodes[*run.activeNode].Expansion = NodeExpansion{ExpansionInterrupted, r.Termination}
		run.activeNode = nil
	}
	// An edge evaluated when the search ended (stop-at-first failure, error)
	// is a rejected successor whether or not its record was retained.
	if run.pendingEdge != nil {
		run.setTarget(*run.pendingEdge, EdgeTarget{Kind: TargetRejected, Reason: r.Termination})
		r.RejectedSuccessors++
	}
	r.FrontierSize = 0
	for _, n := range r.Nodes {
		if n.Expansion.Kind != ExpansionComplete {
			r.FrontierSize++
		}
	}
	if r.Termination == "graph_exhausted" || r.Termination == "depth_bound" {
		if !r.DepthCutoff && !r.FailurePruned {
			r.Coverage = GraphCoverage{Kind: CoverageExhaustive}
		} else {
			r.Coverage = GraphCoverage{Kind: CoverageBounded, DepthCutoff: r.DepthCutoff, FailurePruned: r.FailurePruned}
		}
	} else {
		r.Coverage = GraphCoverage{Kind: CoverageIncomplete, Reason: r.Termination}
	}
}

// execute mirrors enumerateObserved callback for callback; graph bookkeeping
// adds only detaching clone calls, events and optional application hooks.
func (run *graphSearchRun[S, I, O]) execute() error {
	m, r := run.m, run.r
	if run.stop() {
		return nil
	}
	initial, e := call("initial_state", m.InitialState)
	if e != nil {
		return e
	}
	initial, e = copied(m.CloneState, initial)
	if e != nil {
		return e
	}
	// Retain the search node before checks or observers can mutate an alias.
	initialNode, initialNodeErr := copied(m.CloneState, initial)
	r.States = 1
	checks, e := searchInitialObservedInto(m, initial, run.options.Observer, &run.checkStorage)
	if e != nil {
		return e
	}
	initialChecks := copyChecks(checks)
	bad := findings("initial_state", checks, &r.SkippedChecks)
	if len(bad) > 0 {
		if e := run.recordFinding(bad, nil, false, func() ([]I, error) { return []I{}, nil }); e != nil {
			return e
		}
		if !run.collect {
			r.Termination = "failure_found"
			return nil
		}
	}
	if run.stop() {
		return nil
	}
	if initialNodeErr != nil {
		return initialNodeErr
	}
	initial = initialNode
	initialBytes, e := estimateState(m, initial)
	if e != nil {
		return e
	}
	r.EstimatedRetainedStateBytes = initialBytes
	if run.stop() {
		return nil
	}
	if run.limits.MaxEstimatedStateBytes != nil {
		if initialBytes == nil {
			return &ModelError{"state byte budget requires estimated_state_bytes"}
		}
		if *initialBytes > *run.limits.MaxEstimatedStateBytes {
			zero := uint64(0)
			r.States = 0
			r.EstimatedRetainedStateBytes = &zero
			r.Termination = "estimated_state_byte_limit"
			return nil
		}
	}
	h, e := run.hash(initial)
	if e != nil {
		return e
	}
	if run.stop() {
		return nil
	}
	if !run.chargeGraph(run.nodeBytes(initialChecks)) {
		zero := uint64(0)
		r.States = 0
		r.EstimatedRetainedStateBytes = &zero
		r.Termination = "graph_retention_limit"
		return nil
	}
	r.Nodes = append(r.Nodes, GraphNode[S, I]{ID: 0, State: initial, Checks: initialChecks, PathFailed: len(bad) > 0, Expansion: NodeExpansion{Kind: ExpansionUnexpanded}})
	run.visited[h] = []int{0}
	if e := run.emit(GraphEvent[I, O]{Kind: EventRootAdmitted, Node: 0}); e != nil {
		return e
	}
	if len(bad) > 0 && !run.expandFailing {
		r.FailurePruned = true
		if e := run.finishNode(0, NodeExpansion{Kind: ExpansionFailurePruned}); e != nil {
			return e
		}
	}
	for cursor := 0; cursor < len(r.Nodes); cursor++ {
		if depth := r.Nodes[cursor].Depth; depth > run.currentLayer {
			if e := run.completeLayer(); e != nil {
				return e
			}
			run.currentLayer = depth
		}
		if r.Nodes[cursor].Expansion.Kind == ExpansionFailurePruned {
			continue
		}
		more, e := run.expand(cursor)
		if e != nil || !more {
			return e
		}
	}
	if e := run.completeLayer(); e != nil {
		return e
	}
	if r.DepthCutoff {
		r.Termination = "depth_bound"
	}
	return nil
}

// expand expands one dequeued node; false when the search ends.
func (run *graphSearchRun[S, I, O]) expand(cursor int) (bool, error) {
	m := run.m
	if run.stop() {
		return false, nil
	}
	run.activeNode = &cursor
	if e := run.emit(GraphEvent[I, O]{Kind: EventExpansionStarted, Node: cursor}); e != nil {
		return false, e
	}
	current := run.r.Nodes[cursor]
	iterator, e := call("enumerate inputs", func() (Iterator[I], error) { return m.Inputs(m.CloneState(current.State)) })
	if e != nil {
		return false, e
	}
	if run.stop() {
		return false, nil
	}
	if current.Depth == run.config.MaxDepth {
		_, ok, e := next(iterator)
		if e != nil {
			return false, e
		}
		if run.stop() {
			return false, nil
		}
		status := ExpansionComplete
		if ok {
			run.r.DepthCutoff = true
			status = ExpansionDepthFrontier
		}
		return true, run.finishNode(cursor, NodeExpansion{Kind: status})
	}
	representatives := map[string][]graphRepresentative[S]{}
	for {
		if run.stop() {
			return false, nil
		}
		input, ok, e := next(iterator)
		if e != nil {
			return false, e
		}
		if run.stop() {
			return false, nil
		}
		if !ok {
			break
		}
		if run.r.Transitions == run.config.MaxTransitions {
			run.r.Termination = "transition_limit"
			return false, nil
		}
		more, e := run.evaluate(cursor, current, input, representatives)
		if e != nil || !more {
			return false, e
		}
	}
	return true, run.finishNode(cursor, NodeExpansion{Kind: ExpansionComplete})
}

// evaluate evaluates and resolves one edge; false when the search ends.
func (run *graphSearchRun[S, I, O]) evaluate(cursor int, current GraphNode[S, I], input I, representatives map[string][]graphRepresentative[S]) (bool, error) {
	m, r, observer := run.m, run.r, run.options.Observer
	// Retain the enumerated input before reducer callbacks can mutate an alias.
	input, e := copied(m.CloneInput, input)
	if e != nil {
		return false, e
	}
	var transitionObserver func(S, I, Transition[S, O], []Check, []Check, []I) error
	if observer != nil {
		transitionObserver = observer.Transition
	}
	transition, e := perform(m, current.State, input)
	if e != nil {
		return false, e
	}
	edgeCapture := captureFuzzEdge(run.g, current.State, input, transition, transitionObserver)
	successor, successorErr := copied(m.CloneState, transition.State)
	r.Transitions++
	r.MaxDepthReached = max(r.MaxDepthReached, current.Depth+1)
	// Detach the edge payload before checks/observers can mutate an external
	// alias. Copy failures surface only when the payload is used.
	var payloadInput I
	var payloadOutputs []O
	var payloadErr error
	if run.needsEdgeRecord {
		payloadInput, payloadErr = copied(m.CloneInput, input)
		if payloadErr == nil {
			payloadOutputs, payloadErr = run.copyOutputs(transition.Outputs)
		}
	}
	var history []I
	needsHistory := false
	if observer != nil && observer.NeedsHistory != nil {
		needsHistory, e = call("observer needs history", func() (bool, error) { return observer.NeedsHistory(), nil })
		if e != nil {
			return false, e
		}
	}
	if needsHistory {
		history, e = run.path(cursor, input)
		if e != nil {
			return false, e
		}
	}
	bad, boundary, e := searchEdgeObservedBoundary(run.g, current.State, input, transition, history, &r.SkippedChecks, observer, &run.checkStorage, edgeCapture)
	if e != nil {
		return false, e
	}
	stateChecks, transitionChecks := copyChecks(run.checkStorage[:boundary]), copyChecks(run.checkStorage[boundary:])
	labels, e := run.annotations(current.State, input, successor, successorErr, transition)
	if e != nil {
		return false, e
	}
	edgeID := run.nextEdge
	run.nextEdge++
	run.pendingEdge = &edgeID
	retained := false
	if run.needsEdgeRecord {
		if payloadErr != nil {
			return false, payloadErr
		}
		edge := GraphEdge[I, O]{edgeID, cursor, payloadInput, transition.Disposition, payloadOutputs, stateChecks, transitionChecks, labels, EdgeTarget{Kind: TargetPending}}
		if r.EdgesRetained {
			if limit := run.options.Retention.MaxRetainedEdges; limit != nil && len(r.Edges) >= *limit {
				r.EdgesRetained = false
			} else if !run.chargeGraph(run.edgeBytes(edge)) {
				r.EdgesRetained = false
			} else {
				r.Edges = append(r.Edges, edge)
				retained = true
			}
		}
		if run.options.Events != nil {
			record, e := run.detached(edge)
			if e != nil {
				return false, e
			}
			if e := run.emit(GraphEvent[I, O]{Kind: EventEdgeEvaluated, Edge: edgeID, EdgeRecord: record}); e != nil {
				return false, e
			}
		}
	}
	if len(bad) > 0 {
		e := run.recordFinding(bad, &edgeID, current.PathFailed, func() ([]I, error) { return run.path(cursor, input) })
		if e != nil {
			return false, e
		}
		if !run.collect {
			r.Termination = "failure_found"
			return false, nil
		}
	}
	if run.retain && !retained {
		// The graph can no longer be retained completely.
		r.Termination = "graph_retention_limit"
		return false, run.reject(edgeID, "graph_retention_limit")
	}
	if run.stop() {
		return run.rejectStop(edgeID)
	}
	if successorErr != nil {
		return false, successorErr
	}
	if reduction := run.options.Reduction; reduction != nil {
		reduced, more, e := run.reduce(reduction, cursor, current, input, successor, transition, stateChecks, transitionChecks, labels, edgeID, representatives)
		if e != nil || !more || reduced {
			return more, e
		}
	}
	h, e := run.hash(successor)
	if e != nil {
		return false, e
	}
	if run.stop() {
		return run.rejectStop(edgeID)
	}
	duplicate := -1
	// Rust inserts each newly admitted state at the collision chain head.
	for position := len(run.visited[h]) - 1; position >= 0; position-- {
		if run.stop() {
			return run.rejectStop(edgeID)
		}
		index := run.visited[h][position]
		equal, e := run.sameState(r.Nodes[index].State, successor)
		if e != nil {
			return false, e
		}
		if run.stop() {
			return run.rejectStop(edgeID)
		}
		if equal {
			duplicate = index
			break
		}
	}
	if duplicate >= 0 {
		run.setTarget(edgeID, EdgeTarget{Kind: TargetDuplicate, ID: duplicate})
		r.DuplicateEdges++
		r.GraphEdges++
		return true, run.emit(GraphEvent[I, O]{Kind: EventDuplicateEdge, Edge: edgeID, Existing: duplicate})
	}
	if len(bad) > 0 && !run.expandFailing {
		r.FailurePruned = true
		return true, run.reject(edgeID, "failing_successor")
	}
	if len(r.Nodes) == run.config.MaxStates {
		r.Termination = "state_limit"
		return false, run.reject(edgeID, "state_limit")
	}
	estimate, e := estimateState(m, successor)
	if e != nil {
		return false, e
	}
	if run.stop() {
		return run.rejectStop(edgeID)
	}
	var total *uint64
	if r.EstimatedRetainedStateBytes != nil && estimate != nil && *estimate <= math.MaxUint64-*r.EstimatedRetainedStateBytes {
		sum := *r.EstimatedRetainedStateBytes + *estimate
		total = &sum
	}
	if run.limits.MaxEstimatedStateBytes != nil {
		if estimate == nil {
			return false, &ModelError{"state byte budget requires estimated_state_bytes"}
		}
		if total == nil || *total > *run.limits.MaxEstimatedStateBytes {
			r.Termination = "estimated_state_byte_limit"
			return false, run.reject(edgeID, "estimated_state_byte_limit")
		}
	}
	if !run.chargeGraph(run.nodeBytes(stateChecks)) {
		r.Termination = "graph_retention_limit"
		return false, run.reject(edgeID, "graph_retention_limit")
	}
	saved, e := copied(m.CloneInput, input)
	if e != nil {
		return false, e
	}
	id := len(r.Nodes)
	parent, admitting := cursor, edgeID
	run.visited[h] = append(run.visited[h], id)
	r.Nodes = append(r.Nodes, GraphNode[S, I]{id, successor, &parent, saved, &admitting, current.Depth + 1, stateChecks, current.PathFailed || len(bad) > 0, NodeExpansion{Kind: ExpansionUnexpanded}})
	r.States = len(r.Nodes)
	r.EstimatedRetainedStateBytes = total
	r.GraphEdges++
	run.setTarget(edgeID, EdgeTarget{Kind: TargetAdmitted, ID: id})
	return true, run.emit(GraphEvent[I, O]{Kind: EventSuccessorAdmitted, Edge: edgeID, Node: id})
}
func (run *graphSearchRun[S, I, O]) sameState(admitted, candidate S) (bool, error) {
	return call("state equality", func() (bool, error) {
		return run.m.EqualStates(run.m.CloneState(admitted), run.m.CloneState(candidate)), nil
	})
}
func (run *graphSearchRun[S, I, O]) annotations(before S, input I, successor S, successorErr error, t Transition[S, O]) ([]string, error) {
	annotate := run.options.AnnotateEdge
	if annotate == nil {
		return []string{}, nil
	}
	if successorErr != nil {
		return nil, successorErr
	}
	snapshot, e := cloneTransition(run.m, Transition[S, O]{successor, t.Outputs, t.Disposition})
	if e != nil {
		return nil, e
	}
	labels, e := call("annotate edge", func() ([]string, error) {
		return annotate(run.m.CloneState(before), run.m.CloneInput(input), snapshot)
	})
	if e != nil {
		return nil, e
	}
	return append([]string{}, labels...), nil
}

// reduce applies reduction-v1 to an evaluated edge. reduced is true when the
// edge collapsed into a representative; more is false when the search ends.
func (run *graphSearchRun[S, I, O]) reduce(reduction *EquivalenceReduction[S, I, O], cursor int, current GraphNode[S, I], input I, successor S, t Transition[S, O],
	stateChecks, transitionChecks []Check, labels []string, edgeID int, representatives map[string][]graphRepresentative[S]) (reduced, more bool, err error) {
	m := run.m
	type class struct {
		label string
		ok    bool
	}
	eligible, e := call("reduction eligible_class", func() (class, error) {
		label, ok, e := reduction.EligibleClass(m.CloneState(current.State), m.CloneInput(input))
		return class{label, ok}, e
	})
	if e != nil {
		return false, false, e
	}
	if run.stop() {
		more, e := run.rejectStop(edgeID)
		return false, more, e
	}
	if !eligible.ok {
		return false, true, nil
	}
	observed, e := cloneTransition(m, Transition[S, O]{successor, t.Outputs, t.Disposition})
	if e != nil {
		return false, false, e
	}
	projection, e := call("reduction projection", func() ([]byte, error) {
		bytes, e := reduction.Projection(m.CloneInput(input), observed, append([]string{}, labels...))
		return append([]byte{}, bytes...), e
	})
	if e != nil {
		return false, false, e
	}
	if run.stop() {
		more, e := run.rejectStop(edgeID)
		return false, more, e
	}
	rejected := []RejectedEquivalence{}
	var reducedTo *int
	for _, rep := range representatives[eligible.label] {
		reason := ""
		switch {
		case rep.disposition != t.Disposition:
			reason = "disposition_differs"
		case !slices.Equal(rep.stateChecks, stateChecks) || !slices.Equal(rep.transChecks, transitionChecks):
			reason = "checks_differ"
		case !bytes.Equal(rep.projection, projection):
			reason = "projection_differs"
		default:
			if run.stop() {
				more, e := run.rejectStop(edgeID)
				return false, more, e
			}
			same, e := run.sameState(rep.state, successor)
			if e != nil {
				return false, false, e
			}
			if run.stop() {
				more, e := run.rejectStop(edgeID)
				return false, more, e
			}
			if !same {
				reason = "successor_differs"
			}
		}
		if reason == "" {
			representative := rep.edge
			reducedTo = &representative
			break
		}
		rejected = append(rejected, RejectedEquivalence{rep.edge, reason})
	}
	run.r.ReductionDecisions = append(run.r.ReductionDecisions, ReductionDecision{edgeID, cursor, eligible.label, reducedTo, rejected})
	if reducedTo != nil {
		run.setTarget(edgeID, EdgeTarget{Kind: TargetReduced, ID: *reducedTo})
		run.r.ReducedEdges++
		return true, true, run.emit(GraphEvent[I, O]{Kind: EventEdgeReduced, Edge: edgeID, Representative: *reducedTo})
	}
	state, e := copied(m.CloneState, successor)
	if e != nil {
		return false, false, e
	}
	representatives[eligible.label] = append(representatives[eligible.label], graphRepresentative[S]{edgeID, state, t.Disposition, stateChecks, transitionChecks, projection})
	return false, true, nil
}

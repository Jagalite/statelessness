package statelessness_test

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	s "github.com/Jagalite/statelessness/go"
)

// Hand-derived expectations for graph-search-v1 (S1/S2). Graphs are small
// integer models; step uses the input as the successor with outputs [input*10].
type graphSpec struct {
	edges     map[int][]int
	hash      func(int) uint64
	failState map[int]bool
	failEdge  map[[2]int]bool
}

func graphModel(spec graphSpec) s.Model[int, int, int] {
	return s.Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil },
		Step: func(_ int, input int) (s.Transition[int, int], error) {
			return s.Accept(input, input*10), nil
		},
		CheckState: func(state int) ([]s.Check, error) {
			if spec.failState[state] {
				return []s.Check{s.Passed("ok"), s.Failed("bad_state", fmt.Sprintf("state %d", state))}, nil
			}
			return []s.Check{s.Passed("ok")}, nil
		},
		CheckTransition: func(before, input int, _ s.Transition[int, int]) ([]s.Check, error) {
			if spec.failEdge[[2]int{before, input}] {
				return []s.Check{s.Failed("bad_edge", fmt.Sprintf("%d->%d", before, input))}, nil
			}
			return nil, nil
		},
		CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		Inputs:      func(state int) (s.Iterator[int], error) { return s.Values(spec.edges[state]), nil },
		EqualStates: func(a, b int) bool { return a == b },
		HashState: func(state int) uint64 {
			if spec.hash != nil {
				return spec.hash(state)
			}
			return uint64(state)
		},
	}
}

var diamondEdges = map[int][]int{0: {1, 2, 1}, 1: {3}, 2: {3}}
var chainEdges = map[int][]int{0: {1}, 1: {2}, 2: {3}, 3: {4}}

func states(values ...int) map[int]bool {
	set := map[int]bool{}
	for _, v := range values {
		set[v] = true
	}
	return set
}

type graphRun struct {
	config      s.SearchConfig
	limits      s.SearchLimits
	policy      s.FailurePolicy
	retention   s.GraphRetention
	cancelAfter string
}

func runGraph(t *testing.T, m s.Model[int, int, int], run graphRun) (*s.GraphSearchReport[int, int, int], []string, error) {
	t.Helper()
	if run.config == (s.SearchConfig{}) {
		run.config = s.DefaultSearchConfig()
	}
	events := []string{}
	var cancelled atomic.Bool
	run.limits.Control.Cancellation = &cancelled
	options := s.GraphSearchOptions[int, int, int]{FailurePolicy: run.policy, Retention: run.retention, Events: func(e s.GraphEvent[int, int]) error {
		text := e.String()
		events = append(events, text)
		if text == run.cancelAfter {
			cancelled.Store(true)
		}
		return nil
	}}
	r, e := s.ExploreGraph(m, run.config, run.limits, options)
	return r, events, e
}
func mustGraph(t *testing.T, m s.Model[int, int, int], run graphRun) (*s.GraphSearchReport[int, int, int], []string) {
	t.Helper()
	r, events, e := runGraph(t, m, run)
	if e != nil {
		t.Fatal(e)
	}
	return r, events
}
func targets[S, I, O any](r *s.GraphSearchReport[S, I, O]) []string {
	result := []string{}
	for _, e := range r.Edges {
		result = append(result, e.Target.String())
	}
	return result
}
func expansions[S, I, O any](r *s.GraphSearchReport[S, I, O]) []string {
	result := []string{}
	for _, n := range r.Nodes {
		result = append(result, n.Expansion.String())
	}
	return result
}
func witnesses[S, I, O any](r *s.GraphSearchReport[S, I, O]) [][]I {
	result := [][]I{}
	for _, f := range r.Findings {
		result = append(result, f.Witness)
	}
	return result
}
func expectEqual(t *testing.T, label string, got, want any) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("%s\n got %#v\nwant %#v", label, got, want)
	}
}

// Default options must execute the bfs-controls-v1 callback sequence and report
// the same counters and witness as EnumerateObserved (GRAPH-001).
func TestGraphDefaultOptionsReproduceBFSControlsCorpus(t *testing.T) {
	path := os.Getenv("STATELESS_BFS_CORPUS")
	if path == "" {
		path = "../conformance/bfs-controls-v1.json"
	}
	data, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct{ Cases []bfsVector }
	if e = json.Unmarshal(data, &corpus); e != nil {
		t.Fatal(e)
	}
	if len(corpus.Cases) < 43 {
		t.Fatal("missing BFS vectors")
	}
	for _, v := range corpus.Cases {
		t.Run(v.Case, func(t *testing.T) {
			execute := func(graph bool) (string, []string) {
				events := []string{}
				var cancelled atomic.Bool
				log := func(event string) error {
					events = append(events, event)
					if v.CancelAt != nil && event == *v.CancelAt {
						cancelled.Store(true)
					}
					if v.ErrorAt != nil && event == *v.ErrorAt {
						return fmt.Errorf("fixture check error")
					}
					return nil
				}
				number := func(text *string) *uint64 {
					if text == nil {
						return nil
					}
					value, e := strconv.ParseUint(*text, 10, 64)
					if e != nil {
						t.Fatal(e)
					}
					return &value
				}
				m := s.Model[int, int, int]{InitialState: func() (int, error) { return 0, log("initial") }, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], Step: func(state, input int) (s.Transition[int, int], error) {
					e := log(fmt.Sprintf("step:%d:%d", state, input))
					if v.Fixture == "diamond" {
						return s.Accept[int, int](input), e
					}
					return s.Accept[int, int](state + input), e
				}, CheckState: func(state int) ([]s.Check, error) {
					if e := log(fmt.Sprintf("state:%d", state)); e != nil {
						var prefix []s.Check
						if v.ErrorPrefix {
							prefix = []s.Check{s.Passed("prefix")}
						}
						return prefix, e
					}
					checks := []s.Check{s.Passed("ok")}
					if v.Skipped {
						checks = append(checks, s.Skipped("skip", "not run"))
					}
					if v.FailInitial && state == 0 || v.FailState != nil && state == *v.FailState {
						checks = append(checks, s.Failed("bad", "broken"))
					}
					return checks, nil
				}, CheckTransition: func(before, input int, tr s.Transition[int, int]) ([]s.Check, error) {
					if e := log(fmt.Sprintf("transition:%d:%d:%d", before, input, tr.State)); e != nil {
						var prefix []s.Check
						if v.ErrorPrefix {
							prefix = []s.Check{s.Passed("prefix")}
						}
						return prefix, e
					}
					return nil, nil
				}, EstimatedStateBytes: func(state int) (*uint64, error) {
					if e := log(fmt.Sprintf("estimate:%d", state)); e != nil {
						return nil, e
					}
					if state >= len(v.Estimates) {
						return nil, fmt.Errorf("estimate fixture state out of range")
					}
					return number(v.Estimates[state]), nil
				}, HashState: func(state int) uint64 { log(fmt.Sprintf("hash:%d", state)); return 0 }, EqualStates: func(a, b int) bool { log(fmt.Sprintf("equal:%d:%d", a, b)); return a == b }, Inputs: func(state int) (s.Iterator[int], error) {
					if e := log(fmt.Sprintf("inputs:%d", state)); e != nil {
						return nil, e
					}
					var inputs []int
					switch v.Fixture {
					case "chain":
						if state < 2 {
							inputs = []int{1}
						}
					case "empty":
					case "loop":
						inputs = []int{0, 0}
					case "diamond":
						if state == 0 {
							inputs = []int{1, 2, 1}
						} else if state == 1 || state == 2 {
							inputs = []int{3}
						}
					default:
						return nil, fmt.Errorf("unknown fixture")
					}
					index := 0
					return func() (int, bool, error) {
						if index == len(inputs) {
							return 0, false, log(fmt.Sprintf("next:%d:end", state))
						}
						value := inputs[index]
						index++
						return value, true, log(fmt.Sprintf("next:%d:%d", state, value))
					}, nil
				}}
				observer := s.SearchObserver[int, int, int]{NeedsHistory: func() bool { return v.NeedsHistory }, Initial: func(state int, _ []s.Check) error { return log(fmt.Sprintf("observe_initial:%d", state)) }, Transition: func(before, input int, tr s.Transition[int, int], _, _ []s.Check, history []int) error {
					parts := make([]string, len(history))
					for k, value := range history {
						parts[k] = strconv.Itoa(value)
					}
					text := "-"
					if len(parts) > 0 {
						text = strings.Join(parts, ",")
					}
					return log(fmt.Sprintf("observe:%d:%d:%d:%s", before, input, tr.State, text))
				}, CheckError: func(phase string, checks []s.Check, complete bool) error {
					return log(fmt.Sprintf("check_error:%s:%t:%d", phase, complete, len(checks)))
				}}
				limits := s.SearchLimits{Control: s.RunLimits{Cancellation: &cancelled}, MaxEstimatedStateBytes: number(v.MaxEstimatedStateBytes)}
				switch v.EntryControl {
				case "none":
				case "cancelled":
					cancelled.Store(true)
				case "deadline":
					zero := time.Duration(0)
					limits.Control.MaxDuration = &zero
				case "both":
					cancelled.Store(true)
					zero := time.Duration(0)
					limits.Control.MaxDuration = &zero
				default:
					t.Fatal("unknown entry control")
				}
				config := s.SearchConfig{MaxStates: v.MaxStates, MaxTransitions: v.MaxTransitions, MaxDepth: v.MaxDepth}
				summary := func(termination string, states int, transitions uint64, depth int, skipped uint64, bytes *uint64, failure *s.Failure[int]) string {
					estimate := "nil"
					if bytes != nil {
						estimate = strconv.FormatUint(*bytes, 10)
					}
					return fmt.Sprintf("%s %d %d %d %d %s %#v", termination, states, transitions, depth, skipped, estimate, failure)
				}
				if graph {
					r, e := s.ExploreGraph(m, config, limits, s.GraphSearchOptions[int, int, int]{Observer: &observer})
					if e != nil {
						return "error " + e.Error(), events
					}
					return summary(r.Termination, r.States, r.Transitions, r.MaxDepthReached, r.SkippedChecks, r.EstimatedRetainedStateBytes, r.Failure()), events
				}
				r, e := s.EnumerateObserved(m, config, limits, observer)
				if e != nil {
					return "error " + e.Error(), events
				}
				return summary(r.Termination, r.States, r.Transitions, r.MaxDepthReached, r.SkippedChecks, r.EstimatedRetainedStateBytes, r.Failure), events
			}
			portable, portableEvents := execute(false)
			extended, extendedEvents := execute(true)
			expectEqual(t, "summary", extended, portable)
			expectEqual(t, "callback log", extendedEvents, portableEvents)
			if !slices.Equal(portableEvents, v.Events) {
				t.Fatalf("portable kernel no longer matches the corpus:\n%v\n%v", portableEvents, v.Events)
			}
		})
	}
}

func TestGraphDiamondIDsAndEventOrder(t *testing.T) {
	r, events := mustGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{})
	expectEqual(t, "events", events, []string{"root:0", "expand:0", "edge:0:0:1", "admit:0:1", "edge:1:0:2", "admit:1:2", "edge:2:0:1", "dup:2:1", "finish:0:complete", "layer:0",
		"expand:1", "edge:3:1:3", "admit:3:3", "finish:1:complete", "expand:2", "edge:4:2:3", "dup:4:3", "finish:2:complete", "layer:1",
		"expand:3", "finish:3:complete", "layer:2", "end:graph_exhausted"})
	if r.Coverage.String() != "exhaustive" || r.Termination != "graph_exhausted" {
		t.Fatal(r.Coverage, r.Termination)
	}
	nodeStates, parents, depths, admitting := []int{}, []string{}, []int{}, []string{}
	optional := func(p *int) string {
		if p == nil {
			return "nil"
		}
		return strconv.Itoa(*p)
	}
	for _, n := range r.Nodes {
		nodeStates = append(nodeStates, n.State)
		parents = append(parents, optional(n.Parent))
		depths = append(depths, n.Depth)
		admitting = append(admitting, optional(n.AdmittingEdge))
	}
	expectEqual(t, "states", nodeStates, []int{0, 1, 2, 3})
	expectEqual(t, "parents", parents, []string{"nil", "0", "0", "1"})
	expectEqual(t, "depths", depths, []int{0, 1, 1, 2})
	expectEqual(t, "admitting", admitting, []string{"nil", "0", "1", "3"})
	expectEqual(t, "targets", targets(r), []string{"admitted:1", "admitted:2", "duplicate:1", "admitted:3", "duplicate:3"})
	outputs := [][]int{}
	for k, e := range r.Edges {
		if e.ID != k {
			t.Fatal("edge ID is not its index", e.ID, k)
		}
		outputs = append(outputs, e.Outputs)
	}
	expectEqual(t, "outputs", outputs, [][]int{{10}, {20}, {10}, {30}, {30}})
	if r.Transitions != 5 || r.GraphEdges != 5 || r.DuplicateEdges != 2 || r.RejectedSuccessors != 0 || r.FrontierSize != 0 || r.CompletedLayers != 3 || r.States != 4 || r.Failure() != nil || r.EstimatedGraphBytes == 0 {
		t.Fatalf("%+v", r)
	}
}

func TestGraphSelfLoopAndHashCollisionsKeepStructure(t *testing.T) {
	loop, _ := mustGraph(t, graphModel(graphSpec{edges: map[int][]int{0: {0, 0}}}), graphRun{})
	expectEqual(t, "loop", targets(loop), []string{"duplicate:0", "duplicate:0"})
	if len(loop.Nodes) != 1 || loop.Coverage.Kind != s.CoverageExhaustive {
		t.Fatal(loop.Nodes, loop.Coverage)
	}
	collide, events := mustGraph(t, graphModel(graphSpec{edges: diamondEdges, hash: func(int) uint64 { return 7 }}), graphRun{})
	distinct, expected := mustGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{})
	expectEqual(t, "events", events, expected)
	expectEqual(t, "targets", targets(collide), targets(distinct))
}

func TestGraphDuplicateFailingEdgeKeepsEvidence(t *testing.T) {
	failing := graphSpec{edges: diamondEdges, failEdge: map[[2]int]bool{{2, 3}: true}}
	r, events := mustGraph(t, graphModel(failing), graphRun{policy: s.CollectFailures(10, false)})
	if !slices.Contains(events, "finding:0:4:transition/bad_edge") {
		t.Fatal(events)
	}
	if r.Edges[4].Target.String() != "duplicate:3" || !r.Edges[4].Failed() || *r.Findings[0].Edge != 4 {
		t.Fatal(r.Edges[4], r.Findings)
	}
	expectEqual(t, "witnesses", witnesses(r), [][]int{{2, 3}})
	// A failing edge to an admitted state prunes nothing.
	if r.Coverage.String() != "exhaustive" || r.Termination != "graph_exhausted" {
		t.Fatal(r.Coverage, r.Termination)
	}
	stop, _ := mustGraph(t, graphModel(failing), graphRun{})
	if stop.Termination != "failure_found" || stop.Coverage.String() != "incomplete:failure_found" || stop.Edges[len(stop.Edges)-1].Target.String() != "rejected:failure_found" ||
		stop.Nodes[2].Expansion.String() != "interrupted(failure_found)" || !slices.Equal(stop.Failure().Inputs, []int{2, 3}) || stop.RejectedSuccessors != 1 {
		t.Fatalf("%+v", stop)
	}
	// Review regression: the unresolved edge is counted even when only streamed.
	streamed, _ := mustGraph(t, graphModel(failing), graphRun{retention: s.GraphRetention{StreamEdgesOnly: true}})
	if streamed.RejectedSuccessors != 1 || len(streamed.Edges) != 0 {
		t.Fatal(streamed.RejectedSuccessors, streamed.Edges)
	}
	// Without an event sink, nothing is recorded at all, and it is still counted.
	quiet, e := s.ExploreGraph(graphModel(failing), s.DefaultSearchConfig(), s.SearchLimits{}, s.GraphSearchOptions[int, int, int]{Retention: s.GraphRetention{StreamEdgesOnly: true}})
	if e != nil || quiet.RejectedSuccessors != 1 {
		t.Fatal(e, quiet)
	}
}

func TestGraphStateCapOnNewSuccessor(t *testing.T) {
	r, events := mustGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{config: s.SearchConfig{MaxStates: 2, MaxTransitions: 1_000_000, MaxDepth: 100}})
	expectEqual(t, "suffix", events[len(events)-3:], []string{"edge:1:0:2", "reject:1:state_limit", "end:state_limit"})
	expectEqual(t, "targets", targets(r), []string{"admitted:1", "rejected:state_limit"})
	expectEqual(t, "expansions", expansions(r), []string{"interrupted(state_limit)", "unexpanded"})
	if r.Coverage.String() != "incomplete:state_limit" || r.RejectedSuccessors != 1 || r.FrontierSize != 2 || r.CompletedLayers != 0 {
		t.Fatalf("%+v", r)
	}
}

func TestGraphEdgeCapMidwayThroughNode(t *testing.T) {
	r, _ := mustGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{config: s.SearchConfig{MaxStates: 100, MaxTransitions: 2, MaxDepth: 100}})
	if r.Termination != "transition_limit" || r.Transitions != 2 || len(r.Edges) != 2 || r.Nodes[0].Expansion.String() != "interrupted(transition_limit)" || r.Coverage.String() != "incomplete:transition_limit" {
		t.Fatalf("%+v", r)
	}
}

func TestGraphCancellationAndDeadline(t *testing.T) {
	r, events := mustGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{cancelAfter: "admit:3:3"})
	if r.Termination != "cancelled" || events[len(events)-1] != "end:cancelled" || r.FrontierSize != 3 {
		t.Fatal(r.Termination, events)
	}
	// The admission completes; the following poll stops node 1 before it finishes.
	expectEqual(t, "expansions", expansions(r), []string{"complete", "interrupted(cancelled)", "unexpanded", "unexpanded"})
	late, _ := mustGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{cancelAfter: "edge:4:2:3"})
	if late.Edges[4].Target.String() != "rejected:cancelled" || late.RejectedSuccessors != 1 {
		t.Fatal(late.Edges[4], late.RejectedSuccessors)
	}
	zero := time.Duration(0)
	expired, none := mustGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{limits: s.SearchLimits{Control: s.RunLimits{MaxDuration: &zero}}})
	if expired.Termination != "deadline" || expired.States != 0 || len(expired.Nodes) != 0 {
		t.Fatalf("%+v", expired)
	}
	expectEqual(t, "pre-start", none, []string{"end:deadline"})
}

func TestGraphZeroDepthAndDepthBoundCoverage(t *testing.T) {
	config := func(depth int) s.SearchConfig {
		return s.SearchConfig{MaxStates: 100, MaxTransitions: 100, MaxDepth: depth}
	}
	zero, events := mustGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{config: config(0)})
	expectEqual(t, "events", events, []string{"root:0", "expand:0", "finish:0:depthFrontier", "layer:0", "end:depth_bound"})
	if zero.Termination != "depth_bound" || zero.Coverage.String() != "bounded:depth_cutoff" || zero.Transitions != 0 {
		t.Fatalf("%+v", zero)
	}
	one, _ := mustGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{config: config(1)})
	if one.Coverage.String() != "bounded:depth_cutoff" || one.FrontierSize != 2 {
		t.Fatalf("%+v", one)
	}
	// Reaching max_depth at a leaf is not a cutoff.
	leaf, _ := mustGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{config: config(2)})
	if leaf.Coverage.String() != "exhaustive" || leaf.Termination != "graph_exhausted" {
		t.Fatalf("%+v", leaf)
	}
}

func TestGraphRetentionLimitsAreVisible(t *testing.T) {
	three := 3
	capped, _ := mustGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{retention: s.GraphRetention{MaxRetainedEdges: &three}})
	if capped.Termination != "graph_retention_limit" || len(capped.Edges) != 3 || capped.EdgesRetained || capped.Coverage.String() != "incomplete:graph_retention_limit" {
		t.Fatalf("%+v", capped)
	}
	streamed, events := mustGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{retention: s.GraphRetention{StreamEdgesOnly: true}})
	edgeEvents := 0
	for _, e := range events {
		if strings.HasPrefix(e, "edge:") {
			edgeEvents++
		}
	}
	if len(streamed.Edges) != 0 || streamed.EdgesRetained || streamed.Coverage.String() != "exhaustive" || streamed.GraphEdges != 5 || edgeEvents != 5 {
		t.Fatalf("%+v", streamed)
	}
	one := uint64(1)
	tiny, _ := mustGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{retention: s.GraphRetention{MaxGraphBytes: &one}})
	if tiny.Termination != "graph_retention_limit" || tiny.States != 0 {
		t.Fatalf("%+v", tiny)
	}
	negative := -1
	if _, _, e := runGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{retention: s.GraphRetention{MaxRetainedEdges: &negative}}); e == nil {
		t.Fatal("negative edge cap accepted")
	}
	if _, _, e := runGraph(t, graphModel(graphSpec{edges: diamondEdges}), graphRun{policy: s.CollectFailures(-1, true)}); e == nil {
		t.Fatal("negative finding cap accepted")
	}
}

func TestGraphFirstAndSubsequentFailures(t *testing.T) {
	m := graphModel(graphSpec{edges: chainEdges, failState: states(2, 3, 4)})
	r, _ := mustGraph(t, m, graphRun{policy: s.CollectFailures(10, true)})
	expectEqual(t, "witnesses", witnesses(r), [][]int{{1, 2}, {1, 2, 3}, {1, 2, 3, 4}})
	continued, indexes, pathFailed := []bool{}, []uint64{}, []bool{}
	for _, f := range r.Findings {
		continued = append(continued, f.RequiresContinuedEvidence)
		indexes = append(indexes, f.Index)
	}
	for _, n := range r.Nodes {
		pathFailed = append(pathFailed, n.PathFailed)
	}
	expectEqual(t, "continued", continued, []bool{false, true, true})
	expectEqual(t, "indexes", indexes, []uint64{0, 1, 2})
	expectEqual(t, "path failed", pathFailed, []bool{false, false, true, true, true})
	expectEqual(t, "properties", r.FailedProperties, []s.PropertyFailureCount{{Phase: "state", ID: "bad_state", Count: 3, FirstFinding: 0}})
	if r.FindingCount != 3 || r.FindingsTruncated() || r.Coverage.String() != "exhaustive" || !slices.Equal(r.Failure().Inputs, []int{1, 2}) {
		t.Fatalf("%+v", r)
	}
	pruned, _ := mustGraph(t, m, graphRun{policy: s.CollectFailures(10, false)})
	if pruned.FindingCount != 1 || len(pruned.Nodes) != 2 || pruned.Edges[len(pruned.Edges)-1].Target.String() != "rejected:failing_successor" ||
		pruned.Coverage.String() != "bounded:failure_pruned" || pruned.Termination != "graph_exhausted" {
		t.Fatalf("%+v", pruned)
	}
}

func TestGraphMultiplePhasesOnOneEdgeAreOrdered(t *testing.T) {
	r, events := mustGraph(t, graphModel(graphSpec{edges: chainEdges, failState: states(1), failEdge: map[[2]int]bool{{0, 1}: true}}), graphRun{policy: s.CollectFailures(10, true)})
	if len(r.Findings) != 1 || !slices.Contains(events, "finding:0:0:state/bad_state,transition/bad_edge") {
		t.Fatal(r.Findings, events)
	}
	ids := []string{}
	for _, p := range r.FailedProperties {
		ids = append(ids, p.ID)
	}
	expectEqual(t, "ids", ids, []string{"bad_state", "bad_edge"})
	expectEqual(t, "state checks", r.Edges[0].StateChecks, []s.Check{s.Passed("ok"), s.Failed("bad_state", "state 1")})
	expectEqual(t, "transition checks", r.Edges[0].TransitionChecks, []s.Check{s.Failed("bad_edge", "0->1")})
}

func TestGraphInitialFailureUnderCollection(t *testing.T) {
	m := graphModel(graphSpec{edges: chainEdges, failState: states(0)})
	expand, _ := mustGraph(t, m, graphRun{policy: s.CollectFailures(10, true)})
	expectEqual(t, "witnesses", witnesses(expand), [][]int{{}})
	if expand.Findings[0].Edge != nil || len(expand.Nodes) != 5 || expand.Findings[0].Violations[0].Phase != "initial_state" || expand.Coverage.String() != "exhaustive" {
		t.Fatalf("%+v", expand)
	}
	pruned, events := mustGraph(t, m, graphRun{policy: s.CollectFailures(10, false)})
	expectEqual(t, "events", events, []string{"finding:0:-:initial_state/bad_state", "root:0", "finish:0:failurePruned", "layer:0", "end:graph_exhausted"})
	if pruned.Coverage.String() != "bounded:failure_pruned" {
		t.Fatal(pruned.Coverage)
	}
}

func TestGraphFailurePlusCutoff(t *testing.T) {
	r, _ := mustGraph(t, graphModel(graphSpec{edges: chainEdges, failState: states(2)}), graphRun{config: s.SearchConfig{MaxStates: 3, MaxTransitions: 100, MaxDepth: 100}, policy: s.CollectFailures(10, true)})
	if r.FindingCount != 1 || r.Termination != "state_limit" || r.Coverage.String() != "incomplete:state_limit" || !slices.Equal(r.Findings[0].Witness, []int{1, 2}) {
		t.Fatalf("%+v", r)
	}
}

func TestGraphCheckerErrorAfterPartialEvidence(t *testing.T) {
	m := graphModel(graphSpec{edges: chainEdges, failState: states(2)})
	base := m.CheckState
	m.CheckState = func(state int) ([]s.Check, error) {
		if state == 3 {
			return nil, fmt.Errorf("checker exploded")
		}
		return base(state)
	}
	_, e := s.ExploreGraph(m, s.DefaultSearchConfig(), s.SearchLimits{}, s.GraphSearchOptions[int, int, int]{FailurePolicy: s.CollectFailures(10, true)})
	var graphError *s.GraphSearchError[int, int, int]
	if !errors.As(e, &graphError) || !strings.Contains(e.Error(), "checker exploded") {
		t.Fatal(e)
	}
	r := graphError.Report
	expectEqual(t, "witnesses", witnesses(r), [][]int{{1, 2}})
	// The failing batch never completed, so no record exists for its edge.
	if r.Termination != "model_error" || r.Coverage.String() != "incomplete:model_error" || r.Nodes[2].Expansion.String() != "interrupted(model_error)" ||
		len(r.Edges) != 2 || r.Edges[1].Target.String() != "admitted:2" || r.RejectedSuccessors != 0 {
		t.Fatalf("%+v", r)
	}
}

func TestGraphDeterministicFindingTruncation(t *testing.T) {
	m := graphModel(graphSpec{edges: chainEdges, failState: states(2, 3, 4)})
	a, eventsA := mustGraph(t, m, graphRun{policy: s.CollectFailures(1, true)})
	b, eventsB := mustGraph(t, m, graphRun{policy: s.CollectFailures(1, true)})
	expectEqual(t, "a", witnesses(a), [][]int{{1, 2}})
	expectEqual(t, "b", witnesses(b), [][]int{{1, 2}})
	expectEqual(t, "events", eventsA, eventsB)
	if a.FindingCount != 3 || !a.FindingsTruncated() || a.FailedProperties[0].Count != 3 {
		t.Fatalf("%+v", a)
	}
	none, _ := mustGraph(t, graphModel(graphSpec{edges: chainEdges, failState: states(2)}), graphRun{policy: s.CollectFailures(0, true)})
	if len(none.Findings) != 0 || none.Failure() != nil || none.FindingCount != 1 || none.FailedProperties[0].ID != "bad_state" {
		t.Fatalf("%+v", none)
	}
}

func TestGraphAnnotationsAndDetachedEventPayloads(t *testing.T) {
	type box struct{ n int }
	clone := func(b *box) *box { return &box{b.n} }
	m := s.Model[*box, *box, *box]{InitialState: func() (*box, error) { return &box{0}, nil }, Step: func(_ *box, i *box) (s.Transition[*box, *box], error) {
		return s.Accept(&box{i.n}, &box{i.n}), nil
	}, CheckState: func(*box) ([]s.Check, error) { return nil, nil }, CloneState: clone, CloneInput: clone, CloneOutput: clone,
		Inputs: func(b *box) (s.Iterator[*box], error) {
			if b.n == 0 {
				return s.Values([]*box{{1}, {2}}), nil
			}
			return s.Values[*box](nil), nil
		}, EqualStates: func(a, b *box) bool { return a.n == b.n }}
	options := s.GraphSearchOptions[*box, *box, *box]{Events: func(e s.GraphEvent[*box, *box]) error {
		if e.Kind == s.EventEdgeEvaluated {
			e.EdgeRecord.Input.n = 99
			for _, o := range e.EdgeRecord.Outputs {
				o.n = 99
			}
		}
		return nil
	}, AnnotateEdge: func(before, input *box, t s.Transition[*box, *box]) ([]string, error) {
		input.n = 77
		return []string{fmt.Sprintf("from %d to %d", before.n, t.State.n)}, nil
	}}
	r, e := s.ExploreGraph(m, s.DefaultSearchConfig(), s.SearchLimits{}, options)
	if e != nil {
		t.Fatal(e)
	}
	inputs, outputs, labels, nodes := []int{}, []int{}, [][]string{}, []int{}
	for _, edge := range r.Edges {
		inputs = append(inputs, edge.Input.n)
		outputs = append(outputs, edge.Outputs[0].n)
		labels = append(labels, edge.Annotations)
	}
	for _, n := range r.Nodes {
		nodes = append(nodes, n.State.n)
	}
	expectEqual(t, "inputs", inputs, []int{1, 2})
	expectEqual(t, "outputs", outputs, []int{1, 2})
	expectEqual(t, "labels", labels, [][]string{{"from 0 to 1"}, {"from 0 to 2"}})
	expectEqual(t, "nodes", nodes, []int{0, 1, 2})
}

func TestGraphSinkErrorAbortsWithPartialReport(t *testing.T) {
	options := s.GraphSearchOptions[int, int, int]{Events: func(e s.GraphEvent[int, int]) error {
		if e.Kind == s.EventSuccessorAdmitted && e.Node == 2 {
			return fmt.Errorf("sink full")
		}
		return nil
	}}
	_, e := s.ExploreGraph(graphModel(graphSpec{edges: diamondEdges}), s.DefaultSearchConfig(), s.SearchLimits{}, options)
	var graphError *s.GraphSearchError[int, int, int]
	if !errors.As(e, &graphError) || !strings.Contains(e.Error(), "sink full") || len(graphError.Report.Nodes) != 3 || graphError.Report.Coverage.String() != "incomplete:model_error" {
		t.Fatal(e)
	}
}

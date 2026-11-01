package statelessness_test

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

// Consumes the shared graph-search-v1 and progress-analysis-v1 fixtures.
// Relocated package tests bind fixture paths explicitly (scripts/check-native-ports.py).
func migrationFixture(t *testing.T, name, key string, cases any) {
	t.Helper()
	path := os.Getenv(key)
	if path == "" {
		path = "../conformance/" + name
	}
	data, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var doc struct {
		Version int
		Cases   json.RawMessage
	}
	if e = json.Unmarshal(data, &doc); e != nil {
		t.Fatal(e)
	}
	if doc.Version != 1 {
		t.Fatal("unexpected fixture version", doc.Version)
	}
	if e = json.Unmarshal(doc.Cases, cases); e != nil {
		t.Fatal(e)
	}
}

func TestGraphSearchFixtures(t *testing.T) {
	var cases []struct {
		Case      string
		Edges     map[string][]int
		Hash      string
		FailState []int   `json:"fail_state"`
		FailEdge  [][]int `json:"fail_edge"`
		Config    struct {
			MaxStates      int    `json:"max_states"`
			MaxTransitions uint64 `json:"max_transitions"`
			MaxDepth       int    `json:"max_depth"`
		}
		Policy struct {
			Kind                    string
			MaxRetainedFindings     int  `json:"max_retained_findings"`
			ExpandFailingSuccessors bool `json:"expand_failing_successors"`
		}
		Expected struct {
			Events                    []string
			Targets                   []string
			Termination               string
			Coverage                  string
			States                    int
			Transitions               uint64
			FindingWitnesses          [][]int `json:"finding_witnesses"`
			RequiresContinuedEvidence []bool  `json:"requires_continued_evidence"`
			FindingCount              uint64  `json:"finding_count"`
		}
	}
	migrationFixture(t, "graph-search-v1.json", "STATELESS_GRAPH_SEARCH_CORPUS", &cases)
	if len(cases) != 10 {
		t.Fatal("graph-search-v1 cases", len(cases))
	}
	for _, c := range cases {
		t.Run(c.Case, func(t *testing.T) {
			spec := graphSpec{edges: map[int][]int{}, failState: states(c.FailState...), failEdge: map[[2]int]bool{}}
			for key, inputs := range c.Edges {
				node, e := strconv.Atoi(key)
				if e != nil {
					t.Fatal(e)
				}
				spec.edges[node] = inputs
			}
			for _, pair := range c.FailEdge {
				spec.failEdge[[2]int{pair[0], pair[1]}] = true
			}
			switch c.Hash {
			case "constant":
				spec.hash = func(int) uint64 { return 0 }
			case "identity":
			default:
				t.Fatal("unknown hash", c.Hash)
			}
			policy := s.StopAtFirst()
			switch c.Policy.Kind {
			case "collect":
				policy = s.CollectFailures(c.Policy.MaxRetainedFindings, c.Policy.ExpandFailingSuccessors)
			case "stop_at_first":
			default:
				t.Fatal("unknown policy", c.Policy.Kind)
			}
			r, events := mustGraph(t, graphModel(spec), graphRun{config: s.SearchConfig{MaxStates: c.Config.MaxStates, MaxTransitions: c.Config.MaxTransitions, MaxDepth: c.Config.MaxDepth}, policy: policy})
			x := c.Expected
			expectEqual(t, "events", events, x.Events)
			expectEqual(t, "targets", targets(r), x.Targets)
			continued := []bool{}
			for _, f := range r.Findings {
				continued = append(continued, f.RequiresContinuedEvidence)
			}
			expectEqual(t, "witnesses", witnesses(r), x.FindingWitnesses)
			expectEqual(t, "requires continued evidence", continued, x.RequiresContinuedEvidence)
			got := fmt.Sprint(r.Termination, " ", r.Coverage, " ", r.States, " ", r.Transitions, " ", r.FindingCount)
			want := fmt.Sprint(x.Termination, " ", x.Coverage, " ", x.States, " ", x.Transitions, " ", x.FindingCount)
			expectEqual(t, "summary", got, want)
		})
	}
}

func TestProgressAnalysisFixtures(t *testing.T) {
	var cases []struct {
		Case       string
		Nodes      int
		Edges      [][3]any
		Unmeasured []int
		Kinds      []string
		Ranks      []uint64
		Expected   struct {
			Classes              []string
			Closed               [][]int
			Unsettled            []int
			SettledTransactions  int  `json:"settled_transactions"`
			UnmeasuredComponents int  `json:"unmeasured_components"`
			GraphComplete        bool `json:"graph_complete"`
		}
	}
	migrationFixture(t, "progress-analysis-v1.json", "STATELESS_PROGRESS_CORPUS", &cases)
	if len(cases) != 9 {
		t.Fatal("progress-analysis-v1 cases", len(cases))
	}
	for _, c := range cases {
		t.Run(c.Case, func(t *testing.T) {
			edges := []s.ProgressEdge{}
			for _, e := range c.Edges {
				edges = append(edges, s.ProgressEdge{Source: int(e[0].(float64)), Target: int(e[1].(float64)), Fair: e[2].(bool)})
			}
			unmeasured := states(c.Unmeasured...)
			measured := make([]bool, c.Nodes)
			for k := range measured {
				measured[k] = !unmeasured[k]
			}
			graph, e := s.NewProgressGraph(c.Nodes, edges, measured)
			if e != nil {
				t.Fatal(e)
			}
			var rank func(int) (uint64, error)
			if c.Ranks != nil {
				rank = func(n int) (uint64, error) { return c.Ranks[n], nil }
			}
			r, e := s.AnalyzeProgress(graph, func(n int) (s.ProgressKind, error) { return s.ProgressKind(c.Kinds[n]), nil }, rank)
			if e != nil {
				t.Fatal(e)
			}
			classes := []string{}
			for _, class := range r.Classes {
				classes = append(classes, string(class))
			}
			x := c.Expected
			expectEqual(t, "classes", classes, x.Classes)
			expectEqual(t, "closed", r.ClosedNonterminalComponents, x.Closed)
			expectEqual(t, "unsettled", r.UnsettledTransactionStates, x.Unsettled)
			expectEqual(t, "counts", []any{r.SettledTransactionStates, r.UnmeasuredComponents, r.GraphComplete}, []any{x.SettledTransactions, x.UnmeasuredComponents, x.GraphComplete})
		})
	}
}

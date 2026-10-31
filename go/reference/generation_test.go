package reference

import (
	s "github.com/Jagalite/statelessness/go"
	"testing"
)

func TestGeneratorMatchesReferenceIndexedDomain(t *testing.T) {
	c := Controller{Bounds: DefaultBounds()}
	g := c.Generator()
	state := State{Jobs: []Job{{Generation: 1, Attempt: 1, Phase: "running", ResourceOwned: true}}}
	it, err := c.Inputs(state)
	if err != nil {
		t.Fatal(err)
	}
	var domain []Input
	for {
		v, ok, err := it()
		if err != nil {
			t.Fatal(err)
		}
		if !ok {
			break
		}
		domain = append(domain, v)
	}
	rng, oracle := s.NewRng(42), s.NewRng(42)
	for i := 0; i < 100; i++ {
		idx, _ := oracle.Index(uint64(len(domain)))
		got, ok, err := g.Generate(state, rng)
		if err != nil || !ok || got != domain[idx] {
			t.Fatalf("draw %d: %+v %v", i, got, err)
		}
		enabled, err := g.IsEnabled(state, got)
		if err != nil || !enabled {
			t.Fatal(got, err)
		}
	}
}
func TestMutationEvaluationShrinksAndReplaysTargets(t *testing.T) {
	for name, target := range mutationTargets {
		t.Run(name, func(t *testing.T) {
			result := EvaluateMutation(name, s.DefaultSearchConfig())
			if result.Status != "killed" || result.Original == nil || result.Minimized == nil {
				t.Fatalf("not killed: %+v", result)
			}
			found := false
			for _, v := range result.Minimized.Violations {
				if v.Check.ID == target {
					found = true
				}
			}
			if !found {
				t.Fatal("target lost")
			}
			c := Controller{Bounds: DefaultBounds(), Mutation: name}
			trace, err := s.Record(c.Model(), s.Values(result.Minimized.Inputs), len(result.Minimized.Inputs))
			if err != nil {
				t.Fatal(err)
			}
			replay, err := s.Replay(c.Model(), trace, false)
			if err != nil || replay.Outcome != "exact" || !replay.FailureReproduced {
				t.Fatalf("replay: %+v %v", replay, err)
			}
		})
	}
	cfg := s.DefaultSearchConfig()
	cfg.MaxDepth = 0
	if result := EvaluateMutation("missing-cleanup", cfg); result.Status != "not_evaluated" {
		t.Fatal("cutoff misclassified", result)
	}
}

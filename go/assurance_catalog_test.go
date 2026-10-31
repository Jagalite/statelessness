package statelessness_test

import (
	"errors"
	s "github.com/Jagalite/statelessness/go"
	"testing"
)

func TestAssuranceCatalogDerivesObservedAndUnreachableStatuses(t *testing.T) {
	m := assuranceManifest()
	m.Properties = []s.AssuranceProperty{{ID: "z", Phase: "state"}, {ID: "a", Phase: "state"}}
	m.Families = []s.AssuranceFamily{{Name: "optional", Excluded: true, Reason: "not supported"}, {Name: "request"}}
	m.Goals = []s.AssuranceGoal{{ID: "history", Scope: "history"}, {ID: "unseen", Scope: "transition"}, {ID: "seen", Scope: "transition"}}
	collector, err := s.NewAssuranceCollector[int, int, int](m, s.Identity("example", "build"), func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
		return s.AssuranceHits{Families: []string{"request", "optional"}, Goals: []string{"seen"}}, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	o := collector.Observer()
	if err = o.Initial(0, []s.Check{s.Passed("a"), s.Skipped("z", "not available")}); err != nil {
		t.Fatal(err)
	}
	if err = o.Transition(0, 1, s.Accept[int, int](1), []s.Check{s.Failed("a", "failed"), s.Skipped("z", "not available")}, nil, []int{1}); err != nil {
		t.Fatal(err)
	}
	e := collector.Finish("graph_exhausted", "graph_exhausted", nil)
	catalog, err := e.Catalog()
	if err != nil {
		t.Fatal(err)
	}
	if catalog.Properties[0].Property.ID != "a" || catalog.Properties[0].Status != "failed" || catalog.Properties[1].Status != "skipped" {
		t.Fatalf("properties: %+v", catalog.Properties)
	}
	if catalog.Families[0].Name != "optional" || catalog.Families[0].Status != "exclusion_violated" || catalog.Families[0].Reason != "not supported" || catalog.Families[1].Status != "observed" {
		t.Fatalf("families: %+v", catalog.Families)
	}
	if catalog.Goals[0].Status != "not_reached_before_stop" || catalog.Goals[1].Status != "observed" || catalog.Goals[2].Status != "unreachable_under_declared_model" {
		t.Fatalf("goals: %+v", catalog.Goals)
	}
	if e.AssessStrict().Adequate {
		t.Fatal("failed and unreached requirements assessed adequate")
	}
	e.Errors = []string{"callback failure"}
	catalog, err = e.Catalog()
	if err != nil || catalog.Goals[2].Status != "not_reached_before_stop" {
		t.Fatalf("callback error falsely proves unreachable: %+v %v", catalog.Goals, err)
	}
	e.Termination = "unknown"
	if _, err = e.Catalog(); err == nil {
		t.Fatal("inconsistent evidence accepted")
	}
}
func TestAssuranceCatalogNeverPromotesPartialCallbackHits(t *testing.T) {
	m := assuranceManifest()
	c, err := s.NewAssuranceCollector[int, int, int](m, s.Identity("example", "build"), func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
		return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, errors.New("partial")
	})
	if err != nil {
		t.Fatal(err)
	}
	o := c.Observer()
	o.Transition(0, 1, s.Accept[int, int](1), []s.Check{s.Passed("state")}, []s.Check{s.Passed("edge")}, []int{1})
	catalog, err := c.Finish("graph_exhausted", "graph_exhausted", nil).Catalog()
	if err != nil {
		t.Fatal(err)
	}
	if catalog.Families[0].Status != "missing" || catalog.Goals[0].Status != "not_reached_before_stop" {
		t.Fatalf("partial hits accepted: %+v", catalog)
	}
}

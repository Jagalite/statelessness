package statelessness_test

import (
	s "github.com/Jagalite/statelessness/go"
	"math"
	"strings"
	"testing"
)

func assuranceMergeJob(t *testing.T) s.AssuranceEvidence {
	t.Helper()
	c, err := s.NewAssuranceCollector[int, int, int](assuranceManifest(), s.Identity("example", "build"), func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
		return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	o := c.Observer()
	if err = o.Initial(0, []s.Check{s.Passed("state")}); err != nil {
		t.Fatal(err)
	}
	if err = o.Transition(0, 1, s.Accept[int, int](1), []s.Check{s.Passed("state")}, []s.Check{s.Passed("edge")}, []int{1}); err != nil {
		t.Fatal(err)
	}
	return c.Finish("cases_completed", "cases_completed", nil)
}
func TestAssuranceMergeKeepsMissingAndRejectsForgedEvidence(t *testing.T) {
	a := assuranceMergeJob(t)
	m := a.Manifest
	meta := a.Metadata
	merged, err := s.MergeAssuranceReports(m, meta, []uint64{7, 3}, []s.AssuranceJob{{ID: 3, Evidence: a}})
	if err != nil {
		t.Fatal(err)
	}
	if len(merged.MissingJobs) != 1 || merged.MissingJobs[0] != 7 || merged.Report.Outcome != "interrupted" || merged.Report.AssessStrict().Adequate {
		t.Fatalf("missing jobs hidden: %+v", merged)
	}
	full, err := s.MergeAssuranceReports(m, meta, []uint64{7, 3}, []s.AssuranceJob{{ID: 7, Evidence: a}, {ID: 3, Evidence: a}})
	if err != nil {
		t.Fatal(err)
	}
	if full.Report.Outcome != "cases_completed" || !full.Report.AssessStrict().Adequate || full.Report.CheckedTransitions != 2 || full.Jobs[0].ID != 3 {
		t.Fatalf("valid merge: %+v", full)
	}
	full.Report.Outcome = "graph_exhausted"
	if full.Report.AssessStrict().Adequate {
		t.Fatal("forged aggregate outcome accepted")
	}
	full.Report.Outcome = "cases_completed"
	full.Jobs[0].Evidence.Families["request"] = false
	if !full.Report.AssessStrict().Adequate {
		t.Fatal("returned job alias changed report proof")
	}
	a.Outcome = "graph_exhausted"
	if _, err = s.MergeAssuranceReports(m, meta, []uint64{3}, []s.AssuranceJob{{ID: 3, Evidence: a}}); err == nil {
		t.Fatal("inconsistent job accepted")
	}
	if _, err = s.MergeAssuranceReports(m, meta, []uint64{3, 3}, nil); err == nil {
		t.Fatal("duplicate expected job accepted")
	}
}
func TestAssuranceMergeSaturatesCountsAndFailureDominates(t *testing.T) {
	a := assuranceMergeJob(t)
	b := assuranceMergeJob(t)
	p := s.AssuranceProperty{ID: "state", Phase: "state"}
	a.Properties[p] = s.AssuranceCheckCounts{Passed: math.MaxUint64}
	b.Properties[p] = s.AssuranceCheckCounts{Passed: 1}
	b.Outcome = "failed"
	b.Termination = "failure_found"
	merged, err := s.MergeAssuranceReports(a.Manifest, a.Metadata, []uint64{1, 2, 3}, []s.AssuranceJob{{ID: 1, Evidence: a}, {ID: 2, Evidence: b}})
	if err != nil {
		t.Fatal(err)
	}
	if merged.Report.Outcome != "failed" || merged.Report.Properties[p].Passed != math.MaxUint64 || merged.Report.AssessStrict().Adequate {
		t.Fatalf("bad merge: %+v", merged.Report)
	}
}

func TestAssuranceMergeSupportsExplicitAdmissionLimits(t *testing.T) {
	m := assuranceManifest()
	m.Provenance.Manifest = strings.Repeat("p", 4097)
	limits := s.DefaultAssuranceObservationLimits()
	limits.MaxTextBytes = 5000
	collector, err := s.NewAssuranceCollectorWithLimits[int, int, int](m, s.Identity("example", "build"), func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
		return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, nil
	}, limits)
	if err != nil {
		t.Fatal(err)
	}
	o := collector.Observer()
	if err = o.Initial(0, []s.Check{s.Passed("state")}); err != nil {
		t.Fatal(err)
	}
	if err = o.Transition(0, 1, s.Accept[int, int](1), []s.Check{s.Passed("state")}, []s.Check{s.Passed("edge")}, []int{1}); err != nil {
		t.Fatal(err)
	}
	evidence := collector.Finish("cases_completed", "cases_completed", nil)
	jobs := []s.AssuranceJob{{ID: 1, Evidence: evidence}}
	if _, err = s.MergeAssuranceReports(m, evidence.Metadata, []uint64{1}, jobs); err == nil {
		t.Fatal("default bound ignored")
	}
	merged, err := s.MergeAssuranceReportsWithLimits(m, evidence.Metadata, []uint64{1}, jobs, limits)
	if err != nil || !merged.Report.AssessStrict().Adequate {
		t.Fatalf("custom merge: %v", err)
	}
	data, err := merged.JSON()
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseMergedAssuranceJSON(data); err == nil {
		t.Fatal("default import bound ignored")
	}
	restored, err := s.ParseMergedAssuranceJSONWithAdmission(data, s.DefaultAssuranceJSONLimits(), limits)
	if err != nil || !restored.Report.AssessStrict().Adequate {
		t.Fatalf("custom import: %v", err)
	}
}

func TestAssuranceMergeBoundsAdmitNestedEvidenceBeforeCloning(t *testing.T) {
	e := assuranceMergeJob(t)
	jobs := []s.AssuranceJob{{ID: 1, Evidence: e}}
	admission := s.DefaultAssuranceObservationLimits()
	bounds := s.DefaultAssuranceMergeLimits()
	bounds.MaxEvidenceBytes = 1
	if _, err := s.MergeAssuranceReportsWithBounds(e.Manifest, e.Metadata, []uint64{1}, jobs, admission, bounds); err == nil {
		t.Fatal("oversized evidence admitted")
	}
	bounds = s.DefaultAssuranceMergeLimits()
	bounds.MaxJobs = 1
	if _, err := s.MergeAssuranceReportsWithBounds(e.Manifest, e.Metadata, []uint64{1, 2}, jobs, admission, bounds); err == nil {
		t.Fatal("oversized expected job set admitted")
	}
	inner, err := s.MergeAssuranceReports(e.Manifest, e.Metadata, []uint64{1}, jobs)
	if err != nil {
		t.Fatal(err)
	}
	nested := []s.AssuranceJob{{ID: 2, Evidence: inner.Report}}
	bounds = s.DefaultAssuranceMergeLimits()
	bounds.MaxNestedDepth = 1
	if _, err = s.MergeAssuranceReportsWithBounds(e.Manifest, e.Metadata, []uint64{2}, nested, admission, bounds); err == nil {
		t.Fatal("nested depth bound ignored")
	}
	bounds.MaxNestedDepth = 2
	outer, err := s.MergeAssuranceReportsWithBounds(e.Manifest, e.Metadata, []uint64{2}, nested, admission, bounds)
	if err != nil || !outer.Report.AssessStrict().Adequate {
		t.Fatalf("valid bounded nested merge: %v", err)
	}
}

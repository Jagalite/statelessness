package statelessness_test

import (
	"errors"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"reflect"
	"strings"
	"testing"
)

func assuranceManifest() s.AssuranceManifest {
	return s.AssuranceManifest{Version: 1, Model: "example", Provenance: s.AssuranceProvenance{Manifest: "m1", Model: "model1", Properties: "p1"}, RequiredOutcome: "completed", Properties: []s.AssuranceProperty{{ID: "state", Phase: "state"}, {ID: "edge", Phase: "transition"}}, Families: []s.AssuranceFamily{{Name: "request"}}, Goals: []s.AssuranceGoal{{ID: "seen", Scope: "transition"}}}
}
func TestAssuranceCollectorObservedFuzzAndStrictNegatives(t *testing.T) {
	g := integerSearch(t, "singleton", nil)
	g.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("state")}, nil }
	g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) { return []s.Check{s.Passed("edge")}, nil }
	manifest := assuranceManifest()
	collector, err := s.NewAssuranceCollector[int, int, int](manifest, s.Identity("example", "build"), func(_ int, _ int, _ s.Transition[int, int], history []int) (s.AssuranceHits, error) {
		if len(history) == 0 {
			t.Fatal("missing actual input history")
		}
		return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	report, err := s.FuzzObserved(g, s.FuzzConfig{Cases: 1, MaxSteps: 1, MaxTransitions: 1}, s.RunLimits{}, collector.Observer())
	if err != nil {
		t.Fatal(err)
	}
	evidence := collector.Finish("cases_completed", report.Termination, nil)
	if evidence.CheckedTransitions != 1 || !evidence.AssessStrict().Adequate {
		t.Fatalf("actual checked evidence: %+v %+v", evidence, evidence.AssessStrict())
	}
	manifest.Properties[0].ID = "mutated"
	if evidence.Manifest.Properties[0].ID != "state" {
		t.Fatal("manifest aliased")
	}
	evidence.Properties[s.AssuranceProperty{ID: "unexpected", Phase: "state"}] = s.AssuranceCheckCounts{Failed: 1}
	if evidence.AssessStrict().Adequate {
		t.Fatal("undeclared failed check hidden")
	}
}
func TestAssuranceIncompleteAndFailedCoverageAreNotEvidence(t *testing.T) {
	m := assuranceManifest()
	c, err := s.NewAssuranceCollector[int, int, int](m, s.Identity("example", "build"), func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
		return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, errors.New("broken")
	})
	if err != nil {
		t.Fatal(err)
	}
	obs := c.Observer()
	if err = obs.CheckError("state", []s.Check{}, false); err != nil {
		t.Fatal(err)
	}
	if err = obs.Transition(0, 1, s.Accept[int, int](1), []s.Check{s.Passed("state")}, []s.Check{s.Passed("edge")}, []int{1}); err != nil {
		t.Fatal(err)
	}
	e := c.Finish("graph_exhausted", "complete", nil)
	if e.Properties[s.AssuranceProperty{ID: "state", Phase: "state"}].Missing != 0 || e.UnclassifiedTransitions != 1 || e.Families["request"] || e.Goals["seen"] || len(e.Errors) == 0 || e.AssessStrict().Adequate {
		t.Fatalf("invalid coverage evidence: %+v", e)
	}
}
func TestAssuranceManifestRejectsAmbiguousCatalog(t *testing.T) {
	m := assuranceManifest()
	m.Families = append(m.Families, m.Families[0])
	if m.Validate() == nil {
		t.Fatal("duplicate accepted")
	}
	m = assuranceManifest()
	m.Families[0] = s.AssuranceFamily{Name: "request", Excluded: true}
	if m.Validate() == nil {
		t.Fatal("unexplained exclusion accepted")
	}
}

func TestAssuredFuzzBindsActualOutcomeAndBudgets(t *testing.T) {
	g := integerSearch(t, "singleton", nil)
	g.Codec = &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("example", "build"), nil }}
	g.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("state")}, nil }
	g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) { return []s.Check{s.Passed("edge")}, nil }
	coverage := func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
		return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, nil
	}
	config := s.FuzzConfig64{Seed: 42, Cases: 1, MaxSteps: 1, MaxTransitions: 10}
	run, err := s.AssuranceFuzz64(g, assuranceManifest(), coverage, config, s.RunLimits{})
	if err != nil || run.SearchError != nil || run.Search == nil || run.Search.Termination != "cases_completed" || run.Assurance.Outcome != "cases_completed" || run.Assurance.Budgets["seed"] != "42" || !run.Assurance.AssessStrict().Adequate {
		t.Fatalf("completed: %+v %v", run, err)
	}
	// Rust fuzz_budgets defines the budget keys; observation admission limits
	// are not among them.
	for key := range run.Assurance.Budgets {
		if strings.HasPrefix(key, "assurance.") {
			t.Fatalf("Go-only budget key %q in Rust-defined budgets", key)
		}
	}
	forged := run.Assurance
	forged.Termination = "unknown"
	if forged.AssessStrict().Adequate {
		t.Fatal("unknown termination accepted")
	}
	forged.Termination = "transition_limit"
	if forged.AssessStrict().Adequate {
		t.Fatal("mismatched termination accepted")
	}
	config.MaxTransitions = 0
	limited, err := s.AssuranceFuzz64(g, assuranceManifest(), coverage, config, s.RunLimits{})
	if err != nil || limited.SearchError != nil || limited.Search.Termination != "transition_limit" || limited.Assurance.Outcome != "budget_limited" || limited.Assurance.AssessStrict().Adequate {
		t.Fatalf("limited: %+v %v", limited, err)
	}
	g.Step = func(int, int) (s.Transition[int, int], error) {
		return s.Transition[int, int]{}, errors.New("reducer failure")
	}
	config.MaxTransitions = 10
	failed, err := s.AssuranceFuzz64(g, assuranceManifest(), coverage, config, s.RunLimits{})
	if err != nil || failed.SearchError == nil || failed.Assurance.Outcome != "failed" || len(failed.Assurance.Errors) == 0 || failed.Assurance.AssessStrict().Adequate {
		t.Fatalf("failure: %+v %v", failed, err)
	}
}
func TestAssuredEnumerationUsesActualMetadataAndSearchTermination(t *testing.T) {
	g := integerSearch(t, "singleton", nil)
	g.EqualStates = func(a, b int) bool { return a == b }
	g.Codec = &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("example", "build"), nil }}
	g.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("state")}, nil }
	g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) { return []s.Check{s.Passed("edge")}, nil }
	coverage := func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
		return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, nil
	}
	run, err := s.AssuranceEnumerate(g.Model, assuranceManifest(), coverage, s.SearchConfig{MaxStates: 10, MaxTransitions: 10, MaxDepth: 1}, s.SearchLimits{})
	if err != nil || run.SearchError != nil || run.Search == nil || run.Search.Termination != "depth_bound" || run.Assurance.Outcome != "depth_limited" || run.Assurance.AssessStrict().Adequate || run.Assurance.Budgets["max_depth"] != "1" {
		t.Fatalf("depth bound: %+v %v", run, err)
	}
	g.Codec.Metadata = func() (s.Metadata, error) { return s.Identity("wrong", "build"), nil }
	if _, err = s.AssuranceEnumerate(g.Model, assuranceManifest(), coverage, s.DefaultSearchConfig(), s.SearchLimits{}); err == nil {
		t.Fatal("forged model identity accepted")
	}
}

func TestAssuredGuidedFuzzPinsFeedbackAndRetainsSearchFailure(t *testing.T) {
	g := guidedCounter()
	g.Codec = &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("example", "build"), nil }}
	g.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("state")}, nil }
	g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) { return []s.Check{s.Passed("edge")}, nil }
	feedback := counterFeedback()
	calls := 0
	feedback.Metadata = func() (s.FeedbackMetadata, error) {
		calls++
		return s.FeedbackMetadata{Name: "feedback", Version: uint32(calls), Build: "build"}, nil
	}
	coverage := func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
		return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, nil
	}
	config := s.FuzzConfig64{Seed: 7, Cases: 1, MaxSteps: 2, MaxTransitions: 10}
	guidance := s.DefaultGuidanceConfig64()
	run, err := s.AssuranceGuidedFuzz64(g, assuranceManifest(), coverage, config, guidance, feedback, s.RunLimits{})
	if err != nil || run.SearchError != nil || run.Search == nil || run.Search.Guidance == nil || calls != 1 || run.Search.Guidance.Feedback.Version != 1 || run.Assurance.Budgets["guidance.feedback.version"] != "1" || run.Assurance.Budgets["guidance.algorithm"] != s.GuidanceAlgorithm || !run.Assurance.AssessStrict().Adequate {
		t.Fatalf("guided evidence: %+v %v calls=%d", run, err, calls)
	}
	feedback.TransitionFeatures = func(int, int, s.Transition[int, int], *s.FeatureBuffer[int]) error {
		return errors.New("feedback failure")
	}
	failed, err := s.AssuranceGuidedFuzz64(g, assuranceManifest(), coverage, config, guidance, feedback, s.RunLimits{})
	if err != nil || failed.SearchError == nil || failed.Assurance.Outcome != "failed" || len(failed.Assurance.Errors) == 0 || failed.Assurance.AssessStrict().Adequate {
		t.Fatalf("feedback error hidden: %+v %v", failed, err)
	}
}

func TestAssuranceObservationLimitsFailClosedWithoutPartialHits(t *testing.T) {
	limits := s.DefaultAssuranceObservationLimits()
	limits.MaxProperties = 2
	limits.MaxBatchChecks = 2
	limits.MaxCoverageHits = 1
	c, err := s.NewAssuranceCollectorWithLimits[int, int, int](assuranceManifest(), s.Identity("example", "build"), func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
		return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, nil
	}, limits)
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
	e := c.Finish("cases_completed", "cases_completed", nil)
	if len(e.Errors) == 0 || e.UnclassifiedTransitions != 1 || e.Families["request"] || e.Goals["seen"] || e.AssessStrict().Adequate {
		t.Fatalf("truncated coverage accepted: %+v", e)
	}
	limits.MaxCoverageHits = 2
	c, err = s.NewAssuranceCollectorWithLimits[int, int, int](assuranceManifest(), s.Identity("example", "build"), nil, limits)
	if err != nil {
		t.Fatal(err)
	}
	o = c.Observer()
	o.Initial(0, []s.Check{s.Passed("a"), s.Passed("b"), s.Passed("state")})
	e = c.Finish("cases_completed", "cases_completed", nil)
	if len(e.Errors) == 0 || e.AssessStrict().Adequate {
		t.Fatal("oversized batch accepted")
	}
	e = c.Finish("failed", "ModelError", errors.New(strings.Repeat("x", limits.MaxTextBytes+1)))
	if len(e.Errors) != 1 || e.Errors[0] != "assurance observation limit exceeded" || e.AssessStrict().Adequate {
		t.Fatal("unbounded run error retained")
	}
}

func TestAssuranceManifestAdmissionBoundsAndBudgetProvenance(t *testing.T) {
	limits := s.DefaultAssuranceObservationLimits()
	limits.MaxFamilies = 1
	m := assuranceManifest()
	m.Families = append(m.Families, s.AssuranceFamily{Name: "other"})
	if _, err := s.NewAssuranceCollectorWithLimits[int, int, int](m, s.Identity("example", "build"), nil, limits); err == nil {
		t.Fatal("oversized family catalog accepted")
	}
	m = assuranceManifest()
	limits = s.DefaultAssuranceObservationLimits()
	limits.MaxManifestBytes = 128
	if _, err := s.NewAssuranceCollectorWithLimits[int, int, int](m, s.Identity("example", "build"), nil, limits); err == nil {
		t.Fatal("aggregate manifest byte budget ignored")
	}
	limits = s.DefaultAssuranceObservationLimits()
	limits.MaxTextBytes = 16
	m.Provenance.Manifest = strings.Repeat("x", 17)
	if _, err := s.NewAssuranceCollectorWithLimits[int, int, int](m, s.Identity("example", "build"), nil, limits); err == nil {
		t.Fatal("oversized provenance accepted")
	}
}
func TestAssuranceCollectorRejectsReentrantAndConcurrentObservation(t *testing.T) {
	entered := make(chan struct{})
	release := make(chan struct{})
	var c *s.AssuranceCollector[int, int, int]
	var nested error
	coverage := func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
		nested = c.Observer().Initial(0, []s.Check{s.Passed("forged")})
		close(entered)
		<-release
		return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, nil
	}
	var err error
	c, err = s.NewAssuranceCollector[int, int, int](assuranceManifest(), s.Identity("example", "build"), coverage)
	if err != nil {
		t.Fatal(err)
	}
	o := c.Observer()
	done := make(chan error, 1)
	go func() {
		done <- o.Transition(0, 1, s.Accept[int, int](1), []s.Check{s.Passed("state")}, []s.Check{s.Passed("edge")}, []int{1})
	}()
	<-entered
	if nested == nil {
		t.Fatal("reentrant observation accepted")
	}
	if err = o.Initial(0, []s.Check{s.Passed("forged")}); err == nil {
		t.Fatal("concurrent observation accepted")
	}
	if e := c.Finish("cases_completed", "cases_completed", nil); e.AssessStrict().Adequate {
		t.Fatal("busy finish produced adequate evidence")
	}
	close(release)
	if err = <-done; err != nil {
		t.Fatal(err)
	}
	e := c.Finish("cases_completed", "cases_completed", nil)
	if e.AssessStrict().Adequate || len(e.Errors) == 0 {
		t.Fatal("concurrent misuse not reflected in evidence")
	}
	if _, ok := e.Properties[s.AssuranceProperty{ID: "forged", Phase: "state"}]; ok {
		t.Fatal("rejected checks mutated evidence")
	}
}

func TestAssuredGuidedValidationAndMetadataErrorsRetainEvidence(t *testing.T) {
	g := guidedCounter()
	g.Codec = &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("example", "build"), nil }}
	feedback := counterFeedback()
	calls := 0
	feedback.Metadata = func() (s.FeedbackMetadata, error) {
		calls++
		return s.FeedbackMetadata{}, errors.New("feedback metadata failed")
	}
	cfg := s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 10}
	guidance := s.DefaultGuidanceConfig64()
	guidance.MaxFeatures = 0
	invalid, err := s.AssuranceGuidedFuzz64(g, assuranceManifest(), nil, cfg, guidance, feedback, s.RunLimits{})
	if err != nil || invalid.SearchError == nil || calls != 0 || invalid.Assurance.AssessStrict().Adequate || len(invalid.Assurance.Errors) == 0 {
		t.Fatalf("validation side effects or lost report: %+v %v calls=%d", invalid, err, calls)
	}
	guidance = s.DefaultGuidanceConfig64()
	failed, err := s.AssuranceGuidedFuzz64(g, assuranceManifest(), nil, cfg, guidance, feedback, s.RunLimits{})
	if err != nil || failed.SearchError == nil || calls != 1 || failed.Assurance.AssessStrict().Adequate || len(failed.Assurance.Errors) == 0 {
		t.Fatalf("metadata error lost: %+v %v calls=%d", failed, err, calls)
	}
	if _, ok := failed.Assurance.Budgets["guidance.feedback.name"]; ok {
		t.Fatal("unknown metadata invented")
	}
}

func TestAssuredWrappersRejectOversizedManifestBeforeCallbacks(t *testing.T) {
	m := assuranceManifest()
	m.Families = make([]s.AssuranceFamily, s.DefaultAssuranceObservationLimits().MaxFamilies+1)
	calls := 0
	g := guidedCounter()
	g.Codec = &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) { calls++; return s.Identity("example", "build"), nil }}
	if _, err := s.AssuranceEnumerate(g.Model, m, nil, s.DefaultSearchConfig(), s.SearchLimits{}); err == nil {
		t.Fatal("enumerate accepted oversized manifest")
	}
	if _, err := s.AssuranceFuzz64(g, m, nil, s.DefaultFuzzConfig64(), s.RunLimits{}); err == nil {
		t.Fatal("fuzz accepted oversized manifest")
	}
	if _, err := s.AssuranceGuidedFuzz64(g, m, nil, s.DefaultFuzzConfig64(), s.DefaultGuidanceConfig64(), counterFeedback(), s.RunLimits{}); err == nil {
		t.Fatal("guided accepted oversized manifest")
	}
	if calls != 0 {
		t.Fatal("metadata callback invoked before admission")
	}
}

func TestAssuranceIndexedCoveragePreservesUnknownAndHistorySemantics(t *testing.T) {
	m := assuranceManifest()
	m.Families = nil
	m.Goals = nil
	for i := 0; i < 512; i++ {
		m.Families = append(m.Families, s.AssuranceFamily{Name: fmt.Sprintf("family-%d", i)})
		scope := "transition"
		if i == 511 {
			scope = "history"
		}
		m.Goals = append(m.Goals, s.AssuranceGoal{ID: fmt.Sprintf("goal-%d", i), Scope: scope})
	}
	c, err := s.NewAssuranceCollector[int, int, int](m, s.Identity("example", "build"), func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
		return s.AssuranceHits{Families: []string{"family-511", "unknown-family"}, Goals: []string{"goal-511", "unknown-goal"}}, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	o := c.Observer()
	if !o.NeedsHistory() {
		t.Fatal("history scope lost")
	}
	if err = o.Transition(0, 1, s.Accept[int, int](1), []s.Check{s.Passed("state")}, []s.Check{s.Passed("edge")}, []int{1}); err != nil {
		t.Fatal(err)
	}
	e := c.Finish("cases_completed", "cases_completed", nil)
	if !e.Families["family-511"] || !e.Goals["goal-511"] || e.UnclassifiedTransitions != 0 || len(e.Errors) != 2 || e.AssessStrict().Adequate {
		t.Fatalf("classification changed: %+v", e)
	}
}

func TestAssuranceManifestDeclarationsDoNotInventCoverage(t *testing.T) {
	base := assuranceManifest()
	base.Bounds = map[string]string{"manual": "preserved"}
	modified := base
	if err := modified.AddDomain(s.DomainDescriptor{Name: "requests", Assumptions: "bounded pending work", MaxEntries: 7}); err != nil {
		t.Fatal(err)
	}
	if modified.Bounds["domain.requests.assumptions"] != "bounded pending work" || modified.Bounds["domain.requests.max_entries"] != "7" || modified.Bounds["manual"] != "preserved" || len(base.Bounds) != 1 {
		t.Fatal("domain declaration lost or aliased")
	}
	if err := modified.Require("independent.extra", "transition"); err != nil {
		t.Fatal(err)
	}
	if len(base.Properties) != 2 || len(modified.Properties) != 3 {
		t.Fatal("property declarations aliased")
	}
	c, err := s.NewAssuranceCollector[int, int, int](modified, s.Identity("example", "build"), func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
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
	evidence := c.Finish("cases_completed", "cases_completed", nil)
	if evidence.AssessStrict().Adequate || evidence.Properties[s.AssuranceProperty{ID: "independent.extra", Phase: "transition"}].Missing != 1 {
		t.Fatal("declaration invented execution evidence")
	}
	if err = modified.AddDomain(s.DomainDescriptor{Name: " ", Assumptions: "x", MaxEntries: 1}); err == nil {
		t.Fatal("invalid domain accepted")
	}
	if err = modified.Require("x", "unknown"); err == nil {
		t.Fatal("invalid phase accepted")
	}
}

func TestAssuredRunWrappersHonorExplicitObservationLimits(t *testing.T) {
	m := assuranceManifest()
	m.Provenance.Manifest = strings.Repeat("p", 4097)
	limits := s.DefaultAssuranceObservationLimits()
	limits.MaxTextBytes = 5000
	g := guidedCounter()
	g.Codec = &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("example", "build"), nil }}
	g.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("state")}, nil }
	g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) { return []s.Check{s.Passed("edge")}, nil }
	coverage := func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
		return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, nil
	}
	cfg := s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 10}
	if _, err := s.AssuranceFuzz64(g, m, coverage, cfg, s.RunLimits{}); err == nil {
		t.Fatal("default admission bypassed")
	}
	fuzz, err := s.AssuranceFuzz64WithObservationLimits(g, m, coverage, cfg, s.RunLimits{}, limits)
	if err != nil || fuzz.SearchError != nil || !fuzz.Assurance.AssessStrict().Adequate || fuzz.Assurance.Budgets["assurance.max_text_bytes"] != "" {
		t.Fatalf("custom fuzz: %+v %v", fuzz, err)
	}
	guided, err := s.AssuranceGuidedFuzz64WithObservationLimits(g, m, coverage, cfg, s.DefaultGuidanceConfig64(), counterFeedback(), s.RunLimits{}, limits)
	if err != nil || guided.SearchError != nil || !guided.Assurance.AssessStrict().Adequate {
		t.Fatalf("custom guided: %+v %v", guided, err)
	}
	g.Inputs = func(int) (s.Iterator[int], error) { return s.Values([]int{1}), nil }
	enumeration, err := s.AssuranceEnumerateWithObservationLimits(g.Model, m, coverage, s.SearchConfig{MaxStates: 4, MaxTransitions: 4, MaxDepth: 1}, s.SearchLimits{}, limits)
	if err != nil || enumeration.SearchError != nil || enumeration.Assurance.Budgets["assurance.max_text_bytes"] != "" {
		t.Fatalf("custom enumeration: %+v %v", enumeration, err)
	}
}

func TestAssuranceAssessmentUsesReferenceReasonOrderAndStatuses(t *testing.T) {
	m := assuranceManifest()
	m.RequiredOutcome = "graph_exhausted"
	m.Properties = []s.AssuranceProperty{{ID: "z", Phase: "state"}, {ID: "a", Phase: "transition"}}
	m.Families = []s.AssuranceFamily{{Name: "z"}, {Name: "a"}}
	m.Goals = []s.AssuranceGoal{{ID: "z", Scope: "transition"}, {ID: "a", Scope: "history"}}
	c, err := s.NewAssuranceCollector[int, int, int](m, s.Identity("example", "build"), nil)
	if err != nil {
		t.Fatal(err)
	}
	result := c.Finish("graph_exhausted", "graph_exhausted", nil).AssessStrict()
	want := []string{"property a Transition: Missing", "property z State: Missing", "input family a: Missing", "input family z: Missing", "cover goal a: NotReachedBeforeStop", "cover goal z: UnreachableUnderDeclaredModel"}
	if !reflect.DeepEqual(result.Reasons, want) || result.Adequate {
		t.Fatalf("assessment reasons: %+v", result.Reasons)
	}
}

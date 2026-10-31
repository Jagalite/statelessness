package statelessness_test

import (
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

func TestExactDeadlineDirectSearchAndAssurance(t *testing.T) {
	zero := s.Uint128{}
	g := integerSearch(t, "singleton", nil)
	report, err := s.FuzzWithLimits64(g, s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 1}, s.RunLimits{MaxDurationNS: &zero})
	if err != nil || report.Termination != "deadline" || report.Cases != 0 {
		t.Fatal(report, err)
	}
	high := s.Uint128{Hi: 1}
	report, err = s.FuzzWithLimits64(g, s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 1}, s.RunLimits{MaxDurationNS: &high})
	if err != nil || report.Termination != "cases_completed" {
		t.Fatal(report, err)
	}
	g.Codec = &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) { high = s.Uint128{}; return s.Identity("example", "build"), nil }}
	high = s.Uint128{Hi: 1}
	run, err := s.AssuranceFuzz64(g, assuranceManifest(), nil, s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 1}, s.RunLimits{MaxDurationNS: &high})
	if err != nil || run.SearchError != nil || run.Search.Termination != "cases_completed" || run.Assurance.Budgets["max_duration_ns"] != "18446744073709551616" {
		t.Fatal(run, err)
	}
}

func TestExactDeadlineEnumerationGuidedAndShrink(t *testing.T) {
	zero := s.Uint128{}
	search, err := s.EnumerateWithLimits(bfsCounter(), s.DefaultSearchConfig(), s.SearchLimits{Control: s.RunLimits{MaxDurationNS: &zero}})
	if err != nil || search.Termination != "deadline" {
		t.Fatal(search, err)
	}
	guided, err := s.GuidedFuzzWithLimits(guidedCounter(), guidedConfig(1, 1), s.DefaultGuidanceConfig(), counterFeedback(), s.RunLimits{MaxDurationNS: &zero})
	if err != nil || guided.Termination != "deadline" || guided.Cases != 0 {
		t.Fatal(guided, err)
	}
	original := s.Failure[int]{Inputs: []int{1}, Violations: []s.Violation{{Phase: "state", Check: s.Failed("bad", "")}}}
	shrunk, err := s.ShrinkWithLimits(integerSearch(t, "cycle", nil), original, s.DefaultShrinkConfig(), s.ShrinkLimits{Control: s.RunLimits{MaxDurationNS: &zero}})
	if err != nil || shrunk.Termination != "deadline" || shrunk.ValidatedOriginal {
		t.Fatal(shrunk, err)
	}
}
func TestExactDeadlineCampaignBudgets(t *testing.T) {
	budget := s.Uint128{}
	called := false
	config := s.DefaultCampaignConfig64()
	config.Jobs = 1
	config.Workers = 1
	config.Fuzz = s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 1}
	factory := func(uint64) (s.CampaignModel[int, int, int], error) {
		called = true
		budget = s.Uint128{}
		return s.CampaignModel[int, int, int]{Model: guidedCounter()}, nil
	}
	run, err := s.RunCampaign64(config, s.RunLimits{MaxDurationNS: &budget}, factory, func(s.JobReport64[int]) error { return nil })
	if err != nil || run.Termination.Kind != "deadline" || called {
		t.Fatal(run, err, called)
	}
	budget = s.Uint128{Hi: 1}
	run, err = s.RunCampaign64(config, s.RunLimits{MaxDurationNS: &budget}, factory, func(s.JobReport64[int]) error { return nil })
	if err != nil || run.Termination.Kind != "jobs_completed" || !called {
		t.Fatal(run, err, called)
	}
}

func TestExactDeadlineCampaignConcurrentWorkers(t *testing.T) {
	budget := s.Uint128{Hi: 1}
	cfg := s.DefaultCampaignConfig64()
	cfg.Jobs = 16
	cfg.Workers = 4
	cfg.ResultBuffer = 4
	cfg.Fuzz = s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 1}
	run, err := s.RunCampaign64(cfg, s.RunLimits{MaxDurationNS: &budget}, func(uint64) (s.CampaignModel[int, int, int], error) {
		return s.CampaignModel[int, int, int]{Model: guidedCounter()}, nil
	}, func(s.JobReport64[int]) error { return nil })
	if err != nil || run.Termination.Kind != "jobs_completed" || run.Finished != 16 {
		t.Fatal(run, err)
	}
}

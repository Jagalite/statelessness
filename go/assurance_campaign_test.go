package statelessness_test

import (
	"encoding/json"
	"errors"
	s "github.com/Jagalite/statelessness/go"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestAssuredCampaignUsesCoordinatorAndPreservesSinkFailure(t *testing.T) {
	g := integerSearch(t, "singleton", nil)
	g.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("state")}, nil }
	g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) { return []s.Check{s.Passed("edge")}, nil }
	metadata := s.Identity("example", "build")
	var closed atomic.Int64
	factory := func(id uint64) (s.CampaignModel[int, int, int], error) {
		return s.CampaignModel[int, int, int]{Model: g, Metadata: func() (s.Metadata, error) { return metadata, nil }, Close: func() error { closed.Add(1); return nil }}, nil
	}
	coverage := func(id uint64, _ s.GenerateModel[int, int, int]) (func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error), error) {
		return func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
			return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, nil
		}, nil
	}
	cfg := s.DefaultCampaignConfig64()
	cfg.Jobs = 2
	cfg.Workers = 2
	cfg.Fuzz = s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 10}
	delivered := 0
	run, err := s.AssuranceRunCampaign64(cfg, s.RunLimits{}, assuranceManifest(), metadata, factory, coverage, func(s.JobReport64[int]) error { delivered++; return nil })
	if err != nil || delivered != 2 || closed.Load() != 2 || run.Campaign.Delivered != 2 || len(run.Assurance.MissingJobs) != 0 || !run.Assurance.Report.AssessStrict().Adequate {
		t.Fatalf("campaign: %+v %v", run, err)
	}
	for _, job := range run.Assurance.Jobs {
		if _, ok := job.Evidence.Budgets["assurance.max_manifest_bytes"]; ok {
			t.Fatal("campaign job leaked Go-only observation bounds into Rust budgets")
		}
	}
	encoded, err := run.Assurance.JSON()
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := s.ParseMergedAssuranceJSON(encoded)
	if err != nil || !decoded.Report.AssessStrict().Adequate {
		t.Fatalf("campaign proof round trip: %v", err)
	}
	var modified map[string]json.RawMessage
	if err := json.Unmarshal(encoded, &modified); err != nil {
		t.Fatal(err)
	}
	var proof map[string]json.RawMessage
	if err := json.Unmarshal(modified["campaign"], &proof); err != nil {
		t.Fatal(err)
	}
	proof["delivered"] = json.RawMessage(`3`)
	modified["campaign"], err = json.Marshal(proof)
	if err != nil {
		t.Fatal(err)
	}
	bad, err := json.Marshal(modified)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseMergedAssuranceJSON(bad); err == nil {
		t.Fatal("invalid coordinator count accepted")
	}
	proof["delivered"] = json.RawMessage(`2`)
	proof["failures"] = json.RawMessage(`1`)
	modified["campaign"], err = json.Marshal(proof)
	if err != nil {
		t.Fatal(err)
	}
	bad, err = json.Marshal(modified)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseMergedAssuranceJSON(bad); err == nil {
		t.Fatal("fabricated completed-job failure count accepted")
	}
	proof["failures"] = json.RawMessage(`0`)
	var proofBudgets map[string]string
	if err = json.Unmarshal(proof["budgets"], &proofBudgets); err != nil {
		t.Fatal(err)
	}
	proofBudgets["requested_jobs"] = "0"
	proof["budgets"], err = json.Marshal(proofBudgets)
	if err != nil {
		t.Fatal(err)
	}
	modified["campaign"], err = json.Marshal(proof)
	if err != nil {
		t.Fatal(err)
	}
	var report map[string]json.RawMessage
	if err = json.Unmarshal(modified["report"], &report); err != nil {
		t.Fatal(err)
	}
	var search map[string]json.RawMessage
	if err = json.Unmarshal(report["search"], &search); err != nil {
		t.Fatal(err)
	}
	var searchBudgets map[string]string
	if err = json.Unmarshal(search["budgets"], &searchBudgets); err != nil {
		t.Fatal(err)
	}
	searchBudgets["requested_jobs"] = "0"
	search["budgets"], err = json.Marshal(searchBudgets)
	if err != nil {
		t.Fatal(err)
	}
	report["search"], err = json.Marshal(search)
	if err != nil {
		t.Fatal(err)
	}
	modified["report"], err = json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	bad, err = json.Marshal(modified)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseMergedAssuranceJSON(bad); err == nil {
		t.Fatal("aggregate budget collision accepted")
	}
	// Even a matching aggregate budget must not misstate the coordinator range.
	delete(proofBudgets, "requested_jobs")
	proofBudgets["campaign.first_job"] = "3"
	proof["budgets"], err = json.Marshal(proofBudgets)
	if err != nil {
		t.Fatal(err)
	}
	modified["campaign"], err = json.Marshal(proof)
	if err != nil {
		t.Fatal(err)
	}
	delete(searchBudgets, "requested_jobs")
	searchBudgets["requested_jobs"] = "2"
	searchBudgets["campaign.first_job"] = "3"
	search["budgets"], err = json.Marshal(searchBudgets)
	if err != nil {
		t.Fatal(err)
	}
	report["search"], err = json.Marshal(search)
	if err != nil {
		t.Fatal(err)
	}
	modified["report"], err = json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	bad, err = json.Marshal(modified)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseMergedAssuranceJSON(bad); err == nil {
		t.Fatal("forged coordinator job range accepted")
	}
	closed.Store(0)
	delivered = 0
	failed, err := s.AssuranceRunCampaign64(cfg, s.RunLimits{}, assuranceManifest(), metadata, factory, coverage, func(s.JobReport64[int]) error { delivered++; return errors.New("sink failed") })
	if err != nil || failed.Campaign.Termination.Kind != "callback_error" || delivered != 1 || failed.Assurance.Report.AssessStrict().Adequate || len(failed.Assurance.Report.Errors) == 0 {
		t.Fatalf("sink loss hidden: %+v %v", failed, err)
	}
	encoded, err = failed.Assurance.JSON()
	if err != nil {
		t.Fatal(err)
	}
	decoded, err = s.ParseMergedAssuranceJSON(encoded)
	if err != nil || decoded.Report.AssessStrict().Adequate || len(decoded.Report.Errors) == 0 {
		t.Fatalf("sink failure proof lost: %v", err)
	}
}
func TestAssuredCampaignRejectsActualJobIdentityMismatch(t *testing.T) {
	g := integerSearch(t, "singleton", nil)
	metadata := s.Identity("example", "build")
	cfg := s.DefaultCampaignConfig64()
	cfg.Jobs = 1
	cfg.Fuzz = s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 10}
	factory := func(uint64) (s.CampaignModel[int, int, int], error) {
		return s.CampaignModel[int, int, int]{Model: g, Metadata: func() (s.Metadata, error) { return s.Identity("example", "other"), nil }}, nil
	}
	run, err := s.AssuranceRunCampaign64(cfg, s.RunLimits{}, assuranceManifest(), metadata, factory, nil, func(s.JobReport64[int]) error { return nil })
	if err != nil || len(run.Assurance.MissingJobs) != 1 || run.Assurance.Report.AssessStrict().Adequate || run.Campaign.Errors != 1 {
		t.Fatalf("identity mismatch hidden: %+v %v", run, err)
	}
}

func TestAssuredGuidedCampaignFreshFeedbackAndCleanup(t *testing.T) {
	g := guidedCounter()
	g.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("state")}, nil }
	g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) { return []s.Check{s.Passed("edge")}, nil }
	metadata := s.Identity("example", "build")
	var feedbackClosed, modelClosed atomic.Int64
	factory := func(uint64) (s.CampaignModel[int, int, int], error) {
		return s.CampaignModel[int, int, int]{Model: g, Metadata: func() (s.Metadata, error) { return metadata, nil }, Close: func() error { modelClosed.Add(1); return nil }}, nil
	}
	feedbackFactory := func(id uint64, _ s.GenerateModel[int, int, int]) (s.CampaignFeedback[int, int, int, int], error) {
		f := counterFeedback()
		f.Metadata = func() (s.FeedbackMetadata, error) {
			return s.FeedbackMetadata{Name: "worker", Version: uint32(id + 1), Build: "build"}, nil
		}
		return s.CampaignFeedback[int, int, int, int]{Feedback: f, Close: func() error { feedbackClosed.Add(1); return nil }}, nil
	}
	coverage := func(uint64, s.GenerateModel[int, int, int]) (func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error), error) {
		return func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
			return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, nil
		}, nil
	}
	cfg := s.DefaultCampaignConfig64()
	cfg.Jobs = 2
	cfg.Workers = 2
	cfg.Fuzz = s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 10}
	delivered := 0
	run, err := s.AssuranceRunGuidedCampaign64(cfg, s.RunLimits{}, assuranceManifest(), metadata, s.DefaultGuidanceConfig64(), factory, feedbackFactory, coverage, func(job s.JobReport64[int]) error {
		delivered++
		if job.Outcome.Report == nil || job.Outcome.Report.Guidance == nil {
			t.Error("missing guidance")
		}
		return nil
	})
	if err != nil || delivered != 2 || feedbackClosed.Load() != 2 || modelClosed.Load() != 2 || !run.Assurance.Report.AssessStrict().Adequate {
		t.Fatalf("guided campaign: %+v %v", run, err)
	}
	if len(run.Assurance.Jobs) != 2 || run.Assurance.Jobs[0].Evidence.Budgets["guidance.feedback.version"] != "1" || run.Assurance.Jobs[1].Evidence.Budgets["guidance.feedback.version"] != "2" {
		t.Fatalf("wrong per-job feedback: %+v", run.Assurance.Jobs)
	}
}
func TestAssuredGuidedCampaignCleanupFailureCannotBeAdequate(t *testing.T) {
	g := guidedCounter()
	g.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("state")}, nil }
	g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) { return []s.Check{s.Passed("edge")}, nil }
	metadata := s.Identity("example", "build")
	cfg := s.DefaultCampaignConfig64()
	cfg.Jobs = 1
	cfg.Fuzz = s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 10}
	factory := func(uint64) (s.CampaignModel[int, int, int], error) {
		return s.CampaignModel[int, int, int]{Model: g, Metadata: func() (s.Metadata, error) { return metadata, nil }}, nil
	}
	feedback := func(uint64, s.GenerateModel[int, int, int]) (s.CampaignFeedback[int, int, int, int], error) {
		return s.CampaignFeedback[int, int, int, int]{Feedback: counterFeedback(), Close: func() error { return errors.New("cleanup") }}, nil
	}
	coverage := func(uint64, s.GenerateModel[int, int, int]) (func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error), error) {
		return func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
			return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, nil
		}, nil
	}
	run, err := s.AssuranceRunGuidedCampaign64(cfg, s.RunLimits{}, assuranceManifest(), metadata, s.DefaultGuidanceConfig64(), factory, feedback, coverage, func(s.JobReport64[int]) error { return nil })
	if err != nil || run.Campaign.Errors != 1 || run.Assurance.Report.AssessStrict().Adequate {
		t.Fatalf("cleanup failure hidden: %+v %v", run, err)
	}
	// Feedback Close runs before the job evidence is finished, so the job
	// evidence agrees with the failed job result instead of claiming success.
	if len(run.Assurance.Jobs) != 1 {
		t.Fatalf("job evidence missing: %+v", run.Assurance)
	}
	job := run.Assurance.Jobs[0].Evidence
	if job.Outcome != "failed" || job.Termination != "ModelError" || !strings.Contains(strings.Join(job.Errors, "\n"), "cleanup") {
		t.Fatalf("job evidence contradicts job result: %+v", job)
	}
}

func TestAssuredCampaignRejectsOversizedLedgerBeforeSideEffects(t *testing.T) {
	cfg := s.DefaultCampaignConfig64()
	cfg.Jobs = 1000000
	cfg.AssuranceMaxJobs = 2
	cfg.Fuzz.Cases = 0
	cfg.Fuzz.MaxSteps = 0
	var cancelled atomic.Bool
	cancelled.Store(true)
	var invoked atomic.Int64
	factory := func(uint64) (s.CampaignModel[int, int, int], error) {
		invoked.Add(1)
		return s.CampaignModel[int, int, int]{}, nil
	}
	feedback := func(uint64, s.GenerateModel[int, int, int]) (s.CampaignFeedback[int, int, int, int], error) {
		invoked.Add(1)
		return s.CampaignFeedback[int, int, int, int]{}, nil
	}
	sink := func(s.JobReport64[int]) error { invoked.Add(1); return nil }
	if _, err := s.AssuranceRunCampaign64(cfg, s.RunLimits{Cancellation: &cancelled}, assuranceManifest(), s.Identity("example", "build"), factory, nil, sink); err == nil {
		t.Fatal("oversized ledger accepted")
	}
	if _, err := s.AssuranceRunGuidedCampaign64(cfg, s.RunLimits{Cancellation: &cancelled}, assuranceManifest(), s.Identity("example", "build"), s.DefaultGuidanceConfig64(), factory, feedback, nil, sink); err == nil {
		t.Fatal("oversized guided ledger accepted")
	}
	if invoked.Load() != 0 {
		t.Fatal("callbacks invoked before admission")
	}
}

func TestAssuredCampaignEvidenceLedgerLimitFailsClosed(t *testing.T) {
	g := guidedCounter()
	g.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("state")}, nil }
	g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) { return []s.Check{s.Passed("edge")}, nil }
	metadata := s.Identity("example", "build")
	cfg := s.DefaultCampaignConfig64()
	cfg.Jobs = 1
	cfg.AssuranceMaxEvidenceBytes = 1
	cfg.Fuzz = s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 10}
	factory := func(uint64) (s.CampaignModel[int, int, int], error) {
		return s.CampaignModel[int, int, int]{Model: g, Metadata: func() (s.Metadata, error) { return metadata, nil }}, nil
	}
	feedback := func(uint64, s.GenerateModel[int, int, int]) (s.CampaignFeedback[int, int, int, int], error) {
		return s.CampaignFeedback[int, int, int, int]{Feedback: counterFeedback()}, nil
	}
	coverage := func(uint64, s.GenerateModel[int, int, int]) (func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error), error) {
		return func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
			return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, nil
		}, nil
	}
	for _, guided := range []bool{false, true} {
		var outcome string
		sink := func(job s.JobReport64[int]) error { outcome = job.Outcome.Kind; return nil }
		var run s.AssuredCampaign
		var err error
		if guided {
			run, err = s.AssuranceRunGuidedCampaign64(cfg, s.RunLimits{}, assuranceManifest(), metadata, s.DefaultGuidanceConfig64(), factory, feedback, coverage, sink)
		} else {
			run, err = s.AssuranceRunCampaign64(cfg, s.RunLimits{}, assuranceManifest(), metadata, factory, coverage, sink)
		}
		if err != nil || outcome != "model_error" || run.Campaign.Errors != 1 || len(run.Assurance.MissingJobs) != 1 || run.Assurance.Report.AssessStrict().Adequate {
			t.Fatalf("guided=%v limit hidden: %+v %v", guided, run, err)
		}
		// The ledger bound is a Go-only extension: it stays out of the Rust
		// budgets map and is carried by the "campaign" JSON extension.
		if _, ok := run.Assurance.Report.Budgets["campaign.assurance_max_evidence_bytes"]; ok {
			t.Fatalf("guided=%v ledger bound leaked into Rust budgets", guided)
		}
		encoded, err := run.Assurance.JSON()
		if err != nil || !strings.Contains(string(encoded), `"assurance_max_evidence_bytes":1`) {
			t.Fatalf("guided=%v ledger bound not exported: %s %v", guided, encoded, err)
		}
	}
}

func TestAssuredCampaignCoverageSetupConsumesDeadline(t *testing.T) {
	g := guidedCounter()
	var initialCalls atomic.Int64
	g.InitialState = func() (int, error) { initialCalls.Add(1); return 0, nil }
	metadata := s.Identity("example", "build")
	cfg := s.DefaultCampaignConfig64()
	cfg.Jobs = 1
	cfg.Fuzz = s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 10}
	factory := func(uint64) (s.CampaignModel[int, int, int], error) {
		return s.CampaignModel[int, int, int]{Model: g, Metadata: func() (s.Metadata, error) { return metadata, nil }}, nil
	}
	feedback := func(uint64, s.GenerateModel[int, int, int]) (s.CampaignFeedback[int, int, int, int], error) {
		return s.CampaignFeedback[int, int, int, int]{Feedback: counterFeedback()}, nil
	}
	coverage := func(uint64, s.GenerateModel[int, int, int]) (func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error), error) {
		time.Sleep(120 * time.Millisecond)
		return nil, nil
	}
	for _, guided := range []bool{false, true} {
		duration := 80 * time.Millisecond
		var run s.AssuredCampaign
		var err error
		if guided {
			run, err = s.AssuranceRunGuidedCampaign64(cfg, s.RunLimits{MaxDuration: &duration}, assuranceManifest(), metadata, s.DefaultGuidanceConfig64(), factory, feedback, coverage, func(s.JobReport64[int]) error { return nil })
		} else {
			run, err = s.AssuranceRunCampaign64(cfg, s.RunLimits{MaxDuration: &duration}, assuranceManifest(), metadata, factory, coverage, func(s.JobReport64[int]) error { return nil })
		}
		if err != nil || run.Campaign.Termination.Kind != "deadline" || initialCalls.Load() != 0 || run.Assurance.Report.AssessStrict().Adequate {
			t.Fatalf("guided=%v deadline ignored: %+v %v", guided, run, err)
		}
	}
}

func TestAssuredCampaignExactDeadlineCoversSetup(t *testing.T) {
	g := guidedCounter()
	var initialCalls atomic.Int64
	g.InitialState = func() (int, error) { initialCalls.Add(1); return 0, nil }
	metadata := s.Identity("example", "build")
	cfg := s.DefaultCampaignConfig64()
	cfg.Jobs = 1
	cfg.Fuzz = s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 10}
	factory := func(uint64) (s.CampaignModel[int, int, int], error) {
		return s.CampaignModel[int, int, int]{Model: g, Metadata: func() (s.Metadata, error) { return metadata, nil }}, nil
	}
	feedback := func(uint64, s.GenerateModel[int, int, int]) (s.CampaignFeedback[int, int, int, int], error) {
		return s.CampaignFeedback[int, int, int, int]{Feedback: counterFeedback()}, nil
	}
	coverage := func(uint64, s.GenerateModel[int, int, int]) (func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error), error) {
		time.Sleep(120 * time.Millisecond)
		return nil, nil
	}
	for _, guided := range []bool{false, true} {
		budget := s.Uint128{Lo: 80_000_000}
		var run s.AssuredCampaign
		var err error
		if guided {
			run, err = s.AssuranceRunGuidedCampaign64(cfg, s.RunLimits{MaxDurationNS: &budget}, assuranceManifest(), metadata, s.DefaultGuidanceConfig64(), factory, feedback, coverage, func(s.JobReport64[int]) error { return nil })
		} else {
			run, err = s.AssuranceRunCampaign64(cfg, s.RunLimits{MaxDurationNS: &budget}, assuranceManifest(), metadata, factory, coverage, func(s.JobReport64[int]) error { return nil })
		}
		if err != nil || run.Campaign.Termination.Kind != "deadline" || initialCalls.Load() != 0 || run.Assurance.Report.Budgets["max_duration_ns"] != "80000000" || len(run.Assurance.Jobs) != 1 || run.Assurance.Jobs[0].Evidence.Budgets["max_duration_ns"] != "0" || run.Assurance.Report.AssessStrict().Adequate {
			t.Fatalf("guided=%v: %+v %v", guided, run, err)
		}
	}
}

func TestAssuredGuidedCampaignRetainsFeedbackMetadataFailure(t *testing.T) {
	g := guidedCounter()
	metadata := s.Identity("example", "build")
	cfg := s.DefaultCampaignConfig64()
	cfg.Jobs = 1
	cfg.Fuzz = s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 10}
	factory := func(uint64) (s.CampaignModel[int, int, int], error) {
		return s.CampaignModel[int, int, int]{Model: g, Metadata: func() (s.Metadata, error) { return metadata, nil }}, nil
	}
	feedback := func(uint64, s.GenerateModel[int, int, int]) (s.CampaignFeedback[int, int, int, int], error) {
		f := counterFeedback()
		f.Metadata = func() (s.FeedbackMetadata, error) {
			return s.FeedbackMetadata{}, errors.New("feedback metadata failed")
		}
		return s.CampaignFeedback[int, int, int, int]{Feedback: f}, nil
	}
	run, err := s.AssuranceRunGuidedCampaign64(cfg, s.RunLimits{}, assuranceManifest(), metadata, s.DefaultGuidanceConfig64(), factory, feedback, nil, func(s.JobReport64[int]) error { return nil })
	if err != nil || run.Campaign.Errors != 1 || len(run.Assurance.Jobs) != 1 || len(run.Assurance.Jobs[0].Evidence.Errors) == 0 || run.Assurance.Report.AssessStrict().Adequate {
		t.Fatalf("failed feedback evidence lost: %+v %v", run, err)
	}
	if _, ok := run.Assurance.Jobs[0].Evidence.Budgets["guidance.feedback.name"]; ok {
		t.Fatal("feedback identity invented")
	}
}

func TestAssuredCampaignRejectsOversizedManifestBeforeFactory(t *testing.T) {
	m := assuranceManifest()
	m.Families = make([]s.AssuranceFamily, s.DefaultAssuranceObservationLimits().MaxFamilies+1)
	cfg := s.DefaultCampaignConfig64()
	cfg.Jobs = 1
	var calls atomic.Int64
	factory := func(uint64) (s.CampaignModel[int, int, int], error) {
		calls.Add(1)
		return s.CampaignModel[int, int, int]{}, nil
	}
	feedback := func(uint64, s.GenerateModel[int, int, int]) (s.CampaignFeedback[int, int, int, int], error) {
		calls.Add(1)
		return s.CampaignFeedback[int, int, int, int]{}, nil
	}
	if _, err := s.AssuranceRunCampaign64(cfg, s.RunLimits{}, m, s.Identity("example", "build"), factory, nil, nil); err == nil {
		t.Fatal("oversized campaign manifest accepted")
	}
	if _, err := s.AssuranceRunGuidedCampaign64(cfg, s.RunLimits{}, m, s.Identity("example", "build"), s.DefaultGuidanceConfig64(), factory, feedback, nil, nil); err == nil {
		t.Fatal("oversized guided campaign manifest accepted")
	}
	if calls.Load() != 0 {
		t.Fatal("factory invoked before admission")
	}
}

func TestAssuredCampaignWrappersHonorExplicitObservationLimits(t *testing.T) {
	m := assuranceManifest()
	m.Provenance.Manifest = strings.Repeat("p", 4097)
	limits := s.DefaultAssuranceObservationLimits()
	limits.MaxTextBytes = 5000
	g := guidedCounter()
	g.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("state")}, nil }
	g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) { return []s.Check{s.Passed("edge")}, nil }
	metadata := s.Identity("example", "build")
	factory := func(uint64) (s.CampaignModel[int, int, int], error) {
		return s.CampaignModel[int, int, int]{Model: g, Metadata: func() (s.Metadata, error) { return metadata, nil }}, nil
	}
	feedback := func(uint64, s.GenerateModel[int, int, int]) (s.CampaignFeedback[int, int, int, int], error) {
		return s.CampaignFeedback[int, int, int, int]{Feedback: counterFeedback()}, nil
	}
	coverage := func(uint64, s.GenerateModel[int, int, int]) (func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error), error) {
		return func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
			return s.AssuranceHits{Families: []string{"request"}, Goals: []string{"seen"}}, nil
		}, nil
	}
	cfg := s.DefaultCampaignConfig64()
	cfg.Jobs = 1
	cfg.Fuzz = s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 10}
	sink := func(s.JobReport64[int]) error { return nil }
	if _, err := s.AssuranceRunCampaign64(cfg, s.RunLimits{}, m, metadata, factory, coverage, sink); err == nil {
		t.Fatal("default campaign admission bypassed")
	}
	plain, err := s.AssuranceRunCampaign64WithObservationLimits(cfg, s.RunLimits{}, m, metadata, factory, coverage, sink, limits)
	if err != nil || !plain.Assurance.Report.AssessStrict().Adequate || plain.Assurance.Jobs[0].Evidence.Budgets["assurance.max_text_bytes"] != "" {
		t.Fatalf("custom campaign: %+v %v", plain, err)
	}
	data, err := plain.Assurance.JSON()
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseMergedAssuranceJSON(data); err == nil {
		t.Fatal("default import admission bypassed")
	}
	parsed, err := s.ParseMergedAssuranceJSONWithAdmission(data, s.DefaultAssuranceJSONLimits(), limits)
	if err != nil || !parsed.Report.AssessStrict().Adequate {
		t.Fatalf("custom campaign import: %v", err)
	}
	guided, err := s.AssuranceRunGuidedCampaign64WithObservationLimits(cfg, s.RunLimits{}, m, metadata, s.DefaultGuidanceConfig64(), factory, feedback, coverage, sink, limits)
	if err != nil || !guided.Assurance.Report.AssessStrict().Adequate {
		t.Fatalf("custom guided campaign: %+v %v", guided, err)
	}
}

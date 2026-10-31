package statelessness_test

import (
	"strconv"
	"strings"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

// These fixtures are hand-built by following Rust src/assurance.rs
// Manifest::to_json, AssuranceReport::to_json and MergedReport::to_json field
// by field (quoted/object/array/string_map, BTreeMap key order, numeric job
// order), fuzz_budgets/control_budgets, the run_campaign catch_unwind branch
// ("Panicked") and run_campaign's "Campaign::{:?}" termination and budgets.
const rustFixtureManifest = `{"assurance_version":1,"model":"m",` +
	`"required_properties":[{"id":"ok","phase":"state"}],` +
	`"input_families":[{"name":"request","coverage":"required"},{"name":"admin","coverage":"excluded","reason":"not \"modeled\""}],` +
	`"bounds":{},"cover_goals":[],"required_search_outcome":"completed",` +
	`"provenance":{"manifest":"p","model":"q","properties":"r"}}`

const rustFixtureMetadata = `{"name":"m","model_version":1,"properties_version":1,"codec_version":1,"build":"b"}`

const rustFixtureJobBudgets = `{"cancellation_enabled":"false","cases":"1","max_steps":"2","max_transitions":"10","mutation_percent":"0","seed":"7"}`

func rustFixtureReport(outcome, termination, budgets string, checked, passed uint64, request bool, errors string) string {
	requestStatus, observed := `"Missing"`, `[]`
	if request {
		requestStatus, observed = `"Observed"`, `["request"]`
	}
	n := func(v uint64) string { return strconv.FormatUint(v, 10) }
	return `{"assurance_version":1,"manifest":` + rustFixtureManifest + `,"metadata":` + rustFixtureMetadata +
		`,"search":{"outcome":"` + outcome + `","termination":` + termination + `,"budgets":` + budgets + `,"checked_transitions":` + n(checked) + `}` +
		`,"properties":[{"property":{"id":"ok","phase":"state"},"status":"Passed","counts":{"passed":` + n(passed) + `,"failed":0,"skipped":0,"missing":0}}]` +
		`,"families":[{"name":"admin","status":"Excluded","reason":"not \"modeled\""},{"name":"request","status":` + requestStatus + `,"reason":null}]` +
		`,"coverage":[]` +
		`,"observed_properties":[{"property":{"id":"ok","phase":"state"},"counts":{"passed":` + n(passed) + `,"failed":0,"skipped":0,"missing":0}}]` +
		`,"observed_families":` + observed + `,"observed_goals":[],"callback_errors":` + errors + `,"unclassified_transitions":0}`
}

func TestAssuranceParsesAndReemitsGenuineRustSingleReport(t *testing.T) {
	rust := rustFixtureReport("CasesCompleted", `"CasesCompleted"`, rustFixtureJobBudgets, 2, 3, true, `[]`)
	e, err := s.ParseAssuranceJSON([]byte(rust))
	if err != nil {
		t.Fatal(err)
	}
	if !e.AssessStrict().Adequate {
		t.Fatalf("Rust fixture not adequate: %+v", e.AssessStrict())
	}
	again, err := e.JSON()
	if err != nil || string(again) != rust {
		t.Fatalf("not byte-identical to Rust writer:\n got %s\nwant %s\n%v", again, rust, err)
	}
}

func TestAssuranceParsesGenuineRustCampaignWithPanickedJob(t *testing.T) {
	job0 := rustFixtureReport("CasesCompleted", `"CasesCompleted"`, rustFixtureJobBudgets, 2, 3, true, `[]`)
	job1 := rustFixtureReport("Failed", `"Panicked"`, rustFixtureJobBudgets, 0, 1, false, `["job panicked; partial observations retained"]`)
	if _, err := s.ParseAssuranceJSON([]byte(job1)); err != nil {
		t.Fatalf("Panicked job rejected: %v", err)
	}
	// Rust: merge_reports budgets, then campaign_budgets (with control_budgets
	// and a u128 max_duration_ns), then started/finished/delivered/accounting.
	budgets := `{"campaign.accounting_complete":"true","campaign.delivered":"1","campaign.finished":"2","campaign.first_job":"0",` +
		`"campaign.master_seed":"7","campaign.result_buffer":"0","campaign.started":"2","campaign.stop_on_failure":"false",` +
		`"campaign.workers":"1","cancellation_enabled":"false","max_duration_ns":"18446744073709551616000000000",` +
		`"reported_jobs":"2","requested_jobs":"2"}`
	termination := `"Campaign::CallbackError(ModelError(\"sink said \\\"no\\\"\\n\"))"`
	errors := `["campaign completion/delivery error: sink said \"no\"\n","job 1: job panicked; partial observations retained","not every requested campaign report was delivered successfully"]`
	report := rustFixtureReport("Failed", termination, budgets, 2, 4, true, errors)
	rust := `{"assurance_version":1,"report":` + report + `,"jobs":{"0":` + job0 + `,"1":` + job1 + `},"missing_jobs":[]}`
	merged, err := s.ParseMergedAssuranceJSON([]byte(rust))
	if err != nil {
		t.Fatal(err)
	}
	if merged.Report.Termination != `Campaign::CallbackError(ModelError("sink said \"no\"`+"\\n"+`"))` || merged.Report.Outcome != "failed" || merged.Report.AssessStrict().Adequate {
		t.Fatalf("campaign facts not reconstructed: %+v", merged.Report)
	}
	again, err := merged.JSON()
	if err != nil || string(again) != rust {
		t.Fatalf("not byte-identical to Rust writer:\n got %s\nwant %s\n%v", again, rust, err)
	}
	// A forged campaign outcome is rejected rather than trusted.
	forged := strings.Replace(rust, `"outcome":"Failed","termination":"Campaign::`, `"outcome":"CasesCompleted","termination":"Campaign::`, 1)
	if _, err := s.ParseMergedAssuranceJSON([]byte(forged)); err == nil {
		t.Fatal("forged campaign outcome accepted")
	}
}

func TestAssuranceExclusionViolatedReasonUsesRustDebugEscaping(t *testing.T) {
	m := s.AssuranceManifest{Version: 1, Model: "m", Properties: []s.AssuranceProperty{{ID: "ok", Phase: "state"}}, Families: []s.AssuranceFamily{{Name: "admin", Excluded: true, Reason: "tab\there \u00e9\u0301 \x1b"}}, RequiredOutcome: "completed", Provenance: s.AssuranceProvenance{Manifest: "p", Model: "q", Properties: "r"}}
	e := s.AssuranceEvidence{Manifest: m, Metadata: s.Metadata{Name: "m", ModelVersion: 1, PropertiesVersion: 1, CodecVersion: 1, Build: "b"}, Outcome: "cases_completed", Termination: "cases_completed", CheckedTransitions: 1, Properties: map[s.AssuranceProperty]s.AssuranceCheckCounts{{ID: "ok", Phase: "state"}: {Passed: 1}}, Families: map[string]bool{"admin": true}, Goals: map[string]bool{}, Budgets: map[string]string{}}
	want := `input family admin: ExclusionViolated { reason: "tab\there é\u{301} \u{1b}" }`
	for _, reason := range e.AssessStrict().Reasons {
		if reason == want {
			return
		}
	}
	t.Fatalf("missing Rust {:?} reason %q in %q", want, e.AssessStrict().Reasons)
}

// Mirrors Rust run_campaign: an unwound job is retained as "Panicked" with the
// fixed message, and the aggregate termination is "Campaign::{:?}".
func TestAssuredCampaignRecordsPanickedJobsAndCampaignTermination(t *testing.T) {
	g := integerSearch(t, "singleton", nil)
	g.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("state")}, nil }
	g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) { return []s.Check{s.Passed("edge")}, nil }
	g.Step = func(int, int) (s.Transition[int, int], error) { panic("boom") }
	metadata := s.Identity("example", "build")
	factory := func(uint64) (s.CampaignModel[int, int, int], error) {
		return s.CampaignModel[int, int, int]{Model: g, Metadata: func() (s.Metadata, error) { return metadata, nil }}, nil
	}
	coverage := func(uint64, s.GenerateModel[int, int, int]) (func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error), error) {
		return func(int, int, s.Transition[int, int], []int) (s.AssuranceHits, error) {
			return s.AssuranceHits{Families: []string{"request"}}, nil
		}, nil
	}
	cfg := s.DefaultCampaignConfig64()
	cfg.Jobs = 1
	cfg.Fuzz = s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 10}
	run, err := s.AssuranceRunCampaign64(cfg, s.RunLimits{}, assuranceManifest(), metadata, factory, coverage, func(s.JobReport64[int]) error { return nil })
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(run.Assurance.Report.Termination, "Campaign::") || run.Assurance.Report.AssessStrict().Adequate {
		t.Fatalf("aggregate termination not Rust-shaped: %q", run.Assurance.Report.Termination)
	}
	if len(run.Assurance.Jobs) != 1 {
		t.Fatalf("panicked job evidence not retained: %+v", run.Assurance)
	}
	job := run.Assurance.Jobs[0].Evidence
	if job.Termination != "Panicked" || job.Outcome != "failed" || !slicesContain(job.Errors, "job panicked; partial observations retained") {
		t.Fatalf("panicked job not Rust-shaped: %+v", job)
	}
	encoded, err := run.Assurance.JSON()
	if err != nil || !strings.Contains(string(encoded), `"termination":"Panicked"`) {
		t.Fatalf("Panicked not exported: %s %v", encoded, err)
	}
	if _, err := s.ParseMergedAssuranceJSON(encoded); err != nil {
		t.Fatalf("Panicked campaign does not round trip: %v", err)
	}
}

func slicesContain(values []string, want string) bool {
	for _, v := range values {
		if v == want {
			return true
		}
	}
	return false
}

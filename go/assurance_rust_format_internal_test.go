package statelessness

import (
	"strings"
	"testing"
)

func TestRustDebugStringMatchesRustStrDebug(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{`plain`, `"plain"`},
		{"q\"b\\", `"q\"b\\"`},
		{"\t\r\n\x00", `"\t\r\n\0"`},
		{"\x1b\x7f", `"\u{1b}\u{7f}"`},
		{"\u00e9\u0301", `"é\u{301}"`},
		{"\u00ad\u200b", `"\u{ad}\u{200b}"`},
		{"'\u00a0 日本", `"'\u{a0} 日本"`},
	} {
		if got := rustDebugString(tc.in); got != tc.want {
			t.Fatalf("rustDebugString(%q) = %s, want %s", tc.in, got, tc.want)
		}
		back, rest, ok := rustDebugStringParse(tc.want + "))")
		if !ok || back != tc.in || rest != "))" {
			t.Fatalf("round trip %s: %q %q %v", tc.want, back, rest, ok)
		}
	}
}

func TestRustCampaignTerminationRoundTrips(t *testing.T) {
	for _, tc := range []CampaignTermination{{Kind: "jobs_completed"}, {Kind: "failure_found"}, {Kind: "cancelled"}, {Kind: "deadline"}, {Kind: "callback_error", Error: &ModelError{"a \"b\"\n"}}, {Kind: "worker_error", Error: &ModelError{"lost"}}} {
		text := "Campaign::" + rustCampaignTermination(tc)
		back, ok := parseRustCampaignTermination(text)
		if !ok || back.Kind != tc.Kind || (tc.Error != nil) != (back.Error != nil) || (tc.Error != nil && back.Error.Error() != tc.Error.Error()) {
			t.Fatalf("%s did not round trip: %+v", text, back)
		}
	}
	if got := rustCampaignTermination(CampaignTermination{Kind: "callback_error", Error: &ModelError{"x"}}); got != `CallbackError(ModelError("x"))` {
		t.Fatalf("wrong Debug spelling %s", got)
	}
	for _, bad := range []string{"JobsCompleted", "Campaign::Other", `Campaign::CallbackError(ModelError("x")`, `Campaign::WorkerError(ModelError("x"))x`} {
		if _, ok := parseRustCampaignTermination(bad); ok {
			t.Fatalf("accepted %s", bad)
		}
	}
}

func TestAssuranceU128DecimalBudget(t *testing.T) {
	for raw, want := range map[string]bool{"0": true, "18446744073709551616000000000": true, "340282366920938463463374607431768211455": true, "340282366920938463463374607431768211456": false, "-1": false, "01": false, "": false, "1e3": false} {
		if assuranceU128Decimal(raw) != want {
			t.Fatalf("assuranceU128Decimal(%q) != %v", raw, want)
		}
	}
}

// The merge proof is sealed at construction: assessing it compares against
// the sealed aggregate instead of re-merging every job, yet tampering with
// the visible aggregate is still detected.
func TestMergedAssuranceValidationUsesSealedAggregate(t *testing.T) {
	manifest := AssuranceManifest{Version: 1, Model: "m", Properties: []AssuranceProperty{{"ok", "state"}}, Families: []AssuranceFamily{{Name: "request"}}, RequiredOutcome: "completed", Provenance: AssuranceProvenance{"p", "q", "r"}}
	metadata := Metadata{"m", 1, 1, 1, "b"}
	job := AssuranceEvidence{Manifest: manifest, Metadata: metadata, Outcome: "cases_completed", Termination: "cases_completed", CheckedTransitions: 1, Properties: map[AssuranceProperty]AssuranceCheckCounts{{"ok", "state"}: {Passed: 2}}, Families: map[string]bool{"request": true}, Goals: map[string]bool{}, Budgets: map[string]string{}}
	merged, err := MergeAssuranceReports(manifest, metadata, []uint64{1}, []AssuranceJob{{1, job}})
	if err != nil {
		t.Fatal(err)
	}
	report := merged.Report
	if report.merged.sealed == nil || !report.AssessStrict().Adequate {
		t.Fatalf("merge not sealed or not adequate: %+v", report.AssessStrict())
	}
	// Removing the job evidence would make a re-merge disagree; the sealed
	// comparison must not depend on it.
	report.merged.jobs = nil
	if !report.AssessStrict().Adequate {
		t.Fatal("assessment re-merged instead of using the sealed aggregate")
	}
	tampered := merged.Report
	tampered.CheckedTransitions++
	if reasons := tampered.AssessStrict().Reasons; len(reasons) == 0 || !strings.HasPrefix(reasons[0], "inconsistent assurance report:") {
		t.Fatalf("tampered aggregate accepted: %v", reasons)
	}
}

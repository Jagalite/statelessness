package reference

import (
	s "github.com/Jagalite/statelessness/go"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestRetainedReferenceWitnessesReplayAgainstMutantsAndFixed(t *testing.T) {
	corpus, err := RetainedRegressionCorpus()
	if err != nil {
		t.Fatal(err)
	}
	cases, err := RegressionCorpus(corpus)
	if err != nil || len(cases) != 9 {
		t.Fatal(len(cases), err)
	}
	if err = CheckRegressions(corpus); err != nil {
		t.Fatal(err)
	}
	if _, err = RegressionCorpus(corpus + "\n[obsolete-publication]\nsubmit\n"); err == nil {
		t.Fatal("duplicate mutation accepted")
	}
	if _, err = RegressionCorpus(strings.Replace(corpus, "complete 1 1 success", "complete 1 1 bogus", 1)); err == nil {
		t.Fatal("invalid outcome accepted")
	}
}
func TestMutationSuitePreservesFreshReplayedBinaryEvidence(t *testing.T) {
	directory := filepath.Join(t.TempDir(), "suite")
	results, err := WriteMutationSuite(directory, s.DefaultSearchConfig())
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 9 {
		t.Fatal(len(results))
	}
	for _, result := range results {
		if result.Status != "killed" {
			t.Fatalf("missing killed evidence: %+v", result)
		}
		for _, label := range []string{"original", "minimized"} {
			path := filepath.Join(directory, result.Mutation+"-"+label+".sttrace")
			file, err := os.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			audit, err := s.ReadAuditTrace(file, s.DefaultBinaryLimits())
			file.Close()
			if err != nil {
				t.Fatal(err)
			}
			trace, _, err := audit.Portable()
			if err != nil {
				t.Fatal(err)
			}
			c := Controller{Bounds: DefaultBounds(), Mutation: result.Mutation}
			if err = exactTarget(c, &trace, mutationTargets[result.Mutation]); err != nil {
				t.Fatal(err)
			}
		}
	}
	if _, err = WriteMutationSuite(directory, s.DefaultSearchConfig()); err == nil {
		t.Fatal("overwrote existing suite")
	}
}

func TestSuiteJSONAndExclusiveTraceExport(t *testing.T) {
	c := Controller{Bounds: DefaultBounds(), Mutation: "missing-cleanup"}
	result := EvaluateMutation(c.Mutation, s.DefaultSearchConfig())
	if result.Status != "killed" {
		t.Fatal(result)
	}
	data, err := SuiteJSON([]MutationResult{result})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(data), `"search":"Some(FailureFound)"`) || !strings.Contains(string(data), `"review_status":"awaiting_independent_review"`) {
		t.Fatal(string(data))
	}
	bad := result
	bad.Minimized = nil
	if _, err = SuiteJSON([]MutationResult{bad}); err == nil {
		t.Fatal("killed result without witness accepted")
	}
	trace, err := s.Record(c.Model(), s.Values(result.Minimized.Inputs), len(result.Minimized.Inputs))
	if err != nil {
		t.Fatal(err)
	}
	audit, err := s.AuditFromPortable(*trace, s.RunConfig{Strategy: "reference-fault-control"})
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "witness.sttrace")
	if err = SaveTrace(path, audit); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	// Windows does not report POSIX permission bits.
	if err != nil || (runtime.GOOS != "windows" && info.Mode().Perm() != 0600) {
		t.Fatal(info, err)
	}
	if err = SaveTrace(path, audit); err == nil {
		t.Fatal("existing witness overwritten")
	}
}

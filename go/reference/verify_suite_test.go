package reference

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

func TestVerifyMutationSuiteReplaysAndRejectsInvalidEvidence(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "suite")
	if _, err := WriteMutationSuite(dir, s.DefaultSearchConfig()); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	files := map[string][]byte{}
	for _, e := range entries {
		files[e.Name()], err = os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			t.Fatal(err)
		}
	}
	assessment, err := VerifyMutationSuite(files, 64<<20)
	if err != nil || !assessment.Adequate || assessment.ReviewStatus != "awaiting_independent_review" {
		t.Fatal(assessment, err)
	}
	if _, err = VerifyMutationSuite(files, 10); err == nil {
		t.Fatal("byte limit ignored")
	}
	name := "missing-cleanup-original.sttrace"
	original := files[name]
	delete(files, name)
	if _, err = VerifyMutationSuite(files, 64<<20); err == nil {
		t.Fatal("missing witness accepted")
	}
	files[name] = original
	bad := append([]byte(nil), original...)
	bad[len(bad)-1] ^= 1
	files[name] = bad
	if _, err = VerifyMutationSuite(files, 64<<20); err == nil {
		t.Fatal("corrupt witness accepted")
	}
	files[name] = original
	report := files["mutations.json"]
	files["mutations.json"] = append([]byte(`{"model":"duplicate",`), report[1:]...)
	if _, err = VerifyMutationSuite(files, 64<<20); err == nil {
		t.Fatal("duplicate key accepted")
	}
	files["mutations.json"] = report
	var root map[string]any
	if err = json.Unmarshal(report, &root); err != nil {
		t.Fatal(err)
	}
	row := root["results"].([]any)[0].(map[string]any)
	mutation := row["mutation"].(string)
	// Search strings must be the Rust Debug spelling every writer emits.
	row["search"] = "failure_found"
	files["mutations.json"], err = json.Marshal(root)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = VerifyMutationSuite(files, 64<<20); err == nil {
		t.Fatal("snake_case search termination accepted")
	}
	row["search"] = "Some(FailureFound)"
	row["status"] = "not_evaluated"
	row["reason"] = "cutoff"
	row["original"] = nil
	row["minimized"] = nil
	delete(files, mutation+"-original.sttrace")
	delete(files, mutation+"-minimized.sttrace")
	// A surviving mutation never ran the kill path, so it cannot claim a
	// complete shrink.
	row["status"] = "survives"
	row["reason"] = nil
	row["search"] = "Some(GraphExhausted)"
	row["shrink_complete"] = true
	files["mutations.json"], err = json.Marshal(root)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = VerifyMutationSuite(files, 64<<20); err == nil {
		t.Fatal("shrink completion accepted on a surviving row")
	}
	// Rust write_mutation_suite downgrades a killed result to not_evaluated
	// when preservation fails and keeps its shrink_complete flag.
	row["status"] = "not_evaluated"
	row["reason"] = "cutoff"
	row["search"] = "Some(FailureFound)"
	row["shrink_complete"] = true
	files["mutations.json"], err = json.Marshal(root)
	if err != nil {
		t.Fatal(err)
	}
	assessment, err = VerifyMutationSuite(files, 64<<20)
	if err != nil || assessment.Adequate || len(assessment.Reasons) != 1 {
		t.Fatal(assessment, err)
	}
}

func TestVerifyMutationSuitePinnedCrossBuildReplay(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "suite")
	if _, err := WriteMutationSuite(dir, s.DefaultSearchConfig()); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	files := map[string][]byte{}
	for _, e := range entries {
		files[e.Name()], err = os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			t.Fatal(err)
		}
	}
	var report map[string]any
	if err = json.Unmarshal(files["mutations.json"], &report); err != nil {
		t.Fatal(err)
	}
	report["build"] = "foreign-controller"
	files["mutations.json"], err = json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	policy := &SuiteBuildPolicy{ReportBuild: "foreign-controller", MutationBuilds: map[string]string{}}
	for _, mutation := range mutationOrder {
		build := "foreign-" + mutation
		policy.MutationBuilds[mutation] = build
		for _, label := range []string{"original", "minimized"} {
			name := mutation + "-" + label + ".sttrace"
			audit, e := s.ReadAuditTrace(bytes.NewReader(files[name]), s.DefaultBinaryLimits())
			if e != nil {
				t.Fatal(e)
			}
			audit.Metadata.Build = build
			var encoded bytes.Buffer
			if e = audit.WriteBinary(&encoded); e != nil {
				t.Fatal(e)
			}
			files[name] = encoded.Bytes()
		}
	}
	if _, err = VerifyMutationSuite(files, 64<<20); err == nil {
		t.Fatal("foreign build accepted by strict default")
	}
	result, err := VerifyMutationSuiteWithBuilds(files, 64<<20, policy)
	if err != nil || !result.Adequate || result.BuildMatches {
		t.Fatal(result, err)
	}
	policy.MutationBuilds[mutationOrder[0]] = "wrong"
	if _, err = VerifyMutationSuiteWithBuilds(files, 64<<20, policy); err == nil {
		t.Fatal("incorrect mutant build accepted")
	}
	delete(policy.MutationBuilds, mutationOrder[0])
	if _, err = VerifyMutationSuiteWithBuilds(files, 64<<20, policy); err == nil {
		t.Fatal("incomplete build policy accepted")
	}
}

func TestMutationSuiteCleansUpFailedWitnessPair(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "suite")
	calls := 0
	results, err := writeMutationSuite(dir, s.DefaultSearchConfig(), func(path string, trace *s.AuditTrace) (bool, error) {
		calls++
		owned, err := saveTraceNew(path, trace)
		if calls == 2 && err == nil {
			return owned, errors.New("injected sync failure")
		}
		return owned, err
	})
	if err != nil {
		t.Fatal(err)
	}
	if results[0].Status != "not_evaluated" {
		t.Fatalf("failed preservation reported as %q", results[0].Status)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	files := map[string][]byte{}
	for _, e := range entries {
		files[e.Name()], err = os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			t.Fatal(err)
		}
	}
	if _, ok := files[results[0].Mutation+"-original.sttrace"]; ok {
		t.Fatal("orphaned original witness")
	}
	if _, ok := files[results[0].Mutation+"-minimized.sttrace"]; ok {
		t.Fatal("orphaned failed witness")
	}
	assessment, err := VerifyMutationSuite(files, 64<<20)
	if err != nil || assessment.Adequate || len(assessment.Reasons) != 1 {
		t.Fatal(assessment, err)
	}
}

func TestSuiteReportPublicationIsExclusiveAndFailureClean(t *testing.T) {
	for _, tc := range []struct {
		name  string
		write func(*os.File, []byte) error
	}{
		{"partial_write", func(f *os.File, b []byte) error {
			if _, err := f.Write(b[:1]); err != nil {
				return err
			}
			return errors.New("injected write failure")
		}},
		{"premature_close", func(f *os.File, b []byte) error {
			if _, err := f.Write(b); err != nil {
				return err
			}
			return f.Close()
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dir := t.TempDir()
			if err := publishSuiteReport(dir, []byte("report"), tc.write); err == nil {
				t.Fatal("failure accepted")
			}
			entries, err := os.ReadDir(dir)
			if err != nil || len(entries) != 0 {
				t.Fatal(entries, err)
			}
		})
	}
	dir := t.TempDir()
	final := filepath.Join(dir, "mutations.json")
	if err := os.WriteFile(final, []byte("existing"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := publishSuiteReport(dir, []byte("new"), func(f *os.File, b []byte) error { _, err := f.Write(b); return err }); err == nil {
		t.Fatal("overwrote report")
	}
	content, err := os.ReadFile(final)
	if err != nil || string(content) != "existing" {
		t.Fatal(string(content), err)
	}
	if _, err := os.Lstat(filepath.Join(dir, ".mutations.json.pending")); !os.IsNotExist(err) {
		t.Fatal("pending report retained", err)
	}
}

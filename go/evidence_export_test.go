package statelessness_test

import (
	"bytes"
	"encoding/json"
	s "github.com/Jagalite/statelessness/go"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func diagnosticArtifact(t *testing.T) s.Artifact {
	t.Helper()
	m, r, _ := evidenceRecorderSetup(t, 10, 1024)
	if e := s.ObserveEvidenceRecorder(r, evidenceTurn(t, m, 1), s.CausalContext{}); e != nil {
		t.Fatal(e)
	}
	trace := r.Snapshot()
	trace.Trace.Metadata = s.Identity("SECRET_MODEL", "SECRET_BUILD")
	trace.Trace.InitialChecks = []s.Check{s.Skipped("allow\"\n", "SECRET_DETAILS")}
	trace.Trace.Steps[0].Checks = []s.Check{s.Passed("SECRET_ID")}
	trace.Trace.InitialState = []byte("SECRET_PAYLOAD")
	trace.Provenance.Run = "SECRET_RUN"
	trace.Provenance.Environment = append(trace.Provenance.Environment, [2]string{"SECRET_LABEL", "SECRET_VALUE"})
	return s.Artifact{V2: trace}
}
func TestDiagnosticExportOmitsSensitiveDataAndHonorsBoundaries(t *testing.T) {
	a := diagnosticArtifact(t)
	p := s.DefaultExportPolicy()
	p.PropertyIDs = []string{"allow\"\n"}
	data, e := s.DiagnosticJSON(a, p)
	if e != nil {
		t.Fatal(e)
	}
	if bytes.Contains(data, []byte("SECRET")) {
		t.Fatal("sensitive data leaked")
	}
	if !bytes.Contains(data, []byte(`"id":"allow\"\u000a"`)) {
		t.Fatalf("wrong escaping: %s", data)
	}
	var parsed map[string]any
	if e = json.Unmarshal(data, &parsed); e != nil {
		t.Fatal(e)
	}
	if parsed["replayable"] != false || parsed["schema"] != "statelessness.diagnostic-export/1" {
		t.Fatal(parsed)
	}
	p.MaxBytes = uint64(len(data))
	exact, e := s.DiagnosticJSON(a, p)
	if e != nil || !bytes.Equal(exact, data) {
		t.Fatal("exact byte budget failed")
	}
	p.MaxBytes--
	if _, e = s.DiagnosticJSON(a, p); e == nil {
		t.Fatal("over byte budget accepted")
	}
	p = s.DefaultExportPolicy()
	p.MaxSteps = 0
	data, e = s.DiagnosticJSON(a, p)
	if e != nil {
		t.Fatal(e)
	}
	if !bytes.Contains(data, []byte(`"truncated":true`)) || bytes.Contains(data, []byte(`"sequence":1`)) || !bytes.Contains(data, []byte(`"sequence":0`)) {
		t.Fatalf("wrong step cap %s", data)
	}
	legacy := s.Artifact{V1: &a.V2.Trace}
	data, e = s.DiagnosticJSON(legacy, s.DefaultExportPolicy())
	if e != nil {
		t.Fatal(e)
	}
	if !bytes.Contains(data, []byte(`"checkpoint_after":null`)) || !bytes.Contains(data, []byte(`"omitted_prefix":null`)) || !bytes.Contains(data, []byte(`"phase":"unknown_legacy"`)) {
		t.Fatalf("invented legacy context %s", data)
	}
	if _, e = s.DiagnosticJSON(s.Artifact{}, p); e == nil {
		t.Fatal("empty artifact accepted")
	}
	if _, e = s.DiagnosticJSON(s.Artifact{V1: &a.V2.Trace, V2: a.V2}, p); e == nil {
		t.Fatal("ambiguous artifact accepted")
	}
}
func TestEvidenceExportsCreateExclusivePrivateFiles(t *testing.T) {
	a := diagnosticArtifact(t)
	dir := t.TempDir()
	path := filepath.Join(dir, "diagnostic.json")
	if e := s.ExportDiagnosticNew(a, path, s.DefaultExportPolicy()); e != nil {
		t.Fatal(e)
	}
	before, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	if runtime.GOOS != "windows" {
		info, e := os.Stat(path)
		if e != nil || info.Mode().Perm()&0077 != 0 {
			t.Fatal("public file permissions")
		}
	}
	if e = s.ExportDiagnosticNew(a, path, s.DefaultExportPolicy()); e == nil {
		t.Fatal("overwrote existing file")
	}
	after, _ := os.ReadFile(path)
	if !bytes.Equal(before, after) {
		t.Fatal("overwrite mutated file")
	}
	blocked := filepath.Join(dir, "over-budget.json")
	policy := s.DefaultExportPolicy()
	policy.MaxBytes = 1
	if e = s.ExportDiagnosticNew(a, blocked, policy); e == nil {
		t.Fatal("over budget exported")
	}
	if _, e = os.Stat(blocked); !os.IsNotExist(e) {
		t.Fatal("created file before bounded preflight")
	}
	m, r, limits := evidenceRecorderSetup(t, 10, 1024)
	if e = s.ObserveEvidenceRecorder(r, evidenceTurn(t, m, 1), s.CausalContext{}); e != nil {
		t.Fatal(e)
	}
	raw := filepath.Join(dir, "raw.trace")
	if e = r.Snapshot().ExportRawNew(raw, limits); e != nil {
		t.Fatal(e)
	}
	data, e := os.ReadFile(raw)
	if e != nil {
		t.Fatal(e)
	}
	parsed, e := s.ParseArtifact(data, limits)
	if e != nil || parsed.V2 == nil {
		t.Fatalf("raw artifact %v", e)
	}
	if e = r.Snapshot().ExportRawNew(raw, limits); e == nil {
		t.Fatal("raw overwrite accepted")
	}
	legacyRaw := filepath.Join(dir, "legacy.trace")
	if e = (s.Artifact{V1: &r.Snapshot().Trace}).ExportRawNew(legacyRaw, limits); e != nil {
		t.Fatal(e)
	}
	legacy, _ := os.ReadFile(legacyRaw)
	if !strings.HasPrefix(string(legacy), "STLESS") {
		t.Fatal("bad legacy export")
	}
}

func TestCausalDiagnosticExportSharedCorpus(t *testing.T) {
	path := os.Getenv("STATELESS_CAUSAL_CORPUS")
	if path == "" {
		path = "../conformance/causal-evidence-v2.json"
	}
	data, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct {
		Cases []struct {
			Case         string
			ArtifactKind string `json:"artifact_kind"`
			Evidence     evidenceWireValue
			Policy       struct {
				MaxSteps    uint64   `json:"max_steps"`
				MaxBytes    uint64   `json:"max_bytes"`
				PropertyIDs []string `json:"property_ids"`
			}
			Expected struct {
				Error *string `json:"error_contains"`
				Text  string  `json:"json_text"`
			}
		} `json:"export_cases"`
	}
	if e = json.Unmarshal(data, &corpus); e != nil {
		t.Fatal(e)
	}
	if len(corpus.Cases) < 11 {
		t.Fatal("missing export cases")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Case, func(t *testing.T) {
			value := evidenceWireConvert(t, c.Evidence)
			artifact := s.Artifact{V2: value}
			if c.ArtifactKind == "v1" {
				artifact = s.Artifact{V1: &value.Trace}
			}
			data, e := s.DiagnosticJSON(artifact, s.ExportPolicy{MaxSteps: c.Policy.MaxSteps, MaxBytes: c.Policy.MaxBytes, PropertyIDs: c.Policy.PropertyIDs})
			if c.Expected.Error != nil {
				if e == nil || !strings.Contains(e.Error(), *c.Expected.Error) {
					t.Fatalf("error %v want %q", e, *c.Expected.Error)
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			if string(data) != c.Expected.Text {
				t.Fatalf("got %s want %s", data, c.Expected.Text)
			}
		})
	}
}

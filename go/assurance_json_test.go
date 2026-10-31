package statelessness_test

import (
	"bytes"
	"encoding/json"
	s "github.com/Jagalite/statelessness/go"
	"strings"
	"testing"
)

func TestAssuranceJSONMatchesReferenceShape(t *testing.T) {
	e := assuranceMergeJob(t)
	e.Budgets = map[string]string{"seed": "18446744073709551615"}
	e.CheckedTransitions = 18446744073709551615
	data, err := e.JSON()
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Version  int `json:"assurance_version"`
		Manifest struct {
			Properties []struct{ ID, Phase string }      `json:"required_properties"`
			Families   []struct{ Name, Coverage string } `json:"input_families"`
			Outcome    string                            `json:"required_search_outcome"`
		} `json:"manifest"`
		Search struct {
			Outcome, Termination string
			Budgets              map[string]string
			Checked              uint64 `json:"checked_transitions"`
		} `json:"search"`
		Properties []struct {
			Status string
			Counts struct {
				Passed uint64 `json:"passed"`
			} `json:"counts"`
		} `json:"properties"`
		Families []struct {
			Status string
			Reason *string
		} `json:"families"`
		Coverage []struct{ Status string } `json:"coverage"`
		Observed []string                  `json:"observed_families"`
	}
	if err = json.Unmarshal(data, &doc); err != nil {
		t.Fatal(err)
	}
	if doc.Version != 1 || doc.Manifest.Outcome != "completed" || doc.Manifest.Properties[0].ID != "state" || doc.Manifest.Families[0].Coverage != "required" || doc.Search.Outcome != "CasesCompleted" || doc.Search.Termination != "CasesCompleted" || doc.Search.Checked != 18446744073709551615 || doc.Search.Budgets["seed"] != "18446744073709551615" || doc.Properties[0].Status != "Passed" || doc.Families[0].Status != "Observed" || doc.Families[0].Reason != nil || doc.Coverage[0].Status != "Observed" || len(doc.Observed) != 1 {
		t.Fatalf("wrong reference shape: %s", data)
	}
	e.Termination = "forged"
	if _, err = e.JSON(); err == nil {
		t.Fatal("inconsistent report serialized")
	}
}
func TestAssuranceMergedJSONRejectsModifiedJobs(t *testing.T) {
	e := assuranceMergeJob(t)
	merged, err := s.MergeAssuranceReports(e.Manifest, e.Metadata, []uint64{1, 2}, []s.AssuranceJob{{ID: 1, Evidence: e}})
	if err != nil {
		t.Fatal(err)
	}
	data, err := merged.JSON()
	if err != nil {
		t.Fatal(err)
	}
	var doc struct {
		Jobs    map[string]json.RawMessage `json:"jobs"`
		Missing []uint64                   `json:"missing_jobs"`
	}
	if err = json.Unmarshal(data, &doc); err != nil || len(doc.Jobs) != 1 || len(doc.Missing) != 1 || doc.Missing[0] != 2 {
		t.Fatalf("wrong merged export: %s %v", data, err)
	}
	merged.Jobs[0].Evidence.Outcome = "graph_exhausted"
	if _, err = merged.JSON(); err == nil {
		t.Fatal("modified job accepted")
	}
}

func TestAssuranceJSONImportRecomputesDerivedEvidence(t *testing.T) {
	e := assuranceMergeJob(t)
	e.CheckedTransitions = 18446744073709551615
	data, err := e.JSON()
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := s.ParseAssuranceJSON(data)
	if err != nil || parsed.CheckedTransitions != e.CheckedTransitions || !parsed.AssessStrict().Adequate {
		t.Fatalf("round trip: %+v %v", parsed, err)
	}
	var doc map[string]any
	if err = json.Unmarshal(data, &doc); err != nil {
		t.Fatal(err)
	}
	doc["properties"].([]any)[0].(map[string]any)["status"] = "Failed"
	tampered, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseAssuranceJSON(tampered); err == nil {
		t.Fatal("forged derived status accepted")
	}
	var exact map[string]json.RawMessage
	if err = json.Unmarshal(data, &exact); err != nil {
		t.Fatal(err)
	}
	exact["extra"] = json.RawMessage(`true`)
	tampered, err = json.Marshal(exact)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseAssuranceJSON(tampered); err == nil {
		t.Fatal("unknown field accepted")
	}
}
func TestAssuranceJSONImportRejectsDuplicateObservationsAndMergedClaims(t *testing.T) {
	e := assuranceMergeJob(t)
	data, err := e.JSON()
	if err != nil {
		t.Fatal(err)
	}
	var doc map[string]json.RawMessage
	if err = json.Unmarshal(data, &doc); err != nil {
		t.Fatal(err)
	}
	doc["observed_families"] = json.RawMessage(`["request","request"]`)
	data, err = json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseAssuranceJSON(data); err == nil {
		t.Fatal("duplicate observed family accepted")
	}
	merged, err := s.MergeAssuranceReports(e.Manifest, e.Metadata, []uint64{1}, []s.AssuranceJob{{ID: 1, Evidence: e}})
	if err != nil {
		t.Fatal(err)
	}
	data, err = merged.Report.JSON()
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseAssuranceJSON(data); err == nil {
		t.Fatal("aggregate without job proof accepted")
	}
}

func TestAssuranceImportRejectsDuplicateJSONKeys(t *testing.T) {
	e := assuranceMergeJob(t)
	data, err := e.JSON()
	if err != nil {
		t.Fatal(err)
	}
	var doc map[string]json.RawMessage
	if err = json.Unmarshal(data, &doc); err != nil {
		t.Fatal(err)
	}
	doc["metadata"] = json.RawMessage(`{"name":"forged","name":"example","model_version":1,"properties_version":1,"codec_version":1,"build":"build"}`)
	data, err = json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseAssuranceJSON(data); err == nil {
		t.Fatal("duplicate nested key accepted")
	}
	data = []byte(`{"assurance_version":1,"assurance_version":1}`)
	if _, err = s.ParseAssuranceJSON(data); err == nil {
		t.Fatal("duplicate top level key accepted")
	}
}
func TestAssuranceMergedJSONImportRebuildsProof(t *testing.T) {
	e := assuranceMergeJob(t)
	merged, err := s.MergeAssuranceReports(e.Manifest, e.Metadata, []uint64{1, 2}, []s.AssuranceJob{{ID: 1, Evidence: e}})
	if err != nil {
		t.Fatal(err)
	}
	data, err := merged.JSON()
	if err != nil {
		t.Fatal(err)
	}
	imported, err := s.ParseMergedAssuranceJSON(data)
	if err != nil || len(imported.Jobs) != 1 || len(imported.MissingJobs) != 1 || imported.Report.Outcome != "interrupted" || imported.Report.AssessStrict().Adequate {
		t.Fatalf("merged import: %+v %v", imported, err)
	}
	var doc map[string]json.RawMessage
	if err = json.Unmarshal(data, &doc); err != nil {
		t.Fatal(err)
	}
	doc["missing_jobs"] = json.RawMessage(`[]`)
	bad, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseMergedAssuranceJSON(bad); err == nil {
		t.Fatal("missing job hidden")
	}
	doc["missing_jobs"] = json.RawMessage(`[2]`)
	doc["jobs"] = json.RawMessage(`{"01":{}}`)
	bad, err = json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseMergedAssuranceJSON(bad); err == nil {
		t.Fatal("noncanonical job ID accepted")
	}
}

func TestAssuranceJSONRejectsInvalidUTF8Evidence(t *testing.T) {
	invalid := string([]byte{0xff})
	m := assuranceManifest()
	m.Model = invalid
	if _, err := m.JSON(); err == nil {
		t.Fatal("invalid manifest identity accepted")
	}
	e := assuranceMergeJob(t)
	e.Metadata.Build = invalid
	if _, err := e.JSON(); err == nil {
		t.Fatal("invalid metadata accepted")
	}
	e = assuranceMergeJob(t)
	e.Errors = []string{invalid}
	if _, err := e.JSON(); err == nil {
		t.Fatal("invalid error text accepted")
	}
	e = assuranceMergeJob(t)
	e.Budgets = map[string]string{"seed": invalid}
	if _, err := e.JSON(); err == nil {
		t.Fatal("invalid budget value accepted")
	}
}

func TestAssuranceImportRejectsUnpairedSurrogates(t *testing.T) {
	for _, raw := range []string{`{"key":"\ud800"}`, `{"key":"\udc00"}`, `{"key":"\ud800\u0041"}`} {
		if _, err := s.ParseAssuranceJSON([]byte(raw)); err == nil {
			t.Fatalf("accepted %s", raw)
		}
	}
	e := assuranceMergeJob(t)
	e.Metadata.Build = "\U0001f600"
	data, err := e.JSON()
	if err != nil {
		t.Fatal(err)
	}
	data = bytes.ReplaceAll(data, []byte("😀"), []byte(`\ud83d\ude00`))
	got, err := s.ParseAssuranceJSON(data)
	if err != nil || got.Metadata.Build != "😀" {
		t.Fatalf("valid surrogate pair rejected: %v", err)
	}
}

func TestAssuranceJSONLimitsBoundAdmission(t *testing.T) {
	e := assuranceMergeJob(t)
	data, err := e.JSON()
	if err != nil {
		t.Fatal(err)
	}
	limits := s.DefaultAssuranceJSONLimits()
	limits.MaxBytes = len(data)
	if _, err = s.ParseAssuranceJSONWithLimits(data, limits); err != nil {
		t.Fatalf("exact byte limit rejected: %v", err)
	}
	limits.MaxBytes--
	if _, err = s.ParseAssuranceJSONWithLimits(data, limits); err == nil {
		t.Fatal("byte limit ignored")
	}
	limits = s.DefaultAssuranceJSONLimits()
	limits.MaxDepth = 1
	if _, err = s.ParseAssuranceJSONWithLimits(data, limits); err == nil {
		t.Fatal("depth limit ignored")
	}
	limits = s.DefaultAssuranceJSONLimits()
	limits.MaxValues = 1
	if _, err = s.ParseAssuranceJSONWithLimits(data, limits); err == nil {
		t.Fatal("value limit ignored")
	}
	limits = s.DefaultAssuranceJSONLimits()
	limits.MaxTextBytes = 2
	if _, err = s.ParseAssuranceJSONWithLimits(data, limits); err == nil {
		t.Fatal("text limit ignored")
	}
	merged, err := s.MergeAssuranceReports(e.Manifest, e.Metadata, []uint64{1}, []s.AssuranceJob{{ID: 1, Evidence: e}})
	if err != nil {
		t.Fatal(err)
	}
	data, err = merged.JSON()
	if err != nil {
		t.Fatal(err)
	}
	limits = s.DefaultAssuranceJSONLimits()
	limits.MaxBytes = len(data)
	if _, err = s.ParseMergedAssuranceJSONWithLimits(data, limits); err != nil {
		t.Fatal(err)
	}
	limits.MaxValues = 10
	if _, err = s.ParseMergedAssuranceJSONWithLimits(data, limits); err == nil {
		t.Fatal("merged value limit ignored")
	}
}

func TestStandaloneAssuranceManifestImport(t *testing.T) {
	manifest := assuranceManifest()
	manifest.RequiredOutcome = "graph_exhausted"
	manifest.Families = append(manifest.Families, s.AssuranceFamily{Name: "excluded", Excluded: true, Reason: "outside model"})
	data, err := manifest.JSON()
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := s.ParseAssuranceManifestJSON(data)
	if err != nil {
		t.Fatal(err)
	}
	if parsed.Model != manifest.Model || parsed.RequiredOutcome != "graph_exhausted" || len(parsed.Properties) != 2 || len(parsed.Families) != 2 || parsed.Families[1].Reason != "outside model" {
		t.Fatalf("manifest changed: %+v", parsed)
	}
	parsed.Properties[0].ID = "changed"
	if manifest.Properties[0].ID == "changed" {
		t.Fatal("import aliases caller")
	}
	limits := s.DefaultAssuranceJSONLimits()
	limits.MaxBytes = len(data) - 1
	if _, err = s.ParseAssuranceManifestJSONWithLimits(data, limits); err == nil {
		t.Fatal("byte limit ignored")
	}
	var doc map[string]json.RawMessage
	if err = json.Unmarshal(data, &doc); err != nil {
		t.Fatal(err)
	}
	doc["unknown"] = json.RawMessage(`true`)
	bad, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseAssuranceManifestJSON(bad); err == nil {
		t.Fatal("unknown manifest field accepted")
	}
	if _, err = s.ParseAssuranceManifestJSON([]byte(`{"model":"x","model":"x"}`)); err == nil {
		t.Fatal("duplicate manifest key accepted")
	}
}

func TestNestedAssuranceMergeJSONRetainsAndValidatesProof(t *testing.T) {
	e := assuranceMergeJob(t)
	inner, err := s.MergeAssuranceReports(e.Manifest, e.Metadata, []uint64{1}, []s.AssuranceJob{{ID: 1, Evidence: e}})
	if err != nil {
		t.Fatal(err)
	}
	outer, err := s.MergeAssuranceReports(e.Manifest, e.Metadata, []uint64{2}, []s.AssuranceJob{{ID: 2, Evidence: inner.Report}})
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := outer.JSON()
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := s.ParseMergedAssuranceJSON(encoded)
	if err != nil || !decoded.Report.AssessStrict().Adequate {
		t.Fatalf("nested proof lost: %v", err)
	}
	mergeLimits := s.DefaultAssuranceMergeLimits()
	mergeLimits.MaxNestedDepth = 1
	if _, err = s.ParseMergedAssuranceJSONWithBounds(encoded, s.DefaultAssuranceJSONLimits(), s.DefaultAssuranceObservationLimits(), mergeLimits); err == nil {
		t.Fatal("nested import ignored merge depth limit")
	}
	mergeLimits.MaxNestedDepth = 2
	if _, err = s.ParseMergedAssuranceJSONWithBounds(encoded, s.DefaultAssuranceJSONLimits(), s.DefaultAssuranceObservationLimits(), mergeLimits); err != nil {
		t.Fatalf("bounded nested import: %v", err)
	}
	mergeLimits.MaxEvidenceBytes = 1
	if _, err = s.ParseMergedAssuranceJSONWithBounds(encoded, s.DefaultAssuranceJSONLimits(), s.DefaultAssuranceObservationLimits(), mergeLimits); err == nil {
		t.Fatal("nested import ignored merge evidence limit")
	}
	var doc map[string]json.RawMessage
	if err = json.Unmarshal(encoded, &doc); err != nil {
		t.Fatal(err)
	}
	delete(doc, "nested_jobs")
	bad, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseMergedAssuranceJSON(bad); err == nil {
		t.Fatal("nested aggregate without proof accepted")
	}
	doc["nested_jobs"] = json.RawMessage(`{"3":{}}`)
	bad, err = json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseMergedAssuranceJSON(bad); err == nil {
		t.Fatal("unmatched nested proof accepted")
	}
}

func TestAssuranceImportsHonorObservationAdmission(t *testing.T) {
	manifest := assuranceManifest()
	manifest.Provenance.Manifest = strings.Repeat("p", 4097)
	data, err := manifest.JSON()
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseAssuranceManifestJSON(data); err == nil {
		t.Fatal("oversized manifest accepted by default")
	}
	admission := s.DefaultAssuranceObservationLimits()
	admission.MaxTextBytes = 5000
	if _, err = s.ParseAssuranceManifestJSONWithAdmission(data, s.DefaultAssuranceJSONLimits(), admission); err != nil {
		t.Fatal(err)
	}
	c, err := s.NewAssuranceCollectorWithLimits[int, int, int](manifest, s.Identity("example", "build"), nil, admission)
	if err != nil {
		t.Fatal(err)
	}
	e := c.Finish("cases_completed", "cases_completed", nil)
	data, err = e.JSON()
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.ParseAssuranceJSON(data); err == nil {
		t.Fatal("oversized report manifest accepted by default")
	}
	if _, err = s.ParseAssuranceJSONWithAdmission(data, s.DefaultAssuranceJSONLimits(), admission); err != nil {
		t.Fatal(err)
	}
	ordinary := assuranceMergeJob(t)
	ordinary.Properties[s.AssuranceProperty{ID: "extra", Phase: "state"}] = s.AssuranceCheckCounts{Passed: 1}
	data, err = ordinary.JSON()
	if err != nil {
		t.Fatal(err)
	}
	admission = s.DefaultAssuranceObservationLimits()
	admission.MaxProperties = 2
	if _, err = s.ParseAssuranceJSONWithAdmission(data, s.DefaultAssuranceJSONLimits(), admission); err == nil {
		t.Fatal("oversized observed property catalog accepted")
	}
}

package statelessness

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"maps"
	"math/big"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

// The writer below mirrors Rust src/assurance.rs quoted/object/array/
// string_map exactly, including field order, so a Go report and a Rust
// report over the same evidence are byte-identical. encoding/json is not used
// because it sorts object keys and applies HTML and U+2028 escaping.
type assuranceField struct{ key, value string }

// assuranceQuoted mirrors Rust quoted(): only '"', '\\', \n, \r, \t and the
// remaining C0 controls (as lowercase \u00xx) are escaped.
func assuranceQuoted(v string) string {
	var b strings.Builder
	b.Grow(len(v) + 2)
	b.WriteByte('"')
	for _, r := range v {
		switch {
		case r == '"':
			b.WriteString(`\"`)
		case r == '\\':
			b.WriteString(`\\`)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case r < 0x20:
			fmt.Fprintf(&b, `\u%04x`, r)
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte('"')
	return b.String()
}
func assuranceObject(fields ...assuranceField) string {
	var b strings.Builder
	b.WriteByte('{')
	for i, f := range fields {
		if i > 0 {
			b.WriteByte(',')
		}
		b.WriteString(assuranceQuoted(f.key))
		b.WriteByte(':')
		b.WriteString(f.value)
	}
	b.WriteByte('}')
	return b.String()
}
func assuranceArray(values []string) string { return "[" + strings.Join(values, ",") + "]" }

// assuranceStringMap mirrors a Rust BTreeMap<String, String>: byte order.
func assuranceStringMap(m map[string]string) string {
	keys := slices.Sorted(maps.Keys(m))
	fields := make([]assuranceField, 0, len(keys))
	for _, k := range keys {
		fields = append(fields, assuranceField{k, assuranceQuoted(m[k])})
	}
	return assuranceObject(fields...)
}
func assuranceUint(v uint64) string { return strconv.FormatUint(v, 10) }
func assurancePropertyJSON(p AssuranceProperty) string {
	return assuranceObject(assuranceField{"id", assuranceQuoted(p.ID)}, assuranceField{"phase", assuranceQuoted(p.Phase)})
}
func assuranceGoalJSON(g AssuranceGoal) string {
	return assuranceObject(assuranceField{"id", assuranceQuoted(g.ID)}, assuranceField{"scope", assuranceQuoted(g.Scope)})
}
func assuranceCountsJSON(c AssuranceCheckCounts) string {
	return assuranceObject(assuranceField{"passed", assuranceUint(c.Passed)}, assuranceField{"failed", assuranceUint(c.Failed)}, assuranceField{"skipped", assuranceUint(c.Skipped)}, assuranceField{"missing", assuranceUint(c.Missing)})
}

// assuranceManifestJSON mirrors Rust Manifest::to_json.
func assuranceManifestJSON(m AssuranceManifest) string {
	properties := make([]string, 0, len(m.Properties))
	for _, p := range m.Properties {
		properties = append(properties, assurancePropertyJSON(p))
	}
	families := make([]string, 0, len(m.Families))
	for _, f := range m.Families {
		fields := []assuranceField{{"name", assuranceQuoted(f.Name)}}
		if f.Excluded {
			fields = append(fields, assuranceField{"coverage", assuranceQuoted("excluded")}, assuranceField{"reason", assuranceQuoted(f.Reason)})
		} else {
			fields = append(fields, assuranceField{"coverage", assuranceQuoted("required")})
		}
		families = append(families, assuranceObject(fields...))
	}
	goals := make([]string, 0, len(m.Goals))
	for _, g := range m.Goals {
		goals = append(goals, assuranceGoalJSON(g))
	}
	outcome := m.RequiredOutcome
	if outcome == "graph_exhausted" {
		outcome = "exhausted_within_declared_bounds"
	}
	return assuranceObject(
		assuranceField{"assurance_version", strconv.FormatUint(uint64(m.Version), 10)},
		assuranceField{"model", assuranceQuoted(m.Model)},
		assuranceField{"required_properties", assuranceArray(properties)},
		assuranceField{"input_families", assuranceArray(families)},
		assuranceField{"bounds", assuranceStringMap(m.Bounds)},
		assuranceField{"cover_goals", assuranceArray(goals)},
		assuranceField{"required_search_outcome", assuranceQuoted(outcome)},
		assuranceField{"provenance", assuranceObject(assuranceField{"manifest", assuranceQuoted(m.Provenance.Manifest)}, assuranceField{"model", assuranceQuoted(m.Provenance.Model)}, assuranceField{"properties", assuranceQuoted(m.Provenance.Properties)})},
	)
}

// JSON emits the Rust assurance manifest schema after validating its catalog.
func (m AssuranceManifest) JSON() ([]byte, error) {
	if err := m.Validate(); err != nil {
		return nil, err
	}
	return []byte(assuranceManifestJSON(m)), nil
}
func assuranceVariant(s string) string {
	parts := strings.Split(s, "_")
	for i, p := range parts {
		if p != "" {
			parts[i] = strings.ToUpper(p[:1]) + p[1:]
		}
	}
	return strings.Join(parts, "")
}

// assuranceTermination returns Rust's {:?} spelling. "ModelError",
// "Panicked", "MergedJobs" and "Campaign::..." are already stored verbatim.
func assuranceTermination(s string) string {
	switch s {
	case "graph_exhausted":
		return "GraphExhausted"
	case "cases_completed":
		return "CasesCompleted"
	case "depth_bound":
		return "DepthBound"
	case "state_limit":
		return "StateLimit"
	case "transition_limit":
		return "TransitionLimit"
	case "estimated_state_byte_limit":
		return "EstimatedStateByteLimit"
	case "cancelled":
		return "Cancelled"
	case "deadline":
		return "Deadline"
	case "failure_found":
		return "FailureFound"
	default:
		return s
	}
}

// rustDebugString mirrors Rust <str as Debug>: '"', '\\', \t, \r, \n and \0
// use short escapes; grapheme-extending marks and non-printable characters
// use \u{hex}. Rust's is_printable excludes Cc, Cf, Cs, Co, Cn, Zl, Zp and
// Zs other than ' ', which is exactly unicode.IsPrint; results may differ only
// for code points whose category differs between the Rust and Go Unicode
// versions.
func rustDebugString(v string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range v {
		switch {
		case r == '"':
			b.WriteString(`\"`)
		case r == '\\':
			b.WriteString(`\\`)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case r == 0:
			b.WriteString(`\0`)
		case unicode.In(r, unicode.Mn, unicode.Me, unicode.Other_Grapheme_Extend) || !unicode.IsPrint(r):
			fmt.Fprintf(&b, `\u{%x}`, r)
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte('"')
	return b.String()
}

// rustDebugStringParse inverts rustDebugString for the escapes it emits and
// returns the remainder after the closing quote.
func rustDebugStringParse(v string) (string, string, bool) {
	if !strings.HasPrefix(v, `"`) {
		return "", "", false
	}
	var b strings.Builder
	for i := 1; i < len(v); {
		c := v[i]
		switch c {
		case '"':
			return b.String(), v[i+1:], true
		case '\\':
			if i+1 >= len(v) {
				return "", "", false
			}
			switch v[i+1] {
			case '"', '\\', '\'':
				b.WriteByte(v[i+1])
			case 'n':
				b.WriteByte('\n')
			case 'r':
				b.WriteByte('\r')
			case 't':
				b.WriteByte('\t')
			case '0':
				b.WriteByte(0)
			case 'u':
				end := strings.IndexByte(v[i:], '}')
				if end < 0 || i+2 >= len(v) || v[i+2] != '{' {
					return "", "", false
				}
				n, err := strconv.ParseUint(v[i+3:i+end], 16, 32)
				if err != nil || !utf8.ValidRune(rune(n)) {
					return "", "", false
				}
				b.WriteRune(rune(n))
				i += end + 1
				continue
			default:
				return "", "", false
			}
			i += 2
		default:
			b.WriteByte(c)
			i++
		}
	}
	return "", "", false
}

// rustCampaignTermination mirrors Rust {:?} of CampaignTermination, used by
// run_campaign as "Campaign::{:?}".
func rustCampaignTermination(t CampaignTermination) string {
	message := ""
	if t.Error != nil {
		message = t.Error.Error()
	}
	switch t.Kind {
	case "jobs_completed":
		return "JobsCompleted"
	case "failure_found":
		return "FailureFound"
	case "cancelled":
		return "Cancelled"
	case "deadline":
		return "Deadline"
	case "callback_error":
		return "CallbackError(ModelError(" + rustDebugString(message) + "))"
	case "worker_error":
		return "WorkerError(ModelError(" + rustDebugString(message) + "))"
	default:
		return assuranceVariant(t.Kind)
	}
}

// parseRustCampaignTermination inverts rustCampaignTermination.
func parseRustCampaignTermination(v string) (CampaignTermination, bool) {
	rest, ok := strings.CutPrefix(v, "Campaign::")
	if !ok {
		return CampaignTermination{}, false
	}
	switch rest {
	case "JobsCompleted":
		return CampaignTermination{Kind: "jobs_completed"}, true
	case "FailureFound":
		return CampaignTermination{Kind: "failure_found"}, true
	case "Cancelled":
		return CampaignTermination{Kind: "cancelled"}, true
	case "Deadline":
		return CampaignTermination{Kind: "deadline"}, true
	}
	for _, variant := range [][2]string{{"CallbackError", "callback_error"}, {"WorkerError", "worker_error"}} {
		if inner, ok := strings.CutPrefix(rest, variant[0]+"(ModelError("); ok {
			message, tail, ok := rustDebugStringParse(inner)
			if ok && tail == "))" {
				return CampaignTermination{Kind: variant[1], Error: &ModelError{message}}, true
			}
		}
	}
	return CampaignTermination{}, false
}

// jsonText mirrors Rust AssuranceReport::to_json field order and spelling.
func (e AssuranceEvidence) jsonText() (string, error) {
	catalog, err := e.Catalog()
	if err != nil {
		return "", err
	}
	properties := make([]string, 0, len(catalog.Properties))
	for _, p := range catalog.Properties {
		properties = append(properties, assuranceObject(assuranceField{"property", assurancePropertyJSON(p.Property)}, assuranceField{"status", assuranceQuoted(assuranceVariant(p.Status))}, assuranceField{"counts", assuranceCountsJSON(p.Counts)}))
	}
	families := make([]string, 0, len(catalog.Families))
	for _, f := range catalog.Families {
		reason := "null"
		if f.Status == "excluded" || f.Status == "exclusion_violated" {
			reason = assuranceQuoted(f.Reason)
		}
		families = append(families, assuranceObject(assuranceField{"name", assuranceQuoted(f.Name)}, assuranceField{"status", assuranceQuoted(assuranceVariant(f.Status))}, assuranceField{"reason", reason}))
	}
	coverage := make([]string, 0, len(catalog.Goals))
	for _, g := range catalog.Goals {
		coverage = append(coverage, assuranceObject(assuranceField{"goal", assuranceGoalJSON(g.Goal)}, assuranceField{"status", assuranceQuoted(assuranceVariant(g.Status))}))
	}
	keys := make([]AssuranceProperty, 0, len(e.Properties))
	for p := range e.Properties {
		keys = append(keys, p)
	}
	slices.SortFunc(keys, func(a, b AssuranceProperty) int {
		if n := strings.Compare(a.ID, b.ID); n != 0 {
			return n
		}
		return strings.Compare(a.Phase, b.Phase)
	})
	observed := make([]string, 0, len(keys))
	for _, p := range keys {
		observed = append(observed, assuranceObject(assuranceField{"property", assurancePropertyJSON(p)}, assuranceField{"counts", assuranceCountsJSON(e.Properties[p])}))
	}
	quotedHits := func(hits map[string]bool) []string {
		names := make([]string, 0, len(hits))
		for n, hit := range hits {
			if hit {
				names = append(names, n)
			}
		}
		slices.Sort(names)
		for i, n := range names {
			names[i] = assuranceQuoted(n)
		}
		return names
	}
	errors := slices.Clone(e.Errors)
	slices.Sort(errors)
	errors = slices.Compact(errors)
	for i, v := range errors {
		errors[i] = assuranceQuoted(v)
	}
	m := e.Metadata
	return assuranceObject(
		assuranceField{"assurance_version", "1"},
		assuranceField{"manifest", assuranceManifestJSON(e.Manifest)},
		assuranceField{"metadata", assuranceObject(assuranceField{"name", assuranceQuoted(m.Name)}, assuranceField{"model_version", assuranceUint(uint64(m.ModelVersion))}, assuranceField{"properties_version", assuranceUint(uint64(m.PropertiesVersion))}, assuranceField{"codec_version", assuranceUint(uint64(m.CodecVersion))}, assuranceField{"build", assuranceQuoted(m.Build)})},
		assuranceField{"search", assuranceObject(assuranceField{"outcome", assuranceQuoted(assuranceVariant(e.Outcome))}, assuranceField{"termination", assuranceQuoted(assuranceTermination(e.Termination))}, assuranceField{"budgets", assuranceStringMap(e.Budgets)}, assuranceField{"checked_transitions", assuranceUint(e.CheckedTransitions)})},
		assuranceField{"properties", assuranceArray(properties)},
		assuranceField{"families", assuranceArray(families)},
		assuranceField{"coverage", assuranceArray(coverage)},
		assuranceField{"observed_properties", assuranceArray(observed)},
		assuranceField{"observed_families", assuranceArray(quotedHits(e.Families))},
		assuranceField{"observed_goals", assuranceArray(quotedHits(e.Goals))},
		assuranceField{"callback_errors", assuranceArray(errors)},
		assuranceField{"unclassified_transitions", assuranceUint(e.UnclassifiedTransitions)},
	), nil
}

// JSON exports only validated evidence; it does not authenticate observations.
func (e AssuranceEvidence) JSON() ([]byte, error) {
	v, err := e.jsonText()
	if err != nil {
		return nil, err
	}
	return []byte(v), nil
}

// JSON rejects divergence between visible job records and the aggregate proof.
// Rust MergedReport::to_json defines assurance_version, report, jobs (in
// numeric job order) and missing_jobs. The trailing "nested_jobs" and
// "campaign" keys are Go-only extensions (see go/README.md) carrying the
// proofs needed to re-verify nested merges and coordinator counts; they are
// omitted when absent, so plain merges and imported Rust campaign reports are
// emitted in exactly Rust's shape.
func (m MergedAssurance) JSON() ([]byte, error) {
	if m.Report.merged == nil || !reflect.DeepEqual(m.Jobs, m.Report.merged.jobs) {
		return nil, &ModelError{"inconsistent merged assurance jobs"}
	}
	expected := map[uint64]bool{}
	for _, id := range m.Report.merged.expected {
		expected[id] = true
	}
	for _, j := range m.Jobs {
		delete(expected, j.ID)
	}
	missing := make([]uint64, 0, len(expected))
	for id := range expected {
		missing = append(missing, id)
	}
	slices.Sort(missing)
	if !slices.Equal(missing, m.MissingJobs) {
		return nil, &ModelError{"inconsistent missing assurance jobs"}
	}
	report, err := m.Report.jsonText()
	if err != nil {
		return nil, err
	}
	jobs := make([]assuranceField, 0, len(m.Jobs))
	var nested []assuranceField
	for _, j := range m.Jobs {
		v, err := j.Evidence.jsonText()
		if err != nil {
			return nil, err
		}
		key := strconv.FormatUint(j.ID, 10)
		jobs = append(jobs, assuranceField{key, v})
		if proof := j.Evidence.merged; proof != nil {
			child := MergedAssurance{Report: j.Evidence, Jobs: proof.jobs}
			reported := map[uint64]bool{}
			for _, job := range proof.jobs {
				reported[job.ID] = true
			}
			for _, id := range proof.expected {
				if !reported[id] {
					child.MissingJobs = append(child.MissingJobs, id)
				}
			}
			encoded, err := child.JSON()
			if err != nil {
				return nil, err
			}
			nested = append(nested, assuranceField{key, string(encoded)})
		}
	}
	missingText := make([]string, 0, len(missing))
	for _, id := range missing {
		missingText = append(missingText, assuranceUint(id))
	}
	fields := []assuranceField{{"assurance_version", "1"}, {"report", report}, {"jobs", assuranceObject(jobs...)}, {"missing_jobs", assuranceArray(missingText)}}
	if len(nested) > 0 {
		fields = append(fields, assuranceField{"nested_jobs", assuranceObject(nested...)})
	}
	if proof := m.Report.merged; proof.campaign != nil && !proof.fromReport {
		encoded, err := json.Marshal(assuranceCampaignJSON(*proof.campaign, proof.campaignBudgets, proof.maxEvidenceBytes))
		if err != nil {
			return nil, err
		}
		fields = append(fields, assuranceField{"campaign", string(encoded)})
	}
	return []byte(assuranceObject(fields...)), nil
}

type assuranceJSONProperty struct {
	ID    string `json:"id"`
	Phase string `json:"phase"`
}
type assuranceJSONCounts struct {
	Passed  uint64 `json:"passed"`
	Failed  uint64 `json:"failed"`
	Skipped uint64 `json:"skipped"`
	Missing uint64 `json:"missing"`
}
type assuranceJSONDocument struct {
	Version  uint32 `json:"assurance_version"`
	Manifest struct {
		Version    uint32                  `json:"assurance_version"`
		Model      string                  `json:"model"`
		Properties []assuranceJSONProperty `json:"required_properties"`
		Families   []struct {
			Name     string `json:"name"`
			Coverage string `json:"coverage"`
			Reason   string `json:"reason"`
		} `json:"input_families"`
		Bounds map[string]string `json:"bounds"`
		Goals  []struct {
			ID    string `json:"id"`
			Scope string `json:"scope"`
		} `json:"cover_goals"`
		Outcome    string `json:"required_search_outcome"`
		Provenance struct {
			Manifest   string `json:"manifest"`
			Model      string `json:"model"`
			Properties string `json:"properties"`
		} `json:"provenance"`
	} `json:"manifest"`
	Metadata Metadata `json:"metadata"`
	Search   struct {
		Outcome     string            `json:"outcome"`
		Termination string            `json:"termination"`
		Budgets     map[string]string `json:"budgets"`
		Checked     uint64            `json:"checked_transitions"`
	} `json:"search"`
	ObservedProperties []struct {
		Property assuranceJSONProperty `json:"property"`
		Counts   assuranceJSONCounts   `json:"counts"`
	} `json:"observed_properties"`
	ObservedFamilies []string `json:"observed_families"`
	ObservedGoals    []string `json:"observed_goals"`
	Errors           []string `json:"callback_errors"`
	Unclassified     uint64   `json:"unclassified_transitions"`
}

func assuranceReadOutcome(s string) string {
	switch s {
	case "GraphExhausted":
		return "graph_exhausted"
	case "CasesCompleted":
		return "cases_completed"
	case "DepthLimited":
		return "depth_limited"
	case "BudgetLimited":
		return "budget_limited"
	case "Interrupted":
		return "interrupted"
	case "TimedOut":
		return "timed_out"
	case "Failed":
		return "failed"
	case "NotStarted":
		return "not_started"
	default:
		return ""
	}
}
func assuranceReadTermination(s string) string {
	switch s {
	case "GraphExhausted":
		return "graph_exhausted"
	case "CasesCompleted":
		return "cases_completed"
	case "DepthBound":
		return "depth_bound"
	case "StateLimit":
		return "state_limit"
	case "TransitionLimit":
		return "transition_limit"
	case "EstimatedStateByteLimit":
		return "estimated_state_byte_limit"
	case "Cancelled":
		return "cancelled"
	case "Deadline":
		return "deadline"
	case "FailureFound":
		return "failure_found"
	case "ModelError", "Panicked":
		// Rust finish_fuzz and run_campaign's catch_unwind branch.
		return s
	default:
		return ""
	}
}

// ParseAssuranceJSON imports individual Rust-format reports. Catalog rows are
// recomputed and must match the document exactly; observations are not attested.
func ParseAssuranceJSON(data []byte) (AssuranceEvidence, error) {
	return ParseAssuranceJSONWithLimits(data, DefaultAssuranceJSONLimits())
}
func ParseAssuranceJSONWithLimits(data []byte, limits AssuranceJSONLimits) (AssuranceEvidence, error) {
	return ParseAssuranceJSONWithAdmission(data, limits, DefaultAssuranceObservationLimits())
}

// ParseAssuranceJSONWithAdmission selects JSON and retained-evidence limits.
func ParseAssuranceJSONWithAdmission(data []byte, limits AssuranceJSONLimits, admission AssuranceObservationLimits) (AssuranceEvidence, error) {
	var empty AssuranceEvidence
	if err := validateAssuranceJSON(data, limits); err != nil {
		return empty, err
	}
	var doc assuranceJSONDocument
	if err := json.Unmarshal(data, &doc); err != nil {
		return empty, err
	}
	if admission.MaxProperties <= 0 || admission.MaxFamilies <= 0 || admission.MaxGoals <= 0 || admission.MaxBounds <= 0 || admission.MaxErrors <= 0 || admission.MaxTextBytes <= 0 || len(doc.Manifest.Properties) > admission.MaxProperties || len(doc.Manifest.Families) > admission.MaxFamilies || len(doc.Manifest.Goals) > admission.MaxGoals || len(doc.Manifest.Bounds) > admission.MaxBounds || len(doc.ObservedProperties) > admission.MaxProperties || len(doc.ObservedFamilies) > admission.MaxFamilies || len(doc.ObservedGoals) > admission.MaxGoals || (len(doc.Errors) > admission.MaxErrors && len(doc.Errors)-admission.MaxErrors > 2) {
		return empty, &ConfigError{"assurance import observation limit exceeded"}
	}
	m := AssuranceManifest{Version: doc.Manifest.Version, Model: doc.Manifest.Model, Bounds: doc.Manifest.Bounds, RequiredOutcome: doc.Manifest.Outcome, Provenance: AssuranceProvenance{doc.Manifest.Provenance.Manifest, doc.Manifest.Provenance.Model, doc.Manifest.Provenance.Properties}}
	if m.RequiredOutcome == "exhausted_within_declared_bounds" {
		m.RequiredOutcome = "graph_exhausted"
	}
	for _, p := range doc.Manifest.Properties {
		m.Properties = append(m.Properties, AssuranceProperty{p.ID, p.Phase})
	}
	for _, f := range doc.Manifest.Families {
		if f.Coverage != "required" && f.Coverage != "excluded" {
			return empty, &ModelError{"invalid family coverage"}
		}
		m.Families = append(m.Families, AssuranceFamily{Name: f.Name, Excluded: f.Coverage == "excluded", Reason: f.Reason})
	}
	for _, g := range doc.Manifest.Goals {
		m.Goals = append(m.Goals, AssuranceGoal{g.ID, g.Scope})
	}
	if err := validateAssuranceAdmission(m, doc.Metadata, admission); err != nil {
		return empty, err
	}
	for _, p := range doc.ObservedProperties {
		if len(p.Property.ID) > admission.MaxTextBytes {
			return empty, &ConfigError{"assurance observed property text limit exceeded"}
		}
	}
	for _, v := range doc.ObservedFamilies {
		if len(v) > admission.MaxTextBytes {
			return empty, &ConfigError{"assurance observed family text limit exceeded"}
		}
	}
	for _, v := range doc.ObservedGoals {
		if len(v) > admission.MaxTextBytes {
			return empty, &ConfigError{"assurance observed goal text limit exceeded"}
		}
	}
	for _, v := range doc.Errors {
		if len(v) > admission.MaxTextBytes && v != "assurance observation limit exceeded" && v != "assurance collector concurrent or reentrant observation" {
			return empty, &ConfigError{"assurance error text limit exceeded"}
		}
	}
	e := AssuranceEvidence{Manifest: m, Metadata: doc.Metadata, Outcome: assuranceReadOutcome(doc.Search.Outcome), Termination: assuranceReadTermination(doc.Search.Termination), Budgets: doc.Search.Budgets, CheckedTransitions: doc.Search.Checked, UnclassifiedTransitions: doc.Unclassified, Properties: map[AssuranceProperty]AssuranceCheckCounts{}, Families: map[string]bool{}, Goals: map[string]bool{}, Errors: doc.Errors}
	for _, item := range doc.ObservedProperties {
		p := AssuranceProperty{item.Property.ID, item.Property.Phase}
		if p.Phase != "state" && p.Phase != "transition" {
			return empty, &ModelError{"invalid observed property phase"}
		}
		if _, ok := e.Properties[p]; ok {
			return empty, &ModelError{"duplicate observed property"}
		}
		c := item.Counts
		e.Properties[p] = AssuranceCheckCounts{c.Passed, c.Failed, c.Skipped, c.Missing}
	}
	for _, name := range doc.ObservedFamilies {
		if e.Families[name] {
			return empty, &ModelError{"duplicate observed family"}
		}
		e.Families[name] = true
	}
	for _, id := range doc.ObservedGoals {
		if e.Goals[id] {
			return empty, &ModelError{"duplicate observed goal"}
		}
		e.Goals[id] = true
	}
	if doc.Version != 1 || e.Outcome == "" || e.Termination == "" {
		return empty, &ModelError{"unsupported assurance report version or termination"}
	}
	canonical, err := e.JSON()
	if err != nil {
		return empty, err
	}
	var original, derived any
	decode := func(in []byte, out *any) error {
		decoder := json.NewDecoder(strings.NewReader(string(in)))
		decoder.UseNumber()
		return decoder.Decode(out)
	}
	if err = decode(data, &original); err != nil {
		return empty, err
	}
	if err = decode(canonical, &derived); err != nil {
		return empty, err
	}
	if !reflect.DeepEqual(original, derived) {
		return empty, &ModelError{"assurance report fields do not match derived evidence"}
	}
	return cloneAssuranceEvidence(e), nil
}

// validateAssuranceJSON checks every object level; standard json.Unmarshal
// otherwise silently accepts the last value for a repeated object key.
// AssuranceJSONLimits constrain input decoding, not memory already owned by a caller.
type AssuranceJSONLimits struct{ MaxBytes, MaxDepth, MaxValues, MaxTextBytes int }

func DefaultAssuranceJSONLimits() AssuranceJSONLimits {
	return AssuranceJSONLimits{MaxBytes: 16 << 20, MaxDepth: 64, MaxValues: 1000000, MaxTextBytes: 1 << 20}
}
func validateAssuranceJSON(data []byte, limits AssuranceJSONLimits) error {
	if limits.MaxBytes <= 0 || limits.MaxDepth <= 0 || limits.MaxValues <= 0 || limits.MaxTextBytes <= 0 {
		return &ConfigError{"invalid assurance JSON limits"}
	}
	if len(data) > limits.MaxBytes {
		return &ModelError{"assurance JSON byte limit"}
	}
	if !utf8.Valid(data) {
		return &ModelError{"assurance JSON is not valid UTF-8"}
	}
	// encoding/json replaces unpaired surrogate escapes; reject them before
	// decoding so identity and evidence text cannot silently change.
	inString := false
	for i := 0; i < len(data); i++ {
		if !inString {
			if data[i] == '"' {
				inString = true
			}
			continue
		}
		if data[i] == '"' {
			inString = false
			continue
		}
		if data[i] != '\\' {
			continue
		}
		i++
		if i >= len(data) {
			break
		}
		if data[i] != 'u' {
			continue
		}
		if i+4 >= len(data) {
			break
		}
		value, err := strconv.ParseUint(string(data[i+1:i+5]), 16, 16)
		if err != nil {
			break
		}
		i += 4
		if value >= 0xdc00 && value <= 0xdfff {
			return &ModelError{"unpaired assurance JSON surrogate"}
		}
		if value >= 0xd800 && value <= 0xdbff {
			if i+6 >= len(data) || data[i+1] != '\\' || data[i+2] != 'u' {
				return &ModelError{"unpaired assurance JSON surrogate"}
			}
			low, err := strconv.ParseUint(string(data[i+3:i+7]), 16, 16)
			if err != nil || low < 0xdc00 || low > 0xdfff {
				return &ModelError{"unpaired assurance JSON surrogate"}
			}
			i += 6
		}
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	values := 0
	charge := func() error {
		if values >= limits.MaxValues {
			return &ModelError{"assurance JSON value limit"}
		}
		values++
		return nil
	}
	var read func(int) error
	read = func(depth int) error {
		if err := charge(); err != nil {
			return err
		}
		token, err := dec.Token()
		if err != nil {
			return err
		}
		delim, ok := token.(json.Delim)
		if !ok {
			if text, ok := token.(string); ok && len(text) > limits.MaxTextBytes {
				return &ModelError{"assurance JSON text limit"}
			}
			return nil
		}
		if depth >= limits.MaxDepth {
			return &ModelError{"assurance JSON depth limit"}
		}
		switch delim {
		case '{':
			keys := map[string]bool{}
			for dec.More() {
				if err := charge(); err != nil {
					return err
				}
				token, err := dec.Token()
				if err != nil {
					return err
				}
				key, ok := token.(string)
				if !ok {
					return &ModelError{"invalid assurance JSON object key"}
				}
				if len(key) > limits.MaxTextBytes {
					return &ModelError{"assurance JSON text limit"}
				}
				if keys[key] {
					return &ModelError{"duplicate assurance JSON object key: " + key}
				}
				keys[key] = true
				if err = read(depth + 1); err != nil {
					return err
				}
			}
			end, err := dec.Token()
			if err != nil {
				return err
			}
			if end != json.Delim('}') {
				return &ModelError{"invalid assurance JSON object"}
			}
		case '[':
			for dec.More() {
				if err := read(depth + 1); err != nil {
					return err
				}
			}
			end, err := dec.Token()
			if err != nil {
				return err
			}
			if end != json.Delim(']') {
				return &ModelError{"invalid assurance JSON array"}
			}
		default:
			return &ModelError{"invalid assurance JSON value"}
		}
		return nil
	}
	if err := read(0); err != nil {
		return err
	}
	if _, err := dec.Token(); err != io.EOF {
		if err == nil {
			return &ModelError{"trailing assurance JSON value"}
		}
		return err
	}
	return nil
}

// ParseMergedAssuranceJSON reconstructs and validates every individual job and
// recomputes the complete merged result. Campaign claims are checked against
// the Go-only "campaign" coordinator extension when present. Genuine Rust
// run_campaign output has no extension: its termination ("Campaign::{:?}")
// and the campaign.* budgets Rust writes are then the only coordinator facts,
// and the report must equal what Rust derives from them and the jobs. Counts
// Rust does not record (failures, reported totals) are not cross-checked.
func ParseMergedAssuranceJSON(data []byte) (MergedAssurance, error) {
	return ParseMergedAssuranceJSONWithLimits(data, DefaultAssuranceJSONLimits())
}
func ParseMergedAssuranceJSONWithLimits(data []byte, limits AssuranceJSONLimits) (MergedAssurance, error) {
	return ParseMergedAssuranceJSONWithAdmission(data, limits, DefaultAssuranceObservationLimits())
}

// ParseMergedAssuranceJSONWithAdmission selects both JSON and catalog limits.
func ParseMergedAssuranceJSONWithAdmission(data []byte, limits AssuranceJSONLimits, admission AssuranceObservationLimits) (MergedAssurance, error) {
	return ParseMergedAssuranceJSONWithBounds(data, limits, admission, DefaultAssuranceMergeLimits())
}

// ParseMergedAssuranceJSONWithBounds selects JSON, catalog, and merge bounds.
func ParseMergedAssuranceJSONWithBounds(data []byte, limits AssuranceJSONLimits, admission AssuranceObservationLimits, mergeLimits AssuranceMergeLimits) (MergedAssurance, error) {
	var empty MergedAssurance
	if err := validateAssuranceJSON(data, limits); err != nil {
		return empty, err
	}
	var doc struct {
		Version  uint32                     `json:"assurance_version"`
		Report   json.RawMessage            `json:"report"`
		Jobs     map[string]json.RawMessage `json:"jobs"`
		Missing  []uint64                   `json:"missing_jobs"`
		Campaign *assuranceCampaignDocument `json:"campaign"`
		Nested   map[string]json.RawMessage `json:"nested_jobs"`
	}
	if err := json.Unmarshal(data, &doc); err != nil {
		return empty, err
	}
	if doc.Version != 1 {
		return empty, &ModelError{"unsupported merged assurance version"}
	}
	if mergeLimits.MaxJobs == 0 || mergeLimits.MaxEvidenceBytes == 0 || mergeLimits.MaxNestedDepth == 0 {
		return empty, &ConfigError{"invalid assurance merge limits"}
	}
	if uint64(len(doc.Jobs)) > mergeLimits.MaxJobs || uint64(len(doc.Missing)) > mergeLimits.MaxJobs-uint64(len(doc.Jobs)) {
		return empty, &ConfigError{"assurance merge job limit exceeded"}
	}
	jobs := make([]AssuranceJob, 0, len(doc.Jobs))
	expected := slices.Clone(doc.Missing)
	for key, raw := range doc.Jobs {
		id, err := strconv.ParseUint(key, 10, 64)
		if err != nil || strconv.FormatUint(id, 10) != key {
			return empty, &ModelError{"invalid merged assurance job ID"}
		}
		var e AssuranceEvidence
		if nested, ok := doc.Nested[key]; ok {
			child, err := ParseMergedAssuranceJSONWithBounds(nested, limits, admission, mergeLimits)
			if err != nil {
				return empty, fmt.Errorf("nested job %s: %w", key, err)
			}
			canonical, err := child.Report.JSON()
			if err != nil {
				return empty, err
			}
			var actual, expected any
			decode := func(input []byte, out *any) error {
				decoder := json.NewDecoder(bytes.NewReader(input))
				decoder.UseNumber()
				return decoder.Decode(out)
			}
			if err = decode(raw, &actual); err != nil {
				return empty, err
			}
			if err = decode(canonical, &expected); err != nil {
				return empty, err
			}
			if !reflect.DeepEqual(actual, expected) {
				return empty, &ModelError{"nested job report does not match proof"}
			}
			e = child.Report
		} else {
			var err error
			e, err = ParseAssuranceJSONWithAdmission(raw, limits, admission)
			if err != nil {
				return empty, fmt.Errorf("job %s: %w", key, err)
			}
		}
		jobs = append(jobs, AssuranceJob{id, e})
		expected = append(expected, id)
	}
	for key := range doc.Nested {
		if _, ok := doc.Jobs[key]; !ok {
			return empty, &ModelError{"nested proof has no corresponding job"}
		}
	}
	// The report carries the manifest/metadata even when no jobs were reported.
	var header struct {
		Manifest json.RawMessage `json:"manifest"`
		Metadata Metadata        `json:"metadata"`
	}
	if err := json.Unmarshal(doc.Report, &header); err != nil {
		return empty, err
	}
	var rawManifest assuranceJSONDocument
	synthetic := []byte(`{"manifest":` + string(header.Manifest) + `}`)
	if err := json.Unmarshal(synthetic, &rawManifest); err != nil {
		return empty, err
	}
	source := rawManifest.Manifest
	manifest := AssuranceManifest{Version: source.Version, Model: source.Model, Bounds: source.Bounds, RequiredOutcome: source.Outcome, Provenance: AssuranceProvenance{source.Provenance.Manifest, source.Provenance.Model, source.Provenance.Properties}}
	if manifest.RequiredOutcome == "exhausted_within_declared_bounds" {
		manifest.RequiredOutcome = "graph_exhausted"
	}
	for _, p := range source.Properties {
		manifest.Properties = append(manifest.Properties, AssuranceProperty{p.ID, p.Phase})
	}
	for _, f := range source.Families {
		if f.Coverage != "required" && f.Coverage != "excluded" {
			return empty, &ModelError{"invalid family coverage"}
		}
		manifest.Families = append(manifest.Families, AssuranceFamily{Name: f.Name, Excluded: f.Coverage == "excluded", Reason: f.Reason})
	}
	for _, g := range source.Goals {
		manifest.Goals = append(manifest.Goals, AssuranceGoal{g.ID, g.Scope})
	}
	merged, err := MergeAssuranceReportsWithBounds(manifest, header.Metadata, expected, jobs, admission, mergeLimits)
	if err != nil {
		return empty, err
	}
	fromReport := false
	if doc.Campaign == nil {
		// Genuine Rust run_campaign output has no coordinator extension; its
		// coordinator facts live in the report termination and budgets.
		var report struct {
			Search struct {
				Termination string            `json:"termination"`
				Budgets     map[string]string `json:"budgets"`
			} `json:"search"`
		}
		if err := json.Unmarshal(doc.Report, &report); err != nil {
			return empty, err
		}
		if strings.HasPrefix(report.Search.Termination, "Campaign::") {
			d, err := assuranceCampaignFromReport(report.Search.Termination, report.Search.Budgets, uint64(len(expected)))
			if err != nil {
				return empty, err
			}
			doc.Campaign, fromReport = &d, true
		}
	}
	if doc.Campaign != nil {
		campaign, budgets, err := doc.Campaign.validate(expected, uint64(len(jobs)), fromReport)
		if err != nil {
			return empty, err
		}
		// When every job finished without job errors, retained evidence covers
		// all reports and must agree with the coordinator's failure and checked
		// transition totals. In incomplete campaigns, retained evidence can
		// precede cleanup or a lost worker, so it is not a received report.
		var failures, transitions uint64
		for _, job := range jobs {
			if job.Evidence.Termination == "failure_found" {
				failures++
			}
			transitions = assuranceAdd(transitions, job.Evidence.CheckedTransitions)
		}
		if !fromReport && campaign.AccountingComplete && campaign.Errors == 0 && uint64(len(jobs)) == campaign.Finished && campaign.Started == campaign.Finished {
			if failures != campaign.Failures || transitions != campaign.ReportedTransitions {
				return empty, &ModelError{"campaign totals contradict job evidence"}
			}
		}
		applyAssuranceCampaign(&merged.Report, campaign, budgets)
		merged.Report.merged.campaign = &campaign
		merged.Report.merged.campaignBudgets = budgets
		merged.Report.merged.maxEvidenceBytes = doc.Campaign.AssuranceMaxEvidenceBytes
		merged.Report.merged.fromReport = fromReport
		sealAssuranceMerge(&merged.Report)
	}
	canonical, err := merged.JSON()
	if err != nil {
		return empty, err
	}
	var original, derived any
	decode := func(raw []byte, out *any) error {
		dec := json.NewDecoder(bytes.NewReader(raw))
		dec.UseNumber()
		return dec.Decode(out)
	}
	if err = decode(data, &original); err != nil {
		return empty, err
	}
	if err = decode(canonical, &derived); err != nil {
		return empty, err
	}
	if !reflect.DeepEqual(original, derived) {
		return empty, &ModelError{"merged assurance report does not match job evidence"}
	}
	return merged, nil
}

type assuranceCampaignDocument struct {
	Requested             uint64            `json:"requested"`
	Started               uint64            `json:"started"`
	Finished              uint64            `json:"finished"`
	NotStarted            uint64            `json:"not_started"`
	Delivered             uint64            `json:"delivered"`
	Failures              uint64            `json:"failures"`
	Errors                uint64            `json:"errors"`
	ReportedTransitions   uint64            `json:"reported_transitions"`
	ReportedCases         uint64            `json:"reported_cases"`
	ReportedSkippedChecks uint64            `json:"reported_skipped_checks"`
	AccountingComplete    bool              `json:"accounting_complete"`
	Termination           string            `json:"termination"`
	Error                 *string           `json:"error"`
	Budgets               map[string]string `json:"budgets"`
	// Go-only ledger bound; never part of the Rust-defined search budgets.
	AssuranceMaxEvidenceBytes uint64 `json:"assurance_max_evidence_bytes"`
}

func assuranceCampaignJSON(c CampaignReport64, budgets map[string]string, maxEvidenceBytes uint64) assuranceCampaignDocument {
	var message *string
	if c.Termination.Error != nil {
		v := c.Termination.Error.Error()
		message = &v
	}
	owned := map[string]string{}
	for k, v := range budgets {
		owned[k] = v
	}
	return assuranceCampaignDocument{Requested: c.Requested, Started: c.Started, Finished: c.Finished, NotStarted: c.NotStarted, Delivered: c.Delivered, Failures: c.Failures, Errors: c.Errors, ReportedTransitions: c.ReportedTransitions, ReportedCases: c.ReportedCases, ReportedSkippedChecks: c.ReportedSkippedChecks, AccountingComplete: c.AccountingComplete, Termination: c.Termination.Kind, Error: message, Budgets: owned, AssuranceMaxEvidenceBytes: maxEvidenceBytes}
}

// assuranceCampaignFromReport rebuilds the coordinator facts Rust
// run_campaign records: the termination plus campaign.started/finished/
// delivered/accounting_complete layered over the merge and coordinator
// budgets. Counts Rust does not record (failures, errors, reported totals)
// stay zero and are never compared.
func assuranceCampaignFromReport(termination string, budgets map[string]string, requested uint64) (assuranceCampaignDocument, error) {
	var zero assuranceCampaignDocument
	t, ok := parseRustCampaignTermination(termination)
	if !ok {
		return zero, &ModelError{"invalid campaign termination"}
	}
	natural := func(key string) (uint64, bool) {
		raw, ok := budgets[key]
		if !ok {
			return 0, false
		}
		n, err := strconv.ParseUint(raw, 10, 64)
		return n, err == nil && strconv.FormatUint(n, 10) == raw
	}
	started, startedOK := natural("campaign.started")
	finished, finishedOK := natural("campaign.finished")
	delivered, deliveredOK := natural("campaign.delivered")
	accounting := budgets["campaign.accounting_complete"]
	if !startedOK || !finishedOK || !deliveredOK || (accounting != "true" && accounting != "false") || started > requested {
		return zero, &ModelError{"invalid campaign coordinator budgets"}
	}
	coordinator := maps.Clone(budgets)
	for _, key := range []string{"requested_jobs", "reported_jobs", "campaign.started", "campaign.finished", "campaign.delivered", "campaign.accounting_complete"} {
		delete(coordinator, key)
	}
	d := assuranceCampaignDocument{Requested: requested, Started: started, Finished: finished, NotStarted: requested - started, Delivered: delivered, AccountingComplete: accounting == "true", Termination: t.Kind, Budgets: coordinator}
	if t.Error != nil {
		message := t.Error.Error()
		d.Error = &message
	}
	return d, nil
}

// assuranceU128Decimal accepts a canonical unsigned decimal below 2^128.
func assuranceU128Decimal(raw string) bool {
	if raw == "" || (len(raw) > 1 && raw[0] == '0') {
		return false
	}
	for i := 0; i < len(raw); i++ {
		if raw[i] < '0' || raw[i] > '9' {
			return false
		}
	}
	value, ok := new(big.Int).SetString(raw, 10)
	return ok && value.BitLen() <= 128
}

// validate checks a coordinator document. fromReport documents are rebuilt
// from a genuine Rust report, which carries only the coordinator facts Rust
// run_campaign writes into the budgets and termination.
func (d assuranceCampaignDocument) validate(expected []uint64, jobs uint64, fromReport bool) (CampaignReport64, map[string]string, error) {
	var zero CampaignReport64
	if d.Requested == 0 || d.Requested != uint64(len(expected)) || d.Started > d.Requested || d.NotStarted != d.Requested-d.Started || d.Finished > d.Started || d.Delivered > d.Finished || d.Failures > d.Finished || d.Errors > d.Finished || jobs > d.Started || (d.AccountingComplete && d.Started != d.Finished) {
		return zero, nil, &ModelError{"inconsistent campaign coordinator counts"}
	}
	switch d.Termination {
	case "jobs_completed", "failure_found", "cancelled", "deadline":
		if d.Error != nil {
			return zero, nil, &ModelError{"unexpected campaign termination error"}
		}
	case "callback_error", "worker_error":
		if d.Error == nil || *d.Error == "" {
			return zero, nil, &ModelError{"missing campaign termination error"}
		}
	default:
		return zero, nil, &ModelError{"invalid campaign termination"}
	}
	if d.Termination == "jobs_completed" && (d.Started != d.Requested || d.Finished != d.Requested) {
		return zero, nil, &ModelError{"completed campaign has unfinished jobs"}
	}
	for k, v := range d.Budgets {
		switch k {
		case "campaign.master_seed", "campaign.first_job", "campaign.workers", "campaign.result_buffer", "campaign.stop_on_failure", "cancellation_enabled", "max_duration_ns":
		default:
			return zero, nil, &ModelError{"unsupported or reserved campaign budget key"}
		}
		if !utf8.ValidString(k) || !utf8.ValidString(v) {
			return zero, nil, &ModelError{"invalid campaign budget UTF-8"}
		}
	}
	natural := func(key string) (uint64, bool) {
		raw, ok := d.Budgets[key]
		if !ok {
			return 0, false
		}
		n, err := strconv.ParseUint(raw, 10, 64)
		return n, err == nil && strconv.FormatUint(n, 10) == raw
	}
	_, seedOK := natural("campaign.master_seed")
	first, firstOK := natural("campaign.first_job")
	workers, workersOK := natural("campaign.workers")
	buffer, bufferOK := natural("campaign.result_buffer")
	stop, stopOK := d.Budgets["campaign.stop_on_failure"]
	cancel, cancelOK := d.Budgets["cancellation_enabled"]
	if !seedOK || !firstOK || !workersOK || workers == 0 || workers > 256 || !bufferOK || buffer > 1024 || (!fromReport && d.AssuranceMaxEvidenceBytes == 0) || !stopOK || (stop != "true" && stop != "false") || !cancelOK || (cancel != "true" && cancel != "false") || d.Requested-1 > ^uint64(0)-first {
		return zero, nil, &ModelError{"invalid campaign coordinator budgets"}
	}
	if raw, ok := d.Budgets["max_duration_ns"]; ok {
		// Rust writes Duration::as_nanos(), a u128 decimal.
		if !assuranceU128Decimal(raw) {
			return zero, nil, &ModelError{"invalid campaign duration budget"}
		}
	}
	for _, id := range expected {
		if id < first || id-first >= d.Requested {
			return zero, nil, &ModelError{"campaign job IDs contradict coordinator range"}
		}
	}
	c := CampaignReport64{Requested: d.Requested, Started: d.Started, Finished: d.Finished, NotStarted: d.NotStarted, Delivered: d.Delivered, Failures: d.Failures, Errors: d.Errors, ReportedTransitions: d.ReportedTransitions, ReportedCases: d.ReportedCases, ReportedSkippedChecks: d.ReportedSkippedChecks, AccountingComplete: d.AccountingComplete, Termination: CampaignTermination{Kind: d.Termination}}
	if d.Error != nil {
		c.Termination.Error = &ModelError{*d.Error}
	}
	return c, d.Budgets, nil
}

// ParseAssuranceManifestJSON imports a standalone reference-format manifest;
// declarations alone do not constitute observation or adequacy evidence.
func ParseAssuranceManifestJSON(data []byte) (AssuranceManifest, error) {
	return ParseAssuranceManifestJSONWithLimits(data, DefaultAssuranceJSONLimits())
}
func ParseAssuranceManifestJSONWithLimits(data []byte, limits AssuranceJSONLimits) (AssuranceManifest, error) {
	return ParseAssuranceManifestJSONWithAdmission(data, limits, DefaultAssuranceObservationLimits())
}

// ParseAssuranceManifestJSONWithAdmission selects JSON and catalog limits.
func ParseAssuranceManifestJSONWithAdmission(data []byte, limits AssuranceJSONLimits, admission AssuranceObservationLimits) (AssuranceManifest, error) {
	var empty AssuranceManifest
	if err := validateAssuranceJSON(data, limits); err != nil {
		return empty, err
	}
	// Reuse the report's strongly typed manifest representation without decoding
	// numbers through floating point or accepting unknown derived fields.
	wrapped := make([]byte, 0, len(data)+13)
	wrapped = append(wrapped, `{"manifest":`...)
	wrapped = append(wrapped, data...)
	wrapped = append(wrapped, '}')
	var doc assuranceJSONDocument
	if err := json.Unmarshal(wrapped, &doc); err != nil {
		return empty, err
	}
	x := doc.Manifest
	if admission.MaxProperties <= 0 || admission.MaxFamilies <= 0 || admission.MaxGoals <= 0 || admission.MaxBounds <= 0 || len(x.Properties) > admission.MaxProperties || len(x.Families) > admission.MaxFamilies || len(x.Goals) > admission.MaxGoals || len(x.Bounds) > admission.MaxBounds {
		return empty, &ConfigError{"assurance manifest import limit exceeded"}
	}
	m := AssuranceManifest{Version: x.Version, Model: x.Model, Bounds: x.Bounds, RequiredOutcome: x.Outcome, Provenance: AssuranceProvenance{x.Provenance.Manifest, x.Provenance.Model, x.Provenance.Properties}}
	if m.RequiredOutcome == "exhausted_within_declared_bounds" {
		m.RequiredOutcome = "graph_exhausted"
	}
	for _, p := range x.Properties {
		m.Properties = append(m.Properties, AssuranceProperty{p.ID, p.Phase})
	}
	for _, f := range x.Families {
		if f.Coverage != "required" && f.Coverage != "excluded" {
			return empty, &ModelError{"invalid family coverage"}
		}
		m.Families = append(m.Families, AssuranceFamily{Name: f.Name, Excluded: f.Coverage == "excluded", Reason: f.Reason})
	}
	for _, g := range x.Goals {
		m.Goals = append(m.Goals, AssuranceGoal{g.ID, g.Scope})
	}
	if err := validateAssuranceAdmission(m, Metadata{Name: m.Model}, admission); err != nil {
		return empty, err
	}
	canonical, err := m.JSON()
	if err != nil {
		return empty, err
	}
	decode := func(raw []byte) (any, error) {
		d := json.NewDecoder(bytes.NewReader(raw))
		d.UseNumber()
		var v any
		err := d.Decode(&v)
		return v, err
	}
	original, err := decode(data)
	if err != nil {
		return empty, err
	}
	derived, err := decode(canonical)
	if err != nil {
		return empty, err
	}
	if !reflect.DeepEqual(original, derived) {
		return empty, &ModelError{"assurance manifest fields do not match declarations"}
	}
	return cloneAssuranceManifest(m), nil
}

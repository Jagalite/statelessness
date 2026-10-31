package reference

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"unicode/utf8"

	s "github.com/Jagalite/statelessness/go"
)

// SuiteAssessment reports local witness replay, not independent catalog review.
type SuiteAssessment struct {
	Adequate     bool
	ReviewStatus string
	Reasons      []string
	BuildMatches bool
}

// SuiteBuildPolicy explicitly pins the source report and each mutant build.
// A nil policy retains strict local-build verification. A non-nil policy enables
// cross-build replay only for these exact identities; it does not authenticate
// the producer or waive model, codec, property, or replay compatibility checks.
type SuiteBuildPolicy struct {
	ReportBuild    string
	MutationBuilds map[string]string
}

type suiteReport struct {
	Version int        `json:"schema_version"`
	Model   string     `json:"model"`
	Build   string     `json:"build"`
	Review  string     `json:"review_status"`
	Results []suiteRow `json:"results"`
}
type suiteRow struct {
	Mutation       string  `json:"mutation"`
	Target         string  `json:"target"`
	Status         string  `json:"status"`
	Reason         *string `json:"reason"`
	Search         *string `json:"search"`
	ShrinkComplete *bool   `json:"shrink_complete"`
	Original       *string `json:"original"`
	Minimized      *string `json:"minimized"`
}

// VerifyMutationSuite validates a bounded set of owned artifacts and exactly
// replays both witnesses for every claimed kill. An incomplete valid suite is
// returned with Adequate false. It does not imply independent catalog review.
func VerifyMutationSuite(files map[string][]byte, maxBytes uint64) (SuiteAssessment, error) {
	return VerifyMutationSuiteWithBuilds(files, maxBytes, nil)
}

func VerifyMutationSuiteWithBuilds(files map[string][]byte, maxBytes uint64, policy *SuiteBuildPolicy) (SuiteAssessment, error) {
	out := SuiteAssessment{ReviewStatus: "awaiting_independent_review"}
	fail := func(message string) (SuiteAssessment, error) { return out, &s.ModelError{Message: message} }
	expectedReport := controllerBuild(Controller{Bounds: DefaultBounds()})
	expectedBuilds := make(map[string]string, len(mutationOrder))
	if policy != nil {
		if strings.TrimSpace(policy.ReportBuild) == "" || len(policy.ReportBuild) > 4096 || len(policy.MutationBuilds) != len(mutationOrder) {
			return fail("invalid reference suite build policy")
		}
		expectedReport = strings.Clone(policy.ReportBuild)
		for _, mutation := range mutationOrder {
			build, ok := policy.MutationBuilds[mutation]
			if !ok || strings.TrimSpace(build) == "" || len(build) > 4096 {
				return fail("invalid reference suite build policy")
			}
			expectedBuilds[mutation] = strings.Clone(build)
		}
	} else {
		for _, mutation := range mutationOrder {
			expectedBuilds[mutation] = controllerBuild(Controller{Bounds: DefaultBounds(), Mutation: mutation})
		}
	}
	if maxBytes == 0 || len(files) > 1+2*len(mutationOrder) {
		return fail("reference suite budget")
	}
	owned := make(map[string][]byte, len(files))
	var size uint64
	for name, data := range files {
		if len(name) > 128 || uint64(len(data)) > maxBytes-size {
			return fail("reference suite byte limit")
		}
		size += uint64(len(data))
		owned[name] = bytes.Clone(data)
	}
	raw, ok := owned["mutations.json"]
	if !ok {
		return fail("missing mutation report")
	}
	if err := uniqueSuiteJSON(raw); err != nil {
		return fail("invalid mutation report: " + err.Error())
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil || !suiteKeys(fields, "schema_version", "model", "build", "review_status", "results") {
		return fail("invalid mutation report fields")
	}
	var rows []map[string]json.RawMessage
	if err := json.Unmarshal(fields["results"], &rows); err != nil {
		return fail("invalid mutation result fields")
	}
	for _, row := range rows {
		if !suiteKeys(row, "mutation", "target", "status", "reason", "search", "shrink_complete", "original", "minimized") {
			return fail("invalid mutation result fields")
		}
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	var report suiteReport
	if err := dec.Decode(&report); err != nil {
		return fail("invalid mutation report: " + err.Error())
	}
	if report.Version != 1 || report.Model != "reference-controller" || report.Review != "awaiting_independent_review" || report.Build != expectedReport || len(report.Results) != len(mutationOrder) {
		return fail("reference suite identity or catalog mismatch")
	}
	out.BuildMatches = report.Build == controllerBuild(Controller{Bounds: DefaultBounds()})
	seen := map[string]bool{}
	expected := map[string]bool{"mutations.json": true}
	for _, row := range report.Results {
		target, valid := mutationTargets[row.Mutation]
		if !valid || seen[row.Mutation] || row.Target != target || row.ShrinkComplete == nil {
			return fail("invalid mutation catalog")
		}
		seen[row.Mutation] = true
		// Rust suite_json (src/reference/evidence.rs) always quotes the Debug
		// form of Option<SearchTermination>; no writer emits snake_case or null.
		if row.Search == nil || !suiteSearchValues[*row.Search] {
			return fail("invalid mutation search termination")
		}
		// Rust evaluate_mutation (src/reference.rs) only sets shrink_complete on
		// the kill path. write_mutation_suite may later downgrade a killed result
		// to not_evaluated when preservation fails, keeping the flag, so only a
		// surviving mutation claiming a complete shrink is forged.
		if row.Status == "survives" && *row.ShrinkComplete {
			return fail("invalid shrink completion claim")
		}
		switch row.Status {
		case "killed":
			if row.Reason != nil || *row.Search != "Some(FailureFound)" {
				return fail("invalid killed result")
			}
			for _, witness := range []struct {
				name  string
				field *string
			}{{"original", row.Original}, {"minimized", row.Minimized}} {
				name := row.Mutation + "-" + witness.name + ".sttrace"
				if witness.field == nil || *witness.field != name {
					return fail("invalid witness filename")
				}
				data, ok := owned[name]
				if !ok {
					return fail("missing mutation witness")
				}
				expected[name] = true
				audit, err := s.ReadAuditTrace(bytes.NewReader(data), s.DefaultBinaryLimits())
				if err != nil {
					return fail("invalid mutation witness: " + err.Error())
				}
				mutationCount, targetCount := 0, 0
				for _, p := range audit.Config.Parameters {
					switch p[0] {
					case "mutation":
						mutationCount++
						if p[1] != row.Mutation {
							return fail("mutation witness identity mismatch")
						}
					case "target":
						targetCount++
						if p[1] != target {
							return fail("mutation witness identity mismatch")
						}
					}
				}
				if audit.Config.Strategy != "reference-fault-control" || mutationCount != 1 || targetCount != 1 {
					return fail("mutation witness identity mismatch")
				}
				trace, _, err := audit.Portable()
				if err != nil {
					return fail("invalid mutation witness: " + err.Error())
				}
				c := Controller{Bounds: DefaultBounds(), Mutation: row.Mutation}
				if trace.Metadata.Build != expectedBuilds[row.Mutation] {
					return fail("mutation witness build mismatch")
				}
				if trace.Metadata.Build != controllerBuild(c) {
					out.BuildMatches = false
				}
				if err = exactTargetWithBuild(c, &trace, target, policy != nil); err != nil {
					return fail("mutation witness replay: " + err.Error())
				}
			}
		case "survives":
			if row.Original != nil || row.Minimized != nil || row.Reason != nil || *row.Search != "Some(GraphExhausted)" {
				return fail("invalid surviving result")
			}
			out.Reasons = append(out.Reasons, "mutation "+row.Mutation+": survives")
		case "not_evaluated":
			if row.Original != nil || row.Minimized != nil || row.Reason == nil || strings.TrimSpace(*row.Reason) == "" {
				return fail("invalid not-evaluated result")
			}
			out.Reasons = append(out.Reasons, "mutation "+row.Mutation+": not_evaluated")
		default:
			return fail("invalid mutation status")
		}
	}
	for name := range owned {
		if !expected[name] {
			return fail("unexpected mutation evidence artifact")
		}
	}
	out.Adequate = len(out.Reasons) == 0
	return out, nil
}

// suiteSearchValues are exactly the strings Rust's {:?} of
// Option<SearchTermination> and Go SuiteJSON can emit.
var suiteSearchValues = map[string]bool{
	"None": true, "Some(GraphExhausted)": true, "Some(DepthBound)": true, "Some(StateLimit)": true,
	"Some(TransitionLimit)": true, "Some(EstimatedStateByteLimit)": true, "Some(FailureFound)": true,
	"Some(Deadline)": true, "Some(Cancelled)": true,
}

func suiteKeys(fields map[string]json.RawMessage, keys ...string) bool {
	if len(fields) != len(keys) {
		return false
	}
	for _, key := range keys {
		if _, ok := fields[key]; !ok {
			return false
		}
	}
	return true
}

// Reject duplicate JSON object keys, excess nesting and trailing values.
func uniqueSuiteJSON(data []byte) error {
	if !utf8.Valid(data) {
		return fmt.Errorf("invalid UTF-8")
	}
	d := json.NewDecoder(bytes.NewReader(data))
	var value func(int) error
	value = func(depth int) error {
		if depth > 16 {
			return fmt.Errorf("nesting limit")
		}
		token, err := d.Token()
		if err != nil {
			return err
		}
		delim, ok := token.(json.Delim)
		if !ok {
			return nil
		}
		switch delim {
		case '{':
			keys := map[string]bool{}
			for d.More() {
				t, e := d.Token()
				if e != nil {
					return e
				}
				key, ok := t.(string)
				if !ok || keys[key] {
					return fmt.Errorf("duplicate or invalid object key")
				}
				keys[key] = true
				if e = value(depth + 1); e != nil {
					return e
				}
			}
			end, e := d.Token()
			if e != nil {
				return e
			}
			if end != json.Delim('}') {
				return fmt.Errorf("invalid object")
			}
		case '[':
			for d.More() {
				if e := value(depth + 1); e != nil {
					return e
				}
			}
			end, e := d.Token()
			if e != nil {
				return e
			}
			if end != json.Delim(']') {
				return fmt.Errorf("invalid array")
			}
		default:
			return fmt.Errorf("invalid delimiter")
		}
		return nil
	}
	if err := value(0); err != nil {
		return err
	}
	if _, err := d.Token(); err != io.EOF {
		return fmt.Errorf("trailing JSON")
	}
	return nil
}

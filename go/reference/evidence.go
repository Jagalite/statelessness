package reference

import (
	"encoding/json"
	"errors"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

var mutationOrder = []string{"obsolete-publication", "missing-cleanup", "double-settlement", "dropped-cancellation-ack", "duplicate-completion", "early-release", "skipped-retry-deadline", "ignored-mutation", "reordered-effects"}

type RegressionCase struct {
	Mutation string
	Inputs   []Input
}

// RegressionCorpus strictly parses all named, nonempty independent witnesses.
func RegressionCorpus(text string) ([]RegressionCase, error) {
	var cases []RegressionCase
	seen := map[string]bool{}
	number := func(value string, bits int) (uint64, error) { return strconv.ParseUint(value, 10, bits) }
	for _, raw := range strings.Split(text, "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, "[") && strings.HasSuffix(line, "]") {
			name := line[1 : len(line)-1]
			if _, ok := mutationTargets[name]; !ok || seen[name] {
				return nil, &s.ModelError{Message: "unknown or duplicate regression mutation"}
			}
			seen[name] = true
			cases = append(cases, RegressionCase{Mutation: name})
			continue
		}
		if len(cases) == 0 {
			return nil, &s.ModelError{Message: "regression input before section"}
		}
		parts := strings.Fields(line)
		in := Input{}
		var err error
		switch {
		case len(parts) == 1 && parts[0] == "submit":
			in.Kind = "submit"
		case len(parts) == 2 && (parts[0] == "cancel" || parts[0] == "tick"):
			var n uint64
			n, err = number(parts[1], 64)
			in.Kind = parts[0]
			if in.Kind == "tick" {
				in.Now = n
			} else {
				in.Generation = n
			}
		case (len(parts) == 3 && parts[0] == "cancelled") || (len(parts) == 4 && parts[0] == "complete"):
			in.Kind = parts[0]
			in.Generation, err = number(parts[1], 64)
			if err == nil {
				var a uint64
				a, err = number(parts[2], 8)
				in.Attempt = uint8(a)
			}
			if in.Kind == "complete" {
				switch parts[3] {
				case "success":
					in.Outcome = "success"
				case "retry":
					in.Outcome = "retryable_failure"
				default:
					err = fmt.Errorf("invalid outcome")
				}
			}
		default:
			err = fmt.Errorf("invalid input")
		}
		if err != nil {
			return nil, &s.ModelError{Message: "bad regression input"}
		}
		cases[len(cases)-1].Inputs = append(cases[len(cases)-1].Inputs, in)
	}
	if len(cases) != len(mutationOrder) {
		return nil, &s.ModelError{Message: "missing required regression cases"}
	}
	for _, c := range cases {
		if len(c.Inputs) == 0 {
			return nil, &s.ModelError{Message: "empty required regression case"}
		}
	}
	return cases, nil
}
func exactTarget(c Controller, trace *s.Trace, target string) error {
	return exactTargetWithBuild(c, trace, target, false)
}
func exactTargetWithBuild(c Controller, trace *s.Trace, target string, allowBuildMismatch bool) error {
	report, err := s.Replay(c.Model(), trace, allowBuildMismatch)
	if err != nil {
		return err
	}
	found := false
	for _, step := range trace.Steps {
		for _, check := range step.Checks {
			if check.ID == target && check.Status == "failed" {
				found = true
			}
		}
	}
	for _, check := range trace.InitialChecks {
		if check.ID == target && check.Status == "failed" {
			found = true
		}
	}
	if trace.Termination != "property_failed" || report.Outcome != "exact" || !report.FailureReproduced || !found {
		return &s.ModelError{Message: "recorded witness did not exactly replay required target"}
	}
	return nil
}

// CheckRegressions requires both mutant target reproduction and fixed-model
// passing evidence for every retained witness.
func CheckRegressions(text string) error {
	cases, err := RegressionCorpus(text)
	if err != nil {
		return err
	}
	for _, test := range cases {
		mutant := Controller{Bounds: DefaultBounds(), Mutation: test.Mutation}
		trace, err := s.Record(mutant.Model(), s.Values(test.Inputs), len(test.Inputs))
		if err != nil {
			return err
		}
		if err = exactTarget(mutant, trace, mutationTargets[test.Mutation]); err != nil {
			return err
		}
		fixed := Controller{Bounds: DefaultBounds()}
		trace, err = s.Record(fixed.Model(), s.Values(test.Inputs), len(test.Inputs))
		if err != nil {
			return err
		}
		// Deliberately stricter than Rust check_regressions
		// (src/reference/evidence.rs), which only rejects PropertyFailed or a
		// non-passing step check: a fixed reducer that stops with a model error
		// or truncates the witness has not shown that it passes it.
		if trace.Termination != "completed" || len(trace.Steps) != len(test.Inputs) {
			return &s.ModelError{Message: "fixed reducer did not complete retained witness"}
		}
		for _, check := range trace.InitialChecks {
			if check.Status != "passed" {
				return &s.ModelError{Message: "fixed reducer did not pass initial checks"}
			}
		}
		for _, step := range trace.Steps {
			for _, check := range step.Checks {
				if check.Status != "passed" {
					return &s.ModelError{Message: "fixed reducer did not pass retained witness"}
				}
			}
		}
	}
	return nil
}
func RetainedRegressionCorpus() (string, error) {
	data, err := sourceFiles.ReadFile("controller-witnesses.txt")
	return string(data), err
}

// WriteMutationSuite creates a new private directory and never overwrites prior
// evidence. Partial preservation failure is represented as not_evaluated.
func WriteMutationSuite(directory string, config s.SearchConfig) ([]MutationResult, error) {
	return writeMutationSuite(directory, config, saveTraceNew)
}

// save returns whether this invocation exclusively created the destination,
// including when encoding or syncing that newly created file fails.
func writeMutationSuite(directory string, config s.SearchConfig, save func(string, *s.AuditTrace) (bool, error)) ([]MutationResult, error) {
	if err := os.Mkdir(directory, 0700); err != nil {
		return nil, err
	}
	results := make([]MutationResult, 0, len(mutationOrder))
	for _, name := range mutationOrder {
		result := EvaluateMutation(name, config)
		if result.Status == "killed" {
			c := Controller{Bounds: DefaultBounds(), Mutation: name}
			created := []string{}
			var foreignPath string
			for _, entry := range []struct {
				name    string
				failure *s.Failure[Input]
			}{{"original", result.Original}, {"minimized", result.Minimized}} {
				if entry.failure == nil {
					result.Status = "not_evaluated"
					result.Reason = "missing killed witness"
					break
				}
				trace, err := s.Record(c.Model(), s.Values(entry.failure.Inputs), len(entry.failure.Inputs))
				if err == nil {
					err = exactTarget(c, trace, mutationTargets[name])
				}
				if err == nil {
					config := s.RunConfig{Strategy: "reference-fault-control", Parameters: [][2]string{{"mutation", name}, {"target", mutationTargets[name]}, {"max_states", strconv.Itoa(config.MaxStates)}, {"max_transitions", strconv.FormatUint(config.MaxTransitions, 10)}, {"max_depth", strconv.Itoa(config.MaxDepth)}}}
					var audit *s.AuditTrace
					audit, err = s.AuditFromPortable(*trace, config)
					if err == nil {
						path := filepath.Join(directory, name+"-"+entry.name+".sttrace")
						var owned bool
						owned, err = save(path, audit)
						if owned {
							created = append(created, path)
						} else if err != nil {
							if _, statErr := os.Lstat(path); statErr == nil || !os.IsNotExist(statErr) {
								foreignPath = path
							}
						}
					}
				}
				if err != nil {
					result.Status = "not_evaluated"
					result.Reason = "evidence preservation/replay: " + err.Error()
					break
				}
			}
			if result.Status != "killed" {
				for _, path := range created {
					if err := os.Remove(path); err != nil {
						return results, fmt.Errorf("remove incomplete mutation witness: %w", err)
					}
				}
				if foreignPath != "" {
					return results, fmt.Errorf("cannot publish suite with unowned witness path: %s", foreignPath)
				}
			}
		}
		results = append(results, result)
	}
	data, err := SuiteJSON(results)
	if err != nil {
		return results, err
	}
	err = publishSuiteReport(directory, data, func(f *os.File, data []byte) error {
		written, err := f.Write(data)
		if err == nil && written != len(data) {
			return io.ErrShortWrite
		}
		return err
	})
	return results, err
}

// Mirrors Rust write_mutation_suite (src/reference/evidence.rs), which opens
// mutations.json with create_new: O_CREATE|O_EXCL never replaces an existing
// report, and a report this call created is removed again if writing fails.
func publishSuiteReport(directory string, data []byte, write func(*os.File, []byte) error) error {
	final := filepath.Join(directory, "mutations.json")
	f, err := os.OpenFile(final, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	err = write(f, data)
	if err == nil {
		err = f.Sync()
	}
	if closeErr := f.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return errors.Join(err, os.Remove(final))
	}
	return nil
}

// SuiteJSON emits the reference mutation summary without claiming review.
func SuiteJSON(results []MutationResult) ([]byte, error) {
	report := make([]map[string]any, 0, len(results))
	for _, r := range results {
		if r.Status == "killed" && (r.Original == nil || r.Minimized == nil) {
			return nil, &s.ModelError{Message: "killed mutation is missing a witness"}
		}
		var reason, original, minimized any
		if r.Reason != "" {
			reason = r.Reason
		}
		if r.Status == "killed" {
			original = r.Mutation + "-original.sttrace"
			minimized = r.Mutation + "-minimized.sttrace"
		}
		target, ok := mutationTargets[r.Mutation]
		if !ok {
			return nil, &s.ModelError{Message: "unknown mutation"}
		}
		if r.Status != "killed" && r.Status != "survives" && r.Status != "not_evaluated" {
			return nil, &s.ModelError{Message: "unknown mutation status"}
		}
		search := "None"
		if r.Search != "" {
			names := map[string]string{"graph_exhausted": "GraphExhausted", "depth_bound": "DepthBound", "state_limit": "StateLimit", "transition_limit": "TransitionLimit", "estimated_state_byte_limit": "EstimatedStateByteLimit", "failure_found": "FailureFound", "deadline": "Deadline", "cancelled": "Cancelled"}
			name, ok := names[r.Search]
			if !ok {
				return nil, &s.ModelError{Message: "unknown mutation search termination"}
			}
			search = "Some(" + name + ")"
		}
		report = append(report, map[string]any{"mutation": r.Mutation, "target": target, "status": r.Status, "reason": reason, "search": search, "shrink_complete": r.ShrinkComplete, "original": original, "minimized": minimized})
	}
	data, err := json.Marshal(map[string]any{"schema_version": 1, "model": "reference-controller", "build": controllerBuild(Controller{Bounds: DefaultBounds()}), "review_status": "awaiting_independent_review", "results": report})
	if err != nil {
		return nil, err
	}
	return append(data, '\n'), nil
}

// SaveTrace writes a binary trace to a new private file and syncs it.
func SaveTrace(path string, trace *s.AuditTrace) error {
	_, err := saveTraceNew(path, trace)
	return err
}

func saveTraceNew(path string, trace *s.AuditTrace) (bool, error) {
	if trace == nil {
		return false, &s.ModelError{Message: "nil audit trace"}
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return false, err
	}
	err = trace.WriteBinary(f)
	if err == nil {
		err = f.Sync()
	}
	if closeErr := f.Close(); err == nil {
		err = closeErr
	}
	return true, err
}

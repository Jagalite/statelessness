package statelessness_test

import (
	"bytes"
	"encoding/json"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"testing"
)

type researchCorpusObject = map[string]any

func researchCorpusMust(err error) {
	if err != nil {
		panic(err)
	}
}
func researchCorpusText(v any) string {
	if v == nil {
		return ""
	}
	return v.(string)
}
func researchCorpusNumber(v any) uint64 {
	n, err := strconv.ParseUint(fmt.Sprint(v), 10, 64)
	researchCorpusMust(err)
	return n
}
func researchCorpusMerge(base, over researchCorpusObject) researchCorpusObject {
	out := researchCorpusObject{}
	for k, v := range base {
		out[k] = v
	}
	for k, v := range over {
		if child, ok := v.(map[string]any); ok {
			out[k] = researchCorpusMerge(out[k].(map[string]any), child)
		} else {
			out[k] = v
		}
	}
	return out
}
func researchCorpusOverrides(v any) researchCorpusObject {
	if v == nil {
		return researchCorpusObject{}
	}
	return v.(map[string]any)
}
func researchCorpusProperties(v any) []s.RequiredProperty {
	out := []s.RequiredProperty{}
	for _, x := range v.([]any) {
		p := x.(map[string]any)
		out = append(out, s.RequiredProperty{ID: researchCorpusText(p["id"]), Phase: s.PropertyPhase(researchCorpusText(p["phase"]))})
	}
	return out
}
func researchCorpusProposal(v researchCorpusObject) s.ResearchProposal {
	a := v["assumptions"].(map[string]any)
	return s.ResearchProposal{Name: researchCorpusText(v["name"]), Version: uint32(researchCorpusNumber(v["version"])), Build: researchCorpusText(v["build"]), RequiredProperties: researchCorpusProperties(v["required_properties"]), Assumptions: s.ResearchAssumptions{Established: a["established"].(bool), FiniteDomain: researchCorpusText(a["finite_domain"]), Equivalence: researchCorpusText(a["equivalence"]), Effects: researchCorpusText(a["effects"]), TransitionProperties: researchCorpusText(a["transition_properties"]), QueueOrder: researchCorpusText(a["queue_order"]), OracleHistory: researchCorpusText(a["oracle_history"]), VisibleTiming: researchCorpusText(a["visible_timing"])}}
}
func researchCorpusChecks(v any) []s.Check {
	out := []s.Check{}
	for _, x := range v.([]any) {
		c := x.(map[string]any)
		out = append(out, s.Check{ID: researchCorpusText(c["id"]), Status: researchCorpusText(c["status"]), Details: researchCorpusText(c["details"])})
	}
	return out
}
func researchCorpusStrings(v any) []string {
	out := []string{}
	for _, x := range v.([]any) {
		out = append(out, researchCorpusText(x))
	}
	return out
}
func researchCorpusDec(n uint64) string { return strconv.FormatUint(n, 10) }
func researchCorpusProp(p s.RequiredProperty) researchCorpusObject {
	return researchCorpusObject{"id": p.ID, "phase": string(p.Phase)}
}
func researchCorpusProps(v []s.RequiredProperty) []any {
	out := []any{}
	for _, p := range v {
		out = append(out, researchCorpusProp(p))
	}
	return out
}
func researchCorpusViolations(v []s.ViolationWitness[string]) []any {
	out := []any{}
	for _, x := range v {
		out = append(out, researchCorpusObject{"property": researchCorpusProp(x.Property), "details": x.Details, "inputs": append([]string{}, x.Inputs...)})
	}
	return out
}
func researchCorpusReach(v *s.ResearchReachability[string]) any {
	if v == nil {
		return nil
	}
	return researchCorpusObject{"states": append([]int{}, v.States...), "edges": researchCorpusDec(v.Edges), "violations": researchCorpusViolations(v.Violations), "observed_properties": researchCorpusProps(v.ObservedProperties), "skipped_checks": researchCorpusDec(v.SkippedChecks)}
}
func researchCorpusReport(r *s.ExperimentReport[string], p, c, metadata researchCorpusObject) researchCorpusObject {

	a := r.Proposal.Assumptions
	p = researchCorpusObject{"name": r.Proposal.Name, "version": r.Proposal.Version, "build": r.Proposal.Build, "required_properties": researchCorpusProps(r.Proposal.RequiredProperties), "assumptions": researchCorpusObject{"established": a.Established, "finite_domain": a.FiniteDomain, "equivalence": a.Equivalence, "effects": a.Effects, "transition_properties": a.TransitionProperties, "queue_order": a.QueueOrder, "oracle_history": a.OracleHistory, "visible_timing": a.VisibleTiming}}
	c = researchCorpusObject{"enabled": r.Config.Enabled, "max_states": researchCorpusDec(r.Config.MaxStates), "max_edges": researchCorpusDec(r.Config.MaxEdges), "max_equivalence_pairs": researchCorpusDec(r.Config.MaxEquivalencePairs)}
	metadata = researchCorpusObject{"name": r.Model.Name, "model_version": r.Model.ModelVersion, "properties_version": r.Model.PropertiesVersion, "codec_version": r.Model.CodecVersion, "build": r.Model.Build}
	counter := []any{}
	for _, x := range r.Counterexamples {
		counter = append(counter, researchCorpusObject{"left_state": x.LeftState, "right_state": x.RightState, "left_prefix": append([]string{}, x.LeftPrefix...), "right_prefix": append([]string{}, x.RightPrefix...), "reason": x.Reason})
	}
	return researchCorpusObject{"model": metadata, "proposal": p, "config": c, "status": string(r.Status), "declared_states": researchCorpusDec(r.DeclaredStates), "materialized_edges": researchCorpusDec(r.MaterializedEdges), "compared_pairs": researchCorpusDec(r.ComparedPairs), "exact": researchCorpusReach(r.Exact), "reduced": researchCorpusReach(r.Reduced), "counterexamples": counter, "missing_violations": researchCorpusViolations(r.MissingViolations), "missing_properties": researchCorpusProps(r.MissingProperties), "limitation": r.Limitation}
}

func researchCorpusRunCase(defaults, row researchCorpusObject) researchCorpusObject {
	log := []string{}
	result := researchCorpusObject{"case": row["case"], "error": nil, "report": nil, "qualification_parameters": nil}
	event := func(name string) error {
		log = append(log, name)
		if name == researchCorpusText(row["error_at"]) {
			return &s.ModelError{Message: "fixture error at " + name}
		}
		return nil
	}
	scenario := row["scenario"].(map[string]any)
	rows := map[int]researchCorpusObject{}
	for _, x := range scenario["rows"].([]any) {
		v := x.(map[string]any)
		rows[int(researchCorpusNumber(v["state"]))] = v
	}
	edge := func(state int, input string) researchCorpusObject {
		for _, x := range rows[state]["inputs"].([]any) {
			v := x.(map[string]any)
			if researchCorpusText(v["input"]) == input {
				return v
			}
		}
		panic("undeclared input")
	}
	p := researchCorpusMerge(defaults["proposal"].(map[string]any), researchCorpusOverrides(row["proposal_overrides"]))
	c := researchCorpusMerge(defaults["config"].(map[string]any), researchCorpusOverrides(row["config_overrides"]))
	identity := defaults["metadata"].(map[string]any)
	metadata := func() (s.Metadata, error) {
		if err := event("metadata"); err != nil {
			return s.Metadata{}, err
		}
		return s.Metadata{Name: researchCorpusText(identity["name"]), ModelVersion: uint32(researchCorpusNumber(identity["model_version"])), PropertiesVersion: uint32(researchCorpusNumber(identity["properties_version"])), CodecVersion: uint32(researchCorpusNumber(identity["codec_version"])), Build: researchCorpusText(identity["build"])}, nil
	}
	model := s.Model[int, string, string]{CloneState: func(v int) int { return v }, CloneInput: func(v string) string { return v }, CloneOutput: func(v string) string { return v }, EqualStates: func(a, b int) bool { return a == b },
		InitialState: func() (int, error) { return int(researchCorpusNumber(scenario["initial"])), event("initial") },
		CheckState: func(state int) ([]s.Check, error) {
			if err := event(fmt.Sprintf("state:%d", state)); err != nil {
				return nil, err
			}
			return researchCorpusChecks(rows[state]["checks"]), nil
		},
		Inputs: func(state int) (s.Iterator[string], error) {
			if err := event(fmt.Sprintf("inputs:%d", state)); err != nil {
				return nil, err
			}
			entries := rows[state]["inputs"].([]any)
			index := 0
			return s.Iterator[string](func() (string, bool, error) {
				if err := event(fmt.Sprintf("next:%d:%d", state, index)); err != nil {
					return "", false, err
				}
				if index == len(entries) {
					return "", false, nil
				}
				v := entries[index].(map[string]any)
				index++
				return researchCorpusText(v["input"]), true, nil
			}), nil
		},
		Step: func(state int, input string) (s.Transition[int, string], error) {
			if err := event(fmt.Sprintf("step:%d:%s", state, input)); err != nil {
				return s.Transition[int, string]{}, err
			}
			e := edge(state, input)
			d := e["disposition"].(map[string]any)
			return s.Transition[int, string]{State: int(researchCorpusNumber(e["target"])), Outputs: researchCorpusStrings(e["outputs"]), Disposition: s.Disposition{Kind: researchCorpusText(d["kind"]), Reason: researchCorpusText(d["reason"])}}, nil
		},
		CheckTransition: func(state int, input string, _ s.Transition[int, string]) ([]s.Check, error) {
			if err := event(fmt.Sprintf("transition:%d:%s", state, input)); err != nil {
				return nil, err
			}
			return researchCorpusChecks(edge(state, input)["checks"]), nil
		}}
	states := []int{}
	for _, x := range scenario["states"].([]any) {
		states = append(states, int(researchCorpusNumber(x)))
	}
	cfg := s.ResearchConfig{Enabled: c["enabled"].(bool), MaxStates: researchCorpusNumber(c["max_states"]), MaxEdges: researchCorpusNumber(c["max_edges"]), MaxEquivalencePairs: researchCorpusNumber(c["max_equivalence_pairs"])}
	r, err := s.CompareEquivalence(model, states, researchCorpusProposal(p), cfg, func(state int) (string, error) {
		if err := event(fmt.Sprintf("projection:%d", state)); err != nil {
			return "", err
		}
		for index, candidate := range scenario["states"].([]any) {
			if int(researchCorpusNumber(candidate)) == state {
				return researchCorpusText(scenario["keys"].([]any)[index]), nil
			}
		}
		panic("undeclared projection state")
	}, s.ResearchEquality[string, string]{Input: func(a, b string) bool { return a == b }, Output: func(a, b string) bool { return a == b }}, metadata)
	if err != nil {
		result["error"] = err.Error()
	} else {
		result["report"] = researchCorpusReport(r, p, c, identity)
		result["qualification_parameters"] = s.QualificationParameters(*r)
	}
	result["callback_log"] = log
	return result
}

func TestResearchSharedCorpus(t *testing.T) {
	path := os.Getenv("STATELESS_RESEARCH_CORPUS")
	if path == "" {
		path = filepath.Join("..", "conformance", "research-equivalence-v1.json")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	decode := func(data []byte) researchCorpusObject {
		var value researchCorpusObject
		decoder := json.NewDecoder(bytes.NewReader(data))
		decoder.UseNumber()
		if err := decoder.Decode(&value); err != nil {
			t.Fatal(err)
		}
		return value
	}
	fixture := decode(raw)
	rows := fixture["cases"].([]any)
	for _, item := range rows {
		row := item.(map[string]any)
		t.Run(researchCorpusText(row["case"]), func(t *testing.T) {
			actual := researchCorpusRunCase(fixture["defaults"].(map[string]any), row)
			delete(actual, "case")
			encoded, err := json.Marshal(actual)
			if err != nil {
				t.Fatal(err)
			}
			normalized := decode(encoded)
			if !reflect.DeepEqual(normalized, row["expected"]) {
				want, _ := json.Marshal(row["expected"])
				t.Fatalf("got %s\nwant %s", encoded, want)
			}
		})
	}
	t.Logf("executed every %d shared public research cases", len(rows))
}

func TestResearchSharedNativeAdmission(t *testing.T) {
	path := os.Getenv("STATELESS_RESEARCH_CORPUS")
	if path == "" {
		path = filepath.Join("..", "conformance", "research-equivalence-v1.json")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture researchCorpusObject
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if err := decoder.Decode(&fixture); err != nil {
		t.Fatal(err)
	}
	defaults := fixture["defaults"].(map[string]any)
	for _, item := range fixture["native_admission_cases"].([]any) {
		row := item.(map[string]any)
		name := researchCorpusText(row["case"])
		t.Run(name, func(t *testing.T) {
			switch name {
			case "negative-config-budget", "config-over-u64", "proposal-version-over-u32":
				t.Skip("static Go uint64/uint32 argument exclusion; no runtime admission claim")
			case "unsafe-number-config":
				t.Skip("TypeScript Number-only admission; Go config uses exact uint64")
			case "asynchronous-custom-callback":
				t.Skip("static Go synchronous callback signature; no runtime admission claim")
			}
			model := s.Model[int, string, string]{InitialState: func() (int, error) { return 0, nil }, Step: func(v int, _ string) (s.Transition[int, string], error) { return s.Accept(v, "effect"), nil }, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[string], CloneOutput: s.ValueCopy[string], EqualStates: func(a, b int) bool { return a == b }, Inputs: func(int) (s.Iterator[string], error) { return s.Values([]string{"tick"}), nil }, CheckState: func(int) ([]s.Check, error) { return []s.Check{s.Passed("state")}, nil }, CheckTransition: func(int, string, s.Transition[int, string]) ([]s.Check, error) {
				return []s.Check{s.Passed("transition")}, nil
			}}
			eq := s.ResearchEquality[string, string]{Input: func(a, b string) bool { return a == b }, Output: func(a, b string) bool { return a == b }}
			metadata := func() (s.Metadata, error) { return s.Identity("native-research", "native"), nil }
			project := func(int) (string, error) { return "same", nil }
			var report *s.ExperimentReport[string]
			var failure error
			switch name {
			case "projection-nonreflexive-key":
				report, failure = s.CompareEquivalence(model, []int{0, 1}, researchCorpusProposal(defaults["proposal"].(map[string]any)), s.ResearchConfig{Enabled: true, MaxStates: 256, MaxEdges: 4096, MaxEquivalencePairs: 65536}, func(int) (float64, error) { return math.NaN(), nil }, eq, metadata)
			case "projection-callback-throws":
				project = func(int) (string, error) { panic("projection failure") }
			case "equality-callback-throws":
				model.EqualStates = func(int, int) bool { panic("equality failure") }
			case "iterator-next-throws":
				model.Inputs = func(int) (s.Iterator[string], error) {
					return func() (string, bool, error) { panic("next failure") }, nil
				}
			case "metadata-supplier-throws":
				metadata = func() (s.Metadata, error) { panic("metadata failure") }
			default:
				t.Fatal("unhandled native research case", name)
			}
			if name != "projection-nonreflexive-key" {
				report, failure = s.CompareEquivalence(model, []int{0, 1}, researchCorpusProposal(defaults["proposal"].(map[string]any)), s.ResearchConfig{Enabled: true, MaxStates: 256, MaxEdges: 4096, MaxEquivalencePairs: 65536}, project, eq, metadata)
			}
			if failure == nil && (report == nil || report.Status != s.ExperimentIncomplete) {
				t.Fatalf("native failure became qualified: %+v %v", report, failure)
			}
		})
	}
}

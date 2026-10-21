package main

import (
	"encoding/json"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"os"
	"strconv"
)

type object = map[string]any

func must(err error) {
	if err != nil {
		panic(err)
	}
}
func text(v any) string {
	if v == nil {
		return ""
	}
	return v.(string)
}
func number(v any) uint64 { n, err := strconv.ParseUint(fmt.Sprint(v), 10, 64); must(err); return n }
func merge(base, over object) object {
	out := object{}
	for k, v := range base {
		out[k] = v
	}
	for k, v := range over {
		if child, ok := v.(map[string]any); ok {
			out[k] = merge(out[k].(map[string]any), child)
		} else {
			out[k] = v
		}
	}
	return out
}
func overrides(v any) object {
	if v == nil {
		return object{}
	}
	return v.(map[string]any)
}
func properties(v any) []s.RequiredProperty {
	out := []s.RequiredProperty{}
	for _, x := range v.([]any) {
		p := x.(map[string]any)
		out = append(out, s.RequiredProperty{ID: text(p["id"]), Phase: s.PropertyPhase(text(p["phase"]))})
	}
	return out
}
func proposal(v object) s.ResearchProposal {
	a := v["assumptions"].(map[string]any)
	return s.ResearchProposal{Name: text(v["name"]), Version: uint32(number(v["version"])), Build: text(v["build"]), RequiredProperties: properties(v["required_properties"]), Assumptions: s.ResearchAssumptions{Established: a["established"].(bool), FiniteDomain: text(a["finite_domain"]), Equivalence: text(a["equivalence"]), Effects: text(a["effects"]), TransitionProperties: text(a["transition_properties"]), QueueOrder: text(a["queue_order"]), OracleHistory: text(a["oracle_history"]), VisibleTiming: text(a["visible_timing"])}}
}
func checks(v any) []s.Check {
	out := []s.Check{}
	for _, x := range v.([]any) {
		c := x.(map[string]any)
		out = append(out, s.Check{ID: text(c["id"]), Status: text(c["status"]), Details: text(c["details"])})
	}
	return out
}
func strings(v any) []string {
	out := []string{}
	for _, x := range v.([]any) {
		out = append(out, text(x))
	}
	return out
}
func dec(n uint64) string              { return strconv.FormatUint(n, 10) }
func prop(p s.RequiredProperty) object { return object{"id": p.ID, "phase": string(p.Phase)} }
func props(v []s.RequiredProperty) []any {
	out := []any{}
	for _, p := range v {
		out = append(out, prop(p))
	}
	return out
}
func violations(v []s.ViolationWitness[string]) []any {
	out := []any{}
	for _, x := range v {
		out = append(out, object{"property": prop(x.Property), "details": x.Details, "inputs": append([]string{}, x.Inputs...)})
	}
	return out
}
func reach(v *s.ResearchReachability[string]) any {
	if v == nil {
		return nil
	}
	return object{"states": append([]int{}, v.States...), "edges": dec(v.Edges), "violations": violations(v.Violations), "observed_properties": props(v.ObservedProperties), "skipped_checks": dec(v.SkippedChecks)}
}
func report(r *s.ExperimentReport[string], _p, _c, _metadata object) object {
	a := r.Proposal.Assumptions
	p := object{"name": r.Proposal.Name, "version": r.Proposal.Version, "build": r.Proposal.Build, "required_properties": props(r.Proposal.RequiredProperties), "assumptions": object{"established": a.Established, "finite_domain": a.FiniteDomain, "equivalence": a.Equivalence, "effects": a.Effects, "transition_properties": a.TransitionProperties, "queue_order": a.QueueOrder, "oracle_history": a.OracleHistory, "visible_timing": a.VisibleTiming}}
	c := object{"enabled": r.Config.Enabled, "max_states": dec(r.Config.MaxStates), "max_edges": dec(r.Config.MaxEdges), "max_equivalence_pairs": dec(r.Config.MaxEquivalencePairs)}
	metadata := object{"name": r.Model.Name, "model_version": r.Model.ModelVersion, "properties_version": r.Model.PropertiesVersion, "codec_version": r.Model.CodecVersion, "build": r.Model.Build}
	counter := []any{}
	for _, x := range r.Counterexamples {
		counter = append(counter, object{"left_state": x.LeftState, "right_state": x.RightState, "left_prefix": append([]string{}, x.LeftPrefix...), "right_prefix": append([]string{}, x.RightPrefix...), "reason": x.Reason})
	}
	return object{"model": metadata, "proposal": p, "config": c, "status": string(r.Status), "declared_states": dec(r.DeclaredStates), "materialized_edges": dec(r.MaterializedEdges), "compared_pairs": dec(r.ComparedPairs), "exact": reach(r.Exact), "reduced": reach(r.Reduced), "counterexamples": counter, "missing_violations": violations(r.MissingViolations), "missing_properties": props(r.MissingProperties), "limitation": r.Limitation}
}

func runCase(defaults, row object) object {
	log := []string{}
	result := object{"case": row["case"], "error": nil, "report": nil, "qualification_parameters": nil}
	event := func(name string) error {
		log = append(log, name)
		if name == text(row["error_at"]) {
			return &s.ModelError{Message: "fixture error at " + name}
		}
		return nil
	}
	scenario := row["scenario"].(map[string]any)
	rows := map[int]object{}
	for _, x := range scenario["rows"].([]any) {
		v := x.(map[string]any)
		rows[int(number(v["state"]))] = v
	}
	edge := func(state int, input string) object {
		for _, x := range rows[state]["inputs"].([]any) {
			v := x.(map[string]any)
			if text(v["input"]) == input {
				return v
			}
		}
		panic("undeclared input")
	}
	p := merge(defaults["proposal"].(map[string]any), overrides(row["proposal_overrides"]))
	c := merge(defaults["config"].(map[string]any), overrides(row["config_overrides"]))
	identity := defaults["metadata"].(map[string]any)
	metadata := func() (s.Metadata, error) {
		if err := event("metadata"); err != nil {
			return s.Metadata{}, err
		}
		return s.Metadata{Name: text(identity["name"]), ModelVersion: uint32(number(identity["model_version"])), PropertiesVersion: uint32(number(identity["properties_version"])), CodecVersion: uint32(number(identity["codec_version"])), Build: text(identity["build"])}, nil
	}
	model := s.Model[int, string, string]{CloneState: func(v int) int { return v }, CloneInput: func(v string) string { return v }, CloneOutput: func(v string) string { return v }, EqualStates: func(a, b int) bool { return a == b },
		InitialState: func() (int, error) { return int(number(scenario["initial"])), event("initial") },
		CheckState: func(state int) ([]s.Check, error) {
			if err := event(fmt.Sprintf("state:%d", state)); err != nil {
				return nil, err
			}
			return checks(rows[state]["checks"]), nil
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
				return text(v["input"]), true, nil
			}), nil
		},
		Step: func(state int, input string) (s.Transition[int, string], error) {
			if err := event(fmt.Sprintf("step:%d:%s", state, input)); err != nil {
				return s.Transition[int, string]{}, err
			}
			e := edge(state, input)
			d := e["disposition"].(map[string]any)
			return s.Transition[int, string]{State: int(number(e["target"])), Outputs: strings(e["outputs"]), Disposition: s.Disposition{Kind: text(d["kind"]), Reason: text(d["reason"])}}, nil
		},
		CheckTransition: func(state int, input string, _ s.Transition[int, string]) ([]s.Check, error) {
			if err := event(fmt.Sprintf("transition:%d:%s", state, input)); err != nil {
				return nil, err
			}
			return checks(edge(state, input)["checks"]), nil
		}}
	states := []int{}
	for _, x := range scenario["states"].([]any) {
		states = append(states, int(number(x)))
	}
	cfg := s.ResearchConfig{Enabled: c["enabled"].(bool), MaxStates: number(c["max_states"]), MaxEdges: number(c["max_edges"]), MaxEquivalencePairs: number(c["max_equivalence_pairs"])}
	r, err := s.CompareEquivalence(model, states, proposal(p), cfg, func(state int) (string, error) {
		if err := event(fmt.Sprintf("projection:%d", state)); err != nil {
			return "", err
		}
		for index, candidate := range scenario["states"].([]any) {
			if int(number(candidate)) == state {
				return text(scenario["keys"].([]any)[index]), nil
			}
		}
		panic("undeclared projection state")
	}, s.ResearchEquality[string, string]{Input: func(a, b string) bool { return a == b }, Output: func(a, b string) bool { return a == b }}, metadata)
	if err != nil {
		result["error"] = err.Error()
	} else {
		result["report"] = report(r, p, c, identity)
		result["qualification_parameters"] = s.QualificationParameters(*r)
	}
	result["callback_log"] = log
	return result
}
func main() {
	if len(os.Args) != 4 || os.Args[1] != "run" {
		panic("usage: consumer run CORPUS OUTPUT")
	}
	f, err := os.Open(os.Args[2])
	must(err)
	defer f.Close()
	decoder := json.NewDecoder(f)
	decoder.UseNumber()
	var fixture object
	must(decoder.Decode(&fixture))
	results := []any{}
	for _, x := range fixture["cases"].([]any) {
		results = append(results, runCase(fixture["defaults"].(map[string]any), x.(map[string]any)))
	}
	b, err := json.Marshal(object{"profile": "research-equivalence-v1", "results": results})
	must(err)
	must(os.WriteFile(os.Args[3], append(b, '\n'), 0600))
	must(json.NewEncoder(os.Stdout).Encode(object{"producer": "go", "mode": "run", "case_count": len(results), "production_pruning_enabled": false}))
}

//go:build cgo

// Package conformance adapts the shared fixture tables to the Rust ABI 2 engine.
package conformance

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"reflect"
	"strconv"
	"unicode/utf8"

	s "github.com/Jagalite/statelessness/bindings/go/v2"
	j "github.com/Jagalite/statelessness/bindings/go/v2/internal/portablejson"
)

const MaxLine = 4 * 1024 * 1024
const maxWork = 100_000

type edge struct {
	input, to             string
	outputs               []string
	disposition           s.Disposition
	checks                []s.Check
	stepError, checkError bool
}
type row struct {
	checks                  []s.Check
	edges                   []edge
	checkError, inputsError bool
}
type table struct {
	initial      string
	metadata     s.Metadata
	rows         map[string]row
	initialError bool
	calls        int
}

func readChecks(r *j.Reader, v any) []s.Check {
	a := r.Array(v, 4096)
	result := make([]s.Check, len(a))
	for i, x := range a {
		result[i] = readCheck(r, x)
	}
	return result
}
func parseTable(value any) (*table, error) {
	r := &j.Reader{}
	o := r.Object(value, []string{"id", "initial", "states"}, "metadata", "initial_error")
	id := r.Text(o["id"])
	t := &table{initial: r.Text(o["initial"]), metadata: s.Metadata{Name: id, Build: "fixture-v1", ModelVersion: 1, PropertiesVersion: 1, CodecVersion: 1}, rows: map[string]row{}, initialError: r.Bool(j.Default(o, "initial_error", false))}
	if v, ok := o["metadata"]; ok {
		t.metadata = readMetadata(r, v)
	}
	count := 0
	for _, v := range r.Array(o["states"], 1000) {
		x := r.Object(v, []string{"id"}, "checks", "edges", "check_error", "inputs_error")
		id := r.Text(x["id"])
		if _, ok := t.rows[id]; ok {
			r.Fail("duplicate state")
		}
		entry := row{checks: readChecks(r, j.Default(x, "checks", []any{})), edges: []edge{}, checkError: r.Bool(j.Default(x, "check_error", false)), inputsError: r.Bool(j.Default(x, "inputs_error", false))}
		seen := map[string]edge{}
		for _, v := range r.Array(j.Default(x, "edges", []any{}), 10_000) {
			e := r.Object(v, []string{"input", "to"}, "outputs", "disposition", "checks", "step_error", "check_error")
			item := edge{r.Text(e["input"]), r.Text(e["to"]), r.Strings(j.Default(e, "outputs", []any{}), 4096), readDisposition(r, j.Default(e, "disposition", map[string]any{"kind": "accepted"})), readChecks(r, j.Default(e, "checks", []any{})), r.Bool(j.Default(e, "step_error", false)), r.Bool(j.Default(e, "check_error", false))}
			if old, ok := seen[item.input]; ok && !reflect.DeepEqual(old, item) {
				r.Fail("conflicting transition")
			}
			seen[item.input] = item
			entry.edges = append(entry.edges, item)
			count++
		}
		t.rows[id] = entry
	}
	if count > 10_000 {
		r.Fail("model edge limit")
	}
	if _, ok := t.rows[t.initial]; !ok {
		r.Fail("unknown initial state")
	}
	for _, row := range t.rows {
		for _, e := range row.edges {
			if _, ok := t.rows[e.to]; !ok {
				r.Fail("unknown target state")
			}
		}
	}
	if r.Err != nil {
		return nil, r.Err
	}
	return t, nil
}
func (t *table) row(state string) (row, error) {
	r, ok := t.rows[state]
	if !ok {
		return row{}, fmt.Errorf("unknown state")
	}
	return r, nil
}
func (t *table) edge(state, input string) (edge, error) {
	r, e := t.row(state)
	if e != nil {
		return edge{}, e
	}
	for _, x := range r.edges {
		if x.input == input {
			return x, nil
		}
	}
	return edge{}, fmt.Errorf("input is not in this state's domain")
}
func (t *table) model() s.Model[string, string, string] {
	return s.Model[string, string, string]{
		Metadata: t.metadata,
		InitialState: func() (string, error) {
			if t.initialError {
				return "", fmt.Errorf("injected initial_state")
			}
			return t.initial, nil
		},
		Step: func(state, input string) (s.Transition[string, string], error) {
			t.calls++
			e, err := t.edge(state, input)
			if err != nil {
				return s.Transition[string, string]{}, err
			}
			if e.stepError {
				return s.Transition[string, string]{}, fmt.Errorf("injected step")
			}
			return s.Transition[string, string]{State: e.to, Outputs: e.outputs, Disposition: e.disposition}, nil
		},
		CheckState: func(state string) ([]s.Check, error) {
			r, e := t.row(state)
			if e != nil {
				return nil, e
			}
			if r.checkError {
				return nil, fmt.Errorf("injected check_state")
			}
			return r.checks, nil
		},
		CheckTransition: func(state, input string, _ s.Transition[string, string]) ([]s.Check, error) {
			e, err := t.edge(state, input)
			if err != nil {
				return nil, err
			}
			if e.checkError {
				return nil, fmt.Errorf("injected check_transition")
			}
			return e.checks, nil
		},
		CloneState: s.ValueCopy[string], CloneInput: s.ValueCopy[string], CloneOutput: s.ValueCopy[string],
		EqualStates: func(a, b string) (bool, error) { return a == b, nil }, HashState: func(string) (uint64, error) { return 0, nil },
		Inputs: func(state string) (s.Iterator[string], error) {
			r, e := t.row(state)
			if e != nil {
				return s.Iterator[string]{}, e
			}
			if r.inputsError {
				return s.Iterator[string]{}, fmt.Errorf("injected inputs")
			}
			values := make([]string, len(r.edges))
			for i, e := range r.edges {
				values[i] = e.input
			}
			return s.Values(values), nil
		},
		Codec: &s.Codec[string, string, string]{
			EncodeState: func(state string) ([]byte, error) { _, err := t.row(state); return []byte(state), err },
			DecodeState: func(b []byte) (string, error) {
				if !utf8.Valid(b) {
					return "", fmt.Errorf("invalid UTF-8")
				}
				state := string(b)
				_, e := t.row(state)
				return state, e
			},
			EncodeInput: func(i string) ([]byte, error) { return []byte(i), nil },
			DecodeInput: func(b []byte) (string, error) {
				if !utf8.Valid(b) {
					return "", fmt.Errorf("invalid UTF-8")
				}
				return string(b), nil
			},
			EncodeOutput: func(o string) ([]byte, error) { return []byte(o), nil },
		},
	}
}
func Execute(value any) (any, error) {
	r := &j.Reader{}
	o, ok := value.(map[string]any)
	if !ok {
		return nil, &j.Error{Message: "expected request"}
	}
	version := r.Integer(o["version"], (1<<53)-1)
	if r.Err != nil {
		return nil, r.Err
	}
	if version != 1 {
		return map[string]any{"error": "unsupported_version"}, nil
	}
	op := r.Text(o["operation"])
	if r.Err != nil {
		return nil, r.Err
	}
	if op == "hello" {
		r.Object(value, []string{"version", "operation"})
		if r.Err != nil {
			return nil, r.Err
		}
		return map[string]any{"implementation": "go-rust-abi2", "engine_family": "rust-shared", "abi_version": 2, "package_version": "0.1.0", "spec_version": "1.0.0", "profiles": []string{"core-v1", "bfs-v1", "rng-splitmix64-v1", "trace-json-v1"}, "protocol_version": 1}, nil
	}
	if op == "rng" {
		r.Object(value, []string{"version", "operation", "seed", "draws", "bounds"})
		seed := r.U64(o["seed"])
		draws := r.Integer(o["draws"], 10_000)
		a := r.Array(o["bounds"], 10_000)
		bounds := make([]uint64, len(a))
		for i, x := range a {
			bounds[i] = r.U64(x)
		}
		if r.Err != nil {
			return nil, r.Err
		}
		rng := s.RNG{State: seed}
		raw := make([]string, int(draws))
		for i := range raw {
			v, err := rng.Next()
			if err != nil {
				return nil, err
			}
			raw[i] = strconv.FormatUint(v, 10)
		}
		indices := make([]any, len(bounds))
		for i, b := range bounds {
			n, ok, err := rng.Index(b)
			if err != nil {
				return nil, err
			}
			if ok {
				indices[i] = strconv.FormatUint(n, 10)
			}
		}
		next, err := rng.Next()
		if err != nil {
			return nil, err
		}
		return map[string]any{"raw": raw, "indices": indices, "next": strconv.FormatUint(next, 10)}, nil
	}
	extra := map[string][]string{"enumerate": {"config"}, "record": {"inputs", "max_steps"}, "observe": {"before", "input", "transition", "sequence", "policy"}, "replay": {"trace", "allow_build_mismatch"}}
	required, ok := extra[op]
	if !ok {
		return map[string]any{"error": "unsupported_operation"}, nil
	}
	r.Object(value, append([]string{"version", "operation", "model"}, required...))
	if r.Err != nil {
		return nil, r.Err
	}
	table, err := parseTable(o["model"])
	if err != nil {
		return nil, err
	}
	var result any
	err = s.WithSession(table.model(), s.DefaultOptions(), func(session *s.Session[string, string, string]) error {
		result, err = executeModel(session, table, op, o, r)
		return err
	})
	return result, err
}
func executeModel(session *s.Session[string, string, string], table *table, op string, o map[string]any, r *j.Reader) (any, error) {
	switch op {
	case "enumerate":
		c := r.Object(o["config"], []string{"max_states", "max_transitions", "max_depth"})
		maxStates := r.Integer(c["max_states"], maxWork)
		maxTransitions := r.Integer(c["max_transitions"], maxWork)
		maxDepth := r.Integer(c["max_depth"], maxWork)
		if r.Err != nil {
			return nil, r.Err
		}
		if maxStates == 0 {
			return map[string]any{"error": "invalid_config"}, nil
		}
		result, err := session.Enumerate(s.Bounds{States: maxStates, Transitions: maxTransitions, Depth: maxDepth})
		if err != nil {
			return nil, err
		}
		defer result.Close()
		var failure any
		if result.Status == s.PropertyFailed {
			violations := make([]any, len(result.Checks))
			phases := []string{"", "initial_state", "state", "transition"}
			for i, check := range result.Checks {
				violations[i] = map[string]any{"phase": phases[result.CheckPhases[i]], "check": check}
			}
			failure = map[string]any{"inputs": result.Inputs, "violations": violations}
		}
		names := []string{"graph_exhausted", "depth_bound", "state_limit", "transition_limit", "failure_found", "estimated_state_bytes", "deadline", "cancelled"}
		return map[string]any{"termination": names[result.Termination], "states": result.States, "transitions": result.Transitions, "max_depth_reached": result.Depth, "skipped_checks": result.Skipped, "failure": failure}, nil
	case "record":
		inputs := r.Strings(o["inputs"], maxWork)
		limit := r.Integer(o["max_steps"], maxWork)
		if r.Err != nil {
			return nil, r.Err
		}
		trace, err := session.RecordPortable(inputs, limit, s.DefaultTraceLimits())
		if err != nil {
			return nil, err
		}
		return map[string]any{"trace": s.TraceData(trace)}, nil
	case "observe":
		before, input := r.Text(o["before"]), r.Text(o["input"])
		t := r.Object(o["transition"], []string{"state", "outputs", "disposition"})
		transition := s.Transition[string, string]{State: r.Text(t["state"]), Outputs: r.Strings(t["outputs"], 4096), Disposition: readDisposition(r, t["disposition"])}
		p := r.Object(o["policy"], []string{"state_every", "transition_checks"})
		sequence := r.Integer(o["sequence"], maxWork)
		every := r.Integer(p["state_every"], maxWork)
		enabled := r.Bool(p["transition_checks"])
		if r.Err != nil {
			return nil, r.Err
		}
		if every == 0 || sequence == 0 {
			return map[string]any{"error": "invalid_config"}, nil
		}
		checks, err := session.CheckObserved(before, input, transition, sequence, s.CheckPolicy{StateEvery: every, TransitionChecks: enabled})
		if err != nil {
			return nil, err
		}
		defer checks.Close()
		return map[string]any{"checks": checks.Checks, "step_calls": table.calls}, nil
	default:
		trace, err := s.TraceFromData(o["trace"], s.DefaultTraceLimits())
		if err != nil {
			return nil, err
		}
		allow := r.Bool(o["allow_build_mismatch"])
		if r.Err != nil {
			return nil, r.Err
		}
		packet, err := s.TracePacket(trace)
		if err != nil {
			return nil, err
		}
		artifact, err := session.BinaryTrace(packet)
		if err != nil {
			return nil, err
		}
		result, err := session.Replay(artifact, allow)
		if err != nil {
			return nil, err
		}
		defer result.Close()
		outcome := "exact"
		if result.Status == s.Diverged {
			outcome = "diverged"
		}
		if result.Status == s.Incompatible {
			outcome = "incompatible"
		}
		var step, field any
		if result.HasDivergenceStep {
			step = result.DivergenceStep
		}
		if result.DivergenceField != "" {
			field = result.DivergenceField
		}
		return map[string]any{"outcome": outcome, "steps_verified": result.StepsVerified, "failure_reproduced": result.FailureReproduced, "build_matches": result.BuildMatches, "step": step, "field": field}, nil
	}
}
func Response(raw []byte) any {
	value, err := j.Strict(raw, MaxLine)
	if err == nil {
		value, err = Execute(value)
	}
	if err == nil {
		return value
	}
	var modelError *s.Error
	if errors.As(err, &modelError) && (modelError.Status == 11 || modelError.Status == 12) {
		return map[string]any{"error": "model_error"}
	}
	return map[string]any{"error": "invalid_request"}
}

// Run is synchronous, byte-bounded, and recovers at each newline after oversized
// input. Non-JSON diagnostics are never written to the output stream.
func Run(input io.Reader, output io.Writer) error {
	reader := bufio.NewReader(input)
	encoder := json.NewEncoder(output)
	raw := []byte{}
	oversized := false
	for {
		chunk, err := reader.ReadSlice('\n')
		if !oversized {
			if len(raw)+len(chunk) > MaxLine {
				oversized = true
				raw = nil
			} else {
				raw = append(raw, chunk...)
			}
		}
		if err == bufio.ErrBufferFull {
			continue
		}
		if err != nil && err != io.EOF {
			return err
		}
		if len(raw) > 0 || oversized {
			var response any
			if oversized {
				response = map[string]any{"error": "invalid_request"}
			} else {
				response = Response(raw)
			}
			if e := encoder.Encode(response); e != nil {
				return e
			}
		}
		raw = nil
		oversized = false
		if err == io.EOF {
			return nil
		}
	}
}

func readCheck(r *j.Reader, v any) s.Check {
	c, e := s.CheckFromData(v)
	if e != nil {
		r.Fail(e.Error())
	}
	return c
}
func readDisposition(r *j.Reader, v any) s.Disposition {
	d, e := s.DispositionFromData(v)
	if e != nil {
		r.Fail(e.Error())
	}
	return d
}
func readMetadata(r *j.Reader, v any) s.Metadata {
	m, e := s.MetadataFromData(v)
	if e != nil {
		r.Fail(e.Error())
	}
	return m
}

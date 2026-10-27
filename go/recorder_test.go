package statelessness_test

import (
	"bytes"
	"encoding/json"
	"errors"
	s "github.com/Jagalite/statelessness/go"
	"math"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

func TestRuntimeRecorderCheckedRingAndOwnership(t *testing.T) {
	m := auditRecordingModel()
	states, edges, steps := 0, 0, 0
	m.Step = func(int, int) (s.Transition[int, string], error) { steps++; panic("must not execute") }
	m.CheckState = func(int) ([]s.Check, error) { states++; return []s.Check{s.Passed("state")}, nil }
	m.CheckTransition = func(int, int, s.Transition[int, string]) ([]s.Check, error) {
		edges++
		return []s.Check{s.Passed("edge")}, nil
	}
	initial, e := s.CheckInitial(m, 5)
	if e != nil {
		t.Fatal(e)
	}
	o := s.DefaultRecorderOptions()
	o.MaxSteps = 1
	r, e := s.NewRecorderWithCheckedInitialAt(initial, 9, s.RunConfig{Strategy: "manual"}, o)
	if e != nil {
		t.Fatal(e)
	}
	other, e := s.NewRecorderWithCheckedInitialAt(initial, 9, s.RunConfig{Strategy: "manual"}, o)
	if e != nil {
		t.Fatal(e)
	}
	turn, e := s.CheckTurn(m, 5, 1, s.Accept(6, "o"), 10, s.FullChecks())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.ObserveCheckedRecorder(r, turn); e != nil {
		t.Fatal(e)
	}
	if _, e = s.ObserveCheckedRecorder(other, turn); e != nil {
		t.Fatal(e)
	}
	turn, e = s.CheckTurn(m, 6, 1, s.Accept(7, "o"), 11, s.FullChecks())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.ObserveCheckedRecorder(r, turn); e != nil {
		t.Fatal(e)
	}
	if steps != 0 || states != 3 || edges != 2 {
		t.Fatalf("callbacks %d %d %d", steps, states, edges)
	}
	trace := r.Snapshot()
	if r.ObservedSteps() != 2 || r.EvictedSteps() != 1 || r.RetainedSteps() != 1 || !reflect.DeepEqual(trace.InitialChecks, []s.Check{s.Passed("state")}) || trace.InitialState[0] != 6 {
		t.Fatalf("bad retained window %+v", trace)
	}
	for _, p := range trace.Config.Parameters {
		if p[0] == "stateless.monitor.checkpoint_after_sequence" && p[1] != "1" {
			t.Fatal(p)
		}
	}
	var b bytes.Buffer
	if _, e = r.WriteTo(&b); e != nil {
		t.Fatal(e)
	}
	if uint64(b.Len()) != r.RetainedBytes() {
		t.Fatal("size mismatch")
	}
	trace.InitialState[0] = 99
	trace.Steps[0].Checks[0] = s.Failed("mutated", "")
	trace.Config.Parameters[0][1] = "mutated"
	if r.Snapshot().InitialState[0] != 6 || r.Snapshot().Steps[0].Checks[0].Status != "passed" {
		t.Fatal("snapshot aliases")
	}
	if _, e = s.ObserveCheckedRecorder(r, turn); e == nil || !r.IsFrozen() {
		t.Fatal("duplicate accepted")
	}
	assertAuditWritable(t, r.Snapshot(), o.Limits)
}
func TestRuntimeRecorderRejectsHandoffsAndKeepsPrefix(t *testing.T) {
	for _, kind := range []string{"sampled", "gap", "overflow", "metadata", "continuity", "forged"} {
		t.Run(kind, func(t *testing.T) {
			m := auditRecordingModel()
			initial, e := s.CheckInitial(m, 0)
			if e != nil {
				t.Fatal(e)
			}
			origin := uint64(0)
			if kind == "overflow" {
				origin = math.MaxUint64
			}
			r, e := s.NewRecorderWithCheckedInitialAt(initial, origin, s.RunConfig{Strategy: "manual"}, s.DefaultRecorderOptions())
			if e != nil {
				t.Fatal(e)
			}
			sequence := uint64(1)
			before := 0
			policy := s.FullChecks()
			if kind == "sampled" {
				policy.StateEvery = 2
			}
			if kind == "gap" {
				sequence = 2
			}
			if kind == "metadata" {
				m.Codec.Metadata = func() (s.Metadata, error) { return s.Identity("other", "b"), nil }
			}
			if kind == "continuity" {
				before = 7
			}
			turn, e := s.CheckTurn(m, before, 1, s.Accept(1, "o"), sequence, policy)
			if e != nil {
				t.Fatal(e)
			}
			if kind == "forged" {
				turn = new(s.CheckedTurn[int, int, string])
			}
			if _, e = s.ObserveCheckedRecorder(r, turn); e == nil || !r.IsFrozen() {
				t.Fatal("accepted invalid handoff")
			}
			if r.ObservedSteps() != 0 || r.RetainedSteps() != 0 {
				t.Fatal("incoherent prefix")
			}
			reason := r.Snapshot().Error
			r.StopWithError(errors.New("later"))
			if r.Snapshot().Error != reason {
				t.Fatal("overwrote error")
			}
			assertAuditWritable(t, r.Snapshot(), s.DefaultBinaryLimits())
		})
	}
}
func TestRuntimeRecorderOrdinaryFailuresAndInitialFreeze(t *testing.T) {
	m := auditRecordingModel()
	calls := 0
	m.CheckTransition = func(int, int, s.Transition[int, string]) ([]s.Check, error) {
		calls++
		return []s.Check{s.Failed("edge", "bad")}, nil
	}
	r, e := s.NewRecorder(m, 0, s.RunConfig{Strategy: "manual"}, 2)
	if e != nil {
		t.Fatal(e)
	}
	checks, e := s.ObserveRecorder(r, m, 0, 1, s.Accept(1, "o"))
	if e != nil || len(checks) != 2 || calls != 1 || !r.IsFrozen() || r.Snapshot().Termination != "property_failed" {
		t.Fatalf("%+v %v", checks, e)
	}
	r.StopWithError(errors.New("later"))
	if r.Snapshot().Termination != "property_failed" {
		t.Fatal("overwrote property failure")
	}
	m.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Failed("initial", "")}, nil }
	initial, e := s.CheckInitial(m, 0)
	if e != nil {
		t.Fatal(e)
	}
	m.CheckState = func(int) ([]s.Check, error) { panic("rechecked") }
	r, e = s.NewRecorderWithCheckedInitial(initial, s.RunConfig{Strategy: "manual"}, s.DefaultRecorderOptions())
	if e != nil || !r.IsFrozen() {
		t.Fatalf("%v", e)
	}
}
func TestRuntimeRecorderEncodingErrorIsAtomicAndBounded(t *testing.T) {
	m := auditRecordingModel()
	r, e := s.NewRecorder(m, 0, s.RunConfig{Strategy: "manual"}, 2)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.ObserveRecorder(r, m, 0, 1, s.Accept(1, "o")); e != nil {
		t.Fatal(e)
	}
	m.Codec.EncodeOutput = func(string) ([]byte, error) { return nil, errors.New(strings.Repeat("é", 200)) }
	if _, e = s.ObserveRecorder(r, m, 1, 1, s.Accept(2, "o")); e == nil {
		t.Fatal("expected encode error")
	}
	if r.ObservedSteps() != 1 || r.Snapshot().Steps[0].PostState[0] != 1 || len(r.Snapshot().Error) > 256 {
		t.Fatal("bad prefix")
	}
	assertAuditWritable(t, r.Snapshot(), s.DefaultBinaryLimits())
}
func TestRuntimeRecorderOptionsAndReservedParameters(t *testing.T) {
	m := auditRecordingModel()
	o := s.DefaultRecorderOptions()
	o.MaxSteps = 0
	if _, e := s.NewRecorderWithOptions(m, 0, s.RunConfig{}, o); e == nil {
		t.Fatal("zero steps")
	}
	o = s.DefaultRecorderOptions()
	o.MaxRetainedBytes = 1
	if _, e := s.NewRecorderWithOptions(m, 0, s.RunConfig{}, o); e == nil {
		t.Fatal("tiny budget")
	}
	for _, prefix := range []string{"stateless.monitor.fake", "stateless.engine.fake"} {
		if _, e := s.NewRecorder(m, 0, s.RunConfig{Parameters: [][2]string{{prefix, "x"}}}, 1); e == nil {
			t.Fatal("reserved accepted")
		}
	}
}

type recorderCorpusModel struct {
	StateChecks      []s.Check `json:"state_checks"`
	TransitionChecks []s.Check `json:"transition_checks"`
	StateError       *string   `json:"state_error"`
	TransitionError  *string   `json:"transition_error"`
}

func TestRuntimeRecorderSharedCorpus(t *testing.T) {
	path := os.Getenv("STATELESS_CHECKED_CORPUS")
	if path == "" {
		path = "../conformance/checked-results-v1.json"
	}
	data, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct {
		Cases []struct {
			Case         string
			Model        recorderCorpusModel
			InitialState int `json:"initial_state"`
			Origin       string
			Config       s.RunConfig
			EntryMode    string `json:"entry_mode"`
			Options      struct {
				MaxSteps         int    `json:"max_steps"`
				MaxRetainedBytes string `json:"max_retained_bytes"`
				Limits           map[string]json.RawMessage
			}
			Actions []struct {
				Sequence      string
				Before, Input int
				Transition    s.Transition[int, int]
				Policy        struct {
					StateEvery       string `json:"state_every"`
					TransitionChecks bool   `json:"transition_checks"`
				}
				ModelName      string               `json:"model_name"`
				ChecksModel    *recorderCorpusModel `json:"checks_model"`
				EncodeError    string               `json:"encode_error"`
				StopError      *string              `json:"stop_error"`
				Mode           string
				Representation string `json:"representation"`
			}
			Expected struct {
				InitialError *string `json:"initial_error_contains"`
				States       []json.RawMessage
			}
		} `json:"recorder_cases"`
	}
	if e = json.Unmarshal(data, &corpus); e != nil {
		t.Fatal(e)
	}
	if len(corpus.Cases) < 29 {
		t.Fatal("missing recorder cases")
	}
	parse := func(v string) uint64 {
		n, e := strconv.ParseUint(v, 10, 64)
		if e != nil {
			t.Fatal(e)
		}
		return n
	}
	for _, c := range corpus.Cases {
		t.Run(c.Case, func(t *testing.T) {
			checksCalls := 0
			makeModel := func(cm recorderCorpusModel, name, encodeError string) s.Model[int, int, int] {
				m := s.Model[int, int, int]{CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], InitialState: func() (int, error) { t.Fatal("initial called"); return 0, nil }, Step: func(int, int) (s.Transition[int, int], error) {
					t.Fatal("step called")
					return s.Accept[int, int](0), nil
				}}
				m.CheckState = func(int) ([]s.Check, error) {
					checksCalls++
					if cm.StateError != nil {
						return nil, errors.New(*cm.StateError)
					}
					return cm.StateChecks, nil
				}
				m.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) {
					checksCalls++
					if cm.TransitionError != nil {
						return nil, errors.New(*cm.TransitionError)
					}
					return cm.TransitionChecks, nil
				}
				encoder := func(kind string) func(int, *s.EncodeBuffer) error {
					return func(v int, b *s.EncodeBuffer) error {
						if encodeError == kind {
							return errors.New("fixture encode " + kind)
						}
						return b.Append([]byte(strconv.Itoa(v)))
					}
				}
				if name == "" {
					name = "same"
				}
				m.Codec = &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity(name, "fixture"), nil }, EncodeStateInto: encoder("state"), EncodeInputInto: encoder("input"), EncodeOutputInto: encoder("output"), DecodeState: func(b []byte) (int, error) { return strconv.Atoi(string(b)) }, DecodeInput: func(b []byte) (int, error) { return strconv.Atoi(string(b)) }}
				return m
			}
			limits := s.DefaultBinaryLimits()
			for k, raw := range c.Options.Limits {
				var v uint64
				if len(raw) > 0 && raw[0] == '"' {
					var str string
					json.Unmarshal(raw, &str)
					v = parse(str)
				} else {
					if e = json.Unmarshal(raw, &v); e != nil {
						t.Fatal(e)
					}
				}
				switch k {
				case "max_total_bytes":
					limits.MaxTotalBytes = v
				case "max_frame_bytes":
					limits.MaxFrameBytes = v
				case "max_blob_bytes":
					limits.MaxBlobBytes = v
				case "max_string_bytes":
					limits.MaxStringBytes = v
				case "max_steps":
					limits.MaxSteps = v
				case "max_checks_per_batch":
					limits.MaxChecksPerStep = v
				case "max_outputs_per_step":
					limits.MaxOutputsPerStep = v
				case "max_parameters":
					limits.MaxParameters = v
				case "max_items":
					limits.MaxItems = v
				default:
					t.Fatal(k)
				}
			}
			o := s.RecorderOptions{MaxSteps: c.Options.MaxSteps, MaxRetainedBytes: parse(c.Options.MaxRetainedBytes), Limits: limits}
			m := makeModel(c.Model, "", "")
			var r *s.Recorder
			var err error
			if c.EntryMode == "ordinary" {
				r, err = s.NewRecorderWithOptions(m, c.InitialState, c.Config, o)
			} else {
				initial, e := s.CheckInitial(m, c.InitialState)
				if e != nil {
					t.Fatal(e)
				}
				before := checksCalls
				r, err = s.NewRecorderWithCheckedInitialAt(initial, parse(c.Origin), c.Config, o)
				if checksCalls != before {
					t.Fatal("initial rechecked")
				}
			}
			if c.Expected.InitialError != nil {
				if err == nil || !strings.Contains(err.Error(), *c.Expected.InitialError) {
					t.Fatalf("initial error=%v want %q", err, *c.Expected.InitialError)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			for ai, a := range c.Actions {
				if a.StopError != nil {
					r.StopWithError(errors.New(*a.StopError))
					err = nil
				} else {
					cm := c.Model
					if a.ChecksModel != nil {
						cm = *a.ChecksModel
					}
					am := makeModel(cm, a.ModelName, a.EncodeError)
					if a.Representation == "decimal-string" {
						stringModel := s.Model[string, string, string]{CloneState: s.ValueCopy[string], CloneInput: s.ValueCopy[string], CloneOutput: s.ValueCopy[string], CheckState: func(string) ([]s.Check, error) {
							checksCalls++
							if cm.StateError != nil {
								return nil, errors.New(*cm.StateError)
							}
							return cm.StateChecks, nil
						}, CheckTransition: func(string, string, s.Transition[string, string]) ([]s.Check, error) {
							checksCalls++
							if cm.TransitionError != nil {
								return nil, errors.New(*cm.TransitionError)
							}
							return cm.TransitionChecks, nil
						}}
						encoder := func(kind string) func(string, *s.EncodeBuffer) error {
							return func(value string, b *s.EncodeBuffer) error {
								if a.EncodeError == kind {
									return errors.New("fixture encode " + kind)
								}
								return b.Append([]byte(value))
							}
						}
						stringModel.Codec = &s.Codec[string, string, string]{Metadata: am.Codec.Metadata, EncodeStateInto: encoder("state"), EncodeInputInto: encoder("input"), EncodeOutputInto: encoder("output"), DecodeState: func(b []byte) (string, error) { return string(b), nil }, DecodeInput: func(b []byte) (string, error) { return string(b), nil }}
						tr := s.Transition[string, string]{State: strconv.Itoa(a.Transition.State), Disposition: a.Transition.Disposition, Outputs: []string{}}
						for _, output := range a.Transition.Outputs {
							tr.Outputs = append(tr.Outputs, strconv.Itoa(output))
						}
						if a.Mode == "ordinary" {
							_, err = s.ObserveRecorder(r, stringModel, strconv.Itoa(a.Before), strconv.Itoa(a.Input), tr)
						} else {
							token, e := s.CheckTurn(stringModel, strconv.Itoa(a.Before), strconv.Itoa(a.Input), tr, parse(a.Sequence), s.CheckPolicy{StateEvery: parse(a.Policy.StateEvery), TransitionChecks: a.Policy.TransitionChecks})
							if e != nil {
								t.Fatal(e)
							}
							before := checksCalls
							_, err = s.ObserveCheckedRecorder(r, token)
							if checksCalls != before {
								t.Fatal("string turn rechecked")
							}
						}
					} else if a.Representation != "" {
						t.Fatalf("unknown representation %q", a.Representation)
					} else if a.Mode == "ordinary" {
						_, err = s.ObserveRecorder(r, am, a.Before, a.Input, a.Transition)
					} else {
						turn, e := s.CheckTurn(am, a.Before, a.Input, a.Transition, parse(a.Sequence), s.CheckPolicy{StateEvery: parse(a.Policy.StateEvery), TransitionChecks: a.Policy.TransitionChecks})
						if e != nil {
							t.Fatal(e)
						}
						before := checksCalls
						_, err = s.ObserveCheckedRecorder(r, turn)
						if checksCalls != before {
							t.Fatal("turn rechecked")
						}
					}
				}
				var expected map[string]any
				if e = json.Unmarshal(c.Expected.States[ai], &expected); e != nil {
					t.Fatal(e)
				}
				wantError := expected["error_contains"]
				if wantError == nil {
					if err != nil {
						t.Fatal(err)
					}
				} else if err == nil || !strings.Contains(err.Error(), wantError.(string)) {
					t.Fatalf("action %d error=%v want %v", ai, err, wantError)
				}
				tr := r.Snapshot()
				initial, e := strconv.Atoi(string(tr.InitialState))
				if e != nil {
					t.Fatal(e)
				}
				steps := []any{}
				for _, step := range tr.Steps {
					state, _ := strconv.Atoi(string(step.PostState))
					input, _ := strconv.Atoi(string(step.Input))
					outputs := []int{}
					for _, b := range step.Outputs {
						v, e := strconv.Atoi(string(b))
						if e != nil {
							t.Fatal(e)
						}
						outputs = append(outputs, v)
					}
					steps = append(steps, map[string]any{"input": input, "transition": map[string]any{"state": state, "outputs": outputs, "disposition": step.Disposition}, "checks": step.Checks})
				}
				actual := map[string]any{"observed_steps": strconv.FormatUint(r.ObservedSteps(), 10), "evicted_steps": strconv.FormatUint(r.EvictedSteps(), 10), "retained_steps": r.RetainedSteps(), "frozen": r.IsFrozen(), "termination": tr.Termination, "initial_state": initial, "initial_checks": tr.InitialChecks, "steps": steps}
				delete(expected, "error_contains")
				for _, step := range expected["steps"].([]any) {
					delete(step.(map[string]any), "state_check_count")
				}
				encoded, _ := json.Marshal(actual)
				var normalized any
				json.Unmarshal(encoded, &normalized)
				if !reflect.DeepEqual(normalized, expected) {
					t.Fatalf("action %d got %s expected %s", ai, encoded, c.Expected.States[ai])
				}
				assertAuditWritable(t, tr, limits)
			}
		})
	}
}

func TestRuntimeRecorderAcceptsEquivalentDifferentRepresentation(t *testing.T) {
	m := auditRecordingModel()
	initial, e := s.CheckInitial(m, 0)
	if e != nil {
		t.Fatal(e)
	}
	r, e := s.NewRecorderWithCheckedInitial(initial, s.RunConfig{Strategy: "manual"}, s.DefaultRecorderOptions())
	if e != nil {
		t.Fatal(e)
	}
	clone := func(v []byte) []byte { return append([]byte{}, v...) }
	alternate := s.Model[[]byte, []byte, []byte]{CloneState: clone, CloneInput: clone, CloneOutput: clone, CheckState: func([]byte) ([]s.Check, error) { return []s.Check{s.Passed("c")}, nil }, Codec: &s.Codec[[]byte, []byte, []byte]{Metadata: func() (s.Metadata, error) { return s.Identity("m", "b"), nil }, EncodeState: func(v []byte) ([]byte, error) { return clone(v), nil }, EncodeInput: func(v []byte) ([]byte, error) { return clone(v), nil }, EncodeOutput: func(v []byte) ([]byte, error) { return clone(v), nil }, DecodeState: func(v []byte) ([]byte, error) { return clone(v), nil }, DecodeInput: func(v []byte) ([]byte, error) { return clone(v), nil }}}
	turn, e := s.CheckTurn(alternate, []byte{0}, []byte{1}, s.Accept([]byte{1}, []byte("o")), 1, s.FullChecks())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.ObserveCheckedRecorder(r, turn); e != nil {
		t.Fatal(e)
	}
	if _, e = s.ObserveRecorder(r, m, 1, 1, s.Accept(2, "o")); e != nil {
		t.Fatal(e)
	}
	if r.ObservedSteps() != 2 || r.Snapshot().Steps[1].PostState[0] != 2 {
		t.Fatal("cross-representation handoff failed")
	}
	assertAuditWritable(t, r.Snapshot(), s.DefaultBinaryLimits())
}

type recorderReentrantError struct{ run func() }

func (e recorderReentrantError) Error() string { e.run(); return "first cause" }
func TestRuntimeRecorderRejectsReentryAndPreservesFirstStop(t *testing.T) {
	for _, stage := range []string{"metadata", "before", "checker", "output", "post"} {
		t.Run(stage, func(t *testing.T) {
			m := auditRecordingModel()
			r, e := s.NewRecorder(m, 0, s.RunConfig{Strategy: "manual"}, 2)
			if e != nil {
				t.Fatal(e)
			}
			first := errors.New("first stop")
			nested := func() {
				if _, e := s.ObserveRecorder(r, m, 0, 1, s.Accept(1, "o")); e == nil {
					t.Fatal("nested observation accepted")
				}
				if r.IntoTrace() != nil {
					t.Fatal("nested transfer accepted")
				}
				r.StopWithError(first)
			}
			switch stage {
			case "metadata":
				m.Codec.Metadata = func() (s.Metadata, error) { nested(); return s.Identity("m", "b"), nil }
			case "before", "post":
				m.Codec.EncodeState = func(v int) ([]byte, error) {
					if stage == "before" && v == 0 || stage == "post" && v == 1 {
						nested()
					}
					return []byte{byte(v)}, nil
				}
			case "checker":
				m.CheckState = func(int) ([]s.Check, error) { nested(); return []s.Check{s.Passed("c")}, nil }
			case "output":
				m.Codec.EncodeOutput = func(string) ([]byte, error) { nested(); return nil, errors.New("later codec failure") }
			}
			if _, e = s.ObserveRecorder(r, m, 0, 1, s.Accept(1, "o")); e != first {
				t.Fatalf("first error lost: %v", e)
			}
			if r.ObservedSteps() != 0 || r.RetainedSteps() != 0 || r.Snapshot().Error != "first stop" {
				t.Fatal("reentrant stop committed or overwritten")
			}
			assertAuditWritable(t, r.Snapshot(), s.DefaultBinaryLimits())
		})
	}
}
func TestRuntimeRecorderGuardsExternalErrorFormatter(t *testing.T) {
	m := auditRecordingModel()
	r, e := s.NewRecorder(m, 0, s.RunConfig{Strategy: "manual"}, 2)
	if e != nil {
		t.Fatal(e)
	}
	called := 0
	first := recorderReentrantError{run: func() {
		called++
		if r.IntoTrace() != nil {
			t.Fatal("formatter transferred")
		}
		if _, e := s.ObserveRecorder(r, m, 0, 1, s.Accept(1, "o")); e == nil {
			t.Fatal("formatter observed")
		}
		r.StopWithError(errors.New("later"))
		assertAuditWritable(t, r.Snapshot(), s.DefaultBinaryLimits())
	}}
	r.StopWithError(first)
	if called != 1 || r.Snapshot().Error != "first cause" || r.ObservedSteps() != 0 {
		t.Fatal("formatter reentry corrupted first cause")
	}
}

type recorderReentrantWriter struct {
	bytes.Buffer
	run func()
}

func (w *recorderReentrantWriter) Write(v []byte) (int, error) {
	if w.run != nil {
		run := w.run
		w.run = nil
		run()
	}
	return w.Buffer.Write(v)
}
func TestRuntimeRecorderWriterReentryCannotChangeAdmittedArtifact(t *testing.T) {
	m := auditRecordingModel()
	r, e := s.NewRecorder(m, 0, s.RunConfig{Strategy: "manual"}, 2)
	if e != nil {
		t.Fatal(e)
	}
	limits := s.DefaultBinaryLimits()
	expected, e := s.MarshalAuditTrace(r.Snapshot(), limits)
	if e != nil {
		t.Fatal(e)
	}
	limits.MaxTotalBytes = uint64(len(expected))
	first := errors.New("writer stop")
	w := &recorderReentrantWriter{run: func() {
		if _, e := s.ObserveRecorder(r, m, 0, 1, s.Accept(1, "o")); e == nil {
			t.Fatal("writer observed")
		}
		if r.IntoTrace() != nil {
			t.Fatal("writer transferred")
		}
		if e := r.StopWithError(first); e == first {
			t.Fatal("writer stop was accepted")
		}
	}}
	if e = r.WriteWithLimits(w, limits); e != nil {
		t.Fatal(e)
	}
	if !bytes.Equal(w.Bytes(), expected) {
		t.Fatal("writer reentry changed admitted artifact")
	}
	if r.ObservedSteps() != 0 || r.IsFrozen() {
		t.Fatal("writer mutation changed recorder")
	}
	if _, e = s.ParseAuditTrace(w.Bytes(), limits); e != nil {
		t.Fatal(e)
	}
}

func TestRuntimeRecorderWriterPanicReleasesExportBorrow(t *testing.T) {
	m := auditRecordingModel()
	r, e := s.NewRecorder(m, 0, s.RunConfig{Strategy: "manual"}, 2)
	if e != nil {
		t.Fatal(e)
	}
	w := &recorderReentrantWriter{run: func() { panic("writer panic") }}
	func() {
		defer func() {
			if recover() == nil {
				t.Fatal("expected writer panic")
			}
		}()
		r.WriteWithLimits(w, s.DefaultBinaryLimits())
	}()
	if _, e = s.ObserveRecorder(r, m, 0, 1, s.Accept(1, "o")); e != nil {
		t.Fatalf("export guard remained active: %v", e)
	}
}

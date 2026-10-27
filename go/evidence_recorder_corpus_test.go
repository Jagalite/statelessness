package statelessness_test

import (
	"encoding/json"
	"errors"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

type causalRecorderContext struct {
	Machine, Correlation *string
	Generation           *evidenceWireUint
	LogicalTime          *evidenceWireUint `json:"logical_time"`
	Causes               []struct {
		Sequence evidenceWireUint
		Output   uint32
	}
}

func (c causalRecorderContext) value() s.CausalContext {
	v := s.CausalContext{Machine: c.Machine, Correlation: c.Correlation, Causes: []s.OutputOrigin{}}
	if c.Generation != nil {
		n := uint64(*c.Generation)
		v.Generation = &n
	}
	if c.LogicalTime != nil {
		n := uint64(*c.LogicalTime)
		v.LogicalTime = &n
	}
	for _, o := range c.Causes {
		v.Causes = append(v.Causes, s.OutputOrigin{Sequence: uint64(o.Sequence), Output: o.Output})
	}
	return v
}
func TestCausalEvidenceRecorderSharedCorpus(t *testing.T) {
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
			Case  string
			Model struct {
				Name, Build string
				StateDelta  int     `json:"state_delta"`
				OutputDelta int     `json:"output_delta"`
				PhaseShift  bool    `json:"phase_shift"`
				ErrorAt     *string `json:"error_at"`
				FailAt      *int    `json:"fail_at"`
			}
			InitialState int `json:"initial_state"`
			Origin       evidenceWireUint
			Config       s.RunConfig
			Options      struct {
				MaxSteps         int              `json:"max_steps"`
				MaxRetainedBytes evidenceWireUint `json:"max_retained_bytes"`
				Limits           map[string]uint64
			}
			Provenance json.RawMessage
			Limits     map[string]json.RawMessage
			Actions    []struct {
				Sequence      evidenceWireUint
				Before, Input int
				Transition    s.Transition[int, int]
				Policy        struct {
					StateEvery       evidenceWireUint `json:"state_every"`
					TransitionChecks bool             `json:"transition_checks"`
				}
				Causal      causalRecorderContext
				EncodeError string `json:"encode_error"`
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
	if len(corpus.Cases) < 20 {
		t.Fatal("missing evidence recorder cases")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Case, func(t *testing.T) {
			m := causalReplayModel(c.Model.Name, c.Model.Build, c.Model.StateDelta, c.Model.OutputDelta, c.Model.PhaseShift, c.Model.ErrorAt)
			m.Step = func(int, int) (s.Transition[int, int], error) {
				t.Fatal("recorder executed reducer")
				return s.Accept[int, int](0), nil
			}
			if c.Model.FailAt != nil {
				m.CheckState = func(state int) ([]s.Check, error) {
					if state >= *c.Model.FailAt {
						return []s.Check{s.Failed("s", "at threshold")}, nil
					}
					return []s.Check{s.Passed("s")}, nil
				}
			}
			calls := 0
			stateCheck := m.CheckState
			edgeCheck := m.CheckTransition
			m.CheckState = func(v int) ([]s.Check, error) { calls++; return stateCheck(v) }
			m.CheckTransition = func(b, i int, tr s.Transition[int, int]) ([]s.Check, error) { calls++; return edgeCheck(b, i, tr) }
			initial, e := s.CheckInitial(m, c.InitialState)
			if e != nil {
				t.Fatal(e)
			}
			var p evidenceWireValue
			if e = json.Unmarshal(c.Provenance, &p.Provenance); e != nil {
				t.Fatal(e)
			}
			provenance := s.Provenance{Run: p.Provenance.Run, Epoch: uint64(p.Provenance.Epoch), Manifest: p.Provenance.Manifest, Source: p.Provenance.Source, Environment: p.Provenance.Environment}
			limits := evidenceWireLimits(t, c.Limits)
			o := s.RecorderOptions{MaxSteps: c.Options.MaxSteps, MaxRetainedBytes: uint64(c.Options.MaxRetainedBytes), Limits: binaryLimits(c.Options.Limits)}
			beforeCalls := calls
			r, e := s.NewEvidenceRecorder(initial, uint64(c.Origin), c.Config, o, provenance, limits)
			if calls != beforeCalls {
				t.Fatal("initial rechecked")
			}
			if c.Expected.InitialError != nil {
				if e == nil || !strings.Contains(e.Error(), *c.Expected.InitialError) {
					t.Fatalf("initial %v want %q", e, *c.Expected.InitialError)
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			for index, a := range c.Actions {
				am := m
				if a.EncodeError != "" {
					copyCodec := *m.Codec
					copyCodec.EncodeOutputInto = func(int, *s.EncodeBuffer) error { return errors.New("fixture encode output") }
					am.Codec = &copyCodec
				}
				turn, e := s.CheckTurn(am, a.Before, a.Input, a.Transition, uint64(a.Sequence), s.CheckPolicy{StateEvery: uint64(a.Policy.StateEvery), TransitionChecks: a.Policy.TransitionChecks})
				if e != nil {
					t.Fatal(e)
				}
				beforeCalls = calls
				e = s.ObserveEvidenceRecorder(r, turn, a.Causal.value())
				if calls != beforeCalls {
					t.Fatal("turn rechecked")
				}
				var expected map[string]any
				json.Unmarshal(c.Expected.States[index], &expected)
				want := expected["error_contains"]
				if want == nil {
					if e != nil {
						t.Fatal(e)
					}
				} else if e == nil || !strings.Contains(e.Error(), want.(string)) {
					t.Fatalf("action %d error %v want %v", index, e, want)
				}
				snapshot := r.Snapshot()
				if e = snapshot.Validate(limits); e != nil {
					t.Fatal(e)
				}
				steps := []any{}
				for _, step := range snapshot.Trace.Steps {
					input, _ := strconv.Atoi(string(step.Input))
					post, _ := strconv.Atoi(string(step.PostState))
					outputs := []int{}
					for _, o := range step.Outputs {
						n, e := strconv.Atoi(string(o))
						if e != nil {
							t.Fatal(e)
						}
						outputs = append(outputs, n)
					}
					steps = append(steps, map[string]any{"input": input, "post_state": post, "outputs": outputs, "checks": step.Checks})
				}
				initialState, _ := strconv.Atoi(string(snapshot.Trace.InitialState))
				observed, evicted := "", ""
				for _, p := range snapshot.Trace.Config.Parameters {
					if p[0] == "stateless.monitor.observed_steps" {
						observed = p[1]
					}
					if p[0] == "stateless.monitor.evicted_steps" {
						evicted = p[1]
					}
				}
				actual := map[string]any{"checkpoint_after": fmt.Sprint(snapshot.CheckpointAfter), "initial_state": initialState, "initial_checks": snapshot.Trace.InitialChecks, "steps": steps, "evidence_steps": normalizeEvidenceSteps(snapshot.Steps), "observed_steps": observed, "evicted_steps": evicted, "frozen": r.IsFrozen(), "termination": snapshot.Trace.Termination}
				delete(expected, "error_contains")
				bytes, _ := json.Marshal(actual)
				var normalized any
				json.Unmarshal(bytes, &normalized)
				if !reflect.DeepEqual(normalized, expected) {
					t.Fatalf("action %d got %s want %s", index, bytes, c.Expected.States[index])
				}
			}
		})
	}
}

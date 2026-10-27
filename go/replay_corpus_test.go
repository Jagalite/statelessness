package statelessness_test

import (
	"encoding/hex"
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

type replayFixture struct {
	Case  string
	Trace binaryValue
	Model struct {
		StateDelta          int `json:"state_delta"`
		OutputDelta         int `json:"output_delta"`
		OutputCount         int `json:"output_count"`
		OutputPadding       int `json:"output_padding"`
		Disposition         string
		DispositionReason   *string      `json:"disposition_reason"`
		InitialCheck        *binaryCheck `json:"initial_check"`
		TurnStateCheck      *binaryCheck `json:"turn_state_check"`
		TurnTransitionCheck *binaryCheck `json:"turn_transition_check"`
		ErrorAt             *string      `json:"error_at"`
		Name, Build         string
	}
	Bounded            bool
	Limits             map[string]uint64
	ObserverErrorAt    *string `json:"observer_error_at"`
	AllowBuildMismatch bool    `json:"allow_build_mismatch"`
	ErrorContains      *string `json:"error_contains"`
	Expected           json.RawMessage
	Events             []string
}

func replayNormalizedReport(r s.ReplayReport) any {
	return map[string]any{"outcome": r.Outcome, "divergence_step": r.Step, "divergence_field": r.Field, "steps_verified": r.StepsVerified, "failure_reproduced": r.FailureReproduced, "build_matches": r.BuildMatches}
}
func replayNormalizedDisposition(d s.Disposition) any {
	return map[string]any{"kind": d.Kind, "reason": d.Reason}
}
func replayNormalizedTransition(t s.Transition[int, int]) any {
	return map[string]any{"state": t.State, "outputs": t.Outputs, "disposition": replayNormalizedDisposition(t.Disposition)}
}
func replayNormalizedDiff(d s.ReplayDifferences) any {
	return map[string]any{"disposition": d.Disposition, "outputs": d.Outputs, "state": d.State, "checks": d.Checks}
}
func replayNormalizedTurn(a s.ReplayTurnActual[int, int, int]) map[string]any {
	var checks any
	if a.StateCheckCount != nil {
		checks = a.Checks
	}
	return map[string]any{"kind": "turn", "sequence": a.Sequence, "before": a.Before, "input": a.Input, "transition": replayNormalizedTransition(a.Transition), "checks": checks, "state_check_count": a.StateCheckCount}
}
func replayNormalizedActual(a *s.ReplayActual[int, int, int]) any {
	if a == nil {
		return nil
	}
	if a.Initial != nil {
		var checks any
		if a.Initial.ChecksComplete {
			checks = a.Initial.Checks
		}
		return map[string]any{"kind": "initial", "sequence": 0, "state": a.Initial.State, "checks": checks}
	}
	return replayNormalizedTurn(*a.Turn)
}
func replayHexes(values [][]byte) []string {
	out := make([]string, len(values))
	for i, v := range values {
		out[i] = hex.EncodeToString(v)
	}
	return out
}
func replayNormalizedStep(v s.TraceStep) any {
	return map[string]any{"input": hex.EncodeToString(v.Input), "disposition": replayNormalizedDisposition(v.Disposition), "outputs": replayHexes(v.Outputs), "post_state": hex.EncodeToString(v.PostState), "checks": v.Checks}
}
func TestReplayEvidenceSharedCorpus(t *testing.T) {
	path := os.Getenv("STATELESS_REPLAY_CORPUS")
	if path == "" {
		path = "../conformance/replay-evidence-v1.json"
	}
	data, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct{ Cases []replayFixture }
	if e = json.Unmarshal(data, &corpus); e != nil {
		t.Fatal(e)
	}
	if len(corpus.Cases) == 0 {
		t.Fatal("empty replay corpus")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Case, func(t *testing.T) {
			events := []string{}
			event := func(name string) error {
				events = append(events, name)
				if c.Model.ErrorAt != nil && *c.Model.ErrorAt == name {
					return fmt.Errorf("fixture codec replay error")
				}
				return nil
			}
			check := func(custom *binaryCheck, fallback s.Check) []s.Check {
				if custom == nil {
					return []s.Check{fallback}
				}
				return binaryChecks([]binaryCheck{*custom})
			}
			m := s.Model[int, int, int]{CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], InitialState: func() (int, error) { t.Fatal("replay called InitialState"); return 0, nil }}
			m.Step = func(before, input int) (s.Transition[int, int], error) {
				if e := event(fmt.Sprintf("step:%d:%d", before, input)); e != nil {
					return s.Transition[int, int]{}, e
				}
				base := before + input
				out := make([]int, c.Model.OutputCount)
				for k := range out {
					out[k] = base + c.Model.OutputDelta
				}
				d := s.Accepted()
				if c.Model.Disposition != "accepted" {
					reason := "why"
					if c.Model.DispositionReason != nil {
						reason = *c.Model.DispositionReason
					}
					d = s.Disposition{Kind: c.Model.Disposition, Reason: reason}
				}
				return s.Transition[int, int]{State: base + c.Model.StateDelta, Outputs: out, Disposition: d}, nil
			}
			m.CheckState = func(state int) ([]s.Check, error) {
				if e := event(fmt.Sprintf("check_state:%d", state)); e != nil {
					return nil, e
				}
				if state == 0 {
					return check(c.Model.InitialCheck, s.Passed("ok")), nil
				}
				return check(c.Model.TurnStateCheck, s.Passed("ok")), nil
			}
			m.CheckTransition = func(before, input int, tr s.Transition[int, int]) ([]s.Check, error) {
				if e := event(fmt.Sprintf("check_transition:%d:%d:%d", before, input, tr.State)); e != nil {
					return nil, e
				}
				return check(c.Model.TurnTransitionCheck, s.Passed("edge")), nil
			}
			encode := func(kind string, value int, b *s.EncodeBuffer) error {
				if e := event(fmt.Sprintf("encode_%s:%d", kind, value)); e != nil {
					return e
				}
				text := strconv.Itoa(value)
				if kind == "output" {
					text += strings.Repeat("x", c.Model.OutputPadding)
				}
				return b.Append([]byte(text))
			}
			decode := func(kind string, b []byte) (int, error) {
				if e := event("decode_" + kind + ":" + hex.EncodeToString(b)); e != nil {
					return 0, e
				}
				return strconv.Atoi(string(b))
			}
			legacy := func(int) ([]byte, error) { t.Fatal("legacy codec called despite into hook"); return nil, nil }
			m.Codec = &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) {
				if e := event("metadata"); e != nil {
					return s.Metadata{}, e
				}
				return s.Identity(c.Model.Name, c.Model.Build), nil
			}, EncodeState: legacy, EncodeInput: legacy, EncodeOutput: legacy, DecodeState: func(b []byte) (int, error) { return decode("state", b) }, DecodeInput: func(b []byte) (int, error) { return decode("input", b) }, EncodeStateInto: func(v int, b *s.EncodeBuffer) error { return encode("state", v, b) }, EncodeInputInto: func(v int, b *s.EncodeBuffer) error { return encode("input", v, b) }, EncodeOutputInto: func(v int, b *s.EncodeBuffer) error { return encode("output", v, b) }}
			observations := []any{}
			observe := func(key string) error {
				if c.ObserverErrorAt != nil && *c.ObserverErrorAt == key {
					return fmt.Errorf("replay observer fixture")
				}
				return nil
			}
			observer := s.ReplayObserver[int, int, int]{Initial: func(o s.ReplayInitialObservation[int]) error {
				events = append(events, "observe_initial")
				observations = append(observations, map[string]any{"kind": "initial", "sequence": 0, "state": o.Actual.State, "checks": o.Actual.Checks, "expected_state": hex.EncodeToString(o.ExpectedState), "actual_state": hex.EncodeToString(o.ActualState), "expected_checks": o.ExpectedChecks, "differences": replayNormalizedDiff(o.Differences), "report": replayNormalizedReport(o.Report)})
				return observe("initial")
			}, Turn: func(o s.ReplayTurnObservation[int, int, int]) error {
				events = append(events, fmt.Sprintf("observe_turn:%d", o.Actual.Sequence))
				v := replayNormalizedTurn(o.Actual)
				v["expected"] = replayNormalizedStep(o.Expected)
				v["actual_input"] = hex.EncodeToString(o.ActualInput)
				v["actual_outputs"] = replayHexes(o.ActualOutputs)
				v["actual_state"] = hex.EncodeToString(o.ActualState)
				v["differences"] = replayNormalizedDiff(o.Differences)
				v["report"] = replayNormalizedReport(o.Report)
				observations = append(observations, v)
				return observe(fmt.Sprintf("turn:%d", o.Actual.Sequence))
			}, Error: func(o s.ReplayErrorObservation[int, int, int]) error {
				events = append(events, fmt.Sprintf("observe_error:%d", o.Sequence))
				observations = append(observations, map[string]any{"kind": "error", "sequence": o.Sequence, "actual": replayNormalizedActual(o.Actual), "report": replayNormalizedReport(o.Report)})
				return observe("error")
			}}
			audit := binaryAudit(t, c.Trace)
			var report *s.ReplayReport
			var err error
			if c.Bounded {
				report, err = s.ReplayWithObservationsBounded(m, audit, c.AllowBuildMismatch, binaryLimits(c.Limits), observer)
			} else {
				report, err = s.ReplayWithObservations(m, &audit.Trace, c.AllowBuildMismatch, observer)
			}
			var actual any
			secondary := false
			if err != nil {
				var re *s.ReplayError[int, int, int]
				if !errors.As(err, &re) {
					t.Fatalf("unexpected error type %T: %v", err, err)
				}
				actual = replayNormalizedActual(re.Actual)
				secondary = re.ObserverError != nil
			}
			if report == nil {
				t.Fatal("missing report")
			}
			got := map[string]any{"report": replayNormalizedReport(*report), "observations": observations, "error": err != nil, "actual": actual, "observer_error": secondary}
			gotJSON, e := json.Marshal(got)
			if e != nil {
				t.Fatal(e)
			}
			var gotValue, expectedValue any
			if e = json.Unmarshal(gotJSON, &gotValue); e != nil {
				t.Fatal(e)
			}
			if e = json.Unmarshal(c.Expected, &expectedValue); e != nil {
				t.Fatal(e)
			}
			if !reflect.DeepEqual(gotValue, expectedValue) {
				t.Fatalf("result mismatch\ngot %s\nwant %s", gotJSON, c.Expected)
			}
			if !reflect.DeepEqual(events, c.Events) {
				t.Fatalf("events mismatch\ngot %v\nwant %v", events, c.Events)
			}
			if c.ErrorContains != nil && (err == nil || !strings.Contains(err.Error(), *c.ErrorContains)) {
				t.Fatalf("error %v must contain %q", err, *c.ErrorContains)
			}
		})
	}
}

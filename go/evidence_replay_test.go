package statelessness_test

import (
	"encoding/json"
	"errors"
	s "github.com/Jagalite/statelessness/go"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

func causalReplayModel(name, build string, stateDelta, outputDelta int, phaseShift bool, errorAt *string) s.Model[int, int, int] {
	event := func(v string) error {
		if errorAt != nil && *errorAt == v {
			return errors.New("fixture causal replay error")
		}
		return nil
	}
	m := s.Model[int, int, int]{CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], InitialState: func() (int, error) { panic("replay must not call initial") }}
	m.Step = func(before, input int) (s.Transition[int, int], error) {
		if e := event("step"); e != nil {
			return s.Transition[int, int]{}, e
		}
		return s.Accept(before+input+stateDelta, before+input+outputDelta), nil
	}
	m.CheckState = func(state int) ([]s.Check, error) {
		if e := event("state_check"); e != nil {
			return nil, e
		}
		if phaseShift && state != 0 {
			return []s.Check{}, nil
		}
		return []s.Check{s.Passed("s")}, nil
	}
	m.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) {
		if e := event("transition_check"); e != nil {
			return nil, e
		}
		if phaseShift {
			return []s.Check{s.Passed("s"), s.Passed("t")}, nil
		}
		return []s.Check{s.Passed("t")}, nil
	}
	enc := func(int) ([]byte, error) { panic("allocating encoder selected") }
	into := func(v int, b *s.EncodeBuffer) error { return b.Append([]byte(strconv.Itoa(v))) }
	m.Codec = &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) {
		if e := event("metadata"); e != nil {
			return s.Metadata{}, e
		}
		return s.Identity(name, build), nil
	}, EncodeState: enc, EncodeInput: enc, EncodeOutput: enc, EncodeStateInto: into, EncodeInputInto: into, EncodeOutputInto: into, DecodeState: func(b []byte) (int, error) { return strconv.Atoi(string(b)) }, DecodeInput: func(b []byte) (int, error) { return strconv.Atoi(string(b)) }}
	return m
}
func normalizeEvidenceSteps(v []s.StepEvidence) []any {
	out := []any{}
	for _, step := range v {
		causes := []any{}
		for _, c := range step.Causal.Causes {
			causes = append(causes, map[string]any{"sequence": strconv.FormatUint(c.Sequence, 10), "output": c.Output})
		}
		var generation, logical any
		if step.Causal.Generation != nil {
			generation = strconv.FormatUint(*step.Causal.Generation, 10)
		}
		if step.Causal.LogicalTime != nil {
			logical = strconv.FormatUint(*step.Causal.LogicalTime, 10)
		}
		out = append(out, map[string]any{"sequence": strconv.FormatUint(step.Sequence, 10), "state_check_count": step.StateCheckCount, "causal": map[string]any{"machine": step.Causal.Machine, "generation": generation, "correlation": step.Causal.Correlation, "logical_time": logical, "causes": causes}})
	}
	return out
}
func TestCausalReplayAndUpgradeSharedCorpus(t *testing.T) {
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
			Case, Operation string
			Evidence        evidenceWireValue
			Model           struct {
				Name, Build string
				StateDelta  int     `json:"state_delta"`
				OutputDelta int     `json:"output_delta"`
				PhaseShift  bool    `json:"phase_shift"`
				ErrorAt     *string `json:"error_at"`
			}
			AllowBuildMismatch bool `json:"allow_build_mismatch"`
			Expected           struct {
				ErrorContains *string `json:"error_contains"`
				Report        json.RawMessage
				Steps         json.RawMessage
			}
		} `json:"replay_cases"`
	}
	if e = json.Unmarshal(data, &corpus); e != nil {
		t.Fatal(e)
	}
	if len(corpus.Cases) == 0 {
		t.Fatal("empty causal replay corpus")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Case, func(t *testing.T) {
			v := evidenceWireConvert(t, c.Evidence)
			m := causalReplayModel(c.Model.Name, c.Model.Build, c.Model.StateDelta, c.Model.OutputDelta, c.Model.PhaseShift, c.Model.ErrorAt)
			var report *s.ReplayReport
			var upgraded *s.TraceV2
			var err error
			switch c.Operation {
			case "replay":
				report, err = s.ReplayTraceV2(m, v, c.AllowBuildMismatch, s.DefaultEvidenceLimits())
			case "upgrade":
				upgraded, err = s.VerifyUpgrade(m, &v.Trace, v.Provenance, v.CheckpointAfter, c.AllowBuildMismatch, s.DefaultEvidenceLimits())
			default:
				t.Fatal(c.Operation)
			}
			if c.Expected.ErrorContains != nil {
				if err == nil || !strings.Contains(err.Error(), *c.Expected.ErrorContains) {
					t.Fatalf("error=%v expected %s", err, *c.Expected.ErrorContains)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if report != nil {
				encoded, _ := json.Marshal(replayNormalizedReport(*report))
				var got, want any
				json.Unmarshal(encoded, &got)
				json.Unmarshal(c.Expected.Report, &want)
				if !reflect.DeepEqual(got, want) {
					t.Fatalf("report=%s expected=%s", encoded, c.Expected.Report)
				}
			}
			if upgraded != nil {
				encoded, _ := json.Marshal(normalizeEvidenceSteps(upgraded.Steps))
				var got, want any
				json.Unmarshal(encoded, &got)
				json.Unmarshal(c.Expected.Steps, &want)
				if !reflect.DeepEqual(got, want) {
					t.Fatalf("steps=%s expected=%s", encoded, c.Expected.Steps)
				}
				if e := upgraded.Validate(s.DefaultEvidenceLimits()); e != nil {
					t.Fatal(e)
				}
			}
		})
	}
}

func TestCausalUpgradeAndReplayCaptureEntryOwnership(t *testing.T) {
	m := causalReplayModel("causal-fixture", "candidate-v1", 0, 0, false, nil)
	m.InitialState = func() (int, error) { return 0, nil }
	trace, e := s.RecordAudit(m, s.Values([]int{1}), s.RunConfig{Strategy: "manual"}, 2, s.DefaultBinaryLimits())
	if e != nil {
		t.Fatal(e)
	}
	source := "original"
	p := s.Provenance{Run: "run", Source: &source, Environment: [][2]string{{"env", "original"}}}
	originalMetadata := m.Codec.Metadata
	m.Codec.Metadata = func() (s.Metadata, error) {
		source = "changed"
		p.Environment[0][1] = "changed"
		trace.Steps[0].Input[0] = '9'
		return originalMetadata()
	}
	upgraded, e := s.VerifyUpgrade(m, trace, p, 0, false, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	if *upgraded.Provenance.Source != "original" || upgraded.Provenance.Environment[0][1] != "original" || string(upgraded.Trace.Steps[0].Input) != "1" {
		t.Fatal("upgrade captured later caller mutation")
	}
	m.Codec.Metadata = func() (s.Metadata, error) { upgraded.Steps[0].StateCheckCount = 0; return originalMetadata() }
	report, e := s.ReplayTraceV2(m, upgraded, false, s.DefaultEvidenceLimits())
	if e != nil || report.Outcome != "exact" {
		t.Fatalf("replay phase context mutated: report=%+v error=%v", report, e)
	}
}

func TestArtifactSnapshotAndLegacyPhasesAreIndependent(t *testing.T) {
	m := causalReplayModel("causal-fixture", "candidate-v1", 0, 0, false, nil)
	m.InitialState = func() (int, error) { return 0, nil }
	trace, e := s.RecordAudit(m, s.Values([]int{1}), s.RunConfig{Strategy: "manual"}, 2, s.DefaultBinaryLimits())
	if e != nil {
		t.Fatal(e)
	}
	artifact := s.Artifact{V1: trace}
	if phase, ok := artifact.CheckPhase(0, 0); !ok || phase != s.PhaseInitialState {
		t.Fatal("v1 initial phase missing")
	}
	if _, ok := artifact.CheckPhase(1, 0); ok {
		t.Fatal("legacy phase inferred")
	}
	owned := artifact.Snapshot()
	owned.V1.InitialChecks[0].ID = "changed"
	owned.V1.Steps[0].Input[0] = '9'
	owned.V1.Config.Parameters[0][1] = "changed"
	if trace.InitialChecks[0].ID != "s" || string(trace.Steps[0].Input) != "1" || trace.Config.Parameters[0][1] != "0.1.0" {
		t.Fatal("artifact snapshot aliases")
	}
	if _, e = s.MarshalArtifact(s.Artifact{V1: trace, V2: &s.TraceV2{}}, s.DefaultEvidenceLimits()); e == nil {
		t.Fatal("ambiguous artifact accepted")
	}
}

func TestCausalUpgradeDeferredAdmissionPreservesIdentityAndCallbackOrder(t *testing.T) {
	base := causalReplayModel("causal-fixture", "candidate-v1", 0, 0, false, nil)
	base.InitialState = func() (int, error) { return 0, nil }
	trace, e := s.RecordAudit(base, s.Values([]int{1}), s.RunConfig{Strategy: "manual"}, 2, s.DefaultBinaryLimits())
	if e != nil {
		t.Fatal(e)
	}
	for _, foreign := range []bool{false, true} {
		m := base
		codec := *base.Codec
		m.Codec = &codec
		calls := 0
		m.Codec.Metadata = func() (s.Metadata, error) {
			calls++
			if foreign {
				return s.Identity("foreign", "candidate-v1"), nil
			}
			return s.Identity("causal-fixture", "candidate-v1"), nil
		}
		m.Codec.DecodeState = func([]byte) (int, error) { t.Fatal("unadmitted artifact decoded"); return 0, nil }
		limits := s.DefaultEvidenceLimits()
		limits.Trace.MaxBlobBytes = 0
		_, e = s.VerifyUpgrade(m, trace, s.Provenance{}, 0, false, limits)
		want := "bounded replay artifact"
		if foreign {
			want = "v1 upgrade requires exact compatible replay"
		}
		if e == nil || !strings.Contains(e.Error(), want) || calls != 1 {
			t.Fatalf("foreign=%t error=%v metadata calls=%d", foreign, e, calls)
		}
	}
	p := s.Provenance{Environment: [][2]string{{"duplicate", "1"}, {"duplicate", "2"}}}
	m := base
	codec := *base.Codec
	m.Codec = &codec
	calls, steps := 0, 0
	m.Codec.Metadata = func() (s.Metadata, error) {
		calls++
		p.Environment[1][0] = "fixed later"
		return s.Identity("causal-fixture", "candidate-v1"), nil
	}
	m.Step = func(b, i int) (s.Transition[int, int], error) { steps++; return s.Accept(b+i, b+i), nil }
	_, e = s.VerifyUpgrade(m, trace, p, 0, false, s.DefaultEvidenceLimits())
	if e == nil || !strings.Contains(e.Error(), "empty or duplicate evidence environment name") || calls != 1 || steps != 1 {
		t.Fatalf("deferred entryerror=%v metadata=%d steps=%d", e, calls, steps)
	}
}

package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"os"
	"strconv"
)

func must(e error) {
	if e != nil {
		panic(e)
	}
}
func require(v bool, why string) {
	if !v {
		panic(why)
	}
}

type valueOutput struct {
	Code    uint8
	Message string
}
type modelState = []s.Option[uint64]
type modelStats struct{ initial, step, stateChecks, edgeChecks, legacy, into int }

func modelLimits() s.DecodeLimits {
	return s.DecodeLimits{Bytes: 128, Allocation: 128, Elements: 16, Depth: 8, Work: 64}
}
func application() (s.Model[modelState, s.Option[uint64], valueOutput], *modelStats) {
	c := &modelStats{}
	option, e := s.OptionOf(s.Uint64Codec())
	must(e)
	layout, e := s.NewValueLayout(16)
	must(e)
	option, e = option.WithLayout(layout)
	must(e)
	state, e := s.VectorOf(option)
	must(e)
	outputFields, e := s.Tuple2Of(s.Uint8Codec(), s.StringCodec())
	must(e)
	output, e := s.NewValueCodec("application-output", func(v valueOutput, out *s.EncodeBuffer) error {
		return outputFields.EncodeRaw(s.Tuple2[uint8, string]{V0: v.Code, V1: v.Message}, out)
	}, nil, nil)
	must(e)
	old := func() ([]byte, error) { c.legacy++; return nil, fmt.Errorf("legacy encoder") }
	model := s.Model[modelState, s.Option[uint64], valueOutput]{InitialState: func() (modelState, error) { c.initial++; return modelState{}, nil }, CloneState: func(v modelState) modelState { return append(modelState{}, v...) }, CloneInput: s.ValueCopy[s.Option[uint64]], CloneOutput: s.ValueCopy[valueOutput], EqualStates: func(a, b modelState) bool {
		if len(a) != len(b) {
			return false
		}
		for i := range a {
			if a[i] != b[i] {
				return false
			}
		}
		return true
	},
		Step: func(before modelState, input s.Option[uint64]) (s.Transition[modelState, valueOutput], error) {
			c.step++
			next := append(append(modelState{}, before...), input)
			return s.Accept[modelState, valueOutput](next, valueOutput{7, "\ufeffchanged"}), nil
		},
		CheckState: func(modelState) ([]s.Check, error) { return nil, fmt.Errorf("legacy state checker") }, CheckStateInto: func(v modelState, out *s.CheckSink) error {
			c.stateChecks++
			sum := uint64(0)
			for _, x := range v {
				if x.Present {
					if x.Value >= 3 {
						sum = 3
						break
					}
					sum += x.Value
					if sum >= 3 {
						break
					}
				}
			}
			check := s.Passed("threshold.bound")
			if sum >= 3 {
				check = s.Failed("threshold.bound", "at least three")
			}
			must(out.Push(check))
			return out.Push(s.Passed("state.appended"))
		}, CheckTransitionInto: func(_ modelState, _ s.Option[uint64], _ s.Transition[modelState, valueOutput], out *s.CheckSink) error {
			c.edgeChecks++
			return out.Push(s.Passed("edge.checked"))
		},
		Codec: &s.Codec[modelState, s.Option[uint64], valueOutput]{Metadata: func() (s.Metadata, error) { return s.Identity("native-values-threshold", "application-v1"), nil }, EncodeState: func(modelState) ([]byte, error) { return old() }, EncodeInput: func(s.Option[uint64]) ([]byte, error) { return old() }, EncodeOutput: func(valueOutput) ([]byte, error) { return old() },
			EncodeStateInto: func(v modelState, out *s.EncodeBuffer) error { c.into++; return state.EncodeInto(v, out) }, EncodeInputInto: func(v s.Option[uint64], out *s.EncodeBuffer) error { c.into++; return option.EncodeInto(v, out) }, EncodeOutputInto: func(v valueOutput, out *s.EncodeBuffer) error {
				c.into++
				return output.EncodeInto(v, out)
			}, DecodeState: func(b []byte) (modelState, error) { return s.FromValueBytes(state, b, modelLimits()) }, DecodeInput: func(b []byte) (s.Option[uint64], error) { return s.FromValueBytes(option, b, modelLimits()) }}}
	return model, c
}
func limits() s.BinaryLimits {
	return s.BinaryLimits{MaxTotalBytes: 16384, MaxFrameBytes: 4096, MaxBlobBytes: 64, MaxStringBytes: 256, MaxSteps: 16, MaxChecksPerStep: 16, MaxOutputsPerStep: 16, MaxParameters: 32, MaxItems: 256}
}
func el() s.EvidenceLimits {
	return s.EvidenceLimits{Trace: limits(), MaxMetadataBytes: 1024, MaxLabels: 8, MaxLabelBytes: 128, MaxCausesPerStep: 4, MaxTotalCauses: 8}
}
func strp(v string) *string { return &v }
func up(v uint64) *uint64   { return &v }
func provenance() s.Provenance {
	return s.Provenance{Run: "installed-values-run", Epoch: 1, Manifest: strp("canonical-value-codec-v1"), Source: strp("consumer-source"), Environment: [][2]string{{"schema", "vec-option-u64"}}}
}
func causal(seq uint64) s.CausalContext {
	c := s.CausalContext{Machine: strp("worker-a"), Generation: up(9), Correlation: strp("request-1"), LogicalTime: up(10 + seq), Causes: []s.OutputOrigin{}}
	if seq > 1 {
		c.Causes = append(c.Causes, s.OutputOrigin{Sequence: seq - 1, Output: 0})
	}
	return c
}
func count(c *modelStats) map[string]int {
	return map[string]int{"initial_state": c.initial, "reducer": c.step, "state": c.stateChecks, "transition": c.edgeChecks}
}
func nullable(v string) any {
	if v == "" {
		return nil
	}
	return v
}
func uintJSON(v *uint64) any {
	if v == nil {
		return nil
	}
	return strconv.FormatUint(*v, 10)
}
func configView(c s.RunConfig) map[string]any {
	p := c.Parameters
	if p == nil {
		p = [][2]string{}
	}
	return map[string]any{"strategy": c.Strategy, "seed": uintJSON(c.Seed), "parameters": p}
}
func checksJSON(cs []s.Check) []map[string]any {
	out := []map[string]any{}
	for _, c := range cs {
		out = append(out, map[string]any{"id": c.ID, "status": c.Status, "reason": nullable(c.Details)})
	}
	return out
}
func norm(v *s.TraceV2) map[string]any {
	t := v.Trace
	steps := []map[string]any{}
	for _, x := range t.Steps {
		outputs := []string{}
		for _, b := range x.Outputs {
			outputs = append(outputs, hex.EncodeToString(b))
		}
		steps = append(steps, map[string]any{"input": hex.EncodeToString(x.Input), "disposition": map[string]any{"kind": x.Disposition.Kind, "reason": nullable(x.Disposition.Reason)}, "outputs": outputs, "post_state": hex.EncodeToString(x.PostState), "checks": checksJSON(x.Checks)})
	}
	rows := []map[string]any{}
	for _, x := range v.Steps {
		causes := []map[string]any{}
		for _, o := range x.Causal.Causes {
			causes = append(causes, map[string]any{"sequence": strconv.FormatUint(o.Sequence, 10), "output": o.Output})
		}
		rows = append(rows, map[string]any{"sequence": strconv.FormatUint(x.Sequence, 10), "state_check_count": x.StateCheckCount, "causal": map[string]any{"machine": x.Causal.Machine, "generation": uintJSON(x.Causal.Generation), "correlation": x.Causal.Correlation, "logical_time": uintJSON(x.Causal.LogicalTime), "causes": causes}})
	}
	env := v.Provenance.Environment
	if env == nil {
		env = [][2]string{}
	}
	return map[string]any{"trace": map[string]any{"metadata": t.Metadata, "config": configView(t.Config), "initial_state": hex.EncodeToString(t.InitialState), "initial_checks": checksJSON(t.InitialChecks), "steps": steps, "termination": map[string]any{"kind": t.Termination, "reason": nullable(t.Error)}}, "checkpoint_after": strconv.FormatUint(v.CheckpointAfter, 10), "provenance": map[string]any{"run": v.Provenance.Run, "epoch": strconv.FormatUint(v.Provenance.Epoch, 10), "manifest": v.Provenance.Manifest, "source": v.Provenance.Source, "environment": env}, "steps": rows}
}
func reportView(r *s.ReplayReport) map[string]any {
	return map[string]any{"outcome": r.Outcome, "divergence_step": r.Step, "divergence_field": r.Field, "steps_verified": r.StepsVerified, "failure_reproduced": r.FailureReproduced, "build_matches": r.BuildMatches}
}

func replayV(m s.Model[modelState, s.Option[uint64], valueOutput], v *s.TraceV2) map[string]any {
	r, e := s.ReplayTraceV2(m, v, false, el())
	must(e)
	return reportView(r)
}
func main() {
	loadContract()
	require(len(os.Args) >= 3, "missing operation/path")
	mode, path := os.Args[1], os.Args[2]
	var receipt map[string]any
	if mode == "values" {
		receipt = runValues(mode, "", path)
	} else if mode == "transcode" {
		require(len(os.Args) == 4, "missing output")
		receipt = runValues(mode, path, os.Args[3])
	} else {
		m, c := application()
		switch mode {
		case "record":
			initial, e := s.CheckInitial(m, modelState{})
			must(e)
			r, e := s.NewEvidenceRecorder(initial, 0, s.RunConfig{Strategy: "manual"}, s.RecorderOptions{MaxSteps: 3, MaxRetainedBytes: 16384, Limits: limits()}, provenance(), el())
			must(e)
			state := modelState{}
			for seq := 1; seq <= 3; seq++ {
				input := s.Option[uint64]{Present: true, Value: 1}
				turn, e := m.Step(state, input)
				must(e)
				checked, e := s.CheckTurn(m, state, input, turn, uint64(seq), s.CheckPolicy{StateEvery: 1, TransitionChecks: true})
				must(e)
				before := count(c)
				must(s.ObserveEvidenceRecorder(r, checked, causal(uint64(seq))))
				require(before["state"] == c.stateChecks && before["transition"] == c.edgeChecks && before["reducer"] == c.step, "handoff reexecuted")
				state = turn.State
			}
			v := r.Snapshot()
			mainCounts := count(c)
			require(c.initial == 0 && c.step == 3 && c.stateChecks == 4 && c.edgeChecks == 3, "check-once counts")
			must(v.ExportRawNew(path+".v2", el()))
			raw, e := s.MarshalAuditTrace(&v.Trace, limits())
			must(e)
			must(os.WriteFile(path+".v1", raw, 0600))
			portable, _, e := v.Trace.Portable()
			must(e)
			j, e := s.MarshalTrace(&portable)
			must(e)
			must(os.WriteFile(path+".json", j, 0600))
			receipt = map[string]any{"evidence": norm(v), "report": replayV(m, v), "retained_config": configView(v.Trace.Config), "main_counts": mainCounts, "output_encode_only": true}
		case "replay":
			require(len(os.Args) == 4, "missing output")
			data, e := os.ReadFile(path)
			must(e)
			a, e := s.ReadArtifact(bytes.NewReader(data), el())
			must(e)
			require(a.V2 != nil, "expected v2")
			v := a.V2
			must(v.ExportRawNew(os.Args[3], el()))
			receipt = map[string]any{"version": 2, "evidence": norm(v), "report": replayV(m, v), "retained_config": configView(v.Trace.Config)}
		default:
			panic("unknown operation")
		}
		receipt["legacy_encoder_calls"] = c.legacy
		receipt["codec_into_calls"] = c.into
	}
	receipt["producer"] = "go"
	receipt["mode"] = mode
	must(json.NewEncoder(os.Stdout).Encode(receipt))
}

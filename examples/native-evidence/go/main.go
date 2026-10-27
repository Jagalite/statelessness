package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"os"
	"strconv"
	"strings"
)

func must(err error) {
	if err != nil {
		panic(err)
	}
}

type counts struct{ legacy, decode, step, state, input, output, initial, stateChecks, edgeChecks int }

func application(variant string) (s.Model[int, int, string], *counts) {
	c := &counts{}
	legacyInt := func(int) ([]byte, error) { c.legacy++; return nil, fmt.Errorf("legacy encoder invoked") }
	intoInt := func(kind string) func(int, *s.EncodeBuffer) error {
		return func(v int, b *s.EncodeBuffer) error {
			if kind == "state" {
				c.state++
			} else {
				c.input++
			}
			for _, v := range []byte(strconv.Itoa(v)) {
				if err := b.Append([]byte{v}); err != nil {
					return err
				}
			}
			return nil
		}
	}
	m := s.Model[int, int, string]{InitialState: func() (int, error) { c.initial++; return 0, nil }, Inputs: func(int) (s.Iterator[int], error) { return s.Values([]int{0, 1}), nil }, EqualStates: func(a, b int) bool { return a == b }, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[string],
		Step: func(state, input int) (s.Transition[int, string], error) {
			c.step++
			if input != 0 && input != 1 {
				return s.Transition[int, string]{}, fmt.Errorf("expected zero or one")
			}
			if input == 0 {
				return s.Accept[int, string](state), nil
			}
			output := "changed"
			if variant == "overrun" {
				output = strings.Repeat("x", 65)
			}
			if variant == "diverged" {
				output = "different"
			}
			return s.Accept[int, string](state+input, output), nil
		},
		CheckState: func(int) ([]s.Check, error) { return nil, fmt.Errorf("legacy state check") },
		CheckTransition: func(int, int, s.Transition[int, string]) ([]s.Check, error) {
			return nil, fmt.Errorf("legacy edge check")
		},
		CheckStateInto: func(state int, sink *s.CheckSink) error {
			c.stateChecks++
			check := s.Passed("threshold.bound")
			if state >= 3 {
				check = s.Failed("threshold.bound", "at least three")
			}
			must(sink.Push(check))
			return sink.Push(s.Passed("state.appended"))
		},
		CheckTransitionInto: func(_ int, _ int, _ s.Transition[int, string], sink *s.CheckSink) error {
			c.edgeChecks++
			return sink.Push(s.Passed("edge.checked"))
		},
		Codec: &s.Codec[int, int, string]{Metadata: func() (s.Metadata, error) { return s.Identity("native-evidence-threshold", "application-v1"), nil }, EncodeState: legacyInt, EncodeInput: legacyInt, EncodeOutput: func(string) ([]byte, error) { c.legacy++; return nil, fmt.Errorf("legacy encoder invoked") },
			DecodeState: func(b []byte) (int, error) { c.decode++; return strconv.Atoi(string(b)) }, DecodeInput: func(b []byte) (int, error) {
				c.decode++
				if string(b) != "0" && string(b) != "1" {
					return 0, fmt.Errorf("expected zero or one")
				}
				return strconv.Atoi(string(b))
			}, EncodeStateInto: intoInt("state"), EncodeInputInto: intoInt("input"), EncodeOutputInto: func(v string, b *s.EncodeBuffer) error {
				c.output++
				for _, v := range []byte(v) {
					if err := b.Append([]byte{v}); err != nil {
						return err
					}
				}
				return nil
			}}}
	return m, c
}
func limits() s.BinaryLimits {
	return s.BinaryLimits{MaxTotalBytes: 16384, MaxFrameBytes: 4096, MaxBlobBytes: 64, MaxStringBytes: 256, MaxSteps: 16, MaxChecksPerStep: 16, MaxOutputsPerStep: 16, MaxParameters: 32, MaxItems: 256}
}

func strp(v string) *string { return &v }
func up(v uint64) *uint64   { return &v }
func el() s.EvidenceLimits {
	return s.EvidenceLimits{Trace: limits(), MaxMetadataBytes: 1024, MaxLabels: 8, MaxLabelBytes: 128, MaxCausesPerStep: 4, MaxTotalCauses: 8}
}
func provenance() s.Provenance {
	return s.Provenance{Run: "installed-evidence-run", Epoch: 7, Manifest: strp("manifest-v1"), Source: strp("consumer-source"), Environment: [][2]string{{"region", "test"}, {"note", "é"}}}
}
func causal(seq uint64, full bool) s.CausalContext {
	c := s.CausalContext{Causes: []s.OutputOrigin{}}
	if full {
		c.Machine = strp("worker-a")
		c.Generation = up(9)
		c.Correlation = strp("request-1")
		c.LogicalTime = up(10 + seq)
	}
	if seq > 1 {
		c.Causes = append(c.Causes, s.OutputOrigin{Sequence: seq - 1, Output: 0})
	}
	return c
}
func options(n int) s.RecorderOptions {
	return s.RecorderOptions{MaxSteps: n, MaxRetainedBytes: 16384, Limits: limits()}
}
func attach(m s.Model[int, int, string], n int, l s.EvidenceLimits, p s.Provenance) *s.EvidenceRecorder {
	initial, e := s.CheckInitial(m, 0)
	must(e)
	r, e := s.NewEvidenceRecorder(initial, 0, s.RunConfig{Strategy: "manual"}, options(n), p, l)
	must(e)
	return r
}
func token(m s.Model[int, int, string], before int, seq uint64) *s.CheckedTurn[int, int, string] {
	t, e := s.CheckTurn(m, before, 1, s.Accept[int, string](before+1, "changed"), seq, s.CheckPolicy{StateEvery: 1, TransitionChecks: true})
	must(e)
	return t
}
func count(c *counts) map[string]int {
	return map[string]int{"initial_state": c.initial, "reducer": c.step, "state": c.stateChecks, "transition": c.edgeChecks}
}
func require(v bool, why string) {
	if !v {
		panic(why)
	}
}
func rejected(e error, contains string) {
	require(e != nil, "expected rejection")
	if contains != "" {
		require(strings.Contains(strings.ToLower(e.Error()), contains), "wrong rejection: "+e.Error())
	}
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
func replayV(m s.Model[int, int, string], v *s.TraceV2, l s.EvidenceLimits) map[string]any {
	r, e := s.ReplayTraceV2(m, v, false, l)
	must(e)
	return reportView(r)
}
func policy() s.ExportPolicy {
	return s.ExportPolicy{MaxSteps: 2, MaxBytes: 4096, PropertyIDs: []string{"threshold.bound"}}
}
func controls(v *s.TraceV2, prefix string) (map[string]any, string) {
	for _, kind := range []string{"metadata", "cause", "trace"} {
		m, _ := application("")
		l := el()
		l.MaxMetadataBytes = 256
		n := 3
		if kind == "metadata" {
			l.MaxMetadataBytes = 70
		}
		if kind == "cause" {
			l.MaxTotalCauses = 1
		}
		if kind == "trace" {
			n = 1
		}
		r := attach(m, n, l, s.Provenance{})
		end := 3
		if kind == "trace" {
			end = 2
		}
		for seq := 1; seq <= end; seq++ {
			c := s.CausalContext{}
			if kind == "cause" {
				c = causal(uint64(seq), false)
			}
			must(s.ObserveEvidenceRecorder(r, token(m, seq-1, uint64(seq)), c))
		}
		x := r.Snapshot()
		must(x.Validate(l))
		cp := uint64(1)
		if kind == "cause" {
			cp = 2
		}
		require(x.CheckpointAfter == cp && len(x.Steps) == len(x.Trace.Steps) && len(x.Trace.InitialChecks) == 2, "coordinated eviction")
		for _, c := range x.Trace.InitialChecks {
			require(c.ID != "edge.checked", "edge promoted")
		}
		require(replayV(m, x, l)["outcome"] == "exact", "ring replay")
	}
	m, c := application("")
	l := el()
	l.MaxMetadataBytes = 62
	r := attach(m, 3, l, s.Provenance{})
	must(s.ObserveEvidenceRecorder(r, token(m, 0, 1), s.CausalContext{}))
	rejected(s.ObserveEvidenceRecorder(r, token(m, 1, 2), s.CausalContext{Causes: []s.OutputOrigin{{Sequence: 1, Output: 99}}}), "output")
	x := r.Snapshot()
	must(x.Validate(l))
	require(r.IsFrozen() && x.CheckpointAfter == 0 && len(x.Steps) == 1, "invalid source corrupted prefix")
	changed := v.Snapshot()
	changed.Steps[0].StateCheckCount = 1
	negative, e := s.ReplayTraceV2(m, changed, false, el())
	must(e)
	require(negative.Outcome == "diverged" && negative.Field != nil && *negative.Field == "check_phase" && negative.StepsVerified == 0 && !negative.FailureReproduced, "phase bypass")
	up, e := s.VerifyUpgrade(m, &v.Trace, provenance(), 0, false, el())
	must(e)
	require(replayV(m, up, el())["outcome"] == "exact", "upgrade replay")
	for _, row := range up.Steps {
		require(row.Causal.Machine == nil && len(row.Causal.Causes) == 0, "upgrade fabricated labels")
	}
	a := s.Artifact{V2: v}
	data, e := s.DiagnosticJSON(a, policy())
	must(e)
	fit := policy()
	fit.MaxBytes = uint64(len(data))
	exact, e := s.DiagnosticJSON(a, fit)
	must(e)
	require(bytes.Equal(exact, data), "diagnostic exact fit")
	fit.MaxBytes--
	_, e = s.DiagnosticJSON(a, fit)
	rejected(e, "")
	zero := policy()
	zero.MaxSteps = 0
	z, e := s.DiagnosticJSON(a, zero)
	must(e)
	var decoded map[string]any
	must(json.Unmarshal(z, &decoded))
	require(len(decoded["observations"].([]any)) == 1 && decoded["truncated"] == true, "diagnostic initial omitted")
	must(s.ExportDiagnosticNew(a, prefix+".diagnostic", policy()))
	rejected(s.ExportDiagnosticNew(a, prefix+".diagnostic", policy()), "")
	actual, e := os.ReadFile(prefix + ".diagnostic")
	must(e)
	require(bytes.Equal(actual, data), "diagnostic overwritten")
	rejected(v.ExportRawNew(prefix+".v2", el()), "")
	before := c.initial + c.step + c.stateChecks + c.edgeChecks
	legacyBytes, e := s.MarshalAuditTrace(&v.Trace, limits())
	must(e)
	legacy, e := s.ReadArtifact(bytes.NewReader(legacyBytes), el())
	must(e)
	phase, known := legacy.CheckPhase(0, 0)
	_, turnKnown := legacy.CheckPhase(1, 0)
	require(legacy.V1 != nil && known && phase == s.PhaseInitialState && !turnKnown && before == c.initial+c.step+c.stateChecks+c.edgeChecks, "passive v1 phase invention")
	return map[string]any{"check_once": true, "coordinated_metadata_eviction": true, "coordinated_cause_eviction": true, "coordinated_trace_eviction": true, "source_index_before_eviction": true, "phase_divergence": true, "verified_upgrade": true, "diagnostic_exact": true, "diagnostic_caps": true, "exclusive_private_files": true, "no_overwrite": true, "passive_v1_no_execution": true}, string(data)
}
func main() {
	require(len(os.Args) >= 3, "missing operation/path")
	mode, path := os.Args[1], os.Args[2]
	m, c := application("")
	var receipt map[string]any
	switch mode {
	case "record":
		r := attach(m, 3, el(), provenance())
		for seq := 1; seq <= 3; seq++ {
			actual, e := m.Step(seq-1, 1)
			must(e)
			checked, e := s.CheckTurn(m, seq-1, 1, actual, uint64(seq), s.CheckPolicy{StateEvery: 1, TransitionChecks: true})
			must(e)
			before := count(c)
			must(s.ObserveEvidenceRecorder(r, checked, causal(uint64(seq), true)))
			require(before["state"] == c.stateChecks && before["transition"] == c.edgeChecks && before["reducer"] == c.step, "handoff reexecuted")
		}
		v := r.Snapshot()
		mainCounts := count(c)
		require(c.initial == 0 && c.step == 3 && c.stateChecks == 4 && c.edgeChecks == 3, "check-once counters")
		must(v.ExportRawNew(path+".v2", el()))
		b, e := s.MarshalAuditTrace(&v.Trace, limits())
		must(e)
		must(os.WriteFile(path+".v1", b, 0600))
		portable, _, e := v.Trace.Portable()
		must(e)
		j, e := s.MarshalTrace(&portable)
		must(e)
		must(os.WriteFile(path+".json", j, 0600))
		control, text := controls(v, path)
		receipt = map[string]any{"evidence": norm(v), "report": replayV(m, v, el()), "retained_config": configView(v.Trace.Config), "main_counts": mainCounts, "controls": control, "diagnostic_text": text}
	case "read-v1":
		f, e := os.Open(path)
		must(e)
		a, e := s.ReadArtifact(f, el())
		must(e)
		must(f.Close())
		require(a.V1 != nil, "expected v1")
		phase, _ := a.CheckPhase(0, 0)
		_, known := a.CheckPhase(1, 0)
		require(!known, "legacy phase fabricated")
		receipt = map[string]any{"version": 1, "initial_phase": phase, "turn_phase": nil, "execution_calls": c.initial + c.step + c.stateChecks + c.edgeChecks, "retained_config": configView(a.ExactTrace().Config)}
	case "replay", "upgrade":
		require(len(os.Args) == 4, "missing output")
		f, e := os.Open(path)
		must(e)
		a, e := s.ReadArtifact(f, el())
		must(e)
		must(f.Close())
		v := a.V2
		if mode == "upgrade" {
			v, e = s.VerifyUpgrade(m, a.ExactTrace(), provenance(), 0, false, el())
			must(e)
		}
		require(v != nil, "expected v2")
		must(v.ExportRawNew(os.Args[3], el()))
		receipt = map[string]any{"version": 2, "evidence": norm(v), "report": replayV(m, v, el()), "retained_config": configView(v.Trace.Config)}
	case "diagnostic":
		require(len(os.Args) == 4, "missing output")
		f, e := os.Open(path)
		must(e)
		a, e := s.ReadArtifact(f, el())
		must(e)
		must(f.Close())
		data, e := s.DiagnosticJSON(a, policy())
		must(e)
		must(s.ExportDiagnosticNew(a, os.Args[3], policy()))
		rejected(s.ExportDiagnosticNew(a, os.Args[3], policy()), "")
		actual, e := os.ReadFile(os.Args[3])
		must(e)
		require(bytes.Equal(data, actual), "diagnostic overwritten")
		receipt = map[string]any{"diagnostic_text": string(data), "execution_calls": c.initial + c.step + c.stateChecks + c.edgeChecks, "no_overwrite": true}
	default:
		panic("unknown operation")
	}
	receipt["producer"] = "go"
	receipt["mode"] = mode
	receipt["legacy_encoder_calls"] = c.legacy
	receipt["codec_into_calls"] = c.state + c.input + c.output
	must(json.NewEncoder(os.Stdout).Encode(receipt))
}

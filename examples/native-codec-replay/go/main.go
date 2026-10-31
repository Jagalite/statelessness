package main

import (
	"bytes"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
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

type counts struct{ legacy, decode, step, state, input, output int }

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
	m := s.Model[int, int, string]{InitialState: func() (int, error) { return 0, nil }, Inputs: func(int) (s.Iterator[int], error) { return s.Values([]int{0, 1}), nil }, EqualStates: func(a, b int) bool { return a == b }, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[string],
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
		CheckState: func(state int) ([]s.Check, error) {
			if state >= 3 {
				return []s.Check{s.Failed("threshold.bound", "at least three")}, nil
			}
			return []s.Check{s.Passed("threshold.bound")}, nil
		},
		Codec: &s.Codec[int, int, string]{Metadata: func() (s.Metadata, error) { return s.Identity("native-codec-replay-threshold", "application-v1"), nil }, EncodeState: legacyInt, EncodeInput: legacyInt, EncodeOutput: func(string) ([]byte, error) { c.legacy++; return nil, fmt.Errorf("legacy encoder invoked") },
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
func expired(b *s.EncodeBuffer) {
	for _, f := range []func() error{func() error { return b.Append([]byte("x")) }, func() error { return b.ExtendFromSlice(nil) }, func() error { return b.ExtendFromVec(nil) }, func() error { _, e := b.Len(); return e }, func() error { _, e := b.Snapshot(); return e }, b.Finish, func() error { return b.Framed(func(*s.EncodeBuffer) error { return nil }) }} {
		if err := f(); err == nil || !strings.Contains(err.Error(), "expired") {
			panic("escaped encode buffer remained active")
		}
	}
}
func bufferControls() map[string]any {
	storage := []byte("stale")
	var outer, child *s.EncodeBuffer
	must(s.WithEncodeBuffer(&storage, 11, func(b *s.EncodeBuffer) error {
		outer = b
		n, e := b.Len()
		must(e)
		if n != 0 {
			panic("buffer did not clear")
		}
		must(b.Append([]byte("a")))
		must(b.Framed(func(n *s.EncodeBuffer) error { child = n; return n.Append([]byte("bc")) }))
		expired(child)
		snapshot, e := b.Snapshot()
		must(e)
		if len(snapshot) != 11 || snapshot[0] != 'a' || binary.LittleEndian.Uint64(snapshot[1:9]) != 2 || string(snapshot[9:]) != "bc" {
			panic("framing mismatch")
		}
		return b.Finish()
	}))
	expired(outer)
	var nested []byte
	must(s.WithEncodeBuffer(&nested, 17, func(b *s.EncodeBuffer) error {
		return b.Framed(func(outer *s.EncodeBuffer) error {
			return outer.Framed(func(inner *s.EncodeBuffer) error { return inner.Append([]byte("x")) })
		})
	}))
	if len(nested) != 17 || binary.LittleEndian.Uint64(nested[:8]) != 9 || binary.LittleEndian.Uint64(nested[8:16]) != 1 || nested[16] != 'x' {
		panic("nested framing mismatch")
	}
	var shared []byte
	if e := s.WithEncodeBuffer(&shared, 9, func(b *s.EncodeBuffer) error {
		return b.Framed(func(child *s.EncodeBuffer) error { return child.Append([]byte("xx")) })
	}); e == nil || len(shared) != 8 {
		panic("framed budget not shared")
	}
	framed := append([]byte{}, storage...)
	var first error
	err := s.WithEncodeBuffer(&storage, 2, func(b *s.EncodeBuffer) error {
		must(b.Append([]byte("ab")))
		first = b.Append([]byte("c"))
		if first == nil {
			panic("missing overflow")
		}
		if e := b.Append(nil); e != first {
			panic("overflow not sticky")
		}
		if e := b.Finish(); e != first {
			panic("finish not sticky")
		}
		if _, e := b.Snapshot(); e != first {
			panic("snapshot not sticky")
		}
		return nil
	})
	if err == nil || !bytes.Equal(storage, []byte("ab")) {
		panic("overflow changed accepted prefix")
	}
	m, c := application("")
	err = s.WithEncodeBuffer(&storage, 1, func(b *s.EncodeBuffer) error { must(m.AppendStateEncoding(0, b)); return m.AppendInputEncoding(1, b) })
	if err == nil || string(storage) != "0" {
		panic("codec callbacks do not share budget")
	}
	return map[string]any{"clear_entry": true, "sticky_overflow": true, "whole_chunk_admission": true, "framed_bytes": framed, "shared_limit": true, "escaped_buffer_expired": true, "nested_child_expired": true, "outer_active_after_nested": true, "legacy_calls": c.legacy}
}

func limits() s.BinaryLimits {
	return s.BinaryLimits{MaxTotalBytes: 4096, MaxFrameBytes: 1024, MaxBlobBytes: 64, MaxStringBytes: 128, MaxSteps: 16, MaxChecksPerStep: 16, MaxOutputsPerStep: 16, MaxParameters: 16, MaxItems: 128}
}
func reportView(r s.ReplayReport) map[string]any {
	return map[string]any{"outcome": r.Outcome, "divergence_step": r.Step, "divergence_field": r.Field, "steps_verified": r.StepsVerified, "failure_reproduced": r.FailureReproduced, "build_matches": r.BuildMatches}
}
func configView(config s.RunConfig) map[string]any {
	var seed any
	if config.Seed != nil {
		seed = strconv.FormatUint(*config.Seed, 10)
	}
	parameters := config.Parameters
	if parameters == nil {
		parameters = [][2]string{}
	}
	return map[string]any{"strategy": config.Strategy, "seed": seed, "parameters": parameters}
}
func diffView(d s.ReplayDifferences) map[string]any {
	return map[string]any{"disposition": d.Disposition, "outputs": d.Outputs, "state": d.State, "checks": d.Checks}
}
func hexOutputs(outputs [][]byte) []string {
	out := []string{}
	for _, v := range outputs {
		out = append(out, hex.EncodeToString(v))
	}
	return out
}
func transitionView(t s.Transition[int, string]) map[string]any {
	outputs := t.Outputs
	if outputs == nil {
		outputs = []string{}
	}
	return map[string]any{"state": t.State, "outputs": outputs, "disposition": t.Disposition}
}
func expectedView(t s.TraceStep) map[string]any {
	return map[string]any{"input": hex.EncodeToString(t.Input), "disposition": t.Disposition, "outputs": hexOutputs(t.Outputs), "post_state": hex.EncodeToString(t.PostState), "checks": t.Checks}
}
func replayRun(trace *s.AuditTrace, bounded bool, variant string, l s.BinaryLimits) (map[string]any, *counts, error) {
	m, c := application(variant)
	events := []map[string]any{}
	observer := s.ReplayObserver[int, int, string]{Initial: func(o s.ReplayInitialObservation[int]) error {
		events = append(events, map[string]any{"kind": "initial", "sequence": 0, "state": o.Actual.State, "checks": o.Actual.Checks, "expected_state": hex.EncodeToString(o.ExpectedState), "actual_state": hex.EncodeToString(o.ActualState), "expected_checks": o.ExpectedChecks, "report": reportView(o.Report), "differences": diffView(o.Differences)})
		return nil
	}, Turn: func(o s.ReplayTurnObservation[int, int, string]) error {
		events = append(events, map[string]any{"kind": "turn", "sequence": o.Actual.Sequence, "before": o.Actual.Before, "input": o.Actual.Input, "transition": transitionView(o.Actual.Transition), "checks": o.Actual.Checks, "state_check_count": o.Actual.StateCheckCount, "expected": expectedView(o.Expected), "actual_input": hex.EncodeToString(o.ActualInput), "actual_outputs": hexOutputs(o.ActualOutputs), "actual_state": hex.EncodeToString(o.ActualState), "report": reportView(o.Report), "differences": diffView(o.Differences)})
		return nil
	}}
	var r *s.ReplayReport
	var err error
	if bounded {
		r, err = s.ReplayWithObservationsBounded(m, trace, false, l, observer)
	} else {
		r, err = s.ReplayWithObservations(m, &trace.Trace, false, observer)
	}
	return map[string]any{"report": reportView(*r), "observations": events}, c, err
}
func controls(trace *s.AuditTrace) map[string]any {
	base := bufferControls()
	m, c := application("")
	l := limits()
	l.MaxSteps = 2
	_, err := s.ReplayWithObservationsBounded(m, trace, false, l, s.ReplayObserver[int, int, string]{})
	if err == nil || c.decode != 0 || c.step != 0 || c.legacy != 0 || c.state+c.input+c.output != 0 {
		panic("preflight did not reject before decoder/reducer/encoder")
	}
	m, c = application("overrun")
	_, err = s.ReplayWithObservationsBounded(m, trace, false, limits(), s.ReplayObserver[int, int, string]{})
	var re *s.ReplayError[int, int, string]
	if !errors.As(err, &re) || re.Actual == nil || re.Actual.Turn == nil || re.Actual.Turn.StateCheckCount == nil || *re.Actual.Turn.StateCheckCount != 1 || re.Actual.Turn.Transition.State != 1 || len(re.Actual.Turn.Transition.Outputs) != 1 || len(re.Actual.Turn.Transition.Outputs[0]) != 65 || c.step != 1 {
		panic("oversize actual evidence lost")
	}
	copyTrace := *trace
	copyTrace.Steps = append([]s.TraceStep{}, trace.Steps...)
	last := len(copyTrace.Steps) - 1
	copyTrace.Steps[last].Checks = append([]s.Check{}, trace.Steps[last].Checks...)
	copyTrace.Steps[last].Checks[0].Details = "different expected detail"
	diverged, dc, e := replayRun(&copyTrace, false, "", limits())
	must(e)
	r := diverged["report"].(map[string]any)
	if r["outcome"] != "diverged" || r["failure_reproduced"] != true {
		panic("rich divergence missing")
	}
	return map[string]any{"clear_entry": base["clear_entry"], "sticky_finish": true, "framed_shared_budget": true, "nested_framing": true, "preflight_decode_calls": 0, "oversize_reducer_calls": c.step, "oversize_actual_retained": true, "legacy_encoder_calls": c.legacy + dc.legacy, "codec_into_calls": c.state + c.input + c.output + dc.state + dc.input + dc.output, "rich_divergence": true, "escaped_sink_expired": true, "nested_child_expired": true, "outer_active_after_nested": true}
}
func main() {
	if len(os.Args) != 3 {
		panic("expected record PREFIX, replay BIN, or replay-json JSON")
	}
	mode, path := os.Args[1], os.Args[2]
	var receipt map[string]any
	switch mode {
	case "record":
		m, c := application("")
		trace, err := s.RecordAudit(m, s.Values([]int{1, 1, 1}), s.RunConfig{Strategy: "manual", Parameters: [][2]string{}}, 9, limits())
		must(err)
		binary, err := s.MarshalAuditTrace(trace, limits())
		must(err)
		must(os.WriteFile(path+".bin", binary, 0600))
		portable, _, err := trace.Portable()
		must(err)
		text, err := s.MarshalTrace(&portable)
		must(err)
		must(os.WriteFile(path+".json", text, 0600))
		rich, rc, err := replayRun(trace, false, "", limits())
		must(err)
		bounded, bc, err := replayRun(trace, true, "", limits())
		must(err)
		receipt = map[string]any{"controls": controls(trace), "rich": rich, "bounded": bounded, "retained_config": configView(trace.Config), "legacy_encoder_calls": c.legacy + rc.legacy + bc.legacy, "codec_into_calls": c.state + c.input + c.output + rc.state + rc.input + rc.output + bc.state + bc.input + bc.output, "steps": len(trace.Steps), "termination": trace.Termination}
	case "replay":
		data, err := os.ReadFile(path)
		must(err)
		trace, err := s.ParseAuditTrace(data, limits())
		must(err)
		rich, rc, err := replayRun(trace, false, "", limits())
		must(err)
		bounded, bc, err := replayRun(trace, true, "", limits())
		must(err)
		receipt = map[string]any{"rich": rich, "bounded": bounded, "retained_config": configView(trace.Config), "legacy_encoder_calls": rc.legacy + bc.legacy, "codec_into_calls": rc.state + rc.input + rc.output + bc.state + bc.input + bc.output}
	case "replay-json":
		data, err := os.ReadFile(path)
		must(err)
		trace, err := s.ParseTrace(data)
		must(err)
		m, c := application("")
		report, err := s.Replay(m, trace, false)
		must(err)
		receipt = reportView(*report)
		receipt["legacy_encoder_calls"] = c.legacy
		receipt["codec_into_calls"] = c.state + c.input + c.output
	default:
		panic("unknown command")
	}
	receipt["producer"] = "go"
	receipt["mode"] = mode
	must(json.NewEncoder(os.Stdout).Encode(receipt))
}

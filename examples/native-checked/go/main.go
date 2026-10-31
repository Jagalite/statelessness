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
		Codec: &s.Codec[int, int, string]{Metadata: func() (s.Metadata, error) { return s.Identity("native-checked-threshold", "application-v1"), nil }, EncodeState: legacyInt, EncodeInput: legacyInt, EncodeOutput: func(string) ([]byte, error) { c.legacy++; return nil, fmt.Errorf("legacy encoder invoked") },
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
	return s.BinaryLimits{MaxTotalBytes: 8192, MaxFrameBytes: 2048, MaxBlobBytes: 64, MaxStringBytes: 256, MaxSteps: 16, MaxChecksPerStep: 16, MaxOutputsPerStep: 16, MaxParameters: 32, MaxItems: 256}
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
	if bounded {
		return replaySession(&trace.Trace, trace, variant, l)
	}
	return replaySession(&trace.Trace, nil, variant, l)
}
func replaySession(trace *s.Trace, audit *s.AuditTrace, variant string, l s.BinaryLimits) (map[string]any, *counts, error) {
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
	if audit != nil {
		r, err = s.ReplayWithObservationsBounded(m, audit, false, l, observer)
	} else {
		r, err = s.ReplayWithObservations(m, trace, false, observer)
	}
	return map[string]any{"report": reportView(*r), "observations": events}, c, err
}
func observe(r *s.Recorder, t *s.CheckedTurn[int, int, string]) error {
	_, e := s.ObserveCheckedRecorder(r, t)
	return e
}
func options(n int) s.RecorderOptions {
	return s.RecorderOptions{MaxSteps: n, MaxRetainedBytes: 8192, Limits: limits()}
}
func count(c *counts) map[string]int {
	return map[string]int{"initial_state": c.initial, "reducer": c.step, "state": c.stateChecks, "transition": c.edgeChecks}
}
func applicationCounts(c *counts) [4]int {
	return [4]int{c.initial, c.step, c.stateChecks, c.edgeChecks}
}
func attach(m s.Model[int, int, string], state int, origin uint64, n int) *s.Recorder {
	t, e := s.CheckInitial(m, state)
	must(e)
	r, e := s.NewRecorderWithCheckedInitialAt(t, origin, s.RunConfig{Strategy: "manual"}, options(n))
	must(e)
	return r
}
func token(m s.Model[int, int, string], before int, seq uint64, policy s.CheckPolicy, output string) *s.CheckedTurn[int, int, string] {
	t, e := s.CheckTurn(m, before, 1, s.Accept[int, string](before+1, output), seq, policy)
	must(e)
	return t
}
func full() s.CheckPolicy { return s.CheckPolicy{StateEvery: 1, TransitionChecks: true} }
func require(v bool, why string) {
	if !v {
		panic(why)
	}
}
func encoded(r *s.Recorder) []byte {
	b, e := s.MarshalAuditTrace(r.Snapshot(), limits())
	must(e)
	return b
}
func controls() (map[string]any, map[string]any) {
	m, c := application("")
	initial, e := s.CheckInitial(m, 1)
	must(e)
	before := c.stateChecks
	r, e := s.NewRecorderWithCheckedInitialAt(initial, 10, s.RunConfig{Strategy: "manual"}, options(3))
	must(e)
	require(c.stateChecks == before, "attach recheck")
	for j := 0; j < 2; j++ {
		t := token(m, j+1, uint64(j+11), full(), "changed")
		a, b := c.stateChecks, c.edgeChecks
		must(observe(r, t))
		require(c.stateChecks == a && c.edgeChecks == b, "handoff recheck")
	}
	origin := false
	for _, p := range r.Snapshot().Config.Parameters {
		if p == [2]string{"stateless.monitor.sequence_origin", "10"} {
			origin = true
		}
	}
	require(origin && r.IsFrozen() && string(r.Snapshot().InitialState) == "1", "origin lost")
	start, e := s.CheckInitial(m, 0)
	must(e)
	first := token(m, 0, 1, full(), "changed")
	left, e := s.NewRecorderWithCheckedInitial(start, s.RunConfig{Strategy: "manual"}, options(3))
	must(e)
	right, e := s.NewRecorderWithCheckedInitial(start, s.RunConfig{Strategy: "manual"}, options(3))
	must(e)
	must(observe(left, first))
	must(observe(right, first))
	require(bytes.Equal(encoded(left), encoded(right)), "token reuse")
	for _, kind := range []string{"sampled", "duplicate", "gap"} {
		r = attach(m, 0, 0, 3)
		bad := first
		if kind == "duplicate" {
			must(observe(r, first))
		}
		if kind == "sampled" {
			bad = token(m, 0, 1, s.CheckPolicy{StateEvery: 2, TransitionChecks: true}, "changed")
		}
		if kind == "gap" {
			bad = token(m, 0, 2, full(), "changed")
		}
		require(observe(r, bad) != nil && r.IsFrozen(), "invalid token not rejected")
		prefix := encoded(r)
		n := 0
		if kind == "duplicate" {
			n = 1
		}
		require(len(r.Snapshot().Steps) == n && r.Snapshot().Termination == "model_error", "prefix lost")
		require(observe(r, first) != nil && bytes.Equal(prefix, encoded(r)), "freeze overwritten")
	}
	r = attach(m, 0, 0, 3)
	require(observe(r, &s.CheckedTurn[int, int, string]{}) != nil, "zero token accepted")
	view, e := first.Observation()
	must(e)
	view.Transition.State = 99
	view.Checks[0].Details = "changed"
	tr, e := first.Transition()
	must(e)
	require(tr.State == 1 && first.Checks()[0].Details == "", "accessor ownership")
	r = attach(m, 0, 0, 3)
	must(observe(r, first))
	bad := token(m, 1, 2, full(), strings.Repeat("x", 65))
	a, b := c.stateChecks, c.edgeChecks
	require(observe(r, bad) != nil && c.stateChecks == a && c.edgeChecks == b, "bounded accepted or checks repeated")
	prefix := r.Snapshot()
	data := encoded(r)
	decoded, e := s.ParseAuditTrace(data, limits())
	must(e)
	again, e := s.MarshalAuditTrace(decoded, limits())
	must(e)
	require(prefix.Termination == "model_error" && len(prefix.Steps) == 1 && bytes.Equal(data, again), "bounded prefix incoherent")
	ring := attach(m, 0, 0, 1)
	must(observe(ring, first))
	must(observe(ring, token(m, 1, 2, full(), "changed")))
	audit := ring.Snapshot()
	require(string(audit.InitialState) == "1" && len(audit.InitialChecks) == 2 && ring.EvictedSteps() == 1, "ring prefix")
	for _, c := range audit.InitialChecks {
		require(c.ID != "edge.checked", "edge promoted")
	}
	rich, _, e := replayRun(audit, false, "", limits())
	must(e)
	bounded, _, e := replayRun(audit, true, "", limits())
	must(e)
	return map[string]any{"check_once": true, "attach_rechecks": false, "handoff_rechecks": false, "token_reused": true, "sampled_rejected": true, "duplicate_rejected": true, "gap_rejected": true, "origin_preserved": true, "ring_state_prefix_only": true, "bounded_error_prefix": true, "forged_diagnostic_rejected": true, "accessor_ownership": true}, map[string]any{"rich": rich, "bounded": bounded, "retained_config": configView(audit.Config)}
}
func main() {
	if len(os.Args) != 3 {
		panic("expected record PREFIX, record-suffix PREFIX, record-failing-suffix PREFIX, replay BIN, replay-suffix BIN, replay-json JSON, or replay-suffix-json JSON")
	}
	mode, path := os.Args[1], os.Args[2]
	var receipt map[string]any
	switch mode {
	case "record":
		m, c := application("")
		initial, err := s.CheckInitial(m, 0)
		must(err)
		rec, err := s.NewRecorderWithCheckedInitial(initial, s.RunConfig{Strategy: "manual"}, options(3))
		must(err)
		var firstChecked *s.CheckedTurn[int, int, string]
		for seq := 1; seq <= 3; seq++ {
			actual, e := m.Step(seq-1, 1)
			must(e)
			checked, e := s.CheckTurn(m, seq-1, 1, actual, uint64(seq), full())
			must(e)
			if seq == 1 {
				firstChecked = checked
			}
			a, b := c.stateChecks, c.edgeChecks
			must(observe(rec, checked))
			require(c.stateChecks == a && c.edgeChecks == b, "handoff repeated checks")
		}
		trace := rec.Snapshot()
		mainCounts := count(c)
		require(c.initial == 0 && c.step == 3 && c.stateChecks == 4 && c.edgeChecks == 3, "check-once counts differ")
		control, ring := controls()
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
		receipt = map[string]any{"controls": control, "ring": ring, "main_counts": mainCounts, "checked": map[string]any{"initial_checks": initial.StateCheckCount(), "state_check_count": firstChecked.StateCheckCount(), "full_checking": firstChecked.FullChecking(), "sequence": strconv.FormatUint(firstChecked.Sequence(), 10)}, "rich": rich, "bounded": bounded, "retained_config": configView(trace.Config), "legacy_encoder_calls": c.legacy + rc.legacy + bc.legacy, "codec_into_calls": c.state + c.input + c.output + rc.state + rc.input + rc.output + bc.state + bc.input + bc.output, "steps": len(trace.Steps), "termination": trace.Termination}
	case "record-suffix", "record-failing-suffix":
		failing := mode == "record-failing-suffix"
		turns := 2
		termination := "interrupted"
		lastCheck := s.Passed("threshold.bound")
		if failing {
			turns = 3
			termination = "property_failed"
			lastCheck = s.Failed("threshold.bound", "at least three")
		}
		m, c := application("")
		state := 0
		initial, err := s.CheckInitial(m, state)
		must(err)
		before := applicationCounts(c)
		rec, err := s.NewRecorderWithCheckedInitialAt(initial, 40, s.RunConfig{Strategy: "manual"}, options(1))
		must(err)
		require(applicationCounts(c) == before, "suffix attach reran application")
		observerPrefix := "suffix-observer\n"
		var writer bytes.Buffer
		_, err = writer.WriteString(observerPrefix)
		must(err)
		logOptions := s.DefaultObservationOptions()
		logOptions.Level, logOptions.Snapshots = s.LogTrace, s.SnapshotEveryTransition()
		logOptions.MaxRecordBytes = 1024
		observer, err := s.NewTextObserver(&writer, "suffix", logOptions, s.NoPayloads[int, int, string]{})
		must(err)
		checkpoint, err := initial.State()
		must(err)
		beforeCallbacks := *c
		must(observer.Observe(m, s.Event[int, int, string]{Kind: s.EventCheckpoint, Sequence: 40, State: checkpoint, Checks: initial.Checks()}))
		require(*c == beforeCallbacks, "suffix checkpoint observer ran application or codec callbacks")
		var firstChecked, lastChecked *s.CheckedTurn[int, int, string]
		for seq := uint64(41); seq <= 40+uint64(turns); seq++ {
			actual, e := m.Step(state, 1)
			must(e)
			checked, e := s.CheckTurn(m, state, 1, actual, seq, full())
			must(e)
			if seq == 41 {
				firstChecked = checked
			}
			lastChecked = checked
			before = applicationCounts(c)
			// Detached diagnostics do not authorize exact recording; retain the token.
			view, e := checked.Observation()
			must(e)
			beforeCallbacks = *c
			must(observer.Observe(m, view.Event()))
			require(*c == beforeCallbacks, "suffix turn observer ran application or codec callbacks")
			must(observe(rec, checked))
			require(applicationCounts(c) == before, "suffix checked handoff reran application")
			state = actual.State
		}
		beforeCallbacks = *c
		must(observer.Flush())
		require(*c == beforeCallbacks, "suffix observer flush ran application or codec callbacks")
		trace := rec.Snapshot()
		mainCounts := count(c)
		require(applicationCounts(c) == [4]int{0, turns, turns + 1, turns}, "suffix check-once counts differ")
		require(state == turns && lastChecked.Sequence() == 40+uint64(turns), "suffix final application turn differs")
		require(string(trace.InitialState) == strconv.Itoa(turns-1) && len(trace.InitialChecks) == 2, "suffix checkpoint differs")
		require(trace.InitialChecks[0] == s.Passed("threshold.bound") && trace.InitialChecks[1] == s.Passed("state.appended"), "suffix checkpoint lost state-only checks")
		require(len(trace.Steps) == 1 && string(trace.Steps[0].PostState) == strconv.Itoa(turns) && len(trace.Steps[0].Checks) == 3, "suffix retained turn differs")
		for i, check := range []s.Check{lastCheck, s.Passed("state.appended"), s.Passed("edge.checked")} {
			require(trace.Steps[0].Checks[i] == check, "suffix retained checks differ")
		}
		require(trace.Termination == termination && rec.IsFrozen() == failing && rec.ObservedSteps() == uint64(turns) && rec.EvictedSteps() == uint64(turns-1), "suffix recorder status differs")
		parameters := map[string]string{}
		for _, pair := range trace.Config.Parameters {
			parameters[pair[0]] = pair[1]
		}
		for name, expected := range map[string]string{"sequence_origin": "40", "checkpoint_after_sequence": strconv.Itoa(turns - 1), "observed_steps": strconv.Itoa(turns), "evicted_steps": strconv.Itoa(turns - 1)} {
			require(parameters["stateless.monitor."+name] == expected, "suffix local metadata differs: "+name)
		}
		binary, err := s.MarshalAuditTrace(trace, limits())
		must(err)
		portable, _, err := trace.Portable()
		must(err)
		text, err := s.MarshalTrace(&portable)
		must(err)
		var freezeWriter bytes.Buffer
		if failing {
			beforeCallbacks = *c
			freezeObserver, e := s.NewTextObserver(&freezeWriter, "suffix", logOptions, s.NoPayloads[int, int, string]{})
			must(e)
			// A detached checkpoint has no continuity or checked authority.
			must(freezeObserver.Observe(m, s.Event[int, int, string]{Kind: s.EventCheckpoint, Sequence: 44, State: 999, Checks: []s.Check{}}))
			must(freezeObserver.Flush())
			require(*c == beforeCallbacks, "frozen diagnostic ran application or codec callbacks")
			// Reuse the authentic final token; do not execute or check another turn.
			require(observe(rec, lastChecked) != nil, "frozen checked handoff accepted")
			require(*c == beforeCallbacks, "frozen handoff ran application or codec callbacks")
			after := rec.Snapshot()
			afterBinary, e := s.MarshalAuditTrace(after, limits())
			must(e)
			afterPortable, _, e := after.Portable()
			must(e)
			afterText, e := s.MarshalTrace(&afterPortable)
			must(e)
			require(bytes.Equal(binary, afterBinary) && bytes.Equal(text, afterText) && rec.IsFrozen(), "first failure artifacts changed after freeze")
			require(*c == beforeCallbacks, "frozen artifact comparison ran application or codec callbacks")
			require(freezeWriter.String() == "Trace stream=\"suffix\" event=checkpoint sequence=44 checks=0 state=unavailable\n", "frozen diagnostic differs")
		}
		must(os.WriteFile(path+".bin", binary, 0600))
		must(os.WriteFile(path+".json", text, 0600))
		rich, rc, err := replayRun(trace, false, "", limits())
		must(err)
		bounded, bc, err := replayRun(trace, true, "", limits())
		must(err)
		require(applicationCounts(rc) == [4]int{0, 1, 2, 1}, "suffix rich replay counts differ")
		require(applicationCounts(bc) == [4]int{0, 1, 2, 1}, "suffix bounded replay counts differ")
		receipt = map[string]any{"main_counts": mainCounts, "checked": map[string]any{"initial_checks": initial.StateCheckCount(), "state_check_count": firstChecked.StateCheckCount(), "full_checking": firstChecked.FullChecking(), "sequence": strconv.FormatUint(firstChecked.Sequence(), 10)}, "rich": rich, "bounded": bounded, "retained_config": configView(trace.Config), "observer_prefix": observerPrefix, "observer_text": writer.String(), "legacy_encoder_calls": c.legacy + rc.legacy + bc.legacy, "codec_into_calls": c.state + c.input + c.output + rc.state + rc.input + rc.output + bc.state + bc.input + bc.output, "steps": len(trace.Steps), "termination": trace.Termination}
		receipt["rich_counts"], receipt["bounded_counts"] = count(rc), count(bc)
		if failing {
			receipt["freeze_verified"] = true
			receipt["freeze_observer_text"] = freezeWriter.String()
		}
	case "replay", "replay-suffix":
		data, err := os.ReadFile(path)
		must(err)
		trace, err := s.ParseAuditTrace(data, limits())
		must(err)
		rich, rc, err := replayRun(trace, false, "", limits())
		must(err)
		bounded, bc, err := replayRun(trace, true, "", limits())
		must(err)
		receipt = map[string]any{"rich": rich, "bounded": bounded, "retained_config": configView(trace.Config), "legacy_encoder_calls": rc.legacy + bc.legacy, "codec_into_calls": rc.state + rc.input + rc.output + bc.state + bc.input + bc.output}
		if mode == "replay-suffix" {
			require(applicationCounts(rc) == [4]int{0, 1, 2, 1}, "suffix rich replay counts differ")
			require(applicationCounts(bc) == [4]int{0, 1, 2, 1}, "suffix bounded replay counts differ")
			receipt["rich_counts"], receipt["bounded_counts"] = count(rc), count(bc)
		}
	case "replay-suffix-json":
		data, err := os.ReadFile(path)
		must(err)
		portable, err := s.ParseTrace(data)
		must(err)
		rich, rc, err := replaySession(portable, nil, "", limits())
		must(err)
		// Portable JSON has no audit configuration. Supply defaults only for
		// the public bounded API; these are not retained producer metadata.
		audit, err := s.AuditFromPortable(*portable, s.RunConfig{Strategy: "manual"})
		must(err)
		bounded, bc, err := replayRun(audit, true, "", limits())
		must(err)
		require(applicationCounts(rc) == [4]int{0, 1, 2, 1}, "suffix JSON rich replay counts differ")
		require(applicationCounts(bc) == [4]int{0, 1, 2, 1}, "suffix JSON bounded replay counts differ")
		receipt = map[string]any{"rich": rich, "bounded": bounded, "rich_counts": count(rc), "bounded_counts": count(bc), "legacy_encoder_calls": rc.legacy + bc.legacy, "codec_into_calls": rc.state + rc.input + rc.output + bc.state + bc.input + bc.output}
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

package statelessness_test

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"reflect"
	"strconv"
	"strings"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

// Source-only integration coverage for observation-v1 and N07. These tests use
// the public checked handoff, existing Recorder, binary reader and replay engine.
// Replay has its own counters: its intentional reducer/check calls must never
// hide an extra callback during application execution or diagnostic observation.
type observationRecorderCalls struct {
	initial, reducer, state, edge, effects int
	encodeState, encodeInput, encodeOutput int
	encodeFault                           string
}

func (c *observationRecorderCalls) application() [5]int {
	return [5]int{c.initial, c.reducer, c.state, c.edge, c.effects}
}

func (c *observationRecorderCalls) encoding() [3]int {
	return [3]int{c.encodeState, c.encodeInput, c.encodeOutput}
}

func (c *observationRecorderCalls) executeEffect(_ string) {
	c.effects++
}

func observationRecorderStateChecks(state, failAt int) []s.Check {
	bound := s.Passed("state-bound")
	if failAt > 0 && state >= failAt {
		bound = s.Failed("state-bound", "ceiling")
	}
	checks := []s.Check{bound, s.Passed("state:" + strconv.Itoa(state))}
	if state%2 == 0 {
		checks = append(checks, s.Passed("even-state"))
	}
	return checks
}

func observationRecorderModel(c *observationRecorderCalls, failAt int) s.Model[int, int, string] {
	// Extend the existing audit model pattern, using lossless decimal state/input
	// codecs so the 1024-turn session cannot wrap its checkpoint at a byte boundary.
	m := auditRecordingModel()
	m.InitialState = func() (int, error) { c.initial++; return 0, nil }
	m.Step = func(before, input int) (s.Transition[int, string], error) {
		c.reducer++
		next, disposition := before, s.Accepted()
		switch {
		case input > 0:
			next += input
		case input == 0:
			disposition = s.Ignored("duplicate")
		default:
			disposition = s.Rejected("negative")
		}
		return s.Transition[int, string]{State: next, Outputs: []string{
			"effect:" + strconv.Itoa(next), "audit:" + strconv.Itoa(input),
		}, Disposition: disposition}, nil
	}
	m.CheckState = func(state int) ([]s.Check, error) {
		c.state++
		return observationRecorderStateChecks(state, failAt), nil
	}
	m.CheckTransition = func(_ int, input int, actual s.Transition[int, string]) ([]s.Check, error) {
		c.edge++
		want := []string{"effect:" + strconv.Itoa(actual.State), "audit:" + strconv.Itoa(input)}
		if !reflect.DeepEqual(actual.Outputs, want) {
			return []s.Check{s.Failed("edge-output", "supplied outputs differ")}, nil
		}
		checks := []s.Check{s.Passed("edge-output")}
		if input == 0 {
			checks = append(checks, s.Skipped("optional-edge", "duplicate"))
		}
		return checks, nil
	}
	m.Codec.EncodeState = func(v int) ([]byte, error) {
		c.encodeState++
		if c.encodeFault == "state" {
			return nil, errors.New("state codec unavailable")
		}
		return []byte(strconv.Itoa(v)), nil
	}
	m.Codec.EncodeInput = func(v int) ([]byte, error) {
		c.encodeInput++
		if c.encodeFault == "input" {
			return nil, errors.New("input codec unavailable")
		}
		return []byte(strconv.Itoa(v)), nil
	}
	m.Codec.EncodeOutput = func(v string) ([]byte, error) {
		c.encodeOutput++
		if c.encodeFault == "output" {
			return nil, errors.New("output codec unavailable")
		}
		return []byte(v), nil
	}
	m.Codec.DecodeState = func(b []byte) (int, error) { return strconv.Atoi(string(b)) }
	m.Codec.DecodeInput = func(b []byte) (int, error) { return strconv.Atoi(string(b)) }
	return m
}

func observationRecorderTurn(t *testing.T, m s.Model[int, int, string], c *observationRecorderCalls, before, input int, sequence uint64, policy s.CheckPolicy) *s.CheckedTurn[int, int, string] {
	t.Helper()
	actual, err := m.Step(before, input) // The application alone executes the reducer.
	if err != nil {
		t.Fatal(err)
	}
	for _, output := range actual.Outputs {
		c.executeEffect(output) // Application-owned effect callback.
	}
	turn, err := s.CheckTurn(m, before, input, actual, sequence, policy)
	if err != nil {
		t.Fatal(err)
	}
	return turn
}

func observationRecorderEvent(t *testing.T, turn *s.CheckedTurn[int, int, string]) s.Event[int, int, string] {
	t.Helper()
	view, err := turn.Observation()
	if err != nil {
		t.Fatal(err)
	}
	return view.Event()
}

func observationRecorderObserver(t *testing.T, writer io.Writer, level s.LogLevel, snapshots s.SnapshotPolicy, payloads s.PayloadFormat[int, int, string]) *s.TextObserver[int, int, string] {
	t.Helper()
	options := s.DefaultObservationOptions()
	options.Level, options.Snapshots, options.MaxRecordBytes = level, snapshots, 1024
	observer, err := s.NewTextObserver[int, int, string](writer, "integration", options, payloads)
	if err != nil {
		t.Fatal(err)
	}
	return observer
}

func observationRecorderWantCalls(t *testing.T, c *observationRecorderCalls, want [5]int) {
	t.Helper()
	if got := c.application(); got != want {
		t.Fatalf("application callbacks [initial reducer state edge effects] = %v, want %v", got, want)
	}
}

func observationRecorderReplay(t *testing.T, trace *s.AuditTrace, failAt, retained int, failed bool) {
	t.Helper()
	wire, err := s.MarshalAuditTrace(trace, s.DefaultBinaryLimits())
	if err != nil {
		t.Fatal(err)
	}
	restored, err := s.ReadAuditTrace(bytes.NewReader(wire), s.DefaultBinaryLimits())
	if err != nil {
		t.Fatal(err)
	}
	calls := &observationRecorderCalls{}
	playback := observationRecorderModel(calls, failAt)
	report, err := s.ReplayWithObservationsBounded(playback, restored, false, s.DefaultBinaryLimits(), s.ReplayObserver[int, int, string]{})
	if err != nil {
		t.Fatal(err)
	}
	if report.Outcome != "exact" || report.StepsVerified != retained || report.FailureReproduced != failed {
		t.Fatalf("retained suffix replay: %+v", report)
	}
	observationRecorderWantCalls(t, calls, [5]int{0, retained, retained + 1, retained, 0})
}

func TestObservationRecorderLevelsShareCheckedExecution(t *testing.T) {
	type result struct {
		calls   [5]int
		encodes [3]int
		wire    []byte
		text    string
		state   int
	}
	run := func(t *testing.T, instrument bool, level s.LogLevel) result {
		calls := &observationRecorderCalls{}
		model := observationRecorderModel(calls, 11)
		state := 7 // Attach to a supplied mid-run checkpoint, never InitialState.
		initial, err := s.CheckInitial(model, state)
		if err != nil {
			t.Fatal(err)
		}
		var recorder *s.Recorder
		var observer *s.TextObserver[int, int, string]
		var text bytes.Buffer
		if instrument {
			options := s.DefaultRecorderOptions()
			options.MaxSteps = 8
			recorder, err = s.NewRecorderWithCheckedInitialAt(initial, 40, s.RunConfig{Strategy: "application"}, options)
			if err != nil {
				t.Fatal(err)
			}
			observer = observationRecorderObserver(t, &text, level, s.SnapshotEveryTransition(), s.NewEncodedPayloads[int, int, string](64))
			if err = observer.Observe(model, s.Event[int, int, string]{Kind: s.EventCheckpoint, Sequence: 40, State: state, Checks: initial.Checks()}); err != nil {
				t.Fatal(err)
			}
		}
		observationRecorderWantCalls(t, calls, [5]int{0, 0, 1, 0, 0})
		for index, input := range []int{1, 0, -1, 2, 1} {
			turn := observationRecorderTurn(t, model, calls, state, input, uint64(41+index), s.FullChecks())
			if instrument {
				event := observationRecorderEvent(t, turn)
				checks, err := s.ObserveCheckedRecorder(recorder, turn)
				if err != nil || !reflect.DeepEqual(checks, event.Checks) {
					t.Fatalf("recorder changed the authentic check batch: %v %v", checks, err)
				}
				if err = observer.Observe(model, event); err != nil {
					t.Fatal(err)
				}
				step := recorder.Snapshot().Steps[index]
				if step.Disposition != event.Transition.Disposition || !reflect.DeepEqual(step.Checks, event.Checks) ||
					!reflect.DeepEqual(step.Outputs, [][]byte{[]byte(event.Transition.Outputs[0]), []byte(event.Transition.Outputs[1])}) {
					t.Fatalf("recorder did not retain supplied transition: %+v", step)
				}
			}
			actual, err := turn.Transition()
			if err != nil {
				t.Fatal(err)
			}
			state = actual.State
			observationRecorderWantCalls(t, calls, [5]int{0, index + 1, index + 2, index + 1, 2 * (index + 1)})
		}
		out := result{calls: calls.application(), encodes: calls.encoding(), text: text.String(), state: state}
		if instrument {
			trace := recorder.Snapshot()
			if !recorder.IsFrozen() || trace.Termination != "property_failed" || trace.Steps[4].Checks[0] != s.Failed("state-bound", "ceiling") {
				t.Fatalf("lost first property failure: %+v", trace)
			}
			out.wire, err = s.MarshalAuditTrace(trace, s.DefaultBinaryLimits())
			if err != nil {
				t.Fatal(err)
			}
			observationRecorderReplay(t, trace, 11, 5, true)
		}
		return out
	}
	// No Recorder, TextObserver or codec work in this application/check baseline.
	baseline := run(t, false, s.LogOff)
	if baseline.encodes != [3]int{} || baseline.calls != [5]int{0, 5, 6, 5, 10} || baseline.state != 11 {
		t.Fatalf("invalid uninstrumented baseline: %+v", baseline)
	}
	var evidence []byte
	for _, level := range []s.LogLevel{s.LogOff, s.LogError, s.LogWarn, s.LogInfo, s.LogDebug, s.LogTrace} {
		t.Run(level.String(), func(t *testing.T) {
			got := run(t, true, level)
			if got.calls != baseline.calls || got.state != baseline.state {
				t.Fatalf("logging changed application execution: %+v versus %+v", got, baseline)
			}
			if evidence == nil {
				evidence = append([]byte(nil), got.wire...)
			} else if !bytes.Equal(evidence, got.wire) {
				t.Fatal("verbosity changed exact Recorder evidence")
			}
			wantEncodes := [3]int{11, 5, 10} // Recorder: checkpoint, before/post, inputs, outputs.
			wantLines := map[s.LogLevel]int{s.LogOff: 0, s.LogError: 1, s.LogWarn: 2, s.LogInfo: 2, s.LogDebug: 5, s.LogTrace: 6}[level]
			if level == s.LogTrace {
				wantEncodes = [3]int{17, 10, 20} // Trace adds one checkpoint and five payload sets.
				for _, field := range []string{"sequence=42 disposition=Ignored(\"duplicate\")", "sequence=43 disposition=Rejected(\"negative\")", "input=hex:32", "output[0]=hex:6566666563743a3130", "post_state=hex:3131", "check=\"state-bound\":Failed(\"ceiling\")"} {
					if !strings.Contains(got.text, field) {
						t.Fatalf("missing supplied field %q in %s", field, got.text)
					}
				}
			} else if strings.Contains(got.text, "hex:") {
				t.Fatal("payloads formatted below Trace")
			}
			if got.encodes != wantEncodes || strings.Count(got.text, "\n") != wantLines {
				t.Fatalf("encoding/record count: %v / %d, want %v / %d", got.encodes, strings.Count(got.text, "\n"), wantEncodes, wantLines)
			}
		})
	}
}

func TestObservationRecorderEscalatesActualChecks(t *testing.T) {
	for _, tc := range []struct {
		name        string
		checks      []s.Check
		disposition s.Disposition
		severity    s.LogLevel
	}{
		{"passed", []s.Check{s.Passed("p")}, s.Accepted(), s.LogDebug},
		{"rejected", []s.Check{s.Passed("p")}, s.Rejected("actual rejection"), s.LogDebug},
		{"ignored", []s.Check{s.Passed("p")}, s.Ignored("actual ignore"), s.LogDebug},
		{"passed_then_skip", []s.Check{s.Passed("p"), s.Skipped("s", "policy")}, s.Accepted(), s.LogWarn},
		{"skip_then_failed", []s.Check{s.Skipped("s", "policy"), s.Failed("f", "bad")}, s.Ignored("actual ignore"), s.LogError},
		{"failed_then_skip", []s.Check{s.Failed("f", "bad"), s.Skipped("s", "policy")}, s.Rejected("actual rejection"), s.LogError},
		{"passed_then_failed", []s.Check{s.Passed("p"), s.Failed("f", "bad")}, s.Accepted(), s.LogError},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := &observationRecorderCalls{}
			model := observationRecorderModel(calls, 0)
			reduce := model.Step
			model.Step = func(before, input int) (s.Transition[int, string], error) {
				actual, err := reduce(before, input)
				actual.Disposition = tc.disposition
				return actual, err
			}
			model.CheckTransition = func(int, int, s.Transition[int, string]) ([]s.Check, error) {
				calls.edge++
				return tc.checks, nil
			}
			initial, err := s.CheckInitial(model, 0)
			if err != nil {
				t.Fatal(err)
			}
			recorder, err := s.NewRecorderWithCheckedInitial(initial, s.RunConfig{}, s.DefaultRecorderOptions())
			if err != nil {
				t.Fatal(err)
			}
			turn := observationRecorderTurn(t, model, calls, 0, 1, 1, s.FullChecks())
			event := observationRecorderEvent(t, turn)
			if _, err = s.ObserveCheckedRecorder(recorder, turn); err != nil {
				t.Fatal(err)
			}
			frozen := recorder.Snapshot()
			for _, level := range []s.LogLevel{s.LogOff, s.LogError, s.LogWarn, s.LogInfo, s.LogDebug, s.LogTrace} {
				var sink bytes.Buffer
				observer := observationRecorderObserver(t, &sink, level, s.SnapshotEveryTransition(), s.NewEncodedPayloads[int, int, string](64))
				before := calls.encoding()
				if err = observer.Observe(model, event); err != nil {
					t.Fatal(err)
				}
				visible := level != s.LogOff && level >= tc.severity
				if (sink.Len() != 0) != visible || visible && !strings.HasPrefix(sink.String(), tc.severity.String()+" stream=") {
					t.Fatalf("%s did not respect severity %s: %s", level, tc.severity, sink.String())
				}
				want := before
				if level == s.LogTrace {
					want = [3]int{before[0] + 1, before[1] + 1, before[2] + 2}
					for _, check := range tc.checks {
						if !strings.Contains(sink.String(), "check="+strconv.Quote(check.ID)+":") {
							t.Fatalf("omitted actual check: %s", sink.String())
						}
					}
				}
				if calls.encoding() != want || !reflect.DeepEqual(recorder.Snapshot(), frozen) {
					t.Fatal("diagnostics changed encoding dispatch or Recorder evidence")
				}
				observationRecorderWantCalls(t, calls, [5]int{0, 1, 2, 1, 2})
			}
			if !reflect.DeepEqual(frozen.Steps[0].Checks, event.Checks) || frozen.Steps[0].Disposition != tc.disposition || recorder.IsFrozen() != (tc.severity == s.LogError) {
				t.Fatalf("diagnostics altered actual property/disposition evidence: %+v", frozen)
			}
		})
	}
}

type observationRecorderLastWriter struct {
	last    []byte
	maximum int
	writes  int
}

func (w *observationRecorderLastWriter) Write(p []byte) (int, error) {
	w.last = append(w.last[:0], p...)
	w.maximum = max(w.maximum, len(p))
	w.writes++
	return len(p), nil
}

func TestObservationRecorderLongBoundedSuffixReplay(t *testing.T) {
	for _, tc := range []struct {
		name     string
		origin   uint64
		capacity int
		bytes    uint64
	}{
		{"origin_zero_capacity_one", 0, 1, 4096},
		{"nonzero_origin_capacity_one", 9000, 1, 4096},
		{"three_steps", 9000, 3, 4096},
		{"byte_eviction", 9000, 1024, 1400},
	} {
		t.Run(tc.name, func(t *testing.T) {
			const turns = 1024
			calls := &observationRecorderCalls{}
			model := observationRecorderModel(calls, 0)
			initial, err := s.CheckInitial(model, 17)
			if err != nil {
				t.Fatal(err)
			}
			options := s.DefaultRecorderOptions()
			options.MaxSteps, options.MaxRetainedBytes = tc.capacity, tc.bytes
			recorder, err := s.NewRecorderWithCheckedInitialAt(initial, tc.origin, s.RunConfig{Strategy: "long-session"}, options)
			if err != nil {
				t.Fatal(err)
			}
			periodic, err := s.SnapshotEvery(17)
			if err != nil {
				t.Fatal(err)
			}
			sink := &observationRecorderLastWriter{}
			observer := observationRecorderObserver(t, sink, s.LogTrace, periodic, s.NewEncodedPayloads[int, int, string](64))
			if err = observer.Observe(model, s.Event[int, int, string]{Kind: s.EventCheckpoint, Sequence: tc.origin, State: 17, Checks: initial.Checks()}); err != nil {
				t.Fatal(err)
			}
			for n := 1; n <= turns; n++ {
				turn := observationRecorderTurn(t, model, calls, 16+n, 1, tc.origin+uint64(n), s.FullChecks())
				if _, err = s.ObserveCheckedRecorder(recorder, turn); err != nil {
					t.Fatal(err)
				}
				if err = observer.Observe(model, observationRecorderEvent(t, turn)); err != nil {
					t.Fatal(err)
				}
				trace := recorder.Snapshot()
				checkpoint := 17 + int(recorder.EvictedSteps())
				if recorder.ObservedSteps() != uint64(n) || recorder.IsFrozen() || recorder.RetainedSteps() < 1 || recorder.RetainedSteps() > tc.capacity || recorder.EvictedSteps()+uint64(recorder.RetainedSteps()) != uint64(n) || recorder.RetainedBytes() > tc.bytes {
					t.Fatalf("unbounded or incoherent retention at %d: %+v", n, trace)
				}
				if string(trace.InitialState) != strconv.Itoa(checkpoint) || !reflect.DeepEqual(trace.InitialChecks, observationRecorderStateChecks(checkpoint, 0)) {
					t.Fatalf("checkpoint did not promote exactly the variable state-check prefix at %d: %+v", n, trace)
				}
				if n%256 == 0 {
					checks := turn.Checks()[:turn.StateCheckCount()]
					if err = observer.Observe(model, s.Event[int, int, string]{Kind: s.EventCheckpoint, Sequence: tc.origin + uint64(n), State: 17 + n, Checks: checks}); err != nil {
						t.Fatal(err)
					}
					var wire bytes.Buffer
					written, err := recorder.WriteTo(&wire)
					if err != nil || uint64(written) != recorder.RetainedBytes() || uint64(wire.Len()) != recorder.RetainedBytes() {
						t.Fatalf("logical serialized-byte accounting: %d %d %d %v", written, wire.Len(), recorder.RetainedBytes(), err)
					}
					observationRecorderReplay(t, trace, 0, recorder.RetainedSteps(), false)
				}
				observationRecorderWantCalls(t, calls, [5]int{0, n, n + 1, n, 2 * n})
			}
			trace := recorder.Snapshot()
			parameters := map[string]string{}
			for _, pair := range trace.Config.Parameters {
				parameters[pair[0]] = pair[1]
			}
			if parameters["stateless.monitor.observed_steps"] != "1024" || parameters["stateless.monitor.evicted_steps"] != strconv.FormatUint(recorder.EvictedSteps(), 10) || parameters["stateless.monitor.checkpoint_after_sequence"] != strconv.FormatUint(recorder.EvictedSteps(), 10) || parameters["stateless.monitor.checking"] != "full" {
				t.Fatalf("lost local retained-window coverage: %v", parameters)
			}
			if tc.origin != 0 && parameters["stateless.monitor.sequence_origin"] != strconv.FormatUint(tc.origin, 10) || tc.origin == 0 && parameters["stateless.monitor.sequence_origin"] != "" {
				t.Fatalf("lost host sequence origin: %v", parameters)
			}
			if recorder.EvictedSteps() == 0 || tc.capacity < turns && recorder.RetainedSteps() != tc.capacity || tc.capacity == turns && recorder.RetainedSteps() >= turns {
				t.Fatalf("retention limit did not evict the prefix: %d steps", recorder.RetainedSteps())
			}
			selected := int((tc.origin+turns)/17 - tc.origin/17)
			if calls.encoding() != [3]int{1 + 2*turns + 5 + selected, 2 * turns, 4 * turns} || sink.writes != turns+5 || sink.maximum > 1024 || !strings.Contains(string(sink.last), "sequence="+strconv.FormatUint(tc.origin+turns, 10)) {
				t.Fatalf("absolute snapshot schedule or logical record bound: encodes=%v records=%d max=%d last=%s", calls.encoding(), sink.writes, sink.maximum, sink.last)
			}
		})
	}
}

func TestObservationRecorderZeroCapacityAndInitialFailure(t *testing.T) {
	for _, capacity := range []int{0, 1} {
		t.Run(strconv.Itoa(capacity), func(t *testing.T) {
			calls := &observationRecorderCalls{}
			model := observationRecorderModel(calls, 7)
			initial, err := s.CheckInitial(model, 7)
			if err != nil {
				t.Fatal(err)
			}
			options := s.DefaultRecorderOptions()
			options.MaxSteps = capacity
			recorder, err := s.NewRecorderWithCheckedInitialAt(initial, 99, s.RunConfig{}, options)
			if capacity == 0 {
				// Zero is rejected by the real Recorder; it is not a zero-slot ring.
				if err == nil || recorder != nil || !strings.Contains(err.Error(), "max_steps") || calls.encoding() != [3]int{} {
					t.Fatalf("zero-capacity contract: recorder=%v err=%v encodes=%v", recorder, err, calls.encoding())
				}
			} else {
				if err != nil {
					t.Fatal(err)
				}
				frozen := recorder.Snapshot()
				var text bytes.Buffer
				observer := observationRecorderObserver(t, &text, s.LogError, s.SnapshotNone(), nil)
				if err = observer.Observe(model, s.Event[int, int, string]{Kind: s.EventCheckpoint, Sequence: 99, State: 7, Checks: initial.Checks()}); err != nil {
					t.Fatal(err)
				}
				recorder.StopWithError(errors.New("later observation failure"))
				if !recorder.IsFrozen() || recorder.ObservedSteps() != 0 || recorder.RetainedSteps() != 0 || frozen.Termination != "property_failed" || !reflect.DeepEqual(recorder.Snapshot(), frozen) || !strings.HasPrefix(text.String(), "Error stream=") {
					t.Fatalf("initial failed checkpoint lost: %+v, log=%s", recorder.Snapshot(), text.String())
				}
				observationRecorderReplay(t, frozen, 7, 0, true)
			}
			observationRecorderWantCalls(t, calls, [5]int{0, 0, 1, 0, 0})
		})
	}
}

type observationRecorderFaultPayloads struct {
	encoded *s.EncodedPayloads[int, int, string]
	fault   string
}

func (p *observationRecorderFaultPayloads) reject(stage string, out io.Writer) error {
	if p.fault == "swallow_limit" && stage == "input" {
		_, _ = out.Write(bytes.Repeat([]byte("x"), 2048))
		return nil // Deliberately swallow overflow: TextObserver must keep it sticky.
	}
	if p.fault == stage {
		_, _ = io.WriteString(out, "unpublished formatter prefix")
		return fmt.Errorf("%s formatter unavailable", stage)
	}
	return nil
}

func (p *observationRecorderFaultPayloads) State(m s.Model[int, int, string], v int, out io.Writer) error {
	if err := p.reject("state", out); err != nil {
		return err
	}
	return p.encoded.State(m, v, out)
}

func (p *observationRecorderFaultPayloads) Input(m s.Model[int, int, string], v int, out io.Writer) error {
	if err := p.reject("input", out); err != nil {
		return err
	}
	if p.fault == "swallow_limit" {
		return nil
	}
	return p.encoded.Input(m, v, out)
}

func (p *observationRecorderFaultPayloads) Output(m s.Model[int, int, string], v string, out io.Writer) error {
	if err := p.reject("output", out); err != nil {
		return err
	}
	return p.encoded.Output(m, v, out)
}

type observationRecorderFaultWriter struct {
	bytes.Buffer
	fault   string
	writes  int
	flushes int
}

func (w *observationRecorderFaultWriter) Write(p []byte) (int, error) {
	w.writes++
	if w.fault == "write" {
		n, _ := w.Buffer.Write(p[:min(7, len(p))])
		return n, errors.New("sink unavailable")
	}
	return w.Buffer.Write(p)
}

func (w *observationRecorderFaultWriter) Flush() error {
	w.flushes++
	if w.fault == "flush" {
		return errors.New("flush unavailable")
	}
	return nil
}

func TestObservationRecorderErrorsPreservePrefixAndFirstFailure(t *testing.T) {
	for _, kind := range []string{"format_input", "format_output", "format_state", "encode_input", "encode_output", "encode_state", "swallow_limit", "max_zero", "max_one", "sink_write", "sink_flush"} {
		t.Run(kind, func(t *testing.T) {
			calls := &observationRecorderCalls{}
			model := observationRecorderModel(calls, 3)
			initial, err := s.CheckInitial(model, 0)
			if err != nil {
				t.Fatal(err)
			}
			options := s.DefaultRecorderOptions()
			options.MaxSteps, options.MaxRetainedBytes = 2, 4096
			recorder, err := s.NewRecorderWithCheckedInitial(initial, s.RunConfig{}, options)
			if err != nil {
				t.Fatal(err)
			}
			sink := &observationRecorderFaultWriter{}
			payloads := &observationRecorderFaultPayloads{encoded: s.NewEncodedPayloads[int, int, string](64)}
			observer := observationRecorderObserver(t, sink, s.LogTrace, s.SnapshotEveryTransition(), payloads)
			first := observationRecorderTurn(t, model, calls, 0, 1, 1, s.FullChecks())
			if _, err = s.ObserveCheckedRecorder(recorder, first); err != nil {
				t.Fatal(err)
			}
			if err = observer.Observe(model, observationRecorderEvent(t, first)); err != nil {
				t.Fatal(err)
			}
			second := observationRecorderTurn(t, model, calls, 1, 1, 2, s.FullChecks())
			if _, err = s.ObserveCheckedRecorder(recorder, second); err != nil {
				t.Fatal(err)
			}
			prefix, written := recorder.Snapshot(), sink.String()
			wantStage := "format"
			switch {
			case strings.HasPrefix(kind, "format_"):
				payloads.fault = strings.TrimPrefix(kind, "format_")
				wantStage = "format " + payloads.fault
			case strings.HasPrefix(kind, "encode_"):
				calls.encodeFault = strings.TrimPrefix(kind, "encode_")
				wantStage = "encode " + calls.encodeFault
			case kind == "swallow_limit":
				payloads.fault = kind
			case kind == "max_zero" || kind == "max_one":
				// A fresh observer with the same borrowed writer has a real 0/1-byte
				// bound. Recovery below uses a healthy filtered event and a new logger.
				logOptions := s.DefaultObservationOptions()
				logOptions.Level, logOptions.Snapshots, logOptions.MaxRecordBytes = s.LogTrace, s.SnapshotEveryTransition(), 0
				if kind == "max_one" {
					logOptions.MaxRecordBytes = 1
				}
				observer, err = s.NewTextObserver[int, int, string](sink, "integration", logOptions, payloads)
				if err != nil {
					t.Fatal(err)
				}
			case kind == "sink_write":
				sink.fault, wantStage = "write", "write"
			case kind == "sink_flush":
				wantStage = "flush"
			}
			event := observationRecorderEvent(t, second)
			err = observer.Observe(model, event)
			if kind == "sink_flush" {
				if err != nil {
					t.Fatal(err)
				}
				written = sink.String()
				sink.fault = "flush"
				err = observer.Flush()
			}
			var attributed *s.TextObserverError
			if !errors.As(err, &attributed) || attributed.Stage != wantStage {
				t.Fatalf("wrong observer error: got %v, want %s", err, wantStage)
			}
			if (kind == "swallow_limit" || strings.HasPrefix(kind, "max_")) && !errors.Is(err, s.ErrObservationRecordLimit) {
				t.Fatalf("lost sticky logical record bound: %v", err)
			}
			poisoned := strings.HasPrefix(kind, "sink_")
			if observer.IsPoisoned() != poisoned || !reflect.DeepEqual(recorder.Snapshot(), prefix) || recorder.IsFrozen() {
				t.Fatal("observer error changed healthy Recorder prefix or wrong sink health")
			}
			if kind == "sink_write" {
				if !strings.HasPrefix(sink.String(), written) || sink.Len() != len(written)+7 {
					t.Fatal("sink failure did not preserve the exact delivered prefix")
				}
			} else if sink.String() != written {
				t.Fatal("formatter/codec/limit/flush error published a partial record")
			}
			observationRecorderWantCalls(t, calls, [5]int{0, 2, 3, 2, 4})
			calls.encodeFault, payloads.fault, sink.fault = "", "", ""
			if poisoned || strings.HasPrefix(kind, "max_") {
				if err = observer.SetLevel(s.LogOff); err != nil {
					t.Fatal(err)
				}
				encodes, writes, flushes := calls.encoding(), sink.writes, sink.flushes
				err = observer.Observe(model, event)
				if poisoned && !errors.Is(err, s.ErrObservationPoisoned) || !poisoned && err != nil {
					t.Fatalf("Off ignored sink health or retained a recoverable error: %v", err)
				}
				if calls.encoding() != encodes || sink.writes != writes || sink.flushes != flushes {
					t.Fatal("Off or poisoned observer invoked codec/writer")
				}
				observer = observationRecorderObserver(t, sink, s.LogTrace, s.SnapshotEveryTransition(), payloads)
			}
			// Retry only diagnostics using the SAME token, not Step/CheckTurn or
			// Recorder. A recovered formatter must not duplicate the recorded turn.
			if err = observer.Observe(model, event); err != nil {
				t.Fatal(err)
			}
			if !strings.Contains(sink.String(), "event=transition sequence=2") || !reflect.DeepEqual(recorder.Snapshot(), prefix) {
				t.Fatal("observer recovery changed exact recording")
			}
			third := observationRecorderTurn(t, model, calls, 2, 1, 3, s.FullChecks())
			if _, err = s.ObserveCheckedRecorder(recorder, third); err != nil {
				t.Fatal(err)
			}
			frozen := recorder.Snapshot()
			payloads.fault = "input"
			laterError := observer.Observe(model, observationRecorderEvent(t, third))
			if laterError == nil {
				t.Fatal("missing later diagnostic failure")
			}
			recorder.StopWithError(laterError)
			if _, err = s.ObserveCheckedRecorder(recorder, third); err == nil {
				t.Fatal("frozen Recorder accepted another observation")
			}
			if !reflect.DeepEqual(recorder.Snapshot(), frozen) || frozen.Termination != "property_failed" || frozen.Error != "" || frozen.Steps[1].Checks[0] != s.Failed("state-bound", "ceiling") {
				t.Fatalf("later observer failure overwrote first property failure: %+v", recorder.Snapshot())
			}
			observationRecorderWantCalls(t, calls, [5]int{0, 3, 4, 3, 6})
			observationRecorderReplay(t, frozen, 3, 2, true)
		})
	}
}

func TestObservationRecorderMissingAndSkippedCoverageDiffer(t *testing.T) {
	for _, kind := range []string{"missing_turn", "periodic_missed", "periodic_matching", "transition_disabled", "full_check_with_skip"} {
		t.Run(kind, func(t *testing.T) {
			calls := &observationRecorderCalls{}
			model := observationRecorderModel(calls, 0)
			initial, err := s.CheckInitial(model, 0)
			if err != nil {
				t.Fatal(err)
			}
			recorder, err := s.NewRecorderWithCheckedInitialAt(initial, 40, s.RunConfig{}, s.DefaultRecorderOptions())
			if err != nil {
				t.Fatal(err)
			}
			var text bytes.Buffer
			observer := observationRecorderObserver(t, &text, s.LogDebug, s.SnapshotEveryTransition(), s.NewEncodedPayloads[int, int, string](64))
			first := observationRecorderTurn(t, model, calls, 0, 1, 41, s.FullChecks())
			if _, err = s.ObserveCheckedRecorder(recorder, first); err != nil {
				t.Fatal(err)
			}
			if err = observer.Observe(model, observationRecorderEvent(t, first)); err != nil {
				t.Fatal(err)
			}
			prefix := recorder.Snapshot()
			policy, input := s.FullChecks(), 1
			wantCalls := [5]int{0, 2, 3, 2, 4}
			switch kind {
			case "periodic_missed":
				policy.StateEvery, wantCalls[2] = 4, 2 // Host sequence 42 is not selected.
			case "periodic_matching":
				policy.StateEvery = 2 // 42 is selected, but coverage is still sampled.
			case "transition_disabled":
				policy.TransitionChecks, wantCalls[3] = false, 1
			case "full_check_with_skip":
				input = 0 // A model-provided skip is different from omitted checking.
			}
			turn := observationRecorderTurn(t, model, calls, 1, input, 42, policy)
			event := observationRecorderEvent(t, turn)
			if err = observer.Observe(model, event); err != nil {
				t.Fatal(err)
			}
			switch kind {
			case "periodic_missed":
				if turn.FullChecking() || turn.StateCheckCount() != 1 || event.Checks[0] != s.Skipped("stateless.state_checks", "periodic checking policy") || !strings.Contains(text.String(), "Warn stream=") {
					t.Fatalf("omitted state coverage was not explicit: %+v", event.Checks)
				}
			case "periodic_matching":
				if turn.FullChecking() || turn.StateCheckCount() != 3 || strings.Contains(text.String(), "Skipped(") {
					t.Fatal("a selected periodic sample was promoted to full coverage")
				}
			case "transition_disabled":
				if turn.FullChecking() || event.Checks[len(event.Checks)-1] != s.Skipped("stateless.transition_checks", "disabled by policy") {
					t.Fatalf("omitted transition coverage was not explicit: %+v", event.Checks)
				}
			case "missing_turn":
				// Both diagnostic records exist; the exact recorder never receives 42.
				turn = observationRecorderTurn(t, model, calls, 2, 1, 43, s.FullChecks())
				if err = observer.Observe(model, observationRecorderEvent(t, turn)); err != nil {
					t.Fatal(err)
				}
				wantCalls = [5]int{0, 3, 4, 3, 6}
				if !strings.Contains(text.String(), "sequence=42") || !strings.Contains(text.String(), "sequence=43") || strings.Contains(text.String(), "Skipped(") {
					t.Fatal("missing Recorder handoff was conflated with skipped property checking")
				}
			}
			_, err = s.ObserveCheckedRecorder(recorder, turn)
			if kind == "full_check_with_skip" {
				if err != nil || recorder.IsFrozen() || !turn.FullChecking() || recorder.ObservedSteps() != 2 || !strings.Contains(text.String(), "Skipped(\"duplicate\")") {
					t.Fatalf("fully invoked checks with an explicit skip were rejected: %v", err)
				}
				observationRecorderReplay(t, recorder.Snapshot(), 0, 2, false)
			} else {
				fragment := "requires full checking"
				if kind == "missing_turn" {
					fragment = "missing or reordered"
				}
				if err == nil || !strings.Contains(err.Error(), fragment) || !recorder.IsFrozen() {
					t.Fatalf("wrong coverage rejection: %v", err)
				}
				frozen := recorder.Snapshot()
				if !reflect.DeepEqual(frozen.InitialState, prefix.InitialState) || !reflect.DeepEqual(frozen.InitialChecks, prefix.InitialChecks) || !reflect.DeepEqual(frozen.Steps, prefix.Steps) || !reflect.DeepEqual(frozen.Config, prefix.Config) || recorder.ObservedSteps() != 1 {
					t.Fatal("coverage rejection discarded or extended the coherent prefix")
				}
				recorder.StopWithError(errors.New("later observer failure"))
				if !reflect.DeepEqual(recorder.Snapshot(), frozen) {
					t.Fatal("later diagnostic failure overwrote the first recording error")
				}
				observationRecorderReplay(t, frozen, 0, 1, false)
			}
			observationRecorderWantCalls(t, calls, wantCalls)
		})
	}
}

func TestObservationRecorderDiagnosticForgeryHasNoAuthority(t *testing.T) {
	calls := &observationRecorderCalls{}
	model := observationRecorderModel(calls, 0)
	initial, err := s.CheckInitial(model, 0)
	if err != nil {
		t.Fatal(err)
	}
	recorder, err := s.NewRecorderWithCheckedInitial(initial, s.RunConfig{}, s.DefaultRecorderOptions())
	if err != nil {
		t.Fatal(err)
	}
	first := observationRecorderTurn(t, model, calls, 0, 1, 1, s.FullChecks())
	if _, err = s.ObserveCheckedRecorder(recorder, first); err != nil {
		t.Fatal(err)
	}
	second := observationRecorderTurn(t, model, calls, 1, 1, 2, s.FullChecks())
	view, err := second.Observation()
	if err != nil {
		t.Fatal(err)
	}
	view.Sequence, view.Before, view.Transition.State = 999, 999, 999
	view.Transition.Outputs[0], view.Transition.Disposition = "forged output", s.Ignored("forged disposition")
	view.Checks[0] = s.Failed("forged property", "not checked")
	view.StateCheckCount = 0
	event := view.Event()
	var text bytes.Buffer
	observer := observationRecorderObserver(t, &text, s.LogTrace, s.SnapshotEveryTransition(), s.NewEncodedPayloads[int, int, string](64))
	if err = observer.Observe(model, event); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(text.String(), "sequence=999") || !strings.Contains(text.String(), "forged property") {
		t.Fatal("diagnostic event did not expose the caller-supplied data")
	}
	// Compile-guard note: ObserveCheckedRecorder(recorder, event) and passing
	// TurnObservation do not type-check; constructing a seal from this external
	// package is also impossible. Such negative snippets are deliberately not run
	// here. Runtime checks below cover zero/nil tokens and detached-view mutation,
	// not unsafe/reflection attacks or a claim of a negative compile-test receipt.
	if _, ok := any(event).(*s.CheckedTurn[int, int, string]); ok {
		t.Fatal("diagnostic event acquired checked authority")
	}
	if _, ok := any(view).(*s.CheckedTurn[int, int, string]); ok {
		t.Fatal("diagnostic view acquired checked authority")
	}
	if _, err = s.ObserveCheckedRecorder(recorder, second); err != nil {
		t.Fatal(err)
	}
	actual := recorder.Snapshot().Steps[1]
	if second.Sequence() != 2 || string(actual.PostState) != "2" || string(actual.Outputs[0]) != "effect:2" || actual.Disposition != s.Accepted() || actual.Checks[0] != s.Passed("state-bound") {
		t.Fatalf("diagnostic mutation changed authentic token: %+v", actual)
	}
	for _, kind := range []string{"zero", "nil"} {
		t.Run(kind, func(t *testing.T) {
			// The authentic initial and first-turn token may feed another Recorder.
			other, err := s.NewRecorderWithCheckedInitial(initial, s.RunConfig{}, s.DefaultRecorderOptions())
			if err != nil {
				t.Fatal(err)
			}
			if _, err = s.ObserveCheckedRecorder(other, first); err != nil {
				t.Fatal(err)
			}
			prefix := other.Snapshot()
			var forged *s.CheckedTurn[int, int, string]
			if kind == "zero" {
				forged = new(s.CheckedTurn[int, int, string])
			}
			if _, err = s.ObserveCheckedRecorder(other, forged); err == nil || !strings.Contains(err.Error(), "invalid checked turn") || !other.IsFrozen() {
				t.Fatalf("forgery accepted: %v", err)
			}
			frozen := other.Snapshot()
			if !reflect.DeepEqual(frozen.Steps, prefix.Steps) || !reflect.DeepEqual(frozen.InitialChecks, prefix.InitialChecks) || other.ObservedSteps() != 1 {
				t.Fatal("forgery destroyed the authentic recorded prefix")
			}
			other.StopWithError(errors.New("later"))
			if !reflect.DeepEqual(other.Snapshot(), frozen) {
				t.Fatal("forgery error was overwritten")
			}
			observationRecorderReplay(t, frozen, 0, 1, false)
		})
	}
	if forged, err := s.NewRecorderWithCheckedInitial(new(s.CheckedInitial[int, int, string]), s.RunConfig{}, s.DefaultRecorderOptions()); err == nil || forged != nil {
		t.Fatal("zero checked initial authorized recording")
	}
	observationRecorderWantCalls(t, calls, [5]int{0, 2, 3, 2, 4})
	observationRecorderReplay(t, recorder.Snapshot(), 0, 2, false)
}

func TestObservationRecorderSnapshotAndLevelChangesUseAbsoluteSequence(t *testing.T) {
	calls := &observationRecorderCalls{}
	model := observationRecorderModel(calls, 0)
	initial, err := s.CheckInitial(model, 7)
	if err != nil {
		t.Fatal(err)
	}
	recorder, err := s.NewRecorderWithCheckedInitialAt(initial, 101, s.RunConfig{}, s.DefaultRecorderOptions())
	if err != nil {
		t.Fatal(err)
	}
	periodic, err := s.SnapshotEvery(2)
	if err != nil {
		t.Fatal(err)
	}
	var text bytes.Buffer
	observer := observationRecorderObserver(t, &text, s.LogTrace, periodic, s.NewEncodedPayloads[int, int, string](64))
	if err = observer.Observe(model, s.Event[int, int, string]{Kind: s.EventCheckpoint, Sequence: 101, State: 7, Checks: initial.Checks()}); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(text.String(), "state=hex:37") {
		t.Fatal("explicit odd-sequence checkpoint did not include state")
	}
	turns := make([]*s.CheckedTurn[int, int, string], 0, 3)
	for n := 1; n <= 3; n++ {
		turn := observationRecorderTurn(t, model, calls, 6+n, 1, uint64(101+n), s.FullChecks())
		turns = append(turns, turn)
		if _, err = s.ObserveCheckedRecorder(recorder, turn); err != nil {
			t.Fatal(err)
		}
		beforeBytes, beforeEncodes := text.Len(), calls.encoding()
		level := s.LogTrace
		if n == 1 {
			level = s.LogDebug
		}
		if err = observer.SetLevel(level); err != nil {
			t.Fatal(err)
		}
		if n == 3 {
			if err = observer.SetSnapshots(s.SnapshotNone()); err != nil {
				t.Fatal(err)
			}
		}
		if text.Len() != beforeBytes || calls.encoding() != beforeEncodes {
			t.Fatal("changing options synthesized history or a checkpoint")
		}
		if err = observer.Observe(model, observationRecorderEvent(t, turn)); err != nil {
			t.Fatal(err)
		}
		record := text.String()[beforeBytes:]
		if strings.Contains(record, "post_state=") {
			t.Fatalf("unexpected snapshot at sequence %d: %s", 101+n, record)
		}
		want := beforeEncodes
		if n > 1 {
			want = [3]int{beforeEncodes[0], beforeEncodes[1] + 1, beforeEncodes[2] + 2}
		}
		if calls.encoding() != want {
			t.Fatalf("snapshot/verbosity callback selection: %v, want %v", calls.encoding(), want)
		}
	}
	frozen := recorder.Snapshot()
	before := calls.encoding()
	if err = observer.Observe(model, s.Event[int, int, string]{Kind: s.EventCheckpoint, Sequence: 104, State: 10, Checks: turns[2].Checks()[:turns[2].StateCheckCount()]}); err != nil {
		t.Fatal(err)
	}
	if calls.encoding() != before {
		t.Fatal("None snapshots encoded an explicit checkpoint")
	}
	if err = observer.SetSnapshots(periodic); err != nil {
		t.Fatal(err)
	}
	if err = observer.Observe(model, s.Event[int, int, string]{Kind: s.EventCheckpoint, Sequence: 103, State: 9, Checks: turns[1].Checks()[:turns[1].StateCheckCount()]}); err != nil {
		t.Fatal(err)
	}
	if calls.encoding() != [3]int{before[0] + 1, before[1], before[2]} {
		t.Fatal("explicit checkpoint incorrectly used periodic divisibility")
	}
	if err = observer.SetSnapshots(s.SnapshotEveryTransition()); err != nil {
		t.Fatal(err)
	}
	if err = observer.Observe(model, observationRecorderEvent(t, turns[2])); err != nil {
		t.Fatal(err)
	}
	if calls.encoding() != [3]int{before[0] + 2, before[1] + 1, before[2] + 2} || !strings.Contains(text.String(), "post_state=hex:3130") || !reflect.DeepEqual(recorder.Snapshot(), frozen) {
		t.Fatal("runtime snapshot changes altered evidence or did not format supplied post-state")
	}
	observationRecorderWantCalls(t, calls, [5]int{0, 3, 4, 3, 6})
	observationRecorderReplay(t, frozen, 0, 3, false)
}

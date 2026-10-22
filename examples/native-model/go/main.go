// Independently installed native binder and checked recorder composition witness.
package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"os"
	"reflect"
)

type State struct{ Count uint32 }
type Input struct{ Amount uint8 }
type Output struct{ Count uint32 }
type Counts struct {
	// Private negative-control state does not alter the success count receipt.
	outputFailure                                                                                                       *s.ModelError
	outputCalls, outputFailures                                                                                         int
	Reducer, State, Transition, Initial, DecodeLimits, StateEncode, InputEncode, OutputEncode, StateDecode, InputDecode int
	version                                                                                                             uint32
	hostile, custom                                                                                                     bool
	work                                                                                                                uint64
}

func normalizedCounts(c Counts) map[string]int {
	return map[string]int{"initial": c.Initial, "reducer": c.Reducer, "state": c.State,
		"transition": c.Transition, "encode_state": c.StateEncode,
		"encode_input": c.InputEncode, "encode_output": c.OutputEncode,
		"decode_state": c.StateDecode, "decode_input": c.InputDecode, "limits": c.DecodeLimits}
}

func must(e error) {
	if e != nil {
		panic(e)
	}
}
func require(ok bool, why string) {
	if !ok {
		panic(why)
	}
}
func application() (s.Model[State, Input, Output], *Counts) { return applicationWithProvider(true) }
func applicationWithProvider(withProvider bool) (s.Model[State, Input, Output], *Counts) {
	c := &Counts{version: 1}
	f, e := s.OrdinaryField("count", s.Uint32Codec())
	must(e)
	state, e := s.OrderedRecord("State", s.PrependRecordField(f, s.NoRecordFields()), func(v State) (s.FieldProduct[uint32, s.Unit], error) {
		if c.hostile {
			panic("hostile state visited")
		}
		return s.FieldProduct[uint32, s.Unit]{Head: v.Count}, nil
	}, func(v s.FieldProduct[uint32, s.Unit]) (State, error) { return State{v.Head}, nil }, s.RecordOptions{})
	must(e)
	add, e := s.TaggedCase(1, "Add", s.Uint8Codec(), func(v Input) (uint8, error) { return v.Amount, nil }, func(v uint8) (Input, error) { return Input{v}, nil })
	must(e)
	input, e := s.TaggedEnum("Input", func(Input) (uint32, error) {
		if c.hostile {
			panic("hostile input visited")
		}
		return 1, nil
	}, s.DefaultEnumOptions(), add)
	must(e)
	output, e := s.NewValueCodec("Output", func(v Output, b *s.EncodeBuffer) error {
		c.outputCalls++
		if c.hostile {
			panic("hostile output visited")
		}
		if e := s.Uint32Codec().EncodeRaw(v.Count, b); e != nil {
			return e
		}
		// Fail in the bound encoder after writing candidate bytes, not in glue.
		if c.outputFailure != nil {
			c.outputFailures++
			return c.outputFailure
		}
		return nil
	}, nil, nil)
	must(e)
	opts := s.DefaultModelCodecOptions()
	if withProvider {
		opts.DecodeLimits = func() (s.DecodeLimits, error) {
			c.DecodeLimits++
			l := s.DefaultDecodeLimits()
			if c.custom {
				l.Work = c.work
			}
			return l, nil
		}
	}
	codec, e := s.BindModelCodec(func() (s.Metadata, error) {
		v := s.Identity("n13-installed-model", "application-v1")
		v.CodecVersion = c.version
		return v, nil
	}, state, input, output, opts)
	must(e)
	// Count binder entry points independently of reducer/check callbacks.
	es, ei, eo, ds, di := codec.EncodeStateInto, codec.EncodeInputInto, codec.EncodeOutputInto, codec.DecodeState, codec.DecodeInput
	codec.EncodeStateInto = func(v State, b *s.EncodeBuffer) error { c.StateEncode++; return es(v, b) }
	codec.EncodeInputInto = func(v Input, b *s.EncodeBuffer) error { c.InputEncode++; return ei(v, b) }
	codec.EncodeOutputInto = func(v Output, b *s.EncodeBuffer) error { c.OutputEncode++; return eo(v, b) }
	codec.DecodeState = func(b []byte) (State, error) { c.StateDecode++; return ds(b) }
	codec.DecodeInput = func(b []byte) (Input, error) { c.InputDecode++; return di(b) }
	m := s.Model[State, Input, Output]{Codec: codec, CloneState: s.ValueCopy[State], CloneInput: s.ValueCopy[Input], CloneOutput: s.ValueCopy[Output], EqualStates: func(a, b State) bool { return a == b }, InitialState: func() (State, error) { c.Initial++; return State{}, nil }, Step: func(v State, i Input) (s.Transition[State, Output], error) {
		c.Reducer++
		v.Count += uint32(i.Amount)
		return s.Accept(v, Output{v.Count}), nil
	}, CheckState: func(v State) ([]s.Check, error) {
		c.State++
		if v.Count >= 3 {
			return []s.Check{s.Failed("threshold.bound", "at least three")}, nil
		}
		return []s.Check{s.Passed("threshold.bound")}, nil
	}, CheckTransition: func(State, Input, s.Transition[State, Output]) ([]s.Check, error) {
		c.Transition++
		return []s.Check{s.Passed("edge.checked")}, nil
	}}
	return m, c
}
func sp(v string) *string { return &v }
func up(v uint64) *uint64 { return &v }
func binaryLimits() s.BinaryLimits {
	return s.BinaryLimits{MaxTotalBytes: 16384, MaxFrameBytes: 4096, MaxBlobBytes: 64, MaxStringBytes: 256, MaxSteps: 16, MaxChecksPerStep: 16, MaxOutputsPerStep: 16, MaxParameters: 32, MaxItems: 256}
}
func evidenceLimits() s.EvidenceLimits {
	return s.EvidenceLimits{Trace: binaryLimits(), MaxMetadataBytes: 1024, MaxLabels: 8, MaxLabelBytes: 128, MaxCausesPerStep: 4, MaxTotalCauses: 8}
}
func produce() (map[string]string, Counts) {
	m, c := application()
	initial, e := s.CheckInitial(m, State{})
	must(e)
	config := s.RunConfig{Strategy: "manual", Seed: up(7), Parameters: [][2]string{{"region", "test"}}}
	opts := s.RecorderOptions{MaxSteps: 3, MaxRetainedBytes: 16384, Limits: binaryLimits()}
	r, e := s.NewRecorderWithCheckedInitial(initial, config, opts)
	must(e)
	p := s.Provenance{Run: "n13-installed-run", Epoch: 7, Manifest: sp("manifest-v1"), Source: sp("consumer-source"), Environment: [][2]string{{"region", "test"}}}
	er, e := s.NewEvidenceRecorder(initial, 0, config, opts, p, evidenceLimits())
	must(e)
	// Recorders snapshot the producer config, including the seed and ordered parameters.
	*config.Seed = 99
	config.Parameters[0][1] = "mutated"
	before := State{}
	for seq := uint64(1); seq <= 3; seq++ {
		in := Input{1}
		turn, e := m.Step(before, in)
		must(e)
		checked, e := s.CheckTurn(m, before, in, turn, seq, s.FullChecks())
		must(e)
		old := *c
		_, e = s.ObserveCheckedRecorder(r, checked)
		must(e)
		causal := s.CausalContext{Machine: sp("worker-a"), Generation: up(9), Correlation: sp("request-1"), LogicalTime: up(10 + seq)}
		if seq > 1 {
			causal.Causes = []s.OutputOrigin{{Sequence: seq - 1, Output: 0}}
		}
		must(s.ObserveEvidenceRecorder(er, checked, causal))
		require(c.Reducer == old.Reducer && c.State == old.State && c.Transition == old.Transition, "checked handoff reexecuted application")
		before = turn.State
	}
	require(c.Reducer == 3 && c.State == 4 && c.Transition == 3 && c.Initial == 0 && c.DecodeLimits == 0, "producer callback counts")
	require(c.StateEncode == 14 && c.InputEncode == 6 && c.OutputEncode == 6 && c.StateDecode == 0 && c.InputDecode == 0, "producer codec counts")
	v1 := r.Snapshot()
	v2 := er.Snapshot()
	require(v2.Provenance.Run == "n13-installed-run" && v2.Provenance.Epoch == 7 && v2.CheckpointAfter == 0, "producer provenance")
	require(v2.Trace.Metadata == s.Identity("n13-installed-model", "application-v1"), "producer identity")
	require(len(v2.Steps) == 3 && len(v1.Steps) == 3, "retained turns")
	for i, row := range v2.Steps {
		require(row.Sequence == uint64(i+1) && row.StateCheckCount == 1, "property phase evidence")
		require(bytes.Equal(v1.Steps[i].Input, []byte{1, 1}) && bytes.Equal(v1.Steps[i].PostState, []byte{byte(i + 1), 0, 0, 0}) && len(v1.Steps[i].Outputs) == 1 && bytes.Equal(v1.Steps[i].Outputs[0], []byte{byte(i + 1), 0, 0, 0}), "independent state/input/output bytes")
	}
	require(len(v1.Steps[2].Checks) == 2 && v1.Steps[2].Checks[0] == s.Failed("threshold.bound", "at least three") && v1.Steps[2].Checks[1] == s.Passed("edge.checked"), "failed property evidence")
	for i, t := range []*s.AuditTrace{v1, &v2.Trace} {
		params := map[string]string{}
		for _, p := range t.Config.Parameters {
			params[p[0]] = p[1]
		}
		want := "16384"
		if i == 1 {
			want = "15328"
		}
		require(params["stateless.monitor.max_steps"] == "3" && params["stateless.monitor.max_retained_bytes"] == want && params["stateless.monitor.observed_steps"] == "3" && params["stateless.monitor.evicted_steps"] == "0", "retained recorder config")
	}
	require(*v1.Config.Seed == 7 && v1.Config.Parameters[0][1] == "test", "producer config mutated")
	a, e := s.MarshalAuditTrace(v1, binaryLimits())
	must(e)
	b, e := s.MarshalTraceV2(v2, evidenceLimits())
	must(e)
	return map[string]string{"v1": hex.EncodeToString(a), "v2": hex.EncodeToString(b)}, *c
}
func transcode(path string) (map[string]string, map[string]Counts) {
	data, e := os.ReadFile(path)
	must(e)
	var env map[string]string
	must(json.Unmarshal(data, &env))
	out := map[string]string{}
	counts := map[string]Counts{}
	for _, version := range []string{"v1", "v2"} {
		wire, e := hex.DecodeString(env[version])
		must(e)
		a, e := s.ReadArtifact(bytes.NewReader(wire), evidenceLimits())
		must(e)
		m, c := application()
		var report *s.ReplayReport
		if version == "v1" {
			require(a.V1 != nil, "expected v1")
			report, e = s.ReplayWithObservationsBounded(m, a.V1, false, binaryLimits(), s.ReplayObserver[State, Input, Output]{})
		} else {
			require(a.V2 != nil, "expected v2")
			report, e = s.ReplayTraceV2(m, a.V2, false, evidenceLimits())
		}
		must(e)
		require(report.Outcome == "exact" && report.StepsVerified == 3 && report.FailureReproduced, "replay failed")
		require(c.Reducer == 3 && c.State == 4 && c.Transition == 3 && c.Initial == 0 && c.DecodeLimits == 4, "consumer callback counts")
		require(c.StateEncode == 4 && c.InputEncode == 3 && c.OutputEncode == 3 && c.StateDecode == 1 && c.InputDecode == 3, "consumer codec counts")
		rewritten, e := s.MarshalArtifact(a, evidenceLimits())
		must(e)
		require(bytes.Equal(wire, rewritten), "artifact bytes changed")
		out[version] = hex.EncodeToString(rewritten)
		counts[version] = *c
	}
	return out, counts
}

// Negative captures stay separate from every produced/transcoded success artifact.
func requireCaptureCounts(c *Counts, model [4]int, codec [6]int, failures int) {
	require([4]int{c.Initial, c.Reducer, c.State, c.Transition} == model, "negative capture model counts")
	require([6]int{c.StateEncode, c.InputEncode, c.OutputEncode, c.StateDecode, c.InputDecode, c.DecodeLimits} == codec, "negative capture codec counts")
	require(c.outputCalls == codec[2] && c.outputFailures == failures, "negative capture actual Output callbacks")
}
func requireCaptureError(e error, message string) {
	owned, ok := e.(*s.ModelError)
	// The public recorder owns a staged ModelError, not the raw callback pointer.
	require(ok && owned.Message == message, "negative capture error owner/stage")
}
func requireFirstCapture(t *s.AuditTrace) {
	require(t.Metadata == s.Identity("n13-installed-model", "application-v1"), "negative prefix identity")
	require(t.Config.Strategy == "manual" && t.Config.Seed != nil && *t.Config.Seed == 7, "negative prefix producer config")
	params := map[string]string{}
	for _, p := range t.Config.Parameters {
		params[p[0]] = p[1]
	}
	require(params["region"] == "test" && params["stateless.monitor.checking"] == "full" && params["stateless.monitor.observed_steps"] == "1" && params["stateless.monitor.evicted_steps"] == "0" && params["stateless.monitor.checkpoint_after_sequence"] == "0", "negative prefix recorder config")
	require(bytes.Equal(t.InitialState, []byte{0, 0, 0, 0}) && reflect.DeepEqual(t.InitialChecks, []s.Check{s.Passed("threshold.bound")}), "negative prefix checkpoint")
	want := []s.TraceStep{{Input: []byte{1, 1}, Disposition: s.Accepted(), Outputs: [][]byte{{1, 0, 0, 0}}, PostState: []byte{1, 0, 0, 0}, Checks: []s.Check{s.Passed("threshold.bound"), s.Passed("edge.checked")}}}
	require(reflect.DeepEqual(t.Steps, want), "negative prefix first captured turn")
	require(t.Termination == "interrupted" && t.Error == "", "negative prefix unexpectedly terminal")
}
func requireFailedPrefix(t, prefix *s.AuditTrace, message string) {
	require(t.Metadata == prefix.Metadata && reflect.DeepEqual(t.Config, prefix.Config), "failure changed identity/config")
	require(bytes.Equal(t.InitialState, prefix.InitialState) && reflect.DeepEqual(t.InitialChecks, prefix.InitialChecks), "failure changed checkpoint")
	require(reflect.DeepEqual(t.Steps, prefix.Steps), "failure changed retained trace prefix")
	require(t.Termination == "model_error" && t.Error == message, "failure footer lost callback ownership")
}
func captureFailureControls() {
	m, c := application()
	initial, e := s.CheckInitial(m, State{})
	must(e)
	requireCaptureCounts(c, [4]int{0, 0, 1, 0}, [6]int{}, 0)
	config := s.RunConfig{Strategy: "manual", Seed: up(7), Parameters: [][2]string{{"region", "test"}}}
	opts := s.RecorderOptions{MaxSteps: 3, MaxRetainedBytes: 16384, Limits: binaryLimits()}
	r, e := s.NewRecorderWithCheckedInitial(initial, config, opts)
	must(e)
	requireCaptureCounts(c, [4]int{0, 0, 1, 0}, [6]int{1, 0, 0, 0, 0, 0}, 0)
	p := s.Provenance{Run: "n13-installed-run", Epoch: 7, Manifest: sp("manifest-v1"), Source: sp("consumer-source"), Environment: [][2]string{{"region", "test"}}}
	er, e := s.NewEvidenceRecorder(initial, 0, config, opts, p, evidenceLimits())
	must(e)
	requireCaptureCounts(c, [4]int{0, 0, 1, 0}, [6]int{2, 0, 0, 0, 0, 0}, 0)
	causal := func(seq uint64) s.CausalContext {
		v := s.CausalContext{Machine: sp("worker-a"), Generation: up(9), Correlation: sp("request-1"), LogicalTime: up(10 + seq)}
		if seq > 1 {
			v.Causes = []s.OutputOrigin{{Sequence: seq - 1, Output: 0}}
		}
		return v
	}
	in := Input{1}
	first, e := m.Step(State{}, in)
	must(e)
	checked, e := s.CheckTurn(m, State{}, in, first, 1, s.FullChecks())
	must(e)
	requireCaptureCounts(c, [4]int{0, 1, 2, 1}, [6]int{2, 0, 0, 0, 0, 0}, 0)
	checks, e := s.ObserveCheckedRecorder(r, checked)
	must(e)
	require(reflect.DeepEqual(checks, checked.Checks()), "checked recorder changed check batch")
	requireCaptureCounts(c, [4]int{0, 1, 2, 1}, [6]int{4, 1, 1, 0, 0, 0}, 0)
	must(s.ObserveEvidenceRecorder(er, checked, causal(1)))
	requireCaptureCounts(c, [4]int{0, 1, 2, 1}, [6]int{6, 2, 2, 0, 0, 0}, 0)
	prefix, evidencePrefix := r.Snapshot(), er.Snapshot()
	requireFirstCapture(prefix)
	requireFirstCapture(&evidencePrefix.Trace)
	require(!r.IsFrozen() && !er.IsFrozen(), "negative first capture froze")
	require(evidencePrefix.CheckpointAfter == 0 && reflect.DeepEqual(evidencePrefix.Provenance, p), "negative evidence origin/provenance")
	require(len(evidencePrefix.Steps) == 1 && evidencePrefix.Steps[0].Sequence == 1 && evidencePrefix.Steps[0].StateCheckCount == 1, "negative evidence first phase row")
	firstCausal := evidencePrefix.Steps[0].Causal
	wantCausal := causal(1)
	require(reflect.DeepEqual(firstCausal.Machine, wantCausal.Machine) && reflect.DeepEqual(firstCausal.Generation, wantCausal.Generation) && reflect.DeepEqual(firstCausal.Correlation, wantCausal.Correlation) && reflect.DeepEqual(firstCausal.LogicalTime, wantCausal.LogicalTime) && len(firstCausal.Causes) == 0, "negative evidence first causal context")

	second, e := m.Step(first.State, in)
	must(e)
	checked, e = s.CheckTurn(m, first.State, in, second, 2, s.FullChecks())
	must(e)
	require(second.State == (State{2}) && reflect.DeepEqual(checked.Checks(), []s.Check{s.Passed("threshold.bound"), s.Passed("edge.checked")}), "second checked turn must pass before capture")
	requireCaptureCounts(c, [4]int{0, 2, 3, 2}, [6]int{6, 2, 2, 0, 0, 0}, 0)
	// The token is already checked; arm only the actual Output callback now.
	c.outputFailure = &s.ModelError{Message: "n13 output encode failure"}
	message := "runtime encode output: encode callback: n13 output encode failure"
	checks, e = s.ObserveCheckedRecorder(r, checked)
	requireCaptureError(e, message)
	require(checks == nil, "failed capture returned a successful check batch")
	requireCaptureCounts(c, [4]int{0, 2, 3, 2}, [6]int{7, 3, 3, 0, 0, 0}, 1)
	// A valid link to retained output 1:0 must reach the same failing encoder.
	requireCaptureError(s.ObserveEvidenceRecorder(er, checked, causal(2)), message)
	requireCaptureCounts(c, [4]int{0, 2, 3, 2}, [6]int{8, 4, 4, 0, 0, 0}, 2)
	require(r.IsFrozen() && er.IsFrozen(), "capture error did not freeze")
	require(r.ObservedSteps() == 1 && r.RetainedSteps() == 1 && r.EvictedSteps() == 0, "failed capture changed recorder counters")
	failed, failedEvidence := r.Snapshot(), er.Snapshot()
	requireFailedPrefix(failed, prefix, message)
	requireFailedPrefix(&failedEvidence.Trace, &evidencePrefix.Trace, message)
	require(failedEvidence.CheckpointAfter == evidencePrefix.CheckpointAfter && reflect.DeepEqual(failedEvidence.Provenance, evidencePrefix.Provenance) && reflect.DeepEqual(failedEvidence.Steps, evidencePrefix.Steps), "failure changed evidence phase/causal journal")
	for _, want := range []struct {
		seq, check uint64
		phase      s.CheckPhase
	}{{0, 0, s.PhaseInitialState}, {1, 0, s.PhaseState}, {1, 1, s.PhaseTransition}} {
		phase, ok := failedEvidence.CheckPhase(want.seq, want.check)
		require(ok && phase == want.phase, "retained property phase changed")
	}
	_, present := failedEvidence.CheckPhase(2, 0)
	require(!present, "failed capture added a phase row")

	c.outputFailure = nil
	checks, e = s.ObserveCheckedRecorder(r, checked)
	requireCaptureError(e, "runtime recorder is frozen; export its retained trace")
	require(checks == nil, "frozen recorder returned checks")
	requireCaptureCounts(c, [4]int{0, 2, 3, 2}, [6]int{8, 4, 4, 0, 0, 0}, 2)
	requireCaptureError(s.ObserveEvidenceRecorder(er, checked, causal(2)), "runtime evidence recorder is frozen; export its retained trace")
	requireCaptureCounts(c, [4]int{0, 2, 3, 2}, [6]int{8, 4, 4, 0, 0, 0}, 2)
	require(reflect.DeepEqual(r.Snapshot(), failed) && reflect.DeepEqual(er.Snapshot(), failedEvidence), "frozen retry changed retained error/evidence")
	// Only in-memory negative round trips: no success envelope or output decoder.
	a, e := s.MarshalAuditTrace(failed, binaryLimits())
	must(e)
	b, e := s.MarshalTraceV2(failedEvidence, evidenceLimits())
	must(e)
	x, e := s.ReadArtifact(bytes.NewReader(a), evidenceLimits())
	must(e)
	y, e := s.ReadArtifact(bytes.NewReader(b), evidenceLimits())
	must(e)
	require(reflect.DeepEqual(x.V1, failed) && reflect.DeepEqual(y.V2, failedEvidence), "negative artifact round trip changed prefix/diagnostic")
	requireCaptureCounts(c, [4]int{0, 2, 3, 2}, [6]int{8, 4, 4, 0, 0, 0}, 2)
}

// Controls run in each fresh installed process, separately from artifact counters.
func controls() {
	// This construction genuinely omits DecodeLimits, selecting binder defaults.
	defaults, dc := applicationWithProvider(false)
	_, de := defaults.Codec.DecodeState([]byte{0, 0, 0, 0})
	must(de)
	_, de = defaults.Codec.DecodeInput([]byte{1, 1})
	must(de)
	require(dc.DecodeLimits == 0, "omitted-provider default invoked a provider")
	m, c := application()
	codec := m.Codec
	c.version = 2
	c.hostile = true
	var prefix []byte
	must(s.WithEncodeBuffer(&prefix, 64, func(b *s.EncodeBuffer) error {
		must(b.Append([]byte{0xa5}))
		hooks := []func() error{
			func() error { _, e := codec.EncodeState(State{}); return e },
			func() error { _, e := codec.EncodeInput(Input{}); return e },
			func() error { _, e := codec.EncodeOutput(Output{}); return e },
			func() error { return codec.EncodeStateInto(State{}, b) },
			func() error { return codec.EncodeInputInto(Input{}, b) },
			func() error { return codec.EncodeOutputInto(Output{}, b) },
			func() error { _, e := codec.DecodeState([]byte{255}); return e },
			func() error { _, e := codec.DecodeInput([]byte{255}); return e },
		}
		for _, h := range hooks {
			e := h()
			require(e != nil && e.Error() == "ModelCodec derive version does not match Model metadata", "version did not win")
		}
		require(bytes.Equal(prefix, []byte{0xa5}) && c.DecodeLimits == 0, "version touched sink or limits")
		return nil
	}))
	c.version = 1
	c.hostile = false
	// All six encoder hooks remain valid even when the next decoder has no work.
	c.custom = true
	c.work = 0
	_, e := codec.EncodeState(State{})
	must(e)
	_, e = codec.EncodeInput(Input{1})
	must(e)
	_, e = codec.EncodeOutput(Output{})
	must(e)
	var wire []byte
	must(s.WithEncodeBuffer(&wire, 64, func(b *s.EncodeBuffer) error {
		must(codec.EncodeStateInto(State{}, b))
		must(codec.EncodeInputInto(Input{1}, b))
		return codec.EncodeOutputInto(Output{}, b)
	}))
	require(c.DecodeLimits == 0, "encoder consulted decode limits")
	_, e = codec.DecodeState([]byte{0, 0, 0, 0})
	require(e != nil, "zero work accepted")
	c.work = 2
	_, e = codec.DecodeState([]byte{0, 0, 0, 0})
	must(e)
	c.work = 1
	_, e = codec.DecodeState([]byte{0, 0, 0, 0})
	require(e != nil, "one-under work accepted")
	// Enum payload is raw, so its exact work admission is one root guard.
	c.work = 1
	_, e = codec.DecodeInput([]byte{1, 1})
	must(e)
	c.work = 0
	_, e = codec.DecodeInput([]byte{1, 1})
	require(e != nil, "input one-under work accepted")
	c.work = 1
	_, e = codec.DecodeInput([]byte{1, 1})
	must(e)
	c.custom = false
	_, e = codec.DecodeState([]byte{0, 0, 0, 0})
	must(e)
	_, e = codec.DecodeInput([]byte{1, 1})
	must(e)
	for _, b := range [][]byte{{}, {0, 0, 0}, {0, 0, 0, 0, 0}} {
		_, e = codec.DecodeState(b)
		require(e != nil, "malformed/trailing state accepted")
	}
	for _, b := range [][]byte{{}, {1}, {2, 1}, {1, 1, 0}} {
		_, e = codec.DecodeInput(b)
		require(e != nil, "malformed/trailing input accepted")
	}
	require(c.DecodeLimits == 15, "fresh provider call count")
}
func main() {
	controls()
	captureFailureControls()
	require(len(os.Args) >= 3, "usage: produce DEST | transcode SOURCE DEST")
	var env map[string]string
	var receipt any
	var captureCounts map[string]int
	replayCounts := map[string]map[string]int{}
	switch os.Args[1] {
	case "produce":
		var counts Counts
		env, counts = produce()
		receipt = counts
		captureCounts = normalizedCounts(counts)
	case "transcode":
		require(len(os.Args) == 4, "transcode SOURCE DEST")
		var counts map[string]Counts
		env, counts = transcode(os.Args[2])
		receipt = counts
		for version, c := range counts {
			replayCounts[version] = normalizedCounts(c)
		}
	default:
		panic("unknown command")
	}
	dst := os.Args[len(os.Args)-1]
	b, e := json.Marshal(env)
	must(e)
	must(os.WriteFile(dst, b, 0600))
	b, e = json.Marshal(map[string]any{"engine": "go", "capture_failure_controls": true, "capture_counts": captureCounts, "replay_counts": replayCounts, "counts": receipt, "controls": "eight-version-hooks,fresh-limits,malformed-trailing,encode-no-limits", "custom_output_encode_only": true, "checked_handoff_no_reexecution": true})
	must(e)
	fmt.Println(string(b))
}

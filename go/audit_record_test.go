package statelessness_test

import (
	"bytes"
	"errors"
	"reflect"
	"strings"
	"testing"
	"unicode/utf8"

	s "github.com/Jagalite/statelessness/go"
)

func auditRecordingModel() s.Model[int, int, string] {
	return s.Model[int, int, string]{
		InitialState: func() (int, error) { return 0, nil },
		Step:         func(state, input int) (s.Transition[int, string], error) { return s.Accept(state+input, "o"), nil },
		CheckState:   func(int) ([]s.Check, error) { return []s.Check{s.Passed("c")}, nil },
		CloneState:   s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[string],
		Codec: &s.Codec[int, int, string]{Metadata: func() (s.Metadata, error) { return s.Identity("m", "b"), nil }, EncodeState: func(state int) ([]byte, error) { return []byte{byte(state)}, nil }, DecodeState: func(b []byte) (int, error) { return int(b[0]), nil }, EncodeInput: func(input int) ([]byte, error) { return []byte{byte(input)}, nil }, DecodeInput: func(b []byte) (int, error) { return int(b[0]), nil }, EncodeOutput: func(output string) ([]byte, error) { return []byte(output), nil }},
	}
}
func assertAuditWritable(t *testing.T, trace *s.AuditTrace, limits s.BinaryLimits) {
	t.Helper()
	var b bytes.Buffer
	if e := trace.WriteBinaryWithLimits(&b, limits); e != nil {
		t.Fatalf("recorded artifact fails write: %v; trace=%+v", e, trace)
	}
}
func TestRecordAuditConfigOwnershipAndCompleteChecks(t *testing.T) {
	m := auditRecordingModel()
	seed := uint64(8)
	config := s.RunConfig{Strategy: "test", Seed: &seed, Parameters: [][2]string{{"k", "v"}, {"k", "v2"}}}
	m.InitialState = func() (int, error) { seed = 99; config.Parameters[0][1] = "mutated"; return 0, nil }
	m.CheckState = func(state int) ([]s.Check, error) {
		if state == 1 {
			return []s.Check{s.Failed("first", "bad"), s.Passed("after")}, nil
		}
		return []s.Check{s.Passed("c")}, nil
	}
	m.CheckTransition = func(int, int, s.Transition[int, string]) ([]s.Check, error) {
		return []s.Check{s.Failed("edge", "also")}, nil
	}
	trace, e := s.RecordAudit(m, s.Values([]int{1, 1}), config, 2, s.DefaultBinaryLimits())
	if e != nil {
		t.Fatal(e)
	}
	if trace.Termination != "property_failed" || len(trace.Steps) != 1 || len(trace.Steps[0].Checks) != 3 {
		t.Fatalf("%+v", trace)
	}
	if *trace.Config.Seed != 8 || trace.Config.Parameters[0][1] != "v" {
		t.Fatal("run config aliased")
	}
	assertAuditWritable(t, trace, s.DefaultBinaryLimits())
}
func TestRecordAuditInitialFailureNeedsOnlyTerminalFooter(t *testing.T) {
	m := auditRecordingModel()
	m.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Failed("c", "")}, nil }
	limits := s.DefaultBinaryLimits()
	limits.MaxStringBytes = 32
	trace, e := s.RecordAudit(m, s.Values([]int{1}), s.RunConfig{Strategy: "s"}, 1, limits)
	if e != nil {
		t.Fatal(e)
	}
	var buf bytes.Buffer
	if e = trace.WriteBinaryWithLimits(&buf, limits); e != nil {
		t.Fatal(e)
	}
	limits.MaxTotalBytes = uint64(buf.Len())
	trace, e = s.RecordAudit(m, s.Values([]int{1}), s.RunConfig{Strategy: "s"}, 1, limits)
	if e != nil {
		t.Fatal(e)
	}
	if trace.Termination != "property_failed" || len(trace.Steps) != 0 {
		t.Fatalf("%+v", trace)
	}
	assertAuditWritable(t, trace, limits)
	m.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("c")}, nil }
	if trace, e = s.RecordAudit(m, s.Values([]int{1}), s.RunConfig{Strategy: "s"}, 1, limits); e == nil || trace != nil {
		t.Fatal("passing initial checkpoint admitted without error-footer reserve")
	}
}
func TestRecordAuditQuotasAndPrefix(t *testing.T) {
	tests := []struct {
		name        string
		change      func(*s.BinaryLimits)
		initial     bool
		termination string
	}{
		{"total", func(l *s.BinaryLimits) { l.MaxTotalBytes = 1 }, true, ""},
		{"frame", func(l *s.BinaryLimits) { l.MaxFrameBytes = 12 }, true, ""},
		{"blob", func(l *s.BinaryLimits) { l.MaxBlobBytes = 0 }, true, ""},
		{"string", func(l *s.BinaryLimits) { l.MaxStringBytes = 0 }, true, ""},
		{"parameters", func(l *s.BinaryLimits) { l.MaxParameters = 0 }, true, ""},
		{"checks", func(l *s.BinaryLimits) { l.MaxChecksPerStep = 0 }, true, ""},
		{"items", func(l *s.BinaryLimits) { l.MaxItems = 4 }, false, "model_error"},
		{"outputs", func(l *s.BinaryLimits) { l.MaxOutputsPerStep = 0 }, false, "model_error"},
		{"steps", func(l *s.BinaryLimits) { l.MaxSteps = 0 }, false, "step_limit"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			limits := s.DefaultBinaryLimits()
			tt.change(&limits)
			trace, e := s.RecordAudit(auditRecordingModel(), s.Values([]int{1}), s.RunConfig{Strategy: "s", Parameters: [][2]string{{"k", "v"}}}, 1, limits)
			if tt.initial {
				if e == nil || trace != nil {
					t.Fatalf("expected initial failure, trace=%+v err=%v", trace, e)
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			if trace.Termination != tt.termination || len(trace.Steps) != 0 {
				t.Fatalf("%+v", trace)
			}
			assertAuditWritable(t, trace, limits)
		})
	}
}
func TestRecordAuditPrefixErrorsAndUTF8Reason(t *testing.T) {
	m := auditRecordingModel()
	m.Step = func(state, input int) (s.Transition[int, string], error) {
		if state == 1 {
			return s.Transition[int, string]{}, errors.New(strings.Repeat("界", 200))
		}
		return s.Accept(1, "o"), nil
	}
	limits := s.DefaultBinaryLimits()
	trace, e := s.RecordAudit(m, s.Values([]int{1, 1}), s.RunConfig{Strategy: "s"}, 2, limits)
	if e != nil {
		t.Fatal(e)
	}
	if len(trace.Steps) != 1 || trace.Termination != "model_error" || !utf8.ValidString(trace.Error) || len(trace.Error) > 256 || !strings.HasSuffix(trace.Error, "...") {
		t.Fatalf("%+v", trace)
	}
	assertAuditWritable(t, trace, limits)
}
func TestRecordAuditIntoPreferenceAndEarlyOutputPreflight(t *testing.T) {
	for _, kind := range []string{"outputs", "items", "checks", "disposition"} {
		t.Run(kind, func(t *testing.T) {
			m := auditRecordingModel()
			encodes := []string{}
			m.Codec.EncodeState = nil
			m.Codec.EncodeInput = nil
			m.Codec.EncodeOutput = nil
			m.Codec.EncodeStateInto = func(v int, b *s.EncodeBuffer) error {
				encodes = append(encodes, "state")
				return b.Append([]byte{byte(v)})
			}
			m.Codec.EncodeInputInto = func(v int, b *s.EncodeBuffer) error {
				encodes = append(encodes, "input")
				return b.Append([]byte{byte(v)})
			}
			m.Codec.EncodeOutputInto = func(v string, b *s.EncodeBuffer) error {
				encodes = append(encodes, "output")
				return b.Append([]byte(v))
			}
			limits := s.DefaultBinaryLimits()
			switch kind {
			case "outputs":
				limits.MaxOutputsPerStep = 0
			case "items":
				limits.MaxItems = 3
			case "checks":
				limits.MaxChecksPerStep = 1
				m.CheckTransition = func(int, int, s.Transition[int, string]) ([]s.Check, error) { return []s.Check{s.Passed("edge")}, nil }
			case "disposition":
				limits.MaxStringBytes = 32
				m.Step = func(int, int) (s.Transition[int, string], error) {
					return s.Transition[int, string]{State: 1, Outputs: []string{"o"}, Disposition: s.Rejected(strings.Repeat("r", 33))}, nil
				}
			}
			trace, e := s.RecordAudit(m, s.Values([]int{1}), s.RunConfig{Strategy: "s"}, 1, limits)
			if e != nil {
				t.Fatal(e)
			}
			if trace.Termination != "model_error" || !reflect.DeepEqual(encodes, []string{"state", "input"}) {
				t.Fatalf("trace=%+v callbacks=%v", trace, encodes)
			}
			assertAuditWritable(t, trace, limits)
		})
	}
}

func TestRecordAuditSharedFrameBudgetStopsBeforePostState(t *testing.T) {
	m := auditRecordingModel()
	stateCalls, outputCalls := 0, 0
	m.Codec.EncodeStateInto = func(v int, b *s.EncodeBuffer) error { stateCalls++; return b.Append([]byte{byte(v)}) }
	m.Codec.EncodeOutputInto = func(v string, b *s.EncodeBuffer) error { outputCalls++; return b.Append([]byte(v)) }
	m.Step = func(int, int) (s.Transition[int, string], error) {
		return s.Accept(1, strings.Repeat("x", 80), strings.Repeat("y", 80)), nil
	}
	limits := s.DefaultBinaryLimits()
	limits.MaxFrameBytes = 160
	trace, e := s.RecordAudit(m, s.Values([]int{1}), s.RunConfig{Strategy: "s"}, 1, limits)
	if e != nil {
		t.Fatal(e)
	}
	if trace.Termination != "model_error" || len(trace.Steps) != 0 || stateCalls != 1 || outputCalls != 2 {
		t.Fatalf("trace=%+v state=%d output=%d", trace, stateCalls, outputCalls)
	}
	assertAuditWritable(t, trace, limits)
}
func TestRecordAuditFailingTurnCanUseErrorFooterReserve(t *testing.T) {
	m := auditRecordingModel()
	m.CheckState = func(state int) ([]s.Check, error) {
		if state == 1 {
			return []s.Check{s.Failed("c", "")}, nil
		}
		return []s.Check{s.Passed("c")}, nil
	}
	limits := s.DefaultBinaryLimits()
	trace, e := s.RecordAudit(m, s.Values([]int{1}), s.RunConfig{Strategy: "s"}, 1, limits)
	if e != nil {
		t.Fatal(e)
	}
	var b bytes.Buffer
	if e = trace.WriteBinaryWithLimits(&b, limits); e != nil {
		t.Fatal(e)
	}
	// Reduce the reason reserve so both the initial run plus reserve and the
	// larger terminal failing turn plus its small footer fit at this boundary.
	limits.MaxStringBytes = 32
	limits.MaxTotalBytes = uint64(b.Len())
	trace, e = s.RecordAudit(m, s.Values([]int{1}), s.RunConfig{Strategy: "s"}, 1, limits)
	if e != nil {
		t.Fatal(e)
	}
	if trace.Termination != "property_failed" || len(trace.Steps) != 1 {
		t.Fatalf("terminal candidate failed admission: %+v", trace)
	}
	assertAuditWritable(t, trace, limits)
	m.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("c")}, nil }
	trace, e = s.RecordAudit(m, s.Values([]int{1}), s.RunConfig{Strategy: "s"}, 1, limits)
	if e != nil {
		t.Fatal(e)
	}
	if trace.Termination != "model_error" || len(trace.Steps) != 0 {
		t.Fatalf("passing candidate consumed future error reserve: %+v", trace)
	}
	assertAuditWritable(t, trace, limits)
}
func TestRecordAuditCompleteBatchNotFirstFailurePrefix(t *testing.T) {
	m := auditRecordingModel()
	m.CheckStateInto = func(state int, sink *s.CheckSink) error {
		if state == 0 {
			return sink.Push(s.Passed("c"))
		}
		if e := sink.Push(s.Failed("first", "bad")); e != nil {
			return e
		}
		return sink.Push(s.Check{ID: "bad", Status: "invalid"})
	}
	trace, e := s.RecordAudit(m, s.Values([]int{1}), s.RunConfig{Strategy: "s"}, 1, s.DefaultBinaryLimits())
	if e != nil {
		t.Fatal(e)
	}
	if trace.Termination != "model_error" || len(trace.Steps) != 0 {
		t.Fatalf("incomplete failing batch admitted: %+v", trace)
	}
	assertAuditWritable(t, trace, s.DefaultBinaryLimits())
}

func TestRecordAuditEngineStampReservedParametersAndQuota(t *testing.T) {
	model := auditRecordingModel()
	calls := 0
	model.InitialState = func() (int, error) { calls++; return 0, nil }
	for _, key := range []string{"stateless.engine.version", "stateless.engine.build", "stateless.engine.other"} {
		if trace, e := s.RecordAudit(model, s.Values([]int{}), s.RunConfig{Strategy: "s", Parameters: [][2]string{{key, "spoof"}}}, 0, s.DefaultBinaryLimits()); e == nil || trace != nil || calls != 0 {
			t.Fatal("reserved engine parameter reached model", key, calls, e)
		}
	}
	config := s.RunConfig{Strategy: "s", Parameters: [][2]string{{"duplicate", "one"}, {"duplicate", "two"}}}
	trace, e := s.RecordAudit(model, s.Values([]int{}), config, 0, s.DefaultBinaryLimits())
	if e != nil {
		t.Fatal(e)
	}
	expected := [][2]string{{"duplicate", "one"}, {"duplicate", "two"}, {"stateless.engine.version", s.Version}, {"stateless.engine.build", s.NativeEngineBuild}}
	if !reflect.DeepEqual(trace.Config.Parameters, expected) || len(config.Parameters) != 2 {
		t.Fatal(trace.Config, config)
	}
	limits := s.DefaultBinaryLimits()
	limits.MaxParameters = 1
	if _, e = s.RecordAudit(model, s.Values([]int{}), s.RunConfig{Strategy: "s"}, 0, limits); e == nil {
		t.Fatal("engine parameters bypassed parameter quota")
	}
}

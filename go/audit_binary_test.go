package statelessness_test

import (
	"bytes"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	s "github.com/Jagalite/statelessness/go"
	"io"
	"os"
	"reflect"
	"strconv"
	"testing"
)

type binaryCheck struct {
	ID, Status string
	Reason     *string
}
type binaryDisposition struct {
	Kind   string
	Reason *string
}
type binaryStep struct {
	Input       string
	Disposition binaryDisposition
	Outputs     []string
	PostState   string `json:"post_state"`
	Checks      []binaryCheck
}
type binaryValue struct {
	Metadata s.Metadata
	Config   struct {
		Strategy   string
		Seed       *string
		Parameters [][2]string
	}
	InitialState  string        `json:"initial_state"`
	InitialChecks []binaryCheck `json:"initial_checks"`
	Steps         []binaryStep
	Termination   struct {
		Kind   string
		Reason *string
	}
}
type binaryFixture struct {
	Case          string
	Valid         bool
	BytesHex      string `json:"bytes_hex"`
	Trace         *binaryValue
	ExpectedError struct {
		Kind   string
		Detail *string
	} `json:"expected_error"`
	Limits map[string]uint64
}

func binaryCorpus(t *testing.T) []binaryFixture {
	t.Helper()
	path := os.Getenv("STATELESS_BINARY_CORPUS")
	if path == "" {
		path = "../conformance/native-binary-v1.json"
	}
	data, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct{ Fixtures []binaryFixture }
	if e = json.Unmarshal(data, &corpus); e != nil {
		t.Fatal(e)
	}
	return corpus.Fixtures
}
func binaryBytes(t *testing.T, value string) []byte {
	t.Helper()
	data, e := hex.DecodeString(value)
	if e != nil {
		t.Fatal(e)
	}
	return data
}
func binaryChecks(checks []binaryCheck) []s.Check {
	result := make([]s.Check, len(checks))
	for k, c := range checks {
		result[k] = s.Check{ID: c.ID, Status: c.Status}
		if c.Reason != nil {
			result[k].Details = *c.Reason
		}
	}
	return result
}
func binaryAudit(t *testing.T, value binaryValue) *s.AuditTrace {
	t.Helper()
	trace := s.AuditTrace{Trace: s.Trace{Metadata: value.Metadata, InitialState: binaryBytes(t, value.InitialState), InitialChecks: binaryChecks(value.InitialChecks), Steps: []s.TraceStep{}, Termination: value.Termination.Kind}, Config: s.RunConfig{Strategy: value.Config.Strategy, Parameters: value.Config.Parameters}}
	if value.Config.Seed != nil {
		seed, e := strconv.ParseUint(*value.Config.Seed, 10, 64)
		if e != nil {
			t.Fatal(e)
		}
		trace.Config.Seed = &seed
	}
	if value.Termination.Reason != nil {
		trace.Error = *value.Termination.Reason
	}
	for _, step := range value.Steps {
		tr := s.TraceStep{Input: binaryBytes(t, step.Input), Disposition: s.Disposition{Kind: step.Disposition.Kind}, PostState: binaryBytes(t, step.PostState), Checks: binaryChecks(step.Checks), Outputs: [][]byte{}}
		if step.Disposition.Reason != nil {
			tr.Disposition.Reason = *step.Disposition.Reason
		}
		for _, output := range step.Outputs {
			tr.Outputs = append(tr.Outputs, binaryBytes(t, output))
		}
		trace.Steps = append(trace.Steps, tr)
	}
	return &trace
}
func binaryLimits(overrides map[string]uint64) s.BinaryLimits {
	limits := s.DefaultBinaryLimits()
	for name, value := range overrides {
		switch name {
		case "max_total_bytes":
			limits.MaxTotalBytes = value
		case "max_frame_bytes":
			limits.MaxFrameBytes = value
		case "max_blob_bytes":
			limits.MaxBlobBytes = value
		case "max_string_bytes":
			limits.MaxStringBytes = value
		case "max_steps":
			limits.MaxSteps = value
		case "max_checks_per_step":
			limits.MaxChecksPerStep = value
		case "max_outputs_per_step":
			limits.MaxOutputsPerStep = value
		case "max_parameters":
			limits.MaxParameters = value
		case "max_items":
			limits.MaxItems = value
		default:
			panic("unknown binary quota " + name)
		}
	}
	return limits
}
func TestBinarySharedCorpus(t *testing.T) {
	for _, fixture := range binaryCorpus(t) {
		t.Run(fixture.Case, func(t *testing.T) {
			data := binaryBytes(t, fixture.BytesHex)
			limits := binaryLimits(fixture.Limits)
			trace, e := s.ParseAuditTrace(data, limits)
			if !fixture.Valid {
				var auditError *s.AuditError
				if !errors.As(e, &auditError) || auditError.Kind != fixture.ExpectedError.Kind || fixture.ExpectedError.Detail != nil && auditError.Detail != *fixture.ExpectedError.Detail {
					t.Fatalf("error %v expected %+v", e, fixture.ExpectedError)
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			expected := binaryAudit(t, *fixture.Trace)
			if !reflect.DeepEqual(trace, expected) {
				t.Fatalf("decoded %+v expected %+v", trace, expected)
			}
			encoded, e := s.MarshalAuditTrace(expected, limits)
			if e != nil || !bytes.Equal(encoded, data) {
				t.Fatalf("wire mismatch %x expected %x: %v", encoded, data, e)
			}
			roundtrip, e := s.MarshalAuditTrace(trace, limits)
			if e != nil || !bytes.Equal(roundtrip, data) {
				t.Fatal("read/reencode mismatch", e)
			}
			for end := 0; end < len(data); end++ {
				if _, e := s.ParseAuditTrace(data[:end], limits); e == nil {
					t.Fatalf("accepted truncated prefix %d/%d", end, len(data))
				}
			}
			chunked, e := s.ReadAuditTrace(&chunkReader{reader: bytes.NewReader(data), maximum: 1}, limits)
			if e != nil || !reflect.DeepEqual(chunked, expected) {
				t.Fatal("chunked read", e)
			}
		})
	}
}

type chunkReader struct {
	reader  io.Reader
	maximum int
}

func (r *chunkReader) Read(data []byte) (int, error) {
	if len(data) > r.maximum {
		data = data[:r.maximum]
	}
	return r.reader.Read(data)
}

type failWriter struct {
	writes int
	err    error
}

func (w *failWriter) Write(data []byte) (int, error) { w.writes++; return 0, w.err }
func TestBinaryWriterPreflightAndIndependentQuotas(t *testing.T) {
	var fixture binaryFixture
	for _, f := range binaryCorpus(t) {
		if f.Case == "single-step" {
			fixture = f
			break
		}
	}
	base := binaryAudit(t, *fixture.Trace)
	cases := []struct {
		name   string
		quota  map[string]uint64
		change func(*s.AuditTrace)
	}{
		{"total", map[string]uint64{"max_total_bytes": 1}, nil}, {"frame", map[string]uint64{"max_frame_bytes": 1}, nil}, {"blob", map[string]uint64{"max_blob_bytes": 0}, nil}, {"string", map[string]uint64{"max_string_bytes": 0}, nil}, {"steps", map[string]uint64{"max_steps": 0}, nil}, {"checks", map[string]uint64{"max_checks_per_step": 0}, nil}, {"outputs", map[string]uint64{"max_outputs_per_step": 0}, nil}, {"parameters", map[string]uint64{"max_parameters": 0}, func(t *s.AuditTrace) { t.Config.Parameters = [][2]string{{"duplicate", "one"}, {"duplicate", "two"}} }}, {"items", map[string]uint64{"max_items": 0}, nil},
		{"last step invalid", nil, func(t *s.AuditTrace) { t.Steps[0].Checks = append(t.Steps[0].Checks, s.Check{Status: "bogus"}) }}, {"footer invalid", nil, func(t *s.AuditTrace) { t.Termination = "bogus" }}, {"invalid UTF8", nil, func(t *s.AuditTrace) { t.Metadata.Build = string([]byte{255}) }}, {"passed reason", nil, func(t *s.AuditTrace) { t.InitialChecks[0].Details = "hidden" }}, {"accepted reason", nil, func(t *s.AuditTrace) { t.Steps[0].Disposition.Reason = "hidden" }}, {"unrepresentable error", nil, func(t *s.AuditTrace) { t.Error = "hidden" }},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			trace, _, e := base.Portable()
			if e != nil {
				t.Fatal(e)
			}
			copy, e := s.AuditFromPortable(trace, base.Config)
			if e != nil {
				t.Fatal(e)
			}
			if test.change != nil {
				test.change(copy)
			}
			writer := &failWriter{err: errors.New("should never write")}
			if e = copy.WriteBinaryWithLimits(writer, binaryLimits(test.quota)); e == nil || writer.writes != 0 {
				t.Fatal("published invalid trace", writer.writes, e)
			}
		})
	}
	writer := &failWriter{err: errors.New("disk failed")}
	e := base.WriteBinary(writer)
	var ae *s.AuditError
	if !errors.As(e, &ae) || ae.Kind != "io" || writer.writes != 1 {
		t.Fatal(e, writer.writes)
	}
	// A raised blob/string quota cannot bypass the independent frame budget.
	copy, _ := s.AuditFromPortable(base.Trace, base.Config)
	copy.Metadata.Build = string(bytes.Repeat([]byte{'a'}, 100))
	limits := s.DefaultBinaryLimits()
	limits.MaxStringBytes = 1000
	limits.MaxFrameBytes = 64
	writer = &failWriter{err: errors.New("should never write")}
	if e = copy.WriteBinaryWithLimits(writer, limits); e == nil || writer.writes != 0 {
		t.Fatal(e, writer.writes)
	}
}
func TestBinaryLosslessPortableConversion(t *testing.T) {
	for _, fixture := range binaryCorpus(t) {
		if !fixture.Valid {
			continue
		}
		audit := binaryAudit(t, *fixture.Trace)
		portable, config, e := audit.Portable()
		if e != nil {
			t.Fatal(e)
		}
		restored, e := s.AuditFromPortable(portable, config)
		if e != nil {
			t.Fatal(e)
		}
		expected := binaryBytes(t, fixture.BytesHex)
		data, e := s.MarshalAuditTrace(restored, s.DefaultBinaryLimits())
		if e != nil || !bytes.Equal(data, expected) {
			t.Fatal(fixture.Case, e)
		}
		if config.Seed != nil {
			*config.Seed ^= 1
			if *audit.Config.Seed == *config.Seed {
				t.Fatal("seed aliases")
			}
		}
		if len(portable.InitialState) > 0 {
			portable.InitialState[0] ^= 1
			if bytes.Equal(portable.InitialState, audit.InitialState) {
				t.Fatal("checkpoint aliases")
			}
		}
	}
	_, e := s.AuditFromPortable(s.Trace{Termination: "completed", Error: "cannot encode"}, s.RunConfig{Strategy: "explicit"})
	if e == nil {
		t.Fatal("lossy error accepted")
	}
	// Structural semantics are preserved, rather than silently repaired.
	audit := s.AuditTrace{Trace: s.Trace{Termination: "property_failed", InitialChecks: []s.Check{}, Steps: []s.TraceStep{}}, Config: s.RunConfig{Strategy: "explicit"}}
	data, e := s.MarshalAuditTrace(&audit, s.DefaultBinaryLimits())
	if e != nil {
		t.Fatal(e)
	}
	parsed, e := s.ParseAuditTrace(data, s.DefaultBinaryLimits())
	if e != nil || parsed.Termination != "property_failed" {
		t.Fatal(parsed, e)
	}
}

type binaryErrorReader struct{ err error }

func (r binaryErrorReader) Read([]byte) (int, error) { return 0, r.err }
func TestBinaryReaderIOAndInclusiveLimits(t *testing.T) {
	sentinel := errors.New("read failed")
	for _, fixture := range binaryCorpus(t) {
		if !fixture.Valid {
			continue
		}
		t.Run(fixture.Case, func(t *testing.T) {
			data := binaryBytes(t, fixture.BytesHex)
			trace := binaryAudit(t, *fixture.Trace)
			for _, offset := range []int{0, 12, len(data) - 1, len(data)} {
				reader := io.MultiReader(bytes.NewReader(data[:offset]), binaryErrorReader{sentinel})
				_, e := s.ReadAuditTrace(reader, s.DefaultBinaryLimits())
				if !errors.Is(e, sentinel) {
					t.Fatal("I/O cause lost", offset, e)
				}
			}
			exact := s.BinaryLimits{MaxTotalBytes: uint64(len(data)), MaxSteps: uint64(len(trace.Steps)), MaxParameters: uint64(len(trace.Config.Parameters)), MaxItems: uint64(len(trace.Steps) + len(trace.Config.Parameters) + len(trace.InitialChecks))}
			for offset := 12; offset < len(data); {
				length := uint64(binary.LittleEndian.Uint32(data[offset+1:]))
				exact.MaxFrameBytes = max(exact.MaxFrameBytes, length)
				offset += int(length) + 9
			}
			stringSize := func(value string) { exact.MaxStringBytes = max(exact.MaxStringBytes, uint64(len(value))) }
			checkSizes := func(checks []s.Check) {
				exact.MaxChecksPerStep = max(exact.MaxChecksPerStep, uint64(len(checks)))
				for _, check := range checks {
					stringSize(check.ID)
					stringSize(check.Details)
				}
			}
			stringSize(trace.Metadata.Name)
			stringSize(trace.Metadata.Build)
			stringSize(trace.Config.Strategy)
			for _, pair := range trace.Config.Parameters {
				stringSize(pair[0])
				stringSize(pair[1])
			}
			stringSize(trace.Error)
			checkSizes(trace.InitialChecks)
			exact.MaxBlobBytes = uint64(len(trace.InitialState))
			for _, step := range trace.Steps {
				exact.MaxBlobBytes = max(exact.MaxBlobBytes, uint64(len(step.Input)), uint64(len(step.PostState)))
				exact.MaxOutputsPerStep = max(exact.MaxOutputsPerStep, uint64(len(step.Outputs)))
				exact.MaxItems += uint64(len(step.Outputs) + len(step.Checks))
				for _, output := range step.Outputs {
					exact.MaxBlobBytes = max(exact.MaxBlobBytes, uint64(len(output)))
				}
				stringSize(step.Disposition.Reason)
				checkSizes(step.Checks)
			}
			encoded, e := s.MarshalAuditTrace(trace, exact)
			if e != nil || !bytes.Equal(encoded, data) {
				t.Fatal("inclusive writer", exact, e)
			}
			parsed, e := s.ParseAuditTrace(data, exact)
			if e != nil || !reflect.DeepEqual(parsed, trace) {
				t.Fatal("inclusive reader", exact, e)
			}
		})
	}
}

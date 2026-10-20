package statelessness_test

import (
	s "github.com/Jagalite/statelessness/go"
	"strings"
	"testing"
)

type valueEncodeOnlyOutput struct{ Message string }

func TestValueEncodeOnlyOutputCodecWorksThroughRecordAndReplay(t *testing.T) {
	encodes := 0
	output, e := s.NewValueCodec[valueEncodeOnlyOutput]("application-output", func(v valueEncodeOnlyOutput, b *s.EncodeBuffer) error {
		encodes++
		return s.StringCodec().EncodeRaw(v.Message, b)
	}, nil, nil)
	if e != nil {
		t.Fatal(e)
	}
	scalar := s.Uint8Codec()
	decode := func(data []byte) (uint8, error) { return s.FromValueBytes(scalar, data, s.DefaultDecodeLimits()) }
	m := s.Model[uint8, uint8, valueEncodeOnlyOutput]{InitialState: func() (uint8, error) { return 0, nil }, CloneState: s.ValueCopy[uint8], CloneInput: s.ValueCopy[uint8], CloneOutput: s.ValueCopy[valueEncodeOnlyOutput], CheckState: func(uint8) ([]s.Check, error) { return []s.Check{s.Passed("state")}, nil }, Step: func(v, in uint8) (s.Transition[uint8, valueEncodeOnlyOutput], error) {
		return s.Accept(v+in, valueEncodeOnlyOutput{"ok"}), nil
	}, Codec: &s.Codec[uint8, uint8, valueEncodeOnlyOutput]{Metadata: func() (s.Metadata, error) { return s.Identity("value-output-only", "test"), nil }, EncodeStateInto: scalar.EncodeInto, EncodeInputInto: scalar.EncodeInto, EncodeOutputInto: output.EncodeInto, DecodeState: decode, DecodeInput: decode}}
	trace, e := s.RecordAudit(m, s.Values([]uint8{1}), s.RunConfig{Strategy: "manual"}, 2, s.DefaultBinaryLimits())
	if e != nil {
		t.Fatal(e)
	}
	report, e := s.ReplayWithObservationsBounded(m, trace, false, s.DefaultBinaryLimits(), s.ReplayObserver[uint8, uint8, valueEncodeOnlyOutput]{})
	if e != nil || report.Outcome != "exact" || encodes != 2 {
		t.Fatalf("report=%+v error=%v encodes=%d", report, e, encodes)
	}
	if _, e = s.FromValueBytes(output, []byte{}, s.DefaultDecodeLimits()); e == nil || !strings.Contains(e.Error(), "no decoder") {
		t.Fatal("missing decoder not explicit")
	}
}
func TestValueDecodeOnlyCodecNeedsNoInventedEncoder(t *testing.T) {
	codec, e := s.NewValueCodec[uint16]("decode-only-u16", nil, func(d *s.Decoder) (uint16, error) { return s.Uint16Codec().DecodeRaw(d) }, nil)
	if e != nil {
		t.Fatal(e)
	}
	data := []byte{0x34, 0x12}
	limits := s.DecodeLimits{Bytes: 2, Depth: 1, Work: 1}
	got, e := s.FromValueBytes(codec, data, limits)
	if e != nil || got != 0x1234 {
		t.Fatalf("value=%d error=%v", got, e)
	}
	d, e := s.NewDecoder(data, limits)
	if e != nil {
		t.Fatal(e)
	}
	got, e = s.ReadValue(d, codec)
	if e != nil || got != 0x1234 || d.Finish() != nil {
		t.Fatal("decode-only reader failed")
	}
	if _, e = s.EncodeValue(codec, uint16(1), 2); e == nil || !strings.Contains(e.Error(), "no encoder") {
		t.Fatal("missing encoder not explicit")
	}
	if _, e = s.NewValueCodec[uint16]("neither", nil, nil, nil); e == nil {
		t.Fatal("empty capabilities accepted")
	}
}

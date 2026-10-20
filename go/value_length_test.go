package statelessness

import (
	"errors"
	"testing"
)

func TestIndependentLengthCodecs(t *testing.T) {
	var escaped *EncodeBuffer
	encoder, err := NewLengthValueCodec("custom", func(v uint8, w uint8, out *EncodeBuffer) error {
		escaped = out
		return encodeValueLength(uint64(v), w, out)
	}, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	selected, err := encoder.WithWidth(2)
	if err != nil {
		t.Fatal(err)
	}
	bytes, err := EncodeValue(selected, uint8(7), 2)
	if err != nil || string(bytes) != string([]byte{7, 0}) {
		t.Fatalf("%x %v", bytes, err)
	}
	if escaped.Append([]byte{9}) == nil {
		t.Fatal("escaped lease remained active")
	}
	d, _ := NewDecoder([]byte{7}, DefaultDecodeLimits())
	if _, err := encoder.DecodeLengthRaw(d, 1); err == nil || d.Remaining() != 1 {
		t.Fatal("missing decode consumed input")
	}
	decoder, err := NewLengthValueCodec[uint8]("custom", nil, func(d *Decoder, w uint8) (uint8, error) { n, e := decodeValueLength(d, w); return uint8(n), e }, nil)
	if err != nil {
		t.Fatal(err)
	}
	ordinary, _ := decoder.WithWidth(1)
	limits := DefaultDecodeLimits()
	limits.Work = 2
	limits.Depth = 2
	got, err := FromValueBytes(ordinary, []byte{9}, limits)
	if err != nil || got != 9 {
		t.Fatalf("width wrapper introduced a guard: %v %v", got, err)
	}
	if _, err := EncodeValue(ordinary, 9, 10); err == nil {
		t.Fatal("missing encoder accepted")
	}
	if _, err := NewLengthValueCodec[uint8]("none", nil, nil, nil); err == nil {
		t.Fatal("empty descriptor accepted")
	}
}

func TestLengthRawFailuresAndLeases(t *testing.T) {
	sentinel := errors.New("length callback")
	codec, _ := NewLengthValueCodec[uint8]("custom", func(v uint8, w uint8, out *EncodeBuffer) error { _ = out.Append([]byte{v}); return sentinel }, nil, nil)
	var bytes []byte
	err := WithEncodeBuffer(&bytes, 2, func(out *EncodeBuffer) error {
		if !errors.Is(codec.EncodeLengthRaw(1, 1, out), sentinel) {
			t.Fatal("raw error lost")
		}
		return out.Append([]byte{2})
	})
	if err != nil || string(bytes) != string([]byte{1, 2}) {
		t.Fatalf("raw poisoned sink: %x %v", bytes, err)
	}
	bytes = nil
	err = WithEncodeBuffer(&bytes, 2, func(out *EncodeBuffer) error { _ = codec.EncodeLengthInto(1, 1, out); return nil })
	if !errors.Is(err, sentinel) {
		t.Fatalf("strong helper did not poison: %x %v", bytes, err)
	}
	raw, _ := NewLengthValueCodec[uint8]("custom", nil, func(d *Decoder, w uint8) (uint8, error) { return 0, sentinel }, nil)
	d, _ := NewDecoder(nil, DefaultDecodeLimits())
	if _, err := raw.DecodeLengthRaw(d, 1); !errors.Is(err, sentinel) {
		t.Fatal(err)
	}
	if err := d.Finish(); err != nil {
		t.Fatalf("raw validation poisoned reader: %v", err)
	}
	ordinary, _ := raw.WithWidth(1)
	if _, err := ReadValue(d, ordinary); !errors.Is(err, sentinel) {
		t.Fatal(err)
	}
	if !errors.Is(d.Finish(), sentinel) {
		t.Fatal("guard did not retain error")
	}
}

func TestBuiltinIndependentLengthAdapters(t *testing.T) {
	stringCodec, _ := StringLengthValueCodec().WithWidth(1)
	bytes, err := EncodeValue(stringCodec, "hi", 3)
	if err != nil || string(bytes) != string([]byte{2, 'h', 'i'}) {
		t.Fatalf("%x %v", bytes, err)
	}
	vector, err := VectorLengthValueCodec(Uint8Codec())
	if err != nil {
		t.Fatal(err)
	}
	selected, _ := vector.WithWidth(1)
	bytes, err = EncodeValue(selected, []uint8{3, 4}, 3)
	if err != nil || string(bytes) != string([]byte{2, 3, 4}) {
		t.Fatalf("%x %v", bytes, err)
	}
	decoded, err := FromValueBytes(selected, bytes, DefaultDecodeLimits())
	if err != nil || len(decoded) != 2 || decoded[1] != 4 {
		t.Fatalf("%v %v", decoded, err)
	}
}

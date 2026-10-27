package statelessness_test

import (
	"bytes"
	s "github.com/Jagalite/statelessness/go"
	"testing"
)

func TestValueTypedFixedArrayAdapterPreservesGuardAndBudgets(t *testing.T) {
	codec, e := s.AdaptFixedArray(3, s.Uint16Codec(), func(v [3]uint16) []uint16 { return v[:] }, func(v []uint16) ([3]uint16, error) { var out [3]uint16; copy(out[:], v); return out, nil })
	if e != nil {
		t.Fatal(e)
	}
	value := [3]uint16{1, 256, 65535}
	encoded, e := s.EncodeValue(codec, value, 6)
	if e != nil || !bytes.Equal(encoded, []byte{1, 0, 0, 1, 255, 255}) {
		t.Fatalf("bytes=%x error=%v", encoded, e)
	}
	limits := s.DecodeLimits{Bytes: 6, Allocation: 6, Elements: 3, Depth: 2, Work: 4}
	decoded, e := s.FromValueBytes(codec, encoded, limits)
	if e != nil || decoded != value {
		t.Fatalf("value=%v error=%v", decoded, e)
	}
	limits.Work--
	if _, e = s.FromValueBytes(codec, encoded, limits); e == nil {
		t.Fatal("typed adapter added/lost guard accounting")
	}
	bad, e := s.AdaptFixedArray(3, s.Uint16Codec(), func(v [3]uint16) []uint16 { return v[:2] }, func(v []uint16) ([3]uint16, error) { var out [3]uint16; copy(out[:], v); return out, nil })
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.EncodeValue(bad, value, 6); e == nil {
		t.Fatal("conversion length mismatch accepted")
	}
	if _, e = s.FromValueBytes(bad, encoded, s.DefaultDecodeLimits()); e == nil {
		t.Fatal("decoded conversion length mismatch accepted")
	}
}

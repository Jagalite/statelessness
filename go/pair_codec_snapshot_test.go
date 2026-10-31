package statelessness

import (
	"bytes"
	"strings"
	"testing"
)

func TestPairCodecCapturesPendingSlotsBeforeChildEncoder(t *testing.T) {
	var pending []PairAddress[uint8, uint8]
	mutate := false
	base := Uint8Codec()
	layout, _ := base.Layout()
	left, err := NewValueCodec("mutating-left", func(v uint8, b *EncodeBuffer) error {
		if mutate {
			pending[0] = PairRight[uint8, uint8](99)
		}
		return base.EncodeRaw(v, b)
	}, base.DecodeRaw, &layout)
	if err != nil {
		t.Fatal(err)
	}
	codec, err := PairCodec(left, base, base, base, base, base, func() ([]Metadata, error) {
		return []Metadata{Identity("l", "b"), Identity("r", "b"), Identity("w", "b")}, nil
	}, 2)
	if err != nil {
		t.Fatal(err)
	}
	pending = []PairAddress[uint8, uint8]{PairLeft[uint8, uint8](7)}
	state := PairState[uint8, uint8, uint8, uint8]{Left: 1, Right: 2, Pending: pending}
	expected, err := codec.EncodeState(state)
	if err != nil {
		t.Fatal(err)
	}
	mutate = true
	actual, err := codec.EncodeState(state)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(actual, expected) {
		t.Fatalf("pending slots changed during encoding: got %x want %x", actual, expected)
	}
}

func TestPairCodecBoundedSnapshotPreservesFirstError(t *testing.T) {
	var pending []PairAddress[Unit, Unit]
	unit := UnitCodec()
	layout, _ := unit.Layout()
	left, err := NewValueCodec("mutating-unit", func(v Unit, b *EncodeBuffer) error {
		pending[0].Side = "invalid"
		return nil
	}, unit.DecodeRaw, &layout)
	if err != nil {
		t.Fatal(err)
	}
	codec, err := PairCodec(left, unit, unit, unit, unit, unit, func() ([]Metadata, error) {
		return []Metadata{Identity("l", "b"), Identity("r", "b"), Identity("w", "b")}, nil
	}, 6)
	if err != nil {
		t.Fatal(err)
	}
	pending = make([]PairAddress[Unit, Unit], 6)
	for i := range pending {
		pending[i] = PairLeft[Unit, Unit](Unit{})
	}
	state := PairState[Unit, Unit, Unit, Unit]{Pending: pending}
	var out []byte
	err = WithEncodeBuffer(&out, 5, func(b *EncodeBuffer) error { return codec.EncodeStateInto(state, b) })
	if err == nil || !strings.Contains(err.Error(), "encoded blob exceeds byte limit") {
		t.Fatalf("got %v, want byte limit", err)
	}
}

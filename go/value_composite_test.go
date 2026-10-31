package statelessness

import (
	"bytes"
	"reflect"
	"strings"
	"testing"
)

func compositeMust[T any](c ValueCodec[T], e error) ValueCodec[T] {
	if e != nil {
		panic(e)
	}
	return c
}
func compositeLimits() DecodeLimits {
	return DecodeLimits{Bytes: 4096, Allocation: 4096, Elements: 4096, Depth: 64, Work: 4096}
}
func compositeRoundtrip[T any](t *testing.T, c ValueCodec[T], value T, want []byte) {
	t.Helper()
	wire, e := EncodeValue(c, value, 4096)
	if e != nil || !bytes.Equal(wire, want) {
		t.Fatalf("wire%x want%x: %v", wire, want, e)
	}
	got, e := FromValueBytes(c, wire, compositeLimits())
	if e != nil || !reflect.DeepEqual(got, value) {
		t.Fatalf("got%#v want%#v: %v", got, value, e)
	}
}

// A declared logical layout is not necessarily the Go heap size of T. A large
// requested collection must report allocation failure rather than misclassify
// the runtime makeslice panic as a user decoder panic.
func TestCompositeDecodeAllocationFailureIsNotCallbackPanic(t *testing.T) {
	type huge [1 << 20]byte
	layout, err := NewValueLayout(0)
	if err != nil {
		t.Fatal(err)
	}
	element := compositeMust(NewValueCodec("huge", nil, func(*Decoder) (huge, error) { return huge{}, nil }, &layout))
	limits := DecodeLimits{Bytes: 4, Allocation: 0, Elements: 1 << 32, Depth: 8, Work: 1<<32 + 1}
	vector := compositeMust(VectorOf(element))
	if _, err := FromValueBytes(vector, []byte{0xff, 0xff, 0xff, 0xff}, limits); err == nil || err.Error() != "decode allocation failed" {
		t.Fatalf("vector error = %v", err)
	}
	array := compositeMust(ArrayOf(1<<32-1, element))
	if _, err := FromValueBytes(array, nil, limits); err == nil || err.Error() != "decode allocation failed" {
		t.Fatalf("array error = %v", err)
	}
}

func TestCompositeEncodingPinsSliceSlots(t *testing.T) {
	for _, fixed := range []bool{false, true} {
		t.Run(map[bool]string{false: "vector", true: "array"}[fixed], func(t *testing.T) {
			values := []uint8{1, 2}
			element := compositeMust(NewValueCodec("mutating", func(v uint8, out *EncodeBuffer) error {
				if v == 1 {
					values[1] = 9
					values = append(values, 7)
				}
				return Uint8Codec().EncodeRaw(v, out)
			}, nil, nil))
			var codec ValueCodec[[]uint8]
			var err error
			want := []byte{2, 0, 0, 0, 1, 2}
			if fixed {
				codec, err = ArrayOf(2, element)
				want = []byte{1, 2}
			} else {
				codec, err = VectorOf(element)
			}
			if err != nil {
				t.Fatal(err)
			}
			got, err := EncodeValue(codec, values, 32)
			if err != nil || !bytes.Equal(got, want) {
				t.Fatalf("got %x want %x: %v", got, want, err)
			}
		})
	}
}
func TestValueCompositeIndependentGoldens(t *testing.T) {
	option := compositeMust(OptionOf(Uint8Codec()))
	compositeRoundtrip(t, option, Option[uint8]{Value: 9, Present: true}, []byte{1, 9})
	compositeRoundtrip(t, option, Option[uint8]{}, []byte{0})
	tuple := compositeMust(Tuple4Of(Uint16Codec(), Int32Codec(), BoolCodec(), option))
	compositeRoundtrip(t, tuple, Tuple4[uint16, int32, bool, Option[uint8]]{0x1234, -2, true, Option[uint8]{9, true}}, []byte{0x34, 0x12, 0xfe, 0xff, 0xff, 0xff, 1, 1, 9})
	array := compositeMust(ArrayOf(3, Uint16Codec()))
	compositeRoundtrip(t, array, []uint16{3, 4, 5}, []byte{3, 0, 4, 0, 5, 0})
	vector := compositeMust(VectorLengthOf(Uint16Codec(), 1))
	compositeRoundtrip(t, vector, []uint16{0x1234, 2}, []byte{2, 0x34, 0x12, 2, 0})
	oracle := compositeMust(OracleStateOf(Uint8Codec(), Uint16Codec()))
	compositeRoundtrip(t, oracle, OracleState[uint8, uint16]{Model: 1, Oracle: 0x1234}, []byte{1, 0x34, 0x12})
	one := compositeMust(Tuple1Of(Uint8Codec()))
	compositeRoundtrip(t, one, Tuple1[uint8]{7}, []byte{7})
	three := compositeMust(Tuple3Of(Uint8Codec(), Uint8Codec(), Uint8Codec()))
	compositeRoundtrip(t, three, Tuple3[uint8, uint8, uint8]{1, 2, 3}, []byte{1, 2, 3})
}
func TestValueCompositeUnknownLayoutsAndDeclaredOverrides(t *testing.T) {
	custom := compositeMust(NewValueCodec("custom-zero", func(Unit, *EncodeBuffer) error { return nil }, func(*Decoder) (Unit, error) { return Unit{}, nil }, nil))
	tuple := compositeMust(Tuple2Of(custom, custom))
	option := compositeMust(OptionOf(tuple))
	if _, ok := tuple.Layout(); ok {
		t.Fatal("invented compound layout")
	}
	if _, ok := option.Layout(); ok {
		t.Fatal("invented option layout")
	}
	compositeRoundtrip(t, option, Option[Tuple2[Unit, Unit]]{Value: Tuple2[Unit, Unit]{}, Present: true}, []byte{1})
	vector := compositeMust(VectorOf(tuple))
	if _, e := EncodeValue(vector, []Tuple2[Unit, Unit]{{}}, 10); e != nil {
		t.Fatal(e)
	}
	if _, e := FromValueBytes(vector, []byte{1, 0, 0, 0}, compositeLimits()); e == nil || !strings.Contains(e.Error(), "layout") {
		t.Fatal("unknown collection layout accepted", e)
	}
	layout, e := NewValueLayout(0)
	if e != nil {
		t.Fatal(e)
	}
	declared, e := tuple.WithLayout(layout)
	if e != nil {
		t.Fatal(e)
	}
	vector = compositeMust(VectorOf(declared))
	compositeRoundtrip(t, vector, []Tuple2[Unit, Unit]{{}}, []byte{1, 0, 0, 0})
}
func TestValueCompositeCollectionAdmission(t *testing.T) {
	array := compositeMust(ArrayOf(3, Uint16Codec()))
	if _, e := EncodeValue(array, []uint16{1, 2}, 4096); e == nil {
		t.Fatal("wrong fixed count accepted")
	}
	vector := compositeMust(VectorOf(array))
	wire := []byte{2, 0, 0, 0, 1, 0, 2, 0, 3, 0, 4, 0, 5, 0, 6, 0}
	limits := compositeLimits()
	limits.Allocation = 24
	limits.Elements = 8
	limits.Work = 10
	if _, e := FromValueBytes(vector, wire, limits); e != nil {
		t.Fatal(e)
	}
	limits.Allocation--
	if _, e := FromValueBytes(vector, wire, limits); e == nil || !strings.Contains(e.Error(), "allocation") {
		t.Fatal(e)
	}
	unitVector := compositeMust(VectorOf(UnitCodec()))
	limits = compositeLimits()
	limits.Allocation = 0
	limits.Work = 5
	limits.Elements = 3
	compositeRoundtrip(t, unitVector, []Unit{{}, {}, {}}, []byte{3, 0, 0, 0})
	if _, e := FromValueBytes(unitVector, []byte{3, 0, 0, 0}, limits); e != nil {
		t.Fatal(e)
	}
	limits.Work = 4
	if _, e := FromValueBytes(unitVector, []byte{3, 0, 0, 0}, limits); e == nil || !strings.Contains(e.Error(), "work") {
		t.Fatal(e)
	}
}
func TestValueCompositeStrictOptionsAndLengthWidths(t *testing.T) {
	option := compositeMust(OptionOf(Uint8Codec()))
	for _, wire := range [][]byte{{2}, {1}, {0, 0}} {
		if _, e := FromValueBytes(option, wire, compositeLimits()); e == nil {
			t.Fatalf("accepted%x", wire)
		}
	}
	for _, width := range []uint8{0, 3, 255} {
		codec, e := VectorLengthOf(Uint8Codec(), width)
		if e != nil {
			t.Fatal("descriptor must defer invalid width", e)
		}
		if _, e := EncodeValue(codec, []uint8{}, 10); e == nil || !strings.Contains(e.Error(), "unsupported length width") {
			t.Fatal(e)
		}
		if _, e := FromValueBytes(codec, []byte{}, compositeLimits()); e == nil || !strings.Contains(e.Error(), "unsupported length width") {
			t.Fatal(e)
		}
	}
	vector := compositeMust(VectorLengthOf(UnitCodec(), 1))
	if _, e := EncodeValue(vector, make([]Unit, 256), 4); e == nil || !strings.Contains(e.Error(), "length overflow") {
		t.Fatal(e)
	}
}
func TestValueCompositeDecodedStorageOwnership(t *testing.T) {
	codec := compositeMust(VectorOf(Uint8Codec()))
	wire := []byte{2, 0, 0, 0, 1, 2}
	got, e := FromValueBytes(codec, wire, compositeLimits())
	if e != nil {
		t.Fatal(e)
	}
	wire[4] = 99
	if got[0] != 1 {
		t.Fatal("decoded slice aliases bytes")
	}
	got[1] = 88
	if wire[5] != 2 {
		t.Fatal("bytes alias decoded slice")
	}
}

func TestValueCompositeDiagnosticSchemaCannotForgeLayout(t *testing.T) {
	spoof := compositeMust(NewValueCodec("u64", func(Unit, *EncodeBuffer) error { return nil }, func(*Decoder) (Unit, error) { return Unit{}, nil }, nil))
	option := compositeMust(OptionOf(spoof))
	if _, ok := option.Layout(); ok {
		t.Fatal("diagnostic spelling forged Option layout")
	}
	tuple := compositeMust(Tuple2Of(Uint8Codec(), spoof))
	if _, ok := tuple.Layout(); ok {
		t.Fatal("diagnostic spelling forged tuple layout")
	}
	layout, e := NewValueLayout(999)
	if e != nil {
		t.Fatal(e)
	}
	changed, e := Uint64Codec().WithLayout(layout)
	if e != nil {
		t.Fatal(e)
	}
	changedOption := compositeMust(OptionOf(changed))
	if _, ok := changedOption.Layout(); ok {
		t.Fatal("explicit layout override retained trusted builtin identity")
	}
}

func compositeMissingDecode[T any](t *testing.T, c ValueCodec[T]) {
	t.Helper()
	d, e := NewDecoder([]byte{0x7f}, compositeLimits())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = c.DecodeRaw(d); e == nil || !strings.Contains(e.Error(), "no decoder") {
		t.Fatal("composite invented decoder", e)
	}
	next, e := d.Take(1)
	if e != nil || !bytes.Equal(next, []byte{0x7f}) {
		t.Fatal("missing operation consumed input", next, e)
	}
}
func compositeMissingEncode[T any](t *testing.T, c ValueCodec[T], v T) {
	t.Helper()
	var out []byte
	e := WithEncodeBuffer(&out, 100, func(b *EncodeBuffer) error { return c.EncodeRaw(v, b) })
	if e == nil || !strings.Contains(e.Error(), "no encoder") || len(out) != 0 {
		t.Fatal("composite invented encoder", out, e)
	}
}
func TestValueCompositePropagatesOperationCapabilities(t *testing.T) {
	enc := compositeMust(NewValueCodec("encode-only", func(Unit, *EncodeBuffer) error { return nil }, nil, nil))
	dec := compositeMust(NewValueCodec("decode-only", nil, func(*Decoder) (Unit, error) { return Unit{}, nil }, nil))
	// Even absent/empty shapes require the element trait for the entire codec.
	option := compositeMust(OptionOf(enc))
	compositeMissingDecode(t, option)
	if _, e := EncodeValue(option, Option[Unit]{}, 10); e != nil {
		t.Fatal(e)
	}
	vector := compositeMust(VectorOf(dec))
	compositeMissingEncode(t, vector, []Unit{})
	compositeMissingDecode(t, compositeMust(VectorOf(enc)))
	compositeMissingDecode(t, compositeMust(ArrayOf(0, enc)))
	compositeMissingDecode(t, compositeMust(Tuple1Of(enc)))
	compositeMissingDecode(t, compositeMust(Tuple2Of(enc, UnitCodec())))
	compositeMissingDecode(t, compositeMust(Tuple3Of(UnitCodec(), enc, UnitCodec())))
	compositeMissingDecode(t, compositeMust(Tuple4Of(UnitCodec(), UnitCodec(), UnitCodec(), enc)))
	compositeMissingDecode(t, compositeMust(OracleStateOf(UnitCodec(), enc)))
	compositeMissingEncode(t, compositeMust(OptionOf(dec)), Option[Unit]{})
	compositeMissingEncode(t, compositeMust(ArrayOf(0, dec)), []Unit{})
	compositeMissingEncode(t, compositeMust(Tuple1Of(dec)), Tuple1[Unit]{})
	compositeMissingEncode(t, compositeMust(Tuple2Of(UnitCodec(), dec)), Tuple2[Unit, Unit]{})
	compositeMissingEncode(t, compositeMust(Tuple3Of(UnitCodec(), dec, UnitCodec())), Tuple3[Unit, Unit, Unit]{})
	compositeMissingEncode(t, compositeMust(Tuple4Of(dec, UnitCodec(), UnitCodec(), UnitCodec())), Tuple4[Unit, Unit, Unit, Unit]{})
	compositeMissingEncode(t, compositeMust(OracleStateOf(dec, UnitCodec())), OracleState[Unit, Unit]{})
	if _, e := Tuple2Of(enc, dec); e == nil {
		t.Fatal("codec with neither complete operation accepted")
	}
	d, e := NewDecoder([]byte{0x7f}, compositeLimits())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = DecodeVectorLength(d, enc, 4); e == nil || !strings.Contains(e.Error(), "no decoder") {
		t.Fatal(e)
	}
	next, e := d.Take(1)
	if e != nil || !bytes.Equal(next, []byte{0x7f}) {
		t.Fatal("direct missing decoder consumed input", next, e)
	}
	var out []byte
	e = WithEncodeBuffer(&out, 10, func(b *EncodeBuffer) error { return EncodeVectorLength([]Unit{}, dec, 4, b) })
	if e == nil || len(out) != 0 {
		t.Fatal("direct missing encoder wrote length", out, e)
	}
}

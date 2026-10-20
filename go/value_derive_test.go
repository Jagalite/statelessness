package statelessness

import (
	"bytes"
	"errors"
	"testing"
)

type derivePair struct {
	A uint8
	B uint16
}

func pairDeclaration(t *testing.T, constructed *int) ValueCodec[derivePair] {
	t.Helper()
	a, _ := OrdinaryField("a", Uint8Codec())
	b, _ := OrdinaryField("b", Uint16Codec())
	c, e := OrderedRecord("Pair", PrependRecordField(a, PrependRecordField(b, NoRecordFields())), func(v derivePair) (FieldProduct[uint8, FieldProduct[uint16, Unit]], error) {
		return FieldProduct[uint8, FieldProduct[uint16, Unit]]{v.A, FieldProduct[uint16, Unit]{v.B, Unit{}}}, nil
	}, func(p FieldProduct[uint8, FieldProduct[uint16, Unit]]) (derivePair, error) {
		*constructed++
		return derivePair{p.Head, p.Tail.Head}, nil
	}, RecordOptions{})
	if e != nil {
		t.Fatal(e)
	}
	return c
}
func TestDerivedOrderedRecords(t *testing.T) {
	n := 0
	c := pairDeclaration(t, &n)
	want := []byte{7, 0x34, 0x12}
	got, e := EncodeValue(c, derivePair{7, 0x1234}, 100)
	if e != nil || !bytes.Equal(got, want) {
		t.Fatal(got, e)
	}
	l := DefaultDecodeLimits()
	l.Work = 3
	l.Depth = 2
	v, e := FromValueBytes(c, want, l)
	if e != nil || v != (derivePair{7, 0x1234}) || n != 1 {
		t.Fatal(v, e, n)
	}
	for k := 0; k < len(want); k++ {
		before := n
		if _, e := FromValueBytes(c, want[:k], DefaultDecodeLimits()); e == nil || n != before {
			t.Fatal(k, e, n)
		}
	}
	if _, e := FromValueBytes(c, append(want, 0), DefaultDecodeLimits()); e == nil {
		t.Fatal("trailing accepted")
	}
	l.Work = 2
	if _, e := FromValueBytes(c, want, l); e == nil {
		t.Fatal("work accepted")
	}
	l.Work = 3
	l.Depth = 1
	if _, e := FromValueBytes(c, want, l); e == nil {
		t.Fatal("depth accepted")
	}
	if _, ok := c.Layout(); ok {
		t.Fatal("invented layout")
	}
}
func TestDerivedDirectionsAndRawAdapters(t *testing.T) {
	enc, _ := NewValueCodec("enc", func(v uint8, b *EncodeBuffer) error { return Uint8Codec().EncodeRaw(v, b) }, nil, nil)
	dec, _ := NewValueCodec[uint8]("dec", nil, Uint8Codec().DecodeRaw, nil)
	a, _ := OrdinaryField("", enc)
	b, _ := OrdinaryField("", dec)
	fields := PrependRecordField(a, PrependRecordField(b, NoRecordFields()))
	project := func(v Unit) (FieldProduct[uint8, FieldProduct[uint8, Unit]], error) {
		t.Fatal("project called")
		return FieldProduct[uint8, FieldProduct[uint8, Unit]]{}, nil
	}
	construct := func(FieldProduct[uint8, FieldProduct[uint8, Unit]]) (Unit, error) {
		t.Fatal("construct called")
		return Unit{}, nil
	}
	c, e := OrderedRecord("neither", fields, project, construct, RecordOptions{})
	if e != nil {
		t.Fatal(e)
	}
	if _, e = EncodeValue(c, Unit{}, 100); e == nil || e.Error() != "value codec has no encoder" {
		t.Fatal(e)
	}
	if _, e = FromValueBytes(c, nil, DefaultDecodeLimits()); e == nil || e.Error() != "value codec has no decoder" {
		t.Fatal(e)
	}
	req := ValueDirections{Encode: true}
	if _, e = OrderedRecord("bad", fields, project, construct, RecordOptions{Require: &req}); e == nil {
		t.Fatal("missing eager validation")
	}
	lf, _ := LengthField("text", StringLengthValueCodec(), 1)
	lc, _ := OrderedRecord("text", PrependRecordField(lf, NoRecordFields()), func(v string) (FieldProduct[string, Unit], error) { return FieldProduct[string, Unit]{v, Unit{}}, nil }, func(p FieldProduct[string, Unit]) (string, error) { return p.Head, nil }, RecordOptions{})
	l := DefaultDecodeLimits()
	l.Work = 5
	l.Depth = 2
	if v, e := FromValueBytes(lc, []byte{3, 'a', 'b', 'c'}, l); e != nil || v != "abc" {
		t.Fatal(v, e)
	}
	marker := errors.New("callback")
	var retained *EncodeBuffer
	af, _ := AdapterField("adapter", func(v Unit, b *EncodeBuffer) error { retained = b; return marker }, func(d *Decoder) (Unit, error) {
		if d.Depth() != 1 {
			t.Fatal(d.Depth())
		}
		return Unit{}, nil
	})
	ac, _ := OrderedRecord("adapter", PrependRecordField(af, NoRecordFields()), func(v Unit) (FieldProduct[Unit, Unit], error) { return FieldProduct[Unit, Unit]{}, nil }, func(FieldProduct[Unit, Unit]) (Unit, error) { return Unit{}, nil }, RecordOptions{})
	outer, _ := NewValueCodec("outer", func(v Unit, b *EncodeBuffer) error {
		if e := ac.EncodeRaw(v, b); e != marker {
			t.Fatal(e)
		}
		return Uint8Codec().EncodeRaw(9, b)
	}, nil, nil)
	if got, e := EncodeValue(outer, Unit{}, 1); e != nil || !bytes.Equal(got, []byte{9}) {
		t.Fatal(got, e)
	}
	if e := Uint8Codec().EncodeRaw(1, retained); e == nil {
		t.Fatal("retained lease live")
	}
	if _, e := FromValueBytes(ac, nil, DefaultDecodeLimits()); e != nil {
		t.Fatal(e)
	}
}
func TestDerivedTaggedEnum(t *testing.T) {
	n := 0
	payload := pairDeclaration(t, &n)
	a, _ := TaggedCase(257, "Pair", payload, func(v derivePair) (derivePair, error) { return v, nil }, func(v derivePair) (derivePair, error) { return v, nil })
	opts := DefaultEnumOptions()
	opts.TagWidth = 2
	c, e := TaggedEnum("E", func(derivePair) (uint32, error) { return 257, nil }, opts, a)
	if e != nil {
		t.Fatal(e)
	}
	want := []byte{1, 1, 7, 0x34, 0x12}
	got, e := EncodeValue(c, derivePair{7, 0x1234}, 100)
	if e != nil || !bytes.Equal(got, want) {
		t.Fatal(got, e)
	}
	l := DefaultDecodeLimits()
	l.Work = 4
	l.Depth = 2
	if _, e := FromValueBytes(c, want, l); e != nil {
		t.Fatal(e)
	}
	label, ok, e := c.VariantFor(derivePair{})
	if e != nil || !ok || label != "Pair" {
		t.Fatal(label, ok, e)
	}
	before := n
	if _, e := FromValueBytes(c, []byte{2, 1}, l); e == nil || e.Error() != "unknown enum tag" || n != before {
		t.Fatal(e, n)
	}
	if _, e := TaggedEnum("dup", func(derivePair) (uint32, error) { return 257, nil }, opts, a, a); e == nil {
		t.Fatal("duplicate accepted")
	}
	opts.TagWidth = 1
	if _, e := TaggedEnum("overflow", func(derivePair) (uint32, error) { return 257, nil }, opts, a); e == nil {
		t.Fatal("overflow accepted")
	}
}
func TestBoundModelCodecGatesAllHooks(t *testing.T) {
	calls, limits, metadata := 0, 0, 0
	version := uint32(2)
	value, _ := NewValueCodec("value", func(v uint8, b *EncodeBuffer) error { calls++; return Uint8Codec().EncodeRaw(v, b) }, func(d *Decoder) (uint8, error) { calls++; return Uint8Codec().DecodeRaw(d) }, nil)
	output, _ := NewValueCodec("output", value.encode, nil, nil)
	opts := DefaultModelCodecOptions()
	opts.DecodeLimits = func() (DecodeLimits, error) { limits++; return DefaultDecodeLimits(), nil }
	c, e := BindModelCodec(func() (Metadata, error) { metadata++; return Metadata{CodecVersion: version}, nil }, value, value, output, opts)
	if e != nil {
		t.Fatal(e)
	}
	hooks := []func() error{func() error { _, e := c.EncodeState(1); return e }, func() error { _, e := c.EncodeInput(1); return e }, func() error { _, e := c.EncodeOutput(1); return e }, func() error { return c.EncodeStateInto(1, nil) }, func() error { return c.EncodeInputInto(1, nil) }, func() error { return c.EncodeOutputInto(1, nil) }, func() error { _, e := c.DecodeState(nil); return e }, func() error { _, e := c.DecodeInput(nil); return e }}
	for _, h := range hooks {
		if e := h(); e == nil || e.Error() != "ModelCodec derive version does not match Model metadata" {
			t.Fatal(e)
		}
	}
	if calls != 0 || limits != 0 || metadata != 8 {
		t.Fatal(calls, limits, metadata)
	}
	version = 1
	for i := 0; i < 2; i++ {
		if _, e := c.DecodeState([]byte{4}); e != nil {
			t.Fatal(e)
		}
	}
	if limits != 2 {
		t.Fatal(limits)
	}
	if _, e := c.EncodeOutput(1); e != nil {
		t.Fatal(e)
	}
	if limits != 2 {
		t.Fatal(limits)
	}
}

func TestBoundModelCodecEngineComposition(t *testing.T) {
	output, _ := NewValueCodec("Output", Uint8Codec().EncodeRaw, nil, nil)
	codec, e := BindModelCodec(func() (Metadata, error) { return Identity("derive-bound", "test"), nil }, Uint8Codec(), Uint8Codec(), output, DefaultModelCodecOptions())
	if e != nil {
		t.Fatal(e)
	}
	m := Model[uint8, uint8, uint8]{InitialState: func() (uint8, error) { return 0, nil }, Step: func(s, i uint8) (Transition[uint8, uint8], error) { return Accept(s+i, s+i), nil }, CheckState: func(uint8) ([]Check, error) { return []Check{Passed("ok")}, nil }, CloneState: ValueCopy[uint8], CloneInput: ValueCopy[uint8], CloneOutput: ValueCopy[uint8], EqualStates: func(a, b uint8) bool { return a == b }, Codec: codec}
	trace, e := Record(m, Values([]uint8{1, 2}), 2)
	if e != nil {
		t.Fatal(e)
	}
	if _, e := Replay(m, trace, false); e != nil {
		t.Fatal(e)
	}
	initial, e := CheckInitial(m, uint8(0))
	if e != nil {
		t.Fatal(e)
	}
	recorder, e := NewRecorderWithCheckedInitialAt(initial, 0, RunConfig{Strategy: "manual"}, DefaultRecorderOptions())
	if e != nil {
		t.Fatal(e)
	}
	evidence, e := NewEvidenceRecorder(initial, 0, RunConfig{Strategy: "manual"}, DefaultRecorderOptions(), Provenance{}, DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	turn, e := CheckTurn(m, uint8(0), uint8(1), Accept(uint8(1), uint8(1)), 1, FullChecks())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = ObserveCheckedRecorder(recorder, turn); e != nil {
		t.Fatal(e)
	}
	if e = ObserveEvidenceRecorder(evidence, turn, CausalContext{}); e != nil {
		t.Fatal(e)
	}
	if _, e = ReplayTraceV2(m, evidence.Snapshot(), false, DefaultEvidenceLimits()); e != nil {
		t.Fatal(e)
	}
}

func TestDerivedRawWriteFailureAndCompleteCapabilities(t *testing.T) {
	marker := errors.New("raw callback")
	f, _ := AdapterField("raw", func(_ Unit, b *EncodeBuffer) error { _ = b.Append([]byte{1, 2}); return marker }, func(*Decoder) (Unit, error) { return Unit{}, nil })
	req := ValueDirections{Encode: true}
	c, e := OrderedRecord("raw", PrependRecordField(f, NoRecordFields()), func(Unit) (FieldProduct[Unit, Unit], error) { return FieldProduct[Unit, Unit]{}, nil }, func(FieldProduct[Unit, Unit]) (Unit, error) { return Unit{}, nil }, RecordOptions{Require: &req})
	if e != nil {
		t.Fatal(e)
	}
	if c.decode == nil {
		t.Fatal("require filtered available decoder")
	}
	var dest []byte
	e = WithEncodeBuffer(&dest, 1, func(b *EncodeBuffer) error {
		if e := c.EncodeRaw(Unit{}, b); e != marker {
			t.Fatal("raw callback priority", e)
		}
		if e := b.Finish(); e == nil || e.Error() != "encoded blob exceeds byte limit" {
			t.Fatal("sticky write", e)
		}
		return nil
	})
	if e == nil {
		t.Fatal("lost actual write failure")
	}
	eo, _ := NewValueCodec("eo", UnitCodec().EncodeRaw, nil, nil)
	unit, _ := TaggedCase(0, "unit", UnitCodec(), func(Unit) (Unit, error) { return Unit{}, nil }, func(Unit) (Unit, error) { return Unit{}, nil })
	missing, _ := TaggedCase(1, "missing", eo, func(Unit) (Unit, error) { return Unit{}, nil }, func(Unit) (Unit, error) { return Unit{}, nil })
	enum, e := TaggedEnum("all", func(Unit) (uint32, error) { return 0, nil }, DefaultEnumOptions(), unit, missing)
	if e != nil {
		t.Fatal(e)
	}
	if _, e := FromValueBytes(enum, []byte{0}, DefaultDecodeLimits()); e == nil || e.Error() != "value codec has no decoder" {
		t.Fatal("unselected case direction", e)
	}
	if _, e := TaggedEnum[Unit]("zero", nil, EnumOptions{}); e == nil {
		t.Fatal("zero enum width accepted")
	}
	if _, e := BindModelCodec(func() (Metadata, error) { return Identity("x", "x"), nil }, UnitCodec(), UnitCodec(), UnitCodec(), ModelCodecOptions{}); e == nil {
		t.Fatal("zero version accepted")
	}
}

func TestDerivedUnitConstructorFailureAndConfigurationCopies(t *testing.T) {
	calls := 0
	layout, _ := NewValueLayout(4)
	req := ValueDirections{Encode: true}
	unit, e := OrderedRecord("unit", NoRecordFields(), func(Unit) (Unit, error) { return Unit{}, nil }, func(Unit) (Unit, error) { calls++; return Unit{}, nil }, RecordOptions{Require: &req, Layout: &layout})
	if e != nil {
		t.Fatal(e)
	}
	req.Decode = true
	layout, _ = NewValueLayout(999)
	if l, ok := unit.Layout(); !ok || l.Size() != 4 {
		t.Fatal(l, ok)
	}
	if b, e := EncodeValue(unit, Unit{}, 0); e != nil || len(b) != 0 {
		t.Fatal(b, e)
	}
	if _, e := FromValueBytes(unit, nil, DefaultDecodeLimits()); e != nil || calls != 1 {
		t.Fatal(e, calls)
	}
	marker := errors.New("construction")
	bad, _ := OrderedRecord("bad", NoRecordFields(), func(int) (Unit, error) { return Unit{}, nil }, func(Unit) (int, error) { return 99, marker }, RecordOptions{})
	if v, e := FromValueBytes(bad, nil, DefaultDecodeLimits()); v != 0 || e != marker {
		t.Fatal(v, e)
	}
	opts := DefaultEnumOptions()
	opts.TagWidth = 4
	ec, _ := TaggedCase(uint32(0xffffffff), "maximum", UnitCodec(), func(Unit) (Unit, error) { return Unit{}, nil }, func(Unit) (Unit, error) { return Unit{}, nil })
	en, e := TaggedEnum("maximum", func(Unit) (uint32, error) { return 0xffffffff, nil }, opts, ec)
	if e != nil {
		t.Fatal(e)
	}
	if b, e := EncodeValue(en, Unit{}, 4); e != nil || !bytes.Equal(b, []byte{255, 255, 255, 255}) {
		t.Fatal(b, e)
	}
}

func TestDerivedEnumRawUnknownAndGuardedSticky(t *testing.T) {
	cc, _ := TaggedCase(3, "three", UnitCodec(), func(Unit) (Unit, error) { return Unit{}, nil }, func(Unit) (Unit, error) { return Unit{}, nil })
	c, _ := TaggedEnum("E", func(Unit) (uint32, error) { return 3, nil }, DefaultEnumOptions(), cc)
	d, _ := NewDecoder([]byte{8}, DefaultDecodeLimits())
	if _, e := c.DecodeRaw(d); e == nil || e.Error() != "unknown enum tag" {
		t.Fatal(e)
	}
	if e := d.Finish(); e != nil {
		t.Fatal("raw unknown became sticky", e)
	}
	d, _ = NewDecoder([]byte{8}, DefaultDecodeLimits())
	if _, e := ReadValue(d, c); e == nil {
		t.Fatal("unknown accepted")
	}
	if e := d.Finish(); e == nil || e.Error() != "unknown enum tag" {
		t.Fatal(e)
	}
	d, _ = NewDecoder(nil, DefaultDecodeLimits())
	if _, e := c.DecodeRaw(d); e == nil || e.Error() != "truncated value" {
		t.Fatal(e)
	}
	if e := d.Finish(); e == nil || e.Error() != "truncated value" {
		t.Fatal(e)
	}
}

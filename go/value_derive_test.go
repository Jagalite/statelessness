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
	var gateSink *EncodeBuffer
	hooks := []func() error{func() error { _, e := c.EncodeState(1); return e }, func() error { _, e := c.EncodeInput(1); return e }, func() error { _, e := c.EncodeOutput(1); return e }, func() error { return c.EncodeStateInto(1, gateSink) }, func() error { return c.EncodeInputInto(1, gateSink) }, func() error { return c.EncodeOutputInto(1, gateSink) }, func() error { _, e := c.DecodeState(nil); return e }, func() error { _, e := c.DecodeInput(nil); return e }}
	var prefix []byte
	if e := WithEncodeBuffer(&prefix, 4, func(s *EncodeBuffer) error {
		if e := s.Append([]byte{'p'}); e != nil {
			return e
		}
		gateSink = s
		for _, h := range hooks {
			if e := h(); e == nil || e.Error() != "ModelCodec derive version does not match Model metadata" {
				t.Fatal(e)
			}
			if !bytes.Equal(prefix, []byte{'p'}) {
				t.Fatal("version mismatch changed sink", prefix)
			}
		}
		return nil
	}); e != nil {
		t.Fatal(e)
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
	requiredDecode := DefaultEnumOptions()
	requiredDecode.Require = &ValueDirections{Decode: true}
	if _, e := TaggedEnum("all-required", func(Unit) (uint32, error) { return 0, nil }, requiredDecode, unit, missing); e == nil {
		t.Fatal("unselected case must reject required decoder at builder")
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

// Literal draft wire witnesses. The strings and budgets are independently specified,
// never replaced with output from this implementation.
func TestDerivedLiteralLengthRoutesAndAdmission(t *testing.T) {
	for _, tc := range []struct {
		name, value      string
		width            uint8
		wire             []byte
		work, allocation uint64
	}{
		{"utf8", "é", 2, []byte{2, 0, 0xc3, 0xa9}, 4, 2},
		{"bom", "\ufeff", 4, []byte{3, 0, 0, 0, 0xef, 0xbb, 0xbf}, 5, 3},
		{"empty", "", 1, []byte{0}, 2, 0},
		{"abc", "abc", 1, []byte{3, 'a', 'b', 'c'}, 5, 3},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f, e := LengthField("text", StringLengthValueCodec(), tc.width)
			if e != nil {
				t.Fatal(e)
			}
			calls := 0
			c, e := OrderedRecord("Text", PrependRecordField(f, NoRecordFields()), func(v string) (FieldProduct[string, Unit], error) { return FieldProduct[string, Unit]{v, Unit{}}, nil }, func(p FieldProduct[string, Unit]) (string, error) { calls++; return p.Head, nil }, RecordOptions{})
			if e != nil {
				t.Fatal(e)
			}
			if b, e := EncodeValue(c, tc.value, 100); e != nil || !bytes.Equal(b, tc.wire) {
				t.Fatal(b, e)
			}
			l := DecodeLimits{Bytes: uint64(len(tc.wire)), Allocation: tc.allocation, Elements: tc.allocation, Work: tc.work, Depth: 2}
			if v, e := FromValueBytes(c, tc.wire, l); e != nil || v != tc.value || calls != 1 {
				t.Fatal(v, e, calls)
			}
			if tc.allocation > 0 {
				for _, kind := range []string{"bytes", "allocation", "elements", "work", "depth"} {
					under := l
					switch kind {
					case "bytes":
						under.Bytes--
					case "allocation":
						under.Allocation--
					case "elements":
						under.Elements--
					case "work":
						under.Work--
					case "depth":
						under.Depth--
					}
					before := calls
					if _, e := FromValueBytes(c, tc.wire, under); e == nil || calls != before {
						t.Fatal(kind, e, calls)
					}
				}
			}
			if tc.width == 1 {
				for _, bad := range [][]byte{{1, 255}, {3, 'a'}} {
					before := calls
					if _, e := FromValueBytes(c, bad, DefaultDecodeLimits()); e == nil || calls != before {
						t.Fatal(bad, e, calls)
					}
				}
				var dest []byte
				e = WithEncodeBuffer(&dest, 1000, func(b *EncodeBuffer) error {
					if e := b.Append([]byte{'p'}); e != nil {
						return e
					}
					e := c.EncodeRaw(string(bytes.Repeat([]byte{'a'}, 256)), b)
					if e == nil || e.Error() != "length overflow" {
						t.Fatal(e)
					}
					return b.Finish()
				})
				if e != nil || !bytes.Equal(dest, []byte{'p'}) {
					t.Fatal(dest, e)
				}
			}
		})
	}
	lv, _ := VectorLengthValueCodec(Uint16Codec())
	f, _ := LengthField("words", lv, 1)
	c, _ := OrderedRecord("Words", PrependRecordField(f, NoRecordFields()), func(v []uint16) (FieldProduct[[]uint16, Unit], error) {
		return FieldProduct[[]uint16, Unit]{v, Unit{}}, nil
	}, func(p FieldProduct[[]uint16, Unit]) ([]uint16, error) { return p.Head, nil }, RecordOptions{})
	for _, tc := range []struct {
		v       []uint16
		b       []byte
		w, a, e uint64
	}{{[]uint16{258, 65535}, []byte{2, 2, 1, 255, 255}, 4, 4, 2}, {[]uint16{}, []byte{0}, 2, 0, 0}} {
		if b, e := EncodeValue(c, tc.v, 100); e != nil || !bytes.Equal(b, tc.b) {
			t.Fatal(b, e)
		}
		v, e := FromValueBytes(c, tc.b, DecodeLimits{Bytes: uint64(len(tc.b)), Work: tc.w, Depth: 2, Allocation: tc.a, Elements: tc.e})
		if e != nil || len(v) != len(tc.v) {
			t.Fatal(v, e)
		}
		for i := range v {
			if v[i] != tc.v[i] {
				t.Fatal(v)
			}
		}
	}
	calls := 0
	f2, _ := AdapterField("field", func(Unit, *EncodeBuffer) error { return nil }, func(*Decoder) (Unit, error) { calls++; return Unit{}, nil })
	a, _ := OrderedRecord("admission", PrependRecordField(f2, NoRecordFields()), func(Unit) (FieldProduct[Unit, Unit], error) { return FieldProduct[Unit, Unit]{}, nil }, func(FieldProduct[Unit, Unit]) (Unit, error) { calls++; return Unit{}, nil }, RecordOptions{})
	l := DefaultDecodeLimits()
	l.Bytes = 3
	if _, e := FromValueBytes(a, []byte{1, 2, 3, 4}, l); e == nil || calls != 0 {
		t.Fatal(e, calls)
	}
}

func TestDerivedRawBudgetsCollectionAndReaderScripts(t *testing.T) {
	n := 0
	p := pairDeclaration(t, &n)
	d, _ := NewDecoder([]byte{7, 0x34, 0x12}, DecodeLimits{Bytes: 3, Work: 2, Depth: 1})
	if v, e := p.DecodeRaw(d); e != nil || v != (derivePair{7, 0x1234}) {
		t.Fatal(v, e)
	}
	if e := d.Finish(); e != nil {
		t.Fatal(e)
	}
	u, _ := OrderedRecord("Unit", NoRecordFields(), func(Unit) (Unit, error) { return Unit{}, nil }, func(Unit) (Unit, error) { return Unit{}, nil }, RecordOptions{})
	d, _ = NewDecoder(nil, DecodeLimits{})
	if _, e := u.DecodeRaw(d); e != nil {
		t.Fatal(e)
	}
	if e := d.Finish(); e != nil {
		t.Fatal(e)
	}
	layout, _ := NewValueLayout(4)
	p, _ = p.WithLayout(layout)
	v, _ := VectorOf(p)
	wire := []byte{2, 0, 0, 0, 7, 0x34, 0x12, 1, 2, 1}
	l := DecodeLimits{Bytes: 10, Allocation: 8, Elements: 2, Depth: 3, Work: 8}
	if got, e := FromValueBytes(v, wire, l); e != nil || len(got) != 2 {
		t.Fatal(got, e)
	}
	l.Allocation = 7
	before := n
	if _, e := FromValueBytes(v, wire, l); e == nil || n != before {
		t.Fatal(e, n)
	}
	cc, _ := TaggedCase(7, "Idle", UnitCodec(), func(Unit) (Unit, error) { return Unit{}, nil }, func(Unit) (Unit, error) { return Unit{}, nil })
	c, _ := TaggedEnum("E", func(Unit) (uint32, error) { return 7, nil }, DefaultEnumOptions(), cc)
	d, _ = NewDecoder([]byte{8, 9}, DefaultDecodeLimits())
	if _, e := c.DecodeRaw(d); e == nil || e.Error() != "unknown enum tag" {
		t.Fatal(e)
	}
	if v, e := ReadValue(d, Uint8Codec()); e != nil || v != 9 {
		t.Fatal(v, e)
	}
	if e := d.Finish(); e != nil {
		t.Fatal(e)
	}
	d, _ = NewDecoder([]byte{8, 9}, DefaultDecodeLimits())
	if _, e := ReadValue(d, c); e == nil {
		t.Fatal(e)
	}
	if _, e := ReadValue(d, Uint8Codec()); e == nil || e.Error() != "unknown enum tag" {
		t.Fatal(e)
	}
	if e := d.Finish(); e == nil || e.Error() != "unknown enum tag" {
		t.Fatal(e)
	}
	for _, firstRaw := range []bool{true, false} {
		d, _ = NewDecoder(nil, DefaultDecodeLimits())
		calls := 0
		if firstRaw {
			if _, e := c.DecodeRaw(d); e == nil || e.Error() != "truncated value" {
				t.Fatal(e)
			}
		}
		custom, _ := NewValueCodec[Unit]("callback", nil, func(d *Decoder) (Unit, error) {
			calls++
			_, _ = c.DecodeRaw(d)
			return Unit{}, errors.New("later callback error")
		}, nil)
		if _, e := ReadValue(d, custom); e == nil || e.Error() != "truncated value" {
			t.Fatal(e)
		}
		if e := d.Finish(); e == nil || e.Error() != "truncated value" {
			t.Fatal(e)
		}
		want := 1
		if firstRaw {
			want = 0
		}
		if calls != want {
			t.Fatal(calls, want)
		}
	}
}

func TestDerivedDiagnosticsAndBinderFreshMalformed(t *testing.T) {
	n := 0
	p := pairDeclaration(t, &n)
	if _, ok, e := p.VariantFor(derivePair{}); e != nil || ok {
		t.Fatal(ok, e)
	}
	for _, name := range []string{"Idle", "Waiting"} {
		cc, _ := TaggedCase(7, name, UnitCodec(), func(Unit) (Unit, error) { return Unit{}, nil }, func(Unit) (Unit, error) { return Unit{}, nil })
		c, _ := TaggedEnum("E", func(Unit) (uint32, error) { return 7, nil }, DefaultEnumOptions(), cc)
		if label, ok, e := c.VariantFor(Unit{}); e != nil || !ok || label != name {
			t.Fatal(label, ok, e)
		}
		if b, e := EncodeValue(c, Unit{}, 1); e != nil || !bytes.Equal(b, []byte{7}) {
			t.Fatal(b, e)
		}
	}
	calls := 0
	work := uint64(3)
	opts := DefaultModelCodecOptions()
	opts.DecodeLimits = func() (DecodeLimits, error) { calls++; return DecodeLimits{Bytes: 3, Work: work, Depth: 2}, nil }
	output, _ := NewValueCodec("Output", Uint8Codec().EncodeRaw, nil, nil)
	b, e := BindModelCodec(func() (Metadata, error) { return Identity("n13", "test"), nil }, p, p, output, opts)
	if e != nil {
		t.Fatal(e)
	}
	for _, decode := range []func([]byte) (derivePair, error){b.DecodeState, b.DecodeInput} {
		if v, e := decode([]byte{7, 0x34, 0x12}); e != nil || v != (derivePair{7, 0x1234}) {
			t.Fatal(v, e)
		}
		for _, bad := range [][]byte{nil, {7, 0x34}, {7, 0x34, 0x12, 0}} {
			if _, e := decode(bad); e == nil {
				t.Fatal(bad)
			}
		}
	}
	work = 2
	if _, e := b.DecodeState([]byte{7, 0x34, 0x12}); e == nil {
		t.Fatal("one under")
	}
	work = 3
	if _, e := b.DecodeState([]byte{7, 0x34, 0x12}); e != nil {
		t.Fatal(e)
	}
	if calls != 10 {
		t.Fatal(calls)
	}
	_, _ = b.EncodeState(derivePair{7, 0x1234})
	_, _ = b.EncodeInput(derivePair{7, 0x1234})
	_, _ = b.EncodeOutput(7)
	var dest []byte
	e = WithEncodeBuffer(&dest, 100, func(s *EncodeBuffer) error {
		if e := b.EncodeStateInto(derivePair{7, 0x1234}, s); e != nil {
			return e
		}
		if e := b.EncodeInputInto(derivePair{7, 0x1234}, s); e != nil {
			return e
		}
		return b.EncodeOutputInto(7, s)
	})
	if e != nil || calls != 10 {
		t.Fatal(e, calls)
	}
}

func TestDerivedRecordBoolOrdinaryTextAndDirectionalEnums(t *testing.T) {
	type header struct {
		Byte  uint8
		Ready bool
		Count uint16
	}
	a, _ := OrdinaryField("byte", Uint8Codec())
	b, _ := OrdinaryField("ready", BoolCodec())
	c, _ := OrdinaryField("count", Uint16Codec())
	constructs := 0
	h, _ := OrderedRecord("Record", PrependRecordField(a, PrependRecordField(b, PrependRecordField(c, NoRecordFields()))), func(v header) (FieldProduct[uint8, FieldProduct[bool, FieldProduct[uint16, Unit]]], error) {
		return FieldProduct[uint8, FieldProduct[bool, FieldProduct[uint16, Unit]]]{v.Byte, FieldProduct[bool, FieldProduct[uint16, Unit]]{v.Ready, FieldProduct[uint16, Unit]{v.Count, Unit{}}}}, nil
	}, func(p FieldProduct[uint8, FieldProduct[bool, FieldProduct[uint16, Unit]]]) (header, error) {
		constructs++
		return header{p.Head, p.Tail.Head, p.Tail.Tail.Head}, nil
	}, RecordOptions{})
	wire := []byte{7, 1, 0x34, 0x12}
	value := header{7, true, 0x1234}
	if encoded, e := EncodeValue(h, value, 4); e != nil || !bytes.Equal(encoded, wire) {
		t.Fatal(encoded, e)
	}
	if got, e := FromValueBytes(h, wire, DecodeLimits{Bytes: 4, Work: 5, Depth: 3}); e != nil || got != value {
		t.Fatal(got, e)
	}
	before := constructs
	if _, e := FromValueBytes(h, []byte{7, 2, 0x34, 0x12}, DefaultDecodeLimits()); e == nil || constructs != before {
		t.Fatal(e, constructs)
	}
	var dest []byte
	_ = WithEncodeBuffer(&dest, 3, func(s *EncodeBuffer) error {
		if e := h.EncodeRaw(value, s); e == nil {
			t.Fatal("cap accepted")
		}
		if !bytes.Equal(dest, []byte{7, 1}) {
			t.Fatal(dest)
		}
		return s.Finish()
	})
	f, _ := OrdinaryField("text", StringCodec())
	text, _ := OrderedRecord("Text", PrependRecordField(f, NoRecordFields()), func(v string) (FieldProduct[string, Unit], error) { return FieldProduct[string, Unit]{v, Unit{}}, nil }, func(p FieldProduct[string, Unit]) (string, error) { return p.Head, nil }, RecordOptions{})
	l := DecodeLimits{Bytes: 7, Work: 6, Depth: 3, Allocation: 3, Elements: 3}
	tw := []byte{3, 0, 0, 0, 'a', 'b', 'c'}
	if v, e := FromValueBytes(text, tw, l); e != nil || v != "abc" {
		t.Fatal(v, e)
	}
	l.Work = 5
	if _, e := FromValueBytes(text, tw, l); e == nil {
		t.Fatal("work")
	}
	l.Work = 6
	l.Depth = 2
	if _, e := FromValueBytes(text, tw, l); e == nil {
		t.Fatal("depth")
	}
	for _, encodeOnly := range []bool{true, false} {
		eo, _ := NewValueCodec("encode only", Uint8Codec().EncodeRaw, nil, nil)
		do, _ := NewValueCodec[uint8]("decode only", nil, Uint8Codec().DecodeRaw, nil)
		payload := eo
		if !encodeOnly {
			payload = do
		}
		unit, _ := TaggedCase(7, "Idle", UnitCodec(), func(Unit) (Unit, error) { return Unit{}, nil }, func(Unit) (Unit, error) { return Unit{}, nil })
		other, _ := TaggedCase(2, "Value", payload, func(Unit) (uint8, error) { return 0, nil }, func(uint8) (Unit, error) { return Unit{}, nil })
		var selectTag func(Unit) (uint32, error)
		if encodeOnly {
			selectTag = func(Unit) (uint32, error) { return 7, nil }
		}
		c, e := TaggedEnum("Directional", selectTag, DefaultEnumOptions(), unit, other)
		if e != nil {
			t.Fatal(e)
		}
		if (c.encode != nil) != encodeOnly || (c.decode != nil) == encodeOnly {
			t.Fatal("directions")
		}
		if encodeOnly {
			if b, e := EncodeValue(c, Unit{}, 1); e != nil || !bytes.Equal(b, []byte{7}) {
				t.Fatal(b, e)
			}
		} else {
			if _, e := FromValueBytes(c, []byte{7}, DecodeLimits{Bytes: 1, Work: 2, Depth: 2}); e != nil {
				t.Fatal(e)
			}
		}
	}
}

func TestBoundModelCodecAdmittedTrailingBothDirections(t *testing.T) {
	cc, _ := TaggedCase(7, "Idle", UnitCodec(), func(Unit) (Unit, error) { return Unit{}, nil }, func(Unit) (Unit, error) { return Unit{}, nil })
	event, _ := TaggedEnum("Event8", func(Unit) (uint32, error) { return 7, nil }, DefaultEnumOptions(), cc)
	b, e := BindModelCodec(func() (Metadata, error) { return Identity("n13", "test"), nil }, event, event, UnitCodec(), DefaultModelCodecOptions())
	if e != nil {
		t.Fatal(e)
	}
	for _, decode := range []func([]byte) (Unit, error){b.DecodeState, b.DecodeInput} {
		if _, e := decode([]byte{7, 0}); e == nil || e.Error() != "trailing value bytes" {
			t.Fatal(e)
		}
		if _, e := decode(nil); e == nil || e.Error() != "truncated value" {
			t.Fatal(e)
		}
	}
}

func TestDerivedAdditionalLiteralEnumAndAdapterRows(t *testing.T) {
	// Ordinary enum payload fields are explicit ordered records decoded raw by cases.
	for _, tc := range []struct {
		width uint8
		tag   uint32
		label string
		want  []byte
	}{{1, 2, "Set", []byte{2, 0x34, 0x12}}, {2, 400, "High", []byte{0x90, 1, 0x34, 0x12}}} {
		f, _ := OrdinaryField("value", Uint16Codec())
		payload, _ := OrderedRecord("Payload", PrependRecordField(f, NoRecordFields()), func(v uint16) (FieldProduct[uint16, Unit], error) { return FieldProduct[uint16, Unit]{v, Unit{}}, nil }, func(p FieldProduct[uint16, Unit]) (uint16, error) { return p.Head, nil }, RecordOptions{})
		cc, _ := TaggedCase(tc.tag, tc.label, payload, func(v uint16) (uint16, error) { return v, nil }, func(v uint16) (uint16, error) { return v, nil })
		opts := DefaultEnumOptions()
		opts.TagWidth = tc.width
		c, e := TaggedEnum("Event", func(uint16) (uint32, error) { return tc.tag, nil }, opts, cc)
		if e != nil {
			t.Fatal(e)
		}
		if b, e := EncodeValue(c, 0x1234, 10); e != nil || !bytes.Equal(b, tc.want) {
			t.Fatal(b, e)
		}
		if v, e := FromValueBytes(c, tc.want, DecodeLimits{Bytes: uint64(len(tc.want)), Work: 3, Depth: 2}); e != nil || v != 0x1234 {
			t.Fatal(v, e)
		}
	}
	type ready struct {
		Ready bool
		Byte  uint8
	}
	bf, _ := OrdinaryField("ready", BoolCodec())
	uf, _ := OrdinaryField("byte", Uint8Codec())
	payload, _ := OrderedRecord("ReadyPayload", PrependRecordField(bf, PrependRecordField(uf, NoRecordFields())), func(v ready) (FieldProduct[bool, FieldProduct[uint8, Unit]], error) {
		return FieldProduct[bool, FieldProduct[uint8, Unit]]{v.Ready, FieldProduct[uint8, Unit]{v.Byte, Unit{}}}, nil
	}, func(p FieldProduct[bool, FieldProduct[uint8, Unit]]) (ready, error) {
		return ready{p.Head, p.Tail.Head}, nil
	}, RecordOptions{})
	cc, _ := TaggedCase(255, "Ready", payload, func(v ready) (ready, error) { return v, nil }, func(v ready) (ready, error) { return v, nil })
	en, _ := TaggedEnum("Event8", func(ready) (uint32, error) { return 255, nil }, DefaultEnumOptions(), cc)
	if b, e := EncodeValue(en, ready{true, 9}, 3); e != nil || !bytes.Equal(b, []byte{255, 1, 9}) {
		t.Fatal(b, e)
	}
	if v, e := FromValueBytes(en, []byte{255, 1, 9}, DecodeLimits{Bytes: 3, Work: 5, Depth: 3}); e != nil || v != (ready{true, 9}) {
		t.Fatal(v, e)
	}
	if label, ok, e := en.VariantFor(ready{}); e != nil || !ok || label != "Ready" {
		t.Fatal(label, ok, e)
	}
	af, _ := AdapterField("be", func(v uint16, s *EncodeBuffer) error { return s.Append([]byte{byte(v >> 8), byte(v)}) }, func(d *Decoder) (uint16, error) {
		b, e := d.Take(2)
		if e != nil {
			return 0, e
		}
		return uint16(b[0])<<8 | uint16(b[1]), nil
	})
	be, _ := OrderedRecord("BigEndian", PrependRecordField(af, NoRecordFields()), func(v uint16) (FieldProduct[uint16, Unit], error) { return FieldProduct[uint16, Unit]{v, Unit{}}, nil }, func(p FieldProduct[uint16, Unit]) (uint16, error) { return p.Head, nil }, RecordOptions{})
	if b, e := EncodeValue(be, 0x1234, 2); e != nil || !bytes.Equal(b, []byte{0x12, 0x34}) {
		t.Fatal(b, e)
	}
	if v, e := FromValueBytes(be, []byte{0x12, 0x34}, DecodeLimits{Bytes: 2, Work: 1, Depth: 1}); e != nil || v != 0x1234 {
		t.Fatal(v, e)
	}
}

func TestDerivedAdditionalRecordAndMaxTagRows(t *testing.T) {
	n := 0
	p := pairDeclaration(t, &n)
	type nested struct {
		Marker uint8
		Pair   derivePair
	}
	a, _ := OrdinaryField("marker", Uint8Codec())
	b, _ := OrdinaryField("pair", p)
	c, _ := OrderedRecord("Nested", PrependRecordField(a, PrependRecordField(b, NoRecordFields())), func(v nested) (FieldProduct[uint8, FieldProduct[derivePair, Unit]], error) {
		return FieldProduct[uint8, FieldProduct[derivePair, Unit]]{v.Marker, FieldProduct[derivePair, Unit]{v.Pair, Unit{}}}, nil
	}, func(p FieldProduct[uint8, FieldProduct[derivePair, Unit]]) (nested, error) {
		return nested{p.Head, p.Tail.Head}, nil
	}, RecordOptions{})
	want := []byte{9, 1, 2, 1}
	value := nested{9, derivePair{1, 258}}
	if got, e := EncodeValue(c, value, 4); e != nil || !bytes.Equal(got, want) {
		t.Fatal(got, e)
	}
	if v, e := FromValueBytes(c, want, DecodeLimits{Bytes: 4, Work: 5, Depth: 3}); e != nil || v != value {
		t.Fatal(v, e)
	}
	f, _ := OrdinaryField("0", Uint8Codec())
	payload, _ := OrderedRecord("Payload", PrependRecordField(f, NoRecordFields()), func(v uint8) (FieldProduct[uint8, Unit], error) { return FieldProduct[uint8, Unit]{v, Unit{}}, nil }, func(p FieldProduct[uint8, Unit]) (uint8, error) { return p.Head, nil }, RecordOptions{})
	cc, _ := TaggedCase(0xffffffff, "Maximum", payload, func(v uint8) (uint8, error) { return v, nil }, func(v uint8) (uint8, error) { return v, nil })
	opts := DefaultEnumOptions()
	opts.TagWidth = 4
	en, _ := TaggedEnum("Event32", func(uint8) (uint32, error) { return 0xffffffff, nil }, opts, cc)
	if b, e := EncodeValue(en, 7, 5); e != nil || !bytes.Equal(b, []byte{255, 255, 255, 255, 7}) {
		t.Fatal(b, e)
	}
	if v, e := FromValueBytes(en, []byte{255, 255, 255, 255, 7}, DecodeLimits{Bytes: 5, Work: 3, Depth: 2}); e != nil || v != 7 {
		t.Fatal(v, e)
	}
	for _, encodeOnly := range []bool{true, false} {
		eo, _ := NewValueCodec("encode", Uint8Codec().EncodeRaw, nil, nil)
		do, _ := NewValueCodec[uint8]("decode", nil, Uint8Codec().DecodeRaw, nil)
		raw := eo
		if !encodeOnly {
			raw = do
		}
		f, _ := OrdinaryField("0", raw)
		r, _ := OrderedRecord("Directional", PrependRecordField(f, NoRecordFields()), func(v uint8) (FieldProduct[uint8, Unit], error) { return FieldProduct[uint8, Unit]{v, Unit{}}, nil }, func(p FieldProduct[uint8, Unit]) (uint8, error) { return p.Head, nil }, RecordOptions{})
		if (r.encode != nil) != encodeOnly || (r.decode != nil) == encodeOnly {
			t.Fatal("direction")
		}
		if encodeOnly {
			if b, e := EncodeValue(r, 7, 1); e != nil || !bytes.Equal(b, []byte{7}) {
				t.Fatal(b, e)
			}
		} else {
			if v, e := FromValueBytes(r, []byte{7}, DecodeLimits{Bytes: 1, Work: 2, Depth: 2}); e != nil || v != 7 {
				t.Fatal(v, e)
			}
		}
	}
}

func TestDerivedMissingDirectionsAndRawLiteralPrefixes(t *testing.T) {
	eo, _ := NewValueCodec("enc", Uint8Codec().EncodeRaw, nil, nil)
	ordinary, _ := OrdinaryField("x", eo)
	decoder := ValueDirections{Decode: true}
	project := func(v uint8) (FieldProduct[uint8, Unit], error) { return FieldProduct[uint8, Unit]{v, Unit{}}, nil }
	construct := func(p FieldProduct[uint8, Unit]) (uint8, error) { return p.Head, nil }
	if _, e := OrderedRecord("missing", PrependRecordField(ordinary, NoRecordFields()), project, construct, RecordOptions{Require: &decoder}); e == nil {
		t.Fatal("ordinary")
	}
	lengthOnly, _ := NewLengthValueCodec[string]("decode length", nil, func(d *Decoder, w uint8) (string, error) { return DecodeStringLength(d, w) }, nil)
	lf, _ := LengthField("x", lengthOnly, 1)
	encoder := ValueDirections{Encode: true}
	if _, e := OrderedRecord("missing length", PrependRecordField(lf, NoRecordFields()), func(v string) (FieldProduct[string, Unit], error) { return FieldProduct[string, Unit]{v, Unit{}}, nil }, func(p FieldProduct[string, Unit]) (string, error) { return p.Head, nil }, RecordOptions{Require: &encoder}); e == nil {
		t.Fatal("length")
	}
	af, _ := AdapterField("x", Uint8Codec().EncodeRaw, nil)
	if _, e := OrderedRecord("missing adapter", PrependRecordField(af, NoRecordFields()), project, construct, RecordOptions{Require: &decoder}); e == nil {
		t.Fatal("adapter")
	}
	for _, w := range []uint8{0, 3, 8} {
		opts := DefaultEnumOptions()
		opts.TagWidth = w
		if _, e := TaggedEnum[Unit]("bad", nil, opts); e == nil {
			t.Fatal(w)
		}
	}
	for _, w := range []uint8{0, 3, 8} {
		if _, e := LengthField("bad", StringLengthValueCodec(), w); e == nil {
			t.Fatal(w)
		}
	}
	marker := errors.New("adapter rejected")
	raw, _ := AdapterField("x", func(_ uint8, b *EncodeBuffer) error {
		if e := b.Append([]byte{0xaa}); e != nil {
			return e
		}
		return marker
	}, func(*Decoder) (uint8, error) { return 0, nil })
	prefix, _ := OrdinaryField("prefix", Uint8Codec())
	r, _ := OrderedRecord("AdapterError", PrependRecordField(prefix, PrependRecordField(raw, NoRecordFields())), func(Unit) (FieldProduct[uint8, FieldProduct[uint8, Unit]], error) {
		return FieldProduct[uint8, FieldProduct[uint8, Unit]]{7, FieldProduct[uint8, Unit]{1, Unit{}}}, nil
	}, func(FieldProduct[uint8, FieldProduct[uint8, Unit]]) (Unit, error) { return Unit{}, nil }, RecordOptions{})
	var dest []byte
	e := WithEncodeBuffer(&dest, 3, func(b *EncodeBuffer) error {
		if e := r.EncodeRaw(Unit{}, b); e != marker {
			t.Fatal(e)
		}
		if !bytes.Equal(dest, []byte{7, 0xaa}) {
			t.Fatal(dest)
		}
		if e := b.Append([]byte{0xbb}); e != nil {
			return e
		}
		return b.Finish()
	})
	if e != nil || !bytes.Equal(dest, []byte{7, 0xaa, 0xbb}) {
		t.Fatal(dest, e)
	}
}

package statelessness

import "fmt"

func PairAddressCodec[L, R any](left ValueCodec[L], right ValueCodec[R]) (ValueCodec[PairAddress[L, R]], error) {
	a, ok := left.Layout()
	b, ok2 := right.Layout()
	if !ok || !ok2 || a.Size() > ^uint64(0)-64 || b.Size() > ^uint64(0)-64-a.Size() {
		return ValueCodec[PairAddress[L, R]]{}, &ModelError{"pair payload layouts required"}
	}
	layout, _ := NewValueLayout(a.Size() + b.Size() + 64)
	return NewValueCodec("PairAddress", func(v PairAddress[L, R], out *EncodeBuffer) error {
		switch v.Side {
		case "left":
			if e := Uint8Codec().EncodeRaw(0, out); e != nil {
				return e
			}
			return left.EncodeRaw(v.Left, out)
		case "right":
			if e := Uint8Codec().EncodeRaw(1, out); e != nil {
				return e
			}
			return right.EncodeRaw(v.Right, out)
		default:
			return &ModelError{"invalid pair address tag"}
		}
	}, func(d *Decoder) (PairAddress[L, R], error) {
		tag, e := ReadValue(d, Uint8Codec())
		if e != nil {
			return PairAddress[L, R]{}, e
		}
		if tag == 0 {
			v, e := ReadValue(d, left)
			return PairLeft[L, R](v), e
		}
		if tag == 1 {
			v, e := ReadValue(d, right)
			return PairRight[L, R](v), e
		}
		return PairAddress[L, R]{}, &ModelError{"invalid pair address tag"}
	}, &layout)
}
func PairCodec[L, R, LI, RI, LO, RO any](left ValueCodec[L], right ValueCodec[R], li ValueCodec[LI], ri ValueCodec[RI], lo ValueCodec[LO], ro ValueCodec[RO], metadata func() ([]Metadata, error), maxPending int) (*Codec[PairState[L, R, LI, RI], PairInput[LI, RI], PairAddress[LO, RO]], error) {
	msg, e := PairAddressCodec(li, ri)
	if e != nil {
		return nil, e
	}
	queue, e := VectorOf(msg)
	if e != nil {
		return nil, e
	}
	state, e := NewValueCodec("PairState", func(s PairState[L, R, LI, RI], b *EncodeBuffer) error {
		if e := left.EncodeRaw(s.Left, b); e != nil {
			return e
		}
		if e := right.EncodeRaw(s.Right, b); e != nil {
			return e
		}
		return queue.EncodeRaw(s.Pending, b)
	}, func(d *Decoder) (PairState[L, R, LI, RI], error) {
		var s PairState[L, R, LI, RI]
		var e error
		s.Left, e = ReadValue(d, left)
		if e != nil {
			return s, e
		}
		s.Right, e = ReadValue(d, right)
		if e != nil {
			return s, e
		}
		s.Pending, e = ReadValue(d, queue)
		return s, e
	}, nil)
	if e != nil {
		return nil, e
	}
	input, e := NewValueCodec("PairInput", func(i PairInput[LI, RI], b *EncodeBuffer) error {
		if i.Kind == "local" {
			if e := Uint8Codec().EncodeRaw(0, b); e != nil {
				return e
			}
			return msg.EncodeRaw(i.Address, b)
		}
		if i.Kind != "deliver" {
			return &ModelError{"invalid composition input tag"}
		}
		if e := Uint8Codec().EncodeRaw(1, b); e != nil {
			return e
		}
		return Uint32Codec().EncodeRaw(i.Index, b)
	}, func(d *Decoder) (PairInput[LI, RI], error) {
		var i PairInput[LI, RI]
		tag, e := ReadValue(d, Uint8Codec())
		if e != nil {
			return i, e
		}
		if tag == 0 {
			i.Kind = "local"
			i.Address, e = ReadValue(d, msg)
			return i, e
		}
		if tag == 1 {
			i.Kind = "deliver"
			i.Index, e = ReadValue(d, Uint32Codec())
			return i, e
		}
		return i, &ModelError{"invalid composition input tag"}
	}, nil)
	if e != nil {
		return nil, e
	}
	output, e := PairAddressCodec(lo, ro)
	if e != nil {
		return nil, e
	}
	identity := func() (Metadata, error) {
		ms, e := metadata()
		if e != nil {
			return Metadata{}, e
		}
		if len(ms) != 3 {
			return Metadata{}, &ModelError{"pair requires left, right, wiring metadata"}
		}
		names, builds := "", ""
		for _, m := range ms {
			s := fmt.Sprintf("%d:%s:%d/%d/%d", len(m.Name), m.Name, m.ModelVersion, m.PropertiesVersion, m.CodecVersion)
			names += fmt.Sprintf("%d:%s", len(s), s)
			builds += fmt.Sprintf("%d:%s", len(m.Build), m.Build)
		}
		return Identity(fmt.Sprintf("pair-v1:%s;pending=%d", names, maxPending), builds+";native"), nil
	}
	return BindModelCodec(identity, state, input, output, DefaultModelCodecOptions())
}

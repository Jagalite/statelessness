package statelessness

// ValueDirections declares eager requirements without filtering inferred support.
type ValueDirections struct{ Encode, Decode bool }
type RecordOptions struct {
	Require *ValueDirections
	Layout  *ValueLayout
}
type RecordField[F any] struct {
	encode func(F, *EncodeBuffer) error
	decode func(*Decoder) (F, error)
}
type FieldProduct[H, T any] struct {
	Head H
	Tail T
}
type RecordFields[P any] struct {
	encode func(P, *EncodeBuffer) error
	decode func(*Decoder) (P, error)
}

func OrdinaryField[F any](label string, c ValueCodec[F]) (RecordField[F], error) {
	f := RecordField[F]{}
	if c.encode != nil {
		f.encode = c.EncodeRaw
	}
	if c.decode != nil {
		f.decode = func(d *Decoder) (F, error) { return ReadValue(d, c) }
	}
	return f, nil
}
func LengthField[F any](label string, c LengthValueCodec[F], width uint8) (RecordField[F], error) {
	if width != 1 && width != 2 && width != 4 {
		return RecordField[F]{}, &ConfigError{"unsupported length width"}
	}
	f := RecordField[F]{}
	if c.encode != nil {
		f.encode = func(v F, b *EncodeBuffer) error { return c.EncodeLengthRaw(v, width, b) }
	}
	if c.decode != nil {
		f.decode = func(d *Decoder) (F, error) { return c.DecodeLengthRaw(d, width) }
	}
	return f, nil
}
func AdapterField[F any](label string, encode func(F, *EncodeBuffer) error, decode func(*Decoder) (F, error)) (RecordField[F], error) {
	// Wrap each callback in the existing raw lease/panic boundary.
	c := ValueCodec[F]{encode: encode, decode: decode}
	f := RecordField[F]{}
	if encode != nil {
		f.encode = c.EncodeRaw
	}
	if decode != nil {
		f.decode = c.DecodeRaw
	}
	return f, nil
}
func NoRecordFields() RecordFields[Unit] {
	return RecordFields[Unit]{func(Unit, *EncodeBuffer) error { return nil }, func(*Decoder) (Unit, error) { return Unit{}, nil }}
}
func PrependRecordField[H, T any](h RecordField[H], t RecordFields[T]) RecordFields[FieldProduct[H, T]] {
	f := RecordFields[FieldProduct[H, T]]{}
	if h.encode != nil && t.encode != nil {
		f.encode = func(v FieldProduct[H, T], b *EncodeBuffer) error {
			if e := h.encode(v.Head, b); e != nil {
				return e
			}
			return t.encode(v.Tail, b)
		}
	}
	if h.decode != nil && t.decode != nil {
		f.decode = func(d *Decoder) (v FieldProduct[H, T], e error) {
			v.Head, e = h.decode(d)
			if e != nil {
				return FieldProduct[H, T]{}, e
			}
			v.Tail, e = t.decode(d)
			if e != nil {
				return FieldProduct[H, T]{}, e
			}
			return v, nil
		}
	}
	return f
}
func derivedValue[T any](schema string, enc func(T, *EncodeBuffer) error, dec func(*Decoder) (T, error), require *ValueDirections, layout *ValueLayout) (ValueCodec[T], error) {
	if require != nil && ((require.Encode && enc == nil) || (require.Decode && dec == nil)) {
		return ValueCodec[T]{}, &ConfigError{"required value direction unavailable"}
	}
	c := ValueCodec[T]{schema: schema, encode: enc, decode: dec, declared: true}
	if layout != nil {
		if !layout.valid {
			return ValueCodec[T]{}, &ConfigError{"invalid logical value layout"}
		}
		v := *layout
		c.layout = &v
	}
	return c, nil
}
func OrderedRecord[R, P any](schema string, fields RecordFields[P], project func(R) (P, error), construct func(P) (R, error), options RecordOptions) (ValueCodec[R], error) {
	var enc func(R, *EncodeBuffer) error
	var dec func(*Decoder) (R, error)
	if project != nil && fields.encode != nil {
		enc = func(v R, b *EncodeBuffer) error {
			p, e := project(v)
			if e != nil {
				return e
			}
			return fields.encode(p, b)
		}
	}
	if construct != nil && fields.decode != nil {
		dec = func(d *Decoder) (r R, e error) {
			p, e := fields.decode(d)
			if e != nil {
				return r, e
			}
			r, e = construct(p)
			if e != nil {
				var z R
				return z, e
			}
			return r, nil
		}
	}
	return derivedValue(schema, enc, dec, options.Require, options.Layout)
}

type EnumCase[E any] struct {
	tag    uint32
	label  string
	encode func(E, *EncodeBuffer) error
	decode func(*Decoder) (E, error)
}
type EnumOptions struct {
	Require  *ValueDirections
	TagWidth uint8
	Layout   *ValueLayout
}

func DefaultEnumOptions() EnumOptions { return EnumOptions{TagWidth: 1} }
func TaggedCase[E, P any](tag uint32, label string, payload ValueCodec[P], extract func(E) (P, error), construct func(P) (E, error)) (EnumCase[E], error) {
	c := EnumCase[E]{tag: tag, label: label}
	if payload.encode != nil && extract != nil {
		c.encode = func(v E, b *EncodeBuffer) error {
			p, e := extract(v)
			if e != nil {
				return e
			}
			return payload.EncodeRaw(p, b)
		}
	}
	if payload.decode != nil && construct != nil {
		c.decode = func(d *Decoder) (v E, e error) {
			p, e := payload.DecodeRaw(d)
			if e != nil {
				return v, e
			}
			v, e = construct(p)
			if e != nil {
				var z E
				return z, e
			}
			return v, nil
		}
	}
	return c, nil
}
func TaggedEnum[E any](schema string, selectTag func(E) (uint32, error), options EnumOptions, cases ...EnumCase[E]) (ValueCodec[E], error) {
	w := options.TagWidth
	if w != 1 && w != 2 && w != 4 {
		return ValueCodec[E]{}, &ConfigError{"unsupported enum tag width"}
	}
	owned := make(map[uint32]EnumCase[E], len(cases))
	ce, cd := selectTag != nil, true
	for _, c := range cases {
		if (w == 1 && c.tag > 255) || (w == 2 && c.tag > 65535) {
			return ValueCodec[E]{}, &ConfigError{"enum tag overflow"}
		}
		if _, ok := owned[c.tag]; ok {
			return ValueCodec[E]{}, &ConfigError{"duplicate enum tag"}
		}
		owned[c.tag] = c
		ce = ce && c.encode != nil
		cd = cd && c.decode != nil
	}
	var enc func(E, *EncodeBuffer) error
	var dec func(*Decoder) (E, error)
	if ce {
		enc = func(v E, b *EncodeBuffer) error {
			tag, e := selectTag(v)
			if e != nil {
				return e
			}
			c, ok := owned[tag]
			if !ok {
				return &ModelError{"unknown enum tag"}
			}
			if e = encodeValueLength(uint64(tag), w, b); e != nil {
				return e
			}
			return c.encode(v, b)
		}
	}
	if cd {
		dec = func(d *Decoder) (v E, e error) {
			tag, e := decodeValueLength(d, w)
			if e != nil {
				return v, e
			}
			c, ok := owned[uint32(tag)]
			if !ok {
				return v, &ModelError{"unknown enum tag"}
			}
			return c.decode(d)
		}
	}
	c, e := derivedValue(schema, enc, dec, options.Require, options.Layout)
	if e != nil {
		return c, e
	}
	if selectTag != nil {
		c.variantFor = func(v E) (string, bool) {
			tag, e := selectTag(v)
			if e != nil {
				return "", false
			}
			entry, ok := owned[tag]
			return entry.label, ok
		}
	}
	return c, nil
}

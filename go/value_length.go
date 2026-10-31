package statelessness

// LengthValueCodec describes LengthEncode and LengthDecode independently of the
// ordinary value operations. Either direction may be absent.
type LengthValueCodec[T any] struct {
	schema  string
	encode  func(T, uint8, *EncodeBuffer) error
	decode  func(*Decoder, uint8) (T, error)
	layout  *ValueLayout
	builtin string
}

func NewLengthValueCodec[T any](schema string, encode func(T, uint8, *EncodeBuffer) error, decode func(*Decoder, uint8) (T, error), layout *ValueLayout) (LengthValueCodec[T], error) {
	if encode == nil && decode == nil {
		return LengthValueCodec[T]{}, &ConfigError{"length value codec has no operations"}
	}
	var owned *ValueLayout
	if layout != nil {
		if !layout.valid {
			return LengthValueCodec[T]{}, &ConfigError{"invalid logical value layout"}
		}
		copy := *layout
		owned = &copy
	}
	return LengthValueCodec[T]{schema: schema, encode: encode, decode: decode, layout: owned}, nil
}
func (c LengthValueCodec[T]) Schema() string { return c.schema }
func (c LengthValueCodec[T]) Layout() (ValueLayout, bool) {
	if c.layout == nil {
		return ValueLayout{}, false
	}
	return *c.layout, true
}

// WithWidth selects a length width without adding a decoder value guard.
func (c LengthValueCodec[T]) WithWidth(width uint8) (ValueCodec[T], error) {
	var encode func(T, *EncodeBuffer) error
	var decode func(*Decoder) (T, error)
	if c.encode != nil {
		encode = func(v T, out *EncodeBuffer) error { return c.encode(v, width, out) }
	}
	if c.decode != nil {
		decode = func(d *Decoder) (T, error) { return c.decode(d, width) }
	}
	result, err := NewValueCodec(c.schema, encode, decode, c.layout)
	result.builtin = c.builtin
	return result, err
}
func (c LengthValueCodec[T]) EncodeLengthRaw(v T, width uint8, out *EncodeBuffer) error {
	codec, e := c.WithWidth(width)
	if e != nil {
		return e
	}
	return codec.EncodeRaw(v, out)
}
func (c LengthValueCodec[T]) EncodeLengthInto(v T, width uint8, out *EncodeBuffer) error {
	codec, e := c.WithWidth(width)
	if e != nil {
		return e
	}
	return codec.EncodeInto(v, out)
}
func (c LengthValueCodec[T]) DecodeLengthRaw(d *Decoder, width uint8) (T, error) {
	codec, e := c.WithWidth(width)
	if e != nil {
		var zero T
		return zero, e
	}
	return codec.DecodeRaw(d)
}

func StringLengthValueCodec() LengthValueCodec[string] {
	layout, _ := NewValueLayout(24)
	codec, _ := NewLengthValueCodec("String", EncodeStringLength, DecodeStringLength, &layout)
	codec.builtin = "String"
	return codec
}
func VectorLengthValueCodec[T any](element ValueCodec[T]) (LengthValueCodec[[]T], error) {
	var encode func([]T, uint8, *EncodeBuffer) error
	var decode func(*Decoder, uint8) ([]T, error)
	if element.encode != nil {
		encode = func(v []T, w uint8, out *EncodeBuffer) error { return EncodeVectorLength(v, element, w, out) }
	}
	if element.decode != nil {
		decode = func(d *Decoder, w uint8) ([]T, error) { return DecodeVectorLength(d, element, w) }
	}
	layout, _ := NewValueLayout(24)
	return NewLengthValueCodec("Vec<"+element.Schema()+">", encode, decode, &layout)
}

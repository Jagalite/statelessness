package statelessness

import (
	"fmt"
	"math"
)

func compositeValueCodec[T any](schema string, encode func(T, *EncodeBuffer) error, decode func(*Decoder) (T, error), weight *uint64, canEncode, canDecode bool) (ValueCodec[T], error) {
	var layout *ValueLayout
	if weight != nil {
		v, e := NewValueLayout(*weight)
		if e != nil {
			return ValueCodec[T]{}, e
		}
		layout = &v
	}
	if !canEncode {
		encode = nil
	}
	if !canDecode {
		decode = nil
	}
	return NewValueCodec(schema, encode, decode, layout)
}
func valueWeight(n uint64) *uint64 { return &n }
func valueCompositeLayout[T any](c ValueCodec[T]) (uint64, bool) {
	v, ok := c.Layout()
	if !ok {
		return 0, false
	}
	return v.Size(), true
}
func OptionOf[T any](element ValueCodec[T]) (ValueCodec[Option[T]], error) {
	// Explicit finite Rust64 layout entries; no generic tag-plus-payload rule.
	var weight *uint64
	switch element.builtin {
	case "()", "bool":
		weight = valueWeight(1)
	case "u8", "i8":
		weight = valueWeight(2)
	case "u16", "i16":
		weight = valueWeight(4)
	case "u32", "i32":
		weight = valueWeight(8)
	case "u64", "i64":
		weight = valueWeight(16)
	case "u128", "i128":
		weight = valueWeight(32)
	case "String":
		weight = valueWeight(24)
	}
	return compositeValueCodec("Option<"+element.Schema()+">", func(v Option[T], out *EncodeBuffer) error {
		tag := uint8(0)
		if v.Present {
			tag = 1
		}
		if e := Uint8Codec().EncodeRaw(tag, out); e != nil {
			return e
		}
		if v.Present {
			return element.EncodeRaw(v.Value, out)
		}
		return nil
	}, func(d *Decoder) (Option[T], error) {
		var result Option[T]
		tag, e := ReadValue(d, Uint8Codec())
		if e != nil {
			return result, e
		}
		switch tag {
		case 0:
			return result, nil
		case 1:
			v, e := ReadValue(d, element)
			if e != nil {
				return result, e
			}
			return Option[T]{Value: v, Present: true}, nil
		default:
			return result, &ModelError{"invalid option tag"}
		}
	}, weight, element.encode != nil, element.decode != nil)
}

func EncodeVectorLength[T any](values []T, element ValueCodec[T], width uint8, out *EncodeBuffer) error {
	if element.encode == nil {
		return &ConfigError{"value codec has no encoder"}
	}
	if e := encodeValueLength(uint64(len(values)), width, out); e != nil {
		return e
	}
	for _, v := range values {
		if e := element.EncodeRaw(v, out); e != nil {
			return e
		}
	}
	return nil
}
func DecodeVectorLength[T any](d *Decoder, element ValueCodec[T], width uint8) ([]T, error) {
	if element.decode == nil {
		return nil, &ConfigError{"value codec has no decoder"}
	}
	n, e := decodeValueLength(d, width)
	if e != nil {
		return nil, e
	}
	weight, ok := valueCompositeLayout(element)
	if !ok {
		return nil, &ModelError{"value layout unavailable for collection element"}
	}
	if e = d.Collection(n, weight); e != nil {
		return nil, e
	}
	if n > uint64(math.MaxInt) {
		return nil, &ModelError{"decode allocation failed"}
	}
	values := make([]T, int(n))
	for i := range values {
		v, e := ReadValue(d, element)
		if e != nil {
			return nil, e
		}
		values[i] = v
	}
	return values, nil
}
func VectorLengthOf[T any](element ValueCodec[T], width uint8) (ValueCodec[[]T], error) {

	schema := "Vec<" + element.Schema() + ">"
	if width != 4 {
		schema = fmt.Sprintf("Vec%d<%s>", width, element.Schema())
	}
	return compositeValueCodec(schema, func(v []T, out *EncodeBuffer) error { return EncodeVectorLength(v, element, width, out) }, func(d *Decoder) ([]T, error) { return DecodeVectorLength(d, element, width) }, valueWeight(24), element.encode != nil, element.decode != nil)
}
func VectorOf[T any](element ValueCodec[T]) (ValueCodec[[]T], error) {
	return VectorLengthOf(element, 4)
}

// ArrayOf represents Rust's fixed-array wire schema as an exactly sized Go slice.
// Applications can adapt ordinary [N]T values through NewValueCodec callbacks.
func ArrayOf[T any](n uint64, element ValueCodec[T]) (ValueCodec[[]T], error) {
	var weight *uint64
	if size, ok := valueCompositeLayout(element); ok {
		if size != 0 && n > math.MaxUint64/size {
			return ValueCodec[[]T]{}, &ModelError{"value layout size overflow"}
		}
		weight = valueWeight(n * size)
	}
	return compositeValueCodec(fmt.Sprintf("[%s;%d]", element.Schema(), n), func(v []T, out *EncodeBuffer) error {
		if uint64(len(v)) != n {
			return &ModelError{"fixed array length mismatch"}
		}
		for _, v := range v {
			if e := element.EncodeRaw(v, out); e != nil {
				return e
			}
		}
		return nil
	}, func(d *Decoder) ([]T, error) {
		size, ok := valueCompositeLayout(element)
		if !ok {
			return nil, &ModelError{"value layout unavailable for collection element"}
		}
		if e := d.Collection(n, size); e != nil {
			return nil, e
		}
		if n > uint64(math.MaxInt) {
			return nil, &ModelError{"decode allocation failed"}
		}
		values := make([]T, int(n))
		for i := range values {
			v, e := ReadValue(d, element)
			if e != nil {
				return nil, e
			}
			values[i] = v
		}
		return values, nil
	}, weight, element.encode != nil, element.decode != nil)
}

type Tuple1[A any] struct{ V0 A }
type Tuple2[A, B any] struct {
	V0 A
	V1 B
}
type Tuple3[A, B, C any] struct {
	V0 A
	V1 B
	V2 C
}
type Tuple4[A, B, C, D any] struct {
	V0 A
	V1 B
	V2 C
	V3 D
}

func Tuple1Of[A any](a ValueCodec[A]) (ValueCodec[Tuple1[A]], error) {
	var weight *uint64
	if a.builtin == "()" {
		weight = valueWeight(0)
	}
	return compositeValueCodec("("+a.Schema()+",)", func(v Tuple1[A], out *EncodeBuffer) error { return a.EncodeRaw(v.V0, out) }, func(d *Decoder) (Tuple1[A], error) { v, e := ReadValue(d, a); return Tuple1[A]{v}, e }, weight, a.encode != nil, a.decode != nil)
}
func Tuple2Of[A, B any](a ValueCodec[A], b ValueCodec[B]) (ValueCodec[Tuple2[A, B]], error) {
	var weight *uint64
	key := "(" + a.Schema() + "," + b.Schema() + ")"
	trustedKey := "(" + a.builtin + "," + b.builtin + ")"
	switch trustedKey {
	case "(u8,u64)":
		weight = valueWeight(16)
	case "(u8,u8)":
		weight = valueWeight(2)
	case "((),())":
		weight = valueWeight(0)
	}
	return compositeValueCodec(key, func(v Tuple2[A, B], out *EncodeBuffer) error {
		if e := a.EncodeRaw(v.V0, out); e != nil {
			return e
		}
		return b.EncodeRaw(v.V1, out)
	}, func(d *Decoder) (Tuple2[A, B], error) {
		var result Tuple2[A, B]
		v, e := ReadValue(d, a)
		if e != nil {
			return result, e
		}
		w, e := ReadValue(d, b)
		if e != nil {
			return result, e
		}
		return Tuple2[A, B]{v, w}, nil
	}, weight, a.encode != nil && b.encode != nil, a.decode != nil && b.decode != nil)
}
func Tuple3Of[A, B, C any](a ValueCodec[A], b ValueCodec[B], c ValueCodec[C]) (ValueCodec[Tuple3[A, B, C]], error) {
	return compositeValueCodec("("+a.Schema()+","+b.Schema()+","+c.Schema()+")", func(v Tuple3[A, B, C], out *EncodeBuffer) error {
		if e := a.EncodeRaw(v.V0, out); e != nil {
			return e
		}
		if e := b.EncodeRaw(v.V1, out); e != nil {
			return e
		}
		return c.EncodeRaw(v.V2, out)
	}, func(d *Decoder) (Tuple3[A, B, C], error) {
		var result Tuple3[A, B, C]
		v, e := ReadValue(d, a)
		if e != nil {
			return result, e
		}
		w, e := ReadValue(d, b)
		if e != nil {
			return result, e
		}
		x, e := ReadValue(d, c)
		if e != nil {
			return result, e
		}
		return Tuple3[A, B, C]{v, w, x}, nil
	}, nil, a.encode != nil && b.encode != nil && c.encode != nil, a.decode != nil && b.decode != nil && c.decode != nil)
}
func Tuple4Of[A, B, C, D any](a ValueCodec[A], b ValueCodec[B], c ValueCodec[C], d ValueCodec[D]) (ValueCodec[Tuple4[A, B, C, D]], error) {
	return compositeValueCodec("("+a.Schema()+","+b.Schema()+","+c.Schema()+","+d.Schema()+")", func(v Tuple4[A, B, C, D], out *EncodeBuffer) error {
		if e := a.EncodeRaw(v.V0, out); e != nil {
			return e
		}
		if e := b.EncodeRaw(v.V1, out); e != nil {
			return e
		}
		if e := c.EncodeRaw(v.V2, out); e != nil {
			return e
		}
		return d.EncodeRaw(v.V3, out)
	}, func(reader *Decoder) (Tuple4[A, B, C, D], error) {
		var result Tuple4[A, B, C, D]
		v, e := ReadValue(reader, a)
		if e != nil {
			return result, e
		}
		w, e := ReadValue(reader, b)
		if e != nil {
			return result, e
		}
		x, e := ReadValue(reader, c)
		if e != nil {
			return result, e
		}
		y, e := ReadValue(reader, d)
		if e != nil {
			return result, e
		}
		return Tuple4[A, B, C, D]{v, w, x, y}, nil
	}, nil, a.encode != nil && b.encode != nil && c.encode != nil && d.encode != nil, a.decode != nil && b.decode != nil && c.decode != nil && d.decode != nil)
}
func OracleStateOf[S, H any](state ValueCodec[S], history ValueCodec[H]) (ValueCodec[OracleState[S, H]], error) {
	return compositeValueCodec("OracleState<"+state.Schema()+","+history.Schema()+">", func(v OracleState[S, H], out *EncodeBuffer) error {
		if e := state.EncodeRaw(v.Model, out); e != nil {
			return e
		}
		return history.EncodeRaw(v.Oracle, out)
	}, func(d *Decoder) (OracleState[S, H], error) {
		var result OracleState[S, H]
		v, e := ReadValue(d, state)
		if e != nil {
			return result, e
		}
		w, e := ReadValue(d, history)
		if e != nil {
			return result, e
		}
		return OracleState[S, H]{Model: v, Oracle: w}, nil
	}, nil, state.encode != nil && history.encode != nil, state.decode != nil && history.decode != nil)
}

// EncodeVectorLengthRaw is the explicit cooperative LengthEncode surface.
func EncodeVectorLengthRaw[T any](values []T, element ValueCodec[T], width uint8, out *EncodeBuffer) error {
	return EncodeVectorLength(values, element, width, out)
}

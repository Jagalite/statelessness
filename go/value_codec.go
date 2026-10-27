package statelessness

import (
	"encoding/binary"
	"math"
	"unicode/utf8"
)

// DecodeLimits are independent Rust64 structural request budgets, not heap/RSS.
type DecodeLimits struct{ Bytes, Allocation, Elements, Depth, Work uint64 }

func DefaultDecodeLimits() DecodeLimits {
	return DecodeLimits{16777216, 33554432, 1000000, 64, 2000000}
}

// Decoder owns its input. Raw operations cooperate with guarded ReadValue; only
// Take, Collection and guards record sticky failures. It is synchronous, reusable,
// and supports nested guards. Finish's trailing-bytes error is not sticky.
type Decoder struct {
	data     []byte
	position uint64
	limits   DecodeLimits
	depth    uint64
	failure  error
}

func NewDecoder(data []byte, l DecodeLimits) (d *Decoder, err error) {
	if uint64(len(data)) > l.Bytes {
		return nil, &ModelError{"decode byte limit"}
	}
	defer func() {
		if recover() != nil {
			d = nil
			err = &ModelError{"decode allocation failed"}
		}
	}()
	return &Decoder{data: append([]byte{}, data...), limits: l}, nil
}

// Built-in error payloads are owned; opaque host errors retain their identity.
func copyValueDecodeError(e error) error {
	if model, ok := e.(*ModelError); ok && model != nil {
		owned := *model
		return &owned
	}
	return e
}
func (d *Decoder) reject(e error) error {
	if d.failure == nil {
		d.failure = copyValueDecodeError(e)
	}
	return copyValueDecodeError(d.failure)
}
func (d *Decoder) Take(n uint64) (out []byte, err error) {
	if d == nil {
		return nil, &ConfigError{"nil value decoder"}
	}
	if d.failure != nil {
		return nil, copyValueDecodeError(d.failure)
	}
	if n > uint64(len(d.data))-d.position {
		return nil, d.reject(&ModelError{"truncated value"})
	}
	defer func() {
		if recover() != nil {
			out = nil
			err = d.reject(&ModelError{"decode allocation failed"})
		}
	}()
	out = append([]byte{}, d.data[int(d.position):int(d.position+n)]...)
	d.position += n
	return out, nil
}
func (d *Decoder) Collection(n, size uint64) error {
	if d == nil {
		return &ConfigError{"nil value decoder"}
	}
	if d.failure != nil {
		return copyValueDecodeError(d.failure)
	}
	if n > d.limits.Elements {
		return d.reject(&ModelError{"decode element limit"})
	}
	d.limits.Elements -= n
	if size != 0 && n > math.MaxUint64/size {
		return d.reject(&ModelError{"decode allocation overflow"})
	}
	allocation := n * size
	if allocation > d.limits.Allocation {
		return d.reject(&ModelError{"decode allocation limit"})
	}
	d.limits.Allocation -= allocation
	if n > d.limits.Work {
		return d.reject(&ModelError{"decode work limit"})
	}
	return nil
}
func (d *Decoder) Finish() error {
	if d == nil {
		return &ConfigError{"nil value decoder"}
	}
	if d.failure != nil {
		return copyValueDecodeError(d.failure)
	}
	if d.position != uint64(len(d.data)) {
		return &ModelError{"trailing value bytes"}
	}
	return nil
}
func (d *Decoder) Remaining() uint64 {
	if d == nil {
		return 0
	}
	return uint64(len(d.data)) - d.position
}
func (d *Decoder) Limits() DecodeLimits {
	if d == nil {
		return DecodeLimits{}
	}
	return d.limits
}
func (d *Decoder) Depth() uint64 {
	if d == nil {
		return 0
	}
	return d.depth
}

// ValueLayout is an explicit logical Rust64 collection element charge. A caller
// declaration is not a measurement or a claim about Go physical storage.
type ValueLayout struct {
	size  uint64
	valid bool
}

func NewValueLayout(size uint64) (ValueLayout, error) { return ValueLayout{size, true}, nil }
func (l ValueLayout) Size() uint64                    { return l.size }

// ValueCodec is opt-in canonical value encoding, unrelated to model Codec.
// Diagnostic schema/variant labels are not compatibility identities or wire tags.
type ValueCodec[T any] struct {
	encode     func(T, *EncodeBuffer) error
	decode     func(*Decoder) (T, error)
	schema     string
	variant    *string
	variantFor func(T) (string, bool)
	layout     *ValueLayout
	builtin    string
	declared   bool // initialized declaration may intentionally expose neither direction
}

func NewValueCodec[T any](schema string, encode func(T, *EncodeBuffer) error, decode func(*Decoder) (T, error), layout *ValueLayout) (ValueCodec[T], error) {
	if encode == nil && decode == nil {
		return ValueCodec[T]{}, &ConfigError{"value codec requires an encoder or decoder callback"}
	}
	c := ValueCodec[T]{encode: encode, decode: decode, schema: schema}
	if layout != nil {
		if !layout.valid {
			return ValueCodec[T]{}, &ConfigError{"invalid logical value layout"}
		}
		owned := *layout
		c.layout = &owned
	}
	return c, nil
}
func (c ValueCodec[T]) Schema() string { return c.schema }
func (c ValueCodec[T]) Variant() (string, bool) {
	if c.variant == nil {
		return "", false
	}
	return *c.variant, true
}
func (c ValueCodec[T]) WithVariant(v string) ValueCodec[T] {
	c.variant = &v
	c.variantFor = nil
	return c
}
func (c ValueCodec[T]) Layout() (ValueLayout, bool) {
	if c.layout == nil {
		return ValueLayout{}, false
	}
	return *c.layout, true
}
func (c ValueCodec[T]) WithLayout(l ValueLayout) (ValueCodec[T], error) {
	if !l.valid {
		return ValueCodec[T]{}, &ConfigError{"invalid logical value layout"}
	}
	owned := l
	c.layout = &owned
	c.builtin = ""
	return c, nil
}

// EncodeInto appends under a new revocable lease. A swallowed child failure
// remains sticky in the enclosing sink; callbacks cannot retain writable access.
func (c ValueCodec[T]) EncodeInto(v T, out *EncodeBuffer) error {
	return encodeValueCallback(c, v, out, true)
}

// EncodeRaw mirrors a direct TraceEncode call: returned callback/validation
// errors do not poison the sink. Only failed writes remain sticky. Each call
// still receives a revocable lease; raw composition must call children raw.
func (c ValueCodec[T]) EncodeRaw(v T, out *EncodeBuffer) error {
	return encodeValueCallback(c, v, out, false)
}
func encodeValueCallback[T any](c ValueCodec[T], v T, out *EncodeBuffer, sticky bool) (err error) {
	if c.encode == nil {
		if c.decode == nil && !c.declared {
			return &ConfigError{"invalid value codec"}
		}
		return &ConfigError{"value codec has no encoder"}
	}
	batch, e := out.batch()
	if e != nil {
		return e
	}
	if sticky && batch.failure != nil {
		return batch.failure
	}
	lease := &encodeLease{batch: batch, active: true}
	child := &EncodeBuffer{lease: lease}
	defer func() {
		lease.active = false
		lease.batch = nil
		if recover() != nil {
			err = &ModelError{"value encoder panic"}
			if batch.failure != nil {
				err = batch.failure
			} else if sticky {
				err = encodeFailure(batch, err)
			}
		}
	}()
	err = c.encode(v, child)
	if sticky {
		if batch.failure != nil {
			return batch.failure
		}
		if err != nil {
			return encodeFailure(batch, err)
		}
	}
	return err
}

// DecodeRaw invokes the concrete decoder without adding a value guard. Returned
// validation errors are cooperative and nonsticky unless propagated by a guard.
func (c ValueCodec[T]) DecodeRaw(d *Decoder) (v T, err error) {
	defer func() {
		if p := recover(); p != nil {
			var z T
			v = z
			if d != nil && d.failure != nil {
				err = copyValueDecodeError(d.failure)
			} else {
				err = &ModelError{"value decoder panic"}
			}
		}
	}()
	if d == nil {
		return v, &ConfigError{"nil value decoder"}
	}
	if c.decode == nil {
		if c.encode == nil && !c.declared {
			return v, &ConfigError{"invalid value codec"}
		}
		return v, &ConfigError{"value codec has no decoder"}
	}
	return c.decode(d)
}
func ReadValue[T any](d *Decoder, c ValueCodec[T]) (v T, err error) {
	if d == nil {
		return v, &ConfigError{"nil value decoder"}
	}
	if d.failure != nil {
		return v, copyValueDecodeError(d.failure)
	}
	if c.decode == nil {
		if c.encode == nil && !c.declared {
			return v, &ConfigError{"invalid value codec"}
		}
		return v, &ConfigError{"value codec has no decoder"}
	}
	if d.limits.Work == 0 {
		return v, d.reject(&ModelError{"decode work limit"})
	}
	d.limits.Work--
	if d.depth >= d.limits.Depth {
		return v, d.reject(&ModelError{"decode nesting limit"})
	}
	d.depth++
	defer func() { d.depth-- }()
	v, err = c.DecodeRaw(d)
	if err != nil {
		var z T
		return z, d.reject(err)
	}
	if d.failure != nil {
		var z T
		return z, copyValueDecodeError(d.failure)
	}
	return v, nil
}
func EncodeValue[T any](c ValueCodec[T], v T, maximum uint64) ([]byte, error) {
	encoded := []byte{}
	batch := &encodeBatch{bytes: &encoded, maximum: maximum}
	lease := &encodeLease{batch: batch, active: true}
	defer func() { lease.active = false; lease.batch = nil }()
	out := &EncodeBuffer{lease: lease}
	if e := c.EncodeRaw(v, out); e != nil {
		return nil, e
	}
	if e := FinishValueEncoding(out); e != nil {
		return nil, e
	}
	return encoded, nil
}
func FromValueBytes[T any](c ValueCodec[T], data []byte, l DecodeLimits) (v T, err error) {
	d, e := NewDecoder(data, l)
	if e != nil {
		return v, e
	}
	v, e = ReadValue(d, c)
	if e != nil {
		var z T
		return z, e
	}
	if e = d.Finish(); e != nil {
		var z T
		return z, e
	}
	return v, nil
}

type Unit struct{}
type Uint128 struct{ Lo, Hi uint64 }
type Int128 struct {
	Lo uint64
	Hi int64
}
type Option[T any] struct {
	Value   T
	Present bool
}

func builtinValueCodec[T any](schema string, size uint64, encode func(T, *EncodeBuffer) error, decode func(*Decoder) (T, error)) ValueCodec[T] {
	layout := ValueLayout{size, true}
	return ValueCodec[T]{encode: encode, decode: decode, schema: schema, layout: &layout, builtin: schema}
}
func unsignedValueCodec[T ~uint8 | ~uint16 | ~uint32 | ~uint64](schema string, size int) ValueCodec[T] {
	return builtinValueCodec(schema, uint64(size), func(v T, b *EncodeBuffer) error {
		var raw [8]byte
		binary.LittleEndian.PutUint64(raw[:], uint64(v))
		return b.Append(raw[:size])
	}, func(d *Decoder) (T, error) {
		b, e := d.Take(uint64(size))
		if e != nil {
			return 0, e
		}
		var raw [8]byte
		copy(raw[:], b)
		return T(binary.LittleEndian.Uint64(raw[:])), nil
	})
}
func signedValueCodec[T ~int8 | ~int16 | ~int32 | ~int64](schema string, size int) ValueCodec[T] {
	return builtinValueCodec(schema, uint64(size), func(v T, b *EncodeBuffer) error {
		var raw [8]byte
		binary.LittleEndian.PutUint64(raw[:], uint64(v))
		return b.Append(raw[:size])
	}, func(d *Decoder) (T, error) {
		b, e := d.Take(uint64(size))
		if e != nil {
			return 0, e
		}
		var raw [8]byte
		copy(raw[:], b)
		if b[size-1]&0x80 != 0 {
			for i := size; i < 8; i++ {
				raw[i] = 0xff
			}
		}
		return T(int64(binary.LittleEndian.Uint64(raw[:]))), nil
	})
}
func Uint8Codec() ValueCodec[uint8]   { return unsignedValueCodec[uint8]("u8", 1) }
func Uint16Codec() ValueCodec[uint16] { return unsignedValueCodec[uint16]("u16", 2) }
func Uint32Codec() ValueCodec[uint32] { return unsignedValueCodec[uint32]("u32", 4) }
func Uint64Codec() ValueCodec[uint64] { return unsignedValueCodec[uint64]("u64", 8) }
func Int8Codec() ValueCodec[int8]     { return signedValueCodec[int8]("i8", 1) }
func Int16Codec() ValueCodec[int16]   { return signedValueCodec[int16]("i16", 2) }
func Int32Codec() ValueCodec[int32]   { return signedValueCodec[int32]("i32", 4) }
func Int64Codec() ValueCodec[int64]   { return signedValueCodec[int64]("i64", 8) }
func Uint128Codec() ValueCodec[Uint128] {
	return builtinValueCodec("u128", 16, func(v Uint128, b *EncodeBuffer) error {
		var raw [16]byte
		binary.LittleEndian.PutUint64(raw[:8], v.Lo)
		binary.LittleEndian.PutUint64(raw[8:], v.Hi)
		return b.Append(raw[:])
	}, func(d *Decoder) (Uint128, error) {
		b, e := d.Take(16)
		if e != nil {
			return Uint128{}, e
		}
		return Uint128{binary.LittleEndian.Uint64(b[:8]), binary.LittleEndian.Uint64(b[8:])}, nil
	})
}
func Int128Codec() ValueCodec[Int128] {
	return builtinValueCodec("i128", 16, func(v Int128, b *EncodeBuffer) error {
		var raw [16]byte
		binary.LittleEndian.PutUint64(raw[:8], v.Lo)
		binary.LittleEndian.PutUint64(raw[8:], uint64(v.Hi))
		return b.Append(raw[:])
	}, func(d *Decoder) (Int128, error) {
		b, e := d.Take(16)
		if e != nil {
			return Int128{}, e
		}
		return Int128{binary.LittleEndian.Uint64(b[:8]), int64(binary.LittleEndian.Uint64(b[8:]))}, nil
	})
}
func UnitCodec() ValueCodec[Unit] {
	return builtinValueCodec("()", 0, func(Unit, *EncodeBuffer) error { return nil }, func(*Decoder) (Unit, error) { return Unit{}, nil })
}
func BoolCodec() ValueCodec[bool] {
	return builtinValueCodec("bool", 1, func(v bool, b *EncodeBuffer) error {
		if v {
			return b.Append([]byte{1})
		}
		return b.Append([]byte{0})
	}, func(d *Decoder) (bool, error) {
		v, e := ReadValue(d, Uint8Codec())
		if e != nil {
			return false, e
		}
		if v > 1 {
			return false, &ModelError{"invalid boolean"}
		}
		return v == 1, nil
	})
}
func encodeValueLength(n uint64, width uint8, b *EncodeBuffer) error {
	switch width {
	case 1:
		if n > math.MaxUint8 {
			return &ModelError{"length overflow"}
		}
		return Uint8Codec().EncodeRaw(uint8(n), b)
	case 2:
		if n > math.MaxUint16 {
			return &ModelError{"length overflow"}
		}
		return Uint16Codec().EncodeRaw(uint16(n), b)
	case 4:
		if n > math.MaxUint32 {
			return &ModelError{"length overflow"}
		}
		return Uint32Codec().EncodeRaw(uint32(n), b)
	default:
		return &ModelError{"unsupported length width"}
	}
}
func decodeValueLength(d *Decoder, width uint8) (uint64, error) {
	switch width {
	case 1:
		v, e := ReadValue(d, Uint8Codec())
		return uint64(v), e
	case 2:
		v, e := ReadValue(d, Uint16Codec())
		return uint64(v), e
	case 4:
		v, e := ReadValue(d, Uint32Codec())
		return uint64(v), e
	default:
		return 0, &ModelError{"unsupported length width"}
	}
}
func EncodeStringLength(v string, width uint8, b *EncodeBuffer) error {
	if !utf8.ValidString(v) {
		return &ModelError{"invalid UTF-8"}
	}
	if e := encodeValueLength(uint64(len(v)), width, b); e != nil {
		return e
	}
	batch, e := b.batch()
	if e != nil {
		return e
	}
	if batch.failure != nil {
		return batch.failure
	}
	if uint64(len(v)) > batch.maximum-uint64(len(*batch.bytes)) {
		return encodeFailure(batch, &ModelError{"encoded blob exceeds byte limit"})
	}
	return b.Append([]byte(v))
}
func DecodeStringLength(d *Decoder, width uint8) (string, error) {
	n, e := decodeValueLength(d, width)
	if e != nil {
		return "", e
	}
	if e = d.Collection(n, 1); e != nil {
		return "", e
	}
	d.limits.Work -= n
	b, e := d.Take(n)
	if e != nil {
		return "", e
	}
	if !utf8.Valid(b) {
		return "", &ModelError{"invalid UTF-8"}
	}
	return string(b), nil
}
func StringCodec() ValueCodec[string] { c, _ := StringLengthCodec(4); c.builtin = "String"; return c }
func StringLengthCodec(width uint8) (ValueCodec[string], error) {
	l := ValueLayout{24, true}
	c, e := NewValueCodec("String", func(v string, b *EncodeBuffer) error { return EncodeStringLength(v, width, b) }, func(d *Decoder) (string, error) { return DecodeStringLength(d, width) }, &l)
	c.builtin = "String"
	return c, e
}

// EncodeLengthRaw appends a string with a selected prefix width without poisoning
// returned validation errors. A failed write is still sticky in the sink.
func EncodeLengthRaw(v string, width uint8, out *EncodeBuffer) error {
	return EncodeStringLength(v, width, out)
}
func (c ValueCodec[T]) WithVariantFunc(callback func(T) (string, bool)) ValueCodec[T] {
	c.variantFor = callback
	c.variant = nil
	return c
}
func (c ValueCodec[T]) VariantFor(v T) (label string, present bool, err error) {
	defer func() {
		if recover() != nil {
			label = ""
			present = false
			err = &ModelError{"value variant callback panic"}
		}
	}()
	if c.variantFor != nil {
		label, present = c.variantFor(v)
		return label, present, nil
	}
	label, present = c.Variant()
	return label, present, nil
}

type valueFinishError struct{ cause error }

func (e *valueFinishError) Error() string { return "encoded blob exceeds byte or allocation limit" }
func (e *valueFinishError) Unwrap() error { return e.cause }

// FinishValueEncoding exposes the canonical Rust finish diagnostic without
// changing EncodeBuffer.Finish's established native first-cause contract.
func FinishValueEncoding(out *EncodeBuffer) error {
	batch, e := out.batch()
	if e != nil {
		return e
	}
	if batch.failure != nil {
		return &valueFinishError{batch.failure}
	}
	return nil
}

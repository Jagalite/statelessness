package statelessness

// AdaptFixedArray provides an explicit typed adapter for application [N]T values
// without reflection or unsafe conversion. Conversion callbacks must be pure;
// their lengths are checked rather than silently truncating or padding values.
func AdaptFixedArray[A, T any](n uint64, element ValueCodec[T], toSlice func(A) []T, fromSlice func([]T) (A, error)) (ValueCodec[A], error) {
	if toSlice == nil || fromSlice == nil {
		return ValueCodec[A]{}, &ConfigError{"fixed array requires conversion callbacks"}
	}
	array, e := ArrayOf(n, element)
	if e != nil {
		return ValueCodec[A]{}, e
	}
	var layout *ValueLayout
	if known, ok := array.Layout(); ok {
		layout = &known
	}
	codec, err := NewValueCodec(array.Schema(), func(value A, out *EncodeBuffer) error {
		values := toSlice(value)
		if uint64(len(values)) != n {
			return &ModelError{"fixed array length mismatch"}
		}
		return array.EncodeRaw(values, out)
	}, func(d *Decoder) (value A, err error) {
		values, e := array.DecodeRaw(d)
		if e != nil {
			return value, e
		}
		value, e = fromSlice(values)
		if e != nil {
			var zero A
			return zero, e
		}
		if uint64(len(toSlice(value))) != n {
			var zero A
			return zero, &ModelError{"fixed array length mismatch"}
		}
		return value, nil
	}, layout)
	if err != nil {
		return ValueCodec[A]{}, err
	}
	if array.encode == nil {
		codec.encode = nil
	}
	if array.decode == nil {
		codec.decode = nil
	}
	return codec, nil
}

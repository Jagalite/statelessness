package statelessness

// ModelCodecOptions configures an explicitly attached canonical codec.
type ModelCodecOptions struct {
	Version      uint32
	DecodeLimits func() (DecodeLimits, error)
}

func DefaultModelCodecOptions() ModelCodecOptions { return ModelCodecOptions{Version: 1} }
func BindModelCodec[S, I, O any](metadata func() (Metadata, error), state ValueCodec[S], input ValueCodec[I], output ValueCodec[O], options ModelCodecOptions) (*Codec[S, I, O], error) {
	if metadata == nil || options.Version == 0 || state.encode == nil || state.decode == nil || input.encode == nil || input.decode == nil || output.encode == nil {
		return nil, &ConfigError{"model codec requires metadata, positive version and state/input encode/decode and output encode"}
	}
	gate := func() error {
		m, e := metadata()
		if e != nil {
			return e
		}
		if m.CodecVersion != options.Version {
			return &ModelError{"ModelCodec derive version does not match Model metadata"}
		}
		return nil
	}
	c := &Codec[S, I, O]{Metadata: metadata}
	c.EncodeState = bindLegacyEncode(state, gate)
	c.EncodeInput = bindLegacyEncode(input, gate)
	c.EncodeOutput = bindLegacyEncode(output, gate)
	c.EncodeStateInto = bindEncodeInto(state, gate)
	c.EncodeInputInto = bindEncodeInto(input, gate)
	c.EncodeOutputInto = bindEncodeInto(output, gate)
	c.DecodeState = bindDecode(state, gate, options.DecodeLimits)
	c.DecodeInput = bindDecode(input, gate, options.DecodeLimits)
	return c, nil
}
func bindLegacyEncode[T any](c ValueCodec[T], gate func() error) func(T) ([]byte, error) {
	return func(v T) ([]byte, error) {
		if e := gate(); e != nil {
			return nil, e
		}
		return EncodeValue(c, v, uint64(^uint(0)>>1))
	}
}
func bindEncodeInto[T any](c ValueCodec[T], gate func() error) func(T, *EncodeBuffer) error {
	return func(v T, b *EncodeBuffer) error {
		if e := gate(); e != nil {
			return e
		}
		return c.EncodeRaw(v, b)
	}
}
func bindDecode[T any](c ValueCodec[T], gate func() error, limits func() (DecodeLimits, error)) func([]byte) (T, error) {
	return func(data []byte) (v T, e error) {
		if e = gate(); e != nil {
			return v, e
		}
		l := DefaultDecodeLimits()
		if limits != nil {
			l, e = limits()
			if e != nil {
				return v, e
			}
		}
		return FromValueBytes(c, data, l)
	}
}

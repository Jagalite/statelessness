package statelessness

// LifecycleHistoryCodec preserves the original unversioned legacy wire layout,
// or the indexed v1 layout. Keys use the caller's canonical native value codec.
func LifecycleHistoryCodec[K comparable](m LifecycleMonitor[K], key ValueCodec[K]) (ValueCodec[LifecycleHistory[K]], error) {
	layout, ok := key.Layout()
	if !ok {
		return ValueCodec[LifecycleHistory[K]]{}, &ModelError{"explicit key allocation layout required"}
	}
	weight := layout.Size() + 256
	if weight < layout.Size() {
		return ValueCodec[LifecycleHistory[K]]{}, &ModelError{"key allocation layout overflow"}
	}
	indexed := m.Epoch != nil
	return NewValueCodec("lifecycle-history", func(h LifecycleHistory[K], b *EncodeBuffer) error {
		if !indexed {
			if e := validateLegacyLifecycleFields(h); e != nil {
				return e
			}
		}
		if indexed {
			if e := m.Validate(h); e != nil {
				return e
			}
			if e := Uint8Codec().EncodeRaw(1, b); e != nil {
				return e
			}
		}
		if e := Uint64Codec().EncodeRaw(h.Now, b); e != nil {
			return e
		}
		if indexed {
			if e := Uint64Codec().EncodeRaw(h.RetiredBefore, b); e != nil {
				return e
			}
		}
		count := func(n int) error { return encodeValueLength(uint64(n), 4, b) }
		obligation := func(v LifecycleObligation, full bool) error {
			if e := Uint64Codec().EncodeRaw(v.Deadline, b); e != nil {
				return e
			}
			for _, flag := range []bool{v.Cancelled, v.Settled} {
				if e := BoolCodec().EncodeRaw(flag, b); e != nil {
					return e
				}
			}
			if full {
				return BoolCodec().EncodeRaw(v.Published, b)
			}
			return nil
		}
		if e := count(len(h.Obligations)); e != nil {
			return e
		}
		for _, v := range h.Obligations {
			if e := key.EncodeRaw(v.Key, b); e != nil {
				return e
			}
			if e := obligation(v.Obligation, indexed); e != nil {
				return e
			}
		}
		if e := count(len(h.Resources)); e != nil {
			return e
		}
		for _, v := range h.Resources {
			if e := key.EncodeRaw(v.Key, b); e != nil {
				return e
			}
			if e := BoolCodec().EncodeRaw(v.Acquired, b); e != nil {
				return e
			}
		}
		if e := count(len(h.Violations)); e != nil {
			return e
		}
		for _, v := range h.Violations {
			if !indexed {
				if e := StringCodec().EncodeRaw(func() string {
					if v.Legacy != nil {
						return *v.Legacy
					}
					return legacyLifecycleText(v.Code)
				}(), b); e != nil {
					return e
				}
				continue
			}
			tag := uint8(0)
			if v.Key != nil {
				tag = 1
			}
			if e := Uint8Codec().EncodeRaw(tag, b); e != nil {
				return e
			}
			if v.Key != nil {
				if e := key.EncodeRaw(*v.Key, b); e != nil {
					return e
				}
			}
			if e := StringCodec().EncodeRaw(v.Code, b); e != nil {
				return e
			}
			if e := StringCodec().EncodeRaw(v.Event, b); e != nil {
				return e
			}
			if e := Uint64Codec().EncodeRaw(v.ObservedAt, b); e != nil {
				return e
			}
			tag = 0
			if v.Prior != nil {
				tag = 1
			}
			if e := Uint8Codec().EncodeRaw(tag, b); e != nil {
				return e
			}
			if v.Prior != nil {
				if e := obligation(*v.Prior, true); e != nil {
					return e
				}
			}
		}
		return nil
	}, func(d *Decoder) (LifecycleHistory[K], error) {
		var h LifecycleHistory[K]
		var e error
		if indexed {
			v, e := ReadValue(d, Uint8Codec())
			if e != nil {
				return h, e
			}
			if v != 1 {
				return h, &ModelError{"indexed lifecycle codec version"}
			}
		}
		h.Now, e = ReadValue(d, Uint64Codec())
		if e != nil {
			return h, e
		}
		if indexed {
			h.RetiredBefore, e = ReadValue(d, Uint64Codec())
			if e != nil {
				return h, e
			}
		}
		count := func() (uint32, error) {
			n, e := ReadValue(d, Uint32Codec())
			if e != nil {
				return n, e
			}
			return n, d.Collection(uint64(n), weight)
		}
		obligation := func(full bool) (LifecycleObligation, error) {
			var v LifecycleObligation
			var e error
			v.Deadline, e = ReadValue(d, Uint64Codec())
			if e != nil {
				return v, e
			}
			v.Cancelled, e = ReadValue(d, BoolCodec())
			if e != nil {
				return v, e
			}
			v.Settled, e = ReadValue(d, BoolCodec())
			if e != nil {
				return v, e
			}
			if full {
				v.Published, e = ReadValue(d, BoolCodec())
			}
			return v, e
		}
		n, e := count()
		if e != nil {
			return h, e
		}
		for j := uint32(0); j < n; j++ {
			k, e := ReadValue(d, key)
			if e != nil {
				return h, e
			}
			v, e := obligation(indexed)
			if e != nil {
				return h, e
			}
			h.Obligations = append(h.Obligations, LifecycleEntry[K]{k, v})
		}
		n, e = count()
		if e != nil {
			return h, e
		}
		for j := uint32(0); j < n; j++ {
			k, e := ReadValue(d, key)
			if e != nil {
				return h, e
			}
			v, e := ReadValue(d, BoolCodec())
			if e != nil {
				return h, e
			}
			h.Resources = append(h.Resources, LifecycleResource[K]{k, v})
		}
		n, e = count()
		if e != nil {
			return h, e
		}
		for j := uint32(0); j < n; j++ {
			v := LifecycleViolation[K]{}
			if !indexed {
				v.Code, e = ReadValue(d, StringCodec())
				if e != nil {
					return h, e
				}
				text := v.Code
				v.Legacy = &text
				v.Code = legacyLifecycleCode(v.Code)
				h.Violations = append(h.Violations, v)
				continue
			}
			tag, e := ReadValue(d, Uint8Codec())
			if e != nil {
				return h, e
			}
			if tag > 1 {
				return h, &ModelError{"invalid option tag"}
			}
			if tag == 1 {
				k, e := ReadValue(d, key)
				if e != nil {
					return h, e
				}
				v.Key = &k
			}
			v.Code, e = ReadValue(d, StringCodec())
			if e != nil {
				return h, e
			}
			v.Event, e = ReadValue(d, StringCodec())
			if e != nil {
				return h, e
			}
			v.ObservedAt, e = ReadValue(d, Uint64Codec())
			if e != nil {
				return h, e
			}
			tag, e = ReadValue(d, Uint8Codec())
			if e != nil {
				return h, e
			}
			if tag > 1 {
				return h, &ModelError{"invalid option tag"}
			}
			if tag == 1 {
				o, e := obligation(true)
				if e != nil {
					return h, e
				}
				v.Prior = &o
			}
			h.Violations = append(h.Violations, v)
		}
		return h, m.Validate(h)
	}, nil)
}
func ScopedKeyCodec[K cmpOrdered](key ValueCodec[K]) (ValueCodec[ScopedKey[K]], error) {
	layout, ok := key.Layout()
	if !ok {
		return ValueCodec[ScopedKey[K]]{}, &ModelError{"key layout required"}
	}
	size := layout.Size() + 16
	if size < layout.Size() {
		return ValueCodec[ScopedKey[K]]{}, &ModelError{"key layout overflow"}
	}
	l, e := NewValueLayout(size)
	if e != nil {
		return ValueCodec[ScopedKey[K]]{}, e
	}
	return NewValueCodec("ScopedKey<"+key.Schema()+">", func(k ScopedKey[K], b *EncodeBuffer) error {
		if e := Uint64Codec().EncodeRaw(k.Epoch, b); e != nil {
			return e
		}
		if e := Uint64Codec().EncodeRaw(k.Generation, b); e != nil {
			return e
		}
		return key.EncodeRaw(k.Key, b)
	}, func(d *Decoder) (ScopedKey[K], error) {
		var k ScopedKey[K]
		var e error
		k.Epoch, e = ReadValue(d, Uint64Codec())
		if e != nil {
			return k, e
		}
		k.Generation, e = ReadValue(d, Uint64Codec())
		if e != nil {
			return k, e
		}
		k.Key, e = ReadValue(d, key)
		return k, e
	}, &l)
}

type cmpOrdered interface {
	~int | ~int8 | ~int16 | ~int32 | ~int64 | ~uint | ~uint8 | ~uint16 | ~uint32 | ~uint64 | ~uintptr | ~float32 | ~float64 | ~string
}

func legacyLifecycleText(code string) string {
	if text, ok := legacyLifecycleMessages[code]; ok {
		return text
	}
	return code
}
func legacyLifecycleCode(text string) string {
	for code, v := range legacyLifecycleMessages {
		if v == text {
			return code
		}
	}
	return text
}

var legacyLifecycleMessages = map[string]string{
	"time_backwards":                 "logical time moved backwards",
	"missing_before_advance":         "required settlement missing before time advance",
	"key_reused":                     "correlation key reused",
	"began_after_deadline":           "obligation began after its deadline",
	"cancel_without_obligation":      "cancellation without obligation",
	"settlement_after_cancel":        "settlement after cancellation",
	"duplicate_settlement":           "duplicate settlement",
	"settlement_after_deadline":      "settlement after deadline",
	"settlement_without_obligation":  "settlement without obligation",
	"stale_or_premature_publication": "stale or premature publication",
	"resource_already_acquired":      "resource already acquired",
	"release_before_settlement":      "resource released before settlement",
	"duplicate_release":              "duplicate resource release",
	"release_without_acquisition":    "release without acquisition",
	"missing_in_prior_history":       "required settlement missing in prior history",
	"missing_at_deadline":            "required settlement missing at deadline",
}

// BindLifecycleCodec adds optional history persistence without an ABI dependency.
func BindLifecycleCodec[S, I, O any, K comparable](o Oracle[S, LifecycleHistory[K], I, O], m LifecycleMonitor[K], key ValueCodec[K]) (Oracle[S, LifecycleHistory[K], I, O], error) {
	c, e := LifecycleHistoryCodec(m, key)
	if e != nil {
		return o, e
	}
	o.EncodeHistory = func(h LifecycleHistory[K]) ([]byte, error) { return EncodeValue(c, h, ^uint64(0)) }
	o.DecodeHistory = func(b []byte) (LifecycleHistory[K], error) { return FromValueBytes(c, b, DefaultDecodeLimits()) }
	return o, nil
}

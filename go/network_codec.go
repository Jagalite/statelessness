package statelessness

import "fmt"

func NetworkAddressCodec[T any](value ValueCodec[T]) (ValueCodec[NetworkAddress[T]], error) {
	l, ok := value.Layout()
	if !ok || l.Size() > ^uint64(0)-64 {
		return ValueCodec[NetworkAddress[T]]{}, &ModelError{"payload layout required"}
	}
	layout, _ := NewValueLayout(l.Size() + 64)
	return NewValueCodec("NetworkAddress", func(v NetworkAddress[T], b *EncodeBuffer) error {
		if e := Uint32Codec().EncodeRaw(uint32(v.Machine), b); e != nil {
			return e
		}
		return value.EncodeRaw(v.Value, b)
	}, func(d *Decoder) (NetworkAddress[T], error) {
		id, e := ReadValue(d, Uint32Codec())
		if e != nil {
			return NetworkAddress[T]{}, e
		}
		v, e := ReadValue(d, value)
		return NetworkAddress[T]{MachineID(id), v}, e
	}, &layout)
}
func (n *Network[S, I, O]) BindCodec(state ValueCodec[S], input ValueCodec[I], output ValueCodec[O], wiringMetadata func() (Metadata, error)) error {
	entry, e := NetworkAddressCodec(state)
	if e != nil {
		return e
	}
	entries, e := VectorOf(entry)
	if e != nil {
		return e
	}
	l, ok := input.Layout()
	if !ok || l.Size() > ^uint64(0)-128 {
		return &ModelError{"input allocation layout required"}
	}
	layout, _ := NewValueLayout(l.Size() + 128)
	message, e := NewValueCodec("NetworkMessage", func(m NetworkMessage[I], b *EncodeBuffer) error {
		for _, id := range []uint32{uint32(m.Source), uint32(m.Route.Destination), m.Route.Channel} {
			if e := Uint32Codec().EncodeRaw(id, b); e != nil {
				return e
			}
		}
		return input.EncodeRaw(m.Route.Input, b)
	}, func(d *Decoder) (NetworkMessage[I], error) {
		var m NetworkMessage[I]
		a, e := ReadValue(d, Uint32Codec())
		if e != nil {
			return m, e
		}
		b, e := ReadValue(d, Uint32Codec())
		if e != nil {
			return m, e
		}
		c, e := ReadValue(d, Uint32Codec())
		if e != nil {
			return m, e
		}
		v, e := ReadValue(d, input)
		return NetworkMessage[I]{MachineID(a), NetworkRoute[I]{MachineID(b), c, v}}, e
	}, &layout)
	if e != nil {
		return e
	}
	messages, e := VectorOf(message)
	if e != nil {
		return e
	}
	sc, e := NewValueCodec("NetworkState", func(s NetworkState[S, I], b *EncodeBuffer) error {
		if e := n.validateState(s); e != nil {
			return e
		}
		if e := Uint8Codec().EncodeRaw(1, b); e != nil {
			return e
		}
		if e := entries.EncodeRaw(s.Machines, b); e != nil {
			return e
		}
		return messages.EncodeRaw(s.Pending, b)
	}, func(d *Decoder) (NetworkState[S, I], error) {
		var s NetworkState[S, I]
		v, e := ReadValue(d, Uint8Codec())
		if e != nil {
			return s, e
		}
		if v != 1 {
			return s, &ModelError{"network state codec version"}
		}
		s.Machines, e = ReadValue(d, entries)
		if e != nil {
			return s, e
		}
		s.Pending, e = ReadValue(d, messages)
		if e != nil {
			return s, e
		}
		return s, n.validateState(s)
	}, nil)
	if e != nil {
		return e
	}
	local, e := NetworkAddressCodec(input)
	if e != nil {
		return e
	}
	ic, e := NewValueCodec("NetworkInput", func(i NetworkInput[I], b *EncodeBuffer) error {
		tag, ok := map[string]uint8{"local": 0, "deliver": 1, "drop": 2, "duplicate": 3}[i.Kind]
		if !ok {
			return &ModelError{"network action tag"}
		}
		if e := Uint8Codec().EncodeRaw(tag, b); e != nil {
			return e
		}
		if tag == 0 {
			return local.EncodeRaw(i.Local, b)
		}
		return Uint32Codec().EncodeRaw(i.Index, b)
	}, func(d *Decoder) (NetworkInput[I], error) {
		var i NetworkInput[I]
		tag, e := ReadValue(d, Uint8Codec())
		if e != nil {
			return i, e
		}
		if tag > 3 {
			return i, &ModelError{"network action tag"}
		}
		i.Kind = []string{"local", "deliver", "drop", "duplicate"}[tag]
		if tag == 0 {
			i.Local, e = ReadValue(d, local)
			if e != nil {
				return i, e
			}
			if _, ok := n.machines[i.Local.Machine]; !ok {
				return i, &ModelError{"unknown local machine"}
			}
		} else {
			i.Index, e = ReadValue(d, Uint32Codec())
			if e != nil {
				return i, e
			}
			if tag == 2 && !n.policy.AllowDrop || tag == 3 && !n.policy.AllowDuplicate {
				return i, &ModelError{"network action disabled"}
			}
		}
		return i, nil
	}, nil)
	if e != nil {
		return e
	}
	oc, e := NetworkAddressCodec(output)
	if e != nil {
		return e
	}
	metadata := func() (Metadata, error) {
		names, builds := "", ""
		identity := func(m Metadata) string {
			return fmt.Sprintf("%d:%s/%d/%d/%d", len(m.Name), m.Name, m.ModelVersion, m.PropertiesVersion, m.CodecVersion)
		}
		for _, id := range n.ids {
			child := n.machines[id]
			if child.Codec == nil || child.Codec.Metadata == nil {
				return Metadata{}, &ModelError{"child metadata required"}
			}
			m, e := child.Codec.Metadata()
			if e != nil {
				return m, e
			}
			name := identity(m)
			names += fmt.Sprintf("%d=%d:%s;", id, len(name), name)
			builds += fmt.Sprintf("%d:%s", len(m.Build), m.Build)
		}
		if wiringMetadata == nil {
			return Metadata{}, &ModelError{"wiring metadata required"}
		}
		w, e := wiringMetadata()
		if e != nil {
			return w, e
		}
		name := identity(w)
		names += fmt.Sprintf("w=%d:%s;", len(name), name)
		builds += fmt.Sprintf("%d:%s", len(w.Build), w.Build)
		p := n.policy
		return Identity(fmt.Sprintf("network-v1:%spolicy=%s,%d,%d,%d,%t,%t", names, p.Delivery, p.MaxPending, p.MaxPerChannel, p.MaxRoutesPerOutput, p.AllowDrop, p.AllowDuplicate), builds+";native"), nil
	}
	n.Codec, e = BindModelCodec(metadata, sc, ic, oc, DefaultModelCodecOptions())
	return e
}

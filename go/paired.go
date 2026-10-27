package statelessness

import (
	"encoding/binary"
	"fmt"
)

// Projection defines semantic equality for differing native representations.
type Projection[S, H, O, R, V, E any] struct {
	Metadata        func() (Metadata, error)
	ActualState     func(S) V
	ReferenceState  func(H) V
	ActualEffect    func(O) E
	ReferenceEffect func(R) E
	EqualStates     func(V, V) bool
	EqualEffects    func(E, E) bool
}

func Paired[S, H, I, O, R, V, E any](r Model[H, I, R], p Projection[S, H, O, R, V, E]) (Oracle[S, H, I, O], error) {
	var z Oracle[S, H, I, O]
	if err := r.validate(); err != nil {
		return z, err
	}
	if r.InitialState == nil || r.Step == nil || p.Metadata == nil || p.ActualState == nil || p.ReferenceState == nil || p.ActualEffect == nil || p.ReferenceEffect == nil || p.EqualStates == nil || p.EqualEffects == nil {
		return z, &ModelError{"paired requires reference reducer and complete projection callbacks"}
	}
	o := Oracle[S, H, I, O]{InitialState: r.InitialState, CloneHistory: r.CloneState, EqualHistory: r.EqualStates, EstimatedStateBytes: func(h H) (*uint64, error) { return estimateState(r, h) }}
	o.Advance = func(h H, i I, _ []O, _ Disposition) (H, error) { t, e := perform(r, h, i); return t.State, e }
	compare := func(id string, a, b any, equal bool) Check {
		if equal {
			return Passed(id)
		}
		return Failed(id, fmt.Sprintf("actual=%v; reference=%v", a, b))
	}
	o.CheckStateInto = func(history H, actual S, sink *CheckSink) error {
		if e := appendStateChecks(r, history, sink); e != nil {
			return e
		}
		comparison, e := call("state projection", func() (Check, error) {
			a, b := p.ActualState(actual), p.ReferenceState(r.CloneState(history))
			return compare("pair.state", a, b, p.EqualStates(a, b)), nil
		})
		if e != nil {
			return e
		}
		return sink.Push(comparison)
	}
	o.CheckTransitionInto = func(state OracleState[S, H], input I, t Transition[OracleState[S, H], O], sink *CheckSink) error {
		reference, e := perform(r, state.Oracle, input)
		if e != nil {
			return e
		}
		if e := appendEdgeChecks(r, state.Oracle, input, reference, sink); e != nil {
			return e
		}
		comparison, e := call("effect projection", func() (Check, error) {
			actual := make([]E, len(t.Outputs))
			expected := make([]E, len(reference.Outputs))
			for k, v := range t.Outputs {
				actual[k] = p.ActualEffect(v)
			}
			for k, v := range reference.Outputs {
				expected[k] = p.ReferenceEffect(r.CloneOutput(v))
			}
			equal := len(actual) == len(expected)
			if equal {
				for k := range actual {
					if !p.EqualEffects(actual[k], expected[k]) {
						equal = false
						break
					}
				}
			}
			return compare("pair.effects", actual, expected, equal), nil
		})
		if e != nil {
			return e
		}
		if e := sink.Push(comparison); e != nil {
			return e
		}
		return sink.Push(compare("pair.outcome", t.Disposition, reference.Disposition, t.Disposition == reference.Disposition))
	}
	o.CheckState = func(history H, actual S) ([]Check, error) {
		checks := []Check{}
		e := invokeChecks("paired state check", &checks, func(sink *CheckSink) error { return o.CheckStateInto(history, actual, sink) })
		return checks, e
	}
	o.CheckTransition = func(state OracleState[S, H], input I, t Transition[OracleState[S, H], O]) ([]Check, error) {
		checks := []Check{}
		e := invokeChecks("paired transition check", &checks, func(sink *CheckSink) error { return o.CheckTransitionInto(state, input, t, sink) })
		return checks, e
	}

	if r.Codec != nil && r.Codec.Metadata != nil {
		o.Metadata = func() (Metadata, error) {
			a, e := r.Codec.Metadata()
			if e != nil {
				return Metadata{}, e
			}
			b, e := p.Metadata()
			if e != nil {
				return Metadata{}, e
			}
			if e = a.Validate(); e != nil {
				return Metadata{}, e
			}
			if e = b.Validate(); e != nil {
				return Metadata{}, e
			}
			name := fmt.Sprintf("paired-v1:%d:%s:%d:%d:%d:%d:%s:%d", len(a.Name), a.Name, a.ModelVersion, a.PropertiesVersion, a.CodecVersion, len(b.Name), b.Name, b.ModelVersion)
			return Metadata{name, b.ModelVersion, b.PropertiesVersion, b.CodecVersion, fmt.Sprintf("%d:%s:%d:%s", len(a.Build), a.Build, len(b.Build), b.Build)}, nil
		}
		historyInto := func(history H, buffer *EncodeBuffer) error {
			return appendEncoded(history, r.CloneState, r.Codec.EncodeState, r.Codec.EncodeStateInto, buffer)
		}
		o.EncodeHistoryInto = historyInto
		o.EncodeHistory = func(history H) ([]byte, error) {
			var bytes []byte
			e := WithEncodeBuffer(&bytes, uint64(int(^uint(0)>>1)), func(buffer *EncodeBuffer) error { return historyInto(history, buffer) })
			if e != nil {
				return nil, e
			}
			return bytes, nil
		}
		o.DecodeHistory = r.Codec.DecodeState
	}
	return o, nil
}
func combinedMetadata(a, b Metadata) Metadata {
	return Metadata{fmt.Sprintf("with-oracle-v1:%d:%s:%d:%d:%d:%d:%s:%d:%d:%d", len(a.Name), a.Name, a.ModelVersion, a.PropertiesVersion, a.CodecVersion, len(b.Name), b.Name, b.ModelVersion, b.PropertiesVersion, b.CodecVersion), a.ModelVersion, a.PropertiesVersion, a.CodecVersion, fmt.Sprintf("%d:%s:%d:%s", len(a.Build), a.Build, len(b.Build), b.Build)}
}
func frameOracle(a, b []byte) []byte {
	out := make([]byte, 4+8+len(a)+8+len(b))
	copy(out, "ORC1")
	binary.LittleEndian.PutUint64(out[4:], uint64(len(a)))
	copy(out[12:], a)
	offset := 12 + len(a)
	binary.LittleEndian.PutUint64(out[offset:], uint64(len(b)))
	copy(out[offset+8:], b)
	return out
}
func unframeOracle(data []byte) ([]byte, []byte, error) {
	if len(data) < 4 || string(data[:4]) != "ORC1" {
		return nil, nil, &ModelError{"invalid oracle checkpoint format"}
	}
	offset := 4
	var parts [2][]byte
	for k := range parts {
		if len(data)-offset < 8 {
			return nil, nil, &ModelError{"truncated oracle checkpoint length"}
		}
		size := binary.LittleEndian.Uint64(data[offset:])
		offset += 8
		if size > uint64(len(data)-offset) {
			return nil, nil, &ModelError{"truncated oracle checkpoint component"}
		}
		end := offset + int(size)
		parts[k] = append([]byte{}, data[offset:end]...)
		offset = end
	}
	if offset != len(data) {
		return nil, nil, &ModelError{"trailing oracle checkpoint bytes"}
	}
	return parts[0], parts[1], nil
}

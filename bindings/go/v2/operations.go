//go:build cgo

package v2

/*
#include "bridge.h"
*/
import "C"
import "fmt"

func (s *Session[S, I, O]) copyField(result C.uint64_t, field uint32, index uint64) ([]byte, error) {
	var size C.uint64_t
	code := C.stateless_v2_result_copy(C.uint64_t(s.native.Load()), result, C.uint32_t(field), C.uint64_t(index), nil, 0, &size)
	if code != 0 && code != 18 {
		return nil, statusError(code)
	}
	if uint64(size) > s.options.MaxBytes || uint64(size) > uint64(^uint(0)>>1) {
		return nil, fmt.Errorf("result byte quota exceeded")
	}
	b := make([]byte, int(size))
	if err := statusError(C.stateless_v2_result_copy(C.uint64_t(s.native.Load()), result, C.uint32_t(field), C.uint64_t(index), bytesPtr(b), size, &size)); err != nil {
		return nil, err
	}
	return b, nil
}
func (s *Session[S, I, O]) readInputs(handle C.uint64_t, count uint64) ([]I, error) {
	return guard(func() ([]I, error) {
		if count > s.options.MaxItems || count > uint64(^uint(0)>>1) {
			return nil, fmt.Errorf("input count quota exceeded")
		}
		inputs := make([]I, int(count))
		for i := range inputs {
			var value C.uint64_t
			if e := statusError(C.stateless_v2_result_input(C.uint64_t(s.native.Load()), handle, C.uint64_t(i), &value)); e != nil {
				return nil, e
			}
			v, e := func() (I, error) {
				defer s.releaseValue(value)
				var kind C.uint32_t
				var token C.uint64_t
				if e := statusError(C.stateless_v2_value_host_token(C.uint64_t(s.native.Load()), value, &kind, &token)); e != nil {
					var zero I
					return zero, e
				}
				if kind != 2 {
					var zero I
					return zero, fmt.Errorf("result input kind mismatch")
				}
				return s.input(uint64(token)), nil
			}()
			if e != nil {
				return nil, e
			}
			inputs[i] = v
		}
		return inputs, nil
	})
}
func (s *Session[S, I, O]) readResult(handle C.uint64_t) (result *Result[I], err error) {
	if handle == 0 {
		return nil, nil
	}
	defer func() {
		if err != nil {
			C.stateless_v2_result_release(C.uint64_t(s.native.Load()), handle)
		}
	}()
	result, err = guard(func() (*Result[I], error) {
		r := &Result[I]{}
		scalar := func(field uint32) (uint64, error) {
			var v C.uint64_t
			e := statusError(C.stateless_v2_result_scalar(C.uint64_t(s.native.Load()), handle, C.uint32_t(field), &v))
			return uint64(v), e
		}
		values := make([]uint64, 22)
		for i := uint32(1); i <= 21; i++ {
			v, e := scalar(i)
			if e != nil {
				return nil, e
			}
			values[i] = v
		}
		r.Status = Status(values[1])
		r.Kind = values[2]
		r.Termination = values[3]
		r.States = values[4]
		r.Transitions = values[5]
		r.Depth = values[6]
		r.Skipped = values[7]
		r.EstimatedBytes = values[8]
		r.HasEstimate = values[9] != 0
		r.StateCheckCount = values[13]
		r.StepsVerified = values[14]
		r.BuildMatches = values[15] != 0
		r.FailureReproduced = values[16] != 0
		r.Cases = values[17]
		r.Attempts = values[18]
		r.ValidatedOriginal = values[19] != 0
		r.HasDivergenceStep = values[20] != 0
		r.DivergenceStep = values[21]
		if values[10] > s.options.MaxItems || values[11] > s.options.MaxItems || values[10] > uint64(^uint(0)>>1) || values[11] > uint64(^uint(0)>>1) {
			return nil, fmt.Errorf("result item quota exceeded")
		}
		r.Checks = make([]Check, int(values[10]))
		r.CheckPhases = make([]uint32, len(r.Checks))
		for i := range r.Checks {
			var status, phase C.uint32_t
			if e := statusError(C.stateless_v2_result_check(C.uint64_t(s.native.Load()), handle, C.uint64_t(i), &status, &phase)); e != nil {
				return nil, e
			}
			if status > 2 {
				return nil, fmt.Errorf("unknown result check status")
			}
			id, e := s.copyField(handle, 2, uint64(i))
			if e != nil {
				return nil, e
			}
			details, e := s.copyField(handle, 3, uint64(i))
			if e != nil {
				return nil, e
			}
			r.Checks[i] = Check{string(id), []string{"passed", "failed", "skipped"}[status], string(details)}
			r.CheckPhases[i] = uint32(phase)
		}
		inputs, e := s.readInputs(handle, values[11])
		if e != nil {
			return nil, e
		}
		r.Inputs = inputs
		artifact, e := s.copyField(handle, 1, 0)
		if e != nil {
			return nil, e
		}
		r.Artifact = artifact
		diagnostic, e := s.copyField(handle, 4, 0)
		if e != nil {
			return nil, e
		}
		r.Diagnostic = string(diagnostic)
		field, e := s.copyField(handle, 5, 0)
		if e != nil {
			return nil, e
		}
		r.DivergenceField = string(field)
		r.handle = &resultHandle{value: uint64(handle), session: s.native.Load(), release: func(h uint64) error {
			if s.native.Load() == 0 {
				return nil
			}
			if e := s.enter(); e != nil {
				return e
			}
			defer s.leave()
			return statusError(C.stateless_v2_result_release(C.uint64_t(s.native.Load()), C.uint64_t(h)))
		}}
		return r, nil
	})
	return
}
func (s *Session[S, I, O]) finish(code C.int32_t, handle C.uint64_t) (*Result[I], error) {
	// Capture the original diagnostic before result accessors can overwrite it.
	operationErr := statusError(code)
	r, err := s.readResult(handle)
	if err != nil {
		return nil, err
	}
	return r, operationErr
}
func (s *Session[S, I, O]) Enumerate(bounds Bounds) (*Result[I], error) {
	if err := s.enter(); err != nil {
		return nil, err
	}
	defer s.leave()
	bytes := ^uint64(0)
	if bounds.StateBytes != nil {
		bytes = *bounds.StateBytes
	}
	var out C.uint64_t
	code := C.stateless_v2_enumerate(C.uint64_t(s.native.Load()), C.uint64_t(bounds.States), C.uint64_t(bounds.Transitions), C.uint64_t(bounds.Depth), C.uint64_t(bytes), C.uint64_t(bounds.DurationMillis), &out)
	return s.finish(code, out)
}
func (s *Session[S, I, O]) CheckInitial(state S) (*Result[I], error) {
	if err := s.enter(); err != nil {
		return nil, err
	}
	defer s.leave()
	return guard(func() (*Result[I], error) {
		v, err := s.importValue(1, s.model.CloneState(state))
		if err != nil {
			return nil, err
		}
		defer s.releaseValue(v)
		var out C.uint64_t
		code := C.stateless_v2_check_initial(C.uint64_t(s.native.Load()), v, &out)
		return s.finish(code, out)
	})
}
func (s *Session[S, I, O]) withObserved(before S, input I, t Transition[S, O], call func(C.uint64_t, C.uint64_t, C.uint64_t, []C.uint64_t, uint32, []byte) (*Result[I], error)) (*Result[I], error) {
	return guard(func() (*Result[I], error) {
		if uint64(len(t.Outputs)) > s.options.MaxItems {
			return nil, fmt.Errorf("observed output quota exceeded")
		}
		d, reason, err := disposition(t.Disposition)
		if err != nil {
			return nil, err
		}
		a, err := s.importValue(1, s.model.CloneState(before))
		if err != nil {
			return nil, err
		}
		defer s.releaseValue(a)
		b, err := s.importValue(2, s.model.CloneInput(input))
		if err != nil {
			return nil, err
		}
		defer s.releaseValue(b)
		c, err := s.importValue(1, s.model.CloneState(t.State))
		if err != nil {
			return nil, err
		}
		defer s.releaseValue(c)
		outputs := make([]C.uint64_t, 0, len(t.Outputs))
		defer func() {
			for _, v := range outputs {
				s.releaseValue(v)
			}
		}()
		for _, v := range t.Outputs {
			h, e := s.importValue(3, s.model.CloneOutput(v))
			if e != nil {
				return nil, e
			}
			outputs = append(outputs, h)
		}
		return call(a, b, c, outputs, d, reason)
	})
}
func (s *Session[S, I, O]) CheckObserved(before S, input I, transition Transition[S, O], sequence uint64, policy CheckPolicy) (*Result[I], error) {
	if err := s.enter(); err != nil {
		return nil, err
	}
	defer s.leave()
	return s.withObserved(before, input, transition, func(a, b, c C.uint64_t, outputs []C.uint64_t, d uint32, reason []byte) (*Result[I], error) {
		var out C.uint64_t
		code := C.stateless_v2_check_observed(C.uint64_t(s.native.Load()), a, b, c, intsPtr(outputs), C.uint64_t(len(outputs)), C.uint32_t(d), bytesPtr(reason), C.uint64_t(len(reason)), C.uint64_t(sequence), C.uint64_t(policy.StateEvery), C.uint32_t(boolValue(policy.TransitionChecks)), &out)
		return s.finish(code, out)
	})
}
func (s *Session[S, I, O]) Record(inputs []I, maxSteps uint64) (*Result[I], error) {
	return s.RecordIterator(Values(inputs), maxSteps)
}

// RecordIterator consumes inputs lazily and closes the iterator even on error.
// A late input/codec/model error may return both a coherent prefix and an error.
func (s *Session[S, I, O]) RecordIterator(cursor Iterator[I], maxSteps uint64) (*Result[I], error) {
	if err := s.enter(); err != nil {
		return nil, err
	}
	defer s.leave()
	if cursor.Next == nil {
		return nil, fmt.Errorf("cursor has no Next callback")
	}
	h, err := s.importValue(4, cursor)
	if err != nil {
		return nil, err
	}
	defer s.releaseValue(h)
	var out C.uint64_t
	code := C.stateless_v2_record_cursor(C.uint64_t(s.native.Load()), h, C.uint64_t(maxSteps), &out)
	return s.finish(code, out)
}
func (s *Session[S, I, O]) Replay(artifact []byte, allowBuildMismatch bool) (*Result[I], error) {
	if err := s.enter(); err != nil {
		return nil, err
	}
	defer s.leave()
	var out C.uint64_t
	code := C.stateless_v2_replay(C.uint64_t(s.native.Load()), bytesPtr(artifact), C.uint64_t(len(artifact)), C.uint32_t(boolValue(allowBuildMismatch)), &out)
	return s.finish(code, out)
}
func (s *Session[S, I, O]) Fuzz(config FuzzConfig) (*Result[I], error) {
	if err := s.enter(); err != nil {
		return nil, err
	}
	defer s.leave()
	var out C.uint64_t
	code := C.stateless_v2_fuzz(C.uint64_t(s.native.Load()), C.uint64_t(config.Seed), C.uint64_t(config.Cases), C.uint64_t(config.Steps), C.uint64_t(config.Transitions), C.uint32_t(config.MutationPercent), C.uint64_t(config.DurationMillis), &out)
	return s.finish(code, out)
}
func (s *Session[S, I, O]) Shrink(failure *Result[I], config ShrinkConfig) (*Result[I], error) {
	if err := s.enter(); err != nil {
		return nil, err
	}
	defer s.leave()
	if failure == nil || failure.handle == nil {
		return nil, fmt.Errorf("shrink requires a live failure result")
	}
	h := failure.handle
	if !h.mu.TryLock() {
		return nil, fmt.Errorf("result operation already active")
	}
	defer h.mu.Unlock()
	if h.value == 0 || h.session != s.native.Load() {
		return nil, fmt.Errorf("failure belongs to a closed or different session")
	}
	var out C.uint64_t
	code := C.stateless_v2_shrink(C.uint64_t(s.native.Load()), C.uint64_t(h.value), C.uint64_t(config.Attempts), C.uint64_t(config.Transitions), C.uint64_t(config.DurationMillis), &out)
	r, err := s.finish(code, out)
	if r != nil {
		var count C.uint64_t
		e := statusError(C.stateless_v2_result_scalar(C.uint64_t(s.native.Load()), C.uint64_t(h.value), 11, &count))
		if e != nil {
			C.stateless_v2_result_release(C.uint64_t(s.native.Load()), out)
			return nil, e
		}
		copied, e := s.readInputs(C.uint64_t(h.value), uint64(count))
		if e != nil {
			C.stateless_v2_result_release(C.uint64_t(s.native.Load()), out)
			return nil, e
		}
		r.OriginalInputs = copied
	}
	return r, err
}
func (s *Session[S, I, O]) convert(data []byte, format uint32) ([]byte, error) {
	if err := s.enter(); err != nil {
		return nil, err
	}
	defer s.leave()
	var out C.uint64_t
	code := C.stateless_v2_trace_convert(C.uint64_t(s.native.Load()), C.uint32_t(format), bytesPtr(data), C.uint64_t(len(data)), &out)
	if err := statusError(code); err != nil {
		return nil, err
	}
	defer C.stateless_v2_result_release(C.uint64_t(s.native.Load()), out)
	return s.copyField(out, 1, 0)
}

// NeutralPacket converts an audit binary to its complete structural packet.
func (s *Session[S, I, O]) NeutralPacket(artifact []byte) ([]byte, error) {
	return s.convert(artifact, 1)
}
func (s *Session[S, I, O]) BinaryTrace(packet []byte) ([]byte, error) { return s.convert(packet, 2) }

// Ring stores a bounded window of observed evidence; Observe never invokes Step.
type Ring[S, I, O any] struct {
	session *Session[S, I, O]
	handle  uint64
}

func (s *Session[S, I, O]) NewRing(initial S, maxSteps, maxBytes uint64) (*Ring[S, I, O], error) {
	if err := s.enter(); err != nil {
		return nil, err
	}
	defer s.leave()
	if s.model.Codec == nil {
		return nil, &Error{Status: 17, Message: "ring recording requires codecs"}
	}
	return guard(func() (*Ring[S, I, O], error) {
		v, err := s.importValue(1, s.model.CloneState(initial))
		if err != nil {
			return nil, err
		}
		defer s.releaseValue(v)
		var out C.uint64_t
		if err = statusError(C.stateless_v2_ring_new(C.uint64_t(s.native.Load()), v, C.uint64_t(maxSteps), C.uint64_t(maxBytes), &out)); err != nil {
			return nil, err
		}
		return &Ring[S, I, O]{s, uint64(out)}, nil
	})
}
func (r *Ring[S, I, O]) Observe(before S, input I, t Transition[S, O]) (*Result[I], error) {
	s := r.session
	if err := s.enter(); err != nil {
		return nil, err
	}
	defer s.leave()
	if r.handle == 0 {
		return nil, fmt.Errorf("ring is closed")
	}
	return s.withObserved(before, input, t, func(a, b, c C.uint64_t, outputs []C.uint64_t, d uint32, reason []byte) (*Result[I], error) {
		var out C.uint64_t
		code := C.stateless_v2_ring_observe(C.uint64_t(s.native.Load()), C.uint64_t(r.handle), a, b, c, intsPtr(outputs), C.uint64_t(len(outputs)), C.uint32_t(d), bytesPtr(reason), C.uint64_t(len(reason)), &out)
		return s.finish(code, out)
	})
}
func (r *Ring[S, I, O]) Snapshot() (*Result[I], error) {
	s := r.session
	if err := s.enter(); err != nil {
		return nil, err
	}
	defer s.leave()
	if r.handle == 0 {
		return nil, fmt.Errorf("ring is closed")
	}
	var out C.uint64_t
	code := C.stateless_v2_ring_snapshot(C.uint64_t(s.native.Load()), C.uint64_t(r.handle), &out)
	return s.finish(code, out)
}
func (r *Ring[S, I, O]) Close() error {
	if r == nil {
		return nil
	}
	s := r.session
	if s.native.Load() == 0 {
		r.handle = 0
		return nil
	}
	if err := s.enter(); err != nil {
		return err
	}
	defer s.leave()
	if r.handle == 0 {
		return nil
	}
	err := statusError(C.stateless_v2_ring_release(C.uint64_t(s.native.Load()), C.uint64_t(r.handle)))
	if err == nil {
		r.handle = 0
	}
	return err
}

// RNG uses the engine's SplitMix64 implementation, including full-width bounds.
type RNG struct{ State uint64 }

func (r *RNG) Next() (uint64, error) {
	state := C.uint64_t(r.State)
	var value C.uint64_t
	err := statusError(C.stateless_v2_rng_next(&state, &value))
	if err == nil {
		r.State = uint64(state)
	}
	return uint64(value), err
}
func (r *RNG) Index(upper uint64) (uint64, bool, error) {
	state := C.uint64_t(r.State)
	var value C.uint64_t
	var present C.uint32_t
	err := statusError(C.stateless_v2_rng_index(&state, C.uint64_t(upper), &present, &value))
	if err == nil {
		r.State = uint64(state)
	}
	return uint64(value), present != 0, err
}

// RecordIteratorWithLimits applies independent encoded blob, aggregate item and
// payload limits inside the same Rust recording loop. Late errors retain only
// the coherent prefix and return both that result and an error.
func (s *Session[S, I, O]) RecordIteratorWithLimits(cursor Iterator[I], maxSteps, maxBlobBytes, maxItems, maxPayloadBytes uint64) (*Result[I], error) {
	if err := s.enter(); err != nil {
		return nil, err
	}
	defer s.leave()
	if cursor.Next == nil {
		return nil, fmt.Errorf("cursor has no Next callback")
	}
	h, err := s.importValue(4, cursor)
	if err != nil {
		return nil, err
	}
	defer s.releaseValue(h)
	var out C.uint64_t
	code := C.stateless_v2_record_cursor_limited(C.uint64_t(s.native.Load()), h, C.uint64_t(maxSteps), C.uint64_t(maxBlobBytes), C.uint64_t(maxItems), C.uint64_t(maxPayloadBytes), &out)
	return s.finish(code, out)
}

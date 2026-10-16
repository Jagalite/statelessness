//go:build cgo

package v2

/*
#cgo !stateless_jobs LDFLAGS: -lstateless
#cgo stateless_jobs LDFLAGS: -lstateless_go_jobs
#cgo linux LDFLAGS: -lpthread -ldl -lm
#include "bridge.h"
*/
import "C"
import (
	"errors"
	"fmt"
	"runtime"
	"runtime/cgo"
	"sync"
	"sync/atomic"
	"unsafe"
)

type rootValue struct {
	kind  uint32
	value any
}
type callbackHost interface {
	dispatch(uint64, uint32, []uint64, []byte) error
	release(uint32, uint64)
	diagnosticLimit() uint64
}

// Session is scoped to WithSession. Methods are synchronous and may only be
// called by its owning goroutine; Cancel is the sole cross-goroutine method.
type Session[S, I, O any] struct {
	model        Model[S, I, O]
	options      Options
	native       atomic.Uint64
	busy         sync.Mutex
	roots        map[uint64]rootValue
	nextToken    uint64
	cleanupError error
}

func (s *Session[S, I, O]) diagnosticLimit() uint64 { return s.options.MaxBytes }

func bytesPtr(data []byte) *C.uint8_t {
	if len(data) == 0 {
		return nil
	}
	return (*C.uint8_t)(unsafe.Pointer(&data[0]))
}
func intsPtr(data []C.uint64_t) *C.uint64_t {
	if len(data) == 0 {
		return nil
	}
	return &data[0]
}
func statusError(status C.int32_t) error {
	if status >= 0 && status <= 3 {
		return nil
	}
	var s C.int32_t
	var op, phase C.uint32_t
	var seq, n C.uint64_t
	C.stateless_v2_last_error(&s, &op, &phase, &seq, nil, 0, &n)
	message := "engine operation failed"
	if uint64(n) <= 64*1024*1024 {
		data := make([]byte, int(n))
		C.stateless_v2_last_error(&s, &op, &phase, &seq, bytesPtr(data), C.uint64_t(len(data)), &n)
		message = string(data)
	}
	return &Error{Status: Status(status), Operation: uint32(op), Phase: uint32(phase), Sequence: uint64(seq), Message: message}
}
func (s *Session[S, I, O]) enter() error {
	if !s.busy.TryLock() {
		return fmt.Errorf("session operation already active")
	}
	id := s.native.Load()
	if id == 0 {
		s.busy.Unlock()
		return fmt.Errorf("session is closed")
	}
	var caps C.uint64_t
	if err := statusError(C.stateless_v2_session_capabilities(C.uint64_t(id), &caps)); err != nil {
		s.busy.Unlock()
		return err
	}
	return nil
}
func (s *Session[S, I, O]) leave() { s.busy.Unlock() }
func validateModel[S, I, O any](m Model[S, I, O], o Options) (uint64, error) {
	if m.InitialState == nil || m.Step == nil || m.CheckState == nil || m.CloneState == nil || m.CloneInput == nil || m.CloneOutput == nil {
		return 0, fmt.Errorf("initial state, step, state checks, and all three snapshot callbacks are required")
	}
	if o.MaxValues == 0 || o.MaxItems == 0 || o.MaxBytes == 0 {
		return 0, fmt.Errorf("session quotas must be nonzero")
	}
	if err := validText(m.Metadata.Name); err != nil {
		return 0, err
	}
	if err := validText(m.Metadata.Build); err != nil {
		return 0, err
	}
	caps := uint64(1)
	if m.Inputs != nil {
		if m.EqualStates == nil {
			return 0, fmt.Errorf("enumeration requires semantic state equality")
		}
		caps |= 2
	}
	if m.Codec != nil {
		c := m.Codec
		if c.EncodeState == nil || c.DecodeState == nil || c.EncodeInput == nil || c.DecodeInput == nil || c.EncodeOutput == nil {
			return 0, fmt.Errorf("codec must provide every callback")
		}
		caps |= 4 | 128
	}
	if m.Generate != nil || m.Automatic {
		if m.EqualInputs == nil {
			return 0, fmt.Errorf("generation requires semantic input equality")
		}
		caps |= 8 | 16
	}
	if m.Automatic {
		if m.Inputs == nil {
			return 0, fmt.Errorf("automatic generation requires finite input enumeration")
		}
		if m.Generate != nil {
			return 0, fmt.Errorf("choose automatic generation or an explicit generator")
		}
		caps |= 256
	}
	if m.HashState != nil {
		caps |= 32
	}
	if m.EstimatedStateBytes != nil {
		caps |= 64
	}
	return caps, nil
}
func WithSession[S, I, O any](model Model[S, I, O], options Options, fn func(*Session[S, I, O]) error) (err error) {
	caps, err := validateModel(model, options)
	if err != nil {
		return err
	}
	if fn == nil {
		return fmt.Errorf("nil session function")
	}
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	s := &Session[S, I, O]{model: model, options: options, roots: make(map[uint64]rootValue)}
	context := cgo.NewHandle(callbackHost(s))
	defer context.Delete()
	name, build := []byte(model.Metadata.Name), []byte(model.Metadata.Build)
	var id C.uint64_t
	err = statusError(C.go_v2_new(C.uint64_t(context), C.uint64_t(caps), C.uint64_t(options.MaxValues), C.uint64_t(options.MaxItems), C.uint64_t(options.MaxBytes), bytesPtr(name), C.uint64_t(len(name)), bytesPtr(build), C.uint64_t(len(build)), C.uint32_t(model.Metadata.ModelVersion), C.uint32_t(model.Metadata.PropertiesVersion), C.uint32_t(model.Metadata.CodecVersion), &id))
	if err != nil {
		return err
	}
	s.native.Store(uint64(id))
	defer func() {
		closeErr := statusError(C.stateless_v2_session_close(id))
		s.native.Store(0)
		err = errors.Join(err, closeErr, s.cleanupError)
	}()
	return fn(s)
}

// Cancel may be called from another goroutine while a synchronous run is active.
func (s *Session[S, I, O]) Cancel() error {
	id := s.native.Load()
	if id == 0 {
		return fmt.Errorf("session is closed")
	}
	return statusError(C.stateless_v2_cancel(C.uint64_t(id)))
}
func (s *Session[S, I, O]) ResetCancellation() error {
	if err := s.enter(); err != nil {
		return err
	}
	defer s.leave()
	return statusError(C.stateless_v2_cancel_reset(C.uint64_t(s.native.Load())))
}
func (s *Session[S, I, O]) newRoot(kind uint32, value any) (uint64, error) {
	if uint64(len(s.roots)) >= s.options.MaxValues {
		return 0, fmt.Errorf("host root quota exceeded")
	}
	if s.nextToken == ^uint64(0) {
		return 0, fmt.Errorf("host token space exhausted")
	}
	s.nextToken++
	s.roots[s.nextToken] = rootValue{kind, value}
	return s.nextToken, nil
}
func (s *Session[S, I, O]) release(kind uint32, token uint64) {
	root, ok := s.roots[token]
	if !ok {
		return
	}
	delete(s.roots, token)
	if root.kind != kind {
		s.cleanupError = errors.Join(s.cleanupError, fmt.Errorf("released host value kind mismatch"))
		return
	}
	if kind == 4 {
		_, err := guard(func() (struct{}, error) {
			cursor := root.value.(Iterator[I])
			if cursor.Close != nil {
				return struct{}{}, cursor.Close()
			}
			return struct{}{}, nil
		})
		s.cleanupError = errors.Join(s.cleanupError, err)
	}
}
func (s *Session[S, I, O]) root(token uint64, kind uint32) (any, error) {
	v, ok := s.roots[token]
	if !ok || v.kind != kind {
		return nil, fmt.Errorf("unknown host value or kind")
	}
	return v.value, nil
}
func (s *Session[S, I, O]) state(token uint64) S {
	v, err := s.root(token, 1)
	if err != nil {
		panic(err)
	}
	return s.model.CloneState(v.(S))
}
func (s *Session[S, I, O]) input(token uint64) I {
	v, err := s.root(token, 2)
	if err != nil {
		panic(err)
	}
	return s.model.CloneInput(v.(I))
}
func (s *Session[S, I, O]) output(token uint64) O {
	v, err := s.root(token, 3)
	if err != nil {
		panic(err)
	}
	return s.model.CloneOutput(v.(O))
}
func (s *Session[S, I, O]) publish(inv uint64, kind uint32, value any) error {
	token, err := s.newRoot(kind, value)
	if err != nil {
		if kind == 4 {
			cursor := value.(Iterator[I])
			if cursor.Close != nil {
				_, closeErr := guard(func() (struct{}, error) { return struct{}{}, cursor.Close() })
				s.cleanupError = errors.Join(s.cleanupError, closeErr)
			}
		}
		return err
	}
	code := C.stateless_v2_callback_value(C.uint64_t(s.native.Load()), C.uint64_t(inv), C.uint32_t(kind), C.uint64_t(token))
	if code != 0 {
		s.release(kind, token)
		return fmt.Errorf("callback value rejected with status %d", code)
	}
	return nil
}
func (s *Session[S, I, O]) importValue(kind uint32, value any) (C.uint64_t, error) {
	token, err := s.newRoot(kind, value)
	if err != nil {
		if kind == 4 {
			cursor := value.(Iterator[I])
			if cursor.Close != nil {
				_, closeErr := guard(func() (struct{}, error) { return struct{}{}, cursor.Close() })
				s.cleanupError = errors.Join(s.cleanupError, closeErr)
			}
		}
		return 0, err
	}
	var out C.uint64_t
	err = statusError(C.stateless_v2_value_import(C.uint64_t(s.native.Load()), C.uint32_t(kind), C.uint64_t(token), &out))
	if err != nil {
		s.release(kind, token)
	}
	return out, err
}
func (s *Session[S, I, O]) releaseValue(value C.uint64_t) {
	if value != 0 {
		if err := statusError(C.stateless_v2_value_release(C.uint64_t(s.native.Load()), value)); err != nil {
			s.cleanupError = errors.Join(s.cleanupError, err)
		}
	}
}
func (s *Session[S, I, O]) scalar(inv, value uint64) error {
	code := C.stateless_v2_callback_scalar(C.uint64_t(s.native.Load()), C.uint64_t(inv), C.uint64_t(value))
	if code != 0 {
		return fmt.Errorf("callback scalar rejected: %d", code)
	}
	return nil
}
func boolValue(b bool) uint64 {
	if b {
		return 1
	}
	return 0
}
func disposition(d Disposition) (uint32, []byte, error) {
	if err := validText(d.Reason); err != nil {
		return 0, nil, err
	}
	switch d.Kind {
	case "", "accepted":
		if d.Reason != "" {
			return 0, nil, fmt.Errorf("accepted disposition requires empty reason")
		}
		return 0, nil, nil
	case "rejected":
		return 1, []byte(d.Reason), nil
	case "ignored":
		return 2, []byte(d.Reason), nil
	default:
		return 0, nil, fmt.Errorf("unknown disposition %q", d.Kind)
	}
}
func (s *Session[S, I, O]) checks(inv uint64, checks []Check) error {
	if uint64(len(checks)) > s.options.MaxItems {
		return fmt.Errorf("check quota exceeded")
	}
	for _, check := range checks {
		if err := validText(check.ID); err != nil {
			return err
		}
		if err := validText(check.Details); err != nil {
			return err
		}
		var code uint32
		switch check.Status {
		case "passed":
			code = 0
		case "failed":
			code = 1
		case "skipped":
			code = 2
		default:
			return fmt.Errorf("unknown check status")
		}
		id, details := []byte(check.ID), []byte(check.Details)
		c := C.stateless_v2_callback_check(C.uint64_t(s.native.Load()), C.uint64_t(inv), C.uint32_t(code), bytesPtr(id), C.uint64_t(len(id)), bytesPtr(details), C.uint64_t(len(details)))
		if c != 0 {
			return fmt.Errorf("callback check rejected: %d", c)
		}
	}
	return nil
}
func (s *Session[S, I, O]) dispatch(inv uint64, op uint32, args []uint64, data []byte) error {
	need := func(n int) error {
		if len(args) != n {
			return fmt.Errorf("operation %d requires %d arguments", op, n)
		}
		return nil
	}
	switch op {
	case 1:
		v, err := s.model.InitialState()
		if err != nil {
			return err
		}
		return s.publish(inv, 1, s.model.CloneState(v))
	case 2:
		if err := need(2); err != nil {
			return err
		}
		t, err := s.model.Step(s.state(args[0]), s.input(args[1]))
		if err != nil {
			return err
		}
		if uint64(len(t.Outputs)) > s.options.MaxItems {
			return fmt.Errorf("output quota exceeded")
		}
		if err = s.publish(inv, 1, s.model.CloneState(t.State)); err != nil {
			return err
		}
		for _, output := range t.Outputs {
			if err = s.publish(inv, 3, s.model.CloneOutput(output)); err != nil {
				return err
			}
		}
		d, reason, err := disposition(t.Disposition)
		if err != nil {
			return err
		}
		code := C.stateless_v2_callback_disposition(C.uint64_t(s.native.Load()), C.uint64_t(inv), C.uint32_t(d), bytesPtr(reason), C.uint64_t(len(reason)))
		if code != 0 {
			return fmt.Errorf("callback disposition rejected: %d", code)
		}
		return nil
	case 3:
		if err := need(1); err != nil {
			return err
		}
		checks, err := s.model.CheckState(s.state(args[0]))
		if err != nil {
			return err
		}
		return s.checks(inv, checks)
	case 4:
		if len(args) < 4 {
			return fmt.Errorf("transition check missing arguments")
		}
		if s.model.CheckTransition == nil {
			return nil
		}
		kinds := []string{"accepted", "rejected", "ignored"}
		if args[3] > 2 {
			return fmt.Errorf("invalid disposition")
		}
		outputs := make([]O, len(args)-4)
		for i, t := range args[4:] {
			outputs[i] = s.output(t)
		}
		t := Transition[S, O]{s.state(args[2]), outputs, Disposition{kinds[args[3]], string(data)}}
		checks, err := s.model.CheckTransition(s.state(args[0]), s.input(args[1]), t)
		if err != nil {
			return err
		}
		return s.checks(inv, checks)
	case 5:
		if err := need(2); err != nil {
			return err
		}
		v, err := s.model.EqualStates(s.state(args[0]), s.state(args[1]))
		if err != nil {
			return err
		}
		return s.scalar(inv, boolValue(v))
	case 6:
		if err := need(1); err != nil {
			return err
		}
		v, err := s.model.HashState(s.state(args[0]))
		if err != nil {
			return err
		}
		return s.scalar(inv, v)
	case 7:
		if err := need(1); err != nil {
			return err
		}
		v, ok, err := s.model.EstimatedStateBytes(s.state(args[0]))
		if err != nil {
			return err
		}
		if !ok {
			return nil
		}
		return s.scalar(inv, v)
	case 8, 18:
		if err := need(1); err != nil {
			return err
		}
		var cursor Iterator[I]
		var err error
		if op == 8 {
			cursor, err = s.model.Inputs(s.state(args[0]))
		} else if s.model.SimplerInputs == nil {
			cursor = Values([]I(nil))
		} else {
			cursor, err = s.model.SimplerInputs(s.input(args[0]))
		}
		if err != nil {
			return err
		}
		if cursor.Next == nil {
			if cursor.Close != nil {
				_, _ = guard(func() (struct{}, error) { return struct{}{}, cursor.Close() })
			}
			return fmt.Errorf("cursor has no Next callback")
		}
		return s.publish(inv, 4, cursor)
	case 9:
		if err := need(1); err != nil {
			return err
		}
		v, err := s.root(args[0], 4)
		if err != nil {
			return err
		}
		input, ok, err := v.(Iterator[I]).Next()
		if err != nil {
			return err
		}
		if !ok {
			return nil
		}
		return s.publish(inv, 2, s.model.CloneInput(input))
	case 10, 12, 14:
		if err := need(1); err != nil {
			return err
		}
		var b []byte
		var err error
		switch op {
		case 10:
			b, err = s.model.Codec.EncodeState(s.state(args[0]))
		case 12:
			b, err = s.model.Codec.EncodeInput(s.input(args[0]))
		case 14:
			b, err = s.model.Codec.EncodeOutput(s.output(args[0]))
		}
		if err != nil {
			return err
		}
		code := C.stateless_v2_callback_bytes(C.uint64_t(s.native.Load()), C.uint64_t(inv), bytesPtr(b), C.uint64_t(len(b)))
		if code != 0 {
			return fmt.Errorf("callback bytes rejected: %d", code)
		}
		return nil
	case 11:
		v, err := s.model.Codec.DecodeState(data)
		if err != nil {
			return err
		}
		return s.publish(inv, 1, s.model.CloneState(v))
	case 13:
		v, err := s.model.Codec.DecodeInput(data)
		if err != nil {
			return err
		}
		return s.publish(inv, 2, s.model.CloneInput(v))
	case 15:
		if err := need(1); err != nil {
			return err
		}
		rng := &CallbackRNG{}
		rng.next = func() (uint64, error) {
			var v C.uint64_t
			code := C.stateless_v2_callback_rng(C.uint64_t(s.native.Load()), C.uint64_t(inv), &v)
			if code != 0 {
				return 0, fmt.Errorf("callback RNG rejected: %d", code)
			}
			return uint64(v), nil
		}
		rng.index = func(upper uint64) (uint64, bool, error) {
			var v C.uint64_t
			var present C.uint32_t
			code := C.stateless_v2_callback_rng_index(C.uint64_t(s.native.Load()), C.uint64_t(inv), C.uint64_t(upper), &present, &v)
			if code != 0 {
				return 0, false, fmt.Errorf("callback RNG rejected: %d", code)
			}
			return uint64(v), present != 0, nil
		}
		defer func() { rng.mu.Lock(); rng.next = nil; rng.index = nil; rng.mu.Unlock() }()
		v, ok, err := s.model.Generate(s.state(args[0]), rng)
		if err != nil {
			return err
		}
		if !ok {
			return nil
		}
		return s.publish(inv, 2, s.model.CloneInput(v))
	case 16:
		if err := need(2); err != nil {
			return err
		}
		if s.model.IsEnabled == nil {
			return s.scalar(inv, 1)
		}
		v, err := s.model.IsEnabled(s.state(args[0]), s.input(args[1]))
		if err != nil {
			return err
		}
		return s.scalar(inv, boolValue(v))
	case 17:
		if err := need(2); err != nil {
			return err
		}
		v, err := s.model.EqualInputs(s.input(args[0]), s.input(args[1]))
		if err != nil {
			return err
		}
		return s.scalar(inv, boolValue(v))
	default:
		return fmt.Errorf("unsupported operation %d", op)
	}
}

//export goV2Dispatch
func goV2Dispatch(context, session, inv C.uint64_t, op C.uint32_t, args *C.uint64_t, n C.uint32_t, data *C.uint8_t, size C.uint64_t) (status C.int32_t) {
	// This outer guard includes diagnostic construction and publication, not just
	// model dispatch. No Go panic may unwind through the exported C callback.
	status = 11
	defer func() {
		if recover() != nil {
			status = 11
		}
	}()
	limit := uint64(65536)
	_, err := guard(func() (struct{}, error) {
		if uint64(size) > uint64(^uint(0)>>1) {
			return struct{}{}, fmt.Errorf("callback payload too large")
		}
		host := cgo.Handle(context).Value().(callbackHost)
		limit = host.diagnosticLimit()
		raw := unsafe.Slice(args, int(n))
		a := make([]uint64, len(raw))
		for i, v := range raw {
			a[i] = uint64(v)
		}
		b := append([]byte(nil), unsafe.Slice((*byte)(unsafe.Pointer(data)), int(size))...)
		return struct{}{}, host.dispatch(uint64(inv), uint32(op), a, b)
	})
	if err != nil {
		message := callbackDiagnostic(err, limit)
		C.stateless_v2_callback_error(session, inv, bytesPtr(message), C.uint64_t(len(message)))
		return 11
	}
	return 0
}

//export goV2Release
func goV2Release(context C.uint64_t, kind C.uint32_t, token C.uint64_t) {
	// Rust release must never unwind, including a stale or malformed context.
	defer func() { _ = recover() }()
	cgo.Handle(context).Value().(callbackHost).release(uint32(kind), uint64(token))
}

// Package stateless provides synchronous native-engine testing sessions.
package stateless

/*
#cgo !stateless_jobs LDFLAGS: -lstateless
#cgo stateless_jobs LDFLAGS: -lstateless_go_jobs
#cgo linux LDFLAGS: -lpthread -ldl -lm
#include "bridge.h"
*/
import "C"
import (
	"fmt"
	"runtime"
	"runtime/cgo"
	"sync"
	"unsafe"
)

type Metadata struct {
	Name, Build                                   string
	ModelVersion, PropertiesVersion, CodecVersion uint32
}
type Disposition byte

const (
	Accepted Disposition = iota
	Rejected
	Ignored
)

type CheckStatus byte

const (
	Passed CheckStatus = iota
	Failed
	Skipped
)

type Transition struct {
	State       []byte
	Effects     [][]byte
	Disposition Disposition
	Reason      string
}
type Check struct {
	ID      string
	Status  CheckStatus
	Details string
}
type Model interface {
	Metadata() Metadata
	Initial() ([]byte, error)
	Step([]byte, []byte) (Transition, error)
	CheckState([]byte) ([]Check, error)
	CheckTransition([]byte, []byte, Transition) ([]Check, error)
	Inputs([]byte) ([][]byte, error)
}
type Status int

const (
	OK Status = iota
	PropertyFailed
	Diverged
	Incompatible
)

type Session struct {
	model         Model
	native        *C.StatelessModel
	ctx           unsafe.Pointer
	handle        cgo.Handle
	callbackError error
	mu            sync.Mutex
	closed        bool
}

// WithSession owns all resources on one locked OS thread. Do not retain s or
// invoke it concurrently/from callbacks. Cleanup runs even when fn panics.
func WithSession(m Model, fn func(*Session) error) error {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	s := &Session{model: m}
	s.handle = cgo.NewHandle(s)
	defer s.handle.Delete()
	s.ctx = C.go_context_new(C.uintptr_t(s.handle))
	if s.ctx == nil {
		return fmt.Errorf("allocate callback context")
	}
	defer C.free(s.ctx)
	md := m.Metadata()
	n, b := []byte(md.Name), []byte(md.Build)
	var native *C.StatelessModel
	status := C.go_model_new(s.ctx, ptr(n), C.size_t(len(n)), ptr(b), C.size_t(len(b)), C.uint32_t(md.ModelVersion), C.uint32_t(md.PropertiesVersion), C.uint32_t(md.CodecVersion), &native)
	s.native = native
	if status != 0 {
		return s.err(status)
	}
	defer func() {
		s.mu.Lock()
		defer s.mu.Unlock()
		s.closed = true
		C.stateless_model_free(s.native)
	}()
	return fn(s)
}
func ptr(b []byte) *C.uint8_t {
	if len(b) == 0 {
		return nil
	}
	return (*C.uint8_t)(unsafe.Pointer(&b[0]))
}
func buffer() *C.StatelessBuffer { return C.stateless_buffer_new(0) }
func copyBuffer(b *C.StatelessBuffer) []byte {
	return C.GoBytes(unsafe.Pointer(C.stateless_buffer_data(b)), C.int(C.stateless_buffer_len(b)))
}
func (s *Session) err(code C.int32_t) error {
	b := buffer()
	if b == nil {
		return fmt.Errorf("native status %d (error allocation failed)", code)
	}
	defer C.stateless_buffer_free(b)
	C.stateless_last_error(b)
	return fmt.Errorf("native status %d: %s; callback: %v", code, copyBuffer(b), s.callbackError)
}
func (s *Session) enter() error {
	if !s.mu.TryLock() {
		return fmt.Errorf("session concurrent or reentrant")
	}
	if s.closed || s.ctx == nil || s.native == nil {
		s.mu.Unlock()
		return fmt.Errorf("session closed or uninitialized")
	}
	if C.go_context_is_owner(s.ctx) == 0 {
		s.mu.Unlock()
		return fmt.Errorf("session used from a different OS thread; use the WithSession goroutine")
	}
	s.callbackError = nil
	return nil
}
func (s *Session) Record(inputs [][]byte, maxSteps uint) ([]byte, Status, error) {
	if e := s.enter(); e != nil {
		return nil, 0, e
	}
	defer s.mu.Unlock()
	p, e := batch(inputs)
	if e != nil {
		return nil, 0, e
	}
	b := buffer()
	if b == nil {
		return nil, 0, fmt.Errorf("buffer allocation failed")
	}
	defer C.stateless_buffer_free(b)
	c := C.stateless_record(s.native, ptr(p), C.size_t(len(p)), C.size_t(maxSteps), b)
	if c > 1 {
		return nil, Status(c), s.err(c)
	}
	return copyBuffer(b), Status(c), nil
}
func (s *Session) Replay(artifact []byte) (Status, error) {
	if e := s.enter(); e != nil {
		return 0, e
	}
	defer s.mu.Unlock()
	c := C.stateless_replay(s.native, ptr(artifact), C.size_t(len(artifact)))
	if c != 0 && c != 1 {
		return Status(c), s.err(c)
	}
	return Status(c), nil
}

type Bounds struct {
	States      uint
	Transitions uint64
	Depth       uint
}

func (s *Session) Enumerate(l Bounds) (string, []byte, Status, error) {
	if e := s.enter(); e != nil {
		return "", nil, 0, e
	}
	defer s.mu.Unlock()
	r, a := buffer(), buffer()
	defer C.stateless_buffer_free(r)
	defer C.stateless_buffer_free(a)
	if r == nil || a == nil {
		return "", nil, 0, fmt.Errorf("buffer allocation failed")
	}
	c := C.stateless_enumerate(s.native, C.size_t(l.States), C.uint64_t(l.Transitions), C.size_t(l.Depth), r, a)
	if c > 1 {
		return "", nil, Status(c), s.err(c)
	}
	return string(copyBuffer(r)), copyBuffer(a), Status(c), nil
}

// WithNative borrows the handle for a synchronous opt-in Rust bridge. The
// bridge must obey ABI ownership and thread confinement and never retain it.
func (s *Session) WithNative(fn func(unsafe.Pointer) error) error {
	if e := s.enter(); e != nil {
		return e
	}
	defer s.mu.Unlock()
	err := fn(unsafe.Pointer(s.native))
	if s.callbackError != nil {
		return fmt.Errorf("native bridge: %v; callback: %w", err, s.callbackError)
	}
	return err
}

//export goDispatch
func goDispatch(h C.uintptr_t, op C.uint32_t, sp *C.uint8_t, sn C.size_t, ip *C.uint8_t, in C.size_t, response *C.StatelessBuffer) (status C.int32_t) {
	s := cgo.Handle(h).Value().(*Session)
	defer func() {
		if p := recover(); p != nil {
			s.callbackError = fmt.Errorf("callback operation %d panic: %v", op, p)
			status = 11
		}
	}()
	state, input := C.GoBytes(unsafe.Pointer(sp), C.int(sn)), C.GoBytes(unsafe.Pointer(ip), C.int(in))
	var out []byte
	var err error
	switch op {
	case 0:
		out, err = s.model.Initial()
	case 1:
		var t Transition
		t, err = s.model.Step(state, input)
		if err == nil {
			out, err = transitionPacket(t)
		}
	case 2:
		var c []Check
		c, err = s.model.CheckState(state)
		if err == nil {
			out, err = checksPacket(c)
		}
	case 3:
		var event []byte
		var t Transition
		event, t, err = decodeCheckInput(input)
		if err == nil {
			var c []Check
			c, err = s.model.CheckTransition(state, event, t)
			if err == nil {
				out, err = checksPacket(c)
			}
		}
	case 4:
		var i [][]byte
		i, err = s.model.Inputs(state)
		if err == nil {
			out, err = batch(i)
		}
	default:
		err = fmt.Errorf("unknown operation %d", op)
	}
	if err != nil {
		s.callbackError = fmt.Errorf("callback operation %d: %w", op, err)
		return 11
	}
	if len(out) > maxBytes {
		s.callbackError = fmt.Errorf("callback operation %d: response exceeds 64 MiB", op)
		return 11
	}
	status = C.stateless_buffer_assign(response, ptr(out), C.size_t(len(out)))
	if status != 0 {
		s.callbackError = fmt.Errorf("callback operation %d: response assignment status %d", op, status)
	}
	return status
}

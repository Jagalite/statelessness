package stateless

import (
	"fmt"
	"strings"
	"testing"
	"unsafe"
)

type counter struct {
	build   string
	panicOp bool
	fail    bool
	bad     bool
}

func (m counter) Metadata() Metadata {
	return Metadata{Name: "counter-v1", Build: m.build, ModelVersion: 1, PropertiesVersion: 1, CodecVersion: 1}
}
func (m counter) Initial() ([]byte, error) {
	if m.panicOp {
		panic("test panic")
	}
	if m.fail {
		return nil, fmt.Errorf("test error")
	}
	return []byte{0}, nil
}
func (m counter) Step(b, i []byte) (Transition, error) {
	if len(b) != 1 || len(i) != 1 {
		return Transition{}, fmt.Errorf("malformed counter")
	}
	return Transition{State: []byte{b[0] + i[0]}}, nil
}
func (m counter) CheckState(b []byte) ([]Check, error) {
	if len(b) != 1 {
		return nil, fmt.Errorf("malformed state")
	}
	if m.bad {
		return []Check{{ID: ""}}, nil
	}
	return nil, nil
}
func (m counter) CheckTransition(b, i []byte, t Transition) ([]Check, error) { return nil, nil }
func (m counter) Inputs(b []byte) ([][]byte, error)                          { return [][]byte{{0}}, nil }
func TestRecordReplayEnumeration(t *testing.T) {
	var artifact []byte
	err := WithSession(counter{build: "a"}, func(s *Session) error {
		var e error
		artifact, _, e = s.Record([][]byte{{1}, {2}}, 3)
		if e != nil {
			return e
		}
		status, e := s.Replay(artifact)
		if e != nil || status != OK {
			t.Fatalf("replay %d %v", status, e)
		}
		r, _, _, e := s.Enumerate(Bounds{10, 100, 10})
		if !strings.Contains(r, "GraphExhausted") {
			t.Fatal(r)
		}
		return e
	})
	if err != nil {
		t.Fatal(err)
	}
	err = WithSession(counter{build: "b"}, func(s *Session) error {
		status, e := s.Replay(artifact)
		if status != Incompatible || e == nil {
			t.Fatalf("identity %d %v", status, e)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}
func TestCallbackFailuresAndCleanup(t *testing.T) {
	for _, m := range []counter{{build: "a", panicOp: true}, {build: "a", fail: true}, {build: "a", bad: true}} {
		var retained *Session
		err := WithSession(m, func(s *Session) error { retained = s; _, _, e := s.Record(nil, 1); return e })
		if err == nil {
			t.Fatal("expected callback error")
		}
		if !retained.closed {
			t.Fatal("session not released")
		}
		func() {
			defer func() {
				if recover() == nil {
					t.Fatal("handle retained")
				}
			}()
			retained.handle.Value()
		}()
		if _, _, e := retained.Record(nil, 1); e == nil {
			t.Fatal("closed session used")
		}
	}
}
func TestSessionCleanupOnUserPanic(t *testing.T) {
	var retained *Session
	func() {
		defer func() {
			if recover() == nil {
				t.Fatal("expected panic")
			}
		}()
		_ = WithSession(counter{build: "a"}, func(s *Session) error { retained = s; panic("user panic") })
	}()
	if !retained.closed {
		t.Fatal("not closed")
	}
}
func TestMalformedPackets(t *testing.T) {
	valid, _ := transitionPacket(Transition{State: []byte{0}})
	p := packet{}
	p.blob([]byte{1})
	p.b = append(p.b, valid...)
	for i := 0; i < len(p.b); i++ {
		if _, _, e := decodeCheckInput(p.b[:i]); e == nil {
			t.Fatalf("truncation %d", i)
		}
	}
	if _, _, e := decodeCheckInput(append(p.b, 0)); e == nil {
		t.Fatal("trailing")
	}
	if _, e := transitionPacket(Transition{Disposition: 9}); e == nil {
		t.Fatal("tag")
	}
	if _, e := checksPacket([]Check{{ID: "x", Details: "unexpected"}}); e == nil {
		t.Fatal("passed details")
	}
}

type reentrantCounter struct {
	counter
	initial func() ([]byte, error)
}

func (m reentrantCounter) Initial() ([]byte, error) { return m.initial() }
func TestReentrancy(t *testing.T) {
	var session *Session
	calls := 0
	model := reentrantCounter{counter: counter{build: "a"}, initial: func() ([]byte, error) {
		calls++
		if calls > 1 {
			return nil, fmt.Errorf("reentry reached native callback")
		}
		_, _, err := session.Record(nil, 1)
		return nil, err
	}}
	err := WithSession(model, func(s *Session) error {
		session = s
		_, _, err := s.Record(nil, 1)
		if err == nil || !strings.Contains(err.Error(), "reentrant") {
			t.Fatalf("callback reentry: %v", err)
		}
		if calls != 1 {
			t.Fatalf("native callback invoked %d times", calls)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

type schemaCounter struct{ counter }

func (m schemaCounter) Metadata() Metadata {
	md := m.counter.Metadata()
	md.CodecVersion = 2
	return md
}
func TestIncompatibleSchemaAndMalformedTrace(t *testing.T) {
	var artifact []byte
	if err := WithSession(counter{build: "a"}, func(s *Session) error { var err error; artifact, _, err = s.Record(nil, 1); return err }); err != nil {
		t.Fatal(err)
	}
	if err := WithSession(schemaCounter{counter{build: "a"}}, func(s *Session) error {
		status, err := s.Replay(artifact)
		if status != Incompatible || err == nil {
			t.Fatalf("schema %d %v", status, err)
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := WithSession(counter{build: "a"}, func(s *Session) error {
		for _, b := range [][]byte{nil, {0}, artifact[:len(artifact)/2]} {
			status, err := s.Replay(b)
			if status != 12 || err == nil {
				t.Fatalf("malformed trace %d %v", status, err)
			}
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}

func TestWrongThreadAndConcurrentUse(t *testing.T) {
	if err := WithSession(counter{build: "a"}, func(s *Session) error {
		call := func(want string) {
			t.Helper()
			done := make(chan error, 1)
			go func() { _, _, err := s.Record(nil, 1); done <- err }()
			if err := <-done; err == nil || !strings.Contains(err.Error(), want) {
				t.Fatalf("expected %s, got %v", want, err)
			}
		}
		call("different OS thread")
		if err := s.WithNative(func(_ unsafe.Pointer) error {
			call("concurrent or reentrant")
			return nil
		}); err != nil {
			return err
		}
		_, _, err := s.Record(nil, 1)
		return err
	}); err != nil {
		t.Fatal(err)
	}
}

func TestPacketAggregateBoundAndStickyError(t *testing.T) {
	// Reuse a full-capacity backing array: a rejected append must neither grow
	// nor change the partial packet, including its length prefix.
	p := packet{b: make([]byte, maxBytes-3, maxBytes)}
	before := len(p.b)
	p.blob([]byte{1})
	if p.err == nil || len(p.b) != before {
		t.Fatal("aggregate bound was checked after writing")
	}
	p.u32(0)
	p.tag(0)
	p.str("ignored")
	p.blob([]byte{2})
	if len(p.b) != before {
		t.Fatal("failed packet kept growing")
	}
	if b, err := p.finish(); b != nil || err == nil {
		t.Fatal("partial packet returned")
	}
	p = packet{}
	p.count(maxItems + 1)
	p.blob([]byte{1})
	if len(p.b) != 0 {
		t.Fatal("invalid count allowed allocation")
	}
	p = packet{b: make([]byte, maxBytes-4, maxBytes)}
	p.blob(nil)
	if _, err := p.finish(); err != nil || len(p.b) != maxBytes {
		t.Fatal("exact packet limit rejected")
	}
}

func TestZeroSessionIsRejected(t *testing.T) {
	var s Session
	if _, _, err := s.Record(nil, 1); err == nil || !strings.Contains(err.Error(), "uninitialized") {
		t.Fatalf("zero session: %v", err)
	}
}

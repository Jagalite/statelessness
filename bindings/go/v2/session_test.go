//go:build cgo

package v2

import (
	"errors"
	"fmt"
	"strings"
	"testing"
	"unicode/utf8"
)

type panickingDiagnosticError struct{}

func (panickingDiagnosticError) Error() string { panic("error formatter fault") }

func TestCallbackDiagnosticContainsFormattingFaults(t *testing.T) {
	for _, tc := range []struct {
		name  string
		err   error
		limit uint64
		want  string
	}{
		{"panic", panickingDiagnosticError{}, 65536, "Go callback error (diagnostic formatting panicked)"},
		{"invalid UTF-8", errors.New("bad\xfftext"), 65536, "bad\ufffdtext"},
		{"multibyte boundary", errors.New("a\u20acb"), 3, "a"},
		{"session quota", errors.New("too long"), 3, "too"},
		{"ABI quota", errors.New(strings.Repeat("x", 70000)), 70000, strings.Repeat("x", 65536)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			message := callbackDiagnostic(tc.err, tc.limit)
			if !utf8.Valid(message) || uint64(len(message)) > min(tc.limit, uint64(65536)) || string(message) != tc.want {
				t.Fatalf("invalid diagnostic: bytes=%d, valid=%v", len(message), utf8.Valid(message))
			}
		})
	}
}

func TestCallbackErrorFormatterPanicStaysInsideBoundary(t *testing.T) {
	model := counterModel(false)
	closed := 0
	model.Inputs = func(uint8) (Iterator[uint8], error) {
		return Iterator[uint8]{
			Next:  func() (uint8, bool, error) { return 0, false, panickingDiagnosticError{} },
			Close: func() error { closed++; return nil },
		}, nil
	}
	if err := WithSession(model, DefaultOptions(), func(s *Session[uint8, uint8, uint8]) error {
		result, err := s.Enumerate(DefaultBounds())
		var nativeErr *Error
		if result != nil || !errors.As(err, &nativeErr) || !strings.Contains(nativeErr.Message, "diagnostic formatting panicked") {
			t.Fatalf("missing contained callback failure: result=%v error=%v", result, err)
		}
		if len(s.roots) != 0 || closed != 1 {
			t.Fatalf("callback failure retained roots=%d or closed cursor %d times", len(s.roots), closed)
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}

func counterModel(fail bool) Model[uint8, uint8, uint8] {
	codec := &Codec[uint8, uint8, uint8]{EncodeState: func(v uint8) ([]byte, error) { return []byte{v}, nil }, DecodeState: func(b []byte) (uint8, error) {
		if len(b) != 1 {
			return 0, fmt.Errorf("state length")
		}
		return b[0], nil
	}, EncodeInput: func(v uint8) ([]byte, error) { return []byte{v}, nil }, DecodeInput: func(b []byte) (uint8, error) {
		if len(b) != 1 {
			return 0, fmt.Errorf("input length")
		}
		return b[0], nil
	}, EncodeOutput: func(v uint8) ([]byte, error) { return []byte{v}, nil }}
	return Model[uint8, uint8, uint8]{Metadata: Metadata{Name: "counter", Build: "test", ModelVersion: 1, PropertiesVersion: 1, CodecVersion: 1}, InitialState: func() (uint8, error) { return 0, nil }, Step: func(s, i uint8) (Transition[uint8, uint8], error) { return Accept((s+i)%3, s), nil }, CheckState: func(s uint8) ([]Check, error) {
		if fail && s == 2 {
			return []Check{Failed("bound", "two")}, nil
		}
		return []Check{Passed("bound")}, nil
	}, CloneState: ValueCopy[uint8], CloneInput: ValueCopy[uint8], CloneOutput: ValueCopy[uint8], Inputs: func(s uint8) (Iterator[uint8], error) { return Values([]uint8{1}), nil }, EqualStates: func(a, b uint8) (bool, error) { return a == b, nil }, HashState: func(uint8) (uint64, error) { return 0, nil }, Codec: codec, Generate: func(s uint8, rng *CallbackRNG) (uint8, bool, error) { _, _, err := rng.Index(1); return 1, true, err }, EqualInputs: func(a, b uint8) (bool, error) { return a == b, nil }}
}
func TestSessionEnumerationRecordReplayAndShrink(t *testing.T) {
	if err := WithSession(counterModel(false), DefaultOptions(), func(s *Session[uint8, uint8, uint8]) error {
		enumerated, err := s.Enumerate(DefaultBounds())
		if err != nil {
			return err
		}
		defer enumerated.Close()
		if enumerated.States != 3 || enumerated.Transitions != 3 || enumerated.Termination != 0 {
			t.Fatalf("unexpected enumeration: %+v", enumerated)
		}
		recorded, err := s.Record([]uint8{1, 1}, 5)
		if err != nil {
			return err
		}
		defer recorded.Close()
		replayed, err := s.Replay(recorded.Artifact, false)
		if err != nil {
			return err
		}
		defer replayed.Close()
		if replayed.Status != OK || replayed.StepsVerified != 2 {
			t.Fatalf("unexpected replay %+v", replayed)
		}
		json, err := s.ExportJSON(recorded.Artifact)
		if err != nil {
			return err
		}
		portable, err := s.ReplayJSON(json, false)
		if err != nil {
			return err
		}
		defer portable.Close()
		if portable.Status != OK {
			t.Fatal("portable replay diverged")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := WithSession(counterModel(true), DefaultOptions(), func(s *Session[uint8, uint8, uint8]) error {
		failure, err := s.Fuzz(DefaultFuzzConfig())
		if err != nil {
			return err
		}
		defer failure.Close()
		if failure.Status != PropertyFailed {
			t.Fatal("missing property failure")
		}
		shrunk, err := s.Shrink(failure, DefaultShrinkConfig())
		if err != nil {
			return err
		}
		defer shrunk.Close()
		if !shrunk.ValidatedOriginal || len(shrunk.Inputs) != 2 || len(shrunk.OriginalInputs) != len(failure.Inputs) {
			t.Fatalf("unexpected shrink %+v", shrunk)
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}
func TestObservationAndRingNeverRunReducer(t *testing.T) {
	model := counterModel(false)
	calls := 0
	model.Step = func(s, i uint8) (Transition[uint8, uint8], error) { calls++; return Accept[uint8, uint8](s), nil }
	if err := WithSession(model, DefaultOptions(), func(s *Session[uint8, uint8, uint8]) error {
		result, err := s.CheckObserved(0, 1, Accept[uint8, uint8](1, 0), 1, FullChecks())
		if err != nil {
			return err
		}
		defer result.Close()
		ring, err := s.NewRing(0, 2, 4096)
		if err != nil {
			return err
		}
		defer ring.Close()
		observed, err := ring.Observe(0, 1, Accept[uint8, uint8](1, 0))
		if err != nil {
			return err
		}
		defer observed.Close()
		snapshot, err := ring.Snapshot()
		if err != nil {
			return err
		}
		defer snapshot.Close()
		if calls != 0 || snapshot.Transitions != 1 {
			t.Fatal("observation executed a reducer or lost evidence")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}
func TestLazyInputPanicKeepsPrefixAndCloses(t *testing.T) {
	closed := 0
	if err := WithSession(counterModel(false), DefaultOptions(), func(s *Session[uint8, uint8, uint8]) error {
		n := 0
		cursor := Iterator[uint8]{Next: func() (uint8, bool, error) {
			n++
			if n == 2 {
				panic("iterator fault")
			}
			return 1, true, nil
		}, Close: func() error { closed++; return nil }}
		result, err := s.RecordIterator(cursor, 5)
		if err == nil || result == nil {
			t.Fatal("missing coherent error prefix")
		}
		if result != nil {
			defer result.Close()
			if result.Termination != 4 || result.Transitions != 1 {
				t.Fatalf("bad prefix %+v", result)
			}
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if closed != 1 {
		t.Fatalf("cursor closed %d times", closed)
	}
}
func TestCallbackFaultAndWrongThread(t *testing.T) {
	model := counterModel(false)
	model.EqualStates = func(a, b uint8) (bool, error) { panic("equality fault") }
	if err := WithSession(model, DefaultOptions(), func(s *Session[uint8, uint8, uint8]) error {
		if result, err := s.Enumerate(DefaultBounds()); err == nil || result != nil {
			t.Fatal("callback failure fabricated report")
		}
		done := make(chan error, 1)
		go func() { _, err := s.Enumerate(DefaultBounds()); done <- err }()
		if err := <-done; err == nil {
			t.Fatal("wrong-thread call succeeded")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}
func TestRNGFullWidth(t *testing.T) {
	r := RNG{}
	v, err := r.Next()
	if err != nil || v != 0xe220a8397b1dcdaf {
		t.Fatalf("bad next %x %v", v, err)
	}
	r.State = 0
	v, ok, err := r.Index(^uint64(0))
	if err != nil || !ok || v != 0xe220a8397b1dcdaf {
		t.Fatalf("bad full-width index %x %v", v, err)
	}
}

func TestSnapshotsIsolateMutableStateAndCodecIsOptional(t *testing.T) {
	model := Model[[]byte, uint8, uint8]{Metadata: Metadata{Name: "mutable", Build: "test"}, InitialState: func() ([]byte, error) { return []byte{0}, nil }, Step: func(s []byte, i uint8) (Transition[[]byte, uint8], error) {
		s[0] = (s[0] + i) % 3
		return Accept[[]byte, uint8](s), nil
	}, CheckState: func(s []byte) ([]Check, error) { s[0] = 99; return []Check{Passed("borrow")}, nil }, CloneState: func(s []byte) []byte { return append([]byte(nil), s...) }, CloneInput: ValueCopy[uint8], CloneOutput: ValueCopy[uint8], Inputs: func(s []byte) (Iterator[uint8], error) { s[0] = 99; return Values([]uint8{1}), nil }, EqualStates: func(a, b []byte) (bool, error) { equal := a[0] == b[0]; a[0] = 98; b[0] = 97; return equal, nil }}
	if err := WithSession(model, DefaultOptions(), func(s *Session[[]byte, uint8, uint8]) error {
		result, err := s.Enumerate(DefaultBounds())
		if err != nil {
			return err
		}
		defer result.Close()
		if result.States != 3 || result.Transitions != 3 {
			t.Fatalf("mutable callback escaped snapshot: %+v", result)
		}
		if result, err := s.Record([]uint8{1}, 2); err == nil || result != nil {
			t.Fatal("record without codecs succeeded")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}
func TestAutomaticGeneratorUsesSharedSampler(t *testing.T) {
	model := counterModel(true)
	model.Generate = nil
	model.Automatic = true
	if err := WithSession(model, DefaultOptions(), func(s *Session[uint8, uint8, uint8]) error {
		failure, err := s.Fuzz(DefaultFuzzConfig())
		if err != nil {
			return err
		}
		defer failure.Close()
		if failure.Status != PropertyFailed {
			t.Fatal("automatic sampler missed deterministic failure")
		}
		failure.Inputs[0] = 2
		shrunk, err := s.Shrink(failure, DefaultShrinkConfig())
		if err != nil {
			return err
		}
		defer shrunk.Close()
		if shrunk.OriginalInputs[0] != 1 {
			t.Fatal("caller mutation changed original witness")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}

func TestPortableRecordingExactItemBoundary(t *testing.T) {
	if err := WithSession(counterModel(false), DefaultOptions(), func(s *Session[uint8, uint8, uint8]) error {
		limits := DefaultTraceLimits()
		limits.MaxItems = 1
		trace, err := s.RecordPortable(nil, 1, limits)
		if err != nil {
			return err
		}
		if len(trace.InitialChecks) != 1 || len(trace.Steps) != 0 {
			t.Fatal("exact portable item boundary lost checkpoint")
		}
		limits.MaxPayloadBytes = 0
		if trace, err := s.RecordPortable(nil, 1, limits); trace != nil || err == nil {
			t.Fatal("initial payload cap accepted")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
}

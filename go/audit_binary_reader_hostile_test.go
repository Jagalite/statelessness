package statelessness_test

import (
	"bytes"
	"errors"
	"io"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

type stalledAuditReader struct{ calls int }

func (r *stalledAuditReader) Read([]byte) (int, error) {
	r.calls++
	if r.calls > 100 {
		return 0, errors.New("reader was polled too many times")
	}
	return 0, nil
}

type invalidCountAuditReader struct{ n int }

func (r invalidCountAuditReader) Read(p []byte) (int, error) {
	if r.n > 0 {
		return len(p) + 1, nil
	}
	return -1, nil
}
func TestAuditReaderRejectsHostileReaders(t *testing.T) {
	stalled := &stalledAuditReader{}
	_, err := s.ReadAuditTrace(stalled, s.DefaultBinaryLimits())
	if !errors.Is(err, io.ErrNoProgress) {
		t.Errorf("stalled reader: got %v", err)
	}
	for _, n := range []int{-1, 1} {
		t.Run(map[int]string{-1: "negative", 1: "oversized"}[n], func(t *testing.T) {
			_, err := s.ReadAuditTrace(invalidCountAuditReader{n}, s.DefaultBinaryLimits())
			var ae *s.AuditError
			if !errors.As(err, &ae) || ae.Kind != "io" {
				t.Fatalf("invalid count: got %v", err)
			}
		})
	}
	for _, fixture := range binaryCorpus(t) {
		if !fixture.Valid {
			continue
		}
		for _, n := range []int{-1, 1} {
			reader := io.MultiReader(bytes.NewReader(binaryBytes(t, fixture.BytesHex)), invalidCountAuditReader{n})
			_, err := s.ReadAuditTrace(reader, s.DefaultBinaryLimits())
			var ae *s.AuditError
			if !errors.As(err, &ae) || ae.Kind != "io" {
				t.Fatalf("footer invalid count %d: got %v", n, err)
			}
		}
		break
	}
}

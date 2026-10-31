package statelessness_test

import (
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

type recorderInvalidCountWriter struct {
	prior int
	bad   int
}

func (w *recorderInvalidCountWriter) Write(p []byte) (int, error) {
	if w.prior == 0 {
		w.prior = len(p)
		return len(p), nil
	}
	if w.bad < 0 {
		return -1, nil
	}
	return len(p) + 1, nil
}

func TestRecorderWriteToDoesNotCountInvalidWriterCounts(t *testing.T) {
	for _, bad := range []int{-1, 1} {
		w := &recorderInvalidCountWriter{bad: bad}
		r, err := s.NewRecorder(auditRecordingModel(), 0, s.RunConfig{Strategy: "manual"}, 1)
		if err != nil {
			t.Fatal(err)
		}
		n, err := r.WriteTo(w)
		if err == nil || w.prior == 0 || n != int64(w.prior) {
			t.Fatalf("invalid count %d: n=%d prior=%d err=%v", bad, n, w.prior, err)
		}
	}
}

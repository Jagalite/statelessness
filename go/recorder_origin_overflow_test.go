package statelessness_test

import (
	"math"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

func TestRawRecorderRejectsAbsoluteSequenceOverflow(t *testing.T) {
	for _, tc := range []struct {
		name     string
		origin   uint64
		accepted int
	}{{"already_max", math.MaxUint64, 0}, {"one_remaining", math.MaxUint64 - 1, 1}} {
		t.Run(tc.name, func(t *testing.T) {
			calls := &observationRecorderCalls{}
			model := observationRecorderModel(calls, 0)
			initial, err := s.CheckInitial(model, 0)
			if err != nil {
				t.Fatal(err)
			}
			r, err := s.NewRecorderWithCheckedInitialAt(initial, tc.origin, s.RunConfig{}, s.DefaultRecorderOptions())
			if err != nil {
				t.Fatal(err)
			}
			for n := 0; n < tc.accepted; n++ {
				_, err = s.ObserveRecorder(r, model, n, 1, s.Accept[int, string](n+1, "effect:1", "audit:1"))
				if err != nil {
					t.Fatal(err)
				}
			}
			before, encoded := calls.application(), calls.encoding()
			if _, err = s.ObserveRecorder(r, model, 1, 1, s.Accept[int, string](2, "effect:2", "audit:1")); err == nil {
				t.Fatal("overflow accepted")
			}
			if !r.IsFrozen() || r.ObservedSteps() != uint64(tc.accepted) || calls.application() != before || calls.encoding() != encoded {
				t.Fatal("overflow changed evidence or invoked callbacks")
			}
		})
	}
}

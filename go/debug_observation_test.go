package statelessness_test

import (
	s "github.com/Jagalite/statelessness/go"
	"testing"
)

func TestDebugObservationVariants(t *testing.T) {
	var initial s.DebugObservation[int, int, string] = s.DebugInitial[int, int, string]{State: 9, Checks: []s.Check{s.Passed("state")}}
	switch value := initial.(type) {
	case s.DebugInitial[int, int, string]:
		if value.State != 9 || len(value.Checks) != 1 {
			t.Fatal(value)
		}
	default:
		t.Fatal("initial variant lost")
	}
	view := s.TurnObservation[int, int, string]{Sequence: ^uint64(0), Before: 9, Input: 1, Transition: s.Accept[int, string](10, "effect"), Checks: []s.Check{s.Passed("state"), s.Passed("transition")}, StateCheckCount: 1}
	var turn s.DebugObservation[int, int, string] = s.DebugTurn[int, int, string]{Observation: view}
	got := turn.(s.DebugTurn[int, int, string]).Observation
	if got.Sequence != ^uint64(0) || got.StateCheckCount != 1 || len(got.Checks) != 2 {
		t.Fatal(got)
	}
	if _, ok := any(turn).(*s.CheckedTurn[int, int, string]); ok {
		t.Fatal("diagnostics granted authority")
	}
}

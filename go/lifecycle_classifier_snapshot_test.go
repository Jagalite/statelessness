package statelessness_test

import (
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

func TestLifecycleOraclePinsHistoryAndInputEventsBeforeClassifiers(t *testing.T) {
	monitor := s.NewIndexedLifecycleMonitor[int](s.RetentionLimits{Obligations: 4, Resources: 4, Violations: 4})
	key := s.ScopedKey[int]{Epoch: 1, Key: 1}
	history := s.LifecycleHistory[s.ScopedKey[int]]{Obligations: []s.LifecycleEntry[s.ScopedKey[int]]{{Key: key, Obligation: s.LifecycleObligation{Deadline: 10}}}}
	events := []s.LifecycleEvent[s.ScopedKey[int]]{{Kind: "settle", Key: key}}
	outputs := []int{5}
	oracle := s.LifecycleOracle[int, int, int](monitor, s.Identity("snapshot", "test"),
		func(int, s.Disposition) ([]s.LifecycleEvent[s.ScopedKey[int]], error) {
			history.Obligations[0].Obligation.Cancelled = true
			outputs[0] = 9
			return events, nil
		},
		func(got []int) ([]s.LifecycleEvent[s.ScopedKey[int]], error) {
			if len(got) != 1 || got[0] != 5 {
				t.Errorf("output classifier saw mutated slots: %v", got)
			}
			events[0].Kind = "cancel"
			return nil, nil
		})
	result, err := oracle.Advance(history, 0, outputs, s.Accepted())
	if err != nil {
		t.Fatal(err)
	}
	obligation := result.Obligations[0].Obligation
	if !obligation.Settled || obligation.Cancelled || len(result.Violations) != 0 {
		t.Fatalf("classifiers changed retained observations: %+v", result)
	}
}

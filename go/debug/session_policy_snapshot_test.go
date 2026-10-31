package debug

import (
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

func TestSessionPolicyCapturesInputBeforeStateCopy(t *testing.T) {
	for _, declared := range []bool{false, true} {
		t.Run(map[bool]string{false: "enumerated", true: "declared"}[declared], func(t *testing.T) {
			input := 7
			mutate := false
			seen := 0
			m := s.Model[int, *int, int]{
				CloneState: func(v int) int {
					if mutate {
						input = 99
					}
					return v
				},
				CloneInput:  func(v *int) *int { x := *v; return &x },
				CloneOutput: s.ValueCopy[int],
				CheckState:  func(int) ([]s.Check, error) { return nil, nil },
				Step:        func(state int, v *int) (s.Transition[int, int], error) { seen = *v; return s.Accept[int, int](state), nil },
			}
			policy := InputPolicy[int, *int]{Label: "test"}
			if declared {
				policy.Validate = func(_ int, v *int) (bool, error) { return *v == 7, nil }
			} else {
				policy.Candidates = func(int) (s.Iterator[*int], error) { v := 7; return s.Values([]*int{&v}), nil }
			}
			session, err := SessionFromState("test", m, 0, policy, func(a, b *int) (bool, error) { return *a == *b, nil }, DefaultSessionLimits())
			if err != nil {
				t.Fatal(err)
			}
			mutate = true
			result, err := session.Step(0, &input)
			if err != nil || !result.Delivered || seen != 7 {
				t.Fatalf("result=%+v err=%v seen=%d", result, err, seen)
			}
		})
	}
}

func TestSessionInputCopyFailureOnEmptyDomainAndUnrestricted(t *testing.T) {
	m := s.Model[int, int, int]{
		CloneState: s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		CloneInput: func(int) int { panic("copy failure") },
		CheckState: func(int) ([]s.Check, error) { return nil, nil },
		Step:       func(state, _ int) (s.Transition[int, int], error) { return s.Accept[int, int](state), nil },
	}
	equal := func(a, b int) (bool, error) { return a == b, nil }
	empty := InputPolicy[int, int]{Label: "empty", Candidates: func(int) (s.Iterator[int], error) { return s.Values([]int{}), nil }}
	v, err := SessionFromState("empty", m, 0, empty, equal, DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	if _, err = v.Step(0, 1); err == nil || v.Phase() == Ended {
		t.Fatalf("empty domain: phase=%v err=%v", v.Phase(), err)
	}
	u, err := SessionFromState("open", m, 0, Unrestricted[int, int](), equal, DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	func() {
		defer func() {
			if recover() == nil {
				t.Error("expected clone panic")
			}
		}()
		_, _ = u.Step(0, 1)
	}()
	if u.Phase() != Ended {
		t.Fatalf("clone panic left session live: %v", u.Phase())
	}
}

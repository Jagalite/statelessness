package statelessness_test

import (
	"encoding/json"
	"errors"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"math"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

func TestCheckedResultsSharedCorpus(t *testing.T) {
	path := os.Getenv("STATELESS_CHECKED_CORPUS")
	if path == "" {
		path = "../conformance/checked-results-v1.json"
	}
	data, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct {
		Cases []struct {
			Case, Kind string
			Model      struct {
				StateChecks      []s.Check `json:"state_checks"`
				TransitionChecks []s.Check `json:"transition_checks"`
				StateError       *string   `json:"state_error"`
				TransitionError  *string   `json:"transition_error"`
			}
			State, Before, Input int
			Transition           s.Transition[int, int]
			Sequence             string
			Policy               struct {
				StateEvery       string `json:"state_every"`
				TransitionChecks bool   `json:"transition_checks"`
			}
			Expected struct {
				ErrorContains     *string `json:"error_contains"`
				Events            []string
				Token             json.RawMessage
				InitialStateCalls int `json:"initial_state_calls"`
				StepCalls         int `json:"step_calls"`
			}
		}
	}
	if e = json.Unmarshal(data, &corpus); e != nil {
		t.Fatal(e)
	}
	if len(corpus.Cases) == 0 {
		t.Fatal("empty checked corpus")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Case, func(t *testing.T) {
			events := []string{}
			initialCalls, stepCalls := 0, 0
			m := s.Model[int, int, int]{CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], InitialState: func() (int, error) { initialCalls++; return 0, nil }, Step: func(int, int) (s.Transition[int, int], error) { stepCalls++; return s.Accept[int, int](0), nil }}
			m.CheckStateInto = func(v int, b *s.CheckSink) error {
				events = append(events, fmt.Sprintf("state:%d", v))
				if e := b.Extend(c.Model.StateChecks); e != nil {
					return e
				}
				if c.Model.StateError != nil {
					return errors.New(*c.Model.StateError)
				}
				return nil
			}
			m.CheckTransitionInto = func(bef, in int, tr s.Transition[int, int], b *s.CheckSink) error {
				events = append(events, fmt.Sprintf("transition:%d:%d:%d", bef, in, tr.State))
				if e := b.Extend(c.Model.TransitionChecks); e != nil {
					return e
				}
				if c.Model.TransitionError != nil {
					return errors.New(*c.Model.TransitionError)
				}
				return nil
			}
			var actual any
			var err error
			if c.Kind == "initial" {
				token, e := s.CheckInitial(m, c.State)
				err = e
				if token != nil {
					state, e := token.State()
					if e != nil {
						t.Fatal(e)
					}
					actual = map[string]any{"kind": "initial", "state": state, "checks": token.Checks(), "state_check_count": token.StateCheckCount()}
				}
			} else {
				seq, e := strconv.ParseUint(c.Sequence, 10, 64)
				if e != nil {
					t.Fatal(e)
				}
				period, e := strconv.ParseUint(c.Policy.StateEvery, 10, 64)
				if e != nil {
					t.Fatal(e)
				}
				token, e := s.CheckTurn(m, c.Before, c.Input, c.Transition, seq, s.CheckPolicy{StateEvery: period, TransitionChecks: c.Policy.TransitionChecks})
				err = e
				if token != nil {
					obs, e := token.Observation()
					if e != nil {
						t.Fatal(e)
					}
					actual = map[string]any{"kind": "turn", "before": obs.Before, "input": obs.Input, "transition": replayNormalizedTransition(obs.Transition), "sequence": strconv.FormatUint(token.Sequence(), 10), "checks": token.Checks(), "state_check_count": token.StateCheckCount(), "full_checking": token.FullChecking()}
				}
			}
			if c.Expected.ErrorContains != nil {
				if err == nil || !strings.Contains(err.Error(), *c.Expected.ErrorContains) {
					t.Fatalf("error=%v want substring %q", err, *c.Expected.ErrorContains)
				}
			} else if err != nil {
				t.Fatal(err)
			}
			a, _ := json.Marshal(actual)
			var av, ev any
			json.Unmarshal(a, &av)
			json.Unmarshal(c.Expected.Token, &ev)
			if !reflect.DeepEqual(av, ev) {
				t.Fatalf("token=%s expected=%s", a, c.Expected.Token)
			}
			if !reflect.DeepEqual(events, c.Expected.Events) || initialCalls != c.Expected.InitialStateCalls || stepCalls != c.Expected.StepCalls {
				t.Fatalf("events=%v initial=%d step=%d expected=%+v", events, initialCalls, stepCalls, c.Expected)
			}
		})
	}
}

func checkedSliceModel() s.Model[[]int, []int, []int] {
	clone := func(v []int) []int { return append([]int(nil), v...) }
	return s.Model[[]int, []int, []int]{CloneState: clone, CloneInput: clone, CloneOutput: clone, CheckStateInto: func(v []int, b *s.CheckSink) error { v[0] = 999; return b.Push(s.Failed("state", "bad")) }, CheckTransitionInto: func(before, input []int, tr s.Transition[[]int, []int], b *s.CheckSink) error {
		before[0] = 100
		input[0] = 100
		tr.State[0] = 100
		tr.Outputs[0][0] = 100
		return b.Push(s.Passed("edge"))
	}}
}
func TestCheckedResultsDetachedConsistentContext(t *testing.T) {
	m := checkedSliceModel()
	before, input, after, output := []int{7}, []int{2}, []int{9}, []int{90}
	tr := s.Accept(after, output)
	initial, e := s.CheckInitial(m, before)
	if e != nil {
		t.Fatal(e)
	}
	token, e := s.CheckTurn(m, before, input, tr, math.MaxUint64, s.FullChecks())
	if e != nil {
		t.Fatal(e)
	}
	before[0] = 1
	input[0] = 1
	after[0] = 1
	output[0] = 1
	first, e := token.Observation()
	if e != nil {
		t.Fatal(e)
	}
	if first.Before[0] != 7 || first.Input[0] != 2 || first.Transition.State[0] != 9 || first.Transition.Outputs[0][0] != 90 || token.Sequence() != math.MaxUint64 || !token.FullChecking() {
		t.Fatalf("bad detached context %+v", first)
	}
	first.Before[0] = 5
	first.Input[0] = 5
	first.Transition.State[0] = 5
	first.Transition.Outputs[0][0] = 5
	first.Checks[0].ID = "changed"
	again, e := token.Observation()
	if e != nil {
		t.Fatal(e)
	}
	if again.Before[0] != 7 || again.Input[0] != 2 || again.Transition.State[0] != 9 || again.Transition.Outputs[0][0] != 90 || again.Checks[0].ID != "state" {
		t.Fatal("diagnostic aliases token")
	}
	st, e := initial.State()
	if e != nil || st[0] != 7 {
		t.Fatalf("initial state=%v error=%v", st, e)
	}
	st[0] = 88
	checks := initial.IntoChecks()
	checks[0].ID = "changed"
	st, _ = initial.State()
	if st[0] != 7 || initial.Checks()[0].ID != "state" {
		t.Fatal("initial accessor aliases token")
	}
}
func TestCheckedResultsNoTokenAfterFailuresAndDefaultForgery(t *testing.T) {
	m := checkedSliceModel()
	calls := 0
	m.CheckStateInto = func(v []int, b *s.CheckSink) error {
		calls++
		b.Push(s.Passed("prefix"))
		return errors.New("state failure")
	}
	token, e := s.CheckInitial(m, []int{7})
	if e == nil || token != nil || calls != 1 {
		t.Fatal("initial failed check minted token")
	}
	m.CheckStateInto = func(v []int, b *s.CheckSink) error { calls++; return b.Push(s.Passed("state")) }
	m.CheckTransitionInto = func([]int, []int, s.Transition[[]int, []int], *s.CheckSink) error { panic("edge panic") }
	actual := s.Accept([]int{9}, []int{90})
	turn, e := s.CheckTurn(m, []int{7}, []int{2}, actual, 1, s.FullChecks())
	if e == nil || turn != nil || calls != 2 || actual.State[0] != 9 || actual.Outputs[0][0] != 90 {
		t.Fatal("panic minted token or mutated actual")
	}
	m.CloneState = func([]int) []int { panic("snapshot") }
	turn, e = s.CheckTurn(m, []int{7}, []int{2}, actual, 1, s.FullChecks())
	if e == nil || turn != nil || calls != 2 {
		t.Fatal("snapshot failure repeated checks")
	}
	var forged s.CheckedTurn[[]int, []int, []int]
	if _, e = forged.Observation(); e == nil || forged.FullChecking() || forged.Sequence() != 0 || forged.Checks() != nil {
		t.Fatal("default token authenticated")
	}
	var forgedInitial s.CheckedInitial[[]int, []int, []int]
	if _, e = forgedInitial.State(); e == nil {
		t.Fatal("default initial authenticated")
	}
}

func TestCheckedTokenSnapshotFailureAfterStateCheckDoesNotRepeat(t *testing.T) {
	m := checkedSliceModel()
	stateCalls, edgeCalls, outputClones := 0, 0, 0
	m.CheckStateInto = func([]int, *s.CheckSink) error { stateCalls++; return nil }
	m.CheckTransitionInto = func([]int, []int, s.Transition[[]int, []int], *s.CheckSink) error { edgeCalls++; return nil }
	m.CloneOutput = func(v []int) []int {
		outputClones++
		if outputClones == 2 {
			panic("second output snapshot")
		}
		return append([]int(nil), v...)
	}
	actual := s.Accept([]int{9}, []int{90})
	token, e := s.CheckTurn(m, []int{7}, []int{2}, actual, 1, s.FullChecks())
	if e == nil || token != nil || stateCalls != 1 || edgeCalls != 0 || actual.State[0] != 9 || actual.Outputs[0][0] != 90 {
		t.Fatalf("token=%v error=%v state=%d edge=%d actual=%+v", token, e, stateCalls, edgeCalls, actual)
	}
}

func TestCheckedTokenCapturesBeforeCallbacksAndDoesNotExposeModelCodec(t *testing.T) {
	state := []int{7}
	m := checkedSliceModel()
	m.Codec = &s.Codec[[]int, []int, []int]{Metadata: func() (s.Metadata, error) { return s.Identity("original", "build"), nil }}
	m.CheckStateInto = func(v []int, b *s.CheckSink) error { state[0] = 400; v[0] = 500; return b.Push(s.Passed("state")) }
	token, e := s.CheckInitial(m, state)
	if e != nil {
		t.Fatal(e)
	}
	st, e := token.State()
	if e != nil || st[0] != 7 || state[0] != 400 {
		t.Fatalf("snapshot=%v original=%v error=%v", st, state, e)
	}
	m.Codec.Metadata = func() (s.Metadata, error) { return s.Identity("caller replacement", "build"), nil }
	exposed, e := token.Model()
	if e != nil {
		t.Fatal(e)
	}
	identity, e := exposed.Codec.Metadata()
	if e != nil || identity.Name != "original" {
		t.Fatal("source codec replacement modified token")
	}
	exposed.Codec.Metadata = func() (s.Metadata, error) { return s.Identity("accessor replacement", "build"), nil }
	again, _ := token.Model()
	identity, _ = again.Codec.Metadata()
	if identity.Name != "original" {
		t.Fatal("model accessor exposed token codec")
	}
}

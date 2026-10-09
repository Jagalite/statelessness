// A native application model: JSON is not involved in transitions or exploration.
package main

import (
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"strconv"
)

func main() {
	model := s.Model[int, string, string]{
		InitialState: func() (int, error) { return 0, nil },
		Step: func(state int, input string) (s.Transition[int, string], error) {
			if input != "increment" {
				return s.Transition[int, string]{}, fmt.Errorf("unknown input")
			}
			return s.Accept[int, string](state+1, "changed"), nil
		},
		CheckState: func(state int) ([]s.Check, error) {
			if state > 2 {
				return []s.Check{s.Failed("counter.bound", "above two")}, nil
			}
			return []s.Check{s.Passed("counter.bound")}, nil
		},
		CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[string], CloneOutput: s.ValueCopy[string],
		Inputs:      func(int) (s.Iterator[string], error) { return s.Values([]string{"increment"}), nil },
		EqualStates: func(a, b int) bool { return a == b },
		Codec: &s.Codec[int, string, string]{
			Metadata:     func() (s.Metadata, error) { return s.Identity("counter", "example-v1"), nil },
			EncodeState:  func(v int) ([]byte, error) { return []byte(strconv.Itoa(v)), nil },
			DecodeState:  func(b []byte) (int, error) { return strconv.Atoi(string(b)) },
			EncodeInput:  func(v string) ([]byte, error) { return []byte(v), nil },
			DecodeInput:  func(b []byte) (string, error) { return string(b), nil },
			EncodeOutput: func(v string) ([]byte, error) { return []byte(v), nil },
		},
	}
	report, err := s.Enumerate(model, s.SearchConfig{MaxStates: 10, MaxTransitions: 10, MaxDepth: 10})
	if err != nil {
		panic(err)
	}
	if report.Failure == nil || len(report.Failure.Inputs) != 3 {
		panic("expected three-step witness")
	}
	trace, err := s.Record(model, s.Values(report.Failure.Inputs), 10)
	if err != nil {
		panic(err)
	}
	encoded, err := s.MarshalTrace(trace)
	if err != nil {
		panic(err)
	}
	restored, err := s.ParseTrace(encoded)
	if err != nil {
		panic(err)
	}
	replay, err := s.Replay(model, restored, false)
	if err != nil {
		panic(err)
	}
	if replay.Outcome != "exact" || !replay.FailureReproduced {
		panic("failure did not replay")
	}
	fmt.Printf("%s: %v; replay=%s\n", report.Termination, report.Failure.Inputs, replay.Outcome)
}

package statelessness_test

import (
	"encoding/json"
	"errors"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

type appendObservation struct {
	Phase    string    `json:"phase"`
	Complete bool      `json:"complete"`
	Checks   []s.Check `json:"checks"`
}
type appendExpected struct {
	Outcome      string              `json:"outcome"`
	Checks       []s.Check           `json:"checks"`
	Observations []appendObservation `json:"observations"`
	Witness      []int               `json:"witness"`
	Attempts     *int                `json:"attempts"`
}
type appendVector struct {
	Case, Operation, Mode string
	ErrorAt               *string `json:"error_at"`
	FailState             *int    `json:"fail_state"`
	Sequence              uint64
	StateEvery            uint64  `json:"state_every"`
	TransitionChecks      bool    `json:"transition_checks"`
	WrapperError          *string `json:"wrapper_error"`
	ErrorContains         *string `json:"error_contains"`
	Expected              appendExpected
	Events                []string
}

func TestAppendSharedCorpus(t *testing.T) {
	path := os.Getenv("STATELESS_APPEND_CORPUS")
	if path == "" {
		path = "../conformance/append-checks-v1.json"
	}
	data, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct{ Cases []appendVector }
	if e = json.Unmarshal(data, &corpus); e != nil {
		t.Fatal(e)
	}
	if len(corpus.Cases) < 52 {
		t.Fatal("missing append vectors")
	}
	for _, v := range corpus.Cases {
		t.Run(v.Case, func(t *testing.T) {
			events := []string{}
			log := func(event string) { events = append(events, event) }
			fixtureError := func() error { return errors.New("fixture append error") }
			isError := func(event string) bool { return v.ErrorAt != nil && *v.ErrorAt == event }
			wrapperError := func(kind string) bool { return v.WrapperError != nil && *v.WrapperError == kind }
			stateValues := func(state int) []s.Check {
				values := []s.Check{s.Passed("same"), s.Skipped("skip", "not run")}
				if v.FailState != nil && state == *v.FailState {
					values = append(values, s.Failed("bad", "broken"))
				}
				return values
			}
			makeModel := func(prefix string) s.Model[int, int, int] {
				model := s.Model[int, int, int]{InitialState: func() (int, error) { log(prefix + "initial"); return 0, nil }, Step: func(state, input int) (s.Transition[int, int], error) {
					log(fmt.Sprintf("%sstep:%d:%d", prefix, state, input))
					return s.Accept[int, int](state + input), nil
				}, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], Inputs: func(state int) (s.Iterator[int], error) {
					if state == 0 {
						return s.Values([]int{1}), nil
					}
					return s.Values([]int{}), nil
				}, EqualStates: func(a, b int) bool { return a == b }, Codec: &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("append-fixture", "native-test"), nil }, EncodeState: func(state int) ([]byte, error) { return []byte(strconv.Itoa(state)), nil }, DecodeState: func(data []byte) (int, error) { return strconv.Atoi(string(data)) }, EncodeInput: func(input int) ([]byte, error) { return []byte(strconv.Itoa(input)), nil }, DecodeInput: func(data []byte) (int, error) { return strconv.Atoi(string(data)) }, EncodeOutput: func(output int) ([]byte, error) { return []byte(strconv.Itoa(output)), nil }}}
				model.CheckState = func(state int) ([]s.Check, error) {
					log(fmt.Sprintf("%slegacy_state:%d", prefix, state))
					if v.Mode == "append" {
						return nil, errors.New("append model used legacy state")
					}
					if isError(fmt.Sprintf("state:%d", state)) {
						return nil, fixtureError()
					}
					return stateValues(state), nil
				}
				model.CheckTransition = func(_ int, _ int, tr s.Transition[int, int]) ([]s.Check, error) {
					log(fmt.Sprintf("%slegacy_transition:%d", prefix, tr.State))
					if v.Mode == "append" {
						return nil, errors.New("append model used legacy edge")
					}
					if isError(fmt.Sprintf("transition:%d", tr.State)) {
						return nil, fixtureError()
					}
					return []s.Check{s.Passed("same")}, nil
				}
				if v.Mode == "append" {
					model.CheckStateInto = func(state int, sink *s.CheckSink) error {
						length, e := sink.Len()
						if e != nil {
							return e
						}
						log(fmt.Sprintf("%sappend_state:%d:%d", prefix, state, length))
						if e = sink.Push(s.Passed("same")); e != nil {
							return e
						}
						if isError(fmt.Sprintf("state:%d", state)) || prefix == "reference_" && wrapperError("reference_state") {
							return fixtureError()
						}
						return sink.Extend(stateValues(state)[1:])
					}
					model.CheckTransitionInto = func(_ int, _ int, tr s.Transition[int, int], sink *s.CheckSink) error {
						length, e := sink.Len()
						if e != nil {
							return e
						}
						log(fmt.Sprintf("%sappend_transition:%d:%d", prefix, tr.State, length))
						if e = sink.Push(s.Passed("same")); e != nil {
							return e
						}
						if isError(fmt.Sprintf("transition:%d", tr.State)) {
							return fixtureError()
						}
						return nil
					}
				}
				return model
			}
			model := makeModel("")
			result := appendExpected{Outcome: "ok", Observations: []appendObservation{}}
			observer := s.SearchObserver[int, int, int]{Initial: func(_ int, checks []s.Check) error {
				log("observe_initial")
				result.Observations = append(result.Observations, appendObservation{"initial_state", true, checks})
				return nil
			}, Transition: func(_ int, _ int, _ s.Transition[int, int], state, edge []s.Check, _ []int) error {
				log("observe_transition")
				result.Observations = append(result.Observations, appendObservation{"state", true, state}, appendObservation{"transition", true, edge})
				return nil
			}, CheckError: func(phase string, checks []s.Check, complete bool) error {
				log(fmt.Sprintf("check_error:%s:%t", phase, complete))
				result.Observations = append(result.Observations, appendObservation{phase, complete, checks})
				return nil
			}}
			storage := []s.Check{s.Passed("sentinel")}
			transition := s.Accept[int, int](1)
			var operationError error
			switch v.Operation {
			case "direct_state":
				operationError = s.WithCheckSink(&storage, func(sink *s.CheckSink) error { return model.AppendStateChecks(1, sink) })
				result.Checks = storage
			case "direct_transition":
				operationError = s.WithCheckSink(&storage, func(sink *s.CheckSink) error { return model.AppendTransitionChecks(0, 1, transition, sink) })
				result.Checks = storage
			case "observed":
				operationError = s.CheckObservedInto(model, 0, 1, transition, v.Sequence, s.CheckPolicy{StateEvery: v.StateEvery, TransitionChecks: v.TransitionChecks}, &storage)
				result.Checks = storage
			case "bfs":
				report, e := s.EnumerateObserved(model, s.DefaultSearchConfig(), s.SearchLimits{}, observer)
				operationError = e
				if e == nil {
					result.Outcome = report.Termination
					if report.Failure != nil {
						result.Witness = report.Failure.Inputs
					}
				}
			case "fuzz":
				g := s.GenerateModel[int, int, int]{Model: model, Generate: func(int, *s.Rng) (int, bool, error) { return 1, true, nil }}
				report, e := s.FuzzObserved(g, s.FuzzConfig{Cases: 1, MaxSteps: 1, MaxTransitions: 1}, s.RunLimits{}, observer)
				operationError = e
				if e == nil {
					result.Outcome = report.Termination
					if report.Failure != nil {
						result.Witness = report.Failure.Inputs
					}
				}
			case "shrink":
				g := s.GenerateModel[int, int, int]{Model: model, InputEqual: func(a, b int) (bool, error) { return a == b, nil }}
				report, e := s.Shrink(g, s.Failure[int]{Inputs: []int{1}, Violations: []s.Violation{{Phase: "state", Check: s.Failed("bad", "broken")}}}, s.DefaultShrinkConfig())
				operationError = e
				if e == nil {
					result.Outcome = report.Termination
					result.Witness = report.Minimized.Inputs
					result.Attempts = &report.Attempts
				}
			case "record":
				trace, e := s.Record(model, s.Values([]int{1}), 10)
				operationError = e
				if e == nil {
					result.Outcome = trace.Termination
					result.Checks = append([]s.Check{}, trace.InitialChecks...)
					for _, step := range trace.Steps {
						result.Checks = append(result.Checks, step.Checks...)
					}
				}
			case "replay":
				trace := s.Trace{Metadata: s.Identity("append-fixture", "native-test"), InitialState: []byte("0"), InitialChecks: stateValues(0), Steps: []s.TraceStep{{Input: []byte("1"), Disposition: s.Accepted(), Outputs: [][]byte{}, PostState: []byte("1"), Checks: append(stateValues(1), s.Passed("same"))}}, Termination: "completed"}
				report, e := s.Replay(model, &trace, false)
				operationError = e
				if e == nil {
					result.Outcome = report.Outcome
				}
			case "oracle_state", "oracle_transition", "paired_state", "paired_transition":
				oracle := s.Oracle[int, int, int, int]{InitialState: func() (int, error) { return 0, nil }, CloneHistory: s.ValueCopy[int], Advance: func(history, input int, _ []int, _ s.Disposition) (int, error) { return history + input, nil }, CheckStateInto: func(history int, _ int, sink *s.CheckSink) error {
					length, e := sink.Len()
					if e != nil {
						return e
					}
					log(fmt.Sprintf("oracle_state:%d:%d", history, length))
					if e = sink.Push(s.Passed("oracle.state")); e != nil {
						return e
					}
					if wrapperError("oracle_state") {
						return fixtureError()
					}
					return sink.Push(s.Skipped("oracle.skip", "not run"))
				}, CheckTransitionInto: func(_ s.OracleState[int, int], _ int, tr s.Transition[s.OracleState[int, int], int], sink *s.CheckSink) error {
					length, e := sink.Len()
					if e != nil {
						return e
					}
					log(fmt.Sprintf("oracle_transition:%d:%d", tr.State.Oracle, length))
					if e = sink.Push(s.Passed("oracle.transition")); e != nil {
						return e
					}
					if wrapperError("oracle_transition") {
						return fixtureError()
					}
					return sink.Push(s.Skipped("oracle.skip", "not run"))
				}}
				if strings.HasPrefix(v.Operation, "paired_") {
					reference := makeModel("reference_")
					projection := s.Projection[int, int, int, int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("projection", "native-test"), nil }, ActualState: func(state int) int {
						log("projection_actual_state")
						if wrapperError("projection_actual_state") {
							panic("fixture append error")
						}
						return state
					}, ReferenceState: func(state int) int { log("projection_reference_state"); return state }, ActualEffect: s.ValueCopy[int], ReferenceEffect: s.ValueCopy[int], EqualStates: func(a, b int) bool { return a == b }, EqualEffects: func(a, b int) bool { return a == b }}
					var e error
					oracle, e = s.Paired(reference, projection)
					if e != nil {
						t.Fatal(e)
					}
				}
				wrapped, e := s.WithOracle(model, oracle)
				if e != nil {
					t.Fatal(e)
				}
				operationError = s.WithCheckSink(&storage, func(sink *s.CheckSink) error {
					if strings.HasSuffix(v.Operation, "state") {
						return wrapped.AppendStateChecks(s.OracleState[int, int]{Model: 1, Oracle: 1}, sink)
					}
					return wrapped.AppendTransitionChecks(s.OracleState[int, int]{}, 1, s.Accept[s.OracleState[int, int], int](s.OracleState[int, int]{Model: 1, Oracle: 1}), sink)
				})
				result.Checks = storage
			default:
				t.Fatal("unknown append operation", v.Operation)
			}
			if operationError != nil {
				result.Outcome = "error"
			}
			if v.ErrorContains != nil && (operationError == nil || !strings.Contains(operationError.Error(), *v.ErrorContains)) {
				t.Fatal("expected error", v.ErrorContains, operationError)
			}
			if !reflect.DeepEqual(events, v.Events) {
				t.Fatalf("events%v expected%v error%v", events, v.Events, operationError)
			}
			if !reflect.DeepEqual(result, v.Expected) {
				t.Fatalf("result%+v expected%+v error%v", result, v.Expected, operationError)
			}
		})
	}
}

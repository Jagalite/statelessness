package statelessness_test

import (
	"encoding/json"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"os"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type bfsVector struct {
	Case, Fixture          string
	Estimates              []*string
	MaxStates              int     `json:"max_states"`
	MaxTransitions         uint64  `json:"max_transitions"`
	MaxDepth               int     `json:"max_depth"`
	MaxEstimatedStateBytes *string `json:"max_estimated_state_bytes"`
	EntryControl           string  `json:"entry_control"`
	CancelAt               *string `json:"cancel_at"`
	ErrorAt                *string `json:"error_at"`
	ErrorPrefix            bool    `json:"error_prefix"`
	NeedsHistory           bool    `json:"needs_history"`
	FailInitial            bool    `json:"fail_initial"`
	FailState              *int    `json:"fail_state"`
	Skipped                bool
	Expected               *struct {
		Termination                 string
		States                      int
		Transitions                 uint64
		MaxDepthReached             int     `json:"max_depth_reached"`
		SkippedChecks               uint64  `json:"skipped_checks"`
		EstimatedRetainedStateBytes *string `json:"estimated_retained_state_bytes"`
		Witness                     []int
		FailedPhase                 *string `json:"failed_phase"`
		FailedID                    *string `json:"failed_id"`
	}
	ErrorContains *string `json:"error_contains"`
	Events        []string
}

func TestBFSSharedControlsCorpus(t *testing.T) {
	path := os.Getenv("STATELESS_BFS_CORPUS")
	if path == "" {
		path = "../conformance/bfs-controls-v1.json"
	}
	data, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct{ Cases []bfsVector }
	if e = json.Unmarshal(data, &corpus); e != nil {
		t.Fatal(e)
	}
	if len(corpus.Cases) < 43 {
		t.Fatal("missing BFS vectors")
	}
	for _, v := range corpus.Cases {
		t.Run(v.Case, func(t *testing.T) {
			events := []string{}
			var cancelled atomic.Bool
			log := func(event string) {
				events = append(events, event)
				if v.CancelAt != nil && event == *v.CancelAt {
					cancelled.Store(true)
				}
			}
			number := func(text *string) *uint64 {
				if text == nil {
					return nil
				}
				value, e := strconv.ParseUint(*text, 10, 64)
				if e != nil {
					t.Fatal(e)
				}
				return &value
			}
			m := s.Model[int, int, int]{InitialState: func() (int, error) { log("initial"); return 0, nil }, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], Step: func(state, input int) (s.Transition[int, int], error) {
				log(fmt.Sprintf("step:%d:%d", state, input))
				if v.Fixture == "diamond" {
					return s.Accept[int, int](input), nil
				}
				return s.Accept[int, int](state + input), nil
			}, CheckState: func(state int) ([]s.Check, error) {
				event := fmt.Sprintf("state:%d", state)
				log(event)
				if v.ErrorAt != nil && event == *v.ErrorAt {
					var prefix []s.Check
					if v.ErrorPrefix {
						prefix = []s.Check{s.Passed("prefix")}
					}
					return prefix, fmt.Errorf("fixture check error")
				}
				checks := []s.Check{s.Passed("ok")}
				if v.Skipped {
					checks = append(checks, s.Skipped("skip", "not run"))
				}
				if v.FailInitial && state == 0 || v.FailState != nil && state == *v.FailState {
					checks = append(checks, s.Failed("bad", "broken"))
				}
				return checks, nil
			}, CheckTransition: func(before, input int, tr s.Transition[int, int]) ([]s.Check, error) {
				event := fmt.Sprintf("transition:%d:%d:%d", before, input, tr.State)
				log(event)
				if v.ErrorAt != nil && event == *v.ErrorAt {
					var prefix []s.Check
					if v.ErrorPrefix {
						prefix = []s.Check{s.Passed("prefix")}
					}
					return prefix, fmt.Errorf("fixture check error")
				}
				return nil, nil
			}, EstimatedStateBytes: func(state int) (*uint64, error) {
				log(fmt.Sprintf("estimate:%d", state))
				if state >= len(v.Estimates) {
					return nil, fmt.Errorf("estimate fixture state out of range")
				}
				return number(v.Estimates[state]), nil
			}, HashState: func(state int) uint64 { log(fmt.Sprintf("hash:%d", state)); return 0 }, EqualStates: func(a, b int) bool { log(fmt.Sprintf("equal:%d:%d", a, b)); return a == b }, Inputs: func(state int) (s.Iterator[int], error) {
				log(fmt.Sprintf("inputs:%d", state))
				var inputs []int
				switch v.Fixture {
				case "chain":
					if state < 2 {
						inputs = []int{1}
					}
				case "empty":
				case "loop":
					inputs = []int{0, 0}
				case "diamond":
					if state == 0 {
						inputs = []int{1, 2, 1}
					} else if state == 1 || state == 2 {
						inputs = []int{3}
					}
				default:
					return nil, fmt.Errorf("unknown fixture")
				}
				index := 0
				return func() (int, bool, error) {
					if index == len(inputs) {
						log(fmt.Sprintf("next:%d:end", state))
						return 0, false, nil
					}
					value := inputs[index]
					index++
					log(fmt.Sprintf("next:%d:%d", state, value))
					return value, true, nil
				}, nil
			}}
			observer := s.SearchObserver[int, int, int]{NeedsHistory: func() bool { return v.NeedsHistory }, Initial: func(state int, _ []s.Check) error { log(fmt.Sprintf("observe_initial:%d", state)); return nil }, Transition: func(before, input int, tr s.Transition[int, int], _, _ []s.Check, history []int) error {
				parts := make([]string, len(history))
				for k, value := range history {
					parts[k] = strconv.Itoa(value)
				}
				text := "-"
				if len(parts) > 0 {
					text = strings.Join(parts, ",")
				}
				log(fmt.Sprintf("observe:%d:%d:%d:%s", before, input, tr.State, text))
				return nil
			}, CheckError: func(phase string, checks []s.Check, complete bool) error {
				log(fmt.Sprintf("check_error:%s:%t:%d", phase, complete, len(checks)))
				return nil
			}}
			limits := s.SearchLimits{Control: s.RunLimits{Cancellation: &cancelled}, MaxEstimatedStateBytes: number(v.MaxEstimatedStateBytes)}
			switch v.EntryControl {
			case "none":
			case "cancelled":
				cancelled.Store(true)
			case "deadline":
				zero := time.Duration(0)
				limits.Control.MaxDuration = &zero
			case "both":
				cancelled.Store(true)
				zero := time.Duration(0)
				limits.Control.MaxDuration = &zero
			default:
				t.Fatal("unknown entry control")
			}
			report, e := s.EnumerateObserved(m, s.SearchConfig{MaxStates: v.MaxStates, MaxTransitions: v.MaxTransitions, MaxDepth: v.MaxDepth}, limits, observer)
			if !reflect.DeepEqual(events, v.Events) {
				t.Fatalf("events\n%v\nexpected\n%v\nreport%+v error%v", events, v.Events, report, e)
			}
			if v.ErrorContains != nil {
				if e == nil || !strings.Contains(e.Error(), *v.ErrorContains) {
					t.Fatal("expected error", v.ErrorContains, e)
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			want := v.Expected
			if report.Termination != want.Termination || report.States != want.States || report.Transitions != want.Transitions || report.MaxDepthReached != want.MaxDepthReached || report.SkippedChecks != want.SkippedChecks || !reflect.DeepEqual(report.EstimatedRetainedStateBytes, number(want.EstimatedRetainedStateBytes)) {
				t.Fatalf("report %+v expected %+v", report, want)
			}
			if want.Witness == nil {
				if report.Failure != nil {
					t.Fatal(report.Failure)
				}
			} else if report.Failure == nil || !slices.Equal(report.Failure.Inputs, want.Witness) || len(report.Failure.Violations) != 1 || report.Failure.Violations[0].Phase != *want.FailedPhase || report.Failure.Violations[0].Check.ID != *want.FailedID {
				t.Fatal(report.Failure, want)
			}
		})
	}
}

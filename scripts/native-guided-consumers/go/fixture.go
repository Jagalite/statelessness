package main

import (
	"encoding/binary"
	"errors"
	"fmt"
	"slices"
	"strconv"
	"sync/atomic"
	"time"

	sl "github.com/Jagalite/statelessness/go"
)

type state [2]int64
type transition = sl.Transition[state, int64]
type generatedModel = sl.GenerateModel[state, int64, int64]

type rngRecord struct {
	Case   int      `json:"case"`
	State  state    `json:"state"`
	Values []string `json:"values"`
}

type checkBatch struct {
	phase  string
	checks []sl.Check
}

type fixture struct {
	spec           fixtureCase
	caseNumber     int
	calls          map[string]int
	cancelled      atomic.Bool
	trace          [][]any
	rng            []rngRecord
	checkBatches   []checkBatch
	lastCheckPhase string
}

func newFixture(spec fixtureCase) *fixture {
	f := &fixture{spec: spec, calls: make(map[string]int), trace: [][]any{}, rng: []rngRecord{}}
	f.cancelled.Store(spec.Control.Cancelled)
	return f
}

func (f *fixture) enter(label string, args ...any) {
	f.calls[label]++
	f.trace = append(f.trace, append([]any{label}, args...))
}

func (f *fixture) done(label string) {
	if cancel := f.spec.Control.CancelAfter; cancel != nil && cancel.Callback == label && cancel.Call == f.calls[label] {
		f.cancelled.Store(true)
	}
}

func (f *fixture) fault(label string) error {
	if fault := f.spec.Model.Fault; fault != nil && fault.Callback == label && fault.Call == f.calls[label] {
		return errors.New(fault.Message)
	}
	return nil
}

func (f *fixture) limits() sl.RunLimits {
	limits := sl.RunLimits{Cancellation: &f.cancelled}
	if f.spec.Control.ZeroDuration {
		zero := time.Duration(0)
		limits.MaxDuration = &zero
	}
	return limits
}

func (f *fixture) checks(phase string, s state) ([]sl.Check, *string) {
	checks := []sl.Check{}
	var callbackError *string
	for _, rule := range f.spec.Model.Checks {
		if rule.Phase == phase && rule.matches(f.caseNumber, s[0]) {
			for _, check := range rule.Checks {
				// The schema has already validated these descriptors.
				native, _ := check.native()
				checks = append(checks, native)
			}
			callbackError = rule.Error
			break
		}
	}
	if limit := f.spec.Model.FailTotal; limit != nil && callbackError == nil && phase != "Transition" && s[1] >= *limit {
		checks = append(checks, sl.Failed("limit", "total reached limit"))
	}
	return checks, callbackError
}

func (f *fixture) appendChecks(sink *sl.CheckSink, phase string, checks []sl.Check, callbackError *string) error {
	f.lastCheckPhase = phase
	f.checkBatches = append(f.checkBatches, checkBatch{phase, slices.Clone(checks)})
	if err := sink.Extend(checks); err != nil {
		return err
	}
	if callbackError != nil {
		return errors.New(*callbackError)
	}
	return nil
}

func (f *fixture) model() generatedModel {
	m := sl.Model[state, int64, int64]{
		CloneState: sl.ValueCopy[state], CloneInput: sl.ValueCopy[int64], CloneOutput: sl.ValueCopy[int64],
		EqualStates: func(a, b state) bool { return a == b },
		InitialState: func() (state, error) {
			f.caseNumber++
			f.enter("initial_state", f.caseNumber)
			defer f.done("initial_state")
			return state{}, f.fault("initial_state")
		},
		Step: func(before state, input int64) (transition, error) {
			f.enter("step", f.caseNumber, before, input)
			defer f.done("step")
			if err := f.fault("step"); err != nil {
				return transition{}, err
			}
			after := state{before[0] + 1, before[1] + input}
			disposition, err := f.spec.Model.Disposition.native()
			if err != nil {
				return transition{}, err
			}
			return transition{State: after, Outputs: []int64{after[1]}, Disposition: disposition}, nil
		},
		CheckStateInto: func(s state, sink *sl.CheckSink) error {
			phase := "State"
			if s[0] == 0 {
				phase = "InitialState"
			}
			checks, callbackError := f.checks(phase, s)
			f.enter("check_state", f.caseNumber, s, normalizeChecks(checks), callbackError)
			defer f.done("check_state")
			return f.appendChecks(sink, phase, checks, callbackError)
		},
		CheckTransitionInto: func(before state, input int64, after transition, sink *sl.CheckSink) error {
			checks, callbackError := f.checks("Transition", after.State)
			f.enter("check_transition", f.caseNumber, before, input, after.State, normalizeChecks(checks), callbackError)
			defer f.done("check_transition")
			return f.appendChecks(sink, "Transition", checks, callbackError)
		},
	}
	if f.spec.Feedback.Hash == "value" {
		m.HashState = func(s state) uint64 { return hashInts(s[:]) }
	} else {
		m.HashState = func(state) uint64 { return 0 }
	}
	return generatedModel{
		Model: m,
		Generate: func(s state, rng *sl.Rng) (int64, bool, error) {
			raw := make([]string, f.spec.Model.Draws)
			var last uint64
			for i := range raw {
				last = rng.NextU64()
				raw[i] = strconv.FormatUint(last, 10)
			}
			result := f.spec.Model.Fallback
			if table := f.spec.Model.Generate; len(table) > 0 {
				row := table[min(f.caseNumber-1, len(table)-1)]
				if s[0] >= 0 && s[0] < int64(len(row)) {
					result = row[s[0]]
				}
			}
			if modulus := f.spec.Model.RNGMod; result != nil && modulus != nil {
				value := int64(1 + last%*modulus)
				result = &value
			}
			f.enter("generate", f.caseNumber, s, result, slices.Clone(raw))
			defer f.done("generate")
			f.rng = append(f.rng, rngRecord{f.caseNumber, s, raw})
			if err := f.fault("generate"); err != nil {
				return 0, false, err
			}
			if result == nil {
				return 0, false, nil
			}
			return *result, true, nil
		},
		IsEnabled: func(s state, input int64) (bool, error) {
			enabled := input >= 0 && (f.spec.Model.Enabled != "successor" || input == s[1]+1)
			f.enter("is_enabled", f.caseNumber, s, input, enabled)
			defer f.done("is_enabled")
			return enabled, f.fault("is_enabled")
		},
		InputEqual:    func(a, b int64) (bool, error) { return a == b, nil },
		SimplerInputs: func(int64) (sl.Iterator[int64], error) { return sl.Values([]int64{}), nil },
	}
}

func (f *fixture) observer() sl.SearchObserver[state, int64, int64] {
	return sl.SearchObserver[state, int64, int64]{
		NeedsHistory: func() bool { return true },
		Initial: func(s state, checks []sl.Check) error {
			f.enter("observer_initial", f.caseNumber, s, normalizeChecks(checks))
			f.done("observer_initial")
			return nil
		},
		Transition: func(before state, input int64, after transition, cs, ct []sl.Check, history []int64) error {
			f.enter("observer_transition", f.caseNumber, before, input, after.State, nonnil(after.Outputs),
				normalizeChecks(cs), normalizeChecks(ct), nonnil(history), normalizeDisposition(after.Disposition))
			f.done("observer_transition")
			return nil
		},
		CheckError: func(phase string, checks []sl.Check, complete bool) error {
			f.enter("observer_check_error", f.caseNumber, pascal(phase), normalizeChecks(checks), complete)
			f.done("observer_check_error")
			return nil
		},
	}
}

func fixtureCodec() *sl.Codec[state, int64, int64] {
	return &sl.Codec[state, int64, int64]{
		Metadata: func() (sl.Metadata, error) { return sl.Identity("guided-table-walk", "table-walk-v1"), nil },
		EncodeState: func(s state) ([]byte, error) {
			data := make([]byte, 16)
			binary.LittleEndian.PutUint64(data, uint64(s[0]))
			binary.LittleEndian.PutUint64(data[8:], uint64(s[1]))
			return data, nil
		},
		DecodeState: decodeState,
		EncodeInput: encodeInt, DecodeInput: decodeInt, EncodeOutput: encodeInt,
	}
}

func encodeInt(value int64) ([]byte, error) {
	data := make([]byte, 8)
	binary.LittleEndian.PutUint64(data, uint64(value))
	return data, nil
}
func decodeInt(data []byte) (int64, error) {
	if len(data) != 8 {
		return 0, fmt.Errorf("table-walk integer requires exactly 8 bytes")
	}
	return int64(binary.LittleEndian.Uint64(data)), nil
}
func decodeState(data []byte) (state, error) {
	if len(data) != 16 {
		return state{}, fmt.Errorf("table-walk state requires exactly 16 bytes")
	}
	return state{int64(binary.LittleEndian.Uint64(data)), int64(binary.LittleEndian.Uint64(data[8:]))}, nil
}

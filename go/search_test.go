package statelessness_test

import (
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strconv"
	"sync/atomic"
	"testing"
	"time"
)

func integerSearch(t *testing.T, fixture string, executed *[][]int) s.GenerateModel[int, int, int] {
	t.Helper()
	m := s.Model[int, int, int]{InitialState: func() (int, error) {
		if executed != nil {
			*executed = append(*executed, []int{})
		}
		return 0, nil
	}, Step: func(state, input int) (s.Transition[int, int], error) {
		if executed != nil {
			index := len(*executed) - 1
			(*executed)[index] = append((*executed)[index], input)
		}
		if fixture == "causal" || fixture == "phase" || fixture == "cycle" {
			return s.Accept[int, int](input), nil
		}
		return s.Accept[int, int](state + input), nil
	}, CheckState: func(state int) ([]s.Check, error) {
		id := "bad"
		bad := fixture == "initial" || fixture == "threshold" && state >= 3 || fixture == "causal" && state == 2 || fixture == "cycle" && state != 0 || fixture == "phase" && state == 1
		if fixture == "phase" {
			id = "same"
		}
		if bad {
			return []s.Check{s.Failed(id, "fixture")}, nil
		}
		return nil, nil
	}, CheckTransition: func(_ int, _ int, tr s.Transition[int, int]) ([]s.Check, error) {
		if fixture == "phase" && tr.State == 2 {
			return []s.Check{s.Failed("same", "fixture")}, nil
		}
		return nil, nil
	}, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], Inputs: func(state int) (s.Iterator[int], error) {
		switch fixture {
		case "empty":
			return s.Values([]int{}), nil
		case "binary", "phase", "cycle":
			return s.Values([]int{1, 2}), nil
		case "causal":
			if state == 0 {
				return s.Values([]int{1}), nil
			}
			if state == 1 {
				return s.Values([]int{2}), nil
			}
			return s.Values([]int{}), nil
		default:
			return s.Values([]int{1}), nil
		}
	}}
	g, e := s.Auto(m, func(a, b int) (bool, error) { return a == b, nil }, s.AutoOptions{MaxCandidates: 100})
	if e != nil {
		t.Fatal(e)
	}
	if fixture == "digits" {
		g.Generate = func(_ int, rng *s.Rng) (int, bool, error) { return int(rng.NextU64() % 10), true, nil }
		g.IsEnabled = nil
	}
	g.SimplerInputs = func(input int) (s.Iterator[int], error) {
		if fixture == "phase" && input == 1 {
			return s.Values([]int{2}), nil
		}
		if fixture == "cycle" {
			return s.Values([]int{3 - input}), nil
		}
		return s.Values([]int{}), nil
	}
	return g
}
func TestSearchGolden(t *testing.T) {
	path := os.Getenv("STATELESS_SEARCH_CORPUS")
	if path == "" {
		path = "../conformance/search-v1.tsv"
	}
	f, e := os.Open(path)
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	reader := csv.NewReader(f)
	reader.Comma = '\t'
	rows, e := reader.ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	if len(rows) < 20 {
		t.Fatal("missing corpus cases")
	}
	historyPath := os.Getenv("STATELESS_SEARCH_HISTORY")
	if historyPath == "" {
		historyPath = filepath.Join(filepath.Dir(path), "search-v1-history.json")
	}
	historyBytes, err := os.ReadFile(historyPath)
	if err != nil {
		t.Fatal(err)
	}
	var histories map[string][]goldenSearchAttempt
	if err = json.Unmarshal(historyBytes, &histories); err != nil {
		t.Fatal(err)
	}
	for _, values := range rows[1:] {
		row := map[string]string{}
		for k, key := range rows[0] {
			row[key] = values[k]
		}
		t.Run(row["case"], func(t *testing.T) {
			integer := func(key string) int {
				v, e := strconv.Atoi(row[key])
				if e != nil {
					t.Fatal(key, e)
				}
				return v
			}
			number := func(key string) uint64 {
				v, e := strconv.ParseUint(row[key], 10, 64)
				if e != nil {
					t.Fatal(key, e)
				}
				return v
			}
			controls := s.RunLimits{}
			switch row["control"] {
			case "cancelled":
				var flag atomic.Bool
				flag.Store(true)
				controls.Cancellation = &flag
			case "deadline":
				zero := time.Duration(0)
				controls.MaxDuration = &zero
			}
			var failure *s.Failure[int]
			if row["operation"] == "fuzz" {
				executed := [][]int{}
				g := integerSearch(t, row["fixture"], &executed)
				if row["fixture"] != "digits" {
					generated, err := s.Auto(g.Model, g.InputEqual, s.AutoOptions{MaxCandidates: integer("max_candidates")})
					if err != nil {
						if row["error"] == "invalid_candidate_budget" {
							var configError *s.ConfigError
							if !errors.As(err, &configError) {
								t.Fatal("wrong candidate budget error", err)
							}
							return
						}
						t.Fatal(err)
					}
					g.Generate, g.IsEnabled = generated.Generate, generated.IsEnabled
				}
				r, e := s.FuzzWithLimits(g, s.FuzzConfig{Seed: number("seed"), Cases: integer("cases"), MaxSteps: integer("max_steps"), MaxTransitions: number("max_transitions"), MutationPercent: integer("mutation_percent")}, controls)
				if row["error"] != "-" {
					if e == nil {
						t.Fatal("expected fuzz error")
					}
					var modelError *s.ModelError
					if row["error"] != "auto_candidate_limit" || !errors.As(e, &modelError) {
						t.Fatal("wrong fuzz error", e)
					}
					return
				}
				if e != nil {
					t.Fatal(e)
				}
				if r.Termination != row["termination"] || r.Cases != integer("started_cases") || r.Transitions != number("transitions") || r.SkippedChecks != 0 {
					t.Fatalf("report %+v expected %v", r, row)
				}
				failure = r.Failure
				var expected [][]int
				if e = json.Unmarshal([]byte(row["trace"]), &expected); e != nil {
					t.Fatal(e)
				}
				if !reflect.DeepEqual(executed, expected) {
					t.Fatal(executed, expected)
				}
			} else {
				g := integerSearch(t, row["fixture"], nil)
				var inputs []int
				if e = json.Unmarshal([]byte(row["original"]), &inputs); e != nil {
					t.Fatal(e)
				}
				original := s.Failure[int]{Inputs: inputs, Violations: []s.Violation{{Phase: row["target_phase"], Check: s.Failed(row["target_id"], "supplied")}}}
				limits := s.ShrinkLimits{Control: controls}
				if row["max_replayed_transitions"] != "-" {
					budget := number("max_replayed_transitions")
					limits.MaxReplayedTransitions = &budget
				}
				r, e := s.ShrinkWithLimits(g, original, s.ShrinkConfig{MaxAttempts: integer("max_attempts")}, limits)
				if row["error"] != "-" {
					if e == nil {
						t.Fatal("expected shrink error")
					}
					var configError *s.ConfigError
					if row["error"] != "invalid_attempt_budget" || !errors.As(e, &configError) {
						t.Fatal("wrong shrink error", e)
					}
					return
				}
				if e != nil {
					t.Fatal(e)
				}
				if r.Termination != row["termination"] || r.Attempts != integer("attempts") || r.ReplayedTransitions != number("transitions") || strconv.FormatBool(r.ValidatedOriginal) != row["validated_original"] || len(r.History) != r.Attempts {
					t.Fatalf("report %+v expected %v", r, row)
				}
				expectedHistory, ok := histories[row["case"]]
				if !ok {
					t.Fatal("missing shrink history")
				}
				actualHistory := make([]goldenSearchAttempt, len(r.History))
				for k, h := range r.History {
					actualHistory[k] = goldenSearchAttempt{Attempt: h.Attempt, CandidateLength: h.CandidateLength, Outcome: h.Outcome, Operation: goldenSearchOperation{Kind: h.Operation.Kind, Start: h.Operation.Start, End: h.Operation.End, Index: h.Operation.Index, Alternative: h.Operation.Alternative}}
				}
				if !reflect.DeepEqual(actualHistory, expectedHistory) {
					t.Fatalf("history %+v expected %+v", actualHistory, expectedHistory)
				}
				failure = &r.Minimized
			}
			if row["witness"] == "-" {
				if failure != nil {
					t.Fatal(failure)
				}
			} else {
				if failure == nil {
					t.Fatal("missing failure")
				}
				var expected []int
				if e = json.Unmarshal([]byte(row["witness"]), &expected); e != nil {
					t.Fatal(e)
				}
				if !slices.Equal(failure.Inputs, expected) || len(failure.Violations) != 1 || failure.Violations[0].Phase != row["failed_phase"] || failure.Violations[0].Check.ID != row["failed_id"] {
					t.Fatal(failure, row)
				}
			}
		})
	}
}

func TestAutoReservoirRollbackCapAndEarlyMembership(t *testing.T) {
	g := integerSearch(t, "binary", nil)
	rng := s.NewRng(0)
	v, ok, e := g.Generate(0, rng)
	if e != nil || !ok || v != 1 {
		t.Fatal(v, ok, e)
	}
	v, ok, e = g.Generate(0, rng)
	if e != nil || !ok || v != 2 {
		t.Fatal(v, ok, e)
	}
	m := g.Model
	pulls := 0
	m.Inputs = func(int) (s.Iterator[int], error) {
		i := 0
		return func() (int, bool, error) { pulls++; i++; return i, true, nil }, nil
	}
	auto, e := s.Auto(m, g.InputEqual, s.AutoOptions{MaxCandidates: 2})
	if e != nil {
		t.Fatal(e)
	}
	rng = s.NewRng(123)
	if v, ok, e = auto.Generate(0, rng); e == nil || ok || v != 0 || pulls != 3 {
		t.Fatal(v, ok, e, pulls)
	}
	if rng.NextU64() != s.NewRng(123).NextU64() {
		t.Fatal("RNG advanced after overflow")
	}
	pulls = 0
	enabled, e := auto.IsEnabled(0, 1)
	if e != nil || !enabled || pulls != 1 {
		t.Fatal(enabled, e, pulls)
	}
	sentinel := errors.New("iterator failed")
	m.Inputs = func(int) (s.Iterator[int], error) {
		count := 0
		return func() (int, bool, error) {
			count++
			if count == 3 {
				return 0, false, sentinel
			}
			return count, true, nil
		}, nil
	}
	auto, e = s.Auto(m, g.InputEqual, s.AutoOptions{MaxCandidates: 10})
	if e != nil {
		t.Fatal(e)
	}
	rng = s.NewRng(9)
	if _, _, e = auto.Generate(0, rng); e == nil {
		t.Fatal("iterator error lost")
	}
	if rng.NextU64() != s.NewRng(9).NextU64() {
		t.Fatal("RNG advanced after error")
	}
	for _, fixture := range []string{"empty", "singleton"} {
		g := integerSearch(t, fixture, nil)
		rng := s.NewRng(8)
		if _, _, e = g.Generate(0, rng); e != nil {
			t.Fatal(e)
		}
		if rng.NextU64() != s.NewRng(8).NextU64() {
			t.Fatal("empty/singleton draw")
		}
	}
}

func TestSearchControlsErrorsAndCompleteFailureBatch(t *testing.T) {
	var cancelled atomic.Bool
	g := integerSearch(t, "singleton", nil)
	g.CheckState = func(state int) ([]s.Check, error) {
		if state > 0 {
			cancelled.Store(true)
			return []s.Check{s.Failed("one", "a"), s.Skipped("skip", "b"), s.Failed("two", "c")}, nil
		}
		return nil, nil
	}
	g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) {
		return []s.Check{s.Failed("three", "d")}, nil
	}
	r, e := s.FuzzWithLimits(g, s.FuzzConfig{Cases: 1, MaxSteps: 2, MaxTransitions: 2}, s.RunLimits{Cancellation: &cancelled})
	if e != nil || r.Termination != "failure_found" || len(r.Failure.Violations) != 3 || r.SkippedChecks != 1 {
		t.Fatal(r, e)
	}
	for k, v := range r.Failure.Violations {
		expected := []string{"one", "two", "three"}
		if v.Check.ID != expected[k] {
			t.Fatal(r)
		}
	}
	cancelled.Store(false)
	g = integerSearch(t, "singleton", nil)
	g.Generate = func(int, *s.Rng) (int, bool, error) {
		cancelled.Store(true)
		return 0, false, fmt.Errorf("callback error wins")
	}
	if _, e = s.FuzzWithLimits(g, s.FuzzConfig{Cases: 1, MaxSteps: 2, MaxTransitions: 2}, s.RunLimits{Cancellation: &cancelled}); e == nil {
		t.Fatal("error became cancellation")
	}
	g = integerSearch(t, "singleton", nil)
	g.Generate = func(int, *s.Rng) (int, bool, error) { return 1, true, nil }
	g.IsEnabled = func(int, int) (bool, error) { return false, nil }
	if _, e = s.Fuzz(g, s.FuzzConfig{Cases: 1, MaxSteps: 2, MaxTransitions: 2}); e == nil {
		t.Fatal("disabled generation accepted")
	}
	g.Generate = func(int, *s.Rng) (int, bool, error) { panic("generation panic") }
	if _, e = s.Fuzz(g, s.FuzzConfig{Cases: 1, MaxSteps: 2, MaxTransitions: 2}); e == nil {
		t.Fatal("panic escaped")
	}
	g = integerSearch(t, "threshold", nil)
	original := s.Failure[int]{Inputs: []int{1, 1, 1}, Violations: []s.Violation{{Phase: "state", Check: s.Failed("bad", "provided")}}}
	budget := uint64(2)
	sh, e := s.ShrinkWithLimits(g, original, s.DefaultShrinkConfig(), s.ShrinkLimits{MaxReplayedTransitions: &budget})
	if e != nil || sh.ValidatedOriginal || sh.Termination != "transition_limit" || sh.ReplayedTransitions != 2 || sh.History[0].Outcome != "resource_limit" || !slices.Equal(sh.Minimized.Inputs, original.Inputs) {
		t.Fatal(sh, e)
	}
	original.Violations[0].Phase = "transition"
	if _, e = s.Shrink(g, original, s.DefaultShrinkConfig()); e == nil {
		t.Fatal("wrong target phase accepted")
	}
}
func TestShrinkHistoryMatchesDeletionAndCycle(t *testing.T) {
	g := integerSearch(t, "threshold", nil)
	original := s.Failure[int]{Inputs: []int{1, 1, 1}, Violations: []s.Violation{{Phase: "state", Check: s.Failed("bad", "provided")}}}
	report, e := s.Shrink(g, original, s.DefaultShrinkConfig())
	if e != nil {
		t.Fatal(e)
	}
	lengths := []int{3, 1, 2, 2, 2, 2}
	ranges := [][2]int{{0, 0}, {0, 2}, {2, 3}, {0, 1}, {1, 2}, {2, 3}}
	for k, h := range report.History {
		if h.Attempt != k+1 || h.CandidateLength != lengths[k] {
			t.Fatal(report.History)
		}
		if k > 0 && (h.Operation.Kind != "remove" || h.Operation.Start != ranges[k][0] || h.Operation.End != ranges[k][1] || h.Outcome != "no_failure") {
			t.Fatal(report.History)
		}
	}
	g = integerSearch(t, "cycle", nil)
	original.Inputs = []int{2}
	report, e = s.Shrink(g, original, s.DefaultShrinkConfig())
	if e != nil || report.Attempts != 4 || report.History[2].Operation.Kind != "simplify" || report.History[2].Outcome != "retained" || report.History[2].Operation.Alternative != 0 {
		t.Fatal(report, e)
	}
}
func TestMutableSearchInputsAreSnapshots(t *testing.T) {
	shared := []int{1}
	g := s.GenerateModel[int, []int, int]{Model: s.Model[int, []int, int]{InitialState: func() (int, error) { return 0, nil }, Step: func(state int, input []int) (s.Transition[int, int], error) {
		input[0] = 100
		return s.Accept[int, int](state + 1), nil
	}, CheckState: func(state int) ([]s.Check, error) {
		if state == 2 {
			return []s.Check{s.Failed("bad", "two")}, nil
		}
		return nil, nil
	}, CloneState: s.ValueCopy[int], CloneInput: slices.Clone[[]int], CloneOutput: s.ValueCopy[int]}, Generate: func(int, *s.Rng) ([]int, bool, error) { return shared, true, nil }, IsEnabled: func(_ int, input []int) (bool, error) { input[0] = 50; return true, nil }, InputEqual: func(a, b []int) (bool, error) { return slices.Equal(a, b), nil }}
	report, e := s.Fuzz(g, s.FuzzConfig{Cases: 1, MaxSteps: 2, MaxTransitions: 10})
	if e != nil || report.Failure == nil || shared[0] != 1 || report.Failure.Inputs[0][0] != 1 {
		t.Fatal(report, e, shared)
	}
	shared[0] = 9
	if report.Failure.Inputs[1][0] != 1 {
		t.Fatal("witness aliases generator")
	}
	original := *report.Failure
	sh, e := s.Shrink(g, original, s.DefaultShrinkConfig())
	if e != nil {
		t.Fatal(e)
	}
	original.Inputs[0][0] = 8
	if sh.Original.Inputs[0][0] != 1 || sh.Minimized.Inputs[0][0] != 1 {
		t.Fatal("shrink report aliases caller")
	}
}

type goldenSearchOperation struct {
	Kind        string `json:"kind"`
	Start       int    `json:"start"`
	End         int    `json:"end"`
	Index       int    `json:"index"`
	Alternative int    `json:"alternative"`
}
type goldenSearchAttempt struct {
	Attempt         int                   `json:"attempt"`
	CandidateLength int                   `json:"candidate_length"`
	Operation       goldenSearchOperation `json:"operation"`
	Outcome         string                `json:"outcome"`
}

func TestFuzzMutationExactDrawsAndDisabledReuse(t *testing.T) {
	executed := [][]int{}
	g := integerSearch(t, "digits", &executed)
	var generated []int
	g.Generate = func(_ int, rng *s.Rng) (int, bool, error) {
		value := int(rng.NextU64() % 10)
		generated = append(generated, value)
		return value, true, nil
	}
	r, e := s.Fuzz(g, s.FuzzConfig{Seed: 0, Cases: 3, MaxSteps: 3, MaxTransitions: 9, MutationPercent: 100})
	if e != nil || r.Termination != "cases_completed" {
		t.Fatal(r, e)
	}
	if !reflect.DeepEqual(executed, [][]int{{5, 0, 9}, {5, 9, 0}, {5, 3, 1}}) || !slices.Equal(generated, []int{5, 0, 9, 0, 3, 1}) {
		t.Fatal(executed, generated)
	}
	// Case two's mutated candidate starts [5,9]. Disable the reused 5 once,
	// forcing state-aware generation at position zero and consuming the next word.
	executed = [][]int{}
	g = integerSearch(t, "digits", &executed)
	var enabled []int
	rejected := false
	g.IsEnabled = func(_ int, input int) (bool, error) {
		enabled = append(enabled, input)
		if len(executed) == 2 && !rejected && input == 5 {
			rejected = true
			return false, nil
		}
		return true, nil
	}
	r, e = s.Fuzz(g, s.FuzzConfig{Seed: 0, Cases: 2, MaxSteps: 3, MaxTransitions: 6, MutationPercent: 100})
	if e != nil || r.Termination != "cases_completed" || !rejected {
		t.Fatal(r, e)
	}
	if !reflect.DeepEqual(executed, [][]int{{5, 0, 9}, {0, 9, 9}}) || !slices.Equal(enabled, []int{5, 0, 9, 5, 0, 9, 9}) {
		t.Fatal(executed, enabled)
	}
}

func TestShrinkReplayBudgetSnapshotsCallerValue(t *testing.T) {
	for _, test := range []struct {
		name             string
		initial, changed uint64
		transitions      uint64
		validated        bool
	}{
		{"raising-entry-budget-does-not-admit-more-replay", 2, 100, 2, false},
		{"lowering-entry-budget-does-not-stop-validation", 3, 0, 3, true},
	} {
		t.Run(test.name, func(t *testing.T) {
			maximum := test.initial
			g := integerSearch(t, "threshold", nil)
			g.InitialState = func() (int, error) { maximum = test.changed; return 0, nil }
			original := s.Failure[int]{Inputs: []int{1, 1, 1}, Violations: []s.Violation{{Phase: "state", Check: s.Failed("bad", "supplied")}}}
			report, e := s.ShrinkWithLimits(g, original, s.DefaultShrinkConfig(), s.ShrinkLimits{MaxReplayedTransitions: &maximum})
			if e != nil || report.Termination != "transition_limit" || report.ReplayedTransitions != test.transitions || report.ValidatedOriginal != test.validated || maximum != test.changed {
				t.Fatal(report, e, maximum)
			}
			if !slices.Equal(report.Minimized.Inputs, []int{1, 1, 1}) {
				t.Fatal("supplied evidence changed", report)
			}
		})
	}
}

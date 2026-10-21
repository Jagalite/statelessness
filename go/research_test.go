package statelessness

import (
	"errors"
	"math"
	"strings"
	"testing"
)

func researchTestProposal() ResearchProposal {
	return ResearchProposal{Name: "toggle", Version: 1, Build: "fixture", Assumptions: ResearchAssumptions{true, "closed", "exact", "ordered", "named", "ordered", "explicit", "logical"}, RequiredProperties: []RequiredProperty{{"state", PropertyState}, {"transition", PropertyTransition}}}
}
func researchTestModel(steps *int) Model[int, int, int] {
	return Model[int, int, int]{InitialState: func() (int, error) { return 0, nil }, Step: func(s, i int) (Transition[int, int], error) { *steps++; return Accept(1-s, 1, 2), nil }, CloneState: ValueCopy[int], CloneInput: ValueCopy[int], CloneOutput: ValueCopy[int], EqualStates: func(a, b int) bool { return a == b }, Inputs: func(int) (Iterator[int], error) { return Values([]int{0}), nil }, CheckState: func(int) ([]Check, error) { return []Check{Passed("state")}, nil }, CheckTransition: func(int, int, Transition[int, int]) ([]Check, error) { return []Check{Passed("transition")}, nil }}
}
func researchTestMetadata() (Metadata, error) { return Identity("research", "test"), nil }
func researchTestEqual() ResearchEquality[int, int] {
	return ResearchEquality[int, int]{func(a, b int) bool { return a == b }, func(a, b int) bool { return a == b }}
}
func researchEnabled() ResearchConfig { c := DefaultResearchConfig(); c.Enabled = true; return c }
func TestResearchDisabledAndAssumptionAdmission(t *testing.T) {
	steps, initial, projection, metadata := 0, 0, 0, 0
	m := researchTestModel(&steps)
	m.InitialState = func() (int, error) { initial++; return 0, nil }
	project := func(int) (int, error) { projection++; return 0, nil }
	identity := func() (Metadata, error) { metadata++; return researchTestMetadata() }
	p := researchTestProposal()
	r, e := CompareEquivalence(m, []int{0, 1}, p, DefaultResearchConfig(), project, researchTestEqual(), identity)
	if e != nil || r.Status != ExperimentDisabled || metadata != 1 || initial != 0 || steps != 0 || projection != 0 {
		t.Fatalf("%+v %v counts=%d/%d/%d/%d", r, e, metadata, initial, steps, projection)
	}
	p.Assumptions.Established = false
	r, e = CompareEquivalence(m, []int{0, 1}, p, researchEnabled(), project, researchTestEqual(), identity)
	if e != nil || r.Status != ExperimentAssumptionsNotEstablished || metadata != 2 || initial != 0 {
		t.Fatalf("%+v %v", r, e)
	}
	p.Assumptions.Effects = " "
	_, e = CompareEquivalence(m, nil, p, DefaultResearchConfig(), project, researchTestEqual(), identity)
	if e == nil || metadata != 2 {
		t.Fatal("invalid proposal ran metadata")
	}
}
func TestResearchFiniteToggleAndOrderedEffects(t *testing.T) {
	steps := 0
	m := researchTestModel(&steps)
	p := researchTestProposal()
	project := func(int) (int, error) { return 0, nil }
	r, e := CompareEquivalence(m, []int{0, 1}, p, researchEnabled(), project, researchTestEqual(), researchTestMetadata)
	if e != nil || r.Status != ExperimentAgreesWithinDeclaredFixture || steps != 2 || r.MaterializedEdges != 2 || len(r.Exact.States) != 2 || len(r.Reduced.States) != 1 {
		t.Fatalf("%+v %v", r, e)
	}
	if QualificationParameters(*r)["research.production_pruning_enabled"] != "false" {
		t.Fatal("pruning qualification")
	}
	m.Step = func(s, i int) (Transition[int, int], error) {
		if s == 1 {
			return Accept(0, 2, 1), nil
		}
		return Accept(1, 1, 2), nil
	}
	r, e = CompareEquivalence(m, []int{0, 1}, p, researchEnabled(), project, researchTestEqual(), researchTestMetadata)
	if e != nil || r.Status != ExperimentCounterexample || r.Counterexamples[0].Reason != "ordered effects differ" || len(r.Counterexamples[0].RightPrefix) != 1 {
		t.Fatalf("%+v %v", r, e)
	}
}
func TestResearchMaterializesUnreachableAndErrorPrecedence(t *testing.T) {
	steps := 0
	m := researchTestModel(&steps)
	projectCalls := 0
	project := func(int) (int, error) { projectCalls++; return 0, nil }
	m.Step = func(s, i int) (Transition[int, int], error) {
		steps++
		if s == 2 {
			return Transition[int, int]{}, errors.New("unreachable reducer")
		}
		return Accept[int, int](1 - s), nil
	}
	r, e := CompareEquivalence(m, []int{0, 1, 2}, researchTestProposal(), researchEnabled(), project, researchTestEqual(), researchTestMetadata)
	if e != nil || r.Status != ExperimentIncomplete || steps != 3 || r.MaterializedEdges != 2 || projectCalls != 0 || !strings.Contains(r.Limitation, "unreachable reducer") {
		t.Fatalf("%+v %v", r, e)
	}
	m.Step = func(s, i int) (Transition[int, int], error) { return Accept[int, int](99), nil }
	m.CheckTransition = func(int, int, Transition[int, int]) ([]Check, error) {
		t.Fatal("edge check before closed-domain validation")
		return nil, nil
	}
	r, e = CompareEquivalence(m, []int{0, 1}, researchTestProposal(), researchEnabled(), project, researchTestEqual(), researchTestMetadata)
	if e != nil || r.MaterializedEdges != 1 || !strings.Contains(r.Limitation, "successor is outside") {
		t.Fatalf("%+v %v", r, e)
	}
}
func TestResearchPhaseWitnessesAndCounterexampleBeatsPairLimit(t *testing.T) {
	steps := 0
	m := researchTestModel(&steps)
	m.Step = func(s, i int) (Transition[int, int], error) { return Accept[int, int]((s + 1) % 3), nil }
	m.CheckState = func(s int) ([]Check, error) {
		if s == 2 {
			return []Check{Failed("same", "state failure")}, nil
		}
		return []Check{Passed("same")}, nil
	}
	m.CheckTransition = func(s, i int, tr Transition[int, int]) ([]Check, error) {
		if s == 1 {
			return []Check{Failed("same", "edge failure")}, nil
		}
		return []Check{Passed("same")}, nil
	}
	p := researchTestProposal()
	p.RequiredProperties = []RequiredProperty{{"same", PropertyState}, {"same", PropertyTransition}}
	c := researchEnabled()
	c.MaxEquivalencePairs = 1
	r, e := CompareEquivalence(m, []int{0, 1, 2}, p, c, func(int) (int, error) { return 0, nil }, researchTestEqual(), researchTestMetadata)
	if e != nil || r.Status != ExperimentCounterexample || r.ComparedPairs != 1 || len(r.Exact.Violations) != 2 || len(r.MissingViolations) != 2 {
		t.Fatalf("%+v %v", r, e)
	}
	for _, v := range r.Exact.Violations {
		if len(v.Inputs) != 2 {
			t.Fatalf("non-shortest witness: %+v", v)
		}
	}
	if r.Exact.ObservedProperties[0].Phase != PropertyState || r.Exact.ObservedProperties[1].Phase != PropertyTransition {
		t.Fatal("property sorting")
	}
}
func TestResearchLimitsSkippedAndMissingNeverQualify(t *testing.T) {
	for _, kind := range []string{"state", "edge", "pair", "skipped", "missing", "empty-properties"} {
		t.Run(kind, func(t *testing.T) {
			steps := 0
			m := researchTestModel(&steps)
			c := researchEnabled()
			p := researchTestProposal()
			switch kind {
			case "state":
				c.MaxStates = 1
			case "edge":
				c.MaxEdges = 1
			case "pair":
				c.MaxEquivalencePairs = 0
			case "skipped":
				m.CheckState = func(int) ([]Check, error) { return []Check{Skipped("state", "not run")}, nil }
			case "missing":
				p.RequiredProperties = append(p.RequiredProperties, RequiredProperty{"absent", PropertyState})
			case "empty-properties":
				p.RequiredProperties = nil
			}
			r, e := CompareEquivalence(m, []int{0, 1}, p, c, func(int) (int, error) { return 0, nil }, researchTestEqual(), researchTestMetadata)
			if e != nil || r.Status != ExperimentIncomplete {
				t.Fatalf("%+v %v", r, e)
			}
		})
	}
}

func TestResearchOwnsMutableDomainEdgesAndWitnesses(t *testing.T) {
	clone := func(v []int) []int { return append([]int{}, v...) }
	domain := [][]int{{0}, {1}}
	sharedInput := []int{7}
	sharedOutput := []int{9}
	steps := 0
	m := Model[[]int, []int, []int]{
		CloneState: clone, CloneInput: clone, CloneOutput: clone,
		InitialState: func() ([]int, error) { return []int{0}, nil },
		EqualStates:  func(a, b []int) bool { same := a[0] == b[0]; a[0] = 77; b[0] = 88; return same },
		Inputs:       func(state []int) (Iterator[[]int], error) { state[0] = 99; return Values([][]int{sharedInput}), nil },
		Step: func(s, i []int) (Transition[[]int, []int], error) {
			steps++
			next := 1 - s[0]
			s[0] = 99
			i[0] = 99
			return Accept([]int{next}, sharedOutput), nil
		},
		CheckState: func(s []int) ([]Check, error) { s[0] = 99; return []Check{Failed("state", "failure")}, nil },
		CheckTransition: func(s, i []int, tr Transition[[]int, []int]) ([]Check, error) {
			s[0] = 99
			i[0] = 99
			tr.Outputs[0][0] = 99
			return []Check{Failed("transition", "failure")}, nil
		},
	}
	equality := ResearchEquality[[]int, []int]{Input: func(a, b []int) bool { same := a[0] == b[0]; a[0] = 99; b[0] = 99; return same }, Output: func(a, b []int) bool { same := a[0] == b[0]; a[0] = 99; b[0] = 99; return same }}
	r, e := CompareEquivalence(m, domain, researchTestProposal(), researchEnabled(), func(s []int) (int, error) { s[0] = 99; return 0, nil }, equality, researchTestMetadata)
	if e != nil || r.Status != ExperimentAgreesWithinDeclaredFixture || steps != 2 || domain[0][0] != 0 || domain[1][0] != 1 || sharedInput[0] != 7 || sharedOutput[0] != 9 {
		t.Fatalf("report=%+v error=%v domain=%v input=%v output=%v", r, e, domain, sharedInput, sharedOutput)
	}
	exact := r.Exact.Violations[1].Inputs
	reduced := r.Reduced.Violations[1].Inputs
	exact[0][0] = 123
	if reduced[0][0] != 7 {
		t.Fatal("report arms share witness input")
	}
}

func TestResearchCounterexampleReasonOrder(t *testing.T) {
	for _, reason := range []string{"state checks differ", "enabled input counts differ", "enabled input labels or order differ", "ordered effects differ", "transition disposition differs", "transition checks differ", "successor equivalence classes differ"} {
		t.Run(reason, func(t *testing.T) {
			steps := 0
			m := researchTestModel(&steps)
			// State 0 reaches every declared state; the first compared class pair is 0,1.
			m.Inputs = func(s int) (Iterator[int], error) {
				if reason == "enabled input counts differ" && s == 1 {
					return Values([]int{0}), nil
				}
				if reason == "enabled input labels or order differ" && s == 1 {
					return Values([]int{1, 0}), nil
				}
				return Values([]int{0, 1}), nil
			}
			m.Step = func(s, i int) (Transition[int, int], error) {
				target := 1 + i
				tr := Accept(target, 1, 2)
				if s == 1 {
					if reason == "ordered effects differ" {
						tr.Outputs = []int{2, 1}
					}
					if reason == "transition disposition differs" {
						tr.Disposition = Rejected("reason")
					}
					if reason == "successor equivalence classes differ" {
						tr.State = 2
					}
				}
				return tr, nil
			}
			m.CheckState = func(s int) ([]Check, error) {
				if s == 1 && reason == "state checks differ" {
					return []Check{Failed("state", "changed")}, nil
				}
				return []Check{Passed("state")}, nil
			}
			m.CheckTransition = func(s, i int, tr Transition[int, int]) ([]Check, error) {
				if s == 1 && reason == "transition checks differ" {
					return []Check{Failed("transition", "changed")}, nil
				}
				return []Check{Passed("transition")}, nil
			}
			r, e := CompareEquivalence(m, []int{0, 1, 2}, researchTestProposal(), researchEnabled(), func(s int) (int, error) {
				if s == 2 {
					return 1, nil
				}
				return 0, nil
			}, researchTestEqual(), researchTestMetadata)
			if e != nil || r.Status != ExperimentCounterexample || len(r.Counterexamples) != 1 || r.Counterexamples[0].Reason != reason {
				t.Fatalf("%+v %v", r, e)
			}
		})
	}
}

func TestResearchRejectsNonReflexiveProjectionBeforeTraversal(t *testing.T) {
	steps := 0
	m := researchTestModel(&steps)
	m.Step = func(s, i int) (Transition[int, int], error) { steps++; return Accept[int, int](s), nil }
	r, e := CompareEquivalence(m, []int{0}, researchTestProposal(), researchEnabled(), func(int) (float64, error) { return math.NaN(), nil }, researchTestEqual(), researchTestMetadata)
	if e != nil || r.Status != ExperimentIncomplete || r.Exact != nil || r.Reduced != nil || steps != 1 || !strings.Contains(r.Limitation, "not reflexive") {
		t.Fatalf("%+v %v", r, e)
	}
}

func TestResearchPopulationProjectionLosesReplayableFailure(t *testing.T) {
	const (
		left    = 1
		right   = 2
		observe = 0
	)
	m := Model[int, int, Unit]{InitialState: func() (int, error) { return 0, nil }, CloneState: ValueCopy[int], CloneInput: ValueCopy[int], CloneOutput: ValueCopy[Unit], EqualStates: func(a, b int) bool { return a == b }, Inputs: func(int) (Iterator[int], error) { return Values([]int{left, right, observe}), nil }, Step: func(s, i int) (Transition[int, Unit], error) { return Accept[int, Unit](s | i), nil }, CheckState: func(int) ([]Check, error) { return []Check{Passed("state")}, nil }, CheckTransition: func(s, i int, tr Transition[int, Unit]) ([]Check, error) {
		if s == right && i == observe {
			return []Check{Failed("transition", "right before left dependency")}, nil
		}
		return []Check{Passed("transition")}, nil
	}}
	equality := ResearchEquality[int, Unit]{func(a, b int) bool { return a == b }, func(a, b Unit) bool { return true }}
	r, e := CompareEquivalence(m, []int{0, 1, 2, 3}, researchTestProposal(), researchEnabled(), func(s int) (int, error) { return (s & 1) + (s >> 1 & 1), nil }, equality, researchTestMetadata)
	if e != nil || r.Status != ExperimentCounterexample || len(r.MissingViolations) != 1 || len(r.Reduced.Violations) != 0 {
		t.Fatalf("%+v %v", r, e)
	}
	witness := r.MissingViolations[0].Inputs
	if len(witness) != 2 || witness[0] != right || witness[1] != observe {
		t.Fatalf("wrong witness: %v", witness)
	}
	state := 0
	failed := false
	for _, input := range witness {
		tr, e := m.Step(state, input)
		if e != nil {
			t.Fatal(e)
		}
		checks, e := m.CheckTransition(state, input, tr)
		if e != nil {
			t.Fatal(e)
		}
		for _, c := range checks {
			failed = failed || c.Status == "failed"
		}
		state = tr.State
	}
	if !failed {
		t.Fatal("retained witness did not reproduce named failure")
	}
	r, e = CompareEquivalence(m, []int{0, 1, 2, 3}, researchTestProposal(), researchEnabled(), func(s int) (int, error) { return s, nil }, equality, researchTestMetadata)
	if e != nil || r.Status != ExperimentAgreesWithinDeclaredFixture || len(r.Exact.Violations) != 1 || len(r.Reduced.Violations) != 1 {
		t.Fatalf("%+v %v", r, e)
	}
}

func TestResearchSnapshotsReusedIteratorAndReducerBuffers(t *testing.T) {
	steps := 0
	input := []int{0}
	output := []int{0}
	m := Model[int, []int, []int]{InitialState: func() (int, error) { return 0, nil }, CloneState: ValueCopy[int], CloneInput: func(v []int) []int { return append([]int{}, v...) }, CloneOutput: func(v []int) []int { return append([]int{}, v...) }, EqualStates: func(a, b int) bool { return a == b }, CheckState: func(int) ([]Check, error) { return []Check{Passed("state")}, nil }, Inputs: func(int) (Iterator[[]int], error) {
		j := 0
		return func() ([]int, bool, error) {
			if j == 2 {
				input[0] = 99
				return nil, false, nil
			}
			input[0] = j
			j++
			return input, true, nil
		}, nil
	}, Step: func(s int, i []int) (Transition[int, []int], error) {
		steps++
		output[0] = s
		return Accept(1+i[0], output), nil
	}, CheckTransition: func(int, []int, Transition[int, []int]) ([]Check, error) { return []Check{Passed("transition")}, nil }}
	eq := ResearchEquality[[]int, []int]{func(a, b []int) bool { return a[0] == b[0] }, func(a, b []int) bool { return a[0] == b[0] }}
	r, e := CompareEquivalence(m, []int{0, 1, 2}, researchTestProposal(), researchEnabled(), func(s int) (int, error) {
		if s == 2 {
			return 1, nil
		}
		return 0, nil
	}, eq, researchTestMetadata)
	if e != nil || r.Status != ExperimentCounterexample || steps != 6 || r.Counterexamples[0].Reason != "ordered effects differ" || r.Counterexamples[0].RightPrefix[0][0] != 0 {
		t.Fatalf("%+v %v", r, e)
	}
	input[0] = 88
	output[0] = 88
	if r.Counterexamples[0].RightPrefix[0][0] != 0 {
		t.Fatal("prefix retains reused iterator buffer")
	}
}
func TestResearchMissingWitnessAndPairPrefixIsolation(t *testing.T) {
	clone := func(v []int) []int { return append([]int{}, v...) }
	m := Model[int, []int, Unit]{InitialState: func() (int, error) { return 0, nil }, CloneState: ValueCopy[int], CloneInput: clone, CloneOutput: ValueCopy[Unit], EqualStates: func(a, b int) bool { return a == b }, Inputs: func(int) (Iterator[[]int], error) { return Values([][]int{{0}, {1}}), nil }, Step: func(s int, i []int) (Transition[int, Unit], error) {
		if s == 0 {
			return Accept[int, Unit](1 + i[0]), nil
		}
		return Accept[int, Unit](s), nil
	}, CheckState: func(int) ([]Check, error) { return []Check{Passed("state")}, nil }, CheckTransition: func(s int, i []int, tr Transition[int, Unit]) ([]Check, error) {
		if s == 2 {
			return []Check{Failed("transition", "hidden")}, nil
		}
		return []Check{Passed("transition")}, nil
	}}
	r, e := CompareEquivalence(m, []int{0, 1, 2}, researchTestProposal(), researchEnabled(), func(s int) (int, error) {
		if s == 0 {
			return 0, nil
		}
		return 1, nil
	}, ResearchEquality[[]int, Unit]{func(a, b []int) bool { return a[0] == b[0] }, func(Unit, Unit) bool { return true }}, researchTestMetadata)
	if e != nil || r.Status != ExperimentCounterexample || len(r.MissingViolations) != 1 || len(r.Counterexamples) != 1 {
		t.Fatalf("%+v %v", r, e)
	}
	r.MissingViolations[0].Inputs[0][0] = 77
	if r.Exact.Violations[0].Inputs[0][0] != 1 || r.Counterexamples[0].RightPrefix[0][0] != 1 {
		t.Fatal("missing witness aliases exact or pairprefix")
	}
	r.Counterexamples[0].RightPrefix[0][0] = 88
	if r.Counterexamples[0].LeftPrefix[0][0] != 0 || r.Exact.Violations[0].Inputs[0][0] != 1 {
		t.Fatal("pairprefix aliases another evidence view")
	}
}

func TestResearchSnapshotFailureKeepsExecutedCountAndNoArms(t *testing.T) {
	steps, projections := 0, 0
	m := researchTestModel(&steps)
	m.Step = func(s, i int) (Transition[int, int], error) { steps++; return Accept(1-s, s), nil }
	m.CloneOutput = func(v int) int {
		if v == 1 {
			panic("snapshot fixture")
		}
		return v
	}
	r, e := CompareEquivalence(m, []int{0, 1}, researchTestProposal(), researchEnabled(), func(int) (int, error) { projections++; return 0, nil }, researchTestEqual(), researchTestMetadata)
	if e != nil || r.Status != ExperimentIncomplete || steps != 2 || r.MaterializedEdges != 2 || r.Exact != nil || r.Reduced != nil || projections != 0 || !strings.Contains(r.Limitation, "snapshot") {
		t.Fatalf("%+v %v steps=%d", r, e, steps)
	}
}

func TestResearchEffectsDifferencePrecedesLaterDifferences(t *testing.T) {
	steps := 0
	m := researchTestModel(&steps)
	m.Inputs = func(int) (Iterator[int], error) { return Values([]int{0, 1}), nil }
	m.Step = func(s, i int) (Transition[int, int], error) {
		if s == 1 {
			return Transition[int, int]{State: 2, Outputs: []int{2, 1}, Disposition: Rejected("different")}, nil
		}
		return Accept(1+i, 1, 2), nil
	}
	m.CheckTransition = func(s, i int, tr Transition[int, int]) ([]Check, error) {
		if s == 1 {
			return []Check{Failed("transition", "different")}, nil
		}
		return []Check{Passed("transition")}, nil
	}
	r, e := CompareEquivalence(m, []int{0, 1, 2}, researchTestProposal(), researchEnabled(), func(s int) (int, error) {
		if s == 2 {
			return 1, nil
		}
		return 0, nil
	}, researchTestEqual(), researchTestMetadata)
	if e != nil || r.Status != ExperimentCounterexample || r.Counterexamples[0].Reason != "ordered effects differ" {
		t.Fatalf("%+v %v", r, e)
	}
}

type researchPanicPayload struct{ formats int }

func (p *researchPanicPayload) Error() string { p.formats++; return "unexpected formatting" }
func TestResearchRawErrorsAndPanicPayloadContainment(t *testing.T) {
	for _, stage := range []string{"initial", "state", "inputs", "step", "edge"} {
		t.Run(stage, func(t *testing.T) {
			steps := 0
			m := researchTestModel(&steps)
			failure := errors.New("fixture error")
			switch stage {
			case "initial":
				m.InitialState = func() (int, error) { return 0, failure }
			case "state":
				m.CheckState = func(int) ([]Check, error) { return nil, failure }
			case "inputs":
				m.Inputs = func(int) (Iterator[int], error) { return nil, failure }
			case "step":
				m.Step = func(int, int) (Transition[int, int], error) { return Transition[int, int]{}, failure }
			case "edge":
				m.CheckTransition = func(int, int, Transition[int, int]) ([]Check, error) { return nil, failure }
			}
			r, e := CompareEquivalence(m, []int{0, 1}, researchTestProposal(), researchEnabled(), func(int) (int, error) { return 0, nil }, researchTestEqual(), researchTestMetadata)
			if e != nil || r.Status != ExperimentIncomplete || r.Limitation != "fixture error" {
				t.Fatalf("%+v %v", r, e)
			}
		})
	}
	steps := 0
	m := researchTestModel(&steps)
	payload := &researchPanicPayload{}
	m.Step = func(int, int) (Transition[int, int], error) { panic(payload) }
	r, e := CompareEquivalence(m, []int{0, 1}, researchTestProposal(), researchEnabled(), func(int) (int, error) { return 0, nil }, researchTestEqual(), researchTestMetadata)
	if e != nil || r.Status != ExperimentIncomplete || payload.formats != 0 || r.Limitation != "transition: callback panic" {
		t.Fatalf("%+v %v formats=%d", r, e, payload.formats)
	}
}

func TestResearchPreferredCheckLeasesAndStickyFailures(t *testing.T) {
	for _, mode := range []string{"success", "error", "panic", "ignored-invalid"} {
		t.Run(mode, func(t *testing.T) {
			steps := 0
			m := researchTestModel(&steps)
			escaped := []*CheckSink{}
			stateCalls, edgeCalls := 0, 0
			m.CheckState = func(int) ([]Check, error) { t.Fatal("legacy state checker selected"); return nil, nil }
			m.CheckTransition = func(int, int, Transition[int, int]) ([]Check, error) {
				t.Fatal("legacy edge checker selected")
				return nil, nil
			}
			m.CheckStateInto = func(_ int, sink *CheckSink) error {
				escaped = append(escaped, sink)
				stateCalls++
				if e := sink.Push(Passed("state")); e != nil {
					return e
				}
				switch mode {
				case "error":
					return errors.New("preferred check error")
				case "panic":
					panic("preferred check panic")
				case "ignored-invalid":
					_ = sink.Push(Check{Status: "invalid"})
				}
				return nil
			}
			m.CheckTransitionInto = func(_ int, _ int, _ Transition[int, int], sink *CheckSink) error {
				escaped = append(escaped, sink)
				edgeCalls++
				return sink.Push(Passed("transition"))
			}
			r, e := CompareEquivalence(m, []int{0, 1}, researchTestProposal(), researchEnabled(), func(int) (int, error) { return 0, nil }, researchTestEqual(), researchTestMetadata)
			if e != nil {
				t.Fatal(e)
			}
			if mode == "success" {
				if r.Status != ExperimentAgreesWithinDeclaredFixture || stateCalls != 2 || edgeCalls != 2 {
					t.Fatalf("%+v calls=%d/%d", r, stateCalls, edgeCalls)
				}
			} else if r.Status != ExperimentIncomplete || r.Exact != nil || r.Reduced != nil || edgeCalls != 0 {
				t.Fatalf("%+v", r)
			}
			for _, sink := range escaped {
				if _, e := sink.Len(); e == nil {
					t.Fatal("research check lease escaped")
				}
			}
		})
	}
}

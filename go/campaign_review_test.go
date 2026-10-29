package statelessness

import (
	"errors"
	"fmt"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
)

func TestCampaignNativeCountBoundary(t *testing.T) {
	c := DefaultCampaignConfig()
	c.Jobs = int(^uint(0) >> 1)
	c.Fuzz.Cases, c.Fuzz.MaxSteps, c.Fuzz.MaxTransitions = 0, 0, 0
	var flag atomic.Bool
	flag.Store(true)
	r, e := RunCampaign(c, RunLimits{Cancellation: &flag}, func(uint64) (CampaignModel[int, uint64, int], error) {
		t.Fatal("pre-cancel factory")
		return CampaignModel[int, uint64, int]{}, nil
	}, func(JobReport[uint64]) error { t.Fatal("pre-cancel sink"); return nil })
	if e != nil || r.Requested != c.Jobs || r.NotStarted != c.Jobs || r.Started != 0 || r.Termination.Kind != "cancelled" || !r.AccountingComplete {
		t.Fatal(e, r)
	}
}

type campaignDiagnostic func() string

func (f campaignDiagnostic) Error() string { return f() }

func TestCampaignErrorDiagnosticPreservesPanicProvenance(t *testing.T) {
	for _, phase := range []string{"factory", "initial", "transition", "feedback-close", "model-close"} {
		for _, priorPanic := range []bool{false, true} {
			t.Run(phase+"/prior="+fmt.Sprint(priorPanic), func(t *testing.T) {
				c := campaignTestConfig(1)
				c.Jobs = 1
				var retained GenerateModel[int, uint64, int]
				var diagnosticCalls int
				var panicClone bool
				panicMessage := "genuine callback panic"
				errorAtBoundary := func() error {
					panicClone = true
					if priorPanic {
						// Application recovery does not erase a genuine callback
						// panic already observed by the campaign boundary.
						func() { defer func() { _ = recover() }(); retained.CloneState(0) }()
					}
					return campaignDiagnostic(func() string {
						diagnosticCalls++
						panicMessage = "diagnostic-only panic"
						retained.CloneState(0)
						return "unreachable"
					})
				}
				r, e := RunGuidedCampaign(c, RunLimits{}, DefaultGuidanceConfig(), func(id uint64) (CampaignModel[int, uint64, int], error) {
					m := campaignTestModel(id, false)
					clone := m.Model.CloneState
					m.Model.CloneState = func(s int) int {
						if panicClone {
							panic(panicMessage)
						}
						return clone(s)
					}
					if phase == "model-close" {
						m.Close = errorAtBoundary
					}
					return m, nil
				}, func(_ uint64, g GenerateModel[int, uint64, int]) (CampaignFeedback[int, uint64, int, int], error) {
					retained = g
					if phase == "factory" {
						return CampaignFeedback[int, uint64, int, int]{}, errorAtBoundary()
					}
					f := CampaignFeedback[int, uint64, int, int]{Feedback: ExactStateFeedback(g.Model)}
					if phase == "initial" {
						f.Feedback.InitialFeatures = func(int, *FeatureBuffer[int]) error { return errorAtBoundary() }
					}
					if phase == "transition" {
						f.Feedback.TransitionFeatures = func(int, uint64, Transition[int, int], *FeatureBuffer[int]) error { return errorAtBoundary() }
					}
					if phase == "feedback-close" {
						f.Close = errorAtBoundary
					}
					return f, nil
				}, func(j JobReport[uint64]) error {
					if j.Metadata == nil {
						t.Error("lost metadata")
					}
					if priorPanic {
						if j.Outcome.Kind != "panicked" || j.Outcome.Panic != "genuine callback panic" {
							t.Errorf("%+v", j.Outcome)
						}
					} else if j.Outcome.Kind != "model_error" || j.Outcome.Error == nil || !strings.Contains(j.Outcome.Error.Error(), "error diagnostic unavailable") {
						t.Errorf("%+v", j.Outcome)
					}
					return nil
				})
				if e != nil || r.Errors != 1 || r.AccountingComplete || diagnosticCalls != 1 {
					t.Fatal(e, r, diagnosticCalls)
				}
			})
		}
	}
}

func TestCampaignFeedbackLeaseBeforeErrorDiagnostics(t *testing.T) {
	for _, phase := range []string{"initial", "transition"} {
		c := campaignTestConfig(1)
		c.Jobs = 1
		var diagnostics, lateClones int
		r, e := RunGuidedCampaign(c, RunLimits{}, DefaultGuidanceConfig(), func(id uint64) (CampaignModel[int, uint64, int], error) { return campaignTestModel(id, false), nil }, func(_ uint64, g GenerateModel[int, uint64, int]) (CampaignFeedback[int, uint64, int, int], error) {
			f := ExactStateFeedback(g.Model)
			formatting := false
			f.Key.Clone = func(v int) int {
				if formatting {
					lateClones++
					panic("late clone must be unreachable")
				}
				return v
			}
			callback := func(out *FeatureBuffer[int]) error {
				copied := *out
				return campaignDiagnostic(func() string {
					diagnostics++
					formatting = true
					for _, handle := range []*FeatureBuffer[int]{out, &copied} {
						if e := handle.Push(123); e == nil || e.Error() != "feature buffer lease expired" {
							t.Errorf("live lease during diagnostics: %v", e)
						}
					}
					return "original feedback error"
				})
			}
			if phase == "initial" {
				f.InitialFeatures = func(_ int, out *FeatureBuffer[int]) error { return callback(out) }
			} else {
				f.TransitionFeatures = func(_ int, _ uint64, _ Transition[int, int], out *FeatureBuffer[int]) error { return callback(out) }
			}
			return CampaignFeedback[int, uint64, int, int]{Feedback: f}, nil
		}, func(j JobReport[uint64]) error {
			if j.Outcome.Kind != "model_error" || !strings.Contains(j.Outcome.Error.Error(), "original feedback error") {
				t.Errorf("%+v", j)
			}
			return nil
		})
		if e != nil || r.Errors != 1 || r.AccountingComplete || diagnostics != 1 || lateClones != 0 {
			t.Fatal(phase, e, r, diagnostics, lateClones)
		}
	}
}

func TestCampaignAutoPanicProvenance(t *testing.T) {
	for _, phase := range []string{"enumeration", "iterator", "equality"} {
		c := campaignTestConfig(1)
		c.Jobs = 1
		r, e := RunCampaign(c, RunLimits{}, func(id uint64) (CampaignModel[int, uint64, int], error) {
			m := campaignTestModel(id, false)
			m.Model.Inputs = func(int) (Iterator[uint64], error) {
				if phase == "enumeration" {
					panic("enumeration")
				}
				if phase == "iterator" {
					return func() (uint64, bool, error) { panic("iterator") }, nil
				}
				return Values([]uint64{1}), nil
			}
			return CampaignAuto(m, func(a, b uint64) (bool, error) {
				if phase == "equality" {
					panic("equality")
				}
				return a == b, nil
			}, DefaultAutoOptions())
		}, func(j JobReport[uint64]) error {
			if j.Metadata == nil || j.Outcome.Kind != "panicked" || j.Outcome.Panic != phase {
				t.Errorf("%+v", j)
			}
			return nil
		})
		if e != nil || r.Errors != 1 || r.AccountingComplete {
			t.Fatal(phase, e, r)
		}
	}
}

func TestCampaignCleanupPriority(t *testing.T) {
	for _, search := range []string{"pass", "failure", "error", "panic"} {
		for _, feedback := range []string{"pass", "error", "panic"} {
			for _, model := range []string{"pass", "error", "panic"} {
				t.Run(search+"/"+feedback+"/"+model, func(t *testing.T) {
					c := campaignTestConfig(1)
					c.Jobs = 1
					var cleanup []string
					closeFn := func(stage, mode string) func() error {
						return func() error {
							cleanup = append(cleanup, stage)
							if mode == "panic" {
								panic(stage)
							}
							if mode == "error" {
								return errors.New(stage)
							}
							return nil
						}
					}
					r, e := RunGuidedCampaign(c, RunLimits{}, DefaultGuidanceConfig(), func(id uint64) (CampaignModel[int, uint64, int], error) {
						m := campaignTestModel(id, search == "failure")
						step := m.Model.Step
						m.Model.Step = func(s int, i uint64) (Transition[int, int], error) {
							if search == "panic" {
								panic("search")
							}
							if search == "error" {
								return Transition[int, int]{}, errors.New("search")
							}
							return step(s, i)
						}
						m.Close = closeFn("model-close", model)
						return m, nil
					}, func(_ uint64, g GenerateModel[int, uint64, int]) (CampaignFeedback[int, uint64, int, int], error) {
						return CampaignFeedback[int, uint64, int, int]{Feedback: ExactStateFeedback(g.Model), Close: closeFn("feedback-close", feedback)}, nil
					}, func(j JobReport[uint64]) error {
						kind, diagnostic := "completed", ""
						for _, x := range []struct{ stage, mode string }{{"search", search}, {"feedback-close", feedback}, {"model-close", model}} {
							if x.mode == "error" {
								kind, diagnostic = "model_error", x.stage
							}
						}
						for _, x := range []struct{ stage, mode string }{{"search", search}, {"feedback-close", feedback}, {"model-close", model}} {
							if x.mode == "panic" {
								kind, diagnostic = "panicked", x.stage
							}
						}
						if j.Metadata == nil || j.Outcome.Kind != kind {
							t.Errorf("%+v want %s", j, kind)
						}
						if kind == "panicked" && j.Outcome.Panic != diagnostic {
							t.Error(j.Outcome, diagnostic)
						}
						if kind == "model_error" && (j.Outcome.Error == nil || !strings.Contains(j.Outcome.Error.Error(), diagnostic)) {
							t.Error(j.Outcome, diagnostic)
						}
						if !reflect.DeepEqual(cleanup, []string{"feedback-close", "model-close"}) {
							t.Error(cleanup)
						}
						return nil
					})
					wantComplete := search != "error" && search != "panic" && feedback == "pass" && model == "pass"
					if e != nil || r.AccountingComplete != wantComplete {
						t.Fatal(e, r)
					}
				})
			}
		}
	}
}

func TestCampaignGuidedFinalTransferAndPanic(t *testing.T) {
	for _, panicTransfer := range []bool{false, true} {
		c := campaignTestConfig(2)
		c.Jobs = 1
		c.Fuzz.Cases = 1
		c.Fuzz.MaxSteps = 2
		var cleanup []string
		transferring := false
		transfers := 0
		r, e := RunGuidedCampaign(c, RunLimits{}, DefaultGuidanceConfig(), func(id uint64) (CampaignModel[int, []uint64, int], error) {
			storage := []uint64{7}
			g := GenerateModel[int, []uint64, int]{Model: Model[int, []uint64, int]{
				InitialState: func() (int, error) { return 0, nil },
				Step:         func(s int, _ []uint64) (Transition[int, int], error) { return Accept[int, int](s + 1), nil },
				CheckState:   func(int) ([]Check, error) { return nil, nil },
				CloneState:   ValueCopy[int], CloneOutput: ValueCopy[int], EqualStates: func(a, b int) bool { return a == b },
				CloneInput: func(v []uint64) []uint64 {
					if transferring {
						transfers++
						if panicTransfer {
							panic("final transfer")
						}
					}
					return append([]uint64(nil), v...)
				},
			}, Generate: func(int, *Rng) ([]uint64, bool, error) { return storage, true, nil }}
			return CampaignModel[int, []uint64, int]{Model: g, Metadata: func() (Metadata, error) { return Identity("transfer", "fixture"), nil }, Close: func() error { cleanup = append(cleanup, "model"); storage[0] = 999; return nil }}, nil
		}, func(_ uint64, g GenerateModel[int, []uint64, int]) (CampaignFeedback[int, []uint64, int, int], error) {
			return CampaignFeedback[int, []uint64, int, int]{Feedback: ExactStateFeedback(g.Model), Close: func() error { cleanup = append(cleanup, "feedback"); transferring = true; return nil }}, nil
		}, func(j JobReport[[]uint64]) error {
			if !reflect.DeepEqual(cleanup, []string{"feedback", "model"}) {
				t.Error(cleanup)
			}
			if panicTransfer {
				if j.Outcome.Kind != "panicked" || j.Outcome.Panic != "final transfer" || j.Metadata == nil {
					t.Errorf("%+v", j)
				}
			} else {
				if j.Outcome.Kind != "completed" {
					t.Fatalf("%+v", j)
				}
				for _, entry := range j.Outcome.Report.Guidance.Corpus {
					for _, input := range entry.Inputs {
						if !reflect.DeepEqual(input, []uint64{7}) {
							t.Fatal("cleanup alias", input)
						}
					}
				}
			}
			return nil
		})
		if e != nil || transfers == 0 || r.AccountingComplete == panicTransfer {
			t.Fatal(e, r, transfers)
		}
	}
}

func TestAlreadyComposedAutoPanicProvenance(t *testing.T) {
	for _, stage := range []string{"enumerate", "iterator", "equality"} {
		c := campaignTestConfig(1)
		c.Jobs = 1
		c.Fuzz.Cases = 2
		c.Fuzz.MutationPercent = 100
		model := campaignTestModel(0, false)
		model.Model.Inputs = func(int) (Iterator[uint64], error) {
			if stage == "enumerate" {
				panic("auto sentinel")
			}
			once := false
			return func() (uint64, bool, error) {
				if stage == "iterator" {
					panic("auto sentinel")
				}
				if once {
					return 0, false, nil
				}
				once = true
				return 1, true, nil
			}, nil
		}
		g, err := Auto(model.Model.Model, func(a, b uint64) (bool, error) {
			if stage == "equality" {
				panic("auto sentinel")
			}
			return a == b, nil
		}, DefaultAutoOptions())
		if err != nil {
			t.Fatal(err)
		}
		// Force membership in the composed adapter independently of mutation RNG.
		if stage == "equality" {
			g.Generate = func(s int, r *Rng) (uint64, bool, error) { _, e := g.IsEnabled(s, 1); return 1, true, e }
		}
		model.Model = g
		_, err = RunCampaign(c, RunLimits{}, func(uint64) (CampaignModel[int, uint64, int], error) { return model, nil }, func(job JobReport[uint64]) error {
			if job.Outcome.Kind != "panicked" || job.Outcome.Panic != "auto sentinel" {
				t.Fatalf("%s: %+v", stage, job.Outcome)
			}
			return nil
		})
		if err != nil {
			t.Fatal(err)
		}
	}
}

type campaignHostileAs struct{}

func (campaignHostileAs) Error() string { return "returned error" }
func (campaignHostileAs) As(any) bool   { panic("diagnostic trap") }
func (campaignHostileAs) Unwrap() error { panic("diagnostic trap") }
func TestCampaignReturnedErrorDoesNotInspectUserUnwrap(t *testing.T) {
	c := campaignTestConfig(1)
	c.Jobs = 1
	_, err := RunCampaign(c, RunLimits{}, func(uint64) (CampaignModel[int, uint64, int], error) {
		model := campaignTestModel(0, false)
		model.Model.Generate = func(int, *Rng) (uint64, bool, error) { return 0, false, campaignHostileAs{} }
		return model, nil
	}, func(job JobReport[uint64]) error {
		if job.Outcome.Kind != "model_error" {
			t.Fatalf("%+v", job.Outcome)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}

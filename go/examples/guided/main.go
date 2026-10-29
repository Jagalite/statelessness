package main

import (
	"fmt"
	"log"

	s "github.com/Jagalite/statelessness/go"
)

func main() {
	model := s.GenerateModel[int, int, int]{Model: s.Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil },
		Step:         func(state, input int) (s.Transition[int, int], error) { return s.Accept(state+input, state+input), nil },
		CheckState: func(state int) ([]s.Check, error) {
			if state >= 5 {
				return []s.Check{s.Failed("depth", "reached five")}, nil
			}
			return []s.Check{s.Passed("depth")}, nil
		},
		CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		EqualStates: func(a, b int) bool { return a == b },
	}, Generate: func(_ int, rng *s.Rng) (int, bool, error) {
		draw, _ := rng.Index(2)
		return int(draw), true, nil
	}}
	config := s.DefaultFuzzConfig()
	config.Seed, config.Cases, config.MaxSteps, config.MaxTransitions = 42, 100, 8, 800
	report, err := s.GuidedFuzz(model, config, s.DefaultGuidanceConfig(), s.ExactStateFeedback(model.Model))
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("%s: %d transitions, %d features, %d retained prefixes\n", report.Termination, report.Transitions, report.Guidance.UniqueFeatures, len(report.Guidance.Corpus))
}

package statelessness

// The execution kernel owns every model/check/observer/control boundary. Drivers
// select candidates and learn only after successful checks. A nil driver uses
// ordinary previous-sequence mutation without adding any callback boundaries.
type fuzzDriver[S, I, O any] interface {
	candidate([]I, *Rng, int) ([]I, int, error)
	initial(S, uint64) error
	transition(S, I, Transition[S, O], []I, uint64, uint64) error
}

func validateFuzzModel[S, I, O any](g GenerateModel[S, I, O]) error {
	if e := g.validate(); e != nil {
		return e
	}
	if g.InitialState == nil || g.Step == nil || g.Generate == nil {
		return &ModelError{"fuzz requires initial, step and generate callbacks"}
	}
	return nil
}
func validateFuzzConfig64(config FuzzConfig64) error {
	if config.MutationPercent < 0 || config.MutationPercent > 100 {
		return &ConfigError{"invalid fuzz configuration"}
	}
	return nil
}

func previousCandidate[S, I, O any](g GenerateModel[S, I, O], previous []I, rng *Rng, percent int) ([]I, int, error) {
	if len(previous) == 0 {
		return nil, -1, nil
	}
	draw, _ := rng.Index(100)
	if draw >= uint64(percent) {
		return nil, -1, nil
	}
	mutated, e := cloneInputs(g, previous)
	if e != nil {
		return nil, -1, e
	}
	start, _ := rng.Index(uint64(len(mutated)))
	span, _ := rng.Index(uint64(len(mutated)) - start)
	end := int(start + span + 1)
	mutated = append(mutated[:int(start)], mutated[end:]...)
	r, _ := rng.Index(uint64(len(mutated)) + 1)
	return mutated, int(r), nil
}

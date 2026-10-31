package main

import (
	"fmt"
	"slices"

	sl "github.com/Jagalite/statelessness/go"
)

type wireBatch struct {
	Phase  string      `json:"phase"`
	Checks []wireCheck `json:"checks"`
}

type wireWitness struct {
	Inputs       []int64           `json:"inputs"`
	States       []state           `json:"states"`
	Outputs      [][]int64         `json:"outputs"`
	Dispositions []wireDisposition `json:"dispositions"`
	Checks       []wireBatch       `json:"checks"`
	Termination  string            `json:"termination"`
	Replay       wireReplay        `json:"replay"`
}

type wireReplay struct {
	Outcome           string `json:"outcome"`
	FailureReproduced bool   `json:"failure_reproduced"`
	StepsVerified     int    `json:"steps_verified"`
}

func replayWitness(spec fixtureCase, inputs []int64, propertyFailure bool) (wireWitness, error) {
	// Recording and replay are separate public calls on fresh fixture instances.
	// Their callbacks never enter the original fuzz trace or generator_rng.
	spec.Control = controlSpec{}
	recordingFixture := newFixture(spec)
	g := recordingFixture.model()
	g.Codec = fixtureCodec()
	recording, err := sl.Record(g.Model, sl.Values(nonnil(inputs)), len(inputs))
	if err != nil {
		return wireWitness{}, fmt.Errorf("record witness: %w", err)
	}
	termination := "completed"
	if propertyFailure {
		termination = "property_failed"
	}
	if recording == nil || recording.Termination != termination || recording.Error != "" || len(recording.Steps) != len(inputs) {
		return wireWitness{}, fmt.Errorf("witness did not record all inputs with termination %s", termination)
	}
	batches := recordingFixture.checkBatches
	if len(batches) != 1+2*len(inputs) || !slices.Equal(recording.InitialChecks, batches[0].checks) {
		return wireWitness{}, fmt.Errorf("recorded witness initial checks/batch count disagree with callbacks")
	}
	initial, err := decodeState(recording.InitialState)
	if err != nil {
		return wireWitness{}, err
	}
	witness := wireWitness{Inputs: []int64{}, States: []state{initial}, Outputs: [][]int64{},
		Dispositions: []wireDisposition{}, Checks: make([]wireBatch, len(batches)), Termination: pascal(recording.Termination)}
	for i, batch := range batches {
		witness.Checks[i] = wireBatch{batch.phase, normalizeChecks(batch.checks)}
	}
	for i, step := range recording.Steps {
		checks := append(nonnil(batches[1+2*i].checks), batches[2+2*i].checks...)
		if !slices.Equal(checks, step.Checks) {
			return wireWitness{}, fmt.Errorf("recorded witness checks disagree with callback batches at step %d", i)
		}
		input, err := decodeInt(step.Input)
		if err != nil {
			return wireWitness{}, err
		}
		if input != inputs[i] {
			return wireWitness{}, fmt.Errorf("recorded witness input mismatch at step %d", i)
		}
		post, err := decodeState(step.PostState)
		if err != nil {
			return wireWitness{}, err
		}
		outputs := make([]int64, len(step.Outputs))
		for j, encoded := range step.Outputs {
			outputs[j], err = decodeInt(encoded)
			if err != nil {
				return wireWitness{}, err
			}
		}
		witness.Inputs = append(witness.Inputs, input)
		witness.States = append(witness.States, post)
		witness.Outputs = append(witness.Outputs, outputs)
		witness.Dispositions = append(witness.Dispositions, normalizeDisposition(step.Disposition))
	}
	replayFixture := newFixture(spec)
	// Replay loads a checkpoint instead of calling InitialState. Give its first
	// checkpoint the same case selector as a fresh recording, with reset counts.
	replayFixture.caseNumber = 1
	replayModel := replayFixture.model().Model
	replayModel.Codec = fixtureCodec()
	replayed, err := sl.Replay(replayModel, recording, false)
	if err != nil {
		return wireWitness{}, fmt.Errorf("replay witness: %w", err)
	}
	if replayed == nil || replayed.Outcome != "exact" || replayed.FailureReproduced != propertyFailure ||
		!replayed.BuildMatches || replayed.StepsVerified != len(recording.Steps) {
		return wireWitness{}, fmt.Errorf("witness replay was not exact and complete with failure_reproduced=%t", propertyFailure)
	}
	witness.Replay = wireReplay{Outcome: pascal(replayed.Outcome), FailureReproduced: replayed.FailureReproduced,
		StepsVerified: replayed.StepsVerified}
	return witness, nil
}

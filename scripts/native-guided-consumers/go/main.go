package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	sl "github.com/Jagalite/statelessness/go"
)

type actual struct {
	Report         *wireReport   `json:"report"`
	Error          *string       `json:"error"`
	Trace          [][]any       `json:"trace"`
	GeneratorRNG   []rngRecord   `json:"generator_rng"`
	WitnessReplays []wireWitness `json:"witness_replays"`
	Shrink         *wireShrink   `json:"shrink"`
}

type caseResult struct {
	ID     string `json:"id"`
	Actual actual `json:"actual"`
}

type results struct {
	Schema string       `json:"schema"`
	Engine string       `json:"engine"`
	Cases  []caseResult `json:"cases"`
}

func runCase(spec fixtureCase) (actual, error) {
	config, err := spec.Config.native()
	if err != nil {
		return actual{}, err
	}
	f := newFixture(spec)
	report, runError := f.run(f.model(), config)
	result := actual{Trace: f.trace, GeneratorRNG: f.rng, WitnessReplays: []wireWitness{}}
	if runError != nil {
		if report != nil {
			return actual{}, fmt.Errorf("public fuzz API returned both report and error")
		}
		message := f.normalizeError(runError)
		result.Error = &message
		return result, nil
	}
	if report == nil {
		return actual{}, fmt.Errorf("public fuzz API returned neither report nor error")
	}
	result.Report = normalizeReport(report)
	if report.Guidance != nil {
		for i, entry := range report.Guidance.Corpus {
			witness, err := replayWitness(spec, entry.Inputs, false)
			if err != nil {
				return actual{}, fmt.Errorf("corpus witness %d: %w", i, err)
			}
			result.WitnessReplays = append(result.WitnessReplays, witness)
		}
	}
	if spec.ShrinkConfig != nil {
		if report.Failure == nil {
			return actual{}, fmt.Errorf("shrink_config requires an observed failure")
		}
		original, err := replayWitness(spec, report.Failure.Inputs, true)
		if err != nil {
			return actual{}, fmt.Errorf("original failing witness: %w", err)
		}
		result.WitnessReplays = append(result.WitnessReplays, original)
		freshSpec := spec
		freshSpec.Control = controlSpec{}
		fresh := newFixture(freshSpec)
		shrunk, err := sl.Shrink(fresh.model(), *report.Failure, sl.ShrinkConfig{MaxAttempts: spec.ShrinkConfig.MaxAttempts})
		if err != nil {
			return actual{}, fmt.Errorf("shrink observed failure: %w", err)
		}
		if shrunk == nil {
			return actual{}, fmt.Errorf("public shrink API returned no report")
		}
		result.Shrink, err = normalizeShrink(shrunk)
		if err != nil {
			return actual{}, err
		}
		minimized, err := replayWitness(spec, shrunk.Minimized.Inputs, true)
		if err != nil {
			return actual{}, fmt.Errorf("minimized failing witness: %w", err)
		}
		// Keep both failure entries even when the shrink attempt budget leaves
		// the minimized inputs equal to the original witness.
		result.WitnessReplays = append(result.WitnessReplays, minimized)
	}
	return result, nil
}

func runCLI(args []string) error {
	flags := flag.NewFlagSet("guided-go-consumer", flag.ContinueOnError)
	path := flags.String("corpus", "", "guided-v2 corpus file (or - for stdin)")
	selected := flags.String("case", "", "execute only this case id")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if *path == "" && flags.NArg() == 1 {
		*path = flags.Arg(0)
	} else if flags.NArg() != 0 {
		return fmt.Errorf("unexpected positional arguments")
	}
	if *path == "" {
		return fmt.Errorf("usage: guided-go-consumer --corpus FILE [--case ID]")
	}
	input := os.Stdin
	if *path != "-" {
		file, err := os.Open(*path)
		if err != nil {
			return err
		}
		defer file.Close()
		input = file
	}
	c, err := readCorpus(input)
	if err != nil {
		return err
	}
	output := results{Schema: "stateless-guided-v2-results/1", Engine: "go", Cases: []caseResult{}}
	for _, row := range c.Cases {
		if *selected != "" && row.ID != *selected {
			continue
		}
		observed, err := runCase(row)
		if err != nil {
			return fmt.Errorf("case %s: %w", row.ID, err)
		}
		output.Cases = append(output.Cases, caseResult{row.ID, observed})
	}
	if *selected != "" && len(output.Cases) == 0 {
		return fmt.Errorf("case %q not found", *selected)
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	return encoder.Encode(output)
}

func main() {
	if err := runCLI(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

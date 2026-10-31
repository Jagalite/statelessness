// An external-module consumer. Qualification installs the source-bound package
// into a private Go module proxy; this module contains no local replace rule.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strconv"
	"sync"
	"sync/atomic"

	s "github.com/Jagalite/statelessness/go"
)

func main() {
	host := flag.Bool("host-check", false, "exercise real multiworker rendezvous and out-of-order callbacks")
	ordinary := flag.Bool("ordinary", false, "use ordinary fuzz instead of guided fuzz")
	referenceCase := flag.String("case", "", "run a named Rust public reference case")
	allReference := flag.Bool("all-reference", false, "run all Rust public reference cases")
	listCases := flag.Bool("list-cases", false, "list Rust public reference case names")
	flag.Parse()
	if (*referenceCase != "" && (*allReference || *listCases)) || (*allReference && *listCases) {
		fmt.Fprintln(os.Stderr, "reference options are mutually exclusive")
		os.Exit(2)
	}
	if *listCases {
		for _, name := range referenceNames() {
			fmt.Println(name)
		}
		return
	}
	if *allReference {
		for _, name := range referenceNames() {
			if err := emitReference(name); err != nil {
				fmt.Fprintln(os.Stderr, err)
				os.Exit(1)
			}
		}
		return
	}
	if *referenceCase != "" {
		if err := emitReference(*referenceCase); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	c := s.DefaultCampaignConfig()
	c.FirstJob, c.Jobs = 7, 4
	c.Fuzz = s.FuzzConfig{Cases: 2, MaxSteps: 3, MaxTransitions: 6, MutationPercent: 100}
	if *host {
		c.Workers, c.ResultBuffer = 3, 0
	}
	var mu sync.Mutex
	var events []string
	emit := func(value string) { mu.Lock(); events = append(events, value); mu.Unlock() }
	first := make(chan struct{})
	release := make(chan struct{})
	var live, maximum atomic.Int64
	factory := func(id uint64) (s.CampaignModel[int, uint64, int], error) {
		n := live.Add(1)
		for {
			old := maximum.Load()
			if n <= old || maximum.CompareAndSwap(old, n) {
				break
			}
		}
		emit(fmt.Sprintf("factory:%d", id))
		if *host {
			if id == 7 {
				close(first)
				<-release
			} else if id == 8 {
				<-first
			}
		}
		steps := 0 // Owned by this job's worker; no shared mutable model.
		g := s.GenerateModel[int, uint64, int]{Model: s.Model[int, uint64, int]{
			InitialState: func() (int, error) { emit(fmt.Sprintf("initial:%d", id)); return 0, nil },
			Step: func(state int, input uint64) (s.Transition[int, int], error) {
				steps++
				return s.Accept[int, int](state + int(input)), nil
			},
			CheckState: func(int) ([]s.Check, error) {
				return []s.Check{s.Passed("counter"), s.Skipped("optional", "example")}, nil
			},
			CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[uint64], CloneOutput: s.ValueCopy[int],
			EqualStates: func(a, b int) bool { return a == b },
		}, Generate: func(_ int, rng *s.Rng) (uint64, bool, error) { n, _ := rng.Index(3); return n + 1, true, nil }}
		return s.CampaignModel[int, uint64, int]{Model: g,
			Metadata: func() (s.Metadata, error) {
				emit(fmt.Sprintf("metadata:%d", id))
				return s.Identity(fmt.Sprintf("counter:%d", id), "n06-example-v1"), nil
			},
			Close: func() error { emit(fmt.Sprintf("close:%d:%d", id, steps)); live.Add(-1); return nil },
		}, nil
	}
	var jobs []map[string]any
	sink := func(j s.JobReport[uint64]) error {
		emit(fmt.Sprintf("sink:%d", j.JobID))
		if *host && j.JobID == 8 {
			close(release)
		}
		if j.Metadata == nil || j.Outcome.Kind != "completed" {
			return fmt.Errorf("unexpected job outcome %s", j.Outcome.Kind)
		}
		r := j.Outcome.Report
		jobs = append(jobs, map[string]any{"job_id": strconv.FormatUint(j.JobID, 10), "seed": strconv.FormatUint(j.Seed, 10), "termination": r.Termination, "cases": r.Cases, "transitions": strconv.FormatUint(r.Transitions, 10), "metadata": j.Metadata, "guidance": r.Guidance})
		return nil
	}
	var report s.CampaignReport
	var err error
	if *ordinary {
		report, err = s.RunCampaign(c, s.RunLimits{}, factory, sink)
	} else {
		report, err = s.RunGuidedCampaign(c, s.RunLimits{}, s.DefaultGuidanceConfig(), factory,
			func(id uint64, g s.GenerateModel[int, uint64, int]) (s.CampaignFeedback[int, uint64, int, int], error) {
				emit(fmt.Sprintf("feedback:%d", id))
				return s.CampaignFeedback[int, uint64, int, int]{Feedback: s.ExactStateFeedback(g.Model), Close: func() error { emit(fmt.Sprintf("feedback-close:%d", id)); return nil }}, nil
			}, sink)
	}
	if err != nil || report.Delivered != c.Jobs || !report.AccountingComplete || report.Termination.Kind != "jobs_completed" || live.Load() != 0 {
		fmt.Fprintln(os.Stderr, "campaign failed", err, report)
		os.Exit(1)
	}
	if *host && (maximum.Load() < 2 || jobs[0]["job_id"] == "7") {
		fmt.Fprintln(os.Stderr, "workers did not overlap or reports sorted")
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(map[string]any{"schema": "n06-go-example-v1", "host_check": *host, "ordinary": *ordinary, "jobs": jobs, "events": events, "report": report, "max_live_models": maximum.Load()}); err != nil {
		panic(err)
	}
}

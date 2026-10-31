package main

import (
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"os"
)

func networkMain() {
	must := func(e error) {
		if e != nil {
			panic(e)
		}
	}
	child := s.Model[uint8, uint8, uint8]{InitialState: func() (uint8, error) { return 0, nil }, Step: func(v, i uint8) (s.Transition[uint8, uint8], error) { return s.Accept[uint8, uint8](v+i, i), nil }, CloneState: s.ValueCopy[uint8], CloneInput: s.ValueCopy[uint8], CloneOutput: s.ValueCopy[uint8], CheckState: func(uint8) ([]s.Check, error) { return []s.Check{s.Passed("safe")}, nil }, Codec: &s.Codec[uint8, uint8, uint8]{Metadata: func() (s.Metadata, error) { return s.Identity("ring-child", "fixture-v1"), nil }}}
	mode := os.Args[1]
	switch mode {
	case "control":
		if len(os.Args) != 2 {
			panic("usage: control")
		}
	case "record", "record-audit", "replay", "compare-cross-build":
		if len(os.Args) != 3 {
			panic("usage: " + mode + " <trace-path>")
		}
	default:
		panic("unknown mode")
	}
	weak := mode == "control"
	wiring := s.NetworkWiring[uint8, uint8, uint8]{Routes: func(o s.NetworkAddress[uint8]) ([]s.NetworkRoute[uint8], error) {
		return []s.NetworkRoute[uint8]{{Destination: (o.Machine + 1) % 3, Channel: 7, Input: o.Value}}, nil
	}, CheckState: func(state s.NetworkState[uint8, uint8], sink *s.CheckSink) error {
		sum := 0
		for _, a := range state.Machines {
			sum += int(a.Value)
		}
		threshold := 2
		if weak {
			threshold = 3
		}
		if sum > threshold {
			return sink.Push(s.Failed("total", "total exceeds two"))
		}
		return sink.Push(s.Passed("total"))
	}}
	n, e := s.NewNetwork([]s.NetworkAddress[s.Model[uint8, uint8, uint8]]{{Machine: 0, Value: child}, {Machine: 1, Value: child}, {Machine: 2, Value: child}}, wiring, s.DefaultQueuePolicy())
	must(e)
	must(n.BindCodec(s.Uint8Codec(), s.Uint8Codec(), s.Uint8Codec(), func() (s.Metadata, error) { return s.Identity("ring-global", "fixture-v1"), nil }))
	// This independently specified outcome must hold on both recording and replay.
	// A completed weakened run is required: an error or early stop is not detection.
	expected := func(t *s.Trace, weakened bool) error {
		termination, status, detail := "property_failed", "failed", "total exceeds two"
		if weakened {
			termination, status, detail = "completed", "passed", ""
		}
		if t.Termination != termination || len(t.Steps) != 3 {
			return fmt.Errorf("unexpected termination or step count: %s %d", t.Termination, len(t.Steps))
		}
		last := t.Steps[2]
		state, err := n.Model.Codec.DecodeState(last.PostState)
		if err != nil {
			return err
		}
		if len(state.Machines) != 3 || len(state.Pending) != 1 {
			return fmt.Errorf("unexpected network shape")
		}
		for i, machine := range state.Machines {
			if machine.Machine != s.MachineID(i) || machine.Value != 1 {
				return fmt.Errorf("unexpected machine state")
			}
		}
		for _, check := range last.Checks {
			if check.ID == "global::total" && check.Status == status && check.Details == detail {
				return nil
			}
		}
		return fmt.Errorf("expected global check missing")
	}
	// The independent oracle reads only the decoded final network state, never
	// the model's own check results, so it can disagree with a weakened check.
	independentViolation := func(t *s.Trace) (bool, error) {
		if len(t.Steps) == 0 {
			return false, fmt.Errorf("trace has no steps")
		}
		state, err := n.Model.Codec.DecodeState(t.Steps[len(t.Steps)-1].PostState)
		if err != nil {
			return false, err
		}
		sum := 0
		for _, machine := range state.Machines {
			sum += int(machine.Value)
		}
		return sum > 2, nil
	}
	inputs := func() s.Iterator[s.NetworkInput[uint8]] {
		return s.Values([]s.NetworkInput[uint8]{{Kind: "local", Local: s.NetworkAddress[uint8]{Machine: 0, Value: 1}}, {Kind: "deliver"}, {Kind: "deliver"}})
	}
	if weak {
		t, err := s.Record(n.Model, inputs(), 10)
		must(err)
		// The weakened property must pass this run while the independent
		// requirement is violated; only that disagreement counts as detection.
		must(expected(t, true))
		violated, err := independentViolation(t)
		must(err)
		if !violated {
			panic("weakened property did not violate independent requirement")
		}
		fmt.Println("network-control-detected")
		return
	}
	if mode == "record-audit" {
		t, e := s.RecordAudit(n.Model, inputs(), s.RunConfig{Strategy: "stateful-fixture"}, 10, s.DefaultBinaryLimits())
		must(e)
		must(expected(&t.Trace, false))
		b, e := s.MarshalAuditTrace(t, s.DefaultBinaryLimits())
		must(e)
		must(os.WriteFile(os.Args[2], b, 0600))
		fmt.Println("audit recorded")
	} else if mode == "record" {
		t, e := s.Record(n.Model, inputs(), 10)
		must(e)
		must(expected(t, false))
		b, e := s.MarshalTrace(t)
		must(e)
		must(os.WriteFile(os.Args[2], b, 0600))
		fmt.Println("recorded")
	} else if mode == "replay" || mode == "compare-cross-build" {
		b, e := os.ReadFile(os.Args[2])
		must(e)
		t, e := s.ParseTrace(b)
		must(e)
		must(expected(t, false))
		if mode == "compare-cross-build" {
			own, e := n.Model.Codec.Metadata()
			must(e)
			if t.Metadata.Build != os.Getenv("STATEFUL_PRODUCER_BUILD") || own.Build != os.Getenv("STATEFUL_CONSUMER_BUILD") || t.Metadata.Build == own.Build {
				panic("build identity mismatch")
			}
			strict, e := s.Replay(n.Model, t, false)
			must(e)
			if strict.Outcome != "incompatible" || strict.BuildMatches || strict.StepsVerified != 0 || strict.FailureReproduced {
				panic("strict replay did not reject")
			}
			r, e := s.Replay(n.Model, t, true)
			must(e)
			if r.Outcome != "exact" || r.BuildMatches || r.StepsVerified != 3 || !r.FailureReproduced {
				panic("cross-build comparison failed")
			}
			fmt.Println(`STATEFUL_COMPARISON {"strict_outcome":"incompatible","strict_build_matches":false,"strict_steps_verified":0,"strict_failure_reproduced":false,"observation_outcome":"exact","observation_build_matches":false,"observation_steps_verified":3,"observation_failure_reproduced":true}`)
			return
		}
		r, e := s.Replay(n.Model, t, false)
		must(e)
		if r.Outcome != "exact" || !r.FailureReproduced {
			panic("failure not reproduced")
		}
		fmt.Println("exact failure reproduced")
	} else {
		panic("unknown mode")
	}
}

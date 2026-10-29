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
	wiring := s.NetworkWiring[uint8, uint8, uint8]{Routes: func(o s.NetworkAddress[uint8]) ([]s.NetworkRoute[uint8], error) {
		return []s.NetworkRoute[uint8]{{Destination: (o.Machine + 1) % 3, Channel: 7, Input: o.Value}}, nil
	}, CheckState: func(state s.NetworkState[uint8, uint8], sink *s.CheckSink) error {
		sum := 0
		for _, a := range state.Machines {
			sum += int(a.Value)
		}
		if sum > 2 {
			return sink.Push(s.Failed("total", "total exceeds two"))
		}
		return sink.Push(s.Passed("total"))
	}}
	n, e := s.NewNetwork([]s.NetworkAddress[s.Model[uint8, uint8, uint8]]{{Machine: 0, Value: child}, {Machine: 1, Value: child}, {Machine: 2, Value: child}}, wiring, s.DefaultQueuePolicy())
	must(e)
	must(n.BindCodec(s.Uint8Codec(), s.Uint8Codec(), s.Uint8Codec(), func() (s.Metadata, error) { return s.Identity("ring-global", "fixture-v1"), nil }))
	if os.Args[1] == "record" {
		t, e := s.Record(n.Model, s.Values([]s.NetworkInput[uint8]{{Kind: "local", Local: s.NetworkAddress[uint8]{Machine: 0, Value: 1}}, {Kind: "deliver"}, {Kind: "deliver"}}), 10)
		must(e)
		b, e := s.MarshalTrace(t)
		must(e)
		must(os.WriteFile(os.Args[2], b, 0600))
		fmt.Println("recorded")
	} else {
		b, e := os.ReadFile(os.Args[2])
		must(e)
		t, e := s.ParseTrace(b)
		must(e)
		r, e := s.Replay(n.Model, t, false)
		must(e)
		if r.Outcome != "exact" || !r.FailureReproduced {
			panic("failure not reproduced")
		}
		fmt.Println("exact failure reproduced")
	}
}

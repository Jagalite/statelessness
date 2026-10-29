package main

import (
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"os"
)

func main() {
	if len(os.Args) > 1 {
		networkMain()
		return
	}
	m := s.NewIndexedLifecycleMonitor[uint8](s.RetentionLimits{Obligations: 2, Resources: 2, Violations: 8})
	k := s.ScopedKey[uint8]{Epoch: 2, Generation: 3, Key: 7}
	h, e := m.Advance(s.LifecycleHistory[s.ScopedKey[uint8]]{}, []s.LifecycleEvent[s.ScopedKey[uint8]]{{Kind: "begin", Key: k, Time: 10}, {Kind: "settle", Key: k}, {Kind: "fence", Time: 3}})
	if e != nil {
		panic(e)
	}
	kc, e := s.ScopedKeyCodec(s.Uint8Codec())
	if e != nil {
		panic(e)
	}
	c, e := s.LifecycleHistoryCodec(m, kc)
	if e != nil {
		panic(e)
	}
	b, e := s.EncodeValue(c, h, 1024)
	if e != nil {
		panic(e)
	}
	h, e = s.FromValueBytes(c, b, s.DefaultDecodeLimits())
	if e != nil {
		panic(e)
	}
	h, e = m.Advance(h, []s.LifecycleEvent[s.ScopedKey[uint8]]{{Kind: "settle", Key: k}})
	if e != nil || h.Violations[0].Code != "retired_epoch_reference" {
		panic("stale reference accepted")
	}
	b, e = s.EncodeValue(c, h, 1024)
	if e != nil {
		panic(e)
	}
	fmt.Printf("%x\n", b)
}

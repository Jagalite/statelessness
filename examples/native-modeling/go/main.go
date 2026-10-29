package main

import (
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"strings"
)

func require(v bool) {
	if !v {
		panic("assertion failed")
	}
}
func check(e error) {
	if e != nil {
		panic(e)
	}
}
func rejects(e error, text string) { require(e != nil && strings.Contains(e.Error(), text)) }
func main() {
	_, err := s.CheckLazy(string([]byte{255}), true, nil)
	rejects(err, "invalid")
	var nested *s.Domain[int]
	entered := false
	nested, err = s.NewDomain(s.DomainDescriptor{MaxEntries: 1}, func(v int) int {
		if !entered {
			entered = true
			check(nested.Push(1))
		}
		return v
	})
	check(err)
	rejects(nested.Push(2), "limit")
	_, err = s.DomainProduct([]int{1}, []int{2}, 1, func(int, int) (int, error) { panic("map panic") })
	rejects(err, "map panic")
	rejects(nested.Extend(func() (int, bool, error) { panic("iterator panic") }), "iterator panic")
	panicClone := false
	panics, err := s.NewDomain(s.DomainDescriptor{MaxEntries: 1}, func(v int) int {
		if panicClone {
			panic("clone panic")
		}
		return v
	})
	check(err)
	check(panics.Push(1))
	panicClone = true
	_, err = panics.Entries()
	rejects(err, "clone panic")
	_, _, err = panics.SampleIndexed(s.NewRng(0))
	rejects(err, "clone panic")
	_, err = panics.Contains(1, func(int, int) bool { return true })
	rejects(err, "clone panic")

	d, e := s.NewDomain(s.DomainDescriptor{Name: "actions", Assumptions: "duplicates carry weight", MaxEntries: 4}, s.ValueCopy[int])
	check(e)
	check(d.Extend(s.Values([]int{3, 1, 3, 7})))
	rejects(d.Push(9), "limit")
	a, b := s.NewRng(42), s.NewRng(42)
	entries, e := d.Entries()
	check(e)
	for i := 0; i < 100; i++ {
		v, ok, e := d.SampleIndexed(a)
		check(e)
		index, _ := b.Index(4)
		require(ok && v == entries[index])
	}
	product, e := s.DomainProduct([]int{1, 2}, []int{4, 5}, 4, func(a, b int) (int, error) { return a + b, nil })
	check(e)
	require(fmt.Sprint(product) == "[5 6 6 7]")
	_, e = s.DomainProduct([]int{1, 2}, []int{4, 5}, 3, func(a, b int) (int, error) { panic("eager map") })
	rejects(e, "limit")
	_, e = s.CheckLazy("lazy", true, func() (string, error) { panic("eager") })
	check(e)
	c, e := s.CheckLazy("lazy", false, func() (string, error) { return "detail", nil })
	check(e)
	require(c.Details == "detail")
	_, e = s.CheckLazy("lazy", false, func() (string, error) { panic("callback") })
	rejects(e, "callback")
	_, e = s.DeclaredProperties([]s.PropertyMetadata{{ID: "p", Phase: "state"}, {ID: "p", Phase: "transition"}})
	rejects(e, "duplicate")
	owned, e := s.NewDomain(s.DomainDescriptor{MaxEntries: 2}, func(v []int) []int { return append([]int(nil), v...) })
	check(e)
	value := []int{1}
	check(owned.Push(value))
	value[0] = 2
	escaped, e := owned.Entries()
	check(e)
	escaped[0][0] = 3
	escaped, e = owned.Entries()
	check(e)
	require(escaped[0][0] == 1)
	codec, e := s.BindModelCodec(func() (s.Metadata, error) { return s.Identity("native-modeling-application", "teaching-v1"), nil }, s.Uint8Codec(), s.Uint8Codec(), s.Uint8Codec(), s.DefaultModelCodecOptions())
	check(e)
	m := s.Model[uint8, uint8, uint8]{CloneState: s.ValueCopy[uint8], CloneInput: s.ValueCopy[uint8], CloneOutput: s.ValueCopy[uint8], Codec: codec,
		Step: func(state, event uint8) (s.Transition[uint8, uint8], error) {
			if event == 1 {
				return s.Accept[uint8, uint8](1, 1), nil
			}
			if event == 3 {
				return s.Accept[uint8, uint8](0, 3), nil
			}
			if event == 2 && state == 1 {
				return s.Accept[uint8, uint8](2, 2), nil
			}
			return s.Transition[uint8, uint8]{State: state, Disposition: s.Rejected("not busy")}, nil
		},
		CheckState: func(state uint8) ([]s.Check, error) {
			c, e := s.CheckLazy("state.range", state <= 2, func() (string, error) { return "invalid", nil })
			return []s.Check{c}, e
		}, Inputs: func(uint8) (s.Iterator[uint8], error) { return s.Values([]uint8{1, 2, 3}), nil }}
	m, e = s.AdaptModel(m, []s.PropertyMetadata{{ID: "state.range", Phase: "state"}}, func() (uint8, error) { return 0, nil }, false)
	check(e)
	_, e = s.AdaptModel(m, []s.PropertyMetadata{{ID: "p", Phase: "state"}}, func() (uint8, error) { return 0, nil }, false)
	rejects(e, "conflicts")
	gen, e := s.Auto(m, func(a, b uint8) (bool, error) { return a == b, nil }, s.AutoOptions{MaxCandidates: 2})
	check(e)
	r, copy := s.NewRng(42), s.NewRng(42)
	_, _, e = gen.Generate(0, r)
	rejects(e, "max_candidates")
	require(r.NextU64() == copy.NextU64())
	encoded, e := codec.EncodeState(2)
	check(e)
	require(len(encoded) == 1 && encoded[0] == 2)
	_, e = codec.DecodeState([]byte{2, 0})
	rejects(e, "trailing")
	for _, events := range [][]uint8{{1, 3, 1, 2}, {1, 2}} {
		state, e := m.InitialState()
		check(e)
		var observed []uint8
		for _, event := range events {
			turn, e := m.Step(state, event)
			check(e)
			state = turn.State
			observed = append(observed, state)
		}
		trace, e := s.Record(m, s.Values(events), 10)
		check(e)
		report, e := s.Replay(m, trace, false)
		check(e)
		require(report.Outcome == "exact")
		for i, step := range trace.Steps {
			state, e := codec.DecodeState(step.PostState)
			check(e)
			require(state == observed[i])
		}
	}
	start, e := m.Step(0, 1)
	check(e)
	completion := make(chan uint8, 1)
	go func() { completion <- 2 }()
	event := <-completion
	finish, e := m.Step(start.State, event)
	check(e)
	require(finish.State == 2)
	trace, e := s.Record(m, s.Values([]uint8{1, event}), 10)
	check(e)
	report, e := s.Replay(m, trace, false)
	check(e)
	require(report.Outcome == "exact")
	fmt.Println(`{"engine":"go","controls":"passed","hosts":["ui","background","async"],"turns":8}`)
}

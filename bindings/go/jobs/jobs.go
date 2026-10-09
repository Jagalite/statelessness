// Package jobs is a pure, bounded job reducer used by the conformance example.
package jobs

import (
	"fmt"
	s "github.com/Jagalite/statelessness/bindings/go"
)

type Model struct {
	Faulty bool
	Build  string
}
type state struct{ phase, generation byte }

func decode(b []byte) (state, error) {
	if len(b) != 2 || b[0] > 4 || b[1] > 2 || (b[0] == 0) != (b[1] == 0) {
		return state{}, fmt.Errorf("invalid job state v1")
	}
	return state{b[0], b[1]}, nil
}
func (m Model) Metadata() s.Metadata {
	name := "go-jobs/event-v1"
	if m.Faulty {
		name += "/faulty"
	}
	return s.Metadata{Name: name, Build: m.Build, ModelVersion: 1, PropertiesVersion: 1, CodecVersion: 1}
}
func (m Model) Initial() ([]byte, error) { return []byte{0, 0}, nil }
func (m Model) Step(b, e []byte) (s.Transition, error) {
	st, err := decode(b)
	if err != nil {
		return s.Transition{}, err
	}
	if len(e) != 2 || e[0] > 4 || e[1] > 2 {
		return s.Transition{}, fmt.Errorf("invalid event v1")
	}
	n := st
	t := s.Transition{Disposition: 1, Reason: "invalid-phase"}
	accept := func(effect byte) { t.Disposition = 0; t.Reason = ""; t.Effects = [][]byte{{effect}} }
	switch e[0] {
	case 0:
		if (st.phase == 0 || st.phase >= 3) && st.generation < 2 {
			n.generation++
			n.phase = 1
			accept(0)
		}
	case 1:
		if st.phase == 1 {
			n.phase = 2
			accept(1)
		}
	case 2:
		if st.phase == 1 || st.phase == 2 {
			n.phase = 3
			accept(2)
		}
	case 3, 4:
		if e[1] != st.generation && !m.Faulty {
			t.Reason = "stale"
		} else if st.phase == 2 {
			n.phase = 4
			accept(3)
		} else if e[1] != st.generation {
			t.Reason = "stale"
		}
	}
	t.State = []byte{n.phase, n.generation}
	return t, nil
}
func (m Model) CheckState(b []byte) ([]s.Check, error) {
	_, e := decode(b)
	if e != nil {
		return nil, e
	}
	return []s.Check{{ID: "go.bounds"}}, nil
}
func (m Model) CheckTransition(b, e []byte, t s.Transition) ([]s.Check, error) {
	_, err := decode(b)
	if err != nil {
		return nil, err
	}
	_, err = decode(t.State)
	if err != nil {
		return nil, err
	}
	c := s.Check{ID: "go.rejection-preserves-state"}
	if t.Disposition != 0 && (b[0] != t.State[0] || b[1] != t.State[1]) {
		c.Status = 1
		c.Details = "rejected event mutated state"
	}
	return []s.Check{c}, nil
}
func (m Model) Inputs(b []byte) ([][]byte, error) {
	if _, e := decode(b); e != nil {
		return nil, e
	}
	var out [][]byte
	for kind := byte(0); kind <= 4; kind++ {
		for generation := byte(0); generation <= 2; generation++ {
			out = append(out, []byte{kind, generation})
		}
	}
	return out, nil
}

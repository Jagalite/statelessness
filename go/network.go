package statelessness

import (
	"fmt"
	"slices"
)

type MachineID uint32
type NetworkAddress[T any] struct {
	Machine MachineID
	Value   T
}
type NetworkRoute[I any] struct {
	Destination MachineID
	Channel     uint32
	Input       I
}
type NetworkMessage[I any] struct {
	Source MachineID
	Route  NetworkRoute[I]
}

func (m NetworkMessage[I]) channel() [3]uint32 {
	return [3]uint32{uint32(m.Source), uint32(m.Route.Destination), m.Route.Channel}
}

type NetworkInput[I any] struct {
	Kind  string
	Local NetworkAddress[I]
	Index uint32
}
type NetworkState[S, I any] struct {
	Machines []NetworkAddress[S]
	Pending  []NetworkMessage[I]
}
type QueuePolicy struct {
	Delivery                                      string
	MaxPending, MaxPerChannel, MaxRoutesPerOutput int
	AllowDrop, AllowDuplicate                     bool
}

func DefaultQueuePolicy() QueuePolicy { return QueuePolicy{"FifoPerChannel", 32, 32, 8, false, false} }

type NetworkWiring[S, I, O any] struct {
	Routes          func(NetworkAddress[O]) ([]NetworkRoute[I], error)
	CheckState      func(NetworkState[S, I], *CheckSink) error
	CheckTransition func(NetworkState[S, I], NetworkInput[I], Transition[NetworkState[S, I], NetworkAddress[O]], *CheckSink) error
}
type Network[S, I, O any] struct {
	Model[NetworkState[S, I], NetworkInput[I], NetworkAddress[O]]
	machines map[MachineID]Model[S, I, O]
	ids      []MachineID
	wiring   NetworkWiring[S, I, O]
	policy   QueuePolicy
}

func networkChecks(prefix string, sink *CheckSink, callback func(*CheckSink) error) error {
	checks := []Check{}
	err := invokeChecks("composed check", &checks, callback)
	for _, c := range checks {
		c.ID = prefix + "::" + c.ID
		prior, e := sink.Snapshot()
		if e != nil {
			return e
		}
		for _, p := range prior {
			if p.ID == c.ID {
				return &ModelError{"duplicate network property id: " + c.ID}
			}
		}
		if e := sink.Push(c); e != nil {
			return e
		}
	}
	return err
}
func NewNetwork[S, I, O any](machines []NetworkAddress[Model[S, I, O]], w NetworkWiring[S, I, O], p QueuePolicy) (*Network[S, I, O], error) {
	if len(machines) == 0 || p.MaxPending < 0 || uint64(p.MaxPending) > 0xffffffff || p.MaxPerChannel < 0 || p.MaxRoutesPerOutput < 0 || (p.Delivery != "Unordered" && p.Delivery != "FifoPerChannel") || w.Routes == nil || w.CheckState == nil {
		return nil, &ModelError{"invalid network configuration"}
	}
	n := &Network[S, I, O]{machines: map[MachineID]Model[S, I, O]{}, wiring: w, policy: p}
	for _, a := range machines {
		if _, ok := n.machines[a.Machine]; ok {
			return nil, &ModelError{"duplicate network machine id"}
		}
		if e := a.Value.validate(); e != nil {
			return nil, e
		}
		n.machines[a.Machine] = a.Value
		n.ids = append(n.ids, a.Machine)
	}
	slices.Sort(n.ids)
	n.CloneState = func(s NetworkState[S, I]) NetworkState[S, I] {
		s.Machines = slices.Clone(s.Machines)
		s.Pending = slices.Clone(s.Pending)
		for i, a := range s.Machines {
			a.Value = n.machines[a.Machine].CloneState(a.Value)
			s.Machines[i] = a
		}
		for i, m := range s.Pending {
			m.Route.Input = n.machines[m.Route.Destination].CloneInput(m.Route.Input)
			s.Pending[i] = m
		}
		return s
	}
	n.CloneInput = func(i NetworkInput[I]) NetworkInput[I] {
		if i.Kind == "local" {
			i.Local.Value = n.machines[i.Local.Machine].CloneInput(i.Local.Value)
		}
		return i
	}
	n.CloneOutput = func(o NetworkAddress[O]) NetworkAddress[O] {
		o.Value = n.machines[o.Machine].CloneOutput(o.Value)
		return o
	}
	n.InitialState = func() (NetworkState[S, I], error) {
		s := NetworkState[S, I]{}
		for _, id := range n.ids {
			v, e := n.machines[id].InitialState()
			if e != nil {
				return s, e
			}
			s.Machines = append(s.Machines, NetworkAddress[S]{id, n.machines[id].CloneState(v)})
		}
		return s, nil
	}
	n.Step = n.step
	n.Inputs = n.inputs
	n.CheckStateInto = func(s NetworkState[S, I], sink *CheckSink) error {
		if e := n.validateState(s); e != nil {
			return e
		}
		for _, a := range s.Machines {
			if e := networkChecks(fmt.Sprintf("machine/%d", a.Machine), sink, func(c *CheckSink) error { return appendStateChecks(n.machines[a.Machine], a.Value, c) }); e != nil {
				return e
			}
		}
		return networkChecks("global", sink, func(c *CheckSink) error { return w.CheckState(n.CloneState(s), c) })
	}
	n.CheckTransitionInto = func(s NetworkState[S, I], i NetworkInput[I], t Transition[NetworkState[S, I], NetworkAddress[O]], sink *CheckSink) error {
		if e := n.validateState(s); e != nil {
			return e
		}
		if e := n.validateState(t.State); e != nil {
			return e
		}
		a, e := n.addressed(s, i)
		if e != nil {
			return e
		}
		if a != nil {
			o := []O{}
			for _, v := range t.Outputs {
				if v.Machine != a.Machine {
					return &ModelError{"wrong network child output address"}
				}
				o = append(o, n.machines[a.Machine].CloneOutput(v.Value))
			}
			pos, _ := slices.BinarySearch(n.ids, a.Machine)
			ct := Transition[S, O]{t.State.Machines[pos].Value, o, t.Disposition}
			if e := networkChecks(fmt.Sprintf("machine/%d", a.Machine), sink, func(c *CheckSink) error {
				return appendEdgeChecks(n.machines[a.Machine], s.Machines[pos].Value, a.Value, ct, c)
			}); e != nil {
				return e
			}
		} else if len(t.Outputs) > 0 {
			return &ModelError{"network queue action emitted child output"}
		}
		if w.CheckTransition == nil {
			return nil
		}
		return networkChecks("global", sink, func(c *CheckSink) error {
			outputs := make([]NetworkAddress[O], len(t.Outputs))
			for j, o := range t.Outputs {
				outputs[j] = n.CloneOutput(o)
			}
			return w.CheckTransition(n.CloneState(s), n.CloneInput(i), Transition[NetworkState[S, I], NetworkAddress[O]]{n.CloneState(t.State), outputs, t.Disposition}, c)
		})
	}
	n.CheckState = func(s NetworkState[S, I]) ([]Check, error) {
		c := []Check{}
		e := invokeChecks("network state", &c, func(sink *CheckSink) error { return n.CheckStateInto(s, sink) })
		return c, e
	}
	n.CheckTransition = func(s NetworkState[S, I], i NetworkInput[I], t Transition[NetworkState[S, I], NetworkAddress[O]]) ([]Check, error) {
		c := []Check{}
		e := invokeChecks("network transition", &c, func(sink *CheckSink) error { return n.CheckTransitionInto(s, i, t, sink) })
		return c, e
	}
	return n, nil
}
func (n *Network[S, I, O]) validateState(s NetworkState[S, I]) error {
	if len(s.Machines) != len(n.ids) {
		return &ModelError{"network machine identity mismatch"}
	}
	for j, a := range s.Machines {
		if a.Machine != n.ids[j] {
			return &ModelError{"network machine identity mismatch"}
		}
	}
	if len(s.Pending) > n.policy.MaxPending {
		return &ModelError{"network pending bound exceeded; verification incomplete"}
	}
	counts := map[[3]uint32]int{}
	for _, m := range s.Pending {
		if _, ok := n.machines[m.Source]; !ok {
			return &ModelError{"unknown network source"}
		}
		if _, ok := n.machines[m.Route.Destination]; !ok {
			return &ModelError{"unknown network destination"}
		}
		counts[m.channel()]++
		if counts[m.channel()] > n.policy.MaxPerChannel {
			return &ModelError{"network channel bound exceeded; verification incomplete"}
		}
	}
	return nil
}
func (n *Network[S, I, O]) deliverable(s NetworkState[S, I], index int) bool {
	if n.policy.Delivery == "Unordered" {
		return true
	}
	for _, m := range s.Pending[:index] {
		if m.channel() == s.Pending[index].channel() {
			return false
		}
	}
	return true
}
func (n *Network[S, I, O]) addressed(s NetworkState[S, I], i NetworkInput[I]) (*NetworkAddress[I], error) {
	if i.Kind == "local" {
		if _, ok := n.machines[i.Local.Machine]; !ok {
			return nil, &ModelError{"unknown network local machine"}
		}
		return &i.Local, nil
	}
	if uint64(i.Index) >= uint64(len(s.Pending)) {
		return nil, &ModelError{"network pending index out of range"}
	}
	switch i.Kind {
	case "deliver":
		if !n.deliverable(s, int(i.Index)) {
			return nil, &ModelError{"network FIFO channel order violation"}
		}
		m := s.Pending[i.Index]
		return &NetworkAddress[I]{m.Route.Destination, m.Route.Input}, nil
	case "drop":
		if !n.policy.AllowDrop {
			return nil, &ModelError{"network dropping disabled"}
		}
	case "duplicate":
		if !n.policy.AllowDuplicate {
			return nil, &ModelError{"network duplication disabled"}
		}
	default:
		return nil, &ModelError{"invalid network action"}
	}
	return nil, nil
}
func (n *Network[S, I, O]) enqueue(s *NetworkState[S, I], m NetworkMessage[I]) error {
	child, ok := n.machines[m.Route.Destination]
	if !ok {
		return &ModelError{"unknown network route machine"}
	}
	count := 0
	for _, v := range s.Pending {
		if v.channel() == m.channel() {
			count++
		}
	}
	if len(s.Pending) >= n.policy.MaxPending || count >= n.policy.MaxPerChannel {
		return &ModelError{"network queue bound exceeded; verification incomplete"}
	}
	m.Route.Input = child.CloneInput(m.Route.Input)
	s.Pending = append(s.Pending, m)
	return nil
}
func (n *Network[S, I, O]) step(s NetworkState[S, I], i NetworkInput[I]) (Transition[NetworkState[S, I], NetworkAddress[O]], error) {
	var empty Transition[NetworkState[S, I], NetworkAddress[O]]
	if e := n.validateState(s); e != nil {
		return empty, e
	}
	a, e := n.addressed(s, i)
	if e != nil {
		return empty, e
	}
	next := n.CloneState(s)
	if i.Kind == "drop" || i.Kind == "deliver" {
		next.Pending = slices.Delete(next.Pending, int(i.Index), int(i.Index)+1)
	}
	if i.Kind == "duplicate" {
		if e := n.enqueue(&next, s.Pending[i.Index]); e != nil {
			return empty, e
		}
	}
	if a == nil {
		return Accept[NetworkState[S, I], NetworkAddress[O]](next), nil
	}
	pos, _ := slices.BinarySearch(n.ids, a.Machine)
	t, e := perform(n.machines[a.Machine], s.Machines[pos].Value, a.Value)
	if e != nil {
		return empty, e
	}
	next.Machines[pos].Value = t.State
	outputs := []NetworkAddress[O]{}
	for _, o := range t.Outputs {
		address := NetworkAddress[O]{a.Machine, o}
		outputs = append(outputs, n.CloneOutput(address))
		routes, e := n.wiring.Routes(n.CloneOutput(address))
		if e != nil {
			return empty, e
		}
		if len(routes) > n.policy.MaxRoutesPerOutput {
			return empty, &ModelError{"network route fanout bound exceeded; verification incomplete"}
		}
		for _, r := range routes {
			if e := n.enqueue(&next, NetworkMessage[I]{a.Machine, r}); e != nil {
				return empty, e
			}
		}
	}
	return Transition[NetworkState[S, I], NetworkAddress[O]]{next, outputs, t.Disposition}, nil
}
func (n *Network[S, I, O]) inputs(s NetworkState[S, I]) (Iterator[NetworkInput[I]], error) {
	if e := n.validateState(s); e != nil {
		return nil, e
	}
	values := []NetworkInput[I]{}
	for _, a := range s.Machines {
		m := n.machines[a.Machine]
		if m.Inputs == nil {
			return nil, &ModelError{"network child lacks enumeration"}
		}
		it, e := m.Inputs(m.CloneState(a.Value))
		if e != nil {
			return nil, e
		}
		for {
			v, ok, e := it()
			if e != nil {
				return nil, e
			}
			if !ok {
				break
			}
			values = append(values, NetworkInput[I]{Kind: "local", Local: NetworkAddress[I]{a.Machine, m.CloneInput(v)}})
		}
	}
	for j, m := range s.Pending {
		if n.deliverable(s, j) {
			values = append(values, NetworkInput[I]{Kind: "deliver", Index: uint32(j)})
		}
		if n.policy.AllowDrop {
			values = append(values, NetworkInput[I]{Kind: "drop", Index: uint32(j)})
		}
		count := 0
		for _, v := range s.Pending {
			if m.channel() == v.channel() {
				count++
			}
		}
		if n.policy.AllowDuplicate && len(s.Pending) < n.policy.MaxPending && count < n.policy.MaxPerChannel {
			values = append(values, NetworkInput[I]{Kind: "duplicate", Index: uint32(j)})
		}
	}
	return Values(values), nil
}

// WithInputEquality enables search identity including the exact queued payloads.
func (n *Network[S, I, O]) WithInputEquality(equal func(I, I) bool) Model[NetworkState[S, I], NetworkInput[I], NetworkAddress[O]] {
	m := n.Model
	m.EqualStates = func(a, b NetworkState[S, I]) bool {
		if equal == nil {
			panic("input equality required")
		}
		if n.validateState(a) != nil || n.validateState(b) != nil {
			return false
		}
		for j, s := range a.Machines {
			child := n.machines[s.Machine]
			if child.EqualStates == nil {
				panic("child equality required")
			}
			if !child.EqualStates(s.Value, b.Machines[j].Value) {
				return false
			}
		}
		if len(a.Pending) != len(b.Pending) {
			return false
		}
		for j, v := range a.Pending {
			if v.channel() != b.Pending[j].channel() || !equal(v.Route.Input, b.Pending[j].Route.Input) {
				return false
			}
		}
		return true
	}
	return m
}

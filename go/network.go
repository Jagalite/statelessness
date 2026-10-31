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

// Machine returns the state stored for id in the canonical sorted machine list.
// Like direct access to Machines, reference-containing values alias that state.
func (s NetworkState[S, I]) Machine(id MachineID) (S, bool) {
	pos, found := slices.BinarySearchFunc(s.Machines, id, func(a NetworkAddress[S], b MachineID) int {
		switch {
		case a.Machine < b:
			return -1
		case a.Machine > b:
			return 1
		default:
			return 0
		}
	})
	if !found {
		var zero S
		return zero, false
	}
	return s.Machines[pos].Value, true
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

// Policy returns the queue policy captured at construction.
func (n *Network[S, I, O]) Policy() QueuePolicy { return n.policy }

// MachineIDs returns a detached, ascending list of configured machine IDs.
func (n *Network[S, I, O]) MachineIDs() []MachineID { return slices.Clone(n.ids) }

// Wiring returns the callback set captured at construction.
func (n *Network[S, I, O]) Wiring() NetworkWiring[S, I, O] { return n.wiring }

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
		// Capture queued inputs before machine clone hooks can mutate their aliases.
		for i, m := range s.Pending {
			m.Route.Input = n.machines[m.Route.Destination].CloneInput(m.Route.Input)
			s.Pending[i] = m
		}
		for i, a := range s.Machines {
			a.Value = n.machines[a.Machine].CloneState(a.Value)
			s.Machines[i] = a
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
	hasInitial := true
	for _, id := range n.ids {
		if n.machines[id].InitialState == nil {
			hasInitial = false
			break
		}
	}
	if hasInitial {
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
	}
	hasStep, hasInputs := true, true
	for _, id := range n.ids {
		hasStep = hasStep && n.machines[id].Step != nil
		hasInputs = hasInputs && n.machines[id].Inputs != nil
	}
	if hasStep {
		n.Step = n.step
	}
	if hasInputs {
		n.Inputs = n.inputs
	}
	n.CheckStateInto = func(s NetworkState[S, I], sink *CheckSink) error {
		if e := n.validateState(s); e != nil {
			return e
		}
		// All machine states must be owned before the first child check runs.
		s = n.CloneState(s)
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
		if a == nil && len(t.Outputs) != 0 {
			return &ModelError{"network queue action emitted child output"}
		}
		if a != nil && len(t.Outputs) != 0 && t.Outputs[0].Machine != a.Machine {
			return &ModelError{"wrong network child output address"}
		}
		// Queue-only actions have no child transition to check. Once their
		// structure is valid there is nothing to snapshot without a global check.
		if a == nil && w.CheckTransition == nil {
			return nil
		}
		// Freeze address slots before application clone hooks can rewrite the
		// caller's slices, and preserve the selected input before state cloning.
		s.Machines = slices.Clone(s.Machines)
		s.Pending = slices.Clone(s.Pending)
		t.State.Machines = slices.Clone(t.State.Machines)
		t.State.Pending = slices.Clone(t.State.Pending)
		t.Outputs = slices.Clone(t.Outputs)
		input, inputErr := copied(n.CloneInput, i)
		before, beforeErr := copied(n.CloneState, s)
		after, afterErr := copied(n.CloneState, t.State)
		dispositionErr := t.Disposition.Validate()
		// Match the reference's ordered child-output mapping: an earlier
		// output clone failure precedes a later wrong address. Observations
		// above are captured first to isolate them from output callbacks; their
		// errors are deferred until mapping succeeds.
		outputs := make([]NetworkAddress[O], 0, len(t.Outputs))
		if a != nil {
			for _, output := range t.Outputs {
				if output.Machine != a.Machine {
					return &ModelError{"wrong network child output address"}
				}
				owned, err := copied(n.CloneOutput, output)
				if err != nil {
					return err
				}
				outputs = append(outputs, owned)
			}
		}
		if inputErr != nil {
			return inputErr
		}
		if beforeErr != nil {
			return beforeErr
		}
		if afterErr != nil {
			return afterErr
		}
		if dispositionErr != nil {
			return dispositionErr
		}
		i, s = input, before
		t = Transition[NetworkState[S, I], NetworkAddress[O]]{after, outputs, t.Disposition}
		a, e = n.addressed(s, i)
		if e != nil {
			return e
		}
		if a != nil {
			o := []O{}
			for _, v := range t.Outputs {
				if v.Machine != a.Machine {
					return &ModelError{"wrong network child output address"}
				}
				o = append(o, v.Value)
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
	// Capture the selected input before cloning state: application clone hooks
	// may otherwise mutate caller-owned input through an external alias.
	if a != nil {
		value, err := copied(n.machines[a.Machine].CloneInput, a.Value)
		if err != nil {
			return empty, err
		}
		a = &NetworkAddress[I]{a.Machine, value}
	}
	// Preserve the whole retained queue before machine state clone callbacks.
	// Those callbacks can hold aliases to any pending message, not only the
	// selected message.
	pending := slices.Clone(s.Pending)
	for index := range pending {
		message := &pending[index]
		message.Route.Input, e = copied(n.machines[message.Route.Destination].CloneInput, message.Route.Input)
		if e != nil {
			return empty, e
		}
	}
	stateOnly := s
	stateOnly.Pending = nil
	next := n.CloneState(stateOnly)
	next.Pending = pending
	if i.Kind == "drop" || i.Kind == "deliver" {
		next.Pending = slices.Delete(next.Pending, int(i.Index), int(i.Index)+1)
	}
	if i.Kind == "duplicate" {
		if e := n.enqueue(&next, next.Pending[i.Index]); e != nil {
			return empty, e
		}
	}
	if a == nil {
		return Accept[NetworkState[S, I], NetworkAddress[O]](next), nil
	}
	pos, _ := slices.BinarySearch(n.ids, a.Machine)
	t, e := perform(n.machines[a.Machine], next.Machines[pos].Value, a.Value)
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
	for _, a := range s.Machines {
		if n.machines[a.Machine].Inputs == nil {
			return nil, &ModelError{"network child lacks enumeration"}
		}
	}
	// Own the per-machine state and queue channel identities at admission.
	// Queue payloads are not needed to enumerate action indices.
	channels := make([][3]uint32, len(s.Pending))
	counts := make(map[[3]uint32]int)
	for i, message := range s.Pending {
		channels[i] = message.channel()
		counts[channels[i]]++
	}
	machines := slices.Clone(s.Machines)
	for i, a := range machines {
		machines[i] = NetworkAddress[S]{a.Machine, n.machines[a.Machine].CloneState(a.Value)}
	}
	machineIndex, queueIndex, queuePhase := 0, 0, 0
	var child Iterator[I]
	seen := make(map[[3]uint32]bool)
	done := false
	return func() (NetworkInput[I], bool, error) {
		var zero NetworkInput[I]
		if done {
			return zero, false, nil
		}
		for machineIndex < len(machines) {
			a := machines[machineIndex]
			m := n.machines[a.Machine]
			if child == nil {
				var err error
				child, err = m.Inputs(a.Value)
				if err != nil {
					done = true
					return zero, false, err
				}
				if child == nil {
					done = true
					return zero, false, &ModelError{"nil network child iterator"}
				}
			}
			value, ok, err := child()
			if err != nil {
				done = true
				return zero, false, err
			}
			if ok {
				return NetworkInput[I]{Kind: "local", Local: NetworkAddress[I]{a.Machine, m.CloneInput(value)}}, true, nil
			}
			child = nil
			machineIndex++
		}
		for queueIndex < len(channels) {
			channel := channels[queueIndex]
			switch queuePhase {
			case 0:
				queuePhase = 1
				eligible := n.policy.Delivery == "Unordered" || !seen[channel]
				seen[channel] = true
				if eligible {
					return NetworkInput[I]{Kind: "deliver", Index: uint32(queueIndex)}, true, nil
				}
			case 1:
				queuePhase = 2
				if n.policy.AllowDrop {
					return NetworkInput[I]{Kind: "drop", Index: uint32(queueIndex)}, true, nil
				}
			case 2:
				queuePhase = 3
				if n.policy.AllowDuplicate && len(channels) < n.policy.MaxPending && counts[channel] < n.policy.MaxPerChannel {
					return NetworkInput[I]{Kind: "duplicate", Index: uint32(queueIndex)}, true, nil
				}
			default:
				queueIndex++
				queuePhase = 0
			}
		}
		done = true
		return zero, false, nil
	}, nil
}

// WithInputEquality enables search identity including the exact queued payloads.
func (n *Network[S, I, O]) WithInputEquality(equal func(I, I) bool) Model[NetworkState[S, I], NetworkInput[I], NetworkAddress[O]] {
	m := n.Model
	if equal == nil {
		return m
	}
	for _, id := range n.ids {
		if n.machines[id].EqualStates == nil {
			return m
		}
	}
	m.EqualStates = func(a, b NetworkState[S, I]) bool {
		if n.validateState(a) != nil || n.validateState(b) != nil {
			return false
		}
		// Capture queue payloads before invoking machine state clone hooks: those
		// hooks can otherwise change a queued input through an external alias.
		// Copy both slices first so callbacks cannot alter later addresses.
		a.Machines, b.Machines = slices.Clone(a.Machines), slices.Clone(b.Machines)
		a.Pending, b.Pending = slices.Clone(a.Pending), slices.Clone(b.Pending)
		for _, state := range []*NetworkState[S, I]{&a, &b} {
			for j, msg := range state.Pending {
				msg.Route.Input = n.machines[msg.Route.Destination].CloneInput(msg.Route.Input)
				state.Pending[j] = msg
			}
		}
		for _, state := range []*NetworkState[S, I]{&a, &b} {
			for j, machine := range state.Machines {
				machine.Value = n.machines[machine.Machine].CloneState(machine.Value)
				state.Machines[j] = machine
			}
		}
		for j, s := range a.Machines {
			child := n.machines[s.Machine]
			if !child.EqualStates(child.CloneState(s.Value), child.CloneState(b.Machines[j].Value)) {
				return false
			}
		}
		if len(a.Pending) != len(b.Pending) {
			return false
		}
		for j, v := range a.Pending {
			if v.channel() != b.Pending[j].channel() || !equal(n.machines[v.Route.Destination].CloneInput(v.Route.Input), n.machines[b.Pending[j].Route.Destination].CloneInput(b.Pending[j].Route.Input)) {
				return false
			}
		}
		return true
	}
	return m
}

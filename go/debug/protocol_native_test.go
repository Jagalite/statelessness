package debug_test

import (
	"errors"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

func TestConcreteCandidateCanonicalSelection(t *testing.T) {
	allowed := []byte{1, 2}
	decoded := 0
	model := s.Model[int, byte, int]{InitialState: func() (int, error) { return 0, nil }, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[int], Step: func(state int, input byte) (s.Transition[int, int], error) {
		return s.Accept[int, int](state + int(input)), nil
	}, CheckState: func(int) ([]s.Check, error) { return nil, nil }, CheckTransition: func(int, byte, s.Transition[int, int]) ([]s.Check, error) { return nil, nil }, Inputs: func(int) (s.Iterator[byte], error) { return s.Values(allowed), nil }, Codec: &s.Codec[int, byte, int]{EncodeInput: func(input byte) ([]byte, error) { return []byte{input}, nil }}}
	session, err := d.NewSession("c", model, d.Unrestricted[int, byte](), func(a, b byte) (bool, error) { return a == b, nil }, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	p, err := d.NewProtocolSession(session, func(bytes []byte) (byte, error) {
		decoded++
		if len(bytes) != 1 {
			return 0, errors.New("bad input")
		}
		return bytes[0], nil
	}, d.ProtocolAuthority{DeliverInputs: true}, d.DefaultProtocolLimits())
	if err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(cmd string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "c", Epoch: p.Epoch(), ID: id, Revision: session.Revision(), Command: cmd, Args: args})
	}
	page := send("inputs", "u64:0", "u64:1")
	if page.Error != "" {
		t.Fatal(page)
	}
	for _, f := range page.Fields {
		if f.Key == "candidates.0.encoded" {
			t.Fatal("unauthorized exact value")
		}
	}
	if r := send("inputs", "u64:1000", "u64:1"); r.Error != "invalid_input" {
		t.Fatal(r)
	}
	allowed = []byte{2}
	if r := send("select", "u64:1"); r.Error != "invalid_input" {
		t.Fatal(r)
	}
	if decoded != 1 || session.Sequence() != 0 {
		t.Fatal("candidate admission side effects")
	}
	allowed = []byte{1, 2}
	if r := send("select", "u64:1"); r.Error != "" {
		t.Fatal(r)
	}
	if decoded != 2 || session.Sequence() != 1 {
		t.Fatal("missing canonical decode")
	}
	if r := send("select", "u64:1"); r.Error != "stale_handle" {
		t.Fatal(r)
	}
}

type protocolScalar uint64

func (v protocolScalar) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "counter", Version: 1}
}
func (v protocolScalar) Inspect(path []d.Segment, cx *d.InspectContext) (d.InspectNode, error) {
	return cx.Scalar(path, d.Scalar{Kind: "u64", Decimal: fmt.Sprint(uint64(v))})
}
func TestAttachedWatchBoundaries(t *testing.T) {
	m := s.Model[uint64, byte, uint64]{InitialState: func() (uint64, error) { return 0, nil }, CloneState: s.ValueCopy[uint64], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[uint64], Step: func(v uint64, i byte) (s.Transition[uint64, uint64], error) {
		return s.Accept[uint64, uint64](v + uint64(i)), nil
	}, CheckState: func(uint64) ([]s.Check, error) { return nil, nil }}
	session, err := d.NewSession("watch", m, d.Unrestricted[uint64, byte](), func(a, b byte) (bool, error) { return a == b, nil }, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	p, err := d.NewProtocolSession(session, func(b []byte) (byte, error) { return b[0], nil }, d.ProtocolAuthority{DeliverInputs: true, ConfigureWatches: true}, d.DefaultProtocolLimits())
	if err != nil {
		t.Fatal(err)
	}
	registry, _ := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	calls := 0
	if err = p.EnableWatches(registry, func(v uint64) d.InspectAdapter { calls++; return protocolScalar(v) }); err != nil {
		t.Fatal(err)
	}
	if calls != 0 {
		t.Fatal("disabled projection")
	}
	id, config := uint64(0), uint64(0)
	send := func(cmd string, args ...string) map[string]string {
		id++
		r := p.Handle(d.ProtocolRequest{Session: "watch", Epoch: p.Epoch(), ID: id, Revision: session.Revision(), Configuration: config, Command: cmd, Args: args})
		if r.Error != "" {
			t.Fatalf("%s: %s", cmd, r.Error)
		}
		config = r.Configuration
		fields := map[string]string{}
		for _, f := range r.Fields {
			fields[f.Key] = f.Value
		}
		return fields
	}
	ack := send("watch", "u64:1", "u64:0")
	if ack["watch.effective_sequence"] != "u64:1" || ack["watch.resolved_type"] != "enum:u64" {
		t.Fatal(ack)
	}
	send("step", "bytes:01")
	current := send("watch_current", "u64:1")
	if current["watch.kind"] != "enum:Baseline(Initial)" || current["watch.node.value"] != "u64:1" {
		t.Fatal(current)
	}
	send("step", "bytes:00")
	current = send("watch_current", "u64:1")
	if current["watch.snapshot_revision"] != "u64:2" || current["watch.kind"] != "enum:Unchanged" {
		t.Fatal(current)
	}
	status := send("watch_status")
	if status["watches.0.effective_sequence"] != "u64:1" {
		t.Fatal(status)
	}
	ack = send("unwatch", "u64:1")
	if ack["watch.generation"] != "u64:2" || ack["watch.effective_sequence"] != "u64:3" {
		t.Fatal(ack)
	}
}

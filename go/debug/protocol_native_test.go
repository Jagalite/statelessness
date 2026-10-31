package debug_test

import (
	"errors"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
	"strings"
	"testing"
)

type longSchemaValue uint64

func (v longSchemaValue) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: strings.Repeat("é", 50), Version: 1}
}
func (v longSchemaValue) Inspect(path []d.Segment, cx *d.InspectContext) (d.InspectNode, error) {
	return cx.Scalar(path, d.Scalar{Kind: "u64", Decimal: "7"})
}

func TestWatchAcknowledgementKeepsFittingMetadata(t *testing.T) {
	model := s.Model[uint64, byte, uint64]{InitialState: func() (uint64, error) { return 0, nil }, CloneState: s.ValueCopy[uint64], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[uint64], Step: func(v uint64, _ byte) (s.Transition[uint64, uint64], error) { return s.Accept[uint64, uint64](v), nil }}
	limits := d.DefaultProtocolLimits()
	limits.MaxFrame, limits.MaxField = 2048, 600
	found := false
	for count := 220; count <= 290; count++ {
		name := strings.Repeat("é", count)
		session, err := d.NewSession(name, model, d.Unrestricted[uint64, byte](), func(a, b byte) (bool, error) { return a == b, nil }, d.DefaultSessionLimits())
		if err != nil {
			t.Fatal(err)
		}
		protocol, err := d.NewProtocolSession(session, func(b []byte) (byte, error) { return b[0], nil }, d.ProtocolAuthority{ConfigureWatches: true}, limits)
		if err != nil {
			t.Fatal(err)
		}
		registry, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
		if err != nil {
			t.Fatal(err)
		}
		if err = protocol.EnableWatches(registry, func(v uint64) d.InspectAdapter { return longSchemaValue(v) }); err != nil {
			t.Fatal(err)
		}
		response := protocol.Handle(d.ProtocolRequest{Session: name, Epoch: protocol.Epoch(), ID: 1, Command: "watch", Args: []string{"u64:1", "u64:0"}})
		fields := map[string]string{}
		for _, field := range response.Fields {
			fields[field.Key] = field.Value
		}
		if response.Error == "" && fields["watch.metadata_complete"] == "bool:false" && fields["watch.display_schema_version"] == "u64:1" && fields["watch.resolved_type"] == "enum:truncated" {
			if _, exists := fields["watch.display_schema"]; exists {
				t.Fatal("oversized schema unexpectedly fit", response)
			}
			found = true
			break
		}
	}
	if !found {
		t.Fatal("no response budget admitted partial metadata")
	}
}

func TestWatchConfigurationContainsProjectionPanic(t *testing.T) {
	model := s.Model[uint64, byte, uint64]{InitialState: func() (uint64, error) { return 0, nil }, CloneState: s.ValueCopy[uint64], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[uint64], Step: func(v uint64, _ byte) (s.Transition[uint64, uint64], error) { return s.Accept[uint64, uint64](v), nil }}
	session, err := d.NewSession("watch-panic", model, d.Unrestricted[uint64, byte](), func(a, b byte) (bool, error) { return a == b, nil }, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	protocol, err := d.NewProtocolSession(session, func(b []byte) (byte, error) { return b[0], nil }, d.ProtocolAuthority{ConfigureWatches: true}, d.DefaultProtocolLimits())
	if err != nil {
		t.Fatal(err)
	}
	registry, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	if err = protocol.EnableWatches(registry, func(uint64) d.InspectAdapter { panic("projection failed") }); err != nil {
		t.Fatal(err)
	}
	response := protocol.Handle(d.ProtocolRequest{Session: "watch-panic", Epoch: protocol.Epoch(), ID: 1, Command: "watch", Args: []string{"u64:1", "u64:0"}})
	if response.Error != "callback" || registry.CaptureRevision() != 0 || len(response.Fields) != 0 {
		t.Fatal(response)
	}
}

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
	var keys []string
	send := func(cmd string, args ...string) map[string]string {
		id++
		r := p.Handle(d.ProtocolRequest{Session: "watch", Epoch: p.Epoch(), ID: id, Revision: session.Revision(), Configuration: config, Command: cmd, Args: args})
		if r.Error != "" {
			t.Fatalf("%s: %s", cmd, r.Error)
		}
		config = r.Configuration
		fields := map[string]string{}
		keys = keys[:0]
		for _, f := range r.Fields {
			keys = append(keys, f.Key)
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
	// Rust protocol.rs WatchStatus field order: totals and health, then rows.
	wantKeys := []string{"watches.total", "watches.truncated", "watches.dropped_events", "watches.observation_gaps", "watches.inspection_errors", "watches.0.id", "watches.0.generation", "watches.0.enabled", "watches.0.has_baseline", "watches.0.effective_sequence"}
	if strings.Join(keys, ",") != strings.Join(wantKeys, ",") {
		t.Fatalf("watch_status order %v", keys)
	}
	ack = send("unwatch", "u64:1")
	if ack["watch.generation"] != "u64:2" || ack["watch.effective_sequence"] != "u64:3" {
		t.Fatal(ack)
	}
	events := send("events", "u64:4")
	if events["events.returned"] != "u64:4" || events["events.0.kind"] != "enum:watch_configured" || events["events.1.kind"] != "enum:transition" || events["events.2.kind"] != "enum:transition" || events["events.3.kind"] != "enum:watch_removed" {
		t.Fatal("event order", events)
	}
}

func TestArbitraryFloatWatchProtocolAcknowledgement(t *testing.T) {
	m := s.Model[uint64, byte, uint64]{InitialState: func() (uint64, error) { return 0, nil }, CloneState: s.ValueCopy[uint64], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[uint64], Step: func(v uint64, _ byte) (s.Transition[uint64, uint64], error) { return s.Accept[uint64, uint64](v), nil }}
	session, err := d.NewSession("float", m, d.Unrestricted[uint64, byte](), func(a, b byte) (bool, error) { return a == b, nil }, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	p, err := d.NewProtocolSession(session, func(b []byte) (byte, error) { return b[0], nil }, d.ProtocolAuthority{ConfigureWatches: true}, d.DefaultProtocolLimits())
	if err != nil {
		t.Fatal(err)
	}
	registry, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	if err = p.EnableWatches(registry, func(uint64) d.InspectAdapter { return scalarProjection{d.FloatScalar("f16", "x", 3)} }); err != nil {
		t.Fatal(err)
	}
	response := p.Handle(d.ProtocolRequest{Session: "float", Epoch: p.Epoch(), ID: 1, Revision: session.Revision(), Command: "watch", Args: []string{"u64:1", "u64:0"}})
	if response.Error != "" {
		t.Fatal(response.Error)
	}
	fields := map[string]string{}
	for _, field := range response.Fields {
		fields[field.Key] = field.Value
	}
	if fields["watch.resolved_type"] != "enum:f16" {
		t.Fatalf("wrong resolved type: %#v", fields)
	}
}

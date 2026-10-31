package debug_test

import (
	"strings"
	"testing"

	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
)

type longSchemaScalar struct{ protocolScalar }

func (v longSchemaScalar) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: strings.Repeat("s", 8185), Version: 1}
}

func TestProtocolInspectionSchemaUsesFieldLimit(t *testing.T) {
	model := s.Model[uint64, byte, uint64]{InitialState: func() (uint64, error) { return 7, nil }, CloneState: s.ValueCopy[uint64], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[uint64], Step: func(v uint64, _ byte) (s.Transition[uint64, uint64], error) { return s.Accept[uint64, uint64](v), nil }}
	session, err := d.NewSession("inspect", model, d.Unrestricted[uint64, byte](), func(a, b byte) (bool, error) { return a == b, nil }, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	p, err := d.NewProtocolSession(session, nil, d.ProtocolAuthority{}, d.DefaultProtocolLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err := p.EnableInspection(func(v uint64) d.InspectAdapter { return longSchemaScalar{protocolScalar(v)} }); err != nil {
		t.Fatal(err)
	}
	request := func(id uint64, command string, args ...string) d.ProtocolResponse {
		return p.Handle(d.ProtocolRequest{Session: "inspect", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if got := request(1, "snapshot"); got.Error != "" {
		t.Fatal(got)
	}
	got := request(2, "inspect", "u64:1", "u64:1", "u64:0", "u64:1")
	if got.Error != "" {
		t.Fatal(got)
	}
	if got.Fields[0].Key != "schema.name" || got.Fields[0].Value != "string:"+strings.Repeat("s", 8185) {
		t.Fatal("schema name was not retained")
	}
}

func TestProtocolInspectionProjection(t *testing.T) {
	model := s.Model[uint64, byte, uint64]{InitialState: func() (uint64, error) { return 7, nil }, CloneState: s.ValueCopy[uint64], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[uint64], Step: func(v uint64, _ byte) (s.Transition[uint64, uint64], error) { return s.Accept[uint64, uint64](v), nil }}
	session, err := d.NewSession("inspect", model, d.Unrestricted[uint64, byte](), func(a, b byte) (bool, error) { return a == b, nil }, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	p, err := d.NewProtocolSession(session, nil, d.ProtocolAuthority{}, d.DefaultProtocolLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err = p.EnableInspection(func(v uint64) d.InspectAdapter { return protocolScalar(v) }); err != nil {
		t.Fatal(err)
	}
	request := func(id uint64, command string, args ...string) d.ProtocolResponse {
		return p.Handle(d.ProtocolRequest{Session: "inspect", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	snapshot := request(1, "snapshot")
	if snapshot.Error != "" {
		t.Fatal(snapshot)
	}
	response := request(2, "inspect", "u64:1", "u64:1", "u64:0", "u64:1")
	if response.Error != "" {
		t.Fatal(response)
	}
	fields := map[string]string{}
	for _, f := range response.Fields {
		fields[f.Key] = f.Value
	}
	for key, want := range map[string]string{"schema.name": "string:counter", "schema.version": "u64:1", "snapshot.revision": "u64:0", "node.kind": "enum:integer", "node.value": "u64:7"} {
		if fields[key] != want {
			t.Fatalf("%s: got %q, want %q", key, fields[key], want)
		}
	}
	if got := request(3, "inspect", "u64:1", "u64:2", "u64:0", "u64:1").Error; got != "invalid_input" {
		t.Fatal(got)
	}
	if got := request(4, "inspect", "u64:1", "u64:1", "u64:0", "u64:0").Error; got != "limit" {
		t.Fatal(got)
	}
	if got := request(5, "inspect", "u64:9", "u64:1", "u64:0", "u64:1").Error; got != "stale_handle" {
		t.Fatal(got)
	}
}

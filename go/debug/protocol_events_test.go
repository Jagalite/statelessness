package debug_test

import (
	"fmt"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

func TestProtocolEventsOverflowDedupAndReconnect(t *testing.T) {
	count := 0
	g := model(&count)
	session, err := d.SessionFromState("events", g.Model, 0, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	limits := d.DefaultProtocolLimits()
	limits.MaxEventEntries = 1
	p, err := d.NewProtocolSession(session, func(b []byte) (int, error) { return int(b[0]), nil }, d.ProtocolAuthority{DeliverInputs: true}, limits)
	if err != nil {
		t.Fatal(err)
	}
	request := d.ProtocolRequest{Session: "events", Epoch: p.Epoch(), Command: "step", Args: []string{"bytes:01"}, ID: 1}
	if r := p.Handle(request); r.Error != "" {
		t.Fatal(r)
	}
	request.ID, request.Revision = 2, session.Revision()
	if r := p.Handle(request); r.Error != "" {
		t.Fatal(r)
	}
	request.ID, request.Command, request.Args = 3, "events", []string{"u64:1"}
	r := p.Handle(request)
	fields := map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["events.dropped"] != "u64:1" || fields["events.returned"] != "u64:1" || fields["events.0.sequence"] != "u64:1" || fields["events.0.kind"] != "enum:transition" {
		t.Fatal(r)
	}
	if again := p.Handle(request); fmt.Sprint(again.Fields) != fmt.Sprint(r.Fields) || again.Error != "" {
		t.Fatal("deduplicated poll changed", again)
	}
	p.Disconnect()
	if err = p.Reconnect(); err != nil {
		t.Fatal(err)
	}
	request.Epoch, request.ID = p.Epoch(), 1
	r = p.Handle(request)
	fields = map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["events.dropped"] != "u64:1" || fields["events.0.sequence"] != "u64:1" || fields["events.0.kind"] != "enum:reconnected" {
		t.Fatal(r)
	}
}

func TestProtocolEventsPreflightDoesNotDrain(t *testing.T) {
	count := 0
	g := model(&count)
	session, err := d.SessionFromState("events", g.Model, 0, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	limits := d.DefaultProtocolLimits()
	limits.MaxFields = 64
	p, err := d.NewProtocolSession(session, func(b []byte) (int, error) { return int(b[0]), nil }, d.ProtocolAuthority{DeliverInputs: true}, limits)
	if err != nil {
		t.Fatal(err)
	}
	for id := uint64(1); id <= 10; id++ {
		r := p.Handle(d.ProtocolRequest{Session: "events", Epoch: p.Epoch(), ID: id, Revision: session.Revision(), Command: "step", Args: []string{"bytes:01"}})
		if r.Error != "" {
			t.Fatal(r)
		}
	}
	request := d.ProtocolRequest{Session: "events", Epoch: p.Epoch(), ID: 11, Command: "events", Args: []string{"u64:10"}}
	if r := p.Handle(request); r.Error != "limit" {
		t.Fatal("expected bounded response", r)
	}
	request.ID, request.Args = 12, []string{"u64:1"}
	r := p.Handle(request)
	fields := map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["events.returned"] != "u64:1" || fields["events.0.sequence"] != "u64:1" {
		t.Fatal("failed preflight drained events", r)
	}
}

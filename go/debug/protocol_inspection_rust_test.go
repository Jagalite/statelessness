package debug_test

import (
	"os"
	"strings"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
	"github.com/Jagalite/statelessness/go/demo"
	demodbg "github.com/Jagalite/statelessness/go/demo/debug"
)

func TestProtocolInspectionRustWireDifferential(t *testing.T) {
	path := os.Getenv("STATELESS_RUST_INSPECTION_WIRE")
	if path == "" {
		path = "../../conformance/swift-inspection-wire-v1.tsv"
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	expected := map[string]string{}
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		parts := strings.Split(line, "\t")
		if len(parts) < 10 {
			t.Fatalf("invalid fixture %q", line)
		}
		expected[parts[0]] = strings.Join(parts[9:], "\t")
	}
	model := demo.Fixed().Model()
	session, err := d.NewSession("test", model, d.Unrestricted[demo.State, demo.Input](), func(a, b demo.Input) (bool, error) { return a == b, nil }, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	limits := d.DefaultProtocolLimits()
	p, err := d.NewProtocolSession(session, nil, d.ProtocolAuthority{}, limits)
	if err != nil {
		t.Fatal(err)
	}
	if err = p.EnableInspection(demodbg.RequestState); err != nil {
		t.Fatal(err)
	}
	snapshot := p.Handle(d.ProtocolRequest{Session: "test", Epoch: p.Epoch(), ID: 1, Command: "snapshot"})
	if snapshot.Error != "" {
		t.Fatal(snapshot)
	}
	cases := []struct {
		name string
		args []string
	}{
		{"root", []string{"u64:1", "u64:1", "u64:0", "u64:1"}},
		{"scalar", []string{"u64:1", "u64:1", "u64:0", "u64:1", "field:generation"}},
		{"page", []string{"u64:1", "u64:1", "u64:0", "u64:1", "field:pending"}},
		{"object_past_end", []string{"u64:1", "u64:1", "u64:9", "u64:1"}},
		{"sequence_past_end", []string{"u64:1", "u64:1", "u64:9", "u64:1", "field:pending"}},
		{"missing", []string{"u64:1", "u64:1", "u64:0", "u64:1", "field:missing"}},
		{"schema", []string{"u64:1", "u64:2", "u64:0", "u64:1"}},
	}
	for i, tc := range cases {
		response := p.Handle(d.ProtocolRequest{Session: "test", Epoch: p.Epoch(), ID: uint64(i + 2), Command: "inspect", Args: tc.args})
		wire, e := d.EncodeResponse(response, limits)
		if e != nil {
			t.Fatal(e)
		}
		parts := strings.Split(string(wire), "\t")
		if len(parts) < 9 {
			t.Fatalf("%s invalid response", tc.name)
		}
		got := strings.Join(parts[8:], "\t")
		if want, ok := expected[tc.name]; !ok || got != want {
			t.Errorf("%s\ngot  %s\nwant %s", tc.name, got, want)
		}
	}
}

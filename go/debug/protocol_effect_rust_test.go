package debug_test

import (
	"os"
	"strings"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

type advancingEffectClock struct{ next uint64 }

func (c *advancingEffectClock) Now() (d.LocalInstant, error) {
	n := c.next
	c.next += 100
	return d.LocalInstant{Domain: 7, Nanos: n}, nil
}
func TestProtocolEffectRustWireDifferential(t *testing.T) {
	path := os.Getenv("STATELESS_RUST_EFFECT_WIRE")
	if path == "" {
		path = "../../conformance/swift-effect-wire-v1.tsv"
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
	options := d.DefaultEffectOptions()
	options.Origin = d.OriginTestClock
	observer, err := d.NewEffectObserver(options, &advancingEffectClock{})
	if err != nil {
		t.Fatal(err)
	}
	effect, err := observer.Requested(d.RequestOrigin{Run: 1, Epoch: 1, Machine: 1, TransitionSequence: 1}, d.Labels{})
	if err != nil {
		t.Fatal(err)
	}
	observer.DebuggerPause(true)
	if err = observer.Admission(effect, true); err != nil {
		t.Fatal(err)
	}
	attempt, err := observer.AttemptCreated(effect)
	if err != nil {
		t.Fatal(err)
	}
	if err = observer.AttemptStarted(attempt); err != nil {
		t.Fatal(err)
	}
	p, _, _ := probeFixture(t, d.DefaultProtocolLimits())
	if err = p.AttachEffectObserver(observer); err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name, cmd string
		args      []string
	}{{"details", "effect_details", []string{"u64:1", "u64:1", "u64:1"}}, {"timeline", "effect_timeline", []string{"u64:1", "u64:1", "u64:1", "u64:4"}}}
	for i, tc := range cases {
		r := p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: uint64(i + 1), Command: tc.cmd, Args: tc.args})
		wire, e := d.EncodeResponse(r, d.DefaultProtocolLimits())
		if e != nil {
			t.Fatal(e)
		}
		parts := strings.Split(string(wire), "\t")
		if len(parts) < 9 {
			t.Fatal("invalid response", tc.name)
		}
		got := strings.Join(parts[8:], "\t")
		if want, ok := expected[tc.name]; !ok || got != want {
			t.Errorf("%s\ngot  %s\nwant %s", tc.name, got, want)
		}
	}
}

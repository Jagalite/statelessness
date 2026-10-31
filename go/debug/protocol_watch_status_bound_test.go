package debug_test

import (
	"fmt"
	"strconv"
	"testing"

	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
)

func TestWatchStatusReturnsWholeBoundedPrefix(t *testing.T) {
	for _, caseName := range []string{"fields", "frame"} {
		t.Run(caseName, func(t *testing.T) {
			m := s.Model[uint64, byte, uint64]{InitialState: func() (uint64, error) { return 0, nil }, CloneState: s.ValueCopy[uint64], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[uint64], Step: func(v uint64, i byte) (s.Transition[uint64, uint64], error) { return s.Accept[uint64, uint64](v), nil }, CheckState: func(uint64) ([]s.Check, error) { return nil, nil }}
			session, err := d.NewSession("bounded", m, d.Unrestricted[uint64, byte](), func(a, b byte) (bool, error) { return a == b, nil }, d.DefaultSessionLimits())
			if err != nil {
				t.Fatal(err)
			}
			limits := d.DefaultProtocolLimits()
			if caseName == "fields" {
				limits.MaxFields = 64
			} else {
				limits.MaxFrame = 2048
			}
			limits.MaxPage = 50
			p, err := d.NewProtocolSession(session, func([]byte) (byte, error) { return 0, nil }, d.ProtocolAuthority{}, limits)
			if err != nil {
				t.Fatal(err)
			}
			reg, err := d.NewWatchRegistry(d.DefaultWatchLimits(), d.AllowAllWatches())
			if err != nil {
				t.Fatal(err)
			}
			for i := 0; i < 30; i++ {
				if _, err = reg.Add(d.ChangedWatch(nil)); err != nil {
					t.Fatal(err)
				}
			}
			if err = p.EnableWatches(reg, func(v uint64) d.InspectAdapter { return protocolScalar(v) }); err != nil {
				t.Fatal(err)
			}
			response := p.Handle(d.ProtocolRequest{Session: "bounded", Epoch: p.Epoch(), ID: 1, Command: "watch_status"})
			if response.Error != "" {
				t.Fatal(response.Error)
			}
			if _, err = d.EncodeResponse(response, limits); err != nil {
				t.Fatal(err)
			}
			fields := map[string]string{}
			for _, f := range response.Fields {
				fields[f.Key] = f.Value
			}
			if fields["watches.total"] != "u64:30" || fields["watches.truncated"] != "bool:true" {
				t.Fatal(fields)
			}
			n := 0
			for {
				key := fmt.Sprintf("watches.%d.id", n)
				if _, ok := fields[key]; !ok {
					break
				}
				for _, suffix := range []string{"generation", "enabled", "has_baseline", "effective_sequence"} {
					if _, ok := fields[fmt.Sprintf("watches.%d.%s", n, suffix)]; !ok {
						t.Fatal("partial row", fields)
					}
				}
				if fields[key] != "u64:"+strconv.Itoa(n+1) {
					t.Fatal("wrong order", fields)
				}
				n++
			}
			if n == 0 || n >= 30 {
				t.Fatalf("unexpected retained rows %d", n)
			}
		})
	}
}

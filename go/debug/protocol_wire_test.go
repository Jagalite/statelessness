package debug_test

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"io"
	"math"
	"reflect"
	"testing"

	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
)

func wireSession(t *testing.T, authority d.ProtocolAuthority, limits d.ProtocolLimits) (*d.ProtocolSession[int, byte, int], *[]string) {
	t.Helper()
	calls := []string{}
	model := s.Model[int, byte, int]{
		InitialState: func() (int, error) { calls = append(calls, "initial"); return 0, nil },
		CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[int],
		Step: func(before int, input byte) (s.Transition[int, int], error) {
			calls = append(calls, fmt.Sprintf("step %d %d", before, input))
			switch input {
			case 0:
				return s.Transition[int, int]{State: before, Disposition: s.Ignored("")}, nil
			case 1:
				return s.Accept(before+1, before+1), nil
			case 2:
				return s.Transition[int, int]{State: before, Disposition: s.Rejected("private rejection")}, nil
			default:
				return s.Transition[int, int]{}, fmt.Errorf("private reducer detail")
			}
		},
		CheckState: func(state int) ([]s.Check, error) {
			calls = append(calls, fmt.Sprintf("state %d", state))
			if state > 2 {
				return []s.Check{s.Failed("bounded", "private state detail")}, nil
			}
			return []s.Check{s.Passed("bounded")}, nil
		},
		CheckTransition: func(before int, input byte, after s.Transition[int, int]) ([]s.Check, error) {
			calls = append(calls, fmt.Sprintf("transition %d %d %d", before, input, after.State))
			return []s.Check{s.Passed("transition")}, nil
		},
		Codec: &s.Codec[int, byte, int]{
			DecodeInput: func(input []byte) (byte, error) {
				calls = append(calls, fmt.Sprintf("decode %x", input))
				if len(input) != 1 || input[0] > 3 {
					return 0, fmt.Errorf("private decode detail")
				}
				return input[0], nil
			},
			EncodeOutputInto: func(value int, out *s.EncodeBuffer) error {
				var bytes [8]byte
				binary.LittleEndian.PutUint64(bytes[:], uint64(value))
				return out.Append(bytes[:])
			},
		},
	}
	session, err := d.NewSession("wire\té😀", model, d.Unrestricted[int, byte](), func(a, b byte) (bool, error) { return a == b, nil }, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	protocol, err := d.NewProtocolSession(session, model.Codec.DecodeInput, authority, limits)
	if err != nil {
		t.Fatal(err)
	}
	return protocol, &calls
}

func wireFields(t *testing.T, response d.ProtocolResponse, want map[string]string) {
	t.Helper()
	got := map[string]string{}
	for _, field := range response.Fields {
		if _, exists := got[field.Key]; exists {
			t.Fatal("duplicate field", field.Key)
		}
		got[field.Key] = field.Value
	}
	if response.Error != "" || !reflect.DeepEqual(got, want) {
		t.Fatalf("response=%+v\nwant=%v", response, want)
	}
}

// Field expectations come from public Rust protocol.rs, not a native response.
func TestWireCompleteStatusAndObservationFields(t *testing.T) {
	p, calls := wireSession(t, d.ProtocolAuthority{DeliverInputs: true, ReadExactValues: true}, d.DefaultProtocolLimits())
	r := d.ProtocolRequest{Session: "wire\té😀", ID: 1, Command: "status"}
	wireFields(t, p.Handle(r), map[string]string{
		"mode": "enum:simulation", "phase": "enum:Ready", "sequence": "u64:0",
		"environment": "string:out-of-domain-injection",
		"capability.input_delivery": "bool:true", "capability.inspection": "bool:false",
		"capability.watch_configuration": "bool:false", "capability.exact_values": "bool:true",
		"capability.live_control": "bool:false", "capability.live_restore": "bool:false", "capability.effects": "bool:false",
		"capability.probe_queries": "bool:false", "capability.probe_configuration": "bool:false", "capability.trace_export": "bool:false",
		"capability.effect_telemetry": "bool:false", "capability.metric_queries": "bool:false",
		"limits.telemetry_snapshot_bytes": "u64:8388608", "limits.frame_bytes": "u64:65536",
		"limits.dedup_entries": "u64:64", "limits.dedup_bytes": "u64:4194304", "limits.page_size": "u64:100",
		"events.dropped": "u64:0", "stop": "enum:none",
	})
	r.Epoch = p.Epoch()
	r.ID, r.Command = 2, "snapshot"
	wireFields(t, p.Handle(r), map[string]string{"handle": "u64:1", "snapshot.revision": "u64:0", "snapshot.sequence": "u64:0"})
	r.ID, r.Command, r.Args = 3, "checks", []string{"u64:1"}
	r.Revision = math.MaxUint64 // Checks bind the handle, not this request revision.
	wireFields(t, p.Handle(r), map[string]string{
		"checks.complete": "bool:true", "checks.total": "u64:1", "checks.first_failure": "none",
		"checks.returned": "u64:1", "checks.truncated": "bool:false", "checks.0.id": "string:bounded", "checks.0.status": "enum:passed",
	})
	r.ID, r.Command = 4, "outputs"
	wireFields(t, p.Handle(r), map[string]string{"outputs.total": "u64:0"})
	r.ID, r.Command, r.Args, r.Revision = 5, "step", []string{"bytes:01"}, 0
	wireFields(t, p.Handle(r), map[string]string{"delivered": "bool:true", "sequence": "u64:1"})
	r.ID, r.Command, r.Args, r.Revision = 6, "snapshot", nil, 1
	wireFields(t, p.Handle(r), map[string]string{"handle": "u64:2", "snapshot.revision": "u64:1", "snapshot.sequence": "u64:1"})
	r.ID, r.Command, r.Args = 7, "outputs", []string{"u64:2"}
	wireFields(t, p.Handle(r), map[string]string{
		"outputs.total": "u64:1", "disposition": "enum:accepted", "outputs.provenance": "enum:simulated",
		"outputs.payloads": "enum:exact_sensitive", "outputs.0.encoded": "bytes:0100000000000000", "outputs.truncated": "bool:false",
	})
	if want := []string{"initial", "state 0", "decode 01", "step 0 1", "state 1", "transition 0 1 1"}; !reflect.DeepEqual(*calls, want) {
		t.Fatal(*calls)
	}
}

func TestWireRejectionFailureAndCallbackOrder(t *testing.T) {
	p, calls := wireSession(t, d.ProtocolAuthority{DeliverInputs: true}, d.DefaultProtocolLimits())
	r := d.ProtocolRequest{Session: "wire\té😀", Epoch: p.Epoch(), Command: "step"}
	inputs := []byte{0, 2, 1, 1, 1}
	wantCalls := []string{"initial", "state 0"}
	state := 0
	for index, input := range inputs {
		r.ID, r.Revision, r.Args = uint64(index+1), uint64(index), []string{fmt.Sprintf("bytes:%02x", input)}
		before := state
		if input == 1 {
			state++
		}
		wantCalls = append(wantCalls, fmt.Sprintf("decode %02x", input), fmt.Sprintf("step %d %d", before, input), fmt.Sprintf("state %d", state), fmt.Sprintf("transition %d %d %d", before, input, state))
		wireFields(t, p.Handle(r), map[string]string{"delivered": "bool:true", "sequence": fmt.Sprintf("u64:%d", index+1)})
	}
	r.ID, r.Command, r.Args, r.Revision = 6, "snapshot", nil, 5
	wireFields(t, p.Handle(r), map[string]string{"handle": "u64:1", "snapshot.revision": "u64:5", "snapshot.sequence": "u64:5"})
	r.ID, r.Command, r.Args = 7, "checks", []string{"u64:1"}
	wireFields(t, p.Handle(r), map[string]string{
		"checks.complete": "bool:true", "checks.total": "u64:2", "checks.returned": "u64:2", "checks.truncated": "bool:false",
		"checks.first_failure.id": "string:bounded", "checks.first_failure.index": "u64:0", "checks.first_failure.phase": "enum:post_state", "checks.first_failure.sequence": "u64:5",
		"checks.0.id": "string:bounded", "checks.0.status": "enum:failed", "checks.1.id": "string:transition", "checks.1.status": "enum:passed",
	})
	r.ID, r.Command, r.Args = 8, "step", []string{"bytes:01"}
	if response := p.Handle(r); response.Error != "ended" {
		t.Fatal(response)
	}
	wantCalls = append(wantCalls, "decode 01") // Decode precedes the ended check.
	if !reflect.DeepEqual(*calls, wantCalls) {
		t.Fatal(*calls, wantCalls)
	}
	p, calls = wireSession(t, d.ProtocolAuthority{DeliverInputs: true}, d.DefaultProtocolLimits())
	r = d.ProtocolRequest{Session: "wire\té😀", Epoch: p.Epoch(), ID: 1, Command: "step", Args: []string{"bytes:03"}}
	wireFields(t, p.Handle(r), map[string]string{"delivered": "bool:false", "sequence": "u64:0"})
	if want := []string{"initial", "state 0", "decode 03", "step 0 3"}; !reflect.DeepEqual(*calls, want) {
		t.Fatal(*calls)
	}
}

func TestWireMalformedCommandsDoNotAdmitRequestIDs(t *testing.T) {
	for _, test := range []struct{ command string; args []string; category string }{
		{"status", []string{"u64:1"}, "malformed"},
		{"step", []string{"bytes:0"}, "malformed"},
		{"step", []string{"bytes:zz"}, "malformed"},
		{"checks", []string{"u64:18446744073709551616"}, "malformed"},
		{"unknown", nil, "unsupported"},
	} {
		t.Run(test.command+fmt.Sprint(test.args), func(t *testing.T) {
			p, calls := wireSession(t, d.ProtocolAuthority{}, d.DefaultProtocolLimits())
			limits := d.DefaultProtocolLimits()
			request := d.ProtocolRequest{Session: "wire\té😀", Epoch: p.Epoch(), ID: 99, Revision: math.MaxUint64, Command: test.command, Args: test.args}
			var input, output bytes.Buffer
			for _, r := range []d.ProtocolRequest{request, {Session: request.Session, Epoch: request.Epoch, ID: 1, Command: "status"}} {
				payload, err := d.EncodeRequest(r, limits)
				if err != nil { t.Fatal(err) }
				if err := d.WriteFrame(&input, payload, limits.MaxFrame); err != nil { t.Fatal(err) }
			}
			if err := p.Serve(&input, &output); err != nil { t.Fatal(err) }
			for index := 0; index < 2; index++ {
				payload, err := d.ReadFrame(&output, limits.MaxFrame)
				if err != nil { t.Fatal(err) }
				response, err := d.DecodeResponse(payload, limits)
				if err != nil { t.Fatal(err) }
				if index == 0 && (response.ID != 0 || response.Error != test.category || len(response.Fields) != 0) || index == 1 && (response.ID != 1 || response.Error != "") {
					t.Fatal(response)
				}
			}
			if !reflect.DeepEqual(*calls, []string{"initial", "state 0"}) { t.Fatal(*calls) }
		})
	}
}

func TestWireTypedDedupAndSnapshotEviction(t *testing.T) {
	limits := d.DefaultProtocolLimits()
	limits.MaxHandles = 1
	p, _ := wireSession(t, d.ProtocolAuthority{}, limits)
	r := d.ProtocolRequest{Session: "wire\té😀", ID: 9007199254740993, Command: "status"}
	first := p.Handle(r)
	r.Epoch = p.Epoch()
	if second := p.Handle(r); !reflect.DeepEqual(first, second) { t.Fatal("discovery retry", first, second) }
	r.ID, r.Command = 9007199254740994, "snapshot"
	wireFields(t, p.Handle(r), map[string]string{"handle": "u64:1", "snapshot.revision": "u64:0", "snapshot.sequence": "u64:0"})
	r.ID++
	wireFields(t, p.Handle(r), map[string]string{"handle": "u64:2", "snapshot.revision": "u64:0", "snapshot.sequence": "u64:0"})
	r.ID, r.Command, r.Args = r.ID+1, "checks", []string{"u64:1"}
	if response := p.Handle(r); response.Error != "stale_handle" { t.Fatal(response) }
	r.ID, r.Args = r.ID+1, []string{"u64:0002"}
	first = p.Handle(r)
	r.Args = []string{"u64:2"}
	if second := p.Handle(r); first.Error != "" || !reflect.DeepEqual(first, second) { t.Fatal(first, second) }
	r.ID, r.Command, r.Args = 0, "status", nil
	if response := p.Handle(r); response.Error != "malformed" { t.Fatal(response) }
}

func TestWireHeaderOnlyPayloadEOFIsTransportFailure(t *testing.T) {
	p, calls := wireSession(t, d.ProtocolAuthority{DeliverInputs: true}, d.DefaultProtocolLimits())
	var output bytes.Buffer
	if err := p.Serve(bytes.NewReader([]byte{0, 0, 0, 1}), &output); err != io.ErrUnexpectedEOF { t.Fatal(err) }
	if output.Len() != 0 || !reflect.DeepEqual(*calls, []string{"initial", "state 0"}) { t.Fatal(output.String(), *calls) }
}

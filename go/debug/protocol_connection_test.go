package debug_test

import (
	"bytes"
	"errors"
	"io"
	"math"
	"os"
	"reflect"
	"syscall"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

type connectionFixture struct {
	session        *d.Session[int, int, int]
	protocol       *d.ProtocolSession[int, int, int]
	steps, decodes int
}

func newConnectionFixture(t *testing.T, authority d.ProtocolAuthority) *connectionFixture {
	t.Helper()
	f := &connectionFixture{}
	g := model(&f.steps)
	var err error
	f.session, err = d.SessionFromState("connection", g.Model, 0, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	f.protocol, err = d.NewProtocolSession(f.session, func(input []byte) (int, error) {
		f.decodes++
		return g.Codec.DecodeInput(input)
	}, authority, d.DefaultProtocolLimits())
	if err != nil {
		t.Fatal(err)
	}
	return f
}

func (f *connectionFixture) request(id uint64, command string, args ...string) d.ProtocolRequest {
	return d.ProtocolRequest{Session: f.session.ID(), Epoch: f.protocol.Epoch(), ID: id, Revision: f.session.Revision(), Command: command, Args: args}
}

func connectionFrame(t *testing.T, request d.ProtocolRequest) []byte {
	t.Helper()
	limits := d.DefaultProtocolLimits()
	payload, err := d.EncodeRequest(request, limits)
	if err != nil {
		t.Fatal(err)
	}
	var frame bytes.Buffer
	if err := d.WriteFrame(&frame, payload, limits.MaxFrame); err != nil {
		t.Fatal(err)
	}
	return frame.Bytes()
}

func assertConnectionState(t *testing.T, f *connectionFixture, state int, revision uint64) {
	t.Helper()
	if f.session.State() != state || f.session.Revision() != revision || f.session.Sequence() != revision {
		t.Fatalf("state/revision/sequence = %d/%d/%d, want %d/%d/%d", f.session.State(), f.session.Revision(), f.session.Sequence(), state, revision, revision)
	}
}

// Admission order comes from Rust ProtocolSession::handle: identity precedes
// disconnected, which precedes request ID, cache, authority and revision checks.
func assertConnectionDisconnected(t *testing.T, f *connectionFixture, cached d.ProtocolRequest) {
	t.Helper()
	state, revision, sequence, phase := f.session.State(), f.session.Revision(), f.session.Sequence(), f.session.Phase()
	steps, decodes := f.steps, f.decodes
	discovery := f.request(50, "status")
	discovery.Epoch = 0
	zeroID := discovery
	zeroID.ID = 0
	fresh := f.request(math.MaxUint64, "step", "bytes:31")
	fresh.Revision = math.MaxUint64
	wrongSession := fresh
	wrongSession.Session, wrongSession.Epoch = "other", 0
	wrongEpoch := fresh
	wrongEpoch.Epoch = 0
	for _, test := range []struct {
		request  d.ProtocolRequest
		category string
	}{
		{cached, "disconnected"},
		{discovery, "disconnected"},
		{zeroID, "disconnected"},
		{fresh, "disconnected"},
		{f.request(99, "cancel"), "disconnected"},
		{wrongSession, "wrong_session"},
		{wrongEpoch, "wrong_epoch"},
	} {
		response := f.protocol.Handle(test.request)
		if response.Error != test.category || len(response.Fields) != 0 || response.ID != test.request.ID || response.Epoch != f.protocol.Epoch() || response.Revision != revision {
			t.Fatalf("request=%+v response=%+v want=%s", test.request, response, test.category)
		}
	}
	if f.steps != steps || f.decodes != decodes || f.session.State() != state || f.session.Revision() != revision || f.session.Sequence() != sequence || f.session.Phase() != phase {
		t.Fatal("disconnected admission invoked a model callback or changed the session")
	}
}

func TestProtocolConnectionExplicitDisconnectAndReconnect(t *testing.T) {
	f := newConnectionFixture(t, d.ProtocolAuthority{DeliverInputs: true, ConfigureWatches: true})
	step := f.request(1, "step", "bytes:31")
	committed := f.protocol.Handle(step)
	wireFields(t, committed, map[string]string{"delivered": "bool:true", "sequence": "u64:1"})
	if err := f.protocol.RegisterAdapter("watch", func(d.ProtocolRequest) (d.ProtocolPrepared, error) {
		return d.ProtocolPrepared{}, nil
	}); err != nil {
		t.Fatal(err)
	}
	if response := f.protocol.Handle(f.request(2, "watch", "u64:1", "u64:1")); response.Error != "" || response.Configuration != 1 {
		t.Fatal(response)
	}
	wireFields(t, f.protocol.Handle(f.request(3, "snapshot")), map[string]string{"handle": "u64:1", "snapshot.revision": "u64:1", "snapshot.sequence": "u64:1"})
	epoch := f.protocol.Epoch()
	if err := f.protocol.Reconnect(); err != d.ProtocolError("invalid_input") || f.protocol.Epoch() != epoch {
		t.Fatal("active reconnect changed identity or succeeded", err, f.protocol.Epoch())
	}
	if response := f.protocol.Handle(step); !reflect.DeepEqual(response, committed) || f.steps != 1 || f.decodes != 1 {
		t.Fatal("active reconnect discarded cached delivery", response, f.steps, f.decodes)
	}
	f.protocol.Disconnect()
	f.protocol.Disconnect()
	assertConnectionDisconnected(t, f, step)
	assertConnectionState(t, f, 1, 1)
	if err := f.protocol.Reconnect(); err != nil || f.protocol.Epoch() <= epoch {
		t.Fatal("reconnect did not allocate a fresh epoch", err, f.protocol.Epoch())
	}
	if response := f.protocol.Handle(step); response.Error != "wrong_epoch" {
		t.Fatal(response)
	}
	if response := f.protocol.Handle(f.request(1, "status")); response.Error != "" || response.Configuration != 1 || response.Revision != 1 {
		t.Fatal("reconnect did not reset IDs or preserve configuration", response)
	}
	if response := f.protocol.Handle(f.request(2, "checks", "u64:1")); response.Error != "stale_handle" {
		t.Fatal(response)
	}
	wireFields(t, f.protocol.Handle(f.request(3, "snapshot")), map[string]string{"handle": "u64:2", "snapshot.revision": "u64:1", "snapshot.sequence": "u64:1"})
	if response := f.protocol.Handle(f.request(4, "checks", "u64:1")); response.Error != "stale_handle" {
		t.Fatal("new snapshot revived an old handle", response)
	}
	if response := f.protocol.Handle(f.request(5, "checks", "u64:2")); response.Error != "" {
		t.Fatal(response)
	}
	wireFields(t, f.protocol.Handle(f.request(6, "step", "bytes:31")), map[string]string{"delivered": "bool:true", "sequence": "u64:2"})
	assertConnectionState(t, f, 2, 2)
	if f.steps != 2 || f.decodes != 2 {
		t.Fatal(f.steps, f.decodes)
	}
}

func TestProtocolConnectionServeEOFDisconnectsWithoutRollback(t *testing.T) {
	for _, committed := range []bool{false, true} {
		name := "initial"
		if committed {
			name = "committed"
		}
		t.Run(name, func(t *testing.T) {
			f := newConnectionFixture(t, d.ProtocolAuthority{DeliverInputs: committed})
			cached := f.request(1, "snapshot")
			input := []byte{}
			if committed {
				cached = f.request(1, "step", "bytes:31")
				input = connectionFrame(t, cached)
			} else if response := f.protocol.Handle(cached); response.Error != "" {
				t.Fatal(response)
			}
			var output bytes.Buffer
			if err := f.protocol.Serve(bytes.NewReader(input), &output); err != nil {
				t.Fatal(err)
			}
			state := 0
			if committed {
				state = 1
			}
			assertConnectionState(t, f, state, uint64(state))
			if f.steps != state || f.decodes != state {
				t.Fatal("EOF repeated a model callback", f.steps, f.decodes)
			}
			assertConnectionDisconnected(t, f, cached)
			if err := f.protocol.Reconnect(); err != nil {
				t.Fatal(err)
			}
			assertConnectionState(t, f, state, uint64(state))
			if !committed {
				if response := f.protocol.Handle(f.request(1, "step", "bytes:31")); response.Error != "unauthorized" || f.decodes != 0 || f.steps != 0 {
					t.Fatal("reconnect granted delivery authority", response)
				}
			}
		})
	}
}

type connectionReadError struct{ err error }

func (r connectionReadError) Read([]byte) (int, error) { return 0, r.err }

func TestProtocolConnectionReadFailuresDisconnect(t *testing.T) {
	readFailure := errors.New("transport read failure")
	for _, test := range []struct {
		name   string
		reader io.Reader
		want   error
	}{
		{"partial_header", bytes.NewReader([]byte{0}), io.ErrUnexpectedEOF},
		{"missing_payload", bytes.NewReader([]byte{0, 0, 0, 2}), io.ErrUnexpectedEOF},
		{"partial_payload", bytes.NewReader([]byte{0, 0, 0, 2, 'D'}), io.ErrUnexpectedEOF},
		{"zero_length", bytes.NewReader([]byte{0, 0, 0, 0}), d.ProtocolError("limit")},
		{"read_error", connectionReadError{readFailure}, readFailure},
	} {
		t.Run(test.name, func(t *testing.T) {
			f := newConnectionFixture(t, d.ProtocolAuthority{DeliverInputs: true})
			cached := f.request(1, "snapshot")
			if response := f.protocol.Handle(cached); response.Error != "" {
				t.Fatal(response)
			}
			var output bytes.Buffer
			if err := f.protocol.Serve(test.reader, &output); !errors.Is(err, test.want) {
				t.Fatal(err)
			}
			if output.Len() != 0 || f.steps != 0 || f.decodes != 0 {
				t.Fatal("incomplete frame was dispatched", output.String(), f.steps, f.decodes)
			}
			assertConnectionState(t, f, 0, 0)
			assertConnectionDisconnected(t, f, cached)
			if err := f.protocol.Reconnect(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestProtocolConnectionBrokenOSPipePreservesCommittedTurn(t *testing.T) {
	f := newConnectionFixture(t, d.ProtocolAuthority{DeliverInputs: true})
	step := f.request(1, "step", "bytes:31")
	reader, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	if err := reader.Close(); err != nil {
		t.Fatal(err)
	}
	// This descriptor is a real closed-reader OS pipe, not a synthetic writer.
	err = f.protocol.Serve(bytes.NewReader(connectionFrame(t, step)), writer)
	if !errors.Is(err, syscall.EPIPE) {
		t.Fatal("expected broken pipe", err)
	}
	assertConnectionState(t, f, 1, 1)
	if f.steps != 1 || f.decodes != 1 || f.session.Phase() != d.Paused {
		t.Fatal("failed write reran, cancelled or rolled back the turn", f.steps, f.decodes, f.session.Phase())
	}
	assertConnectionDisconnected(t, f, step)
	if err := f.protocol.Reconnect(); err != nil {
		t.Fatal(err)
	}
	assertConnectionState(t, f, 1, 1)
	if response := f.protocol.Handle(step); response.Error != "wrong_epoch" {
		t.Fatal(response)
	}
	if response := f.protocol.Handle(f.request(1, "status")); response.Error != "" || response.Revision != 1 || f.steps != 1 || f.decodes != 1 {
		t.Fatal("reconciliation delivered another input", response)
	}
}

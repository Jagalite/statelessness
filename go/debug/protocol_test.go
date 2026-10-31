package debug_test

import (
	"bytes"
	"errors"
	d "github.com/Jagalite/statelessness/go/debug"
	"io"
	"math"
	"os"
	"testing"
)

func TestProtocolGolden(t *testing.T) {
	l := d.DefaultProtocolLimits()
	r := d.ProtocolRequest{Session: "a\t<é", Epoch: math.MaxUint64, ID: 9007199254740993, Command: "status"}
	b, e := d.EncodeRequest(r, l)
	if e != nil {
		t.Fatal(e)
	}
	want := "DDBG\tu32:1\trequest\tstring:a%09%3C%C3%A9\tu64:18446744073709551615\tu64:9007199254740993\tu64:0\tu64:0\tstatus"
	if string(b) != want {
		t.Fatal(string(b))
	}
	q, e := d.DecodeRequest(b, l)
	if e != nil || q.Session != r.Session || q.ID != r.ID || q.Epoch != r.Epoch {
		t.Fatal(q, e)
	}
	var buf bytes.Buffer
	if e = d.WriteFrame(&buf, b, l.MaxFrame); e != nil {
		t.Fatal(e)
	}
	got, e := d.ReadFrame(&buf, l.MaxFrame)
	if e != nil || !bytes.Equal(got, b) {
		t.Fatal(e)
	}
	if _, e = d.ReadFrame(bytes.NewReader([]byte{0, 0}), l.MaxFrame); !errors.Is(e, io.ErrUnexpectedEOF) {
		t.Fatal(e)
	}
	if _, e = d.ReadFrame(bytes.NewReader([]byte{255, 255, 255, 255}), l.MaxFrame); e == nil {
		t.Fatal("oversize")
	}
}
func TestProtocolDedupAuthorityAndHandles(t *testing.T) {
	count := 0
	g := model(&count)
	s, _ := d.SessionFromState("p", g.Model, 10, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	l := d.DefaultProtocolLimits()
	l.MaxCache = 1
	p, e := d.NewProtocolSession(s, func(b []byte) (int, error) {
		if len(b) != 1 {
			return 0, errors.New("codec")
		}
		return int(b[0]), nil
	}, d.ProtocolAuthority{DeliverInputs: true}, l)
	if e != nil {
		t.Fatal(e)
	}
	r := d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: 1, Command: "snapshot"}
	a := p.Handle(r)
	if a.Error != "" {
		t.Fatal(a)
	}
	handle := a.Fields[0].Value
	r.ID = 2
	r.Command = "step"
	r.Args = []string{"bytes:01"}
	a = p.Handle(r)
	if a.Error != "" || a.Revision != 1 || count != 1 {
		t.Fatal(a, count)
	}
	if a = p.Handle(r); a.Error != "" || count != 1 {
		t.Fatal(a)
	}
	r.Args = []string{"bytes:02"}
	if p.Handle(r).Error != "duplicate_conflict" {
		t.Fatal("conflict")
	}
	r.ID = 1
	r.Command = "status"
	r.Args = nil
	if p.Handle(r).Error != "retired_request" {
		t.Fatal("retired")
	}
	r.ID = 3
	r.Revision = 1
	r.Command = "checks"
	r.Args = []string{handle}
	if p.Handle(r).Error != "stale_handle" {
		t.Fatal("handle")
	}
	old := p.Epoch()
	p.Disconnect()
	if err := p.Reconnect(); err != nil {
		t.Fatal(err)
	}
	r.ID = 1
	r.Command = "status"
	r.Args = nil
	r.Epoch = old
	if p.Handle(r).Error != "wrong_epoch" {
		t.Fatal("epoch")
	}
	r.Epoch = 0
	if p.Handle(r).Error != "" {
		t.Fatal("discovery")
	}
	p2, _ := d.NewProtocolSession(s, nil, d.ProtocolAuthority{}, l)
	r.Epoch = p2.Epoch()
	r.Command = "cancel"
	if p2.Handle(r).Error != "unauthorized" {
		t.Fatal("default authority")
	}
}
func TestProtocolServeMalformedContinues(t *testing.T) {
	count := 0
	g := model(&count)
	s, _ := d.SessionFromState("p", g.Model, 10, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	l := d.DefaultProtocolLimits()
	p, _ := d.NewProtocolSession(s, nil, d.ProtocolAuthority{}, l)
	var in, out bytes.Buffer
	d.WriteFrame(&in, []byte("bad"), l.MaxFrame)
	r := d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: 1, Command: "status"}
	b, _ := d.EncodeRequest(r, l)
	d.WriteFrame(&in, b, l.MaxFrame)
	if e := p.Serve(&in, &out); e != nil {
		t.Fatal(e)
	}
	for i := 0; i < 2; i++ {
		b, e := d.ReadFrame(&out, l.MaxFrame)
		if e != nil {
			t.Fatal(e)
		}
		a, e := d.DecodeResponse(b, l)
		if e != nil || i == 0 && a.Error != "malformed" || i == 1 && a.Error != "" {
			t.Fatal(a, e)
		}
	}
}

func TestProtocolStagedAdapter(t *testing.T) {
	count := 0
	g := model(&count)
	s, _ := d.SessionFromState("p", g.Model, 10, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	l := d.DefaultProtocolLimits()
	p, _ := d.NewProtocolSession(s, nil, d.ProtocolAuthority{ConfigureWatches: true}, l)
	commits := 0
	oversize := true
	e := p.RegisterAdapter("watch", func(r d.ProtocolRequest) (d.ProtocolPrepared, error) {
		value := "string:ok"
		if oversize {
			value = "string:" + string(bytes.Repeat([]byte{'x'}, l.MaxField))
		}
		return d.ProtocolPrepared{Fields: []d.ProtocolField{{Key: "value", Value: value}}, Commit: func() error { commits++; return nil }}, nil
	})
	if e != nil {
		t.Fatal(e)
	}
	r := d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: 1, Command: "watch", Args: []string{"u64:1", "u64:0", "field:x"}}
	if a := p.Handle(r); a.Error != "limit" || a.Configuration != 0 || commits != 0 {
		t.Fatal(a, commits)
	}
	oversize = false
	r.ID = 2
	if a := p.Handle(r); a.Error != "" || a.Configuration != 1 || commits != 1 {
		t.Fatal(a, commits)
	}
	if a := p.Handle(r); a.Error != "" || commits != 1 {
		t.Fatal(a)
	}
	r.ID = 3
	if a := p.Handle(r); a.Error != "stale_configuration" || commits != 1 {
		t.Fatal(a)
	}
	r.Command = "status"
	r.Args = nil
	r.ID = 4
	r.Revision = math.MaxUint64
	if a := p.Handle(r); a.Error != "" {
		t.Fatal(a)
	}
}

func TestProtocolCommandSpecificRevisionGuards(t *testing.T) {
	count := 0
	g := model(&count)
	session, _ := d.SessionFromState("guards", g.Model, 10, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	p, _ := d.NewProtocolSession(session, nil, d.ProtocolAuthority{ConfigureWatches: true}, d.DefaultProtocolLimits())
	for _, command := range []string{"checks", "outputs", "telemetry_health", "metric_catalog", "events", "unwatch", "watch"} {
		if err := p.RegisterAdapter(command, func(d.ProtocolRequest) (d.ProtocolPrepared, error) { return d.ProtocolPrepared{}, nil }); err != nil {
			t.Fatal(err)
		}
	}
	request := d.ProtocolRequest{Session: "guards", Epoch: p.Epoch(), ID: 1, Command: "snapshot"}
	snapshot := p.Handle(request)
	if snapshot.Error != "" {
		t.Fatal(snapshot)
	}
	handle := snapshot.Fields[0].Value
	request.Revision = math.MaxUint64
	request.Configuration = math.MaxUint64
	for _, test := range []struct {
		command string
		args    []string
	}{
		{"checks", []string{handle}}, {"outputs", []string{handle}},
		{"telemetry_health", nil}, {"metric_catalog", []string{"u64:0", "u64:1"}}, {"events", []string{"u64:1"}},
	} {
		request.ID++
		request.Command = test.command
		request.Args = test.args
		if response := p.Handle(request); response.Error != "" {
			t.Fatal(test.command, response)
		}
	}
	request.ID++
	request.Command = "unwatch"
	request.Args = []string{"u64:1"}
	request.Configuration = 0
	if response := p.Handle(request); response.Error != "" || response.Configuration != 1 {
		t.Fatal(response)
	}
	request.ID++
	request.Command = "watch"
	request.Args = []string{"u64:1", "u64:0"}
	request.Configuration = 1
	if response := p.Handle(request); response.Error != "stale_revision" {
		t.Fatal(response)
	}
	request.ID++
	request.Revision = 0
	request.Args = []string{"u64:1", "u64:1"}
	if response := p.Handle(request); response.Error != "stale_revision" {
		t.Fatal("watch baseline", response)
	}
	request.ID++
	request.Command = "snapshot"
	request.Args = nil
	request.Revision = math.MaxUint64
	if response := p.Handle(request); response.Error != "stale_revision" {
		t.Fatal(response)
	}
	// Independent authority is checked before a stale model revision.
	request.ID++
	request.Command = "step"
	request.Args = []string{"bytes:31"}
	if response := p.Handle(request); response.Error != "unauthorized" {
		t.Fatal(response)
	}
}

func TestProtocolOSPipeFramingAndDedup(t *testing.T) {
	count := 0
	g := model(&count)
	session, err := d.SessionFromState("pipe", g.Model, 0, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	limits := d.DefaultProtocolLimits()
	p, err := d.NewProtocolSession(session, g.Codec.DecodeInput, d.ProtocolAuthority{DeliverInputs: true}, limits)
	if err != nil {
		t.Fatal(err)
	}
	input, clientWrite, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer input.Close()
	defer clientWrite.Close()
	clientRead, output, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer clientRead.Close()
	defer output.Close()
	request := d.ProtocolRequest{Session: "pipe", Epoch: p.Epoch(), ID: 1, Command: "step", Args: []string{"bytes:31"}}
	wire, err := d.EncodeRequest(request, limits)
	if err != nil {
		t.Fatal(err)
	}
	// Duplicate requests travel as distinct OS frames but deliver only once.
	for n := 0; n < 2; n++ {
		if err := d.WriteFrame(clientWrite, wire, limits.MaxFrame); err != nil {
			t.Fatal(err)
		}
	}
	if err := clientWrite.Close(); err != nil {
		t.Fatal(err)
	}
	if err := p.Serve(input, output); err != nil {
		t.Fatal(err)
	}
	if err := output.Close(); err != nil {
		t.Fatal(err)
	}
	for n := 0; n < 2; n++ {
		responseWire, err := d.ReadFrame(clientRead, limits.MaxFrame)
		if err != nil {
			t.Fatal(err)
		}
		response, err := d.DecodeResponse(responseWire, limits)
		if err != nil || response.Error != "" || response.Revision != 1 {
			t.Fatal(response, err)
		}
	}
	if _, err := d.ReadFrame(clientRead, limits.MaxFrame); err != io.EOF {
		t.Fatal(err)
	}
	if count != 1 || session.State() != 1 {
		t.Fatal("duplicate OS frame delivered twice", count)
	}
}

func TestProtocolOSPipePartialFrameNeverDispatches(t *testing.T) {
	count := 0
	g := model(&count)
	session, _ := d.SessionFromState("partial", g.Model, 0, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	limits := d.DefaultProtocolLimits()
	p, _ := d.NewProtocolSession(session, g.Codec.DecodeInput, d.ProtocolAuthority{DeliverInputs: true}, limits)
	input, writer, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	defer input.Close()
	defer writer.Close()
	// Header announces ten payload bytes, but the pipe closes after two.
	if _, err := writer.Write([]byte{0, 0, 0, 10, 'D', 'D'}); err != nil {
		t.Fatal(err)
	}
	writer.Close()
	var output bytes.Buffer
	if err := p.Serve(input, &output); err != io.ErrUnexpectedEOF {
		t.Fatal(err)
	}
	if count != 0 || session.Revision() != 0 || output.Len() != 0 {
		t.Fatal("partial frame dispatched", count, output.String())
	}
}

func TestProtocolPreflightsRevisionGrowthAndReadCallbacks(t *testing.T) {
	count := 0
	g := model(&count)
	session, _ := d.SessionFromState("reserve", g.Model, 0, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	limits := d.DefaultProtocolLimits()
	fields := []d.ProtocolField{{Key: "padding", Value: "string:" + string(bytes.Repeat([]byte{'x'}, 2100))}}
	wire, err := d.EncodeResponse(d.ProtocolResponse{Session: "reserve", Epoch: 1, ID: 1, Fields: fields}, limits)
	if err != nil {
		t.Fatal(err)
	}
	limits.MaxFrame = len(wire)
	p, err := d.NewProtocolSession(session, nil, d.ProtocolAuthority{DeliverInputs: true}, limits)
	if err != nil {
		t.Fatal(err)
	}
	// Account for the host epoch's actual width while reserving exactly the
	// current-revision response. A revision growth must reject before Commit.
	wire, err = d.EncodeResponse(d.ProtocolResponse{Session: "reserve", Epoch: p.Epoch(), ID: 1, Fields: fields}, d.DefaultProtocolLimits())
	if err != nil {
		t.Fatal(err)
	}
	limits.MaxFrame = len(wire)
	p, err = d.NewProtocolSession(session, nil, d.ProtocolAuthority{DeliverInputs: true}, limits)
	if err != nil {
		t.Fatal(err)
	}
	commits := 0
	if err := p.RegisterAdapter("select", func(d.ProtocolRequest) (d.ProtocolPrepared, error) {
		if err := session.Cancel(0); err == nil {
			t.Fatal("prepare mutated session")
		}
		return d.ProtocolPrepared{Fields: fields, Commit: func() error { commits++; _, err := session.Step(0, 1); return err }}, nil
	}); err != nil {
		t.Fatal(err)
	}
	response := p.Handle(d.ProtocolRequest{Session: "reserve", Epoch: p.Epoch(), ID: 1, Command: "select", Args: []string{"u64:1"}})
	if response.Error != "limit" || commits != 0 || count != 0 || session.Revision() != 0 {
		t.Fatal(response, commits, count)
	}

	// Decode is read-only even when it was supplied separately from Model.Codec.
	decoder := func(b []byte) (int, error) {
		if err := session.Cancel(0); err == nil {
			t.Fatal("decoder mutated session")
		}
		return g.Codec.DecodeInput(b)
	}
	q, _ := d.NewProtocolSession(session, decoder, d.ProtocolAuthority{DeliverInputs: true}, d.DefaultProtocolLimits())
	response = q.Handle(d.ProtocolRequest{Session: "reserve", Epoch: q.Epoch(), ID: 1, Command: "step", Args: []string{"bytes:31"}})
	if response.Error != "" || count != 1 {
		t.Fatal(response, count)
	}
}

func TestProtocolStepMaxInputDoesNotOverflow(t *testing.T) {
	count := 0
	g := model(&count)
	session, err := d.SessionFromState("huge-input", g.Model, 0, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	limits := d.DefaultProtocolLimits()
	limits.MaxInput = math.MaxInt
	p, err := d.NewProtocolSession(session, func(b []byte) (int, error) { return int(b[0]), nil }, d.ProtocolAuthority{DeliverInputs: true}, limits)
	if err != nil {
		t.Fatal(err)
	}
	response := p.Handle(d.ProtocolRequest{Session: "huge-input", Epoch: p.Epoch(), ID: 1, Command: "step", Args: []string{"bytes:31"}})
	if response.Error != "" {
		t.Fatalf("step rejected with huge input limit: %+v", response)
	}
}

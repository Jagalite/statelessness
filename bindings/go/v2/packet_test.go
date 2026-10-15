package v2

import (
	"bytes"
	"encoding/binary"
	"testing"
)

func portableFixture() *Trace {
	return &Trace{Metadata: Metadata{Name: "counter", Build: "test", ModelVersion: 1, PropertiesVersion: 1, CodecVersion: 1}, InitialState: []byte{0}, InitialChecks: []Check{Passed("valid")}, Steps: []TraceStep{{Input: []byte{1}, Disposition: Accepted(), Outputs: [][]byte{{2}}, PostState: []byte{1}, Checks: []Check{Failed("bound", "limit")}}}, Termination: "property_failed"}
}
func TestPortablePacketRoundTrip(t *testing.T) {
	original := portableFixture()
	packet, err := TracePacket(original)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := PacketTrace(packet)
	if err != nil {
		t.Fatal(err)
	}
	want, err := MarshalTrace(original)
	if err != nil {
		t.Fatal(err)
	}
	got, err := MarshalTrace(decoded)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(want, got) {
		t.Fatalf("different observation envelopes: %s != %s", want, got)
	}
	for i := 0; i < len(packet); i++ {
		if _, err := PacketTrace(packet[:i]); err == nil {
			t.Fatalf("accepted truncated packet at %d", i)
		}
	}
	if _, err := PacketTrace(append(packet, 0)); err == nil {
		t.Fatal("accepted trailing packet")
	}
}
func TestPortableStrictJSON(t *testing.T) {
	valid, err := MarshalTrace(portableFixture())
	if err != nil {
		t.Fatal(err)
	}
	invalid := [][]byte{[]byte(`{"format":"a","format":"b"}`), []byte(`{"bad":"\ud800"}`), bytes.Replace(valid, []byte(`"version":1`), []byte(`"version":1.0`), 1), bytes.Replace(valid, []byte(`"initial_state":"00"`), []byte(`"initial_state":"FF"`), 1)}
	for _, data := range invalid {
		if _, err := ParseTrace(data); err == nil {
			t.Fatalf("accepted invalid JSON: %s", data)
		}
	}
}
func TestPacketOversizedCount(t *testing.T) {
	packet := append([]byte(packetMagic), make([]byte, 4)...)
	binary.LittleEndian.PutUint32(packet[8:], ^uint32(0))
	if _, err := PacketTrace(packet); err == nil {
		t.Fatal("accepted impossible blob size")
	}
}
func TestExpiredCallbackRNG(t *testing.T) {
	r := &CallbackRNG{}
	if _, err := r.Next(); err == nil {
		t.Fatal("expired next accepted")
	}
	if _, _, err := r.Index(2); err == nil {
		t.Fatal("expired index accepted")
	}
}

func TestPacketParametersDoNotConsumePortableItems(t *testing.T) {
	w := packetWriter{maximum: 4096}
	w.raw([]byte(packetMagic))
	w.text("counter")
	w.text("test")
	w.version(1)
	w.version(1)
	w.version(1)
	w.text("engine-run")
	w.byte(0)
	w.u32(1)
	w.text("provenance")
	w.text("omitted")
	w.blob([]byte{0})
	w.checks([]Check{Passed("valid")})
	w.u32(0)
	w.byte(0)
	w.text("")
	limits := DefaultTraceLimits()
	limits.MaxItems = 1
	trace, err := PacketTraceWithLimits(w.data, limits)
	if err != nil {
		t.Fatal(err)
	}
	if len(trace.InitialChecks) != 1 {
		t.Fatal("lost initial check")
	}
}

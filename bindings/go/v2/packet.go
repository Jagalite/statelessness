package v2

import (
	"encoding/binary"
	"fmt"
	"unicode/utf8"
)

// The neutral packet preserves the binary audit configuration. The portable
// trace-json-v1 profile intentionally has no configuration field; exporting that
// JSON projection omits provenance, and importing supplies portable-json-v1.
const packetMagic = "STV2P001"

type packetReader struct {
	data          []byte
	offset, items int
	err           error
	limits        TraceLimits
}

func (r *packetReader) raw(n int) []byte {
	if r.err != nil {
		return nil
	}
	if n < 0 || n > len(r.data)-r.offset {
		r.err = fmt.Errorf("truncated neutral packet")
		return nil
	}
	b := r.data[r.offset : r.offset+n]
	r.offset += n
	return b
}
func (r *packetReader) byte() byte {
	b := r.raw(1)
	if len(b) == 0 {
		return 0
	}
	return b[0]
}
func (r *packetReader) u32() uint32 {
	b := r.raw(4)
	if len(b) != 4 {
		return 0
	}
	return binary.LittleEndian.Uint32(b)
}
func (r *packetReader) count(maximum int) int {
	n := uint64(r.u32())
	if r.err != nil {
		return 0
	}
	if n > uint64(maximum) || n > uint64(r.limits.MaxItems-r.items) {
		r.err = fmt.Errorf("neutral packet item quota exceeded")
		return 0
	}
	r.items += int(n)
	return int(n)
}
func (r *packetReader) blob() []byte {
	n := uint64(r.u32())
	if n > uint64(r.limits.MaxBlobBytes) {
		r.err = fmt.Errorf("neutral packet blob quota exceeded")
		return nil
	}
	return append([]byte{}, r.raw(int(n))...)
}
func (r *packetReader) text() string {
	n := uint64(r.u32())
	if n > uint64(len(r.data)-r.offset) {
		r.err = fmt.Errorf("truncated packet text")
		return ""
	}
	b := r.raw(int(n))
	if !utf8.Valid(b) {
		r.err = fmt.Errorf("invalid packet UTF-8")
	}
	return string(b)
}
func (r *packetReader) checks() []Check {
	n := r.count(r.limits.MaxItems)
	checks := make([]Check, 0, n)
	for i := 0; i < n && r.err == nil; i++ {
		id := r.text()
		tag := r.byte()
		details := r.text()
		if tag > 2 {
			r.err = fmt.Errorf("invalid packet check")
			break
		}
		c := Check{id, []string{"passed", "failed", "skipped"}[tag], details}
		if err := c.Validate(); err != nil {
			r.err = err
			break
		}
		checks = append(checks, c)
	}
	return checks
}

// PacketTrace returns the observation projection of a complete neutral packet.
func PacketTrace(packet []byte) (*Trace, error) {
	return PacketTraceWithLimits(packet, DefaultTraceLimits())
}
func PacketTraceWithLimits(packet []byte, limits TraceLimits) (*Trace, error) {
	if err := limits.validate(); err != nil {
		return nil, err
	}
	// Run configuration is transport provenance, omitted by the JSON profile.
	if len(packet) > max(DefaultTraceLimits().MaxJSONBytes, limits.MaxJSONBytes) {
		return nil, fmt.Errorf("neutral packet byte quota exceeded")
	}
	r := packetReader{data: packet, limits: limits}
	if string(r.raw(8)) != packetMagic {
		return nil, fmt.Errorf("invalid neutral packet version")
	}
	t := &Trace{}
	t.Metadata.Name = r.text()
	t.Metadata.Build = r.text()
	t.Metadata.ModelVersion = r.u32()
	t.Metadata.PropertiesVersion = r.u32()
	t.Metadata.CodecVersion = r.u32()
	_ = r.text()
	switch r.byte() {
	case 0:
	case 1:
		_ = r.raw(8)
	default:
		r.err = fmt.Errorf("invalid packet seed")
	}
	parameters := r.u32()
	if uint64(parameters) > uint64(len(r.data)-r.offset)/8 {
		r.err = fmt.Errorf("truncated packet parameters")
	}
	for n := parameters; n > 0 && r.err == nil; n-- {
		_ = r.text()
		_ = r.text()
	}
	t.InitialState = r.blob()
	t.InitialChecks = r.checks()
	n := r.count(limits.MaxSteps)
	t.Steps = make([]TraceStep, 0, n)
	for i := 0; i < n && r.err == nil; i++ {
		step := TraceStep{Input: r.blob()}
		tag := r.byte()
		reason := r.text()
		if tag > 2 {
			r.err = fmt.Errorf("invalid packet disposition")
			break
		}
		step.Disposition = Disposition{[]string{"accepted", "rejected", "ignored"}[tag], reason}
		if err := step.Disposition.Validate(); err != nil {
			r.err = err
			break
		}
		outputs := r.count(limits.MaxItems)
		step.Outputs = make([][]byte, 0, outputs)
		for j := 0; j < outputs && r.err == nil; j++ {
			step.Outputs = append(step.Outputs, r.blob())
		}
		step.PostState = r.blob()
		step.Checks = r.checks()
		t.Steps = append(t.Steps, step)
	}
	tag := r.byte()
	t.Error = r.text()
	if tag > 4 {
		r.err = fmt.Errorf("invalid packet termination")
	} else {
		t.Termination = []string{"completed", "property_failed", "step_limit", "interrupted", "model_error"}[tag]
	}
	if r.err != nil {
		return nil, r.err
	}
	if r.offset != len(packet) {
		return nil, fmt.Errorf("trailing neutral packet bytes")
	}
	// Reuse the strict profile's aggregate payload and envelope validation.
	if _, err := MarshalTraceWithLimits(t, limits); err != nil {
		return nil, err
	}
	return t, nil
}

type packetWriter struct {
	data    []byte
	err     error
	maximum int
}

func (w *packetWriter) raw(b []byte) {
	if w.err != nil {
		return
	}
	if len(b) > w.maximum-len(w.data) {
		w.err = fmt.Errorf("neutral packet byte quota exceeded")
		return
	}
	w.data = append(w.data, b...)
}
func (w *packetWriter) byte(b byte) { w.raw([]byte{b}) }
func (w *packetWriter) u32(n int) {
	if n < 0 || uint64(n) > uint64(^uint32(0)) {
		w.err = fmt.Errorf("neutral packet count overflow")
		return
	}
	var b [4]byte
	binary.LittleEndian.PutUint32(b[:], uint32(n))
	w.raw(b[:])
}
func (w *packetWriter) version(n uint32) {
	var b [4]byte
	binary.LittleEndian.PutUint32(b[:], n)
	w.raw(b[:])
}
func (w *packetWriter) blob(b []byte) { w.u32(len(b)); w.raw(b) }
func (w *packetWriter) text(s string) { w.blob([]byte(s)) }
func (w *packetWriter) checks(checks []Check) {
	w.u32(len(checks))
	for _, c := range checks {
		w.text(c.ID)
		switch c.Status {
		case "passed":
			w.byte(0)
		case "failed":
			w.byte(1)
		case "skipped":
			w.byte(2)
		}
		w.text(c.Details)
	}
}

// TracePacket imports only the portable observation profile; rich audit run
// configuration cannot be recovered from trace-json-v1.
func TracePacket(trace *Trace) ([]byte, error) {
	if _, err := MarshalTrace(trace); err != nil {
		return nil, err
	}
	w := packetWriter{maximum: DefaultTraceLimits().MaxJSONBytes}
	w.raw([]byte(packetMagic))
	m := trace.Metadata
	w.text(m.Name)
	w.text(m.Build)
	w.version(m.ModelVersion)
	w.version(m.PropertiesVersion)
	w.version(m.CodecVersion)
	w.text("portable-json-v1")
	w.byte(0)
	w.u32(0)
	w.blob(trace.InitialState)
	w.checks(trace.InitialChecks)
	w.u32(len(trace.Steps))
	for _, step := range trace.Steps {
		w.blob(step.Input)
		switch step.Disposition.Kind {
		case "accepted":
			w.byte(0)
		case "rejected":
			w.byte(1)
		case "ignored":
			w.byte(2)
		}
		w.text(step.Disposition.Reason)
		w.u32(len(step.Outputs))
		for _, output := range step.Outputs {
			w.blob(output)
		}
		w.blob(step.PostState)
		w.checks(step.Checks)
	}
	switch trace.Termination {
	case "completed":
		w.byte(0)
	case "property_failed":
		w.byte(1)
	case "step_limit":
		w.byte(2)
	case "interrupted":
		w.byte(3)
	case "model_error":
		w.byte(4)
	}
	w.text(trace.Error)
	return w.data, w.err
}

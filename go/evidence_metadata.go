package statelessness

import (
	"encoding/binary"
	"fmt"
	"math"
	"unicode/utf8"
)

func cloneProvenance(p Provenance) Provenance {
	p.Environment = append([][2]string{}, p.Environment...)
	if p.Manifest != nil {
		v := *p.Manifest
		p.Manifest = &v
	}
	if p.Source != nil {
		v := *p.Source
		p.Source = &v
	}
	return p
}
func cloneCausalContext(c CausalContext) CausalContext {
	if c.Machine != nil {
		v := *c.Machine
		c.Machine = &v
	}
	if c.Correlation != nil {
		v := *c.Correlation
		c.Correlation = &v
	}
	if c.Generation != nil {
		v := *c.Generation
		c.Generation = &v
	}
	if c.LogicalTime != nil {
		v := *c.LogicalTime
		c.LogicalTime = &v
	}
	c.Causes = append([]OutputOrigin{}, c.Causes...)
	return c
}
func cloneStepEvidence(s StepEvidence) StepEvidence {
	s.Causal = cloneCausalContext(s.Causal)
	return s
}

type evidenceMetadataEncoder struct {
	b   *EncodeBuffer
	err error
}

func (e *evidenceMetadataEncoder) raw(v []byte) {
	if e.err == nil {
		e.err = e.b.Append(v)
	}
}
func (e *evidenceMetadataEncoder) u8(v byte) { e.raw([]byte{v}) }
func (e *evidenceMetadataEncoder) u32(v uint64) {
	if e.err != nil {
		return
	}
	if v > math.MaxUint32 {
		e.err = &ModelError{"evidence metadata length overflow"}
		return
	}
	var b [4]byte
	binary.LittleEndian.PutUint32(b[:], uint32(v))
	e.raw(b[:])
}
func (e *evidenceMetadataEncoder) u64(v uint64) {
	var b [8]byte
	binary.LittleEndian.PutUint64(b[:], v)
	e.raw(b[:])
}
func (e *evidenceMetadataEncoder) str(v string) {
	if e.err != nil {
		return
	}
	if !utf8.ValidString(v) {
		e.err = &ModelError{"evidence metadata invalid UTF-8"}
		return
	}
	e.u32(uint64(len(v)))
	if e.err == nil {
		batch, err := e.b.batch()
		if err != nil {
			e.err = err
			return
		}
		if uint64(len(v)) > batch.maximum-uint64(len(*batch.bytes)) {
			e.err = encodeFailure(batch, &ModelError{"encoded blob exceeds byte limit"})
			return
		}
		e.raw([]byte(v))
	}
}
func (e *evidenceMetadataEncoder) optString(v *string) {
	if v == nil {
		e.u8(0)
	} else {
		e.u8(1)
		e.str(*v)
	}
}
func (e *evidenceMetadataEncoder) optUint(v *uint64) {
	if v == nil {
		e.u8(0)
	} else {
		e.u8(1)
		e.u64(*v)
	}
}
func (e *evidenceMetadataEncoder) provenance(p Provenance) {
	e.str(p.Run)
	e.u64(p.Epoch)
	e.optString(p.Manifest)
	e.optString(p.Source)
	e.u32(uint64(len(p.Environment)))
	if e.err != nil {
		return
	}
	for _, v := range p.Environment {
		e.str(v[0])
		e.str(v[1])
		if e.err != nil {
			return
		}
	}
}
func (e *evidenceMetadataEncoder) step(s StepEvidence) {
	e.u64(s.Sequence)
	e.u32(uint64(s.StateCheckCount))
	c := s.Causal
	e.optString(c.Machine)
	e.optUint(c.Generation)
	e.optString(c.Correlation)
	e.optUint(c.LogicalTime)
	e.u32(uint64(len(c.Causes)))
	if e.err != nil {
		return
	}
	for _, v := range c.Causes {
		e.u64(v.Sequence)
		e.u32(uint64(v.Output))
		if e.err != nil {
			return
		}
	}
}
func encodeEvidencePart(max uint64, f func(*evidenceMetadataEncoder)) ([]byte, error) {
	var data []byte
	err := WithEncodeBuffer(&data, max, func(b *EncodeBuffer) error { e := evidenceMetadataEncoder{b: b}; f(&e); return e.err })
	if err != nil {
		return nil, err
	}
	return data, nil
}
func encodeEvidenceProvenance(p Provenance, max uint64) ([]byte, error) {
	return encodeEvidencePart(max, func(e *evidenceMetadataEncoder) { e.provenance(p) })
}
func encodeEvidenceStep(s StepEvidence, max uint64) ([]byte, error) {
	return encodeEvidencePart(max, func(e *evidenceMetadataEncoder) { e.step(s) })
}
func encodeEvidenceMetadata(v *TraceV2, max uint64) ([]byte, error) {
	if v == nil {
		return nil, &ModelError{"nil evidence metadata"}
	}
	return encodeEvidencePart(max, func(e *evidenceMetadataEncoder) {
		e.u64(v.CheckpointAfter)
		e.provenance(v.Provenance)
		e.u32(uint64(len(v.Steps)))
		if e.err != nil {
			return
		}
		for _, s := range v.Steps {
			e.step(s)
			if e.err != nil {
				return
			}
		}
	})
}

// Rust64 logical schema weights preserve canonical structural admission across
// native implementations: two owned Strings=48, OutputOrigin=16, and
// StepEvidence=120. Option fields are inline parts of their containing values;
// they have no independent collection allocation charge. These weights bound
// logical decoder requests, not Go heap bytes, allocator overhead, or RSS.
const (
	evidenceEnvironmentWeight uint64 = 48
	evidenceOriginWeight      uint64 = 16
	evidenceStepWeight        uint64 = 120
)

// Independent budgets cover logical allocation requests, elements, nesting,
// and decode work. This target64 convention does not qualify non64 layouts.
type evidenceMetadataDecoder struct {
	data                       []byte
	allocation, elements, work uint64
	depth                      uint64
	err                        error
}

func evidenceTimes16(n uint64) uint64 {
	if n > math.MaxUint64/16 {
		return math.MaxUint64
	}
	return n * 16
}
func (d *evidenceMetadataDecoder) reject(s string) {
	if d.err == nil {
		d.err = &ModelError{s}
	}
}
func (d *evidenceMetadataDecoder) enter() bool {
	if d.err != nil {
		return false
	}
	if d.work == 0 {
		d.reject("decode work limit")
		return false
	}
	d.work--
	if d.depth >= 16 {
		d.reject("decode nesting limit")
		return false
	}
	d.depth++
	return true
}
func (d *evidenceMetadataDecoder) take(n uint64) []byte {
	if d.err != nil {
		return nil
	}
	if n > uint64(len(d.data)) {
		d.reject("truncated value")
		return nil
	}
	v := d.data[:int(n)]
	d.data = d.data[int(n):]
	return v
}
func (d *evidenceMetadataDecoder) u8() (v byte) {
	if !d.enter() {
		return
	}
	defer func() { d.depth-- }()
	b := d.take(1)
	if d.err == nil {
		v = b[0]
	}
	return
}
func (d *evidenceMetadataDecoder) u32() (v uint32) {
	if !d.enter() {
		return
	}
	defer func() { d.depth-- }()
	b := d.take(4)
	if d.err == nil {
		v = binary.LittleEndian.Uint32(b)
	}
	return
}
func (d *evidenceMetadataDecoder) u64() (v uint64) {
	if !d.enter() {
		return
	}
	defer func() { d.depth-- }()
	b := d.take(8)
	if d.err == nil {
		v = binary.LittleEndian.Uint64(b)
	}
	return
}
func (d *evidenceMetadataDecoder) allocate(n uint64) bool {
	if d.err != nil {
		return false
	}
	if n > d.allocation {
		d.reject("decode allocation limit")
		return false
	}
	d.allocation -= n
	return true
}
func (d *evidenceMetadataDecoder) collection(n, size, minWidth uint64) bool {
	if d.err != nil {
		return false
	}
	if n > d.elements {
		d.reject("decode element limit")
		return false
	}
	d.elements -= n
	if size != 0 && n > math.MaxUint64/size {
		d.reject("decode allocation overflow")
		return false
	}
	if !d.allocate(n * size) {
		return false
	}
	if n > d.work {
		d.reject("decode work limit")
		return false
	}
	if n > uint64(math.MaxInt) || (minWidth > 0 && n > uint64(len(d.data))/minWidth) {
		d.reject("truncated value")
		return false
	}
	return true
}
func (d *evidenceMetadataDecoder) str() (v string) {
	if !d.enter() {
		return
	}
	defer func() { d.depth-- }()
	n := uint64(d.u32())
	if !d.collection(n, 1, 1) {
		return
	}
	d.work -= n
	b := d.take(n)
	if d.err != nil {
		return
	}
	if !utf8.Valid(b) {
		d.reject("invalid UTF-8")
		return
	}
	return string(b)
}
func (d *evidenceMetadataDecoder) optString() (v *string) {
	if !d.enter() {
		return
	}
	defer func() { d.depth-- }()
	switch d.u8() {
	case 0:
		return
	case 1:
		s := d.str()
		if d.err == nil {
			v = &s
		}
	default:
		d.reject("invalid option tag")
	}
	return
}
func (d *evidenceMetadataDecoder) optUint() (v *uint64) {
	if !d.enter() {
		return
	}
	defer func() { d.depth-- }()
	switch d.u8() {
	case 0:
		return
	case 1:
		n := d.u64()
		if d.err == nil {
			v = &n
		}
	default:
		d.reject("invalid option tag")
	}
	return
}
func (d *evidenceMetadataDecoder) provenance() (p Provenance) {
	if !d.enter() {
		return
	}
	defer func() { d.depth-- }()
	p.Run = d.str()
	p.Epoch = d.u64()
	p.Manifest = d.optString()
	p.Source = d.optString()
	if !d.enter() {
		return
	}
	defer func() { d.depth-- }()
	n := uint64(d.u32())
	if !d.collection(n, evidenceEnvironmentWeight, 0) {
		return
	}
	// Grow only after decoding each element; a forged count must not reserve
	// memory before its first malformed or missing element is inspected.
	p.Environment = make([][2]string, 0)
	for i := uint64(0); i < n; i++ {
		if !d.enter() {
			return
		}
		pair := [2]string{d.str(), d.str()}
		d.depth--
		if d.err != nil {
			return
		}
		p.Environment = append(p.Environment, pair)
	}
	return
}
func (d *evidenceMetadataDecoder) causal() (c CausalContext) {
	if !d.enter() {
		return
	}
	defer func() { d.depth-- }()
	c.Machine = d.optString()
	c.Generation = d.optUint()
	c.Correlation = d.optString()
	c.LogicalTime = d.optUint()
	if !d.enter() {
		return
	}
	defer func() { d.depth-- }()
	n := uint64(d.u32())
	if !d.collection(n, evidenceOriginWeight, 0) {
		return
	}
	c.Causes = make([]OutputOrigin, 0)
	for i := uint64(0); i < n; i++ {
		if !d.enter() {
			return
		}
		origin := OutputOrigin{Sequence: d.u64(), Output: d.u32()}
		d.depth--
		if d.err != nil {
			return
		}
		c.Causes = append(c.Causes, origin)
	}
	return
}
func (d *evidenceMetadataDecoder) step() (s StepEvidence) {
	if !d.enter() {
		return
	}
	defer func() { d.depth-- }()
	s.Sequence = d.u64()
	s.StateCheckCount = d.u32()
	s.Causal = d.causal()
	return
}
func decodeEvidenceMetadata(data []byte, max uint64) (checkpoint uint64, provenance Provenance, steps []StepEvidence, err error) {
	if uint64(len(data)) > max {
		return 0, Provenance{}, nil, &ModelError{"decode byte limit"}
	}
	d := evidenceMetadataDecoder{data: data, allocation: evidenceTimes16(max), elements: max, work: evidenceTimes16(max)}
	checkpoint = d.u64()
	provenance = d.provenance()
	if d.enter() {
		n := uint64(d.u32())
		if d.collection(n, evidenceStepWeight, 0) {
			steps = make([]StepEvidence, 0)
			for i := uint64(0); i < n; i++ {
				step := d.step()
				if d.err != nil {
					break
				}
				steps = append(steps, step)
			}
		}
		d.depth--
	}
	if d.err == nil && len(d.data) != 0 {
		d.reject("trailing value bytes")
	}
	if d.err != nil {
		return 0, Provenance{}, nil, fmt.Errorf("evidence metadata: %w", d.err)
	}
	return checkpoint, provenance, steps, nil
}

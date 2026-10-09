package stateless

import (
	"encoding/binary"
	"fmt"
	"unicode/utf8"
)

const maxBytes = 64 << 20
const maxItems = 1000000

type packet struct {
	b   []byte
	err error
}

// reserve checks the aggregate limit before allocating or copying any bytes.
// Errors are sticky: a failed packet never grows again.
func (p *packet) reserve(n int) bool {
	if p.err != nil {
		return false
	}
	if n < 0 || n > maxBytes-len(p.b) {
		p.err = fmt.Errorf("oversized packet")
		return false
	}
	return true
}
func (p *packet) count(n int) {
	if p.err != nil {
		return
	}
	if n < 0 || n > maxItems {
		p.err = fmt.Errorf("too many items")
		return
	}
	p.u32(n)
}
func (p *packet) u32(n int) {
	if p.reserve(4) {
		p.b = binary.LittleEndian.AppendUint32(p.b, uint32(n))
	}
}
func (p *packet) tag(n byte) {
	if p.reserve(1) {
		p.b = append(p.b, n)
	}
}
func (p *packet) blob(b []byte) {
	if len(b) > maxBytes || !p.reserve(4+len(b)) {
		if p.err == nil {
			p.err = fmt.Errorf("oversized blob")
		}
		return
	}
	p.u32(len(b))
	p.b = append(p.b, b...)
}
func (p *packet) str(s string) {
	if len(s) > maxBytes || !p.reserve(4+len(s)) {
		if p.err == nil {
			p.err = fmt.Errorf("oversized string")
		}
		return
	}
	if !utf8.ValidString(s) {
		p.err = fmt.Errorf("invalid UTF-8")
		return
	}
	p.u32(len(s))
	p.b = append(p.b, s...)
}
func (p *packet) finish() ([]byte, error) {
	if p.err != nil {
		return nil, p.err
	}
	return p.b, nil
}
func batch(bs [][]byte) ([]byte, error) {
	p := packet{}
	p.count(len(bs))
	for _, b := range bs {
		if p.err != nil {
			break
		}
		p.blob(b)
	}
	return p.finish()
}
func transitionPacket(t Transition) ([]byte, error) {
	p := packet{}
	if t.Disposition > 2 || (t.Disposition == 0 && t.Reason != "") {
		return nil, fmt.Errorf("invalid disposition")
	}
	p.tag(byte(t.Disposition))
	p.str(t.Reason)
	p.blob(t.State)
	p.count(len(t.Effects))
	for _, e := range t.Effects {
		if p.err != nil {
			break
		}
		p.blob(e)
	}
	return p.finish()
}
func checksPacket(cs []Check) ([]byte, error) {
	p := packet{}
	p.count(len(cs))
	for _, c := range cs {
		if p.err != nil {
			break
		}
		if c.ID == "" || c.Status > 2 || (c.Status == 0 && c.Details != "") {
			return nil, fmt.Errorf("invalid check")
		}
		p.str(c.ID)
		p.tag(byte(c.Status))
		p.str(c.Details)
	}
	return p.finish()
}

type reader struct{ b []byte }

func (r *reader) take(n uint32) ([]byte, error) {
	if uint64(n) > uint64(len(r.b)) {
		return nil, fmt.Errorf("truncated packet")
	}
	b := r.b[:n]
	r.b = r.b[n:]
	return b, nil
}
func (r *reader) u32() (uint32, error) {
	b, e := r.take(4)
	if e != nil {
		return 0, e
	}
	return binary.LittleEndian.Uint32(b), nil
}
func (r *reader) blob() ([]byte, error) {
	n, e := r.u32()
	if e != nil {
		return nil, e
	}
	if n > maxBytes {
		return nil, fmt.Errorf("oversized blob")
	}
	return r.take(n)
}
func decodeCheckInput(b []byte) ([]byte, Transition, error) {
	r := reader{b}
	var t Transition
	i, e := r.blob()
	if e != nil {
		return nil, t, e
	}
	tag, e := r.take(1)
	if e != nil {
		return nil, t, e
	}
	t.Disposition = Disposition(tag[0])
	reason, e := r.blob()
	if e != nil {
		return nil, t, e
	}
	t.Reason = string(reason)
	if !utf8.Valid(reason) || t.Disposition > 2 || (t.Disposition == 0 && t.Reason != "") {
		return nil, t, fmt.Errorf("invalid disposition")
	}
	t.State, e = r.blob()
	if e != nil {
		return nil, t, e
	}
	n, e := r.u32()
	if e != nil {
		return nil, t, e
	}
	if n > maxItems {
		return nil, t, fmt.Errorf("too many effects")
	}
	for j := uint32(0); j < n; j++ {
		v, e := r.blob()
		if e != nil {
			return nil, t, e
		}
		t.Effects = append(t.Effects, v)
	}
	if len(r.b) != 0 {
		return nil, t, fmt.Errorf("trailing packet")
	}
	return i, t, nil
}

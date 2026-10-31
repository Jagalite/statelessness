package debug

import (
	"encoding/hex"
	s "github.com/Jagalite/statelessness/go"
	"math"
	"strconv"
)

type protocolCandidate[I any] struct {
	id       uint64
	revision uint64
	bytes    []byte
	cost     int
}
type traceWindow struct {
	offset, position uint64
	limit            int
	bytes            []byte
}

func (w *traceWindow) Write(data []byte) (int, error) {
	if uint64(len(data)) > math.MaxUint64-w.position {
		return 0, ProtocolError("limit")
	}
	end := w.position + uint64(len(data))
	lo := max(w.offset, w.position)
	hi := min(w.offset+uint64(w.limit), end)
	if lo < hi {
		w.bytes = append(w.bytes, data[lo-w.position:hi-w.position]...)
	}
	w.position = end
	return len(data), nil
}
func (p *ProtocolSession[S, I, O]) concrete(r ProtocolRequest, out ProtocolResponse) ProtocolResponse {
	fail := func(code string) ProtocolResponse { out.Fields = nil; out.Error = code; return out }
	field := func(k, v string) { out.Fields = append(out.Fields, ProtocolField{k, v}) }
	flag := func(k string, v bool) { field(k, "bool:"+strconv.FormatBool(v)) }
	quantity := func(i int) uint64 { v, _ := parseNumber(r.Args[i]); return v }
	switch r.Command {
	case "inputs":
		offset, limit := quantity(0), quantity(1)
		if limit == 0 || limit > uint64(p.limits.MaxPage) || offset > math.MaxUint64-limit {
			return fail("limit")
		}
		if p.session.model.Inputs == nil {
			return fail("unsupported")
		}
		if limit > uint64(p.session.limits.MaxCandidates) || offset > uint64(p.session.limits.MaxCandidates)-limit {
			return fail("invalid_input")
		}
		page, err := p.session.InputsPage(r.Revision, offset, int(limit))
		if err != nil {
			return fail("callback")
		}
		flag("complete", page.Complete)
		next := "none"
		if page.NextOffset != nil {
			next = number(*page.NextOffset)
		}
		field("next_offset", next)
		pending := []protocolCandidate[I]{}
		cost := 0
		for i, token := range page.Candidates {
			if uint64(i)+1 > math.MaxUint64-p.nextCandidate {
				return fail("exhausted")
			}
			var bytes []byte
			err := func() error {
				p.session.busy = true
				defer func() { p.session.busy = false }()
				return s.WithEncodeBuffer(&bytes, uint64(p.limits.MaxInput), func(b *s.EncodeBuffer) error { return p.session.model.AppendInputEncoding(token.Input(), b) })
			}()
			if err != nil {
				return fail("callback")
			}
			cost += len(bytes) + 64
			if cost > p.limits.MaxCandidateBytes || len(pending) == p.limits.MaxCandidates {
				return fail("limit")
			}
			id := p.nextCandidate + uint64(i) + 1
			pending = append(pending, protocolCandidate[I]{id, r.Revision, append([]byte{}, bytes...), len(bytes) + 64})
			prefix := "candidates." + strconv.Itoa(i)
			field(prefix+".token", number(id))
			if p.authority.ReadExactValues {
				field(prefix+".encoded", "bytes:"+hex.EncodeToString(bytes))
			}
		}
		field("candidates.returned", number(uint64(len(pending))))
		if _, err := EncodeResponse(out, p.limits); err != nil {
			return fail("limit")
		}
		for len(p.candidates) > 0 && (len(p.candidates)+len(pending) > p.limits.MaxCandidates || p.candidateBytes+cost > p.limits.MaxCandidateBytes) {
			p.candidateBytes -= p.candidates[0].cost
			p.candidates = p.candidates[1:]
		}
		p.candidates = append(p.candidates, pending...)
		p.candidateBytes += cost
		p.nextCandidate += uint64(len(pending))
	case "select":
		id := quantity(0)
		var candidate *protocolCandidate[I]
		for i := range p.candidates {
			if p.candidates[i].id == id {
				candidate = &p.candidates[i]
				break
			}
		}
		if candidate == nil || candidate.revision != p.session.Revision() {
			return fail("stale_handle")
		}
		if p.decode == nil {
			return fail("unsupported")
		}
		input, err := func() (I, error) {
			p.session.busy = true
			defer func() { p.session.busy = false }()
			return p.decode(append([]byte{}, candidate.bytes...))
		}()
		if err != nil {
			return fail("invalid_input")
		}
		permitted, err := p.session.CandidatePermitted(input)
		if err != nil {
			return fail("callback")
		}
		if !permitted {
			return fail("invalid_input")
		}
		if p.session.Phase() == Ended {
			return fail("ended")
		}
		result, err := p.session.Step(r.Revision, input)
		if err != nil {
			return fail("callback")
		}
		flag("delivered", result.Delivered)
		field("sequence", number(result.Sequence))

	case "export_trace":
		offset, limit := quantity(1), quantity(2)
		if limit == 0 || limit > uint64((p.limits.MaxField-6)/2) || offset > math.MaxUint64-limit {
			return fail("limit")
		}
		recorder := p.session.recorder
		if recorder == nil {
			return fail("unsupported")
		}
		if offset > recorder.RetainedBytes() {
			return fail("invalid_input")
		}
		window := &traceWindow{offset: offset, limit: int(limit)}
		err := func() error {
			p.session.busy = true
			defer func() { p.session.busy = false }()
			_, err := recorder.WriteTo(window)
			return err
		}()
		if err != nil {
			return fail("callback")
		}
		next := offset + uint64(len(window.bytes))
		field("export.kind", "enum:exact_trace_sensitive")
		flag("export.redacted", false)
		field("export.snapshot_revision", number(p.session.Revision()))
		field("export.snapshot_sequence", number(p.session.Sequence()))
		field("export.total_bytes", number(window.position))
		field("export.offset", number(offset))
		field("export.returned_bytes", number(uint64(len(window.bytes))))
		nextField := "none"
		if next < window.position {
			nextField = number(next)
		}
		field("export.next_offset", nextField)
		flag("export.complete", next == window.position)
		field("export.retained_steps", number(uint64(recorder.RetainedSteps())))
		field("export.evicted_steps", number(recorder.EvictedSteps()))
		stop := "none"
		if p.session.stop != nil {
			stop = p.session.stop.Kind
		}
		field("export.capture_outcome", "enum:"+stop)
		field("export.bytes", "bytes:"+hex.EncodeToString(window.bytes))
	}
	return out
}

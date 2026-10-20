package statelessness

import (
	"bytes"
	"encoding/binary"
	"errors"
	"hash/crc32"
	"io"
	"strconv"
	"unicode/utf8"
)

type auditReader struct {
	reader   io.Reader
	limits   BinaryLimits
	consumed uint64
}

func (r *auditReader) exact(data []byte) error {
	if e := auditAdd(&r.consumed, uint64(len(data)), r.limits.MaxTotalBytes, "total bytes"); e != nil {
		return e
	}
	_, e := io.ReadFull(r.reader, data)
	if errors.Is(e, io.EOF) || errors.Is(e, io.ErrUnexpectedEOF) {
		return auditError("truncated", "")
	}
	if e != nil {
		return &AuditError{Kind: "io", Err: e}
	}
	return nil
}
func (r *auditReader) frame() (byte, []byte, error) {
	var header [5]byte
	if e := r.exact(header[:]); e != nil {
		return 0, nil, e
	}
	length := uint64(binary.LittleEndian.Uint32(header[1:]))
	if e := auditLimit(length, r.limits.MaxFrameBytes, "frame bytes"); e != nil {
		return 0, nil, e
	}
	if length+4 > r.limits.MaxTotalBytes-r.consumed {
		return 0, nil, auditError("limit_exceeded", "total bytes")
	}
	if length > uint64(int(^uint(0)>>1)) {
		return 0, nil, auditError("limit_exceeded", "frame bytes")
	}
	payload := make([]byte, int(length))
	if e := r.exact(payload); e != nil {
		return 0, nil, e
	}
	var checksum [4]byte
	if e := r.exact(checksum[:]); e != nil {
		return 0, nil, e
	}
	sum := crc32.Update(0, crc32.IEEETable, header[:])
	sum = crc32.Update(sum, crc32.IEEETable, payload)
	if binary.LittleEndian.Uint32(checksum[:]) != sum {
		return 0, nil, auditError("checksum_mismatch", "")
	}
	return header[0], payload, nil
}
func (r *auditReader) eof() error {
	var one [1]byte
	for attempt := 0; attempt < 100; attempt++ {
		n, e := r.reader.Read(one[:])
		if n != 0 {
			return auditError("invalid_data", "trailing data after footer")
		}
		if errors.Is(e, io.EOF) {
			return nil
		}
		if e != nil {
			return &AuditError{Kind: "io", Err: e}
		}
	}
	return &AuditError{Kind: "io", Err: io.ErrNoProgress}
}

type auditDecoder struct {
	data   []byte
	pos    int
	limits BinaryLimits
	items  *uint64
	err    error
}

func (d *auditDecoder) fail(kind, detail string) {
	if d.err == nil {
		d.err = auditError(kind, detail)
	}
}
func (d *auditDecoder) raw(n uint64) []byte {
	if d.err != nil {
		return nil
	}
	if n > uint64(len(d.data)-d.pos) {
		d.fail("invalid_data", "field exceeds frame")
		return nil
	}
	start := d.pos
	d.pos += int(n)
	return d.data[start:d.pos]
}
func (d *auditDecoder) u8() byte {
	b := d.raw(1)
	if len(b) == 0 {
		return 0
	}
	return b[0]
}
func (d *auditDecoder) u32() uint32 {
	b := d.raw(4)
	if len(b) != 4 {
		return 0
	}
	return binary.LittleEndian.Uint32(b)
}
func (d *auditDecoder) u64() uint64 {
	b := d.raw(8)
	if len(b) != 8 {
		return 0
	}
	return binary.LittleEndian.Uint64(b)
}
func (d *auditDecoder) sized(maximum uint64, name string) []byte {
	n := uint64(d.u32())
	if d.err != nil {
		return nil
	}
	if e := auditLimit(n, maximum, name); e != nil {
		d.err = e
		return nil
	}
	return d.raw(n)
}
func (d *auditDecoder) str() string {
	b := d.sized(d.limits.MaxStringBytes, "string bytes")
	if d.err != nil {
		return ""
	}
	if !utf8.Valid(b) {
		d.fail("invalid_data", "invalid UTF-8")
		return ""
	}
	return string(b)
}
func (d *auditDecoder) blob() []byte {
	b := d.sized(d.limits.MaxBlobBytes, "blob bytes")
	if d.err != nil {
		return nil
	}
	return append([]byte{}, b...)
}
func (d *auditDecoder) count(maximum uint64, name string, minWidth uint64) int {
	n := uint64(d.u32())
	if d.err != nil {
		return 0
	}
	if e := auditLimit(n, maximum, name); e != nil {
		d.err = e
		return 0
	}
	if e := auditAdd(d.items, n, d.limits.MaxItems, "aggregate items"); e != nil {
		d.err = e
		return 0
	}
	if n > uint64(len(d.data)-d.pos)/minWidth {
		d.fail("invalid_data", "collection count exceeds frame")
		return 0
	}
	return int(n)
}
func (d *auditDecoder) checks() []Check {
	n := d.count(d.limits.MaxChecksPerStep, "checks per step", 5)
	if d.err != nil {
		return nil
	}
	checks := make([]Check, 0, n)
	for k := 0; k < n && d.err == nil; k++ {
		id := d.str()
		status := d.u8()
		check := Check{ID: id}
		switch status {
		case 0:
			check.Status = "passed"
		case 1:
			check.Status = "failed"
			check.Details = d.str()
		case 2:
			check.Status = "skipped"
			check.Details = d.str()
		default:
			d.fail("invalid_data", "check status tag")
		}
		checks = append(checks, check)
	}
	return checks
}
func (d *auditDecoder) finish() error {
	if d.err != nil {
		return d.err
	}
	if d.pos != len(d.data) {
		return auditError("invalid_data", "trailing record fields")
	}
	return nil
}

// ReadAuditTrace reads exactly one artifact and requires EOF after its footer.
// Supply bounded input and host I/O deadlines when reading network streams.
// Structural decoding preserves unusual termination/check combinations; semantic
// replay is a separate operation and cannot establish binary-v1 check phases.
func ReadAuditTrace(reader io.Reader, limits BinaryLimits) (*AuditTrace, error) {
	if reader == nil {
		return nil, auditError("invalid_data", "nil reader")
	}
	r := auditReader{reader: reader, limits: limits}
	var header [12]byte
	if e := r.exact(header[:]); e != nil {
		return nil, e
	}
	if !bytes.Equal(header[:8], auditHeader[:8]) {
		return nil, auditError("invalid_data", "file magic")
	}
	version := binary.LittleEndian.Uint16(header[8:10])
	if version != 1 {
		return nil, auditError("unsupported_version", strconv.Itoa(int(version)))
	}
	if header[10] != 0 || header[11] != 0 {
		return nil, auditError("invalid_data", "unsupported header flags")
	}
	tag, payload, e := r.frame()
	if e != nil {
		return nil, e
	}
	if tag != 1 {
		return nil, auditError("invalid_data", "first record must be run metadata")
	}
	items := uint64(0)
	d := auditDecoder{data: payload, limits: limits, items: &items}
	trace := &AuditTrace{}
	trace.Metadata = Metadata{Name: d.str(), ModelVersion: d.u32(), PropertiesVersion: d.u32(), CodecVersion: d.u32(), Build: d.str()}
	trace.Config.Strategy = d.str()
	switch d.u8() {
	case 0:
	case 1:
		seed := d.u64()
		trace.Config.Seed = &seed
	default:
		d.fail("invalid_data", "seed tag")
	}
	n := d.count(limits.MaxParameters, "parameters", 8)
	if d.err != nil {
		return nil, d.err
	}
	trace.Config.Parameters = make([][2]string, 0, n)
	for k := 0; k < n && d.err == nil; k++ {
		trace.Config.Parameters = append(trace.Config.Parameters, [2]string{d.str(), d.str()})
	}
	trace.InitialState = d.blob()
	trace.InitialChecks = d.checks()
	if e := d.finish(); e != nil {
		return nil, e
	}
	trace.Steps = []TraceStep{}
	for {
		tag, payload, e = r.frame()
		if e != nil {
			return nil, e
		}
		d = auditDecoder{data: payload, limits: limits, items: &items}
		switch tag {
		case 2:
			if e := auditLimit(uint64(len(trace.Steps))+1, limits.MaxSteps, "steps"); e != nil {
				return nil, e
			}
			if e := auditAdd(&items, 1, limits.MaxItems, "aggregate items"); e != nil {
				return nil, e
			}
			index := d.u64()
			if d.err != nil {
				return nil, d.err
			}
			if index != uint64(len(trace.Steps)) {
				return nil, auditError("invalid_data", "transition index")
			}
			step := TraceStep{Input: d.blob()}
			switch d.u8() {
			case 0:
				step.Disposition = Accepted()
			case 1:
				step.Disposition = Rejected(d.str())
			case 2:
				step.Disposition = Ignored(d.str())
			default:
				d.fail("invalid_data", "disposition tag")
			}
			n := d.count(limits.MaxOutputsPerStep, "outputs per step", 4)
			if d.err != nil {
				return nil, d.err
			}
			step.Outputs = make([][]byte, 0, n)
			for k := 0; k < n && d.err == nil; k++ {
				step.Outputs = append(step.Outputs, d.blob())
			}
			step.PostState = d.blob()
			step.Checks = d.checks()
			if e := d.finish(); e != nil {
				return nil, e
			}
			trace.Steps = append(trace.Steps, step)
		case 3:
			count := d.u64()
			if d.err != nil {
				return nil, d.err
			}
			if count != uint64(len(trace.Steps)) {
				return nil, auditError("invalid_data", "footer transition count")
			}
			switch d.u8() {
			case 0:
				trace.Termination = "completed"
			case 1:
				trace.Termination = "property_failed"
			case 2:
				trace.Termination = "step_limit"
			case 3:
				trace.Termination = "interrupted"
			case 4:
				trace.Termination = "model_error"
				trace.Error = d.str()
			default:
				d.fail("invalid_data", "termination tag")
			}
			if e := d.finish(); e != nil {
				return nil, e
			}
			if e := r.eof(); e != nil {
				return nil, e
			}
			return trace, nil
		default:
			return nil, auditError("invalid_data", "unknown or misplaced record")
		}
	}
}
func ParseAuditTrace(data []byte, limits BinaryLimits) (*AuditTrace, error) {
	return ReadAuditTrace(bytes.NewReader(data), limits)
}

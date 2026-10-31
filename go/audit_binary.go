package statelessness

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"hash/crc32"
	"io"
	"math"
	"unicode/utf8"
)

// RunConfig preserves seed presence and ordered, duplicate parameter keys.
type RunConfig struct {
	Strategy   string
	Seed       *uint64
	Parameters [][2]string
}

// AuditTrace is the lossless Rust binary-v1 shape. STEP checks have unknown
// legacy phases; this format contains no state/transition boundary evidence.
type AuditTrace struct {
	Trace
	Config RunConfig
}

// BinaryLimits are independent of portable JSON trace limits. Every field is
// inclusive; increasing one never increases another. They bound wire bytes and
// retained items, not allocator overhead or application callback allocations.
type BinaryLimits struct {
	MaxTotalBytes, MaxFrameBytes, MaxBlobBytes, MaxStringBytes             uint64
	MaxSteps, MaxChecksPerStep, MaxOutputsPerStep, MaxParameters, MaxItems uint64
}

func DefaultBinaryLimits() BinaryLimits {
	return BinaryLimits{67108864, 8388608, 4194304, 65536, 100000, 4096, 4096, 1024, 250000}
}

type AuditError struct {
	Kind, Detail string
	Err          error
}

func (e *AuditError) Error() string {
	if e.Err != nil {
		return fmt.Sprintf("audit %s: %v", e.Kind, e.Err)
	}
	return fmt.Sprintf("audit %s: %s", e.Kind, e.Detail)
}
func (e *AuditError) Unwrap() error        { return e.Err }
func auditError(kind, detail string) error { return &AuditError{Kind: kind, Detail: detail} }
func auditLimit(value, maximum uint64, name string) error {
	if value > maximum {
		return auditError("limit_exceeded", name)
	}
	return nil
}
func auditAdd(total *uint64, n, maximum uint64, name string) error {
	if n > math.MaxUint64-*total {
		return auditError("limit_exceeded", name)
	}
	*total += n
	return auditLimit(*total, maximum, name)
}

var auditHeader = []byte{'S', 'T', 'L', 'E', 'S', 'S', 0x1a, 0x0a, 1, 0, 0, 0}

func cloneRunConfig(config RunConfig) RunConfig {
	result := config
	result.Parameters = append([][2]string{}, config.Parameters...)
	if config.Seed != nil {
		seed := *config.Seed
		result.Seed = &seed
	}
	return result
}
func clonePortableTrace(trace Trace) Trace {
	result := trace
	result.InitialState = append([]byte{}, trace.InitialState...)
	result.InitialChecks = copyChecks(trace.InitialChecks)
	result.Steps = make([]TraceStep, len(trace.Steps))
	for k, step := range trace.Steps {
		result.Steps[k] = step
		result.Steps[k].Input = append([]byte{}, step.Input...)
		result.Steps[k].PostState = append([]byte{}, step.PostState...)
		result.Steps[k].Checks = copyChecks(step.Checks)
		result.Steps[k].Outputs = make([][]byte, len(step.Outputs))
		for j, output := range step.Outputs {
			result.Steps[k].Outputs[j] = append([]byte{}, output...)
		}
	}
	return result
}

// AuditFromPortable requires explicit run configuration. It never invents a
// strategy, seed, or parameters absent from the narrower portable trace.
func AuditFromPortable(trace Trace, config RunConfig) (*AuditTrace, error) {
	if trace.Termination != "model_error" && trace.Error != "" {
		return nil, auditError("invalid_data", "error with non-model-error termination")
	}
	return &AuditTrace{Trace: clonePortableTrace(trace), Config: cloneRunConfig(config)}, nil
}

// Portable returns both the narrower trace and its omitted run configuration,
// with independent ownership. No binary evidence is silently discarded.
func (trace AuditTrace) Portable() (Trace, RunConfig, error) {
	if trace.Termination != "model_error" && trace.Error != "" {
		return Trace{}, RunConfig{}, auditError("invalid_data", "error with non-model-error termination")
	}
	return clonePortableTrace(trace.Trace), cloneRunConfig(trace.Config), nil
}

type auditEncoder struct {
	limits  BinaryLimits
	items   *uint64
	size    uint64
	data    []byte
	writing bool
	err     error
}

func (e *auditEncoder) raw(data []byte) {
	if e.err != nil {
		return
	}
	if e.err = auditAdd(&e.size, uint64(len(data)), min(e.limits.MaxFrameBytes, uint64(math.MaxUint32)), "frame bytes"); e.err != nil {
		return
	}
	if e.writing {
		e.data = append(e.data, data...)
	}
}
func (e *auditEncoder) u8(value byte) { e.raw([]byte{value}) }
func (e *auditEncoder) u32(value uint64) {
	if value > math.MaxUint32 {
		e.err = auditError("limit_exceeded", "wire u32")
		return
	}
	var b [4]byte
	binary.LittleEndian.PutUint32(b[:], uint32(value))
	e.raw(b[:])
}
func (e *auditEncoder) u64(value uint64) {
	var b [8]byte
	binary.LittleEndian.PutUint64(b[:], value)
	e.raw(b[:])
}
func (e *auditEncoder) sized(data []byte, maximum uint64, name string) {
	if e.err != nil {
		return
	}
	if e.err = auditLimit(uint64(len(data)), min(maximum, uint64(math.MaxUint32)), name); e.err != nil {
		return
	}
	e.u32(uint64(len(data)))
	e.raw(data)
}
func (e *auditEncoder) str(value string) {
	if e.err != nil {
		return
	}
	if e.err = auditLimit(uint64(len(value)), min(e.limits.MaxStringBytes, uint64(math.MaxUint32)), "string bytes"); e.err != nil {
		return
	}
	if !utf8.ValidString(value) {
		e.err = auditError("invalid_data", "invalid UTF-8")
		return
	}
	e.u32(uint64(len(value)))
	if e.err != nil {
		return
	}
	if e.err = auditAdd(&e.size, uint64(len(value)), min(e.limits.MaxFrameBytes, uint64(math.MaxUint32)), "frame bytes"); e.err != nil {
		return
	}
	if e.writing {
		e.data = append(e.data, value...)
	}
}
func (e *auditEncoder) blob(value []byte) { e.sized(value, e.limits.MaxBlobBytes, "blob bytes") }
func (e *auditEncoder) count(n int, maximum uint64, name string) {
	if e.err != nil {
		return
	}
	if e.err = auditLimit(uint64(n), min(maximum, uint64(math.MaxUint32)), name); e.err != nil {
		return
	}
	if e.err = auditAdd(e.items, uint64(n), e.limits.MaxItems, "aggregate items"); e.err != nil {
		return
	}
	e.u32(uint64(n))
}
func (e *auditEncoder) checks(checks []Check) {
	e.count(len(checks), e.limits.MaxChecksPerStep, "checks per step")
	for _, check := range checks {
		if e.err != nil {
			return
		}
		e.str(check.ID)
		switch check.Status {
		case "passed":
			if check.Details != "" {
				e.err = auditError("invalid_data", "passed check details")
				return
			}
			e.u8(0)
		case "failed":
			e.u8(1)
			e.str(check.Details)
		case "skipped":
			e.u8(2)
			e.str(check.Details)
		default:
			e.err = auditError("invalid_data", "check status tag")
		}
	}
}
func auditEncodeRun(e *auditEncoder, t *AuditTrace) {
	m := t.Metadata
	e.str(m.Name)
	e.u32(uint64(m.ModelVersion))
	e.u32(uint64(m.PropertiesVersion))
	e.u32(uint64(m.CodecVersion))
	e.str(m.Build)
	e.str(t.Config.Strategy)
	if t.Config.Seed == nil {
		e.u8(0)
	} else {
		e.u8(1)
		e.u64(*t.Config.Seed)
	}
	e.count(len(t.Config.Parameters), e.limits.MaxParameters, "parameters")
	if e.err != nil {
		return
	}
	for _, pair := range t.Config.Parameters {
		e.str(pair[0])
		e.str(pair[1])
	}
	e.blob(t.InitialState)
	e.checks(t.InitialChecks)
}
func auditEncodeStep(e *auditEncoder, index int, s TraceStep) {
	e.u64(uint64(index))
	e.blob(s.Input)
	switch s.Disposition.Kind {
	case "accepted":
		if s.Disposition.Reason != "" {
			e.err = auditError("invalid_data", "accepted disposition reason")
			return
		}
		e.u8(0)
	case "rejected":
		e.u8(1)
		e.str(s.Disposition.Reason)
	case "ignored":
		e.u8(2)
		e.str(s.Disposition.Reason)
	default:
		e.err = auditError("invalid_data", "disposition tag")
		return
	}
	e.count(len(s.Outputs), e.limits.MaxOutputsPerStep, "outputs per step")
	if e.err != nil {
		return
	}
	for _, output := range s.Outputs {
		e.blob(output)
	}
	e.blob(s.PostState)
	e.checks(s.Checks)
}
func auditEncodeEnd(e *auditEncoder, t *AuditTrace) {
	e.u64(uint64(len(t.Steps)))
	if t.Termination != "model_error" && t.Error != "" {
		e.err = auditError("invalid_data", "error with non-model-error termination")
		return
	}
	switch t.Termination {
	case "completed":
		e.u8(0)
	case "property_failed":
		e.u8(1)
	case "step_limit":
		e.u8(2)
	case "interrupted":
		e.u8(3)
	case "model_error":
		e.u8(4)
		e.str(t.Error)
	default:
		e.err = auditError("invalid_data", "termination tag")
	}
}

// WriteBinaryWithLimits validates every frame and aggregate quota before its
// first write. I/O errors can leave a prefix; it never flushes/closes/fsyncs.
func (t *AuditTrace) WriteBinaryWithLimits(writer io.Writer, limits BinaryLimits) error {
	if t == nil {
		return auditError("invalid_data", "nil audit trace")
	}
	if writer == nil {
		return auditError("invalid_data", "nil writer")
	}
	if e := auditLimit(uint64(len(t.Steps)), limits.MaxSteps, "steps"); e != nil {
		return e
	}
	items := uint64(len(t.Steps))
	if e := auditLimit(items, limits.MaxItems, "aggregate items"); e != nil {
		return e
	}
	total := uint64(12)
	var maximum uint64
	preflight := func(encode func(*auditEncoder)) error {
		e := auditEncoder{limits: limits, items: &items}
		encode(&e)
		if e.err != nil {
			return e.err
		}
		maximum = max(maximum, e.size)
		return auditAdd(&total, e.size+9, limits.MaxTotalBytes, "total bytes")
	}
	if e := preflight(func(e *auditEncoder) { auditEncodeRun(e, t) }); e != nil {
		return e
	}
	for index, step := range t.Steps {
		if e := preflight(func(e *auditEncoder) { auditEncodeStep(e, index, step) }); e != nil {
			return e
		}
	}
	if e := preflight(func(e *auditEncoder) { auditEncodeEnd(e, t) }); e != nil {
		return e
	}
	if maximum > uint64(int(^uint(0)>>1)) {
		return auditError("limit_exceeded", "frame bytes")
	}
	// One reusable payload buffer; the sizing pass did not copy blob contents.
	buffer := make([]byte, 0, int(maximum))
	if e := auditWriteAll(writer, auditHeader); e != nil {
		return e
	}
	items = uint64(len(t.Steps))
	frame := func(tag byte, encode func(*auditEncoder)) error {
		e := auditEncoder{limits: limits, items: &items, data: buffer[:0], writing: true}
		encode(&e)
		if e.err != nil {
			return e.err
		}
		buffer = e.data
		var header [5]byte
		header[0] = tag
		binary.LittleEndian.PutUint32(header[1:], uint32(len(buffer)))
		sum := crc32.Update(0, crc32.IEEETable, header[:])
		sum = crc32.Update(sum, crc32.IEEETable, buffer)
		var checksum [4]byte
		binary.LittleEndian.PutUint32(checksum[:], sum)
		if e := auditWriteAll(writer, header[:]); e != nil {
			return e
		}
		if e := auditWriteAll(writer, buffer); e != nil {
			return e
		}
		return auditWriteAll(writer, checksum[:])
	}
	if e := frame(1, func(e *auditEncoder) { auditEncodeRun(e, t) }); e != nil {
		return e
	}
	for index, step := range t.Steps {
		if e := frame(2, func(e *auditEncoder) { auditEncodeStep(e, index, step) }); e != nil {
			return e
		}
	}
	return frame(3, func(e *auditEncoder) { auditEncodeEnd(e, t) })
}
func (t *AuditTrace) WriteBinary(writer io.Writer) error {
	return t.WriteBinaryWithLimits(writer, DefaultBinaryLimits())
}
func MarshalAuditTrace(t *AuditTrace, limits BinaryLimits) ([]byte, error) {
	var buffer bytes.Buffer
	if e := t.WriteBinaryWithLimits(&buffer, limits); e != nil {
		return nil, e
	}
	return buffer.Bytes(), nil
}
func auditWriteAll(writer io.Writer, data []byte) error {
	for len(data) > 0 {
		n, e := writer.Write(data)
		if n < 0 || n > len(data) {
			return &AuditError{Kind: "io", Err: io.ErrShortWrite}
		}
		data = data[n:]
		if e != nil {
			return &AuditError{Kind: "io", Err: e}
		}
		if n == 0 {
			return &AuditError{Kind: "io", Err: io.ErrShortWrite}
		}
	}
	return nil
}

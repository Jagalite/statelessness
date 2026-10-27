package statelessness

import (
	"math"
	"strings"
	"unicode/utf8"
)

// RecordAudit records directly into the lossless binary-v1 shape. Limits bound
// accepted encoded bytes and retained wire items, not application allocations.
// Errors after the initial checkpoint return a coherent model_error prefix.
func RecordAudit[S, I, O any](m Model[S, I, O], inputs Iterator[I], config RunConfig, maxSteps int, limits BinaryLimits) (*AuditTrace, error) {
	var stampError error
	config, stampError = stampAuditConfig(config)
	if stampError != nil {
		return nil, stampError
	}
	if maxSteps < 0 {
		return nil, &ConfigError{"negative step limit"}
	}
	if err := m.validateCodec(); err != nil {
		return nil, err
	}
	if m.InitialState == nil || m.Step == nil || inputs == nil {
		return nil, &ModelError{"record requires initial, step and input callbacks"}
	}
	state, err := call("initial state", m.InitialState)
	if err != nil {
		return nil, err
	}
	state, err = copied(m.CloneState, state)
	if err != nil {
		return nil, err
	}
	var checks []Check
	if err = stateChecksInto(m, state, &checks); err != nil {
		return nil, err
	}
	initialChecks := copyChecks(checks)
	blobLimit := min(limits.MaxBlobBytes, limits.MaxFrameBytes, limits.MaxTotalBytes, uint64(math.MaxUint32))
	encodeState := func(value S, target *[]byte, maximum uint64, stage string) error {
		_, e := call(stage, func() (struct{}, error) {
			return struct{}{}, encodeStateInto(m, value, target, maximum)
		})
		return e
	}
	var initial []byte
	if err = encodeState(state, &initial, blobLimit, "encode initial state"); err != nil {
		return nil, err
	}
	metadata, err := call("metadata", func() (Metadata, error) {
		v, e := m.Codec.Metadata()
		if e == nil {
			e = v.Validate()
		}
		return v, e
	})
	if err != nil {
		return nil, err
	}
	trace := &AuditTrace{Trace: Trace{Metadata: metadata, InitialState: initial, InitialChecks: initialChecks, Steps: []TraceStep{}, Termination: "completed"}, Config: config}
	items := uint64(0)
	run := auditEncoder{limits: limits, items: &items}
	auditEncodeRun(&run, trace)
	if run.err != nil {
		return nil, run.err
	}
	terminalEnd := uint64(18) // nine-byte frame envelope plus u64 count and tag.
	reserve := terminalEnd
	if hasFailure(initialChecks) {
		if limits.MaxFrameBytes < 9 {
			return nil, auditError("limit_exceeded", "frame bytes")
		}
	} else {
		reserve += 4 + auditRecordReasonLimit(limits)
		if limits.MaxFrameBytes < 13 {
			return nil, auditError("limit_exceeded", "frame bytes")
		}
	}
	total := uint64(12)
	if err = auditAdd(&total, run.size+9, limits.MaxTotalBytes, "total bytes"); err != nil {
		return nil, err
	}
	budget := total
	if err = auditAdd(&budget, reserve, limits.MaxTotalBytes, "total bytes"); err != nil {
		return nil, err
	}
	if hasFailure(initialChecks) {
		trace.Termination = "property_failed"
		return trace, nil
	}
	terminalError := func(e error) (*AuditTrace, error) {
		trace.Termination = "model_error"
		trace.Error = auditRecordReason(e.Error(), limits)
		return trace, nil
	}
	for {
		type item struct {
			value I
			ok    bool
		}
		x, e := call("record input", func() (item, error) { v, ok, e := inputs(); return item{v, ok}, e })
		if e != nil {
			return terminalError(e)
		}
		if !x.ok {
			return trace, nil
		}
		if len(trace.Steps) >= maxSteps || uint64(len(trace.Steps)) >= limits.MaxSteps {
			trace.Termination = "step_limit"
			return trace, nil
		}
		frameBudget := uint64(0)
		if limits.MaxTotalBytes >= total && limits.MaxTotalBytes-total >= terminalEnd {
			frameBudget = limits.MaxTotalBytes - total - terminalEnd
		}
		frameBudget = min(frameBudget, min(limits.MaxFrameBytes, uint64(math.MaxUint32))+9)
		var input []byte
		_, e = call("encode input", func() (struct{}, error) {
			return struct{}{}, encodeInputInto(m, x.value, &input, min(blobLimit, frameBudget))
		})
		if e != nil {
			return terminalError(e)
		}
		transition, e := perform(m, state, x.value)
		if e != nil {
			return terminalError(e)
		}
		if e = CheckObservedInto(m, state, x.value, transition, uint64(len(trace.Steps)+1), FullChecks(), &checks); e != nil {
			return terminalError(e)
		}
		if e = auditLimit(uint64(len(transition.Outputs)), min(limits.MaxOutputsPerStep, uint64(math.MaxUint32)), "outputs per step"); e != nil {
			return terminalError(e)
		}
		nextItems := items
		for _, n := range []uint64{1, uint64(len(checks)), uint64(len(transition.Outputs))} {
			if e = auditAdd(&nextItems, n, limits.MaxItems, "aggregate items"); e != nil {
				return terminalError(e)
			}
		}
		step := TraceStep{Input: input, Disposition: transition.Disposition, Outputs: [][]byte{}, Checks: copyChecks(checks)}
		scratchItems := uint64(1)
		base := auditEncoder{limits: limits, items: &scratchItems}
		auditEncodeStep(&base, len(trace.Steps), step)
		if base.err != nil {
			return terminalError(base.err)
		}
		if base.size+9 > frameBudget {
			return terminalError(auditError("limit_exceeded", "remaining frame bytes"))
		}
		remaining := frameBudget - base.size - 9
		for _, output := range transition.Outputs {
			if remaining < 4 {
				return terminalError(auditError("limit_exceeded", "output frame bytes"))
			}
			remaining -= 4
			var encoded []byte
			_, e = call("encode output", func() (struct{}, error) {
				return struct{}{}, encodeOutputInto(m, output, &encoded, min(blobLimit, remaining))
			})
			if e != nil {
				return terminalError(e)
			}
			remaining -= uint64(len(encoded))
			step.Outputs = append(step.Outputs, encoded)
		}
		if e = encodeState(transition.State, &step.PostState, min(blobLimit, remaining), "encode state"); e != nil {
			return terminalError(e)
		}
		scratchItems = 1
		size := auditEncoder{limits: limits, items: &scratchItems}
		auditEncodeStep(&size, len(trace.Steps), step)
		if size.err != nil {
			return terminalError(size.err)
		}
		nextTotal := total
		if e = auditAdd(&nextTotal, size.size+9, limits.MaxTotalBytes, "total bytes"); e != nil {
			return terminalError(e)
		}
		footer := reserve
		if hasFailure(step.Checks) {
			footer = terminalEnd
		}
		budget = nextTotal
		if e = auditAdd(&budget, footer, limits.MaxTotalBytes, "total bytes"); e != nil {
			return terminalError(e)
		}
		trace.Steps = append(trace.Steps, step)
		total = nextTotal
		items = nextItems
		state = transition.State
		if hasFailure(step.Checks) {
			trace.Termination = "property_failed"
			return trace, nil
		}
	}
}

func auditRecordReasonLimit(limits BinaryLimits) uint64 {
	frame := uint64(0)
	if limits.MaxFrameBytes > 13 {
		frame = limits.MaxFrameBytes - 13
	}
	return min(uint64(256), limits.MaxStringBytes, frame)
}
func auditRecordReason(message string, limits BinaryLimits) string {
	maximum := int(auditRecordReasonLimit(limits))
	// Go error strings may contain arbitrary bytes; wire strings must be UTF-8.
	message = strings.ToValidUTF8(message, "�")
	end := min(len(message), maximum)
	truncated := end < len(message)
	if truncated && maximum >= 3 {
		end = maximum - 3
	}
	for end > 0 && !utf8.ValidString(message[:end]) {
		end--
	}
	result := message[:end]
	if truncated && maximum >= 3 {
		result += "..."
	}
	return result
}

// NativeEngineBuild identifies this independent implementation; it is not a
// checkout hash or a claim of source-bound build provenance.
const NativeEngineBuild = "native-go-v1"

func stampAuditConfig(config RunConfig) (RunConfig, error) {
	config = cloneRunConfig(config)
	for _, pair := range config.Parameters {
		if strings.HasPrefix(pair[0], "stateless.engine.") {
			return RunConfig{}, &ModelError{"stateless.engine. parameters are reserved"}
		}
	}
	config.Parameters = append(config.Parameters, [2]string{"stateless.engine.version", Version}, [2]string{"stateless.engine.build", NativeEngineBuild})
	return config, nil
}

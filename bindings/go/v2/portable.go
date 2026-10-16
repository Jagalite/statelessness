//go:build cgo

package v2

// ExportJSON projects a Rust audit artifact into portable trace-json-v1. Binary
// run configuration is omitted by that profile; model/check observations remain.
func (s *Session[S, I, O]) ExportJSON(artifact []byte) ([]byte, error) {
	packet, err := s.NeutralPacket(artifact)
	if err != nil {
		return nil, err
	}
	trace, err := PacketTrace(packet)
	if err != nil {
		return nil, err
	}
	return MarshalTrace(trace)
}

// ImportJSON parses the strict portable profile before invoking any model code.
// The shared Rust codec validates and builds the resulting binary audit artifact.
func (s *Session[S, I, O]) ImportJSON(data []byte) ([]byte, error) {
	trace, err := ParseTrace(data)
	if err != nil {
		return nil, err
	}
	packet, err := TracePacket(trace)
	if err != nil {
		return nil, err
	}
	return s.BinaryTrace(packet)
}
func (s *Session[S, I, O]) ReplayJSON(data []byte, allowBuildMismatch bool) (*Result[I], error) {
	artifact, err := s.ImportJSON(data)
	if err != nil {
		return nil, err
	}
	return s.Replay(artifact, allowBuildMismatch)
}

// RecordPortable records the portable observation profile with its independent
// blob, aggregate item, payload, step and JSON serialization limits.
func (s *Session[S, I, O]) RecordPortable(inputs []I, maxSteps uint64, limits TraceLimits) (*Trace, error) {
	return s.RecordPortableIterator(Values(inputs), maxSteps, limits)
}
func (s *Session[S, I, O]) RecordPortableIterator(inputs Iterator[I], maxSteps uint64, limits TraceLimits) (*Trace, error) {
	if err := limits.validate(); err != nil {
		return nil, err
	}
	if maxSteps > uint64(limits.MaxSteps) {
		maxSteps = uint64(limits.MaxSteps)
	}
	result, recordErr := s.RecordIteratorWithLimits(inputs, maxSteps, uint64(limits.MaxBlobBytes), uint64(limits.MaxItems), uint64(limits.MaxPayloadBytes))
	if result == nil {
		return nil, recordErr
	}
	defer result.Close()
	packet, err := s.NeutralPacket(result.Artifact)
	if err != nil {
		return nil, err
	}
	trace, err := PacketTraceWithLimits(packet, limits)
	if err != nil {
		return nil, err
	}
	// Late model errors are represented by the coherent trace termination.
	data, err := MarshalTraceWithLimits(trace, limits)
	if err != nil {
		return nil, err
	}
	if len(data) > limits.MaxJSONBytes {
		return nil, &FormatError{Message: "portable JSON byte limit"}
	}
	return trace, nil
}

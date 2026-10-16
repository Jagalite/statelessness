package v2

type TraceStep struct {
	Input       []byte
	Disposition Disposition
	Outputs     [][]byte
	PostState   []byte
	Checks      []Check
}
type Trace struct {
	Metadata      Metadata
	InitialState  []byte
	InitialChecks []Check
	Steps         []TraceStep
	Termination   string
	Error         string
}

// TraceLimits count retained bytes/items, not arbitrary allocations by callbacks.
type TraceLimits struct {
	MaxSteps        int
	MaxBlobBytes    int
	MaxItems        int
	MaxPayloadBytes int
	MaxJSONBytes    int
}

func DefaultTraceLimits() TraceLimits {
	return TraceLimits{100_000, 4 * 1024 * 1024, 250_000, 32 * 1024 * 1024, 64 * 1024 * 1024}
}
func (l TraceLimits) validate() error {
	if l.MaxSteps < 0 || l.MaxBlobBytes < 0 || l.MaxItems < 0 || l.MaxPayloadBytes < 0 || l.MaxJSONBytes < 0 {
		return &FormatError{"negative trace limit"}
	}
	// Hex expansion and aggregate accounting must not overflow platform Int.
	maximum := int(^uint(0)>>1) / 2
	if l.MaxSteps > maximum || l.MaxBlobBytes > maximum || l.MaxItems > maximum || l.MaxPayloadBytes > maximum || l.MaxJSONBytes > maximum {
		return &FormatError{"trace limit exceeds safe platform range"}
	}
	return nil
}

func validTermination(t string) bool {
	return t == "completed" || t == "property_failed" || t == "step_limit" || t == "interrupted" || t == "model_error"
}

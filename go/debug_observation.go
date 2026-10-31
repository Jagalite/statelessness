package statelessness

// DebugObservation is the diagnostic initial/turn sum. It grants no checked
// authority. Payloads are application-owned values/references; no effects run.
// Use a type switch to inspect the exported variant fields.
type DebugObservation[S, I, O any] interface {
	debugObservation(S, I, O)
}

// DebugInitial describes supplied state and checks, without a sequence number.
type DebugInitial[S, I, O any] struct {
	State  S
	Checks []Check
}

func (DebugInitial[S, I, O]) debugObservation(S, I, O) {}

// DebugTurn contains the complete diagnostic turn including its check boundary.
type DebugTurn[S, I, O any] struct {
	Observation TurnObservation[S, I, O]
}

func (DebugTurn[S, I, O]) debugObservation(S, I, O) {}

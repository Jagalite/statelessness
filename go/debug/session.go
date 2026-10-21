package debug

import (
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"math"
	"sync/atomic"
	"unicode/utf8"
)

type Phase string

const (
	Ready  Phase = "ready"
	Paused Phase = "paused"
	Ended  Phase = "ended"
)

type StopReason struct {
	Kind, Detail string
	Breakpoint   uint64
}
type SessionLimits struct {
	MaxTurns                                   uint64
	MaxContinue, MaxBreakpoints, MaxCandidates int
}

func DefaultSessionLimits() SessionLimits { return SessionLimits{100000, 1000, 64, 1000} }

type StepResult struct {
	Sequence  uint64
	Delivered bool
	Stop      *StopReason
}
type Observation[S, I, O any] struct {
	InitialChecks      []s.Check
	State              S
	Sequence, Revision uint64
	Phase              Phase
	ChecksComplete     bool
	Last               *s.TurnObservation[S, I, O]
}
type preBreakpoint[S, I any] struct {
	id        uint64
	predicate func(S, I) bool
}
type postBreakpoint[S, I, O any] struct {
	id        uint64
	predicate func(s.TurnObservation[S, I, O]) bool
}

var epochs atomic.Uint64

// Session is a cooperative single-owner simulator. The host must serialize calls;
// reentrant calls are rejected. It neither executes effects nor starts goroutines.
type Session[S, I, O any] struct {
	id                                 string
	epoch                              uint64
	model                              s.Model[S, I, O]
	state                              S
	policy                             InputPolicy[S, I]
	equal                              func(I, I) (bool, error)
	limits                             SessionLimits
	sequence, revision, nextBreakpoint uint64
	phase                              Phase
	stop                               *StopReason
	initial                            []s.Check
	last                               *s.TurnObservation[S, I, O]
	complete                           bool
	pre                                []preBreakpoint[S, I]
	post                               []postBreakpoint[S, I, O]
	pending                            *I
	pendingRevision                    uint64
	busy                               bool
	issues                             []string
	recorder                           *s.Recorder
	initialChecked                     *s.CheckedInitial[S, I, O]
}

func NewSession[S, I, O any](id string, m s.Model[S, I, O], policy InputPolicy[S, I], equal func(I, I) (bool, error), limits SessionLimits) (*Session[S, I, O], error) {
	if m.InitialState == nil {
		return nil, fmt.Errorf("missing initial state")
	}
	state, e := m.InitialState()
	if e != nil {
		return nil, e
	}
	return SessionFromState(id, m, state, policy, equal, limits)
}
func SessionFromState[S, I, O any](id string, m s.Model[S, I, O], state S, policy InputPolicy[S, I], equal func(I, I) (bool, error), limits SessionLimits) (*Session[S, I, O], error) {
	if id == "" || !utf8.ValidString(id) || !utf8.ValidString(policy.Label) || limits.MaxTurns == 0 || limits.MaxContinue <= 0 || limits.MaxCandidates <= 0 || limits.MaxBreakpoints < 0 {
		return nil, fmt.Errorf("nonempty identity and positive work budgets required")
	}
	if m.CloneState == nil || m.CloneInput == nil || m.CloneOutput == nil || m.Step == nil || equal == nil {
		return nil, fmt.Errorf("missing model copy, reducer or equality hook")
	}
	if !policy.Unrestricted && policy.Candidates == nil && policy.Validate == nil {
		return nil, fmt.Errorf("declared input policy needs candidates")
	}
	var epoch uint64
	for {
		old := epochs.Load()
		if old >= math.MaxUint64-1 {
			return nil, fmt.Errorf("session identity exhausted")
		}
		if epochs.CompareAndSwap(old, old+1) {
			epoch = old + 1
			break
		}
	}
	if m.Codec != nil {
		codec := *m.Codec
		m.Codec = &codec
	}
	state = m.CloneState(state)
	v := &Session[S, I, O]{id: id, epoch: epoch, model: m, state: state, policy: policy, equal: equal, limits: limits, phase: Ready, nextBreakpoint: 1}
	checked, e := s.CheckInitial(m, state)
	if e != nil {
		v.phase = Ended
		v.stop = &StopReason{Kind: "initial_check_error", Detail: e.Error()}
		return v, nil
	}
	v.initialChecked = checked
	v.initial = checked.Checks()
	v.complete = true
	for _, c := range v.initial {
		if c.Status == "failed" {
			v.phase = Ended
			v.stop = &StopReason{Kind: "property_failure"}
			break
		}
	}
	return v, nil
}
func (v *Session[S, I, O]) ID() string       { return v.id }
func (v *Session[S, I, O]) Epoch() uint64    { return v.epoch }
func (v *Session[S, I, O]) Revision() uint64 { return v.revision }
func (v *Session[S, I, O]) Sequence() uint64 { return v.sequence }
func (v *Session[S, I, O]) Phase() Phase     { return v.phase }

// Clone hooks are application callbacks too. Keep read-only projections from
// granting reentrant mutation of the session they are projecting.
func (v *Session[S, I, O]) State() S {
	old := v.busy
	v.busy = true
	defer func() { v.busy = old }()
	return v.model.CloneState(v.state)
}
func (v *Session[S, I, O]) copyInput(input I) I {
	old := v.busy
	v.busy = true
	defer func() { v.busy = old }()
	return v.model.CloneInput(input)
}
func (v *Session[S, I, O]) InitialChecks() []s.Check { return append([]s.Check{}, v.initial...) }
func (v *Session[S, I, O]) Issues() []string         { return append([]string{}, v.issues...) }
func (v *Session[S, I, O]) Stop() *StopReason {
	if v.stop == nil {
		return nil
	}
	c := *v.stop
	return &c
}
func (v *Session[S, I, O]) cloneTurn(t *s.TurnObservation[S, I, O]) *s.TurnObservation[S, I, O] {
	if t == nil {
		return nil
	}
	x := *t
	x.Before = v.model.CloneState(t.Before)
	x.Input = v.model.CloneInput(t.Input)
	x.Transition.State = v.model.CloneState(t.Transition.State)
	x.Transition.Outputs = make([]O, len(t.Transition.Outputs))
	for i, o := range t.Transition.Outputs {
		x.Transition.Outputs[i] = v.model.CloneOutput(o)
	}
	x.Checks = append([]s.Check{}, t.Checks...)
	return &x
}
func (v *Session[S, I, O]) Observation() Observation[S, I, O] {
	old := v.busy
	v.busy = true
	defer func() { v.busy = old }()
	return Observation[S, I, O]{State: v.State(), Sequence: v.sequence, Revision: v.revision, Phase: v.phase, ChecksComplete: v.complete, Last: v.cloneTurn(v.last), InitialChecks: v.InitialChecks()}
}
func (v *Session[S, I, O]) CheckRevision(expected uint64) error {
	if v.busy {
		return fmt.Errorf("session callback is active")
	}
	if expected != v.revision {
		return fmt.Errorf("stale revision: expected %d actual %d", expected, v.revision)
	}
	return nil
}
func (v *Session[S, I, O]) result(delivered bool) StepResult {
	return StepResult{v.sequence, delivered, v.Stop()}
}
func (v *Session[S, I, O]) permits(input I) (bool, error) {
	if v.policy.Unrestricted {
		return true, nil
	}
	if v.policy.Validate != nil {
		return v.policy.Validate(v.State(), v.model.CloneInput(input))
	}
	it, e := v.policy.Candidates(v.State())
	if e != nil {
		return false, e
	}
	if it == nil {
		return false, fmt.Errorf("nil candidate iterator")
	}
	for n := 0; ; n++ {
		x, ok, e := it()
		if e != nil {
			return false, e
		}
		if !ok {
			return false, nil
		}
		if n == v.limits.MaxCandidates {
			return false, fmt.Errorf("environmental admission work budget exhausted")
		}
		same, e := v.equal(v.model.CloneInput(x), v.model.CloneInput(input))
		if e != nil {
			return false, e
		}
		if same {
			return true, nil
		}
	}
}
func (v *Session[S, I, O]) Step(expected uint64, input I) (StepResult, error) {
	return v.StepWithObserver(expected, input, nil)
}
func (v *Session[S, I, O]) StepWithObserver(expected uint64, input I, observer func(Observation[S, I, O]) error) (StepResult, error) {
	if e := v.CheckRevision(expected); e != nil {
		return v.result(false), e
	}
	if v.phase == Ended {
		return v.result(false), fmt.Errorf("session ended")
	}
	if v.sequence >= v.limits.MaxTurns || v.revision == math.MaxUint64 {
		v.phase = Ended
		v.stop = &StopReason{Kind: "budget_exhausted"}
		return v.result(false), nil
	}
	v.busy = true
	defer func() { v.busy = false }()
	oldPhase := v.phase
	v.phase = Ended
	ok, e := v.permits(input)
	v.phase = oldPhase
	if e != nil {
		return v.result(false), e
	}
	if !ok {
		return v.result(false), fmt.Errorf("input not permitted")
	}
	bypass := false
	if v.pending != nil && v.pendingRevision == v.revision {
		bypass, e = v.equal(v.model.CloneInput(*v.pending), v.model.CloneInput(input))
		if e != nil {
			return v.result(false), e
		}
	}
	v.pending = nil
	v.phase = Ended
	if !bypass {
		for _, b := range v.pre {
			if b.predicate(v.State(), v.model.CloneInput(input)) {
				x := v.model.CloneInput(input)
				v.pending = &x
				v.pendingRevision = v.revision
				v.phase = Paused
				v.stop = &StopReason{Kind: "pre_breakpoint", Breakpoint: b.id}
				return v.result(false), nil
			}
		}
	}
	v.stop = nil
	before := v.State()
	input = v.model.CloneInput(input)
	transition, e := v.model.Step(v.model.CloneState(before), v.model.CloneInput(input))
	if e != nil {
		v.stop = &StopReason{Kind: "model_error", Detail: e.Error()}
		if v.recorder != nil {
			v.recorder.StopWithError(e)
		}
		return v.result(false), nil
	}
	if e := transition.Disposition.Validate(); e != nil {
		v.stop = &StopReason{Kind: "model_error", Detail: e.Error()}
		if v.recorder != nil {
			v.recorder.StopWithError(e)
		}
		return v.result(false), nil
	}
	v.sequence++
	v.revision++
	checked, e := s.CheckTurn(v.model, before, input, transition, v.sequence, s.FullChecks())
	v.complete = e == nil
	turn := &s.TurnObservation[S, I, O]{Sequence: v.sequence, Before: before, Input: input, Transition: transition}
	if e != nil {
		v.stop = &StopReason{Kind: "checker_error", Detail: e.Error()}
		if v.recorder != nil {
			v.recorder.StopWithError(e)
		}
	} else {
		if v.recorder != nil {
			if _, err := s.ObserveCheckedRecorder(v.recorder, checked); err != nil {
				v.issues = append(v.issues, err.Error())
				v.stop = &StopReason{Kind: "recording_error", Detail: err.Error()}
			}
		}
		turn, e = checkedObservation(checked)
		if e != nil {
			v.stop = &StopReason{Kind: "checker_error", Detail: e.Error()}
			if v.recorder != nil {
				v.recorder.StopWithError(e)
			}
			v.complete = false
		} else {
			for _, c := range turn.Checks {
				if c.Status == "failed" {
					v.stop = &StopReason{Kind: "property_failure"}
					break
				}
			}
		}
	}
	if v.stop == nil {
		for _, b := range v.post {
			if b.predicate(*v.cloneTurn(turn)) {
				v.stop = &StopReason{Kind: "post_breakpoint", Breakpoint: b.id}
				break
			}
		}
	}
	// Commit the actual reducer result even if checking/diagnostics fail.
	v.state = v.model.CloneState(transition.State)
	v.last = v.cloneTurn(turn)
	v.phase = Paused
	if v.stop != nil && v.stop.Kind != "post_breakpoint" {
		v.phase = Ended
	}
	if observer != nil {
		if e := observer(v.Observation()); e != nil {
			v.issues = append(v.issues, e.Error())
			if v.stop == nil {
				v.stop = &StopReason{Kind: "observer_error", Detail: e.Error()}
			}
			v.phase = Ended
		}
	}
	return v.result(true), nil
}
func checkedObservation[S, I, O any](c *s.CheckedTurn[S, I, O]) (*s.TurnObservation[S, I, O], error) {
	o, e := c.Observation()
	return &o, e
}
func (v *Session[S, I, O]) AddPreBreakpoint(predicate func(S, I) bool) (uint64, error) {
	if predicate == nil {
		return 0, fmt.Errorf("nil predicate")
	}
	id, e := v.breakpointID()
	if e == nil {
		v.pre = append(v.pre, preBreakpoint[S, I]{id, predicate})
	}
	return id, e
}
func (v *Session[S, I, O]) AddPostBreakpoint(predicate func(s.TurnObservation[S, I, O]) bool) (uint64, error) {
	if predicate == nil {
		return 0, fmt.Errorf("nil predicate")
	}
	id, e := v.breakpointID()
	if e == nil {
		v.post = append(v.post, postBreakpoint[S, I, O]{id, predicate})
	}
	return id, e
}
func (v *Session[S, I, O]) breakpointID() (uint64, error) {
	if v.busy || len(v.pre)+len(v.post) >= v.limits.MaxBreakpoints || v.nextBreakpoint == math.MaxUint64 {
		return 0, fmt.Errorf("breakpoint limit or active callback")
	}
	id := v.nextBreakpoint
	v.nextBreakpoint++
	return id, nil
}
func (v *Session[S, I, O]) RemoveBreakpoint(id uint64) bool {
	if v.busy {
		return false
	}
	for i, b := range v.pre {
		if b.id == id {
			v.pre = append(v.pre[:i], v.pre[i+1:]...)
			return true
		}
	}
	for i, b := range v.post {
		if b.id == id {
			v.post = append(v.post[:i], v.post[i+1:]...)
			return true
		}
	}
	return false
}
func (v *Session[S, I, O]) Cancel(expected uint64) error {
	if e := v.CheckRevision(expected); e != nil {
		return e
	}
	if v.phase == Ended {
		return fmt.Errorf("session ended")
	}
	v.phase = Ended
	v.stop = &StopReason{Kind: "cancelled"}
	v.pending = nil
	return nil
}
func (v *Session[S, I, O]) RunBounded(expected uint64, inputs s.Iterator[I], maximum int) ([]StepResult, error) {
	if e := v.CheckRevision(expected); e != nil {
		return nil, e
	}
	if maximum <= 0 || maximum > v.limits.MaxContinue || inputs == nil {
		return nil, fmt.Errorf("continue requires positive bounded turn count and iterator")
	}
	out := []StepResult{}
	for n := 0; n < maximum; n++ {
		input, ok, e := func() (I, bool, error) { v.busy = true; defer func() { v.busy = false }(); return inputs() }()
		if e != nil {
			return out, e
		}
		if !ok {
			return out, nil
		}
		r, e := v.Step(v.revision, input)
		if e != nil {
			return out, e
		}
		out = append(out, r)
		if r.Stop != nil {
			return out, nil
		}
	}
	v.stop = &StopReason{Kind: "budget_exhausted"}
	v.phase = Paused
	return out, nil
}

// CandidateToken binds a copied input to this exact session and revision. Its
// authority fields are private; list indices never become persistent identities.
type CandidateToken[I any] struct {
	clone           func(I) I
	input           I
	epoch, revision uint64
	owner           any
}
type CandidatePage[I any] struct {
	NextOffset *uint64
	Complete   bool
	Candidates []CandidateToken[I]
	Truncated  bool
}

func (v *Session[S, I, O]) Inputs(expected uint64, maximum int) (CandidatePage[I], error) {
	return v.InputsPage(expected, 0, maximum)
}
func (v *Session[S, I, O]) InputsPage(expected, offset uint64, maximum int) (CandidatePage[I], error) {
	page := CandidatePage[I]{}
	if e := v.CheckRevision(expected); e != nil {
		return page, e
	}
	if maximum <= 0 || maximum > v.limits.MaxCandidates || offset > uint64(v.limits.MaxCandidates)-uint64(maximum) || v.model.Inputs == nil {
		return page, fmt.Errorf("enumeration unavailable or invalid candidate work budget")
	}
	v.busy = true
	defer func() { v.busy = false }()
	it, e := v.model.Inputs(v.State())
	if e != nil {
		return page, e
	}
	if it == nil {
		return page, fmt.Errorf("nil input iterator")
	}
	for n := uint64(0); n <= offset+uint64(maximum); n++ {
		x, ok, e := it()
		if e != nil {
			return CandidatePage[I]{}, e
		}
		if !ok {
			page.Complete = true
			return page, nil
		}
		if n < offset {
			continue
		}
		if n == offset+uint64(maximum) {
			page.Truncated = true
			next := offset + uint64(maximum)
			page.NextOffset = &next
			return page, nil
		}
		page.Candidates = append(page.Candidates, CandidateToken[I]{clone: v.copyInput, input: v.model.CloneInput(x), epoch: v.epoch, revision: v.revision, owner: v})
	}
	return page, nil
}
func (t CandidateToken[I]) Input() I {
	if t.clone != nil {
		return t.clone(t.input)
	}
	var zero I
	return zero
}
func (t CandidateToken[I]) Revision() uint64 { return t.revision }
func (v *Session[S, I, O]) CandidatePermitted(input I) (bool, error) {
	if v.busy {
		return false, fmt.Errorf("session callback active")
	}
	if v.model.Inputs == nil {
		return false, fmt.Errorf("enumeration unavailable")
	}
	v.busy = true
	defer func() { v.busy = false }()
	it, e := v.model.Inputs(v.State())
	if e != nil {
		return false, e
	}
	if it == nil {
		return false, fmt.Errorf("nil input iterator")
	}
	for n := 0; ; n++ {
		candidate, ok, e := it()
		if e != nil {
			return false, e
		}
		if !ok {
			return false, nil
		}
		if n == v.limits.MaxCandidates {
			return false, fmt.Errorf("candidate admission work budget exhausted")
		}
		same, e := v.equal(v.model.CloneInput(candidate), v.model.CloneInput(input))
		if e != nil {
			return false, e
		}
		if same {
			return true, nil
		}
	}
}
func (v *Session[S, I, O]) Select(token CandidateToken[I]) (StepResult, error) {
	if token.owner != v || token.epoch != v.epoch || token.revision != v.revision {
		return v.result(false), fmt.Errorf("stale or foreign candidate token")
	}
	ok, e := v.CandidatePermitted(token.input)
	if e != nil {
		return v.result(false), e
	}
	if !ok {
		return v.result(false), fmt.Errorf("candidate input no longer permitted")
	}
	return v.Step(token.revision, v.copyInput(token.input))
}

type Mode string

const (
	TraceViewMode       Mode = "trace_view"
	VerifiedReplayMode  Mode = "verified_replay"
	SimulationMode      Mode = "simulation"
	LiveObservationMode Mode = "live_observation"
	LiveControlMode     Mode = "live_control"
)

type Capabilities struct {
	Mode                                                                        Mode
	TypedInput, ExactRecording, EnumerateInputs, EffectsDispatched, LiveRestore bool
}

func (v *Session[S, I, O]) Capabilities() Capabilities {
	return Capabilities{Mode: SimulationMode, TypedInput: true, ExactRecording: v.recorder != nil, EnumerateInputs: v.policy.Candidates != nil}
}
func (v *Session[S, I, O]) ChecksComplete() bool  { return v.complete }
func (v *Session[S, I, O]) InputPolicy() string   { return v.policy.Label }
func (v *Session[S, I, O]) Limits() SessionLimits { return v.limits }
func (v *Session[S, I, O]) StepEncoded(expected uint64, data []byte) (StepResult, error) {
	if e := v.CheckRevision(expected); e != nil {
		return v.result(false), e
	}
	if uint64(len(data)) > s.DefaultBinaryLimits().MaxBlobBytes {
		return v.result(false), fmt.Errorf("input payload exceeds byte limit")
	}
	if v.model.Codec == nil || v.model.Codec.DecodeInput == nil {
		return v.result(false), fmt.Errorf("input decoder unavailable")
	}
	input, e := func() (I, error) {
		v.busy = true
		defer func() { v.busy = false }()
		return v.model.Codec.DecodeInput(append([]byte{}, data...))
	}()
	if e != nil {
		return v.result(false), fmt.Errorf("decode input: %w", e)
	}
	return v.Step(expected, input)
}

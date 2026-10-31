// Package debug provides opt-in offline diagnostics. Importing the core engine
// does not import this package. Evidence is untrusted stored data, not authority.
package debug

import (
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"io"
	"math"
	"strconv"
	"strings"
)

type EvidenceViewer struct {
	artifact   s.Artifact
	limits     s.EvidenceLimits
	checkpoint uint64
}
type Boundary struct {
	Before, After, Input        []byte
	Outputs                     [][]byte
	Checks                      []s.Check
	Causal                      *s.StepEvidence
	LocalSequence, HostSequence uint64
}
type EncodedPreview struct {
	Bytes      []byte
	TotalBytes uint64
	Complete   bool
	Provenance string
}

func Preview(data []byte, maximum uint64) EncodedPreview {
	n := min(uint64(len(data)), maximum)
	return EncodedPreview{append([]byte{}, data[:n]...), uint64(len(data)), n == uint64(len(data)), "stored-exact-encoding"}
}
func NewEvidenceViewer(a s.Artifact, limits s.EvidenceLimits) (*EvidenceViewer, error) {
	// Validate before copying to avoid retaining a second oversized artifact.
	var t *s.AuditTrace
	if (a.V1 == nil) == (a.V2 == nil) {
		return nil, fmt.Errorf("artifact must contain exactly one version")
	}
	if a.V1 != nil {
		t = a.V1
	} else {
		t = &a.V2.Trace
	}
	if a.V2 != nil {
		if e := a.V2.Validate(limits); e != nil {
			return nil, e
		}
	} else {
		if e := s.ValidateRecording(&t.Trace); e != nil {
			return nil, e
		}
		if e := t.WriteBinaryWithLimits(io.Discard, limits.Trace); e != nil {
			return nil, e
		}
	}
	checkpoint := uint64(0)
	seen := false
	if a.V1 != nil {
		var origin uint64
		originSeen := false
		for _, entry := range t.Config.Parameters {
			var target *uint64
			var present *bool
			switch entry[0] {
			case "stateless.monitor.checkpoint_after_sequence":
				target, present = &checkpoint, &seen
			case "stateless.monitor.sequence_origin":
				target, present = &origin, &originSeen
			default:
				continue
			}
			if *present {
				return nil, fmt.Errorf("ambiguous retained checkpoint sequence provenance")
			}
			*present = true
			value, err := strconv.ParseUint(strings.TrimPrefix(entry[1], "+"), 10, 64)
			if err != nil {
				return nil, fmt.Errorf("invalid retained checkpoint sequence provenance")
			}
			*target = value
		}
		if checkpoint > math.MaxUint64-origin {
			return nil, fmt.Errorf("host sequence overflow")
		}
		checkpoint += origin
	}
	if a.V2 != nil {
		checkpoint = a.V2.CheckpointAfter
	}
	if uint64(len(t.Steps)) > math.MaxUint64-checkpoint {
		return nil, fmt.Errorf("host sequence overflow")
	}
	return &EvidenceViewer{a.Snapshot(), limits, checkpoint}, nil
}
func ReadEvidenceViewer(r io.Reader, l s.EvidenceLimits) (*EvidenceViewer, error) {
	a, e := s.ReadArtifact(r, l)
	if e != nil {
		return nil, e
	}
	return NewEvidenceViewer(a, l)
}
func (v *EvidenceViewer) Artifact() s.Artifact { return v.artifact.Snapshot() }
func (v *EvidenceViewer) Boundary(sequence uint64) (Boundary, bool) {
	t := v.artifact.ExactTrace()
	if sequence > uint64(len(t.Steps)) {
		return Boundary{}, false
	}
	b := Boundary{Before: t.InitialState, After: t.InitialState, Checks: t.InitialChecks, LocalSequence: sequence, HostSequence: sequence}

	b.HostSequence += v.checkpoint
	if sequence > 0 {
		i := int(sequence) - 1
		x := t.Steps[i]
		b.After = x.PostState
		b.Input = x.Input
		b.Outputs = x.Outputs
		b.Checks = x.Checks
		if i > 0 {
			b.Before = t.Steps[i-1].PostState
		}
		if v.artifact.V2 != nil {
			a := v.artifact.V2.Snapshot()
			b.Causal = &a.Steps[i]
		}
	}
	return b, true
}

type PropertyFailure struct {
	Phase s.CheckPhase
	Check s.Check
}

func (v *EvidenceViewer) Failure(sequence, check uint64) (PropertyFailure, error) {
	b, ok := v.Boundary(sequence)
	if !ok || check >= uint64(len(b.Checks)) {
		return PropertyFailure{}, fmt.Errorf("unknown evidence check")
	}
	c := b.Checks[check]
	if c.Status != "failed" {
		return PropertyFailure{}, fmt.Errorf("selected check is not a failure")
	}
	p, ok := v.artifact.CheckPhase(sequence, check)
	if !ok {
		return PropertyFailure{}, fmt.Errorf("v1 phase unknown; explicitly verify and upgrade or supply a phase")
	}
	return PropertyFailure{p, c}, nil
}

type Verification[S, I, O any] struct {
	Report      *s.ReplayReport
	Initial     *s.ReplayInitialObservation[S]
	Turns       []s.ReplayTurnObservation[S, I, O]
	Transitions uint64
}

// Verify runs only model replay. There is no host output dispatcher. Bounds are
// checked between callbacks; arbitrary callback work cannot be preempted.
func Verify[S, I, O any](v *EvidenceViewer, m s.Model[S, I, O], limits s.ShrinkLimits) (*Verification[S, I, O], error) {
	return VerifyWithOptions(v, m, limits, false)
}
func VerifyWithOptions[S, I, O any](v *EvidenceViewer, m s.Model[S, I, O], limits s.ShrinkLimits, allowBuildMismatch bool) (*Verification[S, I, O], error) {
	limits = snapshotLimits(limits)
	deadline, err := s.NewRunDeadline(limits.Control)
	if err != nil {
		return nil, err
	}
	if m.Codec != nil {
		codec := *m.Codec
		m.Codec = &codec
	}
	result := &Verification[S, I, O]{}
	if limits.MaxReplayedTransitions == nil {
		maximum := v.limits.Trace.MaxSteps
		limits.MaxReplayedTransitions = &maximum
	}
	t := v.artifact.ExactTrace()
	stop := func() error {
		switch deadline.Stop() {
		case "cancelled":
			return fmt.Errorf("verification cancelled")
		case "deadline":
			return fmt.Errorf("verification deadline")
		}
		return nil
	}
	if e := stop(); e != nil {
		return result, e
	}
	if limits.MaxReplayedTransitions != nil && uint64(len(t.Steps)) > *limits.MaxReplayedTransitions {
		return result, fmt.Errorf("budget cannot cover verification")
	}
	observer := s.ReplayObserver[S, I, O]{Initial: func(o s.ReplayInitialObservation[S]) error { result.Initial = &o; return stop() }, Turn: func(o s.ReplayTurnObservation[S, I, O]) error {
		result.Transitions++
		result.Turns = append(result.Turns, o)
		if v.artifact.V2 != nil && o.Report.Outcome == "exact" {
			i := o.Actual.Sequence - 1
			if o.Actual.StateCheckCount == nil || uint64(*o.Actual.StateCheckCount) != uint64(v.artifact.V2.Steps[i].StateCheckCount) {
				return fmt.Errorf("persisted check phase differs from replay")
			}
		}
		return stop()
	}}
	r, e := s.ReplayWithObservationsBounded(m, t, allowBuildMismatch, v.limits.Trace, observer)
	result.Report = r
	// Preserve a replay/callback failure over a simultaneous stop, but do not
	// return a non-error replay outcome if an early exit bypassed all observers
	// after consuming the remaining deadline or observing cancellation.
	if e == nil {
		e = stop()
	}
	return result, e
}

func snapshotLimits(limits s.ShrinkLimits) s.ShrinkLimits {
	if limits.MaxReplayedTransitions != nil {
		x := *limits.MaxReplayedTransitions
		limits.MaxReplayedTransitions = &x
	}
	if limits.Control.MaxDuration != nil {
		x := *limits.Control.MaxDuration
		limits.Control.MaxDuration = &x
	}
	if limits.Control.MaxDurationNS != nil {
		x := *limits.Control.MaxDurationNS
		limits.Control.MaxDurationNS = &x
	}
	return limits
}

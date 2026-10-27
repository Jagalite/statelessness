package debug

import (
	"fmt"
	s "github.com/Jagalite/statelessness/go"
)

// TraceViewer is a passive local cursor over owned evidence. Verification and
// branch execution never change this cursor. Its digest binds the exact v1 trace
// bytes/config, while v2 causal provenance stays in the underlying artifact.
type TraceViewer struct {
	viewer   *EvidenceViewer
	position uint64
	identity ArtifactIdentity
}

func NewTraceViewer(viewer *EvidenceViewer) (*TraceViewer, error) {
	if viewer == nil {
		return nil, fmt.Errorf("nil evidence viewer")
	}
	identity, e := ArtifactDigest(viewer.artifact.ExactTrace(), viewer.limits.Trace)
	if e != nil {
		return nil, e
	}
	return &TraceViewer{viewer: viewer, identity: identity}, nil
}
func (v *TraceViewer) Identity() ArtifactIdentity { return v.identity }
func (v *TraceViewer) Position() uint64           { return v.position }
func (v *TraceViewer) Seek(sequence uint64) error {
	if _, ok := v.viewer.Boundary(sequence); !ok {
		return fmt.Errorf("position outside retained history")
	}
	v.position = sequence
	return nil
}
func (v *TraceViewer) Current() Boundary { b, _ := v.viewer.Boundary(v.position); return b }
func (v *TraceViewer) Next() bool {
	if v.position == ^uint64(0) {
		return false
	}
	if _, ok := v.viewer.Boundary(v.position + 1); !ok {
		return false
	}
	v.position++
	return true
}
func (v *TraceViewer) Previous() bool {
	if v.position == 0 {
		return false
	}
	v.position--
	return true
}
func (v *TraceViewer) InputPreview(maximum uint64) (EncodedPreview, bool) {
	if v.position == 0 {
		return EncodedPreview{}, false
	}
	return Preview(v.Current().Input, maximum), true
}
func (v *TraceViewer) OutputPreview(index, maximum uint64) (EncodedPreview, bool) {
	b := v.Current()
	if index >= uint64(len(b.Outputs)) {
		return EncodedPreview{}, false
	}
	return Preview(b.Outputs[index], maximum), true
}

// DecodeState explicitly invokes an application codec after exact identity
// matching. It does not certify this build's replay or decode historical outputs.
func DecodeState[S, I, O any](v *TraceViewer, m s.Model[S, I, O], sequence uint64) (S, error) {
	var zero S
	if m.Codec == nil || m.Codec.Metadata == nil || m.Codec.DecodeState == nil {
		return zero, fmt.Errorf("state decoder unavailable")
	}
	metadata, e := m.Codec.Metadata()
	if e != nil {
		return zero, e
	}
	if metadata != v.viewer.artifact.ExactTrace().Metadata {
		return zero, fmt.Errorf("snapshot decoder identity differs; verify selected build before branching")
	}
	b, ok := v.viewer.Boundary(sequence)
	if !ok {
		return zero, fmt.Errorf("snapshot outside retained history")
	}
	return m.Codec.DecodeState(b.After)
}

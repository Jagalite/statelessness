package debug

import (
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"hash/fnv"
	"io"
	"strconv"
	"strings"
)

type ArtifactIdentity struct{ FNV1a64, Bytes uint64 }
type identityWriter struct {
	hash  uint64
	bytes uint64
}

func (w *identityWriter) Write(b []byte) (int, error) {
	for _, v := range b {
		w.hash = (w.hash ^ uint64(v)) * 1099511628211
	}
	w.bytes += uint64(len(b))
	return len(b), nil
}
func ArtifactDigest(trace *s.AuditTrace, limits s.BinaryLimits) (ArtifactIdentity, error) {
	w := identityWriter{hash: 14695981039346656037}
	if e := trace.WriteBinaryWithLimits(&w, limits); e != nil {
		return ArtifactIdentity{}, e
	}
	return ArtifactIdentity{w.hash, w.bytes}, nil
}
func bytesDigest(b []byte) ArtifactIdentity {
	h := fnv.New64a()
	h.Write(b)
	return ArtifactIdentity{h.Sum64(), uint64(len(b))}
}

type BranchProvenance struct {
	Version                       uint32
	Parent, Checkpoint            ArtifactIdentity
	ForkSequence                  uint64
	ParentMetadata, ModelMetadata s.Metadata
	ParentPrefixVerified          bool
	Environment, Origin           string
}

func (p BranchProvenance) ValidateParent(trace *s.AuditTrace, limits s.BinaryLimits) error {
	digest, e := ArtifactDigest(trace, limits)
	if e != nil {
		return e
	}
	if p.Version != 1 || digest != p.Parent || trace.Metadata != p.ParentMetadata {
		return fmt.Errorf("branch sidecar does not match parent artifact")
	}
	checkpoint := trace.InitialState
	if p.ForkSequence > 0 {
		if p.ForkSequence > uint64(len(trace.Steps)) {
			return fmt.Errorf("fork outside parent history")
		}
		checkpoint = trace.Steps[p.ForkSequence-1].PostState
	}
	if bytesDigest(checkpoint) != p.Checkpoint {
		return fmt.Errorf("fork checkpoint binding differs")
	}
	return nil
}

type Branch[S, I, O any] struct {
	*Session[S, I, O]
	Provenance BranchProvenance
}

var _ io.Writer = (*identityWriter)(nil)

// RecordingSessionFromState checks its checkpoint once and hands the canonical
// checked token to the recorder. The reducer/checker is never run twice to record.
func RecordingSessionFromState[S, I, O any](id string, m s.Model[S, I, O], state S, p InputPolicy[S, I], equal func(I, I) (bool, error), limits SessionLimits, config s.RunConfig, options s.RecorderOptions) (*Session[S, I, O], error) {
	return recordingSessionFromState(id, m, state, p, equal, limits, config, options, false)
}
func recordingSessionFromState[S, I, O any](id string, m s.Model[S, I, O], state S, p InputPolicy[S, I], equal func(I, I) (bool, error), limits SessionLimits, config s.RunConfig, options s.RecorderOptions, provenance bool) (*Session[S, I, O], error) {
	for _, item := range config.Parameters {
		if strings.HasPrefix(item[0], "stateless.debug.") && !provenance {
			return nil, fmt.Errorf("reserved debugger provenance")
		}
	}
	origin := "simulation"
	if p.Unrestricted {
		origin = "out-of-domain-injection"
	}
	config.Parameters = append(append([][2]string{}, config.Parameters...), [2]string{"stateless.debug.environment", p.Label}, [2]string{"stateless.debug.origin", origin})
	v, e := SessionFromState(id, m, state, p, equal, limits)
	if e != nil {
		return nil, e
	}
	if v.initialChecked == nil {
		return v, nil
	}
	r, e := s.NewRecorderWithCheckedInitial(v.initialChecked, config, options)
	if e != nil {
		v.issues = append(v.issues, e.Error())
		if v.stop == nil {
			v.stop = &StopReason{Kind: "recording_error", Detail: e.Error()}
			v.phase = Ended
		}
		return v, nil
	}
	v.recorder = r
	return v, nil
}
func (v *Session[S, I, O]) ExportTrace() (*s.AuditTrace, error) {
	if v.busy {
		return nil, fmt.Errorf("session callback is active")
	}
	if v.recorder == nil {
		return nil, fmt.Errorf("session has no recorder")
	}
	return v.recorder.Snapshot(), nil
}

// Fork verifies the selected stored prefix before decoding its checkpoint. The
// child owns its state and fresh session epoch. This does not reconstruct outputs.
func Fork[S, I, O any](v *EvidenceViewer, m s.Model[S, I, O], sequence uint64, id string, p InputPolicy[S, I], equal func(I, I) (bool, error), limits SessionLimits, verification s.ShrinkLimits) (*Branch[S, I, O], error) {
	return ForkWithOptions(v, m, sequence, id, p, equal, limits, verification, false)
}
func ForkWithOptions[S, I, O any](v *EvidenceViewer, m s.Model[S, I, O], sequence uint64, id string, p InputPolicy[S, I], equal func(I, I) (bool, error), limits SessionLimits, verification s.ShrinkLimits, allowBuildMismatch bool) (*Branch[S, I, O], error) {
	b, ok := v.Boundary(sequence)
	if !ok {
		return nil, fmt.Errorf("unknown fork boundary")
	}
	t := v.artifact.ExactTrace()
	parentIdentity, e := ArtifactDigest(t, v.limits.Trace)
	if e != nil {
		return nil, e
	}
	for _, c := range t.InitialChecks {
		if c.Status == "failed" {
			return nil, fmt.Errorf("normal branches must start before property failure")
		}
	}
	for _, c := range b.Checks {
		if c.Status == "failed" {
			return nil, fmt.Errorf("normal branches must start before property failure")
		}
	}
	if e := policyProvenance(t, &p); e != nil && !p.Unrestricted {
		return nil, e
	}
	t.Steps = t.Steps[:sequence]
	t.Termination = "completed"
	t.Error = ""
	checks := t.InitialChecks
	if sequence > 0 {
		checks = t.Steps[sequence-1].Checks
	}
	for _, c := range checks {
		if c.Status == "failed" {
			t.Termination = "property_failed"
		}
	}
	a := s.Artifact{V1: t}
	if v.artifact.V2 != nil {
		evidence := v.artifact.V2.Snapshot()
		evidence.Trace = *t
		evidence.Steps = evidence.Steps[:sequence]
		a = s.Artifact{V2: evidence}
	}
	prefix, e := NewEvidenceViewer(a, v.limits)
	if e != nil {
		return nil, e
	}
	report, e := VerifyWithOptions(prefix, m, verification, allowBuildMismatch)
	if e != nil {
		return nil, e
	}
	if report.Report.Outcome != "exact" {
		return nil, fmt.Errorf("fork requires exactly verified prefix")
	}
	if m.Codec == nil || m.Codec.DecodeState == nil {
		return nil, fmt.Errorf("fork requires state decoder")
	}
	state, e := m.Codec.DecodeState(b.After)
	if e != nil {
		return nil, e
	}
	origin := "verified-simulation-branch"
	if p.Unrestricted {
		origin = "out-of-domain-injection"
	}
	metadata, e := m.Codec.Metadata()
	if e != nil {
		return nil, e
	}
	provenance := BranchProvenance{Version: 1, Parent: parentIdentity, Checkpoint: bytesDigest(b.After), ForkSequence: sequence, ParentMetadata: t.Metadata, ModelMetadata: metadata, ParentPrefixVerified: true, Environment: p.Label, Origin: origin}
	config := s.RunConfig{Strategy: "debugger-branch", Seed: t.Config.Seed, Parameters: [][2]string{{"stateless.debug.parent.digest", fmt.Sprintf("fnv1a64:%016x", parentIdentity.FNV1a64)}, {"stateless.debug.parent.bytes", strconv.FormatUint(parentIdentity.Bytes, 10)}, {"stateless.debug.fork.sequence", strconv.FormatUint(sequence, 10)}, {"stateless.debug.fork.checkpoint", fmt.Sprintf("fnv1a64:%016x", provenance.Checkpoint.FNV1a64)}, {"stateless.debug.parent.build", t.Metadata.Build}, {"stateless.debug.parent_prefix_verified", "true"}, {"stateless.debug.branch.origin", origin}}}
	child, e := recordingSessionFromState(id, m, state, p, equal, limits, config, s.DefaultRecorderOptions(), true)
	if e != nil {
		return nil, e
	}
	return &Branch[S, I, O]{child, provenance}, nil
}

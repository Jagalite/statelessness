package statelessness

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"hash/crc32"
	"io"
	"math"
	"unicode/utf8"
)

const EvidenceFormatVersion uint16 = 2
const evidenceOverhead uint64 = 32

type Provenance struct {
	Run              string
	Epoch            uint64
	Manifest, Source *string
	Environment      [][2]string
}
type OutputOrigin struct {
	Sequence uint64
	Output   uint32
}
type CausalContext struct {
	Machine     *string
	Generation  *uint64
	Correlation *string
	LogicalTime *uint64
	Causes      []OutputOrigin
}
type StepEvidence struct {
	Sequence        uint64
	StateCheckCount uint32
	Causal          CausalContext
}
type EvidenceLimits struct {
	Trace                                                                        BinaryLimits
	MaxMetadataBytes, MaxLabels, MaxLabelBytes, MaxCausesPerStep, MaxTotalCauses uint64
}

func DefaultEvidenceLimits() EvidenceLimits {
	return EvidenceLimits{DefaultBinaryLimits(), 4 * 1024 * 1024, 1024, 4096, 32, 100000}
}

// TraceV2 preserves exact v1 bytes/configuration and independent causal phases.
// Public data is owned; Snapshot returns a detached copy. Labels are assertions,
// not authentication. Applications must not mutate an artifact during operations.
type TraceV2 struct {
	Trace           AuditTrace
	CheckpointAfter uint64
	Provenance      Provenance
	Steps           []StepEvidence
}
type CheckPhase string

const (
	PhaseInitialState CheckPhase = "initial_state"
	PhaseState        CheckPhase = "state"
	PhaseTransition   CheckPhase = "transition"
)

func evidenceTraceError(e error) error {
	var quota *AuditError
	if errors.As(e, &quota) && quota.Kind == "limit_exceeded" {
		return fmt.Errorf("trace exceeds %s limit: %w", quota.Detail, e)
	}
	return e
}

func evidenceLabel(v string, l EvidenceLimits) error {
	if uint64(len(v)) > l.MaxLabelBytes {
		return &ModelError{"evidence label byte limit"}
	}
	if !utf8.ValidString(v) {
		return &ModelError{"invalid UTF-8"}
	}
	return nil
}
func validateEvidenceProvenance(v Provenance, l EvidenceLimits) error {
	if e := evidenceLabel(v.Run, l); e != nil {
		return e
	}
	for _, s := range []*string{v.Manifest, v.Source} {
		if s != nil {
			if e := evidenceLabel(*s, l); e != nil {
				return e
			}
		}
	}
	if uint64(len(v.Environment)) > l.MaxLabels {
		return &ModelError{"evidence environment label limit"}
	}
	seen := map[string]bool{}
	for _, p := range v.Environment {
		if e := evidenceLabel(p[0], l); e != nil {
			return e
		}
		if e := evidenceLabel(p[1], l); e != nil {
			return e
		}
		if p[0] == "" || seen[p[0]] {
			return &ModelError{"empty or duplicate evidence environment name"}
		}
		seen[p[0]] = true
	}
	return nil
}
func validateEvidenceContext(v CausalContext, sequence uint64, l EvidenceLimits) error {
	for _, s := range []*string{v.Machine, v.Correlation} {
		if s != nil {
			if e := evidenceLabel(*s, l); e != nil {
				return e
			}
		}
	}
	if uint64(len(v.Causes)) > l.MaxCausesPerStep {
		return &ModelError{"causal links per step limit"}
	}
	seen := map[OutputOrigin]bool{}
	for _, o := range v.Causes {
		if o.Sequence == 0 || o.Sequence >= sequence || seen[o] {
			return &ModelError{"causal origin must be unique and precede its input"}
		}
		seen[o] = true
	}
	return nil
}
func (v *TraceV2) Snapshot() *TraceV2 {
	if v == nil {
		return nil
	}
	out := *v
	out.Trace = AuditTrace{Trace: clonePortableTrace(v.Trace.Trace), Config: cloneRunConfig(v.Trace.Config)}
	out.Provenance = cloneProvenance(v.Provenance)
	out.Steps = make([]StepEvidence, len(v.Steps))
	for i, s := range v.Steps {
		out.Steps[i] = cloneStepEvidence(s)
	}
	return &out
}

type evidenceCounter struct{ n uint64 }

func (c *evidenceCounter) Write(p []byte) (int, error) {
	if uint64(len(p)) > math.MaxUint64-c.n {
		return 0, &ModelError{"evidence size overflow"}
	}
	c.n += uint64(len(p))
	return len(p), nil
}
func (v *TraceV2) Validate(l EvidenceLimits) error {
	if v == nil {
		return &ConfigError{"nil v2 evidence"}
	}
	if e := validateEvidenceProvenance(v.Provenance, l); e != nil {
		return e
	}
	if e := ValidateRecording(&v.Trace.Trace); e != nil {
		return e
	}
	measured := &evidenceCounter{}
	if e := v.Trace.WriteBinaryWithLimits(measured, l.Trace); e != nil {
		return evidenceTraceError(e)
	}
	metadata, e := encodeEvidenceMetadata(v, l.MaxMetadataBytes)
	if e != nil {
		return e
	}
	if measured.n > math.MaxUint64-evidenceOverhead || uint64(len(metadata)) > math.MaxUint64-measured.n-evidenceOverhead || measured.n+uint64(len(metadata))+evidenceOverhead > l.Trace.MaxTotalBytes {
		return &ModelError{"evidence total byte limit"}
	}
	if len(v.Steps) != len(v.Trace.Steps) {
		return &ModelError{"phase evidence does not cover every recorded step"}
	}
	if uint64(len(v.Steps)) > math.MaxUint64-v.CheckpointAfter {
		return &ModelError{"evidence sequence overflow"}
	}
	var causes uint64
	for i, s := range v.Steps {
		if s.Sequence != v.CheckpointAfter+uint64(i)+1 || uint64(s.StateCheckCount) > uint64(len(v.Trace.Steps[i].Checks)) {
			return &ModelError{"invalid phase evidence sequence or check boundary"}
		}
		if e := validateEvidenceContext(s.Causal, s.Sequence, l); e != nil {
			return e
		}
		if uint64(len(s.Causal.Causes)) > math.MaxUint64-causes {
			return &ModelError{"causal count overflow"}
		}
		causes += uint64(len(s.Causal.Causes))
		if causes > l.MaxTotalCauses {
			return &ModelError{"total causal links limit"}
		}
		for _, o := range s.Causal.Causes {
			if o.Sequence > v.CheckpointAfter {
				index := o.Sequence - v.CheckpointAfter - 1
				if index >= uint64(len(v.Trace.Steps)) || uint64(o.Output) >= uint64(len(v.Trace.Steps[int(index)].Outputs)) {
					return &ModelError{"causal origin output index outside retained evidence"}
				}
			}
		}
	}
	return nil
}
func (v *TraceV2) CheckPhase(sequence, check uint64) (CheckPhase, bool) {
	if v == nil {
		return "", false
	}
	if sequence == 0 {
		if check < uint64(len(v.Trace.InitialChecks)) {
			return PhaseInitialState, true
		}
		return "", false
	}
	i := sequence - 1
	if i >= uint64(len(v.Trace.Steps)) || i >= uint64(len(v.Steps)) || check >= uint64(len(v.Trace.Steps[int(i)].Checks)) {
		return "", false
	}
	if check < uint64(v.Steps[int(i)].StateCheckCount) {
		return PhaseState, true
	}
	return PhaseTransition, true
}

// Write preflights the entire envelope before publishing any bytes.
func (v *TraceV2) Write(w io.Writer, l EvidenceLimits) error {
	if w == nil {
		return &ConfigError{"nil evidence writer"}
	}
	if e := v.Validate(l); e != nil {
		return e
	}
	metadata, e := encodeEvidenceMetadata(v, l.MaxMetadataBytes)
	if e != nil {
		return e
	}
	exact, e := MarshalAuditTrace(&v.Trace, l.Trace)
	if e != nil {
		return evidenceTraceError(e)
	}
	header := make([]byte, 28)
	copy(header, auditHeader[:8])
	binary.LittleEndian.PutUint16(header[8:], EvidenceFormatVersion)
	binary.LittleEndian.PutUint64(header[12:], uint64(len(exact)))
	binary.LittleEndian.PutUint64(header[20:], uint64(len(metadata)))
	sum := crc32.NewIEEE()
	sum.Write(header)
	sum.Write(exact)
	sum.Write(metadata)
	var crc [4]byte
	binary.LittleEndian.PutUint32(crc[:], sum.Sum32())
	for _, b := range [][]byte{header, exact, metadata, crc[:]} {
		if e = auditWriteAll(w, b); e != nil {
			return fmt.Errorf("write evidence: %w", e)
		}
	}
	return nil
}
func MarshalTraceV2(v *TraceV2, l EvidenceLimits) ([]byte, error) {
	var b bytes.Buffer
	if e := v.Write(&b, l); e != nil {
		return nil, e
	}
	return b.Bytes(), nil
}

// Artifact preserves whether phases were persisted in v2 or unknown in v1.
// Exactly one of V1/V2 must be nonnil. Reading never invokes a model.
type Artifact struct {
	V1 *AuditTrace
	V2 *TraceV2
}

func (a Artifact) traceRef() (*AuditTrace, error) {
	if (a.V1 == nil) == (a.V2 == nil) {
		return nil, &ConfigError{"artifact must contain exactly one version"}
	}
	if a.V1 != nil {
		return a.V1, nil
	}
	return &a.V2.Trace, nil
}
func (a Artifact) ExactTrace() *AuditTrace {
	t, e := a.traceRef()
	if e != nil {
		return nil
	}
	return &AuditTrace{Trace: clonePortableTrace(t.Trace), Config: cloneRunConfig(t.Config)}
}
func (a Artifact) CheckPhase(sequence, check uint64) (CheckPhase, bool) {
	t, e := a.traceRef()
	if e != nil {
		return "", false
	}
	if a.V2 != nil {
		return a.V2.CheckPhase(sequence, check)
	}
	if sequence == 0 && check < uint64(len(t.InitialChecks)) {
		return PhaseInitialState, true
	}
	return "", false
}
func ParseArtifact(data []byte, l EvidenceLimits) (Artifact, error) {
	return ReadArtifact(bytes.NewReader(data), l)
}
func ReadArtifact(r io.Reader, l EvidenceLimits) (Artifact, error) {
	var z Artifact
	if r == nil {
		return z, &ConfigError{"nil evidence reader"}
	}
	data := []byte{}
	var chunk [8192]byte
	for {
		n, e := r.Read(chunk[:])
		if n < 0 || n > len(chunk) {
			return z, &ModelError{"invalid reader count"}
		}
		if uint64(n) > l.Trace.MaxTotalBytes-uint64(len(data)) {
			return z, &ModelError{"evidence byte limit"}
		}
		if n > 0 {
			required := len(data) + n
			if required < len(data) {
				return z, &ModelError{"evidence size overflow"}
			}
			if required > cap(data) {
				capacity := min(l.Trace.MaxTotalBytes, uint64(max(required, cap(data)*2)))
				if capacity > uint64(math.MaxInt) {
					capacity = uint64(required)
				}
				owned := make([]byte, len(data), int(capacity))
				copy(owned, data)
				data = owned
			}
			data = append(data, chunk[:n]...)
		}
		if e == io.EOF {
			break
		}
		if e != nil {
			return z, fmt.Errorf("read evidence: %w", e)
		}
		if n == 0 {
			return z, io.ErrNoProgress
		}
	}
	if len(data) < 12 || !bytes.Equal(data[:8], auditHeader[:8]) {
		return z, &ModelError{"truncated or invalid evidence header"}
	}
	version := binary.LittleEndian.Uint16(data[8:])
	if version == 1 {
		v, e := ParseAuditTrace(data, l.Trace)
		if e != nil {
			return z, evidenceTraceError(e)
		}
		return Artifact{V1: v}, nil
	}
	if version != 2 {
		return z, &ModelError{"unsupported evidence version"}
	}
	if data[10] != 0 || data[11] != 0 || len(data) < 32 {
		return z, &ModelError{"invalid or truncated v2 header"}
	}
	traceLen, metaLen := binary.LittleEndian.Uint64(data[12:]), binary.LittleEndian.Uint64(data[20:])
	if traceLen > math.MaxUint64-metaLen || traceLen+metaLen > math.MaxUint64-32 || traceLen+metaLen+32 != uint64(len(data)) || metaLen > l.MaxMetadataBytes {
		return z, &ModelError{"invalid evidence lengths or metadata limit"}
	}
	end := len(data) - 4
	if crc32.ChecksumIEEE(data[:end]) != binary.LittleEndian.Uint32(data[end:]) {
		return z, &ModelError{"evidence checksum mismatch"}
	}
	split := 28 + int(traceLen)
	trace, e := ParseAuditTrace(data[28:split], l.Trace)
	if e != nil {
		return z, evidenceTraceError(e)
	}
	checkpoint, p, steps, e := decodeEvidenceMetadata(data[split:end], l.MaxMetadataBytes)
	if e != nil {
		return z, e
	}
	v := &TraceV2{Trace: *trace, CheckpointAfter: checkpoint, Provenance: p, Steps: steps}
	if e = v.Validate(l); e != nil {
		return z, e
	}
	return Artifact{V2: v}, nil
}

func ReplayTraceV2[S, I, O any](m Model[S, I, O], v *TraceV2, allowBuildMismatch bool, l EvidenceLimits) (*ReplayReport, error) {
	if e := v.Validate(l); e != nil {
		return nil, e
	}
	v = v.Snapshot()
	var mismatch int
	report, e := ReplayWithObservationsBounded(m, &v.Trace, allowBuildMismatch, l.Trace, ReplayObserver[S, I, O]{Turn: func(o ReplayTurnObservation[S, I, O]) error {
		index := int(o.Actual.Sequence) - 1
		if o.Report.Outcome == "exact" && o.Actual.StateCheckCount != nil && uint64(v.Steps[index].StateCheckCount) != uint64(*o.Actual.StateCheckCount) {
			mismatch = index + 1
			return &ModelError{"persisted check phase differs from replay"}
		}
		return nil
	}})
	if mismatch != 0 {
		field := "check_phase"
		report.Outcome = "diverged"
		report.Step = &mismatch
		report.Field = &field
		report.StepsVerified = mismatch - 1
		report.FailureReproduced = false
		return report, nil
	}
	if e != nil {
		if re, ok := e.(*ReplayError[S, I, O]); ok {
			return report, re.Err
		}
		return report, e
	}
	return report, nil
}
func VerifyUpgrade[S, I, O any](m Model[S, I, O], trace *AuditTrace, p Provenance, checkpoint uint64, allowBuildMismatch bool, l EvidenceLimits) (*TraceV2, error) {
	if trace == nil {
		return nil, &ConfigError{"nil upgrade trace"}
	}
	// Pure entry admission does not execute model callbacks. Cache errors so
	// identity and exact-replay precedence remain the Rust ordering.
	header := Trace{Metadata: trace.Metadata}
	artifactError := evidenceTraceError(trace.WriteBinaryWithLimits(io.Discard, l.Trace))
	var owned *AuditTrace
	if artifactError == nil {
		owned = &AuditTrace{Trace: clonePortableTrace(trace.Trace), Config: cloneRunConfig(trace.Config)}
	}
	provenanceError := validateEvidenceProvenance(p, l)
	if provenanceError == nil {
		_, provenanceError = encodeEvidenceProvenance(p, l.MaxMetadataBytes)
	}
	if provenanceError == nil {
		p = cloneProvenance(p)
	}
	steps := []StepEvidence{}
	observer := ReplayObserver[S, I, O]{Turn: func(o ReplayTurnObservation[S, I, O]) error {
		if o.Actual.Sequence > math.MaxUint64-checkpoint {
			return &ModelError{"evidence sequence overflow"}
		}
		if o.Actual.StateCheckCount == nil || uint64(*o.Actual.StateCheckCount) > math.MaxUint32 {
			return &ModelError{"phase check count overflow"}
		}
		steps = append(steps, StepEvidence{Sequence: checkpoint + o.Actual.Sequence, StateCheckCount: uint32(*o.Actual.StateCheckCount)})
		return nil
	}}
	report, e := replayEvidenceCaptured(m, &header, nil, allowBuildMismatch, &l.Trace, observer, func() (*AuditTrace, error) { return owned, artifactError })
	if e != nil {
		if re, ok := e.(*ReplayError[S, I, O]); ok {
			return nil, re.Err
		}
		return nil, e
	}
	if report.Outcome != "exact" {
		return nil, &ModelError{"v1 upgrade requires exact compatible replay"}
	}
	if provenanceError != nil {
		return nil, provenanceError
	}
	v := &TraceV2{Trace: *owned, CheckpointAfter: checkpoint, Provenance: p, Steps: steps}
	if e = v.Validate(l); e != nil {
		return nil, e
	}
	return v, nil
}

func (a Artifact) Snapshot() Artifact {
	if a.V2 != nil && a.V1 == nil {
		return Artifact{V2: a.V2.Snapshot()}
	}
	if a.V1 != nil && a.V2 == nil {
		return Artifact{V1: a.ExactTrace()}
	}
	return Artifact{}
}
func (a Artifact) Write(w io.Writer, l EvidenceLimits) error {
	t, e := a.traceRef()
	if e != nil {
		return e
	}
	if a.V2 != nil {
		return a.V2.Write(w, l)
	}
	return t.WriteBinaryWithLimits(w, l.Trace)
}
func MarshalArtifact(a Artifact, l EvidenceLimits) ([]byte, error) {
	var b bytes.Buffer
	if e := a.Write(&b, l); e != nil {
		return nil, e
	}
	return b.Bytes(), nil
}

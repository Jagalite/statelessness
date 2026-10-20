package statelessness

import (
	"fmt"
	"io"
	"os"
	"unicode/utf8"
)

// ExportPolicy controls nonreplayable diagnostic disclosure. Only exact
// allowlisted property IDs survive; payloads, details and host labels never do.
type ExportPolicy struct {
	MaxSteps, MaxBytes uint64
	PropertyIDs        []string
}

func DefaultExportPolicy() ExportPolicy {
	return ExportPolicy{MaxSteps: 1000, MaxBytes: 1024 * 1024, PropertyIDs: []string{}}
}

type evidenceLimitedWriter struct {
	data    []byte
	maximum uint64
}

func (w *evidenceLimitedWriter) Write(v []byte) (int, error) {
	if uint64(len(v)) > w.maximum-uint64(len(w.data)) {
		return 0, &ModelError{"evidence byte limit"}
	}
	w.data = append(w.data, v...)
	return len(v), nil
}
func evidenceJSONString(w io.Writer, value string) error {
	if !utf8.ValidString(value) {
		return &ModelError{"invalid diagnostic property ID UTF-8"}
	}
	if _, e := io.WriteString(w, "\""); e != nil {
		return e
	}
	for _, ch := range value {
		var value string
		switch ch {
		case '"':
			value = "\\\""
		case '\\':
			value = "\\\\"
		default:
			if ch <= 31 {
				value = fmt.Sprintf("\\u%04x", ch)
			} else {
				value = string(ch)
			}
		}
		if _, e := io.WriteString(w, value); e != nil {
			return e
		}
	}
	_, e := io.WriteString(w, "\"")
	return e
}

// DiagnosticJSON emits statelessness.diagnostic-export/1. This is a diagnostic
// summary, never a redacted replayable trace. Initial checks are always included.
func DiagnosticJSON(artifact Artifact, policy ExportPolicy) ([]byte, error) {
	if (artifact.V1 == nil) == (artifact.V2 == nil) {
		return nil, &ModelError{"artifact must contain exactly one version"}
	}
	trace := artifact.V1
	checkpoint, omitted := "null", "null"
	if artifact.V2 != nil {
		trace = &artifact.V2.Trace
		checkpoint = fmt.Sprint(artifact.V2.CheckpointAfter)
		if artifact.V2.CheckpointAfter == 0 {
			omitted = "false"
		} else {
			omitted = "true"
		}
	}
	termination := map[string]string{"completed": "completed_sequence", "property_failed": "property_failure", "step_limit": "step_limit", "interrupted": "interrupted", "model_error": "model_error"}[trace.Termination]
	if termination == "" {
		return nil, &ModelError{"invalid diagnostic termination"}
	}
	writer := &evidenceLimitedWriter{maximum: policy.MaxBytes}
	_, e := fmt.Fprintf(writer, "{\"schema\":\"statelessness.diagnostic-export/1\",\"replayable\":false,\"redaction_policy\":\"omit-payloads-v1\",\"raw_payloads_included\":false,\"checkpoint_after\":%s,\"omitted_prefix\":%s,\"termination\":\"%s\",\"truncated\":%t,\"observations\":[", checkpoint, omitted, termination, uint64(len(trace.Steps)) > policy.MaxSteps)
	if e != nil {
		return nil, e
	}
	allow := map[string]bool{}
	for _, id := range policy.PropertyIDs {
		allow[id] = true
	}
	for sequence := uint64(0); sequence <= min(uint64(len(trace.Steps)), policy.MaxSteps); sequence++ {
		if sequence != 0 {
			if _, e = io.WriteString(writer, ","); e != nil {
				return nil, e
			}
		}
		if _, e = fmt.Fprintf(writer, "{\"sequence\":%d,\"checks\":[", sequence); e != nil {
			return nil, e
		}
		checks := trace.InitialChecks
		if sequence != 0 {
			checks = trace.Steps[int(sequence-1)].Checks
		}
		for index, c := range checks {
			if index != 0 {
				if _, e = io.WriteString(writer, ","); e != nil {
					return nil, e
				}
			}
			if c.Status != "passed" && c.Status != "failed" && c.Status != "skipped" {
				return nil, &ModelError{"invalid diagnostic check status"}
			}
			phase := "unknown_legacy"
			if p, ok := artifact.CheckPhase(sequence, uint64(index)); ok {
				phase = string(p)
			}
			if _, e = fmt.Fprintf(writer, "{\"status\":\"%s\",\"phase\":\"%s\",\"id\":", c.Status, phase); e != nil {
				return nil, e
			}
			if allow[c.ID] {
				if e = evidenceJSONString(writer, c.ID); e != nil {
					return nil, e
				}
			} else if _, e = io.WriteString(writer, "null"); e != nil {
				return nil, e
			}
			if _, e = io.WriteString(writer, "}"); e != nil {
				return nil, e
			}
		}
		if _, e = io.WriteString(writer, "]}"); e != nil {
			return nil, e
		}
	}
	if _, e = io.WriteString(writer, "]}"); e != nil {
		return nil, e
	}
	return writer.data, nil
}

// ExportDiagnostic is the bounded diagnostic byte API.
func ExportDiagnostic(artifact Artifact, policy ExportPolicy) ([]byte, error) {
	return DiagnosticJSON(artifact, policy)
}
func evidencePrivateNew(path string, data []byte) error {
	file, e := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if e != nil {
		return e
	}
	return evidenceWriteNew(file, data)
}

type evidenceNewFile interface {
	io.Writer
	Sync() error
	Close() error
}

func evidenceWriteNew(file evidenceNewFile, data []byte) error {
	e := auditWriteAll(file, data)
	if e == nil {
		e = file.Sync()
	}
	closeError := file.Close()
	if e == nil {
		e = closeError
	}
	if e != nil {
		return fmt.Errorf("incomplete newly created evidence file: %w", e)
	}
	return nil
}

// ExportDiagnosticNew precomputes bounded bytes, creates exclusively with Unix
// permissions 0600, and syncs the new file. It does not sync its directory.
func ExportDiagnosticNew(artifact Artifact, path string, policy ExportPolicy) error {
	data, e := DiagnosticJSON(artifact, policy)
	if e != nil {
		return e
	}
	return evidencePrivateNew(path, data)
}
func (t *TraceV2) ExportRawNew(path string, limits EvidenceLimits) error {
	writer := &evidenceLimitedWriter{maximum: limits.Trace.MaxTotalBytes}
	if e := t.Write(writer, limits); e != nil {
		return e
	}
	return evidencePrivateNew(path, writer.data)
}
func (a Artifact) ExportRawNew(path string, limits EvidenceLimits) error {
	if (a.V1 == nil) == (a.V2 == nil) {
		return &ModelError{"artifact must contain exactly one version"}
	}
	if a.V2 != nil {
		return a.V2.ExportRawNew(path, limits)
	}
	writer := &evidenceLimitedWriter{maximum: limits.Trace.MaxTotalBytes}
	if e := a.V1.WriteBinaryWithLimits(writer, limits.Trace); e != nil {
		return e
	}
	return evidencePrivateNew(path, writer.data)
}

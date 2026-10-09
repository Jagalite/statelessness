package statelessness

import (
	"encoding/hex"
	"encoding/json"
	"github.com/Jagalite/statelessness/go/internal/portablejson"
	"unicode/utf8"
)

type FormatError = portablejson.Error

// readCheck/readDisposition/readMetadata are shared by the trace decoder and
// qualification adapter. Reader errors are sticky; these do not run model code.
func readCheck(r *portablejson.Reader, v any) Check {
	o := r.Object(v, []string{"id", "status"}, "details")
	c := Check{r.Text(o["id"]), r.Text(o["status"]), r.Text(portablejson.Default(o, "details", ""))}
	if err := c.Validate(); err != nil {
		r.Fail(err.Error())
	}
	return c
}
func readDisposition(r *portablejson.Reader, v any) Disposition {
	o := r.Object(v, []string{"kind"}, "reason")
	d := Disposition{r.Text(o["kind"]), r.Text(portablejson.Default(o, "reason", ""))}
	if err := d.Validate(); err != nil {
		r.Fail(err.Error())
	}
	return d
}
func readMetadata(r *portablejson.Reader, v any) Metadata {
	o := r.Object(v, []string{"name", "model_version", "properties_version", "codec_version", "build"})
	return Metadata{r.Text(o["name"]), uint32(r.Integer(o["model_version"], 0xffffffff)), uint32(r.Integer(o["properties_version"], 0xffffffff)), uint32(r.Integer(o["codec_version"], 0xffffffff)), r.Text(o["build"])}
}

// TraceData returns the observation envelope, not a Rust .sttrace audit conversion.
func TraceData(t *Trace) map[string]any {
	steps := make([]any, len(t.Steps))
	emptyChecks := func(c []Check) []Check {
		if c == nil {
			return []Check{}
		}
		return c
	}
	for i, s := range t.Steps {
		outputs := make([]string, len(s.Outputs))
		for j, o := range s.Outputs {
			outputs[j] = hex.EncodeToString(o)
		}
		steps[i] = map[string]any{"input": hex.EncodeToString(s.Input), "disposition": s.Disposition, "outputs": outputs, "post_state": hex.EncodeToString(s.PostState), "checks": emptyChecks(s.Checks)}
	}
	return map[string]any{"format": "stateless.trace-json", "version": 1, "metadata": t.Metadata, "initial_state": hex.EncodeToString(t.InitialState), "initial_checks": emptyChecks(t.InitialChecks), "steps": steps, "termination": t.Termination, "error": t.Error}
}
func TraceFromData(value any, limits TraceLimits) (*Trace, error) {
	if err := limits.validate(); err != nil {
		return nil, err
	}
	r := &portablejson.Reader{}
	o := r.Object(value, []string{"format", "version", "metadata", "initial_state", "initial_checks", "steps", "termination", "error"})
	if r.Text(o["format"]) != "stateless.trace-json" || r.Integer(o["version"], (1<<53)-1) != 1 {
		r.Fail("unsupported trace format")
	}
	items, payload := 0, 0
	count := func(n int) {
		items += n
		if items > limits.MaxItems {
			r.Fail("aggregate trace item limit")
		}
	}
	checks := func(v any) []Check {
		a := r.Array(v, limits.MaxItems)
		count(len(a))
		result := make([]Check, len(a))
		for i, x := range a {
			result[i] = readCheck(r, x)
		}
		return result
	}
	blob := func(v any) []byte {
		s := r.Text(v)
		if len(s) > limits.MaxBlobBytes*2 || len(s)%2 != 0 {
			r.Fail("hexadecimal byte limit")
			return nil
		}
		for _, c := range s {
			if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
				r.Fail("invalid lowercase hexadecimal")
				return nil
			}
		}
		payload += len(s) / 2
		if payload > limits.MaxPayloadBytes {
			r.Fail("aggregate payload limit")
			return nil
		}
		b, err := hex.DecodeString(s)
		if err != nil {
			r.Fail(err.Error())
		}
		return b
	}
	t := &Trace{Metadata: readMetadata(r, o["metadata"]), InitialState: blob(o["initial_state"]), InitialChecks: checks(o["initial_checks"]), Steps: []TraceStep{}, Termination: r.Text(o["termination"]), Error: r.Text(o["error"])}
	for _, v := range r.Array(o["steps"], limits.MaxSteps) {
		s := r.Object(v, []string{"input", "disposition", "outputs", "post_state", "checks"})
		outputs := r.Array(s["outputs"], limits.MaxItems)
		count(1 + len(outputs))
		out := make([][]byte, len(outputs))
		for i, x := range outputs {
			out[i] = blob(x)
		}
		t.Steps = append(t.Steps, TraceStep{blob(s["input"]), readDisposition(r, s["disposition"]), out, blob(s["post_state"]), checks(s["checks"])})
	}
	if !validTermination(t.Termination) || t.Error != "" && t.Termination != "model_error" {
		r.Fail("invalid trace termination")
	}
	if r.Err != nil {
		return nil, r.Err
	}
	return t, nil
}
func ParseTrace(data []byte) (*Trace, error) {
	limits := DefaultTraceLimits()
	value, err := portablejson.Strict(data, limits.MaxJSONBytes)
	if err != nil {
		return nil, err
	}
	return TraceFromData(value, limits)
}
func MarshalTrace(t *Trace) ([]byte, error) {
	if t == nil {
		return nil, &FormatError{Message: "nil trace"}
	}
	// encoding/json substitutes invalid Go UTF-8. Reject it before serialization
	// rather than silently changing persisted identity/check observations.
	if err := t.Metadata.Validate(); err != nil {
		return nil, &FormatError{Message: err.Error()}
	}
	if !utf8.ValidString(t.Error) {
		return nil, &FormatError{Message: "invalid trace error UTF-8"}
	}
	for _, c := range t.InitialChecks {
		if err := c.Validate(); err != nil {
			return nil, &FormatError{Message: err.Error()}
		}
	}
	for _, step := range t.Steps {
		if err := step.Disposition.Validate(); err != nil {
			return nil, &FormatError{Message: err.Error()}
		}
		for _, c := range step.Checks {
			if err := c.Validate(); err != nil {
				return nil, &FormatError{Message: err.Error()}
			}
		}
	}
	data, err := json.Marshal(TraceData(t))
	if err != nil {
		return nil, err
	}
	if _, err = ParseTrace(data); err != nil {
		return nil, err
	}
	return data, nil
}

// CheckFromData validates a parsed portable JSON check.
func CheckFromData(value any) (Check, error) {
	r := &portablejson.Reader{}
	c := readCheck(r, value)
	return c, r.Err
}

// DispositionFromData validates a parsed portable JSON disposition.
func DispositionFromData(value any) (Disposition, error) {
	r := &portablejson.Reader{}
	d := readDisposition(r, value)
	return d, r.Err
}

// MetadataFromData validates parsed metadata without floating-point conversions.
func MetadataFromData(value any) (Metadata, error) {
	r := &portablejson.Reader{}
	m := readMetadata(r, value)
	return m, r.Err
}

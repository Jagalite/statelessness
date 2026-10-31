// This installed consumer uses only the public Go package. Fixture expectations
// are read exclusively by self-test, never used to construct observed results.
package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"reflect"
	"strconv"
	"strings"

	s "github.com/Jagalite/statelessness/go"
)

type fixture struct {
	Schema string        `json:"schema"`
	Cases  []fixtureCase `json:"cases"`
}
type fixtureCase struct {
	ID      string `json:"id"`
	Stream  string `json:"stream"`
	Binding string `json:"binding"`
	Options struct {
		Level          string          `json:"level"`
		Snapshots      json.RawMessage `json:"snapshots"`
		MaxRecordBytes *uint64         `json:"max_record_bytes"`
	} `json:"options"`
	Payloads struct {
		Kind         string  `json:"kind"`
		Text         string  `json:"text"`
		MaxBlobBytes *uint64 `json:"max_blob_bytes"`
	} `json:"payloads"`
	Sink struct {
		FailAfterBytes *uint64 `json:"fail_after_bytes"`
		FailFlush      bool    `json:"fail_flush"`
	} `json:"sink"`
	Events   []fixtureEvent `json:"events"`
	Expected resultFields   `json:"expected"`
}
type fixtureEvent struct {
	Kind        string  `json:"kind"`
	Sequence    *string `json:"sequence"`
	Before      byte    `json:"before"`
	Input       byte    `json:"input"`
	State       byte    `json:"state"`
	Outputs     []byte  `json:"outputs"`
	Disposition struct {
		Kind   string `json:"kind"`
		Reason string `json:"reason"`
	} `json:"disposition"`
	Checks []struct {
		ID      string `json:"id"`
		Status  string `json:"status"`
		Message string `json:"message"`
	} `json:"checks"`
	Level       string     `json:"level"`
	Message     string     `json:"message"`
	Transitions string     `json:"transitions"`
	Reason      string     `json:"reason"`
	Metadata    s.Metadata `json:"metadata"`
	Config      struct {
		Strategy   string      `json:"strategy"`
		Seed       *string     `json:"seed"`
		Parameters [][2]string `json:"parameters"`
	} `json:"config"`
	Snapshots json.RawMessage `json:"snapshots"`
	Fault     string          `json:"fault"`
}
type resultFields struct {
	RecordHex    string   `json:"record_hex"`
	Callbacks    []string `json:"callbacks"`
	ErrorStage   *string  `json:"error_stage"`
	SinkPoisoned bool     `json:"sink_poisoned"`
}
type caseResult struct {
	ID string `json:"id"`
	resultFields
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}
func u64(v string) uint64 { n, err := strconv.ParseUint(v, 10, 64); must(err); return n }
func optionalU64(v *string) *uint64 {
	if v == nil {
		return nil
	}
	n := u64(*v)
	return &n
}
func level(v string) s.LogLevel {
	switch v {
	case "", "Off":
		return s.LogOff
	case "Error":
		return s.LogError
	case "Warn":
		return s.LogWarn
	case "Info":
		return s.LogInfo
	case "Debug":
		return s.LogDebug
	case "Trace":
		return s.LogTrace
	default:
		panic("invalid fixture level")
	}
}
func snapshots(raw json.RawMessage) s.SnapshotPolicy {
	if len(raw) == 0 {
		return s.SnapshotNone()
	}
	var name string
	if json.Unmarshal(raw, &name) == nil {
		switch name {
		case "None":
			return s.SnapshotNone()
		case "EveryTransition":
			return s.SnapshotEveryTransition()
		default:
			panic("invalid fixture snapshots")
		}
	}
	var periodic struct {
		Every string `json:"every"`
	}
	must(json.Unmarshal(raw, &periodic))
	p, err := s.SnapshotEvery(u64(periodic.Every))
	must(err)
	return p
}

type fixtureWriter struct {
	data            bytes.Buffer
	failAfter       *uint64
	failFlush       bool
	writes, flushes int
}

func (w *fixtureWriter) Write(p []byte) (int, error) {
	w.writes++
	if w.failAfter != nil {
		used := uint64(w.data.Len())
		if used >= *w.failAfter {
			return 0, errors.New("fixture write failed")
		}
		remaining := *w.failAfter - used
		if uint64(len(p)) > remaining {
			n, _ := w.data.Write(p[:int(remaining)])
			return n, errors.New("fixture write failed")
		}
	}
	return w.data.Write(p)
}
func (w *fixtureWriter) Flush() error {
	w.flushes++
	if w.failFlush {
		return errors.New("fixture flush failed")
	}
	return nil
}

type instrumentation struct {
	callbacks []string
	fault     string
	forbidden int
}

func (c *instrumentation) encode(kind string) func(byte, *s.EncodeBuffer) error {
	return func(v byte, b *s.EncodeBuffer) error {
		stage := "encode_" + kind
		c.callbacks = append(c.callbacks, stage+":"+strconv.Itoa(int(v)))
		if c.fault == stage {
			return errors.New("fixture codec failed")
		}
		return b.Append([]byte{v})
	}
}
func declaredCodec(kind string, c *instrumentation) s.ValueCodec[byte] {
	leaf, err := s.NewValueCodec(kind, c.encode(kind), func(*s.Decoder) (byte, error) {
		c.forbidden++
		return 0, errors.New("unexpected decoder")
	}, nil)
	must(err)
	f, err := s.OrdinaryField("value", leaf)
	must(err)
	declared, err := s.OrderedRecord(kind, s.PrependRecordField(f, s.NoRecordFields()),
		func(v byte) (s.FieldProduct[byte, s.Unit], error) { return s.FieldProduct[byte, s.Unit]{Head: v}, nil },
		func(v s.FieldProduct[byte, s.Unit]) (byte, error) { return v.Head, nil }, s.RecordOptions{})
	must(err)
	return declared
}
func application(c *instrumentation, binding string) s.Model[byte, byte, byte] {
	metadata := func() (s.Metadata, error) { return s.Identity("observation", "fixture"), nil }
	m := s.Model[byte, byte, byte]{CloneState: s.ValueCopy[byte], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[byte],
		InitialState: func() (byte, error) { c.forbidden++; return 0, errors.New("unexpected initial") },
		Step: func(byte, byte) (s.Transition[byte, byte], error) {
			c.forbidden++
			return s.Transition[byte, byte]{}, errors.New("unexpected reducer")
		},
		CheckState: func(byte) ([]s.Check, error) { c.forbidden++; return nil, errors.New("unexpected check") },
		CheckTransition: func(byte, byte, s.Transition[byte, byte]) ([]s.Check, error) {
			c.forbidden++
			return nil, errors.New("unexpected check")
		},
	}
	if binding == "declaration" {
		codec, err := s.BindModelCodec(metadata, declaredCodec("state", c), declaredCodec("input", c), declaredCodec("output", c), s.DefaultModelCodecOptions())
		must(err)
		m.Codec = codec
	} else {
		if binding != "" {
			panic("unknown fixture binding")
		}
		legacy := func(byte) ([]byte, error) { c.forbidden++; return nil, errors.New("unexpected legacy codec") }
		decode := func([]byte) (byte, error) { c.forbidden++; return 0, errors.New("unexpected decoder") }
		m.Codec = &s.Codec[byte, byte, byte]{Metadata: metadata, EncodeState: legacy, EncodeInput: legacy, EncodeOutput: legacy,
			DecodeState: decode, DecodeInput: decode,
			EncodeStateInto: c.encode("state"), EncodeInputInto: c.encode("input"), EncodeOutputInto: c.encode("output")}
	}
	return m
}

type customPayloads struct {
	c    *instrumentation
	text string
}

func (p customPayloads) format(kind string, v byte, out io.Writer) error {
	stage := "format_" + kind
	p.c.callbacks = append(p.c.callbacks, stage+":"+strconv.Itoa(int(v)))
	if p.c.fault == stage {
		return errors.New("fixture formatter failed")
	}
	if p.c.fault == "swallow_input" && kind == "input" {
		_, _ = out.Write(bytes.Repeat([]byte{'x'}, 1024))
		return nil
	}
	_, err := io.WriteString(out, p.text)
	return err
}
func (p customPayloads) State(_ s.Model[byte, byte, byte], v byte, w io.Writer) error {
	return p.format("state", v, w)
}
func (p customPayloads) Input(_ s.Model[byte, byte, byte], v byte, w io.Writer) error {
	return p.format("input", v, w)
}
func (p customPayloads) Output(_ s.Model[byte, byte, byte], v byte, w io.Writer) error {
	return p.format("output", v, w)
}

func event(f fixtureEvent) s.Event[byte, byte, byte] {
	e := s.Event[byte, byte, byte]{}
	switch f.Kind {
	case "transition", "checkpoint":
		if f.Sequence == nil {
			panic("missing sequence")
		}
		e.Sequence, e.State = u64(*f.Sequence), f.State
		for _, check := range f.Checks {
			switch check.Status {
			case "Passed":
				e.Checks = append(e.Checks, s.Passed(check.ID))
			case "Failed":
				e.Checks = append(e.Checks, s.Failed(check.ID, check.Message))
			case "Skipped":
				e.Checks = append(e.Checks, s.Skipped(check.ID, check.Message))
			default:
				panic("invalid fixture check")
			}
		}
		if f.Kind == "checkpoint" {
			e.Kind = s.EventCheckpoint
			return e
		}
		if e.Sequence == 0 {
			panic("zero transition sequence")
		}
		e.Kind, e.Before, e.Input = s.EventTransition, f.Before, f.Input
		e.Transition = s.Transition[byte, byte]{State: f.State, Outputs: f.Outputs}
		switch f.Disposition.Kind {
		case "Accepted":
			e.Transition.Disposition = s.Accepted()
		case "Rejected":
			e.Transition.Disposition = s.Rejected(f.Disposition.Reason)
		case "Ignored":
			e.Transition.Disposition = s.Ignored(f.Disposition.Reason)
		default:
			panic("invalid fixture disposition")
		}
	case "message":
		e.Kind, e.Level, e.MessageSequence, e.Message = s.EventMessage, level(f.Level), optionalU64(f.Sequence), f.Message
	case "finished":
		e.Kind, e.Transitions, e.Reason = s.EventFinished, u64(f.Transitions), f.Reason
	case "started":
		e.Kind, e.Metadata = s.EventStarted, f.Metadata
		e.Config = s.RunConfig{Strategy: f.Config.Strategy, Seed: optionalU64(f.Config.Seed), Parameters: f.Config.Parameters}
	default:
		panic("invalid fixture event")
	}
	return e
}

func errorStage(err error) string {
	e, ok := err.(*s.TextObserverError)
	if !ok || e == nil {
		panic(err)
	}
	if e.Cause == s.ErrObservationRecordLimit {
		return "record_limit"
	}
	switch e.Stage {
	case "encode input", "encode output", "encode state", "format input", "format output", "format state":
		return strings.ReplaceAll(e.Stage, " ", "_")
	case "write":
		return "sink_write"
	case "flush":
		return "sink_flush"
	case "poisoned":
		return "sink_poisoned"
	default:
		panic("unexpected observation error stage: " + e.Stage)
	}
}

func observe(f fixtureCase) caseResult {
	c := &instrumentation{callbacks: []string{}}
	m := application(c, f.Binding)
	w := &fixtureWriter{failAfter: f.Sink.FailAfterBytes, failFlush: f.Sink.FailFlush}
	opts := s.DefaultObservationOptions()
	opts.Level, opts.Snapshots = level(f.Options.Level), snapshots(f.Options.Snapshots)
	if f.Options.MaxRecordBytes != nil {
		opts.MaxRecordBytes = *f.Options.MaxRecordBytes
	}
	var payloads s.PayloadFormat[byte, byte, byte]
	switch f.Payloads.Kind {
	case "none":
		payloads = s.NoPayloads[byte, byte, byte]{}
	case "custom":
		payloads = customPayloads{c, f.Payloads.Text}
	case "encoded":
		maximum := uint64(16)
		if f.Payloads.MaxBlobBytes != nil {
			maximum = *f.Payloads.MaxBlobBytes
		}
		payloads = s.NewEncodedPayloads[byte, byte, byte](maximum)
	default:
		panic("invalid fixture payload kind")
	}
	o, err := s.NewTextObserver(w, f.Stream, opts, payloads)
	must(err)
	var firstError *string
	for _, f := range f.Events {
		c.fault = f.Fault
		switch f.Kind {
		case "set_level":
			err = o.SetLevel(level(f.Level))
		case "set_snapshots":
			err = o.SetSnapshots(snapshots(f.Snapshots))
		case "flush":
			err = o.Flush()
		default:
			err = o.Observe(m, event(f))
		}
		if err != nil && firstError == nil {
			stage := errorStage(err)
			firstError = &stage
		}
	}
	if c.forbidden != 0 {
		panic("observation invoked forbidden model work")
	}
	beforeWrites, beforeFlushes, beforeCallbacks := w.writes, w.flushes, len(c.callbacks)
	must(o.SetLevel(s.LogOff))
	err = o.Observe(m, s.Event[byte, byte, byte]{Kind: s.EventMessage, Level: s.LogOff, Message: "health probe"})
	poisoned := err != nil
	if poisoned && errorStage(err) != "sink_poisoned" {
		panic("unexpected health probe error")
	}
	if o.IsPoisoned() != poisoned || w.writes != beforeWrites || w.flushes != beforeFlushes || len(c.callbacks) != beforeCallbacks {
		panic("health probe performed work")
	}
	writer, err := o.IntoInner()
	must(err)
	if writer != w || w.writes != beforeWrites || w.flushes != beforeFlushes || len(c.callbacks) != beforeCallbacks || c.forbidden != 0 {
		panic("health probe or ownership extraction performed work")
	}
	return caseResult{ID: f.ID, resultFields: resultFields{RecordHex: hex.EncodeToString(w.data.Bytes()), Callbacks: c.callbacks, ErrorStage: firstError, SinkPoisoned: poisoned}}
}

func main() {
	var initial s.DebugObservation[int, int, string] = s.DebugInitial[int, int, string]{State: 7, Checks: []s.Check{s.Passed("state")}}
	var turn s.DebugObservation[int, int, string] = s.DebugTurn[int, int, string]{Observation: s.TurnObservation[int, int, string]{Sequence: ^uint64(0), StateCheckCount: 1}}
	if initial.(s.DebugInitial[int, int, string]).State != 7 || turn.(s.DebugTurn[int, int, string]).Observation.StateCheckCount != 1 {
		panic("debug variants lost payload")
	}

	if len(os.Args) != 3 || (os.Args[1] != "observe" && os.Args[1] != "self-test") {
		fmt.Fprintln(os.Stderr, "usage: native-observation observe|self-test fixturePath")
		os.Exit(2)
	}
	data, err := os.ReadFile(os.Args[2])
	must(err)
	var f fixture
	must(json.Unmarshal(data, &f))
	if f.Schema != "statelessness.observation.v1" || len(f.Cases) == 0 {
		panic("invalid or empty observation fixture")
	}
	results := make([]caseResult, 0, len(f.Cases))
	seen := map[string]bool{}
	for _, c := range f.Cases {
		if c.ID == "" || seen[c.ID] {
			panic("empty or duplicate case ID")
		}
		seen[c.ID] = true
		actual := observe(c)
		if os.Args[1] == "self-test" && !reflect.DeepEqual(actual.resultFields, c.Expected) {
			fmt.Fprintf(os.Stderr, "observation case %s differs from source-derived fixture\n", c.ID)
			os.Exit(1)
		}
		results = append(results, actual)
	}
	must(json.NewEncoder(os.Stdout).Encode(struct {
		Schema   string       `json:"schema"`
		Language string       `json:"language"`
		Cases    []caseResult `json:"cases"`
	}{"statelessness.observation-results.v1", "go", results}))
}

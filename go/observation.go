package statelessness

import (
	"errors"
	"io"
	"strconv"
	"unicode"
	"unicode/utf8"
)

// LogLevel is increasing verbosity. Rejected and ignored inputs are Debug,
// unless their supplied checks contain a failure (Error) or a skip (Warn).
type LogLevel uint8

const (
	LogOff LogLevel = iota
	LogError
	LogWarn
	LogInfo
	LogDebug
	LogTrace
)

func (l LogLevel) String() string {
	switch l {
	case LogOff:
		return "Off"
	case LogError:
		return "Error"
	case LogWarn:
		return "Warn"
	case LogInfo:
		return "Info"
	case LogDebug:
		return "Debug"
	case LogTrace:
		return "Trace"
	default:
		return "Invalid"
	}
}

// SnapshotPolicy's zero value is None. Constructors preserve a positive u64
// period; the policy only selects payloads when verbosity is exactly Trace.
type SnapshotPolicy struct {
	kind  uint8
	every uint64
}

func SnapshotNone() SnapshotPolicy            { return SnapshotPolicy{} }
func SnapshotEveryTransition() SnapshotPolicy { return SnapshotPolicy{kind: 2} }
func SnapshotEvery(n uint64) (SnapshotPolicy, error) {
	if n == 0 {
		return SnapshotPolicy{}, &ConfigError{"snapshot period must be positive"}
	}
	return SnapshotPolicy{kind: 1, every: n}, nil
}
func (p SnapshotPolicy) includes(sequence uint64) bool {
	return p.kind == 2 || (p.kind == 1 && sequence%p.every == 0)
}

type ObservationOptions struct {
	Level     LogLevel
	Snapshots SnapshotPolicy
	// Inclusive UTF-8 byte bound, including the final newline. Zero is a real
	// zero-byte limit, not a request for defaults. Callback allocations are outside it.
	MaxRecordBytes uint64
}

func DefaultObservationOptions() ObservationOptions {
	return ObservationOptions{Level: LogOff, Snapshots: SnapshotNone(), MaxRecordBytes: 1024 * 1024}
}

type EventKind uint8

const (
	EventStarted EventKind = iota + 1
	EventCheckpoint
	EventTransition
	EventMessage
	EventFinished
)

// Event is an application-owned, synchronous diagnostic view. Only fields for
// Kind are used. No cloning, checking, reduction, effect execution, continuity
// validation, or checked authority is implied. Do not mutate borrowed payloads,
// output/check slices, config parameters or pointer fields during Observe.
//
// Started uses Metadata/Config; Checkpoint uses Sequence/State/Checks;
// Transition uses positive Sequence/Before/Input/Transition/Checks; Message uses
// Level/MessageSequence/Message (nil sequence means None); Finished uses
// Transitions/Reason. Before is deliberately never inspected or formatted.
type Event[S, I, O any] struct {
	Kind            EventKind
	Metadata        Metadata
	Config          RunConfig
	Sequence        uint64
	State           S
	Before          S
	Input           I
	Transition      Transition[S, O]
	Checks          []Check
	Level           LogLevel
	MessageSequence *uint64
	Message         string
	Transitions     uint64
	Reason          string
}

// Event converts detached turn diagnostics into an event, not a checked token.
func (t TurnObservation[S, I, O]) Event() Event[S, I, O] {
	return Event[S, I, O]{Kind: EventTransition, Sequence: t.Sequence, Before: t.Before,
		Input: t.Input, Transition: t.Transition, Checks: t.Checks}
}

type Observer[S, I, O any] interface {
	Observe(Model[S, I, O], Event[S, I, O]) error
}

// PayloadFormat works without a codec. Callbacks borrow application values and
// a revocable writer for the call only. They must preserve single-line text and
// must not mutate values or run effects. Implementations need not implement fmt.
type PayloadFormat[S, I, O any] interface {
	State(Model[S, I, O], S, io.Writer) error
	Input(Model[S, I, O], I, io.Writer) error
	Output(Model[S, I, O], O, io.Writer) error
}

type NoPayloads[S, I, O any] struct{}

func (NoPayloads[S, I, O]) State(_ Model[S, I, O], _ S, out io.Writer) error {
	return observationWriteAll(out, []byte("unavailable"))
}
func (NoPayloads[S, I, O]) Input(_ Model[S, I, O], _ I, out io.Writer) error {
	return observationWriteAll(out, []byte("unavailable"))
}
func (NoPayloads[S, I, O]) Output(_ Model[S, I, O], _ O, out io.Writer) error {
	return observationWriteAll(out, []byte("unavailable"))
}

// TextObserverError attributes a failure without invoking arbitrary host Error,
// String, Format, Is or As methods. Inspect Cause/Unwrap explicitly if desired.
type TextObserverError struct {
	Stage string
	Cause error
}

func (e *TextObserverError) Error() string {
	if e == nil {
		return "observation failed"
	}
	return "observation " + e.Stage + " failed"
}
func (e *TextObserverError) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.Cause
}

// Only errors minted for this active payload lease carry internal codec
// attribution. Public errors, errors from other observers or earlier payloads,
// and mismatched codec stages remain opaque formatter causes.
type observationEncodingFailure struct {
	lease *observationRecordLease
	stage string
	cause error
}

func (e *observationEncodingFailure) Error() string { return "observation " + e.stage + " failed" }
func (e *observationEncodingFailure) Unwrap() error { return e.cause }

func observationEncodeError(stage string, out io.Writer, cause error) error {
	if lease, ok := out.(*observationRecordLease); ok && lease.record != nil && lease.encodingStage == stage {
		return &observationEncodingFailure{lease: lease, stage: stage, cause: cause}
	}
	return &TextObserverError{stage, cause}
}

var ErrObservationPoisoned = errors.New("observation sink failed; output may be incomplete")
var ErrObservationRecordLimit = errors.New("observation record exceeds byte limit")
var errObservationBusy = errors.New("observation operation already in progress")
var errObservationTransferred = errors.New("observation writer ownership transferred")

// EncodedPayloads reuses one bounded buffer and the existing model encoding
// dispatch (incremental before legacy, including BindModelCodec declarations).
// Legacy return buffers and clone/callback allocations precede the byte limit.
// It is synchronous and must not be shared concurrently between observers.
// Do not copy an EncodedPayloads after first use.
type EncodedPayloads[S, I, O any] struct {
	bytes   []byte
	maximum uint64
	busy    bool
}

func NewEncodedPayloads[S, I, O any](maxBlobBytes uint64) *EncodedPayloads[S, I, O] {
	return &EncodedPayloads[S, I, O]{maximum: maxBlobBytes}
}

func (p *EncodedPayloads[S, I, O]) encode(stage string, out io.Writer, encode func(*EncodeBuffer) error) error {
	if p == nil {
		return observationEncodeError(stage, out, &ConfigError{"nil encoded payloads"})
	}
	if p.busy {
		return observationEncodeError(stage, out, errObservationBusy)
	}
	p.busy = true
	defer func() { p.busy = false }()
	p.bytes = p.bytes[:0]
	batch := &encodeBatch{bytes: &p.bytes, maximum: p.maximum, preserveErrors: true}
	if err := invokeEncodeBatch(stage, batch, encode); err != nil {
		return observationEncodeError(stage, out, err)
	}
	if err := observationWriteAll(out, []byte("hex:")); err != nil {
		return err
	}
	const digits = "0123456789abcdef"
	var pair [2]byte
	for _, b := range p.bytes {
		pair[0], pair[1] = digits[b>>4], digits[b&15]
		if err := observationWriteAll(out, pair[:]); err != nil {
			return err
		}
	}
	return nil
}

func (p *EncodedPayloads[S, I, O]) State(m Model[S, I, O], v S, out io.Writer) error {
	return p.encode("encode state", out, func(b *EncodeBuffer) error { return m.AppendStateEncoding(v, b) })
}
func (p *EncodedPayloads[S, I, O]) Input(m Model[S, I, O], v I, out io.Writer) error {
	return p.encode("encode input", out, func(b *EncodeBuffer) error { return m.AppendInputEncoding(v, b) })
}
func (p *EncodedPayloads[S, I, O]) Output(m Model[S, I, O], v O, out io.Writer) error {
	return p.encode("encode output", out, func(b *EncodeBuffer) error { return m.AppendOutputEncoding(v, b) })
}

// TextObserver stages a complete bounded record before touching its writer.
// Format/encode errors recover on the next event; write/flush errors permanently
// poison it, including while Off. It is synchronous, exclusive and not safe for
// concurrent use. Nested mutation from callbacks is rejected.
// Do not copy a TextObserver after first use; retain and share its pointer only
// under the caller's exclusive ownership discipline.
type TextObserver[S, I, O any] struct {
	writer      io.Writer
	payloads    PayloadFormat[S, I, O]
	stream      string
	options     ObservationOptions
	record      []byte
	failed      bool
	busy        bool
	transferred bool
}

func NewTextObserver[S, I, O any](writer io.Writer, streamID string, options ObservationOptions, payloads PayloadFormat[S, I, O]) (*TextObserver[S, I, O], error) {
	if writer == nil || !utf8.ValidString(streamID) || options.Level > LogTrace {
		return nil, &ConfigError{"observation requires writer, UTF-8 stream ID and valid level"}
	}
	if payloads == nil {
		payloads = NoPayloads[S, I, O]{}
	}
	return &TextObserver[S, I, O]{writer: writer, stream: streamID, options: options, payloads: payloads}, nil
}

func (o *TextObserver[S, I, O]) SetLevel(level LogLevel) error {
	if o.busy {
		return errObservationBusy
	}
	if level > LogTrace {
		return &ConfigError{"invalid observation level"}
	}
	o.options.Level = level
	return nil
}
func (o *TextObserver[S, I, O]) SetSnapshots(policy SnapshotPolicy) error {
	if o.busy {
		return errObservationBusy
	}
	o.options.Snapshots = policy
	return nil
}
func (o *TextObserver[S, I, O]) GetRef() io.Writer { return o.writer }
func (o *TextObserver[S, I, O]) IsPoisoned() bool  { return o.failed }

// IntoInner transfers the writer, including after failure. It never flushes.
// The observer cannot be used afterwards. A callback cannot take ownership.
func (o *TextObserver[S, I, O]) IntoInner() (io.Writer, error) {
	if o.busy {
		return nil, errObservationBusy
	}
	if o.transferred {
		return nil, errObservationTransferred
	}
	w := o.writer
	o.writer, o.transferred = nil, true
	return w, nil
}
func (o *TextObserver[S, I, O]) healthy() error {
	if o.failed {
		return &TextObserverError{"poisoned", ErrObservationPoisoned}
	}
	if o.transferred {
		return errObservationTransferred
	}
	if o.busy {
		return errObservationBusy
	}
	if o.writer == nil {
		return &ConfigError{"uninitialized text observer"}
	}
	return nil
}
func (o *TextObserver[S, I, O]) Flush() error {
	if err := o.healthy(); err != nil {
		return err
	}
	o.busy = true
	defer func() { o.busy = false }()
	if f, ok := o.writer.(interface{ Flush() error }); ok {
		err := observationCallback(func() error { return f.Flush() })
		if err != nil {
			o.failed = true
			return &TextObserverError{"flush", err}
		}
	}
	return nil
}

// Go io.Writer requires an error for n < len(p). A short nil-error write is
// io.ErrShortWrite; partial bytes plus an error are never retried or duplicated.
func observationWriteAll(w io.Writer, p []byte) error {
	n, err := w.Write(p)
	if n < 0 || n > len(p) {
		return errors.New("invalid observation writer count")
	}
	if err != nil {
		return err
	}
	if n != len(p) {
		return io.ErrShortWrite
	}
	return nil
}

// Contain callback panics without formatting the panic value or returned error.
func observationCallback(f func() error) (err error) {
	completed := false
	defer func() {
		if !completed {
			_ = recover()
			err = errors.New("observation callback panicked")
		}
	}()
	err = f()
	completed = true
	return err
}

func observationCheckLevel(checks []Check, ordinary LogLevel) LogLevel {
	if hasFailure(checks) {
		return LogError
	}
	for _, c := range checks {
		if c.Status == "skipped" {
			return LogWarn
		}
	}
	return ordinary
}

func (o *TextObserver[S, I, O]) Observe(m Model[S, I, O], event Event[S, I, O]) error {
	if err := o.healthy(); err != nil {
		return err
	}
	if o.options.Level == LogOff {
		return nil
	}
	var level LogLevel
	switch event.Kind {
	case EventStarted, EventFinished:
		level = LogInfo
	case EventCheckpoint:
		level = observationCheckLevel(event.Checks, LogTrace)
	case EventTransition:
		level = observationCheckLevel(event.Checks, LogDebug)
	case EventMessage:
		level = event.Level
	default:
		return &ConfigError{"invalid observation event kind"}
	}
	if level > LogTrace {
		return &ConfigError{"invalid message level"}
	}
	if level == LogOff || level > o.options.Level {
		return nil
	}
	o.busy = true
	defer func() { o.busy = false }()
	o.record = o.record[:0]
	r := &observationRecord{bytes: &o.record, maximum: o.options.MaxRecordBytes}
	if err := o.format(m, event, level, r); err != nil {
		return err
	}
	r.text("\n")
	if r.failure != nil {
		return &TextObserverError{"format", r.failure}
	}
	err := observationCallback(func() error { return observationWriteAll(o.writer, o.record) })
	if err != nil {
		o.failed = true
		return &TextObserverError{"write", err}
	}
	return nil
}

func (o *TextObserver[S, I, O]) payload(r *observationRecord, kind string, f func(io.Writer) error) *TextObserverError {
	if r.failure != nil {
		return &TextObserverError{"format", r.failure}
	}
	lease := &observationRecordLease{record: r, encodingStage: "encode " + kind}
	defer func() { lease.record = nil }()
	err := observationCallback(func() error { return f(lease) })
	// A swallowed record write failure outranks any returned error or panic.
	if r.failure != nil {
		return &TextObserverError{"format", r.failure}
	}
	if err != nil {
		if encoded, ok := err.(*observationEncodingFailure); ok && encoded != nil && encoded.lease == lease {
			return &TextObserverError{encoded.stage, encoded.cause}
		}
		return &TextObserverError{"format " + kind, err}
	}
	return nil
}

func (o *TextObserver[S, I, O]) format(m Model[S, I, O], e Event[S, I, O], level LogLevel, r *observationRecord) *TextObserverError {
	r.text(level.String())
	r.text(" stream=")
	r.quoted(o.stream)
	switch e.Kind {
	case EventStarted:
		r.text(" event=started metadata=ModelMetadata { name: ")
		r.quoted(e.Metadata.Name)
		r.text(", model_version: ")
		r.number(uint64(e.Metadata.ModelVersion))
		r.text(", properties_version: ")
		r.number(uint64(e.Metadata.PropertiesVersion))
		r.text(", codec_version: ")
		r.number(uint64(e.Metadata.CodecVersion))
		r.text(", build: ")
		r.quoted(e.Metadata.Build)
		r.text(" } config=RunConfig { strategy: ")
		r.quoted(e.Config.Strategy)
		r.text(", seed: ")
		r.optional(e.Config.Seed)
		r.text(", parameters: [")
		for i, pair := range e.Config.Parameters {
			if r.failure != nil {
				break
			}
			if i != 0 {
				r.text(", ")
			}
			r.text("(")
			r.quoted(pair[0])
			r.text(", ")
			r.quoted(pair[1])
			r.text(")")
		}
		r.text("] }")
	case EventFinished:
		r.text(" event=finished transitions=")
		r.number(e.Transitions)
		r.text(" reason=")
		r.quoted(e.Reason)
	case EventMessage:
		r.text(" event=message sequence=")
		r.optional(e.MessageSequence)
		r.text(" message=")
		r.quoted(e.Message)
	case EventCheckpoint:
		r.text(" event=checkpoint sequence=")
		r.number(e.Sequence)
		r.checks(e.Checks, o.options.Level)
		if o.options.Level == LogTrace && o.options.Snapshots.kind != 0 {
			r.text(" state=")
			if err := o.payload(r, "state", func(w io.Writer) error { return o.payloads.State(m, e.State, w) }); err != nil {
				return err
			}
		}
	case EventTransition:
		if e.Sequence == 0 {
			return &TextObserverError{"format", &ConfigError{"transition sequence must be positive"}}
		}
		r.text(" event=transition sequence=")
		r.number(e.Sequence)
		r.text(" disposition=")
		r.disposition(e.Transition.Disposition)
		r.text(" outputs=")
		r.number(uint64(len(e.Transition.Outputs)))
		r.checks(e.Checks, o.options.Level)
		if o.options.Level == LogTrace {
			r.text(" input=")
			if err := o.payload(r, "input", func(w io.Writer) error { return o.payloads.Input(m, e.Input, w) }); err != nil {
				return err
			}
			for i, output := range e.Transition.Outputs {
				r.text(" output[")
				r.number(uint64(i))
				r.text("]=")
				if err := o.payload(r, "output", func(w io.Writer) error { return o.payloads.Output(m, output, w) }); err != nil {
					return err
				}
			}
			if o.options.Snapshots.includes(e.Sequence) {
				r.text(" post_state=")
				if err := o.payload(r, "state", func(w io.Writer) error { return o.payloads.State(m, e.Transition.State, w) }); err != nil {
					return err
				}
			}
		}
	}
	if r.failure != nil {
		return &TextObserverError{"format", r.failure}
	}
	return nil
}

type observationRecord struct {
	bytes   *[]byte
	maximum uint64
	failure error
}

func (r *observationRecord) Write(p []byte) (int, error) {
	if err := r.reserve(len(p)); err != nil {
		return 0, err
	}
	*r.bytes = append(*r.bytes, p...)
	return len(p), nil
}
func (r *observationRecord) WriteString(s string) (int, error) {
	if err := r.reserve(len(s)); err != nil {
		return 0, err
	}
	*r.bytes = append(*r.bytes, s...)
	return len(s), nil
}
func (r *observationRecord) reserve(length int) (err error) {
	if r.failure != nil {
		return r.failure
	}
	current, incoming := uint64(len(*r.bytes)), uint64(length)
	maxInt := uint64(int(^uint(0) >> 1))
	if current > r.maximum || incoming > r.maximum-current || incoming > maxInt-current {
		r.failure = ErrObservationRecordLimit
		return r.failure
	}
	defer func() {
		if recover() != nil {
			r.failure = errors.New("unable to allocate observation record")
			err = r.failure
		}
	}()
	required := current + incoming
	if required > uint64(cap(*r.bytes)) {
		capacity := uint64(cap(*r.bytes))
		if capacity > maxInt/2 {
			capacity = maxInt
		} else {
			capacity *= 2
		}
		capacity = min(max(capacity, required, 64), r.maximum, maxInt)
		grown := make([]byte, len(*r.bytes), int(capacity))
		copy(grown, *r.bytes)
		*r.bytes = grown
	}
	return nil
}
func (r *observationRecord) text(s string) { _, _ = r.WriteString(s) }
func (r *observationRecord) number(n uint64) {
	if r.failure == nil {
		r.text(strconv.FormatUint(n, 10))
	}
}
func (r *observationRecord) optional(n *uint64) {
	if n == nil {
		r.text("None")
	} else {
		r.text("Some(")
		r.number(*n)
		r.text(")")
	}
}
func (r *observationRecord) quoted(s string) {
	if r.failure != nil {
		return
	}
	r.text("\"")
	for len(s) != 0 && r.failure == nil {
		ch, size := utf8.DecodeRuneInString(s)
		if ch == utf8.RuneError && size == 1 {
			r.failure = &ConfigError{"invalid observation UTF-8"}
			return
		}
		part := s[:size]
		s = s[size:]
		switch ch {
		case 0:
			r.text("\\0")
		case '\t':
			r.text("\\t")
		case '\r':
			r.text("\\r")
		case '\n':
			r.text("\\n")
		case '\\':
			r.text("\\\\")
		case '"':
			r.text("\\\"")
		default:
			if ch == ' ' || (unicode.IsGraphic(ch) && !unicode.Is(unicode.Z, ch) && !unicode.Is(unicode.Mn, ch) && !unicode.Is(unicode.Me, ch) && !unicode.Is(unicode.Other_Grapheme_Extend, ch)) {
				r.text(part)
			} else {
				r.text("\\u{")
				r.text(strconv.FormatUint(uint64(ch), 16))
				r.text("}")
			}
		}
	}
	r.text("\"")
}
func (r *observationRecord) checks(checks []Check, threshold LogLevel) {
	r.text(" checks=")
	r.number(uint64(len(checks)))
	for _, c := range checks {
		if r.failure != nil {
			return
		}
		if threshold < LogTrace && c.Status == "passed" {
			continue
		}
		if err := c.Validate(); err != nil {
			r.failure = err
			return
		}
		r.text(" check=")
		r.quoted(c.ID)
		r.text(":")
		switch c.Status {
		case "passed":
			r.text("Passed")
		case "failed":
			r.text("Failed(")
			r.quoted(c.Details)
			r.text(")")
		case "skipped":
			r.text("Skipped(")
			r.quoted(c.Details)
			r.text(")")
		}
	}
}
func (r *observationRecord) disposition(d Disposition) {
	if r.failure != nil {
		return
	}
	if err := d.Validate(); err != nil {
		r.failure = err
		return
	}
	switch d.Kind {
	case "accepted":
		r.text("Accepted")
	case "rejected":
		r.text("Rejected(")
		r.quoted(d.Reason)
		r.text(")")
	case "ignored":
		r.text("Ignored(")
		r.quoted(d.Reason)
		r.text(")")
	}
}

type observationRecordLease struct {
	record        *observationRecord
	encodingStage string
}

func (w *observationRecordLease) Write(p []byte) (int, error) {
	if w.record == nil {
		return 0, errors.New("observation record lease expired")
	}
	return w.record.Write(p)
}
func (w *observationRecordLease) WriteString(s string) (int, error) {
	if w.record == nil {
		return 0, errors.New("observation record lease expired")
	}
	return w.record.WriteString(s)
}

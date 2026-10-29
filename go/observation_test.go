package statelessness_test

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"math"
	"reflect"
	"strconv"
	"strings"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

type observationCounts struct {
	calls                                  []string
	initial, step, checks, effects, legacy int
}

func observationModel(c *observationCounts) s.Model[byte, byte, byte] {
	encode := func(stage string) func(byte, *s.EncodeBuffer) error {
		return func(v byte, b *s.EncodeBuffer) error {
			c.calls = append(c.calls, stage+":"+strconv.Itoa(int(v)))
			return b.Append([]byte{v})
		}
	}
	legacy := func(byte) ([]byte, error) { c.legacy++; return nil, errors.New("legacy called") }
	return s.Model[byte, byte, byte]{
		InitialState: func() (byte, error) { c.initial++; return 0, nil },
		Step:         func(byte, byte) (s.Transition[byte, byte], error) { c.step++; return s.Accept[byte, byte](0), nil },
		CheckState:   func(byte) ([]s.Check, error) { c.checks++; return []s.Check{s.Passed("state")}, nil },
		CheckTransition: func(byte, byte, s.Transition[byte, byte]) ([]s.Check, error) {
			c.checks++
			return []s.Check{s.Passed("edge")}, nil
		},
		CloneState: s.ValueCopy[byte], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[byte],
		Codec: &s.Codec[byte, byte, byte]{
			Metadata:    func() (s.Metadata, error) { return s.Identity("observation", "test"), nil },
			EncodeState: legacy, EncodeInput: legacy, EncodeOutput: legacy,
			EncodeStateInto: encode("state"), EncodeInputInto: encode("input"), EncodeOutputInto: encode("output"),
			DecodeState: func(b []byte) (byte, error) { return b[0], nil }, DecodeInput: func(b []byte) (byte, error) { return b[0], nil },
		},
	}
}
func observationTurn(sequence uint64) s.Event[byte, byte, byte] {
	return s.Event[byte, byte, byte]{Kind: s.EventTransition, Sequence: sequence, Before: 250, Input: 1,
		Transition: s.Accept[byte, byte](7, 99, 100), Checks: []s.Check{s.Passed("first")}}
}
func observationLogger(t *testing.T, w io.Writer, level s.LogLevel, snapshots s.SnapshotPolicy, maximum uint64, p s.PayloadFormat[byte, byte, byte]) *s.TextObserver[byte, byte, byte] {
	t.Helper()
	o, err := s.NewTextObserver[byte, byte, byte](w, "test", s.ObservationOptions{Level: level, Snapshots: snapshots, MaxRecordBytes: maximum}, p)
	if err != nil {
		t.Fatal(err)
	}
	return o
}
func observationOK(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}
func observationStage(t *testing.T, err error, want string) *s.TextObserverError {
	t.Helper()
	e, ok := err.(*s.TextObserverError)
	if !ok || e == nil || e.Stage != want {
		t.Fatalf("unexpected error type/stage: %T, want %s", err, want)
	}
	return e
}

func TestObservationThresholdsDispositionAndNoExecution(t *testing.T) {
	for level := s.LogOff; level <= s.LogTrace; level++ {
		for _, disposition := range []s.Disposition{s.Accepted(), s.Rejected("no"), s.Ignored("duplicate")} {
			t.Run(level.String()+"/"+disposition.Kind, func(t *testing.T) {
				c := &observationCounts{}
				m := observationModel(c)
				var dst bytes.Buffer
				o := observationLogger(t, &dst, level, s.SnapshotEveryTransition(), 4096, s.NewEncodedPayloads[byte, byte, byte](16))
				e := observationTurn(1)
				e.Transition.Disposition = disposition
				observationOK(t, o.Observe(m, e))
				if (dst.Len() != 0) != (level >= s.LogDebug) {
					t.Fatal("threshold")
				}
				if level == s.LogTrace {
					if !reflect.DeepEqual(c.calls, []string{"input:1", "output:99", "output:100", "state:7"}) {
						t.Fatal(c.calls)
					}
				} else if len(c.calls) != 0 {
					t.Fatal("payloads below Trace")
				}
				if c.initial+c.step+c.checks+c.effects+c.legacy != 0 {
					t.Fatal("diagnostics executed model work")
				}
			})
		}
	}
}

func TestObservationLifecycleAndMessageLevels(t *testing.T) {
	for level := s.LogOff; level <= s.LogTrace; level++ {
		t.Run(level.String(), func(t *testing.T) {
			var dst bytes.Buffer
			o := observationLogger(t, &dst, level, s.SnapshotNone(), 4096, nil)
			m := s.Model[byte, byte, byte]{} // No codec, clone, check or reducer required.
			observationOK(t, o.Observe(m, s.Event[byte, byte, byte]{Kind: s.EventStarted, Metadata: s.Identity("m", "b"), Config: s.RunConfig{Strategy: "manual"}}))
			for severity := s.LogOff; severity <= s.LogTrace; severity++ {
				observationOK(t, o.Observe(m, s.Event[byte, byte, byte]{Kind: s.EventMessage, Level: severity, Message: "message"}))
			}
			observationOK(t, o.Observe(m, s.Event[byte, byte, byte]{Kind: s.EventFinished, Reason: "done"}))
			if strings.Contains(dst.String(), "event=started") != (level >= s.LogInfo) || strings.Contains(dst.String(), "event=finished") != (level >= s.LogInfo) {
				t.Fatal(dst.String())
			}
			if strings.Count(dst.String(), "event=message") != int(level) {
				t.Fatal(dst.String())
			}
		})
	}
}

func TestObservationFailurePrecedesSkipAndTraceDetails(t *testing.T) {
	for _, kind := range []s.EventKind{s.EventCheckpoint, s.EventTransition} {
		for _, checks := range [][]s.Check{
			{s.Skipped("skip", "policy"), s.Failed("fail", "bad\nline"), s.Passed("ok")},
			{s.Failed("fail", "bad\nline"), s.Skipped("skip", "policy"), s.Passed("ok")},
		} {
			for _, level := range []s.LogLevel{s.LogError, s.LogTrace} {
				var dst bytes.Buffer
				o := observationLogger(t, &dst, level, s.SnapshotEveryTransition(), 4096, nil)
				e := observationTurn(1)
				e.Kind, e.Checks = kind, checks
				observationOK(t, o.Observe(s.Model[byte, byte, byte]{}, e))
				text := dst.String()
				if !strings.HasPrefix(text, "Error ") || strings.Count(text, "\n") != 1 || !strings.Contains(text, `Failed("bad\nline")`) {
					t.Fatal(text)
				}
				if strings.Contains(text, `check="ok":Passed`) != (level == s.LogTrace) {
					t.Fatal(text)
				}
				if strings.Contains(text, "=unavailable") != (level == s.LogTrace) {
					t.Fatal(text)
				}
			}
		}
	}
	for _, level := range []s.LogLevel{s.LogError, s.LogWarn, s.LogDebug, s.LogTrace} {
		var dst bytes.Buffer
		o := observationLogger(t, &dst, level, s.SnapshotNone(), 4096, nil)
		e := observationTurn(2)
		e.Kind, e.Checks = s.EventCheckpoint, []s.Check{s.Passed("ok"), s.Skipped("skip", "policy")}
		observationOK(t, o.Observe(s.Model[byte, byte, byte]{}, e))
		if (dst.Len() != 0) != (level >= s.LogWarn) {
			t.Fatal(dst.String())
		}
		dst.Reset()
		e.Checks = nil
		observationOK(t, o.Observe(s.Model[byte, byte, byte]{}, e))
		if (dst.Len() != 0) != (level == s.LogTrace) {
			t.Fatal("ordinary checkpoint threshold")
		}
	}
}

func TestObservationSnapshotsAndExactU64(t *testing.T) {
	c := &observationCounts{}
	m := observationModel(c)
	period, err := s.SnapshotEvery(2)
	observationOK(t, err)
	var dst bytes.Buffer
	o := observationLogger(t, &dst, s.LogOff, period, 4096, s.NewEncodedPayloads[byte, byte, byte](16))
	observationOK(t, o.Observe(m, observationTurn(1)))
	observationOK(t, o.SetLevel(s.LogTrace))
	observationOK(t, o.Observe(m, s.Event[byte, byte, byte]{Kind: s.EventCheckpoint, Sequence: 1, State: 8}))
	observationOK(t, o.Observe(m, observationTurn(2)))
	observationOK(t, o.Observe(m, observationTurn(3)))
	observationOK(t, o.SetSnapshots(s.SnapshotNone()))
	observationOK(t, o.Observe(m, observationTurn(4)))
	maximum, err := s.SnapshotEvery(math.MaxUint64)
	observationOK(t, err)
	observationOK(t, o.SetSnapshots(maximum))
	observationOK(t, o.Observe(m, observationTurn(math.MaxUint64)))
	if strings.Count(dst.String(), "post_state=") != 2 || !strings.Contains(dst.String(), "sequence=18446744073709551615") {
		t.Fatal(dst.String())
	}
	want := []string{"state:8", "input:1", "output:99", "output:100", "state:7", "input:1", "output:99", "output:100", "input:1", "output:99", "output:100", "input:1", "output:99", "output:100", "state:7"}
	if !reflect.DeepEqual(c.calls, want) {
		t.Fatal(c.calls)
	}
	if _, err := s.SnapshotEvery(0); err == nil {
		t.Fatal("zero period accepted")
	}
}

func TestObservationExactSourceText(t *testing.T) {
	c := &observationCounts{}
	var dst bytes.Buffer
	o := observationLogger(t, &dst, s.LogTrace, s.SnapshotEveryTransition(), 4096, s.NewEncodedPayloads[byte, byte, byte](16))
	e := observationTurn(12)
	e.Transition.Disposition = s.Ignored("duplicate")
	e.Checks = append(e.Checks, s.Skipped("second", "policy"))
	observationOK(t, o.Observe(observationModel(c), e))
	want := "Warn stream=\"test\" event=transition sequence=12 disposition=Ignored(\"duplicate\") outputs=2 checks=2 check=\"first\":Passed check=\"second\":Skipped(\"policy\") input=hex:01 output[0]=hex:63 output[1]=hex:64 post_state=hex:07\n"
	if dst.String() != want {
		t.Fatalf("got %q want %q", dst.String(), want)
	}
	dst.Reset()
	seed := uint64(4)
	observationOK(t, o.Observe(s.Model[byte, byte, byte]{}, s.Event[byte, byte, byte]{Kind: s.EventStarted, Metadata: s.Identity("m\n", "é"), Config: s.RunConfig{Strategy: "manual", Seed: &seed, Parameters: [][2]string{{"k", "v\t"}}}}))
	want = "Info stream=\"test\" event=started metadata=ModelMetadata { name: \"m\\n\", model_version: 1, properties_version: 1, codec_version: 1, build: \"é\" } config=RunConfig { strategy: \"manual\", seed: Some(4), parameters: [(\"k\", \"v\\t\")] }\n"
	if dst.String() != want {
		t.Fatalf("got %q want %q", dst.String(), want)
	}
}

func TestObservationUTF8NewlineBoundsAndRecovery(t *testing.T) {
	sequence := uint64(math.MaxUint64)
	want := "Info stream=\"é\" event=message sequence=Some(18446744073709551615) message=\"🙂\"\n"
	for _, maximum := range []uint64{0, uint64(len(want) - 1), uint64(len(want))} {
		var dst bytes.Buffer
		opts := s.DefaultObservationOptions()
		opts.Level, opts.MaxRecordBytes = s.LogTrace, maximum
		o, err := s.NewTextObserver[byte, byte, byte](&dst, "é", opts, nil)
		observationOK(t, err)
		err = o.Observe(s.Model[byte, byte, byte]{}, s.Event[byte, byte, byte]{Kind: s.EventMessage, Level: s.LogInfo, MessageSequence: &sequence, Message: "🙂"})
		if maximum < uint64(len(want)) {
			if observationStage(t, err, "format").Cause != s.ErrObservationRecordLimit || dst.Len() != 0 || o.IsPoisoned() {
				t.Fatal("bound leaked or poisoned")
			}
			observationOK(t, o.SetLevel(s.LogOff))
			observationOK(t, o.Observe(s.Model[byte, byte, byte]{}, observationTurn(1)))
		} else if err != nil || dst.String() != want {
			t.Fatal("exact bound rejected")
		}
	}
	var dst bytes.Buffer
	o := observationLogger(t, &dst, s.LogInfo, s.SnapshotNone(), 80, nil)
	err := o.Observe(s.Model[byte, byte, byte]{}, s.Event[byte, byte, byte]{Kind: s.EventMessage, Level: s.LogInfo, Message: strings.Repeat("é", 100)})
	observationStage(t, err, "format")
	observationOK(t, o.Observe(s.Model[byte, byte, byte]{}, s.Event[byte, byte, byte]{Kind: s.EventFinished, Reason: "ok"}))
	if strings.Count(dst.String(), "\n") != 1 {
		t.Fatal(dst.String())
	}
}

type observationFormatter struct {
	f func(string, byte, io.Writer) error
}

func (p observationFormatter) State(_ s.Model[byte, byte, byte], v byte, w io.Writer) error {
	return p.f("state", v, w)
}
func (p observationFormatter) Input(_ s.Model[byte, byte, byte], v byte, w io.Writer) error {
	return p.f("input", v, w)
}
func (p observationFormatter) Output(_ s.Model[byte, byte, byte], v byte, w io.Writer) error {
	return p.f("output", v, w)
}

func TestObservationStickyFormatterAndExpiredWriter(t *testing.T) {
	var retained io.Writer
	var dst bytes.Buffer
	callbackCount := 0
	p := observationFormatter{func(stage string, _ byte, w io.Writer) error {
		callbackCount++
		retained = w
		_, _ = w.Write(bytes.Repeat([]byte{'x'}, 1024)) // Deliberately swallowed.
		_, _ = w.Write([]byte("small"))
		return nil
	}}
	o := observationLogger(t, &dst, s.LogTrace, s.SnapshotEveryTransition(), 512, p)
	err := o.Observe(s.Model[byte, byte, byte]{}, observationTurn(1))
	if observationStage(t, err, "format").Cause != s.ErrObservationRecordLimit || dst.Len() != 0 || callbackCount != 1 {
		t.Fatal("swallowed error published a record or continued payloads")
	}
	if _, err := retained.Write([]byte("escaped")); err == nil {
		t.Fatal("retained writer live")
	}
	observationOK(t, o.SetLevel(s.LogDebug))
	observationOK(t, o.Observe(s.Model[byte, byte, byte]{}, observationTurn(2)))
	if o.IsPoisoned() || strings.Count(dst.String(), "\n") != 1 {
		t.Fatal("no recovery")
	}
}

type hostileObservationError struct{ calls *int }

func (e *hostileObservationError) Error() string {
	(*e.calls)++
	panic("Error must not run")
}
func (e *hostileObservationError) String() string {
	(*e.calls)++
	panic("String must not run")
}
func (e *hostileObservationError) Unwrap() error {
	(*e.calls)++
	panic("Unwrap must not run")
}
func (e *hostileObservationError) Is(error) bool {
	(*e.calls)++
	panic("Is must not run")
}
func (e *hostileObservationError) As(any) bool {
	(*e.calls)++
	panic("As must not run")
}
func (e *hostileObservationError) Format(fmt.State, rune) {
	(*e.calls)++
	panic("Format must not run")
}

func TestObservationPublicErrorsRemainDistinct(t *testing.T) {
	cause := errors.New("oracle advance failed")
	oracle := &s.ObservationError[byte, byte]{Err: cause, Transition: s.Accept[byte, byte](7, 99)}
	if oracle.Unwrap() != cause || oracle.Transition.State != 7 || oracle.Transition.Outputs[0] != 99 {
		t.Fatal("oracle error lost the actual transition")
	}
	var diagnostic *s.TextObserverError
	if diagnostic.Error() != "observation failed" || diagnostic.Unwrap() != nil {
		t.Fatal("nil diagnostic inspection must be safe")
	}
	found := false
	for _, profile := range s.ExperimentalProfiles() {
		found = found || profile == "observation-v1"
	}
	if !found {
		t.Fatal("observation profile missing from experimental discovery")
	}
}

func TestObservationFormatterCannotSupplyStageOrOverrideRecordFailure(t *testing.T) {
	for _, stage := range []string{"input", "output", "state"} {
		for _, mode := range []string{"write", "flush", "poisoned", "format", "encode input", "encode output", "encode state", "typed-nil", "panic", "panic-nil", "nested-observer"} {
			for _, overflow := range []bool{false, true} {
				t.Run(stage+"/"+mode+"/overflow="+strconv.FormatBool(overflow), func(t *testing.T) {
					formatted := 0
					hostile := &hostileObservationError{&formatted}
					var supplied error = &s.TextObserverError{Stage: mode, Cause: hostile}
					if mode == "typed-nil" {
						var absent *s.TextObserverError
						supplied = absent
					}
					var nested *s.TextObserver[byte, byte, byte]
					var nestedWriter *observationWriter
					if mode == "nested-observer" {
						nestedWriter = &observationWriter{mode: "full-error", cause: hostile}
						nested = observationLogger(t, nestedWriter, s.LogDebug, s.SnapshotNone(), 4096, nil)
					}
					broken, callbacks := true, 0
					var retained io.Writer
					p := observationFormatter{func(kind string, _ byte, w io.Writer) error {
						callbacks++
						if broken && kind == stage {
							retained = w
							if overflow {
								_, _ = w.Write(bytes.Repeat([]byte{'x'}, 1024))
							}
							if mode == "panic" {
								panic(supplied)
							}
							if mode == "panic-nil" {
								panic(nil)
							}
							if nested != nil {
								supplied = nested.Observe(s.Model[byte, byte, byte]{}, observationTurn(99))
							}
							return supplied
						}
						_, err := io.WriteString(w, "ok")
						return err
					}}
					w := &observationWriter{}
					o := observationLogger(t, w, s.LogTrace, s.SnapshotEveryTransition(), 512, p)
					wantStage := "format " + stage
					if overflow {
						wantStage = "format"
					}
					err := observationStage(t, o.Observe(s.Model[byte, byte, byte]{}, observationTurn(1)), wantStage)
					if err.Error() != "observation "+wantStage+" failed" {
						t.Fatal("unsafe diagnostic message")
					}
					if overflow {
						if err.Unwrap() != s.ErrObservationRecordLimit {
							t.Fatal("callback masked sticky record failure")
						}
					} else if mode != "panic" && mode != "panic-nil" && err.Unwrap() != supplied {
						t.Fatal("opaque callback cause lost")
					}
					wantCallbacks := map[string]int{"input": 1, "output": 2, "state": 4}[stage]
					if callbacks != wantCallbacks || formatted != 0 || w.Len() != 0 || w.writes != 0 || w.flushes != 0 || o.IsPoisoned() {
						t.Fatal("formatter failure touched or poisoned the destination")
					}
					if nested != nil && (!nested.IsPoisoned() || nestedWriter.writes != 1) {
						t.Fatal("nested sink failure was not exercised")
					}
					if _, escaped := retained.Write([]byte("escaped")); escaped == nil {
						t.Fatal("failed formatter retained a writer lease")
					}
					if _, escaped := io.WriteString(retained, "escaped"); escaped == nil {
						t.Fatal("failed formatter retained a string writer lease")
					}
					before := callbacks
					observationOK(t, o.SetLevel(s.LogOff))
					observationOK(t, o.Observe(s.Model[byte, byte, byte]{}, observationTurn(2)))
					if callbacks != before || w.writes != 0 || w.flushes != 0 {
						t.Fatal("healthy Off probe did work")
					}
					broken = false
					observationOK(t, o.SetLevel(s.LogTrace))
					observationOK(t, o.Observe(s.Model[byte, byte, byte]{}, observationTurn(3)))
					if callbacks != before+4 || w.writes != 1 || strings.Count(w.String(), "\n") != 1 {
						t.Fatal("formatter did not recover")
					}
				})
			}
		}
	}
}

func TestObservationCodecAttributionBelongsToActivePayloadLease(t *testing.T) {
	for _, mode := range []string{"active", "other-stage", "other-writer", "previous-payload", "previous-output", "previous-event"} {
		t.Run(mode, func(t *testing.T) {
			var captured error
			invocation := 0
			encoded := s.NewEncodedPayloads[byte, byte, byte](16)
			noCodec := s.Model[byte, byte, byte]{}
			p := observationFormatter{func(kind string, v byte, w io.Writer) error {
				if mode == "previous-output" {
					if kind == "input" {
						return nil
					}
					if v == 99 {
						captured = encoded.Output(noCodec, v, w)
						return nil
					}
					return captured
				}
				if kind == "input" {
					invocation++
					if mode == "previous-event" && invocation > 1 {
						return captured
					}
					switch mode {
					case "other-stage":
						captured = encoded.Output(noCodec, v, w)
					case "other-writer":
						captured = encoded.Input(noCodec, v, io.Discard)
					default:
						captured = encoded.Input(noCodec, v, w)
					}
					if mode == "previous-payload" {
						return nil
					}
					return captured
				}
				return captured
			}}
			w := &observationWriter{}
			o := observationLogger(t, w, s.LogTrace, s.SnapshotEveryTransition(), 4096, p)
			want := "format input"
			if mode == "active" || mode == "previous-event" {
				want = "encode input"
			} else if mode == "previous-payload" || mode == "previous-output" {
				want = "format output"
			}
			observationStage(t, o.Observe(noCodec, observationTurn(1)), want)
			if mode == "previous-event" {
				observationStage(t, o.Observe(noCodec, observationTurn(2)), "format input")
			}
			if w.writes != 0 || w.flushes != 0 || w.Len() != 0 || o.IsPoisoned() {
				t.Fatal("codec attribution changed destination health")
			}
			observationOK(t, o.SetLevel(s.LogDebug))
			observationOK(t, o.Observe(noCodec, observationTurn(3)))
			if w.writes != 1 || strings.Count(w.String(), "\n") != 1 {
				t.Fatal("codec failure did not recover")
			}
		})
	}
}

func TestObservationFormatterErrorsAndPanicsAreOpaque(t *testing.T) {
	for _, stage := range []string{"input", "output", "state"} {
		for _, panicInstead := range []bool{false, true} {
			var dst bytes.Buffer
			formatted, calls := 0, []string{}
			hostile := &hostileObservationError{&formatted}
			broken := true
			p := observationFormatter{func(kind string, _ byte, w io.Writer) error {
				calls = append(calls, kind)
				_, err := io.WriteString(w, "prefix")
				if err != nil {
					return err
				}
				if broken && kind == stage {
					if panicInstead {
						panic(hostile)
					}
					return hostile
				}
				return nil
			}}
			o := observationLogger(t, &dst, s.LogTrace, s.SnapshotEveryTransition(), 4096, p)
			err := observationStage(t, o.Observe(s.Model[byte, byte, byte]{}, observationTurn(1)), "format "+stage)
			_ = err.Error()
			if formatted != 0 || dst.Len() != 0 || o.IsPoisoned() {
				t.Fatal("opaque failure mishandled")
			}
			if !panicInstead && err.Unwrap() != hostile {
				t.Fatal("cause lost")
			}
			broken = false
			observationOK(t, o.Observe(s.Model[byte, byte, byte]{}, observationTurn(2)))
			if strings.Count(dst.String(), "\n") != 1 {
				t.Fatal("recovery")
			}
		}
	}
}

func TestObservationCodecStickyFailureRecoveryAndPriority(t *testing.T) {
	c := &observationCounts{}
	m := observationModel(c)
	var retained *s.EncodeBuffer
	broken := true
	m.Codec.EncodeInputInto = func(v byte, b *s.EncodeBuffer) error {
		retained = b
		if broken {
			_ = b.Append([]byte{1, 2})
			_ = b.Append(nil)
			return nil
		}
		return b.Append([]byte{v})
	}
	var dst bytes.Buffer
	o := observationLogger(t, &dst, s.LogTrace, s.SnapshotEveryTransition(), 4096, s.NewEncodedPayloads[byte, byte, byte](1))
	observationStage(t, o.Observe(m, observationTurn(1)), "encode input")
	if dst.Len() != 0 || o.IsPoisoned() {
		t.Fatal("encoder failure published or poisoned")
	}
	if retained.Append(nil) == nil {
		t.Fatal("encoder lease retained")
	}
	broken = false
	observationOK(t, o.Observe(m, observationTurn(2)))
	if c.legacy != 0 || !strings.Contains(dst.String(), "input=hex:01") {
		t.Fatal("dispatch priority")
	}
	var noCodec bytes.Buffer
	n := observationLogger(t, &noCodec, s.LogTrace, s.SnapshotEveryTransition(), 4096, s.NewEncodedPayloads[byte, byte, byte](1))
	observationStage(t, n.Observe(s.Model[byte, byte, byte]{}, observationTurn(1)), "encode input")
	// The documented legacy fallback still goes through bounded admission.
	legacyBytes := []byte{0xab, 0xcd}
	m.Codec.EncodeInputInto = nil
	m.Codec.EncodeInput = func(byte) ([]byte, error) { c.legacy++; return legacyBytes, nil }
	var legacyDestination bytes.Buffer
	legacyObserver := observationLogger(t, &legacyDestination, s.LogTrace, s.SnapshotNone(), 4096, s.NewEncodedPayloads[byte, byte, byte](2))
	legacyEvent := observationTurn(3)
	legacyEvent.Transition.Outputs = nil
	observationOK(t, legacyObserver.Observe(m, legacyEvent))
	legacyBytes[0] = 0
	if c.legacy != 1 || !strings.Contains(legacyDestination.String(), "input=hex:abcd") {
		t.Fatal("legacy fallback or detached encoding changed")
	}
}

func TestObservationHostileCodecErrorsAllStages(t *testing.T) {
	for _, stage := range []string{"input", "output", "state"} {
		for _, legacy := range []bool{false, true} {
			for _, panicInstead := range []bool{false, true} {
				c := &observationCounts{}
				m := observationModel(c)
				formatted := 0
				hostile := &hostileObservationError{&formatted}
				badInto := func(byte, *s.EncodeBuffer) error {
					if panicInstead {
						panic(hostile)
					}
					return hostile
				}
				badLegacy := func(byte) ([]byte, error) {
					if panicInstead {
						panic(hostile)
					}
					return nil, hostile
				}
				switch stage {
				case "input":
					m.Codec.EncodeInputInto = badInto
					if legacy {
						m.Codec.EncodeInputInto, m.Codec.EncodeInput = nil, badLegacy
					}
				case "output":
					m.Codec.EncodeOutputInto = badInto
					if legacy {
						m.Codec.EncodeOutputInto, m.Codec.EncodeOutput = nil, badLegacy
					}
				case "state":
					m.Codec.EncodeStateInto = badInto
					if legacy {
						m.Codec.EncodeStateInto, m.Codec.EncodeState = nil, badLegacy
					}
				}
				var dst bytes.Buffer
				o := observationLogger(t, &dst, s.LogTrace, s.SnapshotEveryTransition(), 4096, s.NewEncodedPayloads[byte, byte, byte](16))
				err := observationStage(t, o.Observe(m, observationTurn(1)), "encode "+stage)
				_ = err.Error()
				if !panicInstead && err.Unwrap() != hostile {
					t.Fatal("opaque cause lost")
				}
				if formatted != 0 || dst.Len() != 0 || o.IsPoisoned() {
					t.Fatal("hostile error invoked or record leaked")
				}
			}
		}
	}
}

func TestObservationDeclarationBindingAndVersionGate(t *testing.T) {
	projected, metadataCalls := 0, 0
	field, err := s.OrdinaryField("value", s.Uint8Codec())
	observationOK(t, err)
	declared, err := s.OrderedRecord("Byte", s.PrependRecordField(field, s.NoRecordFields()),
		func(v byte) (s.FieldProduct[byte, s.Unit], error) {
			projected++
			return s.FieldProduct[byte, s.Unit]{Head: v}, nil
		},
		func(p s.FieldProduct[byte, s.Unit]) (byte, error) { return p.Head, nil }, s.RecordOptions{})
	observationOK(t, err)
	version := uint32(1)
	codec, err := s.BindModelCodec(func() (s.Metadata, error) {
		metadataCalls++
		m := s.Identity("declared", "test")
		m.CodecVersion = version
		return m, nil
	}, declared, declared, declared, s.DefaultModelCodecOptions())
	observationOK(t, err)
	m := s.Model[byte, byte, byte]{Codec: codec, CloneState: s.ValueCopy[byte], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[byte]}
	var dst bytes.Buffer
	o := observationLogger(t, &dst, s.LogDebug, s.SnapshotEveryTransition(), 4096, s.NewEncodedPayloads[byte, byte, byte](16))
	observationOK(t, o.Observe(m, observationTurn(1)))
	if metadataCalls != 0 || projected != 0 {
		t.Fatal("binding ran below Trace")
	}
	observationOK(t, o.SetLevel(s.LogTrace))
	observationOK(t, o.Observe(m, observationTurn(2)))
	if metadataCalls != 4 || projected != 4 {
		t.Fatal(metadataCalls, projected)
	}
	version = 2
	prior := dst.String()
	observationStage(t, o.Observe(m, observationTurn(3)), "encode input")
	if dst.String() != prior || metadataCalls != 5 || projected != 4 {
		t.Fatal("gate bypassed")
	}
	version = 1
	observationOK(t, o.Observe(m, observationTurn(4)))
}

// The two declarations exercise raw field adapter dispatch; each framing call
// adds a revocable child lease over the same observation-owned bounded batch.
func observationNestedDeclaration(t *testing.T, encode func(byte, *s.EncodeBuffer) error) *s.Codec[byte, byte, byte] {
	t.Helper()
	field, err := s.AdapterField("payload", encode, func(*s.Decoder) (byte, error) {
		return 0, errors.New("diagnostic declaration must not decode")
	})
	observationOK(t, err)
	project := func(v byte) (s.FieldProduct[byte, s.Unit], error) {
		return s.FieldProduct[byte, s.Unit]{Head: v}, nil
	}
	construct := func(p s.FieldProduct[byte, s.Unit]) (byte, error) { return p.Head, nil }
	inner, err := s.OrderedRecord("Inner", s.PrependRecordField(field, s.NoRecordFields()), project, construct, s.RecordOptions{})
	observationOK(t, err)
	outerField, err := s.OrdinaryField("inner", inner)
	observationOK(t, err)
	outer, err := s.OrderedRecord("Outer", s.PrependRecordField(outerField, s.NoRecordFields()), project, construct, s.RecordOptions{})
	observationOK(t, err)
	codec, err := s.BindModelCodec(func() (s.Metadata, error) { return s.Identity("nested", "test"), nil }, outer, outer, outer, s.DefaultModelCodecOptions())
	observationOK(t, err)
	return codec
}

func observationExpiredEncodingLeases(t *testing.T, leases []*s.EncodeBuffer) {
	t.Helper()
	for _, lease := range leases {
		if lease.Append(nil) == nil || lease.Finish() == nil {
			t.Fatal("completed nested encoding lease remains active")
		}
		if _, err := lease.Snapshot(); err == nil {
			t.Fatal("expired encoding lease exposes bytes")
		}
		if _, err := lease.Len(); err == nil {
			t.Fatal("expired encoding lease exposes length")
		}
		called := false
		if lease.Framed(func(*s.EncodeBuffer) error { called = true; return nil }) == nil || called {
			t.Fatal("expired encoding lease invoked a framed callback")
		}
	}
}

func TestObservationNestedDeclarationFramingPreservesErrorsAndBounds(t *testing.T) {
	for _, stage := range []string{"input", "output", "state"} {
		for _, mode := range []string{"error", "panic", "typed-nil", "spoof", "adapter-error", "adapter-panic", "adapter-typed-nil", "swallowed-write", "write-then-error", "write-then-panic", "write-then-typed-nil", "write-then-spoof", "swallowed-framed-error", "expired-lease"} {
			t.Run(stage+"/"+mode, func(t *testing.T) {
				c := &observationCounts{}
				m := observationModel(c)
				formatted, callbacks := 0, 0
				hostile := &hostileObservationError{&formatted}
				var absent *s.TextObserverError
				spoof := &s.TextObserverError{Stage: "write", Cause: hostile}
				var firstWrite, expired error
				var leases, stale []*s.EncodeBuffer
				retain := func(b *s.EncodeBuffer) {
					copied := *b
					leases = append(leases, b, &copied)
				}
				broken := true
				target := map[string]byte{"input": 1, "output": 99, "state": 7}[stage]
				m.Codec = observationNestedDeclaration(t, func(v byte, b *s.EncodeBuffer) error {
					callbacks++
					observationExpiredEncodingLeases(t, stale)
					retain(b)
					if broken && v == target {
						switch mode {
						case "adapter-error":
							return hostile
						case "adapter-panic":
							panic(hostile)
						case "adapter-typed-nil":
							return absent
						}
					}
					var innermost *s.EncodeBuffer
					err := b.Framed(func(outer *s.EncodeBuffer) error {
						retain(outer)
						return outer.Framed(func(inner *s.EncodeBuffer) error {
							retain(inner)
							innermost = inner
							if broken && v == target {
								if strings.HasPrefix(mode, "write-then-") || mode == "swallowed-write" {
									// Two u64 frame headers consume 16 of the 17 bytes.
									firstWrite = inner.Append([]byte{v, v})
									if firstWrite == nil || inner.Append(nil) != firstWrite {
										t.Fatal("framed bound is not sticky")
									}
								}
								switch mode {
								case "error", "write-then-error", "swallowed-framed-error":
									return hostile
								case "panic", "write-then-panic":
									panic(hostile)
								case "typed-nil", "write-then-typed-nil":
									return absent
								case "spoof", "write-then-spoof":
									return spoof
								case "swallowed-write":
									return nil
								}
							}
							return inner.Append([]byte{v})
						})
					})
					if err != nil {
						if mode == "swallowed-framed-error" {
							return nil
						}
						return err
					}
					if broken && v == target && mode == "expired-lease" {
						expired = innermost.Append(nil)
						if expired == nil {
							t.Fatal("framed child survived return to its parent")
						}
						return expired
					}
					return nil
				})
				w := &observationWriter{}
				o := observationLogger(t, w, s.LogTrace, s.SnapshotEveryTransition(), 4096, s.NewEncodedPayloads[byte, byte, byte](17))
				err := observationStage(t, o.Observe(m, observationTurn(1)), "encode "+stage)
				_ = err.Error()
				if firstWrite != nil {
					if err.Unwrap() != firstWrite {
						t.Fatal("nested callback masked the first failed write")
					}
				} else {
					switch mode {
					case "error", "adapter-error", "swallowed-framed-error":
						if err.Unwrap() != hostile {
							t.Fatal("nested declaration formatted or replaced a host error")
						}
					case "typed-nil", "adapter-typed-nil":
						if err.Unwrap() != absent {
							t.Fatal("nested typed-nil cause lost")
						}
					case "spoof":
						if err.Unwrap() != spoof {
							t.Fatal("codec-supplied public stage became authority")
						}
					case "expired-lease":
						if err.Unwrap() != expired {
							t.Fatal("expired child error lost")
						}
					}
				}
				wantCallbacks := map[string]int{"input": 1, "output": 2, "state": 4}[stage]
				if callbacks != wantCallbacks || w.writes != 0 || w.flushes != 0 || w.Len() != 0 || formatted != 0 || o.IsPoisoned() {
					t.Fatal("nested failure continued work, formatted an error or published")
				}
				observationExpiredEncodingLeases(t, leases)
				oldLeases := append([]*s.EncodeBuffer(nil), leases...)
				stale = oldLeases
				broken = false
				observationOK(t, o.Observe(m, observationTurn(2)))
				prior := w.String()
				observationExpiredEncodingLeases(t, oldLeases)
				observationExpiredEncodingLeases(t, leases)
				// Source-authored: outer frame length 9, inner frame length 1, byte 1.
				const wantInput = "input=hex:" + "0900000000000000" + "0100000000000000" + "01"
				if !strings.Contains(prior, wantInput) || w.String() != prior || w.writes != 1 || callbacks != wantCallbacks+4 {
					t.Fatal("nested framing did not recover with exact bytes")
				}
				if c.initial+c.step+c.checks+c.effects+c.legacy != 0 {
					t.Fatal("nested diagnostics executed model work")
				}
			})
		}
	}
}

func TestObservationNestedRawCallbackAndFailedWriteStayDistinct(t *testing.T) {
	for _, failedWrite := range []bool{false, true} {
		t.Run("failed-write="+strconv.FormatBool(failedWrite), func(t *testing.T) {
			formatted := 0
			hostile := &hostileObservationError{&formatted}
			var retained *s.EncodeBuffer
			var firstWrite error
			raw, err := s.NewValueCodec[byte]("Raw", func(_ byte, b *s.EncodeBuffer) error {
				retained = b
				if failedWrite {
					firstWrite = b.Append([]byte{1, 2})
				}
				return hostile
			}, nil, nil)
			observationOK(t, err)
			codec := observationNestedDeclaration(t, func(v byte, b *s.EncodeBuffer) error {
				if raw.EncodeRaw(v, b) != hostile {
					t.Fatal("raw callback error replaced")
				}
				// A returned raw callback error is cooperative; a failed write is sticky.
				_ = b.Append([]byte{v})
				return nil
			})
			m := s.Model[byte, byte, byte]{Codec: codec, CloneState: s.ValueCopy[byte], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[byte]}
			w := &observationWriter{}
			o := observationLogger(t, w, s.LogTrace, s.SnapshotNone(), 4096, s.NewEncodedPayloads[byte, byte, byte](1))
			event := observationTurn(1)
			event.Transition.Outputs = nil
			err = o.Observe(m, event)
			if failedWrite {
				if firstWrite == nil || observationStage(t, err, "encode input").Unwrap() != firstWrite || w.writes != 0 {
					t.Fatal("swallowed raw write failure published")
				}
			} else {
				observationOK(t, err)
				if w.writes != 1 || !strings.Contains(w.String(), "input=hex:01") {
					t.Fatal("raw callback error incorrectly poisoned the batch")
				}
			}
			observationExpiredEncodingLeases(t, []*s.EncodeBuffer{retained})
			if formatted != 0 || o.IsPoisoned() {
				t.Fatal("raw error was formatted or poisoned the destination")
			}
		})
	}
}

type observationWriter struct {
	bytes.Buffer
	mode            string
	writes, flushes int
	cause           error
}

func (w *observationWriter) Write(p []byte) (int, error) {
	w.writes++
	switch w.mode {
	case "partial":
		_, _ = w.Buffer.Write(p[:3])
		return 3, w.cause
	case "short":
		_, _ = w.Buffer.Write(p[:3])
		return 3, nil
	case "zero":
		return 0, nil
	case "negative":
		return -1, nil
	case "excess":
		return len(p) + 1, nil
	case "full-error":
		_, _ = w.Buffer.Write(p)
		return len(p), w.cause
	case "panic":
		panic(w.cause)
	}
	return w.Buffer.Write(p)
}
func (w *observationWriter) Flush() error {
	w.flushes++
	if w.mode == "flush" {
		return w.cause
	}
	if w.mode == "flush-panic" {
		panic(w.cause)
	}
	return nil
}

func TestObservationNativeWriterErrorsPoisonEvenOff(t *testing.T) {
	for _, mode := range []string{"partial", "short", "zero", "negative", "excess", "full-error", "panic"} {
		t.Run(mode, func(t *testing.T) {
			formatted := 0
			hostile := &hostileObservationError{&formatted}
			w := &observationWriter{mode: mode, cause: hostile}
			o := observationLogger(t, w, s.LogDebug, s.SnapshotNone(), 4096, nil)
			err := observationStage(t, o.Observe(s.Model[byte, byte, byte]{}, observationTurn(1)), "write")
			_ = err.Error()
			if mode == "short" || mode == "zero" {
				if err.Unwrap() != io.ErrShortWrite {
					t.Fatal("short-write semantics")
				}
			}
			if !o.IsPoisoned() || formatted != 0 || w.writes != 1 {
				t.Fatal("writer failure mishandled")
			}
			observationOK(t, o.SetLevel(s.LogOff))
			observationStage(t, o.Observe(s.Model[byte, byte, byte]{}, observationTurn(2)), "poisoned")
			observationStage(t, o.Flush(), "poisoned")
			if w.writes != 1 || w.flushes != 0 {
				t.Fatal("poisoned sink touched")
			}
			if mode == "partial" || mode == "short" {
				if w.String() != "Deb" {
					t.Fatal("partial bytes duplicated")
				}
			}
		})
	}
}

func TestObservationFlushAndOwnershipNeverImplicit(t *testing.T) {
	for _, mode := range []string{"ok", "flush", "flush-panic"} {
		formatted := 0
		w := &observationWriter{mode: mode, cause: &hostileObservationError{&formatted}}
		o := observationLogger(t, w, s.LogOff, s.SnapshotNone(), 4096, nil)
		err := o.Flush()
		if mode == "ok" {
			observationOK(t, err)
		} else {
			observationStage(t, err, "flush")
			observationStage(t, o.Observe(s.Model[byte, byte, byte]{}, observationTurn(1)), "poisoned")
		}
		if o.GetRef() != w {
			t.Fatal("writer reference")
		}
		got, err := o.IntoInner()
		observationOK(t, err)
		if got != w || w.flushes != 1 || formatted != 0 {
			t.Fatal("transfer flushed")
		}
		if _, err := o.IntoInner(); err == nil {
			t.Fatal("double ownership transfer")
		}
		if o.Observe(s.Model[byte, byte, byte]{}, observationTurn(1)) == nil {
			t.Fatal("detached observer used")
		}
	}
	w := &observationWriter{}
	o := observationLogger(t, w, s.LogDebug, s.SnapshotNone(), 4096, nil)
	observationOK(t, o.Observe(s.Model[byte, byte, byte]{}, observationTurn(1)))
	_, err := o.IntoInner()
	observationOK(t, err)
	if w.flushes != 0 || w.writes != 1 {
		t.Fatal("implicit flush")
	}
}

func TestObservationReentrancyRejectsMutationAndExpiresLeases(t *testing.T) {
	var o *s.TextObserver[byte, byte, byte]
	var retained io.Writer
	var dst bytes.Buffer
	p := observationFormatter{func(_ string, _ byte, w io.Writer) error {
		retained = w
		if o.Observe(s.Model[byte, byte, byte]{}, observationTurn(99)) == nil || o.Flush() == nil || o.SetLevel(s.LogOff) == nil || o.SetSnapshots(s.SnapshotNone()) == nil {
			t.Fatal("nested mutation allowed")
		}
		if _, err := o.IntoInner(); err == nil {
			t.Fatal("nested transfer")
		}
		_, err := io.WriteString(w, "custom")
		return err
	}}
	o = observationLogger(t, &dst, s.LogTrace, s.SnapshotEveryTransition(), 4096, p)
	observationOK(t, o.Observe(s.Model[byte, byte, byte]{}, observationTurn(1)))
	prior := dst.String()
	if _, err := retained.Write([]byte("escape")); err == nil || dst.String() != prior {
		t.Fatal("escaped writer changed destination")
	}
	if strings.Count(prior, "\n") != 1 || strings.Contains(prior, "99 disposition=") {
		t.Fatal(prior)
	}
}

func TestObservationChecksRemainSeparateFromRecorderAuthority(t *testing.T) {
	c := &observationCounts{}
	m := observationModel(c)
	initial, err := s.CheckInitial(m, 0)
	observationOK(t, err)
	token, err := s.CheckTurn(m, 0, 1, s.Accept[byte, byte](7, 99), 1, s.FullChecks())
	observationOK(t, err)
	beforeChecks := c.checks
	r, err := s.NewRecorderWithCheckedInitial(initial, s.RunConfig{Strategy: "manual"}, s.DefaultRecorderOptions())
	observationOK(t, err)
	diagnostic, err := token.Observation()
	observationOK(t, err)
	var dst bytes.Buffer
	o := observationLogger(t, &dst, s.LogTrace, s.SnapshotEveryTransition(), 4096, s.NewEncodedPayloads[byte, byte, byte](16))
	observationOK(t, o.Observe(m, diagnostic.Event()))
	_, err = s.ObserveCheckedRecorder(r, token)
	observationOK(t, err)
	if c.checks != beforeChecks || c.step+c.initial != 0 {
		t.Fatal("handoff re-executed model")
	}
	forged := diagnostic
	forged.Sequence, forged.Before = 900, 88
	forged.Checks = []s.Check{s.Passed("invented")}
	observationOK(t, o.Observe(m, forged.Event())) // Diagnostics accept discontinuous data.
	if r.ObservedSteps() != 1 {
		t.Fatal("diagnostics changed recorder")
	}
	if _, err := s.ObserveCheckedRecorder(r, &s.CheckedTurn[byte, byte, byte]{}); err == nil || !r.IsFrozen() {
		t.Fatal("zero token bypassed guard")
	}
	if c.checks != beforeChecks {
		t.Fatal("forgery caused checks")
	}
}

func TestObservationEscapingConfigurationAndCustomResponsibility(t *testing.T) {
	opts := s.DefaultObservationOptions()
	if opts.Level != s.LogOff || opts.Snapshots != s.SnapshotNone() || opts.MaxRecordBytes != 1048576 {
		t.Fatal(opts)
	}
	var dst bytes.Buffer
	if _, err := s.NewTextObserver[byte, byte, byte](nil, "", opts, nil); err == nil {
		t.Fatal("nil writer")
	}
	if _, err := s.NewTextObserver[byte, byte, byte](&dst, string([]byte{255}), opts, nil); err == nil {
		t.Fatal("invalid UTF-8")
	}
	o := observationLogger(t, &dst, s.LogTrace, s.SnapshotNone(), 4096, nil)
	observationOK(t, o.Observe(s.Model[byte, byte, byte]{}, s.Event[byte, byte, byte]{Kind: s.EventMessage, Level: s.LogInfo, Message: "\x00\x01\x7f\t\n\r\\\"'é🙂\u2028\u0301"}))
	want := "Info stream=\"test\" event=message sequence=None message=\"\\0\\u{1}\\u{7f}\\t\\n\\r\\\\\\\"'é🙂\\u{2028}\\u{301}\"\n"
	if dst.String() != want {
		t.Fatalf("got %q want %q", dst.String(), want)
	}
	if o.SetLevel(s.LogLevel(99)) == nil {
		t.Fatal("invalid level")
	}
	if o.Observe(s.Model[byte, byte, byte]{}, observationTurn(0)) == nil {
		t.Fatal("zero transition sequence")
	}
	if o.Observe(s.Model[byte, byte, byte]{}, s.Event[byte, byte, byte]{Kind: s.EventMessage, Level: s.LogInfo, Message: string([]byte{255})}) == nil {
		t.Fatal("invalid text")
	}
	dst.Reset()
	p := observationFormatter{func(_ string, _ byte, w io.Writer) error { _, err := io.WriteString(w, "caller\ntext"); return err }}
	o = observationLogger(t, &dst, s.LogTrace, s.SnapshotNone(), 4096, p)
	e := observationTurn(1)
	e.Transition.Outputs = nil
	observationOK(t, o.Observe(s.Model[byte, byte, byte]{}, e))
	if strings.Count(dst.String(), "\n") != 2 {
		t.Fatal("custom bytes unexpectedly sanitized")
	}
}

type opaqueObservationValue struct {
	effect    func()
	inspected *int
}

func (v opaqueObservationValue) String() string { *v.inspected++; panic("opaque value was formatted") }

func TestObservationOpaquePayloadsAndEffectsStayApplicationOwned(t *testing.T) {
	inspected, effects := 0, 0
	v := opaqueObservationValue{effect: func() { effects++ }, inspected: &inspected}
	var dst bytes.Buffer
	opts := s.DefaultObservationOptions()
	opts.Level, opts.Snapshots = s.LogTrace, s.SnapshotEveryTransition()
	o, err := s.NewTextObserver[opaqueObservationValue, opaqueObservationValue, opaqueObservationValue](&dst, "opaque", opts, nil)
	observationOK(t, err)
	var observer s.Observer[opaqueObservationValue, opaqueObservationValue, opaqueObservationValue] = o
	m := s.Model[opaqueObservationValue, opaqueObservationValue, opaqueObservationValue]{}
	observationOK(t, observer.Observe(m, s.Event[opaqueObservationValue, opaqueObservationValue, opaqueObservationValue]{
		Kind: s.EventTransition, Sequence: 1, Before: v, Input: v, Transition: s.Accept(v, v),
	}))
	if inspected != 0 || effects != 0 || strings.Count(dst.String(), "=unavailable") != 3 {
		t.Fatal("opaque callbacks invoked")
	}
	prior := dst.String()
	observationOK(t, o.Flush())
	if dst.String() != prior {
		t.Fatal("writer without Flush changed")
	}
}

func TestObservationFilteringAndPrefixBoundsPrecedeFormatter(t *testing.T) {
	for _, level := range []s.LogLevel{s.LogOff, s.LogError, s.LogInfo, s.LogDebug, s.LogTrace} {
		calls := 0
		p := observationFormatter{func(string, byte, io.Writer) error { calls++; panic("formatter should not be called") }}
		var dst bytes.Buffer
		o := observationLogger(t, &dst, level, s.SnapshotEveryTransition(), 4, p)
		err := o.Observe(s.Model[byte, byte, byte]{}, observationTurn(1))
		if level >= s.LogDebug {
			observationStage(t, err, "format")
		} else {
			observationOK(t, err)
		}
		if calls != 0 || dst.Len() != 0 || o.IsPoisoned() {
			t.Fatal("work before filtering/prefix admission")
		}
	}
}

func TestObservationSampledChecksDoNotGrantRecorderCoverage(t *testing.T) {
	c := &observationCounts{}
	m := observationModel(c)
	initial, err := s.CheckInitial(m, 0)
	observationOK(t, err)
	r, err := s.NewRecorderWithCheckedInitial(initial, s.RunConfig{Strategy: "manual"}, s.DefaultRecorderOptions())
	observationOK(t, err)
	token, err := s.CheckTurn(m, 0, 1, s.Accept[byte, byte](7), 1, s.CheckPolicy{StateEvery: 2, TransitionChecks: false})
	observationOK(t, err)
	diagnostic, err := token.Observation()
	observationOK(t, err)
	priorChecks := c.checks
	var dst bytes.Buffer
	o := observationLogger(t, &dst, s.LogWarn, s.SnapshotNone(), 4096, nil)
	observationOK(t, o.Observe(m, diagnostic.Event()))
	if !strings.HasPrefix(dst.String(), "Warn ") || token.FullChecking() {
		t.Fatal("periodic checks misclassified")
	}
	if _, err := s.ObserveCheckedRecorder(r, token); err == nil || !r.IsFrozen() {
		t.Fatal("sampled token gained full coverage")
	}
	observationOK(t, o.Observe(m, diagnostic.Event()))
	if c.checks != priorChecks || r.ObservedSteps() != 0 || strings.Count(dst.String(), "\n") != 2 {
		t.Fatal("diagnostic logging altered checked recording")
	}
}

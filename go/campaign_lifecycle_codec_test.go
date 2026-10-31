package statelessness

import (
	"bytes"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"reflect"
	"sort"
	"strings"
	"testing"
	"unicode/utf8"
)

// Test-only persistence for the actual campaign decision core. The format is
// src/campaign/lifecycle/codec.rs, not a public campaign checkpoint API:
// seven u64 counts as u64 LE, three u64 totals, accounting, termination, stop,
// sorted pending entries, optional delivery, lost-worker and finalized flags.
// Encoding checks representability, not lifecycle properties. Decoding a core
// invokes the independent lcStateChecks owned by campaign_lifecycle_test.go.
func lcEncodeCore(state campaignState) ([]byte, error) {
	w := lcWireEncoder{}
	r := state.report
	for _, n := range []uint64{r.Requested, r.Started, r.Finished, r.NotStarted, r.Delivered, r.Failures, r.Errors} {
		w.number(n)
	}
	w.number(r.ReportedTransitions)
	w.number(r.ReportedCases)
	w.number(r.ReportedSkippedChecks)
	w.boolean(r.AccountingComplete)
	switch r.Termination.Kind {
	case "jobs_completed":
		w.tag(0)
	case "failure_found":
		w.tag(1)
	case "cancelled":
		w.tag(2)
	case "deadline":
		w.tag(3)
	case "callback_error":
		w.tag(4)
		w.message(r.Termination.Error)
	case "worker_error":
		w.tag(5)
		w.message(r.Termination.Error)
	default:
		w.err = lcWireInvalid()
	}
	switch state.stop {
	case campaignNoStop:
		w.tag(0)
	case campaignStopFailure:
		w.tag(1)
	case campaignStopCancelled:
		w.tag(2)
	case campaignStopDeadline:
		w.tag(3)
	case campaignStopSink:
		w.tag(4)
	case campaignStopWorker:
		w.tag(5)
	default:
		w.err = lcWireInvalid()
	}
	if w.err != nil {
		return nil, w.err
	}
	ids := make([]uint64, 0, len(state.pending))
	for id := range state.pending {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	w.count(len(ids))
	for _, id := range ids {
		w.number(id)
		p := state.pending[id]
		w.boolean(p.ready)
		if p.ready {
			w.summary(p.summary)
		}
	}
	w.boolean(state.hasDelivery)
	if state.hasDelivery {
		w.number(state.delivering)
	}
	w.boolean(state.lostWorker)
	w.boolean(state.finalized)
	return w.finish()
}

func lcDecodeCore(m campaignLifecycle, b []byte) (campaignState, error) {
	d := lcWireDecoder{remaining: b}
	r := CampaignReport64{
		Requested:             d.number(),
		Started:               d.number(),
		Finished:              d.number(),
		NotStarted:            d.number(),
		Delivered:             d.number(),
		Failures:              d.number(),
		Errors:                d.number(),
		ReportedTransitions:   d.number(),
		ReportedCases:         d.number(),
		ReportedSkippedChecks: d.number(),
		AccountingComplete:    d.boolean(),
	}
	switch d.tag() {
	case 0:
		r.Termination = CampaignTermination{Kind: "jobs_completed"}
	case 1:
		r.Termination = CampaignTermination{Kind: "failure_found"}
	case 2:
		r.Termination = CampaignTermination{Kind: "cancelled"}
	case 3:
		r.Termination = CampaignTermination{Kind: "deadline"}
	case 4:
		r.Termination = CampaignTermination{Kind: "callback_error", Error: d.message()}
	case 5:
		r.Termination = CampaignTermination{Kind: "worker_error", Error: d.message()}
	default:
		d.err = lcWireInvalid()
	}
	state := campaignState{report: r}
	switch d.tag() {
	case 0:
		state.stop = campaignNoStop
	case 1:
		state.stop = campaignStopFailure
	case 2:
		state.stop = campaignStopCancelled
	case 3:
		state.stop = campaignStopDeadline
	case 4:
		state.stop = campaignStopSink
	case 5:
		state.stop = campaignStopWorker
	default:
		d.err = lcWireInvalid()
	}
	n := d.count()
	// Each entry needs at least an ID and a tag. Never trust a wire count as
	// an allocation hint before checking both the job limit and the byte range.
	if d.err != nil {
		return campaignState{}, d.err
	}
	if uint64(n) > m.jobs || n > len(d.remaining)/9 {
		return campaignState{}, lcWireInvalid()
	}
	state.pending = make(map[uint64]campaignPending)
	var previous uint64
	for i := 0; i < n; i++ {
		id := d.number()
		if d.err != nil || (i > 0 && id <= previous) {
			return campaignState{}, lcWireInvalid()
		}
		previous = id
		p := campaignPending{}
		switch d.tag() {
		case 0:
		case 1:
			p.ready = true
			p.summary = d.summary()
		default:
			d.err = lcWireInvalid()
		}
		if d.err != nil {
			return campaignState{}, d.err
		}
		state.pending[id] = p
	}
	state.hasDelivery = d.boolean()
	if state.hasDelivery {
		state.delivering = d.number()
	}
	state.lostWorker = d.boolean()
	state.finalized = d.boolean()
	if err := d.finish(); err != nil {
		return campaignState{}, err
	}
	for _, check := range lcStateChecks(m, state) {
		if check.Status == "failed" {
			return campaignState{}, &ModelError{Message: "decoded campaign state violates lifecycle properties"}
		}
	}
	return state, nil
}

func lcEncodeEvent(input campaignEvent) ([]byte, error) {
	w := lcWireEncoder{}
	switch input.kind {
	case campaignClaim:
		w.tag(0)
		switch input.stop {
		case campaignNoStop:
			w.tag(0)
		case campaignStopCancelled:
			w.tag(1)
		case campaignStopDeadline:
			w.tag(2)
		default:
			w.err = lcWireInvalid()
		}
	case campaignComplete:
		w.tag(1)
		w.number(input.jobID)
		w.summary(input.summary)
	case campaignReceive:
		w.tag(2)
		w.number(input.jobID)
	case campaignSinkReturned:
		w.tag(3)
		w.number(input.jobID)
		w.boolean(input.err != nil)
		if input.err != nil {
			w.message(input.err)
		}
	case campaignWorkerFailed:
		w.tag(4)
		w.message(input.err)
		w.boolean(input.lostWork)
	case campaignFinish:
		w.tag(5)
	default:
		w.err = lcWireInvalid()
	}
	return w.finish()
}

func lcDecodeEvent(b []byte) (campaignEvent, error) {
	d := lcWireDecoder{remaining: b}
	input := campaignEvent{}
	switch d.tag() {
	case 0:
		input.kind = campaignClaim
		switch d.tag() {
		case 0:
		case 1:
			input.stop = campaignStopCancelled
		case 2:
			input.stop = campaignStopDeadline
		default:
			d.err = lcWireInvalid()
		}
	case 1:
		input.kind = campaignComplete
		input.jobID = d.number()
		input.summary = d.summary()
	case 2:
		input.kind = campaignReceive
		input.jobID = d.number()
	case 3:
		input.kind = campaignSinkReturned
		input.jobID = d.number()
		if d.boolean() {
			input.err = d.message()
		}
	case 4:
		input.kind = campaignWorkerFailed
		input.err = d.message()
		input.lostWork = d.boolean()
	case 5:
		input.kind = campaignFinish
	default:
		d.err = lcWireInvalid()
	}
	if err := d.finish(); err != nil {
		return campaignEvent{}, err
	}
	return input, nil
}

func lcEncodeEffect(output campaignEffect) ([]byte, error) {
	w := lcWireEncoder{}
	switch output.kind {
	case campaignRunJob:
		w.tag(0)
	case campaignDeliver:
		w.tag(1)
	case campaignDiscard:
		w.tag(2)
	default:
		w.err = lcWireInvalid()
	}
	w.number(output.jobID)
	return w.finish()
}

func lcWireInvalid() error {
	return &ModelError{Message: "invalid campaign lifecycle encoding"}
}

type lcWireEncoder struct {
	b   []byte
	err error
}

func (w *lcWireEncoder) tag(v byte) {
	if w.err == nil {
		w.b = append(w.b, v)
	}
}

func (w *lcWireEncoder) boolean(v bool) {
	if v {
		w.tag(1)
	} else {
		w.tag(0)
	}
}

func (w *lcWireEncoder) number(v uint64) {
	if w.err != nil {
		return
	}
	var b [8]byte
	binary.LittleEndian.PutUint64(b[:], v)
	w.b = append(w.b, b[:]...)
}

func (w *lcWireEncoder) count(v int) {
	if v < 0 {
		w.err = lcWireInvalid()
		return
	}
	w.number(uint64(v))
}

func (w *lcWireEncoder) message(err error) {
	if w.err != nil {
		return
	}
	// Rust error variants always contain a string. A missing required error
	// cannot be silently converted into Some(""); optional nil has its own tag.
	if err == nil {
		w.err = lcWireInvalid()
		return
	}
	message := err.Error()
	if !utf8.ValidString(message) {
		w.err = lcWireInvalid()
		return
	}
	w.count(len(message))
	w.b = append(w.b, message...)
}

func (w *lcWireEncoder) summary(s campaignSummary) {
	switch s.outcome {
	case campaignCompleted:
		w.tag(0)
	case campaignFailure:
		w.tag(1)
	case campaignCancelled:
		w.tag(2)
	case campaignDeadline:
		w.tag(3)
	case campaignFactoryError:
		w.tag(4)
	case campaignModelError:
		w.tag(5)
	case campaignPanicked:
		w.tag(6)
	default:
		w.err = lcWireInvalid()
	}
	w.boolean(s.failure)
	w.number(s.transitions)
	w.number(s.cases)
	w.number(s.skippedChecks)
}

func (w *lcWireEncoder) finish() ([]byte, error) {
	if w.err != nil {
		return nil, w.err
	}
	return w.b, nil
}

type lcWireDecoder struct {
	remaining []byte
	err       error
}

func (d *lcWireDecoder) take(n int) []byte {
	if d.err != nil {
		return nil
	}
	if n < 0 || n > len(d.remaining) {
		d.err = lcWireInvalid()
		return nil
	}
	b := d.remaining[:n]
	d.remaining = d.remaining[n:]
	return b
}

func (d *lcWireDecoder) tag() byte {
	b := d.take(1)
	if d.err != nil {
		return 0
	}
	return b[0]
}

func (d *lcWireDecoder) boolean() bool {
	switch d.tag() {
	case 0:
		return false
	case 1:
		return true
	default:
		d.err = lcWireInvalid()
		return false
	}
}

func (d *lcWireDecoder) number() uint64 {
	b := d.take(8)
	if d.err != nil {
		return 0
	}
	return binary.LittleEndian.Uint64(b)
}

func (d *lcWireDecoder) count() int {
	n := d.number()
	// Native materialization lengths must fit int. Logical report counts use
	// number() and never pass through this allocation-boundary conversion.
	if n > uint64(^uint(0)>>1) {
		d.err = &ModelError{"campaign lifecycle materialization exceeds native int range"}
		return 0
	}
	return int(n)
}

func TestLCWireFullWidthCounts(t *testing.T) {
	for _, jobs := range []uint64{uint64(^uint(0) >> 1), uint64(^uint(0)>>1) + 1, ^uint64(0)} {
		m := campaignLifecycle{jobs: jobs}
		state := lcWireInitial(m)
		lcWireRoundtripCore(t, m, state)
		lcApply(t, m, &state, campaignEvent{kind: campaignClaim})
		lcWireRoundtripCore(t, m, state)
		b := lcWireCoreBytes(t, state)
		restarted, err := lcDecodeCore(m, b)
		if err != nil {
			t.Fatal(err)
		}
		lcApply(t, m, &restarted, lcComplete(0, campaignCompleted))
		lcApply(t, m, &restarted, campaignEvent{kind: campaignReceive, jobID: 0})
		lcApply(t, m, &restarted, lcSink(0, nil))
		lcApply(t, m, &restarted, campaignEvent{kind: campaignClaim, stop: campaignStopCancelled})
		lcApply(t, m, &restarted, campaignEvent{kind: campaignFinish})
		if restarted.report.Requested != jobs || restarted.report.NotStarted != jobs-1 || restarted.report.Delivered != 1 {
			t.Fatal(restarted)
		}
		lcWireRoundtripCore(t, m, restarted)
	}
}

func (d *lcWireDecoder) message() error {
	n := d.count()
	b := d.take(n)
	if d.err != nil {
		return nil
	}
	// Check the complete byte slice before allocating the owned string.
	if !utf8.Valid(b) {
		d.err = lcWireInvalid()
		return nil
	}
	return &ModelError{Message: string(b)}
}

func (d *lcWireDecoder) summary() campaignSummary {
	s := campaignSummary{}
	switch d.tag() {
	case 0:
		s.outcome = campaignCompleted
	case 1:
		s.outcome = campaignFailure
	case 2:
		s.outcome = campaignCancelled
	case 3:
		s.outcome = campaignDeadline
	case 4:
		s.outcome = campaignFactoryError
	case 5:
		s.outcome = campaignModelError
	case 6:
		s.outcome = campaignPanicked
	default:
		d.err = lcWireInvalid()
	}
	s.failure = d.boolean()
	s.transitions = d.number()
	s.cases = d.number()
	s.skippedChecks = d.number()
	// Rust's input codec admits inconsistent summaries. Complete rejects them;
	// state decoding rejects them via the model's independent properties.
	return s
}

func (d *lcWireDecoder) finish() error {
	if d.err != nil {
		return d.err
	}
	if len(d.remaining) != 0 {
		return lcWireInvalid()
	}
	return nil
}

func lcWireModel() campaignLifecycle {
	return campaignLifecycle{firstJob: 7, jobs: 4, stopOnFailure: true}
}

func lcWireInitial(m campaignLifecycle) campaignState {
	return campaignState{
		report: CampaignReport64{
			Requested: m.jobs, NotStarted: m.jobs, AccountingComplete: true,
			Termination: CampaignTermination{Kind: "jobs_completed"},
		},
		pending: make(map[uint64]campaignPending),
	}
}

func lcWireOutcomes() []campaignOutcome {
	return []campaignOutcome{
		campaignCompleted, campaignFailure, campaignCancelled, campaignDeadline,
		campaignFactoryError, campaignModelError, campaignPanicked,
	}
}

func lcWireSummary(outcome campaignOutcome) campaignSummary {
	s := campaignSummary{outcome: outcome, failure: outcome == campaignFailure}
	switch outcome {
	case campaignFactoryError, campaignModelError, campaignPanicked:
	default:
		s.transitions, s.cases, s.skippedChecks = ^uint64(0), 17, 19
	}
	return s
}

func lcWireHex(t *testing.T, value string) []byte {
	t.Helper()
	b, err := hex.DecodeString(strings.Join(strings.Fields(value), ""))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func lcWireSameError(a, b error) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return a.Error() == b.Error()
}

func lcWireAssertEvent(t *testing.T, want, got campaignEvent) {
	t.Helper()
	if !lcWireSameError(want.err, got.err) {
		t.Fatalf("event error: want %#v, got %#v", want.err, got.err)
	}
	want.err, got.err = nil, nil
	if !reflect.DeepEqual(want, got) {
		t.Fatalf("event: want %#v, got %#v", want, got)
	}
}

func lcWireAssertState(t *testing.T, want, got campaignState) {
	t.Helper()
	if !lcWireSameError(want.report.Termination.Error, got.report.Termination.Error) {
		t.Fatalf("termination error: want %#v, got %#v", want.report.Termination.Error, got.report.Termination.Error)
	}
	want.report.Termination.Error, got.report.Termination.Error = nil, nil
	if !reflect.DeepEqual(want, got) {
		t.Fatalf("core: want %#v, got %#v", want, got)
	}
}

func lcWireCoreBytes(t *testing.T, state campaignState) []byte {
	t.Helper()
	b, err := lcEncodeCore(state)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func lcWireEventBytes(t *testing.T, input campaignEvent) []byte {
	t.Helper()
	b, err := lcEncodeEvent(input)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func lcWireRoundtripCore(t *testing.T, m campaignLifecycle, state campaignState) {
	t.Helper()
	b := lcWireCoreBytes(t, state)
	got, err := lcDecodeCore(m, b)
	if err != nil {
		t.Fatalf("decode core: %v; bytes %x", err, b)
	}
	lcWireAssertState(t, state, got)
	if reencoded := lcWireCoreBytes(t, got); !bytes.Equal(reencoded, b) {
		t.Fatalf("noncanonical core: %x became %x", b, reencoded)
	}
	for end := 0; end < len(b); end++ {
		if _, err := lcDecodeCore(m, b[:end]); err == nil {
			t.Fatalf("accepted core prefix %d/%d", end, len(b))
		}
	}
	if _, err := lcDecodeCore(m, append(append([]byte{}, b...), 0)); err == nil {
		t.Fatal("accepted trailing core byte")
	}
}

func TestLCWireKnownEventBytes(t *testing.T) {
	// Literal expectations, independent of the encoder, including byte length
	// (not rune count), embedded NUL, full-width IDs and present-empty errors.
	cases := []struct {
		name  string
		input campaignEvent
		hex   string
	}{
		{"claim", campaignEvent{kind: campaignClaim}, "00 00"},
		{"cancel", campaignEvent{kind: campaignClaim, stop: campaignStopCancelled}, "00 01"},
		{"deadline", campaignEvent{kind: campaignClaim, stop: campaignStopDeadline}, "00 02"},
		{"receive", campaignEvent{kind: campaignReceive, jobID: 0x0102030405060708}, "02 0807060504030201"},
		{"sink-nil", campaignEvent{kind: campaignSinkReturned, jobID: 7}, "03 0700000000000000 00"},
		{"sink-empty", campaignEvent{kind: campaignSinkReturned, jobID: 7, err: errors.New("")}, "03 0700000000000000 01 0000000000000000"},
		{"sink-utf8", campaignEvent{kind: campaignSinkReturned, jobID: 8, err: errors.New("sink\x00錯誤")}, "03 0800000000000000 01 0b00000000000000 73696e6b00e98cafe8aaa4"},
		{"worker-empty", campaignEvent{kind: campaignWorkerFailed, err: errors.New(""), lostWork: true}, "04 0000000000000000 01"},
		{"worker", campaignEvent{kind: campaignWorkerFailed, err: errors.New("x")}, "04 0100000000000000 78 00"},
		{"finish", campaignEvent{kind: campaignFinish}, "05"},
		{"complete-failure", campaignEvent{kind: campaignComplete, jobID: ^uint64(0), summary: lcWireSummary(campaignFailure)}, "01 ffffffffffffffff 01 01 ffffffffffffffff 1100000000000000 1300000000000000"},
		{"complete-panicked", campaignEvent{kind: campaignComplete, jobID: 0, summary: lcWireSummary(campaignPanicked)}, "01 0000000000000000 06 00 0000000000000000 0000000000000000 0000000000000000"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			want := lcWireHex(t, tc.hex)
			if got := lcWireEventBytes(t, tc.input); !bytes.Equal(got, want) {
				t.Fatalf("want %x, got %x", want, got)
			}
			got, err := lcDecodeEvent(want)
			if err != nil {
				t.Fatal(err)
			}
			lcWireAssertEvent(t, tc.input, got)
		})
	}
}

func TestLCWireKnownCoreBytes(t *testing.T) {
	m := lcWireModel()
	initial := lcWireInitial(m)
	initialBytes := lcWireHex(t, `
		0400000000000000 0000000000000000 0000000000000000
		0400000000000000 0000000000000000 0000000000000000 0000000000000000
		0000000000000000 0000000000000000 0000000000000000
		01 00 00 0000000000000000 00 00 00`)
	// Unequal counts/totals expose field swaps; unsorted map insertion must
	// still yield ascending numeric IDs on the wire.
	denseModel := campaignLifecycle{firstJob: 7, jobs: 9, stopOnFailure: true}
	dense := campaignState{
		report: CampaignReport64{
			Requested: 9, Started: 8, Finished: 6, NotStarted: 1, Delivered: 5, Failures: 2, Errors: 3,
			ReportedTransitions: 0x0102030405060708, ReportedCases: 17, ReportedSkippedChecks: 19,
			Termination: CampaignTermination{Kind: "jobs_completed"},
		},
		pending: map[uint64]campaignPending{
			14: {ready: true, summary: lcWireSummary(campaignFailure)},
			13: {},
		},
		delivering: 12, hasDelivery: true,
	}
	denseBytes := lcWireHex(t, `
		0900000000000000 0800000000000000 0600000000000000
		0100000000000000 0500000000000000 0200000000000000 0300000000000000
		0807060504030201 1100000000000000 1300000000000000
		00 00 00 0200000000000000
		0d00000000000000 00
		0e00000000000000 01 01 01 ffffffffffffffff 1100000000000000 1300000000000000
		01 0c00000000000000 00 00`)
	for _, tc := range []struct {
		name  string
		model campaignLifecycle
		state campaignState
		want  []byte
	}{{"initial", m, initial, initialBytes}, {"dense", denseModel, dense, denseBytes}} {
		t.Run(tc.name, func(t *testing.T) {
			if got := lcWireCoreBytes(t, tc.state); !bytes.Equal(got, tc.want) {
				t.Fatalf("want %x, got %x", tc.want, got)
			}
			got, err := lcDecodeCore(tc.model, tc.want)
			if err != nil {
				t.Fatal(err)
			}
			lcWireAssertState(t, tc.state, got)
			lcWireRoundtripCore(t, tc.model, tc.state)
		})
	}
}

func TestLCWireEveryEventRoundtripAndFraming(t *testing.T) {
	inputs := []campaignEvent{
		{kind: campaignClaim},
		{kind: campaignClaim, stop: campaignStopCancelled},
		{kind: campaignClaim, stop: campaignStopDeadline},
		{kind: campaignReceive, jobID: ^uint64(0)},
		{kind: campaignSinkReturned, jobID: 7},
		{kind: campaignSinkReturned, err: errors.New("")},
		{kind: campaignSinkReturned, jobID: 8, err: errors.New("sink\x00錯誤")},
		{kind: campaignWorkerFailed, err: errors.New("worker\nerror")},
		{kind: campaignWorkerFailed, err: errors.New(""), lostWork: true},
		{kind: campaignFinish},
	}
	for _, outcome := range lcWireOutcomes() {
		inputs = append(inputs, campaignEvent{kind: campaignComplete, jobID: 9, summary: lcWireSummary(outcome)})
	}
	for i, input := range inputs {
		t.Run(fmt.Sprintf("%d", i), func(t *testing.T) {
			b := lcWireEventBytes(t, input)
			got, err := lcDecodeEvent(b)
			if err != nil {
				t.Fatal(err)
			}
			lcWireAssertEvent(t, input, got)
			if again := lcWireEventBytes(t, got); !bytes.Equal(b, again) {
				t.Fatalf("noncanonical event: %x became %x", b, again)
			}
			for end := 0; end < len(b); end++ {
				if _, err := lcDecodeEvent(b[:end]); err == nil {
					t.Fatalf("accepted event prefix %d/%d", end, len(b))
				}
			}
			if _, err := lcDecodeEvent(append(append([]byte{}, b...), 0)); err == nil {
				t.Fatal("accepted trailing event byte")
			}
		})
	}
}

func TestLCWireEveryCoreVariantAndFraming(t *testing.T) {
	m := lcWireModel()
	lcWireRoundtripCore(t, m, lcWireInitial(m))
	state := campaignState{
		report: CampaignReport64{
			Requested: 4, Started: 4, Finished: 3, Delivered: 2, Failures: 1, Errors: 1,
			ReportedTransitions: ^uint64(0), ReportedCases: 17, ReportedSkippedChecks: 19,
			Termination: CampaignTermination{Kind: "jobs_completed"},
		},
		pending:    map[uint64]campaignPending{10: {}},
		delivering: 9, hasDelivery: true,
	}
	lcWireRoundtripCore(t, m, state)
	for _, outcome := range lcWireOutcomes() {
		state.pending[10] = campaignPending{ready: true, summary: lcWireSummary(outcome)}
		lcWireRoundtripCore(t, m, state)
	}
	for _, stop := range []campaignStop{campaignStopFailure, campaignStopCancelled, campaignStopDeadline} {
		state.stop = stop
		lcWireRoundtripCore(t, m, state)
	}
	state.hasDelivery, state.delivering = false, 0
	state.pending = make(map[uint64]campaignPending)
	state.report.Finished, state.report.Delivered = 4, 4
	state.finalized = true
	for _, tc := range []struct {
		stop campaignStop
		term CampaignTermination
		tail string
	}{
		{campaignNoStop, CampaignTermination{Kind: "jobs_completed"}, "00 00 0000000000000000 00 00 01"},
		{campaignStopFailure, CampaignTermination{Kind: "failure_found"}, "01 01 0000000000000000 00 00 01"},
		{campaignStopCancelled, CampaignTermination{Kind: "cancelled"}, "02 02 0000000000000000 00 00 01"},
		{campaignStopDeadline, CampaignTermination{Kind: "deadline"}, "03 03 0000000000000000 00 00 01"},
		{campaignStopSink, CampaignTermination{Kind: "callback_error", Error: errors.New("sink\x00錯誤")}, "04 0b00000000000000 73696e6b00e98cafe8aaa4 04 0000000000000000 00 00 01"},
		{campaignStopWorker, CampaignTermination{Kind: "worker_error", Error: errors.New("")}, "05 0000000000000000 05 0000000000000000 00 00 01"},
	} {
		t.Run(tc.term.Kind, func(t *testing.T) {
			variant := state.clone()
			variant.stop, variant.report.Termination = tc.stop, tc.term
			if tc.term.Kind == "callback_error" {
				variant.report.Delivered = 3
			}
			b := lcWireCoreBytes(t, variant)
			if want := lcWireHex(t, tc.tail); !bytes.Equal(b[81:], want) {
				t.Fatalf("termination/stop suffix: want %x, got %x", want, b[81:])
			}
			lcWireRoundtripCore(t, m, variant)
		})
	}
	state.stop = campaignStopWorker
	state.report.Termination = CampaignTermination{Kind: "worker_error", Error: errors.New("")}
	state.lostWorker = true
	state.report.Finished, state.report.Delivered = 3, 3
	state.pending[10] = campaignPending{}
	lcWireRoundtripCore(t, m, state)
	// Full-width job IDs are valid independently of Go's signed int counts.
	for _, first := range []uint64{0, ^uint64(0)} {
		m := campaignLifecycle{firstJob: first, jobs: 1}
		s := lcWireInitial(m)
		s.report.Started, s.report.NotStarted = 1, 0
		s.pending[first] = campaignPending{}
		lcWireRoundtripCore(t, m, s)
		delete(s.pending, first)
		s.report.Finished, s.hasDelivery, s.delivering = 1, true, first
		lcWireRoundtripCore(t, m, s)
	}
}

func TestLCWireEffectsCanonicalBytes(t *testing.T) {
	for _, tc := range []struct {
		id  uint64
		hex string
	}{
		{0, "0000000000000000"},
		{7, "0700000000000000"},
		{0x0102030405060708, "0807060504030201"},
		{^uint64(0), "ffffffffffffffff"},
	} {
		for _, variant := range []struct {
			kind campaignEffectKind
			tag  byte
		}{{campaignRunJob, 0}, {campaignDeliver, 1}, {campaignDiscard, 2}} {
			want := append([]byte{variant.tag}, lcWireHex(t, tc.hex)...)
			got, err := lcEncodeEffect(campaignEffect{kind: variant.kind, jobID: tc.id})
			if err != nil || !bytes.Equal(want, got) {
				t.Fatalf("effect %d/%d: want %x, got %x, error %v", variant.tag, tc.id, want, got, err)
			}
		}
	}
	if b, err := lcEncodeEffect(campaignEffect{kind: campaignEffectKind(255)}); err == nil || b != nil {
		t.Fatalf("unknown output tag: bytes %x, error %v", b, err)
	}
}

func TestLCWireSummaryTagsCanonicalBytes(t *testing.T) {
	for _, tc := range []struct {
		outcome campaignOutcome
		hex     string
	}{
		{campaignCompleted, "00 00 ffffffffffffffff 1100000000000000 1300000000000000"},
		{campaignFailure, "01 01 ffffffffffffffff 1100000000000000 1300000000000000"},
		{campaignCancelled, "02 00 ffffffffffffffff 1100000000000000 1300000000000000"},
		{campaignDeadline, "03 00 ffffffffffffffff 1100000000000000 1300000000000000"},
		{campaignFactoryError, "04 00 0000000000000000 0000000000000000 0000000000000000"},
		{campaignModelError, "05 00 0000000000000000 0000000000000000 0000000000000000"},
		{campaignPanicked, "06 00 0000000000000000 0000000000000000 0000000000000000"},
	} {
		t.Run(string(tc.outcome), func(t *testing.T) {
			input := campaignEvent{kind: campaignComplete, jobID: 7, summary: lcWireSummary(tc.outcome)}
			want := append(lcWireHex(t, "01 0700000000000000"), lcWireHex(t, tc.hex)...)
			if got := lcWireEventBytes(t, input); !bytes.Equal(got, want) {
				t.Fatalf("summary: want %x, got %x", want, got)
			}
			got, err := lcDecodeEvent(want)
			if err != nil {
				t.Fatal(err)
			}
			lcWireAssertEvent(t, input, got)
		})
	}
}

func TestLCWireCanonicalizesInactiveFields(t *testing.T) {
	inputs := []campaignEvent{
		{kind: campaignClaim, stop: campaignStopDeadline},
		{kind: campaignComplete, jobID: 7, summary: lcWireSummary(campaignCompleted)},
		{kind: campaignReceive, jobID: 7},
		{kind: campaignSinkReturned, jobID: 7},
		{kind: campaignSinkReturned, jobID: 7, err: errors.New("")},
		{kind: campaignWorkerFailed, err: errors.New("worker"), lostWork: true},
		{kind: campaignFinish},
	}
	for _, want := range inputs {
		noisy := campaignEvent{
			kind: want.kind, jobID: ^uint64(0), stop: campaignStop("not a stop"),
			summary: campaignSummary{outcome: campaignOutcome("not an outcome"), failure: true, transitions: 99},
			err:     errors.New("\xff"), lostWork: true,
		}
		switch want.kind {
		case campaignClaim:
			noisy.stop = want.stop
		case campaignComplete:
			noisy.jobID, noisy.summary = want.jobID, want.summary
		case campaignReceive:
			noisy.jobID = want.jobID
		case campaignSinkReturned:
			noisy.jobID, noisy.err = want.jobID, want.err
		case campaignWorkerFailed:
			noisy.err, noisy.lostWork = want.err, want.lostWork
		}
		b := lcWireEventBytes(t, noisy)
		if !bytes.Equal(b, lcWireEventBytes(t, want)) {
			t.Fatalf("inactive fields affected event kind %d", want.kind)
		}
		got, err := lcDecodeEvent(b)
		if err != nil {
			t.Fatal(err)
		}
		lcWireAssertEvent(t, want, got)
	}

	m := lcWireModel()
	clean := lcWireInitial(m)
	clean.report.Started, clean.report.NotStarted = 1, 3
	clean.pending[7] = campaignPending{}
	noisy := clean.clone()
	noisy.delivering = ^uint64(0)                       // hasDelivery is false.
	noisy.report.Termination.Error = errors.New("\xff") // Ordinary termination.
	noisy.pending[7] = campaignPending{summary: campaignSummary{outcome: "unknown", failure: true, cases: 99}}
	b := lcWireCoreBytes(t, noisy)
	if !bytes.Equal(b, lcWireCoreBytes(t, clean)) {
		t.Fatal("inactive state fields changed canonical bytes")
	}
	got, err := lcDecodeCore(m, b)
	if err != nil {
		t.Fatal(err)
	}
	lcWireAssertState(t, clean, got)
	noisy = lcWireInitial(m)
	noisy.pending = nil
	if !bytes.Equal(lcWireCoreBytes(t, noisy), lcWireCoreBytes(t, lcWireInitial(m))) {
		t.Fatal("nil and empty pending maps have different wire encodings")
	}
}

func TestLCWirePendingSortIsNumericAndInsertionIndependent(t *testing.T) {
	m := campaignLifecycle{firstJob: 254, jobs: 3}
	var reference []byte
	for _, ids := range [][]uint64{
		{254, 255, 256}, {256, 254, 255}, {255, 256, 254},
	} {
		state := lcWireInitial(m)
		state.report.Started, state.report.NotStarted = 3, 0
		for _, id := range ids {
			state.pending[id] = campaignPending{}
		}
		b := lcWireCoreBytes(t, state)
		wantPending := lcWireHex(t, "fe00000000000000 00 ff00000000000000 00 0001000000000000 00")
		if !bytes.Equal(b[91:118], wantPending) {
			t.Fatalf("pending order: want %x, got %x", wantPending, b[91:118])
		}
		if reference != nil && !bytes.Equal(reference, b) {
			t.Fatal("map insertion order changed core bytes")
		}
		reference = b
		lcWireRoundtripCore(t, m, state)
	}
}

func TestLCWireRejectsMalformedEvents(t *testing.T) {
	complete := lcWireEventBytes(t, campaignEvent{kind: campaignComplete, jobID: 7, summary: lcWireSummary(campaignCompleted)})
	sink := lcWireEventBytes(t, campaignEvent{kind: campaignSinkReturned, jobID: 7})
	worker := lcWireEventBytes(t, campaignEvent{kind: campaignWorkerFailed, err: errors.New("x")})
	bad := []struct {
		name string
		b    []byte
	}{
		{"unknown-event", []byte{6}},
		{"hostile-event", []byte{255}},
		{"unknown-claim-stop", []byte{0, 3}},
		{"hostile-claim-stop", []byte{0, 255}},
	}
	for _, tc := range []struct {
		name  string
		base  []byte
		index int
		value byte
	}{
		{"unknown-outcome", complete, 9, 7},
		{"hostile-outcome", complete, 9, 255},
		{"summary-boolean", complete, 10, 2},
		{"hostile-summary-boolean", complete, 10, 255},
		{"optional-error-flag", sink, 9, 2},
		{"lost-work-flag", worker, len(worker) - 1, 2},
	} {
		b := append([]byte{}, tc.base...)
		b[tc.index] = tc.value
		bad = append(bad, struct {
			name string
			b    []byte
		}{tc.name, b})
	}
	for _, tc := range bad {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := lcDecodeEvent(tc.b); err == nil {
				t.Fatalf("accepted %x", tc.b)
			}
		})
	}
}

func TestLCWireRejectsHostileErrorLengthsAndUTF8(t *testing.T) {
	m := lcWireModel()
	core := lcWireInitial(m)
	core.stop = campaignStopWorker
	core.report.Termination = CampaignTermination{Kind: "worker_error", Error: errors.New("x")}
	// Every call site for a string is covered, including both core error tags.
	for _, tc := range []struct {
		name   string
		b      []byte
		offset int
		core   bool
	}{
		{"worker", lcWireEventBytes(t, campaignEvent{kind: campaignWorkerFailed, err: errors.New("x")}), 1, false},
		{"sink", lcWireEventBytes(t, campaignEvent{kind: campaignSinkReturned, err: errors.New("x")}), 10, false},
		{"worker-termination", lcWireCoreBytes(t, core), 82, true},
		{"callback-termination", func() []byte {
			// One received report whose sink failed is required causal evidence.
			s := core.clone()
			s.report.Started, s.report.Finished, s.report.NotStarted = 1, 1, 3
			s.stop = campaignStopSink
			s.report.Termination = CampaignTermination{Kind: "callback_error", Error: errors.New("x")}
			return lcWireCoreBytes(t, s)
		}(), 82, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			decode := func(b []byte) error {
				if tc.core {
					_, err := lcDecodeCore(m, b)
					return err
				}
				_, err := lcDecodeEvent(b)
				return err
			}
			if err := decode(tc.b); err != nil {
				t.Fatalf("valid control: %v", err)
			}
			for _, count := range []uint64{^uint64(0), uint64(^uint(0) >> 1), uint64(^uint(0)>>1) + 1, uint64(len(tc.b))} {
				b := append([]byte{}, tc.b...)
				binary.LittleEndian.PutUint64(b[tc.offset:tc.offset+8], count)
				if err := decode(b); err == nil {
					t.Fatalf("accepted error byte length %d", count)
				}
			}
			for _, invalid := range [][]byte{
				{0xff}, {0x80}, {0xc0, 0x80}, {0xed, 0xa0, 0x80}, {0xe9, 0x8c}, {0xf4, 0x90, 0x80, 0x80},
			} {
				b := append([]byte{}, tc.b[:tc.offset+8]...)
				binary.LittleEndian.PutUint64(b[tc.offset:], uint64(len(invalid)))
				b = append(b, invalid...)
				b = append(b, tc.b[tc.offset+9:]...) // Replace the single 'x'.
				if err := decode(b); err == nil {
					t.Fatalf("accepted invalid UTF-8 %x", invalid)
				}
			}
		})
	}
}

func TestLCWireRejectsMalformedCoreCountsTagsAndOrder(t *testing.T) {
	m := lcWireModel()
	initial := lcWireCoreBytes(t, lcWireInitial(m))
	for _, index := range []int{80, 81, 82, 91, 92, 93} {
		for _, value := range []byte{6, 255} {
			b := append([]byte{}, initial...)
			b[index] = value
			if _, err := lcDecodeCore(m, b); err == nil {
				t.Fatalf("accepted core tag %d at %d", value, index)
			}
		}
	}
	for _, index := range []int{80, 91, 92, 93} {
		b := append([]byte{}, initial...)
		b[index] = 2
		if _, err := lcDecodeCore(m, b); err == nil {
			t.Fatalf("accepted boolean 2 at %d", index)
		}
	}
	for _, offset := range []int{0, 8, 16, 24, 32, 40, 48, 83} {
		for _, count := range []uint64{^uint64(0), uint64(^uint(0)>>1) + 1} {
			b := append([]byte{}, initial...)
			binary.LittleEndian.PutUint64(b[offset:offset+8], count)
			if _, err := lcDecodeCore(m, b); err == nil {
				t.Fatalf("accepted unrepresentable count %d at %d", count, offset)
			}
		}
	}
	// Representable but hostile map lengths must also fail before allocation.
	for _, count := range []uint64{1, 5, uint64(^uint(0) >> 1)} {
		b := append([]byte{}, initial...)
		binary.LittleEndian.PutUint64(b[83:91], count)
		if _, err := lcDecodeCore(m, b); err == nil {
			t.Fatalf("accepted pending count %d without enough bytes", count)
		}
	}
	twice := lcWireInitial(m)
	twice.report.Started, twice.report.NotStarted = 2, 2
	twice.pending[7], twice.pending[8] = campaignPending{}, campaignPending{}
	b := lcWireCoreBytes(t, twice)
	for _, ids := range [][2]uint64{{7, 7}, {8, 7}} {
		bad := append([]byte{}, b...)
		binary.LittleEndian.PutUint64(bad[91:99], ids[0])
		binary.LittleEndian.PutUint64(bad[100:108], ids[1])
		if _, err := lcDecodeCore(m, bad); err == nil {
			t.Fatalf("accepted unordered/nonunique pending IDs %v", ids)
		}
	}
	badPending := append([]byte{}, b...)
	badPending[99] = 2
	if _, err := lcDecodeCore(m, badPending); err == nil {
		t.Fatal("accepted unknown pending tag")
	}
	twice.pending[7] = campaignPending{ready: true, summary: lcWireSummary(campaignCompleted)}
	ready := lcWireCoreBytes(t, twice)
	for _, index := range []int{100, 101} {
		bad := append([]byte{}, ready...)
		bad[index] = 255 // Ready summary outcome / boolean.
		if _, err := lcDecodeCore(m, bad); err == nil {
			t.Fatalf("accepted invalid Ready summary tag at %d", index)
		}
	}
	// Valid largest Go count needs no proportional allocation for an empty map.
	large := campaignLifecycle{jobs: ^uint64(0)}
	lcWireRoundtripCore(t, large, lcWireInitial(large))
	fullTotals := lcWireInitial(m)
	fullTotals.report.ReportedTransitions = ^uint64(0)
	fullTotals.report.ReportedCases = ^uint64(0)
	fullTotals.report.ReportedSkippedChecks = ^uint64(0)
	lcWireRoundtripCore(t, m, fullTotals)
}

func TestLCWireStatePropertiesRejectImpossibleCores(t *testing.T) {
	m := lcWireModel()
	initial := lcWireInitial(m)
	claimed := lcWireInitial(m)
	claimed.report.Started, claimed.report.NotStarted = 1, 3
	claimed.pending[7] = campaignPending{}
	delivery := claimed.clone()
	delete(delivery.pending, 7)
	delivery.report.Finished, delivery.delivering, delivery.hasDelivery = 1, 7, true
	cases := []struct {
		name   string
		base   campaignState
		mutate func(*campaignState)
	}{
		{"requested-mismatch", initial, func(s *campaignState) { s.report.Requested = 5 }},
		{"started-overflow", initial, func(s *campaignState) { s.report.Started = 5 }},
		{"not-started-mismatch", initial, func(s *campaignState) { s.report.NotStarted = 3 }},
		{"unclaimed-finished", initial, func(s *campaignState) { s.report.Finished = 1 }},
		{"unreceived-delivery", initial, func(s *campaignState) { s.report.Delivered = 1 }},
		{"unreceived-failure", initial, func(s *campaignState) { s.report.Failures = 1 }},
		{"unreceived-error", initial, func(s *campaignState) { s.report.Errors = 1 }},
		{"failure-error-overlap", delivery, func(s *campaignState) { s.report.Failures, s.report.Errors = 1, 1 }},
		{"missing-pending", claimed, func(s *campaignState) { delete(s.pending, 7) }},
		{"pending-before-range", claimed, func(s *campaignState) { delete(s.pending, 7); s.pending[6] = campaignPending{} }},
		{"pending-unclaimed", claimed, func(s *campaignState) { delete(s.pending, 7); s.pending[8] = campaignPending{} }},
		{"pending-max-id", claimed, func(s *campaignState) { delete(s.pending, 7); s.pending[^uint64(0)] = campaignPending{} }},
		{"delivery-before-range", delivery, func(s *campaignState) { s.delivering = 6 }},
		{"delivery-unclaimed", delivery, func(s *campaignState) { s.delivering = 8 }},
		{"delivery-max-id", delivery, func(s *campaignState) { s.delivering = ^uint64(0) }},
		{"delivery-still-pending", delivery, func(s *campaignState) {
			s.report.Started, s.report.NotStarted = 2, 2
			s.pending[7] = campaignPending{}
		}},
		{"receipt-without-ack-or-active-sink", delivery, func(s *campaignState) { s.hasDelivery, s.delivering = false, 0 }},
		{"already-acknowledged-active-sink", delivery, func(s *campaignState) { s.report.Delivered = 1 }},
		{"finalized-active-delivery", delivery, func(s *campaignState) { s.finalized = true }},
		{"finalized-pending-without-loss", claimed, func(s *campaignState) {
			s.finalized, s.stop = true, campaignStopCancelled
			s.report.Termination = CampaignTermination{Kind: "cancelled"}
		}},
		{"completed-with-unstarted-work", initial, func(s *campaignState) { s.finalized = true }},
		{"nonfinal-ordinary-termination", initial, func(s *campaignState) {
			s.stop = campaignStopCancelled
			s.report.Termination = CampaignTermination{Kind: "cancelled"}
		}},
		{"final-reason-mismatch", initial, func(s *campaignState) {
			s.finalized = true
			s.report.Termination = CampaignTermination{Kind: "cancelled"}
		}},
		{"sink-stop-without-error", initial, func(s *campaignState) { s.stop = campaignStopSink }},
		{"worker-stop-without-error", initial, func(s *campaignState) { s.stop = campaignStopWorker }},
		{"error-without-stop", initial, func(s *campaignState) {
			s.report.Termination = CampaignTermination{Kind: "worker_error", Error: errors.New("worker")}
		}},
		{"loss-with-complete-accounting", initial, func(s *campaignState) {
			s.lostWorker, s.stop = true, campaignStopWorker
			s.report.Termination = CampaignTermination{Kind: "worker_error", Error: errors.New("lost")}
		}},
		{"loss-without-error", initial, func(s *campaignState) {
			s.lostWorker, s.report.AccountingComplete = true, false
		}},
		{"callback-without-failed-sink", initial, func(s *campaignState) {
			s.stop = campaignStopSink
			s.report.Termination = CampaignTermination{Kind: "callback_error", Error: errors.New("never called")}
		}},
		{"callback-with-active-sink", delivery, func(s *campaignState) {
			s.stop = campaignStopSink
			s.report.Termination = CampaignTermination{Kind: "callback_error", Error: errors.New("already failed")}
		}},
		{"failure-without-evidence", initial, func(s *campaignState) {
			s.finalized, s.stop = true, campaignStopFailure
			s.report.Termination = CampaignTermination{Kind: "failure_found"}
		}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			state := tc.base.clone()
			tc.mutate(&state)
			// These must remain encodable: encoding is not the property gate.
			b := lcWireCoreBytes(t, state)
			if _, err := lcDecodeCore(m, b); err == nil {
				t.Fatalf("accepted impossible core: %#v", state)
			}
		})
	}
	failure := claimed.clone()
	failure.stop = campaignStopFailure
	failure.pending[7] = campaignPending{ready: true, summary: lcWireSummary(campaignFailure)}
	lcWireRoundtripCore(t, m, failure)
	disabled := m
	disabled.stopOnFailure = false
	if _, err := lcDecodeCore(disabled, lcWireCoreBytes(t, failure)); err == nil {
		t.Fatal("accepted failure stop while stop-on-failure is disabled")
	}
	otherRange := m
	otherRange.firstJob = 70
	if _, err := lcDecodeCore(otherRange, lcWireCoreBytes(t, claimed)); err == nil {
		t.Fatal("accepted core from a different job range")
	}
}

func TestLCWireSummarySyntaxIsSeparateFromStateProperties(t *testing.T) {
	m := lcWireModel()
	for _, outcome := range lcWireOutcomes() {
		good := lcWireSummary(outcome)
		bad := good
		bad.failure = !good.failure
		invalid := []campaignSummary{bad}
		if outcome == campaignFactoryError || outcome == campaignModelError || outcome == campaignPanicked {
			for i := 0; i < 3; i++ {
				bad := good
				switch i {
				case 0:
					bad.transitions = 1
				case 1:
					bad.cases = 1
				case 2:
					bad.skippedChecks = 1
				}
				invalid = append(invalid, bad)
			}
		}
		for _, summary := range invalid {
			input := campaignEvent{kind: campaignComplete, jobID: 7, summary: summary}
			got, err := lcDecodeEvent(lcWireEventBytes(t, input))
			if err != nil {
				t.Fatalf("Rust permits this syntactic event: %v", err)
			}
			lcWireAssertEvent(t, input, got)
			state := lcWireInitial(m)
			state.report.Started, state.report.NotStarted = 1, 3
			state.pending[7] = campaignPending{ready: true, summary: summary}
			if _, err := lcDecodeCore(m, lcWireCoreBytes(t, state)); err == nil {
				t.Fatalf("accepted inconsistent pending summary: %#v", summary)
			}
		}
	}
}

func TestLCWireEncodingRejectsUnrepresentableActiveValues(t *testing.T) {
	for _, input := range []campaignEvent{
		{kind: campaignEventKind(255)},
		{kind: campaignClaim, stop: campaignStopFailure},
		{kind: campaignClaim, stop: campaignStopSink},
		{kind: campaignClaim, stop: campaignStopWorker},
		{kind: campaignClaim, stop: "unknown"},
		{kind: campaignComplete, summary: campaignSummary{outcome: "unknown"}},
		{kind: campaignSinkReturned, err: errors.New("\xff")},
		{kind: campaignWorkerFailed},
		{kind: campaignWorkerFailed, err: errors.New("\xff")},
	} {
		if b, err := lcEncodeEvent(input); err == nil || b != nil {
			t.Fatalf("encoded unrepresentable event %#v: %x, %v", input, b, err)
		}
	}
	for _, mutate := range []func(*campaignState){
		func(s *campaignState) { s.stop = "unknown" },
		func(s *campaignState) { s.report.Termination.Kind = "unknown" },
		func(s *campaignState) { s.report.Termination = CampaignTermination{Kind: "callback_error"} },
		func(s *campaignState) { s.report.Termination = CampaignTermination{Kind: "worker_error"} },
		func(s *campaignState) {
			s.report.Termination = CampaignTermination{Kind: "callback_error", Error: errors.New("\xff")}
		},
		func(s *campaignState) {
			s.report.Termination = CampaignTermination{Kind: "worker_error", Error: errors.New("\xff")}
		},
		func(s *campaignState) {
			s.pending[7] = campaignPending{ready: true, summary: campaignSummary{outcome: "unknown"}}
		},
	} {
		state := lcWireInitial(lcWireModel())
		mutate(&state)
		if b, err := lcEncodeCore(state); err == nil || b != nil {
			t.Fatalf("encoded unrepresentable core %#v: %x, %v", state, b, err)
		}
	}
}

func TestLCWireUnsignedCountsPreserveWidthAndRejectImpossibleState(t *testing.T) {
	// Counts are u64 both in Go and on the wire, so a negative count is not
	// representable. The all-ones bit pattern is MaxUint64, not a signed -1:
	// encoding must preserve it, while the model rejects impossible counts.
	setters := []struct {
		name string
		set  func(*campaignState)
	}{
		{"requested", func(s *campaignState) { s.report.Requested = math.MaxUint64 }},
		{"started", func(s *campaignState) { s.report.Started = math.MaxUint64 }},
		{"finished", func(s *campaignState) { s.report.Finished = math.MaxUint64 }},
		{"not_started", func(s *campaignState) { s.report.NotStarted = math.MaxUint64 }},
		{"delivered", func(s *campaignState) { s.report.Delivered = math.MaxUint64 }},
		{"failures", func(s *campaignState) { s.report.Failures = math.MaxUint64 }},
		{"errors", func(s *campaignState) { s.report.Errors = math.MaxUint64 }},
	}
	for i, tc := range setters {
		t.Run(tc.name, func(t *testing.T) {
			model := lcWireModel()
			state := lcWireInitial(model)
			tc.set(&state)
			encoded, err := lcEncodeCore(state)
			if err != nil {
				t.Fatalf("u64 encoding rejected: %v", err)
			}
			if got := binary.LittleEndian.Uint64(encoded[i*8 : (i+1)*8]); got != math.MaxUint64 {
				t.Fatalf("encoded count = %d", got)
			}
			if _, err = lcDecodeCore(model, encoded); err == nil {
				t.Fatal("accepted impossible full-width count")
			}
		})
	}
}

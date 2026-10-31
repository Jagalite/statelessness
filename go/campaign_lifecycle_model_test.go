package statelessness

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"math"
	"math/big"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"
	"unicode/utf8"
)

// The oracle records observable protocol facts, not a second scheduler. History
// is part of state identity, with no unbounded event count on inert self-loops.
// This fixture qualifies neither threads/channels nor clocks or application code.
const (
	lcClaimed byte = 1 << iota
	lcCompleted
	lcReceived
	lcAttempted
	lcReturned
	lcSucceeded
	lcDiscarded
)

type lcFact struct {
	flags   byte
	summary campaignSummary
}
type lcMessage struct {
	present bool
	text    string
}
type lcLedger struct {
	facts                                                          map[uint64]lcFact
	firstSink, lastWorker                                          lcMessage
	cancelled, deadline, failureStop, lostWork, invalid, finalized bool
}
type lcAuditedState struct {
	core   campaignState
	ledger lcLedger
}

func (l lcLedger) stopped() bool {
	return l.cancelled || l.deadline || l.failureStop || l.firstSink.present || l.lastWorker.present
}

// Admission uses only independently observed claims, receipts, sink returns,
// external-stop events and genuine worker-loss events. Never consult core flags,
// pending state, counters or disposition when deciding what was permitted.
func lcLedgerAdmissible(m campaignLifecycle, l lcLedger, input campaignEvent) bool {
	if l.finalized {
		return false
	}
	claims, receipts, active := uint64(0), uint64(0), uint64(0)
	for _, fact := range l.facts {
		if fact.flags&lcClaimed != 0 {
			claims++
		}
		if fact.flags&lcReceived != 0 {
			receipts++
		}
		if fact.flags&lcAttempted != 0 && fact.flags&lcReturned == 0 {
			active++
		}
	}
	f := l.facts[input.jobID]
	switch input.kind {
	case campaignClaim:
		return !l.stopped() && claims < m.jobs && (input.stop == campaignNoStop || input.stop == campaignStopCancelled || input.stop == campaignStopDeadline)
	case campaignComplete:
		return f.flags&lcClaimed != 0 && f.flags&lcCompleted == 0 && lcSummaryValid(input.summary)
	case campaignReceive:
		return active == 0 && f.flags&lcCompleted != 0 && f.flags&lcReceived == 0
	case campaignSinkReturned:
		return f.flags&lcAttempted != 0 && f.flags&lcReturned == 0
	case campaignWorkerFailed:
		return true
	case campaignFinish:
		return active == 0 && (claims == receipts || l.lostWork) && (l.stopped() || claims == m.jobs)
	default:
		return false
	}
}

func lcAuditTransitionChecks(m campaignLifecycle, before lcAuditedState, input campaignEvent, tr Transition[lcAuditedState, campaignEffect]) []Check {
	checks := lcTransitionChecks(m, before.core, input, Transition[campaignState, campaignEffect]{State: tr.State.core, Outputs: tr.Outputs, Disposition: tr.Disposition})
	return append(checks, lcCheck("audit.admission", (tr.Disposition.Kind == "accepted") == lcLedgerAdmissible(m, before.ledger, input)))
}
func (s lcAuditedState) clone() lcAuditedState {
	s.core = s.core.clone()
	if s.core.report.Termination.Error != nil {
		s.core.report.Termination.Error = errors.New(s.core.report.Termination.Error.Error())
	}
	facts := make(map[uint64]lcFact, len(s.ledger.facts))
	for id, f := range s.ledger.facts {
		facts[id] = f
	}
	s.ledger.facts = facts
	return s
}
func lcCloneEvent(i campaignEvent) campaignEvent {
	if i.err != nil {
		i.err = errors.New(i.err.Error())
	}
	return i
}
func lcAuditedEqual(a, b lcAuditedState) bool {
	return lcStateEqual(a.core, b.core) && reflect.DeepEqual(a.ledger, b.ledger)
}
func lcIDs[V any](values map[uint64]V) []uint64 {
	ids := make([]uint64, 0, len(values))
	for id := range values {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	return ids
}

func lcObserve(m campaignLifecycle, before lcAuditedState, input campaignEvent, actual Transition[campaignState, campaignEffect]) Transition[lcAuditedState, campaignEffect] {
	state := before.clone()
	state.core = actual.State
	l := &state.ledger
	l.invalid = l.invalid || (actual.Disposition.Kind == "accepted") != lcLedgerAdmissible(m, before.ledger, input)
	if actual.Disposition.Kind == "accepted" {
		f := l.facts[input.jobID]
		switch input.kind {
		case campaignClaim:
			l.cancelled = l.cancelled || input.stop == campaignStopCancelled
			l.deadline = l.deadline || input.stop == campaignStopDeadline
		case campaignComplete:
			l.invalid = l.invalid || f.flags&lcClaimed == 0 || f.flags&lcCompleted != 0
			f.flags |= lcCompleted
			f.summary = input.summary
			l.facts[input.jobID] = f
			l.cancelled = l.cancelled || input.summary.outcome == campaignCancelled
			l.deadline = l.deadline || input.summary.outcome == campaignDeadline
			l.failureStop = l.failureStop || (m.stopOnFailure && input.summary.outcome == campaignFailure)
		case campaignReceive:
			l.invalid = l.invalid || f.flags&lcCompleted == 0 || f.flags&lcReceived != 0
			f.flags |= lcReceived
			l.facts[input.jobID] = f
		case campaignSinkReturned:
			l.invalid = l.invalid || f.flags&lcAttempted == 0 || f.flags&lcReturned != 0
			f.flags |= lcReturned
			if input.err != nil {
				if !l.firstSink.present {
					l.firstSink = lcMessage{true, input.err.Error()}
				}
			} else {
				l.invalid = l.invalid || f.flags&lcSucceeded != 0
				f.flags |= lcSucceeded
			}
			l.facts[input.jobID] = f
		case campaignWorkerFailed:
			l.lastWorker = lcMessage{true, input.err.Error()}
			l.lostWork = l.lostWork || input.lostWork
		case campaignFinish:
			l.finalized = true
		}
	}
	for _, effect := range actual.Outputs {
		f := l.facts[effect.jobID]
		switch effect.kind {
		case campaignRunJob:
			l.invalid = l.invalid || f.flags&lcClaimed != 0 || before.ledger.stopped() || before.ledger.finalized || effect.jobID < m.firstJob || effect.jobID-m.firstJob >= uint64(m.jobs)
			f.flags |= lcClaimed
		case campaignDeliver:
			l.invalid = l.invalid || f.flags&lcReceived == 0 || f.flags&lcAttempted != 0 || l.firstSink.present || f.flags&lcDiscarded != 0
			f.flags |= lcAttempted
		case campaignDiscard:
			l.invalid = l.invalid || f.flags&lcReceived == 0 || f.flags&lcDiscarded != 0 || !l.firstSink.present || f.flags&lcAttempted != 0
			f.flags |= lcDiscarded
		default:
			l.invalid = true
		}
		l.facts[effect.jobID] = f
	}
	return Transition[lcAuditedState, campaignEffect]{State: state, Outputs: actual.Outputs, Disposition: actual.Disposition}
}

func lcAuditChecks(m campaignLifecycle, s lcAuditedState) []Check {
	checks := lcStateChecks(m, s.core)
	l, r := s.ledger, s.core.report
	claims, receipts, successes, failures, jobErrors := uint64(0), uint64(0), uint64(0), uint64(0), uint64(0)
	var sums [3]big.Int
	complete := !l.lostWork
	activeOK, destinationsOK := true, true
	active := 0
	for id, f := range l.facts {
		if f.flags&lcClaimed != 0 {
			claims++
		}
		if f.flags&lcSucceeded != 0 {
			successes++
		}
		if f.flags&lcReturned != 0 && f.flags&lcAttempted == 0 {
			activeOK = false
		}
		if f.flags&lcAttempted != 0 && f.flags&lcReturned == 0 {
			active++
			activeOK = activeOK && s.core.hasDelivery && s.core.delivering == id
		}
		attempt, discard, receipt := f.flags&lcAttempted != 0, f.flags&lcDiscarded != 0, f.flags&lcReceived != 0
		destinationsOK = destinationsOK && !(attempt && discard) && receipt == (attempt || discard)
		if receipt {
			receipts++
			if f.summary.failure {
				failures++
			}
			if f.summary.outcome == campaignFactoryError || f.summary.outcome == campaignModelError || f.summary.outcome == campaignPanicked {
				jobErrors++
			}
			complete = complete && f.summary.outcome != campaignModelError && f.summary.outcome != campaignPanicked
			// Wide independent arithmetic: deliberately do not reuse production's
			// checked-add/saturation implementation as the accounting oracle.
			for n, v := range []uint64{f.summary.transitions, f.summary.cases, f.summary.skippedChecks} {
				var term big.Int
				sums[n].Add(&sums[n], term.SetUint64(v))
			}
		}
		if l.finalized && (f.flags&lcClaimed != 0) != receipt {
			complete = false
		}
	}
	wantActive := 0
	if s.core.hasDelivery {
		wantActive = 1
	}
	activeOK = activeOK && active == wantActive
	var totals [3]uint64
	for n := range sums {
		if sums[n].BitLen() > 64 {
			totals[n] = math.MaxUint64
			complete = false
		} else {
			totals[n] = sums[n].Uint64()
		}
	}
	wantTermination := CampaignTermination{Kind: "jobs_completed"}
	switch {
	case l.firstSink.present:
		wantTermination = CampaignTermination{Kind: "callback_error", Error: errors.New(l.firstSink.text)}
	case l.lastWorker.present:
		wantTermination = CampaignTermination{Kind: "worker_error", Error: errors.New(l.lastWorker.text)}
	case l.cancelled:
		wantTermination.Kind = "cancelled"
	case l.deadline:
		wantTermination.Kind = "deadline"
	case l.failureStop:
		wantTermination.Kind = "failure_found"
	}
	return append(checks,
		lcCheck("audit.protocol", !l.invalid),
		lcCheck("audit.worker_loss", s.core.lostWorker == l.lostWork),
		lcCheck("audit.finalized", s.core.finalized == l.finalized),
		lcCheck("audit.stop_observed", (s.core.stop != campaignNoStop) == l.stopped()),
		lcCheck("audit.active_sink", activeOK),
		lcCheck("audit.identity_accounting", r.Started == claims && r.Finished == receipts && r.Delivered == successes && r.Failures == failures && r.Errors == jobErrors),
		lcCheck("audit.work_accounting", r.ReportedTransitions == totals[0] && r.ReportedCases == totals[1] && r.ReportedSkippedChecks == totals[2] && r.AccountingComplete == complete),
		lcCheck("audit.receipt_has_one_destination", destinationsOK),
		lcCheck("audit.stop_precedence", !l.finalized || lcTerminationEqual(r.Termination, wantTermination)),
	)
}

func lcAlphabet(m campaignLifecycle) []campaignEvent {
	inputs := []campaignEvent{
		{kind: campaignClaim}, {kind: campaignClaim, stop: campaignStopCancelled}, {kind: campaignClaim, stop: campaignStopDeadline},
		{kind: campaignWorkerFailed, err: errors.New("worker")}, {kind: campaignWorkerFailed, err: errors.New("worker"), lostWork: true}, {kind: campaignFinish},
	}
	// Includes one never-allocated identity; stale/duplicate events are always
	// enabled as inputs, although the reducer must reject them inertly.
	for offset := uint64(0); offset <= m.jobs; offset++ {
		if uint64(offset) > math.MaxUint64-m.firstJob {
			break
		}
		id := m.firstJob + uint64(offset)
		for _, o := range []campaignOutcome{campaignCompleted, campaignFailure, campaignCancelled, campaignDeadline, campaignFactoryError, campaignModelError, campaignPanicked} {
			inputs = append(inputs, lcComplete(id, o))
		}
		// The codec admits these syntactically valid summaries; the decision
		// law must reject them. They add only inert self-loops to healthy BFS,
		// and the fuzz generator's hostile branch exercises the same alphabet.
		for _, invalid := range []campaignSummary{
			{outcome: campaignCompleted, failure: true}, {outcome: campaignFailure},
			{outcome: campaignFactoryError, transitions: 1}, {outcome: campaignModelError, cases: 1},
			{outcome: campaignPanicked, skippedChecks: 1},
		} {
			inputs = append(inputs, campaignEvent{kind: campaignComplete, jobID: id, summary: invalid})
		}
		inputs = append(inputs, campaignEvent{kind: campaignReceive, jobID: id}, lcSink(id, nil), lcSink(id, errors.New("sink")))
	}
	return inputs
}

// This is an explicitly versioned private evidence format. The core prefix is
// Rust-compatible; the ledger suffix belongs to this Go self-model only. Its
// canonical ordered facts preserve oracle history in equality and trace state.
// No codec is installed on production CampaignConfig/JobReport/CampaignReport.
func lcEncodeAudited(s lcAuditedState) ([]byte, error) {
	core, e := lcEncodeCore(s.core)
	if e != nil {
		return nil, e
	}
	b := binary.LittleEndian.AppendUint64(nil, uint64(len(core)))
	b = append(b, core...)
	b = binary.LittleEndian.AppendUint64(b, uint64(len(s.ledger.facts)))
	for _, id := range lcIDs(s.ledger.facts) {
		f := s.ledger.facts[id]
		b = binary.LittleEndian.AppendUint64(b, id)
		b = append(b, f.flags)
		if f.flags&lcCompleted != 0 {
			encoded, e := lcEncodeEvent(campaignEvent{kind: campaignComplete, jobID: id, summary: f.summary})
			if e != nil {
				return nil, e
			}
			b = append(b, encoded[9:]...) // fixed 26-byte summary, no duplicate identity
		}
	}
	for _, message := range []lcMessage{s.ledger.firstSink, s.ledger.lastWorker} {
		if !message.present {
			b = append(b, 0)
			continue
		}
		if !utf8.ValidString(message.text) {
			return nil, errors.New("invalid ledger error UTF-8")
		}
		b = append(b, 1)
		b = binary.LittleEndian.AppendUint64(b, uint64(len(message.text)))
		b = append(b, []byte(message.text)...)
	}
	for _, flag := range []bool{s.ledger.cancelled, s.ledger.deadline, s.ledger.failureStop, s.ledger.lostWork, s.ledger.invalid, s.ledger.finalized} {
		if flag {
			b = append(b, 1)
		} else {
			b = append(b, 0)
		}
	}
	return b, nil
}

type lcAuditReader struct {
	bytes []byte
	err   error
}

func (r *lcAuditReader) take(n uint64) []byte {
	if r.err != nil {
		return nil
	}
	if n > uint64(len(r.bytes)) {
		r.err = errors.New("truncated lifecycle ledger")
		return nil
	}
	b := r.bytes[:int(n)]
	r.bytes = r.bytes[int(n):]
	return b
}
func (r *lcAuditReader) number() uint64 {
	b := r.take(8)
	if len(b) != 8 {
		return 0
	}
	return binary.LittleEndian.Uint64(b)
}
func (r *lcAuditReader) tag() byte {
	b := r.take(1)
	if len(b) == 0 {
		return 0
	}
	return b[0]
}
func (r *lcAuditReader) flag() bool {
	b := r.tag()
	if b > 1 {
		r.err = errors.New("invalid ledger boolean")
	}
	return b == 1
}
func (r *lcAuditReader) message() lcMessage {
	if !r.flag() {
		return lcMessage{}
	}
	b := r.take(r.number())
	if !utf8.Valid(b) {
		r.err = errors.New("invalid ledger error UTF-8")
	}
	return lcMessage{true, string(b)}
}
func lcDecodeAudited(m campaignLifecycle, b []byte) (lcAuditedState, error) {
	r := lcAuditReader{bytes: b}
	coreBytes := r.take(r.number())
	if r.err != nil {
		return lcAuditedState{}, r.err
	}
	core, e := lcDecodeCore(m, coreBytes)
	if e != nil {
		return lcAuditedState{}, e
	}
	s := lcAuditedState{core: core, ledger: lcLedger{facts: map[uint64]lcFact{}}}
	n := r.number()
	if n > uint64(m.jobs) || n > uint64(len(r.bytes)/9) {
		return lcAuditedState{}, errors.New("invalid ledger entry count")
	}
	var previous uint64
	for j := uint64(0); j < n; j++ {
		id, flags := r.number(), r.tag()
		if (j > 0 && id <= previous) || id < m.firstJob || id-m.firstJob >= uint64(m.jobs) || flags == 0 || flags&0x80 != 0 {
			return lcAuditedState{}, errors.New("invalid ledger entry")
		}
		previous = id
		f := lcFact{flags: flags}
		if flags&lcCompleted != 0 {
			eventBytes := binary.LittleEndian.AppendUint64([]byte{byte(campaignComplete)}, id)
			eventBytes = append(eventBytes, r.take(26)...)
			i, e := lcDecodeEvent(eventBytes)
			if e != nil {
				return lcAuditedState{}, e
			}
			f.summary = i.summary
		}
		s.ledger.facts[id] = f
	}
	s.ledger.firstSink, s.ledger.lastWorker = r.message(), r.message()
	s.ledger.cancelled, s.ledger.deadline, s.ledger.failureStop, s.ledger.lostWork, s.ledger.invalid, s.ledger.finalized = r.flag(), r.flag(), r.flag(), r.flag(), r.flag(), r.flag()
	if r.err != nil {
		return lcAuditedState{}, r.err
	}
	if len(r.bytes) != 0 {
		return lcAuditedState{}, errors.New("trailing lifecycle ledger bytes")
	}
	// A failing ledger must remain encodable/decodable: persisted fault evidence
	// contains audit.protocol=false. Parsing cannot erase those counterexamples.
	return s, nil
}

func lcSelfModel(m campaignLifecycle, duplicate bool) GenerateModel[lcAuditedState, campaignEvent, campaignEffect] {
	metadata := Metadata{Name: fmt.Sprintf("go-campaign-ledger:%d:%d:%t", m.firstJob, m.jobs, m.stopOnFailure), ModelVersion: 1, PropertiesVersion: 3, CodecVersion: 2, Build: "go-campaign-lifecycle-fixture-v2"}
	if duplicate {
		metadata.Build += ":duplicate-delivery"
	}
	model := Model[lcAuditedState, campaignEvent, campaignEffect]{
		InitialState: func() (lcAuditedState, error) {
			core, e := m.initial()
			return lcAuditedState{core: core, ledger: lcLedger{facts: map[uint64]lcFact{}}}, e
		},
		Step: func(before lcAuditedState, input campaignEvent) (Transition[lcAuditedState, campaignEffect], error) {
			actual := m.step(before.core, input)
			if duplicate && len(actual.Outputs) > 0 && actual.Outputs[0].kind == campaignDeliver {
				actual.Outputs = append(actual.Outputs, actual.Outputs[0])
			}
			return lcObserve(m, before, input, actual), nil
		},
		CheckState: func(s lcAuditedState) ([]Check, error) { return lcAuditChecks(m, s), nil },
		CheckTransition: func(b lcAuditedState, i campaignEvent, tr Transition[lcAuditedState, campaignEffect]) ([]Check, error) {
			return lcAuditTransitionChecks(m, b, i, tr), nil
		},
		CloneState: func(s lcAuditedState) lcAuditedState { return s.clone() },
		CloneInput: lcCloneEvent, CloneOutput: ValueCopy[campaignEffect],
		EqualStates: lcAuditedEqual,
		HashState: func(s lcAuditedState) uint64 {
			b, e := lcEncodeAudited(s)
			if e != nil {
				panic(e)
			}
			h := uint64(14695981039346656037)
			for _, v := range b {
				h = (h ^ uint64(v)) * 1099511628211
			}
			return h
		},
		Inputs: func(s lcAuditedState) (Iterator[campaignEvent], error) {
			if s.core.finalized {
				return Values([]campaignEvent{}), nil
			}
			return Values(lcAlphabet(m)), nil
		},
		Codec: &Codec[lcAuditedState, campaignEvent, campaignEffect]{
			Metadata: func() (Metadata, error) { return metadata, nil }, EncodeState: lcEncodeAudited,
			DecodeState: func(b []byte) (lcAuditedState, error) { return lcDecodeAudited(m, b) },
			EncodeInput: lcEncodeEvent, DecodeInput: lcDecodeEvent, EncodeOutput: lcEncodeEffect,
		},
	}
	g := GenerateModel[lcAuditedState, campaignEvent, campaignEffect]{Model: model, InputEqual: func(a, b campaignEvent) (bool, error) { return lcEventEqual(a, b), nil }}
	// Do not use Auto: it would disable stale noise/post-finalization inputs and
	// make the deliberate rejected-acknowledgement shrink prefix inadmissible.
	g.Generate = func(s lcAuditedState, rng *Rng) (campaignEvent, bool, error) {
		var choices []campaignEvent
		if !s.core.finalized {
			draw, _ := rng.Index(4)
			if draw != 0 {
				if s.core.stop == campaignNoStop && s.core.report.Started < m.jobs {
					choices = append(choices, campaignEvent{kind: campaignClaim})
				}
				for _, id := range lcIDs(s.core.pending) {
					p := s.core.pending[id]
					if !p.ready {
						for _, o := range []campaignOutcome{campaignCompleted, campaignFailure, campaignCancelled, campaignDeadline, campaignFactoryError, campaignModelError, campaignPanicked} {
							choices = append(choices, lcComplete(id, o))
						}
					} else if !s.core.hasDelivery {
						choices = append(choices, campaignEvent{kind: campaignReceive, jobID: id})
					}
				}
				if s.core.hasDelivery {
					choices = append(choices, lcSink(s.core.delivering, nil), lcSink(s.core.delivering, errors.New("sink")))
				} else if len(s.core.pending) == 0 {
					choices = append(choices, campaignEvent{kind: campaignFinish})
				}
			}
		}
		if len(choices) == 0 {
			choices = lcAlphabet(m)
		}
		index, ok := rng.Index(uint64(len(choices)))
		if !ok {
			return campaignEvent{}, false, nil
		}
		return choices[int(index)], true, nil
	}
	return g
}

func lcAuditedPrefix(t *testing.T, m campaignLifecycle, inputs []campaignEvent) lcAuditedState {
	t.Helper()
	g := lcSelfModel(m, false)
	s, e := g.InitialState()
	if e != nil {
		t.Fatal(e)
	}
	for _, i := range inputs {
		tr, e := g.Step(s, i)
		if e != nil {
			t.Fatal(e)
		}
		if tr.Disposition.Kind != "accepted" {
			t.Fatalf("prefix rejected %+v", i)
		}
		lcMustPass(t, lcAuditChecks(m, tr.State))
		lcMustPass(t, lcAuditTransitionChecks(m, s, i, tr))
		s = tr.State
	}
	return s
}

func TestCampaignLifecycleIndependentOracleNegativeControls(t *testing.T) {
	m := campaignLifecycle{firstJob: 50, jobs: 2, stopOnFailure: true}
	running := []campaignEvent{{kind: campaignClaim}}
	ready := append(append([]campaignEvent{}, running...), lcComplete(50, campaignCompleted))
	delivering := append(append([]campaignEvent{}, ready...), campaignEvent{kind: campaignReceive, jobID: 50})
	delivered := append(append([]campaignEvent{}, delivering...), lcSink(50, nil))
	for _, tc := range []struct {
		prefix []campaignEvent
		input  campaignEvent
	}{
		{nil, campaignEvent{kind: campaignClaim}}, {nil, campaignEvent{kind: campaignClaim, stop: campaignStopCancelled}}, {nil, campaignEvent{kind: campaignClaim, stop: campaignStopDeadline}},
		{running, lcComplete(50, campaignCompleted)}, {ready, campaignEvent{kind: campaignReceive, jobID: 50}}, {delivering, lcSink(50, nil)}, {delivering, lcSink(50, errors.New("sink"))},
		{nil, campaignEvent{kind: campaignWorkerFailed, err: errors.New("worker")}},
		{[]campaignEvent{{kind: campaignClaim, stop: campaignStopCancelled}}, campaignEvent{kind: campaignFinish}},
	} {
		b := lcAuditedPrefix(t, m, tc.prefix)
		if m.step(b.core, tc.input).Disposition.Kind != "accepted" {
			t.Fatal("negative control premise is not enabled")
		}
		fake := Transition[campaignState, campaignEffect]{State: b.core.clone(), Disposition: Rejected("injected false rejection")}
		lcMustFail(t, lcTransitionChecks(m, b.core, tc.input, fake), "campaign.admission")
	}
	for _, otherActive := range []bool{false, true} {
		prefix := append([]campaignEvent{}, delivered...)
		if otherActive {
			prefix = append(prefix, campaignEvent{kind: campaignClaim}, lcComplete(51, campaignCompleted), campaignEvent{kind: campaignReceive, jobID: 51})
		}
		b := lcAuditedPrefix(t, m, prefix)
		i := lcSink(50, errors.New("stale error"))
		fake := b.core.clone()
		fake.report.Termination = CampaignTermination{Kind: "callback_error", Error: i.err}
		fake.stop = campaignStopSink
		fake.hasDelivery = false
		fake.delivering = 0
		tr := lcObserve(m, b, i, Accept[campaignState, campaignEffect](fake))
		lcMustFail(t, lcAuditChecks(m, tr.State), "audit.protocol")
		lcMustFail(t, lcTransitionChecks(m, b.core, i, Accept[campaignState, campaignEffect](fake)), "campaign.admission")
		if otherActive {
			lcMustFail(t, lcAuditChecks(m, tr.State), "audit.active_sink")
		}
	}
	// Observe a corrupted transition directly; the ledger must catch duplicate
	// delivery without relying on campaign.effects or a second reducer call.
	b := lcAuditedPrefix(t, m, ready)
	i := campaignEvent{kind: campaignReceive, jobID: 50}
	actual := m.step(b.core, i)
	actual.Outputs = append(actual.Outputs, actual.Outputs[0])
	fault := lcObserve(m, b, i, actual)
	lcMustFail(t, lcAuditChecks(m, fault.State), "audit.protocol")
	for _, tc := range []struct {
		prefix []campaignEvent
		input  campaignEvent
	}{
		{nil, campaignEvent{kind: campaignClaim, stop: campaignStopCancelled}}, {nil, campaignEvent{kind: campaignClaim, stop: campaignStopDeadline}},
		{running, lcComplete(50, campaignFailure)}, {running, lcComplete(50, campaignCancelled)}, {running, lcComplete(50, campaignDeadline)},
		{nil, campaignEvent{kind: campaignWorkerFailed, err: errors.New("worker")}}, {delivering, lcSink(50, errors.New("sink"))},
	} {
		b := lcAuditedPrefix(t, m, tc.prefix)
		actual := m.step(b.core, tc.input)
		actual.State.stop = campaignNoStop
		fault := lcObserve(m, b, tc.input, actual)
		lcMustFail(t, lcAuditChecks(m, fault.State), "audit.stop_observed")
		claim := campaignEvent{kind: campaignClaim}
		extra := m.step(fault.State.core, claim)
		if extra.Disposition.Kind != "accepted" {
			t.Fatal("forgotten stop control cannot claim")
		}
		lcMustFail(t, lcAuditChecks(m, lcObserve(m, fault.State, claim, extra).State), "audit.protocol")
	}
	// An accepted Finish that silently leaves a fully delivered campaign live
	// would satisfy the old terminal-immutability check. Detect that missing
	// transition even when its report already says jobs_completed.
	one := campaignLifecycle{firstJob: 50, jobs: 1}
	beforeFinish := lcAuditedPrefix(t, one, delivered)
	finish := campaignEvent{kind: campaignFinish}
	forgotFinish := Accept[campaignState, campaignEffect](beforeFinish.core.clone())
	lcMustFail(t, lcTransitionChecks(one, beforeFinish.core, finish, forgotFinish), "campaign.finish_is_observed")
	// The converse is equally invalid: an unrelated accepted event cannot end
	// the campaign early while all the accounting counters remain coherent.
	initial := lcAuditedPrefix(t, one, nil)
	stop := campaignEvent{kind: campaignClaim, stop: campaignStopCancelled}
	early := one.step(initial.core, stop)
	early.State.finalized = true
	lcMustFail(t, lcTransitionChecks(one, initial.core, stop, early), "campaign.finish_is_observed")
}

func TestCampaignLifecycleFiniteGraphAndFuzz(t *testing.T) {
	for _, stop := range []bool{false, true} {
		g := lcSelfModel(campaignLifecycle{firstJob: 50, jobs: 2, stopOnFailure: stop}, false)
		// The 51-event alphabet adds malformed-summary self-loops to Rust's
		// original 36; keep the state/depth bounds and budget every added edge.
		r, e := Enumerate(g.Model, SearchConfig{MaxStates: 200000, MaxTransitions: 12000000, MaxDepth: 20})
		if e != nil {
			t.Fatal(e)
		}
		if r.Termination != "graph_exhausted" || r.Failure != nil || r.SkippedChecks != 0 {
			t.Fatalf("stop=%t BFS %+v", stop, r)
		}
		t.Logf("campaign ledger BFS stop=%t states=%d transitions=%d depth=%d", stop, r.States, r.Transitions, r.MaxDepthReached)
		g = lcSelfModel(campaignLifecycle{firstJob: 50, jobs: 4, stopOnFailure: stop}, false)
		f, e := Fuzz(g, FuzzConfig{Seed: 0x53544154454c4553, Cases: 256, MaxSteps: 64, MaxTransitions: 16384, MutationPercent: 50})
		if e != nil {
			t.Fatal(e)
		}
		if f.Termination != "cases_completed" || f.Failure != nil || f.SkippedChecks != 0 {
			t.Fatalf("stop=%t fuzz %+v", stop, f)
		}
		t.Logf("campaign ledger fuzz stop=%t cases=%d transitions=%d", stop, f.Cases, f.Transitions)
	}
}

func TestCampaignLifecycleLedgerRejectsInventedWorkerLoss(t *testing.T) {
	m := campaignLifecycle{firstJob: 50, jobs: 2}
	before := lcAuditedPrefix(t, m, []campaignEvent{
		{kind: campaignClaim}, {kind: campaignClaim}, lcComplete(50, campaignModelError),
		{kind: campaignReceive, jobID: 50}, lcSink(50, nil),
	})
	if before.core.report.AccountingComplete {
		t.Fatal("premise requires earlier hidden work")
	}
	worker := campaignEvent{kind: campaignWorkerFailed, err: errors.New("start failure"), lostWork: false}
	actual := m.step(before.core, worker)
	actual.State.lostWorker = true // deliberate defect; no genuine lost event
	fault := lcObserve(m, before, worker, actual)
	if fault.State.ledger.lostWork {
		t.Fatal("oracle incorrectly copied core loss")
	}
	lcMustFail(t, lcAuditChecks(m, fault.State), "audit.worker_loss")
	finish := campaignEvent{kind: campaignFinish}
	earlyCore := m.step(fault.State.core, finish)
	if earlyCore.Disposition.Kind != "accepted" {
		t.Fatal("injected flag must enable the erroneous finish")
	}
	early := lcObserve(m, fault.State, finish, earlyCore)
	lcMustFail(t, lcAuditTransitionChecks(m, fault.State, finish, early), "audit.admission")
	lcMustFail(t, lcAuditChecks(m, early.State), "audit.protocol")
	// The independent Finish gate also rejects an unacknowledged sink even when
	// a genuine worker-loss event permits missing receipts.
	active := lcAuditedPrefix(t, m, []campaignEvent{{kind: campaignClaim}, lcComplete(50, campaignCompleted), {kind: campaignReceive, jobID: 50}, {kind: campaignWorkerFailed, err: errors.New("lost"), lostWork: true}})
	if lcLedgerAdmissible(m, active.ledger, finish) {
		t.Fatal("Finish ignored outstanding sink return")
	}
}

func TestCampaignLifecycleLedgerSaturationAndWorkerPrecedence(t *testing.T) {
	// Saturation and the MAX+0 boundary are checked by the event ledger's wide
	// arithmetic as well as by the focused reducer assertions.
	for field := 0; field < 3; field++ {
		m := campaignLifecycle{firstJob: 50, jobs: 3}
		var prefix []campaignEvent
		for n, value := range []uint64{math.MaxUint64, 0, 1} {
			sum := campaignSummary{outcome: campaignCompleted}
			switch field {
			case 0:
				sum.transitions = value
			case 1:
				sum.cases = value
			case 2:
				sum.skippedChecks = value
			}
			id := 50 + uint64(n)
			prefix = append(prefix, campaignEvent{kind: campaignClaim}, campaignEvent{kind: campaignComplete, jobID: id, summary: sum}, campaignEvent{kind: campaignReceive, jobID: id}, lcSink(id, nil))
			s := lcAuditedPrefix(t, m, prefix)
			if s.core.report.AccountingComplete != (n < 2) {
				t.Fatalf("field=%d prefix=%d: %+v", field, n, s.core.report)
			}
		}
		lcAuditedPrefix(t, m, append(prefix, campaignEvent{kind: campaignFinish}))
	}
	// Infrastructure priority is independent of ordinary completion order. A
	// worker error still delivers the job and a later worker error replaces it.
	for _, outcome := range []campaignOutcome{campaignCompleted, campaignFailure, campaignCancelled, campaignDeadline, campaignFactoryError, campaignModelError, campaignPanicked} {
		for _, workerBefore := range []bool{false, true} {
			m := campaignLifecycle{firstJob: 50, jobs: 2, stopOnFailure: true}
			prefix := []campaignEvent{{kind: campaignClaim}}
			worker := campaignEvent{kind: campaignWorkerFailed, err: errors.New("first worker")}
			if workerBefore {
				prefix = append(prefix, worker)
			}
			prefix = append(prefix, lcComplete(50, outcome))
			if !workerBefore {
				prefix = append(prefix, worker)
			}
			prefix = append(prefix, campaignEvent{kind: campaignReceive, jobID: 50}, lcSink(50, nil), campaignEvent{kind: campaignWorkerFailed, err: errors.New("last worker")}, campaignEvent{kind: campaignFinish})
			s := lcAuditedPrefix(t, m, prefix)
			if s.core.report.Delivered != 1 || s.core.report.Termination.Kind != "worker_error" || s.core.report.Termination.Error.Error() != "last worker" {
				t.Fatalf("outcome=%s before=%t: %+v", outcome, workerBefore, s.core.report)
			}
		}
	}
}

func lcRoundtripTrace(t *testing.T, trace *AuditTrace) *AuditTrace {
	t.Helper()
	b, e := MarshalAuditTrace(trace, DefaultBinaryLimits())
	if e != nil {
		t.Fatal(e)
	}
	recovered, e := ReadAuditTrace(bytes.NewReader(b), DefaultBinaryLimits())
	if e != nil {
		t.Fatal(e)
	}
	if !reflect.DeepEqual(trace, recovered) {
		t.Fatal("binary audit changed trace")
	}
	return recovered
}
func lcAssertReplay(t *testing.T, g GenerateModel[lcAuditedState, campaignEvent, campaignEffect], trace *AuditTrace, allow bool, outcome string, failure, build bool) {
	t.Helper()
	r, e := Replay(g.Model, &trace.Trace, allow)
	if e != nil {
		t.Fatal(e)
	}
	if r.Outcome != outcome || r.FailureReproduced != failure || r.BuildMatches != build {
		t.Fatalf("replay: %+v", r)
	}
	if outcome == "exact" && r.StepsVerified != len(trace.Steps) {
		t.Fatalf("partial exact replay: %+v", r)
	}
}

func TestCampaignLifecycleDuplicateDeliveryFullFaultPipeline(t *testing.T) {
	m := campaignLifecycle{firstJob: 50, jobs: 2}
	faulty, healthy := lcSelfModel(m, true), lcSelfModel(m, false)
	found, e := Enumerate(faulty.Model, SearchConfig{MaxStates: 10000, MaxTransitions: 100000, MaxDepth: 10})
	if e != nil {
		t.Fatal(e)
	}
	if found.Termination != "failure_found" || found.Failure == nil {
		t.Fatalf("fault not found: %+v", found)
	}
	checks := []Check{}
	for _, v := range found.Failure.Violations {
		checks = append(checks, v.Check)
	}
	lcMustFail(t, checks, "audit.protocol")
	lcMustFail(t, checks, "campaign.effects")
	fuzzed, e := Fuzz(faulty, FuzzConfig{Seed: 0x53544154454c4553, Cases: 256, MaxSteps: 64, MaxTransitions: 16384, MutationPercent: 50})
	if e != nil {
		t.Fatal(e)
	}
	if fuzzed.Termination != "failure_found" || fuzzed.Failure == nil {
		t.Fatalf("fuzz missed injected fault: %+v", fuzzed)
	}
	checks = nil
	for _, v := range fuzzed.Failure.Violations {
		checks = append(checks, v.Check)
	}
	lcMustFail(t, checks, "audit.protocol")
	original := *found.Failure
	// Shrink selects Violations[0]. State checks run first, so explicitly put
	// the source contract's transition/campaign.effects target first while
	// preserving the independent audit.protocol violation as another witness.
	original.Violations = append([]Violation{}, found.Failure.Violations...)
	target := -1
	for n, v := range original.Violations {
		if v.Phase == "transition" && v.Check.ID == "campaign.effects" {
			target = n
			break
		}
	}
	if target < 0 {
		t.Fatalf("missing transition effect target: %+v", original.Violations)
	}
	original.Violations[0], original.Violations[target] = original.Violations[target], original.Violations[0]
	original.Inputs = append([]campaignEvent{lcSink(999, nil), {kind: campaignClaim}}, original.Inputs...)
	shrunk, e := Shrink(faulty, original, ShrinkConfig{MaxAttempts: 1000})
	if e != nil {
		t.Fatal(e)
	}
	if !shrunk.ValidatedOriginal || shrunk.Termination != "search_complete" || len(shrunk.Minimized.Inputs) != 3 || len(shrunk.Minimized.Inputs) >= len(original.Inputs) {
		t.Fatalf("shrink: %+v", shrunk)
	}
	if shrunk.Minimized.Inputs[0].kind != campaignClaim || shrunk.Minimized.Inputs[1].kind != campaignComplete || shrunk.Minimized.Inputs[2].kind != campaignReceive {
		t.Fatalf("wrong causal witness: %+v", shrunk.Minimized.Inputs)
	}
	checks = nil
	for _, v := range shrunk.Minimized.Violations {
		checks = append(checks, v.Check)
	}
	lcMustFail(t, checks, "campaign.effects")
	lcMustFail(t, checks, "audit.protocol")
	artifacts := map[string]*AuditTrace{}
	for name, inputs := range map[string][]campaignEvent{"original": original.Inputs, "minimized": shrunk.Minimized.Inputs} {
		config := RunConfig{Strategy: "go-campaign-self-model-fault-control", Parameters: [][2]string{{"injected_fault", "duplicate Deliver"}}}
		trace, e := RecordAudit(faulty.Model, Values(inputs), config, 100, DefaultBinaryLimits())
		if e != nil {
			t.Fatal(e)
		}
		if trace.Termination != "property_failed" {
			t.Fatalf("recorded fault: %+v", trace)
		}
		recovered := lcRoundtripTrace(t, trace)
		lcAssertReplay(t, faulty, recovered, false, "exact", true, true)
		lcAssertReplay(t, healthy, recovered, false, "incompatible", false, false)
		lcAssertReplay(t, healthy, recovered, true, "diverged", false, false)
		artifacts[name] = trace
		// Checkpoints include the independent ledger, including its failing bit.
		for _, step := range recovered.Steps {
			state, e := faulty.Codec.DecodeState(step.PostState)
			if e != nil {
				t.Fatal(e)
			}
			encoded, e := faulty.Codec.EncodeState(state)
			if e != nil || !bytes.Equal(encoded, step.PostState) {
				t.Fatalf("checkpoint: %v", e)
			}
		}
	}
	// Optional parent-owned artifact directory. Exclusive creation preserves old
	// receipts; no default filesystem writes and no claim of fresh-process replay.
	if directory := os.Getenv("STATELESS_GO_CAMPAIGN_ARTIFACTS"); directory != "" {
		for name, trace := range artifacts {
			path := filepath.Join(directory, "campaign-fault-"+name+".sttrace")
			file, e := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if e != nil {
				t.Fatal(e)
			}
			writeErr := trace.WriteBinary(file)
			closeErr := file.Close()
			if writeErr != nil {
				t.Fatal(writeErr)
			}
			if closeErr != nil {
				t.Fatal(closeErr)
			}
			t.Logf("saved %s", path)
		}
	}
	t.Logf("duplicate Deliver: BFS %d transitions; fuzz %d transitions; shrink %d -> %d inputs in %d attempts", found.Transitions, fuzzed.Transitions, len(original.Inputs), len(shrunk.Minimized.Inputs), shrunk.Attempts)
}

func TestCampaignLifecyclePersistedFaultReplay(t *testing.T) {
	path := os.Getenv("STATELESS_GO_CAMPAIGN_REPLAY")
	if path == "" {
		t.Skip("fresh-process replay requires parent-supplied STATELESS_GO_CAMPAIGN_REPLAY")
	}
	file, e := os.Open(path)
	if e != nil {
		t.Fatal(e)
	}
	defer file.Close()
	trace, e := ReadAuditTrace(file, DefaultBinaryLimits())
	if e != nil {
		t.Fatal(e)
	}
	if trace.Termination != "property_failed" {
		t.Fatalf("not fault evidence: %s", trace.Termination)
	}
	if trace.Config.Strategy != "go-campaign-self-model-fault-control" || len(trace.Config.Parameters) != 3 || trace.Config.Parameters[0] != [2]string{"injected_fault", "duplicate Deliver"} || trace.Config.Parameters[1][0] != "stateless.engine.version" || trace.Config.Parameters[2][0] != "stateless.engine.build" {
		t.Fatalf("unexpected persisted configuration: %+v", trace.Config)
	}
	if len(trace.Steps) != 3 {
		t.Fatalf("unexpected persisted witness length: got %d, want 3", len(trace.Steps))
	}
	expected := []campaignEvent{{kind: campaignClaim}, lcComplete(50, campaignCompleted), {kind: campaignReceive, jobID: 50}}
	for i, step := range trace.Steps {
		want, err := lcEncodeEvent(expected[i])
		if err != nil || !bytes.Equal(step.Input, want) {
			t.Fatalf("unexpected persisted witness input %d: %v", i, err)
		}
	}
	lcMustFail(t, trace.Steps[2].Checks, "campaign.effects")
	m := campaignLifecycle{firstJob: 50, jobs: 2}
	lcAssertReplay(t, lcSelfModel(m, true), trace, false, "exact", true, true)
	lcAssertReplay(t, lcSelfModel(m, false), trace, false, "incompatible", false, false)
	lcAssertReplay(t, lcSelfModel(m, false), trace, true, "diverged", false, false)
}

func TestCampaignLifecycleHealthyAuditAndLedgerPersistence(t *testing.T) {
	m := campaignLifecycle{firstJob: 50, jobs: 2}
	g := lcSelfModel(m, false)
	inputs := []campaignEvent{{kind: campaignClaim}, {kind: campaignClaim}, lcComplete(51, campaignFactoryError), lcComplete(50, campaignFailure), {kind: campaignReceive, jobID: 51}, lcSink(51, nil), {kind: campaignReceive, jobID: 50}, lcSink(50, nil), {kind: campaignFinish}}
	trace, e := RecordAudit(g.Model, Values(inputs), RunConfig{Strategy: "go-campaign-self-model-healthy"}, 100, DefaultBinaryLimits())
	if e != nil {
		t.Fatal(e)
	}
	if trace.Termination != "completed" || len(trace.Steps) != 9 {
		t.Fatalf("healthy trace: %+v", trace)
	}
	recovered := lcRoundtripTrace(t, trace)
	lcAssertReplay(t, g, recovered, false, "exact", false, true)
	for _, encoded := range append([][]byte{trace.InitialState}, func() [][]byte {
		var b [][]byte
		for _, step := range trace.Steps {
			b = append(b, step.PostState)
		}
		return b
	}()...) {
		decoded, e := lcDecodeAudited(m, encoded)
		if e != nil {
			t.Fatal(e)
		}
		lcMustPass(t, lcAuditChecks(m, decoded))
		b, e := lcEncodeAudited(decoded)
		if e != nil || !bytes.Equal(b, encoded) {
			t.Fatalf("ledger roundtrip: %v", e)
		}
		for end := 0; end < len(encoded); end++ {
			if _, e := lcDecodeAudited(m, encoded[:end]); e == nil {
				t.Fatalf("accepted ledger prefix %d", end)
			}
		}
		if _, e := lcDecodeAudited(m, append(append([]byte{}, encoded...), 0)); e == nil {
			t.Fatal("accepted ledger suffix")
		}
	}
	s := lcAuditedPrefix(t, m, inputs[:3])
	detached := s.clone()
	delete(detached.core.pending, 50)
	delete(detached.ledger.facts, 50)
	if _, ok := s.core.pending[50]; !ok {
		t.Fatal("core clone aliases map")
	}
	if _, ok := s.ledger.facts[50]; !ok {
		t.Fatal("ledger clone aliases map")
	}
	other := s.clone()
	other.ledger.invalid = true
	if lcAuditedEqual(s, other) {
		t.Fatal("oracle history omitted from equality")
	}
}

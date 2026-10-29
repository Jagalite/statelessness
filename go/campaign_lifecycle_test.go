package statelessness

import (
	"errors"
	"math"
	"reflect"
	"testing"
)

// These assertions are deliberately independent of stepOwned. In particular,
// admission is derived from the predecessor and event, never from its verdict.
func lcCheck(id string, ok bool) Check {
	if ok {
		return Passed(id)
	}
	return Failed(id, "campaign lifecycle contract violated")
}

func lcErrorEqual(a, b error) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return a.Error() == b.Error()
}

func lcTerminationEqual(a, b CampaignTermination) bool {
	return a.Kind == b.Kind && lcErrorEqual(a.Error, b.Error)
}

func lcStateEqual(a, b campaignState) bool {
	if !lcTerminationEqual(a.report.Termination, b.report.Termination) {
		return false
	}
	a.report.Termination.Error, b.report.Termination.Error = nil, nil
	return reflect.DeepEqual(a, b)
}

func lcEventEqual(a, b campaignEvent) bool {
	if !lcErrorEqual(a.err, b.err) {
		return false
	}
	a.err, b.err = nil, nil
	return a == b
}

func lcSummaryValid(s campaignSummary) bool {
	if s.outcome == campaignFactoryError || s.outcome == campaignModelError || s.outcome == campaignPanicked {
		return !s.failure && (s.transitions|s.cases|s.skippedChecks) == 0
	}
	switch s.outcome {
	case campaignCompleted, campaignFailure, campaignCancelled, campaignDeadline:
		return s.failure == (s.outcome == campaignFailure)
	}
	return false
}

func lcStateChecks(m campaignLifecycle, s campaignState) []Check {
	r := s.report
	isError := r.Termination.Kind == "callback_error" || r.Termination.Kind == "worker_error"
	reasonOK := false
	switch r.Termination.Kind {
	case "callback_error":
		reasonOK = r.Termination.Error != nil && (s.stop == campaignStopSink || s.stop == campaignStopWorker || s.stop == campaignStopCancelled || s.stop == campaignStopDeadline)
	case "worker_error":
		reasonOK = r.Termination.Error != nil && (s.stop == campaignStopWorker || s.stop == campaignStopCancelled || s.stop == campaignStopDeadline)
	default:
		if !s.finalized {
			reasonOK = r.Termination.Kind == "jobs_completed" && s.stop != campaignStopSink && s.stop != campaignStopWorker
		} else {
			reasonOK = (s.stop == campaignNoStop && r.Termination.Kind == "jobs_completed") ||
				(s.stop == campaignStopFailure && r.Termination.Kind == "failure_found") ||
				(s.stop == campaignStopCancelled && r.Termination.Kind == "cancelled") ||
				(s.stop == campaignStopDeadline && r.Termination.Kind == "deadline")
		}
		reasonOK = reasonOK && r.Termination.Error == nil
	}
	validStop := s.stop == campaignNoStop || s.stop == campaignStopFailure || s.stop == campaignStopCancelled || s.stop == campaignStopDeadline || s.stop == campaignStopSink || s.stop == campaignStopWorker
	failureSeen := r.Failures > 0
	pendingOK := r.Started >= r.Finished && r.Finished >= 0 && r.Started-r.Finished == uint64(len(s.pending))
	for id, p := range s.pending {
		pendingOK = pendingOK && id >= m.firstJob && r.Started >= 0 && id-m.firstJob < uint64(r.Started) && (!p.ready || lcSummaryValid(p.summary))
		failureSeen = failureSeen || (p.ready && p.summary.failure)
	}
	active := uint64(0)
	if s.hasDelivery {
		active = 1
	}
	deliveryOK := r.Finished >= r.Delivered && r.Delivered >= 0 && r.Finished-r.Delivered == active
	if r.Termination.Kind == "callback_error" {
		deliveryOK = r.Finished > r.Delivered
	}
	if s.hasDelivery {
		_, pending := s.pending[s.delivering]
		deliveryOK = deliveryOK && !pending && s.delivering >= m.firstJob && r.Started >= 0 && s.delivering-m.firstJob < uint64(r.Started) && r.Delivered < r.Finished && r.Termination.Kind != "callback_error"
	}
	return []Check{
		lcCheck("campaign.accounting", r.Requested == m.jobs && r.Started >= 0 && r.Started <= r.Requested && r.NotStarted == r.Requested-r.Started && r.Finished >= 0 && r.Finished <= r.Started && r.Delivered >= 0 && r.Delivered <= r.Finished && r.Failures >= 0 && r.Failures <= r.Finished && r.Errors >= 0 && r.Errors <= r.Finished-r.Failures),
		lcCheck("campaign.pending", pendingOK),
		lcCheck("campaign.delivery", deliveryOK),
		lcCheck("campaign.finalized", !s.finalized || (!s.hasDelivery && (len(s.pending) == 0 || (s.lostWorker && !r.AccountingComplete)) && (r.Termination.Kind != "jobs_completed" || r.Started == r.Requested))),
		lcCheck("campaign.termination", reasonOK && validStop && (!s.lostWorker || (isError && !r.AccountingComplete)) && (s.stop != campaignStopFailure || (m.stopOnFailure && failureSeen))),
	}
}

func lcTransitionChecks(m campaignLifecycle, b campaignState, i campaignEvent, tr Transition[campaignState, campaignEffect]) []Check {
	a := tr.State
	accepted := tr.Disposition.Kind == "accepted"
	admissible := false
	observesStop := false
	switch i.kind {
	case campaignClaim:
		admissible = b.stop == campaignNoStop && b.report.Started < m.jobs && (i.stop == campaignNoStop || i.stop == campaignStopCancelled || i.stop == campaignStopDeadline)
		observesStop = i.stop != campaignNoStop
	case campaignComplete:
		p, exists := b.pending[i.jobID]
		admissible = exists && !p.ready && lcSummaryValid(i.summary)
		observesStop = i.summary.outcome == campaignCancelled || i.summary.outcome == campaignDeadline || (m.stopOnFailure && i.summary.outcome == campaignFailure)
	case campaignReceive:
		p, exists := b.pending[i.jobID]
		admissible = exists && p.ready && !b.hasDelivery
	case campaignSinkReturned:
		admissible = b.hasDelivery && b.delivering == i.jobID
		observesStop = i.err != nil
	case campaignWorkerFailed:
		admissible, observesStop = true, true
	case campaignFinish:
		admissible = !b.hasDelivery && (len(b.pending) == 0 || b.lostWorker) && (b.stop != campaignNoStop || b.report.Started == m.jobs)
	}
	admissible = admissible && !b.finalized
	claim, received, delivered := uint64(0), uint64(0), uint64(0)
	var expected []campaignEffect
	if accepted && i.kind == campaignClaim && i.stop == campaignNoStop {
		claim = 1
		if b.report.Started >= 0 && uint64(b.report.Started) <= math.MaxUint64-m.firstJob {
			expected = append(expected, campaignEffect{campaignRunJob, m.firstJob + uint64(b.report.Started)})
		}
	}
	if accepted && i.kind == campaignReceive {
		received = 1
		kind := campaignDeliver
		if b.report.Termination.Kind == "callback_error" {
			kind = campaignDiscard
		}
		expected = append(expected, campaignEffect{kind, i.jobID})
	}
	if accepted && i.kind == campaignSinkReturned && i.err == nil {
		delivered = 1
	}
	effectsOK := len(expected) == len(tr.Outputs)
	for k := range expected {
		if k >= len(tr.Outputs) || expected[k] != tr.Outputs[k] {
			effectsOK = false
		}
	}
	return []Check{
		lcCheck("campaign.admission", accepted == admissible),
		lcCheck("campaign.stop_is_observed", (a.stop != campaignNoStop) == (b.stop != campaignNoStop || (accepted && observesStop))),
		lcCheck("campaign.rejection_is_inert", accepted || (lcStateEqual(a, b) && len(tr.Outputs) == 0)),
		lcCheck("campaign.claim_once", a.report.Started >= b.report.Started && a.report.Started-b.report.Started == claim && (claim == 0 || (b.stop == campaignNoStop && b.report.Started < m.jobs))),
		lcCheck("campaign.receive_once", a.report.Finished >= b.report.Finished && a.report.Finished-b.report.Finished == received),
		lcCheck("campaign.deliver_once", a.report.Delivered >= b.report.Delivered && a.report.Delivered-b.report.Delivered == delivered),
		lcCheck("campaign.effects", effectsOK),
		lcCheck("campaign.first_sink_error", b.report.Termination.Kind != "callback_error" || lcTerminationEqual(a.report.Termination, b.report.Termination)),
		lcCheck("campaign.accounting_loss_is_sticky", b.report.AccountingComplete || !a.report.AccountingComplete),
		lcCheck("campaign.finish_is_observed", a.finalized == (b.finalized || (accepted && i.kind == campaignFinish))),
		lcCheck("campaign.finalization_is_terminal", !b.finalized || lcStateEqual(a, b)),
	}
}

func lcMustPass(t *testing.T, checks []Check) {
	t.Helper()
	for _, c := range checks {
		if c.Status != "passed" {
			t.Fatalf("unexpected check: %+v", c)
		}
	}
}
func lcMustFail(t *testing.T, checks []Check, id string) {
	t.Helper()
	for _, c := range checks {
		if c.ID == id && c.Status == "failed" {
			return
		}
	}
	t.Fatalf("missing failure %s: %+v", id, checks)
}
func lcInitial(t *testing.T, m campaignLifecycle) campaignState {
	t.Helper()
	s, e := m.initial()
	if e != nil {
		t.Fatal(e)
	}
	lcMustPass(t, lcStateChecks(m, s))
	return s
}
func lcApply(t *testing.T, m campaignLifecycle, s *campaignState, i campaignEvent) []campaignEffect {
	t.Helper()
	saved := s.clone()
	tr := m.step(*s, i)
	if !lcStateEqual(saved, *s) {
		t.Fatal("snapshot step mutated predecessor")
	}
	if tr.Disposition.Kind != "accepted" {
		t.Fatalf("event %+v: %+v", i, tr.Disposition)
	}
	lcMustPass(t, lcStateChecks(m, tr.State))
	lcMustPass(t, lcTransitionChecks(m, *s, i, tr))
	*s = tr.State
	return tr.Outputs
}
func lcReject(t *testing.T, m campaignLifecycle, s campaignState, i campaignEvent) {
	t.Helper()
	saved := s.clone()
	tr := m.step(s, i)
	if tr.Disposition.Kind != "rejected" || !lcStateEqual(saved, tr.State) || !lcStateEqual(saved, s) || len(tr.Outputs) != 0 {
		t.Fatalf("non-inert rejection %+v: %+v", i, tr)
	}
	lcMustPass(t, lcTransitionChecks(m, s, i, tr))
}
func lcSummary(o campaignOutcome) campaignSummary {
	s := campaignSummary{outcome: o, failure: o == campaignFailure}
	if o != campaignFactoryError && o != campaignModelError && o != campaignPanicked {
		s.transitions, s.cases, s.skippedChecks = 2, 1, 1
	}
	return s
}
func lcComplete(id uint64, o campaignOutcome) campaignEvent {
	return campaignEvent{kind: campaignComplete, jobID: id, summary: lcSummary(o)}
}
func lcSink(id uint64, e error) campaignEvent {
	return campaignEvent{kind: campaignSinkReturned, jobID: id, err: e}
}

func TestCampaignLifecycleHostileEventsAndSnapshots(t *testing.T) {
	m := campaignLifecycle{firstJob: 50, jobs: 2}
	s := lcInitial(t, m)
	for _, i := range []campaignEvent{lcComplete(50, campaignCompleted), {kind: campaignReceive, jobID: 50}, lcSink(50, nil), {kind: campaignFinish}} {
		lcReject(t, m, s, i)
	}
	lcApply(t, m, &s, campaignEvent{kind: campaignClaim})
	lcReject(t, m, s, lcComplete(49, campaignCompleted))
	lcReject(t, m, s, lcComplete(52, campaignCompleted))
	for _, bad := range []campaignSummary{
		{outcome: campaignFactoryError, failure: true}, {outcome: campaignPanicked, transitions: 1},
		{outcome: campaignModelError, cases: 1}, {outcome: campaignFactoryError, skippedChecks: 1},
		{outcome: campaignFailure}, {outcome: campaignCompleted, failure: true},
		{outcome: campaignCancelled, failure: true}, {outcome: campaignDeadline, failure: true},
	} {
		lcReject(t, m, s, campaignEvent{kind: campaignComplete, jobID: 50, summary: bad})
	}
	lcApply(t, m, &s, lcComplete(50, campaignCompleted))
	lcReject(t, m, s, lcComplete(50, campaignPanicked))
	lcApply(t, m, &s, campaignEvent{kind: campaignReceive, jobID: 50})
	lcApply(t, m, &s, campaignEvent{kind: campaignClaim})
	lcApply(t, m, &s, lcComplete(51, campaignCompleted))
	lcReject(t, m, s, campaignEvent{kind: campaignReceive, jobID: 51})
	lcReject(t, m, s, lcSink(51, nil))
	lcReject(t, m, s, campaignEvent{kind: campaignFinish})
	lcApply(t, m, &s, lcSink(50, nil))
	lcReject(t, m, s, lcSink(50, errors.New("stale error")))
	lcApply(t, m, &s, campaignEvent{kind: campaignReceive, jobID: 51})
	lcReject(t, m, s, lcSink(50, errors.New("stale while another sink runs")))
	lcApply(t, m, &s, lcSink(51, nil))
	lcReject(t, m, s, campaignEvent{kind: campaignReceive, jobID: 51})
	lcApply(t, m, &s, campaignEvent{kind: campaignFinish})
	for _, i := range lcAlphabet(m) {
		lcReject(t, m, s, i)
	}
	if s.report.Started != 2 || s.report.Finished != 2 || s.report.Delivered != 2 {
		t.Fatalf("%+v", s.report)
	}
}

func TestCampaignLifecycleOrdinaryStopPrecedenceAndInertClaims(t *testing.T) {
	orders := [][3]int{{0, 1, 2}, {0, 2, 1}, {1, 0, 2}, {1, 2, 0}, {2, 0, 1}, {2, 1, 0}}
	for _, order := range orders {
		m := campaignLifecycle{firstJob: 50, jobs: 4, stopOnFailure: true}
		s := lcInitial(t, m)
		for n := 0; n < 3; n++ {
			lcApply(t, m, &s, campaignEvent{kind: campaignClaim})
		}
		outcomes := []campaignOutcome{campaignFailure, campaignDeadline, campaignCancelled}
		for _, n := range order {
			lcApply(t, m, &s, lcComplete(50+uint64(n), outcomes[n]))
			for _, stop := range []campaignStop{campaignNoStop, campaignStopCancelled, campaignStopDeadline} {
				lcReject(t, m, s, campaignEvent{kind: campaignClaim, stop: stop})
			}
		}
		for id := uint64(50); id < 53; id++ {
			lcApply(t, m, &s, campaignEvent{kind: campaignReceive, jobID: id})
			lcApply(t, m, &s, lcSink(id, nil))
		}
		lcApply(t, m, &s, campaignEvent{kind: campaignFinish})
		if s.report.Termination.Kind != "cancelled" || s.report.NotStarted != 1 || s.report.Failures != 1 {
			t.Fatalf("order %v: %+v", order, s.report)
		}
	}
	for _, stop := range []campaignStop{campaignStopCancelled, campaignStopDeadline} {
		m := campaignLifecycle{jobs: 1}
		s := lcInitial(t, m)
		lcApply(t, m, &s, campaignEvent{kind: campaignClaim, stop: stop})
		lcApply(t, m, &s, campaignEvent{kind: campaignFinish})
		if s.report.Termination.Kind != string(stop) || s.report.Started != 0 {
			t.Fatalf("%+v", s.report)
		}
		s = lcInitial(t, m)
		for _, i := range []campaignEvent{{kind: campaignClaim}, lcComplete(0, campaignCompleted), {kind: campaignReceive, jobID: 0}, lcSink(0, nil)} {
			lcApply(t, m, &s, i)
		}
		lcReject(t, m, s, campaignEvent{kind: campaignClaim, stop: stop})
		lcApply(t, m, &s, campaignEvent{kind: campaignFinish})
		if s.report.Termination.Kind != "jobs_completed" {
			t.Fatalf("late claim observed: %+v", s.report)
		}
	}
}

func TestCampaignLifecycleErrorsDrainAndLostWork(t *testing.T) {
	for _, workerFirst := range []bool{false, true} {
		for _, outcome := range []campaignOutcome{campaignFailure, campaignCancelled, campaignDeadline} {
			m := campaignLifecycle{jobs: 3, stopOnFailure: true}
			s := lcInitial(t, m)
			for n := 0; n < 2; n++ {
				lcApply(t, m, &s, campaignEvent{kind: campaignClaim})
			}
			lcApply(t, m, &s, lcComplete(0, campaignCompleted))
			worker := campaignEvent{kind: campaignWorkerFailed, err: errors.New("worker first")}
			if workerFirst {
				lcApply(t, m, &s, worker)
			}
			effects := lcApply(t, m, &s, campaignEvent{kind: campaignReceive, jobID: 0})
			if len(effects) != 1 || effects[0].kind != campaignDeliver {
				t.Fatal("worker error suppressed sink")
			}
			lcApply(t, m, &s, lcSink(0, errors.New("first sink")))
			if !workerFirst {
				lcApply(t, m, &s, worker)
			}
			lcApply(t, m, &s, lcComplete(1, outcome))
			lcApply(t, m, &s, campaignEvent{kind: campaignWorkerFailed, err: errors.New("last worker")})
			lcReject(t, m, s, lcSink(0, errors.New("replacement sink")))
			effects = lcApply(t, m, &s, campaignEvent{kind: campaignReceive, jobID: 1})
			if len(effects) != 1 || effects[0].kind != campaignDiscard {
				t.Fatal("sink error failed to discard drain")
			}
			lcReject(t, m, s, lcSink(1, nil))
			lcApply(t, m, &s, campaignEvent{kind: campaignFinish})
			if s.report.Termination.Kind != "callback_error" || s.report.Termination.Error.Error() != "first sink" || !s.report.AccountingComplete || s.report.Delivered != 0 || s.report.Errors != 0 {
				t.Fatalf("%+v", s.report)
			}
		}
	}
	m := campaignLifecycle{jobs: 2}
	s := lcInitial(t, m)
	lcApply(t, m, &s, campaignEvent{kind: campaignClaim})
	lcApply(t, m, &s, campaignEvent{kind: campaignWorkerFailed, err: errors.New("spawn")})
	lcReject(t, m, s, campaignEvent{kind: campaignFinish})
	lcApply(t, m, &s, campaignEvent{kind: campaignWorkerFailed, err: errors.New("lost"), lostWork: true})
	lcApply(t, m, &s, campaignEvent{kind: campaignFinish})
	if s.report.AccountingComplete || s.report.Finished != 0 || s.report.Started != 1 || s.report.NotStarted != 1 || s.report.Termination.Error.Error() != "lost" {
		t.Fatalf("%+v", s.report)
	}
}

func TestCampaignLifecycleJobErrorsAndSaturation(t *testing.T) {
	for _, outcome := range []campaignOutcome{campaignFactoryError, campaignModelError, campaignPanicked, campaignFailure} {
		for _, stopOnFailure := range []bool{false, true} {
			m := campaignLifecycle{jobs: 2, stopOnFailure: stopOnFailure}
			s := lcInitial(t, m)
			for _, i := range []campaignEvent{{kind: campaignClaim}, lcComplete(0, outcome), {kind: campaignReceive}, lcSink(0, nil)} {
				lcApply(t, m, &s, i)
			}
			if outcome == campaignFailure && stopOnFailure {
				lcReject(t, m, s, campaignEvent{kind: campaignClaim})
			} else {
				for _, i := range []campaignEvent{{kind: campaignClaim}, lcComplete(1, campaignCompleted), {kind: campaignReceive, jobID: 1}, lcSink(1, nil)} {
					lcApply(t, m, &s, i)
				}
			}
			lcApply(t, m, &s, campaignEvent{kind: campaignFinish})
			wantKind := "jobs_completed"
			if outcome == campaignFailure && stopOnFailure {
				wantKind = "failure_found"
			}
			wantComplete := outcome != campaignModelError && outcome != campaignPanicked
			wantErrors := 1
			if outcome == campaignFailure {
				wantErrors = 0
			}
			if s.report.Termination.Kind != wantKind || s.report.AccountingComplete != wantComplete || s.report.Errors != wantErrors {
				t.Fatalf("%s %+v", outcome, s.report)
			}
		}
	}
	// Exact MaxUint64 is valid; only addition beyond it loses accounting. Each
	// field gets an independent overflow witness, plus simultaneous overflow.
	for field := 0; field < 4; field++ {
		m := campaignLifecycle{jobs: 4}
		s := lcInitial(t, m)
		for id := uint64(0); id < 4; id++ {
			v := uint64(0)
			if id == 0 {
				v = math.MaxUint64
			} else if id == 2 {
				v = 1
			}
			sum := campaignSummary{outcome: campaignCompleted}
			if field == 0 || field == 3 {
				sum.transitions = v
			}
			if field == 1 || field == 3 {
				sum.cases = v
			}
			if field == 2 || field == 3 {
				sum.skippedChecks = v
			}
			for _, i := range []campaignEvent{{kind: campaignClaim}, {kind: campaignComplete, jobID: id, summary: sum}, {kind: campaignReceive, jobID: id}, lcSink(id, nil)} {
				lcApply(t, m, &s, i)
			}
			if s.report.AccountingComplete != (id < 2) {
				t.Fatalf("field %d id %d accounting %+v", field, id, s.report)
			}
		}
		lcApply(t, m, &s, campaignEvent{kind: campaignFinish})
		values := []uint64{s.report.ReportedTransitions, s.report.ReportedCases, s.report.ReportedSkippedChecks}
		for n, v := range values {
			want := uint64(0)
			if n == field || field == 3 {
				want = math.MaxUint64
			}
			if v != want {
				t.Fatalf("field %d: %v", field, values)
			}
		}
	}
}

func TestCampaignLifecycleRangeAndSummaryProjection(t *testing.T) {
	for _, m := range []campaignLifecycle{{jobs: 0}, {firstJob: math.MaxUint64, jobs: 2}} {
		if _, e := m.initial(); e == nil {
			t.Fatal("accepted invalid range", m)
		}
	}
	m := campaignLifecycle{firstJob: math.MaxUint64, jobs: 1}
	s := lcInitial(t, m)
	lcApply(t, m, &s, campaignEvent{kind: campaignClaim})
	bad := Accept(s, campaignEffect{campaignRunJob, math.MaxUint64})
	lcMustFail(t, lcTransitionChecks(m, s, campaignEvent{kind: campaignClaim}, bad), "campaign.claim_once")
	lcMustFail(t, lcTransitionChecks(m, s, campaignEvent{kind: campaignClaim}, bad), "campaign.effects")
	for _, tc := range []struct {
		term    string
		outcome campaignOutcome
	}{{"cases_completed", campaignCompleted}, {"transition_limit", campaignCompleted}, {"failure_found", campaignFailure}, {"cancelled", campaignCancelled}, {"deadline", campaignDeadline}} {
		f := &FuzzReport64[int]{Termination: tc.term, Transitions: 19, Cases: 7, SkippedChecks: 11}
		if tc.outcome == campaignFailure {
			f.Failure = &Failure[int]{}
		}
		got := campaignSummaryFromJob(JobReport64[int]{Outcome: JobOutcome64[int]{Kind: "completed", Report: f}})
		if got != (campaignSummary{tc.outcome, tc.outcome == campaignFailure, 19, 7, 11}) || !got.valid() {
			t.Fatalf("%s %+v", tc.term, got)
		}
		f.Transitions = 99
		if got.transitions != 19 {
			t.Fatal("summary aliases report")
		}
	}
	for _, o := range []campaignOutcome{campaignFactoryError, campaignModelError, campaignPanicked} {
		got := campaignSummaryFromJob(JobReport64[int]{Outcome: JobOutcome64[int]{Kind: string(o), Error: errors.New("job"), Panic: "panic"}})
		if got != (campaignSummary{outcome: o}) || !got.valid() {
			t.Fatal(got)
		}
	}
	if campaignSummaryFromJob(JobReport64[int]{Outcome: JobOutcome64[int]{Kind: "completed"}}).valid() {
		t.Fatal("nil completed report fabricated work")
	}
}

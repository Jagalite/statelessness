package statelessness

import "math"

// campaignLifecycle is the actual campaign decision law. The executor owns
// synchronization, controls, factories, report payloads, channels and sink calls.
//
// Executor integration: newCampaignLifecycle(config).initial() creates state;
// stepOwned consumes exclusive ownership of that state and returns the next state
// and RunJob/Deliver/Discard effects. Complete MUST precede the blocking send;
// Receive follows channel receipt; SinkReturned acknowledges exactly that delivery.
// Finish follows draining and joining. Keep callbacks outside the coordinator lock.
// Snapshot-based model checking uses step, which detaches the pending map first.
// The codecs and independent self-model are deliberately confined to test files.
type campaignLifecycle struct {
	firstJob      uint64
	jobs          int
	stopOnFailure bool
}

type campaignStop string

const (
	campaignNoStop        campaignStop = ""
	campaignStopFailure   campaignStop = "failure"
	campaignStopCancelled campaignStop = "cancelled"
	campaignStopDeadline  campaignStop = "deadline"
	campaignStopSink      campaignStop = "sink"
	campaignStopWorker    campaignStop = "worker"
)

type campaignOutcome string

const (
	campaignCompleted    campaignOutcome = "completed"
	campaignFailure      campaignOutcome = "failure"
	campaignCancelled    campaignOutcome = "cancelled"
	campaignDeadline     campaignOutcome = "deadline"
	campaignFactoryError campaignOutcome = "factory_error"
	campaignModelError   campaignOutcome = "model_error"
	campaignPanicked     campaignOutcome = "panicked"
)

// Only protocol summaries are retained: no JobReport, FuzzReport, input, model,
// metadata, or application output can be reached through a pending entry.
type campaignSummary struct {
	outcome                           campaignOutcome
	failure                           bool
	transitions, cases, skippedChecks uint64
}

func (s campaignSummary) valid() bool {
	switch s.outcome {
	case campaignFactoryError, campaignModelError, campaignPanicked:
		return !s.failure && s.transitions == 0 && s.cases == 0 && s.skippedChecks == 0
	case campaignCompleted, campaignFailure, campaignCancelled, campaignDeadline:
		return s.failure == (s.outcome == campaignFailure)
	default:
		return false
	}
}

func campaignSummaryFromJob[I any](job JobReport[I]) campaignSummary {
	s := campaignSummary{}
	switch job.Outcome.Kind {
	case "completed":
		f := job.Outcome.Report
		if f == nil || f.Cases < 0 {
			return s // Invalid executor payload: never invent a successful summary.
		}
		s.outcome = campaignCompleted
		switch f.Termination {
		case "failure_found":
			s.outcome = campaignFailure
		case "cancelled":
			s.outcome = campaignCancelled
		case "deadline":
			s.outcome = campaignDeadline
		}
		s.failure = f.Failure != nil
		s.transitions, s.cases, s.skippedChecks = f.Transitions, uint64(f.Cases), f.SkippedChecks
	case "factory_error":
		s.outcome = campaignFactoryError
	case "model_error":
		s.outcome = campaignModelError
	case "panicked":
		s.outcome = campaignPanicked
	}
	return s
}

type campaignPending struct {
	ready   bool
	summary campaignSummary
}

type campaignState struct {
	report      CampaignReport
	stop        campaignStop
	pending     map[uint64]campaignPending
	delivering  uint64
	hasDelivery bool
	lostWorker  bool
	finalized   bool
}

func (s campaignState) clone() campaignState {
	pending := make(map[uint64]campaignPending, len(s.pending))
	for id, p := range s.pending {
		pending[id] = p
	}
	s.pending = pending
	return s
}

type campaignEventKind uint8

const (
	campaignClaim campaignEventKind = iota
	campaignComplete
	campaignReceive
	campaignSinkReturned
	campaignWorkerFailed
	campaignFinish
)

type campaignEvent struct {
	kind  campaignEventKind
	jobID uint64
	// A Claim samples only NoStop, Cancelled or Deadline. Other events use stop=NoStop.
	stop    campaignStop
	summary campaignSummary
	// The executor supplies an owned immutable error (its campaignOwnedError
	// boundary snapshots arbitrary application errors before entering this core).
	err      error
	lostWork bool
}

type campaignEffectKind uint8

const (
	campaignRunJob campaignEffectKind = iota
	campaignDeliver
	campaignDiscard
)

type campaignEffect struct {
	kind  campaignEffectKind
	jobID uint64
}

func newCampaignLifecycle(config CampaignConfig) campaignLifecycle {
	return campaignLifecycle{config.FirstJob, config.Jobs, config.StopOnFailure}
}

func (m campaignLifecycle) initial() (campaignState, error) {
	if m.jobs <= 0 || uint64(m.jobs-1) > math.MaxUint64-m.firstJob {
		return campaignState{}, &ModelError{"invalid campaign lifecycle job range"}
	}
	return campaignState{
		report: CampaignReport{Requested: m.jobs, NotStarted: m.jobs, AccountingComplete: true,
			Termination: CampaignTermination{Kind: "jobs_completed"}},
		pending: make(map[uint64]campaignPending),
	}, nil
}

func (m campaignLifecycle) step(before campaignState, input campaignEvent) Transition[campaignState, campaignEffect] {
	return m.stepOwned(before.clone(), input)
}

func (m campaignLifecycle) stepOwned(state campaignState, input campaignEvent) Transition[campaignState, campaignEffect] {
	reject := func(reason string) Transition[campaignState, campaignEffect] {
		return Transition[campaignState, campaignEffect]{State: state, Disposition: Rejected(reason)}
	}
	if state.finalized {
		return reject("campaign is finalized")
	}
	var outputs []campaignEffect
	switch input.kind {
	case campaignClaim:
		// Rejected late Claims do not observe their sampled external controls.
		if state.stop != campaignNoStop || state.report.Started == m.jobs {
			return reject("campaign cannot claim more jobs")
		}
		if input.stop == campaignStopCancelled || input.stop == campaignStopDeadline {
			state.stop = input.stop
		} else if input.stop != campaignNoStop {
			return reject("invalid external campaign stop")
		} else {
			id := m.firstJob + uint64(state.report.Started)
			state.pending[id] = campaignPending{}
			state.report.Started++
			state.report.NotStarted--
			outputs = append(outputs, campaignEffect{campaignRunJob, id})
		}
	case campaignComplete:
		if !input.summary.valid() {
			return reject("inconsistent job summary")
		}
		p, exists := state.pending[input.jobID]
		if !exists || p.ready {
			return reject("job is not running")
		}
		state.pending[input.jobID] = campaignPending{ready: true, summary: input.summary}
		switch input.summary.outcome {
		case campaignCancelled:
			state.stop = campaignStopCancelled
		case campaignDeadline:
			if state.stop != campaignStopCancelled {
				state.stop = campaignStopDeadline
			}
		case campaignFailure:
			if m.stopOnFailure && state.stop == campaignNoStop {
				state.stop = campaignStopFailure
			}
		}
	case campaignReceive:
		p, exists := state.pending[input.jobID]
		if !exists || !p.ready {
			return reject("job has no completed report")
		}
		if state.hasDelivery {
			return reject("sink is already delivering a report")
		}
		delete(state.pending, input.jobID)
		state.report.Finished++
		if p.summary.failure {
			state.report.Failures++
		}
		switch p.summary.outcome {
		case campaignFactoryError:
			state.report.Errors++
		case campaignModelError, campaignPanicked:
			state.report.Errors++
			state.report.AccountingComplete = false
		}
		for _, pair := range []struct {
			total *uint64
			count uint64
		}{
			{&state.report.ReportedTransitions, p.summary.transitions},
			{&state.report.ReportedCases, p.summary.cases},
			{&state.report.ReportedSkippedChecks, p.summary.skippedChecks},
		} {
			if pair.count > math.MaxUint64-*pair.total {
				*pair.total = math.MaxUint64
				state.report.AccountingComplete = false
			} else {
				*pair.total += pair.count
			}
		}
		if state.report.Termination.Kind == "callback_error" {
			outputs = append(outputs, campaignEffect{campaignDiscard, input.jobID})
		} else {
			state.delivering, state.hasDelivery = input.jobID, true
			outputs = append(outputs, campaignEffect{campaignDeliver, input.jobID})
		}
	case campaignSinkReturned:
		if !state.hasDelivery || state.delivering != input.jobID {
			return reject("job is not awaiting a sink result")
		}
		state.delivering, state.hasDelivery = 0, false
		if input.err != nil {
			state.report.Termination = CampaignTermination{Kind: "callback_error", Error: input.err}
			state.stop = campaignStopSink
		} else {
			state.report.Delivered++
		}
	case campaignWorkerFailed:
		if state.report.Termination.Kind != "callback_error" {
			state.report.Termination = CampaignTermination{Kind: "worker_error", Error: input.err}
		}
		state.stop = campaignStopWorker
		state.lostWorker = state.lostWorker || input.lostWork
		state.report.AccountingComplete = state.report.AccountingComplete && !input.lostWork
	case campaignFinish:
		if state.hasDelivery || (len(state.pending) != 0 && !state.lostWorker) ||
			(state.stop == campaignNoStop && state.report.Started != m.jobs) {
			return reject("campaign has unfinished work")
		}
		state.report.AccountingComplete = state.report.AccountingComplete && state.report.Started == state.report.Finished
		if state.report.Termination.Kind != "callback_error" && state.report.Termination.Kind != "worker_error" {
			kind := "jobs_completed"
			switch state.stop {
			case campaignStopCancelled:
				kind = "cancelled"
			case campaignStopDeadline:
				kind = "deadline"
			case campaignStopFailure:
				kind = "failure_found"
			}
			state.report.Termination = CampaignTermination{Kind: kind}
		}
		state.finalized = true
	default:
		return reject("invalid campaign event")
	}
	return Accept(state, outputs...)
}

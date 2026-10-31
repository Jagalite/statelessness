package debug

import (
	"errors"
	"math"
	"sync"
	"time"
	"unsafe"
)

type LocalInstant struct {
	Domain uint64 `json:"clock_domain"`
	Nanos  uint64 `json:"nanos"`
}

func (t LocalInstant) ElapsedSince(s LocalInstant) (uint64, error) {
	if t.Domain != s.Domain {
		return 0, errors.New("different clock domain")
	}
	if t.Nanos < s.Nanos {
		return 0, errors.New("reversed clock")
	}
	return t.Nanos - s.Nanos, nil
}

type Clock interface{ Now() (LocalInstant, error) }
type MonotonicClock struct {
	start  time.Time
	domain uint64
}

func NewMonotonicClock(domain uint64) *MonotonicClock { return &MonotonicClock{time.Now(), domain} }
func (c *MonotonicClock) Now() (LocalInstant, error) {
	n := time.Since(c.start)
	if n < 0 {
		return LocalInstant{}, errors.New("clock unavailable")
	}
	return LocalInstant{c.domain, uint64(n)}, nil
}

type EffectID struct{ Run, Epoch, Serial uint64 }
type AttemptID struct {
	Effect EffectID
	Serial uint64
}
type DeliveryID struct {
	Effect EffectID
	Serial uint64
}
type RequestOrigin struct {
	Run, Epoch, Machine, TransitionSequence uint64
	OutputIndex                             uint32
}
type EffectOptions struct {
	Run, Epoch                                     uint64
	Origin                                         MeasurementOrigin
	Counters, Timings, Details                     bool
	MaxDetails, MaxDetailBytes, MaxEventsPerEffect int
	MaxAttemptsPerEffect, MaxDeliveriesPerEffect   uint64
	MaxPauseIntervals                              int
	GaugeScope                                     GaugeScope
	Labels                                         LabelCatalog
	Histogram                                      HistogramSchema
	MetricLimits                                   MetricLimits
}

func DefaultEffectOptions() EffectOptions {
	return EffectOptions{Run: 1, Epoch: 1, Origin: OriginLive, Counters: true, Timings: true, Details: true, MaxDetails: 4096, MaxDetailBytes: 8 << 20, MaxEventsPerEffect: 128, MaxAttemptsPerEffect: 1024, MaxDeliveriesPerEffect: 1024, MaxPauseIntervals: 64, GaugeScope: ObservedSinceAttach, Labels: DefaultLabelCatalog(), Histogram: DefaultHistogramSchema(), MetricLimits: DefaultMetricLimits()}
}

type TelemetryHealth struct {
	ClockFailures                uint64 `json:"clock_failures"`
	CallbackFaults               uint64 `json:"callback_faults"`
	InvalidLifecycle             uint64 `json:"invalid_lifecycle"`
	DuplicateObservations        uint64 `json:"duplicate_observations"`
	DuplicateDeliveries          uint64 `json:"duplicate_deliveries"`
	Abandoned                    uint64 `json:"abandoned"`
	DetailEvictions              uint64 `json:"detail_evictions"`
	DetailRejections             uint64 `json:"detail_rejections"`
	DetailEventDrops             uint64 `json:"dropped_detail_events"`
	PauseIntervalEvictions       uint64 `json:"pause_interval_evictions"`
	DebuggerAffectedMeasurements uint64 `json:"debugger_affected_measurements"`
	RetainedDetailRecords        uint64 `json:"retained_detail_records"`
	RetainedDetailBytes          uint64 `json:"retained_detail_bytes"`
	// ActiveEffectHandles and DestructorHookLosses are nil (unknown): Go has no
	// destructors, so a handle that is dropped without a terminal lifecycle
	// call cannot be observed. Rust counts both from its Drop hooks.
	ActiveEffectHandles  *uint64 `json:"active_effect_handles"`
	DestructorHookLosses *uint64 `json:"destructor_hook_losses"`
	// Retained bytes of one handle value plus its shared lifecycle state, the
	// Go analogue of Rust size_of::<EffectToken>() + size_of::<EffectHandle>().
	EffectHandleBytes                            uint64       `json:"effect_handle_bytes"`
	AttemptHandleBytes                           uint64       `json:"attempt_handle_bytes"`
	DeliveryHandleBytes                          uint64       `json:"delivery_handle_bytes"`
	Metric                                       MetricHealth `json:"host_metric_health"`
	UnobservedNativeDebuggerInterferencePossible bool         `json:"unobserved_native_debugger_interference_possible"`
}

type LifecycleEvent struct {
	Sequence         uint64
	At               *LocalInstant
	Kind             string
	DebuggerAffected bool
	Attempt          *AttemptID
	Delivery         *DeliveryID
	DelayNS          *uint64
	Outcome          Outcome
}
type TimingObservation struct {
	Attempt          *AttemptID
	Delivery         *DeliveryID
	Family           MetricFamily
	DurationNS       *uint64
	Error            string
	Outcome          Outcome
	DebuggerAffected bool
}
type EffectTimeline struct {
	Requested, Resolved                                                                   *LocalInstant
	ResolvedKnown                                                                         bool
	Effect                                                                                EffectID
	Origin                                                                                RequestOrigin
	Labels                                                                                Labels
	Events                                                                                []LifecycleEvent
	Timings                                                                               []TimingObservation
	Complete                                                                              bool
	Admission                                                                             Outcome
	Resolution                                                                            Outcome
	Settled, CancellationRequested, CancellationAcknowledged, DebuggerAffected, Abandoned bool
	RunningAttempts, DeliveriesObserved                                                   uint64
}
type PauseInterval struct {
	Generation uint64
	Start, End *LocalInstant
}
type effectState struct {
	id                                                                                                 EffectID
	origin                                                                                             RequestOrigin
	labels                                                                                             Labels
	requested, resolved, settled, lastFinish                                                           *LocalInstant
	admission                                                                                          int
	resolvedKnown, settledKnown, cancelRequested, cancelAcknowledged, affected, firstBegun, firstReady bool
	pauseGeneration                                                                                    uint64
	observed, nextAttempt, nextDelivery                                                                uint64
	running                                                                                            uint64
	resolution                                                                                         Outcome
	abandoned                                                                                          bool
	detail                                                                                             *EffectTimeline
}
type attemptState struct {
	id                                                    AttemptID
	effect                                                *effectState
	ready, start, finish, retryAt, previousFinish         *LocalInstant
	readyKnown, started, finished, abandoned, hasSchedule bool
	delay                                                 uint64
}
type deliveryState struct {
	id                                            DeliveryID
	effect                                        *effectState
	posted, begin                                 *LocalInstant
	confirmed, failed, begun, observed, abandoned bool
}
type EffectHandle struct {
	owner *EffectObserver
	state *effectState
}
type AttemptHandle struct {
	owner *EffectObserver
	state *attemptState
}
type DeliveryHandle struct {
	owner *EffectObserver
	state *deliveryState
}

func (h EffectHandle) ID() EffectID {
	if h.state == nil {
		return EffectID{}
	}
	return h.state.id
}
func (h AttemptHandle) ID() AttemptID {
	if h.state == nil {
		return AttemptID{}
	}
	return h.state.id
}
func (h DeliveryHandle) ID() DeliveryID {
	if h.state == nil {
		return DeliveryID{}
	}
	return h.state.id
}

// EffectObserver is host lifecycle accounting, never an execution scheduler.
// Handles retain bounded per-work state; callers explicitly report abandonment.
// No finalizer claims termination of a goroutine or cancellation of host work.
type EffectObserver struct {
	mu                    sync.Mutex
	options               EffectOptions
	clock                 Clock
	metrics               *MetricsStore
	serial, eventSequence uint64
	details               []*EffectTimeline
	detailBytes           int
	health                TelemetryHealth
	paused                bool
	pauseGeneration       uint64
	pauses                []PauseInterval
}

var ErrLifecycle = errors.New("effect: foreign token, wrong phase, duplicate or limit")

func NewEffectObserver(o EffectOptions, c Clock) (*EffectObserver, error) {
	if o.Run == 0 || o.Epoch == 0 || o.MaxDetails < 0 || o.MaxDetailBytes < 0 || o.MaxEventsPerEffect <= 0 || o.MaxAttemptsPerEffect == 0 || o.MaxDeliveriesPerEffect == 0 || o.MaxPauseIntervals <= 0 || (o.Timings && c == nil) {
		return nil, ErrLifecycle
	}
	m, e := NewMetricsStoreAt(o.Epoch, o.Labels, o.Histogram, o.MetricLimits, nil, o.GaugeScope)
	if e != nil {
		return nil, e
	}
	if !m.s.Labels.Accepts(Labels{Origin: o.Origin}) {
		return nil, ErrLifecycle
	}
	o.Labels = m.s.Labels
	o.Histogram = m.s.Schema
	observer := &EffectObserver{options: o, clock: c, metrics: m, health: TelemetryHealth{UnobservedNativeDebuggerInterferencePossible: true}}
	if at := observer.now(); at != nil {
		start := *at
		end := *at
		last := *at
		m.s.Start = &start
		m.s.End = &end
		m.lastValidEnd = &last
	}
	return observer, nil
}

// Clock callbacks run outside observer locks and panic becomes missing measurement.
func (o *EffectObserver) now() (at *LocalInstant) {
	if !o.options.Timings {
		return nil
	}
	var instant LocalInstant
	var err error
	func() {
		defer func() {
			if recover() != nil {
				err = errors.New("clock panic")
			}
		}()
		instant, err = o.clock.Now()
	}()
	if err != nil {
		o.mu.Lock()
		satInc(&o.health.ClockFailures)
		o.mu.Unlock()
		return nil
	}
	return &instant
}
func (o *EffectObserver) invalid() error           { satInc(&o.health.InvalidLifecycle); return ErrLifecycle }
func (o *EffectObserver) same(h EffectHandle) bool { return h.owner == o && h.state != nil }
func (o *EffectObserver) duplicate(s *effectState) error {
	satInc(&o.health.DuplicateObservations)
	o.count(DuplicateObservations, s.labels)
	return ErrLifecycle
}
func (o *EffectObserver) count(f MetricFamily, l Labels) {
	if o.options.Counters {
		o.metrics.Increment(f, l, 1)
	}
}
func (o *EffectObserver) gauge(f MetricFamily, l Labels, d int64) {
	if o.options.Counters {
		l.Outcome = OutcomeNone
		l.DebuggerAffected = false
		o.metrics.GaugeDelta(f, l, d)
	}
}
func (o *EffectObserver) affected(s *effectState) {
	if o.paused || s.pauseGeneration != o.pauseGeneration {
		s.affected = true
	}
}
func (o *EffectObserver) event(s *effectState, kind string, at *LocalInstant, info ...LifecycleEvent) {
	if !o.options.Details {
		return
	}
	if o.eventSequence == math.MaxUint64 {
		satInc(&o.health.DetailEventDrops)
		return
	}
	o.eventSequence++
	if s.detail == nil {
		return
	}
	found := false
	for _, d := range o.details {
		if d == s.detail {
			found = true
			break
		}
	}
	if !found {
		return
	}
	s.detail.Admission = OutcomeNone
	if s.admission == 1 {
		s.detail.Admission = OutcomeAccepted
	} else if s.admission == 2 {
		s.detail.Admission = OutcomeRejected
	}
	s.detail.Resolution = s.resolution
	s.detail.Settled = s.settledKnown
	s.detail.CancellationRequested = s.cancelRequested
	s.detail.CancellationAcknowledged = s.cancelAcknowledged
	s.detail.RunningAttempts = s.running
	s.detail.DeliveriesObserved = s.observed
	s.detail.DebuggerAffected = s.affected
	s.detail.Abandoned = s.abandoned
	if s.abandoned {
		s.detail.Complete = false
	}
	if len(s.detail.Events) >= o.options.MaxEventsPerEffect || 128 > o.options.MaxDetailBytes-o.detailBytes {
		satInc(&o.health.DetailEventDrops)
		s.detail.Complete = false
		return
	}
	o.detailBytes += 128
	item := LifecycleEvent{Sequence: o.eventSequence, At: at, Kind: kind, DebuggerAffected: s.affected}
	if len(info) != 0 {
		item.Attempt, item.Delivery, item.DelayNS, item.Outcome = info[0].Attempt, info[0].Delivery, info[0].DelayNS, info[0].Outcome
	}
	s.detail.Events = append(s.detail.Events, item)
}
func (o *EffectObserver) measure(s *effectState, f MetricFamily, a, b *LocalInstant, outcome Outcome, identity ...any) {
	if !o.options.Timings {
		return
	}
	o.affected(s)
	t := TimingObservation{Family: f, Outcome: outcome, DebuggerAffected: s.affected}
	for _, id := range identity {
		switch v := id.(type) {
		case AttemptID:
			t.Attempt = &v
		case DeliveryID:
			t.Delivery = &v
		}
	}
	l := s.labels
	l.Outcome = outcome
	l.DebuggerAffected = s.affected
	if a == nil || b == nil {
		t.Error = "missing endpoint"
		satInc(&o.metrics.s.Health.OmittedMeasurements)
	} else if n, e := b.ElapsedSince(*a); e != nil {
		t.Error = e.Error()
		satInc(&o.metrics.s.Health.InvalidMeasurements)
		o.count(InvalidMeasurements, l)
	} else {
		t.DurationNS = &n
		o.metrics.Observe(f, l, n)
		if s.affected {
			satInc(&o.health.DebuggerAffectedMeasurements)
		}
	}
	if t.Error != "" {
		o.metrics.MarkIncomplete()
	}
	if s.detail != nil {
		found := false
		for _, d := range o.details {
			if d == s.detail {
				found = true
				break
			}
		}
		if found {
			if len(s.detail.Timings) >= o.options.MaxEventsPerEffect || 192 > o.options.MaxDetailBytes-o.detailBytes {
				satInc(&o.health.DetailEventDrops)
				s.detail.Complete = false
			} else {
				o.detailBytes += 192
				s.detail.Timings = append(s.detail.Timings, t)
			}
		}
	}
}
func (o *EffectObserver) Requested(origin RequestOrigin, l Labels) (EffectHandle, error) {
	l.Origin = o.options.Origin
	l.Outcome = OutcomeNone
	l.DebuggerAffected = false
	if origin.Run != o.options.Run || origin.Epoch != o.options.Epoch || origin.TransitionSequence == 0 || !o.options.Labels.Accepts(l) {
		o.mu.Lock()
		err := o.invalid()
		o.mu.Unlock()
		return EffectHandle{}, err
	}
	// Clock callbacks run outside the observer lock. Identity and label
	// admission precede clock work; exhaustion is checked atomically after it.
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.serial == math.MaxUint64 {
		return EffectHandle{}, o.invalid()
	}
	o.serial++
	s := &effectState{id: EffectID{o.options.Run, o.options.Epoch, o.serial}, origin: origin, labels: l, requested: at, affected: o.paused, pauseGeneration: o.pauseGeneration}
	if o.options.Details {
		if o.options.MaxDetails == 0 || o.options.MaxDetailBytes < 256 {
			satInc(&o.health.DetailRejections)
		} else {
			for len(o.details) > 0 && (len(o.details) >= o.options.MaxDetails || 256 > o.options.MaxDetailBytes-o.detailBytes) {
				d := o.details[0]
				o.detailBytes -= 256 + 128*len(d.Events) + 192*len(d.Timings)
				d.Events = nil
				d.Timings = nil
				d.Complete = false
				o.details = o.details[1:]
				satInc(&o.health.DetailEvictions)
			}
			s.detail = &EffectTimeline{Effect: s.id, Origin: origin, Labels: l, Complete: true, Requested: at}
			o.details = append(o.details, s.detail)
			o.detailBytes += 256
		}
	}
	o.count(Requests, l)
	o.event(s, "requested", at)
	return EffectHandle{o, s}, nil
}

// preflight runs lifecycle admission under the state lock. The operation
// revalidates after sampling the clock because callbacks run outside that lock.
func (o *EffectObserver) preflight(check func() error) error {
	o.mu.Lock()
	defer o.mu.Unlock()
	return check()
}
func (o *EffectObserver) Admission(h EffectHandle, accepted bool) error {
	if err := o.preflight(func() error {
		if o.same(h) && h.state.admission != 0 {
			return o.duplicate(h.state)
		}
		if !o.same(h) || h.state.admission != 0 || h.state.resolvedKnown {
			return o.invalid()
		}
		return nil
	}); err != nil {
		return err
	}
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.same(h) && h.state.admission != 0 {
		return o.duplicate(h.state)
	}
	if !o.same(h) || h.state.admission != 0 || h.state.resolvedKnown {
		return o.invalid()
	}
	s := h.state
	s.admission = 2
	l := s.labels
	l.Outcome = OutcomeRejected
	if accepted {
		s.admission = 1
		l.Outcome = OutcomeAccepted
		o.gauge(Unresolved, s.labels, 1)
	}
	o.count(Admissions, l)
	o.event(s, "admission:"+string(l.Outcome), at)
	return nil
}
func (o *EffectObserver) AttemptCreated(h EffectHandle) (AttemptHandle, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if !o.same(h) || h.state.admission != 1 || h.state.resolvedKnown || h.state.nextAttempt >= o.options.MaxAttemptsPerEffect {
		return AttemptHandle{}, o.invalid()
	}
	s := h.state
	s.nextAttempt++
	a := &attemptState{id: AttemptID{s.id, s.nextAttempt}, effect: s, previousFinish: s.lastFinish}
	o.event(s, "attempt-created", nil, LifecycleEvent{Attempt: &a.id})
	return AttemptHandle{o, a}, nil
}
func (o *EffectObserver) attempt(h AttemptHandle) bool { return h.owner == o && h.state != nil }
func (o *EffectObserver) RetryScheduled(h AttemptHandle, delayNS uint64) error {
	if err := o.preflight(func() error {
		if !o.attempt(h) || h.state.started || h.state.readyKnown || h.state.hasSchedule || h.state.abandoned || h.state.effect.resolvedKnown {
			return o.invalid()
		}
		return nil
	}); err != nil {
		return err
	}
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if !o.attempt(h) || h.state.started || h.state.readyKnown || h.state.hasSchedule || h.state.abandoned || h.state.effect.resolvedKnown {
		return o.invalid()
	}
	h.state.hasSchedule = true
	h.state.retryAt = at
	o.affected(h.state.effect)
	if o.options.Timings {
		l := h.state.effect.labels
		l.DebuggerAffected = h.state.effect.affected
		o.metrics.Observe(ScheduledBackoff, l, delayNS)
		if h.state.effect.affected {
			satInc(&o.health.DebuggerAffectedMeasurements)
		}
	}
	h.state.delay = delayNS
	o.event(h.state.effect, "retry-scheduled", at, LifecycleEvent{Attempt: &h.state.id, DelayNS: &h.state.delay})
	return nil
}
func (o *EffectObserver) ReadyQueued(h AttemptHandle) error {
	if err := o.preflight(func() error {
		if o.attempt(h) && h.state.readyKnown {
			return o.duplicate(h.state.effect)
		}
		if !o.attempt(h) || h.state.readyKnown || h.state.started || h.state.finished || h.state.abandoned || h.state.effect.resolvedKnown {
			return o.invalid()
		}
		return nil
	}); err != nil {
		return err
	}
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.attempt(h) && h.state.readyKnown {
		return o.duplicate(h.state.effect)
	}
	if !o.attempt(h) || h.state.readyKnown || h.state.started || h.state.finished || h.state.abandoned || h.state.effect.resolvedKnown {
		return o.invalid()
	}
	a := h.state
	a.readyKnown = true
	a.ready = at
	o.affected(a.effect)
	o.gauge(ReadyDepth, a.effect.labels, 1)
	if !a.effect.firstReady {
		a.effect.firstReady = true
		o.measure(a.effect, AdmissionDelay, a.effect.requested, at, OutcomeNone, a.id)
	}
	if a.hasSchedule {
		o.measure(a.effect, EligibilityWait, a.retryAt, at, OutcomeNone, a.id)
	}

	o.event(a.effect, "ready", at, LifecycleEvent{Attempt: &a.id})
	return nil
}
func (o *EffectObserver) ReadyRemoved(h AttemptHandle) error {
	o.mu.Lock()
	defer o.mu.Unlock()
	if !o.attempt(h) || !h.state.readyKnown || h.state.started {
		return o.invalid()
	}
	h.state.readyKnown = false
	o.gauge(ReadyDepth, h.state.effect.labels, -1)
	return nil
}
func (o *EffectObserver) AttemptStarted(h AttemptHandle) error {
	if err := o.preflight(func() error {
		if o.attempt(h) && h.state.started {
			return o.duplicate(h.state.effect)
		}
		if !o.attempt(h) || h.state.started || h.state.finished || h.state.abandoned || h.state.effect.resolvedKnown {
			return o.invalid()
		}
		return nil
	}); err != nil {
		return err
	}
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.attempt(h) && h.state.started {
		return o.duplicate(h.state.effect)
	}
	if !o.attempt(h) || h.state.started || h.state.finished || h.state.abandoned || h.state.effect.resolvedKnown {
		return o.invalid()
	}
	a := h.state
	a.started = true
	a.effect.running++
	a.start = at
	if a.readyKnown {
		o.gauge(ReadyDepth, a.effect.labels, -1)
		a.readyKnown = false
		o.affected(a.effect)
		o.measure(a.effect, QueueWait, a.ready, at, OutcomeNone, a.id)
	}
	o.count(Attempts, a.effect.labels)
	o.gauge(Running, a.effect.labels, 1)
	o.affected(a.effect)
	if a.id.Serial > 1 {
		o.measure(a.effect, InterAttemptGap, a.previousFinish, at, OutcomeNone, a.id)
	}
	o.event(a.effect, "attempt-started", at, LifecycleEvent{Attempt: &a.id})
	return nil
}
func validRuntime(out Outcome) bool {
	return out == OutcomeNone || out == OutcomeSuccess || out == OutcomeFailure || out == OutcomeCancelled
}
func (o *EffectObserver) AttemptFinished(h AttemptHandle, out Outcome) error {
	if err := o.preflight(func() error {
		if o.attempt(h) && h.state.finished {
			return o.duplicate(h.state.effect)
		}
		if !o.attempt(h) || !h.state.started || h.state.finished || !validRuntime(out) {
			return o.invalid()
		}
		return nil
	}); err != nil {
		return err
	}
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.attempt(h) && h.state.finished {
		return o.duplicate(h.state.effect)
	}
	if !o.attempt(h) || !h.state.started || h.state.finished || !validRuntime(out) {
		return o.invalid()
	}
	a := h.state
	a.finished = true
	if a.effect.running > 0 {
		a.effect.running--
	}
	a.finish = at
	a.effect.lastFinish = at
	l := a.effect.labels
	l.Outcome = out
	o.count(AttemptOutcomes, l)
	o.gauge(Running, a.effect.labels, -1)
	o.affected(a.effect)
	o.measure(a.effect, AttemptElapsed, a.start, at, out, a.id)
	o.event(a.effect, "attempt-finished:"+string(out), at, LifecycleEvent{Attempt: &a.id, Outcome: out})
	return nil
}
func (o *EffectObserver) Resolved(h EffectHandle, out Outcome, selected *AttemptHandle) error {
	if selected != nil {
		owned := *selected
		selected = &owned
	}
	if err := o.preflight(func() error {
		if o.same(h) && h.state.resolvedKnown {
			return o.duplicate(h.state)
		}
		if !o.same(h) || h.state.admission != 1 || h.state.resolvedKnown || !validRuntime(out) {
			return o.invalid()
		}
		if selected != nil && (!o.attempt(*selected) || selected.state.effect != h.state || !selected.state.finished) {
			return o.invalid()
		}
		return nil
	}); err != nil {
		return err
	}
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.same(h) && h.state.resolvedKnown {
		return o.duplicate(h.state)
	}
	if !o.same(h) || h.state.admission != 1 || h.state.resolvedKnown || !validRuntime(out) {
		return o.invalid()
	}
	s := h.state
	if selected != nil && (!o.attempt(*selected) || selected.state.effect != s || !selected.state.finished) {
		return o.invalid()
	}
	s.resolvedKnown = true
	s.resolution = out
	s.resolved = at
	if s.detail != nil {
		s.detail.Resolved = at
		s.detail.ResolvedKnown = true
	}
	l := s.labels
	l.Outcome = out
	o.count(Resolutions, l)
	if s.admission == 1 {
		o.gauge(Unresolved, s.labels, -1)
	}
	o.affected(s)
	o.measure(s, Resolution, s.requested, at, out)
	if selected != nil {
		o.measure(s, ResolutionOverhead, selected.state.finish, at, out, selected.state.id)
	}
	o.event(s, "resolved:"+string(out), at, LifecycleEvent{Outcome: out})
	return nil
}
func (o *EffectObserver) CancellationRequested(h EffectHandle) error {
	if err := o.preflight(func() error {
		if o.same(h) && h.state.cancelRequested {
			return o.duplicate(h.state)
		}
		if !o.same(h) || h.state.cancelRequested {
			return o.invalid()
		}
		return nil
	}); err != nil {
		return err
	}
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.same(h) && h.state.cancelRequested {
		return o.duplicate(h.state)
	}
	if !o.same(h) || h.state.cancelRequested {
		return o.invalid()
	}
	h.state.cancelRequested = true
	o.count(CancellationRequests, h.state.labels)
	o.event(h.state, "cancellation-requested", at)
	return nil
}
func (o *EffectObserver) CancellationAcknowledged(h EffectHandle) error {
	if err := o.preflight(func() error {
		if o.same(h) && h.state.cancelAcknowledged {
			return o.duplicate(h.state)
		}
		if !o.same(h) || !h.state.cancelRequested || h.state.cancelAcknowledged {
			return o.invalid()
		}
		return nil
	}); err != nil {
		return err
	}
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.same(h) && h.state.cancelAcknowledged {
		return o.duplicate(h.state)
	}
	if !o.same(h) || !h.state.cancelRequested || h.state.cancelAcknowledged {
		return o.invalid()
	}
	h.state.cancelAcknowledged = true
	o.count(CancellationAcknowledgements, h.state.labels)
	o.event(h.state, "cancellation-acknowledged", at)
	return nil
}
func (o *EffectObserver) ReservePublication(h EffectHandle) (DeliveryHandle, error) {
	if err := o.preflight(func() error {
		if !o.same(h) || !h.state.resolvedKnown || h.state.nextDelivery >= o.options.MaxDeliveriesPerEffect {
			return o.invalid()
		}
		return nil
	}); err != nil {
		return DeliveryHandle{}, err
	}
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if !o.same(h) || !h.state.resolvedKnown || h.state.nextDelivery >= o.options.MaxDeliveriesPerEffect {
		return DeliveryHandle{}, o.invalid()
	}
	s := h.state
	o.affected(s)
	s.nextDelivery++
	d := &deliveryState{id: DeliveryID{s.id, s.nextDelivery}, effect: s, posted: at}
	o.gauge(CompletionDepth, s.labels, 1)
	o.event(s, "publication-reserved", at, LifecycleEvent{Delivery: &d.id})
	return DeliveryHandle{o, d}, nil
}
func (o *EffectObserver) delivery(h DeliveryHandle) bool { return h.owner == o && h.state != nil }
func (o *EffectObserver) confirm(d *deliveryState) {
	if !d.confirmed {
		d.confirmed = true
		o.measure(d.effect, CompletionPreparation, d.effect.resolved, d.posted, OutcomeNone, d.id)
	}
}
func (o *EffectObserver) PublicationCommitted(h DeliveryHandle) error {
	o.mu.Lock()
	defer o.mu.Unlock()
	if !o.delivery(h) || h.state.failed {
		return o.invalid()
	}
	o.confirm(h.state)
	return nil
}
func (o *EffectObserver) PublicationFailed(h DeliveryHandle) error {
	if err := o.preflight(func() error {
		if o.delivery(h) && h.state.failed {
			return o.duplicate(h.state.effect)
		}
		if !o.delivery(h) || h.state.failed || h.state.begun || h.state.confirmed {
			return o.invalid()
		}
		return nil
	}); err != nil {
		return err
	}
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.delivery(h) && h.state.failed {
		return o.duplicate(h.state.effect)
	}
	if !o.delivery(h) || h.state.failed || h.state.begun || h.state.confirmed {
		return o.invalid()
	}
	d := h.state
	d.failed = true
	o.gauge(CompletionDepth, d.effect.labels, -1)
	o.event(d.effect, "publication-failed", at, LifecycleEvent{Delivery: &d.id})
	return nil
}
func (o *EffectObserver) DeliveryBegun(h DeliveryHandle) error {
	if err := o.preflight(func() error {
		if o.delivery(h) && h.state.begun {
			return o.duplicate(h.state.effect)
		}
		if !o.delivery(h) || h.state.begun || h.state.failed {
			return o.invalid()
		}
		return nil
	}); err != nil {
		return err
	}
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.delivery(h) && h.state.begun {
		return o.duplicate(h.state.effect)
	}
	if !o.delivery(h) || h.state.begun || h.state.failed {
		return o.invalid()
	}
	d := h.state
	s := d.effect
	d.begun = true
	d.begin = at
	o.affected(s)
	o.confirm(d)
	o.gauge(CompletionDepth, s.labels, -1)
	o.measure(s, CompletionQueueWait, d.posted, at, OutcomeNone, d.id)
	o.measure(s, DeliveryLag, s.resolved, at, OutcomeNone, d.id)
	if !s.firstBegun {
		s.firstBegun = true
		o.measure(s, EndToEnd, s.requested, at, OutcomeNone, d.id)
	}
	o.event(s, "delivery-begun", at, LifecycleEvent{Delivery: &d.id})
	return nil
}
func (o *EffectObserver) DeliveryObserved(h DeliveryHandle, disposition Outcome) error {
	if err := o.preflight(func() error {
		if o.delivery(h) && h.state.observed {
			return o.duplicate(h.state.effect)
		}
		if !o.delivery(h) || !h.state.begun || h.state.observed || h.state.failed || (disposition != OutcomeAccepted && disposition != OutcomeRejected && disposition != OutcomeIgnored) {
			return o.invalid()
		}
		return nil
	}); err != nil {
		return err
	}
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.delivery(h) && h.state.observed {
		return o.duplicate(h.state.effect)
	}
	if !o.delivery(h) || !h.state.begun || h.state.observed || h.state.failed || (disposition != OutcomeAccepted && disposition != OutcomeRejected && disposition != OutcomeIgnored) {
		return o.invalid()
	}
	d := h.state
	s := d.effect
	d.observed = true
	l := s.labels
	l.Outcome = disposition
	o.count(Deliveries, l)
	if s.observed > 0 {
		o.count(DuplicateDeliveries, s.labels)
		satInc(&o.health.DuplicateDeliveries)
	}
	o.affected(s)
	if s.observed == 0 {
		o.measure(s, EndToEndObserved, s.requested, at, disposition, d.id)
	}
	s.observed++
	o.measure(s, CompletionProcessing, d.begin, at, disposition, d.id)
	o.event(s, "delivery-observed:"+string(disposition), at, LifecycleEvent{Delivery: &d.id, Outcome: disposition})
	return nil
}
func (o *EffectObserver) Settled(h EffectHandle) error {
	if err := o.preflight(func() error {
		if o.same(h) && h.state.settledKnown {
			return o.duplicate(h.state)
		}
		if !o.same(h) || h.state.settledKnown {
			return o.invalid()
		}
		return nil
	}); err != nil {
		return err
	}
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.same(h) && h.state.settledKnown {
		return o.duplicate(h.state)
	}
	if !o.same(h) || h.state.settledKnown {
		return o.invalid()
	}
	s := h.state
	o.affected(s)
	s.settledKnown = true
	s.settled = at
	o.measure(s, Settlement, s.requested, at, OutcomeNone)
	o.event(s, "settled", at)
	return nil
}
func (o *EffectObserver) AttemptAbandoned(h AttemptHandle) error {
	o.mu.Lock()
	defer o.mu.Unlock()
	if !o.attempt(h) {
		return o.invalid()
	}
	if h.state.finished || h.state.abandoned {
		return nil
	}
	a := h.state
	a.abandoned = true
	a.effect.abandoned = true
	satInc(&o.health.Abandoned)
	o.metrics.MarkIncomplete()
	o.count(Abandoned, a.effect.labels)
	o.event(a.effect, "abandoned", nil, LifecycleEvent{Attempt: &a.id})
	return nil
}

// EffectAbandoned records that the host stopped observing an unresolved effect.
// It does not claim cancellation or decrement any host-owned work gauge.
// Go hosts must call this explicitly when they relinquish a token.
func (o *EffectObserver) EffectAbandoned(h EffectHandle) error {
	o.mu.Lock()
	defer o.mu.Unlock()
	if !o.same(h) {
		return o.invalid()
	}
	s := h.state
	if s.admission == 2 || s.resolvedKnown || s.abandoned {
		return nil
	}
	s.abandoned = true
	satInc(&o.health.Abandoned)
	o.metrics.MarkIncomplete()
	o.count(Abandoned, s.labels)
	o.event(s, "abandoned", nil)
	return nil
}

// DeliveryAbandoned records loss of a delivery observation without asserting
// whether publication or consumption happened. It leaves host gauges intact.
func (o *EffectObserver) DeliveryAbandoned(h DeliveryHandle) error {
	o.mu.Lock()
	defer o.mu.Unlock()
	if !o.delivery(h) {
		return o.invalid()
	}
	d := h.state
	if d.failed || d.observed || d.abandoned {
		return nil
	}
	d.abandoned = true
	d.effect.abandoned = true
	satInc(&o.health.Abandoned)
	o.metrics.MarkIncomplete()
	o.count(Abandoned, d.effect.labels)
	o.event(d.effect, "abandoned", nil)
	return nil
}
func (o *EffectObserver) DebuggerPause(paused bool) {
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if paused == o.paused {
		return
	}
	o.paused = paused
	if o.pauseGeneration < math.MaxUint64 {
		o.pauseGeneration++
	}
	if paused {
		if o.options.MaxPauseIntervals == 0 {
			satInc(&o.health.PauseIntervalEvictions)
			return
		}
		if len(o.pauses) >= o.options.MaxPauseIntervals {
			o.pauses = o.pauses[1:]
			satInc(&o.health.PauseIntervalEvictions)
		}
		o.pauses = append(o.pauses, PauseInterval{o.pauseGeneration, at, nil})
	} else if len(o.pauses) > 0 {
		o.pauses[len(o.pauses)-1].End = at
	}
}
func (o *EffectObserver) MetricSnapshot() (MetricSnapshot, error) {
	o.mu.Lock()
	revision := o.metrics.s.Revision
	o.mu.Unlock()
	at := o.now()
	o.mu.Lock()
	defer o.mu.Unlock()
	if at != nil && revision != o.metrics.s.Revision {
		at = nil
		satInc(&o.metrics.s.Health.OmittedMeasurements)
		o.metrics.MarkIncomplete()
	}
	return o.metrics.Snapshot(at)
}
func (o *EffectObserver) MetricWindow(a, b uint64) (MetricSnapshot, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.metrics.Window(a, b)
}
func (o *EffectObserver) AvailableWindows() []uint64 {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.metrics.AvailableWindows()
}
func (o *EffectObserver) TelemetryHealth() TelemetryHealth {
	o.mu.Lock()
	defer o.mu.Unlock()
	h := o.health
	h.Metric = o.metrics.s.Health
	h.RetainedDetailRecords = uint64(len(o.details))
	h.RetainedDetailBytes = uint64(o.detailBytes)
	h.ActiveEffectHandles, h.DestructorHookLosses = nil, nil
	h.EffectHandleBytes = uint64(unsafe.Sizeof(EffectHandle{}) + unsafe.Sizeof(effectState{}))
	h.AttemptHandleBytes = uint64(unsafe.Sizeof(AttemptHandle{}) + unsafe.Sizeof(attemptState{}))
	h.DeliveryHandleBytes = uint64(unsafe.Sizeof(DeliveryHandle{}) + unsafe.Sizeof(deliveryState{}))
	return h
}
func cloneTimeline(t EffectTimeline) EffectTimeline {
	r := t
	if t.Requested != nil {
		x := *t.Requested
		r.Requested = &x
	}
	if t.Resolved != nil {
		x := *t.Resolved
		r.Resolved = &x
	}
	r.Events = append([]LifecycleEvent(nil), t.Events...)
	for i, e := range r.Events {
		if e.At != nil {
			v := *e.At
			r.Events[i].At = &v
		}
		if e.Attempt != nil {
			v := *e.Attempt
			r.Events[i].Attempt = &v
		}
		if e.Delivery != nil {
			v := *e.Delivery
			r.Events[i].Delivery = &v
		}
		if e.DelayNS != nil {
			v := *e.DelayNS
			r.Events[i].DelayNS = &v
		}
	}
	r.Timings = append([]TimingObservation(nil), t.Timings...)
	for i, t := range r.Timings {
		if t.Attempt != nil {
			x := *t.Attempt
			r.Timings[i].Attempt = &x
		}
		if t.Delivery != nil {
			x := *t.Delivery
			r.Timings[i].Delivery = &x
		}
		if t.DurationNS != nil {
			n := *t.DurationNS
			r.Timings[i].DurationNS = &n
		}
	}
	return r
}
func (o *EffectObserver) EffectTimeline(id EffectID) (EffectTimeline, bool) {
	o.mu.Lock()
	defer o.mu.Unlock()
	for _, d := range o.details {
		if d.Effect == id {
			return cloneTimeline(*d), true
		}
	}
	return EffectTimeline{}, false
}
func (o *EffectObserver) PauseIntervals() []PauseInterval {
	o.mu.Lock()
	defer o.mu.Unlock()
	r := append([]PauseInterval(nil), o.pauses...)
	for i, p := range r {
		if p.Start != nil {
			x := *p.Start
			r[i].Start = &x
		}
		if p.End != nil {
			x := *p.End
			r[i].End = &x
		}
	}
	return r
}
func (o *EffectObserver) HostGaugeSnapshot(l Labels, unresolved, running, ready, completion uint64) error {
	o.mu.Lock()
	defer o.mu.Unlock()
	l.Origin = o.options.Origin
	l.Outcome = OutcomeNone
	l.DebuggerAffected = false
	if !o.options.Labels.Accepts(l) {
		return o.invalid()
	}
	if o.options.Counters {
		for _, entry := range []struct {
			family MetricFamily
			value  uint64
		}{{Unresolved, unresolved}, {Running, running}, {ReadyDepth, ready}, {CompletionDepth, completion}} {
			o.metrics.SetGauge(entry.family, l, entry.value)
		}
		o.metrics.SetGaugeScopeFor(l, HostSnapshot)
	}
	return nil
}

type SlowTimingQuery struct {
	Operation                  uint16
	Family                     MetricFamily
	ThresholdNS                uint64
	Origin                     *MeasurementOrigin
	Outcome                    *Outcome
	IncludeDebuggerAffected    bool
	MaxResults, MaxResultBytes int
}
type SlowTimingMatch struct {
	Effect EffectID
	Origin RequestOrigin
	Labels Labels
	Timing TimingObservation
}
type SlowTimingResult struct {
	Matches                                 []SlowTimingMatch
	Run, Epoch, DetailRevision              uint64
	Query                                   SlowTimingQuery
	ResultBytes                             int
	Matched, Inspected, UnknownMeasurements uint64
	Complete, Truncated, RetainedDetailOnly bool
	Availability                            string
}

func (o *EffectObserver) SlowTimings(q SlowTimingQuery) (SlowTimingResult, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	baseBytes := max(128, int(unsafe.Sizeof(SlowTimingResult{})))
	if q.Origin != nil {
		baseBytes += int(unsafe.Sizeof(MeasurementOrigin("")))
	}
	if q.Outcome != nil {
		baseBytes += int(unsafe.Sizeof(Outcome("")))
	}
	if int(q.Operation) >= len(o.options.Labels.Operations) || metricKind(q.Family) != HistogramMetric || q.MaxResults < 0 || q.MaxResults > 4096 || q.MaxResultBytes < baseBytes || q.MaxResultBytes > 4<<20 {
		return SlowTimingResult{}, ErrLifecycle
	}
	labels := Labels{Origin: OriginLive}
	if q.Origin != nil {
		labels.Origin = *q.Origin
	}
	if q.Outcome != nil {
		labels.Outcome = *q.Outcome
	}
	if !o.options.Labels.Accepts(labels) {
		return SlowTimingResult{}, ErrLifecycle
	}
	if q.Origin != nil {
		v := *q.Origin
		q.Origin = &v
	}
	if q.Outcome != nil {
		v := *q.Outcome
		q.Outcome = &v
	}
	r := SlowTimingResult{Run: o.options.Run, Epoch: o.options.Epoch, DetailRevision: o.eventSequence, Query: q, ResultBytes: baseBytes, Complete: true, RetainedDetailOnly: true, Availability: "available"}
	if !o.options.Timings {
		r.Availability = "timings-disabled"
		r.Complete = false
		return r, nil
	}
	if !o.options.Details {
		r.Availability = "details-disabled"
		r.Complete = false
		return r, nil
	}
	r.Complete = o.health.DetailEvictions == 0 && o.health.DetailRejections == 0 && o.health.DetailEventDrops == 0
	// Match bytes include the largest detached timing payload, not just the
	// slice header. Charge allocated capacity so append growth cannot hide it.
	matchBytes := max(192, int(unsafe.Sizeof(SlowTimingMatch{}))+int(unsafe.Sizeof(uint64(0)))+int(unsafe.Sizeof(AttemptID{}))+int(unsafe.Sizeof(DeliveryID{})))
	maxByBytes := (q.MaxResultBytes - baseBytes) / matchBytes
	maxRetained := min(q.MaxResults, maxByBytes)
	for _, d := range o.details {
		if d.Labels.Operation != q.Operation || (q.Origin != nil && d.Labels.Origin != *q.Origin) {
			continue
		}
		r.Complete = r.Complete && d.Complete
		for _, t := range d.Timings {
			if t.Family != q.Family || (t.DebuggerAffected && !q.IncludeDebuggerAffected) || (q.Outcome != nil && t.Outcome != *q.Outcome) {
				continue
			}
			satInc(&r.Inspected)
			if t.DurationNS == nil {
				satInc(&r.UnknownMeasurements)
				r.Complete = false
				continue
			}
			if *t.DurationNS < q.ThresholdNS {
				continue
			}
			satInc(&r.Matched)
			if len(r.Matches) >= maxRetained {
				r.Truncated = true
				continue
			}
			if len(r.Matches) == cap(r.Matches) {
				capacity := min(maxRetained, max(1, cap(r.Matches)*2))
				matches := make([]SlowTimingMatch, len(r.Matches), capacity)
				copy(matches, r.Matches)
				r.Matches = matches
				r.ResultBytes = baseBytes + capacity*matchBytes
			}
			v := *t.DurationNS
			t.DurationNS = &v
			if t.Attempt != nil {
				x := *t.Attempt
				t.Attempt = &x
			}
			if t.Delivery != nil {
				x := *t.Delivery
				t.Delivery = &x
			}
			labels := d.Labels
			labels.Outcome = t.Outcome
			labels.DebuggerAffected = t.DebuggerAffected
			r.Matches = append(r.Matches, SlowTimingMatch{d.Effect, d.Origin, labels, t})
		}
	}
	r.Complete = r.Complete && !r.Truncated
	return r, nil
}

// PendingAge is absent after rejection or resolution. A pending effect with no
// captured start endpoint reports an error instead of implying it is not pending.
func (t EffectTimeline) PendingAge(now LocalInstant) (*uint64, error) {
	if t.ResolvedKnown || t.Admission == OutcomeRejected {
		return nil, nil
	}
	if t.Requested == nil {
		return nil, errors.New("missing endpoint")
	}
	n, e := now.ElapsedSince(*t.Requested)
	if e != nil {
		return nil, e
	}
	return &n, nil
}
func (o *EffectObserver) EffectDetails(id EffectID) (EffectTimeline, bool) {
	return o.EffectTimeline(id)
}

// ExportMetrics invokes a host-selected sink outside locks and catches a failing
// exporter without changing host lifecycle facts or retrying application work.
func (o *EffectObserver) ExportMetrics(sink func(MetricSnapshot) error) (err error) {
	if sink == nil {
		return ErrLifecycle
	}
	snapshot, e := o.MetricSnapshot()
	if e != nil {
		return e
	}
	defer func() {
		if recover() != nil {
			err = errors.New("metric exporter panic")
		}
		if err != nil {
			o.mu.Lock()
			satInc(&o.health.CallbackFaults)
			o.mu.Unlock()
		}
	}()
	return sink(snapshot)
}

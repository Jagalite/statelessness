//! Host-owned effect lifecycle accounting. These hooks never run a reducer,
//! deliver an input, execute an effect, or modify application time.
//!
//! Keep a token with the actual work. Call `reserve_publication` while holding
//! the destination queue's publication lock, immediately BEFORE making the item
//! visible. Its timestamp/state are then visible to a faster consumer. Failed
//! publication must call `publication_failed` before releasing that lock.
//! Sequential retry adapters are qualified; raw overlapping attempts are facts,
//! not an additive critical-path or CPU-time measurement.
use crate::metrics::{
    self, GaugeScope, HistogramSchema, LabelCatalog, Labels, MeasurementOrigin, MetricDescriptor,
    MetricError, MetricFamily as F, MetricHealth, MetricLimits, MetricSnapshot, MetricsStore,
    Outcome, SnapshotTime,
};
use std::collections::VecDeque;
use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::task::{Context, Poll};
use std::time::Instant;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct ClockDomain(pub u64);
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct LocalInstant {
    pub domain: ClockDomain,
    pub nanos: u64,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MeasurementError {
    MissingEndpoint,
    DifferentClockDomain,
    Reversed,
    ClockUnavailable,
}
impl LocalInstant {
    pub fn elapsed_since(self, start: Self) -> Result<u64, MeasurementError> {
        if self.domain != start.domain {
            return Err(MeasurementError::DifferentClockDomain);
        }
        self.nanos
            .checked_sub(start.nanos)
            .ok_or(MeasurementError::Reversed)
    }
    fn snapshot(self) -> SnapshotTime {
        SnapshotTime {
            domain: self.domain.0,
            nanos: self.nanos,
        }
    }
}
pub trait Clock: Send + Sync {
    fn now(&self) -> Result<LocalInstant, MeasurementError>;
}
pub struct MonotonicClock {
    start: Instant,
    domain: ClockDomain,
}
impl MonotonicClock {
    pub fn new(domain: ClockDomain) -> Self {
        Self {
            start: Instant::now(),
            domain,
        }
    }
}
impl Clock for MonotonicClock {
    fn now(&self) -> Result<LocalInstant, MeasurementError> {
        let nanos = u64::try_from(
            Instant::now()
                .checked_duration_since(self.start)
                .ok_or(MeasurementError::Reversed)?
                .as_nanos(),
        )
        .map_err(|_| MeasurementError::ClockUnavailable)?;
        Ok(LocalInstant {
            domain: self.domain,
            nanos,
        })
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct EffectId {
    pub run: u64,
    pub epoch: u64,
    pub serial: u64,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct AttemptId {
    pub effect: EffectId,
    pub serial: u64,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct DeliveryId {
    pub effect: EffectId,
    pub serial: u64,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RequestOrigin {
    pub run: u64,
    pub epoch: u64,
    pub machine: u64,
    pub transition_sequence: u64,
    pub output_index: u32,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RuntimeOutcome {
    Unclassified,
    Success,
    Failure,
    Cancelled,
}
impl RuntimeOutcome {
    fn label(self) -> Outcome {
        match self {
            Self::Unclassified => Outcome::None,
            Self::Success => Outcome::Success,
            Self::Failure => Outcome::Failure,
            Self::Cancelled => Outcome::Cancelled,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DeliveryDisposition {
    Accepted,
    Rejected,
    Ignored,
}
impl DeliveryDisposition {
    fn label(self) -> Outcome {
        match self {
            Self::Accepted => Outcome::Accepted,
            Self::Rejected => Outcome::Rejected,
            Self::Ignored => Outcome::Ignored,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Admission {
    Accepted,
    Rejected,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LifecycleError {
    ForeignToken,
    WrongEpoch,
    InvalidLabels,
    WrongPhase,
    DuplicateObservation,
    Limit,
    Exhausted,
    Poisoned,
}
impl std::fmt::Display for LifecycleError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{self:?}")
    }
}
impl std::error::Error for LifecycleError {}
#[derive(Clone, Debug)]
pub struct EffectOptions {
    pub run: u64,
    pub epoch: u64,
    pub origin: MeasurementOrigin,
    pub counters: bool,
    pub timings: bool,
    pub details: bool,
    pub max_details: usize,
    pub max_detail_bytes: usize,
    pub max_events_per_effect: usize,
    pub max_attempts_per_effect: u64,
    pub max_deliveries_per_effect: u64,
    pub max_pause_intervals: usize,
    pub gauge_scope: GaugeScope,
    pub labels: LabelCatalog,
    pub histogram: HistogramSchema,
    pub metric_limits: MetricLimits,
}
impl Default for EffectOptions {
    fn default() -> Self {
        Self {
            run: 1,
            epoch: 1,
            origin: MeasurementOrigin::Live,
            counters: true,
            timings: true,
            details: true,
            max_details: 4096,
            max_detail_bytes: 8 * 1024 * 1024,
            max_events_per_effect: 128,
            max_attempts_per_effect: 1024,
            max_deliveries_per_effect: 1024,
            max_pause_intervals: 64,
            gauge_scope: GaugeScope::ObservedSinceAttach,
            labels: LabelCatalog::default(),
            histogram: HistogramSchema::default(),
            metric_limits: MetricLimits::default(),
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LifecycleEventKind {
    Requested,
    Admission(Admission),
    AttemptCreated(AttemptId),
    Ready(AttemptId),
    RetryScheduled {
        attempt: AttemptId,
        delay_ns: u64,
    },
    AttemptStarted(AttemptId),
    AttemptFinished {
        attempt: AttemptId,
        outcome: RuntimeOutcome,
    },
    Resolved(RuntimeOutcome),
    CancellationRequested,
    CancellationAcknowledged,
    PublicationReserved(DeliveryId),
    PublicationFailed(DeliveryId),
    DeliveryBegun(DeliveryId),
    DeliveryObserved {
        delivery: DeliveryId,
        disposition: DeliveryDisposition,
    },
    Settled,
    Abandoned {
        attempt: Option<AttemptId>,
    },
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LifecycleEvent {
    pub sequence: u64,
    pub at: Option<LocalInstant>,
    pub kind: LifecycleEventKind,
    pub debugger_affected: bool,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TimingObservation {
    pub family: F,
    pub attempt: Option<AttemptId>,
    pub delivery: Option<DeliveryId>,
    pub duration_ns: Result<u64, MeasurementError>,
    pub outcome: Outcome,
    pub debugger_affected: bool,
}
/// A completed endpoint threshold, over previously captured detail only.
/// This is a diagnostic selection, never the complete latency distribution.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SlowTimingQuery {
    pub operation: u16,
    pub family: F,
    pub threshold_ns: u64,
    pub measurement_origin: Option<MeasurementOrigin>,
    pub outcome: Option<Outcome>,
    pub include_debugger_affected: bool,
    pub max_results: usize,
    pub max_result_bytes: usize,
}
impl SlowTimingQuery {
    pub fn new(operation: u16, family: F, threshold_ns: u64) -> Self {
        Self {
            operation,
            family,
            threshold_ns,
            measurement_origin: Some(MeasurementOrigin::Live),
            outcome: None,
            include_debugger_affected: false,
            max_results: 64,
            max_result_bytes: 64 * 1024,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SlowTimingMatch {
    pub effect: EffectId,
    pub origin: RequestOrigin,
    pub labels: Labels,
    pub timing: TimingObservation,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SlowTimingAvailability {
    Available,
    TimingDisabled,
    DetailsDisabled,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SlowTimingResult {
    pub run: u64,
    pub epoch: u64,
    pub detail_revision: u64,
    pub query: SlowTimingQuery,
    pub availability: SlowTimingAvailability,
    pub matches: Vec<SlowTimingMatch>,
    /// Number of valid retained observations reaching the selected threshold.
    pub matched: u64,
    pub inspected: u64,
    pub unknown_measurements: u64,
    pub result_bytes: usize,
    pub truncated: bool,
    /// All selected retained measurements were inspected and returned without
    /// known detail gaps or unknown durations. This is not an all-effects SLA.
    pub complete: bool,
    pub retained_detail_only: bool,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EffectDetails {
    pub id: EffectId,
    pub origin: RequestOrigin,
    pub labels: Labels,
    pub requested: Option<LocalInstant>,
    pub admission: Option<Admission>,
    pub resolution: Option<RuntimeOutcome>,
    pub settled: bool,
    pub cancellation_requested: bool,
    pub cancellation_acknowledged: bool,
    pub running_attempts: u64,
    pub deliveries_observed: u64,
    pub debugger_affected: bool,
    pub abandoned: bool,
    pub complete: bool,
    pub events: Vec<LifecycleEvent>,
    pub timings: Vec<TimingObservation>,
}
impl EffectDetails {
    pub fn pending_age(&self, now: LocalInstant) -> Option<Result<u64, MeasurementError>> {
        if self.resolution.is_none() && self.admission != Some(Admission::Rejected) {
            Some(
                self.requested
                    .ok_or(MeasurementError::MissingEndpoint)
                    .and_then(|r| now.elapsed_since(r)),
            )
        } else {
            None
        }
    }
    pub fn estimated_bytes(&self) -> usize {
        std::mem::size_of::<Self>()
            + self.events.capacity() * std::mem::size_of::<LifecycleEvent>()
            + self.timings.capacity() * std::mem::size_of::<TimingObservation>()
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EffectTimeline {
    pub effect: EffectId,
    pub origin: RequestOrigin,
    pub measurement_origin: MeasurementOrigin,
    pub complete: bool,
    pub events: Vec<LifecycleEvent>,
    pub timings: Vec<TimingObservation>,
}
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PauseInterval {
    pub generation: u64,
    pub start: Option<LocalInstant>,
    pub end: Option<LocalInstant>,
}
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct TelemetryHealth {
    pub metric: MetricHealth,
    pub detail_evictions: u64,
    pub detail_rejections: u64,
    pub detail_event_drops: u64,
    pub duplicate_observations: u64,
    pub duplicate_deliveries: u64,
    pub invalid_lifecycle: u64,
    pub abandoned: u64,
    pub clock_failures: u64,
    pub callback_faults: u64,
    pub pause_interval_evictions: u64,
    pub debugger_affected_measurements: u64,
    pub active_effect_handles: u64,
    pub destructor_hook_losses: u64,
    pub retained_detail_records: usize,
    pub retained_detail_bytes: usize,
    pub effect_handle_bytes: usize,
    pub attempt_handle_bytes: usize,
    pub delivery_handle_bytes: usize,
    pub unobserved_native_debugger_interference_possible: bool,
}
struct Core {
    metrics: MetricsStore,
    details: VecDeque<EffectDetails>,
    detail_bytes: usize,
    health: TelemetryHealth,
    next_effect: u64,
    event_sequence: u64,
    pause_generation: u64,
    paused: bool,
    pauses: VecDeque<PauseInterval>,
}
struct Inner {
    options: EffectOptions,
    clock: Arc<dyn Clock>,
    core: Mutex<Core>,
    active_handles: AtomicU64,
    drop_losses: AtomicU64,
}
#[derive(Clone)]
pub struct EffectObserver {
    inner: Arc<Inner>,
}
struct EffectState {
    id: EffectId,
    labels: Labels,
    requested: Option<LocalInstant>,
    admission: Option<Admission>,
    resolved: Option<(RuntimeOutcome, Option<LocalInstant>)>,
    settled: bool,
    cancel_requested: bool,
    cancel_ack: bool,
    next_attempt: u64,
    next_delivery: u64,
    running: u64,
    first_ready: bool,
    delivery_begun: bool,
    observed: u64,
    last_finish: Option<LocalInstant>,
    pause_generation: u64,
    affected: bool,
    abandoned: bool,
}
#[derive(Clone)]
pub struct EffectHandle {
    observer: EffectObserver,
    state: Arc<EffectToken>,
}
struct EffectToken {
    state: Mutex<EffectState>,
    observer: EffectObserver,
}
impl std::ops::Deref for EffectToken {
    type Target = Mutex<EffectState>;
    fn deref(&self) -> &Self::Target {
        &self.state
    }
}
struct AttemptState {
    id: AttemptId,
    ready: Option<LocalInstant>,
    queued: bool,
    started: bool,
    start: Option<LocalInstant>,
    finished: bool,
    finish: Option<LocalInstant>,
    scheduled: Option<LocalInstant>,
    has_schedule: bool,
    abandoned: bool,
    pause_generation: u64,
    previous_finish: Option<LocalInstant>,
}
#[derive(Clone)]
pub struct AttemptHandle {
    effect: EffectHandle,
    state: Arc<AttemptToken>,
}
struct DeliveryState {
    id: DeliveryId,
    posted: Option<LocalInstant>,
    confirmed: bool,
    failed: bool,
    begun: bool,
    begin: Option<LocalInstant>,
    observed: bool,
}
#[derive(Clone)]
pub struct DeliveryHandle {
    effect: EffectHandle,
    state: Arc<DeliveryToken>,
}
// The final shared token, rather than any one clone, owns missing-endpoint
// accounting. Dropping raw hooks is as honest as dropping an observed future.
struct AttemptToken {
    state: Mutex<AttemptState>,
    effect: EffectHandle,
    abandonment_reported: AtomicBool,
}
impl std::ops::Deref for AttemptToken {
    type Target = Mutex<AttemptState>;
    fn deref(&self) -> &Self::Target {
        &self.state
    }
}
struct DeliveryToken {
    state: Mutex<DeliveryState>,
    effect: EffectHandle,
}
impl std::ops::Deref for DeliveryToken {
    type Target = Mutex<DeliveryState>;
    fn deref(&self) -> &Self::Target {
        &self.state
    }
}
impl EffectHandle {
    pub fn id(&self) -> EffectId {
        self.state.lock().unwrap_or_else(|e| e.into_inner()).id
    }
}
impl AttemptHandle {
    pub fn id(&self) -> AttemptId {
        self.state.lock().unwrap_or_else(|e| e.into_inner()).id
    }
}
impl DeliveryHandle {
    pub fn id(&self) -> DeliveryId {
        self.state.lock().unwrap_or_else(|e| e.into_inner()).id
    }
}
impl EffectObserver {
    pub fn new(options: EffectOptions, clock: Arc<dyn Clock>) -> Result<Self, MetricError> {
        if options.run == 0
            || options.epoch == 0
            || options.max_events_per_effect == 0
            || options.max_attempts_per_effect == 0
            || options.max_deliveries_per_effect == 0
            || options.max_pause_intervals == 0
        {
            return Err(MetricError::InvalidConfig);
        }
        // Even construction skips the clock for runtime-disabled/counters-only.
        let start = if options.timings {
            std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| clock.now()))
                .ok()
                .and_then(Result::ok)
                .map(LocalInstant::snapshot)
        } else {
            None
        };
        let metrics = MetricsStore::new(
            options.epoch,
            start,
            options.labels.clone(),
            options.histogram.clone(),
            options.metric_limits.clone(),
            options.gauge_scope,
        )?;
        let health = TelemetryHealth {
            clock_failures: u64::from(options.timings && start.is_none()),
            effect_handle_bytes: std::mem::size_of::<EffectToken>()
                + std::mem::size_of::<EffectHandle>(),
            attempt_handle_bytes: std::mem::size_of::<AttemptToken>()
                + std::mem::size_of::<AttemptHandle>(),
            delivery_handle_bytes: std::mem::size_of::<DeliveryToken>()
                + std::mem::size_of::<DeliveryHandle>(),
            unobserved_native_debugger_interference_possible: true,
            ..TelemetryHealth::default()
        };
        Ok(Self {
            inner: Arc::new(Inner {
                options,
                clock,
                active_handles: AtomicU64::new(0),
                drop_losses: AtomicU64::new(0),
                core: Mutex::new(Core {
                    metrics,
                    details: VecDeque::new(),
                    detail_bytes: 0,
                    health,
                    next_effect: 0,
                    event_sequence: 0,
                    pause_generation: 0,
                    paused: false,
                    pauses: VecDeque::new(),
                }),
            }),
        })
    }
    fn core(&self) -> MutexGuard<'_, Core> {
        let mut c = self.inner.core.lock().unwrap_or_else(|e| e.into_inner());
        let lost = self.inner.drop_losses.load(Ordering::Relaxed);
        if lost > c.health.destructor_hook_losses {
            let delta = lost - c.health.destructor_hook_losses;
            c.metrics.health_mut().omitted_measurements = c
                .metrics
                .health()
                .omitted_measurements
                .saturating_add(delta);
            c.metrics.mark_incomplete();
            c.health.destructor_hook_losses = lost;
        }
        c
    }
    fn same(&self, effect: &EffectHandle) -> Result<(), LifecycleError> {
        if Arc::ptr_eq(&self.inner, &effect.observer.inner) {
            Ok(())
        } else {
            Err(LifecycleError::ForeignToken)
        }
    }
    fn now(&self) -> Option<LocalInstant> {
        if !self.inner.options.timings {
            return None;
        }
        match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| self.inner.clock.now())) {
            Ok(Ok(t)) => Some(t),
            _ => {
                self.core().health.clock_failures += 1;
                None
            }
        }
    }
    fn error(&self, error: LifecycleError) -> LifecycleError {
        let mut c = self.core();
        if error == LifecycleError::DuplicateObservation {
            c.health.duplicate_observations += 1;
        } else {
            c.health.invalid_lifecycle += 1;
        }
        error
    }
    fn duplicate(&self, s: &EffectState) -> LifecycleError {
        let mut c = self.core();
        c.health.duplicate_observations += 1;
        if self.inner.options.counters {
            c.metrics.increment(F::DuplicateObservations, s.labels, 1);
        }
        LifecycleError::DuplicateObservation
    }
    fn count(&self, c: &mut Core, f: F, l: Labels) {
        if self.inner.options.counters {
            c.metrics.increment(f, l, 1);
        }
    }
    fn gauge(&self, c: &mut Core, f: F, l: Labels, d: i64) {
        if self.inner.options.counters {
            c.metrics.gauge_delta(f, l, d);
        }
    }
    fn affected(c: &Core, s: &mut EffectState) -> bool {
        s.affected |= c.paused || s.pause_generation != c.pause_generation;
        s.affected
    }
    fn event(
        &self,
        c: &mut Core,
        s: &EffectState,
        kind: LifecycleEventKind,
        at: Option<LocalInstant>,
    ) {
        if !self.inner.options.details {
            return;
        }
        c.event_sequence = c.event_sequence.saturating_add(1);
        if let Some(index) = c.details.iter().position(|d| d.id == s.id) {
            let detail = &mut c.details[index];
            detail.admission = s.admission;
            detail.resolution = s.resolved.map(|r| r.0);
            detail.settled = s.settled;
            detail.running_attempts = s.running;
            detail.deliveries_observed = s.observed;
            detail.cancellation_requested = s.cancel_requested;
            detail.cancellation_acknowledged = s.cancel_ack;
            detail.debugger_affected = s.affected;
            detail.abandoned = s.abandoned;
            detail.complete &= !s.abandoned;
            if detail.events.len() >= self.inner.options.max_events_per_effect {
                detail.complete = false;
                c.health.detail_event_drops += 1;
                return;
            }
            let bytes = std::mem::size_of::<LifecycleEvent>();
            if bytes
                > self
                    .inner
                    .options
                    .max_detail_bytes
                    .saturating_sub(c.detail_bytes)
            {
                detail.complete = false;
                c.health.detail_event_drops += 1;
                return;
            }
            detail.events.reserve_exact(1);
            detail.events.push(LifecycleEvent {
                sequence: c.event_sequence,
                at,
                kind,
                debugger_affected: s.affected,
            });
            c.detail_bytes += bytes;
        }
    }
    // Keep endpoint and identity arguments explicit at every host boundary.
    #[allow(clippy::too_many_arguments)]
    fn measurement(
        &self,
        c: &mut Core,
        s: &EffectState,
        f: F,
        start: Option<LocalInstant>,
        end: Option<LocalInstant>,
        attempt: Option<AttemptId>,
        delivery: Option<DeliveryId>,
        outcome: Outcome,
    ) {
        if !self.inner.options.timings {
            return;
        }
        let duration = match (start, end) {
            (Some(a), Some(b)) => b.elapsed_since(a),
            _ => Err(MeasurementError::MissingEndpoint),
        };
        let mut labels = s.labels;
        labels.outcome = outcome;
        labels.debugger_affected = s.affected;
        match duration {
            Ok(ns) => {
                c.metrics.observe(f, labels, ns);
                if s.affected {
                    c.health.debugger_affected_measurements += 1;
                }
            }
            Err(MeasurementError::MissingEndpoint) => {
                c.metrics.health_mut().omitted_measurements += 1;
                c.metrics.mark_incomplete();
            }
            Err(_) => {
                c.metrics.health_mut().invalid_measurements += 1;
                c.metrics.mark_incomplete();
                self.count(c, F::InvalidMeasurements, labels);
            }
        }
        if self.inner.options.details
            && let Some(d) = c.details.iter_mut().find(|d| d.id == s.id)
        {
            let bytes = std::mem::size_of::<TimingObservation>();
            if d.timings.len() < self.inner.options.max_events_per_effect
                && bytes
                    <= self
                        .inner
                        .options
                        .max_detail_bytes
                        .saturating_sub(c.detail_bytes)
            {
                d.timings.reserve_exact(1);
                d.timings.push(TimingObservation {
                    family: f,
                    attempt,
                    delivery,
                    duration_ns: duration,
                    outcome,
                    debugger_affected: s.affected,
                });
                c.detail_bytes += bytes;
            } else {
                d.complete = false;
                c.health.detail_event_drops += 1;
            }
        }
    }
    pub fn requested(
        &self,
        origin: RequestOrigin,
        mut labels: Labels,
    ) -> Result<EffectHandle, LifecycleError> {
        if origin.run != self.inner.options.run || origin.epoch != self.inner.options.epoch {
            return Err(self.error(LifecycleError::WrongEpoch));
        }
        labels.origin = self.inner.options.origin;
        labels.outcome = Outcome::None;
        labels.debugger_affected = false;
        if !self.core().metrics.labels_valid(labels) {
            return Err(self.error(LifecycleError::InvalidLabels));
        }
        let at = self.now();
        let mut c = self.core();
        c.next_effect = c
            .next_effect
            .checked_add(1)
            .ok_or(LifecycleError::Exhausted)?;
        let id = EffectId {
            run: origin.run,
            epoch: origin.epoch,
            serial: c.next_effect,
        };
        let state = EffectState {
            id,
            labels,
            requested: at,
            admission: None,
            resolved: None,
            settled: false,
            cancel_requested: false,
            cancel_ack: false,
            next_attempt: 0,
            next_delivery: 0,
            running: 0,
            first_ready: false,
            delivery_begun: false,
            observed: 0,
            last_finish: None,
            pause_generation: c.pause_generation,
            affected: c.paused,
            abandoned: false,
        };
        self.count(&mut c, F::Requests, labels);
        self.inner.active_handles.fetch_add(1, Ordering::Relaxed);
        if self.inner.options.details {
            let detail = EffectDetails {
                id,
                origin,
                labels,
                requested: at,
                admission: None,
                resolution: None,
                settled: false,
                cancellation_requested: false,
                cancellation_acknowledged: false,
                running_attempts: 0,
                deliveries_observed: 0,
                debugger_affected: c.paused,
                abandoned: false,
                complete: true,
                events: Vec::new(),
                timings: Vec::new(),
            };
            let bytes = detail.estimated_bytes();
            if self.inner.options.max_details == 0 || bytes > self.inner.options.max_detail_bytes {
                c.health.detail_rejections += 1;
            } else {
                while c.details.len() >= self.inner.options.max_details
                    || bytes
                        > self
                            .inner
                            .options
                            .max_detail_bytes
                            .saturating_sub(c.detail_bytes)
                {
                    if let Some(old) = c.details.pop_front() {
                        c.detail_bytes = c.detail_bytes.saturating_sub(old.estimated_bytes());
                        c.health.detail_evictions += 1;
                    } else {
                        break;
                    }
                }
                c.detail_bytes += bytes;
                c.details.push_back(detail);
            }
        }
        self.event(&mut c, &state, LifecycleEventKind::Requested, at);
        Ok(EffectHandle {
            observer: self.clone(),
            state: Arc::new(EffectToken {
                state: Mutex::new(state),
                observer: self.clone(),
            }),
        })
    }
    pub fn admission(
        &self,
        effect: &EffectHandle,
        admission: Admission,
    ) -> Result<(), LifecycleError> {
        self.same(effect)?;
        let mut s = effect.state.lock().map_err(|_| LifecycleError::Poisoned)?;
        if s.admission.is_some() {
            return Err(self.duplicate(&s));
        }
        if s.resolved.is_some() {
            return Err(self.error(LifecycleError::WrongPhase));
        }
        let at = self.now();
        let mut c = self.core();
        s.admission = Some(admission);
        let mut labels = s.labels;
        labels.outcome = if admission == Admission::Accepted {
            Outcome::Accepted
        } else {
            Outcome::Rejected
        };
        self.count(&mut c, F::Admissions, labels);
        if admission == Admission::Accepted {
            self.gauge(&mut c, F::Unresolved, s.labels, 1);
        }
        self.event(&mut c, &s, LifecycleEventKind::Admission(admission), at);
        Ok(())
    }
    /// Allocation/submission does not count as an actual attempt start.
    pub fn attempt_created(&self, effect: &EffectHandle) -> Result<AttemptHandle, LifecycleError> {
        self.same(effect)?;
        let mut s = effect.state.lock().map_err(|_| LifecycleError::Poisoned)?;
        if s.admission != Some(Admission::Accepted) || s.resolved.is_some() {
            return Err(self.error(LifecycleError::WrongPhase));
        }
        if s.next_attempt >= self.inner.options.max_attempts_per_effect {
            return Err(self.error(LifecycleError::Limit));
        }
        s.next_attempt += 1;
        let id = AttemptId {
            effect: s.id,
            serial: s.next_attempt,
        };
        let mut c = self.core();
        let a = AttemptState {
            id,
            ready: None,
            queued: false,
            started: false,
            start: None,
            finished: false,
            finish: None,
            scheduled: None,
            has_schedule: false,
            abandoned: false,
            pause_generation: c.pause_generation,
            previous_finish: s.last_finish,
        };
        self.event(&mut c, &s, LifecycleEventKind::AttemptCreated(id), None);
        drop(c);
        drop(s);
        Ok(AttemptHandle {
            effect: effect.clone(),
            state: Arc::new(AttemptToken {
                state: Mutex::new(a),
                effect: effect.clone(),
                abandonment_reported: AtomicBool::new(false),
            }),
        })
    }
    pub fn retry_scheduled(
        &self,
        attempt: &AttemptHandle,
        delay_ns: u64,
    ) -> Result<(), LifecycleError> {
        self.same(&attempt.effect)?;
        let mut a = attempt.state.lock().map_err(|_| LifecycleError::Poisoned)?;
        let mut s = attempt
            .effect
            .state
            .lock()
            .map_err(|_| LifecycleError::Poisoned)?;
        if a.started || a.queued || a.has_schedule || a.abandoned || s.resolved.is_some() {
            return Err(self.error(LifecycleError::WrongPhase));
        }
        let at = self.now();
        a.scheduled = at;
        a.has_schedule = true;
        let mut c = self.core();
        Self::affected(&c, &mut s);
        if self.inner.options.timings {
            let mut labels = s.labels;
            labels.debugger_affected = s.affected;
            c.metrics.observe(F::ScheduledBackoff, labels, delay_ns);
            if s.affected {
                c.health.debugger_affected_measurements += 1;
            }
        }
        self.event(
            &mut c,
            &s,
            LifecycleEventKind::RetryScheduled {
                attempt: a.id,
                delay_ns,
            },
            at,
        );
        Ok(())
    }
    pub fn ready_queued(&self, attempt: &AttemptHandle) -> Result<(), LifecycleError> {
        self.same(&attempt.effect)?;
        let mut a = attempt.state.lock().map_err(|_| LifecycleError::Poisoned)?;
        let mut s = attempt
            .effect
            .state
            .lock()
            .map_err(|_| LifecycleError::Poisoned)?;
        if a.queued {
            return Err(self.duplicate(&s));
        }
        if a.started || a.finished || a.abandoned || s.resolved.is_some() {
            return Err(self.error(LifecycleError::WrongPhase));
        }
        let at = self.now();
        a.queued = true;
        a.ready = at;
        let mut c = self.core();
        Self::affected(&c, &mut s);
        self.gauge(&mut c, F::ReadyDepth, s.labels, 1);
        if !s.first_ready {
            self.measurement(
                &mut c,
                &s,
                F::AdmissionDelay,
                s.requested,
                at,
                Some(a.id),
                None,
                Outcome::None,
            );
            s.first_ready = true;
        }
        if a.has_schedule {
            self.measurement(
                &mut c,
                &s,
                F::EligibilityWait,
                a.scheduled,
                at,
                Some(a.id),
                None,
                Outcome::None,
            );
        }
        self.event(&mut c, &s, LifecycleEventKind::Ready(a.id), at);
        Ok(())
    }
    /// Positive host observation that a queued attempt was removed without start.
    pub fn ready_removed(&self, attempt: &AttemptHandle) -> Result<(), LifecycleError> {
        self.same(&attempt.effect)?;
        let mut a = attempt.state.lock().map_err(|_| LifecycleError::Poisoned)?;
        let s = attempt
            .effect
            .state
            .lock()
            .map_err(|_| LifecycleError::Poisoned)?;
        if !a.queued || a.started {
            return Err(self.error(LifecycleError::WrongPhase));
        }
        a.queued = false;
        self.gauge(&mut self.core(), F::ReadyDepth, s.labels, -1);
        Ok(())
    }
    pub fn attempt_started(&self, attempt: &AttemptHandle) -> Result<(), LifecycleError> {
        self.same(&attempt.effect)?;
        let mut a = attempt.state.lock().map_err(|_| LifecycleError::Poisoned)?;
        let mut s = attempt
            .effect
            .state
            .lock()
            .map_err(|_| LifecycleError::Poisoned)?;
        if a.started {
            return Err(self.duplicate(&s));
        }
        if a.finished || a.abandoned || s.resolved.is_some() {
            return Err(self.error(LifecycleError::WrongPhase));
        }
        let at = self.now();
        let mut c = self.core();
        Self::affected(&c, &mut s);
        a.started = true;
        a.start = at;
        a.pause_generation = c.pause_generation;
        s.running += 1;
        self.count(&mut c, F::Attempts, s.labels);
        self.gauge(&mut c, F::Running, s.labels, 1);
        if a.queued {
            a.queued = false;
            self.gauge(&mut c, F::ReadyDepth, s.labels, -1);
            self.measurement(
                &mut c,
                &s,
                F::QueueWait,
                a.ready,
                at,
                Some(a.id),
                None,
                Outcome::None,
            );
        }
        if a.id.serial > 1 {
            self.measurement(
                &mut c,
                &s,
                F::InterAttemptGap,
                a.previous_finish,
                at,
                Some(a.id),
                None,
                Outcome::None,
            );
        }
        self.event(&mut c, &s, LifecycleEventKind::AttemptStarted(a.id), at);
        Ok(())
    }
    pub fn attempt_finished(
        &self,
        attempt: &AttemptHandle,
        outcome: RuntimeOutcome,
    ) -> Result<(), LifecycleError> {
        self.same(&attempt.effect)?;
        let mut a = attempt.state.lock().map_err(|_| LifecycleError::Poisoned)?;
        let mut s = attempt
            .effect
            .state
            .lock()
            .map_err(|_| LifecycleError::Poisoned)?;
        if a.finished {
            return Err(self.duplicate(&s));
        }
        if !a.started {
            return Err(self.error(LifecycleError::WrongPhase));
        }
        let at = self.now();
        let mut c = self.core();
        Self::affected(&c, &mut s);
        a.finished = true;
        a.finish = at;
        s.last_finish = at;
        s.running = s.running.saturating_sub(1);
        let mut labels = s.labels;
        labels.outcome = outcome.label();
        self.count(&mut c, F::AttemptOutcomes, labels);
        self.gauge(&mut c, F::Running, s.labels, -1);
        self.measurement(
            &mut c,
            &s,
            F::AttemptElapsed,
            a.start,
            at,
            Some(a.id),
            None,
            outcome.label(),
        );
        self.event(
            &mut c,
            &s,
            LifecycleEventKind::AttemptFinished {
                attempt: a.id,
                outcome,
            },
            at,
        );
        Ok(())
    }
    /// Fix a logical result. `selected_attempt` defines resolution-overhead's
    /// relevant finish; unfinished/hedged attempts remain physically running.
    pub fn resolved(
        &self,
        effect: &EffectHandle,
        outcome: RuntimeOutcome,
        selected_attempt: Option<&AttemptHandle>,
    ) -> Result<(), LifecycleError> {
        self.same(effect)?;
        let finish = if let Some(a) = selected_attempt {
            if !Arc::ptr_eq(&effect.state, &a.effect.state) {
                return Err(self.error(LifecycleError::ForeignToken));
            }
            let a = a.state.lock().map_err(|_| LifecycleError::Poisoned)?;
            if !a.finished {
                return Err(self.error(LifecycleError::WrongPhase));
            }
            Some((a.id, a.finish))
        } else {
            None
        };
        let mut s = effect.state.lock().map_err(|_| LifecycleError::Poisoned)?;
        if s.resolved.is_some() {
            return Err(self.duplicate(&s));
        }
        if s.admission != Some(Admission::Accepted) {
            return Err(self.error(LifecycleError::WrongPhase));
        }
        let at = self.now();
        let mut c = self.core();
        Self::affected(&c, &mut s);
        s.resolved = Some((outcome, at));
        let mut labels = s.labels;
        labels.outcome = outcome.label();
        self.count(&mut c, F::Resolutions, labels);
        self.gauge(&mut c, F::Unresolved, s.labels, -1);
        self.measurement(
            &mut c,
            &s,
            F::Resolution,
            s.requested,
            at,
            None,
            None,
            outcome.label(),
        );
        if let Some((id, f)) = finish {
            self.measurement(
                &mut c,
                &s,
                F::ResolutionOverhead,
                f,
                at,
                Some(id),
                None,
                outcome.label(),
            );
        }
        self.event(&mut c, &s, LifecycleEventKind::Resolved(outcome), at);
        Ok(())
    }
    pub fn cancellation_requested(&self, effect: &EffectHandle) -> Result<(), LifecycleError> {
        self.same(effect)?;
        let mut s = effect.state.lock().map_err(|_| LifecycleError::Poisoned)?;
        if s.cancel_requested {
            return Err(self.duplicate(&s));
        }
        s.cancel_requested = true;
        let at = self.now();
        let mut c = self.core();
        self.count(&mut c, F::CancellationRequests, s.labels);
        self.event(&mut c, &s, LifecycleEventKind::CancellationRequested, at);
        Ok(())
    }
    /// Acknowledges cancellation, without inventing termination or resolution.
    pub fn cancellation_acknowledged(&self, effect: &EffectHandle) -> Result<(), LifecycleError> {
        self.same(effect)?;
        let mut s = effect.state.lock().map_err(|_| LifecycleError::Poisoned)?;
        if s.cancel_ack {
            return Err(self.duplicate(&s));
        }
        if !s.cancel_requested {
            return Err(self.error(LifecycleError::WrongPhase));
        }
        s.cancel_ack = true;
        let at = self.now();
        let mut c = self.core();
        self.count(&mut c, F::CancellationAcknowledgements, s.labels);
        self.event(&mut c, &s, LifecycleEventKind::CancellationAcknowledged, at);
        Ok(())
    }
    /// Call under host queue synchronization before visibility. Accounting and
    /// posting timestamp are committed together, never after publication.
    pub fn reserve_publication(
        &self,
        effect: &EffectHandle,
    ) -> Result<DeliveryHandle, LifecycleError> {
        self.same(effect)?;
        let mut s = effect.state.lock().map_err(|_| LifecycleError::Poisoned)?;
        if s.resolved.is_none() {
            return Err(self.error(LifecycleError::WrongPhase));
        }
        if s.next_delivery >= self.inner.options.max_deliveries_per_effect {
            return Err(self.error(LifecycleError::Limit));
        }
        let at = self.now();
        let mut c = self.core();
        Self::affected(&c, &mut s);
        s.next_delivery += 1;
        let id = DeliveryId {
            effect: s.id,
            serial: s.next_delivery,
        };
        self.gauge(&mut c, F::CompletionDepth, s.labels, 1);
        self.event(&mut c, &s, LifecycleEventKind::PublicationReserved(id), at);
        drop(c);
        drop(s);
        Ok(DeliveryHandle {
            effect: effect.clone(),
            state: Arc::new(DeliveryToken {
                effect: effect.clone(),
                state: Mutex::new(DeliveryState {
                    id,
                    posted: at,
                    confirmed: false,
                    failed: false,
                    begun: false,
                    begin: None,
                    observed: false,
                }),
            }),
        })
    }
    /// Confirm successful visibility without taking a new posting timestamp.
    /// A consumer beginning delivery also confirms it, so fast consumers win
    /// the same once-only accounting race. Call before releasing publication
    /// synchronization when possible. Failed reservations never produce a
    /// completion-preparation sample.
    pub fn publication_committed(&self, delivery: &DeliveryHandle) -> Result<(), LifecycleError> {
        self.same(&delivery.effect)?;
        let mut d = delivery
            .state
            .lock()
            .map_err(|_| LifecycleError::Poisoned)?;
        let s = delivery
            .effect
            .state
            .lock()
            .map_err(|_| LifecycleError::Poisoned)?;
        if d.failed {
            return Err(self.error(LifecycleError::WrongPhase));
        }
        if !d.confirmed {
            d.confirmed = true;
            self.measurement(
                &mut self.core(),
                &s,
                F::CompletionPreparation,
                s.resolved.and_then(|r| r.1),
                d.posted,
                None,
                Some(d.id),
                Outcome::None,
            );
        }
        Ok(())
    }
    pub fn publication_failed(&self, delivery: &DeliveryHandle) -> Result<(), LifecycleError> {
        self.same(&delivery.effect)?;
        let mut d = delivery
            .state
            .lock()
            .map_err(|_| LifecycleError::Poisoned)?;
        let s = delivery
            .effect
            .state
            .lock()
            .map_err(|_| LifecycleError::Poisoned)?;
        if d.failed {
            return Err(self.duplicate(&s));
        }
        if d.begun || d.confirmed {
            return Err(self.error(LifecycleError::WrongPhase));
        }
        d.failed = true;
        let at = self.now();
        let mut c = self.core();
        self.gauge(&mut c, F::CompletionDepth, s.labels, -1);
        self.event(&mut c, &s, LifecycleEventKind::PublicationFailed(d.id), at);
        Ok(())
    }
    pub fn delivery_begun(&self, delivery: &DeliveryHandle) -> Result<(), LifecycleError> {
        self.same(&delivery.effect)?;
        let mut d = delivery
            .state
            .lock()
            .map_err(|_| LifecycleError::Poisoned)?;
        let mut s = delivery
            .effect
            .state
            .lock()
            .map_err(|_| LifecycleError::Poisoned)?;
        if d.begun {
            return Err(self.duplicate(&s));
        }
        if d.failed {
            return Err(self.error(LifecycleError::WrongPhase));
        }
        let at = self.now();
        let mut c = self.core();
        Self::affected(&c, &mut s);
        d.begun = true;
        d.begin = at;
        if !d.confirmed {
            d.confirmed = true;
            self.measurement(
                &mut c,
                &s,
                F::CompletionPreparation,
                s.resolved.and_then(|r| r.1),
                d.posted,
                None,
                Some(d.id),
                Outcome::None,
            );
        }
        self.gauge(&mut c, F::CompletionDepth, s.labels, -1);
        self.measurement(
            &mut c,
            &s,
            F::CompletionQueueWait,
            d.posted,
            at,
            None,
            Some(d.id),
            Outcome::None,
        );
        self.measurement(
            &mut c,
            &s,
            F::DeliveryLag,
            s.resolved.and_then(|r| r.1),
            at,
            None,
            Some(d.id),
            Outcome::None,
        );
        if !s.delivery_begun {
            self.measurement(
                &mut c,
                &s,
                F::EndToEnd,
                s.requested,
                at,
                None,
                Some(d.id),
                Outcome::None,
            );
            s.delivery_begun = true;
        }
        self.event(&mut c, &s, LifecycleEventKind::DeliveryBegun(d.id), at);
        Ok(())
    }
    /// Timestamp immediately after the reducer returns, before checking or
    /// recording. Rejected and ignored inputs are still observed deliveries.
    pub fn delivery_observed(
        &self,
        delivery: &DeliveryHandle,
        disposition: DeliveryDisposition,
    ) -> Result<(), LifecycleError> {
        self.same(&delivery.effect)?;
        let mut d = delivery
            .state
            .lock()
            .map_err(|_| LifecycleError::Poisoned)?;
        let mut s = delivery
            .effect
            .state
            .lock()
            .map_err(|_| LifecycleError::Poisoned)?;
        if d.observed {
            return Err(self.duplicate(&s));
        }
        if !d.begun || d.failed {
            return Err(self.error(LifecycleError::WrongPhase));
        }
        let at = self.now();
        let mut c = self.core();
        Self::affected(&c, &mut s);
        d.observed = true;
        let mut labels = s.labels;
        labels.outcome = disposition.label();
        self.count(&mut c, F::Deliveries, labels);
        if s.observed > 0 {
            self.count(&mut c, F::DuplicateDeliveries, s.labels);
            c.health.duplicate_deliveries += 1;
        } else {
            self.measurement(
                &mut c,
                &s,
                F::EndToEndObserved,
                s.requested,
                at,
                None,
                Some(d.id),
                disposition.label(),
            );
        }
        s.observed += 1;
        self.measurement(
            &mut c,
            &s,
            F::CompletionProcessing,
            d.begin,
            at,
            None,
            Some(d.id),
            disposition.label(),
        );
        self.event(
            &mut c,
            &s,
            LifecycleEventKind::DeliveryObserved {
                delivery: d.id,
                disposition,
            },
            at,
        );
        Ok(())
    }
    pub fn settled(&self, effect: &EffectHandle) -> Result<(), LifecycleError> {
        self.same(effect)?;
        let mut s = effect.state.lock().map_err(|_| LifecycleError::Poisoned)?;
        if s.settled {
            return Err(self.duplicate(&s));
        }
        let at = self.now();
        let mut c = self.core();
        Self::affected(&c, &mut s);
        s.settled = true;
        self.measurement(
            &mut c,
            &s,
            F::Settlement,
            s.requested,
            at,
            None,
            None,
            Outcome::None,
        );
        self.event(&mut c, &s, LifecycleEventKind::Settled, at);
        Ok(())
    }
    pub fn attempt_abandoned(&self, attempt: &AttemptHandle) -> Result<(), LifecycleError> {
        self.same(&attempt.effect)?;
        let mut a = attempt.state.lock().map_err(|_| LifecycleError::Poisoned)?;
        let mut s = attempt
            .effect
            .state
            .lock()
            .map_err(|_| LifecycleError::Poisoned)?;
        if a.abandoned || a.finished {
            return Ok(());
        }
        a.abandoned = true;
        s.abandoned = true;
        let mut c = self.core();
        c.health.abandoned += 1;
        c.metrics.mark_incomplete();
        self.count(&mut c, F::Abandoned, s.labels);
        self.event(
            &mut c,
            &s,
            LifecycleEventKind::Abandoned {
                attempt: Some(a.id),
            },
            None,
        );
        Ok(())
    }
    /// Manual relevant dispatch/worker pause annotation. No duration is
    /// subtracted: remote work can continue. Operations overlapping a reported
    /// pause are conservatively marked affected for all their later samples.
    pub fn debugger_pause(&self, paused: bool) {
        let at = self.now();
        let mut c = self.core();
        if c.paused != paused {
            c.paused = paused;
            c.pause_generation = c.pause_generation.saturating_add(1);
            if paused {
                if c.pauses.len() >= self.inner.options.max_pause_intervals {
                    c.pauses.pop_front();
                    c.health.pause_interval_evictions += 1;
                }
                let generation = c.pause_generation;
                c.pauses.push_back(PauseInterval {
                    generation,
                    start: at,
                    end: None,
                });
            } else if let Some(interval) = c.pauses.back_mut() {
                interval.end = at;
            }
        }
    }
    /// Coherent host baseline must be obtained under the same synchronization
    /// as subsequent queue/lifecycle boundaries. Existing unknown work is not
    /// reconstructed from retained logs or diagnostic detail.
    pub fn host_gauge_snapshot(
        &self,
        mut labels: Labels,
        unresolved: u64,
        running: u64,
        ready: u64,
        completion: u64,
    ) -> Result<(), LifecycleError> {
        labels.origin = self.inner.options.origin;
        labels.outcome = Outcome::None;
        labels.debugger_affected = false;
        if !self.core().metrics.labels_valid(labels) {
            return Err(self.error(LifecycleError::InvalidLabels));
        }
        let mut c = self.core();
        if self.inner.options.counters {
            for (f, n) in [
                (F::Unresolved, unresolved),
                (F::Running, running),
                (F::ReadyDepth, ready),
                (F::CompletionDepth, completion),
            ] {
                c.metrics.set_gauge(f, labels, n);
            }
            c.metrics
                .set_gauge_scope_for(labels, GaugeScope::HostSnapshot);
        }
        Ok(())
    }
    pub fn pause_intervals(&self) -> Vec<PauseInterval> {
        self.core().pauses.iter().cloned().collect()
    }
    pub fn metric_catalog(&self) -> Vec<MetricDescriptor> {
        metrics::metric_catalog()
    }
    pub fn metric_snapshot(&self) -> MetricSnapshot {
        match self.try_metric_snapshot(|_| Ok::<(), std::convert::Infallible>(())) {
            Ok(snapshot) => snapshot,
            Err(never) => match never {},
        }
    }
    /// Reject a prospective query/transport response before advancing snapshot
    /// revisions or evicting retained windows. Validation must not reenter this
    /// observer: it runs against a coherent snapshot under its metric lock.
    pub(crate) fn try_metric_snapshot<E>(
        &self,
        validate: impl FnOnce(&MetricSnapshot) -> Result<(), E>,
    ) -> Result<MetricSnapshot, E> {
        // Do not invoke an application-provided clock under the metric lock.
        // If recording changed while reading it, the supplied timestamp cannot
        // identify this population's end boundary. Keep the cumulative values,
        // explicitly omit the interval endpoint, and expose incomplete timing.
        let revision = self.core().metrics.revision();
        let mut at = self.now().map(LocalInstant::snapshot);
        let mut c = self.core();
        if at.is_some() && revision != c.metrics.revision() {
            at = None;
            c.metrics.health_mut().omitted_measurements =
                c.metrics.health().omitted_measurements.saturating_add(1);
            c.metrics.mark_incomplete();
        }
        c.metrics.try_snapshot(at, validate)
    }
    pub fn metric_window(&self, from: u64, to: u64) -> Result<MetricSnapshot, MetricError> {
        self.core().metrics.window(from, to)
    }
    pub fn available_windows(&self) -> Vec<u64> {
        self.core().metrics.available_windows()
    }
    pub fn effect_details(&self, id: EffectId) -> Option<EffectDetails> {
        self.core().details.iter().find(|d| d.id == id).cloned()
    }
    /// Select completed slow queue/execution/delivery (or other histogram)
    /// endpoints without reading any clock, changing metrics, or running work.
    /// Pending ages are intentionally not completed threshold samples. Results
    /// stay bounded by both count and bytes; missing detail is explicit.
    pub fn slow_timings(&self, query: SlowTimingQuery) -> Result<SlowTimingResult, LifecycleError> {
        if query.operation as usize >= self.inner.options.labels.operations.len() {
            return Err(LifecycleError::InvalidLabels);
        }
        if query.family.descriptor().kind != crate::metrics::MetricKind::Histogram {
            return Err(LifecycleError::WrongPhase);
        }
        if query.max_results > 4096
            || query.max_result_bytes > 4 * 1024 * 1024
            || query.max_result_bytes < std::mem::size_of::<SlowTimingResult>()
        {
            return Err(LifecycleError::Limit);
        }
        let c = self.core();
        let availability = if !self.inner.options.timings {
            SlowTimingAvailability::TimingDisabled
        } else if !self.inner.options.details {
            SlowTimingAvailability::DetailsDisabled
        } else {
            SlowTimingAvailability::Available
        };
        let mut result = SlowTimingResult {
            run: self.inner.options.run,
            epoch: self.inner.options.epoch,
            detail_revision: c.event_sequence,
            query,
            availability,
            matches: Vec::new(),
            matched: 0,
            inspected: 0,
            unknown_measurements: 0,
            result_bytes: std::mem::size_of::<SlowTimingResult>(),
            truncated: false,
            complete: availability == SlowTimingAvailability::Available
                && c.health.detail_evictions == 0
                && c.health.detail_rejections == 0
                && c.health.detail_event_drops == 0
                && c.health.destructor_hook_losses == 0,
            retained_detail_only: true,
        };
        if availability != SlowTimingAvailability::Available {
            return Ok(result);
        }
        for detail in &c.details {
            if detail.labels.operation != result.query.operation
                || result
                    .query
                    .measurement_origin
                    .is_some_and(|origin| origin != detail.labels.origin)
            {
                continue;
            }
            result.complete &= detail.complete;
            for timing in &detail.timings {
                if timing.family != result.query.family
                    || (timing.debugger_affected && !result.query.include_debugger_affected)
                    || result
                        .query
                        .outcome
                        .is_some_and(|outcome| outcome != timing.outcome)
                {
                    continue;
                }
                result.inspected = result.inspected.saturating_add(1);
                let Ok(duration) = timing.duration_ns else {
                    result.unknown_measurements = result.unknown_measurements.saturating_add(1);
                    result.complete = false;
                    continue;
                };
                if duration < result.query.threshold_ns {
                    continue;
                }
                result.matched = result.matched.saturating_add(1);
                let bytes = std::mem::size_of::<SlowTimingMatch>();
                if result.matches.len() >= result.query.max_results
                    || bytes
                        > result
                            .query
                            .max_result_bytes
                            .saturating_sub(result.result_bytes)
                {
                    result.truncated = true;
                    result.complete = false;
                    continue;
                }
                let mut labels = detail.labels;
                labels.outcome = timing.outcome;
                labels.debugger_affected = timing.debugger_affected;
                result.matches.reserve_exact(1);
                result.matches.push(SlowTimingMatch {
                    effect: detail.id,
                    origin: detail.origin,
                    labels,
                    timing: timing.clone(),
                });
                result.result_bytes += bytes;
            }
        }
        Ok(result)
    }
    pub fn effect_timeline(&self, id: EffectId) -> Option<EffectTimeline> {
        self.effect_details(id).map(|d| EffectTimeline {
            effect: d.id,
            origin: d.origin,
            measurement_origin: d.labels.origin,
            complete: d.complete,
            events: d.events,
            timings: d.timings,
        })
    }
    pub fn telemetry_health(&self) -> TelemetryHealth {
        let c = self.core();
        let mut h = c.health.clone();
        h.metric = c.metrics.health().clone();
        h.active_effect_handles = self.inner.active_handles.load(Ordering::Relaxed);
        h.retained_detail_records = c.details.len();
        h.retained_detail_bytes = c.detail_bytes;
        h
    }
    pub fn options(&self) -> &EffectOptions {
        &self.inner.options
    }
    /// Safe adapter around a host-owned future. Construction never starts an
    /// attempt. First poll and Ready establish actual elapsed boundaries.
    pub fn instrument_future<Fut, Classify>(
        &self,
        attempt: AttemptHandle,
        future: Fut,
        classify: Classify,
    ) -> ObservedFuture<Fut, Classify>
    where
        Fut: Future,
        Classify: Fn(&Fut::Output) -> RuntimeOutcome,
    {
        ObservedFuture {
            future: Box::pin(future),
            attempt,
            observer: self.clone(),
            classify,
            started: false,
            finished: false,
        }
    }
}
impl Drop for EffectToken {
    fn drop(&mut self) {
        self.observer
            .inner
            .active_handles
            .fetch_sub(1, Ordering::Relaxed);
        let s = self.state.get_mut().unwrap_or_else(|e| e.into_inner());
        if s.admission == Some(Admission::Rejected) || s.resolved.is_some() || s.abandoned {
            return;
        }
        let Ok(mut c) = self.observer.inner.core.try_lock() else {
            self.observer
                .inner
                .drop_losses
                .fetch_add(1, Ordering::Relaxed);
            return;
        };
        s.abandoned = true;
        c.health.abandoned += 1;
        c.metrics.mark_incomplete();
        self.observer.count(&mut c, F::Abandoned, s.labels);
        self.observer.event(
            &mut c,
            s,
            LifecycleEventKind::Abandoned { attempt: None },
            None,
        );
    }
}
impl Drop for AttemptToken {
    fn drop(&mut self) {
        let a = self.state.get_mut().unwrap_or_else(|e| e.into_inner());
        if a.finished || a.abandoned || self.abandonment_reported.swap(true, Ordering::Relaxed) {
            return;
        }
        let observer = &self.effect.observer;
        let lost = || {
            observer.inner.drop_losses.fetch_add(1, Ordering::Relaxed);
        };
        let Ok(mut s) = self.effect.state.try_lock() else {
            lost();
            return;
        };
        let Ok(mut c) = observer.inner.core.try_lock() else {
            lost();
            return;
        };
        a.abandoned = true;
        s.abandoned = true;
        c.health.abandoned += 1;
        c.metrics.mark_incomplete();
        observer.count(&mut c, F::Abandoned, s.labels);
        observer.event(
            &mut c,
            &s,
            LifecycleEventKind::Abandoned {
                attempt: Some(a.id),
            },
            None,
        );
        // A lost token does not prove removal from a host queue or termination.
    }
}
impl Drop for DeliveryToken {
    fn drop(&mut self) {
        let d = self.state.get_mut().unwrap_or_else(|e| e.into_inner());
        if d.failed || d.observed {
            return;
        }
        let observer = &self.effect.observer;
        let lost = || {
            observer.inner.drop_losses.fetch_add(1, Ordering::Relaxed);
        };
        let Ok(mut s) = self.effect.state.try_lock() else {
            lost();
            return;
        };
        let Ok(mut c) = observer.inner.core.try_lock() else {
            lost();
            return;
        };
        s.abandoned = true;
        c.health.abandoned += 1;
        c.metrics.mark_incomplete();
        observer.count(&mut c, F::Abandoned, s.labels);
        observer.event(
            &mut c,
            &s,
            LifecycleEventKind::Abandoned { attempt: None },
            None,
        );
        // Neither successful publication nor consumption is invented here.
    }
}
/// Does not hold a span-enter guard across Pending. Hook errors never replace
/// the future's output and never trigger another execution.
pub struct ObservedFuture<Fut: Future, Classify: Fn(&Fut::Output) -> RuntimeOutcome> {
    future: Pin<Box<Fut>>,
    attempt: AttemptHandle,
    observer: EffectObserver,
    classify: Classify,
    started: bool,
    finished: bool,
}
impl<Fut: Future, Classify: Fn(&Fut::Output) -> RuntimeOutcome + Unpin> Future
    for ObservedFuture<Fut, Classify>
{
    type Output = Fut::Output;
    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
        let this = self.get_mut();
        if !this.started {
            this.started = true;
            let _ = this.observer.attempt_started(&this.attempt);
        }
        match this.future.as_mut().poll(cx) {
            Poll::Pending => Poll::Pending,
            Poll::Ready(value) => {
                if !this.finished {
                    this.finished = true;
                    let outcome = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                        (this.classify)(&value)
                    }))
                    .unwrap_or_else(|_| {
                        let mut c = this.observer.core();
                        c.health.callback_faults += 1;
                        c.metrics.mark_incomplete();
                        RuntimeOutcome::Unclassified
                    });
                    let _ = this.observer.attempt_finished(&this.attempt, outcome);
                }
                Poll::Ready(value)
            }
        }
    }
}
impl<Fut: Future, Classify: Fn(&Fut::Output) -> RuntimeOutcome> Drop
    for ObservedFuture<Fut, Classify>
{
    fn drop(&mut self) {
        if !self.finished && self.observer.same(&self.attempt.effect).is_ok() {
            if self
                .attempt
                .state
                .abandonment_reported
                .swap(true, Ordering::Relaxed)
            {
                return;
            }
            let lost = || {
                self.observer
                    .inner
                    .drop_losses
                    .fetch_add(1, Ordering::Relaxed);
            };
            let Ok(mut a) = self.attempt.state.try_lock() else {
                lost();
                return;
            };
            let Ok(mut s) = self.attempt.effect.state.try_lock() else {
                lost();
                return;
            };
            let Ok(mut c) = self.observer.inner.core.try_lock() else {
                lost();
                return;
            };
            if !a.abandoned && !a.finished {
                a.abandoned = true;
                s.abandoned = true;
                c.health.abandoned += 1;
                c.metrics.mark_incomplete();
                self.observer.count(&mut c, F::Abandoned, s.labels);
                self.observer.event(
                    &mut c,
                    &s,
                    LifecycleEventKind::Abandoned {
                        attempt: Some(a.id),
                    },
                    None,
                );
            }
        }
    }
}

#[cfg(test)]
mod drop_tests {
    use super::*;
    fn setup() -> (EffectObserver, EffectHandle) {
        let o = EffectObserver::new(
            EffectOptions {
                timings: false,
                ..EffectOptions::default()
            },
            Arc::new(MonotonicClock::new(ClockDomain(1))),
        )
        .unwrap();
        let e = o
            .requested(
                RequestOrigin {
                    run: 1,
                    epoch: 1,
                    machine: 1,
                    transition_sequence: 1,
                    output_index: 0,
                },
                Labels::default(),
            )
            .unwrap();
        o.admission(&e, Admission::Accepted).unwrap();
        (o, e)
    }
    #[test]
    fn last_effect_drop_never_blocks_and_marks_contended_accounting_unknown() {
        let (o, e) = setup();
        let guard = o.inner.core.lock().unwrap();
        drop(e);
        assert_eq!(o.inner.active_handles.load(Ordering::Relaxed), 0);
        assert_eq!(o.inner.drop_losses.load(Ordering::Relaxed), 1);
        drop(guard);
        assert_eq!(o.telemetry_health().destructor_hook_losses, 1);
        assert!(!o.metric_snapshot().complete);
    }
    #[test]
    fn future_drop_never_blocks_and_reports_lost_hook() {
        let (o, e) = setup();
        let a = o.attempt_created(&e).unwrap();
        let future =
            o.instrument_future(a, std::future::pending::<()>(), |_| RuntimeOutcome::Success);
        let guard = o.inner.core.lock().unwrap();
        drop(future);
        drop(guard);
        assert_eq!(o.telemetry_health().destructor_hook_losses, 1);
        assert!(!o.metric_snapshot().complete);
    }
    #[test]
    fn rejected_snapshot_queries_preserve_revision_and_acknowledged_history() {
        let (o, _e) = setup();
        let first = o.metric_snapshot();
        let second = o.metric_snapshot();
        let revision = o.core().metrics.revision();
        for _ in 0..32 {
            assert_eq!(
                o.try_metric_snapshot(|_| Err("response too large")),
                Err("response too large")
            );
        }
        assert_eq!(o.core().metrics.revision(), revision);
        assert_eq!(o.available_windows(), vec![first.revision, second.revision]);
        assert!(o.metric_window(first.revision, second.revision).is_ok());
        assert_eq!(o.metric_snapshot().revision, revision + 1);
    }
    #[test]
    fn completed_effect_drop_does_not_report_an_unnecessary_contended_hook() {
        let (o, e) = setup();
        o.resolved(&e, RuntimeOutcome::Success, None).unwrap();
        let guard = o.inner.core.lock().unwrap();
        drop(e);
        assert_eq!(o.inner.active_handles.load(Ordering::Relaxed), 0);
        assert_eq!(o.inner.drop_losses.load(Ordering::Relaxed), 0);
        drop(guard);
        assert!(o.metric_snapshot().complete);
    }
    #[test]
    fn raw_attempt_drop_never_blocks_or_invents_termination() {
        let (o, e) = setup();
        let a = o.attempt_created(&e).unwrap();
        o.attempt_started(&a).unwrap();
        o.resolved(&e, RuntimeOutcome::Cancelled, None).unwrap();
        let guard = o.inner.core.lock().unwrap();
        drop(a);
        assert_eq!(o.inner.drop_losses.load(Ordering::Relaxed), 1);
        drop(guard);
        let snapshot = o.metric_snapshot();
        assert!(!snapshot.complete);
        assert_eq!(
            snapshot.get(F::Running, Labels::default()),
            Some(&crate::metrics::MetricValue::Gauge(1))
        );
    }
    #[test]
    fn raw_delivery_drop_never_blocks_or_invents_consumption() {
        let (o, e) = setup();
        o.resolved(&e, RuntimeOutcome::Success, None).unwrap();
        let d = o.reserve_publication(&e).unwrap();
        let guard = o.inner.core.lock().unwrap();
        drop(d);
        assert_eq!(o.inner.drop_losses.load(Ordering::Relaxed), 1);
        drop(guard);
        let snapshot = o.metric_snapshot();
        assert!(!snapshot.complete);
        assert_eq!(
            snapshot.get(F::CompletionDepth, Labels::default()),
            Some(&crate::metrics::MetricValue::Gauge(1))
        );
    }
    #[test]
    fn last_arc_drop_counts_once_under_concurrent_drops() {
        let (o, e) = setup();
        let e2 = e.clone();
        let a = std::thread::spawn(move || drop(e));
        let b = std::thread::spawn(move || drop(e2));
        a.join().unwrap();
        b.join().unwrap();
        assert_eq!(o.telemetry_health().active_effect_handles, 0);
        assert_eq!(o.telemetry_health().abandoned, 1);
    }
}

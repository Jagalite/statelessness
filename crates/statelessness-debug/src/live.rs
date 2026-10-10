//! Observation-only capture and a separate, opt-in cooperative host controller.
//!
//! Neither type invokes a reducer, checker, effect handler, or clock. The host
//! owns those boundaries. Pausing input dispatch does not pause external work.
//!
//! Attach `LiveBridge` to an application-owned coherent checkpoint and share the
//! sealed check tokens from each real turn. Exact capture and the lossy,
//! metadata-only queue have independent failure paths. Exported traces remain
//! sensitive raw artifacts. Count/byte limits cover retained records, not spare
//! allocator capacity or allocations inside application codecs/checkers.
//!
//! `LiveController` is an explicitly opted-in host gate, not a remote endpoint.
//! Client commands require the current command epoch and execution revision;
//! effect origins keep the stable host epoch across client reconnects. The host
//! must consume delivery and dispatch permits once, account for already admitted
//! work after disconnect, and call `terminate` on its checker/property policy.
//! Reserve dispatch before its external action, then confirm or fail it. A
//! missing confirmation remains uncertain and cannot be retried by this gate.
//!
//! For timing provenance, the host associates its own monotonic boundaries with
//! `PauseAck::generation` and records when `resume`/`authorize_one` ends that
//! interval. Reading status or replaying traces never measures an actual effect.
use stateless::monitor::{Recorder, RecorderOptions};
use stateless::observation::{CheckedInitial, CheckedTurn};
use stateless::trace::{RunConfig, Trace};
use stateless::{Disposition, ModelCodec, ModelError, ModelMetadata};
use std::collections::VecDeque;
use std::marker::PhantomData;
use std::sync::Arc;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LiveError {
    InvalidLimits,
    WrongEpoch,
    StaleRevision { expected: u64, actual: u64 },
    SequenceMismatch,
    Duplicate,
    Gap { expected: u64, actual: u64 },
    Unauthorized,
    WrongPhase,
    Disconnected,
    Overflow,
    Exhausted,
    UnknownEffect,
    IdentityChanged,
    RecordingFailed,
}
impl std::fmt::Display for LiveError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{self:?}")
    }
}
impl std::error::Error for LiveError {}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DeliveryDisposition {
    Accepted,
    Rejected,
    Ignored,
}
impl From<&Disposition> for DeliveryDisposition {
    fn from(value: &Disposition) -> Self {
        match value {
            Disposition::Accepted => Self::Accepted,
            Disposition::Rejected(_) => Self::Rejected,
            Disposition::Ignored(_) => Self::Ignored,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct EffectOrigin {
    pub epoch: u64,
    pub transition: u64,
    pub output_index: usize,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LiveEventKind {
    Checkpoint {
        failed_checks: usize,
        checks_omitted: usize,
    },
    Turn {
        disposition: DeliveryDisposition,
        failed_checks: usize,
        checks_omitted: usize,
        full_checking: bool,
        outputs_requested: usize,
    },
    Gap {
        expected: u64,
        actual: u64,
    },
    RecordingFrozen,
    EffectDispatched {
        origin: EffectOrigin,
    },
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LiveEvent {
    pub epoch: u64,
    pub stream_sequence: u64,
    pub host_sequence: u64,
    pub kind: LiveEventKind,
}
#[derive(Clone, Debug)]
pub struct LiveLimits {
    pub max_events: usize,
    pub max_event_bytes: usize,
    pub max_effect_origins: usize,
}
impl Default for LiveLimits {
    fn default() -> Self {
        Self {
            max_events: 1024,
            max_event_bytes: 256 * 1024,
            max_effect_origins: 4096,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LiveHealth {
    pub epoch: u64,
    pub attached_after: u64,
    pub host_sequence: u64,
    /// No recording error or observed sequence gap. A terminal property failure
    /// may still freeze a complete exact prefix while the host continues.
    pub capture_complete: bool,
    pub recorder_frozen: bool,
    pub recorded_steps: u64,
    pub retained_steps: usize,
    pub evicted_steps: u64,
    pub event_drops: u64,
    pub origin_evictions: u64,
    pub duplicate_observations: u64,
}
/// Exact recording receives sealed checked batches directly, independently of
/// the lossy metadata-only stream. No model values enter this stream.
pub struct LiveBridge<M: ModelCodec> {
    recorder: Recorder,
    metadata: ModelMetadata,
    epoch: u64,
    attached_after: u64,
    host_sequence: u64,
    stream_sequence: u64,
    events: VecDeque<LiveEvent>,
    limits: LiveLimits,
    capture_complete: bool,
    max_check_summary: usize,
    event_drops: u64,
    origins: VecDeque<(EffectOrigin, bool)>,
    origin_evictions: u64,
    duplicates: u64,
    _model: PhantomData<fn() -> M>,
}
impl<M: ModelCodec> LiveBridge<M> {
    /// The host must supply one coherent composite checkpoint at a safe boundary,
    /// including oracle history and queued modeled work. No initial-state or
    /// checking callback is run here. Host sequence is the last completed turn.
    pub fn attach(
        initial: &CheckedInitial<'_, M>,
        host_sequence: u64,
        epoch: u64,
        mut config: RunConfig,
        recorder_options: RecorderOptions,
        limits: LiveLimits,
    ) -> Result<Self, ModelError> {
        if epoch == 0
            || limits.max_events == 0
            || limits.max_event_bytes < std::mem::size_of::<LiveEvent>()
            || limits.max_effect_origins == 0
        {
            return Err(ModelError::new(
                "live attachment requires positive bounded capacities and epoch",
            ));
        }
        if config
            .parameters
            .iter()
            .any(|(key, _)| key.starts_with("stateless.debug."))
        {
            return Err(ModelError::new(
                "stateless.debug. provenance parameters are reserved",
            ));
        }
        for (key, value) in [
            ("max_events", limits.max_events),
            ("max_event_bytes", limits.max_event_bytes),
            ("max_effect_origins", limits.max_effect_origins),
        ] {
            config
                .parameters
                .push((format!("stateless.debug.live.{key}"), value.to_string()));
        }
        config.parameters.push((
            "stateless.debug.origin".into(),
            "recorded-live-observation".into(),
        ));
        config
            .parameters
            .push(("stateless.debug.host_epoch".into(), epoch.to_string()));
        let max_check_summary = recorder_options.limits.max_checks_per_step;
        let recorder =
            Recorder::with_checked_initial_at(initial, host_sequence, config, recorder_options)?;
        let mut bridge = Self {
            metadata: initial.model().metadata(),
            recorder,
            epoch,
            attached_after: host_sequence,
            host_sequence,
            stream_sequence: 0,
            events: VecDeque::new(),
            limits,
            capture_complete: true,
            max_check_summary,
            event_drops: 0,
            origins: VecDeque::new(),
            origin_evictions: 0,
            duplicates: 0,
            _model: PhantomData,
        };
        bridge.publish(LiveEventKind::Checkpoint {
            failed_checks: initial
                .checks()
                .iter()
                .take(max_check_summary)
                .filter(|c| c.is_failure())
                .count(),
            checks_omitted: initial.checks().len().saturating_sub(max_check_summary),
        });
        Ok(bridge)
    }
    /// Consume the actual, already checked host result once. The checked token's
    /// sequence and explicit host sequence must agree. A missed host turn ends
    /// this exact capture, including when the state returned to its old value.
    /// The actual host result remains owned by the caller even on error.
    pub fn observe(
        &mut self,
        host_sequence: u64,
        checked: &CheckedTurn<'_, M>,
    ) -> Result<(), LiveError> {
        if checked.model().metadata() != self.metadata {
            self.stop_recording(ModelError::new("live model identity changed"));
            return Err(LiveError::IdentityChanged);
        }
        if checked.sequence() != host_sequence {
            return Err(LiveError::SequenceMismatch);
        }
        if host_sequence <= self.host_sequence {
            self.duplicates = self.duplicates.saturating_add(1);
            return Err(LiveError::Duplicate);
        }
        let expected = self
            .host_sequence
            .checked_add(1)
            .ok_or(LiveError::Exhausted)?;
        let gap = host_sequence != expected;
        self.host_sequence = host_sequence;
        if gap {
            self.stop_recording(ModelError::new(
                "host sequence gap; exact capture ended before missing delivery",
            ));
            self.publish(LiveEventKind::Gap {
                expected,
                actual: host_sequence,
            });
        }
        let recording_failed = self.capture_complete
            && !self.recorder.is_frozen()
            && self.recorder.observe_checked(checked).is_err();
        if recording_failed {
            self.capture_complete = false;
        }
        if self.recorder.is_frozen() || !self.capture_complete {
            self.publish(LiveEventKind::RecordingFrozen);
        }
        let transition = checked.transition();
        self.publish(LiveEventKind::Turn {
            disposition: (&transition.disposition).into(),
            failed_checks: checked
                .checks()
                .iter()
                .take(self.max_check_summary)
                .filter(|c| c.is_failure())
                .count(),
            checks_omitted: checked
                .checks()
                .len()
                .saturating_sub(self.max_check_summary),
            full_checking: checked.full_checking(),
            outputs_requested: transition.outputs.len(),
        });
        // The origin registry is diagnostic only; eviction never implies that a
        // requested effect completed or that a runtime effect was dispatched.
        // Keep only the retained tail. Work is O(capacity), even for a huge
        // application-owned zero-sized output vector rejected by the recorder.
        let count = transition.outputs.len();
        let retained = count.min(self.limits.max_effect_origins);
        let skipped = count - retained;
        let evict_old = self
            .origins
            .len()
            .saturating_sub(self.limits.max_effect_origins - retained);
        self.origin_evictions = self
            .origin_evictions
            .saturating_add(u64::try_from(skipped.saturating_add(evict_old)).unwrap_or(u64::MAX));
        for _ in 0..evict_old {
            self.origins.pop_front();
        }
        for index in skipped..count {
            self.origins.push_back((
                EffectOrigin {
                    epoch: self.epoch,
                    transition: host_sequence,
                    output_index: index,
                },
                false,
            ));
        }
        if gap {
            Err(LiveError::Gap {
                expected,
                actual: host_sequence,
            })
        } else if recording_failed {
            Err(LiveError::RecordingFailed)
        } else {
            Ok(())
        }
    }
    /// An explicit host fact. Merely observing outputs never calls this hook.
    /// Unknown or evicted origins and duplicate dispatch facts are rejected.
    pub fn effect_dispatched(&mut self, origin: EffectOrigin) -> Result<(), LiveError> {
        if origin.epoch != self.epoch {
            return Err(LiveError::WrongEpoch);
        }
        let item = self
            .origins
            .iter_mut()
            .find(|(known, _)| *known == origin)
            .ok_or(LiveError::UnknownEffect)?;
        if item.1 {
            return Err(LiveError::Duplicate);
        }
        item.1 = true;
        self.publish(LiveEventKind::EffectDispatched { origin });
        Ok(())
    }
    fn publish(&mut self, kind: LiveEventKind) {
        let Some(sequence) = self.stream_sequence.checked_add(1) else {
            self.event_drops = self.event_drops.saturating_add(1);
            return;
        };
        self.stream_sequence = sequence;
        if self.events.len() >= self.limits.max_events
            || self
                .events
                .len()
                .saturating_add(1)
                .saturating_mul(std::mem::size_of::<LiveEvent>())
                > self.limits.max_event_bytes
        {
            self.event_drops = self.event_drops.saturating_add(1);
            return;
        }
        self.events.push_back(LiveEvent {
            epoch: self.epoch,
            stream_sequence: sequence,
            host_sequence: self.host_sequence,
            kind,
        });
    }
    pub fn pop_event(&mut self) -> Option<LiveEvent> {
        self.events.pop_front()
    }
    pub fn health(&self) -> LiveHealth {
        LiveHealth {
            epoch: self.epoch,
            attached_after: self.attached_after,
            host_sequence: self.host_sequence,
            capture_complete: self.capture_complete,
            recorder_frozen: self.recorder.is_frozen() || !self.capture_complete,
            recorded_steps: self.recorder.observed_steps(),
            retained_steps: self.recorder.retained_steps(),
            evicted_steps: self.recorder.evicted_steps(),
            event_drops: self.event_drops,
            origin_evictions: self.origin_evictions,
            duplicate_observations: self.duplicates,
        }
    }
    /// End exact capture after a host checker/oracle failure for which no sealed
    /// turn exists. The actual result remains host-owned. No error may overwrite
    /// an already frozen property failure or an earlier recording error.
    pub fn stop_recording(&mut self, error: ModelError) {
        self.capture_complete = false;
        self.recorder.stop_with_error(error);
    }
    pub fn limits(&self) -> &LiveLimits {
        &self.limits
    }
    /// Export the recorder's coherent prefix with its bounded terminal footer.
    /// This is a raw, potentially sensitive replay artifact, not a redacted view.
    pub fn export(&self) -> Trace {
        self.recorder.snapshot()
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ControllerPhase {
    Running,
    PauseRequested,
    Paused,
    OneTurnAuthorized,
    Delivering,
    Terminated,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EffectPhase {
    Dispatched,
    Staged,
    Dispatching,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum DispatchState {
    Staged,
    InFlight,
    Dispatched,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DetachPolicy {
    RemainPaused,
    Resume,
    Terminate,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OverflowPolicy {
    Backpressure,
    Disconnect,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ControlAuthority {
    pub pause_and_step: bool,
    pub inject: bool,
}
#[derive(Clone, Debug)]
pub struct ControllerOptions {
    pub authority: ControlAuthority,
    pub detach: DetachPolicy,
    pub overflow: OverflowPolicy,
    pub max_arrivals: usize,
    pub max_arrival_bytes: usize,
    pub max_outputs: usize,
}
impl Default for ControllerOptions {
    fn default() -> Self {
        Self {
            authority: ControlAuthority {
                pause_and_step: false,
                inject: false,
            },
            detach: DetachPolicy::RemainPaused,
            overflow: OverflowPolicy::Backpressure,
            max_arrivals: 1024,
            max_arrival_bytes: 1024 * 1024,
            max_outputs: 4096,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PauseAck {
    /// Correlation identity for this acknowledged pause. The host can associate
    /// its own monotonic timestamps with this boundary; the gate reads no clock.
    pub generation: u64,
    pub epoch: u64,
    pub revision: u64,
    pub effect_phase: EffectPhase,
}
#[must_use = "report the actual delivery or terminate the host boundary"]
#[derive(Debug)]
pub struct DeliveryPermit {
    owner: Arc<()>,
    epoch: u64,
    revision: u64,
    serial: u64,
}
#[must_use = "confirm dispatch or report its uncertain failure; never retry a dropped permit"]
#[derive(Debug)]
pub struct DispatchPermit {
    owner: Arc<()>,
    epoch: u64,
    revision: u64,
    output_index: usize,
}
impl DispatchPermit {
    /// Stable host origin, independent of client reconnection epochs.
    pub fn origin(&self) -> EffectOrigin {
        EffectOrigin {
            epoch: self.epoch,
            transition: self.revision,
            output_index: self.output_index,
        }
    }
    pub fn revision(&self) -> u64 {
        self.revision
    }
    pub fn output_index(&self) -> usize {
        self.output_index
    }
    pub fn epoch(&self) -> u64 {
        self.epoch
    }
}
/// Arrival rejection returns ownership to the host. Backpressure requires the
/// host to retry later; no completion is silently dropped or counted delivered.
#[derive(Debug)]
pub struct ArrivalRejected<I> {
    pub error: LiveError,
    pub input: I,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ControllerStatus {
    /// Current client-command epoch; reconnection increments this value.
    pub epoch: u64,
    /// Host effect-origin epoch. It stays fixed for this controller's lifetime.
    pub origin_epoch: u64,
    pub revision: u64,
    pub phase: ControllerPhase,
    pub connected: bool,
    pub effect_phase: EffectPhase,
    pub buffered_arrivals: usize,
    pub buffered_bytes: usize,
    pub overflow_count: u64,
    pub pause_generation: u64,
}
/// Cooperative host gate. Exclusive mutable access serializes command admission
/// and host safe points. All real execution remains outside this type.
///
/// The host consumes each returned permit once and reports the resulting turn.
/// A crashed/failed in-flight delivery is never retried by reconnect. Arbitrary
/// live restore is intentionally absent. Counted arrival bytes are the host's
/// declared owned payload estimate, not a bound on arbitrary input internals.
pub struct LiveController<I> {
    options: ControllerOptions,
    owner: Arc<()>,
    epoch: u64,
    revision: u64,
    phase: ControllerPhase,
    connected: bool,
    effects: EffectPhase,
    staged: Vec<DispatchState>,
    arrivals: VecDeque<(I, usize)>,
    arrival_bytes: usize,
    overflow_count: u64,
    serial: u64,
    active_serial: Option<u64>,
    repause: bool,
    pause_generation: u64,
    terminate_pending: bool,
    output_epoch: u64,
    dispatch_remaining: usize,
    dispatch_inflight: usize,
}
impl<I> LiveController<I> {
    pub fn new(epoch: u64, revision: u64, options: ControllerOptions) -> Result<Self, LiveError> {
        if epoch == 0
            || options.max_arrivals == 0
            || options.max_arrival_bytes == 0
            || options.max_outputs == 0
        {
            return Err(LiveError::InvalidLimits);
        }
        Ok(Self {
            options,
            owner: Arc::new(()),
            epoch,
            revision,
            phase: ControllerPhase::Running,
            connected: true,
            effects: EffectPhase::Dispatched,
            staged: Vec::new(),
            arrivals: VecDeque::new(),
            arrival_bytes: 0,
            overflow_count: 0,
            serial: 0,
            active_serial: None,
            repause: false,
            pause_generation: 0,
            terminate_pending: false,
            output_epoch: epoch,
            dispatch_remaining: 0,
            dispatch_inflight: 0,
        })
    }
    /// Host-chosen runtime opt-in and cooperative resource limits. Reconnecting
    /// never mutates these capabilities or policies.
    pub fn options(&self) -> &ControllerOptions {
        &self.options
    }
    /// Stop admission after a checker/property/host failure. If a delivery is
    /// already executing, retain its permit and report its actual result before
    /// terminal shutdown. No state restoration or effect retry is attempted.
    pub fn terminate(&mut self) {
        self.terminate_pending = true;
        if self.phase != ControllerPhase::Delivering {
            self.phase = ControllerPhase::Terminated;
        }
    }
    pub fn status(&self) -> ControllerStatus {
        ControllerStatus {
            epoch: self.epoch,
            origin_epoch: self.output_epoch,
            revision: self.revision,
            phase: self.phase,
            connected: self.connected,
            effect_phase: self.effects,
            buffered_arrivals: self.arrivals.len(),
            buffered_bytes: self.arrival_bytes,
            overflow_count: self.overflow_count,
            pause_generation: self.pause_generation,
        }
    }
    fn command(&self, epoch: u64, revision: u64) -> Result<(), LiveError> {
        if !self.connected {
            return Err(LiveError::Disconnected);
        }
        if epoch != self.epoch {
            return Err(LiveError::WrongEpoch);
        }
        if revision != self.revision {
            return Err(LiveError::StaleRevision {
                expected: revision,
                actual: self.revision,
            });
        }
        if !self.options.authority.pause_and_step {
            return Err(LiveError::Unauthorized);
        }
        Ok(())
    }
    pub fn request_pause(&mut self, epoch: u64, revision: u64) -> Result<(), LiveError> {
        self.command(epoch, revision)?;
        match self.phase {
            ControllerPhase::Running => self.phase = ControllerPhase::PauseRequested,
            ControllerPhase::Delivering => self.repause = true,
            ControllerPhase::PauseRequested | ControllerPhase::Paused => {}
            ControllerPhase::OneTurnAuthorized => self.phase = ControllerPhase::PauseRequested,
            ControllerPhase::Terminated => return Err(LiveError::WrongPhase),
        }
        Ok(())
    }
    /// Host acknowledgement only at a complete turn boundary. The effect phase
    /// is derived from staged bookkeeping, never supplied by an untrusted client.
    pub fn safe_point(&mut self) -> Result<Option<PauseAck>, LiveError> {
        if self.dispatch_inflight != 0 {
            return Err(LiveError::WrongPhase);
        }
        match self.phase {
            ControllerPhase::PauseRequested => {
                self.pause_generation = self
                    .pause_generation
                    .checked_add(1)
                    .ok_or(LiveError::Exhausted)?;
                self.phase = ControllerPhase::Paused;
            }
            ControllerPhase::Paused => {}
            ControllerPhase::Running | ControllerPhase::OneTurnAuthorized => return Ok(None),
            _ => return Err(LiveError::WrongPhase),
        }
        Ok(Some(PauseAck {
            generation: self.pause_generation,
            epoch: self.epoch,
            revision: self.revision,
            effect_phase: self.effects,
        }))
    }
    pub fn authorize_one(&mut self, epoch: u64, revision: u64) -> Result<(), LiveError> {
        self.command(epoch, revision)?;
        if self.phase != ControllerPhase::Paused {
            return Err(LiveError::WrongPhase);
        }
        self.phase = ControllerPhase::OneTurnAuthorized;
        Ok(())
    }
    pub fn resume(&mut self, epoch: u64, revision: u64) -> Result<(), LiveError> {
        self.command(epoch, revision)?;
        if !matches!(
            self.phase,
            ControllerPhase::Paused
                | ControllerPhase::PauseRequested
                | ControllerPhase::OneTurnAuthorized
        ) {
            return Err(LiveError::WrongPhase);
        }
        self.phase = ControllerPhase::Running;
        Ok(())
    }
    pub fn external_arrival(
        &mut self,
        input: I,
        owned_bytes: usize,
    ) -> Result<(), ArrivalRejected<I>> {
        if self.phase == ControllerPhase::Terminated {
            return Err(ArrivalRejected {
                error: LiveError::WrongPhase,
                input,
            });
        }
        if self.arrivals.len() >= self.options.max_arrivals
            || owned_bytes
                > self
                    .options
                    .max_arrival_bytes
                    .saturating_sub(self.arrival_bytes)
        {
            self.overflow_count = self.overflow_count.saturating_add(1);
            if self.options.overflow == OverflowPolicy::Disconnect {
                self.disconnect();
            }
            return Err(ArrivalRejected {
                error: LiveError::Overflow,
                input,
            });
        }
        self.arrival_bytes += owned_bytes;
        self.arrivals.push_back((input, owned_bytes));
        Ok(())
    }
    pub fn inject(
        &mut self,
        epoch: u64,
        revision: u64,
        input: I,
        owned_bytes: usize,
    ) -> Result<(), ArrivalRejected<I>> {
        // Injection permission is separate from pause authority; observe-only
        // runtime capture can never grant it through reconnection.
        let validation = if !self.connected {
            Err(LiveError::Disconnected)
        } else if epoch != self.epoch {
            Err(LiveError::WrongEpoch)
        } else if revision != self.revision {
            Err(LiveError::StaleRevision {
                expected: revision,
                actual: self.revision,
            })
        } else if !self.options.authority.inject {
            Err(LiveError::Unauthorized)
        } else {
            Ok(())
        };
        if let Err(error) = validation {
            return Err(ArrivalRejected { error, input });
        }
        self.external_arrival(input, owned_bytes)
    }
    /// Atomically removes one queued input and grants exactly one delivery.
    /// Staged outputs must be dispatched before the next turn can start.
    pub fn begin_next(&mut self) -> Result<Option<(DeliveryPermit, I)>, LiveError> {
        if !matches!(
            self.phase,
            ControllerPhase::Running | ControllerPhase::OneTurnAuthorized
        ) || self.effects != EffectPhase::Dispatched
        {
            return Err(LiveError::WrongPhase);
        }
        if self.arrivals.is_empty() {
            return Ok(None);
        }
        let serial = self.serial.checked_add(1).ok_or(LiveError::Exhausted)?;
        if self.revision == u64::MAX {
            return Err(LiveError::Exhausted);
        }
        let (input, bytes) = self.arrivals.pop_front().expect("nonempty checked");
        self.arrival_bytes -= bytes;
        self.repause = self.phase == ControllerPhase::OneTurnAuthorized;
        self.phase = ControllerPhase::Delivering;
        self.serial = serial;
        self.active_serial = Some(serial);
        Ok(Some((
            DeliveryPermit {
                owner: self.owner.clone(),
                epoch: self.epoch,
                revision: self.revision,
                serial,
            },
            input,
        )))
    }
    /// Report a completed actual transition. Rejected/ignored deliveries count.
    /// Too many outputs terminate control honestly; the host keeps its result.
    pub fn delivered(&mut self, permit: DeliveryPermit, outputs: usize) -> Result<(), LiveError> {
        if !Arc::ptr_eq(&permit.owner, &self.owner)
            || self.phase != ControllerPhase::Delivering
            || permit.serial != self.active_serial.unwrap_or(0)
            || permit.revision != self.revision
            || permit.epoch != self.epoch
        {
            return Err(LiveError::WrongPhase);
        }
        self.active_serial = None;
        self.revision += 1;
        self.staged.clear();
        self.dispatch_remaining = outputs;
        self.dispatch_inflight = 0;
        self.effects = if outputs == 0 {
            EffectPhase::Dispatched
        } else {
            EffectPhase::Staged
        };
        if outputs > self.options.max_outputs || self.staged.try_reserve_exact(outputs).is_err() {
            self.phase = ControllerPhase::Terminated;
            return Err(LiveError::Overflow);
        }
        self.staged.resize(outputs, DispatchState::Staged);
        self.phase = if self.terminate_pending {
            ControllerPhase::Terminated
        } else if self.repause {
            ControllerPhase::PauseRequested
        } else {
            ControllerPhase::Running
        };
        self.repause = false;
        Ok(())
    }
    /// Terminate an attempt that produced no successful application transition.
    /// If a reducer returned an actual transition and checking later failed,
    /// call delivered(permit, outputs) then terminate() to preserve its revision.
    /// Neither path retries the reducer or manufactures a completed transition.
    pub fn delivery_failed(&mut self, permit: DeliveryPermit) -> Result<(), LiveError> {
        if !Arc::ptr_eq(&permit.owner, &self.owner)
            || self.phase != ControllerPhase::Delivering
            || Some(permit.serial) != self.active_serial
            || permit.epoch != self.epoch
            || permit.revision != self.revision
        {
            return Err(LiveError::WrongPhase);
        }
        self.active_serial = None;
        self.phase = ControllerPhase::Terminated;
        Ok(())
    }
    /// Reserve dispatch before making an output externally visible. Reservation
    /// is not evidence of dispatch: report `dispatched` after the host action.
    /// Dropping this non-Clone permit leaves uncertain work in flight, blocks
    /// safe-point acknowledgement and prevents another turn; it is never retried.
    pub fn take_dispatch(&mut self, output_index: usize) -> Result<DispatchPermit, LiveError> {
        if !matches!(
            self.phase,
            ControllerPhase::Running
                | ControllerPhase::PauseRequested
                | ControllerPhase::OneTurnAuthorized
        ) {
            return Err(LiveError::WrongPhase);
        }
        let state = self
            .staged
            .get_mut(output_index)
            .ok_or(LiveError::UnknownEffect)?;
        if *state != DispatchState::Staged {
            return Err(LiveError::Duplicate);
        }
        *state = DispatchState::InFlight;
        self.dispatch_inflight += 1;
        self.effects = EffectPhase::Dispatching;
        Ok(DispatchPermit {
            owner: self.owner.clone(),
            epoch: self.output_epoch,
            revision: self.revision,
            output_index,
        })
    }
    fn validate_dispatch(&self, permit: &DispatchPermit) -> Result<(), LiveError> {
        if !Arc::ptr_eq(&permit.owner, &self.owner)
            || permit.epoch != self.output_epoch
            || permit.revision != self.revision
            || self.staged.get(permit.output_index) != Some(&DispatchState::InFlight)
        {
            return Err(LiveError::WrongPhase);
        }
        Ok(())
    }
    /// The host confirms that its dispatch action happened, independently of
    /// whether that asynchronous effect later succeeds or posts a completion.
    pub fn dispatched(&mut self, permit: DispatchPermit) -> Result<(), LiveError> {
        self.validate_dispatch(&permit)?;
        self.staged[permit.output_index] = DispatchState::Dispatched;
        self.dispatch_inflight -= 1;
        self.dispatch_remaining -= 1;
        self.effects = if self.dispatch_remaining == 0 {
            EffectPhase::Dispatched
        } else if self.dispatch_inflight != 0 {
            EffectPhase::Dispatching
        } else {
            EffectPhase::Staged
        };
        Ok(())
    }
    /// An uncertain/failed host dispatch terminates control. The reservation is
    /// not released, so neither resume nor reconnection can duplicate the work.
    pub fn dispatch_failed(&mut self, permit: DispatchPermit) -> Result<(), LiveError> {
        self.validate_dispatch(&permit)?;
        self.phase = ControllerPhase::Terminated;
        Ok(())
    }
    pub fn disconnect(&mut self) {
        if !self.connected {
            return;
        }
        self.connected = false;
        match self.options.detach {
            DetachPolicy::Terminate => {
                // A turn already admitted is real execution, even if the client
                // disappears. Account for that result before terminal shutdown.
                self.terminate();
            }
            DetachPolicy::RemainPaused => {
                if self.phase == ControllerPhase::Delivering {
                    self.repause = true;
                } else if !matches!(
                    self.phase,
                    ControllerPhase::Terminated | ControllerPhase::Paused
                ) {
                    self.phase = ControllerPhase::PauseRequested;
                }
            }
            DetachPolicy::Resume => {
                if self.phase == ControllerPhase::Delivering {
                    self.repause = false;
                } else if self.phase != ControllerPhase::Terminated {
                    self.phase = ControllerPhase::Running;
                }
            }
        }
    }
    /// Reattach the same authority. In-flight delivery must finish first; its
    /// permit is never replaced. A fresh command epoch rejects old client
    /// commands; effect origins retain the original host epoch so an attached
    /// observation bridge can continue correlating dispatches without reattach.
    pub fn reconnect(&mut self) -> Result<u64, LiveError> {
        if self.connected
            || self.dispatch_inflight != 0
            || self.phase == ControllerPhase::Delivering
            || self.phase == ControllerPhase::Terminated
        {
            return Err(LiveError::WrongPhase);
        }
        self.epoch = self.epoch.checked_add(1).ok_or(LiveError::Exhausted)?;
        self.connected = true;
        Ok(self.epoch)
    }
}

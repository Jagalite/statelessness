//! Bounded version-one local stdio protocol for an explicitly linked model.
//!
//! Four-byte big-endian lengths frame escaped tab-separated typed fields. This
//! transport does not start a listener, load models, run effects, or grant host
//! authority. Session operations are serialized by an exclusive mutable borrow.
use crate::diagnostic::{DiagnosticError, DiagnosticEvent, DiagnosticHub, SelectedSubscription};
use crate::effects::{
    EffectDetails, EffectId, EffectObserver, LifecycleEventKind, RequestOrigin, TelemetryHealth,
};
use crate::inspect::{
    self, Inspect, InspectError, InspectLimits, InspectNode, InspectQuery, InspectResult,
    IntegerType, MapKey, NodeKind, PageRequest, PathSegment, Scalar, SnapshotId,
};
use crate::metrics::{MetricSnapshot, MetricValue};
use crate::session::{DebugError, DebugSession};
use crate::watches::{
    ObservationContext, OriginKind, WatchAck, WatchConfig, WatchError, WatchRegistry,
};
use stateless::observation::DebugObservation;
use stateless::{CheckStatus, EncodeBuffer, Enumerate, ModelCodec};
use std::cell::RefCell;
use std::collections::VecDeque;
use std::io::{self, Read, Write};
use std::rc::Rc;
use std::sync::atomic::{AtomicU64, Ordering};
static NEXT_CONNECTION_EPOCH: AtomicU64 = AtomicU64::new(1);
fn fresh_epoch() -> Result<u64, ProtocolError> {
    NEXT_CONNECTION_EPOCH
        .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |current| {
            current.checked_add(1)
        })
        .map_err(|_| ProtocolError::Exhausted)
}

pub const PROTOCOL_VERSION: u32 = 1;
#[derive(Clone, Debug)]
pub struct ProtocolLimits {
    pub max_frame_bytes: usize,
    pub max_request_fields: usize,
    pub max_response_fields: usize,
    pub max_field_bytes: usize,
    pub max_input_bytes: usize,
    pub max_cache_entries: usize,
    pub max_cache_bytes: usize,
    pub max_handles: usize,
    pub max_candidates: usize,
    pub max_candidate_bytes: usize,
    pub max_event_entries: usize,
    pub max_event_bytes: usize,
    pub max_page_size: usize,
    pub max_telemetry_snapshot_bytes: usize,
    pub max_probe_profiles: usize,
    pub max_probe_profile_bytes: usize,
}
impl Default for ProtocolLimits {
    fn default() -> Self {
        Self {
            max_frame_bytes: 64 * 1024,
            max_request_fields: 64,
            max_response_fields: 4096,
            max_field_bytes: 8192,
            max_input_bytes: 8192,
            max_cache_entries: 64,
            max_cache_bytes: 4 * 1024 * 1024,
            max_handles: 64,
            max_candidates: 256,
            max_candidate_bytes: 256 * 1024,
            max_event_entries: 1024,
            max_event_bytes: 256 * 1024,
            max_page_size: 100,
            max_telemetry_snapshot_bytes: 8 * 1024 * 1024,
            max_probe_profiles: 64,
            max_probe_profile_bytes: 256 * 1024,
        }
    }
}
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ProtocolAuthority {
    /// Select or decode a new modeled input in this simulation, never real effects.
    pub deliver_inputs: bool,
    /// Runtime watch configuration is independent from modeled input authority.
    pub configure_watches: bool,
    /// Select host-approved probe profiles, independently of state watches.
    pub configure_diagnostics: bool,
    /// Explicit opt-in to sensitive exact input/output payloads in wire queries.
    pub read_exact_values: bool,
    /// Raw replay artifacts have independent sharing authority; redacted views
    /// and read_exact_values do not automatically grant full trace export.
    pub export_exact_trace: bool,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ProtocolError {
    Malformed,
    UnsupportedVersion,
    WrongSession,
    WrongEpoch,
    Unauthorized,
    StaleRevision,
    StaleConfiguration,
    StaleHandle,
    DuplicateConflict,
    RetiredRequest,
    Limit,
    Unsupported,
    InvalidInput,
    Ended,
    Disconnected,
    Callback,
    Exhausted,
    Busy,
}
impl ProtocolError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::Malformed => "malformed",
            Self::UnsupportedVersion => "unsupported_version",
            Self::WrongSession => "wrong_session",
            Self::WrongEpoch => "wrong_epoch",
            Self::Unauthorized => "unauthorized",
            Self::StaleRevision => "stale_revision",
            Self::StaleConfiguration => "stale_configuration",
            Self::StaleHandle => "stale_handle",
            Self::DuplicateConflict => "duplicate_conflict",
            Self::RetiredRequest => "retired_request",
            Self::Limit => "limit",
            Self::Unsupported => "unsupported",
            Self::InvalidInput => "invalid_input",
            Self::Ended => "ended",
            Self::Disconnected => "disconnected",
            Self::Callback => "callback",
            Self::Exhausted => "exhausted",
            Self::Busy => "busy",
        }
    }
}
impl From<DebugError> for ProtocolError {
    fn from(error: DebugError) -> Self {
        match error {
            DebugError::StaleRevision { .. } => Self::StaleRevision,
            DebugError::Unsupported(_) => Self::Unsupported,
            DebugError::Ended => Self::Ended,
            DebugError::InputNotPermitted | DebugError::InvalidCommand(_) => Self::InvalidInput,
            DebugError::Callback(_) => Self::Callback,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Command {
    Status,
    Snapshot,
    ExportTrace {
        handle: u64,
        offset: u64,
        limit: usize,
    },
    Checks {
        handle: u64,
    },
    Outputs {
        handle: u64,
    },
    Inspect {
        handle: u64,
        schema: u32,
        offset: usize,
        limit: usize,
        path: Vec<PathSegment>,
    },
    Inputs {
        offset: usize,
        limit: usize,
    },
    Select {
        token: u64,
    },
    Step {
        encoded_input: Vec<u8>,
    },
    Events {
        limit: usize,
    },
    Cancel,
    Watch {
        schema: u32,
        baseline_revision: u64,
        path: Vec<PathSegment>,
    },
    Unwatch {
        watch_id: u64,
    },
    WatchStatus,
    WatchCurrent {
        watch_id: u64,
    },
    MetricCatalog {
        offset: usize,
        limit: usize,
    },
    MetricSnapshot {
        handle: Option<u64>,
        offset: usize,
        limit: usize,
    },
    MetricWindow {
        from: u64,
        to: u64,
        limit: usize,
    },
    EffectDetails {
        id: EffectId,
    },
    EffectTimeline {
        id: EffectId,
        limit: usize,
    },
    TelemetryHealth,
    ProbeStatus {
        offset: usize,
        limit: usize,
    },
    ProbeConfigure {
        expected_capture_revision: u64,
        profiles: Vec<u64>,
    },
    ProbeEvents {
        sink: u64,
        limit: usize,
        include_payload: bool,
    },
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Request {
    pub version: u32,
    pub session: String,
    pub epoch: u64,
    pub request_id: u64,
    pub expected_revision: u64,
    pub expected_configuration: u64,
    pub command: Command,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Response {
    pub version: u32,
    pub session: String,
    pub epoch: u64,
    pub request_id: u64,
    pub revision: u64,
    pub configuration: u64,
    pub result: Result<(), ProtocolError>,
    /// Keys are protocol-defined; values are explicitly tagged (u64:, bool:,
    /// string:, bytes:, enum:, etc.). No integer is transported through a float.
    pub fields: Vec<(String, String)>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProtocolEvent {
    pub version: u32,
    pub session: String,
    pub epoch: u64,
    pub stream_sequence: u64,
    pub revision: u64,
    pub configuration: u64,
    pub kind: String,
}
/// One explicitly host-approved probe selection. Clients may choose IDs only;
/// they cannot supply code, sink definitions, selectors, or projection paths.
#[derive(Clone, Debug)]
pub struct ProbeProfile {
    pub id: u64,
    pub label: String,
    pub selected: SelectedSubscription,
}
struct ProbeAttachment {
    hub: Rc<RefCell<DiagnosticHub>>,
    profiles: Vec<ProbeProfile>,
    exposed_sinks: Vec<u64>,
    selected: Option<(u64, Vec<u64>)>,
}
struct CacheEntry {
    request: Request,
    response: Response,
    bytes: usize,
}
struct Candidate {
    id: u64,
    revision: u64,
    encoded: Vec<u8>,
}
#[derive(Clone, Copy)]
struct Handle {
    id: u64,
    snapshot: SnapshotId,
}
type WatchObserveFn<M> =
    fn(&mut WatchRegistry, &<M as stateless::Model>::State, ObservationContext<'_>);
type WatchAddFn<M> = fn(
    &mut WatchRegistry,
    &<M as stateless::Model>::State,
    WatchConfig,
) -> Result<WatchAck, WatchError>;
type InspectFn<M> =
    fn(&<M as stateless::Model>::State, &InspectQuery) -> Result<InspectResult, InspectError>;
/// Queries use acknowledged immutable revisions. Old handles return stale rather
/// than silently mixing fields from different states. The current implementation
/// does not retain arbitrary application clones or promise snapshot independence.
pub struct ProtocolSession<M: ModelCodec + Enumerate> {
    session: DebugSession<M>,
    session_key: u64,
    epoch: u64,
    connected: bool,
    authority: ProtocolAuthority,
    limits: ProtocolLimits,
    configuration: u64,
    high_water: u64,
    cache: VecDeque<CacheEntry>,
    cache_bytes: usize,
    next_handle: u64,
    handles: VecDeque<Handle>,
    next_candidate: u64,
    candidates: VecDeque<Candidate>,
    candidate_bytes: usize,
    events: VecDeque<ProtocolEvent>,
    event_bytes: usize,
    event_sequence: u64,
    event_drops: u64,
    inspector: Option<InspectFn<M>>,
    probes: Option<ProbeAttachment>,
    telemetry: Option<EffectObserver>,
    metric_capture: Option<(u64, MetricSnapshot)>,
    watches: Option<WatchRegistry>,
    watch_observer: Option<WatchObserveFn<M>>,
    watch_add: Option<WatchAddFn<M>>,
}
impl<M: ModelCodec + Enumerate> ProtocolSession<M> {
    pub fn new(
        session: DebugSession<M>,
        session_key: u64,
        authority: ProtocolAuthority,
        limits: ProtocolLimits,
    ) -> Result<Self, ProtocolError> {
        if session_key == 0
            || session.id().len() > 128
            || session.id().len().saturating_add(7) > limits.max_field_bytes
            || session.input_policy().len().saturating_add(7) > limits.max_field_bytes
            || limits.max_frame_bytes < 2048
            || limits.max_frame_bytes > u32::MAX as usize
            || limits.max_request_fields < 12
            || limits.max_response_fields < 32
            || limits.max_field_bytes < 64
            || limits.max_input_bytes == 0
            || limits.max_cache_entries == 0
            || limits.max_cache_bytes < limits.max_frame_bytes.saturating_mul(2)
            || limits.max_handles == 0
            || limits.max_candidates == 0
            || limits.max_candidate_bytes < limits.max_input_bytes
            || limits.max_event_entries == 0
            || limits.max_event_bytes < 512
            || limits.max_page_size == 0
            || limits.max_telemetry_snapshot_bytes == 0
            || limits.max_probe_profiles == 0
            || limits.max_probe_profile_bytes == 0
        {
            return Err(ProtocolError::Limit);
        }
        Ok(Self {
            session,
            session_key,
            epoch: fresh_epoch()?,
            connected: true,
            authority,
            limits,
            configuration: 0,
            high_water: 0,
            cache: VecDeque::new(),
            cache_bytes: 0,
            next_handle: 0,
            handles: VecDeque::new(),
            next_candidate: 0,
            candidates: VecDeque::new(),
            candidate_bytes: 0,
            events: VecDeque::new(),
            event_bytes: 0,
            event_sequence: 0,
            event_drops: 0,
            inspector: None,
            probes: None,
            telemetry: None,
            metric_capture: None,
            watches: None,
            watch_observer: None,
            watch_add: None,
        })
    }
    /// The host explicitly supplies an independent lifecycle/metrics observer.
    /// This grants bounded diagnostic reads, never lifecycle mutation, delivery,
    /// replay ingestion, or effects. Oversized host query configurations reject.
    pub fn attach_telemetry(&mut self, observer: EffectObserver) -> Result<(), ProtocolError> {
        if observer.options().metric_limits.max_bytes > self.limits.max_telemetry_snapshot_bytes
            || observer.options().max_detail_bytes > self.limits.max_telemetry_snapshot_bytes
        {
            return Err(ProtocolError::Limit);
        }
        self.telemetry = Some(observer);
        self.metric_capture = None;
        Ok(())
    }
    /// Grant access to one dedicated capture configuration domain. Configuration
    /// replaces that hub's selected subscriptions; only host-approved profiles
    /// are selectable. Exposed sink queues are explicit, independently authorized
    /// read destinations. Runtime producer acknowledgements remain host-owned.
    pub fn attach_probes(
        &mut self,
        hub: Rc<RefCell<DiagnosticHub>>,
        profiles: Vec<ProbeProfile>,
        exposed_sinks: Vec<u64>,
    ) -> Result<(), ProtocolError> {
        if self.probes.is_some()
            || profiles.len() > self.limits.max_probe_profiles
            || exposed_sinks.len() > self.limits.max_probe_profiles
        {
            return Err(ProtocolError::Limit);
        }
        let mut bytes = std::mem::size_of::<ProbeAttachment>();
        for (index, profile) in profiles.iter().enumerate() {
            if profile.id == 0
                || profile.label.len() > 128
                || profile.label.len().saturating_add(7) > self.limits.max_field_bytes
                || profiles[..index].iter().any(|p| p.id == profile.id)
            {
                return Err(ProtocolError::InvalidInput);
            }
            bytes = bytes.saturating_add(profile_bytes(profile));
        }
        bytes = bytes.saturating_add(exposed_sinks.len().saturating_mul(8));
        if bytes > self.limits.max_probe_profile_bytes {
            return Err(ProtocolError::Limit);
        }
        {
            let shared = hub.try_borrow().map_err(|_| ProtocolError::Busy)?;
            if shared.limits().producers > self.limits.max_probe_profiles
                || shared.limits().subscriptions > self.limits.max_probe_profiles
            {
                return Err(ProtocolError::Limit);
            }
            for (index, id) in exposed_sinks.iter().enumerate() {
                if exposed_sinks[..index].contains(id) || shared.health(*id).is_none() {
                    return Err(ProtocolError::InvalidInput);
                }
            }
        }
        self.probes = Some(ProbeAttachment {
            hub,
            profiles,
            exposed_sinks,
            selected: None,
        });
        Ok(())
    }
    pub fn session(&self) -> &DebugSession<M> {
        &self.session
    }
    pub fn epoch(&self) -> u64 {
        self.epoch
    }
    pub fn limits(&self) -> &ProtocolLimits {
        &self.limits
    }
    pub fn configuration_revision(&self) -> u64 {
        self.configuration
    }
    pub fn disconnect(&mut self) {
        self.connected = false;
    }
    /// Reconnect preserves model state and authority. Request IDs restart only in
    /// a new epoch, while old handles and candidates are invalidated.
    pub fn reconnect(&mut self) -> Result<u64, ProtocolError> {
        if self.connected {
            return Err(ProtocolError::InvalidInput);
        }
        self.epoch = fresh_epoch()?;
        self.connected = true;
        self.cache.clear();
        self.cache_bytes = 0;
        self.high_water = 0;
        self.handles.clear();
        self.metric_capture = None;
        self.candidates.clear();
        self.candidate_bytes = 0;
        self.events.clear();
        self.event_bytes = 0;
        self.event_sequence = 0;
        self.publish("reconnected");
        Ok(self.epoch)
    }
    fn snapshot_id(&self) -> SnapshotId {
        SnapshotId {
            session: self.session_key,
            revision: self.session.revision(),
            sequence: self.session.sequence(),
        }
    }
    fn response(
        &self,
        request_id: u64,
        result: Result<(), ProtocolError>,
        fields: Vec<(String, String)>,
    ) -> Response {
        Response {
            version: PROTOCOL_VERSION,
            session: self.session.id().into(),
            epoch: self.epoch,
            request_id,
            revision: self.session.revision(),
            configuration: self.configuration,
            result,
            fields,
        }
    }
    /// No callback is invoked until the envelope, authority, revisions and command
    /// budgets are validated. Every admitted request ID is retired permanently
    /// for this epoch, even when its bounded cached response has been evicted.
    pub fn handle(&mut self, mut request: Request) -> Response {
        // Epoch zero is a read-only discovery request, never mutation authority.
        if request.epoch == 0 && matches!(request.command, Command::Status) {
            request.epoch = self.epoch;
        }
        let fail = |this: &Self, error| this.response(request.request_id, Err(error), vec![]);
        if request.version != PROTOCOL_VERSION {
            return fail(self, ProtocolError::UnsupportedVersion);
        }
        if request.session != self.session.id() {
            return fail(self, ProtocolError::WrongSession);
        }
        if request.epoch != self.epoch {
            return fail(self, ProtocolError::WrongEpoch);
        }
        if !self.connected {
            return fail(self, ProtocolError::Disconnected);
        }
        if request.request_id == 0 {
            return fail(self, ProtocolError::Malformed);
        }
        let request_bytes = match encode_request(&request, &self.limits) {
            Ok(bytes) => bytes.len(),
            Err(error) => return fail(self, error),
        };
        if let Some(cached) = self
            .cache
            .iter()
            .find(|entry| entry.request.request_id == request.request_id)
        {
            return if cached.request == request {
                cached.response.clone()
            } else {
                fail(self, ProtocolError::DuplicateConflict)
            };
        }
        if request.request_id <= self.high_water {
            return fail(self, ProtocolError::RetiredRequest);
        }
        self.high_water = request.request_id;
        let outcome = self.execute(&request);
        let mut response = match outcome {
            Ok(fields) => self.response(request.request_id, Ok(()), fields),
            Err(error) => self.response(request.request_id, Err(error), vec![]),
        };
        let response_bytes = match encode_response(&response, &self.limits) {
            Ok(bytes) => bytes.len(),
            Err(_) => {
                response = self.response(request.request_id, Err(ProtocolError::Limit), vec![]);
                encode_response(&response, &self.limits).map_or(0, |b| b.len())
            }
        };
        let bytes = request_bytes
            .saturating_add(response_bytes)
            .saturating_add(std::mem::size_of::<CacheEntry>())
            .saturating_add(
                response
                    .fields
                    .len()
                    .saturating_mul(std::mem::size_of::<(String, String)>()),
            )
            .saturating_add(command_overhead(&request.command));
        while self.cache.len() >= self.limits.max_cache_entries
            || bytes > self.limits.max_cache_bytes.saturating_sub(self.cache_bytes)
        {
            if let Some(old) = self.cache.pop_front() {
                self.cache_bytes -= old.bytes;
            } else {
                break;
            }
        }
        if bytes <= self.limits.max_cache_bytes {
            self.cache_bytes += bytes;
            self.cache.push_back(CacheEntry {
                request,
                response: response.clone(),
                bytes,
            });
        }
        response
    }
    fn require_revision(&self, expected: u64) -> Result<(), ProtocolError> {
        self.session.check_revision(expected).map_err(Into::into)
    }
    fn bound_handle(&self, id: u64) -> Result<SnapshotId, ProtocolError> {
        let snapshot = self
            .handles
            .iter()
            .find(|handle| handle.id == id)
            .ok_or(ProtocolError::StaleHandle)?
            .snapshot;
        if snapshot != self.snapshot_id() {
            return Err(ProtocolError::StaleHandle);
        }
        Ok(snapshot)
    }
    fn execute(&mut self, request: &Request) -> Result<Vec<(String, String)>, ProtocolError> {
        let mut fields = Fields::new(&self.limits, escaped_len(self.session.id()));
        match &request.command {
            Command::Status => {
                fields.put("mode", "enum:simulation")?;
                fields.put("phase", format!("enum:{:?}", self.session.phase()))?;
                fields.put("sequence", number(self.session.sequence()))?;
                fields.put(
                    "environment",
                    format!("string:{}", self.session.input_policy()),
                )?;
                fields.put(
                    "capability.input_delivery",
                    boolean(self.authority.deliver_inputs),
                )?;
                fields.put("capability.inspection", boolean(self.inspector.is_some()))?;
                fields.put(
                    "capability.watch_configuration",
                    boolean(self.authority.configure_watches && self.watches.is_some()),
                )?;
                fields.put(
                    "capability.exact_values",
                    boolean(self.authority.read_exact_values),
                )?;
                fields.put("capability.live_control", "bool:false")?;
                fields.put("capability.live_restore", "bool:false")?;
                fields.put("capability.effects", "bool:false")?;
                fields.put("capability.probe_queries", boolean(self.probes.is_some()))?;
                fields.put(
                    "capability.probe_configuration",
                    boolean(self.probes.is_some() && self.authority.configure_diagnostics),
                )?;
                fields.put(
                    "capability.trace_export",
                    boolean(self.authority.export_exact_trace && self.session.recorder().is_some()),
                )?;
                fields.put(
                    "capability.effect_telemetry",
                    boolean(self.telemetry.is_some()),
                )?;
                fields.put(
                    "capability.metric_queries",
                    boolean(self.telemetry.is_some()),
                )?;
                fields.put(
                    "limits.telemetry_snapshot_bytes",
                    number(self.limits.max_telemetry_snapshot_bytes as u64),
                )?;
                fields.put(
                    "limits.frame_bytes",
                    number(self.limits.max_frame_bytes as u64),
                )?;
                fields.put(
                    "limits.dedup_entries",
                    number(self.limits.max_cache_entries as u64),
                )?;
                fields.put(
                    "limits.dedup_bytes",
                    number(self.limits.max_cache_bytes as u64),
                )?;
                fields.put("limits.page_size", number(self.limits.max_page_size as u64))?;
                fields.put("events.dropped", number(self.event_drops))?;
                fields.put(
                    "stop",
                    format!("enum:{}", stop_code(self.session.stop_reason())),
                )?;
            }
            Command::Snapshot => {
                self.require_revision(request.expected_revision)?;
                self.next_handle = self
                    .next_handle
                    .checked_add(1)
                    .ok_or(ProtocolError::Exhausted)?;
                let snapshot = self.snapshot_id();
                if self.handles.len() == self.limits.max_handles {
                    self.handles.pop_front();
                }
                self.handles.push_back(Handle {
                    id: self.next_handle,
                    snapshot,
                });
                fields.put("handle", number(self.next_handle))?;
                fields.put("snapshot.revision", number(snapshot.revision))?;
                fields.put("snapshot.sequence", number(snapshot.sequence))?;
            }
            Command::ExportTrace {
                handle,
                offset,
                limit,
            } => {
                if !self.authority.export_exact_trace {
                    return Err(ProtocolError::Unauthorized);
                }
                let snapshot = self.bound_handle(*handle)?;
                self.require_revision(request.expected_revision)?;
                if *limit == 0
                    || *limit > self.limits.max_field_bytes.saturating_sub(6) / 2
                    || offset.checked_add(*limit as u64).is_none()
                {
                    return Err(ProtocolError::Limit);
                }
                let recorder = self.session.recorder().ok_or(ProtocolError::Unsupported)?;
                let total = recorder.retained_bytes();
                if *offset > total {
                    return Err(ProtocolError::InvalidInput);
                }
                let mut window = TraceWindow::new(*offset, *limit);
                recorder
                    .write_to(&mut window)
                    .map_err(|_| ProtocolError::Callback)?;
                if window.position != total {
                    return Err(ProtocolError::Callback);
                }
                let next = offset
                    .checked_add(window.bytes.len() as u64)
                    .ok_or(ProtocolError::Limit)?;
                fields.put("export.kind", "enum:exact_trace_sensitive")?;
                fields.put("export.redacted", "bool:false")?;
                fields.put("export.snapshot_revision", number(snapshot.revision))?;
                fields.put("export.snapshot_sequence", number(snapshot.sequence))?;
                fields.put("export.total_bytes", number(total))?;
                fields.put("export.offset", number(*offset))?;
                fields.put("export.returned_bytes", number(window.bytes.len() as u64))?;
                fields.put(
                    "export.next_offset",
                    if next < total {
                        number(next)
                    } else {
                        "none".into()
                    },
                )?;
                fields.put("export.complete", boolean(next == total))?;
                fields.put(
                    "export.retained_steps",
                    number(recorder.retained_steps() as u64),
                )?;
                fields.put("export.evicted_steps", number(recorder.evicted_steps()))?;
                fields.put(
                    "export.capture_outcome",
                    format!("enum:{}", stop_code(self.session.stop_reason())),
                )?;
                fields.put("export.bytes", format!("bytes:{}", hex(&window.bytes)))?;
            }
            Command::Inspect {
                handle,
                schema,
                offset,
                limit,
                path,
            } => {
                let snapshot = self.bound_handle(*handle)?;
                self.require_revision(request.expected_revision)?;
                self.page(*offset, *limit)?;
                let inspect = self.inspector.ok_or(ProtocolError::Unsupported)?;
                let projection = inspect(
                    self.session.state(),
                    &InspectQuery {
                        snapshot,
                        schema_version: *schema,
                        path: path.clone(),
                        page: PageRequest {
                            offset: *offset,
                            limit: *limit,
                        },
                        limits: InspectLimits {
                            max_depth: 16,
                            max_nodes: 128,
                            max_work: 10_000,
                            max_bytes: self.limits.max_frame_bytes / 8,
                            max_value_bytes: self.limits.max_field_bytes / 4,
                            max_page_size: self.limits.max_page_size,
                        },
                    },
                )
                .map_err(|_| ProtocolError::InvalidInput)?;
                if projection.schema.name.len().saturating_add(7) > self.limits.max_field_bytes {
                    return Err(ProtocolError::Limit);
                }
                fields.put("schema.name", format!("string:{}", projection.schema.name))?;
                fields.put(
                    "schema.version",
                    number(u64::from(projection.schema.version)),
                )?;
                fields.put("snapshot.revision", number(snapshot.revision))?;
                fields.put("snapshot.sequence", number(snapshot.sequence))?;
                append_node(&mut fields, "node", &projection.node, 0)?;
            }
            Command::Checks { handle } => {
                self.bound_handle(*handle)?;
                let observation = self.session.observation();
                let checks = match &observation {
                    DebugObservation::Initial { checks, .. } => *checks,
                    DebugObservation::Turn(turn) => turn.checks,
                };
                fields.put("checks.complete", boolean(self.session.checks_complete()))?;
                fields.put("checks.total", number(checks.len() as u64))?;
                // A bounded prefix must not hide the first failing property.
                // Export its stable identity independently of the displayed
                // check page; application failure messages remain private.
                if let Some((index, failed)) = checks
                    .iter()
                    .enumerate()
                    .find(|(_, check)| check.is_failure())
                {
                    if failed.id.len().saturating_add(7) > self.limits.max_field_bytes {
                        return Err(ProtocolError::Limit);
                    }
                    fields.put("checks.first_failure.id", format!("string:{}", failed.id))?;
                    fields.put("checks.first_failure.index", number(index as u64))?;
                    fields.put(
                        "checks.first_failure.phase",
                        match &observation {
                            DebugObservation::Initial { .. } => "enum:initial_state",
                            DebugObservation::Turn(turn) if index < turn.state_check_count => {
                                "enum:post_state"
                            }
                            DebugObservation::Turn(_) => "enum:transition",
                        },
                    )?;
                    fields.put(
                        "checks.first_failure.sequence",
                        number(self.session.sequence()),
                    )?;
                } else {
                    fields.put("checks.first_failure", "none")?;
                }
                let count = checks.len().min(self.limits.max_page_size);
                fields.put("checks.returned", number(count as u64))?;
                fields.put("checks.truncated", boolean(count < checks.len()))?;
                for (index, check) in checks.iter().take(count).enumerate() {
                    if check.id.len().saturating_add(7) > self.limits.max_field_bytes {
                        return Err(ProtocolError::Limit);
                    }
                    fields.put(format!("checks.{index}.id"), format!("string:{}", check.id))?;
                    fields.put(
                        format!("checks.{index}.status"),
                        match check.status {
                            CheckStatus::Passed => "enum:passed",
                            CheckStatus::Failed(_) => "enum:failed",
                            CheckStatus::Skipped(_) => "enum:skipped",
                        },
                    )?;
                }
            }
            Command::Outputs { handle } => {
                self.bound_handle(*handle)?;
                if let DebugObservation::Turn(turn) = self.session.observation() {
                    fields.put(
                        "outputs.total",
                        number(turn.transition.outputs.len() as u64),
                    )?;
                    fields.put(
                        "disposition",
                        match turn.transition.disposition {
                            stateless::Disposition::Accepted => "enum:accepted",
                            stateless::Disposition::Rejected(_) => "enum:rejected",
                            stateless::Disposition::Ignored(_) => "enum:ignored",
                        },
                    )?;
                    fields.put("outputs.provenance", "enum:simulated")?;
                    fields.put(
                        "outputs.payloads",
                        if self.authority.read_exact_values {
                            "enum:exact_sensitive"
                        } else {
                            "enum:unavailable"
                        },
                    )?;
                    if self.authority.read_exact_values {
                        for (index, output) in turn
                            .transition
                            .outputs
                            .iter()
                            .take(self.limits.max_page_size)
                            .enumerate()
                        {
                            let mut bytes = Vec::new();
                            let mut bounded =
                                EncodeBuffer::new(&mut bytes, self.limits.max_input_bytes);
                            self.session
                                .model()
                                .encode_output_into(output, &mut bounded)
                                .map_err(|_| ProtocolError::Callback)?;
                            bounded.finish().map_err(|_| ProtocolError::Limit)?;
                            fields.put(
                                format!("outputs.{index}.encoded"),
                                format!("bytes:{}", hex(&bytes)),
                            )?;
                        }
                        fields.put(
                            "outputs.truncated",
                            boolean(turn.transition.outputs.len() > self.limits.max_page_size),
                        )?;
                    }
                } else {
                    fields.put("outputs.total", "u64:0")?;
                }
            }
            Command::Inputs { offset, limit } => {
                self.require_revision(request.expected_revision)?;
                self.page(*offset, *limit)?;
                let page = self
                    .session
                    .inputs(request.expected_revision, *offset, *limit)?;
                fields.put("complete", boolean(page.complete))?;
                fields.put(
                    "next_offset",
                    page.next_offset.map_or("none".into(), |n| number(n as u64)),
                )?;
                let mut pending = Vec::new();
                let mut pending_bytes = 0usize;
                for (index, candidate) in page.candidates.into_iter().enumerate() {
                    let mut encoded = Vec::new();
                    let mut bounded = EncodeBuffer::new(&mut encoded, self.limits.max_input_bytes);
                    self.session
                        .model()
                        .encode_input_into(candidate.input(), &mut bounded)
                        .map_err(|_| ProtocolError::Callback)?;
                    bounded.finish().map_err(|_| ProtocolError::Limit)?;
                    pending_bytes = pending_bytes
                        .checked_add(
                            encoded
                                .len()
                                .saturating_add(std::mem::size_of::<Candidate>()),
                        )
                        .ok_or(ProtocolError::Limit)?;
                    if pending_bytes > self.limits.max_candidate_bytes
                        || pending.len() == self.limits.max_candidates
                    {
                        return Err(ProtocolError::Limit);
                    }
                    self.next_candidate = self
                        .next_candidate
                        .checked_add(1)
                        .ok_or(ProtocolError::Exhausted)?;
                    fields.put(
                        format!("candidates.{index}.token"),
                        number(self.next_candidate),
                    )?;
                    if self.authority.read_exact_values {
                        fields.put(
                            format!("candidates.{index}.encoded"),
                            format!("bytes:{}", hex(&encoded)),
                        )?;
                    }
                    pending.push(Candidate {
                        id: self.next_candidate,
                        revision: self.session.revision(),
                        encoded,
                    });
                }
                fields.put("candidates.returned", number(pending.len() as u64))?;
                while self.candidates.len().saturating_add(pending.len())
                    > self.limits.max_candidates
                    || pending_bytes
                        > self
                            .limits
                            .max_candidate_bytes
                            .saturating_sub(self.candidate_bytes)
                {
                    if let Some(old) = self.candidates.pop_front() {
                        self.candidate_bytes -= old
                            .encoded
                            .len()
                            .saturating_add(std::mem::size_of::<Candidate>());
                    } else {
                        break;
                    }
                }
                for item in pending {
                    self.candidate_bytes += item
                        .encoded
                        .len()
                        .saturating_add(std::mem::size_of::<Candidate>());
                    self.candidates.push_back(item);
                }
            }
            Command::Select { token } => {
                if !self.authority.deliver_inputs {
                    return Err(ProtocolError::Unauthorized);
                }
                self.require_revision(request.expected_revision)?;
                let candidate = self
                    .candidates
                    .iter()
                    .find(|candidate| candidate.id == *token)
                    .ok_or(ProtocolError::StaleHandle)?;
                if candidate.revision != self.session.revision() {
                    return Err(ProtocolError::StaleHandle);
                }
                let input = self
                    .session
                    .model()
                    .decode_input(&candidate.encoded)
                    .map_err(|_| ProtocolError::InvalidInput)?;
                if !self.session.candidate_permitted(&input)? {
                    return Err(ProtocolError::InvalidInput);
                }
                let result = self.session.step(request.expected_revision, input)?;
                fields.put("delivered", boolean(result.delivered))?;
                fields.put("sequence", number(result.sequence))?;
                if result.delivered {
                    self.observe_watches();
                    self.publish("transition");
                }
            }
            Command::Step { encoded_input } => {
                if !self.authority.deliver_inputs {
                    return Err(ProtocolError::Unauthorized);
                }
                self.require_revision(request.expected_revision)?;
                if encoded_input.len() > self.limits.max_input_bytes {
                    return Err(ProtocolError::Limit);
                }
                let result = self
                    .session
                    .step_encoded(request.expected_revision, encoded_input)?;
                fields.put("delivered", boolean(result.delivered))?;
                fields.put("sequence", number(result.sequence))?;
                if result.delivered {
                    self.observe_watches();
                    self.publish("transition");
                }
            }
            Command::Events { limit } => {
                self.page(0, *limit)?;
                fields.put("events.dropped", number(self.event_drops))?;
                let count = self.events.len().min(*limit);
                for (index, event) in self.events.iter().take(count).enumerate() {
                    fields.put(
                        format!("events.{index}.version"),
                        number(u64::from(event.version)),
                    )?;
                    fields.put(
                        format!("events.{index}.session"),
                        format!("string:{}", event.session),
                    )?;
                    fields.put(format!("events.{index}.epoch"), number(event.epoch))?;
                    fields.put(
                        format!("events.{index}.sequence"),
                        number(event.stream_sequence),
                    )?;
                    fields.put(format!("events.{index}.revision"), number(event.revision))?;
                    fields.put(
                        format!("events.{index}.configuration"),
                        number(event.configuration),
                    )?;
                    fields.put(
                        format!("events.{index}.kind"),
                        format!("enum:{}", event.kind),
                    )?;
                }
                fields.put("events.returned", number(count as u64))?;
                // Polling is destructive only after a full bounded response fits.
                for _ in 0..count {
                    if let Some(event) = self.events.pop_front() {
                        self.event_bytes -= event_size(&event);
                    }
                }
            }
            Command::Cancel => {
                if !self.authority.deliver_inputs {
                    return Err(ProtocolError::Unauthorized);
                }
                self.session.cancel(request.expected_revision)?;
                self.publish("cancelled");
            }
            Command::ProbeStatus { offset, limit } => {
                self.page(*offset, *limit)?;
                let attachment = self.probes.as_ref().ok_or(ProtocolError::Unsupported)?;
                let hub = attachment
                    .hub
                    .try_borrow()
                    .map_err(|_| ProtocolError::Busy)?;
                fields.put("probes.capture_revision", number(hub.revision()))?;
                append_producer_status(&mut fields, &hub, *offset, *limit)?;
                let total = attachment
                    .profiles
                    .len()
                    .max(attachment.exposed_sinks.len())
                    .max(hub.producer_status().len());
                fields.put("probes.status_live", "bool:true")?;
                fields.put("probes.offset", number(*offset as u64))?;
                fields.put(
                    "probes.next_offset",
                    if offset.saturating_add(*limit) < total {
                        number((offset + limit) as u64)
                    } else {
                        "none".into()
                    },
                )?;
                fields.put(
                    "probes.profiles.total",
                    number(attachment.profiles.len() as u64),
                )?;
                fields.put(
                    "probes.profiles.truncated",
                    boolean(offset.saturating_add(*limit) < attachment.profiles.len()),
                )?;
                for (index, profile) in attachment
                    .profiles
                    .iter()
                    .skip(*offset)
                    .take(*limit)
                    .enumerate()
                {
                    fields.put(format!("probes.profiles.{index}.id"), number(profile.id))?;
                    fields.put(
                        format!("probes.profiles.{index}.label"),
                        format!("string:{}", profile.label),
                    )?;
                }
                let known = attachment
                    .selected
                    .as_ref()
                    .is_some_and(|(revision, _)| *revision == hub.revision());
                fields.put("probes.selected_profiles_known", boolean(known))?;
                if known {
                    for (index, id) in attachment
                        .selected
                        .as_ref()
                        .unwrap()
                        .1
                        .iter()
                        .skip(*offset)
                        .take(*limit)
                        .enumerate()
                    {
                        fields.put(format!("probes.selected.{index}"), number(*id))?;
                    }
                }
                fields.put(
                    "probes.sinks.total",
                    number(attachment.exposed_sinks.len() as u64),
                )?;
                for (index, sink) in attachment
                    .exposed_sinks
                    .iter()
                    .skip(*offset)
                    .take(*limit)
                    .enumerate()
                {
                    fields.put(format!("probes.sinks.{index}.id"), number(*sink))?;
                    if let Some((records, bytes)) = hub.queued(*sink) {
                        fields.put(
                            format!("probes.sinks.{index}.queued_records"),
                            number(records as u64),
                        )?;
                        fields.put(
                            format!("probes.sinks.{index}.queued_bytes"),
                            number(bytes as u64),
                        )?;
                    }
                    if let Some(health) = hub.health(*sink) {
                        append_probe_health(&mut fields, &format!("probes.sinks.{index}"), health)?;
                    }
                }
            }
            Command::ProbeConfigure {
                expected_capture_revision,
                profiles,
            } => {
                if !self.authority.configure_diagnostics {
                    return Err(ProtocolError::Unauthorized);
                }
                if profiles.len() > self.limits.max_probe_profiles {
                    return Err(ProtocolError::Limit);
                }
                let attachment = self.probes.as_mut().ok_or(ProtocolError::Unsupported)?;
                let mut selected = Vec::with_capacity(profiles.len());
                for (index, id) in profiles.iter().enumerate() {
                    if profiles[..index].contains(id) {
                        return Err(ProtocolError::InvalidInput);
                    }
                    let profile = attachment
                        .profiles
                        .iter()
                        .find(|profile| profile.id == *id)
                        .ok_or(ProtocolError::Unauthorized)?;
                    selected.push(profile.selected.clone());
                }
                let mut hub = attachment
                    .hub
                    .try_borrow_mut()
                    .map_err(|_| ProtocolError::Busy)?;
                if hub.revision() != *expected_capture_revision {
                    return Err(ProtocolError::StaleConfiguration);
                }
                // Preflight the complete acknowledgement before changing capture.
                fields.put("probes.capture_revision", number(u64::MAX))?;
                fields.put("probes.selected_count", number(profiles.len() as u64))?;
                let producers = hub.producer_status();
                fields.put(
                    "probes.pending_producers.total",
                    number(producers.len() as u64),
                )?;
                fields.put("probes.pending_producers.truncated", "bool:false")?;
                let mut truncated = producers.len() > self.limits.max_page_size;
                for (index, producer) in
                    producers.iter().take(self.limits.max_page_size).enumerate()
                {
                    if fields
                        .put(
                            format!("probes.pending_producers.{index}"),
                            number(producer.producer),
                        )
                        .is_err()
                    {
                        truncated = true;
                        break;
                    }
                }
                if truncated {
                    fields
                        .replace_reserved("probes.pending_producers.truncated", "bool:true".into());
                }
                let ack = hub
                    .configure_selected(*expected_capture_revision, selected)
                    .map_err(diagnostic_error)?;
                fields.replace_reserved("probes.capture_revision", number(ack.capture_revision));
                attachment.selected = Some((ack.capture_revision, profiles.clone()));
                // No implicit producer acknowledgement: the old config continues
                // until each application producer acknowledges its own boundary.
            }
            Command::ProbeEvents {
                sink,
                limit,
                include_payload,
            } => {
                self.page(0, *limit)?;
                let attachment = self.probes.as_ref().ok_or(ProtocolError::Unsupported)?;
                if !attachment.exposed_sinks.contains(sink) {
                    return Err(ProtocolError::Unauthorized);
                }
                let mut hub = attachment
                    .hub
                    .try_borrow_mut()
                    .map_err(|_| ProtocolError::Busy)?;
                fields.put("probes.sink", number(*sink))?;
                fields.put("probes.capture_revision", number(hub.revision()))?;
                if let Some(health) = hub.health(*sink) {
                    fields.put("probes.sink.health.dropped", number(health.dropped))?;
                    fields.put(
                        "probes.sink.health.exporter_failed",
                        number(health.exporter_failed),
                    )?;
                }
                let mut count = 0;
                for event in hub
                    .peek_events(*sink, *limit)
                    .ok_or(ProtocolError::InvalidInput)?
                {
                    append_probe_event(
                        &mut fields,
                        &format!("probes.events.{count}"),
                        event,
                        *include_payload,
                    )?;
                    count += 1;
                }
                fields.put("probes.events.returned", number(count as u64))?;
                // Commit consumption only once the entire bounded response fits.
                for _ in 0..count {
                    hub.pop(*sink);
                }
            }
            Command::MetricCatalog { offset, limit } => {
                self.page(*offset, *limit)?;
                let observer = self.telemetry.as_ref().ok_or(ProtocolError::Unsupported)?;
                let catalog = observer.metric_catalog();
                fields.put("catalog.total", number(catalog.len() as u64))?;
                fields.put(
                    "catalog.next_offset",
                    if offset.saturating_add(*limit) < catalog.len() {
                        number((offset + limit) as u64)
                    } else {
                        "none".into()
                    },
                )?;
                for (index, item) in catalog.iter().skip(*offset).take(*limit).enumerate() {
                    fields.put(
                        format!("catalog.{index}.family"),
                        format!("enum:{:?}", item.family),
                    )?;
                    fields.put(
                        format!("catalog.{index}.name"),
                        format!("string:{}", item.name),
                    )?;
                    fields.put(
                        format!("catalog.{index}.kind"),
                        format!("enum:{:?}", item.kind),
                    )?;
                    fields.put(
                        format!("catalog.{index}.unit"),
                        format!("string:{}", item.unit),
                    )?;
                    fields.put(
                        format!("catalog.{index}.population"),
                        format!("string:{}", item.population),
                    )?;
                    fields.put(
                        format!("catalog.{index}.reset"),
                        format!("string:{}", item.reset),
                    )?;
                }
            }
            Command::MetricSnapshot {
                handle,
                offset,
                limit,
            } => {
                self.page(*offset, *limit)?;
                if handle.is_none() {
                    if *offset != 0 {
                        return Err(ProtocolError::InvalidInput);
                    }
                    let new_handle = self
                        .next_handle
                        .checked_add(1)
                        .ok_or(ProtocolError::Exhausted)?;
                    // Preflight under the observer's coherent snapshot boundary:
                    // rejection must preserve both the acknowledged capture and
                    // the host's retained metric-window endpoints.
                    let snapshot = self
                        .telemetry
                        .as_ref()
                        .ok_or(ProtocolError::Unsupported)?
                        .try_metric_snapshot(|snapshot| {
                            self.metric_capture_handle(snapshot)?;
                            append_metrics(&mut fields, new_handle, snapshot, *offset, *limit)
                        })?;
                    self.next_handle = new_handle;
                    self.metric_capture = Some((new_handle, snapshot));
                } else {
                    let (actual_handle, snapshot) = self
                        .metric_capture
                        .as_ref()
                        .ok_or(ProtocolError::StaleHandle)?;
                    if handle != &Some(*actual_handle) {
                        return Err(ProtocolError::StaleHandle);
                    }
                    append_metrics(&mut fields, *actual_handle, snapshot, *offset, *limit)?;
                }
            }
            Command::MetricWindow { from, to, limit } => {
                self.page(0, *limit)?;
                let snapshot = self
                    .telemetry
                    .as_ref()
                    .ok_or(ProtocolError::Unsupported)?
                    .metric_window(*from, *to)
                    .map_err(|_| ProtocolError::StaleHandle)?;
                let handle = self.metric_capture_handle(&snapshot)?;
                append_metrics(&mut fields, handle, &snapshot, 0, *limit)?;
                self.next_handle = handle;
                self.metric_capture = Some((handle, snapshot));
            }
            Command::EffectDetails { id } => {
                let detail = self.effect_detail(*id)?;
                append_effect_details(&mut fields, &detail)?;
            }
            Command::EffectTimeline { id, limit } => {
                self.page(0, *limit)?;
                let detail = self.effect_detail(*id)?;
                append_effect_details(&mut fields, &detail)?;
                fields.put("timeline.events.total", number(detail.events.len() as u64))?;
                fields.put(
                    "timeline.timings.total",
                    number(detail.timings.len() as u64),
                )?;
                fields.put(
                    "timeline.truncated",
                    boolean(detail.events.len() > *limit || detail.timings.len() > *limit),
                )?;
                for (index, event) in detail.events.iter().take(*limit).enumerate() {
                    let key = format!("timeline.events.{index}");
                    fields.put(format!("{key}.sequence"), number(event.sequence))?;
                    fields.put(
                        format!("{key}.debugger_affected"),
                        boolean(event.debugger_affected),
                    )?;
                    if let Some(at) = event.at {
                        fields.put(format!("{key}.clock_domain"), number(at.domain.0))?;
                        fields.put(format!("{key}.nanos"), number(at.nanos))?;
                    }
                    append_lifecycle(&mut fields, &key, &event.kind)?;
                }
                for (index, timing) in detail.timings.iter().take(*limit).enumerate() {
                    let key = format!("timeline.timings.{index}");
                    fields.put(format!("{key}.family"), format!("enum:{:?}", timing.family))?;
                    fields.put(
                        format!("{key}.outcome"),
                        format!("enum:{:?}", timing.outcome),
                    )?;
                    fields.put(
                        format!("{key}.duration_ns"),
                        match timing.duration_ns {
                            Ok(value) => number(value),
                            Err(error) => format!("unknown:{error:?}"),
                        },
                    )?;
                    fields.put(
                        format!("{key}.attempt"),
                        timing.attempt.map_or("none".into(), |id| number(id.serial)),
                    )?;
                    fields.put(
                        format!("{key}.delivery"),
                        timing
                            .delivery
                            .map_or("none".into(), |id| number(id.serial)),
                    )?;
                    fields.put(
                        format!("{key}.debugger_affected"),
                        boolean(timing.debugger_affected),
                    )?;
                }
            }
            Command::TelemetryHealth => {
                let observer = self.telemetry.as_ref().ok_or(ProtocolError::Unsupported)?;
                append_telemetry_health(&mut fields, &observer.telemetry_health())?;
                let pauses = observer.pause_intervals();
                fields.put("pauses.total", number(pauses.len() as u64))?;
                fields.put(
                    "pauses.truncated",
                    boolean(pauses.len() > self.limits.max_page_size),
                )?;
                for (index, pause) in pauses.iter().take(self.limits.max_page_size).enumerate() {
                    fields.put(
                        format!("pauses.{index}.generation"),
                        number(pause.generation),
                    )?;
                    for (boundary, at) in [("start", pause.start), ("end", pause.end)] {
                        if let Some(at) = at {
                            fields.put(
                                format!("pauses.{index}.{boundary}.clock_domain"),
                                number(at.domain.0),
                            )?;
                            fields.put(
                                format!("pauses.{index}.{boundary}.nanos"),
                                number(at.nanos),
                            )?;
                        } else {
                            fields.put(
                                format!("pauses.{index}.{boundary}"),
                                "unknown:clock_or_open_interval",
                            )?;
                        }
                    }
                }
                let windows = observer.available_windows();
                fields.put("metric_windows.total", number(windows.len() as u64))?;
                for (index, revision) in windows.iter().take(self.limits.max_page_size).enumerate()
                {
                    fields.put(format!("metric_windows.{index}"), number(*revision))?;
                }
            }
            Command::Watch {
                schema,
                baseline_revision,
                path,
            } => {
                if !self.authority.configure_watches {
                    return Err(ProtocolError::Unauthorized);
                }
                if request.expected_configuration != self.configuration {
                    return Err(ProtocolError::StaleConfiguration);
                }
                self.require_revision(request.expected_revision)?;
                self.require_revision(*baseline_revision)?;
                let add = self.watch_add.ok_or(ProtocolError::Unsupported)?;
                let registry = self.watches.as_mut().ok_or(ProtocolError::Unsupported)?;
                let mut config = WatchConfig::changed(path.clone(), *schema);
                config.limits.max_bytes = registry
                    .limits()
                    .max_event_bytes
                    .min(self.limits.max_frame_bytes / 8);
                config.limits.max_value_bytes = self.limits.max_field_bytes / 4;
                config.limits.max_nodes = 128;
                config.limits.max_depth = 16;
                config.limits.max_page_size = self.limits.max_page_size;
                config.page.limit = self.limits.max_page_size;
                // Reserve the complete mutation acknowledgement before admission.
                // Optional schema labels cannot turn a successful installation
                // into an error response with no returned watch identity.
                fields.put("watch.id", number(u64::MAX))?;
                fields.put("watch.generation", number(u64::MAX))?;
                fields.put("watch.effective_sequence", number(u64::MAX))?;
                fields.put("watch.installed_at_revision", number(*baseline_revision))?;
                fields.put("watch.baseline", "enum:pending_at_effective_boundary")?;
                fields.put("watch.metadata_complete", "bool:false")?;
                let ack = add(registry, self.session.state(), config).map_err(watch_error)?;
                self.configuration = ack.capture_revision;
                fields.replace_reserved("watch.id", number(ack.watch_id));
                fields.replace_reserved("watch.generation", number(ack.generation));
                fields.replace_reserved("watch.effective_sequence", number(ack.effective_sequence));
                let mut metadata_complete = true;
                if let Some(schema) = ack.display_schema {
                    metadata_complete &= fields
                        .put(
                            "watch.display_schema_version",
                            number(u64::from(schema.version)),
                        )
                        .is_ok();
                    metadata_complete &= schema.name.len().saturating_add(7)
                        <= self.limits.max_field_bytes
                        && fields
                            .put("watch.display_schema", format!("string:{}", schema.name))
                            .is_ok();
                }
                if let Some(kind) = ack.resolved_type {
                    metadata_complete &= kind.len().saturating_add(5)
                        <= self.limits.max_field_bytes
                        && fields
                            .put("watch.resolved_type", format!("enum:{kind}"))
                            .is_ok();
                }
                if metadata_complete {
                    fields.replace_reserved("watch.metadata_complete", "bool:true".into());
                }
                self.publish("watch_configured");
            }
            Command::Unwatch { watch_id } => {
                if !self.authority.configure_watches {
                    return Err(ProtocolError::Unauthorized);
                }
                if request.expected_configuration != self.configuration {
                    return Err(ProtocolError::StaleConfiguration);
                }
                let ack = self
                    .watches
                    .as_mut()
                    .ok_or(ProtocolError::Unsupported)?
                    .remove(*watch_id)
                    .map_err(watch_error)?;
                self.configuration = ack.capture_revision;
                fields.put("watch.id", number(ack.watch_id))?;
                fields.put("watch.generation", number(ack.generation))?;
                fields.put("watch.effective_sequence", number(ack.effective_sequence))?;
                self.publish("watch_removed");
            }
            Command::WatchStatus => {
                let watches = self.watches.as_ref().ok_or(ProtocolError::Unsupported)?;
                let statuses = watches.list();
                fields.put("watches.total", number(statuses.len() as u64))?;
                fields.put(
                    "watches.truncated",
                    boolean(statuses.len() > self.limits.max_page_size),
                )?;
                for (index, status) in statuses.iter().take(self.limits.max_page_size).enumerate() {
                    fields.put(format!("watches.{index}.id"), number(status.id))?;
                    fields.put(
                        format!("watches.{index}.generation"),
                        number(status.generation),
                    )?;
                    fields.put(format!("watches.{index}.enabled"), boolean(status.enabled))?;
                    fields.put(
                        format!("watches.{index}.has_baseline"),
                        boolean(status.has_baseline),
                    )?;
                    fields.put(
                        format!("watches.{index}.effective_sequence"),
                        number(status.effective_sequence),
                    )?;
                }
                fields.put(
                    "watches.dropped_events",
                    number(watches.health().dropped_events),
                )?;
                fields.put(
                    "watches.observation_gaps",
                    number(watches.health().observation_gaps),
                )?;
                fields.put(
                    "watches.inspection_errors",
                    number(watches.health().inspection_errors),
                )?;
            }
            Command::WatchCurrent { watch_id } => {
                let watches = self.watches.as_ref().ok_or(ProtocolError::Unsupported)?;
                if let Some(event) = watches.current(*watch_id) {
                    fields.put("watch.id", number(event.watch_id))?;
                    fields.put("watch.generation", number(event.generation))?;
                    fields.put("watch.capture_revision", number(event.capture_revision))?;
                    fields.put("watch.snapshot_revision", number(event.snapshot.revision))?;
                    fields.put("watch.snapshot_sequence", number(event.snapshot.sequence))?;
                    fields.put("watch.kind", format!("enum:{:?}", event.kind))?;
                    fields.put(
                        "watch.completeness",
                        format!("enum:{:?}", event.completeness),
                    )?;
                    fields.put("watch.origin", format!("enum:{:?}", event.origin))?;
                    if let Some(node) = &event.value {
                        append_node(&mut fields, "watch.node", node, 0)?;
                    }
                } else {
                    fields.put("watch.value", "enum:unavailable")?;
                }
            }
        }
        Ok(fields.finish())
    }
    fn metric_capture_handle(&self, snapshot: &MetricSnapshot) -> Result<u64, ProtocolError> {
        if snapshot.estimated_bytes() > self.limits.max_telemetry_snapshot_bytes {
            return Err(ProtocolError::Limit);
        }
        self.next_handle
            .checked_add(1)
            .ok_or(ProtocolError::Exhausted)
    }
    fn effect_detail(&self, id: EffectId) -> Result<EffectDetails, ProtocolError> {
        let observer = self.telemetry.as_ref().ok_or(ProtocolError::Unsupported)?;
        if id.run != observer.options().run || id.epoch != observer.options().epoch {
            return Err(ProtocolError::WrongEpoch);
        }
        observer
            .effect_details(id)
            .ok_or(ProtocolError::StaleHandle)
    }
    fn observe_watches(&mut self) {
        let snapshot = self.snapshot_id();
        let origin = if self.session.input_policy() == "out-of-domain-injection" {
            OriginKind::OutOfDomainInjection
        } else {
            OriginKind::Simulation
        };
        let mut context = ObservationContext::new(snapshot, origin);
        context.property_failure = matches!(
            self.session.stop_reason(),
            Some(crate::session::StopReason::PropertyFailure)
        );
        if let (Some(watches), Some(observe)) = (&mut self.watches, self.watch_observer) {
            observe(watches, self.session.state(), context);
        }
    }
    fn page(&self, offset: usize, limit: usize) -> Result<(), ProtocolError> {
        if limit == 0 || limit > self.limits.max_page_size || offset.checked_add(limit).is_none() {
            Err(ProtocolError::Limit)
        } else {
            Ok(())
        }
    }
    fn publish(&mut self, kind: &str) {
        let Some(sequence) = self.event_sequence.checked_add(1) else {
            self.event_drops = self.event_drops.saturating_add(1);
            return;
        };
        self.event_sequence = sequence;
        let event = ProtocolEvent {
            version: PROTOCOL_VERSION,
            session: self.session.id().into(),
            epoch: self.epoch,
            stream_sequence: sequence,
            revision: self.session.revision(),
            configuration: self.configuration,
            kind: kind.into(),
        };
        let bytes = event_size(&event);
        if self.events.len() >= self.limits.max_event_entries
            || bytes > self.limits.max_event_bytes.saturating_sub(self.event_bytes)
        {
            self.event_drops = self.event_drops.saturating_add(1);
            return;
        }
        self.event_bytes += bytes;
        self.events.push_back(event);
    }
}
impl<M: ModelCodec + Enumerate> ProtocolSession<M>
where
    M::State: Inspect,
{
    /// Host-supplied allowlist and bounds are retained across reconnections.
    /// Remote commands cannot replace this registry or widen its authorization.
    pub fn enable_watches(&mut self, registry: WatchRegistry) -> Result<(), ProtocolError> {
        if self.watches.is_some() {
            return Err(ProtocolError::InvalidInput);
        }
        self.configuration = registry.capture_revision();
        self.watches = Some(registry);
        self.watch_observer = Some(|registry, state, context| {
            registry.observe(state, context);
        });
        self.watch_add = Some(|registry, state, config| registry.add_validated(state, config));
        self.observe_watches();
        Ok(())
    }
    pub fn enable_inspection(&mut self) {
        self.inspector = Some(|state, query| inspect::inspect(state, query));
    }
}
fn watch_error(error: WatchError) -> ProtocolError {
    match error {
        WatchError::Unauthorized | WatchError::RedactedPath => ProtocolError::Unauthorized,
        WatchError::Capacity => ProtocolError::Limit,
        WatchError::RevisionExhausted => ProtocolError::Exhausted,
        WatchError::UnknownWatch => ProtocolError::StaleHandle,
        _ => ProtocolError::InvalidInput,
    }
}
fn command_overhead(command: &Command) -> usize {
    match command {
        Command::ProbeConfigure { profiles, .. } => profiles.len().saturating_mul(8),
        Command::Inspect { path, .. } | Command::Watch { path, .. } => path
            .len()
            .saturating_mul(std::mem::size_of::<PathSegment>()),
        _ => 0,
    }
}
fn event_size(event: &ProtocolEvent) -> usize {
    std::mem::size_of::<ProtocolEvent>() + event.session.len() + event.kind.len()
}
fn stop_code(stop: Option<&crate::session::StopReason>) -> &'static str {
    use crate::session::StopReason::*;
    match stop {
        None => "none",
        Some(PreBreakpoint(_)) => "pre_breakpoint",
        Some(PostBreakpoint(_)) => "post_breakpoint",
        Some(PropertyFailure) => "property_failure",
        Some(InitialCheckError(_)) => "initial_check_error",
        Some(ModelError(_)) => "model_error",
        Some(CheckerError(_)) => "checker_error",
        Some(ObserverError(_)) => "observer_error",
        Some(RecordingError(_)) => "recording_error",
        Some(BudgetExhausted) => "budget_exhausted",
        Some(Cancelled) => "cancelled",
    }
}

struct Fields {
    fields: Vec<(String, String)>,
    bytes: usize,
    max_bytes: usize,
    max_fields: usize,
    max_field_bytes: usize,
}
impl Fields {
    fn new(limits: &ProtocolLimits, escaped_session_bytes: usize) -> Self {
        Self {
            fields: vec![],
            bytes: 256usize.saturating_add(escaped_session_bytes),
            max_bytes: limits.max_frame_bytes,
            max_fields: limits.max_response_fields,
            max_field_bytes: limits.max_field_bytes,
        }
    }
    fn put(
        &mut self,
        key: impl Into<String>,
        value: impl Into<String>,
    ) -> Result<(), ProtocolError> {
        let key = key.into();
        let value = value.into();
        let bytes = escaped_len(&key)
            .saturating_add(escaped_len(&value))
            .saturating_add(2);
        if self.fields.len() >= self.max_fields
            || key.len() > self.max_field_bytes
            || value.len() > self.max_field_bytes
            || bytes > self.max_bytes.saturating_sub(self.bytes)
        {
            return Err(ProtocolError::Limit);
        }
        self.bytes += bytes;
        self.fields.push((key, value));
        Ok(())
    }
    fn replace_reserved(&mut self, key: &str, value: String) {
        let field = self
            .fields
            .iter_mut()
            .find(|(name, _)| name == key)
            .expect("reserved field");
        assert!(
            value.len() <= field.1.len(),
            "reserved acknowledgement must not grow"
        );
        field.1 = value;
    }
    fn finish(self) -> Vec<(String, String)> {
        self.fields
    }
}
fn number(value: u64) -> String {
    format!("u64:{value}")
}
fn boolean(value: bool) -> &'static str {
    if value { "bool:true" } else { "bool:false" }
}
fn append_node(
    fields: &mut Fields,
    prefix: &str,
    node: &InspectNode,
    depth: usize,
) -> Result<(), ProtocolError> {
    if depth > 32 {
        return Err(ProtocolError::Limit);
    }
    fields.put(
        format!("{prefix}.completeness"),
        format!("enum:{:?}", node.completeness),
    )?;
    fields.put(
        format!("{prefix}.child_count"),
        node.child_count.map_or("none".into(), |n| number(n as u64)),
    )?;
    if let Some(page) = node.page {
        fields.put(format!("{prefix}.page.offset"), number(page.offset as u64))?;
        fields.put(
            format!("{prefix}.page.returned"),
            number(page.returned as u64),
        )?;
        fields.put(format!("{prefix}.page.total"), number(page.total as u64))?;
        fields.put(
            format!("{prefix}.page.next"),
            page.next_offset.map_or("none".into(), |n| number(n as u64)),
        )?;
    }
    let (kind, value) = match &node.kind {
        NodeKind::Scalar(Scalar::Unit) => ("unit", "unit".into()),
        NodeKind::Scalar(Scalar::Bool(value)) => ("bool", boolean(*value).into()),
        NodeKind::Scalar(Scalar::Char(value)) => ("char", format!("char:{value}")),
        NodeKind::Scalar(Scalar::Integer { kind, decimal }) => {
            ("integer", format!("{}:{decimal}", integer_name(kind)))
        }
        NodeKind::Scalar(Scalar::Float {
            kind,
            decimal,
            bits,
        }) => {
            fields.put(format!("{prefix}.bits"), number(*bits))?;
            ("float", format!("{kind}:{decimal}"))
        }
        NodeKind::String {
            preview,
            total_bytes,
        } => {
            fields.put(format!("{prefix}.total_bytes"), number(*total_bytes as u64))?;
            ("string", format!("string:{preview}"))
        }
        NodeKind::Bytes {
            preview,
            total_bytes,
        } => {
            fields.put(format!("{prefix}.total_bytes"), number(*total_bytes as u64))?;
            ("bytes", format!("bytes:{}", hex(preview)))
        }
        NodeKind::Object { name } => ("object", format!("string:{name}")),
        NodeKind::Enum {
            name,
            variant_id,
            variant_label,
        } => {
            fields.put(
                format!("{prefix}.variant_id"),
                format!("string:{variant_id}"),
            )?;
            fields.put(
                format!("{prefix}.variant_label"),
                format!("string:{variant_label}"),
            )?;
            ("enum", format!("string:{name}"))
        }
        NodeKind::Sequence { name } => ("sequence", format!("string:{name}")),
        NodeKind::Map { ordering } => ("map", format!("enum:{ordering:?}")),
        NodeKind::Opaque { label } => ("opaque", format!("string:{label}")),
        NodeKind::Redacted => ("redacted", "none".into()),
        NodeKind::Unavailable => ("unavailable", "none".into()),
        NodeKind::Truncated { reason } => ("truncated", format!("enum:{reason:?}")),
    };
    fields.put(format!("{prefix}.kind"), format!("enum:{kind}"))?;
    fields.put(format!("{prefix}.value"), value)?;
    fields.put(
        format!("{prefix}.children.returned"),
        number(node.children.len() as u64),
    )?;
    for (index, child) in node.children.iter().enumerate() {
        let prefix = format!("{prefix}.children.{index}");
        fields.put(format!("{prefix}.segment"), encode_segment(&child.segment))?;
        fields.put(format!("{prefix}.label"), format!("string:{}", child.label))?;
        append_node(fields, &format!("{prefix}.node"), &child.node, depth + 1)?;
    }
    Ok(())
}
fn integer_name(kind: &IntegerType) -> &'static str {
    match kind {
        IntegerType::I8 => "i8",
        IntegerType::I16 => "i16",
        IntegerType::I32 => "i32",
        IntegerType::I64 => "i64",
        IntegerType::I128 => "i128",
        IntegerType::Isize => "isize",
        IntegerType::U8 => "u8",
        IntegerType::U16 => "u16",
        IntegerType::U32 => "u32",
        IntegerType::U64 => "u64",
        IntegerType::U128 => "u128",
        IntegerType::Usize => "usize",
    }
}

fn encode_segment(segment: &PathSegment) -> String {
    match segment {
        PathSegment::Field(value) => format!("field:{value}"),
        PathSegment::Variant(value) => format!("variant:{value}"),
        PathSegment::Index(value) => format!("index:{value}"),
        PathSegment::MapKey(MapKey::String(value)) => format!("key.string:{value}"),
        PathSegment::MapKey(MapKey::Bool(value)) => format!("key.bool:{value}"),
        PathSegment::MapKey(MapKey::Char(value)) => format!("key.char:{value}"),
        PathSegment::MapKey(MapKey::Integer { kind, decimal }) => {
            format!("key.{}:{decimal}", integer_name(kind))
        }
    }
}
fn decode_segment(value: &str) -> Result<PathSegment, ProtocolError> {
    let (kind, value) = value.split_once(':').ok_or(ProtocolError::Malformed)?;
    Ok(match kind {
        "field" => PathSegment::Field(value.into()),
        "variant" => PathSegment::Variant(value.into()),
        "index" => PathSegment::Index(value.parse().map_err(|_| ProtocolError::Malformed)?),
        "key.string" => PathSegment::MapKey(MapKey::String(value.into())),
        "key.bool" => PathSegment::MapKey(MapKey::Bool(match value {
            "true" => true,
            "false" => false,
            _ => return Err(ProtocolError::Malformed),
        })),
        "key.char" => {
            let mut chars = value.chars();
            let ch = chars.next().ok_or(ProtocolError::Malformed)?;
            if chars.next().is_some() {
                return Err(ProtocolError::Malformed);
            }
            PathSegment::MapKey(MapKey::Char(ch))
        }
        key => {
            let (kind, signed, bits) = match key {
                "key.i8" => (IntegerType::I8, true, 8),
                "key.i16" => (IntegerType::I16, true, 16),
                "key.i32" => (IntegerType::I32, true, 32),
                "key.i64" => (IntegerType::I64, true, 64),
                "key.i128" => (IntegerType::I128, true, 128),
                "key.isize" => (IntegerType::Isize, true, usize::BITS),
                "key.u8" => (IntegerType::U8, false, 8),
                "key.u16" => (IntegerType::U16, false, 16),
                "key.u32" => (IntegerType::U32, false, 32),
                "key.u64" => (IntegerType::U64, false, 64),
                "key.u128" => (IntegerType::U128, false, 128),
                "key.usize" => (IntegerType::Usize, false, usize::BITS),
                _ => return Err(ProtocolError::Malformed),
            };
            let canonical = if signed {
                let parsed = value
                    .parse::<i128>()
                    .map_err(|_| ProtocolError::Malformed)?;
                if bits < 128
                    && (parsed < -(1i128 << (bits - 1)) || parsed >= (1i128 << (bits - 1)))
                {
                    return Err(ProtocolError::Malformed);
                }
                parsed.to_string()
            } else {
                let parsed = value
                    .parse::<u128>()
                    .map_err(|_| ProtocolError::Malformed)?;
                if bits < 128 && parsed >= (1u128 << bits) {
                    return Err(ProtocolError::Malformed);
                }
                parsed.to_string()
            };
            PathSegment::MapKey(MapKey::Integer {
                kind,
                decimal: canonical,
            })
        }
    })
}
/// Percent encoding escapes controls, terminal escapes, markup, tabs, Unicode
/// bytes and percent itself. A renderer must display decoded text as text, never
/// interpret it as terminal commands or HTML. Raw wire output is ASCII-safe.
fn escaped_len(value: &str) -> usize {
    value.bytes().fold(0usize, |bytes, byte| {
        bytes.saturating_add(
            if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b':') {
                1
            } else {
                3
            },
        )
    })
}
pub fn escape(value: &str) -> String {
    let mut out = String::new();
    for byte in value.bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.' | b':') {
            out.push(byte as char);
        } else {
            const HEX: &[u8; 16] = b"0123456789ABCDEF";
            out.push('%');
            out.push(HEX[(byte >> 4) as usize] as char);
            out.push(HEX[(byte & 15) as usize] as char);
        }
    }
    out
}
fn unescape(value: &str, limit: usize) -> Result<String, ProtocolError> {
    if value.len() > limit.saturating_mul(3) {
        return Err(ProtocolError::Limit);
    }
    let mut out = Vec::new();
    let bytes = value.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        let byte = bytes[index];
        if byte == b'%' {
            if index + 2 >= bytes.len() {
                return Err(ProtocolError::Malformed);
            }
            out.push((nibble(bytes[index + 1])? << 4) | nibble(bytes[index + 2])?);
            index += 3;
        } else {
            if !byte.is_ascii_alphanumeric() && !matches!(byte, b'-' | b'_' | b'.' | b':') {
                return Err(ProtocolError::Malformed);
            }
            out.push(byte);
            index += 1;
        }
        if out.len() > limit {
            return Err(ProtocolError::Limit);
        }
    }
    String::from_utf8(out).map_err(|_| ProtocolError::Malformed)
}
fn nibble(byte: u8) -> Result<u8, ProtocolError> {
    match byte {
        b'0'..=b'9' => Ok(byte - b'0'),
        b'a'..=b'f' => Ok(byte - b'a' + 10),
        b'A'..=b'F' => Ok(byte - b'A' + 10),
        _ => Err(ProtocolError::Malformed),
    }
}
fn hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len().saturating_mul(2));
    for byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 15) as usize] as char);
    }
    out
}
fn unhex(value: &str, maximum: usize) -> Result<Vec<u8>, ProtocolError> {
    let value = value
        .strip_prefix("bytes:")
        .ok_or(ProtocolError::Malformed)?;
    if value.len() % 2 != 0 {
        return Err(ProtocolError::Malformed);
    }
    if value.len() / 2 > maximum {
        return Err(ProtocolError::Limit);
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|b| Ok((nibble(b[0])? << 4) | nibble(b[1])?))
        .collect()
}
fn numeric(value: &str) -> Result<u64, ProtocolError> {
    let decimal = value.strip_prefix("u64:").ok_or(ProtocolError::Malformed)?;
    if decimal.is_empty() || decimal.bytes().any(|b| !b.is_ascii_digit()) {
        return Err(ProtocolError::Malformed);
    }
    decimal.parse().map_err(|_| ProtocolError::Malformed)
}
fn usize_value(value: &str) -> Result<usize, ProtocolError> {
    usize::try_from(numeric(value)?).map_err(|_| ProtocolError::Limit)
}
fn schema_value(value: &str) -> Result<u32, ProtocolError> {
    u32::try_from(numeric(value)?).map_err(|_| ProtocolError::Malformed)
}
fn fields_bytes(
    values: &[String],
    limits: &ProtocolLimits,
    max_fields: usize,
) -> Result<Vec<u8>, ProtocolError> {
    if values.len() > max_fields {
        return Err(ProtocolError::Limit);
    }
    let mut out = Vec::new();
    for (index, value) in values.iter().enumerate() {
        if value.len() > limits.max_field_bytes {
            return Err(ProtocolError::Limit);
        }
        let value = escape(value);
        if value.len().saturating_add(usize::from(index != 0))
            > limits.max_frame_bytes.saturating_sub(out.len())
        {
            return Err(ProtocolError::Limit);
        }
        if index != 0 {
            out.push(b'\t');
        }
        out.extend_from_slice(value.as_bytes());
    }
    Ok(out)
}
fn decode_fields(
    bytes: &[u8],
    limits: &ProtocolLimits,
    max_fields: usize,
) -> Result<Vec<String>, ProtocolError> {
    if bytes.len() > limits.max_frame_bytes {
        return Err(ProtocolError::Limit);
    }
    let value = std::str::from_utf8(bytes).map_err(|_| ProtocolError::Malformed)?;
    let mut fields = Vec::new();
    for field in value.split('\t') {
        if fields.len() == max_fields {
            return Err(ProtocolError::Limit);
        }
        fields.push(unescape(field, limits.max_field_bytes)?);
    }
    Ok(fields)
}
pub fn encode_request(
    request: &Request,
    limits: &ProtocolLimits,
) -> Result<Vec<u8>, ProtocolError> {
    let mut fields = vec![
        "DDBG".into(),
        format!("u32:{}", request.version),
        "request".into(),
        format!("string:{}", request.session),
        number(request.epoch),
        number(request.request_id),
        number(request.expected_revision),
        number(request.expected_configuration),
    ];
    match &request.command {
        Command::Status => fields.push("status".into()),
        Command::Snapshot => fields.push("snapshot".into()),
        Command::ExportTrace {
            handle,
            offset,
            limit,
        } => fields.extend([
            "export_trace".into(),
            number(*handle),
            number(*offset),
            number(*limit as u64),
        ]),
        Command::Checks { handle } => fields.extend(["checks".into(), number(*handle)]),
        Command::Outputs { handle } => fields.extend(["outputs".into(), number(*handle)]),
        Command::Inspect {
            handle,
            schema,
            offset,
            limit,
            path,
        } => {
            if path.len() > 32 {
                return Err(ProtocolError::Limit);
            }
            fields.extend([
                "inspect".into(),
                number(*handle),
                number(u64::from(*schema)),
                number(*offset as u64),
                number(*limit as u64),
            ]);
            fields.extend(path.iter().map(encode_segment));
        }
        Command::Inputs { offset, limit } => fields.extend([
            "inputs".into(),
            number(*offset as u64),
            number(*limit as u64),
        ]),
        Command::Select { token } => fields.extend(["select".into(), number(*token)]),
        Command::Step { encoded_input } => {
            if encoded_input.len() > limits.max_input_bytes {
                return Err(ProtocolError::Limit);
            }
            fields.extend(["step".into(), format!("bytes:{}", hex(encoded_input))]);
        }
        Command::Events { limit } => fields.extend(["events".into(), number(*limit as u64)]),
        Command::Cancel => fields.push("cancel".into()),
        Command::Watch {
            schema,
            baseline_revision,
            path,
        } => {
            if path.len() > 32 {
                return Err(ProtocolError::Limit);
            }
            fields.extend([
                "watch".into(),
                number(u64::from(*schema)),
                number(*baseline_revision),
            ]);
            fields.extend(path.iter().map(encode_segment));
        }
        Command::Unwatch { watch_id } => fields.extend(["unwatch".into(), number(*watch_id)]),
        Command::WatchStatus => fields.push("watch_status".into()),
        Command::WatchCurrent { watch_id } => {
            fields.extend(["watch_current".into(), number(*watch_id)])
        }
        Command::MetricCatalog { offset, limit } => fields.extend([
            "metric_catalog".into(),
            number(*offset as u64),
            number(*limit as u64),
        ]),
        Command::MetricSnapshot {
            handle,
            offset,
            limit,
        } => fields.extend([
            "metric_snapshot".into(),
            handle.map_or("none".into(), number),
            number(*offset as u64),
            number(*limit as u64),
        ]),
        Command::MetricWindow { from, to, limit } => fields.extend([
            "metric_window".into(),
            number(*from),
            number(*to),
            number(*limit as u64),
        ]),
        Command::EffectDetails { id } => fields.extend([
            "effect_details".into(),
            number(id.run),
            number(id.epoch),
            number(id.serial),
        ]),
        Command::EffectTimeline { id, limit } => fields.extend([
            "effect_timeline".into(),
            number(id.run),
            number(id.epoch),
            number(id.serial),
            number(*limit as u64),
        ]),
        Command::TelemetryHealth => fields.push("telemetry_health".into()),
        Command::ProbeStatus { offset, limit } => fields.extend([
            "probe_status".into(),
            number(*offset as u64),
            number(*limit as u64),
        ]),
        Command::ProbeConfigure {
            expected_capture_revision,
            profiles,
        } => {
            fields.extend(["probe_configure".into(), number(*expected_capture_revision)]);
            fields.extend(profiles.iter().map(|id| number(*id)));
        }
        Command::ProbeEvents {
            sink,
            limit,
            include_payload,
        } => fields.extend([
            "probe_events".into(),
            number(*sink),
            number(*limit as u64),
            boolean(*include_payload).into(),
        ]),
    }
    fields_bytes(&fields, limits, limits.max_request_fields)
}
pub fn decode_request(bytes: &[u8], limits: &ProtocolLimits) -> Result<Request, ProtocolError> {
    let fields = decode_fields(bytes, limits, limits.max_request_fields)?;
    if fields.len() < 9 || fields[0] != "DDBG" || fields[2] != "request" {
        return Err(ProtocolError::Malformed);
    }
    let version = fields[1]
        .strip_prefix("u32:")
        .ok_or(ProtocolError::Malformed)?
        .parse::<u32>()
        .map_err(|_| ProtocolError::Malformed)?;
    if version != PROTOCOL_VERSION {
        return Err(ProtocolError::UnsupportedVersion);
    }
    let args = &fields[9..];
    let exact = |n: usize| {
        if args.len() == n {
            Ok(())
        } else {
            Err(ProtocolError::Malformed)
        }
    };
    let path = |start: usize| -> Result<Vec<PathSegment>, ProtocolError> {
        if args.len() < start || args.len() - start > 32 {
            return Err(ProtocolError::Limit);
        }
        args[start..]
            .iter()
            .map(|value| decode_segment(value))
            .collect()
    };
    let command = match fields[8].as_str() {
        "status" => {
            exact(0)?;
            Command::Status
        }
        "snapshot" => {
            exact(0)?;
            Command::Snapshot
        }
        "export_trace" => {
            exact(3)?;
            Command::ExportTrace {
                handle: numeric(&args[0])?,
                offset: numeric(&args[1])?,
                limit: usize_value(&args[2])?,
            }
        }
        "checks" => {
            exact(1)?;
            Command::Checks {
                handle: numeric(&args[0])?,
            }
        }
        "outputs" => {
            exact(1)?;
            Command::Outputs {
                handle: numeric(&args[0])?,
            }
        }
        "inspect" => {
            if args.len() < 4 {
                return Err(ProtocolError::Malformed);
            }
            Command::Inspect {
                handle: numeric(&args[0])?,
                schema: schema_value(&args[1])?,
                offset: usize_value(&args[2])?,
                limit: usize_value(&args[3])?,
                path: path(4)?,
            }
        }
        "inputs" => {
            exact(2)?;
            Command::Inputs {
                offset: usize_value(&args[0])?,
                limit: usize_value(&args[1])?,
            }
        }
        "select" => {
            exact(1)?;
            Command::Select {
                token: numeric(&args[0])?,
            }
        }
        "step" => {
            exact(1)?;
            Command::Step {
                encoded_input: unhex(&args[0], limits.max_input_bytes)?,
            }
        }
        "events" => {
            exact(1)?;
            Command::Events {
                limit: usize_value(&args[0])?,
            }
        }
        "cancel" => {
            exact(0)?;
            Command::Cancel
        }
        "watch" => {
            if args.len() < 2 {
                return Err(ProtocolError::Malformed);
            }
            Command::Watch {
                schema: schema_value(&args[0])?,
                baseline_revision: numeric(&args[1])?,
                path: path(2)?,
            }
        }
        "unwatch" => {
            exact(1)?;
            Command::Unwatch {
                watch_id: numeric(&args[0])?,
            }
        }
        "watch_status" => {
            exact(0)?;
            Command::WatchStatus
        }
        "watch_current" => {
            exact(1)?;
            Command::WatchCurrent {
                watch_id: numeric(&args[0])?,
            }
        }
        "metric_catalog" => {
            exact(2)?;
            Command::MetricCatalog {
                offset: usize_value(&args[0])?,
                limit: usize_value(&args[1])?,
            }
        }
        "metric_snapshot" => {
            exact(3)?;
            Command::MetricSnapshot {
                handle: if args[0] == "none" {
                    None
                } else {
                    Some(numeric(&args[0])?)
                },
                offset: usize_value(&args[1])?,
                limit: usize_value(&args[2])?,
            }
        }
        "metric_window" => {
            exact(3)?;
            Command::MetricWindow {
                from: numeric(&args[0])?,
                to: numeric(&args[1])?,
                limit: usize_value(&args[2])?,
            }
        }
        "effect_details" => {
            exact(3)?;
            Command::EffectDetails {
                id: EffectId {
                    run: numeric(&args[0])?,
                    epoch: numeric(&args[1])?,
                    serial: numeric(&args[2])?,
                },
            }
        }
        "effect_timeline" => {
            exact(4)?;
            Command::EffectTimeline {
                id: EffectId {
                    run: numeric(&args[0])?,
                    epoch: numeric(&args[1])?,
                    serial: numeric(&args[2])?,
                },
                limit: usize_value(&args[3])?,
            }
        }
        "telemetry_health" => {
            exact(0)?;
            Command::TelemetryHealth
        }
        "probe_status" => {
            exact(2)?;
            Command::ProbeStatus {
                offset: usize_value(&args[0])?,
                limit: usize_value(&args[1])?,
            }
        }
        "probe_configure" => {
            if args.is_empty() {
                return Err(ProtocolError::Malformed);
            }
            Command::ProbeConfigure {
                expected_capture_revision: numeric(&args[0])?,
                profiles: args[1..]
                    .iter()
                    .map(|value| numeric(value))
                    .collect::<Result<_, _>>()?,
            }
        }
        "probe_events" => {
            exact(3)?;
            Command::ProbeEvents {
                sink: numeric(&args[0])?,
                limit: usize_value(&args[1])?,
                include_payload: match args[2].as_str() {
                    "bool:true" => true,
                    "bool:false" => false,
                    _ => return Err(ProtocolError::Malformed),
                },
            }
        }
        _ => return Err(ProtocolError::Unsupported),
    };
    Ok(Request {
        version,
        session: fields[3]
            .strip_prefix("string:")
            .ok_or(ProtocolError::Malformed)?
            .into(),
        epoch: numeric(&fields[4])?,
        request_id: numeric(&fields[5])?,
        expected_revision: numeric(&fields[6])?,
        expected_configuration: numeric(&fields[7])?,
        command,
    })
}
pub fn encode_response(
    response: &Response,
    limits: &ProtocolLimits,
) -> Result<Vec<u8>, ProtocolError> {
    if response.fields.len() > limits.max_response_fields {
        return Err(ProtocolError::Limit);
    }
    let mut fields = vec![
        "DDBG".into(),
        format!("u32:{}", response.version),
        "response".into(),
        format!("string:{}", response.session),
        number(response.epoch),
        number(response.request_id),
        number(response.revision),
        number(response.configuration),
        if response.result.is_ok() {
            "ok".into()
        } else {
            "error".into()
        },
        response
            .result
            .as_ref()
            .err()
            .map_or("none", ProtocolError::code)
            .into(),
        number(response.fields.len() as u64),
    ];
    for (key, value) in &response.fields {
        fields.push(key.clone());
        fields.push(value.clone());
    }
    fields_bytes(
        &fields,
        limits,
        limits
            .max_response_fields
            .saturating_mul(2)
            .saturating_add(11),
    )
}
/// Standalone events have the same session/epoch/revision identity. The stdio
/// server uses bounded `events` polling to avoid unsolicited output interleaving.
pub fn encode_event(
    event: &ProtocolEvent,
    limits: &ProtocolLimits,
) -> Result<Vec<u8>, ProtocolError> {
    fields_bytes(
        &[
            "DDBG".into(),
            format!("u32:{}", event.version),
            "event".into(),
            format!("string:{}", event.session),
            number(event.epoch),
            number(event.stream_sequence),
            number(event.revision),
            number(event.configuration),
            format!("enum:{}", event.kind),
        ],
        limits,
        16,
    )
}
pub fn decode_response(bytes: &[u8], limits: &ProtocolLimits) -> Result<Response, ProtocolError> {
    let fields = decode_fields(
        bytes,
        limits,
        limits
            .max_response_fields
            .saturating_mul(2)
            .saturating_add(11),
    )?;
    if fields.len() < 11 || fields[0] != "DDBG" || fields[1] != "u32:1" || fields[2] != "response" {
        return Err(ProtocolError::Malformed);
    }
    let count = usize_value(&fields[10])?;
    if count > limits.max_response_fields
        || count.checked_mul(2).and_then(|n| n.checked_add(11)) != Some(fields.len())
    {
        return Err(ProtocolError::Malformed);
    }
    let result = match (fields[8].as_str(), fields[9].as_str()) {
        ("ok", "none") => Ok(()),
        ("error", code) => Err(match code {
            "malformed" => ProtocolError::Malformed,
            "unsupported_version" => ProtocolError::UnsupportedVersion,
            "wrong_session" => ProtocolError::WrongSession,
            "wrong_epoch" => ProtocolError::WrongEpoch,
            "unauthorized" => ProtocolError::Unauthorized,
            "stale_revision" => ProtocolError::StaleRevision,
            "stale_configuration" => ProtocolError::StaleConfiguration,
            "stale_handle" => ProtocolError::StaleHandle,
            "duplicate_conflict" => ProtocolError::DuplicateConflict,
            "retired_request" => ProtocolError::RetiredRequest,
            "limit" => ProtocolError::Limit,
            "unsupported" => ProtocolError::Unsupported,
            "invalid_input" => ProtocolError::InvalidInput,
            "ended" => ProtocolError::Ended,
            "disconnected" => ProtocolError::Disconnected,
            "callback" => ProtocolError::Callback,
            "exhausted" => ProtocolError::Exhausted,
            "busy" => ProtocolError::Busy,
            _ => return Err(ProtocolError::Malformed),
        }),
        _ => return Err(ProtocolError::Malformed),
    };
    Ok(Response {
        version: 1,
        session: fields[3]
            .strip_prefix("string:")
            .ok_or(ProtocolError::Malformed)?
            .into(),
        epoch: numeric(&fields[4])?,
        request_id: numeric(&fields[5])?,
        revision: numeric(&fields[6])?,
        configuration: numeric(&fields[7])?,
        result,
        fields: fields[11..]
            .chunks_exact(2)
            .map(|pair| (pair[0].clone(), pair[1].clone()))
            .collect(),
    })
}
/// Oversized frames are rejected before allocation. Partial headers/bodies are
/// errors, never an EOF or an empty command. The caller should close that stream.
pub fn read_frame(reader: &mut impl Read, maximum: usize) -> io::Result<Option<Vec<u8>>> {
    let mut length = [0u8; 4];
    loop {
        match reader.read(&mut length[..1]) {
            Ok(0) => return Ok(None),
            Ok(_) => break,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error),
        }
    }
    reader.read_exact(&mut length[1..])?;
    let length = u32::from_be_bytes(length) as usize;
    if length == 0 || length > maximum {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "protocol frame exceeds limit",
        ));
    }
    let mut bytes = vec![0; length];
    reader.read_exact(&mut bytes)?;
    Ok(Some(bytes))
}
pub fn write_frame(writer: &mut impl Write, bytes: &[u8], maximum: usize) -> io::Result<()> {
    if bytes.is_empty() || bytes.len() > maximum || bytes.len() > u32::MAX as usize {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "protocol frame exceeds limit",
        ));
    }
    writer.write_all(&(bytes.len() as u32).to_be_bytes())?;
    writer.write_all(bytes)?;
    writer.flush()
}
/// Run a single local connection. Clean EOF disconnects without cancelling or
/// stepping the simulation. A partial write may have followed a committed turn;
/// reconnect requires status reconciliation, never blind mutation retries.
pub fn serve<M: ModelCodec + Enumerate>(
    session: &mut ProtocolSession<M>,
    reader: &mut impl Read,
    writer: &mut impl Write,
) -> io::Result<()> {
    let outcome = (|| {
        while let Some(bytes) = read_frame(reader, session.limits.max_frame_bytes)? {
            let response = match decode_request(&bytes, &session.limits) {
                Ok(request) => session.handle(request),
                Err(error) => session.response(0, Err(error), vec![]),
            };
            let bytes = encode_response(&response, &session.limits).map_err(|_| {
                io::Error::new(
                    io::ErrorKind::InvalidData,
                    "response exceeds protocol limits",
                )
            })?;
            write_frame(writer, &bytes, session.limits.max_frame_bytes)?;
        }
        Ok(())
    })();
    session.disconnect();
    outcome
}

fn append_metrics(
    fields: &mut Fields,
    handle: u64,
    snapshot: &MetricSnapshot,
    offset: usize,
    limit: usize,
) -> Result<(), ProtocolError> {
    fields.put("metrics.handle", number(handle))?;
    fields.put("metrics.revision", number(snapshot.revision))?;
    fields.put("metrics.epoch", number(snapshot.epoch))?;
    fields.put("metrics.complete", boolean(snapshot.complete))?;
    fields.put(
        "metrics.temporality",
        format!("enum:{:?}", snapshot.temporality),
    )?;
    fields.put(
        "metrics.gauge_scope",
        format!("enum:{:?}", snapshot.gauge_scope),
    )?;
    fields.put("metrics.histogram_schema", number(snapshot.schema.id))?;
    fields.put("metrics.histogram_storage_unit", "enum:nanoseconds")?;
    fields.put("metrics.series.total", number(snapshot.series.len() as u64))?;
    fields.put("metrics.series.offset", number(offset as u64))?;
    fields.put(
        "metrics.series.next_offset",
        if offset.saturating_add(limit) < snapshot.series.len() {
            number((offset + limit) as u64)
        } else {
            "none".into()
        },
    )?;
    for (key, at) in [
        ("metrics.start", snapshot.start),
        ("metrics.end", snapshot.end),
    ] {
        if let Some(at) = at {
            fields.put(format!("{key}.clock_domain"), number(at.domain))?;
            fields.put(format!("{key}.nanos"), number(at.nanos))?;
        } else {
            fields.put(key, "unknown:clock")?;
        }
    }
    fields.put(
        "metrics.limits.series",
        number(snapshot.limits.max_series as u64),
    )?;
    fields.put(
        "metrics.limits.bytes",
        number(snapshot.limits.max_bytes as u64),
    )?;
    fields.put(
        "metrics.health.rejected_series",
        number(snapshot.health.rejected_series),
    )?;
    fields.put(
        "metrics.health.invalid_measurements",
        number(snapshot.health.invalid_measurements),
    )?;
    fields.put(
        "metrics.health.omitted_measurements",
        number(snapshot.health.omitted_measurements),
    )?;
    let returned = snapshot.series.iter().skip(offset).take(limit).count();
    fields.put("metrics.series.returned", number(returned as u64))?;
    for (index, (series, value)) in snapshot.series.iter().skip(offset).take(limit).enumerate() {
        let prefix = format!("metrics.series.{index}");
        let descriptor = series.family.descriptor();
        fields.put(
            format!("{prefix}.name"),
            format!("string:{}", descriptor.name),
        )?;
        fields.put(
            format!("{prefix}.kind"),
            format!("enum:{:?}", descriptor.kind),
        )?;
        fields.put(
            format!("{prefix}.unit"),
            format!("string:{}", descriptor.unit),
        )?;
        fields.put(
            format!("{prefix}.population"),
            format!("string:{}", descriptor.population),
        )?;
        append_labels(fields, &prefix, series.labels)?;
        for (kind, id, names) in [
            (
                "operation",
                series.labels.operation,
                &snapshot.labels.operations,
            ),
            (
                "component",
                series.labels.component,
                &snapshot.labels.components,
            ),
            (
                "worker_pool",
                series.labels.worker_pool,
                &snapshot.labels.worker_pools,
            ),
        ] {
            if let Some(name) = names.get(id as usize) {
                fields.put(format!("{prefix}.{kind}.name"), format!("string:{name}"))?;
            }
        }
        match value {
            MetricValue::Counter(value) => {
                fields.put(format!("{prefix}.value"), number(*value))?;
            }
            MetricValue::Gauge(value) => {
                fields.put(format!("{prefix}.value"), number(*value))?;
                fields.put(
                    format!("{prefix}.gauge_scope"),
                    format!(
                        "enum:{:?}",
                        snapshot
                            .gauge_scopes
                            .get(series)
                            .copied()
                            .unwrap_or(crate::metrics::GaugeScope::Unknown)
                    ),
                )?;
            }
            MetricValue::Histogram(histogram) => {
                fields.put(format!("{prefix}.count"), number(histogram.count))?;
                fields.put(
                    format!("{prefix}.sum_ns"),
                    format!("u128:{}", histogram.sum_ns),
                )?;
                for (index, count) in histogram
                    .buckets
                    .iter()
                    .take(snapshot.schema.finite_bounds_ns.len() + 1)
                    .enumerate()
                {
                    fields.put(format!("{prefix}.buckets.{index}.count"), number(*count))?;
                    fields.put(
                        format!("{prefix}.buckets.{index}.upper_bound_ns"),
                        snapshot
                            .schema
                            .finite_bounds_ns
                            .get(index)
                            .map_or("infinity".into(), |v| number(*v)),
                    )?;
                }
                fields.put(
                    format!("{prefix}.quantile_method"),
                    "enum:histogram_bucket_upper_bound",
                )?;
                for (name, numerator) in [("p50", 50), ("p95", 95), ("p99", 99)] {
                    if let Some(quantile) = histogram.quantile(&snapshot.schema, numerator, 100) {
                        fields.put(
                            format!("{prefix}.{name}.upper_bound_ns"),
                            quantile.upper_bound_ns.map_or("infinity".into(), number),
                        )?;
                        fields.put(
                            format!("{prefix}.{name}.observations"),
                            number(quantile.observations),
                        )?;
                        fields.put(
                            format!("{prefix}.{name}.low_sample_warning"),
                            boolean(quantile.low_sample_warning),
                        )?;
                    } else {
                        fields.put(format!("{prefix}.{name}"), "unavailable:no_observations")?;
                    }
                }
            }
        }
    }
    Ok(())
}
fn append_labels(
    fields: &mut Fields,
    prefix: &str,
    labels: crate::metrics::Labels,
) -> Result<(), ProtocolError> {
    fields.put(
        format!("{prefix}.operation.id"),
        number(u64::from(labels.operation)),
    )?;
    fields.put(
        format!("{prefix}.component.id"),
        number(u64::from(labels.component)),
    )?;
    fields.put(
        format!("{prefix}.worker_pool.id"),
        number(u64::from(labels.worker_pool)),
    )?;
    fields.put(
        format!("{prefix}.outcome"),
        format!("enum:{:?}", labels.outcome),
    )?;
    fields.put(
        format!("{prefix}.measurement_origin"),
        format!("enum:{:?}", labels.origin),
    )?;
    fields.put(
        format!("{prefix}.debugger_affected"),
        boolean(labels.debugger_affected),
    )?;
    Ok(())
}
fn append_origin(fields: &mut Fields, origin: RequestOrigin) -> Result<(), ProtocolError> {
    fields.put("effect.origin.run", number(origin.run))?;
    fields.put("effect.origin.epoch", number(origin.epoch))?;
    fields.put("effect.origin.machine", number(origin.machine))?;
    fields.put(
        "effect.origin.transition",
        number(origin.transition_sequence),
    )?;
    fields.put(
        "effect.origin.output_index",
        number(u64::from(origin.output_index)),
    )?;
    Ok(())
}
fn append_effect_details(fields: &mut Fields, detail: &EffectDetails) -> Result<(), ProtocolError> {
    fields.put("effect.run", number(detail.id.run))?;
    fields.put("effect.epoch", number(detail.id.epoch))?;
    fields.put("effect.serial", number(detail.id.serial))?;
    append_origin(fields, detail.origin)?;
    append_labels(fields, "effect", detail.labels)?;
    fields.put("effect.complete", boolean(detail.complete))?;
    fields.put(
        "effect.admission",
        detail
            .admission
            .map_or("unknown".into(), |v| format!("enum:{v:?}")),
    )?;
    fields.put(
        "effect.resolution",
        detail
            .resolution
            .map_or("unknown".into(), |v| format!("enum:{v:?}")),
    )?;
    fields.put("effect.settled", boolean(detail.settled))?;
    fields.put("effect.running_attempts", number(detail.running_attempts))?;
    fields.put(
        "effect.deliveries_observed",
        number(detail.deliveries_observed),
    )?;
    fields.put(
        "effect.cancellation_requested",
        boolean(detail.cancellation_requested),
    )?;
    fields.put(
        "effect.cancellation_acknowledged",
        boolean(detail.cancellation_acknowledged),
    )?;
    fields.put(
        "effect.debugger_affected",
        boolean(detail.debugger_affected),
    )?;
    fields.put("effect.abandoned", boolean(detail.abandoned))?;
    if let Some(at) = detail.requested {
        fields.put("effect.requested.clock_domain", number(at.domain.0))?;
        fields.put("effect.requested.nanos", number(at.nanos))?;
    } else {
        fields.put("effect.requested", "unknown:clock")?;
    }
    Ok(())
}
fn append_lifecycle(
    fields: &mut Fields,
    prefix: &str,
    kind: &LifecycleEventKind,
) -> Result<(), ProtocolError> {
    use LifecycleEventKind::*;
    let name = match kind {
        Requested => "requested",
        Admission(value) => {
            fields.put(format!("{prefix}.admission"), format!("enum:{value:?}"))?;
            "admission"
        }
        AttemptCreated(id) | Ready(id) | AttemptStarted(id) => {
            fields.put(format!("{prefix}.attempt"), number(id.serial))?;
            match kind {
                AttemptCreated(_) => "attempt_created",
                Ready(_) => "ready",
                _ => "attempt_started",
            }
        }
        RetryScheduled { attempt, delay_ns } => {
            fields.put(format!("{prefix}.attempt"), number(attempt.serial))?;
            fields.put(format!("{prefix}.delay_ns"), number(*delay_ns))?;
            "retry_scheduled"
        }
        AttemptFinished { attempt, outcome } => {
            fields.put(format!("{prefix}.attempt"), number(attempt.serial))?;
            fields.put(format!("{prefix}.outcome"), format!("enum:{outcome:?}"))?;
            "attempt_finished"
        }
        Resolved(outcome) => {
            fields.put(format!("{prefix}.outcome"), format!("enum:{outcome:?}"))?;
            "resolved"
        }
        CancellationRequested => "cancellation_requested",
        CancellationAcknowledged => "cancellation_acknowledged",
        PublicationReserved(id) | PublicationFailed(id) | DeliveryBegun(id) => {
            fields.put(format!("{prefix}.delivery"), number(id.serial))?;
            match kind {
                PublicationReserved(_) => "publication_reserved",
                PublicationFailed(_) => "publication_failed",
                _ => "delivery_begun",
            }
        }
        DeliveryObserved {
            delivery,
            disposition,
        } => {
            fields.put(format!("{prefix}.delivery"), number(delivery.serial))?;
            fields.put(
                format!("{prefix}.disposition"),
                format!("enum:{disposition:?}"),
            )?;
            "delivery_observed"
        }
        Settled => "settled",
        Abandoned { attempt } => {
            fields.put(
                format!("{prefix}.attempt"),
                attempt.map_or("none".into(), |id| number(id.serial)),
            )?;
            "abandoned"
        }
    };
    fields.put(format!("{prefix}.kind"), format!("enum:{name}"))
}
fn append_telemetry_health(
    fields: &mut Fields,
    health: &TelemetryHealth,
) -> Result<(), ProtocolError> {
    for (key, value) in [
        ("destructor_hook_losses", health.destructor_hook_losses),
        ("callback_faults", health.callback_faults),
        ("pause_interval_evictions", health.pause_interval_evictions),
        ("detail_evictions", health.detail_evictions),
        ("detail_rejections", health.detail_rejections),
        ("detail_event_drops", health.detail_event_drops),
        ("duplicate_observations", health.duplicate_observations),
        ("duplicate_deliveries", health.duplicate_deliveries),
        ("invalid_lifecycle", health.invalid_lifecycle),
        ("abandoned", health.abandoned),
        ("clock_failures", health.clock_failures),
        (
            "debugger_affected_measurements",
            health.debugger_affected_measurements,
        ),
        ("active_effect_handles", health.active_effect_handles),
        (
            "retained_detail_records",
            health.retained_detail_records as u64,
        ),
        ("retained_detail_bytes", health.retained_detail_bytes as u64),
        ("metric.rejected_series", health.metric.rejected_series),
        ("metric.invalid_labels", health.metric.invalid_labels),
        ("metric.arithmetic_errors", health.metric.arithmetic_errors),
        (
            "metric.omitted_measurements",
            health.metric.omitted_measurements,
        ),
        (
            "metric.invalid_measurements",
            health.metric.invalid_measurements,
        ),
        ("metric.imported_ignored", health.metric.imported_ignored),
        ("metric.snapshots_evicted", health.metric.snapshots_evicted),
        (
            "metric.snapshot_rejections",
            health.metric.snapshot_rejections,
        ),
    ] {
        fields.put(format!("telemetry.{key}"), number(value))?;
    }
    fields.put(
        "telemetry.unobserved_native_debugger_interference_possible",
        boolean(health.unobserved_native_debugger_interference_possible),
    )?;
    Ok(())
}

/// A bounded byte window over serialization. It owns only the requested chunk;
/// all other bytes are counted/discarded, never cloned into a full trace buffer.
struct TraceWindow {
    offset: u64,
    end: u64,
    position: u64,
    bytes: Vec<u8>,
}
impl TraceWindow {
    fn new(offset: u64, limit: usize) -> Self {
        Self {
            offset,
            end: offset + limit as u64,
            position: 0,
            bytes: Vec::with_capacity(limit),
        }
    }
}
impl Write for TraceWindow {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let end = self
            .position
            .checked_add(bytes.len() as u64)
            .ok_or_else(|| io::Error::other("trace size overflow"))?;
        let start = self.position.max(self.offset);
        let finish = end.min(self.end);
        if start < finish {
            self.bytes.extend_from_slice(
                &bytes[(start - self.position) as usize..(finish - self.position) as usize],
            );
        }
        self.position = end;
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

fn profile_bytes(profile: &ProbeProfile) -> usize {
    let selected = &profile.selected;
    let subscription = &selected.subscription;
    let mut bytes = std::mem::size_of::<ProbeProfile>()
        .saturating_add(profile.label.capacity())
        .saturating_add(subscription.site.capacity())
        .saturating_add(
            subscription
                .path
                .capacity()
                .saturating_mul(std::mem::size_of::<PathSegment>()),
        );
    for segment in &subscription.path {
        bytes = bytes.saturating_add(match segment {
            PathSegment::Field(value)
            | PathSegment::Variant(value)
            | PathSegment::MapKey(MapKey::String(value)) => value.capacity(),
            PathSegment::MapKey(MapKey::Integer { decimal, .. }) => decimal.capacity(),
            _ => 0,
        });
    }
    for value in [
        &selected.selector.model,
        &selected.selector.machine,
        &selected.selector.entity,
        &selected.selector.correlation,
        &selected.selector.input_variant,
        &selected.selector.output_variant,
        &selected.selector.property,
        &selected.selector.effect_kind,
        &selected.selector.source_site,
    ]
    .into_iter()
    .flatten()
    {
        bytes = bytes.saturating_add(value.capacity());
    }
    if let crate::diagnostic::Trigger::Equals(
        Scalar::Integer { decimal, .. } | Scalar::Float { decimal, .. },
    ) = &subscription.trigger
    {
        bytes = bytes.saturating_add(decimal.capacity());
    }
    bytes
}
fn diagnostic_error(error: DiagnosticError) -> ProtocolError {
    match error {
        DiagnosticError::Capacity => ProtocolError::Limit,
        DiagnosticError::StaleRevision => ProtocolError::StaleConfiguration,
        DiagnosticError::Unauthorized => ProtocolError::Unauthorized,
        DiagnosticError::Exhausted => ProtocolError::Exhausted,
        DiagnosticError::PendingConfiguration => ProtocolError::Busy,
        _ => ProtocolError::InvalidInput,
    }
}
fn append_producer_status(
    fields: &mut Fields,
    hub: &DiagnosticHub,
    offset: usize,
    limit: usize,
) -> Result<(), ProtocolError> {
    let producers = hub.producer_status();
    let pending = hub.pending_producers();
    fields.put("probes.producers.total", number(producers.len() as u64))?;
    fields.put(
        "probes.producers.truncated",
        boolean(offset.saturating_add(limit) < producers.len()),
    )?;
    fields.put(
        "probes.pending_producers.total",
        number(pending.len() as u64),
    )?;
    for (index, producer) in producers.iter().skip(offset).take(limit).enumerate() {
        let key = format!("probes.producers.{index}");
        fields.put(format!("{key}.id"), number(producer.producer))?;
        fields.put(format!("{key}.epoch"), number(producer.epoch))?;
        fields.put(
            format!("{key}.capture_revision"),
            number(producer.capture_revision),
        )?;
        fields.put(
            format!("{key}.effective_transition"),
            number(producer.effective_transition),
        )?;
        fields.put(
            format!("{key}.pending"),
            boolean(pending.contains(&producer.producer)),
        )?;
    }
    Ok(())
}
fn append_probe_health(
    fields: &mut Fields,
    prefix: &str,
    health: &crate::diagnostic::SinkHealth,
) -> Result<(), ProtocolError> {
    for (key, value) in [
        ("captured", health.captured),
        ("filtered", health.filtered),
        ("sampled", health.sampled),
        ("dropped", health.dropped),
        ("truncated", health.truncated),
        ("exporter_failed", health.exporter_failed),
        ("abandoned", health.abandoned),
        ("inspection_failed", health.inspection_failed),
        ("baseline_evicted", health.baseline_evicted),
        ("observation_gaps", health.observation_gaps),
    ] {
        fields.put(format!("{prefix}.health.{key}"), number(value))?;
    }
    Ok(())
}
fn append_probe_event(
    fields: &mut Fields,
    prefix: &str,
    event: &DiagnosticEvent,
    include_payload: bool,
) -> Result<(), ProtocolError> {
    fields.put(
        format!("{prefix}.schema_version"),
        number(u64::from(event.schema_version)),
    )?;
    fields.put(format!("{prefix}.run"), number(event.run_id))?;
    fields.put(format!("{prefix}.producer"), number(event.producer))?;
    fields.put(format!("{prefix}.epoch"), number(event.epoch))?;
    fields.put(
        format!("{prefix}.producer_sequence"),
        number(event.producer_sequence),
    )?;
    fields.put(
        format!("{prefix}.capture_revision"),
        number(event.capture_revision),
    )?;
    fields.put(format!("{prefix}.transition"), number(event.transition))?;
    fields.put(
        format!("{prefix}.origin"),
        format!("enum:{:?}", event.origin),
    )?;
    fields.put(
        format!("{prefix}.severity"),
        format!("enum:{:?}", event.severity),
    )?;

    fields.put(format!("{prefix}.kind"), format!("enum:{:?}", event.kind))?;
    fields.put(format!("{prefix}.baseline"), boolean(event.baseline))?;
    fields.put(
        format!("{prefix}.baseline_reason"),
        event
            .baseline_reason
            .map_or("none".into(), |reason| format!("enum:{reason:?}")),
    )?;
    fields.put(
        format!("{prefix}.change_unknown"),
        boolean(event.change_unknown),
    )?;
    fields.put(
        format!("{prefix}.incomplete_turn"),
        boolean(event.incomplete_turn),
    )?;
    if include_payload {
        fields.put(format!("{prefix}.site"), format!("string:{}", event.site))?;
        for (index, segment) in event.path.iter().enumerate() {
            fields.put(format!("{prefix}.path.{index}"), encode_segment(segment))?;
        }
        if let Some((name, version)) = &event.display_schema {
            if name.len().saturating_add(7) > fields.max_field_bytes {
                return Err(ProtocolError::Limit);
            }
            fields.put(format!("{prefix}.display_schema"), format!("string:{name}"))?;
            fields.put(
                format!("{prefix}.display_schema_version"),
                number(u64::from(*version)),
            )?;
        }
    }
    if !include_payload {
        fields.put(format!("{prefix}.metadata_omitted"), "bool:true")?;
        fields.put(format!("{prefix}.payload"), "omitted:by_request")?;
    } else if let Some(payload) = &event.payload {
        append_node(fields, &format!("{prefix}.payload"), payload, 0)?;
    } else {
        fields.put(format!("{prefix}.payload"), "none")?;
    }
    Ok(())
}

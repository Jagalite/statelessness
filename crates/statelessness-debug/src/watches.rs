//! Runtime-selective, bounded diagnostic watches. No reducer or exact recorder is
//! invoked here. One registry belongs to one ordered producer; remote/multi-producer
//! acknowledgement must be performed by the host, not inferred from this local ack.
use crate::inspect::{
    self, Completeness, DiffKind, DisplaySchema, Inspect, InspectError, InspectLimits, InspectNode,
    InspectQuery, PageRequest, PathSegment, Scalar, SnapshotId, SourceSite,
};
use std::collections::{BTreeMap, VecDeque};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OriginKind {
    /// Configuration metadata, not a modeled/runtime observation.
    DiagnosticControl,
    RecordedLive,
    ExactReplay,
    Simulation,
    OutOfDomainInjection,
    CounterfactualRestore,
    EditedStateExperiment,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WatchSelector {
    pub path: Vec<PathSegment>,
    pub schema_version: u32,
    pub model: Option<String>,
    pub machine: Option<String>,
    pub input_variant: Option<String>,
    pub output_variant: Option<String>,
    pub origin: Option<OriginKind>,
}
impl WatchSelector {
    pub fn path(path: Vec<PathSegment>, schema_version: u32) -> Self {
        Self {
            path,
            schema_version,
            model: None,
            machine: None,
            input_variant: None,
            output_variant: None,
            origin: None,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum WatchTrigger {
    Changed,
    EveryObservation,
    Equals(Scalar),
    PropertyFailure,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WatchConfig {
    pub selector: WatchSelector,
    pub trigger: WatchTrigger,
    pub enabled: bool,
    /// Evaluate every N selected boundaries. N > 1 yields sampled-change labels.
    pub evaluation_interval: u64,
    /// Sample delivery only after evaluation; this never invalidates the baseline.
    pub delivery_interval: u64,
    /// Sequence-based TTL, with no diagnostic clock reads in the model path.
    pub baseline_ttl_sequences: Option<u64>,
    /// Expire after this many selected observations. None means no expiry.
    pub ttl_observations: Option<u64>,
    pub limits: InspectLimits,
    pub page: PageRequest,
}
impl WatchConfig {
    pub fn changed(path: Vec<PathSegment>, schema_version: u32) -> Self {
        Self {
            selector: WatchSelector::path(path, schema_version),
            trigger: WatchTrigger::Changed,
            enabled: true,
            evaluation_interval: 1,
            delivery_interval: 1,
            baseline_ttl_sequences: None,
            ttl_observations: None,
            limits: InspectLimits {
                max_bytes: 32 * 1024,
                ..InspectLimits::default()
            },
            page: PageRequest::default(),
        }
    }
}
#[derive(Clone, Debug)]
pub struct WatchAuthorization {
    allowed_paths: Vec<Vec<PathSegment>>,
}
impl WatchAuthorization {
    pub fn deny_all() -> Self {
        Self {
            allowed_paths: vec![],
        }
    }
    pub fn allow_paths(allowed_paths: Vec<Vec<PathSegment>>) -> Self {
        Self { allowed_paths }
    }
    /// Explicit local authority; do not use this for untrusted remote consumers.
    pub fn allow_all() -> Self {
        Self {
            allowed_paths: vec![vec![]],
        }
    }
    pub fn allows(&self, path: &[PathSegment]) -> bool {
        self.allowed_paths
            .iter()
            .any(|prefix| path.starts_with(prefix))
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct WatchLimits {
    pub max_watches: usize,
    pub max_ring_records: usize,
    pub max_ring_bytes: usize,
    pub max_event_bytes: usize,
    pub max_baseline_bytes: usize,
    pub max_config_bytes: usize,
}
impl Default for WatchLimits {
    fn default() -> Self {
        Self {
            max_watches: 64,
            max_ring_records: 1000,
            max_ring_bytes: 4 * 1024 * 1024,
            max_event_bytes: 32 * 1024,
            max_baseline_bytes: 8 * 1024 * 1024,
            max_config_bytes: 4096,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct WatchAck {
    pub watch_id: u64,
    pub generation: u64,
    pub capture_revision: u64,
    pub effective_sequence: u64,
    pub display_schema: Option<DisplaySchema>,
    pub resolved_type: Option<&'static str>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum WatchError {
    Capacity,
    Unauthorized,
    InvalidConfig,
    SchemaMismatch { expected: u32, actual: u32 },
    UnknownWatch,
    RevisionExhausted,
    Inspection(InspectError),
    RedactedPath,
}
impl std::fmt::Display for WatchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "watch error: {self:?}")
    }
}
impl std::error::Error for WatchError {}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BaselineReason {
    Initial,
    Reenabled,
    ObservationGap,
    Expired,
    Evicted,
    SourceChanged,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum WatchState {
    Active,
    Paused,
    Expired,
    Disabled,
    Removed,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ObservationPhase {
    Initial,
    PostTurn,
    Control,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum WatchEventKind {
    WatchEnabled,
    WatchDisabled,
    WatchPaused,
    WatchExpired,
    WatchRemoved,
    EventsDropped { count: u64 },
    ObservationGap,
    SchemaChanged,
    Baseline(BaselineReason),
    Changed,
    Unchanged,
    ChangeSinceLastSample { changed: bool },
    Unknown,
    PropertyFailure,
    ValueMatched,
    InspectorError(InspectError),
    Snapshot,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WatchEvent {
    pub schema_version: u32,
    pub display_schema_version: u32,
    pub watch_id: u64,
    pub generation: u64,
    pub capture_revision: u64,
    pub snapshot: SnapshotId,
    pub origin: OriginKind,
    pub kind: WatchEventKind,
    pub completeness: Completeness,
    pub value: Option<InspectNode>,
    pub path: Vec<PathSegment>,
    pub schema_name: Option<String>,
    pub source: Option<SourceSite>,
    pub phase: ObservationPhase,
    pub model: Option<String>,
    pub machine: Option<String>,
    pub previous_snapshot: Option<SnapshotId>,
    pub difference: DiffKind,
    pub property_failure: bool,
    /// Bounded, host-supplied IDs; failure details are deliberately not captured.
    pub failing_properties: Vec<String>,
    pub trigger: WatchTrigger,
}
impl WatchEvent {
    pub fn retained_bytes(&self) -> usize {
        std::mem::size_of::<Self>()
            .saturating_add(self.value.as_ref().map_or(0, InspectNode::retained_bytes))
            .saturating_add(path_bytes(&self.path))
            .saturating_add(self.schema_name.as_ref().map_or(0, String::len))
            .saturating_add(self.model.as_ref().map_or(0, String::len))
            .saturating_add(self.machine.as_ref().map_or(0, String::len))
            .saturating_add(self.failing_properties.iter().fold(0usize, |n, p| {
                n.saturating_add(std::mem::size_of::<String>())
                    .saturating_add(p.len())
            }))
            .saturating_add(trigger_bytes(&self.trigger))
    }
}
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct WatchHealth {
    pub evaluated: u64,
    pub filtered: u64,
    pub evaluation_sampled: u64,
    pub delivery_sampled: u64,
    pub dropped_events: u64,
    pub observation_gaps: u64,
    pub baseline_evictions: u64,
    pub inspection_errors: u64,
    pub truncated: u64,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WatchStatus {
    pub id: u64,
    pub generation: u64,
    pub enabled: bool,
    pub state: WatchState,
    pub validated_schema: Option<DisplaySchema>,
    pub resolved_type: Option<&'static str>,
    pub effective_sequence: u64,
    pub config: WatchConfig,
    pub has_baseline: bool,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WatchSnapshot {
    pub capture_revision: u64,
    pub at: Option<SnapshotId>,
    pub events: Vec<WatchEvent>,
    pub retained_bytes: usize,
    pub health: WatchHealth,
}
#[derive(Clone, Copy, Debug)]
pub struct ObservationContext<'a> {
    pub snapshot: SnapshotId,
    pub origin: OriginKind,
    pub model: Option<&'a str>,
    pub machine: Option<&'a str>,
    pub input_variant: Option<&'a str>,
    pub output_variants: &'a [&'a str],
    pub property_failure: bool,
    pub failing_properties: &'a [&'a str],
}
impl<'a> ObservationContext<'a> {
    pub fn new(snapshot: SnapshotId, origin: OriginKind) -> Self {
        Self {
            snapshot,
            origin,
            model: None,
            machine: None,
            input_variant: None,
            output_variants: &[],
            property_failure: false,
            failing_properties: &[],
        }
    }
}
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ObservationSummary {
    pub evaluated: usize,
    pub emitted: usize,
    pub stale: bool,
    pub observation_gap: bool,
    pub inspection_errors: usize,
}
struct Entry {
    id: u64,
    generation: u64,
    effective_sequence: u64,
    config: WatchConfig,
    state: WatchState,
    validated_schema: Option<DisplaySchema>,
    resolved_type: Option<&'static str>,
    current: Option<WatchEvent>,
    reset: Option<BaselineReason>,
    selected: u64,
    emissions: u64,
    sampled: bool,
}
pub struct WatchRegistry {
    limits: WatchLimits,
    authorization: WatchAuthorization,
    schema_version: u32,
    entries: BTreeMap<u64, Entry>,
    next_id: u64,
    capture_revision: u64,
    last: Option<SnapshotId>,
    ring: VecDeque<WatchEvent>,
    ring_bytes: usize,
    health: WatchHealth,
    failure: Option<WatchSnapshot>,
    reported_drops: u64,
}
impl WatchRegistry {
    pub fn new(limits: WatchLimits, authorization: WatchAuthorization) -> Self {
        Self::new_for_schema(limits, authorization, inspect::DISPLAY_SCHEMA_VERSION)
    }
    pub fn new_for_schema(
        limits: WatchLimits,
        authorization: WatchAuthorization,
        schema_version: u32,
    ) -> Self {
        Self {
            limits,
            authorization,
            schema_version,
            entries: BTreeMap::new(),
            next_id: 1,
            capture_revision: 0,
            last: None,
            ring: VecDeque::new(),
            ring_bytes: 0,
            health: WatchHealth::default(),
            failure: None,
            reported_drops: 0,
        }
    }
    pub fn limits(&self) -> WatchLimits {
        self.limits
    }
    pub fn capture_revision(&self) -> u64 {
        self.capture_revision
    }
    pub fn health(&self) -> &WatchHealth {
        &self.health
    }
    pub fn interested(&self) -> bool {
        self.entries.values().any(|e| e.state == WatchState::Active)
    }
    fn boundary(&self) -> u64 {
        self.last.map_or(0, |s| s.sequence.saturating_add(1))
    }
    fn revision(&mut self) -> Result<u64, WatchError> {
        if self
            .last
            .is_some_and(|snapshot| snapshot.sequence == u64::MAX)
        {
            return Err(WatchError::RevisionExhausted);
        }
        self.capture_revision = self
            .capture_revision
            .checked_add(1)
            .ok_or(WatchError::RevisionExhausted)?;
        Ok(self.capture_revision)
    }
    fn validate(&self, c: &WatchConfig) -> Result<(), WatchError> {
        if !self.authorization.allows(&c.selector.path) {
            return Err(WatchError::Unauthorized);
        }
        if c.selector.schema_version != self.schema_version {
            return Err(WatchError::SchemaMismatch {
                expected: self.schema_version,
                actual: c.selector.schema_version,
            });
        }
        if c.evaluation_interval == 0
            || c.delivery_interval == 0
            || c.ttl_observations == Some(0)
            || c.page.limit == 0
            || c.page.limit > c.limits.max_page_size
            || c.limits.max_depth > 32
            || c.selector.path.len() > c.limits.max_depth
            || c.limits.max_bytes == 0
            || c.limits.max_bytes > self.limits.max_event_bytes
            || config_bytes(c) > self.limits.max_config_bytes
        {
            return Err(WatchError::InvalidConfig);
        }
        Ok(())
    }
    /// Admit a local provisional watch. Unknown paths become explicit errors on observation.
    /// Remote callers must use `add_validated` instead.
    pub fn add(&mut self, config: WatchConfig) -> Result<WatchAck, WatchError> {
        self.add_prepared(config, None, None)
    }
    fn add_prepared(
        &mut self,
        config: WatchConfig,
        schema: Option<DisplaySchema>,
        resolved_type: Option<&'static str>,
    ) -> Result<WatchAck, WatchError> {
        self.validate(&config)?;
        if self.entries.len() >= self.limits.max_watches {
            return Err(WatchError::Capacity);
        }
        let id = self.next_id;
        let next = id.checked_add(1).ok_or(WatchError::RevisionExhausted)?;
        let capture_revision = self.revision()?;
        self.next_id = next;
        let effective_sequence = self.boundary();
        let generation = 1;
        self.entries.insert(
            id,
            Entry {
                id,
                generation,
                effective_sequence,
                state: if config.enabled {
                    WatchState::Active
                } else {
                    WatchState::Disabled
                },
                validated_schema: schema,
                resolved_type,
                config,
                current: None,
                reset: Some(BaselineReason::Initial),
                selected: 0,
                emissions: 0,
                sampled: false,
            },
        );
        let event_kind = if self.entries[&id].config.enabled {
            WatchEventKind::WatchEnabled
        } else {
            WatchEventKind::WatchDisabled
        };
        self.lifecycle(id, event_kind);
        Ok(WatchAck {
            watch_id: id,
            generation,
            capture_revision,
            effective_sequence,
            display_schema: self.entries.get(&id).and_then(|e| e.validated_schema),
            resolved_type: self.entries.get(&id).and_then(|e| e.resolved_type),
        })
    }
    /// Validate schema/path under the same authorization before admitting a watch.
    /// No selected payload is retained or emitted during admission.
    pub fn add_validated(
        &mut self,
        value: &dyn Inspect,
        config: WatchConfig,
    ) -> Result<WatchAck, WatchError> {
        self.validate(&config)?;
        if self.entries.len() >= self.limits.max_watches {
            return Err(WatchError::Capacity);
        }
        let query = InspectQuery {
            snapshot: self.last.unwrap_or_default(),
            path: config.selector.path.clone(),
            schema_version: config.selector.schema_version,
            page: config.page,
            limits: config.limits,
        };
        let result = inspect::inspect(value, &query).map_err(WatchError::Inspection)?;
        if matches!(result.node.kind, inspect::NodeKind::Redacted) {
            return Err(WatchError::RedactedPath);
        }
        self.add_prepared(config, Some(result.schema), Some(node_type(&result.node)))
    }
    pub fn update_validated(
        &mut self,
        id: u64,
        value: &dyn Inspect,
        config: WatchConfig,
    ) -> Result<WatchAck, WatchError> {
        self.validate(&config)?;
        if !self.entries.contains_key(&id) {
            return Err(WatchError::UnknownWatch);
        }
        let query = InspectQuery {
            snapshot: self.last.unwrap_or_default(),
            path: config.selector.path.clone(),
            schema_version: config.selector.schema_version,
            page: config.page,
            limits: config.limits,
        };
        let result = inspect::inspect(value, &query).map_err(WatchError::Inspection)?;
        if matches!(result.node.kind, inspect::NodeKind::Redacted) {
            return Err(WatchError::RedactedPath);
        }
        self.update_prepared(
            id,
            config,
            Some(result.schema),
            Some(node_type(&result.node)),
        )
    }
    fn set_state(
        &mut self,
        id: u64,
        state: WatchState,
        event_kind: WatchEventKind,
    ) -> Result<WatchAck, WatchError> {
        let enabled = state == WatchState::Active;
        let e = self.entries.get(&id).ok_or(WatchError::UnknownWatch)?;
        let generation = e
            .generation
            .checked_add(1)
            .ok_or(WatchError::RevisionExhausted)?;
        let capture_revision = self.revision()?;
        let effective_sequence = self.boundary();
        let e = self.entries.get_mut(&id).expect("checked watch");
        e.generation = generation;
        e.effective_sequence = effective_sequence;
        e.config.enabled = enabled;
        e.state = state;
        e.current = None;
        e.reset = Some(BaselineReason::Reenabled);
        e.selected = 0;
        e.emissions = 0;
        e.sampled = false;
        self.lifecycle(id, event_kind);
        Ok(WatchAck {
            watch_id: id,
            generation,
            capture_revision,
            effective_sequence,
            display_schema: self.entries.get(&id).and_then(|e| e.validated_schema),
            resolved_type: self.entries.get(&id).and_then(|e| e.resolved_type),
        })
    }
    pub fn enable(&mut self, id: u64) -> Result<WatchAck, WatchError> {
        self.set_state(id, WatchState::Active, WatchEventKind::WatchEnabled)
    }
    /// Clears the current/baseline value. Previously emitted ring records remain
    /// until eviction; retain-last baseline behavior is intentionally unsupported.
    pub fn disable(&mut self, id: u64) -> Result<WatchAck, WatchError> {
        self.set_state(id, WatchState::Disabled, WatchEventKind::WatchDisabled)
    }
    pub fn pause(&mut self, id: u64) -> Result<WatchAck, WatchError> {
        self.set_state(id, WatchState::Paused, WatchEventKind::WatchPaused)
    }
    pub fn resume(&mut self, id: u64) -> Result<WatchAck, WatchError> {
        self.enable(id)
    }
    pub fn update(&mut self, id: u64, config: WatchConfig) -> Result<WatchAck, WatchError> {
        self.update_prepared(id, config, None, None)
    }
    fn update_prepared(
        &mut self,
        id: u64,
        config: WatchConfig,
        schema: Option<DisplaySchema>,
        resolved_type: Option<&'static str>,
    ) -> Result<WatchAck, WatchError> {
        self.validate(&config)?;
        let entry = self.entries.get(&id).ok_or(WatchError::UnknownWatch)?;
        if entry.generation == u64::MAX
            || self.capture_revision == u64::MAX
            || self
                .last
                .is_some_and(|snapshot| snapshot.sequence == u64::MAX)
        {
            return Err(WatchError::RevisionExhausted);
        }
        let enabled = config.enabled;
        let entry = self.entries.get_mut(&id).expect("known watch");
        entry.config = config;
        entry.validated_schema = schema;
        entry.resolved_type = resolved_type;
        if enabled {
            self.enable(id)
        } else {
            self.disable(id)
        }
    }
    pub fn remove(&mut self, id: u64) -> Result<WatchAck, WatchError> {
        let e = self.entries.get(&id).ok_or(WatchError::UnknownWatch)?;
        let generation = e
            .generation
            .checked_add(1)
            .ok_or(WatchError::RevisionExhausted)?;
        let capture_revision = self.revision()?;
        let effective_sequence = self.boundary();
        let e = self.entries.get_mut(&id).expect("known watch");
        e.state = WatchState::Removed;
        e.generation = generation;
        e.effective_sequence = effective_sequence;
        self.lifecycle(id, WatchEventKind::WatchRemoved);
        self.entries.remove(&id);
        Ok(WatchAck {
            watch_id: id,
            generation,
            capture_revision,
            effective_sequence,
            display_schema: self.entries.get(&id).and_then(|e| e.validated_schema),
            resolved_type: self.entries.get(&id).and_then(|e| e.resolved_type),
        })
    }
    pub fn list(&self) -> Vec<WatchStatus> {
        self.entries
            .values()
            .map(|e| WatchStatus {
                id: e.id,
                generation: e.generation,
                enabled: e.config.enabled,
                state: e.state,
                validated_schema: e.validated_schema,
                resolved_type: e.resolved_type,
                effective_sequence: e.effective_sequence,
                config: e.config.clone(),
                has_baseline: e
                    .current
                    .as_ref()
                    .is_some_and(|v| v.value.as_ref().is_some_and(InspectNode::is_complete))
                    && e.reset.is_none(),
            })
            .collect()
    }
    pub fn current(&self, id: u64) -> Option<&WatchEvent> {
        self.entries.get(&id).and_then(|e| e.current.as_ref())
    }
    pub fn snapshot(&self) -> WatchSnapshot {
        WatchSnapshot {
            capture_revision: self.capture_revision,
            at: self.last,
            events: self.ring.iter().cloned().collect(),
            retained_bytes: self.ring_bytes,
            health: self.health.clone(),
        }
    }
    /// Independent bounded copy of the diagnostic ring at the latest failure.
    /// It is supplementary projected evidence, never a replayable exact trace.
    pub fn failure_snapshot(&self) -> Option<&WatchSnapshot> {
        self.failure.as_ref()
    }
    /// Explicit safe-point projection. It does not advance observation order or
    /// alter the change baseline, and may be used for a paused/disabled watch.
    pub fn snapshot_now(
        &mut self,
        id: u64,
        value: &dyn Inspect,
        context: ObservationContext<'_>,
    ) -> Result<WatchEvent, WatchError> {
        let e = self.entries.get(&id).ok_or(WatchError::UnknownWatch)?;
        self.validate(&e.config)?;
        let schema = value.schema();
        if e.validated_schema
            .is_some_and(|known| known.name != schema.name || known.version != schema.version)
        {
            return Err(WatchError::SchemaMismatch {
                expected: e.config.selector.schema_version,
                actual: schema.version,
            });
        }
        let overhead = std::mem::size_of::<WatchEvent>()
            .saturating_add(path_bytes(&e.config.selector.path))
            .saturating_add(3072)
            .saturating_add(trigger_bytes(&e.config.trigger))
            .saturating_add(property_id_bytes(context.failing_properties));
        let query = InspectQuery {
            snapshot: context.snapshot,
            path: e.config.selector.path.clone(),
            schema_version: e.config.selector.schema_version,
            page: e.config.page,
            limits: InspectLimits {
                max_bytes: e
                    .config
                    .limits
                    .max_bytes
                    .min(self.limits.max_event_bytes.saturating_sub(overhead)),
                ..e.config.limits
            },
        };
        let result = inspect::inspect(value, &query).map_err(WatchError::Inspection)?;
        if matches!(result.node.kind, inspect::NodeKind::Redacted) {
            return Err(WatchError::RedactedPath);
        }
        let event = WatchEvent {
            schema_version: 1,
            display_schema_version: schema.version,
            watch_id: id,
            generation: e.generation,
            capture_revision: self.capture_revision,
            snapshot: context.snapshot,
            origin: context.origin,
            kind: WatchEventKind::Snapshot,
            completeness: result.node.completeness,
            value: Some(result.node),
            path: e.config.selector.path.clone(),
            schema_name: Some(bounded_metadata(schema.name)),
            source: schema.source,
            phase: ObservationPhase::Control,
            model: context.model.map(bounded_metadata),
            machine: context.machine.map(bounded_metadata),
            previous_snapshot: None,
            difference: DiffKind::Unknown,
            property_failure: context.property_failure,
            failing_properties: property_ids(context.failing_properties),
            trigger: e.config.trigger.clone(),
        };
        self.push(event.clone());
        Ok(event)
    }
    pub fn clear_failure_snapshot(&mut self) {
        self.failure = None;
    }
    pub fn clear_ring(&mut self) {
        self.ring.clear();
        self.ring_bytes = 0;
    }
    pub fn observation_gap(&mut self) {
        self.health.observation_gaps = self.health.observation_gaps.saturating_add(1);
        for e in self.entries.values_mut() {
            e.reset = Some(BaselineReason::ObservationGap);
            e.current = None;
        }
        let ids: Vec<u64> = self.entries.keys().copied().collect();
        for id in ids {
            self.lifecycle(id, WatchEventKind::ObservationGap);
        }
    }
    pub fn observe(
        &mut self,
        value: &dyn Inspect,
        context: ObservationContext<'_>,
    ) -> ObservationSummary {
        let mut summary = ObservationSummary::default();
        if !self.interested() {
            if self.last.is_some_and(|last| {
                context.snapshot.session == last.session
                    && context.snapshot.sequence <= last.sequence
            }) {
                summary.stale = true;
                return summary;
            }
            self.last = Some(context.snapshot);
            return summary;
        }
        if let Some(last) = self.last {
            if context.snapshot.session == last.session
                && context.snapshot.sequence <= last.sequence
            {
                summary.stale = true;
                return summary;
            }
            if context.snapshot.session != last.session
                || context.snapshot.sequence != last.sequence.saturating_add(1)
            {
                self.observation_gap();
                summary.observation_gap = true;
            }
        }
        self.last = Some(context.snapshot);
        // Crucially, schema(), projection callbacks and payload allocation are all
        // skipped when no enabled consumer is interested.
        if !self.interested() {
            return summary;
        }
        // Equal queries are evaluated once and shared only after authorization.
        // Cache lifetime is one boundary; its cardinality is bounded by watches.
        let mut projections: Vec<(InspectQuery, Result<InspectNode, InspectError>)> = Vec::new();
        let ids: Vec<u64> = self.entries.keys().copied().collect();
        for id in ids {
            let e = self.entries.get_mut(&id).expect("registered watch");
            if e.state != WatchState::Active {
                continue;
            }
            if e.config
                .ttl_observations
                .is_some_and(|ttl| e.selected >= ttl)
            {
                e.state = WatchState::Expired;
                e.config.enabled = false;
                e.current = None;
                self.lifecycle(id, WatchEventKind::WatchExpired);
                continue;
            }
            if !selector_matches(&e.config.selector, &context) {
                self.health.filtered = self.health.filtered.saturating_add(1);
                continue;
            }
            let actual_schema = value.schema();
            if e.validated_schema.is_some_and(|schema| {
                schema.name != actual_schema.name || schema.version != actual_schema.version
            }) {
                e.state = WatchState::Paused;
                e.config.enabled = false;
                e.current = None;
                e.reset = Some(BaselineReason::ObservationGap);
                self.lifecycle(id, WatchEventKind::SchemaChanged);
                continue;
            }
            if actual_schema.version != self.schema_version {
                e.state = WatchState::Paused;
                e.config.enabled = false;
                e.current = None;
                self.lifecycle(id, WatchEventKind::SchemaChanged);
                continue;
            }
            e.validated_schema = Some(actual_schema);
            if e.current.as_ref().is_some_and(|previous| {
                previous.model.as_deref() != context.model
                    || previous.machine.as_deref() != context.machine
                    || previous.origin != context.origin
            }) {
                e.reset = Some(BaselineReason::SourceChanged);
            }
            e.selected = e.selected.saturating_add(1);
            if !context.property_failure
                && !(e.selected - 1).is_multiple_of(e.config.evaluation_interval)
            {
                e.sampled = true;
                self.health.evaluation_sampled = self.health.evaluation_sampled.saturating_add(1);
                if e.config
                    .ttl_observations
                    .is_some_and(|ttl| e.selected >= ttl)
                {
                    e.state = WatchState::Expired;
                    e.config.enabled = false;
                    self.lifecycle(id, WatchEventKind::WatchExpired);
                }
                continue;
            }
            if e.config.baseline_ttl_sequences.is_some_and(|ttl| {
                e.current.as_ref().is_some_and(|v| {
                    context
                        .snapshot
                        .sequence
                        .saturating_sub(v.snapshot.sequence)
                        > ttl
                })
            }) {
                e.reset = Some(BaselineReason::Expired);
            }
            let query = InspectQuery {
                snapshot: context.snapshot,
                path: e.config.selector.path.clone(),
                schema_version: e.config.selector.schema_version,
                page: e.config.page,
                limits: InspectLimits {
                    max_bytes: e.config.limits.max_bytes.min(
                        self.limits
                            .max_event_bytes
                            .saturating_sub(std::mem::size_of::<WatchEvent>())
                            .saturating_sub(path_bytes(&e.config.selector.path))
                            .saturating_sub(actual_schema.name.len().min(1024))
                            .saturating_sub(context.model.map_or(0, |v| v.len().min(1024)))
                            .saturating_sub(context.machine.map_or(0, |v| v.len().min(1024)))
                            .saturating_sub(trigger_bytes(&e.config.trigger))
                            .saturating_sub(property_id_bytes(context.failing_properties)),
                    ),
                    ..e.config.limits
                },
            };
            let result = if let Some((_, result)) = projections.iter().find(|(q, _)| q == &query) {
                result.clone()
            } else {
                let result = inspect::inspect(value, &query).map(|r| r.node);
                // One bounded result per distinct query. The transient cache is
                // independently bounded by max_watches * max_event_bytes; baseline
                // eviction must never cause duplicate accessor evaluation.
                projections.push((query, result.clone()));
                result
            };
            summary.evaluated += 1;
            self.health.evaluated = self.health.evaluated.saturating_add(1);
            let (kind, node, completeness) = match result {
                Ok(node) => {
                    e.resolved_type = Some(node_type(&node));
                    let complete = node.is_complete();
                    let comparison = inspect::compare_nodes(
                        e.current.as_ref().and_then(|v| v.value.as_ref()),
                        Some(&node),
                    );
                    let kind = if !complete {
                        WatchEventKind::Unknown
                    } else if let Some(reason) = e.reset.take() {
                        WatchEventKind::Baseline(reason)
                    } else if e
                        .current
                        .as_ref()
                        .is_none_or(|v| v.value.as_ref().is_none_or(|n| !n.is_complete()))
                    {
                        WatchEventKind::Baseline(BaselineReason::ObservationGap)
                    } else if e.sampled {
                        WatchEventKind::ChangeSinceLastSample {
                            changed: comparison == DiffKind::Changed,
                        }
                    } else if comparison == DiffKind::Changed {
                        WatchEventKind::Changed
                    } else if comparison == DiffKind::Unchanged {
                        WatchEventKind::Unchanged
                    } else {
                        WatchEventKind::Unknown
                    };
                    if !complete {
                        self.health.truncated = self.health.truncated.saturating_add(1);
                        e.reset = Some(BaselineReason::ObservationGap);
                    }
                    let completeness = node.completeness;
                    (kind, Some(node), completeness)
                }
                Err(error) => {
                    summary.inspection_errors += 1;
                    self.health.inspection_errors = self.health.inspection_errors.saturating_add(1);
                    e.reset = Some(BaselineReason::ObservationGap);
                    (
                        WatchEventKind::InspectorError(error),
                        None,
                        Completeness::Partial(inspect::IncompleteReason::Unavailable),
                    )
                }
            };
            e.sampled = false;
            let should_emit = match &e.config.trigger {
                WatchTrigger::EveryObservation => true,
                WatchTrigger::Changed => !matches!(kind, WatchEventKind::Unchanged),
                WatchTrigger::PropertyFailure => context.property_failure,
                WatchTrigger::Equals(expected) => node.as_ref().is_some_and(|n| {
                    n.is_complete()
                        && matches!(&n.kind,inspect::NodeKind::Scalar(actual)if actual==expected)
                }),
            } || context.property_failure;
            // Trigger labels do not change comparison provenance. Preserve the
            // baseline/gap/sample classification before relabeling the event.
            let difference = if matches!(
                kind,
                WatchEventKind::ChangeSinceLastSample { .. }
                    | WatchEventKind::Unknown
                    | WatchEventKind::Baseline(_)
                    | WatchEventKind::InspectorError(_)
            ) {
                DiffKind::Unknown
            } else {
                inspect::compare_nodes(
                    e.current.as_ref().and_then(|v| v.value.as_ref()),
                    node.as_ref(),
                )
            };
            let completeness = if matches!(kind, WatchEventKind::ChangeSinceLastSample { .. }) {
                Completeness::Partial(inspect::IncompleteReason::Sampled)
            } else {
                completeness
            };
            let kind =
                if context.property_failure && !matches!(kind, WatchEventKind::InspectorError(_)) {
                    WatchEventKind::PropertyFailure
                } else if should_emit && matches!(e.config.trigger, WatchTrigger::Equals(_)) {
                    WatchEventKind::ValueMatched
                } else {
                    kind
                };
            let completeness = if !metadata_complete(actual_schema.name, &context)
                && completeness == Completeness::Complete
            {
                Completeness::Partial(inspect::IncompleteReason::ValueLimit)
            } else {
                completeness
            };
            let event = WatchEvent {
                schema_version: 1,
                display_schema_version: self.schema_version,
                watch_id: id,
                generation: e.generation,
                capture_revision: self.capture_revision,
                snapshot: context.snapshot,
                origin: context.origin,
                kind,
                completeness,
                value: node,
                path: e.config.selector.path.clone(),
                schema_name: Some(bounded_metadata(actual_schema.name)),
                source: actual_schema.source,
                phase: if context.snapshot.sequence == 0 {
                    ObservationPhase::Initial
                } else {
                    ObservationPhase::PostTurn
                },
                model: context.model.map(bounded_metadata),
                machine: context.machine.map(bounded_metadata),
                previous_snapshot: e.current.as_ref().map(|v| v.snapshot),
                difference,
                property_failure: context.property_failure,
                failing_properties: property_ids(context.failing_properties),
                trigger: e.config.trigger.clone(),
            };
            e.current = Some(event.clone());
            if should_emit {
                e.emissions = e.emissions.saturating_add(1);
                if context.property_failure
                    || (e.emissions - 1).is_multiple_of(e.config.delivery_interval)
                {
                    if self.push(event) {
                        summary.emitted += 1;
                    }
                } else {
                    self.health.delivery_sampled = self.health.delivery_sampled.saturating_add(1);
                }
            }
            self.enforce_baselines();
            let e = self.entries.get_mut(&id).expect("known watch");
            if e.config
                .ttl_observations
                .is_some_and(|ttl| e.selected >= ttl)
            {
                e.state = WatchState::Expired;
                e.config.enabled = false;
                self.lifecycle(id, WatchEventKind::WatchExpired);
            }
        }
        self.report_drops();
        if context.property_failure {
            self.failure = Some(self.snapshot());
        }
        summary
    }
    fn lifecycle(&mut self, id: u64, kind: WatchEventKind) {
        let Some(e) = self.entries.get(&id) else {
            return;
        };
        let event = WatchEvent {
            schema_version: 1,
            display_schema_version: self.schema_version,
            watch_id: id,
            generation: e.generation,
            capture_revision: self.capture_revision,
            snapshot: self.last.unwrap_or_default(),
            origin: OriginKind::DiagnosticControl,
            kind,
            completeness: Completeness::Complete,
            value: None,
            path: e.config.selector.path.clone(),
            schema_name: e.validated_schema.map(|s| bounded_metadata(s.name)),
            source: e.validated_schema.and_then(|s| s.source),
            phase: ObservationPhase::Control,
            model: e.config.selector.model.clone(),
            machine: e.config.selector.machine.clone(),
            previous_snapshot: None,
            difference: DiffKind::Unknown,
            property_failure: false,
            failing_properties: vec![],
            trigger: e.config.trigger.clone(),
        };
        self.push(event);
    }
    /// Drop health lives outside the ring. A best-effort marker is appended only
    /// when it fits without causing another eviction, so saturation cannot recurse.
    fn report_drops(&mut self) {
        if self.health.dropped_events == self.reported_drops {
            return;
        }
        let event = WatchEvent {
            schema_version: 1,
            display_schema_version: self.schema_version,
            watch_id: 0,
            generation: 0,
            capture_revision: self.capture_revision,
            snapshot: self.last.unwrap_or_default(),
            origin: OriginKind::DiagnosticControl,
            kind: WatchEventKind::EventsDropped {
                count: self.health.dropped_events - self.reported_drops,
            },
            completeness: Completeness::Partial(inspect::IncompleteReason::ObservationGap),
            value: None,
            path: vec![],
            schema_name: None,
            source: None,
            phase: ObservationPhase::Control,
            model: None,
            machine: None,
            previous_snapshot: None,
            difference: DiffKind::Unknown,
            property_failure: false,
            failing_properties: vec![],
            trigger: WatchTrigger::EveryObservation,
        };
        let bytes = event.retained_bytes();
        if self.ring.len() < self.limits.max_ring_records
            && self.ring_bytes.saturating_add(bytes) <= self.limits.max_ring_bytes
            && bytes <= self.limits.max_event_bytes
        {
            self.ring_bytes += bytes;
            self.ring.push_back(event);
            self.reported_drops = self.health.dropped_events;
        }
    }
    fn push(&mut self, event: WatchEvent) -> bool {
        let bytes = event.retained_bytes();
        if self.limits.max_ring_records == 0
            || bytes > self.limits.max_ring_bytes
            || bytes > self.limits.max_event_bytes
        {
            self.health.dropped_events = self.health.dropped_events.saturating_add(1);
            return false;
        }
        while self.ring.len() >= self.limits.max_ring_records
            || self.ring_bytes.saturating_add(bytes) > self.limits.max_ring_bytes
        {
            if let Some(old) = self.ring.pop_front() {
                self.ring_bytes = self.ring_bytes.saturating_sub(old.retained_bytes());
                self.health.dropped_events = self.health.dropped_events.saturating_add(1);
            } else {
                break;
            }
        }
        self.ring_bytes += bytes;
        self.ring.push_back(event);
        true
    }
    fn enforce_baselines(&mut self) {
        let mut bytes = self
            .entries
            .values()
            .filter_map(|e| e.current.as_ref())
            .fold(0usize, |n, e| n.saturating_add(e.retained_bytes()));
        while bytes > self.limits.max_baseline_bytes {
            let oldest = self
                .entries
                .iter()
                .filter_map(|(id, e)| e.current.as_ref().map(|v| (*id, v.snapshot.sequence)))
                .min_by_key(|(_, seq)| *seq)
                .map(|(id, _)| id);
            let Some(id) = oldest else {
                break;
            };
            let e = self.entries.get_mut(&id).expect("selected baseline");
            let old = e.current.take().expect("selected baseline");
            bytes = bytes.saturating_sub(old.retained_bytes());
            e.reset = Some(BaselineReason::Evicted);
            self.health.baseline_evictions = self.health.baseline_evictions.saturating_add(1);
        }
    }
}
fn selector_matches(s: &WatchSelector, c: &ObservationContext<'_>) -> bool {
    s.model.as_deref().is_none_or(|v| Some(v) == c.model)
        && s.machine.as_deref().is_none_or(|v| Some(v) == c.machine)
        && s.input_variant
            .as_deref()
            .is_none_or(|v| Some(v) == c.input_variant)
        && s.output_variant
            .as_deref()
            .is_none_or(|v| c.output_variants.contains(&v))
        && s.origin.is_none_or(|v| v == c.origin)
}
fn config_bytes(c: &WatchConfig) -> usize {
    let mut n = std::mem::size_of::<WatchConfig>();
    for p in &c.selector.path {
        n = n
            .saturating_add(std::mem::size_of::<PathSegment>())
            .saturating_add(match p {
                PathSegment::Field(s)
                | PathSegment::Variant(s)
                | PathSegment::MapKey(inspect::MapKey::String(s)) => s.len(),
                PathSegment::MapKey(inspect::MapKey::Integer { decimal, .. }) => decimal.len(),
                _ => 0,
            });
    }
    for s in [
        &c.selector.model,
        &c.selector.machine,
        &c.selector.input_variant,
        &c.selector.output_variant,
    ]
    .into_iter()
    .flatten()
    {
        n = n.saturating_add(s.len());
    }
    if let WatchTrigger::Equals(Scalar::Integer { decimal, .. } | Scalar::Float { decimal, .. }) =
        &c.trigger
    {
        n = n.saturating_add(decimal.len());
    }
    n
}

/// Post-turn marker classification only. The callback runs after a completed turn
/// and must not be application behavior. Function-local probes are separate host
/// integration, not automatic access to hidden reducer locals.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DiagnosticMarker {
    pub id: String,
    pub detail: String,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MarkerBatch {
    pub markers: Vec<DiagnosticMarker>,
    pub dropped: u64,
    pub truncated: u64,
    pub retained_bytes: usize,
}
pub struct MarkerCollector {
    enabled: bool,
    max_records: usize,
    max_bytes: usize,
    batch: MarkerBatch,
}
impl MarkerCollector {
    pub fn new(enabled: bool, max_records: usize, max_bytes: usize) -> Self {
        Self {
            enabled,
            max_records,
            max_bytes,
            batch: MarkerBatch {
                markers: vec![],
                dropped: 0,
                truncated: 0,
                retained_bytes: 0,
            },
        }
    }
    pub fn disabled() -> Self {
        Self::new(false, 256, 32 * 1024)
    }
    pub fn interested(&self) -> bool {
        self.enabled
            && self.batch.markers.len() < self.max_records
            && self.batch.retained_bytes < self.max_bytes
    }
    pub fn marker<F>(&mut self, id: &str, detail: F)
    where
        F: FnOnce() -> String,
    {
        if !self.enabled {
            return;
        }
        let overhead = std::mem::size_of::<DiagnosticMarker>().saturating_add(id.len());
        if !self.interested() || overhead > self.max_bytes.saturating_sub(self.batch.retained_bytes)
        {
            self.batch.dropped = self.batch.dropped.saturating_add(1);
            return;
        }
        let mut detail = detail();
        let available = self.max_bytes - self.batch.retained_bytes - overhead;
        if detail.len() > available {
            let mut end = available;
            while !detail.is_char_boundary(end) {
                end -= 1;
            }
            detail.truncate(end);
            detail.shrink_to_fit();
            self.batch.truncated = self.batch.truncated.saturating_add(1);
        }
        self.batch.retained_bytes += overhead + detail.len();
        self.batch.markers.push(DiagnosticMarker {
            id: id.to_owned(),
            detail,
        });
    }
    pub fn finish(self) -> MarkerBatch {
        self.batch
    }
}

fn bounded_metadata(s: &str) -> String {
    let mut end = s.len().min(1024);
    while !s.is_char_boundary(end) {
        end -= 1;
    }
    s[..end].to_owned()
}
fn path_bytes(path: &[PathSegment]) -> usize {
    path.iter().fold(0usize, |n, p| {
        n.saturating_add(std::mem::size_of::<PathSegment>())
            .saturating_add(match p {
                PathSegment::Field(s)
                | PathSegment::Variant(s)
                | PathSegment::MapKey(inspect::MapKey::String(s)) => s.len(),
                PathSegment::MapKey(inspect::MapKey::Integer { decimal, .. }) => decimal.len(),
                _ => 0,
            })
    })
}

fn node_type(node: &InspectNode) -> &'static str {
    use inspect::{IntegerType as I, NodeKind as N};
    match &node.kind {
        N::Scalar(Scalar::Integer { kind, .. }) => match kind {
            I::I8 => "i8",
            I::I16 => "i16",
            I::I32 => "i32",
            I::I64 => "i64",
            I::I128 => "i128",
            I::Isize => "isize",
            I::U8 => "u8",
            I::U16 => "u16",
            I::U32 => "u32",
            I::U64 => "u64",
            I::U128 => "u128",
            I::Usize => "usize",
        },
        N::Scalar(Scalar::Float { kind, .. }) => kind,
        N::Scalar(Scalar::Bool(_)) => "bool",
        N::Scalar(Scalar::Char(_)) => "char",
        N::Scalar(Scalar::Unit) => "unit",
        N::String { .. } => "string",
        N::Bytes { .. } => "bytes",
        N::Object { .. } => "object",
        N::Enum { .. } => "enum",
        N::Sequence { .. } => "sequence",
        N::Map { .. } => "map",
        N::Opaque { .. } => "opaque",
        N::Redacted => "redacted",
        N::Unavailable => "unavailable",
        N::Truncated { .. } => "truncated",
    }
}

fn trigger_bytes(trigger: &WatchTrigger) -> usize {
    match trigger {
        WatchTrigger::Equals(Scalar::Integer { decimal, .. } | Scalar::Float { decimal, .. }) => {
            decimal.len()
        }
        _ => 0,
    }
}
fn property_id_bytes(ids: &[&str]) -> usize {
    ids.iter().take(16).fold(0usize, |n, id| {
        n.saturating_add(std::mem::size_of::<String>())
            .saturating_add(id.len().min(256))
    })
}
fn property_ids(ids: &[&str]) -> Vec<String> {
    ids.iter()
        .take(16)
        .map(|id| {
            let mut end = id.len().min(256);
            while !id.is_char_boundary(end) {
                end -= 1;
            }
            id[..end].to_owned()
        })
        .collect()
}

fn metadata_complete(schema: &str, context: &ObservationContext<'_>) -> bool {
    schema.len() <= 1024
        && context.model.is_none_or(|s| s.len() <= 1024)
        && context.machine.is_none_or(|s| s.len() <= 1024)
        && context.failing_properties.len() <= 16
        && context.failing_properties.iter().all(|id| id.len() <= 256)
}

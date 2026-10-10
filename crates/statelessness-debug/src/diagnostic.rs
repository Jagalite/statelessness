//! Explicit scoped probes, revisioned capture and independent bounded sink queues.
//! No globals, model calls, runtime clocks or exporter I/O occur in a probe.
use crate::inspect::{
    self, Inspect, InspectLimits, InspectNode, InspectQuery, NodeKind, PageRequest, PathSegment,
    Scalar, SnapshotId,
};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::io::{self, Write};

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Severity {
    Trace,
    Debug,
    Info,
    Warn,
    Error,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DiagnosticOrigin {
    Simulation,
    Live,
    DebugControlledLive,
    Test,
    Replay,
    Imported,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SiteKind {
    Probe,
    Marker,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Trigger {
    Every,
    Changed,
    Equals(Scalar),
    AboveU128 {
        threshold: u128,
        hysteresis: u128,
        cooldown_turns: u64,
    },
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Subscription {
    pub sink: u64,
    pub site: String,
    pub kind: SiteKind,
    pub path: Vec<PathSegment>,
    pub trigger: Trigger,
    pub sample_every: u64,
    pub minimum_severity: Severity,
}
/// Host-declared metadata for cheap routing before any value is evaluated.
/// These labels select capture; they are not copied into payloads or exported.
#[derive(Clone, Copy, Debug, Default)]
pub struct CaptureContext<'a> {
    pub model: Option<&'a str>,
    pub machine: Option<&'a str>,
    pub entity: Option<&'a str>,
    pub correlation: Option<&'a str>,
    pub input_variant: Option<&'a str>,
    pub output_variant: Option<&'a str>,
    pub property: Option<&'a str>,
    pub effect_kind: Option<&'a str>,
    pub source_site: Option<&'a str>,
    pub property_failed: Option<bool>,
}
/// All populated selectors must match exact, bounded, host-declared labels.
/// No field access, expression evaluation, wildcard expansion or authorization
/// is implied by a match. Sink permissions still govern every projection.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ScopeSelector {
    pub producer: Option<u64>,
    pub origin: Option<DiagnosticOrigin>,
    pub model: Option<String>,
    pub machine: Option<String>,
    pub entity: Option<String>,
    pub correlation: Option<String>,
    pub input_variant: Option<String>,
    pub output_variant: Option<String>,
    pub property: Option<String>,
    pub effect_kind: Option<String>,
    pub source_site: Option<String>,
    pub property_failed: Option<bool>,
}
impl ScopeSelector {
    fn labels(&self) -> [&Option<String>; 9] {
        [
            &self.model,
            &self.machine,
            &self.entity,
            &self.correlation,
            &self.input_variant,
            &self.output_variant,
            &self.property,
            &self.effect_kind,
            &self.source_site,
        ]
    }
    fn matches(
        &self,
        producer: u64,
        origin: DiagnosticOrigin,
        context: CaptureContext<'_>,
    ) -> bool {
        self.producer.is_none_or(|p| p == producer)
            && self.origin.is_none_or(|o| o == origin)
            && self
                .property_failed
                .is_none_or(|failed| Some(failed) == context.property_failed)
            && self
                .labels()
                .iter()
                .zip([
                    context.model,
                    context.machine,
                    context.entity,
                    context.correlation,
                    context.input_variant,
                    context.output_variant,
                    context.property,
                    context.effect_kind,
                    context.source_site,
                ])
                .all(|(expected, actual)| {
                    expected
                        .as_ref()
                        .is_none_or(|expected| Some(expected.as_str()) == actual)
                })
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SelectedSubscription {
    pub subscription: Subscription,
    pub selector: ScopeSelector,
}
#[derive(Clone, Debug)]
pub struct SinkPermissions {
    pub sites: Vec<String>,
    pub paths: Vec<Vec<PathSegment>>,
}
impl SinkPermissions {
    pub fn deny_all() -> Self {
        Self {
            sites: vec![],
            paths: vec![],
        }
    }
    /// Explicit local-only authority. External consumers should use allowlists.
    pub fn local_all() -> Self {
        Self {
            sites: vec!["*".into()],
            paths: vec![vec![]],
        }
    }
    fn allows(&self, s: &Subscription) -> bool {
        self.sites.iter().any(|id| id == "*" || id == &s.site)
            && self.paths.iter().any(|path| s.path.starts_with(path))
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DropPolicy {
    Newest,
    Oldest,
}
#[derive(Clone, Copy, Debug)]
pub struct SinkLimits {
    pub records: usize,
    pub bytes: usize,
    pub policy: DropPolicy,
}
impl Default for SinkLimits {
    fn default() -> Self {
        Self {
            records: 1024,
            bytes: 4 * 1024 * 1024,
            policy: DropPolicy::Newest,
        }
    }
}
#[derive(Clone, Copy, Debug)]
pub struct DiagnosticLimits {
    pub producers: usize,
    pub sinks: usize,
    pub subscriptions: usize,
    pub config_bytes: usize,
    pub turn_records: usize,
    pub turn_bytes: usize,
    pub event_bytes: usize,
    pub baseline_bytes: usize,
}
impl Default for DiagnosticLimits {
    fn default() -> Self {
        Self {
            producers: 64,
            sinks: 16,
            subscriptions: 64,
            config_bytes: 64 * 1024,
            turn_records: 256,
            turn_bytes: 256 * 1024,
            event_bytes: 32 * 1024,
            baseline_bytes: 8 * 1024 * 1024,
        }
    }
}
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct SinkHealth {
    pub captured: u64,
    pub filtered: u64,
    pub sampled: u64,
    pub dropped: u64,
    pub truncated: u64,
    pub exporter_failed: u64,
    pub abandoned: u64,
    pub inspection_failed: u64,
    pub baseline_evicted: u64,
    pub observation_gaps: u64,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProbeBaselineReason {
    Initial,
    ObservationGap,
    InspectionIncomplete,
    TypeUnavailable,
    SelectorGap,
    Reconfigured,
    SchemaChanged,
    SourceChanged,
    Evicted,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DiagnosticEvent {
    pub schema_version: u32,
    pub run_id: u64,
    pub producer: u64,
    pub epoch: u64,
    pub producer_sequence: u64,
    pub capture_revision: u64,
    pub transition: u64,
    pub origin: DiagnosticOrigin,
    pub severity: Severity,
    pub site: String,
    pub kind: SiteKind,
    pub path: Vec<PathSegment>,
    pub payload: Option<InspectNode>,
    pub display_schema: Option<(String, u32)>,
    pub baseline: bool,
    pub baseline_reason: Option<ProbeBaselineReason>,
    pub change_unknown: bool,
    pub incomplete_turn: bool,
}
impl DiagnosticEvent {
    pub fn retained_bytes(&self) -> usize {
        std::mem::size_of::<Self>()
            + self.site.len()
            + path_bytes(&self.path)
            + self.payload.as_ref().map_or(0, InspectNode::retained_bytes)
            + self
                .display_schema
                .as_ref()
                .map_or(0, |(name, _)| name.len())
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DiagnosticError {
    Capacity,
    UnknownProducer,
    UnknownSink,
    StaleRevision,
    Unauthorized,
    InvalidConfig,
    Exhausted,
    StaleBoundary,
    PendingConfiguration,
}
impl std::fmt::Display for DiagnosticError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{self:?}")
    }
}
impl std::error::Error for DiagnosticError {}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ConfigurationAck {
    pub capture_revision: u64,
    pub pending_producers: Vec<u64>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProducerAck {
    pub producer: u64,
    pub epoch: u64,
    pub capture_revision: u64,
    pub effective_transition: u64,
}
struct ActiveSubscription {
    config: Subscription,
    selector: ScopeSelector,
    baseline: Option<InspectNode>,
    baseline_reason: Option<ProbeBaselineReason>,
    schema: Option<(&'static str, u32)>,
    high: bool,
    last_alert: Option<u64>,
    eligible: u64,
}
struct Producer {
    epoch: u64,
    revision: u64,
    last_transition: Option<u64>,
    last_source: Option<ProducerSource>,
    effective: u64,
    sequence: u64,
    config: Vec<ActiveSubscription>,
}
/// A selected producer may multiplex modeled instances. Keep bounded identity
/// metadata so equality never crosses instances merely because the probe site
/// and display schema match. Unselected/disabled producers allocate no identity.
struct ProducerSource {
    run_id: u64,
    origin: DiagnosticOrigin,
    labels: [Option<String>; 4],
}
impl ProducerSource {
    fn labels(context: CaptureContext<'_>) -> [Option<&str>; 4] {
        [
            context.model,
            context.machine,
            context.entity,
            context.correlation,
        ]
    }
    fn matches(&self, run_id: u64, origin: DiagnosticOrigin, context: CaptureContext<'_>) -> bool {
        self.run_id == run_id
            && self.origin == origin
            && self
                .labels
                .iter()
                .zip(Self::labels(context))
                .all(|(previous, current)| previous.as_deref() == current)
    }
    fn new(run_id: u64, origin: DiagnosticOrigin, context: CaptureContext<'_>) -> Self {
        Self {
            run_id,
            origin,
            labels: Self::labels(context).map(|label| label.map(str::to_owned)),
        }
    }
}
struct SinkQueue {
    permissions: SinkPermissions,
    limits: SinkLimits,
    queue: VecDeque<DiagnosticEvent>,
    bytes: usize,
    health: SinkHealth,
}
pub struct DiagnosticHub {
    limits: DiagnosticLimits,
    revision: u64,
    config: Vec<SelectedSubscription>,
    producers: BTreeMap<u64, Producer>,
    sinks: BTreeMap<u64, SinkQueue>,
}
impl DiagnosticHub {
    pub fn new(limits: DiagnosticLimits) -> Self {
        Self {
            limits,
            revision: 0,
            config: vec![],
            producers: BTreeMap::new(),
            sinks: BTreeMap::new(),
        }
    }
    pub fn revision(&self) -> u64 {
        self.revision
    }
    pub fn limits(&self) -> DiagnosticLimits {
        self.limits
    }
    pub fn pending_producers(&self) -> Vec<u64> {
        self.producers
            .iter()
            .filter(|(_, p)| p.revision != self.revision)
            .map(|(id, _)| *id)
            .collect()
    }
    /// Owned bounded metadata only; a controller cannot acknowledge producers by reading it.
    pub fn producer_status(&self) -> Vec<ProducerAck> {
        self.producers
            .iter()
            .map(|(id, producer)| ProducerAck {
                producer: *id,
                epoch: producer.epoch,
                capture_revision: producer.revision,
                effective_transition: producer.effective,
            })
            .collect()
    }
    pub fn add_sink(
        &mut self,
        id: u64,
        permissions: SinkPermissions,
        limits: SinkLimits,
    ) -> Result<(), DiagnosticError> {
        let permission_bytes =
            permissions
                .sites
                .iter()
                .fold(std::mem::size_of::<SinkPermissions>(), |sum, site| {
                    sum.saturating_add(std::mem::size_of::<String>())
                        .saturating_add(site.len())
                });
        let permission_bytes = permissions
            .paths
            .iter()
            .fold(permission_bytes, |sum, path| {
                sum.saturating_add(std::mem::size_of::<Vec<PathSegment>>())
                    .saturating_add(path_bytes(path))
            });
        if self.sinks.contains_key(&id)
            || self.sinks.len() >= self.limits.sinks
            || limits.records == 0
            || limits.bytes == 0
            || permission_bytes > self.limits.config_bytes
            || permissions.sites.len() > self.limits.subscriptions
            || permissions.paths.len() > self.limits.subscriptions
            || permissions.paths.iter().any(|path| path.len() > 32)
        {
            return Err(DiagnosticError::Capacity);
        }
        self.sinks.insert(
            id,
            SinkQueue {
                permissions,
                limits,
                queue: VecDeque::new(),
                bytes: 0,
                health: SinkHealth::default(),
            },
        );
        Ok(())
    }
    pub fn register_producer(&mut self, id: u64, epoch: u64) -> Result<(), DiagnosticError> {
        if epoch == 0
            || self.producers.contains_key(&id)
            || self.producers.len() >= self.limits.producers
        {
            return Err(DiagnosticError::Capacity);
        }
        self.producers.insert(
            id,
            Producer {
                epoch,
                revision: 0,
                last_transition: None,
                last_source: None,
                effective: 0,
                sequence: 0,
                config: vec![],
            },
        );
        Ok(())
    }
    /// Configuration affects each producer only after its explicit safe-boundary
    /// acknowledgement. Pending producers retain their old bounded selection.
    pub fn configure(
        &mut self,
        expected: u64,
        config: Vec<Subscription>,
    ) -> Result<ConfigurationAck, DiagnosticError> {
        if expected != self.revision {
            return Err(DiagnosticError::StaleRevision);
        }
        if config.len() > self.limits.subscriptions {
            return Err(DiagnosticError::Capacity);
        }
        self.configure_selected(
            expected,
            config
                .into_iter()
                .map(|subscription| SelectedSubscription {
                    subscription,
                    selector: ScopeSelector::default(),
                })
                .collect(),
        )
    }
    pub fn configure_selected(
        &mut self,
        expected: u64,
        config: Vec<SelectedSubscription>,
    ) -> Result<ConfigurationAck, DiagnosticError> {
        if expected != self.revision {
            return Err(DiagnosticError::StaleRevision);
        }
        if config.len() > self.limits.subscriptions {
            return Err(DiagnosticError::Capacity);
        }
        let mut bytes = 0usize;
        for selected in &config {
            let s = &selected.subscription;
            if selected.selector.labels().iter().any(|label| {
                label
                    .as_ref()
                    .is_some_and(|label| label.is_empty() || label.len() > 256)
            }) {
                return Err(DiagnosticError::InvalidConfig);
            }
            bytes = bytes.saturating_add(std::mem::size_of::<ScopeSelector>());
            for label in selected.selector.labels().into_iter().flatten() {
                bytes = bytes.saturating_add(label.len());
            }
            if s.site.is_empty() || s.site.len() > 256 || s.sample_every == 0 || s.path.len() > 32 {
                return Err(DiagnosticError::InvalidConfig);
            }
            let sink = self
                .sinks
                .get(&s.sink)
                .ok_or(DiagnosticError::UnknownSink)?;
            if !sink.permissions.allows(s) {
                return Err(DiagnosticError::Unauthorized);
            }
            bytes = bytes
                .saturating_add(std::mem::size_of::<Subscription>())
                .saturating_add(s.site.len())
                .saturating_add(path_bytes(&s.path))
                .saturating_add(match &s.trigger {
                    Trigger::Equals(Scalar::Integer { decimal, .. })
                    | Trigger::Equals(Scalar::Float { decimal, .. }) => decimal.len(),
                    _ => 0,
                });
            if bytes > self.limits.config_bytes {
                return Err(DiagnosticError::Capacity);
            }
        }
        self.revision = self
            .revision
            .checked_add(1)
            .ok_or(DiagnosticError::Exhausted)?;
        self.config = config;
        Ok(ConfigurationAck {
            capture_revision: self.revision,
            pending_producers: self.producers.keys().copied().collect(),
        })
    }
    pub fn acknowledge(
        &mut self,
        producer: u64,
        revision: u64,
        effective_transition: u64,
    ) -> Result<ProducerAck, DiagnosticError> {
        if revision != self.revision {
            return Err(DiagnosticError::StaleRevision);
        }
        let p = self
            .producers
            .get_mut(&producer)
            .ok_or(DiagnosticError::UnknownProducer)?;
        if p.last_transition.is_some_and(|s| effective_transition <= s) {
            return Err(DiagnosticError::StaleBoundary);
        }
        let baseline_reason = if p.revision == 0 {
            ProbeBaselineReason::Initial
        } else {
            ProbeBaselineReason::Reconfigured
        };
        p.revision = revision;
        p.effective = effective_transition;
        p.last_source = None;
        p.config = self
            .config
            .iter()
            .cloned()
            .map(|selected| ActiveSubscription {
                config: selected.subscription,
                selector: selected.selector,
                baseline: None,
                baseline_reason: Some(baseline_reason),
                schema: None,
                high: false,
                last_alert: None,
                eligible: 0,
            })
            .collect();
        Ok(ProducerAck {
            producer,
            epoch: p.epoch,
            capture_revision: revision,
            effective_transition,
        })
    }
    pub fn begin_turn(
        &mut self,
        producer: u64,
        run_id: u64,
        transition: u64,
        origin: DiagnosticOrigin,
    ) -> Result<ScopedDiagnostics<'_>, DiagnosticError> {
        self.begin_turn_with_context(
            producer,
            run_id,
            transition,
            origin,
            CaptureContext::default(),
        )
    }
    pub fn begin_turn_with_context<'a>(
        &'a mut self,
        producer: u64,
        run_id: u64,
        transition: u64,
        origin: DiagnosticOrigin,
        context: CaptureContext<'a>,
    ) -> Result<ScopedDiagnostics<'a>, DiagnosticError> {
        if [
            context.model,
            context.machine,
            context.entity,
            context.correlation,
            context.input_variant,
            context.output_variant,
            context.property,
            context.effect_kind,
            context.source_site,
        ]
        .into_iter()
        .flatten()
        .any(|label| label.len() > 256)
        {
            return Err(DiagnosticError::InvalidConfig);
        }
        let p = self
            .producers
            .get_mut(&producer)
            .ok_or(DiagnosticError::UnknownProducer)?;
        if p.last_transition.is_some_and(|last| transition <= last) {
            return Err(DiagnosticError::StaleBoundary);
        }
        if transition < p.effective {
            return Err(DiagnosticError::PendingConfiguration);
        }
        let selected = p
            .config
            .iter()
            .any(|subscription| subscription.selector.matches(producer, origin, context));
        let source_changed = selected
            && p.last_source
                .as_ref()
                .is_some_and(|source| !source.matches(run_id, origin, context));
        let observation_gap = p
            .last_transition
            .is_some_and(|last| transition != last.saturating_add(1));
        if source_changed || observation_gap {
            let mut sinks = BTreeSet::new();
            for s in &mut p.config {
                s.baseline = None;
                s.baseline_reason = Some(if source_changed {
                    ProbeBaselineReason::SourceChanged
                } else {
                    ProbeBaselineReason::ObservationGap
                });
                s.high = false;
                s.last_alert = None;
                if observation_gap {
                    sinks.insert(s.config.sink);
                }
            }
            for id in sinks {
                let health = &mut self.sinks.get_mut(&id).expect("registered sink").health;
                health.observation_gaps = health.observation_gaps.saturating_add(1);
            }
        }
        for s in &mut p.config {
            if !s.selector.matches(producer, origin, context) {
                s.baseline = None;
                s.baseline_reason = Some(ProbeBaselineReason::SelectorGap);
                s.high = false;
                s.last_alert = None;
            }
        }
        p.last_transition = Some(transition);
        if selected && (source_changed || p.last_source.is_none()) {
            p.last_source = Some(ProducerSource::new(run_id, origin, context));
        }
        Ok(ScopedDiagnostics {
            hub: self,
            producer,
            run_id,
            transition,
            origin,
            context,
            staged: vec![],
            bytes: 0,
            finished: false,
        })
    }
    pub fn health(&self, sink: u64) -> Option<&SinkHealth> {
        self.sinks.get(&sink).map(|s| &s.health)
    }
    pub fn queued(&self, sink: u64) -> Option<(usize, usize)> {
        self.sinks.get(&sink).map(|s| (s.queue.len(), s.bytes))
    }
    pub fn peek_events(
        &self,
        sink: u64,
        maximum: usize,
    ) -> Option<impl Iterator<Item = &DiagnosticEvent>> {
        Some(self.sinks.get(&sink)?.queue.iter().take(maximum))
    }
    pub fn peek(&self, sink: u64) -> Option<&DiagnosticEvent> {
        self.sinks.get(&sink)?.queue.front()
    }
    pub fn pop(&mut self, sink: u64) -> Option<DiagnosticEvent> {
        let s = self.sinks.get_mut(&sink)?;
        let event = s.queue.pop_front()?;
        s.bytes -= event.retained_bytes();
        Some(event)
    }
    /// Export outside model/effect locks. Custom sink I/O is host-owned and may
    /// block; the capture path never invokes this method or the writer.
    pub fn drain_to(
        &mut self,
        id: u64,
        sink: &mut impl DiagnosticSink,
        maximum: usize,
    ) -> Result<usize, DiagnosticError> {
        if !self.sinks.contains_key(&id) {
            return Err(DiagnosticError::UnknownSink);
        }
        let mut written = 0;
        for _ in 0..maximum {
            let Some(event) = self.pop(id) else {
                break;
            };
            match sink.emit(&event) {
                Ok(()) => written += 1,
                Err(_) => {
                    self.sinks.get_mut(&id).unwrap().health.exporter_failed += 1;
                    break;
                }
            }
        }
        Ok(written)
    }
    /// Bounded shutdown: abandon queued records after a host-selected drain.
    /// A host may implement a timeout between drain calls; blocking I/O itself is
    /// not preemptible and is not performed here.
    pub fn abandon(&mut self, id: u64) -> Result<usize, DiagnosticError> {
        let s = self
            .sinks
            .get_mut(&id)
            .ok_or(DiagnosticError::UnknownSink)?;
        let n = s.queue.len();
        s.health.abandoned = s.health.abandoned.saturating_add(n as u64);
        s.queue.clear();
        s.bytes = 0;
        Ok(n)
    }
    fn enqueue(&mut self, id: u64, event: DiagnosticEvent) {
        let Some(s) = self.sinks.get_mut(&id) else {
            return;
        };
        let bytes = event.retained_bytes();
        if bytes > s.limits.bytes {
            s.health.dropped = s.health.dropped.saturating_add(1);
            return;
        }
        while s.queue.len() >= s.limits.records || s.bytes.saturating_add(bytes) > s.limits.bytes {
            s.health.dropped = s.health.dropped.saturating_add(1);
            if s.limits.policy == DropPolicy::Newest {
                return;
            }
            if let Some(old) = s.queue.pop_front() {
                s.bytes -= old.retained_bytes();
            } else {
                return;
            }
        }
        s.health.captured = s.health.captured.saturating_add(1);
        s.bytes += bytes;
        s.queue.push_back(event);
    }
}
pub struct ScopedDiagnostics<'a> {
    hub: &'a mut DiagnosticHub,
    producer: u64,
    run_id: u64,
    transition: u64,
    origin: DiagnosticOrigin,
    context: CaptureContext<'a>,
    staged: Vec<(u64, DiagnosticEvent)>,
    bytes: usize,
    finished: bool,
}
impl ScopedDiagnostics<'_> {
    pub fn interested(&self, site: &str, kind: SiteKind, severity: Severity) -> bool {
        self.hub.producers[&self.producer].config.iter().any(|s| {
            s.config.site == site
                && s.config.kind == kind
                && severity >= s.config.minimum_severity
                && s.selector.matches(self.producer, self.origin, self.context)
        })
    }
    /// The closure is evaluated once, even with multiple destinations. Borrow a
    /// local as `|| &value`; values never move solely because a probe is present.
    pub fn probe<T: Inspect>(&mut self, site: &str, value: impl FnOnce() -> T) {
        self.probe_at(site, Severity::Debug, value);
    }
    pub fn probe_at<T: Inspect>(
        &mut self,
        site: &str,
        severity: Severity,
        value: impl FnOnce() -> T,
    ) {
        if !self.interested(site, SiteKind::Probe, severity) {
            return;
        }
        let value = value();
        self.capture(site, SiteKind::Probe, severity, Some(&value));
    }
    pub fn marker(&mut self, site: &str) {
        if self.interested(site, SiteKind::Marker, Severity::Debug) {
            self.capture(site, SiteKind::Marker, Severity::Debug, None);
        }
    }
    fn capture(
        &mut self,
        site: &str,
        kind: SiteKind,
        severity: Severity,
        value: Option<&dyn Inspect>,
    ) {
        let schema = value.map(Inspect::schema);
        let p = self.hub.producers.get_mut(&self.producer).unwrap();
        let Some(sequence) = p.sequence.checked_add(1) else {
            return;
        };
        p.sequence = sequence;
        let mut projections: Vec<(Vec<PathSegment>, Option<InspectNode>)> = vec![];
        for subscription in &mut p.config {
            let c = &subscription.config;
            if c.site != site
                || c.kind != kind
                || severity < c.minimum_severity
                || !subscription
                    .selector
                    .matches(self.producer, self.origin, self.context)
            {
                continue;
            }
            let sink = self.hub.sinks.get_mut(&c.sink).expect("validated sink");
            let node = if let Some((_, node)) = projections.iter().find(|(path, _)| path == &c.path)
            {
                node.clone()
            } else {
                let node = value.and_then(|value| {
                    inspect::inspect(
                        value,
                        &InspectQuery {
                            snapshot: SnapshotId {
                                session: self.run_id,
                                revision: self.transition,
                                sequence: self.transition,
                            },
                            path: c.path.clone(),
                            schema_version: value.schema().version,
                            page: PageRequest::default(),
                            limits: InspectLimits {
                                max_bytes: self.hub.limits.event_bytes.saturating_sub(1024),
                                ..Default::default()
                            },
                        },
                    )
                    .ok()
                    .map(|r| r.node)
                });
                projections.push((c.path.clone(), node.clone()));
                node
            };
            if kind == SiteKind::Probe && node.is_none() {
                sink.health.inspection_failed = sink.health.inspection_failed.saturating_add(1);
            }
            let next_schema = schema.map(|s| (s.name, s.version));
            if subscription.schema != next_schema {
                if subscription.schema.is_some() {
                    subscription.baseline_reason = Some(ProbeBaselineReason::SchemaChanged);
                }
                subscription.baseline = None;
                subscription.high = false;
                subscription.last_alert = None;
                subscription.schema = next_schema;
            }
            let complete = node.as_ref().is_some_and(InspectNode::is_complete);
            let threshold_unavailable = matches!(c.trigger, Trigger::AboveU128 { .. })
                && node.as_ref().and_then(unsigned_integer).is_none();
            let baseline = subscription.baseline.is_none();
            let baseline_reason = if baseline {
                subscription
                    .baseline_reason
                    .take()
                    .or(Some(ProbeBaselineReason::Initial))
            } else {
                None
            };
            let unknown = kind == SiteKind::Probe
                && (!complete
                    || threshold_unavailable
                    || baseline_reason
                        .is_some_and(|reason| reason != ProbeBaselineReason::Initial));
            let changed = inspect::compare_nodes(subscription.baseline.as_ref(), node.as_ref())
                == inspect::DiffKind::Changed;
            let emit = match &c.trigger {
                Trigger::Every => true,
                Trigger::Changed => baseline || unknown || changed,
                Trigger::Equals(expected) => {
                    complete
                        && node.as_ref().is_some_and(
                            |n| matches!(&n.kind,NodeKind::Scalar(actual) if actual==expected),
                        )
                }
                Trigger::AboveU128 {
                    threshold,
                    hysteresis,
                    cooldown_turns,
                } => {
                    let numeric = node.as_ref().and_then(unsigned_integer);
                    if let Some(n) = numeric {
                        if n <= threshold.saturating_sub(*hysteresis) {
                            subscription.high = false;
                        }
                        if n > *threshold
                            && !subscription.high
                            && subscription.last_alert.is_none_or(|last| {
                                self.transition.saturating_sub(last) >= *cooldown_turns
                            })
                        {
                            subscription.high = true;
                            subscription.last_alert = Some(self.transition);
                            true
                        } else {
                            false
                        }
                    } else {
                        false
                    }
                }
            };
            subscription.baseline = if complete && !threshold_unavailable {
                node.clone()
            } else {
                None
            };
            if kind == SiteKind::Probe && (!complete || threshold_unavailable) {
                // A missing/redacted/partial observation breaks both change and
                // threshold continuity, even when the display schema is stable.
                subscription.high = false;
                subscription.last_alert = None;
                subscription.baseline_reason = Some(if threshold_unavailable && complete {
                    ProbeBaselineReason::TypeUnavailable
                } else {
                    ProbeBaselineReason::InspectionIncomplete
                });
            }
            if !emit {
                sink.health.filtered = sink.health.filtered.saturating_add(1);
                continue;
            }
            subscription.eligible = subscription.eligible.saturating_add(1);
            if !(subscription.eligible - 1).is_multiple_of(c.sample_every) {
                sink.health.sampled = sink.health.sampled.saturating_add(1);
                continue;
            }
            if unknown {
                sink.health.truncated = sink.health.truncated.saturating_add(1);
            }
            let event = DiagnosticEvent {
                schema_version: 1,
                run_id: self.run_id,
                producer: self.producer,
                epoch: p.epoch,
                producer_sequence: sequence,
                capture_revision: p.revision,
                transition: self.transition,
                origin: self.origin,
                severity,
                site: site.into(),
                kind,
                path: c.path.clone(),
                payload: node,
                display_schema: schema.map(|s| (s.name.chars().take(256).collect(), s.version)),
                baseline,
                baseline_reason,
                change_unknown: unknown,
                incomplete_turn: false,
            };
            let bytes = event.retained_bytes();
            if bytes > self.hub.limits.event_bytes
                || self.staged.len() >= self.hub.limits.turn_records
                || self.bytes.saturating_add(bytes) > self.hub.limits.turn_bytes
            {
                sink.health.dropped = sink.health.dropped.saturating_add(1);
                continue;
            }
            self.bytes += bytes;
            self.staged.push((c.sink, event));
        }
        // Baseline memory is bounded independently from sink queues. Eviction is
        // visible and makes the next observation an unknown/initial baseline.
        let mut baseline_bytes = self
            .hub
            .producers
            .values()
            .flat_map(|p| &p.config)
            .fold(0usize, |sum, s| {
                sum.saturating_add(s.baseline.as_ref().map_or(0, InspectNode::retained_bytes))
            });
        for producer in self.hub.producers.values_mut() {
            for subscription in &mut producer.config {
                if baseline_bytes <= self.hub.limits.baseline_bytes {
                    break;
                }
                if let Some(old) = subscription.baseline.take() {
                    baseline_bytes -= old.retained_bytes();
                    subscription.baseline_reason = Some(ProbeBaselineReason::Evicted);
                    subscription.high = false;
                    subscription.last_alert = None;
                    let health = &mut self
                        .hub
                        .sinks
                        .get_mut(&subscription.config.sink)
                        .unwrap()
                        .health;
                    health.baseline_evicted = health.baseline_evicted.saturating_add(1);
                }
            }
        }
    }
    /// Publish only after the host's turn returns. An error batch is explicitly
    /// incomplete; a successful diagnostic publication says nothing about checks.
    pub fn finish(mut self, complete_turn: bool) {
        self.publish(complete_turn);
        self.finished = true;
    }
    fn publish(&mut self, complete_turn: bool) {
        for (sink, mut event) in self.staged.drain(..) {
            event.incomplete_turn = !complete_turn;
            self.hub.enqueue(sink, event);
        }
    }
}
impl Drop for ScopedDiagnostics<'_> {
    fn drop(&mut self) {
        if !self.finished {
            self.publish(false);
        }
    }
}
fn unsigned_integer(node: &InspectNode) -> Option<u128> {
    if !node.is_complete() {
        return None;
    }
    match &node.kind {
        NodeKind::Scalar(Scalar::Integer { decimal, .. }) => decimal.parse().ok(),
        _ => None,
    }
}
fn path_bytes(path: &[PathSegment]) -> usize {
    path.iter().fold(0usize, |sum, p| {
        sum.saturating_add(std::mem::size_of::<PathSegment>())
            .saturating_add(match p {
                PathSegment::Field(s) | PathSegment::Variant(s) => s.len(),
                PathSegment::MapKey(inspect::MapKey::String(s)) => s.len(),
                PathSegment::MapKey(inspect::MapKey::Integer { decimal, .. }) => decimal.len(),
                _ => 0,
            })
    })
}

pub trait DiagnosticSink {
    fn emit(&mut self, event: &DiagnosticEvent) -> io::Result<()>;
}
pub struct JsonlSink<W: Write> {
    pub writer: W,
}
impl<W: Write> DiagnosticSink for JsonlSink<W> {
    fn emit(&mut self, event: &DiagnosticEvent) -> io::Result<()> {
        self.writer.write_all(event_json(event).as_bytes())?;
        self.writer.write_all(b"\n")
    }
}
pub struct TextSink<W: Write> {
    pub writer: W,
}
impl<W: Write> DiagnosticSink for TextSink<W> {
    fn emit(&mut self, event: &DiagnosticEvent) -> io::Result<()> {
        // All external text is JSON escaped; ESC/newline can never become terminal control.
        writeln!(
            self.writer,
            "{:?} producer={} epoch={} sequence={} capture={} site={} value={} incomplete={}",
            event.severity,
            event.producer,
            event.epoch,
            event.producer_sequence,
            event.capture_revision,
            quoted(&event.site),
            event
                .payload
                .as_ref()
                .map(node_json)
                .unwrap_or_else(|| "null".into()),
            event.incomplete_turn
        )
    }
}
/// Stable JSON envelope. Every integral identifier/value is a decimal string,
/// preserving u128 precision in JavaScript and other language-neutral consumers.
pub fn event_json(event: &DiagnosticEvent) -> String {
    let schema = event
        .display_schema
        .as_ref()
        .map(|(name, version)| format!("{{\"name\":{},\"version\":\"{}\"}}", quoted(name), version))
        .unwrap_or_else(|| "null".into());
    format!(
        "{{\"schema_version\":\"{}\",\"run_id\":\"{}\",\"producer\":\"{}\",\"epoch\":\"{}\",\"producer_sequence\":\"{}\",\"capture_revision\":\"{}\",\"transition\":\"{}\",\"origin\":{},\"severity\":{},\"site\":{},\"kind\":{},\"path\":{},\"display_schema\":{},\"baseline\":{},\"baseline_reason\":{},\"change_unknown\":{},\"incomplete_turn\":{},\"payload\":{}}}",
        event.schema_version,
        event.run_id,
        event.producer,
        event.epoch,
        event.producer_sequence,
        event.capture_revision,
        event.transition,
        quoted(&format!("{:?}", event.origin)),
        quoted(&format!("{:?}", event.severity)),
        quoted(&event.site),
        quoted(&format!("{:?}", event.kind)),
        path_json(&event.path),
        schema,
        event.baseline,
        event
            .baseline_reason
            .map(|reason| quoted(&format!("{reason:?}")))
            .unwrap_or_else(|| "null".into()),
        event.change_unknown,
        event.incomplete_turn,
        event
            .payload
            .as_ref()
            .map(node_json)
            .unwrap_or_else(|| "null".into())
    )
}
pub fn path_json(path: &[PathSegment]) -> String {
    format!(
        "[{}]",
        path.iter()
            .map(|segment| match segment {
                PathSegment::Field(name) => format!("{{\"field\":{}}}", quoted(name)),
                PathSegment::Variant(name) => format!("{{\"variant\":{}}}", quoted(name)),
                PathSegment::Index(index) => format!("{{\"index\":\"{index}\"}}"),
                PathSegment::MapKey(key) => format!("{{\"map_key\":{}}}", map_key_json(key)),
            })
            .collect::<Vec<_>>()
            .join(",")
    )
}
fn map_key_json(key: &inspect::MapKey) -> String {
    match key {
        inspect::MapKey::String(value) => {
            format!("{{\"kind\":\"string\",\"value\":{}}}", quoted(value))
        }
        inspect::MapKey::Integer { kind, decimal } => format!(
            "{{\"kind\":\"integer\",\"type\":{},\"decimal\":{}}}",
            quoted(&format!("{kind:?}")),
            quoted(decimal)
        ),
        inspect::MapKey::Bool(value) => format!("{{\"kind\":\"bool\",\"value\":{value}}}"),
        inspect::MapKey::Char(value) => format!(
            "{{\"kind\":\"char\",\"value\":{}}}",
            quoted(&value.to_string())
        ),
    }
}
fn quoted(value: &str) -> String {
    let mut out = String::from("\"");
    for c in value.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if c.is_control() => out.push_str(&format!("\\u{:04x}", c as u32)),
            _ => out.push(c),
        }
    }
    out.push('"');
    out
}
pub fn node_json(node: &InspectNode) -> String {
    let (kind, value) = match &node.kind {
        NodeKind::Scalar(Scalar::Integer { kind, decimal }) => (
            "integer",
            format!(
                "{{\"type\":{},\"decimal\":{}}}",
                quoted(&format!("{kind:?}")),
                quoted(decimal)
            ),
        ),
        NodeKind::Scalar(Scalar::Float {
            kind,
            decimal,
            bits,
        }) => (
            "float",
            format!(
                "{{\"type\":{},\"decimal\":{},\"bits\":\"{}\"}}",
                quoted(kind),
                quoted(decimal),
                bits
            ),
        ),
        NodeKind::Scalar(Scalar::Bool(b)) => ("bool", b.to_string()),
        NodeKind::Scalar(Scalar::Char(c)) => ("char", quoted(&c.to_string())),
        NodeKind::Scalar(Scalar::Unit) => ("unit", "null".into()),
        NodeKind::String {
            preview,
            total_bytes,
        } => (
            "string",
            format!(
                "{{\"preview\":{},\"total_bytes\":\"{}\"}}",
                quoted(preview),
                total_bytes
            ),
        ),
        NodeKind::Bytes {
            preview,
            total_bytes,
        } => (
            "bytes",
            format!(
                "{{\"hex\":{},\"total_bytes\":\"{}\"}}",
                quoted(
                    &preview
                        .iter()
                        .map(|b| format!("{b:02x}"))
                        .collect::<String>()
                ),
                total_bytes
            ),
        ),
        NodeKind::Object { name } => ("object", quoted(name)),
        NodeKind::Enum {
            name,
            variant_id,
            variant_label,
        } => (
            "enum",
            format!(
                "{{\"name\":{},\"variant_id\":{},\"label\":{}}}",
                quoted(name),
                quoted(variant_id),
                quoted(variant_label)
            ),
        ),
        NodeKind::Sequence { name } => ("sequence", quoted(name)),
        NodeKind::Map { ordering } => ("map", quoted(&format!("{ordering:?}"))),
        NodeKind::Opaque { label } => ("opaque", quoted(label)),
        NodeKind::Redacted => ("redacted", "null".into()),
        NodeKind::Unavailable => ("unavailable", "null".into()),
        NodeKind::Truncated { reason } => ("truncated", quoted(&format!("{reason:?}"))),
    };
    let children = node
        .children
        .iter()
        .map(|c| {
            format!(
                "{{\"segment\":{},\"label\":{},\"node\":{}}}",
                path_json(std::slice::from_ref(&c.segment)),
                quoted(&c.label),
                node_json(&c.node)
            )
        })
        .collect::<Vec<_>>()
        .join(",");
    let count = node
        .child_count
        .map(|n| quoted(&n.to_string()))
        .unwrap_or_else(|| "null".into());
    let page = node
        .page
        .map(|p| {
            format!(
                "{{\"offset\":\"{}\",\"returned\":\"{}\",\"total\":\"{}\",\"next_offset\":{}}}",
                p.offset,
                p.returned,
                p.total,
                p.next_offset
                    .map(|n| quoted(&n.to_string()))
                    .unwrap_or_else(|| "null".into())
            )
        })
        .unwrap_or_else(|| "null".into());
    format!(
        "{{\"kind\":{},\"value\":{},\"completeness\":{},\"child_count\":{},\"page\":{},\"children\":[{}]}}",
        quoted(kind),
        value,
        match node.completeness {
            inspect::Completeness::Complete => "{\"kind\":\"complete\",\"reason\":null}".into(),
            inspect::Completeness::Partial(reason) => format!(
                "{{\"kind\":\"partial\",\"reason\":{}}}",
                quoted(&format!("{reason:?}"))
            ),
        },
        count,
        page,
        children
    )
}

#[cfg(not(feature = "compiled-out-diagnostics"))]
#[macro_export]
macro_rules! probe {
    ($sink:expr,$id:expr,$value:expr) => {{
        $sink.probe($id, $value);
    }};
}
#[cfg(feature = "compiled-out-diagnostics")]
#[macro_export]
macro_rules! probe {
    ($sink:expr,$id:expr,$value:expr) => {{}};
}
#[cfg(not(feature = "compiled-out-diagnostics"))]
#[macro_export]
macro_rules! marker {
    ($sink:expr,$id:expr) => {{
        $sink.marker($id);
    }};
}
#[cfg(feature = "compiled-out-diagnostics")]
#[macro_export]
macro_rules! marker {
    ($sink:expr,$id:expr) => {{}};
}

/// Optional diagnostic JSONL sidecar binding. The trace digest detects accidental
/// mismatch; it is not authentication, and the events are never exact evidence.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SidecarManifest {
    pub version: u32,
    pub run_id: u64,
    pub trace: crate::workbench::ArtifactIdentity,
}
impl SidecarManifest {
    pub fn for_trace(
        trace: &stateless::trace::Trace,
        run_id: u64,
    ) -> Result<Self, stateless::ModelError> {
        Ok(Self {
            version: 1,
            run_id,
            trace: crate::workbench::artifact_identity(trace)?,
        })
    }
    pub fn validate(&self, trace: &stateless::trace::Trace) -> Result<(), stateless::ModelError> {
        if self.version != 1 || self.trace != crate::workbench::artifact_identity(trace)? {
            return Err(stateless::ModelError::new(
                "diagnostic sidecar is incompatible or bound to another exact artifact",
            ));
        }
        Ok(())
    }
    fn header(&self) -> String {
        format!(
            "{{\"schema\":\"stateless.debug.sidecar\",\"version\":\"{}\",\"trace_bytes\":\"{}\",\"trace_fnv1a64\":\"{:016x}\",\"run_id\":\"{}\"}}\n",
            self.version, self.trace.bytes, self.trace.fnv1a64, self.run_id
        )
    }
    /// Validate and buffer within aggregate limits before touching the writer.
    /// Events are redacted projections, never a replacement for `.sttrace`.
    pub fn write_jsonl(
        &self,
        events: &[DiagnosticEvent],
        mut writer: impl Write,
        max_events: usize,
        max_bytes: usize,
    ) -> io::Result<()> {
        if self.version != 1 || events.len() > max_events {
            return Err(io::Error::other("sidecar version or event budget exceeded"));
        }
        let mut output = self.header();
        if output.len() > max_bytes {
            return Err(io::Error::other("sidecar byte budget exceeded"));
        }
        for event in events {
            if event.run_id != self.run_id || event.retained_bytes() > 32 * 1024 {
                return Err(io::Error::other(
                    "sidecar event identity or payload budget differs",
                ));
            }
            let line = event_json(event);
            if output
                .len()
                .checked_add(line.len())
                .and_then(|n| n.checked_add(1))
                .is_none_or(|n| n > max_bytes)
            {
                return Err(io::Error::other("sidecar byte budget exceeded"));
            }
            output.push_str(&line);
            output.push('\n');
        }
        writer.write_all(output.as_bytes())
    }
    /// Read only the bounded canonical manifest. Hosts may parse following JSONL
    /// events as untrusted diagnostic values; this does not execute any model.
    pub fn read_header(reader: &mut impl std::io::BufRead) -> io::Result<Self> {
        use std::io::{BufRead, Read};
        let mut header = String::new();
        reader.take(4097).read_line(&mut header)?;
        if header.len() > 4096 {
            return Err(io::Error::other("sidecar header too large"));
        }
        let error = || io::Error::other("unsupported or malformed sidecar manifest");
        let rest = header
            .strip_prefix(
                "{\"schema\":\"stateless.debug.sidecar\",\"version\":\"1\",\"trace_bytes\":\"",
            )
            .ok_or_else(error)?;
        let (bytes, rest) = rest
            .split_once("\",\"trace_fnv1a64\":\"")
            .ok_or_else(error)?;
        let (digest, rest) = rest.split_once("\",\"run_id\":\"").ok_or_else(error)?;
        let run = rest.strip_suffix("\"}\n").ok_or_else(error)?;
        if digest.len() != 16 {
            return Err(error());
        }
        Ok(Self {
            version: 1,
            run_id: run.parse().map_err(|_| error())?,
            trace: crate::workbench::ArtifactIdentity {
                bytes: bytes.parse().map_err(|_| error())?,
                fnv1a64: u64::from_str_radix(digest, 16).map_err(|_| error())?,
            },
        })
    }
}

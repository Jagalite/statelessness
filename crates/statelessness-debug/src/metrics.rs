//! Bounded, typed cumulative metrics. No log queue is an accounting authority.
//!
//! Duration observations use integer nanoseconds. Exporters convert to seconds.
//! Windows difference compatible cumulative endpoints; gauges use the final
//! endpoint. Quantiles are histogram upper-bound estimates, never averaged.
use std::collections::{BTreeMap, VecDeque};

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum MeasurementOrigin {
    Live,
    DebugControlledLive,
    TestClock,
    ReplayHarness,
    ImportedLive,
}
impl MeasurementOrigin {
    pub fn ingests_live(self) -> bool {
        !matches!(self, Self::ReplayHarness | Self::ImportedLive)
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Outcome {
    None,
    Accepted,
    Rejected,
    Success,
    Failure,
    Cancelled,
    Abandoned,
    Ignored,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Labels {
    pub operation: u16,
    pub component: u16,
    pub worker_pool: u16,
    pub outcome: Outcome,
    pub origin: MeasurementOrigin,
    pub debugger_affected: bool,
}
impl Default for Labels {
    fn default() -> Self {
        Self {
            operation: 0,
            component: 0,
            worker_pool: 0,
            outcome: Outcome::None,
            origin: MeasurementOrigin::Live,
            debugger_affected: false,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MetricKind {
    Counter,
    Gauge,
    Histogram,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum MetricFamily {
    Requests,
    Admissions,
    Attempts,
    AttemptOutcomes,
    Resolutions,
    Deliveries,
    Unresolved,
    Running,
    ReadyDepth,
    CompletionDepth,
    QueueWait,
    AttemptElapsed,
    Resolution,
    DeliveryLag,
    EndToEnd,
    CompletionProcessing,
    AdmissionDelay,
    ResolutionOverhead,
    CompletionPreparation,
    CompletionQueueWait,
    EndToEndObserved,
    Settlement,
    ScheduledBackoff,
    EligibilityWait,
    InterAttemptGap,
    DuplicateObservations,
    DuplicateDeliveries,
    CancellationRequests,
    CancellationAcknowledgements,
    Abandoned,
    Dropped,
    InvalidMeasurements,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MetricDescriptor {
    pub family: MetricFamily,
    pub name: &'static str,
    pub kind: MetricKind,
    pub unit: &'static str,
    pub population: &'static str,
    pub allowed_labels: &'static [&'static str],
    pub reset: &'static str,
}
const LABELS: &[&str] = &[
    "operation",
    "component",
    "worker_pool",
    "outcome",
    "measurement_origin",
    "debugger_affected",
];
impl MetricFamily {
    pub fn descriptor(self) -> MetricDescriptor {
        use MetricFamily::*;
        let (name, kind, population) = match self {
            Requests => (
                "effect_requests_total",
                MetricKind::Counter,
                "observed host output requests",
            ),
            Admissions => (
                "effect_admissions_total",
                MetricKind::Counter,
                "host admission decisions",
            ),
            Attempts => (
                "effect_attempts_total",
                MetricKind::Counter,
                "actual first execution starts",
            ),
            AttemptOutcomes => (
                "effect_attempt_outcomes_total",
                MetricKind::Counter,
                "known attempt terminations",
            ),
            Resolutions => (
                "effect_resolutions_total",
                MetricKind::Counter,
                "logical effect resolutions",
            ),
            Deliveries => (
                "effect_deliveries_total",
                MetricKind::Counter,
                "observed completion inputs including ignored/rejected/duplicates",
            ),
            Unresolved => (
                "effects_unresolved",
                MetricKind::Gauge,
                "admitted unresolved logical effects",
            ),
            Running => (
                "effect_attempts_running",
                MetricKind::Gauge,
                "started attempts without known termination",
            ),
            ReadyDepth => (
                "effect_ready_queue_depth",
                MetricKind::Gauge,
                "synchronized ready entries",
            ),
            CompletionDepth => (
                "completion_queue_depth",
                MetricKind::Gauge,
                "reserved visible completion entries",
            ),
            QueueWait => (
                "effect_queue_wait_seconds",
                MetricKind::Histogram,
                "ready to actual start per attempt",
            ),
            AttemptElapsed => (
                "effect_attempt_elapsed_seconds",
                MetricKind::Histogram,
                "known terminated attempts, including async waits",
            ),
            Resolution => (
                "effect_resolution_seconds",
                MetricKind::Histogram,
                "request to logical resolution",
            ),
            DeliveryLag => (
                "effect_delivery_lag_seconds",
                MetricKind::Histogram,
                "resolution to each completion delivery start",
            ),
            EndToEnd => (
                "effect_end_to_end_seconds",
                MetricKind::Histogram,
                "request to first completion delivery start",
            ),
            CompletionProcessing => (
                "completion_processing_seconds",
                MetricKind::Histogram,
                "delivery begin to immediately after reducer, excluding checks/recording",
            ),
            AdmissionDelay => (
                "effect_admission_delay_seconds",
                MetricKind::Histogram,
                "request to first ready queue boundary",
            ),
            ResolutionOverhead => (
                "effect_resolution_overhead_seconds",
                MetricKind::Histogram,
                "selected finished attempt to logical resolution",
            ),
            CompletionPreparation => (
                "completion_preparation_seconds",
                MetricKind::Histogram,
                "resolution to completion publication reservation",
            ),
            CompletionQueueWait => (
                "completion_queue_wait_seconds",
                MetricKind::Histogram,
                "publication reservation to delivery begin",
            ),
            EndToEndObserved => (
                "effect_end_to_end_observed_seconds",
                MetricKind::Histogram,
                "request to first observed completion",
            ),
            Settlement => (
                "effect_settlement_seconds",
                MetricKind::Histogram,
                "request to explicit application acknowledgement",
            ),
            ScheduledBackoff => (
                "effect_scheduled_backoff_seconds",
                MetricKind::Histogram,
                "host-declared scheduled retry delay",
            ),
            EligibilityWait => (
                "effect_eligibility_wait_seconds",
                MetricKind::Histogram,
                "retry schedule observation to ready eligibility",
            ),
            InterAttemptGap => (
                "effect_inter_attempt_gap_seconds",
                MetricKind::Histogram,
                "previous attempt finish to next actual start; not just backoff",
            ),
            DuplicateObservations => (
                "effect_duplicate_observations_total",
                MetricKind::Counter,
                "repeated notification for the same token boundary",
            ),
            DuplicateDeliveries => (
                "effect_duplicate_deliveries_total",
                MetricKind::Counter,
                "additional observed delivery identities for one effect",
            ),
            CancellationRequests => (
                "effect_cancellation_requests_total",
                MetricKind::Counter,
                "host cancellation requests; no running-gauge decrement",
            ),
            CancellationAcknowledgements => (
                "effect_cancellation_acknowledgements_total",
                MetricKind::Counter,
                "confirmed logical cancellation, distinct from attempt termination",
            ),
            Abandoned => (
                "effect_abandoned_total",
                MetricKind::Counter,
                "local observation/future abandonment; remote termination unknown",
            ),
            Dropped => (
                "telemetry_dropped_total",
                MetricKind::Counter,
                "diagnostic loss; aggregate health remains independent",
            ),
            InvalidMeasurements => (
                "measurement_invalid_total",
                MetricKind::Counter,
                "unknown/reversed/cross-domain measurements",
            ),
        };
        MetricDescriptor {
            family: self,
            name,
            kind,
            population,
            unit: if kind == MetricKind::Histogram {
                "seconds"
            } else {
                "count"
            },
            allowed_labels: LABELS,
            reset: "cumulative per process epoch; never sum repeated snapshots",
        }
    }
}
pub fn metric_catalog() -> Vec<MetricDescriptor> {
    use MetricFamily::*;
    [
        Requests,
        Admissions,
        Attempts,
        AttemptOutcomes,
        Resolutions,
        Deliveries,
        Unresolved,
        Running,
        ReadyDepth,
        CompletionDepth,
        QueueWait,
        AttemptElapsed,
        Resolution,
        DeliveryLag,
        EndToEnd,
        CompletionProcessing,
        AdmissionDelay,
        ResolutionOverhead,
        CompletionPreparation,
        CompletionQueueWait,
        EndToEndObserved,
        Settlement,
        ScheduledBackoff,
        EligibilityWait,
        InterAttemptGap,
        DuplicateObservations,
        DuplicateDeliveries,
        CancellationRequests,
        CancellationAcknowledgements,
        Abandoned,
        Dropped,
        InvalidMeasurements,
    ]
    .into_iter()
    .map(MetricFamily::descriptor)
    .collect()
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LabelCatalog {
    pub operations: Vec<String>,
    pub components: Vec<String>,
    pub worker_pools: Vec<String>,
}
impl Default for LabelCatalog {
    fn default() -> Self {
        Self {
            operations: vec!["unspecified".into()],
            components: vec!["host".into()],
            worker_pools: vec!["default".into()],
        }
    }
}
impl LabelCatalog {
    pub fn accepts(&self, labels: Labels) -> bool {
        (labels.operation as usize) < self.operations.len()
            && (labels.component as usize) < self.components.len()
            && (labels.worker_pool as usize) < self.worker_pools.len()
    }
    fn valid(&self) -> bool {
        [&self.operations, &self.components, &self.worker_pools]
            .into_iter()
            .all(|v| {
                !v.is_empty()
                    && v.len() <= 256
                    && v.iter().all(|s| !s.is_empty() && s.len() <= 64)
                    && v.iter().enumerate().all(|(i, s)| !v[..i].contains(s))
            })
    }
    fn bytes(&self) -> usize {
        [&self.operations, &self.components, &self.worker_pools]
            .into_iter()
            .map(|v| {
                v.capacity() * std::mem::size_of::<String>()
                    + v.iter().map(String::capacity).sum::<usize>()
            })
            .sum()
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct HistogramSchema {
    pub id: u64,
    pub finite_bounds_ns: Vec<u64>,
}
impl Default for HistogramSchema {
    fn default() -> Self {
        Self {
            id: 1,
            finite_bounds_ns: vec![
                1_000,
                10_000,
                100_000,
                500_000,
                1_000_000,
                2_000_000,
                5_000_000,
                10_000_000,
                20_000_000,
                50_000_000,
                100_000_000,
                200_000_000,
                500_000_000,
                1_000_000_000,
                2_000_000_000,
                5_000_000_000,
                10_000_000_000,
                30_000_000_000,
                60_000_000_000,
            ],
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Histogram {
    pub buckets: [u64; 65],
    pub count: u64,
    pub sum_ns: u128,
}
impl Default for Histogram {
    fn default() -> Self {
        Self {
            buckets: [0; 65],
            count: 0,
            sum_ns: 0,
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Quantile {
    pub upper_bound_ns: Option<u64>,
    pub observations: u64,
    pub low_sample_warning: bool,
}
impl Histogram {
    pub fn quantile(
        &self,
        schema: &HistogramSchema,
        numerator: u64,
        denominator: u64,
    ) -> Option<Quantile> {
        if self.count == 0 || denominator == 0 || numerator > denominator {
            return None;
        }
        let rank = ((self.count as u128 * numerator as u128).div_ceil(denominator as u128)).max(1);
        let mut total = 0u128;
        for (i, n) in self
            .buckets
            .iter()
            .take(schema.finite_bounds_ns.len() + 1)
            .enumerate()
        {
            total += *n as u128;
            if total >= rank {
                return Some(Quantile {
                    upper_bound_ns: schema.finite_bounds_ns.get(i).copied(),
                    observations: self.count,
                    low_sample_warning: self.count < 100,
                });
            }
        }
        None
    }
    fn add(&mut self, bounds: &[u64], nanos: u64) -> bool {
        let index = bounds.partition_point(|bound| *bound < nanos);
        let Some(count) = self.count.checked_add(1) else {
            return false;
        };
        let Some(bucket) = self.buckets[index].checked_add(1) else {
            return false;
        };
        let Some(sum) = self.sum_ns.checked_add(nanos as u128) else {
            return false;
        };
        self.count = count;
        self.buckets[index] = bucket;
        self.sum_ns = sum;
        true
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum MetricValue {
    Counter(u64),
    Gauge(u64),
    Histogram(Box<Histogram>),
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct SeriesKey {
    pub family: MetricFamily,
    pub labels: Labels,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum GaugeScope {
    CompleteParticipation,
    HostSnapshot,
    ObservedSinceAttach,
    Unknown,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct SnapshotTime {
    pub domain: u64,
    pub nanos: u64,
}
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct MetricHealth {
    pub rejected_series: u64,
    pub invalid_labels: u64,
    pub arithmetic_errors: u64,
    pub omitted_measurements: u64,
    pub invalid_measurements: u64,
    pub imported_ignored: u64,
    pub snapshots_evicted: u64,
    pub snapshot_rejections: u64,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MetricLimits {
    pub max_families: usize,
    pub max_series: usize,
    pub max_bytes: usize,
    pub max_snapshots: usize,
    pub max_snapshot_bytes: usize,
}
impl Default for MetricLimits {
    fn default() -> Self {
        Self {
            max_families: 128,
            max_series: 2048,
            max_bytes: 8 * 1024 * 1024,
            max_snapshots: 12,
            max_snapshot_bytes: 16 * 1024 * 1024,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MetricSnapshot {
    pub revision: u64,
    pub epoch: u64,
    pub start: Option<SnapshotTime>,
    pub end: Option<SnapshotTime>,
    pub schema: HistogramSchema,
    pub labels: LabelCatalog,
    pub series: BTreeMap<SeriesKey, MetricValue>,
    pub gauge_scope: GaugeScope,
    pub gauge_scopes: BTreeMap<SeriesKey, GaugeScope>,
    pub health: MetricHealth,
    pub limits: MetricLimits,
    pub complete: bool,
    pub temporality: Temporality,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Temporality {
    Cumulative,
    Delta,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum MetricError {
    InvalidConfig,
    UnknownWindow,
    Incompatible,
    ReversedWindow,
    CounterReset,
    Overflow,
    WrongKind,
    InvalidLabels,
}
impl MetricSnapshot {
    pub fn get(&self, family: MetricFamily, labels: Labels) -> Option<&MetricValue> {
        self.series.get(&SeriesKey { family, labels })
    }
    pub fn histogram(&self, family: MetricFamily, labels: Labels) -> Option<&Histogram> {
        match self.get(family, labels) {
            Some(MetricValue::Histogram(h)) => Some(h),
            _ => None,
        }
    }
    pub fn estimated_bytes(&self) -> usize {
        std::mem::size_of::<Self>()
            + self.labels.bytes()
            + self.schema.finite_bounds_ns.capacity() * 8
            + self.series.values().map(value_bytes).sum::<usize>()
    }
    pub fn window(start: &Self, end: &Self) -> Result<Self, MetricError> {
        if start.epoch != end.epoch
            || start.schema != end.schema
            || start.labels != end.labels
            || start.start != end.start
            || start.gauge_scope != end.gauge_scope
            || start
                .gauge_scopes
                .iter()
                .any(|(key, scope)| end.gauge_scopes.get(key) != Some(scope))
            || start.temporality != Temporality::Cumulative
            || end.temporality != Temporality::Cumulative
        {
            return Err(MetricError::Incompatible);
        }
        if start.revision > end.revision {
            return Err(MetricError::ReversedWindow);
        }
        if let (Some(a), Some(b)) = (start.end, end.end) {
            if a.domain != b.domain {
                return Err(MetricError::Incompatible);
            }
            if a.nanos > b.nanos {
                return Err(MetricError::ReversedWindow);
            }
        }
        let mut result = end.clone();
        result.start = start.end;
        result.temporality = Temporality::Delta;
        for (key, value) in &mut result.series {
            if let Some(prior) = start.series.get(key) {
                match (value, prior) {
                    (MetricValue::Counter(v), MetricValue::Counter(p)) => {
                        *v = v.checked_sub(*p).ok_or(MetricError::CounterReset)?
                    }
                    (MetricValue::Gauge(_), MetricValue::Gauge(_)) => {}
                    (MetricValue::Histogram(v), MetricValue::Histogram(p)) => {
                        v.count = v
                            .count
                            .checked_sub(p.count)
                            .ok_or(MetricError::CounterReset)?;
                        v.sum_ns = v
                            .sum_ns
                            .checked_sub(p.sum_ns)
                            .ok_or(MetricError::CounterReset)?;
                        for (b, pb) in v.buckets.iter_mut().zip(p.buckets) {
                            *b = b.checked_sub(pb).ok_or(MetricError::CounterReset)?;
                        }
                    }
                    _ => return Err(MetricError::Incompatible),
                }
            }
        }
        if start.series.keys().any(|key| !end.series.contains_key(key)) {
            return Err(MetricError::CounterReset);
        }
        result.complete &= start.complete;
        Ok(result)
    }
    /// Merge disjoint producer populations with identical interval/schema.
    /// Callers must establish disjointness; this cannot deduplicate exports.
    pub fn merge_disjoint(&mut self, other: &Self) -> Result<(), MetricError> {
        if self.epoch != other.epoch
            || self.start != other.start
            || self.end != other.end
            || self.schema != other.schema
            || self.labels != other.labels
            || self.temporality != other.temporality
            || self.gauge_scope != other.gauge_scope
        {
            return Err(MetricError::Incompatible);
        }
        let mut result = self.clone();
        for (key, scope) in &other.gauge_scopes {
            if result.gauge_scopes.get(key).is_some_and(|s| s != scope) {
                return Err(MetricError::Incompatible);
            }
            result.gauge_scopes.insert(*key, *scope);
        }
        for (key, value) in &other.series {
            if let Some(dest) = result.series.get_mut(key) {
                match (dest, value) {
                    (MetricValue::Counter(v), MetricValue::Counter(p))
                    | (MetricValue::Gauge(v), MetricValue::Gauge(p)) => {
                        *v = v.checked_add(*p).ok_or(MetricError::Overflow)?
                    }
                    (MetricValue::Histogram(v), MetricValue::Histogram(p)) => {
                        v.count = v.count.checked_add(p.count).ok_or(MetricError::Overflow)?;
                        v.sum_ns = v
                            .sum_ns
                            .checked_add(p.sum_ns)
                            .ok_or(MetricError::Overflow)?;
                        for (b, pb) in v.buckets.iter_mut().zip(p.buckets) {
                            *b = b.checked_add(pb).ok_or(MetricError::Overflow)?;
                        }
                    }
                    _ => return Err(MetricError::Incompatible),
                }
            } else {
                result.series.insert(*key, value.clone());
            }
        }
        if result
            .series
            .keys()
            .map(|k| k.family)
            .collect::<std::collections::BTreeSet<_>>()
            .len()
            > self.limits.max_families
            || result.series.len() > self.limits.max_series
            || result.estimated_bytes() > self.limits.max_bytes
        {
            return Err(MetricError::Overflow);
        }
        result.health = MetricHealth {
            rejected_series: self
                .health
                .rejected_series
                .checked_add(other.health.rejected_series)
                .ok_or(MetricError::Overflow)?,
            invalid_labels: self
                .health
                .invalid_labels
                .checked_add(other.health.invalid_labels)
                .ok_or(MetricError::Overflow)?,
            arithmetic_errors: self
                .health
                .arithmetic_errors
                .checked_add(other.health.arithmetic_errors)
                .ok_or(MetricError::Overflow)?,
            omitted_measurements: self
                .health
                .omitted_measurements
                .checked_add(other.health.omitted_measurements)
                .ok_or(MetricError::Overflow)?,
            invalid_measurements: self
                .health
                .invalid_measurements
                .checked_add(other.health.invalid_measurements)
                .ok_or(MetricError::Overflow)?,
            imported_ignored: self
                .health
                .imported_ignored
                .checked_add(other.health.imported_ignored)
                .ok_or(MetricError::Overflow)?,
            snapshots_evicted: self
                .health
                .snapshots_evicted
                .checked_add(other.health.snapshots_evicted)
                .ok_or(MetricError::Overflow)?,
            snapshot_rejections: self
                .health
                .snapshot_rejections
                .checked_add(other.health.snapshot_rejections)
                .ok_or(MetricError::Overflow)?,
        };
        result.complete &= other.complete;
        *self = result;
        Ok(())
    }
    /// Production views omit debugger-interfered and non-live populations.
    pub fn production(&self) -> Self {
        let mut s = self.clone();
        s.gauge_scopes.retain(|k, _| {
            !k.labels.debugger_affected && k.labels.origin == MeasurementOrigin::Live
        });
        s.series.retain(|k, _| {
            !k.labels.debugger_affected && k.labels.origin == MeasurementOrigin::Live
        });
        s
    }
}
fn value_bytes(v: &MetricValue) -> usize {
    std::mem::size_of::<SeriesKey>()
        + std::mem::size_of::<MetricValue>()
        + 64
        + if matches!(v, MetricValue::Histogram(_)) {
            std::mem::size_of::<Histogram>()
        } else if matches!(v, MetricValue::Gauge(_)) {
            128
        } else {
            0
        }
}
/// Exporter integration receives cumulative snapshots, not replayed events.
/// The host initializes its recorder; no global recorder is installed here.
pub trait MetricSink {
    type Error;
    fn export(&mut self, snapshot: &MetricSnapshot) -> Result<(), Self::Error>;
}
pub struct MetricsStore {
    snapshot: MetricSnapshot,
    history: VecDeque<MetricSnapshot>,
    history_bytes: usize,
    bytes: usize,
    default_gauge_scope: GaugeScope,
    // Unknown/invalid endpoints must not erase the previous monotonic boundary.
    last_valid_end: Option<SnapshotTime>,
}
impl MetricsStore {
    pub fn new(
        epoch: u64,
        start: Option<SnapshotTime>,
        labels: LabelCatalog,
        schema: HistogramSchema,
        limits: MetricLimits,
        gauge_scope: GaugeScope,
    ) -> Result<Self, MetricError> {
        if epoch == 0
            || !labels.valid()
            || schema.id == 0
            || schema.finite_bounds_ns.len() > 64
            || schema.finite_bounds_ns.windows(2).any(|w| w[0] >= w[1])
            || limits.max_families == 0
            || limits.max_families > 128
            || limits.max_series == 0
            || limits.max_snapshots == 0
            || labels.bytes() + std::mem::size_of::<MetricSnapshot>() > limits.max_bytes
        {
            return Err(MetricError::InvalidConfig);
        }
        let snapshot = MetricSnapshot {
            revision: 0,
            epoch,
            start,
            end: start,
            schema,
            labels,
            series: BTreeMap::new(),
            gauge_scope,
            gauge_scopes: BTreeMap::new(),
            health: MetricHealth::default(),
            limits,
            complete: true,
            temporality: Temporality::Cumulative,
        };
        let bytes = snapshot.estimated_bytes();
        if bytes > snapshot.limits.max_bytes {
            return Err(MetricError::InvalidConfig);
        }
        Ok(Self {
            snapshot,
            history: VecDeque::new(),
            history_bytes: 0,
            bytes,
            default_gauge_scope: gauge_scope,
            last_valid_end: start,
        })
    }
    pub(crate) fn revision(&self) -> u64 {
        self.snapshot.revision
    }
    pub fn health(&self) -> &MetricHealth {
        &self.snapshot.health
    }
    pub fn health_mut(&mut self) -> &mut MetricHealth {
        &mut self.snapshot.health
    }
    pub fn labels_valid(&self, l: Labels) -> bool {
        self.snapshot.labels.accepts(l)
    }
    pub fn mark_incomplete(&mut self) {
        self.snapshot.complete = false;
    }
    fn value(&mut self, family: MetricFamily, labels: Labels) -> Option<&mut MetricValue> {
        if !labels.origin.ingests_live() {
            self.snapshot.health.imported_ignored =
                self.snapshot.health.imported_ignored.saturating_add(1);
            return None;
        }
        if !self.labels_valid(labels) {
            self.snapshot.health.invalid_labels =
                self.snapshot.health.invalid_labels.saturating_add(1);
            self.snapshot.complete = false;
            return None;
        }
        let key = SeriesKey { family, labels };
        if !self.snapshot.series.contains_key(&key) {
            let value = match family.descriptor().kind {
                MetricKind::Counter => MetricValue::Counter(0),
                MetricKind::Gauge => MetricValue::Gauge(0),
                MetricKind::Histogram => MetricValue::Histogram(Box::default()),
            };
            let new_family = !self.snapshot.series.keys().any(|k| k.family == family);
            let family_count = self
                .snapshot
                .series
                .keys()
                .map(|k| k.family)
                .collect::<std::collections::BTreeSet<_>>()
                .len();
            let bytes = value_bytes(&value);
            if self.snapshot.series.len() >= self.snapshot.limits.max_series
                || bytes > self.snapshot.limits.max_bytes.saturating_sub(self.bytes)
                || (new_family && family_count >= self.snapshot.limits.max_families)
            {
                self.snapshot.health.rejected_series =
                    self.snapshot.health.rejected_series.saturating_add(1);
                self.snapshot.complete = false;
                return None;
            }
            self.bytes += bytes;
            if matches!(value, MetricValue::Gauge(_)) {
                self.snapshot
                    .gauge_scopes
                    .insert(key, self.default_gauge_scope);
                self.refresh_gauge_scope();
            }
            self.snapshot.series.insert(key, value);
        }
        self.snapshot.revision = self.snapshot.revision.saturating_add(1);
        // New lifecycle values no longer belong to the last queried endpoint.
        // Retain the independent watermark for validation of a future snapshot.
        self.snapshot.end = None;
        self.snapshot.series.get_mut(&key)
    }
    pub fn increment(&mut self, family: MetricFamily, labels: Labels, n: u64) {
        if family.descriptor().kind != MetricKind::Counter {
            self.snapshot.health.arithmetic_errors += 1;
            self.snapshot.complete = false;
            return;
        }
        let ok = match self.value(family, labels) {
            Some(MetricValue::Counter(v)) => {
                if let Some(sum) = v.checked_add(n) {
                    *v = sum;
                    true
                } else {
                    false
                }
            }
            None => return,
            _ => false,
        };
        if !ok {
            self.snapshot.health.arithmetic_errors += 1;
            self.snapshot.complete = false;
        }
    }
    pub fn gauge_delta(&mut self, family: MetricFamily, labels: Labels, delta: i64) {
        if family.descriptor().kind != MetricKind::Gauge {
            self.snapshot.health.arithmetic_errors += 1;
            self.snapshot.complete = false;
            return;
        }
        let ok = match self.value(family, labels) {
            Some(MetricValue::Gauge(v)) => {
                let next = if delta >= 0 {
                    v.checked_add(delta as u64)
                } else {
                    v.checked_sub(delta.unsigned_abs())
                };
                if let Some(n) = next {
                    *v = n;
                    true
                } else {
                    false
                }
            }
            None => return,
            _ => false,
        };
        if !ok {
            self.snapshot.health.arithmetic_errors += 1;
            self.snapshot.complete = false;
            self.snapshot
                .gauge_scopes
                .insert(SeriesKey { family, labels }, GaugeScope::Unknown);
            self.refresh_gauge_scope();
        }
    }
    pub fn set_gauge(&mut self, family: MetricFamily, labels: Labels, value: u64) {
        if family.descriptor().kind != MetricKind::Gauge {
            self.snapshot.health.arithmetic_errors += 1;
            self.snapshot.complete = false;
            return;
        }
        if let Some(MetricValue::Gauge(v)) = self.value(family, labels) {
            *v = value;
        }
    }
    pub fn set_gauge_scope(&mut self, scope: GaugeScope) {
        self.default_gauge_scope = scope;
        for value in self.snapshot.gauge_scopes.values_mut() {
            *value = scope;
        }
        self.snapshot.gauge_scope = scope;
    }
    fn refresh_gauge_scope(&mut self) {
        self.snapshot.gauge_scope = match self.snapshot.gauge_scopes.values().next().copied() {
            Some(scope) if self.snapshot.gauge_scopes.values().all(|s| *s == scope) => scope,
            Some(_) => GaugeScope::Unknown,
            None => self.default_gauge_scope,
        };
    }
    pub fn set_gauge_scope_for(&mut self, labels: Labels, scope: GaugeScope) {
        for (key, value) in &mut self.snapshot.gauge_scopes {
            if key.labels == labels {
                *value = scope;
            }
        }
        self.refresh_gauge_scope();
    }
    pub fn observe(&mut self, family: MetricFamily, labels: Labels, nanos: u64) {
        if family.descriptor().kind != MetricKind::Histogram {
            self.snapshot.health.arithmetic_errors += 1;
            self.snapshot.complete = false;
            return;
        }
        let mut bounds = [0u64; 64];
        let len = self.snapshot.schema.finite_bounds_ns.len();
        bounds[..len].copy_from_slice(&self.snapshot.schema.finite_bounds_ns);
        let ok = match self.value(family, labels) {
            Some(MetricValue::Histogram(h)) => h.add(&bounds[..len], nanos),
            None => return,
            _ => false,
        };
        if !ok {
            self.snapshot.health.arithmetic_errors += 1;
            self.snapshot.complete = false;
        }
    }
    pub fn snapshot(&mut self, end: Option<SnapshotTime>) -> MetricSnapshot {
        match self.try_snapshot(end, |_| Ok::<(), std::convert::Infallible>(())) {
            Ok(snapshot) => snapshot,
            Err(never) => match never {},
        }
    }
    /// Stage a single snapshot and its history eviction plan. Validation may
    /// reject transport/query work without invalidating acknowledged windows.
    /// The retained history is inspected in place, never cloned for rollback.
    pub(crate) fn try_snapshot<E>(
        &mut self,
        end: Option<SnapshotTime>,
        validate: impl FnOnce(&MetricSnapshot) -> Result<(), E>,
    ) -> Result<MetricSnapshot, E> {
        let mut staged = self.snapshot.clone();
        staged.revision = staged.revision.saturating_add(1);
        let mut last_valid_end = self.last_valid_end;
        if let Some(end) = end {
            if staged
                .start
                .is_some_and(|start| start.domain != end.domain || start.nanos > end.nanos)
                || last_valid_end
                    .is_some_and(|last| last.domain != end.domain || last.nanos > end.nanos)
            {
                staged.health.invalid_measurements =
                    staged.health.invalid_measurements.saturating_add(1);
                staged.complete = false;
                staged.end = None;
            } else {
                staged.end = Some(end);
                last_valid_end = Some(end);
            }
        } else {
            staged.end = None;
        }
        let bytes = staged.estimated_bytes();
        let retain = bytes <= staged.limits.max_snapshot_bytes;
        let mut evictions = 0;
        let mut retained_bytes = self.history_bytes;
        if !retain {
            staged.health.snapshot_rejections = staged.health.snapshot_rejections.saturating_add(1);
        } else {
            for old in &self.history {
                if self.history.len() - evictions < staged.limits.max_snapshots
                    && bytes
                        <= staged
                            .limits
                            .max_snapshot_bytes
                            .saturating_sub(retained_bytes)
                {
                    break;
                }
                retained_bytes -= old.estimated_bytes();
                evictions += 1;
                staged.health.snapshots_evicted = staged.health.snapshots_evicted.saturating_add(1);
            }
        }
        validate(&staged)?;
        // Lifecycle series cannot change during &mut access. Only these snapshot
        // metadata fields are committed; preserve their existing allocations.
        self.snapshot.revision = staged.revision;
        self.snapshot.end = staged.end;
        self.snapshot.health = staged.health.clone();
        self.snapshot.complete = staged.complete;
        self.last_valid_end = last_valid_end;
        if retain {
            for _ in 0..evictions {
                self.history.pop_front();
            }
            self.history_bytes = retained_bytes + bytes;
            self.history.push_back(staged.clone());
        }
        Ok(staged)
    }
    pub fn current(&self) -> MetricSnapshot {
        self.snapshot.clone()
    }
    pub fn available_windows(&self) -> Vec<u64> {
        self.history.iter().map(|s| s.revision).collect()
    }
    pub fn window(&self, start: u64, end: u64) -> Result<MetricSnapshot, MetricError> {
        let a = self
            .history
            .iter()
            .find(|s| s.revision == start)
            .ok_or(MetricError::UnknownWindow)?;
        let b = self
            .history
            .iter()
            .find(|s| s.revision == end)
            .ok_or(MetricError::UnknownWindow)?;
        MetricSnapshot::window(a, b)
    }
}

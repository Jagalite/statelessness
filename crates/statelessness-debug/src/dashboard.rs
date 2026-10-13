//! Passive default views over the existing metrics/effect ledger. Missing
//! endpoints stay unknown; request identities never become aggregate labels.
use crate::effects::{EffectTimeline, RequestOrigin, TelemetryHealth};
use crate::metrics::{Labels, MetricFamily, MetricSnapshot, MetricValue, Quantile, Temporality};
use stateless::evidence::TraceV2;
use std::fmt::Write;

pub const FAMILIES: &[MetricFamily] = &[
    MetricFamily::Requests,
    MetricFamily::Admissions,
    MetricFamily::Attempts,
    MetricFamily::QueueWait,
    MetricFamily::AttemptElapsed,
    MetricFamily::DeliveryLag,
    MetricFamily::CompletionProcessing,
    MetricFamily::Settlement,
    MetricFamily::Unresolved,
    MetricFamily::Running,
    MetricFamily::ReadyDepth,
    MetricFamily::CompletionDepth,
    MetricFamily::ScheduledBackoff,
    MetricFamily::InterAttemptGap,
    MetricFamily::CancellationRequests,
    MetricFamily::CancellationAcknowledgements,
    MetricFamily::Dropped,
    MetricFamily::InvalidMeasurements,
];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Row {
    pub family: MetricFamily,
    pub labels: Labels,
    /// None means this series has no observation. It is never synthesized as zero.
    pub value: Option<MetricValue>,
    pub gauge_scope: Option<crate::metrics::GaugeScope>,
    pub p50: Option<Quantile>,
    pub p95: Option<Quantile>,
    pub p99: Option<Quantile>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Dashboard {
    pub epoch: u64,
    pub revision: u64,
    pub temporality: Temporality,
    pub start: Option<crate::metrics::SnapshotTime>,
    pub end: Option<crate::metrics::SnapshotTime>,
    pub health: TelemetryHealth,
    pub metric_health: crate::metrics::MetricHealth,
    pub rows: Vec<Row>,
    pub truncated: bool,
    /// Health completeness only. A particular metric may still have no samples.
    pub observation_complete: bool,
}
impl Dashboard {
    /// Numeric, catalog-bound labels select series. The supplied health endpoint
    /// may be collected after the metric snapshot; this view promises no atomicity.
    pub fn from_snapshot(
        snapshot: &MetricSnapshot,
        health: &TelemetryHealth,
        labels: &[Labels],
        max_rows: usize,
    ) -> Self {
        let mut rows = Vec::new();
        for label in labels {
            for family in FAMILIES {
                if rows.len() == max_rows {
                    break;
                }
                let value = snapshot.get(*family, *label).cloned();
                let q = |n| match &value {
                    Some(MetricValue::Histogram(h)) => h.quantile(&snapshot.schema, n, 100),
                    _ => None,
                };
                let gauge_scope = matches!(&value, Some(MetricValue::Gauge(_))).then(|| {
                    snapshot
                        .gauge_scopes
                        .get(&crate::metrics::SeriesKey {
                            family: *family,
                            labels: *label,
                        })
                        .copied()
                        .unwrap_or(snapshot.gauge_scope)
                });
                rows.push(Row {
                    family: *family,
                    labels: *label,
                    p50: q(50),
                    p95: q(95),
                    p99: q(99),
                    value,
                    gauge_scope,
                });
            }
            if rows.len() == max_rows {
                break;
            }
        }
        let truncated = labels
            .len()
            .checked_mul(FAMILIES.len())
            .is_none_or(|total| total > rows.len());
        let mh = &snapshot.health;
        let observation_complete = snapshot.complete
            && !truncated
            && metric_health_complete(mh)
            && metric_health_complete(&health.metric)
            && health.pause_interval_evictions == 0
            && health.abandoned == 0
            && health.duplicate_observations == 0
            && health.callback_faults == 0
            && health.clock_failures == 0
            && health.destructor_hook_losses == 0
            && health.invalid_lifecycle == 0
            && health.detail_evictions == 0
            && health.detail_rejections == 0
            && health.detail_event_drops == 0;
        Self {
            epoch: snapshot.epoch,
            revision: snapshot.revision,
            temporality: snapshot.temporality,
            start: snapshot.start,
            end: snapshot.end,
            health: health.clone(),
            metric_health: mh.clone(),
            rows,
            truncated,
            observation_complete,
        }
    }
    /// Payload-free JSON with bounded output and numeric dimensions. No raw model
    /// IDs, request IDs, correlation strings, event details or inspector callbacks.
    pub fn to_json(&self, max_bytes: usize) -> Result<String, std::fmt::Error> {
        let mut out = BoundedText {
            text: String::new(),
            maximum: max_bytes,
        };
        let time = |t: Option<crate::metrics::SnapshotTime>| match t {
            None => "null".into(),
            Some(t) => format!("{{\"clock_domain\":{},\"nanos\":{}}}", t.domain, t.nanos),
        };
        write!(
            out,
            "{{\"schema\":\"statelessness.effect-dashboard/1\",\"epoch\":{},\"revision\":{},\"temporality\":\"{}\",\"start\":{},\"end\":{},\"truncated\":{},\"observation_complete\":{},\"health\":{{\"dropped_detail_events\":{},\"detail_evictions\":{},\"detail_rejections\":{},\"clock_failures\":{},\"callback_faults\":{},\"destructor_hook_losses\":{},\"invalid_lifecycle\":{},\"active_effect_handles\":{},\"omitted_measurements\":{},\"invalid_measurements\":{},\"rejected_series\":{}",
            self.epoch,
            self.revision,
            match self.temporality {
                Temporality::Cumulative => "cumulative",
                Temporality::Delta => "delta",
            },
            time(self.start),
            time(self.end),
            self.truncated,
            self.observation_complete,
            self.health.detail_event_drops,
            self.health.detail_evictions,
            self.health.detail_rejections,
            self.health.clock_failures,
            self.health.callback_faults,
            self.health.destructor_hook_losses,
            self.health.invalid_lifecycle,
            self.health.active_effect_handles,
            self.metric_health.omitted_measurements,
            self.metric_health.invalid_measurements,
            self.metric_health.rejected_series
        )?;
        write!(
            out,
            ",\"invalid_labels\":{},\"arithmetic_errors\":{},\"imported_ignored\":{},\"snapshots_evicted\":{},\"snapshot_rejections\":{},\"duplicate_observations\":{},\"duplicate_deliveries\":{},\"abandoned\":{},\"pause_interval_evictions\":{},\"debugger_affected_measurements\":{},\"retained_detail_records\":{},\"retained_detail_bytes\":{},\"effect_handle_bytes\":{},\"attempt_handle_bytes\":{},\"delivery_handle_bytes\":{},\"unobserved_native_debugger_interference_possible\":{},\"host_metric_health\":",
            self.metric_health.invalid_labels,
            self.metric_health.arithmetic_errors,
            self.metric_health.imported_ignored,
            self.metric_health.snapshots_evicted,
            self.metric_health.snapshot_rejections,
            self.health.duplicate_observations,
            self.health.duplicate_deliveries,
            self.health.abandoned,
            self.health.pause_interval_evictions,
            self.health.debugger_affected_measurements,
            self.health.retained_detail_records,
            self.health.retained_detail_bytes,
            self.health.effect_handle_bytes,
            self.health.attempt_handle_bytes,
            self.health.delivery_handle_bytes,
            self.health.unobserved_native_debugger_interference_possible
        )?;
        write_metric_health(&mut out, &self.health.metric)?;
        out.write_str("},\"rows\":[")?;
        for (i, row) in self.rows.iter().enumerate() {
            if i > 0 {
                out.write_char(',')?;
            }
            write!(
                out,
                "{{\"metric\":\"{}\",\"operation\":{},\"component\":{},\"worker_pool\":{},\"outcome\":\"{:?}\",\"measurement_origin\":\"{:?}\",\"debugger_affected\":{},\"value\":",
                row.family.descriptor().name,
                row.labels.operation,
                row.labels.component,
                row.labels.worker_pool,
                row.labels.outcome,
                row.labels.origin,
                row.labels.debugger_affected
            )?;
            match &row.value {
                None => out.write_str("null")?,
                Some(MetricValue::Counter(v) | MetricValue::Gauge(v)) => write!(out, "{v}")?,
                Some(MetricValue::Histogram(h)) => {
                    write!(out, "{{\"count\":{},\"sum_ns\":{}}}", h.count, h.sum_ns)?
                }
            }
            write!(out, ",\"gauge_scope\":")?;
            match row.gauge_scope {
                None => out.write_str("null")?,
                Some(scope) => write!(out, "\"{scope:?}\"")?,
            }
            for (name, q) in [("p50", row.p50), ("p95", row.p95), ("p99", row.p99)] {
                write!(out, ",\"{name}\":")?;
                match q {
                    None => out.write_str("null")?,
                    Some(q) => write!(
                        out,
                        "{{\"upper_bound_ns\":{},\"overflow_bucket\":{},\"observations\":{},\"low_sample_warning\":{}}}",
                        q.upper_bound_ns
                            .map_or_else(|| "null".into(), |n| n.to_string()),
                        q.upper_bound_ns.is_none(),
                        q.observations,
                        q.low_sample_warning
                    )?,
                }
            }
            out.write_char('}')?;
        }
        out.write_str("]}")?;
        Ok(out.text)
    }
}
fn metric_health_complete(health: &crate::metrics::MetricHealth) -> bool {
    health.rejected_series == 0
        && health.invalid_labels == 0
        && health.arithmetic_errors == 0
        && health.omitted_measurements == 0
        && health.invalid_measurements == 0
        && health.imported_ignored == 0
        && health.snapshots_evicted == 0
        && health.snapshot_rejections == 0
}
fn write_metric_health(
    out: &mut impl Write,
    health: &crate::metrics::MetricHealth,
) -> std::fmt::Result {
    write!(
        out,
        "{{\"rejected_series\":{},\"invalid_labels\":{},\"arithmetic_errors\":{},\"omitted_measurements\":{},\"invalid_measurements\":{},\"imported_ignored\":{},\"snapshots_evicted\":{},\"snapshot_rejections\":{}}}",
        health.rejected_series,
        health.invalid_labels,
        health.arithmetic_errors,
        health.omitted_measurements,
        health.invalid_measurements,
        health.imported_ignored,
        health.snapshots_evicted,
        health.snapshot_rejections
    )
}
struct BoundedText {
    text: String,
    maximum: usize,
}
impl Write for BoundedText {
    fn write_str(&mut self, value: &str) -> std::fmt::Result {
        if self
            .text
            .len()
            .checked_add(value.len())
            .is_none_or(|n| n > self.maximum)
        {
            return Err(std::fmt::Error);
        }
        self.text
            .try_reserve(value.len())
            .map_err(|_| std::fmt::Error)?;
        self.text.push_str(value);
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Correlation {
    /// The origin names an existing retained output. This validates host labels,
    /// not the truth of an external effect execution or authenticated provenance.
    Retained {
        local_sequence: usize,
        output: u32,
        detail_complete: bool,
    },
    BeforeCheckpoint {
        origin: RequestOrigin,
    },
    MissingIdentity,
    Conflict(&'static str),
}
/// Bridge the existing numeric host origin to v2 labels. Use decimal run/machine
/// labels when recording this bridge; other label schemes remain uncorrelated.
pub fn correlate(evidence: &TraceV2, timeline: &EffectTimeline) -> Correlation {
    let origin = timeline.origin;
    if evidence.provenance.run.is_empty() {
        return Correlation::MissingIdentity;
    }
    if timeline.effect.run != origin.run
        || timeline.effect.epoch != origin.epoch
        || evidence.provenance.run != origin.run.to_string()
        || evidence.provenance.epoch != origin.epoch
    {
        return Correlation::Conflict("run or epoch");
    }
    if origin.transition_sequence == 0 {
        return Correlation::Conflict("zero transition sequence");
    }
    if origin.transition_sequence <= evidence.checkpoint_after {
        return Correlation::BeforeCheckpoint { origin };
    }
    let Ok(index) = usize::try_from(origin.transition_sequence - evidence.checkpoint_after - 1)
    else {
        return Correlation::Conflict("sequence outside evidence");
    };
    let Some(step) = evidence.steps.get(index) else {
        return Correlation::Conflict("sequence outside evidence");
    };
    let Some(machine) = &step.causal.machine else {
        return Correlation::MissingIdentity;
    };
    if machine != &origin.machine.to_string() {
        return Correlation::Conflict("machine");
    }
    if step.sequence != origin.transition_sequence
        || evidence
            .trace
            .steps
            .get(index)
            .is_none_or(|s| origin.output_index as usize >= s.outputs.len())
    {
        return Correlation::Conflict("output outside evidence");
    }
    Correlation::Retained {
        local_sequence: index + 1,
        output: origin.output_index,
        detail_complete: timeline.complete,
    }
}

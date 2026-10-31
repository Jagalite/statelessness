package debug

import (
	"encoding/json"
	"errors"
	s "github.com/Jagalite/statelessness/go"
	"math/big"
	"strconv"
)

var dashboardFamilies = []MetricFamily{Requests, Admissions, Attempts, QueueWait, AttemptElapsed, DeliveryLag, CompletionProcessing, Settlement, Unresolved, Running, ReadyDepth, CompletionDepth, ScheduledBackoff, InterAttemptGap, CancellationRequests, CancellationAcknowledgements, Dropped, InvalidMeasurements}

type DashboardRow struct {
	Family        MetricFamily
	Labels        Labels
	Value         *MetricValue
	GaugeScope    *GaugeScope
	P50, P95, P99 *Quantile
}
type Dashboard struct {
	Epoch, Revision                uint64
	Temporality                    string
	Start, End                     *LocalInstant
	Health                         TelemetryHealth
	MetricHealth                   MetricHealth
	Rows                           []DashboardRow
	Truncated, ObservationComplete bool
}

func DashboardFromSnapshot(s MetricSnapshot, h TelemetryHealth, labels []Labels, maxRows int) Dashboard {
	d := Dashboard{Epoch: s.Epoch, Revision: s.Revision, Temporality: s.Temporality, Start: s.Start, End: s.End, Health: h, MetricHealth: s.Health}
	if d.Start != nil {
		v := *d.Start
		d.Start = &v
	}
	if d.End != nil {
		v := *d.End
		d.End = &v
	}
	if maxRows < 0 {
		maxRows = 0
	}
	for _, l := range labels {
		for _, f := range dashboardFamilies {
			if len(d.Rows) >= maxRows {
				break
			}
			r := DashboardRow{Family: f, Labels: l}
			if v, ok := s.Series[SeriesKey{f, l}]; ok {
				if v.Histogram != nil {
					copy := *v.Histogram
					copy.SumNS = *new(big.Int).Set(&v.Histogram.SumNS)
					v.Histogram = &copy
					r.P50 = v.Histogram.Quantile(s.Schema, 50, 100)
					r.P95 = v.Histogram.Quantile(s.Schema, 95, 100)
					r.P99 = v.Histogram.Quantile(s.Schema, 99, 100)
				}
				r.Value = &v
				if v.Kind == GaugeMetric {
					scope := s.GaugeScope
					if scoped, ok := s.GaugeScopes[SeriesKey{f, l}]; ok {
						scope = scoped
					}
					r.GaugeScope = &scope
				}
			}
			d.Rows = append(d.Rows, r)
		}
		if len(d.Rows) >= maxRows {
			break
		}
	}
	d.Truncated = len(labels) > 0 && len(dashboardFamilies) > 0 && len(d.Rows)/len(dashboardFamilies) < len(labels)
	d.ObservationComplete = s.Complete && !d.Truncated && s.Health.Complete() && h.Metric.Complete() && h.ClockFailures == 0 && h.CallbackFaults == 0 && h.InvalidLifecycle == 0 && h.DetailEvictions == 0 && h.DetailRejections == 0 && h.DetailEventDrops == 0 && h.PauseIntervalEvictions == 0 && h.Abandoned == 0 && h.DuplicateObservations == 0
	return d
}

// JSON emits only catalog numeric dimensions and aggregate values. Unknown
// series/quantiles remain null. The bound applies before any bytes are returned.
// Keys, order and nesting follow Rust dashboard.rs Dashboard::to_json: the
// snapshot's metric counters are flattened into "health" and the observer's
// metric health is "health.host_metric_health". Go cannot observe dropped
// handles, so active_effect_handles and destructor_hook_losses are null
// unless the health value supplies them.
func (d Dashboard) JSON(maxBytes int) ([]byte, error) {
	if maxBytes < 0 {
		return nil, errors.New("dashboard byte bound")
	}
	out := []byte{}
	appendBounded := func(b []byte) error {
		if len(b) > maxBytes-len(out) {
			return errors.New("dashboard byte bound")
		}
		out = append(out, b...)
		return nil
	}
	u := func(v uint64) string { return strconv.FormatUint(v, 10) }
	optional := func(v *uint64) string {
		if v == nil {
			return "null"
		}
		return u(*v)
	}
	instant := func(t *LocalInstant) string {
		if t == nil {
			return "null"
		}
		return `{"clock_domain":` + u(t.Domain) + `,"nanos":` + u(t.Nanos) + "}"
	}
	quoted := func(v string) (string, error) {
		b, err := json.Marshal(v)
		return string(b), err
	}
	metricHealth := func(m MetricHealth) string {
		return `{"rejected_series":` + u(m.RejectedSeries) + `,"invalid_labels":` + u(m.InvalidLabels) + `,"arithmetic_errors":` + u(m.ArithmeticErrors) + `,"omitted_measurements":` + u(m.OmittedMeasurements) + `,"invalid_measurements":` + u(m.InvalidMeasurements) + `,"imported_ignored":` + u(m.ImportedIgnored) + `,"snapshots_evicted":` + u(m.SnapshotsEvicted) + `,"snapshot_rejections":` + u(m.SnapshotRejections) + "}"
	}
	temporality, err := quoted(d.Temporality)
	if err != nil {
		return nil, err
	}
	h, mh := d.Health, d.MetricHealth
	header := `{"schema":"statelessness.effect-dashboard/1","epoch":` + u(d.Epoch) + `,"revision":` + u(d.Revision) + `,"temporality":` + temporality + `,"start":` + instant(d.Start) + `,"end":` + instant(d.End) + `,"truncated":` + strconv.FormatBool(d.Truncated) + `,"observation_complete":` + strconv.FormatBool(d.ObservationComplete) +
		`,"health":{"dropped_detail_events":` + u(h.DetailEventDrops) + `,"detail_evictions":` + u(h.DetailEvictions) + `,"detail_rejections":` + u(h.DetailRejections) + `,"clock_failures":` + u(h.ClockFailures) + `,"callback_faults":` + u(h.CallbackFaults) + `,"destructor_hook_losses":` + optional(h.DestructorHookLosses) + `,"invalid_lifecycle":` + u(h.InvalidLifecycle) + `,"active_effect_handles":` + optional(h.ActiveEffectHandles) + `,"omitted_measurements":` + u(mh.OmittedMeasurements) + `,"invalid_measurements":` + u(mh.InvalidMeasurements) + `,"rejected_series":` + u(mh.RejectedSeries) +
		`,"invalid_labels":` + u(mh.InvalidLabels) + `,"arithmetic_errors":` + u(mh.ArithmeticErrors) + `,"imported_ignored":` + u(mh.ImportedIgnored) + `,"snapshots_evicted":` + u(mh.SnapshotsEvicted) + `,"snapshot_rejections":` + u(mh.SnapshotRejections) + `,"duplicate_observations":` + u(h.DuplicateObservations) + `,"duplicate_deliveries":` + u(h.DuplicateDeliveries) + `,"abandoned":` + u(h.Abandoned) + `,"pause_interval_evictions":` + u(h.PauseIntervalEvictions) + `,"debugger_affected_measurements":` + u(h.DebuggerAffectedMeasurements) + `,"retained_detail_records":` + u(h.RetainedDetailRecords) + `,"retained_detail_bytes":` + u(h.RetainedDetailBytes) + `,"effect_handle_bytes":` + u(h.EffectHandleBytes) + `,"attempt_handle_bytes":` + u(h.AttemptHandleBytes) + `,"delivery_handle_bytes":` + u(h.DeliveryHandleBytes) + `,"unobserved_native_debugger_interference_possible":` + strconv.FormatBool(h.UnobservedNativeDebuggerInterferencePossible) + `,"host_metric_health":` + metricHealth(h.Metric) + `},"rows":[`
	if err = appendBounded([]byte(header)); err != nil {
		return nil, err
	}
	quantile := func(q *Quantile) string {
		if q == nil {
			return "null"
		}
		return `{"upper_bound_ns":` + optional(q.UpperBoundNS) + `,"overflow_bucket":` + strconv.FormatBool(q.OverflowBucket) + `,"observations":` + u(q.Observations) + `,"low_sample_warning":` + strconv.FormatBool(q.LowSampleWarning) + "}"
	}
	for i, r := range d.Rows {
		outcome, ok := map[Outcome]string{OutcomeNone: "None", OutcomeAccepted: "Accepted", OutcomeRejected: "Rejected", OutcomeSuccess: "Success", OutcomeFailure: "Failure", OutcomeCancelled: "Cancelled", OutcomeAbandoned: "Abandoned", OutcomeIgnored: "Ignored"}[r.Labels.Outcome]
		if !ok {
			return nil, ErrMetric
		}
		origin, ok := map[MeasurementOrigin]string{OriginLive: "Live", OriginDebugLive: "DebugControlledLive", OriginTestClock: "TestClock", OriginReplay: "ReplayHarness", OriginImported: "ImportedLive"}[r.Labels.Origin]
		if !ok {
			return nil, ErrMetric
		}
		gaugeScope := "null"
		if r.GaugeScope != nil {
			scope, ok := map[GaugeScope]string{CompleteParticipation: "CompleteParticipation", HostSnapshot: "HostSnapshot", ObservedSinceAttach: "ObservedSinceAttach", UnknownGaugeScope: "Unknown"}[*r.GaugeScope]
			if !ok {
				return nil, ErrMetric
			}
			gaugeScope = `"` + scope + `"`
		}
		value := "null"
		if r.Value != nil {
			switch r.Value.Kind {
			case HistogramMetric:
				if r.Value.Histogram == nil {
					return nil, ErrMetric
				}
				value = `{"count":` + u(r.Value.Histogram.Count) + `,"sum_ns":` + r.Value.Histogram.SumNS.String() + "}"
			default:
				value = u(r.Value.Number)
			}
		}
		metric, err := quoted(string(r.Family))
		if err != nil {
			return nil, err
		}
		row := `{"metric":` + metric + `,"operation":` + u(uint64(r.Labels.Operation)) + `,"component":` + u(uint64(r.Labels.Component)) + `,"worker_pool":` + u(uint64(r.Labels.WorkerPool)) + `,"outcome":"` + outcome + `","measurement_origin":"` + origin + `","debugger_affected":` + strconv.FormatBool(r.Labels.DebuggerAffected) + `,"value":` + value + `,"gauge_scope":` + gaugeScope + `,"p50":` + quantile(r.P50) + `,"p95":` + quantile(r.P95) + `,"p99":` + quantile(r.P99) + "}"
		if i > 0 {
			row = "," + row
		}
		if err = appendBounded([]byte(row)); err != nil {
			return nil, err
		}
	}
	if err := appendBounded([]byte("]}")); err != nil {
		return nil, err
	}
	return out, nil
}

type Correlation struct {
	Kind           string
	LocalSequence  uint64
	Output         uint32
	DetailComplete bool
	Origin         RequestOrigin
	Conflict       string
}

// Correlate validates host numeric labels against retained v2 outputs. It does
// not authenticate external work or prove a host's dispatch assertion.
func Correlate(e s.TraceV2, t EffectTimeline) Correlation {
	o := t.Origin
	conflict := func(s string) Correlation { return Correlation{Kind: "conflict", Conflict: s} }
	if e.Provenance.Run == "" {
		return Correlation{Kind: "missing-identity"}
	}
	if t.Effect.Run != o.Run || t.Effect.Epoch != o.Epoch || e.Provenance.Run != strconv.FormatUint(o.Run, 10) || e.Provenance.Epoch != o.Epoch {
		return conflict("run or epoch")
	}
	if o.TransitionSequence == 0 {
		return conflict("zero transition sequence")
	}
	if o.TransitionSequence <= e.CheckpointAfter {
		return Correlation{Kind: "before-checkpoint", Origin: o}
	}
	index := o.TransitionSequence - e.CheckpointAfter - 1
	if index >= uint64(len(e.Steps)) || index >= uint64(len(e.Trace.Steps)) {
		return conflict("sequence outside evidence")
	}
	step := e.Steps[index]
	if step.Causal.Machine == nil {
		return Correlation{Kind: "missing-identity"}
	}
	if *step.Causal.Machine != strconv.FormatUint(o.Machine, 10) {
		return conflict("machine")
	}
	if step.Sequence != o.TransitionSequence || uint64(o.OutputIndex) >= uint64(len(e.Trace.Steps[index].Outputs)) {
		return conflict("output outside evidence")
	}
	return Correlation{Kind: "retained", LocalSequence: index + 1, Output: o.OutputIndex, DetailComplete: t.Complete}
}

func DashboardMetricFamilies() []MetricFamily {
	return append([]MetricFamily(nil), dashboardFamilies...)
}

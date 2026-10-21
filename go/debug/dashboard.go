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
	}
	d.Truncated = len(labels) > 0 && len(dashboardFamilies) > 0 && len(d.Rows)/len(dashboardFamilies) < len(labels)
	d.ObservationComplete = s.Complete && !d.Truncated && s.Health.Complete() && h.Metric.Complete() && h.ClockFailures == 0 && h.CallbackFaults == 0 && h.InvalidLifecycle == 0 && h.DetailEvictions == 0 && h.DetailRejections == 0 && h.DetailEventDrops == 0 && h.PauseIntervalEvictions == 0 && h.Abandoned == 0 && h.DuplicateObservations == 0
	return d
}

// JSON emits only catalog numeric dimensions and aggregate values. Unknown
// series/quantiles remain null. The bound applies before any bytes are returned.
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
	header := map[string]any{"schema": "statelessness.effect-dashboard/1", "epoch": d.Epoch, "revision": d.Revision, "temporality": d.Temporality, "start": d.Start, "end": d.End, "health": d.Health, "metric_health": d.MetricHealth, "truncated": d.Truncated, "observation_complete": d.ObservationComplete}
	b, e := json.Marshal(header)
	if e != nil {
		return nil, e
	}
	if e = appendBounded(b[:len(b)-1]); e != nil {
		return nil, e
	}
	if e = appendBounded([]byte(",\"rows\":[")); e != nil {
		return nil, e
	}
	for i, r := range d.Rows {
		var value any
		if r.Value != nil {
			switch r.Value.Kind {
			case HistogramMetric:
				if r.Value.Histogram == nil {
					return nil, ErrMetric
				}
				value = map[string]any{"count": r.Value.Histogram.Count, "sum_ns": json.Number(r.Value.Histogram.SumNS.String())}
			default:
				value = r.Value.Number
			}
		}
		row := map[string]any{"metric": r.Family, "operation": r.Labels.Operation, "component": r.Labels.Component, "worker_pool": r.Labels.WorkerPool, "outcome": r.Labels.Outcome, "measurement_origin": r.Labels.Origin, "debugger_affected": r.Labels.DebuggerAffected, "value": value, "gauge_scope": r.GaugeScope, "p50": r.P50, "p95": r.P95, "p99": r.P99}
		b, e = json.Marshal(row)
		if e != nil {
			return nil, e
		}
		if i > 0 {
			if e = appendBounded([]byte(",")); e != nil {
				return nil, e
			}
		}
		if e = appendBounded(b); e != nil {
			return nil, e
		}
	}
	if e = appendBounded([]byte("]}")); e != nil {
		return nil, e
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

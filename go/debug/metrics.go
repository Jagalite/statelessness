package debug

import (
	"errors"
	"math"
	"math/big"
	"reflect"
	"sort"
	"unicode/utf8"
)

type MeasurementOrigin string

const (
	OriginLive      MeasurementOrigin = "live"
	OriginDebugLive MeasurementOrigin = "debug-controlled-live"
	OriginTestClock MeasurementOrigin = "test-clock"
	OriginReplay    MeasurementOrigin = "replay-harness"
	OriginImported  MeasurementOrigin = "imported-live"
)

type Outcome string

const (
	OutcomeNone      Outcome = ""
	OutcomeAccepted  Outcome = "accepted"
	OutcomeRejected  Outcome = "rejected"
	OutcomeSuccess   Outcome = "success"
	OutcomeFailure   Outcome = "failure"
	OutcomeCancelled Outcome = "cancelled"
	OutcomeAbandoned Outcome = "abandoned"
	OutcomeIgnored   Outcome = "ignored"
)

type Labels struct {
	Operation, Component, WorkerPool uint16
	Outcome                          Outcome
	Origin                           MeasurementOrigin
	DebuggerAffected                 bool
}
type MetricKind string

const (
	CounterMetric   MetricKind = "counter"
	GaugeMetric     MetricKind = "gauge"
	HistogramMetric MetricKind = "histogram"
)

type MetricFamily string

const (
	Requests                     MetricFamily = "effect_requests_total"
	Admissions                   MetricFamily = "effect_admissions_total"
	Attempts                     MetricFamily = "effect_attempts_total"
	AttemptOutcomes              MetricFamily = "effect_attempt_outcomes_total"
	Resolutions                  MetricFamily = "effect_resolutions_total"
	Deliveries                   MetricFamily = "effect_deliveries_total"
	Unresolved                   MetricFamily = "effects_unresolved"
	Running                      MetricFamily = "effect_attempts_running"
	ReadyDepth                   MetricFamily = "effect_ready_queue_depth"
	CompletionDepth              MetricFamily = "completion_queue_depth"
	QueueWait                    MetricFamily = "effect_queue_wait_seconds"
	AttemptElapsed               MetricFamily = "effect_attempt_elapsed_seconds"
	Resolution                   MetricFamily = "effect_resolution_seconds"
	DeliveryLag                  MetricFamily = "effect_delivery_lag_seconds"
	EndToEnd                     MetricFamily = "effect_end_to_end_seconds"
	CompletionProcessing         MetricFamily = "completion_processing_seconds"
	AdmissionDelay               MetricFamily = "effect_admission_delay_seconds"
	ResolutionOverhead           MetricFamily = "effect_resolution_overhead_seconds"
	CompletionPreparation        MetricFamily = "completion_preparation_seconds"
	CompletionQueueWait          MetricFamily = "completion_queue_wait_seconds"
	EndToEndObserved             MetricFamily = "effect_end_to_end_observed_seconds"
	Settlement                   MetricFamily = "effect_settlement_seconds"
	ScheduledBackoff             MetricFamily = "effect_scheduled_backoff_seconds"
	EligibilityWait              MetricFamily = "effect_eligibility_wait_seconds"
	InterAttemptGap              MetricFamily = "effect_inter_attempt_gap_seconds"
	DuplicateObservations        MetricFamily = "effect_duplicate_observations_total"
	DuplicateDeliveries          MetricFamily = "effect_duplicate_deliveries_total"
	CancellationRequests         MetricFamily = "effect_cancellation_requests_total"
	CancellationAcknowledgements MetricFamily = "effect_cancellation_acknowledgements_total"
	Abandoned                    MetricFamily = "effect_abandoned_total"
	Dropped                      MetricFamily = "telemetry_dropped_total"
	InvalidMeasurements          MetricFamily = "measurement_invalid_total"
)

type MetricDescriptor struct {
	Family                  MetricFamily
	Name                    string
	Kind                    MetricKind
	Unit, Population, Reset string
	AllowedLabels           []string
}

func MetricCatalog() []MetricDescriptor {
	out := []MetricDescriptor{}
	for _, f := range []MetricFamily{Requests, Admissions, Attempts, AttemptOutcomes, Resolutions, Deliveries, DuplicateObservations, DuplicateDeliveries, CancellationRequests, CancellationAcknowledgements, Abandoned, Dropped, InvalidMeasurements} {
		out = append(out, metricDescriptor(f, CounterMetric, "count"))
	}
	for _, f := range []MetricFamily{Unresolved, Running, ReadyDepth, CompletionDepth} {
		out = append(out, metricDescriptor(f, GaugeMetric, "count"))
	}
	for _, f := range []MetricFamily{QueueWait, AttemptElapsed, Resolution, DeliveryLag, EndToEnd, CompletionProcessing, AdmissionDelay, ResolutionOverhead, CompletionPreparation, CompletionQueueWait, EndToEndObserved, Settlement, ScheduledBackoff, EligibilityWait, InterAttemptGap} {
		out = append(out, metricDescriptor(f, HistogramMetric, "seconds"))
	}
	return out
}
func metricKind(f MetricFamily) MetricKind {
	switch f {
	case Requests, Admissions, Attempts, AttemptOutcomes, Resolutions, Deliveries, DuplicateObservations, DuplicateDeliveries, CancellationRequests, CancellationAcknowledgements, Abandoned, Dropped, InvalidMeasurements:
		return CounterMetric
	case Unresolved, Running, ReadyDepth, CompletionDepth:
		return GaugeMetric
	case QueueWait, AttemptElapsed, Resolution, DeliveryLag, EndToEnd, CompletionProcessing, AdmissionDelay, ResolutionOverhead, CompletionPreparation, CompletionQueueWait, EndToEndObserved, Settlement, ScheduledBackoff, EligibilityWait, InterAttemptGap:
		return HistogramMetric
	}
	return ""
}

type LabelCatalog struct{ Operations, Components, WorkerPools []string }

func DefaultLabelCatalog() LabelCatalog {
	return LabelCatalog{[]string{"unspecified"}, []string{"host"}, []string{"default"}}
}
func (c LabelCatalog) valid() bool {
	for _, xs := range [][]string{c.Operations, c.Components, c.WorkerPools} {
		if len(xs) == 0 || len(xs) > 256 {
			return false
		}
		seen := map[string]bool{}
		for _, s := range xs {
			if len(s) == 0 || len(s) > 64 || !utf8.ValidString(s) || seen[s] {
				return false
			}
			seen[s] = true
		}
	}
	return true
}
func (c LabelCatalog) Accepts(l Labels) bool {
	validOrigin := l.Origin == OriginLive || l.Origin == OriginDebugLive || l.Origin == OriginTestClock || l.Origin == OriginReplay || l.Origin == OriginImported
	validOutcome := l.Outcome == OutcomeNone || l.Outcome == OutcomeAccepted || l.Outcome == OutcomeRejected || l.Outcome == OutcomeSuccess || l.Outcome == OutcomeFailure || l.Outcome == OutcomeCancelled || l.Outcome == OutcomeAbandoned || l.Outcome == OutcomeIgnored
	return validOrigin && validOutcome && int(l.Operation) < len(c.Operations) && int(l.Component) < len(c.Components) && int(l.WorkerPool) < len(c.WorkerPools)
}

type HistogramSchema struct {
	ID             uint64
	FiniteBoundsNS []uint64
}

func DefaultHistogramSchema() HistogramSchema {
	return HistogramSchema{1, []uint64{1000, 10000, 100000, 500000, 1000000, 2000000, 5000000, 10000000, 20000000, 50000000, 100000000, 200000000, 500000000, 1000000000, 2000000000, 5000000000, 10000000000, 30000000000, 60000000000}}
}

// SumNS is an exact unsigned 128-bit integer. Arithmetic rejects overflow.
type Histogram struct {
	Buckets [65]uint64
	Count   uint64
	SumNS   big.Int
}
type Quantile struct {
	UpperBoundNS     *uint64 `json:"upper_bound_ns"`
	Observations     uint64  `json:"observations"`
	LowSampleWarning bool    `json:"low_sample_warning"`
	OverflowBucket   bool    `json:"overflow_bucket"`
}

func (h *Histogram) Quantile(s HistogramSchema, n, d uint64) *Quantile {
	if h.Count == 0 || d == 0 || n > d || len(s.FiniteBoundsNS) > 64 {
		return nil
	}
	rank := new(big.Int).Mul(new(big.Int).SetUint64(h.Count), new(big.Int).SetUint64(n))
	rank.Add(rank, new(big.Int).SetUint64(d-1))
	rank.Div(rank, new(big.Int).SetUint64(d))
	if rank.Sign() == 0 {
		rank.SetUint64(1)
	}
	total := new(big.Int)
	for i := 0; i <= len(s.FiniteBoundsNS); i++ {
		total.Add(total, new(big.Int).SetUint64(h.Buckets[i]))
		if total.Cmp(rank) >= 0 {
			q := &Quantile{Observations: h.Count, LowSampleWarning: h.Count < 100, OverflowBucket: i == len(s.FiniteBoundsNS)}
			if !q.OverflowBucket {
				v := s.FiniteBoundsNS[i]
				q.UpperBoundNS = &v
			}
			return q
		}
	}
	return nil
}

type MetricValue struct {
	Kind      MetricKind
	Number    uint64
	Histogram *Histogram
}
type SeriesKey struct {
	Family MetricFamily
	Labels Labels
}
type MetricHealth struct {
	RejectedSeries      uint64 `json:"rejected_series"`
	InvalidLabels       uint64 `json:"invalid_labels"`
	ArithmeticErrors    uint64 `json:"arithmetic_errors"`
	OmittedMeasurements uint64 `json:"omitted_measurements"`
	InvalidMeasurements uint64 `json:"invalid_measurements"`
	ImportedIgnored     uint64 `json:"imported_ignored"`
	SnapshotsEvicted    uint64 `json:"snapshots_evicted"`
	SnapshotRejections  uint64 `json:"snapshot_rejections"`
}

func (h MetricHealth) Complete() bool { return h == MetricHealth{} }

type MetricLimits struct{ MaxFamilies, MaxSeries, MaxBytes, MaxSnapshots, MaxSnapshotBytes int }

func DefaultMetricLimits() MetricLimits { return MetricLimits{128, 2048, 8 << 20, 12, 16 << 20} }

type GaugeScope string

const (
	CompleteParticipation GaugeScope = "complete-participation"
	HostSnapshot          GaugeScope = "host-snapshot"
	ObservedSinceAttach   GaugeScope = "observed-since-attach"
	UnknownGaugeScope     GaugeScope = "unknown"
)

type MetricSnapshot struct {
	Revision, Epoch uint64
	Start, End      *LocalInstant
	Schema          HistogramSchema
	Labels          LabelCatalog
	Series          map[SeriesKey]MetricValue
	GaugeScope      GaugeScope
	GaugeScopes     map[SeriesKey]GaugeScope
	Limits          MetricLimits
	Health          MetricHealth
	Complete        bool
	Temporality     string
}

func cloneSnapshot(s MetricSnapshot) MetricSnapshot {
	r := s
	r.GaugeScopes = map[SeriesKey]GaugeScope{}
	for k, v := range s.GaugeScopes {
		r.GaugeScopes[k] = v
	}
	r.Schema.FiniteBoundsNS = append([]uint64(nil), s.Schema.FiniteBoundsNS...)
	r.Labels = LabelCatalog{append([]string(nil), s.Labels.Operations...), append([]string(nil), s.Labels.Components...), append([]string(nil), s.Labels.WorkerPools...)}
	if s.Start != nil {
		v := *s.Start
		r.Start = &v
	}
	if s.End != nil {
		v := *s.End
		r.End = &v
	}
	r.Series = map[SeriesKey]MetricValue{}
	for k, v := range s.Series {
		if v.Histogram != nil {
			h := *v.Histogram
			h.SumNS = *new(big.Int).Set(&v.Histogram.SumNS)
			v.Histogram = &h
		}
		r.Series[k] = v
	}
	return r
}

var ErrMetric = errors.New("metrics: invalid config, kind, window or arithmetic")

func MetricWindow(a, b MetricSnapshot) (MetricSnapshot, error) {
	if a.Epoch != b.Epoch || a.Temporality != "cumulative" || b.Temporality != "cumulative" || !reflect.DeepEqual(a.Schema, b.Schema) || !reflect.DeepEqual(a.Labels, b.Labels) || !reflect.DeepEqual(a.Start, b.Start) || a.GaugeScope != b.GaugeScope || a.Revision > b.Revision {
		return MetricSnapshot{}, ErrMetric
	}
	if a.End != nil && b.End != nil {
		if _, e := b.End.ElapsedSince(*a.End); e != nil {
			return MetricSnapshot{}, e
		}
	}
	for k, v := range a.GaugeScopes {
		if b.GaugeScopes[k] != v {
			return MetricSnapshot{}, ErrMetric
		}
	}
	r := cloneSnapshot(b)
	r.Start = nil
	if a.End != nil {
		v := *a.End
		r.Start = &v
	}
	r.Temporality = "delta"
	r.Complete = a.Complete && b.Complete
	for k, p := range a.Series {
		v, ok := r.Series[k]
		if !ok || v.Kind != p.Kind {
			return MetricSnapshot{}, ErrMetric
		}
		switch v.Kind {
		case CounterMetric:
			if v.Number < p.Number {
				return MetricSnapshot{}, ErrMetric
			}
			v.Number -= p.Number
		case HistogramMetric:
			if v.Histogram.Count < p.Histogram.Count || v.Histogram.SumNS.Cmp(&p.Histogram.SumNS) < 0 {
				return MetricSnapshot{}, ErrMetric
			}
			v.Histogram.Count -= p.Histogram.Count
			v.Histogram.SumNS.Sub(&v.Histogram.SumNS, &p.Histogram.SumNS)
			for i, n := range p.Histogram.Buckets {
				if v.Histogram.Buckets[i] < n {
					return MetricSnapshot{}, ErrMetric
				}
				v.Histogram.Buckets[i] -= n
			}
		}
		r.Series[k] = v
	}
	return r, nil
}

type MetricsStore struct {
	s                 MetricSnapshot
	limits            MetricLimits
	history           []MetricSnapshot
	bytes             int
	lastValidEnd      *LocalInstant
	defaultGaugeScope GaugeScope
}

func NewMetricsStore(epoch uint64, c LabelCatalog, s HistogramSchema, l MetricLimits) (*MetricsStore, error) {
	if epoch == 0 || !c.valid() || s.ID == 0 || len(s.FiniteBoundsNS) > 64 || l.MaxFamilies <= 0 || l.MaxFamilies > 128 || l.MaxSeries <= 0 || l.MaxBytes <= 0 || l.MaxSnapshots <= 0 || l.MaxSnapshotBytes < 0 {
		return nil, ErrMetric
	}
	for i, b := range s.FiniteBoundsNS {
		if i > 0 && b <= s.FiniteBoundsNS[i-1] {
			return nil, ErrMetric
		}
	}
	m := &MetricsStore{s: MetricSnapshot{Epoch: epoch, Schema: s, Labels: c, Series: map[SeriesKey]MetricValue{}, GaugeScope: ObservedSinceAttach, Complete: true, Temporality: "cumulative"}, limits: l}
	m.bytes = 512 + 8*len(s.FiniteBoundsNS)
	for _, xs := range [][]string{c.Operations, c.Components, c.WorkerPools} {
		for _, name := range xs {
			m.bytes += 24 + len(name)
		}
	}
	if m.bytes > l.MaxBytes {
		return nil, ErrMetric
	}
	m.defaultGaugeScope = ObservedSinceAttach
	m.s.Limits = l
	m.s = cloneSnapshot(m.s)
	return m, nil
}
func satInc(n *uint64) {
	if *n < math.MaxUint64 {
		*n++
	}
}
func (m *MetricsStore) entry(f MetricFamily, l Labels, kind MetricKind) (MetricValue, bool) {
	if metricKind(f) != kind {
		satInc(&m.s.Health.ArithmeticErrors)
		m.s.Complete = false
		return MetricValue{}, false
	}
	if l.Origin == OriginReplay || l.Origin == OriginImported {
		satInc(&m.s.Health.ImportedIgnored)
		return MetricValue{}, false
	}
	if !m.s.Labels.Accepts(l) {
		satInc(&m.s.Health.InvalidLabels)
		m.s.Complete = false
		return MetricValue{}, false
	}
	k := SeriesKey{f, l}
	if v, ok := m.s.Series[k]; ok {
		if m.s.Revision < math.MaxUint64 {
			m.s.Revision++
		}
		m.s.End = nil
		return v, true
	}
	cost := 128
	if kind == HistogramMetric {
		cost += 640
	}
	families := map[MetricFamily]bool{}
	for k := range m.s.Series {
		families[k.Family] = true
	}
	if len(m.s.Series) >= m.limits.MaxSeries || cost > m.limits.MaxBytes-m.bytes || (!families[f] && len(families) >= m.limits.MaxFamilies) {
		satInc(&m.s.Health.RejectedSeries)
		m.s.Complete = false
		return MetricValue{}, false
	}
	m.bytes += cost
	v := MetricValue{Kind: kind}
	if kind == HistogramMetric {
		v.Histogram = &Histogram{}
	}
	m.s.Series[k] = v
	if kind == GaugeMetric {
		m.s.GaugeScopes[k] = m.defaultGaugeScope
		m.refreshGaugeScope()
	}
	if m.s.Revision < math.MaxUint64 {
		m.s.Revision++
	}
	m.s.End = nil
	return v, true
}
func (m *MetricsStore) Increment(f MetricFamily, l Labels, n uint64) {
	v, ok := m.entry(f, l, CounterMetric)
	if !ok {
		return
	}
	if n > math.MaxUint64-v.Number {
		satInc(&m.s.Health.ArithmeticErrors)
		m.s.Complete = false
		return
	}
	v.Number += n
	m.s.Series[SeriesKey{f, l}] = v
}
func (m *MetricsStore) SetGauge(f MetricFamily, l Labels, n uint64) {
	v, ok := m.entry(f, l, GaugeMetric)
	if ok {
		v.Number = n
		m.s.Series[SeriesKey{f, l}] = v
	}
}
func (m *MetricsStore) GaugeDelta(f MetricFamily, l Labels, d int64) {
	v, ok := m.entry(f, l, GaugeMetric)
	if !ok {
		return
	}
	if d >= 0 {
		if uint64(d) > math.MaxUint64-v.Number {
			satInc(&m.s.Health.ArithmeticErrors)
			m.s.Complete = false
			m.s.GaugeScopes[SeriesKey{f, l}] = UnknownGaugeScope
			m.refreshGaugeScope()
			return
		}
		v.Number += uint64(d)
	} else {
		n := uint64(-(d + 1)) + 1
		if n > v.Number {
			satInc(&m.s.Health.ArithmeticErrors)
			m.s.Complete = false
			m.s.GaugeScopes[SeriesKey{f, l}] = UnknownGaugeScope
			m.refreshGaugeScope()
			return
		}
		v.Number -= n
	}
	m.s.Series[SeriesKey{f, l}] = v
}
func (m *MetricsStore) Observe(f MetricFamily, l Labels, n uint64) {
	v, ok := m.entry(f, l, HistogramMetric)
	if !ok {
		return
	}
	h := v.Histogram
	i := sort.Search(len(m.s.Schema.FiniteBoundsNS), func(i int) bool { return m.s.Schema.FiniteBoundsNS[i] >= n })
	sum := new(big.Int).Add(&h.SumNS, new(big.Int).SetUint64(n))
	if h.Count == math.MaxUint64 || h.Buckets[i] == math.MaxUint64 || sum.BitLen() > 128 {
		satInc(&m.s.Health.ArithmeticErrors)
		m.s.Complete = false
		return
	}
	h.Count++
	h.Buckets[i]++
	h.SumNS.Set(sum)
}
func (m *MetricsStore) MarkIncomplete()         { m.s.Complete = false }
func (m *MetricsStore) Current() MetricSnapshot { return cloneSnapshot(m.s) }
func (m *MetricsStore) Snapshot(end *LocalInstant) (MetricSnapshot, error) {
	if m.s.Revision == math.MaxUint64 {
		return MetricSnapshot{}, ErrMetric
	}
	m.s.Revision++
	m.s.End = nil
	if end != nil {
		valid := true
		for _, previous := range []*LocalInstant{m.s.Start, m.lastValidEnd} {
			if previous != nil {
				if _, e := end.ElapsedSince(*previous); e != nil {
					valid = false
				}
			}
		}
		if valid {
			v := *end
			m.s.End = &v
			last := v
			m.lastValidEnd = &last
		} else {
			satInc(&m.s.Health.InvalidMeasurements)
			m.s.Complete = false
		}
	}
	cost := 512 + m.bytes
	if cost > m.limits.MaxSnapshotBytes {
		satInc(&m.s.Health.SnapshotRejections)
		return m.Current(), nil
	}
	for len(m.history) > 0 && (len(m.history) >= m.limits.MaxSnapshots || cost*(len(m.history)+1) > m.limits.MaxSnapshotBytes) {
		m.history = m.history[1:]
		satInc(&m.s.Health.SnapshotsEvicted)
	}
	s := m.Current()
	m.history = append(m.history, s)
	return cloneSnapshot(s), nil
}
func (m *MetricsStore) AvailableWindows() []uint64 {
	out := []uint64{}
	for _, s := range m.history {
		out = append(out, s.Revision)
	}
	return out
}
func (m *MetricsStore) Window(from, to uint64) (MetricSnapshot, error) {
	var a, b *MetricSnapshot
	for i := range m.history {
		if m.history[i].Revision == from {
			a = &m.history[i]
		}
		if m.history[i].Revision == to {
			b = &m.history[i]
		}
	}
	if a == nil || b == nil {
		return MetricSnapshot{}, ErrMetric
	}
	return MetricWindow(*a, *b)
}
func (s MetricSnapshot) Production() MetricSnapshot {
	r := cloneSnapshot(s)
	for k := range r.Series {
		if k.Labels.Origin != OriginLive || k.Labels.DebuggerAffected {
			delete(r.Series, k)
		}
	}
	return r
}

func (m *MetricsStore) SetGaugeScope(scope GaugeScope) {
	m.defaultGaugeScope = scope
	for k := range m.s.GaugeScopes {
		m.s.GaugeScopes[k] = scope
	}
	m.s.GaugeScope = scope
}
func (m *MetricsStore) SetGaugeScopeFor(l Labels, scope GaugeScope) {
	for k, v := range m.s.Series {
		if k.Labels == l && v.Kind == GaugeMetric {
			m.s.GaugeScopes[k] = scope
		}
	}
	m.refreshGaugeScope()
}

// MergeDisjoint requires the caller to establish disjoint producer populations.
// It is transactional: incompatible or overflowing merges leave the receiver intact.
func (s *MetricSnapshot) MergeDisjoint(other MetricSnapshot) error {
	if s.Epoch != other.Epoch || s.Temporality != other.Temporality || s.GaugeScope != other.GaugeScope || !reflect.DeepEqual(s.Schema, other.Schema) || !reflect.DeepEqual(s.Labels, other.Labels) || !reflect.DeepEqual(s.Start, other.Start) || !reflect.DeepEqual(s.End, other.End) {
		return ErrMetric
	}
	r := cloneSnapshot(*s)
	for k, v := range other.GaugeScopes {
		if a, ok := r.GaugeScopes[k]; ok && a != v {
			return ErrMetric
		}
		r.GaugeScopes[k] = v
	}
	for k, v := range other.Series {
		a, ok := r.Series[k]
		if !ok {
			if v.Histogram != nil {
				h := *v.Histogram
				h.SumNS = *new(big.Int).Set(&v.Histogram.SumNS)
				v.Histogram = &h
			}
			r.Series[k] = v
			continue
		}
		if a.Kind != v.Kind {
			return ErrMetric
		}
		if a.Kind == HistogramMetric {
			if v.Histogram.Count > math.MaxUint64-a.Histogram.Count {
				return ErrMetric
			}
			a.Histogram.Count += v.Histogram.Count
			a.Histogram.SumNS.Add(&a.Histogram.SumNS, &v.Histogram.SumNS)
			if a.Histogram.SumNS.BitLen() > 128 {
				return ErrMetric
			}
			for i, n := range v.Histogram.Buckets {
				if n > math.MaxUint64-a.Histogram.Buckets[i] {
					return ErrMetric
				}
				a.Histogram.Buckets[i] += n
			}
		} else {
			if v.Number > math.MaxUint64-a.Number {
				return ErrMetric
			}
			a.Number += v.Number
		}
		r.Series[k] = a
	}
	families := map[MetricFamily]bool{}
	cost := 512
	for k, v := range r.Series {
		families[k.Family] = true
		cost += 128
		if v.Histogram != nil {
			cost += 640
		}
	}
	if len(r.Series) > s.Limits.MaxSeries || len(families) > s.Limits.MaxFamilies || cost > s.Limits.MaxBytes {
		return ErrMetric
	}
	dst := []*uint64{&r.Health.RejectedSeries, &r.Health.InvalidLabels, &r.Health.ArithmeticErrors, &r.Health.OmittedMeasurements, &r.Health.InvalidMeasurements, &r.Health.ImportedIgnored, &r.Health.SnapshotsEvicted, &r.Health.SnapshotRejections}
	src := []uint64{other.Health.RejectedSeries, other.Health.InvalidLabels, other.Health.ArithmeticErrors, other.Health.OmittedMeasurements, other.Health.InvalidMeasurements, other.Health.ImportedIgnored, other.Health.SnapshotsEvicted, other.Health.SnapshotRejections}
	for i, n := range src {
		if n > math.MaxUint64-*dst[i] {
			return ErrMetric
		}
		*dst[i] += n
	}
	r.Complete = r.Complete && other.Complete
	*s = r
	return nil
}

func metricDescriptor(f MetricFamily, k MetricKind, unit string) MetricDescriptor {
	population := map[MetricFamily]string{Requests: "observed host output requests", Admissions: "host admission decisions", Attempts: "actual first execution starts", AttemptOutcomes: "known attempt terminations", Resolutions: "logical effect resolutions", Deliveries: "observed completion inputs including ignored/rejected/duplicates", Unresolved: "admitted unresolved logical effects", Running: "started attempts without known termination", ReadyDepth: "synchronized ready entries", CompletionDepth: "reserved visible completion entries", QueueWait: "ready to actual start per attempt", AttemptElapsed: "known terminated attempts, including async waits", Resolution: "request to logical resolution", DeliveryLag: "resolution to each completion delivery start", EndToEnd: "request to first completion delivery start", CompletionProcessing: "delivery begin to immediately after reducer, excluding checks/recording", AdmissionDelay: "request to first ready queue boundary", ResolutionOverhead: "selected finished attempt to logical resolution", CompletionPreparation: "resolution to completion publication reservation", CompletionQueueWait: "publication reservation to delivery begin", EndToEndObserved: "request to first observed completion", Settlement: "request to explicit application acknowledgement", ScheduledBackoff: "host-declared scheduled retry delay", EligibilityWait: "retry schedule observation to ready eligibility", InterAttemptGap: "previous attempt finish to next actual start; not just backoff", DuplicateObservations: "repeated notification for the same token boundary", DuplicateDeliveries: "additional observed delivery identities for one effect", CancellationRequests: "host cancellation requests; no running-gauge decrement", CancellationAcknowledgements: "confirmed logical cancellation, distinct from attempt termination", Abandoned: "local observation abandonment; remote termination unknown", Dropped: "diagnostic loss; aggregate health remains independent", InvalidMeasurements: "unknown/reversed/cross-domain measurements"}
	return MetricDescriptor{Family: f, Name: string(f), Kind: k, Unit: unit, Population: population[f], Reset: "cumulative per process epoch; never sum repeated snapshots", AllowedLabels: []string{"operation", "component", "worker_pool", "outcome", "measurement_origin", "debugger_affected"}}
}

// NewMetricsStoreAt binds an explicit host interval and gauge participation scope.
func NewMetricsStoreAt(epoch uint64, c LabelCatalog, s HistogramSchema, l MetricLimits, start *LocalInstant, scope GaugeScope) (*MetricsStore, error) {
	m, e := NewMetricsStore(epoch, c, s, l)
	if e != nil {
		return nil, e
	}
	if scope != CompleteParticipation && scope != HostSnapshot && scope != ObservedSinceAttach && scope != UnknownGaugeScope {
		return nil, ErrMetric
	}
	m.s.GaugeScope = scope
	m.defaultGaugeScope = scope
	if start != nil {
		a := *start
		b := a
		c := a
		m.s.Start = &a
		m.s.End = &b
		m.lastValidEnd = &c
	}
	return m, nil
}

func (m *MetricsStore) refreshGaugeScope() {
	scope := m.defaultGaugeScope
	first := true
	for _, v := range m.s.GaugeScopes {
		if first {
			scope = v
			first = false
		} else if v != scope {
			scope = UnknownGaugeScope
			break
		}
	}
	m.s.GaugeScope = scope
}

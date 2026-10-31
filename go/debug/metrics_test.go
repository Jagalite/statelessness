package debug_test

import (
	"encoding/json"
	d "github.com/Jagalite/statelessness/go/debug"
	"math"
	"os"
	"reflect"
	"strconv"
	"testing"
)

func metricStore(t *testing.T) *d.MetricsStore {
	t.Helper()
	m, e := d.NewMetricsStore(1, d.DefaultLabelCatalog(), d.HistogramSchema{ID: 1, FiniteBoundsNS: []uint64{10, 100}}, d.DefaultMetricLimits())
	if e != nil {
		t.Fatal(e)
	}
	return m
}
func TestHistogramExactOverflowAndWindow(t *testing.T) {
	m := metricStore(t)
	l := d.Labels{Origin: d.OriginLive}
	a, _ := m.Snapshot(&d.LocalInstant{Domain: 1, Nanos: 1})
	m.Observe(d.QueueWait, l, 10)
	m.Observe(d.QueueWait, l, math.MaxUint64)
	b, _ := m.Snapshot(&d.LocalInstant{Domain: 1, Nanos: 2})
	w, e := d.MetricWindow(a, b)
	if e != nil {
		t.Fatal(e)
	}
	h := w.Series[d.SeriesKey{Family: d.QueueWait, Labels: l}].Histogram
	if h.Count != 2 || h.SumNS.String() != "18446744073709551625" {
		t.Fatal(h)
	}
	q := h.Quantile(w.Schema, 95, 100)
	if q == nil || q.UpperBoundNS != nil || !q.OverflowBucket || !q.LowSampleWarning {
		t.Fatal(q)
	}
	q = h.Quantile(w.Schema, 50, 100)
	if q == nil || q.UpperBoundNS == nil || *q.UpperBoundNS != 10 {
		t.Fatal(q)
	}
	h.SumNS.SetUint64(0)
	fresh, _ := m.Window(a.Revision, b.Revision)
	if fresh.Series[d.SeriesKey{Family: d.QueueWait, Labels: l}].Histogram.SumNS.Sign() == 0 {
		t.Fatal("snapshot alias")
	}
	bad := b
	bad.Epoch = 2
	if _, e = d.MetricWindow(a, bad); e == nil {
		t.Fatal("epoch mismatch")
	}
}
func TestMetricRejectedLabelsOverflowAndImported(t *testing.T) {
	m := metricStore(t)
	l := d.Labels{Origin: d.OriginLive}
	m.Increment(d.Requests, l, math.MaxUint64)
	m.Increment(d.Requests, l, 1)
	m.Observe(d.QueueWait, d.Labels{Origin: d.OriginReplay}, 5)
	m.Increment(d.Requests, d.Labels{Origin: d.OriginLive, Operation: 3}, 1)
	m.SetGauge(d.Running, l, 0)
	m.GaugeDelta(d.Running, l, -1)
	s := m.Current()
	if s.Health.ArithmeticErrors != 2 || s.Health.ImportedIgnored != 1 || s.Health.InvalidLabels != 1 || s.Series[d.SeriesKey{Family: d.Requests, Labels: l}].Number != math.MaxUint64 {
		t.Fatal(s.Health)
	}
	l.Origin = d.OriginTestClock
	m.Increment(d.Requests, l, 1)
	if _, ok := m.Current().Production().Series[d.SeriesKey{Family: d.Requests, Labels: l}]; ok {
		t.Fatal("test clock production")
	}
}
func TestMetricsCatalogIsolationAndBoundedHistory(t *testing.T) {
	c := d.DefaultLabelCatalog()
	schema := d.HistogramSchema{ID: math.MaxUint64, FiniteBoundsNS: []uint64{1}}
	limits := d.DefaultMetricLimits()
	limits.MaxSeries = 1
	limits.MaxSnapshots = 1
	m, e := d.NewMetricsStore(math.MaxUint64, c, schema, limits)
	if e != nil {
		t.Fatal(e)
	}
	c.Operations[0] = "mutated"
	schema.FiniteBoundsNS[0] = 7
	l := d.Labels{Origin: d.OriginLive}
	m.Increment(d.Requests, l, 1)
	m.Increment(d.Attempts, l, 1)
	a, _ := m.Snapshot(nil)
	b, _ := m.Snapshot(nil)
	if !reflect.DeepEqual(m.AvailableWindows(), []uint64{b.Revision}) || b.Health.SnapshotsEvicted != 1 || b.Health.RejectedSeries != 1 || b.Schema.FiniteBoundsNS[0] != 1 || b.Labels.Operations[0] != "unspecified" {
		t.Fatal(b)
	}
	if _, e = m.Window(a.Revision, b.Revision); e == nil {
		t.Fatal("evicted window")
	}
}
func TestSnapshotEndpointIsolationAndInvalidClock(t *testing.T) {
	m := metricStore(t)
	end := &d.LocalInstant{Domain: 1, Nanos: 5}
	a, _ := m.Snapshot(end)
	end.Nanos = 0
	if m.Current().End.Nanos != 5 || a.End.Nanos != 5 {
		t.Fatal("endpoint alias")
	}
	b, _ := m.Snapshot(&d.LocalInstant{Domain: 2, Nanos: 6})
	if b.End != nil || b.Complete || b.Health.InvalidMeasurements != 1 {
		t.Fatal(b)
	}
	c, _ := m.Snapshot(&d.LocalInstant{Domain: 1, Nanos: 4})
	if c.End != nil || c.Health.InvalidMeasurements != 2 {
		t.Fatal(c)
	}
}
func TestWindowDoesNotMutateSourceOrStore(t *testing.T) {
	m := metricStore(t)
	l := d.Labels{Origin: d.OriginLive}
	k := d.SeriesKey{Family: d.QueueWait, Labels: l}
	m.Observe(d.QueueWait, l, 10)
	a, _ := m.Snapshot(nil)
	m.Observe(d.QueueWait, l, 20)
	b, _ := m.Snapshot(nil)
	for i := 0; i < 2; i++ {
		w, e := d.MetricWindow(a, b)
		if e != nil || w.Series[k].Histogram.SumNS.String() != "20" {
			t.Fatal(w, e)
		}
	}
	if a.Series[k].Histogram.SumNS.String() != "10" || b.Series[k].Histogram.SumNS.String() != "30" || m.Current().Series[k].Histogram.SumNS.String() != "30" {
		t.Fatal("window mutated source")
	}
	b.Series[k].Histogram.SumNS.SetUint64(9)
	if m.Current().Series[k].Histogram.SumNS.String() != "30" {
		t.Fatal("snapshot aliases store")
	}
}
func TestDisjointMergeAtomicOverflowAndGaugeScope(t *testing.T) {
	m := metricStore(t)
	l := d.Labels{Origin: d.OriginLive}
	m.Increment(d.Requests, l, math.MaxUint64)
	a := m.Current()
	other := metricStore(t)
	other.Increment(d.Requests, l, 1)
	b := other.Current()
	if e := a.MergeDisjoint(b); e == nil || a.Series[d.SeriesKey{Family: d.Requests, Labels: l}].Number != math.MaxUint64 {
		t.Fatal("nontransactional overflow")
	}
	a = other.Current()
	if e := a.MergeDisjoint(b); e != nil || a.Series[d.SeriesKey{Family: d.Requests, Labels: l}].Number != 2 {
		t.Fatal(a, e)
	}
}

// The shared corpus was written manually before any real Rust reference run.
func TestSharedManualHistogramCorpus(t *testing.T) {
	path := os.Getenv("STATELESS_DIAGNOSTIC_CORPUS")
	if path == "" {
		path = "../../conformance/debugger-diagnostics-v1.json"
	}
	raw, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct {
		Histogram struct {
			Bounds       []string `json:"bounds_ns"`
			Samples      []string `json:"samples_ns"`
			Buckets      []string `json:"bucket_counts"`
			Observations string   `json:"observations"`
			P50          struct {
				Upper *string `json:"upper_bound_ns"`
			} `json:"p50"`
			P95 struct {
				Upper *string `json:"upper_bound_ns"`
			} `json:"p95"`
		} `json:"histogram"`
	}
	if e = json.Unmarshal(raw, &corpus); e != nil {
		t.Fatal(e)
	}
	bounds := []uint64{}
	for _, v := range corpus.Histogram.Bounds {
		n, e := strconv.ParseUint(v, 10, 64)
		if e != nil {
			t.Fatal(e)
		}
		bounds = append(bounds, n)
	}
	m, e := d.NewMetricsStore(1, d.DefaultLabelCatalog(), d.HistogramSchema{ID: 1, FiniteBoundsNS: bounds}, d.DefaultMetricLimits())
	if e != nil {
		t.Fatal(e)
	}
	l := d.Labels{Origin: d.OriginLive}
	for _, v := range corpus.Histogram.Samples {
		n, e := strconv.ParseUint(v, 10, 64)
		if e != nil {
			t.Fatal(e)
		}
		m.Observe(d.QueueWait, l, n)
	}
	s := m.Current()
	h := s.Series[d.SeriesKey{Family: d.QueueWait, Labels: l}].Histogram
	for i, v := range corpus.Histogram.Buckets {
		if strconv.FormatUint(h.Buckets[i], 10) != v {
			t.Fatal("bucket", i)
		}
	}
	if strconv.FormatUint(h.Count, 10) != corpus.Histogram.Observations {
		t.Fatal("count")
	}
	q := h.Quantile(s.Schema, 50, 100)
	if q == nil || q.UpperBoundNS == nil || corpus.Histogram.P50.Upper == nil || strconv.FormatUint(*q.UpperBoundNS, 10) != *corpus.Histogram.P50.Upper {
		t.Fatal("p50")
	}
	q = h.Quantile(s.Schema, 95, 100)
	if q == nil || q.UpperBoundNS != nil || corpus.Histogram.P95.Upper != nil || !q.OverflowBucket {
		t.Fatal("p95")
	}
}
func TestGaugeUnderflowMakesParticipationUnknown(t *testing.T) {
	m := metricStore(t)
	l := d.Labels{Origin: d.OriginLive}
	m.SetGauge(d.Running, l, 0)
	before := m.Current()
	m.GaugeDelta(d.Running, l, -1)
	s := m.Current()
	k := d.SeriesKey{Family: d.Running, Labels: l}
	if s.Complete || s.GaugeScopes[k] != d.UnknownGaugeScope || s.GaugeScope != d.UnknownGaugeScope || s.Revision <= before.Revision || s.End != nil {
		t.Fatal(s)
	}
}
func TestHistoryRetentionRejectionStillReturnsCumulativeSnapshot(t *testing.T) {
	limits := d.DefaultMetricLimits()
	limits.MaxSnapshotBytes = 0
	m, e := d.NewMetricsStore(1, d.DefaultLabelCatalog(), d.DefaultHistogramSchema(), limits)
	if e != nil {
		t.Fatal(e)
	}
	l := d.Labels{Origin: d.OriginLive}
	m.Increment(d.Requests, l, 1)
	s, e := m.Snapshot(nil)
	if e != nil || s.Health.SnapshotRejections != 1 || s.Series[d.SeriesKey{Family: d.Requests, Labels: l}].Number != 1 || len(m.AvailableWindows()) != 0 {
		t.Fatal(s, e)
	}
}

func TestDefaultMetricLabelsMatchPinnedCatalog(t *testing.T) {
	catalog := d.DefaultLabelCatalog()
	if !reflect.DeepEqual(catalog.Operations, []string{"unspecified"}) || !reflect.DeepEqual(catalog.Components, []string{"host"}) || !reflect.DeepEqual(catalog.WorkerPools, []string{"default"}) {
		t.Fatal(catalog)
	}
}

package debug

import (
	"math/big"
	"testing"
)

func TestMetricSnapshotAccessors(t *testing.T) {
	labels := Labels{Operation: 3}
	h := &Histogram{Count: 1, SumNS: *new(big.Int).Lsh(big.NewInt(1), 100)}
	h.Buckets[0] = 1
	s := MetricSnapshot{Series: map[SeriesKey]MetricValue{
		{Family: QueueWait, Labels: labels}: {Kind: HistogramMetric, Histogram: h},
		{Family: Requests, Labels: labels}:  {Kind: CounterMetric, Number: 7},
	}}
	if _, ok := s.Get(Requests, Labels{}); ok {
		t.Fatal("missing series found")
	}
	if _, ok := s.Histogram(Requests, labels); ok {
		t.Fatal("counter reported as histogram")
	}
	value, ok := s.Get(Requests, labels)
	if !ok || value.Number != 7 {
		t.Fatal("counter lookup failed")
	}
	got, ok := s.Histogram(QueueWait, labels)
	if !ok || got.Count != 1 || got.SumNS.Cmp(&h.SumNS) != 0 {
		t.Fatal("histogram lookup failed")
	}
	got.Buckets[0] = 9
	got.SumNS.SetInt64(0)
	if h.Buckets[0] != 1 || h.SumNS.Sign() == 0 {
		t.Fatal("accessor aliased snapshot")
	}
	value, ok = s.Get(QueueWait, labels)
	if !ok {
		t.Fatal("histogram value missing")
	}
	value.Histogram.SumNS.SetInt64(0)
	if h.SumNS.Sign() == 0 {
		t.Fatal("Get aliased histogram sum")
	}
}

package debug

import "testing"

type fixedOrderingClock struct{}

func (fixedOrderingClock) Now() (LocalInstant, error) { return LocalInstant{Domain: 1, Nanos: 10}, nil }

func TestFirstDeliveryMeasurementOrderAtFamilyLimit(t *testing.T) {
	opts := DefaultEffectOptions()
	opts.Counters = false
	opts.Details = false
	opts.MetricLimits.MaxFamilies = 1
	o, err := NewEffectObserver(opts, fixedOrderingClock{})
	if err != nil {
		t.Fatal(err)
	}
	requested := LocalInstant{Domain: 1, Nanos: 1}
	begin := LocalInstant{Domain: 1, Nanos: 5}
	effect := &effectState{requested: &requested, labels: Labels{Origin: OriginLive}}
	delivery := DeliveryHandle{owner: o, state: &deliveryState{effect: effect, begun: true, begin: &begin}}
	if err := o.DeliveryObserved(delivery, OutcomeAccepted); err != nil {
		t.Fatal(err)
	}
	s := o.metrics.Current()
	labels := Labels{Origin: OriginLive, Outcome: OutcomeAccepted}
	value, ok := s.Series[SeriesKey{Family: EndToEndObserved, Labels: labels}]
	if !ok || value.Histogram == nil || value.Histogram.Count != 1 || value.Histogram.SumNS.Uint64() != 9 {
		t.Fatalf("end-to-end sample was not retained: %+v", s.Series)
	}
	if len(s.Series) != 1 || s.Health.RejectedSeries != 1 || s.Complete {
		t.Fatalf("unexpected family limit accounting: %+v", s)
	}
}

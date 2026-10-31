package debug_test

import (
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

func TestAttemptCreationAndAbandonmentDoNotReadClock(t *testing.T) {
	observer, clock := effectObserver(t)
	effect := request(t, observer)
	if err := observer.Admission(effect, true); err != nil {
		t.Fatal(err)
	}
	reads := clock.reads
	attempt, err := observer.AttemptCreated(effect)
	if err != nil || clock.reads != reads {
		t.Fatal("attempt creation read clock", err, reads, clock.reads)
	}
	if err = observer.AttemptAbandoned(attempt); err != nil {
		t.Fatal(err)
	}
	if err = observer.AttemptAbandoned(attempt); err != nil {
		t.Fatal("abandonment was not idempotent", err)
	}
	if clock.reads != reads {
		t.Fatal("abandonment read clock", reads, clock.reads)
	}
	detail, ok := observer.EffectDetails(effect.ID())
	if !ok || len(detail.Events) != 4 || detail.Events[2].Kind != "attempt-created" || detail.Events[2].At != nil || detail.Events[3].Kind != "abandoned" || detail.Events[3].At != nil || !detail.Abandoned || detail.Complete {
		t.Fatal(detail)
	}
	if health := observer.TelemetryHealth(); health.Abandoned != 1 || health.InvalidLifecycle != 0 {
		t.Fatal(health)
	}
}

func TestAbandoningFinishedAttemptIsNoOp(t *testing.T) {
	observer, clock := effectObserver(t)
	effect := request(t, observer)
	if err := observer.Admission(effect, true); err != nil {
		t.Fatal(err)
	}
	attempt, err := observer.AttemptCreated(effect)
	if err != nil {
		t.Fatal(err)
	}
	if err = observer.AttemptStarted(attempt); err != nil {
		t.Fatal(err)
	}
	if err = observer.AttemptFinished(attempt, d.OutcomeSuccess); err != nil {
		t.Fatal(err)
	}
	reads := clock.reads
	if err = observer.AttemptAbandoned(attempt); err != nil || clock.reads != reads {
		t.Fatal(err, reads, clock.reads)
	}
	if health := observer.TelemetryHealth(); health.Abandoned != 0 || health.InvalidLifecycle != 0 {
		t.Fatal(health)
	}
}

func TestPauseGenerationsAndAffectedRetryAccounting(t *testing.T) {
	observer, _ := effectObserver(t)
	effect := request(t, observer)
	if err := observer.Admission(effect, true); err != nil {
		t.Fatal(err)
	}
	attempt, err := observer.AttemptCreated(effect)
	if err != nil {
		t.Fatal(err)
	}
	observer.DebuggerPause(true)
	if err = observer.RetryScheduled(attempt, 17); err != nil {
		t.Fatal(err)
	}
	if health := observer.TelemetryHealth(); health.DebuggerAffectedMeasurements != 1 {
		t.Fatal(health)
	}
	observer.DebuggerPause(false)
	observer.DebuggerPause(true)
	observer.DebuggerPause(false)
	observer.DebuggerPause(true)
	pauses := observer.PauseIntervals()
	if len(pauses) != 3 || pauses[0].Generation != 1 || pauses[1].Generation != 3 || pauses[2].Generation != 5 {
		t.Fatal(pauses)
	}
}

func TestExplicitEffectAbandonmentPreservesHostGauges(t *testing.T) {
	observer, clock := effectObserver(t)
	effect := request(t, observer)
	if err := observer.Admission(effect, true); err != nil {
		t.Fatal(err)
	}
	reads := clock.reads
	if err := observer.EffectAbandoned(effect); err != nil {
		t.Fatal(err)
	}
	if err := observer.EffectAbandoned(effect); err != nil {
		t.Fatal("abandonment not idempotent", err)
	}
	if clock.reads != reads {
		t.Fatal("abandonment read clock", reads, clock.reads)
	}
	detail, ok := observer.EffectDetails(effect.ID())
	if !ok || !detail.Abandoned || detail.Complete || detail.ResolvedKnown || detail.Events[len(detail.Events)-1].At != nil {
		t.Fatal(detail)
	}
	metrics, err := observer.MetricSnapshot()
	if err != nil || metrics.Complete || number(metrics, d.Unresolved) != 1 || number(metrics, d.Abandoned) != 1 {
		t.Fatal(metrics, err)
	}
	if health := observer.TelemetryHealth(); health.Abandoned != 1 || health.InvalidLifecycle != 0 {
		t.Fatal(health)
	}
	other := request(t, observer)
	if err := observer.Admission(other, false); err != nil {
		t.Fatal(err)
	}
	if err := observer.EffectAbandoned(other); err != nil {
		t.Fatal(err)
	}
	if observer.TelemetryHealth().Abandoned != 1 {
		t.Fatal("rejected effect reported abandoned")
	}
}

func TestExplicitDeliveryAbandonmentPreservesPublicationUncertainty(t *testing.T) {
	observer, clock := effectObserver(t)
	effect := request(t, observer)
	if err := observer.Admission(effect, true); err != nil {
		t.Fatal(err)
	}
	if err := observer.Resolved(effect, d.OutcomeSuccess, nil); err != nil {
		t.Fatal(err)
	}
	delivery, err := observer.ReservePublication(effect)
	if err != nil {
		t.Fatal(err)
	}
	reads := clock.reads
	if err = observer.DeliveryAbandoned(delivery); err != nil {
		t.Fatal(err)
	}
	if err = observer.DeliveryAbandoned(delivery); err != nil {
		t.Fatal("abandonment not idempotent", err)
	}
	if clock.reads != reads {
		t.Fatal("abandonment read clock", reads, clock.reads)
	}
	metrics, err := observer.MetricSnapshot()
	if err != nil || metrics.Complete || number(metrics, d.CompletionDepth) != 1 || number(metrics, d.Deliveries) != 0 || number(metrics, d.Abandoned) != 1 {
		t.Fatal(metrics, err)
	}
	detail, ok := observer.EffectDetails(effect.ID())
	if !ok || !detail.Abandoned || detail.Complete || detail.DeliveriesObserved != 0 {
		t.Fatal(detail)
	}
	last := detail.Events[len(detail.Events)-1]
	if last.Kind != "abandoned" || last.Attempt != nil || last.At != nil {
		t.Fatal(last)
	}
	if err = observer.DeliveryBegun(delivery); err != nil {
		t.Fatal("later observed boundary rejected", err)
	}
	if err = observer.DeliveryObserved(delivery, d.OutcomeAccepted); err != nil {
		t.Fatal(err)
	}
	if err = observer.DeliveryAbandoned(delivery); err != nil {
		t.Fatal(err)
	}
	if observer.TelemetryHealth().Abandoned != 1 {
		t.Fatal("abandonment double counted")
	}
}

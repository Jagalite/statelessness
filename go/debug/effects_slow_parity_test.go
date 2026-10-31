package debug_test

import (
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

func TestSlowTimingsReportsUnknownDurationIncomplete(t *testing.T) {
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
	clock.fail = true
	if err = observer.AttemptFinished(attempt, d.OutcomeSuccess); err != nil {
		t.Fatal(err)
	}
	result, err := observer.SlowTimings(d.SlowTimingQuery{Family: d.AttemptElapsed, MaxResults: 10, MaxResultBytes: 4096})
	if err != nil || result.UnknownMeasurements != 1 || result.Inspected != 1 || result.Complete || len(result.Matches) != 0 {
		t.Fatal(result, err)
	}
}

func TestSlowTimingsReportsIncompleteMatchingDetail(t *testing.T) {
	observer, _ := effectObserver(t)
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
	if err = observer.AttemptAbandoned(attempt); err != nil {
		t.Fatal(err)
	}
	result, err := observer.SlowTimings(d.SlowTimingQuery{Family: d.Resolution, MaxResults: 10, MaxResultBytes: 4096})
	if err != nil || result.Complete || result.Inspected != 0 {
		t.Fatal(result, err)
	}
}

func TestSlowTimingsMatchLabelsReflectTiming(t *testing.T) {
	observer, clock := effectObserver(t)
	effect := request(t, observer)
	if err := observer.Admission(effect, true); err != nil {
		t.Fatal(err)
	}
	attempt, err := observer.AttemptCreated(effect)
	if err != nil {
		t.Fatal(err)
	}
	observer.DebuggerPause(true)
	if err = observer.AttemptStarted(attempt); err != nil {
		t.Fatal(err)
	}
	clock.ns = 10
	if err = observer.AttemptFinished(attempt, d.OutcomeSuccess); err != nil {
		t.Fatal(err)
	}
	result, err := observer.SlowTimings(d.SlowTimingQuery{Family: d.AttemptElapsed, IncludeDebuggerAffected: true, MaxResults: 10, MaxResultBytes: 4096})
	if err != nil || len(result.Matches) != 1 || result.Matches[0].Labels.Outcome != d.OutcomeSuccess || !result.Matches[0].Labels.DebuggerAffected {
		t.Fatal(result, err)
	}
}

func TestPendingAgeDistinguishesRejectionAndMissingEndpoint(t *testing.T) {
	observer, clock := effectObserver(t)
	effect := request(t, observer)
	if err := observer.Admission(effect, false); err != nil {
		t.Fatal(err)
	}
	detail, ok := observer.EffectDetails(effect.ID())
	if !ok {
		t.Fatal("missing effect detail")
	}
	if age, err := detail.PendingAge(d.LocalInstant{Domain: 1, Nanos: 10}); age != nil || err != nil {
		t.Fatal("rejected effect reported pending", age, err)
	}
	clock.fail = true
	pending := request(t, observer)
	unknown, ok := observer.EffectDetails(pending.ID())
	if !ok {
		t.Fatal("missing effect detail")
	}
	if age, err := unknown.PendingAge(d.LocalInstant{Domain: 1, Nanos: 10}); age != nil || err == nil {
		t.Fatal("missing clock endpoint hidden", age, err)
	}
}

func TestSlowTimingsPreservesOwnedQueryAndProvenance(t *testing.T) {
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
	clock.ns = 10
	if err = observer.AttemptFinished(attempt, d.OutcomeSuccess); err != nil {
		t.Fatal(err)
	}
	origin, outcome := d.OriginLive, d.OutcomeSuccess
	query := d.SlowTimingQuery{Family: d.AttemptElapsed, ThresholdNS: 5, Origin: &origin, Outcome: &outcome, MaxResults: 1, MaxResultBytes: 4096}
	reads := clock.reads
	result, err := observer.SlowTimings(query)
	if err != nil || result.Run != 1 || result.Epoch != 1 || result.DetailRevision != 5 || result.ResultBytes <= 0 || result.ResultBytes > query.MaxResultBytes || len(result.Matches) != 1 || clock.reads != reads {
		t.Fatal(result, err)
	}
	origin, outcome = d.OriginImported, d.OutcomeFailure
	if result.Query.Origin == nil || *result.Query.Origin != d.OriginLive || result.Query.Outcome == nil || *result.Query.Outcome != d.OutcomeSuccess || result.Query.ThresholdNS != 5 {
		t.Fatal("query alias", result.Query)
	}
	boundedQuery := d.SlowTimingQuery{Family: d.AttemptElapsed, MaxResults: 0, MaxResultBytes: 4096}
	bounded, err := observer.SlowTimings(boundedQuery)
	if err != nil || !bounded.Truncated || bounded.Complete || bounded.Matched != 1 || len(bounded.Matches) != 0 {
		t.Fatal("count bound", bounded, err)
	}
	boundedQuery.MaxResults = 1
	boundedQuery.MaxResultBytes = bounded.ResultBytes
	byBytes, err := observer.SlowTimings(boundedQuery)
	if err != nil || !byBytes.Truncated || byBytes.ResultBytes != bounded.ResultBytes || len(byBytes.Matches) != 0 {
		t.Fatal("byte bound", byBytes, err)
	}
	boundedQuery.MaxResultBytes--
	if _, err = observer.SlowTimings(boundedQuery); err == nil {
		t.Fatal("undersized result budget accepted")
	}
	if err = observer.Resolved(effect, d.OutcomeSuccess, &attempt); err != nil {
		t.Fatal(err)
	}
	if result.DetailRevision != 5 {
		t.Fatal("result revision changed", result.DetailRevision)
	}
	bad := d.MeasurementOrigin("invalid")
	if _, err = observer.SlowTimings(d.SlowTimingQuery{Family: d.AttemptElapsed, Origin: &bad, MaxResultBytes: 4096}); err == nil {
		t.Fatal("invalid origin accepted")
	}
}

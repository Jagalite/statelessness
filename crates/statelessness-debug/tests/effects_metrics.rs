use statelessness_debug::effects::*;
use statelessness_debug::metrics::*;
use std::future::Future;
use std::pin::Pin;
use std::sync::{
    Arc,
    atomic::{AtomicBool, AtomicU64, Ordering},
};
use std::task::{Context, Poll, Waker};

#[derive(Default)]
struct FakeClock {
    ns: AtomicU64,
    domain: AtomicU64,
    reads: AtomicU64,
    fail: AtomicBool,
}
impl FakeClock {
    fn ms(&self, n: u64) {
        self.ns.store(n * 1_000_000, Ordering::SeqCst);
    }
}
impl Clock for FakeClock {
    fn now(&self) -> Result<LocalInstant, MeasurementError> {
        self.reads.fetch_add(1, Ordering::SeqCst);
        if self.fail.load(Ordering::SeqCst) {
            Err(MeasurementError::ClockUnavailable)
        } else {
            Ok(LocalInstant {
                domain: ClockDomain(self.domain.load(Ordering::SeqCst)),
                nanos: self.ns.load(Ordering::SeqCst),
            })
        }
    }
}
fn fixture(options: EffectOptions) -> (EffectObserver, Arc<FakeClock>) {
    let clock = Arc::new(FakeClock::default());
    (EffectObserver::new(options, clock.clone()).unwrap(), clock)
}
fn origin(n: u64) -> RequestOrigin {
    RequestOrigin {
        run: 1,
        epoch: 1,
        machine: 7,
        transition_sequence: n,
        output_index: 0,
    }
}
fn request(o: &EffectObserver, n: u64) -> EffectHandle {
    o.requested(origin(n), Labels::default()).unwrap()
}
fn admitted(o: &EffectObserver, n: u64) -> EffectHandle {
    let e = request(o, n);
    o.admission(&e, Admission::Accepted).unwrap();
    e
}
fn number(s: &MetricSnapshot, f: MetricFamily) -> u64 {
    s.series
        .iter()
        .filter(|(k, _)| k.family == f)
        .map(|(_, v)| match v {
            MetricValue::Counter(v) | MetricValue::Gauge(v) => *v,
            _ => 0,
        })
        .sum()
}
fn hist(s: &MetricSnapshot, f: MetricFamily) -> (u64, u128) {
    s.series
        .iter()
        .filter(|(k, _)| k.family == f)
        .map(|(_, v)| match v {
            MetricValue::Histogram(h) => (h.count, h.sum_ns),
            _ => (0, 0),
        })
        .fold((0, 0), |(a, b), (c, d)| (a + c, b + d))
}
fn simple(o: &EffectObserver, n: u64) -> EffectHandle {
    let e = admitted(o, n);
    let a = o.attempt_created(&e).unwrap();
    o.ready_queued(&a).unwrap();
    o.attempt_started(&a).unwrap();
    o.attempt_finished(&a, RuntimeOutcome::Success).unwrap();
    o.resolved(&e, RuntimeOutcome::Success, Some(&a)).unwrap();
    e
}
#[test]
fn synthetic_12_plus_84_plus_4_has_distinct_endpoints() {
    let (o, c) = fixture(EffectOptions::default());
    let e = admitted(&o, 1);
    let a = o.attempt_created(&e).unwrap();
    o.ready_queued(&a).unwrap();
    c.ms(12);
    o.attempt_started(&a).unwrap();
    c.ms(96);
    o.attempt_finished(&a, RuntimeOutcome::Success).unwrap();
    o.resolved(&e, RuntimeOutcome::Success, Some(&a)).unwrap();
    c.ms(97);
    let d = o.reserve_publication(&e).unwrap();
    o.publication_committed(&d).unwrap();
    c.ms(100);
    o.delivery_begun(&d).unwrap();
    c.ms(102);
    o.delivery_observed(&d, DeliveryDisposition::Ignored)
        .unwrap();
    let s = o.metric_snapshot();
    for (f, ms) in [
        (MetricFamily::QueueWait, 12),
        (MetricFamily::AttemptElapsed, 84),
        (MetricFamily::Resolution, 96),
        (MetricFamily::CompletionPreparation, 1),
        (MetricFamily::CompletionQueueWait, 3),
        (MetricFamily::DeliveryLag, 4),
        (MetricFamily::CompletionProcessing, 2),
        (MetricFamily::EndToEnd, 100),
        (MetricFamily::EndToEndObserved, 102),
    ] {
        assert_eq!(hist(&s, f), (1, ms * 1_000_000), "{f:?}");
    }
    assert_eq!(hist(&s, MetricFamily::ResolutionOverhead), (1, 0)); // positively observed same boundary
    assert_eq!(hist(&s, MetricFamily::Settlement), (0, 0)); // unknown is not a zero sample
    assert_eq!(number(&s, MetricFamily::Unresolved), 0);
    assert_eq!(number(&s, MetricFamily::Running), 0);
    assert_eq!(number(&s, MetricFamily::Deliveries), 1);
    assert_eq!(
        o.effect_details(e.id()).unwrap().origin.transition_sequence,
        1
    );
    assert!(!o.effect_details(e.id()).unwrap().settled);
}
#[test]
fn admission_retry_eligibility_queue_and_delayed_post_are_not_collapsed() {
    let (o, c) = fixture(EffectOptions::default());
    let e = request(&o, 1);
    c.ms(3);
    o.admission(&e, Admission::Accepted).unwrap();
    let a = o.attempt_created(&e).unwrap();
    c.ms(5);
    o.ready_queued(&a).unwrap();
    c.ms(7);
    o.attempt_started(&a).unwrap();
    c.ms(10);
    o.attempt_finished(&a, RuntimeOutcome::Failure).unwrap();
    let b = o.attempt_created(&e).unwrap();
    o.retry_scheduled(&b, 10_000_000).unwrap();
    c.ms(22);
    o.ready_queued(&b).unwrap();
    c.ms(25);
    o.attempt_started(&b).unwrap();
    c.ms(30);
    o.attempt_finished(&b, RuntimeOutcome::Success).unwrap();
    c.ms(31);
    o.resolved(&e, RuntimeOutcome::Success, Some(&b)).unwrap();
    c.ms(40);
    let d = o.reserve_publication(&e).unwrap();
    o.publication_committed(&d).unwrap();
    c.ms(44);
    o.delivery_begun(&d).unwrap();
    c.ms(45);
    o.delivery_observed(&d, DeliveryDisposition::Rejected)
        .unwrap();
    c.ms(50);
    o.settled(&e).unwrap();
    let s = o.metric_snapshot();
    assert_eq!(number(&s, MetricFamily::Attempts), 2);
    assert_eq!(number(&s, MetricFamily::Resolutions), 1);
    assert_eq!(number(&s, MetricFamily::AttemptOutcomes), 2);
    for (f, n, ms) in [
        (MetricFamily::AdmissionDelay, 1, 5),
        (MetricFamily::ScheduledBackoff, 1, 10),
        (MetricFamily::EligibilityWait, 1, 12),
        (MetricFamily::InterAttemptGap, 1, 15),
        (MetricFamily::QueueWait, 2, 5),
        (MetricFamily::CompletionPreparation, 1, 9),
        (MetricFamily::CompletionQueueWait, 1, 4),
        (MetricFamily::DeliveryLag, 1, 13),
        (MetricFamily::Settlement, 1, 50),
    ] {
        assert_eq!(hist(&s, f), (n, ms * 1_000_000), "{f:?}");
    }
}
#[test]
fn cancellation_and_resolution_do_not_terminate_running_attempts() {
    let (o, c) = fixture(EffectOptions::default());
    let e = admitted(&o, 1);
    let a = o.attempt_created(&e).unwrap();
    o.attempt_started(&a).unwrap();
    c.ms(1);
    o.cancellation_requested(&e).unwrap();
    o.cancellation_acknowledged(&e).unwrap();
    o.resolved(&e, RuntimeOutcome::Cancelled, None).unwrap();
    let s = o.metric_snapshot();
    assert_eq!(number(&s, MetricFamily::Running), 1);
    assert_eq!(number(&s, MetricFamily::Unresolved), 0);
    assert_eq!(number(&s, MetricFamily::AttemptOutcomes), 0);
    c.ms(9);
    o.attempt_finished(&a, RuntimeOutcome::Success).unwrap();
    let s = o.metric_snapshot();
    assert_eq!(number(&s, MetricFamily::Running), 0);
    assert_eq!(number(&s, MetricFamily::Resolutions), 1);
    assert_eq!(hist(&s, MetricFamily::AttemptElapsed), (1, 9_000_000));
    let details = o.effect_details(e.id()).unwrap();
    assert_eq!(details.resolution, Some(RuntimeOutcome::Cancelled));
    assert!(details.cancellation_requested && details.cancellation_acknowledged);
}
#[test]
fn duplicates_distinguish_notifications_from_real_completion_deliveries() {
    let (o, _) = fixture(EffectOptions::default());
    let e = simple(&o, 1);
    assert_eq!(
        o.resolved(&e, RuntimeOutcome::Success, None),
        Err(LifecycleError::DuplicateObservation)
    );
    for _ in 0..2 {
        let d = o.reserve_publication(&e).unwrap();
        o.delivery_begun(&d).unwrap();
        o.delivery_observed(&d, DeliveryDisposition::Accepted)
            .unwrap();
        assert_eq!(
            o.delivery_observed(&d, DeliveryDisposition::Accepted),
            Err(LifecycleError::DuplicateObservation)
        );
    }
    let s = o.metric_snapshot();
    assert_eq!(number(&s, MetricFamily::Deliveries), 2);
    assert_eq!(number(&s, MetricFamily::DuplicateDeliveries), 1);
    assert_eq!(number(&s, MetricFamily::DuplicateObservations), 3);
    assert_eq!(hist(&s, MetricFamily::EndToEnd).0, 1);
    assert_eq!(hist(&s, MetricFamily::DeliveryLag).0, 2);
}
#[test]
fn rejected_and_fire_and_forget_have_no_invented_completion() {
    let (o, _) = fixture(EffectOptions::default());
    let rejected = request(&o, 1);
    o.admission(&rejected, Admission::Rejected).unwrap();
    assert!(o.attempt_created(&rejected).is_err());
    let _resolved = simple(&o, 2);
    let pending = admitted(&o, 3);
    let s = o.metric_snapshot();
    assert_eq!(number(&s, MetricFamily::Requests), 3);
    assert_eq!(number(&s, MetricFamily::Unresolved), 1);
    assert_eq!(number(&s, MetricFamily::Deliveries), 0);
    assert_eq!(hist(&s, MetricFamily::EndToEnd).0, 0);
    assert_eq!(
        o.effect_details(pending.id())
            .unwrap()
            .pending_age(LocalInstant {
                domain: ClockDomain(0),
                nanos: 123
            }),
        Some(Ok(123))
    );
}
#[test]
fn detail_eviction_does_not_evict_live_accounting() {
    let (o, _) = fixture(EffectOptions {
        max_details: 1,
        max_events_per_effect: 2,
        ..EffectOptions::default()
    });
    let e = admitted(&o, 1);
    let a = o.attempt_created(&e).unwrap();
    o.attempt_started(&a).unwrap();
    let _second = admitted(&o, 2);
    assert!(o.effect_details(e.id()).is_none());
    let s = o.metric_snapshot();
    assert_eq!(number(&s, MetricFamily::Unresolved), 2);
    assert_eq!(number(&s, MetricFamily::Running), 1);
    o.resolved(&e, RuntimeOutcome::Cancelled, None).unwrap();
    assert_eq!(number(&o.metric_snapshot(), MetricFamily::Running), 1);
    o.attempt_finished(&a, RuntimeOutcome::Cancelled).unwrap();
    assert_eq!(number(&o.metric_snapshot(), MetricFamily::Running), 0);
    assert_eq!(o.telemetry_health().detail_evictions, 1);
}
#[test]
fn reversed_cross_domain_and_failed_clocks_never_make_zero_samples() {
    for mode in 0..3 {
        let (o, c) = fixture(EffectOptions::default());
        c.ms(10);
        let e = admitted(&o, 1);
        let a = o.attempt_created(&e).unwrap();
        o.attempt_started(&a).unwrap();
        match mode {
            0 => c.ms(5),
            1 => c.domain.store(4, Ordering::SeqCst),
            _ => c.fail.store(true, Ordering::SeqCst),
        }
        o.attempt_finished(&a, RuntimeOutcome::Failure).unwrap();
        let s = o.metric_snapshot();
        assert_eq!(hist(&s, MetricFamily::AttemptElapsed).0, 0);
        assert!(!s.complete);
        assert_eq!(number(&s, MetricFamily::AttemptOutcomes), 1);
        let details = o.effect_details(e.id()).unwrap();
        assert!(
            details
                .timings
                .iter()
                .find(|t| t.family == MetricFamily::AttemptElapsed)
                .unwrap()
                .duration_ns
                .is_err()
        );
    }
}
#[test]
fn disabled_and_counters_only_do_not_read_clock() {
    for counters in [false, true] {
        let (o, c) = fixture(EffectOptions {
            counters,
            timings: false,
            details: false,
            ..EffectOptions::default()
        });
        let e = simple(&o, 1);
        let d = o.reserve_publication(&e).unwrap();
        o.delivery_begun(&d).unwrap();
        o.delivery_observed(&d, DeliveryDisposition::Accepted)
            .unwrap();
        let s = o.metric_snapshot();
        assert_eq!(c.reads.load(Ordering::SeqCst), 0);
        assert_eq!(number(&s, MetricFamily::Requests), u64::from(counters));
        assert!(
            s.series
                .values()
                .all(|v| !matches!(v, MetricValue::Histogram(_)))
        );
        assert!(o.effect_details(e.id()).is_none());
    }
}
#[test]
fn metric_only_capture_and_imports_are_independent_from_log_delivery() {
    let (o, _) = fixture(EffectOptions {
        details: false,
        ..EffectOptions::default()
    });
    let e = simple(&o, 1);
    assert_eq!(
        hist(&o.metric_snapshot(), MetricFamily::AttemptElapsed).0,
        1
    );
    assert!(o.effect_timeline(e.id()).is_none());
    for origin in [
        MeasurementOrigin::ImportedLive,
        MeasurementOrigin::ReplayHarness,
    ] {
        let (o, _) = fixture(EffectOptions {
            origin,
            ..EffectOptions::default()
        });
        let _e = simple(&o, 1);
        assert!(o.metric_snapshot().series.is_empty());
        assert!(o.telemetry_health().metric.imported_ignored > 0);
    }
}
#[test]
fn debugger_pause_marks_samples_without_subtracting_time() {
    let (o, c) = fixture(EffectOptions::default());
    let e = admitted(&o, 1);
    let a = o.attempt_created(&e).unwrap();
    o.attempt_started(&a).unwrap();
    c.ms(2);
    o.debugger_pause(true);
    c.ms(20);
    o.debugger_pause(false);
    c.ms(25);
    o.attempt_finished(&a, RuntimeOutcome::Success).unwrap();
    o.resolved(&e, RuntimeOutcome::Success, Some(&a)).unwrap();
    let s = o.metric_snapshot();
    assert_eq!(hist(&s, MetricFamily::AttemptElapsed), (1, 25_000_000));
    assert_eq!(hist(&s.production(), MetricFamily::AttemptElapsed).0, 0);
    assert!(o.telemetry_health().debugger_affected_measurements > 0);
}
#[test]
fn publication_failure_invalidates_reservation_without_invented_post_sample() {
    let (o, _) = fixture(EffectOptions::default());
    let e = simple(&o, 1);
    let d = o.reserve_publication(&e).unwrap();
    assert_eq!(
        number(&o.metric_snapshot(), MetricFamily::CompletionDepth),
        1
    );
    o.publication_failed(&d).unwrap();
    assert!(o.delivery_begun(&d).is_err());
    assert_eq!(
        number(&o.metric_snapshot(), MetricFamily::CompletionDepth),
        0
    );
    assert_eq!(
        hist(&o.metric_snapshot(), MetricFamily::CompletionPreparation).0,
        0
    );
}
#[test]
fn concurrent_consumer_can_win_publication_confirmation_race() {
    let (o, c) = fixture(EffectOptions::default());
    let e = simple(&o, 1);
    c.ms(3);
    let d = o.reserve_publication(&e).unwrap();
    let worker = o.clone();
    let token = d.clone();
    c.ms(4);
    std::thread::spawn(move || {
        worker.delivery_begun(&token).unwrap();
        worker
            .delivery_observed(&token, DeliveryDisposition::Ignored)
            .unwrap();
    })
    .join()
    .unwrap();
    o.publication_committed(&d).unwrap();
    let s = o.metric_snapshot();
    assert_eq!(
        hist(&s, MetricFamily::CompletionPreparation),
        (1, 3_000_000)
    );
    assert_eq!(hist(&s, MetricFamily::CompletionQueueWait), (1, 1_000_000));
    assert_eq!(number(&s, MetricFamily::CompletionDepth), 0);
}
struct TwoPolls {
    polls: Arc<AtomicU64>,
}
impl Future for TwoPolls {
    type Output = u8;
    fn poll(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<u8> {
        if self.polls.fetch_add(1, Ordering::SeqCst) == 0 {
            Poll::Pending
        } else {
            Poll::Ready(42)
        }
    }
}
#[test]
fn real_std_future_first_poll_pending_ready_and_never_polled_drop() {
    let (o, c) = fixture(EffectOptions::default());
    let e = admitted(&o, 1);
    let a = o.attempt_created(&e).unwrap();
    let polls = Arc::new(AtomicU64::new(0));
    let mut future = Box::pin(o.instrument_future(
        a,
        TwoPolls {
            polls: polls.clone(),
        },
        |_| RuntimeOutcome::Success,
    ));
    assert_eq!(number(&o.metric_snapshot(), MetricFamily::Attempts), 0);
    c.ms(10);
    let mut cx = Context::from_waker(Waker::noop());
    assert!(future.as_mut().poll(&mut cx).is_pending());
    assert_eq!(number(&o.metric_snapshot(), MetricFamily::Running), 1);
    c.ms(22);
    assert_eq!(future.as_mut().poll(&mut cx), Poll::Ready(42));
    assert_eq!(polls.load(Ordering::SeqCst), 2);
    assert_eq!(
        hist(&o.metric_snapshot(), MetricFamily::AttemptElapsed),
        (1, 12_000_000)
    );
    let never = o.attempt_created(&e).unwrap();
    drop(o.instrument_future(
        never,
        TwoPolls {
            polls: polls.clone(),
        },
        |_| RuntimeOutcome::Success,
    ));
    assert_eq!(polls.load(Ordering::SeqCst), 2);
    assert_eq!(number(&o.metric_snapshot(), MetricFamily::Attempts), 1);
    assert_eq!(o.telemetry_health().abandoned, 1);
}
#[test]
fn dropped_pending_future_is_abandoned_not_confirmed_cancelled() {
    let (o, _) = fixture(EffectOptions::default());
    let e = admitted(&o, 1);
    let a = o.attempt_created(&e).unwrap();
    let mut future = Box::pin(o.instrument_future(
        a,
        TwoPolls {
            polls: Arc::new(AtomicU64::new(0)),
        },
        |_| RuntimeOutcome::Success,
    ));
    let mut cx = Context::from_waker(Waker::noop());
    assert!(future.as_mut().poll(&mut cx).is_pending());
    drop(future);
    let s = o.metric_snapshot();
    assert_eq!(number(&s, MetricFamily::Running), 1);
    assert_eq!(number(&s, MetricFamily::AttemptOutcomes), 0);
    assert_eq!(number(&s, MetricFamily::CancellationAcknowledgements), 0);
    assert!(o.effect_details(e.id()).unwrap().abandoned);
}
#[test]
fn host_snapshot_is_explicit_and_default_is_observed_since_attach() {
    let (o, _) = fixture(EffectOptions::default());
    assert_eq!(
        o.metric_snapshot().gauge_scope,
        GaugeScope::ObservedSinceAttach
    );
    o.host_gauge_snapshot(Labels::default(), 7, 4, 2, 3)
        .unwrap();
    let s = o.metric_snapshot();
    assert_eq!(s.gauge_scope, GaugeScope::HostSnapshot);
    assert_eq!(number(&s, MetricFamily::Unresolved), 7);
    assert_eq!(number(&s, MetricFamily::Running), 4);
}
fn store(limits: MetricLimits) -> MetricsStore {
    MetricsStore::new(
        1,
        Some(SnapshotTime {
            domain: 1,
            nanos: 0,
        }),
        LabelCatalog::default(),
        HistogramSchema {
            id: 77,
            finite_bounds_ns: vec![1, 10, 100],
        },
        limits,
        GaugeScope::CompleteParticipation,
    )
    .unwrap()
}
#[test]
fn windows_epochs_schema_and_history_are_honest() {
    let mut m = store(MetricLimits::default());
    m.observe(MetricFamily::AttemptElapsed, Labels::default(), 1);
    let a = m.snapshot(Some(SnapshotTime {
        domain: 1,
        nanos: 5,
    }));
    m.observe(MetricFamily::AttemptElapsed, Labels::default(), 10);
    let b = m.snapshot(Some(SnapshotTime {
        domain: 1,
        nanos: 10,
    }));
    let delta = m.window(a.revision, b.revision).unwrap();
    assert_eq!(hist(&delta, MetricFamily::AttemptElapsed), (1, 10));
    assert_eq!(delta.temporality, Temporality::Delta);
    assert_eq!(delta.start, a.end);
    let mut other = b.clone();
    other.epoch += 1;
    assert_eq!(
        MetricSnapshot::window(&a, &other),
        Err(MetricError::Incompatible)
    );
    other = b.clone();
    other.schema.id += 1;
    assert_eq!(
        MetricSnapshot::window(&a, &other),
        Err(MetricError::Incompatible)
    );
    assert_eq!(
        m.window(b.revision, a.revision),
        Err(MetricError::ReversedWindow)
    );
    for t in 11..24 {
        m.snapshot(Some(SnapshotTime {
            domain: 1,
            nanos: t,
        }));
    }
    assert_eq!(m.available_windows().len(), 12);
    assert_eq!(
        m.window(a.revision, b.revision),
        Err(MetricError::UnknownWindow)
    );
    assert!(m.health().snapshots_evicted > 0);
}
#[test]
fn quantiles_merge_buckets_and_zero_is_unavailable() {
    let mut left = store(MetricLimits::default());
    let mut right = store(MetricLimits::default());
    let empty = Histogram::default();
    assert!(
        empty
            .quantile(&HistogramSchema::default(), 99, 100)
            .is_none()
    );
    for _ in 0..100 {
        left.observe(MetricFamily::AttemptElapsed, Labels::default(), 1);
    }
    right.observe(MetricFamily::AttemptElapsed, Labels::default(), 100);
    let time = Some(SnapshotTime {
        domain: 1,
        nanos: 10,
    });
    let mut a = left.snapshot(time);
    let b = right.snapshot(time);
    let small = b
        .histogram(MetricFamily::AttemptElapsed, Labels::default())
        .unwrap()
        .quantile(&b.schema, 99, 100)
        .unwrap();
    assert!(small.low_sample_warning);
    assert_eq!(small.upper_bound_ns, Some(100));
    a.merge_disjoint(&b).unwrap();
    let merged = a
        .histogram(MetricFamily::AttemptElapsed, Labels::default())
        .unwrap()
        .quantile(&a.schema, 99, 100)
        .unwrap();
    assert_eq!(merged.observations, 101);
    assert_eq!(merged.upper_bound_ns, Some(1));
    assert!(!merged.low_sample_warning);
}
#[test]
fn aggregate_cardinality_and_bytes_reject_with_independent_health() {
    let mut m = store(MetricLimits {
        max_series: 2,
        ..MetricLimits::default()
    });
    for outcome in [Outcome::Success, Outcome::Failure, Outcome::Cancelled] {
        m.increment(
            MetricFamily::Resolutions,
            Labels {
                outcome,
                ..Labels::default()
            },
            1,
        );
    }
    assert_eq!(m.current().series.len(), 2);
    assert_eq!(m.health().rejected_series, 1);
    assert!(!m.current().complete);
    m.increment(
        MetricFamily::Requests,
        Labels {
            operation: 999,
            ..Labels::default()
        },
        1,
    );
    assert_eq!(m.health().invalid_labels, 1);
    let mut m = store(MetricLimits {
        max_bytes: 1200,
        ..MetricLimits::default()
    });
    for outcome in [Outcome::Success, Outcome::Failure, Outcome::Cancelled] {
        m.observe(
            MetricFamily::AttemptElapsed,
            Labels {
                outcome,
                ..Labels::default()
            },
            100,
        );
    }
    assert!(m.current().estimated_bytes() <= 1200);
    assert!(m.health().rejected_series > 0);
}
#[test]
fn gauge_underflow_and_counter_reset_are_explicit() {
    let mut m = store(MetricLimits::default());
    m.gauge_delta(MetricFamily::Running, Labels::default(), -1);
    assert_eq!(m.current().gauge_scope, GaugeScope::Unknown);
    assert_eq!(m.health().arithmetic_errors, 1);
    m.increment(MetricFamily::Requests, Labels::default(), 2);
    let a = m.snapshot(None);
    let mut b = a.clone();
    b.revision += 1;
    b.series.insert(
        SeriesKey {
            family: MetricFamily::Requests,
            labels: Labels::default(),
        },
        MetricValue::Counter(1),
    );
    assert_eq!(
        MetricSnapshot::window(&a, &b),
        Err(MetricError::CounterReset)
    );
}
#[test]
fn custom_metric_sink_receives_cumulative_schema_and_population() {
    struct Sink(Vec<MetricSnapshot>);
    impl MetricSink for Sink {
        type Error = ();
        fn export(&mut self, s: &MetricSnapshot) -> Result<(), ()> {
            self.0.push(s.clone());
            Ok(())
        }
    }
    let (o, _) = fixture(EffectOptions::default());
    let _e = simple(&o, 1);
    let mut sink = Sink(vec![]);
    sink.export(&o.metric_snapshot()).unwrap();
    assert_eq!(sink.0[0].temporality, Temporality::Cumulative);
    assert_eq!(sink.0[0].epoch, 1);
    assert!(
        o.metric_catalog()
            .iter()
            .all(|d| !d.population.is_empty() && !d.allowed_labels.contains(&"effect_id"))
    );
}
#[test]
fn merges_enforce_family_limit_and_invalid_snapshot_clocks_are_unknown() {
    let limits = MetricLimits {
        max_families: 1,
        ..MetricLimits::default()
    };
    let mut a = store(limits.clone());
    let mut b = store(limits);
    a.increment(MetricFamily::Requests, Labels::default(), 1);
    b.increment(MetricFamily::Attempts, Labels::default(), 1);
    let mut a = a.snapshot(Some(SnapshotTime {
        domain: 1,
        nanos: 10,
    }));
    let b = b.snapshot(Some(SnapshotTime {
        domain: 1,
        nanos: 10,
    }));
    assert_eq!(a.merge_disjoint(&b), Err(MetricError::Overflow));
    assert_eq!(a.series.len(), 1);
    let mut m = store(MetricLimits::default());
    m.snapshot(Some(SnapshotTime {
        domain: 1,
        nanos: 10,
    }));
    let invalid = m.snapshot(Some(SnapshotTime {
        domain: 1,
        nanos: 9,
    }));
    assert!(invalid.end.is_none());
    assert!(!invalid.complete);
    let cross = m.snapshot(Some(SnapshotTime {
        domain: 2,
        nanos: 100,
    }));
    assert!(cross.end.is_none());
    assert_eq!(cross.health.invalid_measurements, 2);
}
#[test]
fn classifier_fault_cannot_replace_completed_future_value() {
    let (o, _) = fixture(EffectOptions::default());
    let e = admitted(&o, 1);
    let a = o.attempt_created(&e).unwrap();
    let mut f = Box::pin(o.instrument_future(a, std::future::ready(77), |_| {
        panic!("synthetic classifier fault")
    }));
    let mut cx = Context::from_waker(Waker::noop());
    assert_eq!(f.as_mut().poll(&mut cx), Poll::Ready(77));
    assert_eq!(o.telemetry_health().callback_faults, 1);
    let s = o.metric_snapshot();
    assert_eq!(number(&s, MetricFamily::Running), 0);
    assert_eq!(number(&s, MetricFamily::AttemptOutcomes), 1);
    assert!(!s.complete);
}
#[test]
fn host_baseline_does_not_upgrade_other_label_groups() {
    let (o, _) = fixture(EffectOptions {
        labels: LabelCatalog {
            operations: vec!["one".into(), "two".into()],
            ..LabelCatalog::default()
        },
        ..EffectOptions::default()
    });
    o.host_gauge_snapshot(Labels::default(), 2, 1, 0, 0)
        .unwrap();
    assert_eq!(o.metric_snapshot().gauge_scope, GaugeScope::HostSnapshot);
    let labels = Labels {
        operation: 1,
        ..Labels::default()
    };
    let e = o.requested(origin(3), labels).unwrap();
    o.admission(&e, Admission::Accepted).unwrap();
    let s = o.metric_snapshot();
    assert_eq!(s.gauge_scope, GaugeScope::Unknown);
    assert_eq!(
        s.gauge_scopes[&SeriesKey {
            family: MetricFamily::Unresolved,
            labels
        }],
        GaugeScope::ObservedSinceAttach
    );
    assert_eq!(
        s.gauge_scopes[&SeriesKey {
            family: MetricFamily::Unresolved,
            labels: Labels::default()
        }],
        GaugeScope::HostSnapshot
    );
}
#[test]
fn byte_budgeted_snapshots_invalid_schema_and_wrong_instrument_are_visible() {
    let mut m = store(MetricLimits {
        max_snapshot_bytes: 1,
        ..MetricLimits::default()
    });
    m.snapshot(None);
    assert_eq!(m.health().snapshot_rejections, 1);
    assert!(m.available_windows().is_empty());
    assert!(
        MetricsStore::new(
            1,
            None,
            LabelCatalog::default(),
            HistogramSchema {
                id: 2,
                finite_bounds_ns: (0..65).collect()
            },
            MetricLimits::default(),
            GaugeScope::Unknown
        )
        .is_err()
    );
    let mut m = store(MetricLimits::default());
    m.gauge_delta(MetricFamily::Requests, Labels::default(), 1);
    m.increment(MetricFamily::AttemptElapsed, Labels::default(), 1);
    m.observe(MetricFamily::Running, Labels::default(), 1);
    m.set_gauge(MetricFamily::Attempts, Labels::default(), 1);
    assert!(m.current().series.is_empty());
    assert!(m.current().gauge_scopes.is_empty());
    assert_eq!(m.health().arithmetic_errors, 4);
}
#[test]
fn pause_interval_retention_is_bounded_and_completed_durations_unchanged() {
    let (o, c) = fixture(EffectOptions {
        max_pause_intervals: 1,
        ..EffectOptions::default()
    });
    c.ms(1);
    o.debugger_pause(true);
    c.ms(2);
    o.debugger_pause(false);
    c.ms(3);
    o.debugger_pause(true);
    c.ms(4);
    o.debugger_pause(false);
    let intervals = o.pause_intervals();
    assert_eq!(intervals.len(), 1);
    assert_eq!(intervals[0].start.unwrap().nanos, 3_000_000);
    assert_eq!(intervals[0].end.unwrap().nanos, 4_000_000);
    assert_eq!(o.telemetry_health().pause_interval_evictions, 1);
}
#[test]
fn completed_slow_queries_distinguish_kind_queue_execution_and_delivery() {
    let (o, c) = fixture(EffectOptions {
        labels: LabelCatalog {
            operations: vec!["Fetch".into(), "Write".into()],
            ..LabelCatalog::default()
        },
        ..EffectOptions::default()
    });
    let e = admitted(&o, 1);
    let a = o.attempt_created(&e).unwrap();
    o.ready_queued(&a).unwrap();
    c.ms(5);
    o.attempt_started(&a).unwrap();
    c.ms(15);
    o.attempt_finished(&a, RuntimeOutcome::Success).unwrap();
    o.resolved(&e, RuntimeOutcome::Success, Some(&a)).unwrap();
    c.ms(16);
    let d = o.reserve_publication(&e).unwrap();
    c.ms(19);
    o.delivery_begun(&d).unwrap();
    c.ms(20);
    o.delivery_observed(&d, DeliveryDisposition::Accepted)
        .unwrap();
    let reads = c.reads.load(Ordering::SeqCst);
    let queue = o
        .slow_timings(SlowTimingQuery::new(0, MetricFamily::QueueWait, 6_000_000))
        .unwrap();
    assert!(queue.matches.is_empty());
    assert!(queue.complete);
    let execution = o
        .slow_timings(SlowTimingQuery::new(
            0,
            MetricFamily::AttemptElapsed,
            9_000_000,
        ))
        .unwrap();
    assert_eq!(execution.matched, 1);
    assert_eq!(execution.matches[0].effect, e.id());
    assert_eq!(execution.matches[0].labels.outcome, Outcome::Success);
    let delivery = o
        .slow_timings(SlowTimingQuery::new(
            0,
            MetricFamily::DeliveryLag,
            4_000_000,
        ))
        .unwrap();
    assert_eq!(delivery.matches.len(), 1);
    assert_eq!(delivery.matches[0].timing.delivery, Some(d.id()));
    assert!(
        o.slow_timings(SlowTimingQuery::new(1, MetricFamily::AttemptElapsed, 0))
            .unwrap()
            .matches
            .is_empty()
    );
    assert_eq!(c.reads.load(Ordering::SeqCst), reads);
}
#[test]
fn slow_queries_bound_results_and_report_retention_gaps_unknowns_and_disabled_capture() {
    let (o, _) = fixture(EffectOptions::default());
    let _a = simple(&o, 1);
    let _b = simple(&o, 2);
    let mut query = SlowTimingQuery::new(0, MetricFamily::AttemptElapsed, 0);
    query.max_results = 1;
    let result = o.slow_timings(query.clone()).unwrap();
    assert_eq!(result.matched, 2);
    assert_eq!(result.matches.len(), 1);
    assert!(result.truncated && !result.complete);
    query.max_results = 64;
    query.max_result_bytes = std::mem::size_of::<SlowTimingResult>();
    let result = o.slow_timings(query).unwrap();
    assert_eq!(result.matches.len(), 0);
    assert!(result.truncated);
    assert_eq!(result.result_bytes, std::mem::size_of::<SlowTimingResult>());
    let (o, _) = fixture(EffectOptions {
        max_details: 1,
        ..EffectOptions::default()
    });
    let _a = simple(&o, 1);
    let _b = simple(&o, 2);
    assert!(
        !o.slow_timings(SlowTimingQuery::new(0, MetricFamily::AttemptElapsed, 0))
            .unwrap()
            .complete
    );
    for (timings, details, expected) in [
        (false, true, SlowTimingAvailability::TimingDisabled),
        (true, false, SlowTimingAvailability::DetailsDisabled),
    ] {
        let (o, c) = fixture(EffectOptions {
            timings,
            details,
            ..EffectOptions::default()
        });
        let _e = simple(&o, 1);
        let before = c.reads.load(Ordering::SeqCst);
        let result = o
            .slow_timings(SlowTimingQuery::new(0, MetricFamily::AttemptElapsed, 0))
            .unwrap();
        assert_eq!(result.availability, expected);
        assert!(!result.complete);
        assert_eq!(c.reads.load(Ordering::SeqCst), before);
    }
    let (o, c) = fixture(EffectOptions::default());
    c.ms(10);
    let e = admitted(&o, 1);
    let a = o.attempt_created(&e).unwrap();
    o.attempt_started(&a).unwrap();
    c.ms(5);
    o.attempt_finished(&a, RuntimeOutcome::Failure).unwrap();
    let result = o
        .slow_timings(SlowTimingQuery::new(0, MetricFamily::AttemptElapsed, 0))
        .unwrap();
    assert_eq!(result.unknown_measurements, 1);
    assert!(result.matches.is_empty() && !result.complete);
}
#[test]
fn slow_query_population_requires_explicit_debugger_and_origin_inclusion() {
    let (o, c) = fixture(EffectOptions {
        origin: MeasurementOrigin::TestClock,
        ..EffectOptions::default()
    });
    let e = admitted(&o, 1);
    let a = o.attempt_created(&e).unwrap();
    o.attempt_started(&a).unwrap();
    o.debugger_pause(true);
    c.ms(10);
    o.attempt_finished(&a, RuntimeOutcome::Failure).unwrap();
    let mut query = SlowTimingQuery::new(0, MetricFamily::AttemptElapsed, 1);
    assert!(o.slow_timings(query.clone()).unwrap().matches.is_empty());
    query.measurement_origin = Some(MeasurementOrigin::TestClock);
    assert!(o.slow_timings(query.clone()).unwrap().matches.is_empty());
    query.include_debugger_affected = true;
    query.outcome = Some(Outcome::Success);
    assert!(o.slow_timings(query.clone()).unwrap().matches.is_empty());
    query.outcome = Some(Outcome::Failure);
    let result = o.slow_timings(query).unwrap();
    assert_eq!(result.matches.len(), 1);
    assert_eq!(
        result.matches[0].labels.origin,
        MeasurementOrigin::TestClock
    );
    assert!(result.matches[0].labels.debugger_affected);
}

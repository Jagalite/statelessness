//! Independent pre-PR regressions for live authority and host effect accounting.
use stateless::demo::{Input, Output, RequestModel, State};
use stateless::execution::{CheckPolicy, ReplayOptions, check_initial, check_turn, replay};
use stateless::monitor::RecorderOptions;
use stateless::trace::RunConfig;
use stateless::{Check, Model, ModelCodec, ModelError, ModelMetadata, Transition, TransitionRef};
use statelessness_debug::{effects::*, live, metrics::*};
use std::cell::Cell;
use std::sync::{
    Arc, Barrier,
    atomic::{AtomicU64, Ordering},
};

#[derive(Default)]
struct TestClock(AtomicU64);
impl TestClock {
    fn set(&self, nanos: u64) {
        self.0.store(nanos, Ordering::SeqCst);
    }
}
impl Clock for TestClock {
    fn now(&self) -> Result<LocalInstant, MeasurementError> {
        Ok(LocalInstant {
            domain: ClockDomain(73),
            nanos: self.0.load(Ordering::SeqCst),
        })
    }
}
fn telemetry() -> (EffectObserver, Arc<TestClock>) {
    let clock = Arc::new(TestClock::default());
    (
        EffectObserver::new(EffectOptions::default(), clock.clone()).unwrap(),
        clock,
    )
}
fn requested(o: &EffectObserver, sequence: u64) -> EffectHandle {
    o.requested(
        RequestOrigin {
            run: 1,
            epoch: 1,
            machine: 9,
            transition_sequence: sequence,
            output_index: 0,
        },
        Labels::default(),
    )
    .unwrap()
}
fn admitted(o: &EffectObserver) -> EffectHandle {
    let e = requested(o, 1);
    o.admission(&e, Admission::Accepted).unwrap();
    e
}
fn count(snapshot: &MetricSnapshot, family: MetricFamily) -> u64 {
    snapshot
        .series
        .iter()
        .filter(|(k, _)| k.family == family)
        .map(|(_, v)| match v {
            MetricValue::Counter(n) | MetricValue::Gauge(n) => *n,
            MetricValue::Histogram(h) => h.count,
        })
        .sum()
}
fn store() -> MetricsStore {
    MetricsStore::new(
        1,
        Some(SnapshotTime {
            domain: 73,
            nanos: 0,
        }),
        LabelCatalog::default(),
        HistogramSchema::default(),
        MetricLimits::default(),
        GaugeScope::CompleteParticipation,
    )
    .unwrap()
}

#[test]
fn rejected_admission_is_not_censored_pending_work() {
    let (o, clock) = telemetry();
    let e = requested(&o, 1);
    o.admission(&e, Admission::Rejected).unwrap();
    clock.set(1_000);
    assert_eq!(
        o.effect_details(e.id())
            .unwrap()
            .pending_age(clock.now().unwrap()),
        None
    );
    assert_eq!(count(&o.metric_snapshot(), MetricFamily::Unresolved), 0);
}

#[test]
fn retry_scheduled_during_pause_never_enters_production_histograms() {
    let (o, clock) = telemetry();
    let e = admitted(&o);
    let a = o.attempt_created(&e).unwrap();
    clock.set(5);
    o.debugger_pause(true);
    o.retry_scheduled(&a, 25).unwrap();
    let snapshot = o.metric_snapshot();
    assert_eq!(count(&snapshot, MetricFamily::ScheduledBackoff), 1);
    assert_eq!(
        count(&snapshot.production(), MetricFamily::ScheduledBackoff),
        0
    );
    assert_eq!(o.telemetry_health().debugger_affected_measurements, 1);
    let details = o.effect_details(e.id()).unwrap();
    assert!(details.debugger_affected);
    assert!(details.events.last().unwrap().debugger_affected);
}

#[test]
fn abandoned_or_resolved_attempt_cannot_be_scheduled_as_a_retry() {
    for resolved in [false, true] {
        let (o, _) = telemetry();
        let e = admitted(&o);
        let a = o.attempt_created(&e).unwrap();
        if resolved {
            o.resolved(&e, RuntimeOutcome::Cancelled, None).unwrap();
        } else {
            o.attempt_abandoned(&a).unwrap();
        }
        assert_eq!(o.retry_scheduled(&a, 10), Err(LifecycleError::WrongPhase));
        assert_eq!(
            count(&o.metric_snapshot(), MetricFamily::ScheduledBackoff),
            0
        );
    }
}

#[test]
fn last_raw_attempt_handle_loss_marks_running_evidence_incomplete() {
    let (o, _) = telemetry();
    let e = admitted(&o);
    let a = o.attempt_created(&e).unwrap();
    o.attempt_started(&a).unwrap();
    o.resolved(&e, RuntimeOutcome::Cancelled, None).unwrap();
    let second = a.clone();
    drop(a);
    assert!(
        o.metric_snapshot().complete,
        "another holder can still report termination"
    );
    drop(second);
    let snapshot = o.metric_snapshot();
    assert_eq!(
        count(&snapshot, MetricFamily::Running),
        1,
        "never invent remote termination"
    );
    assert!(
        !snapshot.complete,
        "no holder remains to report this endpoint"
    );
    assert!(o.effect_details(e.id()).unwrap().abandoned);
}

#[test]
fn last_raw_delivery_handle_loss_marks_unobserved_publication_incomplete() {
    let (o, _) = telemetry();
    let e = admitted(&o);
    o.resolved(&e, RuntimeOutcome::Success, None).unwrap();
    let d = o.reserve_publication(&e).unwrap();
    o.publication_committed(&d).unwrap();
    drop(d);
    let snapshot = o.metric_snapshot();
    assert_eq!(count(&snapshot, MetricFamily::CompletionDepth), 1);
    assert_eq!(count(&snapshot, MetricFamily::Deliveries), 0);
    assert!(!snapshot.complete);
    assert!(o.effect_details(e.id()).unwrap().abandoned);
}

#[test]
fn merging_disjoint_producers_preserves_loss_health() {
    let mut left = store();
    let mut right = store();
    right.health_mut().omitted_measurements = 3;
    right.health_mut().invalid_measurements = 2;
    right.mark_incomplete();
    let end = Some(SnapshotTime {
        domain: 73,
        nanos: 10,
    });
    let mut merged = left.snapshot(end);
    merged.merge_disjoint(&right.snapshot(end)).unwrap();
    assert_eq!(merged.health.omitted_measurements, 3);
    assert_eq!(merged.health.invalid_measurements, 2);
    assert!(!merged.complete);
}

#[test]
fn publication_commit_and_consumer_race_counts_boundaries_once() {
    for _ in 0..32 {
        let (o, clock) = telemetry();
        let e = admitted(&o);
        clock.set(10);
        o.resolved(&e, RuntimeOutcome::Success, None).unwrap();
        clock.set(20);
        let d = o.reserve_publication(&e).unwrap();
        clock.set(30);
        let barrier = Arc::new(Barrier::new(3));
        std::thread::scope(|s| {
            let b = barrier.clone();
            let observer = o.clone();
            let delivery = d.clone();
            s.spawn(move || {
                b.wait();
                observer.publication_committed(&delivery).unwrap();
            });
            let b = barrier.clone();
            let observer = o.clone();
            let delivery = d.clone();
            s.spawn(move || {
                b.wait();
                observer.delivery_begun(&delivery).unwrap();
                observer
                    .delivery_observed(&delivery, DeliveryDisposition::Ignored)
                    .unwrap();
            });
            barrier.wait();
        });
        assert_eq!(o.publication_failed(&d), Err(LifecycleError::WrongPhase));
        let snapshot = o.metric_snapshot();
        for family in [
            MetricFamily::CompletionPreparation,
            MetricFamily::CompletionQueueWait,
            MetricFamily::EndToEnd,
            MetricFamily::EndToEndObserved,
            MetricFamily::Deliveries,
        ] {
            assert_eq!(count(&snapshot, family), 1, "{family:?}");
        }
        assert_eq!(count(&snapshot, MetricFamily::CompletionDepth), 0);
        assert_eq!(snapshot.health.arithmetic_errors, 0);
    }
}

#[derive(Default)]
struct HostModel {
    steps: Cell<usize>,
    state_checks: Cell<usize>,
    turn_checks: Cell<usize>,
    codec_fault: Cell<bool>,
}
impl Model for HostModel {
    type State = State;
    type Input = Input;
    type Output = Output;
    fn metadata(&self) -> ModelMetadata {
        RequestModel::fixed().metadata()
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        RequestModel::fixed().initial_state()
    }
    fn step(&self, state: &State, input: &Input) -> Result<Transition<State, Output>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        RequestModel::fixed().step(state, input)
    }
    fn check_state(&self, state: &State) -> Result<Vec<Check>, ModelError> {
        self.state_checks.set(self.state_checks.get() + 1);
        RequestModel::fixed().check_state(state)
    }
    fn check_transition(
        &self,
        state: &State,
        input: &Input,
        turn: &TransitionRef<'_, State, Output>,
    ) -> Result<Vec<Check>, ModelError> {
        self.turn_checks.set(self.turn_checks.get() + 1);
        RequestModel::fixed().check_transition(state, input, turn)
    }
}
impl ModelCodec for HostModel {
    fn encode_state(&self, state: &State) -> Result<Vec<u8>, ModelError> {
        if self.codec_fault.get() {
            return Err(ModelError::new("intentional diagnostic encoder failure"));
        }
        RequestModel::fixed().encode_state(state)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<State, ModelError> {
        RequestModel::fixed().decode_state(bytes)
    }
    fn encode_input(&self, input: &Input) -> Result<Vec<u8>, ModelError> {
        RequestModel::fixed().encode_input(input)
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Input, ModelError> {
        RequestModel::fixed().decode_input(bytes)
    }
    fn encode_output(&self, output: &Output) -> Result<Vec<u8>, ModelError> {
        RequestModel::fixed().encode_output(output)
    }
}
fn gate() -> live::LiveController<Input> {
    live::LiveController::new(
        1,
        0,
        live::ControllerOptions {
            authority: live::ControlAuthority {
                pause_and_step: true,
                inject: false,
            },
            ..Default::default()
        },
    )
    .unwrap()
}

#[test]
fn integrated_actual_host_pause_cancellation_late_delivery_and_replay() {
    let model = HostModel::default();
    let mut state = model.initial_state().unwrap();
    let mut bridge = live::LiveBridge::attach(
        &check_initial(&model, &state).unwrap(),
        0,
        1,
        RunConfig::default(),
        RecorderOptions::default(),
        live::LiveLimits::default(),
    )
    .unwrap();
    let mut gate = gate();
    let (o, clock) = telemetry();
    let mut effect = None;
    let mut attempt = None;
    let mut completion = None;
    let mut dispatched = Vec::new();
    gate.request_pause(1, 0).unwrap();
    gate.safe_point().unwrap().unwrap();
    o.debugger_pause(true);
    for (index, input) in [
        Input::Start,
        Input::Cancel,
        Input::Complete(1),
        Input::Complete(1),
    ]
    .into_iter()
    .enumerate()
    {
        let sequence = index as u64 + 1;
        clock.set(sequence * 100);
        gate.external_arrival(input, 1).unwrap();
        assert!(matches!(
            gate.begin_next(),
            Err(live::LiveError::WrongPhase)
        ));
        let status = gate.status();
        gate.authorize_one(status.epoch, status.revision).unwrap();
        o.debugger_pause(false);
        let (permit, input) = gate.begin_next().unwrap().unwrap();
        if sequence == 3 {
            o.delivery_begun(completion.as_ref().unwrap()).unwrap();
        }
        if sequence == 4 {
            let d = o.reserve_publication(effect.as_ref().unwrap()).unwrap();
            o.publication_committed(&d).unwrap();
            o.delivery_begun(&d).unwrap();
            completion = Some(d);
        }
        let actual = model.step(&state, &input).unwrap();
        if sequence >= 3 {
            let disposition = if sequence == 3 {
                DeliveryDisposition::Accepted
            } else {
                DeliveryDisposition::Rejected
            };
            o.delivery_observed(completion.as_ref().unwrap(), disposition)
                .unwrap();
        }
        let checked = check_turn(
            &model,
            &state,
            &input,
            &actual,
            sequence,
            CheckPolicy::default(),
        )
        .unwrap();
        bridge.observe(sequence, &checked).unwrap();
        gate.delivered(permit, actual.outputs.len()).unwrap();
        state = actual.state;
        assert_eq!(model.steps.get(), sequence as usize);
        assert_eq!(model.turn_checks.get(), sequence as usize);
        assert_eq!(model.state_checks.get(), sequence as usize + 1);
        for (output_index, output) in actual.outputs.iter().enumerate() {
            let permit = gate.take_dispatch(output_index).unwrap();
            let origin = permit.origin();
            if matches!(output, Output::Request(_)) {
                let e = requested(&o, sequence);
                assert_eq!(
                    o.effect_details(e.id()).unwrap().origin.transition_sequence,
                    origin.transition
                );
                o.admission(&e, Admission::Accepted).unwrap();
                let a = o.attempt_created(&e).unwrap();
                o.ready_queued(&a).unwrap();
                o.attempt_started(&a).unwrap();
                effect = Some(e);
                attempt = Some(a);
            } else {
                o.settled(effect.as_ref().unwrap()).unwrap();
            }
            dispatched.push(origin);
            gate.dispatched(permit).unwrap();
            bridge.effect_dispatched(origin).unwrap();
            assert!(matches!(
                gate.take_dispatch(output_index),
                Err(live::LiveError::Duplicate)
            ));
        }
        if sequence == 2 {
            let e = effect.as_ref().unwrap();
            o.cancellation_requested(e).unwrap();
            o.cancellation_acknowledged(e).unwrap();
            o.resolved(e, RuntimeOutcome::Cancelled, None).unwrap();
            assert_eq!(count(&o.metric_snapshot(), MetricFamily::Running), 1);
            o.attempt_finished(attempt.as_ref().unwrap(), RuntimeOutcome::Success)
                .unwrap();
            let d = o.reserve_publication(e).unwrap();
            o.publication_committed(&d).unwrap();
            completion = Some(d);
        }
        let ack = gate.safe_point().unwrap().unwrap();
        assert_eq!(ack.revision, sequence);
        o.debugger_pause(true);
        assert!(matches!(
            gate.begin_next(),
            Err(live::LiveError::WrongPhase)
        ));
        if sequence == 2 {
            gate.disconnect();
            let epoch = gate.reconnect().unwrap();
            assert_eq!(
                gate.authorize_one(1, sequence),
                Err(live::LiveError::WrongEpoch)
            );
            assert_eq!(epoch, 2);
            assert_eq!(gate.status().origin_epoch, 1);
        }
    }
    assert_eq!(dispatched.len(), 2);
    assert!(state.pending.is_empty());
    assert!(!state.ready);
    let metrics = o.metric_snapshot();
    assert_eq!(count(&metrics, MetricFamily::Attempts), 1);
    assert_eq!(count(&metrics, MetricFamily::Deliveries), 2);
    assert_eq!(count(&metrics, MetricFamily::DuplicateDeliveries), 1);
    assert_eq!(count(&metrics, MetricFamily::EndToEnd), 1);
    assert_eq!(count(&metrics.production(), MetricFamily::EndToEnd), 0);
    assert_eq!(count(&metrics, MetricFamily::Running), 0);
    assert_eq!(count(&metrics, MetricFamily::Unresolved), 0);
    let trace = bridge.export();
    let replayed = replay(&model, &trace, ReplayOptions::default()).unwrap();
    assert_eq!(replayed.steps_verified, 4);
    assert_eq!(model.steps.get(), 8);
    assert_eq!(o.metric_snapshot().series, metrics.series);
    assert_eq!(
        dispatched.len(),
        2,
        "exact replay never dispatches host effects"
    );
}

#[test]
fn diagnostic_encoder_failure_preserves_actual_state_requested_outputs_and_dispatch() {
    let model = HostModel::default();
    let mut state = model.initial_state().unwrap();
    let mut bridge = live::LiveBridge::attach(
        &check_initial(&model, &state).unwrap(),
        0,
        1,
        RunConfig::default(),
        RecorderOptions::default(),
        live::LiveLimits::default(),
    )
    .unwrap();
    let mut gate = gate();
    gate.external_arrival(Input::Start, 1).unwrap();
    let (permit, input) = gate.begin_next().unwrap().unwrap();
    let actual = model.step(&state, &input).unwrap();
    let checked = check_turn(&model, &state, &input, &actual, 1, CheckPolicy::default()).unwrap();
    model.codec_fault.set(true);
    assert_eq!(
        bridge.observe(1, &checked),
        Err(live::LiveError::RecordingFailed)
    );
    state = actual.state;
    gate.delivered(permit, actual.outputs.len()).unwrap();
    assert!(state.active);
    assert_eq!(state.pending, vec![1]);
    let dispatch = gate.take_dispatch(0).unwrap();
    let origin = dispatch.origin();
    let mut dispatches = 0;
    dispatches += 1;
    gate.dispatched(dispatch).unwrap();
    bridge.effect_dispatched(origin).unwrap();
    assert_eq!(dispatches, 1);
    assert_eq!(model.steps.get(), 1);
    assert!(!bridge.health().capture_complete);
    assert!(bridge.health().recorder_frozen);
}

#[test]
fn rejected_publication_and_finished_attempt_drops_do_not_invent_abandonment() {
    let (o, _) = telemetry();
    let e = admitted(&o);
    let a = o.attempt_created(&e).unwrap();
    o.attempt_started(&a).unwrap();
    o.attempt_finished(&a, RuntimeOutcome::Success).unwrap();
    o.resolved(&e, RuntimeOutcome::Success, Some(&a)).unwrap();
    let d = o.reserve_publication(&e).unwrap();
    o.publication_failed(&d).unwrap();
    drop(d);
    drop(a);
    let snapshot = o.metric_snapshot();
    assert!(snapshot.complete);
    assert_eq!(count(&snapshot, MetricFamily::Running), 0);
    assert_eq!(count(&snapshot, MetricFamily::CompletionDepth), 0);
    assert_eq!(o.telemetry_health().abandoned, 0);
}

#[test]
fn dropping_request_before_admission_keeps_the_missing_decision_visible() {
    let (o, _) = telemetry();
    let e = requested(&o, 1);
    let id = e.id();
    drop(e);
    assert!(!o.metric_snapshot().complete);
    assert!(o.effect_details(id).unwrap().abandoned);
    assert_eq!(o.telemetry_health().active_effect_handles, 0);
}

#[test]
fn concurrently_dropped_raw_clones_report_one_missing_endpoint() {
    for delivery in [false, true] {
        let (o, _) = telemetry();
        let e = admitted(&o);
        if delivery {
            o.resolved(&e, RuntimeOutcome::Success, None).unwrap();
            let d = o.reserve_publication(&e).unwrap();
            let d2 = d.clone();
            std::thread::scope(|s| {
                s.spawn(move || drop(d));
                s.spawn(move || drop(d2));
            });
        } else {
            let a = o.attempt_created(&e).unwrap();
            o.attempt_started(&a).unwrap();
            o.resolved(&e, RuntimeOutcome::Cancelled, None).unwrap();
            let a2 = a.clone();
            std::thread::scope(|s| {
                s.spawn(move || drop(a));
                s.spawn(move || drop(a2));
            });
        }
        assert_eq!(o.telemetry_health().abandoned, 1);
        assert!(!o.metric_snapshot().complete);
    }
}

#[test]
fn health_overflow_during_merge_is_atomic() {
    let mut left = store();
    let mut right = store();
    left.health_mut().omitted_measurements = u64::MAX;
    right.health_mut().omitted_measurements = 1;
    right.increment(MetricFamily::Requests, Labels::default(), 5);
    let end = Some(SnapshotTime {
        domain: 73,
        nanos: 10,
    });
    let mut merged = left.snapshot(end);
    let before = merged.clone();
    assert_eq!(
        merged.merge_disjoint(&right.snapshot(end)),
        Err(MetricError::Overflow)
    );
    assert_eq!(merged, before);
}

#[test]
fn safe_point_waits_for_every_reserved_output_across_disconnect() {
    let mut gate = gate();
    gate.external_arrival(Input::Start, 1).unwrap();
    let (delivery, _) = gate.begin_next().unwrap().unwrap();
    gate.delivered(delivery, 2).unwrap();
    let first = gate.take_dispatch(0).unwrap();
    let second = gate.take_dispatch(1).unwrap();
    gate.request_pause(1, 1).unwrap();
    gate.disconnect();
    assert_eq!(gate.safe_point(), Err(live::LiveError::WrongPhase));
    gate.dispatched(second).unwrap();
    assert_eq!(gate.safe_point(), Err(live::LiveError::WrongPhase));
    assert_eq!(gate.reconnect(), Err(live::LiveError::WrongPhase));
    gate.dispatched(first).unwrap();
    let ack = gate.safe_point().unwrap().unwrap();
    assert_eq!(ack.effect_phase, live::EffectPhase::Dispatched);
    assert_eq!(ack.revision, 1);
    gate.external_arrival(Input::Cancel, 1).unwrap();
    assert!(matches!(
        gate.begin_next(),
        Err(live::LiveError::WrongPhase)
    ));
    assert_eq!(gate.reconnect().unwrap(), 2);
    assert_eq!(gate.authorize_one(1, 1), Err(live::LiveError::WrongEpoch));
    gate.authorize_one(2, 1).unwrap();
    assert!(matches!(
        gate.take_dispatch(0),
        Err(live::LiveError::Duplicate)
    ));
    let (delivery, input) = gate.begin_next().unwrap().unwrap();
    assert_eq!(input, Input::Cancel);
    gate.delivered(delivery, 0).unwrap();
    gate.safe_point().unwrap().unwrap();
    assert!(matches!(
        gate.begin_next(),
        Err(live::LiveError::WrongPhase)
    ));
}

#[test]
fn pause_ack_with_staged_outputs_requires_new_authority_before_dispatch() {
    let mut gate = gate();
    gate.external_arrival(Input::Start, 1).unwrap();
    let (delivery, _) = gate.begin_next().unwrap().unwrap();
    gate.delivered(delivery, 2).unwrap();
    gate.request_pause(1, 1).unwrap();
    assert_eq!(
        gate.safe_point().unwrap().unwrap().effect_phase,
        live::EffectPhase::Staged
    );
    assert!(matches!(
        gate.take_dispatch(0),
        Err(live::LiveError::WrongPhase)
    ));
    gate.external_arrival(Input::Cancel, 1).unwrap();
    gate.authorize_one(1, 1).unwrap();
    assert!(matches!(
        gate.begin_next(),
        Err(live::LiveError::WrongPhase)
    ));
    for index in 0..2 {
        let output = gate.take_dispatch(index).unwrap();
        gate.dispatched(output).unwrap();
    }
    let (delivery, _) = gate.begin_next().unwrap().unwrap();
    gate.delivered(delivery, 0).unwrap();
    assert_eq!(gate.safe_point().unwrap().unwrap().revision, 2);
}

#[test]
fn invalid_snapshot_clock_does_not_erase_the_previous_valid_watermark() {
    let mut metrics = store();
    let at = |nanos| Some(SnapshotTime { domain: 73, nanos });
    assert_eq!(metrics.snapshot(at(10)).end, at(10));
    assert_eq!(metrics.snapshot(at(9)).end, None);
    assert_eq!(metrics.snapshot(at(8)).end, None);
    assert_eq!(metrics.snapshot(None).end, None);
    assert_eq!(metrics.snapshot(at(7)).end, None);
    assert_eq!(metrics.snapshot(at(11)).end, at(11));
}

#[test]
fn snapshot_timestamp_never_predates_a_concurrently_included_measurement() {
    use std::sync::{Mutex, atomic::AtomicBool, mpsc};
    struct GatedClock {
        nanos: AtomicU64,
        gate_next: AtomicBool,
        entered: mpsc::SyncSender<()>,
        release: Mutex<mpsc::Receiver<()>>,
    }
    impl Clock for GatedClock {
        fn now(&self) -> Result<LocalInstant, MeasurementError> {
            let nanos = self.nanos.load(Ordering::SeqCst);
            if self.gate_next.swap(false, Ordering::SeqCst) {
                self.entered.send(()).unwrap();
                self.release.lock().unwrap().recv().unwrap();
            }
            Ok(LocalInstant {
                domain: ClockDomain(73),
                nanos,
            })
        }
    }
    let (entered, wait_entered) = mpsc::sync_channel(1);
    let (release, wait_release) = mpsc::sync_channel(1);
    let clock = Arc::new(GatedClock {
        nanos: AtomicU64::new(0),
        gate_next: AtomicBool::new(false),
        entered,
        release: Mutex::new(wait_release),
    });
    let o = EffectObserver::new(EffectOptions::default(), clock.clone()).unwrap();
    let e = admitted(&o);
    clock.nanos.store(10, Ordering::SeqCst);
    clock.gate_next.store(true, Ordering::SeqCst);
    let snapshotter = o.clone();
    let pending = std::thread::spawn(move || snapshotter.metric_snapshot());
    wait_entered.recv().unwrap();
    clock.nanos.store(20, Ordering::SeqCst);
    o.resolved(&e, RuntimeOutcome::Success, None).unwrap();
    release.send(()).unwrap();
    let snapshot = pending.join().unwrap();
    assert_eq!(count(&snapshot, MetricFamily::Resolution), 1);
    assert!(
        snapshot.end.is_none_or(|end| end.nanos >= 20),
        "included a t=20 measurement under a t=10 endpoint: {:?}",
        snapshot.end
    );
}

#[test]
fn foreign_future_adapter_cannot_write_another_observers_accounting() {
    let (owner, _) = telemetry();
    let (foreign, _) = telemetry();
    let e = admitted(&owner);
    let a = owner.attempt_created(&e).unwrap();
    let future = foreign.instrument_future(a.clone(), std::future::pending::<()>(), |_| {
        RuntimeOutcome::Success
    });
    drop(future);
    assert_eq!(foreign.telemetry_health().abandoned, 0);
    assert_eq!(
        count(&foreign.metric_snapshot(), MetricFamily::Abandoned),
        0
    );
    assert!(foreign.metric_snapshot().complete);
    assert!(
        owner.metric_snapshot().complete,
        "the valid token remains owned and usable"
    );
    owner.attempt_started(&a).unwrap();
    owner.attempt_finished(&a, RuntimeOutcome::Success).unwrap();
    owner
        .resolved(&e, RuntimeOutcome::Success, Some(&a))
        .unwrap();
}

#[test]
fn constructor_clock_failure_is_visible_without_inventing_an_epoch_start() {
    struct MissingClock;
    impl Clock for MissingClock {
        fn now(&self) -> Result<LocalInstant, MeasurementError> {
            Err(MeasurementError::ClockUnavailable)
        }
    }
    let o = EffectObserver::new(EffectOptions::default(), Arc::new(MissingClock)).unwrap();
    assert_eq!(o.telemetry_health().clock_failures, 1);
    assert_eq!(o.metric_snapshot().start, None);
    assert_eq!(o.telemetry_health().clock_failures, 2);
}

#[test]
fn current_metrics_do_not_reuse_a_timestamp_from_before_new_observations() {
    let mut metrics = store();
    let first = metrics.snapshot(Some(SnapshotTime {
        domain: 73,
        nanos: 10,
    }));
    assert!(first.end.is_some());
    metrics.observe(MetricFamily::AttemptElapsed, Labels::default(), 20);
    assert_eq!(metrics.current().end, None);
    assert_eq!(
        metrics
            .snapshot(Some(SnapshotTime {
                domain: 73,
                nanos: 9
            }))
            .end,
        None
    );
    assert_eq!(
        metrics
            .snapshot(Some(SnapshotTime {
                domain: 73,
                nanos: 21
            }))
            .end
            .unwrap()
            .nanos,
        21
    );
}

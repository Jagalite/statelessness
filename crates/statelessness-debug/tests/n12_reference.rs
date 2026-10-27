//! Independent finite contract assertions. Expectations were recorded before execution.
use stateless::demo::{Input, RequestModel};
use stateless::evidence::{Artifact, EvidenceLimits, Provenance, TraceV2};
use stateless::execution::{ReplayOptions, ReplayOutcome, record};
use stateless::explore::{CheckPhase, ShrinkLimits};
use stateless::trace::RunConfig;
use statelessness_debug::evidence::EvidenceViewer;
use statelessness_debug::workbench::TraceViewer;

fn fixture() -> TraceV2 {
    let model = RequestModel::buggy();
    let trace = record(
        &model,
        [Input::Start, Input::Cancel, Input::Complete(1)],
        RunConfig::default(),
        3,
    )
    .unwrap();
    TraceV2::verify_upgrade(
        &model,
        trace,
        Provenance::default(),
        0,
        ReplayOptions::default(),
        &EvidenceLimits::default(),
    )
    .unwrap()
}

#[test]
fn n12_independent_offline_goldens() {
    let value = fixture();
    assert_eq!(value.trace.steps.len(), 3);
    let viewer =
        EvidenceViewer::new(Artifact::V2(value.clone()), &EvidenceLimits::default()).unwrap();
    let zero = viewer.boundary(0).unwrap();
    assert_eq!(zero.before, value.trace.initial_state);
    assert_eq!(zero.after, value.trace.initial_state);
    assert!(zero.input.is_none() && zero.outputs.is_empty() && zero.causal.is_none());
    assert_eq!(
        viewer.boundary(3).unwrap().before,
        value.trace.steps[1].post_state
    );
    assert_eq!(viewer.failure(3, 2).unwrap().phase, CheckPhase::Transition);
    assert!(viewer.failure(1, 0).is_err());
    assert!(viewer.failure(4, 0).is_err());
    let legacy = EvidenceViewer::new(
        Artifact::V1(value.trace.clone()),
        &EvidenceLimits::default(),
    )
    .unwrap();
    assert!(
        legacy
            .failure(3, 2)
            .unwrap_err()
            .to_string()
            .contains("phase unknown")
    );
    let mut continued = value.trace.clone();
    continued.steps.push(continued.steps[0].clone());
    let mut bytes = Vec::new();
    continued.write_to(&mut bytes).unwrap();
    let artifact = Artifact::read(bytes.as_slice(), &EvidenceLimits::default()).unwrap();
    assert!(EvidenceViewer::new(artifact, &EvidenceLimits::default()).is_err());
    let report = viewer
        .minimize(
            &RequestModel::buggy(),
            3,
            2,
            Default::default(),
            ShrinkLimits {
                max_replayed_transitions: Some(9),
                ..Default::default()
            },
        )
        .unwrap();
    assert!((6..=9).contains(&report.replayed_transitions));
    assert!(
        viewer
            .minimize(
                &RequestModel::buggy(),
                3,
                2,
                Default::default(),
                ShrinkLimits {
                    max_replayed_transitions: Some(2),
                    ..Default::default()
                }
            )
            .is_err()
    );
    let cancelled = std::sync::atomic::AtomicBool::new(true);
    let error = viewer
        .minimize(
            &RequestModel::buggy(),
            3,
            2,
            Default::default(),
            ShrinkLimits {
                control: stateless::explore::RunLimits {
                    cancellation: Some(&cancelled),
                    ..Default::default()
                },
                ..Default::default()
            },
        )
        .unwrap_err();
    assert!(error.to_string().contains("cancelled"));
    let mut spoofed = value.clone();
    assert_ne!(spoofed.steps[0].state_check_count, 0);
    spoofed.steps[0].state_check_count = 0;
    let spoofed_viewer =
        EvidenceViewer::new(Artifact::V2(spoofed), &EvidenceLimits::default()).unwrap();
    assert!(
        spoofed_viewer
            .minimize(
                &RequestModel::buggy(),
                3,
                2,
                Default::default(),
                ShrinkLimits::default()
            )
            .unwrap_err()
            .to_string()
            .contains("phase evidence")
    );
    let view = TraceViewer::new(value.trace).unwrap();
    assert!(matches!(
        view.verify(&RequestModel::fixed(), ReplayOptions::default())
            .unwrap()
            .outcome,
        ReplayOutcome::Incompatible { .. }
    ));
    let verified = view
        .verify(
            &RequestModel::fixed(),
            ReplayOptions {
                allow_build_mismatch: true,
            },
        )
        .unwrap();
    assert_eq!(
        verified.outcome,
        ReplayOutcome::Diverged {
            step: Some(3),
            field: "outputs"
        }
    );
    assert_eq!(verified.steps_verified, 2);
    assert!(!verified.failure_reproduced);
}

#[test]
fn n12_independent_overflow_quantile_goldens() {
    use statelessness_debug::metrics::{Histogram, HistogramSchema};
    let schema = HistogramSchema {
        id: 1,
        finite_bounds_ns: vec![10, 20],
    };
    let mut histogram = Histogram::default();
    histogram.buckets[..3].copy_from_slice(&[2, 1, 1]);
    histogram.count = 4;
    histogram.sum_ns = 51;
    assert_eq!(
        histogram.quantile(&schema, 50, 100).unwrap().upper_bound_ns,
        Some(10)
    );
    let overflow = histogram.quantile(&schema, 95, 100).unwrap();
    assert_eq!(overflow.upper_bound_ns, None);
    assert_eq!(overflow.observations, 4);
    assert!(overflow.low_sample_warning);
    assert!(Histogram::default().quantile(&schema, 95, 100).is_none());
}

#[test]
fn n12_independent_session_breakpoint_and_fork_goldens() {
    use stateless::monitor::RecorderOptions;
    use statelessness_debug::session::StopReason;
    use statelessness_debug::{DebugSession, InputPolicy, SessionLimits};
    let mut session = DebugSession::recording(
        "manual-reference",
        RequestModel::buggy(),
        InputPolicy::enumerated(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions::default(),
    )
    .unwrap();
    let breakpoint = session
        .add_pre_breakpoint(|_, input| input == &Input::Start)
        .unwrap();
    let before = session.step(0, Input::Start).unwrap();
    assert!(!before.delivered);
    assert_eq!(before.stop, Some(StopReason::PreBreakpoint(breakpoint)));
    assert_eq!(session.sequence(), 0);
    assert_eq!(session.revision(), 0);
    assert!(session.step(0, Input::Start).unwrap().delivered);
    assert_eq!(session.sequence(), 1);
    assert_eq!(session.revision(), 1);
    assert!(!session.step(1, Input::Start).unwrap().delivered);
    let trace = fixture().trace;
    let view = TraceViewer::new(trace.clone()).unwrap();
    let mut fork = view
        .fork_verified(
            "manual-fork",
            RequestModel::fixed(),
            2,
            ReplayOptions {
                allow_build_mismatch: true,
            },
            InputPolicy::enumerated(),
            SessionLimits::default(),
            RecorderOptions::default(),
        )
        .unwrap();
    assert!(fork.provenance.parent_prefix_verified);
    fork.session.step(0, Input::Complete(1)).unwrap();
    assert_eq!(view.trace(), &trace);
    assert!(
        view.fork_verified(
            "past-failure",
            RequestModel::buggy(),
            3,
            ReplayOptions::default(),
            InputPolicy::enumerated(),
            SessionLimits::default(),
            RecorderOptions::default()
        )
        .is_err()
    );
}

#[test]
fn n12_independent_explicit_nanosecond_lifecycle() {
    use statelessness_debug::effects::*;
    use statelessness_debug::metrics::*;
    use std::sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    };
    #[derive(Default)]
    struct ClockFixture(AtomicU64);
    impl Clock for ClockFixture {
        fn now(&self) -> Result<LocalInstant, MeasurementError> {
            Ok(LocalInstant {
                domain: ClockDomain(9),
                nanos: self.0.load(Ordering::SeqCst),
            })
        }
    }
    let clock = Arc::new(ClockFixture::default());
    let observer = EffectObserver::new(EffectOptions::default(), clock.clone()).unwrap();
    let effect = observer
        .requested(
            RequestOrigin {
                run: 1,
                epoch: 1,
                machine: 1,
                transition_sequence: 1,
                output_index: 0,
            },
            Labels::default(),
        )
        .unwrap();
    observer.admission(&effect, Admission::Accepted).unwrap();
    let attempt = observer.attempt_created(&effect).unwrap();
    observer.ready_queued(&attempt).unwrap();
    clock.0.store(12, Ordering::SeqCst);
    observer.attempt_started(&attempt).unwrap();
    clock.0.store(96, Ordering::SeqCst);
    observer
        .attempt_finished(&attempt, RuntimeOutcome::Success)
        .unwrap();
    observer
        .resolved(&effect, RuntimeOutcome::Success, Some(&attempt))
        .unwrap();
    clock.0.store(97, Ordering::SeqCst);
    let delivery = observer.reserve_publication(&effect).unwrap();
    observer.publication_committed(&delivery).unwrap();
    clock.0.store(100, Ordering::SeqCst);
    observer.delivery_begun(&delivery).unwrap();
    clock.0.store(102, Ordering::SeqCst);
    observer
        .delivery_observed(&delivery, DeliveryDisposition::Ignored)
        .unwrap();
    let snapshot = observer.metric_snapshot();
    for (family, expected) in [
        (MetricFamily::QueueWait, 12),
        (MetricFamily::AttemptElapsed, 84),
        (MetricFamily::CompletionPreparation, 1),
        (MetricFamily::CompletionQueueWait, 3),
        (MetricFamily::CompletionProcessing, 2),
        (MetricFamily::EndToEnd, 100),
        (MetricFamily::EndToEndObserved, 102),
    ] {
        let values: Vec<_> = snapshot
            .series
            .iter()
            .filter(|(key, _)| key.family == family)
            .collect();
        assert_eq!(values.len(), 1);
        let MetricValue::Histogram(histogram) = values[0].1 else {
            panic!("duration must be histogram")
        };
        assert_eq!(histogram.count, 1);
        assert_eq!(histogram.sum_ns, expected);
    }
}

#[test]
fn n12_independent_structural_unknowns_and_exact_integer() {
    use statelessness_debug::inspect::*;
    let query = InspectQuery::default();
    let integer = inspect(&u128::MAX, &query).unwrap();
    assert_eq!(
        integer.node.kind,
        NodeKind::Scalar(Scalar::Integer {
            kind: IntegerType::U128,
            decimal: "340282366920938463463374607431768211455".into()
        })
    );
    assert_eq!(
        compare_nodes(Some(&integer.node), Some(&integer.node)),
        DiffKind::Unchanged
    );
    let mut bounded = InspectQuery::default();
    bounded.limits.max_value_bytes = 0;
    let incomplete = inspect(&"secret", &bounded).unwrap();
    assert!(!incomplete.node.is_complete());
    assert_eq!(
        compare_nodes(Some(&incomplete.node), Some(&incomplete.node)),
        DiffKind::Unknown
    );
    struct Hidden;
    impl Inspect for Hidden {
        fn inspect(
            &self,
            p: &[PathSegment],
            cx: &mut InspectContext,
        ) -> Result<InspectNode, InspectError> {
            cx.object(p, "Hidden", &[FieldView::redacted("secret", "Secret")])
        }
    }
    let redacted = inspect(
        &Hidden,
        &InspectQuery {
            path: vec![PathSegment::Field("secret".into())],
            ..Default::default()
        },
    )
    .unwrap();
    assert_eq!(redacted.node.kind, NodeKind::Redacted);
    assert_eq!(
        compare_nodes(Some(&redacted.node), Some(&redacted.node)),
        DiffKind::Unknown
    );
}

#[test]
fn n12_independent_protocol_exact_wire_and_retirement() {
    use stateless::ModelCodec;
    use statelessness_debug::protocol::*;
    use statelessness_debug::{DebugSession, InputPolicy, SessionLimits};
    let limits = ProtocolLimits {
        max_cache_entries: 1,
        ..Default::default()
    };
    let full = Request {
        version: 1,
        session: "wire".into(),
        epoch: u64::MAX,
        request_id: 9_007_199_254_740_993,
        expected_revision: u64::MAX,
        expected_configuration: u64::MAX,
        command: Command::Status,
    };
    let expected=b"DDBG\tu32:1\trequest\tstring:wire\tu64:18446744073709551615\tu64:9007199254740993\tu64:18446744073709551615\tu64:18446744073709551615\tstatus";
    assert_eq!(encode_request(&full, &limits).unwrap(), expected);
    assert_eq!(decode_request(expected, &limits).unwrap(), full);
    let make = || {
        DebugSession::new(
            "wire",
            RequestModel::fixed(),
            InputPolicy::enumerated(),
            SessionLimits::default(),
        )
        .unwrap()
    };
    let mut status_endpoint =
        ProtocolSession::new(make(), 41, ProtocolAuthority::default(), limits.clone()).unwrap();
    let mut status = status_endpoint.handle(Request {
        version: 1,
        session: "wire".into(),
        epoch: status_endpoint.epoch(),
        request_id: 1,
        expected_revision: 0,
        expected_configuration: 0,
        command: Command::Status,
    });
    for pair in [
        ("mode", "enum:simulation"),
        ("phase", "enum:Ready"),
        ("sequence", "u64:0"),
        ("environment", "string:enumerated-domain"),
        ("capability.input_delivery", "bool:false"),
        ("capability.live_control", "bool:false"),
        ("capability.live_restore", "bool:false"),
        ("capability.effects", "bool:false"),
    ] {
        assert!(status.fields.contains(&(pair.0.into(), pair.1.into())));
    }
    status.epoch = 41;
    println!(
        "N12_PROTOCOL_STATUS\t{}",
        String::from_utf8(encode_response(&status, &limits).unwrap()).unwrap()
    );
    let mut endpoint = ProtocolSession::new(
        make(),
        41,
        ProtocolAuthority {
            deliver_inputs: true,
            ..Default::default()
        },
        limits.clone(),
    )
    .unwrap();
    let endpoint_epoch = endpoint.epoch();
    let request = |id, revision, command| Request {
        version: 1,
        session: "wire".into(),
        epoch: endpoint_epoch,
        request_id: id,
        expected_revision: revision,
        expected_configuration: 0,
        command,
    };
    let snapshot = endpoint.handle(request(1, 0, Command::Snapshot));
    assert_eq!(snapshot.epoch, endpoint_epoch);
    let mut normalized = snapshot.clone();
    normalized.epoch = 41;
    println!(
        "N12_PROTOCOL_SNAPSHOT\t{}",
        String::from_utf8(encode_response(&normalized, &limits).unwrap()).unwrap()
    );
    assert_eq!(encode_response(&normalized,&limits).unwrap(),b"DDBG\tu32:1\tresponse\tstring:wire\tu64:41\tu64:1\tu64:0\tu64:0\tok\tnone\tu64:3\thandle\tu64:1\tsnapshot.revision\tu64:0\tsnapshot.sequence\tu64:0");
    let step = request(
        2,
        0,
        Command::Step {
            encoded_input: RequestModel::fixed().encode_input(&Input::Start).unwrap(),
        },
    );
    let first = endpoint.handle(step.clone());
    let mut step_wire = first.clone();
    step_wire.epoch = 41;
    println!(
        "N12_PROTOCOL_STEP\t{}",
        String::from_utf8(encode_response(&step_wire, &limits).unwrap()).unwrap()
    );
    assert!(first.result.is_ok());
    assert!(
        first
            .fields
            .contains(&("delivered".into(), "bool:true".into()))
    );
    assert_eq!(endpoint.handle(step.clone()), first);
    assert_eq!(endpoint.session().sequence(), 1);
    assert_eq!(
        endpoint.handle(request(2, 1, Command::Status)).result,
        Err(ProtocolError::DuplicateConflict)
    );
    assert!(
        endpoint
            .handle(request(3, 1, Command::Status))
            .result
            .is_ok()
    );
    assert_eq!(
        endpoint.handle(step).result,
        Err(ProtocolError::RetiredRequest)
    );
    let cancel = endpoint.handle(request(4, 1, Command::Cancel));
    assert!(cancel.result.is_ok());
    let mut cancel_wire = cancel.clone();
    cancel_wire.epoch = 41;
    println!(
        "N12_PROTOCOL_CANCEL\t{}",
        String::from_utf8(encode_response(&cancel_wire, &limits).unwrap()).unwrap()
    );
    assert_eq!(endpoint.session().sequence(), 1);
    assert_eq!(endpoint.session().revision(), 1);
    let mut readonly =
        ProtocolSession::new(make(), 41, ProtocolAuthority::default(), limits.clone()).unwrap();
    let mut denied = request(
        1,
        0,
        Command::Step {
            encoded_input: RequestModel::fixed().encode_input(&Input::Start).unwrap(),
        },
    );
    denied.epoch = readonly.epoch();
    assert_eq!(
        readonly.handle(denied).result,
        Err(ProtocolError::Unauthorized)
    );
    assert_eq!(readonly.session().sequence(), 0);
    let mut frame = Vec::new();
    write_frame(&mut frame, b"abc", 3).unwrap();
    assert_eq!(frame, b"\0\0\0\x03abc");
    assert!(read_frame(&mut &frame[..5], 3).is_err());
    assert!(read_frame(&mut &b"\0\0\0\0"[..], 3).is_err());
}

#[test]
fn n12_independent_watch_disable_and_baseline_goldens() {
    use statelessness_debug::inspect::SnapshotId;
    use statelessness_debug::watches::*;
    let mut registry = WatchRegistry::new(WatchLimits::default(), WatchAuthorization::allow_all());
    let ack = registry
        .add_validated(&7u64, WatchConfig::changed(vec![], 1))
        .unwrap();
    let context = |sequence| {
        ObservationContext::new(
            SnapshotId {
                session: 1,
                revision: sequence,
                sequence,
            },
            OriginKind::Simulation,
        )
    };
    registry.observe(&7u64, context(0));
    assert_eq!(
        registry.current(ack.watch_id).unwrap().kind,
        WatchEventKind::Baseline(BaselineReason::Initial)
    );
    registry.observe(&7u64, context(1));
    assert_eq!(
        registry.current(ack.watch_id).unwrap().kind,
        WatchEventKind::Unchanged
    );
    registry.observe(&8u64, context(2));
    assert_eq!(
        registry.current(ack.watch_id).unwrap().kind,
        WatchEventKind::Changed
    );
    let disabled = registry.disable(ack.watch_id).unwrap();
    assert_eq!((disabled.generation, disabled.effective_sequence), (2, 3));
    registry.observe(&9u64, context(3));
    assert!(registry.current(ack.watch_id).is_none());
    let enabled = registry.enable(ack.watch_id).unwrap();
    assert_eq!(enabled.generation, 3);
    registry.observe(&9u64, context(4));
    assert_eq!(
        registry.current(ack.watch_id).unwrap().kind,
        WatchEventKind::Baseline(BaselineReason::Reenabled)
    );
}

#[test]
fn n12_independent_probe_revision_callback_and_loss_goldens() {
    use statelessness_debug::diagnostic::*;
    use std::cell::Cell;
    let mut hub = DiagnosticHub::new(DiagnosticLimits::default());
    hub.add_sink(1, SinkPermissions::local_all(), SinkLimits::default())
        .unwrap();
    hub.add_sink(
        2,
        SinkPermissions::local_all(),
        SinkLimits {
            records: 1,
            ..Default::default()
        },
    )
    .unwrap();
    hub.register_producer(1, 7).unwrap();
    let calls = Cell::new(0u64);
    {
        let mut turn = hub
            .begin_turn(1, 1, 0, DiagnosticOrigin::Simulation)
            .unwrap();
        turn.probe("manual", || {
            calls.set(calls.get() + 1);
            42u64
        });
        turn.finish(true);
    }
    assert_eq!(calls.get(), 0);
    let subscription = |sink| Subscription {
        sink,
        site: "manual".into(),
        kind: SiteKind::Probe,
        path: vec![],
        trigger: Trigger::Every,
        sample_every: 1,
        minimum_severity: Severity::Debug,
    };
    let ack = hub
        .configure(0, vec![subscription(1), subscription(2)])
        .unwrap();
    assert_eq!(ack.capture_revision, 1);
    assert_eq!(ack.pending_producers, vec![1]);
    assert_eq!(
        hub.configure(0, vec![]),
        Err(DiagnosticError::StaleRevision)
    );
    hub.acknowledge(1, 1, 1).unwrap();
    for sequence in 1..=3 {
        let mut turn = hub
            .begin_turn(1, 1, sequence, DiagnosticOrigin::Simulation)
            .unwrap();
        turn.probe("manual", || {
            calls.set(calls.get() + 1);
            sequence
        });
        turn.finish(true);
    }
    assert_eq!(calls.get(), 3); // Once per capture, independently of two destinations.
    assert_eq!(hub.queued(1).unwrap().0, 3);
    assert_eq!(hub.queued(2).unwrap().0, 1);
    assert_eq!(hub.health(1).unwrap().dropped, 0);
    assert_eq!(hub.health(2).unwrap().dropped, 2);
    assert_eq!(hub.pop(1).unwrap().capture_revision, 1);
}

#[test]
fn n12_independent_live_safe_point_dispatch_goldens() {
    use statelessness_debug::live::*;
    let mut control = LiveController::<u8>::new(
        1,
        0,
        ControllerOptions {
            authority: ControlAuthority {
                pause_and_step: true,
                inject: false,
            },
            ..Default::default()
        },
    )
    .unwrap();
    control.external_arrival(7, 1).unwrap();
    let (permit, input) = control.begin_next().unwrap().unwrap();
    assert_eq!(input, 7);
    control.delivered(permit, 1).unwrap();
    assert_eq!(control.status().revision, 1);
    assert_eq!(control.status().effect_phase, EffectPhase::Staged);
    let dispatch = control.take_dispatch(0).unwrap();
    assert_eq!(
        (dispatch.origin().transition, dispatch.origin().output_index),
        (1, 0)
    );
    control.request_pause(1, 1).unwrap();
    assert_eq!(control.safe_point(), Err(LiveError::WrongPhase));
    control.dispatched(dispatch).unwrap();
    let pause = control.safe_point().unwrap().unwrap();
    assert_eq!((pause.generation, pause.revision), (1, 1));
    assert_eq!(pause.effect_phase, EffectPhase::Dispatched);
    assert!(matches!(control.begin_next(), Err(LiveError::WrongPhase)));
}

#[test]
fn n12_independent_dashboard_unknowns_and_causal_join_goldens() {
    use statelessness_debug::dashboard::*;
    use statelessness_debug::effects::*;
    use statelessness_debug::metrics::*;
    let mut store = MetricsStore::new(
        1,
        None,
        LabelCatalog::default(),
        HistogramSchema::default(),
        MetricLimits::default(),
        GaugeScope::Unknown,
    )
    .unwrap();
    let dashboard = Dashboard::from_snapshot(
        &store.snapshot(None),
        &TelemetryHealth {
            clock_failures: 1,
            ..Default::default()
        },
        &[Labels::default()],
        100,
    );
    assert!(!dashboard.observation_complete);
    assert!(
        dashboard
            .rows
            .iter()
            .all(|row| row.value.is_none() && row.p50.is_none())
    );
    assert!(dashboard.to_json(32).is_err());
    let mut value = fixture();
    value.provenance.run = "7".into();
    value.provenance.epoch = 4;
    value.steps[0].causal.machine = Some("3".into());
    let mut timeline = EffectTimeline {
        effect: EffectId {
            run: 7,
            epoch: 4,
            serial: 2,
        },
        origin: RequestOrigin {
            run: 7,
            epoch: 4,
            machine: 3,
            transition_sequence: 1,
            output_index: 0,
        },
        measurement_origin: MeasurementOrigin::Live,
        complete: false,
        events: vec![],
        timings: vec![],
    };
    assert_eq!(
        correlate(&value, &timeline),
        Correlation::Retained {
            local_sequence: 1,
            output: 0,
            detail_complete: false
        }
    );
    timeline.origin.machine = 4;
    assert_eq!(
        correlate(&value, &timeline),
        Correlation::Conflict("machine")
    );
}

#[test]
fn n12_independent_metric_window_and_catalog_goldens() {
    use statelessness_debug::metrics::*;
    let mut store = MetricsStore::new(
        1,
        None,
        LabelCatalog::default(),
        HistogramSchema::default(),
        MetricLimits::default(),
        GaugeScope::Unknown,
    )
    .unwrap();
    store.observe(MetricFamily::AttemptElapsed, Labels::default(), 3);
    let before = store.snapshot(Some(SnapshotTime {
        domain: 1,
        nanos: 5,
    }));
    store.observe(MetricFamily::AttemptElapsed, Labels::default(), 11);
    let after = store.snapshot(Some(SnapshotTime {
        domain: 1,
        nanos: 20,
    }));
    let delta = store.window(before.revision, after.revision).unwrap();
    assert_eq!(delta.temporality, Temporality::Delta);
    assert_eq!(delta.start, before.end);
    let histogram = delta
        .histogram(MetricFamily::AttemptElapsed, Labels::default())
        .unwrap();
    assert_eq!((histogram.count, histogram.sum_ns), (1, 11));
    assert_eq!(
        store.window(after.revision, before.revision),
        Err(MetricError::ReversedWindow)
    );
    let mut other_epoch = after.clone();
    other_epoch.epoch += 1;
    assert_eq!(
        MetricSnapshot::window(&before, &other_epoch),
        Err(MetricError::Incompatible)
    );
    let catalog = metric_catalog();
    assert!(
        catalog
            .iter()
            .all(|entry| !entry.population.is_empty()
                && !entry.allowed_labels.contains(&"effect_id"))
    );
    assert_eq!(
        catalog
            .iter()
            .filter(|entry| entry.family == MetricFamily::AttemptElapsed)
            .count(),
        1
    );
}

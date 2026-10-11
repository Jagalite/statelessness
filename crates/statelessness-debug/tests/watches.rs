use statelessness_debug::inspect::*;
use statelessness_debug::watches::*;
use std::cell::Cell;

struct Guarded<'a> {
    reads: &'a Cell<usize>,
    schema_reads: &'a Cell<usize>,
    number: u128,
    secret: &'static str,
    schema_name: &'static str,
}
impl Inspect for Guarded<'_> {
    fn inspect(
        &self,
        p: &[PathSegment],
        cx: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        self.reads.set(self.reads.get() + 1);
        cx.object(
            p,
            "Guarded",
            &[
                FieldView::new("number", "number", &self.number),
                FieldView::redacted("secret", "secret"),
            ],
        )
    }
    fn schema(&self) -> DisplaySchema {
        self.schema_reads.set(self.schema_reads.get() + 1);
        DisplaySchema {
            name: self.schema_name,
            version: 1,
            source: None,
        }
    }
}
fn path(s: &str) -> Vec<PathSegment> {
    vec![PathSegment::Field(s.into())]
}
fn context(sequence: u64) -> ObservationContext<'static> {
    ObservationContext::new(
        SnapshotId {
            session: 7,
            revision: sequence,
            sequence,
        },
        OriginKind::Simulation,
    )
}
fn registry() -> WatchRegistry {
    WatchRegistry::new(WatchLimits::default(), WatchAuthorization::allow_all())
}
fn state<'a>(reads: &'a Cell<usize>, schema_reads: &'a Cell<usize>) -> Guarded<'a> {
    Guarded {
        reads,
        schema_reads,
        number: u128::MAX,
        secret: "SECRET-NEVER-IN-ANY-DIAGNOSTIC",
        schema_name: "test.guard",
    }
}
#[test]
fn lifecycle_reenable_and_expiry_boundaries_are_explicit() {
    let reads = Cell::new(0);
    let sr = Cell::new(0);
    let mut value = state(&reads, &sr);
    let mut r = registry();
    let ack = r
        .add_validated(&value, WatchConfig::changed(path("number"), 1))
        .unwrap();
    assert_eq!(ack.effective_sequence, 0);
    r.observe(&value, context(0));
    assert_eq!(
        r.current(ack.watch_id).unwrap().kind,
        WatchEventKind::Baseline(BaselineReason::Initial)
    );
    r.observe(&value, context(1));
    assert_eq!(
        r.current(ack.watch_id).unwrap().kind,
        WatchEventKind::Unchanged
    );
    value.number = 8;
    r.observe(&value, context(2));
    assert_eq!(
        r.current(ack.watch_id).unwrap().kind,
        WatchEventKind::Changed
    );
    let disabled = r.disable(ack.watch_id).unwrap();
    assert_eq!(disabled.effective_sequence, 3);
    assert_eq!(disabled.generation, 2);
    let before = reads.get();
    r.observe(&value, context(3));
    assert_eq!(reads.get(), before);
    assert!(r.current(ack.watch_id).is_none());
    let enabled = r.enable(ack.watch_id).unwrap();
    assert_eq!(enabled.generation, 3);
    r.observe(&value, context(4));
    assert_eq!(
        r.current(ack.watch_id).unwrap().kind,
        WatchEventKind::Baseline(BaselineReason::Reenabled)
    );
    r.pause(ack.watch_id).unwrap();
    assert_eq!(r.list()[0].state, WatchState::Paused);
    r.resume(ack.watch_id).unwrap();
    let mut config = WatchConfig::changed(path("number"), 1);
    config.ttl_observations = Some(1);
    r.update_validated(ack.watch_id, &value, config).unwrap();
    r.observe(&value, context(5));
    assert_eq!(r.list()[0].state, WatchState::Expired);
    assert!(!r.interested());
    let removed = r.remove(ack.watch_id).unwrap();
    assert!(r.list().is_empty());
    assert!(
        r.snapshot()
            .events
            .iter()
            .any(|e| e.kind == WatchEventKind::WatchExpired)
    );
    let last = r.snapshot().events.last().unwrap().clone();
    assert_eq!(last.kind, WatchEventKind::WatchRemoved);
    assert_eq!(last.generation, removed.generation);
}
#[test]
fn disabled_and_static_filtered_paths_do_not_read_schema_or_payload() {
    let reads = Cell::new(0);
    let sr = Cell::new(0);
    let value = state(&reads, &sr);
    let mut r = registry();
    r.observe(&value, context(0));
    assert_eq!((reads.get(), sr.get()), (0, 0));
    let mut c = WatchConfig::changed(path("number"), 1);
    c.enabled = false;
    let id = r.add(c.clone()).unwrap().watch_id;
    r.observe(&value, context(1));
    assert_eq!((reads.get(), sr.get()), (0, 0));
    c.enabled = true;
    c.selector.machine = Some("different".into());
    r.update(id, c).unwrap();
    r.observe(&value, context(2));
    assert_eq!((reads.get(), sr.get()), (0, 0));
    assert_eq!(r.health().filtered, 1);
}
#[test]
fn admission_authorization_redaction_and_unknown_paths_are_checked() {
    let reads = Cell::new(0);
    let sr = Cell::new(0);
    let value = state(&reads, &sr);
    let mut r = WatchRegistry::new(
        WatchLimits::default(),
        WatchAuthorization::allow_paths(vec![path("number")]),
    );
    assert_eq!(
        r.add_validated(&value, WatchConfig::changed(path("secret"), 1)),
        Err(WatchError::Unauthorized)
    );
    assert_eq!((reads.get(), sr.get()), (0, 0));
    let mut r = registry();
    assert_eq!(
        r.add_validated(&value, WatchConfig::changed(path("secret"), 1)),
        Err(WatchError::RedactedPath)
    );
    assert_eq!(
        r.add_validated(&value, WatchConfig::changed(path("missing"), 1)),
        Err(WatchError::Inspection(InspectError::PathNotFound))
    );
    assert_eq!(
        r.add_validated(&value, WatchConfig::changed(path("*"), 1)),
        Err(WatchError::Inspection(InspectError::PathNotFound))
    );
    assert!(matches!(
        r.add_validated(&value, WatchConfig::changed(path("number"), 2)),
        Err(WatchError::SchemaMismatch { .. })
    ));
    assert!(r.list().is_empty());
    assert!(r.snapshot().events.is_empty());
    let id = r
        .add_validated(&value, WatchConfig::changed(vec![], 1))
        .unwrap()
        .watch_id;
    r.observe(&value, context(0));
    assert_eq!(r.current(id).unwrap().kind, WatchEventKind::Unknown);
    assert!(!format!("{:?}", r.snapshot()).contains(value.secret));
}
#[test]
fn sampled_evaluation_and_delivery_loss_have_different_meanings() {
    let reads = Cell::new(0);
    let sr = Cell::new(0);
    let mut value = state(&reads, &sr);
    let mut r = registry();
    let mut c = WatchConfig::changed(path("number"), 1);
    c.evaluation_interval = 2;
    let id = r.add(c).unwrap().watch_id;
    r.observe(&value, context(0));
    value.number = 1;
    r.observe(&value, context(1));
    value.number = u128::MAX;
    r.observe(&value, context(2));
    let event = r.current(id).unwrap();
    assert_eq!(
        event.kind,
        WatchEventKind::ChangeSinceLastSample { changed: false }
    );
    assert_eq!(
        event.completeness,
        Completeness::Partial(IncompleteReason::Sampled)
    );
    assert_eq!(event.difference, DiffKind::Unknown);
    assert_eq!(reads.get(), 2);
    let mut c = WatchConfig::changed(path("number"), 1);
    c.delivery_interval = 2;
    r.update(id, c).unwrap();
    r.observe(&value, context(3));
    value.number = 9;
    r.observe(&value, context(4));
    value.number = 10;
    r.observe(&value, context(5));
    assert_eq!(r.current(id).unwrap().kind, WatchEventKind::Changed);
    assert_eq!(r.current(id).unwrap().difference, DiffKind::Changed);
    assert_eq!(r.health().delivery_sampled, 1);
    assert_eq!(r.health().observation_gaps, 0);
}
#[test]
fn missing_observations_and_expired_baselines_start_unknown() {
    let reads = Cell::new(0);
    let sr = Cell::new(0);
    let value = state(&reads, &sr);
    let mut r = registry();
    let id = r
        .add(WatchConfig::changed(path("number"), 1))
        .unwrap()
        .watch_id;
    r.observe(&value, context(0));
    assert!(r.observe(&value, context(3)).observation_gap);
    assert_eq!(
        r.current(id).unwrap().kind,
        WatchEventKind::Baseline(BaselineReason::ObservationGap)
    );
    assert_eq!(r.health().observation_gaps, 1);
    let before = reads.get();
    assert!(r.observe(&value, context(3)).stale);
    assert_eq!(reads.get(), before);
    let mut c = WatchConfig::changed(path("number"), 1);
    c.baseline_ttl_sequences = Some(1);
    c.evaluation_interval = 3;
    r.update(id, c).unwrap();
    r.observe(&value, context(4));
    r.observe(&value, context(5));
    r.observe(&value, context(6));
    r.observe(&value, context(7));
    assert_eq!(
        r.current(id).unwrap().kind,
        WatchEventKind::Baseline(BaselineReason::Expired)
    );
}
#[test]
fn schema_changes_pause_before_selected_value_access() {
    let reads = Cell::new(0);
    let sr = Cell::new(0);
    let mut value = state(&reads, &sr);
    let mut r = registry();
    let id = r
        .add_validated(&value, WatchConfig::changed(path("number"), 1))
        .unwrap()
        .watch_id;
    r.observe(&value, context(0));
    let before = reads.get();
    value.schema_name = "changed";
    r.observe(&value, context(1));
    assert_eq!(reads.get(), before);
    assert_eq!(r.list()[0].state, WatchState::Paused);
    assert!(r.current(id).is_none());
    assert!(
        r.snapshot()
            .events
            .iter()
            .any(|e| e.kind == WatchEventKind::SchemaChanged)
    );
}
#[test]
fn matching_queries_are_shared_and_event_loss_does_not_reset_baselines() {
    let reads = Cell::new(0);
    let sr = Cell::new(0);
    let mut value = state(&reads, &sr);
    let limits = WatchLimits {
        max_ring_records: 2,
        ..WatchLimits::default()
    };
    let mut r = WatchRegistry::new(limits, WatchAuthorization::allow_all());
    let id = r
        .add(WatchConfig::changed(path("number"), 1))
        .unwrap()
        .watch_id;
    r.add(WatchConfig::changed(path("number"), 1)).unwrap();
    r.observe(&value, context(0));
    assert_eq!(reads.get(), 1);
    for seq in 1..10 {
        value.number = seq.into();
        r.observe(&value, context(seq));
    }
    assert_eq!(reads.get(), 10);
    assert!(r.health().dropped_events > 0);
    assert_eq!(r.health().observation_gaps, 0);
    assert_eq!(r.current(id).unwrap().kind, WatchEventKind::Changed);
    assert!(r.snapshot().events.len() <= 2);
    assert!(r.snapshot().retained_bytes <= limits.max_ring_bytes);
}
#[test]
fn count_byte_baseline_and_failure_budgets_are_independent() {
    let reads = Cell::new(0);
    let sr = Cell::new(0);
    let value = state(&reads, &sr);
    let limits = WatchLimits {
        max_watches: 1,
        max_ring_bytes: 4096,
        max_baseline_bytes: 0,
        ..WatchLimits::default()
    };
    let mut r = WatchRegistry::new(limits, WatchAuthorization::allow_all());
    let id = r
        .add(WatchConfig::changed(path("number"), 1))
        .unwrap()
        .watch_id;
    assert_eq!(
        r.add(WatchConfig::changed(path("number"), 1)),
        Err(WatchError::Capacity)
    );
    let mut ctx = context(0);
    ctx.property_failure = true;
    r.observe(&value, ctx);
    assert!(r.current(id).is_none());
    assert_eq!(r.health().baseline_evictions, 1);
    let failure = r.failure_snapshot().unwrap();
    assert!(
        failure
            .events
            .iter()
            .any(|e| e.kind == WatchEventKind::PropertyFailure)
    );
    assert!(failure.retained_bytes <= limits.max_ring_bytes);
    assert!(!format!("{failure:?}").contains(value.secret));
    r.observe(&value, context(1));
    assert_eq!(
        r.snapshot()
            .events
            .iter()
            .rev()
            .find(|e| e.value.is_some())
            .unwrap()
            .kind,
        WatchEventKind::Baseline(BaselineReason::Evicted)
    );
    assert_eq!(r.failure_snapshot().unwrap().at, Some(ctx.snapshot));
}
#[test]
fn inspection_errors_are_diagnostic_and_preserve_metadata() {
    let reads = Cell::new(0);
    let sr = Cell::new(0);
    let value = state(&reads, &sr);
    let mut r = registry();
    let id = r
        .add(WatchConfig::changed(path("missing"), 1))
        .unwrap()
        .watch_id;
    let summary = r.observe(&value, context(0));
    assert_eq!(summary.inspection_errors, 1);
    let event = r.current(id).unwrap();
    assert_eq!(
        event.kind,
        WatchEventKind::InspectorError(InspectError::PathNotFound)
    );
    assert_eq!(event.path, path("missing"));
    assert_eq!(event.schema_name.as_deref(), Some("test.guard"));
    assert_eq!(event.phase, ObservationPhase::Initial);
    assert_eq!(event.snapshot.session, 7);
    assert!(event.value.is_none());
}
#[test]
fn lazy_post_turn_marker_skips_disabled_and_full_payloads() {
    let calls = Cell::new(0);
    let mut disabled = MarkerCollector::disabled();
    disabled.marker("site", || {
        calls.set(calls.get() + 1);
        "payload".into()
    });
    assert_eq!(calls.get(), 0);
    assert!(disabled.finish().markers.is_empty());
    let mut active = MarkerCollector::new(true, 1, 1024);
    active.marker("site", || {
        calls.set(calls.get() + 1);
        "value".into()
    });
    active.marker("site", || {
        calls.set(calls.get() + 1);
        "more".into()
    });
    assert_eq!(calls.get(), 1);
    let batch = active.finish();
    assert_eq!(batch.markers.len(), 1);
    assert_eq!(batch.dropped, 1);
}

#[test]
fn explicit_snapshot_returns_type_and_does_not_replace_change_baseline() {
    let reads = Cell::new(0);
    let sr = Cell::new(0);
    let mut value = state(&reads, &sr);
    let mut r = registry();
    let ack = r
        .add_validated(&value, WatchConfig::changed(path("number"), 1))
        .unwrap();
    assert_eq!(ack.resolved_type, Some("u128"));
    assert_eq!(ack.display_schema.unwrap().name, "test.guard");
    r.observe(&value, context(0));
    value.number = 4;
    let snapshot = r.snapshot_now(ack.watch_id, &value, context(0)).unwrap();
    assert_eq!(snapshot.kind, WatchEventKind::Snapshot);
    assert_eq!(
        r.current(ack.watch_id).unwrap().kind,
        WatchEventKind::Baseline(BaselineReason::Initial)
    );
    r.observe(&value, context(1));
    assert_eq!(
        r.current(ack.watch_id).unwrap().kind,
        WatchEventKind::Changed
    );
}
#[test]
fn exact_recording_and_replay_bytes_are_identical_with_watches_off_and_on() {
    use stateless::demo::{Input, RequestModel};
    use stateless::monitor::RecorderOptions;
    use stateless::observation::DebugObservation;
    use stateless::trace::RunConfig;
    use statelessness_debug::{DebugSession, InputPolicy, SessionLimits};
    fn run(enabled: bool) -> Vec<u8> {
        let mut session = DebugSession::recording(
            "parity",
            RequestModel::buggy(),
            InputPolicy::enumerated(),
            SessionLimits::default(),
            RunConfig::default(),
            RecorderOptions::default(),
        )
        .unwrap();
        let mut watches = registry();
        let mut cfg = WatchConfig::changed(path("ready"), 1);
        cfg.enabled = enabled;
        let id = watches.add(cfg).unwrap().watch_id;
        watches.observe(session.state(), context(0));
        for (index, input) in [Input::Start, Input::Cancel, Input::Complete(1)]
            .into_iter()
            .enumerate()
        {
            if enabled && index == 1 {
                watches.disable(id).unwrap();
            }
            if enabled && index == 2 {
                watches.enable(id).unwrap();
            }
            session
                .step_with_observer(index as u64, input, |observation| {
                    if let DebugObservation::Turn(turn) = observation {
                        let mut ctx = context(turn.sequence);
                        ctx.property_failure = turn.checks.iter().any(stateless::Check::is_failure);
                        watches.observe(turn.transition.state, ctx);
                    }
                    Ok(())
                })
                .unwrap();
        }
        let trace = session.export_trace().unwrap();
        let report = stateless::execution::replay(
            session.model(),
            &trace,
            stateless::execution::ReplayOptions::default(),
        )
        .unwrap();
        assert!(report.failure_reproduced);
        let mut bytes = vec![];
        trace.write_to(&mut bytes).unwrap();
        bytes
    }
    assert_eq!(run(false), run(true));
}
#[test]
fn boundary_reconfiguration_cannot_change_an_in_progress_generation() {
    use std::sync::{Arc, Barrier, Mutex};
    struct Slow {
        entered: Arc<Barrier>,
        release: Arc<Barrier>,
    }
    impl Inspect for Slow {
        fn inspect(
            &self,
            p: &[PathSegment],
            c: &mut InspectContext,
        ) -> Result<InspectNode, InspectError> {
            self.entered.wait();
            self.release.wait();
            1u8.inspect(p, c)
        }
    }
    let mut r = registry();
    let id = r.add(WatchConfig::changed(vec![], 1)).unwrap().watch_id;
    let r = Arc::new(Mutex::new(r));
    let entered = Arc::new(Barrier::new(2));
    let release = Arc::new(Barrier::new(2));
    let observing = r.clone();
    let a = entered.clone();
    let b = release.clone();
    let observation = std::thread::spawn(move || {
        observing.lock().unwrap().observe(
            &Slow {
                entered: a,
                release: b,
            },
            context(0),
        )
    });
    entered.wait();
    let configuring = r.clone();
    let configured = std::thread::spawn(move || configuring.lock().unwrap().disable(id).unwrap());
    release.wait();
    assert_eq!(observation.join().unwrap().evaluated, 1);
    let ack = configured.join().unwrap();
    assert_eq!(ack.effective_sequence, 1);
    assert_eq!(ack.generation, 2);
    let r = r.lock().unwrap();
    let baseline = r
        .snapshot()
        .events
        .into_iter()
        .find(|e| matches!(e.kind, WatchEventKind::Baseline(_)))
        .unwrap();
    assert_eq!(baseline.generation, 1);
    assert_eq!(r.list()[0].state, WatchState::Disabled);
}
#[test]
fn committed_boundary_watch_cannot_invent_internal_reverted_changes() {
    let reads = Cell::new(0);
    let sr = Cell::new(0);
    let value = state(&reads, &sr);
    let mut r = registry();
    let id = r
        .add(WatchConfig::changed(path("number"), 1))
        .unwrap()
        .watch_id;
    r.observe(&value, context(0));
    // The host performed its transition and the committed value is unchanged.
    r.observe(&value, context(1));
    assert_eq!(r.current(id).unwrap().kind, WatchEventKind::Unchanged);
}

#[test]
fn live_identity_and_machine_changes_never_claim_cross_machine_change() {
    let reads = Cell::new(0);
    let sr = Cell::new(0);
    let mut value = state(&reads, &sr);
    let mut r = registry();
    let id = r
        .add(WatchConfig::changed(path("number"), 1))
        .unwrap()
        .watch_id;
    let mut first = context(0);
    first.origin = OriginKind::RecordedLive;
    first.model = Some("request-model");
    first.machine = Some("first");
    r.observe(&value, first);
    assert_eq!(r.current(id).unwrap().origin, OriginKind::RecordedLive);
    value.number = 1;
    let mut second = first;
    second.snapshot = context(1).snapshot;
    second.machine = Some("second");
    r.observe(&value, second);
    let event = r.current(id).unwrap();
    assert_eq!(
        event.kind,
        WatchEventKind::Baseline(BaselineReason::SourceChanged)
    );
    assert_eq!(event.difference, DiffKind::Unknown);
    assert_eq!(event.machine.as_deref(), Some("second"));
}
#[test]
fn failure_annotation_is_bounded_and_cannot_hide_an_inspector_error() {
    let reads = Cell::new(0);
    let sr = Cell::new(0);
    let value = state(&reads, &sr);
    let mut r = registry();
    let id = r
        .add(WatchConfig::changed(path("missing"), 1))
        .unwrap()
        .watch_id;
    let ids: Vec<&str> = (0..20).map(|_| "some-property").collect();
    let mut ctx = context(0);
    ctx.property_failure = true;
    ctx.failing_properties = &ids;
    r.observe(&value, ctx);
    let event = r.current(id).unwrap();
    assert!(event.property_failure);
    assert_eq!(event.failing_properties.len(), 16);
    assert_eq!(
        event.kind,
        WatchEventKind::InspectorError(InspectError::PathNotFound)
    );
    assert_ne!(event.completeness, Completeness::Complete);
    assert_eq!(r.health().inspection_errors, 1);
}

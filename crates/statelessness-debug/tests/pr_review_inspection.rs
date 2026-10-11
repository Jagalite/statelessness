//! Independent pre-PR regressions. Do not infer success from historical logs.
use statelessness_debug::diagnostic::*;
use statelessness_debug::inspect::*;
use statelessness_debug::watches::*;

fn context(sequence: u64) -> ObservationContext<'static> {
    ObservationContext::new(
        SnapshotId {
            session: 13,
            revision: sequence,
            sequence,
        },
        OriginKind::Simulation,
    )
}
fn registry(config: WatchConfig) -> (WatchRegistry, u64) {
    let mut registry = WatchRegistry::new(WatchLimits::default(), WatchAuthorization::allow_all());
    let id = registry.add(config).unwrap().watch_id;
    (registry, id)
}

#[test]
fn failure_relabeling_keeps_initial_and_gap_differences_unknown() {
    let (mut registry, id) = registry(WatchConfig::changed(vec![], 1));
    let mut initial = context(0);
    initial.property_failure = true;
    registry.observe(&1u8, initial);
    let event = registry.current(id).unwrap();
    assert_eq!(event.kind, WatchEventKind::PropertyFailure);
    assert_eq!(event.difference, DiffKind::Unknown);
    let mut after_gap = context(3);
    after_gap.property_failure = true;
    registry.observe(&2u8, after_gap);
    assert_eq!(registry.current(id).unwrap().difference, DiffKind::Unknown);
}

#[test]
fn failure_relabeling_preserves_sampled_completeness_and_unknown_diff() {
    let mut config = WatchConfig::changed(vec![], 1);
    config.evaluation_interval = 3;
    let (mut registry, id) = registry(config);
    registry.observe(&1u8, context(0));
    registry.observe(&2u8, context(1));
    let mut failure = context(2);
    failure.property_failure = true;
    registry.observe(&1u8, failure);
    let event = registry.current(id).unwrap();
    assert_eq!(event.kind, WatchEventKind::PropertyFailure);
    assert_eq!(event.difference, DiffKind::Unknown);
    assert_eq!(
        event.completeness,
        Completeness::Partial(IncompleteReason::Sampled)
    );
}

#[test]
fn value_match_relabeling_does_not_invent_an_initial_addition() {
    let mut config = WatchConfig::changed(vec![], 1);
    config.trigger = WatchTrigger::Equals(Scalar::Bool(true));
    let (mut registry, id) = registry(config);
    registry.observe(&true, context(0));
    let event = registry.current(id).unwrap();
    assert_eq!(event.kind, WatchEventKind::ValueMatched);
    assert_eq!(event.difference, DiffKind::Unknown);
}

#[test]
fn watch_origin_changes_reset_comparison_before_failure_capture() {
    let (mut registry, id) = registry(WatchConfig::changed(vec![], 1));
    registry.observe(&1u8, context(0));
    let mut replay = context(1);
    replay.origin = OriginKind::ExactReplay;
    registry.observe(&2u8, replay);
    let event = registry.current(id).unwrap();
    assert_eq!(
        event.kind,
        WatchEventKind::Baseline(BaselineReason::SourceChanged)
    );
    assert_eq!(event.difference, DiffKind::Unknown);
}

#[test]
fn intrinsically_incomplete_kinds_cannot_claim_equality() {
    for kind in [
        NodeKind::Redacted,
        NodeKind::Unavailable,
        NodeKind::Opaque {
            label: "safe metadata".into(),
        },
        NodeKind::Truncated {
            reason: IncompleteReason::Bytes,
        },
        NodeKind::Map {
            ordering: MapOrdering::Unstable,
        },
        NodeKind::String {
            preview: "short".into(),
            total_bytes: 100,
        },
        NodeKind::Bytes {
            preview: vec![1],
            total_bytes: 100,
        },
    ] {
        // Public handwritten adapters can build nodes directly. The comparator
        // must not turn one inconsistent completeness tag into claimed equality.
        let node = InspectNode {
            kind,
            children: vec![],
            child_count: Some(0),
            page: None,
            completeness: Completeness::Complete,
        };
        assert!(!node.is_complete());
        assert_eq!(compare_nodes(Some(&node), Some(&node)), DiffKind::Unknown);
    }
}

#[test]
fn inconsistent_paging_counts_and_partial_descendants_never_certify_equality() {
    let original = inspect(&vec![1u8], &InspectQuery::default()).unwrap().node;
    assert!(original.is_complete());
    let mut cases = vec![];
    let mut changed = original.clone();
    changed.child_count = Some(2);
    cases.push(changed);
    let mut changed = original.clone();
    changed.child_count = None;
    cases.push(changed);
    for page in [
        PageInfo {
            offset: 1,
            returned: 1,
            total: 1,
            next_offset: None,
        },
        PageInfo {
            offset: 0,
            returned: 0,
            total: 1,
            next_offset: None,
        },
        PageInfo {
            offset: 0,
            returned: 1,
            total: 2,
            next_offset: None,
        },
        PageInfo {
            offset: 0,
            returned: 1,
            total: 1,
            next_offset: Some(1),
        },
    ] {
        let mut changed = original.clone();
        changed.page = Some(page);
        cases.push(changed);
    }
    let mut changed = original;
    changed.children[0].node.kind = NodeKind::Redacted;
    cases.push(changed);
    for node in cases {
        assert!(!node.is_complete());
        assert_eq!(compare_nodes(Some(&node), Some(&node)), DiffKind::Unknown);
    }
}

fn subscription(sink: u64) -> Subscription {
    Subscription {
        sink,
        site: "value".into(),
        kind: SiteKind::Probe,
        path: vec![],
        trigger: Trigger::Changed,
        sample_every: 1,
        minimum_severity: Severity::Debug,
    }
}

fn hub(subscriptions: Vec<Subscription>) -> DiagnosticHub {
    let mut hub = DiagnosticHub::new(DiagnosticLimits::default());
    hub.add_sink(1, SinkPermissions::local_all(), SinkLimits::default())
        .unwrap();
    hub.register_producer(1, 1).unwrap();
    let ack = hub.configure(0, subscriptions).unwrap();
    hub.acknowledge(1, ack.capture_revision, 0).unwrap();
    hub
}

#[test]
fn probe_run_and_origin_changes_do_not_reuse_another_stream_baseline() {
    for change_run in [true, false] {
        let mut hub = hub(vec![subscription(1)]);
        let mut first = hub.begin_turn(1, 10, 0, DiagnosticOrigin::Live).unwrap();
        first.probe("value", || 1u8);
        first.finish(true);
        hub.pop(1).unwrap();
        let mut next = hub
            .begin_turn(
                1,
                if change_run { 11 } else { 10 },
                1,
                if change_run {
                    DiagnosticOrigin::Live
                } else {
                    DiagnosticOrigin::Replay
                },
            )
            .unwrap();
        next.probe("value", || 2u8);
        next.finish(true);
        let event = hub.pop(1).unwrap();
        assert!(event.baseline);
        assert!(event.change_unknown);
    }
}

#[test]
fn probe_machine_and_entity_context_changes_reset_unscoped_comparisons() {
    let mut hub = hub(vec![subscription(1)]);
    for (turn, machine, entity) in [
        (0, "machine-a", "entity-a"),
        (1, "machine-b", "entity-a"),
        (2, "machine-b", "entity-b"),
    ] {
        let mut scope = hub
            .begin_turn_with_context(
                1,
                1,
                turn,
                DiagnosticOrigin::Live,
                CaptureContext {
                    machine: Some(machine),
                    entity: Some(entity),
                    ..Default::default()
                },
            )
            .unwrap();
        scope.probe("value", || 1u8);
        scope.finish(true);
        let event = hub
            .pop(1)
            .expect("a new source must emit even if its value matches");
        assert!(event.baseline);
        if turn > 0 {
            assert!(event.change_unknown);
            assert_eq!(
                format!("{:?}", event.baseline_reason),
                "Some(SourceChanged)"
            );
        }
    }
}

#[test]
fn selected_subscribers_share_access_but_never_share_unauthorized_fields() {
    use std::cell::Cell;
    struct Guarded<'a> {
        reads: &'a Cell<usize>,
        secret: &'a str,
    }
    impl Inspect for Guarded<'_> {
        fn inspect(
            &self,
            path: &[PathSegment],
            cx: &mut InspectContext,
        ) -> Result<InspectNode, InspectError> {
            self.reads.set(self.reads.get() + 1);
            cx.object(
                path,
                "Guarded",
                &[
                    FieldView::new("public", "public", &17u8),
                    FieldView::redacted("secret", "secret"),
                ],
            )
        }
    }
    let mut hub = hub(vec![]);
    for sink in [2, 3] {
        hub.add_sink(
            sink,
            SinkPermissions {
                sites: vec!["value".into()],
                paths: vec![vec![PathSegment::Field("public".into())]],
            },
            SinkLimits::default(),
        )
        .unwrap();
    }
    let mut selected = subscription(2);
    selected.path = vec![PathSegment::Field("public".into())];
    let mut third = selected.clone();
    third.sink = 3;
    let ack = hub
        .configure(hub.revision(), vec![subscription(1), selected, third])
        .unwrap();
    hub.acknowledge(1, ack.capture_revision, 0).unwrap();
    let reads = Cell::new(0);
    let value = Guarded {
        reads: &reads,
        secret: "PR-REVIEW-SECRET",
    };
    let mut scope = hub.begin_turn(1, 10, 0, DiagnosticOrigin::Live).unwrap();
    scope.probe("value", || &value);
    scope.finish(true);
    assert_eq!(
        reads.get(),
        2,
        "one root query and one shared public-path query"
    );
    let events: Vec<_> = [1, 2, 3]
        .into_iter()
        .map(|id| hub.pop(id).unwrap())
        .collect();
    assert!(matches!(
        events[0].payload.as_ref().unwrap().kind,
        NodeKind::Object { .. }
    ));
    for event in &events[1..] {
        assert!(matches!(
            event.payload.as_ref().unwrap().kind,
            NodeKind::Scalar(_)
        ));
    }
    struct Custom(Vec<String>);
    impl DiagnosticSink for Custom {
        fn emit(&mut self, event: &DiagnosticEvent) -> std::io::Result<()> {
            self.0.push(format!("{event:?}"));
            Ok(())
        }
    }
    let mut custom = Custom(vec![]);
    let mut text = TextSink { writer: vec![] };
    let mut jsonl = JsonlSink { writer: vec![] };
    for event in &events {
        custom.emit(event).unwrap();
        text.emit(event).unwrap();
        jsonl.emit(event).unwrap();
    }
    for output in [
        custom.0.join("\n"),
        String::from_utf8(text.writer).unwrap(),
        String::from_utf8(jsonl.writer).unwrap(),
    ] {
        assert!(!output.contains(value.secret));
    }
    let trace = stateless::execution::record(
        &stateless::demo::RequestModel::buggy(),
        [],
        Default::default(),
        0,
    )
    .unwrap();
    let manifest = SidecarManifest::for_trace(&trace, 10).unwrap();
    let mut sidecar = vec![];
    manifest
        .write_jsonl(&events, &mut sidecar, 3, 64 * 1024)
        .unwrap();
    assert!(!String::from_utf8(sidecar).unwrap().contains(value.secret));
}

#[test]
fn diagnostic_errors_never_capture_callback_error_text_or_change_other_sinks() {
    struct FailingInspector;
    impl Inspect for FailingInspector {
        fn inspect(
            &self,
            _: &[PathSegment],
            _: &mut InspectContext,
        ) -> Result<InspectNode, InspectError> {
            Err(InspectError::InvalidMetadata)
        }
    }
    struct FailingExporter;
    impl DiagnosticSink for FailingExporter {
        fn emit(&mut self, _: &DiagnosticEvent) -> std::io::Result<()> {
            Err(std::io::Error::other("PRIVATE-EXPORTER-ERROR-CONTENT"))
        }
    }
    let mut hub = hub(vec![]);
    hub.add_sink(2, SinkPermissions::local_all(), SinkLimits::default())
        .unwrap();
    let ack = hub
        .configure(hub.revision(), vec![subscription(1), subscription(2)])
        .unwrap();
    hub.acknowledge(1, ack.capture_revision, 0).unwrap();
    let mut scope = hub.begin_turn(1, 1, 0, DiagnosticOrigin::Live).unwrap();
    scope.probe("value", || FailingInspector);
    scope.finish(false);
    for id in [1, 2] {
        let event = hub.peek(id).unwrap();
        assert!(event.payload.is_none() && event.change_unknown && event.incomplete_turn);
        assert_eq!(hub.health(id).unwrap().inspection_failed, 1);
    }
    assert_eq!(hub.drain_to(1, &mut FailingExporter, 1), Ok(0));
    assert_eq!(hub.health(1).unwrap().exporter_failed, 1);
    assert_eq!(hub.queued(2).unwrap().0, 1);
    assert!(!format!("{:?}{:?}", hub.health(1), hub.peek(2)).contains("PRIVATE-EXPORTER"));
}

#[test]
fn callback_panics_unwind_explicitly_and_partial_batches_stay_honest() {
    use std::panic::{AssertUnwindSafe, catch_unwind};
    let mut hub = hub(vec![subscription(1)]);
    let failed = catch_unwind(AssertUnwindSafe(|| {
        let mut scope = hub.begin_turn(1, 1, 0, DiagnosticOrigin::Test).unwrap();
        scope.probe("value", || 7u8);
        scope.probe("value", || -> u8 { panic!("intentional callback panic") });
        scope.finish(true);
    }));
    assert!(
        failed.is_err(),
        "the library must not pretend arbitrary panics are recoverable"
    );
    let event = hub.pop(1).unwrap();
    assert!(event.incomplete_turn);
    assert_eq!(hub.queued(1).unwrap().0, 0);
}

#[test]
fn inspector_and_exporter_panics_propagate_at_the_documented_host_boundary() {
    use std::panic::{AssertUnwindSafe, catch_unwind};
    struct PanicInspect(bool);
    impl Inspect for PanicInspect {
        fn schema(&self) -> DisplaySchema {
            assert!(!self.0, "intentional schema callback panic");
            DisplaySchema {
                name: "PanicInspect",
                version: 1,
                source: None,
            }
        }
        fn inspect(
            &self,
            _: &[PathSegment],
            _: &mut InspectContext,
        ) -> Result<InspectNode, InspectError> {
            panic!("intentional inspect callback panic")
        }
    }
    for schema_panic in [true, false] {
        let mut hub = hub(vec![subscription(1)]);
        assert!(
            catch_unwind(AssertUnwindSafe(|| {
                let mut scope = hub.begin_turn(1, 1, 0, DiagnosticOrigin::Test).unwrap();
                scope.probe("value", || 7u8);
                scope.probe("value", || PanicInspect(schema_panic));
            }))
            .is_err()
        );
        assert!(hub.pop(1).unwrap().incomplete_turn);
        assert!(hub.pop(1).is_none());
    }
    struct PanicExporter;
    impl DiagnosticSink for PanicExporter {
        fn emit(&mut self, _: &DiagnosticEvent) -> std::io::Result<()> {
            panic!("intentional exporter callback panic")
        }
    }
    let mut hub = hub(vec![subscription(1)]);
    let mut scope = hub.begin_turn(1, 1, 0, DiagnosticOrigin::Test).unwrap();
    scope.probe("value", || 7u8);
    scope.finish(true);
    assert!(
        catch_unwind(AssertUnwindSafe(|| {
            hub.drain_to(1, &mut PanicExporter, 1).unwrap();
        }))
        .is_err()
    );
    // Recoverability and callback panic hooks belong to an explicit host policy;
    // the library does not turn arbitrary unwinding into successful export.
    assert_eq!(hub.queued(1).unwrap().0, 0);
}

#[test]
fn display_metadata_changes_do_not_change_struct_or_enum_exact_bytes() {
    use stateless::value_codec::TraceEncode;
    #[derive(statelessness_macros::Inspect, statelessness_macros::TraceEncode)]
    struct Original {
        value: u64,
        secret: u8,
    }
    #[derive(statelessness_macros::Inspect, statelessness_macros::TraceEncode)]
    #[inspect(version = 7, label = "renamed display")]
    struct DisplayOnly {
        #[inspect(id = "renamed", label = "new label")]
        value: u64,
        #[inspect(redact)]
        secret: u8,
    }
    #[derive(statelessness_macros::Inspect, statelessness_macros::TraceEncode)]
    enum OriginalEnum {
        #[trace(tag = 4)]
        Value(u64),
    }
    #[derive(statelessness_macros::Inspect, statelessness_macros::TraceEncode)]
    enum DisplayEnum {
        #[trace(tag = 4)]
        #[inspect(id = "display-value")]
        Value(#[inspect(redact)] u64),
    }
    assert_eq!(
        Original {
            value: u64::MAX,
            secret: 42
        }
        .trace_bytes(64)
        .unwrap(),
        DisplayOnly {
            value: u64::MAX,
            secret: 42
        }
        .trace_bytes(64)
        .unwrap()
    );
    assert_eq!(
        OriginalEnum::Value(u64::MAX).trace_bytes(64).unwrap(),
        DisplayEnum::Value(u64::MAX).trace_bytes(64).unwrap()
    );
}

#[test]
fn disabled_filtered_and_unauthorized_paths_do_not_call_callbacks() {
    use std::cell::Cell;
    struct NeverRead;
    impl Inspect for NeverRead {
        fn schema(&self) -> DisplaySchema {
            panic!("unexpected schema read")
        }
        fn inspect(
            &self,
            _: &[PathSegment],
            _: &mut InspectContext,
        ) -> Result<InspectNode, InspectError> {
            panic!("unexpected payload read")
        }
    }
    let (mut registry, id) = registry({
        let mut config = WatchConfig::changed(vec![], 1);
        config.enabled = false;
        config
    });
    registry.observe(&NeverRead, context(0));
    let mut config = WatchConfig::changed(vec![], 1);
    config.selector.machine = Some("selected".into());
    registry.update(id, config).unwrap();
    registry.observe(&NeverRead, context(1));
    let mut hub = hub(vec![]);
    let calls = Cell::new(0);
    let mut scope = hub.begin_turn(1, 1, 0, DiagnosticOrigin::Test).unwrap();
    scope.probe("value", || {
        calls.set(calls.get() + 1);
        NeverRead
    });
    scope.finish(true);
    assert_eq!(calls.get(), 0);
    let mut registry = WatchRegistry::new(WatchLimits::default(), WatchAuthorization::deny_all());
    assert_eq!(
        registry.add_validated(&NeverRead, WatchConfig::changed(vec![], 1)),
        Err(WatchError::Unauthorized)
    );
}

#[test]
fn failed_projection_and_stale_boundary_do_not_retain_a_baseline() {
    struct MaybeValue(bool);
    impl Inspect for MaybeValue {
        fn inspect(
            &self,
            p: &[PathSegment],
            cx: &mut InspectContext,
        ) -> Result<InspectNode, InspectError> {
            if self.0 {
                9u8.inspect(p, cx)
            } else {
                Err(InspectError::PathNotFound)
            }
        }
    }
    let (mut registry, id) = registry(WatchConfig::changed(vec![], 1));
    registry.observe(&MaybeValue(true), context(0));
    registry.observe(&MaybeValue(false), context(1));
    assert!(matches!(
        registry.current(id).unwrap().kind,
        WatchEventKind::InspectorError(_)
    ));
    assert!(registry.current(id).unwrap().value.is_none());
    assert!(registry.observe(&MaybeValue(true), context(1)).stale);
    registry.observe(&MaybeValue(true), context(2));
    assert_eq!(
        registry.current(id).unwrap().kind,
        WatchEventKind::Baseline(BaselineReason::ObservationGap)
    );
}

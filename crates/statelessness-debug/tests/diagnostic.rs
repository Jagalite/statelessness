use statelessness_debug::diagnostic::*;
use statelessness_debug::inspect::{
    Inspect, InspectContext, InspectError, InspectNode, PathSegment,
};
use std::cell::Cell;
fn subscription(sink: u64) -> Subscription {
    Subscription {
        sink,
        site: "counter".into(),
        kind: SiteKind::Probe,
        path: vec![],
        trigger: Trigger::Every,
        sample_every: 1,
        minimum_severity: Severity::Debug,
    }
}
fn hub() -> DiagnosticHub {
    let mut hub = DiagnosticHub::new(DiagnosticLimits::default());
    hub.add_sink(1, SinkPermissions::local_all(), SinkLimits::default())
        .unwrap();
    hub.register_producer(1, 7).unwrap();
    hub
}
fn configure(hub: &mut DiagnosticHub, subscriptions: Vec<Subscription>) {
    let ack = hub.configure(hub.revision(), subscriptions).unwrap();
    hub.acknowledge(1, ack.capture_revision, 0).unwrap();
}
#[test]
fn disabled_probe_never_evaluates_closure_or_inspector() {
    let mut hub = hub();
    let calls = Cell::new(0);
    let mut scope = hub
        .begin_turn(1, 1, 0, DiagnosticOrigin::Simulation)
        .unwrap();
    scope.probe("counter", || {
        calls.set(calls.get() + 1);
        42u128
    });
    scope.finish(true);
    assert_eq!(calls.get(), 0);
    assert_eq!(hub.queued(1), Some((0, 0)));
}
#[test]
fn multiple_destinations_evaluate_once_and_do_not_rerun_application_work() {
    let mut hub = hub();
    hub.add_sink(2, SinkPermissions::local_all(), SinkLimits::default())
        .unwrap();
    configure(&mut hub, vec![subscription(1), subscription(2)]);
    let calls = Cell::new(0);
    let reducer_calls = Cell::new(0);
    let mut scope = hub
        .begin_turn(1, 1, 0, DiagnosticOrigin::Simulation)
        .unwrap();
    reducer_calls.set(reducer_calls.get() + 1);
    let result = 41u128 + 1;
    scope.probe("counter", || {
        calls.set(calls.get() + 1);
        &result
    });
    scope.finish(true);
    assert_eq!(calls.get(), 1);
    assert_eq!(reducer_calls.get(), 1);
    assert!(hub.pop(1).is_some());
    assert!(hub.pop(2).is_some());
}
#[test]
fn revisions_are_per_producer_and_acknowledged_at_boundaries() {
    let mut hub = hub();
    hub.register_producer(2, 8).unwrap();
    let ack = hub.configure(0, vec![subscription(1)]).unwrap();
    assert_eq!(ack.pending_producers, vec![1, 2]);
    hub.acknowledge(1, 1, 1).unwrap();
    {
        let mut scope = hub.begin_turn(2, 1, 0, DiagnosticOrigin::Live).unwrap();
        scope.probe("counter", || 42u64);
        scope.finish(true);
    }
    assert!(hub.pop(1).is_none());
    {
        let mut scope = hub.begin_turn(1, 1, 1, DiagnosticOrigin::Live).unwrap();
        scope.probe("counter", || 42u64);
        scope.finish(true);
    }
    assert_eq!(hub.pop(1).unwrap().capture_revision, 1);
    assert_eq!(
        hub.configure(0, vec![]),
        Err(DiagnosticError::StaleRevision)
    );
    assert_eq!(
        hub.acknowledge(1, 1, 1),
        Err(DiagnosticError::StaleBoundary)
    );
}
#[test]
fn drop_is_per_sink_and_health_survives_a_saturated_queue() {
    let mut hub = hub();
    hub.add_sink(
        2,
        SinkPermissions::local_all(),
        SinkLimits {
            records: 1,
            ..Default::default()
        },
    )
    .unwrap();
    configure(&mut hub, vec![subscription(1), subscription(2)]);
    for turn in 0..3 {
        let mut s = hub.begin_turn(1, 1, turn, DiagnosticOrigin::Live).unwrap();
        s.probe("counter", || turn);
        s.finish(true);
    }
    assert_eq!(hub.queued(1).unwrap().0, 3);
    assert_eq!(hub.queued(2).unwrap().0, 1);
    assert_eq!(hub.health(1).unwrap().dropped, 0);
    assert_eq!(hub.health(2).unwrap().dropped, 2);
}
#[test]
fn delivery_sampling_preserves_change_baseline() {
    let mut hub = hub();
    let mut sub = subscription(1);
    sub.trigger = Trigger::Changed;
    sub.sample_every = 2;
    configure(&mut hub, vec![sub]);
    for (turn, value) in [0, 1, 1, 2].into_iter().enumerate() {
        let mut s = hub
            .begin_turn(1, 1, turn as u64, DiagnosticOrigin::Live)
            .unwrap();
        s.probe("counter", || value as u64);
        s.finish(true);
    }
    assert_eq!(hub.queued(1).unwrap().0, 2);
    assert_eq!(hub.health(1).unwrap().sampled, 1);
    assert_eq!(hub.health(1).unwrap().filtered, 1);
    assert!(hub.pop(1).unwrap().baseline);
    assert!(!hub.pop(1).unwrap().baseline);
}
#[test]
fn threshold_crossing_has_hysteresis_and_cooldown() {
    let mut hub = hub();
    let mut sub = subscription(1);
    sub.trigger = Trigger::AboveU128 {
        threshold: 10,
        hysteresis: 2,
        cooldown_turns: 3,
    };
    configure(&mut hub, vec![sub]);
    for (turn, value) in [11, 12, 9, 8, 11].into_iter().enumerate() {
        let mut s = hub
            .begin_turn(1, 1, turn as u64, DiagnosticOrigin::Live)
            .unwrap();
        s.probe("counter", || value as u64);
        s.finish(true);
    }
    assert_eq!(hub.queued(1).unwrap().0, 2);
}
#[derive(statelessness_macros::Inspect)]
struct Secret {
    public: u128,
    #[inspect(redact)]
    password: String,
}
#[test]
fn privacy_survives_text_jsonl_and_custom_sinks() {
    let mut hub = hub();
    configure(&mut hub, vec![subscription(1)]);
    let value = Secret {
        public: u128::MAX,
        password: "never-leak-this-secret".into(),
    };
    {
        let mut s = hub.begin_turn(1, 1, 0, DiagnosticOrigin::Live).unwrap();
        s.probe("counter", || &value);
        s.finish(true);
    }
    let event = hub.pop(1).unwrap();
    let json = event_json(&event);
    assert!(!json.contains(&value.password));
    assert!(json.contains(&u128::MAX.to_string()));
    let mut text = TextSink { writer: Vec::new() };
    text.emit(&event).unwrap();
    assert!(
        !String::from_utf8(text.writer)
            .unwrap()
            .contains(&value.password)
    );
    let mut jsonl = JsonlSink { writer: Vec::new() };
    jsonl.emit(&event).unwrap();
    assert_eq!(String::from_utf8(jsonl.writer).unwrap(), json + "\n");
    struct Custom(bool);
    impl DiagnosticSink for Custom {
        fn emit(&mut self, event: &DiagnosticEvent) -> std::io::Result<()> {
            self.0 = !event_json(event).contains("never-leak");
            Ok(())
        }
    }
    let mut custom = Custom(false);
    custom.emit(&event).unwrap();
    assert!(custom.0);
}
#[test]
fn unauthorized_projection_is_rejected_before_closure_or_queueing() {
    let mut hub = hub();
    hub.add_sink(
        2,
        SinkPermissions {
            sites: vec!["counter".into()],
            paths: vec![vec![PathSegment::Field("public".into())]],
        },
        SinkLimits::default(),
    )
    .unwrap();
    assert_eq!(
        hub.configure(0, vec![subscription(2)]),
        Err(DiagnosticError::Unauthorized)
    );
    assert_eq!(hub.revision(), 0);
}
#[test]
fn incomplete_turn_and_export_failure_do_not_change_application_result() {
    let mut hub = hub();
    configure(&mut hub, vec![subscription(1)]);
    {
        let mut s = hub.begin_turn(1, 1, 0, DiagnosticOrigin::Live).unwrap();
        s.probe("counter", || 42u64);
    }
    assert!(hub.pop(1).unwrap().incomplete_turn);
    {
        let mut s = hub.begin_turn(1, 1, 1, DiagnosticOrigin::Live).unwrap();
        s.probe("counter", || 43u64);
        s.finish(true);
    }
    struct Broken;
    impl DiagnosticSink for Broken {
        fn emit(&mut self, _: &DiagnosticEvent) -> std::io::Result<()> {
            Err(std::io::Error::other("write failed"))
        }
    }
    assert_eq!(hub.drain_to(1, &mut Broken, 100).unwrap(), 0);
    assert_eq!(hub.health(1).unwrap().exporter_failed, 1);
}
struct ErrorInspect;
impl Inspect for ErrorInspect {
    fn inspect(
        &self,
        _: &[PathSegment],
        _: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        Err(InspectError::PathNotFound)
    }
}
#[test]
fn inspector_errors_are_diagnostic_and_do_not_masquerade_as_successful_values() {
    let mut hub = hub();
    configure(&mut hub, vec![subscription(1)]);
    {
        let mut s = hub.begin_turn(1, 1, 0, DiagnosticOrigin::Live).unwrap();
        s.probe("counter", || ErrorInspect);
        s.finish(true);
    }
    let event = hub.pop(1).unwrap();
    assert!(event.change_unknown);
    assert!(event.payload.is_none());
    assert_eq!(hub.health(1).unwrap().inspection_failed, 1);
}
#[test]
fn macros_have_an_explicit_scoped_sink_and_disabled_build_skips_arguments() {
    let mut hub = hub();
    configure(&mut hub, vec![subscription(1)]);
    let calls = Cell::new(0);
    let mut scope = hub
        .begin_turn(1, 1, 0, DiagnosticOrigin::Simulation)
        .unwrap();
    let _ = &mut scope;
    statelessness_debug::probe!(scope, "counter", || {
        calls.set(calls.get() + 1);
        1u64
    });
    statelessness_debug::marker!(scope, "unused");
    scope.finish(true);
    assert_eq!(
        calls.get(),
        if cfg!(feature = "compiled-out-diagnostics") {
            0
        } else {
            1
        }
    );
}

#[test]
fn optional_sidecar_is_versioned_bounded_and_bound_to_exact_artifact() {
    let trace = stateless::execution::record(
        &stateless::demo::RequestModel::buggy(),
        [],
        Default::default(),
        0,
    )
    .unwrap();
    let manifest = SidecarManifest::for_trace(&trace, 99).unwrap();
    let mut hub = hub();
    configure(&mut hub, vec![subscription(1)]);
    {
        let mut scope = hub
            .begin_turn(1, 99, 0, DiagnosticOrigin::Simulation)
            .unwrap();
        scope.probe("counter", || u128::MAX);
        scope.finish(true);
    }
    let events = vec![hub.pop(1).unwrap()];
    let mut bytes = vec![];
    manifest
        .write_jsonl(&events, &mut bytes, 100, 65536)
        .unwrap();
    let read = SidecarManifest::read_header(&mut std::io::Cursor::new(&bytes)).unwrap();
    assert_eq!(read, manifest);
    read.validate(&trace).unwrap();
    let mut other = trace.clone();
    other.config.strategy = "different".into();
    assert!(read.validate(&other).is_err());
    let mut empty = vec![];
    assert!(manifest.write_jsonl(&events, &mut empty, 100, 1).is_err());
    assert!(empty.is_empty());
    assert!(SidecarManifest::read_header(&mut std::io::Cursor::new(vec![b'x'; 5000])).is_err());
}

#[test]
fn json_paths_and_pages_are_structural_without_rust_debug_parsing() {
    use statelessness_debug::inspect::{InspectQuery, IntegerType, MapKey, PageRequest};
    let path = path_json(&[PathSegment::MapKey(MapKey::Integer {
        kind: IntegerType::U128,
        decimal: u128::MAX.to_string(),
    })]);
    assert!(path.contains("\"kind\":\"integer\",\"type\":\"U128\""));
    assert!(path.contains(&format!("\"decimal\":\"{}\"", u128::MAX)));
    assert!(!path.contains("Integer {"));
    let page = statelessness_debug::inspect::inspect(
        &vec![1u64, 2, 3],
        &InspectQuery {
            page: PageRequest {
                offset: 1,
                limit: 1,
            },
            ..Default::default()
        },
    )
    .unwrap();
    let json = node_json(&page.node);
    assert!(json.contains("\"child_count\":\"3\""));
    assert!(json.contains("\"completeness\":{\"kind\":\"partial\",\"reason\":\"Pagination\"}"));
    assert!(
        json.contains("\"offset\":\"1\",\"returned\":\"1\",\"total\":\"3\",\"next_offset\":\"2\"")
    );
}

#[test]
fn nonnumeric_complete_value_cannot_preserve_threshold_crossing_state() {
    struct Dynamic(bool);
    impl Inspect for Dynamic {
        fn inspect(
            &self,
            path: &[PathSegment],
            context: &mut InspectContext,
        ) -> Result<InspectNode, InspectError> {
            if self.0 {
                11u64.inspect(path, context)
            } else {
                "not numeric".inspect(path, context)
            }
        }
    }
    let mut hub = hub();
    let mut sub = subscription(1);
    sub.trigger = Trigger::AboveU128 {
        threshold: 10,
        hysteresis: 1,
        cooldown_turns: 100,
    };
    configure(&mut hub, vec![sub]);
    for (turn, value) in [true, false, true].into_iter().enumerate() {
        let mut scope = hub
            .begin_turn(1, 1, turn as u64, DiagnosticOrigin::Test)
            .unwrap();
        scope.probe("counter", || Dynamic(value));
        scope.finish(true);
    }
    assert!(hub.pop(1).unwrap().baseline);
    let rebased = hub.pop(1).unwrap();
    assert_eq!(
        rebased.baseline_reason,
        Some(ProbeBaselineReason::TypeUnavailable)
    );
    assert!(rebased.change_unknown);
    assert!(hub.pop(1).is_none());
}

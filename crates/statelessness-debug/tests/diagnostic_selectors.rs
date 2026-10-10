//! Adversarial qualification of host-declared scoped capture selection.
use statelessness_debug::diagnostic::*;
use statelessness_debug::inspect::{
    FieldView, Inspect, InspectContext, InspectError, InspectNode, PathSegment,
};
use std::cell::Cell;

#[derive(Clone, Copy, Debug)]
enum Label {
    Model,
    Machine,
    Entity,
    Correlation,
    Input,
    Output,
    Property,
    Effect,
    Source,
}
const LABELS: [Label; 9] = [
    Label::Model,
    Label::Machine,
    Label::Entity,
    Label::Correlation,
    Label::Input,
    Label::Output,
    Label::Property,
    Label::Effect,
    Label::Source,
];
fn selector_label(s: &mut ScopeSelector, field: Label, value: &str) {
    let value = Some(value.to_owned());
    match field {
        Label::Model => s.model = value,
        Label::Machine => s.machine = value,
        Label::Entity => s.entity = value,
        Label::Correlation => s.correlation = value,
        Label::Input => s.input_variant = value,
        Label::Output => s.output_variant = value,
        Label::Property => s.property = value,
        Label::Effect => s.effect_kind = value,
        Label::Source => s.source_site = value,
    }
}
fn context_label<'a>(c: &mut CaptureContext<'a>, field: Label, value: Option<&'a str>) {
    match field {
        Label::Model => c.model = value,
        Label::Machine => c.machine = value,
        Label::Entity => c.entity = value,
        Label::Correlation => c.correlation = value,
        Label::Input => c.input_variant = value,
        Label::Output => c.output_variant = value,
        Label::Property => c.property = value,
        Label::Effect => c.effect_kind = value,
        Label::Source => c.source_site = value,
    }
}
fn selected(selector: ScopeSelector) -> SelectedSubscription {
    SelectedSubscription {
        subscription: Subscription {
            sink: 1,
            site: "probe".into(),
            kind: SiteKind::Probe,
            path: vec![],
            trigger: Trigger::Every,
            sample_every: 1,
            minimum_severity: Severity::Debug,
        },
        selector,
    }
}
fn empty_hub(limits: DiagnosticLimits) -> DiagnosticHub {
    let mut hub = DiagnosticHub::new(limits);
    hub.add_sink(1, SinkPermissions::local_all(), SinkLimits::default())
        .unwrap();
    hub.register_producer(1, 7).unwrap();
    hub.register_producer(2, 8).unwrap();
    hub
}
fn hub(selector: ScopeSelector) -> DiagnosticHub {
    let mut hub = empty_hub(DiagnosticLimits::default());
    hub.configure_selected(0, vec![selected(selector)]).unwrap();
    hub.acknowledge(1, 1, 0).unwrap();
    hub.acknowledge(2, 1, 0).unwrap();
    hub
}
struct Counted<'a>(&'a Cell<usize>);
impl Inspect for Counted<'_> {
    fn inspect(
        &self,
        path: &[PathSegment],
        cx: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        self.0.set(self.0.get() + 1);
        42u64.inspect(path, cx)
    }
}
fn capture(
    hub: &mut DiagnosticHub,
    producer: u64,
    turn: u64,
    origin: DiagnosticOrigin,
    context: CaptureContext<'_>,
) -> (usize, usize, Vec<DiagnosticEvent>) {
    let calls = Cell::new(0);
    let inspections = Cell::new(0);
    {
        let mut scope = hub
            .begin_turn_with_context(producer, 1, turn, origin, context)
            .unwrap();
        scope.probe("probe", || {
            calls.set(calls.get() + 1);
            Counted(&inspections)
        });
        scope.finish(true);
    }
    let mut events = vec![];
    while let Some(event) = hub.pop(1) {
        events.push(event);
    }
    (calls.get(), inspections.get(), events)
}
#[test]
fn every_declared_label_filters_match_mismatch_and_unknown_before_closure() {
    for field in LABELS {
        for actual in [Some("chosen"), Some("other"), None] {
            let mut selector = ScopeSelector::default();
            selector_label(&mut selector, field, "chosen");
            let mut hub = hub(selector);
            let mut context = CaptureContext::default();
            context_label(&mut context, field, actual);
            let (calls, inspections, events) =
                capture(&mut hub, 1, 0, DiagnosticOrigin::Live, context);
            let expected = usize::from(actual == Some("chosen"));
            assert_eq!(
                (calls, inspections, events.len()),
                (expected, expected, expected),
                "{field:?}/{actual:?}"
            );
        }
    }
}
#[test]
fn producer_and_origin_selectors_are_independent_exact_filters() {
    for producer in [1, 2] {
        for origin in [DiagnosticOrigin::Live, DiagnosticOrigin::Replay] {
            let mut hub = hub(ScopeSelector {
                producer: Some(1),
                origin: Some(DiagnosticOrigin::Live),
                ..Default::default()
            });
            let (calls, inspections, events) =
                capture(&mut hub, producer, 0, origin, CaptureContext::default());
            let expected = usize::from(producer == 1 && origin == DiagnosticOrigin::Live);
            assert_eq!(
                (calls, inspections, events.len()),
                (expected, expected, expected)
            );
        }
    }
}
#[test]
fn property_outcome_selection_does_not_treat_unknown_as_nonfailure() {
    for expected in [true, false] {
        for actual in [Some(true), Some(false), None] {
            let mut hub = hub(ScopeSelector {
                property: Some("safe-release".into()),
                property_failed: Some(expected),
                ..Default::default()
            });
            let (calls, inspections, events) = capture(
                &mut hub,
                1,
                0,
                DiagnosticOrigin::Live,
                CaptureContext {
                    property: Some("safe-release"),
                    property_failed: actual,
                    ..Default::default()
                },
            );
            let n = usize::from(actual == Some(expected));
            assert_eq!((calls, inspections, events.len()), (n, n, n));
        }
    }
    let mut hub = hub(ScopeSelector {
        property: Some("safe-release".into()),
        property_failed: Some(true),
        ..Default::default()
    });
    assert_eq!(
        capture(
            &mut hub,
            1,
            0,
            DiagnosticOrigin::Live,
            CaptureContext {
                property: Some("different-property"),
                property_failed: Some(true),
                ..Default::default()
            }
        )
        .0,
        0
    );
}
#[test]
fn combined_scope_is_conjunctive_and_context_is_not_implicitly_exported() {
    let mut selector = ScopeSelector {
        producer: Some(1),
        origin: Some(DiagnosticOrigin::Live),
        property_failed: Some(true),
        ..Default::default()
    };
    let mut context = CaptureContext {
        property_failed: Some(true),
        ..Default::default()
    };
    for field in LABELS {
        selector_label(&mut selector, field, "private-routing-label");
        context_label(&mut context, field, Some("private-routing-label"));
    }
    for absent in LABELS {
        let mut hub = hub(selector.clone());
        let mut missing = context;
        context_label(&mut missing, absent, None);
        assert_eq!(
            capture(&mut hub, 1, 0, DiagnosticOrigin::Live, missing).0,
            0,
            "{absent:?}"
        );
    }
    let mut hub = hub(selector);
    let (calls, inspections, events) = capture(&mut hub, 1, 0, DiagnosticOrigin::Live, context);
    assert_eq!((calls, inspections, events.len()), (1, 1, 1));
    assert!(!event_json(&events[0]).contains("private-routing-label"));
}
#[test]
fn context_byte_limits_reject_before_advancing_producer_boundary() {
    let too_large = "é".repeat(129);
    let accepted = "é".repeat(128);
    for field in LABELS {
        let mut hub = hub(ScopeSelector::default());
        let mut context = CaptureContext::default();
        context_label(&mut context, field, Some(&too_large));
        assert_eq!(
            hub.begin_turn_with_context(1, 1, 0, DiagnosticOrigin::Live, context)
                .err(),
            Some(DiagnosticError::InvalidConfig)
        );
        let mut context = CaptureContext::default();
        context_label(&mut context, field, Some(&accepted));
        assert_eq!(
            capture(&mut hub, 1, 0, DiagnosticOrigin::Live, context).0,
            1,
            "invalid {field:?} must not consume boundary"
        );
    }
}
#[test]
fn invalid_selector_labels_and_aggregate_metadata_budget_do_not_change_revision() {
    for field in LABELS {
        for invalid in [String::new(), "x".repeat(257)] {
            let mut hub = empty_hub(DiagnosticLimits::default());
            let mut selector = ScopeSelector::default();
            selector_label(&mut selector, field, &invalid);
            assert_eq!(
                hub.configure_selected(0, vec![selected(selector)]),
                Err(DiagnosticError::InvalidConfig)
            );
            assert_eq!(hub.revision(), 0);
        }
    }
    let mut hub = empty_hub(DiagnosticLimits {
        config_bytes: 1024,
        ..Default::default()
    });
    let mut selector = ScopeSelector::default();
    for field in LABELS {
        selector_label(&mut selector, field, &"x".repeat(256));
    }
    assert_eq!(
        hub.configure_selected(0, vec![selected(selector)]),
        Err(DiagnosticError::Capacity)
    );
    assert_eq!(hub.revision(), 0);
}
#[test]
fn scoped_configuration_cannot_expand_sink_projection_permissions() {
    let mut hub = empty_hub(DiagnosticLimits::default());
    hub.add_sink(
        3,
        SinkPermissions {
            sites: vec!["probe".into()],
            paths: vec![vec![PathSegment::Field("public".into())]],
        },
        SinkLimits::default(),
    )
    .unwrap();
    let mut config = selected(ScopeSelector {
        producer: Some(1),
        model: Some("allowed-model".into()),
        ..Default::default()
    });
    config.subscription.sink = 3;
    assert_eq!(
        hub.configure_selected(0, vec![config]),
        Err(DiagnosticError::Unauthorized)
    );
    assert_eq!(hub.revision(), 0);
    assert_eq!(
        capture(
            &mut hub,
            1,
            0,
            DiagnosticOrigin::Live,
            CaptureContext {
                model: Some("allowed-model"),
                ..Default::default()
            }
        )
        .0,
        0
    );
}
#[test]
fn configuration_is_stale_safe_and_applied_per_producer_at_explicit_boundaries() {
    let mut hub = hub(ScopeSelector {
        machine: Some("old".into()),
        ..Default::default()
    });
    assert_eq!(
        hub.configure_selected(0, vec![selected(ScopeSelector::default())]),
        Err(DiagnosticError::StaleRevision)
    );
    assert_eq!(
        hub.acknowledge(1, 0, 0),
        Err(DiagnosticError::StaleRevision)
    );
    let ack = hub
        .configure_selected(
            1,
            vec![selected(ScopeSelector {
                machine: Some("new".into()),
                ..Default::default()
            })],
        )
        .unwrap();
    assert_eq!(ack.pending_producers, vec![1, 2]);
    for producer in [1, 2] {
        let events = capture(
            &mut hub,
            producer,
            0,
            DiagnosticOrigin::Live,
            CaptureContext {
                machine: Some("old"),
                ..Default::default()
            },
        )
        .2;
        assert_eq!(events[0].capture_revision, 1);
    }
    hub.acknowledge(1, 2, 2).unwrap();
    assert_eq!(
        hub.begin_turn_with_context(
            1,
            1,
            1,
            DiagnosticOrigin::Live,
            CaptureContext {
                machine: Some("new"),
                ..Default::default()
            }
        )
        .err(),
        Some(DiagnosticError::PendingConfiguration)
    );
    let events = capture(
        &mut hub,
        1,
        2,
        DiagnosticOrigin::Live,
        CaptureContext {
            machine: Some("new"),
            ..Default::default()
        },
    )
    .2;
    assert_eq!(events[0].capture_revision, 2);
    let events = capture(
        &mut hub,
        2,
        1,
        DiagnosticOrigin::Live,
        CaptureContext {
            machine: Some("old"),
            ..Default::default()
        },
    )
    .2;
    assert_eq!(events[0].capture_revision, 1);
    let statuses = hub.producer_status();
    assert_eq!(
        (
            statuses[0].capture_revision,
            statuses[0].effective_transition
        ),
        (2, 2)
    );
    assert_eq!(
        (
            statuses[1].capture_revision,
            statuses[1].effective_transition
        ),
        (1, 0)
    );
}
#[test]
fn selector_gaps_invalidate_change_baselines_without_evaluating_filtered_values() {
    let mut hub = empty_hub(DiagnosticLimits::default());
    let mut subscription = selected(ScopeSelector {
        machine: Some("chosen".into()),
        ..Default::default()
    });
    subscription.subscription.trigger = Trigger::Changed;
    hub.configure_selected(0, vec![subscription]).unwrap();
    hub.acknowledge(1, 1, 0).unwrap();
    assert_eq!(
        capture(
            &mut hub,
            1,
            0,
            DiagnosticOrigin::Live,
            CaptureContext {
                machine: Some("chosen"),
                ..Default::default()
            }
        )
        .2
        .len(),
        1
    );
    assert_eq!(
        capture(
            &mut hub,
            1,
            1,
            DiagnosticOrigin::Live,
            CaptureContext {
                machine: Some("other"),
                ..Default::default()
            }
        )
        .0,
        0
    );
    let events = capture(
        &mut hub,
        1,
        2,
        DiagnosticOrigin::Live,
        CaptureContext {
            machine: Some("chosen"),
            ..Default::default()
        },
    )
    .2;
    assert_eq!(events.len(), 1);
    assert!(events[0].baseline && events[0].change_unknown);
    assert_eq!(
        events[0].baseline_reason,
        Some(ProbeBaselineReason::SelectorGap)
    );
}
#[test]
fn multiple_matching_scopes_share_closure_and_authorized_projection() {
    let mut hub = empty_hub(DiagnosticLimits::default());
    hub.add_sink(3, SinkPermissions::local_all(), SinkLimits::default())
        .unwrap();
    let first = selected(ScopeSelector {
        model: Some("M".into()),
        ..Default::default()
    });
    let mut second = selected(ScopeSelector {
        machine: Some("A".into()),
        ..Default::default()
    });
    second.subscription.sink = 3;
    hub.configure_selected(0, vec![first, second]).unwrap();
    hub.acknowledge(1, 1, 0).unwrap();
    let (calls, inspections, events) = capture(
        &mut hub,
        1,
        0,
        DiagnosticOrigin::Live,
        CaptureContext {
            model: Some("M"),
            machine: Some("A"),
            ..Default::default()
        },
    );
    assert_eq!((calls, inspections, events.len()), (1, 1, 1));
    assert!(hub.pop(3).is_some());
}
struct ScopedSecret;
impl Inspect for ScopedSecret {
    fn inspect(
        &self,
        path: &[PathSegment],
        cx: &mut InspectContext,
    ) -> Result<InspectNode, InspectError> {
        cx.object(
            path,
            "ScopedSecret",
            &[
                FieldView::new("public", "public", &42u64),
                FieldView::redacted("secret", "secret"),
            ],
        )
    }
}
#[test]
fn scope_filters_preserve_field_redaction_in_all_sinks() {
    let mut hub = hub(ScopeSelector {
        entity: Some("private-routing-label".into()),
        ..Default::default()
    });
    {
        let mut scope = hub
            .begin_turn_with_context(
                1,
                1,
                0,
                DiagnosticOrigin::Live,
                CaptureContext {
                    entity: Some("private-routing-label"),
                    ..Default::default()
                },
            )
            .unwrap();
        scope.probe("probe", || ScopedSecret);
        scope.finish(true);
    }
    let event = hub.pop(1).unwrap();
    let json = event_json(&event);
    assert!(json.contains("redacted"));
    assert!(!json.contains("private-routing-label"));
    let mut text = TextSink { writer: vec![] };
    text.emit(&event).unwrap();
    assert!(
        !String::from_utf8(text.writer)
            .unwrap()
            .contains("private-routing-label")
    );
}

#[test]
fn scope_selection_does_not_bypass_site_kind_or_severity_filters() {
    for (site, kind, severity) in [
        ("wrong", SiteKind::Probe, Severity::Debug),
        ("probe", SiteKind::Marker, Severity::Debug),
        ("probe", SiteKind::Probe, Severity::Error),
    ] {
        let mut hub = empty_hub(DiagnosticLimits::default());
        let mut config = selected(ScopeSelector {
            machine: Some("chosen".into()),
            ..Default::default()
        });
        config.subscription.site = site.into();
        config.subscription.kind = kind;
        config.subscription.minimum_severity = severity;
        hub.configure_selected(0, vec![config]).unwrap();
        hub.acknowledge(1, 1, 0).unwrap();
        assert_eq!(
            capture(
                &mut hub,
                1,
                0,
                DiagnosticOrigin::Live,
                CaptureContext {
                    machine: Some("chosen"),
                    ..Default::default()
                }
            )
            .0,
            0
        );
    }
    let mut hub = empty_hub(DiagnosticLimits::default());
    let mut config = selected(ScopeSelector {
        machine: Some("chosen".into()),
        ..Default::default()
    });
    config.subscription.kind = SiteKind::Marker;
    hub.configure_selected(0, vec![config]).unwrap();
    hub.acknowledge(1, 1, 0).unwrap();
    {
        let mut scope = hub
            .begin_turn_with_context(
                1,
                1,
                0,
                DiagnosticOrigin::Live,
                CaptureContext {
                    machine: Some("chosen"),
                    ..Default::default()
                },
            )
            .unwrap();
        scope.marker("probe");
        scope.finish(true);
    }
    assert_eq!(hub.pop(1).unwrap().kind, SiteKind::Marker);
}
#[test]
fn selector_gap_resets_threshold_hysteresis_and_cooldown_as_unknown() {
    let mut hub = empty_hub(DiagnosticLimits::default());
    let mut config = selected(ScopeSelector {
        machine: Some("chosen".into()),
        ..Default::default()
    });
    config.subscription.trigger = Trigger::AboveU128 {
        threshold: 10,
        hysteresis: 2,
        cooldown_turns: 1000,
    };
    hub.configure_selected(0, vec![config]).unwrap();
    hub.acknowledge(1, 1, 0).unwrap();
    assert_eq!(
        capture(
            &mut hub,
            1,
            0,
            DiagnosticOrigin::Live,
            CaptureContext {
                machine: Some("chosen"),
                ..Default::default()
            }
        )
        .2
        .len(),
        1
    );
    assert_eq!(
        capture(
            &mut hub,
            1,
            1,
            DiagnosticOrigin::Live,
            CaptureContext {
                machine: Some("other"),
                ..Default::default()
            }
        )
        .0,
        0
    );
    let events = capture(
        &mut hub,
        1,
        2,
        DiagnosticOrigin::Live,
        CaptureContext {
            machine: Some("chosen"),
            ..Default::default()
        },
    )
    .2;
    assert_eq!(events.len(), 1);
    assert_eq!(
        events[0].baseline_reason,
        Some(ProbeBaselineReason::SelectorGap)
    );
    assert!(events[0].change_unknown);
}

use stateless::demo::{Input, RequestModel};
use stateless::evidence::{Artifact, EvidenceLimits, Provenance, TraceV2};
use stateless::execution::{ReplayOptions, record};
use stateless::trace::RunConfig;
use statelessness_debug::dashboard::*;
use statelessness_debug::effects::*;
use statelessness_debug::evidence::EvidenceViewer;
use statelessness_debug::metrics::*;

fn store() -> MetricsStore {
    MetricsStore::new(
        1,
        None,
        LabelCatalog::default(),
        HistogramSchema::default(),
        MetricLimits::default(),
        GaugeScope::Unknown,
    )
    .unwrap()
}
fn evidence(bug: bool) -> TraceV2 {
    let model = RequestModel { inject_bug: bug };
    let trace = record(
        &model,
        [Input::Start, Input::Cancel, Input::Complete(1)],
        RunConfig::default(),
        10,
    )
    .unwrap();
    TraceV2::verify_upgrade(
        &model,
        trace,
        Provenance {
            run: "7".into(),
            epoch: 4,
            ..Default::default()
        },
        0,
        ReplayOptions::default(),
        &EvidenceLimits::default(),
    )
    .unwrap()
}
#[test]
fn viewer_derives_failure_phase_without_model_or_effect_execution() {
    let value = evidence(true);
    let viewer =
        EvidenceViewer::new(Artifact::V2(value.clone()), &EvidenceLimits::default()).unwrap();
    assert_eq!(
        viewer.failure(3, 2).unwrap().phase,
        stateless::explore::CheckPhase::Transition
    );
    assert_eq!(
        viewer.boundary(3).unwrap().before,
        value.trace.steps[1].post_state
    );
    assert_eq!(
        viewer.boundary(3).unwrap().outputs,
        value.trace.steps[2].outputs
    );
    assert!(viewer.failure(1, 0).is_err());
    assert!(viewer.boundary(4).is_none());
    let legacy =
        EvidenceViewer::new(Artifact::V1(value.trace), &EvidenceLimits::default()).unwrap();
    assert!(legacy.failure(3, 2).is_err());
}
#[test]
fn missing_duration_stays_unknown_and_health_faults_are_visible() {
    let mut store = store();
    let health = TelemetryHealth {
        clock_failures: 1,
        destructor_hook_losses: 2,
        ..TelemetryHealth::default()
    };
    let dashboard =
        Dashboard::from_snapshot(&store.snapshot(None), &health, &[Labels::default()], 100);
    assert!(!dashboard.observation_complete);
    assert!(
        dashboard
            .rows
            .iter()
            .all(|r| r.value.is_none() && r.p50.is_none())
    );
    let json = dashboard.to_json(50_000).unwrap();
    assert!(json.contains("\"value\":null"));
    assert!(json.contains("\"clock_failures\":1"));
    assert!(json.contains("\"destructor_hook_losses\":2"));
    assert!(json.contains("\"start\":null"));
    assert!(dashboard.to_json(32).is_err());
}
#[test]
fn report_limits_never_claim_full_visibility() {
    let mut store = store();
    let dashboard = Dashboard::from_snapshot(
        &store.snapshot(None),
        &TelemetryHealth::default(),
        &[Labels::default()],
        2,
    );
    assert_eq!(dashboard.rows.len(), 2);
    assert!(dashboard.truncated);
    assert!(!dashboard.observation_complete);
}
#[test]
fn host_correlation_requires_exact_run_epoch_machine_and_output() {
    let mut value = evidence(false);
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
    timeline.origin.output_index = 1;
    assert!(matches!(
        correlate(&value, &timeline),
        Correlation::Conflict(_)
    ));
    timeline.origin.output_index = 0;
    timeline.origin.machine = 4;
    assert_eq!(
        correlate(&value, &timeline),
        Correlation::Conflict("machine")
    );
    timeline.origin.machine = 3;
    timeline.effect.epoch = 5;
    assert_eq!(
        correlate(&value, &timeline),
        Correlation::Conflict("run or epoch")
    );
    timeline.effect.epoch = 4;
    value.checkpoint_after = 1;
    assert!(matches!(
        correlate(&value, &timeline),
        Correlation::BeforeCheckpoint { .. }
    ));
}

#[test]
fn dashboard_exports_censored_pause_and_both_metric_health_endpoints() {
    let mut store = store();
    let mut snapshot = store.snapshot(None);
    snapshot.health.invalid_labels = 2;
    snapshot.health.arithmetic_errors = 3;
    snapshot.health.imported_ignored = 4;
    snapshot.health.snapshots_evicted = 5;
    snapshot.health.snapshot_rejections = 6;
    let health = TelemetryHealth {
        abandoned: 7,
        pause_interval_evictions: 8,
        duplicate_deliveries: 9,
        metric: MetricHealth {
            omitted_measurements: 10,
            ..MetricHealth::default()
        },
        ..TelemetryHealth::default()
    };
    let dashboard = Dashboard::from_snapshot(&snapshot, &health, &[Labels::default()], 100);
    assert!(!dashboard.observation_complete);
    let json = dashboard.to_json(50_000).unwrap();
    for field in [
        "\"invalid_labels\":2",
        "\"arithmetic_errors\":3",
        "\"imported_ignored\":4",
        "\"snapshots_evicted\":5",
        "\"snapshot_rejections\":6",
        "\"abandoned\":7",
        "\"pause_interval_evictions\":8",
        "\"duplicate_deliveries\":9",
        "\"omitted_measurements\":10",
        "\"host_metric_health\":",
    ] {
        assert!(json.contains(field), "missing {field}");
    }
    let health = TelemetryHealth {
        pause_interval_evictions: 1,
        ..TelemetryHealth::default()
    };
    assert!(
        !Dashboard::from_snapshot(&store.snapshot(None), &health, &[Labels::default()], 100)
            .observation_complete
    );
}

#[test]
fn v2_minimization_rejects_phase_drift_outside_selected_failure() {
    let mut value = evidence(true);
    assert_ne!(value.steps[0].state_check_count, 0);
    value.steps[0].state_check_count = 0;
    let viewer = EvidenceViewer::new(Artifact::V2(value), &EvidenceLimits::default()).unwrap();
    let error = viewer
        .minimize(
            &RequestModel::buggy(),
            3,
            2,
            Default::default(),
            Default::default(),
        )
        .unwrap_err();
    assert!(error.to_string().contains("phase evidence"));
}

#[test]
fn v2_minimization_counts_phase_verification_and_honors_cancellation() {
    use stateless::explore::{RunLimits, ShrinkLimits};
    use std::sync::atomic::AtomicBool;
    let viewer =
        EvidenceViewer::new(Artifact::V2(evidence(true)), &EvidenceLimits::default()).unwrap();
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
    assert!(report.replayed_transitions <= 9);
    assert!(report.replayed_transitions >= 6);
    let cancelled = AtomicBool::new(true);
    let error = viewer
        .minimize(
            &RequestModel::buggy(),
            3,
            2,
            Default::default(),
            ShrinkLimits {
                control: RunLimits {
                    cancellation: Some(&cancelled),
                    ..Default::default()
                },
                ..Default::default()
            },
        )
        .unwrap_err();
    assert!(error.to_string().contains("cancelled"));
    let error = viewer
        .minimize(
            &RequestModel::buggy(),
            3,
            2,
            Default::default(),
            ShrinkLimits {
                max_replayed_transitions: Some(2),
                ..Default::default()
            },
        )
        .unwrap_err();
    assert!(error.to_string().contains("budget"));
}

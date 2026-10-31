use stateless::Model;
use stateless::demo::{Input, RequestModel};
use stateless::execution::{ReplayOptions, ReplayOutcome, record};
use stateless::explore::{CheckPhase, PropertyFailure, ShrinkConfig, ShrinkLimits};
use stateless::monitor::RecorderOptions;
use stateless::trace::{RunConfig, Termination};
use statelessness_debug::workbench::{TraceViewer, artifact_identity, minimize_with_policy};
use statelessness_debug::{DebugSession, InputPolicy, SessionLimits};
fn failure() -> stateless::trace::Trace {
    record(
        &RequestModel::buggy(),
        [Input::Start, Input::Cancel, Input::Complete(1)],
        RunConfig::default(),
        3,
    )
    .unwrap()
}
#[test]
fn viewing_is_unverified_and_outputs_are_encoded_without_decoder() {
    let mut view = TraceViewer::new(failure()).unwrap();
    view.seek(3).unwrap();
    let state = view.state_bytes(1);
    assert!(!state.complete);
    assert_eq!(state.provenance, "stored-exact-encoding");
    let (outputs, complete) = view.outputs(0, 1, 1).unwrap();
    assert!(complete);
    assert!(!outputs[0].complete);
    assert_eq!(view.retained_range(), (0, 3));
    assert!(view.seek(4).is_err());
}
#[test]
fn build_comparison_is_explicit_and_reports_first_divergence() {
    let view = TraceViewer::new(failure()).unwrap();
    assert!(matches!(
        view.verify(&RequestModel::fixed(), ReplayOptions::default())
            .unwrap()
            .outcome,
        ReplayOutcome::Incompatible { .. }
    ));
    let report = view
        .verify(
            &RequestModel::fixed(),
            ReplayOptions {
                allow_build_mismatch: true,
            },
        )
        .unwrap();
    assert_eq!(
        report.outcome,
        ReplayOutcome::Diverged {
            step: Some(3),
            field: "outputs"
        }
    );
    assert_eq!(report.steps_verified, 2);
    assert!(!report.failure_reproduced);
}
#[test]
fn branch_is_independent_verified_and_preserves_parent() {
    let view = TraceViewer::new(failure()).unwrap();
    let before = artifact_identity(view.trace()).unwrap();
    let mut branch = view
        .fork_verified(
            "branch",
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
    branch.provenance.validate_parent(view.trace()).unwrap();
    branch.session.step(0, Input::Complete(1)).unwrap();
    assert!(!branch.session.state().ready);
    assert_eq!(artifact_identity(view.trace()).unwrap(), before);
    assert!(branch.provenance.parent_prefix_verified);
    assert_ne!(
        branch.provenance.parent_metadata.build,
        branch.provenance.model_metadata.build
    );
    let exported = branch.session.export_trace().unwrap();
    assert!(
        exported
            .config
            .parameters
            .iter()
            .any(|(key, _)| key == "stateless.debug.parent.digest")
    );
    assert!(
        view.fork_verified(
            "bad",
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
fn sidecar_and_exact_continuation_mismatches_are_rejected() {
    let trace = failure();
    let view = TraceViewer::new(trace.clone()).unwrap();
    let branch = view
        .fork_verified(
            "b",
            RequestModel::buggy(),
            1,
            ReplayOptions::default(),
            InputPolicy::enumerated(),
            SessionLimits::default(),
            RecorderOptions::default(),
        )
        .unwrap();
    let mut other = trace.clone();
    other.config.strategy = "changed".into();
    assert!(branch.provenance.validate_parent(&other).is_err());
    let mut continued = trace.clone();
    continued.steps.push(trace.steps[0].clone());
    assert!(TraceViewer::new(continued).is_err());
}
#[test]
fn minimization_starts_from_saved_midrun_checkpoint() {
    let model = RequestModel::buggy();
    let start = model.initial_state().unwrap();
    let checkpoint = model.step(&start, &Input::Start).unwrap().state;
    let mut s = DebugSession::recording_from_state(
        "midrun",
        model,
        checkpoint,
        InputPolicy::enumerated(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions::default(),
    )
    .unwrap();
    s.step(0, Input::Cancel).unwrap();
    s.step(1, Input::Complete(1)).unwrap();
    let trace = s.export_trace().unwrap();
    assert_eq!(trace.termination, Termination::PropertyFailed);
    let target = PropertyFailure {
        phase: CheckPhase::State,
        check: trace.steps[1]
            .checks
            .iter()
            .find(|c| c.id == "ready_requires_active")
            .unwrap()
            .clone(),
    };
    let report = minimize_with_policy(
        s.model(),
        &trace,
        &InputPolicy::enumerated(),
        s.limits().max_candidates,
        target,
        ShrinkConfig { max_attempts: 50 },
        ShrinkLimits {
            max_replayed_transitions: Some(100),
            ..Default::default()
        },
    )
    .unwrap();
    assert!(report.validated_original);
    assert_eq!(
        report.minimized.inputs,
        vec![Input::Cancel, Input::Complete(1)]
    );
}

#[test]
fn fork_requires_unambiguous_complete_environment_provenance() {
    let cases: &[(&[(&str, &str)], &str)] = &[
        (
            &[
                ("stateless.debug.environment", "enumerated-domain"),
                ("stateless.debug.environment", "enumerated-domain"),
                ("stateless.debug.origin", "simulation"),
            ],
            "ambiguous",
        ),
        (
            &[
                ("stateless.debug.environment", "enumerated-domain"),
                ("stateless.debug.environment", "other"),
                ("stateless.debug.origin", "simulation"),
            ],
            "ambiguous",
        ),
        (
            &[
                ("stateless.debug.environment", "enumerated-domain"),
                ("stateless.debug.origin", "simulation"),
                ("stateless.debug.origin", "out-of-domain-injection"),
            ],
            "ambiguous",
        ),
        (
            &[("stateless.debug.environment", "enumerated-domain")],
            "incomplete",
        ),
        (&[("stateless.debug.origin", "simulation")], "incomplete"),
        (
            &[
                ("stateless.debug.environment", "enumerated-domain"),
                ("stateless.debug.origin", "unknown"),
            ],
            "unknown",
        ),
        (
            &[
                ("stateless.debug.environment", "enumerated-domain"),
                ("stateless.debug.origin", "out-of-domain-injection"),
            ],
            "differs",
        ),
    ];
    for (parameters, message) in cases {
        let mut trace = failure();
        trace
            .config
            .parameters
            .extend(parameters.iter().map(|(k, v)| ((*k).into(), (*v).into())));
        let view = TraceViewer::new(trace).unwrap();
        let error = match view.fork_verified(
            "branch",
            RequestModel::fixed(),
            1,
            ReplayOptions::default(),
            InputPolicy::enumerated(),
            SessionLimits::default(),
            RecorderOptions::default(),
        ) {
            Ok(_) => panic!("invalid provenance accepted"),
            Err(error) => error,
        };
        assert!(error.to_string().contains(message), "{error}");
        // Malformed provenance is rejected before the deliberately mismatched
        // model reaches replay, even with explicit unrestricted authority.
        if *message != "differs" {
            let error = match view.fork_verified(
                "branch",
                RequestModel::fixed(),
                1,
                ReplayOptions::default(),
                InputPolicy::unrestricted(),
                SessionLimits::default(),
                RecorderOptions::default(),
            ) {
                Ok(_) => panic!("ambiguous provenance accepted"),
                Err(error) => error,
            };
            assert!(error.to_string().contains(message), "{error}");
        }
    }
    let mut trace = failure();
    trace.config.parameters.extend([
        (
            "stateless.debug.environment".into(),
            "enumerated-domain".into(),
        ),
        ("stateless.debug.origin".into(), "simulation".into()),
    ]);
    let view = TraceViewer::new(trace).unwrap();
    for policy in [InputPolicy::enumerated(), InputPolicy::unrestricted()] {
        assert!(
            view.fork_verified(
                "branch",
                RequestModel::buggy(),
                1,
                ReplayOptions::default(),
                policy,
                SessionLimits::default(),
                RecorderOptions::default()
            )
            .is_ok()
        );
    }
}

#[test]
fn duplicate_retained_checkpoint_is_rejected() {
    for second in ["5", "6", "invalid"] {
        let mut trace = failure();
        trace.config.parameters.push((
            "stateless.monitor.checkpoint_after_sequence".into(),
            "5".into(),
        ));
        trace.config.parameters.push((
            "stateless.monitor.checkpoint_after_sequence".into(),
            second.into(),
        ));
        assert!(
            TraceViewer::new(trace).is_err(),
            "accepted duplicate {second}"
        );
    }
    let mut trace = failure();
    trace.config.parameters.push((
        "stateless.monitor.checkpoint_after_sequence".into(),
        "5".into(),
    ));
    assert!(TraceViewer::new(trace).is_ok());
}

#[test]
fn retained_range_includes_origin_and_rejects_invalid_provenance() {
    let origin = "stateless.monitor.sequence_origin";
    let checkpoint = "stateless.monitor.checkpoint_after_sequence";
    let mut trace = failure();
    trace.config.parameters.extend([
        (origin.into(), "20".into()),
        (checkpoint.into(), "5".into()),
    ]);
    assert_eq!(
        TraceViewer::new(trace.clone()).unwrap().retained_range(),
        (25, 28)
    );
    let mut duplicate = trace.clone();
    duplicate
        .config
        .parameters
        .push((origin.into(), "20".into()));
    assert!(TraceViewer::new(duplicate).is_err());
    let mut invalid = trace.clone();
    invalid
        .config
        .parameters
        .iter_mut()
        .find(|(k, _)| k == origin)
        .unwrap()
        .1 = "invalid".into();
    assert!(TraceViewer::new(invalid).is_err());
    let mut overflow = trace;
    overflow
        .config
        .parameters
        .iter_mut()
        .find(|(k, _)| k == origin)
        .unwrap()
        .1 = u64::MAX.to_string();
    assert!(TraceViewer::new(overflow).is_err());
}

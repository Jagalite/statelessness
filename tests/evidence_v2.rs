use stateless::Model;
use stateless::demo::{Input, RequestModel};
use stateless::evidence::*;
use stateless::execution::{CheckPolicy, ReplayOptions, check_initial, check_turn, record};
use stateless::explore::CheckPhase;
use stateless::monitor::RecorderOptions;
use stateless::trace::{RunConfig, Termination};
use stateless::value_codec::TraceEncode;

fn sample() -> TraceV2 {
    let model = RequestModel::fixed();
    let trace = record(
        &model,
        [Input::Start, Input::Complete(1)],
        RunConfig::default(),
        10,
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
fn exact_v1_compatibility_and_persisted_phase_boundaries() {
    let evidence = sample();
    assert_eq!(evidence.check_phase(0, 0), Some(CheckPhase::InitialState));
    assert_eq!(evidence.check_phase(1, 1), Some(CheckPhase::State));
    assert_eq!(evidence.check_phase(1, 2), Some(CheckPhase::Transition));
    let mut bytes = Vec::new();
    evidence
        .write(&mut bytes, &EvidenceLimits::default())
        .unwrap();
    assert_eq!(
        Artifact::read(bytes.as_slice(), &EvidenceLimits::default()).unwrap(),
        Artifact::V2(evidence.clone())
    );
    bytes.clear();
    evidence.trace.write_to(&mut bytes).unwrap();
    let legacy = Artifact::read(bytes.as_slice(), &EvidenceLimits::default()).unwrap();
    assert_eq!(legacy.check_phase(1, 2), None);
    assert_eq!(legacy.trace(), &evidence.trace);
}

#[test]
fn hostile_envelope_and_metadata_fail_closed() {
    let evidence = sample();
    let limits = EvidenceLimits::default();
    let mut bytes = Vec::new();
    evidence.write(&mut bytes, &limits).unwrap();
    for end in 0..bytes.len() {
        assert!(
            Artifact::read(&bytes[..end], &limits).is_err(),
            "truncation at {end}"
        );
    }
    for offset in [0, 8, 10, 12, 20, 28, bytes.len() - 1] {
        let mut bad = bytes.clone();
        bad[offset] ^= 0xff;
        assert!(Artifact::read(bad.as_slice(), &limits).is_err());
    }
    let mut trailing = bytes.clone();
    trailing.push(0);
    assert!(Artifact::read(trailing.as_slice(), &limits).is_err());
    let mut tiny = limits.clone();
    tiny.trace.max_total_bytes = bytes.len() as u64 - 1;
    assert!(Artifact::read(bytes.as_slice(), &tiny).is_err());
    tiny = limits.clone();
    tiny.max_metadata_bytes = 1;
    assert!(Artifact::read(bytes.as_slice(), &tiny).is_err());
    let mut output = vec![];
    assert!(evidence.write(&mut output, &tiny).is_err());
    assert!(output.is_empty(), "preflight emits no partial artifact");
}

#[test]
fn invalid_phase_and_causal_claims_are_rejected() {
    let original = sample();
    let limits = EvidenceLimits::default();
    let mut value = original.clone();
    value.steps[0].state_check_count = 100;
    assert!(value.validate(&limits).is_err());
    value = original.clone();
    value.steps[0].sequence = 2;
    assert!(value.validate(&limits).is_err());
    value = original.clone();
    value.checkpoint_after = u64::MAX;
    assert!(value.validate(&limits).is_err());
    value = original.clone();
    value.steps[1].causal.causes.push(OutputOrigin {
        sequence: 1,
        output: 0,
    });
    value.validate(&limits).unwrap();
    value.steps[1].causal.causes[0].output = 1;
    assert!(value.validate(&limits).is_err());
    value.steps[1].causal.causes[0] = OutputOrigin {
        sequence: 2,
        output: 0,
    };
    assert!(value.validate(&limits).is_err());
    value = original;
    value.provenance.environment = vec![("bound".into(), "1".into()), ("bound".into(), "2".into())];
    assert!(value.validate(&limits).is_err());
}

#[test]
fn live_recorder_retains_phase_and_checkpoint_after_ring_eviction() {
    let model = RequestModel::fixed();
    let mut state = model.initial_state().unwrap();
    let initial = check_initial(&model, &state).unwrap();
    let mut recorder = EvidenceRecorder::new(
        &initial,
        10,
        RunConfig::default(),
        RecorderOptions {
            max_steps: 2,
            ..Default::default()
        },
        Provenance::default(),
        EvidenceLimits::default(),
    )
    .unwrap();
    for (index, input) in [
        Input::Start,
        Input::Complete(1),
        Input::Start,
        Input::Complete(2),
    ]
    .iter()
    .enumerate()
    {
        let transition = model.step(&state, input).unwrap();
        let turn = check_turn(
            &model,
            &state,
            input,
            &transition,
            11 + index as u64,
            CheckPolicy::default(),
        )
        .unwrap();
        let causes = if index == 3 {
            vec![OutputOrigin {
                sequence: 13,
                output: 0,
            }]
        } else {
            vec![]
        };
        recorder
            .observe(
                &turn,
                CausalContext {
                    causes,
                    ..Default::default()
                },
            )
            .unwrap();
        state = transition.state;
    }
    let evidence = recorder.into_trace();
    assert_eq!(evidence.checkpoint_after, 12);
    assert_eq!(evidence.steps.len(), 2);
    assert_eq!(evidence.steps[0].sequence, 13);
    assert_eq!(evidence.check_phase(1, 2), Some(CheckPhase::Transition));
    evidence.validate(&EvidenceLimits::default()).unwrap();
    let text = String::from_utf8(
        diagnostic_json(&Artifact::V2(evidence), &ExportPolicy::default()).unwrap(),
    )
    .unwrap();
    assert!(text.contains("\"checkpoint_after\":12"));
    assert!(text.contains("\"omitted_prefix\":true"));
}

#[test]
fn evidence_failure_freezes_coherent_prefix() {
    let model = RequestModel::fixed();
    let state = model.initial_state().unwrap();
    let initial = check_initial(&model, &state).unwrap();
    let mut recorder = EvidenceRecorder::new(
        &initial,
        0,
        RunConfig::default(),
        RecorderOptions::default(),
        Provenance::default(),
        EvidenceLimits::default(),
    )
    .unwrap();
    let input = Input::Start;
    let transition = model.step(&state, &input).unwrap();
    let turn = check_turn(
        &model,
        &state,
        &input,
        &transition,
        1,
        CheckPolicy::default(),
    )
    .unwrap();
    assert!(
        recorder
            .observe(
                &turn,
                CausalContext {
                    causes: vec![OutputOrigin {
                        sequence: 1,
                        output: 0
                    }],
                    ..Default::default()
                }
            )
            .is_err()
    );
    assert!(recorder.is_frozen());
    let evidence = recorder.into_trace();
    assert!(evidence.steps.is_empty());
    assert!(matches!(
        evidence.trace.termination,
        Termination::ModelError(_)
    ));
    evidence.validate(&EvidenceLimits::default()).unwrap();
}

#[test]
fn metadata_retention_evicts_in_lockstep_instead_of_freezing_a_full_window() {
    let model = RequestModel::fixed();
    let mut state = model.initial_state().unwrap();
    let initial = check_initial(&model, &state).unwrap();
    let base = Provenance::default().trace_bytes(1024).unwrap().len() + 12;
    let step = StepEvidence {
        sequence: 1,
        state_check_count: 2,
        causal: CausalContext::default(),
    };
    let limits = EvidenceLimits {
        max_metadata_bytes: base + step.trace_bytes(1024).unwrap().len(),
        ..EvidenceLimits::default()
    };
    let mut recorder = EvidenceRecorder::new(
        &initial,
        0,
        RunConfig::default(),
        RecorderOptions {
            max_steps: 8,
            ..Default::default()
        },
        Provenance::default(),
        limits.clone(),
    )
    .unwrap();
    for (index, input) in [Input::Start, Input::Complete(1), Input::Start]
        .iter()
        .enumerate()
    {
        let transition = model.step(&state, input).unwrap();
        let turn = check_turn(
            &model,
            &state,
            input,
            &transition,
            index as u64 + 1,
            CheckPolicy::default(),
        )
        .unwrap();
        recorder.observe(&turn, CausalContext::default()).unwrap();
        state = transition.state;
    }
    assert!(!recorder.is_frozen());
    let evidence = recorder.into_trace();
    assert_eq!(evidence.steps.len(), 1);
    assert_eq!(evidence.checkpoint_after, 2);
    evidence.validate(&limits).unwrap();
    let mut encoded = vec![];
    evidence.write(&mut encoded, &limits).unwrap();
    assert_eq!(
        Artifact::read(encoded.as_slice(), &limits).unwrap(),
        Artifact::V2(evidence.clone())
    );
    let mut tiny = limits;
    tiny.max_metadata_bytes -= 1;
    assert!(evidence.validate(&tiny).is_err());
}

#[test]
fn upgrade_demands_exact_replay() {
    let mut value = sample();
    value.trace.steps[0].outputs[0].push(42);
    assert!(
        TraceV2::verify_upgrade(
            &RequestModel::fixed(),
            value.trace,
            Provenance::default(),
            0,
            ReplayOptions::default(),
            &EvidenceLimits::default()
        )
        .is_err()
    );
}

#[test]
fn recorder_reserves_metadata_inside_the_complete_retention_budget() {
    let model = RequestModel::fixed();
    let state = model.initial_state().unwrap();
    let initial = check_initial(&model, &state).unwrap();
    let limits = EvidenceLimits {
        max_metadata_bytes: 4096,
        ..Default::default()
    };
    // Below and exactly at the envelope reservation, no exact trace can fit.
    for maximum in [4095, 4128] {
        assert!(
            EvidenceRecorder::new(
                &initial,
                0,
                RunConfig::default(),
                RecorderOptions {
                    max_retained_bytes: maximum,
                    ..Default::default()
                },
                Provenance::default(),
                limits.clone(),
            )
            .is_err()
        );
    }
    // Each independently selected cap must cover metadata, exact bytes and footer.
    for (retention, format) in [(4096, 8192), (8192, 4096)] {
        let mut limits = EvidenceLimits {
            max_metadata_bytes: 1024,
            ..Default::default()
        };
        limits.trace.max_total_bytes = format;
        let mut recorder = EvidenceRecorder::new(
            &initial,
            0,
            RunConfig::default(),
            RecorderOptions {
                max_steps: 128,
                max_retained_bytes: retention,
                ..Default::default()
            },
            Provenance {
                run: "r".repeat(400),
                ..Default::default()
            },
            limits.clone(),
        )
        .unwrap();
        let mut state = state.clone();
        for index in 0u8..40 {
            let input = if index.is_multiple_of(2) {
                Input::Start
            } else {
                Input::Complete(index / 2 + 1)
            };
            let transition = model.step(&state, &input).unwrap();
            let turn = check_turn(
                &model,
                &state,
                &input,
                &transition,
                u64::from(index) + 1,
                CheckPolicy::default(),
            )
            .unwrap();
            recorder.observe(&turn, CausalContext::default()).unwrap();
            let mut bytes = Vec::new();
            recorder.snapshot().write(&mut bytes, &limits).unwrap();
            assert!(bytes.len() as u64 <= retention.min(format));
            state = transition.state;
        }
        assert!(recorder.snapshot().checkpoint_after > 0);
        let input = Input::Start;
        let transition = model.step(&state, &input).unwrap();
        let turn = check_turn(
            &model,
            &state,
            &input,
            &transition,
            41,
            CheckPolicy::default(),
        )
        .unwrap();
        assert!(
            recorder
                .observe(
                    &turn,
                    CausalContext {
                        correlation: Some("x".repeat(2048)),
                        ..Default::default()
                    },
                )
                .is_err()
        );
        assert!(recorder.is_frozen());
        let mut bytes = Vec::new();
        recorder.into_trace().write(&mut bytes, &limits).unwrap();
        assert!(bytes.len() as u64 <= retention.min(format));
    }
}

#[test]
fn replay_rejects_plausible_but_incorrect_persisted_phases() {
    let mut value = sample();
    value.steps[0].state_check_count = 1;
    value.validate(&EvidenceLimits::default()).unwrap();
    let report = value
        .replay(
            &RequestModel::fixed(),
            ReplayOptions::default(),
            &EvidenceLimits::default(),
        )
        .unwrap();
    assert_eq!(
        report.outcome,
        stateless::execution::ReplayOutcome::Diverged {
            step: Some(1),
            field: "check_phase"
        }
    );
    assert_eq!(report.steps_verified, 0);
}

#[test]
fn redacted_export_omits_every_raw_surface_and_is_not_replayable() {
    let secret = "PRIVATE_PAYLOAD_SENTINEL";
    let mut value = sample();
    value.provenance.run = secret.into();
    value.provenance.source = Some(secret.into());
    value
        .provenance
        .environment
        .push((secret.into(), secret.into()));
    value.trace.metadata.name = secret.into();
    value.trace.metadata.build = secret.into();
    value.trace.initial_state = secret.as_bytes().to_vec();
    value.trace.initial_checks = vec![stateless::Check::skipped(secret, secret)];
    value.trace.steps[0].input = secret.as_bytes().to_vec();
    value.trace.steps[0].post_state = secret.as_bytes().to_vec();
    value.trace.steps[0].outputs = vec![secret.as_bytes().to_vec()];
    value.steps[0].causal.correlation = Some(secret.into());
    value.trace.termination = Termination::ModelError(secret.into());
    let artifact = Artifact::V2(value);
    let bytes = diagnostic_json(&artifact, &ExportPolicy::default()).unwrap();
    let text = String::from_utf8(bytes.clone()).unwrap();
    assert!(!text.contains(secret));
    assert!(text.contains("\"replayable\":false"));
    assert!(text.contains("\"phase\":\"transition\""));
    assert!(Artifact::read(bytes.as_slice(), &EvidenceLimits::default()).is_err());
    assert!(
        diagnostic_json(
            &artifact,
            &ExportPolicy {
                max_bytes: 20,
                ..Default::default()
            }
        )
        .is_err()
    );
    let text = String::from_utf8(
        diagnostic_json(
            &artifact,
            &ExportPolicy {
                max_steps: 0,
                ..Default::default()
            },
        )
        .unwrap(),
    )
    .unwrap();
    assert!(text.contains("\"truncated\":true"));
    assert!(!text.contains("\"sequence\":1"));
}

#[test]
fn explicit_raw_export_is_private_and_never_overwrites() {
    let path = std::env::temp_dir().join(format!(
        "stateless-evidence-{}-{}.stless",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    let value = sample();
    value
        .write_raw_new(&path, &EvidenceLimits::default())
        .unwrap();
    let before = std::fs::read(&path).unwrap();
    assert!(
        value
            .write_raw_new(&path, &EvidenceLimits::default())
            .is_err()
    );
    assert_eq!(std::fs::read(&path).unwrap(), before);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
    std::fs::remove_file(path).unwrap();
}

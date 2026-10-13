use stateless::assurance::{self, AssurancePolicy, PropertyStatus};
use stateless::execution::{ReplayOptions, ReplayOutcome, record, replay};
use stateless::explore::{SearchConfig, SearchLimits};
use stateless::reference::{
    self, Controller, Effect, Input, Mutation, MutationStatus, Outcome, Phase, Settlement,
};
use stateless::reference::{coverage, evidence, host::Host};
use stateless::trace::{ReadLimits, RunConfig, Trace};
use stateless::{Disposition, Model, ModelCodec, Transition};
use std::time::Duration;

#[test]
fn finite_reference_exhausts_with_every_independent_requirement_and_goal() {
    let model = Controller::default();
    let run = assurance::enumerate(
        &model,
        coverage::manifest(&model).unwrap(),
        coverage::ReferenceCoverage,
        SearchConfig::default(),
        SearchLimits::default(),
    )
    .unwrap();
    run.search.unwrap();
    let assessed = run.assurance.assess(AssurancePolicy::Strict);
    assert!(assessed.adequate, "{:?}", assessed.reasons);
}
#[test]
fn missing_requirement_and_incomplete_search_are_never_green() {
    let model = Controller::default();
    let mut manifest = coverage::manifest(&model).unwrap();
    manifest.require(
        "independent.missing",
        stateless::assurance::PropertyPhase::Transition,
    );
    let run = assurance::enumerate(
        &model,
        manifest,
        coverage::ReferenceCoverage,
        SearchConfig {
            max_depth: 1,
            ..SearchConfig::default()
        },
        SearchLimits::default(),
    )
    .unwrap();
    assert!(!run.assurance.assess(AssurancePolicy::Strict).adequate);
    assert!(
        run.assurance
            .properties
            .iter()
            .any(|p| p.property.id == "independent.missing" && p.status == PropertyStatus::Missing)
    );
}
#[test]
fn all_fault_controls_preserve_named_target_and_replayable_witnesses() {
    for mutation in Mutation::ALL {
        let result = reference::evaluate_mutation(mutation, SearchConfig::default());
        assert_eq!(result.status, MutationStatus::Killed, "{}", mutation.name());
        for failure in [result.original.unwrap(), result.minimized.unwrap()] {
            assert!(
                failure
                    .violations
                    .iter()
                    .any(|v| v.check.id == mutation.target())
            );
            let model = Controller::with_mutation(mutation);
            let trace = record(
                &model,
                failure.inputs.clone(),
                RunConfig::default(),
                failure.inputs.len(),
            )
            .unwrap();
            let mut bytes = Vec::new();
            trace.write_to(&mut bytes).unwrap();
            let restored = Trace::read_from(bytes.as_slice(), &ReadLimits::default()).unwrap();
            let replayed = replay(&model, &restored, ReplayOptions::default()).unwrap();
            assert_eq!(replayed.outcome, ReplayOutcome::Exact);
            assert!(replayed.failure_reproduced);
        }
    }
}
#[test]
fn cutoff_and_configuration_error_are_not_mutation_survival_or_kills() {
    let result = reference::evaluate_mutation(
        Mutation::MissingCleanup,
        SearchConfig {
            max_depth: 0,
            ..SearchConfig::default()
        },
    );
    assert!(matches!(result.status, MutationStatus::NotEvaluated { .. }));
    let result = reference::evaluate_mutation(
        Mutation::MissingCleanup,
        SearchConfig {
            max_states: 0,
            ..SearchConfig::default()
        },
    );
    assert!(matches!(result.status, MutationStatus::NotEvaluated { .. }));
}
#[test]
fn retained_requirement_seeds_reproduce_named_targets_and_fixed_regressions() {
    evidence::check_regressions(include_str!(
        "../docs/requirements/controller-witnesses.txt"
    ))
    .unwrap();
}
#[test]
fn missing_regression_section_fails_closed() {
    assert!(
        evidence::regression_corpus("[missing-cleanup]\nsubmit\ncomplete 1 1 success\n").is_err()
    );
}
#[test]
fn superseded_work_retains_resource_until_ack_and_never_publishes() {
    let model = Controller::default();
    let first = model
        .step(&model.initial_state().unwrap(), &Input::Submit)
        .unwrap();
    let second = model.step(&first.state, &Input::Submit).unwrap();
    assert_eq!(
        second.outputs,
        [
            Effect::Cancel {
                generation: 1,
                attempt: 1
            },
            Effect::Start {
                generation: 2,
                attempt: 1
            }
        ]
    );
    assert!(second.state.job(1).unwrap().resource_owned);
    let late = model
        .step(
            &second.state,
            &Input::Complete {
                generation: 1,
                attempt: 1,
                outcome: Outcome::Success,
            },
        )
        .unwrap();
    assert_eq!(
        late.outputs,
        [
            Effect::Settle {
                generation: 1,
                result: Settlement::Cancelled
            },
            Effect::Release { generation: 1 }
        ]
    );
    assert_eq!(late.state.current, Some(2));
}

#[test]
fn input_derived_obligations_reject_missing_state_and_effect_progress() {
    let model = Controller::default();
    let initial = model.initial_state().unwrap();
    let running = model.step(&initial, &Input::Submit).unwrap().state;
    let waiting = model
        .step(
            &running,
            &Input::Complete {
                generation: 1,
                attempt: 1,
                outcome: Outcome::RetryableFailure,
            },
        )
        .unwrap()
        .state;
    let cancelling = model
        .step(&running, &Input::Cancel { generation: 1 })
        .unwrap()
        .state;
    // Deliberately fabricated observations are independent of reducer branches.
    // Each leaves both the prior state and required progress effects absent.
    for (before, input, outputs, target) in [
        (
            &initial,
            Input::Submit,
            vec![],
            "controller.submission_progress",
        ),
        (
            &running,
            Input::Cancel { generation: 1 },
            vec![],
            "controller.cancellation_progress",
        ),
        (
            &waiting,
            Input::Cancel { generation: 1 },
            vec![],
            "controller.cancellation_progress",
        ),
        (
            &running,
            Input::Complete {
                generation: 1,
                attempt: 1,
                outcome: Outcome::Success,
            },
            vec![Effect::Publish { generation: 1 }],
            "controller.completion_progress",
        ),
        (
            &running,
            Input::Complete {
                generation: 1,
                attempt: 1,
                outcome: Outcome::RetryableFailure,
            },
            vec![],
            "controller.completion_progress",
        ),
        (
            &cancelling,
            Input::Complete {
                generation: 1,
                attempt: 1,
                outcome: Outcome::Success,
            },
            vec![],
            "controller.completion_progress",
        ),
    ] {
        for disposition in [
            Disposition::Accepted,
            Disposition::Ignored("no progress".into()),
        ] {
            let fabricated = Transition {
                state: before.clone(),
                outputs: outputs.clone(),
                disposition,
            };
            let checks = model
                .check_transition(before, &input, &fabricated.as_ref())
                .unwrap();
            assert!(
                checks.iter().any(|c| c.id == target && c.is_failure()),
                "{target}"
            );
        }
        let correct = model.step(before, &input).unwrap();
        assert!(
            model
                .check_transition(before, &input, &correct.as_ref())
                .unwrap()
                .iter()
                .all(|c| !c.is_failure())
        );
    }
}
#[test]
fn logical_time_controls_retry_and_stale_attempts_are_inert() {
    let model = Controller::default();
    let started = model
        .step(&model.initial_state().unwrap(), &Input::Submit)
        .unwrap();
    let waiting = model
        .step(
            &started.state,
            &Input::Complete {
                generation: 1,
                attempt: 1,
                outcome: Outcome::RetryableFailure,
            },
        )
        .unwrap();
    assert_eq!(
        waiting.state.job(1).unwrap().phase,
        Phase::Waiting { deadline: 1 }
    );
    let retry = model.step(&waiting.state, &Input::Tick { now: 1 }).unwrap();
    assert_eq!(
        retry.outputs,
        [Effect::Start {
            generation: 1,
            attempt: 2
        }]
    );
    let late = model
        .step(
            &retry.state,
            &Input::Complete {
                generation: 1,
                attempt: 1,
                outcome: Outcome::Success,
            },
        )
        .unwrap();
    assert_eq!(late.state, retry.state);
    assert!(late.outputs.is_empty());
}
#[test]
fn canonical_codec_rejects_trailing_invalid_and_unbounded_values() {
    let model = Controller::default();
    let state = model.initial_state().unwrap();
    assert_eq!(
        model
            .decode_state(&model.encode_state(&state).unwrap())
            .unwrap(),
        state
    );
    let input = Input::Complete {
        generation: 1,
        attempt: 2,
        outcome: Outcome::Success,
    };
    let bytes = model.encode_input(&input).unwrap();
    assert_eq!(model.decode_input(&bytes).unwrap(), input);
    let mut bad = bytes.clone();
    bad.push(0);
    assert!(model.decode_input(&bad).is_err());
    let mut bad = bytes;
    bad[0] = 255;
    assert!(model.decode_input(&bad).is_err());
    assert!(
        model
            .decode_input(
                &model
                    .encode_input(&Input::Cancel { generation: 9 })
                    .unwrap()
            )
            .is_err()
    );
}
#[test]
fn threaded_host_executes_once_and_live_recording_replays_without_effects() {
    let mut host = Host::new(Default::default(), |_, _| Outcome::Success, true).unwrap();
    host.deliver(Input::Submit).unwrap();
    assert!(host.receive(Duration::from_secs(5)).unwrap());
    assert_eq!(host.published, [1]);
    assert_eq!(host.settled.len(), 1);
    assert_eq!(host.pending_workers(), 0);
    let effects = host.executed_effects;
    host.deliver(Input::Complete {
        generation: 1,
        attempt: 1,
        outcome: Outcome::Success,
    })
    .unwrap();
    assert_eq!(host.executed_effects, effects);
    let trace = host.recording().unwrap();
    let replayed = replay(&Controller::default(), &trace, ReplayOptions::default()).unwrap();
    assert_eq!(replayed.outcome, ReplayOutcome::Exact);
    assert!(host.diagnostics.is_empty());
}
#[test]
fn host_cancellation_waits_for_real_worker_acknowledgment() {
    let (release, wait) = std::sync::mpsc::channel();
    let wait = std::sync::Mutex::new(wait);
    let mut host = Host::new(
        Default::default(),
        move |_, _| {
            wait.lock().unwrap().recv().unwrap();
            Outcome::Success
        },
        true,
    )
    .unwrap();
    host.deliver(Input::Submit).unwrap();
    host.deliver(Input::Cancel { generation: 1 }).unwrap();
    assert!(host.state().job(1).unwrap().resource_owned);
    assert!(!host.receive(Duration::from_millis(1)).unwrap());
    release.send(()).unwrap();
    assert!(host.receive(Duration::from_secs(5)).unwrap());
    assert_eq!(host.settled.get(&1), Some(&Settlement::Cancelled));
    assert!(host.published.is_empty());
    assert_eq!(host.pending_workers(), 0);
}

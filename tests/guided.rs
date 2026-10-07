use stateless::campaign::{CampaignConfig, CampaignTermination, JobOutcome, run_guided_campaign};
use stateless::execution::{ReplayOptions, ReplayOutcome, record, replay};
use stateless::explore::{self, FuzzConfig, FuzzTermination, RunLimits, ShrinkConfig};
use stateless::guided::{
    self, FeatureBuffer, Feedback, FeedbackMetadata, GuidanceConfig, StateFeedback,
};
use stateless::trace::RunConfig;
use stateless::*;
use std::cell::Cell;
use std::hash::{Hash, Hasher};
use std::sync::atomic::{AtomicBool, Ordering};

#[derive(Clone, Debug, PartialEq, Eq)]
struct Input {
    at: u8,
    advance: bool,
}
struct Walk {
    rare: bool,
    failure_at: Option<u8>,
    calls: Cell<usize>,
}
impl Walk {
    fn new(rare: bool, failure_at: Option<u8>) -> Self {
        Self {
            rare,
            failure_at,
            calls: Cell::new(0),
        }
    }
}
impl Model for Walk {
    type State = u8;
    type Input = Input;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "guided-walk".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: format!("rare={};failure={:?}", self.rare, self.failure_at),
        }
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u8, input: &Input) -> Result<Transition<u8, u8>, ModelError> {
        if input.at != *state {
            return Err(ModelError::new("stale input"));
        }
        self.calls.set(self.calls.get() + 1);
        let next = if input.advance { state + 1 } else { 0 };
        Ok(Transition::accepted(next, vec![next]))
    }
    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if self.failure_at.is_some_and(|at| *state >= at) {
            Check::failed("depth", "deep state")
        } else {
            Check::passed("depth")
        }])
    }
}
impl Generate for Walk {
    fn generate(&self, state: &u8, rng: &mut Rng) -> Result<Option<Input>, ModelError> {
        Ok(Some(Input {
            at: *state,
            advance: !self.rare || rng.index(8) == Some(0),
        }))
    }
    fn is_enabled(&self, state: &u8, input: &Input) -> Result<bool, ModelError> {
        Ok(input.at == *state)
    }
}
impl ModelCodec for Walk {
    fn encode_state(&self, s: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*s])
    }
    fn decode_state(&self, b: &[u8]) -> Result<u8, ModelError> {
        match b {
            [s] => Ok(*s),
            _ => Err(ModelError::new("state")),
        }
    }
    fn encode_input(&self, i: &Input) -> Result<Vec<u8>, ModelError> {
        Ok(vec![i.at, u8::from(i.advance)])
    }
    fn decode_input(&self, b: &[u8]) -> Result<Input, ModelError> {
        match b {
            [at, advance] if *advance <= 1 => Ok(Input {
                at: *at,
                advance: *advance == 1,
            }),
            _ => Err(ModelError::new("input")),
        }
    }
    fn encode_output(&self, o: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*o])
    }
}
#[derive(PartialEq, Eq)]
struct Collision(u8);
impl Hash for Collision {
    fn hash<H: Hasher>(&self, h: &mut H) {
        0u8.hash(h);
    }
}
fn metadata() -> FeedbackMetadata {
    FeedbackMetadata {
        name: "depth".into(),
        version: 1,
        build: "depth-v1".into(),
    }
}
fn feedback() -> impl Feedback<Walk> {
    StateFeedback::new(metadata(), |state: &u8| Collision(*state))
}
fn config() -> FuzzConfig {
    FuzzConfig {
        seed: 42,
        cases: 1_000,
        max_steps: 12,
        max_transitions: 12_000,
        mutation_percent: 100,
    }
}

#[test]
fn novel_prefixes_find_a_deep_failure_and_remain_shrinkable_and_replayable() {
    let plain = explore::fuzz(&Walk::new(true, Some(6)), config()).unwrap();
    assert!(
        plain.failure.is_none(),
        "fixed-seed comparison fixture changed"
    );
    let model = Walk::new(true, Some(6));
    let report = guided::fuzz(&model, config(), GuidanceConfig::default(), feedback()).unwrap();
    assert_eq!(report.termination, FuzzTermination::FailureFound);
    println!(
        "fixed-seed deep-state comparison: plain={} transitions (no failure), guided={} transitions (failure)",
        plain.transitions, report.transitions
    );
    let guidance = report.guidance.as_ref().unwrap();
    assert!(guidance.corpus.len() >= 5);
    assert!(guidance.mutation_cases > 0);
    assert_eq!(guidance.unique_features, 6); // failing transition takes precedence over feedback
    let failure = report.failure.as_ref().unwrap();
    let shrunk = explore::shrink(&model, failure, ShrinkConfig::default()).unwrap();
    assert!(shrunk.validated_original);
    assert!(shrunk.minimized.inputs.len() <= failure.inputs.len());
    let trace = record(&model, shrunk.minimized.inputs, RunConfig::default(), 100).unwrap();
    let replay = replay(&model, &trace, ReplayOptions::default()).unwrap();
    assert_eq!(replay.outcome, ReplayOutcome::Exact);
    assert!(replay.failure_reproduced);
}
#[test]
fn fixed_seed_repeats_inputs_and_collision_safe_corpus_evidence() {
    let a = guided::fuzz(
        &Walk::new(true, None),
        config(),
        GuidanceConfig::default(),
        feedback(),
    )
    .unwrap();
    let b = guided::fuzz(
        &Walk::new(true, None),
        config(),
        GuidanceConfig::default(),
        feedback(),
    )
    .unwrap();
    assert_eq!(a.guidance, b.guidance);
    assert_eq!(a.failure, b.failure);
    assert_eq!(a.transitions, b.transitions);
    let g = a.guidance.unwrap();
    assert!(g.unique_features > 4);
    assert!(g.corpus.len() > 3);
    for entry in g.corpus {
        let trace = record(
            &Walk::new(true, None),
            entry.inputs,
            RunConfig::default(),
            100,
        )
        .unwrap();
        assert!(matches!(
            trace.termination,
            stateless::trace::Termination::Completed
        ));
    }
}
#[test]
fn retention_saturates_without_stopping_execution_or_growing_past_limits() {
    let limits = GuidanceConfig {
        max_corpus_entries: 1,
        max_corpus_inputs: 1,
        max_features: 4,
        max_features_per_observation: 1,
    };
    let report = guided::fuzz(
        &Walk::new(false, None),
        FuzzConfig {
            cases: 20,
            max_steps: 10,
            max_transitions: 200,
            ..config()
        },
        limits,
        feedback(),
    )
    .unwrap();
    assert_eq!(report.termination, FuzzTermination::CasesCompleted);
    assert_eq!(report.transitions, 200);
    let g = report.guidance.unwrap();
    assert_eq!(g.unique_features, 4);
    assert_eq!(g.corpus.len(), 1);
    assert_eq!(g.corpus_inputs, 1);
    assert_eq!(g.dropped_prefixes, 2);
    assert!(g.feature_limit_reached && g.corpus_limit_reached);
}
struct Spam {
    fail: bool,
    called: Cell<usize>,
}
impl Feedback<Walk> for Spam {
    type Feature = u8;
    fn metadata(&self) -> FeedbackMetadata {
        metadata()
    }
    fn transition_features(
        &self,
        _: &u8,
        _: &Input,
        _: &TransitionRef<'_, u8, u8>,
        out: &mut FeatureBuffer<'_, u8>,
    ) -> Result<(), ModelError> {
        self.called.set(self.called.get() + 1);
        if self.fail {
            return Err(ModelError::new("feedback unavailable"));
        }
        for i in 0..5 {
            let _ = out.push(i);
        }
        Ok(())
    }
}
#[test]
fn ignored_feedback_limits_and_callback_errors_are_engine_errors() {
    let limits = GuidanceConfig {
        max_features_per_observation: 2,
        ..GuidanceConfig::default()
    };
    for fail in [false, true] {
        let result = guided::fuzz(
            &Walk::new(false, None),
            config(),
            limits,
            Spam {
                fail,
                called: Cell::new(0),
            },
        );
        assert!(result.unwrap_err().0.contains(if fail {
            "feedback unavailable"
        } else {
            "feature limit"
        }));
    }
}
#[test]
fn failing_property_precedes_feedback_error_and_pre_cancel_invokes_no_execution() {
    let report = guided::fuzz(
        &Walk::new(false, Some(1)),
        config(),
        GuidanceConfig::default(),
        Spam {
            fail: true,
            called: Cell::new(0),
        },
    )
    .unwrap();
    assert_eq!(report.termination, FuzzTermination::FailureFound);
    assert_eq!(report.transitions, 1);
    let cancelled = AtomicBool::new(true);
    let calls = Cell::new(0);
    let feedback = StateFeedback::new(metadata(), |s: &u8| {
        calls.set(calls.get() + 1);
        *s
    });
    let report = guided::fuzz_with_limits(
        &Walk::new(false, None),
        config(),
        GuidanceConfig::default(),
        feedback,
        RunLimits {
            cancellation: Some(&cancelled),
            max_duration: None,
        },
    )
    .unwrap();
    assert_eq!(report.termination, FuzzTermination::Cancelled);
    assert_eq!(report.transitions, 0);
    assert_eq!(calls.get(), 0);
}
#[test]
fn cancellation_from_feedback_is_observed_before_the_next_transition() {
    let cancelled = AtomicBool::new(false);
    let feedback = StateFeedback::new(metadata(), |s: &u8| {
        if *s == 1 {
            cancelled.store(true, Ordering::Relaxed);
        }
        *s
    });
    let report = guided::fuzz_with_limits(
        &Walk::new(false, None),
        config(),
        GuidanceConfig::default(),
        feedback,
        RunLimits {
            cancellation: Some(&cancelled),
            max_duration: None,
        },
    )
    .unwrap();
    assert_eq!(report.termination, FuzzTermination::Cancelled);
    assert_eq!(report.transitions, 1);
    assert_eq!(report.guidance.unwrap().corpus.len(), 1);
}
#[test]
fn guided_campaign_jobs_match_across_workers_with_local_non_send_feature_keys() {
    let mut runs = Vec::new();
    for workers in [1, 4] {
        let mut jobs = std::collections::BTreeMap::new();
        let report = run_guided_campaign(
            CampaignConfig {
                jobs: 8,
                workers,
                fuzz: config(),
                ..CampaignConfig::default()
            },
            RunLimits::default(),
            GuidanceConfig::default(),
            |_| Ok(Walk::new(true, Some(6))),
            |_, _| {
                Ok(StateFeedback::new(metadata(), |s: &u8| {
                    std::rc::Rc::new(*s)
                }))
            },
            |job| {
                assert!(matches!(job.outcome, JobOutcome::Completed(_)));
                jobs.insert(job.job_id, format!("{job:#?}"));
                Ok(())
            },
        )
        .unwrap();
        assert_eq!(report.termination, CampaignTermination::JobsCompleted);
        assert_eq!(report.delivered, 8);
        runs.push(jobs);
    }
    assert_eq!(runs[0], runs[1]);
}
#[test]
fn invalid_guidance_rejected_before_campaign_factories_or_sinks() {
    let called = Cell::new(false);
    // The serial factory still has to be Sync; use an atomic for factory evidence.
    let factory_called = AtomicBool::new(false);
    let result = run_guided_campaign(
        CampaignConfig::default(),
        RunLimits::default(),
        GuidanceConfig {
            max_features: 0,
            ..GuidanceConfig::default()
        },
        |_| {
            factory_called.store(true, Ordering::Relaxed);
            Ok(Walk::new(false, None))
        },
        |_, _| Ok(feedback()),
        |_| {
            called.set(true);
            Ok(())
        },
    );
    assert!(result.is_err());
    assert!(!factory_called.load(Ordering::Relaxed));
    assert!(!called.get());
}

#[test]
fn campaign_deadline_includes_feedback_factory_time_in_the_last_job() {
    let mut outcome = None;
    let report = run_guided_campaign(
        CampaignConfig {
            jobs: 1,
            workers: 1,
            ..CampaignConfig::default()
        },
        RunLimits {
            max_duration: Some(std::time::Duration::from_millis(2)),
            cancellation: None,
        },
        GuidanceConfig::default(),
        |_| Ok(Walk::new(false, None)),
        |_, _| {
            std::thread::sleep(std::time::Duration::from_millis(15));
            Ok(feedback())
        },
        |job| {
            outcome = Some(job.outcome);
            Ok(())
        },
    )
    .unwrap();
    assert_eq!(report.termination, CampaignTermination::Deadline);
    let JobOutcome::Completed(fuzz) = outcome.unwrap() else {
        panic!("expected bounded run");
    };
    assert_eq!(fuzz.termination, FuzzTermination::Deadline);
    assert_eq!(fuzz.transitions, 0);
}

#[test]
fn a_full_corpus_replaces_oldest_paths_and_accounts_for_stored_inputs() {
    let report = guided::fuzz(
        &Walk::new(false, None),
        FuzzConfig {
            cases: 1,
            max_steps: 10,
            max_transitions: 10,
            ..config()
        },
        GuidanceConfig {
            max_corpus_entries: 2,
            max_corpus_inputs: 17,
            ..GuidanceConfig::default()
        },
        feedback(),
    )
    .unwrap();
    let g = report.guidance.unwrap();
    assert_eq!(
        g.corpus_inputs,
        g.corpus.iter().map(|entry| entry.inputs.len()).sum()
    );
    assert!(g.corpus_inputs <= 17);
    assert!(g.corpus.len() <= 2);
    assert_eq!(g.corpus.last().unwrap().inputs.len(), 10);
    assert!(g.evicted_prefixes > 0);
    assert_eq!(g.dropped_prefixes, 0);
    assert!(g.corpus_limit_reached);
}

#[test]
fn a_shorter_witness_recovers_a_feature_from_a_dropped_prefix_even_at_feature_cap() {
    struct Shortcuts;
    impl Model for Shortcuts {
        type State = u8;
        type Input = u8;
        type Output = ();
        fn metadata(&self) -> ModelMetadata {
            Walk::new(false, None).metadata()
        }
        fn initial_state(&self) -> Result<u8, ModelError> {
            Ok(0)
        }
        fn step(&self, state: &u8, input: &u8) -> Result<Transition<u8, ()>, ModelError> {
            Ok(Transition::accepted(state + input, Vec::new()))
        }
        fn check_state(&self, _: &u8) -> Result<Vec<Check>, ModelError> {
            Ok(vec![Check::passed("valid")])
        }
    }
    impl Generate for Shortcuts {
        fn generate(&self, _: &u8, rng: &mut Rng) -> Result<Option<u8>, ModelError> {
            Ok(Some(1 + rng.index(2).unwrap() as u8))
        }
    }
    struct Target;
    impl Feedback<Shortcuts> for Target {
        type Feature = Collision;
        fn metadata(&self) -> FeedbackMetadata {
            metadata()
        }
        fn transition_features(
            &self,
            _: &u8,
            _: &u8,
            after: &TransitionRef<'_, u8, ()>,
            out: &mut FeatureBuffer<'_, Collision>,
        ) -> Result<(), ModelError> {
            if *after.state == 2 {
                // Duplicate keys must not inflate discoveries or admissions.
                out.push(Collision(2))?;
                out.push(Collision(2))?;
            }
            Ok(())
        }
    }
    let seed = (0..1_000)
        .find(|seed| {
            let mut rng = Rng::new(*seed);
            [rng.index(2), rng.index(2), rng.index(2)] == [Some(0), Some(0), Some(1)]
        })
        .unwrap();
    let report = guided::fuzz(
        &Shortcuts,
        FuzzConfig {
            seed,
            cases: 2,
            max_steps: 2,
            max_transitions: 4,
            mutation_percent: 0,
        },
        GuidanceConfig {
            max_corpus_inputs: 1,
            max_features: 1,
            max_features_per_observation: 2,
            ..GuidanceConfig::default()
        },
        Target,
    )
    .unwrap();
    let g = report.guidance.unwrap();
    assert_eq!(g.algorithm, guided::ALGORITHM);
    assert_eq!(g.unique_features, 1);
    assert_eq!(g.dropped_prefixes, 1);
    assert_eq!(g.corpus_inputs, 1);
    assert_eq!(g.corpus.len(), 1);
    assert_eq!(g.corpus[0].inputs, vec![2]);
    assert_eq!(g.corpus[0].new_features, 0);
    assert_eq!(g.corpus[0].discovered_case, 2);
    assert_eq!(g.corpus[0].discovered_transition, 3);
}

#[test]
fn metadata_time_is_included_and_exact_state_feedback_is_available() {
    struct Slow;
    impl Feedback<Walk> for Slow {
        type Feature = u8;
        fn metadata(&self) -> FeedbackMetadata {
            std::thread::sleep(std::time::Duration::from_millis(15));
            metadata()
        }
        fn transition_features(
            &self,
            _: &u8,
            _: &Input,
            _: &TransitionRef<'_, u8, u8>,
            _: &mut FeatureBuffer<'_, u8>,
        ) -> Result<(), ModelError> {
            panic!("expired search must not invoke feedback")
        }
    }
    let report = guided::fuzz_with_limits(
        &Walk::new(false, None),
        config(),
        GuidanceConfig::default(),
        Slow,
        RunLimits {
            max_duration: Some(std::time::Duration::from_millis(2)),
            cancellation: None,
        },
    )
    .unwrap();
    assert_eq!(report.termination, FuzzTermination::Deadline);
    assert_eq!(report.cases, 0);
    let report = guided::fuzz(
        &Walk::new(false, None),
        FuzzConfig {
            cases: 1,
            max_steps: 5,
            max_transitions: 5,
            ..config()
        },
        GuidanceConfig::default(),
        guided::exact_state::<Walk>(),
    )
    .unwrap();
    let g = report.guidance.unwrap();
    assert_eq!(g.feedback.name, "exact-state");
    assert_eq!(g.unique_features, 6);
}

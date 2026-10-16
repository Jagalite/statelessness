use std::cell::Cell;
use std::collections::BTreeMap;
use std::sync::atomic::AtomicBool;

use stateless::assurance::*;
use stateless::explore::{FuzzConfig, RunLimits, SearchConfig, SearchLimits, SearchTermination};
use stateless::{
    Check, CheckSink, Enumerate, Generate, Model, ModelError, ModelMetadata, Rng, Transition,
    TransitionRef,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Input {
    Advance,
    Loop,
}
#[derive(Default)]
struct Fixture {
    steps: Cell<u64>,
    skip: bool,
    hidden_failure: bool,
    callback_error: bool,
    step_error: bool,
    omit_after_initial: bool,
    duplicate_state_property: bool,
}
impl Model for Fixture {
    type State = u8;
    type Input = Input;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "fixture".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "source-fixture".into(),
        }
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, _: &u8, input: &Input) -> Result<Transition<u8, ()>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        if self.step_error && *input == Input::Loop {
            return Err(ModelError::new("step failure"));
        }
        Ok(Transition::accepted(1, vec![]))
    }
    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        if self.omit_after_initial && *state != 0 {
            return Ok(vec![]);
        }
        let mut checks = vec![if self.skip {
            Check::skipped("same-id", "fixture")
        } else {
            Check::passed("same-id")
        }];
        if self.duplicate_state_property {
            checks.push(Check::passed("same-id"));
        }
        Ok(checks)
    }
    fn check_transition_into(
        &self,
        _: &u8,
        input: &Input,
        _: &TransitionRef<'_, u8, ()>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.push(Check::passed("same-id"));
        if self.hidden_failure {
            checks.push(Check::failed("undeclared", "must not be concealed"));
        }
        if self.callback_error && *input == Input::Loop {
            return Err(ModelError::new("partial transition check"));
        }
        Ok(())
    }
}
impl Enumerate for Fixture {
    fn inputs(&self, state: &u8) -> Result<Vec<Input>, ModelError> {
        Ok(vec![if *state == 0 {
            Input::Advance
        } else {
            Input::Loop
        }])
    }
}
impl Generate for Fixture {
    fn generate(&self, state: &u8, _: &mut Rng) -> Result<Option<Input>, ModelError> {
        Ok(Some(if *state == 0 {
            Input::Advance
        } else {
            Input::Loop
        }))
    }
}
struct Covers;
impl Coverage<Fixture> for Covers {
    fn observe(
        &self,
        _: &u8,
        input: &Input,
        _: &TransitionRef<'_, u8, ()>,
        history: &[Input],
        hits: &mut CoverageHits,
    ) -> Result<(), ModelError> {
        match input {
            Input::Advance => hits.family("advance"),
            Input::Loop => {
                hits.family("loop");
                hits.goal("visited_loop");
            }
        }
        if history.len() >= 3 {
            hits.goal("two_loops");
        }
        Ok(())
    }
}
fn manifest() -> Manifest {
    Manifest {
        assurance_version: 1,
        model: "fixture".into(),
        required_properties: vec![
            RequiredProperty {
                id: "same-id".into(),
                phase: PropertyPhase::State,
            },
            RequiredProperty {
                id: "same-id".into(),
                phase: PropertyPhase::Transition,
            },
        ],
        input_families: vec![
            InputFamily {
                name: "advance".into(),
                coverage: FamilyCoverage::Required,
            },
            InputFamily {
                name: "loop".into(),
                coverage: FamilyCoverage::Required,
            },
            InputFamily {
                name: "crash".into(),
                coverage: FamilyCoverage::Excluded {
                    reason: "separate persistence model".into(),
                },
            },
        ],
        bounds: BTreeMap::from([("states".into(), "0..=1".into())]),
        cover_goals: vec![CoverGoal {
            id: "visited_loop".into(),
            scope: GoalScope::Transition,
        }],
        required_search_outcome: RequiredSearchOutcome::GraphExhausted,
        provenance: Provenance {
            manifest: "requirements-v1".into(),
            model: "model-v1".into(),
            properties: "properties-v1".into(),
        },
    }
}
fn run(
    model: &Fixture,
    m: Manifest,
    config: SearchConfig,
) -> AssuredRun<stateless::explore::SearchReport<Input>> {
    enumerate(model, m, Covers, config, SearchLimits::default()).unwrap()
}
#[test]
fn satisfied_manifest_observes_duplicate_state_edges_without_reducer_rerun() {
    let model = Fixture::default();
    let run = run(&model, manifest(), SearchConfig::default());
    assert_eq!(
        run.search.unwrap().termination,
        SearchTermination::GraphExhausted
    );
    assert_eq!(model.steps.get(), 2);
    assert_eq!(run.assurance.search.checked_transitions, 2);
    assert!(run.assurance.assess(AssurancePolicy::Strict).adequate);
    assert_eq!(run.assurance.properties[0].counts.passed, 3);
    assert_eq!(run.assurance.properties[1].counts.passed, 2);
    assert!(matches!(
        run.assurance
            .families
            .iter()
            .find(|f| f.name == "crash")
            .unwrap()
            .status,
        FamilyStatus::Excluded { .. }
    ));
}
#[test]
fn omitted_property_and_input_family_cannot_be_green() {
    let mut m = manifest();
    m.require("forgotten", PropertyPhase::Transition);
    m.input_families.push(InputFamily {
        name: "forgotten-event".into(),
        coverage: FamilyCoverage::Required,
    });
    let report = run(&Fixture::default(), m, SearchConfig::default()).assurance;
    assert_eq!(
        report
            .properties
            .iter()
            .find(|p| p.property.id == "forgotten")
            .unwrap()
            .status,
        PropertyStatus::Missing
    );
    assert_eq!(
        report
            .families
            .iter()
            .find(|f| f.name == "forgotten-event")
            .unwrap()
            .status,
        FamilyStatus::Missing
    );
    assert!(!report.assess(AssurancePolicy::Strict).adequate);
}

#[test]
fn required_property_present_only_initially_is_missing_in_later_batches() {
    let report = run(
        &Fixture {
            omit_after_initial: true,
            ..Fixture::default()
        },
        manifest(),
        SearchConfig::default(),
    )
    .assurance;
    let property = report
        .properties
        .iter()
        .find(|p| p.property.phase == PropertyPhase::State)
        .unwrap();
    assert_eq!(property.counts.passed, 1);
    assert_eq!(property.counts.missing, 2);
    assert_eq!(property.status, PropertyStatus::Missing);
    assert!(!report.assess(AssurancePolicy::Strict).adequate);
    assert!(report.to_json().contains("\"missing\":2"));
    let good = run(&Fixture::default(), manifest(), SearchConfig::default()).assurance;
    let merged = merge_reports(
        manifest(),
        Fixture::default().metadata(),
        [0, 1],
        [(0, report), (1, good)],
    )
    .unwrap();
    let property = merged
        .report
        .properties
        .iter()
        .find(|p| p.property.phase == PropertyPhase::State)
        .unwrap();
    assert_eq!(property.counts.passed, 4);
    assert_eq!(property.counts.missing, 2);
    assert_eq!(property.status, PropertyStatus::Missing);
    assert!(!merged.report.assess(AssurancePolicy::Strict).adequate);
}

#[test]
fn duplicate_ids_within_a_phase_are_ambiguous_even_if_every_copy_passes() {
    let report = run(
        &Fixture {
            duplicate_state_property: true,
            ..Fixture::default()
        },
        manifest(),
        SearchConfig::default(),
    )
    .assurance;
    assert!(
        report
            .properties
            .iter()
            .all(|p| p.status == PropertyStatus::Passed)
    );
    assert!(
        report
            .callback_errors
            .iter()
            .any(|e| e.contains("ambiguous duplicate property ID in State batch: same-id"))
    );
    assert!(!report.assess(AssurancePolicy::Strict).adequate);
    // Same spelling in separate state/transition phases remains unambiguous.
    assert!(
        run(&Fixture::default(), manifest(), SearchConfig::default())
            .assurance
            .assess(AssurancePolicy::Strict)
            .adequate
    );
}
#[test]
fn transition_and_state_property_ids_are_separate() {
    let mut m = manifest();
    m.require("state-only", PropertyPhase::Transition);
    let report = run(&Fixture::default(), m, SearchConfig::default()).assurance;
    assert_eq!(
        report
            .properties
            .iter()
            .filter(|p| p.status == PropertyStatus::Passed)
            .count(),
        2
    );
    assert_eq!(
        report
            .properties
            .iter()
            .find(|p| p.property.id == "state-only")
            .unwrap()
            .status,
        PropertyStatus::Missing
    );
}
#[test]
fn exhausted_transition_goal_is_unreachable_but_still_fails_adequacy() {
    let mut m = manifest();
    m.cover_goals.push(CoverGoal {
        id: "impossible".into(),
        scope: GoalScope::Transition,
    });
    let report = run(&Fixture::default(), m, SearchConfig::default()).assurance;
    assert_eq!(
        report
            .coverage
            .iter()
            .find(|g| g.goal.id == "impossible")
            .unwrap()
            .status,
        CoverageStatus::UnreachableUnderDeclaredModel
    );
    assert!(!report.assess(AssurancePolicy::Strict).adequate);
}
#[test]
fn graph_dedup_never_certifies_unobserved_history_unreachable() {
    let mut m = manifest();
    m.cover_goals.push(CoverGoal {
        id: "two_loops".into(),
        scope: GoalScope::History,
    });
    let report = run(&Fixture::default(), m.clone(), SearchConfig::default()).assurance;
    assert_eq!(report.search.outcome, SearchOutcome::GraphExhausted);
    assert_eq!(
        report
            .coverage
            .iter()
            .find(|g| g.goal.id == "two_loops")
            .unwrap()
            .status,
        CoverageStatus::NotReachedBeforeStop
    );
    m.required_search_outcome = RequiredSearchOutcome::Completed;
    let sampled = fuzz(
        &Fixture::default(),
        m,
        Covers,
        FuzzConfig {
            cases: 1,
            max_steps: 3,
            ..FuzzConfig::default()
        },
        RunLimits::default(),
    )
    .unwrap();
    assert!(sampled.assurance.assess(AssurancePolicy::Strict).adequate);
}
#[test]
fn budget_and_depth_stop_leave_unobserved_goals_incomplete() {
    for config in [
        SearchConfig {
            max_transitions: 1,
            ..SearchConfig::default()
        },
        SearchConfig {
            max_depth: 1,
            ..SearchConfig::default()
        },
        SearchConfig {
            max_states: 1,
            ..SearchConfig::default()
        },
    ] {
        let report = run(&Fixture::default(), manifest(), config).assurance;
        assert_eq!(
            report.coverage[0].status,
            CoverageStatus::NotReachedBeforeStop
        );
        assert!(!report.assess(AssurancePolicy::Strict).adequate);
    }
}
#[test]
fn terminal_snapshot_does_not_satisfy_unexecuted_scenario() {
    let report = run(
        &Fixture::default(),
        manifest(),
        SearchConfig {
            max_depth: 0,
            ..SearchConfig::default()
        },
    )
    .assurance;
    assert_eq!(report.search.checked_transitions, 0);
    assert_eq!(
        report.coverage[0].status,
        CoverageStatus::NotReachedBeforeStop
    );
    assert_eq!(report.properties[1].status, PropertyStatus::Missing);
}
#[test]
fn cancellation_before_start_has_no_observations() {
    let cancel = AtomicBool::new(true);
    let model = Fixture::default();
    let report = enumerate(
        &model,
        manifest(),
        Covers,
        SearchConfig::default(),
        SearchLimits {
            control: RunLimits {
                cancellation: Some(&cancel),
                ..RunLimits::default()
            },
            ..SearchLimits::default()
        },
    )
    .unwrap()
    .assurance;
    assert_eq!(report.search.outcome, SearchOutcome::Interrupted);
    assert_eq!(model.steps.get(), 0);
    assert!(
        report
            .properties
            .iter()
            .all(|p| p.status == PropertyStatus::Missing)
    );
}
#[test]
fn undeclared_failure_and_skipped_property_block_strict_assurance() {
    for model in [
        Fixture {
            hidden_failure: true,
            ..Fixture::default()
        },
        Fixture {
            skip: true,
            ..Fixture::default()
        },
    ] {
        let report = run(&model, manifest(), SearchConfig::default()).assurance;
        assert!(!report.assess(AssurancePolicy::Strict).adequate);
        assert!(!report.observed_properties.is_empty());
    }
}
#[test]
fn checked_failure_edge_is_observed_before_search_stops() {
    let report = run(
        &Fixture {
            hidden_failure: true,
            ..Fixture::default()
        },
        manifest(),
        SearchConfig::default(),
    )
    .assurance;
    assert_eq!(report.search.outcome, SearchOutcome::Failed);
    assert_eq!(report.search.checked_transitions, 1);
    assert_eq!(
        report
            .families
            .iter()
            .find(|f| f.name == "advance")
            .unwrap()
            .status,
        FamilyStatus::Observed
    );
}
#[test]
fn model_error_preserves_prior_observations_and_partial_check_evidence() {
    let run = run(
        &Fixture {
            callback_error: true,
            ..Fixture::default()
        },
        manifest(),
        SearchConfig::default(),
    );
    assert!(run.search.is_err());
    assert_eq!(run.assurance.search.checked_transitions, 1);
    assert_eq!(run.assurance.properties[1].counts.passed, 2);
    assert_eq!(
        run.assurance.coverage[0].status,
        CoverageStatus::NotReachedBeforeStop
    );
    assert_eq!(run.assurance.callback_errors.len(), 1);
}
#[test]
fn step_error_retains_successful_prefix() {
    let run = run(
        &Fixture {
            step_error: true,
            ..Fixture::default()
        },
        manifest(),
        SearchConfig::default(),
    );
    assert!(run.search.is_err());
    assert_eq!(run.assurance.search.checked_transitions, 1);
    assert_eq!(
        run.assurance
            .families
            .iter()
            .find(|f| f.name == "advance")
            .unwrap()
            .status,
        FamilyStatus::Observed
    );
}
struct BadCoverage;
impl Coverage<Fixture> for BadCoverage {
    fn observe(
        &self,
        _: &u8,
        _: &Input,
        _: &TransitionRef<'_, u8, ()>,
        _: &[Input],
        hits: &mut CoverageHits,
    ) -> Result<(), ModelError> {
        hits.goal("visited_loop");
        Err(ModelError::new("observer failed"))
    }
}
#[test]
fn observer_error_does_not_change_search_or_commit_partial_hits() {
    let model = Fixture::default();
    let report = enumerate(
        &model,
        manifest(),
        BadCoverage,
        SearchConfig::default(),
        SearchLimits::default(),
    )
    .unwrap();
    assert_eq!(
        report.search.unwrap().termination,
        SearchTermination::GraphExhausted
    );
    assert_eq!(
        report.assurance.coverage[0].status,
        CoverageStatus::NotReachedBeforeStop
    );
    assert_eq!(model.steps.get(), 2);
    assert!(!report.assurance.assess(AssurancePolicy::Strict).adequate);
}
#[test]
fn excluded_event_observed_is_a_policy_contradiction() {
    let mut m = manifest();
    m.input_families[1].coverage = FamilyCoverage::Excluded {
        reason: "must not be modeled".into(),
    };
    let report = run(&Fixture::default(), m, SearchConfig::default()).assurance;
    assert!(matches!(
        report
            .families
            .iter()
            .find(|f| f.name == "loop")
            .unwrap()
            .status,
        FamilyStatus::ExclusionViolated { .. }
    ));
    assert!(!report.assess(AssurancePolicy::Strict).adequate);
}
#[test]
fn validation_rejects_duplicates_unsupported_version_and_empty_rationale() {
    let mut cases = vec![];
    let mut m = manifest();
    m.required_properties.push(m.required_properties[0].clone());
    cases.push(m);
    let mut m = manifest();
    m.assurance_version = 2;
    cases.push(m);
    let mut m = manifest();
    m.input_families[2].coverage = FamilyCoverage::Excluded {
        reason: "  ".into(),
    };
    cases.push(m);
    for m in cases {
        assert!(m.validate().is_err());
    }
}
#[test]
fn merge_is_order_independent_and_retains_budget_limited_jobs() {
    let full = run(&Fixture::default(), manifest(), SearchConfig::default()).assurance;
    let partial = run(
        &Fixture::default(),
        manifest(),
        SearchConfig {
            max_transitions: 1,
            ..SearchConfig::default()
        },
    )
    .assurance;
    let first = merge_reports(
        manifest(),
        Fixture::default().metadata(),
        [7, 8],
        [(8, partial.clone()), (7, full.clone())],
    )
    .unwrap();
    let second = merge_reports(
        manifest(),
        Fixture::default().metadata(),
        [8, 7],
        [(7, full), (8, partial)],
    )
    .unwrap();
    assert_eq!(first, second);
    assert_eq!(first.to_json(), second.to_json());
    assert_eq!(first.report.search.outcome, SearchOutcome::BudgetLimited);
    assert_eq!(first.report.coverage[0].status, CoverageStatus::Observed);
    assert!(!first.report.assess(AssurancePolicy::Strict).adequate);
}
#[test]
fn merge_never_hides_missing_jobs_or_mixes_builds() {
    let full = run(&Fixture::default(), manifest(), SearchConfig::default()).assurance;
    let report = merge_reports(
        manifest(),
        Fixture::default().metadata(),
        [1, 2],
        [(1, full.clone())],
    )
    .unwrap();
    assert_eq!(report.missing_jobs, vec![2]);
    assert!(!report.report.assess(AssurancePolicy::Strict).adequate);
    let mut wrong = full;
    wrong.metadata.build = "other-build".into();
    assert!(merge_reports(manifest(), Fixture::default().metadata(), [1], [(1, wrong)]).is_err());
}
#[test]
fn guided_search_observes_same_checked_transitions() {
    let mut m = manifest();
    m.required_search_outcome = RequiredSearchOutcome::Completed;
    let model = Fixture::default();
    let result = guided_fuzz(
        &model,
        m,
        Covers,
        FuzzConfig {
            cases: 2,
            max_steps: 2,
            ..FuzzConfig::default()
        },
        stateless::guided::GuidanceConfig::default(),
        stateless::guided::exact_state::<Fixture>(),
        RunLimits::default(),
    )
    .unwrap();
    assert!(result.assurance.assess(AssurancePolicy::Strict).adequate);
    assert_eq!(
        result.search.unwrap().transitions,
        result.assurance.search.checked_transitions
    );
    assert_eq!(model.steps.get(), 4);
    let budgets = &result.assurance.search.budgets;
    assert_eq!(budgets["guidance.algorithm"], stateless::guided::ALGORITHM);
    assert_eq!(budgets["guidance.max_corpus_entries"], "256");
    assert_eq!(budgets["guidance.max_corpus_inputs"], "25600");
    assert_eq!(budgets["guidance.max_features"], "100000");
    assert_eq!(budgets["guidance.max_features_per_observation"], "256");
    assert_eq!(budgets["guidance.feedback.name"], "exact-state");
    assert_eq!(budgets["guidance.feedback.version"], "1");
    assert_eq!(budgets["guidance.feedback.build"], "clone-eq-hash-v1");
}

#[test]
fn altered_or_missing_public_report_catalogs_fail_closed() {
    let full = run(&Fixture::default(), manifest(), SearchConfig::default()).assurance;
    let mut variants = Vec::new();
    let mut altered = full.clone();
    altered.properties.clear();
    variants.push(altered);
    let mut altered = full.clone();
    altered.families.clear();
    variants.push(altered);
    let mut altered = full.clone();
    altered.coverage.clear();
    variants.push(altered);
    let mut altered = full.clone();
    altered.properties[0].counts.passed += 1;
    variants.push(altered);
    let mut altered = full.clone();
    altered.manifest.required_properties.push(RequiredProperty {
        id: "new-unobserved".into(),
        phase: PropertyPhase::State,
    });
    variants.push(altered);
    let mut altered = full.clone();
    altered.coverage[0].goal.scope = GoalScope::History;
    variants.push(altered);
    let mut altered = full.clone();
    altered.metadata.name = "different".into();
    variants.push(altered);
    let mut altered = full.clone();
    altered.search.checked_transitions = 0;
    variants.push(altered);
    for report in variants {
        assert!(report.validate().is_err());
        assert!(!report.assess(AssurancePolicy::Strict).adequate);
        assert!(
            merge_reports(
                manifest(),
                Fixture::default().metadata(),
                [0],
                [(0, report)]
            )
            .is_err()
        );
    }
    assert!(full.validate().is_ok());
}

#[test]
fn changing_missing_family_to_observed_without_hit_evidence_fails_closed() {
    let mut requirements = manifest();
    requirements.input_families.push(InputFamily {
        name: "never_observed".into(),
        coverage: FamilyCoverage::Required,
    });
    let mut report = run(
        &Fixture::default(),
        requirements.clone(),
        SearchConfig::default(),
    )
    .assurance;
    assert!(report.search.checked_transitions > 0);
    assert!(report.validate().is_ok());
    assert!(!report.observed_families.contains("never_observed"));
    let family = report
        .families
        .iter_mut()
        .find(|family| family.name == "never_observed")
        .unwrap();
    assert_eq!(family.status, FamilyStatus::Missing);
    family.status = FamilyStatus::Observed;
    assert!(report.validate().is_err());
    assert!(!report.assess(AssurancePolicy::Strict).adequate);
    assert!(
        merge_reports(
            requirements,
            Fixture::default().metadata(),
            [0],
            [(0, report)]
        )
        .is_err()
    );
}

#[test]
fn promoting_unobserved_goals_without_hit_evidence_fails_closed() {
    for (scope, status) in [
        (
            GoalScope::Transition,
            CoverageStatus::UnreachableUnderDeclaredModel,
        ),
        (GoalScope::History, CoverageStatus::NotReachedBeforeStop),
    ] {
        let mut requirements = manifest();
        requirements.cover_goals.push(CoverGoal {
            id: "never_observed".into(),
            scope,
        });
        let mut report = run(
            &Fixture::default(),
            requirements.clone(),
            SearchConfig::default(),
        )
        .assurance;
        assert!(report.search.checked_transitions > 0);
        assert!(report.validate().is_ok());
        assert!(!report.observed_goals.contains("never_observed"));
        let goal = report
            .coverage
            .iter_mut()
            .find(|goal| goal.goal.id == "never_observed")
            .unwrap();
        assert_eq!(goal.status, status);
        goal.status = CoverageStatus::Observed;
        assert!(report.validate().is_err());
        assert!(!report.assess(AssurancePolicy::Strict).adequate);
        assert!(
            merge_reports(
                requirements,
                Fixture::default().metadata(),
                [0],
                [(0, report)]
            )
            .is_err()
        );
    }
}

#[test]
fn observed_hits_survive_merge_and_json_but_undeclared_hits_are_rejected() {
    let full = run(&Fixture::default(), manifest(), SearchConfig::default()).assurance;
    assert_eq!(
        full.observed_families
            .iter()
            .map(String::as_str)
            .collect::<Vec<_>>(),
        ["advance", "loop"]
    );
    assert_eq!(
        full.observed_goals
            .iter()
            .map(String::as_str)
            .collect::<Vec<_>>(),
        ["visited_loop"]
    );
    let merged = merge_reports(
        manifest(),
        Fixture::default().metadata(),
        [1, 0],
        [(1, full.clone()), (0, full.clone())],
    )
    .unwrap();
    assert_eq!(merged.report.observed_families, full.observed_families);
    assert_eq!(merged.report.observed_goals, full.observed_goals);
    assert!(merged.report.assess(AssurancePolicy::Strict).adequate);
    let encoded = merged.report.to_json();
    assert!(encoded.contains("\"observed_families\":[\"advance\",\"loop\"]"));
    assert!(encoded.contains("\"observed_goals\":[\"visited_loop\"]"));
    for family in [true, false] {
        let mut altered = full.clone();
        if family {
            altered.observed_families.insert("unknown".into());
        } else {
            altered.observed_goals.insert("unknown".into());
        }
        assert!(altered.validate().is_err());
        assert!(!altered.assess(AssurancePolicy::Strict).adequate);
    }
}

#[test]
fn sink_failure_after_all_jobs_run_blocks_campaign_adequacy() {
    let mut m = manifest();
    m.required_search_outcome = RequiredSearchOutcome::Completed;
    let result = run_campaign(
        stateless::campaign::CampaignConfig {
            jobs: 1,
            workers: 1,
            fuzz: FuzzConfig {
                cases: 1,
                max_steps: 2,
                ..FuzzConfig::default()
            },
            ..Default::default()
        },
        RunLimits::default(),
        m,
        Fixture::default().metadata(),
        |_| Ok(Fixture::default()),
        |_, _| Ok(Covers),
        |_| Err(ModelError::new("receipt storage unavailable")),
    )
    .unwrap();
    assert_eq!(result.campaign.finished, 1);
    assert_eq!(result.assurance.jobs.len(), 1);
    assert!(
        result.assurance.jobs[&0]
            .assess(AssurancePolicy::Strict)
            .adequate
    );
    assert_eq!(
        result.assurance.report.search.outcome,
        SearchOutcome::Failed
    );
    assert!(
        result
            .assurance
            .report
            .callback_errors
            .iter()
            .any(|e| e.contains("receipt storage unavailable"))
    );
    assert!(
        !result
            .assurance
            .report
            .assess(AssurancePolicy::Strict)
            .adequate
    );
    assert!(result.assurance.report.validate().is_ok());
}

#[test]
fn observed_bfs_history_is_each_edges_real_shortest_prefix() {
    struct Branch;
    impl Model for Branch {
        type State = u8;
        type Input = u8;
        type Output = ();
        fn metadata(&self) -> ModelMetadata {
            Fixture::default().metadata()
        }
        fn initial_state(&self) -> Result<u8, ModelError> {
            Ok(0)
        }
        fn step(&self, _: &u8, input: &u8) -> Result<Transition<u8, ()>, ModelError> {
            Ok(Transition::accepted(
                match input {
                    1 => 1,
                    2 => 2,
                    _ => 3,
                },
                vec![],
            ))
        }
        fn check_state(&self, _: &u8) -> Result<Vec<Check>, ModelError> {
            Ok(vec![Check::passed("branch")])
        }
    }
    impl Enumerate for Branch {
        fn inputs(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
            Ok(match state {
                0 => vec![1, 2],
                1 => vec![3],
                2 => vec![4],
                _ => vec![5],
            })
        }
    }
    #[derive(Default)]
    struct Prefixes(Vec<Vec<u8>>);
    impl stateless::explore::SearchObserver<Branch> for Prefixes {
        fn needs_history(&self) -> bool {
            true
        }
        fn transition(
            &mut self,
            _: &u8,
            _: &u8,
            _: &TransitionRef<'_, u8, ()>,
            _: &[Check],
            _: &[Check],
            history: &[u8],
        ) {
            self.0.push(history.to_vec());
        }
    }
    let mut prefixes = Prefixes::default();
    let result = stateless::explore::enumerate_observed(
        &Branch,
        SearchConfig::default(),
        SearchLimits::default(),
        &mut prefixes,
    )
    .unwrap();
    assert_eq!(result.termination, SearchTermination::GraphExhausted);
    assert_eq!(
        prefixes.0,
        [vec![1], vec![2], vec![1, 3], vec![2, 4], vec![1, 3, 5]]
    );
}
#[test]
fn campaign_uses_existing_scheduler_and_preserves_partial_reports() {
    let mut m = manifest();
    m.required_search_outcome = RequiredSearchOutcome::Completed;
    let result = run_campaign(
        stateless::campaign::CampaignConfig {
            jobs: 3,
            workers: 2,
            fuzz: FuzzConfig {
                cases: 1,
                max_steps: 2,
                ..FuzzConfig::default()
            },
            ..stateless::campaign::CampaignConfig::default()
        },
        RunLimits::default(),
        m,
        Fixture::default().metadata(),
        |_| Ok(Fixture::default()),
        |_, _| Ok(Covers),
        |_| Ok(()),
    )
    .unwrap();
    assert_eq!(result.campaign.finished, 3);
    assert_eq!(result.assurance.jobs.len(), 3);
    assert!(
        result
            .assurance
            .report
            .assess(AssurancePolicy::Strict)
            .adequate
    );
}
#[test]
fn campaign_factory_failure_is_explicitly_missing_qualification() {
    let mut m = manifest();
    m.required_search_outcome = RequiredSearchOutcome::Completed;
    let result = run_campaign(
        stateless::campaign::CampaignConfig {
            jobs: 2,
            fuzz: FuzzConfig {
                cases: 1,
                max_steps: 2,
                ..FuzzConfig::default()
            },
            ..stateless::campaign::CampaignConfig::default()
        },
        RunLimits::default(),
        m,
        Fixture::default().metadata(),
        |id| {
            if id == 0 {
                Err(ModelError::new("factory"))
            } else {
                Ok(Fixture::default())
            }
        },
        |_, _| Ok(Covers),
        |_| Ok(()),
    )
    .unwrap();
    assert_eq!(result.campaign.errors, 1);
    assert_eq!(result.assurance.missing_jobs, vec![0]);
    assert!(
        !result
            .assurance
            .report
            .assess(AssurancePolicy::Strict)
            .adequate
    );
}
#[test]
fn report_json_escapes_untrusted_metadata_and_remains_deterministic() {
    let mut m = manifest();
    m.provenance.manifest = "quoted\"\\\n\t\u{0} unicode ☃".into();
    let report = run(&Fixture::default(), m, SearchConfig::default()).assurance;
    let json = report.to_json();
    assert!(json.contains("quoted\\\"\\\\\\n\\t\\u0000 unicode ☃"));
    assert!(!json.contains('\u{0}'));
    assert_eq!(json, report.to_json());
}

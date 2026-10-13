use stateless::assurance::{PropertyPhase, RequiredProperty};
use stateless::research::*;
use stateless::{Check, Enumerate, Model, ModelError, ModelMetadata, Transition, TransitionRef};
use std::cell::Cell;

fn proposal() -> Proposal {
    Proposal {
        name: "finite-example-projection".into(),
        version: 1,
        build: "fixture-proposal-v1".into(),
        assumptions: Assumptions {
            established: true,
            finite_domain: "explicit fixture states; every successor included".into(),
            equivalence: "only behaviorally interchangeable states may share a key".into(),
            effects: "exact ordered output equality".into(),
            transition_properties: "same named checks and results for each input".into(),
            queue_order: "no hidden queue; represented queues retain exact order".into(),
            oracle_history: "history affecting properties remains represented in state".into(),
            visible_timing: "no hidden wall clock; represented logical time remains observable"
                .into(),
        },
        required_properties: vec![
            RequiredProperty {
                id: "state".into(),
                phase: PropertyPhase::State,
            },
            RequiredProperty {
                id: "transition".into(),
                phase: PropertyPhase::Transition,
            },
        ],
    }
}
fn enabled() -> ExperimentConfig {
    ExperimentConfig {
        enabled: true,
        ..ExperimentConfig::default()
    }
}
#[derive(Default)]
struct Toggle {
    steps: Cell<u64>,
    ordered_effects: bool,
    skip: bool,
    error: bool,
}
impl Model for Toggle {
    type State = u8;
    type Input = ();
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "toggle-research".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "test-source".into(),
        }
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u8, _: &()) -> Result<Transition<u8, u8>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        if self.error && *state == 1 {
            return Err(ModelError::new("fixture error"));
        }
        Ok(Transition::accepted(
            1 - *state,
            if self.ordered_effects && *state == 1 {
                vec![2, 1]
            } else {
                vec![1, 2]
            },
        ))
    }
    fn check_state(&self, _: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if self.skip {
            Check::skipped("state", "not evaluated")
        } else {
            Check::passed("state")
        }])
    }
    fn check_transition(
        &self,
        _: &u8,
        _: &(),
        _: &TransitionRef<'_, u8, u8>,
    ) -> Result<Vec<Check>, ModelError> {
        Ok(vec![Check::passed("transition")])
    }
}
impl Enumerate for Toggle {
    fn inputs(&self, _: &u8) -> Result<Vec<()>, ModelError> {
        Ok(vec![()])
    }
}
#[test]
fn default_is_disabled_without_executing_the_model() {
    let model = Toggle::default();
    let report = compare_equivalence(
        &model,
        &[0, 1],
        proposal(),
        ExperimentConfig::default(),
        |_| (),
    )
    .unwrap();
    assert_eq!(report.status, ExperimentStatus::Disabled);
    assert_eq!(model.steps.get(), 0);
    assert!(report.reduced.is_none());
}
#[test]
fn unestablished_assumptions_disable_the_experiment() {
    let model = Toggle::default();
    let mut p = proposal();
    p.assumptions.established = false;
    let report = compare_equivalence(&model, &[0, 1], p, enabled(), |_| ()).unwrap();
    assert_eq!(report.status, ExperimentStatus::AssumptionsNotEstablished);
    assert_eq!(model.steps.get(), 0);
}
#[test]
fn equivalent_toggle_states_agree_with_one_materialization() {
    let model = Toggle::default();
    let report = compare_equivalence(&model, &[0, 1], proposal(), enabled(), |_| ()).unwrap();
    assert_eq!(report.status, ExperimentStatus::AgreesWithinDeclaredFixture);
    assert_eq!(model.steps.get(), 2);
    assert_eq!(report.exact.as_ref().unwrap().states.len(), 2);
    assert_eq!(report.reduced.as_ref().unwrap().states.len(), 1);
    assert_eq!(
        qualification_parameters(&report)["research.production_pruning_enabled"],
        "false"
    );
}
#[test]
fn ordered_effect_difference_rejects_projection() {
    let report = compare_equivalence(
        &Toggle {
            ordered_effects: true,
            ..Toggle::default()
        },
        &[0, 1],
        proposal(),
        enabled(),
        |_| (),
    )
    .unwrap();
    assert_eq!(report.status, ExperimentStatus::Counterexample);
    assert_eq!(report.counterexamples[0].reason, "ordered effects differ");
}
#[test]
fn edge_pair_and_state_budgets_never_count_as_agreement() {
    for config in [
        ExperimentConfig {
            max_edges: 1,
            ..enabled()
        },
        ExperimentConfig {
            max_states: 1,
            ..enabled()
        },
        ExperimentConfig {
            max_equivalence_pairs: 0,
            ..enabled()
        },
    ] {
        let report =
            compare_equivalence(&Toggle::default(), &[0, 1], proposal(), config, |_| ()).unwrap();
        assert_eq!(report.status, ExperimentStatus::Incomplete);
    }
}
#[test]
fn domain_must_be_closed_and_exact_states_unique() {
    let report =
        compare_equivalence(&Toggle::default(), &[0], proposal(), enabled(), |_| ()).unwrap();
    assert_eq!(report.status, ExperimentStatus::Incomplete);
    assert!(report.limitation.contains("outside"));
    assert!(
        compare_equivalence(
            &Toggle::default(),
            &[0, 0, 1],
            proposal(),
            enabled(),
            |_| ()
        )
        .is_err()
    );
}
#[test]
fn missing_or_skipped_properties_never_qualify() {
    let report = compare_equivalence(
        &Toggle {
            skip: true,
            ..Toggle::default()
        },
        &[0, 1],
        proposal(),
        enabled(),
        |_| (),
    )
    .unwrap();
    assert_eq!(report.status, ExperimentStatus::Incomplete);
    let mut p = proposal();
    p.required_properties.push(RequiredProperty {
        id: "forgotten".into(),
        phase: PropertyPhase::State,
    });
    let report = compare_equivalence(&Toggle::default(), &[0, 1], p, enabled(), |_| ()).unwrap();
    assert_eq!(report.status, ExperimentStatus::Incomplete);
    assert_eq!(report.missing_properties[0].id, "forgotten");
}
#[test]
fn callback_error_returns_partial_unqualified_evidence() {
    let report = compare_equivalence(
        &Toggle {
            error: true,
            ..Toggle::default()
        },
        &[0, 1],
        proposal(),
        enabled(),
        |_| (),
    )
    .unwrap();
    assert_eq!(report.status, ExperimentStatus::Incomplete);
    assert_eq!(report.materialized_edges, 1);
    assert!(report.exact.is_none());
    assert!(report.limitation.contains("fixture error"));
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Event {
    Left,
    Right,
    Observe,
}
struct Concurrent;
impl Model for Concurrent {
    type State = u8;
    type Input = Event;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "two-machines".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "test-source".into(),
        }
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u8, input: &Event) -> Result<Transition<u8, ()>, ModelError> {
        Ok(Transition::accepted(
            match input {
                Event::Left => state | 1,
                Event::Right => state | 2,
                Event::Observe => *state,
            },
            vec![],
        ))
    }
    fn check_state(&self, _: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![Check::passed("state")])
    }
    fn check_transition(
        &self,
        before: &u8,
        input: &Event,
        _: &TransitionRef<'_, u8, ()>,
    ) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if *before == 2 && *input == Event::Observe {
            Check::failed(
                "transition",
                "right machine published before left dependency",
            )
        } else {
            Check::passed("transition")
        }])
    }
}
impl Enumerate for Concurrent {
    fn inputs(&self, _: &u8) -> Result<Vec<Event>, ModelError> {
        Ok(vec![Event::Left, Event::Right, Event::Observe])
    }
}
#[test]
fn naive_population_count_projection_hides_a_replayable_concurrent_failure() {
    let report = compare_equivalence(&Concurrent, &[0, 1, 2, 3], proposal(), enabled(), |s| {
        s.count_ones()
    })
    .unwrap();
    assert_eq!(report.status, ExperimentStatus::Counterexample);
    assert_eq!(report.missing_violations.len(), 1);
    let witness = &report.missing_violations[0];
    assert_eq!(witness.inputs, vec![Event::Right, Event::Observe]);
    let mut state = Concurrent.initial_state().unwrap();
    let mut failed = false;
    for input in &witness.inputs {
        let next = Concurrent.step(&state, input).unwrap();
        failed |= Concurrent
            .check_transition(&state, input, &next.as_ref())
            .unwrap()
            .iter()
            .any(Check::is_failure);
        state = next.state;
    }
    assert!(failed);
    assert!(report.reduced.unwrap().violations.is_empty());
}
#[test]
fn exact_identity_retains_the_same_failure_in_both_arms() {
    let report =
        compare_equivalence(&Concurrent, &[0, 1, 2, 3], proposal(), enabled(), |s| *s).unwrap();
    assert_eq!(report.status, ExperimentStatus::AgreesWithinDeclaredFixture);
    assert_eq!(
        report.exact.as_ref().unwrap().violations,
        report.reduced.as_ref().unwrap().violations
    );
    assert_eq!(report.exact.unwrap().violations.len(), 1);
}

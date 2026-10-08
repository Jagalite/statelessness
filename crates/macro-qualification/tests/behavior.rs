use macro_qualification::*;
use stateless::automatic::Auto;
use stateless::*;
use statelessness_macros::model;
use std::cell::Cell;

#[test]
fn every_request_state_matches_handwritten_and_preserves_stale_delivery() {
    for inject_bug in [false, true] {
        let hand = demo::RequestModel { inject_bug };
        let generated = Request(hand);
        let mut seen = std::collections::HashSet::new();
        let mut queue = std::collections::VecDeque::from([hand.initial_state().unwrap()]);
        while let Some(s) = queue.pop_front() {
            if !seen.insert(s.clone()) {
                continue;
            }
            assert_eq!(generated.inputs(&s).unwrap(), hand.inputs(&s).unwrap());
            assert_eq!(
                generated.check_state(&s).unwrap(),
                hand.check_state(&s).unwrap()
            );
            for i in hand.inputs(&s).unwrap() {
                let h = hand.step(&s, &i).unwrap();
                let g = generated.step(&s, &i).unwrap();
                assert_eq!(g, h);
                assert_eq!(
                    generated.check_transition(&s, &i, &g.as_ref()).unwrap(),
                    hand.check_transition(&s, &i, &h.as_ref()).unwrap()
                );
                assert!(deliveries(&generated, &s).unwrap().contains(&i));
                queue.push_back(h.state);
            }
        }
        assert!(seen.len() > 10);
        let m = Auto::new(generated);
        let mut s = m.initial_state().unwrap();
        for i in [
            demo::Input::Start,
            demo::Input::Cancel,
            demo::Input::Complete(1),
        ] {
            assert!(m.is_enabled(&s, &i).unwrap());
            s = m.step(&s, &i).unwrap().state;
        }
        assert_eq!(
            m.check_state(&s).unwrap().iter().any(Check::is_failure),
            inject_bug
        );
    }
}
#[test]
fn macro_search_fuzz_shrink_guidance_and_replay() {
    let m = Auto::new(Request(demo::RequestModel::buggy()));
    let report = stateless::explore::enumerate(&m, Default::default()).unwrap();
    let failure = report.failure.unwrap();
    let hand =
        stateless::explore::enumerate(&demo::RequestModel::buggy(), Default::default()).unwrap();
    assert_eq!(failure.inputs, hand.failure.unwrap().inputs);
    let fuzz = stateless::explore::FuzzConfig {
        seed: 31,
        cases: 100,
        max_steps: 20,
        ..Default::default()
    };
    let report = stateless::explore::fuzz(&m, fuzz.clone()).unwrap();
    assert!(report.failure.is_some());
    let shrunk =
        stateless::explore::shrink(&m, &report.failure.unwrap(), Default::default()).unwrap();
    let trace =
        stateless::execution::record(&m, shrunk.minimized.inputs.clone(), Default::default(), 100)
            .unwrap();
    let mut bytes = Vec::new();
    trace.write_to(&mut bytes).unwrap();
    let trace = stateless::trace::Trace::read_from(bytes.as_slice(), &Default::default()).unwrap();
    let replay = stateless::execution::replay(&m, &trace, Default::default()).unwrap();
    assert!(replay.failure_reproduced);
    assert_eq!(replay.outcome, stateless::execution::ReplayOutcome::Exact);
    let guided = stateless::guided::fuzz(
        &m,
        fuzz,
        Default::default(),
        stateless::guided::exact_state::<Auto<Request>>(),
    )
    .unwrap();
    assert!(guided.failure.is_some());
    let fixed = Request(demo::RequestModel::fixed());
    let report = stateless::explore::enumerate(&fixed, Default::default()).unwrap();
    assert_eq!(
        report.termination,
        stateless::explore::SearchTermination::GraphExhausted
    );
    assert!(report.failure.is_none());
    assert_eq!(report.skipped_checks, 0);
}
struct Count {
    steps: Cell<u32>,
}
#[model(state=u8,input=u8,output=u8)]
impl Count {
    /// Forward normal method documentation unchanged.
    #[stateless(metadata)]
    fn identity(&self) -> ModelMetadata {
        Counter.metadata()
    }
    #[stateless(initial)]
    fn initial(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    #[stateless(step)]
    fn reduce(&self, s: &u8, i: &u8) -> Result<Transition<u8, u8>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        Ok(Transition::accepted(s + i, vec![*i]))
    }
    #[stateless(state_check(id = "stable.failed"))]
    fn renamed_property(&self, _: &u8) -> Result<CheckStatus, ModelError> {
        Ok(CheckStatus::Failed("deliberate".into()))
    }
    #[stateless(state_check(id = "stable.skipped"))]
    fn skipped(&self, _: &u8) -> Result<CheckStatus, ModelError> {
        Ok(CheckStatus::Skipped("missing history".into()))
    }
    #[stateless(state_check(id = "stable.error"))]
    fn error(&self, _: &u8) -> Result<CheckStatus, ModelError> {
        Err(ModelError::new("checker error"))
    }
}
#[test]
fn exactly_once_partial_error_and_lazy_details() {
    let model = Count {
        steps: Cell::new(0),
    };
    model.step(&0, &1).unwrap();
    assert_eq!(model.steps.get(), 1);
    let mut checks = vec![Check::passed("prior")];
    assert!(
        model
            .check_state_into(&0, &mut CheckSink::new(&mut checks))
            .is_err()
    );
    assert_eq!(checks.len(), 3);
    assert_eq!(checks[1].id, "stable.failed");
    assert!(matches!(checks[2].status, CheckStatus::Skipped(_)));
    let mut predicates = 0;
    let mut details = 0;
    let check = stateless::check!(
        "lazy",
        {
            predicates += 1;
            true
        },
        "{}",
        {
            details += 1;
            details
        }
    );
    assert!(!check.is_failure());
    assert_eq!(predicates, 1);
    assert_eq!(details, 0);
}

#[test]
fn campaigns_use_generated_adapter() {
    let mut received = 0;
    let report = stateless::campaign::run_campaign(
        stateless::campaign::CampaignConfig {
            jobs: 4,
            workers: 2,
            fuzz: stateless::explore::FuzzConfig {
                cases: 20,
                max_steps: 10,
                ..Default::default()
            },
            ..Default::default()
        },
        Default::default(),
        |_| Ok(Auto::new(Request(demo::RequestModel::fixed()))),
        |_| {
            received += 1;
            Ok(())
        },
    )
    .unwrap();
    assert_eq!(received, 4);
    assert_eq!(report.finished, 4);
}

struct Observation;
impl Oracle<Count> for Observation {
    type State = u8;
    fn metadata(&self) -> ModelMetadata {
        Counter.metadata()
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn advance(&self, h: &u8, _: &u8, _: &[u8], _: &Disposition) -> Result<u8, ModelError> {
        Ok(h + 1)
    }
    fn check_state_into(&self, _: &u8, _: &u8, _: &mut CheckSink<'_>) -> Result<(), ModelError> {
        Ok(())
    }
}
#[test]
fn observing_generated_model_does_not_execute_reducer_again() {
    let model = WithOracle::new(
        Count {
            steps: Cell::new(0),
        },
        Observation,
    );
    let before = model.initial_state().unwrap();
    let actual = model.model().step(&before.model, &1).unwrap();
    let observed = model.observe_transition(&before, &1, actual).unwrap();
    assert_eq!(model.model().steps.get(), 1);
    assert_eq!(observed.state.oracle, 1);
    assert_eq!(observed.state.model, 1);
}

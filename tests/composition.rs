use stateless::composition::{self, Address, Input, Pair, PairOutput, PairState, Wiring};
use stateless::*;
#[derive(Clone, Copy)]
struct Child {
    send: bool,
}
impl Model for Child {
    type State = u8;
    type Input = u8;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        let mut m = demo::RequestModel::fixed().metadata();
        m.name = format!("child:{}", self.send);
        m
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, _: &u8, i: &u8) -> Result<Transition<u8, u8>, ModelError> {
        if *i > 1 {
            return Err(ModelError::new("invalid command"));
        }
        Ok(Transition::accepted(
            *i,
            if self.send && *i == 1 {
                vec![1]
            } else {
                vec![]
            },
        ))
    }
    fn check_state(&self, _: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![Check::passed("local")])
    }
}
impl Enumerate for Child {
    fn inputs(&self, _: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![0, 1])
    }
}
struct Route;
impl Wiring<Child, Child> for Route {
    fn metadata(&self) -> ModelMetadata {
        Child { send: false }.metadata()
    }
    fn messages(&self, o: &PairOutput<Child, Child>) -> Result<Vec<Address<u8, u8>>, ModelError> {
        Ok(match o {
            Address::Left(v) => vec![Address::Right(*v)],
            _ => vec![],
        })
    }
    fn check_state_into(
        &self,
        s: &PairState<Child, Child>,
        c: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        c.push(stateless::check!(
            "ownership",
            s.right == 0 || s.left == 1,
            "cancelled owner has active peer"
        ));
        Ok(())
    }
}
fn pair() -> Pair<Child, Child, Route> {
    Pair {
        left: Child { send: true },
        right: Child { send: false },
        wiring: Route,
        max_pending: 3,
    }
}
type SystemTransition = Transition<PairState<Child, Child>, Address<u8, u8>>;
fn handwritten(
    s: &PairState<Child, Child>,
    i: &Input<u8, u8>,
) -> Result<SystemTransition, ModelError> {
    let mut state = s.clone();
    let (left, value) = match i {
        Input::Local(Address::Left(v)) => (true, *v),
        Input::Local(Address::Right(v)) => (false, *v),
        Input::Deliver(n) => {
            let message = state
                .pending
                .get(*n as usize)
                .ok_or_else(|| ModelError::new("pending message index out of range"))?
                .clone();
            state.pending.remove(*n as usize);
            match message {
                Address::Left(v) => (true, v),
                Address::Right(v) => (false, v),
            }
        }
    };
    if value > 1 {
        return Err(ModelError::new("invalid command"));
    }
    let mut outputs = vec![];
    if left {
        state.left = value;
        if value == 1 {
            if state.pending.len() == 3 {
                return Err(ModelError::new("pending message bound exceeded"));
            }
            state.pending.push(Address::Right(1));
            outputs.push(Address::Left(1));
        }
    } else {
        state.right = value;
    }
    Ok(Transition::accepted(state, outputs))
}
#[test]
fn routing_matches_handwritten_for_bounded_interleavings() {
    let m = pair();
    let mut seen = std::collections::HashSet::new();
    let mut queue = std::collections::VecDeque::from([m.initial_state().unwrap()]);
    while let Some(s) = queue.pop_front() {
        if !seen.insert(s.clone()) {
            continue;
        }
        for i in m.inputs(&s).unwrap() {
            let h = handwritten(&s, &i);
            let g = m.step(&s, &i);
            assert_eq!(g, h);
            if let Ok(t) = g {
                queue.push_back(t.state);
            }
        }
    }
    assert!(seen.len() > 10);
}
#[test]
fn delayed_delivery_cancellation_namespaces_and_replay() {
    let m = pair();
    let initial = m.initial_state().unwrap();
    let started = m
        .step(&initial, &Input::Local(Address::Left(1)))
        .unwrap()
        .state;
    assert_eq!(started.right, 0);
    assert_eq!(started.pending, [Address::Right(1)]);
    let mut no_queue = started.clone();
    no_queue.pending.clear();
    assert_ne!(started, no_queue);
    let inputs = vec![
        Input::Local(Address::Left(1)),
        Input::Local(Address::Left(0)),
        Input::Deliver(0),
    ];
    let trace = stateless::execution::record(&m, inputs, Default::default(), 10).unwrap();
    let report = stateless::execution::replay(&m, &trace, Default::default()).unwrap();
    assert!(report.failure_reproduced);
    assert_eq!(report.outcome, stateless::execution::ReplayOutcome::Exact);
    let checks = m.check_state(&initial).unwrap();
    assert_eq!(
        checks.iter().map(|c| c.id.as_str()).collect::<Vec<_>>(),
        ["left::local", "right::local", "global::ownership"]
    );
    let state = m.decode_state(&m.encode_state(&started).unwrap()).unwrap();
    assert_eq!(state, started);
    assert!(m.step(&initial, &Input::Deliver(0)).is_err());
    let reversed = composition::State {
        left: 1,
        right: 0,
        pending: vec![Address::Right(1), Address::Right(0)],
    };
    let a = m.step(&reversed, &Input::Deliver(0)).unwrap();
    let b = m.step(&reversed, &Input::Deliver(1)).unwrap();
    assert_ne!(a.state, b.state);
}

struct Duplicate;
impl Wiring<Child, Child> for Duplicate {
    fn metadata(&self) -> ModelMetadata {
        Route.metadata()
    }
    fn messages(&self, _: &PairOutput<Child, Child>) -> Result<Vec<Address<u8, u8>>, ModelError> {
        Ok(vec![])
    }
    fn check_state_into(
        &self,
        _: &PairState<Child, Child>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.push(Check::passed("same"));
        checks.push(Check::failed("same", "collision"));
        Ok(())
    }
}
#[test]
fn registry_collisions_are_errors_and_prior_checks_survive() {
    let model = Pair {
        left: Child { send: true },
        right: Child { send: false },
        wiring: Duplicate,
        max_pending: 3,
    };
    let mut checks = Vec::new();
    let error = model
        .check_state_into(
            &model.initial_state().unwrap(),
            &mut CheckSink::new(&mut checks),
        )
        .unwrap_err();
    assert!(error.0.contains("duplicate composed property id"));
    assert_eq!(checks.len(), 3);
    assert_eq!(checks[2].id, "global::same");
}

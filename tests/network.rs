use stateless::network::{
    self, Address, DeliveryOrder, Input, MachineId, Message, Network, NetworkState, QueuePolicy,
    Route, Wiring,
};
use stateless::value_codec::{TraceDecode, TraceEncode};
use stateless::*;

const COORDINATOR: MachineId = MachineId(10);
const SOURCE: MachineId = MachineId(20);
const TARGET: MachineId = MachineId(30);
const START: u8 = 1;
const RELEASE: u8 = 2;
const CLAIM: u8 = 3;
const ACK: u8 = 4;
const REVOKE: u8 = 10;
const GRANT: u8 = 11;
const RELEASED: u8 = 12;
const CLAIMED: u8 = 13;

#[derive(Clone, Copy)]
struct Worker {
    id: MachineId,
    faulty: bool,
}
impl Model for Worker {
    type State = u8;
    type Input = u8;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        let mut m = demo::RequestModel::fixed().metadata();
        m.name = format!("handoff-role-{}-faulty={}", self.id.0, self.faulty);
        m
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(u8::from(self.id == SOURCE))
    }
    fn step(&self, s: &u8, i: &u8) -> Result<Transition<u8, u8>, ModelError> {
        match (self.id, *s, *i) {
            (COORDINATOR, 0, START) => Ok(Transition::accepted(
                1,
                if self.faulty {
                    vec![REVOKE, GRANT]
                } else {
                    vec![REVOKE]
                },
            )),
            (COORDINATOR, 1, ACK) => Ok(Transition::accepted(
                2,
                if self.faulty { vec![] } else { vec![GRANT] },
            )),
            (SOURCE, 1, RELEASE) => Ok(Transition::accepted(0, vec![RELEASED])),
            (TARGET, 0, CLAIM) => Ok(Transition::accepted(1, vec![CLAIMED])),
            _ => Ok(Transition {
                state: *s,
                outputs: vec![],
                disposition: Disposition::Ignored("not enabled".into()),
            }),
        }
    }
    fn check_state(&self, s: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![stateless::check!(
            "valid_role_state",
            *s <= if self.id == COORDINATOR { 2 } else { 1 },
            "invalid local state"
        )])
    }
    fn check_transition(
        &self,
        _: &u8,
        _: &u8,
        _: &TransitionRef<'_, u8, u8>,
    ) -> Result<Vec<Check>, ModelError> {
        Ok(vec![Check::passed("local_transition")])
    }
}
impl Enumerate for Worker {
    fn inputs(&self, s: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(if self.id == COORDINATOR && *s == 0 {
            vec![START]
        } else {
            vec![]
        })
    }
}
struct Routes;
impl Wiring<Worker> for Routes {
    fn metadata(&self) -> ModelMetadata {
        demo::RequestModel::fixed().metadata()
    }
    fn routes(&self, o: &Address<u8>) -> Result<Vec<Route<u8>>, ModelError> {
        let route = match (o.machine, o.value) {
            (COORDINATOR, REVOKE) => Some((SOURCE, RELEASE)),
            (COORDINATOR, GRANT) => Some((TARGET, CLAIM)),
            (SOURCE, RELEASED) => Some((COORDINATOR, ACK)),
            _ => None,
        };
        Ok(route
            .map(|(destination, input)| Route {
                destination,
                channel: 0,
                input,
            })
            .into_iter()
            .collect())
    }
    fn check_state_into(
        &self,
        s: &NetworkState<Worker>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        let owners =
            u16::from(*s.machine(SOURCE).unwrap()) + u16::from(*s.machine(TARGET).unwrap());
        checks.push(stateless::check!(
            "one_owner",
            owners <= 1,
            "source and target both own the resource"
        ));
        Ok(())
    }
    fn check_transition_into(
        &self,
        _: &NetworkState<Worker>,
        _: &Input<u8>,
        _: &TransitionRef<'_, NetworkState<Worker>, Address<u8>>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.push(Check::passed("handoff_transition"));
        Ok(())
    }
}
type System = Network<Worker, Routes>;
fn system(faulty: bool, policy: QueuePolicy) -> System {
    Network::new(
        [TARGET, COORDINATOR, SOURCE].map(|id| (id, Worker { id, faulty })),
        Routes,
        policy,
    )
    .unwrap()
}
fn start() -> Input<u8> {
    Input::Local(Address {
        machine: COORDINATOR,
        value: START,
    })
}

/// Independent ownership history derives changes from observed releases/claims,
/// never from the application's next state. Initial source ownership is specified.
struct Ownership;
impl Oracle<System> for Ownership {
    type State = u8;
    fn metadata(&self) -> ModelMetadata {
        demo::RequestModel::fixed().metadata()
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(1)
    }
    fn advance(
        &self,
        before: &u8,
        _: &Input<u8>,
        outputs: &[Address<u8>],
        _: &Disposition,
    ) -> Result<u8, ModelError> {
        let mut owners = *before;
        for o in outputs {
            match (o.machine, o.value) {
                (SOURCE, RELEASED) => owners &= !1,
                (TARGET, CLAIMED) => owners |= 2,
                _ => {}
            }
        }
        Ok(owners)
    }
    fn check_state_into(
        &self,
        owners: &u8,
        actual: &NetworkState<Worker>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.push(stateless::check!(
            "ownership.history",
            owners.count_ones() <= 1,
            "overlapping ownership effects"
        ));
        let actual = *actual.machine(SOURCE).unwrap() | (*actual.machine(TARGET).unwrap() << 1);
        checks.push(stateless::check!(
            "ownership.correspondence",
            actual == *owners,
            "state disagrees with ownership history"
        ));
        Ok(())
    }
}
impl OracleCodec<System> for Ownership {
    fn encode_history(&self, h: &u8) -> Result<Vec<u8>, ModelError> {
        h.trace_bytes(1)
    }
    fn decode_history(&self, b: &[u8]) -> Result<u8, ModelError> {
        u8::from_trace(b, Default::default())
    }
}

#[test]
fn three_machine_defect_is_global_and_replays_with_independent_history() {
    let m = WithOracle::new(system(true, QueuePolicy::default()), Ownership);
    let initial = m.initial_state().unwrap();
    let issued = m.step(&initial, &start()).unwrap();
    assert_eq!(
        issued.outputs.iter().map(|o| o.value).collect::<Vec<_>>(),
        [REVOKE, GRANT]
    );
    assert_eq!(
        *issued.state.model.machine(TARGET).unwrap(),
        0,
        "routing must not drain the queue"
    );
    assert_eq!(issued.state.model.pending.len(), 2);
    let started = m
        .decode_state(&m.encode_state(&issued.state).unwrap())
        .unwrap();
    assert_eq!(started, issued.state);
    // Different channels may interleave even with per-channel FIFO.
    let bad = m.step(&started, &Input::Deliver(1)).unwrap();
    let checks = m.check_state(&bad.state).unwrap();
    assert!(
        checks
            .iter()
            .filter(|c| c.id.as_str().starts_with("machine/"))
            .all(|c| !c.is_failure())
    );
    assert!(
        checks
            .iter()
            .any(|c| c.id == "global::one_owner" && c.is_failure())
    );
    assert!(
        checks
            .iter()
            .any(|c| c.id == "ownership.history" && c.is_failure())
    );
    let trace =
        execution::record(&m, vec![start(), Input::Deliver(1)], Default::default(), 8).unwrap();
    let replay = execution::replay(&m, &trace, Default::default()).unwrap();
    assert!(replay.failure_reproduced);
    assert_eq!(replay.outcome, execution::ReplayOutcome::Exact);
}

#[test]
fn acknowledged_handoff_exhausts_small_unreduced_graph_without_double_owner() {
    let m = WithOracle::new(system(false, QueuePolicy::default()), Ownership);
    let mut seen = std::collections::HashSet::new();
    let mut queue = std::collections::VecDeque::from([m.initial_state().unwrap()]);
    while let Some(s) = queue.pop_front() {
        if !seen.insert(s.clone()) {
            continue;
        }
        assert!(m.check_state(&s).unwrap().iter().all(|c| !c.is_failure()));
        for input in m.inputs(&s).unwrap() {
            let t = m.step(&s, &input).unwrap();
            assert!(
                m.check_transition(&s, &input, &t.as_ref())
                    .unwrap()
                    .iter()
                    .all(|c| !c.is_failure())
            );
            queue.push_back(m.decode_state(&m.encode_state(&t.state).unwrap()).unwrap());
        }
    }
    assert_eq!(seen.len(), 5);
    assert!(seen.iter().any(|s| *s.model.machine(TARGET).unwrap() == 1));
}

#[test]
fn delivery_drop_duplicate_and_bounds_are_explicit() {
    let m = system(
        true,
        QueuePolicy {
            allow_drop: true,
            allow_duplicate: true,
            max_pending: 3,
            max_per_channel: 2,
            ..Default::default()
        },
    );
    let initial = m.initial_state().unwrap();
    let pending = m.step(&initial, &start()).unwrap().state;
    let duplicated = m.step(&pending, &Input::Duplicate(0)).unwrap();
    assert_eq!(duplicated.state.pending.len(), 3);
    assert!(duplicated.outputs.is_empty());
    assert!(
        m.step(&duplicated.state, &Input::Deliver(2))
            .unwrap_err()
            .0
            .contains("FIFO")
    );
    assert!(
        !m.inputs(&duplicated.state)
            .unwrap()
            .contains(&Input::Deliver(2))
    );
    assert!(
        m.step(&duplicated.state, &Input::Duplicate(0))
            .unwrap_err()
            .0
            .contains("incomplete")
    );
    let dropped = m.step(&duplicated.state, &Input::Drop(0)).unwrap();
    assert_eq!(dropped.state.pending.len(), 2);
    assert!(
        m.inputs(&dropped.state)
            .unwrap()
            .contains(&Input::Deliver(1))
    );
    assert_eq!(dropped.state.machines, pending.machines);
    let unordered = system(
        true,
        QueuePolicy {
            delivery: DeliveryOrder::Unordered,
            allow_drop: true,
            allow_duplicate: true,
            max_pending: 3,
            max_per_channel: 2,
            ..Default::default()
        },
    );
    assert!(
        unordered
            .step(&duplicated.state, &Input::Deliver(2))
            .is_ok()
    );
    for input in [
        start(),
        Input::Deliver(1),
        Input::Drop(0),
        Input::Duplicate(0),
    ] {
        assert_eq!(
            m.decode_input(&m.encode_input(&input).unwrap()).unwrap(),
            input
        );
    }
    let disabled = system(true, Default::default());
    assert!(disabled.step(&pending, &Input::Drop(0)).is_err());
    assert!(
        disabled
            .decode_input(&m.encode_input(&Input::Duplicate(0)).unwrap())
            .is_err()
    );
    let bounded = system(
        true,
        QueuePolicy {
            max_pending: 1,
            ..Default::default()
        },
    );
    assert!(
        bounded
            .step(&initial, &start())
            .unwrap_err()
            .0
            .contains("incomplete")
    );
    assert_eq!(
        initial,
        bounded.initial_state().unwrap(),
        "failed atomic step cannot mutate caller state"
    );
}

#[test]
fn identity_order_malformed_addresses_and_canonical_codec() {
    let m = system(false, Default::default());
    let reordered = Network::new(
        [COORDINATOR, SOURCE, TARGET].map(|id| (id, Worker { id, faulty: false })),
        Routes,
        Default::default(),
    )
    .unwrap();
    assert_eq!(m.metadata(), reordered.metadata());
    let initial = m.initial_state().unwrap();
    let checks = m.check_state(&initial).unwrap();
    assert_eq!(
        checks.iter().map(|c| c.id.as_str()).collect::<Vec<_>>(),
        [
            "machine/10::valid_role_state",
            "machine/20::valid_role_state",
            "machine/30::valid_role_state",
            "global::one_owner"
        ]
    );
    assert!(
        Network::new(
            [(
                SOURCE,
                Worker {
                    id: SOURCE,
                    faulty: false
                }
            ); 2],
            Routes,
            Default::default()
        )
        .is_err()
    );
    let unknown = Input::Local(Address {
        machine: MachineId(999),
        value: START,
    });
    assert!(m.step(&initial, &unknown).is_err());
    assert!(m.decode_input(&m.encode_input(&unknown).unwrap()).is_err());
    let mut bad = initial.clone();
    bad.machines.swap(0, 1);
    assert!(m.encode_state(&bad).is_err());
    assert!(m.decode_state(&bad.trace_bytes(4096).unwrap()).is_err());
    let mut bad = initial;
    bad.pending.push(Message {
        source: SOURCE,
        route: Route {
            destination: MachineId(999),
            channel: 0,
            input: CLAIM,
        },
    });
    assert!(m.decode_state(&bad.trace_bytes(4096).unwrap()).is_err());
    assert!(network::Input::<u8>::from_trace(&[9], Default::default()).is_err());
}

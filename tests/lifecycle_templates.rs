use stateless::lifecycle::*;
use stateless::value_codec::{TraceDecode, TraceEncode};
use stateless::*;
#[derive(Clone)]
struct Machine;
impl Model for Machine {
    type State = u8;
    type Input = Vec<Event<u8>>;
    type Output = Event<u8>;
    fn metadata(&self) -> ModelMetadata {
        demo::RequestModel::fixed().metadata()
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, s: &u8, _: &Self::Input) -> Result<Transition<u8, Self::Output>, ModelError> {
        Ok(Transition::accepted(*s, vec![]))
    }
    fn check_state(&self, _: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![])
    }
}
struct Spec;
impl LifecycleSpec<Machine> for Spec {
    type Key = u8;
    fn metadata(&self) -> ModelMetadata {
        Machine.metadata()
    }
    fn input_events(
        &self,
        i: &Vec<Event<u8>>,
        _: &Disposition,
    ) -> Result<Vec<Event<u8>>, ModelError> {
        Ok(i.clone())
    }
    fn output_events(&self, o: &[Event<u8>]) -> Result<Vec<Event<u8>>, ModelError> {
        Ok(o.to_vec())
    }
}
fn advance(h: &History<u8>, input: Vec<Event<u8>>, output: Vec<Event<u8>>) -> History<u8> {
    LifecycleMonitor {
        spec: Spec,
        max_history: 20,
    }
    .advance(h, &input, &output, &Disposition::Accepted)
    .unwrap()
}
fn failed(h: &History<u8>) -> bool {
    let mut c = vec![];
    LifecycleMonitor {
        spec: Spec,
        max_history: 20,
    }
    .check_state_into(h, &0, &mut CheckSink::new(&mut c))
    .unwrap();
    c.iter().any(Check::is_failure)
}
#[test]
fn missing_duplicate_stale_and_resource_faults() {
    let start = advance(
        &History::default(),
        vec![
            Event::Acquire(1),
            Event::Begin {
                key: 1,
                deadline: 2,
            },
        ],
        vec![],
    );
    assert!(!failed(&start));
    assert_eq!(start.pending(), 1);
    assert!(failed(&advance(&start, vec![Event::Tick(2)], vec![])));
    assert!(failed(&advance(
        &start,
        vec![Event::Tick(3)],
        vec![Event::Settle(1)]
    )));
    assert!(!failed(&advance(
        &start,
        vec![Event::Tick(2)],
        vec![Event::Settle(1)]
    )));
    let settled = advance(
        &start,
        vec![],
        vec![Event::Settle(1), Event::Publish(1), Event::Release(1)],
    );
    assert!(!failed(&settled));
    assert_eq!(settled.pending(), 0);
    assert!(failed(&advance(&settled, vec![], vec![Event::Settle(1)])));
    assert!(failed(&advance(
        &start,
        vec![Event::Cancel(1)],
        vec![Event::Publish(1)]
    )));
    assert!(failed(&advance(&start, vec![], vec![Event::Release(1)])));
    assert!(failed(&advance(&start, vec![Event::Acquire(1)], vec![])));
    assert!(failed(&advance(
        &settled,
        vec![Event::Begin {
            key: 1,
            deadline: 4
        }],
        vec![]
    )));
    assert!(failed(&advance(&start, vec![], vec![Event::Settle(9)])));
}
#[test]
fn bounded_history_persistence_and_observation_error() {
    let monitor = LifecycleMonitor {
        spec: Spec,
        max_history: 1,
    };
    let h = advance(
        &History::default(),
        vec![Event::Begin {
            key: 1,
            deadline: 2,
        }],
        vec![],
    );
    let bytes = h.trace_bytes(1024).unwrap();
    assert_eq!(
        History::<u8>::from_trace(&bytes, Default::default()).unwrap(),
        h
    );
    assert_eq!(
        monitor
            .decode_history(&monitor.encode_history(&h).unwrap())
            .unwrap(),
        h
    );
    let m = WithOracle::new(Machine, monitor);
    let before = m.attach(0, h);
    let actual = Transition::accepted(7, vec![Event::Publish(9)]);
    let error = m
        .observe_transition(
            &before,
            &vec![Event::Begin {
                key: 2,
                deadline: 3,
            }],
            actual,
        )
        .unwrap_err();
    assert_eq!(error.transition.state, 7);
    assert_eq!(error.transition.outputs, [Event::Publish(9)]);
    let mut checks = vec![];
    m.check_state_into(&before, &mut CheckSink::new(&mut checks))
        .unwrap();
    assert!(
        checks
            .iter()
            .any(|c| matches!(c.status, CheckStatus::Skipped(_)))
    );
}

#[test]
fn overdue_cancellation_cannot_erase_a_missed_deadline() {
    let start = advance(
        &History::default(),
        vec![Event::Begin {
            key: 1,
            deadline: 2,
        }],
        vec![],
    );
    assert!(failed(&advance(
        &start,
        vec![Event::Tick(3), Event::Cancel(1)],
        vec![]
    )));
    // Cancellation by the deadline still legitimately discharges the obligation.
    assert!(!failed(&advance(
        &start,
        vec![Event::Tick(2), Event::Cancel(1)],
        vec![]
    )));
}

#[test]
fn restored_overdue_history_is_a_failure_without_reconstructed_events() {
    let h = History {
        now: 3,
        obligations: vec![(1, 2, false, false)],
        ..History::default()
    };
    assert!(failed(&h));
    assert!(failed(&advance(&h, vec![Event::Cancel(1)], vec![])));
    let bytes = h.trace_bytes(1024).unwrap();
    let restored = History::<u8>::from_trace(&bytes, Default::default()).unwrap();
    assert!(failed(&restored));
}

#[test]
fn past_due_creation_cannot_be_hidden_by_immediate_cancellation() {
    let h = advance(
        &History::default(),
        vec![
            Event::Tick(3),
            Event::Begin {
                key: 1,
                deadline: 2,
            },
            Event::Cancel(1),
        ],
        vec![],
    );
    assert!(failed(&h));
}

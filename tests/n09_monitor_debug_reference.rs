//! Real Rust indexed monitor diagnostic reference for native compatibility.
use stateless::lifecycle::Event;
use stateless::lifecycle::indexed::*;
use stateless::*;

struct Machine;
impl Model for Machine {
    type State = ();
    type Input = Vec<IndexedEvent<u8>>;
    type Output = IndexedEvent<u8>;
    fn metadata(&self) -> ModelMetadata {
        demo::RequestModel::fixed().metadata()
    }
    fn initial_state(&self) -> Result<(), ModelError> {
        Ok(())
    }
    fn step(&self, _: &(), _: &Self::Input) -> Result<Transition<(), Self::Output>, ModelError> {
        Ok(Transition::accepted((), vec![]))
    }
    fn check_state(&self, _: &()) -> Result<Vec<Check>, ModelError> {
        Ok(vec![])
    }
}
struct Spec;
impl IndexedLifecycleSpec<Machine> for Spec {
    type Key = u8;
    fn metadata(&self) -> ModelMetadata {
        Machine.metadata()
    }
    fn input_events(
        &self,
        input: &Vec<IndexedEvent<u8>>,
        _: &Disposition,
    ) -> Result<Vec<IndexedEvent<u8>>, ModelError> {
        Ok(input.clone())
    }
    fn output_events(
        &self,
        outputs: &[IndexedEvent<u8>],
    ) -> Result<Vec<IndexedEvent<u8>>, ModelError> {
        Ok(outputs.to_vec())
    }
}
#[test]
fn real_indexed_monitor_failed_and_skipped_details() {
    let monitor = IndexedLifecycleMonitor {
        spec: Spec,
        limits: RetentionLimits::default(),
    };
    let key = ScopedKey {
        epoch: 2,
        generation: 3,
        key: 7,
    };
    let begin = IndexedEvent::Lifecycle(Event::Begin { key, deadline: 9 });
    let history = <IndexedLifecycleMonitor<Spec> as Oracle<Machine>>::advance(
        &monitor,
        &IndexedHistory::default(),
        &vec![begin.clone(), begin],
        &[],
        &Disposition::Accepted,
    )
    .unwrap();
    let mut checks = Vec::new();
    <IndexedLifecycleMonitor<Spec> as Oracle<Machine>>::check_state_into(
        &monitor,
        &history,
        &(),
        &mut CheckSink::new(&mut checks),
    )
    .unwrap();
    assert_eq!(
        checks,
        vec![
            Check::failed(
                "lifecycle.indexed.safety",
                "violations=[Violation { key: Some(ScopedKey { epoch: 2, generation: 3, key: 7 }), code: \"key_reused\", event: \"begin\", observed_at: 0, prior: Some(Obligation { deadline: 9, cancelled: false, settled: false, published: false }) }]; retention=RetentionStatus { active_obligations: 1, historical_obligations: 0, acquired_resources: 0, historical_resources: 0, oldest_unresolved: Some((ScopedKey { epoch: 2, generation: 3, key: 7 }, 9)), retired_before: 0 }"
            ),
            Check::skipped(
                "lifecycle.indexed.obligations",
                "finite history: RetentionStatus { active_obligations: 1, historical_obligations: 0, acquired_resources: 0, historical_resources: 0, oldest_unresolved: Some((ScopedKey { epoch: 2, generation: 3, key: 7 }, 9)), retired_before: 0 }"
            )
        ]
    );
}

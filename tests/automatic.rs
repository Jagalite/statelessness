use stateless::CheckSink;
use stateless::TransitionRef;
use stateless::automatic::{Auto, AutoOptions};
use stateless::execution::{ReplayOptions, ReplayOutcome, record, replay};
use stateless::explore::{FuzzConfig, FuzzTermination, ShrinkConfig, fuzz, shrink};
use stateless::trace::{RunConfig, Termination};
use stateless::{
    Check, EncodeBuffer, Enumerate, Generate, Model, ModelCodec, ModelError, ModelMetadata, Rng,
    Transition,
};
use std::cell::Cell;

#[derive(Debug, Default)]
struct Domain {
    pulls: Cell<usize>,
    eager_calls: Cell<usize>,
    error: bool,
}
impl Model for Domain {
    type State = usize;
    type Input = usize;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "automatic-domain".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "fixture-v1".into(),
        }
    }
    fn initial_state(&self) -> Result<usize, ModelError> {
        Ok(4)
    }
    fn step(&self, state: &usize, _: &usize) -> Result<Transition<usize, ()>, ModelError> {
        Ok(Transition::accepted(*state, vec![]))
    }
    fn check_state(&self, _: &usize) -> Result<Vec<Check>, ModelError> {
        Ok(vec![Check::passed("domain")])
    }
}
impl Enumerate for Domain {
    fn inputs(&self, state: &usize) -> Result<Vec<usize>, ModelError> {
        self.eager_calls.set(self.eager_calls.get() + 1);
        Ok((0..*state).collect())
    }
    fn input_iter<'a>(
        &'a self,
        state: &'a usize,
    ) -> Result<Box<dyn Iterator<Item = usize> + 'a>, ModelError> {
        if self.error {
            return Err(ModelError::new("domain construction failed"));
        }
        Ok(Box::new(
            (0..*state).inspect(|_| self.pulls.set(self.pulls.get() + 1)),
        ))
    }
}

#[test]
fn reservoir_is_repeatable_and_samples_the_whole_lazy_domain() {
    let auto = Auto::new(Domain::default());
    let mut histogram = [0usize; 3];
    for seed in 0..30_000 {
        let mut first = Rng::new(seed);
        let mut second = Rng::new(seed);
        let a = auto.generate(&3, &mut first).unwrap().unwrap();
        let b = auto.generate(&3, &mut second).unwrap().unwrap();
        assert_eq!(a, b);
        assert_eq!(first.next_u64(), second.next_u64());
        histogram[a] += 1;
    }
    // Fixed seeds make this deterministic. The broad bound detects selecting
    // only the first/last entry and common incorrect reservoir probabilities.
    assert!(
        histogram
            .iter()
            .all(|count| (9_600..10_400).contains(count)),
        "{histogram:?}"
    );
    assert_eq!(auto.model().pulls.get(), 180_000);
    assert_eq!(auto.model().eager_calls.get(), 0);
}

#[test]
fn empty_domains_and_errors_leave_the_rng_unchanged() {
    let auto = Auto::with_options(Domain::default(), AutoOptions { max_candidates: 3 }).unwrap();
    for size in [0, 4, usize::MAX] {
        let mut rng = Rng::new(42);
        let mut unchanged = rng.clone();
        let result = auto.generate(&size, &mut rng);
        if size == 0 {
            assert_eq!(result.unwrap(), None);
        } else {
            assert!(result.unwrap_err().0.contains("max_candidates"));
        }
        assert_eq!(rng.next_u64(), unchanged.next_u64());
    }
    assert_eq!(
        auto.model().pulls.get(),
        8,
        "oversized scans pull only cap + one entry"
    );
    let broken = Auto::new(Domain {
        error: true,
        ..Domain::default()
    });
    let mut rng = Rng::new(19);
    let mut unchanged = rng.clone();
    assert!(
        broken
            .generate(&3, &mut rng)
            .unwrap_err()
            .0
            .contains("domain construction failed")
    );
    assert_eq!(rng.next_u64(), unchanged.next_u64());
}

#[test]
fn candidate_boundary_accepts_complete_domains_but_never_a_truncated_prefix() {
    let auto = Auto::with_options(Domain::default(), AutoOptions { max_candidates: 3 }).unwrap();
    assert!(auto.generate(&3, &mut Rng::new(5)).unwrap().is_some());
    assert_eq!(auto.model().pulls.get(), 3);
    assert!(auto.generate(&4, &mut Rng::new(5)).is_err());
    assert_eq!(auto.model().pulls.get(), 7);
    assert!(Auto::with_options(Domain::default(), AutoOptions { max_candidates: 0 }).is_err());
    assert_eq!(auto.options().max_candidates, 3);
    assert_eq!(auto.into_inner().pulls.get(), 7);
}

#[test]
fn membership_short_circuits_matches_and_bounds_missing_candidates() {
    let auto = Auto::with_options(Domain::default(), AutoOptions { max_candidates: 3 }).unwrap();
    assert!(auto.is_enabled(&usize::MAX, &0).unwrap());
    assert_eq!(auto.model().pulls.get(), 1);
    assert!(auto.is_enabled(&3, &2).unwrap());
    assert!(!auto.is_enabled(&3, &3).unwrap());
    assert!(
        auto.is_enabled(&4, &3)
            .unwrap_err()
            .0
            .contains("max_candidates")
    );
    assert!(!auto.is_enabled(&0, &0).unwrap());
    assert_eq!(auto.model().eager_calls.get(), 0);
    // Explicit enumeration remains a direct delegation, independent of the
    // generation limit; it preserves the application's declared input order.
    assert_eq!(auto.inputs(&4).unwrap(), vec![0, 1, 2, 3]);
    assert_eq!(
        auto.input_iter(&4).unwrap().collect::<Vec<_>>(),
        vec![0, 1, 2, 3]
    );
}

// Deliberately implements Enumerate without Generate. Optimized callbacks panic
// if the wrapper accidentally falls back to legacy allocating callback methods.
struct Counter;
impl Model for Counter {
    type State = u8;
    type Input = u8;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "automatic-counter".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "counter-v1".into(),
        }
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u8, input: &u8) -> Result<Transition<u8, u8>, ModelError> {
        Ok(Transition::accepted(state + input, vec![state + input]))
    }
    fn check_state(&self, _: &u8) -> Result<Vec<Check>, ModelError> {
        panic!("optimized state checker was not delegated")
    }
    fn check_state_into(&self, state: &u8, checks: &mut CheckSink<'_>) -> Result<(), ModelError> {
        checks.push(if *state < 3 {
            Check::passed("under-three")
        } else {
            Check::failed("under-three", "counter reached threshold")
        });
        Ok(())
    }
    fn check_transition(
        &self,
        _: &u8,
        _: &u8,
        _: &TransitionRef<'_, u8, u8>,
    ) -> Result<Vec<Check>, ModelError> {
        panic!("optimized edge checker was not delegated")
    }
    fn check_transition_into(
        &self,
        _: &u8,
        _: &u8,
        transition: &TransitionRef<'_, u8, u8>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.push(if transition.outputs == [*transition.state] {
            Check::passed("output")
        } else {
            Check::failed("output", "incorrect output")
        });
        Ok(())
    }
    fn estimated_state_bytes(&self, _: &u8) -> Option<usize> {
        Some(1)
    }
}
impl Enumerate for Counter {
    fn inputs(&self, _: &u8) -> Result<Vec<u8>, ModelError> {
        panic!("automatic generation must use the iterator")
    }
    fn input_iter<'a>(
        &'a self,
        state: &'a u8,
    ) -> Result<Box<dyn Iterator<Item = u8> + 'a>, ModelError> {
        Ok(Box::new((1..=2).filter(move |_| *state < 3)))
    }
}
fn decode(bytes: &[u8]) -> Result<u8, ModelError> {
    match bytes {
        [value] => Ok(*value),
        _ => Err(ModelError::new("expected one byte")),
    }
}
impl ModelCodec for Counter {
    fn encode_state(&self, _: &u8) -> Result<Vec<u8>, ModelError> {
        panic!("bounded state codec was not delegated")
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        decode(bytes)
    }
    fn encode_input(&self, _: &u8) -> Result<Vec<u8>, ModelError> {
        panic!("bounded input codec was not delegated")
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        decode(bytes)
    }
    fn encode_output(&self, _: &u8) -> Result<Vec<u8>, ModelError> {
        panic!("bounded output codec was not delegated")
    }
    fn encode_state_into(&self, state: &u8, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        out.extend_from_slice(&[*state])
    }
    fn encode_input_into(&self, input: &u8, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        out.extend_from_slice(&[*input])
    }
    fn encode_output_into(
        &self,
        output: &u8,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_slice(&[*output])
    }
}

#[test]
fn enumerated_only_model_fuzzes_shrinks_records_and_replays_through_the_adapter() {
    let auto = Auto::new(Counter);
    assert_eq!(auto.metadata(), Counter.metadata());
    assert_eq!(auto.estimated_state_bytes(&0), Some(1));
    let result = fuzz(
        &auto,
        FuzzConfig {
            seed: 7,
            cases: 2,
            max_steps: 8,
            max_transitions: 16,
            mutation_percent: 0,
        },
    )
    .unwrap();
    assert_eq!(result.termination, FuzzTermination::FailureFound);
    let failure = result.failure.unwrap();
    let shrunk = shrink(&auto, &failure, ShrinkConfig { max_attempts: 100 }).unwrap();
    assert!(shrunk.minimized.inputs.len() <= failure.inputs.len());
    let mut state = auto.initial_state().unwrap();
    for input in &shrunk.minimized.inputs {
        assert!(auto.is_enabled(&state, input).unwrap());
        state = auto.step(&state, input).unwrap().state;
    }
    let trace = record(&auto, shrunk.minimized.inputs, RunConfig::default(), 8).unwrap();
    assert_eq!(trace.termination, Termination::PropertyFailed);
    let report = replay(&auto, &trace, ReplayOptions::default()).unwrap();
    assert_eq!(report.outcome, ReplayOutcome::Exact);
    assert!(report.failure_reproduced);
}

#[test]
fn bounded_codec_errors_are_preserved_by_delegation() {
    let auto = Auto::new(Counter);
    let mut bytes = Vec::new();
    let mut output = EncodeBuffer::new(&mut bytes, 0);
    assert!(auto.encode_state_into(&1, &mut output).is_err());
    assert!(output.finish().is_err());
    assert!(auto.decode_input(&[1, 2]).is_err());
}

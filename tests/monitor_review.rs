//! Independent recorder review: change the state-check shape across checkpoints
//! and force encoding failure exactly when the retention window is full.
use stateless::TransitionRef;
use stateless::execution::{ReplayOptions, ReplayOutcome, replay};
use stateless::monitor::Recorder;
use stateless::trace::{ReadLimits, RunConfig, Termination, Trace};
use stateless::{Check, Model, ModelCodec, ModelError, ModelMetadata, Transition};
use std::cell::Cell;

struct Counter {
    observe_only: bool,
    failed_output: Option<u8>,
    bound: u8,
    state_checks: Cell<usize>,
    transition_checks: Cell<usize>,
}

impl Counter {
    fn new(observe_only: bool) -> Self {
        Self {
            observe_only,
            failed_output: None,
            bound: 100,
            state_checks: Cell::new(0),
            transition_checks: Cell::new(0),
        }
    }
}

impl Model for Counter {
    type State = u8;
    type Input = u8;
    type Output = u8;

    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "monitor-review".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "fixture-1".into(),
        }
    }

    fn initial_state(&self) -> Result<u8, ModelError> {
        panic!("runtime recording and snapshot replay must use supplied checkpoints")
    }

    fn step(&self, state: &u8, input: &u8) -> Result<Transition<u8, u8>, ModelError> {
        assert!(
            !self.observe_only,
            "observation must not execute the reducer"
        );
        let next = state
            .checked_add(*input)
            .ok_or_else(|| ModelError::new("counter overflow"))?;
        Ok(Transition::accepted(next, vec![next]))
    }

    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        self.state_checks.set(self.state_checks.get() + 1);
        let mut checks = vec![if *state <= self.bound {
            Check::passed("bound")
        } else {
            Check::failed("bound", "counter exceeded bound")
        }];
        if state.is_multiple_of(2) {
            checks.push(Check::passed("even-state-only-check"));
        }
        Ok(checks)
    }

    fn check_transition(
        &self,
        _: &u8,
        _: &u8,
        transition: &TransitionRef<'_, u8, u8>,
    ) -> Result<Vec<Check>, ModelError> {
        self.transition_checks.set(self.transition_checks.get() + 1);
        Ok(vec![if transition.outputs == vec![*transition.state] {
            Check::passed("transition-output")
        } else {
            Check::failed("transition-output", "wrong output")
        }])
    }
}

impl ModelCodec for Counter {
    fn encode_state(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*state])
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        match bytes {
            [state] => Ok(*state),
            _ => Err(ModelError::new("invalid counter encoding")),
        }
    }
    fn encode_input(&self, input: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*input])
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        self.decode_state(bytes)
    }
    fn encode_output(&self, output: &u8) -> Result<Vec<u8>, ModelError> {
        if Some(*output) == self.failed_output {
            Err(ModelError::new("output encoding unavailable"))
        } else {
            Ok(vec![*output])
        }
    }
}

#[test]
fn every_rolling_checkpoint_replays_with_variable_check_counts() {
    for capacity in 1..=4 {
        let runtime = Counter::new(true);
        let playback = Counter::new(false);
        let mut recorder = Recorder::new(&runtime, &4, RunConfig::default(), capacity).unwrap();
        for before in 4..13 {
            let actual = Transition::accepted(before + 1, vec![before + 1]);
            recorder.observe(&runtime, &before, &1, &actual).unwrap();
            let trace = recorder.snapshot();
            assert_eq!(trace.termination, Termination::Interrupted);
            assert_eq!(
                trace.initial_state,
                vec![4 + recorder.evicted_steps() as u8]
            );
            assert_eq!(trace.steps.len(), recorder.retained_steps());
            assert!(
                trace
                    .initial_checks
                    .iter()
                    .all(|check| check.id != "transition-output")
            );
            let mut bytes = Vec::new();
            trace.write_to(&mut bytes).unwrap();
            let restored = Trace::read_from(bytes.as_slice(), &ReadLimits::default()).unwrap();
            let replayed = replay(&playback, &restored, ReplayOptions::default()).unwrap();
            assert_eq!(replayed.outcome, ReplayOutcome::Exact);
            assert!(!replayed.failure_reproduced);
        }
        assert_eq!(recorder.observed_steps(), 9);
        assert_eq!(runtime.state_checks.get(), 10); // checkpoint + once per observation
        assert_eq!(runtime.transition_checks.get(), 9);
    }
}

#[test]
fn encoding_error_at_full_capacity_freezes_without_evicting_valid_evidence() {
    let mut runtime = Counter::new(true);
    runtime.failed_output = Some(3);
    let mut recorder = Recorder::new(&runtime, &0, RunConfig::default(), 2).unwrap();
    for before in 0..2 {
        recorder
            .observe(
                &runtime,
                &before,
                &1,
                &Transition::accepted(before + 1, vec![before + 1]),
            )
            .unwrap();
    }
    let coherent_prefix = recorder.snapshot();
    let error = recorder
        .observe(&runtime, &2, &1, &Transition::accepted(3, vec![3]))
        .unwrap_err();
    assert!(error.0.contains("output encoding unavailable"));
    assert!(recorder.is_frozen());
    assert_eq!(recorder.observed_steps(), 2);
    assert_eq!(recorder.evicted_steps(), 0);
    let frozen = recorder.snapshot();
    assert_eq!(frozen.initial_state, coherent_prefix.initial_state);
    assert_eq!(frozen.steps, coherent_prefix.steps);
    assert!(matches!(frozen.termination, Termination::ModelError(_)));
    let checks_before_retry = runtime.state_checks.get();
    assert!(
        recorder
            .observe(&runtime, &2, &1, &Transition::accepted(3, vec![3]))
            .is_err()
    );
    assert_eq!(runtime.state_checks.get(), checks_before_retry);
    assert_eq!(recorder.snapshot(), frozen);
    let replayed = replay(&Counter::new(false), &frozen, ReplayOptions::default()).unwrap();
    assert_eq!(replayed.outcome, ReplayOutcome::Exact); // Only the coherent prefix.
    assert!(!replayed.failure_reproduced);
}

#[test]
fn failure_after_eviction_retains_the_failing_edge_and_freezes_it() {
    let mut runtime = Counter::new(true);
    runtime.bound = 2;
    let mut recorder = Recorder::new(&runtime, &0, RunConfig::default(), 1).unwrap();
    for before in 0..3 {
        recorder
            .observe(
                &runtime,
                &before,
                &1,
                &Transition::accepted(before + 1, vec![before + 1]),
            )
            .unwrap();
    }
    let frozen = recorder.snapshot();
    assert_eq!(frozen.termination, Termination::PropertyFailed);
    assert_eq!(frozen.initial_state, vec![2]);
    assert_eq!(frozen.steps[0].post_state, vec![3]);
    assert_eq!(recorder.evicted_steps(), 2);
    assert!(
        recorder
            .observe(&runtime, &3, &1, &Transition::accepted(4, vec![4]))
            .is_err()
    );
    assert_eq!(recorder.snapshot(), frozen);
    let mut playback = Counter::new(false);
    playback.bound = 2;
    let replayed = replay(&playback, &frozen, ReplayOptions::default()).unwrap();
    assert_eq!(replayed.outcome, ReplayOutcome::Exact);
    assert!(replayed.failure_reproduced);
}

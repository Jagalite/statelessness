use stateless::automatic::Auto;
use stateless::execution::{
    CheckPolicy, ReplayOptions, ReplayOutcome, check_observed, record, replay,
};
use stateless::explore::{FuzzConfig, SearchConfig, ShrinkConfig, enumerate, fuzz, shrink};
use stateless::monitor::Recorder;
use stateless::trace::{RunConfig, Termination};
use stateless::*;
use std::cell::Cell;

struct Counter {
    steps: Cell<usize>,
    bug: bool,
    missing: bool,
    state_failure: bool,
}
fn metadata(name: &str) -> ModelMetadata {
    ModelMetadata {
        name: name.into(),
        model_version: 1,
        properties_version: 1,
        codec_version: 1,
        build: "fixture".into(),
    }
}
impl Model for Counter {
    type State = u64;
    type Input = u64;
    type Output = u64;
    fn metadata(&self) -> ModelMetadata {
        metadata("counter")
    }
    fn initial_state(&self) -> Result<u64, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u64, input: &u64) -> Result<Transition<u64, u64>, ModelError> {
        self.steps.set(self.steps.get() + 1);
        let next = state + input + u64::from(self.bug);
        Ok(Transition::accepted(
            next,
            if self.missing { vec![] } else { vec![*input] },
        ))
    }
    fn check_state(&self, _: &u64) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if self.state_failure {
            Check::failed("application.failure", "must survive transition callbacks")
        } else {
            Check::passed("application.consistent")
        }])
    }
    fn check_transition_into(
        &self,
        _: &u64,
        _: &u64,
        transition: &TransitionRef<'_, u64, u64>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.push(if *transition.state <= 100 {
            Check::passed("application.bound")
        } else {
            Check::failed("application.bound", "bound")
        });
        Ok(())
    }
    fn estimated_state_bytes(&self, _: &u64) -> Option<usize> {
        Some(8)
    }
}
fn number(bytes: &[u8]) -> Result<u64, ModelError> {
    Ok(u64::from_le_bytes(
        bytes
            .try_into()
            .map_err(|_| ModelError::new("invalid number"))?,
    ))
}
impl ModelCodec for Counter {
    fn encode_state(&self, s: &u64) -> Result<Vec<u8>, ModelError> {
        Ok(s.to_le_bytes().to_vec())
    }
    fn decode_state(&self, b: &[u8]) -> Result<u64, ModelError> {
        number(b)
    }
    fn encode_input(&self, s: &u64) -> Result<Vec<u8>, ModelError> {
        self.encode_state(s)
    }
    fn decode_input(&self, b: &[u8]) -> Result<u64, ModelError> {
        number(b)
    }
    fn encode_output(&self, s: &u64) -> Result<Vec<u8>, ModelError> {
        self.encode_state(s)
    }
    fn encode_state_into(&self, s: &u64, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        out.extend_from_slice(&s.to_le_bytes())
    }
}
impl Enumerate for Counter {
    fn inputs(&self, s: &u64) -> Result<Vec<u64>, ModelError> {
        Ok(if *s < 4 { vec![0, 1] } else { vec![] })
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct History {
    expected: u64,
    calls: u64,
    effects_ok: bool,
}
struct Reference {
    version: u32,
    error: bool,
    replenish: bool,
}
impl Oracle<Counter> for Reference {
    type State = History;
    fn metadata(&self) -> ModelMetadata {
        let mut m = metadata("reference");
        m.properties_version = self.version;
        m
    }
    fn initial_state(&self) -> Result<History, ModelError> {
        Ok(History {
            expected: 0,
            calls: 0,
            effects_ok: true,
        })
    }
    fn advance(
        &self,
        h: &History,
        input: &u64,
        outputs: &[u64],
        _: &Disposition,
    ) -> Result<History, ModelError> {
        if self.error {
            return Err(ModelError::new("oracle unavailable"));
        }
        Ok(History {
            expected: h.expected + input,
            calls: h.calls + 1,
            effects_ok: h.effects_ok && outputs == [*input],
        })
    }
    fn check_state_into(
        &self,
        h: &History,
        actual: &u64,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.push(if h.expected == *actual {
            Check::passed("oracle.expected")
        } else {
            Check::failed("oracle.expected", "independent sum differs")
        });
        checks.push(if h.effects_ok {
            Check::passed("oracle.effects")
        } else {
            Check::failed("oracle.effects", "required effect missing or wrong")
        });
        Ok(())
    }
    fn check_transition_into(
        &self,
        _: &OracleState<u64, History>,
        _: &u64,
        _: &TransitionRef<'_, OracleState<u64, History>, u64>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        if self.replenish {
            for id in ["oracle.t1", "oracle.t2", "oracle.t3", "oracle.t4"] {
                checks.push(Check::passed(id));
            }
        }
        Ok(())
    }
    fn estimated_state_bytes(&self, _: &History) -> Option<usize> {
        Some(size_of::<History>())
    }
}
impl OracleCodec<Counter> for Reference {
    fn encode_history(&self, h: &History) -> Result<Vec<u8>, ModelError> {
        let mut b = Vec::new();
        self.encode_history_into(h, &mut EncodeBuffer::new(&mut b, 17))?;
        Ok(b)
    }
    fn decode_history(&self, b: &[u8]) -> Result<History, ModelError> {
        if b.len() != 17 || b[16] > 1 {
            return Err(ModelError::new("invalid history"));
        }
        Ok(History {
            expected: number(&b[..8])?,
            calls: number(&b[8..16])?,
            effects_ok: b[16] == 1,
        })
    }
    fn encode_history_into(
        &self,
        h: &History,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_slice(&h.expected.to_le_bytes())?;
        out.extend_from_slice(&h.calls.to_le_bytes())?;
        out.extend_from_slice(&[u8::from(h.effects_ok)])
    }
}
fn fixture(bug: bool, missing: bool) -> WithOracle<Counter, Reference> {
    WithOracle::new(
        Counter {
            steps: Cell::new(0),
            bug,
            missing,
            state_failure: false,
        },
        Reference {
            version: 1,
            error: false,
            replenish: false,
        },
    )
}
#[test]
fn independent_reference_finds_consistent_wrong_state_and_missing_effects() {
    for (bug, missing, id) in [
        (true, false, "oracle.expected"),
        (false, true, "oracle.effects"),
    ] {
        let m = fixture(bug, missing);
        let t = record(&m, [1], RunConfig::default(), 10).unwrap();
        assert_eq!(t.termination, Termination::PropertyFailed);
        assert!(
            t.steps[0]
                .checks
                .iter()
                .any(|c| c.id == id && c.is_failure())
        );
        let report = replay(&m, &t, ReplayOptions::default()).unwrap();
        assert_eq!(report.outcome, ReplayOutcome::Exact);
        assert!(report.failure_reproduced);
    }
}
#[test]
fn runtime_advances_once_without_reexecuting_and_evicted_checkpoint_replays() {
    let m = fixture(false, false);
    let mut state = m.initial_state().unwrap();
    let mut recorder = Recorder::new(&m, &state, RunConfig::default(), 2).unwrap();
    for _ in 0..5 {
        let app = m.model().step(&state.model, &1).unwrap();
        let transition = m.observe_transition(&state, &1, app).unwrap();
        let calls = m.model().steps.get();
        let checks =
            check_observed(&m, &state, &1, &transition, 1, CheckPolicy::default()).unwrap();
        assert!(checks.iter().all(|c| !c.is_failure()));
        recorder.observe(&m, &state, &1, &transition).unwrap();
        assert_eq!(m.model().steps.get(), calls);
        state = transition.state;
    }
    assert_eq!(state.oracle.calls, 5);
    assert_eq!(m.model().steps.get(), 5);
    let trace = recorder.into_trace();
    assert_eq!(
        m.decode_state(&trace.initial_state).unwrap().oracle.calls,
        3
    );
    assert_eq!(
        replay(&m, &trace, ReplayOptions::default())
            .unwrap()
            .outcome,
        ReplayOutcome::Exact
    );
}
#[test]
fn checkpoint_is_strict_bounded_and_versions_remain_strict() {
    let m = fixture(false, false);
    let s = m.initial_state().unwrap();
    let encoded = m.encode_state(&s).unwrap();
    assert_eq!(m.decode_state(&encoded).unwrap(), s);
    for end in 0..encoded.len() {
        assert!(m.decode_state(&encoded[..end]).is_err());
    }
    let mut trailing = encoded.clone();
    trailing.push(0);
    assert!(m.decode_state(&trailing).is_err());
    let mut huge = encoded.clone();
    huge[4..12].copy_from_slice(&u64::MAX.to_le_bytes());
    assert!(m.decode_state(&huge).is_err());
    let mut b = Vec::new();
    let mut out = EncodeBuffer::new(&mut b, encoded.len() - 1);
    assert!(m.encode_state_into(&s, &mut out).is_err());
    assert!(out.finish().is_err());
    assert!(b.len() < encoded.len());
    let t = record(&m, [1], RunConfig::default(), 10).unwrap();
    let changed = WithOracle::new(
        Counter {
            steps: Cell::new(0),
            bug: false,
            missing: false,
            state_failure: false,
        },
        Reference {
            version: 2,
            error: false,
            replenish: false,
        },
    );
    assert!(matches!(
        replay(
            &changed,
            &t,
            ReplayOptions {
                allow_build_mismatch: true
            }
        )
        .unwrap()
        .outcome,
        ReplayOutcome::Incompatible { .. }
    ));
    assert_eq!(
        m.estimated_state_bytes(&s),
        Some(size_of::<OracleState<u64, History>>())
    );
}
#[test]
fn automatic_fuzz_shrink_and_enumeration_preserve_oracle_history() {
    let auto = Auto::new(fixture(true, false));
    let result = fuzz(
        &auto,
        FuzzConfig {
            seed: 12,
            cases: 10,
            max_steps: 10,
            ..FuzzConfig::default()
        },
    )
    .unwrap();
    let failure = result.failure.unwrap();
    let reduced = shrink(&auto, &failure, ShrinkConfig::default()).unwrap();
    assert_eq!(reduced.minimized.inputs.len(), 1);
    let good = fixture(false, false);
    let report = enumerate(
        &good,
        SearchConfig {
            max_depth: 2,
            ..SearchConfig::default()
        },
    )
    .unwrap();
    // The zero-input self-loop changes oracle call history, so it is retained.
    assert!(report.states > 3);
    assert!(report.failure.is_none());
}
#[test]
fn oracle_advancement_error_leaves_a_coherent_recorded_prefix() {
    let m = WithOracle::new(
        Counter {
            steps: Cell::new(0),
            bug: false,
            missing: false,
            state_failure: false,
        },
        Reference {
            version: 1,
            error: true,
            replenish: false,
        },
    );
    let t = record(&m, [1], RunConfig::default(), 10).unwrap();
    assert!(matches!(t.termination, Termination::ModelError(_)));
    assert!(t.steps.is_empty());
}

#[test]
fn borrowed_transition_checks_do_not_clone_application_state() {
    #[derive(Debug, PartialEq, Eq)]
    struct Large(Vec<u8>);
    impl Clone for Large {
        fn clone(&self) -> Self {
            panic!("checking must not clone application state")
        }
    }
    struct App;
    impl Model for App {
        type State = Large;
        type Input = ();
        type Output = ();
        fn metadata(&self) -> ModelMetadata {
            metadata("large")
        }
        fn initial_state(&self) -> Result<Large, ModelError> {
            Ok(Large(vec![0; 1_000_000]))
        }
        fn step(&self, _: &Large, _: &()) -> Result<Transition<Large, ()>, ModelError> {
            Ok(Transition::accepted(Large(vec![1; 1_000_000]), vec![]))
        }
        fn check_state(&self, _: &Large) -> Result<Vec<Check>, ModelError> {
            Ok(vec![])
        }
        fn check_transition_into(
            &self,
            _: &Large,
            _: &(),
            after: &TransitionRef<'_, Large, ()>,
            checks: &mut CheckSink<'_>,
        ) -> Result<(), ModelError> {
            assert_eq!(after.state.0[0], 1);
            checks.push(Check::passed("borrowed"));
            Ok(())
        }
    }
    struct Policy;
    impl Oracle<App> for Policy {
        type State = u64;
        fn metadata(&self) -> ModelMetadata {
            metadata("large-policy")
        }
        fn initial_state(&self) -> Result<u64, ModelError> {
            Ok(0)
        }
        fn advance(&self, h: &u64, _: &(), _: &[()], _: &Disposition) -> Result<u64, ModelError> {
            Ok(h + 1)
        }
        fn check_state_into(
            &self,
            _: &u64,
            _: &Large,
            _: &mut CheckSink<'_>,
        ) -> Result<(), ModelError> {
            Ok(())
        }
        fn check_transition_into(
            &self,
            before: &OracleState<Large, u64>,
            _: &(),
            after: &TransitionRef<'_, OracleState<Large, u64>, ()>,
            checks: &mut CheckSink<'_>,
        ) -> Result<(), ModelError> {
            assert_eq!(after.state.oracle, before.oracle + 1);
            checks.push(Check::passed("oracle.once"));
            Ok(())
        }
    }
    let m = WithOracle::new(App, Policy);
    let state = m.initial_state().unwrap();
    let transition = m.step(&state, &()).unwrap();
    let checks = check_observed(&m, &state, &(), &transition, 1, CheckPolicy::default()).unwrap();
    assert_eq!(checks.len(), 2);
}

#[test]
fn application_checker_cannot_erase_state_checks_and_hide_behind_oracle_checks() {
    let (mut app, mut oracle) = fixture(false, false).into_parts();
    app.state_failure = true;
    oracle.replenish = true;
    let m = WithOracle::new(app, oracle);
    let before = m.initial_state().unwrap();
    let transition = m.step(&before, &1).unwrap();
    let checks = check_observed(&m, &before, &1, &transition, 1, CheckPolicy::default()).unwrap();
    assert_eq!(
        checks[0],
        Check::failed("application.failure", "must survive transition callbacks")
    );
    assert_eq!(
        checks[1..],
        [
            "oracle.expected",
            "oracle.effects",
            "application.bound",
            "oracle.t1",
            "oracle.t2",
            "oracle.t3",
            "oracle.t4"
        ]
        .map(Check::passed)
    );
}

#[test]
fn failed_runtime_advancement_returns_the_actual_transition_without_copying() {
    let (app, mut oracle) = fixture(false, false).into_parts();
    oracle.error = true;
    let model = WithOracle::new(app, oracle);
    let before = model.initial_state().unwrap();
    let application_transition = model.model().step(&before.model, &1).unwrap();
    let outputs_pointer = application_transition.outputs.as_ptr();
    let error = model
        .observe_transition(&before, &1, application_transition)
        .unwrap_err();
    assert_eq!(error.error, ModelError::new("oracle unavailable"));
    assert_eq!(error.transition.state, 1);
    assert_eq!(error.transition.outputs, [1]);
    assert_eq!(error.transition.outputs.as_ptr(), outputs_pointer);
    assert_eq!(error.transition.disposition, Disposition::Accepted);
    assert_eq!(model.model().steps.get(), 1);
    assert_eq!(before.oracle.calls, 0);
}

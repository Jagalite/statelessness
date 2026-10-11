//! The debugger delivers one explicitly selected composition message per turn.
use stateless::composition::{Address, Input, Pair, PairOutput, PairState, Wiring};
use stateless::monitor::RecorderOptions;
use stateless::trace::RunConfig;
use stateless::{Check, CheckSink, Enumerate, Model, ModelError, ModelMetadata, Transition};
use statelessness_debug::{DebugSession, InputPolicy, SessionLimits};
struct Counter;
fn metadata(name: &str) -> ModelMetadata {
    ModelMetadata {
        name: name.into(),
        model_version: 1,
        properties_version: 1,
        codec_version: 1,
        build: "debug-composition-v1".into(),
    }
}
impl Model for Counter {
    type State = u8;
    type Input = u8;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        metadata("counter")
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u8, input: &u8) -> Result<Transition<u8, u8>, ModelError> {
        Ok(Transition::accepted(
            state
                .checked_add(*input)
                .ok_or_else(|| ModelError::new("counter overflow"))?,
            vec![*input],
        ))
    }
    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if *state <= 20 {
            Check::passed("bounded")
        } else {
            Check::failed("bounded", "over20")
        }])
    }
}
impl Enumerate for Counter {
    fn inputs(&self, _: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![1, 2, 3])
    }
}
struct Route;
impl Wiring<Counter, Counter> for Route {
    fn metadata(&self) -> ModelMetadata {
        metadata("route-left-to-right")
    }
    fn messages(
        &self,
        output: &PairOutput<Counter, Counter>,
    ) -> Result<Vec<Address<u8, u8>>, ModelError> {
        Ok(match output {
            Address::Left(n) => vec![Address::Right(*n)],
            Address::Right(_) => vec![],
        })
    }
    fn check_state_into(
        &self,
        _: &PairState<Counter, Counter>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.push(Check::passed("routing"));
        Ok(())
    }
}
fn exercise() -> Result<(), Box<dyn std::error::Error>> {
    let pair = Pair {
        left: Counter,
        right: Counter,
        wiring: Route,
        max_pending: 8,
    };
    let mut session = DebugSession::recording(
        "composed",
        pair,
        InputPolicy::enumerated(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions::default(),
    )?;
    session.step(0, Input::Local(Address::Left(2)))?;
    session.step(1, Input::Local(Address::Left(3)))?;
    assert_eq!(session.state().right, 0);
    assert_eq!(
        session.state().pending,
        vec![Address::Right(2), Address::Right(3)]
    );
    session.step(2, Input::Deliver(1))?;
    assert_eq!(session.state().right, 3);
    assert_eq!(session.state().pending, vec![Address::Right(2)]);
    let trace = session.export_trace()?;
    let report = stateless::execution::replay(session.model(), &trace, Default::default())?;
    assert_eq!(report.steps_verified, 3);
    println!(
        "head={} state={:?}; one message remains pending",
        session.sequence(),
        session.state()
    );
    Ok(())
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    exercise()
}
#[test]
fn explicit_message_order_is_preserved() {
    exercise().unwrap();
}

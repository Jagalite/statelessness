//! Rust participant in the native stateful replay matrix.
use stateless::execution::{ReplayOptions, ReplayOutcome, record, replay};
use stateless::network::{
    Address, Input, MachineId, Network, NetworkState, QueuePolicy, Route, Wiring,
};
use stateless::trace::{ReadLimits, RunConfig, Trace};
use stateless::{
    Check, CheckSink, CheckStatus, Model, ModelCodec, ModelError, ModelMetadata, Transition,
};

fn identity(name: &str) -> ModelMetadata {
    ModelMetadata {
        name: name.into(),
        model_version: 1,
        properties_version: 1,
        codec_version: 1,
        build: "fixture-v1".into(),
    }
}
struct Child;
impl Model for Child {
    type State = u8;
    type Input = u8;
    type Output = u8;
    fn metadata(&self) -> ModelMetadata {
        identity("ring-child")
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u8, input: &u8) -> Result<Transition<u8, u8>, ModelError> {
        Ok(Transition::accepted(
            state
                .checked_add(*input)
                .ok_or_else(|| ModelError::new("overflow"))?,
            vec![*input],
        ))
    }
    fn check_state(&self, _: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![Check::passed("safe")])
    }
}
struct Ring {
    weaken: bool,
}
impl Wiring<Child> for Ring {
    fn metadata(&self) -> ModelMetadata {
        identity("ring-global")
    }
    fn routes(&self, output: &Address<u8>) -> Result<Vec<Route<u8>>, ModelError> {
        Ok(vec![Route {
            destination: MachineId((output.machine.0 + 1) % 3),
            channel: 7,
            input: output.value,
        }])
    }
    fn check_state_into(
        &self,
        state: &NetworkState<Child>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        let sum: u16 = (0..3)
            .map(|id| u16::from(*state.machine(MachineId(id)).expect("fixed machines")))
            .sum();
        checks.push(if sum > if self.weaken { 3 } else { 2 } {
            Check::failed("total", "total exceeds two")
        } else {
            Check::passed("total")
        });
        Ok(())
    }
}
// Independent expected outcome for the fixed three-input script: each machine
// must have processed one message, one route remains, and the global rule fails.
fn expected(model: &Network<Child, Ring>, trace: &Trace, weak: bool) -> Result<bool, ModelError> {
    let termination = if weak {
        stateless::trace::Termination::Completed
    } else {
        stateless::trace::Termination::PropertyFailed
    };
    if trace.steps.len() != 3 || trace.termination != termination {
        return Ok(false);
    }
    let last = &trace.steps[2];
    let state = model.decode_state(&last.post_state)?;
    let status = if weak {
        CheckStatus::Passed
    } else {
        CheckStatus::Failed("total exceeds two".into())
    };
    Ok((0..3).all(|id| state.machine(MachineId(id)) == Some(&1))
        && state.pending.len() == 1
        && last
            .checks
            .iter()
            .any(|check| check.id.as_str() == "global::total" && check.status == status))
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().collect();
    if args.len() != 3 {
        return Err("usage: network record|replay BINARY_TRACE".into());
    }
    let model = Network::new(
        (0..3).map(|id| (MachineId(id), Child)),
        Ring { weaken: false },
        QueuePolicy::default(),
    )?;
    match args[1].as_str() {
        "record" => {
            let inputs = [
                Input::Local(Address {
                    machine: MachineId(0),
                    value: 1,
                }),
                Input::Deliver(0),
                Input::Deliver(0),
            ];
            let trace = record(&model, inputs, RunConfig::default(), 10)?;
            if !expected(&model, &trace, false)? {
                return Err("independent expected failure not observed".into());
            }
            trace.write_to(std::fs::File::create(&args[2])?)?;
        }
        "control" => {
            let weak = Network::new(
                (0..3).map(|id| (MachineId(id), Child)),
                Ring { weaken: true },
                QueuePolicy::default(),
            )?;
            let inputs = [
                Input::Local(Address {
                    machine: MachineId(0),
                    value: 1,
                }),
                Input::Deliver(0),
                Input::Deliver(0),
            ];
            let trace = record(&weak, inputs, RunConfig::default(), 10)?;
            if !expected(&weak, &trace, true)? || expected(&weak, &trace, false)? {
                return Err(
                    "weakened-property control did not produce the expected missed failure".into(),
                );
            }
        }
        "replay" | "compare-cross-build" => {
            let trace = Trace::read_from(std::fs::File::open(&args[2])?, &ReadLimits::default())?;
            if !expected(&model, &trace, false)? {
                return Err("trace does not contain the independent expected failure".into());
            }
            if args[1] == "compare-cross-build" {
                let producer = std::env::var("STATEFUL_PRODUCER_BUILD")?;
                let consumer = std::env::var("STATEFUL_CONSUMER_BUILD")?;
                if trace.metadata.build != producer
                    || model.metadata().build != consumer
                    || producer == consumer
                {
                    return Err("build identity mismatch".into());
                }
                let strict = replay(&model, &trace, ReplayOptions::default())?;
                if !matches!(strict.outcome, ReplayOutcome::Incompatible { .. })
                    || strict.build_matches
                    || strict.steps_verified != 0
                    || strict.failure_reproduced
                {
                    return Err("strict replay did not reject before execution".into());
                }
                let result = replay(
                    &model,
                    &trace,
                    ReplayOptions {
                        allow_build_mismatch: true,
                    },
                )?;
                if result.outcome != ReplayOutcome::Exact
                    || result.build_matches
                    || result.steps_verified != 3
                    || !result.failure_reproduced
                {
                    return Err(format!("cross-build comparison failed: {result:?}").into());
                }
                println!(
                    r#"STATEFUL_COMPARISON {{"strict_outcome":"incompatible","strict_build_matches":false,"strict_steps_verified":0,"strict_failure_reproduced":false,"observation_outcome":"exact","observation_build_matches":false,"observation_steps_verified":3,"observation_failure_reproduced":true}}"#
                );
                return Ok(());
            }
            let result = replay(&model, &trace, ReplayOptions::default())?;
            if result.outcome != ReplayOutcome::Exact || !result.failure_reproduced {
                return Err(format!("replay failed: {result:?}").into());
            }
        }
        _ => return Err("unknown mode".into()),
    }
    Ok(())
}

//! Small canonical codec shared by the verifier and a fresh replay process.
use super::*;
use crate::value_codec::{DecodeLimits, TraceDecode, TraceEncode};

fn phase_wire(phase: Phase) -> (u8, u64) {
    match phase {
        Phase::Running => (0, 0),
        Phase::Cancelling => (1, 0),
        Phase::Waiting { deadline } => (2, deadline),
        Phase::Settled(Settlement::Succeeded) => (3, 0),
        Phase::Settled(Settlement::Cancelled) => (4, 0),
        Phase::Settled(Settlement::Failed) => (5, 0),
    }
}
fn phase_read(wire: (u8, u64)) -> Result<Phase, ModelError> {
    Ok(match wire {
        (0, 0) => Phase::Running,
        (1, 0) => Phase::Cancelling,
        (2, deadline) => Phase::Waiting { deadline },
        (3, 0) => Phase::Settled(Settlement::Succeeded),
        (4, 0) => Phase::Settled(Settlement::Cancelled),
        (5, 0) => Phase::Settled(Settlement::Failed),
        _ => return Err(ModelError::new("invalid/noncanonical phase")),
    })
}
impl ModelCodec for Controller {
    fn encode_state(&self, state: &State) -> Result<Vec<u8>, ModelError> {
        (
            state.now,
            state.current,
            state
                .jobs
                .iter()
                .map(|j| {
                    (
                        j.generation,
                        j.attempt,
                        phase_wire(j.phase),
                        j.resource_owned,
                    )
                })
                .collect::<Vec<_>>(),
        )
            .trace_bytes(1 << 20)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<State, ModelError> {
        type Wire = (u64, Option<u64>, Vec<(u64, u8, (u8, u64), bool)>);
        let (now, current, jobs) = Wire::from_trace(
            bytes,
            DecodeLimits {
                bytes: 1 << 20,
                allocation: 1 << 20,
                elements: 32_768,
                work: 200_000,
                ..DecodeLimits::default()
            },
        )?;
        if jobs.len() as u64 > self.bounds.generations
            || now > self.bounds.time
            || current.is_some_and(|g| g == 0 || g > jobs.len() as u64)
        {
            return Err(ModelError::new("state exceeds declared domain"));
        }
        let jobs = jobs
            .into_iter()
            .enumerate()
            .map(|(i, (generation, attempt, phase, resource_owned))| {
                if generation != i as u64 + 1 || attempt == 0 || attempt > self.bounds.attempts {
                    return Err(ModelError::new("invalid generation/attempt"));
                }
                let phase = phase_read(phase)?;
                if matches!(phase, Phase::Waiting { deadline } if deadline > self.bounds.time) {
                    return Err(ModelError::new("deadline outside domain"));
                }
                // Property-invalid but structurally valid states remain decodable so
                // evidence of the exact bug is not erased by the persistence layer.
                Ok(Job {
                    generation,
                    attempt,
                    phase,
                    resource_owned,
                })
            })
            .collect::<Result<_, ModelError>>()?;
        Ok(State { now, current, jobs })
    }
    fn encode_input(&self, input: &Input) -> Result<Vec<u8>, ModelError> {
        let wire = match *input {
            Input::Submit => (0u8, 0u64, 0u8, 0u8),
            Input::Cancel { generation } => (1, generation, 0, 0),
            Input::Complete {
                generation,
                attempt,
                outcome,
            } => (
                2,
                generation,
                attempt,
                u8::from(outcome == Outcome::RetryableFailure),
            ),
            Input::Cancelled {
                generation,
                attempt,
            } => (3, generation, attempt, 0),
            Input::Tick { now } => (4, now, 0, 0),
        };
        wire.trace_bytes(32)
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Input, ModelError> {
        let wire = <(u8, u64, u8, u8)>::from_trace(bytes, DecodeLimits::default())?;
        Ok(match wire {
            (0, 0, 0, 0) => Input::Submit,
            (1, generation, 0, 0) if generation <= self.bounds.generations => {
                Input::Cancel { generation }
            }
            (2, generation, attempt, outcome)
                if generation <= self.bounds.generations
                    && attempt > 0
                    && attempt <= self.bounds.attempts
                    && outcome <= 1 =>
            {
                Input::Complete {
                    generation,
                    attempt,
                    outcome: if outcome == 0 {
                        Outcome::Success
                    } else {
                        Outcome::RetryableFailure
                    },
                }
            }
            (3, generation, attempt, 0)
                if generation <= self.bounds.generations
                    && attempt > 0
                    && attempt <= self.bounds.attempts =>
            {
                Input::Cancelled {
                    generation,
                    attempt,
                }
            }
            (4, now, 0, 0) if now <= self.bounds.time => Input::Tick { now },
            _ => return Err(ModelError::new("invalid/noncanonical input")),
        })
    }
    fn encode_output(&self, output: &Effect) -> Result<Vec<u8>, ModelError> {
        let wire = match *output {
            Effect::Start {
                generation,
                attempt,
            } => (0u8, generation, attempt, 0u8),
            Effect::Cancel {
                generation,
                attempt,
            } => (1, generation, attempt, 0),
            Effect::Publish { generation } => (2, generation, 0, 0),
            Effect::Settle { generation, result } => (
                3,
                generation,
                0,
                match result {
                    Settlement::Succeeded => 0,
                    Settlement::Cancelled => 1,
                    Settlement::Failed => 2,
                },
            ),
            Effect::Release { generation } => (4, generation, 0, 0),
        };
        wire.trace_bytes(32)
    }
}

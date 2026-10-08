//! A finite request lifecycle. The optional bug lets a retired completion publish.
//! This is an engine fixture, not a qualified model of a production application.
use crate::model::*;

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct State {
    pub generation: u8,
    pub active: bool,
    pub ready: bool,
    pub pending: Vec<u8>,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Input {
    Start,
    Cancel,
    Complete(u8),
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Output {
    Request(u8),
    Publish(u8),
    Release(u8),
}

#[derive(Clone, Copy, Debug)]
pub struct RequestModel {
    pub inject_bug: bool,
}
impl RequestModel {
    pub fn buggy() -> Self {
        Self { inject_bug: true }
    }
    pub fn fixed() -> Self {
        Self { inject_bug: false }
    }
}

impl Model for RequestModel {
    type State = State;
    type Input = Input;
    type Output = Output;

    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "request-lifecycle".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: format!(
                "{}:{}",
                env!("STATELESS_BUILD_ID"),
                if self.inject_bug { "injected" } else { "fixed" }
            ),
        }
    }
    fn initial_state(&self) -> Result<State, ModelError> {
        Ok(State {
            generation: 0,
            active: false,
            ready: false,
            pending: vec![],
        })
    }
    fn step(&self, before: &State, input: &Input) -> Result<Transition<State, Output>, ModelError> {
        let mut state = before.clone();
        let mut outputs = Vec::new();
        match input {
            Input::Start if state.generation < 2 => {
                state.generation += 1;
                state.active = true;
                state.ready = false;
                state.pending.push(state.generation);
                outputs.push(Output::Request(state.generation));
            }
            Input::Cancel if state.active => {
                state.active = false;
                state.ready = false;
            }
            Input::Complete(generation) if state.pending.contains(generation) => {
                state.pending.retain(|g| g != generation);
                if self.inject_bug || (state.active && *generation == state.generation) {
                    state.ready = true;
                    outputs.push(Output::Publish(*generation));
                } else {
                    outputs.push(Output::Release(*generation));
                }
            }
            _ => {
                return Ok(Transition {
                    state,
                    outputs,
                    disposition: Disposition::Rejected("input is not enabled".into()),
                });
            }
        }
        Ok(Transition::accepted(state, outputs))
    }
    fn check_state(&self, state: &State) -> Result<Vec<Check>, ModelError> {
        let mut checks = Vec::new();
        self.check_state_into(state, &mut CheckSink::new(&mut checks))?;
        Ok(checks)
    }
    fn check_state_into(
        &self,
        state: &State,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        checks.extend([
            rule(
                "ready_requires_active",
                !state.ready || state.active,
                "retired request became ready",
            ),
            rule(
                "bounded_pending",
                state.pending.len() <= 2
                    && state.generation <= 2
                    && state.pending.windows(2).all(|pair| pair[0] < pair[1])
                    && state
                        .pending
                        .iter()
                        .all(|g| *g > 0 && *g <= state.generation),
                "invalid pending ledger",
            ),
        ]);
        Ok(())
    }
    fn check_transition(
        &self,
        before: &State,
        input: &Input,
        transition: &TransitionRef<'_, State, Output>,
    ) -> Result<Vec<Check>, ModelError> {
        let mut checks = Vec::new();
        self.check_transition_into(before, input, transition, &mut CheckSink::new(&mut checks))?;
        Ok(checks)
    }
    fn check_transition_into(
        &self,
        before: &State,
        input: &Input,
        transition: &TransitionRef<'_, State, Output>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        let stale = matches!(input, Input::Complete(g) if before.pending.contains(g) && (!before.active || *g != before.generation));
        checks.push(rule(
            "stale_completion_is_cleanup_only",
            !stale
                || matches!(input, Input::Complete(g) if transition.outputs == [Output::Release(*g)]),
            "stale completion published instead of releasing",
        ));
        Ok(())
    }
    fn estimated_state_bytes(&self, state: &State) -> Option<usize> {
        Some(size_of::<State>() + state.pending.capacity())
    }
}

impl Enumerate for RequestModel {
    fn inputs(&self, state: &State) -> Result<Vec<Input>, ModelError> {
        Ok(self.input_iter(state)?.collect())
    }
    fn input_iter<'a>(
        &'a self,
        state: &'a State,
    ) -> Result<Box<dyn Iterator<Item = Input> + 'a>, ModelError> {
        Ok(Box::new(
            (state.generation < 2)
                .then_some(Input::Start)
                .into_iter()
                .chain(state.active.then_some(Input::Cancel))
                .chain(state.pending.iter().copied().map(Input::Complete)),
        ))
    }
}
impl Generate for RequestModel {
    fn generate(&self, state: &State, rng: &mut Rng) -> Result<Option<Input>, ModelError> {
        let count =
            usize::from(state.generation < 2) + usize::from(state.active) + state.pending.len();
        match rng.index(count) {
            Some(index) => Ok(self.input_iter(state)?.nth(index)),
            None => Ok(None),
        }
    }
    fn is_enabled(&self, state: &State, input: &Input) -> Result<bool, ModelError> {
        Ok(match input {
            Input::Start => state.generation < 2,
            Input::Cancel => state.active,
            Input::Complete(g) => state.pending.contains(g),
        })
    }
}
impl ModelCodec for RequestModel {
    fn encode_state_into(
        &self,
        state: &State,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        let count = u8::try_from(state.pending.len())
            .map_err(|_| ModelError::new("pending list too long"))?;
        out.extend_from_slice(&[
            state.generation,
            state.active as u8,
            state.ready as u8,
            count,
        ])?;
        out.extend_from_slice(&state.pending)
    }
    fn encode_input_into(
        &self,
        input: &Input,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        match input {
            Input::Start => out.extend_from_slice(&[0]),
            Input::Cancel => out.extend_from_slice(&[1]),
            Input::Complete(g) => out.extend_from_slice(&[2, *g]),
        }
    }
    fn encode_output_into(
        &self,
        output: &Output,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        match output {
            Output::Request(g) => out.extend_from_slice(&[0, *g]),
            Output::Publish(g) => out.extend_from_slice(&[1, *g]),
            Output::Release(g) => out.extend_from_slice(&[2, *g]),
        }
    }
    fn encode_state(&self, state: &State) -> Result<Vec<u8>, ModelError> {
        let count = u8::try_from(state.pending.len())
            .map_err(|_| ModelError::new("pending list too long"))?;
        let mut bytes = vec![
            state.generation,
            state.active as u8,
            state.ready as u8,
            count,
        ];
        bytes.extend_from_slice(&state.pending);
        Ok(bytes)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<State, ModelError> {
        if bytes.len() < 4
            || bytes.len() != 4 + usize::from(bytes[3])
            || bytes[1] > 1
            || bytes[2] > 1
        {
            return Err(ModelError::new("invalid request state encoding"));
        }
        Ok(State {
            generation: bytes[0],
            active: bytes[1] != 0,
            ready: bytes[2] != 0,
            pending: bytes[4..].to_vec(),
        })
    }
    fn encode_input(&self, input: &Input) -> Result<Vec<u8>, ModelError> {
        Ok(match input {
            Input::Start => vec![0],
            Input::Cancel => vec![1],
            Input::Complete(g) => vec![2, *g],
        })
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Input, ModelError> {
        match bytes {
            [0] => Ok(Input::Start),
            [1] => Ok(Input::Cancel),
            [2, g] => Ok(Input::Complete(*g)),
            _ => Err(ModelError::new("invalid request input encoding")),
        }
    }
    fn encode_output(&self, output: &Output) -> Result<Vec<u8>, ModelError> {
        Ok(match output {
            Output::Request(g) => vec![0, *g],
            Output::Publish(g) => vec![1, *g],
            Output::Release(g) => vec![2, *g],
        })
    }
}
fn rule(id: &'static str, holds: bool, failure: &str) -> Check {
    if holds {
        Check::passed(id)
    } else {
        Check::failed(id, failure)
    }
}

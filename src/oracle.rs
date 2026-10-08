//! Independent verification with replayable history in the same engine state.
//!
//! Advancement sees inputs and observed effects, never the application's next
//! state. Comparison checks can inspect actual state. Oracle implementations must
//! be deterministic and independently derive required behavior, including effects
//! that should have been emitted. The engine cannot establish that independence.
use crate::model::*;

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct OracleState<S, H> {
    pub model: S,
    pub oracle: H,
}

pub trait Oracle<M: Model> {
    type State: Clone + Eq;
    fn metadata(&self) -> ModelMetadata;
    fn initial_state(&self) -> Result<Self::State, ModelError>;
    fn advance(
        &self,
        before: &Self::State,
        input: &M::Input,
        outputs: &[M::Output],
        disposition: &Disposition,
    ) -> Result<Self::State, ModelError>;
    /// Append comparison observations, preserving existing application checks.
    fn check_state_into(
        &self,
        history: &Self::State,
        actual: &M::State,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError>;
    fn check_transition_into(
        &self,
        _before: &OracleState<M::State, Self::State>,
        _input: &M::Input,
        _transition: &TransitionRef<'_, OracleState<M::State, Self::State>, M::Output>,
        _checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        Ok(())
    }
    fn estimated_state_bytes(&self, _history: &Self::State) -> Option<usize> {
        None
    }
}

pub trait OracleCodec<M: Model>: Oracle<M> {
    fn encode_history(&self, history: &Self::State) -> Result<Vec<u8>, ModelError>;
    fn decode_history(&self, bytes: &[u8]) -> Result<Self::State, ModelError>;
    /// Override to avoid an unbounded temporary allocation in the default.
    fn encode_history_into(
        &self,
        history: &Self::State,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_vec(self.encode_history(history)?)
    }
}

#[derive(Clone, Copy, Debug)]
pub struct WithOracle<M, O> {
    model: M,
    oracle: O,
}
impl<M, O> WithOracle<M, O> {
    pub fn new(model: M, oracle: O) -> Self {
        Self { model, oracle }
    }
    pub fn model(&self) -> &M {
        &self.model
    }
    pub fn oracle(&self) -> &O {
        &self.oracle
    }
    pub fn into_parts(self) -> (M, O) {
        (self.model, self.oracle)
    }
}
/// Oracle advancement failed after the application transition had completed.
/// The caller retains that transition so actual runtime state/effects are not lost.
pub struct ObservationError<S, O> {
    pub error: ModelError,
    pub transition: Transition<S, O>,
}
/// Result of advancing verification for an already executed transition.
pub type ObservationResult<S, H, O> =
    Result<Transition<OracleState<S, H>, O>, ObservationError<S, O>>;

impl<S, O> std::fmt::Debug for ObservationError<S, O> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ObservationError")
            .field("error", &self.error)
            .finish_non_exhaustive()
    }
}
impl<S, O> std::fmt::Display for ObservationError<S, O> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.error.fmt(f)
    }
}
impl<S, O> std::error::Error for ObservationError<S, O> {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(&self.error)
    }
}

impl<M: Model, O: Oracle<M>> WithOracle<M, O> {
    /// Attach an explicitly supplied checkpoint. Mid-run history must be restored
    /// from evidence; it cannot generally be reconstructed from application state.
    pub fn attach(&self, model: M::State, oracle: O::State) -> OracleState<M::State, O::State> {
        OracleState { model, oracle }
    }
    /// Lift an already executed application transition. Never calls the reducer.
    /// Advance once, then retain this result for checking and recording. On
    /// advancement error, `ObservationError` returns the actual transition intact.
    pub fn observe_transition(
        &self,
        before: &OracleState<M::State, O::State>,
        input: &M::Input,
        transition: Transition<M::State, M::Output>,
    ) -> ObservationResult<M::State, O::State, M::Output> {
        let history = match self.oracle.advance(
            &before.oracle,
            input,
            &transition.outputs,
            &transition.disposition,
        ) {
            Ok(history) => history,
            Err(error) => return Err(ObservationError { error, transition }),
        };
        Ok(Transition {
            state: OracleState {
                model: transition.state,
                oracle: history,
            },
            outputs: transition.outputs,
            disposition: transition.disposition,
        })
    }
}
impl<M: Model, O: Oracle<M>> Model for WithOracle<M, O> {
    type State = OracleState<M::State, O::State>;
    type Input = M::Input;
    type Output = M::Output;
    fn metadata(&self) -> ModelMetadata {
        let m = self.model.metadata();
        let o = self.oracle.metadata();
        ModelMetadata {
            // Structured, length-delimited identities avoid lossy version hashing.
            // All semantic versions remain strict even if build mismatch is allowed.
            name: format!(
                "with-oracle-v1:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}",
                m.name.len(),
                m.name,
                m.model_version,
                m.properties_version,
                m.codec_version,
                o.name.len(),
                o.name,
                o.model_version,
                o.properties_version,
                o.codec_version
            ),
            model_version: m.model_version,
            properties_version: m.properties_version,
            codec_version: m.codec_version,
            build: format!(
                "{}:{}:{}:{}",
                m.build.len(),
                m.build,
                o.build.len(),
                o.build
            ),
        }
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        Ok(OracleState {
            model: self.model.initial_state()?,
            oracle: self.oracle.initial_state()?,
        })
    }
    fn step(
        &self,
        before: &Self::State,
        input: &Self::Input,
    ) -> Result<Transition<Self::State, Self::Output>, ModelError> {
        self.observe_transition(before, input, self.model.step(&before.model, input)?)
            .map_err(|error| error.error)
    }
    fn check_state(&self, state: &Self::State) -> Result<Vec<Check>, ModelError> {
        let mut checks = Vec::new();
        self.check_state_into(state, &mut CheckSink::new(&mut checks))?;
        Ok(checks)
    }
    fn check_state_into(
        &self,
        state: &Self::State,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.model.check_state_into(&state.model, checks)?;
        self.oracle
            .check_state_into(&state.oracle, &state.model, checks)?;
        Ok(())
    }
    fn check_transition(
        &self,
        before: &Self::State,
        input: &Self::Input,
        transition: &TransitionRef<'_, Self::State, Self::Output>,
    ) -> Result<Vec<Check>, ModelError> {
        let mut checks = Vec::new();
        self.check_transition_into(before, input, transition, &mut CheckSink::new(&mut checks))?;
        Ok(checks)
    }
    fn check_transition_into(
        &self,
        before: &Self::State,
        input: &Self::Input,
        transition: &TransitionRef<'_, Self::State, Self::Output>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.model.check_transition_into(
            &before.model,
            input,
            &TransitionRef {
                state: &transition.state.model,
                outputs: transition.outputs,
                disposition: transition.disposition,
            },
            checks,
        )?;
        self.oracle
            .check_transition_into(before, input, transition, checks)?;
        Ok(())
    }
    fn estimated_state_bytes(&self, state: &Self::State) -> Option<usize> {
        let overhead =
            size_of::<Self::State>().saturating_sub(size_of::<M::State>() + size_of::<O::State>());
        self.model
            .estimated_state_bytes(&state.model)?
            .checked_add(self.oracle.estimated_state_bytes(&state.oracle)?)?
            .checked_add(overhead)
    }
}
impl<M: Enumerate, O: Oracle<M>> Enumerate for WithOracle<M, O> {
    fn inputs(&self, state: &Self::State) -> Result<Vec<Self::Input>, ModelError> {
        self.model.inputs(&state.model)
    }
    fn input_iter<'a>(
        &'a self,
        state: &'a Self::State,
    ) -> Result<Box<dyn Iterator<Item = Self::Input> + 'a>, ModelError> {
        self.model.input_iter(&state.model)
    }
}
impl<M: Generate, O: Oracle<M>> Generate for WithOracle<M, O> {
    fn generate(
        &self,
        state: &Self::State,
        rng: &mut Rng,
    ) -> Result<Option<Self::Input>, ModelError> {
        self.model.generate(&state.model, rng)
    }
    fn is_enabled(&self, state: &Self::State, input: &Self::Input) -> Result<bool, ModelError> {
        self.model.is_enabled(&state.model, input)
    }
    fn simpler_inputs(&self, input: &Self::Input) -> Vec<Self::Input> {
        self.model.simpler_inputs(input)
    }
}
impl<M: ModelCodec, O: OracleCodec<M>> ModelCodec for WithOracle<M, O> {
    fn encode_state(&self, state: &Self::State) -> Result<Vec<u8>, ModelError> {
        let mut bytes = Vec::new();
        let mut out = EncodeBuffer::new(&mut bytes, usize::MAX);
        self.encode_state_into(state, &mut out)?;
        out.finish()?;
        Ok(bytes)
    }
    fn encode_state_into(
        &self,
        state: &Self::State,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        out.extend_from_slice(b"ORC1")?;
        out.framed(|out| self.model.encode_state_into(&state.model, out))?;
        out.framed(|out| self.oracle.encode_history_into(&state.oracle, out))
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<Self::State, ModelError> {
        let mut remaining = bytes
            .strip_prefix(b"ORC1")
            .ok_or_else(|| ModelError::new("invalid oracle checkpoint format"))?;
        let model = component(&mut remaining)?;
        let oracle = component(&mut remaining)?;
        if !remaining.is_empty() {
            return Err(ModelError::new("trailing oracle checkpoint bytes"));
        }
        Ok(OracleState {
            model: self.model.decode_state(model)?,
            oracle: self.oracle.decode_history(oracle)?,
        })
    }
    fn encode_input(&self, input: &Self::Input) -> Result<Vec<u8>, ModelError> {
        self.model.encode_input(input)
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Self::Input, ModelError> {
        self.model.decode_input(bytes)
    }
    fn encode_output(&self, output: &Self::Output) -> Result<Vec<u8>, ModelError> {
        self.model.encode_output(output)
    }
    fn encode_input_into(
        &self,
        input: &Self::Input,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        self.model.encode_input_into(input, out)
    }
    fn encode_output_into(
        &self,
        output: &Self::Output,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        self.model.encode_output_into(output, out)
    }
}
fn component<'a>(remaining: &mut &'a [u8]) -> Result<&'a [u8], ModelError> {
    let header = remaining
        .get(..8)
        .ok_or_else(|| ModelError::new("truncated oracle checkpoint length"))?;
    let length = usize::try_from(u64::from_le_bytes(header.try_into().unwrap()))
        .map_err(|_| ModelError::new("oracle checkpoint length overflow"))?;
    let tail = &remaining[8..];
    let value = tail
        .get(..length)
        .ok_or_else(|| ModelError::new("truncated oracle checkpoint component"))?;
    *remaining = &tail[length..];
    Ok(value)
}

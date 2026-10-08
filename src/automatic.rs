//! Opt-in random generation from an application's declared finite input domain.
//!
//! `Auto` delegates model behavior and codecs unchanged. It supplies a generator
//! by sampling the model's stable `Enumerate::input_iter` order without collecting
//! inputs. A complete scan is one cooperative model callback: `RunLimits` can
//! stop between calls, but cannot preempt an iterator's `next()` or this scan.
//! Models should override `input_iter` for lazy generation: its default still
//! allocates `inputs()` before this adapter can apply the candidate limit.

use crate::CheckSink;
use crate::model::{
    Check, EncodeBuffer, Enumerate, Generate, Model, ModelCodec, ModelError, ModelMetadata, Rng,
    Transition, TransitionRef,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct AutoOptions {
    /// Maximum candidate entries considered by one generation/membership call.
    /// A scan may pull one more entry to distinguish an exhausted domain from
    /// an oversized one; generation never samples a capped prefix.
    pub max_candidates: usize,
}

impl Default for AutoOptions {
    fn default() -> Self {
        Self {
            max_candidates: 100_000,
        }
    }
}

/// Add automatic random generation to an enumerated model. This is an explicit
/// wrapper; existing custom `Generate` implementations are unaffected.
#[derive(Clone, Debug)]
pub struct Auto<M> {
    model: M,
    options: AutoOptions,
}

impl<M> Auto<M> {
    pub fn new(model: M) -> Self {
        Self {
            model,
            options: AutoOptions::default(),
        }
    }

    pub fn with_options(model: M, options: AutoOptions) -> Result<Self, ModelError> {
        if options.max_candidates == 0 {
            return Err(ModelError::new(
                "automatic generation max_candidates must be positive",
            ));
        }
        Ok(Self { model, options })
    }

    pub fn model(&self) -> &M {
        &self.model
    }
    pub fn into_inner(self) -> M {
        self.model
    }
    pub fn options(&self) -> AutoOptions {
        self.options
    }
}

impl<M: Model> Model for Auto<M> {
    type State = M::State;
    type Input = M::Input;
    type Output = M::Output;

    fn metadata(&self) -> ModelMetadata {
        self.model.metadata()
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        self.model.initial_state()
    }
    fn step(
        &self,
        state: &Self::State,
        input: &Self::Input,
    ) -> Result<Transition<Self::State, Self::Output>, ModelError> {
        self.model.step(state, input)
    }
    fn check_state(&self, state: &Self::State) -> Result<Vec<Check>, ModelError> {
        self.model.check_state(state)
    }
    fn check_state_into(
        &self,
        state: &Self::State,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.model.check_state_into(state, checks)
    }
    fn check_transition(
        &self,
        before: &Self::State,
        input: &Self::Input,
        transition: &TransitionRef<'_, Self::State, Self::Output>,
    ) -> Result<Vec<Check>, ModelError> {
        self.model.check_transition(before, input, transition)
    }
    fn check_transition_into(
        &self,
        before: &Self::State,
        input: &Self::Input,
        transition: &TransitionRef<'_, Self::State, Self::Output>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.model
            .check_transition_into(before, input, transition, checks)
    }
    fn estimated_state_bytes(&self, state: &Self::State) -> Option<usize> {
        self.model.estimated_state_bytes(state)
    }
}

impl<M: ModelCodec> ModelCodec for Auto<M> {
    fn encode_state(&self, state: &Self::State) -> Result<Vec<u8>, ModelError> {
        self.model.encode_state(state)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<Self::State, ModelError> {
        self.model.decode_state(bytes)
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
    fn encode_state_into(
        &self,
        state: &Self::State,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        self.model.encode_state_into(state, out)
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

impl<M: Enumerate> Enumerate for Auto<M> {
    fn inputs(&self, state: &Self::State) -> Result<Vec<Self::Input>, ModelError> {
        self.model.inputs(state)
    }
    fn input_iter<'a>(
        &'a self,
        state: &'a Self::State,
    ) -> Result<Box<dyn Iterator<Item = Self::Input> + 'a>, ModelError> {
        self.model.input_iter(state)
    }
}

impl<M: Enumerate> Generate for Auto<M> {
    /// Reservoir sampling is uniform over iterator entries; repeated entries
    /// therefore give that input more weight. Only the selected input is retained.
    /// The RNG advances only after a successful complete scan. An empty domain
    /// returns None without advancing it; errors never return a partial choice.
    fn generate(
        &self,
        state: &Self::State,
        rng: &mut Rng,
    ) -> Result<Option<Self::Input>, ModelError> {
        let mut candidate_rng = rng.clone();
        let mut selected = None;
        let mut count = 0;
        for input in self.model.input_iter(state)? {
            count = next_count(count, self.options.max_candidates)?;
            if count == 1 || candidate_rng.index(count) == Some(0) {
                selected = Some(input);
            }
        }
        *rng = candidate_rng;
        Ok(selected)
    }

    /// Membership is checked in declared order and stops at the first match.
    /// Its candidate cap bounds the inspected prefix; a match within the cap
    /// does not require validating the size of the rest of the domain.
    fn is_enabled(&self, state: &Self::State, input: &Self::Input) -> Result<bool, ModelError> {
        let mut count = 0;
        for candidate in self.model.input_iter(state)? {
            count = next_count(count, self.options.max_candidates)?;
            if candidate == *input {
                return Ok(true);
            }
        }
        Ok(false)
    }
}

fn next_count(count: usize, maximum: usize) -> Result<usize, ModelError> {
    let next = count
        .checked_add(1)
        .ok_or_else(|| ModelError::new("automatic input candidate count overflow"))?;
    if next > maximum {
        return Err(ModelError::new(
            "automatic input domain exceeds max_candidates",
        ));
    }
    Ok(next)
}

#[cfg(test)]
mod tests {
    use super::next_count;

    #[test]
    fn candidate_counter_never_wraps_at_usize_max() {
        assert_eq!(next_count(usize::MAX - 1, usize::MAX).unwrap(), usize::MAX);
        assert!(
            next_count(usize::MAX, usize::MAX)
                .unwrap_err()
                .0
                .contains("overflow")
        );
    }
}

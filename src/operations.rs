//! Internal execution operations shared by native and future foreign adapters.
//!
//! Foreign value owners do not implement semantic `Eq` or `Hash`. Comparisons,
//! hashing, and cursor advancement have explicit error channels instead. This is
//! an internal foundation, not a public foreign interface or an ABI declaration.

use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hash};

use crate::model::{
    CheckSink, EncodeBuffer, Enumerate, Generate, Model, ModelCodec, ModelError, ModelMetadata,
    Rng, Transition, TransitionRef,
};

/// Operations that do not require persistence or state identity.
///
/// Values retained by the engine are immutable snapshots. Foreign adapters must
/// create and isolate those snapshots in fallible adapter operations; duplicating
/// an owner alone does not isolate mutable application state. `Input::clone`
/// duplicates an already retained owner, without calling fallible foreign code.
pub(crate) trait ModelOps {
    type State;
    type Input: Clone;
    type Output;

    fn initial_state(&self) -> Result<Self::State, ModelError>;
    fn step(
        &self,
        state: &Self::State,
        input: &Self::Input,
    ) -> Result<Transition<Self::State, Self::Output>, ModelError>;
    fn check_state_into(
        &self,
        state: &Self::State,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError>;
    fn check_transition_into(
        &self,
        _before: &Self::State,
        _input: &Self::Input,
        _transition: &TransitionRef<'_, Self::State, Self::Output>,
        _checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        Ok(())
    }
    fn estimated_state_bytes(&self, _state: &Self::State) -> Result<Option<usize>, ModelError> {
        Ok(None)
    }
}

/// An ordered input domain with distinct end-of-domain and failure results.
///
/// Implementations close their cursor in `Drop`, including on early bounds,
/// cancellation, and callback errors. Cleanup must not throw or reenter engine
/// execution. A cursor borrows or retains its source snapshot for its lifetime.
pub(crate) trait InputCursor {
    type Input;

    fn next(&mut self) -> Result<Option<Self::Input>, ModelError>;
}

/// Finite exploration with fallible semantic identity and input production.
pub(crate) trait EnumerateOps: ModelOps {
    type Cursor<'a>: InputCursor<Input = Self::Input>
    where
        Self: 'a;

    fn input_cursor<'a>(&'a self, state: &'a Self::State) -> Result<Self::Cursor<'a>, ModelError>;
    fn state_equal(&self, left: &Self::State, right: &Self::State) -> Result<bool, ModelError>;

    /// Equal states must hash equally. Every collision is resolved by equality.
    /// The default puts all states in one bucket for equality-only exploration.
    fn state_hash(&self, _state: &Self::State) -> Result<u64, ModelError> {
        Ok(0)
    }
}

/// Semantic input identity is separate from storage tokens and persistence.
pub(crate) trait InputIdentityOps: ModelOps {
    fn input_equal(&self, left: &Self::Input, right: &Self::Input) -> Result<bool, ModelError>;
}

/// Optional canonical persistence; execution never requires these operations.
pub(crate) trait CodecOps: ModelOps {
    fn metadata(&self) -> Result<ModelMetadata, ModelError>;
    fn encode_state_into(
        &self,
        state: &Self::State,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError>;
    fn decode_state(&self, bytes: &[u8]) -> Result<Self::State, ModelError>;
    fn encode_input_into(
        &self,
        input: &Self::Input,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError>;
    fn decode_input(&self, bytes: &[u8]) -> Result<Self::Input, ModelError>;
    fn encode_output_into(
        &self,
        output: &Self::Output,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError>;
}

/// Stateful generation and causal simplification with explicit callback errors.
pub(crate) trait GenerateOps: InputIdentityOps {
    type Simplifications<'a>: InputCursor<Input = Self::Input>
    where
        Self: 'a;

    fn generate(
        &self,
        state: &Self::State,
        rng: &mut Rng,
    ) -> Result<Option<Self::Input>, ModelError>;
    fn is_enabled(&self, _state: &Self::State, _input: &Self::Input) -> Result<bool, ModelError> {
        Ok(true)
    }
    /// The cursor must own or retain any input data it needs after this call.
    fn simpler_inputs<'a>(
        &'a self,
        input: &Self::Input,
    ) -> Result<Self::Simplifications<'a>, ModelError>;
}

/// A statically dispatched adapter that preserves the public Rust model traits.
/// Non-search operations keep only a borrowed model; hashing is initialized only
/// when the caller selects the enumeration adapter.
pub(crate) struct NativeOps<'a, M: ?Sized, H = ()> {
    model: &'a M,
    hashing: H,
}

impl<'a, M: Model + ?Sized> NativeOps<'a, M> {
    pub(crate) fn new(model: &'a M) -> Self {
        Self { model, hashing: () }
    }

    pub(crate) fn enumerating(model: &'a M) -> NativeOps<'a, M, RandomState>
    where
        M: Enumerate,
        M::State: Hash,
    {
        NativeOps {
            model,
            hashing: RandomState::new(),
        }
    }
}

impl<M: Model + ?Sized, H> ModelOps for NativeOps<'_, M, H> {
    type State = M::State;
    type Input = M::Input;
    type Output = M::Output;

    fn initial_state(&self) -> Result<Self::State, ModelError> {
        <M as Model>::initial_state(self.model)
    }

    fn step(
        &self,
        state: &Self::State,
        input: &Self::Input,
    ) -> Result<Transition<Self::State, Self::Output>, ModelError> {
        <M as Model>::step(self.model, state, input)
    }

    fn check_state_into(
        &self,
        state: &Self::State,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        <M as Model>::check_state_into(self.model, state, checks)
    }

    fn check_transition_into(
        &self,
        before: &Self::State,
        input: &Self::Input,
        transition: &TransitionRef<'_, Self::State, Self::Output>,
        checks: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        <M as Model>::check_transition_into(self.model, before, input, transition, checks)
    }

    fn estimated_state_bytes(&self, state: &Self::State) -> Result<Option<usize>, ModelError> {
        Ok(<M as Model>::estimated_state_bytes(self.model, state))
    }
}

impl<M: Model + ?Sized, H> InputIdentityOps for NativeOps<'_, M, H> {
    fn input_equal(&self, left: &Self::Input, right: &Self::Input) -> Result<bool, ModelError> {
        Ok(left == right)
    }
}

impl<M: ModelCodec + ?Sized, H> CodecOps for NativeOps<'_, M, H> {
    fn metadata(&self) -> Result<ModelMetadata, ModelError> {
        Ok(<M as Model>::metadata(self.model))
    }
    fn encode_state_into(
        &self,
        state: &Self::State,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        <M as ModelCodec>::encode_state_into(self.model, state, out)
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<Self::State, ModelError> {
        <M as ModelCodec>::decode_state(self.model, bytes)
    }
    fn encode_input_into(
        &self,
        input: &Self::Input,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        <M as ModelCodec>::encode_input_into(self.model, input, out)
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Self::Input, ModelError> {
        <M as ModelCodec>::decode_input(self.model, bytes)
    }
    fn encode_output_into(
        &self,
        output: &Self::Output,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        <M as ModelCodec>::encode_output_into(self.model, output, out)
    }
}

pub(crate) struct OwnedCursor<I>(pub(crate) std::vec::IntoIter<I>);

impl<I> InputCursor for OwnedCursor<I> {
    type Input = I;
    fn next(&mut self) -> Result<Option<I>, ModelError> {
        Ok(self.0.next())
    }
}

impl<M: Generate + ?Sized, H> GenerateOps for NativeOps<'_, M, H> {
    type Simplifications<'a>
        = OwnedCursor<M::Input>
    where
        Self: 'a;

    fn generate(
        &self,
        state: &Self::State,
        rng: &mut Rng,
    ) -> Result<Option<Self::Input>, ModelError> {
        <M as Generate>::generate(self.model, state, rng)
    }
    fn is_enabled(&self, state: &Self::State, input: &Self::Input) -> Result<bool, ModelError> {
        <M as Generate>::is_enabled(self.model, state, input)
    }
    fn simpler_inputs<'a>(
        &'a self,
        input: &Self::Input,
    ) -> Result<Self::Simplifications<'a>, ModelError> {
        Ok(OwnedCursor(
            <M as Generate>::simpler_inputs(self.model, input).into_iter(),
        ))
    }
}

pub(crate) struct NativeCursor<'a, I> {
    inner: Box<dyn Iterator<Item = I> + 'a>,
}

impl<I> InputCursor for NativeCursor<'_, I> {
    type Input = I;

    fn next(&mut self) -> Result<Option<I>, ModelError> {
        Ok(self.inner.next())
    }
}

impl<M: Enumerate + ?Sized> EnumerateOps for NativeOps<'_, M, RandomState>
where
    M::State: Hash,
{
    type Cursor<'a>
        = NativeCursor<'a, M::Input>
    where
        Self: 'a;

    fn input_cursor<'a>(&'a self, state: &'a Self::State) -> Result<Self::Cursor<'a>, ModelError> {
        Ok(NativeCursor {
            inner: <M as Enumerate>::input_iter(self.model, state)?,
        })
    }

    fn state_equal(&self, left: &Self::State, right: &Self::State) -> Result<bool, ModelError> {
        Ok(left == right)
    }

    fn state_hash(&self, state: &Self::State) -> Result<u64, ModelError> {
        Ok(self.hashing.hash_one(state))
    }
}

// Automatic generation needs ordered inputs and input equality, without adding
// a Hash bound to the public Auto model. This adapter uses equality-only search
// identity if an internal caller also chooses to enumerate it.
impl<M: Enumerate + ?Sized> EnumerateOps for NativeOps<'_, M> {
    type Cursor<'a>
        = NativeCursor<'a, M::Input>
    where
        Self: 'a;

    fn input_cursor<'a>(&'a self, state: &'a Self::State) -> Result<Self::Cursor<'a>, ModelError> {
        Ok(NativeCursor {
            inner: <M as Enumerate>::input_iter(self.model, state)?,
        })
    }
    fn state_equal(&self, left: &Self::State, right: &Self::State) -> Result<bool, ModelError> {
        Ok(left == right)
    }
}

#[cfg(test)]
mod production_tests;
#[cfg(test)]
mod tests;

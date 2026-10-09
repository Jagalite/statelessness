//! Semantic paired execution using the existing WithOracle engine.
//! Reference advancement ignores application observations. Projections define
//! equivalence; codecs retain each implementation's independent checkpoint.
use crate::{model::*, oracle::*};
use std::fmt::Debug;

pub trait Projection<M: Model, R: Model<Input = M::Input>> {
    type State: Eq + Debug;
    type Effect: Eq + Debug;
    fn actual_state(&self, state: &M::State) -> Result<Self::State, ModelError>;
    fn reference_state(&self, state: &R::State) -> Result<Self::State, ModelError>;
    fn actual_effect(&self, effect: &M::Output) -> Result<Self::Effect, ModelError>;
    fn reference_effect(&self, effect: &R::Output) -> Result<Self::Effect, ModelError>;
    /// Versioned projection identity, independent of either reducer identity.
    fn metadata(&self) -> ModelMetadata;
}

pub struct Paired<R, P> {
    pub reference: R,
    pub projection: P,
}
impl<M, R, P> Oracle<M> for Paired<R, P>
where
    M: Model,
    R: Model<Input = M::Input>,
    P: Projection<M, R>,
{
    type State = R::State;
    fn metadata(&self) -> ModelMetadata {
        let r = self.reference.metadata();
        let p = self.projection.metadata();
        ModelMetadata {
            name: format!(
                "paired-v1:{}:{}:{}:{}:{}:{}:{}:{}",
                r.name.len(),
                r.name,
                r.model_version,
                r.properties_version,
                r.codec_version,
                p.name.len(),
                p.name,
                p.model_version
            ),
            build: format!(
                "{}:{}:{}:{}",
                r.build.len(),
                r.build,
                p.build.len(),
                p.build
            ),
            model_version: p.model_version,
            properties_version: p.properties_version,
            codec_version: p.codec_version,
        }
    }
    fn estimated_state_bytes(&self, history: &R::State) -> Option<usize> {
        self.reference.estimated_state_bytes(history)
    }
    fn initial_state(&self) -> Result<R::State, ModelError> {
        self.reference.initial_state()
    }
    fn advance(
        &self,
        b: &R::State,
        i: &M::Input,
        _: &[M::Output],
        _: &Disposition,
    ) -> Result<R::State, ModelError> {
        Ok(self.reference.step(b, i)?.state)
    }
    fn check_state_into(
        &self,
        h: &R::State,
        a: &M::State,
        c: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        self.reference.check_state_into(h, c)?;
        compare(
            c,
            "pair.state",
            self.projection.actual_state(a)?,
            self.projection.reference_state(h)?,
        );
        Ok(())
    }
    fn check_transition_into(
        &self,
        b: &OracleState<M::State, R::State>,
        i: &M::Input,
        t: &TransitionRef<'_, OracleState<M::State, R::State>, M::Output>,
        c: &mut CheckSink<'_>,
    ) -> Result<(), ModelError> {
        // Pure reference recomputation keeps oracle history finite and explicit.
        let r = self.reference.step(&b.oracle, i)?;
        self.reference.check_transition_into(
            &b.oracle,
            i,
            &TransitionRef {
                state: &r.state,
                outputs: &r.outputs,
                disposition: &r.disposition,
            },
            c,
        )?;
        let a = t
            .outputs
            .iter()
            .map(|e| self.projection.actual_effect(e))
            .collect::<Result<Vec<_>, _>>()?;
        let e = r
            .outputs
            .iter()
            .map(|e| self.projection.reference_effect(e))
            .collect::<Result<Vec<_>, _>>()?;
        compare(c, "pair.effects", a, e);
        compare(c, "pair.outcome", t.disposition.clone(), r.disposition);
        Ok(())
    }
}
fn compare<T: Eq + Debug>(c: &mut CheckSink<'_>, id: &'static str, a: T, r: T) {
    c.push(if a == r {
        Check::passed(id)
    } else {
        Check::failed(id, format!("actual={a:?}; reference={r:?}"))
    });
}
impl<M, R, P> OracleCodec<M> for Paired<R, P>
where
    M: Model,
    R: ModelCodec<Input = M::Input>,
    P: Projection<M, R>,
{
    fn encode_history(&self, h: &R::State) -> Result<Vec<u8>, ModelError> {
        self.reference.encode_state(h)
    }
    fn encode_history_into(
        &self,
        h: &R::State,
        out: &mut EncodeBuffer<'_>,
    ) -> Result<(), ModelError> {
        self.reference.encode_state_into(h, out)
    }
    fn decode_history(&self, b: &[u8]) -> Result<R::State, ModelError> {
        self.reference.decode_state(b)
    }
}

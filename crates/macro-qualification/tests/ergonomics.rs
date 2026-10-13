use stateless::{
    Check, CheckStatus, Generate, Model, ModelCodec, ModelError, ModelMetadata, Rng, Transition,
};
use statelessness_macros::model;

#[derive(Clone)]
struct Simple;
#[model(state=u8,input=u8,output=u8,codec,initial_default,generate)]
impl Simple {
    #[stateless(metadata)]
    fn identity(&self) -> ModelMetadata {
        ModelMetadata {
            name: "ergonomic-simple".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "fixture-v1".into(),
        }
    }
    #[stateless(step)]
    fn reduce(&self, state: &u8, input: &u8) -> Result<Transition<u8, u8>, ModelError> {
        Ok(Transition::accepted(
            state.saturating_add(*input),
            vec![*input],
        ))
    }
    #[stateless(state_check(id = "simple.bound"))]
    fn bound(&self, state: &u8) -> Result<CheckStatus, ModelError> {
        Ok(if *state <= 5 {
            CheckStatus::Passed
        } else {
            CheckStatus::Failed("bound".into())
        })
    }
    #[stateless(inputs)]
    fn domain(&self, _: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![0, 1, 2])
    }
}

#[derive(statelessness_macros::ModelCodec)]
#[model_codec(version = 1)]
struct Handwritten<T>(T)
where
    T: Clone + Eq;
impl<T: Clone + Eq> Model for Handwritten<T> {
    type State = T;
    type Input = T;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        Simple.metadata()
    }
    fn initial_state(&self) -> Result<T, ModelError> {
        Ok(self.0.clone())
    }
    fn step(&self, _: &T, input: &T) -> Result<Transition<T, ()>, ModelError> {
        Ok(Transition::accepted(input.clone(), vec![]))
    }
    fn check_state(&self, _: &T) -> Result<Vec<Check>, ModelError> {
        Ok(vec![Check::passed("handwritten")])
    }
}
#[derive(statelessness_macros::ModelCodec)]
#[model_codec(version = 2)]
struct WrongVersion;
impl Model for WrongVersion {
    type State = u8;
    type Input = u8;
    type Output = ();
    fn metadata(&self) -> ModelMetadata {
        Simple.metadata()
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, _: &u8, input: &u8) -> Result<Transition<u8, ()>, ModelError> {
        Ok(Transition::accepted(*input, vec![]))
    }
    fn check_state(&self, _: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![])
    }
}

#[test]
fn generated_generator_matches_existing_auto_seed_and_causality_contract() {
    let auto = stateless::automatic::Auto::new(Simple);
    let (mut generated_rng, mut auto_rng) = (Rng::new(918), Rng::new(918));
    for _ in 0..100 {
        assert_eq!(
            Simple.generate(&0, &mut generated_rng).unwrap(),
            auto.generate(&0, &mut auto_rng).unwrap()
        );
    }
    assert_eq!(Simple.initial_state().unwrap(), 0);
    assert!(Simple.is_enabled(&0, &1).unwrap());
    assert!(!Simple.is_enabled(&0, &9).unwrap());
    assert_eq!(
        Simple.stateless_declared_properties_for_test(),
        Simple::stateless_declared_properties()
    );
}
impl Simple {
    fn stateless_declared_properties_for_test(
        &self,
    ) -> Vec<stateless::assurance::RequiredProperty> {
        vec![stateless::assurance::RequiredProperty {
            id: "simple.bound".into(),
            phase: stateless::assurance::PropertyPhase::State,
        }]
    }
}
#[test]
fn handwritten_generic_model_gets_canonical_codec_without_adapter_macro() {
    let model = Handwritten(0u16);
    assert_eq!(model.encode_state(&0x1234).unwrap(), [0x34, 0x12]);
    assert_eq!(model.decode_input(&[7, 0]).unwrap(), 7);
    assert!(model.decode_input(&[7, 0, 0]).is_err());
    assert!(
        WrongVersion
            .encode_state(&1)
            .unwrap_err()
            .to_string()
            .contains("version")
    );
}
#[test]
fn shared_sampling_helpers_preserve_rng_on_domain_failure() {
    let mut rng = Rng::new(8);
    let mut untouched = rng.clone();
    assert!(
        stateless::automatic::sample_enumerated(
            &Simple,
            &0,
            &mut rng,
            stateless::automatic::AutoOptions { max_candidates: 2 }
        )
        .is_err()
    );
    assert_eq!(rng.next_u64(), untouched.next_u64());
}

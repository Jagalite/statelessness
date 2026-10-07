use stateless::explore::{SearchConfig, enumerate};
use stateless::{Check, Enumerate, Model, ModelError, ModelMetadata, Transition};

struct Counter;
impl Model for Counter {
    type State = u8;
    type Input = ();
    type Output = ();

    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: "counter-example".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "example-v1".into(),
        }
    }
    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &u8, _: &()) -> Result<Transition<u8, ()>, ModelError> {
        let next = state
            .checked_add(1)
            .ok_or_else(|| ModelError::new("counter overflow"))?;
        Ok(Transition::accepted(next, vec![]))
    }
    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if *state <= 2 {
            Check::passed("at_most_two")
        } else {
            Check::failed("at_most_two", format!("counter reached {state}"))
        }])
    }
}
impl Enumerate for Counter {
    fn inputs(&self, _: &u8) -> Result<Vec<()>, ModelError> {
        Ok(vec![()])
    }
}

fn main() -> Result<(), ModelError> {
    let report = enumerate(&Counter, SearchConfig::default())?;
    let failure = report
        .failure
        .expect("the deliberately faulty counter must fail");
    println!(
        "Found {} after {} increments",
        failure.violations[0].check.id,
        failure.inputs.len()
    );
    assert_eq!(failure.inputs.len(), 3);
    Ok(())
}

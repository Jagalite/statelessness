//! cargo run --example logging
//! Text diagnostics and full replay evidence from the same executed transitions.
use stateless::Model;
use stateless::demo::{Input, RequestModel};
use stateless::execution::{ReplayOptions, ReplayOutcome, replay};
use stateless::monitor::Recorder;
use stateless::observation::{
    EncodedPayloads, Event, LogLevel, ObservationOptions, Observer, SnapshotPolicy, TextObserver,
};
use stateless::trace::{ReadLimits, RunConfig, Trace};
use std::io;
use std::num::NonZeroU64;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let model = RequestModel::fixed();
    let mut state = model.initial_state()?;
    let config = RunConfig::default();
    let mut recorder = Recorder::new(&model, &state, config.clone(), 32)?;
    let mut logger = TextObserver::new(
        io::stdout().lock(),
        "request-example",
        ObservationOptions {
            level: LogLevel::Trace,
            snapshots: SnapshotPolicy::EveryTransition,
            ..ObservationOptions::default()
        },
        EncodedPayloads::new(64 * 1024),
    );
    logger.observe(
        &model,
        Event::Started {
            metadata: &model.metadata(),
            config: &config,
        },
    )?;
    // The recorder already checked this state. Reuse those observations.
    let initial = recorder.snapshot();
    logger.observe(
        &model,
        Event::Checkpoint {
            sequence: 0,
            state: &state,
            checks: &initial.initial_checks,
        },
    )?;
    for (index, input) in [Input::Start, Input::Complete(1), Input::Cancel]
        .iter()
        .enumerate()
    {
        let transition = model.step(&state, input)?;
        let checks = recorder.observe(&model, &state, input, &transition)?;
        logger.observe(
            &model,
            Event::Transition {
                sequence: NonZeroU64::new(index as u64 + 1).unwrap(),
                before: &state,
                input,
                transition: transition.as_ref(),
                checks,
            },
        )?;
        state = transition.state;
    }
    logger.observe(
        &model,
        Event::Finished {
            transitions: recorder.observed_steps(),
            reason: "example inputs consumed",
        },
    )?;
    logger.flush()?;
    // Export without copying the recorder's retained history. A file writer can
    // be supplied instead; flushing and durable publication belong to the host.
    let mut bytes = Vec::new();
    recorder.write_to(&mut bytes)?;
    let restored = Trace::read_from(bytes.as_slice(), &ReadLimits::default())?;
    assert_eq!(
        replay(&model, &restored, ReplayOptions::default())?.outcome,
        ReplayOutcome::Exact
    );
    Ok(())
}

//! A complete application-owned model, regression check, and replay executable.
//! See examples/README.md for commands and adaptation guidance.
use stateless::automatic::Auto;
use stateless::execution::{ReplayOptions, ReplayOutcome, record, replay};
use stateless::explore::{
    FuzzConfig, SearchConfig, SearchReport, SearchTermination, ShrinkConfig, enumerate, fuzz,
    shrink,
};
use stateless::trace::{ReadLimits, RunConfig, Termination, Trace};
use stateless::{Check, Enumerate, Model, ModelCodec, ModelError, ModelMetadata, Transition};
use std::{error::Error, fs::File, path::Path, process::ExitCode};

#[derive(Clone, Debug, PartialEq, Eq)]
enum Input {
    Increment,
    Reset,
}

struct Counter {
    fixed: bool,
}

// In an application, call the same pure reducer from production and this adapter.
// Outputs describe effects; simulation must never execute real external effects.
fn reduce(state: u8, input: &Input, fixed: bool) -> u8 {
    match input {
        Input::Reset => 0,
        Input::Increment if fixed => state.saturating_add(1).min(2),
        Input::Increment => state.saturating_add(1),
    }
}

impl Model for Counter {
    type State = u8;
    type Input = Input;
    type Output = ();

    fn metadata(&self) -> ModelMetadata {
        // Fingerprint this self-contained example. Real adapters must fingerprint
        // all application code/configuration that can change modeled behavior.
        let source = include_bytes!("application.rs");
        let hash = source.iter().fold(0xcbf29ce484222325u64, |hash, byte| {
            (hash ^ u64::from(*byte)).wrapping_mul(0x100000001b3)
        });
        ModelMetadata {
            name: "application-counter".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: format!("example-{hash:016x}-fixed={}", self.fixed),
        }
    }

    fn initial_state(&self) -> Result<u8, ModelError> {
        Ok(0)
    }

    fn step(&self, state: &u8, input: &Input) -> Result<Transition<u8, ()>, ModelError> {
        Ok(Transition::accepted(
            reduce(*state, input, self.fixed),
            vec![],
        ))
    }

    fn check_state(&self, state: &u8) -> Result<Vec<Check>, ModelError> {
        Ok(vec![if *state <= 2 {
            Check::passed("counter.at_most_two")
        } else {
            Check::failed("counter.at_most_two", format!("counter reached {state}"))
        }])
    }
}

impl Enumerate for Counter {
    fn inputs(&self, _: &u8) -> Result<Vec<Input>, ModelError> {
        Ok(vec![Input::Increment, Input::Reset])
    }
}

impl ModelCodec for Counter {
    fn encode_state(&self, state: &u8) -> Result<Vec<u8>, ModelError> {
        Ok(vec![*state])
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<u8, ModelError> {
        match bytes {
            [state @ 0..=3] => Ok(*state),
            _ => Err(ModelError::new("expected one counter byte in 0..=3")),
        }
    }
    fn encode_input(&self, input: &Input) -> Result<Vec<u8>, ModelError> {
        Ok(vec![match input {
            Input::Increment => 0,
            Input::Reset => 1,
        }])
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Input, ModelError> {
        match bytes {
            [0] => Ok(Input::Increment),
            [1] => Ok(Input::Reset),
            _ => Err(ModelError::new("invalid input tag or length")),
        }
    }
    fn encode_output(&self, _: &()) -> Result<Vec<u8>, ModelError> {
        Ok(vec![])
    }
}

// A bounded run without a finding is not necessarily a completed graph check.
fn require_exhausted(report: &SearchReport<Input>) -> Result<(), ModelError> {
    if report.failure.is_some() {
        return Err(ModelError::new(format!(
            "property failure: {:?}",
            report.failure
        )));
    }
    if report.termination != SearchTermination::GraphExhausted || report.skipped_checks != 0 {
        return Err(ModelError::new(format!(
            "incomplete verification: {:?}, {} skipped checks",
            report.termination, report.skipped_checks
        )));
    }
    Ok(())
}

fn save(path: &Path, trace: &Trace) -> Result<(), Box<dyn Error>> {
    let mut file = File::create_new(path)?;
    trace.write_to(&mut file)?;
    file.sync_all()?;
    Ok(())
}

fn find(directory: &Path) -> Result<u8, Box<dyn Error>> {
    let model = Auto::new(Counter { fixed: false });
    let config = FuzzConfig {
        seed: 42,
        cases: 100,
        max_steps: 20,
        ..FuzzConfig::default()
    };
    let report = fuzz(&model, config.clone())?;
    let failure = report.failure.ok_or_else(|| {
        ModelError::new(format!(
            "no counterexample found; search ended with {:?}",
            report.termination
        ))
    })?;
    // Exclusive directory creation prevents accidental replacement of evidence.
    std::fs::create_dir(directory)?;
    let run = RunConfig {
        strategy: "application-starter".into(),
        seed: Some(config.seed),
        parameters: vec![
            ("example.cases".into(), config.cases.to_string()),
            ("example.max_steps".into(), config.max_steps.to_string()),
        ],
    };
    let original = record(
        &model,
        failure.inputs.clone(),
        run.clone(),
        failure.inputs.len(),
    )?;
    if original.termination != Termination::PropertyFailed {
        return Err("original failure did not reproduce during recording".into());
    }
    save(&directory.join("original.sttrace"), &original)?;
    let shrunk = shrink(&model, &failure, ShrinkConfig::default())?;
    if !shrunk.validated_original {
        return Err("shrinking did not validate the original failure".into());
    }
    let minimized = record(
        &model,
        shrunk.minimized.inputs.clone(),
        run,
        shrunk.minimized.inputs.len(),
    )?;
    if minimized.termination != Termination::PropertyFailed {
        return Err("minimized failure did not reproduce during recording".into());
    }
    save(&directory.join("minimized.sttrace"), &minimized)?;
    println!(
        "Property failure: {} -> {} inputs; shrink {:?}; artifacts in {}",
        failure.inputs.len(),
        shrunk.minimized.inputs.len(),
        shrunk.termination,
        directory.display()
    );
    Ok(1)
}

fn run(args: &[String]) -> Result<u8, Box<dyn Error>> {
    match args
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["check"] => {
            let report = enumerate(&Counter { fixed: true }, SearchConfig::default())?;
            require_exhausted(&report)?;
            println!(
                "Graph exhausted: {} states, {} transitions, no skipped checks",
                report.states, report.transitions
            );
            Ok(0)
        }
        ["find", directory] => find(Path::new(directory)),
        ["replay", path] | ["replay-fixed", path] => {
            let fixed = args[0] == "replay-fixed";
            let trace = Trace::read_from(File::open(path)?, &ReadLimits::default())?;
            let report = replay(
                &Counter { fixed },
                &trace,
                ReplayOptions {
                    allow_build_mismatch: fixed,
                },
            )?;
            println!(
                "{:?}; failure reproduced: {}; build matches: {}; recorded termination: {:?}",
                report.outcome, report.failure_reproduced, report.build_matches, trace.termination
            );
            Ok(match report.outcome {
                ReplayOutcome::Exact => 0,
                ReplayOutcome::Diverged { .. } => 3,
                ReplayOutcome::Incompatible { .. } => 4,
            })
        }
        _ => Err(
            "usage: application check | find NEW_DIRECTORY | replay TRACE | replay-fixed TRACE"
                .into(),
        ),
    }
}

fn main() -> ExitCode {
    match run(&std::env::args().skip(1).collect::<Vec<_>>()) {
        Ok(code) => ExitCode::from(code),
        Err(error) => {
            eprintln!("{error}");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn application_regression() -> Result<(), ModelError> {
        let report = enumerate(&Counter { fixed: true }, SearchConfig::default())?;
        require_exhausted(&report)
    }

    #[test]
    fn regression_gate_rejects_bug_and_incomplete_search() {
        let buggy = enumerate(&Counter { fixed: false }, SearchConfig::default()).unwrap();
        assert!(require_exhausted(&buggy).is_err());
        let bounded = enumerate(
            &Counter { fixed: true },
            SearchConfig {
                max_depth: 1,
                ..SearchConfig::default()
            },
        )
        .unwrap();
        assert!(bounded.failure.is_none());
        assert!(require_exhausted(&bounded).is_err());
        let mut skipped = enumerate(&Counter { fixed: true }, SearchConfig::default()).unwrap();
        skipped.skipped_checks = 1;
        assert!(require_exhausted(&skipped).is_err());
    }

    #[test]
    fn restored_payloads_reject_invalid_values_and_trailing_bytes() {
        let model = Counter { fixed: false };
        for bytes in [&[][..], &[4], &[0, 1]] {
            assert!(model.decode_state(bytes).is_err());
        }
        for bytes in [&[][..], &[2], &[0, 1]] {
            assert!(model.decode_input(bytes).is_err());
        }
    }
}

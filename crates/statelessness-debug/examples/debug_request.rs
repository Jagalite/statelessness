//! Linked request-lifecycle harness. Outputs are displayed data, never effects.
use stateless::demo::{Input, RequestModel};
use stateless::execution::{
    ReplayObservation, ReplayOptions, ReplayOutcome, replay_with_observations_bounded,
};
use stateless::monitor::RecorderOptions;
use stateless::observation::DebugObservation;
use stateless::trace::{ReadLimits, RunConfig};
use statelessness_debug::workbench::TraceViewer;
use statelessness_debug::{DebugSession, InputPolicy, SessionLimits};
use std::error::Error;
use std::fs::File;
use std::io::{self, BufRead, Write};

fn session(fixed: bool) -> Result<DebugSession<RequestModel>, Box<dyn Error>> {
    let model = if fixed {
        RequestModel::fixed()
    } else {
        RequestModel::buggy()
    };
    Ok(DebugSession::recording(
        "request-lifecycle",
        model,
        InputPolicy::enumerated(),
        SessionLimits::default(),
        RunConfig::default(),
        RecorderOptions::default(),
    )?)
}
fn show(session: &DebugSession<RequestModel>) {
    println!(
        "sequence={} revision={} phase={:?} state={:?}",
        session.sequence(),
        session.revision(),
        session.phase(),
        session.state()
    );
    match session.observation() {
        DebugObservation::Initial { checks, .. } => println!("initial_checks={checks:?}"),
        DebugObservation::Turn(turn) => println!(
            "input={:?} disposition={:?} outputs={:?} checks={:?}",
            turn.input, turn.transition.disposition, turn.transition.outputs, turn.checks
        ),
    }
    println!(
        "stop={:?} diagnostics={:?}",
        session.stop_reason(),
        session.diagnostic_errors()
    );
}
fn save(session: &DebugSession<RequestModel>, path: &str) -> Result<(), Box<dyn Error>> {
    // This local harness explicitly accepts a user-supplied output path. A remote
    // protocol does not inherit arbitrary filesystem write authority.
    let file = File::options().write(true).create_new(true).open(path)?;
    session.export_trace()?.write_to(file)?;
    println!("saved exact evidence to {path}; raw evidence may contain sensitive values");
    Ok(())
}
fn load(path: &str) -> Result<TraceViewer, Box<dyn Error>> {
    Ok(TraceViewer::read(
        File::open(path)?,
        &ReadLimits::default(),
    )?)
}
fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let command = args.first().map(String::as_str).unwrap_or("interactive");
    match command {
        "record" => {
            let path = args.get(1).ok_or("record requires a new output path")?;
            let mut s = session(false)?;
            for input in [Input::Start, Input::Cancel, Input::Complete(1)] {
                s.step(s.revision(), input)?;
                show(&s);
            }
            save(&s, path)?;
        }
        "verify" | "compare" => {
            let view = load(args.get(1).ok_or("verify/compare requires trace path")?)?;
            let model = if command == "compare" {
                RequestModel::fixed()
            } else {
                RequestModel::buggy()
            };
            let report = replay_with_observations_bounded(&model, view.trace(), ReplayOptions { allow_build_mismatch: command == "compare" }, &ReadLimits::default(), |observation, report| {
                match observation {
                    ReplayObservation::Initial(initial) => println!("initial checks={:?} differences={:?}", initial.checks, initial.differences),
                    ReplayObservation::Turn(turn) => println!("sequence={} replay-produced outputs={:?} all_differences={:?} verified_prefix={}", turn.actual.sequence, turn.actual.transition.outputs, turn.differences, report.steps_verified),
                    ReplayObservation::Error { error, .. } => println!("incomplete verification: {error}"),
                }
                Ok(())
            }).map_err(|e| e.error)?;
            println!("{report:?}");
            if command == "verify" && report.outcome != ReplayOutcome::Exact {
                return Err("trace did not verify".into());
            }
            if command == "compare" {
                println!("Comparison divergence is not a universal correctness proof");
            }
        }
        "view" => {
            let mut view = load(args.get(1).ok_or("view requires trace path")?)?;
            let position = args
                .get(2)
                .map(|s| s.parse::<usize>())
                .transpose()?
                .unwrap_or(0);
            view.seek(position)?;
            println!(
                "unverified stored view: model={:?} range={:?} position={} state={:?} outputs={:?} termination={:?}",
                view.trace().metadata,
                view.retained_range(),
                view.position(),
                view.state_bytes(4096),
                view.outputs(0, 100, 4096)?,
                view.trace().termination
            );
        }
        "fork" => {
            let view = load(args.get(1).ok_or("fork requires parent trace")?)?;
            let sequence = args
                .get(2)
                .ok_or("fork requires sequence")?
                .parse::<usize>()?;
            let path = args.get(3).ok_or("fork requires a new output path")?;
            let branch = view.fork_verified(
                "request-branch",
                RequestModel::buggy(),
                sequence,
                ReplayOptions::default(),
                InputPolicy::enumerated(),
                SessionLimits::default(),
                RecorderOptions::default(),
            )?;
            println!("branch provenance={:?}", branch.provenance);
            save(&branch.session, path)?;
        }
        "interactive" => {
            let mut s = session(args.iter().any(|a| a == "--fixed"))?;
            println!(
                "Commands: state | inputs | step Start | step Cancel | step Complete N | export NEW_PATH | quit"
            );
            show(&s);
            for line in io::stdin().lock().lines() {
                let line = line?;
                let parts: Vec<_> = line.split_whitespace().collect();
                let result: Result<(), Box<dyn Error>> = (|| {
                    match parts.as_slice() {
                        ["state"] => show(&s),
                        ["inputs"] => {
                            let page = s.inputs(s.revision(), 0, 100)?;
                            for candidate in page.candidates {
                                println!("{:?}", candidate.input());
                            }
                            println!("complete={}", page.complete);
                        }
                        ["step", "Start"] => {
                            s.step(s.revision(), Input::Start)?;
                            show(&s);
                        }
                        ["step", "Cancel"] => {
                            s.step(s.revision(), Input::Cancel)?;
                            show(&s);
                        }
                        ["step", "Complete", generation] => {
                            s.step(s.revision(), Input::Complete(generation.parse()?))?;
                            show(&s);
                        }
                        ["export", path] => save(&s, path)?,
                        ["quit"] => return Ok(()),
                        _ => return Err("unsupported command".into()),
                    };
                    Ok(())
                })();
                if parts.as_slice() == ["quit"] {
                    break;
                }
                if let Err(error) = result {
                    eprintln!("error: {error}");
                }
                io::stdout().flush()?;
            }
        }
        _ => return Err("expected interactive, record, verify, compare, view or fork".into()),
    }
    Ok(())
}

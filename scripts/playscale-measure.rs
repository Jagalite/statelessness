// Compiled only in an isolated copy of the explicitly supplied Playscale core.
// No application source is vendored into the Stateless distribution.
include!("../tests/jobs_model.rs");

fn main() -> Result<(), Box<dyn std::error::Error>> {
    use stateless::execution::{CheckPolicy, ReplayOptions, ReplayOutcome, check_observed_into, replay};
    use stateless::monitor::Recorder;
    use stateless::trace::{ReadLimits, RunConfig, Trace};
    use std::{fs::File, hint::black_box, time::Instant};
    let args: Vec<_> = std::env::args().collect();
    if args.get(1).map(String::as_str) == Some("replay") {
        let trace = Trace::read_from(File::open(&args[2])?, &ReadLimits::default())?;
        let report = replay(&Jobs, &trace, ReplayOptions::default())?;
        assert_eq!(report.outcome, ReplayOutcome::Exact);
        assert!(!report.failure_reproduced);
        println!("fresh-process replay: {} transitions exact", report.steps_verified);
        return Ok(());
    }
    let steps: usize = args[1].parse()?;
    let samples: usize = args[2].parse()?;
    println!("mode,sample,steps,elapsed_ns,retained_steps,evicted_steps,encoded_bytes,export_ns,replay_ns");
    for mode in ["reducer", "checked", "recorded"] {
        for sample in 0..samples {
            let model = Jobs;
            let mut state = model.initial_state()?;
            let mut checks = Vec::new();
            let mut recorder = if mode == "recorded" {
                Some(Recorder::new(&model, &state, RunConfig::default(), 1024)?)
            } else { None };
            let start = Instant::now();
            for index in 0..steps {
                let input = match index % 6 {
                    0 => Input::Start,
                    1 => Input::Cancel,
                    2 => Input::Finished { attempt: state.attempt.saturating_sub(1), success: true },
                    3 => Input::Finished { attempt: state.attempt, success: true },
                    4 => Input::Retry,
                    _ => Input::Finished { attempt: state.attempt, success: true },
                };
                let transition = model.step(black_box(&state), black_box(&input))?;
                if let Some(recorder) = &mut recorder {
                    recorder.observe(&model, &state, &input, &transition)?;
                    assert!(!recorder.is_frozen());
                } else if mode == "checked" {
                    check_observed_into(&model, &state, &input, &transition,
                        index as u64 + 1, CheckPolicy::default(), &mut checks)?;
                    assert!(checks.iter().all(|check| matches!(check.status, stateless::CheckStatus::Passed)));
                }
                state = transition.state;
                black_box(&state);
            }
            let elapsed = start.elapsed().as_nanos();
            if let Some(recorder) = recorder {
                let path = format!("jobs-{sample}.sttrace");
                let start = Instant::now();
                let mut file = File::create_new(path)?;
                recorder.write_to(&mut file)?;
                file.sync_all()?;
                let export_ns = start.elapsed().as_nanos();
                let retained = recorder.retained_steps();
                let evicted = recorder.evicted_steps();
                let encoded = recorder.retained_bytes();
                let trace = recorder.into_trace();
                let start = Instant::now();
                let report = replay(&model, &trace, ReplayOptions::default())?;
                assert_eq!(report.outcome, ReplayOutcome::Exact);
                assert_eq!(report.steps_verified, retained);
                println!("{mode},{sample},{steps},{elapsed},{retained},{evicted},{encoded},{export_ns},{}", start.elapsed().as_nanos());
            } else {
                println!("{mode},{sample},{steps},{elapsed},0,0,0,0,0");
            }
        }
    }
    Ok(())
}

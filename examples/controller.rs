//! See docs/INTEGRATION.md. This executable is both a real background worker
//! application and the fresh-process checker/replay entry point.
use stateless::assurance::{self, AssurancePolicy};
use stateless::execution::{ReplayOptions, ReplayOutcome, replay};
use stateless::explore::{SearchConfig, SearchLimits};
use stateless::reference::{Controller, Input, Mutation, MutationStatus, Outcome};
use stateless::reference::{coverage, evidence, host::Host};
use stateless::trace::{ReadLimits, Trace};
use std::{
    error::Error,
    fs::{File, OpenOptions},
    io::Write,
    path::Path,
    process::ExitCode,
    time::Duration,
};

fn run(args: &[String]) -> Result<u8, Box<dyn Error>> {
    match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["check", report_path] => {
            let model = Controller::default();
            let run = assurance::enumerate(&model, coverage::manifest(&model)?, coverage::ReferenceCoverage, SearchConfig::default(), SearchLimits::default())?;
            let adequate = run.assurance.assess(AssurancePolicy::Strict);
            let mut report = OpenOptions::new().write(true).create_new(true).open(report_path)?;
            report.write_all(run.assurance.to_json().as_bytes())?; report.sync_all()?;
            println!("{}", adequate.confidence);
            run.search?;
            Ok(if adequate.adequate { 0 } else { 1 })
        }
        ["mutations", directory] => {
            let results = evidence::write_mutation_suite(Path::new(directory), SearchConfig::default())?;
            for result in &results { println!("{}: {:?} [{}]", result.mutation.name(), result.status, result.mutation.target()); }
            Ok(if results.iter().all(|r| r.status == MutationStatus::Killed) { 0 } else { 1 })
        }
        ["regressions"] => {
            evidence::check_regressions(include_str!("../docs/requirements/controller-witnesses.txt"))?;
            println!("All retained cases reproduced their named mutant target and passed on the production reducer.");
            Ok(0)
        }
        ["replay", name, path] => {
            let mutation = if *name == "fixed" { None } else { Some(Mutation::named(name).ok_or("unknown mutation")?) };
            let model = Controller { mutation, ..Controller::default() };
            let trace = Trace::read_from(File::open(path)?, &ReadLimits::default())?;
            let report = replay(&model, &trace, ReplayOptions::default())?;
            let target_matches = mutation.is_none_or(|m| trace.steps.iter().flat_map(|s| &s.checks).any(|c| c.id == m.target() && c.is_failure()) && report.failure_reproduced);
            println!("{:?}; failure reproduced={}; target_matches={target_matches}", report.outcome, report.failure_reproduced);
            Ok(if report.outcome == ReplayOutcome::Exact && target_matches { 0 } else { 1 })
        }
        ["live", path] => {
            // The worker performs actual work on a background OS thread. Replace
            // this callback with file/command/service work without changing the
            // reducer or the verifier's adapter.
            let mut host = Host::new(Default::default(), |generation, attempt| {
                let payload = (0..4096u64).fold(generation, |sum, item| sum.wrapping_add(item));
                std::hint::black_box(payload);
                if attempt == 1 { Outcome::RetryableFailure } else { Outcome::Success }
            }, true)?;
            host.deliver(Input::Submit)?;
            host.deliver(Input::Submit)?;
            while host.state().pending() > 0 {
                if let Some(deadline) = host.state().jobs.iter().filter_map(|j| match j.phase { stateless::reference::Phase::Waiting { deadline } => Some(deadline), _ => None }).min() {
                    host.tick(deadline)?;
                } else if !host.receive(Duration::from_secs(5))? { return Err("host timeout: pending operations remain unqualified".into()); }
            }
            evidence::save_trace(Path::new(path), &host.recording().ok_or("recorder unavailable")?)?;
            println!("published={:?}; settled={:?}; effects={}; diagnostics={:?}", host.published, host.settled, host.executed_effects, host.diagnostics);
            Ok(if host.diagnostics.is_empty() { 0 } else { 1 })
        }
        _ => Err("usage: controller check NEW_REPORT | mutations NEW_DIRECTORY | regressions | replay fixed|MUTATION TRACE | live NEW_TRACE".into()),
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

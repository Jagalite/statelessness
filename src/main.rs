use stateless::{
    Generate, Model, ModelCodec, ModelError,
    automatic::{Auto, AutoOptions},
    campaign::{self, CampaignConfig, CampaignTermination, JobOutcome, JobReport},
    demo::{Input, RequestModel},
    execution::{self, ReplayOptions, ReplayOutcome},
    explore,
    guided::{self, FeedbackMetadata, GuidanceConfig},
    trace::{ReadLimits, RunConfig, Termination, Trace},
};
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    process::ExitCode,
    time::Duration,
};

const HELP: &str = "Stateless — dependency-free state-machine exploration\n\n\
  stateless demo TRACE [--fixed]\n\
  stateless fuzz TRACE [--guided] [--auto] [--fixed] [--seed N] [--cases N] [--steps N] [--max-candidates N]\n\
  stateless campaign NEW_DIRECTORY [--guided] [--auto] [--fixed] [--seed N] [--jobs N] [--workers N]\n\
      [--cases N] [--steps N] [--max-candidates N] [--max-ms N] [--stop-on-failure]\n\
      [--corpus N] [--corpus-inputs N] [--features N] [--features-per-step N]\n\
  stateless enumerate TRACE [--fixed] [--depth N] [--states N] [--edges N]\n\
  stateless replay TRACE [--fixed] [--allow-build-mismatch] [--delay-ms N] [--step]\n\
  stateless inspect TRACE\n\n\
Fuzz and enumeration write TRACE.report.txt, including successful/bounded runs.\n\
Campaigns write configuration, per-job reports, and a summary into a new directory.\n\
--auto samples the complete enumerated input domain; --max-candidates requires it.\n\
--guided retains novel feature paths; corpus/feature limits apply to fuzz and campaign.\n\
--max-ms is a cooperative search limit, not a deadline for artifact I/O or shrinking.\n\
The CLI executes the bundled request-lifecycle fixture. Application models use\n\
the Rust API or bindings. Existing artifacts are never overwritten.\n\
Exit codes: 0 success/exact replay, 1 property failure, 2 usage/engine/I/O error,\n\
3 replay divergence, 4 incompatible build/model, 5 exploration budget/deadline/cancellation reached.\n\
Exact replay can successfully reproduce a known failure; consult its report.\n";

fn main() -> ExitCode {
    match run() {
        Ok(code) => ExitCode::from(code),
        Err(error) => {
            eprintln!("error: {error}");
            ExitCode::from(2)
        }
    }
}

type Error = Box<dyn std::error::Error>;
fn run() -> Result<u8, Error> {
    let mut args: Vec<String> = env::args().skip(1).collect();
    if args.is_empty() || matches!(args[0].as_str(), "help" | "--help" | "-h") {
        print!("{HELP}");
        return Ok(0);
    }
    let command = args.remove(0);
    if args.is_empty() {
        return Err("missing trace path; see --help".into());
    }
    let path = PathBuf::from(args.remove(0));
    if command == "inspect" {
        reject_extra(&args)?;
        let trace = read(&path)?;
        inspect(&trace);
        return Ok(0);
    }
    let fixed = flag(&mut args, "--fixed");
    let model = if fixed {
        RequestModel::fixed()
    } else {
        RequestModel::buggy()
    };
    match command.as_str() {
        "demo" => {
            reject_extra(&args)?;
            let trace = execution::record(
                &model,
                [Input::Start, Input::Cancel, Input::Complete(1)],
                RunConfig::default(),
                3,
            )?;
            save(&path, &trace)?;
            summarize(&trace);
            Ok(
                if matches!(trace.termination, Termination::PropertyFailed) {
                    1
                } else {
                    0
                },
            )
        }
        "replay" => {
            let allow_build_mismatch = flag(&mut args, "--allow-build-mismatch");
            let step = flag(&mut args, "--step");
            let delay = number(&mut args, "--delay-ms", 0u64)?;
            reject_extra(&args)?;
            let trace = read(&path)?;
            let report = execution::replay_with_observer(
                &model,
                &trace,
                ReplayOptions {
                    allow_build_mismatch,
                },
                |sequence, report| {
                    if delay > 0 {
                        std::thread::sleep(Duration::from_millis(delay));
                    }
                    if step {
                        eprintln!(
                            "step {sequence}: {:?}; press Enter to continue",
                            report.outcome
                        );
                        let mut line = String::new();
                        let _ = io::stdin().read_line(&mut line);
                    }
                },
            )?;
            println!(
                "replay: {:?}\nverified transitions: {}\nfailure reproduced: {}\nbuild matches: {}\nrecorded termination: {:?}",
                report.outcome,
                report.steps_verified,
                report.failure_reproduced,
                report.build_matches,
                trace.termination
            );
            Ok(match report.outcome {
                ReplayOutcome::Exact => 0,
                ReplayOutcome::Diverged { .. } => 3,
                ReplayOutcome::Incompatible { .. } => 4,
            })
        }
        "fuzz" => {
            let guidance = guidance_options(&mut args)?;
            let automatic = flag(&mut args, "--auto");
            let candidates_supplied = args.iter().any(|arg| arg == "--max-candidates");
            let max_candidates = number(
                &mut args,
                "--max-candidates",
                AutoOptions::default().max_candidates,
            )?;
            let seed = number(&mut args, "--seed", 42u64)?;
            let cases = number(&mut args, "--cases", 100usize)?;
            let steps = number(&mut args, "--steps", 20usize)?;
            reject_extra(&args)?;
            validate_generator_options(automatic, candidates_supplied, max_candidates)?;
            let fuzz = fuzz_config(seed, cases, steps)?;
            check_report_destination(&path)?;
            if automatic {
                run_fuzz(
                    &Auto::with_options(model, AutoOptions { max_candidates })?,
                    &path,
                    fuzz,
                    Some(max_candidates),
                    guidance,
                )
            } else {
                run_fuzz(&model, &path, fuzz, None, guidance)
            }
        }
        "campaign" => {
            let guidance = guidance_options(&mut args)?;
            let automatic = flag(&mut args, "--auto");
            let stop_on_failure = flag(&mut args, "--stop-on-failure");
            let candidates_supplied = args.iter().any(|arg| arg == "--max-candidates");
            let max_candidates = number(
                &mut args,
                "--max-candidates",
                AutoOptions::default().max_candidates,
            )?;
            let seed = number(&mut args, "--seed", 42u64)?;
            // Workload identities stay fixed when only CPU parallelism changes.
            let jobs = number(&mut args, "--jobs", CampaignConfig::default().jobs)?;
            let default_workers = std::thread::available_parallelism()
                .map_or(1, |count| count.get())
                .min(256)
                .min(jobs.max(1));
            let workers = number(&mut args, "--workers", default_workers)?;
            let cases = number(&mut args, "--cases", 100usize)?;
            let steps = number(&mut args, "--steps", 20usize)?;
            let duration =
                optional_number::<u64>(&mut args, "--max-ms")?.map(Duration::from_millis);
            reject_extra(&args)?;
            validate_generator_options(automatic, candidates_supplied, max_candidates)?;
            if jobs == 0 || workers == 0 {
                return Err("campaign jobs and workers must be positive".into());
            }
            let fuzz = fuzz_config(seed, cases, steps)?;
            let result_buffer = workers
                .checked_mul(2)
                .ok_or("campaign result buffer overflow")?;
            u64::try_from(jobs).map_err(|_| "campaign job count exceeds identity domain")?;
            let config = CampaignConfig {
                master_seed: seed,
                first_job: 0,
                jobs,
                workers,
                result_buffer,
                fuzz,
                stop_on_failure,
            };
            if automatic {
                run_cli_campaign(
                    &path,
                    config,
                    duration,
                    Some(max_candidates),
                    guidance,
                    model.metadata(),
                    move |_| Auto::with_options(model, AutoOptions { max_candidates }),
                )
            } else {
                run_cli_campaign(
                    &path,
                    config,
                    duration,
                    None,
                    guidance,
                    model.metadata(),
                    move |_| Ok(model),
                )
            }
        }
        "enumerate" => {
            let depth = number(&mut args, "--depth", 20usize)?;
            let states = number(&mut args, "--states", 10_000usize)?;
            let edges = number(&mut args, "--edges", 100_000u64)?;
            reject_extra(&args)?;
            check_report_destination(&path)?;
            let report = explore::enumerate(
                &model,
                explore::SearchConfig {
                    max_depth: depth,
                    max_states: states,
                    max_transitions: edges,
                },
            )?;
            let config = RunConfig {
                strategy: "breadth-first".into(),
                seed: None,
                parameters: vec![
                    ("max_depth".into(), depth.to_string()),
                    ("max_states".into(), states.to_string()),
                    ("max_transitions".into(), edges.to_string()),
                    (
                        "search_termination".into(),
                        format!("{:?}", report.termination),
                    ),
                    (
                        "executed_transitions".into(),
                        report.transitions.to_string(),
                    ),
                    ("retained_states".into(), report.states.to_string()),
                ],
            };
            println!(
                "enumeration: {:?}; states: {}; transitions: {}; skipped checks: {}",
                report.termination, report.states, report.transitions, report.skipped_checks
            );
            if let Some(failure) = &report.failure {
                save_failure(&model, &path, failure, config.clone())?;
                save_report(&path, &model, &config, &format!("{report:#?}"))?;
                Ok(1)
            } else {
                save_report(&path, &model, &config, &format!("{report:#?}"))?;
                println!("No failure artifact. Coverage is limited to the bundled finite model.");
                Ok(
                    if matches!(
                        report.termination,
                        explore::SearchTermination::GraphExhausted
                    ) {
                        0
                    } else {
                        5
                    },
                )
            }
        }
        _ => Err(format!("unknown command {command:?}; see --help").into()),
    }
}

fn validate_generator_options(
    automatic: bool,
    supplied: bool,
    maximum: usize,
) -> Result<(), Error> {
    if supplied && !automatic {
        return Err("--max-candidates requires --auto".into());
    }
    if maximum == 0 {
        return Err("--max-candidates must be positive".into());
    }
    Ok(())
}

fn fuzz_config(seed: u64, cases: usize, steps: usize) -> Result<explore::FuzzConfig, Error> {
    let cases_u64 = u64::try_from(cases).map_err(|_| "case budget overflow")?;
    let steps_u64 = u64::try_from(steps).map_err(|_| "step budget overflow")?;
    let max_transitions = cases_u64
        .checked_mul(steps_u64)
        .ok_or("transition budget overflow")?;
    Ok(explore::FuzzConfig {
        seed,
        cases,
        max_steps: steps,
        max_transitions,
        mutation_percent: 25,
    })
}

fn guidance_options(args: &mut Vec<String>) -> Result<Option<GuidanceConfig>, Error> {
    let guided = flag(args, "--guided");
    let supplied = args.iter().any(|a| {
        matches!(
            a.as_str(),
            "--corpus" | "--corpus-inputs" | "--features" | "--features-per-step"
        )
    });
    let defaults = GuidanceConfig::default();
    let config = GuidanceConfig {
        max_corpus_entries: number(args, "--corpus", defaults.max_corpus_entries)?,
        max_corpus_inputs: number(args, "--corpus-inputs", defaults.max_corpus_inputs)?,
        max_features: number(args, "--features", defaults.max_features)?,
        max_features_per_observation: number(
            args,
            "--features-per-step",
            defaults.max_features_per_observation,
        )?,
    };
    if supplied && !guided {
        return Err("corpus/feature options require --guided".into());
    }
    if guided {
        config.validate()?;
        Ok(Some(config))
    } else {
        Ok(None)
    }
}
fn request_feedback_metadata() -> FeedbackMetadata {
    FeedbackMetadata {
        name: "request-lifecycle-projection".into(),
        version: 1,
        build: "generation-active-ready-pending-bitset-v1".into(),
    }
}
fn request_feedback<M: Model<State = stateless::demo::State>>() -> impl guided::Feedback<M> {
    guided::StateFeedback::new(
        request_feedback_metadata(),
        |state: &stateless::demo::State| {
            (
                state.generation,
                state.active,
                state.ready,
                state
                    .pending
                    .iter()
                    .fold(0u8, |bits, generation| bits | (1 << generation)),
            )
        },
    )
}
fn add_guidance_config(
    config: &mut RunConfig,
    guidance: GuidanceConfig,
    feedback: &FeedbackMetadata,
) {
    config.parameters.extend([
        ("guidance.algorithm".into(), guided::ALGORITHM.into()),
        ("guidance.feedback".into(), feedback.name.clone()),
        ("guidance.version".into(), feedback.version.to_string()),
        ("guidance.build".into(), feedback.build.clone()),
        (
            "guidance.max_corpus_entries".into(),
            guidance.max_corpus_entries.to_string(),
        ),
        (
            "guidance.max_corpus_inputs".into(),
            guidance.max_corpus_inputs.to_string(),
        ),
        (
            "guidance.max_features".into(),
            guidance.max_features.to_string(),
        ),
        (
            "guidance.max_features_per_observation".into(),
            guidance.max_features_per_observation.to_string(),
        ),
    ]);
}

fn fuzz_run_config(
    fuzz: &explore::FuzzConfig,
    automatic: Option<usize>,
    strategy: &str,
    guidance: Option<GuidanceConfig>,
) -> RunConfig {
    let mut config = RunConfig {
        strategy: strategy.into(),
        seed: Some(fuzz.seed),
        parameters: vec![
            ("cases".into(), fuzz.cases.to_string()),
            ("steps".into(), fuzz.max_steps.to_string()),
            ("max_transitions".into(), fuzz.max_transitions.to_string()),
            ("mutation_percent".into(), fuzz.mutation_percent.to_string()),
            ("rng".into(), "splitmix64-v1".into()),
            (
                "generator".into(),
                if automatic.is_some() {
                    "enumerated-reservoir-v1"
                } else {
                    "model-generate"
                }
                .into(),
            ),
        ],
    };
    if let Some(maximum) = automatic {
        config
            .parameters
            .push(("max_candidates".into(), maximum.to_string()));
    }
    if let Some(guidance) = guidance {
        add_guidance_config(&mut config, guidance, &request_feedback_metadata());
    }
    config
}

fn add_fuzz_result(config: &mut RunConfig, report: &explore::FuzzReport<Input>) {
    if let Some(guidance) = &report.guidance {
        config.parameters.extend([
            (
                "guidance.unique_features".into(),
                guidance.unique_features.to_string(),
            ),
            (
                "guidance.corpus_entries".into(),
                guidance.corpus.len().to_string(),
            ),
            (
                "guidance.corpus_inputs".into(),
                guidance.corpus_inputs.to_string(),
            ),
            (
                "guidance.mutation_cases".into(),
                guidance.mutation_cases.to_string(),
            ),
            (
                "guidance.dropped_prefixes".into(),
                guidance.dropped_prefixes.to_string(),
            ),
            (
                "guidance.evicted_prefixes".into(),
                guidance.evicted_prefixes.to_string(),
            ),
            (
                "guidance.feature_limit_reached".into(),
                guidance.feature_limit_reached.to_string(),
            ),
            (
                "guidance.corpus_limit_reached".into(),
                guidance.corpus_limit_reached.to_string(),
            ),
        ]);
    }
    config.parameters.extend([
        (
            "search_termination".into(),
            format!("{:?}", report.termination),
        ),
        (
            "executed_transitions".into(),
            report.transitions.to_string(),
        ),
        ("started_cases".into(), report.cases.to_string()),
        ("skipped_checks".into(), report.skipped_checks.to_string()),
    ]);
}

fn run_fuzz<M: Generate + ModelCodec<Input = Input, State = stateless::demo::State>>(
    model: &M,
    path: &Path,
    fuzz: explore::FuzzConfig,
    automatic: Option<usize>,
    guidance: Option<GuidanceConfig>,
) -> Result<u8, Error> {
    let mut config = fuzz_run_config(
        &fuzz,
        automatic,
        if guidance.is_some() {
            "guided-fuzz"
        } else {
            "fuzz"
        },
        guidance,
    );
    let searched = if let Some(guidance) = guidance {
        guided::fuzz(model, fuzz, guidance, request_feedback::<M>())
    } else {
        explore::fuzz(model, fuzz)
    };
    let report = match searched {
        Ok(report) => report,
        Err(error) => {
            save_report(
                path,
                model,
                &config,
                &format!("Engine error: {error}\nNo successful search result is claimed."),
            )?;
            return Err(error.into());
        }
    };
    add_fuzz_result(&mut config, &report);
    println!(
        "fuzz: {:?}; cases: {}; transitions: {}; skipped checks: {}",
        report.termination, report.cases, report.transitions, report.skipped_checks
    );
    if let Some(failure) = &report.failure {
        save_failure(model, path, failure, config.clone())?;
        save_report(path, model, &config, &format!("{report:#?}"))?;
        Ok(1)
    } else {
        save_report(path, model, &config, &format!("{report:#?}"))?;
        println!(
            "No failure artifact. This is a bounded search result, not a proof of correctness."
        );
        Ok(
            if matches!(report.termination, explore::FuzzTermination::CasesCompleted) {
                0
            } else {
                5
            },
        )
    }
}

const CAMPAIGN_AUDIT_SCOPE: &str = "Search deadline: cooperative; a model callback cannot be interrupted. Artifact I/O and up to 1000 shrink attempts per failure are not time-bounded.\nAccounting: errored/panicked jobs can have unreported partial work; consult accounting_complete.\nDomain: bundled finite request-lifecycle fixture, not a production qualification.\n";

fn run_cli_campaign<M, F>(
    directory: &Path,
    config: CampaignConfig,
    duration: Option<Duration>,
    automatic: Option<usize>,
    guidance: Option<GuidanceConfig>,
    metadata: stateless::ModelMetadata,
    factory: F,
) -> Result<u8, Error>
where
    M: Generate + ModelCodec<Input = Input, State = stateless::demo::State>,
    F: Fn(u64) -> Result<M, ModelError> + Sync,
{
    config.validate()?;
    // create_dir, rather than create_dir_all, requires one fresh destination.
    fs::create_dir(directory)?;
    let description = format!(
        "Stateless campaign configuration version 1\nEngine: {} {}\nModel: {metadata:#?}\nCampaign: {config:#?}\nAutomatic max_candidates: {automatic:?}\nGuidance: {guidance:?}\nSearch max_duration: {duration:?}\nSeed derivation: stateless::campaign::job_seed(master_seed, job_id)\n{CAMPAIGN_AUDIT_SCOPE}",
        env!("CARGO_PKG_VERSION"),
        env!("STATELESS_BUILD_ID"),
    );
    write_new_text(&directory.join("campaign.config.txt"), &description)?;
    let mut bounded_job = false;
    let sink = |job: JobReport<Input>| {
        let trace_path = directory.join(format!("job-{:06}.sttrace", job.job_id));
        let mut run_config = fuzz_run_config(
            &job.config,
            automatic,
            if guidance.is_some() {
                "campaign-guided-fuzz"
            } else {
                "campaign-fuzz"
            },
            guidance,
        );
        run_config.seed = Some(job.seed);
        run_config.parameters.extend([
            (
                "campaign.master_seed".into(),
                config.master_seed.to_string(),
            ),
            ("campaign.job_id".into(), job.job_id.to_string()),
            ("campaign.first_job".into(), config.first_job.to_string()),
            (
                "campaign.seed_derivation".into(),
                "stateless::campaign::job_seed (pinned engine build)".into(),
            ),
            (
                "campaign.artifact_processing".into(),
                "I/O and 1000 shrink attempts are outside the search time bound".into(),
            ),
        ]);
        if let JobOutcome::Completed(report) = &job.outcome {
            add_fuzz_result(&mut run_config, report);
            bounded_job |= matches!(
                report.termination,
                explore::FuzzTermination::TransitionLimit
                    | explore::FuzzTermination::Deadline
                    | explore::FuzzTermination::Cancelled
            );
        }
        let text = format!(
            "Stateless campaign job report version 1\nEngine: {} {}\nJob: {}\nSeed: {}\nModel: {:#?}\nConfiguration: {run_config:#?}\nOutcome: {:#?}\nThis records the search outcome. Failure traces are separate files and may be absent if artifact processing failed; consult the campaign summary.\n",
            env!("CARGO_PKG_VERSION"),
            env!("STATELESS_BUILD_ID"),
            job.job_id,
            job.seed,
            job.metadata,
            job.outcome,
        );
        write_new_text(&report_path(&trace_path), &text)
            .map_err(|error| ModelError::new(format!("job {} audit: {error}", job.job_id)))?;
        if let JobOutcome::Completed(report) = &job.outcome
            && let Some(failure) = &report.failure
        {
            let model = factory(job.job_id)?;
            if job
                .metadata
                .as_ref()
                .is_some_and(|expected| *expected != model.metadata())
            {
                return Err(ModelError::new(format!(
                    "job {} model identity changed before artifact recording",
                    job.job_id
                )));
            }
            save_failure(&model, &trace_path, failure, run_config).map_err(|error| {
                ModelError::new(format!("job {} failure artifacts: {error}", job.job_id))
            })?;
        }
        Ok(())
    };
    let limits = explore::RunLimits {
        max_duration: duration,
        cancellation: None,
    };
    let run = if let Some(guidance) = guidance {
        campaign::run_guided_campaign(
            config.clone(),
            limits,
            guidance,
            &factory,
            |_, _| Ok(request_feedback::<M>()),
            sink,
        )
    } else {
        campaign::run_campaign(config.clone(), limits, &factory, sink)
    };
    let report = match run {
        Ok(report) => report,
        Err(error) => {
            write_new_text(
                &directory.join("campaign.report.txt"),
                &format!(
                    "{description}\nCampaign engine error: {error}\nNo complete accounting is available.\n"
                ),
            )?;
            return Err(error.into());
        }
    };
    write_new_text(
        &directory.join("campaign.report.txt"),
        &format!(
            "{description}\nReport: {report:#?}\nPer-job reports are identified by job ID; arrival order depends on worker scheduling.\n"
        ),
    )?;
    println!(
        "campaign: {:?}; jobs: {}/{}; failures: {}; errors: {}; reported transitions: {}; accounting complete: {}",
        report.termination,
        report.finished,
        report.requested,
        report.failures,
        report.errors,
        report.reported_transitions,
        report.accounting_complete
    );
    let infrastructure_error = matches!(
        report.termination,
        CampaignTermination::CallbackError(_) | CampaignTermination::WorkerError(_)
    );
    Ok(if report.errors != 0 || infrastructure_error {
        2
    } else if report.failures != 0 {
        1
    } else if !bounded_job && matches!(report.termination, CampaignTermination::JobsCompleted) {
        0
    } else {
        5
    })
}

fn write_new_text(path: &Path, text: &str) -> Result<(), Error> {
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(text.as_bytes())?;
    file.sync_all()?;
    println!("saved {}", path.display());
    Ok(())
}

fn save_failure<M: Generate + ModelCodec<Input = Input>>(
    model: &M,
    path: &Path,
    failure: &explore::Failure<Input>,
    mut config: RunConfig,
) -> Result<(), Error> {
    let shrunk = explore::shrink(
        model,
        failure,
        explore::ShrinkConfig {
            max_attempts: 1_000,
        },
    )?;
    if let Some(target) = failure.violations.first() {
        config
            .parameters
            .push(("target_property".into(), target.check.id.to_string()));
        config
            .parameters
            .push(("target_phase".into(), format!("{:?}", target.phase)));
    }
    let original_path = path.with_extension("original.sttrace");
    if original_path == path || path.exists() || original_path.exists() {
        return Err(
            "artifact destination already exists or conflicts with original trace path".into(),
        );
    }
    let original = execution::record(
        model,
        shrunk.original.inputs.clone(),
        config.clone(),
        usize::MAX,
    )?;
    config.parameters.push((
        "original_artifact".into(),
        original_path.to_string_lossy().into_owned(),
    ));
    config.parameters.push((
        "original_steps".into(),
        shrunk.original.inputs.len().to_string(),
    ));
    config
        .parameters
        .push(("shrink_attempts".into(), shrunk.attempts.to_string()));
    config.parameters.push((
        "shrink_termination".into(),
        format!("{:?}", shrunk.termination),
    ));
    config
        .parameters
        .push(("shrink_history".into(), format!("{:?}", shrunk.history)));
    let minimized = execution::record(model, shrunk.minimized.inputs.clone(), config, usize::MAX)?;
    if !matches!(original.termination, Termination::PropertyFailed)
        || !matches!(minimized.termination, Termination::PropertyFailed)
    {
        return Err("failure did not reproduce while recording".into());
    }
    save(&original_path, &original)?;
    save(path, &minimized)?;
    println!(
        "shrinking: {} -> {} transitions, {} attempts",
        original.steps.len(),
        minimized.steps.len(),
        shrunk.attempts
    );
    summarize(&minimized);
    Ok(())
}

fn read(path: &Path) -> Result<Trace, Error> {
    Ok(Trace::read_from(File::open(path)?, &ReadLimits::default())?)
}
fn report_path(path: &Path) -> PathBuf {
    let mut name = path.as_os_str().to_os_string();
    name.push(".report.txt");
    PathBuf::from(name)
}
fn check_report_destination(path: &Path) -> Result<(), Error> {
    if report_path(path).exists()
        || path.exists()
        || path.with_extension("original.sttrace").exists()
    {
        Err("artifact destination already exists".into())
    } else {
        Ok(())
    }
}
fn save_report<M: Model>(
    path: &Path,
    model: &M,
    config: &RunConfig,
    report: &str,
) -> Result<(), Error> {
    let path = report_path(path);
    let text = format!(
        "Stateless search report version 1\nEngine: {} {}\nModel: {:#?}\nConfiguration: {config:#?}\nDomain: at most two request generations; all pending completions; cancellation and replacement\nChecking: all supplied initial/state/transition properties; skips counted separately\n{report}\nThis summary describes a bounded model run. Failure traces, when found, are stored separately.\n",
        env!("CARGO_PKG_VERSION"),
        env!("STATELESS_BUILD_ID"),
        model.metadata()
    );
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)?;
    file.write_all(text.as_bytes())?;
    file.sync_all()?;
    println!("saved {}", path.display());
    Ok(())
}
fn save(path: &Path, trace: &Trace) -> Result<(), Error> {
    let mut bytes = Vec::new();
    trace.write_to(&mut bytes)?;
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    println!("saved {} ({} bytes)", path.display(), bytes.len());
    Ok(())
}
fn inspect(trace: &Trace) {
    summarize(trace);
    println!(
        "versions: model={}, properties={}, codec={}",
        trace.metadata.model_version,
        trace.metadata.properties_version,
        trace.metadata.codec_version
    );
    println!(
        "seed: {:?}\nparameters: {:?}",
        trace.config.seed, trace.config.parameters
    );
    println!(
        "initial state bytes: {:02x?}\ninitial checks: {:?}",
        trace.initial_state, trace.initial_checks
    );
    for (index, step) in trace.steps.iter().enumerate() {
        println!(
            "step {}\n  input bytes: {:02x?}\n  disposition: {:?}\n  output bytes: {:02x?}\n  state bytes: {:02x?}\n  checks: {:?}",
            index + 1,
            step.input,
            step.disposition,
            step.outputs,
            step.post_state,
            step.checks
        );
    }
}
fn summarize(trace: &Trace) {
    println!(
        "model: {}\nbuild: {}\nstrategy: {}\ntransitions: {}\ntermination: {:?}",
        trace.metadata.name,
        trace.metadata.build,
        trace.config.strategy,
        trace.steps.len(),
        trace.termination
    );
    for (step, checks) in std::iter::once((0, &trace.initial_checks)).chain(
        trace
            .steps
            .iter()
            .enumerate()
            .map(|(i, s)| (i + 1, &s.checks)),
    ) {
        for check in checks {
            if !matches!(check.status, stateless::CheckStatus::Passed) {
                println!("step {step}: {}: {:?}", check.id, check.status);
            }
        }
    }
}
fn flag(args: &mut Vec<String>, flag: &str) -> bool {
    if let Some(index) = args.iter().position(|a| a == flag) {
        args.remove(index);
        true
    } else {
        false
    }
}
fn number<T: std::str::FromStr>(
    args: &mut Vec<String>,
    name: &str,
    default: T,
) -> Result<T, Error> {
    let Some(index) = args.iter().position(|arg| arg == name) else {
        return Ok(default);
    };
    args.remove(index);
    if index >= args.len() {
        return Err(format!("missing value for {name}").into());
    }
    args.remove(index)
        .parse()
        .map_err(|_| format!("invalid value for {name}").into())
}
fn reject_extra(args: &[String]) -> Result<(), Error> {
    if args.is_empty() {
        Ok(())
    } else {
        Err(format!("unexpected arguments: {}", args.join(" ")).into())
    }
}

fn optional_number<T: std::str::FromStr>(
    args: &mut Vec<String>,
    name: &str,
) -> Result<Option<T>, Error> {
    let Some(index) = args.iter().position(|arg| arg == name) else {
        return Ok(None);
    };
    args.remove(index);
    if index >= args.len() {
        return Err(format!("missing value for {name}").into());
    }
    args.remove(index)
        .parse()
        .map(Some)
        .map_err(|_| format!("invalid value for {name}").into())
}

use std::{
    fs,
    path::PathBuf,
    process::{Command, Output},
    sync::atomic::{AtomicU64, Ordering},
};
static NEXT: AtomicU64 = AtomicU64::new(0);
struct Scratch(PathBuf);
impl Scratch {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "stateless-cli-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn run(&self, args: &[&str]) -> Output {
        Command::new(env!("CARGO_BIN_EXE_stateless"))
            .current_dir(&self.0)
            .args(args)
            .output()
            .unwrap()
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[test]
fn fresh_process_record_replay_and_fixed_divergence() {
    let s = Scratch::new();
    assert_eq!(s.run(&["demo", "failure.sttrace"]).status.code(), Some(1));
    let replay = s.run(&["replay", "failure.sttrace"]);
    assert_eq!(
        replay.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&replay.stderr)
    );
    assert!(String::from_utf8_lossy(&replay.stdout).contains("failure reproduced: true"));
    assert_eq!(
        s.run(&["replay", "failure.sttrace", "--fixed"])
            .status
            .code(),
        Some(4)
    );
    let fixed = s.run(&[
        "replay",
        "failure.sttrace",
        "--fixed",
        "--allow-build-mismatch",
    ]);
    assert_eq!(fixed.status.code(), Some(3));
    assert!(String::from_utf8_lossy(&fixed.stdout).contains("Some(3)"));
    assert_eq!(s.run(&["demo", "failure.sttrace"]).status.code(), Some(2));
    assert_eq!(
        s.run(&["inspect", "failure.sttrace"]).status.code(),
        Some(0)
    );
}

#[test]
fn search_failures_are_saved_and_fixed_model_exhausts() {
    let s = Scratch::new();
    for command in ["fuzz", "enumerate"] {
        let file = format!("{command}.sttrace");
        let out = s.run(&[command, &file]);
        assert_eq!(
            out.status.code(),
            Some(1),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        assert!(s.0.join(format!("{command}.original.sttrace")).is_file());
        assert_eq!(s.run(&["replay", &file]).status.code(), Some(0));
    }
    let out = s.run(&["enumerate", "unused.sttrace", "--fixed"]);
    assert_eq!(
        out.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(String::from_utf8_lossy(&out.stdout).contains("GraphExhausted"));
    assert!(!s.0.join("unused.sttrace").exists());
    assert!(
        fs::read_to_string(s.0.join("unused.sttrace.report.txt"))
            .unwrap()
            .contains("GraphExhausted")
    );
    assert_eq!(
        s.run(&["enumerate", "bounded.sttrace", "--fixed", "--depth", "0"])
            .status
            .code(),
        Some(5)
    );
    assert!(
        fs::read_to_string(s.0.join("bounded.sttrace.report.txt"))
            .unwrap()
            .contains("DepthBound")
    );
}

#[test]
fn invalid_options_and_truncated_artifacts_fail() {
    let s = Scratch::new();
    assert_eq!(s.run(&["fuzz", "x", "--seed"]).status.code(), Some(2));
    assert_eq!(s.run(&["demo", "x", "--unknown"]).status.code(), Some(2));
    fs::write(s.0.join("bad.sttrace"), b"invalid").unwrap();
    assert_eq!(s.run(&["replay", "bad.sttrace"]).status.code(), Some(2));
}

fn assert_code(output: &Output, expected: i32) {
    assert_eq!(
        output.status.code(),
        Some(expected),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

fn job_reports(path: &std::path::Path) -> std::collections::BTreeMap<String, String> {
    fs::read_dir(path)
        .unwrap()
        .map(|entry| entry.unwrap())
        .filter_map(|entry| {
            let name = entry.file_name().to_string_lossy().into_owned();
            (name.starts_with("job-") && name.ends_with(".report.txt"))
                .then(|| (name, fs::read_to_string(entry.path()).unwrap()))
        })
        .collect()
}

#[test]
fn automatic_fixed_campaign_jobs_are_identical_with_one_or_four_workers() {
    let s = Scratch::new();
    for (directory, workers) in [("one", "1"), ("four", "4")] {
        let output = s.run(&[
            "campaign",
            directory,
            "--auto",
            "--fixed",
            "--seed",
            "42",
            "--workers",
            workers,
            "--cases",
            "10",
            "--steps",
            "12",
        ]);
        assert_code(&output, 0);
        let summary = fs::read_to_string(s.0.join(directory).join("campaign.report.txt")).unwrap();
        assert!(summary.contains("JobsCompleted"));
        assert!(summary.contains("accounting_complete: true"));
        assert!(summary.contains("not_started: 0"));
    }
    let one = job_reports(&s.0.join("one"));
    let four = job_reports(&s.0.join("four"));
    assert_eq!(one.len(), 16);
    // Per-job reports exclude worker count, paths, arrival order, and timing.
    assert_eq!(one, four);
    for report in one.values() {
        assert!(report.contains("enumerated-reservoir-v1"));
        assert!(report.contains("campaign.master_seed"));
        assert!(report.contains("Seed:"));
    }
    let before = fs::read(s.0.join("one/campaign.report.txt")).unwrap();
    assert_code(&s.run(&["campaign", "one", "--auto", "--fixed"]), 2);
    assert_eq!(
        fs::read(s.0.join("one/campaign.report.txt")).unwrap(),
        before
    );
    assert_eq!(job_reports(&s.0.join("one")), one);
}

#[test]
fn automatic_campaign_failure_artifacts_replay_in_new_processes() {
    let s = Scratch::new();
    let output = s.run(&[
        "campaign",
        "failures",
        "--auto",
        "--seed",
        "42",
        "--jobs",
        "4",
        "--workers",
        "2",
        "--cases",
        "100",
        "--steps",
        "20",
        "--stop-on-failure",
    ]);
    assert_code(&output, 1);
    let traces: Vec<_> = fs::read_dir(s.0.join("failures"))
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| {
            path.extension()
                .is_some_and(|extension| extension == "sttrace")
        })
        .collect();
    assert!(traces.len() >= 2);
    assert!(
        traces
            .iter()
            .any(|path| path.to_string_lossy().ends_with(".original.sttrace"))
    );
    for trace in traces {
        let replay = s.run(&["replay", trace.to_str().unwrap()]);
        assert_code(&replay, 0);
        assert!(String::from_utf8_lossy(&replay.stdout).contains("failure reproduced: true"));
        let decoded = stateless::trace::Trace::read_from(
            fs::File::open(&trace).unwrap(),
            &stateless::trace::ReadLimits::default(),
        )
        .unwrap();
        assert!(
            decoded
                .config
                .parameters
                .iter()
                .any(|(key, value)| key == "generator" && value == "enumerated-reservoir-v1")
        );
        assert!(
            decoded
                .config
                .parameters
                .iter()
                .any(|(key, _)| key == "campaign.job_id")
        );
        assert!(decoded.config.seed.is_some());
    }
}

#[test]
fn automatic_single_fuzz_persists_replayable_failure_and_engine_errors() {
    let s = Scratch::new();
    assert_code(
        &s.run(&["fuzz", "auto.sttrace", "--auto", "--seed", "42"]),
        1,
    );
    assert_code(&s.run(&["replay", "auto.sttrace"]), 0);
    let output = s.run(&["fuzz", "limited.sttrace", "--auto", "--max-candidates", "1"]);
    assert_code(&output, 2);
    let report = fs::read_to_string(s.0.join("limited.sttrace.report.txt")).unwrap();
    assert!(report.contains("Engine error"));
    assert!(report.contains("max_candidates"));
    assert!(!s.0.join("limited.sttrace").exists());
}

#[test]
fn campaign_rejects_bad_configuration_before_creating_any_directory() {
    let s = Scratch::new();
    for options in [
        vec!["--workers", "0"],
        vec!["--workers", "257"],
        vec!["--jobs", "0"],
        vec!["--auto", "--max-candidates", "0"],
        vec!["--max-candidates", "4"],
        vec!["--max-ms", "invalid"],
        vec!["--unknown"],
        vec!["--jobs", "18446744073709551615"],
    ] {
        let mut args = vec!["campaign", "invalid"];
        args.extend(options);
        assert_code(&s.run(&args), 2);
        assert!(!s.0.join("invalid").exists());
    }
}

#[test]
fn campaign_deadline_and_automatic_domain_error_are_not_successes() {
    let s = Scratch::new();
    assert_code(
        &s.run(&[
            "campaign",
            "deadline",
            "--auto",
            "--fixed",
            "--jobs",
            "4",
            "--workers",
            "2",
            "--max-ms",
            "0",
        ]),
        5,
    );
    let report = fs::read_to_string(s.0.join("deadline/campaign.report.txt")).unwrap();
    assert!(report.contains("Deadline"));
    assert!(job_reports(&s.0.join("deadline")).is_empty());

    assert_code(
        &s.run(&[
            "campaign",
            "limited",
            "--auto",
            "--fixed",
            "--jobs",
            "1",
            "--workers",
            "1",
            "--max-candidates",
            "1",
        ]),
        2,
    );
    let report = fs::read_to_string(s.0.join("limited/campaign.report.txt")).unwrap();
    assert!(report.contains("errors: 1"));
    assert!(report.contains("accounting_complete: false"));
    let jobs = job_reports(&s.0.join("limited"));
    assert_eq!(jobs.len(), 1);
    assert!(jobs.values().next().unwrap().contains("max_candidates"));
}

#[test]
fn campaigns_default_to_the_custom_generator() {
    let s = Scratch::new();
    assert_code(
        &s.run(&[
            "campaign",
            "custom",
            "--fixed",
            "--jobs",
            "2",
            "--workers",
            "2",
            "--cases",
            "4",
            "--steps",
            "8",
        ]),
        0,
    );
    let reports = job_reports(&s.0.join("custom"));
    assert_eq!(reports.len(), 2);
    assert!(
        reports
            .values()
            .all(|report| report.contains("model-generate"))
    );
}

#[test]
fn zero_case_and_step_budgets_retain_existing_fuzz_semantics() {
    let s = Scratch::new();
    for (name, option) in [("no-cases", "--cases"), ("no-steps", "--steps")] {
        let trace = format!("{name}.sttrace");
        assert_code(&s.run(&["fuzz", &trace, option, "0"]), 0);
        let report = fs::read_to_string(s.0.join(format!("{trace}.report.txt"))).unwrap();
        assert!(report.contains("CasesCompleted"));
        assert!(!s.0.join(&trace).exists());
        assert_code(
            &s.run(&[
                "campaign",
                name,
                "--jobs",
                "2",
                "--workers",
                "1",
                option,
                "0",
            ]),
            0,
        );
        assert_eq!(job_reports(&s.0.join(name)).len(), 2);
    }
}

#[test]
fn guided_fuzz_records_feedback_and_replays_original_and_shrunk_failures() {
    let s = Scratch::new();
    let run = s.run(&[
        "fuzz",
        "guided.sttrace",
        "--guided",
        "--auto",
        "--seed",
        "42",
        "--cases",
        "100",
        "--steps",
        "20",
    ]);
    assert_eq!(
        run.status.code(),
        Some(1),
        "{}",
        String::from_utf8_lossy(&run.stderr)
    );
    let report = fs::read_to_string(s.0.join("guided.sttrace.report.txt")).unwrap();
    assert!(report.contains("GuidanceReport"));
    assert!(report.contains("guidance.feedback"));
    assert!(report.contains("request-lifecycle-projection"));
    assert!(report.contains(stateless::guided::ALGORITHM));
    for trace in ["guided.original.sttrace", "guided.sttrace"] {
        assert_eq!(s.run(&["replay", trace]).status.code(), Some(0));
        let inspected = s.run(&["inspect", trace]);
        assert_code(&inspected, 0);
        assert!(String::from_utf8_lossy(&inspected.stdout).contains(stateless::guided::ALGORITHM));
    }
}
#[test]
fn guided_cli_validates_before_creating_artifacts_and_reports_retention_limits() {
    let s = Scratch::new();
    for args in [
        vec!["fuzz", "bad.sttrace", "--corpus", "1"],
        vec!["campaign", "bad-campaign", "--guided", "--features", "0"],
    ] {
        assert_eq!(s.run(&args).status.code(), Some(2));
    }
    assert!(!s.0.join("bad.sttrace.report.txt").exists());
    assert!(!s.0.join("bad-campaign").exists());
    let result = s.run(&[
        "fuzz",
        "bounded.sttrace",
        "--guided",
        "--fixed",
        "--corpus",
        "1",
        "--corpus-inputs",
        "1",
        "--features",
        "2",
    ]);
    assert_eq!(
        result.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let report = fs::read_to_string(s.0.join("bounded.sttrace.report.txt")).unwrap();
    assert!(report.contains("feature_limit_reached: true"));
}
#[test]
fn guided_campaign_cli_jobs_are_identical_across_worker_counts() {
    let s = Scratch::new();
    for (dir, workers) in [("guided-one", "1"), ("guided-four", "4")] {
        let run = s.run(&[
            "campaign",
            dir,
            "--guided",
            "--auto",
            "--fixed",
            "--jobs",
            "4",
            "--workers",
            workers,
            "--cases",
            "20",
            "--steps",
            "20",
        ]);
        assert_eq!(
            run.status.code(),
            Some(0),
            "{}",
            String::from_utf8_lossy(&run.stderr)
        );
    }
    for id in 0..4 {
        let file = format!("job-{id:06}.sttrace.report.txt");
        assert_eq!(
            fs::read(s.0.join("guided-one").join(&file)).unwrap(),
            fs::read(s.0.join("guided-four").join(&file)).unwrap()
        );
    }
}

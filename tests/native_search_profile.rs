//! Independent search-v1 goldens exercised through Rust's public engines.
//! Compiling this suite does not qualify its runtime behavior or native ports.
use std::collections::HashMap;
use std::sync::atomic::AtomicBool;
use std::time::Duration;

use stateless::automatic::{AutoOptions, contains_enumerated, sample_enumerated};
use stateless::explore::*;
use stateless::*;

struct Fixture<'a> {
    name: &'a str,
    max_candidates: usize,
}

impl Model for Fixture<'_> {
    type State = i32;
    type Input = i32;
    type Output = ();

    fn metadata(&self) -> ModelMetadata {
        ModelMetadata {
            name: format!("search-v1:{}", self.name),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "independent-fixture-v1".into(),
        }
    }
    fn initial_state(&self) -> Result<i32, ModelError> {
        Ok(0)
    }
    fn step(&self, state: &i32, input: &i32) -> Result<Transition<i32, ()>, ModelError> {
        let next = match self.name {
            "causal" | "phase" | "cycle" => *input,
            _ => state + input,
        };
        Ok(Transition::accepted(next, vec![]))
    }
    fn check_state(&self, state: &i32) -> Result<Vec<Check>, ModelError> {
        let (id, failed) = match self.name {
            "initial" => ("bad", true),
            "threshold" => ("bad", *state >= 3),
            "causal" => ("bad", *state == 2),
            "phase" => ("same", *state == 1),
            "cycle" => ("bad", *state != 0),
            _ => return Ok(vec![]),
        };
        Ok(vec![if failed {
            Check::failed(id, "fixture failure")
        } else {
            Check::passed(id)
        }])
    }
    fn check_transition(
        &self,
        _: &i32,
        _: &i32,
        t: &TransitionRef<'_, i32, ()>,
    ) -> Result<Vec<Check>, ModelError> {
        Ok(if self.name == "phase" {
            vec![if *t.state == 2 {
                Check::failed("same", "fixture transition failure")
            } else {
                Check::passed("same")
            }]
        } else {
            vec![]
        })
    }
}
impl Enumerate for Fixture<'_> {
    fn inputs(&self, state: &i32) -> Result<Vec<i32>, ModelError> {
        Ok(match self.name {
            "empty" => vec![],
            "binary" | "phase" | "cycle" => vec![1, 2],
            "causal" => match state {
                0 => vec![1],
                1 => vec![2],
                _ => vec![],
            },
            "singleton" | "initial" | "threshold" => vec![1],
            other => panic!("unknown fixture {other}"),
        })
    }
}
impl Generate for Fixture<'_> {
    fn generate(&self, state: &i32, rng: &mut Rng) -> Result<Option<i32>, ModelError> {
        if self.name == "digits" {
            return Ok(Some((rng.next_u64() % 10) as i32));
        }
        sample_enumerated(
            self,
            state,
            rng,
            AutoOptions {
                max_candidates: self.max_candidates,
            },
        )
    }
    fn is_enabled(&self, state: &i32, input: &i32) -> Result<bool, ModelError> {
        if self.name == "digits" {
            return Ok((0..=9).contains(input));
        }
        contains_enumerated(
            self,
            state,
            input,
            AutoOptions {
                max_candidates: self.max_candidates,
            },
        )
    }
    fn simpler_inputs(&self, input: &i32) -> Vec<i32> {
        match (self.name, input) {
            ("phase", 1) | ("cycle", 1) => vec![2],
            ("cycle", 2) => vec![1],
            _ => vec![],
        }
    }
}

#[derive(Default)]
struct ExecutedCases(Vec<Vec<i32>>);
impl SearchObserver<Fixture<'_>> for ExecutedCases {
    fn initial(&mut self, _: &i32, _: &[Check]) {
        self.0.push(vec![]);
    }
    fn transition(
        &mut self,
        _: &i32,
        input: &i32,
        _: &TransitionRef<'_, i32, ()>,
        _: &[Check],
        _: &[Check],
        _: &[i32],
    ) {
        self.0
            .last_mut()
            .expect("initial observation precedes transition")
            .push(*input);
    }
}

fn phase(text: &str) -> CheckPhase {
    match text {
        "initial_state" => CheckPhase::InitialState,
        "state" => CheckPhase::State,
        "transition" => CheckPhase::Transition,
        _ => panic!("unknown phase {text}"),
    }
}
fn phase_name(value: CheckPhase) -> &'static str {
    match value {
        CheckPhase::InitialState => "initial_state",
        CheckPhase::State => "state",
        CheckPhase::Transition => "transition",
    }
}
fn array(text: &str) -> Vec<i32> {
    let inside = text.strip_prefix('[').unwrap().strip_suffix(']').unwrap();
    if inside.is_empty() {
        vec![]
    } else {
        inside.split(',').map(|s| s.parse().unwrap()).collect()
    }
}
fn cases_json(cases: &[Vec<i32>]) -> String {
    format!(
        "[{}]",
        cases
            .iter()
            .map(|case| format!(
                "[{}]",
                case.iter()
                    .map(ToString::to_string)
                    .collect::<Vec<_>>()
                    .join(",")
            ))
            .collect::<Vec<_>>()
            .join(",")
    )
}
fn assert_failure(failure: Option<&Failure<i32>>, row: &HashMap<&str, &str>) {
    if row["failed_id"] == "-" {
        assert!(failure.is_none(), "unexpected failure: {failure:?}");
    } else {
        let failure = failure.expect("expected a failure");
        assert_eq!(failure.inputs, array(row["witness"]));
        assert_eq!(failure.violations.len(), 1);
        assert_eq!(failure.violations[0].check.id.as_str(), row["failed_id"]);
        assert_eq!(phase_name(failure.violations[0].phase), row["failed_phase"]);
        assert!(failure.violations[0].check.is_failure());
    }
}

// Derived independently from the finite fixture definitions and specified search
// order; these are not produced by observing the engine under test.
fn expected_history(case: &str) -> Vec<(usize, ShrinkOperation, ShrinkOutcome)> {
    use ShrinkOperation::*;
    use ShrinkOutcome::*;
    match case {
        "shrink-threshold" => vec![
            (3, ValidateOriginal, Retained),
            (1, Remove { start: 0, end: 2 }, NoFailure),
            (2, Remove { start: 2, end: 3 }, NoFailure),
            (2, Remove { start: 0, end: 1 }, NoFailure),
            (2, Remove { start: 1, end: 2 }, NoFailure),
            (2, Remove { start: 2, end: 3 }, NoFailure),
        ],
        "shrink-attempt-limit" => vec![(3, ValidateOriginal, Retained)],
        "shrink-zero-transitions" | "shrink-cancelled-entry" | "shrink-deadline-entry" => {
            vec![(3, ValidateOriginal, ResourceLimit)]
        }
        "shrink-initial" => vec![(0, ValidateOriginal, Retained)],
        "shrink-causal" => vec![
            (2, ValidateOriginal, Retained),
            (1, Remove { start: 0, end: 1 }, InvalidCausality),
            (1, Remove { start: 1, end: 2 }, NoFailure),
        ],
        "shrink-phase-identity" => vec![
            (1, ValidateOriginal, Retained),
            (0, Remove { start: 0, end: 1 }, NoFailure),
            (
                1,
                Simplify {
                    index: 0,
                    alternative: 0,
                },
                DifferentFailure,
            ),
        ],
        "shrink-cycle" => vec![
            (1, ValidateOriginal, Retained),
            (0, Remove { start: 0, end: 1 }, NoFailure),
            (
                1,
                Simplify {
                    index: 0,
                    alternative: 0,
                },
                Retained,
            ),
            (0, Remove { start: 0, end: 1 }, NoFailure),
        ],
        other => panic!("missing independent history for {other}"),
    }
}

fn history_json(history: &[ShrinkAttempt]) -> String {
    let entries: Vec<_> = history.iter().map(|entry| {
        let operation = match entry.operation {
            ShrinkOperation::ValidateOriginal => "{\"kind\":\"validate_original\"}".to_owned(),
            ShrinkOperation::Remove { start, end } => format!("{{\"kind\":\"remove\",\"start\":{start},\"end\":{end}}}"),
            ShrinkOperation::Simplify { index, alternative } => format!("{{\"kind\":\"simplify\",\"index\":{index},\"alternative\":{alternative}}}"),
        };
        let outcome = match entry.outcome {
            ShrinkOutcome::Retained => "retained",
            ShrinkOutcome::InvalidCausality => "invalid_causality",
            ShrinkOutcome::DifferentFailure => "different_failure",
            ShrinkOutcome::NoFailure => "no_failure",
            ShrinkOutcome::AlreadySeen => "already_seen",
            ShrinkOutcome::ResourceLimit => "resource_limit",
        };
        format!("{{\"attempt\":{},\"candidate_length\":{},\"operation\":{operation},\"outcome\":\"{outcome}\"}}", entry.attempt, entry.candidate_length)
    }).collect();
    format!("[{}]", entries.join(","))
}

#[test]
fn shared_search_candidate_corpus() {
    let mut lines = include_str!("../conformance/search-v1.tsv").lines();
    let header: Vec<_> = lines.next().unwrap().split('\t').collect();
    let mut count = 0;
    let mut histories = Vec::new();
    for line in lines.filter(|line| !line.is_empty() && !line.starts_with('#')) {
        let values: Vec<_> = line.split('\t').collect();
        assert_eq!(values.len(), header.len());
        let row: HashMap<_, _> = header.iter().copied().zip(values).collect();
        let case = row["case"];
        eprintln!("checking independent search-v1 row: {case}");
        let fixture = Fixture {
            name: row["fixture"],
            max_candidates: if row["max_candidates"] == "-" {
                100_000
            } else {
                row["max_candidates"].parse().unwrap()
            },
        };
        let cancelled = AtomicBool::new(row["control"] == "cancelled");
        let control = RunLimits {
            cancellation: Some(&cancelled),
            max_duration: match row["control"] {
                "deadline" => Some(Duration::ZERO),
                "none" | "cancelled" => None,
                other => panic!("unknown control {other}"),
            },
        };
        match row["operation"] {
            "fuzz" => {
                let config = FuzzConfig {
                    seed: row["seed"].parse().unwrap(),
                    cases: row["cases"].parse().unwrap(),
                    max_steps: row["max_steps"].parse().unwrap(),
                    max_transitions: row["max_transitions"].parse().unwrap(),
                    mutation_percent: row["mutation_percent"].parse().unwrap(),
                };
                let mut observed = ExecutedCases::default();
                let result = fuzz_observed(&fixture, config, control, &mut observed);
                if row["error"] != "-" {
                    let error = result.expect_err("expected automatic generation error");
                    let message = error.to_string();
                    let matches = match row["error"] {
                        "auto_candidate_limit" => message.contains("exceeds max_candidates"),
                        "invalid_candidate_budget" => {
                            message.contains("max_candidates must be positive")
                        }
                        other => panic!("unknown error category {other}"),
                    };
                    assert!(matches, "{case}: incorrect error category: {error}");
                    count += 1;
                    continue;
                }
                let report = result.unwrap_or_else(|e| panic!("{case}: unexpected error: {e}"));
                assert_eq!(row["error"], "-", "{case}");
                let termination = match report.termination {
                    FuzzTermination::CasesCompleted => "cases_completed",
                    FuzzTermination::TransitionLimit => "transition_limit",
                    FuzzTermination::FailureFound => "failure_found",
                    FuzzTermination::Deadline => "deadline",
                    FuzzTermination::Cancelled => "cancelled",
                };
                assert_eq!(termination, row["termination"], "{case}");
                assert_eq!(report.seed, row["seed"].parse::<u64>().unwrap(), "{case}");
                assert_eq!(
                    report.cases,
                    row["started_cases"].parse::<usize>().unwrap(),
                    "{case}"
                );
                assert_eq!(
                    report.transitions,
                    row["transitions"].parse::<u64>().unwrap(),
                    "{case}"
                );
                assert_eq!(report.skipped_checks, 0, "{case}");
                assert!(report.guidance.is_none(), "{case}");
                assert_failure(report.failure.as_ref(), &row);
                assert_eq!(cases_json(&observed.0), row["trace"], "{case}");
            }
            "shrink" => {
                let original = Failure {
                    inputs: array(row["original"]),
                    violations: vec![PropertyFailure {
                        phase: phase(row["target_phase"]),
                        check: Check::failed(row["target_id"].to_owned(), "supplied evidence"),
                    }],
                };
                let result = shrink_with_limits(
                    &fixture,
                    &original,
                    ShrinkConfig {
                        max_attempts: row["max_attempts"].parse().unwrap(),
                    },
                    ShrinkLimits {
                        control,
                        max_replayed_transitions: (row["max_replayed_transitions"] != "-")
                            .then(|| row["max_replayed_transitions"].parse().unwrap()),
                    },
                );
                if row["error"] == "invalid_attempt_budget" {
                    let error = result.expect_err("zero attempts must fail");
                    assert!(
                        error.to_string().contains("max_attempts")
                            && error.to_string().contains("zero"),
                        "{case}: {error}"
                    );
                    count += 1;
                    continue;
                }
                assert_eq!(row["error"], "-", "unknown error category");
                let report = result.unwrap_or_else(|e| panic!("{case}: unexpected error: {e}"));
                let termination = match report.termination {
                    ShrinkTermination::SearchComplete => "search_complete",
                    ShrinkTermination::AttemptLimit => "attempt_limit",
                    ShrinkTermination::TransitionLimit => "transition_limit",
                    ShrinkTermination::Deadline => "deadline",
                    ShrinkTermination::Cancelled => "cancelled",
                };
                assert_eq!(termination, row["termination"], "{case}");
                assert_eq!(report.original, original, "{case}");
                assert_eq!(
                    report.replayed_transitions,
                    row["transitions"].parse::<u64>().unwrap(),
                    "{case}"
                );
                assert_eq!(
                    report.attempts,
                    row["attempts"].parse::<usize>().unwrap(),
                    "{case}"
                );
                assert_eq!(
                    report.validated_original,
                    row["validated_original"].parse::<bool>().unwrap(),
                    "{case}"
                );
                assert_failure(Some(&report.minimized), &row);
                let expected: Vec<_> = expected_history(case)
                    .into_iter()
                    .enumerate()
                    .map(
                        |(index, (candidate_length, operation, outcome))| ShrinkAttempt {
                            attempt: index + 1,
                            candidate_length,
                            operation,
                            outcome,
                        },
                    )
                    .collect();
                assert_eq!(report.history, expected, "{case}");
                histories.push(format!("\"{case}\":{}", history_json(&report.history)));
            }
            other => panic!("unknown operation {other}"),
        }
        count += 1;
    }
    assert_eq!(count, 25, "shared corpus unexpectedly changed case count");
    // All strings in this hand-authored history corpus are identifier tokens;
    // whitespace removal therefore normalizes formatting without changing values.
    let shared: String = include_str!("../conformance/search-v1-history.json")
        .chars()
        .filter(|c| !c.is_whitespace())
        .collect();
    assert_eq!(format!("{{{}}}", histories.join(",")), shared);
}

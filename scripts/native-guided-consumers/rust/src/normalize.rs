use serde_json::{Value, json};
use stateless::explore::{Failure, FuzzReport, ShrinkOperation, ShrinkReport};
use stateless::{Check, CheckStatus, Disposition};

pub fn disposition(disposition: &Disposition) -> Value {
    match disposition {
        Disposition::Accepted => json!({"kind": "Accepted", "reason": null}),
        Disposition::Rejected(reason) => json!({"kind": "Rejected", "reason": reason}),
        Disposition::Ignored(reason) => json!({"kind": "Ignored", "reason": reason}),
    }
}

pub fn check(check: &Check) -> Value {
    let (status, message) = match &check.status {
        CheckStatus::Passed => ("Passed", None),
        CheckStatus::Failed(message) => ("Failed", Some(message.as_str())),
        CheckStatus::Skipped(message) => ("Skipped", Some(message.as_str())),
    };
    json!({"id": check.id.as_str(), "status": status, "message": message})
}
pub fn checks(checks: &[Check]) -> Vec<Value> {
    checks.iter().map(check).collect()
}
pub fn failure(failure: &Failure<i64>) -> Value {
    json!({"inputs": failure.inputs, "violations": failure.violations.iter().map(|v|
        json!({"phase": format!("{:?}", v.phase), "check": check(&v.check)})).collect::<Vec<_>>()})
}
pub fn report(report: &FuzzReport<i64>) -> Value {
    let guidance = report.guidance.as_ref().map(|g| json!({
        "algorithm": g.algorithm,
        "config": {"max_corpus_entries": g.config.max_corpus_entries,
            "max_corpus_inputs": g.config.max_corpus_inputs, "max_features": g.config.max_features,
            "max_features_per_observation": g.config.max_features_per_observation},
        "feedback": {"name": g.feedback.name, "version": g.feedback.version, "build": g.feedback.build},
        "unique_features": g.unique_features, "corpus_inputs": g.corpus_inputs,
        "corpus": g.corpus.iter().map(|entry| json!({"inputs": entry.inputs,
            "new_features": entry.new_features, "discovered_case": entry.discovered_case,
            "discovered_transition": entry.discovered_transition})).collect::<Vec<_>>(),
        "mutation_cases": g.mutation_cases, "dropped_prefixes": g.dropped_prefixes,
        "evicted_prefixes": g.evicted_prefixes, "feature_limit_reached": g.feature_limit_reached,
        "corpus_limit_reached": g.corpus_limit_reached,
    }));
    json!({"termination": format!("{:?}", report.termination), "seed": report.seed.to_string(),
        "cases": report.cases, "transitions": report.transitions, "skipped_checks": report.skipped_checks,
        "failure": report.failure.as_ref().map(failure), "guidance": guidance})
}
pub fn shrink(report: &ShrinkReport<i64>) -> Value {
    json!({"original": failure(&report.original), "minimized": failure(&report.minimized),
        "attempts": report.attempts, "termination": format!("{:?}", report.termination),
        "replayed_transitions": report.replayed_transitions, "validated_original": report.validated_original,
        "history": report.history.iter().map(|attempt| {
            let operation = match attempt.operation {
                ShrinkOperation::ValidateOriginal => json!({"kind": "ValidateOriginal"}),
                ShrinkOperation::Remove { start, end } => json!({"kind": "Remove", "start": start, "end": end}),
                ShrinkOperation::Simplify { index, alternative } => json!({"kind": "Simplify", "index": index, "alternative": alternative}),
            };
            json!({"attempt": attempt.attempt, "candidate_length": attempt.candidate_length,
                "operation": operation, "outcome": format!("{:?}", attempt.outcome)})
        }).collect::<Vec<_>>()})
}

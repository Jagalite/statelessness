//! Persist fault-control evidence without overwriting prior runs.
use super::*;
use crate::execution::{ReplayOptions, ReplayOutcome, record, replay};
use crate::trace::{RunConfig, Termination, Trace};
use std::fs::OpenOptions;
use std::io::Write;
use std::path::Path;

pub fn save_trace(path: &Path, trace: &Trace) -> Result<(), Box<dyn std::error::Error>> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    trace.write_to(&mut file)?;
    file.sync_all()?;
    Ok(())
}
fn exact_target(model: &Controller, trace: &Trace, target: &str) -> Result<(), ModelError> {
    let report = replay(model, trace, ReplayOptions::default())?;
    let target_failed = trace
        .steps
        .iter()
        .flat_map(|s| &s.checks)
        .chain(trace.initial_checks.iter())
        .any(|c| c.id == target && c.is_failure());
    if trace.termination != Termination::PropertyFailed
        || report.outcome != ReplayOutcome::Exact
        || !report.failure_reproduced
        || !target_failed
    {
        return Err(ModelError::new(
            "recorded witness did not exactly replay the required target",
        ));
    }
    Ok(())
}
/// No outcome is green until every named target has a preserved, replayed witness.
/// The returned results retain survives/not_evaluated rather than flattening them
/// into a boolean. The executable also gates against the independent catalog.
pub fn write_mutation_suite(
    directory: &Path,
    config: SearchConfig,
) -> Result<Vec<MutationResult>, Box<dyn std::error::Error>> {
    std::fs::create_dir(directory)?;
    let mut results = Vec::new();
    for mutation in Mutation::ALL {
        let mut result = evaluate_mutation(mutation, config.clone());
        if result.status == MutationStatus::Killed {
            let model = Controller::with_mutation(mutation);
            let write = (|| -> Result<(), Box<dyn std::error::Error>> {
                for (label, failure) in [
                    ("original", &result.original),
                    ("minimized", &result.minimized),
                ] {
                    let failure = failure.as_ref().ok_or("missing killed witness")?;
                    let trace = record(
                        &model,
                        failure.inputs.clone(),
                        RunConfig {
                            strategy: "reference-fault-control".into(),
                            seed: None,
                            parameters: vec![
                                ("mutation".into(), mutation.name().into()),
                                ("target".into(), mutation.target().into()),
                                ("max_states".into(), config.max_states.to_string()),
                                ("max_transitions".into(), config.max_transitions.to_string()),
                                ("max_depth".into(), config.max_depth.to_string()),
                            ],
                        },
                        failure.inputs.len(),
                    )?;
                    exact_target(&model, &trace, mutation.target())?;
                    save_trace(
                        &directory.join(format!("{}-{label}.sttrace", mutation.name())),
                        &trace,
                    )?;
                }
                Ok(())
            })();
            if let Err(error) = write {
                result.status = MutationStatus::NotEvaluated {
                    reason: format!("evidence preservation/replay: {error}"),
                };
            }
        }
        results.push(result);
    }
    let mut report = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(directory.join("mutations.json"))?;
    report.write_all(suite_json(&results).as_bytes())?;
    report.sync_all()?;
    Ok(results)
}
fn quoted(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c < ' ' => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}
pub fn suite_json(results: &[MutationResult]) -> String {
    let model = Controller::default();
    let items = results.iter().map(|result| {
        let (status, reason) = match &result.status { MutationStatus::Killed => ("killed", None), MutationStatus::Survives => ("survives", None), MutationStatus::NotEvaluated { reason } => ("not_evaluated", Some(reason)) };
        let witness = |label: &str| if result.status == MutationStatus::Killed { quoted(&format!("{}-{label}.sttrace", result.mutation.name())) } else { "null".into() };
        format!("{{\"mutation\":{},\"target\":{},\"status\":{},\"reason\":{},\"search\":{},\"shrink_complete\":{},\"original\":{},\"minimized\":{}}}", quoted(result.mutation.name()), quoted(result.mutation.target()), quoted(status), reason.map(|s| quoted(s)).unwrap_or("null".into()), quoted(&format!("{:?}", result.search)), result.shrink_complete, witness("original"), witness("minimized"))
    }).collect::<Vec<_>>().join(",\n");
    format!(
        "{{\"schema_version\":1,\"model\":\"reference-controller\",\"build\":{},\"review_status\":\"awaiting_independent_review\",\"results\":[{}]}}\n",
        quoted(&model.metadata().build),
        items
    )
}

/// Stable human-readable regression inputs. The parser rejects duplicate/unknown
/// mutations and requires exactly the independent mutation catalog's sections.
pub fn regression_corpus(text: &str) -> Result<Vec<(Mutation, Vec<Input>)>, ModelError> {
    let mut cases: Vec<(Mutation, Vec<Input>)> = Vec::new();
    for line in text
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
    {
        if let Some(name) = line.strip_prefix('[').and_then(|s| s.strip_suffix(']')) {
            let mutation = Mutation::named(name)
                .ok_or_else(|| ModelError::new("unknown regression mutation"))?;
            if cases.iter().any(|(m, _)| *m == mutation) {
                return Err(ModelError::new("duplicate regression mutation"));
            }
            cases.push((mutation, Vec::new()));
            continue;
        }
        let number = |s: &str| {
            s.parse::<u64>()
                .map_err(|_| ModelError::new("bad regression integer"))
        };
        let attempt = |s: &str| {
            s.parse::<u8>()
                .map_err(|_| ModelError::new("bad regression attempt"))
        };
        let input = match line.split_whitespace().collect::<Vec<_>>().as_slice() {
            ["submit"] => Input::Submit,
            ["cancel", g] => Input::Cancel {
                generation: number(g)?,
            },
            ["cancelled", g, a] => Input::Cancelled {
                generation: number(g)?,
                attempt: attempt(a)?,
            },
            ["complete", g, a, result] if *result == "success" || *result == "retry" => {
                Input::Complete {
                    generation: number(g)?,
                    attempt: attempt(a)?,
                    outcome: if *result == "success" {
                        Outcome::Success
                    } else {
                        Outcome::RetryableFailure
                    },
                }
            }
            ["tick", now] => Input::Tick { now: number(now)? },
            _ => return Err(ModelError::new("bad regression input")),
        };
        cases
            .last_mut()
            .ok_or_else(|| ModelError::new("regression input before section"))?
            .1
            .push(input);
    }
    if cases.len() != Mutation::ALL.len() || cases.iter().any(|(_, inputs)| inputs.is_empty()) {
        return Err(ModelError::new("missing/empty required regression cases"));
    }
    Ok(cases)
}
pub fn check_regressions(text: &str) -> Result<(), ModelError> {
    for (mutation, inputs) in regression_corpus(text)? {
        let model = Controller::with_mutation(mutation);
        let trace = record(&model, inputs.clone(), RunConfig::default(), inputs.len())?;
        exact_target(&model, &trace, mutation.target())?;
        let fixed = record(
            &Controller::default(),
            inputs.clone(),
            RunConfig::default(),
            inputs.len(),
        )?;
        if fixed.termination == Termination::PropertyFailed
            || fixed
                .steps
                .iter()
                .flat_map(|s| &s.checks)
                .any(|c| !matches!(c.status, crate::CheckStatus::Passed))
        {
            return Err(ModelError::new(
                "fixed reducer failed a retained counterexample",
            ));
        }
    }
    Ok(())
}

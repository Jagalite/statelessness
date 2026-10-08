//! Checking, exact recording, and replay. No real effects are executed here.

use crate::model::*;
use crate::trace::{ReadLimits, RunConfig, Termination, Trace, TraceStep};
use std::num::NonZeroU64;

/// Full checking is the default. Periodic state checks reduce coverage, and the
/// skipped checks are explicitly included in each observation.
#[derive(Clone, Copy, Debug)]
pub struct CheckPolicy {
    pub state_every: NonZeroU64,
    pub transition_checks: bool,
}

impl Default for CheckPolicy {
    fn default() -> Self {
        Self {
            state_every: NonZeroU64::new(1).unwrap(),
            transition_checks: true,
        }
    }
}

/// Check a transition already performed by an application, without executing it
/// again or scheduling any work. `sequence` is one-based; zero is invalid.
/// The application must check its initial state separately.
pub fn check_observed<M: Model>(
    model: &M,
    before: &M::State,
    input: &M::Input,
    transition: &Transition<M::State, M::Output>,
    sequence: u64,
    policy: CheckPolicy,
) -> Result<Vec<Check>, ModelError> {
    let mut checks = Vec::new();
    check_observed_into(
        model,
        before,
        input,
        transition,
        sequence,
        policy,
        &mut checks,
    )?;
    Ok(checks)
}

/// Check into caller-owned storage, clearing old observations while retaining
/// capacity. With static/shared IDs and append-style model callbacks, passing
/// checks require no per-transition allocation after warmup. On error the buffer
/// is cleared so a partial check batch cannot be mistaken for complete evidence.
pub fn check_observed_into<M: Model>(
    model: &M,
    before: &M::State,
    input: &M::Input,
    transition: &Transition<M::State, M::Output>,
    sequence: u64,
    policy: CheckPolicy,
    checks: &mut Vec<Check>,
) -> Result<(), ModelError> {
    checks.clear();
    if sequence == 0 {
        return Err(ModelError::new("transition sequence must be positive"));
    }
    let result = (|| {
        if sequence.is_multiple_of(policy.state_every.get()) {
            model
                .check_state_into(&transition.state, &mut CheckSink::new(checks))
                .map_err(|e| stage("state check", e))?;
        } else {
            checks.push(Check::skipped(
                "stateless.state_checks",
                "periodic checking policy",
            ));
        }
        if policy.transition_checks {
            model
                .check_transition_into(
                    before,
                    input,
                    &transition.as_ref(),
                    &mut CheckSink::new(checks),
                )
                .map_err(|e| stage("transition check", e))?;
        } else {
            checks.push(Check::skipped(
                "stateless.transition_checks",
                "disabled by policy",
            ));
        }
        Ok(())
    })();
    if result.is_err() {
        checks.clear();
    }
    result
}

/// Record a sequence with full checking and exact state/output encodings.
/// Stops at the first violation, checking the initial state before any input.
/// A callback/codec error is an engine error, not a property failure or success.
/// Retains the full trace within default format byte/item limits. An error after
/// the initial checkpoint preserves the coherent prefix with error termination.
pub fn record<M: ModelCodec>(
    model: &M,
    inputs: impl IntoIterator<Item = M::Input>,
    config: RunConfig,
    max_steps: usize,
) -> Result<Trace, ModelError> {
    record_with_limits(model, inputs, config, max_steps, &ReadLimits::default())
}

/// Record within explicit format limits, checked before committing each step.
/// These bound serialized evidence, not arbitrary allocations in model callbacks.
pub fn record_with_limits<M: ModelCodec>(
    model: &M,
    inputs: impl IntoIterator<Item = M::Input>,
    config: RunConfig,
    max_steps: usize,
    limits: &ReadLimits,
) -> Result<Trace, ModelError> {
    use crate::trace::{
        FILE_HEADER_SIZE, end_size, error_termination, reserved_end_size, run_size, step_size,
        validate_totals,
    };
    let format_error =
        |error: crate::trace::TraceError| ModelError::new(format!("recording limit: {error}"));
    let config = stamp_config(config)?;
    let mut state = model
        .initial_state()
        .map_err(|e| stage("initial state", e))?;
    let mut initial_checks = Vec::new();
    model
        .check_state_into(&state, &mut CheckSink::new(&mut initial_checks))
        .map_err(|e| stage("initial check", e))?;
    let mut initial_state = Vec::new();
    let blob_limit = limits
        .max_blob_bytes
        .min(u32::MAX as usize)
        .min(limits.max_frame_bytes)
        .min(usize::try_from(limits.max_total_bytes).unwrap_or(usize::MAX));
    let mut encoder = EncodeBuffer::new(&mut initial_state, blob_limit);
    model
        .encode_state_into(&state, &mut encoder)
        .map_err(|e| stage("encode initial state", e))?;
    encoder.finish()?;
    let mut trace = Trace {
        metadata: model.metadata(),
        config,
        initial_state,
        initial_checks,
        steps: Vec::new(),
        termination: Termination::Completed,
    };
    let run = run_size(
        &trace.metadata,
        &trace.config,
        &trace.initial_state,
        &trace.initial_checks,
        limits,
    )
    .map_err(format_error)?;
    let initial_failed = trace.initial_checks.iter().any(Check::is_failure);
    let terminal_end = end_size(0, &Termination::PropertyFailed, limits).map_err(format_error)?;
    let end = if initial_failed {
        terminal_end
    } else {
        reserved_end_size(limits).map_err(format_error)?
    };
    let mut bytes = FILE_HEADER_SIZE
        .checked_add(run.bytes)
        .ok_or_else(|| ModelError::new("recording byte count overflow"))?;
    let mut items = run.items;
    let initial_budget = bytes
        .checked_add(end.bytes)
        .ok_or_else(|| ModelError::new("recording byte count overflow"))?;
    validate_totals(initial_budget, items, 0, limits).map_err(format_error)?;
    if initial_failed {
        trace.termination = Termination::PropertyFailed;
        return Ok(trace);
    }
    for input in inputs {
        if trace.steps.len() >= max_steps.min(limits.max_steps) {
            trace.termination = Termination::StepLimit;
            break;
        }
        let prepared = (|| {
            // A failing candidate can consume the otherwise reserved future-error
            // space: it is a terminal artifact. Passing candidates must restore
            // the full error reserve at admission below.
            let frame_budget = usize::try_from(
                limits
                    .max_total_bytes
                    .saturating_sub(bytes)
                    .saturating_sub(terminal_end.bytes),
            )
            .unwrap_or(usize::MAX)
            .min(
                limits
                    .max_frame_bytes
                    .min(u32::MAX as usize)
                    .saturating_add(9),
            );
            let mut encoded_input = Vec::new();
            let mut encoder = EncodeBuffer::new(&mut encoded_input, blob_limit.min(frame_budget));
            model
                .encode_input_into(&input, &mut encoder)
                .map_err(|e| stage("encode input", e))?;
            encoder.finish()?;
            let transition = model
                .step(&state, &input)
                .map_err(|e| stage("transition", e))?;
            let checks = check_observed(
                model,
                &state,
                &input,
                &transition,
                trace.steps.len() as u64 + 1,
                CheckPolicy::default(),
            )?;
            if transition.outputs.len() > limits.max_outputs_per_step {
                return Err(ModelError::new("recording limit: outputs per step"));
            }
            let fixed_items = items
                .checked_add(checks.len())
                .and_then(|n| n.checked_add(1));
            if fixed_items
                .and_then(|n| n.checked_add(transition.outputs.len()))
                .is_none_or(|n| n > limits.max_items)
            {
                return Err(ModelError::new("recording limit: aggregate items"));
            }
            let mut step = TraceStep {
                input: encoded_input,
                disposition: transition.disposition,
                outputs: Vec::new(),
                post_state: Vec::new(),
                checks,
            };
            let base = step_size(&step, limits).map_err(format_error)?;
            let base_bytes = usize::try_from(base.bytes)
                .map_err(|_| ModelError::new("recording frame size exceeds platform size"))?;
            let mut remaining = frame_budget
                .checked_sub(base_bytes)
                .ok_or_else(|| ModelError::new("recording limit: remaining frame bytes"))?;
            for output in &transition.outputs {
                remaining = remaining
                    .checked_sub(4)
                    .ok_or_else(|| ModelError::new("recording limit: output frame bytes"))?;
                let mut encoded = Vec::new();
                let mut encoder = EncodeBuffer::new(&mut encoded, blob_limit.min(remaining));
                model
                    .encode_output_into(output, &mut encoder)
                    .map_err(|e| stage("encode output", e))?;
                encoder.finish()?;
                remaining -= encoded.len();
                step.outputs.push(encoded);
            }
            let mut encoder = EncodeBuffer::new(&mut step.post_state, blob_limit.min(remaining));
            model
                .encode_state_into(&transition.state, &mut encoder)
                .map_err(|e| stage("encode state", e))?;
            encoder.finish()?;
            let size = step_size(&step, limits).map_err(format_error)?;
            let next_bytes = bytes
                .checked_add(size.bytes)
                .ok_or_else(|| ModelError::new("recording byte count overflow"))?;
            let next_items = items
                .checked_add(size.items)
                .ok_or_else(|| ModelError::new("recording item count overflow"))?;
            let footer = if step.checks.iter().any(Check::is_failure) {
                terminal_end
            } else {
                end
            };
            let candidate_budget = next_bytes
                .checked_add(footer.bytes)
                .ok_or_else(|| ModelError::new("recording byte count overflow"))?;
            validate_totals(candidate_budget, next_items, trace.steps.len() + 1, limits)
                .map_err(format_error)?;
            Ok((transition.state, step, next_bytes, next_items))
        })();
        let (next_state, step, next_bytes, next_items) = match prepared {
            Ok(value) => value,
            Err(error) => {
                trace.termination = error_termination(&error.to_string(), limits);
                break;
            }
        };
        let failed = step.checks.iter().any(Check::is_failure);
        if trace.steps.try_reserve(1).is_err() {
            trace.termination = error_termination("unable to allocate trace step", limits);
            break;
        }
        trace.steps.push(step);
        bytes = next_bytes;
        items = next_items;
        state = next_state;
        if failed {
            trace.termination = Termination::PropertyFailed;
            break;
        }
    }
    Ok(trace)
}

#[derive(Clone, Copy, Debug, Default)]
pub struct ReplayOptions {
    /// Permits comparing a trace against another build of the same model and
    /// versions. The report still records that builds differed.
    pub allow_build_mismatch: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ReplayOutcome {
    /// All recorded observations matched. Consult trace termination separately:
    /// exact replay of a bounded prefix does not imply a complete execution.
    Exact,
    Diverged {
        step: Option<usize>,
        field: &'static str,
    },
    Incompatible {
        reason: String,
    },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReplayReport {
    pub outcome: ReplayOutcome,
    pub steps_verified: usize,
    /// A recorded failing check ID failed again at the corresponding step.
    /// Details may differ; this can be true even when exact replay diverged.
    pub failure_reproduced: bool,
    pub build_matches: bool,
}

/// Replay concrete inputs from the stored snapshot and compare all observations.
/// Sequence numbers in a divergence are one-based; None means initial state.
/// An incompatible identity is never silently treated as a valid replay.
pub fn replay<M: ModelCodec>(
    model: &M,
    trace: &Trace,
    options: ReplayOptions,
) -> Result<ReplayReport, ModelError> {
    replay_with_observer(model, trace, options, |_, _| {})
}

/// The observer runs after each attempted transition comparison, including a
/// divergent step. A CLI can pause here without altering model logical time.
pub fn replay_with_observer<M: ModelCodec>(
    model: &M,
    trace: &Trace,
    options: ReplayOptions,
    mut observer: impl FnMut(usize, &ReplayReport),
) -> Result<ReplayReport, ModelError> {
    let metadata = model.metadata();
    let mut report = ReplayReport {
        outcome: ReplayOutcome::Exact,
        steps_verified: 0,
        failure_reproduced: false,
        build_matches: metadata.build == trace.metadata.build,
    };
    if metadata.name != trace.metadata.name
        || metadata.model_version != trace.metadata.model_version
        || metadata.properties_version != trace.metadata.properties_version
        || metadata.codec_version != trace.metadata.codec_version
        || (!report.build_matches && !options.allow_build_mismatch)
    {
        report.outcome = ReplayOutcome::Incompatible {
            reason: "model, property, codec, or build identity differs".into(),
        };
        return Ok(report);
    }
    validate_recording(trace)?;
    let mut state = model
        .decode_state(&trace.initial_state)
        .map_err(|e| stage("decode initial state", e))?;
    let mut state_bytes = Vec::new();
    let mut encoder = EncodeBuffer::new(&mut state_bytes, usize::MAX);
    model.encode_state_into(&state, &mut encoder)?;
    encoder.finish()?;
    if state_bytes != trace.initial_state {
        return Err(ModelError::new("initial state encoding is not canonical"));
    }
    let mut checks = Vec::new();
    model
        .check_state_into(&state, &mut CheckSink::new(&mut checks))
        .map_err(|e| stage("initial check", e))?;
    report.failure_reproduced = same_failure(&trace.initial_checks, &checks);
    if checks != trace.initial_checks {
        report.outcome = ReplayOutcome::Diverged {
            step: None,
            field: "initial checks",
        };
        return Ok(report);
    }
    let mut input_bytes = Vec::new();
    let mut outputs: Vec<Vec<u8>> = Vec::new();
    for (index, expected) in trace.steps.iter().enumerate() {
        let input = model
            .decode_input(&expected.input)
            .map_err(|e| stage("decode input", e))?;
        let mut encoder = EncodeBuffer::new(&mut input_bytes, usize::MAX);
        model.encode_input_into(&input, &mut encoder)?;
        encoder.finish()?;
        if input_bytes != expected.input {
            return Err(ModelError::new(format!(
                "input {} encoding is not canonical",
                index + 1
            )));
        }
        let actual = model
            .step(&state, &input)
            .map_err(|e| stage("transition", e))?;
        check_observed_into(
            model,
            &state,
            &input,
            &actual,
            index as u64 + 1,
            CheckPolicy::default(),
            &mut checks,
        )?;
        report.failure_reproduced |= same_failure(&expected.checks, &checks);
        outputs.resize_with(actual.outputs.len(), Vec::new);
        for (output, bytes) in actual.outputs.iter().zip(&mut outputs) {
            let mut encoder = EncodeBuffer::new(bytes, usize::MAX);
            model
                .encode_output_into(output, &mut encoder)
                .map_err(|e| stage("encode output", e))?;
            encoder.finish()?;
        }
        let mut encoder = EncodeBuffer::new(&mut state_bytes, usize::MAX);
        model
            .encode_state_into(&actual.state, &mut encoder)
            .map_err(|e| stage("encode state", e))?;
        encoder.finish()?;
        let mismatch = if actual.disposition != expected.disposition {
            Some("disposition")
        } else if outputs != expected.outputs {
            Some("outputs")
        } else if state_bytes != expected.post_state {
            Some("state")
        } else if checks != expected.checks {
            Some("checks")
        } else {
            None
        };
        if let Some(field) = mismatch {
            report.outcome = ReplayOutcome::Diverged {
                step: Some(index + 1),
                field,
            };
            observer(index + 1, &report);
            return Ok(report);
        }
        report.steps_verified += 1;
        observer(index + 1, &report);
        state = actual.state;
    }
    Ok(report)
}

fn same_failure(expected: &[Check], actual: &[Check]) -> bool {
    expected
        .iter()
        .filter(|c| c.is_failure())
        .any(|e| actual.iter().any(|a| a.id == e.id && a.is_failure()))
}

fn validate_recording(trace: &Trace) -> Result<(), ModelError> {
    // The recorder stops on the first failure; later steps would be fabricated
    // continuation evidence and cannot be silently accepted by this replayer.
    let initial_failed = trace.initial_checks.iter().any(Check::is_failure);
    if initial_failed && !trace.steps.is_empty() {
        return Err(ModelError::new(
            "trace continues after initial property failure",
        ));
    }
    for step in trace.steps.iter().take(trace.steps.len().saturating_sub(1)) {
        if step.checks.iter().any(Check::is_failure) {
            return Err(ModelError::new("trace continues after property failure"));
        }
    }
    let failed = initial_failed
        || trace
            .steps
            .last()
            .is_some_and(|step| step.checks.iter().any(Check::is_failure));
    if failed != matches!(trace.termination, Termination::PropertyFailed) {
        return Err(ModelError::new(
            "trace termination disagrees with recorded checks",
        ));
    }
    Ok(())
}

fn stage(stage: &str, error: ModelError) -> ModelError {
    ModelError::new(format!("{stage}: {error}"))
}

pub(crate) fn stamp_config(mut config: RunConfig) -> Result<RunConfig, ModelError> {
    if config
        .parameters
        .iter()
        .any(|(key, _)| key.starts_with("stateless.engine."))
    {
        return Err(ModelError::new("stateless.engine. parameters are reserved"));
    }
    config.parameters.push((
        "stateless.engine.version".into(),
        env!("CARGO_PKG_VERSION").into(),
    ));
    config.parameters.push((
        "stateless.engine.build".into(),
        env!("STATELESS_BUILD_ID").into(),
    ));
    Ok(config)
}

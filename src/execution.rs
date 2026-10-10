//! Checking, exact recording, and replay. No real effects are executed here.

use crate::model::*;
use crate::observation::{CheckedInitial, CheckedTurn, TurnObservation};
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

/// Check a supplied initial state or coherent checkpoint exactly once. The
/// sealed result can initialize an exact recorder without checking it again.
pub fn check_initial<'a, M: Model>(
    model: &'a M,
    state: &'a M::State,
) -> Result<CheckedInitial<'a, M>, ModelError> {
    let mut checks = Vec::new();
    model
        .check_state_into(state, &mut CheckSink::new(&mut checks))
        .map_err(|e| stage("initial check", e))?;
    Ok(CheckedInitial {
        model,
        state,
        checks,
    })
}

/// Check an already executed turn once and bind its complete result to the
/// borrowed transition. A checker error leaves the caller's actual transition
/// intact and never manufactures a token from a partial batch.
pub fn check_turn<'a, M: Model>(
    model: &'a M,
    before: &'a M::State,
    input: &'a M::Input,
    transition: &'a Transition<M::State, M::Output>,
    sequence: u64,
    policy: CheckPolicy,
) -> Result<CheckedTurn<'a, M>, ModelError> {
    let mut checks = Vec::new();
    let state_check_count = check_observed_count_into(
        model,
        before,
        input,
        transition,
        sequence,
        policy,
        &mut checks,
    )?;
    Ok(CheckedTurn {
        model,
        before,
        input,
        transition,
        sequence,
        checks,
        state_check_count,
        full_checking: policy.state_every.get() == 1 && policy.transition_checks,
    })
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
    check_observed_count_into(model, before, input, transition, sequence, policy, checks)
        .map(|_| ())
}

pub(crate) fn check_observed_count_into<M: Model>(
    model: &M,
    before: &M::State,
    input: &M::Input,
    transition: &Transition<M::State, M::Output>,
    sequence: u64,
    policy: CheckPolicy,
    checks: &mut Vec<Check>,
) -> Result<usize, ModelError> {
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
        let state_check_count = checks.len();
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
        Ok(state_check_count)
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
    replay_with_observations(model, trace, options, |observation, report| {
        if let ReplayObservation::Turn(turn) = observation {
            observer(turn.actual.sequence as usize, report);
        }
        Ok(())
    })
    .map_err(|error| error.error)
}

/// All available differences at the first mismatching boundary. The primary
/// ReplayOutcome field still follows disposition, outputs, state, checks order.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ReplayDifferences {
    pub disposition: bool,
    pub outputs: bool,
    pub state: bool,
    pub checks: bool,
}

impl ReplayDifferences {
    pub fn is_empty(self) -> bool {
        !(self.disposition || self.outputs || self.state || self.checks)
    }
    pub fn primary(self) -> Option<&'static str> {
        if self.disposition {
            Some("disposition")
        } else if self.outputs {
            Some("outputs")
        } else if self.state {
            Some("state")
        } else if self.checks {
            Some("checks")
        } else {
            None
        }
    }
}

pub struct ReplayInitialObservation<'a, M: Model> {
    pub state: &'a M::State,
    pub checks: &'a [Check],
    pub expected_state: &'a [u8],
    pub actual_state: &'a [u8],
    pub expected_checks: &'a [Check],
    pub differences: ReplayDifferences,
}

pub struct ReplayTurnObservation<'a, M: Model> {
    pub actual: TurnObservation<'a, M>,
    pub expected: &'a TraceStep,
    pub actual_input: &'a [u8],
    pub actual_outputs: &'a [Vec<u8>],
    pub actual_state: &'a [u8],
    pub differences: ReplayDifferences,
}

/// Synchronous borrowed replay observations. Stored outputs remain encoded;
/// actual typed outputs are explicitly the result of this replay execution.
/// Error observations never assert a complete check batch unless one exists.
pub enum ReplayObservation<'a, M: Model> {
    Initial(ReplayInitialObservation<'a, M>),
    Turn(ReplayTurnObservation<'a, M>),
    Error {
        sequence: u64,
        actual: Option<&'a ReplayActual<M>>,
        error: &'a ModelError,
    },
}

/// Owned recovery evidence on an incomplete replay. No transition is repeated
/// to obtain this value. None checks means checking never completed, not passed.
pub enum ReplayActual<M: Model> {
    Initial {
        state: M::State,
        checks: Option<Vec<Check>>,
    },
    Turn {
        sequence: u64,
        before: M::State,
        input: M::Input,
        transition: Transition<M::State, M::Output>,
        checks: Option<Vec<Check>>,
        state_check_count: Option<usize>,
    },
}

/// A replay error retains the actual result when execution already produced it.
/// An error-reporting observer's additional failure cannot mask the engine error.
pub struct ReplayError<M: Model> {
    pub error: ModelError,
    pub observer_error: Option<ModelError>,
    pub report: ReplayReport,
    pub actual: Option<Box<ReplayActual<M>>>,
}

impl<M: Model> std::fmt::Debug for ReplayError<M> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ReplayError")
            .field("error", &self.error)
            .field("observer_error", &self.observer_error)
            .field("report", &self.report)
            .field("has_actual", &self.actual.is_some())
            .finish()
    }
}
impl<M: Model> std::fmt::Display for ReplayError<M> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.error.fmt(f)
    }
}
impl<M: Model> std::error::Error for ReplayError<M> {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(&self.error)
    }
}

/// Rich replay with sequence-zero, typed actual values, every encoded
/// difference, and retained error results. Observations borrow engine storage
/// and must be consumed before the callback returns. No Send/Sync/Debug bounds
/// or historical output decoder are required.
///
/// Stops at the same first divergence as replay_with_observer. The callback
/// sees the updated report after comparison; an observer error stops replay and
/// retains that completed actual result without executing the next input.
pub fn replay_with_observations<M: ModelCodec>(
    model: &M,
    trace: &Trace,
    options: ReplayOptions,
    observer: impl FnMut(ReplayObservation<'_, M>, &ReplayReport) -> Result<(), ModelError>,
) -> Result<ReplayReport, ReplayError<M>> {
    replay_observations_impl(model, trace, options, None, observer)
}

/// Rich replay with finite format and allocation-work bounds for debugger use.
/// The stored artifact is validated before decoding. Actual encoded observations
/// share per-frame and aggregate byte/item budgets, including a divergent turn.
/// Application reducers, checkers, decoders and legacy allocating codecs remain
/// cooperative; use incremental codec methods to bound their encoding buffers.
/// A callback may stop at initial/turn boundaries for cancellation or deadlines.
/// Existing unbounded replay APIs retain their original compatibility behavior.
pub fn replay_with_observations_bounded<M: ModelCodec>(
    model: &M,
    trace: &Trace,
    options: ReplayOptions,
    limits: &ReadLimits,
    observer: impl FnMut(ReplayObservation<'_, M>, &ReplayReport) -> Result<(), ModelError>,
) -> Result<ReplayReport, ReplayError<M>> {
    replay_observations_impl(model, trace, options, Some(limits), observer)
}

fn replay_observations_impl<M: ModelCodec>(
    model: &M,
    trace: &Trace,
    options: ReplayOptions,
    limits: Option<&ReadLimits>,
    mut observer: impl FnMut(ReplayObservation<'_, M>, &ReplayReport) -> Result<(), ModelError>,
) -> Result<ReplayReport, ReplayError<M>> {
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
    if let Some(limits) = limits
        && let Err(error) = trace.write_with_limits(std::io::sink(), limits)
    {
        return Err(replay_error(
            stage(
                "bounded replay artifact",
                ModelError::new(error.to_string()),
            ),
            report,
            None,
            0,
            &mut observer,
        ));
    }
    if let Err(error) = validate_recording(trace) {
        return Err(replay_error(error, report, None, 0, &mut observer));
    }
    let blob_limit = limits.map_or(usize::MAX, replay_blob_limit);
    let mut bounded_bytes = 0u64;
    let mut bounded_items = 0usize;
    let bounded_footer = crate::trace::FRAME_OVERHEAD + 9;
    let mut state = match model.decode_state(&trace.initial_state) {
        Ok(state) => state,
        Err(error) => {
            return Err(replay_error(
                stage("decode initial state", error),
                report,
                None,
                0,
                &mut observer,
            ));
        }
    };
    let mut state_bytes = Vec::new();
    let mut checks = Vec::new();
    let mut initial_complete = false;
    let initial_result = (|| {
        let mut encoder = EncodeBuffer::new(&mut state_bytes, blob_limit);
        model.encode_state_into(&state, &mut encoder)?;
        encoder.finish()?;
        if state_bytes != trace.initial_state {
            return Err(ModelError::new("initial state encoding is not canonical"));
        }
        model
            .check_state_into(&state, &mut CheckSink::new(&mut checks))
            .map_err(|e| stage("initial check", e))?;
        initial_complete = true;
        if let Some(limits) = limits {
            let size =
                crate::trace::run_size(&metadata, &trace.config, &state_bytes, &checks, limits)
                    .map_err(|e| ModelError::new(format!("bounded replay initial: {e}")))?;
            bounded_bytes = crate::trace::FILE_HEADER_SIZE
                .checked_add(size.bytes)
                .ok_or_else(|| ModelError::new("bounded replay byte count overflow"))?;
            if bounded_bytes
                .checked_add(bounded_footer)
                .is_none_or(|n| n > limits.max_total_bytes)
            {
                return Err(ModelError::new("bounded replay limit: total bytes"));
            }
            bounded_items = size.items;
        }
        report.failure_reproduced = same_failure(&trace.initial_checks, &checks);
        if checks != trace.initial_checks {
            report.outcome = ReplayOutcome::Diverged {
                step: None,
                field: "initial checks",
            };
        }
        Ok(())
    })();
    if let Err(error) = initial_result {
        let actual = ReplayActual::Initial {
            state,
            checks: initial_complete.then_some(checks),
        };
        return Err(replay_error(error, report, Some(actual), 0, &mut observer));
    }
    let initial = ReplayInitialObservation {
        state: &state,
        checks: &checks,
        expected_state: &trace.initial_state,
        actual_state: &state_bytes,
        expected_checks: &trace.initial_checks,
        differences: ReplayDifferences {
            checks: checks != trace.initial_checks,
            ..ReplayDifferences::default()
        },
    };
    if let Err(error) = observer(ReplayObservation::Initial(initial), &report) {
        return Err(ReplayError {
            error: stage("replay observer", error),
            observer_error: None,
            report,
            actual: Some(Box::new(ReplayActual::Initial {
                state,
                checks: Some(checks),
            })),
        });
    }
    if !matches!(report.outcome, ReplayOutcome::Exact) {
        return Ok(report);
    }
    let mut input_bytes = Vec::new();
    let mut outputs: Vec<Vec<u8>> = Vec::new();
    for (index, expected) in trace.steps.iter().enumerate() {
        let sequence = index as u64 + 1;
        let prepared = (|| {
            let input = model
                .decode_input(&expected.input)
                .map_err(|e| stage("decode input", e))?;
            let mut encoder = EncodeBuffer::new(&mut input_bytes, blob_limit);
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
            Ok((input, actual))
        })();
        let (input, actual) = match prepared {
            Ok(prepared) => prepared,
            Err(error) => return Err(replay_error(error, report, None, sequence, &mut observer)),
        };
        let mut state_check_count = None;
        let compared = (|| {
            state_check_count = Some(check_observed_count_into(
                model,
                &state,
                &input,
                &actual,
                sequence,
                CheckPolicy::default(),
                &mut checks,
            )?);
            let mut remaining = if let Some(limits) = limits {
                let (fixed, items) = replay_step_preflight(
                    input_bytes.len(),
                    &actual.disposition,
                    actual.outputs.len(),
                    &checks,
                    limits,
                )?;
                bounded_items = bounded_items
                    .checked_add(items)
                    .filter(|n| *n <= limits.max_items)
                    .ok_or_else(|| ModelError::new("bounded replay limit: aggregate items"))?;
                let total_remaining = limits
                    .max_total_bytes
                    .checked_sub(bounded_bytes)
                    .and_then(|n| n.checked_sub(bounded_footer))
                    .and_then(|n| n.checked_sub(crate::trace::FRAME_OVERHEAD))
                    .ok_or_else(|| ModelError::new("bounded replay limit: total bytes"))?;
                let frame = limits
                    .max_frame_bytes
                    .min(u32::MAX as usize)
                    .min(usize::try_from(total_remaining).unwrap_or(usize::MAX));
                let remaining = frame
                    .checked_sub(fixed)
                    .ok_or_else(|| ModelError::new("bounded replay limit: frame bytes"))?;
                bounded_bytes = bounded_bytes
                    .checked_add(fixed as u64)
                    .and_then(|n| n.checked_add(crate::trace::FRAME_OVERHEAD))
                    .ok_or_else(|| ModelError::new("bounded replay byte count overflow"))?;
                Some(remaining)
            } else {
                None
            };
            report.failure_reproduced |= same_failure(&expected.checks, &checks);
            if limits.is_some() && actual.outputs.len() > outputs.len() {
                outputs
                    .try_reserve_exact(actual.outputs.len() - outputs.len())
                    .map_err(|_| {
                        ModelError::new("bounded replay output storage allocation failed")
                    })?;
            }
            outputs.resize_with(actual.outputs.len(), Vec::new);
            for (output, bytes) in actual.outputs.iter().zip(&mut outputs) {
                let mut encoder =
                    EncodeBuffer::new(bytes, blob_limit.min(remaining.unwrap_or(usize::MAX)));
                model
                    .encode_output_into(output, &mut encoder)
                    .map_err(|e| stage("encode output", e))?;
                encoder.finish()?;
                if let Some(remaining) = &mut remaining {
                    *remaining -= bytes.len();
                    bounded_bytes += bytes.len() as u64;
                }
            }
            let mut encoder = EncodeBuffer::new(
                &mut state_bytes,
                blob_limit.min(remaining.unwrap_or(usize::MAX)),
            );
            model
                .encode_state_into(&actual.state, &mut encoder)
                .map_err(|e| stage("encode state", e))?;
            encoder.finish()?;
            if limits.is_some() {
                bounded_bytes += state_bytes.len() as u64;
            }
            Ok(ReplayDifferences {
                disposition: actual.disposition != expected.disposition,
                outputs: outputs != expected.outputs,
                state: state_bytes != expected.post_state,
                checks: checks != expected.checks,
            })
        })();
        let differences = match compared {
            Ok(differences) => differences,
            Err(error) => {
                let actual = ReplayActual::Turn {
                    sequence,
                    before: state,
                    input,
                    transition: actual,
                    checks: state_check_count.map(|_| checks),
                    state_check_count,
                };
                return Err(replay_error(
                    error,
                    report,
                    Some(actual),
                    sequence,
                    &mut observer,
                ));
            }
        };
        if let Some(field) = differences.primary() {
            report.outcome = ReplayOutcome::Diverged {
                step: Some(index + 1),
                field,
            };
        } else {
            report.steps_verified += 1;
        }
        let observation = ReplayTurnObservation {
            actual: TurnObservation {
                sequence,
                before: &state,
                input: &input,
                transition: actual.as_ref(),
                checks: &checks,
                state_check_count: state_check_count.expect("completed checks"),
            },
            expected,
            actual_input: &input_bytes,
            actual_outputs: &outputs,
            actual_state: &state_bytes,
            differences,
        };
        if let Err(error) = observer(ReplayObservation::Turn(observation), &report) {
            return Err(ReplayError {
                error: stage("replay observer", error),
                observer_error: None,
                report,
                actual: Some(Box::new(ReplayActual::Turn {
                    sequence,
                    before: state,
                    input,
                    transition: actual,
                    checks: Some(checks),
                    state_check_count,
                })),
            });
        }
        if !differences.is_empty() {
            return Ok(report);
        }
        state = actual.state;
    }
    Ok(report)
}

fn replay_blob_limit(limits: &ReadLimits) -> usize {
    limits
        .max_blob_bytes
        .min(u32::MAX as usize)
        .min(limits.max_frame_bytes)
        .min(usize::try_from(limits.max_total_bytes).unwrap_or(usize::MAX))
}

/// Fixed serialized step payload before output/state bodies. Borrowed checks
/// and reason strings are validated before allocating encoded-output storage.
fn replay_step_preflight(
    input_bytes: usize,
    disposition: &Disposition,
    outputs: usize,
    checks: &[Check],
    limits: &ReadLimits,
) -> Result<(usize, usize), ModelError> {
    let error = |name| ModelError::new(format!("bounded replay limit: {name}"));
    if outputs > limits.max_outputs_per_step.min(u32::MAX as usize) {
        return Err(error("outputs per step"));
    }
    let batch = crate::trace::checked_batch_size(checks, limits)
        .map_err(|e| ModelError::new(format!("bounded replay checks: {e}")))?;
    let items = batch
        .items
        .checked_add(outputs)
        .and_then(|n| n.checked_add(1))
        .filter(|n| *n <= limits.max_items)
        .ok_or_else(|| error("aggregate items"))?;
    let reason = match disposition {
        Disposition::Accepted => 0,
        Disposition::Ignored(reason) | Disposition::Rejected(reason) => {
            if reason.len() > limits.max_string_bytes.min(u32::MAX as usize) {
                return Err(error("string bytes"));
            }
            reason
                .len()
                .checked_add(4)
                .ok_or_else(|| error("frame bytes"))?
        }
    };
    // sequence, input length, disposition tag, output count, state length,
    // per-output lengths, and the complete check batch (including its count).
    let fixed = 8usize + 4 + 1 + 4 + 4;
    let fixed = fixed
        .checked_add(input_bytes)
        .and_then(|n| n.checked_add(reason))
        .and_then(|n| outputs.checked_mul(4).and_then(|v| n.checked_add(v)))
        .and_then(|n| {
            usize::try_from(batch.bytes)
                .ok()
                .and_then(|v| n.checked_add(v))
        })
        .filter(|n| *n <= limits.max_frame_bytes.min(u32::MAX as usize))
        .ok_or_else(|| error("frame bytes"))?;
    Ok((fixed, items))
}

fn replay_error<M: Model>(
    error: ModelError,
    report: ReplayReport,
    actual: Option<ReplayActual<M>>,
    sequence: u64,
    observer: &mut impl FnMut(ReplayObservation<'_, M>, &ReplayReport) -> Result<(), ModelError>,
) -> ReplayError<M> {
    let observer_error = observer(
        ReplayObservation::Error {
            sequence,
            actual: actual.as_ref(),
            error: &error,
        },
        &report,
    )
    .err();
    ReplayError {
        error,
        observer_error,
        report,
        actual: actual.map(Box::new),
    }
}

fn same_failure(expected: &[Check], actual: &[Check]) -> bool {
    expected
        .iter()
        .filter(|c| c.is_failure())
        .any(|e| actual.iter().any(|a| a.id == e.id && a.is_failure()))
}

/// Validate first-failure and termination semantics without executing a model.
/// Format/allocation limits remain the trace reader or writer's responsibility.
pub fn validate_recording(trace: &Trace) -> Result<(), ModelError> {
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

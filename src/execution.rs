//! Checking, exact recording, and replay. No real effects are executed here.

use crate::model::*;
use crate::observation::{CheckedInitial, CheckedTurn, TurnObservation};
use crate::operations::{CodecOps, ModelOps, NativeOps};
use crate::trace::{ReadLimits, RunConfig, Termination, Trace, TraceStep};
use std::num::NonZeroU64;

mod replay_kernel;

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
    check_observed_count_ops_into(
        &NativeOps::new(model),
        before,
        input,
        transition,
        sequence,
        policy,
        checks,
    )
}

/// Shared checking path for already executed native or foreign transitions.
/// Value identity and persistence are deliberately not required. On callback
/// error no partial batch survives, and the supplied transition is never changed.
pub(crate) fn check_observed_count_ops_into<M: ModelOps>(
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
    record_ops(
        &NativeOps::new(model),
        InfallibleInputs(inputs),
        config,
        max_steps,
        limits,
    )
}

/// Keep iterator construction after the initial checkpoint, as in the native
/// recorder. In particular, an initial property failure never opens the input.
pub(crate) struct InfallibleInputs<I>(pub(crate) I);

impl<I: IntoIterator> IntoIterator for InfallibleInputs<I> {
    type Item = Result<I::Item, ModelError>;
    type IntoIter = std::iter::Map<I::IntoIter, fn(I::Item) -> Result<I::Item, ModelError>>;

    fn into_iter(self) -> Self::IntoIter {
        self.0.into_iter().map(Ok)
    }
}

/// Shared recorder. An input-stream error preserves only the already coherent
/// prefix and is never interpreted as successful exhaustion.
pub(crate) fn record_ops<M: CodecOps>(
    model: &M,
    inputs: impl IntoIterator<Item = Result<M::Input, ModelError>>,
    config: RunConfig,
    max_steps: usize,
    limits: &ReadLimits,
) -> Result<Trace, ModelError> {
    record_ops_with_payload_limit(model, inputs, config, max_steps, limits, None, None)
}

/// The same exact recorder with an optional portable payload budget. Payload
/// means retained state/input/output bytes, independently of binary framing,
/// metadata, property text, and the existing encoded-artifact limits. Portable
/// items count checks, outputs, and steps independently of metadata parameters.
pub(crate) fn record_ops_with_payload_limit<M: CodecOps>(
    model: &M,
    inputs: impl IntoIterator<Item = Result<M::Input, ModelError>>,
    config: RunConfig,
    max_steps: usize,
    limits: &ReadLimits,
    max_payload_bytes: Option<u64>,
    max_payload_items: Option<usize>,
) -> Result<Trace, ModelError> {
    record_kernel(
        model,
        inputs,
        config,
        max_steps,
        limits,
        max_payload_bytes,
        max_payload_items,
        false,
    )
}

/// Record with continued-failure-evidence-v1 semantics: failed checks do not
/// end the trace. See [`crate::continued::record_continued`].
pub(crate) fn record_continued_ops<M: CodecOps>(
    model: &M,
    inputs: impl IntoIterator<Item = Result<M::Input, ModelError>>,
    config: RunConfig,
    max_steps: usize,
    limits: &ReadLimits,
) -> Result<Trace, ModelError> {
    record_kernel(model, inputs, config, max_steps, limits, None, None, true)
}

/// `continued` records past property failures (continued-failure-evidence-v1);
/// otherwise the first failing batch ends the trace with property_failed.
#[allow(clippy::too_many_arguments)]
fn record_kernel<M: CodecOps>(
    model: &M,
    inputs: impl IntoIterator<Item = Result<M::Input, ModelError>>,
    config: RunConfig,
    max_steps: usize,
    limits: &ReadLimits,
    max_payload_bytes: Option<u64>,
    max_payload_items: Option<usize>,
    continued: bool,
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
    let mut payload_bytes = initial_state.len() as u64;
    let mut payload_items = initial_checks.len();
    if max_payload_bytes.is_some_and(|maximum| payload_bytes > maximum)
        || max_payload_items.is_some_and(|maximum| payload_items > maximum)
    {
        return Err(ModelError::new("recording limit: initial checkpoint"));
    }
    let mut trace = Trace {
        metadata: model.metadata().map_err(|e| stage("metadata", e))?,
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
    // A continued recording never ends on a failure, so a failing batch keeps
    // the full future-error reserve like any passing one.
    let initial_failed = !continued && trace.initial_checks.iter().any(Check::is_failure);
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
        let input = match input {
            Ok(input) => input,
            Err(error) => {
                trace.termination =
                    error_termination(&stage("record input", error).to_string(), limits);
                break;
            }
        };
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
            let mut checks = Vec::new();
            check_observed_count_ops_into(
                model,
                &state,
                &input,
                &transition,
                trace.steps.len() as u64 + 1,
                CheckPolicy::default(),
                &mut checks,
            )?;
            if transition.outputs.len() > limits.max_outputs_per_step {
                return Err(ModelError::new("recording limit: outputs per step"));
            }
            let next_payload_items = payload_items
                .checked_add(1)
                .and_then(|count| count.checked_add(checks.len()))
                .and_then(|count| count.checked_add(transition.outputs.len()))
                .ok_or_else(|| ModelError::new("recording payload item count overflow"))?;
            if max_payload_items.is_some_and(|maximum| next_payload_items > maximum) {
                return Err(ModelError::new("recording limit: aggregate items"));
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
            let next_payload_bytes = step.outputs.iter().try_fold(
                payload_bytes
                    .checked_add(step.input.len() as u64)
                    .and_then(|bytes| bytes.checked_add(step.post_state.len() as u64))
                    .ok_or_else(|| ModelError::new("recording payload byte count overflow"))?,
                |bytes, output| {
                    bytes
                        .checked_add(output.len() as u64)
                        .ok_or_else(|| ModelError::new("recording payload byte count overflow"))
                },
            )?;
            if max_payload_bytes.is_some_and(|maximum| next_payload_bytes > maximum) {
                return Err(ModelError::new("recording limit: aggregate payload bytes"));
            }
            let size = step_size(&step, limits).map_err(format_error)?;
            let next_bytes = bytes
                .checked_add(size.bytes)
                .ok_or_else(|| ModelError::new("recording byte count overflow"))?;
            let next_items = items
                .checked_add(size.items)
                .ok_or_else(|| ModelError::new("recording item count overflow"))?;
            let footer = if !continued && step.checks.iter().any(Check::is_failure) {
                terminal_end
            } else {
                end
            };
            let candidate_budget = next_bytes
                .checked_add(footer.bytes)
                .ok_or_else(|| ModelError::new("recording byte count overflow"))?;
            validate_totals(candidate_budget, next_items, trace.steps.len() + 1, limits)
                .map_err(format_error)?;
            Ok((
                transition.state,
                step,
                next_bytes,
                next_items,
                next_payload_bytes,
                next_payload_items,
            ))
        })();
        let (next_state, step, next_bytes, next_items, next_payload_bytes, next_payload_items) =
            match prepared {
                Ok(value) => value,
                Err(error) => {
                    trace.termination = error_termination(&error.to_string(), limits);
                    break;
                }
            };
        let failed = !continued && step.checks.iter().any(Check::is_failure);
        if trace.steps.try_reserve(1).is_err() {
            trace.termination = error_termination("unable to allocate trace step", limits);
            break;
        }
        trace.steps.push(step);
        bytes = next_bytes;
        items = next_items;
        payload_bytes = next_payload_bytes;
        payload_items = next_payload_items;
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
    observer: impl FnMut(ReplayObservation<'_, M>, &ReplayReport) -> Result<(), ModelError>,
) -> Result<ReplayReport, ReplayError<M>> {
    replay_kernel::native(model, trace, options, limits, validate_recording, observer)
}

/// Replay with a caller-selected recording validator (continued traces).
pub(crate) fn replay_observations_validated<M: ModelCodec>(
    model: &M,
    trace: &Trace,
    options: ReplayOptions,
    validate: fn(&Trace) -> Result<(), ModelError>,
    observer: impl FnMut(ReplayObservation<'_, M>, &ReplayReport) -> Result<(), ModelError>,
) -> Result<ReplayReport, ReplayError<M>> {
    replay_kernel::native(model, trace, options, None, validate, observer)
}

pub(crate) fn replay_ops<M: CodecOps>(
    model: &M,
    trace: &Trace,
    options: ReplayOptions,
    limits: &ReadLimits,
) -> Result<ReplayReport, ModelError> {
    replay_kernel::run(model, trace, options, limits)
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

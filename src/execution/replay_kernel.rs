//! Shared replay with value-based views and sink-owned recovery evidence.
//! Public Rust observers retain their existing concrete types; foreign values
//! never implement the public Model trait merely to enter the replayer.

use super::*;
use std::marker::PhantomData;

struct InitialView<'a, M: CodecOps> {
    state: &'a M::State,
    checks: &'a [Check],
    expected_state: &'a [u8],
    actual_state: &'a [u8],
    expected_checks: &'a [Check],
    differences: ReplayDifferences,
}

struct TurnValues<'a, M: CodecOps> {
    sequence: u64,
    before: &'a M::State,
    input: &'a M::Input,
    transition: TransitionRef<'a, M::State, M::Output>,
    checks: &'a [Check],
    state_check_count: usize,
}

struct TurnView<'a, M: CodecOps> {
    actual: TurnValues<'a, M>,
    expected: &'a TraceStep,
    actual_input: &'a [u8],
    actual_outputs: &'a [Vec<u8>],
    actual_state: &'a [u8],
    differences: ReplayDifferences,
}

struct OwnedTurn<M: CodecOps> {
    sequence: u64,
    before: M::State,
    input: M::Input,
    transition: Transition<M::State, M::Output>,
    checks: Option<Vec<Check>>,
    state_check_count: Option<usize>,
}

trait ReplaySink<M: CodecOps> {
    type Actual;
    fn initial_actual(state: M::State, checks: Option<Vec<Check>>) -> Self::Actual;
    fn turn_actual(turn: OwnedTurn<M>) -> Self::Actual;
    fn initial(
        &mut self,
        value: InitialView<'_, M>,
        report: &ReplayReport,
    ) -> Result<(), ModelError>;
    fn turn(&mut self, value: TurnView<'_, M>, report: &ReplayReport) -> Result<(), ModelError>;
    fn error(
        &mut self,
        sequence: u64,
        actual: Option<&Self::Actual>,
        error: &ModelError,
        report: &ReplayReport,
    ) -> Result<(), ModelError>;
}

struct OpsReplayError<A> {
    error: ModelError,
    observer_error: Option<ModelError>,
    report: ReplayReport,
    actual: Option<Box<A>>,
}

struct NativeSink<M, F> {
    callback: F,
    marker: PhantomData<fn() -> M>,
}

impl<'m, M: ModelCodec, F> ReplaySink<NativeOps<'m, M>> for NativeSink<M, F>
where
    F: FnMut(ReplayObservation<'_, M>, &ReplayReport) -> Result<(), ModelError>,
{
    type Actual = ReplayActual<M>;
    fn initial_actual(state: M::State, checks: Option<Vec<Check>>) -> Self::Actual {
        ReplayActual::Initial { state, checks }
    }
    fn turn_actual(turn: OwnedTurn<NativeOps<'m, M>>) -> Self::Actual {
        ReplayActual::Turn {
            sequence: turn.sequence,
            before: turn.before,
            input: turn.input,
            transition: turn.transition,
            checks: turn.checks,
            state_check_count: turn.state_check_count,
        }
    }
    fn initial(
        &mut self,
        value: InitialView<'_, NativeOps<'m, M>>,
        report: &ReplayReport,
    ) -> Result<(), ModelError> {
        (self.callback)(
            ReplayObservation::Initial(ReplayInitialObservation {
                state: value.state,
                checks: value.checks,
                expected_state: value.expected_state,
                actual_state: value.actual_state,
                expected_checks: value.expected_checks,
                differences: value.differences,
            }),
            report,
        )
    }
    fn turn(
        &mut self,
        value: TurnView<'_, NativeOps<'m, M>>,
        report: &ReplayReport,
    ) -> Result<(), ModelError> {
        (self.callback)(
            ReplayObservation::Turn(ReplayTurnObservation {
                actual: TurnObservation {
                    sequence: value.actual.sequence,
                    before: value.actual.before,
                    input: value.actual.input,
                    transition: value.actual.transition,
                    checks: value.actual.checks,
                    state_check_count: value.actual.state_check_count,
                },
                expected: value.expected,
                actual_input: value.actual_input,
                actual_outputs: value.actual_outputs,
                actual_state: value.actual_state,
                differences: value.differences,
            }),
            report,
        )
    }
    fn error(
        &mut self,
        sequence: u64,
        actual: Option<&Self::Actual>,
        error: &ModelError,
        report: &ReplayReport,
    ) -> Result<(), ModelError> {
        (self.callback)(
            ReplayObservation::Error {
                sequence,
                actual,
                error,
            },
            report,
        )
    }
}

struct Unobserved;
impl<M: CodecOps> ReplaySink<M> for Unobserved {
    type Actual = ();
    fn initial_actual(_state: M::State, _checks: Option<Vec<Check>>) {}
    fn turn_actual(_turn: OwnedTurn<M>) {}
    fn initial(
        &mut self,
        _value: InitialView<'_, M>,
        _report: &ReplayReport,
    ) -> Result<(), ModelError> {
        Ok(())
    }
    fn turn(&mut self, _value: TurnView<'_, M>, _report: &ReplayReport) -> Result<(), ModelError> {
        Ok(())
    }
    fn error(
        &mut self,
        _sequence: u64,
        _actual: Option<&()>,
        _error: &ModelError,
        _report: &ReplayReport,
    ) -> Result<(), ModelError> {
        Ok(())
    }
}

pub(super) fn native<M: ModelCodec>(
    model: &M,
    trace: &Trace,
    options: ReplayOptions,
    limits: Option<&ReadLimits>,
    observer: impl FnMut(ReplayObservation<'_, M>, &ReplayReport) -> Result<(), ModelError>,
) -> Result<ReplayReport, ReplayError<M>> {
    core(
        &NativeOps::new(model),
        trace,
        options,
        limits,
        NativeSink {
            callback: observer,
            marker: PhantomData::<fn() -> M>,
        },
    )
    .map_err(|error| ReplayError {
        error: error.error,
        observer_error: error.observer_error,
        report: error.report,
        actual: error.actual,
    })
}

pub(super) fn run<M: CodecOps>(
    model: &M,
    trace: &Trace,
    options: ReplayOptions,
    limits: &ReadLimits,
) -> Result<ReplayReport, ModelError> {
    core(model, trace, options, Some(limits), Unobserved).map_err(|error| error.error)
}

fn core<M: CodecOps, S: ReplaySink<M>>(
    model: &M,
    trace: &Trace,
    options: ReplayOptions,
    limits: Option<&ReadLimits>,
    mut observer: S,
) -> Result<ReplayReport, OpsReplayError<S::Actual>> {
    let mut report = ReplayReport {
        outcome: ReplayOutcome::Exact,
        steps_verified: 0,
        failure_reproduced: false,
        build_matches: false,
    };
    let metadata = match model.metadata() {
        Ok(metadata) => metadata,
        Err(error) => {
            return Err(replay_error_ops::<M, S>(
                stage("metadata", error),
                report,
                None,
                0,
                &mut observer,
            ));
        }
    };
    report.build_matches = metadata.build == trace.metadata.build;
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
        return Err(replay_error_ops::<M, S>(
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
        return Err(replay_error_ops::<M, S>(
            error,
            report,
            None,
            0,
            &mut observer,
        ));
    }
    let blob_limit = limits.map_or(usize::MAX, replay_blob_limit);
    let mut bounded_bytes = 0u64;
    let mut bounded_items = 0usize;
    let bounded_footer = crate::trace::FRAME_OVERHEAD + 9;
    let mut state = match model.decode_state(&trace.initial_state) {
        Ok(state) => state,
        Err(error) => {
            return Err(replay_error_ops::<M, S>(
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
        let actual = S::initial_actual(state, initial_complete.then_some(checks));
        return Err(replay_error_ops::<M, S>(
            error,
            report,
            Some(actual),
            0,
            &mut observer,
        ));
    }
    let initial = InitialView::<M> {
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
    if let Err(error) = observer.initial(initial, &report) {
        return Err(OpsReplayError {
            error: stage("replay observer", error),
            observer_error: None,
            report,
            actual: Some(Box::new(S::initial_actual(state, Some(checks)))),
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
            Err(error) => {
                return Err(replay_error_ops::<M, S>(
                    error,
                    report,
                    None,
                    sequence,
                    &mut observer,
                ));
            }
        };
        let mut state_check_count = None;
        let compared = (|| {
            state_check_count = Some(check_observed_count_ops_into(
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
                let actual = S::turn_actual(OwnedTurn::<M> {
                    sequence,
                    before: state,
                    input,
                    transition: actual,
                    checks: state_check_count.map(|_| checks),
                    state_check_count,
                });
                return Err(replay_error_ops::<M, S>(
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
        let observation = TurnView::<M> {
            actual: TurnValues::<M> {
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
        if let Err(error) = observer.turn(observation, &report) {
            return Err(OpsReplayError {
                error: stage("replay observer", error),
                observer_error: None,
                report,
                actual: Some(Box::new(S::turn_actual(OwnedTurn::<M> {
                    sequence,
                    before: state,
                    input,
                    transition: actual,
                    checks: Some(checks),
                    state_check_count,
                }))),
            });
        }
        if !differences.is_empty() {
            return Ok(report);
        }
        state = actual.state;
    }
    Ok(report)
}

fn replay_error_ops<M: CodecOps, S: ReplaySink<M>>(
    error: ModelError,
    report: ReplayReport,
    actual: Option<S::Actual>,
    sequence: u64,
    observer: &mut S,
) -> OpsReplayError<S::Actual> {
    let observer_error = observer
        .error(sequence, actual.as_ref(), &error, &report)
        .err();
    OpsReplayError {
        error,
        observer_error,
        report,
        actual: actual.map(Box::new),
    }
}

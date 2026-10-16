use super::adapter::{HostModel, builder, charge, disposition};
use super::*;
use crate::execution::{
    CheckPolicy, ReplayOptions, ReplayOutcome, check_observed_count_ops_into, record_ops,
    record_ops_with_payload_limit, replay_ops,
};
use crate::explore::{
    CheckPhase, FuzzConfig, RunLimits, SearchConfig, SearchLimits, ShrinkConfig, ShrinkLimits,
    enumerate_ops, fuzz_ops, shrink_ops,
};
use crate::model::{CheckSink, CheckStatus, Transition};
use crate::operations::ModelOps;
use crate::trace::{ReadLimits, RunConfig, Termination, Trace};
use std::num::NonZeroU64;
use std::time::Duration;

#[unsafe(no_mangle)]
pub extern "C" fn stateless_v2_abi_version() -> u32 {
    ABI_VERSION
}
#[unsafe(no_mangle)]
pub extern "C" fn stateless_v2_capabilities() -> u64 {
    CAPABILITIES
}

fn count(value: u64) -> ApiResult<usize> {
    usize::try_from(value).map_err(|_| error(INVALID_ARGUMENT, "count exceeds address space"))
}
fn limits(s: &Session) -> ReadLimits {
    let items = s.callbacks.max_items as usize;
    let bytes = s.callbacks.max_bytes as usize;
    ReadLimits {
        max_total_bytes: bytes as u64,
        max_frame_bytes: bytes,
        max_blob_bytes: bytes,
        max_string_bytes: bytes,
        max_steps: items,
        max_checks_per_step: items,
        max_outputs_per_step: items,
        max_parameters: items,
        max_items: items,
    }
}
fn controls(s: &Session, millis: u64) -> RunLimits<'_> {
    RunLimits {
        max_duration: (millis != 0).then(|| Duration::from_millis(millis)),
        cancellation: Some(&s.cancelled),
    }
}
fn checked_result(checks: Vec<Check>, state_count: usize, initial: bool) -> ResultData {
    let status = if checks.iter().any(Check::is_failure) {
        PROPERTY_FAILED
    } else {
        OK
    };
    let checks = checks
        .into_iter()
        .enumerate()
        .map(|(index, check)| {
            (
                if initial {
                    1
                } else if index < state_count {
                    2
                } else {
                    3
                },
                check,
            )
        })
        .collect();
    ResultData {
        kind: 1,
        status,
        checks,
        scalars: HashMap::from([(13, state_count as u64)]),
        ..ResultData::default()
    }
}
fn with_failure(result: &mut ResultData, failure: Option<Failure<HostValue>>) {
    if let Some(failure) = &failure {
        result.status = PROPERTY_FAILED;
        result.checks = failure
            .violations
            .iter()
            .map(|violation| {
                let phase = match violation.phase {
                    CheckPhase::InitialState => 1,
                    CheckPhase::State => 2,
                    CheckPhase::Transition => 3,
                };
                (phase, violation.check.clone())
            })
            .collect();
    }
    result.failure = failure;
}
fn trace_result(s: &Session, trace: Trace) -> ApiResult<ResultData> {
    let mut blob = Vec::new();
    trace
        .write_with_limits(&mut blob, &limits(s))
        .map_err(|err| error(TRACE_ERROR, err.to_string()))?;
    let (termination, status, message) = match trace.termination {
        Termination::Completed => (0, OK, String::new()),
        Termination::PropertyFailed => (1, PROPERTY_FAILED, String::new()),
        Termination::StepLimit => (2, OK, String::new()),
        Termination::Interrupted => (3, OK, String::new()),
        Termination::ModelError(message) => (4, MODEL_ERROR, message),
    };
    Ok(ResultData {
        kind: 3,
        status,
        blob,
        message,
        scalars: HashMap::from([(3, termination), (5, trace.steps.len() as u64)]),
        ..ResultData::default()
    })
}

api!(
    100,
    fn stateless_v2_session_new(
        callbacks: *const StatelessV2Callbacks,
        name: *const u8,
        name_len: u64,
        build: *const u8,
        build_len: u64,
        model_version: u32,
        properties_version: u32,
        codec_version: u32,
        output: *mut u64,
    ) {
        clear_output(output)?;
        if callbacks.is_null() {
            return Err(error(INVALID_ARGUMENT, "null callback descriptor"));
        }
        let version = std::ptr::addr_of!((*callbacks).abi_version).read();
        let size = std::ptr::addr_of!((*callbacks).struct_size).read();
        if version != ABI_VERSION || size < std::mem::size_of::<StatelessV2Callbacks>() as u32 {
            return Err(error(
                UNSUPPORTED,
                "unsupported ABI descriptor version or size",
            ));
        }
        let callbacks = *callbacks;
        if callbacks.dispatch.is_none()
            || callbacks.release.is_none()
            || callbacks.capabilities & !CAPABILITIES != 0
            || callbacks.capabilities & CAP_CHECK == 0
            || callbacks.max_values == 0
            || callbacks.max_items == 0
            || callbacks.max_bytes == 0
            || callbacks.max_bytes > isize::MAX as u64
            || callbacks.max_items > isize::MAX as u64
            || callbacks.max_values > isize::MAX as u64
        {
            return Err(error(
                INVALID_ARGUMENT,
                "invalid callbacks, capabilities or quotas",
            ));
        }
        if callbacks.capabilities & CAP_SHRINK != 0 && callbacks.capabilities & CAP_GENERATE == 0 {
            return Err(error(
                INVALID_ARGUMENT,
                "shrinking requires generation capability",
            ));
        }
        if callbacks.capabilities & CAP_RING != 0 && callbacks.capabilities & CAP_CODEC == 0 {
            return Err(error(
                INVALID_ARGUMENT,
                "ring recording requires codec capability",
            ));
        }
        if callbacks.capabilities & CAP_AUTO_GENERATE != 0
            && callbacks.capabilities & (CAP_ENUMERATE | CAP_GENERATE)
                != (CAP_ENUMERATE | CAP_GENERATE)
        {
            return Err(error(
                INVALID_ARGUMENT,
                "automatic generation requires enumeration and generation capabilities",
            ));
        }
        let name = string(name, name_len, callbacks.max_bytes)?;
        let build = string(build, build_len, callbacks.max_bytes)?;
        let handle = next_handle()?;
        let cancelled = Arc::new(AtomicBool::new(false));
        let s = Rc::new(Session {
            handle,
            callbacks,
            metadata: ModelMetadata {
                name,
                build,
                model_version,
                properties_version,
                codec_version,
            },
            busy: Cell::new(false),
            cancelled: Arc::clone(&cancelled),
            values: RefCell::new(HashMap::new()),
            results: RefCell::new(HashMap::new()),
            recorders: RefCell::new(HashMap::new()),
            roots: RefCell::new(HashSet::new()),
            active: RefCell::new(None),
            callback_error: RefCell::new(None),
        });
        owners()
            .lock()
            .map_err(|_| error(PANIC, "session registry poisoned"))?
            .insert(
                handle,
                ThreadEntry {
                    owner: std::thread::current().id(),
                    cancelled,
                },
            );
        SESSIONS.with(|sessions| sessions.borrow_mut().insert(handle, s));
        *output = handle;
        Ok(OK)
    }
);

api!(
    101,
    fn stateless_v2_session_close(handle: u64) {
        run(handle, 0, |s| {
            owners()
                .lock()
                .map_err(|_| error(PANIC, "session registry poisoned"))?
                .remove(&handle);
            SESSIONS.with(|sessions| sessions.borrow_mut().remove(&handle));
            let results = std::mem::take(&mut *s.results.borrow_mut());
            drop(results);
            s.recorders.borrow_mut().clear();
            let values = std::mem::take(&mut *s.values.borrow_mut());
            drop(values);
            Ok(OK)
        })
    }
);
api!(
    102,
    fn stateless_v2_session_capabilities(handle: u64, output: *mut u64) {
        clear_output(output)?;
        run(handle, 0, |s| {
            *output = s.callbacks.capabilities;
            Ok(OK)
        })
    }
);
api!(
    103,
    fn stateless_v2_cancel(handle: u64) {
        let registry = owners()
            .lock()
            .map_err(|_| error(PANIC, "session registry poisoned"))?;
        let entry = registry
            .get(&handle)
            .ok_or_else(|| error(INVALID_HANDLE, "unknown session"))?;
        entry.cancelled.store(true, Ordering::Relaxed);
        Ok(OK)
    }
);
api!(
    104,
    fn stateless_v2_cancel_reset(handle: u64) {
        run(handle, 0, |s| {
            s.cancelled.store(false, Ordering::Relaxed);
            Ok(OK)
        })
    }
);
api!(
    105,
    fn stateless_v2_value_import(handle: u64, kind: u32, token: u64, output: *mut u64) {
        clear_output(output)?;
        run(handle, 0, |s| {
            if s.values.borrow().len() as u64 >= s.callbacks.max_items {
                return Err(error(LIMIT, "value handle limit reached"));
            }
            let value_handle = next_handle()?;
            let value = s.own(kind, token)?;
            s.values.borrow_mut().insert(value_handle, value);
            *output = value_handle;
            Ok(OK)
        })
    }
);
api!(
    106,
    fn stateless_v2_value_release(handle: u64, value: u64) {
        run(handle, 0, |s| {
            let value = s
                .values
                .borrow_mut()
                .remove(&value)
                .ok_or_else(|| error(INVALID_HANDLE, "unknown value"))?;
            drop(value);
            Ok(OK)
        })
    }
);
api!(
    107,
    fn stateless_v2_value_host_token(handle: u64, value: u64, kind: *mut u32, token: *mut u64) {
        clear_output(token)?;
        if kind.is_null() {
            return Err(error(INVALID_ARGUMENT, "null kind output"));
        }
        *kind = 0;
        run(handle, 0, |s| {
            let values = s.values.borrow();
            let value = values
                .get(&value)
                .ok_or_else(|| error(INVALID_HANDLE, "unknown value"))?;
            *kind = value.kind();
            *token = value.token();
            Ok(OK)
        })
    }
);

api!(
    110,
    fn stateless_v2_callback_value(handle: u64, invocation: u64, kind: u32, token: u64) {
        builder(handle, invocation, |s, active| {
            let allowed = match active.operation {
                1 | 11 => kind == STATE && active.values.is_empty(),
                2 => {
                    if active.values.is_empty() {
                        kind == STATE
                    } else {
                        kind == OUTPUT
                    }
                }
                8 | 18 => kind == CURSOR && active.values.is_empty(),
                9 | 13 | 15 => kind == INPUT && active.values.is_empty(),
                _ => false,
            };
            if !allowed {
                return Err(error(
                    INVALID_ARGUMENT,
                    "value publication is invalid for this callback",
                ));
            }
            if active.values.len() as u64 >= s.callbacks.max_items {
                return Err(error(LIMIT, "callback value limit reached"));
            }
            active.values.push(s.own(kind, token)?);
            Ok(OK)
        })
    }
);
api!(
    111,
    fn stateless_v2_callback_scalar(handle: u64, invocation: u64, value: u64) {
        builder(handle, invocation, |_s, active| {
            if !matches!(active.operation, 5 | 6 | 7 | 16 | 17) || active.scalar.is_some() {
                return Err(error(
                    INVALID_ARGUMENT,
                    "scalar publication is invalid for this callback",
                ));
            }
            if matches!(active.operation, 5 | 16 | 17) && value > 1 {
                return Err(error(INVALID_ARGUMENT, "invalid boolean scalar"));
            }
            active.scalar = Some(value);
            Ok(OK)
        })
    }
);
api!(
    112,
    fn stateless_v2_callback_check(
        handle: u64,
        invocation: u64,
        status: u32,
        id: *const u8,
        id_len: u64,
        details: *const u8,
        details_len: u64,
    ) {
        builder(handle, invocation, |s, active| {
            if !matches!(active.operation, 3 | 4) {
                return Err(error(INVALID_ARGUMENT, "checks require a checker callback"));
            }
            if active.checks.len() as u64 >= s.callbacks.max_items {
                return Err(error(LIMIT, "callback check limit reached"));
            }
            if status > 2 || (status == 0 && details_len != 0) {
                return Err(error(
                    INVALID_ARGUMENT,
                    "invalid check status or passing details",
                ));
            }
            charge(
                s,
                active,
                id_len
                    .checked_add(details_len)
                    .ok_or_else(|| error(LIMIT, "check size overflow"))?,
            )?;
            let id = string(id, id_len, s.callbacks.max_bytes)?;
            let details = string(details, details_len, s.callbacks.max_bytes)?;
            active.checks.push(match status {
                0 => Check::passed(id),
                1 => Check::failed(id, details),
                _ => Check::skipped(id, details),
            });
            Ok(OK)
        })
    }
);
api!(
    113,
    fn stateless_v2_callback_bytes(handle: u64, invocation: u64, data: *const u8, len: u64) {
        builder(handle, invocation, |s, active| {
            if !matches!(active.operation, 10 | 12 | 14) {
                return Err(error(INVALID_ARGUMENT, "bytes require an encoder callback"));
            }
            charge(s, active, len)?;
            active
                .bytes
                .extend_from_slice(span(data, len, s.callbacks.max_bytes)?);
            Ok(OK)
        })
    }
);
api!(
    114,
    fn stateless_v2_callback_disposition(
        handle: u64,
        invocation: u64,
        kind: u32,
        reason: *const u8,
        len: u64,
    ) {
        builder(handle, invocation, |s, active| {
            if active.operation != 2 || active.disposition.is_some() {
                return Err(error(
                    INVALID_ARGUMENT,
                    "disposition requires a step callback",
                ));
            }
            charge(s, active, len)?;
            active.disposition = Some(disposition(
                kind,
                string(reason, len, s.callbacks.max_bytes)?,
            )?);
            Ok(OK)
        })
    }
);
api!(
    115,
    fn stateless_v2_callback_error(handle: u64, invocation: u64, data: *const u8, len: u64) {
        builder(handle, invocation, |s, _active| {
            Err(error(
                MODEL_ERROR,
                string(data, len, s.callbacks.max_bytes.min(65536))?,
            ))
        })
    }
);
api!(
    116,
    fn stateless_v2_callback_rng(handle: u64, invocation: u64, output: *mut u64) {
        builder(handle, invocation, |_s, active| {
            clear_output(output)?;
            let rng = active
                .rng
                .as_mut()
                .ok_or_else(|| error(INVALID_ARGUMENT, "RNG only available during generation"))?;
            *output = rng.next_u64();
            Ok(OK)
        })
    }
);

api!(
    120,
    fn stateless_v2_check_initial(handle: u64, state: u64, output: *mut u64) {
        clear_output(output)?;
        run(handle, CAP_CHECK, |s| {
            let model = HostModel(Rc::clone(s));
            let state = if state == 0 {
                model.initial_state().map_err(|err| s.model_error(err))?
            } else {
                s.value(state, STATE)?
            };
            let mut checks = Vec::new();
            model
                .check_state_into(&state, &mut CheckSink::new(&mut checks))
                .map_err(|err| s.model_error(err))?;
            let count = checks.len();
            publish(s, checked_result(checks, count, true), output)
        })
    }
);
api!(
    121,
    fn stateless_v2_check_observed(
        handle: u64,
        before: u64,
        input: u64,
        after: u64,
        outputs: *const u64,
        output_count: u64,
        kind: u32,
        reason: *const u8,
        reason_len: u64,
        sequence: u64,
        state_every: u64,
        transition_checks: u32,
        output: *mut u64,
    ) {
        clear_output(output)?;
        run(handle, CAP_CHECK, |s| {
            if transition_checks > 1 {
                return Err(error(INVALID_ARGUMENT, "invalid transition check flag"));
            }
            let policy = CheckPolicy {
                state_every: NonZeroU64::new(state_every).ok_or_else(|| {
                    error(INVALID_ARGUMENT, "state check interval must be positive")
                })?,
                transition_checks: transition_checks != 0,
            };
            let model = HostModel(Rc::clone(s));
            let before = s.value(before, STATE)?;
            let input = s.value(input, INPUT)?;
            let transition = Transition {
                state: s.value(after, STATE)?,
                outputs: handles(outputs, output_count, s.callbacks.max_items)?
                    .iter()
                    .map(|value| s.value(*value, OUTPUT))
                    .collect::<ApiResult<Vec<_>>>()?,
                disposition: disposition(kind, string(reason, reason_len, s.callbacks.max_bytes)?)?,
            };
            let mut checks = Vec::new();
            let count = check_observed_count_ops_into(
                &model,
                &before,
                &input,
                &transition,
                sequence,
                policy,
                &mut checks,
            )
            .map_err(|err| {
                let mut err = s.model_error(err);
                err.sequence = sequence;
                err
            })?;
            publish(s, checked_result(checks, count, false), output)
        })
    }
);
api!(
    122,
    fn stateless_v2_enumerate(
        handle: u64,
        max_states: u64,
        max_transitions: u64,
        max_depth: u64,
        max_state_bytes: u64,
        max_duration_millis: u64,
        output: *mut u64,
    ) {
        clear_output(output)?;
        run(handle, CAP_ENUMERATE, |s| {
            let model = HostModel(Rc::clone(s));
            let config = SearchConfig {
                max_states: count(max_states)?,
                max_transitions,
                max_depth: count(max_depth)?,
            };
            let limits = SearchLimits {
                control: controls(s, max_duration_millis),
                max_estimated_state_bytes: if max_state_bytes == u64::MAX {
                    None
                } else {
                    Some(count(max_state_bytes)?)
                },
            };
            let report = enumerate_ops(&model, config, limits).map_err(|err| s.model_error(err))?;
            let mut result = ResultData {
                kind: 2,
                scalars: HashMap::from([
                    (3, report.termination as u64),
                    (4, report.states as u64),
                    (5, report.transitions),
                    (6, report.max_depth_reached as u64),
                    (7, report.skipped_checks),
                    (8, report.estimated_retained_state_bytes.unwrap_or(0) as u64),
                    (
                        9,
                        u64::from(report.estimated_retained_state_bytes.is_some()),
                    ),
                ]),
                ..ResultData::default()
            };
            with_failure(&mut result, report.failure);
            publish(s, result, output)
        })
    }
);
api!(
    123,
    fn stateless_v2_record(
        handle: u64,
        inputs: *const u64,
        input_count: u64,
        max_steps: u64,
        output: *mut u64,
    ) {
        clear_output(output)?;
        run(handle, CAP_CODEC, |s| {
            let inputs = handles(inputs, input_count, s.callbacks.max_items)?
                .iter()
                .map(|value| s.value(*value, INPUT))
                .collect::<ApiResult<Vec<_>>>()?;
            let model = HostModel(Rc::clone(s));
            let trace = record_ops(
                &model,
                inputs.into_iter().map(Ok),
                RunConfig::default(),
                count(max_steps)?,
                &limits(s),
            )
            .map_err(|err| s.model_error(err))?;
            publish(s, trace_result(s, trace)?, output)
        })
    }
);
api!(
    124,
    fn stateless_v2_replay(
        handle: u64,
        artifact: *const u8,
        artifact_len: u64,
        allow_build_mismatch: u32,
        output: *mut u64,
    ) {
        clear_output(output)?;
        run(handle, CAP_CODEC, |s| {
            if allow_build_mismatch > 1 {
                return Err(error(INVALID_ARGUMENT, "invalid build mismatch flag"));
            }
            let trace = Trace::read_from(
                span(artifact, artifact_len, s.callbacks.max_bytes)?,
                &limits(s),
            )
            .map_err(|err| error(TRACE_ERROR, err.to_string()))?;
            let model = HostModel(Rc::clone(s));
            let report = replay_ops(
                &model,
                &trace,
                ReplayOptions {
                    allow_build_mismatch: allow_build_mismatch != 0,
                },
                &limits(s),
            )
            .map_err(|err| s.model_error(err))?;
            let (status, message, step, divergence_field) = match report.outcome {
                ReplayOutcome::Exact => (OK, String::new(), None, String::new()),
                ReplayOutcome::Diverged { step, field } => (
                    DIVERGED,
                    format!("diverged at {step:?}: {field}"),
                    step,
                    field.to_owned(),
                ),
                ReplayOutcome::Incompatible { reason } => {
                    (INCOMPATIBLE, reason, None, String::new())
                }
            };
            publish(
                s,
                ResultData {
                    kind: 4,
                    status,
                    message,
                    divergence_field,
                    scalars: HashMap::from([
                        (14, report.steps_verified as u64),
                        (15, u64::from(report.build_matches)),
                        (16, u64::from(report.failure_reproduced)),
                        (20, u64::from(step.is_some())),
                        (21, step.unwrap_or(0) as u64),
                    ]),
                    ..ResultData::default()
                },
                output,
            )
        })
    }
);
api!(
    125,
    fn stateless_v2_fuzz(
        handle: u64,
        seed: u64,
        cases: u64,
        max_steps: u64,
        max_transitions: u64,
        mutation_percent: u32,
        max_duration_millis: u64,
        output: *mut u64,
    ) {
        clear_output(output)?;
        run(handle, CAP_GENERATE, |s| {
            let mutation_percent = u8::try_from(mutation_percent)
                .map_err(|_| error(INVALID_ARGUMENT, "invalid mutation percent"))?;
            let model = HostModel(Rc::clone(s));
            let report = fuzz_ops(
                &model,
                FuzzConfig {
                    seed,
                    cases: count(cases)?,
                    max_steps: count(max_steps)?,
                    max_transitions,
                    mutation_percent,
                },
                controls(s, max_duration_millis),
            )
            .map_err(|err| s.model_error(err))?;
            let mut result = ResultData {
                kind: 5,
                scalars: HashMap::from([
                    (3, report.termination as u64),
                    (5, report.transitions),
                    (7, report.skipped_checks),
                    (17, report.cases as u64),
                ]),
                ..ResultData::default()
            };
            with_failure(&mut result, report.failure);
            publish(s, result, output)
        })
    }
);
api!(
    126,
    fn stateless_v2_shrink(
        handle: u64,
        failure_result: u64,
        max_attempts: u64,
        max_transitions: u64,
        max_duration_millis: u64,
        output: *mut u64,
    ) {
        clear_output(output)?;
        run(handle, CAP_SHRINK, |s| {
            let original = s.result(failure_result)?;
            let failure = original
                .failure
                .as_ref()
                .ok_or_else(|| error(INVALID_ARGUMENT, "result contains no failure witness"))?;
            let model = HostModel(Rc::clone(s));
            let report = shrink_ops(
                &model,
                failure,
                ShrinkConfig {
                    max_attempts: count(max_attempts)?,
                },
                ShrinkLimits {
                    control: controls(s, max_duration_millis),
                    max_replayed_transitions: (max_transitions != u64::MAX)
                        .then_some(max_transitions),
                },
            )
            .map_err(|err| s.model_error(err))?;
            let mut result = ResultData {
                kind: 6,
                scalars: HashMap::from([
                    (3, report.termination as u64),
                    (5, report.replayed_transitions),
                    (18, report.attempts as u64),
                    (19, u64::from(report.validated_original)),
                ]),
                ..ResultData::default()
            };
            with_failure(&mut result, Some(report.minimized));
            publish(s, result, output)
        })
    }
);

api!(
    130,
    fn stateless_v2_result_scalar(handle: u64, result: u64, field: u32, output: *mut u64) {
        clear_output(output)?;
        run(handle, 0, |s| {
            let result = s.result(result)?;
            *output = match field {
                1 => result.status as u64,
                2 => result.kind,
                10 => result.checks.len() as u64,
                11 => result
                    .failure
                    .as_ref()
                    .map_or(0, |failure| failure.inputs.len() as u64),
                12 => result.blob.len() as u64,
                3..=9 | 13..=21 => result.scalars.get(&field).copied().unwrap_or(0),
                _ => return Err(error(INVALID_ARGUMENT, "unknown result field")),
            };
            Ok(OK)
        })
    }
);
api!(
    131,
    fn stateless_v2_result_check(
        handle: u64,
        result: u64,
        index: u64,
        status: *mut u32,
        phase: *mut u32,
    ) {
        if status.is_null() || phase.is_null() {
            return Err(error(INVALID_ARGUMENT, "null check outputs"));
        }
        *status = 0;
        *phase = 0;
        run(handle, 0, |s| {
            let result = s.result(result)?;
            let (check_phase, check) = result
                .checks
                .get(count(index)?)
                .ok_or_else(|| error(INVALID_ARGUMENT, "check index out of range"))?;
            *phase = *check_phase;
            *status = match check.status {
                CheckStatus::Passed => 0,
                CheckStatus::Failed(_) => 1,
                CheckStatus::Skipped(_) => 2,
            };
            Ok(OK)
        })
    }
);
api!(
    132,
    fn stateless_v2_result_input(handle: u64, result: u64, index: u64, output: *mut u64) {
        clear_output(output)?;
        run(handle, 0, |s| {
            if s.values.borrow().len() as u64 >= s.callbacks.max_items {
                return Err(error(LIMIT, "value handle limit reached"));
            }
            let result = s.result(result)?;
            let value = result
                .failure
                .as_ref()
                .and_then(|failure| failure.inputs.get(count(index).ok()?))
                .ok_or_else(|| error(INVALID_ARGUMENT, "witness input index out of range"))?
                .clone();
            let handle = next_handle()?;
            s.values.borrow_mut().insert(handle, value);
            *output = handle;
            Ok(OK)
        })
    }
);
api!(
    133,
    fn stateless_v2_result_release(handle: u64, result: u64) {
        run(handle, 0, |s| {
            let result = s
                .results
                .borrow_mut()
                .remove(&result)
                .ok_or_else(|| error(INVALID_HANDLE, "unknown result"))?;
            drop(result);
            Ok(OK)
        })
    }
);
api!(
    134,
    fn stateless_v2_result_copy(
        handle: u64,
        result: u64,
        field: u32,
        index: u64,
        data: *mut u8,
        capacity: u64,
        written: *mut u64,
    ) {
        clear_output(written)?;
        run(handle, 0, |s| {
            let result = s.result(result)?;
            let bytes = match field {
                1 => result.blob.as_slice(),
                4 => result.message.as_bytes(),
                5 => result.divergence_field.as_bytes(),
                2 | 3 => {
                    let (_, check) = result
                        .checks
                        .get(count(index)?)
                        .ok_or_else(|| error(INVALID_ARGUMENT, "check index out of range"))?;
                    if field == 2 {
                        check.id.as_bytes()
                    } else {
                        match &check.status {
                            CheckStatus::Passed => &[],
                            CheckStatus::Failed(reason) | CheckStatus::Skipped(reason) => {
                                reason.as_bytes()
                            }
                        }
                    }
                }
                _ => return Err(error(INVALID_ARGUMENT, "unknown copy field")),
            };
            copy_bytes(bytes, data, capacity, written)
        })
    }
);

/// # Safety
/// All scalar outputs must be writable. The data span must have capacity bytes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn stateless_v2_last_error(
    status: *mut i32,
    operation: *mut u32,
    phase: *mut u32,
    sequence: *mut u64,
    data: *mut u8,
    capacity: u64,
    written: *mut u64,
) -> i32 {
    match catch_unwind(AssertUnwindSafe(|| -> ApiResult<i32> {
        if status.is_null() || operation.is_null() || phase.is_null() || sequence.is_null() {
            return Err(error(INVALID_ARGUMENT, "null error output"));
        }
        let err = LAST_ERROR
            .with(|slot| slot.borrow().clone())
            .unwrap_or_else(|| error(OK, ""));
        // SAFETY: Caller supplies valid scalar outputs and the writable span.
        unsafe {
            *status = err.status;
            *operation = err.operation;
            *phase = err.phase;
            *sequence = err.sequence;
            copy_bytes(err.message.as_bytes(), data, capacity, written)
        }
    })) {
        Ok(Ok(status)) => status,
        Ok(Err(err)) => err.status,
        Err(_) => PANIC,
    }
}

api!(
    140,
    fn stateless_v2_rng_next(state: *mut u64, output: *mut u64) {
        if state.is_null() || output.is_null() || state == output {
            return Err(error(INVALID_ARGUMENT, "invalid or aliased RNG outputs"));
        }
        let mut rng = Rng::new(*state);
        let value = rng.next_u64();
        *state = rng.state();
        *output = value;
        Ok(OK)
    }
);
api!(
    141,
    fn stateless_v2_rng_index(state: *mut u64, upper: u64, present: *mut u32, output: *mut u64) {
        if state.is_null() || output.is_null() || present.is_null() || state == output {
            return Err(error(INVALID_ARGUMENT, "invalid or aliased RNG outputs"));
        }
        let mut rng = Rng::new(*state);
        let value = rng.index_u64(upper);
        *state = rng.state();
        *present = u32::from(value.is_some());
        *output = value.unwrap_or(0);
        Ok(OK)
    }
);
api!(
    142,
    fn stateless_v2_callback_rng_index(
        handle: u64,
        invocation: u64,
        upper: u64,
        present: *mut u32,
        output: *mut u64,
    ) {
        builder(handle, invocation, |_s, active| {
            clear_output(output)?;
            if present.is_null() {
                return Err(error(INVALID_ARGUMENT, "null RNG presence output"));
            }
            *present = 0;
            let rng = active
                .rng
                .as_mut()
                .ok_or_else(|| error(INVALID_ARGUMENT, "RNG only available during generation"))?;
            let value = rng.index_u64(upper);
            *present = u32::from(value.is_some());
            *output = value.unwrap_or(0);
            Ok(OK)
        })
    }
);

api!(
    143,
    fn stateless_v2_trace_convert(
        handle: u64,
        format: u32,
        data: *const u8,
        len: u64,
        output: *mut u64,
    ) {
        clear_output(output)?;
        run(handle, 0, |s| {
            let bytes = span(data, len, s.callbacks.max_bytes)?;
            let blob = match format {
                1 => super::portable::binary_to_packet(bytes, &limits(s)),
                2 => super::portable::packet_to_binary(bytes, &limits(s)),
                _ => return Err(error(INVALID_ARGUMENT, "unknown trace conversion format")),
            }
            .map_err(|err| error(TRACE_ERROR, err.to_string()))?;
            publish(
                s,
                ResultData {
                    blob,
                    ..ResultData::default()
                },
                output,
            )
        })
    }
);

struct RecordCursor {
    model: HostModel,
    cursor: HostValue,
    ended: bool,
}
impl Iterator for RecordCursor {
    type Item = Result<HostValue, ModelError>;
    fn next(&mut self) -> Option<Self::Item> {
        if self.ended {
            return None;
        }
        match self.model.cursor_next(&self.cursor) {
            Ok(Some(value)) => Some(Ok(value)),
            Ok(None) => {
                self.ended = true;
                None
            }
            Err(err) => {
                self.ended = true;
                Some(Err(err))
            }
        }
    }
}
api!(
    144,
    fn stateless_v2_record_cursor(handle: u64, cursor: u64, max_steps: u64, output: *mut u64) {
        clear_output(output)?;
        run(handle, CAP_CODEC, |s| {
            let inputs = RecordCursor {
                model: HostModel(Rc::clone(s)),
                cursor: s.value(cursor, CURSOR)?,
                ended: false,
            };
            let model = HostModel(Rc::clone(s));
            let trace = record_ops(
                &model,
                inputs,
                RunConfig::default(),
                count(max_steps)?,
                &limits(s),
            )
            .map_err(|err| s.model_error(err))?;
            publish(s, trace_result(s, trace)?, output)
        })
    }
);

api!(
    146,
    fn stateless_v2_record_cursor_limited(
        handle: u64,
        cursor: u64,
        max_steps: u64,
        max_blob_bytes: u64,
        max_items: u64,
        max_payload_bytes: u64,
        output: *mut u64,
    ) {
        clear_output(output)?;
        run(handle, CAP_CODEC, |s| {
            let inputs = RecordCursor {
                model: HostModel(Rc::clone(s)),
                cursor: s.value(cursor, CURSOR)?,
                ended: false,
            };
            let model = HostModel(Rc::clone(s));
            let mut bounds = limits(s);
            bounds.max_blob_bytes = count(max_blob_bytes.min(s.callbacks.max_bytes))?;
            // Portable item accounting excludes engine metadata and is enforced by
            // the shared recorder independently of the outer binary artifact quota.
            bounds.max_checks_per_step = count(max_items.min(s.callbacks.max_items))?;
            bounds.max_outputs_per_step = count(max_items.min(s.callbacks.max_items))?;
            let trace = record_ops_with_payload_limit(
                &model,
                inputs,
                RunConfig::default(),
                count(max_steps)?,
                &bounds,
                Some(max_payload_bytes),
                Some(count(max_items.min(s.callbacks.max_items))?),
            )
            .map_err(|err| s.model_error(err))?;
            publish(s, trace_result(s, trace)?, output)
        })
    }
);

api!(
    150,
    fn stateless_v2_ring_new(
        handle: u64,
        state: u64,
        max_steps: u64,
        max_retained_bytes: u64,
        output: *mut u64,
    ) {
        clear_output(output)?;
        run(handle, CAP_RING | CAP_CODEC, |s| {
            if s.recorders.borrow().len() as u64 >= s.callbacks.max_items {
                return Err(error(LIMIT, "recorder handle limit reached"));
            }
            if max_retained_bytes > s.callbacks.max_bytes {
                return Err(error(
                    INVALID_ARGUMENT,
                    "recorder exceeds session byte limit",
                ));
            }
            let model = HostModel(Rc::clone(s));
            let state = s.value(state, STATE)?;
            let recorder = crate::monitor::Recorder::with_ops_options(
                &model,
                &state,
                RunConfig::default(),
                crate::monitor::RecorderOptions {
                    max_steps: count(max_steps)?,
                    max_retained_bytes,
                    limits: limits(s),
                },
            )
            .map_err(|err| s.model_error(err))?;
            let handle = next_handle()?;
            s.recorders.borrow_mut().insert(handle, recorder);
            *output = handle;
            Ok(OK)
        })
    }
);
api!(
    151,
    fn stateless_v2_ring_observe(
        handle: u64,
        recorder: u64,
        before: u64,
        input: u64,
        after: u64,
        outputs: *const u64,
        output_count: u64,
        kind: u32,
        reason: *const u8,
        reason_len: u64,
        output: *mut u64,
    ) {
        clear_output(output)?;
        run(handle, CAP_RING | CAP_CODEC, |s| {
            let model = HostModel(Rc::clone(s));
            let before = s.value(before, STATE)?;
            let input = s.value(input, INPUT)?;
            let transition = Transition {
                state: s.value(after, STATE)?,
                outputs: handles(outputs, output_count, s.callbacks.max_items)?
                    .iter()
                    .map(|value| s.value(*value, OUTPUT))
                    .collect::<ApiResult<Vec<_>>>()?,
                disposition: disposition(kind, string(reason, reason_len, s.callbacks.max_bytes)?)?,
            };
            let mut recorders = s.recorders.borrow_mut();
            let recorder = recorders
                .get_mut(&recorder)
                .ok_or_else(|| error(INVALID_HANDLE, "unknown recorder"))?;
            let checks = recorder
                .observe_ops(&model, &before, &input, &transition)
                .map_err(|err| s.model_error(err))?
                .to_vec();
            let mut result = checked_result(checks, 0, false);
            // Recorder's combined check batch does not expose its phase split.
            for (phase, _) in &mut result.checks {
                *phase = 0;
            }
            result.scalars.insert(5, recorder.observed_steps());
            publish(s, result, output)
        })
    }
);
api!(
    152,
    fn stateless_v2_ring_snapshot(handle: u64, recorder: u64, output: *mut u64) {
        clear_output(output)?;
        run(handle, CAP_RING | CAP_CODEC, |s| {
            let recorders = s.recorders.borrow();
            let recorder = recorders
                .get(&recorder)
                .ok_or_else(|| error(INVALID_HANDLE, "unknown recorder"))?;
            publish(s, trace_result(s, recorder.snapshot())?, output)
        })
    }
);
api!(
    153,
    fn stateless_v2_ring_release(handle: u64, recorder: u64) {
        run(handle, 0, |s| {
            s.recorders
                .borrow_mut()
                .remove(&recorder)
                .ok_or_else(|| error(INVALID_HANDLE, "unknown recorder"))?;
            Ok(OK)
        })
    }
);

#[cfg(all(target_arch = "wasm32", feature = "abi-v2-wasm"))]
#[link(wasm_import_module = "stateless_v2")]
unsafe extern "C" {
    #[link_name = "dispatch"]
    fn imported_dispatch(
        context: u64,
        session: u64,
        invocation: u64,
        operation: u32,
        args: *const u64,
        nargs: u32,
        data: *const u8,
        nbytes: u64,
    ) -> i32;
    #[link_name = "release"]
    fn imported_release(context: u64, kind: u32, token: u64);
}

#[cfg(all(target_arch = "wasm32", feature = "abi-v2-wasm"))]
api!(
    145,
    fn stateless_v2_wasm_session_new(
        capabilities: u64,
        context: u64,
        max_values: u64,
        max_items: u64,
        max_bytes: u64,
        name: *const u8,
        name_len: u64,
        build: *const u8,
        build_len: u64,
        model_version: u32,
        properties_version: u32,
        codec_version: u32,
        output: *mut u64,
    ) {
        let callbacks = StatelessV2Callbacks {
            abi_version: ABI_VERSION,
            struct_size: std::mem::size_of::<StatelessV2Callbacks>() as u32,
            capabilities,
            context,
            dispatch: Some(imported_dispatch),
            release: Some(imported_release),
            max_values,
            max_items,
            max_bytes,
        };
        // Delegate without another boundary so detailed registration errors survive.
        let status = stateless_v2_session_new(
            &callbacks,
            name,
            name_len,
            build,
            build_len,
            model_version,
            properties_version,
            codec_version,
            output,
        );
        if status >= INVALID_ARGUMENT {
            Err(LAST_ERROR
                .with(|slot| slot.borrow().clone())
                .unwrap_or_else(|| error(status, "session creation failed")))
        } else {
            Ok(status)
        }
    }
);

#[cfg(all(target_arch = "wasm32", not(feature = "abi-v2-wasm")))]
api!(
    145,
    fn stateless_v2_wasm_session_new(
        capabilities: u64,
        context: u64,
        max_values: u64,
        max_items: u64,
        max_bytes: u64,
        name: *const u8,
        name_len: u64,
        build: *const u8,
        build_len: u64,
        model_version: u32,
        properties_version: u32,
        codec_version: u32,
        output: *mut u64,
    ) {
        clear_output(output)?;
        let _ = (
            capabilities,
            context,
            max_values,
            max_items,
            max_bytes,
            name,
            name_len,
            build,
            build_len,
            model_version,
            properties_version,
            codec_version,
        );
        Err(error(
            UNSUPPORTED,
            "this Wasm artifact was built without abi-v2-wasm",
        ))
    }
);

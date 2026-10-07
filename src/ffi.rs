//! Version 1 byte-oriented callback ABI. See `bindings/README.md` for ownership,
//! callback packets, threading, and the deliberately limited initial adapter.
//!
//! All non-null pointers supplied by a caller must be valid for their documented
//! length and lifetime. Opaque handles must originate from this library, must not
//! be freed twice, and must not be used concurrently. Foreign callbacks must not
//! unwind or throw across the ABI. Invalid foreign pointers are not recoverable.

use crate::execution::{ReplayOptions, ReplayOutcome, record, replay};
use crate::explore::{SearchConfig, enumerate};
use crate::model::{
    Check, CheckStatus, Disposition, Enumerate, Model, ModelCodec, ModelError, ModelMetadata,
    Transition, TransitionRef,
};
use crate::trace::{ReadLimits, RunConfig, Termination, Trace};
use std::cell::RefCell;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::ptr;
use std::slice;

pub const ABI_VERSION: u32 = 1;
pub const OK: i32 = 0;
pub const PROPERTY_FAILED: i32 = 1;
pub const DIVERGED: i32 = 2;
pub const INCOMPATIBLE: i32 = 3;
pub const INVALID_ARGUMENT: i32 = 10;
pub const MODEL_ERROR: i32 = 11;
pub const TRACE_ERROR: i32 = 12;
pub const PANIC: i32 = 13;
const MAX_BYTES: usize = 64 * 1024 * 1024;
const MAX_ITEMS: usize = 1_000_000;

/// An opaque library-owned byte buffer. A callback borrows its response buffer.
pub struct StatelessBuffer {
    bytes: Vec<u8>,
}

/// Operations: 0 initial, 1 step, 2 state checks, 3 transition checks, 4 inputs.
pub type Dispatch = unsafe extern "C" fn(
    context: *mut std::ffi::c_void,
    operation: u32,
    state: *const u8,
    state_len: usize,
    input: *const u8,
    input_len: usize,
    response: *mut StatelessBuffer,
) -> i32;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct StatelessCallbacks {
    pub abi_version: u32,
    pub struct_size: u32,
    pub context: *mut std::ffi::c_void,
    pub dispatch: Option<Dispatch>,
}

pub struct StatelessModel {
    host: HostModel,
}

struct HostModel {
    callbacks: StatelessCallbacks,
    metadata: ModelMetadata,
}

thread_local! { static LAST_ERROR: RefCell<String> = const { RefCell::new(String::new()) }; }

fn set_error(message: impl Into<String>) {
    LAST_ERROR.with(|slot| *slot.borrow_mut() = message.into());
}

fn boundary(operation: impl FnOnce() -> Result<i32, (i32, String)>) -> i32 {
    set_error("");
    match catch_unwind(AssertUnwindSafe(operation)) {
        Ok(Ok(status)) => status,
        Ok(Err((status, message))) => {
            set_error(message);
            status
        }
        Err(_) => {
            set_error("Rust panic in ABI operation");
            PANIC
        }
    }
}

/// # Safety
/// `data` must point to `len` readable bytes, or be null when `len` is zero.
unsafe fn borrowed<'a>(data: *const u8, len: usize) -> Result<&'a [u8], (i32, String)> {
    if len > MAX_BYTES || (len != 0 && data.is_null()) {
        return Err((
            INVALID_ARGUMENT,
            "null pointer or oversized byte span".into(),
        ));
    }
    if len == 0 {
        return Ok(&[]);
    }
    // SAFETY: The caller guarantees the pointer is valid for len readable bytes.
    Ok(unsafe { slice::from_raw_parts(data, len) })
}

#[unsafe(no_mangle)]
pub extern "C" fn stateless_abi_version() -> u32 {
    ABI_VERSION
}

/// Allocates an initialized buffer. Null means invalid length or allocation failure.
#[unsafe(no_mangle)]
pub extern "C" fn stateless_buffer_new(len: usize) -> *mut StatelessBuffer {
    match catch_unwind(|| {
        if len > MAX_BYTES {
            return ptr::null_mut();
        }
        let mut bytes = Vec::new();
        if bytes.try_reserve_exact(len).is_err() {
            return ptr::null_mut();
        }
        bytes.resize(len, 0);
        Box::into_raw(Box::new(StatelessBuffer { bytes }))
    }) {
        Ok(pointer) => pointer,
        Err(_) => ptr::null_mut(),
    }
}

/// # Safety
/// `buffer` must be null or a live handle from this library. The returned span
/// remains valid until the next assignment or free; it must not alias assignment.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn stateless_buffer_data(buffer: *mut StatelessBuffer) -> *mut u8 {
    if buffer.is_null() {
        return ptr::null_mut();
    }
    // SAFETY: The caller guarantees a live, exclusively accessed buffer handle.
    unsafe { (*buffer).bytes.as_mut_ptr() }
}

/// # Safety
/// `buffer` must be null or a live handle from this library.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn stateless_buffer_len(buffer: *const StatelessBuffer) -> usize {
    if buffer.is_null() {
        return 0;
    }
    // SAFETY: The caller guarantees a live buffer handle.
    unsafe { (*buffer).bytes.len() }
}

/// # Safety
/// `buffer` must be live and exclusively accessed. `data` must be readable for
/// `len` bytes. The input may refer to the current contents of this buffer.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn stateless_buffer_assign(
    buffer: *mut StatelessBuffer,
    data: *const u8,
    len: usize,
) -> i32 {
    boundary(|| {
        if buffer.is_null() {
            return Err((INVALID_ARGUMENT, "null buffer".into()));
        }
        // SAFETY: The caller guarantees the input span and output handle are valid.
        let copy = unsafe { borrowed(data, len)? }.to_vec();
        // SAFETY: The input was copied before mutating a possibly aliased buffer.
        unsafe {
            (*buffer).bytes = copy;
        }
        Ok(OK)
    })
}

/// # Safety
/// Free only a handle returned by buffer_new, at most once. Callback response
/// buffers are borrowed and must never be freed by the callback. Null is allowed.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn stateless_buffer_free(buffer: *mut StatelessBuffer) {
    if !buffer.is_null() {
        // SAFETY: The caller transfers ownership of this library-created handle.
        drop(unsafe { Box::from_raw(buffer) });
    }
}

/// Copies the calling thread's last error. Does not clear it.
/// # Safety
/// `output` must be a live, exclusively accessed buffer from this library.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn stateless_last_error(output: *mut StatelessBuffer) -> i32 {
    if output.is_null() {
        return INVALID_ARGUMENT;
    }
    match catch_unwind(AssertUnwindSafe(|| {
        LAST_ERROR.with(|error| {
            // SAFETY: The caller provides an exclusively accessed live output handle.
            unsafe {
                (*output).bytes = error.borrow().as_bytes().to_vec();
            }
        });
    })) {
        Ok(()) => OK,
        Err(_) => PANIC,
    }
}

/// Creates a model. The callback table is copied; context remains caller-owned
/// and must outlive the model. Versions identify model, properties, and codec.
/// # Safety
/// The table and output pointer must be valid and aligned; byte spans must be
/// readable. Callbacks obey Dispatch's contract and never unwind across C.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn stateless_model_new(
    callbacks: *const StatelessCallbacks,
    name: *const u8,
    name_len: usize,
    build: *const u8,
    build_len: usize,
    model_version: u32,
    properties_version: u32,
    codec_version: u32,
    output: *mut *mut StatelessModel,
) -> i32 {
    boundary(|| {
        if output.is_null() || callbacks.is_null() {
            return Err((INVALID_ARGUMENT, "null model argument".into()));
        }
        // SAFETY: The caller supplies valid aligned pointers for table and output.
        unsafe {
            *output = ptr::null_mut();
        }
        // SAFETY: The full v1 table is readable by the caller contract.
        let callbacks = unsafe { *callbacks };
        if callbacks.abi_version != ABI_VERSION
            || callbacks.struct_size as usize != std::mem::size_of::<StatelessCallbacks>()
            || callbacks.dispatch.is_none()
        {
            return Err((
                INVALID_ARGUMENT,
                "unsupported ABI table or missing dispatch".into(),
            ));
        }
        // SAFETY: The caller supplies readable model name and build spans.
        let name = std::str::from_utf8(unsafe { borrowed(name, name_len)? })
            .map_err(|e| (INVALID_ARGUMENT, e.to_string()))?
            .to_owned();
        // SAFETY: The caller supplies a readable build span.
        let build = std::str::from_utf8(unsafe { borrowed(build, build_len)? })
            .map_err(|e| (INVALID_ARGUMENT, e.to_string()))?
            .to_owned();
        if name.is_empty() || build.is_empty() {
            return Err((
                INVALID_ARGUMENT,
                "model name and build identity are required".into(),
            ));
        }
        let model = StatelessModel {
            host: HostModel {
                callbacks,
                metadata: ModelMetadata {
                    name,
                    build,
                    model_version,
                    properties_version,
                    codec_version,
                },
            },
        };
        // SAFETY: output is valid and the new Box ownership passes to the caller.
        unsafe {
            *output = Box::into_raw(Box::new(model));
        }
        Ok(OK)
    })
}

/// # Safety
/// `model` must be null or a model created here, freed at most once and not in use.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn stateless_model_free(model: *mut StatelessModel) {
    if !model.is_null() {
        // SAFETY: The caller transfers a live owned model handle to this function.
        drop(unsafe { Box::from_raw(model) });
    }
}

/// Records a batch of inputs (u32 count, then u32 length + bytes for each input).
/// Returns PROPERTY_FAILED when the artifact contains an invariant failure.
/// The output buffer is replaced only on success (OK or PROPERTY_FAILED).
/// # Safety
/// Handles must be live and not concurrently accessed. Input span must be valid.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn stateless_record(
    model: *const StatelessModel,
    inputs: *const u8,
    inputs_len: usize,
    max_steps: usize,
    output: *mut StatelessBuffer,
) -> i32 {
    boundary(|| {
        if model.is_null() || output.is_null() {
            return Err((INVALID_ARGUMENT, "null record handle".into()));
        }
        // SAFETY: The caller supplies valid handles and a readable input span.
        let bytes = unsafe { borrowed(inputs, inputs_len)? };
        let mut packet = Packet::new(bytes);
        let inputs = packet
            .blobs()
            .and_then(|inputs| {
                packet.finish()?;
                Ok(inputs)
            })
            .map_err(|e| (INVALID_ARGUMENT, e.0))?;
        // SAFETY: The caller supplies a live model handle for this operation.
        let host = unsafe { &(*model).host };
        let trace = record(
            host,
            inputs,
            RunConfig {
                strategy: "ffi-batch".into(),
                ..RunConfig::default()
            },
            max_steps,
        )
        .map_err(|e| (MODEL_ERROR, e.to_string()))?;
        // Rust recording preserves a coherent error prefix in an Ok(Trace).
        // The existing ABI promises callback errors return MODEL_ERROR and
        // leave the caller's output unchanged; a prefix is not a successful run.
        if let Termination::ModelError(reason) = &trace.termination {
            return Err((MODEL_ERROR, reason.clone()));
        }
        let failed = matches!(trace.termination, Termination::PropertyFailed);
        let mut encoded = Vec::new();
        trace
            .write_to(&mut encoded)
            .map_err(|e| (TRACE_ERROR, e.to_string()))?;
        // SAFETY: The caller provides an exclusively accessed live output handle.
        unsafe {
            (*output).bytes = encoded;
        }
        Ok(if failed { PROPERTY_FAILED } else { OK })
    })
}

/// Restores and replays a normal trace through the host model. An exact matching
/// failure returns PROPERTY_FAILED; a matching passing prefix returns OK.
/// # Safety
/// The model must be live and the artifact span readable throughout the call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn stateless_replay(
    model: *const StatelessModel,
    artifact: *const u8,
    artifact_len: usize,
) -> i32 {
    boundary(|| {
        if model.is_null() {
            return Err((INVALID_ARGUMENT, "null replay model".into()));
        }
        // SAFETY: The caller supplies a readable artifact span.
        let bytes = unsafe { borrowed(artifact, artifact_len)? };
        let trace = Trace::read_from(bytes, &ReadLimits::default())
            .map_err(|e| (TRACE_ERROR, e.to_string()))?;
        // SAFETY: The caller supplies a live model whose context remains valid.
        let result = replay(
            unsafe { &(*model).host },
            &trace,
            ReplayOptions {
                allow_build_mismatch: false,
            },
        )
        .map_err(|e| (MODEL_ERROR, e.to_string()))?;
        match result.outcome {
            ReplayOutcome::Exact => Ok(if result.failure_reproduced {
                PROPERTY_FAILED
            } else {
                OK
            }),
            ReplayOutcome::Diverged { step, field } => {
                set_error(format!("replay diverged at {step:?}: {field}"));
                Ok(DIVERGED)
            }
            ReplayOutcome::Incompatible { reason } => {
                set_error(reason);
                Ok(INCOMPATIBLE)
            }
        }
    })
}

/// Enumerates the host's finite input domain through optional callback operation
/// 4. On success writes a readable report, plus a standard failure trace or an
/// empty artifact when no failure was found. An OK result may be budget-limited;
/// the report's termination is authoritative. Outputs change only on success.
/// # Safety
/// Model/output handles must be live and exclusively accessed. The two output
/// handles must be distinct. The host enumerator must return complete inputs.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn stateless_enumerate(
    model: *const StatelessModel,
    max_states: usize,
    max_transitions: u64,
    max_depth: usize,
    report_output: *mut StatelessBuffer,
    artifact_output: *mut StatelessBuffer,
) -> i32 {
    boundary(|| {
        if model.is_null()
            || report_output.is_null()
            || artifact_output.is_null()
            || report_output == artifact_output
        {
            return Err((
                INVALID_ARGUMENT,
                "null or aliased enumeration handles".into(),
            ));
        }
        // SAFETY: The caller supplies a live model and context throughout this call.
        let host = unsafe { &(*model).host };
        let config = SearchConfig {
            max_states,
            max_transitions,
            max_depth,
        };
        let report = enumerate(host, config).map_err(|e| (MODEL_ERROR, e.to_string()))?;
        let identity = host.metadata();
        let text = format!(
            "model={:?}\nbuild={:?}\nmodel_version={}\nproperties_version={}\ncodec_version={}\ntermination={:?}\nstates={}\ntransitions={}\nmax_depth_reached={}\nskipped_checks={}\nmax_states={}\nmax_transitions={}\nmax_depth={}\n",
            identity.name,
            identity.build,
            identity.model_version,
            identity.properties_version,
            identity.codec_version,
            report.termination,
            report.states,
            report.transitions,
            report.max_depth_reached,
            report.skipped_checks,
            max_states,
            max_transitions,
            max_depth
        );
        let mut artifact = Vec::new();
        let failed = report.failure.is_some();
        if let Some(failure) = report.failure {
            let count = failure.inputs.len();
            let config = RunConfig {
                strategy: "ffi-enumeration-failure".into(),
                seed: None,
                parameters: vec![
                    ("max_states".into(), max_states.to_string()),
                    ("max_transitions".into(), max_transitions.to_string()),
                    ("max_depth".into(), max_depth.to_string()),
                    ("states".into(), report.states.to_string()),
                    ("transitions".into(), report.transitions.to_string()),
                    (
                        "failure_targets".into(),
                        failure
                            .violations
                            .iter()
                            .map(|failure| format!("{:?}:{}", failure.phase, failure.check.id))
                            .collect::<Vec<_>>()
                            .join(","),
                    ),
                ],
            };
            let trace = record(host, failure.inputs, config, count.saturating_add(1))
                .map_err(|e| (MODEL_ERROR, e.to_string()))?;
            let observed = trace
                .steps
                .last()
                .map_or(&trace.initial_checks, |step| &step.checks);
            let observed_failures: Vec<_> =
                observed.iter().filter(|check| check.is_failure()).collect();
            let expected_failures: Vec<_> = failure
                .violations
                .iter()
                .map(|failure| &failure.check)
                .collect();
            if trace.termination != Termination::PropertyFailed
                || trace.steps.len() != count
                || observed_failures != expected_failures
            {
                return Err((MODEL_ERROR, "enumeration failure did not reproduce while recording; model is nondeterministic".into()));
            }
            trace
                .write_to(&mut artifact)
                .map_err(|e| (TRACE_ERROR, e.to_string()))?;
        }
        // SAFETY: The caller provides two distinct, exclusively accessed outputs;
        // all fallible work completed before replacing their contents.
        unsafe {
            (*report_output).bytes = text.into_bytes();
            (*artifact_output).bytes = artifact;
        }
        Ok(if failed { PROPERTY_FAILED } else { OK })
    })
}

impl HostModel {
    fn call(&self, operation: u32, state: &[u8], input: &[u8]) -> Result<Vec<u8>, ModelError> {
        let mut response = StatelessBuffer { bytes: Vec::new() };
        let dispatch = self
            .callbacks
            .dispatch
            .ok_or_else(|| ModelError::new("missing dispatch"))?;
        // SAFETY: Model creation requires a valid callback/context for the model's
        // lifetime. All borrowed argument spans and response remain live here.
        let status = unsafe {
            dispatch(
                self.callbacks.context,
                operation,
                state.as_ptr(),
                state.len(),
                input.as_ptr(),
                input.len(),
                &mut response,
            )
        };
        if status != OK {
            return Err(ModelError::new(format!(
                "host operation {operation} returned status {status}"
            )));
        }
        if response.bytes.len() > MAX_BYTES {
            return Err(ModelError::new("oversized callback response"));
        }
        Ok(response.bytes)
    }
}

impl Model for HostModel {
    type State = Vec<u8>;
    type Input = Vec<u8>;
    type Output = Vec<u8>;
    fn metadata(&self) -> ModelMetadata {
        self.metadata.clone()
    }
    fn initial_state(&self) -> Result<Self::State, ModelError> {
        self.call(0, &[], &[])
    }
    fn step(
        &self,
        state: &Self::State,
        input: &Self::Input,
    ) -> Result<Transition<Self::State, Self::Output>, ModelError> {
        let bytes = self.call(1, state, input)?;
        decode_transition(&bytes)
    }
    fn check_state(&self, state: &Self::State) -> Result<Vec<Check>, ModelError> {
        decode_checks(&self.call(2, state, &[])?)
    }
    fn check_transition(
        &self,
        before: &Self::State,
        input: &Self::Input,
        transition: &TransitionRef<'_, Self::State, Self::Output>,
    ) -> Result<Vec<Check>, ModelError> {
        let mut packet = Vec::new();
        put_blob(&mut packet, input)?;
        encode_transition(&mut packet, transition)?;
        decode_checks(&self.call(3, before, &packet)?)
    }
}

impl ModelCodec for HostModel {
    fn encode_state(&self, state: &Self::State) -> Result<Vec<u8>, ModelError> {
        Ok(state.clone())
    }
    fn decode_state(&self, bytes: &[u8]) -> Result<Self::State, ModelError> {
        Ok(bytes.to_vec())
    }
    fn encode_input(&self, input: &Self::Input) -> Result<Vec<u8>, ModelError> {
        Ok(input.clone())
    }
    fn decode_input(&self, bytes: &[u8]) -> Result<Self::Input, ModelError> {
        Ok(bytes.to_vec())
    }
    fn encode_output(&self, output: &Self::Output) -> Result<Vec<u8>, ModelError> {
        Ok(output.clone())
    }
}

impl Enumerate for HostModel {
    fn inputs(&self, state: &Self::State) -> Result<Vec<Self::Input>, ModelError> {
        let response = self.call(4, state, &[])?;
        let mut packet = Packet::new(&response);
        let inputs = packet.blobs()?;
        packet.finish()?;
        Ok(inputs)
    }
}

fn encode_transition(
    out: &mut Vec<u8>,
    transition: &TransitionRef<'_, Vec<u8>, Vec<u8>>,
) -> Result<(), ModelError> {
    match &transition.disposition {
        Disposition::Accepted => {
            out.push(0);
            put_blob(out, &[])?;
        }
        Disposition::Rejected(reason) => {
            out.push(1);
            put_blob(out, reason.as_bytes())?;
        }
        Disposition::Ignored(reason) => {
            out.push(2);
            put_blob(out, reason.as_bytes())?;
        }
    }
    put_blob(out, transition.state)?;
    put_count(out, transition.outputs.len())?;
    for output in transition.outputs {
        put_blob(out, output)?;
    }
    Ok(())
}

fn decode_transition(bytes: &[u8]) -> Result<Transition<Vec<u8>, Vec<u8>>, ModelError> {
    let mut packet = Packet::new(bytes);
    let kind = packet.byte()?;
    let reason = packet.string()?;
    let disposition = match kind {
        0 if reason.is_empty() => Disposition::Accepted,
        1 => Disposition::Rejected(reason),
        2 => Disposition::Ignored(reason),
        _ => return Err(ModelError::new("invalid disposition")),
    };
    let state = packet.blob()?.to_vec();
    let outputs = packet.blobs()?;
    packet.finish()?;
    Ok(Transition {
        state,
        outputs,
        disposition,
    })
}

fn decode_checks(bytes: &[u8]) -> Result<Vec<Check>, ModelError> {
    let mut packet = Packet::new(bytes);
    let count = packet.count()?;
    let mut checks = Vec::new();
    for _ in 0..count {
        let id = packet.string()?;
        if id.is_empty() {
            return Err(ModelError::new("empty check identity"));
        }
        let kind = packet.byte()?;
        let details = packet.string()?;
        let status = match kind {
            0 if details.is_empty() => CheckStatus::Passed,
            1 => CheckStatus::Failed(details),
            2 => CheckStatus::Skipped(details),
            _ => return Err(ModelError::new("invalid check status")),
        };
        checks.push(Check {
            id: id.into(),
            status,
        });
    }
    packet.finish()?;
    Ok(checks)
}

fn put_count(out: &mut Vec<u8>, value: usize) -> Result<(), ModelError> {
    let value = u32::try_from(value).map_err(|_| ModelError::new("packet length overflow"))?;
    out.extend_from_slice(&value.to_le_bytes());
    Ok(())
}
fn put_blob(out: &mut Vec<u8>, bytes: &[u8]) -> Result<(), ModelError> {
    put_count(out, bytes.len())?;
    out.extend_from_slice(bytes);
    Ok(())
}

struct Packet<'a> {
    bytes: &'a [u8],
    offset: usize,
}
impl<'a> Packet<'a> {
    fn new(bytes: &'a [u8]) -> Self {
        Self { bytes, offset: 0 }
    }
    fn take(&mut self, len: usize) -> Result<&'a [u8], ModelError> {
        let end = self
            .offset
            .checked_add(len)
            .ok_or_else(|| ModelError::new("packet length overflow"))?;
        let result = self
            .bytes
            .get(self.offset..end)
            .ok_or_else(|| ModelError::new("truncated host packet"))?;
        self.offset = end;
        Ok(result)
    }
    fn byte(&mut self) -> Result<u8, ModelError> {
        Ok(self.take(1)?[0])
    }
    fn count(&mut self) -> Result<usize, ModelError> {
        let value = u32::from_le_bytes(self.take(4)?.try_into().expect("four bytes")) as usize;
        if value > MAX_ITEMS {
            return Err(ModelError::new("too many packet items"));
        }
        Ok(value)
    }
    fn blob(&mut self) -> Result<&'a [u8], ModelError> {
        let len = u32::from_le_bytes(self.take(4)?.try_into().expect("four bytes")) as usize;
        self.take(len)
    }
    fn blobs(&mut self) -> Result<Vec<Vec<u8>>, ModelError> {
        let count = self.count()?;
        let mut values = Vec::new();
        for _ in 0..count {
            values.push(self.blob()?.to_vec());
        }
        Ok(values)
    }
    fn string(&mut self) -> Result<String, ModelError> {
        String::from_utf8(self.blob()?.to_vec()).map_err(|e| ModelError::new(e.to_string()))
    }
    fn finish(&self) -> Result<(), ModelError> {
        if self.offset != self.bytes.len() {
            return Err(ModelError::new("trailing host packet bytes"));
        }
        Ok(())
    }
}

#[cfg(target_arch = "wasm32")]
#[link(wasm_import_module = "stateless_host")]
unsafe extern "C" {
    #[link_name = "dispatch"]
    fn host_dispatch(
        context: *mut std::ffi::c_void,
        operation: u32,
        state: *const u8,
        state_len: usize,
        input: *const u8,
        input_len: usize,
        response: *mut std::ffi::c_void,
    ) -> i32;
}

#[cfg(target_arch = "wasm32")]
unsafe extern "C" fn wasm_dispatch(
    context: *mut std::ffi::c_void,
    operation: u32,
    state: *const u8,
    state_len: usize,
    input: *const u8,
    input_len: usize,
    response: *mut StatelessBuffer,
) -> i32 {
    // SAFETY: The wrapper forwards the valid callback spans/response to the host
    // import; the host follows the same borrowing contract as a C callback.
    unsafe {
        host_dispatch(
            context,
            operation,
            state,
            state_len,
            input,
            input_len,
            response.cast(),
        )
    }
}

/// Browser entrypoint: uses the synchronous stateless_host.dispatch Wasm import.
/// # Safety
/// Same pointer and context lifetime requirements as stateless_model_new.
#[cfg(target_arch = "wasm32")]
#[unsafe(no_mangle)]
pub unsafe extern "C" fn stateless_wasm_model_new(
    context: *mut std::ffi::c_void,
    name: *const u8,
    name_len: usize,
    build: *const u8,
    build_len: usize,
    model_version: u32,
    properties_version: u32,
    codec_version: u32,
    output: *mut *mut StatelessModel,
) -> i32 {
    let callbacks = StatelessCallbacks {
        abi_version: ABI_VERSION,
        struct_size: std::mem::size_of::<StatelessCallbacks>() as u32,
        context,
        dispatch: Some(wasm_dispatch),
    };
    // SAFETY: The wrapper forwards its caller's validated lifetime contract and
    // a valid local callback table, copied synchronously by model_new.
    unsafe {
        stateless_model_new(
            &callbacks,
            name,
            name_len,
            build,
            build_len,
            model_version,
            properties_version,
            codec_version,
            output,
        )
    }
}

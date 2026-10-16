//! Synchronous ABI v2. See `bindings/c/stateless_v2.h` for the wire contract.
//!
//! Values are immutable host snapshots with explicit ownership. Numeric handles
//! are validated; arbitrary foreign pointers cannot be validated. Callers must
//! supply readable/writable spans of the declared length and prevent exceptions
//! from crossing callbacks. Handles from separately loaded engines are outside
//! the contract, even if their numeric representations happen to coincide.

use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::rc::{Rc, Weak};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread::ThreadId;

use crate::explore::Failure;
use crate::model::{Check, Disposition, ModelError, ModelMetadata, Rng};

pub const ABI_VERSION: u32 = 2;
pub const OK: i32 = 0;
pub const PROPERTY_FAILED: i32 = 1;
pub const DIVERGED: i32 = 2;
pub const INCOMPATIBLE: i32 = 3;
pub const INVALID_ARGUMENT: i32 = 10;
pub const MODEL_ERROR: i32 = 11;
pub const TRACE_ERROR: i32 = 12;
pub const PANIC: i32 = 13;
pub const INVALID_HANDLE: i32 = 14;
pub const WRONG_THREAD: i32 = 15;
pub const REENTRANT: i32 = 16;
pub const UNSUPPORTED: i32 = 17;
pub const LIMIT: i32 = 18;
pub const CAP_CHECK: u64 = 1;
pub const CAP_ENUMERATE: u64 = 2;
pub const CAP_CODEC: u64 = 4;
pub const CAP_GENERATE: u64 = 8;
pub const CAP_SHRINK: u64 = 16;
pub const CAP_HASH: u64 = 32;
pub const CAP_ESTIMATE: u64 = 64;
pub const CAP_RING: u64 = 128;
pub const CAP_AUTO_GENERATE: u64 = 256;
const CAPABILITIES: u64 = CAP_CHECK
    | CAP_ENUMERATE
    | CAP_CODEC
    | CAP_GENERATE
    | CAP_SHRINK
    | CAP_HASH
    | CAP_ESTIMATE
    | CAP_RING
    | CAP_AUTO_GENERATE;
pub const STATE: u32 = 1;
pub const INPUT: u32 = 2;
pub const OUTPUT: u32 = 3;
pub const CURSOR: u32 = 4;

pub type Dispatch =
    unsafe extern "C" fn(u64, u64, u64, u32, *const u64, u32, *const u8, u64) -> i32;
pub type Release = unsafe extern "C" fn(u64, u32, u64);

#[repr(C)]
#[derive(Clone, Copy)]
pub struct StatelessV2Callbacks {
    pub abi_version: u32,
    pub struct_size: u32,
    pub capabilities: u64,
    pub context: u64,
    pub dispatch: Option<Dispatch>,
    pub release: Option<Release>,
    pub max_values: u64,
    pub max_items: u64,
    pub max_bytes: u64,
}

#[derive(Clone, Debug)]
pub(super) struct ApiError {
    status: i32,
    operation: u32,
    phase: u32,
    sequence: u64,
    message: String,
}
pub(super) type ApiResult<T> = Result<T, ApiError>;
fn error(status: i32, message: impl Into<String>) -> ApiError {
    ApiError {
        status,
        operation: 0,
        phase: 0,
        sequence: 0,
        message: message.into(),
    }
}

thread_local! {
    static SESSIONS: RefCell<HashMap<u64, Rc<Session>>> = RefCell::new(HashMap::new());
    static LAST_ERROR: RefCell<Option<ApiError>> = const { RefCell::new(None) };
}
struct ThreadEntry {
    owner: ThreadId,
    cancelled: Arc<AtomicBool>,
}
static OWNERS: OnceLock<Mutex<HashMap<u64, ThreadEntry>>> = OnceLock::new();
static NEXT_HANDLE: AtomicU64 = AtomicU64::new(1);
fn next_handle() -> ApiResult<u64> {
    NEXT_HANDLE
        .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |value| {
            value.checked_add(1)
        })
        .map_err(|_| error(LIMIT, "engine handle space exhausted"))
}
fn owners() -> &'static Mutex<HashMap<u64, ThreadEntry>> {
    OWNERS.get_or_init(|| Mutex::new(HashMap::new()))
}
fn boundary(operation: u32, call: impl FnOnce() -> ApiResult<i32>) -> i32 {
    LAST_ERROR.with(|slot| *slot.borrow_mut() = None);
    let outcome = catch_unwind(AssertUnwindSafe(call));
    match outcome {
        Ok(Ok(status)) => {
            if status >= INVALID_ARGUMENT {
                LAST_ERROR.with(|slot| {
                    if let Some(err) = slot.borrow_mut().as_mut() {
                        err.operation = operation;
                    }
                });
            }
            status
        }
        failure => {
            let mut err = match failure {
                Ok(Err(err)) => err,
                _ => error(PANIC, "Rust panic in ABI v2 operation"),
            };
            err.operation = operation;
            let status = err.status;
            LAST_ERROR.with(|slot| *slot.borrow_mut() = Some(err));
            status
        }
    }
}

macro_rules! api {
    ($op:expr, fn $name:ident($($arg:ident: $ty:ty),* $(,)?) $body:block) => {
        /// # Safety
        /// Foreign spans and output pointers must be valid for their declared
        /// size and lifetime. See `bindings/c/stateless_v2.h` for each argument.
        #[unsafe(no_mangle)]
        #[allow(unused_unsafe)]
        pub unsafe extern "C" fn $name($($arg:$ty),*) -> i32 {
            boundary($op, || {
                // SAFETY: The caller supplies the documented valid spans and
                // outputs; checked helpers validate nulls, lengths and handles.
                unsafe { $body }
            })
        }
    };
}

pub(super) struct Session {
    handle: u64,
    callbacks: StatelessV2Callbacks,
    metadata: ModelMetadata,
    busy: Cell<bool>,
    cancelled: Arc<AtomicBool>,
    values: RefCell<HashMap<u64, HostValue>>,
    results: RefCell<HashMap<u64, Rc<ResultData>>>,
    recorders: RefCell<HashMap<u64, crate::monitor::Recorder>>,
    roots: RefCell<HashSet<u64>>,
    active: RefCell<Option<Invocation>>,
    callback_error: RefCell<Option<ApiError>>,
}
impl Drop for Session {
    fn drop(&mut self) {
        // TLS teardown also destroys unclosed sessions. Remove the thread-safe
        // cancellation entry before field teardown invokes any root releases.
        // Explicit session_close already removed it; removal is idempotent.
        if let Ok(mut registry) = owners().lock() {
            registry.remove(&self.handle);
        }
    }
}
struct Busy<'a>(&'a Session);
impl Drop for Busy<'_> {
    fn drop(&mut self) {
        self.0.busy.set(false);
    }
}
fn session(handle: u64) -> ApiResult<Rc<Session>> {
    if let Some(session) = SESSIONS.with(|sessions| sessions.borrow().get(&handle).cloned()) {
        return Ok(session);
    }
    let registered = owners()
        .lock()
        .map_err(|_| error(PANIC, "session registry poisoned"))?;
    if registered
        .get(&handle)
        .is_some_and(|entry| entry.owner != std::thread::current().id())
    {
        Err(error(WRONG_THREAD, "session belongs to another thread"))
    } else {
        Err(error(INVALID_HANDLE, "unknown or closed session"))
    }
}
pub(super) fn run(
    handle: u64,
    capability: u64,
    call: impl FnOnce(&Rc<Session>) -> ApiResult<i32>,
) -> ApiResult<i32> {
    let session = session(handle)?;
    if session.busy.replace(true) {
        return Err(error(REENTRANT, "session is executing or releasing values"));
    }
    let _busy = Busy(&session);
    if session.callbacks.capabilities & capability != capability {
        return Err(error(UNSUPPORTED, "model lacks required capability"));
    }
    *session.callback_error.borrow_mut() = None;
    call(&session)
}

#[derive(Clone)]
pub(super) struct HostValue(Rc<HostRoot>);
struct HostRoot {
    token: u64,
    kind: u32,
    session: Weak<Session>,
    callbacks: StatelessV2Callbacks,
}
impl Drop for HostRoot {
    fn drop(&mut self) {
        if let Some(session) = self.session.upgrade() {
            session.roots.borrow_mut().remove(&self.token);
        }
        // SAFETY: Registration requires a nonthrowing release callback and a
        // context that outlives session closure. The final owner releases once.
        unsafe { self.callbacks.release.unwrap()(self.callbacks.context, self.kind, self.token) };
    }
}
impl HostValue {
    fn token(&self) -> u64 {
        self.0.token
    }
    fn kind(&self) -> u32 {
        self.0.kind
    }
}
impl Session {
    fn own(self: &Rc<Self>, kind: u32, token: u64) -> ApiResult<HostValue> {
        if !(STATE..=CURSOR).contains(&kind) || token == 0 {
            return Err(error(
                INVALID_ARGUMENT,
                "invalid value kind or zero host token",
            ));
        }
        let mut roots = self.roots.borrow_mut();
        if roots.contains(&token) {
            return Err(error(INVALID_HANDLE, "host token already owned"));
        }
        if roots.len() as u64 >= self.callbacks.max_values {
            return Err(error(LIMIT, "retained host root limit reached"));
        }
        roots.insert(token);
        Ok(HostValue(Rc::new(HostRoot {
            token,
            kind,
            session: Rc::downgrade(self),
            callbacks: self.callbacks,
        })))
    }
    fn value(&self, handle: u64, kind: u32) -> ApiResult<HostValue> {
        let values = self.values.borrow();
        let value = values
            .get(&handle)
            .ok_or_else(|| error(INVALID_HANDLE, "unknown value handle"))?;
        if value.kind() != kind {
            return Err(error(INVALID_HANDLE, "value kind mismatch"));
        }
        Ok(value.clone())
    }
    fn model_error(&self, err: ModelError) -> ApiError {
        if let Some(mut detail) = self.callback_error.borrow_mut().take() {
            detail.message = err.to_string();
            detail
        } else {
            error(MODEL_ERROR, err.to_string())
        }
    }
    fn result(&self, handle: u64) -> ApiResult<Rc<ResultData>> {
        self.results
            .borrow()
            .get(&handle)
            .cloned()
            .ok_or_else(|| error(INVALID_HANDLE, "unknown result handle"))
    }
}

struct Invocation {
    handle: u64,
    operation: u32,
    values: Vec<HostValue>,
    checks: Vec<Check>,
    bytes: Vec<u8>,
    scalar: Option<u64>,
    disposition: Option<Disposition>,
    failure: Option<ApiError>,
    rng: Option<Rng>,
    retained_bytes: u64,
}
impl Invocation {
    fn new(handle: u64, operation: u32, rng: Option<Rng>) -> Self {
        Self {
            handle,
            operation,
            values: Vec::new(),
            checks: Vec::new(),
            bytes: Vec::new(),
            scalar: None,
            disposition: None,
            failure: None,
            rng,
            retained_bytes: 0,
        }
    }
}

#[derive(Default)]
pub(super) struct ResultData {
    status: i32,
    kind: u64,
    scalars: HashMap<u32, u64>,
    checks: Vec<(u32, Check)>,
    failure: Option<Failure<HostValue>>,
    pub(super) blob: Vec<u8>,
    message: String,
    divergence_field: String,
}
pub(super) unsafe fn publish(
    session: &Rc<Session>,
    result: ResultData,
    output: *mut u64,
) -> ApiResult<i32> {
    if session.results.borrow().len() as u64 >= session.callbacks.max_items {
        return Err(error(LIMIT, "result handle limit reached"));
    }
    let handle = next_handle()?;
    let status = result.status;
    if status >= INVALID_ARGUMENT {
        let mut detail = session
            .callback_error
            .borrow_mut()
            .take()
            .unwrap_or_else(|| error(status, &result.message));
        detail.message = result.message.clone();
        LAST_ERROR.with(|slot| *slot.borrow_mut() = Some(detail));
    }
    session.results.borrow_mut().insert(handle, Rc::new(result));
    // SAFETY: The enclosing ABI entry validates the caller's writable output.
    unsafe {
        *output = handle;
    }
    Ok(status)
}

unsafe fn clear_output(output: *mut u64) -> ApiResult<()> {
    if output.is_null() {
        return Err(error(INVALID_ARGUMENT, "null output pointer"));
    }
    // SAFETY: Caller promises a valid aligned writable output.
    unsafe {
        *output = 0;
    }
    Ok(())
}
unsafe fn span<'a>(data: *const u8, len: u64, maximum: u64) -> ApiResult<&'a [u8]> {
    if len > maximum || len > isize::MAX as u64 || (len != 0 && data.is_null()) {
        return Err(error(INVALID_ARGUMENT, "invalid or oversized byte span"));
    }
    if len == 0 {
        return Ok(&[]);
    }
    // SAFETY: Caller promises a readable span, and its size fits Rust slices.
    Ok(unsafe { std::slice::from_raw_parts(data, len as usize) })
}
unsafe fn string(data: *const u8, len: u64, maximum: u64) -> ApiResult<String> {
    // SAFETY: The caller supplies the byte span required by span.
    let bytes = unsafe { span(data, len, maximum)? };
    std::str::from_utf8(bytes)
        .map(str::to_owned)
        .map_err(|_| error(INVALID_ARGUMENT, "invalid UTF-8"))
}
unsafe fn handles<'a>(data: *const u64, len: u64, maximum: u64) -> ApiResult<&'a [u64]> {
    if len > maximum || len > isize::MAX as u64 / 8 || (len != 0 && data.is_null()) {
        return Err(error(INVALID_ARGUMENT, "invalid or oversized handle span"));
    }
    if len == 0 {
        return Ok(&[]);
    }
    // SAFETY: Caller promises an aligned readable span of handle elements.
    Ok(unsafe { std::slice::from_raw_parts(data, len as usize) })
}
unsafe fn copy_bytes(
    bytes: &[u8],
    data: *mut u8,
    capacity: u64,
    written: *mut u64,
) -> ApiResult<i32> {
    if written.is_null() {
        return Err(error(INVALID_ARGUMENT, "null byte count output"));
    }
    // SAFETY: Caller promises a valid writable count output.
    unsafe {
        *written = bytes.len() as u64;
    }
    if data.is_null() && capacity == 0 {
        return Ok(OK);
    }
    if capacity < bytes.len() as u64 {
        return Err(error(LIMIT, "output buffer too small"));
    }
    if data.is_null() {
        return Err(error(INVALID_ARGUMENT, "null byte output"));
    }
    // SAFETY: The caller's writable span is large enough; copy permits overlap.
    unsafe {
        std::ptr::copy(bytes.as_ptr(), data, bytes.len());
    }
    Ok(OK)
}

mod adapter;
mod api;
mod portable;
pub use api::*;

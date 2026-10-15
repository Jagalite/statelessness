//! Exercise the public numeric-handle ABI, including callback-owned roots.
use stateless::ffi_v2::*;
use stateless::trace::{ReadLimits, Termination, Trace};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::ptr;

#[derive(Clone)]
enum Value {
    Number(i64),
    Cursor { values: Vec<i64>, position: usize },
}

struct Host {
    next: Cell<u64>,
    roots: RefCell<HashMap<u64, (u32, Value)>>,
    released: Cell<usize>,
    calls: RefCell<Vec<u32>>,
    fault: Cell<u32>,
    mode: Cell<u32>,
    failure: Cell<i64>,
    reentry: RefCell<Vec<i32>>,
}
impl Host {
    fn new() -> Box<Self> {
        Box::new(Self {
            next: Cell::new(1),
            roots: RefCell::default(),
            released: Cell::new(0),
            calls: RefCell::default(),
            fault: Cell::new(0),
            mode: Cell::new(0),
            failure: Cell::new(3),
            reentry: RefCell::default(),
        })
    }
    fn root(&self, kind: u32, value: Value) -> u64 {
        let token = self.next.get();
        self.next.set(token + 1);
        self.roots.borrow_mut().insert(token, (kind, value));
        token
    }
    fn number(&self, token: u64) -> Option<i64> {
        match self.roots.borrow().get(&token)?.1 {
            Value::Number(value) => Some(value),
            _ => None,
        }
    }
    fn publish(&self, session: u64, invocation: u64, kind: u32, value: Value) -> i32 {
        let token = self.root(kind, value);
        // SAFETY: Called only during this session's live dispatch invocation.
        let status = unsafe { stateless_v2_callback_value(session, invocation, kind, token) };
        if status != OK {
            self.roots.borrow_mut().remove(&token);
        }
        status
    }
    fn imported(&self, session: u64, kind: u32, value: Value) -> u64 {
        let token = self.root(kind, value);
        let mut handle = 0;
        assert_eq!(
            // SAFETY: The host root and scalar output remain live for import.
            unsafe { stateless_v2_value_import(session, kind, token, &mut handle) },
            OK
        );
        handle
    }
    fn session(&self, capabilities: u64, max_items: u64) -> u64 {
        let table = StatelessV2Callbacks {
            abi_version: 2,
            struct_size: std::mem::size_of::<StatelessV2Callbacks>() as u32,
            capabilities,
            context: ptr::from_ref(self) as usize as u64,
            dispatch: Some(dispatch),
            release: Some(release),
            max_values: 1000,
            max_items,
            max_bytes: 1024 * 1024,
        };
        let mut session = 0;
        assert_eq!(
            // SAFETY: Host remains boxed until the session is closed; table is copied.
            unsafe {
                stateless_v2_session_new(
                    &table,
                    b"v2".as_ptr(),
                    2,
                    b"build".as_ptr(),
                    5,
                    1,
                    1,
                    1,
                    &mut session,
                )
            },
            OK
        );
        session
    }
    fn close(&self, session: u64) {
        // SAFETY: This test owns the session and closes it exactly once.
        assert_eq!(unsafe { stateless_v2_session_close(session) }, OK);
        assert!(
            self.roots.borrow().is_empty(),
            "host roots leaked at session closure"
        );
    }
}

unsafe extern "C" fn release(context: u64, kind: u32, token: u64) {
    // SAFETY: Each test keeps its boxed Host alive through final session release.
    let host = unsafe { &*(context as usize as *const Host) };
    let value = host.roots.borrow_mut().remove(&token);
    if value.as_ref().is_none_or(|value| value.0 != kind) {
        host.reentry.borrow_mut().push(-999);
    }
    host.released.set(host.released.get() + 1);
}

unsafe extern "C" fn dispatch(
    context: u64,
    session: u64,
    invocation: u64,
    operation: u32,
    args: *const u64,
    nargs: u32,
    data: *const u8,
    nbytes: u64,
) -> i32 {
    // SAFETY: The session retains Host and supplies valid borrowed spans.
    let (host, args, bytes) = unsafe {
        (
            &*(context as usize as *const Host),
            std::slice::from_raw_parts(args, nargs as usize),
            std::slice::from_raw_parts(data, nbytes as usize),
        )
    };
    host.calls.borrow_mut().push(operation);
    if host.fault.get() == operation {
        return 77;
    }
    // SAFETY: All builders receive this active callback's session/invocation;
    // byte spans and scalar output storage remain live throughout each call.
    unsafe {
        match operation {
            1 => host.publish(session, invocation, STATE, Value::Number(0)),
            2 => {
                let (Some(state), Some(input)) = (host.number(args[0]), host.number(args[1]))
                else {
                    return 78;
                };
                let status = host.publish(session, invocation, STATE, Value::Number(state + input));
                if status != OK {
                    return status;
                }
                if host.mode.get() == 8 {
                    return 79;
                }
                host.publish(session, invocation, OUTPUT, Value::Number(state + input))
            }
            3 => {
                if host.mode.get() == 6 {
                    let mut nested = 123;
                    host.reentry.borrow_mut().extend([
                        stateless_v2_session_close(session),
                        stateless_v2_check_initial(session, 0, &mut nested),
                    ]);
                    if nested != 0 {
                        host.reentry.borrow_mut().push(-998);
                    }
                }
                let Some(state) = host.number(args[0]) else {
                    return 78;
                };
                let failed = state >= host.failure.get();
                let id: &[u8] = if host.mode.get() == 13 { b"" } else { b"bound" };
                let details: &[u8] = if failed { b"too large" } else { b"" };
                let status = stateless_v2_callback_check(
                    session,
                    invocation,
                    u32::from(failed),
                    id.as_ptr(),
                    id.len() as u64,
                    details.as_ptr(),
                    details.len() as u64,
                );
                if host.mode.get() == 3 {
                    let _ = stateless_v2_callback_check(
                        session,
                        invocation,
                        99,
                        b"bad".as_ptr(),
                        3,
                        ptr::null(),
                        0,
                    );
                    return OK; // Deliberately ignore the builder failure.
                }
                if host.mode.get() == 7 {
                    let _ = stateless_v2_callback_check(
                        session,
                        invocation,
                        0,
                        b"extra".as_ptr(),
                        5,
                        ptr::null(),
                        0,
                    );
                    return OK;
                }
                if host.mode.get() == 11 {
                    let _ = stateless_v2_callback_scalar(session, invocation + 1, 0);
                    return OK;
                }
                status
            }
            4 => OK,
            5 | 17 => {
                let same = host.number(args[0]) == host.number(args[1]);
                stateless_v2_callback_scalar(session, invocation, u64::from(same))
            }
            6 => stateless_v2_callback_scalar(session, invocation, 0),
            7 => stateless_v2_callback_scalar(session, invocation, 8),
            8 => {
                let Some(state) = host.number(args[0]) else {
                    return 78;
                };
                host.publish(
                    session,
                    invocation,
                    CURSOR,
                    Value::Cursor {
                        values: if state < 3 { vec![1] } else { vec![] },
                        position: 0,
                    },
                )
            }
            9 => {
                let next = {
                    let mut roots = host.roots.borrow_mut();
                    let Some((_, Value::Cursor { values, position })) = roots.get_mut(&args[0])
                    else {
                        return 78;
                    };
                    if (host.mode.get() == 1 && *position == 1) || host.mode.get() == 2 {
                        return 80;
                    }
                    let value = if host.mode.get() == 14 {
                        Some(1)
                    } else {
                        values.get(*position).copied()
                    };
                    *position += 1;
                    value
                };
                match next {
                    Some(value) => host.publish(session, invocation, INPUT, Value::Number(value)),
                    None => {
                        if host.mode.get() == 9 {
                            let _ = stateless_v2_cancel(session);
                        }
                        OK
                    }
                }
            }
            10 | 12 | 14 => {
                let Some(value) = host.number(args[0]) else {
                    return 78;
                };
                stateless_v2_callback_bytes(session, invocation, value.to_le_bytes().as_ptr(), 8)
            }
            11 | 13 => {
                let Ok(raw) = <[u8; 8]>::try_from(bytes) else {
                    return 78;
                };
                host.publish(
                    session,
                    invocation,
                    if operation == 11 { STATE } else { INPUT },
                    Value::Number(i64::from_le_bytes(raw)),
                )
            }
            15 => {
                let Some(state) = host.number(args[0]) else {
                    return 78;
                };
                let mut random = 0;
                let status = stateless_v2_callback_rng(session, invocation, &mut random);
                if status != OK || state >= 3 {
                    return status;
                }
                host.publish(session, invocation, INPUT, Value::Number(1))
            }
            16 => stateless_v2_callback_scalar(
                session,
                invocation,
                u64::from(host.number(args[1]) == Some(1)),
            ),
            18 => host.publish(
                session,
                invocation,
                CURSOR,
                Value::Cursor {
                    values: vec![],
                    position: 0,
                },
            ),
            _ => 81,
        }
    }
}

fn scalar(session: u64, result: u64, field: u32) -> u64 {
    let mut value = 0;
    assert_eq!(
        // SAFETY: Tests supply live session/result handles and a scalar output.
        unsafe { stateless_v2_result_scalar(session, result, field, &mut value) },
        OK
    );
    value
}
fn bytes(session: u64, result: u64) -> Vec<u8> {
    let mut len = 0;
    assert_eq!(
        // SAFETY: The null zero-capacity span requests the result size only.
        unsafe { stateless_v2_result_copy(session, result, 1, 0, ptr::null_mut(), 0, &mut len) },
        OK
    );
    let mut bytes = vec![0; len as usize];
    assert_eq!(
        // SAFETY: The allocated output has exactly the requested capacity.
        unsafe {
            stateless_v2_result_copy(session, result, 1, 0, bytes.as_mut_ptr(), len, &mut len)
        },
        OK
    );
    bytes
}

#[test]
fn v2_keeps_v1_and_negotiates_session_capabilities() {
    assert_eq!(stateless::ffi::stateless_abi_version(), 1);
    assert_eq!(stateless_v2_abi_version(), 2);
    let host = Host::new();
    let session = host.session(CAP_CHECK, 1000);
    let mut result = 999;
    assert_eq!(
        // SAFETY: Scalar output is valid and this test owns the session.
        unsafe { stateless_v2_enumerate(session, 10, 10, 10, u64::MAX, 0, &mut result) },
        UNSUPPORTED
    );
    assert_eq!(result, 0);
    assert!(host.calls.borrow().is_empty());
    host.close(session);
}

#[test]
fn snapshot_witness_handles_outlive_result_and_close_releases_them_once() {
    let host = Host::new();
    let session = host.session(CAP_CHECK | CAP_ENUMERATE, 1000);
    let mut result = 0;
    assert_eq!(
        // SAFETY: This test owns the session and writable output.
        unsafe { stateless_v2_enumerate(session, 20, 20, 20, u64::MAX, 0, &mut result) },
        PROPERTY_FAILED
    );
    assert_eq!(scalar(session, result, 11), 3);
    assert_eq!(host.roots.borrow().len(), 3); // Only failure inputs remain rooted.
    let mut input = 0;
    // SAFETY: Result remains live; the returned independent value is released below.
    unsafe {
        assert_eq!(
            stateless_v2_result_input(session, result, 0, &mut input),
            OK
        );
        assert_eq!(stateless_v2_result_release(session, result), OK);
    }
    assert_eq!(host.roots.borrow().len(), 1);
    let mut kind = 0;
    let mut token = 0;
    // SAFETY: Independent input handle remains live after result release.
    unsafe {
        assert_eq!(
            stateless_v2_value_host_token(session, input, &mut kind, &mut token),
            OK
        );
        assert_eq!(stateless_v2_value_release(session, input), OK);
        assert_eq!(stateless_v2_value_release(session, input), INVALID_HANDLE);
    }
    assert_eq!(kind, INPUT);
    assert_ne!(token, 0);
    assert!(host.roots.borrow().is_empty());
    host.close(session);
    assert!(!host.reentry.borrow().contains(&-999));
}

#[test]
fn callback_failure_after_partial_publication_and_boundary_probe_never_publish_success() {
    for (mode, fault, depth, edges) in [
        (8, 0, 10, 10),
        (0, 5, 10, 10),
        (0, 6, 10, 10),
        (1, 0, 10, 1),
        (2, 0, 0, 10),
    ] {
        let host = Host::new();
        host.mode.set(mode);
        host.fault.set(fault);
        let session = host.session(CAP_CHECK | CAP_ENUMERATE | CAP_HASH, 1000);
        let mut result = 999;
        assert_eq!(
            // SAFETY: Valid session and output; callbacks deliberately return errors.
            unsafe { stateless_v2_enumerate(session, 20, edges, depth, u64::MAX, 0, &mut result) },
            MODEL_ERROR
        );
        assert_eq!(result, 0);
        assert!(host.roots.borrow().is_empty());
        host.close(session);
    }
}

#[test]
fn ignored_builder_failures_are_sticky_and_partial_checks_are_not_results() {
    for (mode, quota, expected) in [
        (3, 1000, INVALID_ARGUMENT),
        (7, 1, LIMIT),
        (11, 1000, INVALID_HANDLE),
    ] {
        let host = Host::new();
        host.mode.set(mode);
        let session = host.session(CAP_CHECK, quota);
        let mut result = 999;
        assert_eq!(
            // SAFETY: Valid output; callback intentionally violates builder contracts.
            unsafe { stateless_v2_check_initial(session, 0, &mut result) },
            expected
        );
        assert_eq!(result, 0);
        assert!(host.roots.borrow().is_empty());
        host.close(session);
    }
}

#[test]
fn nested_runs_and_close_are_rejected_without_damaging_the_outer_session() {
    let host = Host::new();
    host.mode.set(6);
    let session = host.session(CAP_CHECK, 1000);
    let mut result = 0;
    assert_eq!(
        // SAFETY: Host callback intentionally attempts forbidden reentry.
        unsafe { stateless_v2_check_initial(session, 0, &mut result) },
        OK
    );
    assert_eq!(*host.reentry.borrow(), [REENTRANT, REENTRANT]);
    host.close(session);
}

#[test]
fn wrong_thread_wrong_session_wrong_kind_and_stale_handles_are_rejected() {
    let host = Host::new();
    let first = host.session(CAP_CHECK, 1000);
    let second = host.session(CAP_CHECK, 1000);
    let input = host.imported(first, INPUT, Value::Number(1));
    let mut result = 0;
    // SAFETY: Numeric handles are deliberately mismatched, not invalid pointers.
    unsafe {
        assert_eq!(
            stateless_v2_check_initial(first, input, &mut result),
            INVALID_HANDLE
        );
        assert_eq!(stateless_v2_value_release(second, input), INVALID_HANDLE);
    }
    let wrong = std::thread::spawn(move || {
        let mut capabilities = 0;
        // SAFETY: Wrong-thread lookup rejects before touching the callback context.
        unsafe { stateless_v2_session_capabilities(first, &mut capabilities) }
    })
    .join()
    .unwrap();
    assert_eq!(wrong, WRONG_THREAD);
    // SAFETY: Close on the owning thread releases first's imported input.
    assert_eq!(unsafe { stateless_v2_session_close(first) }, OK);
    // SAFETY: Stale numeric handle must be rejected without dereferencing it.
    assert_eq!(unsafe { stateless_v2_session_close(first) }, INVALID_HANDLE);
    host.close(second);
}

#[test]
fn observed_checks_preserve_supplied_values_and_never_call_initial_or_step() {
    let host = Host::new();
    host.mode.set(13);
    let session = host.session(CAP_CHECK, 1000);
    let before = host.imported(session, STATE, Value::Number(1));
    let input = host.imported(session, INPUT, Value::Number(1));
    let after = host.imported(session, STATE, Value::Number(2));
    let output_value = host.imported(session, OUTPUT, Value::Number(2));
    let mut result = 0;
    assert_eq!(
        // SAFETY: Values match their required kinds and all spans are borrowed live.
        unsafe {
            stateless_v2_check_observed(
                session,
                before,
                input,
                after,
                &output_value,
                1,
                0,
                ptr::null(),
                0,
                1,
                1,
                1,
                &mut result,
            )
        },
        OK
    );
    assert_eq!(*host.calls.borrow(), [3, 4]);
    assert_eq!(scalar(session, result, 10), 1);
    assert_eq!(host.roots.borrow().len(), 4);
    host.close(session);
}

#[test]
fn lazy_record_error_retains_a_coherent_prefix_and_exact_replay_uses_the_codec() {
    let host = Host::new();
    let session = host.session(CAP_CHECK | CAP_CODEC, 1000);
    let cursor = host.imported(
        session,
        CURSOR,
        Value::Cursor {
            values: vec![1, 1],
            position: 0,
        },
    );
    host.mode.set(1);
    let mut recorded = 0;
    assert_eq!(
        // SAFETY: Cursor is imported and output remains writable.
        unsafe { stateless_v2_record_cursor(session, cursor, 10, &mut recorded) },
        MODEL_ERROR
    );
    assert_ne!(recorded, 0);
    let artifact = bytes(session, recorded);
    let trace = Trace::read_from(artifact.as_slice(), &ReadLimits::default()).unwrap();
    assert_eq!(trace.steps.len(), 1);
    assert!(matches!(trace.termination, Termination::ModelError(_)));
    host.mode.set(0);
    let mut replayed = 0;
    assert_eq!(
        // SAFETY: The artifact span lives throughout replay.
        unsafe {
            stateless_v2_replay(
                session,
                artifact.as_ptr(),
                artifact.len() as u64,
                0,
                &mut replayed,
            )
        },
        OK
    );
    assert_eq!(scalar(session, replayed, 14), 1);
    host.close(session);
}

#[test]
fn cursor_cancellation_cannot_report_exhaustion_and_can_be_reset() {
    let host = Host::new();
    host.mode.set(9);
    host.failure.set(i64::MAX);
    let session = host.session(CAP_CHECK | CAP_ENUMERATE, 1000);
    let mut result = 0;
    // SAFETY: Callback signals cancellation through its explicitly allowed API.
    unsafe {
        assert_eq!(
            stateless_v2_enumerate(session, 20, 20, 20, u64::MAX, 0, &mut result),
            OK
        );
        assert_eq!(scalar(session, result, 3), 7);
        assert_eq!(stateless_v2_cancel_reset(session), OK);
    }
    host.mode.set(0);
    assert_eq!(
        // SAFETY: Same valid session after owner-thread reset.
        unsafe { stateless_v2_enumerate(session, 20, 20, 20, u64::MAX, 0, &mut result) },
        OK
    );
    assert_eq!(scalar(session, result, 3), 0);
    host.close(session);
}

#[test]
fn fuzz_and_shrink_keep_owned_witnesses_and_use_semantic_input_callbacks() {
    let host = Host::new();
    let session = host.session(CAP_CHECK | CAP_GENERATE | CAP_SHRINK, 1000);
    let mut found = 0;
    let mut shrunk = 0;
    // SAFETY: All callback families needed by these negotiated capabilities exist.
    unsafe {
        assert_eq!(
            stateless_v2_fuzz(session, 123, 3, 10, 50, 0, 0, &mut found),
            PROPERTY_FAILED
        );
        assert_eq!(
            stateless_v2_shrink(session, found, 50, 1000, 0, &mut shrunk),
            PROPERTY_FAILED
        );
    }
    assert_eq!(scalar(session, found, 11), 3);
    assert_eq!(scalar(session, shrunk, 11), 3);
    assert_eq!(scalar(session, shrunk, 19), 1);
    assert!(host.calls.borrow().contains(&16));
    host.close(session);
}

#[test]
fn endless_duplicate_simplifications_fail_instead_of_bypassing_work_limits() {
    let host = Host::new();
    let session = host.session(CAP_CHECK | CAP_GENERATE | CAP_SHRINK, 4);
    let mut found = 0;
    let mut shrunk = 99;
    // SAFETY: Both calls use the owned session and writable result scalars.
    unsafe {
        assert_eq!(
            stateless_v2_fuzz(session, 123, 3, 10, 50, 0, 0, &mut found),
            PROPERTY_FAILED
        );
        let retained = host.roots.borrow().len();
        host.mode.set(14);
        host.calls.borrow_mut().clear();
        assert_eq!(
            stateless_v2_shrink(session, found, 50, 1000, 0, &mut shrunk),
            MODEL_ERROR
        );
        assert_eq!(shrunk, 0);
        assert_eq!(host.calls.borrow().iter().filter(|op| **op == 9).count(), 5);
        assert_eq!(host.roots.borrow().len(), retained);
    }
    host.close(session);
}

#[test]
fn ring_records_actual_transitions_and_exports_a_replayable_checkpoint() {
    let host = Host::new();
    let session = host.session(CAP_CHECK | CAP_CODEC | CAP_RING, 1000);
    let initial = host.imported(session, STATE, Value::Number(0));
    let mut ring = 0;
    assert_eq!(
        // SAFETY: Initial checkpoint is an imported immutable state snapshot.
        unsafe { stateless_v2_ring_new(session, initial, 1, 1024 * 1024, &mut ring) },
        OK
    );
    let mut before = initial;
    for value in [1, 2] {
        let input = host.imported(session, INPUT, Value::Number(1));
        let after = host.imported(session, STATE, Value::Number(value));
        let output = host.imported(session, OUTPUT, Value::Number(value));
        let mut checked = 0;
        assert_eq!(
            // SAFETY: Each supplied transition is coherent and ordered.
            unsafe {
                stateless_v2_ring_observe(
                    session,
                    ring,
                    before,
                    input,
                    after,
                    &output,
                    1,
                    0,
                    ptr::null(),
                    0,
                    &mut checked,
                )
            },
            OK
        );
        before = after;
    }
    assert!(!host.calls.borrow().contains(&2));
    let mut snapshot = 0;
    assert_eq!(
        // SAFETY: This session owns the ring and output handle.
        unsafe { stateless_v2_ring_snapshot(session, ring, &mut snapshot) },
        OK
    );
    let artifact = bytes(session, snapshot);
    let trace = Trace::read_from(artifact.as_slice(), &ReadLimits::default()).unwrap();
    assert_eq!(trace.steps.len(), 1);
    assert_eq!(trace.initial_state, 1i64.to_le_bytes());
    host.close(session);
}

#[test]
fn full_width_rng_and_trace_packet_conversion_match_the_shared_engine() {
    let mut state = u64::MAX;
    let mut value = 0;
    let mut present = 0;
    let mut native = stateless::Rng::new(state);
    let expected = native.next_u64();
    // SAFETY: RNG in/out fields are valid distinct scalar storage.
    unsafe {
        assert_eq!(stateless_v2_rng_next(&mut state, &mut value), OK);
        assert_eq!(value, expected);
        assert_eq!(
            stateless_v2_rng_index(&mut state, u64::MAX, &mut present, &mut value),
            OK
        );
    }
    assert_eq!(present, 1);
    assert!(value < u64::MAX);
    let host = Host::new();
    let session = host.session(CAP_CHECK | CAP_CODEC, 1000);
    let input = host.imported(session, INPUT, Value::Number(1));
    let mut recorded = 0;
    let mut packet = 0;
    let mut restored = 0;
    // SAFETY: Inputs and each subsequently allocated artifact span remain live.
    unsafe {
        assert_eq!(
            stateless_v2_record(session, &input, 1, 1, &mut recorded),
            OK
        );
    }
    let original = bytes(session, recorded);
    // SAFETY: Conversion uses live spans and scalar result outputs.
    unsafe {
        assert_eq!(
            stateless_v2_trace_convert(
                session,
                1,
                original.as_ptr(),
                original.len() as u64,
                &mut packet
            ),
            OK
        );
    }
    let packet = bytes(session, packet);
    assert!(packet.starts_with(b"STV2P001"));
    // SAFETY: Neutral packet remains live throughout its checked conversion.
    unsafe {
        assert_eq!(
            stateless_v2_trace_convert(
                session,
                2,
                packet.as_ptr(),
                packet.len() as u64,
                &mut restored
            ),
            OK
        );
    }
    assert_eq!(bytes(session, restored), original);
    host.close(session);
}

#[test]
fn portable_record_limits_apply_before_commit_and_exclude_binary_provenance() {
    for (blob_limit, item_limit, payload_limit) in [(0, 100, 1000), (8, 0, 1000), (8, 100, 7)] {
        let host = Host::new();
        let session = host.session(CAP_CHECK | CAP_CODEC, 1000);
        let cursor = host.imported(
            session,
            CURSOR,
            Value::Cursor {
                values: vec![1],
                position: 0,
            },
        );
        let mut result = 123;
        assert_eq!(
            // SAFETY: The imported cursor is valid; tiny budgets deliberately fail.
            unsafe {
                stateless_v2_record_cursor_limited(
                    session,
                    cursor,
                    10,
                    blob_limit,
                    item_limit,
                    payload_limit,
                    &mut result,
                )
            },
            MODEL_ERROR
        );
        assert_eq!(result, 0);
        assert!(
            !host.calls.borrow().contains(&9),
            "invalid checkpoint must not pull input"
        );
        assert!(!host.calls.borrow().contains(&2));
        host.close(session);
    }
    for (values, item_limit, payload_limit, expected_steps, expected_status) in [
        (vec![], 1, 8, 0, OK),
        (vec![1, 1], 100, 32, 1, MODEL_ERROR),
        (vec![1, 1], 4, 1000, 1, MODEL_ERROR),
    ] {
        let host = Host::new();
        let session = host.session(CAP_CHECK | CAP_CODEC, 1000);
        let cursor = host.imported(
            session,
            CURSOR,
            Value::Cursor {
                values,
                position: 0,
            },
        );
        let mut result = 0;
        assert_eq!(
            // SAFETY: Each case uses a live cursor and bounded output handle.
            unsafe {
                stateless_v2_record_cursor_limited(
                    session,
                    cursor,
                    10,
                    8,
                    item_limit,
                    payload_limit,
                    &mut result,
                )
            },
            expected_status
        );
        assert_ne!(result, 0);
        let artifact = bytes(session, result);
        let trace = Trace::read_from(artifact.as_slice(), &ReadLimits::default()).unwrap();
        assert_eq!(trace.steps.len(), expected_steps);
        if expected_status == MODEL_ERROR {
            assert!(matches!(trace.termination, Termination::ModelError(_)));
        }
        host.close(session);
    }
}

#[test]
fn automatic_generation_uses_shared_cursor_sampling_and_fails_on_incomplete_domains() {
    let host = Host::new();
    host.fault.set(15);
    let session = host.session(
        CAP_CHECK | CAP_ENUMERATE | CAP_GENERATE | CAP_SHRINK | CAP_AUTO_GENERATE,
        1000,
    );
    let mut found = 0;
    let mut minimized = 0;
    // SAFETY: Automatic generation advertises complete cursor and identity hooks.
    unsafe {
        assert_eq!(
            stateless_v2_fuzz(session, 99, 1, 10, 10, 0, 0, &mut found),
            PROPERTY_FAILED
        );
        assert_eq!(
            stateless_v2_shrink(session, found, 50, 1000, 0, &mut minimized),
            PROPERTY_FAILED
        );
    }
    assert_eq!(scalar(session, minimized, 11), 3);
    assert!(!host.calls.borrow().contains(&15));
    assert!(!host.calls.borrow().contains(&16));
    assert!(host.calls.borrow().contains(&17));
    host.close(session);

    let host = Host::new();
    host.mode.set(1);
    let session = host.session(
        CAP_CHECK | CAP_ENUMERATE | CAP_GENERATE | CAP_AUTO_GENERATE,
        1000,
    );
    let mut result = 123;
    assert_eq!(
        // SAFETY: The cursor deliberately errors after yielding the first candidate.
        unsafe { stateless_v2_fuzz(session, 99, 1, 10, 10, 0, 0, &mut result) },
        MODEL_ERROR
    );
    assert_eq!(result, 0);
    assert!(!host.calls.borrow().contains(&2));
    assert!(host.roots.borrow().is_empty());
    host.close(session);
}

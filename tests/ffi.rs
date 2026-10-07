use stateless::ffi::*;
use stateless::trace::{ReadLimits, Termination, Trace};
use std::ffi::c_void;
use std::ptr;

fn blob(bytes: &[u8]) -> Vec<u8> {
    let mut result = (bytes.len() as u32).to_le_bytes().to_vec();
    result.extend_from_slice(bytes);
    result
}

unsafe extern "C" fn callback(
    context: *mut c_void,
    operation: u32,
    state: *const u8,
    state_len: usize,
    input: *const u8,
    input_len: usize,
    response: *mut StatelessBuffer,
) -> i32 {
    // SAFETY: Every test keeps its byte context alive throughout synchronous calls.
    let mode = unsafe { *(context as *const u8) };
    if mode == 2 {
        return 47;
    }
    if mode == 3 {
        // SAFETY: The engine supplies a live borrowed response; one byte is readable.
        return unsafe { stateless_buffer_assign(response, [255].as_ptr(), 1) };
    }
    // SAFETY: The engine supplies readable state/input spans for the callback.
    let state = unsafe { std::slice::from_raw_parts(state, state_len) };
    // SAFETY: The engine supplies readable state/input spans for the callback.
    let input = unsafe { std::slice::from_raw_parts(input, input_len) };
    if (mode == 4 && operation == 1 && state == [1])
        || (mode == 5 && operation == 2 && state == [2])
    {
        return 47;
    }
    let bytes = match operation {
        0 => vec![0],
        1 => {
            if state.len() != 1 || input.len() != 1 {
                return 11;
            }
            let Some(next) = state[0]
                .checked_add(input[0])
                .and_then(|value| value.checked_add(u8::from(mode == 1)))
            else {
                return 11;
            };
            let mut packet = vec![0];
            packet.extend(blob(b""));
            packet.extend(blob(&[next]));
            packet.extend(1u32.to_le_bytes());
            packet.extend(blob(&[next]));
            packet
        }
        2 => {
            if state.len() != 1 {
                return 11;
            }
            let failed = state[0] > 2;
            let mut packet = 1u32.to_le_bytes().to_vec();
            packet.extend(blob(b"counter_bound"));
            packet.push(u8::from(failed));
            packet.extend(blob(if failed { b"overflow" } else { b"" }));
            packet
        }
        3 => 0u32.to_le_bytes().to_vec(),
        4 => {
            let mut packet = 1u32.to_le_bytes().to_vec();
            packet.extend(blob(&[1]));
            packet
        }
        _ => return 11,
    };
    // SAFETY: bytes lives throughout assignment and response is a live borrowed handle.
    unsafe { stateless_buffer_assign(response, bytes.as_ptr(), bytes.len()) }
}

unsafe fn model(mode: &mut u8, abi_version: u32) -> (*mut StatelessModel, i32) {
    let callbacks = StatelessCallbacks {
        abi_version,
        struct_size: std::mem::size_of::<StatelessCallbacks>() as u32,
        context: ptr::from_mut(mode).cast(),
        dispatch: Some(callback),
    };
    let mut result = ptr::null_mut();
    // SAFETY: Table/spans/output are valid; the caller keeps mode alive until free.
    let status = unsafe {
        stateless_model_new(
            &callbacks,
            b"ffi-test".as_ptr(),
            8,
            b"build-1".as_ptr(),
            7,
            1,
            1,
            1,
            &mut result,
        )
    };
    (result, status)
}

#[test]
fn records_normal_trace_and_replays_with_a_fresh_foreign_model() {
    let mut mode = 0;
    let mut changed_mode = 1;
    let mut batch = 3u32.to_le_bytes().to_vec();
    for _ in 0..3 {
        batch.extend(blob(&[1]));
    }
    // SAFETY: Tests exclusively own all handles and keep callbacks/contexts/spans
    // alive until the synchronous operations finish; each handle is freed once.
    unsafe {
        let (first, status) = model(&mut mode, ABI_VERSION);
        assert_eq!(status, OK);
        let output = stateless_buffer_new(0);
        assert_eq!(
            stateless_record(first, batch.as_ptr(), batch.len(), 10, output),
            PROPERTY_FAILED
        );
        let artifact =
            std::slice::from_raw_parts(stateless_buffer_data(output), stateless_buffer_len(output))
                .to_vec();
        stateless_model_free(first);
        let trace = Trace::read_from(&artifact[..], &ReadLimits::default()).unwrap();
        assert_eq!(trace.steps.len(), 3);
        assert_eq!(trace.steps[2].outputs, vec![vec![3]]);
        assert_eq!(trace.termination, Termination::PropertyFailed);
        let (fresh, status) = model(&mut mode, ABI_VERSION);
        assert_eq!(status, OK);
        assert_eq!(
            stateless_replay(fresh, artifact.as_ptr(), artifact.len()),
            PROPERTY_FAILED
        );
        assert_eq!(
            stateless_replay(fresh, artifact.as_ptr(), artifact.len() - 1),
            TRACE_ERROR
        );
        stateless_model_free(fresh);
        let (changed, status) = model(&mut changed_mode, ABI_VERSION);
        assert_eq!(status, OK);
        assert_eq!(
            stateless_replay(changed, artifact.as_ptr(), artifact.len()),
            DIVERGED
        );
        stateless_model_free(changed);
        stateless_buffer_free(output);
    }
}

#[test]
fn abi_and_callback_errors_cannot_become_successful_artifacts() {
    let mut mode = 2;
    let batch = 0u32.to_le_bytes();
    // SAFETY: All spans, model contexts, and exclusively owned handles remain
    // live for synchronous calls; null arguments deliberately exercise rejection.
    unsafe {
        let (invalid, status) = model(&mut mode, 99);
        assert_eq!(status, INVALID_ARGUMENT);
        assert!(invalid.is_null());
        let (valid, status) = model(&mut mode, ABI_VERSION);
        assert_eq!(status, OK);
        let output = stateless_buffer_new(1);
        *stateless_buffer_data(output) = 42;
        assert_eq!(
            stateless_record(valid, batch.as_ptr(), batch.len(), 10, output),
            MODEL_ERROR
        );
        assert_eq!(stateless_buffer_len(output), 1);
        assert_eq!(*stateless_buffer_data(output), 42);
        assert_eq!(
            stateless_record(valid, ptr::null(), 1, 10, output),
            INVALID_ARGUMENT
        );
        stateless_model_free(valid);
        stateless_buffer_free(output);
    }
}

#[test]
fn errors_after_a_valid_prefix_are_errors_and_preserve_output_buffer() {
    let mut batch = 2u32.to_le_bytes().to_vec();
    batch.extend(blob(&[1]));
    batch.extend(blob(&[1]));
    for mut mode in [4, 5] {
        // SAFETY: Context/spans/handles stay live for synchronous calls and all
        // handles are exclusively owned and freed once after use.
        unsafe {
            let (model, status) = model(&mut mode, ABI_VERSION);
            assert_eq!(status, OK);
            let output = stateless_buffer_new(1);
            *stateless_buffer_data(output) = 42;
            let status = stateless_record(model, batch.as_ptr(), batch.len(), 10, output);
            let retained = std::slice::from_raw_parts(
                stateless_buffer_data(output),
                stateless_buffer_len(output),
            )
            .to_vec();
            stateless_model_free(model);
            stateless_buffer_free(output);
            assert_eq!(status, MODEL_ERROR, "mode {mode}");
            assert_eq!(retained, [42]);
        }
    }
}

#[test]
fn malformed_callback_packets_are_model_errors() {
    let mut mode = 3;
    // SAFETY: Context and spans are valid and handles are exclusively owned. The
    // callback deliberately returns invalid packet data through a valid buffer.
    unsafe {
        let (valid, status) = model(&mut mode, ABI_VERSION);
        assert_eq!(status, OK);
        let output = stateless_buffer_new(0);
        assert_eq!(
            stateless_record(valid, 0u32.to_le_bytes().as_ptr(), 4, 10, output),
            MODEL_ERROR
        );
        stateless_model_free(valid);
        stateless_buffer_free(output);
    }
}

#[test]
fn buffer_assignment_permits_its_own_borrowed_contents() {
    // SAFETY: The buffer remains alive and exclusive; assignment documents support
    // for source aliasing, and copies before replacing the original allocation.
    unsafe {
        let buffer = stateless_buffer_new(3);
        let data = stateless_buffer_data(buffer);
        std::slice::from_raw_parts_mut(data, 3).copy_from_slice(b"abc");
        assert_eq!(stateless_buffer_assign(buffer, data.add(1), 2), OK);
        assert_eq!(
            std::slice::from_raw_parts(stateless_buffer_data(buffer), stateless_buffer_len(buffer)),
            b"bc"
        );
        stateless_buffer_free(buffer);
    }
}

#[test]
fn enumeration_reports_budgets_and_emits_a_replayable_failure() {
    let mut mode = 0;
    // SAFETY: Context and all handles remain live and exclusively accessed for
    // each synchronous call, and the two output handles are distinct.
    unsafe {
        let (model, status) = model(&mut mode, ABI_VERSION);
        assert_eq!(status, OK);
        let report = stateless_buffer_new(0);
        let artifact = stateless_buffer_new(0);
        assert_eq!(stateless_enumerate(model, 20, 100, 1, report, artifact), OK);
        let text =
            std::slice::from_raw_parts(stateless_buffer_data(report), stateless_buffer_len(report));
        assert!(
            std::str::from_utf8(text)
                .unwrap()
                .contains("termination=DepthBound")
        );
        assert_eq!(stateless_buffer_len(artifact), 0);
        assert_eq!(
            stateless_enumerate(model, 20, 100, 10, report, artifact),
            PROPERTY_FAILED
        );
        let text =
            std::slice::from_raw_parts(stateless_buffer_data(report), stateless_buffer_len(report));
        assert!(
            std::str::from_utf8(text)
                .unwrap()
                .contains("termination=FailureFound")
        );
        assert_eq!(
            stateless_replay(
                model,
                stateless_buffer_data(artifact),
                stateless_buffer_len(artifact)
            ),
            PROPERTY_FAILED
        );
        assert_eq!(
            stateless_enumerate(model, 20, 100, 10, report, report),
            INVALID_ARGUMENT
        );
        stateless_buffer_free(report);
        stateless_buffer_free(artifact);
        stateless_model_free(model);
    }
}

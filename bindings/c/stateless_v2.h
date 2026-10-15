#ifndef STATELESS_V2_H
#define STATELESS_V2_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

/* All handles are nonzero uint64_t, scoped to their creating engine/session.
 * They are never reused. Sessions and every operation except cancel are confined
 * to the thread that created the session. Raw pointers remain caller obligations.
 * Callbacks must not throw/unwind. No nested engine operation is permitted.
 * Only callback_* functions and cancel may reenter from dispatch. Callback
 * services require the active invocation token.
 * Release must not reenter, fail, or unwind. Context outlives session_close. */
enum {
    STATELESS_V2_OK=0, STATELESS_V2_PROPERTY_FAILED=1,
    STATELESS_V2_DIVERGED=2, STATELESS_V2_INCOMPATIBLE=3,
    STATELESS_V2_INVALID_ARGUMENT=10, STATELESS_V2_MODEL_ERROR=11,
    STATELESS_V2_TRACE_ERROR=12, STATELESS_V2_PANIC=13,
    STATELESS_V2_INVALID_HANDLE=14, STATELESS_V2_WRONG_THREAD=15,
    STATELESS_V2_REENTRANT=16, STATELESS_V2_UNSUPPORTED=17,
    STATELESS_V2_LIMIT=18
};
enum {
    STATELESS_V2_CAP_CHECK=1, STATELESS_V2_CAP_ENUMERATE=2,
    STATELESS_V2_CAP_CODEC=4, STATELESS_V2_CAP_GENERATE=8,
    STATELESS_V2_CAP_SHRINK=16, STATELESS_V2_CAP_HASH=32,
    STATELESS_V2_CAP_ESTIMATE=64, STATELESS_V2_CAP_RING=128,
    STATELESS_V2_CAP_AUTO_GENERATE=256
};
enum {
    STATELESS_V2_STATE=1, STATELESS_V2_INPUT=2,
    STATELESS_V2_OUTPUT=3, STATELESS_V2_CURSOR=4
};
enum {
    STATELESS_V2_INITIAL=1, STATELESS_V2_STEP=2,
    STATELESS_V2_CHECK_STATE=3, STATELESS_V2_CHECK_TRANSITION=4,
    STATELESS_V2_STATE_EQUAL=5, STATELESS_V2_STATE_HASH=6,
    STATELESS_V2_ESTIMATE=7, STATELESS_V2_INPUT_CURSOR=8,
    STATELESS_V2_CURSOR_NEXT=9, STATELESS_V2_ENCODE_STATE=10,
    STATELESS_V2_DECODE_STATE=11, STATELESS_V2_ENCODE_INPUT=12,
    STATELESS_V2_DECODE_INPUT=13, STATELESS_V2_ENCODE_OUTPUT=14,
    STATELESS_V2_GENERATE=15, STATELESS_V2_IS_ENABLED=16,
    STATELESS_V2_INPUT_EQUAL=17, STATELESS_V2_SIMPLER_CURSOR=18
};
/* Arguments are borrowed HOST tokens, never engine value handles.
 * INITIAL/DECODE_STATE publish one STATE; STEP receives [state,input] and
 * publishes one STATE then zero+ OUTPUTs, with optional disposition.
 * CHECK_STATE receives [state]. CHECK_TRANSITION receives
 * [before,input,after,disposition,output...] and disposition reason in data.
 * EQUAL receives [left,right] and publishes scalar 0/1; HASH scalar u64;
 * ESTIMATE scalar bytes or no scalar if unknown. INPUT_CURSOR receives [state],
 * SIMPLER_CURSOR [input]; each publishes one CURSOR. CURSOR_NEXT [cursor]
 * publishes one INPUT or none at end. GENERATE [state] likewise uses callback_rng.
 * IS_ENABLED [state,input] publishes scalar 0/1. ENCODE_* receives [value] and
 * appends bytes; DECODE_* receives data and publishes one matching value.
 * CHECK_* appends checks. All other builder output combinations are rejected.
 * A callback returning nonzero fails; diagnostics may be set via callback_error.
 * Every builder failure is sticky even when the callback returns zero. */
typedef int32_t (*StatelessV2Dispatch)(
    uint64_t context, uint64_t session, uint64_t invocation, uint32_t operation,
    const uint64_t *args, uint32_t nargs, const uint8_t *data, uint64_t nbytes);
typedef void (*StatelessV2Release)(uint64_t context, uint32_t kind, uint64_t host_token);

typedef struct StatelessV2Callbacks {
    uint32_t abi_version;
    uint32_t struct_size;
    uint64_t capabilities;
    uint64_t context;
    StatelessV2Dispatch dispatch;
    StatelessV2Release release;
    uint64_t max_values;
    /* Bounds each callback batch and each simplification cursor's total yielded
     * inputs. A cursor may be probed once past this cap to distinguish clean
     * exhaustion; another item fails the operation even when it is a duplicate. */
    uint64_t max_items;
    uint64_t max_bytes;
} StatelessV2Callbacks;
/* Version is 2, struct_size >= sizeof known prefix. Unknown capability bits are
 * rejected. Nonzero quotas are required; limits count engine roots/builders and
 * encoded bytes, not arbitrary host allocations or RSS. Larger tails ignored. */
uint32_t stateless_v2_abi_version(void);
uint64_t stateless_v2_capabilities(void);
int32_t stateless_v2_session_new(const StatelessV2Callbacks *callbacks,
    const uint8_t *name, uint64_t name_len, const uint8_t *build, uint64_t build_len,
    uint32_t model_version, uint32_t properties_version, uint32_t codec_version,
    uint64_t *session);
int32_t stateless_v2_session_close(uint64_t session);
int32_t stateless_v2_session_capabilities(uint64_t session, uint64_t *capabilities);
/* cancel is thread-safe. Reset is owner-thread-only, outside engine callbacks. */
int32_t stateless_v2_cancel(uint64_t session);
int32_t stateless_v2_cancel_reset(uint64_t session);

/* Import transfers exactly one immutable snapshot/root ONLY on success.
 * host_token must be nonzero and unique among live roots in this session,
 * including roots of other kinds. Cloning a handle shares that root.
 * callback_value has the same transfer-on-success rule. On rejection the host
 * must release the unpublished root; successfully published partial output is
 * released automatically if that callback subsequently fails. */
int32_t stateless_v2_value_import(uint64_t session, uint32_t kind,
    uint64_t host_token, uint64_t *value);
int32_t stateless_v2_value_release(uint64_t session, uint64_t value);
int32_t stateless_v2_value_host_token(uint64_t session, uint64_t value,
    uint32_t *kind, uint64_t *host_token);
int32_t stateless_v2_callback_value(uint64_t session, uint64_t invocation,
    uint32_t kind, uint64_t host_token);
int32_t stateless_v2_callback_scalar(uint64_t session, uint64_t invocation, uint64_t value);
/* Check status: 0 passed, 1 failed, 2 skipped. IDs/details UTF-8; empty allowed.
 * Passing checks require empty details. */
int32_t stateless_v2_callback_check(uint64_t session, uint64_t invocation,
    uint32_t status, const uint8_t *id, uint64_t id_len,
    const uint8_t *details, uint64_t details_len);
int32_t stateless_v2_callback_bytes(uint64_t session, uint64_t invocation,
    const uint8_t *data, uint64_t len);
/* Disposition: 0 accepted, 1 rejected, 2 ignored; default accepted. */
int32_t stateless_v2_callback_disposition(uint64_t session, uint64_t invocation,
    uint32_t disposition, const uint8_t *reason, uint64_t reason_len);
int32_t stateless_v2_callback_error(uint64_t session, uint64_t invocation,
    const uint8_t *message, uint64_t len);
/* Only valid during GENERATE. RNG state is committed only on callback success. */
int32_t stateless_v2_callback_rng(uint64_t session, uint64_t invocation, uint64_t *value);

/* Output handles are zeroed before work and published only on complete results.
 * Property findings and bounded terminations are completed results, not errors.
 * Unsupported operations fail before invoking any callback. */
int32_t stateless_v2_check_initial(uint64_t session, uint64_t state, uint64_t *result);
int32_t stateless_v2_check_observed(uint64_t session,
    uint64_t before, uint64_t input, uint64_t after,
    const uint64_t *outputs, uint64_t output_count,
    uint32_t disposition, const uint8_t *reason, uint64_t reason_len,
    uint64_t sequence, uint64_t state_every, uint32_t transition_checks,
    uint64_t *result);
int32_t stateless_v2_enumerate(uint64_t session, uint64_t max_states,
    uint64_t max_transitions, uint64_t max_depth, uint64_t max_state_bytes,
    uint64_t max_duration_millis, uint64_t *result);
/* max_state_bytes UINT64_MAX disables estimate cap; duration 0 disables clock. */
int32_t stateless_v2_record(uint64_t session, const uint64_t *inputs,
    uint64_t input_count, uint64_t max_steps, uint64_t *result);
/* Lazy record input cursor; import it with kind CURSOR. The engine borrows the
 * imported owner; caller must release its value handle even after completion.
 * A callback error after a coherent initial checkpoint returns MODEL_ERROR with
 * a nonzero result holding the error-terminated prefix. Release that result too. */
int32_t stateless_v2_record_cursor(uint64_t session, uint64_t cursor,
    uint64_t max_steps, uint64_t *result);
/* Portable per-run payload limits. Payload counts retained state/input/output
 * bytes; items count checks, outputs, and steps, excluding engine metadata.
 * Session quotas are additional outer bounds. Errors preserve a coherent prefix
 * after a valid checkpoint, as for record_cursor. */
int32_t stateless_v2_record_cursor_limited(uint64_t session, uint64_t cursor,
    uint64_t max_steps, uint64_t max_blob_bytes, uint64_t max_items,
    uint64_t max_payload_bytes, uint64_t *result);
int32_t stateless_v2_replay(uint64_t session, const uint8_t *artifact,
    uint64_t artifact_len, uint32_t allow_build_mismatch, uint64_t *result);
int32_t stateless_v2_fuzz(uint64_t session, uint64_t seed, uint64_t cases,
    uint64_t max_steps, uint64_t max_transitions, uint32_t mutation_percent,
    uint64_t max_duration_millis, uint64_t *result);
/* CAP_AUTO_GENERATE requires ENUMERATE|GENERATE and semantic INPUT_EQUAL.
 * It selects the shared Rust finite-domain generator and membership validator
 * instead of callbacks GENERATE/IS_ENABLED. Each scan uses max_items candidates
 * plus the end/overflow probe; a failed scan never commits a partial RNG sample.
 * SHRINK additionally requires SIMPLER_CURSOR, which may yield an empty domain. */
int32_t stateless_v2_shrink(uint64_t session, uint64_t failure_result,
    uint64_t max_attempts, uint64_t max_transitions,
    uint64_t max_duration_millis, uint64_t *result);

enum {
    STATELESS_V2_RESULT_STATUS=1, STATELESS_V2_RESULT_KIND=2,
    STATELESS_V2_RESULT_TERMINATION=3, STATELESS_V2_RESULT_STATES=4,
    STATELESS_V2_RESULT_TRANSITIONS=5, STATELESS_V2_RESULT_DEPTH=6,
    STATELESS_V2_RESULT_SKIPPED=7, STATELESS_V2_RESULT_ESTIMATED_BYTES=8,
    STATELESS_V2_RESULT_HAS_ESTIMATE=9, STATELESS_V2_RESULT_CHECK_COUNT=10,
    STATELESS_V2_RESULT_INPUT_COUNT=11, STATELESS_V2_RESULT_BYTE_COUNT=12,
    STATELESS_V2_RESULT_STATE_CHECK_COUNT=13, STATELESS_V2_RESULT_STEPS_VERIFIED=14,
    STATELESS_V2_RESULT_BUILD_MATCHES=15, STATELESS_V2_RESULT_FAILURE_REPRODUCED=16,
    STATELESS_V2_RESULT_CASES=17, STATELESS_V2_RESULT_ATTEMPTS=18,
    STATELESS_V2_RESULT_VALIDATED=19,
    STATELESS_V2_RESULT_HAS_DIVERGENCE_STEP=20, STATELESS_V2_RESULT_DIVERGENCE_STEP=21
};
/* Result kind: 1 checks, 2 enumeration, 3 record, 4 replay, 5 fuzz, 6 shrink.
 * Enumeration termination: 0 graph exhausted,1 depth,2 states,3 transitions,
 * 4 failure,5 estimated bytes,6 deadline,7 cancelled.
 * Record: 0 completed,1 property failed,2 step limit,3 interrupted,4 model error.
 * Fuzz: 0 cases completed,1 transitions,2 failure,3 deadline,4 cancelled.
 * Shrink: 0 search complete,1 attempts,2 transitions,3 deadline,4 cancelled. */
int32_t stateless_v2_result_scalar(uint64_t session, uint64_t result,
    uint32_t field, uint64_t *value);
int32_t stateless_v2_result_check(uint64_t session, uint64_t result,
    uint64_t index, uint32_t *status, uint32_t *phase);
/* Check phase 0 unspecified,1 initial,2 state,3 transition. */
int32_t stateless_v2_result_input(uint64_t session, uint64_t result,
    uint64_t index, uint64_t *value);
/* Returns a separately owned engine value handle; release with value_release. */
int32_t stateless_v2_result_release(uint64_t session, uint64_t result);
/* Copy fields: 1 artifact bytes,2 check ID,3 check details,4 diagnostic,
 * 5 divergence field (core replay field name).
 * Query with null data/capacity zero. written always reports required byte count;
 * insufficient capacity returns LIMIT and copies nothing. Strings omit NUL. */
int32_t stateless_v2_result_copy(uint64_t session, uint64_t result,
    uint32_t field, uint64_t index, uint8_t *data, uint64_t capacity, uint64_t *written);
int32_t stateless_v2_last_error(int32_t *status, uint32_t *operation,
    uint32_t *phase, uint64_t *sequence, uint8_t *data, uint64_t capacity, uint64_t *written);
int32_t stateless_v2_rng_next(uint64_t *state, uint64_t *value);
int32_t stateless_v2_rng_index(uint64_t *state, uint64_t upper, uint32_t *present, uint64_t *value);
int32_t stateless_v2_callback_rng_index(uint64_t session, uint64_t invocation,
    uint64_t upper, uint32_t *present, uint64_t *value);
/* format 1 binary trace -> STV2P001 neutral packet; 2 packet -> binary trace.
 * No model callback is invoked; conversion does not imply replay qualification. */
int32_t stateless_v2_trace_convert(uint64_t session, uint32_t format,
    const uint8_t *data, uint64_t len, uint64_t *result);

/* Ring recorder handles retain encoded evidence, never run reducers, and freeze
 * at a violation or recorder error. Observe returns completed checks; after an
 * error snapshot still exports the coherent prefix with error termination. */
int32_t stateless_v2_ring_new(uint64_t session, uint64_t initial_state,
    uint64_t max_steps, uint64_t max_retained_bytes, uint64_t *recorder);
int32_t stateless_v2_ring_observe(uint64_t session, uint64_t recorder,
    uint64_t before, uint64_t input, uint64_t after,
    const uint64_t *outputs, uint64_t output_count,
    uint32_t disposition, const uint8_t *reason, uint64_t reason_len, uint64_t *result);
int32_t stateless_v2_ring_snapshot(uint64_t session, uint64_t recorder, uint64_t *result);
int32_t stateless_v2_ring_release(uint64_t session, uint64_t recorder);

/* The abi-v2-wasm feature artifact exports the same scalar service functions.
 * The default v1-compatible Wasm artifact's v2 constructor returns UNSUPPORTED
 * and does not require v2 imports. No C struct layout is needed:
 * wasm_session_new replaces the callback descriptor with scalar arguments and
 * imports stateless_v2.dispatch and stateless_v2.release using the signatures
 * above. uint64 values use JS BigInt; pointers are wasm32 linear-memory offsets.
 * Reacquire memory views after every engine call that may grow memory. */
int32_t stateless_v2_wasm_session_new(uint64_t capabilities, uint64_t context,
    uint64_t max_values, uint64_t max_items, uint64_t max_bytes,
    const uint8_t *name, uint64_t name_len, const uint8_t *build, uint64_t build_len,
    uint32_t model_version, uint32_t properties_version, uint32_t codec_version,
    uint64_t *session);

#ifdef __cplusplus
}
#endif
#endif

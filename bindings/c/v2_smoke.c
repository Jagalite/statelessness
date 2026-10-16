/* A real C11 ABI2 consumer. Compile/link through Kyoto; runtime execution is a
 * separate qualification gate. This model has no persistence in its hot path:
 * values/cursors are host roots, equality is semantic, and codecs are optional. */
#include "stateless_v2.h"
#include "stateless.h"
#include <stddef.h>
#include <stdlib.h>
#include <string.h>

_Static_assert(sizeof(uint64_t) == 8, "ABI handles require 64-bit integers");
_Static_assert(offsetof(StatelessV2Callbacks, capabilities) == 8, "descriptor prefix");

typedef struct Root {
    uint32_t kind;
    uint32_t live;
    uint64_t number;
    uint32_t position;
} Root;
typedef struct Model {
    Root roots[256];
    uint64_t next;
    uint32_t live;
    uint32_t invalid_release;
    uint32_t steps;
} Model;

static Root *lookup(Model *model, uint64_t token, uint32_t kind) {
    if (token == 0 || token >= model->next || token >= 256) return NULL;
    Root *root = &model->roots[token];
    return root->live && root->kind == kind ? root : NULL;
}
static uint64_t create(Model *model, uint32_t kind, uint64_t number) {
    if (model->next >= 256) return 0;
    uint64_t token = model->next++;
    model->roots[token] = (Root){kind, 1, number, 0};
    model->live++;
    return token;
}
static void release(uint64_t context, uint32_t kind, uint64_t token) {
    Model *model = (Model *)(uintptr_t)context;
    Root *root = lookup(model, token, kind);
    if (!root) { model->invalid_release++; return; }
    root->live = 0;
    model->live--;
}
static int32_t publish(Model *model, uint64_t session, uint64_t invocation,
                       uint32_t kind, uint64_t number) {
    uint64_t token = create(model, kind, number);
    if (!token) return STATELESS_V2_LIMIT;
    int32_t status = stateless_v2_callback_value(session, invocation, kind, token);
    if (status != STATELESS_V2_OK) release((uint64_t)(uintptr_t)model, kind, token);
    return status;
}
static int32_t dispatch(uint64_t context, uint64_t session, uint64_t invocation,
                        uint32_t operation, const uint64_t *args, uint32_t nargs,
                        const uint8_t *data, uint64_t nbytes) {
    Model *model = (Model *)(uintptr_t)context;
    Root *left, *right;
    switch (operation) {
    case STATELESS_V2_INITIAL:
        return publish(model, session, invocation, STATELESS_V2_STATE, 0);
    case STATELESS_V2_STEP: {
        if (nargs != 2) return STATELESS_V2_MODEL_ERROR;
        left = lookup(model, args[0], STATELESS_V2_STATE);
        right = lookup(model, args[1], STATELESS_V2_INPUT);
        if (!left || !right) return STATELESS_V2_MODEL_ERROR;
        uint64_t next = left->number + right->number;
        model->steps++;
        int32_t status = publish(model, session, invocation, STATELESS_V2_STATE, next);
        if (status != STATELESS_V2_OK) return status;
        return publish(model, session, invocation, STATELESS_V2_OUTPUT, next);
    }
    case STATELESS_V2_CHECK_STATE: {
        if (nargs != 1 || !(left = lookup(model, args[0], STATELESS_V2_STATE))) return STATELESS_V2_MODEL_ERROR;
        const uint8_t *reason = (const uint8_t *)(left->number < 3 ? "" : "bound exceeded");
        return stateless_v2_callback_check(session, invocation, left->number < 3 ? 0 : 1,
            (const uint8_t *)"bound", 5, reason, (uint64_t)strlen((const char *)reason));
    }
    case STATELESS_V2_CHECK_TRANSITION:
        return STATELESS_V2_OK;
    case STATELESS_V2_STATE_EQUAL:
        if (nargs != 2) return STATELESS_V2_MODEL_ERROR;
        left = lookup(model, args[0], STATELESS_V2_STATE);
        right = lookup(model, args[1], STATELESS_V2_STATE);
        if (!left || !right) return STATELESS_V2_MODEL_ERROR;
        return stateless_v2_callback_scalar(session, invocation, left->number == right->number);
    case STATELESS_V2_STATE_HASH:
        return stateless_v2_callback_scalar(session, invocation, 0); /* collisions are intentional */
    case STATELESS_V2_ESTIMATE:
        return stateless_v2_callback_scalar(session, invocation, sizeof(Root));
    case STATELESS_V2_INPUT_CURSOR:
        if (nargs != 1 || !(left = lookup(model, args[0], STATELESS_V2_STATE))) return STATELESS_V2_MODEL_ERROR;
        return publish(model, session, invocation, STATELESS_V2_CURSOR, left->number < 3 ? 1 : 0);
    case STATELESS_V2_CURSOR_NEXT:
        if (nargs != 1 || !(left = lookup(model, args[0], STATELESS_V2_CURSOR))) return STATELESS_V2_MODEL_ERROR;
        if (left->position++ >= left->number) return STATELESS_V2_OK;
        return publish(model, session, invocation, STATELESS_V2_INPUT, 1);
    case STATELESS_V2_ENCODE_STATE:
    case STATELESS_V2_ENCODE_INPUT:
    case STATELESS_V2_ENCODE_OUTPUT: {
        uint32_t kind = operation == STATELESS_V2_ENCODE_STATE ? STATELESS_V2_STATE :
            operation == STATELESS_V2_ENCODE_INPUT ? STATELESS_V2_INPUT : STATELESS_V2_OUTPUT;
        if (nargs != 1 || !(left = lookup(model, args[0], kind))) return STATELESS_V2_MODEL_ERROR;
        uint8_t encoded[8];
        for (uint32_t i = 0; i < 8; i++) encoded[i] = (uint8_t)(left->number >> (8 * i));
        return stateless_v2_callback_bytes(session, invocation, encoded, sizeof encoded);
    }
    case STATELESS_V2_DECODE_STATE:
    case STATELESS_V2_DECODE_INPUT: {
        if (nbytes != 8) return STATELESS_V2_MODEL_ERROR;
        uint64_t value = 0;
        for (uint32_t i = 0; i < 8; i++) value |= (uint64_t)data[i] << (8 * i);
        return publish(model, session, invocation,
            operation == STATELESS_V2_DECODE_STATE ? STATELESS_V2_STATE : STATELESS_V2_INPUT, value);
    }
    default:
        return STATELESS_V2_UNSUPPORTED;
    }
}

int main(void) {
    Model model = {0};
    model.next = 1;
    StatelessV2Callbacks callbacks = {
        .abi_version = 2, .struct_size = sizeof callbacks,
        .capabilities = STATELESS_V2_CAP_CHECK | STATELESS_V2_CAP_ENUMERATE |
            STATELESS_V2_CAP_CODEC | STATELESS_V2_CAP_HASH | STATELESS_V2_CAP_ESTIMATE,
        .context = (uint64_t)(uintptr_t)&model, .dispatch = dispatch, .release = release,
        .max_values = 128, .max_items = 1000, .max_bytes = 1024 * 1024
    };
    uint64_t session = 0, result = 0, witness = 0, cursor = 0, artifact_result = 0, replayed = 0;
    uint8_t *artifact = NULL;
    int failed = 1;
    if (stateless_abi_version() != 1 || stateless_v2_abi_version() != 2) return 1;
    if (stateless_v2_session_new(&callbacks, (const uint8_t *)"c-v2", 4,
        (const uint8_t *)"source-smoke", 12, 1, 1, 1, &session) != STATELESS_V2_OK) return 1;
    if (stateless_v2_enumerate(session, 10, 10, 10, UINT64_MAX, 0, &result) != STATELESS_V2_PROPERTY_FAILED) goto done;
    uint64_t count = 0;
    if (stateless_v2_result_scalar(session, result, STATELESS_V2_RESULT_INPUT_COUNT, &count) || count != 3) goto done;
    if (stateless_v2_result_input(session, result, 0, &witness)) goto done;
    if (stateless_v2_result_release(session, result)) goto done;
    uint32_t kind = 0; uint64_t token = 0;
    if (stateless_v2_value_host_token(session, witness, &kind, &token) || kind != STATELESS_V2_INPUT) goto done;
    if (stateless_v2_value_release(session, witness)) goto done;
    token = create(&model, STATELESS_V2_CURSOR, 2);
    if (!token || stateless_v2_value_import(session, STATELESS_V2_CURSOR, token, &cursor)) goto done;
    if (stateless_v2_record_cursor_limited(session, cursor, 10, 8, 100, 1024, &artifact_result)) goto done;
    uint64_t length = 0;
    if (stateless_v2_result_copy(session, artifact_result, 1, 0, NULL, 0, &length)) goto done;
    artifact = (uint8_t *)malloc((size_t)length);
    if (!artifact || stateless_v2_result_copy(session, artifact_result, 1, 0, artifact, length, &length)) goto done;
    if (stateless_v2_replay(session, artifact, length, 0, &replayed)) goto done;
    if (stateless_v2_result_scalar(session, replayed, STATELESS_V2_RESULT_STEPS_VERIFIED, &count) || count != 2) goto done;
    failed = 0;
done:
    free(artifact);
    if (stateless_v2_session_close(session)) failed = 1;
    if (model.live || model.invalid_release) failed = 1;
    return failed;
}

#include "stateless.h"
#include <assert.h>
#include <stdio.h>

static int32_t dispatch(void *context, uint32_t operation,
                       const uint8_t *state, size_t state_len,
                       const uint8_t *input, size_t input_len,
                       StatelessBuffer *response) {
    (void)context; (void)state; (void)state_len; (void)input; (void)input_len;
    /* Empty canonical state, no checks/outputs. Accepted transition framing:
       disposition byte, empty reason, empty next state, zero outputs. */
    const uint8_t zero[13] = {0};
    size_t length;
    switch (operation) {
        case STATELESS_INITIAL: length = 0; break;
        case STATELESS_STEP: length = 13; break;
        case STATELESS_CHECK_STATE:
        case STATELESS_CHECK_TRANSITION: length = 4; break;
        default: return STATELESS_MODEL_ERROR;
    }
    return stateless_buffer_assign(response, zero, length);
}

int main(void) {
    assert(stateless_abi_version() == 1);
    StatelessCallbacks callbacks = {1, sizeof(StatelessCallbacks), NULL, dispatch};
    StatelessModel *model = NULL;
    const uint8_t name[] = "c-smoke", build[] = "c-smoke-v1";
    assert(stateless_model_new(&callbacks, name, sizeof(name)-1, build, sizeof(build)-1,
                              1, 1, 1, &model) == STATELESS_OK);
    StatelessBuffer *artifact = stateless_buffer_new(0);
    assert(artifact);
    const uint8_t one_empty_input[] = {1, 0, 0, 0, 0, 0, 0, 0};
    assert(stateless_record(model, one_empty_input, sizeof(one_empty_input), 1, artifact) == STATELESS_OK);
    assert(stateless_replay(model, stateless_buffer_data(artifact), stateless_buffer_len(artifact)) == STATELESS_OK);
    assert(stateless_replay(model, stateless_buffer_data(artifact), stateless_buffer_len(artifact)-1) == STATELESS_TRACE_ERROR);
    stateless_buffer_free(artifact);
    stateless_model_free(model);
    puts("C header/library ABI smoke passed");
    return 0;
}

#ifndef STATELESS_H
#define STATELESS_H
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct StatelessBuffer StatelessBuffer;
typedef struct StatelessModel StatelessModel;

enum {
    STATELESS_OK = 0,
    STATELESS_PROPERTY_FAILED = 1,
    STATELESS_DIVERGED = 2,
    STATELESS_INCOMPATIBLE = 3,
    STATELESS_INVALID_ARGUMENT = 10,
    STATELESS_MODEL_ERROR = 11,
    STATELESS_TRACE_ERROR = 12,
    STATELESS_PANIC = 13
};

enum {
    STATELESS_INITIAL = 0,
    STATELESS_STEP = 1,
    STATELESS_CHECK_STATE = 2,
    STATELESS_CHECK_TRANSITION = 3,
    STATELESS_INPUTS = 4
};

/* Every span and response is borrowed for the call. Do not free response.
 * Populate response with stateless_buffer_assign; its bytes are copied.
 * Return zero for success. Never throw/unwind across this function boundary. */
typedef int32_t (*StatelessDispatch)(
    void *context, uint32_t operation,
    const uint8_t *state, size_t state_len,
    const uint8_t *input, size_t input_len,
    StatelessBuffer *response);

typedef struct StatelessCallbacks {
    uint32_t abi_version;
    uint32_t struct_size;
    void *context;
    StatelessDispatch dispatch;
} StatelessCallbacks;

uint32_t stateless_abi_version(void);
StatelessBuffer *stateless_buffer_new(size_t len);
uint8_t *stateless_buffer_data(StatelessBuffer *buffer);
size_t stateless_buffer_len(const StatelessBuffer *buffer);
int32_t stateless_buffer_assign(StatelessBuffer *buffer, const uint8_t *data, size_t len);
void stateless_buffer_free(StatelessBuffer *buffer);
int32_t stateless_last_error(StatelessBuffer *output);

/* Context is caller-owned and must outlive the model. Tables are copied.
 * Handles are thread-confined. The name/build strings are UTF-8.
 * No function may reenter an operation on the same model from its callback. */
int32_t stateless_model_new(
    const StatelessCallbacks *callbacks,
    const uint8_t *name, size_t name_len,
    const uint8_t *build, size_t build_len,
    uint32_t model_version, uint32_t properties_version, uint32_t codec_version,
    StatelessModel **output);
void stateless_model_free(StatelessModel *model);

/* Inputs: u32 little-endian count, repeated u32 length and bytes.
 * OK / PROPERTY_FAILED mean output now holds a standard Stateless trace.
 * Step limits remain encoded in the artifact; OK does not mean exhaustive. */
int32_t stateless_record(
    const StatelessModel *model, const uint8_t *inputs, size_t inputs_len,
    size_t max_steps, StatelessBuffer *output);
int32_t stateless_replay(
    const StatelessModel *model, const uint8_t *artifact, size_t artifact_len);

/* Callback operation 4 supplies a batch of all permitted next inputs.
 * Report includes termination and budget counts. Artifact is empty unless a
 * failure was found. Output handles must be distinct. */
int32_t stateless_enumerate(
    const StatelessModel *model, size_t max_states, uint64_t max_transitions,
    size_t max_depth, StatelessBuffer *report_output, StatelessBuffer *artifact_output);

#ifdef __cplusplus
}
#endif
#endif

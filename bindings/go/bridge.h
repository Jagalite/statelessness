#include "../c/stateless.h"
#include <stdlib.h>
extern int32_t goDispatch(uintptr_t, uint32_t, uint8_t*, size_t, uint8_t*, size_t, StatelessBuffer*);
void *go_context_new(uintptr_t handle);
int go_context_is_owner(void *context);
int32_t go_model_new(void*, const uint8_t*,size_t,const uint8_t*,size_t,uint32_t,uint32_t,uint32_t,StatelessModel**);

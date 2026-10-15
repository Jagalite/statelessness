#ifndef STATELESS_GO_V2_BRIDGE_H
#define STATELESS_GO_V2_BRIDGE_H
#include "../../c/stateless_v2.h"
extern int32_t goV2Dispatch(uint64_t,uint64_t,uint64_t,uint32_t,uint64_t*,uint32_t,uint8_t*,uint64_t);
extern void goV2Release(uint64_t,uint32_t,uint64_t);
int32_t go_v2_new(uint64_t,uint64_t,uint64_t,uint64_t,uint64_t,const uint8_t*,uint64_t,const uint8_t*,uint64_t,uint32_t,uint32_t,uint32_t,uint64_t*);
#endif

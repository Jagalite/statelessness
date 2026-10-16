//go:build cgo

#include "bridge.h"
static int32_t dispatch(uint64_t c,uint64_t s,uint64_t i,uint32_t op,const uint64_t*a,uint32_t n,const uint8_t*d,uint64_t z){return goV2Dispatch(c,s,i,op,(uint64_t*)a,n,(uint8_t*)d,z);}
static void release(uint64_t c,uint32_t k,uint64_t t){goV2Release(c,k,t);}
int32_t go_v2_new(uint64_t c,uint64_t caps,uint64_t v,uint64_t i,uint64_t b,const uint8_t*n,uint64_t nl,const uint8_t*build,uint64_t bl,uint32_t mv,uint32_t pv,uint32_t cv,uint64_t*s){
    StatelessV2Callbacks cb={2,sizeof(cb),caps,c,dispatch,release,v,i,b};
    return stateless_v2_session_new(&cb,n,nl,build,bl,mv,pv,cv,s);
}

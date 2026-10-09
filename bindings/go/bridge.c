#include "bridge.h"
#include <pthread.h>
typedef struct { uintptr_t handle; pthread_t owner; } GoContext;
static int32_t dispatch(void *ctx,uint32_t op,const uint8_t*s,size_t sn,const uint8_t*i,size_t in,StatelessBuffer*r){return goDispatch(((GoContext*)ctx)->handle,op,(uint8_t*)s,sn,(uint8_t*)i,in,r);}
void *go_context_new(uintptr_t h){GoContext*p=malloc(sizeof(*p));if(p){p->handle=h;p->owner=pthread_self();}return p;}
int32_t go_model_new(void*c,const uint8_t*n,size_t nn,const uint8_t*b,size_t bn,uint32_t m,uint32_t p,uint32_t v,StatelessModel**out){StatelessCallbacks cb={1,sizeof(cb),c,dispatch};return stateless_model_new(&cb,n,nn,b,bn,m,p,v,out);}

int go_context_is_owner(void *context){return pthread_equal(((GoContext*)context)->owner,pthread_self());}

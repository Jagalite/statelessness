"""Canonical pinned Rust composition/network layouts on existing value codecs.

Application schemas specify the payload representation. This is not arbitrary
Python serialization. Logical allocation charges are conservative, not heap sizes.
"""
from .core import ModelError
from .codec_declarations import model_codec
from .value_codec import Schema,U8,U32,VectorOf,Rust64Layout,encode_raw,encode_canonical,decode_canonical
from .network import Address,Route,Message,NetworkState,NetworkInput
from .composition import Address as PairAddress,PairState,PairInput

class _Record(Schema):
    def __init__(self,fields,split,join,weight=256):
        self.fields,self.split,self.join=fields,split,join
        self.allocation_layout=Rust64Layout(weight)
        self._compose_directions(*fields);self._seal()
    def encode_into(self,v,b):
        for schema,x in zip(self.fields,self.split(v)):encode_raw(schema,x,b)
    def decode(self,r):return self.join(*(r.value(f) for f in self.fields))

class _Variant(Schema):
    def __init__(self,schemas,split,join):
        self.schemas,self.split,self.join=schemas,split,join
        self.allocation_layout=Rust64Layout(256+sum(_weight(s) for s in schemas))
        self._compose_directions(*schemas);self._seal()
    def encode_into(self,v,b):
        tag,value=self.split(v)
        if type(tag) is not int or not 0<=tag<len(self.schemas):raise ModelError('invalid stateful action tag')
        encode_raw(U8,tag,b);encode_raw(self.schemas[tag],value,b)
    def decode(self,r):
        tag=r.value(U8)
        if tag>=len(self.schemas):raise ModelError('invalid stateful action tag')
        return self.join(tag,r.value(self.schemas[tag]))
def _weight(schema):
    if schema.allocation_layout is None:raise ModelError('explicit payload allocation layout required')
    return schema.allocation_layout.size

def network_schemas(state,input,output):
    def address(v):return _Record((U32,v),lambda a:(a.machine,a.value),Address,32+_weight(v))
    route=_Record((U32,U32,input),lambda r:(r.destination,r.channel,r.input),Route,32+_weight(input))
    message=_Record((U32,route),lambda m:(m.source,m.route),Message,32+_weight(route))
    entry=_Record((U32,state),lambda e:e,lambda id,s:(id,s),32+_weight(state))
    def restore(version,machines,pending):
        if version!=1:raise ModelError('network state codec version')
        ids=[id for id,_ in machines]
        if any(a>=b for a,b in zip(ids,ids[1:])):raise ModelError('noncanonical network machine order')
        if any(m.source not in ids or m.route.destination not in ids for m in pending):raise ModelError('unknown network route machine')
        return NetworkState(tuple(machines),tuple(pending))
    s=_Record((U8,VectorOf(entry),VectorOf(message)),lambda s:(1,s.machines,s.pending),restore)
    kinds=('local','deliver','drop','duplicate')
    i=_Variant((address(input),U32,U32,U32),lambda i:(kinds.index(i.kind),i.value),lambda t,v:NetworkInput(kinds[t],v))
    return s,i,address(output)

def pair_schemas(left,right,left_input,right_input,left_output,right_output):
    def address(l,r):return _Variant((l,r),lambda a:(0 if a.side=='left' else 1,a.value),lambda t,v:PairAddress('left' if t==0 else 'right',v))
    message=address(left_input,right_input)
    state=_Record((left,right,VectorOf(message)),lambda s:(s.left,s.right,s.pending),lambda l,r,q:PairState(l,r,tuple(q)))
    input=_Variant((message,U32),lambda i:(0 if i.kind=='local' else 1,i.value),lambda t,v:PairInput('local' if t==0 else 'deliver',v))
    return state,input,address(left_output,right_output)

class _Validated(Schema):
    def __init__(self,schema,validate):
        self.schema,self.validate=schema,validate
        self.allocation_layout=schema.allocation_layout
        self._compose_directions(schema);self._seal()
    def encode_into(self,v,b):self.validate(v);encode_raw(self.schema,v,b)
    def decode(self,r):
        v=r.value(self.schema);self.validate(v);return v

class StatefulCodec:
    """Existing ModelCodec binding with stateful registration/policy checks."""
    def __init__(self,model,schemas):
        state,input,output=schemas
        if hasattr(model,'validate'):state=_Validated(state,model.validate)
        if hasattr(model,'machines'):
            def validate_input(i):
                if i.kind=='local' and i.value.machine not in model.machines:raise ModelError('unknown network local machine')
                if i.kind in ('drop','duplicate') and not getattr(model.policy,'allow_'+i.kind):raise ModelError('network action disabled')
            input=_Validated(input,validate_input)
        self.bound=model_codec(state=state,input=input,output=output).bind(model)
    def __getattr__(self,name):return getattr(self.bound,name)

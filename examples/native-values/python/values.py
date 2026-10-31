"""Closed fixture adapter; all encoding and reader work uses installed public APIs."""
import json, os
import statelessness.value_codec as vc
from statelessness import EncodeBuffer, OracleState
CONTRACT=json.load(open(os.environ['STATELESS_VALUES_CONTRACT'],encoding='utf8'))
ROWS=CONTRACT['value_cases']
def layout(shape):
    for row in CONTRACT['layouts']['registry']:
        if row['schema']==shape:return vc.Rust64Layout(row['size'],row['alignment'],'measured finite Rust64 shape')
    # These named nested layouts were measured by the same Rust reference job.
    names={'unit':'Option<()>','bool':'Option<bool>','u8':'Option<u8>','u64':'Option<u64>'}
    element=shape.get('element',{})
    name=names.get(element.get('kind')) if shape.get('kind')=='option' and element=={'kind':element.get('kind')} else None
    for row in CONTRACT['layouts']['named']:
        if row['name']==name:return vc.Rust64Layout(row['size'],row['alignment'],'measured finite Rust64 shape')
    return None
class Length:
    def __init__(self,base,width):self.base,self.width,self.allocation_layout=base,width,base.allocation_layout
    def encode_into(self,value,out):self.base.encode_length(value,self.width,out)
    def decode(self,r):return self.base.decode_length(self.width,r)
def schema(d,apply_width=True):
    k=d['kind'];l=layout(d)
    if k in ('string','bool','unit') or k[0] in 'ui' and k[1:].isdigit():s=getattr(vc,{'string':'STRING','bool':'BOOL','unit':'UNIT'}.get(k,k.upper()))
    elif k=='option':s=vc.OptionOf(schema(d['element']),layout=l)
    elif k=='vector':s=vc.VectorOf(schema(d['element']),layout=l)
    elif k=='array':s=vc.ArrayOf(schema(d['element']),d['length'],layout=l)
    elif k=='tuple':s=vc.TupleOf(*(schema(f)for f in d['fields']),layout=l)
    elif k=='oracle_state':s=vc.OracleStateOf(schema(d['model']),schema(d['oracle']),layout=l)
    else:raise ValueError(k)
    return Length(s,d['length_width']) if apply_width and 'length_width'in d and d['length_width']!=4 else s

def native(d,v):
    k=d['kind']
    if k[0]in'ui' and k[1:].isdigit():return int(v)
    if k=='unit':return ()
    if k=='option':return vc.Some(native(d['element'],v['value']))if v['some']else None
    if k in('vector','array'):return [native(d['element'],x)for x in v]
    if k=='tuple':return tuple(native(f,x)for f,x in zip(d['fields'],v))
    if k=='oracle_state':return OracleState(native(d['model'],v['model']),native(d['oracle'],v['oracle']))
    return v

def normalized(d,v):
    k=d['kind']
    if k[0]in'ui' and k[1:].isdigit():return str(v)
    if k=='unit':return None
    if k=='option':return dict(some=False)if v is None else dict(some=True,value=normalized(d['element'],v.value))
    if k in('vector','array'):return [normalized(d['element'],x)for x in v]
    if k=='tuple':return [normalized(f,x)for f,x in zip(d['fields'],v)]
    if k=='oracle_state':return dict(model=normalized(d['model'],v.model),oracle=normalized(d['oracle'],v.oracle))
    return v

def decode(row,data,exact=True):
    limits=vc.DecodeLimits(**{k:int(v)for k,v in row.get('limits',{}).items()})if exact else vc.DecodeLimits()
    reader=vc.Decoder(data,limits)
    value=schema(row['schema'],False).decode_length(row['schema']['length_width'],reader)if row['entry']=='direct_length'else reader.value(schema(row['schema']))
    reader.finish();return value

def encode(row,value,maximum):return vc.encode_canonical(schema(row['schema']),value,maximum)
def rejection(f,needle=None):
    try:f()
    except Exception as error:
        if needle and needle not in str(error):raise RuntimeError('wrong error: '+str(error))from error
        return str(error)
    raise RuntimeError('expected rejection')

def controls():
    vector=vc.VectorOf(vc.UNIT)
    unit_error=rejection(lambda:vc.decode_canonical(vector,b'\xff'*4,vc.DecodeLimits(elements=16)),'decode element limit')
    r=vc.Decoder(b'\x01');calls=[]
    class CustomDecoder:
        def decode(self,reader):
            calls.append(1)
            try:reader.take(2)
            except Exception:pass
            return ()
    custom=CustomDecoder()
    sticky=rejection(lambda:r.value(custom),'truncated value');rejection(r.finish,'truncated value')
    if len(calls)!=1:raise RuntimeError('custom callback repeated')
    data=b'\x02\x01\x02';v=vc.VectorOf(vc.U8);direct=vc.Decoder(data,vc.DecodeLimits(work=3,depth=1));result=v.decode_length(1,direct);direct.finish()
    if result!=[1,2]:raise RuntimeError('direct length changed values')
    guarded=vc.Decoder(data,vc.DecodeLimits(work=3,depth=2));rejection(lambda:guarded.value(lambda q:v.decode_length(1,q)),'decode work limit')
    storage=bytearray();out=EncodeBuffer(storage,3);out.append(b'\xaa');vc.encode_into(vc.U16,4660,out);out.finish()
    if storage!=bytes.fromhex('aa3412'):raise RuntimeError('schema cleared caller prefix')
    return dict(unit_budget_rejected=True,unit_budget_error=unit_error,sticky_swallowed_error=True,sticky_error=sticky,direct_length_work_exact=True,append_preserved=True)

def run(mode,source,target):
    incoming=None
    if mode=='transcode':
        incoming=json.load(open(source,encoding='utf8'))
        if incoming.get('schema')!=CONTRACT['schema']or [x['id']for x in incoming['cases']]!=[r['case']for r in ROWS]:raise RuntimeError('bundle cases differ from closed contract')
    output=[];decoded=[];counts=dict(prefix_rejections=0,trailing_rejections=0,subview_decodes=0,owned_results=0,exact_encode_caps=0,short_encode_caps=0)
    for index,row in enumerate(ROWS):
        wire=bytes.fromhex(incoming['cases'][index]['hex'])if incoming else encode(row,native(row['schema'],row['value']),int(row['encode_max_bytes']))
        value=decode(row,wire);again=encode(row,value,len(wire))
        if again!=wire:raise RuntimeError('noncanonical typed bytes')
        counts['exact_encode_caps']+=1
        if wire:rejection(lambda:encode(row,value,len(wire)-1));counts['short_encode_caps']+=1
        actual=normalized(row['schema'],value)
        for n in range(len(wire)):rejection(lambda n=n:decode(row,wire[:n],False));counts['prefix_rejections']+=1
        rejection(lambda:decode(row,wire+b'\0',False),'trailing');counts['trailing_rejections']+=1
        backing=bytearray(b'\xff'+wire+b'\xfe');view=memoryview(backing)[1:-1];owned=decode(row,view,False);counts['subview_decodes']+=1;backing[:]=b'\0'*len(backing)
        if normalized(row['schema'],owned)!=actual:raise RuntimeError('decoded result aliases input')
        counts['owned_results']+=1;decoded.append(dict(id=row['case'],value=actual));output.append(dict(id=row['case'],hex=again.hex()))
    with open(target,'w',encoding='utf8')as f:json.dump(dict(schema=CONTRACT['schema'],cases=output),f,separators=(',',':'))
    return dict(case_count=len(ROWS),decoded_values=decoded,controls=dict(counts,**controls()))

import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {valueCodecs as vc,UNIT,NONE,some,Decoder,encodeValue,decodeValue,customValueSchema,logicalLayout,withLength,encodeLengthValue,EncodeStorage,withEncodeBuffer,ModelError} from '../dist/index.js';
const corpus=JSON.parse(readFileSync(new URL('../../conformance/canonical-value-codec-v1.json',import.meta.url),'utf8'));
const bytes=hex=>Uint8Array.from(Buffer.from(hex,'hex')),hex=value=>Buffer.from(value).toString('hex');
const limits=value=>Object.fromEntries(Object.entries(value??{}).map(([k,v])=>[k,BigInt(v)]));
function schema(desc,applyWidth=true){
 let value;
 switch(desc.kind){
  case 'string':value=vc.string;break;
  case 'option':value=vc.option(schema(desc.element));break;
  case 'vector':value=vc.vector(schema(desc.element));break;
  case 'array':value=vc.array(schema(desc.element),desc.length);break;
  case 'tuple':value=vc.tuple(desc.fields.map(d=>schema(d)));break;
  case 'oracle_state':value=vc.oracleState(schema(desc.model),schema(desc.oracle));break;
  default:value=vc[desc.kind];if(!value)throw Error(`unknown schema ${desc.kind}`);
 }
 return applyWidth&&desc.length_width!==undefined&&desc.length_width!==4?withLength(value,desc.length_width):value;
}
function native(desc,value){
 if(/^[ui](8|16|32|64|128)$/.test(desc.kind)){if(typeof value!=='string')return value;return /^(u|i)(64|128)$/.test(desc.kind)?BigInt(value):Number(value);}
 switch(desc.kind){
  case 'unit':return UNIT;
  case 'option':return value.some?some(native(desc.element,value.value)):NONE;
  case 'vector':case 'array':return value.map(v=>native(desc.element,v));
  case 'tuple':return value.map((v,i)=>native(desc.fields[i],v));
  case 'oracle_state':return {model:native(desc.model,value.model),oracle:native(desc.oracle,value.oracle)};
  default:return value;
 }
}
function normalized(desc,value){
 if(/^[ui](8|16|32|64|128)$/.test(desc.kind))return String(value);
 switch(desc.kind){
  case 'unit':assert.equal(value,UNIT);return null;
  case 'option':return value.some?{some:true,value:normalized(desc.element,value.value)}:{some:false};
  case 'vector':case 'array':return Array.from(value,v=>normalized(desc.element,v));
  case 'tuple':return value.map((v,i)=>normalized(desc.fields[i],v));
  case 'oracle_state':return {model:normalized(desc.model,value.model),oracle:normalized(desc.oracle,value.oracle)};
  default:return value;
 }
}
function guardedLength(desc){const base=schema(desc,false);return customValueSchema({name:`length-${desc.length_width}`,encodeInto:(v,out)=>base.encodeLengthRaw(v,desc.length_width,out),decodeFrom:r=>base.decodeLength(desc.length_width,r)});}
function decodeCase(row){const input=bytes(row.bytes_hex),selected=limits(row.limits);if(row.entry==='direct_length'){const reader=new Decoder(input,selected),value=schema(row.schema,false).decodeLength(row.schema.length_width,reader);reader.finish();return value;}return decodeValue(row.entry==='guarded_length'?guardedLength(row.schema):schema(row.schema),input,selected);}
function encodeCase(row,maximum=BigInt(row.encode_max_bytes)){
 const value=native(row.schema,row.value);
 if(row.entry==='direct_length'){const out=new EncodeStorage();withEncodeBuffer(out,maximum,sink=>{schema(row.schema,false).encodeLengthRaw(value,row.schema.length_width,sink);});return out.snapshot();}
 return encodeValue(row.entry==='guarded_length'?guardedLength(row.schema):schema(row.schema),value,maximum);
}
for(const row of corpus.value_cases)test(`canonical-value-codec-v1 value ${row.case}`,()=>{
 assert.equal(hex(encodeCase(row)),row.bytes_hex);
 assert.deepEqual(normalized(row.schema,decodeCase(row)),row.expected.value);
});
for(const row of corpus.decode_error_cases)test(`canonical-value-codec-v1 decode error ${row.case}`,()=>{
 assert.throws(()=>decodeCase(row),error=>{assert.equal(error.message,row.expected.error);return true;});
});
for(const row of corpus.encode_error_cases)test(`canonical-value-codec-v1 encode error ${row.case}`,()=>{
 assert.throws(()=>encodeCase(row),error=>{assert.equal(error.message,row.expected.error);return true;});
});
for(const row of corpus.length_cases.filter(c=>c.operation==='encode_length_empty'))test(`canonical-value-codec-v1 public length ${row.case}`,()=>{
 const store=new EncodeStorage();assert.throws(()=>withEncodeBuffer(store,100,sink=>schema(row.schema,false).encodeLengthRaw('',row.width,sink)),error=>{assert.equal(error.message,row.expected.error);return true;});
});
// These six rows exercise a public TypeScript-only arithmetic helper. They are
// not public-Rust execution claims: Rust's primitive is private. No container
// is fabricated and no multi-billion-element encoder loop is run.
for(const row of corpus.length_cases.filter(c=>c.operation==='write_length_primitive'))test(`native-only canonical length primitive ${row.case}`,()=>{
 const storage=new EncodeStorage();const operation=()=>withEncodeBuffer(storage,4,out=>encodeLengthValue(BigInt(row.count),row.width,out));
 if(row.expected.error)assert.throws(operation,error=>{assert.equal(error.message,row.expected.error);return true;});else{operation();assert.equal(hex(storage.snapshot()),row.expected.bytes_hex);}
});
function readerOperation(reader,operation,events){
 const desc=operation.schema;
 switch(operation.kind){
  case 'take':return hex(reader.take(BigInt(operation.count)));
  case 'collection':reader.collection(BigInt(operation.count),BigInt(operation.element_size));return null;
  case 'finish':reader.finish();return null;
  case 'value':return normalized(desc,reader.value(schema(desc)));
  case 'direct_decode':return normalized(desc,schema(desc).decodeFrom(reader));
  case 'direct_length':return normalized(desc,schema(desc,false).decodeLength(operation.width,reader));
  case 'value_custom':return reader.value(customValueSchema({name:'corpus-custom',encodeInto(){throw Error('unused encoder');},decodeFrom(r){
   events.push('enter');
   for(const action of operation.body){try{readerOperation(r,action,events);events.push(`${action.kind}:ok`);}catch(error){events.push(`${action.kind}:error:${error.message}`);if(action.on_error==='swallow')events.push('swallow');else{events.push(`propagate:${error.message}`);throw error;}}}
   if(Object.hasOwn(operation.returns,'error')){events.push(`return_error:${operation.returns.error}`);throw new ModelError(operation.returns.error);}
   events.push('return_ok');return operation.returns.ok;
  }}));
  default:throw Error(`unknown reader operation ${operation.kind}`);
 }
}
for(const row of corpus.reader_cases)test(`canonical-value-codec-v1 reader ${row.case}`,()=>{
 const results=[],events=[];let reader;
 try{reader=new Decoder(bytes(row.bytes_hex),limits(row.limits));}
 catch(error){assert.equal(error.message,row.expected.construction_error);assert.deepEqual(results,row.expected.results);assert.deepEqual(events,row.expected.callback_events);return;}
 assert.equal(row.expected.construction_error,undefined);
 for(const operation of row.actions){try{results.push({value:readerOperation(reader,operation,events),error:null});}catch(error){results.push({value:null,error:error.message});}}
 assert.deepEqual(results,row.expected.results);assert.deepEqual(events,row.expected.callback_events);
});
for(const row of corpus.native_admission_cases)test(`canonical-value-codec-v1 native admission ${row.case}`,()=>{
 let value;
 if(row.utf16_code_units)value=String.fromCharCode(...row.utf16_code_units);
 else if(row.host_value)value=Number(row.host_value.decimal);
 else value=native(row.schema,row.value);
 assert.throws(()=>encodeValue(schema(row.schema),value,1000),error=>{
  const patterns={integer_range:/integer.*range|out.of.range/i,integer_type:/integer|number|bigint/i,boolean_type:/bool/i,invalid_unicode:/unicode|surrogate|scalar/i,array_length:/array.*length|length.*array/i,integer_type_or_unsafe_number:/integer|number|bigint|safe/i};
  assert.match(error.message,patterns[row.expected_error]);return true;
 });
});
for(const row of corpus.encoder_cases)test(`canonical-value-codec-v1 raw encoder ${row.case}`,()=>{
 const storage=new EncodeStorage(),results=[];let terminal=null;
 try{withEncodeBuffer(storage,BigInt(row.max_bytes),out=>{
  for(const action of row.actions){try{
   switch(action.kind){
    case 'direct_length':schema(action.schema,false).encodeLengthRaw(native(action.schema,action.value),action.width,out);break;
    case 'direct_encode':schema(action.schema).encodeRaw(native(action.schema,action.value),out);break;
    case 'direct_encode_custom':customValueSchema({name:'raw corpus encoder',encodeInto(_value,child){try{child.extend(bytes(action.prefix_hex));}catch(error){if(!action.ignore_prefix_error)throw error;}if(action.returns_error!==null)throw new ModelError(action.returns_error);},decodeFrom(){return UNIT;}}).encodeRaw(UNIT,out);break;
    case 'append':out.extend(bytes(action.bytes_hex));break;
    case 'finish':out.finish();break;
    default:throw Error(`unknown encoder action ${action.kind}`);
   }
   results.push({value:null,error:null});
  }catch(error){results.push({value:null,error:error.message});}}
 });}catch(error){terminal=error;}
 assert.equal(results.length,row.expected.results.length);
 for(let i=0;i<results.length;i++){const actual=results[i],expected=row.expected.results[i];assert.equal(actual.value,expected.value);if(expected.error_family==='encode_limit')assert.match(actual.error,/^encoded blob exceeds byte(?: or allocation)? limit$/);else assert.equal(actual.error,expected.error);}
 assert.equal(hex(storage.snapshot()),row.expected.bytes_hex);
 if(row.expected.results.every(result=>result.error===null)||row.expected.results.at(-1).error===null)assert.equal(terminal,null);
 else assert.ok(terminal instanceof ModelError,'failed writer must reject the enclosing scope');
});

test('canonical public length overrides admit inclusive one/two-byte bounds and reject one over',()=>{
 for(const [width,count]of [[1,255],[2,65535]]){
  const value='a'.repeat(count),storage=new EncodeStorage();withEncodeBuffer(storage,BigInt(count+width),out=>vc.string.encodeLengthRaw(value,width,out));const encoded=storage.snapshot();assert.equal(encoded.length,count+width);assert.equal(encoded[0],255);if(width===2)assert.equal(encoded[1],255);
  const reader=new Decoder(encoded);assert.equal(vc.string.decodeLength(width,reader),value);reader.finish();
  const failed=new EncodeStorage();assert.throws(()=>withEncodeBuffer(failed,BigInt(count+width+10),out=>vc.string.encodeLengthRaw(value+'a',width,out)),/length overflow/);
 }
});
test('canonical owned input preserves subview offsets and decoded values do not alias mutable bytes',()=>{
 const backing=Uint8Array.of(255,2,0,0,0,1,2,254),input=backing.subarray(1,7),vector=vc.vector(vc.u8),reader=new Decoder(input);backing.fill(99);assert.deepEqual(reader.value(vector),[1,2]);reader.finish();
 const raw=new Decoder(Uint8Array.of(1,2));const first=raw.take(1);first.fill(9);if(first.buffer.byteLength>1)new Uint8Array(first.buffer).fill(9);assert.equal(raw.value(vc.u8),2);raw.finish();
});
test('custom schemas preserve guarded/raw error distinctions and first failure over later callback error',()=>{
 const invalid=customValueSchema({name:'invalid',encodeInto(){},decodeFrom(){throw new ModelError('returned failure');}}),reader=new Decoder(new Uint8Array());assert.throws(()=>invalid.decodeFrom(reader),/returned failure/);reader.finish();assert.throws(()=>reader.value(invalid),/returned failure/);assert.throws(()=>reader.take(0),/returned failure/);
 const swallowed=customValueSchema({name:'first',encodeInto(){},decodeFrom(r){try{r.take(1);}catch{}throw new ModelError('later failure');}});assert.throws(()=>decodeValue(swallowed,new Uint8Array()),/truncated value/);
});
test('nested schema encoding keeps sticky sink failures and expires retained custom sink leases',()=>{
 let escaped;const custom=customValueSchema({name:'caught write',encodeInto(_v,out){escaped=out;try{out.extend(Uint8Array.of(1,2));}catch{}},decodeFrom(){return UNIT;}});
 assert.throws(()=>encodeValue(custom,UNIT,1),/byte limit/);assert.throws(()=>escaped.extend(new Uint8Array()),/expired/);
 let inner;const child=customValueSchema({name:'child',encodeInto(_v,out){inner=out;out.extend(Uint8Array.of(1));},decodeFrom:r=>r.value(vc.u8)}),storage=new EncodeStorage();withEncodeBuffer(storage,3,out=>{child.encodeInto(1,out);assert.throws(()=>inner.extend(Uint8Array.of(2)),/expired/);out.extend(Uint8Array.of(3));});assert.deepEqual(storage.snapshot(),Uint8Array.of(1,3));
});
test('canonical limits snapshot getters once and account exact u64 collection arithmetic',()=>{
 const reads={};const options={};for(const [key,value]of Object.entries({bytes:0n,allocation:0n,elements:0n,depth:1n,work:1n}))Object.defineProperty(options,key,{enumerable:true,get(){reads[key]=(reads[key]??0)+1;return value;}});
 const reader=new Decoder(new Uint8Array(),options);assert.equal(reader.value(vc.unit),UNIT);reader.finish();assert.deepEqual(reads,{bytes:1,allocation:1,elements:1,depth:1,work:1});
 const maximum=(1n<<64n)-1n,overflow=new Decoder(new Uint8Array(),{elements:2n,allocation:maximum,work:2n});assert.throws(()=>overflow.collection(2n,maximum),/allocation overflow/);assert.throws(()=>overflow.finish(),/allocation overflow/);
 for(const bad of [NaN,Infinity,-1,1.5,Number.MAX_SAFE_INTEGER+1])assert.throws(()=>new Decoder(new Uint8Array(),{work:bad}));
});
test('unknown compound allocation layout requires an explicit cooperative descriptor',()=>{
 const unknown=vc.tuple([vc.u16,vc.u32]);assert.equal(unknown.layout,null);assert.deepEqual(decodeValue(unknown,Uint8Array.of(1,0,2,0,0,0)),[1,2]);const unknownVector=vc.vector(unknown);assert.equal(hex(encodeValue(unknownVector,[],4)),'00000000');assert.throws(()=>decodeValue(unknownVector,bytes('00000000')),/layout/i);
 const declared=vc.tuple([vc.u16,vc.u32],logicalLayout(8,'declared test tuple')),vector=vc.vector(declared),encoded=encodeValue(vector,[[1,2]],100);assert.deepEqual(decodeValue(vector,encoded,{allocation:8n,elements:1n}),[[1,2]]);assert.throws(()=>decodeValue(vector,encoded,{allocation:7n,elements:1n}),/allocation limit/);
});
test('unit options stay distinct, full 128-bit values remain bigint, and non-builtin schemas stay excluded',()=>{
 const option=vc.option(vc.unit);assert.equal(hex(encodeValue(option,NONE,1)),'00');assert.equal(hex(encodeValue(option,some(UNIT),1)),'01');assert.deepEqual(decodeValue(option,bytes('01')),some(UNIT));
 assert.equal(decodeValue(vc.i128,bytes('00000000000000000000000000000080')),-(1n<<127n));assert.equal(decodeValue(vc.u128,bytes('ffffffffffffffffffffffffffffffff')),(1n<<128n)-1n);
 for(const name of ['float','f32','f64','char','usize','isize','map','set','object'])assert.equal(vc[name],undefined);
 for(const invalid of [1,1.5,NaN,Infinity,Number.MAX_SAFE_INTEGER+1])assert.throws(()=>encodeValue(vc.u64,invalid,8),/integer|number|bigint/i);
});
test('collection schemas preserve full-u64 declared weights, zero multiplication, and overflow precedence',()=>{
 const maximum=(1n<<64n)-1n,heavy=customValueSchema({name:'declared heavy unit',layout:logicalLayout(maximum,'declared largest Rust64 request'),encodeInto(){},decodeFrom(){return UNIT;}}),vector=vc.vector(heavy);
 assert.deepEqual(decodeValue(vector,bytes('00000000'),{allocation:0n,elements:0n,work:2n,depth:2n}),[]);
 assert.throws(()=>decodeValue(vector,bytes('02000000'),{allocation:maximum,elements:2n,work:2n}),/allocation overflow/);
});
test('constructor byte admission uses intrinsic typed-array length before copying or decoding',()=>{
 const input=new Uint8Array(16);Object.defineProperty(input,'length',{value:0});Object.defineProperty(input,'byteLength',{value:0});Object.defineProperty(input,'byteOffset',{value:999});Object.defineProperty(input,'buffer',{value:new ArrayBuffer(0)});
 assert.throws(()=>new Decoder(input,{bytes:0n}),/decode byte limit/);
 const scalar=new Decoder(Uint8Array.of(7),{work:0n,depth:0n});assert.equal(vc.u8.decodeFrom(scalar),7);scalar.finish();
});
test('decoder stores the first failure independently of caught mutable Error objects',()=>{
 const reader=new Decoder(new Uint8Array());let original;
 try{reader.take(1);}catch(error){original=error;error.message='application rewrote the diagnostic';}
 assert.ok(original instanceof ModelError);
 for(const attempt of [()=>reader.take(0),()=>reader.value(vc.unit),()=>reader.collection(0,0),()=>reader.finish()]){
  assert.throws(attempt,error=>{assert.notEqual(error,original);assert.equal(error.message,'truncated value');error.message='second mutation';return true;});
 }
});
test('raw encoder callbacks preserve returned errors, expire child leases and keep actual writes sticky',()=>{
 let escaped;const custom=customValueSchema({name:'raw callback',encodeInto(_value,out){escaped=out;out.extend(bytes('aa'));throw new ModelError('raw returned failure');},decodeFrom(){return UNIT;}}),storage=new EncodeStorage();
 withEncodeBuffer(storage,2,out=>{assert.throws(()=>custom.encodeRaw(UNIT,out),/raw returned failure/);assert.throws(()=>escaped.extend(bytes('cc')),/expired/);out.extend(bytes('bb'));});assert.equal(hex(storage.snapshot()),'aabb');
 const failed=new EncodeStorage();assert.throws(()=>withEncodeBuffer(failed,0,out=>{assert.throws(()=>out.extend(bytes('ff')),/byte limit/);assert.doesNotThrow(()=>vc.unit.encodeRaw(UNIT,out));assert.throws(()=>out.finish(),/byte limit/);}),/byte limit/);
 const framed=new EncodeStorage(),framing=customValueSchema({name:'framed failure',encodeInto(_value,out){try{out.framed(()=>{throw new ModelError('framed failure');});}catch{}},decodeFrom(){return UNIT;}});
 assert.throws(()=>withEncodeBuffer(framed,16,out=>{framing.encodeRaw(UNIT,out);assert.throws(()=>out.extend(bytes('aa')),/framed failure/);}),/framed failure/);
});

test('custom directions propagate through every compound even when no elements execute',()=>{
 const enc=customValueSchema({name:'encode only',layout:logicalLayout(1,'test byte'),encodeInto:(v,out)=>vc.u8.encodeRaw(v,out)});
 const dec=customValueSchema({name:'decode only',layout:logicalLayout(1,'test byte'),decodeFrom:r=>r.value(vc.u8)});
 const variants=s=>[[s,7,'07'],[vc.option(s),NONE,'00'],[vc.vector(s),[],'00000000'],[vc.array(s,0),[],''],[vc.tuple([s]),[7],'07'],[vc.oracleState(s,vc.u8),{model:7,oracle:8},'0708']];
 for(const [codec,value,encoded]of variants(enc)){assert.equal(hex(encodeValue(codec,value,32)),encoded);assert.throws(()=>decodeValue(codec,bytes(encoded)),/has no decoder/);}
 for(const [codec,value,encoded]of variants(dec)){assert.throws(()=>encodeValue(codec,value,32),/has no encoder/);assert.deepEqual(decodeValue(codec,bytes(encoded)),value);}
 const unweighed=customValueSchema({name:'unweighed encoder',encodeInto:(v,out)=>vc.u8.encodeRaw(v,out)});assert.equal(hex(encodeValue(vc.vector(unweighed),[3],8)),'0100000003');
});
test('custom length capabilities are independent of ordinary encode and decode traits',()=>{
 const enc=customValueSchema({name:'length encoder',encodeLength(v,width,out){vc.string.encodeLengthRaw(v,width,out);}});
 const dec=customValueSchema({name:'length decoder',decodeLength(width,r){return vc.string.decodeLength(width,r);}});
 const output=new EncodeStorage();withEncodeBuffer(output,8,out=>enc.encodeLengthRaw('x',1,out));assert.equal(hex(output.snapshot()),'0178');
 assert.equal(dec.decodeLength(1,new Decoder(bytes('0178'))),'x');
 for(const codec of [enc,dec]){assert.throws(()=>encodeValue(codec,'x',8),/has no encoder/);assert.throws(()=>decodeValue(codec,bytes('0178')),/has no decoder/);}
 assert.equal(hex(encodeValue(withLength(enc,1),'x',8)),'0178');assert.throws(()=>decodeValue(withLength(enc,1),bytes('0178')),/has no decoder/);
 assert.equal(decodeValue(withLength(dec,1),bytes('0178')),'x');assert.throws(()=>encodeValue(withLength(dec,1),'x',8),/has no encoder/);
});
test('decoder formatter reentry observes a sticky placeholder before any further input consumption',()=>{
 for(const generic of [false,true]){
  const reader=new Decoder(bytes('07')),attempts=[];
  const format=()=>{for(const action of [()=>reader.take(1),()=>reader.value(vc.u8),()=>reader.collection(1,1)]){try{action();assert.fail('formatter consumed input');}catch(error){attempts.push(error.message);}}return 'original formatted failure';};
  const thrown=generic?{toString:format}:Object.defineProperty(new Error(),'message',{get:format});
  const codec=customValueSchema({name:'hostile formatter',decodeFrom(){throw thrown;}});
  assert.throws(()=>reader.value(codec),error=>error.message==='original formatted failure');assert.deepEqual(attempts,Array(3).fill('value codec callback failed'));assert.equal(reader.remaining,1n);assert.throws(()=>reader.finish(),/original formatted failure/);
 }
});
test('encoding owns sticky errors and publishes placeholder before invoking hostile formatters',()=>{
 const storage=new EncodeStorage();assert.throws(()=>withEncodeBuffer(storage,0,out=>{try{out.extend(bytes('01'));}catch(error){error.message='rewritten';}assert.throws(()=>out.finish(),error=>error.message==='encoded blob exceeds byte limit');}),/encoded blob exceeds byte limit/);
 for(const throws of [false,true]){
  let seen;const output=new EncodeStorage();assert.throws(()=>withEncodeBuffer(output,8,out=>{
   const thrown=Object.defineProperty(new Error(),'message',{get(){try{out.finish();}catch(error){seen=error.message;}if(throws)throw Error('format failed');return 'original encoder failure';}});
   const codec=customValueSchema({name:'hostile encoder formatter',encodeInto(){throw thrown;}});assert.throws(()=>codec.encodeInto(UNIT,out),error=>error.message===(throws?'codec callback failed':'original encoder failure'));
   assert.throws(()=>out.finish(),error=>error.message===(throws?'codec callback failed':'original encoder failure'));
  }),error=>error.message===(throws?'codec callback failed':'original encoder failure'));assert.equal(seen,'codec callback failed');
 }
});

test('custom length widths admit exactly native u8 values before any callback or descriptor access',()=>{
 let calls=0;const custom=customValueSchema({name:'custom width three',encodeLength(value,width,out){calls++;assert.equal(width,3);out.extend(Uint8Array.of(value));},decodeLength(width,reader){calls++;assert.equal(width,3);return reader.take(1)[0];}});
 for(const width of [NaN,Infinity,-1,1.5,256,1n,'3',null,undefined]){
  const output=new EncodeStorage();withEncodeBuffer(output,8,out=>{assert.throws(()=>custom.encodeLength(7,width,out),/length width must be a u8/);assert.throws(()=>custom.encodeLengthRaw(7,width,out),/length width must be a u8/);out.finish();});
  const reader=new Decoder(bytes('07'));assert.throws(()=>custom.decodeLength(width,reader),/length width must be a u8/);assert.equal(reader.remaining,1n);
  const descriptor=new Proxy(custom,{get(){assert.fail('invalid width touched descriptor');}});assert.throws(()=>withLength(descriptor,width),/length width must be a u8/);
 }
 assert.equal(calls,0);const output=new EncodeStorage();withEncodeBuffer(output,8,out=>custom.encodeLength(7,3,out));assert.equal(hex(output.snapshot()),'07');
 const raw=new EncodeStorage();withEncodeBuffer(raw,8,out=>custom.encodeLengthRaw(8,3,out));assert.equal(hex(raw.snapshot()),'08');
 assert.equal(custom.decodeLength(3,new Decoder(bytes('09'))),9);assert.equal(hex(encodeValue(withLength(custom,3),10,8)),'0a');assert.equal(decodeValue(withLength(custom,3),bytes('0b')),11);assert.equal(calls,5);
});

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {codecField,orderedRecord,tupleRecord,unitRecord,enumCases,taggedEnum,bindModelCodec,valueCodecs,customValueSchema,encodeValue,decodeValue,Decoder,logicalLayout,EncodeStorage,withEncodeBuffer} from '../dist/index.js';
const hex = bytes => Buffer.from(bytes).toString('hex');
const f = codecField();
const pair = orderedRecord({name:'Pair',fields:[f.value('a',valueCodecs.u8),f.value('b',valueCodecs.u16)],construct:v=>({...v})});
test('ordered records use raw concatenation, guards, and delayed construction',()=>{
 assert.equal(hex(encodeValue(pair,{b:0x302,a:1},100)), '010203');
 assert.deepEqual(decodeValue(pair,Uint8Array.of(1,2,3),{work:3,depth:2}),{a:1,b:0x302});
 assert.throws(()=>decodeValue(pair,Uint8Array.of(1,2,3),{work:2}),/work/);
 for(const bytes of [[],[1],[1,2],[1,2,3,4]]) assert.throws(()=>decodeValue(pair,Uint8Array.from(bytes)));
 let calls=0; const r=orderedRecord({name:'Delayed',fields:[f.value('a',valueCodecs.u8),f.value('b',valueCodecs.u16)],construct:v=>{calls++;return v;}});
 assert.throws(()=>decodeValue(r,Uint8Array.of(1,2))); assert.equal(calls,0);
 assert.equal(pair.layout,null);
 assert.throws(()=>decodeValue(valueCodecs.vector(pair),Uint8Array.of(0,0,0,0)),/layout/);
});
test('direct length and adapters share reader without ordinary guards',()=>{
 const log=[]; const r=orderedRecord({name:'Special',fields:[f.length('s',valueCodecs.string,1),f.adapter('a',{encode(v,out){out.extend(Uint8Array.of(v));},decode(r){log.push(r.depth);return r.take(1)[0];}})],construct:v=>({...v})});
 assert.equal(hex(encodeValue(r,{s:'abc',a:9},100)),'0361626309');
 assert.deepEqual(decodeValue(r,Uint8Array.of(3,97,98,99,9),{work:5,depth:2}),{s:'abc',a:9}); assert.deepEqual(log,[1n]);
 assert.throws(()=>f.length('s',valueCodecs.string,3),/width/);
});
test('independent direction intersections and eager require',()=>{
 const enc=customValueSchema({name:'enc',encodeInto(v,out){out.extend(Uint8Array.of(v));}}), dec=customValueSchema({name:'dec',decodeFrom:r=>r.take(1)[0]});
 const fields=[f.value('a',enc),f.value('b',dec)], r=orderedRecord({name:'Neither',fields,construct:v=>v});
 assert.throws(()=>encodeValue(r,{},100),/no encoder/); assert.throws(()=>decodeValue(r,new Uint8Array()),/no decoder/);
 assert.throws(()=>orderedRecord({name:'Required',fields,construct:v=>v,require:'both'}),/encoder/);
 const length=customValueSchema({name:'length only',encodeLength(v,w,out){out.extend(Uint8Array.of(v));},decodeLength(w,r){return r.take(1)[0];}});
 const lr=orderedRecord({name:'LengthOnly',fields:[f.length('x',length,1)],construct:v=>({...v}),require:'both'});
 assert.deepEqual(decodeValue(lr,encodeValue(lr,{x:8},9)),{x:8});
});
test('tagged enums use explicit sparse maximum tags and raw payload',()=>{
 const c=enumCases('kind'); let calls=0;
 const e=taggedEnum({name:'Event',discriminator:'kind',tagType:'u16',cases:[c.record('Data',65535,{fields:[f.value('x',valueCodecs.u8)],construct:v=>{calls++;return {kind:'Data',...v};}}),c.unit('Idle',7,()=>({kind:'Idle'}))]});
 assert.equal(hex(encodeValue(e,{kind:'Data',x:3},100)),'ffff03'); assert.equal(e.variant({kind:'Idle'}),'Idle');
 assert.deepEqual(decodeValue(e,Uint8Array.of(255,255,3),{work:3,depth:2}),{kind:'Data',x:3});
 assert.throws(()=>decodeValue(e,Uint8Array.of(8,0)),/unknown enum tag/); assert.equal(calls,1);
 const reader=new Decoder(Uint8Array.of(8,0,9)); assert.throws(()=>e.decodeFrom(reader),/unknown enum tag/); assert.equal(reader.value(valueCodecs.u8),9);reader.finish();
 assert.throws(()=>taggedEnum({name:'Bad',discriminator:'kind',cases:[c.unit('A',0,()=>({kind:'A'})),c.unit('B',0,()=>({kind:'B'}))]}),/duplicate/);
});
test('immutable declaration captures arrays and callback properties once',()=>{
 const fields=[f.value('__proto__',valueCodecs.u8)], source={name:'Captured',fields,construct:v=>v}; const r=orderedRecord(source); fields.length=0; source.construct=()=>{throw Error('mutated');};
 const v=decodeValue(r,Uint8Array.of(7));assert.equal(Object.getPrototypeOf(v),null); assert.equal(v.__proto__,7);
 assert.throws(()=>orderedRecord({name:'Sparse',fields:Array(1),construct:v=>v}),/sparse/);
 assert.throws(()=>orderedRecord({name:'Duplicate',fields:[f.value('x',valueCodecs.u8),f.value('x',valueCodecs.u8)],construct:v=>v}),/duplicate/);
});
test('unit and tuple records have no framing',()=>{
 const u=unitRecord({name:'U',construct:()=>null}); assert.equal(hex(encodeValue(u,null,0)),'');assert.equal(decodeValue(u,new Uint8Array()),null);
 const t=tupleRecord({name:'T',fields:[f.value(0,valueCodecs.u8),f.value(1,valueCodecs.u16)],construct:v=>v});assert.deepEqual(decodeValue(t,Uint8Array.of(1,2,3)),[1,770]);
});
test('field aliases reject before decoding while distinct symbols retain their identity',()=>{
 let decoded=0,constructed=0;
 const byte=customValueSchema({name:'CountedByte',encodeInto(v,out){out.extend(Uint8Array.of(v));},decodeFrom(r){decoded++;return r.take(1)[0];}});
 for(const key of [0,-0]) {
  assert.throws(()=>orderedRecord({name:'Alias',fields:[f.value(key,byte),f.value('0',byte)],construct:v=>{constructed++;return v;},require:'both'}),/duplicate field key/);
 }
 assert.equal(decoded,0);assert.equal(constructed,0);
 const a=Symbol('0'),b=Symbol('0');
 const distinct=orderedRecord({name:'Symbols',fields:[f.value(a,byte),f.value(b,byte)],construct:v=>{constructed++;return v;},require:'both'});
 const value=decodeValue(distinct,Uint8Array.of(1,2));
 assert.equal(value[a],1);assert.equal(value[b],2);assert.equal(hex(encodeValue(distinct,value,2)),'0102');
 assert.equal(decoded,2);assert.equal(constructed,1);
});
test('tuple enum constructors validate before advertising a decoder',()=>{
 const c=enumCases('kind'),fields=[f.value('x',valueCodecs.u8)];
 for(const construct of [1,0,null,false,'constructor',{}]) {
  assert.throws(()=>taggedEnum({name:'BadTuple',discriminator:'kind',cases:[c.tuple('T',1,{fields,construct,require:'both'})]}),/invalid declaration callback/);
 }
 const entry=c.tuple('T',1,{fields});
 const enc=taggedEnum({name:'EncodeTuple',discriminator:'kind',cases:[entry],require:'encode'});
 assert.equal(hex(encodeValue(enc,{kind:'T',x:7},2)),'0107');
 assert.throws(()=>decodeValue(enc,Uint8Array.of(1,7)),/no decoder/);
 assert.throws(()=>taggedEnum({name:'RequireDecoder',discriminator:'kind',cases:[entry],require:'both'}),/no decoder/);
});
test('all eight model hooks version gate first and limits are fresh per decode',()=>{
 class M { #version=2; metadata(){return {codec_version:this.#version};} setVersion(v){this.#version=v;} get marker(){return this.#version;} extra(){return this.#version;} }
 const model=Object.freeze(new M()); let limits=0;const out=customValueSchema({name:'Output',encodeInto(v,o){o.extend(Uint8Array.of(v));}});
 const bound=bindModelCodec(model,{state:valueCodecs.u8,input:valueCodecs.u8,output:out,decodeLimits(){limits++;return {work:1};}});
 for(const hook of ['encodeState','encodeInput','encodeOutput','encodeStateInto','encodeInputInto','encodeOutputInto','decodeState','decodeInput']) assert.throws(()=>bound[hook](null,null),/ModelCodec derive version/);
 const prefix=new EncodeStorage();withEncodeBuffer(prefix,10,sink=>{sink.extend(Uint8Array.of(99));const hostile=new Proxy({},{get(){throw Error('hostile value accessed');}});for(const hook of ['encodeStateInto','encodeInputInto','encodeOutputInto'])assert.throws(()=>bound[hook](hostile,sink),/ModelCodec derive version/);});assert.equal(hex(prefix.snapshot()),'63');assert.equal(limits,0);assert.equal(bound.extra(),2);assert.equal(bound.marker,2);model.setVersion(1);
 assert.equal(hex(bound.encodeOutput(7)),'07');assert.equal(limits,0);assert.equal(bound.decodeState(Uint8Array.of(3)),3);assert.equal(bound.decodeInput(Uint8Array.of(4)),4);assert.equal(limits,2);assert.equal(bound.decodeOutput,undefined);
});
test('enum tuple constructor uses a captured positional field list',()=>{
 const c=enumCases('kind'), fields=[f.value('x',valueCodecs.u8),f.value('y',valueCodecs.u16)];
 const entry=c.tuple('T',1,{fields,construct:v=>({kind:'T',x:v[0],y:v[1]})});
 const e=taggedEnum({name:'TupleCase',discriminator:'kind',cases:[entry]});
 fields.reverse();fields.map=()=>{throw Error('caller map');};
 assert.deepEqual(decodeValue(e,Uint8Array.of(1,2,3,4)),{kind:'T',x:2,y:1027});
});
test('encode-only model Output composes with record, rich replay, Recorder and evidence',async()=>{
 const {accepted,check,metadata,record,replay,replayWithObservations,Recorder,EvidenceRecorder,checkInitial,checkTurnExact}=await import('../dist/index.js');
 let outputCalls=0;
 const output=customValueSchema({name:'OnlyOutput',encodeInto(v,out){outputCalls++;out.extend(Uint8Array.of(v));}});
 const model=bindModelCodec({metadata:()=>metadata('bound','test'),initialState:()=>0,step:(s,i)=>accepted(s+i,[s+i]),cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,checkState:()=>[check('state')]},{state:valueCodecs.u8,input:valueCodecs.u8,output});
 const trace=record(model,[1,2]);assert.equal(replay(model,trace).outcome,'exact');assert.equal(replayWithObservations(model,trace).outcome,'exact');
 const config={strategy:'manual',seed:null,parameters:[]};const initial=checkInitial(model,0), turn=checkTurnExact(model,0,1,accepted(1,[1]),1n);
 const recorder=Recorder.withCheckedInitial(initial,config);recorder.observeChecked(turn);assert.equal(recorder.snapshot().steps.length,1);
 const evidence=new EvidenceRecorder(initial,0n,config,{maxSteps:3,maxRetainedBytes:100000n},{run:'bound',epoch:0n,manifest:null,source:null,environment:[]},{maxMetadataBytes:1000});
 evidence.observe(turn,{machine:null,generation:null,correlation:null,logicalTime:null,causes:[]});assert.equal(evidence.snapshot().steps.length,1);assert.equal(outputCalls,8);
});
test('structural directional callback getters are captured once',()=>{
 let enc=0,dec=0; const structural={get encodeRaw(){enc++;return (v,out)=>out.extend(Uint8Array.of(v));},get decodeFrom(){dec++;return r=>r.take(1)[0];}};
 const r=orderedRecord({name:'Structural',fields:[f.value('v',structural)],construct:v=>({...v})}); assert.equal(enc,1);assert.equal(dec,1);assert.deepEqual(decodeValue(r,encodeValue(r,{v:7},100)),{v:7});assert.equal(enc,1);assert.equal(dec,1);
 const output={get encodeRaw(){enc++;return (v,out)=>out.extend(Uint8Array.of(v));},get decodeFrom(){throw Error('output decoder must not be inspected');}};
 const bound=bindModelCodec({metadata:()=>({codec_version:1})},{state:valueCodecs.u8,input:valueCodecs.u8,output});assert.equal(hex(bound.encodeOutput(9)),'09');
});
test('private record accessors use an explicit positional constructor after field success',()=>{
 class Secret{#x;#y;constructor(x,y){this.#x=x;this.#y=y;}x(){return this.#x;}y(){return this.#y;}}
 let calls=0;const fields=codecField();const r=orderedRecord({name:'Secret',fields:[fields.access('x',valueCodecs.u8,v=>v.x()),fields.access('y',valueCodecs.u16,v=>v.y())],constructPositional:(x,y)=>{calls++;return new Secret(x,y);},require:'both'});
 assert.equal(hex(encodeValue(r,new Secret(1,770),100)),'010203');const result=decodeValue(r,Uint8Array.of(1,2,3));assert.equal(result.x(),1);assert.equal(result.y(),770);assert.equal(calls,1);assert.throws(()=>decodeValue(r,Uint8Array.of(1,2)));assert.equal(calls,1);
});

test('literal length fields, vector routes and exact independent admission budgets',()=>{
 for(const [width,text,wire,work] of [[1,'abc','03616263',5],[2,'é','0200c3a9',4],[4,'\ufeff','03000000efbbbf',5],[1,'','00',2]]) {
  let calls=0;const r=orderedRecord({name:'Text',fields:[f.length('text',valueCodecs.string,width)],construct:v=>{calls++;return {...v};}}), bytes=Buffer.from(wire,'hex'), n=Buffer.byteLength(text);
  assert.equal(hex(encodeValue(r,{text},100)),wire);
  assert.deepEqual(decodeValue(r,bytes,{bytes:bytes.length,allocation:n,elements:n,work,depth:2}),{text});assert.equal(calls,1);
  for(const limits of [{bytes:bytes.length-1},{work:work-1},{depth:1},...(n?[{allocation:n-1},{elements:n-1}]:[])]) {assert.throws(()=>decodeValue(r,bytes,limits));assert.equal(calls,1);}
 }
 const words=tupleRecord({name:'Words8',fields:[f.length(0,valueCodecs.vector(valueCodecs.u16),1)],construct:v=>v});
 for(const [value,wire,work,allocation,elements] of [[[[258,65535]],'020201ffff',4,4,2],[[[]],'00',2,0,0]]) {assert.equal(hex(encodeValue(words,value,100)),wire);assert.deepEqual(decodeValue(words,Buffer.from(wire,'hex'),{work,depth:2,allocation,elements}),value);}
 const weighted=tupleRecord({name:'Pair',fields:[f.value(0,valueCodecs.u8),f.value(1,valueCodecs.u16)],construct:v=>v,layout:logicalLayout(4,'explicit cooperative Pair')});
 const vector=valueCodecs.vector(weighted), bytes=Buffer.from('02000000073412010201','hex');
 assert.deepEqual(decodeValue(vector,bytes,{allocation:8,elements:2,work:8,depth:3}),[[7,4660],[1,258]]);assert.throws(()=>decodeValue(vector,bytes,{allocation:7}),/allocation/);
 let callbacks=0;const admission=orderedRecord({name:'Admission',fields:[f.adapter('x',{decode:r=>{callbacks++;return r.take(4);}})],construct:v=>v});
 assert.throws(()=>decodeValue(admission,new Uint8Array(4),{bytes:3}),/byte/);assert.equal(callbacks,0);
 const raw=new Decoder(Uint8Array.of(7,52,18),{work:2,depth:1});assert.deepEqual(pair.decodeFrom(raw),{a:7,b:4660});raw.finish();
 const unit=unitRecord({name:'RawUnit',construct:()=>null}), zero=new Decoder(new Uint8Array(),{work:0,depth:0});assert.equal(unit.decodeFrom(zero),null);zero.finish();
 const ordinary=orderedRecord({name:'Text',fields:[f.value('text',valueCodecs.string)],construct:v=>v});
 assert.deepEqual({...decodeValue(ordinary,Buffer.from('03000000616263','hex'),{work:6,depth:3,allocation:3,elements:3})},{text:'abc'});
 assert.throws(()=>decodeValue(ordinary,Buffer.from('03000000616263','hex'),{work:5}),/work/);assert.throws(()=>decodeValue(ordinary,Buffer.from('03000000616263','hex'),{depth:2}),/nesting/);
});
test('all raw and guarded reader scripts preserve first structural error',()=>{
 const c=enumCases('kind'), e=taggedEnum({name:'Event8',discriminator:'kind',cases:[c.unit('Idle',7,()=>({kind:'Idle'}))]});
 const raw=new Decoder(Uint8Array.of(8,9));assert.throws(()=>e.decodeFrom(raw),/unknown enum tag/);assert.equal(raw.value(valueCodecs.u8),9);raw.finish();
 const guarded=new Decoder(Uint8Array.of(8,9));for(const op of [()=>guarded.value(e),()=>guarded.value(valueCodecs.u8),()=>guarded.finish()]) assert.throws(op,/unknown enum tag/);
 for(const rawFirst of [true,false]) {let calls=0;const r=new Decoder(new Uint8Array());const callback=customValueSchema({name:'Callback',decodeFrom:r=>{calls++;assert.throws(()=>e.decodeFrom(r),/truncated/);throw Error('later callback error');}});
  if(rawFirst) assert.throws(()=>e.decodeFrom(r),/truncated/);
  assert.throws(()=>r.value(callback),/truncated/);assert.equal(calls,rawFirst?0:1);assert.throws(()=>r.finish(),/truncated/);
 }
});
test('derived failures precede construction and preserve raw sink prefixes',async()=>{
 const {withEncodeBuffer,EncodeStorage}=await import('../dist/index.js');
 let constructors=0;const record=orderedRecord({name:'Record',fields:[f.value('byte',valueCodecs.u8),f.value('ready',valueCodecs.bool),f.value('count',valueCodecs.u16)],construct:v=>{constructors++;return v;}});
 assert.throws(()=>decodeValue(record,Buffer.from('07023412','hex')),/boolean/);assert.equal(constructors,0);
 const text=orderedRecord({name:'Text8',fields:[f.length('text',valueCodecs.string,1)],construct:v=>{constructors++;return v;}});
 for(const [wire,error] of [['01ff',/UTF-8/],['0361',/truncated/]]) assert.throws(()=>decodeValue(text,Buffer.from(wire,'hex')),error);assert.equal(constructors,0);
 // Raw callback errors do not poison a healthy sink; actual write failures do.
 const adapter=orderedRecord({name:'AdapterError',fields:[f.value('prefix',valueCodecs.u8),f.adapter('value',{encode(v,out){out.extend(Uint8Array.of(170));throw Error('adapter rejected');}})]});
 const accepted=new EncodeStorage();withEncodeBuffer(accepted,10,out=>{assert.throws(()=>adapter.encodeRaw({prefix:7,value:1},out),/adapter rejected/);out.extend(Uint8Array.of(187));});assert.deepEqual([...accepted.snapshot()],[7,170,187]);
 const capped=new EncodeStorage();assert.throws(()=>withEncodeBuffer(capped,3,out=>record.encodeRaw({byte:7,ready:true,count:4660},out)),/limit/);assert.deepEqual([...capped.snapshot()],[7,1]);
 const overflow=new EncodeStorage();assert.throws(()=>withEncodeBuffer(overflow,1024,out=>text.encodeRaw({text:'a'.repeat(256)},out)),/length overflow/);assert.deepEqual([...overflow.snapshot()],[]);
});
test('directional enum admission, labels and public declaration boundaries',()=>{
 const c=enumCases('kind'), enc=customValueSchema({name:'enc',encodeInto(v,out){out.extend(Uint8Array.of(v));}}), dec=customValueSchema({name:'dec',decodeFrom:r=>r.take(1)[0]});
 for(const [codec,requirement] of [[enc,'encode'],[dec,'decode']]) {const cases=[c.unit('Idle',7,()=>({kind:'Idle'})),c.record('Value',2,{fields:[f.value('value',codec)],construct:v=>({kind:'Value',...v})})], e=taggedEnum({name:'Directional',discriminator:'kind',cases,require:requirement});
  if(requirement==='encode'){assert.equal(hex(encodeValue(e,{kind:'Idle'},1)),'07');assert.throws(()=>decodeValue(e,Uint8Array.of(7)),/no decoder/);assert.throws(()=>taggedEnum({name:'bad',discriminator:'kind',cases,require:'decode'}),/decoder/);}else{assert.deepEqual(decodeValue(e,Uint8Array.of(7)),{kind:'Idle'});assert.throws(()=>encodeValue(e,{kind:'Idle'},1),/no encoder/);}
 }
 assert.equal(pair.variant({a:7,b:4660}),null);
 for(const label of ['Idle','Waiting']) {const e=taggedEnum({name:'diagnostic',discriminator:'kind',cases:[c.unit(label,7,()=>({kind:label}))]});assert.equal(e.variant({kind:label}),label);assert.equal(hex(encodeValue(e,{kind:label},1)),'07');}
 const named=taggedEnum({name:'named',discriminator:'kind',cases:[c.record('Ready',255,{fields:[f.value('ready',valueCodecs.bool),f.value('byte',valueCodecs.u8)],construct:v=>({kind:'Ready',...v})})]});assert.equal(named.variant({kind:'Ready',ready:true,byte:9}),'Ready');assert.equal(hex(encodeValue(named,{kind:'Ready',ready:true,byte:9},3)),'ff0109');
 for(const [tag,tagType] of [[undefined,'u8'],[-1,'u8'],[256,'u8'],[4294967296,'u32']]) assert.throws(()=>taggedEnum({name:'bad',discriminator:'kind',tagType,cases:[c.unit('Idle',tag,()=>({kind:'Idle'}))]}),/invalid/);
 assert.throws(()=>taggedEnum({name:'bad',discriminator:'kind',tagType:'u64',cases:[]}),/tag type/);
 for(const extra of [{tagType:'u8'},{unknown:true},{skip:true},{default:0}]) assert.throws(()=>orderedRecord({name:'bad',fields:[],construct:()=>null,...extra}),/unknown declaration option/);
 for(const version of [0,4294967296]) assert.throws(()=>bindModelCodec({metadata:()=>({codec_version:1})},{state:valueCodecs.u8,input:valueCodecs.u8,output:enc,version}),/version/);
 assert.throws(()=>bindModelCodec({metadata:()=>({codec_version:1})},{state:valueCodecs.u8,input:valueCodecs.u8,output:enc,unknown:true}),/unknown/);
});
test('binder exact custom limits vary freshly and encoders never consult decode limits',async()=>{
 const {EncodeStorage,withEncodeBuffer}=await import('../dist/index.js');let calls=0,work=3;
 const model=bindModelCodec({metadata:()=>({codec_version:1})},{state:pair,input:pair,output:pair,decodeLimits:()=>{calls++;return {bytes:3,allocation:0,elements:0,work,depth:2};}});
 for(const hook of ['decodeState','decodeInput']) {assert.deepEqual(model[hook](Uint8Array.of(7,52,18)),{a:7,b:4660});work=2;assert.throws(()=>model[hook](Uint8Array.of(7,52,18)),/work/);work=3;assert.deepEqual(model[hook](Uint8Array.of(7,52,18)),{a:7,b:4660});for(const bytes of [[],[7],[7,52],[7,52,18,0]]) assert.throws(()=>model[hook](Uint8Array.from(bytes)));}
 assert.equal(calls,14);
 for(const kind of ['State','Input','Output']) {assert.equal(hex(model[`encode${kind}`]({a:7,b:4660})),'073412');const storage=new EncodeStorage();withEncodeBuffer(storage,4,out=>{out.extend(Uint8Array.of(99));model[`encode${kind}Into`]({a:7,b:4660},out);});assert.equal(hex(storage.snapshot()),'63073412');}assert.equal(calls,14);
 const defaults=bindModelCodec({metadata:()=>({codec_version:1})},{state:pair,input:pair,output:pair});assert.deepEqual(defaults.decodeState(Uint8Array.of(7,52,18)),{a:7,b:4660});for(const hook of ['decodeState','decodeInput'])assert.throws(()=>defaults[hook](Uint8Array.of(7,52,18,0)),/trailing value bytes/);const c=enumCases('kind'),event=taggedEnum({name:'Event8',discriminator:'kind',cases:[c.unit('Idle',7,()=>({kind:'Idle'}))]}),eventModel=bindModelCodec({metadata:()=>({codec_version:1})},{state:event,input:event,output:event});for(const hook of ['decodeState','decodeInput'])assert.throws(()=>eventModel[hook](Uint8Array.of(7,0)),/trailing value bytes/);
});
test('all 25 independently authored positive rows use actual public native declarations',async()=>{
 const {readFile}=await import('node:fs/promises');const draft=JSON.parse(await readFile(new URL('../../docs/native-parity/derive-model-draft/cases.json',import.meta.url),'utf8'));
 const cache=new Map();
 const special={encode_only_u8:customValueSchema({name:'encode-only',encodeInto(v,out){out.extend(Uint8Array.of(v));}}),decode_only_u8:customValueSchema({name:'decode-only',decodeFrom:r=>r.take(1)[0]}),length_only_string:customValueSchema({name:'length-only',encodeLength(v,w,out){valueCodecs.string.encodeLengthRaw(v,w,out);},decodeLength(w,r){return valueCodecs.string.decodeLength(w,r);}})};
 function schema(name){if(typeof name==='object')return valueCodecs.vector(schema(name.vector));if(name in valueCodecs)return valueCodecs[name];if(name in special)return special[name];if(cache.has(name))return cache.get(name);const d=draft.schema_registry[name];let codec;
  function fields(entries,access=v=>v){return entries.map(e=>{const key=e.name,child=schema(e.codec);if(e.adapter==='be_u16')return f.adapter(key,{encode(v,out){out.extend(Uint8Array.of(v>>8,v&255));},decode(r){const b=r.take(2);return b[0]*256+b[1];}});if(e.length_width)return f.length(key,child,e.length_width);return f.access(key,child,v=>access(v)[key]);});}
  if(d.kind==='vector'){const pair=draft.schema_registry[d.element];codec=valueCodecs.vector(tupleRecord({name:d.element,fields:fields(pair.fields),construct:v=>v,layout:logicalLayout(d.declared_element_weight,'draft cooperative weight')}));}
  else if(d.kind==='record'){const construct=d.shape==='unit'?()=>null:d.shape==='tuple'?v=>v:v=>({...v});codec=d.shape==='unit'?unitRecord({name,construct}):(d.shape==='tuple'?tupleRecord:orderedRecord)({name,fields:fields(d.fields),construct,require:d.requested_direction});}
  else{const c=enumCases('case');codec=taggedEnum({name,discriminator:'case',tagType:`u${d.tag_width*8}`,require:d.requested_direction,cases:d.cases.map(e=>e.shape==='unit'?c.unit(e.name,e.tag,()=>({case:e.name,fields:null})):c.record(e.name,e.tag,{fields:fields(e.fields,v=>v.fields),constructPositional:(...values)=>({case:e.name,fields:e.shape==='tuple'?values:Object.fromEntries(e.fields.map((f,i)=>[f.name,values[i]]))})}))});}
  cache.set(name,codec);return codec;
 }
 for(const row of draft.positive_cases){const codec=schema(row.schema),expected=row.expected,bytes=Buffer.from(row.bytes_hex,'hex');
  if(row.operation!=='decode')assert.equal(hex(encodeValue(codec,row.value,bytes.length)),row.bytes_hex,row.case);
  if(row.operation!=='encode')assert.deepEqual(decodeValue(codec,bytes,{work:expected.min_work,depth:expected.min_depth,allocation:expected.allocation,elements:expected.elements}),expected.value,row.case);
  if(expected.supports_encode===false)assert.throws(()=>encodeValue(codec,expected.value,100),/no encoder/,row.case);
  if(expected.supports_decode===false)assert.throws(()=>decodeValue(codec,bytes),/no decoder/,row.case);
 }
});
test('length and adapter required directions reject at public builder before operations',()=>{
 const length=customValueSchema({name:'decode-length-only',decodeLength(w,r){return r.take(1)[0];}});
 assert.throws(()=>orderedRecord({name:'bad',fields:[f.length('x',length,1)],construct:v=>v,require:'encode'}),/no encoder/);
 assert.throws(()=>orderedRecord({name:'bad',fields:[f.adapter('x',{encode(v,out){out.extend(Uint8Array.of(v));}})],construct:v=>v,require:'decode'}),/no decoder/);
});
test('actual raw big-endian failed write is sticky and unknown tags call no payload',async()=>{
 const {EncodeStorage,withEncodeBuffer}=await import('../dist/index.js');const be=tupleRecord({name:'BigEndian',fields:[f.adapter(0,{encode(v,out){out.extend(Uint8Array.of(v>>8,v&255));},decode(r){const b=r.take(2);return b[0]*256+b[1];}})],construct:v=>v});
 const storage=new EncodeStorage();assert.throws(()=>withEncodeBuffer(storage,1,out=>{assert.throws(()=>be.encodeRaw([4660],out),/limit/);assert.throws(()=>out.extend(Uint8Array.of(187)),/limit/);}),/limit/);assert.equal(hex(storage.snapshot()),'');
 let payloads=0,constructors=0;const payload=customValueSchema({name:'payload',decodeFrom:r=>{payloads++;return r.take(1)[0];}}),c=enumCases('kind'),e=taggedEnum({name:'unknown',discriminator:'kind',cases:[c.record('Value',7,{fields:[f.value('x',payload)],construct:v=>{constructors++;return {kind:'Value',...v};}})]});
 assert.throws(()=>decodeValue(e,Uint8Array.of(8,255,255)),/unknown enum tag/);assert.equal(payloads,0);assert.equal(constructors,0);
});

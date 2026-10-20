import {test} from 'node:test';
import assert from 'node:assert/strict';
import {codecField,orderedRecord,tupleRecord,unitRecord,enumCases,taggedEnum,bindModelCodec,valueCodecs,customValueSchema,encodeValue,decodeValue,Decoder,logicalLayout} from '../dist/index.js';
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
test('all eight model hooks version gate first and limits are fresh per decode',()=>{
 class M { #version=2; metadata(){return {codec_version:this.#version};} setVersion(v){this.#version=v;} get marker(){return this.#version;} extra(){return this.#version;} }
 const model=Object.freeze(new M()); let limits=0;const out=customValueSchema({name:'Output',encodeInto(v,o){o.extend(Uint8Array.of(v));}});
 const bound=bindModelCodec(model,{state:valueCodecs.u8,input:valueCodecs.u8,output:out,decodeLimits(){limits++;return {work:1};}});
 for(const hook of ['encodeState','encodeInput','encodeOutput','encodeStateInto','encodeInputInto','encodeOutputInto','decodeState','decodeInput']) assert.throws(()=>bound[hook](null,null),/ModelCodec derive version/);
 assert.equal(limits,0);assert.equal(bound.extra(),2);assert.equal(bound.marker,2);model.setVersion(1);
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
 evidence.observe(turn,{machine:null,generation:null,correlation:null,logicalTime:null,causes:[]});assert.equal(evidence.snapshot().steps.length,1);assert.ok(outputCalls>=8);
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

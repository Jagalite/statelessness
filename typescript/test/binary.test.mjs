import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {spawnSync} from 'node:child_process';
import {readAuditTrace,writeAuditTrace,writeAuditTraceTo,auditCRC32,BinaryError,DEFAULT_BINARY_LIMITS,auditTraceFromPortable,auditTraceToPortable} from '../dist/index.js';
const corpus=JSON.parse(readFileSync(new URL('../../conformance/native-binary-v1.json',import.meta.url),'utf8'));
const bytes=hex=>Uint8Array.from(Buffer.from(hex,'hex'));
function native(t){return {metadata:{...t.metadata},config:{strategy:t.config.strategy,seed:t.config.seed===null?null:BigInt(t.config.seed),parameters:t.config.parameters.map(p=>[...p])},initialState:bytes(t.initial_state),initialChecks:t.initial_checks.map(check),steps:t.steps.map(s=>({input:bytes(s.input),disposition:{kind:s.disposition.kind,reason:s.disposition.reason??''},outputs:s.outputs.map(bytes),postState:bytes(s.post_state),checks:s.checks.map(check)})),termination:t.termination.kind,error:t.termination.reason??''};}
const check=c=>({id:c.id,status:c.status,details:c.reason??''});
const valid=corpus.fixtures.filter(f=>f.valid),fixture=id=>corpus.fixtures.find(f=>f.case===id);
const error=(kind,detail)=>e=>e instanceof BinaryError&&e.kind===kind&&(detail===undefined||e.detail===detail);
test('hostile native promise species remains an isolated unhandled rejection',()=>{
 const moduleURL=new URL('../dist/index.js',import.meta.url).href;
 const program=`import {writeAuditTraceTo,writeTraceV2To} from ${JSON.stringify(moduleURL)};
 let seen=0; process.on('unhandledRejection', e=>{ if(e.message==='hostile rejection') seen++; });
 class Hostile extends Promise { static get [Symbol.species]() { throw Error('species'); } }
 const trace={metadata:{name:'x',model_version:1,properties_version:1,codec_version:1,build:'x'},config:{strategy:'x',seed:null,parameters:[]},initialState:new Uint8Array(),initialChecks:[],steps:[],termination:'completed',error:''};
 const evidence={trace,checkpointAfter:0n,provenance:{run:'x',epoch:0n,manifest:null,source:null,environment:[]},steps:[]};
 let rejected=0; for(const operation of [()=>writeAuditTraceTo(trace,{write(){return Hostile.reject(Error('hostile rejection'));}}),()=>writeTraceV2To(evidence,{write(){return Hostile.reject(Error('hostile rejection'));}})]) {try {operation();} catch(e){if(String(e).includes('synchronously')) rejected++;}}
 setTimeout(()=>{ if(seen!==2 || rejected!==2) process.exitCode=1; },20);`;
 const run=spawnSync(process.execPath,['--input-type=module','-e',program],{encoding:'utf8',timeout:5000});
 assert.equal(run.error,undefined);assert.equal(run.status,0,run.stderr);
});
test('binary defaults and independent CRC check vector',()=>{assert.deepEqual(DEFAULT_BINARY_LIMITS,corpus.defaults);assert.equal(auditCRC32(new TextEncoder().encode('123456789')),0xcbf43926);});
for(const f of corpus.fixtures)test(`binary-v1 ${f.case}`,()=>{
 const data=bytes(f.bytes_hex);
 if(!f.valid){assert.throws(()=>readAuditTrace(data,f.limits),error(f.expected_error.kind,f.expected_error.detail));return;}
 const wanted=native(f.trace),got=readAuditTrace(data,f.limits);assert.deepEqual(got,wanted);assert.deepEqual(writeAuditTrace(wanted,f.limits),data);assert.deepEqual(writeAuditTrace(got,f.limits),data);
});
test('every proper valid artifact prefix is rejected, including every checksum/footer byte',()=>{
 for(const f of valid){const b=bytes(f.bytes_hex);for(let n=0;n<b.length;n++)assert.throws(()=>readAuditTrace(b.subarray(0,n)),BinaryError,`${f.case}: ${n}/${b.length}`);}
});
test('chunked readers preserve all bytes and detect I/O/short EOF, including footer EOF probe',()=>{
 const f=fixture('unicode-config-full-u64'),b=bytes(f.bytes_hex);
 for(const chunk of [1,2,3,7,13,4096]){let p=0;const r={read(out){const n=Math.min(chunk,out.length,b.length-p);out.set(b.subarray(p,p+n));p+=n;return n;}};assert.deepEqual(readAuditTrace(r),native(f.trace));}
 assert.throws(()=>readAuditTrace({read(){throw Error('reader failed');}}),error('io','reader failed'));
 assert.throws(()=>readAuditTrace({read(){return 0;}}),error('truncated',null));
 let p=0;assert.throws(()=>readAuditTrace({read(out){if(p===b.length)throw Error('EOF probe failed');const n=Math.min(out.length,b.length-p);out.set(b.subarray(p,p+n));p+=n;return n;}}),error('io','EOF probe failed'));
 assert.throws(()=>readAuditTrace({read(out){return out.length+1;}}),error('invalid_data','reader byte count'));
});
test('hostile thrown values cannot escape binary error classification',()=>{
 const t=native(fixture('empty').trace);
 const failures=[{toString(){throw Error('coercion');}},new Proxy({}, {getPrototypeOf(){throw Error('prototype');}}),Object.defineProperty(new Error(), 'message', {get(){throw Error('message');}}),new BinaryError('limit_exceeded','external')];
 for(const failure of failures){
  assert.throws(()=>readAuditTrace({read(){throw failure;}}),error('io'));
  assert.throws(()=>writeAuditTraceTo(t,{write(){throw failure;}}),error('io'));
  const malformed=native(fixture('empty').trace);Object.defineProperty(malformed.metadata,'name',{get(){throw failure;}});
  assert.throws(()=>writeAuditTrace(malformed),error('invalid_data'));
 }
 assert.throws(()=>readAuditTrace({read(){throw new BinaryError('limit_exceeded','from reader');}}),error('io','limit_exceeded: from reader'));
 let prior;try{const oversized=native(fixture('single-step').trace);writeAuditTrace(oversized,{max_blob_bytes:0});}catch(e){prior=e;}
 assert.equal(prior.kind,'limit_exceeded');
 const later=native(fixture('empty').trace);Object.defineProperty(later.metadata,'name',{get(){throw prior;}});
 assert.throws(()=>writeAuditTrace(later),error('invalid_data'));
});
test('writer validates late fields and every quota before publishing any byte',()=>{
 const rich=native(fixture('unicode-config-full-u64').trace),single=native(fixture('single-step').trace);
 const cases=[ [rich,{max_total_bytes:0}],[rich,{max_frame_bytes:0}],[single,{max_blob_bytes:0}],[rich,{max_string_bytes:0}],[single,{max_steps:0}],[single,{max_checks_per_step:0}],[single,{max_outputs_per_step:0}],[rich,{max_parameters:0}],[single,{max_items:0}] ];
 for(const [t,l]of cases){let calls=0;assert.throws(()=>writeAuditTraceTo(t,{write(){calls++;}},l),error('limit_exceeded'));assert.equal(calls,0);}
 for(const mutate of [t=>t.steps.at(-1).checks.push({id:'bad',status:'unknown',details:''}),t=>t.error='unrepresentable',t=>t.metadata.name='\ud800',t=>t.config.seed=Number.MAX_SAFE_INTEGER,t=>t.metadata.model_version=2**32,t=>t.config.seed=1n<<64n,t=>t.config.seed=-1n]){
  const t=native(fixture('single-step').trace);mutate(t);let calls=0;assert.throws(()=>writeAuditTraceTo(t,{write(){calls++;}}));assert.equal(calls,0);
 }
 assert.throws(()=>writeAuditTrace(single,{max_items:Number.MAX_SAFE_INTEGER+1}),BinaryError);
 let called=false;assert.throws(()=>writeAuditTraceTo(single,{write(){called=true;throw Error('writer failed');}}),error('io','writer failed'));assert.equal(called,true);
 for(const result of [0, false, Promise.reject(Error('async writer'))])
  assert.throws(()=>writeAuditTraceTo(single,{write(){return result;}}),error('io','audit writer must return undefined synchronously'));
 let touched=false;
 for(const result of [runInNewContext("Promise.reject(new Error('foreign writer'))"), {get then(){touched=true;throw Error('must not inspect then');}}])
  assert.throws(()=>writeAuditTraceTo(single,{write(){return result;}}),error('io','audit writer must return undefined synchronously'));
 assert.equal(touched,false);
});
test('lossless explicit conversions retain run config, duplicate pairs, seed and error reason',()=>{
 for(const f of valid){const t=native(f.trace),{trace,config}=auditTraceToPortable(t);assert.deepEqual(auditTraceFromPortable(trace,config),t);assert.equal(Object.hasOwn(trace,'config'),false);if(t.config.parameters.length){config.parameters[0][0]='changed';assert.notEqual(t.config.parameters[0][0],'changed');}}
 const t=native(fixture('single-step').trace),{trace}=auditTraceToPortable(t);trace.error='unsupported';assert.throws(()=>auditTraceFromPortable(trace,t.config),BinaryError);
});

test('conversion owns Buffer bytes and fields are captured once before writer preflight',()=>{
 const t=native(fixture('single-step').trace);
 t.initialState=Buffer.from([1]);t.steps[0].input=Buffer.from([2]);t.steps[0].outputs=[Buffer.from([3])];t.steps[0].postState=Buffer.from([4]);
 const audit=auditTraceFromPortable(t,t.config),narrow=auditTraceToPortable(t);
 for(const src of [t.initialState,t.steps[0].input,t.steps[0].outputs[0],t.steps[0].postState])src[0]=99;
 for(const copy of [audit,narrow.trace])assert.deepEqual([copy.initialState[0],copy.steps[0].input[0],copy.steps[0].outputs[0][0],copy.steps[0].postState[0]],[1,2,3,4]);
 const g=native(fixture('single-step').trace);let reads=0;Object.defineProperty(g.metadata,'model_version',{get(){return ++reads===1?1:2**32;}});
 assert.equal(readAuditTrace(writeAuditTrace(g)).metadata.model_version,1);assert.equal(reads,1);
 for(const mutate of [t=>t.metadata=null,t=>t.config=null,t=>t.steps=[null],t=>t.steps=[,],t=>t.initialChecks=[null],t=>t.steps[0].disposition=null]){
  const malformed=native(fixture('single-step').trace);mutate(malformed);let calls=0;assert.throws(()=>writeAuditTraceTo(malformed,{write(){calls++;}}),error('invalid_data'));assert.equal(calls,0);
 }
});
test('writer snapshots bounded indexed collections without invoking custom array iterators',()=>{
 const t=native(fixture('empty').trace);let iteratorCalls=0;
 t.steps[Symbol.iterator]=function*(){iteratorCalls++;for(;;)yield null;};
 t.initialChecks[Symbol.iterator]=function*(){iteratorCalls++;for(;;)yield null;};
 const got=readAuditTrace(writeAuditTrace(t));assert.equal(got.steps.length,0);assert.equal(got.initialChecks.length,0);assert.equal(iteratorCalls,0);
 const s=native(fixture('single-step').trace);let reads=0;const original=s.steps;s.steps=new Proxy(original,{get(target,key,receiver){if(key==='length'){reads++;return reads===1?1:2**32-1;}return Reflect.get(target,key,receiver);}});
 assert.equal(readAuditTrace(writeAuditTrace(s)).steps.length,1);assert.equal(reads,1);
});
test('byte quotas measure intrinsic typed-array storage instead of overridden length',()=>{
 const t=native(fixture('empty').trace);t.initialState=new Uint8Array(1);Object.defineProperty(t.initialState,'length',{value:0});
 assert.throws(()=>writeAuditTrace(t,{max_blob_bytes:0}),error('limit_exceeded','blob bytes'));
 assert.throws(()=>auditTraceFromPortable(t,t.config,{max_blob_bytes:0}),error('limit_exceeded','blob bytes'));
});

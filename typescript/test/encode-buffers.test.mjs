import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {EncodeStorage,withEncodeBuffer,EncodeBuffer,WithOracle,Paired,automatic,metadata,accepted,record,recordAudit,replay,writeAuditTrace,DEFAULT_BINARY_LIMITS,check,ModelError} from '../dist/index.js';
import {encodeModel} from '../dist/encoding.js';
const hex=b=>Buffer.from(b).toString('hex'),bytes=h=>Uint8Array.from(Buffer.from(h,'hex'));
const corpus=JSON.parse(readFileSync(new URL('../../conformance/encode-buffers-v1.json',import.meta.url),'utf8'));
for(const row of corpus.cases)test(`encode-buffers-v1 ${row.case}`,()=>{
 const storage=new EncodeStorage(),events=[];let results=[],ok=true;
 const run=(buffer,actions)=>actions.map(action=>{let children=[],ok=true;try{
  if(action.kind==='append')buffer.extend(bytes(action.hex));else if(action.kind==='legacy')buffer.extendFromVec(bytes(action.hex));else buffer.framed(child=>{children=run(child,action.actions);if(action.callback_error||!action.ignore_child_errors&&children.some(r=>!r.ok))throw Error('fixture encoder error');});
 }catch{ok=false;}return {ok,children};});
 const make=(mode='legacy')=>({initialState:()=>0,cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,equalStates:(a,b)=>a===b,equalInputs:(a,b)=>a===b,inputs:()=>[],step:s=>accepted(s),checkState:()=>[],metadata:()=>metadata('encoding'),decodeState:b=>Number(new TextDecoder().decode(b)),decodeInput:b=>Number(new TextDecoder().decode(b)),
  ...Object.fromEntries(['state','input','output'].flatMap(kind=>{const title=kind[0].toUpperCase()+kind.slice(1);return [[`encode${title}`,v=>{if(mode==='into')throw Error('legacy called');events.push(`legacy_${kind}`);return new TextEncoder().encode(String(v));}],...(mode==='into'?[[`encode${title}Into`,(v,b)=>{events.push(`into_${kind}`);const data=new TextEncoder().encode(String(v));b.extend(data.subarray(0,1));b.extend(data.subarray(1));}]]:[])];}))});
 const oracle=(mode='legacy')=>({metadata:()=>metadata('oracle'),initialState:()=>0,advance:h=>h,cloneHistory:h=>h,equalHistory:(a,b)=>a===b,checkState:()=>[],decodeHistory:b=>Number(new TextDecoder().decode(b)),encodeHistory:h=>{if(mode==='into')throw Error('legacy history called');events.push('legacy_history');return new TextEncoder().encode(String(h));},...(mode==='into'?{encodeHistoryInto(h,b){events.push('into_history');b.extend(new TextEncoder().encode(String(h)));}}:{})});
 try{
  if(row.operation==='buffer'){
   withEncodeBuffer(storage,bytes(row.initial_hex).length,b=>b.extend(bytes(row.initial_hex)));
   withEncodeBuffer(storage,BigInt(row.maximum),b=>{results=run(b,row.actions);});
  }else{
   let m=make(row.mode),value=12,kind=row.encoder??'state';
   if(row.operation==='automatic')m=automatic(m);
   if(['oracle','nested','paired'].includes(row.operation)){
    const o=row.operation==='paired'?new Paired(make(row.history_mode),{metadata:()=>metadata('p'),actualState:s=>s,referenceState:s=>s,actualEffect:o=>o,referenceEffect:o=>o,equalState:(a,b)=>a===b,equalEffect:(a,b)=>a===b}):oracle(row.history_mode);
    m=new WithOracle(m,o);value={model:12,oracle:34};if(row.operation==='nested'){m=new WithOracle(m,oracle(row.history_mode));value={model:value,oracle:56};}
   }
   encodeModel(m,kind,value,storage,BigInt(row.maximum));
  }
 }catch{ok=false;}
 assert.deepEqual({finish_ok:ok,encoded_hex:ok?hex(storage.snapshot()):null,results,events},row.expected);
});
test('encoding capacity reuse, atomic chunks, mutable views, framed child expiry and later reuse',()=>{
 const storage=new EncodeStorage(64);let root,child;const source=Uint8Array.of(1,2);
 withEncodeBuffer(storage,2n,b=>{root=b;b.extend(source);source[0]=9;});assert.equal(storage.capacity,64);assert.equal(hex(storage.snapshot()),'0102');assert.throws(()=>root.length,/lease expired/);
 withEncodeBuffer(storage,0n,()=>{});assert.equal(storage.length,0);assert.equal(storage.capacity,64);
 withEncodeBuffer(storage,10n,b=>{b.framed(c=>{child=c;c.extend(Uint8Array.of(3));});assert.throws(()=>child.extend(Uint8Array.of(4)),/lease expired/);b.extend(Uint8Array.of(5));});assert.equal(hex(storage.snapshot()),'01000000000000000305');
 for(const op of [()=>root.extend(new Uint8Array()),()=>root.snapshot(),()=>root.finish(),()=>root.framed(()=>{})])assert.throws(op,/lease expired/);
 assert.throws(()=>withEncodeBuffer(storage,2n,b=>{b.extend(Uint8Array.of(1));try{b.extend(Uint8Array.of(2,3));}catch{}assert.throws(()=>b.snapshot());}));assert.equal(hex(storage.snapshot()),'01');
 assert.throws(()=>new EncodeBuffer(),/issued/);
});
test('codec callback error wins a previously caught limit error; successful return cannot clear sticky failure',()=>{
 const storage=new EncodeStorage();assert.throws(()=>withEncodeBuffer(storage,0n,b=>{try{b.extend(Uint8Array.of(1));}catch{}throw Error('explicit codec error');}),/explicit codec error/);
 assert.throws(()=>withEncodeBuffer(storage,20n,b=>{try{b.framed(c=>{c.extend(Uint8Array.of(1));throw Error('nested callback');});}catch{}}),/nested callback/);
 for(const maximum of [-1n,1n<<64n,Number.MAX_SAFE_INTEGER+1,NaN])assert.throws(()=>withEncodeBuffer(storage,maximum,()=>{}));
 let saved;assert.throws(()=>withEncodeBuffer(storage,1n,b=>{saved=b;return Promise.resolve();}),/undefined synchronously/);assert.throws(()=>saved.length,/lease expired/);
});
test('codec byte limits use intrinsic lengths and validate before copying',()=>{
 const b=Uint8Array.of(1,2);Object.defineProperty(b,'length',{value:0});const s=new EncodeStorage();assert.throws(()=>withEncodeBuffer(s,1n,out=>out.extend(b)),/byte limit/);assert.equal(s.length,0);
});
function codec(extra={}){return {initialState:()=>0,cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,step:(s,i)=>accepted(s+i,[s]),checkState:()=>[check('ok')],metadata:()=>metadata('incremental','test'),decodeState:b=>Number(new TextDecoder().decode(b)),decodeInput:b=>Number(new TextDecoder().decode(b)),encodeState(){throw Error('legacy state');},encodeInput(){throw Error('legacy input');},encodeOutput(){throw Error('legacy output');},encodeStateInto(s,b){b.extend(new TextEncoder().encode(String(s)));},encodeInputInto(i,b){b.extend(new TextEncoder().encode(String(i)));},encodeOutputInto(o,b){b.extend(new TextEncoder().encode(String(o)));},...extra};}
test('portable record/replay all codec paths prefer Into; Audit recorder stamps config and remains wire-valid',()=>{
 const m=codec(),trace=record(m,[1,2]);assert.equal(trace.steps.length,2);assert.equal(replay(m,trace).outcome,'exact');
 const audit=recordAudit(m,[1,2],{strategy:'manual',seed:0n,parameters:[['duplicate','a'],['duplicate','b']]});assert.equal(audit.termination,'completed');assert.deepEqual(audit.config.parameters.slice(-2),[['stateless.engine.version','0.1.0'],['stateless.engine.build','native-typescript-v1']]);assert.ok(writeAuditTrace(audit).length>0);
 let called=0;assert.throws(()=>recordAudit(codec({initialState(){called++;return 0;}}),[],{strategy:'manual',seed:null,parameters:[['stateless.engine.build','spoof']]}),/reserved/);assert.equal(called,0);
});
test('audit recorder does not commit over-budget turns and shares output/state byte headroom',()=>{
 const m=codec({step:s=>accepted(s+1,[0,0]),encodeOutputInto(_o,b){b.extend(new Uint8Array(100));}});
 const t=recordAudit(m,[1],{strategy:'manual',seed:null,parameters:[]},100,{max_blob_bytes:200,max_frame_bytes:230});assert.equal(t.termination,'model_error');assert.equal(t.steps.length,0);assert.ok(writeAuditTrace(t,{max_blob_bytes:200,max_frame_bytes:230}).length>0);
 const oversized=recordAudit(codec({encodeOutputInto(_o,b){try{b.extend(new Uint8Array(8));}catch{}}}),[1],{strategy:'manual',seed:null,parameters:[]},100,{max_blob_bytes:2});assert.equal(oversized.termination,'model_error');assert.equal(oversized.steps.length,0);
});
test('Into callback leases expire after errors, nested writes and reuse; malformed hooks never fall back',()=>{
 const storage=new EncodeStorage();let expired;
 const model=codec({encodeStateInto(_s,b){expired=b;b.extend(Uint8Array.of(1));throw Error('encoder failed');}});
 assert.throws(()=>encodeModel(model,'state',0,storage,1n),/encoder failed/);assert.throws(()=>expired.snapshot(),/lease expired/);
 withEncodeBuffer(storage,1n,b=>{assert.throws(()=>expired.extend(new Uint8Array()),/lease expired/);b.extend(Uint8Array.of(9));});assert.equal(hex(storage.snapshot()),'09');
 for(const hook of [false,0,'',null]){let legacy=0;assert.throws(()=>encodeModel(codec({encodeStateInto:hook,encodeState(){legacy++;return new Uint8Array();}}),'state',0,storage,0n),/must be a function/);assert.equal(legacy,0);}
});
test('codec hooks preserve private frozen receivers through automatic and nested Oracle framing',()=>{
 class C {#value=7;initialState(){return 0;}cloneState(s){return s;}cloneInput(i){return i;}cloneOutput(o){return o;}inputs(){return [];}equalInputs(a,b){return a===b;}equalStates(a,b){return a===b;}step(s){return accepted(s);}checkState(){return [];}metadata(){return metadata('frozen');}decodeState(b){return b[0];}decodeInput(b){return b[0];}encodeState(){throw Error('legacy');}encodeInput(){throw Error('legacy');}encodeOutput(){throw Error('legacy');}encodeStateInto(_s,b){b.extend(Uint8Array.of(this.#value));}encodeInputInto(_i,b){b.extend(Uint8Array.of(this.#value));}encodeOutputInto(_o,b){b.extend(Uint8Array.of(this.#value));}}
 const m=automatic(Object.freeze(new C())),storage=new EncodeStorage();for(const kind of ['state','input','output'])assert.equal(hex(encodeModel(m,kind,0,storage,1n)),'07');
});
test('record options and codec getters are captured once; legacy allocators run once',()=>{
 const m=codec();let reads=0,calls=0;Object.defineProperty(m,'encodeStateInto',{get(){reads++;if(reads>1)throw Error('encoder getter reread in one dispatch');return (_s,b)=>{calls++;b.extend(Uint8Array.of(0));};}});
 const storage=new EncodeStorage();encodeModel(m,'state',0,storage,1n);assert.equal(reads,1);assert.equal(calls,1);
 let legacy=0;encodeModel(codec({encodeStateInto:undefined,encodeState(){legacy++;return Uint8Array.of(1);}}),'state',0,storage,1n);assert.equal(legacy,1);
});
test('audit recording enforces all nine independent binary quotas and reserves future error footer',()=>{
 const m=codec(),config={strategy:'manual',seed:null,parameters:[]};
 for(const limits of [{max_total_bytes:0},{max_frame_bytes:0},{max_blob_bytes:0},{max_string_bytes:0},{max_checks_per_step:0},{max_parameters:1},{max_items:2}])assert.throws(()=>recordAudit(m,[],config,100,limits));
 const noSteps=recordAudit(m,[1],config,100,{max_steps:0});assert.equal(noSteps.termination,'step_limit');assert.equal(noSteps.steps.length,0);
 const noOutputs=recordAudit(m,[1],config,100,{max_outputs_per_step:0});assert.equal(noOutputs.termination,'model_error');assert.equal(noOutputs.steps.length,0);assert.ok(writeAuditTrace(noOutputs,{max_outputs_per_step:0}).length>0);
 const empty=recordAudit(m,[],config),minimum=writeAuditTrace(empty).length+260;
 assert.equal(recordAudit(m,[],config,100,{max_total_bytes:minimum}).termination,'completed');assert.throws(()=>recordAudit(m,[],config,100,{max_total_bytes:minimum-1}));
 const unicode=codec({metadata:()=>metadata('é'.repeat(13),'test')});assert.throws(()=>recordAudit(unicode,[],config,100,{max_string_bytes:25}));
});
test('audit capture escapes invalid host diagnostics while supplied binary text remains strict',()=>{
 const config={strategy:'manual',seed:null,parameters:[]},m=codec({step(){throw Error('bad\ud800reason');}}),trace=recordAudit(m,[1],config);
 assert.equal(trace.termination,'model_error');assert.ok(trace.error.includes('\\ud800'));assert.ok(writeAuditTrace(trace).length>0);
 assert.throws(()=>writeAuditTrace({...trace,error:'bad\ud800reason'}));
});
const recordCorpus=JSON.parse(readFileSync(new URL('../../conformance/replay-evidence-v1.json',import.meta.url),'utf8'));
for(const row of recordCorpus.record_cases)test(`replay-evidence-v1 ${row.case}`,()=>{
 const config={...row.config,seed:row.config.seed===null?null:BigInt(row.config.seed),parameters:row.config.parameters.map(p=>[...p])};
 const encode=(kind,value,b)=>{if(row.error_at===`encode_${kind}:${value}`)throw Error('fixture codec error');b.extend(new TextEncoder().encode(String(value)));};
 const identity=metadata('replay','same'),m=codec({metadata:()=>identity,step:(s,i)=>accepted(s+i,[s+i]),checkState:()=>[check('ok')],checkTransition:()=>[check('edge')],encodeStateInto:(v,b)=>encode('state',v,b),encodeInputInto:(v,b)=>encode('input',v,b),encodeOutputInto:(v,b)=>encode('output',v,b)});
 const limits={...row.limits};
 if(row.budget_formula){
  const string=s=>4+new TextEncoder().encode(s).length;
  const parameters=[...config.parameters,['stateless.engine.version','0.1.0'],['stateless.engine.build','native-typescript-v1']];
  const run=string(identity.name)+12+string(identity.build)+string(config.strategy)+1+(config.seed===null?0:8)+4+parameters.reduce((n,[k,v])=>n+string(k)+string(v),0)+4+1+4+string('ok')+1;
  limits.max_total_bytes=12+9+run+2*57+278-(row.budget_formula.endsWith('-1')?1:0);
 }
 if(row.expected.error)assert.throws(()=>recordAudit(m,row.inputs,config,100000,limits));
 else{const t=recordAudit(m,row.inputs,config,100000,limits);assert.equal(t.termination,row.expected.termination);assert.equal(t.steps.length,row.expected.steps);assert.ok(writeAuditTrace(t,limits).length>0);}
});
test('terminal failed candidates may consume otherwise reserved future-error headroom',()=>{
 const config={strategy:'manual',seed:null,parameters:[]},common={step:s=>accepted(s+1,[0]),encodeOutputInto(_o,b){b.extend(new Uint8Array(300));}};
 const failing=codec({...common,checkState:s=>s?[check('ok'),check('bad','failed','broken')]:[check('ok')]}),passing=codec(common);
 const expected=recordAudit(failing,[1],config),cap=writeAuditTrace(expected).length;
 const failed=recordAudit(failing,[1],config,100,{max_total_bytes:cap});assert.equal(failed.termination,'property_failed');assert.equal(failed.steps.length,1);assert.equal(writeAuditTrace(failed,{max_total_bytes:cap}).length,cap);
 const rejected=recordAudit(passing,[1],config,100,{max_total_bytes:cap});assert.equal(rejected.termination,'model_error');assert.equal(rejected.steps.length,0);assert.ok(writeAuditTrace(rejected,{max_total_bytes:cap}).length<=cap);
});
test('audit recording preserves Rust per-output prefix charging and callback order on rejected candidates',()=>{
 const config={strategy:'manual',seed:null,parameters:[]};let calls=0;
 const many=codec({step:s=>accepted(s+1,Array(100).fill(0)),encodeOutputInto(_o,b){calls++;b.extend(new Uint8Array());}});
 const trace=recordAudit(many,[1],config,100,{max_frame_bytes:230});assert.equal(trace.termination,'model_error');assert.equal(trace.steps.length,0);assert.equal(calls,49);assert.ok(trace.error.includes('output frame bytes'));
 const events=[];const full=codec({step:s=>accepted(s+1,[0,1]),encodeOutputInto(o,b){events.push(`start:${o}`);b.extend(new Uint8Array(193));events.push(`accepted:${o}`);}});
 const failed=recordAudit(full,[1],config,100,{max_frame_bytes:230});assert.equal(failed.termination,'model_error');assert.equal(failed.steps.length,0);assert.deepEqual(events,['start:0','accepted:0']);assert.ok(writeAuditTrace(failed,{max_frame_bytes:230}).length>0);
});

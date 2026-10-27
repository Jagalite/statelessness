import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {Recorder,checkInitial,checkTurnExact,check,accepted,metadata,ModelError,writeAuditTrace,replayWithObservations} from '../dist/index.js';
function model(extra={}){return {metadata:()=>metadata('checked-fixture','candidate-v1'),initialState(){throw Error('must not initialize');},step(){throw Error('must not execute');},cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,checkState:()=>[check('s')],checkTransition:()=>[check('t')],encodeState:s=>new TextEncoder().encode(String(s)),decodeState:b=>Number(new TextDecoder().decode(b)),encodeInput:i=>new TextEncoder().encode(String(i)),decodeInput:b=>Number(new TextDecoder().decode(b)),encodeOutput:o=>new TextEncoder().encode(String(o)),...extra};}
const config={strategy:'manual',seed:null,parameters:[]};
test('checked recorder does not rerun checks or reducer and promotes only state checks',()=>{
 let checks=0;const m=model({checkState:()=>{checks++;return [check('state'),check('skip','skipped','why')];},checkTransition:()=>{checks++;return [check('edge')];}}),initial=checkInitial(m,7);
 const recorder=Recorder.withCheckedInitial(initial,config,{maxSteps:1});assert.equal(checks,1);
 for(let i=1;i<=3;i++){const token=checkTurnExact(m,6+i,1,accepted(7+i,[90]),BigInt(i));const before=checks;recorder.observeChecked(token);assert.equal(checks,before);}
 const trace=recorder.snapshot();assert.equal(recorder.observedSteps(),3n);assert.equal(recorder.evictedSteps(),2n);assert.deepEqual(trace.initialChecks.map(c=>c.id),['state','skip']);assert.equal(new TextDecoder().decode(trace.initialState),'9');assert.equal(recorder.retainedBytes(),BigInt(writeAuditTrace(trace).length));
});
test('tokens can feed two recorders; duplicate freezes only the receiving recorder',()=>{
 const m=model(),initial=checkInitial(m,0),a=Recorder.withCheckedInitial(initial,config),b=Recorder.withCheckedInitial(initial,config),token=checkTurnExact(m,0,1,accepted(1),1n);
 a.observeChecked(token);b.observeChecked(token);assert.throws(()=>a.observeChecked(token),/missing or reordered/);assert.equal(a.isFrozen(),true);assert.equal(b.isFrozen(),false);assert.equal(a.retainedSteps(),1);
});
test('candidate codec failure freezes coherent prefix without eviction',()=>{
 const m=model(),r=new Recorder(m,0,config,{maxSteps:1});r.observe(m,0,1,accepted(1));const old=r.snapshot();const bad=model({encodeOutput(){throw Error('output failure');}});
 assert.throws(()=>r.observe(bad,1,1,accepted(2,[1])),/output failure/);const next=r.snapshot();assert.deepEqual(next.steps,old.steps);assert.deepEqual(next.initialState,old.initialState);assert.equal(r.evictedSteps(),0n);assert.equal(next.termination,'model_error');assert.equal(r.retainedBytes(),BigInt(writeAuditTrace(next).length));
});
test('snapshot and returned check mutation cannot alter retained evidence; transfer consumes owner',()=>{
 const m=model(),r=new Recorder(m,0,config),cs=r.observe(m,0,1,accepted(1,[9]));assert.throws(()=>{cs[0].id='bad';});const snapshot=r.snapshot();snapshot.steps[0].input[0]=255;snapshot.initialChecks[0].id='bad';snapshot.config.parameters[0][1]='bad';assert.notEqual(r.snapshot().steps[0].input[0],255);const transferred=r.intoTrace();assert.equal(transferred.steps.length,1);assert.throws(()=>r.snapshot(),/transferred/);
});
test('origin and exact u64 boundaries have no rounding and property failure beats later errors',()=>{
 const m=model(),initial=checkInitial(m,0),r=Recorder.withCheckedInitialAt(initial,(1n<<64n)-2n,config);r.observeChecked(checkTurnExact(m,0,1,accepted(1),(1n<<64n)-1n));assert.equal(r.observedSteps(),1n);assert.throws(()=>r.observeChecked(checkTurnExact(m,1,1,accepted(2),1n)),/missing or reordered/);
 const failing=model({checkState:()=>[check('bad','failed','why')]}),f=Recorder.withCheckedInitial(checkInitial(failing,0),config);f.stopWithError(new ModelError('later'));assert.equal(f.snapshot().termination,'property_failed');
});
test('independently typed model representations hand off by metadata and canonical continuity',()=>{
 const numeric=model(),r=new Recorder(numeric,0,config),text=model({cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o});
 r.observeChecked(checkTurnExact(text,'0','1',accepted('1',['9']),1n));assert.equal(r.observedSteps(),1n);
 const directory=mkdtempSync(join(tmpdir(),'stateless-recorder-types-'));
 try{const entry=fileURLToPath(new URL('../dist/index.js',import.meta.url));const source=`import {Recorder,checkInitial,checkTurnExact,metadata,accepted,type CodecModel} from ${JSON.stringify(entry)};\nconst c={strategy:'manual',seed:null,parameters:[]};\ndeclare const numeric:CodecModel<number,number,number>;declare const text:CodecModel<string,string,string>;\nconst r=Recorder.withCheckedInitial(checkInitial(numeric,0),c);r.observeChecked(checkTurnExact(text,'0','1',accepted('1',['9']),1n));r.observe(text,'1','1',accepted('2',['9']));\n`;
  const path=join(directory,'consumer.mts');writeFileSync(path,source);const tsc=fileURLToPath(new URL('../node_modules/typescript/bin/tsc',import.meta.url));const result=spawnSync(process.execPath,[tsc,'--noEmit','--strict','--target','es2022','--module','nodenext','--moduleResolution','nodenext',path],{encoding:'utf8'});assert.equal(result.status,0,result.stdout+result.stderr);
 }finally{rmSync(directory,{recursive:true,force:true});}
});
const corpus=JSON.parse(readFileSync(new URL('../../conformance/checked-results-v1.json',import.meta.url),'utf8'));
for(const row of corpus.recorder_cases)test(`shared checked-results recorder ${row.case}`,()=>{
 const encoder=new TextEncoder(),decoder=new TextDecoder();let stateCalls=0,edgeCalls=0;
 function fixture(description=row.model,name='checked-fixture',encodingError){return model({metadata:()=>metadata(name,'candidate-v1'),checkState(){stateCalls++;if(description.state_error)throw Error(description.state_error);return description.state_checks.map(c=>check(c.id,c.status,c.details));},checkTransition(){edgeCalls++;if(description.transition_error)throw Error(description.transition_error);return description.transition_checks.map(c=>check(c.id,c.status,c.details));},encodeState(s){if(encodingError==='state')throw Error('fixture encode error');return encoder.encode(String(s));},encodeInput(i){if(encodingError==='input')throw Error('fixture encode error');return encoder.encode(String(i));},encodeOutput(o){if(encodingError==='output')throw Error('fixture encode error');return encoder.encode(String(o));}});}
 const m=fixture(),limits={};for(const [k,v]of Object.entries(row.options.limits))limits[k==='max_checks_per_batch'?'max_checks_per_step':k]=Number(v);
 const options={maxSteps:row.options.max_steps,maxRetainedBytes:BigInt(row.options.max_retained_bytes),limits};let r;
 try{r=row.entry_mode==='ordinary'?new Recorder(m,row.initial_state,row.config,options):Recorder.withCheckedInitialAt(checkInitial(m,row.initial_state),BigInt(row.origin),row.config,options);if(row.expected.initial_error_contains)assert.fail('expected constructor error');}
 catch(e){if(row.expected.initial_error_contains){assert.ok(e.message.includes(row.expected.initial_error_contains),e.message);return;}throw e;}
 const boundaries=[];
 for(let index=0;index<row.actions.length;index++){
  const a=row.actions[index],expected=row.expected.states[index];let error=null;
  try{if(a.stop_error!==undefined)r.stopWithError(new ModelError(a.stop_error));else{const actual=fixture(a.checks_model??row.model,a.model_name==='different'?'different':'checked-fixture',a.encode_error),text=a.representation==='decimal-string',convert=x=>text?String(x):x,t={state:convert(a.transition.state),outputs:a.transition.outputs.map(convert),disposition:a.transition.disposition};const before=stateCalls+edgeCalls;const token=a.mode==='ordinary'?null:checkTurnExact(actual,convert(a.before),convert(a.input),t,BigInt(a.sequence),{stateEvery:BigInt(a.policy.state_every),transitionChecks:a.policy.transition_checks});const checkedCalls=stateCalls+edgeCalls;if(token)r.observeChecked(token);else r.observe(actual,convert(a.before),convert(a.input),t);if(token)assert.equal(stateCalls+edgeCalls,checkedCalls);else assert.equal(stateCalls+edgeCalls,before+2);boundaries.push(token?.stateCheckCount??row.model.state_checks.length);}}
  catch(e){error=e;}
  if(expected.error_contains)assert.ok(error?.message.includes(expected.error_contains),error?.message??'no error');else assert.equal(error,null);
  const t=r.snapshot(),evicted=Number(r.evictedSteps());const actual={error_contains:expected.error_contains,observed_steps:r.observedSteps().toString(),evicted_steps:r.evictedSteps().toString(),retained_steps:r.retainedSteps(),frozen:r.isFrozen(),termination:t.termination,initial_state:Number(decoder.decode(t.initialState)),initial_checks:t.initialChecks,steps:t.steps.map((s,i)=>({input:Number(decoder.decode(s.input)),transition:{state:Number(decoder.decode(s.postState)),outputs:s.outputs.map(b=>Number(decoder.decode(b))),disposition:s.disposition},checks:s.checks,state_check_count:boundaries[evicted+i]}))};assert.deepEqual(actual,expected);assert.equal(r.retainedBytes(),BigInt(writeAuditTrace(t).length));
 }
});
test('retention default stays independent of custom total quota and format errors have Rust priority',()=>{
 const m=model(),r=new Recorder(m,0,config,{limits:{max_total_bytes:134217728}});assert.equal(r.snapshot().config.parameters.find(([k])=>k==='stateless.monitor.max_retained_bytes')[1],'67108864');
 const active=new Recorder(m,0,config),maximum=active.retainedBytes()+260n;
 const limited=new Recorder(m,0,config,{maxRetainedBytes:maximum,limits:{max_items:10}});
 assert.throws(()=>limited.observe(m,0,1,accepted(1)),/aggregate items/);assert.equal(limited.retainedSteps(),0);assert.equal(limited.isFrozen(),true);
});
test('trusted check quota preflight happens before copying immutable token batches',async()=>{
 const {requireCheckedInitialForRecorder,requireCheckedTurnForRecorder}=await import('../dist/checked.js');
 const m=model({checkState:()=>[check('a'),check('b')]}),initial=checkInitial(m,0),initialChecks=requireCheckedInitialForRecorder(initial).checks;
 const token=checkTurnExact(m,0,1,accepted(1),1n),turnChecks=requireCheckedTurnForRecorder(token).checks;
 const r=Recorder.withCheckedInitial(initial,config,{limits:{max_checks_per_step:2}});
 let initialCopies=0,turnCopies=0;const previous=Array.prototype.map;
 Array.prototype.map=function(...args){if(this===initialChecks)initialCopies++;if(this===turnChecks)turnCopies++;return previous.apply(this,args);};
 try{assert.throws(()=>Recorder.withCheckedInitial(initial,config,{limits:{max_checks_per_step:1}}),/checks per step/);assert.throws(()=>r.observeChecked(token),/checks per step/);}finally{Array.prototype.map=previous;}
 assert.equal(initialCopies,0);assert.equal(turnCopies,0);assert.equal(r.retainedSteps(),0);
});
test('reentrant stop preserves first terminal prefix and nested mutation is guarded',()=>{
 const m=model();let r;const trigger=model({encodeOutput(){r.stopWithError(new ModelError('first stop'));throw Error('later codec error');}});
 r=new Recorder(m,0,config);assert.throws(()=>r.observe(trigger,0,1,accepted(1,[9])),/later codec error/);assert.equal(r.retainedSteps(),0);assert.equal(r.snapshot().error,'first stop');
 let nested=0;const outer=model({encodeOutput(o){assert.throws(()=>r.observe(m,0,1,accepted(1)),/already in progress/);assert.throws(()=>r.intoTrace(),/already in progress/);nested++;return new TextEncoder().encode(String(o));}});
 r=new Recorder(m,0,config);r.observe(outer,0,1,accepted(1,[9]));assert.equal(nested,1);assert.equal(r.retainedSteps(),1);
 const stops=model({encodeOutput(o){r.stopWithError(new ModelError('stop before commit'));return new TextEncoder().encode(String(o));}});
 r=new Recorder(m,0,config);assert.throws(()=>r.observe(stops,0,1,accepted(1,[9])),/frozen/);assert.equal(r.retainedSteps(),0);assert.equal(r.snapshot().error,'stop before commit');
});
test('terminal reason formatting cannot transfer ownership reentrantly',()=>{
 const r=new Recorder(model(),0,config),error=new ModelError('source');let formatted=0;
 Object.defineProperty(error,'message',{get(){formatted++;assert.throws(()=>r.intoTrace(),/already in progress/);return 'terminal reason';}});
 assert.equal(r.stopWithError(error),error);assert.ok(formatted>0);assert.equal(r.snapshot().error,'terminal reason');assert.equal(r.snapshot().termination,'model_error');
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {EvidenceRecorder,checkInitial,checkTurnExact,accepted,check,metadata,ModelError,Recorder} from '../dist/index.js';
import {resolveEvidenceLimits,measureProvenance,measureStepEvidence,validateTraceV2,writeTraceV2} from '../dist/evidence.js';
const config={strategy:'manual',seed:null,parameters:[]};
const provenance=()=>({run:'run',epoch:0n,manifest:null,source:null,environment:[]});
const context=(causes=[])=>({machine:null,generation:null,correlation:null,logicalTime:null,causes});
function model(extra={}) {return {metadata:()=>metadata('evidence-fixture','test'),initialState(){throw Error('must not initialize');},step(){throw Error('must not execute');},cloneState:s=>s,cloneInput:i=>i,cloneOutput:o=>o,checkState:()=>[check('state')],checkTransition:()=>[check('edge')],encodeState:s=>Uint8Array.of(Number(s)),decodeState:b=>b[0],encodeInput:i=>Uint8Array.of(Number(i)),decodeInput:b=>b[0],encodeOutput:o=>Uint8Array.of(Number(o)),...extra};}
function make(m=model(),options={maxSteps:10,maxRetainedBytes:1000000n},limits={}) {return new EvidenceRecorder(checkInitial(m,0),0n,config,options,provenance(),{maxMetadataBytes:1000,...limits});}
function turn(m,sequence) {return checkTurnExact(m,Number(sequence)-1,1,accepted(Number(sequence),[9]),BigInt(sequence));}
function validate(value,limits={}){validateTraceV2(value,limits);return value;}
test('causal evidence handoff performs no rechecks and step eviction promotes state prefix',()=>{
 let calls=0;const m=model({checkState(){calls++;return [check('state'),check('state.skip','skipped','none')];},checkTransition(){calls++;return [check('edge')];}}),r=make(m,{maxSteps:1,maxRetainedBytes:1000000n});
 for(let i=1;i<=3;i++){const token=turn(m,i),before=calls;r.observe(token,context());assert.equal(calls,before);const s=validate(r.snapshot());assert.equal(s.steps.length,s.trace.steps.length);}
 const s=r.snapshot();assert.equal(s.checkpointAfter,2n);assert.deepEqual(s.trace.initialChecks.map(c=>c.id),['state','state.skip']);assert.deepEqual(s.steps.map(s=>s.sequence),[3n]);
});
test('metadata-only eviction is coordinated with real recorder minimum evictions',()=>{
 const m=model(),p=provenance(),base=12+measureProvenance(p,resolveEvidenceLimits({})),row=measureStepEvidence({sequence:1n,stateCheckCount:1,causal:context()},resolveEvidenceLimits({})),limits={maxMetadataBytes:base+row*2};
 const r=make(m,{maxSteps:10,maxRetainedBytes:1000000n},limits);for(let i=1;i<=3;i++)r.observe(turn(m,i),context());
 const s=validate(r.snapshot(),limits);assert.equal(s.checkpointAfter,1n);assert.equal(s.trace.steps.length,2);assert.deepEqual(s.steps.map(s=>s.sequence),[2n,3n]);
});
test('aggregate cause-only evictions retain permitted omitted-prefix links',()=>{
 const m=model(),limits={maxTotalCauses:1},r=make(m,{maxSteps:10,maxRetainedBytes:1000000n},limits);
 r.observe(turn(m,1),context());r.observe(turn(m,2),context([{sequence:1n,output:0}]));r.observe(turn(m,3),context([{sequence:2n,output:0}]));
 const s=validate(r.snapshot(),limits);assert.equal(s.checkpointAfter,2n);assert.equal(s.trace.steps.length,1);assert.equal(s.steps[0].causal.causes[0].sequence,2n);
});
test('planned eviction cannot excuse a currently invalid retained output index',()=>{
 const m=model(),l=resolveEvidenceLimits({}),base=12+measureProvenance(provenance(),l),row=measureStepEvidence({sequence:2n,stateCheckCount:1,causal:context([{sequence:1n,output:99}])},l),limits={maxMetadataBytes:base+row},r=make(m,{maxSteps:10,maxRetainedBytes:1000000n},limits);
 r.observe(turn(m,1),context());assert.throws(()=>r.observe(turn(m,2),context([{sequence:1n,output:99}])),/outside retained output range/);
 const s=validate(r.snapshot(),limits);assert.equal(s.checkpointAfter,0n);assert.equal(s.trace.steps.length,1);assert.equal(s.steps.length,1);assert.equal(r.isFrozen(),true);
});
test('trace codec failure after metadata eviction plan preserves both coherent prefixes',()=>{
 const m=model(),l=resolveEvidenceLimits({}),base=12+measureProvenance(provenance(),l),row=measureStepEvidence({sequence:1n,stateCheckCount:1,causal:context()},l),limits={maxMetadataBytes:base+row},r=make(m,{maxSteps:10,maxRetainedBytes:1000000n},limits);
 r.observe(turn(m,1),context());const bad=model({encodeOutput(){throw Error('codec failed');}});assert.throws(()=>r.observe(turn(bad,2),context()),/codec failed/);
 const s=validate(r.snapshot(),limits);assert.equal(s.checkpointAfter,0n);assert.deepEqual(s.steps.map(s=>s.sequence),[1n]);assert.equal(s.trace.steps.length,1);assert.equal(s.trace.termination,'model_error');
});
test('full selected metadata reservation is independent and never silently shrunk',()=>{
 const m=model(),initial=checkInitial(m,0);assert.throws(()=>new EvidenceRecorder(initial,0n,config,{maxSteps:1,maxRetainedBytes:1000n},provenance()),/reservation exceeds total limit/);
 const r=new EvidenceRecorder(initial,0n,config,{maxSteps:1,maxRetainedBytes:2000n},provenance(),{maxMetadataBytes:100});assert.equal(r.isFrozen(),false);
});
test('caller provenance/context and returned snapshots cannot rewrite retained evidence',()=>{
 const m=model(),p=provenance();p.environment.push(['k','v']);const r=new EvidenceRecorder(checkInitial(m,0),0n,config,{maxSteps:10,maxRetainedBytes:1000000n},p,{maxMetadataBytes:1000});p.environment[0][1]='changed';
 const c=context();c.machine='host';r.observe(turn(m,1),c);c.machine='changed';c.causes.push({sequence:99n,output:0});const s=r.snapshot();assert.equal(s.provenance.environment[0][1],'v');assert.equal(s.steps[0].causal.machine,'host');assert.throws(()=>{s.provenance.environment[0][1]='tampered';});assert.throws(()=>s.steps[0].causal.causes.push({sequence:99n,output:0}));assert.equal(r.snapshot().provenance.environment[0][1],'v');assert.deepEqual(r.snapshot().steps[0].causal.causes,[]);
 const moved=r.intoTrace();assert.equal(moved.steps.length,1);assert.throws(()=>r.snapshot(),/transferred/);
});
test('origin u64 maximum, initial failure and first capture error retain exact terminal evidence',()=>{
 const m=model(),r=new EvidenceRecorder(checkInitial(m,0),(1n<<64n)-2n,config,{maxSteps:10,maxRetainedBytes:1000000n},provenance(),{maxMetadataBytes:1000});
 r.observe(checkTurnExact(m,0,1,accepted(1,[9]),(1n<<64n)-1n),context([{sequence:1n,output:999}]));assert.equal(validate(r.snapshot()).steps[0].sequence,(1n<<64n)-1n);
 const f=model({checkState:()=>[check('bad','failed','why')]}),frozen=make(f);assert.equal(frozen.isFrozen(),true);assert.throws(()=>frozen.observe(turn(m,1),context()),/frozen/);assert.equal(frozen.snapshot().trace.termination,'property_failed');
 const active=make(m);assert.throws(()=>active.observe(turn(m,1),context([{sequence:0n,output:0}])),/precede|origin/);const first=active.snapshot().trace.error;assert.throws(()=>active.observe(turn(m,1),context()),/frozen/);assert.equal(active.snapshot().trace.error,first);
});
test('recorder internal coordination is absent from public package and cannot authorize a forged owner',async()=>{
 const publicAPI=await import('../dist/index.js');assert.equal(publicAPI.observeCheckedRetaining,undefined);assert.equal(publicAPI.retainedOutputCount,undefined);
 const internal=await import('../dist/runtime_recorder.js');assert.throws(()=>internal.retainedOutputCount({},1n),/invalid runtime recorder/);assert.throws(()=>internal.observeCheckedRetaining({},turn(model(),1),0),/invalid runtime recorder/);
});
test('evidence capture and ownership transfer reject model callback reentry',()=>{
 let r;const m=model(),reentrant=model({encodeOutput(o){assert.throws(()=>r.observe(turn(m,2),context()),/already in progress/);assert.throws(()=>r.intoTrace(),/already in progress/);return Uint8Array.of(o);}});
 r=make(m);r.observe(turn(reentrant,1),context());assert.equal(r.snapshot().steps.length,1);validate(r.snapshot());
});

import {readFileSync} from 'node:fs';
const corpus=JSON.parse(readFileSync(new URL('../../conformance/causal-evidence-v2.json',import.meta.url),'utf8'));
const causalFrom=v=>({machine:v.machine,generation:v.generation===null?null:BigInt(v.generation),correlation:v.correlation,logicalTime:v.logical_time===null?null:BigInt(v.logical_time),causes:v.causes.map(o=>({sequence:BigInt(o.sequence),output:o.output}))});
const causalTo=v=>({machine:v.machine,generation:v.generation===null?null:v.generation.toString(),correlation:v.correlation,logical_time:v.logicalTime===null?null:v.logicalTime.toString(),causes:v.causes.map(o=>({sequence:o.sequence.toString(),output:o.output}))});
function limitOptions(v){const out={};const names={max_metadata_bytes:'maxMetadataBytes',max_labels:'maxLabels',max_label_bytes:'maxLabelBytes',max_causes_per_step:'maxCausesPerStep',max_total_causes:'maxTotalCauses'};for(const [k,value]of Object.entries(v)){if(k==='trace'){out.trace={};for(const [a,b]of Object.entries(value))out.trace[a==='max_checks_per_batch'?'max_checks_per_step':a]=Number(b);}else out[names[k]]=Number(value);}return out;}
for(const row of corpus.recorder_cases)test(`shared causal-evidence-v2 recorder ${row.case}`,()=>{
 const encoder=new TextEncoder(),decoder=new TextDecoder();let checks=0;
 const fixture=encodingError=>model({metadata:()=>metadata(row.model.name,row.model.build),checkState(s){checks++;return [row.model.fail_at!==null&&s>=row.model.fail_at?check('s','failed','at threshold'):check('s')];},checkTransition(){checks++;return [check('t')];},encodeState:s=>encoder.encode(String(s)),decodeState:b=>Number(decoder.decode(b)),encodeInput:i=>encoder.encode(String(i)),decodeInput:b=>Number(decoder.decode(b)),encodeOutput(o){if(encodingError==='output')throw Error('fixture encode output error');return encoder.encode(String(o));}});
 const initialModel=fixture(),p={...row.provenance,epoch:BigInt(row.provenance.epoch)},limits=limitOptions(row.limits),options={maxSteps:row.options.max_steps,maxRetainedBytes:BigInt(row.options.max_retained_bytes),limits:row.options.limits};let r;
 try{r=new EvidenceRecorder(checkInitial(initialModel,row.initial_state),BigInt(row.origin),{...row.config,seed:row.config.seed===null?null:BigInt(row.config.seed)},options,p,limits);if(row.expected.initial_error_contains)assert.fail('expected initial evidence error');}
 catch(e){if(row.expected.initial_error_contains){assert.ok(e.message.includes(row.expected.initial_error_contains),e.message);return;}throw e;}
 for(let index=0;index<row.actions.length;index++){
  const a=row.actions[index],expected=row.expected.states[index];let error=null;
  try{const m=fixture(a.encode_error),token=checkTurnExact(m,a.before,a.input,{state:a.transition.state,outputs:a.transition.outputs,disposition:{kind:a.transition.disposition.kind,reason:a.transition.disposition.reason??''}},BigInt(a.sequence),{stateEvery:BigInt(a.policy.state_every),transitionChecks:a.policy.transition_checks}),count=checks;r.observe(token,causalFrom(a.causal));assert.equal(checks,count);}
  catch(e){error=e;}
  if(expected.error_contains)assert.ok(error?.message.includes(expected.error_contains),error?.message??'no error');else assert.equal(error,null);
  const v=r.snapshot(),counter=key=>v.trace.config.parameters.find(([k])=>k===`stateless.monitor.${key}`)[1];
  const actual={error_contains:expected.error_contains,checkpoint_after:v.checkpointAfter.toString(),initial_state:Number(decoder.decode(v.trace.initialState)),initial_checks:v.trace.initialChecks,steps:v.trace.steps.map(s=>({input:Number(decoder.decode(s.input)),post_state:Number(decoder.decode(s.postState)),outputs:s.outputs.map(o=>Number(decoder.decode(o))),checks:s.checks})),evidence_steps:v.steps.map(s=>({sequence:s.sequence.toString(),state_check_count:s.stateCheckCount,causal:causalTo(s.causal)})),observed_steps:counter('observed_steps'),evicted_steps:counter('evicted_steps'),frozen:r.isFrozen(),termination:v.trace.termination};
  assert.deepEqual(actual,expected);validateTraceV2(v,limits);assert.equal(v.steps.length,v.trace.steps.length);
 }
});
test('recorder constructor encoded provenance and base budgets precede semantic labels',()=>{
 let callbacks=0;const m=model({metadata(){callbacks++;return metadata('evidence-fixture','test');}}),initial=checkInitial(m,0),options={maxSteps:1,maxRetainedBytes:1000000n};
 assert.throws(()=>new EvidenceRecorder(initial,0n,config,options,{...provenance(),run:'x'},{maxMetadataBytes:17,maxLabelBytes:0}),/encoded byte limit/);
 assert.throws(()=>new EvidenceRecorder(initial,0n,config,options,{...provenance(),run:'x'},{maxMetadataBytes:30,maxLabelBytes:0}),/metadata budget exceeded/);
 assert.throws(()=>new EvidenceRecorder(initial,0n,config,options,{...provenance(),run:'x'},{maxMetadataBytes:31,maxLabelBytes:0}),/label byte limit/);
 const duplicate={...provenance(),environment:[['same','v'],['same','v']]};
 assert.throws(()=>new EvidenceRecorder(initial,0n,config,options,duplicate,{maxMetadataBytes:20,maxLabels:0}),/encoded byte limit/);
 assert.throws(()=>new EvidenceRecorder(initial,0n,config,options,duplicate,{maxMetadataBytes:1000,maxLabels:0}),/environment label limit/);
 assert.equal(callbacks,0);
});
test('known oversized constructor strings fail before UTF8 scans or payload encoding',()=>{
 const initial=checkInitial(model(),0),huge='x'.repeat(8*1024*1024),p={...provenance(),run:huge};let characters=0,iterators=0,encodings=0;
 const previousChar=String.prototype.charCodeAt,previousIterator=String.prototype[Symbol.iterator],previousEncode=TextEncoder.prototype.encode;
 String.prototype.charCodeAt=function(...args){if(this.length===huge.length)characters++;return previousChar.apply(this,args);};
 String.prototype[Symbol.iterator]=function(...args){if(this.length===huge.length)iterators++;return previousIterator.apply(this,args);};
 TextEncoder.prototype.encode=function(value){if(value===huge)encodings++;return previousEncode.call(this,value);};
 try{assert.throws(()=>new EvidenceRecorder(initial,0n,config,{maxSteps:1,maxRetainedBytes:1000000n},p,{maxMetadataBytes:17,maxLabelBytes:0}),/encoded byte limit/);}
 finally{String.prototype.charCodeAt=previousChar;String.prototype[Symbol.iterator]=previousIterator;TextEncoder.prototype.encode=previousEncode;}
 assert.equal(characters,0);assert.equal(iterators,0);assert.equal(encodings,0);
});
